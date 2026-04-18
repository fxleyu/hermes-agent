"""
Hermes 插件系统
====================

从三个来源发现、加载和管理插件：

1. **用户插件**   – ``~/.hermes/plugins/<name>/``
2. **项目插件** – ``./.hermes/plugins/<name>/``（通过
   ``HERMES_ENABLE_PROJECT_PLUGINS`` 选择启用）
3. **Pip 插件**     – 暴露 ``hermes_agent.plugins``
   入口点组的包。

每个目录插件必须包含一个 ``plugin.yaml`` 清单文件**以及**一个
带有 ``register(ctx)`` 函数的 ``__init__.py``。

生命周期钩子
---------------
插件可以为 ``VALID_HOOKS`` 中的任何钩子注册回调。
智能体核心在适当的时机调用 ``invoke_hook(name, **kwargs)``。

工具注册
-----------------
``PluginContext.register_tool()`` 委托给 ``tools.registry.register()``，
因此插件定义的工具会与内置工具一起显示。
"""

from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
import logging
import sys
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Union

from hermes_constants import get_hermes_home
from utils import env_var_enabled

try:
    import yaml
except ImportError:  # pragma: no cover – yaml 在导入时是可选的
    yaml = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

VALID_HOOKS: Set[str] = {
    "pre_tool_call",
    "post_tool_call",
    "pre_llm_call",
    "post_llm_call",
    "pre_api_request",
    "post_api_request",
    "on_session_start",
    "on_session_end",
    "on_session_finalize",
    "on_session_reset",
}

ENTRY_POINTS_GROUP = "hermes_agent.plugins"

_NS_PARENT = "hermes_plugins"


def _env_enabled(name: str) -> bool:
    """当环境变量设置为真值选择启用时返回 True。"""
    return env_var_enabled(name)


def _get_disabled_plugins() -> set:
    """从 config.yaml 读取已禁用的插件列表。"""
    try:
        from hermes_cli.config import load_config
        config = load_config()
        disabled = config.get("plugins", {}).get("disabled", [])
        return set(disabled) if isinstance(disabled, list) else set()
    except Exception:
        return set()


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------

@dataclass
class PluginManifest:
    """plugin.yaml 清单的解析表示。"""

    name: str
    version: str = ""
    description: str = ""
    author: str = ""
    requires_env: List[Union[str, Dict[str, Any]]] = field(default_factory=list)
    provides_tools: List[str] = field(default_factory=list)
    provides_hooks: List[str] = field(default_factory=list)
    source: str = ""        # "user"、"project" 或 "entrypoint"
    path: Optional[str] = None


@dataclass
class LoadedPlugin:
    """单个已加载插件的运行时状态。"""

    manifest: PluginManifest
    module: Optional[types.ModuleType] = None
    tools_registered: List[str] = field(default_factory=list)
    hooks_registered: List[str] = field(default_factory=list)
    commands_registered: List[str] = field(default_factory=list)
    enabled: bool = False
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# PluginContext – 传递给每个插件的 ``register()`` 函数
# ---------------------------------------------------------------------------

