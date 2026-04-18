"""
进程注册表 -- 用于管理后台进程的内存注册表。

跟踪通过 terminal(background=true) 启动的进程，提供：
  - 输出缓冲（滚动 200KB 窗口）
  - 状态轮询和日志检索
  - 支持中断的阻塞等待
  - 进程终止
  - 通过 JSON 检查点文件进行崩溃恢复
  - 会话范围跟踪，用于网关重置保护

后台进程通过环境接口执行 -- 除非 TERMINAL_ENV=local，否则不会在
宿主机上运行。对于 Docker、Singularity、Modal、Daytona 和 SSH 后端，
命令在沙箱内运行。

使用方法:
    from tools.process_registry import process_registry

    # 启动后台进程（由 terminal_tool 调用）
    session = process_registry.spawn(env, "pytest -v", task_id="task_123")

    # 轮询状态
    result = process_registry.poll(session.id)

    # 阻塞等待直到完成
    result = process_registry.wait(session.id, timeout=300)

    # 终止进程
    process_registry.kill(session.id)
"""

import json
import logging
import os
import platform
import shlex
import signal
import subprocess
import threading
import time
import uuid

_IS_WINDOWS = platform.system() == "Windows"
from tools.environments.local import _find_shell, _sanitize_subprocess_env
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from hermes_cli.config import get_hermes_home

logger = logging.getLogger(__name__)


# 崩溃恢复用的检查点文件（仅网关使用）
CHECKPOINT_PATH = get_hermes_home() / "processes.json"

# 限制参数
MAX_OUTPUT_CHARS = 200_000      # 200KB 滚动输出缓冲区
FINISHED_TTL_SECONDS = 1800     # 已完成进程保留 30 分钟
MAX_PROCESSES = 64              # 最大并发跟踪进程数（LRU 淘汰）

# 监视模式速率限制参数
WATCH_MAX_PER_WINDOW = 8        # 每个窗口期内最多发送的通知数
WATCH_WINDOW_SECONDS = 10       # 滚动窗口时长
WATCH_OVERLOAD_KILL_SECONDS = 45  # 持续过载超过此时长后禁用监视


