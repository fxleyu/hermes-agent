"""
Hermes Agent 的共享平台注册表。

作为平台元数据的唯一真实来源，被 skills_config（标签展示）和
tools_config（默认工具集解析）共同使用。应从此处导入 ``PLATFORMS``，
而不是在各个模块中维护重复的字典。
"""

from collections import OrderedDict
from typing import NamedTuple


class PlatformInfo(NamedTuple):
    """单个平台条目的元数据。"""
    label: str
    default_toolset: str


# 使用有序字典，确保 TUI 菜单的显示顺序是确定的。
PLATFORMS: OrderedDict[str, PlatformInfo] = OrderedDict([
    ("cli",            PlatformInfo(label="🖥️  CLI",            default_toolset="hermes-cli")),
    ("telegram",       PlatformInfo(label="📱 Telegram",        default_toolset="hermes-telegram")),
    ("discord",        PlatformInfo(label="💬 Discord",         default_toolset="hermes-discord")),
    ("slack",          PlatformInfo(label="💼 Slack",           default_toolset="hermes-slack")),
    ("whatsapp",       PlatformInfo(label="📱 WhatsApp",        default_toolset="hermes-whatsapp")),
    ("signal",         PlatformInfo(label="📡 Signal",          default_toolset="hermes-signal")),
    ("bluebubbles",    PlatformInfo(label="💙 BlueBubbles",     default_toolset="hermes-bluebubbles")),
    ("email",          PlatformInfo(label="📧 Email",           default_toolset="hermes-email")),
    ("homeassistant",  PlatformInfo(label="🏠 Home Assistant",  default_toolset="hermes-homeassistant")),
    ("mattermost",     PlatformInfo(label="💬 Mattermost",      default_toolset="hermes-mattermost")),
    ("matrix",         PlatformInfo(label="💬 Matrix",          default_toolset="hermes-matrix")),
    ("dingtalk",       PlatformInfo(label="💬 DingTalk",        default_toolset="hermes-dingtalk")),
    ("feishu",         PlatformInfo(label="🪽 Feishu",          default_toolset="hermes-feishu")),
    ("wecom",          PlatformInfo(label="💬 WeCom",           default_toolset="hermes-wecom")),
    ("wecom_callback", PlatformInfo(label="💬 WeCom Callback",  default_toolset="hermes-wecom-callback")),
    ("weixin",         PlatformInfo(label="💬 Weixin",          default_toolset="hermes-weixin")),
    ("qqbot",          PlatformInfo(label="💬 QQBot",           default_toolset="hermes-qqbot")),
    ("webhook",        PlatformInfo(label="🔗 Webhook",         default_toolset="hermes-webhook")),
    ("api_server",     PlatformInfo(label="🌐 API Server",      default_toolset="hermes-api-server")),
])


def platform_label(key: str, default: str = "") -> str:
    """返回平台键对应的显示标签，如果未找到则返回 *default*。"""
    info = PLATFORMS.get(key)
    return info.label if info is not None else default
