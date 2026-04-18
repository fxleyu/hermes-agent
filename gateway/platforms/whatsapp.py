"""
WhatsApp 平台适配器。

WhatsApp 集成比 Telegram/Discord 更复杂，原因如下：
- 个人账户没有官方 Bot API
- 商业 API 需要 Meta 商业认证
- 大多数方案使用基于 Web 的自动化

本适配器支持多种后端：
1. WhatsApp Business API（需要 Meta 认证）
2. whatsapp-web.js（通过 Node.js 子进程）— 用于个人账户
3. Baileys（通过 Node.js 子进程）— 个人账户的替代方案

为简化实现，我们采用通用接口，通过桥接模式
对接不同的后端。
"""

import asyncio
import json
import logging
import os
import platform
import re
import subprocess

_IS_WINDOWS = platform.system() == "Windows"
from pathlib import Path
from typing import Dict, Optional, Any

from hermes_constants import get_hermes_dir

logger = logging.getLogger(__name__)


def _kill_port_process(port: int) -> None:
    """终止监听指定 TCP 端口的所有进程。"""
    try:
        if _IS_WINDOWS:
            # 使用 netstat 查找绑定到此端口的 PID，然后用 taskkill 终止
            result = subprocess.run(
                ["netstat", "-ano", "-p", "TCP"],
                capture_output=True, text=True, timeout=5,
            )
            for line in result.stdout.splitlines():
                parts = line.split()
                if len(parts) >= 5 and parts[3] == "LISTENING":
                    local_addr = parts[1]
                    if local_addr.endswith(f":{port}"):
                        try:
                            subprocess.run(
                                ["taskkill", "/PID", parts[4], "/F"],
                                capture_output=True, timeout=5,
                            )
                        except subprocess.SubprocessError:
                            pass
        else:
            result = subprocess.run(
                ["fuser", f"{port}/tcp"],
                capture_output=True, timeout=5,
            )
            if result.returncode == 0:
                subprocess.run(
                    ["fuser", "-k", f"{port}/tcp"],
                    capture_output=True, timeout=5,
                )
    except Exception:
        pass

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import (
    BasePlatformAdapter,
    MessageEvent,
    MessageType,
    SendResult,
    SUPPORTED_DOCUMENT_TYPES,
    cache_image_from_url,
    cache_audio_from_url,
)


def check_whatsapp_requirements() -> bool:
    """
    检查 WhatsApp 依赖项是否可用。

    WhatsApp 的大多数实现方式需要 Node.js 桥接。
    """
    # 检查 Node.js 是否已安装
    try:
        result = subprocess.run(
            ["node", "--version"],
            capture_output=True,
            text=True,
            timeout=5
        )
        return result.returncode == 0
    except Exception:
        return False


