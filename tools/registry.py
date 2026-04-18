"""hermes-agent 所有工具的中央注册表。

每个工具文件在模块级别调用 ``registry.register()`` 来声明其
schema、处理函数、工具集归属和可用性检查。``model_tools.py``
查询注册表，而不是维护自己的并行数据结构。

导入链（循环导入安全）：
    tools/registry.py  （不从 model_tools 或工具文件导入）
           ^
    tools/*.py  （在模块级别从 tools.registry 导入）
           ^
    model_tools.py  （导入 tools.registry + 所有工具模块）
           ^
    run_agent.py、cli.py、batch_runner.py 等。
"""

import ast
import importlib
import json
import logging
import threading
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


def _is_registry_register_call(node: ast.AST) -> bool:
    """当 *node* 是 ``registry.register(...)`` 调用表达式时返回 True。"""
    if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
        return False
    func = node.value.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "register"
        and isinstance(func.value, ast.Name)
        and func.value.id == "registry"
    )


def _module_registers_tools(module_path: Path) -> bool:
    """当模块包含顶层 ``registry.register(...)`` 调用时返回 True。

    只检查模块体语句，这样那些恰好在函数内部调用
    ``registry.register()`` 的辅助模块就不会被误识别。
    """
    try:
        source = module_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(module_path))
    except (OSError, SyntaxError):
        return False

    return any(_is_registry_register_call(stmt) for stmt in tree.body)


def discover_builtin_tools(tools_dir: Optional[Path] = None) -> List[str]:
    """导入内置的自注册工具模块并返回其模块名称列表。"""
    tools_path = Path(tools_dir) if tools_dir is not None else Path(__file__).resolve().parent
    module_names = [
        f"tools.{path.stem}"
        for path in sorted(tools_path.glob("*.py"))
        if path.name not in {"__init__.py", "registry.py", "mcp_tool.py"}
        and _module_registers_tools(path)
    ]

    imported: List[str] = []
    for mod_name in module_names:
        try:
            importlib.import_module(mod_name)
            imported.append(mod_name)
        except Exception as e:
            logger.warning("Could not import tool module %s: %s", mod_name, e)
    return imported


class ToolEntry:
    """单个已注册工具的元数据。"""

    __slots__ = (
        "name", "toolset", "schema", "handler", "check_fn",
        "requires_env", "is_async", "description", "emoji",
        "max_result_size_chars",
    )

    def __init__(self, name, toolset, schema, handler, check_fn,
                 requires_env, is_async, description, emoji,
                 max_result_size_chars=None):
        self.name = name
        self.toolset = toolset
        self.schema = schema
        self.handler = handler
        self.check_fn = check_fn
        self.requires_env = requires_env
        self.is_async = is_async
        self.description = description
        self.emoji = emoji
        self.max_result_size_chars = max_result_size_chars


