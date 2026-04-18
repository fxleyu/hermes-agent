"""
网关配置管理模块。

负责加载和校验以下配置项：
- 已连接的平台（Telegram、Discord、WhatsApp）
- 各平台的主频道（Home Channel）
- 会话重置策略
- 消息投递偏好设置
"""

import logging
import os
import json
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from enum import Enum

from hermes_cli.config import get_hermes_home
from utils import is_truthy_value

logger = logging.getLogger(__name__)


def _coerce_bool(value: Any, default: bool = True) -> bool:
    """将配置中类似布尔值的内容强制转换为 bool 类型，保留调用方指定的默认值。"""
    if value is None:
        return default
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "1", "yes", "on"):
            return True
        if lowered in ("false", "0", "no", "off"):
            return False
        return default
    return is_truthy_value(value, default=default)


def _normalize_unauthorized_dm_behavior(value: Any, default: str = "pair") -> str:
    """将未授权 DM 行为配置规范化为支持的取值（"pair" 或 "ignore"）。"""
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"pair", "ignore"}:
            return normalized
    return default


class Platform(Enum):
    """支持的消息平台枚举。"""
    LOCAL = "local"
    TELEGRAM = "telegram"
    DISCORD = "discord"
    WHATSAPP = "whatsapp"
    SLACK = "slack"
    SIGNAL = "signal"
    MATTERMOST = "mattermost"
    MATRIX = "matrix"
    HOMEASSISTANT = "homeassistant"
    EMAIL = "email"
    SMS = "sms"
    DINGTALK = "dingtalk"
    API_SERVER = "api_server"
    WEBHOOK = "webhook"
    FEISHU = "feishu"
    WECOM = "wecom"
    WECOM_CALLBACK = "wecom_callback"
    WEIXIN = "weixin"
    BLUEBUBBLES = "bluebubbles"
    QQBOT = "qqbot"


@dataclass
class HomeChannel:
    """
    平台的默认投递目标。

    当定时任务指定 deliver="telegram" 但未提供具体 chat ID 时，
    消息将发送到此主频道。
    """
    platform: Platform
    chat_id: str
    name: str  # 用于界面展示的人类可读名称
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "platform": self.platform.value,
            "chat_id": self.chat_id,
            "name": self.name,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "HomeChannel":
        return cls(
            platform=Platform(data["platform"]),
            chat_id=str(data["chat_id"]),
            name=data.get("name", "Home"),
        )


@dataclass
class SessionResetPolicy:
    """
    控制会话何时重置（丢失上下文）。

    模式说明：
    - "daily"：每天在指定时刻重置
    - "idle"：在 N 分钟无活动后重置
    - "both"：两个条件哪个先触发就重置（每日边界 或 空闲超时）
    - "none"：永不自动重置（上下文仅通过压缩管理）
    """
    mode: str = "both"  # "daily"、"idle"、"both" 或 "none"
    at_hour: int = 4  # 每日重置时刻（0-23，本地时间）
    idle_minutes: int = 1440  # 无活动多少分钟后重置（默认 24 小时）
    notify: bool = True  # 自动重置时是否通知用户
    notify_exclude_platforms: tuple = ("api_server", "webhook")  # 不发送重置通知的平台
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "at_hour": self.at_hour,
            "idle_minutes": self.idle_minutes,
            "notify": self.notify,
            "notify_exclude_platforms": list(self.notify_exclude_platforms),
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SessionResetPolicy":
        # 处理缺失的键和显式 null 值（YAML 中的 null 会变成 Python 的 None）
        mode = data.get("mode")
        at_hour = data.get("at_hour")
        idle_minutes = data.get("idle_minutes")
        notify = data.get("notify")
        exclude = data.get("notify_exclude_platforms")
        return cls(
            mode=mode if mode is not None else "both",
            at_hour=at_hour if at_hour is not None else 4,
            idle_minutes=idle_minutes if idle_minutes is not None else 1440,
            notify=notify if notify is not None else True,
            notify_exclude_platforms=tuple(exclude) if exclude is not None else ("api_server", "webhook"),
        )


@dataclass
class PlatformConfig:
    """单个消息平台的配置。"""
    enabled: bool = False
    token: Optional[str] = None  # 机器人令牌（Telegram、Discord）
    api_key: Optional[str] = None  # API 密钥（与 token 不同时使用）
    home_channel: Optional[HomeChannel] = None

    # 回复线程模式（Telegram/Slack）
    # - "off"：从不将回复线程化到原始消息
    # - "first"：仅第一段回复线程化到用户消息（默认）
    # - "all"：多段回复中所有段都线程化到用户消息
    reply_to_mode: str = "first"

    # 平台特定设置
    extra: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        result = {
            "enabled": self.enabled,
            "extra": self.extra,
            "reply_to_mode": self.reply_to_mode,
        }
        if self.token:
            result["token"] = self.token
        if self.api_key:
            result["api_key"] = self.api_key
        if self.home_channel:
            result["home_channel"] = self.home_channel.to_dict()
        return result
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PlatformConfig":
        home_channel = None
        if "home_channel" in data:
            home_channel = HomeChannel.from_dict(data["home_channel"])
        
        return cls(
            enabled=data.get("enabled", False),
            token=data.get("token"),
            api_key=data.get("api_key"),
            home_channel=home_channel,
            reply_to_mode=data.get("reply_to_mode", "first"),
            extra=data.get("extra", {}),
        )


@dataclass
class StreamingConfig:
    """消息平台实时 token 流式传输的配置。"""
    enabled: bool = False
    transport: str = "edit"       # "edit"（渐进式编辑消息文本）或 "off"
    edit_interval: float = 1.0    # 消息编辑间隔秒数（Telegram 限速约为 1 次/秒）
    buffer_threshold: int = 40    # 缓冲多少字符后强制触发一次编辑
    cursor: str = " ▉"           # 流式传输时显示的光标符号

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "transport": self.transport,
            "edit_interval": self.edit_interval,
            "buffer_threshold": self.buffer_threshold,
            "cursor": self.cursor,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StreamingConfig":
        if not data:
            return cls()
        return cls(
            enabled=data.get("enabled", False),
            transport=data.get("transport", "edit"),
            edit_interval=float(data.get("edit_interval", 1.0)),
            buffer_threshold=int(data.get("buffer_threshold", 40)),
            cursor=data.get("cursor", " ▉"),
        )


