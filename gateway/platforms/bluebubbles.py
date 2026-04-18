"""BlueBubbles iMessage 平台适配器。

使用本地 BlueBubbles macOS 服务器进行出站 REST 发送和入站
Webhook 接收。支持文本消息、媒体附件（图片、语音、视频、
文档）、Tapback 表情回应、输入指示器和已读回执。

架构基于 PR #5869 (benjaminsehl)，入站附件下载
来自 PR #4588 (YuhangLin)。
"""

import asyncio
import json
import logging
import os
import re
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import httpx

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import (
    BasePlatformAdapter,
    MessageEvent,
    MessageType,
    SendResult,
    cache_image_from_bytes,
    cache_audio_from_bytes,
    cache_document_from_bytes,
)
from gateway.platforms.helpers import strip_markdown

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

DEFAULT_WEBHOOK_HOST = "127.0.0.1"
DEFAULT_WEBHOOK_PORT = 8645
DEFAULT_WEBHOOK_PATH = "/bluebubbles-webhook"
MAX_TEXT_LENGTH = 4000

# Tapback 表情回应代码（BlueBubbles associatedMessageType 值）
_TAPBACK_ADDED = {
    2000: "love", 2001: "like", 2002: "dislike",
    2003: "laugh", 2004: "emphasize", 2005: "question",
}
_TAPBACK_REMOVED = {
    3000: "love", 3001: "like", 3002: "dislike",
    3003: "laugh", 3004: "emphasize", 3005: "question",
}

# 携带用户消息的 Webhook 事件类型
_MESSAGE_EVENTS = {"new-message", "message", "updated-message"}

# 日志脱敏正则表达式
_PHONE_RE = re.compile(r"\+?\d{7,15}")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")


def _redact(text: str) -> str:
    """从日志输出中脱敏电话号码和邮箱地址。"""
    text = _PHONE_RE.sub("[REDACTED]", text)
    text = _EMAIL_RE.sub("[REDACTED]", text)
    return text


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def check_bluebubbles_requirements() -> bool:
    try:
        import aiohttp  # noqa: F401
        import httpx as _httpx  # noqa: F401
    except ImportError:
        return False
    return True


def _normalize_server_url(raw: str) -> str:
    value = (raw or "").strip()
    if not value:
        return ""
    if not re.match(r"^https?://", value, flags=re.I):
        value = f"http://{value}"
    return value.rstrip("/")





# ---------------------------------------------------------------------------
# 适配器
# ---------------------------------------------------------------------------

