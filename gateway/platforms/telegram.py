"""
Telegram 平台适配器。

使用 python-telegram-bot 库实现：
- 从用户/群组接收消息
- 发送回复
- 处理媒体和命令
"""

import asyncio
import json
import logging
import os
import html as _html
import re
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)

try:
    from telegram import Update, Bot, Message, InlineKeyboardButton, InlineKeyboardMarkup
    try:
        from telegram import LinkPreviewOptions
    except ImportError:
        LinkPreviewOptions = None
    from telegram.ext import (
        Application,
        CommandHandler,
        CallbackQueryHandler,
        MessageHandler as TelegramMessageHandler,
        ContextTypes,
        filters,
    )
    from telegram.constants import ParseMode, ChatType
    from telegram.request import HTTPXRequest
    TELEGRAM_AVAILABLE = True
except ImportError:
    TELEGRAM_AVAILABLE = False
    Update = Any
    Bot = Any
    Message = Any
    InlineKeyboardButton = Any
    InlineKeyboardMarkup = Any
    LinkPreviewOptions = None
    Application = Any
    CommandHandler = Any
    CallbackQueryHandler = Any
    TelegramMessageHandler = Any
    HTTPXRequest = Any
    filters = None
    ParseMode = None
    ChatType = None

    # 模拟 ContextTypes 以便在库未安装时，使用 ContextTypes.DEFAULT_TYPE 的
    # 类型注解在类定义期间不会崩溃。
    class _MockContextTypes:
        DEFAULT_TYPE = Any
    ContextTypes = _MockContextTypes

import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import (
    BasePlatformAdapter,
    MessageEvent,
    MessageType,
    ProcessingOutcome,
    SendResult,
    cache_image_from_bytes,
    cache_audio_from_bytes,
    cache_document_from_bytes,
    resolve_proxy_url,
    SUPPORTED_DOCUMENT_TYPES,
    utf16_len,
    _prefix_within_utf16_limit,
)
from gateway.platforms.telegram_network import (
    TelegramFallbackTransport,
    discover_fallback_ips,
    parse_fallback_ip_env,
)


def check_telegram_requirements() -> bool:
    """检查 Telegram 依赖项是否可用。"""
    return TELEGRAM_AVAILABLE


# 匹配 MarkdownV2 要求在代码段或围栏代码块外部需要反斜杠转义的每个字符。
_MDV2_ESCAPE_RE = re.compile(r'([_*\[\]()~`>#\+\-=|{}.!\\])')


def _escape_mdv2(text: str) -> str:
    """用前置反斜杠转义 Telegram MarkdownV2 特殊字符。"""
    return _MDV2_ESCAPE_RE.sub(r'\\\1', text)


def _strip_mdv2(text: str) -> str:
    """去除 MarkdownV2 转义反斜杠以生成干净的纯文本。

    同时移除 MarkdownV2 格式化标记，使回退时不会显示
    format_message 转换产生的杂散语法字符。
    """
    # 移除特殊字符前的转义反斜杠
    cleaned = re.sub(r'\\([_*\[\]()~`>#\+\-=|{}.!\\])', r'\1', text)
    # 移除 format_message 从 **bold** 转换来的 MarkdownV2 粗体标记
    cleaned = re.sub(r'\*([^*]+)\*', r'\1', cleaned)
    # 移除 format_message 从 *italic* 转换来的 MarkdownV2 斜体标记
    # 使用单词边界 (\b) 避免破坏 snake_case 如 my_variable_name
    cleaned = re.sub(r'(?<!\w)_([^_]+)_(?!\w)', r'\1', cleaned)
    # 移除 MarkdownV2 删除线标记 (~text~ → text)
    cleaned = re.sub(r'~([^~]+)~', r'\1', cleaned)
    # 移除 MarkdownV2 遮挡标记 (||text|| → text)
    cleaned = re.sub(r'\|\|([^|]+)\|\|', r'\1', cleaned)
    return cleaned


