#!/usr/bin/env python3
"""
代码执行工具 -- 编程式工具调用 (PTC, Programmatic Tool Calling)

允许 LLM 编写 Python 脚本，通过 RPC 调用 Hermes 工具，
将多步工具链折叠为单次推理轮次。

架构（两种传输方式）：

  **本地后端（UDS，Unix 域套接字）：**
  1. 父进程生成带有 UDS RPC 函数的 `hermes_tools.py` 桩模块
  2. 父进程打开 Unix 域套接字并启动 RPC 监听线程
  3. 父进程生成子进程执行 LLM 的脚本
  4. 工具调用通过 UDS 回传给父进程进行分发

  **远程后端（基于文件的 RPC）：**
  1. 父进程生成带有基于文件 RPC 桩的 `hermes_tools.py`
  2. 父进程将两个文件传送到远程环境
  3. 脚本在终端后端（Docker/SSH/Modal/Daytona 等）内运行
  4. 工具调用写为请求文件；父进程上的轮询线程通过 env.execute()
     读取它们、分发并写入响应文件
  5. 脚本轮询响应文件并继续执行

两种情况下，只有脚本的 stdout 返回给 LLM；中间工具结果不会
进入上下文窗口。

平台：仅限 Linux / macOS（本地使用 Unix 域套接字）。在 Windows 上禁用。
远程执行还需要终端后端中有 Python 3。
"""

import base64
import json
import logging
import os
import platform
import shlex
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid

_IS_WINDOWS = platform.system() == "Windows"
from typing import Any, Dict, List, Optional

# 可用性门控：UDS 需要 POSIX 操作系统
logger = logging.getLogger(__name__)

SANDBOX_AVAILABLE = sys.platform != "win32"

# 沙箱内允许的 7 种工具。此列表与会话已启用工具的交集
# 决定生成哪些桩函数。
SANDBOX_ALLOWED_TOOLS = frozenset([
    "web_search",
    "web_extract",
    "read_file",
    "write_file",
    "search_files",
    "patch",
    "terminal",
])

# 资源限制默认值（可通过 config.yaml → code_execution.* 覆盖）
DEFAULT_TIMEOUT = 300        # 5 分钟
DEFAULT_MAX_TOOL_CALLS = 50
MAX_STDOUT_BYTES = 50_000    # 50 KB
MAX_STDERR_BYTES = 10_000    # 10 KB


def check_sandbox_requirements() -> bool:
    """代码执行沙箱需要 POSIX 操作系统以使用 Unix 域套接字。"""
    return SANDBOX_AVAILABLE


# ---------------------------------------------------------------------------
# hermes_tools.py 代码生成器
# ---------------------------------------------------------------------------

# 每个工具的桩模板：(函数名, 签名, 文档字符串, 参数字典表达式)
# args_dict_expr 构建通过 RPC 套接字发送的 JSON 载荷。
_TOOL_STUBS = {
    "web_search": (
        "web_search",
        "query: str, limit: int = 5",
        '"""Search the web. Returns dict with data.web list of {url, title, description}."""',
        '{"query": query, "limit": limit}',
    ),
    "web_extract": (
        "web_extract",
        "urls: list",
        '"""Extract content from URLs. Returns dict with results list of {url, title, content, error}."""',
        '{"urls": urls}',
    ),
    "read_file": (
        "read_file",
        "path: str, offset: int = 1, limit: int = 500",
        '"""Read a file (1-indexed lines). Returns dict with "content" and "total_lines"."""',
        '{"path": path, "offset": offset, "limit": limit}',
    ),
    "write_file": (
        "write_file",
        "path: str, content: str",
        '"""Write content to a file (always overwrites). Returns dict with status."""',
        '{"path": path, "content": content}',
    ),
    "search_files": (
        "search_files",
        'pattern: str, target: str = "content", path: str = ".", file_glob: str = None, limit: int = 50, offset: int = 0, output_mode: str = "content", context: int = 0',
        '"""Search file contents (target="content") or find files by name (target="files"). Returns dict with "matches"."""',
        '{"pattern": pattern, "target": target, "path": path, "file_glob": file_glob, "limit": limit, "offset": offset, "output_mode": output_mode, "context": context}',
    ),
    "patch": (
        "patch",
        'path: str = None, old_string: str = None, new_string: str = None, replace_all: bool = False, mode: str = "replace", patch: str = None',
        '"""Targeted find-and-replace (mode="replace") or V4A multi-file patches (mode="patch"). Returns dict with status."""',
        '{"path": path, "old_string": old_string, "new_string": new_string, "replace_all": replace_all, "mode": mode, "patch": patch}',
    ),
    "terminal": (
        "terminal",
        "command: str, timeout: int = None, workdir: str = None",
        '"""Run a shell command (foreground only). Returns dict with "output" and "exit_code"."""',
        '{"command": command, "timeout": timeout, "workdir": workdir}',
    ),
}


def generate_hermes_tools_module(enabled_tools: List[str],
                                 transport: str = "uds") -> str:
    """
    构建 hermes_tools.py 桩模块的源代码。

    只有同时存在于 SANDBOX_ALLOWED_TOOLS 和 enabled_tools 中的工具才会生成桩函数。

    Args:
        enabled_tools: 当前会话中启用的工具名称。
        transport: ``"uds"`` 表示 Unix 域套接字（本地后端），
                   ``"file"`` 表示基于文件的 RPC（远程后端）。
    """
    tools_to_generate = sorted(SANDBOX_ALLOWED_TOOLS & set(enabled_tools))

    stub_functions = []
    export_names = []
    for tool_name in tools_to_generate:
        if tool_name not in _TOOL_STUBS:
            continue
        func_name, sig, doc, args_expr = _TOOL_STUBS[tool_name]
        stub_functions.append(
            f"def {func_name}({sig}):\n"
            f"    {doc}\n"
            f"    return _call({func_name!r}, {args_expr})\n"
        )
        export_names.append(func_name)

    if transport == "file":
        header = _FILE_TRANSPORT_HEADER
    else:
        header = _UDS_TRANSPORT_HEADER

    return header + "\n".join(stub_functions)


