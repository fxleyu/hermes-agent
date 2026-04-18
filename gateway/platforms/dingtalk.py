"""
钉钉平台适配器，使用 Stream Mode（流模式）。

使用 dingtalk-stream SDK 实现实时消息接收，无需配置 Webhook。
回复通过钉钉的会话 Webhook 发送（Markdown 格式）。

依赖项：
    pip install dingtalk-stream httpx
    需要设置 DINGTALK_CLIENT_ID 和 DINGTALK_CLIENT_SECRET 环境变量

在 config.yaml 中的配置方式：
    platforms:
      dingtalk:
        enabled: true
        extra:
          client_id: "your-app-key"      # 或设置 DINGTALK_CLIENT_ID 环境变量
          client_secret: "your-secret"   # 或设置 DINGTALK_CLIENT_SECRET 环境变量
"""

import asyncio
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

try:
    import dingtalk_stream
    from dingtalk_stream import ChatbotHandler, ChatbotMessage
    DINGTALK_STREAM_AVAILABLE = True
except ImportError:
    DINGTALK_STREAM_AVAILABLE = False
    dingtalk_stream = None  # type: ignore[assignment]

try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False
    httpx = None  # type: ignore[assignment]

from gateway.config import Platform, PlatformConfig
from gateway.platforms.helpers import MessageDeduplicator
from gateway.platforms.base import (
    BasePlatformAdapter,
    MessageEvent,
    MessageType,
    SendResult,
)

logger = logging.getLogger(__name__)

MAX_MESSAGE_LENGTH = 20000
RECONNECT_BACKOFF = [2, 5, 10, 30, 60]  # 重连退避时间表（秒）
_SESSION_WEBHOOKS_MAX = 500  # 会话 Webhook 缓存最大数量
# 用于验证钉钉 Webhook URL 来源的正则，防止 SSRF 攻击
_DINGTALK_WEBHOOK_RE = re.compile(r'^https://(?:api|oapi)\.dingtalk\.com/')


def check_dingtalk_requirements() -> bool:
    """检查钉钉依赖项是否可用且已正确配置。"""
    if not DINGTALK_STREAM_AVAILABLE or not HTTPX_AVAILABLE:
        return False
    if not os.getenv("DINGTALK_CLIENT_ID") or not os.getenv("DINGTALK_CLIENT_SECRET"):
        return False
    return True


