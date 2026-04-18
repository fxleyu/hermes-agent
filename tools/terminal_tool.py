#!/usr/bin/env python3
"""
终端工具模块

一个在本地、Docker、Modal、SSH、Singularity 和 Daytona 环境中执行命令的终端工具。
支持本地执行、容器化后端和 Modal 云沙箱，包括托管网关模式。

环境选择（通过 TERMINAL_ENV 环境变量）：
- "local": 直接在主机上执行（默认，最快）
- "docker": 在 Docker 容器中执行（隔离，需要 Docker）
- "modal": 在 Modal 云沙箱中执行（直接 Modal 或托管网关）

功能特性：
- 多种执行后端（本地、docker、modal）
- 后台任务支持
- 虚拟机/容器生命周期管理
- 不活跃后自动清理

云沙箱说明：
- 持久文件系统在沙箱重建时保留工作状态
- 持久文件系统不保证相同的活跃沙箱或长时间运行的进程在清理、空闲回收或 Hermes 退出后存活

用法：
    from terminal_tool import terminal_tool

    # 执行简单命令
    result = terminal_tool("ls -la")

    # 后台执行
    result = terminal_tool("python server.py", background=True)
"""

import importlib.util
import json
import logging
import os
import platform
import re
import time
import threading
import atexit
import shutil
import subprocess
from pathlib import Path
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 全局中断事件：当用户中断到达时由智能体设置。
# 终端工具在命令执行期间轮询此事件，以便能立即终止
# 长时间运行的子进程，而不是阻塞直到超时。
# ---------------------------------------------------------------------------
from tools.interrupt import is_interrupted, _interrupt_event  # noqa: F401 — re-exported
# display_hermes_home 在调用点延迟导入（hermes 更新期间的过时模块安全）




# =============================================================================
# 自定义 Singularity 环境（更大空间）
# =============================================================================

# Singularity 辅助函数（scratch 目录、SIF 缓存）现已移至 tools/environments/singularity.py
from tools.environments.singularity import _get_scratch_dir
from tools.tool_backend_helpers import (
    coerce_modal_mode,
    has_direct_modal_credentials,
    managed_nous_tools_enabled,
    resolve_modal_backend_state,
)


# 前台超时硬上限；可通过 TERMINAL_MAX_FOREGROUND_TIMEOUT 环境变量覆盖。
FOREGROUND_MAX_TIMEOUT = int(os.getenv("TERMINAL_MAX_FOREGROUND_TIMEOUT", "600"))

# 磁盘使用量警告阈值（GB）
DISK_USAGE_WARNING_THRESHOLD_GB = float(os.getenv("TERMINAL_DISK_WARNING_GB", "500"))


def _check_disk_usage_warning():
    """检查总磁盘使用量是否超过警告阈值。"""
    try:
        scratch_dir = _get_scratch_dir()

        # 获取 hermes 目录的总大小
        total_bytes = 0
        import glob
        for path in glob.glob(str(scratch_dir / "hermes-*")):
            for f in Path(path).rglob('*'):
                if f.is_file():
                    try:
                        total_bytes += f.stat().st_size
                    except OSError as e:
                        logger.debug("Could not stat file %s: %s", f, e)
        
        total_gb = total_bytes / (1024 ** 3)
        
        if total_gb > DISK_USAGE_WARNING_THRESHOLD_GB:
            logger.warning("Disk usage (%.1fGB) exceeds threshold (%.0fGB). Consider running cleanup_all_environments().",
                           total_gb, DISK_USAGE_WARNING_THRESHOLD_GB)
            return True
        
        return False
    except Exception as e:
        logger.debug("Disk usage warning check failed: %s", e, exc_info=True)
        return False


# 会话缓存的 sudo 密码（持续到 CLI 退出）
_cached_sudo_password: str = ""

# 可选的 UI 回调，用于交互式提示。设置后，这些回调将替代
# 默认的 /dev/tty 或 input() 读取器。CLI 注册这些回调，
# 使提示通过 prompt_toolkit 的事件循环路由。
#   _sudo_password_callback() -> str  (返回密码或 "" 跳过)
#   _approval_callback(command, description) -> str  ("once"/"session"/"always"/"deny")
_sudo_password_callback = None
_approval_callback = None


def set_sudo_password_callback(cb):
    """注册 sudo 密码提示的回调函数（由 CLI 使用）。"""
    global _sudo_password_callback
    _sudo_password_callback = cb


def set_approval_callback(cb):
    """注册危险命令审批提示的回调函数（由 CLI 使用）。"""
    global _approval_callback
    _approval_callback = cb

# =============================================================================
# 危险命令审批系统
# =============================================================================

# 危险命令检测 + 审批已合并到 tools/approval.py
from tools.approval import (
    check_all_command_guards as _check_all_guards_impl,
)


def _check_all_guards(command: str, env_type: str) -> dict:
    """委托给合并后的守卫（tirith + 危险命令检测），带 CLI 回调。"""
    return _check_all_guards_impl(command, env_type,
                                  approval_callback=_approval_callback)


# 白名单：可以合法出现在目录路径中的字符。
# 涵盖字母数字、路径分隔符、Windows 驱动器/UNC 分隔符、波浪号、
# 点、连字符、下划线、空格、加号、@、等号和逗号。
# 其他字符一律拒绝。
_WORKDIR_SAFE_RE = re.compile(r'^[A-Za-z0-9/\\:_\-.~ +@=,]+$')


def _validate_workdir(workdir: str) -> str | None:
    """拒绝不像文件系统路径的 workdir 值。

    使用允许的字符白名单而非黑名单，这样新的 shell 元字符
    就无法绕过检测。

    如果安全返回 None，如果危险返回错误消息字符串。
    """
    if not workdir:
        return None
    if not _WORKDIR_SAFE_RE.match(workdir):
        # 找到第一个违规字符，提供有用的错误信息。
        for ch in workdir:
            if not _WORKDIR_SAFE_RE.match(ch):
                return (
                    f"Blocked: workdir contains disallowed character {repr(ch)}. "
                    "Use a simple filesystem path without shell metacharacters."
                )
        return "Blocked: workdir contains disallowed characters."
    return None


def _handle_sudo_failure(output: str, env_type: str) -> str:
    """
    检查 sudo 失败并为消息上下文添加有用的提示信息。

    如果在消息上下文中 sudo 失败，返回增强后的输出，否则返回原始输出。
    """
    is_gateway = os.getenv("HERMES_GATEWAY_SESSION")
    
    if not is_gateway:
        return output
    
    # 检查 sudo 失败的标志
    sudo_failures = [
        "sudo: a password is required",
        "sudo: no tty present",
        "sudo: a terminal is required",
    ]
    
    for failure in sudo_failures:
        if failure in output:
            from hermes_constants import display_hermes_home as _dhh
            return output + f"\n\n💡 Tip: To enable sudo over messaging, add SUDO_PASSWORD to {_dhh()}/.env on the agent machine."
    
    return output