# ---- 共享辅助函数部分（嵌入在两种传输头中） ----------

_COMMON_HELPERS = '''\

# ---------------------------------------------------------------------------
# Convenience helpers (avoid common scripting pitfalls)
# ---------------------------------------------------------------------------

def json_parse(text: str):
    """Parse JSON tolerant of control characters (strict=False).
    Use this instead of json.loads() when parsing output from terminal()
    or web_extract() that may contain raw tabs/newlines in strings."""
    return json.loads(text, strict=False)


def shell_quote(s: str) -> str:
    """Shell-escape a string for safe interpolation into commands.
    Use this when inserting dynamic content into terminal() commands:
        terminal(f"echo {shell_quote(user_input)}")
    """
    return shlex.quote(s)


def retry(fn, max_attempts=3, delay=2):
    """Retry a function up to max_attempts times with exponential backoff.
    Use for transient failures (network errors, API rate limits):
        result = retry(lambda: terminal("gh issue list ..."))
    """
    last_err = None
    for attempt in range(max_attempts):
        try:
            return fn()
        except Exception as e:
            last_err = e
            if attempt < max_attempts - 1:
                time.sleep(delay * (2 ** attempt))
    raise last_err

'''

# ---- UDS 传输（本地后端） -----------------------------------------------

_UDS_TRANSPORT_HEADER = '''\
"""Auto-generated Hermes tools RPC stubs."""
import json, os, socket, shlex, time

_sock = None
''' + _COMMON_HELPERS + '''\

def _connect():
    global _sock
    if _sock is None:
        _sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        _sock.connect(os.environ["HERMES_RPC_SOCKET"])
        _sock.settimeout(300)
    return _sock

def _call(tool_name, args):
    """Send a tool call to the parent process and return the parsed result."""
    conn = _connect()
    request = json.dumps({"tool": tool_name, "args": args}) + "\\n"
    conn.sendall(request.encode())
    buf = b""
    while True:
        chunk = conn.recv(65536)
        if not chunk:
            raise RuntimeError("Agent process disconnected")
        buf += chunk
        if buf.endswith(b"\\n"):
            break
    raw = buf.decode().strip()
    result = json.loads(raw)
    if isinstance(result, str):
        try:
            return json.loads(result)
        except (json.JSONDecodeError, TypeError):
            return result
    return result

'''

# ---- 基于文件的传输（远程后端） -------------------------------

_FILE_TRANSPORT_HEADER = '''\
"""Auto-generated Hermes tools RPC stubs (file-based transport)."""
import json, os, shlex, tempfile, time

_RPC_DIR = os.environ.get("HERMES_RPC_DIR") or os.path.join(tempfile.gettempdir(), "hermes_rpc")
_seq = 0
''' + _COMMON_HELPERS + '''\

def _call(tool_name, args):
    """Send a tool call request via file-based RPC and wait for response."""
    global _seq
    _seq += 1
    seq_str = f"{_seq:06d}"
    req_file = os.path.join(_RPC_DIR, f"req_{seq_str}")
    res_file = os.path.join(_RPC_DIR, f"res_{seq_str}")

    # Write request atomically (write to .tmp, then rename)
    tmp = req_file + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"tool": tool_name, "args": args, "seq": _seq}, f)
    os.rename(tmp, req_file)

    # Wait for response with adaptive polling
    deadline = time.monotonic() + 300  # 5-minute timeout per tool call
    poll_interval = 0.05  # Start at 50ms
    while not os.path.exists(res_file):
        if time.monotonic() > deadline:
            raise RuntimeError(f"RPC timeout: no response for {tool_name} after 300s")
        time.sleep(poll_interval)
        poll_interval = min(poll_interval * 1.2, 0.25)  # Back off to 250ms

    with open(res_file) as f:
        raw = f.read()

    # Clean up response file
    try:
        os.unlink(res_file)
    except OSError:
        pass

    result = json.loads(raw)
    if isinstance(result, str):
        try:
            return json.loads(result)
        except (json.JSONDecodeError, TypeError):
            return result
    return result

'''


# ---------------------------------------------------------------------------
# RPC 服务器（在父进程的线程中运行）
# ---------------------------------------------------------------------------

# 临时沙箱脚本中不允许使用的终端参数
_TERMINAL_BLOCKED_PARAMS = {"background", "pty", "notify_on_complete", "watch_patterns"}


def _rpc_server_loop(
    server_sock: socket.socket,
    task_id: str,
    tool_call_log: list,
    tool_call_counter: list,   # 可变 [int]，使线程能够递增
    max_tool_calls: int,
    allowed_tools: frozenset,
):
    """
    接受一个客户端连接并分发工具调用请求，直到
    客户端断开连接或达到调用上限。
    """
    from model_tools import handle_function_call

    conn = None
    try:
        server_sock.settimeout(5)
        conn, _ = server_sock.accept()
        conn.settimeout(300)

        buf = b""
        while True:
            try:
                chunk = conn.recv(65536)
            except socket.timeout:
                break
            if not chunk:
                break
            buf += chunk

            # 处理缓冲区中所有完整的换行分隔消息
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                line = line.strip()
                if not line:
                    continue

                call_start = time.monotonic()
                try:
                    request = json.loads(line.decode())
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    resp = tool_error(f"Invalid RPC request: {exc}")
                    conn.sendall((resp + "\n").encode())
                    continue

                tool_name = request.get("tool", "")
                tool_args = request.get("args", {})

                # 强制执行允许列表
                if tool_name not in allowed_tools:
                    available = ", ".join(sorted(allowed_tools))
                    resp = json.dumps({
                        "error": (
                            f"Tool '{tool_name}' is not available in execute_code. "
                            f"Available: {available}"
                        )
                    })
                    conn.sendall((resp + "\n").encode())
                    continue

                # 强制执行工具调用上限
                if tool_call_counter[0] >= max_tool_calls:
                    resp = json.dumps({
                        "error": (
                            f"Tool call limit reached ({max_tool_calls}). "
                            "No more tool calls allowed in this execution."
                        )
                    })
                    conn.sendall((resp + "\n").encode())
                    continue

                # 移除被禁止的终端参数
                if tool_name == "terminal" and isinstance(tool_args, dict):
                    for param in _TERMINAL_BLOCKED_PARAMS:
                        tool_args.pop(param, None)

                # 通过标准工具处理器分发。
                # 抑制内部工具处理器的 stdout/stderr，
                # 防止其状态打印泄漏到 CLI 旋转动画中。
                try:
                    _real_stdout, _real_stderr = sys.stdout, sys.stderr
                    devnull = open(os.devnull, "w")
                    try:
                        sys.stdout = devnull
                        sys.stderr = devnull
                        result = handle_function_call(
                            tool_name, tool_args, task_id=task_id
                        )
                    finally:
                        sys.stdout, sys.stderr = _real_stdout, _real_stderr
                        devnull.close()
                except Exception as exc:
                    logger.error("Tool call failed in sandbox: %s", exc, exc_info=True)
                    result = tool_error(str(exc))

                tool_call_counter[0] += 1
                call_duration = time.monotonic() - call_start

                # 记录日志以便观测
                args_preview = str(tool_args)[:80]
                tool_call_log.append({
                    "tool": tool_name,
                    "args_preview": args_preview,
                    "duration": round(call_duration, 2),
                })

                conn.sendall((result + "\n").encode())

    except socket.timeout:
        logger.debug("RPC listener socket timeout")
    except OSError as e:
        logger.debug("RPC listener socket error: %s", e, exc_info=True)
    finally:
        if conn:
            try:
                conn.close()
            except OSError as e:
                logger.debug("RPC conn close error: %s", e)


