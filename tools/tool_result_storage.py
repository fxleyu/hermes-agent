"""工具结果持久化——保留大型输出而非截断。

防止上下文窗口溢出的防线在三个层级运作：

1. **每工具输出上限**（在每个工具内部）：像 search_files 这样的工具
   在返回之前会预先截断自己的输出。这是第一道防线，也是工具作者
   唯一可控的。

2. **每结果持久化**（maybe_persist_tool_result）：工具返回后，
   如果其输出超过该工具注册的阈值
   （registry.get_max_result_size），完整输出将通过 env.execute()
   写入沙箱临时目录（例如标准 Linux 上的
   /tmp/hermes-results/{tool_use_id}.txt，或 Termux 上的
   $TMPDIR/hermes-results/{tool_use_id}.txt）。
   上下文中的内容被替换为预览 + 文件路径引用。
   模型可以通过 read_file 在任何后端访问完整输出。

3. **每轮次总预算**（enforce_turn_budget）：在单个助手轮次中
   收集完所有工具结果后，如果总量超过 MAX_TURN_BUDGET_CHARS
   (200K)，最大的未持久化结果将被写入磁盘，直到总量在预算内。
   这可以捕获多个中等大小结果组合导致上下文溢出的情况。
"""

import logging
import os
import shlex
import uuid

from tools.budget_config import (
    DEFAULT_PREVIEW_SIZE_CHARS,
    BudgetConfig,
    DEFAULT_BUDGET,
)

logger = logging.getLogger(__name__)
PERSISTED_OUTPUT_TAG = "<persisted-output>"
PERSISTED_OUTPUT_CLOSING_TAG = "</persisted-output>"
STORAGE_DIR = "/tmp/hermes-results"
HEREDOC_MARKER = "HERMES_PERSIST_EOF"
_BUDGET_TOOL_NAME = "__budget_enforcement__"


def _resolve_storage_dir(env) -> str:
    """返回当前环境中最佳的基于 temp 的存储目录。"""
    if env is not None:
        get_temp_dir = getattr(env, "get_temp_dir", None)
        if callable(get_temp_dir):
            try:
                temp_dir = get_temp_dir()
            except Exception as exc:
                logger.debug("Could not resolve env temp dir: %s", exc)
            else:
                if temp_dir:
                    temp_dir = temp_dir.rstrip("/") or "/"
                    return f"{temp_dir}/hermes-results"
    return STORAGE_DIR


def generate_preview(content: str, max_chars: int = DEFAULT_PREVIEW_SIZE_CHARS) -> tuple[str, bool]:
    """在 max_chars 范围内的最后一个换行符处截断。返回 (preview, has_more)。"""
    if len(content) <= max_chars:
        return content, False
    truncated = content[:max_chars]
    last_nl = truncated.rfind("\n")
    if last_nl > max_chars // 2:
        truncated = truncated[:last_nl + 1]
    return truncated, True


def _heredoc_marker(content: str) -> str:
    """返回一个不与内容冲突的 heredoc 分隔符。"""
    if HEREDOC_MARKER not in content:
        return HEREDOC_MARKER
    return f"HERMES_PERSIST_{uuid.uuid4().hex[:8]}"


def _write_to_sandbox(content: str, remote_path: str, env) -> bool:
    """通过 env.execute() 将内容写入沙箱。成功返回 True。"""
    marker = _heredoc_marker(content)
    storage_dir = os.path.dirname(remote_path)
    cmd = (
        f"mkdir -p {shlex.quote(storage_dir)} && cat > {shlex.quote(remote_path)} << '{marker}'\n"
        f"{content}\n"
        f"{marker}"
    )
    result = env.execute(cmd, timeout=30)
    return result.get("returncode", 1) == 0