@dataclass
class GatewayConfig:
    """
    网关主配置。

    管理所有平台连接、会话策略和投递设置。
    """
    # 平台配置
    platforms: Dict[Platform, PlatformConfig] = field(default_factory=dict)

    # 按类型划分的会话重置策略
    default_reset_policy: SessionResetPolicy = field(default_factory=SessionResetPolicy)
    reset_by_type: Dict[str, SessionResetPolicy] = field(default_factory=dict)
    reset_by_platform: Dict[Platform, SessionResetPolicy] = field(default_factory=dict)

    # 重置触发命令
    reset_triggers: List[str] = field(default_factory=lambda: ["/new", "/reset"])

    # 用户自定义快捷命令（绕过代理循环的斜杠命令）
    quick_commands: Dict[str, Any] = field(default_factory=dict)

    # 存储路径
    sessions_dir: Path = field(default_factory=lambda: get_hermes_home() / "sessions")

    # 投递设置
    always_log_local: bool = True  # 是否始终将定时任务输出保存到本地文件

    # 语音转文字设置
    stt_enabled: bool = True  # 是否自动转录收到的语音消息

    # 共享聊天中的会话隔离
    group_sessions_per_user: bool = True  # 当有用户 ID 时，在群组/频道中按参与者隔离会话
    thread_sessions_per_user: bool = False  # 为 False（默认）时，线程中所有参与者共享会话

    # 未授权 DM 策略
    unauthorized_dm_behavior: str = "pair"  # "pair" 或 "ignore"

    # 流式传输配置
    streaming: StreamingConfig = field(default_factory=StreamingConfig)

    def get_connected_platforms(self) -> List[Platform]:
        """返回已启用且已配置的平台列表。"""
        connected = []
        for platform, config in self.platforms.items():
            if not config.enabled:
                continue
            # 微信需要同时提供 token 和 account_id
            if platform == Platform.WEIXIN:
                if config.extra.get("account_id") and (config.token or config.extra.get("token")):
                    connected.append(platform)
                continue
            # 使用 token/api_key 认证的平台
            if config.token or config.api_key:
                connected.append(platform)
            # WhatsApp 仅使用 enabled 标志（桥接层处理认证）
            elif platform == Platform.WHATSAPP:
                connected.append(platform)
            # Signal 使用 extra 字典配置（http_url + account）
            elif platform == Platform.SIGNAL and config.extra.get("http_url"):
                connected.append(platform)
            # Email 使用 extra 字典配置（address + imap_host + smtp_host）
            elif platform == Platform.EMAIL and config.extra.get("address"):
                connected.append(platform)
            # SMS 使用 api_key（Twilio auth token）— SID 通过环境变量检查
            elif platform == Platform.SMS and os.getenv("TWILIO_ACCOUNT_SID"):
                connected.append(platform)
            # API Server 仅使用 enabled 标志（不需要 token）
            elif platform == Platform.API_SERVER:
                connected.append(platform)
            # Webhook 仅使用 enabled 标志（密钥按路由配置）
            elif platform == Platform.WEBHOOK:
                connected.append(platform)
            # 飞书使用 extra 字典的 app 凭证
            elif platform == Platform.FEISHU and config.extra.get("app_id"):
                connected.append(platform)
            # 企业微信机器人模式使用 extra 字典的 bot 凭证
            elif platform == Platform.WECOM and config.extra.get("bot_id"):
                connected.append(platform)
            # 企业微信回调模式使用 corp_id 或 apps 列表
            elif platform == Platform.WECOM_CALLBACK and (
                config.extra.get("corp_id") or config.extra.get("apps")
            ):
                connected.append(platform)
            # BlueBubbles 使用 extra 字典的本地服务器配置
            elif platform == Platform.BLUEBUBBLES and config.extra.get("server_url") and config.extra.get("password"):
                connected.append(platform)
            # QQ 机器人使用 extra 字典的 app 凭证
            elif platform == Platform.QQBOT and config.extra.get("app_id") and config.extra.get("client_secret"):
                connected.append(platform)
        return connected
    
    def get_home_channel(self, platform: Platform) -> Optional[HomeChannel]:
        """获取指定平台的主频道。"""
        config = self.platforms.get(platform)
        if config:
            return config.home_channel
        return None
    
    def get_reset_policy(
        self,
        platform: Optional[Platform] = None,
        session_type: Optional[str] = None
    ) -> SessionResetPolicy:
        """
        获取适用于某个会话的重置策略。

        优先级：平台覆盖 > 类型覆盖 > 默认策略
        """
        # 平台级别的覆盖优先级最高
        if platform and platform in self.reset_by_platform:
            return self.reset_by_platform[platform]

        # 类型级别的覆盖（dm、group、thread）
        if session_type and session_type in self.reset_by_type:
            return self.reset_by_type[session_type]

        return self.default_reset_policy
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "platforms": {
                p.value: c.to_dict() for p, c in self.platforms.items()
            },
            "default_reset_policy": self.default_reset_policy.to_dict(),
            "reset_by_type": {
                k: v.to_dict() for k, v in self.reset_by_type.items()
            },
            "reset_by_platform": {
                p.value: v.to_dict() for p, v in self.reset_by_platform.items()
            },
            "reset_triggers": self.reset_triggers,
            "quick_commands": self.quick_commands,
            "sessions_dir": str(self.sessions_dir),
            "always_log_local": self.always_log_local,
            "stt_enabled": self.stt_enabled,
            "group_sessions_per_user": self.group_sessions_per_user,
            "thread_sessions_per_user": self.thread_sessions_per_user,
            "unauthorized_dm_behavior": self.unauthorized_dm_behavior,
            "streaming": self.streaming.to_dict(),
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GatewayConfig":
        platforms = {}
        for platform_name, platform_data in data.get("platforms", {}).items():
            try:
                platform = Platform(platform_name)
                platforms[platform] = PlatformConfig.from_dict(platform_data)
            except ValueError:
                pass  # 跳过未知平台
        
        reset_by_type = {}
        for type_name, policy_data in data.get("reset_by_type", {}).items():
            reset_by_type[type_name] = SessionResetPolicy.from_dict(policy_data)
        
        reset_by_platform = {}
        for platform_name, policy_data in data.get("reset_by_platform", {}).items():
            try:
                platform = Platform(platform_name)
                reset_by_platform[platform] = SessionResetPolicy.from_dict(policy_data)
            except ValueError:
                pass
        
        default_policy = SessionResetPolicy()
        if "default_reset_policy" in data:
            default_policy = SessionResetPolicy.from_dict(data["default_reset_policy"])
        
        sessions_dir = get_hermes_home() / "sessions"
        if "sessions_dir" in data:
            sessions_dir = Path(data["sessions_dir"])
        
        quick_commands = data.get("quick_commands", {})
        if not isinstance(quick_commands, dict):
            quick_commands = {}

        stt_enabled = data.get("stt_enabled")
        if stt_enabled is None:
            stt_enabled = data.get("stt", {}).get("enabled") if isinstance(data.get("stt"), dict) else None

        group_sessions_per_user = data.get("group_sessions_per_user")
        thread_sessions_per_user = data.get("thread_sessions_per_user")
        unauthorized_dm_behavior = _normalize_unauthorized_dm_behavior(
            data.get("unauthorized_dm_behavior"),
            "pair",
        )

        return cls(
            platforms=platforms,
            default_reset_policy=default_policy,
            reset_by_type=reset_by_type,
            reset_by_platform=reset_by_platform,
            reset_triggers=data.get("reset_triggers", ["/new", "/reset"]),
            quick_commands=quick_commands,
            sessions_dir=sessions_dir,
            always_log_local=data.get("always_log_local", True),
            stt_enabled=_coerce_bool(stt_enabled, True),
            group_sessions_per_user=_coerce_bool(group_sessions_per_user, True),
            thread_sessions_per_user=_coerce_bool(thread_sessions_per_user, False),
            unauthorized_dm_behavior=unauthorized_dm_behavior,
            streaming=StreamingConfig.from_dict(data.get("streaming", {})),
        )

    def get_unauthorized_dm_behavior(self, platform: Optional[Platform] = None) -> str:
        """返回指定平台生效的未授权 DM 行为策略。"""
        if platform:
            platform_cfg = self.platforms.get(platform)
            if platform_cfg and "unauthorized_dm_behavior" in platform_cfg.extra:
                return _normalize_unauthorized_dm_behavior(
                    platform_cfg.extra.get("unauthorized_dm_behavior"),
                    self.unauthorized_dm_behavior,
                )
        return self.unauthorized_dm_behavior


