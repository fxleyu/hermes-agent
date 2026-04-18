from __future__ import annotations

"""
Discord 平台适配器。

使用 discord.py 库实现：
- 从服务器和私聊接收消息
- 发送回复
- 处理线程和频道
"""

import asyncio
import logging
import os
import struct
import subprocess
import tempfile
import threading
import time
from collections import defaultdict
from typing import Callable, Dict, Optional, Any

logger = logging.getLogger(__name__)

VALID_THREAD_AUTO_ARCHIVE_MINUTES = {60, 1440, 4320, 10080}

try:
    import discord
    from discord import Message as DiscordMessage, Intents
    from discord.ext import commands
    DISCORD_AVAILABLE = True
except ImportError:
    DISCORD_AVAILABLE = False
    discord = None
    DiscordMessage = Any
    Intents = Any
    commands = None

import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))

from gateway.config import Platform, PlatformConfig
import re

from gateway.platforms.helpers import MessageDeduplicator, ThreadParticipationTracker
from gateway.platforms.base import (
    BasePlatformAdapter,
    MessageEvent,
    MessageType,
    ProcessingOutcome,
    SendResult,
    cache_image_from_url,
    cache_audio_from_url,
    cache_document_from_bytes,
    SUPPORTED_DOCUMENT_TYPES,
)
from tools.url_safety import is_safe_url


def _clean_discord_id(entry: str) -> str:
    """去除 Discord 用户 ID 或用户名条目中的常见前缀。

    用户有时会粘贴带有前缀的 ID，如 ``user:123``、``<@123>``
    或 ``<@!123>``，来自 Discord UI 或其他工具。这会将
    条目规范化为纯 ID 或用户名。
    """
    entry = entry.strip()
    # 去除 Discord 提及语法：<@123> 或 <@!123>
    if entry.startswith("<@") and entry.endswith(">"):
        entry = entry.lstrip("<@!").rstrip(">")
    # 去除 "user:" 前缀（见于某些 Discord 工具/入门粘贴）
    if entry.lower().startswith("user:"):
        entry = entry[5:]
    return entry.strip()


def check_discord_requirements() -> bool:
    """检查 Discord 依赖是否可用。"""
    return DISCORD_AVAILABLE


class VoiceReceiver:
    """捕获并解码来自 Discord 语音频道的语音音频。

    连接到 VoiceClient 的套接字监听器，解密 RTP 数据包
    （NaCl 传输 + DAVE E2EE），将 Opus 解码为 PCM，并缓冲
    每个用户的音频。轮询循环检测静音并通过回调
    传递完成的语句。
    """

    SILENCE_THRESHOLD = 1.5    # 静默秒数 -> 语句结束
    MIN_SPEECH_DURATION = 0.5  # minimum seconds to process (skip noise)
    SAMPLE_RATE = 48000        # Discord 原生采样率
    CHANNELS = 2               # Discord 发送立体声

    def __init__(self, voice_client, allowed_user_ids: set = None):
        self._vc = voice_client
        self._allowed_user_ids = allowed_user_ids or set()
        self._running = False

        # 解密
        self._secret_key: Optional[bytes] = None
        self._dave_session = None
        self._bot_ssrc: int = 0

        # SSRC -> user_id 映射（通过 SPEAKING 事件填充）
        self._ssrc_to_user: Dict[int, int] = {}
        self._lock = threading.Lock()

        # 每用户音频缓冲区
        self._buffers: Dict[int, bytearray] = defaultdict(bytearray)
        self._last_packet_time: Dict[int, float] = {}

        # 每 SSRC 的 Opus 解码器（每个用户需要独立的解码器状态）
        self._decoders: Dict[int, object] = {}

        # 暂停标志：机器人播放 TTS 时不捕获音频
        self._paused = False

        # 调试日志计数器（实例级别以避免跨实例竞争）
        self._packet_debug_count = 0

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def start(self):
        """开始监听语音数据包。"""
        conn = self._vc._connection
        self._secret_key = bytes(conn.secret_key)
        self._dave_session = conn.dave_session
        self._bot_ssrc = conn.ssrc

        self._install_speaking_hook(conn)
        conn.add_socket_listener(self._on_packet)
        self._running = True
        logger.info("VoiceReceiver started (bot_ssrc=%d)", self._bot_ssrc)

    def stop(self):
        """停止监听并清理资源。"""
        self._running = False
        try:
            self._vc._connection.remove_socket_listener(self._on_packet)
        except Exception:
            pass
        with self._lock:
            self._buffers.clear()
            self._last_packet_time.clear()
            self._decoders.clear()
            self._ssrc_to_user.clear()
        logger.info("VoiceReceiver stopped")

    def pause(self):
        self._paused = True

    def resume(self):
        self._paused = False

    # ------------------------------------------------------------------
    # SSRC -> user_id 映射，通过 SPEAKING 操作码钩子
    # ------------------------------------------------------------------

    def map_ssrc(self, ssrc: int, user_id: int):
        with self._lock:
            self._ssrc_to_user[ssrc] = user_id

    def _install_speaking_hook(self, conn):
        """包装语音 WebSocket 钩子以捕获 SPEAKING 事件（op 5）。

        VoiceConnectionState 将钩子存储为 ``conn.hook``（公共属性）。
        它在每次（重新）连接时传递给 DiscordVoiceWebSocket，因此我们
        必须在 VoiceConnectionState 级别和当前活动的
        WebSocket 实例上包装它。
        """
        original_hook = conn.hook
        receiver_self = self

        async def wrapped_hook(ws, msg):
            if isinstance(msg, dict) and msg.get("op") == 5:
                data = msg.get("d", {})
                ssrc = data.get("ssrc")
                user_id = data.get("user_id")
                if ssrc and user_id:
                    logger.info("SPEAKING event: ssrc=%d -> user=%s", ssrc, user_id)
                    receiver_self.map_ssrc(int(ssrc), int(user_id))
            if original_hook:
                await original_hook(ws, msg)

        # 设置到连接状态上（用于后续重连）
        conn.hook = wrapped_hook
        # 设置到当前活跃的 WebSocket 上（立即生效）
        try:
            from discord.utils import MISSING
            if hasattr(conn, 'ws') and conn.ws is not MISSING:
                conn.ws._hook = wrapped_hook
                logger.info("Speaking hook installed on live websocket")
        except Exception as e:
            logger.warning("Could not install hook on live ws: %s", e)

    # ------------------------------------------------------------------
    # 数据包处理器（从 SocketReader 线程调用）
    # ------------------------------------------------------------------

    def _on_packet(self, data: bytes):
        if not self._running or self._paused:
            return

        # 记录前几个原始数据包用于调试
        self._packet_debug_count += 1
        if self._packet_debug_count <= 5:
            logger.debug(
                "Raw UDP packet: len=%d, first_bytes=%s",
                len(data), data[:4].hex() if len(data) >= 4 else "short",
            )

        if len(data) < 16:
            return

        # RTP 版本检查：最高 2 位必须为 10（版本 2）。
        # 低位可能变化（填充、扩展、CSRC 计数）。
        # 负载类型（字节 1 低 7 位）= 0x78（120）表示语音。
        if (data[0] >> 6) != 2 or (data[1] & 0x7F) != 0x78:
            if self._packet_debug_count <= 5:
                logger.debug("Skipped non-RTP: byte0=0x%02x byte1=0x%02x", data[0], data[1])
            return

        first_byte = data[0]
        _, _, seq, timestamp, ssrc = struct.unpack_from(">BBHII", data, 0)

        # 跳过机器人自身的音频
        if ssrc == self._bot_ssrc:
            return

        # 计算动态 RTP 头大小（RFC 9335 / rtpsize 模式）
        cc = first_byte & 0x0F  # CSRC 计数
        has_extension = bool(first_byte & 0x10)  # 扩展位
        has_padding = bool(first_byte & 0x20)  # 填充位（RFC 3550 §5.1）
        header_size = 12 + (4 * cc) + (4 if has_extension else 0)

        if len(data) < header_size + 4:  # 至少需要头部 + 随机数
            return

        # 从前导字节读取扩展长度（解密后用于跳过）
        ext_data_len = 0
        if has_extension:
            ext_preamble_offset = 12 + (4 * cc)
            ext_words = struct.unpack_from(">H", data, ext_preamble_offset + 2)[0]
            ext_data_len = ext_words * 4

        if self._packet_debug_count <= 10:
            with self._lock:
                known_user = self._ssrc_to_user.get(ssrc, "unknown")
            logger.debug(
                "RTP packet: ssrc=%d, seq=%d, user=%s, hdr=%d, ext_data=%d",
                ssrc, seq, known_user, header_size, ext_data_len,
            )

        header = bytes(data[:header_size])
        payload_with_nonce = data[header_size:]

        # --- NaCl transport decrypt (aead_xchacha20_poly1305_rtpsize) ---
        if len(payload_with_nonce) < 4:
            return
        nonce = bytearray(24)
        nonce[:4] = payload_with_nonce[-4:]
        encrypted = bytes(payload_with_nonce[:-4])

        try:
            import nacl.secret  # noqa: 延迟导入 - 仅在语音路径中使用
            box = nacl.secret.Aead(self._secret_key)
            decrypted = box.decrypt(encrypted, header, bytes(nonce))
        except Exception as e:
            if self._packet_debug_count <= 10:
                logger.warning("NaCl decrypt failed: %s (hdr=%d, enc=%d)", e, header_size, len(encrypted))
            return

        # 跳过加密的扩展数据以获取实际的 opus 负载
        if ext_data_len and len(decrypted) > ext_data_len:
            decrypted = decrypted[ext_data_len:]

        # --- 去除 RTP 填充（RFC 3550 §5.1）---
        # 当 P 位置位时，负载最后一个字节表示
        # 需要移除的尾部填充字节数（包括自身），
        # 在进一步处理前必须移除。跳过此步骤会将填充污染的
        # 字节传入 DAVE/Opus 并损坏入站音频。
        if has_padding:
            if not decrypted:
                if self._packet_debug_count <= 10:
                    logger.warning(
                        "RTP padding bit set but no payload (ssrc=%d)", ssrc,
                    )
                return
            pad_len = decrypted[-1]
            if pad_len == 0 or pad_len > len(decrypted):
                if self._packet_debug_count <= 10:
                    logger.warning(
                        "Invalid RTP padding length %d for payload size %d (ssrc=%d)",
                        pad_len, len(decrypted), ssrc,
                    )
                return
            decrypted = decrypted[:-pad_len]
            if not decrypted:
                # 填充消耗了整个负载 —— 无数据可解码
                return

        # --- DAVE E2EE decrypt ---
        if self._dave_session:
            with self._lock:
                user_id = self._ssrc_to_user.get(ssrc, 0)
            if user_id:
                try:
                    import davey
                    decrypted = self._dave_session.decrypt(
                        user_id, davey.MediaType.audio, decrypted
                    )
                except Exception as e:
                    # 未加密直通 —— 直接使用 NaCl 解密后的数据
                    if "Unencrypted" not in str(e):
                        if self._packet_debug_count <= 10:
                            logger.warning("DAVE decrypt failed for ssrc=%d: %s", ssrc, e)
                        return
            # 如果 SSRC 未知（尚无 SPEAKING 事件），跳过 DAVE 直接
            # 尝试 Opus 解码 —— 音频可能处于直通模式。
            # 当 SPEAKING 事件到达时缓冲区将获得 user_id。

        # --- Opus decode -> PCM ---
        try:
            if ssrc not in self._decoders:
                self._decoders[ssrc] = discord.opus.Decoder()
            pcm = self._decoders[ssrc].decode(decrypted)
            with self._lock:
                self._buffers[ssrc].extend(pcm)
                self._last_packet_time[ssrc] = time.monotonic()
        except Exception as e:
            logger.debug("Opus decode error for SSRC %s: %s", ssrc, e)
            return

    # ------------------------------------------------------------------
    # 静音检测
    # ------------------------------------------------------------------

    def _infer_user_for_ssrc(self, ssrc: int) -> int:
        """尝试为未映射的 SSRC 推断 user_id。

        When the bot rejoins a voice channel, Discord may not resend
        SPEAKING events for users already speaking.  If exactly one
        allowed user is in the channel, map the SSRC to them.
        """
        try:
            channel = self._vc.channel
            if not channel:
                return 0
            bot_id = self._vc.user.id if self._vc.user else 0
            allowed = self._allowed_user_ids
            candidates = [
                m.id for m in channel.members
                if m.id != bot_id and (not allowed or str(m.id) in allowed)
            ]
            if len(candidates) == 1:
                uid = candidates[0]
                self._ssrc_to_user[ssrc] = uid
                logger.info("Auto-mapped ssrc=%d -> user=%d (sole allowed member)", ssrc, uid)
                return uid
        except Exception:
            pass
        return 0

    def check_silence(self) -> list:
        """返回已完成语句的 (user_id, pcm_bytes) 列表。"""
        now = time.monotonic()
        completed = []

        with self._lock:
            ssrc_user_map = dict(self._ssrc_to_user)
            ssrc_list = list(self._buffers.keys())

            for ssrc in ssrc_list:
                last_time = self._last_packet_time.get(ssrc, now)
                silence_duration = now - last_time
                buf = self._buffers[ssrc]
                # 48kHz, 16-bit, stereo = 192000 bytes/sec
                buf_duration = len(buf) / (self.SAMPLE_RATE * self.CHANNELS * 2)

                if silence_duration >= self.SILENCE_THRESHOLD and buf_duration >= self.MIN_SPEECH_DURATION:
                    user_id = ssrc_user_map.get(ssrc, 0)
                    if not user_id:
                        # SSRC 未映射（机器人重连后缺少 SPEAKING 事件）。
                        # 从语音频道中的允许用户推断。
                        user_id = self._infer_user_for_ssrc(ssrc)
                    if user_id:
                        completed.append((user_id, bytes(buf)))
                    self._buffers[ssrc] = bytearray()
                    self._last_packet_time.pop(ssrc, None)
                elif silence_duration >= self.SILENCE_THRESHOLD * 2:
                    # 过期缓冲区且无有效用户 —— 丢弃
                    self._buffers.pop(ssrc, None)
                    self._last_packet_time.pop(ssrc, None)

        return completed

    # ------------------------------------------------------------------
    # PCM -> WAV conversion (for Whisper STT)
    # ------------------------------------------------------------------

    @staticmethod
    def pcm_to_wav(pcm_data: bytes, output_path: str,
                   src_rate: int = 48000, src_channels: int = 2):
        """通过 ffmpeg 将原始 PCM 转换为 16kHz 单声道 WAV。"""
        with tempfile.NamedTemporaryFile(suffix=".pcm", delete=False) as f:
            f.write(pcm_data)
            pcm_path = f.name
        try:
            subprocess.run(
                [
                    "ffmpeg", "-y", "-loglevel", "error",
                    "-f", "s16le",
                    "-ar", str(src_rate),
                    "-ac", str(src_channels),
                    "-i", pcm_path,
                    "-ar", "16000",
                    "-ac", "1",
                    output_path,
                ],
                check=True,
                timeout=10,
            )
        finally:
            try:
                os.unlink(pcm_path)
            except OSError:
                pass


