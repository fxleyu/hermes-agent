"""Honcho 记忆插件 — Honcho AI 原生记忆的 MemoryProvider。

提供跨会话用户建模，包括辩证问答、语义搜索、
同伴卡片和持久化结论，通过 Honcho SDK 实现。Honcho 提供 AI 原生的跨会话用户
建模，包括辩证问答、语义搜索、同伴卡片和结论。

4 个工具（profile、search、context、conclude）通过
MemoryProvider 接口暴露。

配置：使用现有的 Honcho 配置链：
  1. $HERMES_HOME/honcho.json（配置文件作用域）
  2. ~/.honcho/config.json（旧版全局）
  3. 环境变量
"""

from __future__ import annotations

import json
import logging
import re
import threading
from typing import Any, Dict, List, Optional

from agent.memory_provider import MemoryProvider
from tools.registry import tool_error

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 工具 schema（从 tools/honcho_tools.py 迁移）
# ---------------------------------------------------------------------------

PROFILE_SCHEMA = {
    "name": "honcho_profile",
    "description": (
        "Retrieve or update a peer card from Honcho — a curated list of key facts "
        "about that peer (name, role, preferences, communication style, patterns). "
        "Pass `card` to update; omit `card` to read."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "peer": {
                "type": "string",
                "description": "Peer to query. Built-in aliases: 'user' (default), 'ai'. Or pass any peer ID from this workspace.",
            },
            "card": {
                "type": "array",
                "items": {"type": "string"},
                "description": "New peer card as a list of fact strings. Omit to read the current card.",
            },
        },
        "required": [],
    },
}

SEARCH_SCHEMA = {
    "name": "honcho_search",
    "description": (
        "Semantic search over Honcho's stored context about a peer. "
        "Returns raw excerpts ranked by relevance — no LLM synthesis. "
        "Cheaper and faster than honcho_reasoning. "
        "Good when you want to find specific past facts and reason over them yourself."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "What to search for in Honcho's memory.",
            },
            "max_tokens": {
                "type": "integer",
                "description": "Token budget for returned context (default 800, max 2000).",
            },
            "peer": {
                "type": "string",
                "description": "Peer to query. Built-in aliases: 'user' (default), 'ai'. Or pass any peer ID from this workspace.",
            },
        },
        "required": ["query"],
    },
}

REASONING_SCHEMA = {
    "name": "honcho_reasoning",
    "description": (
        "Ask Honcho a natural language question and get a synthesized answer. "
        "Uses Honcho's LLM (dialectic reasoning) — higher cost than honcho_profile or honcho_search. "
        "Can query about any peer via alias or explicit peer ID. "
        "Pass reasoning_level to control depth: minimal (fast/cheap), low (default), "
        "medium, high, max (deep/expensive). Omit for configured default."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "A natural language question.",
            },
            "reasoning_level": {
                "type": "string",
                "description": (
                    "Override the default reasoning depth. "
                    "Omit to use the configured default (typically low). "
                    "Guide:\n"
                    "- minimal: quick factual lookups (name, role, simple preference)\n"
                    "- low: straightforward questions with clear answers\n"
                    "- medium: multi-aspect questions requiring synthesis across observations\n"
                    "- high: complex behavioral patterns, contradictions, deep analysis\n"
                    "- max: thorough audit-level analysis, leave no stone unturned"
                ),
                "enum": ["minimal", "low", "medium", "high", "max"],
            },
            "peer": {
                "type": "string",
                "description": "Peer to query. Built-in aliases: 'user' (default), 'ai'. Or pass any peer ID from this workspace.",
            },
        },
        "required": ["query"],
    },
}

CONTEXT_SCHEMA = {
    "name": "honcho_context",
    "description": (
        "Retrieve full session context from Honcho — summary, peer representation, "
        "peer card, and recent messages. No LLM synthesis. "
        "Cheaper than honcho_reasoning. Use this to see what Honcho knows about "
        "the current conversation and the specified peer."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Optional focus query to filter context. Omit for full session context snapshot.",
            },
            "peer": {
                "type": "string",
                "description": "Peer to query. Built-in aliases: 'user' (default), 'ai'. Or pass any peer ID from this workspace.",
            },
        },
        "required": [],
    },
}

CONCLUDE_SCHEMA = {
    "name": "honcho_conclude",
    "description": (
        "Write or delete a conclusion about a peer in Honcho's memory. "
        "Conclusions are persistent facts that build a peer's profile. "
        "You MUST pass exactly one of: `conclusion` (to create) or `delete_id` (to delete). "
        "Passing neither is an error. "
        "Deletion is only for PII removal — Honcho self-heals incorrect conclusions over time."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "conclusion": {
                "type": "string",
                "description": "A factual statement to persist. Provide this when creating a conclusion. Do not send it together with delete_id.",
            },
            "delete_id": {
                "type": "string",
                "description": "Conclusion ID to delete for PII removal. Provide this when deleting a conclusion. Do not send it together with conclusion.",
            },
            "peer": {
                "type": "string",
                "description": "Peer to query. Built-in aliases: 'user' (default), 'ai'. Or pass any peer ID from this workspace.",
            },
        },
        "required": [],
    },
}


