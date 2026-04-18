#!/usr/bin/env python3
"""
模型工具模块 (Model Tools Module)

工具注册表之上的轻量编排层。tools/ 目录下的每个工具文件通过
tools.registry.register() 自行注册其 schema、handler 和元数据。
本模块触发工具发现（通过导入所有工具模块），然后提供
run_agent.py、cli.py、batch_runner.py 和 RL 环境所使用的公开 API。

公开 API（签名与原始 2,400 行版本保持一致）：
    get_tool_definitions(enabled_toolsets, disabled_toolsets, quiet_mode) -> list
    handle_function_call(function_name, function_args, task_id, user_task) -> str
    TOOL_TO_TOOLSET_MAP: dict          (供 batch_runner.py 使用)
    TOOLSET_REQUIREMENTS: dict         (供 cli.py, doctor.py 使用)
    get_all_tool_names() -> list
    get_toolset_for_tool(name) -> str
    get_available_toolsets() -> dict
    check_toolset_requirements() -> dict
    check_tool_availability(quiet) -> tuple
"""

import json
import asyncio
import logging
import threading
from typing import Dict, Any, List, Optional, Tuple

from tools.registry import discover_builtin_tools, registry
from toolsets import resolve_toolset, validate_toolset

logger = logging.getLogger(__name__)


# =============================================================================
# 异步桥接 (Async Bridging) — 唯一的事实来源，registry.dispatch 也会使用
# =============================================================================

_tool_loop = None          # 主线程（CLI）的持久化事件循环
_tool_loop_lock = threading.Lock()
_worker_thread_local = threading.local()  # 每个工作线程独立的持久化事件循环


def _get_tool_loop():
    """返回一个长期存活的事件循环，用于运行异步工具处理函数。

    使用持久化事件循环（而非每次创建并*关闭*新循环的 asyncio.run()），
    可以防止缓存的 httpx/AsyncOpenAI 客户端在垃圾回收时
    尝试在已关闭的循环上关闭传输层而导致的 "Event loop is closed" 错误。
    """
    global _tool_loop
    with _tool_loop_lock:
        if _tool_loop is None or _tool_loop.is_closed():
            _tool_loop = asyncio.new_event_loop()
        return _tool_loop


def _get_worker_loop():
    """返回当前工作线程的持久化事件循环。

    每个工作线程（例如 delegate_task 的 ThreadPoolExecutor 线程）
    都有自己的长期存活事件循环，存储在线程本地存储中。
    这可以防止使用 asyncio.run() 时出现的 "Event loop is closed" 错误：
    asyncio.run() 会创建一个循环，运行协程，然后*关闭*循环 ——
    但缓存的 httpx/AsyncOpenAI 客户端仍然绑定在已关闭的循环上，
    在垃圾回收或后续使用时会抛出 RuntimeError。

    通过在线程生命周期内保持循环存活，缓存的客户端保持有效，
    其清理操作会在活跃的循环上运行。
    """
    loop = getattr(_worker_thread_local, 'loop', None)
    if loop is None or loop.is_closed():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        _worker_thread_local.loop = loop
    return loop