def load_gateway_config() -> GatewayConfig:
    """
    从多个来源加载网关配置。

    优先级（从高到低）：
    1. 环境变量
    2. ~/.hermes/config.yaml（主要的用户配置文件）
    3. ~/.hermes/gateway.json（旧版 — 在 config.yaml 下层提供默认值）
    4. 内置默认值
    """
    _home = get_hermes_home()
    gw_data: dict = {}

    # 旧版兜底：gateway.json 提供基础配置层。
    # 当两者都配置了同一设置时，config.yaml 的值始终优先。
    gateway_json_path = _home / "gateway.json"
    if gateway_json_path.exists():
        try:
            with open(gateway_json_path, "r", encoding="utf-8") as f:
                gw_data = json.load(f) or {}
            logger.info(
                "Loaded legacy %s — consider moving settings to config.yaml",
                gateway_json_path,
            )
        except Exception as e:
            logger.warning("Failed to load %s: %s", gateway_json_path, e)

    # 主要来源：config.yaml
    try:
        import yaml
        config_yaml_path = _home / "config.yaml"
        if config_yaml_path.exists():
            with open(config_yaml_path, encoding="utf-8") as f:
                yaml_cfg = yaml.safe_load(f) or {}

            # 将 config.yaml 的键映射到 GatewayConfig.from_dict() 的 schema。
            # 每个键都会覆盖 gateway.json 中可能已设置的对应值。
            sr = yaml_cfg.get("session_reset")
            if sr and isinstance(sr, dict):
                gw_data["default_reset_policy"] = sr

            qc = yaml_cfg.get("quick_commands")
            if qc is not None:
                if isinstance(qc, dict):
                    gw_data["quick_commands"] = qc
                else:
                    logger.warning(
                        "Ignoring invalid quick_commands in config.yaml "
                        "(expected mapping, got %s)",
                        type(qc).__name__,
                    )

            stt_cfg = yaml_cfg.get("stt")
            if isinstance(stt_cfg, dict):
                gw_data["stt"] = stt_cfg

            if "group_sessions_per_user" in yaml_cfg:
                gw_data["group_sessions_per_user"] = yaml_cfg["group_sessions_per_user"]

            if "thread_sessions_per_user" in yaml_cfg:
                gw_data["thread_sessions_per_user"] = yaml_cfg["thread_sessions_per_user"]

            streaming_cfg = yaml_cfg.get("streaming")
            if isinstance(streaming_cfg, dict):
                gw_data["streaming"] = streaming_cfg

            if "reset_triggers" in yaml_cfg:
                gw_data["reset_triggers"] = yaml_cfg["reset_triggers"]

            if "always_log_local" in yaml_cfg:
                gw_data["always_log_local"] = yaml_cfg["always_log_local"]

            if "unauthorized_dm_behavior" in yaml_cfg:
                gw_data["unauthorized_dm_behavior"] = _normalize_unauthorized_dm_behavior(
                    yaml_cfg.get("unauthorized_dm_behavior"),
                    "pair",
                )

            # 将 config.yaml 的 platforms 部分合并到 gw_data，
            # 以便加载嵌套键如 platforms.webhook.extra.routes。
            yaml_platforms = yaml_cfg.get("platforms")
            platforms_data = gw_data.setdefault("platforms", {})
            if not isinstance(platforms_data, dict):
                platforms_data = {}
                gw_data["platforms"] = platforms_data
            if isinstance(yaml_platforms, dict):
                for plat_name, plat_block in yaml_platforms.items():
                    if not isinstance(plat_block, dict):
                        continue
                    existing = platforms_data.get(plat_name, {})
                    if not isinstance(existing, dict):
                        existing = {}
                    # 深度合并 extra 字典，保留 gateway.json 的默认值
                    merged_extra = {**existing.get("extra", {}), **plat_block.get("extra", {})}
                    merged = {**existing, **plat_block}
                    if merged_extra:
                        merged["extra"] = merged_extra
                    platforms_data[plat_name] = merged
                gw_data["platforms"] = platforms_data
            for plat in Platform:
                if plat == Platform.LOCAL:
                    continue
                platform_cfg = yaml_cfg.get(plat.value)
                if not isinstance(platform_cfg, dict):
                    continue
                # 从该平台配置区域收集可桥接的键
                bridged = {}
                if "unauthorized_dm_behavior" in platform_cfg:
                    bridged["unauthorized_dm_behavior"] = _normalize_unauthorized_dm_behavior(
                        platform_cfg.get("unauthorized_dm_behavior"),
                        gw_data.get("unauthorized_dm_behavior", "pair"),
                    )
                if "reply_prefix" in platform_cfg:
                    bridged["reply_prefix"] = platform_cfg["reply_prefix"]
                if "require_mention" in platform_cfg:
                    bridged["require_mention"] = platform_cfg["require_mention"]
                if "free_response_channels" in platform_cfg:
                    bridged["free_response_channels"] = platform_cfg["free_response_channels"]
                if "mention_patterns" in platform_cfg:
                    bridged["mention_patterns"] = platform_cfg["mention_patterns"]
                if plat == Platform.DISCORD and "channel_skill_bindings" in platform_cfg:
                    bridged["channel_skill_bindings"] = platform_cfg["channel_skill_bindings"]
                if "channel_prompts" in platform_cfg:
                    channel_prompts = platform_cfg["channel_prompts"]
                    if isinstance(channel_prompts, dict):
                        bridged["channel_prompts"] = {str(k): v for k, v in channel_prompts.items()}
                    else:
                        bridged["channel_prompts"] = channel_prompts
                if not bridged:
                    continue
                plat_data = platforms_data.setdefault(plat.value, {})
                if not isinstance(plat_data, dict):
                    plat_data = {}
                    platforms_data[plat.value] = plat_data
                extra = plat_data.setdefault("extra", {})
                if not isinstance(extra, dict):
                    extra = {}
                    plat_data["extra"] = extra
                extra.update(bridged)

            # Slack 设置 → 环境变量（环境变量优先）
            slack_cfg = yaml_cfg.get("slack", {})
            if isinstance(slack_cfg, dict):
                if "require_mention" in slack_cfg and not os.getenv("SLACK_REQUIRE_MENTION"):
                    os.environ["SLACK_REQUIRE_MENTION"] = str(slack_cfg["require_mention"]).lower()
                if "allow_bots" in slack_cfg and not os.getenv("SLACK_ALLOW_BOTS"):
                    os.environ["SLACK_ALLOW_BOTS"] = str(slack_cfg["allow_bots"]).lower()
                frc = slack_cfg.get("free_response_channels")
                if frc is not None and not os.getenv("SLACK_FREE_RESPONSE_CHANNELS"):
                    if isinstance(frc, list):
                        frc = ",".join(str(v) for v in frc)
                    os.environ["SLACK_FREE_RESPONSE_CHANNELS"] = str(frc)

            # Discord 设置 → 环境变量（环境变量优先）
            discord_cfg = yaml_cfg.get("discord", {})
            if isinstance(discord_cfg, dict):
                if "require_mention" in discord_cfg and not os.getenv("DISCORD_REQUIRE_MENTION"):
                    os.environ["DISCORD_REQUIRE_MENTION"] = str(discord_cfg["require_mention"]).lower()
                frc = discord_cfg.get("free_response_channels")
                if frc is not None and not os.getenv("DISCORD_FREE_RESPONSE_CHANNELS"):
                    if isinstance(frc, list):
                        frc = ",".join(str(v) for v in frc)
                    os.environ["DISCORD_FREE_RESPONSE_CHANNELS"] = str(frc)
                if "auto_thread" in discord_cfg and not os.getenv("DISCORD_AUTO_THREAD"):
                    os.environ["DISCORD_AUTO_THREAD"] = str(discord_cfg["auto_thread"]).lower()
                if "reactions" in discord_cfg and not os.getenv("DISCORD_REACTIONS"):
                    os.environ["DISCORD_REACTIONS"] = str(discord_cfg["reactions"]).lower()
                # ignored_channels：机器人永不响应的频道（即使被提及也不响应）
                ic = discord_cfg.get("ignored_channels")
                if ic is not None and not os.getenv("DISCORD_IGNORED_CHANNELS"):
                    if isinstance(ic, list):
                        ic = ",".join(str(v) for v in ic)
                    os.environ["DISCORD_IGNORED_CHANNELS"] = str(ic)
                # allowed_channels：如果设置了，机器人只在这些频道中响应（白名单）
                ac = discord_cfg.get("allowed_channels")
                if ac is not None and not os.getenv("DISCORD_ALLOWED_CHANNELS"):
                    if isinstance(ac, list):
                        ac = ",".join(str(v) for v in ac)
                    os.environ["DISCORD_ALLOWED_CHANNELS"] = str(ac)
                # no_thread_channels：机器人直接回复而不创建线程的频道
                ntc = discord_cfg.get("no_thread_channels")
                if ntc is not None and not os.getenv("DISCORD_NO_THREAD_CHANNELS"):
                    if isinstance(ntc, list):
                        ntc = ",".join(str(v) for v in ntc)
                    os.environ["DISCORD_NO_THREAD_CHANNELS"] = str(ntc)

            # Telegram 设置 → 环境变量（环境变量优先）
            telegram_cfg = yaml_cfg.get("telegram", {})
            if isinstance(telegram_cfg, dict):
                if "require_mention" in telegram_cfg and not os.getenv("TELEGRAM_REQUIRE_MENTION"):
                    os.environ["TELEGRAM_REQUIRE_MENTION"] = str(telegram_cfg["require_mention"]).lower()
                if "mention_patterns" in telegram_cfg and not os.getenv("TELEGRAM_MENTION_PATTERNS"):
                    import json as _json
                    os.environ["TELEGRAM_MENTION_PATTERNS"] = _json.dumps(telegram_cfg["mention_patterns"])
                frc = telegram_cfg.get("free_response_chats")
                if frc is not None and not os.getenv("TELEGRAM_FREE_RESPONSE_CHATS"):
                    if isinstance(frc, list):
                        frc = ",".join(str(v) for v in frc)
                    os.environ["TELEGRAM_FREE_RESPONSE_CHATS"] = str(frc)
                ignored_threads = telegram_cfg.get("ignored_threads")
                if ignored_threads is not None and not os.getenv("TELEGRAM_IGNORED_THREADS"):
                    if isinstance(ignored_threads, list):
                        ignored_threads = ",".join(str(v) for v in ignored_threads)
                    os.environ["TELEGRAM_IGNORED_THREADS"] = str(ignored_threads)
                if "reactions" in telegram_cfg and not os.getenv("TELEGRAM_REACTIONS"):
                    os.environ["TELEGRAM_REACTIONS"] = str(telegram_cfg["reactions"]).lower()
                if "proxy_url" in telegram_cfg and not os.getenv("TELEGRAM_PROXY"):
                    os.environ["TELEGRAM_PROXY"] = str(telegram_cfg["proxy_url"]).strip()
                if "disable_link_previews" in telegram_cfg:
                    plat_data = platforms_data.setdefault(Platform.TELEGRAM.value, {})
                    if not isinstance(plat_data, dict):
                        plat_data = {}
                        platforms_data[Platform.TELEGRAM.value] = plat_data
                    extra = plat_data.setdefault("extra", {})
                    if not isinstance(extra, dict):
                        extra = {}
                        plat_data["extra"] = extra
                    extra["disable_link_previews"] = telegram_cfg["disable_link_previews"]

            whatsapp_cfg = yaml_cfg.get("whatsapp", {})
            if isinstance(whatsapp_cfg, dict):
                if "require_mention" in whatsapp_cfg and not os.getenv("WHATSAPP_REQUIRE_MENTION"):
                    os.environ["WHATSAPP_REQUIRE_MENTION"] = str(whatsapp_cfg["require_mention"]).lower()
                if "mention_patterns" in whatsapp_cfg and not os.getenv("WHATSAPP_MENTION_PATTERNS"):
                    os.environ["WHATSAPP_MENTION_PATTERNS"] = json.dumps(whatsapp_cfg["mention_patterns"])
                frc = whatsapp_cfg.get("free_response_chats")
                if frc is not None and not os.getenv("WHATSAPP_FREE_RESPONSE_CHATS"):
                    if isinstance(frc, list):
                        frc = ",".join(str(v) for v in frc)
                    os.environ["WHATSAPP_FREE_RESPONSE_CHATS"] = str(frc)

            # Matrix 设置 → 环境变量（环境变量优先）
            matrix_cfg = yaml_cfg.get("matrix", {})
            if isinstance(matrix_cfg, dict):
                if "require_mention" in matrix_cfg and not os.getenv("MATRIX_REQUIRE_MENTION"):
                    os.environ["MATRIX_REQUIRE_MENTION"] = str(matrix_cfg["require_mention"]).lower()
                frc = matrix_cfg.get("free_response_rooms")
                if frc is not None and not os.getenv("MATRIX_FREE_RESPONSE_ROOMS"):
                    if isinstance(frc, list):
                        frc = ",".join(str(v) for v in frc)
                    os.environ["MATRIX_FREE_RESPONSE_ROOMS"] = str(frc)
                if "auto_thread" in matrix_cfg and not os.getenv("MATRIX_AUTO_THREAD"):
                    os.environ["MATRIX_AUTO_THREAD"] = str(matrix_cfg["auto_thread"]).lower()
                if "dm_mention_threads" in matrix_cfg and not os.getenv("MATRIX_DM_MENTION_THREADS"):
                    os.environ["MATRIX_DM_MENTION_THREADS"] = str(matrix_cfg["dm_mention_threads"]).lower()

    except Exception as e:
        logger.warning(
            "Failed to process config.yaml — falling back to .env / gateway.json values. "
            "Check %s for syntax errors. Error: %s",
            _home / "config.yaml",
            e,
        )

    config = GatewayConfig.from_dict(gw_data)

    # 使用环境变量覆盖配置
    _apply_env_overrides(config)
    
    # --- 校验已加载的配置值 ---
    _validate_gateway_config(config)

    return config