def _prompt_for_sudo_password(timeout_seconds: int = 45) -> str:
    """
    带超时的 sudo 密码提示。

    返回输入的密码，或在以下情况返回空字符串：
    - 用户直接按回车（跳过）
    - 超时到期（默认 45 秒）
    - 发生任何错误

    仅在交互模式（HERMES_INTERACTIVE=1）下有效。
    如果已注册 _sudo_password_callback（由 CLI 注册），则委托给它，
    使提示与 prompt_toolkit 的 UI 集成。否则直接从
    /dev/tty 读取，禁用回显。
    """
    import sys
    import time as time_module
    
    # 有回调注册时使用回调（兼容 prompt_toolkit）
    if _sudo_password_callback is not None:
        try:
            return _sudo_password_callback() or ""
        except Exception:
            return ""

    result = {"password": None, "done": False}
    
    def read_password_thread():
        """禁用回显读取密码。Windows 上使用 msvcrt，Unix 上使用 /dev/tty。"""
        tty_fd = None
        old_attrs = None
        try:
            if platform.system() == "Windows":
                import msvcrt
                chars = []
                while True:
                    c = msvcrt.getwch()
                    if c in ("\r", "\n"):
                        break
                    if c == "\x03":
                        raise KeyboardInterrupt
                    chars.append(c)
                result["password"] = "".join(chars)
            else:
                import termios
                tty_fd = os.open("/dev/tty", os.O_RDONLY)
                old_attrs = termios.tcgetattr(tty_fd)
                new_attrs = termios.tcgetattr(tty_fd)
                new_attrs[3] = new_attrs[3] & ~termios.ECHO
                termios.tcsetattr(tty_fd, termios.TCSAFLUSH, new_attrs)
                chars = []
                while True:
                    b = os.read(tty_fd, 1)
                    if not b or b in (b"\n", b"\r"):
                        break
                    chars.append(b)
                result["password"] = b"".join(chars).decode("utf-8", errors="replace")
        except (EOFError, KeyboardInterrupt, OSError):
            result["password"] = ""
        except Exception:
            result["password"] = ""
        finally:
            if tty_fd is not None and old_attrs is not None:
                try:
                    import termios as _termios
                    _termios.tcsetattr(tty_fd, _termios.TCSAFLUSH, old_attrs)
                except Exception as e:
                    logger.debug("Failed to restore terminal attributes: %s", e)
            if tty_fd is not None:
                try:
                    os.close(tty_fd)
                except Exception as e:
                    logger.debug("Failed to close tty fd: %s", e)
            result["done"] = True
    
    try:
        os.environ["HERMES_SPINNER_PAUSE"] = "1"
        time_module.sleep(0.2)
        
        print()
        print("┌" + "─" * 58 + "┐")
        print("│  🔐 SUDO PASSWORD REQUIRED" + " " * 30 + "│")
        print("├" + "─" * 58 + "┤")
        print("│  Enter password below (input is hidden), or:            │")
        print("│    • Press Enter to skip (command fails gracefully)     │")
        print(f"│    • Wait {timeout_seconds}s to auto-skip" + " " * 27 + "│")
        print("└" + "─" * 58 + "┘")
        print()
        print("  Password (hidden): ", end="", flush=True)
        
        password_thread = threading.Thread(target=read_password_thread, daemon=True)
        password_thread.start()
        password_thread.join(timeout=timeout_seconds)
        
        if result["done"]:
            password = result["password"] or ""
            print()  # newline after hidden input
            if password:
                print("  ✓ Password received (cached for this session)")
            else:
                print("  ⏭ Skipped - continuing without sudo")
            print()
            sys.stdout.flush()
            return password
        else:
            print("\n  ⏱ Timeout - continuing without sudo")
            print("    (Press Enter to dismiss)")
            print()
            sys.stdout.flush()
            return ""
            
    except (EOFError, KeyboardInterrupt):
        print()
        print("  ⏭ Cancelled - continuing without sudo")
        print()
        sys.stdout.flush()
        return ""
    except Exception as e:
        print(f"\n  [sudo prompt error: {e}] - continuing without sudo\n")
        sys.stdout.flush()
        return ""
    finally:
        if "HERMES_SPINNER_PAUSE" in os.environ:
            del os.environ["HERMES_SPINNER_PAUSE"]

def _safe_command_preview(command: Any, limit: int = 200) -> str:
    """返回可能无效的命令值的日志安全预览。"""
    if command is None:
        return "<None>"
    if isinstance(command, str):
        return command[:limit]
    try:
        return repr(command)[:limit]
    except Exception:
        return f"<{type(command).__name__}>"

def _looks_like_env_assignment(token: str) -> bool:
    """当 *token* 是前导 shell 环境变量赋值时返回 True。"""
    if "=" not in token or token.startswith("="):
        return False
    name, _value = token.split("=", 1)
    return bool(re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name))


def _read_shell_token(command: str, start: int) -> tuple[str, int]:
    """从 *start* 位置开始读取一个 shell token，保留引号和转义。"""
    i = start
    n = len(command)

    while i < n:
        ch = command[i]
        if ch.isspace() or ch in ";|&()":
            break
        if ch == "'":
            i += 1
            while i < n and command[i] != "'":
                i += 1
            if i < n:
                i += 1
            continue
        if ch == '"':
            i += 1
            while i < n:
                inner = command[i]
                if inner == "\\" and i + 1 < n:
                    i += 2
                    continue
                if inner == '"':
                    i += 1
                    break
                i += 1
            continue
        if ch == "\\" and i + 1 < n:
            i += 2
            continue
        i += 1

    return command[start:i], i


def _rewrite_real_sudo_invocations(command: str) -> tuple[str, bool]:
    """仅重写真正未加引号的 sudo 命令词，而非纯文本中的 sudo 提及。"""
    out: list[str] = []
    i = 0
    n = len(command)
    command_start = True
    found = False

    while i < n:
        ch = command[i]

        if ch.isspace():
            out.append(ch)
            if ch == "\n":
                command_start = True
            i += 1
            continue

        if ch == "#" and command_start:
            comment_end = command.find("\n", i)
            if comment_end == -1:
                out.append(command[i:])
                break
            out.append(command[i:comment_end])
            i = comment_end
            continue

        if command.startswith("&&", i) or command.startswith("||", i) or command.startswith(";;", i):
            out.append(command[i:i + 2])
            i += 2
            command_start = True
            continue

        if ch in ";|&(":
            out.append(ch)
            i += 1
            command_start = True
            continue

        if ch == ")":
            out.append(ch)
            i += 1
            command_start = False
            continue

        token, next_i = _read_shell_token(command, i)
        if command_start and token == "sudo":
            out.append("sudo -S -p ''")
            found = True
        else:
            out.append(token)

        if command_start and _looks_like_env_assignment(token):
            command_start = True
        else:
            command_start = False
        i = next_i

    return "".join(out), found


def _transform_sudo_command(command: str | None) -> tuple[str | None, str | None]:
    """
    在 SUDO_PASSWORD 可用时将 sudo 命令转换为使用 -S 标志。

    这是一个共享辅助函数，被所有执行环境使用，以在本地、SSH
    和容器环境中提供一致的 sudo 处理。

    返回值：
        (transformed_command, sudo_stdin)，其中：
        - transformed_command 将每个裸 ``sudo`` 替换为
          ``sudo -S -p ''``，使 sudo 从 stdin 读取密码。
        - sudo_stdin 是带有尾随换行符的密码字符串，调用者必须
          将其预置到进程的 stdin 流中。sudo -S 精确读取一行
          （密码），并将 stdin 的其余部分传递给子命令，因此即使
          调用者也有自己的 stdin_data 要传输，预置也是安全的。
        - 如果没有可用密码，sudo_stdin 为 None，命令原样返回，
          以便它优雅地失败并显示 "sudo: a password is required"。

    直接驱动子进程的调用者（local、ssh、docker、singularity）应将
    sudo_stdin 预置到其 stdin_data 中，并将合并后的字节传递给
    Popen 的 stdin 管道。

    无法通过管道传输子进程 stdin 的调用者（modal、daytona）必须自己
    将密码嵌入命令字符串中；参见它们的 execute() 方法了解如何处理
    非 None 的 sudo_stdin 情况。

    如果 SUDO_PASSWORD 未设置且处于交互模式（HERMES_INTERACTIVE=1）：
      提示用户输入密码，超时 45 秒，为会话缓存。

    如果 SUDO_PASSWORD 未设置且不处于交互模式：
      命令原样运行（优雅地失败并显示 "sudo: a password is required"）。
    """
    global _cached_sudo_password

    if command is None:
        return None, None
    transformed, has_real_sudo = _rewrite_real_sudo_invocations(command)
    if not has_real_sudo:
        return command, None

    has_configured_password = "SUDO_PASSWORD" in os.environ
    sudo_password = os.environ.get("SUDO_PASSWORD", "") if has_configured_password else _cached_sudo_password

    if not has_configured_password and not sudo_password and os.getenv("HERMES_INTERACTIVE"):
        sudo_password = _prompt_for_sudo_password(timeout_seconds=45)
        if sudo_password:
            _cached_sudo_password = sudo_password

    if has_configured_password or sudo_password:
        # 尾随换行符是必须的：sudo -S 读取一行作为密码。
        return transformed, sudo_password + "\n"

    return command, None