class ToolRegistry:
    """收集工具文件中的 schema 和处理函数的单例注册表。"""

    def __init__(self):
        self._tools: Dict[str, ToolEntry] = {}
        self._toolset_checks: Dict[str, Callable] = {}
        self._toolset_aliases: Dict[str, str] = {}
        # MCP 动态刷新可能在其他线程读取工具元数据时修改注册表，
        # 因此保持写操作串行化，读操作使用稳定快照。
        self._lock = threading.RLock()

    def _snapshot_state(self) -> tuple[List[ToolEntry], Dict[str, Callable]]:
        """返回注册表条目和工具集检查的一致性快照。"""
        with self._lock:
            return list(self._tools.values()), dict(self._toolset_checks)

    def _snapshot_entries(self) -> List[ToolEntry]:
        """返回已注册工具条目的稳定快照。"""
        return self._snapshot_state()[0]

    def _snapshot_toolset_checks(self) -> Dict[str, Callable]:
        """返回工具集可用性检查的稳定快照。"""
        return self._snapshot_state()[1]

    def _evaluate_toolset_check(self, toolset: str, check: Callable | None) -> bool:
        """运行工具集检查，缺失或失败的检查视为不可用/可用。"""
        if not check:
            return True
        try:
            return bool(check())
        except Exception:
            logger.debug("Toolset %s check raised; marking unavailable", toolset)
            return False

    def get_entry(self, name: str) -> Optional[ToolEntry]:
        """按名称返回已注册的工具条目，不存在则返回 None。"""
        with self._lock:
            return self._tools.get(name)

    def get_registered_toolset_names(self) -> List[str]:
        """返回注册表中存在的已排序唯一工具集名称。"""
        return sorted({entry.toolset for entry in self._snapshot_entries()})

    def get_tool_names_for_toolset(self, toolset: str) -> List[str]:
        """返回给定工具集下已排序的工具名称列表。"""
        return sorted(
            entry.name for entry in self._snapshot_entries()
            if entry.toolset == toolset
        )

    def register_toolset_alias(self, alias: str, toolset: str) -> None:
        """为规范工具集名称注册一个显式别名。"""
        with self._lock:
            existing = self._toolset_aliases.get(alias)
            if existing and existing != toolset:
                logger.warning(
                    "Toolset alias collision: '%s' (%s) overwritten by %s",
                    alias, existing, toolset,
                )
            self._toolset_aliases[alias] = toolset

    def get_registered_toolset_aliases(self) -> Dict[str, str]:
        """返回 ``{别名: 规范工具集名称}`` 映射的快照。"""
        with self._lock:
            return dict(self._toolset_aliases)

    def get_toolset_alias_target(self, alias: str) -> Optional[str]:
        """返回别名对应的规范工具集名称，不存在则返回 None。"""
        with self._lock:
            return self._toolset_aliases.get(alias)

    # ------------------------------------------------------------------
    # 注册
    # ------------------------------------------------------------------

    def register(
        self,
        name: str,
        toolset: str,
        schema: dict,
        handler: Callable,
        check_fn: Callable = None,
        requires_env: list = None,
        is_async: bool = False,
        description: str = "",
        emoji: str = "",
        max_result_size_chars: int | float | None = None,
    ):
        """注册一个工具。在模块导入时由每个工具文件调用。"""
        with self._lock:
            existing = self._tools.get(name)
            if existing and existing.toolset != toolset:
                # 允许 MCP 之间的覆盖（合法场景：服务器刷新，
                # 或两个 MCP 服务器工具名称重叠）。
                both_mcp = (
                    existing.toolset.startswith("mcp-")
                    and toolset.startswith("mcp-")
                )
                if both_mcp:
                    logger.debug(
                        "Tool '%s': MCP toolset '%s' overwriting MCP toolset '%s'",
                        name, toolset, existing.toolset,
                    )
                else:
                    # 拒绝遮蔽 -- 防止插件/MCP 覆盖内置工具，反之亦然。
                    logger.error(
                        "Tool registration REJECTED: '%s' (toolset '%s') would "
                        "shadow existing tool from toolset '%s'. Deregister the "
                        "existing tool first if this is intentional.",
                        name, toolset, existing.toolset,
                    )
                    return
            self._tools[name] = ToolEntry(
                name=name,
                toolset=toolset,
                schema=schema,
                handler=handler,
                check_fn=check_fn,
                requires_env=requires_env or [],
                is_async=is_async,
                description=description or schema.get("description", ""),
                emoji=emoji,
                max_result_size_chars=max_result_size_chars,
            )
            if check_fn and toolset not in self._toolset_checks:
                self._toolset_checks[toolset] = check_fn

    def deregister(self, name: str) -> None:
        """从注册表中移除一个工具。

        如果同一工具集中没有其他工具，同时清理工具集检查和别名。
        用于 MCP 动态工具发现在服务器发送
        ``notifications/tools/list_changed`` 时进行全量刷新。
        """
        with self._lock:
            entry = self._tools.pop(name, None)
            if entry is None:
                return
            # 如果这是该工具集中的最后一个工具，则移除工具集检查和别名
            toolset_still_exists = any(
                e.toolset == entry.toolset for e in self._tools.values()
            )
            if not toolset_still_exists:
                self._toolset_checks.pop(entry.toolset, None)
                self._toolset_aliases = {
                    alias: target
                    for alias, target in self._toolset_aliases.items()
                    if target != entry.toolset
                }
        logger.debug("Deregistered tool: %s", name)

    # ------------------------------------------------------------------
    # Schema 获取
    # ------------------------------------------------------------------

    def get_definitions(self, tool_names: Set[str], quiet: bool = False) -> List[dict]:
        """返回请求工具名称的 OpenAI 格式工具 schema。

        只有 ``check_fn()`` 返回 True（或没有 check_fn）的工具才会被包含。
        """
        result = []
        check_results: Dict[Callable, bool] = {}
        entries_by_name = {entry.name: entry for entry in self._snapshot_entries()}
        for name in sorted(tool_names):
            entry = entries_by_name.get(name)
            if not entry:
                continue
            if entry.check_fn:
                if entry.check_fn not in check_results:
                    try:
                        check_results[entry.check_fn] = bool(entry.check_fn())
                    except Exception:
                        check_results[entry.check_fn] = False
                        if not quiet:
                            logger.debug("Tool %s check raised; skipping", name)
                if not check_results[entry.check_fn]:
                    if not quiet:
                        logger.debug("Tool %s unavailable (check failed)", name)
                    continue
            # 确保 schema 始终有 "name" 字段 -- 使用 entry.name 作为回退
            schema_with_name = {**entry.schema, "name": entry.name}
            result.append({"type": "function", "function": schema_with_name})
        return result

    # ------------------------------------------------------------------
    # 调度
    # ------------------------------------------------------------------

    def dispatch(self, name: str, args: dict, **kwargs) -> str:
        """按名称执行工具处理函数。

        * 异步处理函数通过 ``_run_async()`` 自动桥接。
        * 所有异常被捕获并以 ``{"error": "..."}`` 格式返回，
          确保错误格式的一致性。
        """
        entry = self.get_entry(name)
        if not entry:
            return json.dumps({"error": f"Unknown tool: {name}"})
        try:
            if entry.is_async:
                from model_tools import _run_async
                return _run_async(entry.handler(args, **kwargs))
            return entry.handler(args, **kwargs)
        except Exception as e:
            logger.exception("Tool %s dispatch error: %s", name, e)
            return json.dumps({"error": f"Tool execution failed: {type(e).__name__}: {e}"})

    # ------------------------------------------------------------------
    # 查询辅助方法（替代 model_tools.py 中的冗余字典）
    # ------------------------------------------------------------------

    def get_max_result_size(self, name: str, default: int | float | None = None) -> int | float:
        """返回单个工具的最大结果大小，或 *default*（或全局默认值）。"""
        entry = self.get_entry(name)
        if entry and entry.max_result_size_chars is not None:
            return entry.max_result_size_chars
        if default is not None:
            return default
        from tools.budget_config import DEFAULT_RESULT_SIZE_CHARS
        return DEFAULT_RESULT_SIZE_CHARS

    def get_all_tool_names(self) -> List[str]:
        """返回所有已注册工具名称的排序列表。"""
        return sorted(entry.name for entry in self._snapshot_entries())

    def get_schema(self, name: str) -> Optional[dict]:
        """返回工具的原始 schema 字典，绕过 check_fn 过滤。

        适用于 token 估算和内省场景，在这些场景中
        可用性无关紧要 -- 只关心 schema 内容本身。
        """
        entry = self.get_entry(name)
        return entry.schema if entry else None

    def get_toolset_for_tool(self, name: str) -> Optional[str]:
        """返回工具所属的工具集，不存在则返回 None。"""
        entry = self.get_entry(name)
        return entry.toolset if entry else None

    def get_emoji(self, name: str, default: str = "⚡") -> str:
        """返回工具的 emoji，未设置则返回 *default*。"""
        entry = self.get_entry(name)
        return (entry.emoji if entry and entry.emoji else default)

    def get_tool_to_toolset_map(self) -> Dict[str, str]:
        """返回所有已注册工具的 ``{工具名称: 工具集名称}`` 映射。"""
        return {entry.name: entry.toolset for entry in self._snapshot_entries()}

    def is_toolset_available(self, toolset: str) -> bool:
        """检查工具集的依赖是否满足。

        当检查函数抛出意外异常（如网络错误、缺少导入、配置错误）时
        返回 False（而不是崩溃）。
        """
        with self._lock:
            check = self._toolset_checks.get(toolset)
        return self._evaluate_toolset_check(toolset, check)

    def check_toolset_requirements(self) -> Dict[str, bool]:
        """返回所有工具集的 ``{工具集: 可用性布尔值}``。"""
        entries, toolset_checks = self._snapshot_state()
        toolsets = sorted({entry.toolset for entry in entries})
        return {
            toolset: self._evaluate_toolset_check(toolset, toolset_checks.get(toolset))
            for toolset in toolsets
        }

    def get_available_toolsets(self) -> Dict[str, dict]:
        """返回用于 UI 显示的工具集元数据。"""
        toolsets: Dict[str, dict] = {}
        entries, toolset_checks = self._snapshot_state()
        for entry in entries:
            ts = entry.toolset
            if ts not in toolsets:
                toolsets[ts] = {
                    "available": self._evaluate_toolset_check(
                        ts, toolset_checks.get(ts)
                    ),
                    "tools": [],
                    "description": "",
                    "requirements": [],
                }
            toolsets[ts]["tools"].append(entry.name)
            if entry.requires_env:
                for env in entry.requires_env:
                    if env not in toolsets[ts]["requirements"]:
                        toolsets[ts]["requirements"].append(env)
        return toolsets

    def get_toolset_requirements(self) -> Dict[str, dict]:
        """构建与 TOOLSET_REQUIREMENTS 兼容的字典，用于向后兼容。"""
        result: Dict[str, dict] = {}
        entries, toolset_checks = self._snapshot_state()
        for entry in entries:
            ts = entry.toolset
            if ts not in result:
                result[ts] = {
                    "name": ts,
                    "env_vars": [],
                    "check_fn": toolset_checks.get(ts),
                    "setup_url": None,
                    "tools": [],
                }
            if entry.name not in result[ts]["tools"]:
                result[ts]["tools"].append(entry.name)
            for env in entry.requires_env:
                if env not in result[ts]["env_vars"]:
                    result[ts]["env_vars"].append(env)
        return result

    def check_tool_availability(self, quiet: bool = False):
        """返回 (可用工具集, 不可用信息)，类似旧函数的功能。"""
        available = []
        unavailable = []
        seen = set()
        entries, toolset_checks = self._snapshot_state()
        for entry in entries:
            ts = entry.toolset
            if ts in seen:
                continue
            seen.add(ts)
            if self._evaluate_toolset_check(ts, toolset_checks.get(ts)):
                available.append(ts)
            else:
                unavailable.append({
                    "name": ts,
                    "env_vars": entry.requires_env,
                    "tools": [e.name for e in entries if e.toolset == ts],
                })
        return available, unavailable


