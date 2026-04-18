"""根据首轮用户/助手交互自动生成简短的会话标题。

在首次响应交付后异步运行，不会给面向用户的回复增加延迟。
"""

import logging
import threading
from typing import Optional

from agent.auxiliary_client import call_llm

logger = logging.getLogger(__name__)

_TITLE_PROMPT = (
    "Generate a short, descriptive title (3-7 words) for a conversation that starts with the "
    "following exchange. The title should capture the main topic or intent. "
    "Return ONLY the title text, nothing else. No quotes, no punctuation at the end, no prefixes."
)


def generate_title(user_message: str, assistant_response: str, timeout: float = 30.0) -> Optional[str]:
    """根据首轮交互生成会话标题。

    使用辅助 LLM 客户端（最便宜/最快的可用模型）。
    返回标题字符串，失败时返回 None。
    """
    # 截断过长的消息以保持请求精简
    user_snippet = user_message[:500] if user_message else ""
    assistant_snippet = assistant_response[:500] if assistant_response else ""

    messages = [
        {"role": "system", "content": _TITLE_PROMPT},
        {"role": "user", "content": f"User: {user_snippet}\n\nAssistant: {assistant_snippet}"},
    ]

    try:
        response = call_llm(
            task="title_generation",
            messages=messages,
            max_tokens=30,
            temperature=0.3,
            timeout=timeout,
        )
        title = (response.choices[0].message.content or "").strip()
        # 清理：移除引号、尾部标点、"Title: " 等前缀
        title = title.strip('"\'')
        if title.lower().startswith("title:"):
            title = title[6:].strip()
        # 强制执行合理的长度限制
        if len(title) > 80:
            title = title[:77] + "..."
        return title if title else None
    except Exception as e:
        logger.debug("Title generation failed: %s", e)
        return None


def auto_title_session(
    session_db,
    session_id: str,
    user_message: str,
    assistant_response: str,
) -> None:
    """如果尚未设置标题，则生成并设置会话标题。

    在首轮交互完成后于后台线程中调用。
    在以下情况下静默跳过：
    - session_db 为 None
    - 会话已有标题（用户设置的或之前自动生成的）
    - 标题生成失败
    """
    if not session_db or not session_id:
        return

    # 检查标题是否已存在（用户可能在首次响应前通过 /title 设置了标题）
    try:
        existing = session_db.get_session_title(session_id)
        if existing:
            return
    except Exception:
        return

    title = generate_title(user_message, assistant_response)
    if not title:
        return

    try:
        session_db.set_session_title(session_id, title)
        logger.debug("Auto-generated session title: %s", title)
    except Exception as e:
        logger.debug("Failed to set auto-generated title: %s", e)


def maybe_auto_title(
    session_db,
    session_id: str,
    user_message: str,
    assistant_response: str,
    conversation_history: list,
) -> None:
    """首轮交互后即发即忘的标题生成。

    仅在以下条件满足时生成标题：
    - 这看起来是首次用户-助手交互
    - 尚未设置标题
    """
    if not session_db or not session_id or not user_message or not assistant_response:
        return

    # 统计历史中的用户消息数以检测是否为首轮交互。
    # conversation_history 包含刚刚发生的交互，
    # 因此对于首轮交互我们期望恰好 1 条用户消息
    # （或算上 system 消息为 2 条）。宽容处理：在前 2 轮交互时都生成。
    user_msg_count = sum(1 for m in (conversation_history or []) if m.get("role") == "user")
    if user_msg_count > 2:
        return

    # 启动后台守护线程执行标题生成，不阻塞主流程
    thread = threading.Thread(
        target=auto_title_session,
        args=(session_db, session_id, user_message, assistant_response),
        daemon=True,
        name="auto-title",
    )
    thread.start()