# 环境类现已移至 tools/environments/
from tools.environments.local import LocalEnvironment as _LocalEnvironment
from tools.environments.singularity import SingularityEnvironment as _SingularityEnvironment
from tools.environments.ssh import SSHEnvironment as _SSHEnvironment
from tools.environments.docker import DockerEnvironment as _DockerEnvironment
from tools.environments.modal import ModalEnvironment as _ModalEnvironment
from tools.environments.managed_modal import ManagedModalEnvironment as _ManagedModalEnvironment
from tools.managed_tool_gateway import is_managed_tool_gateway_ready


# 面向 LLM 的工具描述
TERMINAL_TOOL_DESCRIPTION = """Execute shell commands on a Linux environment. Filesystem usually persists between calls.

Do NOT use cat/head/tail to read files — use read_file instead.
Do NOT use grep/rg/find to search — use search_files instead.
Do NOT use ls to list directories — use search_files(target='files') instead.
Do NOT use sed/awk to edit files — use patch instead.
Do NOT use echo/cat heredoc to create files — use write_file instead.
Reserve terminal for: builds, installs, git, processes, scripts, network, package managers, and anything that needs a shell.

Foreground (default): Commands return INSTANTLY when done, even if the timeout is high. Set timeout=300 for long builds/scripts — you'll still get the result in seconds if it's fast. Prefer foreground for short commands.
Background: Set background=true to get a session_id. Two patterns:
  (1) Long-lived processes that never exit (servers, watchers).
  (2) Long-running tasks with notify_on_complete=true — you can keep working on other things and the system auto-notifies you when the task finishes. Great for test suites, builds, deployments, or anything that takes more than a minute.
Use process(action="poll") for progress checks, process(action="wait") to block until done.
Working directory: Use 'workdir' for per-command cwd.
PTY mode: Set pty=true for interactive CLI tools (Codex, Claude Code, Python REPL).

Do NOT use vim/nano/interactive tools without pty=true — they hang without a pseudo-terminal. Pipe git output to cat if it might page.
"""

# 环境生命周期管理的全局状态
_active_environments: Dict[str, Any] = {}
_last_activity: Dict[str, float] = {}
_env_lock = threading.Lock()
_creation_locks: Dict[str, threading.Lock] = {}  # Per-task locks for sandbox creation
_creation_locks_lock = threading.Lock()  # Protects _creation_locks dict itself
_cleanup_thread = None
_cleanup_running = False

# 每任务环境覆盖注册表。
# 允许环境（如 TerminalBench2Env）指定自定义的 Docker/Modal
# image for a specific task_id BEFORE the agent loop starts. When the terminal or
# file tools create a new sandbox for that task_id, they check this registry first
# and fall back to the TERMINAL_MODAL_IMAGE (etc.) env var if no override is set.
#
# 这永远不会暴露给模型 -- 只有基础设施代码会调用它。
# 线程安全，因为每个 task_id 在每次 rollout 中是唯一的。
_task_env_overrides: Dict[str, Dict[str, Any]] = {}


def register_task_env_overrides(task_id: str, overrides: Dict[str, Any]):
    """
    注册特定任务/rollout 的环境覆盖。

    由 Atropos 环境在智能体循环启动前调用，以配置
    每任务的沙箱设置（例如 Modal 镜像的自定义 Dockerfile）。

    支持的覆盖键：
        - modal_image: str -- Dockerfile 路径或 Docker Hub 镜像名
        - docker_image: str -- Docker 镜像名
        - cwd: str -- 沙箱内的工作目录

    Args:
        task_id: rollout 的唯一任务标识符
        overrides: 要覆盖的配置键字典
    """
    _task_env_overrides[task_id] = overrides


def clear_task_env_overrides(task_id: str):
    """
    清除任务的环境覆盖（rollout 完成后调用）。

    在清理期间调用，以避免过期条目累积。
    """
    _task_env_overrides.pop(task_id, None)

# 来自环境变量的配置

def _parse_env_var(name: str, default: str, converter=int, type_label: str = "integer"):
    """解析带有 *converter* 的环境变量，对无效值给出清晰的错误信息。

    没有这个包装器，一个格式错误的环境变量（例如 TERMINAL_TIMEOUT=5m）
    会导致未处理的 ValueError，从而导致所有终端命令失败。
    """
    raw = os.getenv(name, default)
    try:
        return converter(raw)
    except (ValueError, json.JSONDecodeError):
        raise ValueError(
            f"Invalid value for {name}: {raw!r} (expected {type_label}). "
            f"Check ~/.hermes/.env or environment variables."
        )


def _get_env_config() -> Dict[str, Any]:
    """从环境变量获取终端环境配置。"""
    # 兼容性最好的默认镜像，包含 Python 和 Node.js
    default_image = "nikolaik/python-nodejs:python3.11-nodejs20"
    env_type = os.getenv("TERMINAL_ENV", "local")
    
    mount_docker_cwd = os.getenv("TERMINAL_DOCKER_MOUNT_CWD_TO_WORKSPACE", "false").lower() in ("true", "1", "yes")

    # 默认工作目录：local 使用主机当前目录，其他后端
    # 默认在用户家目录（~ 解析为容器/远程中运行的任何账户）。
    if env_type == "local":
        default_cwd = os.getcwd()
    elif env_type == "ssh":
        default_cwd = "~"
    else:
        default_cwd = "/root"

    # 读取 TERMINAL_CWD 但对容器后端进行合理性检查。
    # 如果显式启用了 Docker cwd 透传，将主机路径重映射到
    # /workspace 并单独跟踪原始主机路径。否则保持
    # 正常沙箱行为并丢弃主机路径。
    cwd = os.getenv("TERMINAL_CWD", default_cwd)
    host_cwd = None
    host_prefixes = ("/Users/", "/home/", "C:\\", "C:/")
    if env_type == "docker" and mount_docker_cwd:
        docker_cwd_source = os.getenv("TERMINAL_CWD") or os.getcwd()
        candidate = os.path.abspath(os.path.expanduser(docker_cwd_source))
        if (
            any(candidate.startswith(p) for p in host_prefixes)
            or (os.path.isabs(candidate) and os.path.isdir(candidate) and not candidate.startswith(("/workspace", "/root")))
        ):
            host_cwd = candidate
            cwd = "/workspace"
    elif env_type in ("modal", "docker", "singularity", "daytona") and cwd:
        # 主机路径和相对路径在容器内不起作用
        is_host_path = any(cwd.startswith(p) for p in host_prefixes)
        is_relative = not os.path.isabs(cwd)  # e.g. "." or "src/"
        if (is_host_path or is_relative) and cwd != default_cwd:
            logger.info("Ignoring TERMINAL_CWD=%r for %s backend "
                        "(host/relative path won't work in sandbox). Using %r instead.",
                        cwd, env_type, default_cwd)
            cwd = default_cwd

    return {
        "env_type": env_type,
        "modal_mode": coerce_modal_mode(os.getenv("TERMINAL_MODAL_MODE", "auto")),
        "docker_image": os.getenv("TERMINAL_DOCKER_IMAGE", default_image),
        "docker_forward_env": _parse_env_var("TERMINAL_DOCKER_FORWARD_ENV", "[]", json.loads, "valid JSON"),
        "singularity_image": os.getenv("TERMINAL_SINGULARITY_IMAGE", f"docker://{default_image}"),
        "modal_image": os.getenv("TERMINAL_MODAL_IMAGE", default_image),
        "daytona_image": os.getenv("TERMINAL_DAYTONA_IMAGE", default_image),
        "cwd": cwd,
        "host_cwd": host_cwd,
        "docker_mount_cwd_to_workspace": mount_docker_cwd,
        "timeout": _parse_env_var("TERMINAL_TIMEOUT", "180"),
        "lifetime_seconds": _parse_env_var("TERMINAL_LIFETIME_SECONDS", "300"),
        # SSH 特定配置
        "ssh_host": os.getenv("TERMINAL_SSH_HOST", ""),
        "ssh_user": os.getenv("TERMINAL_SSH_USER", ""),
        "ssh_port": _parse_env_var("TERMINAL_SSH_PORT", "22"),
        "ssh_key": os.getenv("TERMINAL_SSH_KEY", ""),
        # 持久化 shell：SSH 默认使用配置级 persistent_shell
        # 设置（非本地后端默认为 true）；本地始终需要显式启用。
        # 每个后端的环境变量在显式设置时覆盖此项。
        "ssh_persistent": os.getenv(
            "TERMINAL_SSH_PERSISTENT",
            os.getenv("TERMINAL_PERSISTENT_SHELL", "true"),
        ).lower() in ("true", "1", "yes"),
        "local_persistent": os.getenv("TERMINAL_LOCAL_PERSISTENT", "false").lower() in ("true", "1", "yes"),
        # 容器资源配置（适用于 docker、singularity、modal、daytona -- 本地/ssh 忽略）
        "container_cpu": _parse_env_var("TERMINAL_CONTAINER_CPU", "1", float, "number"),
        "container_memory": _parse_env_var("TERMINAL_CONTAINER_MEMORY", "5120"),     # MB (default 5GB)
        "container_disk": _parse_env_var("TERMINAL_CONTAINER_DISK", "51200"),        # MB (default 50GB)
        "container_persistent": os.getenv("TERMINAL_CONTAINER_PERSISTENT", "true").lower() in ("true", "1", "yes"),
        "docker_volumes": _parse_env_var("TERMINAL_DOCKER_VOLUMES", "[]", json.loads, "valid JSON"),
    }