# 模块级别单例
registry = ToolRegistry()


# ---------------------------------------------------------------------------
# 工具响应序列化辅助函数
# ---------------------------------------------------------------------------
# 每个工具处理函数必须返回一个 JSON 字符串。这些辅助函数消除了
# 在工具文件中出现数百次的 ``json.dumps({"error": msg}, ensure_ascii=False)``
# 样板代码。
#
# 用法：
#   from tools.registry import registry, tool_error, tool_result
#
#   return tool_error("something went wrong")
#   return tool_error("not found", code=404)
#   return tool_result(success=True, data=payload)
#   return tool_result(items)            # 直接传递字典


def tool_error(message, **extra) -> str:
    """返回工具处理函数的 JSON 错误字符串。

    >>> tool_error("file not found")
    '{"error": "file not found"}'
    >>> tool_error("bad input", success=False)
    '{"error": "bad input", "success": false}'
    """
    result = {"error": str(message)}
    if extra:
        result.update(extra)
    return json.dumps(result, ensure_ascii=False)


def tool_result(data=None, **kwargs) -> str:
    """返回工具处理函数的 JSON 结果字符串。

    接受字典位置参数 *或* 关键字参数（不能同时使用）：

    >>> tool_result(success=True, count=42)
    '{"success": true, "count": 42}'
    >>> tool_result({"key": "value"})
    '{"key": "value"}'
    """
    if data is not None:
        return json.dumps(data, ensure_ascii=False)
    return json.dumps(kwargs, ensure_ascii=False)
