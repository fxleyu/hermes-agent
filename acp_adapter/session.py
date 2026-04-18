"""ACP 会话管理器 -- 将 ACP 会话映射到 Hermes AIAgent 实例。

会话持久化到共享的 SessionDB（``~/.hermes/state.db``），使其
在进程重启后存活，并可通过 ``session_search`` 搜索。当编辑器
在空闲/重启后重新连接时，``load_session`` / ``resume_session`` 调用
会在数据库中找到持久化的会话并恢复完整的对话历史。
"""
from __future__ import annotations

from hermes_constants import get_hermes_home

import copy
import json
import logging
import sys
import uuid
from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _acp_stderr_print(*args, **kwargs) -> None:
    """ACP stdio 会话的尽力而为的人类可读输出接收器。

    ACP 保留 stdout 用于 JSON-RPC 帧，因此 AIAgent 的任何附带
    CLI/状态输出都必须从 stdout 重定向。将其路由到 stderr。
    """
    kwargs = dict(kwargs)
    kwargs.setdefault("file", sys.stderr)
    print(*args, **kwargs)


def _register_task_cwd(task_id: str, cwd: str) -> None:
    """将任务/会话 ID 绑定到编辑器的工作目录，供工具使用。"""
    if not task_id:
        return
    try:
        from tools.terminal_tool import register_task_env_overrides
        register_task_env_overrides(task_id, {"cwd": cwd})
    except Exception:
        logger.debug("Failed to register ACP task cwd override", exc_info=True)


def _clear_task_cwd(task_id: str) -> None:
    """移除 ACP 会话的任务特定 cwd 覆盖。"""
    if not task_id:
        return
    try:
        from tools.terminal_tool import clear_task_env_overrides
        clear_task_env_overrides(task_id)
    except Exception:
        logger.debug("Failed to clear ACP task cwd override", exc_info=True)


@dataclass
class SessionState:
    """跟踪 ACP 管理的 Hermes 代理的每会话状态。"""

    session_id: str
    agent: Any  # AIAgent instance
    cwd: str = "."
    model: str = ""
    history: List[Dict[str, Any]] = field(default_factory=list)
    cancel_event: Any = None  # threading.Event