def _get_modal_backend_state(modal_mode: object | None) -> Dict[str, Any]:
    """解析直连 vs 托管 Modal 后端选择。"""
    return resolve_modal_backend_state(
        modal_mode,
        has_direct=has_direct_modal_credentials(),
        managed_ready=is_managed_tool_gateway_ready("modal"),
    )


def _create_environment(env_type: str, image: str, cwd: str, timeout: int,
                        ssh_config: dict = None, container_config: dict = None,
                        local_config: dict = None,
                        task_id: str = "default",
                        host_cwd: str = None):
    """
    创建用于沙箱命令执行的执行环境。

    Args:
        env_type: "local"、"docker"、"singularity"、"modal"、"daytona"、"ssh" 之一
        image: Docker/Singularity/Modal 镜像名称（本地/ssh 忽略）
        cwd: 工作目录
        timeout: 默认命令超时时间
        ssh_config: SSH 连接配置（用于 env_type="ssh"）
        container_config: 容器后端的资源配置（cpu、memory、disk、persistent）
        task_id: 用于环境复用和快照键控的任务标识符
        host_cwd: 显式启用时绑定到 Docker 的可选主机工作目录

    Returns:
        带有 execute() 方法的环境实例
    """
    cc = container_config or {}
    cpu = cc.get("container_cpu", 1)
    memory = cc.get("container_memory", 5120)
    disk = cc.get("container_disk", 51200)
    persistent = cc.get("container_persistent", True)
    volumes = cc.get("docker_volumes", [])
    docker_forward_env = cc.get("docker_forward_env", [])
    docker_env = cc.get("docker_env", {})

    if env_type == "local":
        return _LocalEnvironment(cwd=cwd, timeout=timeout)
    
    elif env_type == "docker":
        return _DockerEnvironment(
            image=image, cwd=cwd, timeout=timeout,
            cpu=cpu, memory=memory, disk=disk,
            persistent_filesystem=persistent, task_id=task_id,
            volumes=volumes,
            host_cwd=host_cwd,
            auto_mount_cwd=cc.get("docker_mount_cwd_to_workspace", False),
            forward_env=docker_forward_env,
            env=docker_env,
        )
    
    elif env_type == "singularity":
        return _SingularityEnvironment(
            image=image, cwd=cwd, timeout=timeout,
            cpu=cpu, memory=memory, disk=disk,
            persistent_filesystem=persistent, task_id=task_id,
        )
    
    elif env_type == "modal":
        sandbox_kwargs = {}
        if cpu > 0:
            sandbox_kwargs["cpu"] = cpu
        if memory > 0:
            sandbox_kwargs["memory"] = memory
        if disk > 0:
            try:
                import inspect, modal
                if "ephemeral_disk" in inspect.signature(modal.Sandbox.create).parameters:
                    sandbox_kwargs["ephemeral_disk"] = disk
            except Exception:
                pass

        modal_state = _get_modal_backend_state(cc.get("modal_mode"))

        if modal_state["selected_backend"] == "managed":
            return _ManagedModalEnvironment(
                image=image, cwd=cwd, timeout=timeout,
                modal_sandbox_kwargs=sandbox_kwargs,
                persistent_filesystem=persistent, task_id=task_id,
            )

        if modal_state["selected_backend"] != "direct":
            if modal_state["managed_mode_blocked"]:
                raise ValueError(
                    "Modal backend is configured for managed mode, but "
                    "a paid Nous subscription is required for the Tool Gateway and no direct "
                    "Modal credentials/config were found. Log in with `hermes model` or "
                    "choose TERMINAL_MODAL_MODE=direct/auto."
                )
            if modal_state["mode"] == "managed":
                raise ValueError(
                    "Modal backend is configured for managed mode, but the managed tool gateway is unavailable."
                )
            if modal_state["mode"] == "direct":
                raise ValueError(
                    "Modal backend is configured for direct mode, but no direct Modal credentials/config were found."
                )
            message = "Modal backend selected but no direct Modal credentials/config was found."
            if managed_nous_tools_enabled():
                message = (
                    "Modal backend selected but no direct Modal credentials/config or managed tool gateway was found."
                )
            raise ValueError(message)

        return _ModalEnvironment(
            image=image, cwd=cwd, timeout=timeout,
            modal_sandbox_kwargs=sandbox_kwargs,
            persistent_filesystem=persistent, task_id=task_id,
        )
    
    elif env_type == "daytona":
        # 延迟导入，仅在选择了 daytona 后端时才需要 daytona SDK。
        from tools.environments.daytona import DaytonaEnvironment as _DaytonaEnvironment
        return _DaytonaEnvironment(
            image=image, cwd=cwd, timeout=timeout,
            cpu=int(cpu), memory=memory, disk=disk,
            persistent_filesystem=persistent, task_id=task_id,
        )

    elif env_type == "ssh":
        if not ssh_config or not ssh_config.get("host") or not ssh_config.get("user"):
            raise ValueError("SSH environment requires ssh_host and ssh_user to be configured")
        return _SSHEnvironment(
            host=ssh_config["host"],
            user=ssh_config["user"],
            port=ssh_config.get("port", 22),
            key_path=ssh_config.get("key", ""),
            cwd=cwd,
            timeout=timeout,
        )

    else:
        raise ValueError(f"Unknown environment type: {env_type}. Use 'local', 'docker', 'singularity', 'modal', 'daytona', or 'ssh'")


def _cleanup_inactive_envs(lifetime_seconds: int = 300):
    """清理超过 lifetime_seconds 不活跃的环境。"""
    current_time = time.time()

    # 检查进程注册表 -- 跳过有活跃后台进程的沙箱的清理
    # （它们的 _last_activity 会被刷新以保持存活）。
    try:
        from tools.process_registry import process_registry
        for task_id in list(_last_activity.keys()):
            if process_registry.has_active_processes(task_id):
                _last_activity[task_id] = current_time  # Keep sandbox alive
    except ImportError:
        pass

    # 阶段 1：收集过期条目并在持有锁时从跟踪字典中移除。
    # 不要在锁内调用 env.cleanup() -- Modal 和 Docker 的
    # 拆除可能阻塞 10-15 秒，这会使所有等待 _env_lock 的
    # 并发终端/文件工具调用停滞。
    envs_to_stop = []  # (task_id, env) 对的列表

    with _env_lock:
        for task_id, last_time in list(_last_activity.items()):
            if current_time - last_time > lifetime_seconds:
                env = _active_environments.pop(task_id, None)
                _last_activity.pop(task_id, None)
                if env is not None:
                    envs_to_stop.append((task_id, env))

        # 同时清除已清理任务的每任务创建锁
        with _creation_locks_lock:
            for task_id, _ in envs_to_stop:
                _creation_locks.pop(task_id, None)

    # 阶段 2：在锁外停止实际沙箱，这样其他工具调用
    # 不会在 Modal/Docker 沙箱关闭时被阻塞。
    for task_id, env in envs_to_stop:
        # 使过期的 file_ops 缓存条目失效（Bug 修复：防止
        # ShellFileOperations 引用已死亡的沙箱）
        try:
            from tools.file_tools import clear_file_ops_cache
            clear_file_ops_cache(task_id)
        except ImportError:
            pass

        try:
            if hasattr(env, 'cleanup'):
                env.cleanup()
            elif hasattr(env, 'stop'):
                env.stop()
            elif hasattr(env, 'terminate'):
                env.terminate()

            logger.info("Cleaned up inactive environment for task: %s", task_id)

        except Exception as e:
            error_str = str(e)
            if "404" in error_str or "not found" in error_str.lower():
                logger.info("Environment for task %s already cleaned up", task_id)
            else:
                logger.warning("Error cleaning up environment for task %s: %s", task_id, e)