def _run_async(coro):
    """从同步上下文中运行异步协程。

    如果当前线程已有运行中的事件循环（例如在网关的异步栈或 Atropos 的事件循环中），
    我们启动一个临时线程，让 asyncio.run() 创建自己的循环而不会冲突。

    对于常见的 CLI 路径（没有运行中的循环），我们使用持久化事件循环，
    这样缓存的异步客户端（httpx / AsyncOpenAI）始终绑定到活跃的循环上，
    不会在垃圾回收时触发 "Event loop is closed"。

    当从工作线程调用时（并行工具执行），我们使用每线程独立的持久化循环，
    既避免了与主线程共享循环的竞争，又避免了 asyncio.run() 的
    创建-销毁生命周期导致的 "Event loop is closed" 错误。

    这是工具处理函数中同步->异步桥接的唯一事实来源。
    RL 路径（agent_loop.py, tool_context.py）也提供外层线程池包装作为纵深防御，
    但每个处理函数通过此函数实现自我保护。
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        # 在异步上下文中（网关、RL 环境）—— 在新线程中运行
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, coro)
            return future.result(timeout=300)

    # 如果在工作线程上（例如 delegate_task 中的并行工具执行），
    # 使用每线程的持久化循环。这既避免了与主线程共享循环的竞争，
    # 又让缓存的 httpx/AsyncOpenAI 客户端在线程生命周期内绑定到活跃的循环上——
    # 防止垃圾回收清理时出现 "Event loop is closed"。
    if threading.current_thread() is not threading.main_thread():
        worker_loop = _get_worker_loop()
        return worker_loop.run_until_complete(coro)

    tool_loop = _get_tool_loop()
    return tool_loop.run_until_complete(coro)


# =============================================================================
# 工具发现 (Tool Discovery) — 导入每个模块会触发其 registry.register 调用
# =============================================================================

discover_builtin_tools()

# MCP 工具发现（来自配置的外部 MCP 服务器）
try:
    from tools.mcp_tool import discover_mcp_tools
    discover_mcp_tools()
except Exception as e:
    logger.debug("MCP tool discovery failed: %s", e)

# 插件工具发现（用户/项目/pip 插件）
try:
    from hermes_cli.plugins import discover_plugins
    discover_plugins()
except Exception as e:
    logger.debug("Plugin discovery failed: %s", e)


# =============================================================================
# 向后兼容常量 (Backward-compat constants) — 发现完成后构建一次
# =============================================================================

TOOL_TO_TOOLSET_MAP: Dict[str, str] = registry.get_tool_to_toolset_map()

TOOLSET_REQUIREMENTS: Dict[str, dict] = registry.get_toolset_requirements()

# 上一次 get_tool_definitions() 调用解析出的工具名称列表。
# 供 code_execution_tool 了解当前会话中可用的工具。
_last_resolved_tool_names: List[str] = []


# =============================================================================
# 旧版工具集名称映射 (Legacy toolset name mapping)
# 旧的 _tools 后缀名称 -> 工具名称列表
# =============================================================================

_LEGACY_TOOLSET_MAP = {
    "web_tools": ["web_search", "web_extract"],
    "terminal_tools": ["terminal"],
    "vision_tools": ["vision_analyze"],
    "moa_tools": ["mixture_of_agents"],
    "image_tools": ["image_generate"],
    "skills_tools": ["skills_list", "skill_view", "skill_manage"],
    "browser_tools": [
        "browser_navigate", "browser_snapshot", "browser_click",
        "browser_type", "browser_scroll", "browser_back",
        "browser_press", "browser_get_images",
        "browser_vision", "browser_console"
    ],
    "cronjob_tools": ["cronjob"],
    "rl_tools": [
        "rl_list_environments", "rl_select_environment",
        "rl_get_current_config", "rl_edit_config",
        "rl_start_training", "rl_check_status",
        "rl_stop_training", "rl_get_results",
        "rl_list_runs", "rl_test_inference"
    ],
    "file_tools": ["read_file", "write_file", "patch", "search_files"],
    "tts_tools": ["text_to_speech"],
}


# =============================================================================
# get_tool_definitions (主要的 schema 提供函数)
# =============================================================================

def get_tool_definitions(
    enabled_toolsets: List[str] = None,
    disabled_toolsets: List[str] = None,
    quiet_mode: bool = False,
) -> List[Dict[str, Any]]:
    """
    获取用于模型 API 调用的工具定义，支持基于工具集的过滤。

    所有工具必须属于某个工具集才能被访问。

    参数:
        enabled_toolsets: 只包含这些工具集中的工具。
        disabled_toolsets: 排除这些工具集中的工具（当 enabled_toolsets 为 None 时生效）。
        quiet_mode: 静默模式，不输出状态信息。

    返回:
        过滤后的 OpenAI 格式工具定义列表。
    """
    # 确定调用者希望包含哪些工具名称
    tools_to_include: set = set()

    if enabled_toolsets is not None:
        # 遍历启用的工具集，解析出对应的工具名称
        for toolset_name in enabled_toolsets:
            if validate_toolset(toolset_name):
                resolved = resolve_toolset(toolset_name)
                tools_to_include.update(resolved)
                if not quiet_mode:
                    print(f"✅ Enabled toolset '{toolset_name}': {', '.join(resolved) if resolved else 'no tools'}")
            elif toolset_name in _LEGACY_TOOLSET_MAP:
                # 兼容旧版工具集名称
                legacy_tools = _LEGACY_TOOLSET_MAP[toolset_name]
                tools_to_include.update(legacy_tools)
                if not quiet_mode:
                    print(f"✅ Enabled legacy toolset '{toolset_name}': {', '.join(legacy_tools)}")
            else:
                if not quiet_mode:
                    print(f"⚠️  Unknown toolset: {toolset_name}")

    elif disabled_toolsets:
        # 先加载所有工具集，然后排除被禁用的
        from toolsets import get_all_toolsets
        for ts_name in get_all_toolsets():
            tools_to_include.update(resolve_toolset(ts_name))

        for toolset_name in disabled_toolsets:
            if validate_toolset(toolset_name):
                resolved = resolve_toolset(toolset_name)
                tools_to_include.difference_update(resolved)
                if not quiet_mode:
                    print(f"🚫 Disabled toolset '{toolset_name}': {', '.join(resolved) if resolved else 'no tools'}")
            elif toolset_name in _LEGACY_TOOLSET_MAP:
                legacy_tools = _LEGACY_TOOLSET_MAP[toolset_name]
                tools_to_include.difference_update(legacy_tools)
                if not quiet_mode:
                    print(f"🚫 Disabled legacy toolset '{toolset_name}': {', '.join(legacy_tools)}")
            else:
                if not quiet_mode:
                    print(f"⚠️  Unknown toolset: {toolset_name}")
    else:
        # 未指定启用/禁用，默认加载所有工具集
        from toolsets import get_all_toolsets
        for ts_name in get_all_toolsets():
            tools_to_include.update(resolve_toolset(ts_name))

    # 插件注册的工具现在通过正常的工具集路径解析 ——
    # validate_toolset() / resolve_toolset() / get_all_toolsets()
    # 都会检查工具注册表中的插件提供的工具集。
    # 无需绕过；插件和其他工具集一样遵循 enabled_toolsets / disabled_toolsets。

    # 从注册表获取 schema（只返回 check_fn 检查通过的工具）
    filtered_tools = registry.get_definitions(tools_to_include, quiet=quiet_mode)

    # 实际通过 check_fn 过滤的工具名称集合。
    # 使用此集合（而非 tools_to_include）来处理下游引用其他工具名称的 schema ——
    # 否则模型会看到描述中提及但实际不存在的工具，并产生幻觉调用。
    available_tool_names = {t["function"]["name"] for t in filtered_tools}

    # 重新构建 execute_code 的 schema，只列出实际可用的沙箱工具。
    # 没有这一步，模型会看到 "web_search is available in execute_code"，
    # 即使 API key 未配置或工具集被禁用 (#560-discord)。
    if "execute_code" in available_tool_names:
        from tools.code_execution_tool import SANDBOX_ALLOWED_TOOLS, build_execute_code_schema
        sandbox_enabled = SANDBOX_ALLOWED_TOOLS & available_tool_names
        dynamic_schema = build_execute_code_schema(sandbox_enabled)
        for i, td in enumerate(filtered_tools):
            if td.get("function", {}).get("name") == "execute_code":
                filtered_tools[i] = {"type": "function", "function": dynamic_schema}
                break

    # 当 web_search / web_extract 不可用时，从 browser_navigate 描述中
    # 移除对 web 工具的交叉引用。静态 schema 中写着
    # "prefer web_search or web_extract"，当这些工具缺失时会导致模型幻觉调用它们。
    if "browser_navigate" in available_tool_names:
        web_tools_available = {"web_search", "web_extract"} & available_tool_names
        if not web_tools_available:
            for i, td in enumerate(filtered_tools):
                if td.get("function", {}).get("name") == "browser_navigate":
                    desc = td["function"].get("description", "")
                    desc = desc.replace(
                        " For simple information retrieval, prefer web_search or web_extract (faster, cheaper).",
                        "",
                    )
                    filtered_tools[i] = {
                        "type": "function",
                        "function": {**td["function"], "description": desc},
                    }
                    break

    if not quiet_mode:
        if filtered_tools:
            tool_names = [t["function"]["name"] for t in filtered_tools]
            print(f"🛠️  Final tool selection ({len(filtered_tools)} tools): {', '.join(tool_names)}")
        else:
            print("🛠️  No tools selected (all filtered out or unavailable)")

    # 更新全局变量，记录本次解析出的工具名称
    global _last_resolved_tool_names
    _last_resolved_tool_names = [t["function"]["name"] for t in filtered_tools]

    return filtered_tools


# =============================================================================
# handle_function_call (主要的调度函数)
# =============================================================================

# 由代理循环（run_agent.py）拦截执行的工具，
# 因为它们需要代理级别的状态（TodoStore、MemoryStore 等）。
# 注册表仍然持有它们的 schema；dispatch 只返回一个占位错误，
# 这样如果有调用泄漏，LLM 会看到合理的消息。
_AGENT_LOOP_TOOLS = {"todo", "memory", "session_search", "delegate_task"}
_READ_SEARCH_TOOLS = {"read_file", "search_files"}


# =========================================================================
# 工具参数类型强制转换
# =========================================================================

def coerce_tool_args(tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """将工具调用参数强制转换为匹配其 JSON Schema 声明的类型。

    LLM 经常将数字作为字符串返回（``"42"`` 而非 ``42``），
    将布尔值作为字符串返回（``"true"`` 而非 ``true``）。
    此函数将每个参数值与工具注册的 JSON Schema 进行比较，
    当值是字符串但 schema 期望不同类型时，尝试安全的类型转换。
    转换失败时保留原始值。

    支持 ``"type": "integer"``、``"type": "number"``、``"type": "boolean"``，
    以及联合类型（``"type": ["integer", "string"]``）。
    """
    if not args or not isinstance(args, dict):
        return args

    schema = registry.get_schema(tool_name)
    if not schema:
        return args

    properties = (schema.get("parameters") or {}).get("properties")
    if not properties:
        return args

    for key, value in args.items():
        if not isinstance(value, str):
            continue
        prop_schema = properties.get(key)
        if not prop_schema:
            continue
        expected = prop_schema.get("type")
        if not expected:
            continue
        coerced = _coerce_value(value, expected)
        if coerced is not value:
            args[key] = coerced

    return args


def _coerce_value(value: str, expected_type):
    """尝试将字符串 *value* 转换为 *expected_type*。

    当转换不适用或失败时返回原始字符串。
    """
    if isinstance(expected_type, list):
        # 联合类型 —— 按顺序尝试每种类型，返回第一个成功的转换结果
        for t in expected_type:
            result = _coerce_value(value, t)
            if result is not value:
                return result
        return value

    if expected_type in ("integer", "number"):
        return _coerce_number(value, integer_only=(expected_type == "integer"))
    if expected_type == "boolean":
        return _coerce_boolean(value)
    return value


def _coerce_number(value: str, integer_only: bool = False):
    """尝试将 *value* 解析为数字。解析失败时返回原始字符串。"""
    try:
        f = float(value)
    except (ValueError, OverflowError):
        return value
    # 在 int() 转换之前防止 inf/nan
    if f != f or f == float("inf") or f == float("-inf"):
        return f
    # 如果看起来像整数（没有小数部分），返回 int
    if f == int(f):
        return int(f)
    if integer_only:
        # Schema 期望整数但值有小数 —— 保持为字符串
        return value
    return f


def _coerce_boolean(value: str):
    """尝试将 *value* 解析为布尔值。解析失败时返回原始字符串。"""
    low = value.strip().lower()
    if low == "true":
        return True
    if low == "false":
        return False
    return value


def handle_function_call(
    function_name: str,
    function_args: Dict[str, Any],
    task_id: Optional[str] = None,
    tool_call_id: Optional[str] = None,
    session_id: Optional[str] = None,
    user_task: Optional[str] = None,
    enabled_tools: Optional[List[str]] = None,
    skip_pre_tool_call_hook: bool = False,
) -> str:
    """
    主函数调用调度器，将调用路由到工具注册表。

    参数:
        function_name: 要调用的函数名称。
        function_args: 函数参数。
        task_id: 用于终端/浏览器会话隔离的唯一标识符。
        user_task: 用户的原始任务（为 browser_snapshot 提供上下文）。
        enabled_tools: 本会话启用的工具名称列表。当提供时，
                       execute_code 使用此列表确定要生成哪些沙箱工具。
                       向后兼容时回退到进程全局的 ``_last_resolved_tool_names``。

    返回:
        函数结果的 JSON 字符串。
    """
    # 将字符串参数强制转换为 schema 声明的类型（例如 "42"→42）
    function_args = coerce_tool_args(function_name, function_args)

    try:
        # 代理循环工具由上层拦截处理
        if function_name in _AGENT_LOOP_TOOLS:
            return json.dumps({"error": f"{function_name} must be handled by the agent loop"})

        # 检查插件钩子是否发出拦截指令（除非调用者已经检查过 ——
        # 例如 run_agent._invoke_tool 传入 skip=True 以避免重复触发钩子）。
        if not skip_pre_tool_call_hook:
            block_message: Optional[str] = None
            try:
                from hermes_cli.plugins import get_pre_tool_call_block_message
                block_message = get_pre_tool_call_block_message(
                    function_name,
                    function_args,
                    task_id=task_id or "",
                    session_id=session_id or "",
                    tool_call_id=tool_call_id or "",
                )
            except Exception:
                pass

            if block_message is not None:
                return json.dumps({"error": block_message}, ensure_ascii=False)
        else:
            # 仍然为观察者触发钩子 —— 只是不检查拦截
            # （调用者已经做过检查了）。
            try:
                from hermes_cli.plugins import invoke_hook
                invoke_hook(
                    "pre_tool_call",
                    tool_name=function_name,
                    args=function_args,
                    task_id=task_id or "",
                    session_id=session_id or "",
                    tool_call_id=tool_call_id or "",
                )
            except Exception:
                pass

        # 当非读取/搜索工具运行时，通知读取循环追踪器，
        # 以便*连续*计数器重置（在其他工作之后的读取是正常的）。
        if function_name not in _READ_SEARCH_TOOLS:
            try:
                from tools.file_tools import notify_other_tool_call
                notify_other_tool_call(task_id or "default")
            except Exception:
                pass  # file_tools 可能尚未加载

        if function_name == "execute_code":
            # 优先使用调用者提供的列表，防止子代理通过进程全局变量覆盖父级的工具集
            sandbox_enabled = enabled_tools if enabled_tools is not None else _last_resolved_tool_names
            result = registry.dispatch(
                function_name, function_args,
                task_id=task_id,
                enabled_tools=sandbox_enabled,
            )
        else:
            result = registry.dispatch(
                function_name, function_args,
                task_id=task_id,
                user_task=user_task,
            )

        # 工具执行完成后触发 post_tool_call 钩子
        try:
            from hermes_cli.plugins import invoke_hook
            invoke_hook(
                "post_tool_call",
                tool_name=function_name,
                args=function_args,
                result=result,
                task_id=task_id or "",
                session_id=session_id or "",
                tool_call_id=tool_call_id or "",
            )
        except Exception:
            pass

        return result

    except Exception as e:
        error_msg = f"Error executing {function_name}: {str(e)}"
        logger.error(error_msg)
        return json.dumps({"error": error_msg}, ensure_ascii=False)


# =============================================================================
# 向后兼容的包装函数 (Backward-compat wrapper functions)
# =============================================================================

def get_all_tool_names() -> List[str]:
    """返回所有已注册的工具名称。"""
    return registry.get_all_tool_names()


def get_toolset_for_tool(tool_name: str) -> Optional[str]:
    """返回工具所属的工具集名称。"""
    return registry.get_toolset_for_tool(tool_name)


def get_available_toolsets() -> Dict[str, dict]:
    """返回工具集的可用性信息，用于 UI 展示。"""
    return registry.get_available_toolsets()


def check_toolset_requirements() -> Dict[str, bool]:
    """返回 {工具集名称: 是否可用} 的字典，涵盖所有已注册的工具集。"""
    return registry.check_toolset_requirements()


def check_tool_availability(quiet: bool = False) -> Tuple[List[str], List[dict]]:
    """返回 (可用的工具集列表, 不可用的详情列表)。"""
    return registry.check_tool_availability(quiet=quiet)