@dataclass
class ProcessSession:
    """带有输出缓冲的被跟踪后台进程。"""
    id: str                                     # 唯一会话 ID ("proc_xxxxxxxxxxxx")
    command: str                                 # 原始命令字符串
    task_id: str = ""                           # 任务/沙箱隔离键
    session_key: str = ""                       # 网关会话键（用于重置保护）
    pid: Optional[int] = None                   # 操作系统进程 ID
    process: Optional[subprocess.Popen] = None  # Popen 句柄（仅本地模式）
    env_ref: Any = None                         # 环境对象的引用
    cwd: Optional[str] = None                   # 工作目录
    started_at: float = 0.0                     # 启动时的 time.time()
    exited: bool = False                        # 进程是否已结束
    exit_code: Optional[int] = None             # 退出码（运行中时为 None）
    output_buffer: str = ""                     # 滚动输出（最后 MAX_OUTPUT_CHARS 个字符）
    max_output_chars: int = MAX_OUTPUT_CHARS
    detached: bool = False                      # 如果从崩溃中恢复则为 True（无管道）
    pid_scope: str = "host"                     # "host" 表示本地/PTY PID，"sandbox" 表示环境内部 PID
    # 监视器/通知元数据（持久化用于崩溃恢复）
    watcher_platform: str = ""
    watcher_chat_id: str = ""
    watcher_user_id: str = ""
    watcher_user_name: str = ""
    watcher_thread_id: str = ""
    watcher_interval: int = 0                   # 0 = 未配置监视器
    notify_on_complete: bool = False             # 退出时将代理通知加入队列
    # 监视模式 — 当输出匹配任意模式时触发代理通知
    watch_patterns: List[str] = field(default_factory=list)
    _watch_hits: int = field(default=0, repr=False)          # 已发送的总匹配数
    _watch_suppressed: int = field(default=0, repr=False)    # 被速率限制丢弃的匹配数
    _watch_overload_since: float = field(default=0.0, repr=False)  # 持续过载开始的时间
    _watch_disabled: bool = field(default=False, repr=False) # 因过载被永久禁用
    _watch_window_hits: int = field(default=0, repr=False)   # 当前速率窗口内的命中数
    _watch_window_start: float = field(default=0.0, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _reader_thread: Optional[threading.Thread] = field(default=None, repr=False)
    _pty: Any = field(default=None, repr=False)  # ptyprocess 句柄（当 use_pty=True 时）


class ProcessRegistry:
    """
    运行中和已完成后台进程的内存注册表。

    线程安全。被以下组件访问：
      - 执行器线程（terminal_tool、进程工具处理器）
      - 网关 asyncio 循环（监视器任务、会话重置检查）
      - 清理线程（沙箱回收协调）
    """

    # 需要过滤的 shell 启动噪音字符串
    _SHELL_NOISE_SUBSTRINGS = (
        "bash: cannot set terminal process group",
        "bash: no job control in this shell",
        "no job control in this shell",
        "cannot set terminal process group",
        "tcsetattr: Inappropriate ioctl for device",
    )

    def __init__(self):
        self._running: Dict[str, ProcessSession] = {}
        self._finished: Dict[str, ProcessSession] = {}
        self._lock = threading.Lock()

        # 用于 check_interval 监视器的旁路通道（网关在代理运行后读取）
        self.pending_watchers: List[Dict[str, Any]] = []

        # 通知队列 — 所有后台进程事件的统一队列。
        # 完成通知 (notify_on_complete) 和监视模式匹配都会到达这里，
        # 通过 "type" 字段区分。CLI process_loop 和网关在每个代理轮次后
        # 排空此队列以自动触发新的轮次。
        import queue as _queue_mod
        self.completion_queue: _queue_mod.Queue = _queue_mod.Queue()

        # 跟踪已被代理通过 wait/poll/log 消费的会话完成事件。
        # 排空循环会跳过这些会话的通知。
        self._completion_consumed: set = set()

    @staticmethod
    def _clean_shell_noise(text: str) -> str:
        """去除输出开头的 shell 启动警告信息。"""
        lines = text.split("\n")
        while lines and any(noise in lines[0] for noise in ProcessRegistry._SHELL_NOISE_SUBSTRINGS):
            lines.pop(0)
        return "\n".join(lines)

    def _check_watch_patterns(self, session: ProcessSession, new_text: str) -> None:
        """扫描新输出中的监视模式并将通知加入队列。

        由读取器线程调用，new_text 是新读取的数据块。
        速率限制：每 WATCH_WINDOW_SECONDS 秒最多 WATCH_MAX_PER_WINDOW 个通知。
        如果持续过载超过 WATCH_OVERLOAD_KILL_SECONDS，则永久禁用该进程的监视功能。
        """
        if not session.watch_patterns or session._watch_disabled:
            return

        # 逐行扫描新文本查找模式匹配
        matched_lines = []
        matched_pattern = None
        for line in new_text.splitlines():
            for pat in session.watch_patterns:
                if pat in line:
                    matched_lines.append(line.rstrip())
                    if matched_pattern is None:
                        matched_pattern = pat
                    break  # 每行一个匹配就够了

        if not matched_lines:
            return

        now = time.time()
        with session._lock:
            # 如果窗口已过期则重置
            if now - session._watch_window_start >= WATCH_WINDOW_SECONDS:
                session._watch_window_hits = 0
                session._watch_window_start = now

            # 检查速率限制
            if session._watch_window_hits >= WATCH_MAX_PER_WINDOW:
                session._watch_suppressed += len(matched_lines)

                # 跟踪持续过载以触发终止开关
                if session._watch_overload_since == 0.0:
                    session._watch_overload_since = now
                elif now - session._watch_overload_since > WATCH_OVERLOAD_KILL_SECONDS:
                    session._watch_disabled = True
                    self.completion_queue.put({
                        "session_id": session.id,
                        "session_key": session.session_key,
                        "command": session.command,
                        "type": "watch_disabled",
                        "suppressed": session._watch_suppressed,
                        "platform": session.watcher_platform,
                        "chat_id": session.watcher_chat_id,
                        "user_id": session.watcher_user_id,
                        "user_name": session.watcher_user_name,
                        "thread_id": session.watcher_thread_id,
                        "message": (
                            f"Watch patterns disabled for process {session.id} — "
                            f"too many matches ({session._watch_suppressed} suppressed). "
                            f"Use process(action='poll') to check output manually."
                        ),
                    })
                return

            # 在速率限制内 — 发送通知
            session._watch_window_hits += 1
            session._watch_hits += 1
            # 清除过载跟踪器，因为我们成功发送了通知
            session._watch_overload_since = 0.0

            # 如果有事件被丢弃，包含被抑制的计数
            suppressed = session._watch_suppressed
            session._watch_suppressed = 0

        # 将匹配的输出截断到合理大小
        output = "\n".join(matched_lines[:20])
        if len(output) > 2000:
            output = output[:2000] + "\n...(truncated)"

        self.completion_queue.put({
            "session_id": session.id,
            "session_key": session.session_key,
            "command": session.command,
            "type": "watch_match",
            "pattern": matched_pattern,
            "output": output,
            "suppressed": suppressed,
            "platform": session.watcher_platform,
            "chat_id": session.watcher_chat_id,
            "user_id": session.watcher_user_id,
            "user_name": session.watcher_user_name,
            "thread_id": session.watcher_thread_id,
        })

    @staticmethod
    def _is_host_pid_alive(pid: Optional[int]) -> bool:
        """尽力检测宿主机可见 PID 的存活状态。"""
        if not pid:
            return False
        try:
            os.kill(pid, 0)
            return True
        except (ProcessLookupError, PermissionError):
            return False

    def _refresh_detached_session(self, session: Optional[ProcessSession]) -> Optional[ProcessSession]:
        """当底层进程已退出时，更新恢复的宿主机 PID 会话。"""
        if session is None or session.exited or not session.detached or session.pid_scope != "host":
            return session

        if self._is_host_pid_alive(session.pid):
            return session

        with session._lock:
            if session.exited:
                return session
            session.exited = True
            # 恢复的会话不再有可等待的句柄，因此一旦原始进程
            # 对象消失，真正的退出码就不可用了。
            session.exit_code = None

        self._move_to_finished(session)
        return session

    @staticmethod
    def _terminate_host_pid(pid: int) -> None:
        """在不需要原始进程句柄的情况下终止宿主机可见的 PID。"""
        if _IS_WINDOWS:
            os.kill(pid, signal.SIGTERM)
            return

        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except (OSError, ProcessLookupError, PermissionError):
            os.kill(pid, signal.SIGTERM)

    # ----- 启动进程 -----

    @staticmethod
    def _env_temp_dir(env: Any) -> str:
        """返回环境支持的后台任务的可写沙箱临时目录。"""
        get_temp_dir = getattr(env, "get_temp_dir", None)
        if callable(get_temp_dir):
            try:
                temp_dir = get_temp_dir()
                if isinstance(temp_dir, str) and temp_dir.startswith("/"):
                    return temp_dir.rstrip("/") or "/"
            except Exception as exc:
                logger.debug("Could not resolve environment temp dir: %s", exc)
        return "/tmp"

    def spawn_local(
        self,
        command: str,
        cwd: str = None,
        task_id: str = "",
        session_key: str = "",
        env_vars: dict = None,
        use_pty: bool = False,
    ) -> ProcessSession:
        """
        在本地启动后台进程。

        仅用于 TERMINAL_ENV=local。其他后端使用 spawn_via_env()。

        参数:
            use_pty: 如果为 True，通过 ptyprocess 使用伪终端，
                     适用于交互式 CLI 工具（Codex、Claude Code、Python REPL）。
                     如果 ptyprocess 未安装，则回退到 subprocess.Popen。
        """
        session = ProcessSession(
            id=f"proc_{uuid.uuid4().hex[:12]}",
            command=command,
            task_id=task_id,
            session_key=session_key,
            cwd=cwd or os.getcwd(),
            started_at=time.time(),
        )

        if use_pty:
            # 尝试 PTY 模式以支持交互式 CLI 工具
            try:
                if _IS_WINDOWS:
                    from winpty import PtyProcess as _PtyProcessCls
                else:
                    from ptyprocess import PtyProcess as _PtyProcessCls
                user_shell = _find_shell()
                pty_env = _sanitize_subprocess_env(os.environ, env_vars)
                pty_env["PYTHONUNBUFFERED"] = "1"
                pty_proc = _PtyProcessCls.spawn(
                    [user_shell, "-lic", f"set +m; {command}"],
                    cwd=session.cwd,
                    env=pty_env,
                    dimensions=(30, 120),
                )
                session.pid = pty_proc.pid
                # 将 pty 句柄存储在会话上以便读写
                session._pty = pty_proc

                # PTY 读取器线程
                reader = threading.Thread(
                    target=self._pty_reader_loop,
                    args=(session,),
                    daemon=True,
                    name=f"proc-pty-reader-{session.id}",
                )
                session._reader_thread = reader
                reader.start()

                with self._lock:
                    self._prune_if_needed()
                    self._running[session.id] = session

                self._write_checkpoint()
                return session

            except ImportError:
                logger.warning("ptyprocess not installed, falling back to pipe mode")
            except Exception as e:
                logger.warning("PTY spawn failed (%s), falling back to pipe mode", e)

        # 标准 Popen 路径（非 PTY 或 PTY 回退）
        # 使用用户的登录 shell 以与 LocalEnvironment 保持一致 ——
        # 确保 rc 文件被加载，用户工具可用。
        user_shell = _find_shell()
        # 强制 Python 脚本无缓冲输出，以便在后台执行期间可见进度
        # （tqdm/datasets 等库在 stdout 为管道时会缓冲输出，
        # 导致 process(action="poll") 看不到输出）。
        bg_env = _sanitize_subprocess_env(os.environ, env_vars)
        bg_env["PYTHONUNBUFFERED"] = "1"
        proc = subprocess.Popen(
            [user_shell, "-lic", f"set +m; {command}"],
            text=True,
            cwd=session.cwd,
            env=bg_env,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.PIPE,
            preexec_fn=None if _IS_WINDOWS else os.setsid,
        )

        session.process = proc
        session.pid = proc.pid

        # 启动输出读取器线程
        reader = threading.Thread(
            target=self._reader_loop,
            args=(session,),
            daemon=True,
            name=f"proc-reader-{session.id}",
        )
        session._reader_thread = reader
        reader.start()

        with self._lock:
            self._prune_if_needed()
            self._running[session.id] = session

        self._write_checkpoint()
        return session

    def spawn_via_env(
        self,
        env: Any,
        command: str,
        cwd: str = None,
        task_id: str = "",
        session_key: str = "",
        timeout: int = 10,
    ) -> ProcessSession:
        """
        通过非本地环境后端启动后台进程。

        对于 Docker/Singularity/Modal/Daytona/SSH：使用环境的 execute() 接口
        在沙箱内运行命令。我们将命令包装起来以捕获沙箱内的 PID，
        并将输出重定向到沙箱内的日志文件，然后通过后续的 execute() 调用轮询日志。

        相比本地启动能力较弱（没有实时 stdout 管道，没有 stdin），
        但确保命令在正确的沙箱上下文中运行。
        """
        session = ProcessSession(
            id=f"proc_{uuid.uuid4().hex[:12]}",
            command=command,
            task_id=task_id,
            session_key=session_key,
            cwd=cwd,
            started_at=time.time(),
            env_ref=env,
            pid_scope="sandbox",
        )

        # 在沙箱内运行命令并捕获输出
        temp_dir = self._env_temp_dir(env)
        log_path = f"{temp_dir}/hermes_bg_{session.id}.log"
        pid_path = f"{temp_dir}/hermes_bg_{session.id}.pid"
        exit_path = f"{temp_dir}/hermes_bg_{session.id}.exit"
        quoted_command = shlex.quote(command)
        quoted_temp_dir = shlex.quote(temp_dir)
        quoted_log_path = shlex.quote(log_path)
        quoted_pid_path = shlex.quote(pid_path)
        quoted_exit_path = shlex.quote(exit_path)
        bg_command = (
            f"mkdir -p {quoted_temp_dir} && "
            f"( nohup bash -lc {quoted_command} > {quoted_log_path} 2>&1; "
            f"rc=$?; printf '%s\\n' \"$rc\" > {quoted_exit_path} ) & "
            f"echo $! > {quoted_pid_path} && cat {quoted_pid_path}"
        )

        try:
            result = env.execute(bg_command, timeout=timeout)
            output = result.get("output", "").strip()
            # 尝试从输出中提取 PID
            for line in output.splitlines():
                line = line.strip()
                if line.isdigit():
                    session.pid = int(line)
                    break
        except Exception as e:
            session.exited = True
            session.exit_code = -1
            session.output_buffer = f"Failed to start: {e}"

        if not session.exited:
            # 启动一个轮询线程，定期读取日志文件
            reader = threading.Thread(
                target=self._env_poller_loop,
                args=(session, env, log_path, pid_path, exit_path),
                daemon=True,
                name=f"proc-poller-{session.id}",
            )
            session._reader_thread = reader
            reader.start()

        with self._lock:
            self._prune_if_needed()
            self._running[session.id] = session

        self._write_checkpoint()
        return session

    # ----- 读取器/轮询器线程 -----

    def _reader_loop(self, session: ProcessSession):
        """后台线程：从本地 Popen 进程读取 stdout。"""
        first_chunk = True
        try:
            while True:
                chunk = session.process.stdout.read(4096)
                if not chunk:
                    break
                if first_chunk:
                    chunk = self._clean_shell_noise(chunk)
                    first_chunk = False
                with session._lock:
                    session.output_buffer += chunk
                    if len(session.output_buffer) > session.max_output_chars:
                        session.output_buffer = session.output_buffer[-session.max_output_chars:]
                self._check_watch_patterns(session, chunk)
        except Exception as e:
            logger.debug("Process stdout reader ended: %s", e)
        finally:
            # 始终回收子进程以防止僵尸进程。
            try:
                session.process.wait(timeout=5)
            except Exception as e:
                logger.debug("Process wait timed out or failed: %s", e)
            session.exited = True
            session.exit_code = session.process.returncode
            self._move_to_finished(session)

    def _env_poller_loop(
        self, session: ProcessSession, env: Any, log_path: str, pid_path: str, exit_path: str
    ):
        """后台线程：为非本地后端轮询沙箱日志文件。"""
        quoted_log_path = shlex.quote(log_path)
        quoted_pid_path = shlex.quote(pid_path)
        quoted_exit_path = shlex.quote(exit_path)
        prev_output_len = 0  # 跟踪增量以用于监视模式扫描
        while not session.exited:
            time.sleep(2)  # 每 2 秒轮询一次
            try:
                # 从日志文件读取新输出
                result = env.execute(f"cat {quoted_log_path} 2>/dev/null", timeout=10)
                new_output = result.get("output", "")
                if new_output:
                    # 计算增量用于监视模式扫描
                    delta = new_output[prev_output_len:] if len(new_output) > prev_output_len else ""
                    prev_output_len = len(new_output)
                    with session._lock:
                        session.output_buffer = new_output
                        if len(session.output_buffer) > session.max_output_chars:
                            session.output_buffer = session.output_buffer[-session.max_output_chars:]
                    if delta:
                        self._check_watch_patterns(session, delta)

                # 检查进程是否仍在运行
                check = env.execute(
                    f"kill -0 \"$(cat {quoted_pid_path} 2>/dev/null)\" 2>/dev/null; echo $?",
                    timeout=5,
                )
                check_output = check.get("output", "").strip()
                if check_output and check_output.splitlines()[-1].strip() != "0":
                    # 进程已退出 -- 获取包装 shell 捕获的退出码。
                    exit_result = env.execute(
                        f"cat {quoted_exit_path} 2>/dev/null",
                        timeout=5,
                    )
                    exit_str = exit_result.get("output", "").strip()
                    try:
                        session.exit_code = int(exit_str.splitlines()[-1].strip())
                    except (ValueError, IndexError):
                        session.exit_code = -1
                    session.exited = True
                    self._move_to_finished(session)
                    return

            except Exception:
                # 环境可能已不存在（沙箱已被回收等）
                session.exited = True
                session.exit_code = -1
                self._move_to_finished(session)
                return

    def _pty_reader_loop(self, session: ProcessSession):
        """后台线程：从 PTY 进程读取输出。"""
        pty = session._pty
        try:
            while pty.isalive():
                try:
                    chunk = pty.read(4096)
                    if chunk:
                        # ptyprocess 返回 bytes
                        text = chunk if isinstance(chunk, str) else chunk.decode("utf-8", errors="replace")
                        with session._lock:
                            session.output_buffer += text
                            if len(session.output_buffer) > session.max_output_chars:
                                session.output_buffer = session.output_buffer[-session.max_output_chars:]
                        self._check_watch_patterns(session, text)
                except EOFError:
                    break
                except Exception:
                    break
        except Exception as e:
            logger.debug("PTY stdout reader ended: %s", e)

        # 进程已退出
        try:
            pty.wait()
        except Exception as e:
            logger.debug("PTY wait timed out or failed: %s", e)
        session.exited = True
        session.exit_code = pty.exitstatus if hasattr(pty, 'exitstatus') else -1
        self._move_to_finished(session)

    def _move_to_finished(self, session: ProcessSession):
        """将会话从运行中移动到已完成。

        幂等性：如果会话已被移动（例如 kill_process 与读取器线程竞争），
        第二次调用是空操作 — 不会有重复的完成通知入队。
        """
        with self._lock:
            was_running = self._running.pop(session.id, None) is not None
            self._finished[session.id] = session
        self._write_checkpoint()

        # 仅在首次移动时入队完成通知。没有这个保护，
        # kill_process() 和读取器线程都可能调用 _move_to_finished()，
        # 产生重复的 [SYSTEM: ...] 消息。
        if was_running and session.notify_on_complete:
            from tools.ansi_strip import strip_ansi
            output_tail = strip_ansi(session.output_buffer[-2000:]) if session.output_buffer else ""
            self.completion_queue.put({
                "type": "completion",
                "session_id": session.id,
                "command": session.command,
                "exit_code": session.exit_code,
                "output": output_tail,
            })

    # ----- 查询方法 -----

    def is_completion_consumed(self, session_id: str) -> bool:
        """检查完成通知是否已通过 wait/poll/log 被消费。"""
        return session_id in self._completion_consumed

    def get(self, session_id: str) -> Optional[ProcessSession]:
        """通过 ID 获取会话（运行中或已完成）。"""
        with self._lock:
            session = self._running.get(session_id) or self._finished.get(session_id)
        return self._refresh_detached_session(session)

    def poll(self, session_id: str) -> dict:
        """检查后台进程的状态并获取新输出。"""
        from tools.ansi_strip import strip_ansi

        session = self.get(session_id)
        if session is None:
            return {"status": "not_found", "error": f"No process with ID {session_id}"}

        with session._lock:
            output_preview = strip_ansi(session.output_buffer[-1000:]) if session.output_buffer else ""

        result = {
            "session_id": session.id,
            "command": session.command,
            "status": "exited" if session.exited else "running",
            "pid": session.pid,
            "uptime_seconds": int(time.time() - session.started_at),
            "output_preview": output_preview,
        }
        if session.exited:
            result["exit_code"] = session.exit_code
            self._completion_consumed.add(session_id)
        if session.detached:
            result["detached"] = True
            result["note"] = "Process recovered after restart -- output history unavailable"
        return result

    def read_log(self, session_id: str, offset: int = 0, limit: int = 200) -> dict:
        """读取完整输出日志，支持按行分页。"""
        from tools.ansi_strip import strip_ansi

        session = self.get(session_id)
        if session is None:
            return {"status": "not_found", "error": f"No process with ID {session_id}"}

        with session._lock:
            full_output = strip_ansi(session.output_buffer)

        lines = full_output.splitlines()
        total_lines = len(lines)

        # 默认：最后 N 行
        if offset == 0 and limit > 0:
            selected = lines[-limit:]
        else:
            selected = lines[offset:offset + limit]

        result = {
            "session_id": session.id,
            "status": "exited" if session.exited else "running",
            "output": "\n".join(selected),
            "total_lines": total_lines,
            "showing": f"{len(selected)} lines",
        }
        if session.exited:
            self._completion_consumed.add(session_id)
        return result

    def wait(self, session_id: str, timeout: int = None) -> dict:
        """
        阻塞等待直到进程退出、超时或中断。

        参数:
            session_id: 要等待的进程。
            timeout: 最大阻塞秒数。回退到 TERMINAL_TIMEOUT 配置。

        返回:
            包含 status ("exited"、"timeout"、"interrupted"、"not_found")
            和输出快照的字典。
        """
        from tools.ansi_strip import strip_ansi
        from tools.interrupt import is_interrupted as _is_interrupted

        try:
            default_timeout = int(os.getenv("TERMINAL_TIMEOUT", "180"))
        except (ValueError, TypeError):
            default_timeout = 180
        max_timeout = default_timeout
        requested_timeout = timeout
        timeout_note = None

        if requested_timeout and requested_timeout > max_timeout:
            effective_timeout = max_timeout
            timeout_note = (
                f"Requested wait of {requested_timeout}s was clamped "
                f"to configured limit of {max_timeout}s"
            )
        else:
            effective_timeout = requested_timeout or max_timeout

        session = self.get(session_id)
        if session is None:
            return {"status": "not_found", "error": f"No process with ID {session_id}"}

        deadline = time.monotonic() + effective_timeout

        while time.monotonic() < deadline:
            session = self._refresh_detached_session(session)
            if session.exited:
                self._completion_consumed.add(session_id)
                result = {
                    "status": "exited",
                    "exit_code": session.exit_code,
                    "output": strip_ansi(session.output_buffer[-2000:]),
                }
                if timeout_note:
                    result["timeout_note"] = timeout_note
                return result

            if _is_interrupted():
                result = {
                    "status": "interrupted",
                    "output": strip_ansi(session.output_buffer[-1000:]),
                    "note": "User sent a new message -- wait interrupted",
                }
                if timeout_note:
                    result["timeout_note"] = timeout_note
                return result

            time.sleep(1)

        result = {
            "status": "timeout",
            "output": strip_ansi(session.output_buffer[-1000:]),
        }
        if timeout_note:
            result["timeout_note"] = timeout_note
        else:
            result["timeout_note"] = f"Waited {effective_timeout}s, process still running"
        return result

    def kill_process(self, session_id: str) -> dict:
        """终止后台进程。"""
        session = self.get(session_id)
        if session is None:
            return {"status": "not_found", "error": f"No process with ID {session_id}"}

        if session.exited:
            return {
                "status": "already_exited",
                "exit_code": session.exit_code,
            }

        # 通过 PTY、Popen（本地）或 env execute（非本地）终止
        try:
            if session._pty:
                # PTY 进程 -- 通过 ptyprocess 终止
                try:
                    session._pty.terminate(force=True)
                except Exception:
                    if session.pid:
                        os.kill(session.pid, signal.SIGTERM)
            elif session.process:
                # 本地进程 -- 终止进程组
                try:
                    if _IS_WINDOWS:
                        session.process.terminate()
                    else:
                        os.killpg(os.getpgid(session.process.pid), signal.SIGTERM)
                except (ProcessLookupError, PermissionError):
                    session.process.kill()
            elif session.env_ref and session.pid:
                # 非本地 -- 在沙箱内终止
                session.env_ref.execute(f"kill {session.pid} 2>/dev/null", timeout=5)
            elif session.detached and session.pid_scope == "host" and session.pid:
                if not self._is_host_pid_alive(session.pid):
                    with session._lock:
                        session.exited = True
                        session.exit_code = None
                    self._move_to_finished(session)
                    return {
                        "status": "already_exited",
                        "exit_code": session.exit_code,
                    }
                self._terminate_host_pid(session.pid)
            else:
                return {
                    "status": "error",
                    "error": (
                        "Recovered process cannot be killed after restart because "
                        "its original runtime handle is no longer available"
                    ),
                }
            session.exited = True
            session.exit_code = -15  # SIGTERM 信号
            self._move_to_finished(session)
            self._write_checkpoint()
            return {"status": "killed", "session_id": session.id}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def write_stdin(self, session_id: str, data: str) -> dict:
        """向运行中进程的 stdin 发送原始数据（不追加换行符）。"""
        session = self.get(session_id)
        if session is None:
            return {"status": "not_found", "error": f"No process with ID {session_id}"}
        if session.exited:
            return {"status": "already_exited", "error": "Process has already finished"}

        # PTY 模式 -- 通过 pty 句柄写入（期望 bytes）
        if hasattr(session, '_pty') and session._pty:
            try:
                pty_data = data.encode("utf-8") if isinstance(data, str) else data
                session._pty.write(pty_data)
                return {"status": "ok", "bytes_written": len(data)}
            except Exception as e:
                return {"status": "error", "error": str(e)}

        # Popen 模式 -- 通过 stdin 管道写入
        if not session.process or not session.process.stdin:
            return {"status": "error", "error": "Process stdin not available (non-local backend or stdin closed)"}
        try:
            session.process.stdin.write(data)
            session.process.stdin.flush()
            return {"status": "ok", "bytes_written": len(data)}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def submit_stdin(self, session_id: str, data: str = "") -> dict:
        """向运行中进程的 stdin 发送数据 + 换行符（类似按回车键）。"""
        return self.write_stdin(session_id, data + "\n")

    def close_stdin(self, session_id: str) -> dict:
        """关闭运行中进程的 stdin / 发送 EOF 而不终止进程。"""
        session = self.get(session_id)
        if session is None:
            return {"status": "not_found", "error": f"No process with ID {session_id}"}
        if session.exited:
            return {"status": "already_exited", "error": "Process has already finished"}

        if hasattr(session, '_pty') and session._pty:
            try:
                session._pty.sendeof()
                return {"status": "ok", "message": "EOF sent"}
            except Exception as e:
                return {"status": "error", "error": str(e)}

        if not session.process or not session.process.stdin:
            return {"status": "error", "error": "Process stdin not available (non-local backend or stdin closed)"}
        try:
            session.process.stdin.close()
            return {"status": "ok", "message": "stdin closed"}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def list_sessions(self, task_id: str = None) -> list:
        """列出所有运行中和近期已完成的进程。"""
        with self._lock:
            all_sessions = list(self._running.values()) + list(self._finished.values())

        all_sessions = [self._refresh_detached_session(s) for s in all_sessions]

        if task_id:
            all_sessions = [s for s in all_sessions if s.task_id == task_id]

        result = []
        for s in all_sessions:
            entry = {
                "session_id": s.id,
                "command": s.command[:200],
                "cwd": s.cwd,
                "pid": s.pid,
                "started_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(s.started_at)),
                "uptime_seconds": int(time.time() - s.started_at),
                "status": "exited" if s.exited else "running",
                "output_preview": s.output_buffer[-200:] if s.output_buffer else "",
            }
            if s.exited:
                entry["exit_code"] = s.exit_code
            if s.detached:
                entry["detached"] = True
            result.append(entry)
        return result

    # ----- 会话/任务查询（用于网关集成）-----

    def has_active_processes(self, task_id: str) -> bool:
        """检查指定 task_id 是否有活跃（运行中）的进程。"""
        with self._lock:
            sessions = list(self._running.values())

        for session in sessions:
            self._refresh_detached_session(session)

        with self._lock:
            return any(
                s.task_id == task_id and not s.exited
                for s in self._running.values()
            )

    def has_active_for_session(self, session_key: str) -> bool:
        """检查指定网关会话键是否有活跃进程。"""
        with self._lock:
            sessions = list(self._running.values())

        for session in sessions:
            self._refresh_detached_session(session)

        with self._lock:
            return any(
                s.session_key == session_key and not s.exited
                for s in self._running.values()
            )

    def kill_all(self, task_id: str = None) -> int:
        """终止所有运行中的进程，可选按 task_id 过滤。返回终止的数量。"""
        with self._lock:
            targets = [
                s for s in self._running.values()
                if (task_id is None or s.task_id == task_id) and not s.exited
            ]

        killed = 0
        for session in targets:
            result = self.kill_process(session.id)
            if result.get("status") in ("killed", "already_exited"):
                killed += 1
        return killed

    # ----- 清理/淘汰 -----

    def _prune_if_needed(self):
        """如果超过 MAX_PROCESSES 限制，移除最旧的已完成会话。调用时必须持有 _lock。"""
        # 首先清除过期的已完成会话
        now = time.time()
        expired = [
            sid for sid, s in self._finished.items()
            if (now - s.started_at) > FINISHED_TTL_SECONDS
        ]
        for sid in expired:
            del self._finished[sid]

        # 如果仍超过限制，移除最旧的已完成会话
        total = len(self._running) + len(self._finished)
        if total >= MAX_PROCESSES and self._finished:
            oldest_id = min(self._finished, key=lambda sid: self._finished[sid].started_at)
            del self._finished[oldest_id]

    # ----- 检查点（崩溃恢复）-----

    def _write_checkpoint(self):
        """原子性地将运行中的进程元数据写入检查点文件。"""
        try:
            with self._lock:
                entries = []
                for s in self._running.values():
                    if not s.exited:
                        entries.append({
                            "session_id": s.id,
                            "command": s.command,
                            "pid": s.pid,
                            "pid_scope": s.pid_scope,
                            "cwd": s.cwd,
                            "started_at": s.started_at,
                            "task_id": s.task_id,
                            "session_key": s.session_key,
                            "watcher_platform": s.watcher_platform,
                            "watcher_chat_id": s.watcher_chat_id,
                            "watcher_user_id": s.watcher_user_id,
                            "watcher_user_name": s.watcher_user_name,
                            "watcher_thread_id": s.watcher_thread_id,
                            "watcher_interval": s.watcher_interval,
                            "notify_on_complete": s.notify_on_complete,
                            "watch_patterns": s.watch_patterns,
                        })
            
            # 原子写入以避免崩溃时数据损坏
            from utils import atomic_json_write
            atomic_json_write(CHECKPOINT_PATH, entries)
        except Exception as e:
            logger.debug("Failed to write checkpoint file: %s", e, exc_info=True)

    def recover_from_checkpoint(self) -> int:
        """
        在网关启动时，从检查点文件探测 PID。

        返回作为分离状态恢复的进程数量。
        """
        if not CHECKPOINT_PATH.exists():
            return 0

        try:
            entries = json.loads(CHECKPOINT_PATH.read_text(encoding="utf-8"))
        except Exception:
            return 0

        recovered = 0
        for entry in entries:
            pid = entry.get("pid")
            if not pid:
                continue

            pid_scope = entry.get("pid_scope", "host")
            if pid_scope != "host":
                # 沙箱支持的进程在检查点中只保存沙箱内部的 PID，
                # 一旦原始环境句柄消失，这些 PID 对重启后的宿主进程没有意义。
                logger.info(
                    "Skipping recovery for non-host process: %s (pid=%s, scope=%s)",
                    entry.get("command", "unknown")[:60],
                    pid,
                    pid_scope,
                )
                continue

            # 检查 PID 是否仍然存活
            alive = self._is_host_pid_alive(pid)

            if alive:
                session = ProcessSession(
                    id=entry["session_id"],
                    command=entry.get("command", "unknown"),
                    task_id=entry.get("task_id", ""),
                    session_key=entry.get("session_key", ""),
                    pid=pid,
                    pid_scope=pid_scope,
                    cwd=entry.get("cwd"),
                    started_at=entry.get("started_at", time.time()),
                    detached=True,  # 无法读取输出，但可以报告状态和终止
                    watcher_platform=entry.get("watcher_platform", ""),
                    watcher_chat_id=entry.get("watcher_chat_id", ""),
                    watcher_user_id=entry.get("watcher_user_id", ""),
                    watcher_user_name=entry.get("watcher_user_name", ""),
                    watcher_thread_id=entry.get("watcher_thread_id", ""),
                    watcher_interval=entry.get("watcher_interval", 0),
                    notify_on_complete=entry.get("notify_on_complete", False),
                    watch_patterns=entry.get("watch_patterns", []),
                )
                with self._lock:
                    self._running[session.id] = session
                recovered += 1
                logger.info("Recovered detached process: %s (pid=%d)", session.command[:60], pid)

                # 重新入队监视器以便网关可以恢复通知
                if session.watcher_interval > 0:
                    self.pending_watchers.append({
                        "session_id": session.id,
                        "check_interval": session.watcher_interval,
                        "session_key": session.session_key,
                        "platform": session.watcher_platform,
                        "chat_id": session.watcher_chat_id,
                        "user_id": session.watcher_user_id,
                        "user_name": session.watcher_user_name,
                        "thread_id": session.watcher_thread_id,
                        "notify_on_complete": session.notify_on_complete,
                    })

        self._write_checkpoint()

        return recovered


# 模块级单例
process_registry = ProcessRegistry()


# ---------------------------------------------------------------------------
# 注册表 -- "process" 工具的 schema + 处理器
# ---------------------------------------------------------------------------
from tools.registry import registry, tool_error

PROCESS_SCHEMA = {
    "name": "process",
    "description": (
        "Manage background processes started with terminal(background=true). "
        "Actions: 'list' (show all), 'poll' (check status + new output), "
        "'log' (full output with pagination), 'wait' (block until done or timeout), "
        "'kill' (terminate), 'write' (send raw stdin data without newline), "
        "'submit' (send data + Enter, for answering prompts), 'close' (close stdin/send EOF)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "poll", "log", "wait", "kill", "write", "submit", "close"],
                "description": "Action to perform on background processes"
            },
            "session_id": {
                "type": "string",
                "description": "Process session ID (from terminal background output). Required for all actions except 'list'."
            },
            "data": {
                "type": "string",
                "description": "Text to send to process stdin (for 'write' and 'submit' actions)"
            },
            "timeout": {
                "type": "integer",
                "description": "Max seconds to block for 'wait' action. Returns partial output on timeout.",
                "minimum": 1
            },
            "offset": {
                "type": "integer",
                "description": "Line offset for 'log' action (default: last 200 lines)"
            },
            "limit": {
                "type": "integer",
                "description": "Max lines to return for 'log' action",
                "minimum": 1
            }
        },
        "required": ["action"]
    }
}