# ---------------------------------------------------------------------------
# 远程执行支持（通过终端后端的基于文件的 RPC）
# ---------------------------------------------------------------------------

def _get_or_create_env(task_id: str):
    """获取或创建 *task_id* 对应的终端环境。

    复用终端和文件工具使用的同一环境（容器/沙箱/SSH 会话），
    如果不存在则创建一个。返回 ``(env, env_type)`` 元组。
    """
    from tools.terminal_tool import (
        _active_environments, _env_lock, _create_environment,
        _get_env_config, _last_activity, _start_cleanup_thread,
        _creation_locks, _creation_locks_lock, _task_env_overrides,
    )

    effective_task_id = task_id or "default"

    # 快速路径：环境已存在
    with _env_lock:
        if effective_task_id in _active_environments:
            _last_activity[effective_task_id] = time.time()
            return _active_environments[effective_task_id], _get_env_config()["env_type"]

    # 慢速路径：创建环境（与 file_tools._get_file_ops 相同的模式）
    with _creation_locks_lock:
        if effective_task_id not in _creation_locks:
            _creation_locks[effective_task_id] = threading.Lock()
        task_lock = _creation_locks[effective_task_id]

    with task_lock:
        with _env_lock:
            if effective_task_id in _active_environments:
                _last_activity[effective_task_id] = time.time()
                return _active_environments[effective_task_id], _get_env_config()["env_type"]

        config = _get_env_config()
        env_type = config["env_type"]
        overrides = _task_env_overrides.get(effective_task_id, {})

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

        logger.info("Creating new %s environment for execute_code task %s...",
                     env_type, effective_task_id[:8])
        env = _create_environment(
            env_type=env_type,
            image=image,
            cwd=cwd,
            timeout=config["timeout"],
            ssh_config=ssh_config,
            container_config=container_config,
            local_config=local_config,
            task_id=effective_task_id,
            host_cwd=config.get("host_cwd"),
        )

        with _env_lock:
            _active_environments[effective_task_id] = env
            _last_activity[effective_task_id] = time.time()

        _start_cleanup_thread()
        logger.info("%s environment ready for execute_code task %s",
                     env_type, effective_task_id[:8])
        return env, env_type


def _ship_file_to_remote(env, remote_path: str, content: str) -> None:
    """将 *content* 写入远程环境上的 *remote_path*。

    使用 ``echo … | base64 -d`` 而非 stdin 管道传输，因为某些
    后端（Modal）无法可靠地将 stdin_data 传递给链式命令。
    Base64 输出是 shell 安全的（[A-Za-z0-9+/=]），所以使用
    单引号即可。
    """
    encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
    quoted_remote_path = shlex.quote(remote_path)
    env.execute(
        f"echo '{encoded}' | base64 -d > {quoted_remote_path}",
        cwd="/",
        timeout=30,
    )


def _env_temp_dir(env: Any) -> str:
    """返回基于环境的 execute_code 沙箱的可写临时目录。"""
    get_temp_dir = getattr(env, "get_temp_dir", None)
    if callable(get_temp_dir):
        try:
            temp_dir = get_temp_dir()
            if isinstance(temp_dir, str) and temp_dir.startswith("/"):
                return temp_dir.rstrip("/") or "/"
        except Exception as exc:
            logger.debug("Could not resolve execute_code env temp dir: %s", exc)
    candidate = tempfile.gettempdir()
    if isinstance(candidate, str) and candidate.startswith("/"):
        return candidate.rstrip("/") or "/"
    return "/tmp"


