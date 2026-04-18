"""OpenViking 记忆插件 — 完整双向 MemoryProvider 接口。

由火山引擎（字节跳动）开发的上下文数据库，将智能体知识
组织为文件系统层级结构（viking:// URI），具有分层上下文加载、
自动记忆提取和会话管理功能。

原始 PR #3369 由 Mibayy 提交，重写为使用完整的 OpenViking 会话
生命周期，而非只读搜索端点。

通过环境变量配置（通过每个配置文件的 .env 实现配置文件作用域）：
  OPENVIKING_ENDPOINT  — 服务器 URL（默认：http://127.0.0.1:1933）
  OPENVIKING_API_KEY   — API 密钥（认证服务器需要）
  OPENVIKING_ACCOUNT   — 租户账户（默认：default）
  OPENVIKING_USER      — 租户用户（默认：default）
  OPENVIKING_AGENT   — 租户智能体（默认：hermes）

能力：
  - 会话提交时自动记忆提取（6 个类别）
  - 分层上下文：L0（~100 token）、L1（~2k）、L2（完整）
  - 带层级目录检索的语义搜索
  - 通过 viking:// URI 的文件系统式浏览
  - 资源摄入（URL、文档、代码）
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import threading
from typing import Any, Dict, List, Optional

from agent.memory_provider import MemoryProvider
from tools.registry import tool_error

logger = logging.getLogger(__name__)

_DEFAULT_ENDPOINT = "http://127.0.0.1:1933"
_TIMEOUT = 30.0


# ---------------------------------------------------------------------------
# 进程级 atexit 安全网 —— 确保即使 shutdown_memory_provider 未被调用
# （例如网关崩溃、SIGKILL 或 _async_flush_memories 中的异常阻止关闭），
# 待处理的会话也能被提交。
# ---------------------------------------------------------------------------
_last_active_provider: Optional["OpenVikingMemoryProvider"] = None


def _atexit_commit_sessions():
    """在进程退出时为最后活跃的 provider 触发 on_session_end。"""
    global _last_active_provider
    provider = _last_active_provider
    if provider is None:
        return
    _last_active_provider = None
    try:
        provider.on_session_end([])
    except Exception:
        pass  # 关闭时尽力执行


atexit.register(_atexit_commit_sessions)


# ---------------------------------------------------------------------------
# HTTP 辅助工具 —— 使用 httpx 以避免依赖 openviking SDK
# ---------------------------------------------------------------------------

def _get_httpx():
    """延迟导入 httpx。"""
    try:
        import httpx
        return httpx
    except ImportError:
        return None


class _VikingClient:
    """OpenViking REST API 的轻量 HTTP 客户端。"""

    def __init__(self, endpoint: str, api_key: str = "",
                 account: str = "", user: str = "", agent: str = ""):
        self._endpoint = endpoint.rstrip("/")
        self._api_key = api_key
        self._account = account or os.environ.get("OPENVIKING_ACCOUNT", "default")
        self._user = user or os.environ.get("OPENVIKING_USER", "default")
        self._agent = agent or os.environ.get("OPENVIKING_AGENT", "hermes")
        self._httpx = _get_httpx()
        if self._httpx is None:
            raise ImportError("httpx is required for OpenViking: pip install httpx")

    def _headers(self) -> dict:
        h = {
            "Content-Type": "application/json",
            "X-OpenViking-Account": self._account,
            "X-OpenViking-User": self._user,
            "X-OpenViking-Agent": self._agent,
        }
        if self._api_key:
            h["X-API-Key"] = self._api_key
        return h

    def _url(self, path: str) -> str:
        return f"{self._endpoint}{path}"

    def get(self, path: str, **kwargs) -> dict:
        resp = self._httpx.get(
            self._url(path), headers=self._headers(), timeout=_TIMEOUT, **kwargs
        )
        resp.raise_for_status()
        return resp.json()

    def post(self, path: str, payload: dict = None, **kwargs) -> dict:
        resp = self._httpx.post(
            self._url(path), json=payload or {}, headers=self._headers(),
            timeout=_TIMEOUT, **kwargs
        )
        resp.raise_for_status()
        return resp.json()

    def health(self) -> bool:
        try:
            resp = self._httpx.get(
                self._url("/health"), timeout=3.0
            )
            return resp.status_code == 200
        except Exception:
            return False


# ---------------------------------------------------------------------------
# 工具 schema
# ---------------------------------------------------------------------------

SEARCH_SCHEMA = {
    "name": "viking_search",
    "description": (
        "Semantic search over the OpenViking knowledge base. "
        "Returns ranked results with viking:// URIs for deeper reading. "
        "Use mode='deep' for complex queries that need reasoning across "
        "multiple sources, 'fast' for simple lookups."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query."},
            "mode": {
                "type": "string", "enum": ["auto", "fast", "deep"],
                "description": "Search depth (default: auto).",
            },
            "scope": {
                "type": "string",
                "description": "Viking URI prefix to scope search (e.g. 'viking://resources/docs/').",
            },
            "limit": {"type": "integer", "description": "Max results (default: 10)."},
        },
        "required": ["query"],
    },
}

READ_SCHEMA = {
    "name": "viking_read",
    "description": (
        "Read content at a viking:// URI. Three detail levels:\n"
        "  abstract — ~100 token summary (L0)\n"
        "  overview — ~2k token key points (L1)\n"
        "  full — complete content (L2)\n"
        "Start with abstract/overview, only use full when you need details."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "uri": {"type": "string", "description": "viking:// URI to read."},
            "level": {
                "type": "string", "enum": ["abstract", "overview", "full"],
                "description": "Detail level (default: overview).",
            },
        },
        "required": ["uri"],
    },
}

BROWSE_SCHEMA = {
    "name": "viking_browse",
    "description": (
        "Browse the OpenViking knowledge store like a filesystem.\n"
        "  list — show directory contents\n"
        "  tree — show hierarchy\n"
        "  stat — show metadata for a URI"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string", "enum": ["tree", "list", "stat"],
                "description": "Browse action.",
            },
            "path": {
                "type": "string",
                "description": "Viking URI path (default: viking://). Examples: 'viking://resources/', 'viking://user/memories/'.",
            },
        },
        "required": ["action"],
    },
}

REMEMBER_SCHEMA = {
    "name": "viking_remember",
    "description": (
        "Explicitly store a fact or memory in the OpenViking knowledge base. "
        "Use for important information the agent should remember long-term. "
        "The system automatically categorizes and indexes the memory."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "The information to remember."},
            "category": {
                "type": "string",
                "enum": ["preference", "entity", "event", "case", "pattern"],
                "description": "Memory category (default: auto-detected).",
            },
        },
        "required": ["content"],
    },
}

ADD_RESOURCE_SCHEMA = {
    "name": "viking_add_resource",
    "description": (
        "Add a URL or document to the OpenViking knowledge base. "
        "Supports web pages, GitHub repos, PDFs, markdown, code files. "
        "The system automatically parses, indexes, and generates summaries."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "URL or path of the resource to add."},
            "reason": {
                "type": "string",
                "description": "Why this resource is relevant (improves search).",
            },
        },
        "required": ["url"],
    },
}


# ---------------------------------------------------------------------------
# MemoryProvider 实现
# ---------------------------------------------------------------------------

class OpenVikingMemoryProvider(MemoryProvider):
    """通过 OpenViking 上下文数据库实现的完整双向记忆。"""

    def __init__(self):
        self._client: Optional[_VikingClient] = None
        self._endpoint = ""
        self._api_key = ""
        self._session_id = ""
        self._turn_count = 0
        self._sync_thread: Optional[threading.Thread] = None
        self._prefetch_result = ""
        self._prefetch_lock = threading.Lock()
        self._prefetch_thread: Optional[threading.Thread] = None

    @property
    def name(self) -> str:
        return "openviking"

    def is_available(self) -> bool:
        """检查 OpenViking 端点是否已配置。不发起网络调用。"""
        return bool(os.environ.get("OPENVIKING_ENDPOINT"))

    def get_config_schema(self):
        return [
            {
                "key": "endpoint",
                "description": "OpenViking server URL",
                "required": True,
                "default": _DEFAULT_ENDPOINT,
                "env_var": "OPENVIKING_ENDPOINT",
            },
            {
                "key": "api_key",
                "description": "OpenViking API key (leave blank for local dev mode)",
                "secret": True,
                "env_var": "OPENVIKING_API_KEY",
            },
            {
                "key": "account",
                "description": "OpenViking tenant account ID ([default], used when local mode, OPENVIKING_API_KEY is empty)",
                "default": "default",
                "env_var": "OPENVIKING_ACCOUNT",
            },
            {
                "key": "user",
                "description": "OpenViking user ID within the account ([default], used when local mode, OPENVIKING_API_KEY is empty)",
                "default": "default",
                "env_var": "OPENVIKING_USER",
            },
            {
                "key": "agent",
                "description": "OpenViking agent ID within the account ([hermes], useful in multi-agent mode)",
                "default": "hermes",
                "env_var": "OPENVIKING_AGENT",
            },
        ]

    def initialize(self, session_id: str, **kwargs) -> None:
        self._endpoint = os.environ.get("OPENVIKING_ENDPOINT", _DEFAULT_ENDPOINT)
        self._api_key = os.environ.get("OPENVIKING_API_KEY", "")
        self._account = os.environ.get("OPENVIKING_ACCOUNT", "default")
        self._user = os.environ.get("OPENVIKING_USER", "default")
        self._agent = os.environ.get("OPENVIKING_AGENT", "hermes")
        self._session_id = session_id
        self._turn_count = 0

        try:
            self._client = _VikingClient(
                self._endpoint, self._api_key,
                account=self._account, user=self._user, agent=self._agent,
            )
            if not self._client.health():
                logger.warning("OpenViking server at %s is not reachable", self._endpoint)
                self._client = None
        except ImportError:
            logger.warning("httpx not installed — OpenViking plugin disabled")
            self._client = None

        # 注册为最后活跃的 provider，用于 atexit 安全网
        global _last_active_provider
        _last_active_provider = self

    def system_prompt_block(self) -> str:
        if not self._client:
            return ""
        # 提供关于知识库的简要信息
        try:
            # 通过根目录列表检查知识库中的内容
            resp = self._client.get("/api/v1/fs/ls", params={"uri": "viking://"})
            result = resp.get("result", [])
            children = len(result) if isinstance(result, list) else 0
            if children == 0:
                return ""
            return (
                "# OpenViking Knowledge Base\n"
                f"Active. Endpoint: {self._endpoint}\n"
                "Use viking_search to find information, viking_read for details "
                "(abstract/overview/full), viking_browse to explore.\n"
                "Use viking_remember to store facts, viking_add_resource to index URLs/docs."
            )
        except Exception as e:
            logger.warning("OpenViking system_prompt_block failed: %s", e)
            return (
                "# OpenViking Knowledge Base\n"
                f"Active. Endpoint: {self._endpoint}\n"
                "Use viking_search, viking_read, viking_browse, "
                "viking_remember, viking_add_resource."
            )

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        """返回后台线程的预取结果。"""
        if self._prefetch_thread and self._prefetch_thread.is_alive():
            self._prefetch_thread.join(timeout=3.0)
        with self._prefetch_lock:
            result = self._prefetch_result
            self._prefetch_result = ""
        if not result:
            return ""
        return f"## OpenViking Context\n{result}"

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        """触发后台搜索以预加载相关上下文。"""
        if not self._client or not query:
            return

        def _run():
            try:
                client = _VikingClient(
                    self._endpoint, self._api_key,
                    account=self._account, user=self._user, agent=self._agent,
                )
                resp = client.post("/api/v1/search/find", {
                    "query": query,
                    "top_k": 5,
                })
                result = resp.get("result", {})
                parts = []
                for ctx_type in ("memories", "resources"):
                    items = result.get(ctx_type, [])
                    for item in items[:3]:
                        uri = item.get("uri", "")
                        abstract = item.get("abstract", "")
                        score = item.get("score", 0)
                        if abstract:
                            parts.append(f"- [{score:.2f}] {abstract} ({uri})")
                if parts:
                    with self._prefetch_lock:
                        self._prefetch_result = "\n".join(parts)
            except Exception as e:
                logger.debug("OpenViking prefetch failed: %s", e)

        self._prefetch_thread = threading.Thread(
            target=_run, daemon=True, name="openviking-prefetch"
        )
        self._prefetch_thread.start()

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        """将对话轮次记录到 OpenViking 的会话中（非阻塞）。"""
        if not self._client:
            return

        self._turn_count += 1

        def _sync():
            try:
                client = _VikingClient(
                    self._endpoint, self._api_key,
                    account=self._account, user=self._user, agent=self._agent,
                )
                sid = self._session_id

                # 添加用户消息
                client.post(f"/api/v1/sessions/{sid}/messages", {
                    "role": "user",
                    "content": user_content[:4000],  # 截断过长的消息
                })
                # 添加助手消息
                client.post(f"/api/v1/sessions/{sid}/messages", {
                    "role": "assistant",
                    "content": assistant_content[:4000],
                })
            except Exception as e:
                logger.debug("OpenViking sync_turn failed: %s", e)

        # 在启动新同步之前等待上一次同步完成
        if self._sync_thread and self._sync_thread.is_alive():
            self._sync_thread.join(timeout=5.0)

        self._sync_thread = threading.Thread(
            target=_sync, daemon=True, name="openviking-sync"
        )
        self._sync_thread.start()

    def on_session_end(self, messages: List[Dict[str, Any]]) -> None:
        """提交会话以触发记忆提取。

        OpenViking 自动提取 6 类记忆：
        画像、偏好、实体、事件、案例和模式。
        """
        if not self._client:
            return

        # 先等待待处理的同步完成 —— 在 turn_count 检查之前执行，
        # 这样即使计数尚未递增，最后一轮的消息也能被刷新。
        if self._sync_thread and self._sync_thread.is_alive():
            self._sync_thread.join(timeout=10.0)

        if self._turn_count == 0:
            return

        try:
            self._client.post(f"/api/v1/sessions/{self._session_id}/commit")
            logger.info("OpenViking session %s committed (%d turns)", self._session_id, self._turn_count)
        except Exception as e:
            logger.warning("OpenViking session commit failed: %s", e)

    def on_memory_write(self, action: str, target: str, content: str) -> None:
        """将内置记忆写入镜像到 OpenViking 作为显式记忆。"""
        if not self._client or action != "add" or not content:
            return

        def _write():
            try:
                client = _VikingClient(
                    self._endpoint, self._api_key,
                    account=self._account, user=self._user, agent=self._agent,
                )
                # 作为带记忆上下文的用户消息添加，使提交时的
                # 提取过程能将其识别为显式记忆
                client.post(f"/api/v1/sessions/{self._session_id}/messages", {
                    "role": "user",
                    "parts": [
                        {"type": "text", "text": f"[Memory note — {target}] {content}"},
                    ],
                })
            except Exception as e:
                logger.debug("OpenViking memory mirror failed: %s", e)

        t = threading.Thread(target=_write, daemon=True, name="openviking-memwrite")
        t.start()

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return [SEARCH_SCHEMA, READ_SCHEMA, BROWSE_SCHEMA, REMEMBER_SCHEMA, ADD_RESOURCE_SCHEMA]

    def handle_tool_call(self, tool_name: str, args: dict, **kwargs) -> str:
        if not self._client:
            return tool_error("OpenViking server not connected")

        try:
            if tool_name == "viking_search":
                return self._tool_search(args)
            elif tool_name == "viking_read":
                return self._tool_read(args)
            elif tool_name == "viking_browse":
                return self._tool_browse(args)
            elif tool_name == "viking_remember":
                return self._tool_remember(args)
            elif tool_name == "viking_add_resource":
                return self._tool_add_resource(args)
            return tool_error(f"Unknown tool: {tool_name}")
        except Exception as e:
            return tool_error(str(e))

    def shutdown(self) -> None:
        # 等待后台线程完成
        for t in (self._sync_thread, self._prefetch_thread):
            if t and t.is_alive():
                t.join(timeout=5.0)
        # 清除 atexit 引用以避免重复提交
        global _last_active_provider
        if _last_active_provider is self:
            _last_active_provider = None

    # -- 工具实现 ------------------------------------------------

    def _tool_search(self, args: dict) -> str:
        query = args.get("query", "")
        if not query:
            return tool_error("query is required")

        payload: Dict[str, Any] = {"query": query}
        mode = args.get("mode", "auto")
        if mode != "auto":
            payload["mode"] = mode
        if args.get("scope"):
            payload["target_uri"] = args["scope"]
        if args.get("limit"):
            payload["top_k"] = args["limit"]

        resp = self._client.post("/api/v1/search/find", payload)
        result = resp.get("result", {})

        # 为模型格式化结果 —— 保持简洁
        scored_entries = []
        for ctx_type in ("memories", "resources", "skills"):
            items = result.get(ctx_type, [])
            for item in items:
                raw_score = item.get("score")
                sort_score = raw_score if raw_score is not None else 0.0
                entry = {
                    "uri": item.get("uri", ""),
                    "type": ctx_type.rstrip("s"),
                    "score": round(raw_score, 3) if raw_score is not None else 0.0,
                    "abstract": item.get("abstract", ""),
                }
                if item.get("relations"):
                    entry["related"] = [r.get("uri") for r in item["relations"][:3]]
                scored_entries.append((sort_score, entry))

        scored_entries.sort(key=lambda x: x[0], reverse=True)
        formatted = [entry for _, entry in scored_entries]

        return json.dumps({
            "results": formatted,
            "total": result.get("total", len(formatted)),
        }, ensure_ascii=False)

    def _tool_read(self, args: dict) -> str:
        uri = args.get("uri", "")
        if not uri:
            return tool_error("uri is required")

        level = args.get("level", "overview")
        # 将我们的级别名称映射到 OpenViking GET 端点
        if level == "abstract":
            resp = self._client.get("/api/v1/content/abstract", params={"uri": uri})
        elif level == "full":
            resp = self._client.get("/api/v1/content/read", params={"uri": uri})
        else:  # 概览
            resp = self._client.get("/api/v1/content/overview", params={"uri": uri})

        result = resp.get("result", "")
        # result 是内容端点返回的纯字符串
        content = result if isinstance(result, str) else result.get("content", "")

        # 截断过长的内容以避免淹没上下文
        if len(content) > 8000:
            content = content[:8000] + "\n\n[... truncated, use a more specific URI or abstract level]"

        return json.dumps({
            "uri": uri,
            "level": level,
            "content": content,
        }, ensure_ascii=False)

    def _tool_browse(self, args: dict) -> str:
        action = args.get("action", "list")
        path = args.get("path", "viking://")

        # 将操作映射到正确的 fs 端点（全部使用 GET 和 uri= 参数）
        endpoint_map = {"tree": "/api/v1/fs/tree", "list": "/api/v1/fs/ls", "stat": "/api/v1/fs/stat"}
        endpoint = endpoint_map.get(action, "/api/v1/fs/ls")
        resp = self._client.get(endpoint, params={"uri": path})
        result = resp.get("result", {})

        # 格式化 list/tree 结果以提高可读性
        if action in ("list", "tree") and isinstance(result, list):
            entries = []
            for e in result[:50]:  # 限制最多 50 条
                entries.append({
                    "name": e.get("rel_path", e.get("name", "")),
                    "uri": e.get("uri", ""),
                    "type": "dir" if e.get("isDir") else "file",
                    "abstract": e.get("abstract", ""),
                })
            return json.dumps({"path": path, "entries": entries}, ensure_ascii=False)

        return json.dumps(result, ensure_ascii=False)

    def _tool_remember(self, args: dict) -> str:
        content = args.get("content", "")
        if not content:
            return tool_error("content is required")

        # 作为会话消息存储，在提交时被提取。
        # 类别提示帮助 OpenViking 的提取过程正确分类。
        category = args.get("category", "")
        text = f"[Remember] {content}"
        if category:
            text = f"[Remember — {category}] {content}"

        self._client.post(f"/api/v1/sessions/{self._session_id}/messages", {
            "role": "user",
            "parts": [
                {"type": "text", "text": text},
            ],
        })

        return json.dumps({
            "status": "stored",
            "message": "Memory recorded. Will be extracted and indexed on session commit.",
        })

    def _tool_add_resource(self, args: dict) -> str:
        url = args.get("url", "")
        if not url:
            return tool_error("url is required")

        payload: Dict[str, Any] = {"path": url}
        if args.get("reason"):
            payload["reason"] = args["reason"]

        resp = self._client.post("/api/v1/resources", payload)
        result = resp.get("result", {})

        return json.dumps({
            "status": "added",
            "root_uri": result.get("root_uri", ""),
            "message": "Resource queued for processing. Use viking_search after a moment to find it.",
        }, ensure_ascii=False)


# ---------------------------------------------------------------------------
# 插件入口点
# ---------------------------------------------------------------------------

def register(ctx) -> None:
    """将 OpenViking 注册为记忆 provider 插件。"""
    ctx.register_memory_provider(OpenVikingMemoryProvider())