def _validate_gateway_config(config: "GatewayConfig") -> None:
    """校验并修正已加载的 GatewayConfig（就地修改）。

    在所有配置来源合并后由 ``load_gateway_config()`` 调用。
    单独提取为函数便于测试。
    """
    policy = config.default_reset_policy

    if not (0 <= policy.at_hour <= 23):
        logger.warning(
            "Invalid at_hour=%s (must be 0-23). Using default 4.", policy.at_hour
        )
        policy.at_hour = 4

    if policy.idle_minutes is None or policy.idle_minutes <= 0:
        logger.warning(
            "Invalid idle_minutes=%s (must be positive). Using default 1440.",
            policy.idle_minutes,
        )
        policy.idle_minutes = 1440

    # 警告空的机器人令牌 — 加载了空字符串的平台不会连接，
    # 没有日志提示会让原因难以排查。
    _token_env_names = {
        Platform.TELEGRAM: "TELEGRAM_BOT_TOKEN",
        Platform.DISCORD: "DISCORD_BOT_TOKEN",
        Platform.SLACK: "SLACK_BOT_TOKEN",
        Platform.MATTERMOST: "MATTERMOST_TOKEN",
        Platform.MATRIX: "MATRIX_ACCESS_TOKEN",
        Platform.WEIXIN: "WEIXIN_TOKEN",
    }
    for platform, pconfig in config.platforms.items():
        if not pconfig.enabled:
            continue
        env_name = _token_env_names.get(platform)
        if env_name and pconfig.token is not None and not pconfig.token.strip():
            logger.warning(
                "%s is enabled but %s is empty. "
                "The adapter will likely fail to connect.",
                platform.value, env_name,
            )

    # 拒绝已知的弱占位符令牌。
    # 移植自 openclaw/openclaw#64586：用户复制 .env.example 后未修改
    # 占位符值时，会收到清晰的启动错误，而不是来自平台 API 的
    # 令人困惑的"认证失败"消息。
    try:
        from hermes_cli.auth import has_usable_secret
    except ImportError:
        has_usable_secret = None  # type: ignore[assignment]

    if has_usable_secret is not None:
        for platform, pconfig in config.platforms.items():
            if not pconfig.enabled:
                continue
            env_name = _token_env_names.get(platform)
            if not env_name:
                continue
            token = pconfig.token
            if token and token.strip() and not has_usable_secret(token, min_length=4):
                logger.error(
                    "%s is enabled but %s is set to a placeholder value ('%s'). "
                    "Set a real bot token before starting the gateway. "
                    "The adapter will NOT be started.",
                    platform.value, env_name, token.strip()[:6] + "...",
                )
                pconfig.enabled = False