def _rpc_poll_loop(
    env,
    rpc_dir: str,
    task_id: str,
    tool_call_log: list,
    tool_call_counter: list,
    max_tool_calls: int,
    allowed_tools: frozenset,
    stop_event: threading.Event,
):
    """轮询远程文件系统获取工具调用请求并分发它们。

    在后台线程中运行。每个 ``env.execute()`` 生成一个
    独立的进程，因此这些调用可以安全地与脚本执行线程并发运行。
    """
    from model_tools import handle_function_call

    poll_interval = 0.1  # 100 ms

    quoted_rpc_dir = shlex.quote(rpc_dir)
    while not stop_event.is_set():
        try:
            # 列出待处理的请求文件（跳过 .tmp 部分文件）
            ls_result = env.execute(
                f"ls -1 {quoted_rpc_dir}/req_* 2>/dev/null || true",
                cwd="/",
                timeout=10,
            )
            output = ls_result.get("output", "").strip()
            if not output:
                stop_event.wait(poll_interval)
                continue

            req_files = sorted([
                f.strip() for f in output.split("\n")
                if f.strip()
                and not f.strip().endswith(".tmp")
                and "/req_" in f.strip()
            ])

            for req_file in req_files:
                if stop_event.is_set():
                    break

                call_start = time.monotonic()

                quoted_req_file = shlex.quote(req_file)
                # 读取请求
                read_result = env.execute(
                    f"cat {quoted_req_file}",
                    cwd="/",
                    timeout=10,
                )
                try:
                    request = json.loads(read_result.get("output", ""))
                except (json.JSONDecodeError, ValueError):
                    logger.debug("Malformed RPC request in %s", req_file)
                    # 移除错误的请求以避免无限重试
                    env.execute(f"rm -f {quoted_req_file}", cwd="/", timeout=5)
                    continue

                tool_name = request.get("tool", "")
                tool_args = request.get("args", {})
                seq = request.get("seq", 0)
                seq_str = f"{seq:06d}"
                res_file = f"{rpc_dir}/res_{seq_str}"
                quoted_res_file = shlex.quote(res_file)

                # 强制执行允许列表
                if tool_name not in allowed_tools:
                    available = ", ".join(sorted(allowed_tools))
                    tool_result = json.dumps({
                        "error": (
                            f"Tool '{tool_name}' is not available in execute_code. "
                            f"Available: {available}"
                        )
                    })
                # 强制执行工具调用上限
                elif tool_call_counter[0] >= max_tool_calls:
                    tool_result = json.dumps({
                        "error": (
                            f"Tool call limit reached ({max_tool_calls}). "
                            "No more tool calls allowed in this execution."
                        )
                    })
                else:
                    # 移除被禁止的终端参数
                    if tool_name == "terminal" and isinstance(tool_args, dict):
                        for param in _TERMINAL_BLOCKED_PARAMS:
                            tool_args.pop(param, None)

                    # 通过标准工具处理器分发
                    try:
                        _real_stdout, _real_stderr = sys.stdout, sys.stderr
                        devnull = open(os.devnull, "w")
                        try:
                            sys.stdout = devnull
                            sys.stderr = devnull
                            tool_result = handle_function_call(
                                tool_name, tool_args, task_id=task_id
                            )
                        finally:
                            sys.stdout, sys.stderr = _real_stdout, _real_stderr
                            devnull.close()
                    except Exception as exc:
                        logger.error("Tool call failed in remote sandbox: %s",
                                     exc, exc_info=True)
                        tool_result = tool_error(str(exc))

                    tool_call_counter[0] += 1
                    call_duration = time.monotonic() - call_start
                    tool_call_log.append({
                        "tool": tool_name,
                        "args_preview": str(tool_args)[:80],
                        "duration": round(call_duration, 2),
                    })

                # 原子写入响应（tmp + rename）。
                # 使用 echo 管道（而非 stdin_data），因为 Modal 无法
                # 可靠地将 stdin 传递给链式命令。
                encoded_result = base64.b64encode(
                    tool_result.encode("utf-8")
                ).decode("ascii")
                env.execute(
                    f"echo '{encoded_result}' | base64 -d > {quoted_res_file}.tmp"
                    f" && mv {quoted_res_file}.tmp {quoted_res_file}",
                    cwd="/",
                    timeout=60,
                )

                # 移除请求文件
                env.execute(f"rm -f {quoted_req_file}", cwd="/", timeout=5)

        except Exception as e:
            if not stop_event.is_set():
                logger.debug("RPC poll error: %s", e, exc_info=True)

        if not stop_event.is_set():
            stop_event.wait(poll_interval)


