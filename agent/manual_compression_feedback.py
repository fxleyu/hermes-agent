"""手动压缩命令的面向用户的摘要反馈。"""

from __future__ import annotations

from typing import Any, Sequence


def summarize_manual_compression(
    before_messages: Sequence[dict[str, Any]],
    after_messages: Sequence[dict[str, Any]],
    before_tokens: int,
    after_tokens: int,
) -> dict[str, Any]:
    """返回手动压缩的一致性用户反馈。"""
    before_count = len(before_messages)
    after_count = len(after_messages)
    # 判断压缩是否为空操作（前后消息列表完全相同）
    noop = list(after_messages) == list(before_messages)

    if noop:
        headline = f"No changes from compression: {before_count} messages"
        if after_tokens == before_tokens:
            token_line = (
                f"Rough transcript estimate: ~{before_tokens:,} tokens (unchanged)"
            )
        else:
            token_line = (
                f"Rough transcript estimate: ~{before_tokens:,} → "
                f"~{after_tokens:,} tokens"
            )
    else:
        headline = f"Compressed: {before_count} → {after_count} messages"
        token_line = (
            f"Rough transcript estimate: ~{before_tokens:,} → "
            f"~{after_tokens:,} tokens"
        )

    # 当消息数减少但令牌估算反而增加时，提供解释说明
    note = None
    if not noop and after_count < before_count and after_tokens > before_tokens:
        note = (
            "Note: fewer messages can still raise this rough transcript estimate "
            "when compression rewrites the transcript into denser summaries."
        )

    return {
        "noop": noop,
        "headline": headline,
        "token_line": token_line,
        "note": note,
    }
