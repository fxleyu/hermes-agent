#!/usr/bin/env python3
"""文件工具模块 - LLM 智能体文件操作工具。"""

import errno
import json
import logging
import os
import threading
from pathlib import Path
from tools.binary_extensions import has_binary_extension
from tools.file_operations import ShellFileOperations
from agent.redact import redact_sensitive_text

logger = logging.getLogger(__name__)


_EXPECTED_WRITE_ERRNOS = {errno.EACCES, errno.EPERM, errno.EROFS}

# ---------------------------------------------------------------------------
# 读取大小限制：限制返回给模型的字符数。
# 由于与模型无关，我们无法直接计算 token 数；字符数是一个安全的近似值。
# 100K 字符 ≈ 25-35K token（不同分词器略有差异）。单次读取超过
# 此限制的文件会占用过多上下文窗口——模型应使用 offset+limit 来
# 读取相关部分。
#
# 可通过 config.yaml 配置：file_read_max_chars: 200000
# ---------------------------------------------------------------------------
_DEFAULT_MAX_READ_CHARS = 100_000
_max_read_chars_cached: int | None = None


def _get_max_read_chars() -> int:
    """返回配置的单次文件读取最大字符数。

    首次调用时从 config.yaml 读取 ``file_read_max_chars``，并在
    进程生命周期内缓存结果。如果配置缺失或无效，则回退到内置默认值。
    """
    global _max_read_chars_cached
    if _max_read_chars_cached is not None:
        return _max_read_chars_cached
    try:
        from hermes_cli.config import load_config
        cfg = load_config()
        val = cfg.get("file_read_max_chars")
        if isinstance(val, (int, float)) and val > 0:
            _max_read_chars_cached = int(val)
            return _max_read_chars_cached
    except Exception:
        pass
    _max_read_chars_cached = _DEFAULT_MAX_READ_CHARS
    return _max_read_chars_cached

# 如果文件总大小超过此值且调用方未指定较小的范围（limit <= 200），
# 则附加提示鼓励使用定向读取。
_LARGE_FILE_HINT_BYTES = 512_000  # 512 KB

# ---------------------------------------------------------------------------
# 设备路径黑名单——读取这些路径会导致进程挂起（无限输出或阻塞等待输入）。
# 仅通过路径检查（无 I/O）。
# ---------------------------------------------------------------------------
_BLOCKED_DEVICE_PATHS = frozenset({
    # 无限输出——永远不会到达 EOF
    "/dev/zero", "/dev/random", "/dev/urandom", "/dev/full",
    # 阻塞等待输入
    "/dev/stdin", "/dev/tty", "/dev/console",
    # 读取无意义
    "/dev/stdout", "/dev/stderr",
    # 文件描述符别名
    "/dev/fd/0", "/dev/fd/1", "/dev/fd/2",
})


def _is_blocked_device(filepath: str) -> bool:
    """如果路径会导致进程挂起（无限输出或阻塞输入），返回 True。

    使用*字面*路径——不解析符号链接——因为模型直接指定路径，
    而 realpath 会一路追踪符号链接（例如 /dev/stdin → /proc/self/fd/0
    → /dev/pts/0），从而绕过检查。
    """
    normalized = os.path.expanduser(filepath)
    if normalized in _BLOCKED_DEVICE_PATHS:
        return True
    # /proc/self/fd/0-2 和 /proc/<pid>/fd/0-2 是 Linux 上标准输入输出的别名
    if normalized.startswith("/proc/") and normalized.endswith(
        ("/fd/0", "/fd/1", "/fd/2")
    ):
        return True
    return False


# 文件工具应拒绝写入的路径（除非通过终端工具的审批系统）。
# 这些路径前缀在 os.path.realpath 之后进行匹配。
_SENSITIVE_PATH_PREFIXES = (
    "/etc/", "/boot/", "/usr/lib/systemd/",
    "/private/etc/", "/private/var/",
)
_SENSITIVE_EXACT_PATHS = {"/var/run/docker.sock", "/run/docker.sock"}