def _execute_remote(
    code: str,
    task_id: Optional[str],
    enabled_tools: Optional[List[str]],
) -> str:
    """在远程终端后端通过基于文件的 RPC 运行脚本。

    脚本和生成的 hermes_tools.py 模块被传送到远程环境，
    工具调用通过一个轮询线程代理，该线程通过请求/响应文件通信。
    """

    _cfg = _load_config()
    timeout = _cfg.get("timeout", DEFAULT_TIMEOUT)
    max_tool_calls = _cfg.get("max_tool_calls", DEFAULT_MAX_TOOL_CALLS)

    session_tools = set(enabled_tools) if enabled_tools else set()
    sandbox_tools = frozenset(SANDBOX_ALLOWED_TOOLS & session_tools)
    if not sandbox_tools:
        sandbox_tools = SANDBOX_ALLOWED_TOOLS

    effective_task_id = task_id or "default"
    env, env_type = _get_or_create_env(effective_task_id)

    sandbox_id = uuid.uuid4().hex[:12]
    temp_dir = _env_temp_dir(env)
    sandbox_dir = f"{temp_dir}/hermes_exec_{sandbox_id}"
    quoted_sandbox_dir = shlex.quote(sandbox_dir)
    quoted_rpc_dir = shlex.quote(f"{sandbox_dir}/rpc")

    tool_call_log: list = []
    tool_call_counter = [0]
    exec_start = time.monotonic()
    stop_event = threading.Event()
    rpc_thread = None

    try:
        # 验证远程环境上 Python 是否可用
        py_check = env.execute(
            "command -v python3 >/dev/null 2>&1 && echo OK",
            cwd="/", timeout=15,
        )
        if "OK" not in py_check.get("output", ""):
            return json.dumps({
                "status": "error",
                "error": (
                    f"Python 3 is not available in the {env_type} terminal "
                    "environment. Install Python to use execute_code with "
                    "remote backends."
                ),
                "tool_calls_made": 0,
                "duration_seconds": 0,
            })

        # 在远程创建沙箱目录
        env.execute(
            f"mkdir -p {quoted_rpc_dir}", cwd="/", timeout=10,
        )

        # 生成并传送文件
        tools_src = generate_hermes_tools_module(
            list(sandbox_tools), transport="file",
        )
        _ship_file_to_remote(env, f"{sandbox_dir}/hermes_tools.py", tools_src)
        _ship_file_to_remote(env, f"{sandbox_dir}/script.py", code)

        # 启动 RPC 轮询线程
        rpc_thread = threading.Thread(
            target=_rpc_poll_loop,
            args=(
                env, f"{sandbox_dir}/rpc", effective_task_id,
                tool_call_log, tool_call_counter, max_tool_calls,
                sandbox_tools, stop_event,
            ),
            daemon=True,
        )
        rpc_thread.start()

        # 为脚本构建环境变量前缀
        env_prefix = (
            f"HERMES_RPC_DIR={shlex.quote(f'{sandbox_dir}/rpc')} "
            f"PYTHONDONTWRITEBYTECODE=1"
        )
        tz = os.getenv("HERMES_TIMEZONE", "").strip()
        if tz:
            env_prefix += f" TZ={tz}"

        # 在远程后端执行脚本
        logger.info("Executing code on %s backend (task %s)...",
                     env_type, effective_task_id[:8])
        script_result = env.execute(
            f"cd {quoted_sandbox_dir} && {env_prefix} python3 script.py",
            timeout=timeout,
        )

        stdout_text = script_result.get("output", "")
        exit_code = script_result.get("returncode", -1)
        status = "success"

        # 检查后端返回的超时/中断
        if exit_code == 124:
            status = "timeout"
        elif exit_code == 130:
            status = "interrupted"

    except Exception as exc:
        duration = round(time.monotonic() - exec_start, 2)
        logger.error(
            "execute_code remote failed after %ss with %d tool calls: %s: %s",
            duration, tool_call_counter[0], type(exc).__name__, exc,
            exc_info=True,
        )
        return json.dumps({
            "status": "error",
            "error": str(exc),
            "tool_calls_made": tool_call_counter[0],
            "duration_seconds": duration,
        }, ensure_ascii=False)

    finally:
        # 停止轮询线程
        stop_event.set()
        if rpc_thread is not None:
            rpc_thread.join(timeout=5)

        # 清理远程沙箱目录
        try:
            env.execute(
                f"rm -rf {quoted_sandbox_dir}", cwd="/", timeout=15,
            )
        except Exception:
            logger.debug("Failed to clean up remote sandbox %s", sandbox_dir)

    duration = round(time.monotonic() - exec_start, 2)

    # --- 后处理输出（与本地路径相同） ---

    # 截断 stdout 至上限
    if len(stdout_text) > MAX_STDOUT_BYTES:
        head_bytes = int(MAX_STDOUT_BYTES * 0.4)
        tail_bytes = MAX_STDOUT_BYTES - head_bytes
        head = stdout_text[:head_bytes]
        tail = stdout_text[-tail_bytes:]
        omitted = len(stdout_text) - len(head) - len(tail)
        stdout_text = (
            head
            + f"\n\n... [OUTPUT TRUNCATED - {omitted:,} chars omitted "
            f"out of {len(stdout_text):,} total] ...\n\n"
            + tail
        )

    # 去除 ANSI 转义序列
    from tools.ansi_strip import strip_ansi
    stdout_text = strip_ansi(stdout_text)

    # 脱敏处理
    from agent.redact import redact_sensitive_text
    stdout_text = redact_sensitive_text(stdout_text)

    # 构建响应
    result: Dict[str, Any] = {
        "status": status,
        "output": stdout_text,
        "tool_calls_made": tool_call_counter[0],
        "duration_seconds": duration,
    }

    if status == "timeout":
        timeout_msg = f"Script timed out after {timeout}s and was killed."
        result["error"] = timeout_msg
        # 将超时消息包含在输出中，以便 LLM 始终向用户展示
        # （参见本地路径注释——相同的理由，#10807）。
        if stdout_text:
            result["output"] = stdout_text + f"\n\n⏰ {timeout_msg}"
        else:
            result["output"] = f"⏰ {timeout_msg}"
        logger.warning(
            "execute_code (remote) timed out after %ss (limit %ss) with %d tool calls",
            duration, timeout, tool_call_counter[0],
        )
    elif status == "interrupted":
        result["output"] = (
            stdout_text + "\n[execution interrupted — user sent a new message]"
        )
    elif exit_code != 0:
        result["status"] = "error"
        result["error"] = f"Script exited with code {exit_code}"

    return json.dumps(result, ensure_ascii=False)


# ---------------------------------------------------------------------------
# 主入口点
# ---------------------------------------------------------------------------

