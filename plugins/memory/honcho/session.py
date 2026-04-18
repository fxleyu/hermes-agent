"""基于 Honcho 的会话管理，用于对话历史。"""

from __future__ import annotations

import queue
import re
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, TYPE_CHECKING

from plugins.memory.honcho.client import get_honcho_client

if TYPE_CHECKING:
    from honcho import Honcho

logger = logging.getLogger(__name__)

# 哨兵值，用于通知异步写入线程关闭
_ASYNC_SHUTDOWN = object()


@dataclass
class HonchoSession:
    """
    基于 Honcho 的对话会话。

    提供本地消息缓存，与 Honcho 的 AI 原生记忆系统
    同步以实现用户建模。
    """

    key: str  # channel:chat_id 会话标识
    user_peer_id: str  # 用户的 Honcho 对等方 ID
    assistant_peer_id: str  # 助手的 Honcho 对等方 ID
    honcho_session_id: str  # Honcho 会话 ID
    messages: list[dict[str, Any]] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def add_message(self, role: str, content: str, **kwargs: Any) -> None:
        """向本地缓存添加消息。"""
        msg = {
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
            **kwargs,
        }
        self.messages.append(msg)
        self.updated_at = datetime.now()

    def get_history(self, max_messages: int = 50) -> list[dict[str, Any]]:
        """获取 LLM 上下文的消息历史。"""
        recent = (
            self.messages[-max_messages:]
            if len(self.messages) > max_messages
            else self.messages
        )
        return [{"role": m["role"], "content": m["content"]} for m in recent]

    def clear(self) -> None:
        """清除会话中的所有消息。"""
        self.messages = []
        self.updated_at = datetime.now()