def _check_sensitive_path(filepath: str) -> str | None:
    """如果路径指向敏感系统位置，返回错误信息。"""
    try:
        resolved = os.path.realpath(os.path.expanduser(filepath))
    except (OSError, ValueError):
        resolved = filepath
    normalized = os.path.normpath(os.path.expanduser(filepath))
    _err = (
        f"Refusing to write to sensitive system path: {filepath}\n"
        "Use the terminal tool with sudo if you need to modify system files."
    )
    for prefix in _SENSITIVE_PATH_PREFIXES:
        if resolved.startswith(prefix) or normalized.startswith(prefix):
            return _err
    if resolved in _SENSITIVE_EXACT_PATHS or normalized in _SENSITIVE_EXACT_PATHS:
        return _err
    return None


def _is_expected_write_exception(exc: Exception) -> bool:
    """对于预期中的写入拒绝异常返回 True，这些异常不应记录到错误日志。"""
    if isinstance(exc, PermissionError):
        return True
    if isinstance(exc, OSError) and exc.errno in _EXPECTED_WRITE_ERRNOS:
        return True
    return False


_file_ops_lock = threading.Lock()
_file_ops_cache: dict = {}

# 按任务跟踪已读取文件，用于检测重复读取循环和去重。
# 每个 task_id 存储：
#   "last_key":     最近一次读取/搜索调用的键（或 None）
#   "consecutive":  该完全相同的调用连续重复了多少次
#   "read_history": (path, offset, limit) 元组集合，用于 get_read_files_summary
#   "dedup":        字典映射 (resolved_path, offset, limit) → mtime 浮点数
#                   用于跳过未修改文件的重复读取。上下文压缩时重置
#                   （原始内容已被摘要掉，模型需要重新获取完整内容）。
#   "read_timestamps": 字典映射 resolved_path → 修改时间浮点数
#                      记录该任务最后一次读取（或写入）文件时的时间。
#                      用于 write_file 和 patch 检测智能体读取与写入之间
#                      的外部更改。成功写入后更新，以避免同一任务的连续
#                      编辑触发误报警告。
_read_tracker_lock = threading.Lock()
_read_tracker: dict = {}