def execute_code(
    code: str,
    task_id: Optional[str] = None,
    enabled_tools: Optional[List[str]] = None,
) -> str:
    """
    在沙箱子进程中运行 Python 脚本，可通过 RPC 访问
    Hermes 工具的子集。

    根据配置的终端后端，分发到本地（UDS）或远程（基于文件的 RPC）路径。

    Args:
        code:          要执行的 Python 源代码。
        task_id:       会话任务 ID，用于工具隔离（终端环境等）。
        enabled_tools: 当前会话中启用的工具名称。沙箱获取
                       与 SANDBOX_ALLOWED_TOOLS 的交集。

    Returns:
        包含执行结果的 JSON 字符串。
    """
    if not SANDBOX_AVAILABLE:
        return json.dumps({
            "error": "execute_code is not available on Windows. Use normal tool calls instead."
        })

    if not code or not code.strip():
        return tool_error("No code provided.")

    # 分发：远程后端使用基于文件的 RPC，本地使用 UDS
    from tools.terminal_tool import _get_env_config
    env_type = _get_env_config()["env_type"]
    if env_type != "local":
        return _execute_remote(code, task_id, enabled_tools)

    # --- 本地执行路径（UDS） --- 以下代码未变 ---

    # 导入每线程中断检查（协作式取消）
    from tools.interrupt import is_interrupted as _is_interrupted

    # 解析配置
    _cfg = _load_config()
    timeout = _cfg.get("timeout", DEFAULT_TIMEOUT)
    max_tool_calls = _cfg.get("max_tool_calls", DEFAULT_MAX_TOOL_CALLS)

    # 确定沙箱可以调用哪些工具
    session_tools = set(enabled_tools) if enabled_tools else set()
    sandbox_tools = frozenset(SANDBOX_ALLOWED_TOOLS & session_tools)

    if not sandbox_tools:
        sandbox_tools = SANDBOX_ALLOWED_TOOLS

    # --- 设置包含 hermes_tools.py 和 script.py 的临时目录 ---
    tmpdir = tempfile.mkdtemp(prefix="hermes_sandbox_")
    # 在 macOS 上使用 /tmp 以避免过长的 /var/folders/... 路径
    # 超过 macOS AF_UNIX 的 104 字节限制。
    # 在 Linux 上，tempfile.gettempdir() 已经返回 /tmp。
    _sock_tmpdir = "/tmp" if sys.platform == "darwin" else tempfile.gettempdir()
    sock_path = os.path.join(_sock_tmpdir, f"hermes_rpc_{uuid.uuid4().hex}.sock")

    tool_call_log: list = []
    tool_call_counter = [0]  # 可变以便 RPC 线程能递增
    exec_start = time.monotonic()
    server_sock = None

    try:
        # 写入自动生成的 hermes_tools 模块
        # sandbox_tools 已经是正确的集合（与会话工具的交集，
        # 或 SANDBOX_ALLOWED_TOOLS 作为回退——见上面的行）。
        tools_src = generate_hermes_tools_module(list(sandbox_tools))
        with open(os.path.join(tmpdir, "hermes_tools.py"), "w") as f:
            f.write(tools_src)

        # 写入用户的脚本
        with open(os.path.join(tmpdir, "script.py"), "w") as f:
            f.write(code)

        # --- 启动 UDS 服务器 ---
        server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server_sock.bind(sock_path)
        server_sock.listen(1)

        rpc_thread = threading.Thread(
            target=_rpc_server_loop,
            args=(
                server_sock, task_id, tool_call_log,
                tool_call_counter, max_tool_calls, sandbox_tools,
            ),
            daemon=True,
        )
        rpc_thread.start()

        # --- 生成子进程 ---
        # 为子进程构建最小环境。我们有意排除 API 密钥和令牌，
        # 以防止 LLM 生成的脚本窃取凭证。子进程通过 RPC 访问工具，
        # 而非直接 API。
        # 例外：已加载技能声明的环境变量（通过 env_passthrough 注册表）
        # 或用户在 config.yaml (terminal.env_passthrough) 中明确允许的
        # 变量会被传递。
        _SAFE_ENV_PREFIXES = ("PATH", "HOME", "USER", "LANG", "LC_", "TERM",
                              "TMPDIR", "TMP", "TEMP", "SHELL", "LOGNAME",
                              "XDG_", "PYTHONPATH", "VIRTUAL_ENV", "CONDA",
                              "HERMES_")
        _SECRET_SUBSTRINGS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL",
                              "PASSWD", "AUTH")
        try:
            from tools.env_passthrough import is_env_passthrough as _is_passthrough
        except Exception:
            _is_passthrough = lambda _: False  # noqa: E731
        child_env = {}
        for k, v in os.environ.items():
            # 透传变量（技能声明或用户配置的）始终传递。
            if _is_passthrough(k):
                child_env[k] = v
                continue
            # 阻止包含类似密钥名称的变量。
            if any(s in k.upper() for s in _SECRET_SUBSTRINGS):
                continue
            # 允许具有已知安全前缀的变量。
            if any(k.startswith(p) for p in _SAFE_ENV_PREFIXES):
                child_env[k] = v
        child_env["HERMES_RPC_SOCKET"] = sock_path
        child_env["PYTHONDONTWRITEBYTECODE"] = "1"
        # 确保 hermes-agent 根目录在沙箱中可导入，
        # 以便子脚本能访问仓库根模块。
        _hermes_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        _existing_pp = child_env.get("PYTHONPATH", "")
        child_env["PYTHONPATH"] = _hermes_root + (os.pathsep + _existing_pp if _existing_pp else "")
        # 注入用户配置的时区，使沙箱代码中的 datetime.now()
        # 反映正确的挂钟时间。只设置 TZ——
        # HERMES_TIMEZONE 是 Hermes 内部设置，不得泄漏
        # 到子进程中。
        _tz_name = os.getenv("HERMES_TIMEZONE", "").strip()
        if _tz_name:
            child_env["TZ"] = _tz_name
        child_env.pop("HERMES_TIMEZONE", None)

        # 每配置文件 HOME 隔离：当 {HERMES_HOME}/home/ 目录存在时，
        # 将系统工具配置重定向到该目录。
        from hermes_constants import get_subprocess_home
        _profile_home = get_subprocess_home()
        if _profile_home:
            child_env["HOME"] = _profile_home

        proc = subprocess.Popen(
            [sys.executable, "script.py"],
            cwd=tmpdir,
            env=child_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            preexec_fn=None if _IS_WINDOWS else os.setsid,
        )

        # --- 轮询循环：监视退出、超时和中断 ---
        deadline = time.monotonic() + timeout
        stderr_chunks: list = []

        # 后台读取器以避免管道缓冲区死锁。
        # 对于 stdout，我们使用头+尾策略：保留前 HEAD_BYTES
        # 和最后 TAIL_BYTES 的滚动窗口，确保最终的 print()
        # 输出永远不会丢失。Stderr 仅保留头部（错误出现在早期）。
        _STDOUT_HEAD_BYTES = int(MAX_STDOUT_BYTES * 0.4)   # 40% 头部
        _STDOUT_TAIL_BYTES = MAX_STDOUT_BYTES - _STDOUT_HEAD_BYTES  # 60% 尾部

        def _drain(pipe, chunks, max_bytes):
            """简单的仅头部排空（用于 stderr）。"""
            total = 0
            try:
                while True:
                    data = pipe.read(4096)
                    if not data:
                        break
                    if total < max_bytes:
                        keep = max_bytes - total
                        chunks.append(data[:keep])
                    total += len(data)
            except (ValueError, OSError) as e:
                logger.debug("Error reading process output: %s", e, exc_info=True)

        stdout_total_bytes = [0]  # 总字节数的可变引用

        def _drain_head_tail(pipe, head_chunks, tail_chunks, head_bytes, tail_bytes, total_ref):
            """排空 stdout，同时保留头部和尾部数据。"""
            head_collected = 0
            from collections import deque
            tail_buf = deque()
            tail_collected = 0
            try:
                while True:
                    data = pipe.read(4096)
                    if not data:
                        break
                    total_ref[0] += len(data)
                    # 先填满头部缓冲区
                    if head_collected < head_bytes:
                        keep = min(len(data), head_bytes - head_collected)
                        head_chunks.append(data[:keep])
                        head_collected += keep
                        data = data[keep:]  # remaining goes to tail
                        if not data:
                            continue
                    # 超过头部的所有数据进入滚动尾部缓冲区
                    tail_buf.append(data)
                    tail_collected += len(data)
                    # 淘汰旧的尾部数据以保持在 tail_bytes 预算内
                    while tail_collected > tail_bytes and tail_buf:
                        oldest = tail_buf.popleft()
                        tail_collected -= len(oldest)
            except (ValueError, OSError):
                pass
            # 将最终尾部数据转移到输出列表
            tail_chunks.extend(tail_buf)

        stdout_head_chunks: list = []
        stdout_tail_chunks: list = []

        stdout_reader = threading.Thread(
            target=_drain_head_tail,
            args=(proc.stdout, stdout_head_chunks, stdout_tail_chunks,
                  _STDOUT_HEAD_BYTES, _STDOUT_TAIL_BYTES, stdout_total_bytes),
            daemon=True
        )
        stderr_reader = threading.Thread(
            target=_drain, args=(proc.stderr, stderr_chunks, MAX_STDERR_BYTES), daemon=True
        )
        stdout_reader.start()
        stderr_reader.start()

        status = "success"
        _activity_state = {
            "last_touch": time.monotonic(),
            "start": exec_start,
        }
        while proc.poll() is None:
            if _is_interrupted():
                _kill_process_group(proc)
                status = "interrupted"
                break
            if time.monotonic() > deadline:
                _kill_process_group(proc, escalate=True)
                status = "timeout"
                break
            # 定期活动触发，防止网关的不活动超时
            # 在长时间代码执行期间杀死代理（#10807）。
            try:
                from tools.environments.base import touch_activity_if_due
                touch_activity_if_due(_activity_state, "execute_code running")
            except Exception:
                pass
            time.sleep(0.2)

        # 等待读取器完成排空
        stdout_reader.join(timeout=3)
        stderr_reader.join(timeout=3)

        stdout_head = b"".join(stdout_head_chunks).decode("utf-8", errors="replace")
        stdout_tail = b"".join(stdout_tail_chunks).decode("utf-8", errors="replace")
        stderr_text = b"".join(stderr_chunks).decode("utf-8", errors="replace")

        # 使用头+尾截断组装 stdout
        total_stdout = stdout_total_bytes[0]
        if total_stdout > MAX_STDOUT_BYTES and stdout_tail:
            omitted = total_stdout - len(stdout_head) - len(stdout_tail)
            truncated_notice = (
                f"\n\n... [OUTPUT TRUNCATED - {omitted:,} chars omitted "
                f"out of {total_stdout:,} total] ...\n\n"
            )
            stdout_text = stdout_head + truncated_notice + stdout_tail
        else:
            stdout_text = stdout_head + stdout_tail

        exit_code = proc.returncode if proc.returncode is not None else -1
        duration = round(time.monotonic() - exec_start, 2)

        # 等待 RPC 线程完成
        server_sock.close()  # 中断 accept() 使线程及时退出
        server_sock = None  # 防止在 finally 中重复关闭
        rpc_thread.join(timeout=3)

        # 去除 ANSI 转义序列，防止模型看到终端
        # 格式化——避免将转义字符复制到文件写入中。
        from tools.ansi_strip import strip_ansi
        stdout_text = strip_ansi(stdout_text)
        stderr_text = strip_ansi(stderr_text)

        # 脱敏沙箱输出中的密钥（API 密钥、令牌等）。
        # 沙箱环境变量过滤器（第 434-454 行）阻止了 os.environ 访问，
        # 但脚本仍可从磁盘读取密钥（如 open('~/.hermes/.env')）。
        # 这确保泄漏的密钥永远不会进入模型上下文。
        from agent.redact import redact_sensitive_text
        stdout_text = redact_sensitive_text(stdout_text)
        stderr_text = redact_sensitive_text(stderr_text)

        # 构建响应
        result: Dict[str, Any] = {
            "status": status,
            "output": stdout_text,
            "tool_calls_made": tool_call_counter[0],
            "duration_seconds": duration,
        }

        if status == "timeout":
            timeout_msg = f"Script timed out after {timeout}s and was killed."
            result["error"] = timeout_msg
            # 将超时消息包含在输出中，以便 LLM 始终向用户展示。
            # 当输出为空时，模型通常将结果视为"什么也没发生"并产生
            # 空响应，网关流消费者会静默丢弃它（#10807）。
            if stdout_text:
                result["output"] = stdout_text + f"\n\n⏰ {timeout_msg}"
            else:
                result["output"] = f"⏰ {timeout_msg}"
            logger.warning(
                "execute_code timed out after %ss (limit %ss) with %d tool calls",
                duration, timeout, tool_call_counter[0],
            )
        elif status == "interrupted":
            result["output"] = stdout_text + "\n[execution interrupted — user sent a new message]"
        elif exit_code != 0:
            result["status"] = "error"
            result["error"] = stderr_text or f"Script exited with code {exit_code}"
            # 将 stderr 包含在输出中以便 LLM 看到回溯
            if stderr_text:
                result["output"] = stdout_text + "\n--- stderr ---\n" + stderr_text

        return json.dumps(result, ensure_ascii=False)

    except Exception as exc:
        duration = round(time.monotonic() - exec_start, 2)
        logger.error(
            "execute_code failed after %ss with %d tool calls: %s: %s",
            duration,
            tool_call_counter[0],
            type(exc).__name__,
            exc,
            exc_info=True,
        )
        return json.dumps({
            "status": "error",
            "error": str(exc),
            "tool_calls_made": tool_call_counter[0],
            "duration_seconds": duration,
        }, ensure_ascii=False)

    finally:
        # 清理临时目录和套接字
        if server_sock is not None:
            try:
                server_sock.close()
            except OSError as e:
                logger.debug("Server socket close error: %s", e)
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)
        try:
            os.unlink(sock_path)
        except OSError:
            pass  # 已清理或从未创建


