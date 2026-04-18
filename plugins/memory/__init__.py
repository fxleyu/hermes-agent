"""记忆提供者插件发现模块。

扫描两个目录以查找记忆提供者插件：

1. 内置提供者：``plugins/memory/<name>/``（随 hermes-agent 一起发布）
2. 用户安装的提供者：``$HERMES_HOME/plugins/<name>/``

每个子目录必须包含 ``__init__.py``，其中有一个实现了 MemoryProvider 抽象基类的类。
名称冲突时，内置提供者优先。

同一时间只能激活一个提供者，通过 config.yaml 中的
``memory.provider`` 选项选择。

用法:
    from plugins.memory import discover_memory_providers, load_memory_provider

    available = discover_memory_providers()   # [(名称, 描述, 是否可用), ...]
    provider = load_memory_provider("mnemosyne")  # MemoryProvider 实例
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import sys
from pathlib import Path
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

_MEMORY_PLUGINS_DIR = Path(__file__).parent


# ---------------------------------------------------------------------------
# 目录辅助函数
# ---------------------------------------------------------------------------

def _get_user_plugins_dir() -> Optional[Path]:
    """返回 ``$HERMES_HOME/plugins/`` 路径，如果不可用则返回 None。"""
    try:
        from hermes_constants import get_hermes_home
        d = get_hermes_home() / "plugins"
        return d if d.is_dir() else None
    except Exception:
        return None


def _is_memory_provider_dir(path: Path) -> bool:
    """启发式判断：*path* 是否像一个记忆提供者插件目录？

    通过检查 ``__init__.py`` 源代码中是否包含 ``register_memory_provider``
    或 ``MemoryProvider`` 来判断。使用廉价的文本扫描 — 无需导入模块。
    """
    init_file = path / "__init__.py"
    if not init_file.exists():
        return False
    try:
        source = init_file.read_text(errors="replace")[:8192]
        return "register_memory_provider" in source or "MemoryProvider" in source
    except Exception:
        return False


def _iter_provider_dirs() -> List[Tuple[str, Path]]:
    """生成所有已发现的提供者目录的 ``(名称, 路径)`` 对。

    先扫描内置提供者，再扫描用户安装的提供者。名称冲突时内置提供者优先
    （通过 ``seen`` 集合实现先到先得）。
    """
    seen: set = set()
    dirs: List[Tuple[str, Path]] = []

    # 1. 内置提供者 (plugins/memory/<name>/)
    if _MEMORY_PLUGINS_DIR.is_dir():
        for child in sorted(_MEMORY_PLUGINS_DIR.iterdir()):
            if not child.is_dir() or child.name.startswith(("_", ".")):
                continue
            if not (child / "__init__.py").exists():
                continue
            seen.add(child.name)
            dirs.append((child.name, child))

    # 2. 用户安装的提供者 ($HERMES_HOME/plugins/<name>/)
    user_dir = _get_user_plugins_dir()
    if user_dir:
        for child in sorted(user_dir.iterdir()):
            if not child.is_dir() or child.name.startswith(("_", ".")):
                continue
            if child.name in seen:
                continue  # 内置提供者优先
            if not _is_memory_provider_dir(child):
                continue  # 跳过非记忆插件目录
            dirs.append((child.name, child))

    return dirs


def find_provider_dir(name: str) -> Optional[Path]:
    """根据提供者名称解析其目录路径。

    先检查内置提供者，再检查用户安装的提供者。
    """
    # 内置提供者
    bundled = _MEMORY_PLUGINS_DIR / name
    if bundled.is_dir() and (bundled / "__init__.py").exists():
        return bundled
    # 用户安装的提供者
    user_dir = _get_user_plugins_dir()
    if user_dir:
        user = user_dir / name
        if user.is_dir() and _is_memory_provider_dir(user):
            return user
    return None


# ---------------------------------------------------------------------------
# 公共 API
# ---------------------------------------------------------------------------

def discover_memory_providers() -> List[Tuple[str, str, bool]]:
    """扫描内置和用户安装目录以查找可用的提供者。

    返回 (名称, 描述, 是否可用) 元组的列表。
    名称冲突时内置提供者优先。
    """
    results = []

    for name, child in _iter_provider_dirs():
        # 如果有 plugin.yaml，从中读取描述信息
        desc = ""
        yaml_file = child / "plugin.yaml"
        if yaml_file.exists():
            try:
                import yaml
                with open(yaml_file) as f:
                    meta = yaml.safe_load(f) or {}
                desc = meta.get("description", "")
            except Exception:
                pass

        # 快速可用性检查 — 尝试加载并调用 is_available()
        available = True
        try:
            provider = _load_provider_from_dir(child)
            if provider:
                available = provider.is_available()
            else:
                available = False
        except Exception:
            available = False

        results.append((name, desc, available))

    return results


def load_memory_provider(name: str) -> Optional["MemoryProvider"]:
    """根据名称加载并返回 MemoryProvider 实例。

    同时检查内置（``plugins/memory/<name>/``）和用户安装
    （``$HERMES_HOME/plugins/<name>/``）目录。名称冲突时内置提供者优先。

    如果提供者未找到或加载失败，返回 None。
    """
    provider_dir = find_provider_dir(name)
    if not provider_dir:
        logger.debug("Memory provider '%s' not found in bundled or user plugins", name)
        return None

    try:
        provider = _load_provider_from_dir(provider_dir)
        if provider:
            return provider
        logger.warning("Memory provider '%s' loaded but no provider instance found", name)
        return None
    except Exception as e:
        logger.warning("Failed to load memory provider '%s': %s", name, e)
        return None


def _load_provider_from_dir(provider_dir: Path) -> Optional["MemoryProvider"]:
    """导入提供者模块并提取 MemoryProvider 实例。

    模块必须具有以下其一：
    - register(ctx) 函数（插件风格）— 我们模拟一个 ctx 对象
    - 继承 MemoryProvider 的顶层类 — 我们将其实例化
    """
    name = provider_dir.name
    # 为用户安装的插件使用独立的命名空间，避免与内置提供者在 sys.modules 中冲突
    _is_bundled = _MEMORY_PLUGINS_DIR in provider_dir.parents or provider_dir.parent == _MEMORY_PLUGINS_DIR
    module_name = f"plugins.memory.{name}" if _is_bundled else f"_hermes_user_memory.{name}"
    init_file = provider_dir / "__init__.py"

    if not init_file.exists():
        return None

    # 检查是否已加载过该模块
    if module_name in sys.modules:
        mod = sys.modules[module_name]
    else:
        # 处理插件内的相对导入
        # 首先确保父包已注册到 sys.modules
        for parent in ("plugins", "plugins.memory"):
            if parent not in sys.modules:
                parent_path = Path(__file__).parent
                if parent == "plugins":
                    parent_path = parent_path.parent
                parent_init = parent_path / "__init__.py"
                if parent_init.exists():
                    spec = importlib.util.spec_from_file_location(
                        parent, str(parent_init),
                        submodule_search_locations=[str(parent_path)]
                    )
                    if spec:
                        parent_mod = importlib.util.module_from_spec(spec)
                        sys.modules[parent] = parent_mod
                        try:
                            spec.loader.exec_module(parent_mod)
                        except Exception:
                            pass

        # 现在加载提供者模块
        spec = importlib.util.spec_from_file_location(
            module_name, str(init_file),
            submodule_search_locations=[str(provider_dir)]
        )
        if not spec:
            return None

        mod = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = mod

        # 注册子模块以便相对导入能正常工作
        # 例如 holographic 插件中的 "from .store import MemoryStore"
        for sub_file in provider_dir.glob("*.py"):
            if sub_file.name == "__init__.py":
                continue
            sub_name = sub_file.stem
            full_sub_name = f"{module_name}.{sub_name}"
            if full_sub_name not in sys.modules:
                sub_spec = importlib.util.spec_from_file_location(
                    full_sub_name, str(sub_file)
                )
                if sub_spec:
                    sub_mod = importlib.util.module_from_spec(sub_spec)
                    sys.modules[full_sub_name] = sub_mod
                    try:
                        sub_spec.loader.exec_module(sub_mod)
                    except Exception as e:
                        logger.debug("Failed to load submodule %s: %s", full_sub_name, e)

        try:
            spec.loader.exec_module(mod)
        except Exception as e:
            logger.debug("Failed to exec_module %s: %s", module_name, e)
            sys.modules.pop(module_name, None)
            return None

    # 首先尝试 register(ctx) 模式（我们插件的标准编写方式）
    if hasattr(mod, "register"):
        collector = _ProviderCollector()
        try:
            mod.register(collector)
            if collector.provider:
                return collector.provider
        except Exception as e:
            logger.debug("register() failed for %s: %s", name, e)

    # 回退方案：查找 MemoryProvider 子类并实例化
    from agent.memory_provider import MemoryProvider
    for attr_name in dir(mod):
        attr = getattr(mod, attr_name, None)
        if (isinstance(attr, type) and issubclass(attr, MemoryProvider)
                and attr is not MemoryProvider):
            try:
                return attr()
            except Exception:
                pass

    return None


class _ProviderCollector:
    """模拟的插件上下文对象，用于捕获 register_memory_provider 调用。"""

    def __init__(self):
        self.provider = None

    def register_memory_provider(self, provider):
        self.provider = provider

    # 其他注册方法的空操作实现
    def register_tool(self, *args, **kwargs):
        pass

    def register_hook(self, *args, **kwargs):
        pass

    def register_cli_command(self, *args, **kwargs):
        pass  # CLI 注册通过 discover_plugin_cli_commands() 完成


def _get_active_memory_provider() -> Optional[str]:
    """从 config.yaml 读取当前活跃的记忆提供者名称。

    返回提供者名称（例如 ``"honcho"``），如果未配置外部提供者则返回 None。
    轻量级操作 — 仅读取配置，不加载插件。
    """
    try:
        from hermes_cli.config import load_config
        config = load_config()
        return config.get("memory", {}).get("provider") or None
    except Exception:
        return None


def discover_plugin_cli_commands() -> List[dict]:
    """仅返回**当前活跃**记忆插件的 CLI 命令。

    同一时间只能有一个记忆提供者处于活跃状态（通过 config.yaml 中的
    ``memory.provider`` 设置）。此函数读取该值，仅加载匹配插件的
    CLI 注册信息。如果没有活跃的提供者，则不注册任何命令。

    在活跃插件的 ``cli.py`` 中查找 ``register_cli(subparser)`` 函数。
    返回最多包含一个字典的列表，字典键包括：``name``、``help``、
    ``description``、``setup_fn``、``handler_fn``。

    这是一个轻量级扫描 — 仅导入 ``cli.py``，不导入完整的插件模块。
    可在 argparse 设置阶段安全调用，此时尚未加载任何提供者。
    """
    results: List[dict] = []
    if not _MEMORY_PLUGINS_DIR.is_dir():
        return results

    active_provider = _get_active_memory_provider()
    if not active_provider:
        return results

    # 仅查看活跃提供者的目录
    plugin_dir = find_provider_dir(active_provider)
    if not plugin_dir:
        return results

    cli_file = plugin_dir / "cli.py"
    if not cli_file.exists():
        return results

    _is_bundled = _MEMORY_PLUGINS_DIR in plugin_dir.parents or plugin_dir.parent == _MEMORY_PLUGINS_DIR
    module_name = f"plugins.memory.{active_provider}.cli" if _is_bundled else f"_hermes_user_memory.{active_provider}.cli"
    try:
        # 导入 CLI 模块（轻量级 — 不需要 SDK）
        if module_name in sys.modules:
            cli_mod = sys.modules[module_name]
        else:
            spec = importlib.util.spec_from_file_location(
                module_name, str(cli_file)
            )
            if not spec or not spec.loader:
                return results
            cli_mod = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = cli_mod
            spec.loader.exec_module(cli_mod)

        register_cli = getattr(cli_mod, "register_cli", None)
        if not callable(register_cli):
            return results

        # Read metadata from plugin.yaml if available
        help_text = f"Manage {active_provider} memory plugin"
        description = ""
        yaml_file = plugin_dir / "plugin.yaml"
        if yaml_file.exists():
            try:
                import yaml
                with open(yaml_file) as f:
                    meta = yaml.safe_load(f) or {}
                desc = meta.get("description", "")
                if desc:
                    help_text = desc
                    description = desc
            except Exception:
                pass

        handler_fn = getattr(cli_mod, f"{active_provider}_command", None) or \
                     getattr(cli_mod, "honcho_command", None)

        results.append({
            "name": active_provider,
            "help": help_text,
            "description": description,
            "setup_fn": register_cli,
            "handler_fn": handler_fn,
            "plugin": active_provider,
        })
    except Exception as e:
        logger.debug("Failed to scan CLI for memory plugin '%s': %s", active_provider, e)

    return results