def _cleanup_thread_worker():
    """定期清理不活跃环境的后台线程工作函数。"""
    while _cleanup_running:
        try:
            config = _get_env_config()
            _cleanup_inactive_envs(config["lifetime_seconds"])
        except Exception as e:
            logger.warning("Error in cleanup thread: %s", e, exc_info=True)

        for _ in range(60):
            if not _cleanup_running:
                break
            time.sleep(1)


def _start_cleanup_thread():
    """如果后台清理线程尚未运行，则启动它。"""
    global _cleanup_thread, _cleanup_running

    with _env_lock:
        if _cleanup_thread is None or not _cleanup_thread.is_alive():
            _cleanup_running = True
            _cleanup_thread = threading.Thread(target=_cleanup_thread_worker, daemon=True)
            _cleanup_thread.start()


def _stop_cleanup_thread():
    """停止后台清理线程。"""
    global _cleanup_running
    _cleanup_running = False
    if _cleanup_thread is not None:
        try:
            _cleanup_thread.join(timeout=5)
        except (SystemExit, KeyboardInterrupt):
            pass


def get_active_env(task_id: str):
    """返回 *task_id* 对应的活跃 BaseEnvironment，如果没有则返回 None。"""
    with _env_lock:
        return _active_environments.get(task_id)


def is_persistent_env(task_id: str) -> bool:
    """如果 task_id 对应的活跃环境配置了跨轮次持久化
    （``persistent_filesystem=True``），则返回 True。

    由智能体循环使用，以跳过持久化后端的每轮拆除 -- 这些后端的
    整个意义就是在轮次之间存活（docker 的 ``container_persistent``、
    daytona、modal 等）。非持久化后端（如 Morph）仍然在轮次结束时
    被拆除以防止泄漏。空闲回收器（``_cleanup_inactive_envs``）在
    持久化环境超过 ``terminal.lifetime_seconds`` 后处理它们。
    """
    env = get_active_env(task_id)
    if env is None:
        return False
    return bool(getattr(env, "_persistent", False))




def cleanup_all_environments():
    """清理所有活跃环境。谨慎使用。"""
    task_ids = list(_active_environments.keys())
    cleaned = 0
    
    for task_id in task_ids:
        try:
            cleanup_vm(task_id)
            cleaned += 1
        except Exception as e:
            logger.error("Error cleaning %s: %s", task_id, e, exc_info=True)
    
    # 同时清理所有孤立目录
    scratch_dir = _get_scratch_dir()
    import glob
    for path in glob.glob(str(scratch_dir / "hermes-*")):
        try:
            shutil.rmtree(path, ignore_errors=True)
            logger.info("Removed orphaned: %s", path)
        except OSError as e:
            logger.debug("Failed to remove orphaned path %s: %s", path, e)
    
    if cleaned > 0:
        logger.info("Cleaned %d environments", cleaned)
    return cleaned


def cleanup_vm(task_id: str):
    """按 task_id 手动清理指定环境。"""
    # 在持有锁时从跟踪字典中移除，但将实际的（可能较慢的）
    # env.cleanup() 调用推迟到锁外执行，这样其他工具调用不会被阻塞。
    env = None
    with _env_lock:
        env = _active_environments.pop(task_id, None)
        _last_activity.pop(task_id, None)

    # 清除每任务创建锁
    with _creation_locks_lock:
        _creation_locks.pop(task_id, None)

    # 使过期的 file_ops 缓存条目失效
    try:
        from tools.file_tools import clear_file_ops_cache
        clear_file_ops_cache(task_id)
    except ImportError:
        pass

    if env is None:
        return

    try:
        if hasattr(env, 'cleanup'):
            env.cleanup()
        elif hasattr(env, 'stop'):
            env.stop()
        elif hasattr(env, 'terminate'):
            env.terminate()

        logger.info("Manually cleaned up environment for task: %s", task_id)

    except Exception as e:
        error_str = str(e)
        if "404" in error_str or "not found" in error_str.lower():
            logger.info("Environment for task %s already cleaned up", task_id)
        else:
            logger.warning("Error cleaning up environment for task %s: %s", task_id, e)


def _atexit_cleanup():
    """停止清理线程并在退出时关闭所有剩余沙箱。"""
    _stop_cleanup_thread()
    if _active_environments:
        count = len(_active_environments)
        logger.info("Shutting down %d remaining sandbox(es)...", count)
        cleanup_all_environments()

atexit.register(_atexit_cleanup)


# =============================================================================
# 常见 CLI 工具的退出码上下文
# =============================================================================
# 许多 Unix 命令使用非零退出码作为信息用途，而非表示故障。
# 模型看到 `grep` 的原始 exit_code=1 后会浪费一轮去排查，
# 实际上这只是意味着"没有匹配项"。
# 这个查找表添加了人类可读的说明，以便智能体可以继续。

def _interpret_exit_code(command: str, exit_code: int) -> str | None:
    """当非零退出码并非错误时返回人类可读的说明。

    当退出码为 0 或确实表示错误时返回 None。
    该说明会附加到工具结果中，这样模型就不会浪费
    轮次去排查预期的退出码。
    """
    if exit_code == 0:
        return None

    # 提取管道/链中最后一个命令 -- 它决定退出码。
    # 处理 `cmd1 && cmd2`、`cmd1 | cmd2`、`cmd1; cmd2`。
    # 故意简化：按 shell 操作符分割并取最后一段。
    segments = re.split(r'\s*(?:\|\||&&|[|;])\s*', command)
    last_segment = (segments[-1] if segments else command).strip()

    # 获取基本命令名称（第一个词），跳过环境变量赋值
    # 如 VAR=val cmd ...
    words = last_segment.split()
    base_cmd = ""
    for w in words:
        if "=" in w and not w.startswith("-"):
            continue  # 跳过 VAR=val
        base_cmd = w.split("/")[-1]  # 处理 /usr/bin/grep -> grep
        break

    if not base_cmd:
        return None

    # 各命令的语义
    semantics: dict[str, dict[int, str]] = {
        # grep/rg/ag/ack: 1=未找到匹配（正常），2+=真正的错误
        "grep":  {1: "No matches found (not an error)"},
        "egrep": {1: "No matches found (not an error)"},
        "fgrep": {1: "No matches found (not an error)"},
        "rg":    {1: "No matches found (not an error)"},
        "ag":    {1: "No matches found (not an error)"},
        "ack":   {1: "No matches found (not an error)"},
        # diff: 1=文件不同（预期），2+=真正的错误
        "diff":  {1: "Files differ (expected, not an error)"},
        "colordiff": {1: "Files differ (expected, not an error)"},
        # find: 1=部分目录不可访问但结果可能仍然有效
        "find":  {1: "Some directories were inaccessible (partial results may still be valid)"},
        # test/[: 1=条件为假（预期）
        "test":  {1: "Condition evaluated to false (expected, not an error)"},
        "[":     {1: "Condition evaluated to false (expected, not an error)"},
        # curl: 常见的非错误退出码
        "curl":  {
            6: "Could not resolve host",
            7: "Failed to connect to host",
            22: "HTTP response code indicated error (e.g. 404, 500)",
            28: "Operation timed out",
        },
        # git: 1 依赖于上下文，但通常是正常的（例如 git diff 有变更时返回 1）
        "git":   {1: "Non-zero exit (often normal — e.g. 'git diff' returns 1 when files differ)"},
    }

    cmd_semantics = semantics.get(base_cmd)
    if cmd_semantics and exit_code in cmd_semantics:
        return cmd_semantics[exit_code]

    return None


