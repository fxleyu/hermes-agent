"""``hermes logs`` —— 查看和筛选 Hermes 日志文件。

支持尾部查看、实时跟踪、会话过滤、级别过滤、
组件过滤和相对时间范围。所有日志文件位于
``~/.hermes/logs/`` 下。

使用示例::

    hermes logs                    # 查看 agent.log 的最后 50 行
    hermes logs -f                 # 实时跟踪 agent.log
    hermes logs errors             # 查看 errors.log 的最后 50 行
    hermes logs gateway -n 100    # 查看 gateway.log 的最后 100 行
    hermes logs --level WARNING    # 仅显示 WARNING 及以上级别的行
    hermes logs --session abc123   # 按会话 ID 子串过滤
    hermes logs --component tools  # 仅显示工具相关的行
    hermes logs --since 1h         # 最近一小时的行
    hermes logs --since 30m -f     # 从 30 分钟前开始实时跟踪
"""

import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Sequence

from hermes_constants import get_hermes_home, display_hermes_home

# 已知的日志文件（名称 → 文件名）
LOG_FILES = {
    "agent": "agent.log",
    "errors": "errors.log",
    "gateway": "gateway.log",
}

# 日志行时间戳正则 —— 匹配行首的 "2026-04-05 22:35:00,123" 或
# "2026-04-05 22:35:00" 格式
_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})")

# 日志级别提取 —— 匹配 " INFO "、" WARNING "、" ERROR "、" DEBUG "、" CRITICAL "
_LEVEL_RE = re.compile(r"\s(DEBUG|INFO|WARNING|ERROR|CRITICAL)\s")

# 日志器名称提取 —— 在级别和可选的会话标签之后，冒号之前的
# 非空白 token 就是日志器名称。
# 匹配示例: "INFO gateway.run:" 或 "INFO [sess_abc] tools.terminal_tool:"
_LOGGER_NAME_RE = re.compile(
    r"\s(?:DEBUG|INFO|WARNING|ERROR|CRITICAL)"  # 日志级别
    r"(?:\s+\[.*?\])?"                           # 可选的会话标签
    r"\s+(\S+):"                                 # 日志器名称
)

# 日志级别排序，用于 >= 过滤比较
_LEVEL_ORDER = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3, "CRITICAL": 4}


def _parse_since(since_str: str) -> Optional[datetime]:
    """将相对时间字符串（如 '1h'、'30m'、'2d'）解析为时间截止点。

    如果字符串无法解析，返回 None。
    """
    since_str = since_str.strip().lower()
    # 匹配数字+时间单位的格式（s=秒, m=分, h=时, d=天）
    match = re.match(r"^(\d+)\s*([smhd])$", since_str)
    if not match:
        return None
    value = int(match.group(1))
    unit = match.group(2)
    # 根据单位创建对应的时间增量
    delta = {
        "s": timedelta(seconds=value),
        "m": timedelta(minutes=value),
        "h": timedelta(hours=value),
        "d": timedelta(days=value),
    }[unit]
    # 返回当前时间减去时间增量，即截止时间点
    return datetime.now() - delta


def _parse_line_timestamp(line: str) -> Optional[datetime]:
    """从日志行中提取时间戳。无法解析时返回 None。"""
    m = _TS_RE.match(line)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _extract_level(line: str) -> Optional[str]:
    """从日志行中提取日志级别。"""
    m = _LEVEL_RE.search(line)
    return m.group(1) if m else None


def _extract_logger_name(line: str) -> Optional[str]:
    """从日志行中提取日志器名称。"""
    m = _LOGGER_NAME_RE.search(line)
    return m.group(1) if m else None


def _line_matches_component(line: str, prefixes: Sequence[str]) -> bool:
    """检查日志行的日志器名称是否以给定的任意前缀开头。"""
    name = _extract_logger_name(line)
    if name is None:
        return False
    return name.startswith(tuple(prefixes))


def _matches_filters(
    line: str,
    *,
    min_level: Optional[str] = None,
    session_filter: Optional[str] = None,
    since: Optional[datetime] = None,
    component_prefixes: Optional[Sequence[str]] = None,
) -> bool:
    """检查日志行是否通过所有激活的过滤条件。"""
    # 时间过滤：如果行的时间戳早于截止时间，则不匹配
    if since is not None:
        ts = _parse_line_timestamp(line)
        if ts is not None and ts < since:
            return False

    # 级别过滤：如果行的级别低于最低级别要求，则不匹配
    if min_level is not None:
        level = _extract_level(line)
        if level is not None:
            if _LEVEL_ORDER.get(level, 0) < _LEVEL_ORDER.get(min_level, 0):
                return False

    # 会话过滤：如果行中不包含会话 ID 子串，则不匹配
    if session_filter is not None:
        if session_filter not in line:
            return False

    # 组件过滤：如果行的日志器名称不匹配任何组件前缀，则不匹配
    if component_prefixes is not None:
        if not _line_matches_component(line, component_prefixes):
            return False

    return True