class SessionManager:
    """由 Hermes AIAgent 实例支持的线程安全 ACP 会话管理器。

    会话在内存中保持以便快速访问，**同时**持久化到
    共享的 SessionDB，使其在进程重启后存活并可通过
    ``session_search`` 搜索。
    """

    def __init__(self, agent_factory=None, db=None):
        """
        Args:
            agent_factory: 可选的可调用对象，用于创建类似 AIAgent 的对象。
                           用于测试。省略时使用当前 Hermes 运行时提供商配置
                           创建真实的 AIAgent。
            db:            可选的 SessionDB 实例。省略时延迟创建默认的
                           SessionDB（``~/.hermes/state.db``）。
        """
        self._sessions: Dict[str, SessionState] = {}
        self._lock = Lock()
        self._agent_factory = agent_factory
        self._db_instance = db  # None → lazy-init on first use

    # ---- 公共 API ---------------------------------------------------------

    def create_session(self, cwd: str = ".") -> SessionState:
        """使用唯一 ID 和全新的 AIAgent 创建新会话。"""
        import threading

        session_id = str(uuid.uuid4())
        agent = self._make_agent(session_id=session_id, cwd=cwd)
        state = SessionState(
            session_id=session_id,
            agent=agent,
            cwd=cwd,
            model=getattr(agent, "model", "") or "",
            cancel_event=threading.Event(),
        )
        with self._lock:
            self._sessions[session_id] = state
        _register_task_cwd(session_id, cwd)
        self._persist(state)
        logger.info("Created ACP session %s (cwd=%s)", session_id, cwd)
        return state

    def get_session(self, session_id: str) -> Optional[SessionState]:
        """返回 *session_id* 对应的会话，如果不存在则返回 ``None``。

        如果会话不在内存中但存在于数据库中（例如进程重启后），
        将透明地恢复该会话。
        """
        with self._lock:
            state = self._sessions.get(session_id)
        if state is not None:
            return state
        # 尝试从数据库恢复。
        return self._restore(session_id)

    def remove_session(self, session_id: str) -> bool:
        """从内存和数据库中移除会话。如果存在则返回 True。"""
        with self._lock:
            existed = self._sessions.pop(session_id, None) is not None
        db_existed = self._delete_persisted(session_id)
        if existed or db_existed:
            _clear_task_cwd(session_id)
        return existed or db_existed

    def fork_session(self, session_id: str, cwd: str = ".") -> Optional[SessionState]:
        """将会话的历史深拷贝到新会话中。"""
        import threading

        original = self.get_session(session_id)  # checks DB too
        if original is None:
            return None

        new_id = str(uuid.uuid4())
        agent = self._make_agent(
            session_id=new_id,
            cwd=cwd,
            model=original.model or None,
        )
        state = SessionState(
            session_id=new_id,
            agent=agent,
            cwd=cwd,
            model=getattr(agent, "model", original.model) or original.model,
            history=copy.deepcopy(original.history),
            cancel_event=threading.Event(),
        )
        with self._lock:
            self._sessions[new_id] = state
        _register_task_cwd(new_id, cwd)
        self._persist(state)
        logger.info("Forked ACP session %s -> %s", session_id, new_id)
        return state

    def list_sessions(self) -> List[Dict[str, Any]]:
        """返回所有会话（内存 + 数据库）的轻量级信息字典。"""
        # 首先收集内存中的会话。
        with self._lock:
            seen_ids = set(self._sessions.keys())
            results = [
                {
                    "session_id": s.session_id,
                    "cwd": s.cwd,
                    "model": s.model,
                    "history_len": len(s.history),
                }
                for s in self._sessions.values()
            ]

        # 合并当前不在内存中的持久化会话。
        db = self._get_db()
        if db is not None:
            try:
                rows = db.search_sessions(source="acp", limit=1000)
                for row in rows:
                    sid = row["id"]
                    if sid in seen_ids:
                        continue
                    # 从 model_config JSON 中提取 cwd。
                    cwd = "."
                    mc = row.get("model_config")
                    if mc:
                        try:
                            cwd = json.loads(mc).get("cwd", ".")
                        except (json.JSONDecodeError, TypeError):
                            pass
                    results.append({
                        "session_id": sid,
                        "cwd": cwd,
                        "model": row.get("model") or "",
                        "history_len": row.get("message_count") or 0,
                    })
            except Exception:
                logger.debug("Failed to list ACP sessions from DB", exc_info=True)

        return results

    def update_cwd(self, session_id: str, cwd: str) -> Optional[SessionState]:
        """更新会话的工作目录及其工具覆盖。"""
        state = self.get_session(session_id)  # checks DB too
        if state is None:
            return None
        state.cwd = cwd
        _register_task_cwd(session_id, cwd)
        self._persist(state)
        return state

    def cleanup(self) -> None:
        """移除所有会话（内存和数据库），并清除任务特定的 cwd 覆盖。"""
        with self._lock:
            session_ids = list(self._sessions.keys())
            self._sessions.clear()
        for session_id in session_ids:
            _clear_task_cwd(session_id)
            self._delete_persisted(session_id)
        # 同时移除数据库中当前不在内存中的 ACP 会话。
        db = self._get_db()
        if db is not None:
            try:
                rows = db.search_sessions(source="acp", limit=10000)
                for row in rows:
                    sid = row["id"]
                    _clear_task_cwd(sid)
                    db.delete_session(sid)
            except Exception:
                logger.debug("Failed to cleanup ACP sessions from DB", exc_info=True)

    def save_session(self, session_id: str) -> None:
        """将会话的当前状态持久化到数据库。

        在提示完成、修改历史的斜杠命令和模型切换后
        由服务器调用。
        """
        with self._lock:
            state = self._sessions.get(session_id)
        if state is not None:
            self._persist(state)

    # ---- 通过 SessionDB 持久化 ------------------------------------------

    def _get_db(self):
        """延迟初始化并返回 SessionDB 实例。

        如果数据库不可用（例如在最小化测试环境中导入错误），
        则返回 ``None``。

        注意：我们动态解析 ``HERMES_HOME``，而不是依赖模块级别的
        ``DEFAULT_DB_PATH`` 常量，因为该常量在导入时求值，
        不会反映之后进行的环境变量更改
        （例如测试夹具 ``_isolate_hermes_home``）。
        """
        if self._db_instance is not None:
            return self._db_instance
        try:
            from hermes_state import SessionDB
            hermes_home = get_hermes_home()
            self._db_instance = SessionDB(db_path=hermes_home / "state.db")
            return self._db_instance
        except Exception:
            logger.debug("SessionDB unavailable for ACP persistence", exc_info=True)
            return None

    def _persist(self, state: SessionState) -> None:
        """将会话状态写入数据库。

        如果会话记录不存在则创建，然后用当前内存中的
        历史替换所有存储的消息。
        """
        db = self._get_db()
        if db is None:
            return

        # 确保 model 是一个普通字符串（而非 MagicMock 或其他代理对象）。
        model_str = str(state.model) if state.model else None
        session_meta = {"cwd": state.cwd}
        provider = getattr(state.agent, "provider", None)
        base_url = getattr(state.agent, "base_url", None)
        api_mode = getattr(state.agent, "api_mode", None)
        if isinstance(provider, str) and provider.strip():
            session_meta["provider"] = provider.strip()
        if isinstance(base_url, str) and base_url.strip():
            session_meta["base_url"] = base_url.strip()
        if isinstance(api_mode, str) and api_mode.strip():
            session_meta["api_mode"] = api_mode.strip()
        cwd_json = json.dumps(session_meta)

        try:
            # 确保会话记录存在。
            existing = db.get_session(state.session_id)
            if existing is None:
                db.create_session(
                    session_id=state.session_id,
                    source="acp",
                    model=model_str,
                    model_config={"cwd": state.cwd},
                )
            else:
                # 如果 model_config（包含 cwd）发生了变化则更新。
                try:
                    with db._lock:
                        db._conn.execute(
                            "UPDATE sessions SET model_config = ?, model = COALESCE(?, model) WHERE id = ?",
                            (cwd_json, model_str, state.session_id),
                        )
                        db._conn.commit()
                except Exception:
                    logger.debug("Failed to update ACP session metadata", exc_info=True)

            # 用当前历史替换存储的消息。
            db.clear_messages(state.session_id)
            for msg in state.history:
                db.append_message(
                    session_id=state.session_id,
                    role=msg.get("role", "user"),
                    content=msg.get("content"),
                    tool_name=msg.get("tool_name") or msg.get("name"),
                    tool_calls=msg.get("tool_calls"),
                    tool_call_id=msg.get("tool_call_id"),
                )
        except Exception:
            logger.warning("Failed to persist ACP session %s", state.session_id, exc_info=True)

    def _restore(self, session_id: str) -> Optional[SessionState]:
        """从数据库将会话加载到内存中，重新创建 AIAgent。"""
        import threading

        db = self._get_db()
        if db is None:
            return None

        try:
            row = db.get_session(session_id)
        except Exception:
            logger.debug("Failed to query DB for ACP session %s", session_id, exc_info=True)
            return None

        if row is None:
            return None

        # 仅恢复 ACP 会话。
        if row.get("source") != "acp":
            return None

        # 从 model_config 中提取 cwd。
        cwd = "."
        requested_provider = row.get("billing_provider")
        restored_base_url = row.get("billing_base_url")
        restored_api_mode = None
        mc = row.get("model_config")
        if mc:
            try:
                meta = json.loads(mc)
                if isinstance(meta, dict):
                    cwd = meta.get("cwd", ".")
                    requested_provider = meta.get("provider") or requested_provider
                    restored_base_url = meta.get("base_url") or restored_base_url
                    restored_api_mode = meta.get("api_mode") or restored_api_mode
            except (json.JSONDecodeError, TypeError):
                pass

        model = row.get("model") or None

        # 加载对话历史。
        try:
            history = db.get_messages_as_conversation(session_id)
        except Exception:
            logger.warning("Failed to load messages for ACP session %s", session_id, exc_info=True)
            history = []

        try:
            agent = self._make_agent(
                session_id=session_id,
                cwd=cwd,
                model=model,
                requested_provider=requested_provider,
                base_url=restored_base_url,
                api_mode=restored_api_mode,
            )
        except Exception:
            logger.warning("Failed to recreate agent for ACP session %s", session_id, exc_info=True)
            return None

        state = SessionState(
            session_id=session_id,
            agent=agent,
            cwd=cwd,
            model=model or getattr(agent, "model", "") or "",
            history=history,
            cancel_event=threading.Event(),
        )
        with self._lock:
            self._sessions[session_id] = state
        _register_task_cwd(session_id, cwd)
        logger.info("Restored ACP session %s from DB (%d messages)", session_id, len(history))
        return state

    def _delete_persisted(self, session_id: str) -> bool:
        """从数据库中删除会话。如果存在则返回 True。"""
        db = self._get_db()
        if db is None:
            return False
        try:
            return db.delete_session(session_id)
        except Exception:
            logger.debug("Failed to delete ACP session %s from DB", session_id, exc_info=True)
            return False

    # ---- 内部方法 -----------------------------------------------------------

    def _make_agent(
        self,
        *,
        session_id: str,
        cwd: str,
        model: str | None = None,
        requested_provider: str | None = None,
        base_url: str | None = None,
        api_mode: str | None = None,
    ):
        if self._agent_factory is not None:
            return self._agent_factory()

        from run_agent import AIAgent
        from hermes_cli.config import load_config
        from hermes_cli.runtime_provider import resolve_runtime_provider

        config = load_config()
        model_cfg = config.get("model")
        default_model = ""
        config_provider = None
        if isinstance(model_cfg, dict):
            default_model = str(model_cfg.get("default") or default_model)
            config_provider = model_cfg.get("provider")
        elif isinstance(model_cfg, str) and model_cfg.strip():
            default_model = model_cfg.strip()

        kwargs = {
            "platform": "acp",
            "enabled_toolsets": ["hermes-acp"],
            "quiet_mode": True,
            "session_id": session_id,
            "model": model or default_model,
        }

        try:
            runtime = resolve_runtime_provider(requested=requested_provider or config_provider)
            kwargs.update(
                {
                    "provider": runtime.get("provider"),
                    "api_mode": api_mode or runtime.get("api_mode"),
                    "base_url": base_url or runtime.get("base_url"),
                    "api_key": runtime.get("api_key"),
                    "command": runtime.get("command"),
                    "args": list(runtime.get("args") or []),
                }
            )
        except Exception:
            logger.debug("ACP session falling back to default provider resolution", exc_info=True)

        _register_task_cwd(session_id, cwd)
        agent = AIAgent(**kwargs)
        # ACP stdio 传输要求 stdout 保持仅用于协议 JSON-RPC。
        # 将 AIAgent 的任何附带人类可读输出路由到 stderr。
        agent._print_fn = _acp_stderr_print
        return agent