def _kill_process_group(proc, escalate: bool = False):
    """终止子进程及其整个进程组。"""
    try:
        if _IS_WINDOWS:
            proc.terminate()
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError) as e:
        logger.debug("Could not kill process group: %s", e, exc_info=True)
        try:
            proc.kill()
        except Exception as e2:
            logger.debug("Could not kill process: %s", e2, exc_info=True)

    if escalate:
        # SIGTERM 后给进程 5 秒退出时间，然后 SIGKILL
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                if _IS_WINDOWS:
                    proc.kill()
                else:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError) as e:
                logger.debug("Could not kill process group with SIGKILL: %s", e, exc_info=True)
                try:
                    proc.kill()
                except Exception as e2:
                    logger.debug("Could not kill process: %s", e2, exc_info=True)


def _load_config() -> dict:
    """如果可用，从 CLI_CONFIG 加载 code_execution 配置。"""
    try:
        from cli import CLI_CONFIG
        return CLI_CONFIG.get("code_execution", {})
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# OpenAI 函数调用 Schema
# ---------------------------------------------------------------------------

# 每个工具的文档行，用于 execute_code 的描述。
# 按照规范显示顺序排列。
_TOOL_DOC_LINES = [
    ("web_search",
     "  web_search(query: str, limit: int = 5) -> dict\n"
     "    Returns {\"data\": {\"web\": [{\"url\", \"title\", \"description\"}, ...]}}"),
    ("web_extract",
     "  web_extract(urls: list[str]) -> dict\n"
     "    Returns {\"results\": [{\"url\", \"title\", \"content\", \"error\"}, ...]} where content is markdown"),
    ("read_file",
     "  read_file(path: str, offset: int = 1, limit: int = 500) -> dict\n"
     "    Lines are 1-indexed. Returns {\"content\": \"...\", \"total_lines\": N}"),
    ("write_file",
     "  write_file(path: str, content: str) -> dict\n"
     "    Always overwrites the entire file."),
    ("search_files",
     "  search_files(pattern: str, target=\"content\", path=\".\", file_glob=None, limit=50) -> dict\n"
     "    target: \"content\" (search inside files) or \"files\" (find files by name). Returns {\"matches\": [...]}"),
    ("patch",
     "  patch(path: str, old_string: str, new_string: str, replace_all: bool = False) -> dict\n"
     "    Replaces old_string with new_string in the file."),
    ("terminal",
     "  terminal(command: str, timeout=None, workdir=None) -> dict\n"
     "    Foreground only (no background/pty). Returns {\"output\": \"...\", \"exit_code\": N}"),
]