class PluginContext:
    """提供给插件的门面，使其可以注册工具和钩子。"""

    def __init__(self, manifest: PluginManifest, manager: "PluginManager"):
        self.manifest = manifest
        self._manager = manager

    # -- 工具注册 --------------------------------------------------

    def register_tool(
        self,
        name: str,
        toolset: str,
        schema: dict,
        handler: Callable,
        check_fn: Callable | None = None,
        requires_env: list | None = None,
        is_async: bool = False,
        description: str = "",
        emoji: str = "",
    ) -> None:
        """在全局注册表中注册工具**并**将其跟踪为插件提供的工具。"""
        from tools.registry import registry

        registry.register(
            name=name,
            toolset=toolset,
            schema=schema,
            handler=handler,
            check_fn=check_fn,
            requires_env=requires_env,
            is_async=is_async,
            description=description,
            emoji=emoji,
        )
        self._manager._plugin_tool_names.add(name)
        logger.debug("Plugin %s registered tool: %s", self.manifest.name, name)

    # -- 消息注入 --------------------------------------------------

    def inject_message(self, content: str, role: str = "user") -> bool:
        """向活跃对话中注入消息。

        如果智能体空闲（等待用户输入），这会启动一个新回合。
        如果智能体正在运行，这会中断并注入消息。

        这使插件（如远程控制查看器、消息桥接器）能够
        从外部来源向对话发送消息。

        成功排队返回 True。
        """
        cli = self._manager._cli_ref
        if cli is None:
            logger.warning("inject_message: no CLI reference (not available in gateway mode)")
            return False

        msg = content if role == "user" else f"[{role}] {content}"

        if getattr(cli, "_agent_running", False):
            # 智能体正在回合中——用消息中断
            cli._interrupt_queue.put(msg)
        else:
            # 智能体空闲——排队作为下一个输入
            cli._pending_input.put(msg)
        return True

    # -- CLI 命令注册 --------------------------------------------

    def register_cli_command(
        self,
        name: str,
        help: str,
        setup_fn: Callable,
        handler_fn: Callable | None = None,
        description: str = "",
    ) -> None:
        """注册 CLI 子命令（如 ``hermes honcho ...``）。

        *setup_fn* 接收一个 argparse 子解析器，应添加任何参数/子子解析器。
        如果提供了 *handler_fn*，它将通过 ``set_defaults(func=...)``
        设置为默认调度函数。"""
        self._manager._cli_commands[name] = {
            "name": name,
            "help": help,
            "description": description,
            "setup_fn": setup_fn,
            "handler_fn": handler_fn,
            "plugin": self.manifest.name,
        }
        logger.debug("Plugin %s registered CLI command: %s", self.manifest.name, name)

    # -- 斜杠命令注册 -------------------------------------------

    def register_command(
        self,
        name: str,
        handler: Callable,
        description: str = "",
    ) -> None:
        """注册在 CLI 和网关会话中可用的斜杠命令（如 ``/lcm``）。

        处理函数签名为 ``fn(raw_args: str) -> str | None``。
        也可以是异步可调用对象——网关调度会处理两种情况。

        与 ``register_cli_command()``（创建 ``hermes <subcommand>``
        终端命令）不同，这里注册的是用户在对话中调用的
        会话内斜杠命令。

        与内置命令冲突的名称会被拒绝并发出警告。
        """
        clean = name.lower().strip().lstrip("/").replace(" ", "-")
        if not clean:
            logger.warning(
                "Plugin '%s' tried to register a command with an empty name.",
                self.manifest.name,
            )
            return

        # 如果与内置命令冲突则拒绝
        try:
            from hermes_cli.commands import resolve_command
            if resolve_command(clean) is not None:
                logger.warning(
                    "Plugin '%s' tried to register command '/%s' which conflicts "
                    "with a built-in command. Skipping.",
                    self.manifest.name, clean,
                )
                return
        except Exception:
            pass  # 如果 commands 模块不可用，跳过检查

        self._manager._plugin_commands[clean] = {
            "handler": handler,
            "description": description or "Plugin command",
            "plugin": self.manifest.name,
        }
        logger.debug("Plugin %s registered command: /%s", self.manifest.name, clean)

    # -- 工具调度 -------------------------------------------------------

    def dispatch_tool(self, tool_name: str, args: dict, **kwargs) -> str:
        """通过注册表调度工具调用，带父智能体上下文。

        这是插件斜杠命令需要调用 ``delegate_task`` 等工具时的公共接口，
        无需深入框架内部。父智能体（如果可用）会自动解析——
        插件永远不需要直接访问智能体。

        Args:
            tool_name: 工具的注册表名称（如 ``"delegate_task"``）。
            args: 工具参数字典（与模型传递的格式相同）。
            **kwargs: 转发给注册表调度的额外关键字参数。

        Returns:
            来自工具处理函数的 JSON 字符串（与模型工具调用相同的格式）。
        """
        from tools.registry import registry

        # 当可用时连接父智能体上下文（CLI 模式）。
        # 在网关模式下 _cli_ref 为 None——工具会优雅降级
        # （workspace 提示回退到 TERMINAL_CWD，无加载动画）。
        if "parent_agent" not in kwargs:
            cli = self._manager._cli_ref
            agent = getattr(cli, "agent", None) if cli else None
            if agent is not None:
                kwargs["parent_agent"] = agent

        return registry.dispatch(tool_name, args, **kwargs)

    # -- 上下文引擎注册 -----------------------------------------

    def register_context_engine(self, engine) -> None:
        """注册上下文引擎以替换内置的 ContextCompressor。

        只允许一个上下文引擎插件。如果第二个插件尝试注册，
        将被拒绝并发出警告。

        引擎必须是 ``agent.context_engine.ContextEngine`` 的实例。
        """
        if self._manager._context_engine is not None:
            logger.warning(
                "Plugin '%s' tried to register a context engine, but one is "
                "already registered. Only one context engine plugin is allowed.",
                self.manifest.name,
            )
            return
        # 延迟导入以避免模块级别的循环依赖
        from agent.context_engine import ContextEngine
        if not isinstance(engine, ContextEngine):
            logger.warning(
                "Plugin '%s' tried to register a context engine that does not "
                "inherit from ContextEngine. Ignoring.",
                self.manifest.name,
            )
            return
        self._manager._context_engine = engine
        logger.info(
            "Plugin '%s' registered context engine: %s",
            self.manifest.name, engine.name,
        )

    # -- 钩子注册 --------------------------------------------------

    def register_hook(self, hook_name: str, callback: Callable) -> None:
        """注册生命周期钩子回调。

        未知的钩子名称会产生警告，但仍会存储，以便
        前向兼容的插件不会出错。
        """
        if hook_name not in VALID_HOOKS:
            logger.warning(
                "Plugin '%s' registered unknown hook '%s' "
                "(valid: %s)",
                self.manifest.name,
                hook_name,
                ", ".join(sorted(VALID_HOOKS)),
            )
        self._manager._hooks.setdefault(hook_name, []).append(callback)
        logger.debug("Plugin %s registered hook: %s", self.manifest.name, hook_name)

    # -- 技能注册 -------------------------------------------------

    def register_skill(
        self,
        name: str,
        path: Path,
        description: str = "",
    ) -> None:
        """注册此插件提供的只读技能。

        该技能可通过 ``skill_view()`` 以 ``'<plugin_name>:<name>'``
        的形式解析。它**不会**进入扁平的 ``~/.hermes/skills/`` 目录树，
        也**不会**列在系统提示的 ``<available_skills>`` 索引中——
        插件技能仅为显式选择加载。

        Raises:
            ValueError: 如果 *name* 包含 ``':'`` 或无效字符。
            FileNotFoundError: 如果 *path* 不存在。
        """
        from agent.skill_utils import _NAMESPACE_RE

        if ":" in name:
            raise ValueError(
                f"Skill name '{name}' must not contain ':' "
                f"(the namespace is derived from the plugin name "
                f"'{self.manifest.name}' automatically)."
            )
        if not name or not _NAMESPACE_RE.match(name):
            raise ValueError(
                f"Invalid skill name '{name}'. Must match [a-zA-Z0-9_-]+."
            )
        if not path.exists():
            raise FileNotFoundError(f"SKILL.md not found at {path}")

        qualified = f"{self.manifest.name}:{name}"
        self._manager._plugin_skills[qualified] = {
            "path": path,
            "plugin": self.manifest.name,
            "bare_name": name,
            "description": description,
        }
        logger.debug(
            "Plugin %s registered skill: %s",
            self.manifest.name, qualified,
        )