def _command_requires_pipe_stdin(command: str) -> bool:
    """当 PTY 模式会破坏依赖 stdin 的命令时返回 True。

    某些 CLI 在 stdin 是 TTY 时行为不同。特别是，
    `gh auth login --with-token` 期望 token 通过管道 stdin 传入并
    等待 EOF；当我们在 PTY 下启动它时，`process.submit()` 只发送一个
    换行符，所以命令看起来会永远挂起而没有任何可见进度。
    """
    normalized = " ".join(command.lower().split())
    return (
        normalized.startswith("gh auth login")
        and "--with-token" in normalized
    )


def terminal_tool(
    command: str,
    background: bool = False,
    timeout: Optional[int] = None,
    task_id: Optional[str] = None,
    force: bool = False,
    workdir: Optional[str] = None,
    pty: bool = False,
    notify_on_complete: bool = False,
    watch_patterns: Optional[List[str]] = None,
) -> str:
    """
    在配置的终端环境中执行命令。

    Args:
        command: 要执行的命令
        background: 是否在后台运行（默认：False）
        timeout: 命令超时秒数（默认：来自配置）
        task_id: 用于环境隔离的唯一标识符（可选）
        force: 如果为 True，跳过危险命令检查（用户确认后使用）
        workdir: 此命令的工作目录（可选，未设置时使用会话 cwd）
        pty: 如果为 True，使用伪终端模式运行交互式 CLI 工具（仅限本地后端）
        notify_on_complete: 如果为 True 且 background=True，进程退出时自动通知智能体
        watch_patterns: 在后台输出中监视的字符串列表；匹配时触发通知

    Returns:
        str: 包含 output、exit_code 和 error 字段的 JSON 字符串

    Examples:
        # 执行简单命令
        >>> result = terminal_tool(command="ls -la /tmp")

        # 运行后台任务
        >>> result = terminal_tool(command="python server.py", background=True)

        # 自定义超时
        >>> result = terminal_tool(command="long_task.sh", timeout=300)

        # 用户确认后强制运行
        # 注意：force 参数仅供内部使用，不暴露给模型 API
    """
    try:
        if not isinstance(command, str):
            logger.warning(
                "Rejected invalid terminal command value: %s",
                type(command).__name__,
            )
            return json.dumps({
                "output": "",
                "exit_code": -1,
                "error": f"Invalid command: expected string, got {type(command).__name__}",
                "status": "error",
            }, ensure_ascii=False)

        # 获取配置
        config = _get_env_config()
        env_type = config["env_type"]

        # 使用 task_id 进行环境隔离
        effective_task_id = task_id or "default"

        # 检查每任务覆盖（由 TerminalBench2Env 等环境设置），
        # 然后回退到全局环境变量配置
        overrides = _task_env_overrides.get(effective_task_id, {})
        
        # 根据环境类型选择镜像，支持每任务覆盖
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
        default_timeout = config["timeout"]
        effective_timeout = timeout or default_timeout

        # 拒绝模型显式请求超过 FOREGROUND_MAX_TIMEOUT 超时的前台命令
        # —— 引导它使用后台模式。
        if not background and timeout and timeout > FOREGROUND_MAX_TIMEOUT:
            return json.dumps({
                "error": (
                    f"Foreground timeout {timeout}s exceeds the maximum of "
                    f"{FOREGROUND_MAX_TIMEOUT}s. Use background=true with "
                    f"notify_on_complete=true for long-running commands."
                ),
            }, ensure_ascii=False)

        # 启动清理线程
        _start_cleanup_thread()

        # 获取或创建环境。
        # 使用每任务创建锁，这样同一 task_id 的并发工具调用
        # 会等待第一个完成沙箱创建，而不是各自创建自己的
        # （浪费 Modal 资源）。
        with _env_lock:
            if effective_task_id in _active_environments:
                _last_activity[effective_task_id] = time.time()
                env = _active_environments[effective_task_id]
                needs_creation = False
            else:
                needs_creation = True

        if needs_creation:
            # 每任务锁：只有一个线程创建沙箱，其他线程等待
            with _creation_locks_lock:
                if effective_task_id not in _creation_locks:
                    _creation_locks[effective_task_id] = threading.Lock()
                task_lock = _creation_locks[effective_task_id]

            with task_lock:
                # 获取每任务锁后再次检查
                with _env_lock:
                    if effective_task_id in _active_environments:
                        _last_activity[effective_task_id] = time.time()
                        env = _active_environments[effective_task_id]
                        needs_creation = False

                if needs_creation:
                    if env_type == "singularity":
                        _check_disk_usage_warning()
                    logger.info("Creating new %s environment for task %s...", env_type, effective_task_id[:8])
                    try:
                        ssh_config = None
                        if env_type == "ssh":
                            ssh_config = {
                                "host": config.get("ssh_host", ""),
                                "user": config.get("ssh_user", ""),
                                "port": config.get("ssh_port", 22),
                                "key": config.get("ssh_key", ""),
                                "persistent": config.get("ssh_persistent", False),
                            }

                        container_config = None
                        if env_type in ("docker", "singularity", "modal", "daytona"):
                            container_config = {
                                "container_cpu": config.get("container_cpu", 1),
                                "container_memory": config.get("container_memory", 5120),
                                "container_disk": config.get("container_disk", 51200),
                                "container_persistent": config.get("container_persistent", True),
                                "modal_mode": config.get("modal_mode", "auto"),
                                "docker_volumes": config.get("docker_volumes", []),
                                "docker_mount_cwd_to_workspace": config.get("docker_mount_cwd_to_workspace", False),
                            }

                        local_config = None
                        if env_type == "local":
                            local_config = {
                                "persistent": config.get("local_persistent", False),
                            }

                        new_env = _create_environment(
                            env_type=env_type,
                            image=image,
                            cwd=cwd,
                            timeout=effective_timeout,
                            ssh_config=ssh_config,
                            container_config=container_config,
                            local_config=local_config,
                            task_id=effective_task_id,
                            host_cwd=config.get("host_cwd"),
                        )
                    except ImportError as e:
                        return json.dumps({
                            "output": "",
                            "exit_code": -1,
                            "error": f"Terminal tool disabled: environment creation failed ({e})",
                            "status": "disabled"
                        }, ensure_ascii=False)

                    with _env_lock:
                        _active_environments[effective_task_id] = new_env
                        _last_activity[effective_task_id] = time.time()
                        env = new_env
                    logger.info("%s environment ready for task %s", env_type, effective_task_id[:8])

        # 执行前安全检查（tirith + 危险命令检测）
        # 如果 force=True 则跳过检查（用户已确认要运行它）
        approval_note = None
        if not force:
            approval = _check_all_guards(command, env_type)
            if not approval["approved"]:
                # 检查是否为 approval_required（网关询问模式）
                if approval.get("status") == "approval_required":
                    return json.dumps({
                        "output": "",
                        "exit_code": -1,
                        "error": approval.get("message", "Waiting for user approval"),
                        "status": "approval_required",
                        "command": approval.get("command", command),
                        "description": approval.get("description", "command flagged"),
                        "pattern_key": approval.get("pattern_key", ""),
                    }, ensure_ascii=False)
                # 命令被阻止
                desc = approval.get("description", "command flagged")
                fallback_msg = (
                    f"Command denied: {desc}. "
                    "Use the approval prompt to allow it, or rephrase the command."
                )
                return json.dumps({
                    "output": "",
                    "exit_code": -1,
                    "error": approval.get("message", fallback_msg),
                    "status": "blocked"
                }, ensure_ascii=False)
            # 跟踪用户是否明确批准了该命令
            if approval.get("user_approved"):
                desc = approval.get("description", "flagged as dangerous")
                approval_note = f"Command required approval ({desc}) and was approved by the user."
            elif approval.get("smart_approved"):
                desc = approval.get("description", "flagged as dangerous")
                approval_note = f"Command was flagged ({desc}) and auto-approved by smart approval."

        # 验证 workdir 以防 shell 注入
        if workdir:
            workdir_error = _validate_workdir(workdir)
            if workdir_error:
                logger.warning("Blocked dangerous workdir: %s (command: %s)",
                               workdir[:200], _safe_command_preview(command))
                return json.dumps({
                    "output": "",
                    "exit_code": -1,
                    "error": workdir_error,
                    "status": "blocked"
                }, ensure_ascii=False)

        # 准备命令执行
        pty_disabled_reason = None
        effective_pty = pty
        if pty and _command_requires_pipe_stdin(command):
            effective_pty = False
            pty_disabled_reason = (
                "PTY disabled for this command because it expects piped stdin/EOF "
                "(for example gh auth login --with-token). For local background "
                "processes, call process(action='close') after writing so it receives "
                "EOF."
            )

        if background:
            # 通过进程注册表生成一个被跟踪的后台进程。
            # 对于本地后端：使用 subprocess.Popen 带输出缓冲。
            # 对于非本地后端：通过 env.execute() 在沙箱内运行。
            from tools.approval import get_current_session_key
            from tools.process_registry import process_registry

            session_key = get_current_session_key(default="")
            effective_cwd = workdir or cwd
            try:
                if env_type == "local":
                    proc_session = process_registry.spawn_local(
                        command=command,
                        cwd=effective_cwd,
                        task_id=effective_task_id,
                        session_key=session_key,
                        env_vars=env.env if hasattr(env, 'env') else None,
                        use_pty=effective_pty,
                    )
                else:
                    proc_session = process_registry.spawn_via_env(
                        env=env,
                        command=command,
                        cwd=effective_cwd,
                        task_id=effective_task_id,
                        session_key=session_key,
                    )

                result_data = {
                    "output": "Background process started",
                    "session_id": proc_session.id,
                    "pid": proc_session.pid,
                    "exit_code": 0,
                    "error": None,
                }
                if approval_note:
                    result_data["approval"] = approval_note
                if pty_disabled_reason:
                    result_data["pty_note"] = pty_disabled_reason

                # 在会话上填充路由元数据，以便
                # 监视模式和完成通知可以路由回正确的聊天/线程。
                if background and (notify_on_complete or watch_patterns):
                    from gateway.session_context import get_session_env as _gse
                    _gw_platform = _gse("HERMES_SESSION_PLATFORM", "")
                    if _gw_platform:
                        _gw_chat_id = _gse("HERMES_SESSION_CHAT_ID", "")
                        _gw_thread_id = _gse("HERMES_SESSION_THREAD_ID", "")
                        _gw_user_id = _gse("HERMES_SESSION_USER_ID", "")
                        _gw_user_name = _gse("HERMES_SESSION_USER_NAME", "")
                        proc_session.watcher_platform = _gw_platform
                        proc_session.watcher_chat_id = _gw_chat_id
                        proc_session.watcher_user_id = _gw_user_id
                        proc_session.watcher_user_name = _gw_user_name
                        proc_session.watcher_thread_id = _gw_thread_id

                # 标记在完成时通知智能体
                if notify_on_complete and background:
                    proc_session.notify_on_complete = True
                    result_data["notify_on_complete"] = True

                    # 在网关模式下，自动注册快速监视器，以便网关
                    # 检测完成并触发新的智能体轮次。CLI 模式直接
                    # 使用 completion_queue。
                    if proc_session.watcher_platform:
                        proc_session.watcher_interval = 5
                        process_registry.pending_watchers.append({
                            "session_id": proc_session.id,
                            "check_interval": 5,
                            "session_key": session_key,
                            "platform": proc_session.watcher_platform,
                            "chat_id": proc_session.watcher_chat_id,
                            "user_id": proc_session.watcher_user_id,
                            "user_name": proc_session.watcher_user_name,
                            "thread_id": proc_session.watcher_thread_id,
                            "notify_on_complete": True,
                        })

                # 设置用于输出监控的监视模式
                if watch_patterns and background:
                    proc_session.watch_patterns = list(watch_patterns)
                    result_data["watch_patterns"] = proc_session.watch_patterns

                return json.dumps(result_data, ensure_ascii=False)
            except Exception as e:
                return json.dumps({
                    "output": "",
                    "exit_code": -1,
                    "error": f"Failed to start background process: {str(e)}"
                }, ensure_ascii=False)
        else:
            # 使用重试逻辑运行前台命令
            max_retries = 3
            retry_count = 0
            result = None
            
            while retry_count <= max_retries:
                try:
                    execute_kwargs = {"timeout": effective_timeout}
                    if workdir:
                        execute_kwargs["cwd"] = workdir
                    result = env.execute(command, **execute_kwargs)
                except Exception as e:
                    error_str = str(e).lower()
                    if "timeout" in error_str:
                        return json.dumps({
                            "output": "",
                            "exit_code": 124,
                            "error": f"Command timed out after {effective_timeout} seconds"
                        }, ensure_ascii=False)
                    
                    # 在瞬态错误时重试
                    if retry_count < max_retries:
                        retry_count += 1
                        wait_time = 2 ** retry_count
                        logger.warning("Execution error, retrying in %ds (attempt %d/%d) - Command: %s - Error: %s: %s - Task: %s, Backend: %s",
                                       wait_time, retry_count, max_retries, _safe_command_preview(command), type(e).__name__, e, effective_task_id, env_type)
                        time.sleep(wait_time)
                        continue
                    
                    logger.error("Execution failed after %d retries - Command: %s - Error: %s: %s - Task: %s, Backend: %s",
                                 max_retries, _safe_command_preview(command), type(e).__name__, e, effective_task_id, env_type)
                    return json.dumps({
                        "output": "",
                        "exit_code": -1,
                        "error": f"Command execution failed: {type(e).__name__}: {str(e)}"
                    }, ensure_ascii=False)
                
                # 获得结果
                break
            
            # 提取输出
            output = result.get("output", "")
            returncode = result.get("returncode", 0)
            
            # 在消息上下文中为 sudo 失败添加有用提示
            output = _handle_sudo_failure(output, env_type)
            
            # 如果输出过长则截断，保留头部和尾部
            MAX_OUTPUT_CHARS = 50000
            if len(output) > MAX_OUTPUT_CHARS:
                head_chars = int(MAX_OUTPUT_CHARS * 0.4)  # 40% 头部（错误消息通常出现在前面）
                tail_chars = MAX_OUTPUT_CHARS - head_chars  # 60% 尾部（最新/最相关的输出）
                omitted = len(output) - head_chars - tail_chars
                truncated_notice = (
                    f"\n\n... [OUTPUT TRUNCATED - {omitted} chars omitted "
                    f"out of {len(output)} total] ...\n\n"
                )
                output = output[:head_chars] + truncated_notice + output[-tail_chars:]

            # 去除 ANSI 转义序列，使模型永远不会看到终端
            # 格式化 —— 防止它将转义字符复制到文件写入中。
            from tools.ansi_strip import strip_ansi
            output = strip_ansi(output)

            # 从命令输出中脱敏密钥（捕获 env/printenv 泄漏密钥的情况）
            from agent.redact import redact_sensitive_text
            output = redact_sensitive_text(output.strip()) if output else ""

            # 解释非真正错误的非零退出码
            # （例如 grep=1 表示 "没有匹配"，diff=1 表示 "文件不同"）
            exit_note = _interpret_exit_code(command, returncode)

            result_dict = {
                "output": output,
                "exit_code": returncode,
                "error": None,
            }
            if approval_note:
                result_dict["approval"] = approval_note
            if exit_note:
                result_dict["exit_code_meaning"] = exit_note

            return json.dumps(result_dict, ensure_ascii=False)

    except Exception as e:
        import traceback
        tb_str = traceback.format_exc()
        logger.error("terminal_tool exception:\n%s", tb_str)
        return json.dumps({
            "output": "",
            "exit_code": -1,
            "error": f"Failed to execute command: {str(e)}",
            "traceback": tb_str,
            "status": "error"
        }, ensure_ascii=False)


