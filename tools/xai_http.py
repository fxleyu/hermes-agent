"""直接 xAI HTTP 集成的共享辅助函数。"""

from __future__ import annotations


def hermes_xai_user_agent() -> str:
    """返回一个稳定的 Hermes 专用 User-Agent 字符串，用于 xAI HTTP 调用。"""
    try:
        from hermes_cli import __version__
    except Exception:
        __version__ = "unknown"
    return f"Hermes-Agent/{__version__}"