def _get_file_ops(task_id: str = "default") -> ShellFileOperations:
    """获取或创建终端环境的 ShellFileOperations。

    遵循 TERMINAL_ENV 设置——如果 task_id 还没有对应的环境，
    则使用配置的后端（local、docker、modal 等）创建，
    而不是总是默认使用 local。

    线程安全：使用与 terminal_tool 相同的按任务创建锁，
    防止并发工具调用重复创建沙箱。
    """
    from tools.terminal_tool import (
        _active_environments, _env_lock, _create_environment,
        _get_env_config, _last_activity, _start_cleanup_thread,
        _creation_locks,
        _creation_locks_lock,
    )
    import time

    # 快速路径：检查缓存——同时验证底层环境是否仍然存活
    # （可能已被清理线程终止）。
    with _file_ops_lock:
        cached = _file_ops_cache.get(task_id)
    if cached is not None:
        with _env_lock:
            if task_id in _active_environments:
                _last_activity[task_id] = time.time()
                return cached
            else:
                # 环境已被清理——使过期的缓存条目失效
                with _file_ops_lock:
                    _file_ops_cache.pop(task_id, None)

    # 需要确保环境存在后再构建 file_ops。
    # 获取按任务的锁，以确保只有一个线程创建沙箱。
    with _creation_locks_lock:
        if task_id not in _creation_locks:
            _creation_locks[task_id] = threading.Lock()
        task_lock = _creation_locks[task_id]

    with task_lock:
        # 双重检查：等待期间另一个线程可能已经创建了环境
        with _env_lock:
            if task_id in _active_environments:
                _last_activity[task_id] = time.time()
                terminal_env = _active_environments[task_id]
            else:
                terminal_env = None

        if terminal_env is None:
            from tools.terminal_tool import _task_env_overrides

            config = _get_env_config()
            env_type = config["env_type"]
            overrides = _task_env_overrides.get(task_id, {})

            if env_type == "docker":
                image = overrides.get("docker_image") or config["docker_image"]
            elif env_type == "singularity":
                image = overrides.get("singularity_image") or config["singularity_image"]
            elif env_type == "modal":
                image = overrides.get("modal_image") or config["modal_image"]
            elif env_type == "daytona":
                image = overrides.get("daytona_image") or config["daytona_image"]
            else:
                image = ""

            cwd = overrides.get("cwd") or config["cwd"]
            logger.info("Creating new %s environment for task %s...", env_type, task_id[:8])

            container_config = None
            if env_type in ("docker", "singularity", "modal", "daytona"):
                container_config = {
                    "container_cpu": config.get("container_cpu", 1),
                    "container_memory": config.get("container_memory", 5120),
                    "container_disk": config.get("container_disk", 51200),
                    "container_persistent": config.get("container_persistent", True),
                    "docker_volumes": config.get("docker_volumes", []),
                }

            ssh_config = None
            if env_type == "ssh":
                ssh_config = {
                    "host": config.get("ssh_host", ""),
                    "user": config.get("ssh_user", ""),
                    "port": config.get("ssh_port", 22),
                    "key": config.get("ssh_key", ""),
                    "persistent": config.get("ssh_persistent", False),
                }

            local_config = None
            if env_type == "local":
                local_config = {
                    "persistent": config.get("local_persistent", False),
                }

            terminal_env = _create_environment(
                env_type=env_type,
                image=image,
                cwd=cwd,
                timeout=config["timeout"],
                ssh_config=ssh_config,
                container_config=container_config,
                local_config=local_config,
                task_id=task_id,
                host_cwd=config.get("host_cwd"),
            )

            with _env_lock:
                _active_environments[task_id] = terminal_env
                _last_activity[task_id] = time.time()

            _start_cleanup_thread()
            logger.info("%s environment ready for task %s", env_type, task_id[:8])

    # 从（保证存活的）环境构建 file_ops 并缓存
    file_ops = ShellFileOperations(terminal_env)
    with _file_ops_lock:
        _file_ops_cache[task_id] = file_ops
    return file_ops


def clear_file_ops_cache(task_id: str = None):
    """清除文件操作缓存。"""
    with _file_ops_lock:
        if task_id:
            _file_ops_cache.pop(task_id, None)
        else:
            _file_ops_cache.clear()


