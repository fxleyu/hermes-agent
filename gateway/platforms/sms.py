"""SMS（Twilio）平台适配器。

通过 Twilio REST API 发送出站短信，并运行 aiohttp
Webhook 服务器接收入站消息。

与可选的电话技能共享凭据——使用相同的环境变量：
  - TWILIO_ACCOUNT_SID
  - TWILIO_AUTH_TOKEN
  - TWILIO_PHONE_NUMBER  （E.164 格式的发送号码，例如 +15551234567）

网关专用环境变量：
  - SMS_WEBHOOK_PORT     （默认 8080）
  - SMS_WEBHOOK_HOST     （默认 0.0.0.0）
  - SMS_WEBHOOK_URL      （Twilio 签名验证所需的公网 URL——必填）
  - SMS_INSECURE_NO_SIGNATURE  （设为 true 可禁用签名验证——仅限开发环境）
  - SMS_ALLOWED_USERS    （逗号分隔的 E.164 格式电话号码列表）
  - SMS_ALLOW_ALL_USERS  （true/false）
  - SMS_HOME_CHANNEL     （定时任务消息投递的电话号码）
"""

import asyncio
import base64
import hashlib
import hmac
import logging
import os
import urllib.parse
from typing import Any, Dict, Optional

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import (
    BasePlatformAdapter,
    MessageEvent,
    MessageType,
    SendResult,
)
from gateway.platforms.helpers import redact_phone, strip_markdown

logger = logging.getLogger(__name__)

TWILIO_API_BASE = "https://api.twilio.com/2010-04-01/Accounts"
MAX_SMS_LENGTH = 1600  # 约 10 个短信分段
DEFAULT_WEBHOOK_PORT = 8080
DEFAULT_WEBHOOK_HOST = "0.0.0.0"


def check_sms_requirements() -> bool:
    """检查 SMS 适配器的依赖项是否可用。"""
    try:
        import aiohttp  # noqa: F401
    except ImportError:
        return False
    return bool(os.getenv("TWILIO_ACCOUNT_SID") and os.getenv("TWILIO_AUTH_TOKEN"))