ALL_TOOL_SCHEMAS = [PROFILE_SCHEMA, SEARCH_SCHEMA, REASONING_SCHEMA, CONTEXT_SCHEMA, CONCLUDE_SCHEMA]


# ---------------------------------------------------------------------------
# MemoryProvider 实现
# ---------------------------------------------------------------------------

class HonchoMemoryProvider(MemoryProvider):
    """Honcho AI 原生记忆，具备辩证问答和持久化用户建模。"""

    def __init__(self):
        self._manager = None   # HonchoSessionManager 会话管理器
        self._config = None    # HonchoClientConfig 客户端配置
        self._session_key = ""
        self._prefetch_result = ""
        self._prefetch_lock = threading.Lock()
        self._prefetch_thread: Optional[threading.Thread] = None
        self._sync_thread: Optional[threading.Thread] = None

        # B1: recall_mode — 在 initialize 期间从配置设置
        self._recall_mode = "hybrid"  # "context"、"tools" 或 "hybrid"

        # 基础上下文缓存 — 按 context_cadence 刷新，非冻结
        self._base_context_cache: Optional[str] = None
        self._base_context_lock = threading.Lock()

        # B5: 成本感知的轮次计数和节奏控制
        self._turn_count = 0
        self._injection_frequency = "every-turn"  # 或 "first-turn"
        self._context_cadence = 1   # 上下文 API 调用之间的最小轮次间隔
        self._dialectic_cadence = 3  # 辩证 API 调用之间的最小轮次间隔
        self._dialectic_depth = 1   # 每个辩证周期的 .chat() 调用次数（1-3）
        self._dialectic_depth_levels: list[str] | None = None  # 每轮的推理级别
        self._reasoning_level_cap: Optional[str] = None  # "minimal"、"low"、"medium"、"high"
        self._last_context_turn = -999
        self._last_dialectic_turn = -999

        # Port #1957: tools-only 模式的延迟会话初始化
        self._session_initialized = False
        self._lazy_init_kwargs: Optional[dict] = None
        self._lazy_init_session_id: Optional[str] = None

        # Port #4053: 定时任务守卫 — 为 True 时插件完全不活跃
        self._cron_skipped = False

    @property
    def name(self) -> str:
        return "honcho"

    def is_available(self) -> bool:
        """检查 Honcho 是否已配置。无网络调用。"""
        try:
            from plugins.memory.honcho.client import HonchoClientConfig
            cfg = HonchoClientConfig.from_global_config()
            # Port #2645: 仅 baseUrl 验证 — api_key 或 base_url 二者有一即可
            return cfg.enabled and bool(cfg.api_key or cfg.base_url)
        except Exception:
            return False

    def save_config(self, values, hermes_home):
        """将配置写入 $HERMES_HOME/honcho.json（Honcho SDK 原生格式）。"""
        import json
        from pathlib import Path
        config_path = Path(hermes_home) / "honcho.json"
        existing = {}
        if config_path.exists():
            try:
                existing = json.loads(config_path.read_text())
            except Exception:
                pass
        existing.update(values)
        config_path.write_text(json.dumps(existing, indent=2))

    def get_config_schema(self):
        return [
            {"key": "api_key", "description": "Honcho API key", "secret": True, "env_var": "HONCHO_API_KEY", "url": "https://app.honcho.dev"},
            {"key": "baseUrl", "description": "Honcho base URL (for self-hosted)"},
        ]

    def post_setup(self, hermes_home: str, config: dict) -> None:
        """在选择提供者后运行完整的 Honcho 设置向导。"""
        import types
        from plugins.memory.honcho.cli import cmd_setup
        cmd_setup(types.SimpleNamespace())

    def initialize(self, session_id: str, **kwargs) -> None:
        """初始化 Honcho 会话管理器。

        处理：定时任务守卫、recall_mode、会话名称解析、
        对等记忆模式、SOUL.md ai_peer 同步、记忆文件迁移、
        以及初始化时的上下文预热。
        """
        try:
            # ----- Port #4053: 定时任务守卫 -----
            agent_context = kwargs.get("agent_context", "")
            platform = kwargs.get("platform", "cli")
            if agent_context in ("cron", "flush") or platform == "cron":
                logger.debug("Honcho skipped: cron/flush context (agent_context=%s, platform=%s)",
                             agent_context, platform)
                self._cron_skipped = True
                return

            from plugins.memory.honcho.client import HonchoClientConfig, get_honcho_client
            from plugins.memory.honcho.session import HonchoSessionManager

            cfg = HonchoClientConfig.from_global_config()
            if not cfg.enabled or not (cfg.api_key or cfg.base_url):
                logger.debug("Honcho not configured — plugin inactive")
                return

            # 使用网关 user_id 覆盖 peer_name 以实现按用户记忆隔离。
            # 仅在未显式配置 peerName 时生效 — 显式 peerName
            # 意味着用户选择了自己的身份；原始 user_id（如 Telegram
            # 聊天 ID）不应静默替换它。
            _gw_user_id = kwargs.get("user_id")
            if _gw_user_id and not cfg.peer_name:
                cfg.peer_name = _gw_user_id

            self._config = cfg

            # ----- B1: 从配置读取 recall_mode -----
            self._recall_mode = cfg.recall_mode  # "context", "tools", or "hybrid"
            logger.debug("Honcho recall_mode: %s", self._recall_mode)

            # ----- B5: 成本感知配置 -----
            try:
                raw = cfg.raw or {}
                self._injection_frequency = raw.get("injectionFrequency", "every-turn")
                self._context_cadence = int(raw.get("contextCadence", 1))
                self._dialectic_cadence = int(raw.get("dialecticCadence", 3))
                self._dialectic_depth = max(1, min(cfg.dialectic_depth, 3))
                self._dialectic_depth_levels = cfg.dialectic_depth_levels
                cap = raw.get("reasoningLevelCap")
                if cap and cap in ("minimal", "low", "medium", "high"):
                    self._reasoning_level_cap = cap
            except Exception as e:
                logger.debug("Honcho cost-awareness config parse error: %s", e)

            # ----- Port #1969: 从 SOUL.md 同步 aiPeer — 已移除 -----
            # SOUL.md 是人格内容，不是身份配置。aiPeer 应仅
            # 来自 honcho.json（主机块或根节点）或默认值。
            # 详见 scratch/memory-plugin-ux-specs.md #10 的说明。

            # ----- Port #1957: tools-only 模式的延迟会话初始化 -----
            if self._recall_mode == "tools":
                if cfg.init_on_session_start:
                    # 即使在 tools 模式下也立即初始化（需主动选择）
                    self._do_session_init(cfg, session_id, **kwargs)
                    return
                # 延迟实际会话创建到首次工具调用时
                self._lazy_init_kwargs = kwargs
                self._lazy_init_session_id = session_id
                # 仍需客户端引用用于 _ensure_session
                self._config = cfg
                logger.debug("Honcho tools-only mode — deferring session init until first tool call")
                return

            # ----- 立即初始化（context 或 hybrid 模式）-----
            self._do_session_init(cfg, session_id, **kwargs)

        except ImportError:
            logger.debug("honcho-ai package not installed — plugin inactive")
        except Exception as e:
            logger.warning("Honcho init failed: %s", e)
            self._manager = None

    def _do_session_init(self, cfg, session_id: str, **kwargs) -> None:
        """立即初始化和延迟初始化路径共享的会话初始化逻辑。"""
        from plugins.memory.honcho.client import get_honcho_client
        from plugins.memory.honcho.session import HonchoSessionManager

        client = get_honcho_client(cfg)
        self._manager = HonchoSessionManager(
            honcho=client,
            config=cfg,
            context_tokens=cfg.context_tokens,
        )

        # ----- B3: 解析会话名称 -----
        session_title = kwargs.get("session_title")
        gateway_session_key = kwargs.get("gateway_session_key")
        self._session_key = (
            cfg.resolve_session_name(
                session_title=session_title,
                session_id=session_id,
                gateway_session_key=gateway_session_key,
            )
            or session_id
            or "hermes-default"
        )
        logger.debug("Honcho session key resolved: %s", self._session_key)

        # 立即创建会话
        session = self._manager.get_or_create(self._session_key)
        self._session_initialized = True

        # ----- B6: 记忆文件迁移（一次性，用于新会话）-----
        # 在 per-session 策略下跳过：每次 Hermes 运行按设计创建一个新的
        # Honcho 会话，因此上传 MEMORY.md/USER.md/SOUL.md 到每个会话
        # 会向后端充斥短生命周期的重复数据，而非执行一次性迁移。
        try:
            if not session.messages and cfg.session_strategy != "per-session":
                from hermes_constants import get_hermes_home
                mem_dir = str(get_hermes_home() / "memories")
                self._manager.migrate_memory_files(self._session_key, mem_dir)
                logger.debug("Honcho memory file migration attempted for new session: %s", self._session_key)
            elif cfg.session_strategy == "per-session":
                logger.debug(
                    "Honcho memory file migration skipped: per-session strategy creates a fresh session per run (%s)",
                    self._session_key,
                )
        except Exception as e:
            logger.debug("Honcho memory file migration skipped: %s", e)

        # ----- B7: 初始化时预热上下文 -----
        if self._recall_mode in ("context", "hybrid"):
            try:
                self._manager.prefetch_context(self._session_key)
                self._manager.prefetch_dialectic(self._session_key, "What should I know about this user?")
                logger.debug("Honcho pre-warm threads started for session: %s", self._session_key)
            except Exception as e:
                logger.debug("Honcho pre-warm failed: %s", e)

    def _ensure_session(self) -> bool:
        """延迟初始化 Honcho 会话（用于 tools-only 模式）。

        如果管理器已就绪返回 True，否则返回 False。
        """
        if self._manager and self._session_initialized:
            return True
        if self._cron_skipped:
            return False
        if not self._config or not self._lazy_init_kwargs:
            return False

        try:
            self._do_session_init(
                self._config,
                self._lazy_init_session_id or "hermes-default",
                **self._lazy_init_kwargs,
            )
            # 清除延迟引用
            self._lazy_init_kwargs = None
            self._lazy_init_session_id = None
            return self._manager is not None
        except Exception as e:
            logger.warning("Honcho lazy session init failed: %s", e)
            return False

    def _format_first_turn_context(self, ctx: dict) -> str:
        """将预取的上下文字典格式化为可读的系统提示块。"""
        parts = []

        # 会话摘要 — 会话范围的上下文，优先放在最前面
        summary = ctx.get("summary", "")
        if summary:
            parts.append(f"## Session Summary\n{summary}")

        rep = ctx.get("representation", "")
        if rep:
            parts.append(f"## User Representation\n{rep}")

        card = ctx.get("card", "")
        if card:
            parts.append(f"## User Peer Card\n{card}")

        ai_rep = ctx.get("ai_representation", "")
        if ai_rep:
            parts.append(f"## AI Self-Representation\n{ai_rep}")

        ai_card = ctx.get("ai_card", "")
        if ai_card:
            parts.append(f"## AI Identity Card\n{ai_card}")

        if not parts:
            return ""
        return "\n\n".join(parts)

    def system_prompt_block(self) -> str:
        """返回系统提示文本，根据 recall_mode 调整。

        仅返回模式标头和工具指令 — 静态文本，
        不会在轮次之间变化（对提示缓存友好）。
        动态上下文（表示、卡片）通过 prefetch() 注入。
        """
        if self._cron_skipped:
            return ""
        if not self._manager or not self._session_key:
            # tools-only 模式下没有会话时仍返回最小的提示块
            if self._recall_mode == "tools" and self._config:
                return (
                    "# Honcho Memory\n"
                    "Active (tools-only mode). Use honcho_profile, honcho_search, "
                    "honcho_reasoning, honcho_context, and honcho_conclude tools to access user memory."
                )
            return ""

        # ----- B1: 根据 recall_mode 调整文本 -----
        if self._recall_mode == "context":
            header = (
                "# Honcho Memory\n"
                "Active (context-injection mode). Relevant user context is automatically "
                "injected before each turn. No memory tools are available — context is "
                "managed automatically."
            )
        elif self._recall_mode == "tools":
            header = (
                "# Honcho Memory\n"
                "Active (tools-only mode). Use honcho_profile for a quick factual snapshot, "
                "honcho_search for raw excerpts, honcho_context for raw peer context, "
                "honcho_reasoning for synthesized answers, "
                "honcho_conclude to save facts about the user. "
                "No automatic context injection — you must use tools to access memory."
            )
        else:  # hybrid
            header = (
                "# Honcho Memory\n"
                "Active (hybrid mode). Relevant context is auto-injected AND memory tools are available. "
                "Use honcho_profile for a quick factual snapshot, "
                "honcho_search for raw excerpts, honcho_context for raw peer context, "
                "honcho_reasoning for synthesized answers, "
                "honcho_conclude to save facts about the user."
            )

        return header

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        """返回基础上下文（表示 + 卡片）加上辩证补充。

        组装两层：
        1. 来自 peer.context() 的基础上下文 — 缓存，按 context_cadence 刷新
        2. 辩证补充 — 缓存，按 dialectic_cadence 刷新

        B1: recall_mode 为 "tools" 时返回空（不注入）。
        B5: 遵循 injection_frequency — "first-turn" 在第 0 轮后返回缓存/空。
        Port #3265: 截断到 context_tokens 预算。
        """
        if self._cron_skipped:
            return ""

        # B1: tools-only 模式 — 不自动注入
        if self._recall_mode == "tools":
            return ""

        # B5: injection_frequency — 如果是 "first-turn" 且过了第一轮，返回空。
        # _turn_count 从 1 开始（第一条用户消息 = 1），所以 > 1 表示"过了第一轮"。
        if self._injection_frequency == "first-turn" and self._turn_count > 1:
            return ""

        parts = []

        # ----- 第 1 层：基础上下文（表示 + 卡片）-----
        # 首次调用时同步获取，以确保第 1 轮不为空。
        # 之后从缓存提供，并在 cadence 时在后台刷新。
        with self._base_context_lock:
            if self._base_context_cache is None:
                # 首次调用 — 同步获取
                try:
                    ctx = self._manager.get_prefetch_context(self._session_key)
                    self._base_context_cache = self._format_first_turn_context(ctx) if ctx else ""
                    self._last_context_turn = self._turn_count
                except Exception as e:
                    logger.debug("Honcho base context fetch failed: %s", e)
                    self._base_context_cache = ""
            base_context = self._base_context_cache

        # 检查后台上下文预取是否有更新的结果
        if self._manager:
            fresh_ctx = self._manager.pop_context_result(self._session_key)
            if fresh_ctx:
                formatted = self._format_first_turn_context(fresh_ctx)
                if formatted:
                    with self._base_context_lock:
                        self._base_context_cache = formatted
                    base_context = formatted

        if base_context:
            parts.append(base_context)

        # ----- 第 2 层：辩证补充 -----
        # 在最初第一轮时，还没有运行 queue_prefetch() 所以
        # 辩证结果为空。使用有界超时运行，避免慢速
        # Honcho 连接无限期阻塞首次响应。
        # 超时后跳过结果，queue_prefetch() 将在
        # 下一个 cadence 允许的轮次中通过异步路径拾取。
        if self._last_dialectic_turn == -999 and query:
            _first_turn_timeout = (
                self._config.timeout if self._config and self._config.timeout else 8.0
            )
            _result_holder: list[str] = []

            def _run_first_turn() -> None:
                try:
                    _result_holder.append(self._run_dialectic_depth(query))
                except Exception as exc:
                    logger.debug("Honcho first-turn dialectic failed: %s", exc)

            _t = threading.Thread(target=_run_first_turn, daemon=True)
            _t.start()
            _t.join(timeout=_first_turn_timeout)
            if not _t.is_alive():
                first_turn_dialectic = _result_holder[0] if _result_holder else ""
                if first_turn_dialectic and first_turn_dialectic.strip():
                    with self._prefetch_lock:
                        self._prefetch_result = first_turn_dialectic
                self._last_dialectic_turn = self._turn_count
            else:
                logger.debug(
                    "Honcho first-turn dialectic timed out (%.1fs) — "
                    "will inject at next cadence-allowed turn",
                    _first_turn_timeout,
                )
                # 不更新 _last_dialectic_turn：queue_prefetch() 将
                # 在下一个 cadence 允许的轮次通过异步路径重试。

        if self._prefetch_thread and self._prefetch_thread.is_alive():
            self._prefetch_thread.join(timeout=3.0)
        with self._prefetch_lock:
            dialectic_result = self._prefetch_result
            self._prefetch_result = ""

        if dialectic_result and dialectic_result.strip():
            parts.append(dialectic_result)

        if not parts:
            return ""

        result = "\n\n".join(parts)

        # ----- Port #3265: 令牌预算限制 -----
        result = self._truncate_to_budget(result)

        return result

    def _truncate_to_budget(self, text: str) -> str:
        """将文本截断以适应 context_tokens 预算（如已设置）。"""
        if not self._config or not self._config.context_tokens:
            return text
        budget_chars = self._config.context_tokens * 4  # 保守的字符估算
        if len(text) <= budget_chars:
            return text
        # 在单词边界处截断
        truncated = text[:budget_chars]
        last_space = truncated.rfind(" ")
        if last_space > budget_chars * 0.8:
            truncated = truncated[:last_space]
        return truncated + " …"

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        """为即将到来的轮次启动后台预取线程。

        B5: 独立检查辩证和上下文刷新的节奏。
        上下文刷新更新基础层（表示 + 卡片）。
        辩证触发 LLM 推理补充。
        """
        if self._cron_skipped:
            return
        if not self._manager or not self._session_key or not query:
            return

        # B1: tools-only 模式 — 不预取
        if self._recall_mode == "tools":
            return

        # ----- 上下文刷新（基础层）— 独立节奏 -----
        if self._context_cadence <= 1 or (self._turn_count - self._last_context_turn) >= self._context_cadence:
            self._last_context_turn = self._turn_count
            try:
                self._manager.prefetch_context(self._session_key, query)
            except Exception as e:
                logger.debug("Honcho context prefetch failed: %s", e)

        # ----- 辩证预取（补充层）-----
        # B5: 节奏检查 — 如果距上次辩证调用太近则跳过
        if self._dialectic_cadence > 1:
            if (self._turn_count - self._last_dialectic_turn) < self._dialectic_cadence:
                logger.debug("Honcho dialectic prefetch skipped: cadence %d, turns since last: %d",
                             self._dialectic_cadence, self._turn_count - self._last_dialectic_turn)
                return

        self._last_dialectic_turn = self._turn_count

        def _run():
            try:
                result = self._run_dialectic_depth(query)
                if result and result.strip():
                    with self._prefetch_lock:
                        self._prefetch_result = result
            except Exception as e:
                logger.debug("Honcho prefetch failed: %s", e)

        self._prefetch_thread = threading.Thread(
            target=_run, daemon=True, name="honcho-prefetch"
        )
        self._prefetch_thread.start()

    # ----- 辩证深度：多轮 .chat() 调用，支持冷/热提示 -----

    # 当 dialecticDepthLevels 未配置时，每个深度/轮次的比例推理级别。
    # 基础级别为 dialecticReasoningLevel。
    # 索引: (depth, pass) -> 相对于基础的级别。
    _PROPORTIONAL_LEVELS: dict[tuple[int, int], str] = {
        # 深度 1：单轮，使用基础级别
        (1, 0): "base",
        # 深度 2：第 0 轮较轻，第 1 轮使用基础级别
        (2, 0): "minimal",
        (2, 1): "base",
        # 深度 3：第 0 轮较轻，第 1 轮基础级别，第 2 轮比 minimal 高一级
        (3, 0): "minimal",
        (3, 1): "base",
        (3, 2): "low",
    }

    _LEVEL_ORDER = ("minimal", "low", "medium", "high", "max")

    def _resolve_pass_level(self, pass_idx: int) -> str:
        """解析给定轮次的推理级别。

        如果已配置则使用 dialecticDepthLevels，否则使用
        相对于 dialecticReasoningLevel 的比例默认值。
        """
        if self._dialectic_depth_levels and pass_idx < len(self._dialectic_depth_levels):
            return self._dialectic_depth_levels[pass_idx]

        base = (self._config.dialectic_reasoning_level if self._config else "low")
        mapping = self._PROPORTIONAL_LEVELS.get((self._dialectic_depth, pass_idx))
        if mapping is None or mapping == "base":
            return base
        return mapping

    def _build_dialectic_prompt(self, pass_idx: int, prior_results: list[str], is_cold: bool) -> str:
        """为给定的辩证轮次构建提示。

        第 0 轮：冷启动（通用用户查询）或热启动（会话范围）。
        第 1 轮：自审计 / 针对第 0 轮缺口的定向综合。
        第 2 轮：跨前几轮的调和 / 矛盾检查。
        """
        if pass_idx == 0:
            if is_cold:
                return (
                    "Who is this person? What are their preferences, goals, "
                    "and working style? Focus on facts that would help an AI "
                    "assistant be immediately useful."
                )
            return (
                "Given what's been discussed in this session so far, what "
                "context about this user is most relevant to the current "
                "conversation? Prioritize active context over biographical facts."
            )
        elif pass_idx == 1:
            prior = prior_results[-1] if prior_results else ""
            return (
                f"Given this initial assessment:\n\n{prior}\n\n"
                "What gaps remain in your understanding that would help "
                "going forward? Synthesize what you actually know about "
                "the user's current state and immediate needs, grounded "
                "in evidence from recent sessions."
            )
        else:
            # 第 2 轮：调和
            return (
                f"Prior passes produced:\n\n"
                f"Pass 1:\n{prior_results[0] if len(prior_results) > 0 else '(empty)'}\n\n"
                f"Pass 2:\n{prior_results[1] if len(prior_results) > 1 else '(empty)'}\n\n"
                "Do these assessments cohere? Reconcile any contradictions "
                "and produce a final, concise synthesis of what matters most "
                "for the current conversation."
            )

    @staticmethod
    def _signal_sufficient(result: str) -> bool:
        """检查辩证轮次是否返回了足够的信号以跳过后续轮次。

        启发式：超过 100 个字符且具有结构化输出
        （章节标题、项目符号或有序列表）的响应被视为充分。
        """
        if not result or len(result.strip()) < 100:
            return False
        # 带结构化输出的章节/项目符号是强信号
        if "\n" in result and (
            "##" in result
            or "•" in result
            or re.search(r"^[*-] ", result, re.MULTILINE)
            or re.search(r"^\s*\d+\. ", result, re.MULTILINE)
        ):
            return True
        # 即使没有结构化，只要足够长也算
        return len(result.strip()) > 300

    def _run_dialectic_depth(self, query: str) -> str:
        """执行最多 dialecticDepth 次 .chat() 调用，支持条件提前退出。

        冷启动（无基础上下文）：通用的面向用户的查询。
        热会话（有基础上下文）：会话范围的查询。
        每轮都是有条件的 — 如果前一轮返回了强信号则提前退出。
        返回最佳（通常是最后一轮）的结果。
        """
        if not self._manager or not self._session_key:
            return ""

        is_cold = not self._base_context_cache
        results: list[str] = []

        for i in range(self._dialectic_depth):
            if i == 0:
                prompt = self._build_dialectic_prompt(0, results, is_cold)
            else:
                # 如果前一轮有强信号则跳过后续轮次
                if results and self._signal_sufficient(results[-1]):
                    logger.debug("Honcho dialectic depth %d: pass %d skipped, prior signal sufficient",
                                 self._dialectic_depth, i)
                    break
                prompt = self._build_dialectic_prompt(i, results, is_cold)

            level = self._resolve_pass_level(i)
            logger.debug("Honcho dialectic depth %d: pass %d, level=%s, cold=%s",
                         self._dialectic_depth, i, level, is_cold)

            result = self._manager.dialectic_query(
                self._session_key, prompt,
                reasoning_level=level,
                peer="user",
            )
            results.append(result or "")

        # 返回最后一个非空结果（运行过的最深轮次）
        for r in reversed(results):
            if r and r.strip():
                return r
        return ""

    def on_turn_start(self, turn_number: int, message: str, **kwargs) -> None:
        """跟踪轮次计数，用于节奏和 injection_frequency 逻辑。"""
        self._turn_count = turn_number

    @staticmethod
    def _chunk_message(content: str, limit: int) -> list[str]:
        """将内容分割成适合 Honcho 消息限制的块。

        尽可能在段落边界分割，回退到句子边界，
        然后是单词边界。每个续传块以 "[continued] " 为前缀，
        以便 Honcho 的表示引擎能重建完整消息。
        """
        if len(content) <= limit:
            return [content]

        prefix = "[continued] "
        prefix_len = len(prefix)
        chunks = []
        remaining = content
        first = True
        while remaining:
            effective = limit if first else limit - prefix_len
            if len(remaining) <= effective:
                chunks.append(remaining if first else prefix + remaining)
                break

            segment = remaining[:effective]

            # 尝试段落分割，然后句子，最后单词
            cut = segment.rfind("\n\n")
            if cut < effective * 0.3:
                cut = segment.rfind(". ")
                if cut >= 0:
                    cut += 2  # include the period and space
            if cut < effective * 0.3:
                cut = segment.rfind(" ")
            if cut < effective * 0.3:
                cut = effective  # hard cut

            chunk = remaining[:cut].rstrip()
            remaining = remaining[cut:].lstrip()
            if not first:
                chunk = prefix + chunk
            chunks.append(chunk)
            first = False

        return chunks

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        """将对话轮次记录到 Honcho（非阻塞）。

        超过 Honcho API 限制（默认 25k 字符）的消息
        会被分割为多条带续传标记的消息。
        """
        if self._cron_skipped:
            return
        if not self._manager or not self._session_key:
            return

        msg_limit = self._config.message_max_chars if self._config else 25000

        def _sync():
            try:
                session = self._manager.get_or_create(self._session_key)
                for chunk in self._chunk_message(user_content, msg_limit):
                    session.add_message("user", chunk)
                for chunk in self._chunk_message(assistant_content, msg_limit):
                    session.add_message("assistant", chunk)
                self._manager._flush_session(session)
            except Exception as e:
                logger.debug("Honcho sync_turn failed: %s", e)

        if self._sync_thread and self._sync_thread.is_alive():
            self._sync_thread.join(timeout=5.0)
        self._sync_thread = threading.Thread(
            target=_sync, daemon=True, name="honcho-sync"
        )
        self._sync_thread.start()

    def on_memory_write(self, action: str, target: str, content: str) -> None:
        """将内置用户画像写入镜像为 Honcho 结论。"""
        if action != "add" or target != "user" or not content:
            return
        if self._cron_skipped:
            return
        if not self._manager or not self._session_key:
            return

        def _write():
            try:
                self._manager.create_conclusion(self._session_key, content)
            except Exception as e:
                logger.debug("Honcho memory mirror failed: %s", e)

        t = threading.Thread(target=_write, daemon=True, name="honcho-memwrite")
        t.start()

    def on_session_end(self, messages: List[Dict[str, Any]]) -> None:
        """在会话结束时将所有待处理消息刷新到 Honcho。"""
        if self._cron_skipped:
            return
        if not self._manager:
            return
        # 等待正在进行的同步
        if self._sync_thread and self._sync_thread.is_alive():
            self._sync_thread.join(timeout=10.0)
        try:
            self._manager.flush_all()
        except Exception as e:
            logger.debug("Honcho session-end flush failed: %s", e)

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        """返回工具 schema，遵循 recall_mode。

        B1: context-only 模式隐藏所有工具。
        """
        if self._cron_skipped:
            return []
        if self._recall_mode == "context":
            return []
        return list(ALL_TOOL_SCHEMAS)

    def handle_tool_call(self, tool_name: str, args: dict, **kwargs) -> str:
        """处理 Honcho 工具调用，支持 tools-only 模式的延迟会话初始化。"""
        if self._cron_skipped:
            return tool_error("Honcho is not active (cron context).")

        # Port #1957: 确保 tools-only 模式下会话已初始化
        if not self._session_initialized:
            if not self._ensure_session():
                return tool_error("Honcho session could not be initialized.")

        if not self._manager or not self._session_key:
            return tool_error("Honcho is not active for this session.")

        try:
            if tool_name == "honcho_profile":
                peer = args.get("peer", "user")
                card_update = args.get("card")
                if card_update:
                    result = self._manager.set_peer_card(self._session_key, card_update, peer=peer)
                    if result is None:
                        return tool_error("Failed to update peer card.")
                    return json.dumps({"result": f"Peer card updated ({len(result)} facts).", "card": result})
                card = self._manager.get_peer_card(self._session_key, peer=peer)
                if not card:
                    return json.dumps({"result": "No profile facts available yet."})
                return json.dumps({"result": card})

            elif tool_name == "honcho_search":
                query = args.get("query", "")
                if not query:
                    return tool_error("Missing required parameter: query")
                max_tokens = min(int(args.get("max_tokens", 800)), 2000)
                peer = args.get("peer", "user")
                result = self._manager.search_context(
                    self._session_key, query, max_tokens=max_tokens, peer=peer
                )
                if not result:
                    return json.dumps({"result": "No relevant context found."})
                return json.dumps({"result": result})

            elif tool_name == "honcho_reasoning":
                query = args.get("query", "")
                if not query:
                    return tool_error("Missing required parameter: query")
                peer = args.get("peer", "user")
                reasoning_level = args.get("reasoning_level")
                result = self._manager.dialectic_query(
                    self._session_key, query,
                    reasoning_level=reasoning_level,
                    peer=peer,
                )
                # 更新节奏跟踪器，使自动注入在显式调用后遵循间隔
                self._last_dialectic_turn = self._turn_count
                return json.dumps({"result": result or "No result from Honcho."})

            elif tool_name == "honcho_context":
                peer = args.get("peer", "user")
                ctx = self._manager.get_session_context(self._session_key, peer=peer)
                if not ctx:
                    return json.dumps({"result": "No context available yet."})
                parts = []
                if ctx.get("summary"):
                    parts.append(f"## Summary\n{ctx['summary']}")
                if ctx.get("representation"):
                    parts.append(f"## Representation\n{ctx['representation']}")
                if ctx.get("card"):
                    parts.append(f"## Card\n{ctx['card']}")
                if ctx.get("recent_messages"):
                    msgs = ctx["recent_messages"]
                    msg_str = "\n".join(
                        f"  [{m['role']}] {m['content'][:200]}"
                        for m in msgs[-5:]  # last 5 for brevity
                    )
                    parts.append(f"## Recent messages\n{msg_str}")
                return json.dumps({"result": "\n\n".join(parts) or "No context available."})

            elif tool_name == "honcho_conclude":
                delete_id = (args.get("delete_id") or "").strip()
                conclusion = args.get("conclusion", "").strip()
                peer = args.get("peer", "user")

                has_delete_id = bool(delete_id)
                has_conclusion = bool(conclusion)
                if has_delete_id == has_conclusion:
                    return tool_error("Exactly one of conclusion or delete_id must be provided.")

                if has_delete_id:
                    ok = self._manager.delete_conclusion(self._session_key, delete_id, peer=peer)
                    if ok:
                        return json.dumps({"result": f"Conclusion {delete_id} deleted."})
                    return tool_error(f"Failed to delete conclusion {delete_id}.")
                ok = self._manager.create_conclusion(self._session_key, conclusion, peer=peer)
                if ok:
                    return json.dumps({"result": f"Conclusion saved for {peer}: {conclusion}"})
                return tool_error("Failed to save conclusion.")

            return tool_error(f"Unknown tool: {tool_name}")

        except Exception as e:
            logger.error("Honcho tool %s failed: %s", tool_name, e)
            return tool_error(f"Honcho {tool_name} failed: {e}")

    def shutdown(self) -> None:
        for t in (self._prefetch_thread, self._sync_thread):
            if t and t.is_alive():
                t.join(timeout=5.0)
        # 刷新所有剩余消息
        if self._manager:
            try:
                self._manager.flush_all()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# 插件入口点
# ---------------------------------------------------------------------------

def register(ctx) -> None:
    """将 Honcho 注册为记忆提供者插件。"""
    ctx.register_memory_provider(HonchoMemoryProvider())