# ---------------------------------------------------------------------------
# PluginManager
# ---------------------------------------------------------------------------

class PluginManager:
    """发现、加载和调用插件的中央管理器。"""

    def __init__(self) -> None:
        self._plugins: Dict[str, LoadedPlugin] = {}
        self._hooks: Dict[str, List[Callable]] = {}
        self._plugin_tool_names: Set[str] = set()
        self._cli_commands: Dict[str, dict] = {}
        self._context_engine = None  # 由插件通过 register_context_engine() 设置
        self._plugin_commands: Dict[str, dict] = {}  # 插件注册的斜杠命令
        self._discovered: bool = False
        self._cli_ref = None  # 在插件发现后由 CLI 设置
        # 插件技能注册表：限定名称 -> 元数据字典。
        self._plugin_skills: Dict[str, Dict[str, Any]] = {}

    # -----------------------------------------------------------------------
    # 公共接口
    # -----------------------------------------------------------------------

    def discover_and_load(self) -> None:
        """扫描所有插件来源并加载找到的每个插件。"""
        if self._discovered:
            return
        self._discovered = True

        manifests: List[PluginManifest] = []

        # 1. 用户插件 (~/.hermes/plugins/)
        user_dir = get_hermes_home() / "plugins"
        manifests.extend(self._scan_directory(user_dir, source="user"))

        # 2. 项目插件 (./.hermes/plugins/)
        if _env_enabled("HERMES_ENABLE_PROJECT_PLUGINS"):
            project_dir = Path.cwd() / ".hermes" / "plugins"
            manifests.extend(self._scan_directory(project_dir, source="project"))

        # 3. Pip / 入口点插件
        manifests.extend(self._scan_entry_points())

        # 加载每个清单（跳过用户禁用的插件）
        disabled = _get_disabled_plugins()
        for manifest in manifests:
            if manifest.name in disabled:
                loaded = LoadedPlugin(manifest=manifest, enabled=False)
                loaded.error = "disabled via config"
                self._plugins[manifest.name] = loaded
                logger.debug("Skipping disabled plugin '%s'", manifest.name)
                continue
            self._load_plugin(manifest)

        if manifests:
            logger.info(
                "Plugin discovery complete: %d found, %d enabled",
                len(self._plugins),
                sum(1 for p in self._plugins.values() if p.enabled),
            )

    # -----------------------------------------------------------------------
    # 目录扫描
    # -----------------------------------------------------------------------

    def _scan_directory(self, path: Path, source: str) -> List[PluginManifest]:
        """从 *path* 的子目录中读取 ``plugin.yaml`` 清单。"""
        manifests: List[PluginManifest] = []
        if not path.is_dir():
            return manifests

        for child in sorted(path.iterdir()):
            if not child.is_dir():
                continue
            manifest_file = child / "plugin.yaml"
            if not manifest_file.exists():
                manifest_file = child / "plugin.yml"
            if not manifest_file.exists():
                logger.debug("Skipping %s (no plugin.yaml)", child)
                continue

            try:
                if yaml is None:
                    logger.warning("PyYAML not installed – cannot load %s", manifest_file)
                    continue
                data = yaml.safe_load(manifest_file.read_text()) or {}
                manifest = PluginManifest(
                    name=data.get("name", child.name),
                    version=str(data.get("version", "")),
                    description=data.get("description", ""),
                    author=data.get("author", ""),
                    requires_env=data.get("requires_env", []),
                    provides_tools=data.get("provides_tools", []),
                    provides_hooks=data.get("provides_hooks", []),
                    source=source,
                    path=str(child),
                )
                manifests.append(manifest)
            except Exception as exc:
                logger.warning("Failed to parse %s: %s", manifest_file, exc)

        return manifests

    # -----------------------------------------------------------------------
    # 入口点扫描
    # -----------------------------------------------------------------------

    def _scan_entry_points(self) -> List[PluginManifest]:
        """检查 ``importlib.metadata`` 中 pip 安装的插件。"""
        manifests: List[PluginManifest] = []
        try:
            eps = importlib.metadata.entry_points()
            # Python 3.12+ 返回 SelectableGroups；更早版本返回 dict
            if hasattr(eps, "select"):
                group_eps = eps.select(group=ENTRY_POINTS_GROUP)
            elif isinstance(eps, dict):
                group_eps = eps.get(ENTRY_POINTS_GROUP, [])
            else:
                group_eps = [ep for ep in eps if ep.group == ENTRY_POINTS_GROUP]

            for ep in group_eps:
                manifest = PluginManifest(
                    name=ep.name,
                    source="entrypoint",
                    path=ep.value,
                )
                manifests.append(manifest)
        except Exception as exc:
            logger.debug("Entry-point scan failed: %s", exc)

        return manifests

    # -----------------------------------------------------------------------
    # 加载
    # -----------------------------------------------------------------------

    def _load_plugin(self, manifest: PluginManifest) -> None:
        """导入插件模块并调用其 ``register(ctx)`` 函数。"""
        loaded = LoadedPlugin(manifest=manifest)

        try:
            if manifest.source in ("user", "project"):
                module = self._load_directory_module(manifest)
            else:
                module = self._load_entrypoint_module(manifest)

            loaded.module = module

            # 调用 register()
            register_fn = getattr(module, "register", None)
            if register_fn is None:
                loaded.error = "no register() function"
                logger.warning("Plugin '%s' has no register() function", manifest.name)
            else:
                ctx = PluginContext(manifest, self)
                register_fn(ctx)
                loaded.tools_registered = [
                    t for t in self._plugin_tool_names
                    if t not in {
                        n
                        for name, p in self._plugins.items()
                        for n in p.tools_registered
                    }
                ]
                loaded.hooks_registered = list(
                    {
                        h
                        for h, cbs in self._hooks.items()
                        if cbs  # non-empty
                    }
                    - {
                        h
                        for name, p in self._plugins.items()
                        for h in p.hooks_registered
                    }
                )
                loaded.commands_registered = [
                    c for c in self._plugin_commands
                    if self._plugin_commands[c].get("plugin") == manifest.name
                ]
                loaded.enabled = True

        except Exception as exc:
            loaded.error = str(exc)
            logger.warning("Failed to load plugin '%s': %s", manifest.name, exc)

        self._plugins[manifest.name] = loaded

    def _load_directory_module(self, manifest: PluginManifest) -> types.ModuleType:
        """将基于目录的插件导入为 ``hermes_plugins.<name>``。"""
        plugin_dir = Path(manifest.path)  # type: ignore[arg-type]
        init_file = plugin_dir / "__init__.py"
        if not init_file.exists():
            raise FileNotFoundError(f"No __init__.py in {plugin_dir}")

        # 确保命名空间父包存在
        if _NS_PARENT not in sys.modules:
            ns_pkg = types.ModuleType(_NS_PARENT)
            ns_pkg.__path__ = []  # type: ignore[attr-defined]
            ns_pkg.__package__ = _NS_PARENT
            sys.modules[_NS_PARENT] = ns_pkg

        module_name = f"{_NS_PARENT}.{manifest.name.replace('-', '_')}"
        spec = importlib.util.spec_from_file_location(
            module_name,
            init_file,
            submodule_search_locations=[str(plugin_dir)],
        )
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot create module spec for {init_file}")

        module = importlib.util.module_from_spec(spec)
        module.__package__ = module_name
        module.__path__ = [str(plugin_dir)]  # type: ignore[attr-defined]
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module

    def _load_entrypoint_module(self, manifest: PluginManifest) -> types.ModuleType:
        """通过入口点引用加载 pip 安装的插件。"""
        eps = importlib.metadata.entry_points()
        if hasattr(eps, "select"):
            group_eps = eps.select(group=ENTRY_POINTS_GROUP)
        elif isinstance(eps, dict):
            group_eps = eps.get(ENTRY_POINTS_GROUP, [])
        else:
            group_eps = [ep for ep in eps if ep.group == ENTRY_POINTS_GROUP]

        for ep in group_eps:
            if ep.name == manifest.name:
                return ep.load()

        raise ImportError(
            f"Entry point '{manifest.name}' not found in group '{ENTRY_POINTS_GROUP}'"
        )

    # -----------------------------------------------------------------------
    # 钩子调用
    # -----------------------------------------------------------------------

    def invoke_hook(self, hook_name: str, **kwargs: Any) -> List[Any]:
        """调用 *hook_name* 的所有已注册回调。

        每个回调都包装在自己的 try/except 中，以便行为不当的
        插件不会破坏核心智能体循环。

        返回回调的非 ``None`` 返回值列表。

        对于 ``pre_llm_call``，回调可以返回一个字典来描述
        要注入到当前回合用户消息中的上下文::

            {"context": "recalled text..."}
            "recalled text..."          # 纯字符串，等效

        上下文始终注入到用户消息中，而不是系统提示中。
        这保持了提示缓存前缀——系统提示在各回合间保持相同，
        因此缓存的 token 会被重用。所有注入的上下文都是临时的——
        不会持久化到会话数据库中。
        """
        callbacks = self._hooks.get(hook_name, [])
        results: List[Any] = []
        for cb in callbacks:
            try:
                ret = cb(**kwargs)
                if ret is not None:
                    results.append(ret)
            except Exception as exc:
                logger.warning(
                    "Hook '%s' callback %s raised: %s",
                    hook_name,
                    getattr(cb, "__name__", repr(cb)),
                    exc,
                )
        return results

    # -----------------------------------------------------------------------
    # 内省
    # -----------------------------------------------------------------------

    def list_plugins(self) -> List[Dict[str, Any]]:
        """返回所有已发现插件的信息字典列表。"""
        result: List[Dict[str, Any]] = []
        for name, loaded in sorted(self._plugins.items()):
            result.append(
                {
                    "name": name,
                    "version": loaded.manifest.version,
                    "description": loaded.manifest.description,
                    "source": loaded.manifest.source,
                    "enabled": loaded.enabled,
                    "tools": len(loaded.tools_registered),
                    "hooks": len(loaded.hooks_registered),
                    "commands": len(loaded.commands_registered),
                    "error": loaded.error,
                }
            )
        return result

    # -----------------------------------------------------------------------
    # 插件技能查找
    # -----------------------------------------------------------------------

    def find_plugin_skill(self, qualified_name: str) -> Optional[Path]:
        """返回插件技能的 SKILL.md 的 ``Path``，或 ``None``。"""
        entry = self._plugin_skills.get(qualified_name)
        return entry["path"] if entry else None

    def list_plugin_skills(self, plugin_name: str) -> List[str]:
        """返回 *plugin_name* 注册的所有技能的排序裸名称。"""
        prefix = f"{plugin_name}:"
        return sorted(
            e["bare_name"]
            for qn, e in self._plugin_skills.items()
            if qn.startswith(prefix)
        )

    def remove_plugin_skill(self, qualified_name: str) -> None:
        """移除过期的注册表条目（静默忽略缺失的键）。"""
        self._plugin_skills.pop(qualified_name, None)