def read_file_tool(path: str, offset: int = 1, limit: int = 500, task_id: str = "default") -> str:
    """分页读取文件并附带行号。"""
    try:
        # ── 设备路径防护 ─────────────────────────────────────────
        # 阻止会导致进程挂起的路径（无限输出、阻塞输入）。
        # 纯路径检查——无 I/O。
        if _is_blocked_device(path):
            return json.dumps({
                "error": (
                    f"Cannot read '{path}': this is a device file that would "
                    "block or produce infinite output."
                ),
            })

        _resolved = Path(path).expanduser().resolve()

        # ── 二进制文件防护 ─────────────────────────────────────────
        # 通过扩展名阻止二进制文件（无 I/O）。
        if has_binary_extension(str(_resolved)):
            _ext = _resolved.suffix.lower()
            return json.dumps({
                "error": (
                    f"Cannot read binary file '{path}' ({_ext}). "
                    "Use vision_analyze for images, or terminal to inspect binary files."
                ),
            })

        # ── Hermes 内部路径防护 ────────────────────────────────
        # 防止通过目录或 hub 元数据文件进行提示注入。
        from hermes_constants import get_hermes_home as _get_hh
        _hermes_home = _get_hh().resolve()
        _blocked_dirs = [
            _hermes_home / "skills" / ".hub" / "index-cache",
            _hermes_home / "skills" / ".hub",
        ]
        for _blocked in _blocked_dirs:
            try:
                _resolved.relative_to(_blocked)
                return json.dumps({
                    "error": (
                        f"Access denied: {path} is an internal Hermes cache file "
                        "and cannot be read directly to prevent prompt injection. "
                        "Use the skills_list or skill_view tools instead."
                    )
                })
            except ValueError:
                pass

        # ── 去重检查 ───────────────────────────────────────────────
        # 如果已经读取过完全相同的 (path, offset, limit) 且文件自那以后
        # 未被修改，则返回轻量级的占位结果而非重新发送相同内容。
        # 节省上下文 token。
        resolved_str = str(_resolved)
        dedup_key = (resolved_str, offset, limit)
        with _read_tracker_lock:
            task_data = _read_tracker.setdefault(task_id, {
                "last_key": None, "consecutive": 0,
                "read_history": set(), "dedup": {},
            })
            cached_mtime = task_data.get("dedup", {}).get(dedup_key)

        if cached_mtime is not None:
            try:
                current_mtime = os.path.getmtime(resolved_str)
                if current_mtime == cached_mtime:
                    return json.dumps({
                        "content": (
                            "File unchanged since last read. The content from "
                            "the earlier read_file result in this conversation is "
                            "still current — refer to that instead of re-reading."
                        ),
                        "path": path,
                        "dedup": True,
                    }, ensure_ascii=False)
            except OSError:
                pass  # stat 失败——继续执行完整读取

        # ── 执行读取 ──────────────────────────────────────────
        file_ops = _get_file_ops(task_id)
        result = file_ops.read_file(path, offset, limit)
        result_dict = result.to_dict()

        # ── 字符数限制防护 ─────────────────────────────────────
        # 由于与模型无关，我们无法计算 token；字符数是最佳近似值。
        # 如果读取产生了不合理的大量内容，拒绝并告知模型缩小范围。
        # 注意：检查的是格式化内容（含行号前缀），而非原始文件大小，
        # 因为进入上下文的是格式化内容。
        # 在脱敏之前检查，以避免对大量内容执行昂贵的正则匹配。
        content_len = len(result.content or "")
        file_size = result_dict.get("file_size", 0)
        max_chars = _get_max_read_chars()
        if content_len > max_chars:
            total_lines = result_dict.get("total_lines", "unknown")
            return json.dumps({
                "error": (
                    f"Read produced {content_len:,} characters which exceeds "
                    f"the safety limit ({max_chars:,} chars). "
                    "Use offset and limit to read a smaller range. "
                    f"The file has {total_lines} lines total."
                ),
                "path": path,
                "total_lines": total_lines,
                "file_size": file_size,
            }, ensure_ascii=False)

        # ── 脱敏处理（在大小检查之后，跳过超大内容）──
        if result.content:
            result.content = redact_sensitive_text(result.content)
            result_dict["content"] = result.content

        # 大文件提示：如果文件很大且调用方未请求较小窗口，
        # 引导使用定向读取。
        if (file_size and file_size > _LARGE_FILE_HINT_BYTES
                and limit > 200
                and result_dict.get("truncated")):
            result_dict.setdefault("_hint", (
                f"This file is large ({file_size:,} bytes). "
                "Consider reading only the section you need with offset and limit "
                "to keep context usage efficient."
            ))

        # ── 连续循环检测跟踪 ──────────────────────
        read_key = ("read", path, offset, limit)
        with _read_tracker_lock:
            # 确保 "dedup" 键存在（向后兼容旧的跟踪器状态）
            if "dedup" not in task_data:
                task_data["dedup"] = {}
            task_data["read_history"].add((path, offset, limit))
            if task_data["last_key"] == read_key:
                task_data["consecutive"] += 1
            else:
                task_data["last_key"] = read_key
                task_data["consecutive"] = 1
            count = task_data["consecutive"]

            # 在读取时存储 mtime 有两个目的：
            # 1. 去重：跳过未修改文件的相同重复读取。
            # 2. 过期检测：如果文件在智能体上次读取后发生了变化
            #    （外部编辑、并发智能体等），在写入/补丁时发出警告。
            try:
                _mtime_now = os.path.getmtime(resolved_str)
                task_data["dedup"][dedup_key] = _mtime_now
                task_data.setdefault("read_timestamps", {})[resolved_str] = _mtime_now
            except OSError:
                pass  # 无法 stat——跳过此条目的跟踪

        if count >= 4:
            # 硬性阻止：停止返回内容以打破循环
            return json.dumps({
                "error": (
                    f"BLOCKED: You have read this exact file region {count} times in a row. "
                    "The content has NOT changed. You already have this information. "
                    "STOP re-reading and proceed with your task."
                ),
                "path": path,
                "already_read": count,
            }, ensure_ascii=False)
        elif count >= 3:
            result_dict["_warning"] = (
                f"You have read this exact file region {count} times consecutively. "
                "The content has not changed since your last read. Use the information you already have. "
                "If you are stuck in a loop, stop reading and proceed with writing or responding."
            )

        return json.dumps(result_dict, ensure_ascii=False)
    except Exception as e:
        return tool_error(str(e))




