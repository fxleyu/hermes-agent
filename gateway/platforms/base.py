"""
平台适配器基类接口。

所有平台适配器（Telegram、Discord、WhatsApp 等）都继承此基类
并实现其中的必要方法。
"""

import asyncio
import ipaddress
import logging
import os
import random
import re
import socket as _socket
import subprocess
import sys
import uuid
from abc import ABC, abstractmethod
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)


def utf16_len(s: str) -> int:
    """计算字符串 *s* 中 UTF-16 编码单元的数量。

    Telegram 的消息长度限制（4096）以 UTF-16 编码单元为计量单位，
    而**非** Unicode 码点。基本多文种平面之外的字符（如 😀 表情、
    CJK 扩展 B 区汉字、音乐符号等）在 UTF-16 中编码为代理对，
    因此每个字符占用**两个** UTF-16 编码单元，尽管 Python 的
    ``len()`` 将它们计为一个。

    移植自 nearai/ironclaw#2304，该 PR 发现了 Rust ``chars().count()``
    的相同差异。
    """
    return len(s.encode("utf-16-le")) // 2


def _prefix_within_utf16_limit(s: str, limit: int) -> str:
    """返回 *s* 中 UTF-16 长度不超过 *limit* 的最长前缀。

    与简单的 ``s[:limit]`` 不同，本函数会遵守代理对边界，
    不会将一个多编码单元字符从中间切断。
    """
    if utf16_len(s) <= limit:
        return s
    # 通过二分查找找到最长的安全前缀
    lo, hi = 0, len(s)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if utf16_len(s[:mid]) <= limit:
            lo = mid
        else:
            hi = mid - 1
    return s[:lo]


def _custom_unit_to_cp(s: str, budget: int, len_fn) -> int:
    """返回最大的码点偏移 *n*，使得 ``len_fn(s[:n]) <= budget``。

    当 :meth:`BasePlatformAdapter.truncate_message` 使用的 *len_fn*
    以不同于 Python 码点的单位度量长度（例如 UTF-16 编码单元）时使用此函数。
    内部通过二分查找实现，时间复杂度为 O(log n) 次 *len_fn* 调用。
    """
    if len_fn(s) <= budget:
        return len(s)
    lo, hi = 0, len(s)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if len_fn(s[:mid]) <= budget:
            lo = mid
        else:
            hi = mid - 1
    return lo


def is_network_accessible(host: str) -> bool:
    """判断 *host* 是否会将服务暴露到回环地址之外。

    回环地址（127.0.0.1、::1、IPv4 映射的 ::ffff:127.0.0.1）
    仅限本地访问。未指定地址（0.0.0.0、::）会绑定所有接口。
    主机名会进行 DNS 解析；DNS 解析失败时采用保守策略（返回 True）。
    """
    try:
        addr = ipaddress.ip_address(host)
        if addr.is_loopback:
            return False
        # ::ffff:127.0.0.1 — Python 对映射地址报告 is_loopback=False，
        # 因此需要显式检查底层 IPv4 地址。
        if getattr(addr, "ipv4_mapped", None) and addr.ipv4_mapped.is_loopback:
            return False
        return True
    except ValueError:
        # host 是主机名时，需要在下面进行 DNS 解析
        pass

    try:
        resolved = _socket.getaddrinfo(
            host, None, _socket.AF_UNSPEC, _socket.SOCK_STREAM,
        )
        # 如果主机名解析出至少一个非回环地址，则视为可通过网络访问
        for _family, _type, _proto, _canonname, sockaddr in resolved:
            addr = ipaddress.ip_address(sockaddr[0])
            if not addr.is_loopback:
                return True
        return False
    except (_socket.gaierror, OSError):
        return True


def _detect_macos_system_proxy() -> str | None:
    """通过 ``scutil --proxy`` 读取 macOS 系统 HTTP(S) 代理配置。

    如果启用了 HTTP 或 HTTPS 代理，返回 ``http://host:port`` 格式的 URL 字符串，
    否则返回 *None*。在非 macOS 系统或发生子进程错误时静默回退。
    """
    if sys.platform != "darwin":
        return None
    try:
        out = subprocess.check_output(
            ["scutil", "--proxy"], timeout=3, text=True, stderr=subprocess.DEVNULL,
        )
    except Exception:
        return None

    props: dict[str, str] = {}
    for line in out.splitlines():
        line = line.strip()
        if " : " in line:
            key, _, val = line.partition(" : ")
            props[key.strip()] = val.strip()

    # 优先使用 HTTPS 代理，回退到 HTTP 代理
    for enable_key, host_key, port_key in (
        ("HTTPSEnable", "HTTPSProxy", "HTTPSPort"),
        ("HTTPEnable", "HTTPProxy", "HTTPPort"),
    ):
        if props.get(enable_key) == "1":
            host = props.get(host_key)
            port = props.get(port_key)
            if host and port:
                return f"http://{host}:{port}"
    return None


def resolve_proxy_url(platform_env_var: str | None = None) -> str | None:
    """从环境变量或 macOS 系统代理中获取代理 URL。

    检查顺序：
      0. *platform_env_var*（例如 ``DISCORD_PROXY``）— 最高优先级
      1. HTTPS_PROXY / HTTP_PROXY / ALL_PROXY（及其小写变体）
      2. macOS 系统代理，通过 ``scutil --proxy`` 自动检测

    未找到代理时返回 *None*。
    """
    if platform_env_var:
        value = (os.environ.get(platform_env_var) or "").strip()
        if value:
            return value
    for key in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY",
                "https_proxy", "http_proxy", "all_proxy"):
        value = (os.environ.get(key) or "").strip()
        if value:
            return value
    return _detect_macos_system_proxy()


def proxy_kwargs_for_bot(proxy_url: str | None) -> dict:
    """为 ``commands.Bot()`` / ``discord.Client()`` 构建代理参数。

    返回值：
      - SOCKS URL  → ``{"connector": ProxyConnector(..., rdns=True)}``
      - HTTP URL   → ``{"proxy": url}``
      - *None*     → ``{}``

    ``rdns=True`` 强制通过代理进行远程 DNS 解析 — 许多 SOCKS 实现
    （如 Shadowrocket、Clash）要求此选项，且对于绕过 GFW 背后的
    DNS 污染至关重要。
    """
    if not proxy_url:
        return {}
    if proxy_url.lower().startswith("socks"):
        try:
            from aiohttp_socks import ProxyConnector

            connector = ProxyConnector.from_url(proxy_url, rdns=True)
            return {"connector": connector}
        except ImportError:
            logger.warning(
                "aiohttp_socks not installed — SOCKS proxy %s ignored. "
                "Run: pip install aiohttp-socks",
                proxy_url,
            )
            return {}
    return {"proxy": proxy_url}


def proxy_kwargs_for_aiohttp(proxy_url: str | None) -> tuple[dict, dict]:
    """为独立的 ``aiohttp.ClientSession`` 构建代理参数。

    返回 ``(session_kwargs, request_kwargs)``，其中：
      - SOCKS → ``({"connector": ProxyConnector(...)}, {})``
      - HTTP  → ``({}, {"proxy": url})``
      - None  → ``({}, {})``

    用法示例::

        sess_kw, req_kw = proxy_kwargs_for_aiohttp(proxy_url)
        async with aiohttp.ClientSession(**sess_kw) as session:
            async with session.get(url, **req_kw) as resp:
                ...
    """
    if not proxy_url:
        return {}, {}
    if proxy_url.lower().startswith("socks"):
        try:
            from aiohttp_socks import ProxyConnector

            connector = ProxyConnector.from_url(proxy_url, rdns=True)
            return {"connector": connector}, {}
        except ImportError:
            logger.warning(
                "aiohttp_socks not installed — SOCKS proxy %s ignored. "
                "Run: pip install aiohttp-socks",
                proxy_url,
            )
            return {}, {}
    return {}, {"proxy": proxy_url}


from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any, Callable, Awaitable, Tuple
from enum import Enum

from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))

from gateway.config import Platform, PlatformConfig
from gateway.session import SessionSource, build_session_key
from hermes_constants import get_hermes_dir


GATEWAY_SECRET_CAPTURE_UNSUPPORTED_MESSAGE = (
    "Secure secret entry is not supported over messaging. "
    "Load this skill in the local CLI to be prompted, or add the key to ~/.hermes/.env manually."
)


def safe_url_for_log(url: str, max_len: int = 80) -> str:
    """返回适合记录日志的 URL 字符串（去除查询参数/片段/用户凭据）。"""
    if max_len <= 0:
        return ""

    if url is None:
        return ""

    raw = str(url)
    if not raw:
        return ""

    try:
        parsed = urlsplit(raw)
    except Exception:
        return raw[:max_len]

    if parsed.scheme and parsed.netloc:
        # 去除可能嵌入的用户凭据（user:pass@host）
        netloc = parsed.netloc.rsplit("@", 1)[-1]
        base = f"{parsed.scheme}://{netloc}"
        path = parsed.path or ""
        if path and path != "/":
            basename = path.rsplit("/", 1)[-1]
            safe = f"{base}/.../{basename}" if basename else f"{base}/..."
        else:
            safe = base
    else:
        safe = raw

    if len(safe) <= max_len:
        return safe
    if max_len <= 3:
        return "." * max_len
    return f"{safe[:max_len - 3]}..."


