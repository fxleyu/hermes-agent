"""
QQ Bot 平台适配器，使用官方 QQ Bot API (v2)。

通过 QQ Bot WebSocket 网关接收入站事件，使用
REST API（``api.sgroup.qq.com``）发送出站消息和上传媒体文件。

在 config.yaml 中的配置方式：
    platforms:
      qq:
        enabled: true
        extra:
          app_id: "your-app-id"            # 或设置 QQ_APP_ID 环境变量
          client_secret: "your-secret"     # 或设置 QQ_CLIENT_SECRET 环境变量
          markdown_support: true           # 启用 QQ Markdown（msg_type 2）
          dm_policy: "open"                # open | allowlist | disabled
          allow_from: ["openid_1"]
          group_policy: "open"             # open | allowlist | disabled
          group_allow_from: ["group_openid_1"]
          stt:                             # 语音转文字配置（可选）
            provider: "zai"                # zai (GLM-ASR)、openai (Whisper) 等
            baseUrl: "https://open.bigmodel.cn/api/coding/paas/v4"
            apiKey: "your-stt-api-key"     # 或设置 QQ_STT_API_KEY 环境变量
            model: "glm-asr"               # glm-asr、whisper-1 等

    语音转录优先级：
      1. QQ 内置的 ``asr_refer_text``（腾讯 ASR——免费，始终优先尝试）
      2. 通过 ``stt`` 配置或 ``QQ_STT_*`` 环境变量配置的 STT 提供商

参考文档：https://bot.q.qq.com/wiki/develop/api-v2/
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import mimetypes
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

try:
    import aiohttp
    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False
    aiohttp = None  # type: ignore[assignment]

try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False
    httpx = None  # type: ignore[assignment]

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import (
    BasePlatformAdapter,
    MessageEvent,
    MessageType,
    SendResult,
    cache_document_from_bytes,
    cache_image_from_bytes,
)
from gateway.platforms.helpers import strip_markdown

logger = logging.getLogger(__name__)


class QQCloseError(Exception):
    """当 QQ WebSocket 以特定关闭码关闭时抛出。

    携带关闭码和原因，供重连循环正确处理。
    """

    def __init__(self, code, reason=""):
        self.code = int(code) if code else None
        self.reason = str(reason) if reason else ""
        super().__init__(f"WebSocket closed (code={self.code}, reason={self.reason})")
# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

API_BASE = "https://api.sgroup.qq.com"
TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"
GATEWAY_URL_PATH = "/gateway"

DEFAULT_API_TIMEOUT = 30.0
FILE_UPLOAD_TIMEOUT = 120.0
CONNECT_TIMEOUT_SECONDS = 20.0

RECONNECT_BACKOFF = [2, 5, 10, 30, 60]  # 重连退避时间表（秒）
MAX_RECONNECT_ATTEMPTS = 100  # 最大重连尝试次数
RATE_LIMIT_DELAY = 60  # 限速等待时间（秒）
QUICK_DISCONNECT_THRESHOLD = 5.0  # 快速断开阈值（秒）
MAX_QUICK_DISCONNECT_COUNT = 3  # 允许的快速断开最大次数

MAX_MESSAGE_LENGTH = 4000  # 消息最大长度
DEDUP_WINDOW_SECONDS = 300  # 消息去重窗口（秒）
DEDUP_MAX_SIZE = 1000  # 去重缓存最大大小

# QQ Bot 消息类型
MSG_TYPE_TEXT = 0       # 纯文本
MSG_TYPE_MARKDOWN = 2   # Markdown
MSG_TYPE_MEDIA = 7      # 媒体（图片/视频/音频/文件）
MSG_TYPE_INPUT_NOTIFY = 6  # 输入状态通知

# QQ Bot 文件媒体类型
MEDIA_TYPE_IMAGE = 1  # 图片
MEDIA_TYPE_VIDEO = 2  # 视频
MEDIA_TYPE_VOICE = 3  # 语音
MEDIA_TYPE_FILE = 4   # 文件


def check_qq_requirements() -> bool:
    """检查 QQ 运行时依赖项是否可用。"""
    return AIOHTTP_AVAILABLE and HTTPX_AVAILABLE


def _coerce_list(value: Any) -> List[str]:
    """将配置值强制转换为去空格的字符串列表。"""
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


# ---------------------------------------------------------------------------
# QQ 适配器
# ---------------------------------------------------------------------------

class QQAdapter(BasePlatformAdapter):
    """QQ Bot 适配器，基于官方 QQ Bot WebSocket 网关 + REST API。"""

    # QQ Bot API 不支持编辑已发送的消息
    SUPPORTS_MESSAGE_EDITING = False

    def _fail_pending(self, reason: str) -> None:
        """将所有待处理的响应 Future 置为失败状态。"""
        for fut in self._pending_responses.values():
            if not fut.done():
                fut.set_exception(RuntimeError(reason))
        self._pending_responses.clear()

    MAX_MESSAGE_LENGTH = MAX_MESSAGE_LENGTH

    def __init__(self, config: PlatformConfig):
        super().__init__(config, Platform.QQBOT)

        extra = config.extra or {}
        self._app_id = str(extra.get("app_id") or os.getenv("QQ_APP_ID", "")).strip()
        self._client_secret = str(extra.get("client_secret") or os.getenv("QQ_CLIENT_SECRET", "")).strip()
        self._markdown_support = bool(extra.get("markdown_support", True))

        # 认证/访问控制策略
        self._dm_policy = str(extra.get("dm_policy", "open")).strip().lower()
        self._allow_from = _coerce_list(extra.get("allow_from") or extra.get("allowFrom"))
        self._group_policy = str(extra.get("group_policy", "open")).strip().lower()
        self._group_allow_from = _coerce_list(extra.get("group_allow_from") or extra.get("groupAllowFrom"))

        # 连接状态
        self._session: Optional[aiohttp.ClientSession] = None
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._http_client: Optional[httpx.AsyncClient] = None
        self._listen_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._heartbeat_interval: float = 30.0  # 秒，由 Hello 事件更新
        self._session_id: Optional[str] = None
        self._last_seq: Optional[int] = None
        self._chat_type_map: Dict[str, str] = {}  # chat_id → "c2c"|"group"|"guild"|"dm"

        # 请求/响应关联
        self._pending_responses: Dict[str, asyncio.Future] = {}
        self._seen_messages: Dict[str, float] = {}

        # 令牌缓存
        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0.0
        self._token_lock = asyncio.Lock()

        # 上传缓存：content_hash -> {file_info, file_uuid, expires_at}
        self._upload_cache: Dict[str, Dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # 属性
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "QQBot"

    # ------------------------------------------------------------------
    # 连接生命周期
    # ------------------------------------------------------------------

    async def connect(self) -> bool:
        """认证并获取网关 URL，然后打开 WebSocket 连接。"""
        if not AIOHTTP_AVAILABLE:
            message = "QQ startup failed: aiohttp not installed"
            self._set_fatal_error("qq_missing_dependency", message, retryable=True)
            logger.warning("[%s] %s. Run: pip install aiohttp", self.name, message)
            return False
        if not HTTPX_AVAILABLE:
            message = "QQ startup failed: httpx not installed"
            self._set_fatal_error("qq_missing_dependency", message, retryable=True)
            logger.warning("[%s] %s. Run: pip install httpx", self.name, message)
            return False
        if not self._app_id or not self._client_secret:
            message = "QQ startup failed: QQ_APP_ID and QQ_CLIENT_SECRET are required"
            self._set_fatal_error("qq_missing_credentials", message, retryable=True)
            logger.warning("[%s] %s", self.name, message)
            return False

        # 防止使用相同凭据的重复连接
        if not self._acquire_platform_lock(
            "qqbot-appid", self._app_id, "QQBot app ID"
        ):
            return False

        try:
            self._http_client = httpx.AsyncClient(timeout=30.0, follow_redirects=True)

            # 1. 获取访问令牌
            await self._ensure_token()

            # 2. 获取 WebSocket 网关 URL
            gateway_url = await self._get_gateway_url()
            logger.info("[%s] Gateway URL: %s", self.name, gateway_url)

            # 3. 打开 WebSocket 连接
            await self._open_ws(gateway_url)

            # 4. 启动监听任务
            self._listen_task = asyncio.create_task(self._listen_loop())
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
            self._mark_connected()
            logger.info("[%s] Connected", self.name)
            return True
        except Exception as exc:
            message = f"QQ startup failed: {exc}"
            self._set_fatal_error("qq_connect_error", message, retryable=True)
            logger.error("[%s] %s", self.name, message, exc_info=True)
            await self._cleanup()
            self._release_platform_lock()
            return False

    async def disconnect(self) -> None:
        """关闭所有连接并停止监听任务。"""
        self._running = False
        self._mark_disconnected()

        if self._listen_task:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
            self._listen_task = None

        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
            self._heartbeat_task = None

        await self._cleanup()
        self._release_platform_lock()
        logger.info("[%s] Disconnected", self.name)

    async def _cleanup(self) -> None:
        """关闭 WebSocket、HTTP 会话和客户端。"""
        if self._ws and not self._ws.closed:
            await self._ws.close()
        self._ws = None

        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None

        # 使所有待处理的 Future 失败
        for fut in self._pending_responses.values():
            if not fut.done():
                fut.set_exception(RuntimeError("Disconnected"))
        self._pending_responses.clear()

    # ------------------------------------------------------------------
    # 令牌管理
    # ------------------------------------------------------------------

    async def _ensure_token(self) -> str:
        """返回有效的访问令牌，如需要则刷新（使用 singleflight 模式防止并发刷新）。

        采用双重检查锁定模式：先检查令牌是否过期（提前 60 秒），
        获取锁后再次检查，避免多个协程同时刷新令牌。
        """
        if self._access_token and time.time() < self._token_expires_at - 60:
            return self._access_token

        async with self._token_lock:
            # 获取锁后再次检查（双重检查锁定）
            if self._access_token and time.time() < self._token_expires_at - 60:
                return self._access_token

            try:
                resp = await self._http_client.post(
                    TOKEN_URL,
                    json={"appId": self._app_id, "clientSecret": self._client_secret},
                    timeout=DEFAULT_API_TIMEOUT,
                )
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc:
                raise RuntimeError(f"Failed to get QQ Bot access token: {exc}") from exc

            token = data.get("access_token")
            if not token:
                raise RuntimeError(f"QQ Bot token response missing access_token: {data}")

            expires_in = int(data.get("expires_in", 7200))
            self._access_token = token
            self._token_expires_at = time.time() + expires_in
            logger.info("[%s] Access token refreshed, expires in %ds", self.name, expires_in)
            return self._access_token

    async def _get_gateway_url(self) -> str:
        """从 REST API 获取 WebSocket 网关 URL。"""
        token = await self._ensure_token()
        try:
            resp = await self._http_client.get(
                f"{API_BASE}{GATEWAY_URL_PATH}",
                headers={"Authorization": f"QQBot {token}"},
                timeout=DEFAULT_API_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            raise RuntimeError(f"Failed to get QQ Bot gateway URL: {exc}") from exc

        url = data.get("url")
        if not url:
            raise RuntimeError(f"QQ Bot gateway response missing url: {data}")
        return url

    # ------------------------------------------------------------------
    # WebSocket 生命周期
    # ------------------------------------------------------------------

    async def _open_ws(self, gateway_url: str) -> None:
        """打开到 QQ Bot 网关的 WebSocket 连接。"""
        # 仅清理 WebSocket 资源——保留 _http_client 供 REST API 调用使用
        if self._ws and not self._ws.closed:
            await self._ws.close()
        self._ws = None
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

        self._session = aiohttp.ClientSession()
        self._ws = await self._session.ws_connect(
            gateway_url,
            timeout=CONNECT_TIMEOUT_SECONDS,
        )
        logger.info("[%s] WebSocket connected to %s", self.name, gateway_url)

    async def _listen_loop(self) -> None:
        """读取 WebSocket 事件并在出错时重连。

        关闭码处理遵循 OpenClaw qqbot 参考实现：
          4004 → 令牌无效，刷新令牌后重连
          4006/4007/4009 → 会话无效，清除会话后重新鉴权
          4008 → 限速，等待 60 秒后重试
          4914 → 机器人离线/仅沙箱，停止重连
          4915 → 机器人被封禁，停止重连
        """
        backoff_idx = 0
        connect_time = 0.0
        quick_disconnect_count = 0

        while self._running:
            try:
                connect_time = time.monotonic()
                await self._read_events()
                backoff_idx = 0
                quick_disconnect_count = 0
            except asyncio.CancelledError:
                return
            except QQCloseError as exc:
                if not self._running:
                    return

                code = exc.code
                logger.warning("[%s] WebSocket closed: code=%s reason=%s",
                              self.name, code, exc.reason)

                # 快速断开检测（权限问题、配置错误等）
                duration = time.monotonic() - connect_time
                if duration < QUICK_DISCONNECT_THRESHOLD and connect_time > 0:
                    quick_disconnect_count += 1
                    logger.info("[%s] Quick disconnect (%.1fs), count: %d",
                               self.name, duration, quick_disconnect_count)
                    if quick_disconnect_count >= MAX_QUICK_DISCONNECT_COUNT:
                        logger.error(
                            "[%s] Too many quick disconnects. "
                            "Check: 1) AppID/Secret correct 2) Bot permissions on QQ Open Platform",
                            self.name,
                        )
                        self._set_fatal_error("qq_quick_disconnect",
                            "Too many quick disconnects — check bot permissions", retryable=True)
                        return
                else:
                    quick_disconnect_count = 0

                self._mark_disconnected()
                self._fail_pending("Connection closed")

                # 对致命关闭码停止重连
                if code in (4914, 4915):
                    desc = "offline/sandbox-only" if code == 4914 else "banned"
                    logger.error("[%s] Bot is %s. Check QQ Open Platform.", self.name, desc)
                    self._set_fatal_error(f"qq_{desc}", f"Bot is {desc}", retryable=False)
                    return

                # 被限速
                if code == 4008:
                    logger.info("[%s] Rate limited (4008), waiting %ds", self.name, RATE_LIMIT_DELAY)
                    if backoff_idx >= MAX_RECONNECT_ATTEMPTS:
                        return
                    await asyncio.sleep(RATE_LIMIT_DELAY)
                    if await self._reconnect(backoff_idx):
                        backoff_idx = 0
                        quick_disconnect_count = 0
                    else:
                        backoff_idx += 1
                    continue

                # 令牌无效 → 清除缓存令牌以便 _ensure_token() 刷新
                if code == 4004:
                    logger.info("[%s] Invalid token (4004), will refresh and reconnect", self.name)
                    self._access_token = None
                    self._token_expires_at = 0.0

                # 会话无效 → 清除会话，下次 Hello 时将重新鉴权
                if code in (4006, 4007, 4009, 4900, 4901, 4902, 4903, 4904, 4905,
                           4906, 4907, 4908, 4909, 4910, 4911, 4912, 4913):
                    logger.info("[%s] Session error (%d), clearing session for re-identify", self.name, code)
                    self._session_id = None
                    self._last_seq = None

                if await self._reconnect(backoff_idx):
                    backoff_idx = 0
                    quick_disconnect_count = 0
                else:
                    backoff_idx += 1

            except Exception as exc:
                if not self._running:
                    return
                logger.warning("[%s] WebSocket error: %s", self.name, exc)
                self._mark_disconnected()
                self._fail_pending("Connection interrupted")

                if backoff_idx >= MAX_RECONNECT_ATTEMPTS:
                    logger.error("[%s] Max reconnect attempts reached", self.name)
                    return

                if await self._reconnect(backoff_idx):
                    backoff_idx = 0
                    quick_disconnect_count = 0
                else:
                    backoff_idx += 1

    async def _reconnect(self, backoff_idx: int) -> bool:
        """尝试重连 WebSocket。成功返回 True。"""
        delay = RECONNECT_BACKOFF[min(backoff_idx, len(RECONNECT_BACKOFF) - 1)]
        logger.info("[%s] Reconnecting in %ds (attempt %d)...", self.name, delay, backoff_idx + 1)
        await asyncio.sleep(delay)

        self._heartbeat_interval = 30.0  # 重置，等待 Hello 事件更新
        try:
            await self._ensure_token()
            gateway_url = await self._get_gateway_url()
            await self._open_ws(gateway_url)
            self._mark_connected()
            logger.info("[%s] Reconnected", self.name)
            return True
        except Exception as exc:
            logger.warning("[%s] Reconnect failed: %s", self.name, exc)
            return False

    async def _read_events(self) -> None:
        """读取 WebSocket 帧直到连接关闭。"""
        if not self._ws:
            raise RuntimeError("WebSocket not connected")

        while self._running and self._ws and not self._ws.closed:
            msg = await self._ws.receive()
            if msg.type == aiohttp.WSMsgType.TEXT:
                payload = self._parse_json(msg.data)
                if payload:
                    self._dispatch_payload(payload)
            elif msg.type in (aiohttp.WSMsgType.PING,):
                # aiohttp 会自动回复 PONG
                pass
            elif msg.type == aiohttp.WSMsgType.CLOSE:
                raise QQCloseError(msg.data, msg.extra)
            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                raise RuntimeError("WebSocket closed")

    async def _heartbeat_loop(self) -> None:
        """定期发送心跳（QQ 网关期望 op 1 心跳携带最新 seq）。

        心跳间隔由 Hello（op 10）事件的 heartbeat_interval 字段设定。
        QQ 默认约为 41 秒；我们以 80% 的间隔发送以保证安全。
        """
        try:
            while self._running:
                await asyncio.sleep(self._heartbeat_interval)
                if not self._ws or self._ws.closed:
                    continue
                try:
                    # d 应为最近接收到的序列号，若没有则为 null
                    await self._ws.send_json({"op": 1, "d": self._last_seq})
                except Exception as exc:
                    logger.debug("[%s] Heartbeat failed: %s", self.name, exc)
        except asyncio.CancelledError:
            pass

    async def _send_identify(self) -> None:
        """发送 op 2 Identify 以认证 WebSocket 连接。

        收到 op 10 Hello 后，客户端必须发送 op 2 Identify，
        携带机器人令牌和意图（intents）。成功后服务器回复 READY 分发事件。

        参考：https://bot.q.qq.com/wiki/develop/api-v2/dev-prepare/interface-framework/reference.html
        """
        token = await self._ensure_token()
        identify_payload = {
            "op": 2,
            "d": {
                "token": f"QQBot {token}",
                "intents": (1 << 25) | (1 << 30) | (1 << 12),  # C2C_GROUP_AT_MESSAGES + PUBLIC_GUILD_MESSAGES + DIRECT_MESSAGE（C2C群@消息 + 公域频道消息 + 私信）
                "shard": [0, 1],
                "properties": {
                    "$os": "macOS",
                    "$browser": "hermes-agent",
                    "$device": "hermes-agent",
                },
            },
        }
        try:
            if self._ws and not self._ws.closed:
                await self._ws.send_json(identify_payload)
                logger.info("[%s] Identify sent", self.name)
            else:
                logger.warning("[%s] Cannot send Identify: WebSocket not connected", self.name)
        except Exception as exc:
            logger.error("[%s] Failed to send Identify: %s", self.name, exc)

    async def _send_resume(self) -> None:
        """发送 op 6 Resume 以在重连后恢复会话。

        参考：https://bot.q.qq.com/wiki/develop/api-v2/dev-prepare/interface-framework/reference.html
        """
        token = await self._ensure_token()
        resume_payload = {
            "op": 6,
            "d": {
                "token": f"QQBot {token}",
                "session_id": self._session_id,
                "seq": self._last_seq,
            },
        }
        try:
            if self._ws and not self._ws.closed:
                await self._ws.send_json(resume_payload)
                logger.info("[%s] Resume sent (session_id=%s, seq=%s)",
                             self.name, self._session_id, self._last_seq)
            else:
                logger.warning("[%s] Cannot send Resume: WebSocket not connected", self.name)
        except Exception as exc:
            logger.error("[%s] Failed to send Resume: %s", self.name, exc)
            # Resume 失败时，清除会话，下次 Hello 时回退到 Identify
            self._session_id = None
            self._last_seq = None

    @staticmethod
    def _create_task(coro):
        """调度一个协程，如果没有运行中的事件循环则静默跳过。

        避免在测试中同步调用 ``_dispatch_payload`` 时产生
        ``RuntimeError: no running event loop`` 错误。
        """
        try:
            loop = asyncio.get_running_loop()
            return loop.create_task(coro)
        except RuntimeError:
            return None

    def _dispatch_payload(self, payload: Dict[str, Any]) -> None:
        """路由入站 WebSocket 载荷（同步分发，异步处理器通过 create_task 执行）。"""
        op = payload.get("op")
        t = payload.get("t")
        s = payload.get("s")
        d = payload.get("d")
        if isinstance(s, int) and (self._last_seq is None or s > self._last_seq):
            self._last_seq = s

        # op 10 = Hello（心跳间隔）——必须回复 Identify/Resume
        if op == 10:
            d_data = d if isinstance(d, dict) else {}
            interval_ms = d_data.get("heartbeat_interval", 30000)
            # 以服务器间隔的 80% 发送心跳以确保安全
            self._heartbeat_interval = interval_ms / 1000.0 * 0.8
            logger.debug("[%s] Hello received, heartbeat_interval=%dms (sending every %.1fs)",
                        self.name, interval_ms, self._heartbeat_interval)
            # 鉴权：如果有会话则发送 Resume，否则发送 Identify
            # 使用 _create_task 在没有事件循环时也安全（用于测试）
            if self._session_id and self._last_seq is not None:
                self._create_task(self._send_resume())
            else:
                self._create_task(self._send_identify())
            return

        # op 0 = 分发事件
        if op == 0 and t:
            if t == "READY":
                self._handle_ready(d)
            elif t == "RESUMED":
                logger.info("[%s] Session resumed", self.name)
            elif t in ("C2C_MESSAGE_CREATE", "GROUP_AT_MESSAGE_CREATE",
                        "DIRECT_MESSAGE_CREATE", "GUILD_MESSAGE_CREATE",
                        "GUILD_AT_MESSAGE_CREATE"):
                asyncio.create_task(self._on_message(t, d))
            else:
                logger.debug("[%s] Unhandled dispatch: %s", self.name, t)
            return

        # op 11 = 心跳确认
        if op == 11:
            return

        logger.debug("[%s] Unknown op: %s", self.name, op)

    def _handle_ready(self, d: Any) -> None:
        """处理 READY 事件——存储 session_id 以供恢复使用。"""
        if isinstance(d, dict):
            self._session_id = d.get("session_id")
            logger.info("[%s] Ready, session_id=%s", self.name, self._session_id)

    # ------------------------------------------------------------------
    # JSON 辅助工具
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_json(raw: Any) -> Optional[Dict[str, Any]]:
        try:
            payload = json.loads(raw)
        except Exception:
            logger.debug("[%s] Failed to parse JSON: %r", "QQBot", raw)
            return None
        return payload if isinstance(payload, dict) else None

    @staticmethod
    def _next_msg_seq(msg_id: str) -> int:
        """生成 0..65535 范围内的消息序列号。"""
        time_part = int(time.time()) % 100000000
        rand = int(uuid.uuid4().hex[:4], 16)
        return (time_part ^ rand) % 65536

    # ------------------------------------------------------------------
    # 入站消息处理
    # ------------------------------------------------------------------

    async def _on_message(self, event_type: str, d: Any) -> None:
        """处理入站 QQ Bot 消息事件。"""
        if not isinstance(d, dict):
            return

        # 提取通用字段
        msg_id = str(d.get("id", ""))
        if not msg_id or self._is_duplicate(msg_id):
            logger.debug("[%s] Duplicate or missing message id: %s", self.name, msg_id)
            return

        timestamp = str(d.get("timestamp", ""))
        content = str(d.get("content", "")).strip()
        author = d.get("author") if isinstance(d.get("author"), dict) else {}

        # 按事件类型路由
        if event_type == "C2C_MESSAGE_CREATE":
            await self._handle_c2c_message(d, msg_id, content, author, timestamp)
        elif event_type in ("GROUP_AT_MESSAGE_CREATE",):
            await self._handle_group_message(d, msg_id, content, author, timestamp)
        elif event_type in ("GUILD_MESSAGE_CREATE", "GUILD_AT_MESSAGE_CREATE"):
            await self._handle_guild_message(d, msg_id, content, author, timestamp)
        elif event_type == "DIRECT_MESSAGE_CREATE":
            await self._handle_dm_message(d, msg_id, content, author, timestamp)

    async def _handle_c2c_message(
        self, d: Dict[str, Any], msg_id: str, content: str, author: Dict[str, Any], timestamp: str
    ) -> None:
        """处理 C2C（私聊）消息事件。"""
        user_openid = str(author.get("user_openid", ""))
        if not user_openid:
            return
        if not self._is_dm_allowed(user_openid):
            return

        text = content
        attachments_raw = d.get("attachments")
        logger.info("[QQ] C2C message: id=%s content=%r attachments=%s",
                    msg_id, content[:50] if content else "",
                    f"{len(attachments_raw) if isinstance(attachments_raw, list) else 0} items"
                    if attachments_raw else "None")
        if attachments_raw and isinstance(attachments_raw, list):
            for _i, _att in enumerate(attachments_raw):
                if isinstance(_att, dict):
                    logger.info("[QQ]   attachment[%d]: content_type=%s url=%s filename=%s",
                                _i, _att.get("content_type", ""),
                                str(_att.get("url", ""))[:80],
                                _att.get("filename", ""))

        # 统一处理所有附件（图片、语音、文件）
        att_result = await self._process_attachments(attachments_raw)
        image_urls = att_result["image_urls"]
        image_media_types = att_result["image_media_types"]
        voice_transcripts = att_result["voice_transcripts"]
        attachment_info = att_result["attachment_info"]

        # 将语音转录文本追加到正文
        if voice_transcripts:
            voice_block = "\n".join(voice_transcripts)
            text = (text + "\n\n" + voice_block).strip() if text.strip() else voice_block
        # 追加非媒体附件信息
        if attachment_info:
            text = (text + "\n\n" + attachment_info).strip() if text.strip() else attachment_info

        logger.info("[QQ] After processing: images=%d, voice=%d",
                    len(image_urls), len(voice_transcripts))

        if not text.strip() and not image_urls:
            return

        self._chat_type_map[user_openid] = "c2c"
        event = MessageEvent(
            source=self.build_source(
                chat_id=user_openid,
                user_id=user_openid,
                chat_type="dm",
            ),
            text=text,
            message_type=self._detect_message_type(image_urls, image_media_types),
            raw_message=d,
            message_id=msg_id,
            media_urls=image_urls,
            media_types=image_media_types,
            timestamp=self._parse_qq_timestamp(timestamp),
        )
        await self.handle_message(event)

    async def _handle_group_message(
        self, d: Dict[str, Any], msg_id: str, content: str, author: Dict[str, Any], timestamp: str
    ) -> None:
        """处理群 @消息事件。"""
        group_openid = str(d.get("group_openid", ""))
        if not group_openid:
            return
        if not self._is_group_allowed(group_openid, str(author.get("member_openid", ""))):
            return

        # 去除内容中的 @bot 前缀
        text = self._strip_at_mention(content)
        att_result = await self._process_attachments(d.get("attachments"))
        image_urls = att_result["image_urls"]
        image_media_types = att_result["image_media_types"]
        voice_transcripts = att_result["voice_transcripts"]
        attachment_info = att_result["attachment_info"]

        # 追加语音转录
        if voice_transcripts:
            voice_block = "\n".join(voice_transcripts)
            text = (text + "\n\n" + voice_block).strip() if text.strip() else voice_block
        if attachment_info:
            text = (text + "\n\n" + attachment_info).strip() if text.strip() else attachment_info

        if not text.strip() and not image_urls:
            return

        self._chat_type_map[group_openid] = "group"
        event = MessageEvent(
            source=self.build_source(
                chat_id=group_openid,
                user_id=str(author.get("member_openid", "")),
                chat_type="group",
            ),
            text=text,
            message_type=self._detect_message_type(image_urls, image_media_types),
            raw_message=d,
            message_id=msg_id,
            media_urls=image_urls,
            media_types=image_media_types,
            timestamp=self._parse_qq_timestamp(timestamp),
        )
        await self.handle_message(event)

    async def _handle_guild_message(
        self, d: Dict[str, Any], msg_id: str, content: str, author: Dict[str, Any], timestamp: str
    ) -> None:
        """处理频道/子频道消息事件。"""
        channel_id = str(d.get("channel_id", ""))
        if not channel_id:
            return

        member = d.get("member") if isinstance(d.get("member"), dict) else {}
        nick = str(member.get("nick", "")) or str(author.get("username", ""))

        text = content
        att_result = await self._process_attachments(d.get("attachments"))
        image_urls = att_result["image_urls"]
        image_media_types = att_result["image_media_types"]
        voice_transcripts = att_result["voice_transcripts"]
        attachment_info = att_result["attachment_info"]

        if voice_transcripts:
            voice_block = "\n".join(voice_transcripts)
            text = (text + "\n\n" + voice_block).strip() if text.strip() else voice_block
        if attachment_info:
            text = (text + "\n\n" + attachment_info).strip() if text.strip() else attachment_info

        if not text.strip() and not image_urls:
            return

        self._chat_type_map[channel_id] = "guild"
        event = MessageEvent(
            source=self.build_source(
                chat_id=channel_id,
                user_id=str(author.get("id", "")),
                user_name=nick or None,
                chat_type="group",
            ),
            text=text,
            message_type=self._detect_message_type(image_urls, image_media_types),
            raw_message=d,
            message_id=msg_id,
            media_urls=image_urls,
            media_types=image_media_types,
            timestamp=self._parse_qq_timestamp(timestamp),
        )
        await self.handle_message(event)

    async def _handle_dm_message(
        self, d: Dict[str, Any], msg_id: str, content: str, author: Dict[str, Any], timestamp: str
    ) -> None:
        """处理频道私信消息事件。"""
        guild_id = str(d.get("guild_id", ""))
        if not guild_id:
            return

        text = content
        att_result = await self._process_attachments(d.get("attachments"))
        image_urls = att_result["image_urls"]
        image_media_types = att_result["image_media_types"]
        voice_transcripts = att_result["voice_transcripts"]
        attachment_info = att_result["attachment_info"]

        if voice_transcripts:
            voice_block = "\n".join(voice_transcripts)
            text = (text + "\n\n" + voice_block).strip() if text.strip() else voice_block
        if attachment_info:
            text = (text + "\n\n" + attachment_info).strip() if text.strip() else attachment_info

        if not text.strip() and not image_urls:
            return

        self._chat_type_map[guild_id] = "dm"
        event = MessageEvent(
            source=self.build_source(
                chat_id=guild_id,
                user_id=str(author.get("id", "")),
                chat_type="dm",
            ),
            text=text,
            message_type=self._detect_message_type(image_urls, image_media_types),
            raw_message=d,
            message_id=msg_id,
            media_urls=image_urls,
            media_types=image_media_types,
            timestamp=self._parse_qq_timestamp(timestamp),
        )
        await self.handle_message(event)

    # ------------------------------------------------------------------
    # 附件处理
    # ------------------------------------------------------------------


    @staticmethod
    def _detect_message_type(media_urls: list, media_types: list):
        """根据附件的 content_type 判定消息类型。"""
        if not media_urls:
            return MessageType.TEXT
        if not media_types:
            return MessageType.PHOTO
        first_type = media_types[0].lower() if media_types else ""
        if "audio" in first_type or "voice" in first_type or "silk" in first_type:
            return MessageType.VOICE
        if "video" in first_type:
            return MessageType.VIDEO
        if "image" in first_type or "photo" in first_type:
            return MessageType.PHOTO
        # 未知的 content_type 但有附件——不要假设为 PHOTO，
        # 以防止非图片文件被发送到视觉分析模块
        logger.debug("[QQ] Unknown media content_type '%s', defaulting to TEXT", first_type)
        return MessageType.TEXT

    async def _process_attachments(
        self, attachments: Any,
    ) -> Dict[str, Any]:
        """处理入站附件（所有消息类型通用）。

        参照 OpenClaw 的 ``processAttachments``——统一处理图片、语音和其他文件。

        返回字典：
        - image_urls: list[str]  — 缓存的本地图片路径
        - image_media_types: list[str] — 缓存图片的 MIME 类型
        - voice_transcripts: list[str] — 语音消息的 STT 转录文本
        - attachment_info: str — 非图片、非语音附件的文本描述
        """
        if not isinstance(attachments, list):
            return {"image_urls": [], "image_media_types": [],
                    "voice_transcripts": [], "attachment_info": ""}

        image_urls: List[str] = []
        image_media_types: List[str] = []
        voice_transcripts: List[str] = []
        other_attachments: List[str] = []

        for att in attachments:
            if not isinstance(att, dict):
                continue

            ct = str(att.get("content_type", "")).strip().lower()
            url_raw = str(att.get("url", "")).strip()
            filename = str(att.get("filename", ""))
            if url_raw.startswith("//"):
                url = f"https:{url_raw}"
            elif url_raw:
                url = url_raw
            else:
                url = ""
                continue

            logger.debug("[QQ] Processing attachment: content_type=%s, url=%s, filename=%s",
                         ct, url[:80], filename)

            if self._is_voice_content_type(ct, filename):
                # 语音：优先使用 QQ 的 asr_refer_text，其次 voice_wav_url，最后 STT
                asr_refer = (
                    str(att.get("asr_refer_text", "")).strip()
                    if isinstance(att.get("asr_refer_text"), str) else ""
                )
                voice_wav_url = (
                    str(att.get("voice_wav_url", "")).strip()
                    if isinstance(att.get("voice_wav_url"), str) else ""
                )

                transcript = await self._stt_voice_attachment(
                    url, ct, filename,
                    asr_refer_text=asr_refer or None,
                    voice_wav_url=voice_wav_url or None,
                )
                if transcript:
                    voice_transcripts.append(f"[Voice] {transcript}")
                    logger.info("[QQ] Voice transcript: %s", transcript)
                else:
                    logger.warning("[QQ] Voice STT failed for %s", url[:60])
                    voice_transcripts.append("[Voice] [语音识别失败]")
            elif ct.startswith("image/"):
                # 图片：下载并缓存到本地
                try:
                    cached_path = await self._download_and_cache(url, ct)
                    if cached_path and os.path.isfile(cached_path):
                        image_urls.append(cached_path)
                        image_media_types.append(ct or "image/jpeg")
                    elif cached_path:
                        logger.warning("[QQ] Cached image path does not exist: %s", cached_path)
                except Exception as exc:
                    logger.debug("[QQ] Failed to cache image: %s", exc)
            else:
                # 其他附件（视频、文件等）：记录为文本描述
                try:
                    cached_path = await self._download_and_cache(url, ct)
                    if cached_path:
                        other_attachments.append(f"[Attachment: {filename or ct}]")
                except Exception as exc:
                    logger.debug("[QQ] Failed to cache attachment: %s", exc)

        attachment_info = "\n".join(other_attachments) if other_attachments else ""
        return {
            "image_urls": image_urls,
            "image_media_types": image_media_types,
            "voice_transcripts": voice_transcripts,
            "attachment_info": attachment_info,
        }

    async def _download_and_cache(self, url: str, content_type: str) -> Optional[str]:
        """下载 URL 并缓存到本地。"""
        from tools.url_safety import is_safe_url
        if not is_safe_url(url):
            raise ValueError(f"Blocked unsafe URL: {url[:80]}")

        if not self._http_client:
            return None

        try:
            resp = await self._http_client.get(
                url, timeout=30.0, headers=self._qq_media_headers(),
            )
            resp.raise_for_status()
            data = resp.content
        except Exception as exc:
            logger.debug("[%s] Download failed for %s: %s", self.name, url[:80], exc)
            return None

        if content_type.startswith("image/"):
            ext = mimetypes.guess_extension(content_type) or ".jpg"
            return cache_image_from_bytes(data, ext)
        elif content_type == "voice" or content_type.startswith("audio/"):
            # QQ 语音消息通常是 .amr 或 .silk 格式
            # 使用 ffmpeg 转换为 .wav 以便 STT 引擎处理
            return await self._convert_audio_to_wav(data, url)
        else:
            filename = Path(urlparse(url).path).name or "qq_attachment"
            return cache_document_from_bytes(data, filename)

    @staticmethod
    def _is_voice_content_type(content_type: str, filename: str) -> bool:
        """检查附件是否为语音/音频消息。"""
        ct = content_type.strip().lower()
        fn = filename.strip().lower()
        if ct == "voice" or ct.startswith("audio/"):
            return True
        _VOICE_EXTENSIONS = (".silk", ".amr", ".mp3", ".wav", ".ogg", ".m4a", ".aac", ".speex", ".flac")
        if any(fn.endswith(ext) for ext in _VOICE_EXTENSIONS):
            return True
        return False

    def _qq_media_headers(self) -> Dict[str, str]:
        """返回用于 QQ 多媒体 CDN 下载的 Authorization 请求头。

        QQ 的多媒体 URL（multimedia.nt.qq.com.cn）需要在 Authorization 头中
        携带机器人的访问令牌，否则下载会返回非 200 状态码。
        """
        if self._access_token:
            return {"Authorization": f"QQBot {self._access_token}"}
        return {}

    async def _stt_voice_attachment(
        self,
        url: str,
        content_type: str,
        filename: str,
        *,
        asr_refer_text: Optional[str] = None,
        voice_wav_url: Optional[str] = None,
    ) -> Optional[str]:
        """下载语音附件，转换为 wav 并转录。

        优先级：
        1. QQ 内置的 ``asr_refer_text``（腾讯 ASR——免费，无需 API 调用）
        2. 对 ``voice_wav_url`` 进行自托管 STT（QQ 预转换的 WAV，无需 SILK 解码）
        3. 对原始附件 URL 进行自托管 STT（需要 SILK→WAV 转换）

        返回转录文本，失败时返回 None。
        """
        # 1. 如有 QQ 内置 ASR 文本则直接使用
        if asr_refer_text:
            logger.info("[QQ] STT: using QQ asr_refer_text: %r", asr_refer_text[:100])
            return asr_refer_text

        # 确定下载 URL（优先使用 voice_wav_url——已经是 WAV 格式）
        download_url = url
        is_pre_wav = False
        if voice_wav_url:
            if voice_wav_url.startswith("//"):
                voice_wav_url = f"https:{voice_wav_url}"
            download_url = voice_wav_url
            is_pre_wav = True
            logger.info("[QQ] STT: using voice_wav_url (pre-converted WAV)")

        try:
            # 2. 下载音频（QQ CDN 需要 Authorization 请求头）
            if not self._http_client:
                logger.warning("[QQ] STT: no HTTP client")
                return None

            download_headers = self._qq_media_headers()
            logger.info("[QQ] STT: downloading voice from %s (pre_wav=%s, headers=%s)",
                        download_url[:80], is_pre_wav, bool(download_headers))
            resp = await self._http_client.get(
                download_url, timeout=30.0, headers=download_headers, follow_redirects=True,
            )
            resp.raise_for_status()
            audio_data = resp.content
            logger.info("[QQ] STT: downloaded %d bytes, content_type=%s",
                        len(audio_data), resp.headers.get("content-type", "unknown"))

            if len(audio_data) < 10:
                logger.warning("[QQ] STT: downloaded data too small (%d bytes), skipping", len(audio_data))
                return None

            # 3. 转换为 wav（如果已经是预转换的 WAV 则跳过）
            if is_pre_wav:
                import tempfile
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                    tmp.write(audio_data)
                    wav_path = tmp.name
                logger.info("[QQ] STT: using pre-converted WAV directly (%d bytes)", len(audio_data))
            else:
                logger.info("[QQ] STT: converting to wav, filename=%r", filename)
                wav_path = await self._convert_audio_to_wav_file(audio_data, filename)
                if not wav_path or not Path(wav_path).exists():
                    logger.warning("[QQ] STT: ffmpeg conversion produced no output")
                    return None

            # 4. 调用 STT API
            logger.info("[QQ] STT: calling ASR on %s", wav_path)
            transcript = await self._call_stt(wav_path)

            # 5. 清理临时文件
            try:
                os.unlink(wav_path)
            except OSError:
                pass

            if transcript:
                logger.info("[QQ] STT success: %r", transcript[:100])
            else:
                logger.warning("[QQ] STT: ASR returned empty transcript")
            return transcript
        except (httpx.HTTPStatusError, httpx.TransportError, IOError) as exc:
            logger.warning("[QQ] STT failed for voice attachment: %s: %s", type(exc).__name__, exc)
            return None

    async def _convert_audio_to_wav_file(self, audio_data: bytes, filename: str) -> Optional[str]:
        """使用 pilk（SILK）或 ffmpeg 将音频字节转换为临时 .wav 文件。

        QQ 语音消息通常是 SILK 格式，ffmpeg 无法直接解码。
        策略：始终先尝试 pilk，pilk 失败再回退到 ffmpeg。

        返回 wav 文件路径，失败时返回 None。
        """
        import tempfile

        ext = Path(filename).suffix.lower() if Path(filename).suffix else self._guess_ext_from_data(audio_data)
        logger.info("[QQ] STT: audio_data size=%d, ext=%r, first_20_bytes=%r",
                    len(audio_data), ext, audio_data[:20])

        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp_src:
            tmp_src.write(audio_data)
            src_path = tmp_src.name

        wav_path = src_path.rsplit(".", 1)[0] + ".wav"

        # 先尝试 pilk（可处理 SILK 及其他多种格式）
        result = await self._convert_silk_to_wav(src_path, wav_path)

        # 如果 pilk 失败，尝试 ffmpeg
        if not result:
            result = await self._convert_ffmpeg_to_wav(src_path, wav_path)

        # 如果 ffmpeg 也失败，尝试将原始 PCM 写为 WAV（最后手段）
        if not result:
            result = await self._convert_raw_to_wav(audio_data, wav_path)

        # 清理源文件
        try:
            os.unlink(src_path)
        except OSError:
            pass

        return result

    @staticmethod
    def _guess_ext_from_data(data: bytes) -> str:
        """根据魔数字节猜测文件扩展名。"""
        if data[:9] == b"#!SILK_V3" or data[:5] == b"#!SILK":
            return ".silk"
        if data[:2] == b"\x02!":
            return ".silk"
        if data[:4] == b"RIFF":
            return ".wav"
        if data[:4] == b"fLaC":
            return ".flac"
        if data[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
            return ".mp3"
        if data[:4] == b"\x30\x26\xb2\x75" or data[:4] == b"\x4f\x67\x67\x53":
            return ".ogg"
        if data[:4] == b"\x00\x00\x00\x20" or data[:4] == b"\x00\x00\x00\x1c":
            return ".amr"
        # 未知格式默认使用 .amr（QQ 最常见的语音格式）
        return ".amr"

    @staticmethod
    def _looks_like_silk(data: bytes) -> bool:
        """检查字节数据是否看起来像 SILK 音频文件。"""
        return data[:4] == b"#!SILK" or data[:2] == b"\x02!" or data[:9] == b"#!SILK_V3"

    @staticmethod
    async def _convert_silk_to_wav(src_path: str, wav_path: str) -> Optional[str]:
        """使用 pilk 库将音频文件转换为 WAV。

        先按原样尝试转换，如果扩展名不同则尝试重命名为 .silk 再转换。
        pilk 可以处理各种头部（或无头部）的 SILK 文件。
        """
        try:
            import pilk
        except ImportError:
            logger.warning("[QQ] pilk not installed — cannot decode SILK audio. Run: pip install pilk")
            return None

        # 按原样尝试转换
        try:
            pilk.silk_to_wav(src_path, wav_path, rate=16000)
            if Path(wav_path).exists() and Path(wav_path).stat().st_size > 44:
                logger.info("[QQ] pilk converted %s to wav (%d bytes)",
                            Path(src_path).name, Path(wav_path).stat().st_size)
                return wav_path
        except Exception as exc:
            logger.debug("[QQ] pilk direct conversion failed: %s", exc)

        # 尝试重命名为 .silk 再转换（pilk 会检查扩展名）
        silk_path = src_path.rsplit(".", 1)[0] + ".silk"
        try:
            import shutil
            shutil.copy2(src_path, silk_path)
            pilk.silk_to_wav(silk_path, wav_path, rate=16000)
            if Path(wav_path).exists() and Path(wav_path).stat().st_size > 44:
                logger.info("[QQ] pilk converted %s (as .silk) to wav (%d bytes)",
                            Path(src_path).name, Path(wav_path).stat().st_size)
                return wav_path
        except Exception as exc:
            logger.debug("[QQ] pilk .silk conversion failed: %s", exc)
        finally:
            try:
                os.unlink(silk_path)
            except OSError:
                pass

        return None

    @staticmethod
    async def _convert_raw_to_wav(audio_data: bytes, wav_path: str) -> Optional[str]:
        """最后手段：尝试将音频数据按原始 PCM 16位 单声道 16kHz 写为 WAV。

        如果数据不是原始 PCM 则会产生噪音，但至少 ASR 引擎不会崩溃——
        只是返回空结果。
        """
        try:
            import wave
            with wave.open(wav_path, "w") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(16000)
                wf.writeframes(audio_data)
            return wav_path
        except Exception as exc:
            logger.debug("[QQ] raw PCM fallback failed: %s", exc)
            return None

    @staticmethod
    async def _convert_ffmpeg_to_wav(src_path: str, wav_path: str) -> Optional[str]:
        """使用 ffmpeg 将音频文件转换为 WAV。"""
        try:
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-y", "-i", src_path, "-ar", "16000", "-ac", "1", wav_path,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.wait(), timeout=30)
            if proc.returncode != 0:
                stderr = await proc.stderr.read() if proc.stderr else b""
                logger.warning("[QQ] ffmpeg failed for %s: %s",
                            Path(src_path).name, stderr[:200].decode(errors="replace"))
                return None
        except (asyncio.TimeoutError, FileNotFoundError) as exc:
            logger.warning("[QQ] ffmpeg conversion error: %s", exc)
            return None

        if not Path(wav_path).exists() or Path(wav_path).stat().st_size <= 44:
            logger.warning("[QQ] ffmpeg produced no/small output for %s", Path(src_path).name)
            return None
        logger.info("[QQ] ffmpeg converted %s to wav (%d bytes)",
                    Path(src_path).name, Path(wav_path).stat().st_size)
        return wav_path

    def _resolve_stt_config(self) -> Optional[Dict[str, str]]:
        """解析 STT 后端配置（来自 config/环境变量）。

        优先级：
        1. 插件特定配置：config.yaml 中 ``channels.qqbot.stt`` → ``self.config.extra["stt"]``
        2. QQ 特定环境变量：``QQ_STT_API_KEY`` / ``QQ_STT_BASE_URL`` / ``QQ_STT_MODEL``
        3. 返回 None 表示未配置（STT 将被跳过，QQ 内置 ASR 仍然可用）
        """
        extra = self.config.extra or {}

        # 1. 插件特定 STT 配置（与 OpenClaw 的 channels.qqbot.stt 对应）
        stt_cfg = extra.get("stt")
        if isinstance(stt_cfg, dict) and stt_cfg.get("enabled") is not False:
            base_url = stt_cfg.get("baseUrl") or stt_cfg.get("base_url", "")
            api_key = stt_cfg.get("apiKey") or stt_cfg.get("api_key", "")
            model = stt_cfg.get("model", "")
            if base_url and api_key:
                return {
                    "base_url": base_url.rstrip("/"),
                    "api_key": api_key,
                    "model": model or "whisper-1",
                }
            # 仅提供商配置：只有模型名，使用默认提供商
            if api_key:
                provider = stt_cfg.get("provider", "zai")
                # 将提供商映射到 base URL
                _PROVIDER_BASE_URLS = {
                    "zai": "https://open.bigmodel.cn/api/coding/paas/v4",
                    "openai": "https://api.openai.com/v1",
                    "glm": "https://open.bigmodel.cn/api/coding/paas/v4",
                }
                base_url = _PROVIDER_BASE_URLS.get(provider, "")
                if base_url:
                    return {
                        "base_url": base_url,
                        "api_key": api_key,
                        "model": model or ("glm-asr" if provider in ("zai", "glm") else "whisper-1"),
                    }

        # 2. QQ 特定环境变量（由 `hermes setup gateway` / `hermes gateway` 设置）
        qq_stt_key = os.getenv("QQ_STT_API_KEY", "")
        if qq_stt_key:
            base_url = os.getenv(
                "QQ_STT_BASE_URL",
                "https://open.bigmodel.cn/api/coding/paas/v4",
            )
            model = os.getenv("QQ_STT_MODEL", "glm-asr")
            return {
                "base_url": base_url.rstrip("/"),
                "api_key": qq_stt_key,
                "model": model,
            }

        return None

    async def _call_stt(self, wav_path: str) -> Optional[str]:
        """调用 OpenAI 兼容的 STT API 转录 wav 文件。

        使用 ``channels.qqbot.stt`` 配置中设定的提供商，
        如未配置则回退到 QQ 内置的 ``asr_refer_text``。
        未配置或调用失败时返回 None。
        """
        stt_cfg = self._resolve_stt_config()
        if not stt_cfg:
            logger.warning("[QQ] STT not configured (no stt config or QQ_STT_API_KEY)")
            return None

        base_url = stt_cfg["base_url"]
        api_key = stt_cfg["api_key"]
        model = stt_cfg["model"]

        try:
            with open(wav_path, "rb") as f:
                resp = await self._http_client.post(
                    f"{base_url}/audio/transcriptions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    files={"file": (Path(wav_path).name, f, "audio/wav")},
                    data={"model": model},
                    timeout=30.0,
                )
            resp.raise_for_status()
            result = resp.json()
            # 智谱/GLM 格式：{"choices": [{"message": {"content": "转录文本"}}]}
            choices = result.get("choices", [])
            if choices:
                content = choices[0].get("message", {}).get("content", "")
                if content.strip():
                    return content.strip()
            # OpenAI/Whisper 格式：{"text": "转录文本"}
            text = result.get("text", "")
            if text.strip():
                return text.strip()
            return None
        except (httpx.HTTPStatusError, IOError) as exc:
            logger.warning("[QQ] STT API call failed (model=%s, base=%s): %s",
                           model, base_url[:50], exc)
            return None

    async def _convert_audio_to_wav(self, audio_data: bytes, source_url: str) -> Optional[str]:
        """使用 pilk（SILK）或 ffmpeg 将音频字节转换为 .wav 并缓存结果。"""
        import tempfile

        # 根据魔数字节或 URL 判断源格式
        ext = Path(urlparse(source_url).path).suffix.lower() if urlparse(source_url).path else ""
        if not ext or ext not in (".silk", ".amr", ".mp3", ".wav", ".ogg", ".m4a", ".aac", ".flac"):
            ext = self._guess_ext_from_data(audio_data)

        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp_src:
            tmp_src.write(audio_data)
            src_path = tmp_src.name

        wav_path = src_path.rsplit(".", 1)[0] + ".wav"
        try:
            is_silk = ext == ".silk" or self._looks_like_silk(audio_data)
            if is_silk:
                result = await self._convert_silk_to_wav(src_path, wav_path)
            else:
                result = await self._convert_ffmpeg_to_wav(src_path, wav_path)

            if not result:
                logger.warning("[%s] audio conversion failed for %s (format=%s)",
                            self.name, source_url[:60], ext)
                return cache_document_from_bytes(audio_data, f"qq_voice{ext}")
        except Exception:
            return cache_document_from_bytes(audio_data, f"qq_voice{ext}")
        finally:
            try:
                os.unlink(src_path)
            except OSError:
                pass

        # 验证输出并缓存
        try:
            wav_data = Path(wav_path).read_bytes()
            os.unlink(wav_path)
            return cache_document_from_bytes(wav_data, "qq_voice.wav")
        except Exception as exc:
            logger.debug("[%s] Failed to read converted wav: %s", self.name, exc)
            return None

    # ------------------------------------------------------------------
    # 出站消息——REST API
    # ------------------------------------------------------------------

    async def _api_request(
        self,
        method: str,
        path: str,
        body: Optional[Dict[str, Any]] = None,
        timeout: float = DEFAULT_API_TIMEOUT,
    ) -> Dict[str, Any]:
        """向 QQ Bot API 发送认证后的 REST API 请求。"""
        if not self._http_client:
            raise RuntimeError("HTTP client not initialized — not connected?")

        token = await self._ensure_token()
        headers = {
            "Authorization": f"QQBot {token}",
            "Content-Type": "application/json",
        }

        try:
            resp = await self._http_client.request(
                method,
                f"{API_BASE}{path}",
                headers=headers,
                json=body,
                timeout=timeout,
            )
            data = resp.json()
            if resp.status_code >= 400:
                raise RuntimeError(
                    f"QQ Bot API error [{resp.status_code}] {path}: "
                    f"{data.get('message', data)}"
                )
            return data
        except httpx.TimeoutException as exc:
            raise RuntimeError(f"QQ Bot API timeout [{path}]: {exc}") from exc

    async def _upload_media(
        self,
        target_type: str,
        target_id: str,
        file_type: int,
        url: Optional[str] = None,
        file_data: Optional[str] = None,
        srv_send_msg: bool = False,
        file_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """上传媒体文件并返回 file_info。"""
        path = f"/v2/users/{target_id}/files" if target_type == "c2c" else f"/v2/groups/{target_id}/files"

        body: Dict[str, Any] = {
            "file_type": file_type,
            "srv_send_msg": srv_send_msg,
        }
        if url:
            body["url"] = url
        elif file_data:
            body["file_data"] = file_data
        if file_type == MEDIA_TYPE_FILE and file_name:
            body["file_name"] = file_name

        # 重试临时上传失败
        last_exc = None
        for attempt in range(3):
            try:
                return await self._api_request("POST", path, body, timeout=FILE_UPLOAD_TIMEOUT)
            except RuntimeError as exc:
                last_exc = exc
                err_msg = str(exc)
                if any(kw in err_msg for kw in ("400", "401", "Invalid", "timeout", "Timeout")):
                    raise
                if attempt < 2:
                    await asyncio.sleep(1.5 * (attempt + 1))

        raise last_exc  # type: ignore[misc]

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """向 QQ 用户或群发送文本或 Markdown 消息。

        应用 format_message() 格式化，通过 truncate_message() 拆分长消息，
        并在临时失败时使用指数退避重试。
        """
        del metadata

        if not self.is_connected:
            return SendResult(success=False, error="Not connected")

        if not content or not content.strip():
            return SendResult(success=True)

        formatted = self.format_message(content)
        chunks = self.truncate_message(formatted, self.MAX_MESSAGE_LENGTH)

        last_result = SendResult(success=False, error="No chunks")
        for chunk in chunks:
            last_result = await self._send_chunk(chat_id, chunk, reply_to)
            if not last_result.success:
                return last_result
            # 只对第一个分块使用 reply_to
            reply_to = None
        return last_result

    async def _send_chunk(
        self, chat_id: str, content: str, reply_to: Optional[str] = None,
    ) -> SendResult:
        """发送单个消息分块，支持重试和指数退避。"""
        last_exc: Optional[Exception] = None
        chat_type = self._guess_chat_type(chat_id)

        for attempt in range(3):
            try:
                if chat_type == "c2c":
                    return await self._send_c2c_text(chat_id, content, reply_to)
                elif chat_type == "group":
                    return await self._send_group_text(chat_id, content, reply_to)
                elif chat_type == "guild":
                    return await self._send_guild_text(chat_id, content, reply_to)
                else:
                    return SendResult(success=False, error=f"Unknown chat type for {chat_id}")
            except Exception as exc:
                last_exc = exc
                err = str(exc).lower()
                # 永久性错误——不重试
                if any(k in err for k in ("invalid", "forbidden", "not found", "bad request")):
                    break
                # 临时性错误——退避后重试
                if attempt < 2:
                    delay = 1.0 * (2 ** attempt)
                    logger.warning("[%s] send retry %d/3 after %.1fs: %s",
                                   self.name, attempt + 1, delay, exc)
                    await asyncio.sleep(delay)

        error_msg = str(last_exc) if last_exc else "Unknown error"
        logger.error("[%s] Send failed: %s", self.name, error_msg)
        retryable = not any(k in error_msg.lower()
                            for k in ("invalid", "forbidden", "not found"))
        return SendResult(success=False, error=error_msg, retryable=retryable)

    async def _send_c2c_text(
        self, openid: str, content: str, reply_to: Optional[str] = None
    ) -> SendResult:
        """通过 REST API 向 C2C 用户发送文本。"""
        msg_seq = self._next_msg_seq(reply_to or openid)
        body = self._build_text_body(content, reply_to)
        if reply_to:
            body["msg_id"] = reply_to

        data = await self._api_request("POST", f"/v2/users/{openid}/messages", body)
        msg_id = str(data.get("id", uuid.uuid4().hex[:12]))
        return SendResult(success=True, message_id=msg_id, raw_response=data)

    async def _send_group_text(
        self, group_openid: str, content: str, reply_to: Optional[str] = None
    ) -> SendResult:
        """通过 REST API 向群组发送文本。"""
        msg_seq = self._next_msg_seq(reply_to or group_openid)
        body = self._build_text_body(content, reply_to)
        if reply_to:
            body["msg_id"] = reply_to

        data = await self._api_request("POST", f"/v2/groups/{group_openid}/messages", body)
        msg_id = str(data.get("id", uuid.uuid4().hex[:12]))
        return SendResult(success=True, message_id=msg_id, raw_response=data)

    async def _send_guild_text(
        self, channel_id: str, content: str, reply_to: Optional[str] = None
    ) -> SendResult:
        """通过 REST API 向频道发送文本。"""
        body: Dict[str, Any] = {"content": content[:self.MAX_MESSAGE_LENGTH]}
        if reply_to:
            body["msg_id"] = reply_to

        data = await self._api_request("POST", f"/channels/{channel_id}/messages", body)
        msg_id = str(data.get("id", uuid.uuid4().hex[:12]))
        return SendResult(success=True, message_id=msg_id, raw_response=data)

    def _build_text_body(self, content: str, reply_to: Optional[str] = None) -> Dict[str, Any]:
        """构建 C2C/群组文本发送的消息体。"""
        msg_seq = self._next_msg_seq(reply_to or "default")

        if self._markdown_support:
            body: Dict[str, Any] = {
                "markdown": {"content": content[:self.MAX_MESSAGE_LENGTH]},
                "msg_type": MSG_TYPE_MARKDOWN,
                "msg_seq": msg_seq,
            }
        else:
            body = {
                "content": content[:self.MAX_MESSAGE_LENGTH],
                "msg_type": MSG_TYPE_TEXT,
                "msg_seq": msg_seq,
            }

        if reply_to:
            # 非 Markdown 模式下，添加消息引用
            if not self._markdown_support:
                body["message_reference"] = {"message_id": reply_to}

        return body

    # ------------------------------------------------------------------
    # 原生媒体发送
    # ------------------------------------------------------------------

    async def send_image(
        self,
        chat_id: str,
        image_url: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """通过 QQ Bot API 上传并原生发送图片。"""
        del metadata

        result = await self._send_media(chat_id, image_url, MEDIA_TYPE_IMAGE, "image", caption, reply_to)
        if result.success or not self._is_url(image_url):
            return result

        # 回退到文本 URL
        logger.warning("[%s] Image send failed, falling back to text: %s", self.name, result.error)
        fallback = f"{caption}\n{image_url}" if caption else image_url
        return await self.send(chat_id=chat_id, content=fallback, reply_to=reply_to)

    async def send_image_file(
        self,
        chat_id: str,
        image_path: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        **kwargs,
    ) -> SendResult:
        """原生发送本地图片文件。"""
        del kwargs
        return await self._send_media(chat_id, image_path, MEDIA_TYPE_IMAGE, "image", caption, reply_to)

    async def send_voice(
        self,
        chat_id: str,
        audio_path: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        **kwargs,
    ) -> SendResult:
        """原生发送语音消息。"""
        del kwargs
        return await self._send_media(chat_id, audio_path, MEDIA_TYPE_VOICE, "voice", caption, reply_to)

    async def send_video(
        self,
        chat_id: str,
        video_path: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        **kwargs,
    ) -> SendResult:
        """原生发送视频。"""
        del kwargs
        return await self._send_media(chat_id, video_path, MEDIA_TYPE_VIDEO, "video", caption, reply_to)

    async def send_document(
        self,
        chat_id: str,
        file_path: str,
        caption: Optional[str] = None,
        file_name: Optional[str] = None,
        reply_to: Optional[str] = None,
        **kwargs,
    ) -> SendResult:
        """原生发送文件/文档。"""
        del kwargs
        return await self._send_media(chat_id, file_path, MEDIA_TYPE_FILE, "file", caption, reply_to,
                                       file_name=file_name)

    async def _send_media(
        self,
        chat_id: str,
        media_source: str,
        file_type: int,
        kind: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        file_name: Optional[str] = None,
    ) -> SendResult:
        """上传媒体并作为原生消息发送。"""
        if not self.is_connected:
            return SendResult(success=False, error="Not connected")

        try:
            # 解析媒体来源
            data, content_type, resolved_name = await self._load_media(media_source, file_name)

            # 路由
            chat_type = self._guess_chat_type(chat_id)
            target_path = f"/v2/users/{chat_id}/files" if chat_type == "c2c" else f"/v2/groups/{chat_id}/files"

            if chat_type == "guild":
                # 频道不支持以这种方式原生上传媒体
                # 回退到 URL 形式发送
                return SendResult(success=False, error="Guild media send not supported via this path")

            # 上传
            upload = await self._upload_media(
                chat_type, chat_id, file_type,
                file_data=data if not self._is_url(media_source) else None,
                url=media_source if self._is_url(media_source) else None,
                srv_send_msg=False,
                file_name=resolved_name if file_type == MEDIA_TYPE_FILE else None,
            )

            file_info = upload.get("file_info")
            if not file_info:
                return SendResult(success=False, error=f"Upload returned no file_info: {upload}")

            # 发送媒体消息
            msg_seq = self._next_msg_seq(chat_id)
            body: Dict[str, Any] = {
                "msg_type": MSG_TYPE_MEDIA,
                "media": {"file_info": file_info},
                "msg_seq": msg_seq,
            }
            if caption:
                body["content"] = caption[:self.MAX_MESSAGE_LENGTH]
            if reply_to:
                body["msg_id"] = reply_to

            send_data = await self._api_request(
                "POST",
                f"/v2/users/{chat_id}/messages" if chat_type == "c2c" else f"/v2/groups/{chat_id}/messages",
                body,
            )
            return SendResult(
                success=True,
                message_id=str(send_data.get("id", uuid.uuid4().hex[:12])),
                raw_response=send_data,
            )
        except Exception as exc:
            logger.error("[%s] Media send failed: %s", self.name, exc)
            return SendResult(success=False, error=str(exc))

    async def _load_media(
        self, source: str, file_name: Optional[str] = None
    ) -> Tuple[str, str, str]:
        """从 URL 或本地路径加载媒体。返回 (base64_or_url, content_type, filename)。"""
        source = str(source).strip()
        if not source:
            raise ValueError("Media source is required")

        parsed = urlparse(source)
        if parsed.scheme in ("http", "https"):
            # 对于 URL，直接传给上传 API
            content_type = mimetypes.guess_type(source)[0] or "application/octet-stream"
            resolved_name = file_name or Path(parsed.path).name or "media"
            return source, content_type, resolved_name

        # 本地文件——编码为原始 base64 用于 QQ Bot API 的 file_data 字段
        # QQ API 期望纯 base64，不是 data URI
        local_path = Path(source).expanduser()
        if not local_path.is_absolute():
            local_path = (Path.cwd() / local_path).resolve()

        if not local_path.exists() or not local_path.is_file():
            # 防止 LLM 有时输出的占位符路径（如 "<path>"）
            if source.startswith("<") or len(source) < 3:
                raise ValueError(
                    f"Invalid media source (looks like a placeholder): {source!r}"
                )
            raise FileNotFoundError(f"Media file not found: {local_path}")

        raw = local_path.read_bytes()
        resolved_name = file_name or local_path.name
        content_type = mimetypes.guess_type(str(local_path))[0] or "application/octet-stream"
        b64 = base64.b64encode(raw).decode("ascii")
        return b64, content_type, resolved_name

    # ------------------------------------------------------------------
    # 输入状态指示器
    # ------------------------------------------------------------------

    async def send_typing(self, chat_id: str, metadata=None) -> None:
        """向 C2C 用户发送输入通知（仅 C2C 支持）。"""
        del metadata

        if not self.is_connected:
            return

        # 仅 C2C 支持输入通知
        chat_type = self._guess_chat_type(chat_id)
        if chat_type != "c2c":
            return

        try:
            msg_seq = self._next_msg_seq(chat_id)
            body = {
                "msg_type": MSG_TYPE_INPUT_NOTIFY,
                "input_notify": {"input_type": 1, "input_second": 60},
                "msg_seq": msg_seq,
            }
            await self._api_request("POST", f"/v2/users/{chat_id}/messages", body)
        except Exception as exc:
            logger.debug("[%s] send_typing failed: %s", self.name, exc)

    # ------------------------------------------------------------------
    # 消息格式化
    # ------------------------------------------------------------------

    def format_message(self, content: str) -> str:
        """为 QQ 格式化消息。

        启用 markdown_support 时，内容原样发送（QQ 会渲染 Markdown）。
        禁用时，通过共享辅助函数去除 Markdown（与 BlueBubbles/SMS 相同）。
        """
        if self._markdown_support:
            return content
        return strip_markdown(content)

    # ------------------------------------------------------------------
    # 聊天信息
    # ------------------------------------------------------------------

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        """基于聊天类型启发式返回聊天信息。"""
        chat_type = self._guess_chat_type(chat_id)
        return {
            "name": chat_id,
            "type": "group" if chat_type in ("group", "guild") else "dm",
        }

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    @staticmethod
    def _is_url(source: str) -> bool:
        return urlparse(str(source)).scheme in ("http", "https")

    def _guess_chat_type(self, chat_id: str) -> str:
        """根据存储的入站元数据判断聊天类型，默认回退到 'c2c'。"""
        if chat_id in self._chat_type_map:
            return self._chat_type_map[chat_id]
        return "c2c"

    @staticmethod
    def _strip_at_mention(content: str) -> str:
        """去除群消息内容中的 @bot 前缀。"""
        # QQ 群 @消息可能以机器人的 QQ/ID 作为前缀
        import re
        stripped = re.sub(r'^@\S+\s*', '', content.strip())
        return stripped

    def _is_dm_allowed(self, user_id: str) -> bool:
        if self._dm_policy == "disabled":
            return False
        if self._dm_policy == "allowlist":
            return self._entry_matches(self._allow_from, user_id)
        return True

    def _is_group_allowed(self, group_id: str, user_id: str) -> bool:
        if self._group_policy == "disabled":
            return False
        if self._group_policy == "allowlist":
            return self._entry_matches(self._group_allow_from, group_id)
        return True

    @staticmethod
    def _entry_matches(entries: List[str], target: str) -> bool:
        normalized_target = str(target).strip().lower()
        for entry in entries:
            normalized = str(entry).strip().lower()
            if normalized == "*" or normalized == normalized_target:
                return True
        return False

    def _parse_qq_timestamp(self, raw: str) -> datetime:
        """解析 QQ API 时间戳（ISO 8601 字符串或整数毫秒）。

        QQ API 从整数毫秒改为了 ISO 8601 字符串格式，
        此方法能优雅地处理两种格式。
        """
        if not raw:
            return datetime.now(tz=timezone.utc)
        try:
            return datetime.fromisoformat(raw)
        except (ValueError, TypeError):
            pass
        try:
            return datetime.fromtimestamp(int(raw) / 1000, tz=timezone.utc)
        except (ValueError, TypeError):
            pass
        return datetime.now(tz=timezone.utc)

    def _is_duplicate(self, msg_id: str) -> bool:
        now = time.time()
        if len(self._seen_messages) > DEDUP_MAX_SIZE:
            cutoff = now - DEDUP_WINDOW_SECONDS
            self._seen_messages = {
                key: ts for key, ts in self._seen_messages.items() if ts > cutoff
            }
        if msg_id in self._seen_messages:
            return True
        self._seen_messages[msg_id] = now
        return False
