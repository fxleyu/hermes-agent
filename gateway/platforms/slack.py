"""
Slack 平台适配器。

使用 slack-bolt（Python）的 Socket Mode 实现：
- 从频道和私信中接收消息
- 发送回复
- 处理斜杠命令
- 线程支持
"""

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Dict, Optional, Any, Tuple

try:
    from slack_bolt.async_app import AsyncApp
    from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
    from slack_sdk.web.async_client import AsyncWebClient
    SLACK_AVAILABLE = True
except ImportError:
    SLACK_AVAILABLE = False
    AsyncApp = Any
    AsyncSocketModeHandler = Any
    AsyncWebClient = Any

import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))

from gateway.config import Platform, PlatformConfig
from gateway.platforms.helpers import MessageDeduplicator
from gateway.platforms.base import (
    BasePlatformAdapter,
    MessageEvent,
    MessageType,
    SendResult,
    SUPPORTED_DOCUMENT_TYPES,
    safe_url_for_log,
    cache_document_from_bytes,
)


logger = logging.getLogger(__name__)


@dataclass
class _ThreadContextCache:
    """已获取的线程上下文的缓存条目。"""
    content: str
    fetched_at: float = field(default_factory=time.monotonic)
    message_count: int = 0


def check_slack_requirements() -> bool:
    """检查 Slack 依赖项是否可用。"""
    return SLACK_AVAILABLE