# ---------------------------------------------------------------------------
# 模块级单例和便捷函数
# ---------------------------------------------------------------------------

_plugin_manager: Optional[PluginManager] = None


def get_plugin_manager() -> PluginManager:
    """返回（并惰性创建）全局 PluginManager 单例。"""
    global _plugin_manager
    if _plugin_manager is None:
        _plugin_manager = PluginManager()
    return _plugin_manager


def discover_plugins() -> None:
    """发现并加载所有插件（幂等操作）。"""
    get_plugin_manager().discover_and_load()


def invoke_hook(hook_name: str, **kwargs: Any) -> List[Any]:
    """在所有已加载的插件上调用生命周期钩子。

    返回插件回调的非 ``None`` 返回值列表。
    """
    return get_plugin_manager().invoke_hook(hook_name, **kwargs)



def get_pre_tool_call_block_message(
    tool_name: str,
    args: Optional[Dict[str, Any]],
    task_id: str = "",
    session_id: str = "",
    tool_call_id: str = "",
) -> Optional[str]:
    """检查 ``pre_tool_call`` 钩子是否有阻止指令。

    需要执行策略（速率限制、安全限制、审批工作流）的插件
    可以从其 ``pre_tool_call`` 回调中返回::

        {"action": "block", "message": "工具被阻止的原因"}

    第一个有效的阻止指令获胜。无效或无关的钩子返回值
    被静默忽略，以便现有的仅观察钩子不受影响。
    """
    hook_results = invoke_hook(
        "pre_tool_call",
        tool_name=tool_name,
        args=args if isinstance(args, dict) else {},
        task_id=task_id,
        session_id=session_id,
        tool_call_id=tool_call_id,
    )

    for result in hook_results:
        if not isinstance(result, dict):
            continue
        if result.get("action") != "block":
            continue
        message = result.get("message")
        if isinstance(message, str) and message:
            return message

    return None