class DingTalkAdapter(BasePlatformAdapter):
    """钉钉聊天机器人适配器，使用 Stream Mode（流模式）。

    dingtalk-stream SDK 会维护一个持久的 WebSocket 连接。
    入站消息通过 ChatbotHandler 回调到达。回复通过
    入站消息携带的 session_webhook URL 使用 httpx 发送。
    """

    MAX_MESSAGE_LENGTH = MAX_MESSAGE_LENGTH

    def __init__(self, config: PlatformConfig):
        super().__init__(config, Platform.DINGTALK)

        extra = config.extra or {}
        self._client_id: str = extra.get("client_id") or os.getenv("DINGTALK_CLIENT_ID", "")
        self._client_secret: str = extra.get("client_secret") or os.getenv("DINGTALK_CLIENT_SECRET", "")

        self._stream_client: Any = None
        self._stream_task: Optional[asyncio.Task] = None
        self._http_client: Optional["httpx.AsyncClient"] = None

        # 消息去重器
        self._dedup = MessageDeduplicator(max_size=1000)
        # 会话 ID -> session_webhook 的映射，用于回复路由
        self._session_webhooks: Dict[str, str] = {}

    # -- 连接生命周期 -----------------------------------------------

    async def connect(self) -> bool:
        """通过 Stream Mode 连接到钉钉。"""
        if not DINGTALK_STREAM_AVAILABLE:
            logger.warning("[%s] dingtalk-stream 未安装。请运行：pip install dingtalk-stream", self.name)
            return False
        if not HTTPX_AVAILABLE:
            logger.warning("[%s] httpx 未安装。请运行：pip install httpx", self.name)
            return False
        if not self._client_id or not self._client_secret:
            logger.warning("[%s] 需要设置 DINGTALK_CLIENT_ID 和 DINGTALK_CLIENT_SECRET", self.name)
            return False

        try:
            self._http_client = httpx.AsyncClient(timeout=30.0)

            # 使用应用凭据创建钉钉流式客户端
            credential = dingtalk_stream.Credential(self._client_id, self._client_secret)
            self._stream_client = dingtalk_stream.DingTalkStreamClient(credential)

            # 捕获当前事件循环，用于跨线程消息分发
            loop = asyncio.get_running_loop()
            handler = _IncomingHandler(self, loop)
            self._stream_client.register_callback_handler(
                dingtalk_stream.ChatbotMessage.TOPIC, handler
            )

            # 在后台任务中运行流式客户端
            self._stream_task = asyncio.create_task(self._run_stream())
            self._mark_connected()
            logger.info("[%s] 已通过 Stream Mode 连接", self.name)
            return True
        except Exception as e:
            logger.error("[%s] 连接失败：%s", self.name, e)
            return False

    async def _run_stream(self) -> None:
        """运行流式客户端，支持自动重连。"""
        backoff_idx = 0
        while self._running:
            try:
                logger.debug("[%s] 正在启动流式客户端...", self.name)
                await self._stream_client.start()
            except asyncio.CancelledError:
                return
            except Exception as e:
                if not self._running:
                    return
                logger.warning("[%s] 流式客户端错误：%s", self.name, e)

            if not self._running:
                return

            # 按退避时间表等待后重连
            delay = RECONNECT_BACKOFF[min(backoff_idx, len(RECONNECT_BACKOFF) - 1)]
            logger.info("[%s] 将在 %d 秒后重连...", self.name, delay)
            await asyncio.sleep(delay)
            backoff_idx += 1

    async def disconnect(self) -> None:
        """断开与钉钉的连接。"""
        self._running = False
        self._mark_disconnected()

        if self._stream_task:
            self._stream_task.cancel()
            try:
                await self._stream_task
            except asyncio.CancelledError:
                pass
            self._stream_task = None

        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None

        self._stream_client = None
        self._session_webhooks.clear()
        self._dedup.clear()
        logger.info("[%s] 已断开连接", self.name)

    # -- 入站消息处理 -----------------------------------------

    async def _on_message(self, message: "ChatbotMessage") -> None:
        """处理收到的钉钉聊天机器人消息。"""
        msg_id = getattr(message, "message_id", None) or uuid.uuid4().hex
        if self._dedup.is_duplicate(msg_id):
            logger.debug("[%s] 重复消息 %s，跳过", self.name, msg_id)
            return

        text = self._extract_text(message)
        if not text:
            logger.debug("[%s] 空消息，跳过", self.name)
            return

        # 聊天上下文信息
        conversation_id = getattr(message, "conversation_id", "") or ""
        conversation_type = getattr(message, "conversation_type", "1")
        is_group = str(conversation_type) == "2"  # "2" 表示群聊
        sender_id = getattr(message, "sender_id", "") or ""
        sender_nick = getattr(message, "sender_nick", "") or sender_id
        sender_staff_id = getattr(message, "sender_staff_id", "") or ""

        chat_id = conversation_id or sender_id
        chat_type = "group" if is_group else "dm"

        # 存储会话 Webhook 用于回复路由（验证来源以防止 SSRF 攻击）
        session_webhook = getattr(message, "session_webhook", None) or ""
        if session_webhook and chat_id and _DINGTALK_WEBHOOK_RE.match(session_webhook):
            if len(self._session_webhooks) >= _SESSION_WEBHOOKS_MAX:
                # 淘汰最旧的条目以限制内存增长
                try:
                    self._session_webhooks.pop(next(iter(self._session_webhooks)))
                except StopIteration:
                    pass
            self._session_webhooks[chat_id] = session_webhook

        source = self.build_source(
            chat_id=chat_id,
            chat_name=getattr(message, "conversation_title", None),
            chat_type=chat_type,
            user_id=sender_id,
            user_name=sender_nick,
            user_id_alt=sender_staff_id if sender_staff_id else None,
        )

        # 解析消息时间戳
        create_at = getattr(message, "create_at", None)
        try:
            # 钉钉时间戳为毫秒级，需除以 1000 转换为秒
            timestamp = datetime.fromtimestamp(int(create_at) / 1000, tz=timezone.utc) if create_at else datetime.now(tz=timezone.utc)
        except (ValueError, OSError, TypeError):
            timestamp = datetime.now(tz=timezone.utc)

        event = MessageEvent(
            text=text,
            message_type=MessageType.TEXT,
            source=source,
            message_id=msg_id,
            raw_message=message,
            timestamp=timestamp,
        )

        logger.debug("[%s] 收到来自 %s 在 %s 中的消息：%s",
                      self.name, sender_nick, chat_id[:20] if chat_id else "?", text[:50])
        await self.handle_message(event)

    @staticmethod
    def _extract_text(message: "ChatbotMessage") -> str:
        """从钉钉聊天机器人消息中提取纯文本。

        处理 dingtalk-stream SDK 的新旧两种载荷格式：
          * 旧版：``message.text`` 是字典 ``{"content": "..."}``
          * >= 0.20 版：``message.text`` 是 ``TextContent`` 数据类，其
            ``__str__`` 返回 ``"TextContent(content=...)"``——不能直接
            用 ``str(text)``，必须先提取 ``.content`` 属性。
          * 富文本从 ``message.rich_text``（列表）迁移到了
            ``message.rich_text_content.rich_text_list``（字典列表）。
        """
        text = getattr(message, "text", None)
        content = ""
        if text is not None:
            if isinstance(text, dict):
                # 旧版 SDK：text 是字典
                content = (text.get("content") or "").strip()
            elif hasattr(text, "content"):
                # 新版 SDK：text 是 TextContent 对象
                content = str(text.content or "").strip()
            else:
                content = str(text).strip()

        # 如果纯文本为空，尝试从富文本中提取
        if not content:
            rich_list = None
            rtc = getattr(message, "rich_text_content", None)
            if rtc is not None and hasattr(rtc, "rich_text_list"):
                rich_list = rtc.rich_text_list
            if rich_list is None:
                rich_list = getattr(message, "rich_text", None)
            if rich_list and isinstance(rich_list, list):
                parts = [item["text"] for item in rich_list
                         if isinstance(item, dict) and item.get("text")]
                content = " ".join(parts).strip()
        return content

    # -- 出站消息发送 -------------------------------------------------

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """通过钉钉会话 Webhook 发送 Markdown 格式的回复。"""
        metadata = metadata or {}

        # 获取会话 Webhook URL：优先使用 metadata 中的，其次使用缓存中的
        session_webhook = metadata.get("session_webhook") or self._session_webhooks.get(chat_id)
        if not session_webhook:
            return SendResult(success=False,
                              error="没有可用的 session_webhook。回复必须跟随一条入站消息。")

        if not self._http_client:
            return SendResult(success=False, error="HTTP 客户端未初始化")

        # 构建 Markdown 格式的消息载荷
        payload = {
            "msgtype": "markdown",
            "markdown": {"title": "Hermes", "text": content[:self.MAX_MESSAGE_LENGTH]},
        }

        try:
            resp = await self._http_client.post(session_webhook, json=payload, timeout=15.0)
            if resp.status_code < 300:
                return SendResult(success=True, message_id=uuid.uuid4().hex[:12])
            body = resp.text
            logger.warning("[%s] 发送失败 HTTP %d：%s", self.name, resp.status_code, body[:200])
            return SendResult(success=False, error=f"HTTP {resp.status_code}: {body[:200]}")
        except httpx.TimeoutException:
            return SendResult(success=False, error="向钉钉发送消息超时")
        except Exception as e:
            logger.error("[%s] 发送错误：%s", self.name, e)
            return SendResult(success=False, error=str(e))

    async def send_typing(self, chat_id: str, metadata=None) -> None:
        """钉钉不支持正在输入指示器。"""
        pass

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        """返回钉钉会话的基本信息。"""
        return {"name": chat_id, "type": "group" if "group" in chat_id.lower() else "dm"}


# ---------------------------------------------------------------------------
# 内部流式消息处理器
# ---------------------------------------------------------------------------

class _IncomingHandler(ChatbotHandler if DINGTALK_STREAM_AVAILABLE else object):
    """dingtalk-stream 的 ChatbotHandler，将消息转发给适配器处理。"""

    def __init__(self, adapter: DingTalkAdapter, loop: asyncio.AbstractEventLoop):
        if DINGTALK_STREAM_AVAILABLE:
            super().__init__()
        self._adapter = adapter
        self._loop = loop

    async def process(self, callback_message):
        """当 dingtalk-stream 收到消息时调用。

        dingtalk-stream >= 0.24 传入的是 CallbackMessage，
        其 ``.data`` 包含聊天机器人的载荷。将其转换为
        ChatbotMessage 并在主事件循环上异步调用适配器处理器。
        """
        try:
            chatbot_msg = ChatbotMessage.from_dict(callback_message.data)
            await self._adapter._on_message(chatbot_msg)
        except Exception:
            logger.exception("[DingTalk] 处理入站消息时出错")

        return dingtalk_stream.AckMessage.STATUS_OK, "OK"