def tail_log(
    log_name: str = "agent",
    *,
    num_lines: int = 50,
    follow: bool = False,
    level: Optional[str] = None,
    session: Optional[str] = None,
    since: Optional[str] = None,
    component: Optional[str] = None,
) -> None:
    """读取并显示日志行，可选择实时跟踪。

    参数
    ----------
    log_name
        要读取的日志文件: ``"agent"``、``"errors"``、``"gateway"``。
    num_lines
        要显示的最近行数（在开始跟踪之前）。
    follow
        如果为 True，持续监控新行（按 Ctrl+C 停止）。
    level
        要显示的最低日志级别（例如 ``"WARNING"``）。
    session
        用于过滤的会话 ID 子串。
    since
        相对时间字符串（例如 ``"1h"``、``"30m"``）。
    component
        用于过滤的组件名称（例如 ``"gateway"``、``"tools"``）。
    """
    filename = LOG_FILES.get(log_name)
    if filename is None:
        print(f"Unknown log: {log_name!r}. Available: {', '.join(sorted(LOG_FILES))}")
        sys.exit(1)

    log_path = get_hermes_home() / "logs" / filename
    if not log_path.exists():
        print(f"Log file not found: {log_path}")
        print(f"(Logs are created when Hermes runs — try 'hermes chat' first)")
        sys.exit(1)

    # 将 --since 参数解析为时间截止点
    since_dt = None
    if since:
        since_dt = _parse_since(since)
        if since_dt is None:
            print(f"Invalid --since value: {since!r}. Use format like '1h', '30m', '2d'.")
            sys.exit(1)

    # 验证并标准化日志级别参数
    min_level = level.upper() if level else None
    if min_level and min_level not in _LEVEL_ORDER:
        print(f"Invalid --level: {level!r}. Use DEBUG, INFO, WARNING, ERROR, or CRITICAL.")
        sys.exit(1)

    # 将组件名称解析为日志器名称前缀列表
    component_prefixes = None
    if component:
        from hermes_logging import COMPONENT_PREFIXES
        component_lower = component.lower()
        if component_lower not in COMPONENT_PREFIXES:
            available = ", ".join(sorted(COMPONENT_PREFIXES))
            print(f"Unknown component: {component!r}. Available: {available}")
            sys.exit(1)
        component_prefixes = COMPONENT_PREFIXES[component_lower]

    # 判断是否有任何过滤条件被激活
    has_filters = (
        min_level is not None
        or session is not None
        or since_dt is not None
        or component_prefixes is not None
    )

    # 读取并显示尾部日志
    try:
        lines = _read_tail(log_path, num_lines, has_filters=has_filters,
                           min_level=min_level, session_filter=session,
                           since=since_dt, component_prefixes=component_prefixes)
    except PermissionError:
        print(f"Permission denied: {log_path}")
        sys.exit(1)

    # 打印头部信息（包含过滤条件描述）
    filter_parts = []
    if min_level:
        filter_parts.append(f"level>={min_level}")
    if session:
        filter_parts.append(f"session={session}")
    if component:
        filter_parts.append(f"component={component}")
    if since:
        filter_parts.append(f"since={since}")
    filter_desc = f" [{', '.join(filter_parts)}]" if filter_parts else ""

    if follow:
        print(f"--- {display_hermes_home()}/logs/{filename}{filter_desc} (Ctrl+C to stop) ---")
    else:
        print(f"--- {display_hermes_home()}/logs/{filename}{filter_desc} (last {num_lines}) ---")

    for line in lines:
        print(line, end="")

    if not follow:
        return

    # 跟踪模式 —— 轮询新内容
    try:
        _follow_log(log_path, min_level=min_level, session_filter=session,
                     since=since_dt, component_prefixes=component_prefixes)
    except KeyboardInterrupt:
        print("\n--- stopped ---")


