"""Signal 即时通讯平台适配器。

连接到以 HTTP 模式运行的 signal-cli 守护进程。
入站消息通过 SSE（Server-Sent Events）流式传输接收。
出站消息和操作使用基于 HTTP 的 JSON-RPC 2.0。

基于 PR #268（ibhagwan），修复了 bug 后重建。

依赖：
  - signal-cli 已安装并运行：signal-cli daemon --http 127.0.0.1:8080
  - 已设置 SIGNAL_HTTP_URL 和 SIGNAL_ACCOUNT 环境变量
"""

import asyncio
import base64
import json
import logging
import os
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Any
from urllib.parse import quote, unquote

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
    cache_image_from_url,
)
from gateway.platforms.helpers import redact_phone

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
SIGNAL_MAX_ATTACHMENT_SIZE = 100 * 1024 * 1024  # 100 MB
MAX_MESSAGE_LENGTH = 8000  # Signal 消息大小限制
TYPING_INTERVAL = 8.0  # 输入指示器刷新间隔（秒）
SSE_RETRY_DELAY_INITIAL = 2.0
SSE_RETRY_DELAY_MAX = 60.0
HEALTH_CHECK_INTERVAL = 30.0  # 健康检查间隔（秒）
HEALTH_CHECK_STALE_THRESHOLD = 120.0  # SSE 无活动超过此时间则视为异常（秒）

# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _parse_comma_list(value: str) -> List[str]:
    """将逗号分隔的字符串拆分为列表，去除空白。"""
    return [v.strip() for v in value.split(",") if v.strip()]


def _guess_extension(data: bytes) -> str:
    """根据文件魔数字节猜测文件扩展名。"""
    if data[:4] == b"\x89PNG":
        return ".png"
    if data[:2] == b"\xff\xd8":
        return ".jpg"
    if data[:4] == b"GIF8":
        return ".gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data[:4] == b"%PDF":
        return ".pdf"
    if len(data) >= 8 and data[4:8] == b"ftyp":
        return ".mp4"
    if data[:4] == b"OggS":
        return ".ogg"
    if len(data) >= 2 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0:
        return ".mp3"
    if data[:2] == b"PK":
        return ".zip"
    return ".bin"


def _is_image_ext(ext: str) -> bool:
    return ext.lower() in (".jpg", ".jpeg", ".png", ".gif", ".webp")


def _is_audio_ext(ext: str) -> bool:
    return ext.lower() in (".mp3", ".wav", ".ogg", ".m4a", ".aac")


_EXT_TO_MIME = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".gif": "image/gif", ".webp": "image/webp",
    ".ogg": "audio/ogg", ".mp3": "audio/mpeg", ".wav": "audio/wav",
    ".m4a": "audio/mp4", ".aac": "audio/aac",
    ".mp4": "video/mp4", ".pdf": "application/pdf", ".zip": "application/zip",
}


def _ext_to_mime(ext: str) -> str:
    """将文件扩展名映射为 MIME 类型。"""
    return _EXT_TO_MIME.get(ext.lower(), "application/octet-stream")


def _render_mentions(text: str, mentions: list) -> str:
    """将 Signal 的提及占位符（\\uFFFC）替换为可读的 @标识符。

    Signal 使用 Unicode 对象替换字符编码 @提及，
    并在带外元数据中包含被提及用户的 UUID/号码。
    """
    if not mentions or "\uFFFC" not in text:
        return text
    # 按起始位置倒序排列提及，从后向前替换
    # 以避免替换时索引偏移
    sorted_mentions = sorted(mentions, key=lambda m: m.get("start", 0), reverse=True)
    for mention in sorted_mentions:
        start = mention.get("start", 0)
        length = mention.get("length", 1)
        # 使用提及的号码或 UUID 作为替换文本
        identifier = mention.get("number") or mention.get("uuid") or "user"
        replacement = f"@{identifier}"
        text = text[:start] + replacement + text[start + length:]
    return text


def check_signal_requirements() -> bool:
    """检查 Signal 是否已配置（具有 URL 和账户）。"""
    return bool(os.getenv("SIGNAL_HTTP_URL") and os.getenv("SIGNAL_ACCOUNT"))


# ---------------------------------------------------------------------------
# Signal 适配器
# ---------------------------------------------------------------------------