def reset_file_dedup(task_id: str = None):
    """清除文件读取的去重缓存。

    在上下文压缩后调用——原始读取内容已被摘要掉，因此如果模型再次
    读取相同文件，需要获取完整内容。如果没有此操作，压缩后的读取
    会返回"文件未更改"的占位结果，指向上下文中已不存在的内容。

    传入 task_id 仅清除该任务的缓存，不传则清除所有任务的缓存。
    """
    with _read_tracker_lock:
        if task_id:
            task_data = _read_tracker.get(task_id)
            if task_data and "dedup" in task_data:
                task_data["dedup"].clear()
        else:
            for task_data in _read_tracker.values():
                if "dedup" in task_data:
                    task_data["dedup"].clear()


def notify_other_tool_call(task_id: str = "default"):
    """重置任务的连续读取/搜索计数器。

    当执行 read_file / search_files 以外的工具时，由工具分发器
    （model_tools.py）调用。这确保我们只在*真正连续*的重复读取时
    发出警告或阻止——如果智能体在中间执行了其他操作（写入、补丁、
    终端等），计数器会重置，下次读取被视为新的。
    """
    with _read_tracker_lock:
        task_data = _read_tracker.get(task_id)
        if task_data:
            task_data["last_key"] = None
            task_data["consecutive"] = 0


def _update_read_timestamp(filepath: str, task_id: str) -> None:
    """在成功写入后记录文件的当前修改时间。

    在 write_file 和 patch 之后调用，以确保同一任务的连续编辑
    不会触发误报的过期警告——每次写入都会刷新存储的时间戳，
    使其与文件的新状态一致。
    """
    try:
        resolved = str(Path(filepath).expanduser().resolve())
        current_mtime = os.path.getmtime(resolved)
    except (OSError, ValueError):
        return
    with _read_tracker_lock:
        task_data = _read_tracker.get(task_id)
        if task_data is not None:
            task_data.setdefault("read_timestamps", {})[resolved] = current_mtime


def _check_file_staleness(filepath: str, task_id: str) -> str | None:
    """检查文件自智能体上次读取后是否被修改。

    如果文件已过期（自该任务上次 read_file 调用后 mtime 发生变化），
    返回警告字符串；如果文件是最新的或从未被读取过，返回 None。
    不会阻止操作——写入仍然继续执行。
    """
    try:
        resolved = str(Path(filepath).expanduser().resolve())
    except (OSError, ValueError):
        return None
    with _read_tracker_lock:
        task_data = _read_tracker.get(task_id)
        if not task_data:
            return None
        read_mtime = task_data.get("read_timestamps", {}).get(resolved)
    if read_mtime is None:
        return None  # 文件从未被读取——没有可比较的对象
    try:
        current_mtime = os.path.getmtime(resolved)
    except OSError:
        return None  # 无法 stat——文件可能已被删除，交由写入操作处理
    if current_mtime != read_mtime:
        return (
            f"Warning: {filepath} was modified since you last read it "
            "(external edit or concurrent agent). The content you read may be "
            "stale. Consider re-reading the file to verify before writing."
        )
    return None


