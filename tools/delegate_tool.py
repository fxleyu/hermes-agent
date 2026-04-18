#!/usr/bin/env python3
"""
委派工具 -- 子代理架构

生成具有隔离上下文、受限工具集和独立终端会话的子 AIAgent 实例。
支持单任务和批量（并行）模式。父代理阻塞直到所有子代理完成。

每个子代理获得：
  - 全新的对话（无父代理历史）
  - 独立的 task_id（独立的终端会话、文件操作缓存）
  - 受限的工具集（可配置，始终移除被阻止的工具）
  - 基于委派目标 + 上下文构建的聚焦系统提示

父代理的上下文只看到委派调用和摘要结果，
永远看不到子代理的中间工具调用或推理过程。
"""

import json
import logging
logger = logging.getLogger(__name__)
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional

from toolsets import TOOLSETS


# 子代理绝不允许访问的工具
DELEGATE_BLOCKED_TOOLS = frozenset([
    "delegate_task",   # 禁止递归委派
    "clarify",         # 禁止用户交互
    "memory",          # 禁止写入共享 MEMORY.md
    "send_message",    # 禁止跨平台副作用
    "execute_code",    # 子代理应逐步推理，而非编写脚本
])

# 构建描述片段，列出子代理可用的工具集。
# 排除所有工具都被阻止的工具集、复合/平台工具集
# （hermes-* 前缀）和场景工具集。
_EXCLUDED_TOOLSET_NAMES = frozenset({"debugging", "safe", "delegation", "moa", "rl"})
_SUBAGENT_TOOLSETS = sorted(
    name for name, defn in TOOLSETS.items()
    if name not in _EXCLUDED_TOOLSET_NAMES
    and not name.startswith("hermes-")
    and not all(t in DELEGATE_BLOCKED_TOOLS for t in defn.get("tools", []))
)
_TOOLSET_LIST_STR = ", ".join(f"'{n}'" for n in _SUBAGENT_TOOLSETS)

_DEFAULT_MAX_CONCURRENT_CHILDREN = 3
MAX_DEPTH = 2  # 父代理 (0) -> 子代理 (1) -> 孙代理被拒绝 (2)


def _get_max_concurrent_children() -> int:
    """从配置中读取 delegation.max_concurrent_children，
    回退到 DELEGATION_MAX_CONCURRENT_CHILDREN 环境变量，
    然后回退到默认值 (3)。

    使用与 ``delegate_task`` 其余部分相同的 ``_load_config()`` 路径，
    保持配置优先级一致（config.yaml > env > 默认值）。
    """
    cfg = _load_config()
    val = cfg.get("max_concurrent_children")
    if val is not None:
        try:
            return max(1, int(val))
        except (TypeError, ValueError):
            logger.warning(
                "delegation.max_concurrent_children=%r is not a valid integer; "
                "using default %d", val, _DEFAULT_MAX_CONCURRENT_CHILDREN,
            )
    env_val = os.getenv("DELEGATION_MAX_CONCURRENT_CHILDREN")
    if env_val:
        try:
            return max(1, int(env_val))
        except (TypeError, ValueError):
            pass
    return _DEFAULT_MAX_CONCURRENT_CHILDREN
DEFAULT_MAX_ITERATIONS = 50
_HEARTBEAT_INTERVAL = 30  # 委派期间父代理活动心跳的间隔秒数
DEFAULT_TOOLSETS = ["terminal", "file", "web"]


def check_delegate_requirements() -> bool:
    """委派没有外部依赖——始终可用。"""
    return True


def _build_child_system_prompt(
    goal: str,
    context: Optional[str] = None,
    *,
    workspace_path: Optional[str] = None,
) -> str:
    """为子代理构建聚焦的系统提示。"""
    parts = [
        "You are a focused subagent working on a specific delegated task.",
        "",
        f"YOUR TASK:\n{goal}",
    ]
    if context and context.strip():
        parts.append(f"\nCONTEXT:\n{context}")
    if workspace_path and str(workspace_path).strip():
        parts.append(
            "\nWORKSPACE PATH:\n"
            f"{workspace_path}\n"
            "Use this exact path for local repository/workdir operations unless the task explicitly says otherwise."
        )
    parts.append(
        "\nComplete this task using the tools available to you. "
        "When finished, provide a clear, concise summary of:\n"
        "- What you did\n"
        "- What you found or accomplished\n"
        "- Any files you created or modified\n"
        "- Any issues encountered\n\n"
        "Important workspace rule: Never assume a repository lives at /workspace/... or any other container-style path unless the task/context explicitly gives that path. "
        "If no exact local path is provided, discover it first before issuing git/workdir-specific commands.\n\n"
        "Be thorough but concise -- your response is returned to the "
        "parent agent as a summary."
    )
    return "\n".join(parts)


def _resolve_workspace_hint(parent_agent) -> Optional[str]:
    """为子代理提示提供最佳尝试的本地工作区路径提示。

    仅在有具体的绝对路径目录时才注入路径。这避免了
    在本地仓库任务中教子代理一个假的容器路径，同时
    帮助它们避免猜测 `/workspace/...`。
    """
    candidates = [
        os.getenv("TERMINAL_CWD"),
        getattr(getattr(parent_agent, "_subdirectory_hints", None), "working_dir", None),
        getattr(parent_agent, "terminal_cwd", None),
        getattr(parent_agent, "cwd", None),
    ]
    for candidate in candidates:
        if not candidate:
            continue
        try:
            text = os.path.abspath(os.path.expanduser(str(candidate)))
        except Exception:
            continue
        if os.path.isabs(text) and os.path.isdir(text):
            return text
    return None