def check_terminal_requirements() -> bool:
    """检查终端工具的所有依赖是否满足。"""
    config = _get_env_config()
    env_type = config["env_type"]

    try:
        if env_type == "local":
            return True

        elif env_type == "docker":
            from tools.environments.docker import find_docker
            docker = find_docker()
            if not docker:
                logger.error("Docker executable not found in PATH or common install locations")
                return False
            result = subprocess.run([docker, "version"], capture_output=True, timeout=5)
            return result.returncode == 0

        elif env_type == "singularity":
            executable = shutil.which("apptainer") or shutil.which("singularity")
            if executable:
                result = subprocess.run([executable, "--version"], capture_output=True, timeout=5)
                return result.returncode == 0
            return False

        elif env_type == "ssh":
            if not config.get("ssh_host") or not config.get("ssh_user"):
                logger.error(
                    "SSH backend selected but TERMINAL_SSH_HOST and TERMINAL_SSH_USER "
                    "are not both set. Configure both or switch TERMINAL_ENV to 'local'."
                )
                return False
            return True

        elif env_type == "modal":
            modal_state = _get_modal_backend_state(config.get("modal_mode"))
            if modal_state["selected_backend"] == "managed":
                return True

            if modal_state["selected_backend"] != "direct":
                if modal_state["managed_mode_blocked"]:
                    logger.error(
                        "Modal backend selected with TERMINAL_MODAL_MODE=managed, but "
                        "a paid Nous subscription is required for the Tool Gateway and no direct "
                        "Modal credentials/config were found. Log in with `hermes model` "
                        "or choose TERMINAL_MODAL_MODE=direct/auto."
                    )
                    return False
                if modal_state["mode"] == "managed":
                    logger.error(
                        "Modal backend selected with TERMINAL_MODAL_MODE=managed, but the managed "
                        "tool gateway is unavailable. Configure the managed gateway or choose "
                        "TERMINAL_MODAL_MODE=direct/auto."
                    )
                    return False
                elif modal_state["mode"] == "direct":
                    if managed_nous_tools_enabled():
                        logger.error(
                            "Modal backend selected with TERMINAL_MODAL_MODE=direct, but no direct "
                            "Modal credentials/config were found. Configure Modal or choose "
                            "TERMINAL_MODAL_MODE=managed/auto."
                        )
                    else:
                        logger.error(
                            "Modal backend selected with TERMINAL_MODAL_MODE=direct, but no direct "
                            "Modal credentials/config were found. Configure Modal or choose "
                            "TERMINAL_MODAL_MODE=auto."
                        )
                    return False
                else:
                    if managed_nous_tools_enabled():
                        logger.error(
                            "Modal backend selected but no direct Modal credentials/config or managed "
                            "tool gateway was found. Configure Modal, set up the managed gateway, "
                            "or choose a different TERMINAL_ENV."
                        )
                    else:
                        logger.error(
                            "Modal backend selected but no direct Modal credentials/config was found. "
                            "Configure Modal or choose a different TERMINAL_ENV."
                        )
                    return False

            if importlib.util.find_spec("modal") is None:
                logger.error("modal is required for direct modal terminal backend: pip install modal")
                return False

            return True

        elif env_type == "daytona":
            from daytona import Daytona  # noqa: F401 — SDK presence check
            return os.getenv("DAYTONA_API_KEY") is not None

        else:
            logger.error(
                "Unknown TERMINAL_ENV '%s'. Use one of: local, docker, singularity, "
                "modal, daytona, ssh.",
                env_type,
            )
            return False
    except Exception as e:
        logger.error("Terminal requirements check failed: %s", e, exc_info=True)
        return False