def build_execute_code_schema(enabled_sandbox_tools: set = None) -> dict:
    """构建 execute_code schema，描述中仅列出已启用的工具。

    当通过 ``hermes tools`` 禁用工具时（如关闭 web），
    schema 描述中不应提及 web_search / web_extract——
    否则模型会认为它们可用并不断尝试使用。
    """
    if enabled_sandbox_tools is None:
        enabled_sandbox_tools = SANDBOX_ALLOWED_TOOLS

    # 仅为已启用的工具构建工具文档行
    tool_lines = "\n".join(
        doc for name, doc in _TOOL_DOC_LINES if name in enabled_sandbox_tools
    )

    # 从已启用工具构建示例导入列表
    import_examples = [n for n in ("web_search", "terminal") if n in enabled_sandbox_tools]
    if not import_examples:
        import_examples = sorted(enabled_sandbox_tools)[:2]
    if import_examples:
        import_str = ", ".join(import_examples) + ", ..."
    else:
        import_str = "..."

    description = (
        "Run a Python script that can call Hermes tools programmatically. "
        "Use this when you need 3+ tool calls with processing logic between them, "
        "need to filter/reduce large tool outputs before they enter your context, "
        "need conditional branching (if X then Y else Z), or need to loop "
        "(fetch N pages, process N files, retry on failure).\n\n"
        "Use normal tool calls instead when: single tool call with no processing, "
        "you need to see the full result and apply complex reasoning, "
        "or the task requires interactive user input.\n\n"
        f"Available via `from hermes_tools import ...`:\n\n"
        f"{tool_lines}\n\n"
        "Limits: 5-minute timeout, 50KB stdout cap, max 50 tool calls per script. "
        "terminal() is foreground-only (no background or pty).\n\n"
        "Print your final result to stdout. Use Python stdlib (json, re, math, csv, "
        "datetime, collections, etc.) for processing between tool calls.\n\n"
        "Also available (no import needed — built into hermes_tools):\n"
        "  json_parse(text: str) — json.loads with strict=False; use for terminal() output with control chars\n"
        "  shell_quote(s: str) — shlex.quote(); use when interpolating dynamic strings into shell commands\n"
        "  retry(fn, max_attempts=3, delay=2) — retry with exponential backoff for transient failures"
    )

    return {
        "name": "execute_code",
        "description": description,
        "parameters": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": (
                        "Python code to execute. Import tools with "
                        f"`from hermes_tools import {import_str}` "
                        "and print your final result to stdout."
                    ),
                },
            },
            "required": ["code"],
        },
    }


# 注册时使用的默认 schema（列出所有沙箱工具）
EXECUTE_CODE_SCHEMA = build_execute_code_schema()


# --- 注册表 ---
from tools.registry import registry, tool_error

registry.register(
    name="execute_code",
    toolset="code_execution",
    schema=EXECUTE_CODE_SCHEMA,
    handler=lambda args, **kw: execute_code(
        code=args.get("code", ""),
        task_id=kw.get("task_id"),
        enabled_tools=kw.get("enabled_tools")),
    check_fn=check_sandbox_requirements,
    emoji="🐍",
    max_result_size_chars=100_000,
)