class SlackAdapter(BasePlatformAdapter):
    """
    Slack Bot 适配器，使用 Socket Mode。

    需要两个令牌：
      - SLACK_BOT_TOKEN（xoxb-...）用于 API 调用
      - SLACK_APP_TOKEN（xapp-...）用于 Socket Mode 连接

    功能特性：
      - 私信和频道消息（频道中需 @提及 才触发）
      - 线程支持
      - 文件/图片/音频附件
      - 斜杠命令（/hermes）
      - 正在输入指示器（Slack Bot 原生不支持）
    """

    MAX_MESSAGE_LENGTH = 39000  # Slack API 允许 40,000 字符；留出余量

    def __init__(self, config: PlatformConfig):
        super().__init__(config, Platform.SLACK)
        self._app: Optional[AsyncApp] = None
        self._handler: Optional[AsyncSocketModeHandler] = None
        self._bot_user_id: Optional[str] = None
        self._user_name_cache: Dict[str, str] = {}  # user_id -> 显示名称
        self._socket_mode_task: Optional[asyncio.Task] = None
        # 多工作空间支持
        self._team_clients: Dict[str, AsyncWebClient] = {}   # team_id -> WebClient
        self._team_bot_user_ids: Dict[str, str] = {}          # team_id -> bot_user_id
        self._channel_team: Dict[str, str] = {}                # channel_id -> team_id
        # 去重缓存：防止 Socket Mode 重连后重复投递事件时产生重复的 Bot 响应。
        self._dedup = MessageDeduplicator()
        # 跟踪待审批消息的 message_ts -> 已解决标志，防止审批按钮被重复点击。
        self._approval_resolved: Dict[str, bool] = {}
        # 跟踪 Bot 发送的消息时间戳，这样即使没有显式 @提及也能响应线程回复。
        self._bot_message_ts: set = set()
        self._BOT_TS_MAX = 5000  # 设置上限以避免无限增长
        # 跟踪 Bot 被 @提及 过的线程——一旦被提及，自动响应该线程中的所有后续消息。
        self._mentioned_threads: set = set()
        self._MENTIONED_THREADS_MAX = 5000
        # 按 (channel_id, thread_ts) 索引的 AI 助手线程元数据。Slack 的
        # AI 助手生命周期事件可能在消息事件之前/同时到达，
        # 它们携带了稳定会话和记忆作用域所需的用户/线程标识。
        self._assistant_threads: Dict[Tuple[str, str], Dict[str, str]] = {}
        self._ASSISTANT_THREADS_MAX = 5000
        # _fetch_thread_context 结果的缓存：cache_key -> _ThreadContextCache
        self._thread_context_cache: Dict[str, _ThreadContextCache] = {}
        self._THREAD_CACHE_TTL = 60.0

    async def connect(self) -> bool:
        """通过 Socket Mode 连接到 Slack。"""
        if not SLACK_AVAILABLE:
            logger.error(
                "[Slack] slack-bolt not installed. Run: pip install slack-bolt",
            )
            return False

        raw_token = self.config.token
        app_token = os.getenv("SLACK_APP_TOKEN")

        if not raw_token:
            logger.error("[Slack] SLACK_BOT_TOKEN not set")
            return False
        if not app_token:
            logger.error("[Slack] SLACK_APP_TOKEN not set")
            return False

            # 支持逗号分隔的 Bot 令牌以实现多工作空间
        bot_tokens = [t.strip() for t in raw_token.split(",") if t.strip()]

        # 同时从 OAuth 令牌文件加载令牌
        from hermes_constants import get_hermes_home
        tokens_file = get_hermes_home() / "slack_tokens.json"
        if tokens_file.exists():
            try:
                saved = json.loads(tokens_file.read_text(encoding="utf-8"))
                for team_id, entry in saved.items():
                    tok = entry.get("token", "") if isinstance(entry, dict) else ""
                    if tok and tok not in bot_tokens:
                        bot_tokens.append(tok)
                        team_label = entry.get("team_name", team_id) if isinstance(entry, dict) else team_id
                        logger.info("[Slack] Loaded saved token for workspace %s", team_label)
            except Exception as e:
                logger.warning("[Slack] Failed to read %s: %s", tokens_file, e)

        try:
            if not self._acquire_platform_lock('slack-app-token', app_token, 'Slack app token'):
                return False

            # 第一个令牌为主令牌——用于 AsyncApp / Socket Mode
            primary_token = bot_tokens[0]
            self._app = AsyncApp(token=primary_token)

            # 注册每个 Bot 令牌并映射 team_id -> client
            for token in bot_tokens:
                client = AsyncWebClient(token=token)
                auth_response = await client.auth_test()
                team_id = auth_response.get("team_id", "")
                bot_user_id = auth_response.get("user_id", "")
                bot_name = auth_response.get("user", "unknown")
                team_name = auth_response.get("team", "unknown")

                self._team_clients[team_id] = client
                self._team_bot_user_ids[team_id] = bot_user_id

                # 第一个令牌设置主 bot_user_id（向后兼容）
                if self._bot_user_id is None:
                    self._bot_user_id = bot_user_id

                logger.info(
                    "[Slack] Authenticated as @%s in workspace %s (team: %s)",
                    bot_name, team_name, team_id,
                )

            # 注册消息事件处理器
            @self._app.event("message")
            async def handle_message_event(event, say):
                await self._handle_slack_message(event)

            # 确认 app_mention 事件以防止 Bolt 404 错误。
            # 上面的 "message" 处理器已经处理了频道中的 @提及，
            # 所以这里故意是空操作以避免重复处理。
            @self._app.event("app_mention")
            async def handle_app_mention(event, say):
                pass

            @self._app.event("assistant_thread_started")
            async def handle_assistant_thread_started(event, say):
                await self._handle_assistant_thread_lifecycle_event(event)

            @self._app.event("assistant_thread_context_changed")
            async def handle_assistant_thread_context_changed(event, say):
                await self._handle_assistant_thread_lifecycle_event(event)

            # 注册斜杠命令处理器
            @self._app.command("/hermes")
            async def handle_hermes_command(ack, command):
                await ack()
                await self._handle_slash_command(command)

            # 注册 Block Kit 操作处理器用于审批按钮
            for _action_id in (
                "hermes_approve_once",
                "hermes_approve_session",
                "hermes_approve_always",
                "hermes_deny",
            ):
                self._app.action(_action_id)(self._handle_approval_action)

            # 在后台启动 Socket Mode 处理器
            self._handler = AsyncSocketModeHandler(self._app, app_token)
            self._socket_mode_task = asyncio.create_task(self._handler.start_async())

            self._running = True
            logger.info(
                "[Slack] Socket Mode connected (%d workspace(s))",
                len(self._team_clients),
            )
            return True

        except Exception as e:  # pragma: no cover - defensive logging
            logger.error("[Slack] Connection failed: %s", e, exc_info=True)
            return False

    async def disconnect(self) -> None:
        """断开与 Slack 的连接。"""
        if self._handler:
            try:
                await self._handler.close_async()
            except Exception as e:  # pragma: no cover - defensive logging
                logger.warning("[Slack] Error while closing Socket Mode handler: %s", e, exc_info=True)
        self._running = False

        self._release_platform_lock()

        logger.info("[Slack] Disconnected")

    def _get_client(self, chat_id: str) -> AsyncWebClient:
        """返回指定频道所属工作空间的 WebClient。"""
        team_id = self._channel_team.get(chat_id)
        if team_id and team_id in self._team_clients:
            return self._team_clients[team_id]
        return self._app.client  # 回退到主客户端

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """向 Slack 频道或私信发送消息。"""
        if not self._app:
            return SendResult(success=False, error="Not connected")

        try:
            # 将标准 Markdown 转换为 Slack mrkdwn 格式
            formatted = self.format_message(content)

            # 分割长消息，保留代码块边界
            chunks = self.truncate_message(formatted, self.MAX_MESSAGE_LENGTH)

            thread_ts = self._resolve_thread_ts(reply_to, metadata)
            last_result = None

            # reply_broadcast：同时将线程回复发布到主频道。
            # 通过平台配置控制：gateway.slack.reply_broadcast
            broadcast = self.config.extra.get("reply_broadcast", False)

            for i, chunk in enumerate(chunks):
                kwargs = {
                    "channel": chat_id,
                    "text": chunk,
                    "mrkdwn": True,
                }
                if thread_ts:
                    kwargs["thread_ts"] = thread_ts
                    # 仅对第一条回复的第一个分片进行广播
                    if broadcast and i == 0:
                        kwargs["reply_broadcast"] = True

                last_result = await self._get_client(chat_id).chat_postMessage(**kwargs)

            # 跟踪已发送消息的时间戳，以便在没有 @提及 的情况下自动响应线程回复。
            sent_ts = last_result.get("ts") if last_result else None
            if sent_ts:
                self._bot_message_ts.add(sent_ts)
                # 同时注册线程根消息，使"回复我的回复"也能工作
                if thread_ts:
                    self._bot_message_ts.add(thread_ts)
                if len(self._bot_message_ts) > self._BOT_TS_MAX:
                    excess = len(self._bot_message_ts) - self._BOT_TS_MAX // 2
                    for old_ts in list(self._bot_message_ts)[:excess]:
                        self._bot_message_ts.discard(old_ts)

            return SendResult(
                success=True,
                message_id=sent_ts,
                raw_response=last_result,
            )

        except Exception as e:  # pragma: no cover - defensive logging
            logger.error("[Slack] Send error: %s", e, exc_info=True)
            return SendResult(success=False, error=str(e))

    async def edit_message(
        self,
        chat_id: str,
        message_id: str,
        content: str,
    ) -> SendResult:
        """编辑之前发送的 Slack 消息。"""
        if not self._app:
            return SendResult(success=False, error="Not connected")
        try:
            formatted = self.format_message(content)
            await self._get_client(chat_id).chat_update(
                channel=chat_id,
                ts=message_id,
                text=formatted,
            )
            return SendResult(success=True, message_id=message_id)
        except Exception as e:  # pragma: no cover - defensive logging
            logger.error(
                "[Slack] Failed to edit message %s in channel %s: %s",
                message_id,
                chat_id,
                e,
                exc_info=True,
            )
            return SendResult(success=False, error=str(e))

    async def send_typing(self, chat_id: str, metadata=None) -> None:
        """使用 assistant.threads.setStatus 显示正在输入/状态指示器。

        在线程中 Bot 名称旁边显示 "is thinking..."。
        需要 assistant:write 或 chat:write 权限范围。
        当 Bot 向线程发送回复时自动清除。
        """
        if not self._app:
            return

        thread_ts = None
        if metadata:
            thread_ts = metadata.get("thread_id") or metadata.get("thread_ts")

        if not thread_ts:
            return  # 只能在线程上下文中设置状态

        try:
            await self._get_client(chat_id).assistant_threads_setStatus(
                channel_id=chat_id,
                thread_ts=thread_ts,
                status="is thinking...",
            )
        except Exception as e:
            # 静默忽略——可能缺少 assistant:write 权限或不在
            # 助手启用的上下文中。会回退到表情反应。
            logger.debug("[Slack] assistant.threads.setStatus failed: %s", e)

    def _dm_top_level_threads_as_sessions(self) -> bool:
        """是否将顶级 Slack 私信视为每条消息独立的会话线程。

        默认为 ``True``，这样每个可见的私信回复线程都被隔离为
        独立的 Hermes 会话——与频道中已有的每线程行为一致。
        在 config.yaml 中设置
        ``platforms.slack.extra.dm_top_level_threads_as_sessions``
        为 ``false`` 可恢复旧版行为，即所有顶级私信共享一个连续会话。
        """
        raw = self.config.extra.get("dm_top_level_threads_as_sessions")
        if raw is None:
            return True  # default: each DM thread is its own session
        return str(raw).strip().lower() in ("1", "true", "yes", "on")

    def _resolve_thread_ts(
        self,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """解析 Slack API 调用所需的正确 thread_ts。

        优先使用 metadata 中的 thread_id（线程父消息的 ts，由网关设置），
        而非 reply_to（可能是子消息的 ts）。

        当平台额外配置中 ``reply_in_thread`` 为 ``false`` 时，
        顶级频道消息会收到直接的频道回复而非线程回复。
        源自已有线程中的消息始终在线程内回复以保持对话上下文。
        """
        # 当 reply_in_thread 被禁用时（默认为 True 以向后兼容），
        # 仅回复已属于现有线程的线程消息。
        if not self.config.extra.get("reply_in_thread", True):
            existing_thread = (metadata or {}).get("thread_id") or (metadata or {}).get("thread_ts")
            return existing_thread or None

        if metadata:
            if metadata.get("thread_id"):
                return metadata["thread_id"]
            if metadata.get("thread_ts"):
                return metadata["thread_ts"]
        return reply_to

    async def _upload_file(
        self,
        chat_id: str,
        file_path: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """上传本地文件到 Slack。"""
        if not self._app:
            return SendResult(success=False, error="Not connected")

        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")

        result = await self._get_client(chat_id).files_upload_v2(
            channel=chat_id,
            file=file_path,
            filename=os.path.basename(file_path),
            initial_comment=caption or "",
            thread_ts=self._resolve_thread_ts(reply_to, metadata),
        )
        return SendResult(success=True, raw_response=result)

    # ----- Markdown -> mrkdwn 转换 -----

    def format_message(self, content: str) -> str:
        """将标准 Markdown 转换为 Slack mrkdwn 格式。

        先提取受保护区域（代码块、行内代码），确保其内容不会被修改。
        然后将标准 Markdown 结构（标题、粗体、斜体、链接）
        转换为 mrkdwn 语法。
        """
        if not content:
            return content

        placeholders: dict = {}
        counter = [0]

        def _ph(value: str) -> str:
            """将值存入占位符，使其在后续处理中保持不变。"""
            key = f"\x00SL{counter[0]}\x00"
            counter[0] += 1
            placeholders[key] = value
            return key

        text = content

        # 1) 保护围栏代码块（``` ... ```）
        text = re.sub(
            r'(```(?:[^\n]*\n)?[\s\S]*?```)',
            lambda m: _ph(m.group(0)),
            text,
        )

        # 2) 保护行内代码（`...`）
        text = re.sub(r'(`[^`]+`)', lambda m: _ph(m.group(0)), text)

        # 3) 将 Markdown 链接 [text](url) 转换为 Slack 格式 <url|text>
        def _convert_markdown_link(m):
            label = m.group(1)
            url = m.group(2).strip()
            if url.startswith('<') and url.endswith('>'):
                url = url[1:-1].strip()
            return _ph(f'<{url}|{label}>')

        text = re.sub(
            r'\[([^\]]+)\]\(([^()]*(?:\([^()]*\)[^()]*)*)\)',
            _convert_markdown_link,
            text,
        )

        # 4) 保护已有的 Slack 实体/手动链接，防止转义和后续格式化处理破坏它们。
        text = re.sub(
            r'(<(?:[@#!]|(?:https?|mailto|tel):)[^>\n]+>)',
            lambda m: _ph(m.group(1)),
            text,
        )

        # 5) 在转义之前保护块引用标记
        text = re.sub(r'^(>+\s)', lambda m: _ph(m.group(0)), text, flags=re.MULTILINE)

        # 6) 对剩余纯文本中的 Slack 控制字符进行转义。
        # 先反转义，避免已转义的输入被双重转义。
        text = text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
        text = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

        # 7) 将标题（## Title）转换为 *Title*（粗体）
        def _convert_header(m):
            inner = m.group(1).strip()
            # 移除标题内的冗余粗体标记
            inner = re.sub(r'\*\*(.+?)\*\*', r'\1', inner)
            return _ph(f'*{inner}*')

        text = re.sub(
            r'^#{1,6}\s+(.+)$', _convert_header, text, flags=re.MULTILINE
        )

        # 8) 将粗斜体 ***text*** 转换为 *_text_*（Slack 粗体包裹斜体）
        text = re.sub(
            r'\*\*\*(.+?)\*\*\*',
            lambda m: _ph(f'*_{m.group(1)}_*'),
            text,
        )

        # 9) 将粗体 **text** 转换为 *text*（Slack 粗体）
        text = re.sub(
            r'\*\*(.+?)\*\*',
            lambda m: _ph(f'*{m.group(1)}*'),
            text,
        )

        # 10) 转换斜体：_text_ 保持不变（已是 Slack 斜体）
        #     单 *text* -> _text_（Slack 斜体）
        text = re.sub(
            r'(?<!\*)\*([^*\n]+)\*(?!\*)',
            lambda m: _ph(f'_{m.group(1)}_'),
            text,
        )

        # 11) 将删除线 ~~text~~ 转换为 ~text~
        text = re.sub(
            r'~~(.+?)~~',
            lambda m: _ph(f'~{m.group(1)}~'),
            text,
        )

        # 12) 块引用：> 前缀已在上面第 5 步中保护。

        # 13) 按逆序恢复占位符
        for key in reversed(placeholders):
            text = text.replace(key, placeholders[key])

        return text

    # ----- 表情反应 -----

    async def _add_reaction(
        self, channel: str, timestamp: str, emoji: str
    ) -> bool:
        """为消息添加表情反应。成功时返回 True。"""
        if not self._app:
            return False
        try:
            await self._get_client(channel).reactions_add(
                channel=channel, timestamp=timestamp, name=emoji
            )
            return True
        except Exception as e:
            # 不记录为错误——可能已有反应或缺少权限范围
            logger.debug("[Slack] reactions.add failed (%s): %s", emoji, e)
            return False

    async def _remove_reaction(
        self, channel: str, timestamp: str, emoji: str
    ) -> bool:
        """移除消息的表情反应。成功时返回 True。"""
        if not self._app:
            return False
        try:
            await self._get_client(channel).reactions_remove(
                channel=channel, timestamp=timestamp, name=emoji
            )
            return True
        except Exception as e:
            logger.debug("[Slack] reactions.remove failed (%s): %s", emoji, e)
            return False

    # ----- 用户身份解析 -----

    async def _resolve_user_name(self, user_id: str, chat_id: str = "") -> str:
        """将 Slack 用户 ID 解析为显示名称，带缓存。"""
        if not user_id:
            return ""
        if user_id in self._user_name_cache:
            return self._user_name_cache[user_id]

        if not self._app:
            return user_id

        try:
            client = self._get_client(chat_id) if chat_id else self._app.client
            result = await client.users_info(user=user_id)
            user = result.get("user", {})
            # 优先级：display_name -> real_name -> user_id
            profile = user.get("profile", {})
            name = (
                profile.get("display_name")
                or profile.get("real_name")
                or user.get("real_name")
                or user.get("name")
                or user_id
            )
            self._user_name_cache[user_id] = name
            return name
        except Exception as e:
            logger.debug("[Slack] users.info failed for %s: %s", user_id, e)
            self._user_name_cache[user_id] = user_id
            return user_id

    async def send_image_file(
        self,
        chat_id: str,
        image_path: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """通过上传将本地图片文件发送到 Slack。"""
        try:
            return await self._upload_file(chat_id, image_path, caption, reply_to, metadata)
        except FileNotFoundError:
            return SendResult(success=False, error=f"Image file not found: {image_path}")
        except Exception as e:  # pragma: no cover - defensive logging
            logger.error(
                "[%s] Failed to send local Slack image %s: %s",
                self.name,
                image_path,
                e,
                exc_info=True,
            )
            text = f"🖼️ Image: {image_path}"
            if caption:
                text = f"{caption}\n{text}"
            return await self.send(chat_id, text, reply_to=reply_to, metadata=metadata)

    async def send_image(
        self,
        chat_id: str,
        image_url: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """通过将 URL 作为文件上传来向 Slack 发送图片。"""
        if not self._app:
            return SendResult(success=False, error="Not connected")

        from tools.url_safety import is_safe_url
        if not is_safe_url(image_url):
            logger.warning("[Slack] Blocked unsafe image URL (SSRF protection)")
            return await super().send_image(chat_id, image_url, caption, reply_to, metadata=metadata)

        try:
            import httpx

            async def _ssrf_redirect_guard(response):
                """重新检查重定向目标，防止公开 URL 跳转到私有 IP。"""
                if response.is_redirect and response.next_request:
                    redirect_url = str(response.next_request.url)
                    if not is_safe_url(redirect_url):
                        raise ValueError("Blocked redirect to private/internal address")

            # 先下载图片
            async with httpx.AsyncClient(
                timeout=30.0,
                follow_redirects=True,
                event_hooks={"response": [_ssrf_redirect_guard]},
            ) as client:
                response = await client.get(image_url)
                response.raise_for_status()

            result = await self._get_client(chat_id).files_upload_v2(
                channel=chat_id,
                content=response.content,
                filename="image.png",
                initial_comment=caption or "",
                thread_ts=self._resolve_thread_ts(reply_to, metadata),
            )

            return SendResult(success=True, raw_response=result)

        except Exception as e:  # pragma: no cover - defensive logging
            logger.warning(
                "[Slack] Failed to upload image from URL %s, falling back to text: %s",
                safe_url_for_log(image_url),
                e,
                exc_info=True,
            )
            # 回退到以文本方式发送 URL
            text = f"{caption}\n{image_url}" if caption else image_url
            return await self.send(chat_id=chat_id, content=text, reply_to=reply_to)

    async def send_voice(
        self,
        chat_id: str,
        audio_path: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> SendResult:
        """向 Slack 发送音频文件。"""
        try:
            return await self._upload_file(chat_id, audio_path, caption, reply_to, metadata)
        except FileNotFoundError:
            return SendResult(success=False, error=f"Audio file not found: {audio_path}")
        except Exception as e:  # pragma: no cover - defensive logging
            logger.error(
                "[Slack] Failed to send audio file %s: %s",
                audio_path,
                e,
                exc_info=True,
            )
            return SendResult(success=False, error=str(e))

    async def send_video(
        self,
        chat_id: str,
        video_path: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """向 Slack 发送视频文件。"""
        if not self._app:
            return SendResult(success=False, error="Not connected")

        if not os.path.exists(video_path):
            return SendResult(success=False, error=f"Video file not found: {video_path}")

        try:
            result = await self._get_client(chat_id).files_upload_v2(
                channel=chat_id,
                file=video_path,
                filename=os.path.basename(video_path),
                initial_comment=caption or "",
                thread_ts=self._resolve_thread_ts(reply_to, metadata),
            )
            return SendResult(success=True, raw_response=result)

        except Exception as e:  # pragma: no cover - defensive logging
            logger.error(
                "[%s] Failed to send video %s: %s",
                self.name,
                video_path,
                e,
                exc_info=True,
            )
            text = f"🎬 Video: {video_path}"
            if caption:
                text = f"{caption}\n{text}"
            return await self.send(chat_id, text, reply_to=reply_to, metadata=metadata)

    async def send_document(
        self,
        chat_id: str,
        file_path: str,
        caption: Optional[str] = None,
        file_name: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """向 Slack 发送文档/文件附件。"""
        if not self._app:
            return SendResult(success=False, error="Not connected")

        if not os.path.exists(file_path):
            return SendResult(success=False, error=f"File not found: {file_path}")

        display_name = file_name or os.path.basename(file_path)

        try:
            result = await self._get_client(chat_id).files_upload_v2(
                channel=chat_id,
                file=file_path,
                filename=display_name,
                initial_comment=caption or "",
                thread_ts=self._resolve_thread_ts(reply_to, metadata),
            )
            return SendResult(success=True, raw_response=result)

        except Exception as e:  # pragma: no cover - defensive logging
            logger.error(
                "[%s] Failed to send document %s: %s",
                self.name,
                file_path,
                e,
                exc_info=True,
            )
            text = f"📎 File: {file_path}"
            if caption:
                text = f"{caption}\n{text}"
            return await self.send(chat_id, text, reply_to=reply_to, metadata=metadata)

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        """获取 Slack 频道的信息。"""
        if not self._app:
            return {"name": chat_id, "type": "unknown"}

        try:
            result = await self._get_client(chat_id).conversations_info(channel=chat_id)
            channel = result.get("channel", {})
            is_dm = channel.get("is_im", False)
            return {
                "name": channel.get("name", chat_id),
                "type": "dm" if is_dm else "group",
            }
        except Exception as e:  # pragma: no cover - defensive logging
            logger.error(
                "[Slack] Failed to fetch chat info for %s: %s",
                chat_id,
                e,
                exc_info=True,
            )
            return {"name": chat_id, "type": "unknown"}

    # ----- 内部处理器 -----

    def _assistant_thread_key(self, channel_id: str, thread_ts: str) -> Optional[Tuple[str, str]]:
        """返回 Slack 助手线程元数据的稳定缓存键。"""
        if not channel_id or not thread_ts:
            return None
        return (str(channel_id), str(thread_ts))

    def _extract_assistant_thread_metadata(self, event: dict) -> Dict[str, str]:
        """从事件载荷中提取 Slack 助手线程的身份数据。"""
        assistant_thread = event.get("assistant_thread") or {}
        context = assistant_thread.get("context") or event.get("context") or {}

        channel_id = (
            assistant_thread.get("channel_id")
            or event.get("channel")
            or context.get("channel_id")
            or ""
        )
        thread_ts = (
            assistant_thread.get("thread_ts")
            or event.get("thread_ts")
            or event.get("message_ts")
            or ""
        )
        user_id = (
            assistant_thread.get("user_id")
            or event.get("user")
            or context.get("user_id")
            or ""
        )
        team_id = (
            event.get("team")
            or event.get("team_id")
            or assistant_thread.get("team_id")
            or ""
        )
        context_channel_id = context.get("channel_id") or ""

        return {
            "channel_id": str(channel_id) if channel_id else "",
            "thread_ts": str(thread_ts) if thread_ts else "",
            "user_id": str(user_id) if user_id else "",
            "team_id": str(team_id) if team_id else "",
            "context_channel_id": str(context_channel_id) if context_channel_id else "",
        }

    def _cache_assistant_thread_metadata(self, metadata: Dict[str, str]) -> None:
        """记住助手线程的身份数据，供后续消息事件使用。"""
        channel_id = metadata.get("channel_id", "")
        thread_ts = metadata.get("thread_ts", "")
        key = self._assistant_thread_key(channel_id, thread_ts)
        if not key:
            return

        existing = self._assistant_threads.get(key, {})
        merged = dict(existing)
        merged.update({k: v for k, v in metadata.items() if v})
        self._assistant_threads[key] = merged

        # 当缓存超出限制时淘汰最旧的条目
        if len(self._assistant_threads) > self._ASSISTANT_THREADS_MAX:
            excess = len(self._assistant_threads) - self._ASSISTANT_THREADS_MAX // 2
            for old_key in list(self._assistant_threads)[:excess]:
                del self._assistant_threads[old_key]

        team_id = merged.get("team_id", "")
        if team_id and channel_id:
            self._channel_team[channel_id] = team_id

    def _lookup_assistant_thread_metadata(
        self,
        event: dict,
        channel_id: str = "",
        thread_ts: str = "",
    ) -> Dict[str, str]:
        """加载与当前事件匹配的已缓存助手线程元数据。"""
        metadata = self._extract_assistant_thread_metadata(event)
        if channel_id and not metadata.get("channel_id"):
            metadata["channel_id"] = channel_id
        if thread_ts and not metadata.get("thread_ts"):
            metadata["thread_ts"] = thread_ts

        key = self._assistant_thread_key(
            metadata.get("channel_id", ""),
            metadata.get("thread_ts", ""),
        )
        cached = self._assistant_threads.get(key, {}) if key else {}
        if cached:
            merged = dict(cached)
            merged.update({k: v for k, v in metadata.items() if v})
            return merged
        return metadata

    def _seed_assistant_thread_session(self, metadata: Dict[str, str]) -> None:
        """预初始化会话存储，使助手线程获得稳定的用户作用域。"""
        session_store = getattr(self, "_session_store", None)
        if not session_store:
            return

        channel_id = metadata.get("channel_id", "")
        thread_ts = metadata.get("thread_ts", "")
        user_id = metadata.get("user_id", "")
        if not channel_id or not thread_ts or not user_id:
            return

        source = self.build_source(
            chat_id=channel_id,
            chat_name=channel_id,
            chat_type="dm",
            user_id=user_id,
            thread_id=thread_ts,
            chat_topic=metadata.get("context_channel_id") or None,
        )

        try:
            session_store.get_or_create_session(source)
        except Exception:
            logger.debug(
                "[Slack] Failed to seed assistant thread session for %s/%s",
                channel_id,
                thread_ts,
                exc_info=True,
            )

    async def _handle_assistant_thread_lifecycle_event(self, event: dict) -> None:
        """处理携带用户/线程身份的 Slack 助手生命周期事件。"""
        metadata = self._extract_assistant_thread_metadata(event)
        self._cache_assistant_thread_metadata(metadata)
        self._seed_assistant_thread_session(metadata)

    async def _handle_slack_message(self, event: dict) -> None:
        """处理收到的 Slack 消息事件。"""
        # 去重：Slack Socket Mode 在重连后可能重复投递事件（#4777）
        event_ts = event.get("ts", "")
        if event_ts and self._dedup.is_duplicate(event_ts):
            return

        # Bot 消息过滤（SLACK_ALLOW_BOTS / config allow_bots）：
        #   "none"     — 忽略所有 Bot 消息（默认，向后兼容）
        #   "mentions" — 仅接受 @提及 我们的 Bot 消息
        #   "all"      — 接受所有 Bot 消息（除了我们自己的）
        if event.get("bot_id") or event.get("subtype") == "bot_message":
            allow_bots = self.config.extra.get("allow_bots", "")
            if not allow_bots:
                allow_bots = os.getenv("SLACK_ALLOW_BOTS", "none")
            allow_bots = str(allow_bots).lower().strip()
            if allow_bots == "none":
                return
            elif allow_bots == "mentions":
                text_check = event.get("text", "")
                if self._bot_user_id and f"<@{self._bot_user_id}>" not in text_check:
                    return
            # "all" 直接进入消息处理流程
            # 始终忽略自己的消息以防止回声循环
            msg_user = event.get("user", "")
            if msg_user and self._bot_user_id and msg_user == self._bot_user_id:
                return

        # 忽略消息编辑和删除
        subtype = event.get("subtype")
        if subtype in ("message_changed", "message_deleted"):
            return

        text = event.get("text", "")
        channel_id = event.get("channel", "")
        ts = event.get("ts", "")
        assistant_meta = self._lookup_assistant_thread_metadata(
            event,
            channel_id=channel_id,
            thread_ts=event.get("thread_ts", ""),
        )
        user_id = event.get("user") or assistant_meta.get("user_id", "")
        if not channel_id:
            channel_id = assistant_meta.get("channel_id", "")
        team_id = (
            event.get("team")
            or event.get("team_id")
            or assistant_meta.get("team_id", "")
        )

        # 跟踪哪个工作空间拥有此频道
        if team_id and channel_id:
            self._channel_team[channel_id] = team_id

        # 判断是私信还是频道消息
        channel_type = event.get("channel_type", "")
        if not channel_type and channel_id.startswith("D"):
            channel_type = "im"
        is_dm = channel_type in ("im", "mpim")  # 1:1 和群组私信

        # 构建用于会话键的 thread_ts。
        # 在频道中：回退到 ts，这样每个顶级 @提及 都会启动新的线程/会话（Bot 总是在线程中回复）。
        # 在私信中：回退到 ts，这样每个顶级私信回复线程都有自己的会话键（与频道行为一致）。
        # 在 config 中设置 dm_top_level_threads_as_sessions: false 可恢复旧版单会话行为。
        if is_dm:
            thread_ts = event.get("thread_ts") or assistant_meta.get("thread_ts")
            if not thread_ts and self._dm_top_level_threads_as_sessions():
                thread_ts = ts
        else:
            thread_ts = event.get("thread_ts") or ts  # 频道使用 ts 回退

        # 在频道中，在以下情况下响应：
        #   0. 频道在 free_response_channels 中，或 require_mention 被禁用——始终处理。
        #   1. 此消息中 Bot 被 @提及，或
        #   2. 消息是 Bot 启动/参与的线程中的回复，或
        #   3. 消息在 Bot 之前被 @提及 的线程中，或
        #   4. 此线程有现有会话（重启后仍有效）
        bot_uid = self._team_bot_user_ids.get(team_id, self._bot_user_id)
        is_mentioned = bot_uid and f"<@{bot_uid}>" in text
        event_thread_ts = event.get("thread_ts")
        is_thread_reply = bool(event_thread_ts and event_thread_ts != ts)

        if not is_dm and bot_uid:
            if channel_id in self._slack_free_response_channels():
                pass  # 自由回复频道——始终处理
            elif not self._slack_require_mention():
                pass  # Slack 的 @提及 要求已全局禁用
            elif not is_mentioned:
                reply_to_bot_thread = (
                    is_thread_reply and event_thread_ts in self._bot_message_ts
                )
                in_mentioned_thread = (
                    event_thread_ts is not None
                    and event_thread_ts in self._mentioned_threads
                )
                has_session = (
                    is_thread_reply
                    and self._has_active_session_for_thread(
                        channel_id=channel_id,
                        thread_ts=event_thread_ts,
                        user_id=user_id,
                    )
                )
                if not reply_to_bot_thread and not in_mentioned_thread and not has_session:
                    return

        if is_mentioned:
            # 从文本中移除 Bot @提及
            text = text.replace(f"<@{bot_uid}>", "").strip()
            # 注册此线程，使后续所有消息自动触发 Bot
            if event_thread_ts:
                self._mentioned_threads.add(event_thread_ts)
                if len(self._mentioned_threads) > self._MENTIONED_THREADS_MAX:
                    to_remove = list(self._mentioned_threads)[:self._MENTIONED_THREADS_MAX // 2]
                    for t in to_remove:
                        self._mentioned_threads.discard(t)

        # 首次进入线程时（无现有会话），获取线程上下文让 Agent 理解对话。
        if is_thread_reply and not self._has_active_session_for_thread(
            channel_id=channel_id,
            thread_ts=event_thread_ts,
            user_id=user_id,
        ):
            thread_context = await self._fetch_thread_context(
                channel_id=channel_id,
                thread_ts=event_thread_ts,
                current_ts=ts,
                team_id=team_id,
            )
            if thread_context:
                text = thread_context + text

        # 确定消息类型
        msg_type = MessageType.TEXT
        if text.startswith("/"):
            msg_type = MessageType.COMMAND

        # 处理文件附件
        media_urls = []
        media_types = []
        files = event.get("files", [])
        for f in files:
            mimetype = f.get("mimetype", "unknown")
            url = f.get("url_private_download") or f.get("url_private", "")
            if mimetype.startswith("image/") and url:
                try:
                    ext = "." + mimetype.split("/")[-1].split(";")[0]
                    if ext not in (".jpg", ".jpeg", ".png", ".gif", ".webp"):
                        ext = ".jpg"
                    # Slack 私有 URL 需要 Bot 令牌作为认证头
                    cached = await self._download_slack_file(url, ext, team_id=team_id)
                    media_urls.append(cached)
                    media_types.append(mimetype)
                    msg_type = MessageType.PHOTO
                except Exception as e:  # pragma: no cover - defensive logging
                    logger.warning("[Slack] Failed to cache image from %s: %s", url, e, exc_info=True)
            elif mimetype.startswith("audio/") and url:
                try:
                    ext = "." + mimetype.split("/")[-1].split(";")[0]
                    if ext not in (".ogg", ".mp3", ".wav", ".webm", ".m4a"):
                        ext = ".ogg"
                    cached = await self._download_slack_file(url, ext, audio=True, team_id=team_id)
                    media_urls.append(cached)
                    media_types.append(mimetype)
                    msg_type = MessageType.VOICE
                except Exception as e:  # pragma: no cover - defensive logging
                    logger.warning("[Slack] Failed to cache audio from %s: %s", url, e, exc_info=True)
            elif url:
                    # 尝试作为文档附件处理
                try:
                    original_filename = f.get("name", "")
                    ext = ""
                    if original_filename:
                        _, ext = os.path.splitext(original_filename)
                        ext = ext.lower()

                    # 兜底：从 MIME 类型反向查找扩展名
                    if not ext and mimetype:
                        mime_to_ext = {v: k for k, v in SUPPORTED_DOCUMENT_TYPES.items()}
                        ext = mime_to_ext.get(mimetype, "")

                    if ext not in SUPPORTED_DOCUMENT_TYPES:
                        continue  # 静默跳过不支持的文件类型

                    # 检查文件大小（Slack 限制：Bot 最大 20 MB）
                    file_size = f.get("size", 0)
                    MAX_DOC_BYTES = 20 * 1024 * 1024
                    if not file_size or file_size > MAX_DOC_BYTES:
                        logger.warning("[Slack] Document too large or unknown size: %s", file_size)
                        continue

                    # 下载并缓存
                    raw_bytes = await self._download_slack_file_bytes(url, team_id=team_id)
                    cached_path = cache_document_from_bytes(
                        raw_bytes, original_filename or f"document{ext}"
                    )
                    doc_mime = SUPPORTED_DOCUMENT_TYPES[ext]
                    media_urls.append(cached_path)
                    media_types.append(doc_mime)
                    msg_type = MessageType.DOCUMENT
                    logger.debug("[Slack] Cached user document: %s", cached_path)

                    # 为 .txt/.md 文件注入文本内容（上限 100 KB）
                    MAX_TEXT_INJECT_BYTES = 100 * 1024
                    if ext in (".md", ".txt") and len(raw_bytes) <= MAX_TEXT_INJECT_BYTES:
                        try:
                            text_content = raw_bytes.decode("utf-8")
                            display_name = original_filename or f"document{ext}"
                            display_name = re.sub(r'[^\w.\- ]', '_', display_name)
                            injection = f"[Content of {display_name}]:\n{text_content}"
                            if text:
                                text = f"{injection}\n\n{text}"
                            else:
                                text = injection
                        except UnicodeDecodeError:
                            pass  # 二进制内容，跳过注入

                except Exception as e:  # pragma: no cover - defensive logging
                    logger.warning("[Slack] Failed to cache document from %s: %s", url, e, exc_info=True)

        # 解析用户显示名称（首次查找后缓存）
        user_name = await self._resolve_user_name(user_id, chat_id=channel_id)

        # 构建消息来源
        source = self.build_source(
            chat_id=channel_id,
            chat_name=channel_id,  # 如有需要将在后续解析
            chat_type="dm" if is_dm else "group",
            user_id=user_id,
            user_name=user_name,
            thread_id=thread_ts,
        )

        # 每频道临时提示词
        from gateway.platforms.base import resolve_channel_prompt
        _channel_prompt = resolve_channel_prompt(
            self.config.extra, channel_id, None,
        )

        msg_event = MessageEvent(
            text=text,
            message_type=msg_type,
            source=source,
            raw_message=event,
            message_id=ts,
            media_urls=media_urls,
            media_types=media_types,
            reply_to_message_id=thread_ts if thread_ts != ts else None,
            channel_prompt=_channel_prompt,
        )

        # 仅在 Bot 被直接寻址时（私信或 @提及）添加反应。
        # 在全监听频道（require_mention=false）中，对每条普通消息添加反应会很嘈杂。
        _should_react = is_dm or is_mentioned

        if _should_react:
            await self._add_reaction(channel_id, ts, "eyes")

        await self.handle_message(msg_event)

        if _should_react:
            await self._remove_reaction(channel_id, ts, "eyes")
            await self._add_reaction(channel_id, ts, "white_check_mark")

    # ----- 审批按钮支持（Block Kit）-----

    async def send_exec_approval(
        self, chat_id: str, command: str, session_key: str,
        description: str = "dangerous command",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """发送带交互式按钮的 Block Kit 审批提示。

        按钮调用 ``resolve_gateway_approval()`` 来解除等待中的
        Agent 线程阻塞——与文本 ``/approve`` 流程使用相同的机制。
        """
        if not self._app:
            return SendResult(success=False, error="Not connected")

        try:
            cmd_preview = command[:2900] + "..." if len(command) > 2900 else command
            thread_ts = self._resolve_thread_ts(None, metadata)

            blocks = [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f":warning: *Command Approval Required*\n"
                            f"```{cmd_preview}```\n"
                            f"Reason: {description}"
                        ),
                    },
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Allow Once"},
                            "style": "primary",
                            "action_id": "hermes_approve_once",
                            "value": session_key,
                        },
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Allow Session"},
                            "action_id": "hermes_approve_session",
                            "value": session_key,
                        },
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Always Allow"},
                            "action_id": "hermes_approve_always",
                            "value": session_key,
                        },
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Deny"},
                            "style": "danger",
                            "action_id": "hermes_deny",
                            "value": session_key,
                        },
                    ],
                },
            ]

            kwargs: Dict[str, Any] = {
                "channel": chat_id,
                "text": f"⚠️ Command approval required: {cmd_preview[:100]}",
                "blocks": blocks,
            }
            if thread_ts:
                kwargs["thread_ts"] = thread_ts

            result = await self._get_client(chat_id).chat_postMessage(**kwargs)
            msg_ts = result.get("ts", "")
            if msg_ts:
                self._approval_resolved[msg_ts] = False

            return SendResult(success=True, message_id=msg_ts, raw_response=result)
        except Exception as e:
            logger.error("[Slack] send_exec_approval failed: %s", e, exc_info=True)
            return SendResult(success=False, error=str(e))

    async def _handle_approval_action(self, ack, body, action) -> None:
        """处理 Block Kit 中的审批按钮点击。"""
        await ack()

        action_id = action.get("action_id", "")
        session_key = action.get("value", "")
        message = body.get("message", {})
        msg_ts = message.get("ts", "")
        channel_id = body.get("channel", {}).get("id", "")
        user_name = body.get("user", {}).get("name", "unknown")
        user_id = body.get("user", {}).get("id", "")

        # 只有授权用户可以点击审批按钮。按钮点击绕过了
        # gateway/run.py 中正常的消息认证流程，所以必须在此检查。
        allowed_csv = os.getenv("SLACK_ALLOWED_USERS", "").strip()
        if allowed_csv:
            allowed_ids = {uid.strip() for uid in allowed_csv.split(",") if uid.strip()}
            if "*" not in allowed_ids and user_id not in allowed_ids:
                logger.warning(
                    "[Slack] Unauthorized approval click by %s (%s) — ignoring",
                    user_name, user_id,
                )
                return

        # 将 action_id 映射到审批选项
        choice_map = {
            "hermes_approve_once": "once",
            "hermes_approve_session": "session",
            "hermes_approve_always": "always",
            "hermes_deny": "deny",
        }
        choice = choice_map.get(action_id, "deny")

        # 防止重复点击——原子性 pop；第一个调用者获取 False，其他获取 True（默认值）
        if self._approval_resolved.pop(msg_ts, True):
            return

        # 更新消息以显示决定结果并移除按钮
        label_map = {
            "once": f"✅ Approved once by {user_name}",
            "session": f"✅ Approved for session by {user_name}",
            "always": f"✅ Approved permanently by {user_name}",
            "deny": f"❌ Denied by {user_name}",
        }
        decision_text = label_map.get(choice, f"Resolved by {user_name}")

        # 获取 section block 中的原始文本
        original_text = ""
        for block in message.get("blocks", []):
            if block.get("type") == "section":
                original_text = block.get("text", {}).get("text", "")
                break

        updated_blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": original_text or "Command approval request",
                },
            },
            {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": decision_text},
                ],
            },
        ]

        try:
            await self._get_client(channel_id).chat_update(
                channel=channel_id,
                ts=msg_ts,
                text=decision_text,
                blocks=updated_blocks,
            )
        except Exception as e:
            logger.warning("[Slack] Failed to update approval message: %s", e)

        # 解决审批——这将解除 Agent 线程的阻塞
        try:
            from tools.approval import resolve_gateway_approval
            count = resolve_gateway_approval(session_key, choice)
            logger.info(
                "Slack button resolved %d approval(s) for session %s (choice=%s, user=%s)",
                count, session_key, choice, user_name,
            )
        except Exception as exc:
            logger.error("Failed to resolve gateway approval from Slack button: %s", exc)

        # （审批状态已在上面的原子 pop 中消耗）

    # ----- 线程上下文获取 -----

    async def _fetch_thread_context(
        self, channel_id: str, thread_ts: str, current_ts: str,
        team_id: str = "", limit: int = 30,
    ) -> str:
        """当 Bot 首次在线程中被 @提及 时，获取最近的线程消息以提供上下文。

        此方法仅在线程没有活动会话时调用（由调用处的
        _has_active_session_for_thread 保护）。该保护确保线程消息
        仅在第一次对话轮次中被前置——之后会话历史已经包含了它们，
        所以不会在后续轮次中产生重复。

        结果按线程缓存 _THREAD_CACHE_TTL 秒，避免频繁调用
        conversations.replies（Tier 3，约 50 请求/分钟）。

        返回带有之前线程历史的格式化字符串，失败或线程无先前消息时返回空字符串。
        """
        cache_key = f"{channel_id}:{thread_ts}"
        now = time.monotonic()
        cached = self._thread_context_cache.get(cache_key)
        if cached and (now - cached.fetched_at) < self._THREAD_CACHE_TTL:
            return cached.content

        try:
            client = self._get_client(channel_id)

            # 对 Tier-3 限速（429）进行指数退避重试。
            result = None
            for attempt in range(3):
                try:
                    result = await client.conversations_replies(
                        channel=channel_id,
                        ts=thread_ts,
                        limit=limit + 1,  # +1 because it includes the current message
                        inclusive=True,
                    )
                    break
                except Exception as exc:
                    # 检查来自 slack_sdk 的限速错误
                    err_str = str(exc).lower()
                    is_rate_limit = (
                        "ratelimited" in err_str
                        or "429" in err_str
                        or "rate_limited" in err_str
                    )
                    if is_rate_limit and attempt < 2:
                        retry_after = 1.0 * (2 ** attempt)  # 1s, 2s
                        logger.warning(
                            "[Slack] conversations.replies rate limited; retrying in %.1fs (attempt %d/3)",
                            retry_after, attempt + 1,
                        )
                        await asyncio.sleep(retry_after)
                        continue
                    raise

            if result is None:
                return ""

            messages = result.get("messages", [])
            if not messages:
                return ""

            bot_uid = self._team_bot_user_ids.get(team_id, self._bot_user_id)
            context_parts = []
            for msg in messages:
                msg_ts = msg.get("ts", "")
                # 排除当前触发消息——它将作为用户消息本身投递，
                # 包含在此处会导致重复。
                if msg_ts == current_ts:
                    continue
                # 排除我们自己的 Bot 消息以避免循环上下文。
                if msg.get("bot_id") or msg.get("subtype") == "bot_message":
                    continue

                msg_text = msg.get("text", "").strip()
                if not msg_text:
                    continue

                # 从上下文消息中移除 Bot @提及
                if bot_uid:
                    msg_text = msg_text.replace(f"<@{bot_uid}>", "").strip()

                msg_user = msg.get("user", "unknown")
                is_parent = msg_ts == thread_ts
                prefix = "[thread parent] " if is_parent else ""
                name = await self._resolve_user_name(msg_user, chat_id=channel_id)
                context_parts.append(f"{prefix}{name}: {msg_text}")

            content = ""
            if context_parts:
                content = (
                    "[Thread context — prior messages in this thread (not yet in conversation history):]\n"
                    + "\n".join(context_parts)
                    + "\n[End of thread context]\n\n"
                )

            self._thread_context_cache[cache_key] = _ThreadContextCache(
                content=content,
                fetched_at=now,
                message_count=len(context_parts),
            )
            return content

        except Exception as e:
            logger.warning("[Slack] Failed to fetch thread context: %s", e)
            return ""

    async def _handle_slash_command(self, command: dict) -> None:
        """处理 /hermes 斜杠命令。"""
        text = command.get("text", "").strip()
        user_id = command.get("user_id", "")
        channel_id = command.get("channel_id", "")
        team_id = command.get("team_id", "")

        # 跟踪哪个工作空间拥有此频道
        if team_id and channel_id:
            self._channel_team[channel_id] = team_id

        # 将子命令映射到网关命令——从中央注册表派生。
        # 同时保留 "compact" 作为 /compress 的 Slack 专用别名。
        from hermes_cli.commands import slack_subcommand_map
        subcommand_map = slack_subcommand_map()
        subcommand_map["compact"] = "/compress"
        first_word = text.split()[0] if text else ""
        if first_word in subcommand_map:
            # 保留子命令后的参数
            rest = text[len(first_word):].strip()
            text = f"{subcommand_map[first_word]} {rest}".strip() if rest else subcommand_map[first_word]
        elif text:
            pass  # 作为普通问题处理
        else:
            text = "/help"

        source = self.build_source(
            chat_id=channel_id,
            chat_type="dm",  # 斜杠命令始终在类似私信的上下文中
            user_id=user_id,
        )

        event = MessageEvent(
            text=text,
            message_type=MessageType.COMMAND if text.startswith("/") else MessageType.TEXT,
            source=source,
            raw_message=command,
        )

        await self.handle_message(event)

    def _has_active_session_for_thread(
        self,
        channel_id: str,
        thread_ts: str,
        user_id: str,
    ) -> bool:
        """检查线程是否有活动会话。

        用于判断没有 @提及 的线程回复是否应被处理
        （如果有活动会话则应处理）。

        使用 ``build_session_key()`` 作为键构建的唯一真实来源——
        避免手动构建键时未正确遵循 ``thread_sessions_per_user``
        和 ``group_sessions_per_user`` 设置的 bug。
        """
        session_store = getattr(self, "_session_store", None)
        if not session_store:
            return False

        try:
            from gateway.session import SessionSource, build_session_key

            source = SessionSource(
                platform=Platform.SLACK,
                chat_id=channel_id,
                chat_type="group",
                user_id=user_id,
                thread_id=thread_ts,
            )

            # 从 store 的配置中读取会话隔离设置
            store_cfg = getattr(session_store, "config", None)
            gspu = getattr(store_cfg, "group_sessions_per_user", True) if store_cfg else True
            tspu = getattr(store_cfg, "thread_sessions_per_user", False) if store_cfg else False

            session_key = build_session_key(
                source,
                group_sessions_per_user=gspu,
                thread_sessions_per_user=tspu,
            )

            session_store._ensure_loaded()
            return session_key in session_store._entries
        except Exception:
            return False

    async def _download_slack_file(self, url: str, ext: str, audio: bool = False, team_id: str = "") -> str:
        """使用 Bot 令牌认证下载 Slack 文件，带重试。"""
        import asyncio
        import httpx

        bot_token = self._team_clients[team_id].token if team_id and team_id in self._team_clients else self.config.token
        last_exc = None

        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            for attempt in range(3):
                try:
                    response = await client.get(
                        url,
                        headers={"Authorization": f"Bearer {bot_token}"},
                    )
                    response.raise_for_status()

                    # Slack 可能返回 HTML 登录/重定向页面而非实际媒体字节
                    # （例如令牌过期、文件访问受限）。
                    # 早期检测以避免缓存错误数据并混淆下游工具。
                    ct = response.headers.get("content-type", "")
                    if "text/html" in ct:
                        raise ValueError(
                            "Slack returned HTML instead of media "
                            f"(content-type: {ct}); "
                            "check bot token scopes and file permissions"
                        )

                    if audio:
                        from gateway.platforms.base import cache_audio_from_bytes
                        return cache_audio_from_bytes(response.content, ext)
                    else:
                        from gateway.platforms.base import cache_image_from_bytes
                        return cache_image_from_bytes(response.content, ext)
                except (httpx.TimeoutException, httpx.HTTPStatusError) as exc:
                    last_exc = exc
                    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code < 429:
                        raise
                    if attempt < 2:
                        logger.debug("Slack file download retry %d/2 for %s: %s",
                                     attempt + 1, url[:80], exc)
                        await asyncio.sleep(1.5 * (attempt + 1))
                        continue
                    raise
        raise last_exc

    async def _download_slack_file_bytes(self, url: str, team_id: str = "") -> bytes:
        """下载 Slack 文件并返回原始字节，带重试。"""
        import asyncio
        import httpx

        bot_token = self._team_clients[team_id].token if team_id and team_id in self._team_clients else self.config.token
        last_exc = None

        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            for attempt in range(3):
                try:
                    response = await client.get(
                        url,
                        headers={"Authorization": f"Bearer {bot_token}"},
                    )
                    response.raise_for_status()
                    return response.content
                except (httpx.TimeoutException, httpx.HTTPStatusError) as exc:
                    last_exc = exc
                    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code < 429:
                        raise
                    if attempt < 2:
                        logger.debug("Slack file download retry %d/2 for %s: %s",
                                     attempt + 1, url[:80], exc)
                        await asyncio.sleep(1.5 * (attempt + 1))
                        continue
                    raise
        raise last_exc

    # ── 频道 @提及 门控 ─────────────────────────────────────────────

    def _slack_require_mention(self) -> bool:
        """返回频道消息是否需要显式的 Bot @提及。

        使用显式 false 解析（与 Discord/Matrix 一致），而非
        真值解析，因为安全默认值是 True（启用门控）。
        无法识别或空值将保持门控启用。
        """
        configured = self.config.extra.get("require_mention")
        if configured is not None:
            if isinstance(configured, str):
                return configured.lower() not in ("false", "0", "no", "off")
            return bool(configured)
        return os.getenv("SLACK_REQUIRE_MENTION", "true").lower() not in ("false", "0", "no", "off")

    def _slack_free_response_channels(self) -> set:
        """返回无需 @提及 的频道 ID 集合。"""
        raw = self.config.extra.get("free_response_channels")
        if raw is None:
            raw = os.getenv("SLACK_FREE_RESPONSE_CHANNELS", "")
        if isinstance(raw, list):
            return {str(part).strip() for part in raw if str(part).strip()}
        if isinstance(raw, str) and raw.strip():
            return {part.strip() for part in raw.split(",") if part.strip()}
        return set()