def _read_tail(
    path: Path,
    num_lines: int,
    *,
    has_filters: bool = False,
    min_level: Optional[str] = None,
    session_filter: Optional[str] = None,
    since: Optional[datetime] = None,
    component_prefixes: Optional[Sequence[str]] = None,
) -> list:
    """从日志文件中读取最后 *num_lines* 条匹配的行。

    当有过滤条件时，会读取更多原始行以确保过滤后有足够的匹配结果。
    """
    if has_filters:
        # 有过滤条件时读取更多行，确保过滤后有足够的结果。
        # 对于大文件，读取最后 10K 行然后过滤。
        raw_lines = _read_last_n_lines(path, max(num_lines * 20, 2000))
        filtered = [
            l for l in raw_lines
            if _matches_filters(l, min_level=min_level,
                                session_filter=session_filter, since=since,
                                component_prefixes=component_prefixes)
        ]
        return filtered[-num_lines:]
    else:
        return _read_last_n_lines(path, num_lines)


def _read_last_n_lines(path: Path, n: int) -> list:
    """高效地读取文件的最后 N 行。

    对于 1MB 以下的文件，直接读取整个文件（简单快速）。
    对于更大的文件，从文件末尾分块读取。
    """
    try:
        size = path.stat().st_size
        if size == 0:
            return []

        # 对于 1MB 以内的文件，直接读取整个文件——简单且正确。
        if size <= 1_048_576:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                all_lines = f.readlines()
            return all_lines[-n:]

        # 对于大文件，从末尾分块读取。
        with open(path, "rb") as f:
            chunk_size = 8192
            lines = []
            pos = size

            while pos > 0 and len(lines) <= n + 1:
                read_size = min(chunk_size, pos)
                pos -= read_size
                f.seek(pos)
                chunk = f.read(read_size)
                chunk_lines = chunk.split(b"\n")
                if lines:
                    # 将新块的最后一个不完整行与已有内容的第一个不完整行合并
                    lines[0] = chunk_lines[-1] + lines[0]
                    lines = chunk_lines[:-1] + lines
                else:
                    lines = chunk_lines
                # 逐步增大块大小以提高效率，最大 64KB
                chunk_size = min(chunk_size * 2, 65536)

            # 解码并返回最后 N 行非空行
            decoded = []
            for raw in lines:
                if not raw.strip():
                    continue
                try:
                    decoded.append(raw.decode("utf-8", errors="replace") + "\n")
                except Exception:
                    decoded.append(raw.decode("latin-1") + "\n")
            return decoded[-n:]

    except Exception:
        # 兜底方案：读取整个文件
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
        return all_lines[-n:]


def _follow_log(
    path: Path,
    *,
    min_level: Optional[str] = None,
    session_filter: Optional[str] = None,
    since: Optional[datetime] = None,
    component_prefixes: Optional[Sequence[str]] = None,
) -> None:
    """轮询日志文件的新内容并打印匹配的行。"""
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        # 定位到文件末尾
        f.seek(0, 2)
        while True:
            line = f.readline()
            if line:
                if _matches_filters(line, min_level=min_level,
                                    session_filter=session_filter, since=since,
                                    component_prefixes=component_prefixes):
                    print(line, end="")
                    sys.stdout.flush()
            else:
                # 没有新内容时短暂等待后重试
                time.sleep(0.3)


def list_logs() -> None:
    """打印可用的日志文件及其大小。"""
    log_dir = get_hermes_home() / "logs"
    if not log_dir.exists():
        print(f"No logs directory at {display_hermes_home()}/logs/")
        return

    print(f"Log files in {display_hermes_home()}/logs/:\n")
    found = False
    for entry in sorted(log_dir.iterdir()):
        if entry.is_file() and entry.suffix == ".log":
            size = entry.stat().st_size
            mtime = datetime.fromtimestamp(entry.stat().st_mtime)
            # 将文件大小格式化为人类可读的形式
            if size < 1024:
                size_str = f"{size}B"
            elif size < 1024 * 1024:
                size_str = f"{size / 1024:.1f}KB"
            else:
                size_str = f"{size / (1024 * 1024):.1f}MB"
            # 计算文件修改时间距今的时间差并格式化
            age = datetime.now() - mtime
            if age.total_seconds() < 60:
                age_str = "just now"
            elif age.total_seconds() < 3600:
                age_str = f"{int(age.total_seconds() / 60)}m ago"
            elif age.total_seconds() < 86400:
                age_str = f"{int(age.total_seconds() / 3600)}h ago"
            else:
                age_str = mtime.strftime("%Y-%m-%d")
            print(f"  {entry.name:<25} {size_str:>8}   {age_str}")
            found = True

    if not found:
        print("  (no log files yet — run 'hermes chat' to generate logs)")