def write_file_tool(path: str, content: str, task_id: str = "default") -> str:
    """将内容写入文件。"""
    sensitive_err = _check_sensitive_path(path)
    if sensitive_err:
        return tool_error(sensitive_err)
    try:
        stale_warning = _check_file_staleness(path, task_id)
        file_ops = _get_file_ops(task_id)
        result = file_ops.write_file(path, content)
        result_dict = result.to_dict()
        if stale_warning:
            result_dict["_warning"] = stale_warning
        # 刷新存储的时间戳，避免同一任务的连续写入触发误报的过期警告。
        _update_read_timestamp(path, task_id)
        return json.dumps(result_dict, ensure_ascii=False)
    except Exception as e:
        if _is_expected_write_exception(e):
            logger.debug("write_file expected denial: %s: %s", type(e).__name__, e)
        else:
            logger.error("write_file error: %s: %s", type(e).__name__, e, exc_info=True)
        return tool_error(str(e))


def patch_tool(mode: str = "replace", path: str = None, old_string: str = None,
               new_string: str = None, replace_all: bool = False, patch: str = None,
               task_id: str = "default") -> str:
    """使用替换模式或 V4A 补丁格式修补文件。"""
    # 对 replace（显式路径）和 V4A patch（提取路径）两种模式都检查敏感路径
    _paths_to_check = []
    if path:
        _paths_to_check.append(path)
    if mode == "patch" and patch:
        import re as _re
        for _m in _re.finditer(r'^\*\*\*\s+(?:Update|Add|Delete)\s+File:\s*(.+)$', patch, _re.MULTILINE):
            _paths_to_check.append(_m.group(1).strip())
    for _p in _paths_to_check:
        sensitive_err = _check_sensitive_path(_p)
        if sensitive_err:
            return tool_error(sensitive_err)
    try:
        # 检查此补丁将涉及的所有文件的过期状态。
        stale_warnings = []
        for _p in _paths_to_check:
            _sw = _check_file_staleness(_p, task_id)
            if _sw:
                stale_warnings.append(_sw)

        file_ops = _get_file_ops(task_id)
        
        if mode == "replace":
            if not path:
                return tool_error("path required")
            if old_string is None or new_string is None:
                return tool_error("old_string and new_string required")
            result = file_ops.patch_replace(path, old_string, new_string, replace_all)
        elif mode == "patch":
            if not patch:
                return tool_error("patch content required")
            result = file_ops.patch_v4a(patch)
        else:
            return tool_error(f"Unknown mode: {mode}")
        
        result_dict = result.to_dict()
        if stale_warnings:
            result_dict["_warning"] = stale_warnings[0] if len(stale_warnings) == 1 else " | ".join(stale_warnings)
        # 刷新所有成功补丁路径的时间戳，避免同一任务的连续编辑触发误报警告。
        if not result_dict.get("error"):
            for _p in _paths_to_check:
                _update_read_timestamp(_p, task_id)
        result_json = json.dumps(result_dict, ensure_ascii=False)
        # 当 old_string 未找到时给出提示——避免智能体使用过期内容重试，
        # 而不是重新读取文件。
        if result_dict.get("error") and "Could not find" in str(result_dict["error"]):
            result_json += "\n\n[Hint: old_string not found. Use read_file to verify the current content, or search_files to locate the text.]"
        return result_json
    except Exception as e:
        return tool_error(str(e))