async def _ssrf_redirect_guard(response):
    """对每个重定向目标重新进行安全校验，以防止基于重定向的 SSRF 攻击。

    如果没有此防护，攻击者可以搭建一个公网 URL，通过 302 重定向到
    http://169.254.169.254/ 来绕过请求前的 is_safe_url() 检查。

    必须使用 async 是因为 httpx.AsyncClient 的响应事件钩子需要 await。
    """
    if response.is_redirect and response.next_request:
        redirect_url = str(response.next_request.url)
        from tools.url_safety import is_safe_url
        if not is_safe_url(redirect_url):
            raise ValueError(
                f"Blocked redirect to private/internal address: {safe_url_for_log(redirect_url)}"
            )


# ---------------------------------------------------------------------------
# 图片缓存工具
#
# 当用户在消息平台上发送图片时，我们将其下载到本地缓存目录，
# 以便视觉工具（接收本地文件路径）进行分析。这样可以避免
# 平台临时 URL 失效的问题（例如 Telegram 文件 URL 约 1 小时后过期）。
# ---------------------------------------------------------------------------

# 默认路径：{HERMES_HOME}/cache/images/（历史路径：image_cache/）
IMAGE_CACHE_DIR = get_hermes_dir("cache/images", "image_cache")


def get_image_cache_dir() -> Path:
    """返回图片缓存目录，若不存在则创建。"""
    IMAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return IMAGE_CACHE_DIR


def _looks_like_image(data: bytes) -> bool:
    """如果 *data* 以已知的图片魔术字节序列开头，返回 True。"""
    if len(data) < 4:
        return False
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return True
    if data[:3] == b"\xff\xd8\xff":
        return True
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return True
    if data[:2] == b"BM":
        return True
    if data[:4] == b"RIFF" and len(data) >= 12 and data[8:12] == b"WEBP":
        return True
    return False


def cache_image_from_bytes(data: bytes, ext: str = ".jpg") -> str:
    """
    将原始图片字节保存到缓存并返回绝对文件路径。

    参数：
        data: 原始图片字节数据。
        ext:  文件扩展名，包含点号（例如 ".jpg"、".png"）。

    返回：
        缓存图片文件的绝对路径字符串。

    异常：
        ValueError: 如果 *data* 看起来不是有效图片（例如上游服务器返回的
            HTML 错误页面）。
    """
    if not _looks_like_image(data):
        snippet = data[:80].decode("utf-8", errors="replace")
        raise ValueError(
            f"Refusing to cache non-image data as {ext} "
            f"(starts with: {snippet!r})"
        )
    cache_dir = get_image_cache_dir()
    filename = f"img_{uuid.uuid4().hex[:12]}{ext}"
    filepath = cache_dir / filename
    filepath.write_bytes(data)
    return str(filepath)


async def cache_image_from_url(url: str, ext: str = ".jpg", retries: int = 2) -> str:
    """
    从 URL 下载图片并保存到本地缓存。

    对临时性故障（超时、429、5xx）进行指数退避重试，
    避免因单次 CDN 响应缓慢而丢失媒体文件。

    参数：
        url: 要下载的 HTTP/HTTPS URL。
        ext: 文件扩展名，包含点号（例如 ".jpg"、".png"）。
        retries: 临时性故障的重试次数。

    返回：
        缓存图片文件的绝对路径字符串。

    异常：
        ValueError: 如果 URL 指向私有/内部网络（SSRF 防护）。
    """
    from tools.url_safety import is_safe_url
    if not is_safe_url(url):
        raise ValueError(f"Blocked unsafe URL (SSRF protection): {safe_url_for_log(url)}")

    import asyncio
    import httpx
    import logging as _logging
    _log = _logging.getLogger(__name__)

    last_exc = None
    async with httpx.AsyncClient(
        timeout=30.0,
        follow_redirects=True,
        event_hooks={"response": [_ssrf_redirect_guard]},
    ) as client:
        for attempt in range(retries + 1):
            try:
                response = await client.get(
                    url,
                    headers={
                        "User-Agent": "Mozilla/5.0 (compatible; HermesAgent/1.0)",
                        "Accept": "image/*,*/*;q=0.8",
                    },
                )
                response.raise_for_status()
                return cache_image_from_bytes(response.content, ext)
            except (httpx.TimeoutException, httpx.HTTPStatusError) as exc:
                last_exc = exc
                if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code < 429:
                    raise
                if attempt < retries:
                    wait = 1.5 * (attempt + 1)
                    _log.debug(
                        "Media cache retry %d/%d for %s (%.1fs): %s",
                        attempt + 1,
                        retries,
                        safe_url_for_log(url),
                        wait,
                        exc,
                    )
                    await asyncio.sleep(wait)
                    continue
                raise
    raise last_exc


def cleanup_image_cache(max_age_hours: int = 24) -> int:
    """
    删除超过 *max_age_hours* 小时的缓存图片。

    返回被删除的文件数量。
    """
    import time

    cache_dir = get_image_cache_dir()
    cutoff = time.time() - (max_age_hours * 3600)
    removed = 0
    for f in cache_dir.iterdir():
        if f.is_file() and f.stat().st_mtime < cutoff:
            try:
                f.unlink()
                removed += 1
            except OSError:
                pass
    return removed


# ---------------------------------------------------------------------------
# 音频缓存工具
#
# 与图片缓存相同的模式 — 平台上的语音消息下载到此处，
# 以便 STT 工具（OpenAI Whisper）从本地文件进行语音转文字。
# ---------------------------------------------------------------------------

AUDIO_CACHE_DIR = get_hermes_dir("cache/audio", "audio_cache")


def get_audio_cache_dir() -> Path:
    """返回音频缓存目录，若不存在则创建。"""
    AUDIO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return AUDIO_CACHE_DIR


def cache_audio_from_bytes(data: bytes, ext: str = ".ogg") -> str:
    """
    将原始音频字节保存到缓存并返回绝对文件路径。

    参数：
        data: 原始音频字节数据。
        ext:  文件扩展名，包含点号（例如 ".ogg"、".mp3"）。

    返回：
        缓存音频文件的绝对路径字符串。
    """
    cache_dir = get_audio_cache_dir()
    filename = f"audio_{uuid.uuid4().hex[:12]}{ext}"
    filepath = cache_dir / filename
    filepath.write_bytes(data)
    return str(filepath)


async def cache_audio_from_url(url: str, ext: str = ".ogg", retries: int = 2) -> str:
    """
    从 URL 下载音频文件并保存到本地缓存。

    对临时性故障（超时、429、5xx）进行指数退避重试，
    避免因单次 CDN 响应缓慢而丢失媒体文件。

    参数：
        url: 要下载的 HTTP/HTTPS URL。
        ext: 文件扩展名，包含点号（例如 ".ogg"、".mp3"）。
        retries: 临时性故障的重试次数。

    返回：
        缓存音频文件的绝对路径字符串。

    异常：
        ValueError: 如果 URL 指向私有/内部网络（SSRF 防护）。
    """
    from tools.url_safety import is_safe_url
    if not is_safe_url(url):
        raise ValueError(f"Blocked unsafe URL (SSRF protection): {safe_url_for_log(url)}")

    import asyncio
    import httpx
    import logging as _logging
    _log = _logging.getLogger(__name__)

    last_exc = None
    async with httpx.AsyncClient(
        timeout=30.0,
        follow_redirects=True,
        event_hooks={"response": [_ssrf_redirect_guard]},
    ) as client:
        for attempt in range(retries + 1):
            try:
                response = await client.get(
                    url,
                    headers={
                        "User-Agent": "Mozilla/5.0 (compatible; HermesAgent/1.0)",
                        "Accept": "audio/*,*/*;q=0.8",
                    },
                )
                response.raise_for_status()
                return cache_audio_from_bytes(response.content, ext)
            except (httpx.TimeoutException, httpx.HTTPStatusError) as exc:
                last_exc = exc
                if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code < 429:
                    raise
                if attempt < retries:
                    wait = 1.5 * (attempt + 1)
                    _log.debug(
                        "Audio cache retry %d/%d for %s (%.1fs): %s",
                        attempt + 1,
                        retries,
                        safe_url_for_log(url),
                        wait,
                        exc,
                    )
                    await asyncio.sleep(wait)
                    continue
                raise
    raise last_exc


# ---------------------------------------------------------------------------
# 文档缓存工具
#
# 与图片/音频缓存相同的模式 — 平台上的文档下载到此处，
# 以便 Agent 通过本地文件路径引用它们。
# ---------------------------------------------------------------------------

DOCUMENT_CACHE_DIR = get_hermes_dir("cache/documents", "document_cache")

SUPPORTED_DOCUMENT_TYPES = {
    ".pdf": "application/pdf",
    ".md": "text/markdown",
    ".txt": "text/plain",
    ".log": "text/plain",
    ".zip": "application/zip",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}


def get_document_cache_dir() -> Path:
    """返回文档缓存目录，若不存在则创建。"""
    DOCUMENT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return DOCUMENT_CACHE_DIR


