"""
频道目录 — 每个平台可达频道/联系人的缓存映射。

在网关启动时构建，定期刷新（每 5 分钟），并保存到
~/.hermes/channel_directory.json。send_message 工具读取此文件用于
action="list" 和将人类友好的频道名称解析为数字 ID。
"""

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from hermes_cli.config import get_hermes_home
from utils import atomic_json_write

logger = logging.getLogger(__name__)

DIRECTORY_PATH = get_hermes_home() / "channel_directory.json"


def _normalize_channel_query(value: str) -> str:
    return value.lstrip("#").strip().lower()


def _channel_target_name(platform_name: str, channel: Dict[str, Any]) -> str:
    """返回向用户展示的频道条目的人类可读目标标签。"""
    name = channel["name"]
    if platform_name == "discord" and channel.get("guild"):
        return f"#{name}"
    if platform_name != "discord" and channel.get("type"):
        return f"{name} ({channel['type']})"
    return name


def _session_entry_id(origin: Dict[str, Any]) -> Optional[str]:
    chat_id = origin.get("chat_id")
    if not chat_id:
        return None
    thread_id = origin.get("thread_id")
    if thread_id:
        return f"{chat_id}:{thread_id}"
    return str(chat_id)


def _session_entry_name(origin: Dict[str, Any]) -> str:
    base_name = origin.get("chat_name") or origin.get("user_name") or str(origin.get("chat_id"))
    thread_id = origin.get("thread_id")
    if not thread_id:
        return base_name

    topic_label = origin.get("chat_topic") or f"topic {thread_id}"
    return f"{base_name} / {topic_label}"


# ---------------------------------------------------------------------------
# 构建 / 刷新
# ---------------------------------------------------------------------------

def build_channel_directory(adapters: Dict[Any, Any]) -> Dict[str, Any]:
    """
    从已连接的平台适配器和会话数据构建频道目录。

    返回目录字典并写入 DIRECTORY_PATH。
    """
    from gateway.config import Platform

    platforms: Dict[str, List[Dict[str, str]]] = {}

    for platform, adapter in adapters.items():
        try:
            if platform == Platform.DISCORD:
                platforms["discord"] = _build_discord(adapter)
            elif platform == Platform.SLACK:
                platforms["slack"] = _build_slack(adapter)
        except Exception as e:
            logger.warning("Channel directory: failed to build %s: %s", platform.value, e)

    # 不支持直接频道枚举的平台会自动通过会话发现。
    # 跳过非消息平台的基础设施条目 — 其余通过 _build_from_sessions() 处理。
    _SKIP_SESSION_DISCOVERY = frozenset({"local", "api_server", "webhook"})
    for plat in Platform:
        plat_name = plat.value
        if plat_name in _SKIP_SESSION_DISCOVERY or plat_name in platforms:
            continue
        platforms[plat_name] = _build_from_sessions(plat_name)

    directory = {
        "updated_at": datetime.now().isoformat(),
        "platforms": platforms,
    }

    try:
        atomic_json_write(DIRECTORY_PATH, directory)
    except Exception as e:
        logger.warning("Channel directory: failed to write: %s", e)

    return directory


def _build_discord(adapter) -> List[Dict[str, str]]:
    """枚举 Discord 机器人能看到的所有文字频道。"""
    channels = []
    client = getattr(adapter, "_client", None)
    if not client:
        return channels

    try:
        import discord as _discord  # noqa: F401 — SDK presence check
    except ImportError:
        return channels

    for guild in client.guilds:
        for ch in guild.text_channels:
            channels.append({
                "id": str(ch.id),
                "name": ch.name,
                "guild": guild.name,
                "type": "channel",
            })
        # 通过服务器枚举无法获取可 DM 的用户；这些来自会话。

    # 合并会话历史中的 DM
    channels.extend(_build_from_sessions("discord"))
    return channels


def _build_slack(adapter) -> List[Dict[str, str]]:
    """列出机器人已加入的 Slack 频道。"""
    # Slack adapter may expose a web client
    client = getattr(adapter, "_app", None) or getattr(adapter, "_client", None)
    if not client:
        return _build_from_sessions("slack")

    try:
        from tools.send_message_tool import _send_slack  # noqa: F401
        # Use the Slack Web API directly if available
    except Exception:
        pass

    # 兜底到会话数据
    return _build_from_sessions("slack")