def _strip_blocked_tools(toolsets: List[str]) -> List[str]:
    """移除仅包含被阻止工具的工具集。"""
    blocked_toolset_names = {
        "delegation", "clarify", "memory", "code_execution",
    }
    return [t for t in toolsets if t not in blocked_toolset_names]


def _build_child_progress_callback(task_index: int, parent_agent, task_count: int = 1) -> Optional[callable]:
    """构建一个将子代理工具调用中继到父代理显示的回调。

    两种显示路径：
      CLI:     在父代理的委派旋转动画上方打印树形视图行
      Gateway: 批量工具名称并中继到父代理的进度回调

    如果没有可用的显示机制则返回 None，此时子代理
    运行时不带进度回调（与当前行为相同）。
    """
    spinner = getattr(parent_agent, '_delegate_spinner', None)
    parent_cb = getattr(parent_agent, 'tool_progress_callback', None)

    if not spinner and not parent_cb:
        return None  # 无显示 → 无回调 → 零行为变化

    # 仅在批量模式（多任务）下显示 1 开始的索引前缀
    prefix = f"[{task_index + 1}] " if task_count > 1 else ""

    # Gateway：批量工具名称，定期刷新
    _BATCH_SIZE = 5
    _batch: List[str] = []

    def _callback(event_type: str, tool_name: str = None, preview: str = None, args=None, **kwargs):
        # event_type 是以下之一: "tool.started", "tool.completed",
        # "reasoning.available", "_thinking", "subagent_progress"

        # "_thinking" / 推理事件
        if event_type in ("_thinking", "reasoning.available"):
            text = preview or tool_name or ""
            if spinner:
                short = (text[:55] + "...") if len(text) > 55 else text
                try:
                    spinner.print_above(f" {prefix}├─ 💭 \"{short}\"")
                except Exception as e:
                    logger.debug("Spinner print_above failed: %s", e)
            # 不将思考内容中继到 gateway（对聊天来说太嘈杂）
            return

        # tool.completed — 此处无需显示（旋转动画在 started 时显示）
        if event_type == "tool.completed":
            return

        # tool.started — 显示并批量中继到父代理
        if spinner:
            short = (preview[:35] + "...") if preview and len(preview) > 35 else (preview or "")
            from agent.display import get_tool_emoji
            emoji = get_tool_emoji(tool_name or "")
            line = f" {prefix}├─ {emoji} {tool_name}"
            if short:
                line += f"  \"{short}\""
            try:
                spinner.print_above(line)
            except Exception as e:
                logger.debug("Spinner print_above failed: %s", e)

        if parent_cb:
            _batch.append(tool_name or "")
            if len(_batch) >= _BATCH_SIZE:
                summary = ", ".join(_batch)
                try:
                    parent_cb("subagent_progress", f"🔀 {prefix}{summary}")
                except Exception as e:
                    logger.debug("Parent callback failed: %s", e)
                _batch.clear()

    def _flush():
        """完成时将剩余的批量工具名称刷新到 gateway。"""
        if parent_cb and _batch:
            summary = ", ".join(_batch)
            try:
                parent_cb("subagent_progress", f"🔀 {prefix}{summary}")
            except Exception as e:
                logger.debug("Parent callback flush failed: %s", e)
            _batch.clear()

    _callback._flush = _flush
    return _callback