def search_tool(pattern: str, target: str = "content", path: str = ".",
                file_glob: str = None, limit: int = 50, offset: int = 0,
                output_mode: str = "content", context: int = 0,
                task_id: str = "default") -> str:
    """搜索内容或文件。"""
    try:
        # 跟踪搜索以检测*连续*的重复搜索循环。
        # 包含分页参数，以便用户可以翻页查看截断的结果，
        # 而不会触发重复搜索防护。
        search_key = (
            "search",
            pattern,
            target,
            str(path),
            file_glob or "",
            limit,
            offset,
        )
        with _read_tracker_lock:
            task_data = _read_tracker.setdefault(task_id, {
                "last_key": None, "consecutive": 0, "read_history": set(),
            })
            if task_data["last_key"] == search_key:
                task_data["consecutive"] += 1
            else:
                task_data["last_key"] = search_key
                task_data["consecutive"] = 1
            count = task_data["consecutive"]

        if count >= 4:
            return json.dumps({
                "error": (
                    f"BLOCKED: You have run this exact search {count} times in a row. "
                    "The results have NOT changed. You already have this information. "
                    "STOP re-searching and proceed with your task."
                ),
                "pattern": pattern,
                "already_searched": count,
            }, ensure_ascii=False)

        file_ops = _get_file_ops(task_id)
        result = file_ops.search(
            pattern=pattern, path=path, target=target, file_glob=file_glob,
            limit=limit, offset=offset, output_mode=output_mode, context=context
        )
        if hasattr(result, 'matches'):
            for m in result.matches:
                if hasattr(m, 'content') and m.content:
                    m.content = redact_sensitive_text(m.content)
        result_dict = result.to_dict()

        if count >= 3:
            result_dict["_warning"] = (
                f"You have run this exact search {count} times consecutively. "
                "The results have not changed. Use the information you already have."
            )

        result_json = json.dumps(result_dict, ensure_ascii=False)
        # 当结果被截断时给出提示——明确的下一个 offset 比依赖模型
        # 从 total_count 与匹配数推断更清晰。
        if result_dict.get("truncated"):
            next_offset = offset + limit
            result_json += f"\n\n[Hint: Results truncated. Use offset={next_offset} to see more, or narrow with a more specific pattern or file_glob.]"
        return result_json
    except Exception as e:
        return tool_error(str(e))




# ---------------------------------------------------------------------------
# Schema 定义与注册
# ---------------------------------------------------------------------------
from tools.registry import registry, tool_error


def _check_file_reqs():
    """延迟包装器，避免与 tools/__init__.py 的循环导入。"""
    from tools import check_file_requirements
    return check_file_requirements()

READ_FILE_SCHEMA = {
    "name": "read_file",
    "description": "Read a text file with line numbers and pagination. Use this instead of cat/head/tail in terminal. Output format: 'LINE_NUM|CONTENT'. Suggests similar filenames if not found. Use offset and limit for large files. Reads exceeding ~100K characters are rejected; use offset and limit to read specific sections of large files. NOTE: Cannot read images or binary files — use vision_analyze for images.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to the file to read (absolute, relative, or ~/path)"},
            "offset": {"type": "integer", "description": "Line number to start reading from (1-indexed, default: 1)", "default": 1, "minimum": 1},
            "limit": {"type": "integer", "description": "Maximum number of lines to read (default: 500, max: 2000)", "default": 500, "maximum": 2000}
        },
        "required": ["path"]
    }
}

WRITE_FILE_SCHEMA = {
    "name": "write_file",
    "description": "Write content to a file, completely replacing existing content. Use this instead of echo/cat heredoc in terminal. Creates parent directories automatically. OVERWRITES the entire file — use 'patch' for targeted edits.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to the file to write (will be created if it doesn't exist, overwritten if it does)"},
            "content": {"type": "string", "description": "Complete content to write to the file"}
        },
        "required": ["path", "content"]
    }
}

PATCH_SCHEMA = {
    "name": "patch",
    "description": "Targeted find-and-replace edits in files. Use this instead of sed/awk in terminal. Uses fuzzy matching (9 strategies) so minor whitespace/indentation differences won't break it. Returns a unified diff. Auto-runs syntax checks after editing.\n\nReplace mode (default): find a unique string and replace it.\nPatch mode: apply V4A multi-file patches for bulk changes.",
    "parameters": {
        "type": "object",
        "properties": {
            "mode": {"type": "string", "enum": ["replace", "patch"], "description": "Edit mode: 'replace' for targeted find-and-replace, 'patch' for V4A multi-file patches", "default": "replace"},
            "path": {"type": "string", "description": "File path to edit (required for 'replace' mode)"},
            "old_string": {"type": "string", "description": "Text to find in the file (required for 'replace' mode). Must be unique in the file unless replace_all=true. Include enough surrounding context to ensure uniqueness."},
            "new_string": {"type": "string", "description": "Replacement text (required for 'replace' mode). Can be empty string to delete the matched text."},
            "replace_all": {"type": "boolean", "description": "Replace all occurrences instead of requiring a unique match (default: false)", "default": False},
            "patch": {"type": "string", "description": "V4A format patch content (required for 'patch' mode). Format:\n*** Begin Patch\n*** Update File: path/to/file\n@@ context hint @@\n context line\n-removed line\n+added line\n*** End Patch"}
        },
        "required": ["mode"]
    }
}