def _build_from_sessions(platform_name: str) -> List[Dict[str, str]]:
    """从 sessions.json 的 origin 数据中提取已知的频道/联系人。"""
    sessions_path = get_hermes_home() / "sessions" / "sessions.json"
    if not sessions_path.exists():
        return []

    entries = []
    try:
        with open(sessions_path, encoding="utf-8") as f:
            data = json.load(f)

        seen_ids = set()
        for _key, session in data.items():
            origin = session.get("origin") or {}
            if origin.get("platform") != platform_name:
                continue
            entry_id = _session_entry_id(origin)
            if not entry_id or entry_id in seen_ids:
                continue
            seen_ids.add(entry_id)
            entries.append({
                "id": entry_id,
                "name": _session_entry_name(origin),
                "type": session.get("chat_type", "dm"),
                "thread_id": origin.get("thread_id"),
            })
    except Exception as e:
        logger.debug("Channel directory: failed to read sessions for %s: %s", platform_name, e)

    return entries


# ---------------------------------------------------------------------------
# 读取 / 解析
# ---------------------------------------------------------------------------

def load_directory() -> Dict[str, Any]:
    """从磁盘加载缓存的频道目录。"""
    if not DIRECTORY_PATH.exists():
        return {"updated_at": None, "platforms": {}}
    try:
        with open(DIRECTORY_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"updated_at": None, "platforms": {}}


def resolve_channel_name(platform_name: str, name: str) -> Optional[str]:
    """
    将人类友好的频道名称解析为数字 ID。

    匹配策略（不区分大小写，首次匹配优先）：
    - Discord："bot-home"、"#bot-home"、"GuildName/bot-home"
    - Telegram：显示名称或群组名称
    - Slack："engineering"、"#engineering"
    """
    directory = load_directory()
    channels = directory.get("platforms", {}).get(platform_name, [])
    if not channels:
        return None

    query = _normalize_channel_query(name)

    # 1. 精确名称匹配，包括 send_message(action="list") 显示的标签
    for ch in channels:
        if _normalize_channel_query(ch["name"]) == query:
            return ch["id"]
        if _normalize_channel_query(_channel_target_name(platform_name, ch)) == query:
            return ch["id"]

    # 2. Discord 的服务器限定匹配（"GuildName/channel"）
    if "/" in query:
        guild_part, ch_part = query.rsplit("/", 1)
        for ch in channels:
            guild = ch.get("guild", "").strip().lower()
            if guild == guild_part and _normalize_channel_query(ch["name"]) == ch_part:
                return ch["id"]

    # 3. 部分前缀匹配（仅在结果无歧义时）
    matches = [ch for ch in channels if _normalize_channel_query(ch["name"]).startswith(query)]
    if len(matches) == 1:
        return matches[0]["id"]

    return None


def format_directory_for_display() -> str:
    """将频道目录格式化为供模型使用的人类可读列表。"""
    directory = load_directory()
    platforms = directory.get("platforms", {})

    if not any(platforms.values()):
        return "No messaging platforms connected or no channels discovered yet."

    lines = ["Available messaging targets:\n"]

    for plat_name, channels in sorted(platforms.items()):
        if not channels:
            continue

        # 按服务器分组 Discord 频道
        if plat_name == "discord":
            guilds: Dict[str, List] = {}
            dms: List = []
            for ch in channels:
                guild = ch.get("guild")
                if guild:
                    guilds.setdefault(guild, []).append(ch)
                else:
                    dms.append(ch)

            for guild_name, guild_channels in sorted(guilds.items()):
                lines.append(f"Discord ({guild_name}):")
                for ch in sorted(guild_channels, key=lambda c: c["name"]):
                    lines.append(f"  discord:{_channel_target_name(plat_name, ch)}")
            if dms:
                lines.append("Discord (DMs):")
                for ch in dms:
                    lines.append(f"  discord:{_channel_target_name(plat_name, ch)}")
            lines.append("")
        else:
            lines.append(f"{plat_name.title()}:")
            for ch in channels:
                lines.append(f"  {plat_name}:{_channel_target_name(plat_name, ch)}")
            lines.append("")

    lines.append('Use these as the "target" parameter when sending.')
    lines.append('Bare platform name (e.g. "telegram") sends to home channel.')

    return "\n".join(lines)