class TelegramAdapter(BasePlatformAdapter):
    """
    Telegram 机器人适配器。

    功能：
    - 从用户和群组接收消息
    - 使用 Telegram markdown 发送回复
    - 论坛话题（thread_id 支持）
    - 媒体消息
    """
    
    # Telegram 消息长度限制
    MAX_MESSAGE_LENGTH = 4096
    # 检测 Telegram 客户端消息拆分的阈值。
    # 当分段接近此限制时，几乎可以确定有后续分段。
    _SPLIT_THRESHOLD = 4000
    MEDIA_GROUP_WAIT_SECONDS = 0.8
    _GENERAL_TOPIC_THREAD_ID = "1"
    
    def __init__(self, config: PlatformConfig):
        super().__init__(config, Platform.TELEGRAM)
        self._app: Optional[Application] = None
        self._bot: Optional[Bot] = None
        self._webhook_mode: bool = False
        self._mention_patterns = self._compile_mention_patterns()
        self._reply_to_mode: str = getattr(config, 'reply_to_mode', 'first') or 'first'
        self._disable_link_previews: bool = self._coerce_bool_extra("disable_link_previews", False)
        # 缓冲快速/相册照片更新，使 Telegram 图片连发作为
        # 单个 MessageEvent 处理，而不是自我中断的多轮对话。
        self._media_batch_delay_seconds = float(os.getenv("HERMES_TELEGRAM_MEDIA_BATCH_DELAY_SECONDS", "0.8"))
        self._pending_photo_batches: Dict[str, MessageEvent] = {}
        self._pending_photo_batch_tasks: Dict[str, asyncio.Task] = {}
        self._media_group_events: Dict[str, MessageEvent] = {}
        self._media_group_tasks: Dict[str, asyncio.Task] = {}
        # 缓冲快速文本消息，使 Telegram 客户端拆分的长消息
        # 聚合为单个 MessageEvent。
        self._text_batch_delay_seconds = float(os.getenv("HERMES_TELEGRAM_TEXT_BATCH_DELAY_SECONDS", "0.6"))
        self._text_batch_split_delay_seconds = float(os.getenv("HERMES_TELEGRAM_TEXT_BATCH_SPLIT_DELAY_SECONDS", "2.0"))
        self._pending_text_batches: Dict[str, MessageEvent] = {}
        self._pending_text_batch_tasks: Dict[str, asyncio.Task] = {}
        self._polling_error_task: Optional[asyncio.Task] = None
        self._polling_conflict_count: int = 0
        self._polling_network_error_count: int = 0
        self._polling_error_callback_ref = None
        # DM 话题：topic_name -> message_thread_id 的映射（启动时填充）
        self._dm_topics: Dict[str, int] = {}
        # 来自 extra.dm_topics 的 DM 话题配置
        self._dm_topics_config: List[Dict[str, Any]] = self.config.extra.get("dm_topics", [])
        # 每个聊天的交互式模型选择器状态
        self._model_picker_state: Dict[str, dict] = {}
        # 审批按钮状态：message_id → session_key
        self._approval_state: Dict[int, str] = {}

    @staticmethod
    def _is_callback_user_authorized(user_id: str) -> bool:
        """返回 Telegram 内联按钮调用者是否可以执行受限操作。"""
        allowed_csv = os.getenv("TELEGRAM_ALLOWED_USERS", "").strip()
        if not allowed_csv:
            return True
        allowed_ids = {uid.strip() for uid in allowed_csv.split(",") if uid.strip()}
        return "*" in allowed_ids or user_id in allowed_ids

    @classmethod
    def _metadata_thread_id(cls, metadata: Optional[Dict[str, Any]]) -> Optional[str]:
        if not metadata:
            return None
        thread_id = metadata.get("thread_id") or metadata.get("message_thread_id")
        return str(thread_id) if thread_id is not None else None

    @classmethod
    def _message_thread_id_for_send(cls, thread_id: Optional[str]) -> Optional[int]:
        if not thread_id or str(thread_id) == cls._GENERAL_TOPIC_THREAD_ID:
            return None
        return int(thread_id)

    @classmethod
    def _message_thread_id_for_typing(cls, thread_id: Optional[str]) -> Optional[int]:
        if not thread_id:
            return None
        return int(thread_id)

    @staticmethod
    def _is_thread_not_found_error(error: Exception) -> bool:
        return "thread not found" in str(error).lower()

    def _fallback_ips(self) -> list[str]:
        """返回从配置中验证过的备用 IP（由 _apply_env_overrides 填充）。"""
        configured = self.config.extra.get("fallback_ips", []) if getattr(self.config, "extra", None) else []
        if isinstance(configured, str):
            configured = configured.split(",")
        return parse_fallback_ip_env(",".join(str(v) for v in configured) if configured else None)

    @staticmethod
    def _looks_like_polling_conflict(error: Exception) -> bool:
        text = str(error).lower()
        return (
            error.__class__.__name__.lower() == "conflict"
            or "terminated by other getupdates request" in text
            or "another bot instance is running" in text
        )

    @staticmethod
    def _looks_like_network_error(error: Exception) -> bool:
        """对需要重连尝试的瞬时网络错误返回 True。"""
        name = error.__class__.__name__.lower()
        if name in ("networkerror", "timedout", "connectionerror"):
            return True
        try:
            from telegram.error import NetworkError, TimedOut
            if isinstance(error, (NetworkError, TimedOut)):
                return True
        except ImportError:
            pass
        return isinstance(error, OSError)

    def _coerce_bool_extra(self, key: str, default: bool = False) -> bool:
        value = self.config.extra.get(key) if getattr(self.config, "extra", None) else None
        if value is None:
            return default
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in ("true", "1", "yes", "on"):
                return True
            if lowered in ("false", "0", "no", "off"):
                return False
            return default
        return bool(value)

    def _link_preview_kwargs(self) -> Dict[str, Any]:
        if not getattr(self, "_disable_link_previews", False):
            return {}
        if LinkPreviewOptions is not None:
            return {"link_preview_options": LinkPreviewOptions(is_disabled=True)}
        return {"disable_web_page_preview": True}

    async def _handle_polling_network_error(self, error: Exception) -> None:
        """在瞬时网络中断后重连轮询。

        由轮询错误回调中的 NetworkError/TimedOut 触发，
        当主机失去连接时发生（Mac 休眠、WiFi 切换、VPN
        重连等）。网关进程保持存活但长轮询连接静默断开；
        没有此处理器机器人将永远无法恢复。

        策略：指数退避（5秒、10秒、20秒、40秒、60秒上限）最多
        MAX_NETWORK_RETRIES 次尝试，然后将适配器标记为可重试致命错误，
        由监控进程重启网关。
        """
        if self.has_fatal_error:
            return

        MAX_NETWORK_RETRIES = 10
        BASE_DELAY = 5
        MAX_DELAY = 60

        self._polling_network_error_count += 1
        attempt = self._polling_network_error_count

        if attempt > MAX_NETWORK_RETRIES:
            message = (
                "Telegram polling could not reconnect after %d network error retries. "
                "Restarting gateway." % MAX_NETWORK_RETRIES
            )
            logger.error("[%s] %s Last error: %s", self.name, message, error)
            self._set_fatal_error("telegram_network_error", message, retryable=True)
            await self._notify_fatal_error()
            return

        delay = min(BASE_DELAY * (2 ** (attempt - 1)), MAX_DELAY)
        logger.warning(
            "[%s] Telegram network error (attempt %d/%d), reconnecting in %ds. Error: %s",
            self.name, attempt, MAX_NETWORK_RETRIES, delay, error,
        )
        await asyncio.sleep(delay)

        try:
            if self._app and self._app.updater and self._app.updater.running:
                await self._app.updater.stop()
        except Exception:
            pass

        try:
            await self._app.updater.start_polling(
                allowed_updates=Update.ALL_TYPES,
                drop_pending_updates=False,
                error_callback=self._polling_error_callback_ref,
            )
            logger.info(
                "[%s] Telegram polling resumed after network error (attempt %d)",
                self.name, attempt,
            )
            self._polling_network_error_count = 0
        except Exception as retry_err:
            logger.warning("[%s] Telegram polling reconnect failed: %s", self.name, retry_err)
            # start_polling 失败 — 轮询已死且不会再触发错误
            # 回调，因此我们自行调度下一次重试。
            if not self.has_fatal_error:
                task = asyncio.ensure_future(
                    self._handle_polling_network_error(retry_err)
                )
                self._background_tasks.add(task)
                task.add_done_callback(self._background_tasks.discard)

    async def _handle_polling_conflict(self, error: Exception) -> None:
        if self.has_fatal_error and self.fatal_error_code == "telegram_polling_conflict":
            return
        # 跟踪连续冲突 — 当前一个网关实例尚未完全释放其在
        # Telegram 服务器上的长轮询会话时会发生瞬时 409 错误（例如
        # --replace 交接或 systemd Restart=on-failure 重生期间）。
        # 在放弃之前重试几次，让旧会话有时间过期。
        self._polling_conflict_count += 1

        MAX_CONFLICT_RETRIES = 3
        RETRY_DELAY = 10  # seconds

        if self._polling_conflict_count <= MAX_CONFLICT_RETRIES:
            logger.warning(
                "[%s] Telegram polling conflict (%d/%d), will retry in %ds. Error: %s",
                self.name, self._polling_conflict_count, MAX_CONFLICT_RETRIES,
                RETRY_DELAY, error,
            )
            try:
                if self._app and self._app.updater and self._app.updater.running:
                    await self._app.updater.stop()
            except Exception:
                pass
            await asyncio.sleep(RETRY_DELAY)
            try:
                await self._app.updater.start_polling(
                    allowed_updates=Update.ALL_TYPES,
                    drop_pending_updates=False,
                    error_callback=self._polling_error_callback_ref,
                )
                logger.info("[%s] Telegram polling resumed after conflict retry %d", self.name, self._polling_conflict_count)
                self._polling_conflict_count = 0  # reset on success
                return
            except Exception as retry_err:
                logger.warning("[%s] Telegram polling retry failed: %s", self.name, retry_err)
                # 不要立即进入致命状态 — 等待下一次冲突
                # 触发另一次重试尝试（最多 MAX_CONFLICT_RETRIES 次）。
                return

        # 重试耗尽 — 致命错误
        message = (
            "Another process is already polling this Telegram bot token "
            "(possibly OpenClaw or another Hermes instance). "
            "Hermes stopped Telegram polling after %d retries. "
            "Only one poller can run per token — stop the other process "
            "and restart with 'hermes start'."
            % MAX_CONFLICT_RETRIES
        )
        logger.error("[%s] %s Original error: %s", self.name, message, error)
        self._set_fatal_error("telegram_polling_conflict", message, retryable=False)
        try:
            if self._app and self._app.updater:
                await self._app.updater.stop()
        except Exception as stop_error:
            logger.warning("[%s] Failed stopping Telegram polling after conflict: %s", self.name, stop_error, exc_info=True)
        await self._notify_fatal_error()

    async def _create_dm_topic(
        self,
        chat_id: int,
        name: str,
        icon_color: Optional[int] = None,
        icon_custom_emoji_id: Optional[str] = None,
    ) -> Optional[int]:
        """在私聊（DM）中创建论坛话题。

        使用 Bot API 9.4 的 createForumTopic，现在支持一对一聊天。
        成功时返回 message_thread_id，失败时返回 None。
        """
        if not self._bot:
            return None
        try:
            kwargs: Dict[str, Any] = {"chat_id": chat_id, "name": name}
            if icon_color is not None:
                kwargs["icon_color"] = icon_color
            if icon_custom_emoji_id:
                kwargs["icon_custom_emoji_id"] = icon_custom_emoji_id

            topic = await self._bot.create_forum_topic(**kwargs)
            thread_id = topic.message_thread_id
            logger.info(
                "[%s] Created DM topic '%s' in chat %s -> thread_id=%s",
                self.name, name, chat_id, thread_id,
            )
            return thread_id
        except Exception as e:
            error_text = str(e).lower()
            # 如果话题已存在，尝试通过 getForumTopicIconStickers 查找
            # 或者只记录并跳过 — Telegram 没有提供"列出话题"的 API
            if "topic_name_duplicate" in error_text or "already" in error_text:
                logger.info(
                    "[%s] DM topic '%s' already exists in chat %s (will be mapped from incoming messages)",
                    self.name, name, chat_id,
                )
            else:
                logger.warning(
                    "[%s] Failed to create DM topic '%s' in chat %s: %s",
                    self.name, name, chat_id, e,
                )
            return None

    def _persist_dm_topic_thread_id(self, chat_id: int, topic_name: str, thread_id: int) -> None:
        """将新创建的 thread_id 保存回 config.yaml 以便跨重启持久化。"""
        try:
            from hermes_constants import get_hermes_home
            config_path = get_hermes_home() / "config.yaml"
            if not config_path.exists():
                logger.warning("[%s] Config file not found at %s, cannot persist thread_id", self.name, config_path)
                return

            import yaml as _yaml
            with open(config_path, "r") as f:
                config = _yaml.safe_load(f) or {}

            # 导航到 platforms.telegram.extra.dm_topics
            dm_topics = (
                config.get("platforms", {})
                .get("telegram", {})
                .get("extra", {})
                .get("dm_topics", [])
            )
            if not dm_topics:
                return

            changed = False
            for chat_entry in dm_topics:
                if int(chat_entry.get("chat_id", 0)) != int(chat_id):
                    continue
                for t in chat_entry.get("topics", []):
                    if t.get("name") == topic_name and not t.get("thread_id"):
                        t["thread_id"] = thread_id
                        changed = True
                        break

            if changed:
                with open(config_path, "w") as f:
                    _yaml.dump(config, f, default_flow_style=False, sort_keys=False)
                logger.info(
                    "[%s] Persisted thread_id=%s for topic '%s' in config.yaml",
                    self.name, thread_id, topic_name,
                )
        except Exception as e:
            logger.warning("[%s] Failed to persist thread_id to config: %s", self.name, e, exc_info=True)

    async def _setup_dm_topics(self) -> None:
        """加载或创建指定聊天的已配置 DM 话题。

        读取 config.extra['dm_topics'] — 一个字典列表：
        [
            {
                "chat_id": 123456789,
                "topics": [
                    {"name": "General", "icon_color": 7322096, "thread_id": 100},
                    {"name": "Accessibility Auditor", "icon_color": 9367192, "skill": "accessibility-auditor"}
                ]
            }
        ]

        如果话题在配置中已有 thread_id（从之前的创建中持久化），
        则直接加载到缓存而不调用 createForumTopic。
        只有没有 thread_id 的话题才会通过 API 创建，然后将其
        thread_id 保存回 config.yaml 供未来重启使用。
        """
        if not self._dm_topics_config:
            return

        for chat_entry in self._dm_topics_config:
            chat_id = chat_entry.get("chat_id")
            topics = chat_entry.get("topics", [])
            if not chat_id or not topics:
                continue

            logger.info(
                "[%s] Setting up %d DM topic(s) for chat %s",
                self.name, len(topics), chat_id,
            )

            for topic_conf in topics:
                topic_name = topic_conf.get("name")
                if not topic_name:
                    continue

                cache_key = f"{chat_id}:{topic_name}"

                # 如果 thread_id 已持久化在配置中，只需加载到缓存
                existing_thread_id = topic_conf.get("thread_id")
                if existing_thread_id:
                    self._dm_topics[cache_key] = int(existing_thread_id)
                    logger.info(
                        "[%s] DM topic loaded from config: %s -> thread_id=%s",
                        self.name, cache_key, existing_thread_id,
                    )
                    continue

                # 没有持久化的 thread_id — 通过 API 创建话题
                icon_color = topic_conf.get("icon_color")
                icon_emoji = topic_conf.get("icon_custom_emoji_id")

                thread_id = await self._create_dm_topic(
                    chat_id=int(chat_id),
                    name=topic_name,
                    icon_color=icon_color,
                    icon_custom_emoji_id=icon_emoji,
                )

                if thread_id:
                    self._dm_topics[cache_key] = thread_id
                    logger.info(
                        "[%s] DM topic cached: %s -> thread_id=%s",
                        self.name, cache_key, thread_id,
                    )
                    # 将 thread_id 持久化到配置中，下次重启时不需要重新创建
                    self._persist_dm_topic_thread_id(int(chat_id), topic_name, thread_id)

    async def connect(self) -> bool:
        """通过轮询或 webhook 连接到 Telegram。

        默认使用长轮询（到 Telegram 的出站连接）。
        如果设置了 ``TELEGRAM_WEBHOOK_URL``，则启动 HTTP webhook 服务器。
        Webhook 模式适用于云部署（Fly.io、Railway），
        其中入站 HTTP 可以唤醒挂起的机器。

        Webhook 模式的环境变量::

            TELEGRAM_WEBHOOK_URL    公共 HTTPS URL（例如 https://app.fly.dev/telegram）
            TELEGRAM_WEBHOOK_PORT   本地监听端口（默认 8443）
            TELEGRAM_WEBHOOK_SECRET 更新验证的密钥令牌
        """
        if not TELEGRAM_AVAILABLE:
            logger.error(
                "[%s] python-telegram-bot not installed. Run: pip install python-telegram-bot",
                self.name,
            )
            return False
        
        if not self.config.token:
            logger.error("[%s] No bot token configured", self.name)
            return False
        
        try:
            if not self._acquire_platform_lock('telegram-bot-token', self.config.token, 'Telegram bot token'):
                return False

            # 构建应用
            builder = Application.builder().token(self.config.token)
            custom_base_url = self.config.extra.get("base_url")
            if custom_base_url:
                builder = builder.base_url(custom_base_url)
                builder = builder.base_file_url(
                    self.config.extra.get("base_file_url", custom_base_url)
                )
                logger.info(
                    "[%s] Using custom Telegram base_url: %s",
                    self.name, custom_base_url,
                )

            # PTB 默认值 (pool_timeout=1s) 在不稳定网络上过于激进，可能在
            # 重连/启动时触发 "Pool timeout: All connections in the connection pool are occupied"
            # 使用更安全的默认值并允许环境变量覆盖。
            def _env_int(name: str, default: int) -> int:
                try:
                    return int(os.getenv(name, str(default)))
                except (TypeError, ValueError):
                    return default

            def _env_float(name: str, default: float) -> float:
                try:
                    return float(os.getenv(name, str(default)))
                except (TypeError, ValueError):
                    return default

            request_kwargs = {
                "connection_pool_size": _env_int("HERMES_TELEGRAM_HTTP_POOL_SIZE", 512),
                "pool_timeout": _env_float("HERMES_TELEGRAM_HTTP_POOL_TIMEOUT", 8.0),
                "connect_timeout": _env_float("HERMES_TELEGRAM_HTTP_CONNECT_TIMEOUT", 10.0),
                "read_timeout": _env_float("HERMES_TELEGRAM_HTTP_READ_TIMEOUT", 20.0),
                "write_timeout": _env_float("HERMES_TELEGRAM_HTTP_WRITE_TIMEOUT", 20.0),
            }

            proxy_url = resolve_proxy_url("TELEGRAM_PROXY")
            disable_fallback = (os.getenv("HERMES_TELEGRAM_DISABLE_FALLBACK_IPS", "").strip().lower() in ("1", "true", "yes", "on"))
            fallback_ips = self._fallback_ips()
            if not fallback_ips:
                fallback_ips = await discover_fallback_ips()
                logger.info(
                    "[%s] Auto-discovered Telegram fallback IPs: %s",
                    self.name,
                    ", ".join(fallback_ips),
                )

            if fallback_ips and not proxy_url and not disable_fallback:
                logger.info(
                    "[%s] Telegram fallback IPs active: %s",
                    self.name,
                    ", ".join(fallback_ips),
                )
                # 保持请求/更新池分离以减少轮询重连 + bot API
                # 启动/delete_webhook 调用期间的竞争。
                request = HTTPXRequest(
                    **request_kwargs,
                    httpx_kwargs={"transport": TelegramFallbackTransport(fallback_ips)},
                )
                get_updates_request = HTTPXRequest(
                    **request_kwargs,
                    httpx_kwargs={"transport": TelegramFallbackTransport(fallback_ips)},
                )
            elif proxy_url:
                logger.info("[%s] Proxy detected; passing explicitly to HTTPXRequest: %s", self.name, proxy_url)
                request = HTTPXRequest(**request_kwargs, proxy=proxy_url)
                get_updates_request = HTTPXRequest(**request_kwargs, proxy=proxy_url)
            else:
                if disable_fallback:
                    logger.info("[%s] Telegram fallback-IP transport disabled via env", self.name)
                request = HTTPXRequest(**request_kwargs)
                get_updates_request = HTTPXRequest(**request_kwargs)

            builder = builder.request(request).get_updates_request(get_updates_request)
            self._app = builder.build()
            self._bot = self._app.bot
            
            # 注册消息处理器
            self._app.add_handler(TelegramMessageHandler(
                filters.TEXT & ~filters.COMMAND,
                self._handle_text_message
            ))
            self._app.add_handler(TelegramMessageHandler(
                filters.COMMAND,
                self._handle_command
            ))
            self._app.add_handler(TelegramMessageHandler(
                filters.LOCATION | getattr(filters, "VENUE", filters.LOCATION),
                self._handle_location_message
            ))
            self._app.add_handler(TelegramMessageHandler(
                filters.PHOTO | filters.VIDEO | filters.AUDIO | filters.VOICE | filters.Document.ALL | filters.Sticker.ALL,
                self._handle_media_message
            ))
            # 处理内联键盘按钮回调（更新提示）
            self._app.add_handler(CallbackQueryHandler(self._handle_callback_query))
            
            # 开始轮询 — 对瞬时 TLS 重置重试 initialize()
            try:
                from telegram.error import NetworkError, TimedOut
            except ImportError:
                NetworkError = TimedOut = OSError  # type: ignore[misc,assignment]
            _max_connect = 8
            for _attempt in range(_max_connect):
                try:
                    await self._app.initialize()
                    break
                except (NetworkError, TimedOut, OSError) as init_err:
                    if _attempt < _max_connect - 1:
                        wait = min(2 ** _attempt, 15)
                        logger.warning(
                            "[%s] Connect attempt %d/%d failed: %s — retrying in %ds",
                            self.name, _attempt + 1, _max_connect, init_err, wait,
                        )
                        await asyncio.sleep(wait)
                    else:
                        raise
            await self._app.start()

            # 决定使用 webhook 还是轮询模式
            webhook_url = os.getenv("TELEGRAM_WEBHOOK_URL", "").strip()

            if webhook_url:
                # ── Webhook 模式 ─────────────────────────────────────
                # Telegram 将更新推送到我们的 HTTP 端点。这使得
                # 云平台（Fly.io、Railway）可以在收到入站 HTTP 流量时
                # 自动唤醒挂起的机器。
                webhook_port = int(os.getenv("TELEGRAM_WEBHOOK_PORT", "8443"))
                webhook_secret = os.getenv("TELEGRAM_WEBHOOK_SECRET", "").strip() or None
                from urllib.parse import urlparse
                webhook_path = urlparse(webhook_url).path or "/telegram"

                await self._app.updater.start_webhook(
                    listen="0.0.0.0",
                    port=webhook_port,
                    url_path=webhook_path,
                    webhook_url=webhook_url,
                    secret_token=webhook_secret,
                    allowed_updates=Update.ALL_TYPES,
                    drop_pending_updates=True,
                )
                self._webhook_mode = True
                logger.info(
                    "[%s] Webhook server listening on 0.0.0.0:%d%s",
                    self.name, webhook_port, webhook_path,
                )
            else:
                # ── 轮询模式（默认）───────────────────────────
                # 先清除任何残留的 webhook 以防轮询继承之前的
                # webhook 注册并静默停止接收更新。
                delete_webhook = getattr(self._bot, "delete_webhook", None)
                if callable(delete_webhook):
                    await delete_webhook(drop_pending_updates=False)

                loop = asyncio.get_running_loop()

                def _polling_error_callback(error: Exception) -> None:
                    if self._polling_error_task and not self._polling_error_task.done():
                        return
                    if self._looks_like_polling_conflict(error):
                        self._polling_error_task = loop.create_task(self._handle_polling_conflict(error))
                    elif self._looks_like_network_error(error):
                        logger.warning("[%s] Telegram network error, scheduling reconnect: %s", self.name, error)
                        self._polling_error_task = loop.create_task(self._handle_polling_network_error(error))
                    else:
                        logger.error("[%s] Telegram polling error: %s", self.name, error, exc_info=True)

                # 存储引用以便在 _handle_polling_conflict 中重试时使用
                self._polling_error_callback_ref = _polling_error_callback

                await self._app.updater.start_polling(
                    allowed_updates=Update.ALL_TYPES,
                    drop_pending_updates=True,
                    error_callback=_polling_error_callback,
                )
            
            # 注册机器人命令，使 Telegram 在用户输入 / 时显示提示菜单
            # 列表来自中央 COMMAND_REGISTRY — 在那里添加新的
            # 网关命令会自动添加到 Telegram 菜单。
            try:
                from telegram import BotCommand
                from hermes_cli.commands import telegram_menu_commands
                # Telegram 允许最多 100 个命令但有未记录的
                # 载荷大小限制。技能描述在 telegram_menu_commands() 中
                # 被截断为 40 个字符以安全容纳 100 个命令。
                menu_commands, hidden_count = telegram_menu_commands(max_commands=100)
                await self._bot.set_my_commands([
                    BotCommand(name, desc) for name, desc in menu_commands
                ])
                if hidden_count:
                    logger.info(
                        "[%s] Telegram menu: %d commands registered, %d hidden (over 100 limit). Use /commands for full list.",
                        self.name, len(menu_commands), hidden_count,
                    )
            except Exception as e:
                logger.warning(
                    "[%s] Could not register Telegram command menu: %s",
                    self.name,
                    e,
                    exc_info=True,
                )
            
            self._mark_connected()
            mode = "webhook" if self._webhook_mode else "polling"
            logger.info("[%s] Connected to Telegram (%s mode)", self.name, mode)

            # 设置 DM 话题（Bot API 9.4 — 私聊话题）
            # 在连接建立后运行，这样机器人可以调用 createForumTopic。
            # 此处失败是非致命的 — 机器人没有话题也能正常工作。
            try:
                await self._setup_dm_topics()
            except Exception as topics_err:
                logger.warning(
                    "[%s] DM topics setup failed (non-fatal): %s",
                    self.name, topics_err, exc_info=True,
                )

            return True
            
        except Exception as e:
            self._release_platform_lock()
            message = f"Telegram startup failed: {e}"
            self._set_fatal_error("telegram_connect_error", message, retryable=True)
            logger.error("[%s] Failed to connect to Telegram: %s", self.name, e, exc_info=True)
            return False
    
    async def disconnect(self) -> None:
        """停止轮询/webhook，取消待处理的相册刷新，并断开连接。"""
        pending_media_group_tasks = list(self._media_group_tasks.values())
        for task in pending_media_group_tasks:
            task.cancel()
        if pending_media_group_tasks:
            await asyncio.gather(*pending_media_group_tasks, return_exceptions=True)
        self._media_group_tasks.clear()
        self._media_group_events.clear()

        if self._app:
            try:
                # 仅在更新器运行时停止它
                if self._app.updater and self._app.updater.running:
                    await self._app.updater.stop()
                if self._app.running:
                    await self._app.stop()
                await self._app.shutdown()
            except Exception as e:
                logger.warning("[%s] Error during Telegram disconnect: %s", self.name, e, exc_info=True)
        self._release_platform_lock()

        for task in self._pending_photo_batch_tasks.values():
            if task and not task.done():
                task.cancel()
        self._pending_photo_batch_tasks.clear()
        self._pending_photo_batches.clear()

        self._mark_disconnected()
        self._app = None
        self._bot = None
        logger.info("[%s] Disconnected from Telegram", self.name)

    def _should_thread_reply(self, reply_to: Optional[str], chunk_index: int) -> bool:
        """判断此消息分段是否应回复原始消息。

        Args:
            reply_to: 要回复的原始消息 ID
            chunk_index: 此分段的索引（0 = 第一个分段）

        Returns:
            如果此分段应回复原始消息则返回 True
        """
        if not reply_to:
            return False
        mode = self._reply_to_mode
        if mode == "off":
            return False
        elif mode == "all":
            return True
        else:  # "first" (default)
            return chunk_index == 0

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> SendResult:
        """向 Telegram 聊天发送消息。"""
        if not self._bot:
            return SendResult(success=False, error="Not connected")
        
        # 跳过仅含空白的文本以防止 Telegram 400 空文本错误。
        if not content or not content.strip():
            return SendResult(success=True, message_id=None)
        
        try:
            # 格式化并在需要时拆分消息
            formatted = self.format_message(content)
            chunks = self.truncate_message(
                formatted, self.MAX_MESSAGE_LENGTH, len_fn=utf16_len,
            )
            if len(chunks) > 1:
                # truncate_message 追加了原始 " (1/2)" 后缀。转义
                # MarkdownV2 特殊字符括号，避免 Telegram 拒绝分段
                # 并回退到纯文本。
                chunks = [
                    re.sub(r" \((\d+)/(\d+)\)$", r" \\(\1/\2\\)", chunk)
                    for chunk in chunks
                ]
            
            message_ids = []
            thread_id = self._metadata_thread_id(metadata)
            
            try:
                from telegram.error import NetworkError as _NetErr
            except ImportError:
                _NetErr = OSError  # type: ignore[misc,assignment]

            try:
                from telegram.error import BadRequest as _BadReq
            except ImportError:
                _BadReq = None  # type: ignore[assignment,misc]

            try:
                from telegram.error import TimedOut as _TimedOut
            except (ImportError, AttributeError):
                _TimedOut = None  # type: ignore[assignment,misc]

            for i, chunk in enumerate(chunks):
                should_thread = self._should_thread_reply(reply_to, i)
                reply_to_id = int(reply_to) if should_thread else None
                effective_thread_id = self._message_thread_id_for_send(thread_id)

                msg = None
                for _send_attempt in range(3):
                    try:
                        # 先尝试 Markdown，失败后回退到纯文本
                        try:
                            msg = await self._bot.send_message(
                                chat_id=int(chat_id),
                                text=chunk,
                                parse_mode=ParseMode.MARKDOWN_V2,
                                reply_to_message_id=reply_to_id,
                                message_thread_id=effective_thread_id,
                                **self._link_preview_kwargs(),
                            )
                        except Exception as md_error:
                            # Markdown 解析失败，尝试纯文本
                            if "parse" in str(md_error).lower() or "markdown" in str(md_error).lower():
                                logger.warning("[%s] MarkdownV2 parse failed, falling back to plain text: %s", self.name, md_error)
                                plain_chunk = _strip_mdv2(chunk)
                                msg = await self._bot.send_message(
                                    chat_id=int(chat_id),
                                    text=plain_chunk,
                                    parse_mode=None,
                                    reply_to_message_id=reply_to_id,
                                    message_thread_id=effective_thread_id,
                                    **self._link_preview_kwargs(),
                                )
                            else:
                                raise
                        break  # 成功
                    except _NetErr as send_err:
                        # BadRequest 是 python-telegram-bot 中 NetworkError 的子类，
                        # 但表示永久性错误（非瞬时网络问题）。
                        # 检测并处理特定情况，而不是盲目重试。
                        if _BadReq and isinstance(send_err, _BadReq):
                            if self._is_thread_not_found_error(send_err) and effective_thread_id is not None:
                                # 话题不存在 — 不带
                                # message_thread_id 重试以确保消息仍能
                                # 到达聊天。
                                logger.warning(
                                    "[%s] Thread %s not found, retrying without message_thread_id",
                                    self.name, effective_thread_id,
                                )
                                effective_thread_id = None
                                continue
                            err_lower = str(send_err).lower()
                            if "message to be replied not found" in err_lower and reply_to_id is not None:
                                # 原始消息在我们回复之前已被删除 —
                                # 清除回复目标并重试，确保回复仍能送达。
                                logger.warning(
                                    "[%s] Reply target deleted, retrying without reply_to: %s",
                                    self.name, send_err,
                                )
                                reply_to_id = None
                                continue
                            # 其他 BadRequest 错误是永久性的 — 不重试
                            raise
                        # TimedOut 也是 NetworkError 的子类，但表示请求
                        # 可能已到达服务器 — 重试有重复发送消息的风险。
                        if _TimedOut and isinstance(send_err, _TimedOut):
                            raise
                        if _send_attempt < 2:
                            wait = 2 ** _send_attempt
                            logger.warning("[%s] Network error on send (attempt %d/3), retrying in %ds: %s",
                                           self.name, _send_attempt + 1, wait, send_err)
                            await asyncio.sleep(wait)
                        else:
                            raise
                    except Exception as send_err:
                        retry_after = getattr(send_err, "retry_after", None)
                        if retry_after is not None or "retry after" in str(send_err).lower():
                            if _send_attempt < 2:
                                wait = float(retry_after) if retry_after is not None else 1.0
                                logger.warning(
                                    "[%s] Telegram flood control on send (attempt %d/3), retrying in %.1fs: %s",
                                    self.name,
                                    _send_attempt + 1,
                                    wait,
                                    send_err,
                                )
                                await asyncio.sleep(wait)
                                continue
                        raise
                message_ids.append(str(msg.message_id))
            
            return SendResult(
                success=True,
                message_id=message_ids[0] if message_ids else None,
                raw_response={"message_ids": message_ids}
            )
            
        except Exception as e:
            logger.error("[%s] Failed to send Telegram message: %s", self.name, e, exc_info=True)
            # TimedOut 意味着请求可能已到达 Telegram —
            # 标记为不可重试，使 _send_with_retry() 不会重新发送。
            _to = locals().get("_TimedOut")
            err_str = str(e).lower()
            is_timeout = (_to and isinstance(e, _to)) or "timed out" in err_str
            return SendResult(success=False, error=str(e), retryable=not is_timeout)

    async def edit_message(
        self,
        chat_id: str,
        message_id: str,
        content: str,
    ) -> SendResult:
        """编辑之前发送的 Telegram 消息。"""
        if not self._bot:
            return SendResult(success=False, error="Not connected")
        try:
            formatted = self.format_message(content)
            try:
                await self._bot.edit_message_text(
                    chat_id=int(chat_id),
                    message_id=int(message_id),
                    text=formatted,
                    parse_mode=ParseMode.MARKDOWN_V2,
                )
            except Exception as fmt_err:
                # "Message is not modified" 是空操作，不是错误
                if "not modified" in str(fmt_err).lower():
                    return SendResult(success=True, message_id=message_id)
                # 回退：不带 markdown 格式重试
                await self._bot.edit_message_text(
                    chat_id=int(chat_id),
                    message_id=int(message_id),
                    text=content,
                )
            return SendResult(success=True, message_id=message_id)
        except Exception as e:
            err_str = str(e).lower()
            # "Message is not modified" — 内容相同，视为成功
            if "not modified" in err_str:
                return SendResult(success=True, message_id=message_id)
            # 消息过长 — 内容超过 4096 字符（例如流式传输期间）。
            # 截断并成功返回，使流消费者可以将溢出部分拆分到
            # 新消息中，而不是崩溃。
            if "message_too_long" in err_str or "too long" in err_str:
                truncated = _prefix_within_utf16_limit(
                    content, self.MAX_MESSAGE_LENGTH - 20
                ) + "…"
                try:
                    await self._bot.edit_message_text(
                        chat_id=int(chat_id),
                        message_id=int(message_id),
                        text=truncated,
                    )
                except Exception:
                    pass  # 尽力截断
                return SendResult(success=True, message_id=message_id)
            # 流量控制 / RetryAfter — 短等待内联重试，
            # 长等待立即返回失败，使流式传输可以回退到
            # 正常的最终发送，而不是留下截断的部分内容。
            retry_after = getattr(e, "retry_after", None)
            if retry_after is not None or "retry after" in err_str:
                wait = retry_after if retry_after else 1.0
                logger.warning(
                    "[%s] Telegram flood control, waiting %.1fs",
                    self.name, wait,
                )
                if wait > 5.0:
                    return SendResult(success=False, error=f"flood_control:{wait}")
                await asyncio.sleep(wait)
                try:
                    await self._bot.edit_message_text(
                        chat_id=int(chat_id),
                        message_id=int(message_id),
                        text=content,
                    )
                    return SendResult(success=True, message_id=message_id)
                except Exception as retry_err:
                    logger.error(
                        "[%s] Edit retry failed after flood wait: %s",
                        self.name, retry_err,
                    )
                    return SendResult(success=False, error=str(retry_err))
            logger.error(
                "[%s] Failed to edit Telegram message %s: %s",
                self.name,
                message_id,
                e,
                exc_info=True,
            )
            return SendResult(success=False, error=str(e))

    async def send_update_prompt(
        self, chat_id: str, prompt: str, default: str = "",
        session_key: str = "",
    ) -> SendResult:
        """发送内联键盘更新提示（是/否按钮）。

        当 ``hermes update --gateway`` 需要用户输入（暂存恢复、
        配置迁移）时由网关 ``/update`` 监视器使用。
        """
        if not self._bot:
            return SendResult(success=False, error="Not connected")
        try:
            default_hint = f" (default: {default})" if default else ""
            text = f"⚕ *Update needs your input:*\n\n{prompt}{default_hint}"
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✓ Yes", callback_data="update_prompt:y"),
                    InlineKeyboardButton("✗ No", callback_data="update_prompt:n"),
                ]
            ])
            msg = await self._bot.send_message(
                chat_id=int(chat_id),
                text=text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=keyboard,
                **self._link_preview_kwargs(),
            )
            return SendResult(success=True, message_id=str(msg.message_id))
        except Exception as e:
            logger.warning("[%s] send_update_prompt failed: %s", self.name, e)
            return SendResult(success=False, error=str(e))

    async def send_exec_approval(
        self, chat_id: str, command: str, session_key: str,
        description: str = "dangerous command",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """发送带交互按钮的内联键盘审批提示。

        按钮调用 ``resolve_gateway_approval()`` 来解除等待中的
        代理线程 — 与文本 ``/approve`` 流程使用相同机制。
        """
        if not self._bot:
            return SendResult(success=False, error="Not connected")

        try:
            cmd_preview = command[:3800] + "..." if len(command) > 3800 else command
            text = (
                f"⚠️ <b>Command Approval Required</b>\n\n"
                f"<pre>{_html.escape(cmd_preview)}</pre>\n\n"
                f"Reason: {_html.escape(description)}"
            )

            # 解析回复的话题上下文
            thread_id = self._metadata_thread_id(metadata)

            # 我们将使用 message_id 作为 callback_data 的一部分来查找 session_key
            # 先发送占位符再更新 — 或使用计数器。
            # 更简单的方式：使用单调递增计数器生成短 ID。
            import itertools
            if not hasattr(self, "_approval_counter"):
                self._approval_counter = itertools.count(1)
            approval_id = next(self._approval_counter)

            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Allow Once", callback_data=f"ea:once:{approval_id}"),
                    InlineKeyboardButton("✅ Session", callback_data=f"ea:session:{approval_id}"),
                ],
                [
                    InlineKeyboardButton("✅ Always", callback_data=f"ea:always:{approval_id}"),
                    InlineKeyboardButton("❌ Deny", callback_data=f"ea:deny:{approval_id}"),
                ],
            ])

            kwargs: Dict[str, Any] = {
                "chat_id": int(chat_id),
                "text": text,
                "parse_mode": ParseMode.HTML,
                "reply_markup": keyboard,
                **self._link_preview_kwargs(),
            }
            message_thread_id = self._message_thread_id_for_send(thread_id)
            if message_thread_id is not None:
                kwargs["message_thread_id"] = message_thread_id

            msg = await self._bot.send_message(**kwargs)

            # 存储按 approval_id 索引的 session_key 供回调处理器使用
            self._approval_state[approval_id] = session_key

            return SendResult(success=True, message_id=str(msg.message_id))
        except Exception as e:
            logger.warning("[%s] send_exec_approval failed: %s", self.name, e)
            return SendResult(success=False, error=str(e))

    async def send_model_picker(
        self,
        chat_id: str,
        providers: list,
        current_model: str,
        current_provider: str,
        session_key: str,
        on_model_selected,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """发送交互式内联键盘模型选择器。

        两步下钻：提供者选择 → 模型选择。
        用户导航时原地编辑同一条消息。
        """
        if not self._bot:
            return SendResult(success=False, error="Not connected")

        try:
            from hermes_cli.providers import get_label
        except ImportError:
            def get_label(slug):
                return slug

        try:
            # 构建提供者按钮 — 每行 2 个
            buttons: list = []
            for p in providers:
                count = p.get("total_models", len(p.get("models", [])))
                label = f"{p['name']} ({count})"
                if p.get("is_current"):
                    label = f"✓ {label}"
                # 紧凑回调数据：mp:<slug>（最大 64 字节）
                buttons.append(
                    InlineKeyboardButton(label, callback_data=f"mp:{p['slug']}")
                )

            rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
            rows.append([InlineKeyboardButton("✗ Cancel", callback_data="mx")])
            keyboard = InlineKeyboardMarkup(rows)

            provider_label = get_label(current_provider)
            text = (
                f"⚙ *Model Configuration*\n\n"
                f"Current model: `{current_model or 'unknown'}`\n"
                f"Provider: {provider_label}\n\n"
                f"Select a provider:"
            )

            thread_id = metadata.get("thread_id") if metadata else None
            msg = await self._bot.send_message(
                chat_id=int(chat_id),
                text=text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=keyboard,
                message_thread_id=int(thread_id) if thread_id else None,
                **self._link_preview_kwargs(),
            )

            # 存储按 chat_id 索引的选择器状态
            self._model_picker_state[str(chat_id)] = {
                "msg_id": msg.message_id,
                "providers": providers,
                "session_key": session_key,
                "on_model_selected": on_model_selected,
                "current_model": current_model,
                "current_provider": current_provider,
            }

            return SendResult(success=True, message_id=str(msg.message_id))
        except Exception as e:
            logger.warning("[%s] send_model_picker failed: %s", self.name, e)
            return SendResult(success=False, error=str(e))

    _MODEL_PAGE_SIZE = 8

    def _build_model_keyboard(self, models: list, page: int) -> tuple:
        """构建分页模型按钮。返回 (keyboard, page_info_text)。"""
        page_size = self._MODEL_PAGE_SIZE
        total = len(models)
        total_pages = max(1, (total + page_size - 1) // page_size)
        page = max(0, min(page, total_pages - 1))

        start = page * page_size
        end = min(start + page_size, total)
        page_models = models[start:end]

        buttons: list = []
        for i, model_id in enumerate(page_models):
            abs_idx = start + i
            short = model_id.split("/")[-1] if "/" in model_id else model_id
            if len(short) > 38:
                short = short[:35] + "..."
            buttons.append(
                InlineKeyboardButton(short, callback_data=f"mm:{abs_idx}")
            )

        rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]

        # 分页行（如果需要）
        if total_pages > 1:
            nav: list = []
            if page > 0:
                nav.append(InlineKeyboardButton("◀ Prev", callback_data=f"mg:{page - 1}"))
            nav.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="mx:noop"))
            if page < total_pages - 1:
                nav.append(InlineKeyboardButton("Next ▶", callback_data=f"mg:{page + 1}"))
            rows.append(nav)

        rows.append([
            InlineKeyboardButton("◀ Back", callback_data="mb"),
            InlineKeyboardButton("✗ Cancel", callback_data="mx"),
        ])

        page_info = f" ({start + 1}–{end} of {total})" if total_pages > 1 else ""
        return InlineKeyboardMarkup(rows), page_info

    async def _handle_model_picker_callback(
        self, query, data: str, chat_id: str
    ) -> None:
        """处理模型选择器内联键盘回调 (mp:/mm:/mb:/mx:/mg:)。"""
        state = self._model_picker_state.get(chat_id)
        if not state:
            await query.answer(text="Picker expired — use /model again.")
            return

        try:
            from hermes_cli.providers import get_label
        except ImportError:
            def get_label(slug):
                return slug

        if data.startswith("mp:"):
            # --- 选择了提供者：显示模型按钮（第 0 页）---
            provider_slug = data[3:]
            provider = next(
                (p for p in state["providers"] if p["slug"] == provider_slug),
                None,
            )
            if not provider:
                await query.answer(text="Provider not found.")
                return

            models = provider.get("models", [])
            state["selected_provider"] = provider_slug
            state["selected_provider_name"] = provider.get("name", provider_slug)
            state["model_list"] = models
            state["model_page"] = 0

            keyboard, page_info = self._build_model_keyboard(models, 0)

            pname = provider.get("name", provider_slug)
            total = provider.get("total_models", len(models))
            shown = len(models)
            extra = f"\n_{total - shown} more available — type `/model <name>` directly_" if total > shown else ""

            await query.edit_message_text(
                text=(
                    f"⚙ *Model Configuration*\n\n"
                    f"Provider: *{pname}*{page_info}\n"
                    f"Select a model:{extra}"
                ),
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=keyboard,
            )
            await query.answer()

        elif data.startswith("mg:"):
            # --- 页面导航 ---
            try:
                page = int(data[3:])
            except ValueError:
                await query.answer(text="Invalid page.")
                return

            models = state.get("model_list", [])
            state["model_page"] = page

            keyboard, page_info = self._build_model_keyboard(models, page)

            pname = state.get("selected_provider_name", "")
            provider_slug = state.get("selected_provider", "")
            provider = next(
                (p for p in state["providers"] if p["slug"] == provider_slug),
                None,
            )
            total = provider.get("total_models", len(models)) if provider else len(models)
            shown = len(models)
            extra = f"\n_{total - shown} more available — type `/model <name>` directly_" if total > shown else ""

            await query.edit_message_text(
                text=(
                    f"⚙ *Model Configuration*\n\n"
                    f"Provider: *{pname}*{page_info}\n"
                    f"Select a model:{extra}"
                ),
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=keyboard,
            )
            await query.answer()

        elif data.startswith("mm:"):
            # --- 选择了模型：执行切换 ---
            try:
                idx = int(data[3:])
            except ValueError:
                await query.answer(text="Invalid selection.")
                return

            model_list = state.get("model_list", [])
            if idx < 0 or idx >= len(model_list):
                await query.answer(text="Invalid model index.")
                return

            model_id = model_list[idx]
            provider_slug = state.get("selected_provider", "")
            callback = state.get("on_model_selected")

            if not callback:
                await query.answer(text="Picker expired.")
                return

            try:
                result_text = await callback(chat_id, model_id, provider_slug)
            except Exception as exc:
                logger.error("Model picker switch failed: %s", exc)
                result_text = f"Error switching model: {exc}"

            # 编辑消息显示确认，移除按钮
            try:
                await query.edit_message_text(
                    text=result_text,
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=None,
                )
            except Exception:
                # Markdown 解析失败 — 以纯文本重试
                try:
                    await query.edit_message_text(
                        text=result_text,
                        parse_mode=None,
                        reply_markup=None,
                    )
                except Exception:
                    pass
            await query.answer(text="Model switched!")

            # 清理状态
            self._model_picker_state.pop(chat_id, None)

        elif data == "mb":
            # --- 返回提供者列表 ---
            buttons = []
            for p in state["providers"]:
                count = p.get("total_models", len(p.get("models", [])))
                label = f"{p['name']} ({count})"
                if p.get("is_current"):
                    label = f"✓ {label}"
                buttons.append(
                    InlineKeyboardButton(label, callback_data=f"mp:{p['slug']}")
                )

            rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
            rows.append([InlineKeyboardButton("✗ Cancel", callback_data="mx")])
            keyboard = InlineKeyboardMarkup(rows)

            try:
                provider_label = get_label(state["current_provider"])
            except Exception:
                provider_label = state["current_provider"]

            await query.edit_message_text(
                text=(
                    f"⚙ *Model Configuration*\n\n"
                    f"Current model: `{state['current_model'] or 'unknown'}`\n"
                    f"Provider: {provider_label}\n\n"
                    f"Select a provider:"
                ),
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=keyboard,
            )
            await query.answer()

        elif data == "mx":
            # --- 取消 ---
            self._model_picker_state.pop(chat_id, None)
            await query.edit_message_text(
                text="Model selection cancelled.",
                reply_markup=None,
            )
            await query.answer()

        else:
            # 捕获所有（例如页面计数器按钮 "mx:noop"）
            await query.answer()

    async def _handle_callback_query(
        self, update: "Update", context: "ContextTypes.DEFAULT_TYPE"
    ) -> None:
        """处理内联键盘按钮点击。"""
        query = update.callback_query
        if not query or not query.data:
            return
        data = query.data

        # --- 模型选择器回调 ---
        if data.startswith(("mp:", "mm:", "mb", "mx", "mg:")):
            chat_id = str(query.message.chat_id) if query.message else None
            if chat_id:
                await self._handle_model_picker_callback(query, data, chat_id)
            return

        # --- 命令执行审批回调 (ea:choice:id) ---
        if data.startswith("ea:"):
            parts = data.split(":", 2)
            if len(parts) == 3:
                choice = parts[1]  # once, session, always, deny
                try:
                    approval_id = int(parts[2])
                except (ValueError, IndexError):
                    await query.answer(text="Invalid approval data.")
                    return

                # 只有授权用户可以点击审批按钮。
                caller_id = str(getattr(query.from_user, "id", ""))
                if not self._is_callback_user_authorized(caller_id):
                    await query.answer(text="⛔ You are not authorized to approve commands.")
                    return

                session_key = self._approval_state.pop(approval_id, None)
                if not session_key:
                    await query.answer(text="This approval has already been resolved.")
                    return

                # 将选择映射为人类可读的标签
                label_map = {
                    "once": "✅ Approved once",
                    "session": "✅ Approved for session",
                    "always": "✅ Approved permanently",
                    "deny": "❌ Denied",
                }
                user_display = getattr(query.from_user, "first_name", "User")
                label = label_map.get(choice, "Resolved")

                await query.answer(text=label)

                # 编辑消息显示决定，移除按钮
                try:
                    await query.edit_message_text(
                        text=f"{label} by {user_display}",
                        parse_mode=ParseMode.MARKDOWN,
                        reply_markup=None,
                    )
                except Exception:
                    pass  # 编辑失败非致命

                # 解决审批 — 解除代理线程阻塞
                try:
                    from tools.approval import resolve_gateway_approval
                    count = resolve_gateway_approval(session_key, choice)
                    logger.info(
                        "Telegram button resolved %d approval(s) for session %s (choice=%s, user=%s)",
                        count, session_key, choice, user_display,
                    )
                except Exception as exc:
                    logger.error("Failed to resolve gateway approval from Telegram button: %s", exc)
            return

        # --- 更新提示回调 ---
        if not data.startswith("update_prompt:"):
            return
        answer = data.split(":", 1)[1]  # "y" or "n"
        caller_id = str(getattr(query.from_user, "id", ""))
        if not self._is_callback_user_authorized(caller_id):
            await query.answer(text="⛔ You are not authorized to answer update prompts.")
            return
        await query.answer(text=f"Sent '{answer}' to the update process.")
        # 编辑消息以显示选择并移除按钮
        label = "Yes" if answer == "y" else "No"
        try:
            await query.edit_message_text(
                text=f"⚕ Update prompt answered: *{label}*",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=None,
            )
        except Exception:
            pass  # non-fatal if edit fails
        # 写入响应文件
        try:
            from hermes_constants import get_hermes_home
            home = get_hermes_home()
            response_path = home / ".update_response"
            tmp = response_path.with_suffix(".tmp")
            tmp.write_text(answer)
            tmp.replace(response_path)
            logger.info("Telegram update prompt answered '%s' by user %s",
                        answer, getattr(query.from_user, "id", "unknown"))
        except Exception as exc:
            logger.error("Failed to write update response from callback: %s", exc)

    async def send_voice(
        self,
        chat_id: str,
        audio_path: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> SendResult:
        """以原生 Telegram 语音消息或音频文件发送音频。"""
        if not self._bot:
            return SendResult(success=False, error="Not connected")
        
        try:
            import os
            if not os.path.exists(audio_path):
                return SendResult(success=False, error=f"Audio file not found: {audio_path}")
            
            with open(audio_path, "rb") as audio_file:
                # .ogg 文件 -> 以语音发送（圆形可播放气泡）
                if audio_path.endswith((".ogg", ".opus")):
                    _voice_thread = self._metadata_thread_id(metadata)
                    msg = await self._bot.send_voice(
                        chat_id=int(chat_id),
                        voice=audio_file,
                        caption=caption[:1024] if caption else None,
                        reply_to_message_id=int(reply_to) if reply_to else None,
                        message_thread_id=self._message_thread_id_for_send(_voice_thread),
                    )
                else:
                    # .mp3 和其他格式 -> 以音频文件发送
                    _audio_thread = self._metadata_thread_id(metadata)
                    msg = await self._bot.send_audio(
                        chat_id=int(chat_id),
                        audio=audio_file,
                        caption=caption[:1024] if caption else None,
                        reply_to_message_id=int(reply_to) if reply_to else None,
                        message_thread_id=self._message_thread_id_for_send(_audio_thread),
                    )
            return SendResult(success=True, message_id=str(msg.message_id))
        except Exception as e:
            logger.error(
                "[%s] Failed to send Telegram voice/audio, falling back to base adapter: %s",
                self.name,
                e,
                exc_info=True,
            )
            return await super().send_voice(chat_id, audio_path, caption, reply_to)
    
    async def send_image_file(
        self,
        chat_id: str,
        image_path: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> SendResult:
        """以原生 Telegram 照片发送本地图片文件。"""
        if not self._bot:
            return SendResult(success=False, error="Not connected")

        try:
            import os
            if not os.path.exists(image_path):
                return SendResult(success=False, error=f"Image file not found: {image_path}")

            _thread = self._metadata_thread_id(metadata)
            with open(image_path, "rb") as image_file:
                msg = await self._bot.send_photo(
                    chat_id=int(chat_id),
                    photo=image_file,
                    caption=caption[:1024] if caption else None,
                    reply_to_message_id=int(reply_to) if reply_to else None,
                    message_thread_id=self._message_thread_id_for_send(_thread),
                )
            return SendResult(success=True, message_id=str(msg.message_id))
        except Exception as e:
            logger.error(
                "[%s] Failed to send Telegram local image, falling back to base adapter: %s",
                self.name,
                e,
                exc_info=True,
            )
            return await super().send_image_file(chat_id, image_path, caption, reply_to)

    async def send_document(
        self,
        chat_id: str,
        file_path: str,
        caption: Optional[str] = None,
        file_name: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> SendResult:
        """以原生 Telegram 文件附件发送文档/文件。"""
        if not self._bot:
            return SendResult(success=False, error="Not connected")

        try:
            if not os.path.exists(file_path):
                return SendResult(success=False, error=f"File not found: {file_path}")

            display_name = file_name or os.path.basename(file_path)
            _thread = self._metadata_thread_id(metadata)

            with open(file_path, "rb") as f:
                msg = await self._bot.send_document(
                    chat_id=int(chat_id),
                    document=f,
                    filename=display_name,
                    caption=caption[:1024] if caption else None,
                    reply_to_message_id=int(reply_to) if reply_to else None,
                    message_thread_id=self._message_thread_id_for_send(_thread),
                )
            return SendResult(success=True, message_id=str(msg.message_id))
        except Exception as e:
            print(f"[{self.name}] Failed to send document: {e}")
            return await super().send_document(chat_id, file_path, caption, file_name, reply_to)

    async def send_video(
        self,
        chat_id: str,
        video_path: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> SendResult:
        """以原生 Telegram 视频消息发送视频。"""
        if not self._bot:
            return SendResult(success=False, error="Not connected")

        try:
            if not os.path.exists(video_path):
                return SendResult(success=False, error=f"Video file not found: {video_path}")

            _thread = self._metadata_thread_id(metadata)
            with open(video_path, "rb") as f:
                msg = await self._bot.send_video(
                    chat_id=int(chat_id),
                    video=f,
                    caption=caption[:1024] if caption else None,
                    reply_to_message_id=int(reply_to) if reply_to else None,
                    message_thread_id=self._message_thread_id_for_send(_thread),
                )
            return SendResult(success=True, message_id=str(msg.message_id))
        except Exception as e:
            print(f"[{self.name}] Failed to send video: {e}")
            return await super().send_video(chat_id, video_path, caption, reply_to)

    async def send_image(
        self,
        chat_id: str,
        image_url: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """以原生 Telegram 照片发送图片。

        先尝试基于 URL 发送（快速，适用于 <5MB 图片）。
        回退到下载后作为文件上传（支持最大 10MB）。
        """
        if not self._bot:
            return SendResult(success=False, error="Not connected")

        from tools.url_safety import is_safe_url
        if not is_safe_url(image_url):
            logger.warning("[%s] Blocked unsafe image URL (SSRF protection)", self.name)
            return await super().send_image(chat_id, image_url, caption, reply_to, metadata=metadata)

        try:
            # Telegram 可以直接从 URL 发送照片（最大约 5MB）
            _photo_thread = self._metadata_thread_id(metadata)
            msg = await self._bot.send_photo(
                chat_id=int(chat_id),
                photo=image_url,
                caption=caption[:1024] if caption else None,  # Telegram caption limit
                reply_to_message_id=int(reply_to) if reply_to else None,
                message_thread_id=self._message_thread_id_for_send(_photo_thread),
            )
            return SendResult(success=True, message_id=str(msg.message_id))
        except Exception as e:
            logger.warning(
                "[%s] URL-based send_photo failed, trying file upload: %s",
                self.name,
                e,
                exc_info=True,
            )
            # 回退：下载后作为文件上传（支持最大 10MB）
            try:
                import httpx
                async with httpx.AsyncClient(timeout=30.0) as client:
                    resp = await client.get(image_url)
                    resp.raise_for_status()
                    image_data = resp.content
                
                msg = await self._bot.send_photo(
                    chat_id=int(chat_id),
                    photo=image_data,
                    caption=caption[:1024] if caption else None,
                    reply_to_message_id=int(reply_to) if reply_to else None,
                    message_thread_id=self._message_thread_id_for_send(_photo_thread),
                )
                return SendResult(success=True, message_id=str(msg.message_id))
            except Exception as e2:
                logger.error(
                    "[%s] File upload send_photo also failed: %s",
                    self.name,
                    e2,
                    exc_info=True,
                )
                # 最终回退：以文本发送 URL
                return await super().send_image(chat_id, image_url, caption, reply_to)
    
    async def send_animation(
        self,
        chat_id: str,
        animation_url: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """以原生 Telegram 动画发送动态 GIF（自动内联播放）。"""
        if not self._bot:
            return SendResult(success=False, error="Not connected")
        
        try:
            _anim_thread = self._metadata_thread_id(metadata)
            msg = await self._bot.send_animation(
                chat_id=int(chat_id),
                animation=animation_url,
                caption=caption[:1024] if caption else None,
                reply_to_message_id=int(reply_to) if reply_to else None,
                message_thread_id=self._message_thread_id_for_send(_anim_thread),
            )
            return SendResult(success=True, message_id=str(msg.message_id))
        except Exception as e:
            logger.error(
                "[%s] Failed to send Telegram animation, falling back to photo: %s",
                self.name,
                e,
                exc_info=True,
            )
            # 回退：尝试作为普通照片发送
            return await self.send_image(chat_id, animation_url, caption, reply_to)

    async def send_typing(self, chat_id: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        """发送输入中指示器。"""
        if self._bot:
            try:
                _typing_thread = self._metadata_thread_id(metadata)
                message_thread_id = self._message_thread_id_for_typing(_typing_thread)
                try:
                    await self._bot.send_chat_action(
                        chat_id=int(chat_id),
                        action="typing",
                        message_thread_id=message_thread_id,
                    )
                except Exception as e:
                    if message_thread_id is not None and self._is_thread_not_found_error(e):
                        await self._bot.send_chat_action(
                            chat_id=int(chat_id),
                            action="typing",
                            message_thread_id=None,
                        )
                    else:
                        raise
            except Exception as e:
                # 输入指示器失败是非致命的；仅在调试级别记录。
                logger.debug(
                    "[%s] Failed to send Telegram typing indicator: %s",
                    self.name,
                    e,
                    exc_info=True,
                )
    
    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        """获取 Telegram 聊天的信息。"""
        if not self._bot:
            return {"name": "Unknown", "type": "dm"}
        
        try:
            chat = await self._bot.get_chat(int(chat_id))
            
            chat_type = "dm"
            if chat.type == ChatType.GROUP:
                chat_type = "group"
            elif chat.type == ChatType.SUPERGROUP:
                chat_type = "group"
                if chat.is_forum:
                    chat_type = "forum"
            elif chat.type == ChatType.CHANNEL:
                chat_type = "channel"
            
            return {
                "name": chat.title or chat.full_name or str(chat_id),
                "type": chat_type,
                "username": chat.username,
                "is_forum": getattr(chat, "is_forum", False),
            }
        except Exception as e:
            logger.error(
                "[%s] Failed to get Telegram chat info for %s: %s",
                self.name,
                chat_id,
                e,
                exc_info=True,
            )
            return {"name": str(chat_id), "type": "dm", "error": str(e)}
    
    def format_message(self, content: str) -> str:
        """
        将标准 markdown 转换为 Telegram MarkdownV2 格式。

        受保护区域（代码块、内联代码）先被提取出来，
        其内容永远不会被修改。标准 markdown 构造
        （标题、粗体、斜体、链接）被翻译为 MarkdownV2 语法，
        所有剩余特殊字符被转义。
        """
        if not content:
            return content

        placeholders: dict = {}
        counter = [0]

        def _ph(value: str) -> str:
            """将 *value* 存储在占位符令牌后面，以在转义过程中保留。"""
            key = f"\x00PH{counter[0]}\x00"
            counter[0] += 1
            placeholders[key] = value
            return key

        text = content

        # 1) 保护围栏代码块 (``` ... ```)
        #    按 MarkdownV2 规范，pre/code 内的 \ 和 ` 必须转义。
        def _protect_fenced(m):
            raw = m.group(0)
            # Split off opening ``` (with optional language) and closing ```
            open_end = raw.index('\n') + 1 if '\n' in raw[3:] else 3
            opening = raw[:open_end]
            body_and_close = raw[open_end:]
            body = body_and_close[:-3]
            body = body.replace('\\', '\\\\').replace('`', '\\`')
            return _ph(opening + body + '```')

        text = re.sub(
            r'(```(?:[^\n]*\n)?[\s\S]*?```)',
            _protect_fenced,
            text,
        )

        # 2) 保护内联代码 (`...`)
        #    按 MarkdownV2 规范，转义内联代码中的 \。
        text = re.sub(
            r'(`[^`]+`)',
            lambda m: _ph(m.group(0).replace('\\', '\\\\')),
            text,
        )

        # 3) 转换 markdown 链接 — 转义显示文本；URL 内部
        #    按 MarkdownV2 规范只需转义 ')' 和 '\'。
        def _convert_link(m):
            display = _escape_mdv2(m.group(1))
            url = m.group(2).replace('\\', '\\\\').replace(')', '\\)')
            return _ph(f'[{display}]({url})')

        text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', _convert_link, text)

        # 4) 转换 markdown 标题 (## Title) → 粗体 *Title*
        def _convert_header(m):
            inner = m.group(1).strip()
            # 去除可能出现在标题内的冗余粗体标记
            inner = re.sub(r'\*\*(.+?)\*\*', r'\1', inner)
            return _ph(f'*{_escape_mdv2(inner)}*')

        text = re.sub(
            r'^#{1,6}\s+(.+)$', _convert_header, text, flags=re.MULTILINE
        )

        # 5) 转换粗体：**text** → *text*（MarkdownV2 粗体）
        text = re.sub(
            r'\*\*(.+?)\*\*',
            lambda m: _ph(f'*{_escape_mdv2(m.group(1))}*'),
            text,
        )

        # 6) 转换斜体：*text*（单星号）→ _text_（MarkdownV2 斜体）
        #    [^*\n]+ 防止跨行匹配（否则会破坏使用 * 标记的
        #    项目列表和多行内容）。
        text = re.sub(
            r'\*([^*\n]+)\*',
            lambda m: _ph(f'_{_escape_mdv2(m.group(1))}_'),
            text,
        )

        # 7) 转换删除线：~~text~~ → ~text~（MarkdownV2）
        text = re.sub(
            r'~~(.+?)~~',
            lambda m: _ph(f'~{_escape_mdv2(m.group(1))}~'),
            text,
        )

        # 8) 转换遮挡：||text|| → ||text||（保护 | 免受转义）
        text = re.sub(
            r'\|\|(.+?)\|\|',
            lambda m: _ph(f'||{_escape_mdv2(m.group(1))}||'),
            text,
        )

        # 9) 转换引用块：行首的 > → 保护 > 免受转义
        #    处理常规引用块 (> text) 和可展开引用块
        #    （Telegram MarkdownV2：**> 表示可展开起始，|| 结束引用）
        def _convert_blockquote(m):
            prefix = m.group(1)  # >, >>, >>>, **>, or **>> etc.
            content = m.group(2)
            # 检查内容是否以 || 结尾（可展开引用块结束标记）
            # 此情况下，保留尾部 || 不转义以供 Telegram 使用
            if prefix.startswith('**') and content.endswith('||'):
                return _ph(f'{prefix} {_escape_mdv2(content[:-2])}||')
            return _ph(f'{prefix} {_escape_mdv2(content)}')

        text = re.sub(
            r'^((?:\*\*)?>{1,3}) (.+)$',
            _convert_blockquote,
            text,
            flags=re.MULTILINE,
        )

        # 10) 转义纯文本中剩余的特殊字符
        text = _escape_mdv2(text)

        # 11) 按逆插入顺序恢复占位符，使嵌套引用
        #    （占位符内的占位符）能正确解析。
        for key in reversed(list(placeholders.keys())):
            text = text.replace(key, placeholders[key])

        # 12) 安全网：转义通过占位符处理遗漏的未转义 ( ) { }。
        #     将文本拆分为代码/非代码段，
        #     确保永远不修改 ``` 或 ` 内的内容。
        _code_split = re.split(r'(```[\s\S]*?```|`[^`]+`)', text)
        _safe_parts = []
        for _idx, _seg in enumerate(_code_split):
            if _idx % 2 == 1:
                # 在代码段/块内 — 保持不变
                _safe_parts.append(_seg)
            else:
                # 代码外部 — 转义裸露的 ( ) { }
                def _esc_bare(m, _seg=_seg):
                    s = m.start()
                    ch = m.group(0)
                    # 已转义
                    if s > 0 and _seg[s - 1] == '\\':
                        return ch
                    # 打开 MarkdownV2 链接 [text](url) 的 (
                    if ch == '(' and s > 0 and _seg[s - 1] == ']':
                        return ch
                    # 关闭链接 URL 的 )
                    if ch == ')':
                        before = _seg[:s]
                        if '](http' in before or '](' in before:
                            # 检查深度
                            depth = 0
                            for j in range(s - 1, max(s - 2000, -1), -1):
                                if _seg[j] == '(':
                                    depth -= 1
                                    if depth < 0:
                                        if j > 0 and _seg[j - 1] == ']':
                                            return ch
                                        break
                                elif _seg[j] == ')':
                                    depth += 1
                    return '\\' + ch
                _safe_parts.append(re.sub(r'[(){}]', _esc_bare, _seg))
        text = ''.join(_safe_parts)

        return text
    
    # ── 群组提及门控 ──────────────────────────────────────────────

    def _telegram_require_mention(self) -> bool:
        """返回群聊是否应要求显式机器人触发。"""
        configured = self.config.extra.get("require_mention")
        if configured is not None:
            if isinstance(configured, str):
                return configured.lower() in ("true", "1", "yes", "on")
            return bool(configured)
        return os.getenv("TELEGRAM_REQUIRE_MENTION", "false").lower() in ("true", "1", "yes", "on")

    def _telegram_free_response_chats(self) -> set[str]:
        raw = self.config.extra.get("free_response_chats")
        if raw is None:
            raw = os.getenv("TELEGRAM_FREE_RESPONSE_CHATS", "")
        if isinstance(raw, list):
            return {str(part).strip() for part in raw if str(part).strip()}
        return {part.strip() for part in str(raw).split(",") if part.strip()}

    def _telegram_ignored_threads(self) -> set[int]:
        raw = self.config.extra.get("ignored_threads")
        if raw is None:
            raw = os.getenv("TELEGRAM_IGNORED_THREADS", "")

        if isinstance(raw, list):
            values = raw
        else:
            values = str(raw).split(",")

        ignored: set[int] = set()
        for value in values:
            text = str(value).strip()
            if not text:
                continue
            try:
                ignored.add(int(text))
            except (TypeError, ValueError):
                logger.warning("[%s] Ignoring invalid Telegram thread id: %r", self.name, value)
        return ignored

    def _compile_mention_patterns(self) -> List[re.Pattern]:
        """编译可选的正则唤醒词模式用于群组触发。"""
        patterns = self.config.extra.get("mention_patterns")
        if patterns is None:
            raw = os.getenv("TELEGRAM_MENTION_PATTERNS", "").strip()
            if raw:
                try:
                    loaded = json.loads(raw)
                except Exception:
                    loaded = [part.strip() for part in raw.splitlines() if part.strip()]
                    if not loaded:
                        loaded = [part.strip() for part in raw.split(",") if part.strip()]
                patterns = loaded

        if patterns is None:
            return []
        if isinstance(patterns, str):
            patterns = [patterns]
        if not isinstance(patterns, list):
            logger.warning(
                "[%s] telegram mention_patterns must be a list or string; got %s",
                self.name,
                type(patterns).__name__,
            )
            return []

        compiled: List[re.Pattern] = []
        for pattern in patterns:
            if not isinstance(pattern, str) or not pattern.strip():
                continue
            try:
                compiled.append(re.compile(pattern, re.IGNORECASE))
            except re.error as exc:
                logger.warning("[%s] Invalid Telegram mention pattern %r: %s", self.name, pattern, exc)
        if compiled:
            logger.info("[%s] Loaded %d Telegram mention pattern(s)", self.name, len(compiled))
        return compiled

    def _is_group_chat(self, message: Message) -> bool:
        chat = getattr(message, "chat", None)
        if not chat:
            return False
        chat_type = str(getattr(chat, "type", "")).split(".")[-1].lower()
        return chat_type in ("group", "supergroup")

    def _is_reply_to_bot(self, message: Message) -> bool:
        if not self._bot or not getattr(message, "reply_to_message", None):
            return False
        reply_user = getattr(message.reply_to_message, "from_user", None)
        return bool(reply_user and getattr(reply_user, "id", None) == getattr(self._bot, "id", None))

    def _message_mentions_bot(self, message: Message) -> bool:
        if not self._bot:
            return False

        bot_username = (getattr(self._bot, "username", None) or "").lstrip("@").lower()
        bot_id = getattr(self._bot, "id", None)

        def _iter_sources():
            yield getattr(message, "text", None) or "", getattr(message, "entities", None) or []
            yield getattr(message, "caption", None) or "", getattr(message, "caption_entities", None) or []

        for source_text, entities in _iter_sources():
            if bot_username and f"@{bot_username}" in source_text.lower():
                return True
            for entity in entities:
                entity_type = str(getattr(entity, "type", "")).split(".")[-1].lower()
                if entity_type == "mention" and bot_username:
                    offset = int(getattr(entity, "offset", -1))
                    length = int(getattr(entity, "length", 0))
                    if offset < 0 or length <= 0:
                        continue
                    if source_text[offset:offset + length].strip().lower() == f"@{bot_username}":
                        return True
                elif entity_type == "text_mention":
                    user = getattr(entity, "user", None)
                    if user and getattr(user, "id", None) == bot_id:
                        return True
        return False

    def _message_matches_mention_patterns(self, message: Message) -> bool:
        if not self._mention_patterns:
            return False
        for candidate in (getattr(message, "text", None), getattr(message, "caption", None)):
            if not candidate:
                continue
            for pattern in self._mention_patterns:
                if pattern.search(candidate):
                    return True
        return False

    def _clean_bot_trigger_text(self, text: Optional[str]) -> Optional[str]:
        if not text or not self._bot or not getattr(self._bot, "username", None):
            return text
        username = re.escape(self._bot.username)
        cleaned = re.sub(rf"(?i)@{username}\b[,:\-]*\s*", "", text).strip()
        return cleaned or text

    def _should_process_message(self, message: Message, *, is_command: bool = False) -> bool:
        """应用 Telegram 群组触发规则。

        DM 保持不受限。群组/超级群组消息在以下情况下被接受：
        - 聊天被显式添加到 ``free_response_chats`` 白名单中
        - ``require_mention`` 被禁用
        - 消息是命令
        - 消息回复了机器人
        - 机器人被 @提及
        - 文本/标题匹配配置的正则唤醒词模式
        """
        if not self._is_group_chat(message):
            return True
        thread_id = getattr(message, "message_thread_id", None)
        if thread_id is not None:
            try:
                if int(thread_id) in self._telegram_ignored_threads():
                    return False
            except (TypeError, ValueError):
                logger.warning("[%s] Ignoring non-numeric Telegram message_thread_id: %r", self.name, thread_id)
        if str(getattr(getattr(message, "chat", None), "id", "")) in self._telegram_free_response_chats():
            return True
        if not self._telegram_require_mention():
            return True
        if is_command:
            return True
        if self._is_reply_to_bot(message):
            return True
        if self._message_mentions_bot(message):
            return True
        return self._message_matches_mention_patterns(message)

    async def _handle_text_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """处理传入的文本消息。

        Telegram 客户端会将长消息拆分为多个更新。缓冲来自
        同一用户/聊天的快速连续文本消息，聚合为单个
        MessageEvent 后再分发。
        """
        if not update.message or not update.message.text:
            return
        if not self._should_process_message(update.message):
            return

        event = self._build_message_event(update.message, MessageType.TEXT)
        event.text = self._clean_bot_trigger_text(event.text)
        self._enqueue_text_event(event)
    
    async def _handle_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """处理传入的命令消息。"""
        if not update.message or not update.message.text:
            return
        if not self._should_process_message(update.message, is_command=True):
            return
        
        event = self._build_message_event(update.message, MessageType.COMMAND)
        await self.handle_message(event)
    
    async def _handle_location_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """处理传入的位置/场馆标注消息。"""
        if not update.message:
            return
        if not self._should_process_message(update.message):
            return

        msg = update.message
        venue = getattr(msg, "venue", None)
        location = getattr(venue, "location", None) if venue else getattr(msg, "location", None)

        if not location:
            return

        lat = getattr(location, "latitude", None)
        lon = getattr(location, "longitude", None)
        if lat is None or lon is None:
            return

        # 构建包含坐标和上下文的文本消息
        parts = ["[The user shared a location pin.]"]
        if venue:
            title = getattr(venue, "title", None)
            address = getattr(venue, "address", None)
            if title:
                parts.append(f"Venue: {title}")
            if address:
                parts.append(f"Address: {address}")
        parts.append(f"latitude: {lat}")
        parts.append(f"longitude: {lon}")
        parts.append(f"Map: https://www.google.com/maps/search/?api=1&query={lat},{lon}")
        parts.append("Ask what they'd like to find nearby (restaurants, cafes, etc.) and any preferences.")

        event = self._build_message_event(msg, MessageType.LOCATION)
        event.text = "\n".join(parts)
        await self.handle_message(event)

    # ------------------------------------------------------------------
    # 文本消息聚合（处理 Telegram 客户端侧拆分）
    # ------------------------------------------------------------------

    def _text_batch_key(self, event: MessageEvent) -> str:
        """会话范围的文本消息批处理键。"""
        from gateway.session import build_session_key
        return build_session_key(
            event.source,
            group_sessions_per_user=self.config.extra.get("group_sessions_per_user", True),
            thread_sessions_per_user=self.config.extra.get("thread_sessions_per_user", False),
        )

    def _enqueue_text_event(self, event: MessageEvent) -> None:
        """缓冲文本事件并重置刷新定时器。

        当 Telegram 将长用户消息拆分为多个更新时，
        它们会在几百毫秒内到达。此方法将它们拼接起来，
        等待短暂的静默期后再分发合并的消息。
        """
        key = self._text_batch_key(event)
        existing = self._pending_text_batches.get(key)
        chunk_len = len(event.text or "")
        if existing is None:
            event._last_chunk_len = chunk_len  # type: ignore[attr-defined]
            self._pending_text_batches[key] = event
        else:
            # 追加后续分段的文本
            if event.text:
                existing.text = f"{existing.text}\n{event.text}" if existing.text else event.text
            existing._last_chunk_len = chunk_len  # type: ignore[attr-defined]
            # 合并可能附加的媒体
            if event.media_urls:
                existing.media_urls.extend(event.media_urls)
                existing.media_types.extend(event.media_types)

        # 取消任何待处理的刷新并重启定时器
        prior_task = self._pending_text_batch_tasks.get(key)
        if prior_task and not prior_task.done():
            prior_task.cancel()
        self._pending_text_batch_tasks[key] = asyncio.create_task(
            self._flush_text_batch(key)
        )

    async def _flush_text_batch(self, key: str) -> None:
        """等待静默期后分发聚合的文本。

        当最新分段接近 Telegram 4096 字符拆分点时使用更长的延迟，
        因为几乎可以确定会有后续分段。
        """
        current_task = asyncio.current_task()
        try:
            # 自适应延迟：如果最新分段接近 Telegram 4096 字符
            # 拆分点，几乎可以确定会有后续分段 — 等待更长时间。
            pending = self._pending_text_batches.get(key)
            last_len = getattr(pending, "_last_chunk_len", 0) if pending else 0
            if last_len >= self._SPLIT_THRESHOLD:
                delay = self._text_batch_split_delay_seconds
            else:
                delay = self._text_batch_delay_seconds
            await asyncio.sleep(delay)
            event = self._pending_text_batches.pop(key, None)
            if not event:
                return
            logger.info(
                "[Telegram] Flushing text batch %s (%d chars)",
                key, len(event.text or ""),
            )
            await self.handle_message(event)
        finally:
            if self._pending_text_batch_tasks.get(key) is current_task:
                self._pending_text_batch_tasks.pop(key, None)

    # ------------------------------------------------------------------
    # 照片批处理
    # ------------------------------------------------------------------

    def _photo_batch_key(self, event: MessageEvent, msg: Message) -> str:
        """返回 Telegram 照片/相册的批处理键。"""
        from gateway.session import build_session_key
        session_key = build_session_key(
            event.source,
            group_sessions_per_user=self.config.extra.get("group_sessions_per_user", True),
            thread_sessions_per_user=self.config.extra.get("thread_sessions_per_user", False),
        )
        media_group_id = getattr(msg, "media_group_id", None)
        if media_group_id:
            return f"{session_key}:album:{media_group_id}"
        return f"{session_key}:photo-burst"

    async def _flush_photo_batch(self, batch_key: str) -> None:
        """将缓冲的照片连发/相册作为单个 MessageEvent 发送。"""
        current_task = asyncio.current_task()
        try:
            await asyncio.sleep(self._media_batch_delay_seconds)
            event = self._pending_photo_batches.pop(batch_key, None)
            if not event:
                return
            logger.info("[Telegram] Flushing photo batch %s with %d image(s)", batch_key, len(event.media_urls))
            await self.handle_message(event)
        finally:
            if self._pending_photo_batch_tasks.get(batch_key) is current_task:
                self._pending_photo_batch_tasks.pop(batch_key, None)

    def _enqueue_photo_event(self, batch_key: str, event: MessageEvent) -> None:
        """将照片事件合并到待处理批次并调度刷新。"""
        existing = self._pending_photo_batches.get(batch_key)
        if existing is None:
            self._pending_photo_batches[batch_key] = event
        else:
            existing.media_urls.extend(event.media_urls)
            existing.media_types.extend(event.media_types)
            if event.text:
                existing.text = self._merge_caption(existing.text, event.text)

        prior_task = self._pending_photo_batch_tasks.get(batch_key)
        if prior_task and not prior_task.done():
            prior_task.cancel()

        self._pending_photo_batch_tasks[batch_key] = asyncio.create_task(self._flush_photo_batch(batch_key))

    async def _handle_media_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """处理传入的媒体消息，将图片下载到本地缓存。"""
        if not update.message:
            return
        if not self._should_process_message(update.message):
            return
        
        msg = update.message
        
        # 确定媒体类型
        if msg.sticker:
            msg_type = MessageType.STICKER
        elif msg.photo:
            msg_type = MessageType.PHOTO
        elif msg.video:
            msg_type = MessageType.VIDEO
        elif msg.audio:
            msg_type = MessageType.AUDIO
        elif msg.voice:
            msg_type = MessageType.VOICE
        elif msg.document:
            msg_type = MessageType.DOCUMENT
        else:
            msg_type = MessageType.DOCUMENT
        
        event = self._build_message_event(msg, msg_type)
        
        # 添加标题作为文本
        if msg.caption:
            event.text = self._clean_bot_trigger_text(msg.caption)
        
        # 处理贴纸：通过视觉工具描述并缓存
        if msg.sticker:
            await self._handle_sticker(msg, event)
            await self.handle_message(event)
            return
        
        # 下载照片到本地图片缓存，使视觉工具可以访问它，
        # 即使 Telegram 的临时文件 URL 过期（约 1 小时）后也能使用。
        if msg.photo:
            try:
                # msg.photo 是按大小排列的 PhotoSize 列表；取最大的
                photo = msg.photo[-1]
                file_obj = await photo.get_file()
                # 将图片字节直接下载到内存
                image_bytes = await file_obj.download_as_bytearray()
                # 从文件路径确定扩展名（如果可用）
                ext = ".jpg"
                if file_obj.file_path:
                    for candidate in [".png", ".webp", ".gif", ".jpeg", ".jpg"]:
                        if file_obj.file_path.lower().endswith(candidate):
                            ext = candidate
                            break
                # 保存到本地缓存（供视觉工具访问）
                cached_path = cache_image_from_bytes(bytes(image_bytes), ext=ext)
                event.media_urls = [cached_path]
                event.media_types = [f"image/{ext.lstrip('.')}" ]
                logger.info("[Telegram] Cached user photo at %s", cached_path)
                media_group_id = getattr(msg, "media_group_id", None)
                if media_group_id:
                    await self._queue_media_group_event(str(media_group_id), event)
                else:
                    batch_key = self._photo_batch_key(event, msg)
                    self._enqueue_photo_event(batch_key, event)
                return

            except Exception as e:
                logger.warning("[Telegram] Failed to cache photo: %s", e, exc_info=True)

        # 下载语音/音频消息到缓存用于 STT 转录
        if msg.voice:
            try:
                file_obj = await msg.voice.get_file()
                audio_bytes = await file_obj.download_as_bytearray()
                cached_path = cache_audio_from_bytes(bytes(audio_bytes), ext=".ogg")
                event.media_urls = [cached_path]
                event.media_types = ["audio/ogg"]
                logger.info("[Telegram] Cached user voice at %s", cached_path)
            except Exception as e:
                logger.warning("[Telegram] Failed to cache voice: %s", e, exc_info=True)
        elif msg.audio:
            try:
                file_obj = await msg.audio.get_file()
                audio_bytes = await file_obj.download_as_bytearray()
                cached_path = cache_audio_from_bytes(bytes(audio_bytes), ext=".mp3")
                event.media_urls = [cached_path]
                event.media_types = ["audio/mp3"]
                logger.info("[Telegram] Cached user audio at %s", cached_path)
            except Exception as e:
                logger.warning("[Telegram] Failed to cache audio: %s", e, exc_info=True)

        # 下载文档文件到缓存供代理处理
        elif msg.document:
            doc = msg.document
            try:
                # 确定文件扩展名
                ext = ""
                original_filename = doc.file_name or ""
                if original_filename:
                    _, ext = os.path.splitext(original_filename)
                    ext = ext.lower()

                # 如果文件名没有扩展名，从 MIME 类型反向查找
                if not ext and doc.mime_type:
                    mime_to_ext = {v: k for k, v in SUPPORTED_DOCUMENT_TYPES.items()}
                    ext = mime_to_ext.get(doc.mime_type, "")

                # 检查是否支持
                if ext not in SUPPORTED_DOCUMENT_TYPES:
                    supported_list = ", ".join(sorted(SUPPORTED_DOCUMENT_TYPES.keys()))
                    event.text = (
                        f"Unsupported document type '{ext or 'unknown'}'. "
                        f"Supported types: {supported_list}"
                    )
                    logger.info("[Telegram] Unsupported document type: %s", ext or "unknown")
                    await self.handle_message(event)
                    return

                # 检查文件大小（Telegram Bot API 限制：20 MB）
                MAX_DOC_BYTES = 20 * 1024 * 1024
                if not doc.file_size or doc.file_size > MAX_DOC_BYTES:
                    event.text = (
                        "The document is too large or its size could not be verified. "
                        "Maximum: 20 MB."
                    )
                    logger.info("[Telegram] Document too large: %s bytes", doc.file_size)
                    await self.handle_message(event)
                    return

                # 下载并缓存
                file_obj = await doc.get_file()
                doc_bytes = await file_obj.download_as_bytearray()
                raw_bytes = bytes(doc_bytes)
                cached_path = cache_document_from_bytes(raw_bytes, original_filename or f"document{ext}")
                mime_type = SUPPORTED_DOCUMENT_TYPES[ext]
                event.media_urls = [cached_path]
                event.media_types = [mime_type]
                logger.info("[Telegram] Cached user document at %s", cached_path)

                # 对于文本文件，将内容注入 event.text（上限 100 KB）
                MAX_TEXT_INJECT_BYTES = 100 * 1024
                if ext in (".md", ".txt") and len(raw_bytes) <= MAX_TEXT_INJECT_BYTES:
                    try:
                        text_content = raw_bytes.decode("utf-8")
                        display_name = original_filename or f"document{ext}"
                        display_name = re.sub(r'[^\w.\- ]', '_', display_name)
                        injection = f"[Content of {display_name}]:\n{text_content}"
                        if event.text:
                            event.text = f"{injection}\n\n{event.text}"
                        else:
                            event.text = injection
                    except UnicodeDecodeError:
                        logger.warning(
                            "[Telegram] Could not decode text file as UTF-8, skipping content injection",
                            exc_info=True,
                        )

            except Exception as e:
                logger.warning("[Telegram] Failed to cache document: %s", e, exc_info=True)

        media_group_id = getattr(msg, "media_group_id", None)
        if media_group_id:
            await self._queue_media_group_event(str(media_group_id), event)
            return

        await self.handle_message(event)
    
    async def _queue_media_group_event(self, media_group_id: str, event: MessageEvent) -> None:
        """缓冲 Telegram 媒体组项目，使相册作为一个逻辑事件到达。

        Telegram 以共享 media_group_id 的多个更新传递相册。
        如果我们立即转发每个项目，网关会认为第二张图片是
        新的用户消息并中断第一张。我们短暂防抖并将
        附件合并为单个 MessageEvent。
        """
        existing = self._media_group_events.get(media_group_id)
        if existing is None:
            self._media_group_events[media_group_id] = event
        else:
            existing.media_urls.extend(event.media_urls)
            existing.media_types.extend(event.media_types)
            if event.text:
                existing.text = self._merge_caption(existing.text, event.text)

        prior_task = self._media_group_tasks.get(media_group_id)
        if prior_task:
            prior_task.cancel()

        self._media_group_tasks[media_group_id] = asyncio.create_task(
            self._flush_media_group_event(media_group_id)
        )

    async def _flush_media_group_event(self, media_group_id: str) -> None:
        try:
            await asyncio.sleep(self.MEDIA_GROUP_WAIT_SECONDS)
            event = self._media_group_events.pop(media_group_id, None)
            if event is not None:
                await self.handle_message(event)
        except asyncio.CancelledError:
            return
        finally:
            self._media_group_tasks.pop(media_group_id, None)

    async def _handle_sticker(self, msg: Message, event: "MessageEvent") -> None:
        """
        通过视觉分析描述 Telegram 贴纸，带缓存。

        对于静态贴纸 (WEBP)，下载后用视觉工具分析，并按
        file_unique_id 缓存描述。对于动画/视频贴纸，注入
        标注表情符号的占位符。
        """
        from gateway.sticker_cache import (
            get_cached_description,
            cache_sticker_description,
            build_sticker_injection,
            build_animated_sticker_injection,
            STICKER_VISION_PROMPT,
        )

        sticker = msg.sticker
        emoji = sticker.emoji or ""
        set_name = sticker.set_name or ""

        # 动画和视频贴纸无法作为静态图片分析
        if sticker.is_animated or sticker.is_video:
            event.text = build_animated_sticker_injection(emoji)
            return

        # 先检查缓存
        cached = get_cached_description(sticker.file_unique_id)
        if cached:
            event.text = build_sticker_injection(
                cached["description"], cached.get("emoji", emoji), cached.get("set_name", set_name)
            )
            logger.info("[Telegram] Sticker cache hit: %s", sticker.file_unique_id)
            return

        # 缓存未命中 -- 下载并分析
        try:
            file_obj = await sticker.get_file()
            image_bytes = await file_obj.download_as_bytearray()
            cached_path = cache_image_from_bytes(bytes(image_bytes), ext=".webp")
            logger.info("[Telegram] Analyzing sticker at %s", cached_path)

            from tools.vision_tools import vision_analyze_tool
            import json as _json

            result_json = await vision_analyze_tool(
                image_url=cached_path,
                user_prompt=STICKER_VISION_PROMPT,
            )
            result = _json.loads(result_json)

            if result.get("success"):
                description = result.get("analysis", "a sticker")
                cache_sticker_description(sticker.file_unique_id, description, emoji, set_name)
                event.text = build_sticker_injection(description, emoji, set_name)
            else:
                # 视觉分析失败 -- 使用表情符号作为回退
                event.text = build_sticker_injection(
                    f"a sticker with emoji {emoji}" if emoji else "a sticker",
                    emoji, set_name,
                )
        except Exception as e:
            logger.warning("[Telegram] Sticker analysis error: %s", e, exc_info=True)
            event.text = build_sticker_injection(
                f"a sticker with emoji {emoji}" if emoji else "a sticker",
                emoji, set_name,
            )

    def _reload_dm_topics_from_config(self) -> None:
        """从 config.yaml 重新读取 dm_topics 并将任何新的 thread_id 加载到缓存。

        这允许外部创建的话题（例如通过 API 由代理创建）
        无需重启网关即可被识别。
        """
        try:
            from hermes_constants import get_hermes_home
            config_path = get_hermes_home() / "config.yaml"
            if not config_path.exists():
                return

            import yaml as _yaml
            with open(config_path, "r") as f:
                config = _yaml.safe_load(f) or {}

            dm_topics = (
                config.get("platforms", {})
                .get("telegram", {})
                .get("extra", {})
                .get("dm_topics", [])
            )
            if not dm_topics:
                return

            # 更新内存中的配置并缓存任何新的 thread_id
            self._dm_topics_config = dm_topics
            for chat_entry in dm_topics:
                cid = chat_entry.get("chat_id")
                if not cid:
                    continue
                for t in chat_entry.get("topics", []):
                    tid = t.get("thread_id")
                    name = t.get("name")
                    if tid and name:
                        cache_key = f"{cid}:{name}"
                        if cache_key not in self._dm_topics:
                            self._dm_topics[cache_key] = int(tid)
                            logger.info(
                                "[%s] Hot-loaded DM topic from config: %s -> thread_id=%s",
                                self.name, cache_key, tid,
                            )
        except Exception as e:
            logger.debug("[%s] Failed to reload dm_topics from config: %s", self.name, e)

    def _get_dm_topic_info(self, chat_id: str, thread_id: Optional[str]) -> Optional[Dict[str, Any]]:
        """按 chat_id 和 thread_id 查找 DM 话题配置。

        如果此 thread_id 匹配已知的 DM 话题则返回话题配置字典
        （name、skill 等），否则返回 None。
        """
        if not thread_id:
            return None

        thread_id_int = int(thread_id)

        # 先检查已缓存的话题（由我们创建或启动时加载）
        for key, cached_tid in self._dm_topics.items():
            if cached_tid == thread_id_int and key.startswith(f"{chat_id}:"):
                topic_name = key.split(":", 1)[1]
                # 查找此话题的完整配置
                for chat_entry in self._dm_topics_config:
                    if str(chat_entry.get("chat_id")) == chat_id:
                        for t in chat_entry.get("topics", []):
                            if t.get("name") == topic_name:
                                return t
                return {"name": topic_name}

        # 不在缓存中 — 热重载配置以防话题是在外部添加的
        self._reload_dm_topics_from_config()

        # 重载后再次检查缓存
        for key, cached_tid in self._dm_topics.items():
            if cached_tid == thread_id_int and key.startswith(f"{chat_id}:"):
                topic_name = key.split(":", 1)[1]
                for chat_entry in self._dm_topics_config:
                    if str(chat_entry.get("chat_id")) == chat_id:
                        for t in chat_entry.get("topics", []):
                            if t.get("name") == topic_name:
                                return t
                return {"name": topic_name}

        return None

    def _cache_dm_topic_from_message(self, chat_id: str, thread_id: str, topic_name: str) -> None:
        """缓存从传入消息中发现的 thread_id -> topic_name 映射。"""
        cache_key = f"{chat_id}:{topic_name}"
        if cache_key not in self._dm_topics:
            self._dm_topics[cache_key] = int(thread_id)
            logger.info(
                "[%s] Cached DM topic from message: %s -> thread_id=%s",
                self.name, cache_key, thread_id,
            )

    def _build_message_event(self, message: Message, msg_type: MessageType) -> MessageEvent:
        """从 Telegram 消息构建 MessageEvent。"""
        chat = message.chat
        user = message.from_user
        
        # 确定聊天类型
        chat_type = "dm"
        if chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
            chat_type = "group"
        elif chat.type == ChatType.CHANNEL:
            chat_type = "channel"

        # 解析 DM 话题名称和技能绑定
        thread_id_raw = message.message_thread_id
        thread_id_str = str(thread_id_raw) if thread_id_raw is not None else None
        if chat_type == "group" and thread_id_str is None and getattr(chat, "is_forum", False):
            thread_id_str = self._GENERAL_TOPIC_THREAD_ID
        chat_topic = None
        topic_skill = None

        if chat_type == "dm" and thread_id_str:
            topic_info = self._get_dm_topic_info(str(chat.id), thread_id_str)
            if topic_info:
                chat_topic = topic_info.get("name")
                topic_skill = topic_info.get("skill")

            # 也检查 forum_topic_created 服务消息以发现话题
            if hasattr(message, "forum_topic_created") and message.forum_topic_created:
                created_name = message.forum_topic_created.name
                if created_name:
                    self._cache_dm_topic_from_message(str(chat.id), thread_id_str, created_name)
                    if not chat_topic:
                        chat_topic = created_name

        elif chat_type == "group" and thread_id_str:
            # 群组/超级群组论坛话题通过 config.extra['group_topics'] 绑定技能
            group_topics_config: list = self.config.extra.get("group_topics", [])
            for chat_entry in group_topics_config:
                if str(chat_entry.get("chat_id", "")) == str(chat.id):
                    for topic in chat_entry.get("topics", []):
                        tid = topic.get("thread_id")
                        if tid is not None and str(tid) == thread_id_str:
                            chat_topic = topic.get("name")
                            topic_skill = topic.get("skill")
                            break
                    break

        # 构建来源
        source = self.build_source(
            chat_id=str(chat.id),
            chat_name=chat.title or (chat.full_name if hasattr(chat, "full_name") else None),
            chat_type=chat_type,
            user_id=str(user.id) if user else None,
            user_name=user.full_name if user else None,
            thread_id=thread_id_str,
            chat_topic=chat_topic,
        )
        
        # 如果此消息是回复，提取回复上下文
        reply_to_id = None
        reply_to_text = None
        if message.reply_to_message:
            reply_to_id = str(message.reply_to_message.message_id)
            reply_to_text = message.reply_to_message.text or message.reply_to_message.caption or None

        # 每频道/话题的临时提示
        from gateway.platforms.base import resolve_channel_prompt
        _chat_id_str = str(chat.id)
        _channel_prompt = resolve_channel_prompt(
            self.config.extra,
            thread_id_str or _chat_id_str,
            _chat_id_str if thread_id_str else None,
        )

        return MessageEvent(
            text=message.text or "",
            message_type=msg_type,
            source=source,
            raw_message=message,
            message_id=str(message.message_id),
            reply_to_message_id=reply_to_id,
            reply_to_text=reply_to_text,
            auto_skill=topic_skill,
            channel_prompt=_channel_prompt,
            timestamp=message.date,
        )

    # ── 消息反应（处理生命周期）──────────────────────────

    def _reactions_enabled(self) -> bool:
        """检查消息反应是否通过配置/环境变量启用。"""
        return os.getenv("TELEGRAM_REACTIONS", "false").lower() not in ("false", "0", "no")

    async def _set_reaction(self, chat_id: str, message_id: str, emoji: str) -> bool:
        """在 Telegram 消息上设置单个表情反应。"""
        if not self._bot:
            return False
        try:
            await self._bot.set_message_reaction(
                chat_id=int(chat_id),
                message_id=int(message_id),
                reaction=emoji,
            )
            return True
        except Exception as e:
            logger.debug("[%s] set_message_reaction failed (%s): %s", self.name, emoji, e)
            return False

    async def on_processing_start(self, event: MessageEvent) -> None:
        """消息处理开始时添加处理中反应。"""
        if not self._reactions_enabled():
            return
        chat_id = getattr(event.source, "chat_id", None)
        message_id = getattr(event, "message_id", None)
        if chat_id and message_id:
            await self._set_reaction(chat_id, message_id, "\U0001f440")

    async def on_processing_complete(self, event: MessageEvent, outcome: ProcessingOutcome) -> None:
        """将处理中反应替换为最终的成功/失败反应。

        与 Discord（累加反应）不同，Telegram 的 set_message_reaction
        在一次调用中替换所有现有反应 — 无需移除步骤。
        """
        if not self._reactions_enabled():
            return
        chat_id = getattr(event.source, "chat_id", None)
        message_id = getattr(event, "message_id", None)
        if chat_id and message_id and outcome != ProcessingOutcome.CANCELLED:
            await self._set_reaction(
                chat_id,
                message_id,
                "\U0001f44d" if outcome == ProcessingOutcome.SUCCESS else "\U0001f44e",
            )
