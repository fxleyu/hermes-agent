#!/usr/bin/env python3
"""
会话搜索工具 - 长期对话回忆

通过 FTS5 在 SQLite 中搜索过往会话记录，然后使用低成本/高速模型
（与 web_extract 相同的模式）对匹配的顶部会话进行摘要。
返回的是过往对话的聚焦摘要而非原始记录，保持主模型的上下文窗口整洁。

流程：
  1. FTS5 搜索按相关性排名查找匹配消息
  2. 按会话分组，取前 N 个唯一会话（默认 3 个）
  3. 加载每个会话的对话，截断至匹配位置附近约 10 万字符
  4. 发送给 Gemini Flash 进行聚焦摘要
  5. 返回带有元数据的逐会话摘要
"""

import asyncio
import concurrent.futures
import json
import logging
import re
from typing import Dict, Any, List, Optional, Union

from agent.auxiliary_client import async_call_llm, extract_content_or_reasoning
MAX_SESSION_CHARS = 100_000
MAX_SUMMARY_TOKENS = 10000


def _format_timestamp(ts: Union[int, float, str, None]) -> str:
    """将 Unix 时间戳（float/int）或 ISO 字符串转换为人类可读的日期。

    对于 None 返回 "unknown"，转换失败时返回 str(ts)。
    """
    if ts is None:
        return "unknown"
    try:
        if isinstance(ts, (int, float)):
            from datetime import datetime
            dt = datetime.fromtimestamp(ts)
            return dt.strftime("%B %d, %Y at %I:%M %p")
        if isinstance(ts, str):
            if ts.replace(".", "").replace("-", "").isdigit():
                from datetime import datetime
                dt = datetime.fromtimestamp(float(ts))
                return dt.strftime("%B %d, %Y at %I:%M %p")
            return ts
    except (ValueError, OSError, OverflowError) as e:
        # 记录特定错误用于调试，同时优雅处理边界情况
        logging.debug("Failed to format timestamp %s: %s", ts, e, exc_info=True)
    except Exception as e:
        logging.debug("Unexpected error formatting timestamp %s: %s", ts, e, exc_info=True)
    return str(ts)


def _format_conversation(messages: List[Dict[str, Any]]) -> str:
    """将会话消息格式化为可读的记录文本，用于摘要生成。"""
    parts = []
    for msg in messages:
        role = msg.get("role", "unknown").upper()
        content = msg.get("content") or ""
        tool_name = msg.get("tool_name")

        if role == "TOOL" and tool_name:
            # 截断过长的工具输出
            if len(content) > 500:
                content = content[:250] + "\n...[truncated]...\n" + content[-250:]
            parts.append(f"[TOOL:{tool_name}]: {content}")
        elif role == "ASSISTANT":
            # 如果存在工具调用则包含工具调用名称
            tool_calls = msg.get("tool_calls")
            if tool_calls and isinstance(tool_calls, list):
                tc_names = []
                for tc in tool_calls:
                    if isinstance(tc, dict):
                        name = tc.get("name") or tc.get("function", {}).get("name", "?")
                        tc_names.append(name)
                if tc_names:
                    parts.append(f"[ASSISTANT]: [Called: {', '.join(tc_names)}]")
                if content:
                    parts.append(f"[ASSISTANT]: {content}")
            else:
                parts.append(f"[ASSISTANT]: {content}")
        else:
            parts.append(f"[{role}]: {content}")

    return "\n\n".join(parts)