def _handle_process(args, **kw):
    import json as _json
    task_id = kw.get("task_id")
    action = args.get("action", "")
    # 强制转换为字符串 — 某些模型将 session_id 发送为整数
    session_id = str(args.get("session_id", "")) if args.get("session_id") is not None else ""

    if action == "list":
        return _json.dumps({"processes": process_registry.list_sessions(task_id=task_id)}, ensure_ascii=False)
    elif action in ("poll", "log", "wait", "kill", "write", "submit", "close"):
        if not session_id:
            return tool_error(f"session_id is required for {action}")
        if action == "poll":
            return _json.dumps(process_registry.poll(session_id), ensure_ascii=False)
        elif action == "log":
            return _json.dumps(process_registry.read_log(
                session_id, offset=args.get("offset", 0), limit=args.get("limit", 200)), ensure_ascii=False)
        elif action == "wait":
            return _json.dumps(process_registry.wait(session_id, timeout=args.get("timeout")), ensure_ascii=False)
        elif action == "kill":
            return _json.dumps(process_registry.kill_process(session_id), ensure_ascii=False)
        elif action == "write":
            return _json.dumps(process_registry.write_stdin(session_id, str(args.get("data", ""))), ensure_ascii=False)
        elif action == "submit":
            return _json.dumps(process_registry.submit_stdin(session_id, str(args.get("data", ""))), ensure_ascii=False)
        elif action == "close":
            return _json.dumps(process_registry.close_stdin(session_id), ensure_ascii=False)
    return tool_error(f"Unknown process action: {action}. Use: list, poll, log, wait, kill, write, submit, close")


registry.register(
    name="process",
    toolset="terminal",
    schema=PROCESS_SCHEMA,
    handler=_handle_process,
    emoji="⚙️",
)
