"""
Hermes 网关的会话级上下文变量。

替换之前基于 ``os.environ`` 的会话状态
（``HERMES_SESSION_PLATFORM``、``HERMES_SESSION_CHAT_ID`` 等），
改用 Python 的 ``contextvars.ContextVar``。

**为什么这很重要**

网关通过 ``asyncio`` 并发处理消息。当两条消息同时到达时，
旧代码执行了：

    os.environ["HERMES_SESSION_THREAD_ID"] = str(context.source.thread_id)

由于 ``os.environ`` 是*进程全局*的，消息 A 的值会在消息 A 的
代理完成运行之前被消息 B 静默覆盖。后台任务通知和工具调用
因此路由到了错误的线程。

``contextvars.ContextVar`` 的值是*任务本地*的：每个 ``asyncio``
任务（及其通过 ``run_in_executor`` 启动的线程）都有自己的副本，
所以并发的消息不会互相干扰。

**向后兼容**

公开的辅助函数 ``get_session_env(name, default="")`` 镜像了旧的
``os.getenv("HERMES_SESSION_*", ...)`` 调用。现有的工具代码只需
替换 import 和调用位置：

    # 之前
    import os
    platform = os.getenv("HERMES_SESSION_PLATFORM", "")

    # 之后
    from gateway.session_context import get_session_env
    platform = get_session_env("HERMES_SESSION_PLATFORM", "")
"""

from contextvars import ContextVar
from typing import Any

# 哨兵值，用于区分"在当前上下文中从未设置"和"显式设置为空"。
# 当 contextvar 持有 _UNSET 时，回退到 os.environ（CLI/cron 兼容）。
# 当它持有 ""（clear_session_vars 重置后）时，返回 "" — 不回退。
_UNSET: Any = object()

# ---------------------------------------------------------------------------
# 每个任务的会话变量
# ---------------------------------------------------------------------------

_SESSION_PLATFORM: ContextVar = ContextVar("HERMES_SESSION_PLATFORM", default=_UNSET)
_SESSION_CHAT_ID: ContextVar = ContextVar("HERMES_SESSION_CHAT_ID", default=_UNSET)
_SESSION_CHAT_NAME: ContextVar = ContextVar("HERMES_SESSION_CHAT_NAME", default=_UNSET)
_SESSION_THREAD_ID: ContextVar = ContextVar("HERMES_SESSION_THREAD_ID", default=_UNSET)
_SESSION_USER_ID: ContextVar = ContextVar("HERMES_SESSION_USER_ID", default=_UNSET)
_SESSION_USER_NAME: ContextVar = ContextVar("HERMES_SESSION_USER_NAME", default=_UNSET)
_SESSION_KEY: ContextVar = ContextVar("HERMES_SESSION_KEY", default=_UNSET)

_VAR_MAP = {
    "HERMES_SESSION_PLATFORM": _SESSION_PLATFORM,
    "HERMES_SESSION_CHAT_ID": _SESSION_CHAT_ID,
    "HERMES_SESSION_CHAT_NAME": _SESSION_CHAT_NAME,
    "HERMES_SESSION_THREAD_ID": _SESSION_THREAD_ID,
    "HERMES_SESSION_USER_ID": _SESSION_USER_ID,
    "HERMES_SESSION_USER_NAME": _SESSION_USER_NAME,
    "HERMES_SESSION_KEY": _SESSION_KEY,
}


def set_session_vars(
    platform: str = "",
    chat_id: str = "",
    chat_name: str = "",
    thread_id: str = "",
    user_id: str = "",
    user_name: str = "",
    session_key: str = "",
) -> list:
    """设置所有会话上下文变量并返回重置令牌。

    在 ``finally`` 块中调用 ``clear_session_vars(tokens)`` 以在
    处理器退出时恢复之前的值。

    返回一个 ``Token`` 对象列表（每个变量一个），可以传递给
    ``clear_session_vars``。
    """
    tokens = [
        _SESSION_PLATFORM.set(platform),
        _SESSION_CHAT_ID.set(chat_id),
        _SESSION_CHAT_NAME.set(chat_name),
        _SESSION_THREAD_ID.set(thread_id),
        _SESSION_USER_ID.set(user_id),
        _SESSION_USER_NAME.set(user_name),
        _SESSION_KEY.set(session_key),
    ]
    return tokens


def clear_session_vars(tokens: list) -> None:
    """将会话上下文变量标记为已显式清除。

    将所有变量设置为 ``""``，使 ``get_session_env`` 返回空字符串
    而不是回退到（可能已过时的）``os.environ`` 值。*tokens* 参数
    为了与保存了 ``set_session_vars`` 返回值的调用方的 API 兼容
    而接受，但实际清除使用 ``var.set("")`` 而非 ``var.reset(token)``，
    以确保"已显式清除"状态可与"从未设置"（持有 ``_UNSET`` 哨兵值）
    区分开。
    """
    for var in (
        _SESSION_PLATFORM,
        _SESSION_CHAT_ID,
        _SESSION_CHAT_NAME,
        _SESSION_THREAD_ID,
        _SESSION_USER_ID,
        _SESSION_USER_NAME,
        _SESSION_KEY,
    ):
        var.set("")


def get_session_env(name: str, default: str = "") -> str:
    """通过旧版 ``HERMES_SESSION_*`` 名称读取会话上下文变量。

    ``os.getenv("HERMES_SESSION_*", default)`` 的直接替代品。

    解析顺序：
    1. 上下文变量（由网关设置，用于并发安全访问）。
       如果变量已通过 ``set_session_vars`` 或 ``clear_session_vars``
       显式设置（即使设为 ``""``），返回该值 — **不回退到 os.environ**。
    2. ``os.environ``（仅当上下文变量在当前上下文中从未被设置时
       — 即 CLI、定时任务调度器和不使用 ``set_session_vars`` 的
       测试进程）。
    3. *default*
    """
    import os

    var = _VAR_MAP.get(name)
    if var is not None:
        value = var.get()
        if value is not _UNSET:
            return value
    # 为 CLI、cron 和测试兼容性回退到 os.environ
    return os.getenv(name, default)
