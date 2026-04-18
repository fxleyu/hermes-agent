"""将 AIAgent 事件桥接到 ACP 通知的回调工厂。

每个工厂返回一个符合 AIAgent 期望的回调签名的可调用对象。
在内部，回调通过 ``conn.session_update()`` 使用
``asyncio.run_coroutine_threadsafe()`` 将 ACP 会话更新推送给客户端
（因为 AIAgent 在工作线程中运行，而事件循环在主线程上）。
"""

import asyncio
import json
import logging
from collections import deque
from typing import Any, Callable, Deque, Dict

import acp

from .tools import (
    build_tool_complete,
    build_tool_start,
    make_tool_call_id,
)

logger = logging.getLogger(__name__)


def _send_update(
    conn: acp.Client,
    session_id: str,
    loop: asyncio.AbstractEventLoop,
    update: Any,
) -> None:
    """从工作线程中发送即发即忘的 ACP 会话更新。"""
    try:
        future = asyncio.run_coroutine_threadsafe(
            conn.session_update(session_id, update), loop
        )
        future.result(timeout=5)
    except Exception:
        logger.debug("Failed to send ACP update", exc_info=True)


# ------------------------------------------------------------------
# 工具进度回调
# ------------------------------------------------------------------

def make_tool_progress_cb(
    conn: acp.Client,
    session_id: str,
    loop: asyncio.AbstractEventLoop,
    tool_call_ids: Dict[str, Deque[str]],
) -> Callable:
    """创建 AIAgent 的 ``tool_progress_callback``。

    AIAgent 期望的签名::

        tool_progress_callback(event_type: str, name: str, preview: str, args: dict, **kwargs)

    为 ``tool.started`` 事件发出 ``ToolCallStart``，并在每个工具名称的 FIFO
    队列中追踪 ID，这样重复/并行的同名调用仍然能与正确的
    ACP 工具调用完成配对。其他事件类型（``tool.completed``、
    ``reasoning.available``）被静默忽略。
    """

    def _tool_progress(event_type: str, name: str = None, preview: str = None, args: Any = None, **kwargs) -> None:
        # 仅为 tool.started 事件发出 ACP ToolCallStart；忽略其他事件类型
        if event_type != "tool.started":
            return
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except (json.JSONDecodeError, TypeError):
                args = {"raw": args}
        if not isinstance(args, dict):
            args = {}

        tc_id = make_tool_call_id()
        queue = tool_call_ids.get(name)
        if queue is None:
            queue = deque()
            tool_call_ids[name] = queue
        elif isinstance(queue, str):
            queue = deque([queue])
            tool_call_ids[name] = queue
        queue.append(tc_id)

        update = build_tool_start(tc_id, name, args)
        _send_update(conn, session_id, loop, update)

    return _tool_progress


# ------------------------------------------------------------------
# 思考过程回调
# ------------------------------------------------------------------

def make_thinking_cb(
    conn: acp.Client,
    session_id: str,
    loop: asyncio.AbstractEventLoop,
) -> Callable:
    """创建 AIAgent 的 ``thinking_callback``。"""

    def _thinking(text: str) -> None:
        if not text:
            return
        update = acp.update_agent_thought_text(text)
        _send_update(conn, session_id, loop, update)

    return _thinking


# ------------------------------------------------------------------
# 步骤回调
# ------------------------------------------------------------------

def make_step_cb(
    conn: acp.Client,
    session_id: str,
    loop: asyncio.AbstractEventLoop,
    tool_call_ids: Dict[str, Deque[str]],
) -> Callable:
    """创建 AIAgent 的 ``step_callback``。

    AIAgent 期望的签名::

        step_callback(api_call_count: int, prev_tools: list)
    """

    def _step(api_call_count: int, prev_tools: Any = None) -> None:
        if prev_tools and isinstance(prev_tools, list):
            for tool_info in prev_tools:
                tool_name = None
                result = None

                if isinstance(tool_info, dict):
                    tool_name = tool_info.get("name") or tool_info.get("function_name")
                    result = tool_info.get("result") or tool_info.get("output")
                elif isinstance(tool_info, str):
                    tool_name = tool_info

                queue = tool_call_ids.get(tool_name or "")
                if isinstance(queue, str):
                    queue = deque([queue])
                    tool_call_ids[tool_name] = queue
                if tool_name and queue:
                    tc_id = queue.popleft()
                    update = build_tool_complete(
                        tc_id, tool_name, result=str(result) if result is not None else None
                    )
                    _send_update(conn, session_id, loop, update)
                    if not queue:
                        tool_call_ids.pop(tool_name, None)

    return _step


# ------------------------------------------------------------------
# 代理消息回调
# ------------------------------------------------------------------

def make_message_cb(
    conn: acp.Client,
    session_id: str,
    loop: asyncio.AbstractEventLoop,
) -> Callable:
    """创建一个将代理响应文本流式传输到编辑器的回调。"""

    def _message(text: str) -> None:
        if not text:
            return
        update = acp.update_agent_message_text(text)
        _send_update(conn, session_id, loop, update)

    return _message
