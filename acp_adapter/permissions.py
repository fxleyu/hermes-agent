"""ACP 权限桥接 -- 将 ACP 审批请求映射到 hermes 审批回调。"""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Callable

from acp.schema import (
    AllowedOutcome,
    PermissionOption,
)

logger = logging.getLogger(__name__)

# 映射 ACP PermissionOptionKind -> hermes 审批结果字符串
_KIND_TO_HERMES = {
    "allow_once": "once",
    "allow_always": "always",
    "reject_once": "deny",
    "reject_always": "deny",
}


def make_approval_callback(
    request_permission_fn: Callable,
    loop: asyncio.AbstractEventLoop,
    session_id: str,
    timeout: float = 60.0,
) -> Callable[[str, str], str]:
    """
    返回一个 hermes 兼容的 ``approval_callback(command, description) -> str``，
    桥接到 ACP 客户端的 ``request_permission`` 调用。

    Args:
        request_permission_fn: ACP 连接的 ``request_permission`` 协程。
        loop: ACP 连接所在的事件循环。
        session_id: 当前 ACP 会话 ID。
        timeout: 等待响应的超时秒数，超时后自动拒绝。
    """

    def _callback(command: str, description: str) -> str:
        options = [
            PermissionOption(option_id="allow_once", kind="allow_once", name="Allow once"),
            PermissionOption(option_id="allow_always", kind="allow_always", name="Allow always"),
            PermissionOption(option_id="deny", kind="reject_once", name="Deny"),
        ]
        import acp as _acp

        tool_call = _acp.start_tool_call("perm-check", command, kind="execute")

        coro = request_permission_fn(
            session_id=session_id,
            tool_call=tool_call,
            options=options,
        )

        try:
            future = asyncio.run_coroutine_threadsafe(coro, loop)
            response = future.result(timeout=timeout)
        except (FutureTimeout, Exception) as exc:
            logger.warning("Permission request timed out or failed: %s", exc)
            return "deny"

        outcome = response.outcome
        if isinstance(outcome, AllowedOutcome):
            option_id = outcome.option_id
            # 从选项列表中查找对应的 kind
            for opt in options:
                if opt.option_id == option_id:
                    return _KIND_TO_HERMES.get(opt.kind, "deny")
            return "once"  # fallback for unknown option_id
        else:
            return "deny"

    return _callback