def _build_child_agent(
    task_index: int,
    goal: str,
    context: Optional[str],
    toolsets: Optional[List[str]],
    model: Optional[str],
    max_iterations: int,
    parent_agent,
    # 从委派配置中获取的凭证覆盖（provider:model 解析）
    override_provider: Optional[str] = None,
    override_base_url: Optional[str] = None,
    override_api_key: Optional[str] = None,
    override_api_mode: Optional[str] = None,
    # ACP 传输覆盖 — 让非 ACP 父代理能够生成 ACP 子代理
    override_acp_command: Optional[str] = None,
    override_acp_args: Optional[List[str]] = None,
):
    """
    在主线程上构建子 AIAgent（线程安全构造）。
    返回构造好的子代理而不运行它。

    当设置了 override_* 参数（来自委派配置）时，子代理使用
    那些凭证而非从父代理继承。这使得子代理可以路由到不同的
    provider:model 对（例如在父代理运行于 Nous Portal 时，
    子代理使用 OpenRouter 上的廉价/快速模型）。
    """
    from run_agent import AIAgent

    # 未明确给出工具集时，从父代理的已启用工具集继承，
    # 以确保禁用的工具（如 web）不会泄漏到子代理。
    # 注意：enabled_toolsets=None 表示"所有工具已启用"（默认值），
    # 因此必须从父代理的已加载工具中推导有效工具集。
    parent_enabled = getattr(parent_agent, "enabled_toolsets", None)
    if parent_enabled is not None:
        parent_toolsets = set(parent_enabled)
    elif parent_agent and hasattr(parent_agent, "valid_tool_names"):
        # enabled_toolsets 为 None（所有工具）— 从已加载的工具名称推导
        import model_tools
        parent_toolsets = {
            ts for name in parent_agent.valid_tool_names
            if (ts := model_tools.get_toolset_for_tool(name)) is not None
        }
    else:
        parent_toolsets = set(DEFAULT_TOOLSETS)

    if toolsets:
        # 与父代理取交集 — 子代理不能获得父代理没有的工具
        child_toolsets = _strip_blocked_tools([t for t in toolsets if t in parent_toolsets])
    elif parent_agent and parent_enabled is not None:
        child_toolsets = _strip_blocked_tools(parent_enabled)
    elif parent_toolsets:
        child_toolsets = _strip_blocked_tools(sorted(parent_toolsets))
    else:
        child_toolsets = _strip_blocked_tools(DEFAULT_TOOLSETS)

    workspace_hint = _resolve_workspace_hint(parent_agent)
    child_prompt = _build_child_system_prompt(goal, context, workspace_path=workspace_hint)
    # 提取父代理的 API 密钥，以便子代理继承认证（如 Nous Portal）。
    parent_api_key = getattr(parent_agent, "api_key", None)
    if (not parent_api_key) and hasattr(parent_agent, "_client_kwargs"):
        parent_api_key = parent_agent._client_kwargs.get("api_key")

    # 构建进度回调以将工具调用中继到父代理显示
    child_progress_cb = _build_child_progress_callback(task_index, parent_agent)

    # 每个子代理获得自己的迭代预算，上限为 max_iterations
    # （可通过 delegation.max_iterations 配置，默认 50）。这意味着
    # 父代理 + 子代理的总迭代次数可能超过父代理的 max_iterations。
    # 用户在 config.yaml 中控制每个子代理的上限。

    child_thinking_cb = None
    if child_progress_cb:
        def _child_thinking(text: str) -> None:
            if not text:
                return
            try:
                child_progress_cb("_thinking", text)
            except Exception as e:
                logger.debug("Child thinking callback relay failed: %s", e)

        child_thinking_cb = _child_thinking

    # 解析有效凭证：配置覆盖 > 父代理继承
    effective_model = model or parent_agent.model
    effective_provider = override_provider or getattr(parent_agent, "provider", None)
    effective_base_url = override_base_url or parent_agent.base_url
    effective_api_key = override_api_key or parent_api_key
    effective_api_mode = override_api_mode or getattr(parent_agent, "api_mode", None)
    effective_acp_command = override_acp_command or getattr(parent_agent, "acp_command", None)
    effective_acp_args = list(override_acp_args if override_acp_args is not None else (getattr(parent_agent, "acp_args", []) or []))

    # 解析推理配置：委派覆盖 > 父代理继承
    parent_reasoning = getattr(parent_agent, "reasoning_config", None)
    child_reasoning = parent_reasoning
    try:
        delegation_cfg = _load_config()
        delegation_effort = str(delegation_cfg.get("reasoning_effort") or "").strip()
        if delegation_effort:
            from hermes_constants import parse_reasoning_effort
            parsed = parse_reasoning_effort(delegation_effort)
            if parsed is not None:
                child_reasoning = parsed
            else:
                logger.warning(
                    "Unknown delegation.reasoning_effort '%s', inheriting parent level",
                    delegation_effort,
                )
    except Exception as exc:
        logger.debug("Could not load delegation reasoning_effort: %s", exc)

    child = AIAgent(
        base_url=effective_base_url,
        api_key=effective_api_key,
        model=effective_model,
        provider=effective_provider,
        api_mode=effective_api_mode,
        acp_command=effective_acp_command,
        acp_args=effective_acp_args,
        max_iterations=max_iterations,
        max_tokens=getattr(parent_agent, "max_tokens", None),
        reasoning_config=child_reasoning,
        prefill_messages=getattr(parent_agent, "prefill_messages", None),
        enabled_toolsets=child_toolsets,
        quiet_mode=True,
        ephemeral_system_prompt=child_prompt,
        log_prefix=f"[subagent-{task_index}]",
        platform=parent_agent.platform,
        skip_context_files=True,
        skip_memory=True,
        clarify_callback=None,
        thinking_callback=child_thinking_cb,
        session_db=getattr(parent_agent, '_session_db', None),
        parent_session_id=getattr(parent_agent, 'session_id', None),
        providers_allowed=parent_agent.providers_allowed,
        providers_ignored=parent_agent.providers_ignored,
        providers_order=parent_agent.providers_order,
        provider_sort=parent_agent.provider_sort,
        tool_progress_callback=child_progress_cb,
        iteration_budget=None,  # fresh budget per subagent
    )
    child._print_fn = getattr(parent_agent, '_print_fn', None)
    # 设置委派深度，使子代理无法再生成孙代理
    child._delegate_depth = getattr(parent_agent, '_delegate_depth', 0) + 1

    # 尽可能与子代理共享凭证池，使子代理能够
    # 在速率限制时轮换凭证，而非固定在一个密钥上。
    child_pool = _resolve_child_credential_pool(effective_provider, parent_agent)
    if child_pool is not None:
        child._credential_pool = child_pool

    # 注册子代理以便中断传播
    if hasattr(parent_agent, '_active_children'):
        lock = getattr(parent_agent, '_active_children_lock', None)
        if lock:
            with lock:
                parent_agent._active_children.append(child)
        else:
            parent_agent._active_children.append(child)

    return child

