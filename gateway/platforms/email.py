"""
邮件平台适配器，用于 Hermes 网关。

允许用户通过发送电子邮件与 Hermes 交互。
使用 IMAP 接收邮件，使用 SMTP 发送邮件。

环境变量：
    EMAIL_IMAP_HOST     — IMAP 服务器主机（例如 imap.gmail.com）
    EMAIL_IMAP_PORT     — IMAP 服务器端口（默认：993）
    EMAIL_SMTP_HOST     — SMTP 服务器主机（例如 smtp.gmail.com）
    EMAIL_SMTP_PORT     — SMTP 服务器端口（默认：587）
    EMAIL_ADDRESS       — Agent 使用的邮箱地址
    EMAIL_PASSWORD      — 邮箱密码或应用专用密码
    EMAIL_POLL_INTERVAL — 检查邮箱的间隔秒数（默认：15）
    EMAIL_ALLOWED_USERS — 允许的发件人地址，逗号分隔
"""

import asyncio
import email as email_lib
import imaplib
import logging
import os
import re
import smtplib
import ssl
import uuid
from email.header import decode_header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from pathlib import Path
from typing import Any, Dict, List, Optional

from gateway.platforms.base import (
    BasePlatformAdapter,
    MessageEvent,
    MessageType,
    SendResult,
    cache_document_from_bytes,
    cache_image_from_bytes,
)
from gateway.config import Platform, PlatformConfig

logger = logging.getLogger(__name__)
# 自动发件人匹配模式——来自这些地址的邮件会被静默忽略
_NOREPLY_PATTERNS = (
    "noreply", "no-reply", "no_reply", "donotreply", "do-not-reply",
    "mailer-daemon", "postmaster", "bounce", "notifications@",
    "automated@", "auto-confirm", "auto-reply", "automailer",
)

# 表示批量/自动化邮件的 RFC 邮件头
_AUTOMATED_HEADERS = {
    "Auto-Submitted": lambda v: v.lower() != "no",
    "Precedence": lambda v: v.lower() in ("bulk", "list", "junk"),
    "X-Auto-Response-Suppress": lambda v: bool(v),
    "List-Unsubscribe": lambda v: bool(v),
}

# Gmail 安全的单封邮件正文最大长度
MAX_MESSAGE_LENGTH = 50_000

# 用于内联检测的支持的图片扩展名
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}

def _is_automated_sender(address: str, headers: dict) -> bool:
    """如果此邮件来自自动化/无需回复的来源，则返回 True。"""
    addr = address.lower()
    # 检查发件人地址是否匹配已知的自动化模式
    if any(pattern in addr for pattern in _NOREPLY_PATTERNS):
        return True
    # 检查邮件头中的自动化标识
    for header, check in _AUTOMATED_HEADERS.items():
        value = headers.get(header, "")
        if value and check(value):
            return True
    return False

def check_email_requirements() -> bool:
    """检查邮件平台依赖项是否可用。"""
    addr = os.getenv("EMAIL_ADDRESS")
    pwd = os.getenv("EMAIL_PASSWORD")
    imap = os.getenv("EMAIL_IMAP_HOST")
    smtp = os.getenv("EMAIL_SMTP_HOST")
    if not all([addr, pwd, imap, smtp]):
        return False
    return True


def _decode_header_value(raw: str) -> str:
    """将 RFC 2047 编码的邮件头解码为纯文本字符串。"""
    parts = decode_header(raw)
    decoded = []
    for part, charset in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(part)
    return " ".join(decoded)


def _extract_text_body(msg: email_lib.message.Message) -> str:
    """从可能是多部分的邮件中提取纯文本正文。"""
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            disposition = str(part.get("Content-Disposition", ""))
            # 跳过附件
            if "attachment" in disposition:
                continue
            if content_type == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace")
        # 兜底：尝试 text/html 并剥离标签
        for part in msg.walk():
            content_type = part.get_content_type()
            disposition = str(part.get("Content-Disposition", ""))
            if "attachment" in disposition:
                continue
            if content_type == "text/html":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    html = payload.decode(charset, errors="replace")
                    return _strip_html(html)
        return ""
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            text = payload.decode(charset, errors="replace")
            if msg.get_content_type() == "text/html":
                return _strip_html(text)
            return text
        return ""