class DiscordAdapter(BasePlatformAdapter):
    """
    Discord 机器人适配器。

    处理功能：
    - 接收来自服务器和私聊的消息
    - 以 Discord Markdown 格式发送回复
    - 线程支持
    - 原生斜杠命令（/ask、/reset、/status、/stop）
    - 基于按钮的命令执行审批
    - 长对话自动创建线程
    - 基于表情反应的反馈
    """

    # Discord 消息长度限制
    MAX_MESSAGE_LENGTH = 2000
    _SPLIT_THRESHOLD = 1900  # 接近 2000 字符拆分点

    # 不活跃后自动断开语音频道的秒数
    VOICE_TIMEOUT = 300

    def __init__(self, config: PlatformConfig):
        super().__init__(config, Platform.DISCORD)
        self._client: Optional[commands.Bot] = None
        self._ready_event = asyncio.Event()
        self._allowed_user_ids: set = set()  # 用于按钮审批授权
        # 语音频道状态（按服务器）
        self._voice_clients: Dict[int, Any] = {}  # guild_id -> VoiceClient
        # 文本批处理：合并快速连续消息（Telegram 风格）
        self._text_batch_delay_seconds = float(os.getenv("HERMES_DISCORD_TEXT_BATCH_DELAY_SECONDS", "0.6"))
        self._text_batch_split_delay_seconds = float(os.getenv("HERMES_DISCORD_TEXT_BATCH_SPLIT_DELAY_SECONDS", "2.0"))
        self._pending_text_batches: Dict[str, MessageEvent] = {}
        self._pending_text_batch_tasks: Dict[str, asyncio.Task] = {}
        self._voice_text_channels: Dict[int, int] = {}  # guild_id -> 文字频道 ID
        self._voice_sources: Dict[int, Dict[str, Any]] = {}  # guild_id -> 关联的文字频道来源元数据
        self._voice_timeout_tasks: Dict[int, asyncio.Task] = {}  # guild_id -> 超时任务
        # 第二阶段：语音监听
        self._voice_receivers: Dict[int, VoiceReceiver] = {}  # guild_id -> 语音接收器
        self._voice_listen_tasks: Dict[int, asyncio.Task] = {}  # guild_id -> 监听循环
        self._voice_input_callback: Optional[Callable] = None  # 由 run.py 设置
        self._on_voice_disconnect: Optional[Callable] = None  # 由 run.py 设置
        # 记录机器人已参与的线程，这些线程中的后续消息
        # 不需要 @提及。持久化到磁盘以便集合在网关重启后保留。
        self._threads = ThreadParticipationTracker("discord")
        # 每个频道的持久打字指示器循环（私聊不可靠地
        # 显示机器人的标准打字网关事件）
        self._typing_tasks: Dict[str, asyncio.Task] = {}
        self._bot_task: Optional[asyncio.Task] = None
        self._post_connect_task: Optional[asyncio.Task] = None
        # 去重缓存：当 Discord RESUME 在重连后重放事件时，
        # 防止重复的机器人响应。
        self._dedup = MessageDeduplicator()
        # 回复线程模式："off"（不回复）、"first"（仅在第一个
        # 分片回复，默认）、"all"（每个分片都使用回复引用）。
        self._reply_to_mode: str = getattr(config, 'reply_to_mode', 'first') or 'first'

    async def connect(self) -> bool:
        """连接到 Discord 并开始接收事件。"""
        if not DISCORD_AVAILABLE:
            logger.error("[%s] discord.py not installed. Run: pip install discord.py", self.name)
            return False

        # 加载 Opus 编解码器以支持语音频道
        if not discord.opus.is_loaded():
            import ctypes.util
            opus_path = ctypes.util.find_library("opus")
            # ctypes.util.find_library fails on macOS with Homebrew-installed libs,
            # so fall back to known Homebrew paths if needed.
            if not opus_path:
                import sys
                _homebrew_paths = (
                    "/opt/homebrew/lib/libopus.dylib",  # Apple Silicon
                    "/usr/local/lib/libopus.dylib",     # Intel Mac
                )
                if sys.platform == "darwin":
                    for _hp in _homebrew_paths:
                        if os.path.isfile(_hp):
                            opus_path = _hp
                            break
            if opus_path:
                try:
                    discord.opus.load_opus(opus_path)
                except Exception:
                    logger.warning("Opus codec found at %s but failed to load", opus_path)
            if not discord.opus.is_loaded():
                logger.warning("Opus codec not found — voice channel playback disabled")

        if not self.config.token:
            logger.error("[%s] No bot token configured", self.name)
            return False

        try:
            if not self._acquire_platform_lock('discord-bot-token', self.config.token, 'Discord bot token'):
                return False

            # 解析允许的用户条目（可能包含用户名或 ID）
            allowed_env = os.getenv("DISCORD_ALLOWED_USERS", "")
            if allowed_env:
                self._allowed_user_ids = {
                    _clean_discord_id(uid) for uid in allowed_env.split(",")
                    if uid.strip()
                }

            # 设置 intents。
            # Message Content 是正常文本回复所必需的。
            # Server Members 仅在白名单包含需要解析为数字 ID 的用户名时需要。
            # 请求在 Discord 开发者门户未启用的特权 intents 可能导致
            # 机器人完全无法上线，因此除非确实需要否则避免请求 members intent。
            intents = Intents.default()
            intents.message_content = True
            intents.dm_messages = True
            intents.guild_messages = True
            intents.members = any(not entry.isdigit() for entry in self._allowed_user_ids)
            intents.voice_states = True

            # 解析代理（DISCORD_PROXY > 通用环境变量 > macOS 系统代理）
            from gateway.platforms.base import resolve_proxy_url, proxy_kwargs_for_bot
            proxy_url = resolve_proxy_url(platform_env_var="DISCORD_PROXY")
            if proxy_url:
                logger.info("[%s] Using proxy for Discord: %s", self.name, proxy_url)

            # 创建机器人 —— proxy= 用于 HTTP，connector= 用于 SOCKS
            self._client = commands.Bot(
                command_prefix="!",  # 实际未使用，我们处理原始消息
                intents=intents,
                **proxy_kwargs_for_bot(proxy_url),
            )
            adapter_self = self  # 供闭包捕获

            # 注册事件处理器
            @self._client.event
            async def on_ready():
                logger.info("[%s] Connected as %s", adapter_self.name, adapter_self._client.user)

                # 将白名单中的用户名解析为数字 ID
                await adapter_self._resolve_allowed_usernames()
                adapter_self._ready_event.set()

                if adapter_self._post_connect_task and not adapter_self._post_connect_task.done():
                    adapter_self._post_connect_task.cancel()
                adapter_self._post_connect_task = asyncio.create_task(
                    adapter_self._run_post_connect_initialization()
                )

            @self._client.event
            async def on_message(message: DiscordMessage):
                # 去重：Discord RESUME 在重连后重放事件（#4777）
                if adapter_self._dedup.is_duplicate(str(message.id)):
                    return

                # 始终忽略自身的消息
                if message.author == self._client.user:
                    return

                # 忽略 Discord 系统消息（线程重命名、置顶、成员加入等）
                # 同时允许默认类型和回复类型 —— 回复有独立的 MessageType。
                if message.type not in (discord.MessageType.default, discord.MessageType.reply):
                    return

                # 检查消息作者是否在允许的用户列表中
                if not self._is_allowed_user(str(message.author.id)):
                    return

                # 机器人消息过滤（DISCORD_ALLOW_BOTS）：
                #   "none"     —— 忽略所有其他机器人（默认）
                #   "mentions" —— 仅当机器人 @提及我们时接受
                #   "all"      —— 接受所有机器人消息
                if getattr(message.author, "bot", False):
                    allow_bots = os.getenv("DISCORD_ALLOW_BOTS", "none").lower().strip()
                    if allow_bots == "none":
                        return
                    elif allow_bots == "mentions":
                        if not self._client.user or self._client.user not in message.mentions:
                            return
                    # "all" falls through to handle_message
                
                # 多 agent 过滤：如果消息提及了特定机器人
                # 但不包括本机器人，则发送者是在与另一个 agent 对话 ——
                # 保持沉默。没有机器人提及的消息（一般聊天）
                # 仍然传递到 _handle_message 进行现有的
                # DISCORD_REQUIRE_MENTION 检查。
                #
                # 这替代了旧的 DISCORD_IGNORE_NO_MENTION 逻辑，
                # 使用感知机器人的过滤，在多个 agent 共享频道时正确工作。
                if not isinstance(message.channel, discord.DMChannel) and message.mentions:
                    _self_mentioned = (
                        self._client.user is not None
                        and self._client.user in message.mentions
                    )
                    _other_bots_mentioned = any(
                        m.bot and m != self._client.user
                        for m in message.mentions
                    )
                    # 如果其他机器人被提及但我们没有被提及 -> 不是给我们的
                    if _other_bots_mentioned and not _self_mentioned:
                        return
                    # 如果人类被提及但我们没有被提及 -> 不是给我们的
                    # （保留旧的 DISCORD_IGNORE_NO_MENTION=true 行为）
                    _ignore_no_mention = os.getenv(
                        "DISCORD_IGNORE_NO_MENTION", "true"
                    ).lower() in ("true", "1", "yes")
                    if _ignore_no_mention and not _self_mentioned and not _other_bots_mentioned:
                        return

                await self._handle_message(message)

            @self._client.event
            async def on_voice_state_update(member, before, after):
                """跟踪语音频道加入/离开事件。"""
                # 仅跟踪机器人已连接的频道
                bot_guild_ids = set(adapter_self._voice_clients.keys())
                if not bot_guild_ids:
                    return
                guild_id = member.guild.id
                if guild_id not in bot_guild_ids:
                    return
                # 忽略机器人自身
                if member == adapter_self._client.user:
                    return

                joined = before.channel is None and after.channel is not None
                left = before.channel is not None and after.channel is None
                switched = (
                    before.channel is not None
                    and after.channel is not None
                    and before.channel != after.channel
                )

                if joined or left or switched:
                    logger.info(
                        "Voice state: %s (%d) %s (guild %d)",
                        member.display_name,
                        member.id,
                        "joined " + after.channel.name if joined
                        else "left " + before.channel.name if left
                        else f"moved {before.channel.name} -> {after.channel.name}",
                        guild_id,
                    )

            # 注册斜杠命令
            self._register_slash_commands()

            # 在后台启动机器人
            self._bot_task = asyncio.create_task(self._client.start(self.config.token))

            # 等待就绪
            await asyncio.wait_for(self._ready_event.wait(), timeout=30)

            self._running = True
            return True

        except asyncio.TimeoutError:
            logger.error("[%s] Timeout waiting for connection to Discord", self.name, exc_info=True)
            self._release_platform_lock()
            return False
        except Exception as e:  # pragma: no cover - defensive logging
            logger.error("[%s] Failed to connect to Discord: %s", self.name, e, exc_info=True)
            self._release_platform_lock()
            return False

    async def disconnect(self) -> None:
        """从 Discord 断开连接。"""
        # 关闭客户端前清理所有活跃的语音连接
        for guild_id in list(self._voice_clients.keys()):
            try:
                await self.leave_voice_channel(guild_id)
            except Exception as e:  # pragma: no cover - defensive logging
                logger.debug("[%s] Error leaving voice channel %s: %s", self.name, guild_id, e)

        if self._client:
            try:
                await self._client.close()
            except Exception as e:  # pragma: no cover - defensive logging
                logger.warning("[%s] Error during disconnect: %s", self.name, e, exc_info=True)

        if self._post_connect_task and not self._post_connect_task.done():
            self._post_connect_task.cancel()
            try:
                await self._post_connect_task
            except asyncio.CancelledError:
                pass

        self._running = False
        self._client = None
        self._ready_event.clear()
        self._post_connect_task = None

        self._release_platform_lock()

        logger.info("[%s] Disconnected", self.name)

    async def _run_post_connect_initialization(self) -> None:
        """在 Discord 连接后完成非关键启动工作。"""
        if not self._client:
            return
        try:
            synced = await asyncio.wait_for(self._client.tree.sync(), timeout=30)
            logger.info("[%s] Synced %d slash command(s)", self.name, len(synced))
        except asyncio.TimeoutError:
            logger.warning("[%s] Slash command sync timed out after 30s", self.name)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # pragma: no cover - defensive logging
            logger.warning("[%s] Slash command sync failed: %s", self.name, e, exc_info=True)

    async def _add_reaction(self, message: Any, emoji: str) -> bool:
        """在 Discord 消息上添加表情反应。"""
        if not message or not hasattr(message, "add_reaction"):
            return False
        try:
            await message.add_reaction(emoji)
            return True
        except Exception as e:
            logger.debug("[%s] add_reaction failed (%s): %s", self.name, emoji, e)
            return False

    async def _remove_reaction(self, message: Any, emoji: str) -> bool:
        """从 Discord 消息中移除机器人自己的表情反应。"""
        if not message or not hasattr(message, "remove_reaction") or not self._client or not self._client.user:
            return False
        try:
            await message.remove_reaction(emoji, self._client.user)
            return True
        except Exception as e:
            logger.debug("[%s] remove_reaction failed (%s): %s", self.name, emoji, e)
            return False

    def _reactions_enabled(self) -> bool:
        """检查消息反应是否通过配置/环境变量启用。"""
        return os.getenv("DISCORD_REACTIONS", "true").lower() not in ("false", "0", "no")

    async def on_processing_start(self, event: MessageEvent) -> None:
        """为普通 Discord 消息事件添加处理中反应。"""
        if not self._reactions_enabled():
            return
        message = event.raw_message
        if hasattr(message, "add_reaction"):
            await self._add_reaction(message, "👀")

    async def on_processing_complete(self, event: MessageEvent, outcome: ProcessingOutcome) -> None:
        """将处理中反应替换为最终的成功/失败反应。"""
        if not self._reactions_enabled():
            return
        message = event.raw_message
        if hasattr(message, "add_reaction"):
            await self._remove_reaction(message, "👀")
            if outcome == ProcessingOutcome.SUCCESS:
                await self._add_reaction(message, "✅")
            elif outcome == ProcessingOutcome.FAILURE:
                await self._add_reaction(message, "❌")

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> SendResult:
        """向 Discord 频道或线程发送消息。

        当 metadata 包含 thread_id 时，消息发送到该线程
        而不是 chat_id 标识的父频道。
        """
        if not self._client:
            return SendResult(success=False, error="Not connected")

        try:
            # 确定目标频道：metadata 中的 thread_id 优先。
            thread_id = None
            if metadata and metadata.get("thread_id"):
                thread_id = metadata["thread_id"]

            if thread_id:
                # 直接获取线程 —— 线程通过自身 ID 寻址。
                channel = self._client.get_channel(int(thread_id))
                if not channel:
                    channel = await self._client.fetch_channel(int(thread_id))
                if not channel:
                    return SendResult(success=False, error=f"Thread {thread_id} not found")
            else:
                # 获取父频道
                channel = self._client.get_channel(int(chat_id))
                if not channel:
                    channel = await self._client.fetch_channel(int(chat_id))
                if not channel:
                    return SendResult(success=False, error=f"Channel {chat_id} not found")

            # 格式化并根据需要分割消息
            formatted = self.format_message(content)
            chunks = self.truncate_message(formatted, self.MAX_MESSAGE_LENGTH)

            message_ids = []
            reference = None

            if reply_to and self._reply_to_mode != "off":
                try:
                    ref_msg = await channel.fetch_message(int(reply_to))
                    reference = ref_msg
                except Exception as e:
                    logger.debug("Could not fetch reply-to message: %s", e)

            for i, chunk in enumerate(chunks):
                if self._reply_to_mode == "all":
                    chunk_reference = reference
                else:  # "first" (default) or "off"
                    chunk_reference = reference if i == 0 else None
                try:
                    msg = await channel.send(
                        content=chunk,
                        reference=chunk_reference,
                    )
                except Exception as e:
                    err_text = str(e)
                    if (
                        chunk_reference is not None
                        and "error code: 50035" in err_text
                        and "Cannot reply to a system message" in err_text
                    ):
                        logger.warning(
                            "[%s] Reply target %s is a Discord system message; retrying send without reply reference",
                            self.name,
                            reply_to,
                        )
                        msg = await channel.send(
                            content=chunk,
                            reference=None,
                        )
                    else:
                        raise
                message_ids.append(str(msg.id))

            return SendResult(
                success=True,
                message_id=message_ids[0] if message_ids else None,
                raw_response={"message_ids": message_ids}
            )

        except Exception as e:  # pragma: no cover - defensive logging
            logger.error("[%s] Failed to send Discord message: %s", self.name, e, exc_info=True)
            return SendResult(success=False, error=str(e))

    async def edit_message(
        self,
        chat_id: str,
        message_id: str,
        content: str,
    ) -> SendResult:
        """编辑之前发送的 Discord 消息。"""
        if not self._client:
            return SendResult(success=False, error="Not connected")
        try:
            channel = self._client.get_channel(int(chat_id))
            if not channel:
                channel = await self._client.fetch_channel(int(chat_id))
            msg = await channel.fetch_message(int(message_id))
            formatted = self.format_message(content)
            if len(formatted) > self.MAX_MESSAGE_LENGTH:
                formatted = formatted[:self.MAX_MESSAGE_LENGTH - 3] + "..."
            await msg.edit(content=formatted)
            return SendResult(success=True, message_id=message_id)
        except Exception as e:  # pragma: no cover - defensive logging
            logger.error("[%s] Failed to edit Discord message %s: %s", self.name, message_id, e, exc_info=True)
            return SendResult(success=False, error=str(e))

    async def _send_file_attachment(
        self,
        chat_id: str,
        file_path: str,
        caption: Optional[str] = None,
        file_name: Optional[str] = None,
    ) -> SendResult:
        """以 Discord 附件发送本地文件。"""
        if not self._client:
            return SendResult(success=False, error="Not connected")

        channel = self._client.get_channel(int(chat_id))
        if not channel:
            channel = await self._client.fetch_channel(int(chat_id))
        if not channel:
            return SendResult(success=False, error=f"Channel {chat_id} not found")

        filename = file_name or os.path.basename(file_path)
        with open(file_path, "rb") as fh:
            file = discord.File(fh, filename=filename)
            msg = await channel.send(content=caption if caption else None, file=file)
        return SendResult(success=True, message_id=str(msg.id))

    async def play_tts(
        self,
        chat_id: str,
        audio_path: str,
        **kwargs,
    ) -> SendResult:
        """播放自动 TTS 音频。

        当机器人在该聊天所属服务器的语音频道中时，
        直接在语音频道播放，而不是作为文件附件发送。
        """
        for gid, text_ch_id in self._voice_text_channels.items():
            if str(text_ch_id) == str(chat_id) and self.is_in_voice_channel(gid):
                logger.info("[%s] Playing TTS in voice channel (guild=%d)", self.name, gid)
                success = await self.play_in_voice_channel(gid, audio_path)
                return SendResult(success=success)
        return await self.send_voice(chat_id=chat_id, audio_path=audio_path, **kwargs)

    async def send_voice(
        self,
        chat_id: str,
        audio_path: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> SendResult:
        """以 Discord 文件附件发送音频。"""
        try:
            import io

            channel = self._client.get_channel(int(chat_id))
            if not channel:
                channel = await self._client.fetch_channel(int(chat_id))
            if not channel:
                return SendResult(success=False, error=f"Channel {chat_id} not found")

            if not os.path.exists(audio_path):
                return SendResult(success=False, error=f"Audio file not found: {audio_path}")

            filename = os.path.basename(audio_path)

            with open(audio_path, "rb") as f:
                file_data = f.read()

            # 尝试通过原始 API 以原生语音消息发送（flags=8192）。
            try:
                import base64

                duration_secs = 5.0
                try:
                    from mutagen.oggopus import OggOpus
                    info = OggOpus(audio_path)
                    duration_secs = info.info.length
                except Exception:
                    duration_secs = max(1.0, len(file_data) / 2000.0)

                waveform_bytes = bytes([128] * 256)
                waveform_b64 = base64.b64encode(waveform_bytes).decode()

                import json as _json
                payload = _json.dumps({
                    "flags": 8192,
                    "attachments": [{
                        "id": "0",
                        "filename": "voice-message.ogg",
                        "duration_secs": round(duration_secs, 2),
                        "waveform": waveform_b64,
                    }],
                })
                form = [
                    {"name": "payload_json", "value": payload},
                    {
                        "name": "files[0]",
                        "value": file_data,
                        "filename": "voice-message.ogg",
                        "content_type": "audio/ogg",
                    },
                ]
                msg_data = await self._client.http.request(
                    discord.http.Route("POST", "/channels/{channel_id}/messages", channel_id=channel.id),
                    form=form,
                )
                return SendResult(success=True, message_id=str(msg_data["id"]))
            except Exception as voice_err:
                logger.debug("Voice message flag failed, falling back to file: %s", voice_err)
                file = discord.File(io.BytesIO(file_data), filename=filename)
                msg = await channel.send(file=file)
                return SendResult(success=True, message_id=str(msg.id))
        except Exception as e:  # pragma: no cover - defensive logging
            logger.error("[%s] Failed to send audio, falling back to base adapter: %s", self.name, e, exc_info=True)
            return await super().send_voice(chat_id, audio_path, caption, reply_to, metadata=metadata)

    # ------------------------------------------------------------------
    # 语音频道方法（加入 / 离开 / 播放）
    # ------------------------------------------------------------------

    async def join_voice_channel(self, channel) -> bool:
        """加入 Discord 语音频道。成功时返回 True。"""
        if not self._client or not DISCORD_AVAILABLE:
            return False
        guild_id = channel.guild.id

        # 已在此服务器中连接？
        existing = self._voice_clients.get(guild_id)
        if existing and existing.is_connected():
            if existing.channel.id == channel.id:
                self._reset_voice_timeout(guild_id)
                return True
            await existing.move_to(channel)
            self._reset_voice_timeout(guild_id)
            return True

        vc = await channel.connect()
        self._voice_clients[guild_id] = vc
        self._reset_voice_timeout(guild_id)

        # 启动语音接收器（第二阶段：监听用户）
        try:
            receiver = VoiceReceiver(vc, allowed_user_ids=self._allowed_user_ids)
            receiver.start()
            self._voice_receivers[guild_id] = receiver
            self._voice_listen_tasks[guild_id] = asyncio.ensure_future(
                self._voice_listen_loop(guild_id)
            )
        except Exception as e:
            logger.warning("Voice receiver failed to start: %s", e)

        return True

    async def leave_voice_channel(self, guild_id: int) -> None:
        """从服务器的语音频道断开连接。"""
        # 先停止语音接收器
        receiver = self._voice_receivers.pop(guild_id, None)
        if receiver:
            receiver.stop()
        listen_task = self._voice_listen_tasks.pop(guild_id, None)
        if listen_task:
            listen_task.cancel()

        vc = self._voice_clients.pop(guild_id, None)
        if vc and vc.is_connected():
            await vc.disconnect()
        task = self._voice_timeout_tasks.pop(guild_id, None)
        if task:
            task.cancel()
        self._voice_text_channels.pop(guild_id, None)
        self._voice_sources.pop(guild_id, None)

    # 语音播放最大等待秒数
    PLAYBACK_TIMEOUT = 120

    async def play_in_voice_channel(self, guild_id: int, audio_path: str) -> bool:
        """在已连接的语音频道中播放音频文件。"""
        vc = self._voice_clients.get(guild_id)
        if not vc or not vc.is_connected():
            return False

        # 播放时暂停语音接收器（防止回声）
        receiver = self._voice_receivers.get(guild_id)
        if receiver:
            receiver.pause()

        try:
            # 等待当前播放完成（带超时）
            wait_start = time.monotonic()
            while vc.is_playing():
                if time.monotonic() - wait_start > self.PLAYBACK_TIMEOUT:
                    logger.warning("Timed out waiting for previous playback to finish")
                    vc.stop()
                    break
                await asyncio.sleep(0.1)

            done = asyncio.Event()
            loop = asyncio.get_running_loop()

            def _after(error):
                if error:
                    logger.error("Voice playback error: %s", error)
                loop.call_soon_threadsafe(done.set)

            source = discord.FFmpegPCMAudio(audio_path)
            source = discord.PCMVolumeTransformer(source, volume=1.0)
            vc.play(source, after=_after)
            try:
                await asyncio.wait_for(done.wait(), timeout=self.PLAYBACK_TIMEOUT)
            except asyncio.TimeoutError:
                logger.warning("Voice playback timed out after %ds", self.PLAYBACK_TIMEOUT)
                vc.stop()
            self._reset_voice_timeout(guild_id)
            return True
        finally:
            if receiver:
                receiver.resume()

    async def get_user_voice_channel(self, guild_id: int, user_id: str):
        """返回用户当前所在的语音频道，如果没有则返回 None。"""
        if not self._client:
            return None
        guild = self._client.get_guild(guild_id)
        if not guild:
            return None
        member = guild.get_member(int(user_id))
        if not member or not member.voice:
            return None
        return member.voice.channel

    def _reset_voice_timeout(self, guild_id: int) -> None:
        """重置自动断开不活动定时器。"""
        task = self._voice_timeout_tasks.pop(guild_id, None)
        if task:
            task.cancel()
        self._voice_timeout_tasks[guild_id] = asyncio.ensure_future(
            self._voice_timeout_handler(guild_id)
        )

    async def _voice_timeout_handler(self, guild_id: int) -> None:
        """在 VOICE_TIMEOUT 秒不活动后自动断开连接。"""
        try:
            await asyncio.sleep(self.VOICE_TIMEOUT)
        except asyncio.CancelledError:
            return
        text_ch_id = self._voice_text_channels.get(guild_id)
        await self.leave_voice_channel(guild_id)
        # 通知运行器以便清理 voice_mode 状态
        if self._on_voice_disconnect and text_ch_id:
            try:
                self._on_voice_disconnect(str(text_ch_id))
            except Exception:
                pass
        if text_ch_id and self._client:
            ch = self._client.get_channel(text_ch_id)
            if ch:
                try:
                    await ch.send("Left voice channel (inactivity timeout).")
                except Exception:
                    pass

    def is_in_voice_channel(self, guild_id: int) -> bool:
        """检查机器人是否已连接到此服务器的语音频道。"""
        vc = self._voice_clients.get(guild_id)
        return vc is not None and vc.is_connected()

    def get_voice_channel_info(self, guild_id: int) -> Optional[Dict[str, Any]]:
        """返回给定服务器的语音频道感知信息。

        如果机器人不在语音频道中则返回 None。否则返回包含
        频道名称、成员列表、计数和当前正在说话的
        用户 ID（来自 SSRC 映射）的字典。
        """
        vc = self._voice_clients.get(guild_id)
        if not vc or not vc.is_connected():
            return None

        channel = vc.channel
        if not channel:
            return None

        # 当前在语音频道中的成员（包括机器人）
        members_info = []
        bot_user = self._client.user if self._client else None
        for m in channel.members:
            if bot_user and m.id == bot_user.id:
                continue  # 跳过机器人自身
            members_info.append({
                "user_id": m.id,
                "display_name": m.display_name,
                "is_bot": m.bot,
            })

        # 当前正在说话的用户（来自 SSRC 映射 + 活跃缓冲区）
        speaking_user_ids: set = set()
        receiver = self._voice_receivers.get(guild_id)
        if receiver:
            import time as _time
            now = _time.monotonic()
            with receiver._lock:
                for ssrc, last_t in receiver._last_packet_time.items():
                    # 如果 2 秒内收到音频则视为"正在说话"
                    if now - last_t < 2.0:
                        uid = receiver._ssrc_to_user.get(ssrc)
                        if uid:
                            speaking_user_ids.add(uid)

        # 在成员上标记说话状态
        for info in members_info:
            info["is_speaking"] = info["user_id"] in speaking_user_ids

        return {
            "channel_name": channel.name,
            "member_count": len(members_info),
            "members": members_info,
            "speaking_count": len(speaking_user_ids),
        }

    def get_voice_channel_context(self, guild_id: int) -> str:
        """返回人类可读的语音频道上下文字符串。

        Suitable for injection into the system/ephemeral prompt so the
        agent is always aware of voice channel state.
        """
        info = self.get_voice_channel_info(guild_id)
        if not info:
            return ""

        parts = [f"[Voice channel: #{info['channel_name']} — {info['member_count']} participant(s)]"]
        for m in info["members"]:
            status = " (speaking)" if m["is_speaking"] else ""
            parts.append(f"  - {m['display_name']}{status}")

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # 语音监听（第二阶段）
    # ------------------------------------------------------------------

    # UDP 保活间隔（秒）—— 防止 Discord 在约 60 秒静默后
    # 丢弃 UDP 路由。
    _KEEPALIVE_INTERVAL = 15

    async def _voice_listen_loop(self, guild_id: int):
        """定期检查已完成的语句并处理它们。"""
        receiver = self._voice_receivers.get(guild_id)
        if not receiver:
            return
        last_keepalive = time.monotonic()
        try:
            while receiver._running:
                await asyncio.sleep(0.2)

                # 发送周期性 UDP 保活以防止 Discord 在约 60 秒
                # 静默后丢弃 UDP 会话。
                now = time.monotonic()
                if now - last_keepalive >= self._KEEPALIVE_INTERVAL:
                    last_keepalive = now
                    try:
                        vc = self._voice_clients.get(guild_id)
                        if vc and vc.is_connected():
                            vc._connection.send_packet(b'\xf8\xff\xfe')
                    except Exception:
                        pass

                completed = receiver.check_silence()
                for user_id, pcm_data in completed:
                    if not self._is_allowed_user(str(user_id)):
                        continue
                    await self._process_voice_input(guild_id, user_id, pcm_data)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("Voice listen loop error: %s", e, exc_info=True)

    async def _process_voice_input(self, guild_id: int, user_id: int, pcm_data: bytes):
        """将 PCM -> WAV -> STT -> 回调。"""
        from tools.voice_mode import is_whisper_hallucination

        tmp_f = tempfile.NamedTemporaryFile(suffix=".wav", prefix="vc_listen_", delete=False)
        wav_path = tmp_f.name
        tmp_f.close()
        try:
            await asyncio.to_thread(VoiceReceiver.pcm_to_wav, pcm_data, wav_path)

            from tools.transcription_tools import transcribe_audio
            result = await asyncio.to_thread(transcribe_audio, wav_path)

            if not result.get("success"):
                return
            transcript = result.get("transcript", "").strip()
            if not transcript or is_whisper_hallucination(transcript):
                return

            logger.info("Voice input from user %d: %s", user_id, transcript[:100])

            if self._voice_input_callback:
                await self._voice_input_callback(
                    guild_id=guild_id,
                    user_id=user_id,
                    transcript=transcript,
                )
        except Exception as e:
            logger.warning("Voice input processing failed: %s", e, exc_info=True)
        finally:
            try:
                os.unlink(wav_path)
            except OSError:
                pass

    def _is_allowed_user(self, user_id: str) -> bool:
        """检查用户是否在 DISCORD_ALLOWED_USERS 中。"""
        if not self._allowed_user_ids:
            return True
        return user_id in self._allowed_user_ids

    async def send_image_file(
        self,
        chat_id: str,
        image_path: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """以原生 Discord 文件附件发送本地图片文件。"""
        try:
            return await self._send_file_attachment(chat_id, image_path, caption)
        except FileNotFoundError:
            return SendResult(success=False, error=f"Image file not found: {image_path}")
        except Exception as e:  # pragma: no cover - defensive logging
            logger.error("[%s] Failed to send local image, falling back to base adapter: %s", self.name, e, exc_info=True)
            return await super().send_image_file(chat_id, image_path, caption, reply_to, metadata=metadata)

    async def send_image(
        self,
        chat_id: str,
        image_url: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """以原生 Discord 文件附件发送图片。"""
        if not self._client:
            return SendResult(success=False, error="Not connected")

        if not is_safe_url(image_url):
            logger.warning("[%s] Blocked unsafe image URL during Discord send_image", self.name)
            return await super().send_image(chat_id, image_url, caption, reply_to, metadata=metadata)

        try:
            import aiohttp

            channel = self._client.get_channel(int(chat_id))
            if not channel:
                channel = await self._client.fetch_channel(int(chat_id))
            if not channel:
                return SendResult(success=False, error=f"Channel {chat_id} not found")

            # 下载图片并以 Discord 文件附件发送
            # （Discord 会内联渲染附件，不同于纯 URL）
            from gateway.platforms.base import resolve_proxy_url, proxy_kwargs_for_aiohttp
            _proxy = resolve_proxy_url(platform_env_var="DISCORD_PROXY")
            _sess_kw, _req_kw = proxy_kwargs_for_aiohttp(_proxy)
            async with aiohttp.ClientSession(**_sess_kw) as session:
                async with session.get(image_url, timeout=aiohttp.ClientTimeout(total=30), **_req_kw) as resp:
                    if resp.status != 200:
                        raise Exception(f"Failed to download image: HTTP {resp.status}")

                    image_data = await resp.read()

                    # 从 URL 或 content type 确定文件名
                    content_type = resp.headers.get("content-type", "image/png")
                    ext = "png"
                    if "jpeg" in content_type or "jpg" in content_type:
                        ext = "jpg"
                    elif "gif" in content_type:
                        ext = "gif"
                    elif "webp" in content_type:
                        ext = "webp"

                    import io
                    file = discord.File(io.BytesIO(image_data), filename=f"image.{ext}")

                    msg = await channel.send(
                        content=caption if caption else None,
                        file=file,
                    )
                    return SendResult(success=True, message_id=str(msg.id))

        except ImportError:
            logger.warning(
                "[%s] aiohttp not installed, falling back to URL. Run: pip install aiohttp",
                self.name,
                exc_info=True,
            )
            return await super().send_image(chat_id, image_url, caption, reply_to)
        except Exception as e:  # pragma: no cover - defensive logging
            logger.error(
                "[%s] Failed to send image attachment, falling back to URL: %s",
                self.name,
                e,
                exc_info=True,
            )
            return await super().send_image(chat_id, image_url, caption, reply_to)

    async def send_animation(
        self,
        chat_id: str,
        animation_url: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """以原生 Discord 文件附件发送动态 GIF。"""
        if not self._client:
            return SendResult(success=False, error="Not connected")

        if not is_safe_url(animation_url):
            logger.warning("[%s] Blocked unsafe animation URL during Discord send_animation", self.name)
            return await super().send_animation(chat_id, animation_url, caption, reply_to, metadata=metadata)

        try:
            import aiohttp

            channel = self._client.get_channel(int(chat_id))
            if not channel:
                channel = await self._client.fetch_channel(int(chat_id))
            if not channel:
                return SendResult(success=False, error=f"Channel {chat_id} not found")

            # 下载 GIF 并以 Discord 文件附件发送
            # （Discord 会将 .gif 附件内联渲染为自动播放的动画）
            from gateway.platforms.base import resolve_proxy_url, proxy_kwargs_for_aiohttp
            _proxy = resolve_proxy_url(platform_env_var="DISCORD_PROXY")
            _sess_kw, _req_kw = proxy_kwargs_for_aiohttp(_proxy)
            async with aiohttp.ClientSession(**_sess_kw) as session:
                async with session.get(animation_url, timeout=aiohttp.ClientTimeout(total=30), **_req_kw) as resp:
                    if resp.status != 200:
                        raise Exception(f"Failed to download animation: HTTP {resp.status}")

                    animation_data = await resp.read()

                    import io
                    file = discord.File(io.BytesIO(animation_data), filename="animation.gif")

                    msg = await channel.send(
                        content=caption if caption else None,
                        file=file,
                    )
                    return SendResult(success=True, message_id=str(msg.id))

        except ImportError:
            logger.warning(
                "[%s] aiohttp not installed, falling back to URL. Run: pip install aiohttp",
                self.name,
                exc_info=True,
            )
            return await super().send_animation(chat_id, animation_url, caption, reply_to, metadata=metadata)
        except Exception as e:  # pragma: no cover - defensive logging
            logger.error(
                "[%s] Failed to send animation attachment, falling back to URL: %s",
                self.name,
                e,
                exc_info=True,
            )
            return await super().send_animation(chat_id, animation_url, caption, reply_to, metadata=metadata)

    async def send_video(
        self,
        chat_id: str,
        video_path: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """以原生 Discord 附件发送本地视频文件。"""
        try:
            return await self._send_file_attachment(chat_id, video_path, caption)
        except FileNotFoundError:
            return SendResult(success=False, error=f"Video file not found: {video_path}")
        except Exception as e:  # pragma: no cover - defensive logging
            logger.error("[%s] Failed to send local video, falling back to base adapter: %s", self.name, e, exc_info=True)
            return await super().send_video(chat_id, video_path, caption, reply_to, metadata=metadata)

    async def send_document(
        self,
        chat_id: str,
        file_path: str,
        caption: Optional[str] = None,
        file_name: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """以原生 Discord 附件发送任意文件。"""
        try:
            return await self._send_file_attachment(chat_id, file_path, caption, file_name=file_name)
        except FileNotFoundError:
            return SendResult(success=False, error=f"File not found: {file_path}")
        except Exception as e:  # pragma: no cover - defensive logging
            logger.error("[%s] Failed to send document, falling back to base adapter: %s", self.name, e, exc_info=True)
            return await super().send_document(chat_id, file_path, caption, file_name, reply_to, metadata=metadata)

    async def send_typing(self, chat_id: str, metadata=None) -> None:
        """为频道启动持续输入指示器。

        Discord 的 TYPING_START 网关事件在私聊中对机器人不可靠。
        因此启动一个后台循环，每 8 秒调用打字端点一次
        （打字指示器持续约 10 秒）。当 stop_typing() 被调用
        （回复发送后）时循环被取消。
        """
        if not self._client:
            return
        # 不启动重复的循环
        if chat_id in self._typing_tasks:
            return

        async def _typing_loop() -> None:
            try:
                while True:
                    try:
                        route = discord.http.Route(
                            "POST", "/channels/{channel_id}/typing",
                            channel_id=chat_id,
                        )
                        await self._client.http.request(route)
                    except asyncio.CancelledError:
                        return
                    except Exception as e:
                        logger.debug("Discord typing indicator failed for %s: %s", chat_id, e)
                        return
                    await asyncio.sleep(8)
            except asyncio.CancelledError:
                pass

        self._typing_tasks[chat_id] = asyncio.create_task(_typing_loop())

    async def stop_typing(self, chat_id: str) -> None:
        """停止频道的持续输入指示器。"""
        task = self._typing_tasks.pop(chat_id, None)
        if task:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        """获取 Discord 频道的信息。"""
        if not self._client:
            return {"name": "Unknown", "type": "dm"}

        try:
            channel = self._client.get_channel(int(chat_id))
            if not channel:
                channel = await self._client.fetch_channel(int(chat_id))

            if not channel:
                return {"name": str(chat_id), "type": "dm"}

            # 判断频道类型
            if isinstance(channel, discord.DMChannel):
                chat_type = "dm"
                name = channel.recipient.name if channel.recipient else str(chat_id)
            elif isinstance(channel, discord.Thread):
                chat_type = "thread"
                name = channel.name
            elif isinstance(channel, discord.TextChannel):
                chat_type = "channel"
                name = f"#{channel.name}"
                if channel.guild:
                    name = f"{channel.guild.name} / {name}"
            else:
                chat_type = "channel"
                name = getattr(channel, "name", str(chat_id))

            return {
                "name": name,
                "type": chat_type,
                "guild_id": str(channel.guild.id) if hasattr(channel, "guild") and channel.guild else None,
                "guild_name": channel.guild.name if hasattr(channel, "guild") and channel.guild else None,
            }
        except Exception as e:  # pragma: no cover - defensive logging
            logger.error("[%s] Failed to get chat info for %s: %s", self.name, chat_id, e, exc_info=True)
            return {"name": str(chat_id), "type": "dm", "error": str(e)}

    async def _resolve_allowed_usernames(self) -> None:
        """
        将 DISCORD_ALLOWED_USERS 中的非数字条目解析为 Discord 用户 ID。

        用户可以指定用户名（如 "teknium"）或显示名称来替代
        原始数字 ID。解析后，环境变量和内部集合将更新，
        使授权检查仅使用 ID。
        """
        if not self._allowed_user_ids or not self._client:
            return

        numeric_ids = set()
        to_resolve = set()

        for entry in self._allowed_user_ids:
            if entry.isdigit():
                numeric_ids.add(entry)
            else:
                to_resolve.add(entry.lower())

        if not to_resolve:
            return

        print(f"[{self.name}] Resolving {len(to_resolve)} username(s): {', '.join(to_resolve)}")
        resolved_count = 0

        for guild in self._client.guilds:
            # 拉取完整成员列表（需要 members intent）
            try:
                members = guild.members
                if len(members) < guild.member_count:
                    members = [m async for m in guild.fetch_members(limit=None)]
            except Exception as e:
                logger.warning("Failed to fetch members for guild %s: %s", guild.name, e)
                continue

            for member in members:
                name_lower = member.name.lower()
                display_lower = member.display_name.lower()
                global_lower = (member.global_name or "").lower()

                matched = name_lower in to_resolve or display_lower in to_resolve or global_lower in to_resolve
                if matched:
                    uid = str(member.id)
                    numeric_ids.add(uid)
                    resolved_count += 1
                    matched_name = name_lower if name_lower in to_resolve else (
                        display_lower if display_lower in to_resolve else global_lower
                    )
                    to_resolve.discard(matched_name)
                    print(f"[{self.name}] Resolved '{matched_name}' -> {uid} ({member.name}#{member.discriminator})")

            if not to_resolve:
                break

        if to_resolve:
            print(f"[{self.name}] Could not resolve usernames: {', '.join(to_resolve)}")

        # 更新内部集合和环境变量，使网关授权检查使用 ID
        self._allowed_user_ids = numeric_ids
        os.environ["DISCORD_ALLOWED_USERS"] = ",".join(sorted(numeric_ids))
        if resolved_count:
            print(f"[{self.name}] Updated DISCORD_ALLOWED_USERS with {resolved_count} resolved ID(s)")

    def format_message(self, content: str) -> str:
        """
        格式化 Discord 消息。

        Discord 使用自己的 Markdown 变体。
        """
        # Discord Markdown 相当标准，不需要特殊转义
        return content

    async def _run_simple_slash(
        self,
        interaction: discord.Interaction,
        command_text: str,
        followup_msg: str | None = None,
    ) -> None:
        """简单斜杠命令的通用处理器，分发命令字符串。

        延迟交互响应（显示"正在思考..."），分发命令，
        然后清理延迟响应。如果提供了 *followup_msg*，
        "正在思考..."指示器将替换为该文本；否则将被
        删除以避免频道杂乱。
        """
        await interaction.response.defer(ephemeral=True)
        event = self._build_slash_event(interaction, command_text)
        await self.handle_message(event)
        try:
            if followup_msg:
                await interaction.edit_original_response(content=followup_msg)
            else:
                await interaction.delete_original_response()
        except Exception as e:
            logger.debug("Discord interaction cleanup failed: %s", e)

    def _register_slash_commands(self) -> None:
        """在命令树上注册 Discord 斜杠命令。"""
        if not self._client:
            return

        tree = self._client.tree

        @tree.command(name="new", description="Start a new conversation")
        async def slash_new(interaction: discord.Interaction):
            await self._run_simple_slash(interaction, "/reset", "New conversation started~")

        @tree.command(name="reset", description="Reset your Hermes session")
        async def slash_reset(interaction: discord.Interaction):
            await self._run_simple_slash(interaction, "/reset", "Session reset~")

        @tree.command(name="model", description="Show or change the model")
        @discord.app_commands.describe(name="Model name (e.g. anthropic/claude-sonnet-4). Leave empty to see current.")
        async def slash_model(interaction: discord.Interaction, name: str = ""):
            await self._run_simple_slash(interaction, f"/model {name}".strip())

        @tree.command(name="reasoning", description="Show or change reasoning effort")
        @discord.app_commands.describe(effort="Reasoning effort: none, minimal, low, medium, high, or xhigh.")
        async def slash_reasoning(interaction: discord.Interaction, effort: str = ""):
            await self._run_simple_slash(interaction, f"/reasoning {effort}".strip())

        @tree.command(name="personality", description="Set a personality")
        @discord.app_commands.describe(name="Personality name. Leave empty to list available.")
        async def slash_personality(interaction: discord.Interaction, name: str = ""):
            await self._run_simple_slash(interaction, f"/personality {name}".strip())

        @tree.command(name="retry", description="Retry your last message")
        async def slash_retry(interaction: discord.Interaction):
            await self._run_simple_slash(interaction, "/retry", "Retrying~")

        @tree.command(name="undo", description="Remove the last exchange")
        async def slash_undo(interaction: discord.Interaction):
            await self._run_simple_slash(interaction, "/undo")

        @tree.command(name="status", description="Show Hermes session status")
        async def slash_status(interaction: discord.Interaction):
            await self._run_simple_slash(interaction, "/status", "Status sent~")

        @tree.command(name="sethome", description="Set this chat as the home channel")
        async def slash_sethome(interaction: discord.Interaction):
            await self._run_simple_slash(interaction, "/sethome")

        @tree.command(name="stop", description="Stop the running Hermes agent")
        async def slash_stop(interaction: discord.Interaction):
            await self._run_simple_slash(interaction, "/stop", "Stop requested~")

        @tree.command(name="compress", description="Compress conversation context")
        async def slash_compress(interaction: discord.Interaction):
            await self._run_simple_slash(interaction, "/compress")

        @tree.command(name="title", description="Set or show the session title")
        @discord.app_commands.describe(name="Session title. Leave empty to show current.")
        async def slash_title(interaction: discord.Interaction, name: str = ""):
            await self._run_simple_slash(interaction, f"/title {name}".strip())

        @tree.command(name="resume", description="Resume a previously-named session")
        @discord.app_commands.describe(name="Session name to resume. Leave empty to list sessions.")
        async def slash_resume(interaction: discord.Interaction, name: str = ""):
            await self._run_simple_slash(interaction, f"/resume {name}".strip())

        @tree.command(name="usage", description="Show token usage for this session")
        async def slash_usage(interaction: discord.Interaction):
            await self._run_simple_slash(interaction, "/usage")

        @tree.command(name="provider", description="Show available providers")
        async def slash_provider(interaction: discord.Interaction):
            await self._run_simple_slash(interaction, "/provider")

        @tree.command(name="help", description="Show available commands")
        async def slash_help(interaction: discord.Interaction):
            await self._run_simple_slash(interaction, "/help")

        @tree.command(name="insights", description="Show usage insights and analytics")
        @discord.app_commands.describe(days="Number of days to analyze (default: 7)")
        async def slash_insights(interaction: discord.Interaction, days: int = 7):
            await self._run_simple_slash(interaction, f"/insights {days}")

        @tree.command(name="reload-mcp", description="Reload MCP servers from config")
        async def slash_reload_mcp(interaction: discord.Interaction):
            await self._run_simple_slash(interaction, "/reload-mcp")

        @tree.command(name="voice", description="Toggle voice reply mode")
        @discord.app_commands.describe(mode="Voice mode: on, off, tts, channel, leave, or status")
        @discord.app_commands.choices(mode=[
            discord.app_commands.Choice(name="channel — join your voice channel", value="channel"),
            discord.app_commands.Choice(name="leave — leave voice channel", value="leave"),
            discord.app_commands.Choice(name="on — voice reply to voice messages", value="on"),
            discord.app_commands.Choice(name="tts — voice reply to all messages", value="tts"),
            discord.app_commands.Choice(name="off — text only", value="off"),
            discord.app_commands.Choice(name="status — show current mode", value="status"),
        ])
        async def slash_voice(interaction: discord.Interaction, mode: str = ""):
            await self._run_simple_slash(interaction, f"/voice {mode}".strip())

        @tree.command(name="update", description="Update Hermes Agent to the latest version")
        async def slash_update(interaction: discord.Interaction):
            await self._run_simple_slash(interaction, "/update", "Update initiated~")

        @tree.command(name="restart", description="Gracefully restart the Hermes gateway")
        async def slash_restart(interaction: discord.Interaction):
            await self._run_simple_slash(interaction, "/restart", "Restart requested~")

        @tree.command(name="approve", description="Approve a pending dangerous command")
        @discord.app_commands.describe(scope="Optional: 'all', 'session', 'always', 'all session', 'all always'")
        async def slash_approve(interaction: discord.Interaction, scope: str = ""):
            await self._run_simple_slash(interaction, f"/approve {scope}".strip())

        @tree.command(name="deny", description="Deny a pending dangerous command")
        @discord.app_commands.describe(scope="Optional: 'all' to deny all pending commands")
        async def slash_deny(interaction: discord.Interaction, scope: str = ""):
            await self._run_simple_slash(interaction, f"/deny {scope}".strip())

        @tree.command(name="thread", description="Create a new thread and start a Hermes session in it")
        @discord.app_commands.describe(
            name="Thread name",
            message="Optional first message to send to Hermes in the thread",
            auto_archive_duration="Auto-archive in minutes (60, 1440, 4320, 10080)",
        )
        async def slash_thread(
            interaction: discord.Interaction,
            name: str,
            message: str = "",
            auto_archive_duration: int = 1440,
        ):
            await interaction.response.defer(ephemeral=True)
            await self._handle_thread_create_slash(interaction, name, message, auto_archive_duration)

        @tree.command(name="queue", description="Queue a prompt for the next turn (doesn't interrupt)")
        @discord.app_commands.describe(prompt="The prompt to queue")
        async def slash_queue(interaction: discord.Interaction, prompt: str):
            await self._run_simple_slash(interaction, f"/queue {prompt}", "Queued for the next turn.")

        @tree.command(name="background", description="Run a prompt in the background")
        @discord.app_commands.describe(prompt="The prompt to run in the background")
        async def slash_background(interaction: discord.Interaction, prompt: str):
            await self._run_simple_slash(interaction, f"/background {prompt}", "Background task started~")

        @tree.command(name="btw", description="Ephemeral side question using session context")
        @discord.app_commands.describe(question="Your side question (no tools, not persisted)")
        async def slash_btw(interaction: discord.Interaction, question: str):
            await self._run_simple_slash(interaction, f"/btw {question}")

        # ── 自动注册命令树上尚未存在的网关可用命令 ──
        # 这确保 hermes_cli/commands.py 的 COMMAND_REGISTRY 中
        # 新增的命令自动显示为 Discord 斜杠命令，
        # 无需在此手动添加条目。
        try:
            from hermes_cli.commands import COMMAND_REGISTRY, _is_gateway_available, _resolve_config_gates

            already_registered = set()
            try:
                already_registered = {cmd.name for cmd in tree.get_commands()}
            except Exception:
                pass

            config_overrides = _resolve_config_gates()

            for cmd_def in COMMAND_REGISTRY:
                if not _is_gateway_available(cmd_def, config_overrides):
                    continue
                # Discord 命令名称：小写，允许连字符，最长 32 字符。
                discord_name = cmd_def.name.lower()[:32]
                if discord_name in already_registered:
                    continue
                # 跳过与已注册名称重叠的别名
                # （显式注册命令的别名已在上方处理）。
                desc = (cmd_def.description or f"Run /{cmd_def.name}")[:100]
                has_args = bool(cmd_def.args_hint)

                if has_args:
                    # 命令接受可选参数 —— 创建带可选
                    # ``args`` 字符串参数的处理器。
                    def _make_args_handler(_name: str, _hint: str):
                        @discord.app_commands.describe(args=f"Arguments: {_hint}"[:100])
                        async def _handler(interaction: discord.Interaction, args: str = ""):
                            await self._run_simple_slash(
                                interaction, f"/{_name} {args}".strip()
                            )
                        _handler.__name__ = f"auto_slash_{_name.replace('-', '_')}"
                        return _handler

                    handler = _make_args_handler(cmd_def.name, cmd_def.args_hint)
                else:
                    # 无参数命令。
                    def _make_simple_handler(_name: str):
                        async def _handler(interaction: discord.Interaction):
                            await self._run_simple_slash(interaction, f"/{_name}")
                        _handler.__name__ = f"auto_slash_{_name.replace('-', '_')}"
                        return _handler

                    handler = _make_simple_handler(cmd_def.name)

                auto_cmd = discord.app_commands.Command(
                    name=discord_name,
                    description=desc,
                    callback=handler,
                )
                try:
                    tree.add_command(auto_cmd)
                    already_registered.add(discord_name)
                except Exception:
                    # 静默跳过注册失败的命令（例如
                    # 与子命令组名称冲突）。
                    pass

            logger.debug(
                "Discord auto-registered %d commands from COMMAND_REGISTRY",
                len(already_registered),
            )
        except Exception as e:
            logger.warning("Discord auto-register from COMMAND_REGISTRY failed: %s", e)

        # 将技能注册在单个 /skill 命令组下，使用分类子命令组。
        # 这使用 1 个顶级槽位而不是 N 个，
        # 支持最多 25 个分类 x 25 个技能 = 625 个技能。
        self._register_skill_group(tree)

    def _register_skill_group(self, tree) -> None:
        """注册带分类子命令组的 ``/skill`` 命令组。

        技能按 ``SKILLS_DIR`` 下的目录分类组织。
        每个分类成为一个子命令组；根级别的技能成为
        直接子命令。Discord 支持 25 个子命令组 x 25 个
        子命令 = 625 个技能 —— 远超旧的 100 命令上限。
        """
        try:
            from hermes_cli.commands import discord_skill_commands_by_category

            existing_names = set()
            try:
                existing_names = {cmd.name for cmd in tree.get_commands()}
            except Exception:
                pass

            categories, uncategorized, hidden = discord_skill_commands_by_category(
                reserved_names=existing_names,
            )

            if not categories and not uncategorized:
                return

            skill_group = discord.app_commands.Group(
                name="skill",
                description="Run a Hermes skill",
            )

            # ── 辅助函数：为技能命令键构建回调 ──
            def _make_handler(_key: str):
                @discord.app_commands.describe(args="Optional arguments for the skill")
                async def _handler(interaction: discord.Interaction, args: str = ""):
                    await self._run_simple_slash(interaction, f"{_key} {args}".strip())
                _handler.__name__ = f"skill_{_key.lstrip('/').replace('-', '_')}"
                return _handler

            # ── 未分类（根级别）技能 -> 直接子命令 ──
            for discord_name, description, cmd_key in uncategorized:
                cmd = discord.app_commands.Command(
                    name=discord_name,
                    description=description or f"Run the {discord_name} skill",
                    callback=_make_handler(cmd_key),
                )
                skill_group.add_command(cmd)

            # ── 分类子命令组 ──
            for cat_name in sorted(categories):
                cat_desc = f"{cat_name.replace('-', ' ').title()} skills"
                if len(cat_desc) > 100:
                    cat_desc = cat_desc[:97] + "..."
                cat_group = discord.app_commands.Group(
                    name=cat_name,
                    description=cat_desc,
                    parent=skill_group,
                )
                for discord_name, description, cmd_key in categories[cat_name]:
                    cmd = discord.app_commands.Command(
                        name=discord_name,
                        description=description or f"Run the {discord_name} skill",
                        callback=_make_handler(cmd_key),
                    )
                    cat_group.add_command(cmd)

            tree.add_command(skill_group)

            total = sum(len(v) for v in categories.values()) + len(uncategorized)
            logger.info(
                "[%s] Registered /skill group: %d skill(s) across %d categories"
                " + %d uncategorized",
                self.name, total, len(categories), len(uncategorized),
            )
            if hidden:
                logger.warning(
                    "[%s] %d skill(s) not registered (Discord subcommand limits)",
                    self.name, hidden,
                )
        except Exception as exc:
            logger.warning("[%s] Failed to register /skill group: %s", self.name, exc)

    def _build_slash_event(self, interaction: discord.Interaction, text: str) -> MessageEvent:
        """根据 Discord 斜杠命令交互构建 MessageEvent。"""
        is_dm = isinstance(interaction.channel, discord.DMChannel)
        is_thread = isinstance(interaction.channel, discord.Thread)
        thread_id = None

        if is_dm:
            chat_type = "dm"
        elif is_thread:
            chat_type = "thread"
            thread_id = str(interaction.channel_id)
        else:
            chat_type = "group"

        chat_name = ""
        if not is_dm and hasattr(interaction.channel, "name"):
            chat_name = interaction.channel.name
            if hasattr(interaction.channel, "guild") and interaction.channel.guild:
                chat_name = f"{interaction.channel.guild.name} / #{chat_name}"

        # 获取频道主题（如果可用）。
        # 对于论坛帖子，继承父级论坛的主题。
        chat_topic = self._get_effective_topic(interaction.channel, is_thread=is_thread)

        source = self.build_source(
            chat_id=str(interaction.channel_id),
            chat_name=chat_name,
            chat_type=chat_type,
            user_id=str(interaction.user.id),
            user_name=interaction.user.display_name,
            thread_id=thread_id,
            chat_topic=chat_topic,
        )

        msg_type = MessageType.COMMAND if text.startswith("/") else MessageType.TEXT
        channel_id = str(interaction.channel_id)
        parent_id = str(getattr(getattr(interaction, "channel", None), "parent_id", "") or "")
        return MessageEvent(
            text=text,
            message_type=msg_type,
            source=source,
            raw_message=interaction,
            channel_prompt=self._resolve_channel_prompt(channel_id, parent_id or None),
        )

    # ------------------------------------------------------------------
    # 线程创建辅助方法
    # ------------------------------------------------------------------

    async def _handle_thread_create_slash(
        self,
        interaction: discord.Interaction,
        name: str,
        message: str = "",
        auto_archive_duration: int = 1440,
    ) -> None:
        """从斜杠命令创建 Discord 线程并在其中启动会话。"""
        result = await self._create_thread(
            interaction,
            name=name,
            message=message,
            auto_archive_duration=auto_archive_duration,
        )

        if not result.get("success"):
            error = result.get("error", "unknown error")
            await interaction.followup.send(f"Failed to create thread: {error}", ephemeral=True)
            return

        thread_id = result.get("thread_id")
        thread_name = result.get("thread_name") or name

        # 告知用户线程的位置
        link = f"<#{thread_id}>" if thread_id else f"**{thread_name}**"
        await interaction.followup.send(f"Created thread {link}", ephemeral=True)

        # 记录线程参与状态，后续消息无需 @提及
        if thread_id:
            self._threads.mark(thread_id)

        # 如果提供了消息内容，则在线程中启动新的 Hermes 会话
        starter = (message or "").strip()
        if starter and thread_id:
            await self._dispatch_thread_session(interaction, thread_id, thread_name, starter)

    async def _dispatch_thread_session(
        self,
        interaction: discord.Interaction,
        thread_id: str,
        thread_name: str,
        text: str,
    ) -> None:
        """构建指向线程的 MessageEvent 并通过 handle_message 分发。"""
        guild_name = ""
        if hasattr(interaction, "guild") and interaction.guild:
            guild_name = interaction.guild.name

        chat_name = f"{guild_name} / {thread_name}" if guild_name else thread_name

        # 当线程创建在论坛频道内时，继承论坛主题。
        _chan = getattr(interaction, "channel", None)
        chat_topic = self._get_effective_topic(_chan, is_thread=True) if _chan else None

        source = self.build_source(
            chat_id=thread_id,
            chat_name=chat_name,
            chat_type="thread",
            user_id=str(interaction.user.id),
            user_name=interaction.user.display_name,
            thread_id=thread_id,
            chat_topic=chat_topic,
        )

        _parent_channel = self._thread_parent_channel(getattr(interaction, "channel", None))
        _parent_id = str(getattr(_parent_channel, "id", "") or "")
        _skills = self._resolve_channel_skills(thread_id, _parent_id or None)
        _channel_prompt = self._resolve_channel_prompt(thread_id, _parent_id or None)
        event = MessageEvent(
            text=text,
            message_type=MessageType.TEXT,
            source=source,
            raw_message=interaction,
            auto_skill=_skills,
            channel_prompt=_channel_prompt,
        )
        await self.handle_message(event)

    def _resolve_channel_skills(self, channel_id: str, parent_id: str | None = None) -> list[str] | None:
        """查找 Discord 频道/论坛线程的自动技能绑定。

        配置格式（在平台 extra 中）：
            channel_skill_bindings:
              - id: "123456"
                skills: ["skill-a", "skill-b"]
        同时检查 parent_id，使论坛线程继承论坛的绑定。
        """
        bindings = self.config.extra.get("channel_skill_bindings", [])
        if not bindings:
            return None
        ids_to_check = {channel_id}
        if parent_id:
            ids_to_check.add(parent_id)
        for entry in bindings:
            entry_id = str(entry.get("id", ""))
            if entry_id in ids_to_check:
                skills = entry.get("skills") or entry.get("skill")
                if isinstance(skills, str):
                    return [skills]
                if isinstance(skills, list) and skills:
                    return list(dict.fromkeys(skills))  # 去重，保留顺序
        return None

    def _resolve_channel_prompt(self, channel_id: str, parent_id: str | None = None) -> str | None:
        """解析 Discord 按频道配置的 prompt，精确频道优先于其父级频道。"""
        from gateway.platforms.base import resolve_channel_prompt
        return resolve_channel_prompt(self.config.extra, channel_id, parent_id)

    def _thread_parent_channel(self, channel: Any) -> Any:
        """在从线程调用时返回父级文字频道。"""
        return getattr(channel, "parent", None) or channel

    async def _resolve_interaction_channel(self, interaction: discord.Interaction) -> Optional[Any]:
        """返回交互频道，如果载荷不完整则进行拉取。"""
        channel = getattr(interaction, "channel", None)
        if channel is not None:
            return channel
        if not self._client:
            return None
        channel_id = getattr(interaction, "channel_id", None)
        if channel_id is None:
            return None
        channel = self._client.get_channel(int(channel_id))
        if channel is not None:
            return channel
        try:
            return await self._client.fetch_channel(int(channel_id))
        except Exception:
            return None

    async def _create_thread(
        self,
        interaction: discord.Interaction,
        *,
        name: str,
        message: str = "",
        auto_archive_duration: int = 1440,
    ) -> Dict[str, Any]:
        """在当前 Discord 频道中创建线程。

        首先尝试 ``parent_channel.create_thread()``。如果 Discord 拒绝
        （例如权限问题），则回退到发送种子消息并从中创建线程。
        """
        name = (name or "").strip()
        if not name:
            return {"error": "Thread name is required."}

        if auto_archive_duration not in VALID_THREAD_AUTO_ARCHIVE_MINUTES:
            allowed = ", ".join(str(v) for v in sorted(VALID_THREAD_AUTO_ARCHIVE_MINUTES))
            return {"error": f"auto_archive_duration must be one of: {allowed}."}

        channel = await self._resolve_interaction_channel(interaction)
        if channel is None:
            return {"error": "Could not resolve the current Discord channel."}
        if isinstance(channel, discord.DMChannel):
            return {"error": "Discord threads can only be created inside server text channels, not DMs."}

        parent_channel = self._thread_parent_channel(channel)
        if parent_channel is None:
            return {"error": "Could not determine a parent text channel for the new thread."}

        display_name = getattr(getattr(interaction, "user", None), "display_name", None) or "unknown user"
        reason = f"Requested by {display_name} via /thread"
        starter_message = (message or "").strip()

        try:
            thread = await parent_channel.create_thread(
                name=name,
                auto_archive_duration=auto_archive_duration,
                reason=reason,
            )
            if starter_message:
                await thread.send(starter_message)
            return {
                "success": True,
                "thread_id": str(thread.id),
                "thread_name": getattr(thread, "name", None) or name,
            }
        except Exception as direct_error:
            try:
                seed_content = starter_message or f"\U0001f9f5 Thread created by Hermes: **{name}**"
                seed_msg = await parent_channel.send(seed_content)
                thread = await seed_msg.create_thread(
                    name=name,
                    auto_archive_duration=auto_archive_duration,
                    reason=reason,
                )
                return {
                    "success": True,
                    "thread_id": str(thread.id),
                    "thread_name": getattr(thread, "name", None) or name,
                }
            except Exception as fallback_error:
                return {
                    "error": (
                        "Discord rejected direct thread creation and the fallback also failed. "
                        f"Direct error: {direct_error}. Fallback error: {fallback_error}"
                    )
                }

    # ------------------------------------------------------------------
    # 自动创建线程辅助方法
    # ------------------------------------------------------------------

    async def _auto_create_thread(self, message: 'DiscordMessage') -> Optional[Any]:
        """为自动线程功能从用户消息创建线程。

        成功时返回创建的线程对象，失败时返回 ``None``。
        """
        # 从消息内容构建短线程名称
        content = (message.content or "").strip()
        thread_name = content[:80] if content else "Hermes"
        if len(content) > 80:
            thread_name = thread_name[:77] + "..."

        try:
            thread = await message.create_thread(name=thread_name, auto_archive_duration=1440)
            return thread
        except Exception as e:
            logger.warning("[%s] Auto-thread creation failed: %s", self.name, e)
            return None

    async def send_exec_approval(
        self, chat_id: str, command: str, session_key: str,
        description: str = "dangerous command",
        metadata: Optional[dict] = None,
    ) -> SendResult:
        """
        发送基于按钮的危险命令执行审批提示。

        按钮调用 ``resolve_gateway_approval()`` 来解除等待中的
        agent 线程阻塞 —— 这替代了 Discord 上基于文本的 ``/approve`` 流程。
        """
        if not self._client or not DISCORD_AVAILABLE:
            return SendResult(success=False, error="Not connected")

        try:
            # 解析频道 —— 如果 metadata 中有 thread_id 则使用线程 ID
            target_id = chat_id
            if metadata and metadata.get("thread_id"):
                target_id = metadata["thread_id"]

            channel = self._client.get_channel(int(target_id))
            if not channel:
                channel = await self._client.fetch_channel(int(target_id))

            # Discord embed 描述限制为 4096 字符；在此限制内显示完整命令
            max_desc = 4088
            cmd_display = command if len(command) <= max_desc else command[: max_desc - 3] + "..."
            embed = discord.Embed(
                title="⚠️ Command Approval Required",
                description=f"```\n{cmd_display}\n```",
                color=discord.Color.orange(),
            )
            embed.add_field(name="Reason", value=description, inline=False)

            view = ExecApprovalView(
                session_key=session_key,
                allowed_user_ids=self._allowed_user_ids,
            )

            msg = await channel.send(embed=embed, view=view)
            return SendResult(success=True, message_id=str(msg.id))

        except Exception as e:
            return SendResult(success=False, error=str(e))

    async def send_update_prompt(
        self, chat_id: str, prompt: str, default: str = "",
        session_key: str = "",
    ) -> SendResult:
        """发送基于按钮的交互式更新提示（是 / 否）。

        当 ``hermes update --gateway`` 需要用户输入（恢复暂存、配置迁移）时，
        由网关 ``/update`` 监听器使用。
        """
        if not self._client or not DISCORD_AVAILABLE:
            return SendResult(success=False, error="Not connected")
        try:
            channel = self._client.get_channel(int(chat_id))
            if not channel:
                channel = await self._client.fetch_channel(int(chat_id))

            default_hint = f" (default: {default})" if default else ""
            embed = discord.Embed(
                title="⚕ Update Needs Your Input",
                description=f"{prompt}{default_hint}",
                color=discord.Color.gold(),
            )
            view = UpdatePromptView(
                session_key=session_key,
                allowed_user_ids=self._allowed_user_ids,
            )
            msg = await channel.send(embed=embed, view=view)
            return SendResult(success=True, message_id=str(msg.id))
        except Exception as e:
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
        """发送交互式下拉菜单模型选择器。

        两步下钻：提供商下拉菜单 -> 模型下拉菜单。
        使用 Discord 嵌入消息 + ``ModelPickerView`` 的下拉菜单。
        """
        if not self._client or not DISCORD_AVAILABLE:
            return SendResult(success=False, error="Not connected")

        try:
            # 解析目标频道（如果存在则使用 thread_id）
            target_id = chat_id
            if metadata and metadata.get("thread_id"):
                target_id = metadata["thread_id"]

            channel = self._client.get_channel(int(target_id))
            if not channel:
                channel = await self._client.fetch_channel(int(target_id))

            try:
                from hermes_cli.providers import get_label
                provider_label = get_label(current_provider)
            except Exception:
                provider_label = current_provider

            embed = discord.Embed(
                title="⚙ Model Configuration",
                description=(
                    f"Current model: `{current_model or 'unknown'}`\n"
                    f"Provider: {provider_label}\n\n"
                    f"Select a provider:"
                ),
                color=discord.Color.blue(),
            )

            view = ModelPickerView(
                providers=providers,
                current_model=current_model,
                current_provider=current_provider,
                session_key=session_key,
                on_model_selected=on_model_selected,
                allowed_user_ids=self._allowed_user_ids,
            )

            msg = await channel.send(embed=embed, view=view)
            return SendResult(success=True, message_id=str(msg.id))

        except Exception as e:
            logger.warning("[%s] send_model_picker failed: %s", self.name, e)
            return SendResult(success=False, error=str(e))

    def _get_parent_channel_id(self, channel: Any) -> Optional[str]:
        """返回 Discord 线程类频道的父频道 ID（如果存在）。"""
        parent = getattr(channel, "parent", None)
        if parent is not None and getattr(parent, "id", None) is not None:
            return str(parent.id)
        parent_id = getattr(channel, "parent_id", None)
        if parent_id is not None:
            return str(parent_id)
        return None

    def _is_forum_parent(self, channel: Any) -> bool:
        """尽力检查一个 Discord 频道是否为论坛频道。"""
        if channel is None:
            return False
        forum_cls = getattr(discord, "ForumChannel", None)
        if forum_cls and isinstance(channel, forum_cls):
            return True
        channel_type = getattr(channel, "type", None)
        if channel_type is not None:
            type_value = getattr(channel_type, "value", channel_type)
            if type_value == 15:
                return True
        return False

    def _get_effective_topic(self, channel: Any, is_thread: bool = False) -> Optional[str]:
        """返回频道主题，论坛线程回退到父级论坛的主题。"""
        topic = getattr(channel, "topic", None)
        if not topic and is_thread:
            parent = getattr(channel, "parent", None)
            if parent and self._is_forum_parent(parent):
                topic = getattr(parent, "topic", None)
        return topic

    def _format_thread_chat_name(self, thread: Any) -> str:
        """为 Discord 线程类频道构建可读的聊天名称，可用时包含论坛上下文。"""
        thread_name = getattr(thread, "name", None) or str(getattr(thread, "id", "thread"))
        parent = getattr(thread, "parent", None)
        guild = getattr(thread, "guild", None) or getattr(parent, "guild", None)
        guild_name = getattr(guild, "name", None)
        parent_name = getattr(parent, "name", None)

        if self._is_forum_parent(parent) and guild_name and parent_name:
            return f"{guild_name} / {parent_name} / {thread_name}"
        if parent_name and guild_name:
            return f"{guild_name} / #{parent_name} / {thread_name}"
        if parent_name:
            return f"{parent_name} / {thread_name}"
        return thread_name

    async def _handle_message(self, message: DiscordMessage) -> None:
        """处理传入的 Discord 消息。"""
        # 在服务器频道（非私聊）中，要求机器人被 @提及，
        # 除非该频道在免提及列表中，或消息在机器人已参与的线程中。
        #
        # 配置（均可通过 config.yaml 中的 discord.* 或 DISCORD_* 环境变量设置）：
        #   discord.require_mention: 服务器频道要求 @提及（默认：true）
        #   discord.free_response_channels: 机器人无需提及即可响应的频道 ID
        #   discord.ignored_channels: 机器人永不响应（即使被提及）的频道 ID
        #   discord.allowed_channels: 如果设置，机器人仅在这些频道中响应（白名单）
        #   discord.no_thread_channels: 机器人直接回复而不创建线程的频道 ID
        #   discord.auto_thread: 在频道中被 @提及时自动创建线程（默认：true）

        thread_id = None
        parent_channel_id = None
        is_thread = isinstance(message.channel, discord.Thread)
        if is_thread:
            thread_id = str(message.channel.id)
            parent_channel_id = self._get_parent_channel_id(message.channel)

        is_voice_linked_channel = False
        if not isinstance(message.channel, discord.DMChannel):
            channel_ids = {str(message.channel.id)}
            if parent_channel_id:
                channel_ids.add(parent_channel_id)

            # 检查允许的频道 - 如果设置了白名单，仅在这些频道中响应
            allowed_channels_raw = os.getenv("DISCORD_ALLOWED_CHANNELS", "")
            if allowed_channels_raw:
                allowed_channels = {ch.strip() for ch in allowed_channels_raw.split(",") if ch.strip()}
                if not (channel_ids & allowed_channels):
                    logger.debug("[%s] Ignoring message in non-allowed channel: %s", self.name, channel_ids)
                    return

            # 检查忽略的频道 - 即使被提及也永不响应
            ignored_channels_raw = os.getenv("DISCORD_IGNORED_CHANNELS", "")
            ignored_channels = {ch.strip() for ch in ignored_channels_raw.split(",") if ch.strip()}
            if channel_ids & ignored_channels:
                logger.debug("[%s] Ignoring message in ignored channel: %s", self.name, channel_ids)
                return

            free_channels_raw = os.getenv("DISCORD_FREE_RESPONSE_CHANNELS", "")
            free_channels = {ch.strip() for ch in free_channels_raw.split(",") if ch.strip()}
            if parent_channel_id:
                channel_ids.add(parent_channel_id)

            require_mention = os.getenv("DISCORD_REQUIRE_MENTION", "true").lower() not in ("false", "0", "no")
            # 语音关联的文字频道在语音活跃时作为免提及频道。
            # 仅绑定的精确频道享有豁免，同级线程不享有。
            voice_linked_ids = {str(ch_id) for ch_id in self._voice_text_channels.values()}
            current_channel_id = str(message.channel.id)
            is_voice_linked_channel = current_channel_id in voice_linked_ids
            is_free_channel = bool(channel_ids & free_channels) or is_voice_linked_channel

            # 如果消息在机器人之前已参与（自动创建或已回复过）的线程中，
            # 则跳过提及检查。
            in_bot_thread = is_thread and thread_id in self._threads

            if require_mention and not is_free_channel and not in_bot_thread:
                if self._client.user not in message.mentions:
                    return

            if self._client.user and self._client.user in message.mentions:
                message.content = message.content.replace(f"<@{self._client.user.id}>", "").strip()
                message.content = message.content.replace(f"<@!{self._client.user.id}>", "").strip()

        # 自动线程：启用后，对文字频道中的每个 @提及自动创建线程，
        # 使每个对话隔离（类似 Slack）。
        # 已在线程中或私聊中的消息不受影响。
        # no_thread_channels：机器人直接回复而不创建线程的频道。
        auto_threaded_channel = None
        if not is_thread and not isinstance(message.channel, discord.DMChannel):
            no_thread_channels_raw = os.getenv("DISCORD_NO_THREAD_CHANNELS", "")
            no_thread_channels = {ch.strip() for ch in no_thread_channels_raw.split(",") if ch.strip()}
            skip_thread = bool(channel_ids & no_thread_channels)
            auto_thread = os.getenv("DISCORD_AUTO_THREAD", "true").lower() in ("true", "1", "yes")
            if auto_thread and not skip_thread and not is_voice_linked_channel:
                thread = await self._auto_create_thread(message)
                if thread:
                    is_thread = True
                    thread_id = str(thread.id)
                    auto_threaded_channel = thread
                    self._threads.mark(thread_id)

        # 判断消息类型
        msg_type = MessageType.TEXT
        if message.content.startswith("/"):
            msg_type = MessageType.COMMAND
        elif message.attachments:
            # 检查附件类型
            for att in message.attachments:
                if att.content_type:
                    if att.content_type.startswith("image/"):
                        msg_type = MessageType.PHOTO
                    elif att.content_type.startswith("video/"):
                        msg_type = MessageType.VIDEO
                    elif att.content_type.startswith("audio/"):
                        msg_type = MessageType.AUDIO
                    else:
                        doc_ext = ""
                        if att.filename:
                            _, doc_ext = os.path.splitext(att.filename)
                            doc_ext = doc_ext.lower()
                        if doc_ext in SUPPORTED_DOCUMENT_TYPES:
                            msg_type = MessageType.DOCUMENT
                    break

        # 当自动线程功能生效时，将回复路由到新线程
        effective_channel = auto_threaded_channel or message.channel

        # 判断聊天类型
        if isinstance(message.channel, discord.DMChannel):
            chat_type = "dm"
            chat_name = message.author.name
        elif is_thread:
            chat_type = "thread"
            chat_name = self._format_thread_chat_name(effective_channel)
        else:
            chat_type = "group"
            chat_name = getattr(message.channel, "name", str(message.channel.id))
            if hasattr(message.channel, "guild") and message.channel.guild:
                chat_name = f"{message.channel.guild.name} / #{chat_name}"

        # 获取频道主题（如果可用 —— TextChannel 有主题，私聊/线程没有）。
        # 对于父级为论坛频道的线程，继承父级的主题，
        # 使论坛描述（如项目说明）出现在会话上下文中。
        chat_topic = self._get_effective_topic(message.channel, is_thread=is_thread)

        # 构建消息来源
        source = self.build_source(
            chat_id=str(effective_channel.id),
            chat_name=chat_name,
            chat_type=chat_type,
            user_id=str(message.author.id),
            user_name=message.author.display_name,
            thread_id=thread_id,
            chat_topic=chat_topic,
        )

        # 构建媒体 URL —— 将图片附件下载到本地缓存，使
        # 视觉工具可以可靠访问（Discord CDN URL 可能过期）。
        media_urls = []
        media_types = []
        pending_text_injection: Optional[str] = None
        for att in message.attachments:
            content_type = att.content_type or "unknown"
            if content_type.startswith("image/"):
                try:
                    # 从 content type 确定扩展名（image/png -> .png）
                    ext = "." + content_type.split("/")[-1].split(";")[0]
                    if ext not in (".jpg", ".jpeg", ".png", ".gif", ".webp"):
                        ext = ".jpg"
                    cached_path = await cache_image_from_url(att.url, ext=ext)
                    media_urls.append(cached_path)
                    media_types.append(content_type)
                    print(f"[Discord] Cached user image: {cached_path}", flush=True)
                except Exception as e:
                    print(f"[Discord] Failed to cache image attachment: {e}", flush=True)
                    # 如果缓存失败，回退到 CDN URL
                    media_urls.append(att.url)
                    media_types.append(content_type)
            elif content_type.startswith("audio/"):
                try:
                    ext = "." + content_type.split("/")[-1].split(";")[0]
                    if ext not in (".ogg", ".mp3", ".wav", ".webm", ".m4a"):
                        ext = ".ogg"
                    cached_path = await cache_audio_from_url(att.url, ext=ext)
                    media_urls.append(cached_path)
                    media_types.append(content_type)
                    print(f"[Discord] Cached user audio: {cached_path}", flush=True)
                except Exception as e:
                    print(f"[Discord] Failed to cache audio attachment: {e}", flush=True)
                    media_urls.append(att.url)
                    media_types.append(content_type)
            else:
                # 文档附件：下载、缓存，并可选注入文本内容
                ext = ""
                if att.filename:
                    _, ext = os.path.splitext(att.filename)
                    ext = ext.lower()
                if not ext and content_type:
                    mime_to_ext = {v: k for k, v in SUPPORTED_DOCUMENT_TYPES.items()}
                    ext = mime_to_ext.get(content_type, "")
                if ext not in SUPPORTED_DOCUMENT_TYPES:
                    logger.warning(
                        "[Discord] Unsupported document type '%s' (%s), skipping",
                        ext or "unknown", content_type,
                    )
                else:
                    MAX_DOC_BYTES = 32 * 1024 * 1024
                    if att.size and att.size > MAX_DOC_BYTES:
                        logger.warning(
                            "[Discord] Document too large (%s bytes), skipping: %s",
                            att.size, att.filename,
                        )
                    else:
                        try:
                            import aiohttp
                            from gateway.platforms.base import resolve_proxy_url, proxy_kwargs_for_aiohttp
                            _proxy = resolve_proxy_url(platform_env_var="DISCORD_PROXY")
                            _sess_kw, _req_kw = proxy_kwargs_for_aiohttp(_proxy)
                            async with aiohttp.ClientSession(**_sess_kw) as session:
                                async with session.get(
                                    att.url,
                                    timeout=aiohttp.ClientTimeout(total=30),
                                    **_req_kw,
                                ) as resp:
                                    if resp.status != 200:
                                        raise Exception(f"HTTP {resp.status}")
                                    raw_bytes = await resp.read()
                            cached_path = cache_document_from_bytes(
                                raw_bytes, att.filename or f"document{ext}"
                            )
                            doc_mime = SUPPORTED_DOCUMENT_TYPES[ext]
                            media_urls.append(cached_path)
                            media_types.append(doc_mime)
                            logger.info("[Discord] Cached user document: %s", cached_path)
                            # 为纯文本文档注入文本内容（上限 100 KB）
                            MAX_TEXT_INJECT_BYTES = 100 * 1024
                            if ext in (".md", ".txt", ".log") and len(raw_bytes) <= MAX_TEXT_INJECT_BYTES:
                                try:
                                    text_content = raw_bytes.decode("utf-8")
                                    display_name = att.filename or f"document{ext}"
                                    display_name = re.sub(r'[^\w.\- ]', '_', display_name)
                                    injection = f"[Content of {display_name}]:\n{text_content}"
                                    if pending_text_injection:
                                        pending_text_injection = f"{pending_text_injection}\n\n{injection}"
                                    else:
                                        pending_text_injection = injection
                                except UnicodeDecodeError:
                                    pass
                        except Exception as e:
                            logger.warning(
                                "[Discord] Failed to cache document %s: %s",
                                att.filename, e, exc_info=True,
                            )

        event_text = message.content
        if pending_text_injection:
            event_text = f"{pending_text_injection}\n\n{event_text}" if event_text else pending_text_injection

        # 纵深防御：防止空的用户消息进入会话
        # （当用户仅发送 @提及而无其他文本时可能发生）
        if not event_text or not event_text.strip():
            event_text = "(The user sent a message with no text content)"

        _chan = message.channel
        _parent_id = str(getattr(_chan, "parent_id", "") or "")
        _chan_id = str(getattr(_chan, "id", ""))
        _skills = self._resolve_channel_skills(_chan_id, _parent_id or None)
        _channel_prompt = self._resolve_channel_prompt(_chan_id, _parent_id or None)

        reply_to_id = None
        reply_to_text = None
        if message.reference:
            reply_to_id = str(message.reference.message_id)
            if message.reference.resolved:
                reply_to_text = getattr(message.reference.resolved, "content", None) or None

        event = MessageEvent(
            text=event_text,
            message_type=msg_type,
            source=source,
            raw_message=message,
            message_id=str(message.id),
            media_urls=media_urls,
            media_types=media_types,
            reply_to_message_id=reply_to_id,
            reply_to_text=reply_to_text,
            timestamp=message.created_at,
            auto_skill=_skills,
            channel_prompt=_channel_prompt,
        )

        # 记录线程参与状态，使机器人在已参与的线程中
        # 后续消息不需要 @提及。
        if thread_id:
            self._threads.mark(thread_id)

        # 仅对纯文本消息进行批处理 —— 命令、媒体等立即分发，
        # 因为它们不会被 Discord 客户端拆分。
        if msg_type == MessageType.TEXT and self._text_batch_delay_seconds > 0:
            self._enqueue_text_event(event)
        else:
            await self.handle_message(event)

    # ------------------------------------------------------------------
    # 文本消息聚合（处理 Discord 客户端拆分）
    # ------------------------------------------------------------------

    def _text_batch_key(self, event: MessageEvent) -> str:
        """文本消息批处理的会话范围键。"""
        from gateway.session import build_session_key
        return build_session_key(
            event.source,
            group_sessions_per_user=self.config.extra.get("group_sessions_per_user", True),
            thread_sessions_per_user=self.config.extra.get("thread_sessions_per_user", False),
        )

    def _enqueue_text_event(self, event: MessageEvent) -> None:
        """缓存文本事件并重置刷新定时器。

        当 Discord 以 2000 字符为界拆分长消息时，分片会在
        几百毫秒内到达。此方法在分发前将它们合并为单个事件。
        """
        key = self._text_batch_key(event)
        existing = self._pending_text_batches.get(key)
        chunk_len = len(event.text or "")
        if existing is None:
            event._last_chunk_len = chunk_len  # type: ignore[attr-defined]
            self._pending_text_batches[key] = event
        else:
            if event.text:
                existing.text = f"{existing.text}\n{event.text}" if existing.text else event.text
            existing._last_chunk_len = chunk_len  # type: ignore[attr-defined]
            if event.media_urls:
                existing.media_urls.extend(event.media_urls)
                existing.media_types.extend(event.media_types)

        prior_task = self._pending_text_batch_tasks.get(key)
        if prior_task and not prior_task.done():
            prior_task.cancel()
        self._pending_text_batch_tasks[key] = asyncio.create_task(
            self._flush_text_batch(key)
        )

    async def _flush_text_batch(self, key: str) -> None:
        """等待静默期结束后分发聚合的文本。

        当最新分片接近 Discord 的 2000 字符拆分点时使用更长的延迟，
        因为后续分片几乎必定会到来。
        """
        current_task = asyncio.current_task()
        try:
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
                "[Discord] Flushing text batch %s (%d chars)",
                key, len(event.text or ""),
            )
            await self.handle_message(event)
        finally:
            if self._pending_text_batch_tasks.get(key) is current_task:
                self._pending_text_batch_tasks.pop(key, None)


# ---------------------------------------------------------------------------
# Discord UI 组件（位于适配器类外部）
# ---------------------------------------------------------------------------

if DISCORD_AVAILABLE:

    class ExecApprovalView(discord.ui.View):
        """
        用于危险命令执行审批的交互式按钮视图。

        显示四个按钮：允许一次、允许本次会话、始终允许、拒绝。
        点击按钮调用 ``resolve_gateway_approval()`` 来解除等待中的
        agent 线程阻塞 —— 与文本 ``/approve`` 流程使用相同机制。
        仅允许列表中的用户可以点击。5 分钟后超时。
        """

        def __init__(self, session_key: str, allowed_user_ids: set):
            super().__init__(timeout=300)  # 5 分钟超时
            self.session_key = session_key
            self.allowed_user_ids = allowed_user_ids
            self.resolved = False

        def _check_auth(self, interaction: discord.Interaction) -> bool:
            """验证点击的用户是否已授权。"""
            if not self.allowed_user_ids:
                return True  # 无白名单 = 任何人都可以审批
            return str(interaction.user.id) in self.allowed_user_ids

        async def _resolve(
            self, interaction: discord.Interaction, choice: str,
            color: discord.Color, label: str,
        ):
            """通过网关审批队列解决审批请求并更新嵌入消息。"""
            if self.resolved:
                await interaction.response.send_message(
                    "This approval has already been resolved~", ephemeral=True
                )
                return

            if not self._check_auth(interaction):
                await interaction.response.send_message(
                    "You're not authorized to approve commands~", ephemeral=True
                )
                return

            self.resolved = True

            # 用审批决定更新嵌入消息
            embed = interaction.message.embeds[0] if interaction.message.embeds else None
            if embed:
                embed.color = color
                embed.set_footer(text=f"{label} by {interaction.user.display_name}")

            # 禁用所有按钮
            for child in self.children:
                child.disabled = True

            await interaction.response.edit_message(embed=embed, view=self)

            # 通过网关审批队列解除等待中的 agent 线程阻塞
            try:
                from tools.approval import resolve_gateway_approval
                count = resolve_gateway_approval(self.session_key, choice)
                logger.info(
                    "Discord button resolved %d approval(s) for session %s (choice=%s, user=%s)",
                    count, self.session_key, choice, interaction.user.display_name,
                )
            except Exception as exc:
                logger.error("Failed to resolve gateway approval from button: %s", exc)

        @discord.ui.button(label="Allow Once", style=discord.ButtonStyle.green)
        async def allow_once(
            self, interaction: discord.Interaction, button: discord.ui.Button
        ):
            await self._resolve(interaction, "once", discord.Color.green(), "Approved once")

        @discord.ui.button(label="Allow Session", style=discord.ButtonStyle.grey)
        async def allow_session(
            self, interaction: discord.Interaction, button: discord.ui.Button
        ):
            await self._resolve(interaction, "session", discord.Color.blue(), "Approved for session")

        @discord.ui.button(label="Always Allow", style=discord.ButtonStyle.blurple)
        async def allow_always(
            self, interaction: discord.Interaction, button: discord.ui.Button
        ):
            await self._resolve(interaction, "always", discord.Color.purple(), "Approved permanently")

        @discord.ui.button(label="Deny", style=discord.ButtonStyle.red)
        async def deny(
            self, interaction: discord.Interaction, button: discord.ui.Button
        ):
            await self._resolve(interaction, "deny", discord.Color.red(), "Denied")

        async def on_timeout(self):
            """处理视图超时 —— 禁用按钮并标记为已过期。"""
            self.resolved = True
            for child in self.children:
                child.disabled = True

    class UpdatePromptView(discord.ui.View):
        """用于 ``hermes update`` 提示的交互式 是/否 按钮。

        点击按钮将答案写入 ``.update_response``，以便
        分离的更新进程获取。仅授权用户可以点击。
        5 分钟后超时（更新进程侧也有 5 分钟超时）。
        """

        def __init__(self, session_key: str, allowed_user_ids: set):
            super().__init__(timeout=300)
            self.session_key = session_key
            self.allowed_user_ids = allowed_user_ids
            self.resolved = False

        def _check_auth(self, interaction: discord.Interaction) -> bool:
            if not self.allowed_user_ids:
                return True
            return str(interaction.user.id) in self.allowed_user_ids

        async def _respond(
            self, interaction: discord.Interaction, answer: str,
            color: discord.Color, label: str,
        ):
            if self.resolved:
                await interaction.response.send_message(
                    "Already answered~", ephemeral=True
                )
                return
            if not self._check_auth(interaction):
                await interaction.response.send_message(
                    "You're not authorized~", ephemeral=True
                )
                return

            self.resolved = True

            # 更新嵌入消息
            embed = interaction.message.embeds[0] if interaction.message.embeds else None
            if embed:
                embed.color = color
                embed.set_footer(text=f"{label} by {interaction.user.display_name}")

            for child in self.children:
                child.disabled = True
            await interaction.response.edit_message(embed=embed, view=self)

            # 写入响应文件
            try:
                from hermes_constants import get_hermes_home
                home = get_hermes_home()
                response_path = home / ".update_response"
                tmp = response_path.with_suffix(".tmp")
                tmp.write_text(answer)
                tmp.replace(response_path)
                logger.info(
                    "Discord update prompt answered '%s' by %s",
                    answer, interaction.user.display_name,
                )
            except Exception as exc:
                logger.error("Failed to write update response: %s", exc)

        @discord.ui.button(label="Yes", style=discord.ButtonStyle.green, emoji="✓")
        async def yes_btn(
            self, interaction: discord.Interaction, button: discord.ui.Button
        ):
            await self._respond(interaction, "y", discord.Color.green(), "Yes")

        @discord.ui.button(label="No", style=discord.ButtonStyle.red, emoji="✗")
        async def no_btn(
            self, interaction: discord.Interaction, button: discord.ui.Button
        ):
            await self._respond(interaction, "n", discord.Color.red(), "No")

        async def on_timeout(self):
            self.resolved = True
            for child in self.children:
                child.disabled = True

    class ModelPickerView(discord.ui.View):
        """交互式下拉菜单模型选择视图。

        两步下钻：提供商下拉菜单 -> 模型下拉菜单。
        在用户导航时原地编辑原始消息。
        2 分钟后超时。
        """

        def __init__(
            self,
            providers: list,
            current_model: str,
            current_provider: str,
            session_key: str,
            on_model_selected,
            allowed_user_ids: set,
        ):
            super().__init__(timeout=120)
            self.providers = providers
            self.current_model = current_model
            self.current_provider = current_provider
            self.session_key = session_key
            self.on_model_selected = on_model_selected
            self.allowed_user_ids = allowed_user_ids
            self.resolved = False
            self._selected_provider: str = ""

            self._build_provider_select()

        def _check_auth(self, interaction: discord.Interaction) -> bool:
            if not self.allowed_user_ids:
                return True
            return str(interaction.user.id) in self.allowed_user_ids

        def _build_provider_select(self):
            """构建提供商下拉菜单。"""
            self.clear_items()
            options = []
            for p in self.providers:
                count = p.get("total_models", len(p.get("models", [])))
                label = f"{p['name']} ({count} models)"
                desc = "current" if p.get("is_current") else None
                options.append(
                    discord.SelectOption(
                        label=label[:100],
                        value=p["slug"],
                        description=desc,
                    )
                )
            if not options:
                return

            select = discord.ui.Select(
                placeholder="Choose a provider...",
                options=options[:25],
                custom_id="model_provider_select",
            )
            select.callback = self._on_provider_selected
            self.add_item(select)

            cancel_btn = discord.ui.Button(
                label="Cancel", style=discord.ButtonStyle.red, custom_id="model_cancel"
            )
            cancel_btn.callback = self._on_cancel
            self.add_item(cancel_btn)

        def _build_model_select(self, provider_slug: str):
            """为指定提供商构建模型下拉菜单。"""
            self.clear_items()
            provider = next(
                (p for p in self.providers if p["slug"] == provider_slug), None
            )
            if not provider:
                return

            models = provider.get("models", [])
            options = []
            for model_id in models[:25]:
                short = model_id.split("/")[-1] if "/" in model_id else model_id
                options.append(
                    discord.SelectOption(
                        label=short[:100],
                        value=model_id[:100],
                    )
                )
            if not options:
                return

            select = discord.ui.Select(
                placeholder=f"Choose a model from {provider.get('name', provider_slug)}...",
                options=options,
                custom_id="model_model_select",
            )
            select.callback = self._on_model_selected
            self.add_item(select)

            back_btn = discord.ui.Button(
                label="◀ Back", style=discord.ButtonStyle.grey, custom_id="model_back"
            )
            back_btn.callback = self._on_back
            self.add_item(back_btn)

            cancel_btn = discord.ui.Button(
                label="Cancel", style=discord.ButtonStyle.red, custom_id="model_cancel2"
            )
            cancel_btn.callback = self._on_cancel
            self.add_item(cancel_btn)

        async def _on_provider_selected(self, interaction: discord.Interaction):
            if not self._check_auth(interaction):
                await interaction.response.send_message(
                    "You're not authorized~", ephemeral=True
                )
                return

            provider_slug = interaction.data["values"][0]
            self._selected_provider = provider_slug
            provider = next(
                (p for p in self.providers if p["slug"] == provider_slug), None
            )
            pname = provider.get("name", provider_slug) if provider else provider_slug

            self._build_model_select(provider_slug)

            total = provider.get("total_models", 0) if provider else 0
            shown = min(len(provider.get("models", [])), 25) if provider else 0
            extra = f"\n*{total - shown} more available — type `/model <name>` directly*" if total > shown else ""

            await interaction.response.edit_message(
                embed=discord.Embed(
                    title="⚙ Model Configuration",
                    description=f"Provider: **{pname}**\nSelect a model:{extra}",
                    color=discord.Color.blue(),
                ),
                view=self,
            )

        async def _on_model_selected(self, interaction: discord.Interaction):
            if self.resolved:
                await interaction.response.send_message(
                    "Already resolved~", ephemeral=True
                )
                return
            if not self._check_auth(interaction):
                await interaction.response.send_message(
                    "You're not authorized~", ephemeral=True
                )
                return

            self.resolved = True
            model_id = interaction.data["values"][0]

            try:
                result_text = await self.on_model_selected(
                    str(interaction.channel_id),
                    model_id,
                    self._selected_provider,
                )
            except Exception as exc:
                result_text = f"Error switching model: {exc}"

            self.clear_items()
            await interaction.response.edit_message(
                embed=discord.Embed(
                    title="⚙ Model Switched",
                    description=result_text,
                    color=discord.Color.green(),
                ),
                view=self,
            )

        async def _on_back(self, interaction: discord.Interaction):
            if not self._check_auth(interaction):
                await interaction.response.send_message(
                    "You're not authorized~", ephemeral=True
                )
                return

            self._build_provider_select()

            try:
                from hermes_cli.providers import get_label
                provider_label = get_label(self.current_provider)
            except Exception:
                provider_label = self.current_provider

            await interaction.response.edit_message(
                embed=discord.Embed(
                    title="⚙ Model Configuration",
                    description=(
                        f"Current model: `{self.current_model or 'unknown'}`\n"
                        f"Provider: {provider_label}\n\n"
                        f"Select a provider:"
                    ),
                    color=discord.Color.blue(),
                ),
                view=self,
            )

        async def _on_cancel(self, interaction: discord.Interaction):
            self.resolved = True
            self.clear_items()
            await interaction.response.edit_message(
                embed=discord.Embed(
                    title="⚙ Model Configuration",
                    description="Model selection cancelled.",
                    color=discord.Color.greyple(),
                ),
                view=self,
            )

        async def on_timeout(self):
            self.resolved = True
            self.clear_items()