SEARCH_FILES_SCHEMA = {
    "name": "search_files",
    "description": "Search file contents or find files by name. Use this instead of grep/rg/find/ls in terminal. Ripgrep-backed, faster than shell equivalents.\n\nContent search (target='content'): Regex search inside files. Output modes: full matches with line numbers, file paths only, or match counts.\n\nFile search (target='files'): Find files by glob pattern (e.g., '*.py', '*config*'). Also use this instead of ls — results sorted by modification time.",
    "parameters": {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Regex pattern for content search, or glob pattern (e.g., '*.py') for file search"},
            "target": {"type": "string", "enum": ["content", "files"], "description": "'content' searches inside file contents, 'files' searches for files by name", "default": "content"},
            "path": {"type": "string", "description": "Directory or file to search in (default: current working directory)", "default": "."},
            "file_glob": {"type": "string", "description": "Filter files by pattern in grep mode (e.g., '*.py' to only search Python files)"},
            "limit": {"type": "integer", "description": "Maximum number of results to return (default: 50)", "default": 50},
            "offset": {"type": "integer", "description": "Skip first N results for pagination (default: 0)", "default": 0},
            "output_mode": {"type": "string", "enum": ["content", "files_only", "count"], "description": "Output format for grep mode: 'content' shows matching lines with line numbers, 'files_only' lists file paths, 'count' shows match counts per file", "default": "content"},
            "context": {"type": "integer", "description": "Number of context lines before and after each match (grep mode only)", "default": 0}
        },
        "required": ["pattern"]
    }
}


def _handle_read_file(args, **kw):
    tid = kw.get("task_id") or "default"
    return read_file_tool(path=args.get("path", ""), offset=args.get("offset", 1), limit=args.get("limit", 500), task_id=tid)


def _handle_write_file(args, **kw):
    tid = kw.get("task_id") or "default"
    return write_file_tool(path=args.get("path", ""), content=args.get("content", ""), task_id=tid)


def _handle_patch(args, **kw):
    tid = kw.get("task_id") or "default"
    return patch_tool(
        mode=args.get("mode", "replace"), path=args.get("path"),
        old_string=args.get("old_string"), new_string=args.get("new_string"),
        replace_all=args.get("replace_all", False), patch=args.get("patch"), task_id=tid)


def _handle_search_files(args, **kw):
    tid = kw.get("task_id") or "default"
    target_map = {"grep": "content", "find": "files"}
    raw_target = args.get("target", "content")
    target = target_map.get(raw_target, raw_target)
    return search_tool(
        pattern=args.get("pattern", ""), target=target, path=args.get("path", "."),
        file_glob=args.get("file_glob"), limit=args.get("limit", 50), offset=args.get("offset", 0),
        output_mode=args.get("output_mode", "content"), context=args.get("context", 0), task_id=tid)


registry.register(name="read_file", toolset="file", schema=READ_FILE_SCHEMA, handler=_handle_read_file, check_fn=_check_file_reqs, emoji="📖", max_result_size_chars=float('inf'))
registry.register(name="write_file", toolset="file", schema=WRITE_FILE_SCHEMA, handler=_handle_write_file, check_fn=_check_file_reqs, emoji="✍️", max_result_size_chars=100_000)
registry.register(name="patch", toolset="file", schema=PATCH_SCHEMA, handler=_handle_patch, check_fn=_check_file_reqs, emoji="🔧", max_result_size_chars=100_000)
registry.register(name="search_files", toolset="file", schema=SEARCH_FILES_SCHEMA, handler=_handle_search_files, check_fn=_check_file_reqs, emoji="🔎", max_result_size_chars=100_000)