def _run_single_child(
    task_index: int,
    goal: str,
    child=None,
    parent_agent=None,
    **_kwargs,
) -> Dict[str, Any]:
    """
    运行预构建的子代理。在线程内调用。
    返回结构化的结果字典。
    """
    child_start = time.monotonic()

    # 从子代理获取进度回调
    child_progress_cb = getattr(child, 'tool_progress_callback', None)

    # 使用子代理构造前保存的值恢复父代理工具名称，
    # 这是修改全局变量之前的正确父代理工具集，而非子代理的。
    import model_tools
    _saved_tool_names = getattr(child, "_delegate_saved_tool_names",
                                list(model_tools._last_resolved_tool_names))

    child_pool = getattr(child, '_credential_pool', None)
    leased_cred_id = None
    if child_pool is not None:
        leased_cred_id = child_pool.acquire_lease()
        if leased_cred_id is not None:
            try:
                leased_entry = child_pool.current()
                if leased_entry is not None and hasattr(child, '_swap_credential'):
                    child._swap_credential(leased_entry)
            except Exception as exc:
                logger.debug("Failed to bind child to leased credential: %s", exc)

    # 心跳：定期将子代理活动传播到父代理，以防
    # 子代理工作时网关不活动超时触发。
    # 没有这个，当 delegate_task 启动时父代理的 _last_activity_ts
    # 会冻结，网关最终会因"无活动"杀死代理。
    _heartbeat_stop = threading.Event()

    def _heartbeat_loop():
        while not _heartbeat_stop.wait(_HEARTBEAT_INTERVAL):
            if parent_agent is None:
                continue
            touch = getattr(parent_agent, '_touch_activity', None)
            if not touch:
                continue
            # 从子代理自身的活动跟踪器获取详情
            desc = f"delegate_task: subagent {task_index} working"
            try:
                child_summary = child.get_activity_summary()
                child_tool = child_summary.get("current_tool")
                child_iter = child_summary.get("api_call_count", 0)
                child_max = child_summary.get("max_iterations", 0)
                if child_tool:
                    desc = (f"delegate_task: subagent running {child_tool} "
                            f"(iteration {child_iter}/{child_max})")
                else:
                    child_desc = child_summary.get("last_activity_desc", "")
                    if child_desc:
                        desc = (f"delegate_task: subagent {child_desc} "
                                f"(iteration {child_iter}/{child_max})")
            except Exception:
                pass
            try:
                touch(desc)
            except Exception:
                pass

    _heartbeat_thread = threading.Thread(target=_heartbeat_loop, daemon=True)
    _heartbeat_thread.start()

    try:
        result = child.run_conversation(user_message=goal)

        # 将剩余的批量进度刷新到 gateway
        if child_progress_cb and hasattr(child_progress_cb, '_flush'):
            try:
                child_progress_cb._flush()
            except Exception as e:
                logger.debug("Progress callback flush failed: %s", e)

        duration = round(time.monotonic() - child_start, 2)

        summary = result.get("final_response") or ""
        completed = result.get("completed", False)
        interrupted = result.get("interrupted", False)
        api_calls = result.get("api_calls", 0)

        if interrupted:
            status = "interrupted"
        elif summary:
            # 有摘要意味着子代理产生了可用的输出。
            # exit_reason ("completed" vs "max_iterations") 已经
            # 告诉父代理任务*如何*结束。
            status = "completed"
        else:
            status = "failed"

        # 从对话消息（已在内存中）构建工具调用追踪。
        # 使用 tool_call_id 正确配对并行工具调用与结果。
        tool_trace: list[Dict[str, Any]] = []
        trace_by_id: Dict[str, Dict[str, Any]] = {}
        messages = result.get("messages") or []
        if isinstance(messages, list):
            for msg in messages:
                if not isinstance(msg, dict):
                    continue
                if msg.get("role") == "assistant":
                    for tc in (msg.get("tool_calls") or []):
                        fn = tc.get("function", {})
                        entry_t = {
                            "tool": fn.get("name", "unknown"),
                            "args_bytes": len(fn.get("arguments", "")),
                        }
                        tool_trace.append(entry_t)
                        tc_id = tc.get("id")
                        if tc_id:
                            trace_by_id[tc_id] = entry_t
                elif msg.get("role") == "tool":
                    content = msg.get("content", "")
                    is_error = bool(
                        content and "error" in content[:80].lower()
                    )
                    result_meta = {
                        "result_bytes": len(content),
                        "status": "error" if is_error else "ok",
                    }
                    # 通过 tool_call_id 匹配并行调用
                    tc_id = msg.get("tool_call_id")
                    target = trace_by_id.get(tc_id) if tc_id else None
                    if target is not None:
                        target.update(result_meta)
                    elif tool_trace:
                        # 对没有 tool_call_id 的消息的回退
                        tool_trace[-1].update(result_meta)

        # 确定退出原因
        if interrupted:
            exit_reason = "interrupted"
        elif completed:
            exit_reason = "completed"
        else:
            exit_reason = "max_iterations"

        # 提取 token 计数（对 mock 对象安全）
        _input_tokens = getattr(child, "session_prompt_tokens", 0)
        _output_tokens = getattr(child, "session_completion_tokens", 0)
        _model = getattr(child, "model", None)

        entry: Dict[str, Any] = {
            "task_index": task_index,
            "status": status,
            "summary": summary,
            "api_calls": api_calls,
            "duration_seconds": duration,
            "model": _model if isinstance(_model, str) else None,
            "exit_reason": exit_reason,
            "tokens": {
                "input": _input_tokens if isinstance(_input_tokens, (int, float)) else 0,
                "output": _output_tokens if isinstance(_output_tokens, (int, float)) else 0,
            },
            "tool_trace": tool_trace,
        }
        if status == "failed":
            entry["error"] = result.get("error", "Subagent did not produce a response.")

        return entry

    except Exception as exc:
        duration = round(time.monotonic() - child_start, 2)
        logging.exception(f"[subagent-{task_index}] failed")
        return {
            "task_index": task_index,
            "status": "error",
            "summary": None,
            "error": str(exc),
            "api_calls": 0,
            "duration_seconds": duration,
        }

    finally:
        # 停止心跳线程，防止子代理完成（或失败）后
        # 继续触发父代理活动。
        _heartbeat_stop.set()
        _heartbeat_thread.join(timeout=5)

        if child_pool is not None and leased_cred_id is not None:
            try:
                child_pool.release_lease(leased_cred_id)
            except Exception as exc:
                logger.debug("Failed to release credential lease: %s", exc)

        # 恢复父代理的工具名称，使进程全局变量对
        # 后续的 execute_code 调用或其他消费者保持正确。
        import model_tools

        saved_tool_names = getattr(child, "_delegate_saved_tool_names", None)
        if isinstance(saved_tool_names, list):
            model_tools._last_resolved_tool_names = list(saved_tool_names)

        # 从活动跟踪中移除子代理

        # 取消子代理的中断传播注册
        if hasattr(parent_agent, '_active_children'):
            try:
                lock = getattr(parent_agent, '_active_children_lock', None)
                if lock:
                    with lock:
                        parent_agent._active_children.remove(child)
                else:
                    parent_agent._active_children.remove(child)
            except (ValueError, UnboundLocalError) as e:
                logger.debug("Could not remove child from active_children: %s", e)

        # 关闭工具资源（终端沙箱、浏览器守护进程、
        # 后台进程、httpx 客户端），以确保子代理的子进程
        # 不会比委派更长寿。
        try:
            if hasattr(child, 'close'):
                child.close()
        except Exception:
            logger.debug("Failed to close child agent after delegation")