def cache_document_from_bytes(data: bytes, filename: str) -> str:
    """
    将原始文档字节保存到缓存并返回绝对文件路径。

    缓存文件名保留原始的可读文件名，并添加唯一前缀：
    ``doc_{uuid12}_{original_filename}``。

    参数：
        data: 原始文档字节数据。
        filename: 原始文件名（例如 "report.pdf"）。

    返回：
        缓存文档文件的绝对路径字符串。

    异常：
        ValueError: 如果清理后的路径逃逸出缓存目录。
    """
    cache_dir = get_document_cache_dir()
    # 清理：去除目录组件、null 字节和控制字符
    safe_name = Path(filename).name if filename else "document"
    safe_name = safe_name.replace("\x00", "").strip()
    if not safe_name or safe_name in (".", ".."):
        safe_name = "document"
    cached_name = f"doc_{uuid.uuid4().hex[:12]}_{safe_name}"
    filepath = cache_dir / cached_name
    # 最终安全检查：确保路径仍在缓存目录内
    if not filepath.resolve().is_relative_to(cache_dir.resolve()):
        raise ValueError(f"Path traversal rejected: {filename!r}")
    filepath.write_bytes(data)
    return str(filepath)


def cleanup_document_cache(max_age_hours: int = 24) -> int:
    """
    删除超过 *max_age_hours* 小时的缓存文档。

    返回被删除的文件数量。
    """
    import time

    cache_dir = get_document_cache_dir()
    cutoff = time.time() - (max_age_hours * 3600)
    removed = 0
    for f in cache_dir.iterdir():
        if f.is_file() and f.stat().st_mtime < cutoff:
            try:
                f.unlink()
                removed += 1
            except OSError:
                pass
    return removed


class MessageType(Enum):
    """入站消息的类型枚举。"""
    TEXT = "text"
    LOCATION = "location"
    PHOTO = "photo"
    VIDEO = "video"
    AUDIO = "audio"
    VOICE = "voice"
    DOCUMENT = "document"
    STICKER = "sticker"
    COMMAND = "command"  # /command 风格的命令消息


class ProcessingOutcome(Enum):
    """消息处理生命周期钩子的结果分类。"""

    SUCCESS = "success"
    FAILURE = "failure"
    CANCELLED = "cancelled"


@dataclass
class MessageEvent:
    """
    来自平台的入站消息。

    所有适配器统一生成的标准化消息表示。
    """
    # 消息内容
    text: str
    message_type: MessageType = MessageType.TEXT

    # 消息来源信息
    source: SessionSource = None

    # 原始平台数据
    raw_message: Any = None
    message_id: Optional[str] = None

    # 媒体附件
    # media_urls: 本地文件路径（供视觉工具访问）
    media_urls: List[str] = field(default_factory=list)
    media_types: List[str] = field(default_factory=list)

    # 回复上下文
    reply_to_message_id: Optional[str] = None
    reply_to_text: Optional[str] = None  # 被回复消息的文本（用于上下文注入）

    # 自动加载的技能，用于主题/频道绑定（例如 Telegram DM 主题、
    # Discord channel_skill_bindings）。可以是单个名称或有序列表。
    auto_skill: Optional[str | list[str]] = None

    # 每频道的临时系统提示词（例如 Discord channel_prompts）。
    # 在 API 调用时应用，不会持久化到对话历史记录中。
    channel_prompt: Optional[str] = None

    # 内部标志 — 用于合成事件（如后台进程完成通知），
    # 这些事件必须绕过用户授权检查。
    internal: bool = False

    # 时间戳
    timestamp: datetime = field(default_factory=datetime.now)
    
    def is_command(self) -> bool:
        """检查是否为命令消息（例如 /new、/reset）。"""
        return self.text.startswith("/")
    
    def get_command(self) -> Optional[str]:
        """如果是命令消息，提取命令名称。"""
        if not self.is_command():
            return None
        # 按空格分割，取第一个单词，去掉 /
        parts = self.text.split(maxsplit=1)
        raw = parts[0][1:].lower() if parts else None
        if raw and "@" in raw:
            raw = raw.split("@", 1)[0]
        # 拒绝文件路径：合法命令名中不包含 /
        if raw and "/" in raw:
            return None
        return raw
    
    def get_command_args(self) -> str:
        """获取命令之后的参数文本。"""
        if not self.is_command():
            return self.text
        parts = self.text.split(maxsplit=1)
        return parts[1] if len(parts) > 1 else ""


@dataclass 
class SendResult:
    """发送消息的结果。"""
    success: bool
    message_id: Optional[str] = None
    error: Optional[str] = None
    raw_response: Any = None
    retryable: bool = False  # 对于临时性连接错误为 True — 基类会自动重试


def merge_pending_message_event(
    pending_messages: Dict[str, MessageEvent],
    session_key: str,
    event: MessageEvent,
    *,
    merge_text: bool = False,
) -> None:
    """存储或合并某个会话的待处理事件。

    图片连拍/相册通常以多个近乎同时到达的 PHOTO 事件形式出现。
    将它们合并到已有的排队事件中，这样下一轮处理时能看到完整的连拍内容。

    当 ``merge_text`` 启用时，快速跟进的 TEXT 事件会被追加而非替换待处理轮次。
    这适用于 Telegram 的连续消息场景，避免用户的多条思考消息被静默截断为
    只保留最后排队的片段。
    """
    existing = pending_messages.get(session_key)
    if existing:
        existing_is_photo = getattr(existing, "message_type", None) == MessageType.PHOTO
        incoming_is_photo = event.message_type == MessageType.PHOTO
        existing_has_media = bool(existing.media_urls)
        incoming_has_media = bool(event.media_urls)

        if existing_is_photo and incoming_is_photo:
            existing.media_urls.extend(event.media_urls)
            existing.media_types.extend(event.media_types)
            if event.text:
                existing.text = BasePlatformAdapter._merge_caption(existing.text, event.text)
            return

        if existing_has_media or incoming_has_media:
            if incoming_has_media:
                existing.media_urls.extend(event.media_urls)
                existing.media_types.extend(event.media_types)
            if event.text:
                if existing.text:
                    existing.text = BasePlatformAdapter._merge_caption(existing.text, event.text)
                else:
                    existing.text = event.text
            if existing_is_photo or incoming_is_photo:
                existing.message_type = MessageType.PHOTO
            return

        if (
            merge_text
            and getattr(existing, "message_type", None) == MessageType.TEXT
            and event.message_type == MessageType.TEXT
        ):
            if event.text:
                existing.text = f"{existing.text}\n{event.text}" if existing.text else event.text
            return

    pending_messages[session_key] = event


# 表示临时性*连接*故障且值得重试的错误子串。
# "timeout" / "timed out" / "readtimeout" / "writetimeout" 被有意排除：
# 读/写超时意味着请求可能已到达服务器 — 重试非幂等调用（如 send_message）
# 有产生重复投递的风险。"connecttimeout" 是安全的，因为连接从未建立。
# 知道超时安全可重试的平台应显式设置 SendResult.retryable = True。
_RETRYABLE_ERROR_PATTERNS = (
    "connecterror",
    "connectionerror",
    "connectionreset",
    "connectionrefused",
    "connecttimeout",
    "network",
    "broken pipe",
    "remotedisconnected",
    "eoferror",
)


# 消息处理器类型定义
MessageHandler = Callable[[MessageEvent], Awaitable[Optional[str]]]


def resolve_channel_prompt(
    config_extra: dict,
    channel_id: str,
    parent_id: str | None = None,
) -> str | None:
    """从平台配置中解析每频道的临时提示词。

    在适配器的 ``config.extra`` 字典中查找 ``channel_prompts``。
    优先精确匹配 *channel_id*；回退到 *parent_id*（适用于论坛帖子/
    子频道继承父频道提示词的场景）。

    返回提示词字符串，或在未找到匹配时返回 None。空白/纯空格的
    提示词视为不存在。
    """
    prompts = config_extra.get("channel_prompts") or {}
    if not isinstance(prompts, dict):
        return None

    for key in (channel_id, parent_id):
        if not key:
            continue
        prompt = prompts.get(key)
        if prompt is None:
            continue
        prompt = str(prompt).strip()
        if prompt:
            return prompt
    return None