class SignalAdapter(BasePlatformAdapter):
    """使用 signal-cli HTTP 守护进程的 Signal 即时通讯适配器。"""

    platform = Platform.SIGNAL

    def __init__(self, config: PlatformConfig):
        super().__init__(config, Platform.SIGNAL)

        extra = config.extra or {}
        self.http_url = extra.get("http_url", "http://127.0.0.1:8080").rstrip("/")
        self.account = extra.get("account", "")
        self.ignore_stories = extra.get("ignore_stories", True)

        # 解析允许列表 - 群组策略由群组允许列表是否存在来决定
        group_allowed_str = os.getenv("SIGNAL_GROUP_ALLOWED_USERS", "")
        self.group_allow_from = set(_parse_comma_list(group_allowed_str))

        # HTTP 客户端
        self.client: Optional[httpx.AsyncClient] = None

        # 后台任务
        self._sse_task: Optional[asyncio.Task] = None
        self._health_monitor_task: Optional[asyncio.Task] = None
        self._typing_tasks: Dict[str, asyncio.Task] = {}
        self._running = False
        self._last_sse_activity = 0.0
        self._sse_response: Optional[httpx.Response] = None

        # 规范化账户用于自消息过滤
        self._account_normalized = self.account.strip()

        # 跟踪最近发送的消息时间戳，防止在"给自己的备忘"
        # 或自聊天模式下出现回声循环（类似 WhatsApp 的 recentlySentIds）
        self._recent_sent_timestamps: set = set()
        self._max_recent_timestamps = 50

        logger.info("Signal adapter initialized: url=%s account=%s groups=%s",
                     self.http_url, redact_phone(self.account),
                     "enabled" if self.group_allow_from else "disabled")

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def connect(self) -> bool:
        """连接到 signal-cli 守护进程并启动 SSE 监听器。"""
        if not self.http_url or not self.account:
            logger.error("Signal: SIGNAL_HTTP_URL and SIGNAL_ACCOUNT are required")
            return False

        # 获取作用域锁，防止同一电话号码的重复 Signal 监听器
        try:
            if not self._acquire_platform_lock('signal-phone', self.account, 'Signal account'):
                return False
        except Exception as e:
            logger.warning("Signal: Could not acquire phone lock (non-fatal): %s", e)

        self.client = httpx.AsyncClient(timeout=30.0)

        # 健康检查 - 验证 signal-cli 守护进程是否可达
        try:
            resp = await self.client.get(f"{self.http_url}/api/v1/check", timeout=10.0)
            if resp.status_code != 200:
                logger.error("Signal: health check failed (status %d)", resp.status_code)
                return False
        except Exception as e:
            logger.error("Signal: cannot reach signal-cli at %s: %s", self.http_url, e)
            return False

        self._running = True
        self._last_sse_activity = time.time()
        self._sse_task = asyncio.create_task(self._sse_listener())
        self._health_monitor_task = asyncio.create_task(self._health_monitor())

        logger.info("Signal: connected to %s", self.http_url)
        return True

    async def disconnect(self) -> None:
        """停止 SSE 监听器并清理资源。"""
        self._running = False

        if self._sse_task:
            self._sse_task.cancel()
            try:
                await self._sse_task
            except asyncio.CancelledError:
                pass

        if self._health_monitor_task:
            self._health_monitor_task.cancel()
            try:
                await self._health_monitor_task
            except asyncio.CancelledError:
                pass

        # 取消所有输入任务
        for task in self._typing_tasks.values():
            task.cancel()
        self._typing_tasks.clear()

        if self.client:
            await self.client.aclose()
            self.client = None

        self._release_platform_lock()

        logger.info("Signal: disconnected")

    # ------------------------------------------------------------------
    # SSE 流式传输（入站消息）
    # ------------------------------------------------------------------

    async def _sse_listener(self) -> None:
        """监听来自 signal-cli 守护进程的 SSE 事件。"""
        url = f"{self.http_url}/api/v1/events?account={quote(self.account, safe='')}"
        backoff = SSE_RETRY_DELAY_INITIAL

        while self._running:
            try:
                logger.debug("Signal SSE: connecting to %s", url)
                async with self.client.stream(
                    "GET", url,
                    headers={"Accept": "text/event-stream"},
                    timeout=None,
                ) as response:
                    self._sse_response = response
                    backoff = SSE_RETRY_DELAY_INITIAL  # 连接成功后重置退避
                    self._last_sse_activity = time.time()
                    logger.info("Signal SSE: connected")

                    buffer = ""
                    async for chunk in response.aiter_text():
                        if not self._running:
                            break
                        buffer += chunk
                        while "\n" in buffer:
                            line, buffer = buffer.split("\n", 1)
                            line = line.strip()
                            if not line:
                                continue
                            # SSE 心跳注释（":"）证明连接
                            # 仍然活跃 - 更新活动时间，避免健康监控
                            # 报告虚假空闲警告。
                            if line.startswith(":"):
                                self._last_sse_activity = time.time()
                                continue
                            # 解析 SSE 数据行
                            if line.startswith("data:"):
                                data_str = line[5:].strip()
                                if not data_str:
                                    continue
                                self._last_sse_activity = time.time()
                                try:
                                    data = json.loads(data_str)
                                    await self._handle_envelope(data)
                                except json.JSONDecodeError:
                                    logger.debug("Signal SSE: invalid JSON: %s", data_str[:100])
                                except Exception:
                                    logger.exception("Signal SSE: error handling event")

            except asyncio.CancelledError:
                break
            except httpx.HTTPError as e:
                if self._running:
                    logger.warning("Signal SSE: HTTP error: %s (reconnecting in %.0fs)", e, backoff)
            except Exception as e:
                if self._running:
                    logger.warning("Signal SSE: error: %s (reconnecting in %.0fs)", e, backoff)

            if self._running:
                # 添加 20% 抖动以防止重连时的惊群效应
                jitter = backoff * 0.2 * random.random()
                await asyncio.sleep(backoff + jitter)
                backoff = min(backoff * 2, SSE_RETRY_DELAY_MAX)

        self._sse_response = None

    # ------------------------------------------------------------------
    # 健康监控
    # ------------------------------------------------------------------

    async def _health_monitor(self) -> None:
        """监控 SSE 连接健康状态，在连接陈旧时强制重连。"""
        while self._running:
            await asyncio.sleep(HEALTH_CHECK_INTERVAL)
            if not self._running:
                break

            elapsed = time.time() - self._last_sse_activity
            if elapsed > HEALTH_CHECK_STALE_THRESHOLD:
                logger.warning("Signal: SSE idle for %.0fs, checking daemon health", elapsed)
                try:
                    resp = await self.client.get(
                        f"{self.http_url}/api/v1/check", timeout=10.0
                    )
                    if resp.status_code == 200:
                        # 守护进程存活但 SSE 空闲 - 更新活动时间以
                        # 避免重复警告（连接可能只是没有新消息）
                        self._last_sse_activity = time.time()
                        logger.debug("Signal: daemon healthy, SSE idle")
                    else:
                        logger.warning("Signal: health check failed (%d), forcing reconnect", resp.status_code)
                        self._force_reconnect()
                except Exception as e:
                    logger.warning("Signal: health check error: %s, forcing reconnect", e)
                    self._force_reconnect()

    def _force_reconnect(self) -> None:
        """通过关闭当前响应来强制 SSE 重连。"""
        if self._sse_response and not self._sse_response.is_stream_consumed:
            try:
                task = asyncio.create_task(self._sse_response.aclose())
                self._background_tasks.add(task)
                task.add_done_callback(self._background_tasks.discard)
            except Exception:
                pass
            self._sse_response = None

    # ------------------------------------------------------------------
    # 消息处理
    # ------------------------------------------------------------------

    async def _handle_envelope(self, envelope: dict) -> None:
        """处理传入的 signal-cli 信封。"""
        # 如果存在嵌套信封则解包
        envelope_data = envelope.get("envelope", envelope)

        # 处理 syncMessage：提取"给自己的备忘"消息（发送给自己的账户），
        # 同时过滤其他同步事件（已读回执、输入状态等）
        is_note_to_self = False
        if "syncMessage" in envelope_data:
            sync_msg = envelope_data.get("syncMessage")
            if sync_msg and isinstance(sync_msg, dict):
                sent_msg = sync_msg.get("sentMessage")
                if sent_msg and isinstance(sent_msg, dict):
                    dest = sent_msg.get("destinationNumber") or sent_msg.get("destination")
                    sent_ts = sent_msg.get("timestamp")
                    if dest == self._account_normalized:
                        # 检查这是否是我们自己出站回复的回声
                        if sent_ts and sent_ts in self._recent_sent_timestamps:
                            self._recent_sent_timestamps.discard(sent_ts)
                            return
                        # 真正的用户"给自己的备忘" - 提升为 dataMessage
                        is_note_to_self = True
                        envelope_data = {**envelope_data, "dataMessage": sent_msg}
            if not is_note_to_self:
                return

        # 提取发送者信息
        sender = (
            envelope_data.get("sourceNumber")
            or envelope_data.get("sourceUuid")
            or envelope_data.get("source")
        )
        sender_name = envelope_data.get("sourceName", "")
        sender_uuid = envelope_data.get("sourceUuid", "")

        if not sender:
            logger.debug("Signal: ignoring envelope with no sender")
            return

        # 自消息过滤 - 防止回复循环（但允许"给自己的备忘"）
        if self._account_normalized and sender == self._account_normalized and not is_note_to_self:
            return

        # 过滤动态消息
        if self.ignore_stories and envelope_data.get("storyMessage"):
            return

        # 获取数据消息 - 也检查 editMessage（编辑后的消息在
        # editMessage.dataMessage 中包含更新后的 dataMessage）
        data_message = (
            envelope_data.get("dataMessage")
            or (envelope_data.get("editMessage") or {}).get("dataMessage")
        )
        if not data_message:
            return

        # 检查是否为群组消息
        group_info = data_message.get("groupInfo")
        group_id = group_info.get("groupId") if group_info else None
        is_group = bool(group_id)

        # 群组消息过滤 - 基于 SIGNAL_GROUP_ALLOWED_USERS 推导：
        # - 未设置环境变量 -> 群组禁用（默认安全行为）
        # - 设置了群组 ID -> 仅允许这些群组
        # - 设置了 "*" -> 允许所有群组
        # 私聊授权完全由 run.py (_is_user_authorized) 处理
        if is_group:
            if not self.group_allow_from:
                logger.debug("Signal: ignoring group message (no SIGNAL_GROUP_ALLOWED_USERS)")
                return
            if "*" not in self.group_allow_from and group_id not in self.group_allow_from:
                logger.debug("Signal: group %s not in allowlist", group_id[:8] if group_id else "?")
                return

        # 构建聊天信息
        chat_id = sender if not is_group else f"group:{group_id}"
        chat_type = "group" if is_group else "dm"

        # 提取文本并渲染提及
        text = data_message.get("message", "")
        mentions = data_message.get("mentions", [])
        if text and mentions:
            text = _render_mentions(text, mentions)

        # 处理附件
        attachments_data = data_message.get("attachments", [])
        media_urls = []
        media_types = []

        if attachments_data and not getattr(self, "ignore_attachments", False):
            for att in attachments_data:
                att_id = att.get("id")
                att_size = att.get("size", 0)
                if not att_id:
                    continue
                if att_size > SIGNAL_MAX_ATTACHMENT_SIZE:
                    logger.warning("Signal: attachment too large (%d bytes), skipping", att_size)
                    continue
                try:
                    cached_path, ext = await self._fetch_attachment(att_id)
                    if cached_path:
                        # 使用 Signal 提供的 contentType（如果有），否则根据扩展名映射
                        content_type = att.get("contentType") or _ext_to_mime(ext)
                        media_urls.append(cached_path)
                        media_types.append(content_type)
                except Exception:
                    logger.exception("Signal: failed to fetch attachment %s", att_id)

        # 构建会话来源
        source = self.build_source(
            chat_id=chat_id,
            chat_name=group_info.get("groupName") if group_info else sender_name,
            chat_type=chat_type,
            user_id=sender,
            user_name=sender_name or sender,
            user_id_alt=sender_uuid if sender_uuid else None,
            chat_id_alt=group_id if is_group else None,
        )

        # 根据媒体类型判断消息类型
        msg_type = MessageType.TEXT
        if media_types:
            if any(mt.startswith("audio/") for mt in media_types):
                msg_type = MessageType.VOICE
            elif any(mt.startswith("image/") for mt in media_types):
                msg_type = MessageType.PHOTO

        # 从信封数据解析时间戳（自纪元以来的毫秒数）
        ts_ms = envelope_data.get("timestamp", 0)
        if ts_ms:
            try:
                timestamp = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
            except (ValueError, OSError):
                timestamp = datetime.now(tz=timezone.utc)
        else:
            timestamp = datetime.now(tz=timezone.utc)

        # 构建并分发事件
        event = MessageEvent(
            source=source,
            text=text or "",
            message_type=msg_type,
            media_urls=media_urls,
            media_types=media_types,
            timestamp=timestamp,
        )

        logger.debug("Signal: message from %s in %s: %s",
                      redact_phone(sender), chat_id[:20], (text or "")[:50])

        await self.handle_message(event)

    # ------------------------------------------------------------------
    # 附件处理
    # ------------------------------------------------------------------

    async def _fetch_attachment(self, attachment_id: str) -> tuple:
        """通过 JSON-RPC 获取附件并缓存。返回 (路径, 扩展名)。"""
        result = await self._rpc("getAttachment", {
            "account": self.account,
            "id": attachment_id,
        })

        if not result:
            return None, ""

        # 处理字典响应（signal-cli 返回 {"data": "base64..."}）
        if isinstance(result, dict):
            result = result.get("data")
            if not result:
                logger.warning("Signal: attachment response missing 'data' key")
                return None, ""

        # 结果是 base64 编码的文件内容
        raw_data = base64.b64decode(result)
        ext = _guess_extension(raw_data)

        if _is_image_ext(ext):
            path = cache_image_from_bytes(raw_data, ext)
        elif _is_audio_ext(ext):
            path = cache_audio_from_bytes(raw_data, ext)
        else:
            path = cache_document_from_bytes(raw_data, ext)

        return path, ext

    # ------------------------------------------------------------------
    # JSON-RPC 通信
    # ------------------------------------------------------------------

    async def _rpc(self, method: str, params: dict, rpc_id: str = None) -> Any:
        """向 signal-cli 守护进程发送 JSON-RPC 2.0 请求。"""
        if not self.client:
            logger.warning("Signal: RPC called but client not connected")
            return None

        if rpc_id is None:
            rpc_id = f"{method}_{int(time.time() * 1000)}"

        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": rpc_id,
        }

        try:
            resp = await self.client.post(
                f"{self.http_url}/api/v1/rpc",
                json=payload,
                timeout=30.0,
            )
            resp.raise_for_status()
            data = resp.json()

            if "error" in data:
                logger.warning("Signal RPC error (%s): %s", method, data["error"])
                return None

            return data.get("result")

        except Exception as e:
            logger.warning("Signal RPC %s failed: %s", method, e)
            return None

    # ------------------------------------------------------------------
    # 消息发送
    # ------------------------------------------------------------------

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """发送文本消息。"""
        await self._stop_typing_indicator(chat_id)

        params: Dict[str, Any] = {
            "account": self.account,
            "message": content,
        }

        if chat_id.startswith("group:"):
            params["groupId"] = chat_id[6:]
        else:
            params["recipient"] = [chat_id]

        result = await self._rpc("send", params)

        if result is not None:
            self._track_sent_timestamp(result)
            # 使用 RPC 结果中的时间戳作为伪 message_id。
            # Signal 没有真正的消息 ID，但流消费者
            # 需要一个真值来正确执行编辑->回退路径。
            _msg_id = str(result.get("timestamp", "")) if isinstance(result, dict) else None
            return SendResult(success=True, message_id=_msg_id or None)
        return SendResult(success=False, error="RPC send failed")

    def _track_sent_timestamp(self, rpc_result) -> None:
        """记录出站消息时间戳用于回声过滤。"""
        ts = rpc_result.get("timestamp") if isinstance(rpc_result, dict) else None
        if ts:
            self._recent_sent_timestamps.add(ts)
            if len(self._recent_sent_timestamps) > self._max_recent_timestamps:
                self._recent_sent_timestamps.pop()

    async def send_typing(self, chat_id: str, metadata=None) -> None:
        """发送正在输入指示器。"""
        params: Dict[str, Any] = {
            "account": self.account,
        }

        if chat_id.startswith("group:"):
            params["groupId"] = chat_id[6:]
        else:
            params["recipient"] = [chat_id]

        await self._rpc("sendTyping", params, rpc_id="typing")

    async def send_image(
        self,
        chat_id: str,
        image_url: str,
        caption: Optional[str] = None,
        **kwargs,
    ) -> SendResult:
        """发送图片。支持 http(s):// 和 file:// URL。"""
        await self._stop_typing_indicator(chat_id)

        # 将图片解析为本地路径
        if image_url.startswith("file://"):
            file_path = unquote(image_url[7:])
        else:
            # 下载远程图片到缓存
            try:
                file_path = await cache_image_from_url(image_url)
            except Exception as e:
                logger.warning("Signal: failed to download image: %s", e)
                return SendResult(success=False, error=str(e))

        if not file_path or not Path(file_path).exists():
            return SendResult(success=False, error="Image file not found")

        # 验证文件大小
        file_size = Path(file_path).stat().st_size
        if file_size > SIGNAL_MAX_ATTACHMENT_SIZE:
            return SendResult(success=False, error=f"Image too large ({file_size} bytes)")

        params: Dict[str, Any] = {
            "account": self.account,
            "message": caption or "",
            "attachments": [file_path],
        }

        if chat_id.startswith("group:"):
            params["groupId"] = chat_id[6:]
        else:
            params["recipient"] = [chat_id]

        result = await self._rpc("send", params)
        if result is not None:
            self._track_sent_timestamp(result)
            return SendResult(success=True)
        return SendResult(success=False, error="RPC send with attachment failed")

    async def _send_attachment(
        self,
        chat_id: str,
        file_path: str,
        media_label: str,
        caption: Optional[str] = None,
    ) -> SendResult:
        """通过 RPC 发送任意文件作为 Signal 附件。

        send_document、send_image_file、send_voice
        和 send_video 的共享实现 - 避免重复验证/路由/RPC 逻辑。
        """
        await self._stop_typing_indicator(chat_id)

        try:
            file_size = Path(file_path).stat().st_size
        except FileNotFoundError:
            return SendResult(success=False, error=f"{media_label} file not found: {file_path}")

        if file_size > SIGNAL_MAX_ATTACHMENT_SIZE:
            return SendResult(success=False, error=f"{media_label} too large ({file_size} bytes)")

        params: Dict[str, Any] = {
            "account": self.account,
            "message": caption or "",
            "attachments": [file_path],
        }

        if chat_id.startswith("group:"):
            params["groupId"] = chat_id[6:]
        else:
            params["recipient"] = [chat_id]

        result = await self._rpc("send", params)
        if result is not None:
            self._track_sent_timestamp(result)
            return SendResult(success=True)
        return SendResult(success=False, error=f"RPC send {media_label.lower()} failed")

    async def send_document(
        self,
        chat_id: str,
        file_path: str,
        caption: Optional[str] = None,
        filename: Optional[str] = None,
        **kwargs,
    ) -> SendResult:
        """发送文档/文件附件。"""
        return await self._send_attachment(chat_id, file_path, "File", caption)

    async def send_image_file(
        self,
        chat_id: str,
        image_path: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        **kwargs,
    ) -> SendResult:
        """将本地图片文件作为原生 Signal 附件发送。

        当从代理响应中提取包含图片路径的 MEDIA: 标签时，
        由网关媒体传递流程调用。
        """
        return await self._send_attachment(chat_id, image_path, "Image", caption)

    async def send_voice(
        self,
        chat_id: str,
        audio_path: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        **kwargs,
    ) -> SendResult:
        """Send an audio file as a Signal attachment.

        Signal does not distinguish voice messages from file attachments at
        the API level, so this routes through the same RPC send path.
        """
        return await self._send_attachment(chat_id, audio_path, "Audio", caption)

    async def send_video(
        self,
        chat_id: str,
        video_path: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        **kwargs,
    ) -> SendResult:
        """Send a video file as a Signal attachment."""
        return await self._send_attachment(chat_id, video_path, "Video", caption)

    # ------------------------------------------------------------------
    # Typing Indicators
    # ------------------------------------------------------------------

    async def _stop_typing_indicator(self, chat_id: str) -> None:
        """Stop a typing indicator loop for a chat."""
        task = self._typing_tasks.pop(chat_id, None)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def stop_typing(self, chat_id: str) -> None:
        """Public interface for stopping typing — called by base adapter's
        _keep_typing finally block to clean up platform-level typing tasks."""
        await self._stop_typing_indicator(chat_id)

    # ------------------------------------------------------------------
    # Chat Info
    # ------------------------------------------------------------------

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        """Get information about a chat/contact."""
        if chat_id.startswith("group:"):
            return {
                "name": chat_id,
                "type": "group",
                "chat_id": chat_id,
            }

        # Try to resolve contact name
        result = await self._rpc("getContact", {
            "account": self.account,
            "contactAddress": chat_id,
        })

        name = chat_id
        if result and isinstance(result, dict):
            name = result.get("name") or result.get("profileName") or chat_id

        return {
            "name": name,
            "type": "dm",
            "chat_id": chat_id,
        }