def _build_persisted_message(
    preview: str,
    has_more: bool,
    original_size: int,
    file_path: str,
) -> str:
    """构建 <persisted-output> 替换块。"""
    size_kb = original_size / 1024
    if size_kb >= 1024:
        size_str = f"{size_kb / 1024:.1f} MB"
    else:
        size_str = f"{size_kb:.1f} KB"

    msg = f"{PERSISTED_OUTPUT_TAG}\n"
    msg += f"This tool result was too large ({original_size:,} characters, {size_str}).\n"
    msg += f"Full output saved to: {file_path}\n"
    msg += "Use the read_file tool with offset and limit to access specific sections of this output.\n\n"
    msg += f"Preview (first {len(preview)} chars):\n"
    msg += preview
    if has_more:
        msg += "\n..."
    msg += f"\n{PERSISTED_OUTPUT_CLOSING_TAG}"
    return msg


def maybe_persist_tool_result(
    content: str,
    tool_name: str,
    tool_use_id: str,
    env=None,
    config: BudgetConfig = DEFAULT_BUDGET,
    threshold: int | float | None = None,
) -> str:
    """第 2 层：将超大结果持久化到沙箱中，返回预览 + 路径。

    通过 env.execute() 写入，使文件在任何后端（本地、Docker、SSH、
    Modal、Daytona）上都可访问。如果写入失败或没有可用的 env，
    则回退到内联截断。

    参数:
        content: 原始工具结果字符串。
        tool_name: 工具名称（用于阈值查找）。
        tool_use_id: 此次工具调用的唯一 ID（用作文件名）。
        env: 活跃的 BaseEnvironment 实例，或 None。
        config: 控制阈值和预览大小的 BudgetConfig。
        threshold: 显式覆盖；优先于配置解析。

    返回:
        如果较小则返回原始内容，否则返回 <persisted-output> 替换内容。
    """
    effective_threshold = threshold if threshold is not None else config.resolve_threshold(tool_name)

    if effective_threshold == float("inf"):
        return content

    if len(content) <= effective_threshold:
        return content

    storage_dir = _resolve_storage_dir(env)
    remote_path = f"{storage_dir}/{tool_use_id}.txt"
    preview, has_more = generate_preview(content, max_chars=config.preview_size)

    if env is not None:
        try:
            if _write_to_sandbox(content, remote_path, env):
                logger.info(
                    "Persisted large tool result: %s (%s, %d chars -> %s)",
                    tool_name, tool_use_id, len(content), remote_path,
                )
                return _build_persisted_message(preview, has_more, len(content), remote_path)
        except Exception as exc:
            logger.warning("Sandbox write failed for %s: %s", tool_use_id, exc)

    logger.info(
        "Inline-truncating large tool result: %s (%d chars, no sandbox write)",
        tool_name, len(content),
    )
    return (
        f"{preview}\n\n"
        f"[Truncated: tool response was {len(content):,} chars. "
        f"Full output could not be saved to sandbox.]"
    )


def enforce_turn_budget(
    tool_messages: list[dict],
    env=None,
    config: BudgetConfig = DEFAULT_BUDGET,
) -> list[dict]:
    """第 3 层：在一个轮次中对所有工具结果强制执行总预算。

    如果总字符数超过预算，首先通过沙箱写入持久化最大的
    未持久化结果，直到预算内。已持久化的结果会被跳过。

    就地修改列表并返回。
    """
    candidates = []
    total_size = 0
    for i, msg in enumerate(tool_messages):
        content = msg.get("content", "")
        size = len(content)
        total_size += size
        if PERSISTED_OUTPUT_TAG not in content:
            candidates.append((i, size))

    if total_size <= config.turn_budget:
        return tool_messages

    candidates.sort(key=lambda x: x[1], reverse=True)

    for idx, size in candidates:
        if total_size <= config.turn_budget:
            break
        msg = tool_messages[idx]
        content = msg["content"]
        tool_use_id = msg.get("tool_call_id", f"budget_{idx}")

        replacement = maybe_persist_tool_result(
            content=content,
            tool_name=_BUDGET_TOOL_NAME,
            tool_use_id=tool_use_id,
            env=env,
            config=config,
            threshold=0,
        )
        if replacement != content:
            total_size -= size
            total_size += len(replacement)
            tool_messages[idx]["content"] = replacement
            logger.info(
                "Budget enforcement: persisted tool result %s (%d chars)",
                tool_use_id, size,
            )

    return tool_messages