def get_plugin_context_engine():
    """返回插件注册的上下文引擎，或 None。"""
    return get_plugin_manager()._context_engine


def get_plugin_command_handler(name: str) -> Optional[Callable]:
    """返回插件注册的斜杠命令的处理函数，或 ``None``。"""
    entry = get_plugin_manager()._plugin_commands.get(name)
    return entry["handler"] if entry else None


def get_plugin_commands() -> Dict[str, dict]:
    """返回完整的插件命令字典（name -> {handler, description, plugin}）。

    可以在发现之前安全调用——如果没有加载插件则返回空字典。
    """
    return get_plugin_manager()._plugin_commands


def get_plugin_toolsets() -> List[tuple]:
    """返回插件工具集为 ``(key, label, description)`` 元组。

    由 ``hermes tools`` TUI 使用，以便插件提供的工具集
    与内置工具集一起显示并可按平台切换开/关。
    """
    manager = get_plugin_manager()
    if not manager._plugin_tool_names:
        return []

    try:
        from tools.registry import registry
    except Exception:
        return []

    # 按工具集对插件工具名称进行分组
    toolset_tools: Dict[str, List[str]] = {}
    toolset_plugin: Dict[str, LoadedPlugin] = {}
    for tool_name in manager._plugin_tool_names:
        entry = registry.get_entry(tool_name)
        if not entry:
            continue
        ts = entry.toolset
        toolset_tools.setdefault(ts, []).append(entry.name)

    # 将工具集映射回注册它们的插件
    for _name, loaded in manager._plugins.items():
        for tool_name in loaded.tools_registered:
            entry = registry.get_entry(tool_name)
            if entry and entry.toolset in toolset_tools:
                toolset_plugin.setdefault(entry.toolset, loaded)

    result = []
    for ts_key in sorted(toolset_tools):
        plugin = toolset_plugin.get(ts_key)
        label = f"🔌 {ts_key.replace('_', ' ').title()}"
        if plugin and plugin.manifest.description:
            desc = plugin.manifest.description
        else:
            desc = ", ".join(sorted(toolset_tools[ts_key]))
        result.append((ts_key, label, desc))

    return result