def _apply_env_overrides(config: GatewayConfig) -> None:
    """应用环境变量对配置的覆盖。"""
    
    # Telegram
    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
    if telegram_token:
        if Platform.TELEGRAM not in config.platforms:
            config.platforms[Platform.TELEGRAM] = PlatformConfig()
        config.platforms[Platform.TELEGRAM].enabled = True
        config.platforms[Platform.TELEGRAM].token = telegram_token
    
    # Telegram 回复线程模式（off/first/all）
    telegram_reply_mode = os.getenv("TELEGRAM_REPLY_TO_MODE", "").lower()
    if telegram_reply_mode in ("off", "first", "all"):
        if Platform.TELEGRAM not in config.platforms:
            config.platforms[Platform.TELEGRAM] = PlatformConfig()
        config.platforms[Platform.TELEGRAM].reply_to_mode = telegram_reply_mode
    
    telegram_fallback_ips = os.getenv("TELEGRAM_FALLBACK_IPS", "")
    if telegram_fallback_ips:
        if Platform.TELEGRAM not in config.platforms:
            config.platforms[Platform.TELEGRAM] = PlatformConfig()
        config.platforms[Platform.TELEGRAM].extra["fallback_ips"] = [
            ip.strip() for ip in telegram_fallback_ips.split(",") if ip.strip()
        ]

    telegram_home = os.getenv("TELEGRAM_HOME_CHANNEL")
    if telegram_home and Platform.TELEGRAM in config.platforms:
        config.platforms[Platform.TELEGRAM].home_channel = HomeChannel(
            platform=Platform.TELEGRAM,
            chat_id=telegram_home,
            name=os.getenv("TELEGRAM_HOME_CHANNEL_NAME", "Home"),
        )
    
    # Discord
    discord_token = os.getenv("DISCORD_BOT_TOKEN")
    if discord_token:
        if Platform.DISCORD not in config.platforms:
            config.platforms[Platform.DISCORD] = PlatformConfig()
        config.platforms[Platform.DISCORD].enabled = True
        config.platforms[Platform.DISCORD].token = discord_token
    
    discord_home = os.getenv("DISCORD_HOME_CHANNEL")
    if discord_home and Platform.DISCORD in config.platforms:
        config.platforms[Platform.DISCORD].home_channel = HomeChannel(
            platform=Platform.DISCORD,
            chat_id=discord_home,
            name=os.getenv("DISCORD_HOME_CHANNEL_NAME", "Home"),
        )
    
    # Discord 回复线程模式（off/first/all）
    discord_reply_mode = os.getenv("DISCORD_REPLY_TO_MODE", "").lower()
    if discord_reply_mode in ("off", "first", "all"):
        if Platform.DISCORD not in config.platforms:
            config.platforms[Platform.DISCORD] = PlatformConfig()
        config.platforms[Platform.DISCORD].reply_to_mode = discord_reply_mode
    
    # WhatsApp（通常使用不同的认证机制）
    whatsapp_enabled = os.getenv("WHATSAPP_ENABLED", "").lower() in ("true", "1", "yes")
    if whatsapp_enabled:
        if Platform.WHATSAPP not in config.platforms:
            config.platforms[Platform.WHATSAPP] = PlatformConfig()
        config.platforms[Platform.WHATSAPP].enabled = True
    
    # Slack
    slack_token = os.getenv("SLACK_BOT_TOKEN")
    if slack_token:
        if Platform.SLACK not in config.platforms:
            config.platforms[Platform.SLACK] = PlatformConfig()
        config.platforms[Platform.SLACK].enabled = True
        config.platforms[Platform.SLACK].token = slack_token
    slack_home = os.getenv("SLACK_HOME_CHANNEL")
    if slack_home and Platform.SLACK in config.platforms:
        config.platforms[Platform.SLACK].home_channel = HomeChannel(
            platform=Platform.SLACK,
            chat_id=slack_home,
            name=os.getenv("SLACK_HOME_CHANNEL_NAME", ""),
        )
    
    # Signal
    signal_url = os.getenv("SIGNAL_HTTP_URL")
    signal_account = os.getenv("SIGNAL_ACCOUNT")
    if signal_url and signal_account:
        if Platform.SIGNAL not in config.platforms:
            config.platforms[Platform.SIGNAL] = PlatformConfig()
        config.platforms[Platform.SIGNAL].enabled = True
        config.platforms[Platform.SIGNAL].extra.update({
            "http_url": signal_url,
            "account": signal_account,
            "ignore_stories": os.getenv("SIGNAL_IGNORE_STORIES", "true").lower() in ("true", "1", "yes"),
        })
    signal_home = os.getenv("SIGNAL_HOME_CHANNEL")
    if signal_home and Platform.SIGNAL in config.platforms:
        config.platforms[Platform.SIGNAL].home_channel = HomeChannel(
            platform=Platform.SIGNAL,
            chat_id=signal_home,
            name=os.getenv("SIGNAL_HOME_CHANNEL_NAME", "Home"),
        )

    # Mattermost
    mattermost_token = os.getenv("MATTERMOST_TOKEN")
    if mattermost_token:
        mattermost_url = os.getenv("MATTERMOST_URL", "")
        if not mattermost_url:
            logger.warning("MATTERMOST_TOKEN set but MATTERMOST_URL is missing")
        if Platform.MATTERMOST not in config.platforms:
            config.platforms[Platform.MATTERMOST] = PlatformConfig()
        config.platforms[Platform.MATTERMOST].enabled = True
        config.platforms[Platform.MATTERMOST].token = mattermost_token
        config.platforms[Platform.MATTERMOST].extra["url"] = mattermost_url
    mattermost_home = os.getenv("MATTERMOST_HOME_CHANNEL")
    if mattermost_home and Platform.MATTERMOST in config.platforms:
        config.platforms[Platform.MATTERMOST].home_channel = HomeChannel(
            platform=Platform.MATTERMOST,
            chat_id=mattermost_home,
            name=os.getenv("MATTERMOST_HOME_CHANNEL_NAME", "Home"),
        )

    # Matrix
    matrix_token = os.getenv("MATRIX_ACCESS_TOKEN")
    matrix_homeserver = os.getenv("MATRIX_HOMESERVER", "")
    if matrix_token or os.getenv("MATRIX_PASSWORD"):
        if not matrix_homeserver:
            logger.warning("MATRIX_ACCESS_TOKEN/MATRIX_PASSWORD set but MATRIX_HOMESERVER is missing")
        if Platform.MATRIX not in config.platforms:
            config.platforms[Platform.MATRIX] = PlatformConfig()
        config.platforms[Platform.MATRIX].enabled = True
        if matrix_token:
            config.platforms[Platform.MATRIX].token = matrix_token
        config.platforms[Platform.MATRIX].extra["homeserver"] = matrix_homeserver
        matrix_user = os.getenv("MATRIX_USER_ID", "")
        if matrix_user:
            config.platforms[Platform.MATRIX].extra["user_id"] = matrix_user
        matrix_password = os.getenv("MATRIX_PASSWORD", "")
        if matrix_password:
            config.platforms[Platform.MATRIX].extra["password"] = matrix_password
        matrix_e2ee = os.getenv("MATRIX_ENCRYPTION", "").lower() in ("true", "1", "yes")
        config.platforms[Platform.MATRIX].extra["encryption"] = matrix_e2ee
        matrix_device_id = os.getenv("MATRIX_DEVICE_ID", "")
        if matrix_device_id:
            config.platforms[Platform.MATRIX].extra["device_id"] = matrix_device_id
    matrix_home = os.getenv("MATRIX_HOME_ROOM")
    if matrix_home and Platform.MATRIX in config.platforms:
        config.platforms[Platform.MATRIX].home_channel = HomeChannel(
            platform=Platform.MATRIX,
            chat_id=matrix_home,
            name=os.getenv("MATRIX_HOME_ROOM_NAME", "Home"),
        )

    # Home Assistant
    hass_token = os.getenv("HASS_TOKEN")
    if hass_token:
        if Platform.HOMEASSISTANT not in config.platforms:
            config.platforms[Platform.HOMEASSISTANT] = PlatformConfig()
        config.platforms[Platform.HOMEASSISTANT].enabled = True
        config.platforms[Platform.HOMEASSISTANT].token = hass_token
        hass_url = os.getenv("HASS_URL")
        if hass_url:
            config.platforms[Platform.HOMEASSISTANT].extra["url"] = hass_url

    # Email
    email_addr = os.getenv("EMAIL_ADDRESS")
    email_pwd = os.getenv("EMAIL_PASSWORD")
    email_imap = os.getenv("EMAIL_IMAP_HOST")
    email_smtp = os.getenv("EMAIL_SMTP_HOST")
    if all([email_addr, email_pwd, email_imap, email_smtp]):
        if Platform.EMAIL not in config.platforms:
            config.platforms[Platform.EMAIL] = PlatformConfig()
        config.platforms[Platform.EMAIL].enabled = True
        config.platforms[Platform.EMAIL].extra.update({
            "address": email_addr,
            "imap_host": email_imap,
            "smtp_host": email_smtp,
        })
    email_home = os.getenv("EMAIL_HOME_ADDRESS")
    if email_home and Platform.EMAIL in config.platforms:
        config.platforms[Platform.EMAIL].home_channel = HomeChannel(
            platform=Platform.EMAIL,
            chat_id=email_home,
            name=os.getenv("EMAIL_HOME_ADDRESS_NAME", "Home"),
        )

    # SMS（Twilio 短信服务）
    twilio_sid = os.getenv("TWILIO_ACCOUNT_SID")
    if twilio_sid:
        if Platform.SMS not in config.platforms:
            config.platforms[Platform.SMS] = PlatformConfig()
        config.platforms[Platform.SMS].enabled = True
        config.platforms[Platform.SMS].api_key = os.getenv("TWILIO_AUTH_TOKEN", "")
    sms_home = os.getenv("SMS_HOME_CHANNEL")
    if sms_home and Platform.SMS in config.platforms:
        config.platforms[Platform.SMS].home_channel = HomeChannel(
            platform=Platform.SMS,
            chat_id=sms_home,
            name=os.getenv("SMS_HOME_CHANNEL_NAME", "Home"),
        )

    # API Server
    api_server_enabled = os.getenv("API_SERVER_ENABLED", "").lower() in ("true", "1", "yes")
    api_server_key = os.getenv("API_SERVER_KEY", "")
    api_server_cors_origins = os.getenv("API_SERVER_CORS_ORIGINS", "")
    api_server_port = os.getenv("API_SERVER_PORT")
    api_server_host = os.getenv("API_SERVER_HOST")
    if api_server_enabled or api_server_key:
        if Platform.API_SERVER not in config.platforms:
            config.platforms[Platform.API_SERVER] = PlatformConfig()
        config.platforms[Platform.API_SERVER].enabled = True
        if api_server_key:
            config.platforms[Platform.API_SERVER].extra["key"] = api_server_key
        if api_server_cors_origins:
            origins = [origin.strip() for origin in api_server_cors_origins.split(",") if origin.strip()]
            if origins:
                config.platforms[Platform.API_SERVER].extra["cors_origins"] = origins
        if api_server_port:
            try:
                config.platforms[Platform.API_SERVER].extra["port"] = int(api_server_port)
            except ValueError:
                pass
        if api_server_host:
            config.platforms[Platform.API_SERVER].extra["host"] = api_server_host
        api_server_model_name = os.getenv("API_SERVER_MODEL_NAME", "")
        if api_server_model_name:
            config.platforms[Platform.API_SERVER].extra["model_name"] = api_server_model_name

    # Webhook 平台
    webhook_enabled = os.getenv("WEBHOOK_ENABLED", "").lower() in ("true", "1", "yes")
    webhook_port = os.getenv("WEBHOOK_PORT")
    webhook_secret = os.getenv("WEBHOOK_SECRET", "")
    if webhook_enabled:
        if Platform.WEBHOOK not in config.platforms:
            config.platforms[Platform.WEBHOOK] = PlatformConfig()
        config.platforms[Platform.WEBHOOK].enabled = True
        if webhook_port:
            try:
                config.platforms[Platform.WEBHOOK].extra["port"] = int(webhook_port)
            except ValueError:
                pass
        if webhook_secret:
            config.platforms[Platform.WEBHOOK].extra["secret"] = webhook_secret

    # 飞书 / Lark
    feishu_app_id = os.getenv("FEISHU_APP_ID")
    feishu_app_secret = os.getenv("FEISHU_APP_SECRET")
    if feishu_app_id and feishu_app_secret:
        if Platform.FEISHU not in config.platforms:
            config.platforms[Platform.FEISHU] = PlatformConfig()
        config.platforms[Platform.FEISHU].enabled = True
        config.platforms[Platform.FEISHU].extra.update({
            "app_id": feishu_app_id,
            "app_secret": feishu_app_secret,
            "domain": os.getenv("FEISHU_DOMAIN", "feishu"),
            "connection_mode": os.getenv("FEISHU_CONNECTION_MODE", "websocket"),
        })
        feishu_encrypt_key = os.getenv("FEISHU_ENCRYPT_KEY", "")
        if feishu_encrypt_key:
            config.platforms[Platform.FEISHU].extra["encrypt_key"] = feishu_encrypt_key
        feishu_verification_token = os.getenv("FEISHU_VERIFICATION_TOKEN", "")
        if feishu_verification_token:
            config.platforms[Platform.FEISHU].extra["verification_token"] = feishu_verification_token
        feishu_home = os.getenv("FEISHU_HOME_CHANNEL")
        if feishu_home:
            config.platforms[Platform.FEISHU].home_channel = HomeChannel(
                platform=Platform.FEISHU,
                chat_id=feishu_home,
                name=os.getenv("FEISHU_HOME_CHANNEL_NAME", "Home"),
            )

    # 企业微信（Enterprise WeChat）
    wecom_bot_id = os.getenv("WECOM_BOT_ID")
    wecom_secret = os.getenv("WECOM_SECRET")
    if wecom_bot_id and wecom_secret:
        if Platform.WECOM not in config.platforms:
            config.platforms[Platform.WECOM] = PlatformConfig()
        config.platforms[Platform.WECOM].enabled = True
        config.platforms[Platform.WECOM].extra.update({
            "bot_id": wecom_bot_id,
            "secret": wecom_secret,
        })
        wecom_ws_url = os.getenv("WECOM_WEBSOCKET_URL", "")
        if wecom_ws_url:
            config.platforms[Platform.WECOM].extra["websocket_url"] = wecom_ws_url
        wecom_home = os.getenv("WECOM_HOME_CHANNEL")
        if wecom_home:
            config.platforms[Platform.WECOM].home_channel = HomeChannel(
                platform=Platform.WECOM,
                chat_id=wecom_home,
                name=os.getenv("WECOM_HOME_CHANNEL_NAME", "Home"),
            )

    # 企业微信回调模式（自建应用）
    wecom_callback_corp_id = os.getenv("WECOM_CALLBACK_CORP_ID")
    wecom_callback_corp_secret = os.getenv("WECOM_CALLBACK_CORP_SECRET")
    if wecom_callback_corp_id and wecom_callback_corp_secret:
        if Platform.WECOM_CALLBACK not in config.platforms:
            config.platforms[Platform.WECOM_CALLBACK] = PlatformConfig()
        config.platforms[Platform.WECOM_CALLBACK].enabled = True
        config.platforms[Platform.WECOM_CALLBACK].extra.update({
            "corp_id": wecom_callback_corp_id,
            "corp_secret": wecom_callback_corp_secret,
            "agent_id": os.getenv("WECOM_CALLBACK_AGENT_ID", ""),
            "token": os.getenv("WECOM_CALLBACK_TOKEN", ""),
            "encoding_aes_key": os.getenv("WECOM_CALLBACK_ENCODING_AES_KEY", ""),
            "host": os.getenv("WECOM_CALLBACK_HOST", "0.0.0.0"),
            "port": int(os.getenv("WECOM_CALLBACK_PORT", "8645")),
        })

    # 个人微信（通过 iLink Bot API）
    weixin_token = os.getenv("WEIXIN_TOKEN")
    weixin_account_id = os.getenv("WEIXIN_ACCOUNT_ID")
    if weixin_token or weixin_account_id:
        if Platform.WEIXIN not in config.platforms:
            config.platforms[Platform.WEIXIN] = PlatformConfig()
        config.platforms[Platform.WEIXIN].enabled = True
        if weixin_token:
            config.platforms[Platform.WEIXIN].token = weixin_token
        extra = config.platforms[Platform.WEIXIN].extra
        if weixin_account_id:
            extra["account_id"] = weixin_account_id
        weixin_base_url = os.getenv("WEIXIN_BASE_URL", "").strip()
        if weixin_base_url:
            extra["base_url"] = weixin_base_url.rstrip("/")
        weixin_cdn_base_url = os.getenv("WEIXIN_CDN_BASE_URL", "").strip()
        if weixin_cdn_base_url:
            extra["cdn_base_url"] = weixin_cdn_base_url.rstrip("/")
        weixin_dm_policy = os.getenv("WEIXIN_DM_POLICY", "").strip().lower()
        if weixin_dm_policy:
            extra["dm_policy"] = weixin_dm_policy
        weixin_group_policy = os.getenv("WEIXIN_GROUP_POLICY", "").strip().lower()
        if weixin_group_policy:
            extra["group_policy"] = weixin_group_policy
        weixin_allowed_users = os.getenv("WEIXIN_ALLOWED_USERS", "").strip()
        if weixin_allowed_users:
            extra["allow_from"] = weixin_allowed_users
        weixin_group_allowed_users = os.getenv("WEIXIN_GROUP_ALLOWED_USERS", "").strip()
        if weixin_group_allowed_users:
            extra["group_allow_from"] = weixin_group_allowed_users
        weixin_split_multiline = os.getenv("WEIXIN_SPLIT_MULTILINE_MESSAGES", "").strip()
        if weixin_split_multiline:
            extra["split_multiline_messages"] = weixin_split_multiline
        weixin_home = os.getenv("WEIXIN_HOME_CHANNEL", "").strip()
        if weixin_home:
            config.platforms[Platform.WEIXIN].home_channel = HomeChannel(
                platform=Platform.WEIXIN,
                chat_id=weixin_home,
                name=os.getenv("WEIXIN_HOME_CHANNEL_NAME", "Home"),
            )

    # BlueBubbles (iMessage 桥接)
    bluebubbles_server_url = os.getenv("BLUEBUBBLES_SERVER_URL")
    bluebubbles_password = os.getenv("BLUEBUBBLES_PASSWORD")
    if bluebubbles_server_url and bluebubbles_password:
        if Platform.BLUEBUBBLES not in config.platforms:
            config.platforms[Platform.BLUEBUBBLES] = PlatformConfig()
        config.platforms[Platform.BLUEBUBBLES].enabled = True
        config.platforms[Platform.BLUEBUBBLES].extra.update({
            "server_url": bluebubbles_server_url.rstrip("/"),
            "password": bluebubbles_password,
            "webhook_host": os.getenv("BLUEBUBBLES_WEBHOOK_HOST", "127.0.0.1"),
            "webhook_port": int(os.getenv("BLUEBUBBLES_WEBHOOK_PORT", "8645")),
            "webhook_path": os.getenv("BLUEBUBBLES_WEBHOOK_PATH", "/bluebubbles-webhook"),
            "send_read_receipts": os.getenv("BLUEBUBBLES_SEND_READ_RECEIPTS", "true").lower() in ("true", "1", "yes"),
        })
    bluebubbles_home = os.getenv("BLUEBUBBLES_HOME_CHANNEL")
    if bluebubbles_home and Platform.BLUEBUBBLES in config.platforms:
        config.platforms[Platform.BLUEBUBBLES].home_channel = HomeChannel(
            platform=Platform.BLUEBUBBLES,
            chat_id=bluebubbles_home,
            name=os.getenv("BLUEBUBBLES_HOME_CHANNEL_NAME", "Home"),
        )

    # QQ（官方机器人 API v2）
    qq_app_id = os.getenv("QQ_APP_ID")
    qq_client_secret = os.getenv("QQ_CLIENT_SECRET")
    if qq_app_id or qq_client_secret:
        if Platform.QQBOT not in config.platforms:
            config.platforms[Platform.QQBOT] = PlatformConfig()
        config.platforms[Platform.QQBOT].enabled = True
        extra = config.platforms[Platform.QQBOT].extra
        if qq_app_id:
            extra["app_id"] = qq_app_id
        if qq_client_secret:
            extra["client_secret"] = qq_client_secret
        qq_allowed_users = os.getenv("QQ_ALLOWED_USERS", "").strip()
        if qq_allowed_users:
            extra["allow_from"] = qq_allowed_users
        qq_group_allowed = os.getenv("QQ_GROUP_ALLOWED_USERS", "").strip()
        if qq_group_allowed:
            extra["group_allow_from"] = qq_group_allowed
        qq_home = os.getenv("QQ_HOME_CHANNEL", "").strip()
        if qq_home:
            config.platforms[Platform.QQBOT].home_channel = HomeChannel(
                platform=Platform.QQBOT,
                chat_id=qq_home,
                name=os.getenv("QQ_HOME_CHANNEL_NAME", "Home"),
            )

    # 会话设置
    idle_minutes = os.getenv("SESSION_IDLE_MINUTES")
    if idle_minutes:
        try:
            config.default_reset_policy.idle_minutes = int(idle_minutes)
        except ValueError:
            pass
    
    reset_hour = os.getenv("SESSION_RESET_HOUR")
    if reset_hour:
        try:
            config.default_reset_policy.at_hour = int(reset_hour)
        except ValueError:
            pass