def _truncate_around_matches(
    full_text: str, query: str, max_chars: int = MAX_SESSION_CHARS
) -> str:
    """
    将对话记录截断至 *max_chars*，选择一个最大化覆盖 *query*
    实际出现位置的窗口。

    策略（按优先级）：
    1. 尝试以完整短语（不区分大小写）查找。
    2. 如果没有短语命中，查找所有查询词在 200 字符近邻窗口内
       共同出现的位置（共现）。
    3. 回退到单个词的位置。

    收集候选位置后，函数选择覆盖最多匹配位置的窗口起始点。
    """
    if len(full_text) <= max_chars:
        return full_text

    text_lower = full_text.lower()
    query_lower = query.lower().strip()
    match_positions: list[int] = []

    # --- 1. 完整短语搜索 ------------------------------------------------
    phrase_pat = re.compile(re.escape(query_lower))
    match_positions = [m.start() for m in phrase_pat.finditer(text_lower)]

    # --- 2. 所有词的近邻共现（200 字符内）-----------
    if not match_positions:
        terms = query_lower.split()
        if len(terms) > 1:
            # 收集每个词的所有出现位置
            term_positions: dict[str, list[int]] = {}
            for t in terms:
                term_positions[t] = [
                    m.start() for m in re.finditer(re.escape(t), text_lower)
                ]
            # 遍历最稀有词的位置并检查近邻性
            rarest = min(terms, key=lambda t: len(term_positions.get(t, [])))
            for pos in term_positions.get(rarest, []):
                if all(
                    any(abs(p - pos) < 200 for p in term_positions.get(t, []))
                    for t in terms
                    if t != rarest
                ):
                    match_positions.append(pos)

    # --- 3. 单个词位置（最后手段）---------------------------
    if not match_positions:
        terms = query_lower.split()
        for t in terms:
            for m in re.finditer(re.escape(t), text_lower):
                match_positions.append(m.start())

    if not match_positions:
        # 完全没找到 — 从开头取
        truncated = full_text[:max_chars]
        suffix = "\n\n...[later conversation truncated]..." if max_chars < len(full_text) else ""
        return truncated + suffix

    # --- 选择覆盖最多匹配位置的窗口 ---------------------
    match_positions.sort()

    best_start = 0
    best_count = 0
    for candidate in match_positions:
        ws = max(0, candidate - max_chars // 4)  # bias: 25% before, 75% after
        we = ws + max_chars
        if we > len(full_text):
            ws = max(0, len(full_text) - max_chars)
            we = len(full_text)
        count = sum(1 for p in match_positions if ws <= p < we)
        if count > best_count:
            best_count = count
            best_start = ws

    start = best_start
    end = min(len(full_text), start + max_chars)

    truncated = full_text[start:end]
    prefix = "...[earlier conversation truncated]...\n\n" if start > 0 else ""
    suffix = "\n\n...[later conversation truncated]..." if end < len(full_text) else ""
    return prefix + truncated + suffix


async def _summarize_session(
    conversation_text: str, query: str, session_meta: Dict[str, Any]
) -> Optional[str]:
    """基于搜索查询对单个会话对话进行摘要。"""
    system_prompt = (
        "You are reviewing a past conversation transcript to help recall what happened. "
        "Summarize the conversation with a focus on the search topic. Include:\n"
        "1. What the user asked about or wanted to accomplish\n"
        "2. What actions were taken and what the outcomes were\n"
        "3. Key decisions, solutions found, or conclusions reached\n"
        "4. Any specific commands, files, URLs, or technical details that were important\n"
        "5. Anything left unresolved or notable\n\n"
        "Be thorough but concise. Preserve specific details (commands, paths, error messages) "
        "that would be useful to recall. Write in past tense as a factual recap."
    )

    source = session_meta.get("source", "unknown")
    started = _format_timestamp(session_meta.get("started_at"))

    user_prompt = (
        f"Search topic: {query}\n"
        f"Session source: {source}\n"
        f"Session date: {started}\n\n"
        f"CONVERSATION TRANSCRIPT:\n{conversation_text}\n\n"
        f"Summarize this conversation with focus on: {query}"
    )

    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = await async_call_llm(
                task="session_search",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.1,
                max_tokens=MAX_SUMMARY_TOKENS,
            )
            content = extract_content_or_reasoning(response)
            if content:
                return content
            # 仅返回推理内容/为空 — 让重试循环处理
            logging.warning("Session search LLM returned empty content (attempt %d/%d)", attempt + 1, max_retries)
            if attempt < max_retries - 1:
                await asyncio.sleep(1 * (attempt + 1))
                continue
            return content
        except RuntimeError:
            logging.warning("No auxiliary model available for session summarization")
            return None
        except Exception as e:
            if attempt < max_retries - 1:
                await asyncio.sleep(1 * (attempt + 1))
            else:
                logging.warning(
                    "Session summarization failed after %d attempts: %s",
                    max_retries,
                    e,
                    exc_info=True,
                )
                return None


# 默认从会话浏览/搜索中排除的来源。
# 第三方集成（Paperclip 智能体等）使用 HERMES_SESSION_SOURCE=tool
# 标记其会话，以免干扰用户的会话历史。
_HIDDEN_SESSION_SOURCES = ("tool",)


def _list_recent_sessions(db, limit: int, current_session_id: str = None) -> str:
    """返回最近会话的元数据（不调用 LLM）。"""
    try:
        sessions = db.list_sessions_rich(limit=limit + 5, exclude_sources=list(_HIDDEN_SESSION_SOURCES))  # 多取一些以跳过当前会话

        # 解析当前会话的血缘关系以排除它
        current_root = None
        if current_session_id:
            try:
                sid = current_session_id
                visited = set()
                while sid and sid not in visited:
                    visited.add(sid)
                    s = db.get_session(sid)
                    parent = s.get("parent_session_id") if s else None
                    sid = parent if parent else None
                current_root = max(visited, key=len) if visited else current_session_id
            except Exception:
                current_root = current_session_id

        results = []
        for s in sessions:
            sid = s.get("id", "")
            if current_root and (sid == current_root or sid == current_session_id):
                continue
            # 跳过子/委托会话（它们有 parent_session_id）
            if s.get("parent_session_id"):
                continue
            results.append({
                "session_id": sid,
                "title": s.get("title") or None,
                "source": s.get("source", ""),
                "started_at": s.get("started_at", ""),
                "last_active": s.get("last_active", ""),
                "message_count": s.get("message_count", 0),
                "preview": s.get("preview", ""),
            })
            if len(results) >= limit:
                break

        return json.dumps({
            "success": True,
            "mode": "recent",
            "results": results,
            "count": len(results),
            "message": f"Showing {len(results)} most recent sessions. Use a keyword query to search specific topics.",
        }, ensure_ascii=False)
    except Exception as e:
        logging.error("Error listing recent sessions: %s", e, exc_info=True)
        return tool_error(f"Failed to list recent sessions: {e}", success=False)


def session_search(
    query: str,
    role_filter: str = None,
    limit: int = 3,
    db=None,
    current_session_id: str = None,
) -> str:
    """
    搜索过往会话并返回匹配对话的聚焦摘要。

    使用 FTS5 查找匹配项，然后用 Gemini Flash 对顶部会话进行摘要。
    当前会话被排除在结果之外，因为智能体已经拥有该上下文。
    """
    if db is None:
        return tool_error("Session database not available.", success=False)

    # 防御性处理：模型（尤其是开源模型）可能发送非整数的 limit 值
    #（JSON null 时为 None，字符串 "int"，甚至是类型对象）。在任何
    # 算术/比较之前强制转换为安全整数以防止 TypeError。
    if not isinstance(limit, int):
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = 3
    limit = max(1, min(limit, 5))  # 限制在 [1, 5] 范围内

    # 最近会话模式：当查询为空时，返回最近会话的元数据。
    # 不调用 LLM — 只查询数据库获取标题、预览和时间戳。
    if not query or not query.strip():
        return _list_recent_sessions(db, limit, current_session_id)

    query = query.strip()

    try:
        # 解析角色过滤器
        role_list = None
        if role_filter and role_filter.strip():
            role_list = [r.strip() for r in role_filter.split(",") if r.strip()]

        # FTS5 搜索 -- 按相关性排名获取匹配结果
        raw_results = db.search_messages(
            query=query,
            role_filter=role_list,
            exclude_sources=list(_HIDDEN_SESSION_SOURCES),
            limit=50,  # 获取更多匹配结果以找到唯一会话
            offset=0,
        )

        if not raw_results:
            return json.dumps({
                "success": True,
                "query": query,
                "results": [],
                "count": 0,
                "message": "No matching sessions found.",
            }, ensure_ascii=False)

        # 将子会话解析到其父会话 — 委托模式将详细内容
        # 存储在子会话中，但用户的对话在父会话中。
        def _resolve_to_parent(session_id: str) -> str:
            """沿委托链向上查找根父会话 ID。"""
            visited = set()
            sid = session_id
            while sid and sid not in visited:
                visited.add(sid)
                try:
                    session = db.get_session(sid)
                    if not session:
                        break
                    parent = session.get("parent_session_id")
                    if parent:
                        sid = parent
                    else:
                        break
                except Exception as e:
                    logging.debug(
                        "Error resolving parent for session %s: %s",
                        sid,
                        e,
                        exc_info=True,
                    )
                    break
            return sid

        current_lineage_root = (
            _resolve_to_parent(current_session_id) if current_session_id else None
        )

        # 按解析后的（父）session_id 分组，去重，跳过当前
        # 会话血缘。压缩和委托创建的子会话仍属于同一个活跃对话。
        seen_sessions = {}
        for result in raw_results:
            raw_sid = result["session_id"]
            resolved_sid = _resolve_to_parent(raw_sid)
            # 跳过当前会话血缘 — 智能体已经拥有该上下文，
            # 即使较早的轮次存在于父片段中。
            if current_lineage_root and resolved_sid == current_lineage_root:
                continue
            if current_session_id and raw_sid == current_session_id:
                continue
            if resolved_sid not in seen_sessions:
                result = dict(result)
                result["session_id"] = resolved_sid
                seen_sessions[resolved_sid] = result
            if len(seen_sessions) >= limit:
                break

        # 准备所有会话进行并行摘要
        tasks = []
        for session_id, match_info in seen_sessions.items():
            try:
                messages = db.get_messages_as_conversation(session_id)
                if not messages:
                    continue
                session_meta = db.get_session(session_id) or {}
                conversation_text = _format_conversation(messages)
                conversation_text = _truncate_around_matches(conversation_text, query)
                tasks.append((session_id, match_info, conversation_text, session_meta))
            except Exception as e:
                logging.warning(
                    "Failed to prepare session %s: %s",
                    session_id,
                    e,
                    exc_info=True,
                )

        # 并行摘要所有会话
        async def _summarize_all() -> List[Union[str, Exception]]:
            """并行摘要所有会话。"""
            coros = [
                _summarize_session(text, query, meta)
                for _, _, text, meta in tasks
            ]
            return await asyncio.gather(*coros, return_exceptions=True)

        try:
            # 使用 _run_async()，它能正确管理 CLI、网关和
            # 工作线程上下文中的事件循环。之前的模式
            #（在 ThreadPoolExecutor 中使用 asyncio.run()）创建了一个
            # 一次性事件循环，与绑定到不同循环的缓存
            # AsyncOpenAI/httpx 客户端冲突，
            # 导致网关模式下的死锁（#2681）。
            from model_tools import _run_async
            results = _run_async(_summarize_all())
        except concurrent.futures.TimeoutError:
            logging.warning(
                "Session summarization timed out after 60 seconds",
                exc_info=True,
            )
            return json.dumps({
                "success": False,
                "error": "Session summarization timed out. Try a more specific query or reduce the limit.",
            }, ensure_ascii=False)

        summaries = []
        for (session_id, match_info, conversation_text, _), result in zip(tasks, results):
            if isinstance(result, Exception):
                logging.warning(
                    "Failed to summarize session %s: %s",
                    session_id, result, exc_info=True,
                )
                result = None

            entry = {
                "session_id": session_id,
                "when": _format_timestamp(match_info.get("session_started")),
                "source": match_info.get("source", "unknown"),
                "model": match_info.get("model"),
            }

            if result:
                entry["summary"] = result
            else:
                # 回退：提供原始预览，这样匹配到的会话在摘要器不可用时
                # 不会被静默丢弃（修复 #3409）。
                preview = (conversation_text[:500] + "\n…[truncated]") if conversation_text else "No preview available."
                entry["summary"] = f"[Raw preview — summarization unavailable]\n{preview}"

            summaries.append(entry)

        return json.dumps({
            "success": True,
            "query": query,
            "results": summaries,
            "count": len(summaries),
            "sessions_searched": len(seen_sessions),
        }, ensure_ascii=False)

    except Exception as e:
        logging.error("Session search failed: %s", e, exc_info=True)
        return tool_error(f"Search failed: {str(e)}", success=False)


def check_session_search_requirements() -> bool:
    """需要 SQLite 状态数据库和辅助文本模型。"""
    try:
        from hermes_state import DEFAULT_DB_PATH
        return DEFAULT_DB_PATH.parent.exists()
    except ImportError:
        return False


SESSION_SEARCH_SCHEMA = {
    "name": "session_search",
    "description": (
        "Search your long-term memory of past conversations, or browse recent sessions. This is your recall -- "
        "every past session is searchable, and this tool summarizes what happened.\n\n"
        "TWO MODES:\n"
        "1. Recent sessions (no query): Call with no arguments to see what was worked on recently. "
        "Returns titles, previews, and timestamps. Zero LLM cost, instant. "
        "Start here when the user asks what were we working on or what did we do recently.\n"
        "2. Keyword search (with query): Search for specific topics across all past sessions. "
        "Returns LLM-generated summaries of matching sessions.\n\n"
        "USE THIS PROACTIVELY when:\n"
        "- The user says 'we did this before', 'remember when', 'last time', 'as I mentioned'\n"
        "- The user asks about a topic you worked on before but don't have in current context\n"
        "- The user references a project, person, or concept that seems familiar but isn't in memory\n"
        "- You want to check if you've solved a similar problem before\n"
        "- The user asks 'what did we do about X?' or 'how did we fix Y?'\n\n"
        "Don't hesitate to search when it is actually cross-session -- it's fast and cheap. "
        "Better to search and confirm than to guess or ask the user to repeat themselves.\n\n"
        "Search syntax: keywords joined with OR for broad recall (elevenlabs OR baseten OR funding), "
        "phrases for exact match (\"docker networking\"), boolean (python NOT java), prefix (deploy*). "
        "IMPORTANT: Use OR between keywords for best results — FTS5 defaults to AND which misses "
        "sessions that only mention some terms. If a broad OR query returns nothing, try individual "
        "keyword searches in parallel. Returns summaries of the top matching sessions."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query — keywords, phrases, or boolean expressions to find in past sessions. Omit this parameter entirely to browse recent sessions instead (returns titles, previews, timestamps with no LLM cost).",
            },
            "role_filter": {
                "type": "string",
                "description": "Optional: only search messages from specific roles (comma-separated). E.g. 'user,assistant' to skip tool outputs.",
            },
            "limit": {
                "type": "integer",
                "description": "Max sessions to summarize (default: 3, max: 5).",
                "default": 3,
            },
        },
        "required": [],
    },
}


# --- 注册到工具注册表 ---
from tools.registry import registry, tool_error

registry.register(
    name="session_search",
    toolset="session_search",
    schema=SESSION_SEARCH_SCHEMA,
    handler=lambda args, **kw: session_search(
        query=args.get("query") or "",
        role_filter=args.get("role_filter"),
        limit=args.get("limit", 3),
        db=kw.get("db"),
        current_session_id=kw.get("current_session_id")),
    check_fn=check_session_search_requirements,
    emoji="🔍",
)