def delegate_task(
    goal: Optional[str] = None,
    context: Optional[str] = None,
    toolsets: Optional[List[str]] = None,
    tasks: Optional[List[Dict[str, Any]]] = None,
    max_iterations: Optional[int] = None,
    acp_command: Optional[str] = None,
    acp_args: Optional[List[str]] = None,
    parent_agent=None,
) -> str:
    """
    生成一个或多个子代理来处理委派的任务。

    支持两种模式：
      - 单任务：提供 goal（+ 可选的 context, toolsets）
      - 批量：提供 tasks 数组 [{goal, context, toolsets}, ...]

    返回包含结果数组的 JSON，每个任务一个条目。
    """
    if parent_agent is None:
        return tool_error("delegate_task requires a parent agent context.")

    # 深度限制
    depth = getattr(parent_agent, '_delegate_depth', 0)
    if depth >= MAX_DEPTH:
        return json.dumps({
            "error": (
                f"Delegation depth limit reached ({MAX_DEPTH}). "
                "Subagents cannot spawn further subagents."
            )
        })

    # 加载配置
    cfg = _load_config()
    default_max_iter = cfg.get("max_iterations", DEFAULT_MAX_ITERATIONS)
    effective_max_iter = max_iterations or default_max_iter

    # 解析委派凭证（provider:model 对）。
    # 当配置了 delegation.provider 时，通过与 CLI/gateway 启动
    # 相同的运行时提供者系统解析完整凭证包（base_url, api_key, api_mode）。
    # 未配置时返回 None 值，子代理从父代理继承。
    try:
        creds = _resolve_delegation_credentials(cfg, parent_agent)
    except ValueError as exc:
        return tool_error(str(exc))

    # 规范化为任务列表
    max_children = _get_max_concurrent_children()
    if tasks and isinstance(tasks, list):
        if len(tasks) > max_children:
            return tool_error(
                f"Too many tasks: {len(tasks)} provided, but "
                f"max_concurrent_children is {max_children}. "
                f"Either reduce the task count, split into multiple "
                f"delegate_task calls, or increase "
                f"delegation.max_concurrent_children in config.yaml."
            )
        task_list = tasks
    elif goal and isinstance(goal, str) and goal.strip():
        task_list = [{"goal": goal, "context": context, "toolsets": toolsets}]
    else:
        return tool_error("Provide either 'goal' (single task) or 'tasks' (batch).")

    if not task_list:
        return tool_error("No tasks provided.")

    # 验证每个任务都有目标
    for i, task in enumerate(task_list):
        if not task.get("goal", "").strip():
            return tool_error(f"Task {i} is missing a 'goal'.")

    overall_start = time.monotonic()
    results = []

    n_tasks = len(task_list)
    # 跟踪任务标签用于进度显示（为可读性截断）
    task_labels = [t["goal"][:40] for t in task_list]

    # 在任何子代理构造修改全局变量之前保存父代理工具名称。
    # _build_child_agent() 调用 AIAgent()，后者调用 get_tool_definitions()，
    # 这会用子代理的工具集覆盖 model_tools._last_resolved_tool_names。
    import model_tools as _model_tools
    _parent_tool_names = list(_model_tools._last_resolved_tool_names)

    # 在主线程上构建所有子代理（线程安全构造）
    # 包装在 try/finally 中，以便即使子代理构建引发异常，
    # 全局变量也始终被恢复（否则 _last_resolved_tool_names 保持损坏状态）。
    children = []
    try:
        for i, t in enumerate(task_list):
            child = _build_child_agent(
                task_index=i, goal=t["goal"], context=t.get("context"),
                toolsets=t.get("toolsets") or toolsets, model=creds["model"],
                max_iterations=effective_max_iter, parent_agent=parent_agent,
                override_provider=creds["provider"], override_base_url=creds["base_url"],
                override_api_key=creds["api_key"],
                override_api_mode=creds["api_mode"],
                override_acp_command=t.get("acp_command") or acp_command,
                override_acp_args=t.get("acp_args") or acp_args,
            )
            # 用正确的父代理工具名称覆盖（子代理构造修改全局变量之前的值）
            child._delegate_saved_tool_names = _parent_tool_names
            children.append((i, t, child))
    finally:
        # 权威恢复：所有子代理构建后重置全局为父代理的工具名称
        _model_tools._last_resolved_tool_names = _parent_tool_names

    if n_tasks == 1:
        # 单任务 -- 直接运行（无线程池开销）
        _i, _t, child = children[0]
        result = _run_single_child(0, _t["goal"], child, parent_agent)
        results.append(result)
    else:
        # 批量 -- 并行运行并显示每个任务的进度行
        completed_count = 0
        spinner_ref = getattr(parent_agent, '_delegate_spinner', None)

        with ThreadPoolExecutor(max_workers=max_children) as executor:
            futures = {}
            for i, t, child in children:
                future = executor.submit(
                    _run_single_child,
                    task_index=i,
                    goal=t["goal"],
                    child=child,
                    parent_agent=parent_agent,
                )
                futures[future] = i

            # 使用中断检查轮询 futures。as_completed() 会阻塞
            # 直到所有 futures 完成——如果子代理卡住，
            # 即使中断传播后父代理也会永远阻塞。
            # 改用带短超时的 wait()，以便在父代理被中断时能退出。
            pending = set(futures.keys())
            while pending:
                if getattr(parent_agent, "_interrupt_requested", False) is True:
                    # 父代理被中断 — 收集已完成的结果并
                    # 放弃其余。子代理已经收到中断信号；
                    # 我们只是不能永远等待。
                    for f in pending:
                        idx = futures[f]
                        if f.done():
                            try:
                                entry = f.result()
                            except Exception as exc:
                                entry = {
                                    "task_index": idx,
                                    "status": "error",
                                    "summary": None,
                                    "error": str(exc),
                                    "api_calls": 0,
                                    "duration_seconds": 0,
                                }
                        else:
                            entry = {
                                "task_index": idx,
                                "status": "interrupted",
                                "summary": None,
                                "error": "Parent agent interrupted — child did not finish in time",
                                "api_calls": 0,
                                "duration_seconds": 0,
                            }
                        results.append(entry)
                        completed_count += 1
                    break

                from concurrent.futures import wait as _cf_wait, FIRST_COMPLETED
                done, pending = _cf_wait(pending, timeout=0.5, return_when=FIRST_COMPLETED)
                for future in done:
                    try:
                        entry = future.result()
                    except Exception as exc:
                        idx = futures[future]
                        entry = {
                            "task_index": idx,
                            "status": "error",
                            "summary": None,
                            "error": str(exc),
                            "api_calls": 0,
                            "duration_seconds": 0,
                        }
                    results.append(entry)
                    completed_count += 1

                    # 在旋转动画上方打印每个任务的完成行
                    idx = entry["task_index"]
                    label = task_labels[idx] if idx < len(task_labels) else f"Task {idx}"
                    dur = entry.get("duration_seconds", 0)
                    status = entry.get("status", "?")
                    icon = "✓" if status == "completed" else "✗"
                    remaining = n_tasks - completed_count
                    completion_line = f"{icon} [{idx+1}/{n_tasks}] {label}  ({dur}s)"
                    if spinner_ref:
                        try:
                            spinner_ref.print_above(completion_line)
                        except Exception:
                            print(f"  {completion_line}")
                    else:
                        print(f"  {completion_line}")

                    # 更新旋转动画文本以显示剩余数量
                    if spinner_ref and remaining > 0:
                        try:
                            spinner_ref.update_text(f"🔀 {remaining} task{'s' if remaining != 1 else ''} remaining")
                        except Exception as e:
                            logger.debug("Spinner update_text failed: %s", e)

        # 按 task_index 排序以使结果与输入顺序匹配
        results.sort(key=lambda r: r["task_index"])

    # 通知父代理的记忆提供者委派结果
    if parent_agent and hasattr(parent_agent, '_memory_manager') and parent_agent._memory_manager:
        for entry in results:
            try:
                _task_goal = task_list[entry["task_index"]]["goal"] if entry["task_index"] < len(task_list) else ""
                parent_agent._memory_manager.on_delegation(
                    task=_task_goal,
                    result=entry.get("summary", "") or "",
                    child_session_id=getattr(children[entry["task_index"]][2], "session_id", "") if entry["task_index"] < len(children) else "",
                )
            except Exception:
                pass

    total_duration = round(time.monotonic() - overall_start, 2)

    return json.dumps({
        "results": results,
        "total_duration_seconds": total_duration,
    }, ensure_ascii=False)