class WhatsAppAdapter(BasePlatformAdapter):
    """
    WhatsApp 适配器。

    本实现使用简单的 HTTP 桥接模式，工作流程如下：
    1. Node.js 进程运行 WhatsApp Web 客户端
    2. 消息通过 HTTP/IPC 转发到本 Python 适配器
    3. 响应通过桥接返回

    实际的 Node.js 桥接实现可以有多种选择：
    - 基于 whatsapp-web.js
    - 基于 Baileys
    - 基于 Business API

    配置项：
    - bridge_script: Node.js 桥接脚本的路径
    - bridge_port: HTTP 通信端口（默认：3000）
    - session_path: WhatsApp 会话数据的存储路径
    """

    # WhatsApp 消息长度限制 - 实际用户体验限制，非协议最大值。
    # WhatsApp 允许约 65K 字符，但长消息在手机上难以阅读。
    MAX_MESSAGE_LENGTH = 4096

    # 相对于 hermes-agent 安装目录的默认桥接位置
    _DEFAULT_BRIDGE_DIR = Path(__file__).resolve().parents[2] / "scripts" / "whatsapp-bridge"

    def __init__(self, config: PlatformConfig):
        super().__init__(config, Platform.WHATSAPP)
        self._bridge_process: Optional[subprocess.Popen] = None
        self._bridge_port: int = config.extra.get("bridge_port", 3000)
        self._bridge_script: Optional[str] = config.extra.get(
            "bridge_script",
            str(self._DEFAULT_BRIDGE_DIR / "bridge.js"),
        )
        self._session_path: Path = Path(config.extra.get(
            "session_path",
            get_hermes_dir("platforms/whatsapp/session", "whatsapp/session")
        ))
        self._reply_prefix: Optional[str] = config.extra.get("reply_prefix")
        self._mention_patterns = self._compile_mention_patterns()
        self._message_queue: asyncio.Queue = asyncio.Queue()
        self._bridge_log_fh = None
        self._bridge_log: Optional[Path] = None
        self._poll_task: Optional[asyncio.Task] = None
        self._http_session: Optional["aiohttp.ClientSession"] = None

    def _whatsapp_require_mention(self) -> bool:
        configured = self.config.extra.get("require_mention")
        if configured is not None:
            if isinstance(configured, str):
                return configured.lower() in ("true", "1", "yes", "on")
            return bool(configured)
        return os.getenv("WHATSAPP_REQUIRE_MENTION", "false").lower() in ("true", "1", "yes", "on")

    def _whatsapp_free_response_chats(self) -> set[str]:
        raw = self.config.extra.get("free_response_chats")
        if raw is None:
            raw = os.getenv("WHATSAPP_FREE_RESPONSE_CHATS", "")
        if isinstance(raw, list):
            return {str(part).strip() for part in raw if str(part).strip()}
        return {part.strip() for part in str(raw).split(",") if part.strip()}

    def _compile_mention_patterns(self):
        patterns = self.config.extra.get("mention_patterns")
        if patterns is None:
            raw = os.getenv("WHATSAPP_MENTION_PATTERNS", "").strip()
            if raw:
                try:
                    patterns = json.loads(raw)
                except Exception:
                    patterns = [part.strip() for part in raw.splitlines() if part.strip()]
                    if not patterns:
                        patterns = [part.strip() for part in raw.split(",") if part.strip()]
        if patterns is None:
            return []
        if isinstance(patterns, str):
            patterns = [patterns]
        if not isinstance(patterns, list):
            logger.warning("[%s] whatsapp mention_patterns must be a list or string; got %s", self.name, type(patterns).__name__)
            return []

        compiled = []
        for pattern in patterns:
            if not isinstance(pattern, str) or not pattern.strip():
                continue
            try:
                compiled.append(re.compile(pattern, re.IGNORECASE))
            except re.error as exc:
                logger.warning("[%s] Invalid WhatsApp mention pattern %r: %s", self.name, pattern, exc)
        if compiled:
            logger.info("[%s] Loaded %d WhatsApp mention pattern(s)", self.name, len(compiled))
        return compiled

    @staticmethod
    def _normalize_whatsapp_id(value: Optional[str]) -> str:
        if not value:
            return ""
        normalized = str(value).strip()
        if ":" in normalized and "@" in normalized:
            normalized = normalized.replace(":", "@", 1)
        return normalized

    def _bot_ids_from_message(self, data: Dict[str, Any]) -> set[str]:
        bot_ids = set()
        for candidate in data.get("botIds") or []:
            normalized = self._normalize_whatsapp_id(candidate)
            if normalized:
                bot_ids.add(normalized)
        return bot_ids

    def _message_is_reply_to_bot(self, data: Dict[str, Any]) -> bool:
        quoted_participant = self._normalize_whatsapp_id(data.get("quotedParticipant"))
        if not quoted_participant:
            return False
        return quoted_participant in self._bot_ids_from_message(data)

    def _message_mentions_bot(self, data: Dict[str, Any]) -> bool:
        bot_ids = self._bot_ids_from_message(data)
        if not bot_ids:
            return False
        mentioned_ids = {
            nid
            for candidate in (data.get("mentionedIds") or [])
            if (nid := self._normalize_whatsapp_id(candidate))
        }
        if mentioned_ids & bot_ids:
            return True

        body = str(data.get("body") or "")
        lower_body = body.lower()
        for bot_id in bot_ids:
            bare_id = bot_id.split("@", 1)[0].lower()
            if bare_id and (f"@{bare_id}" in lower_body or bare_id in lower_body):
                return True
        return False

    def _message_matches_mention_patterns(self, data: Dict[str, Any]) -> bool:
        if not self._mention_patterns:
            return False
        body = str(data.get("body") or "")
        return any(pattern.search(body) for pattern in self._mention_patterns)

    def _clean_bot_mention_text(self, text: str, data: Dict[str, Any]) -> str:
        if not text:
            return text
        bot_ids = self._bot_ids_from_message(data)
        cleaned = text
        for bot_id in bot_ids:
            bare_id = bot_id.split("@", 1)[0]
            if bare_id:
                cleaned = re.sub(rf"@{re.escape(bare_id)}\b[,:\-]*\s*", "", cleaned)
        return cleaned.strip() or text

    def _should_process_message(self, data: Dict[str, Any]) -> bool:
        if not data.get("isGroup"):
            return True
        chat_id = str(data.get("chatId") or "")
        if chat_id in self._whatsapp_free_response_chats():
            return True
        if not self._whatsapp_require_mention():
            return True
        body = str(data.get("body") or "").strip()
        if body.startswith("/"):
            return True
        if self._message_is_reply_to_bot(data):
            return True
        if self._message_mentions_bot(data):
            return True
        return self._message_matches_mention_patterns(data)
    
    async def connect(self) -> bool:
        """
        启动 WhatsApp 桥接。

        启动 Node.js 桥接进程并等待其就绪。
        """
        if not check_whatsapp_requirements():
            logger.warning("[%s] Node.js not found. WhatsApp requires Node.js.", self.name)
            return False
        
        bridge_path = Path(self._bridge_script)
        if not bridge_path.exists():
            logger.warning("[%s] Bridge script not found: %s", self.name, bridge_path)
            return False
        
        logger.info("[%s] Bridge found at %s", self.name, bridge_path)
        
        # 获取作用域锁，防止重复会话
        try:
            if not self._acquire_platform_lock('whatsapp-session', str(self._session_path), 'WhatsApp session'):
                return False
        except Exception as e:
            logger.warning("[%s] Could not acquire session lock (non-fatal): %s", self.name, e)

        # 如果 node_modules 不存在，自动安装 npm 依赖
        bridge_dir = bridge_path.parent
        if not (bridge_dir / "node_modules").exists():
            print(f"[{self.name}] Installing WhatsApp bridge dependencies...")
            try:
                install_result = subprocess.run(
                    ["npm", "install", "--silent"],
                    cwd=str(bridge_dir),
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                if install_result.returncode != 0:
                    print(f"[{self.name}] npm install failed: {install_result.stderr}")
                    return False
                print(f"[{self.name}] Dependencies installed")
            except Exception as e:
                print(f"[{self.name}] Failed to install dependencies: {e}")
                return False
        
        try:
            # 确保会话目录存在
            self._session_path.mkdir(parents=True, exist_ok=True)
            
            # 检查桥接是否已在运行且已连接
            import aiohttp
            import asyncio
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        f"http://127.0.0.1:{self._bridge_port}/health",
                        timeout=aiohttp.ClientTimeout(total=2)
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            bridge_status = data.get("status", "unknown")
                            if bridge_status == "connected":
                                print(f"[{self.name}] Using existing bridge (status: {bridge_status})")
                                self._mark_connected()
                                self._bridge_process = None  # 不由我们管理
                                self._http_session = aiohttp.ClientSession()
                                self._poll_task = asyncio.create_task(self._poll_messages())
                                return True
                            else:
                                print(f"[{self.name}] Bridge found but not connected (status: {bridge_status}), restarting")
            except Exception:
                pass  # 桥接未运行，启动新的
            
            # 终止上一次网关运行残留的孤儿桥接进程
            _kill_port_process(self._bridge_port)
            await asyncio.sleep(1)
            
            # 在独立进程组中启动桥接进程。
            # 将输出路由到日志文件，以便保留二维码、错误和重连
            # 消息，方便故障排查。
            whatsapp_mode = os.getenv("WHATSAPP_MODE", "self-chat")
            self._bridge_log = self._session_path.parent / "bridge.log"
            bridge_log_fh = open(self._bridge_log, "a")
            self._bridge_log_fh = bridge_log_fh

            # 构建桥接子进程环境变量。
            # 从 config.yaml 传递 WHATSAPP_REPLY_PREFIX，使 Node 桥接
            # 无需用户额外设置环境变量即可使用。
            bridge_env = os.environ.copy()
            if self._reply_prefix is not None:
                bridge_env["WHATSAPP_REPLY_PREFIX"] = self._reply_prefix

            self._bridge_process = subprocess.Popen(
                [
                    "node",
                    str(bridge_path),
                    "--port", str(self._bridge_port),
                    "--session", str(self._session_path),
                    "--mode", whatsapp_mode,
                ],
                stdout=bridge_log_fh,
                stderr=bridge_log_fh,
                preexec_fn=None if _IS_WINDOWS else os.setsid,
                env=bridge_env,
            )
            
            # 等待桥接连接到 WhatsApp。
            # 阶段1：等待 HTTP 服务器启动（最多 15 秒）。
            # 阶段2：等待 WhatsApp 状态变为 connected（最多再等 15 秒）。
            import aiohttp
            http_ready = False
            data = {}
            for attempt in range(15):
                await asyncio.sleep(1)
                if self._bridge_process.poll() is not None:
                    print(f"[{self.name}] Bridge process died (exit code {self._bridge_process.returncode})")
                    print(f"[{self.name}] Check log: {self._bridge_log}")
                    self._close_bridge_log()
                    return False
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(
                            f"http://127.0.0.1:{self._bridge_port}/health",
                            timeout=aiohttp.ClientTimeout(total=2)
                        ) as resp:
                            if resp.status == 200:
                                http_ready = True
                                data = await resp.json()
                                if data.get("status") == "connected":
                                    print(f"[{self.name}] Bridge ready (status: connected)")
                                    break
                except Exception:
                    continue

            if not http_ready:
                print(f"[{self.name}] Bridge HTTP server did not start in 15s")
                print(f"[{self.name}] Check log: {self._bridge_log}")
                self._close_bridge_log()
                return False
            
            # 阶段2：HTTP 已就绪但 WhatsApp 可能仍在连接中。
            # 给予更多时间使用已保存的凭据进行认证。
            if data.get("status") != "connected":
                print(f"[{self.name}] Bridge HTTP ready, waiting for WhatsApp connection...")
                for attempt in range(15):
                    await asyncio.sleep(1)
                    if self._bridge_process.poll() is not None:
                        print(f"[{self.name}] Bridge process died during connection")
                        print(f"[{self.name}] Check log: {self._bridge_log}")
                        self._close_bridge_log()
                        return False
                    try:
                        async with aiohttp.ClientSession() as session:
                            async with session.get(
                                f"http://127.0.0.1:{self._bridge_port}/health",
                                timeout=aiohttp.ClientTimeout(total=2)
                            ) as resp:
                                if resp.status == 200:
                                    data = await resp.json()
                                    if data.get("status") == "connected":
                                        print(f"[{self.name}] Bridge ready (status: connected)")
                                        break
                    except Exception:
                        continue
                else:
                    # 仍未连接 - 发出警告但继续（桥接可能
                    # 稍后自动重连，如 515 错误码重启后）。
                    print(f"[{self.name}] ⚠ WhatsApp not connected after 30s")
                    print(f"[{self.name}]   Bridge log: {self._bridge_log}")
                    print(f"[{self.name}]   If session expired, re-pair: hermes whatsapp")
            
            # 创建持久化 HTTP 会话用于所有桥接通信
            self._http_session = aiohttp.ClientSession()

            # 启动消息轮询任务
            self._poll_task = asyncio.create_task(self._poll_messages())
            
            self._mark_connected()
            print(f"[{self.name}] Bridge started on port {self._bridge_port}")
            return True
            
        except Exception as e:
            self._release_platform_lock()
            logger.error("[%s] Failed to start bridge: %s", self.name, e, exc_info=True)
            self._close_bridge_log()
            return False
    
    def _close_bridge_log(self) -> None:
        """关闭桥接日志文件句柄（如果已打开）。"""
        if self._bridge_log_fh:
            try:
                self._bridge_log_fh.close()
            except Exception:
                pass
            self._bridge_log_fh = None

    async def _check_managed_bridge_exit(self) -> Optional[str]:
        """如果受管理的桥接子进程已退出，返回致命错误消息。"""
        if self._bridge_process is None:
            return None

        returncode = self._bridge_process.poll()
        if returncode is None:
            return None

        message = f"WhatsApp bridge process exited unexpectedly (code {returncode})."
        if not self.has_fatal_error:
            logger.error("[%s] %s", self.name, message)
            self._set_fatal_error("whatsapp_bridge_exited", message, retryable=True)
            self._close_bridge_log()
            await self._notify_fatal_error()
        return self.fatal_error_message or message

    async def disconnect(self) -> None:
        """停止 WhatsApp 桥接并清理所有孤儿进程。"""
        if self._bridge_process:
            try:
                # 终止整个进程组，使子 node 进程也被终止
                import signal
                try:
                    if _IS_WINDOWS:
                        self._bridge_process.terminate()
                    else:
                        os.killpg(os.getpgid(self._bridge_process.pid), signal.SIGTERM)
                except (ProcessLookupError, PermissionError):
                    self._bridge_process.terminate()
                await asyncio.sleep(1)
                if self._bridge_process.poll() is None:
                    try:
                        if _IS_WINDOWS:
                            self._bridge_process.kill()
                        else:
                            os.killpg(os.getpgid(self._bridge_process.pid), signal.SIGKILL)
                    except (ProcessLookupError, PermissionError):
                        self._bridge_process.kill()
            except Exception as e:
                print(f"[{self.name}] Error stopping bridge: {e}")
        else:
            # 桥接不是由我们启动的，不终止它
            print(f"[{self.name}] Disconnecting (external bridge left running)")

        # 显式取消轮询任务
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            try:
                await self._poll_task
            except (asyncio.CancelledError, Exception):
                pass
        self._poll_task = None

        # 关闭持久化 HTTP 会话
        if self._http_session and not self._http_session.closed:
            await self._http_session.close()
        self._http_session = None

        self._release_platform_lock()

        self._mark_disconnected()
        self._bridge_process = None
        self._close_bridge_log()
        print(f"[{self.name}] Disconnected")
    
    def format_message(self, content: str) -> str:
        """将标准 Markdown 转换为 WhatsApp 兼容的格式。

        WhatsApp 支持：*粗体*、_斜体_、~删除线~、```代码块```
        和等宽 `行内代码`。标准 Markdown 对粗体/斜体/删除线
        使用不同的语法，因此在此进行转换。

        代码块（``` 围栏式）和行内代码（`）通过占位符替换
        保护，不受转换影响。
        """
        if not content:
            return content

        # --- 1. 保护围栏式代码块不受格式转换影响 ---
        _FENCE_PH = "\x00FENCE"
        fences: list[str] = []

        def _save_fence(m: re.Match) -> str:
            fences.append(m.group(0))
            return f"{_FENCE_PH}{len(fences) - 1}\x00"

        result = re.sub(r"```[\s\S]*?```", _save_fence, content)

        # --- 2. 保护行内代码 ---
        _CODE_PH = "\x00CODE"
        codes: list[str] = []

        def _save_code(m: re.Match) -> str:
            codes.append(m.group(0))
            return f"{_CODE_PH}{len(codes) - 1}\x00"

        result = re.sub(r"`[^`\n]+`", _save_code, result)

        # --- 3. 将 Markdown 格式转换为 WhatsApp 语法 ---
        # 粗体：**text** 或 __text__ -> *text*
        result = re.sub(r"\*\*(.+?)\*\*", r"*\1*", result)
        result = re.sub(r"__(.+?)__", r"*\1*", result)
        # 删除线：~~text~~ -> ~text~
        result = re.sub(r"~~(.+?)~~", r"~\1~", result)
        # 斜体：*text* 在 WhatsApp 中已是斜体 - 保持不变
        # _text_ 在 WhatsApp 中已是斜体 - 保持不变

        # --- 4. 将 Markdown 标题转换为粗体文本 ---
        # # 标题 -> *标题*
        result = re.sub(r"^#{1,6}\s+(.+)$", r"*\1*", result, flags=re.MULTILINE)

        # --- 5. 将 Markdown 链接转换为：[text](url) -> text (url) ---
        result = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", result)

        # --- 6. 恢复受保护的代码区域 ---
        for i, fence in enumerate(fences):
            result = result.replace(f"{_FENCE_PH}{i}\x00", fence)
        for i, code in enumerate(codes):
            result = result.replace(f"{_CODE_PH}{i}\x00", code)

        return result

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> SendResult:
        """通过 WhatsApp 桥接发送消息。

        将 Markdown 格式转换为 WhatsApp 格式，将长消息按代码块边界
        拆分成多个片段，然后依次发送每个片段。
        """
        if not self._running or not self._http_session:
            return SendResult(success=False, error="Not connected")
        bridge_exit = await self._check_managed_bridge_exit()
        if bridge_exit:
            return SendResult(success=False, error=bridge_exit)

        if not content or not content.strip():
            return SendResult(success=True, message_id=None)

        try:
            import aiohttp

            # 格式化并分片消息
            formatted = self.format_message(content)
            chunks = self.truncate_message(formatted, self.MAX_MESSAGE_LENGTH)

            last_message_id = None
            for chunk in chunks:
                payload: Dict[str, Any] = {
                    "chatId": chat_id,
                    "message": chunk,
                }
                if reply_to and last_message_id is None:
                    # 仅在第一个片段上使用 reply-to
                    payload["replyTo"] = reply_to

                async with self._http_session.post(
                    f"http://127.0.0.1:{self._bridge_port}/send",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        last_message_id = data.get("messageId")
                    else:
                        error = await resp.text()
                        return SendResult(success=False, error=error)

                # 片段之间添加短暂延迟以避免触发限流
                if len(chunks) > 1:
                    await asyncio.sleep(0.3)

            return SendResult(
                success=True,
                message_id=last_message_id,
            )
        except Exception as e:
            return SendResult(success=False, error=str(e))

    async def edit_message(
        self,
        chat_id: str,
        message_id: str,
        content: str,
    ) -> SendResult:
        """通过 WhatsApp 桥接编辑已发送的消息。"""
        if not self._running or not self._http_session:
            return SendResult(success=False, error="Not connected")
        bridge_exit = await self._check_managed_bridge_exit()
        if bridge_exit:
            return SendResult(success=False, error=bridge_exit)
        try:
            import aiohttp
            async with self._http_session.post(
                f"http://127.0.0.1:{self._bridge_port}/edit",
                json={
                    "chatId": chat_id,
                    "messageId": message_id,
                    "message": content,
                },
                timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status == 200:
                    return SendResult(success=True, message_id=message_id)
                else:
                    error = await resp.text()
                    return SendResult(success=False, error=error)
        except Exception as e:
            return SendResult(success=False, error=str(e))

    async def _send_media_to_bridge(
        self,
        chat_id: str,
        file_path: str,
        media_type: str,
        caption: Optional[str] = None,
        file_name: Optional[str] = None,
    ) -> SendResult:
        """通过桥接 /send-media 端点发送任意媒体文件。"""
        if not self._running or not self._http_session:
            return SendResult(success=False, error="Not connected")
        bridge_exit = await self._check_managed_bridge_exit()
        if bridge_exit:
            return SendResult(success=False, error=bridge_exit)
        try:
            import aiohttp

            if not os.path.exists(file_path):
                return SendResult(success=False, error=f"File not found: {file_path}")

            payload: Dict[str, Any] = {
                "chatId": chat_id,
                "filePath": file_path,
                "mediaType": media_type,
            }
            if caption:
                payload["caption"] = caption
            if file_name:
                payload["fileName"] = file_name

            async with self._http_session.post(
                f"http://127.0.0.1:{self._bridge_port}/send-media",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=120),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return SendResult(
                        success=True,
                        message_id=data.get("messageId"),
                        raw_response=data,
                    )
                else:
                    error = await resp.text()
                    return SendResult(success=False, error=error)

        except Exception as e:
            return SendResult(success=False, error=str(e))

    async def send_image(
        self,
        chat_id: str,
        image_url: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
    ) -> SendResult:
        """下载图片 URL 到缓存，通过桥接原生发送。"""
        try:
            local_path = await cache_image_from_url(image_url)
            return await self._send_media_to_bridge(chat_id, local_path, "image", caption)
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
        """通过桥接原生发送本地图片文件。"""
        return await self._send_media_to_bridge(chat_id, image_path, "image", caption)

    async def send_video(
        self,
        chat_id: str,
        video_path: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        **kwargs,
    ) -> SendResult:
        """通过桥接原生发送视频 - 在 WhatsApp 中内联播放。"""
        return await self._send_media_to_bridge(chat_id, video_path, "video", caption)

    async def send_document(
        self,
        chat_id: str,
        file_path: str,
        caption: Optional[str] = None,
        file_name: Optional[str] = None,
        reply_to: Optional[str] = None,
        **kwargs,
    ) -> SendResult:
        """通过桥接发送文档/文件作为可下载附件。"""
        return await self._send_media_to_bridge(
            chat_id, file_path, "document", caption,
            file_name or os.path.basename(file_path),
        )

    async def send_typing(self, chat_id: str, metadata=None) -> None:
        """通过桥接发送正在输入指示器。"""
        if not self._running or not self._http_session:
            return
        if await self._check_managed_bridge_exit():
            return
        
        try:
            import aiohttp

            await self._http_session.post(
                f"http://127.0.0.1:{self._bridge_port}/typing",
                json={"chatId": chat_id},
                timeout=aiohttp.ClientTimeout(total=5)
            )
        except Exception:
            pass  # 忽略输入指示器发送失败
    
    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        """获取 WhatsApp 聊天的信息。"""
        if not self._running or not self._http_session:
            return {"name": "Unknown", "type": "dm"}
        if await self._check_managed_bridge_exit():
            return {"name": chat_id, "type": "dm"}
        
        try:
            import aiohttp

            async with self._http_session.get(
                f"http://127.0.0.1:{self._bridge_port}/chat/{chat_id}",
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return {
                        "name": data.get("name", chat_id),
                        "type": "group" if data.get("isGroup") else "dm",
                        "participants": data.get("participants", []),
                    }
        except Exception as e:
            logger.debug("Could not get WhatsApp chat info for %s: %s", chat_id, e)
        
        return {"name": chat_id, "type": "dm"}
    
    async def _poll_messages(self) -> None:
        """轮询桥接以获取传入的消息。"""
        import aiohttp

        while self._running:
            if not self._http_session:
                break
            bridge_exit = await self._check_managed_bridge_exit()
            if bridge_exit:
                print(f"[{self.name}] {bridge_exit}")
                break
            try:
                async with self._http_session.get(
                    f"http://127.0.0.1:{self._bridge_port}/messages",
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    if resp.status == 200:
                        messages = await resp.json()
                        for msg_data in messages:
                            event = await self._build_message_event(msg_data)
                            if event:
                                await self.handle_message(event)
            except asyncio.CancelledError:
                break
            except Exception as e:
                bridge_exit = await self._check_managed_bridge_exit()
                if bridge_exit:
                    print(f"[{self.name}] {bridge_exit}")
                    break
                print(f"[{self.name}] Poll error: {e}")
                await asyncio.sleep(5)
            
            await asyncio.sleep(1)  # 轮询间隔
    
    async def _build_message_event(self, data: Dict[str, Any]) -> Optional[MessageEvent]:
        """根据桥接消息数据构建 MessageEvent，并将图片下载到缓存。"""
        try:
            if not self._should_process_message(data):
                return None

            # 判断消息类型
            msg_type = MessageType.TEXT
            if data.get("hasMedia"):
                media_type = data.get("mediaType", "")
                if "image" in media_type:
                    msg_type = MessageType.PHOTO
                elif "video" in media_type:
                    msg_type = MessageType.VIDEO
                elif "audio" in media_type or "ptt" in media_type:  # ptt = 语音消息
                    msg_type = MessageType.VOICE
                else:
                    msg_type = MessageType.DOCUMENT
            
            # 判断聊天类型
            is_group = data.get("isGroup", False)
            chat_type = "group" if is_group else "dm"
            
            # 构建来源信息
            source = self.build_source(
                chat_id=data.get("chatId", ""),
                chat_name=data.get("chatName"),
                chat_type=chat_type,
                user_id=data.get("senderId"),
                user_name=data.get("senderName"),
            )
            
            # 将媒体 URL 下载到本地缓存，使代理工具
            # 无论 URL 是否过期都能可靠访问。
            raw_urls = data.get("mediaUrls", [])
            cached_urls = []
            media_types = []
            for url in raw_urls:
                if msg_type == MessageType.PHOTO and url.startswith(("http://", "https://")):
                    try:
                        cached_path = await cache_image_from_url(url, ext=".jpg")
                        cached_urls.append(cached_path)
                        media_types.append("image/jpeg")
                        print(f"[{self.name}] Cached user image: {cached_path}", flush=True)
                    except Exception as e:
                        print(f"[{self.name}] Failed to cache image: {e}", flush=True)
                        cached_urls.append(url)
                        media_types.append("image/jpeg")
                elif msg_type == MessageType.PHOTO and os.path.isabs(url):
                    # 本地文件路径 - 桥接已下载了图片
                    cached_urls.append(url)
                    media_types.append("image/jpeg")
                    print(f"[{self.name}] Using bridge-cached image: {url}", flush=True)
                elif msg_type == MessageType.VOICE and url.startswith(("http://", "https://")):
                    try:
                        cached_path = await cache_audio_from_url(url, ext=".ogg")
                        cached_urls.append(cached_path)
                        media_types.append("audio/ogg")
                        print(f"[{self.name}] Cached user voice: {cached_path}", flush=True)
                    except Exception as e:
                        print(f"[{self.name}] Failed to cache voice: {e}", flush=True)
                        cached_urls.append(url)
                        media_types.append("audio/ogg")
                elif msg_type == MessageType.VOICE and os.path.isabs(url):
                    # 本地文件路径 - 桥接已下载了音频
                    cached_urls.append(url)
                    media_types.append("audio/ogg")
                    print(f"[{self.name}] Using bridge-cached audio: {url}", flush=True)
                elif msg_type == MessageType.DOCUMENT and os.path.isabs(url):
                    # 本地文件路径 - 桥接已下载了文档
                    cached_urls.append(url)
                    ext = Path(url).suffix.lower()
                    mime = SUPPORTED_DOCUMENT_TYPES.get(ext, "application/octet-stream")
                    media_types.append(mime)
                    print(f"[{self.name}] Using bridge-cached document: {url}", flush=True)
                elif msg_type == MessageType.VIDEO and os.path.isabs(url):
                    cached_urls.append(url)
                    media_types.append("video/mp4")
                    print(f"[{self.name}] Using bridge-cached video: {url}", flush=True)
                else:
                    cached_urls.append(url)
                    media_types.append("unknown")

            # 对于文本可读的文档，将文件内容直接注入到
            # 消息文本中，使代理可以内联读取。
            # 限制为 100KB，与 Telegram/Discord/Slack 行为一致。
            body = data.get("body", "")
            if data.get("isGroup"):
                body = self._clean_bot_mention_text(body, data)
            MAX_TEXT_INJECT_BYTES = 100 * 1024
            if msg_type == MessageType.DOCUMENT and cached_urls:
                for doc_path in cached_urls:
                    ext = Path(doc_path).suffix.lower()
                    if ext in (".txt", ".md", ".csv", ".json", ".xml", ".yaml", ".yml", ".log", ".py", ".js", ".ts", ".html", ".css"):
                        try:
                            file_size = Path(doc_path).stat().st_size
                            if file_size > MAX_TEXT_INJECT_BYTES:
                                print(f"[{self.name}] Skipping text injection for {doc_path} ({file_size} bytes > {MAX_TEXT_INJECT_BYTES})", flush=True)
                                continue
                            content = Path(doc_path).read_text(errors="replace")
                            fname = Path(doc_path).name
                            # 移除 doc_<hex>_ 前缀用于显示
                            display_name = fname
                            if "_" in fname:
                                parts = fname.split("_", 2)
                                if len(parts) >= 3:
                                    display_name = parts[2]
                            injection = f"[Content of {display_name}]:\n{content}"
                            if body:
                                body = f"{injection}\n\n{body}"
                            else:
                                body = injection
                            print(f"[{self.name}] Injected text content from: {doc_path}", flush=True)
                        except Exception as e:
                            print(f"[{self.name}] Failed to read document text: {e}", flush=True)

            return MessageEvent(
                text=body,
                message_type=msg_type,
                source=source,
                raw_message=data,
                message_id=data.get("messageId"),
                media_urls=cached_urls,
                media_types=media_types,
            )
        except Exception as e:
            print(f"[{self.name}] Error building event: {e}")
            return None
