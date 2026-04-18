"""
消息平台适配器集合。

每个适配器负责：
- 从对应平台接收消息
- 向平台发送消息/回复
- 处理平台特定的身份认证
- 消息格式化与媒体文件处理
"""

from .base import BasePlatformAdapter, MessageEvent, SendResult
from .qqbot import QQAdapter

__all__ = [
    "BasePlatformAdapter",
    "MessageEvent",
    "SendResult",
    "QQAdapter",
]