def _resolve_child_credential_pool(effective_provider: Optional[str], parent_agent):
    """为子代理解析凭证池。

    规则：
    1. 与父代理相同的提供者 -> 共享父代理的池，以便冷却状态
       和轮换保持同步。
    2. 不同的提供者 -> 尝试加载该提供者自己的池。
    3. 无可用池 -> 返回 None，让子代理保持继承的
       固定凭证行为。
    """
    if not effective_provider:
        return getattr(parent_agent, "_credential_pool", None)

    parent_provider = getattr(parent_agent, "provider", None) or ""
    parent_pool = getattr(parent_agent, "_credential_pool", None)
    if parent_pool is not None and effective_provider == parent_provider:
        return parent_pool

    try:
        from agent.credential_pool import load_pool
        pool = load_pool(effective_provider)
        if pool is not None and pool.has_credentials():
            return pool
    except Exception as exc:
        logger.debug(
            "Could not load credential pool for child provider '%s': %s",
            effective_provider,
            exc,
        )
    return None


def _resolve_delegation_credentials(cfg: dict, parent_agent) -> dict:
    """解析子代理委派的凭证。

    如果配置了 ``delegation.base_url``，子代理使用该直接的
    OpenAI 兼容端点。否则，如果配置了 ``delegation.provider``，
    完整凭证包（base_url, api_key, api_mode, provider）通过运行时
    提供者系统解析——与 CLI/gateway 启动使用的相同路径。这使得
    子代理可以在完全不同的 provider:model 对上运行。

    如果既未配置 base_url 也未配置 provider，返回 None 值，
    子代理从父代理继承所有内容。

    凭证失败时抛出带有用户友好消息的 ValueError。
    """
    configured_model = str(cfg.get("model") or "").strip() or None
    configured_provider = str(cfg.get("provider") or "").strip() or None
    configured_base_url = str(cfg.get("base_url") or "").strip() or None
    configured_api_key = str(cfg.get("api_key") or "").strip() or None

    if configured_base_url:
        api_key = (
            configured_api_key
            or os.getenv("OPENAI_API_KEY", "").strip()
        )
        if not api_key:
            raise ValueError(
                "Delegation base_url is configured but no API key was found. "
                "Set delegation.api_key or OPENAI_API_KEY."
            )

        base_lower = configured_base_url.lower()
        provider = "custom"
        api_mode = "chat_completions"
        if "chatgpt.com/backend-api/codex" in base_lower:
            provider = "openai-codex"
            api_mode = "codex_responses"
        elif "api.anthropic.com" in base_lower:
            provider = "anthropic"
            api_mode = "anthropic_messages"

        return {
            "model": configured_model,
            "provider": provider,
            "base_url": configured_base_url,
            "api_key": api_key,
            "api_mode": api_mode,
        }

    if not configured_provider:
        # 无提供者覆盖 — 子代理从父代理继承所有内容
        return {
            "model": configured_model,
            "provider": None,
            "base_url": None,
            "api_key": None,
            "api_mode": None,
        }

    # 已配置提供者 — 解析完整凭证
    try:
        from hermes_cli.runtime_provider import resolve_runtime_provider
        runtime = resolve_runtime_provider(requested=configured_provider)
    except Exception as exc:
        raise ValueError(
            f"Cannot resolve delegation provider '{configured_provider}': {exc}. "
            f"Check that the provider is configured (API key set, valid provider name), "
            f"or set delegation.base_url/delegation.api_key for a direct endpoint. "
            f"Available providers: openrouter, nous, zai, kimi-coding, minimax."
        ) from exc

    api_key = runtime.get("api_key", "")
    if not api_key:
        raise ValueError(
            f"Delegation provider '{configured_provider}' resolved but has no API key. "
            f"Set the appropriate environment variable or run 'hermes auth'."
        )

    return {
        "model": configured_model,
        "provider": runtime.get("provider"),
        "base_url": runtime.get("base_url"),
        "api_key": api_key,
        "api_mode": runtime.get("api_mode"),
        "command": runtime.get("command"),
        "args": list(runtime.get("args") or []),
    }