class HonchoSessionManager:
    """
    使用 Honcho 管理对话会话。

    与 Hermes 现有的 SQLite 状态和基于文件的记忆并行运行，
    通过 Honcho 的 AI 原生记忆添加持久化的跨会话用户建模。
    """

    def __init__(
        self,
        honcho: Honcho | None = None,
        context_tokens: int | None = None,
        config: Any | None = None,
    ):
        """
        初始化会话管理器。

        参数：
            honcho: 可选的 Honcho 客户端。如未提供则使用单例。
            context_tokens: context() 调用的最大令牌数（None = Honcho 默认值）。
            config: 来自全局配置的 HonchoClientConfig（提供 peer_name、ai_peer、
                    write_frequency、observation 等）。
        """
        self._honcho = honcho
        self._context_tokens = context_tokens
        self._config = config
        self._cache: dict[str, HonchoSession] = {}
        self._peers_cache: dict[str, Any] = {}
        self._sessions_cache: dict[str, Any] = {}

        # 写入频率状态
        write_frequency = (config.write_frequency if config else "async")
        self._write_frequency = write_frequency
        self._turn_counter: int = 0

        # 预取缓存：session_key -> 上次结果（每轮消费一次）
        self._context_cache: dict[str, dict] = {}
        self._dialectic_cache: dict[str, str] = {}
        self._prefetch_cache_lock = threading.Lock()
        self._dialectic_reasoning_level: str = (
            config.dialectic_reasoning_level if config else "low"
        )
        self._dialectic_dynamic: bool = (
            config.dialectic_dynamic if config else True
        )
        self._dialectic_max_chars: int = (
            config.dialectic_max_chars if config else 600
        )
        self._observation_mode: str = (
            config.observation_mode if config else "directional"
        )
        # 每对等方观察布尔值（细粒度，来自配置）
        self._user_observe_me: bool = config.user_observe_me if config else True
        self._user_observe_others: bool = config.user_observe_others if config else True
        self._ai_observe_me: bool = config.ai_observe_me if config else True
        self._ai_observe_others: bool = config.ai_observe_others if config else True
        self._message_max_chars: int = (
            config.message_max_chars if config else 25000
        )
        self._dialectic_max_input_chars: int = (
            config.dialectic_max_input_chars if config else 10000
        )

        # 异步写入队列 — 首次入队时延迟启动
        self._async_queue: queue.Queue | None = None
        self._async_thread: threading.Thread | None = None
        if write_frequency == "async":
            self._async_queue = queue.Queue()
            self._async_thread = threading.Thread(
                target=self._async_writer_loop,
                name="honcho-async-writer",
                daemon=True,
            )
            self._async_thread.start()

    @property
    def honcho(self) -> Honcho:
        """获取 Honcho 客户端，必要时初始化。"""
        if self._honcho is None:
            self._honcho = get_honcho_client()
        return self._honcho

    def _get_or_create_peer(self, peer_id: str) -> Any:
        """
        获取或创建一个 Honcho 对等方。

        对等方是惰性的 — 直到首次使用才发起 API 调用。
        观察设置通过 SessionPeerConfig 按会话控制。
        """
        if peer_id in self._peers_cache:
            return self._peers_cache[peer_id]

        peer = self.honcho.peer(peer_id)
        self._peers_cache[peer_id] = peer
        return peer

    def _get_or_create_honcho_session(
        self, session_id: str, user_peer: Any, assistant_peer: Any
    ) -> tuple[Any, list]:
        """
        获取或创建带有对等方配置的 Honcho 会话。

        返回：
            (honcho_session, existing_messages) 元组。
        """
        if session_id in self._sessions_cache:
            logger.debug("Honcho session '%s' retrieved from cache", session_id)
            return self._sessions_cache[session_id], []

        session = self.honcho.session(session_id)

        # 从细粒度布尔值配置每对等方观察。
        # 这些与 Honcho 的 SessionPeerConfig 开关一一映射。
        try:
            from honcho.session import SessionPeerConfig
            user_config = SessionPeerConfig(
                observe_me=self._user_observe_me,
                observe_others=self._user_observe_others,
            )
            ai_config = SessionPeerConfig(
                observe_me=self._ai_observe_me,
                observe_others=self._ai_observe_others,
            )

            session.add_peers([(user_peer, user_config), (assistant_peer, ai_config)])

            # 回写同步：服务器端配置（通过 Honcho UI 设置）优先于
            # 本地默认值。在 add_peers 后读取有效配置。
            # 注意：观察布尔值是管理器作用域的，不是按会话的。
            # 最后一次会话初始化生效。对 CLI 没问题；网关应按会话作用域。
            try:
                server_user = session.get_peer_configuration(user_peer)
                server_ai = session.get_peer_configuration(assistant_peer)
                if server_user.observe_me is not None:
                    self._user_observe_me = server_user.observe_me
                if server_user.observe_others is not None:
                    self._user_observe_others = server_user.observe_others
                if server_ai.observe_me is not None:
                    self._ai_observe_me = server_ai.observe_me
                if server_ai.observe_others is not None:
                    self._ai_observe_others = server_ai.observe_others
                logger.debug(
                    "Honcho observation synced from server: user(me=%s,others=%s) ai(me=%s,others=%s)",
                    self._user_observe_me, self._user_observe_others,
                    self._ai_observe_me, self._ai_observe_others,
                )
            except Exception as e:
                logger.debug("Honcho get_peer_configuration failed (using local config): %s", e)
        except Exception as e:
            logger.warning(
                "Honcho session '%s' add_peers failed (non-fatal): %s",
                session_id, e,
            )

        # 通过 context() 加载现有消息 — 单次调用获取消息 + 元数据
        existing_messages = []
        try:
            ctx = session.context(summary=True, tokens=self._context_tokens)
            existing_messages = ctx.messages or []

            # 验证时间顺序
            if existing_messages and len(existing_messages) > 1:
                timestamps = [m.created_at for m in existing_messages if m.created_at]
                if timestamps and timestamps != sorted(timestamps):
                    logger.warning(
                        "Honcho messages not chronologically ordered for session '%s', sorting",
                        session_id,
                    )
                    existing_messages = sorted(
                        existing_messages,
                        key=lambda m: m.created_at or datetime.min,
                    )

            if existing_messages:
                logger.info(
                    "Honcho session '%s' retrieved (%d existing messages)",
                    session_id, len(existing_messages),
                )
            else:
                logger.info("Honcho session '%s' created (new)", session_id)
        except Exception as e:
            logger.warning(
                "Honcho session '%s' loaded (failed to fetch context: %s)",
                session_id, e,
            )

        self._sessions_cache[session_id] = session
        return session, existing_messages

    def _sanitize_id(self, id_str: str) -> str:
        """清理 ID 以匹配 Honcho 模式：^[a-zA-Z0-9_-]+"""
        return re.sub(r'[^a-zA-Z0-9_-]', '-', id_str)

    def get_or_create(self, key: str) -> HonchoSession:
        """
        获取已有会话或创建新会话。

        参数：
            key: 会话键（通常为 channel:chat_id）。

        返回：
            该会话。
        """
        if key in self._cache:
            logger.debug("Local session cache hit: %s", key)
            return self._cache[key]

        # 可用时使用全局配置中的对等方名称
        if self._config and self._config.peer_name:
            user_peer_id = self._sanitize_id(self._config.peer_name)
        else:
            # 回退：从会话键派生
            parts = key.split(":", 1)
            channel = parts[0] if len(parts) > 1 else "default"
            chat_id = parts[1] if len(parts) > 1 else key
            user_peer_id = self._sanitize_id(f"user-{channel}-{chat_id}")

        assistant_peer_id = self._sanitize_id(
            self._config.ai_peer if self._config else "hermes-assistant"
        )

        # 为 Honcho 清理会话 ID
        honcho_session_id = self._sanitize_id(key)

        # 获取或创建对等方
        user_peer = self._get_or_create_peer(user_peer_id)
        assistant_peer = self._get_or_create_peer(assistant_peer_id)

        # 获取或创建 Honcho 会话
        honcho_session, existing_messages = self._get_or_create_honcho_session(
            honcho_session_id, user_peer, assistant_peer
        )

        # 将 Honcho 消息转换为本地格式
        local_messages = []
        for msg in existing_messages:
            role = "assistant" if msg.peer_id == assistant_peer_id else "user"
            local_messages.append({
                "role": role,
                "content": msg.content,
                "timestamp": msg.created_at.isoformat() if msg.created_at else "",
                "_synced": True,  # 已在 Honcho 中
            })

        # 使用已有消息创建本地会话包装器
        session = HonchoSession(
            key=key,
            user_peer_id=user_peer_id,
            assistant_peer_id=assistant_peer_id,
            honcho_session_id=honcho_session_id,
            messages=local_messages,
        )

        self._cache[key] = session
        return session

    def _flush_session(self, session: HonchoSession) -> bool:
        """内部方法：将未同步的消息同步写入 Honcho。"""
        if not session.messages:
            return True

        user_peer = self._get_or_create_peer(session.user_peer_id)
        assistant_peer = self._get_or_create_peer(session.assistant_peer_id)
        honcho_session = self._sessions_cache.get(session.honcho_session_id)

        if not honcho_session:
            honcho_session, _ = self._get_or_create_honcho_session(
                session.honcho_session_id, user_peer, assistant_peer
            )

        new_messages = [m for m in session.messages if not m.get("_synced")]
        if not new_messages:
            return True

        honcho_messages = []
        for msg in new_messages:
            peer = user_peer if msg["role"] == "user" else assistant_peer
            honcho_messages.append(peer.message(msg["content"]))

        try:
            honcho_session.add_messages(honcho_messages)
            for msg in new_messages:
                msg["_synced"] = True
            logger.debug("Synced %d messages to Honcho for %s", len(honcho_messages), session.key)
            self._cache[session.key] = session
            return True
        except Exception as e:
            for msg in new_messages:
                msg["_synced"] = False
            logger.error("Failed to sync messages to Honcho: %s", e)
            self._cache[session.key] = session
            return False

    def _async_writer_loop(self) -> None:
        """后台守护线程：消费异步写入队列。"""
        while True:
            try:
                item = self._async_queue.get(timeout=5)
                if item is _ASYNC_SHUTDOWN:
                    break

                first_error: Exception | None = None
                try:
                    success = self._flush_session(item)
                except Exception as e:
                    success = False
                    first_error = e

                if success:
                    continue

                if first_error is not None:
                    logger.warning("Honcho async write failed, retrying once: %s", first_error)
                else:
                    logger.warning("Honcho async write failed, retrying once")

                import time as _time
                _time.sleep(2)

                try:
                    retry_success = self._flush_session(item)
                except Exception as e2:
                    logger.error("Honcho async write retry failed, dropping batch: %s", e2)
                    continue

                if not retry_success:
                    logger.error("Honcho async write retry failed, dropping batch")
            except queue.Empty:
                continue
            except Exception as e:
                logger.error("Honcho async writer error: %s", e)

    def save(self, session: HonchoSession) -> None:
        """将消息保存到 Honcho，遵循 write_frequency 设置。

        write_frequency 模式：
          "async"   — 入队到后台线程（零阻塞，零 token 开销）
          "turn"    — 每轮同步刷新
          "session" — 延迟到显式调用 flush_session() 时刷新
          N (int)   — 每 N 轮刷新一次
        """
        self._turn_counter += 1
        wf = self._write_frequency

        if wf == "async":
            if self._async_queue is not None:
                self._async_queue.put(session)
        elif wf == "turn":
            self._flush_session(session)
        elif wf == "session":
            # 累积消息；调用方须在会话结束时调用 flush_all()
            pass
        elif isinstance(wf, int) and wf > 0:
            if self._turn_counter % wf == 0:
                self._flush_session(session)

    def flush_all(self) -> None:
        """刷新所有缓存会话中未同步的消息。

        在 "session" write_frequency 模式的会话结束时调用，
        或在进程退出前强制同步（无论当前模式如何）。
        """
        for session in list(self._cache.values()):
            try:
                self._flush_session(session)
            except Exception as e:
                logger.error("Honcho flush_all error for %s: %s", session.key, e)

        # 如果存在异步队列，同步排空
        if self._async_queue is not None:
            while not self._async_queue.empty():
                try:
                    item = self._async_queue.get_nowait()
                    if item is not _ASYNC_SHUTDOWN:
                        self._flush_session(item)
                except queue.Empty:
                    break

    def shutdown(self) -> None:
        """优雅关闭异步写入线程。"""
        if self._async_queue is not None and self._async_thread is not None:
            self.flush_all()
            self._async_queue.put(_ASYNC_SHUTDOWN)
            self._async_thread.join(timeout=10)

    def delete(self, key: str) -> bool:
        """从本地缓存中删除会话。"""
        if key in self._cache:
            del self._cache[key]
            return True
        return False

    def new_session(self, key: str) -> HonchoSession:
        """
        创建新会话，同时保留旧会话用于用户建模。

        创建一个带有新 ID 的全新会话，同时保留旧会话的数据
        在 Honcho 中以继续进行用户建模。
        """
        import time

        # 从缓存中移除旧会话（但不从 Honcho 删除）
        old_session = self._cache.pop(key, None)
        if old_session:
            self._sessions_cache.pop(old_session.honcho_session_id, None)

        # 创建带时间戳后缀的新会话
        timestamp = int(time.time())
        new_key = f"{key}:{timestamp}"

        # get_or_create 将创建全新的会话
        session = self.get_or_create(new_key)

        # 以原始键缓存，使调用方能通过预期名称找到它
        self._cache[key] = session

        logger.info("Created new session for %s (honcho: %s)", key, session.honcho_session_id)
        return session

    _REASONING_LEVELS = ("minimal", "low", "medium", "high", "max")

    def _default_reasoning_level(self) -> str:
        """返回配置的默认推理级别。"""
        return self._dialectic_reasoning_level

    def dialectic_query(
        self, session_key: str, query: str,
        reasoning_level: str | None = None,
        peer: str = "user",
    ) -> str:
        """
        对指定对等方查询 Honcho 的辩证端点。

        在 Honcho 后端对目标对等方的完整表示运行 LLM。
        延迟高于 context() —— 通过 prefetch_dialectic() 异步调用
        以避免阻塞响应。

        Args:
            session_key: 要查询的会话键。
            query: 自然语言问题。
            reasoning_level: 覆盖配置的默认值（dialecticReasoningLevel）。
                             仅在 dialecticDynamic 为 true 时生效。
                             如果为 None 或 dialecticDynamic 为 false，使用配置的默认值。
            peer: 要查询的对等方 —— "user"（默认）或 "ai"。

        Returns:
            Honcho 的综合回答，失败时返回空字符串。
        """
        session = self._cache.get(session_key)
        if not session:
            return ""

        target_peer_id = self._resolve_peer_id(session, peer)
        if target_peer_id is None:
            return ""

        # 防护：将查询截断到 Honcho 辩证输入限制
        if len(query) > self._dialectic_max_input_chars:
            query = query[:self._dialectic_max_input_chars].rsplit(" ", 1)[0]

        if self._dialectic_dynamic and reasoning_level:
            level = reasoning_level
        else:
            level = self._default_reasoning_level()

        try:
            if self._ai_observe_others:
                # AI 对等方可以观察其他对等方 —— 使用助手作为观察者。
                ai_peer_obj = self._get_or_create_peer(session.assistant_peer_id)
                if target_peer_id == session.assistant_peer_id:
                    result = ai_peer_obj.chat(query, reasoning_level=level) or ""
                else:
                    result = ai_peer_obj.chat(
                        query,
                        target=target_peer_id,
                        reasoning_level=level,
                    ) or ""
            else:
                # 无跨观察能力时，每个对等方查询自己的上下文。
                target_peer = self._get_or_create_peer(target_peer_id)
                result = target_peer.chat(query, reasoning_level=level) or ""

            # 在缓存前应用 Hermes 侧字符上限
            if result and self._dialectic_max_chars and len(result) > self._dialectic_max_chars:
                result = result[:self._dialectic_max_chars].rsplit(" ", 1)[0] + " …"
            return result
        except Exception as e:
            logger.warning("Honcho dialectic query failed: %s", e)
            return ""

    def prefetch_dialectic(self, session_key: str, query: str) -> None:
        """
        在后台线程中触发 dialectic_query，并缓存结果。

        非阻塞。结果通过 pop_dialectic_result() 在下一次调用
        （通常是下一轮）中获取。推理级别根据查询复杂度动态选择。

        Args:
            session_key: 要查询的会话键。
            query: 用户当前的消息，用作查询。
        """
        def _run():
            result = self.dialectic_query(session_key, query)
            if result:
                self.set_dialectic_result(session_key, result)

        t = threading.Thread(target=_run, name="honcho-dialectic-prefetch", daemon=True)
        t.start()

    def set_dialectic_result(self, session_key: str, result: str) -> None:
        """以线程安全方式存储预取的辩证结果。"""
        if not result:
            return
        with self._prefetch_cache_lock:
            self._dialectic_cache[session_key] = result

    def pop_dialectic_result(self, session_key: str) -> str:
        """
        返回并清除此会话缓存的辩证结果。

        如果结果尚未就绪则返回空字符串。
        """
        with self._prefetch_cache_lock:
            return self._dialectic_cache.pop(session_key, "")

    def prefetch_context(self, session_key: str, user_message: str | None = None) -> None:
        """
        在后台线程中触发 get_prefetch_context，并缓存结果。

        非阻塞。通过下一轮的 pop_context_result() 消费。
        避免同步 HTTP 往返阻塞每次响应。
        """
        def _run():
            result = self.get_prefetch_context(session_key, user_message)
            if result:
                self.set_context_result(session_key, result)

        t = threading.Thread(target=_run, name="honcho-context-prefetch", daemon=True)
        t.start()

    def set_context_result(self, session_key: str, result: dict[str, str]) -> None:
        """以线程安全方式存储预取的上下文结果。"""
        if not result:
            return
        with self._prefetch_cache_lock:
            self._context_cache[session_key] = result

    def pop_context_result(self, session_key: str) -> dict[str, str]:
        """
        返回并清除此会话缓存的上下文结果。

        如果结果尚未就绪（首轮）则返回空字典。
        """
        with self._prefetch_cache_lock:
            return self._context_cache.pop(session_key, {})

    def get_prefetch_context(self, session_key: str, user_message: str | None = None) -> dict[str, str]:
        """
        从 Honcho 预取用户和 AI 对等方上下文。

        获取两个对等方的 peer_representation 和 peer_card，以及
        可用时的会话摘要。有意省略 search_query —— 它只会影响
        本代码不消费的额外摘录，且传递原始消息会在服务器访问
        日志中暴露对话内容。

        Args:
            session_key: 要获取上下文的会话键。
            user_message: 未使用；保留以兼容调用方。

        Returns:
            包含 'representation'、'card'、'ai_representation'、
            'ai_card' 以及可选 'summary' 键的字典。
        """
        session = self._cache.get(session_key)
        if not session:
            return {}

        result: dict[str, str] = {}

        # 会话摘要 —— 提供会话范围的上下文。
        # 新会话（每会话冷启动或首次每目录会话）返回 null 摘要 ——
        # 下方的防护代码优雅地处理这种情况。
        # 每目录的回访会话获得其累积摘要。
        try:
            honcho_session = self._sessions_cache.get(session.honcho_session_id)
            if honcho_session:
                ctx = honcho_session.context(summary=True)
                if ctx.summary and getattr(ctx.summary, "content", None):
                    result["summary"] = ctx.summary.content
        except Exception as e:
            logger.debug("Failed to fetch session summary from Honcho: %s", e)

        try:
            user_ctx = self._fetch_peer_context(session.user_peer_id, target=session.user_peer_id)
            result["representation"] = user_ctx["representation"]
            result["card"] = "\n".join(user_ctx["card"])
        except Exception as e:
            logger.warning("Failed to fetch user context from Honcho: %s", e)

        # 同时获取 AI 对等方自身的表示，以便 Hermes 了解自己。
        try:
            ai_ctx = self._fetch_peer_context(session.assistant_peer_id, target=session.assistant_peer_id)
            result["ai_representation"] = ai_ctx["representation"]
            result["ai_card"] = "\n".join(ai_ctx["card"])
        except Exception as e:
            logger.debug("Failed to fetch AI peer context from Honcho: %s", e)

        return result

    def migrate_local_history(self, session_key: str, messages: list[dict[str, Any]]) -> bool:
        """
        将本地会话历史上传到 Honcho 作为文件。

        用于 Honcho 在对话中途激活时保留先前上下文。

        Args:
            session_key: 会话键（例如 "telegram:123456"）。
            messages: 本地消息（包含 role、content、timestamp 的字典列表）。

        Returns:
            上传成功返回 True，否则返回 False。
        """
        session = self._cache.get(session_key)
        if not session:
            logger.warning("No local session cached for '%s', skipping migration", session_key)
            return False

        honcho_session = self._sessions_cache.get(session.honcho_session_id)
        if not honcho_session:
            logger.warning("No Honcho session cached for '%s', skipping migration", session_key)
            return False

        user_peer = self._get_or_create_peer(session.user_peer_id)

        content_bytes = self._format_migration_transcript(session_key, messages)
        first_ts = messages[0].get("timestamp") if messages else None

        try:
            honcho_session.upload_file(
                file=("prior_history.txt", content_bytes, "text/plain"),
                peer=user_peer,
                metadata={"source": "local_jsonl", "count": len(messages)},
                created_at=first_ts,
            )
            logger.info("Migrated %d local messages to Honcho for %s", len(messages), session_key)
            return True
        except Exception as e:
            logger.error("Failed to upload local history to Honcho for %s: %s", session_key, e)
            return False

    @staticmethod
    def _format_migration_transcript(session_key: str, messages: list[dict[str, Any]]) -> bytes:
        """将本地消息格式化为 XML 转录文本，用于 Honcho 文件上传。"""
        timestamps = [m.get("timestamp", "") for m in messages]
        time_range = f"{timestamps[0]} to {timestamps[-1]}" if timestamps else "unknown"

        lines = [
            "<prior_conversation_history>",
            "<context>",
            "This conversation history occurred BEFORE the Honcho memory system was activated.",
            "These messages are the preceding elements of this conversation session and should",
            "be treated as foundational context for all subsequent interactions. The user and",
            "assistant have already established rapport through these exchanges.",
            "</context>",
            "",
            f'<transcript session_key="{session_key}" message_count="{len(messages)}"',
            f'           time_range="{time_range}">',
            "",
        ]
        for msg in messages:
            ts = msg.get("timestamp", "?")
            role = msg.get("role", "unknown")
            content = msg.get("content") or ""
            lines.append(f"[{ts}] {role}: {content}")

        lines.append("")
        lines.append("</transcript>")
        lines.append("</prior_conversation_history>")

        return "\n".join(lines).encode("utf-8")

    def migrate_memory_files(self, session_key: str, memory_dir: str) -> bool:
        """
        将 MEMORY.md 和 USER.md 上传到 Honcho 作为文件。

        用于 Honcho 在已有本地整合记忆的实例上激活时。
        向后兼容 —— 如果文件不存在则跳过。

        Args:
            session_key: 要关联文件的会话键。
            memory_dir: 记忆目录路径（~/.hermes/memories/）。

        Returns:
            至少上传了一个文件返回 True，否则返回 False。
        """
        from pathlib import Path
        memory_path = Path(memory_dir)

        if not memory_path.exists():
            return False

        session = self._cache.get(session_key)
        if not session:
            logger.warning("No local session cached for '%s', skipping memory migration", session_key)
            return False

        honcho_session = self._sessions_cache.get(session.honcho_session_id)
        if not honcho_session:
            logger.warning("No Honcho session cached for '%s', skipping memory migration", session_key)
            return False

        user_peer = self._get_or_create_peer(session.user_peer_id)
        assistant_peer = self._get_or_create_peer(session.assistant_peer_id)

        uploaded = False
        files = [
            (
                "MEMORY.md",
                "consolidated_memory.md",
                "Long-term agent notes and preferences",
                user_peer,
                "user",
            ),
            (
                "USER.md",
                "user_profile.md",
                "User profile and preferences",
                user_peer,
                "user",
            ),
            (
                "SOUL.md",
                "agent_soul.md",
                "Agent persona and identity configuration",
                assistant_peer,
                "ai",
            ),
        ]

        for filename, upload_name, description, target_peer, target_kind in files:
            filepath = memory_path / filename
            if not filepath.exists():
                continue
            content = filepath.read_text(encoding="utf-8").strip()
            if not content:
                continue

            wrapped = (
                f"<prior_memory_file>\n"
                f"<context>\n"
                f"This file was consolidated from local conversations BEFORE Honcho was activated.\n"
                f"{description}. Treat as foundational context for this user.\n"
                f"</context>\n"
                f"\n"
                f"{content}\n"
                f"</prior_memory_file>\n"
            )

            try:
                honcho_session.upload_file(
                    file=(upload_name, wrapped.encode("utf-8"), "text/plain"),
                    peer=target_peer,
                    metadata={
                        "source": "local_memory",
                        "original_file": filename,
                        "target_peer": target_kind,
                    },
                )
                logger.info(
                    "Uploaded %s to Honcho for %s (%s peer)",
                    filename,
                    session_key,
                    target_kind,
                )
                uploaded = True
            except Exception as e:
                logger.error("Failed to upload %s to Honcho: %s", filename, e)

        return uploaded

    @staticmethod
    def _normalize_card(card: Any) -> list[str]:
        """将 Honcho 卡片负载标准化为纯字符串列表。"""
        if not card:
            return []
        if isinstance(card, list):
            return [str(item) for item in card if item]
        return [str(card)]

    def _fetch_peer_card(self, peer_id: str, *, target: str | None = None) -> list[str]:
        """直接从对等方对象获取对等方卡片。

        避免依赖 session.context()，因为在每会话消息模式下，
        即使对等方本身有已填充的卡片，session.context() 也可能返回空的 peer_card。
        """
        peer = self._get_or_create_peer(peer_id)
        getter = getattr(peer, "get_card", None)
        if callable(getter):
            return self._normalize_card(getter(target=target) if target is not None else getter())

        legacy_getter = getattr(peer, "card", None)
        if callable(legacy_getter):
            return self._normalize_card(legacy_getter(target=target) if target is not None else legacy_getter())

        return []

    def _fetch_peer_context(
        self,
        peer_id: str,
        search_query: str | None = None,
        *,
        target: str | None = None,
    ) -> dict[str, Any]:
        """直接从对等方对象获取表示和对等方卡片。"""
        peer = self._get_or_create_peer(peer_id)
        representation = ""
        card: list[str] = []

        try:
            context_kwargs: dict[str, Any] = {}
            if target is not None:
                context_kwargs["target"] = target
            if search_query is not None:
                context_kwargs["search_query"] = search_query
            ctx = peer.context(**context_kwargs) if context_kwargs else peer.context()
            representation = (
                getattr(ctx, "representation", None)
                or getattr(ctx, "peer_representation", None)
                or ""
            )
            card = self._normalize_card(getattr(ctx, "peer_card", None))
        except Exception as e:
            logger.debug("Direct peer.context() failed for '%s': %s", peer_id, e)

        if not representation:
            try:
                representation = (
                    peer.representation(target=target) if target is not None else peer.representation()
                ) or ""
            except Exception as e:
                logger.debug("Direct peer.representation() failed for '%s': %s", peer_id, e)

        if not card:
            try:
                card = self._fetch_peer_card(peer_id, target=target)
            except Exception as e:
                logger.debug("Direct peer card fetch failed for '%s': %s", peer_id, e)

        return {"representation": representation, "card": card}

    def get_session_context(self, session_key: str, peer: str = "user") -> dict[str, Any]:
        """从 Honcho 获取完整的会话上下文，包括摘要。

        使用会话级别的 context() API，返回摘要、
        peer_representation、peer_card 和消息。
        """
        session = self._cache.get(session_key)
        if not session:
            return {}

        honcho_session = self._sessions_cache.get(session.honcho_session_id)
        if not honcho_session:
            # 回退到对等方级别的上下文，遵循请求的对等方
            peer_id = self._resolve_peer_id(session, peer)
            if peer_id is None:
                peer_id = session.user_peer_id
            return self._fetch_peer_context(peer_id, target=peer_id)

        try:
            peer_id = self._resolve_peer_id(session, peer)
            ctx = honcho_session.context(
                summary=True,
                peer_target=peer_id,
                peer_perspective=session.user_peer_id if peer == "user" else session.assistant_peer_id,
            )

            result: dict[str, Any] = {}

            # 摘要
            if ctx.summary:
                result["summary"] = ctx.summary.content

            # 对等方表示和卡片
            if ctx.peer_representation:
                result["representation"] = ctx.peer_representation
            if ctx.peer_card:
                result["card"] = "\n".join(ctx.peer_card)

            # 消息（最近 N 条用于上下文）
            if ctx.messages:
                recent = ctx.messages[-10:]  # 最近 10 条消息
                result["recent_messages"] = [
                    {"role": getattr(m, "peer_id", "unknown"), "content": (m.content or "")[:500]}
                    for m in recent
                ]

            return result
        except Exception as e:
            logger.debug("Session context fetch failed: %s", e)
            return {}

    def _resolve_peer_id(self, session: HonchoSession, peer: str | None) -> str:
        """将对等方别名或显式对等方 ID 解析为具体的 Honcho 对等方 ID。

        始终返回非空字符串：已知的对等方 ID 或调用方提供的
        别名/ID 的清理版本。
        """
        candidate = (peer or "user").strip()
        if not candidate:
            return session.user_peer_id

        normalized = self._sanitize_id(candidate)
        if normalized == self._sanitize_id("user"):
            return session.user_peer_id
        if normalized == self._sanitize_id("ai"):
            return session.assistant_peer_id

        return normalized

    def _resolve_observer_target(
        self,
        session: HonchoSession,
        peer: str | None,
    ) -> tuple[str, str | None]:
        """为上下文/搜索/画像查询解析观察者和目标对等方 ID。"""
        target_peer_id = self._resolve_peer_id(session, peer)

        if target_peer_id == session.assistant_peer_id:
            return session.assistant_peer_id, session.assistant_peer_id

        if self._ai_observe_others:
            return session.assistant_peer_id, target_peer_id

        return target_peer_id, None

    def get_peer_card(self, session_key: str, peer: str = "user") -> list[str]:
        """
        获取对等方卡片 —— 关键事实的精选列表。

        快速，无 LLM 推理。返回 Honcho 从目标对等方推断出的
        原始结构化事实（名称、角色、偏好、模式）。
        不可用时返回空列表。
        """
        session = self._cache.get(session_key)
        if not session:
            return []

        try:
            observer_peer_id, target_peer_id = self._resolve_observer_target(session, peer)
            return self._fetch_peer_card(observer_peer_id, target=target_peer_id)
        except Exception as e:
            logger.debug("Failed to fetch peer card from Honcho: %s", e)
            return []

    def search_context(
        self,
        session_key: str,
        query: str,
        max_tokens: int = 800,
        peer: str = "user",
    ) -> str:
        """
        对 Honcho 会话上下文进行语义搜索。

        返回按与查询相关性排序的原始摘录。无 LLM 推理 ——
        比 dialectic_query 更便宜更快。适用于模型自行
        综合的事实查找场景。

        Args:
            session_key: 要搜索的会话。
            query: 用于语义匹配的搜索查询。
            max_tokens: 返回内容的 token 预算。
            peer: 要搜索的对等方别名或显式对等方 ID。

        Returns:
            相关上下文摘录的字符串，无结果时返回空字符串。
        """
        session = self._cache.get(session_key)
        if not session:
            return ""

        try:
            observer_peer_id, target = self._resolve_observer_target(session, peer)

            ctx = self._fetch_peer_context(
                observer_peer_id,
                search_query=query,
                target=target,
            )
            parts = []
            if ctx["representation"]:
                parts.append(ctx["representation"])
            card = ctx["card"] or []
            if card:
                parts.append("\n".join(f"- {f}" for f in card))
            return "\n\n".join(parts)
        except Exception as e:
            logger.debug("Honcho search_context failed: %s", e)
            return ""

    def create_conclusion(self, session_key: str, content: str, peer: str = "user") -> bool:
        """向 Honcho 写入关于目标对等方的结论。

        结论是一个对等方对另一个对等方或自身的观察 ——
        偏好、纠正、澄清和项目上下文。
        它们会馈入目标对等方的卡片和表示中。

        Args:
            session_key: 要关联结论的会话。
            content: 结论文本。
            peer: 对等方别名或显式对等方 ID。"user" 是默认别名。

        Returns:
            成功返回 True，失败返回 False。
        """
        if not content or not content.strip():
            return False

        session = self._cache.get(session_key)
        if not session:
            logger.warning("No session cached for '%s', skipping conclusion", session_key)
            return False

        try:
            target_peer_id = self._resolve_peer_id(session, peer)
            if target_peer_id is None:
                logger.warning("Could not resolve conclusion peer '%s' for session '%s'", peer, session_key)
                return False

            if target_peer_id == session.assistant_peer_id:
                assistant_peer = self._get_or_create_peer(session.assistant_peer_id)
                conclusions_scope = assistant_peer.conclusions_of(session.assistant_peer_id)
            elif self._ai_observe_others:
                assistant_peer = self._get_or_create_peer(session.assistant_peer_id)
                conclusions_scope = assistant_peer.conclusions_of(target_peer_id)
            else:
                target_peer = self._get_or_create_peer(target_peer_id)
                conclusions_scope = target_peer.conclusions_of(target_peer_id)

            conclusions_scope.create([{
                "content": content.strip(),
                "session_id": session.honcho_session_id,
            }])
            logger.info("Created conclusion about %s for %s: %s", target_peer_id, session_key, content[:80])
            return True
        except Exception as e:
            logger.error("Failed to create conclusion: %s", e)
            return False

    def delete_conclusion(self, session_key: str, conclusion_id: str, peer: str = "user") -> bool:
        """按 ID 删除结论。仅用于 PII 移除。

        Args:
            session_key: 用于对等方解析的会话键。
            conclusion_id: 要删除的结论 ID。
            peer: 对等方别名或显式对等方 ID。

        Returns:
            成功返回 True，失败返回 False。
        """
        session = self._cache.get(session_key)
        if not session:
            return False
        try:
            target_peer_id = self._resolve_peer_id(session, peer)
            if target_peer_id == session.assistant_peer_id:
                observer = self._get_or_create_peer(session.assistant_peer_id)
                scope = observer.conclusions_of(session.assistant_peer_id)
            elif self._ai_observe_others:
                observer = self._get_or_create_peer(session.assistant_peer_id)
                scope = observer.conclusions_of(target_peer_id)
            else:
                target_peer = self._get_or_create_peer(target_peer_id)
                scope = target_peer.conclusions_of(target_peer_id)
            scope.delete(conclusion_id)
            logger.info("Deleted conclusion %s for %s", conclusion_id, session_key)
            return True
        except Exception as e:
            logger.error("Failed to delete conclusion %s: %s", conclusion_id, e)
            return False

    def set_peer_card(self, session_key: str, card: list[str], peer: str = "user") -> list[str] | None:
        """更新对等方的卡片。

        Args:
            session_key: 用于对等方解析的会话键。
            card: 新的对等方卡片，以事实字符串列表形式提供。
            peer: 对等方别名或显式对等方 ID。

        Returns:
            成功时返回更新后的卡片，失败时返回 None。
        """
        session = self._cache.get(session_key)
        if not session:
            return None
        try:
            peer_id = self._resolve_peer_id(session, peer)
            if peer_id is None:
                logger.warning("Could not resolve peer '%s' for set_peer_card in session '%s'", peer, session_key)
                return None
            peer_obj = self._get_or_create_peer(peer_id)
            result = peer_obj.set_card(card)
            logger.info("Updated peer card for %s (%d facts)", peer_id, len(card))
            return result
        except Exception as e:
            logger.error("Failed to set peer card: %s", e)
            return None

    def seed_ai_identity(self, session_key: str, content: str, source: str = "manual") -> bool:
        """
        从文本内容为 AI 对等方的 Honcho 表示进行初始化播种。

        用于从 SOUL.md、导出的聊天记录或任何结构化描述
        初始化 AI 身份。内容作为助手对等方消息发送，
        以便 Honcho 的推理模型能够将其纳入。

        Args:
            session_key: 要关联的会话键。
            content: 要播种的身份/人格内容。
            source: 来源的元数据标签（例如 "soul_md"、"export"）。

        Returns:
            成功返回 True，失败返回 False。
        """
        if not content or not content.strip():
            return False

        session = self._cache.get(session_key)
        if not session:
            logger.warning("No session cached for '%s', skipping AI seed", session_key)
            return False

        assistant_peer = self._get_or_create_peer(session.assistant_peer_id)
        honcho_session = self._sessions_cache.get(session.honcho_session_id)
        if not honcho_session:
            logger.warning("No Honcho session cached for '%s', skipping AI seed", session_key)
            return False

        try:
            wrapped = (
                f"<ai_identity_seed>\n"
                f"<source>{source}</source>\n"
                f"\n"
                f"{content.strip()}\n"
                f"</ai_identity_seed>"
            )
            honcho_session.add_messages([assistant_peer.message(wrapped)])
            logger.info("Seeded AI identity from '%s' into %s", source, session_key)
            return True
        except Exception as e:
            logger.error("Failed to seed AI identity: %s", e)
            return False

    def get_ai_representation(self, session_key: str) -> dict[str, str]:
        """
        获取 AI 对等方当前的 Honcho 表示。

        Returns:
            包含 'representation' 和 'card' 键的字典，不可用时为空字符串。
        """
        session = self._cache.get(session_key)
        if not session:
            return {"representation": "", "card": ""}

        try:
            ctx = self._fetch_peer_context(session.assistant_peer_id, target=session.assistant_peer_id)
            return {
                "representation": ctx["representation"] or "",
                "card": "\n".join(ctx["card"]),
            }
        except Exception as e:
            logger.debug("Failed to fetch AI representation: %s", e)
            return {"representation": "", "card": ""}

    def list_sessions(self) -> list[dict[str, Any]]:
        """列出所有缓存的会话。"""
        return [
            {
                "key": s.key,
                "created_at": s.created_at.isoformat(),
                "updated_at": s.updated_at.isoformat(),
                "message_count": len(s.messages),
            }
            for s in self._cache.values()
        ]