def _strip_html(html: str) -> str:
    """简易 HTML 标签剥离器，用于兜底文本提取。"""
    text = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
    text = re.sub(r"<p[^>]*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_email_address(raw: str) -> str:
    """从 'Name <addr>' 格式中提取纯邮箱地址。"""
    match = re.search(r"<([^>]+)>", raw)
    if match:
        return match.group(1).strip().lower()
    return raw.strip().lower()


def _extract_attachments(
    msg: email_lib.message.Message,
    skip_attachments: bool = False,
) -> List[Dict[str, Any]]:
    """提取附件元数据并将文件缓存到本地。

    当 *skip_attachments* 为 True 时，所有附件/内联部分都会被忽略
    （适用于恶意软件防护或带宽节省场景）。
    """
    attachments = []
    if not msg.is_multipart():
        return attachments

    for part in msg.walk():
        disposition = str(part.get("Content-Disposition", ""))
        if skip_attachments and ("attachment" in disposition or "inline" in disposition):
            continue
        if "attachment" not in disposition and "inline" not in disposition:
            continue
        # 跳过 text/plain 和 text/html 正文部分
        content_type = part.get_content_type()
        if content_type in ("text/plain", "text/html") and "attachment" not in disposition:
            continue

        filename = part.get_filename()
        if filename:
            filename = _decode_header_value(filename)
        else:
            ext = part.get_content_subtype() or "bin"
            filename = f"attachment.{ext}"

        payload = part.get_payload(decode=True)
        if not payload:
            continue

        ext = Path(filename).suffix.lower()
        if ext in _IMAGE_EXTS:
            # 图片附件：验证魔数字节后缓存
            try:
                cached_path = cache_image_from_bytes(payload, ext)
            except ValueError:
                logger.debug("跳过非图片附件 %s（无效的魔数字节）", filename)
                continue
            attachments.append({
                "path": cached_path,
                "filename": filename,
                "type": "image",
                "media_type": content_type,
            })
        else:
            # 非图片附件：作为文档缓存
            cached_path = cache_document_from_bytes(payload, filename)
            attachments.append({
                "path": cached_path,
                "filename": filename,
                "type": "document",
                "media_type": content_type,
            })

    return attachments


class EmailAdapter(BasePlatformAdapter):
    """使用 IMAP（接收）和 SMTP（发送）的邮件网关适配器。"""

    def __init__(self, config: PlatformConfig):
        super().__init__(config, Platform.EMAIL)

        self._address = os.getenv("EMAIL_ADDRESS", "")
        self._password = os.getenv("EMAIL_PASSWORD", "")
        self._imap_host = os.getenv("EMAIL_IMAP_HOST", "")
        self._imap_port = int(os.getenv("EMAIL_IMAP_PORT", "993"))
        self._smtp_host = os.getenv("EMAIL_SMTP_HOST", "")
        self._smtp_port = int(os.getenv("EMAIL_SMTP_PORT", "587"))
        self._poll_interval = int(os.getenv("EMAIL_POLL_INTERVAL", "15"))

        # 是否跳过附件——通过 config.yaml 配置：
        #   platforms:
        #     email:
        #       skip_attachments: true
        extra = config.extra or {}
        self._skip_attachments = extra.get("skip_attachments", False)

        # 跟踪已处理的消息 UID，避免重复处理
        self._seen_uids: set = set()
        self._seen_uids_max: int = 2000   # 设置上限以防止内存无限增长
        self._poll_task: Optional[asyncio.Task] = None

        # 映射 chat_id（发件人邮箱）-> 最近的主题和消息 ID，用于邮件线程
        self._thread_context: Dict[str, Dict[str, str]] = {}

        logger.info("[Email] 适配器已初始化，邮箱地址：%s", self._address)

    def _trim_seen_uids(self) -> None:
        """仅保留最近的 UID 以防止内存无限增长。

        IMAP UID 是单调递增的整数。当集合超出上限时，
        只保留较大的一半——旧 UID 可以安全丢弃，因为新消息
        总是有更大的 UID，且 IMAP 的 UNSEEN 标志可以防止重复投递。
        """
        if len(self._seen_uids) <= self._seen_uids_max:
            return
        try:
            # UID 是类似 b'1234' 的字节——按数值排序并保留较大的一半
            sorted_uids = sorted(self._seen_uids, key=lambda u: int(u))
            keep = self._seen_uids_max // 2
            self._seen_uids = set(sorted_uids[-keep:])
            logger.debug("[Email] 已裁剪已见 UID 至 %d 条", len(self._seen_uids))
        except (ValueError, TypeError):
            # 兜底：如果排序失败，直接截断
            self._seen_uids = set(list(self._seen_uids)[-self._seen_uids_max // 2:])

    async def connect(self) -> bool:
        """连接到 IMAP 服务器并开始轮询新邮件。"""
        try:
            # 测试 IMAP 连接
            imap = imaplib.IMAP4_SSL(self._imap_host, self._imap_port, timeout=30)
            imap.login(self._address, self._password)
            # 将所有现有邮件标记为已见，这样只处理新邮件
            imap.select("INBOX")
            status, data = imap.uid("search", None, "ALL")
            if status == "OK" and data and data[0]:
                for uid in data[0].split():
                    self._seen_uids.add(uid)
            # 仅保留最近的 UID 以防止内存无限增长
            self._trim_seen_uids()
            imap.logout()
            logger.info("[Email] IMAP 连接测试通过。已跳过 %d 封现有邮件。", len(self._seen_uids))
        except Exception as e:
            logger.error("[Email] IMAP 连接失败：%s", e)
            return False

        try:
            # 测试 SMTP 连接
            smtp = smtplib.SMTP(self._smtp_host, self._smtp_port, timeout=30)
            smtp.starttls(context=ssl.create_default_context())
            smtp.login(self._address, self._password)
            smtp.quit()
            logger.info("[Email] SMTP 连接测试通过。")
        except Exception as e:
            logger.error("[Email] SMTP 连接失败：%s", e)
            return False

        self._running = True
        self._poll_task = asyncio.create_task(self._poll_loop())
        print(f"[Email] 已连接，邮箱地址：{self._address}")
        return True

    async def disconnect(self) -> None:
        """停止轮询并断开连接。"""
        self._running = False
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None
        logger.info("[Email] 已断开连接。")

    async def _poll_loop(self) -> None:
        """按固定间隔轮询 IMAP 检查新邮件。"""
        while self._running:
            try:
                await self._check_inbox()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("[Email] 轮询错误：%s", e)
            await asyncio.sleep(self._poll_interval)

    async def _check_inbox(self) -> None:
        """检查收件箱中的未读邮件并分发处理。"""
        # 在线程中运行 IMAP 操作，避免阻塞事件循环
        loop = asyncio.get_running_loop()
        messages = await loop.run_in_executor(None, self._fetch_new_messages)
        for msg_data in messages:
            await self._dispatch_message(msg_data)

    def _fetch_new_messages(self) -> List[Dict[str, Any]]:
        """从 IMAP 获取新的（未读）邮件。在 executor 线程中运行。"""
        results = []
        try:
            imap = imaplib.IMAP4_SSL(self._imap_host, self._imap_port, timeout=30)
            try:
                imap.login(self._address, self._password)
                imap.select("INBOX")

                status, data = imap.uid("search", None, "UNSEEN")
                if status != "OK" or not data or not data[0]:
                    return results

                for uid in data[0].split():
                    if uid in self._seen_uids:
                        continue
                    self._seen_uids.add(uid)
                    # 定期裁剪以防止内存无限增长
                    if len(self._seen_uids) > self._seen_uids_max:
                        self._trim_seen_uids()

                    status, msg_data = imap.uid("fetch", uid, "(RFC822)")
                    if status != "OK":
                        continue

                    raw_email = msg_data[0][1]
                    msg = email_lib.message_from_bytes(raw_email)

                    # 解析发件人信息
                    sender_raw = msg.get("From", "")
                    sender_addr = _extract_email_address(sender_raw)
                    sender_name = _decode_header_value(sender_raw)
                    # 如果名称中包含邮箱地址，移除它
                    if "<" in sender_name:
                        sender_name = sender_name.split("<")[0].strip().strip('"')

                    subject = _decode_header_value(msg.get("Subject", "(no subject)"))
                    message_id = msg.get("Message-ID", "")
                    in_reply_to = msg.get("In-Reply-To", "")
                    # 在进行任何处理前跳过自动化/无需回复的发件人
                    msg_headers = dict(msg.items())
                    if _is_automated_sender(sender_addr, msg_headers):
                        logger.debug("[Email] 跳过自动化发件人：%s", sender_addr)
                        continue
                    body = _extract_text_body(msg)
                    attachments = _extract_attachments(msg, skip_attachments=self._skip_attachments)

                    results.append({
                        "uid": uid,
                        "sender_addr": sender_addr,
                        "sender_name": sender_name,
                        "subject": subject,
                        "message_id": message_id,
                        "in_reply_to": in_reply_to,
                        "body": body,
                        "attachments": attachments,
                        "date": msg.get("Date", ""),
                    })
            finally:
                try:
                    imap.logout()
                except Exception:
                    pass
        except Exception as e:
            logger.error("[Email] IMAP 获取错误：%s", e)
        return results

    async def _dispatch_message(self, msg_data: Dict[str, Any]) -> None:
        """将获取的邮件转换为 MessageEvent 并分发处理。"""
        sender_addr = msg_data["sender_addr"]

        # 跳过自己发的邮件
        if sender_addr == self._address.lower():
            return

        # 永远不要回复自动化发件人
        if _is_automated_sender(sender_addr, {}):
            logger.debug("[Email] 在分发阶段丢弃自动化发件人：%s", sender_addr)
            return

        subject = msg_data["subject"]
        body = msg_data["body"].strip()
        attachments = msg_data["attachments"]

        # 构建消息文本：包含主题作为上下文
        text = body
        if subject and not subject.startswith("Re:"):
            text = f"[Subject: {subject}]\n\n{body}"

        # 确定消息类型和媒体信息
        media_urls = []
        media_types = []
        msg_type = MessageType.TEXT

        for att in attachments:
            media_urls.append(att["path"])
            media_types.append(att["media_type"])
            if att["type"] == "image":
                msg_type = MessageType.PHOTO

        # 存储线程上下文用于回复邮件的线程追踪
        self._thread_context[sender_addr] = {
            "subject": subject,
            "message_id": msg_data["message_id"],
        }

        source = self.build_source(
            chat_id=sender_addr,
            chat_name=msg_data["sender_name"] or sender_addr,
            chat_type="dm",
            user_id=sender_addr,
            user_name=msg_data["sender_name"] or sender_addr,
        )

        event = MessageEvent(
            text=text or "(empty email)",
            message_type=msg_type,
            source=source,
            message_id=msg_data["message_id"],
            media_urls=media_urls,
            media_types=media_types,
            reply_to_message_id=msg_data["in_reply_to"] or None,
        )

        logger.info("[Email] 收到新邮件，发件人：%s，主题：%s", sender_addr, subject)
        await self.handle_message(event)

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """向指定地址发送邮件回复。"""
        try:
            loop = asyncio.get_running_loop()
            message_id = await loop.run_in_executor(
                None, self._send_email, chat_id, content, reply_to
            )
            return SendResult(success=True, message_id=message_id)
        except Exception as e:
            logger.error("[Email] 发送到 %s 失败：%s", chat_id, e)
            return SendResult(success=False, error=str(e))

    def _send_email(
        self,
        to_addr: str,
        body: str,
        reply_to_msg_id: Optional[str] = None,
    ) -> str:
        """通过 SMTP 发送邮件。在 executor 线程中运行。"""
        msg = MIMEMultipart()
        msg["From"] = self._address
        msg["To"] = to_addr

        # 获取回复的线程上下文
        ctx = self._thread_context.get(to_addr, {})
        subject = ctx.get("subject", "Hermes Agent")
        if not subject.startswith("Re:"):
            subject = f"Re: {subject}"
        msg["Subject"] = subject

        # 设置邮件线程头（In-Reply-To 和 References）
        original_msg_id = reply_to_msg_id or ctx.get("message_id")
        if original_msg_id:
            msg["In-Reply-To"] = original_msg_id
            msg["References"] = original_msg_id

        msg_id = f"<hermes-{uuid.uuid4().hex[:12]}@{self._address.split('@')[1]}>"
        msg["Message-ID"] = msg_id

        msg.attach(MIMEText(body, "plain", "utf-8"))

        smtp = smtplib.SMTP(self._smtp_host, self._smtp_port, timeout=30)
        try:
            smtp.starttls(context=ssl.create_default_context())
            smtp.login(self._address, self._password)
            smtp.send_message(msg)
        finally:
            try:
                smtp.quit()
            except Exception:
                smtp.close()

        logger.info("[Email] 已发送回复到 %s（主题：%s）", to_addr, subject)
        return msg_id

    async def send_typing(self, chat_id: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        """邮件没有正在输入指示器——空操作。"""

    async def send_image(
        self,
        chat_id: str,
        image_url: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
    ) -> SendResult:
        """将图片 URL 作为邮件正文的一部分发送。"""
        text = caption or ""
        text += f"\n\nImage: {image_url}"
        return await self.send(chat_id, text.strip(), reply_to)

    async def send_document(
        self,
        chat_id: str,
        file_path: str,
        caption: Optional[str] = None,
        file_name: Optional[str] = None,
        reply_to: Optional[str] = None,
    ) -> SendResult:
        """将文件作为邮件附件发送。"""
        try:
            loop = asyncio.get_running_loop()
            message_id = await loop.run_in_executor(
                None,
                self._send_email_with_attachment,
                chat_id,
                caption or "",
                file_path,
                file_name,
            )
            return SendResult(success=True, message_id=message_id)
        except Exception as e:
            logger.error("[Email] 发送文档失败：%s", e)
            return SendResult(success=False, error=str(e))

    def _send_email_with_attachment(
        self,
        to_addr: str,
        body: str,
        file_path: str,
        file_name: Optional[str] = None,
    ) -> str:
        """通过 SMTP 发送带附件的邮件。"""
        msg = MIMEMultipart()
        msg["From"] = self._address
        msg["To"] = to_addr

        ctx = self._thread_context.get(to_addr, {})
        subject = ctx.get("subject", "Hermes Agent")
        if not subject.startswith("Re:"):
            subject = f"Re: {subject}"
        msg["Subject"] = subject

        original_msg_id = ctx.get("message_id")
        if original_msg_id:
            msg["In-Reply-To"] = original_msg_id
            msg["References"] = original_msg_id

        msg_id = f"<hermes-{uuid.uuid4().hex[:12]}@{self._address.split('@')[1]}>"
        msg["Message-ID"] = msg_id

        if body:
            msg.attach(MIMEText(body, "plain", "utf-8"))

        # 添加文件附件
        p = Path(file_path)
        fname = file_name or p.name
        with open(p, "rb") as f:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(f.read())
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", f"attachment; filename={fname}")
            msg.attach(part)

        smtp = smtplib.SMTP(self._smtp_host, self._smtp_port, timeout=30)
        try:
            smtp.starttls(context=ssl.create_default_context())
            smtp.login(self._address, self._password)
            smtp.send_message(msg)
        finally:
            try:
                smtp.quit()
            except Exception:
                smtp.close()

        return msg_id

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        """返回邮件聊天的基本信息。"""
        ctx = self._thread_context.get(chat_id, {})
        return {
            "name": chat_id,
            "type": "dm",
            "chat_id": chat_id,
            "subject": ctx.get("subject", ""),
        }