class BlueBubblesAdapter(BasePlatformAdapter):
    platform = Platform.BLUEBUBBLES
    MAX_MESSAGE_LENGTH = MAX_TEXT_LENGTH

    def __init__(self, config: PlatformConfig):
        super().__init__(config, Platform.BLUEBUBBLES)
        extra = config.extra or {}
        self.server_url = _normalize_server_url(
            extra.get("server_url") or os.getenv("BLUEBUBBLES_SERVER_URL", "")
        )
        self.password = extra.get("password") or os.getenv("BLUEBUBBLES_PASSWORD", "")
        self.webhook_host = (
            extra.get("webhook_host")
            or os.getenv("BLUEBUBBLES_WEBHOOK_HOST", DEFAULT_WEBHOOK_HOST)
        )
        self.webhook_port = int(
            extra.get("webhook_port")
            or os.getenv("BLUEBUBBLES_WEBHOOK_PORT", str(DEFAULT_WEBHOOK_PORT))
        )
        self.webhook_path = (
            extra.get("webhook_path")
            or os.getenv("BLUEBUBBLES_WEBHOOK_PATH", DEFAULT_WEBHOOK_PATH)
        )
        if not str(self.webhook_path).startswith("/"):
            self.webhook_path = f"/{self.webhook_path}"
        self.send_read_receipts = bool(extra.get("send_read_receipts", True))
        self.client: Optional[httpx.AsyncClient] = None
        self._runner = None
        self._private_api_enabled: Optional[bool] = None
        self._helper_connected: bool = False
        self._guid_cache: Dict[str, str] = {}

    # ------------------------------------------------------------------
    # API 辅助方法
    # ------------------------------------------------------------------

    def _api_url(self, path: str) -> str:
        sep = "&" if "?" in path else "?"
        return f"{self.server_url}{path}{sep}password={quote(self.password, safe='')}"

    async def _api_get(self, path: str) -> Dict[str, Any]:
        assert self.client is not None
        res = await self.client.get(self._api_url(path))
        res.raise_for_status()
        return res.json()

    async def _api_post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        assert self.client is not None
        res = await self.client.post(self._api_url(path), json=payload)
        res.raise_for_status()
        return res.json()

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def connect(self) -> bool:
        if not self.server_url or not self.password:
            logger.error(
                "[bluebubbles] BLUEBUBBLES_SERVER_URL and BLUEBUBBLES_PASSWORD are required"
            )
            return False
        from aiohttp import web

        self.client = httpx.AsyncClient(timeout=30.0)
        try:
            await self._api_get("/api/v1/ping")
            info = await self._api_get("/api/v1/server/info")
            server_data = (info or {}).get("data", {})
            self._private_api_enabled = bool(server_data.get("private_api"))
            self._helper_connected = bool(server_data.get("helper_connected"))
            logger.info(
                "[bluebubbles] connected to %s (private_api=%s, helper=%s)",
                self.server_url,
                self._private_api_enabled,
                self._helper_connected,
            )
        except Exception as exc:
            logger.error(
                "[bluebubbles] cannot reach server at %s: %s", self.server_url, exc
            )
            if self.client:
                await self.client.aclose()
                self.client = None
            return False

        app = web.Application()
        app.router.add_get("/health", lambda _: web.Response(text="ok"))
        app.router.add_post(self.webhook_path, self._handle_webhook)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.webhook_host, self.webhook_port)
        await site.start()
        self._mark_connected()
        logger.info(
            "[bluebubbles] webhook listening on http://%s:%s%s",
            self.webhook_host,
            self.webhook_port,
            self.webhook_path,
        )

        # 向 BlueBubbles 服务器注册 webhook
        # 服务器需要知道将事件发送到哪里
        await self._register_webhook()

        return True

    async def disconnect(self) -> None:
        # 断开连接前先注销 webhook
        await self._unregister_webhook()

        if self.client:
            await self.client.aclose()
            self.client = None
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
        self._mark_disconnected()

    @property
    def _webhook_url(self) -> str:
        """计算用于 BlueBubbles 注册的外部 webhook URL。"""
        host = self.webhook_host
        if host in ("0.0.0.0", "127.0.0.1", "localhost", "::"):
            host = "localhost"
        return f"http://{host}:{self.webhook_port}{self.webhook_path}"

    @property
    def _webhook_register_url(self) -> str:
        """向 BlueBubbles 注册的 webhook URL，包含密码作为
        查询参数，以便入站 webhook POST 携带认证凭据。

        BlueBubbles 将事件发送到通过 ``/api/v1/webhook`` 注册的
        确切 URL。其 webhook 注册 API 不支持自定义头部，因此
        将密码嵌入 URL 是在不禁用认证的情况下验证入站 webhook
        的唯一方式。
        """
        base = self._webhook_url
        if self.password:
            return f"{base}?password={quote(self.password, safe='')}"
        return base

    async def _find_registered_webhooks(self, url: str) -> list:
        """返回匹配 *url* 的 BB webhook 注册条目列表。"""
        try:
            res = await self._api_get("/api/v1/webhook")
            data = res.get("data")
            if isinstance(data, list):
                return [wh for wh in data if wh.get("url") == url]
        except Exception:
            pass
        return []

    async def _register_webhook(self) -> bool:
        """向 BlueBubbles 服务器注册本 webhook URL。

        BlueBubbles 要求先通过 API 注册 webhook 才会发送事件。
        首先检查是否已有注册记录，避免重复注册（例如崩溃后
        未正常关闭的情况）。
        """
        if not self.client:
            return False

        webhook_url = self._webhook_register_url

        # 崩溃恢复 - 如果已有注册记录则复用
        existing = await self._find_registered_webhooks(webhook_url)
        if existing:
            logger.info(
                "[bluebubbles] webhook already registered: %s", webhook_url
            )
            return True

        payload = {
            "url": webhook_url,
            "events": ["new-message", "updated-message"],
        }

        try:
            res = await self._api_post("/api/v1/webhook", payload)
            status = res.get("status", 0)
            if 200 <= status < 300:
                logger.info(
                    "[bluebubbles] webhook registered with server: %s",
                    webhook_url,
                )
                return True
            else:
                logger.warning(
                    "[bluebubbles] webhook registration returned status %s: %s",
                    status,
                    res.get("message"),
                )
                return False
        except Exception as exc:
            logger.warning(
                "[bluebubbles] failed to register webhook with server: %s",
                exc,
            )
            return False

    async def _unregister_webhook(self) -> bool:
        """从 BlueBubbles 服务器注销本 webhook URL。

        移除*所有*匹配的注册记录，以清理之前崩溃
        留下的重复记录。
        """
        if not self.client:
            return False

        webhook_url = self._webhook_register_url
        removed = False

        try:
            for wh in await self._find_registered_webhooks(webhook_url):
                wh_id = wh.get("id")
                if wh_id:
                    res = await self.client.delete(
                        self._api_url(f"/api/v1/webhook/{wh_id}")
                    )
                    res.raise_for_status()
                    removed = True
            if removed:
                logger.info(
                    "[bluebubbles] webhook unregistered: %s", webhook_url
                )
        except Exception as exc:
            logger.debug(
                "[bluebubbles] failed to unregister webhook (non-critical): %s",
                exc,
            )
        return removed

    # ------------------------------------------------------------------
    # 聊天 GUID 解析
    # ------------------------------------------------------------------

    async def _resolve_chat_guid(self, target: str) -> Optional[str]:
        """将邮箱/电话号码解析为 BlueBubbles 聊天 GUID。

        如果 *target* 已包含分号（原始 GUID 格式如
        ``iMessage;-;user@example.com``），则直接返回。否则
        适配器查询 BlueBubbles 聊天列表，通过
        ``chatIdentifier`` 或参与者地址进行匹配。
        """
        target = (target or "").strip()
        if not target:
            return None
        # 已经是原始 GUID
        if ";" in target:
            return target
        if target in self._guid_cache:
            return self._guid_cache[target]
        try:
            payload = await self._api_post(
                "/api/v1/chat/query",
                {"limit": 100, "offset": 0, "with": ["participants"]},
            )
            for chat in payload.get("data", []) or []:
                guid = chat.get("guid") or chat.get("chatGuid")
                identifier = chat.get("chatIdentifier") or chat.get("identifier")
                if identifier == target:
                    if guid:
                        self._guid_cache[target] = guid
                    return guid
                for part in chat.get("participants", []) or []:
                    if (part.get("address") or "").strip() == target and guid:
                        self._guid_cache[target] = guid
                        return guid
        except Exception:
            pass
        return None

    async def _create_chat_for_handle(
        self, address: str, message: str
    ) -> SendResult:
        """通过向 *address* 发送第一条消息来创建新聊天。"""
        payload = {
            "addresses": [address],
            "message": message,
            "tempGuid": f"temp-{datetime.utcnow().timestamp()}",
        }
        try:
            res = await self._api_post("/api/v1/chat/new", payload)
            data = res.get("data") or {}
            msg_id = data.get("guid") or data.get("messageGuid") or "ok"
            return SendResult(success=True, message_id=str(msg_id), raw_response=res)
        except Exception as exc:
            return SendResult(success=False, error=str(exc))

    # ------------------------------------------------------------------
    # 文本发送
    # ------------------------------------------------------------------

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        text = strip_markdown(content or "")
        if not text:
            return SendResult(success=False, error="BlueBubbles send requires text")
        chunks = self.truncate_message(text, max_length=self.MAX_MESSAGE_LENGTH)
        last = SendResult(success=True)
        for chunk in chunks:
            guid = await self._resolve_chat_guid(chat_id)
            if not guid:
                # 如果目标看起来像一个地址，尝试创建新聊天
                if self._private_api_enabled and (
                    "@" in chat_id or re.match(r"^\+\d+", chat_id)
                ):
                    return await self._create_chat_for_handle(chat_id, chunk)
                return SendResult(
                    success=False,
                    error=f"BlueBubbles chat not found for target: {chat_id}",
                )
            payload: Dict[str, Any] = {
                "chatGuid": guid,
                "tempGuid": f"temp-{datetime.utcnow().timestamp()}",
                "message": chunk,
            }
            if reply_to and self._private_api_enabled and self._helper_connected:
                payload["method"] = "private-api"
                payload["selectedMessageGuid"] = reply_to
                payload["partIndex"] = 0
            try:
                res = await self._api_post("/api/v1/message/text", payload)
                data = res.get("data") or {}
                msg_id = data.get("guid") or data.get("messageGuid") or "ok"
                last = SendResult(
                    success=True, message_id=str(msg_id), raw_response=res
                )
            except Exception as exc:
                return SendResult(success=False, error=str(exc))
        return last

    # ------------------------------------------------------------------
    # 媒体发送（出站）
    # ------------------------------------------------------------------

    async def _send_attachment(
        self,
        chat_id: str,
        file_path: str,
        filename: Optional[str] = None,
        caption: Optional[str] = None,
        is_audio_message: bool = False,
    ) -> SendResult:
        """通过 BlueBubbles 分段上传发送文件附件。"""
        if not self.client:
            return SendResult(success=False, error="Not connected")
        if not os.path.isfile(file_path):
            return SendResult(success=False, error=f"File not found: {file_path}")

        guid = await self._resolve_chat_guid(chat_id)
        if not guid:
            return SendResult(success=False, error=f"Chat not found: {chat_id}")

        fname = filename or os.path.basename(file_path)
        try:
            with open(file_path, "rb") as f:
                files = {"attachment": (fname, f, "application/octet-stream")}
                data: Dict[str, str] = {
                    "chatGuid": guid,
                    "name": fname,
                    "tempGuid": uuid.uuid4().hex,
                }
                if is_audio_message:
                    data["isAudioMessage"] = "true"
                res = await self.client.post(
                    self._api_url("/api/v1/message/attachment"),
                    files=files,
                    data=data,
                    timeout=120,
                )
                res.raise_for_status()
                result = res.json()

            if caption:
                await self.send(chat_id, caption)

            if result.get("status") == 200:
                rdata = result.get("data") or {}
                msg_id = rdata.get("guid") if isinstance(rdata, dict) else None
                return SendResult(
                    success=True, message_id=msg_id, raw_response=result
                )
            return SendResult(
                success=False,
                error=result.get("message", "Attachment upload failed"),
            )
        except Exception as e:
            return SendResult(success=False, error=str(e))

    async def send_image(
        self,
        chat_id: str,
        image_url: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        try:
            from gateway.platforms.base import cache_image_from_url

            local_path = await cache_image_from_url(image_url)
            return await self._send_attachment(chat_id, local_path, caption=caption)
        except Exception:
            return await super().send_image(chat_id, image_url, caption, reply_to)

    async def send_image_file(
        self,
        chat_id: str,
        image_path: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        **kwargs,
    ) -> SendResult:
        return await self._send_attachment(chat_id, image_path, caption=caption)

    async def send_voice(
        self,
        chat_id: str,
        audio_path: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        **kwargs,
    ) -> SendResult:
        return await self._send_attachment(
            chat_id, audio_path, caption=caption, is_audio_message=True
        )

    async def send_video(
        self,
        chat_id: str,
        video_path: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        **kwargs,
    ) -> SendResult:
        return await self._send_attachment(chat_id, video_path, caption=caption)

    async def send_document(
        self,
        chat_id: str,
        file_path: str,
        caption: Optional[str] = None,
        file_name: Optional[str] = None,
        reply_to: Optional[str] = None,
        **kwargs,
    ) -> SendResult:
        return await self._send_attachment(
            chat_id, file_path, filename=file_name, caption=caption
        )

    async def send_animation(
        self,
        chat_id: str,
        animation_url: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        return await self.send_image(
            chat_id, animation_url, caption, reply_to, metadata
        )

    # ------------------------------------------------------------------
    # 输入指示器
    # ------------------------------------------------------------------

    async def send_typing(self, chat_id: str, metadata=None) -> None:
        if not self._private_api_enabled or not self._helper_connected or not self.client:
            return
        try:
            guid = await self._resolve_chat_guid(chat_id)
            if guid:
                encoded = quote(guid, safe="")
                await self.client.post(
                    self._api_url(f"/api/v1/chat/{encoded}/typing"), timeout=5
                )
        except Exception:
            pass

    async def stop_typing(self, chat_id: str) -> None:
        if not self._private_api_enabled or not self._helper_connected or not self.client:
            return
        try:
            guid = await self._resolve_chat_guid(chat_id)
            if guid:
                encoded = quote(guid, safe="")
                await self.client.delete(
                    self._api_url(f"/api/v1/chat/{encoded}/typing"), timeout=5
                )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 已读回执
    # ------------------------------------------------------------------

    async def mark_read(self, chat_id: str) -> bool:
        if not self._private_api_enabled or not self._helper_connected or not self.client:
            return False
        try:
            guid = await self._resolve_chat_guid(chat_id)
            if guid:
                encoded = quote(guid, safe="")
                await self.client.post(
                    self._api_url(f"/api/v1/chat/{encoded}/read"), timeout=5
                )
                return True
        except Exception:
            pass
        return False

    # ------------------------------------------------------------------
    # Tapback 表情回应
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # 聊天信息
    # ------------------------------------------------------------------

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        is_group = ";+;" in (chat_id or "")
        info: Dict[str, Any] = {
            "name": chat_id,
            "type": "group" if is_group else "dm",
        }
        try:
            guid = await self._resolve_chat_guid(chat_id)
            if guid:
                encoded = quote(guid, safe="")
                res = await self._api_get(
                    f"/api/v1/chat/{encoded}?with=participants"
                )
                data = (res or {}).get("data", {})
                display_name = (
                    data.get("displayName")
                    or data.get("chatIdentifier")
                    or chat_id
                )
                participants = []
                for p in data.get("participants", []) or []:
                    addr = (p.get("address") or "").strip()
                    if addr:
                        participants.append(addr)
                info["name"] = display_name
                if participants:
                    info["participants"] = participants
        except Exception:
            pass
        return info

    def format_message(self, content: str) -> str:
        return strip_markdown(content)

    # ------------------------------------------------------------------
    # 入站附件下载（来自 #4588）
    # ------------------------------------------------------------------

    async def _download_attachment(
        self, att_guid: str, att_meta: Dict[str, Any]
    ) -> Optional[str]:
        """从 BlueBubbles 下载附件并缓存到本地。

        成功时返回本地文件路径，失败时返回 None。
        """
        if not self.client:
            return None
        try:
            encoded = quote(att_guid, safe="")
            resp = await self.client.get(
                self._api_url(f"/api/v1/attachment/{encoded}/download"),
                timeout=60,
                follow_redirects=True,
            )
            resp.raise_for_status()
            data = resp.content

            mime = (att_meta.get("mimeType") or "").lower()
            transfer_name = att_meta.get("transferName", "")

            if mime.startswith("image/"):
                ext_map = {
                    "image/jpeg": ".jpg",
                    "image/png": ".png",
                    "image/gif": ".gif",
                    "image/webp": ".webp",
                    "image/heic": ".jpg",
                    "image/heif": ".jpg",
                    "image/tiff": ".jpg",
                }
                ext = ext_map.get(mime, ".jpg")
                return cache_image_from_bytes(data, ext)

            if mime.startswith("audio/"):
                ext_map = {
                    "audio/mp3": ".mp3",
                    "audio/mpeg": ".mp3",
                    "audio/ogg": ".ogg",
                    "audio/wav": ".wav",
                    "audio/x-caf": ".mp3",
                    "audio/mp4": ".m4a",
                    "audio/aac": ".m4a",
                }
                ext = ext_map.get(mime, ".mp3")
                return cache_audio_from_bytes(data, ext)

            # 视频、文档及其他所有类型
            filename = transfer_name or f"file_{uuid.uuid4().hex[:8]}"
            return cache_document_from_bytes(data, filename)

        except Exception as exc:
            logger.warning(
                "[bluebubbles] failed to download attachment %s: %s",
                _redact(att_guid),
                exc,
            )
            return None

    # ------------------------------------------------------------------
    # Webhook 处理
    # ------------------------------------------------------------------

    def _extract_payload_record(
        self, payload: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        data = payload.get("data")
        if isinstance(data, dict):
            return data
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    return item
        if isinstance(payload.get("message"), dict):
            return payload.get("message")
        return payload if isinstance(payload, dict) else None

    @staticmethod
    def _value(*candidates: Any) -> Optional[str]:
        for candidate in candidates:
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        return None

    async def _handle_webhook(self, request):
        from aiohttp import web

        token = (
            request.query.get("password")
            or request.query.get("guid")
            or request.headers.get("x-password")
            or request.headers.get("x-guid")
            or request.headers.get("x-bluebubbles-guid")
        )
        if token != self.password:
            return web.json_response({"error": "unauthorized"}, status=401)
        try:
            raw = await request.read()
            body = raw.decode("utf-8", errors="replace")
            try:
                payload = json.loads(body)
            except Exception:
                from urllib.parse import parse_qs

                form = parse_qs(body)
                payload_str = (
                    form.get("payload")
                    or form.get("data")
                    or form.get("message")
                    or [""]
                )[0]
                payload = json.loads(payload_str) if payload_str else {}
        except Exception as exc:
            logger.error("[bluebubbles] webhook parse error: %s", exc)
            return web.json_response({"error": "invalid payload"}, status=400)

        event_type = self._value(payload.get("type"), payload.get("event")) or ""
        # 仅处理消息事件；静默确认其他所有事件
        if event_type and event_type not in _MESSAGE_EVENTS:
            return web.Response(text="ok")

        record = self._extract_payload_record(payload) or {}
        is_from_me = bool(
            record.get("isFromMe")
            or record.get("fromMe")
            or record.get("is_from_me")
        )
        if is_from_me:
            return web.Response(text="ok")

        # 跳过以消息形式投递的 Tapback 表情回应
        assoc_type = record.get("associatedMessageType")
        if isinstance(assoc_type, int) and assoc_type in {
            **_TAPBACK_ADDED,
            **_TAPBACK_REMOVED,
        }:
            return web.Response(text="ok")

        text = (
            self._value(
                record.get("text"), record.get("message"), record.get("body")
            )
            or ""
        )

        # --- 入站附件处理 ---
        attachments = record.get("attachments") or []
        media_urls: List[str] = []
        media_types: List[str] = []
        msg_type = MessageType.TEXT

        for att in attachments:
            att_guid = att.get("guid", "")
            if not att_guid:
                continue
            cached = await self._download_attachment(att_guid, att)
            if cached:
                mime = (att.get("mimeType") or "").lower()
                media_urls.append(cached)
                media_types.append(mime)
                if mime.startswith("image/"):
                    msg_type = MessageType.PHOTO
                elif mime.startswith("audio/") or (att.get("uti") or "").endswith(
                    "caf"
                ):
                    msg_type = MessageType.VOICE
                elif mime.startswith("video/"):
                    msg_type = MessageType.VIDEO
                else:
                    msg_type = MessageType.DOCUMENT

        # 多个附件时，如果有图片则优先设为 PHOTO 类型
        if len(media_urls) > 1:
            mime_prefixes = {(m or "").split("/")[0] for m in media_types}
            if "image" in mime_prefixes:
                msg_type = MessageType.PHOTO

        if not text and media_urls:
            text = "(attachment)"
        # --- 附件处理结束 ---

        chat_guid = self._value(
            record.get("chatGuid"),
            payload.get("chatGuid"),
            record.get("chat_guid"),
            payload.get("chat_guid"),
            payload.get("guid"),
        )
        # 回退：BlueBubbles v1.9+ webhook 载荷中省略了顶层 chatGuid；
        # 聊天 GUID 嵌套在 data.chats[0].guid 中。
        if not chat_guid:
            _chats = record.get("chats") or []
            if _chats and isinstance(_chats[0], dict):
                chat_guid = _chats[0].get("guid") or _chats[0].get("chatGuid")
        chat_identifier = self._value(
            record.get("chatIdentifier"),
            record.get("identifier"),
            payload.get("chatIdentifier"),
            payload.get("identifier"),
        )
        sender = (
            self._value(
                record.get("handle", {}).get("address")
                if isinstance(record.get("handle"), dict)
                else None,
                record.get("sender"),
                record.get("from"),
                record.get("address"),
            )
            or chat_identifier
            or chat_guid
        )
        if not (chat_guid or chat_identifier) and sender:
            chat_identifier = sender
        if not sender or not (chat_guid or chat_identifier) or not text:
            return web.json_response({"error": "missing message fields"}, status=400)

        session_chat_id = chat_guid or chat_identifier
        is_group = bool(record.get("isGroup")) or (";+;" in (chat_guid or ""))
        source = self.build_source(
            chat_id=session_chat_id,
            chat_name=chat_identifier or sender,
            chat_type="group" if is_group else "dm",
            user_id=sender,
            user_name=sender,
            chat_id_alt=chat_identifier,
        )
        event = MessageEvent(
            text=text,
            message_type=msg_type,
            source=source,
            raw_message=payload,
            message_id=self._value(
                record.get("guid"),
                record.get("messageGuid"),
                record.get("id"),
            ),
            reply_to_message_id=self._value(
                record.get("threadOriginatorGuid"),
                record.get("associatedMessageGuid"),
            ),
            media_urls=media_urls,
            media_types=media_types,
        )
        task = asyncio.create_task(self.handle_message(event))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

        # 即发即忘的已读回执
        if self.send_read_receipts and session_chat_id:
            asyncio.create_task(self.mark_read(session_chat_id))

        return web.Response(text="ok")

