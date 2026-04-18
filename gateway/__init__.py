"""
Hermes Gateway - 多平台消息集成模块。

本模块为 Hermes 代理提供统一的网关接口，用于连接各类消息平台
（Telegram、Discord、WhatsApp），具备以下能力：
- 会话管理（持久化对话及重置策略）
- 动态上下文注入（让代理了解消息来源）
- 投递路由（将定时任务输出发送到对应频道）
- 平台特定工具集（不同平台提供不同功能）
"""

from .config import GatewayConfig, PlatformConfig, HomeChannel, load_gateway_config
from .session import (
    SessionContext,
    SessionStore,
    SessionResetPolicy,
    build_session_context_prompt,
)
from .delivery import DeliveryRouter, DeliveryTarget

__all__ = [
    # 配置相关
    "GatewayConfig",
    "PlatformConfig",
    "HomeChannel",
    "load_gateway_config",
    # 会话相关
    "SessionContext",
    "SessionStore",
    "SessionResetPolicy",
    "build_session_context_prompt",
    # 投递相关
    "DeliveryRouter",
    "DeliveryTarget",
]