class BasePlatformAdapter(ABC):
    """
    平台适配器基类。

    子类实现各平台特定的逻辑：
    - 连接和身份验证
    - 接收消息
    - 发送消息/回复
    - 处理媒体文件
    """
    
    def __init__(self, config: PlatformConfig, platform: Platform):
        self.config = config
        self.platform = platform
        self._message_handler: Optional[MessageHandler] = None
        self._running = False
        self._fatal_error_code: Optional[str] = None
        self._fatal_error_message: Optional[str] = None
        self._fatal_error_retryable = True
        self._fatal_error_handler: Optional[Callable[["BasePlatformAdapter"], Awaitable[None] | None]] = None
        
        # 追踪每个会话的活跃消息处理器，用于中断支持
        # 键: session_key（例如 chat_id），值: (event, 用于中断的 asyncio.Event)
        self._active_sessions: Dict[str, asyncio.Event] = {}
        self._pending_messages: Dict[str, MessageEvent] = {}
        # 由 handle_message() 产生的后台消息处理任务。
        # 网关关闭时会取消这些任务，防止旧网关实例在 --replace
        # 或手动重启后继续处理任务。
        self._background_tasks: set[asyncio.Task] = set()
        # 在主响应投递完成后触发的一次性回调。
        # 按 session_key 索引。GatewayRunner 用此机制将后台审核
        # 通知（如 "💾 技能已创建"）延迟到主回复发送之后。
        self._post_delivery_callbacks: Dict[str, Callable] = {}
        self._expected_cancelled_tasks: set[asyncio.Task] = set()
        self._busy_session_handler: Optional[Callable[[MessageEvent, str], Awaitable[bool]]] = None
        # 已禁用语音输入自动 TTS 的聊天（通过 /voice off 设置）
        self._auto_tts_disabled_chats: set = set()
        # 打字指示器已暂停的聊天（例如在等待审批期间）。
        # _keep_typing 在 chat_id 位于此集合中时跳过 send_typing。
        self._typing_paused: set = set()

    @property
    def has_fatal_error(self) -> bool:
        return self._fatal_error_message is not None

    @property
    def fatal_error_message(self) -> Optional[str]:
        return self._fatal_error_message

    @property
    def fatal_error_code(self) -> Optional[str]:
        return self._fatal_error_code

    @property
    def fatal_error_retryable(self) -> bool:
        return self._fatal_error_retryable

    def set_fatal_error_handler(self, handler: Callable[["BasePlatformAdapter"], Awaitable[None] | None]) -> None:
        self._fatal_error_handler = handler

    def _mark_connected(self) -> None:
        self._running = True
        self._fatal_error_code = None
        self._fatal_error_message = None
        self._fatal_error_retryable = True
        try:
            from gateway.status import write_runtime_status
            write_runtime_status(platform=self.platform.value, platform_state="connected", error_code=None, error_message=None)
        except Exception:
            pass

    def _mark_disconnected(self) -> None:
        self._running = False
        if self.has_fatal_error:
            return
        try:
            from gateway.status import write_runtime_status
            write_runtime_status(platform=self.platform.value, platform_state="disconnected", error_code=None, error_message=None)
        except Exception:
            pass

    def _set_fatal_error(self, code: str, message: str, *, retryable: bool) -> None:
        self._running = False
        self._fatal_error_code = code
        self._fatal_error_message = message
        self._fatal_error_retryable = retryable
        try:
            from gateway.status import write_runtime_status
            write_runtime_status(
                platform=self.platform.value,
                platform_state="fatal",
                error_code=code,
                error_message=message,
            )
        except Exception:
            pass

    async def _notify_fatal_error(self) -> None:
        handler = self._fatal_error_handler
        if not handler:
            return
        result = handler(self)
        if asyncio.iscoroutine(result):
            await result

    def _acquire_platform_lock(self, scope: str, identity: str, resource_desc: str) -> bool:
        """为此适配器获取作用域锁。成功返回 True。"""
        from gateway.status import acquire_scoped_lock
        self._platform_lock_scope = scope
        self._platform_lock_identity = identity
        acquired, existing = acquire_scoped_lock(
            scope, identity, metadata={'platform': self.platform.value}
        )
        if acquired:
            return True
        owner_pid = existing.get('pid') if isinstance(existing, dict) else None
        message = (
            f'{resource_desc} already in use'
            + (f' (PID {owner_pid})' if owner_pid else '')
            + '. Stop the other gateway first.'
        )
        logger.error('[%s] %s', self.name, message)
        self._set_fatal_error(f'{scope}_lock', message, retryable=False)
        return False

    def _release_platform_lock(self) -> None:
        """释放由 _acquire_platform_lock 获取的作用域锁。"""
        identity = getattr(self, '_platform_lock_identity', None)
        if not identity:
            return
        from gateway.status import release_scoped_lock
        release_scoped_lock(self._platform_lock_scope, identity)
        self._platform_lock_identity = None

    @property
    def name(self) -> str:
        """本适配器的可读名称。"""
        return self.platform.value.title()
    
    @property
    def is_connected(self) -> bool:
        """检查适配器是否当前已连接。"""
        return self._running
    
    def set_message_handler(self, handler: MessageHandler) -> None:
        """
        设置入站消息的处理器。

        处理器接收一个 MessageEvent，应返回可选的响应字符串。
        """
        self._message_handler = handler

    def set_busy_session_handler(self, handler: Optional[Callable[[MessageEvent, str], Awaitable[bool]]]) -> None:
        """设置可选的处理器，用于处理会话活跃期间到达的消息。"""
        self._busy_session_handler = handler
    
    def set_session_store(self, session_store: Any) -> None:
        """
        设置会话存储，用于检查活跃会话。

        适配器在处理消息前需要检查某个线程/对话是否有活跃会话时使用
        （例如 Slack 线程回复未显式 @ 提及的场景）。
        """
        self._session_store = session_store
    
    @abstractmethod
    async def connect(self) -> bool:
        """
        连接平台并开始接收消息。

        连接成功返回 True。
        """
        pass
    
    @abstractmethod
    async def disconnect(self) -> None:
        """断开与平台的连接。"""
        pass
    
    @abstractmethod
    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> SendResult:
        """
        向指定聊天发送消息。

        参数：
            chat_id: 目标聊天/频道 ID
            content: 消息内容（可能是 Markdown 格式）
            reply_to: 可选的回复目标消息 ID
            metadata: 附加的平台特定选项

        返回：
            包含发送状态和消息 ID 的 SendResult
        """
        pass

    async def edit_message(
        self,
        chat_id: str,
        message_id: str,
        content: str,
    ) -> SendResult:
        """
        编辑之前发送的消息。可选方法 — 不支持编辑的平台返回
        success=False，调用方回退到发送新消息。
        """
        return SendResult(success=False, error="Not supported")

    async def send_typing(self, chat_id: str, metadata=None) -> None:
        """
        发送打字指示器。

        如果平台支持此功能，在子类中重写。
        metadata: 可选字典，包含平台特定上下文（例如 Slack 的 thread_id）。
        """
        pass

    async def stop_typing(self, chat_id: str) -> None:
        """停止持久打字指示器（如果平台使用的话）。

        在使用后台打字循环的子类中重写。
        对于使用单次打字指示器的平台，默认为空操作。
        """
        pass
    
    async def send_image(
        self,
        chat_id: str,
        image_url: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """
        通过平台 API 原生发送图片。

        在子类中重写以将图片作为原生附件发送，
        而非纯文本 URL。默认回退为发送 URL 文本消息。
        """
        # 回退方案：以文本形式发送 URL（子类重写以实现原生图片发送）
        text = f"{caption}\n{image_url}" if caption else image_url
        return await self.send(chat_id=chat_id, content=text, reply_to=reply_to)
    
    async def send_animation(
        self,
        chat_id: str,
        animation_url: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """
        通过平台 API 原生发送 GIF 动画。

        在子类中重写以将 GIF 作为动画发送（例如 Telegram 的 send_animation），
        使其在聊天中自动内联播放。默认回退到 send_image。
        """
        return await self.send_image(chat_id=chat_id, image_url=animation_url, caption=caption, reply_to=reply_to, metadata=metadata)
    
    @staticmethod
    def _is_animation_url(url: str) -> bool:
        """检查 URL 是否指向 GIF 动画（而非静态图片）。"""
        lower = url.lower().split('?')[0]  # 去除查询参数
        return lower.endswith('.gif')

    @staticmethod
    def extract_images(content: str) -> Tuple[List[Tuple[str, str]], str]:
        """
        从响应中的 Markdown 和 HTML 图片标签中提取图片 URL。

        匹配的模式：
        - ![替代文本](https://example.com/image.png)
        - <img src="https://example.com/image.png">
        - <img src="https://example.com/image.png"></img>

        参数：
            content: 要扫描的响应文本。

        返回：
            元组 ([(url, alt_text), ...] 列表, 移除图片标签后的清理文本)。
        """
        images = []
        cleaned = content
        
        # 匹配 Markdown 图片：![alt](url)
        md_pattern = r'!\[([^\]]*)\]\((https?://[^\s\)]+)\)'
        for match in re.finditer(md_pattern, content):
            alt_text = match.group(1)
            url = match.group(2)
            # 仅提取看起来确实是图片的 URL
            if any(url.lower().endswith(ext) or ext in url.lower() for ext in
                   ['.png', '.jpg', '.jpeg', '.gif', '.webp', 'fal.media', 'fal-cdn', 'replicate.delivery']):
                images.append((url, alt_text))
        
        # 匹配 HTML img 标签：<img src="url"> 或 <img src="url"></img> 或 <img src="url"/>
        html_pattern = r'<img\s+src=["\']?(https?://[^\s"\'<>]+)["\']?\s*/?>\s*(?:</img>)?'
        for match in re.finditer(html_pattern, content):
            url = match.group(1)
            images.append((url, ""))
        
        # 仅移除已匹配的图片标签（不是所有 Markdown 图片）
        if images:
            extracted_urls = {url for url, _ in images}
            def _remove_if_extracted(match):
                url = match.group(2) if match.lastindex >= 2 else match.group(1)
                return '' if url in extracted_urls else match.group(0)
            cleaned = re.sub(md_pattern, _remove_if_extracted, cleaned)
            cleaned = re.sub(html_pattern, _remove_if_extracted, cleaned)
            # 清理残留的多余空行
            cleaned = re.sub(r'\n{3,}', '\n\n', cleaned).strip()
        
        return images, cleaned
    
    async def send_voice(
        self,
        chat_id: str,
        audio_path: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        **kwargs,
    ) -> SendResult:
        """
        通过平台 API 原生发送音频文件作为语音消息。

        在子类中重写以发送语音气泡（Telegram）或文件附件（Discord）。
        默认回退为发送文件路径文本。
        """
        text = f"🔊 Audio: {audio_path}"
        if caption:
            text = f"{caption}\n{text}"
        return await self.send(chat_id=chat_id, content=text, reply_to=reply_to)

    async def play_tts(
        self,
        chat_id: str,
        audio_path: str,
        **kwargs,
    ) -> SendResult:
        """
        播放语音回复的自动 TTS 音频。

        在子类中重写以实现隐式播放（例如 Web UI）。
        默认回退到 send_voice（显示音频播放器）。
        """
        return await self.send_voice(chat_id=chat_id, audio_path=audio_path, **kwargs)

    async def send_video(
        self,
        chat_id: str,
        video_path: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        **kwargs,
    ) -> SendResult:
        """
        通过平台 API 原生发送视频。

        在子类中重写以发送可内联播放的视频媒体。
        默认回退为发送文件路径文本。
        """
        text = f"🎬 Video: {video_path}"
        if caption:
            text = f"{caption}\n{text}"
        return await self.send(chat_id=chat_id, content=text, reply_to=reply_to)

    async def send_document(
        self,
        chat_id: str,
        file_path: str,
        caption: Optional[str] = None,
        file_name: Optional[str] = None,
        reply_to: Optional[str] = None,
        **kwargs,
    ) -> SendResult:
        """
        通过平台 API 原生发送文档/文件。

        在子类中重写以发送可下载的文件附件。
        默认回退为发送文件路径文本。
        """
        text = f"📎 File: {file_path}"
        if caption:
            text = f"{caption}\n{text}"
        return await self.send(chat_id=chat_id, content=text, reply_to=reply_to)

    async def send_image_file(
        self,
        chat_id: str,
        image_path: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        **kwargs,
    ) -> SendResult:
        """
        通过平台 API 原生发送本地图片文件。

        与接收 URL 的 send_image() 不同，本方法接收本地文件路径。
        在子类中重写以实现原生图片附件功能。
        默认回退为发送文件路径文本。
        """
        text = f"🖼️ Image: {image_path}"
        if caption:
            text = f"{caption}\n{text}"
        return await self.send(chat_id=chat_id, content=text, reply_to=reply_to)

    @staticmethod
    def extract_media(content: str) -> Tuple[List[Tuple[str, bool]], str]:
        """
        从响应文本中提取 MEDIA:<path> 标签和 [[audio_as_voice]] 指令。

        TTS 工具返回的响应格式如：
            [[audio_as_voice]]
            MEDIA:/path/to/audio.ogg

        参数：
            content: 要扫描的响应文本。

        返回：
            元组 ([(path, is_voice), ...] 列表, 移除标签后的清理文本)。
        """
        media = []
        cleaned = content
        
        # 检查 [[audio_as_voice]] 指令
        has_voice_tag = "[[audio_as_voice]]" in content
        cleaned = cleaned.replace("[[audio_as_voice]]", "")
        
        # 提取 MEDIA:<path> 标签，允许冒号后有可选空格，
        # 以及引号/反引号包裹的路径（用于 LLM 格式化输出）。
        media_pattern = re.compile(
            r'''[`"']?MEDIA:\s*(?P<path>`[^`\n]+`|"[^"\n]+"|'[^'\n]+'|(?:~/|/)\S+(?:[^\S\n]+\S+)*?\.(?:png|jpe?g|gif|webp|mp4|mov|avi|mkv|webm|ogg|opus|mp3|wav|m4a)(?=[\s`"',;:)\]}]|$)|\S+)[`"']?'''
        )
        for match in media_pattern.finditer(content):
            path = match.group("path").strip()
            if len(path) >= 2 and path[0] == path[-1] and path[0] in "`\"'":
                path = path[1:-1].strip()
            path = path.lstrip("`\"'").rstrip("`\"',.;:)}]")
            if path:
                media.append((os.path.expanduser(path), has_voice_tag))

        # 从内容中移除 MEDIA 标签（包括外层的引号/反引号包装）
        if media:
            cleaned = media_pattern.sub('', cleaned)
            cleaned = re.sub(r'\n{3,}', '\n\n', cleaned).strip()
        
        return media, cleaned

    @staticmethod
    def extract_local_files(content: str) -> Tuple[List[str], str]:
        """
        检测响应文本中裸露的本地文件路径，用于原生媒体投递。

        匹配以 /... 或 ~/ 开头、以常见图片或视频扩展名结尾的绝对路径。
        使用 ``os.path.isfile()`` 验证每个候选路径，避免对 URL 或
        不存在的路径产生误判。

        围栏代码块（``` ... ```）和行内代码（`...`）中的路径会被忽略，
        确保代码示例不会被错误截取。

        返回：
            元组 (展开后的文件路径列表, 移除路径字符串后的清理文本)。
        """
        _LOCAL_MEDIA_EXTS = (
            '.png', '.jpg', '.jpeg', '.gif', '.webp',
            '.mp4', '.mov', '.avi', '.mkv', '.webm',
        )
        ext_part = '|'.join(e.lstrip('.') for e in _LOCAL_MEDIA_EXTS)

        # (?<![/:\w.]) 防止匹配 URL 内部（如 https://…/img.png）
        #             和相对路径（./foo.png）
        # (?:~/|/)    锚定到绝对路径或 home 相对路径
        path_re = re.compile(
            r'(?<![/:\w.])(?:~/|/)(?:[\w.\-]+/)*[\w.\-]+\.(?:' + ext_part + r')\b',
            re.IGNORECASE,
        )

        # 构建围栏代码块和行内代码覆盖的区间
        code_spans: list = []
        for m in re.finditer(r'```[^\n]*\n.*?```', content, re.DOTALL):
            code_spans.append((m.start(), m.end()))
        for m in re.finditer(r'`[^`\n]+`', content):
            code_spans.append((m.start(), m.end()))

        def _in_code(pos: int) -> bool:
            return any(s <= pos < e for s, e in code_spans)

        found: list = []  # (raw_match_text, expanded_path)
        for match in path_re.finditer(content):
            if _in_code(match.start()):
                continue
            raw = match.group(0)
            expanded = os.path.expanduser(raw)
            if os.path.isfile(expanded):
                found.append((raw, expanded))

        # 按展开后的路径去重，保持发现顺序
        seen: set = set()
        unique: list = []
        for raw, expanded in found:
            if expanded not in seen:
                seen.add(expanded)
                unique.append((raw, expanded))

        paths = [expanded for _, expanded in unique]

        cleaned = content
        if unique:
            for raw, _exp in unique:
                cleaned = cleaned.replace(raw, '')
            cleaned = re.sub(r'\n{3,}', '\n\n', cleaned).strip()

        return paths, cleaned

    async def _keep_typing(self, chat_id: str, interval: float = 2.0, metadata=None) -> None:
        """
        持续发送打字指示器直到被取消。

        Telegram/Discord 的打字状态约 5 秒后过期，因此每 2 秒刷新一次，
        以便在进度消息中断后快速恢复打字状态。

        当聊天位于 ``_typing_paused`` 集合中时跳过 send_typing（例如
        Agent 等待危险命令审批期间）。这对 Slack 的 Assistant API 至关重要，
        其中 ``assistant_threads_setStatus`` 会禁用输入框 — 暂停打字
        可以让用户输入 ``/approve`` 或 ``/deny``。
        """
        try:
            while True:
                if chat_id not in self._typing_paused:
                    await self.send_typing(chat_id, metadata=metadata)
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            pass  # 处理器完成时的正常取消
        finally:
            # 确保底层平台的打字循环也被停止。
            # _keep_typing 可能在外部 stop_typing() 清空任务字典之后
            # 又调用了 send_typing()，导致循环被重新创建。
            # 仅取消 _keep_typing 本身无法清理这种情况。
            if hasattr(self, "stop_typing"):
                try:
                    await self.stop_typing(chat_id)
                except Exception:
                    pass
            self._typing_paused.discard(chat_id)

    def pause_typing_for_chat(self, chat_id: str) -> None:
        """暂停某个聊天的打字指示器（例如在等待审批期间）。

        线程安全（CPython GIL）— 可以从同步 Agent 线程调用，
        同时 ``_keep_typing`` 在异步事件循环上运行。
        """
        self._typing_paused.add(chat_id)

    def resume_typing_for_chat(self, chat_id: str) -> None:
        """审批解决后恢复某个聊天的打字指示器。"""
        self._typing_paused.discard(chat_id)

    # ── 处理生命周期钩子 ──────────────────────────────────────────
    # 子类重写这些方法以响应消息处理事件
    # （例如 Discord 添加 👀/✅/❌ 表情反应）。

    async def on_processing_start(self, event: MessageEvent) -> None:
        """后台处理开始时调用的钩子。"""

    async def on_processing_complete(self, event: MessageEvent, outcome: ProcessingOutcome) -> None:
        """后台处理完成时调用的钩子。"""

    async def _run_processing_hook(self, hook_name: str, *args: Any, **kwargs: Any) -> None:
        """运行生命周期钩子，不让失败中断消息处理流程。"""
        hook = getattr(self, hook_name, None)
        if not callable(hook):
            return
        try:
            await hook(*args, **kwargs)
        except Exception as e:
            logger.warning("[%s] %s hook failed: %s", self.name, hook_name, e)

    @staticmethod
    def _is_retryable_error(error: Optional[str]) -> bool:
        """如果错误字符串看起来像临时性网络故障，返回 True。"""
        if not error:
            return False
        lowered = error.lower()
        return any(pat in lowered for pat in _RETRYABLE_ERROR_PATTERNS)

    @staticmethod
    def _is_timeout_error(error: Optional[str]) -> bool:
        """如果错误字符串表示读/写超时，返回 True。

        超时错误不可重试，也不应触发纯文本回退 — 请求可能已经被投递。
        """
        if not error:
            return False
        lowered = error.lower()
        return "timed out" in lowered or "readtimeout" in lowered or "writetimeout" in lowered

    async def _send_with_retry(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Any = None,
        max_retries: int = 2,
        base_delay: float = 2.0,
    ) -> "SendResult":
        """
        带自动重试的消息发送，用于临时性网络错误。

        对于永久性失败（如格式/权限错误），在放弃前会尝试回退到纯文本版本。
        如果所有尝试均因网络错误失败，会向用户发送简短的投递失败通知，
        让他们知道需要重试而非无限等待。
        """

        result = await self.send(
            chat_id=chat_id,
            content=content,
            reply_to=reply_to,
            metadata=metadata,
        )

        if result.success:
            return result

        error_str = result.error or ""
        is_network = result.retryable or self._is_retryable_error(error_str)

        # 超时错误不适合重试（消息可能已投递），
        # 也不是格式错误 — 直接返回失败结果。
        if not is_network and self._is_timeout_error(error_str):
            return result

        if is_network:
            # 对临时性错误使用指数退避重试
            for attempt in range(1, max_retries + 1):
                delay = base_delay * (2 ** (attempt - 1)) + random.uniform(0, 1)
                logger.warning(
                    "[%s] Send failed (attempt %d/%d, retrying in %.1fs): %s",
                    self.name, attempt, max_retries, delay, error_str,
                )
                await asyncio.sleep(delay)
                result = await self.send(
                    chat_id=chat_id,
                    content=content,
                    reply_to=reply_to,
                    metadata=metadata,
                )
                if result.success:
                    logger.info("[%s] Send succeeded on retry %d", self.name, attempt)
                    return result
                error_str = result.error or ""
                if not (result.retryable or self._is_retryable_error(error_str)):
                    break  # 错误类型变为非临时性 — 跳出重试循环，走纯文本回退
            else:
                # 所有重试耗尽（循环正常完成，未 break）— 通知用户
                logger.error("[%s] Failed to deliver response after %d retries: %s", self.name, max_retries, error_str)
                notice = (
                    "\u26a0\ufe0f Message delivery failed after multiple attempts. "
                    "Please try again \u2014 your request was processed but the response could not be sent."
                )
                try:
                    await self.send(chat_id=chat_id, content=notice, reply_to=reply_to, metadata=metadata)
                except Exception as notify_err:
                    logger.debug("[%s] Could not send delivery-failure notice: %s", self.name, notify_err)
                return result

        # 非网络错误 / 重试后的格式失败：尝试纯文本回退
        logger.warning("[%s] Send failed: %s — trying plain-text fallback", self.name, error_str)
        fallback_result = await self.send(
            chat_id=chat_id,
            content=f"(Response formatting failed, plain text:)\n\n{content[:3500]}",
            reply_to=reply_to,
            metadata=metadata,
        )
        if not fallback_result.success:
            logger.error("[%s] Fallback send also failed: %s", self.name, fallback_result.error)
        return fallback_result

    @staticmethod
    def _merge_caption(existing_text: Optional[str], new_text: str) -> str:
        """将新的标题文本合并到已有文本中，避免重复。

        使用逐行精确匹配（非子串匹配）以防止误判 —
        例如较短的标题因出现在较长标题的子串中而被静默丢弃
        （如 "Meeting" 出现在 "Meeting agenda" 中）。
        比较时会规范化空白字符。
        """
        if not existing_text:
            return new_text
        existing_captions = [c.strip() for c in existing_text.split("\n\n")]
        if new_text.strip() not in existing_captions:
            return f"{existing_text}\n\n{new_text}".strip()
        return existing_text

    async def handle_message(self, event: MessageEvent) -> None:
        """
        处理入站消息。

        本方法通过产生后台任务来快速返回。
        这使得在 Agent 运行期间仍可处理新消息，从而支持中断功能。
        """
        if not self._message_handler:
            return
        
        session_key = build_session_key(
            event.source,
            group_sessions_per_user=self.config.extra.get("group_sessions_per_user", True),
            thread_sessions_per_user=self.config.extra.get("thread_sessions_per_user", False),
        )
        
        # 检查此会话是否已有活跃的处理器
        if session_key in self._active_sessions:
            # 某些命令必须绕过活跃会话守卫，直接分发给网关运行器。
            # 否则它们会被排队为待处理消息，并且：
            #   - 以用户文本形式泄漏到对话中（/stop、/new），或
            #   - 导致死锁（/approve、/deny — Agent 正在阻塞于 Event.wait）
            #
            # 内联分发：直接调用消息处理器并发送响应。
            # 不要使用 _process_message_background — 它管理会话生命周期，
            # 其清理操作会与正在运行的任务产生竞态（见 PR #4926）。
            cmd = event.get_command()
            if cmd in ("approve", "deny", "status", "stop", "new", "reset", "background", "restart", "queue", "q"):
                logger.debug(
                    "[%s] Command '/%s' bypassing active-session guard for %s",
                    self.name, cmd, session_key,
                )
                try:
                    _thread_meta = {"thread_id": event.source.thread_id} if event.source.thread_id else None
                    response = await self._message_handler(event)
                    if response:
                        await self._send_with_retry(
                            chat_id=event.source.chat_id,
                            content=response,
                            reply_to=event.message_id,
                            metadata=_thread_meta,
                        )
                except Exception as e:
                    logger.error("[%s] Command '/%s' dispatch failed: %s", self.name, cmd, e, exc_info=True)
                return

            if self._busy_session_handler is not None:
                try:
                    if await self._busy_session_handler(event, session_key):
                        return
                except Exception as e:
                    logger.error("[%s] Busy-session handler failed: %s", self.name, e, exc_info=True)

            # 特殊处理：图片连拍/相册经常以多个近乎同时的消息到达。
            # 将它们排队而不中断活跃的运行，当前任务完成后立即处理。
            if event.message_type == MessageType.PHOTO:
                logger.debug("[%s] Queuing photo follow-up for session %s without interrupt", self.name, session_key)
                merge_pending_message_event(self._pending_messages, session_key, event)
                return  # 现在不处理中断 - 当前任务完成后会处理

            # 非图片跟进消息的默认行为：中断正在运行的 Agent
            logger.debug("[%s] New message while session %s is active — triggering interrupt", self.name, session_key)
            self._pending_messages[session_key] = event
            # 发送中断信号（处理任务会检查这个事件）
            self._active_sessions[session_key].set()
            return  # 现在不处理 - 当前任务完成后会处理

        # 在产生后台任务之前标记会话为活跃，以关闭竞态窗口 —
        # 如果第二条消息在任务启动前到达，也会通过 _active_sessions
        # 检查，从而产生重复任务。（grammY sequentialize /
        # aiogram EventIsolation 模式 — 同步设置守卫，而非在任务内部设置。）
        self._active_sessions[session_key] = asyncio.Event()

        # 产生后台任务处理此消息
        task = asyncio.create_task(self._process_message_background(event, session_key))
        try:
            self._background_tasks.add(task)
        except TypeError:
            # 某些测试用轻量级哨兵桩替代 create_task()，
            # 这些对象不可哈希且不支持生命周期回调。
            return
        if hasattr(task, "add_done_callback"):
            task.add_done_callback(self._background_tasks.discard)
            task.add_done_callback(self._expected_cancelled_tasks.discard)
    
    @staticmethod
    def _get_human_delay() -> float:
        """
        返回随机延迟秒数，用于模拟人类回复节奏。

        通过环境变量配置：
          HERMES_HUMAN_DELAY_MODE: "off"（默认）| "natural" | "custom"
          HERMES_HUMAN_DELAY_MIN_MS: 最小延迟毫秒数（默认 800，custom 模式）
          HERMES_HUMAN_DELAY_MAX_MS: 最大延迟毫秒数（默认 2500，custom 模式）
        """
        import random

        mode = os.getenv("HERMES_HUMAN_DELAY_MODE", "off").lower()
        if mode == "off":
            return 0.0
        min_ms = int(os.getenv("HERMES_HUMAN_DELAY_MIN_MS", "800"))
        max_ms = int(os.getenv("HERMES_HUMAN_DELAY_MAX_MS", "2500"))
        if mode == "natural":
            min_ms, max_ms = 800, 2500
        return random.uniform(min_ms / 1000.0, max_ms / 1000.0)

    async def _process_message_background(self, event: MessageEvent, session_key: str) -> None:
        """实际处理消息的后台任务。"""
        # 跟踪投递结果，用于处理完成钩子
        delivery_attempted = False
        delivery_succeeded = False

        def _record_delivery(result):
            nonlocal delivery_attempted, delivery_succeeded
            if result is None:
                return
            delivery_attempted = True
            if getattr(result, "success", False):
                delivery_succeeded = True

        # 复用 handle_message() 设置的中断事件（它在产生本任务之前
        # 标记了会话为活跃以防止竞态）。
        # 仅在条目被外部移除时回退到新建 Event。
        interrupt_event = self._active_sessions.get(session_key) or asyncio.Event()
        self._active_sessions[session_key] = interrupt_event
        
        # 启动持续打字指示器（每 2 秒刷新一次）
        _thread_metadata = {"thread_id": event.source.thread_id} if event.source.thread_id else None
        typing_task = asyncio.create_task(self._keep_typing(event.source.chat_id, metadata=_thread_metadata))
        
        try:
            await self._run_processing_hook("on_processing_start", event)

            # 调用处理器（工具调用可能需要较长时间）
            response = await self._message_handler(event)
            
            # 有响应则发送。None/空响应在流式传输已投递文本时
            # （already_sent=True）或消息被排队到活跃 Agent 后面时是正常的。
            # 使用 DEBUG 级别日志以避免对预期行为产生噪音警告。
            #
            # 当会话被新消息中断且待处理消息尚未被消费时，抑制过时响应。
            # 待处理消息由下面的待处理消息处理器处理（#8221/#2483）。
            if (
                response
                and interrupt_event.is_set()
                and session_key in self._pending_messages
            ):
                logger.info(
                    "[%s] Suppressing stale response for interrupted session %s",
                    self.name,
                    session_key,
                )
                response = None
            if not response:
                logger.debug("[%s] Handler returned empty/None response for %s", self.name, event.source.chat_id)
            if response:
                # 从 TTS 工具提取 MEDIA:<path> 标签，在其他处理之前
                media_files, response = self.extract_media(response)
                
                # 提取图片 URL 并作为原生平台附件发送
                images, text_content = self.extract_images(response)
                # 从消息体中清除所有残留的内部指令（修复 #1561）
                text_content = text_content.replace("[[audio_as_voice]]", "").strip()
                text_content = re.sub(r"MEDIA:\s*\S+", "", text_content).strip()
                if images:
                    logger.info("[%s] extract_images found %d image(s) in response (%d chars)", self.name, len(images), len(response))

                # 自动检测裸露的本地文件路径，用于原生媒体投递
                # （帮助不使用 MEDIA: 语法的小模型）
                local_files, text_content = self.extract_local_files(text_content)
                if local_files:
                    logger.info("[%s] extract_local_files found %d file(s) in response", self.name, len(local_files))
                
                # 自动 TTS：如果是语音消息，先生成音频（在发送文本之前）
                # 当聊天已禁用语音模式（/voice off）时跳过
                _tts_path = None
                if (event.message_type == MessageType.VOICE
                        and text_content
                        and not media_files
                        and event.source.chat_id not in self._auto_tts_disabled_chats):
                    try:
                        from tools.tts_tool import text_to_speech_tool, check_tts_requirements
                        if check_tts_requirements():
                            import json as _json
                            speech_text = re.sub(r'[*_`#\[\]()]', '', text_content)[:4000].strip()
                            if not speech_text:
                                raise ValueError("Empty text after markdown cleanup")
                            tts_result_str = await asyncio.to_thread(
                                text_to_speech_tool, text=speech_text
                            )
                            tts_data = _json.loads(tts_result_str)
                            _tts_path = tts_data.get("file_path")
                    except Exception as tts_err:
                        logger.warning("[%s] Auto-TTS failed: %s", self.name, tts_err)

                # 在文本之前播放 TTS 音频（语音优先体验）
                if _tts_path and Path(_tts_path).exists():
                    try:
                        await self.play_tts(
                            chat_id=event.source.chat_id,
                            audio_path=_tts_path,
                            metadata=_thread_metadata,
                        )
                    finally:
                        try:
                            os.remove(_tts_path)
                        except OSError:
                            pass

                # 发送文本部分
                if text_content:
                    logger.info("[%s] Sending response (%d chars) to %s", self.name, len(text_content), event.source.chat_id)
                    result = await self._send_with_retry(
                        chat_id=event.source.chat_id,
                        content=text_content,
                        reply_to=event.message_id,
                        metadata=_thread_metadata,
                    )
                    _record_delivery(result)

                # 文本和媒体之间的类人节奏延迟
                human_delay = self._get_human_delay()

                # 将提取的图片作为原生附件发送
                if images:
                    logger.info("[%s] Extracted %d image(s) to send as attachments", self.name, len(images))
                for image_url, alt_text in images:
                    if human_delay > 0:
                        await asyncio.sleep(human_delay)
                    try:
                        logger.info(
                            "[%s] Sending image: %s (alt=%s)",
                            self.name,
                            safe_url_for_log(image_url),
                            alt_text[:30] if alt_text else "",
                        )
                        # 将 GIF 动画路由到 send_animation 以实现正确播放
                        if self._is_animation_url(image_url):
                            img_result = await self.send_animation(
                                chat_id=event.source.chat_id,
                                animation_url=image_url,
                                caption=alt_text if alt_text else None,
                                metadata=_thread_metadata,
                            )
                        else:
                            img_result = await self.send_image(
                                chat_id=event.source.chat_id,
                                image_url=image_url,
                                caption=alt_text if alt_text else None,
                                metadata=_thread_metadata,
                            )
                        if not img_result.success:
                            logger.error("[%s] Failed to send image: %s", self.name, img_result.error)
                    except Exception as img_err:
                        logger.error("[%s] Error sending image: %s", self.name, img_err, exc_info=True)

                # 发送提取的媒体文件 — 根据文件类型路由
                _AUDIO_EXTS = {'.ogg', '.opus', '.mp3', '.wav', '.m4a'}
                _VIDEO_EXTS = {'.mp4', '.mov', '.avi', '.mkv', '.webm', '.3gp'}
                _IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.webp', '.gif'}

                for media_path, is_voice in media_files:
                    if human_delay > 0:
                        await asyncio.sleep(human_delay)
                    try:
                        ext = Path(media_path).suffix.lower()
                        if ext in _AUDIO_EXTS:
                            media_result = await self.send_voice(
                                chat_id=event.source.chat_id,
                                audio_path=media_path,
                                metadata=_thread_metadata,
                            )
                        elif ext in _VIDEO_EXTS:
                            media_result = await self.send_video(
                                chat_id=event.source.chat_id,
                                video_path=media_path,
                                metadata=_thread_metadata,
                            )
                        elif ext in _IMAGE_EXTS:
                            media_result = await self.send_image_file(
                                chat_id=event.source.chat_id,
                                image_path=media_path,
                                metadata=_thread_metadata,
                            )
                        else:
                            media_result = await self.send_document(
                                chat_id=event.source.chat_id,
                                file_path=media_path,
                                metadata=_thread_metadata,
                            )

                        if not media_result.success:
                            logger.warning("[%s] Failed to send media (%s): %s", self.name, ext, media_result.error)
                    except Exception as media_err:
                        logger.warning("[%s] Error sending media: %s", self.name, media_err)

                # 将自动检测到的本地文件作为原生附件发送
                for file_path in local_files:
                    if human_delay > 0:
                        await asyncio.sleep(human_delay)
                    try:
                        ext = Path(file_path).suffix.lower()
                        if ext in _IMAGE_EXTS:
                            await self.send_image_file(
                                chat_id=event.source.chat_id,
                                image_path=file_path,
                                metadata=_thread_metadata,
                            )
                        elif ext in _VIDEO_EXTS:
                            await self.send_video(
                                chat_id=event.source.chat_id,
                                video_path=file_path,
                                metadata=_thread_metadata,
                            )
                        else:
                            await self.send_document(
                                chat_id=event.source.chat_id,
                                file_path=file_path,
                                metadata=_thread_metadata,
                            )
                    except Exception as file_err:
                        logger.error("[%s] Error sending local file %s: %s", self.name, file_path, file_err)

            # 为处理钩子确定整体成功状态
            processing_ok = delivery_succeeded if delivery_attempted else not bool(response)
            await self._run_processing_hook(
                "on_processing_complete",
                event,
                ProcessingOutcome.SUCCESS if processing_ok else ProcessingOutcome.FAILURE,
            )

            # 检查在处理期间是否有排队的待处理消息
            if session_key in self._pending_messages:
                pending_event = self._pending_messages.pop(session_key)
                logger.debug("[%s] Processing queued message from interrupt", self.name)
                # 在处理待处理消息之前清理当前会话
                if session_key in self._active_sessions:
                    del self._active_sessions[session_key]
                typing_task.cancel()
                try:
                    await typing_task
                except asyncio.CancelledError:
                    pass
                # 在新的后台任务中处理待处理消息
                await self._process_message_background(pending_event, session_key)
                return  # 已完成清理
                
        except asyncio.CancelledError:
            current_task = asyncio.current_task()
            outcome = ProcessingOutcome.CANCELLED
            if current_task is None or current_task not in self._expected_cancelled_tasks:
                outcome = ProcessingOutcome.FAILURE
            await self._run_processing_hook("on_processing_complete", event, outcome)
            raise
        except Exception as e:
            await self._run_processing_hook("on_processing_complete", event, ProcessingOutcome.FAILURE)
            logger.error("[%s] Error handling message: %s", self.name, e, exc_info=True)
            # 向用户发送错误信息，避免他们陷入无回应的等待
            try:
                error_type = type(e).__name__
                error_detail = str(e)[:300] if str(e) else "no details available"
                _thread_metadata = {"thread_id": event.source.thread_id} if event.source.thread_id else None
                await self.send(
                    chat_id=event.source.chat_id,
                    content=(
                        f"Sorry, I encountered an error ({error_type}).\n"
                        f"{error_detail}\n"
                        "Try again or use /reset to start a fresh session."
                    ),
                    metadata=_thread_metadata,
                )
            except Exception:
                pass  # 最后手段 — 不让错误上报本身导致处理器崩溃
        finally:
            # 触发为此会话注册的一次性投递后回调
            # （例如延迟的后台审核通知）。
            _post_cb = getattr(self, "_post_delivery_callbacks", {}).pop(session_key, None)
            if callable(_post_cb):
                try:
                    _post_cb()
                except Exception:
                    pass
            # 停止打字指示器
            typing_task.cancel()
            try:
                await typing_task
            except asyncio.CancelledError:
                pass
            # 同时取消平台级别的持久打字任务（例如 Discord），
            # 这些任务可能在最后一次 stop_typing() 之后被 _keep_typing 重新创建
            try:
                if hasattr(self, "stop_typing"):
                    await self.stop_typing(event.source.chat_id)
            except Exception:
                pass
            # 清理会话跟踪
            if session_key in self._active_sessions:
                del self._active_sessions[session_key]
    
    async def cancel_background_tasks(self) -> None:
        """取消所有正在运行的后台消息处理任务。

        在网关关闭/替换时使用，确保旧进程中的活跃会话
        在适配器被拆除后不会继续运行。
        """
        tasks = [task for task in self._background_tasks if not task.done()]
        for task in tasks:
            self._expected_cancelled_tasks.add(task)
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._background_tasks.clear()
        self._expected_cancelled_tasks.clear()
        self._pending_messages.clear()
        self._active_sessions.clear()

    def has_pending_interrupt(self, session_key: str) -> bool:
        """检查某个会话是否有待处理的中断。"""
        return session_key in self._active_sessions and self._active_sessions[session_key].is_set()
    
    def get_pending_message(self, session_key: str) -> Optional[MessageEvent]:
        """获取并清除某个会话的待处理消息。"""
        return self._pending_messages.pop(session_key, None)
    
    def build_source(
        self,
        chat_id: str,
        chat_name: Optional[str] = None,
        chat_type: str = "dm",
        user_id: Optional[str] = None,
        user_name: Optional[str] = None,
        thread_id: Optional[str] = None,
        chat_topic: Optional[str] = None,
        user_id_alt: Optional[str] = None,
        chat_id_alt: Optional[str] = None,
    ) -> SessionSource:
        """为当前平台构建 SessionSource 的辅助方法。"""
        # 将空主题规范化为 None
        if chat_topic is not None and not chat_topic.strip():
            chat_topic = None
        return SessionSource(
            platform=self.platform,
            chat_id=str(chat_id),
            chat_name=chat_name,
            chat_type=chat_type,
            user_id=str(user_id) if user_id else None,
            user_name=user_name,
            thread_id=str(thread_id) if thread_id else None,
            chat_topic=chat_topic.strip() if chat_topic else None,
            user_id_alt=user_id_alt,
            chat_id_alt=chat_id_alt,
        )
    
    @abstractmethod
    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        """
        获取聊天/频道的信息。

        返回至少包含以下字段的字典：
        - name: 聊天名称
        - type: "dm"、"group"、"channel"
        """
        pass
    
    def format_message(self, content: str) -> str:
        """
        为当前平台格式化消息。

        在子类中重写以处理平台特定的格式
        （例如 Telegram MarkdownV2、Discord markdown）。

        默认实现原样返回内容。
        """
        return content
    
    @staticmethod
    def truncate_message(
        content: str,
        max_length: int = 4096,
        len_fn: Optional["Callable[[str], int]"] = None,
    ) -> List[str]:
        """
        将长消息分割为多个块，保留代码块边界。

        当分割点落在三反引号代码块内部时，会在当前块末尾关闭围栏，
        并在下一块开头重新打开（使用原始语言标签）。多块响应
        会添加如 ``(1/3)`` 的指示器。

        参数：
            content: 完整的消息内容
            max_length: 每块的最大长度（平台特定）
            len_fn: 可选的长度计算函数。默认为 ``len``（Unicode 码点）。
                     对于以 UTF-16 编码单元计量消息长度的平台（如 Telegram），
                     传入 ``utf16_len``。

        返回：
            消息块列表
        """
        _len = len_fn or len
        if _len(content) <= max_length:
            return [content]

        INDICATOR_RESERVE = 10   # 为 " (XX/XX)" 预留空间
        FENCE_CLOSE = "\n```"

        chunks: List[str] = []
        remaining = content
        # 当上一块在代码块中间结束时，此变量保存语言标签
        # （可能为 ""），以便重新打开围栏。
        carry_lang: Optional[str] = None

        while remaining:
            # 如果是从上一块延续的代码块，
            # 在前面添加带相同语言标签的新开始围栏。
            prefix = f"```{carry_lang}\n" if carry_lang is not None else ""

            # 在扣除前缀、可能的关闭围栏和块指示器后，
            # 还能容纳多少正文文本。
            headroom = max_length - INDICATOR_RESERVE - _len(prefix) - _len(FENCE_CLOSE)
            if headroom < 1:
                headroom = max_length // 2

            # 剩余内容能放进最后一个块
            if _len(prefix) + _len(remaining) <= max_length - INDICATOR_RESERVE:
                chunks.append(prefix + remaining)
                break

            # 找一个自然分割点（优先选换行符，其次选空格）。
            # 当 _len != len 时（例如 Telegram 的 utf16_len），headroom 的
            # 单位是自定义单位。我们需要码点级别的切片位置，使其在
            # 自定义单位的预算范围内。
            #
            # _safe_slice_pos() 将自定义单位预算映射为最大码点偏移，
            # 使得自定义长度 ≤ 预算。
            if _len is not len:
                # 将 headroom（自定义单位）映射为码点切片长度
                _cp_limit = _custom_unit_to_cp(remaining, headroom, _len)
            else:
                _cp_limit = headroom
            region = remaining[:_cp_limit]
            split_at = region.rfind("\n")
            if split_at < _cp_limit // 2:
                split_at = region.rfind(" ")
            if split_at < 1:
                split_at = _cp_limit

            # 避免在行内代码段（`...`）中间分割。
            # 如果 split_at 之前的文本有奇数个未转义反引号，
            # 则分割点落在行内代码内部 — 产生的块会有未配对的反引号，
            # 其中的特殊字符（如括号）会未被转义，
            # 导致 Telegram 的 MarkdownV2 解析错误。
            candidate = remaining[:split_at]
            backtick_count = candidate.count("`") - candidate.count("\\`")
            if backtick_count % 2 == 1:
                # Find the last unescaped backtick and split before it
                last_bt = candidate.rfind("`")
                while last_bt > 0 and candidate[last_bt - 1] == "\\":
                    last_bt = candidate.rfind("`", 0, last_bt)
                if last_bt > 0:
                    # 尝试在反引号之前找到一个空格或换行符
                    safe_split = candidate.rfind(" ", 0, last_bt)
                    nl_split = candidate.rfind("\n", 0, last_bt)
                    safe_split = max(safe_split, nl_split)
                    if safe_split > _cp_limit // 4:
                        split_at = safe_split

            chunk_body = remaining[:split_at]
            remaining = remaining[split_at:].lstrip()

            full_chunk = prefix + chunk_body

            # 仅遍历 chunk_body（不包括我们添加的前缀）来
            # 判断是否在一个未关闭的代码块中结束。
            in_code = carry_lang is not None
            lang = carry_lang or ""
            for line in chunk_body.split("\n"):
                stripped = line.strip()
                if stripped.startswith("```"):
                    if in_code:
                        in_code = False
                        lang = ""
                    else:
                        in_code = True
                        tag = stripped[3:].strip()
                        lang = tag.split()[0] if tag else ""

            if in_code:
                # 关闭孤立的围栏，使此块本身是合法的
                full_chunk += FENCE_CLOSE
                carry_lang = lang
            else:
                carry_lang = None

            chunks.append(full_chunk)

        # 当响应跨越多条消息时附加块指示器
        if len(chunks) > 1:
            total = len(chunks)
            chunks = [
                f"{chunk} ({i + 1}/{total})" for i, chunk in enumerate(chunks)
            ]

        return chunks
