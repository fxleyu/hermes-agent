"""Nous Portal 的跨会话速率限制防护。

将速率限制状态写入共享文件，使所有会话（CLI、网关、
cron、辅助）在发起请求前检查 Nous Portal 是否当前处于
速率限制状态。防止 RPH 配额耗尽时的重试放大效应。

每个来自 Nous 的 429 错误会在每轮对话中触发最多 9 次 API 调用
（3 次 SDK 重试 x 3 次 Hermes 重试），且每一次调用都计入 RPH。
通过在首次 429 时记录速率限制状态，并在后续尝试前检查该状态，
我们消除了重试放大效应。
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from typing import Any, Mapping, Optional

logger = logging.getLogger(__name__)

_STATE_SUBDIR = "rate_limits"
_STATE_FILENAME = "nous.json"


def _state_path() -> str:
    """返回 Nous 速率限制状态文件的路径。"""
    try:
        from hermes_constants import get_hermes_home
        base = get_hermes_home()
    except ImportError:
        base = os.path.join(os.path.expanduser("~"), ".hermes")
    return os.path.join(base, _STATE_SUBDIR, _STATE_FILENAME)


def _parse_reset_seconds(headers: Optional[Mapping[str, str]]) -> Optional[float]:
    """从响应头中提取最佳可用的重置时间估算。

    优先级：
      1. x-ratelimit-reset-requests-1h（每小时 RPH 窗口——最有用）
      2. x-ratelimit-reset-requests（每分钟 RPM 窗口）
      3. retry-after（通用 HTTP 头）

    返回距现在的秒数，如果没有可用头信息则返回 None。
    """
    if not headers:
        return None

    lowered = {k.lower(): v for k, v in headers.items()}

    for key in (
        "x-ratelimit-reset-requests-1h",
        "x-ratelimit-reset-requests",
        "retry-after",
    ):
        raw = lowered.get(key)
        if raw is not None:
            try:
                val = float(raw)
                if val > 0:
                    return val
            except (TypeError, ValueError):
                pass

    return None


def record_nous_rate_limit(
    *,
    headers: Optional[Mapping[str, str]] = None,
    error_context: Optional[dict[str, Any]] = None,
    default_cooldown: float = 300.0,
) -> None:
    """记录 Nous Portal 当前处于速率限制状态。

    从响应头或错误上下文中解析重置时间。
    如果没有重置信息，回退到 ``default_cooldown``（5 分钟）。
    写入所有会话可读取的共享文件。

    参数：
        headers：来自 429 错误的 HTTP 响应头。
        error_context：来自 _extract_api_error_context() 的结构化错误上下文。
        default_cooldown：无头信息数据时的回退冷却时间（秒）。
    """
    now = time.time()
    reset_at = None

    # 首先尝试从头信息获取（最准确）
    header_seconds = _parse_reset_seconds(headers)
    if header_seconds is not None:
        reset_at = now + header_seconds

    # 尝试从 error_context 的 reset_at 获取（来自响应体解析）
    if reset_at is None and isinstance(error_context, dict):
        ctx_reset = error_context.get("reset_at")
        if isinstance(ctx_reset, (int, float)) and ctx_reset > now:
            reset_at = float(ctx_reset)

    # 使用默认冷却时间
    if reset_at is None:
        reset_at = now + default_cooldown

    path = _state_path()
    try:
        state_dir = os.path.dirname(path)
        os.makedirs(state_dir, exist_ok=True)

        state = {
            "reset_at": reset_at,
            "recorded_at": now,
            "reset_seconds": reset_at - now,
        }

        # 原子写入：先写入临时文件，再重命名
        fd, tmp_path = tempfile.mkstemp(dir=state_dir, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(state, f)
            os.replace(tmp_path, path)
        except Exception:
            # 写入失败时清理临时文件
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

        logger.info(
            "Nous rate limit recorded: resets in %.0fs (at %.0f)",
            reset_at - now, reset_at,
        )
    except Exception as exc:
        logger.debug("Failed to write Nous rate limit state: %s", exc)


def nous_rate_limit_remaining() -> Optional[float]:
    """检查 Nous Portal 当前是否处于速率限制状态。

    返回：
        距重置的剩余秒数，如果未受限则返回 None。
    """
    path = _state_path()
    try:
        with open(path) as f:
            state = json.load(f)
        reset_at = state.get("reset_at", 0)
        remaining = reset_at - time.time()
        if remaining > 0:
            return remaining
        # 已过期——清理状态文件
        try:
            os.unlink(path)
        except OSError:
            pass
        return None
    except (FileNotFoundError, json.JSONDecodeError, KeyError, TypeError):
        return None


def clear_nous_rate_limit() -> None:
    """清除速率限制状态（例如在 Nous 请求成功后）。"""
    try:
        os.unlink(_state_path())
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.debug("Failed to clear Nous rate limit state: %s", exc)


def format_remaining(seconds: float) -> str:
    """将剩余秒数格式化为人类可读的时间。"""
    s = max(0, int(seconds))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        m, sec = divmod(s, 60)
        return f"{m}m {sec}s" if sec else f"{m}m"
    h, remainder = divmod(s, 3600)
    m = remainder // 60
    return f"{h}h {m}m" if m else f"{h}h"