class SmsAdapter(BasePlatformAdapter):
    """
    Twilio SMS <-> Hermes 网关适配器。

    每个入站电话号码对应一个独立的 Hermes 会话（多租户模式）。
    回复始终从配置的 TWILIO_PHONE_NUMBER 发送。
    """

    MAX_MESSAGE_LENGTH = MAX_SMS_LENGTH

    def __init__(self, config: PlatformConfig):
        super().__init__(config, Platform.SMS)
        self._account_sid: str = os.environ["TWILIO_ACCOUNT_SID"]
        self._auth_token: str = os.environ["TWILIO_AUTH_TOKEN"]
        self._from_number: str = os.getenv("TWILIO_PHONE_NUMBER", "")
        self._webhook_port: int = int(
            os.getenv("SMS_WEBHOOK_PORT", str(DEFAULT_WEBHOOK_PORT))
        )
        self._webhook_host: str = os.getenv("SMS_WEBHOOK_HOST", DEFAULT_WEBHOOK_HOST)
        self._webhook_url: str = os.getenv("SMS_WEBHOOK_URL", "").strip()
        self._runner = None
        self._http_session: Optional["aiohttp.ClientSession"] = None

    def _basic_auth_header(self) -> str:
        """构建 Twilio 所需的 HTTP Basic 认证头。"""
        creds = f"{self._account_sid}:{self._auth_token}"
        encoded = base64.b64encode(creds.encode("ascii")).decode("ascii")
        return f"Basic {encoded}"

    # ------------------------------------------------------------------
    # 必须实现的抽象方法
    # ------------------------------------------------------------------

    async def connect(self) -> bool:
        import aiohttp
        from aiohttp import web

        if not self._from_number:
            logger.error("[sms] 未设置 TWILIO_PHONE_NUMBER——无法发送回复")
            return False

        # 检查是否禁用了签名验证（仅限开发环境）
        insecure_no_sig = os.getenv("SMS_INSECURE_NO_SIGNATURE", "").lower() == "true"

        if not self._webhook_url and not insecure_no_sig:
            logger.error(
                "[sms] 拒绝启动：SMS_WEBHOOK_URL 是 Twilio 签名验证所必需的。"
                "请将其设置为在 Twilio 控制台中配置的公网 URL"
                "（例如 https://example.com/webhooks/twilio）。"
                "如需在本地开发环境中跳过验证，请设置 "
                "SMS_INSECURE_NO_SIGNATURE=true（不建议在生产环境使用）。",
            )
            return False

        if insecure_no_sig and not self._webhook_url:
            logger.warning(
                "[sms] SMS_INSECURE_NO_SIGNATURE=true——Twilio 签名验证已禁用。"
                "任何能访问端口 %d 的客户端都可以注入消息。"
                "请勿在生产环境中使用此配置。",
                self._webhook_port,
            )

        # 创建 aiohttp Web 应用并注册路由
        app = web.Application()
        app.router.add_post("/webhooks/twilio", self._handle_webhook)
        app.router.add_get("/health", lambda _: web.Response(text="ok"))

        # 启动 Webhook 服务器
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self._webhook_host, self._webhook_port)
        await site.start()
        self._http_session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30),
        )
        self._running = True

        logger.info(
            "[sms] Twilio Webhook 服务器已在 %s:%d 上监听，发送号码：%s",
            self._webhook_host,
            self._webhook_port,
            redact_phone(self._from_number),
        )
        return True

    async def disconnect(self) -> None:
        if self._http_session:
            await self._http_session.close()
            self._http_session = None
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
        self._running = False
        logger.info("[sms] 已断开连接")

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        import aiohttp

        # 格式化消息（剥离 Markdown）并按长度分片
        formatted = self.format_message(content)
        chunks = self.truncate_message(formatted)
        last_result = SendResult(success=True)

        url = f"{TWILIO_API_BASE}/{self._account_sid}/Messages.json"
        headers = {
            "Authorization": self._basic_auth_header(),
        }

        # 优先使用持久化的 HTTP 会话，否则创建临时会话
        session = self._http_session or aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30),
        )
        try:
            for chunk in chunks:
                form_data = aiohttp.FormData()
                form_data.add_field("From", self._from_number)
                form_data.add_field("To", chat_id)
                form_data.add_field("Body", chunk)

                try:
                    async with session.post(url, data=form_data, headers=headers) as resp:
                        body = await resp.json()
                        if resp.status >= 400:
                            error_msg = body.get("message", str(body))
                            logger.error(
                                "[sms] 发送到 %s 失败：%s %s",
                                redact_phone(chat_id),
                                resp.status,
                                error_msg,
                            )
                            return SendResult(
                                success=False,
                                error=f"Twilio {resp.status}: {error_msg}",
                            )
                        msg_sid = body.get("sid", "")
                        last_result = SendResult(success=True, message_id=msg_sid)
                except Exception as e:
                    logger.error("[sms] 发送到 %s 时出错：%s", redact_phone(chat_id), e)
                    return SendResult(success=False, error=str(e))
        finally:
            # 仅在创建了临时会话（非持久化会话）时关闭它
            if not self._http_session and session:
                await session.close()

        return last_result

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        return {"name": chat_id, "type": "dm"}

    # ------------------------------------------------------------------
    # SMS 专用格式化
    # ------------------------------------------------------------------

    def format_message(self, content: str) -> str:
        """剥离 Markdown——短信会将其作为字面字符渲染。"""
        return strip_markdown(content)

    # ------------------------------------------------------------------
    # Twilio 签名验证
    # ------------------------------------------------------------------

    def _validate_twilio_signature(
        self, url: str, post_params: dict, signature: str,
    ) -> bool:
        """验证 ``X-Twilio-Signature`` 请求头（HMAC-SHA1，base64 编码）。

        同时尝试带默认端口和不带默认端口的 URL 两种变体，
        因为 Twilio 可能使用其中任一种进行签名。

        算法详见：https://www.twilio.com/docs/usage/security#validating-requests
        """
        if self._check_signature(url, post_params, signature):
            return True

        # 尝试端口变体 URL（添加或移除默认端口）
        variant = self._port_variant_url(url)
        if variant and self._check_signature(variant, post_params, signature):
            return True

        return False

    def _check_signature(
        self, url: str, post_params: dict, signature: str,
    ) -> bool:
        """计算并比较单个 Twilio 签名。"""
        # 按照 Twilio 签名算法：URL + 按键排序的参数拼接
        data_to_sign = url
        for key in sorted(post_params.keys()):
            data_to_sign += key + post_params[key]
        mac = hmac.new(
            self._auth_token.encode("utf-8"),
            data_to_sign.encode("utf-8"),
            hashlib.sha1,
        )
        computed = base64.b64encode(mac.digest()).decode("utf-8")
        return hmac.compare_digest(computed, signature)

    @staticmethod
    def _port_variant_url(url: str) -> str | None:
        """返回切换了默认端口的 URL 变体，如果不适用则返回 None。

        仅切换默认端口（https 为 443，http 为 80）。
        非标准端口不会被修改。
        """
        parsed = urllib.parse.urlparse(url)
        default_ports = {"https": 443, "http": 80}
        default_port = default_ports.get(parsed.scheme)
        if default_port is None:
            return None

        if parsed.port == default_port:
            # URL 中有显式默认端口 -> 移除它
            return urllib.parse.urlunparse(
                (parsed.scheme, parsed.hostname, parsed.path,
                 parsed.params, parsed.query, parsed.fragment)
            )
        elif parsed.port is None:
            # URL 中没有端口 -> 添加默认端口
            netloc = f"{parsed.hostname}:{default_port}"
            return urllib.parse.urlunparse(
                (parsed.scheme, netloc, parsed.path,
                 parsed.params, parsed.query, parsed.fragment)
            )

        # 非标准端口——没有变体
        return None

    # ------------------------------------------------------------------
    # Twilio Webhook 处理器
    # ------------------------------------------------------------------

    async def _handle_webhook(self, request) -> "aiohttp.web.Response":
        from aiohttp import web

        try:
            raw = await request.read()
            # Twilio 发送表单编码数据，不是 JSON
            form = urllib.parse.parse_qs(raw.decode("utf-8"), keep_blank_values=True)
        except Exception as e:
            logger.error("[sms] Webhook 解析错误：%s", e)
            return web.Response(
                text='<?xml version="1.0" encoding="UTF-8"?><Response></Response>',
                content_type="application/xml",
                status=400,
            )

        # 当配置了 SMS_WEBHOOK_URL 时，验证 Twilio 请求签名
        if self._webhook_url:
            twilio_sig = request.headers.get("X-Twilio-Signature", "")
            if not twilio_sig:
                logger.warning("[sms] 已拒绝：缺少 X-Twilio-Signature 请求头")
                return web.Response(
                    text='<?xml version="1.0" encoding="UTF-8"?><Response></Response>',
                    content_type="application/xml",
                    status=403,
                )
            # 将 parse_qs 返回的列表值展平为单个值用于签名验证
            flat_params = {k: v[0] for k, v in form.items() if v}
            if not self._validate_twilio_signature(
                self._webhook_url, flat_params, twilio_sig
            ):
                logger.warning("[sms] 已拒绝：Twilio 签名无效")
                return web.Response(
                    text='<?xml version="1.0" encoding="UTF-8"?><Response></Response>',
                    content_type="application/xml",
                    status=403,
                )

        # 提取字段（parse_qs 返回的是列表）
        from_number = (form.get("From", [""]))[0].strip()
        to_number = (form.get("To", [""]))[0].strip()
        text = (form.get("Body", [""]))[0].strip()
        message_sid = (form.get("MessageSid", [""]))[0].strip()

        if not from_number or not text:
            return web.Response(
                text='<?xml version="1.0" encoding="UTF-8"?><Response></Response>',
                content_type="application/xml",
            )

        # 忽略来自自身号码的消息（防止回声/循环）
        if from_number == self._from_number:
            logger.debug("[sms] 忽略来自自身号码 %s 的回声消息", redact_phone(from_number))
            return web.Response(
                text='<?xml version="1.0" encoding="UTF-8"?><Response></Response>',
                content_type="application/xml",
            )

        logger.info(
            "[sms] 收到入站消息 %s -> %s：%s",
            redact_phone(from_number),
            redact_phone(to_number),
            text[:80],
        )

        # 构建消息来源信息和事件对象
        source = self.build_source(
            chat_id=from_number,
            chat_name=from_number,
            chat_type="dm",
            user_id=from_number,
            user_name=from_number,
        )
        event = MessageEvent(
            text=text,
            message_type=MessageType.TEXT,
            source=source,
            raw_message=form,
            message_id=message_sid,
        )

        # 非阻塞处理：Twilio 期望快速响应，因此异步处理消息
        task = asyncio.create_task(self.handle_message(event))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

        # 返回空的 TwiML——我们通过 REST API 发送回复，而不是内联 TwiML
        return web.Response(
            text='<?xml version="1.0" encoding="UTF-8"?><Response></Response>',
            content_type="application/xml",
        )