def _load_config() -> dict:
    """从 CLI_CONFIG 或持久化配置加载委派配置。

    先检查运行时配置（cli.py CLI_CONFIG），然后回退到
    持久化配置（hermes_cli/config.py load_config()），以确保
    ``delegation.model`` / ``delegation.provider`` 不论入口点
    （CLI、gateway、cron）都能被获取。
    """
    try:
        from cli import CLI_CONFIG
        cfg = CLI_CONFIG.get("delegation", {})
        if cfg:
            return cfg
    except Exception:
        pass
    try:
        from hermes_cli.config import load_config
        full = load_config()
        return full.get("delegation", {})
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# OpenAI 函数调用 Schema
# ---------------------------------------------------------------------------

DELEGATE_TASK_SCHEMA = {
    "name": "delegate_task",
    "description": (
        "Spawn one or more subagents to work on tasks in isolated contexts. "
        "Each subagent gets its own conversation, terminal session, and toolset. "
        "Only the final summary is returned -- intermediate tool results "
        "never enter your context window.\n\n"
        "TWO MODES (one of 'goal' or 'tasks' is required):\n"
        "1. Single task: provide 'goal' (+ optional context, toolsets)\n"
        "2. Batch (parallel): provide 'tasks' array with up to 3 items. "
        "All run concurrently and results are returned together.\n\n"
        "WHEN TO USE delegate_task:\n"
        "- Reasoning-heavy subtasks (debugging, code review, research synthesis)\n"
        "- Tasks that would flood your context with intermediate data\n"
        "- Parallel independent workstreams (research A and B simultaneously)\n\n"
        "WHEN NOT TO USE (use these instead):\n"
        "- Mechanical multi-step work with no reasoning needed -> use execute_code\n"
        "- Single tool call -> just call the tool directly\n"
        "- Tasks needing user interaction -> subagents cannot use clarify\n\n"
        "IMPORTANT:\n"
        "- Subagents have NO memory of your conversation. Pass all relevant "
        "info (file paths, error messages, constraints) via the 'context' field.\n"
        "- Subagents CANNOT call: delegate_task, clarify, memory, send_message, "
        "execute_code.\n"
        "- Each subagent gets its own terminal session (separate working directory and state).\n"
        "- Results are always returned as an array, one entry per task."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "goal": {
                "type": "string",
                "description": (
                    "What the subagent should accomplish. Be specific and "
                    "self-contained -- the subagent knows nothing about your "
                    "conversation history."
                ),
            },
            "context": {
                "type": "string",
                "description": (
                    "Background information the subagent needs: file paths, "
                    "error messages, project structure, constraints. The more "
                    "specific you are, the better the subagent performs."
                ),
            },
            "toolsets": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Toolsets to enable for this subagent. "
                    "Default: inherits your enabled toolsets. "
                    f"Available toolsets: {_TOOLSET_LIST_STR}. "
                    "Common patterns: ['terminal', 'file'] for code work, "
                    "['web'] for research, ['browser'] for web interaction, "
                    "['terminal', 'file', 'web'] for full-stack tasks."
                ),
            },
            "tasks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "goal": {"type": "string", "description": "Task goal"},
                        "context": {"type": "string", "description": "Task-specific context"},
                        "toolsets": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": f"Toolsets for this specific task. Available: {_TOOLSET_LIST_STR}. Use 'web' for network access, 'terminal' for shell, 'browser' for web interaction.",
                        },
                        "acp_command": {
                            "type": "string",
                            "description": "Per-task ACP command override (e.g. 'claude'). Overrides the top-level acp_command for this task only.",
                        },
                        "acp_args": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Per-task ACP args override.",
                        },
                    },
                    "required": ["goal"],
                },
                # 无 maxItems — 运行时限制可通过
                # delegation.max_concurrent_children 配置（默认 3），
                # 并在 delegate_task() 中以清晰的错误信息强制执行。
                "description": (
                    "Batch mode: tasks to run in parallel (limit configurable via delegation.max_concurrent_children, default 3). Each gets "
                    "its own subagent with isolated context and terminal session. "
                    "When provided, top-level goal/context/toolsets are ignored."
                ),
            },
            "max_iterations": {
                "type": "integer",
                "description": (
                    "Max tool-calling turns per subagent (default: 50). "
                    "Only set lower for simple tasks."
                ),
            },
            "acp_command": {
                "type": "string",
                "description": (
                    "Override ACP command for child agents (e.g. 'claude', 'copilot'). "
                    "When set, children use ACP subprocess transport instead of inheriting "
                    "the parent's transport. Enables spawning Claude Code (claude --acp --stdio) "
                    "or other ACP-capable agents from any parent, including Discord/Telegram/CLI."
                ),
            },
            "acp_args": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Arguments for the ACP command (default: ['--acp', '--stdio']). "
                    "Only used when acp_command is set. Example: ['--acp', '--stdio', '--model', 'claude-opus-4-6']"
                ),
            },
        },
        "required": [],
    },
}


# --- 注册表 ---
from tools.registry import registry, tool_error

registry.register(
    name="delegate_task",
    toolset="delegation",
    schema=DELEGATE_TASK_SCHEMA,
    handler=lambda args, **kw: delegate_task(
        goal=args.get("goal"),
        context=args.get("context"),
        toolsets=args.get("toolsets"),
        tasks=args.get("tasks"),
        max_iterations=args.get("max_iterations"),
        acp_command=args.get("acp_command"),
        acp_args=args.get("acp_args"),
        parent_agent=kw.get("parent_agent")),
    check_fn=check_delegate_requirements,
    emoji="🔀",
)
