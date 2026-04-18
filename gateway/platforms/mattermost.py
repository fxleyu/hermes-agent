"""Mattermost 网关适配器。

通过 REST API (v4) 和 WebSocket 连接到自托管（或云端）的
Mattermost 实例，实现实时事件通信。无需外部 Mattermost 库——
使用已作为 Hermes 依赖项的 aiohttp。

环境变量：
    MATTERMOST_URL              服务器 URL（例如 https://mm.example.com）
    MATTERMOST_TOKEN            Bot 令牌或个人访问令牌
    MATTERMOST_ALLOWED_USERS    逗号分隔的用户 ID 列表
    MATTERMOST_HOME_CHANNEL     用于定时任务/通知投递的频道 ID
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from gateway.config import Platform, PlatformConfig
from gateway.platforms.helpers import MessageDeduplicator
from gateway.platforms.base import (
    BasePlatformAdapter,
    MessageEvent,
    MessageType,
    SendResult,
)

logger = logging.getLogger(__name__)

# Mattermost 帖子大小限制（服务器默认为 16383，但 4000 是
# 可读消息的实际限制——与 OpenClaw 的选择一致）。
MAX_POST_LENGTH = 4000

# Mattermost API 返回的频道类型代码。
_CHANNEL_TYPE_MAP = {
    "D": "dm",        # 私信
    "G": "group",     # 群组消息
    "P": "group",     # 私有频道 -> 视为群组
    "O": "channel",   # 公开频道
}

# 重连参数（指数退避）。
_RECONNECT_BASE_DELAY = 2.0    # 初始重连延迟（秒）
_RECONNECT_MAX_DELAY = 60.0    # 最大重连延迟（秒）
_RECONNECT_JITTER = 0.2        # 随机抖动系数


def check_mattermost_requirements() -> bool:
    """如果 Mattermost 适配器可以使用，则返回 True。"""
    token = os.getenv("MATTERMOST_TOKEN", "")
    url = os.getenv("MATTERMOST_URL", "")
    if not token:
        logger.debug("Mattermost：未设置 MATTERMOST_TOKEN")
        return False
    if not url:
        logger.warning("Mattermost：未设置 MATTERMOST_URL")
        return False
    try:
        import aiohttp  # noqa: F401
        return True
    except ImportError:
        logger.warning("Mattermost：aiohttp 未安装")
        return False


class MattermostAdapter(BasePlatformAdapter):
    """Mattermost（自托管或云端）的网关适配器。"""

    def __init__(self, config: PlatformConfig):
        super().__init__(config, Platform.MATTERMOST)

        self._base_url: str = (
            config.extra.get("url", "")
            or os.getenv("MATTERMOST_URL", "")
        ).rstrip("/")
        self._token: str = config.token or os.getenv("MATTERMOST_TOKEN", "")

        self._bot_user_id: str = ""
        self._bot_username: str = ""

        # aiohttp 会话 + WebSocket 句柄
        self._session: Any = None  # aiohttp.ClientSession
        self._ws: Any = None       # aiohttp.ClientWebSocketResponse
        self._ws_task: Optional[asyncio.Task] = None
        self._reconnect_task: Optional[asyncio.Task] = None
        self._closing = False

        # 回复模式："thread" 将回复嵌套在线程中，"off" 发送平级消息。
        self._reply_mode: str = (
            config.extra.get("reply_mode", "")
            or os.getenv("MATTERMOST_REPLY_MODE", "off")
        ).lower()

        # 消息去重缓存（防止重复处理）
        self._dedup = MessageDeduplicator()

    # ------------------------------------------------------------------
    # HTTP 辅助方法
    # ------------------------------------------------------------------

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }

    async def _api_get(self, path: str) -> Dict[str, Any]:
        """GET /api/v4/{path}。"""
        import aiohttp
        url = f"{self._base_url}/api/v4/{path.lstrip('/')}"
        try:
            async with self._session.get(url, headers=self._headers(), timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status >= 400:
                    body = await resp.text()
                    logger.error("MM API GET %s -> %s：%s", path, resp.status, body[:200])
                    return {}
                return await resp.json()
        except aiohttp.ClientError as exc:
            logger.error("MM API GET %s 网络错误：%s", path, exc)
            return {}

    async def _api_post(
        self, path: str, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """POST /api/v4/{path}，发送 JSON 请求体。"""
        import aiohttp
        url = f"{self._base_url}/api/v4/{path.lstrip('/')}"
        try:
            async with self._session.post(
                url, headers=self._headers(), json=payload,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                if resp.status >= 400:
                    body = await resp.text()
                    logger.error("MM API POST %s -> %s：%s", path, resp.status, body[:200])
                    return {}
                return await resp.json()
        except aiohttp.ClientError as exc:
            logger.error("MM API POST %s 网络错误：%s", path, exc)
            return {}

    async def _api_put(
        self, path: str, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """PUT /api/v4/{path}，发送 JSON 请求体。"""
        import aiohttp
        url = f"{self._base_url}/api/v4/{path.lstrip('/')}"
        try:
            async with self._session.put(
                url, headers=self._headers(), json=payload
            ) as resp:
                if resp.status >= 400:
                    body = await resp.text()
                    logger.error("MM API PUT %s -> %s：%s", path, resp.status, body[:200])
                    return {}
                return await resp.json()
        except aiohttp.ClientError as exc:
            logger.error("MM API PUT %s 网络错误：%s", path, exc)
            return {}

    async def _upload_file(
        self, channel_id: str, file_data: bytes, filename: str, content_type: str = "application/octet-stream"
    ) -> Optional[str]:
        """上传文件并返回其文件 ID，失败时返回 None。"""
        import aiohttp

        url = f"{self._base_url}/api/v4/files"
        form = aiohttp.FormData()
        form.add_field("channel_id", channel_id)
        form.add_field(
            "files",
            file_data,
            filename=filename,
            content_type=content_type,
        )
        headers = {"Authorization": f"Bearer {self._token}"}
        async with self._session.post(url, headers=headers, data=form, timeout=aiohttp.ClientTimeout(total=60)) as resp:
            if resp.status >= 400:
                body = await resp.text()
                logger.error("MM 文件上传 -> %s：%s", resp.status, body[:200])
                return None
            data = await resp.json()
            infos = data.get("file_infos", [])
            return infos[0]["id"] if infos else None

    # ------------------------------------------------------------------
    # 必须实现的接口方法
    # ------------------------------------------------------------------

    async def connect(self) -> bool:
        """连接到 Mattermost 并启动 WebSocket 监听器。"""
        import aiohttp

        if not self._base_url or not self._token:
            logger.error("Mattermost：未配置 URL 或令牌")
            return False

        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30)
        )
        self._closing = False

        # 验证凭据并获取 Bot 身份信息
        me = await self._api_get("users/me")
        if not me or "id" not in me:
            logger.error("Mattermost：认证失败——请检查 MATTERMOST_TOKEN 和 MATTERMOST_URL")
            await self._session.close()
            return False

        self._bot_user_id = me["id"]
        self._bot_username = me.get("username", "")
        logger.info(
            "Mattermost：已认证为 @%s (%s)，服务器：%s",
            self._bot_username,
            self._bot_user_id,
            self._base_url,
        )

        # 在后台启动 WebSocket 监听
        self._ws_task = asyncio.create_task(self._ws_loop())
        self._mark_connected()
        return True

    async def disconnect(self) -> None:
        """断开与 Mattermost 的连接。"""
        self._closing = True

        if self._ws_task and not self._ws_task.done():
            self._ws_task.cancel()
            try:
                await self._ws_task
            except (asyncio.CancelledError, Exception):
                pass

        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()

        if self._ws:
            await self._ws.close()
            self._ws = None

        if self._session and not self._session.closed:
            await self._session.close()

        logger.info("Mattermost：已断开连接")

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """向频道发送消息（或多个分片）。"""
        if not content:
            return SendResult(success=True)

        formatted = self.format_message(content)
        chunks = self.truncate_message(formatted, MAX_POST_LENGTH)

        last_id = None
        for chunk in chunks:
            payload: Dict[str, Any] = {
                "channel_id": chat_id,
                "message": chunk,
            }
            # 线程支持：reply_to 是根帖子 ID
            if reply_to and self._reply_mode == "thread":
                payload["root_id"] = reply_to

            data = await self._api_post("posts", payload)
            if not data or "id" not in data:
                return SendResult(success=False, error="创建帖子失败")
            last_id = data["id"]

        return SendResult(success=True, message_id=last_id)

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        """返回频道名称和类型。"""
        data = await self._api_get(f"channels/{chat_id}")
        if not data:
            return {"name": chat_id, "type": "channel"}

        ch_type = _CHANNEL_TYPE_MAP.get(data.get("type", "O"), "channel")
        display_name = data.get("display_name") or data.get("name") or chat_id
        return {"name": display_name, "type": ch_type}

    # ------------------------------------------------------------------
    # 可选的接口方法覆写
    # ------------------------------------------------------------------

    async def send_typing(
        self, chat_id: str, metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """发送正在输入指示器。"""
        await self._api_post(
            f"users/{self._bot_user_id}/typing",
            {"channel_id": chat_id},
        )

    async def edit_message(
        self, chat_id: str, message_id: str, content: str
    ) -> SendResult:
        """编辑已有的帖子。"""
        formatted = self.format_message(content)
        data = await self._api_put(
            f"posts/{message_id}/patch",
            {"message": formatted},
        )
        if not data or "id" not in data:
            return SendResult(success=False, error="编辑帖子失败")
        return SendResult(success=True, message_id=data["id"])

    async def send_image(
        self,
        chat_id: str,
        image_url: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """下载图片并作为文件附件上传。"""
        return await self._send_url_as_file(
            chat_id, image_url, caption, reply_to, "image"
        )

    async def send_image_file(
        self,
        chat_id: str,
        image_path: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """上传本地图片文件。"""
        return await self._send_local_file(
            chat_id, image_path, caption, reply_to
        )

    async def send_document(
        self,
        chat_id: str,
        file_path: str,
        caption: Optional[str] = None,
        file_name: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """上传本地文件作为文档。"""
        return await self._send_local_file(
            chat_id, file_path, caption, reply_to, file_name
        )

    async def send_voice(
        self,
        chat_id: str,
        audio_path: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """上传音频文件。"""
        return await self._send_local_file(
            chat_id, audio_path, caption, reply_to
        )

    async def send_video(
        self,
        chat_id: str,
        video_path: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """上传视频文件。"""
        return await self._send_local_file(
            chat_id, video_path, caption, reply_to
        )

    def format_message(self, content: str) -> str:
        """Mattermost 使用标准 Markdown——大部分内容直接透传。

        将图片 Markdown 转换为纯链接（文件单独上传）。
        """
        # 将 ![alt](url) 转换为纯 URL——Mattermost 会自动将
        # 图片 URL 渲染为内联预览。
        content = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", r"\2", content)
        return content

    # ------------------------------------------------------------------
    # 文件辅助方法
    # ------------------------------------------------------------------

    async def _send_url_as_file(
        self,
        chat_id: str,
        url: str,
        caption: Optional[str],
        reply_to: Optional[str],
        kind: str = "file",
    ) -> SendResult:
        """下载 URL 内容并作为文件附件上传。"""
        from tools.url_safety import is_safe_url
        if not is_safe_url(url):
            logger.warning("Mattermost：已阻止不安全的 URL（SSRF 防护）")
            return await self.send(chat_id, f"{caption or ''}\n{url}".strip(), reply_to)

        import asyncio
        import aiohttp

        last_exc = None
        file_data = None
        ct = "application/octet-stream"
        fname = url.rsplit("/", 1)[-1].split("?")[0] or f"{kind}.png"

        # 带重试的文件下载（最多 3 次）
        for attempt in range(3):
            try:
                async with self._session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status >= 500 or resp.status == 429:
                        if attempt < 2:
                            logger.debug("Mattermost 下载重试 %d/2，URL：%s（状态码 %d）",
                                         attempt + 1, url[:80], resp.status)
                            await asyncio.sleep(1.5 * (attempt + 1))
                            continue
                    if resp.status >= 400:
                        return await self.send(chat_id, f"{caption or ''}\n{url}".strip(), reply_to)
                    file_data = await resp.read()
                    ct = resp.content_type or "application/octet-stream"
                    break
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                if attempt < 2:
                    await asyncio.sleep(1.5 * (attempt + 1))
                    continue
                logger.warning("Mattermost：下载 %s 失败，已尝试 %d 次：%s", url, attempt + 1, exc)
                return await self.send(chat_id, f"{caption or ''}\n{url}".strip(), reply_to)

        if file_data is None:
            logger.warning("Mattermost：%s 下载返回空数据", url)
            return await self.send(chat_id, f"{caption or ''}\n{url}".strip(), reply_to)

        file_id = await self._upload_file(chat_id, file_data, fname, ct)
        if not file_id:
            return await self.send(chat_id, f"{caption or ''}\n{url}".strip(), reply_to)

        payload: Dict[str, Any] = {
            "channel_id": chat_id,
            "message": caption or "",
            "file_ids": [file_id],
        }
        if reply_to and self._reply_mode == "thread":
            payload["root_id"] = reply_to

        data = await self._api_post("posts", payload)
        if not data or "id" not in data:
            return SendResult(success=False, error="发布带附件的帖子失败")
        return SendResult(success=True, message_id=data["id"])

    async def _send_local_file(
        self,
        chat_id: str,
        file_path: str,
        caption: Optional[str],
        reply_to: Optional[str],
        file_name: Optional[str] = None,
    ) -> SendResult:
        """上传本地文件并附加到帖子。"""
        import mimetypes

        p = Path(file_path)
        if not p.exists():
            return await self.send(
                chat_id, f"{caption or ''}\n(file not found: {file_path})", reply_to
            )

        fname = file_name or p.name
        ct = mimetypes.guess_type(fname)[0] or "application/octet-stream"
        file_data = p.read_bytes()

        file_id = await self._upload_file(chat_id, file_data, fname, ct)
        if not file_id:
            return SendResult(success=False, error="文件上传失败")

        payload: Dict[str, Any] = {
            "channel_id": chat_id,
            "message": caption or "",
            "file_ids": [file_id],
        }
        if reply_to and self._reply_mode == "thread":
            payload["root_id"] = reply_to

        data = await self._api_post("posts", payload)
        if not data or "id" not in data:
            return SendResult(success=False, error="发布带附件的帖子失败")
        return SendResult(success=True, message_id=data["id"])

    # ------------------------------------------------------------------
    # WebSocket
    # ------------------------------------------------------------------

    async def _ws_loop(self) -> None:
        """连接到 WebSocket 并监听事件，失败时自动重连。"""
        delay = _RECONNECT_BASE_DELAY
        while not self._closing:
            try:
                await self._ws_connect_and_listen()
                # 正常断开——重置延迟
                delay = _RECONNECT_BASE_DELAY
            except asyncio.CancelledError:
                return
            except Exception as exc:
                if self._closing:
                    return
                # 检测永久性认证/权限错误——这些错误不会在重试后成功，
                # 停止重连而不是无限循环。
                import aiohttp
                err_str = str(exc).lower()
                if isinstance(exc, aiohttp.WSServerHandshakeError) and exc.status in (401, 403):
                    logger.error("Mattermost WS 认证失败（HTTP %d）——停止重连", exc.status)
                    return
                if "401" in err_str or "403" in err_str or "unauthorized" in err_str:
                    logger.error("Mattermost WS 永久性错误：%s——停止重连", exc)
                    return
                logger.warning("Mattermost WS 错误：%s——将在 %.0f 秒后重连", exc, delay)

            if self._closing:
                return

            # 指数退避加随机抖动
            import random
            jitter = delay * _RECONNECT_JITTER * random.random()
            await asyncio.sleep(delay + jitter)
            delay = min(delay * 2, _RECONNECT_MAX_DELAY)

    async def _ws_connect_and_listen(self) -> None:
        """单次 WebSocket 会话：连接、认证、处理事件。"""
        # 构建 WS URL：https:// -> wss://，http:// -> ws://
        ws_url = re.sub(r"^http", "ws", self._base_url) + "/api/v4/websocket"
        logger.info("Mattermost：正在连接到 %s", ws_url)

        self._ws = await self._session.ws_connect(ws_url, heartbeat=30.0)

        # 通过 WebSocket 进行认证
        auth_msg = {
            "seq": 1,
            "action": "authentication_challenge",
            "data": {"token": self._token},
        }
        await self._ws.send_json(auth_msg)
        logger.info("Mattermost：WebSocket 已连接并完成认证")

        async for raw_msg in self._ws:
            if self._closing:
                return

            if raw_msg.type in (
                raw_msg.type.TEXT,
                raw_msg.type.BINARY,
            ):
                try:
                    event = json.loads(raw_msg.data)
                except (json.JSONDecodeError, TypeError):
                    continue
                await self._handle_ws_event(event)
            elif raw_msg.type in (
                raw_msg.type.ERROR,
                raw_msg.type.CLOSE,
                raw_msg.type.CLOSING,
                raw_msg.type.CLOSED,
            ):
                logger.info("Mattermost：WebSocket 已关闭（%s）", raw_msg.type)
                break

    async def _handle_ws_event(self, event: Dict[str, Any]) -> None:
        """处理单个 WebSocket 事件。"""
        event_type = event.get("event")
        if event_type != "posted":
            return

        data = event.get("data", {})
        raw_post_str = data.get("post")
        if not raw_post_str:
            return

        try:
            post = json.loads(raw_post_str)
        except (json.JSONDecodeError, TypeError):
            return

        # 忽略自己发送的消息
        if post.get("user_id") == self._bot_user_id:
            return

        # 忽略系统帖子
        if post.get("type"):
            return

        post_id = post.get("id", "")

        # 消息去重
        if self._dedup.is_duplicate(post_id):
            return

        # 构建消息事件
        channel_id = post.get("channel_id", "")
        channel_type_raw = data.get("channel_type", "O")
        chat_type = _CHANNEL_TYPE_MAP.get(channel_type_raw, "channel")

        # 对于私信，user_id 足够。对于频道，需要检查 @提及。
        message_text = post.get("message", "")

        # 非私信频道的 @提及 门控。
        # 配置（环境变量）：
        #   MATTERMOST_REQUIRE_MENTION：在频道中要求 @提及（默认：true）
        #   MATTERMOST_FREE_RESPONSE_CHANNELS：Bot 无需 @提及 即可响应的频道 ID
        if channel_type_raw != "D":
            require_mention = os.getenv(
                "MATTERMOST_REQUIRE_MENTION", "true"
            ).lower() not in ("false", "0", "no")

            free_channels_raw = os.getenv("MATTERMOST_FREE_RESPONSE_CHANNELS", "")
            free_channels = {ch.strip() for ch in free_channels_raw.split(",") if ch.strip()}
            is_free_channel = channel_id in free_channels

            mention_patterns = [
                f"@{self._bot_username}",
                f"@{self._bot_user_id}",
            ]
            has_mention = any(
                pattern.lower() in message_text.lower()
                for pattern in mention_patterns
            )

            if require_mention and not is_free_channel and not has_mention:
                logger.debug(
                    "Mattermost：跳过非私信中未 @提及 的消息（频道=%s）",
                    channel_id,
                )
                return

            # 从消息文本中移除 @提及，让 Agent 看到干净的输入
            if has_mention:
                for pattern in mention_patterns:
                    message_text = re.sub(
                        re.escape(pattern), "", message_text, flags=re.IGNORECASE
                    ).strip()

        # 解析发送者信息
        sender_id = post.get("user_id", "")
        sender_name = data.get("sender_name", "").lstrip("@") or sender_id

        # 线程支持：如果帖子在线程中，使用 root_id
        thread_id = post.get("root_id") or None

        # 确定消息类型
        file_ids = post.get("file_ids") or []
        msg_type = MessageType.TEXT
        if message_text.startswith("/"):
            msg_type = MessageType.COMMAND

        # 立即下载文件附件（下游工具没有认证头，无法直接使用 URL）
        media_urls: List[str] = []
        media_types: List[str] = []
        for fid in file_ids:
            try:
                file_info = await self._api_get(f"files/{fid}/info")
                fname = file_info.get("name", f"file_{fid}")
                ext = Path(fname).suffix or ""
                mime = file_info.get("mime_type", "application/octet-stream")

                import aiohttp
                dl_url = f"{self._base_url}/api/v4/files/{fid}"
                async with self._session.get(
                    dl_url,
                    headers={"Authorization": f"Bearer {self._token}"},
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status < 400:
                        file_data = await resp.read()
                        from gateway.platforms.base import cache_image_from_bytes, cache_document_from_bytes
                        if mime.startswith("image/"):
                            local_path = cache_image_from_bytes(file_data, ext or ".png")
                            media_urls.append(local_path)
                            media_types.append(mime)
                        elif mime.startswith("audio/"):
                            from gateway.platforms.base import cache_audio_from_bytes
                            local_path = cache_audio_from_bytes(file_data, ext or ".ogg")
                            media_urls.append(local_path)
                            media_types.append(mime)
                        else:
                            local_path = cache_document_from_bytes(file_data, fname)
                            media_urls.append(local_path)
                            media_types.append(mime)
                    else:
                        logger.warning("Mattermost：下载文件 %s 失败：HTTP %s", fid, resp.status)
            except Exception as exc:
                logger.warning("Mattermost：下载文件 %s 时出错：%s", fid, exc)

        # 根据下载的媒体类型设置消息类型
        if media_types and msg_type == MessageType.TEXT:
            if any(m.startswith("image/") for m in media_types):
                msg_type = MessageType.PHOTO
            elif any(m.startswith("audio/") for m in media_types):
                msg_type = MessageType.VOICE
            elif media_types:
                msg_type = MessageType.DOCUMENT

        source = self.build_source(
            chat_id=channel_id,
            chat_type=chat_type,
            user_id=sender_id,
            user_name=sender_name,
            thread_id=thread_id,
        )

        # 每频道临时提示词
        from gateway.platforms.base import resolve_channel_prompt
        _channel_prompt = resolve_channel_prompt(
            self.config.extra, channel_id, None,
        )

        msg_event = MessageEvent(
            text=message_text,
            message_type=msg_type,
            source=source,
            raw_message=post,
            message_id=post_id,
            media_urls=media_urls if media_urls else None,
            media_types=media_types if media_types else None,
            channel_prompt=_channel_prompt,
        )

        await self.handle_message(msg_event)