if __name__ == "__main__":
    # 直接运行时的简单测试
    print("Terminal Tool Module")
    print("=" * 50)
    
    config = _get_env_config()
    print("\nCurrent Configuration:")
    print(f"  Environment type: {config['env_type']}")
    print(f"  Docker image: {config['docker_image']}")
    print(f"  Modal image: {config['modal_image']}")
    print(f"  Working directory: {config['cwd']}")
    print(f"  Default timeout: {config['timeout']}s")
    print(f"  Lifetime: {config['lifetime_seconds']}s")

    if not check_terminal_requirements():
        print("\n❌ Requirements not met. Please check the messages above.")
        exit(1)

    print("\n✅ All requirements met!")
    print("\nAvailable Tool:")
    print("  - terminal_tool: Execute commands in sandboxed environments")

    print("\nUsage Examples:")
    print("  # Execute a command")
    print("  result = terminal_tool(command='ls -la')")
    print("  ")
    print("  # Run a background task")
    print("  result = terminal_tool(command='python server.py', background=True)")

    print("\nEnvironment Variables:")
    default_img = "nikolaik/python-nodejs:python3.11-nodejs20"
    print(f"  TERMINAL_ENV: {os.getenv('TERMINAL_ENV', 'local')} (local/docker/singularity/modal/daytona/ssh)")
    print(f"  TERMINAL_DOCKER_IMAGE: {os.getenv('TERMINAL_DOCKER_IMAGE', default_img)}")
    print(f"  TERMINAL_SINGULARITY_IMAGE: {os.getenv('TERMINAL_SINGULARITY_IMAGE', f'docker://{default_img}')}")
    print(f"  TERMINAL_MODAL_IMAGE: {os.getenv('TERMINAL_MODAL_IMAGE', default_img)}")
    print(f"  TERMINAL_DAYTONA_IMAGE: {os.getenv('TERMINAL_DAYTONA_IMAGE', default_img)}")
    print(f"  TERMINAL_CWD: {os.getenv('TERMINAL_CWD', os.getcwd())}")
    from hermes_constants import display_hermes_home as _dhh
    print(f"  TERMINAL_SANDBOX_DIR: {os.getenv('TERMINAL_SANDBOX_DIR', f'{_dhh()}/sandboxes')}")
    print(f"  TERMINAL_TIMEOUT: {os.getenv('TERMINAL_TIMEOUT', '60')}")
    print(f"  TERMINAL_LIFETIME_SECONDS: {os.getenv('TERMINAL_LIFETIME_SECONDS', '300')}")


# ---------------------------------------------------------------------------
# 注册
# ---------------------------------------------------------------------------
from tools.registry import registry

TERMINAL_SCHEMA = {
    "name": "terminal",
    "description": TERMINAL_TOOL_DESCRIPTION,
    "parameters": {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The command to execute on the VM"
            },
            "background": {
                "type": "boolean",
                "description": "Run the command in the background. Two patterns: (1) Long-lived processes that never exit (servers, watchers). (2) Long-running tasks paired with notify_on_complete=true — you can keep working and get notified when the task finishes. For short commands, prefer foreground with a generous timeout instead.",
                "default": False
            },
            "timeout": {
                "type": "integer",
                "description": f"Max seconds to wait (default: 180, foreground max: {FOREGROUND_MAX_TIMEOUT}). Returns INSTANTLY when command finishes — set high for long tasks, you won't wait unnecessarily. Foreground timeout above {FOREGROUND_MAX_TIMEOUT}s is rejected; use background=true for longer commands.",
                "minimum": 1
            },
            "workdir": {
                "type": "string",
                "description": "Working directory for this command (absolute path). Defaults to the session working directory."
            },
            "pty": {
                "type": "boolean",
                "description": "Run in pseudo-terminal (PTY) mode for interactive CLI tools like Codex, Claude Code, or Python REPL. Only works with local and SSH backends. Default: false.",
                "default": False
            },
            "notify_on_complete": {
                "type": "boolean",
                "description": "When true (and background=true), you'll be automatically notified when the process finishes — no polling needed. Use this for tasks that take a while (tests, builds, deployments) so you can keep working on other things in the meantime.",
                "default": False
            },
            "watch_patterns": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of strings to watch for in background process output. When any pattern matches a line of output, you'll be notified with the matching text — like notify_on_complete but triggers mid-process on specific output. Use for monitoring logs, watching for errors, or waiting for specific events (e.g. [\"ERROR\", \"FAIL\", \"listening on port\"])."
            }
        },
        "required": ["command"]
    }
}


def _handle_terminal(args, **kw):
    return terminal_tool(
        command=args.get("command"),
        background=args.get("background", False),
        timeout=args.get("timeout"),
        task_id=kw.get("task_id"),
        workdir=args.get("workdir"),
        pty=args.get("pty", False),
        notify_on_complete=args.get("notify_on_complete", False),
        watch_patterns=args.get("watch_patterns"),
    )


registry.register(
    name="terminal",
    toolset="terminal",
    schema=TERMINAL_SCHEMA,
    handler=_handle_terminal,
    check_fn=check_terminal_requirements,
    emoji="💻",
    max_result_size_chars=100_000,
)
