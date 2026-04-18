"""所有 Hermes 执行环境后端的基类。

采用"每次调用创建新进程"的统一模型：每条命令都会启动一个新的 ``bash -c`` 进程。
会话快照（环境变量、函数、别名）在初始化时捕获一次，并在每条命令执行前重新加载。
工作目录（CWD）通过标准输出中的标记（远程后端）或临时文件（本地后端）来持久化。
"""

import json
import logging
import os
import shlex
import subprocess
import threading
import time
import uuid
from abc import ABC, abstractmethod
from pathlib import Path
from typing import IO, Callable, Protocol

from hermes_constants import get_hermes_home
from tools.interrupt import is_interrupted

logger = logging.getLogger(__name__)

# 线程本地的活动回调。代理（agent）在工具调用前设置此回调，
# 以便长时间运行的 _wait_for_process 循环可以向网关报告存活状态。
_activity_callback_local = threading.local()


def set_activity_callback(cb: Callable[[str], None] | None) -> None:
    """注册一个回调函数，由 _wait_for_process 定期调用。"""
    _activity_callback_local.callback = cb


def _get_activity_callback() -> Callable[[str], None] | None:
    return getattr(_activity_callback_local, "callback", None)


def touch_activity_if_due(
    state: dict,
    label: str,
) -> None:
    """按照限流策略触发活动回调，同一 ``state['interval']`` 秒内最多触发一次。

    *state* 必须包含 ``last_touch``（单调时钟时间戳）和 ``start``
    （操作开始的单调时钟时间戳）。可选的 ``interval`` 键可覆盖默认的 10 秒频率。

    此函数会吞掉所有异常，调用方无需自行 try/except。
    """
    now = time.monotonic()
    interval = state.get("interval", 10.0)
    if now - state["last_touch"] < interval:
        return
    state["last_touch"] = now
    try:
        cb = _get_activity_callback()
        if cb:
            elapsed = int(now - state["start"])
            cb(f"{label} ({elapsed}s elapsed)")
    except Exception:
        pass


def get_sandbox_dir() -> Path:
    """返回宿主机侧所有沙箱存储的根目录（Docker 工作空间、
    Singularity overlay/SIF 缓存等）。

    可通过 TERMINAL_SANDBOX_DIR 环境变量配置。默认为 {HERMES_HOME}/sandboxes/。
    """
    custom = os.getenv("TERMINAL_SANDBOX_DIR")
    if custom:
        p = Path(custom)
    else:
        p = get_hermes_home() / "sandboxes"
    p.mkdir(parents=True, exist_ok=True)
    return p


# ---------------------------------------------------------------------------
# 共享常量和工具函数
# ---------------------------------------------------------------------------


def _pipe_stdin(proc: subprocess.Popen, data: str) -> None:
    """在守护线程中将 *data* 写入 proc.stdin，避免管道缓冲区死锁。"""

    def _write():
        try:
            proc.stdin.write(data)
            proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass

    threading.Thread(target=_write, daemon=True).start()


def _popen_bash(
    cmd: list[str], stdin_data: str | None = None, **kwargs
) -> subprocess.Popen:
    """使用标准的 stdout/stderr/stdin 配置启动子进程。

    如果提供了 *stdin_data*，将通过 :func:`_pipe_stdin` 异步写入。
    有特殊 Popen 需求的后端（例如 local 的 ``preexec_fn``）可以绕过此函数，
    直接调用 :func:`_pipe_stdin`。
    """
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.PIPE if stdin_data is not None else subprocess.DEVNULL,
        text=True,
        **kwargs,
    )
    if stdin_data is not None:
        _pipe_stdin(proc, stdin_data)
    return proc


def _load_json_store(path: Path) -> dict:
    """加载 JSON 文件为字典，任何错误时返回 ``{}``。"""
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass
    return {}


def _save_json_store(path: Path, data: dict) -> None:
    """将 *data* 以格式化 JSON 的形式写入 *path*。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


def _file_mtime_key(host_path: str) -> tuple[float, int] | None:
    """返回 ``(mtime, size)`` 用于缓存比较，无法读取时返回 ``None``。"""
    try:
        st = Path(host_path).stat()
        return (st.st_mtime, st.st_size)
    except OSError:
        return None


# ---------------------------------------------------------------------------
# ProcessHandle 协议
# ---------------------------------------------------------------------------


class ProcessHandle(Protocol):
    """每个后端的 _run_bash() 必须返回的鸭子类型接口。

    subprocess.Popen 原生满足此协议。SDK 后端（Modal、Daytona）
    返回 _ThreadedProcessHandle 来适配其阻塞调用。
    """

    def poll(self) -> int | None: ...
    def kill(self) -> None: ...
    def wait(self, timeout: float | None = None) -> int: ...

    @property
    def stdout(self) -> IO[str] | None: ...

    @property
    def returncode(self) -> int | None: ...


class _ThreadedProcessHandle:
    """SDK 后端（Modal、Daytona）的适配器，这些后端没有真正的子进程。

    将阻塞的 ``exec_fn() -> (output_str, exit_code)`` 包装在后台线程中，
    暴露兼容 ProcessHandle 的接口。可选的 ``cancel_fn`` 在 ``kill()`` 时
    被调用，用于后端特定的取消操作（例如 Modal 的 sandbox.terminate、
    Daytona 的 sandbox.stop）。
    """

    def __init__(
        self,
        exec_fn: Callable[[], tuple[str, int]],
        cancel_fn: Callable[[], None] | None = None,
    ):
        self._cancel_fn = cancel_fn
        self._done = threading.Event()
        self._returncode: int | None = None
        self._error: Exception | None = None

        # 用于 stdout 的管道 - _wait_for_process 中的排空线程读取读端。
        read_fd, write_fd = os.pipe()
        self._stdout = os.fdopen(read_fd, "r", encoding="utf-8", errors="replace")
        self._write_fd = write_fd

        def _worker():
            try:
                output, exit_code = exec_fn()
                self._returncode = exit_code
                # 将输出写入管道，以便排空线程能够读取。
                try:
                    os.write(self._write_fd, output.encode("utf-8", errors="replace"))
                except OSError:
                    pass
            except Exception as exc:
                self._error = exc
                self._returncode = 1
            finally:
                try:
                    os.close(self._write_fd)
                except OSError:
                    pass
                self._done.set()

        t = threading.Thread(target=_worker, daemon=True)
        t.start()

    @property
    def stdout(self):
        return self._stdout

    @property
    def returncode(self) -> int | None:
        return self._returncode

    def poll(self) -> int | None:
        return self._returncode if self._done.is_set() else None

    def kill(self):
        if self._cancel_fn:
            try:
                self._cancel_fn()
            except Exception:
                pass

    def wait(self, timeout: float | None = None) -> int:
        self._done.wait(timeout=timeout)
        return self._returncode


# ---------------------------------------------------------------------------
# 远程后端的工作目录标记
# ---------------------------------------------------------------------------


def _cwd_marker(session_id: str) -> str:
    return f"__HERMES_CWD_{session_id}__"


# ---------------------------------------------------------------------------
# BaseEnvironment 基类
# ---------------------------------------------------------------------------


class BaseEnvironment(ABC):
    """所有 Hermes 后端的通用接口和统一执行流程。

    子类需实现 ``_run_bash()`` 和 ``cleanup()``。基类提供 ``execute()`` 方法，
    包含会话快照加载、工作目录跟踪、中断处理和超时控制。
    """

    # 将标准输入作为 heredoc 嵌入的后端（Modal、Daytona）需设置此属性。
    _stdin_mode: str = "pipe"  # "pipe" 或 "heredoc"

    # 快照创建超时时间（可在冷启动较慢的后端中覆盖）。
    _snapshot_timeout: int = 30

    def get_temp_dir(self) -> str:
        """返回后端用于存放会话产物的临时目录。

        大多数沙箱后端使用目标环境内部的 ``/tmp``。
        LocalEnvironment 在 Termux 等平台上会覆盖此方法，因为这些平台可能
        没有 ``/tmp``，``TMPDIR`` 才是可移植的可写位置。
        """
        return "/tmp"

    def __init__(self, cwd: str, timeout: int, env: dict = None):
        self.cwd = cwd
        self.timeout = timeout
        self.env = env or {}

        self._session_id = uuid.uuid4().hex[:12]
        temp_dir = self.get_temp_dir().rstrip("/") or "/"
        self._snapshot_path = f"{temp_dir}/hermes-snap-{self._session_id}.sh"
        self._cwd_file = f"{temp_dir}/hermes-cwd-{self._session_id}.txt"
        self._cwd_marker = _cwd_marker(self._session_id)
        self._snapshot_ready = False

    # ------------------------------------------------------------------
    # 抽象方法
    # ------------------------------------------------------------------

    def _run_bash(
        self,
        cmd_string: str,
        *,
        login: bool = False,
        timeout: int = 120,
        stdin_data: str | None = None,
    ) -> ProcessHandle:
        """启动一个 bash 进程来运行 *cmd_string*。

        返回一个 ProcessHandle（subprocess.Popen 或 _ThreadedProcessHandle）。
        必须由每个后端覆盖实现。
        """
        raise NotImplementedError(f"{type(self).__name__} must implement _run_bash()")

    @abstractmethod
    def cleanup(self):
        """释放后端资源（容器、实例、连接）。"""
        ...

    # ------------------------------------------------------------------
    # 会话快照（init_session）
    # ------------------------------------------------------------------

    def init_session(self):
        """将登录 shell 环境捕获到快照文件中。

        在后端构造后调用一次。成功时将 ``_snapshot_ready`` 设为 True，
        后续命令将加载快照而非使用 ``bash -l`` 运行。
        """
        # 完整捕获：环境变量、函数（过滤后）、别名、shell 选项。
        bootstrap = (
            f"export -p > {self._snapshot_path}\n"
            f"declare -f | grep -vE '^_[^_]' >> {self._snapshot_path}\n"
            f"alias -p >> {self._snapshot_path}\n"
            f"echo 'shopt -s expand_aliases' >> {self._snapshot_path}\n"
            f"echo 'set +e' >> {self._snapshot_path}\n"
            f"echo 'set +u' >> {self._snapshot_path}\n"
            f"pwd -P > {self._cwd_file} 2>/dev/null || true\n"
            f"printf '\\n{self._cwd_marker}%s{self._cwd_marker}\\n' \"$(pwd -P)\"\n"
        )
        try:
            proc = self._run_bash(bootstrap, login=True, timeout=self._snapshot_timeout)
            result = self._wait_for_process(proc, timeout=self._snapshot_timeout)
            self._snapshot_ready = True
            self._update_cwd(result)
            logger.info(
                "Session snapshot created (session=%s, cwd=%s)",
                self._session_id,
                self.cwd,
            )
        except Exception as exc:
            logger.warning(
                "init_session failed (session=%s): %s — "
                "falling back to bash -l per command",
                self._session_id,
                exc,
            )
            self._snapshot_ready = False

    # ------------------------------------------------------------------
    # 命令封装
    # ------------------------------------------------------------------

    def _wrap_command(self, command: str, cwd: str) -> str:
        """构建完整的 bash 脚本：加载快照、切换目录、执行命令、
        重新导出环境变量、输出工作目录标记。"""
        escaped = command.replace("'", "'\\''")

        parts = []

        # 加载快照（来自前一条命令的环境变量）
        if self._snapshot_ready:
            parts.append(f"source {self._snapshot_path} 2>/dev/null || true")

        # 切换到工作目录 - 让 bash 原生展开 ~ 符号
        quoted_cwd = (
            shlex.quote(cwd) if cwd != "~" and not cwd.startswith("~/") else cwd
        )
        parts.append(f"cd {quoted_cwd} || exit 126")

        # 执行实际命令
        parts.append(f"eval '{escaped}'")
        parts.append("__hermes_ec=$?")

        # 将环境变量重新导出到快照文件（并发调用时后写入者覆盖先写入者）
        if self._snapshot_ready:
            parts.append(f"export -p > {self._snapshot_path} 2>/dev/null || true")

        # 将工作目录写入文件（本地后端读取）和标准输出标记（远程后端解析）
        parts.append(f"pwd -P > {self._cwd_file} 2>/dev/null || true")
        # 使用独立行输出标记。开头的 \n 确保即使命令没有以换行符结尾
        # （如 printf 'exact'），标记也从新行开始。我们会在
        # _extract_cwd_from_output 中去掉这个注入的换行符。
        parts.append(
            f"printf '\\n{self._cwd_marker}%s{self._cwd_marker}\\n' \"$(pwd -P)\""
        )
        parts.append("exit $__hermes_ec")

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # 标准输入 heredoc 嵌入（用于 SDK 后端）
    # ------------------------------------------------------------------

    @staticmethod
    def _embed_stdin_heredoc(command: str, stdin_data: str) -> str:
        """将 stdin_data 作为 shell heredoc 附加到命令字符串末尾。"""
        delimiter = f"HERMES_STDIN_{uuid.uuid4().hex[:12]}"
        return f"{command} << '{delimiter}'\n{stdin_data}\n{delimiter}"

    # ------------------------------------------------------------------
    # 进程生命周期管理
    # ------------------------------------------------------------------

    def _wait_for_process(self, proc: ProcessHandle, timeout: int = 120) -> dict:
        """基于轮询的等待，带有中断检查和标准输出排空功能。

        所有后端共享此方法 - 不被覆盖。

        在进程运行期间每 10 秒触发一次 ``activity_callback``（如果已设置），
        以防止网关的空闲超时机制终止长时间运行的命令。
        """
        output_chunks: list[str] = []

        def _drain():
            try:
                for line in proc.stdout:
                    output_chunks.append(line)
            except UnicodeDecodeError:
                output_chunks.clear()
                output_chunks.append(
                    "[binary output detected — raw bytes not displayable]"
                )
            except (ValueError, OSError):
                pass

        drain_thread = threading.Thread(target=_drain, daemon=True)
        drain_thread.start()
        deadline = time.monotonic() + timeout
        _now = time.monotonic()
        _activity_state = {
            "last_touch": _now,
            "start": _now,
        }

        while proc.poll() is None:
            if is_interrupted():
                self._kill_process(proc)
                drain_thread.join(timeout=2)
                return {
                    "output": "".join(output_chunks) + "\n[Command interrupted]",
                    "returncode": 130,
                }
            if time.monotonic() > deadline:
                self._kill_process(proc)
                drain_thread.join(timeout=2)
                partial = "".join(output_chunks)
                timeout_msg = f"\n[Command timed out after {timeout}s]"
                return {
                    "output": partial + timeout_msg
                    if partial
                    else timeout_msg.lstrip(),
                    "returncode": 124,
                }
            # 定期发送活动信号，让网关知道我们仍在运行
            touch_activity_if_due(_activity_state, "terminal command running")
            time.sleep(0.2)

        drain_thread.join(timeout=5)

        try:
            proc.stdout.close()
        except Exception:
            pass

        return {"output": "".join(output_chunks), "returncode": proc.returncode}

    def _kill_process(self, proc: ProcessHandle):
        """终止进程。子类可覆盖此方法以实现进程组级别的终止。"""
        try:
            proc.kill()
        except (ProcessLookupError, PermissionError, OSError):
            pass

    # ------------------------------------------------------------------
    # 工作目录提取
    # ------------------------------------------------------------------

    def _update_cwd(self, result: dict):
        """从命令输出中提取工作目录。本地后端可覆盖为基于文件的读取方式。"""
        self._extract_cwd_from_output(result)

    def _extract_cwd_from_output(self, result: dict):
        """从标准输出中解析 __HERMES_CWD_{session}__ 标记。

        更新 self.cwd 并从 result["output"] 中去除标记。
        远程后端（Docker、SSH、Modal、Daytona、Singularity）使用此方法。
        """
        output = result.get("output", "")
        marker = self._cwd_marker
        last = output.rfind(marker)
        if last == -1:
            return

        # 在此关闭标记之前，查找对应的开始标记
        search_start = max(0, last - 4096)  # 工作目录路径不会超过 4KB
        first = output.rfind(marker, search_start, last)
        if first == -1 or first == last:
            return

        cwd_path = output[first + len(marker) : last].strip()
        if cwd_path:
            self.cwd = cwd_path

        # 去除标记行以及我们在它前面注入的 \n。
        # 封装器输出的格式为：printf '\n__MARKER__%s__MARKER__\n'
        # 所以输出看起来是：<命令输出>\n__MARKER__路径__MARKER__\n
        # 我们需要删除从注入的 \n 开始到标记结束的所有内容。
        line_start = output.rfind("\n", 0, first)
        if line_start == -1:
            line_start = first
        line_end = output.find("\n", last + len(marker))
        line_end = line_end + 1 if line_end != -1 else len(output)

        result["output"] = output[:line_start] + output[line_end:]

    # ------------------------------------------------------------------
    # 钩子方法
    # ------------------------------------------------------------------

    def _before_execute(self) -> None:
        """每次命令执行前调用的钩子。

        远程后端（SSH、Modal、Daytona）覆盖此方法以触发 FileSyncManager
        进行文件同步。bind mount 后端（Docker、Singularity）和本地后端
        不需要文件同步 - 宿主机文件系统在容器/进程内直接可见。
        """
        pass

    # ------------------------------------------------------------------
    # 统一的 execute() 方法
    # ------------------------------------------------------------------

    def execute(
        self,
        command: str,
        cwd: str = "",
        *,
        timeout: int | None = None,
        stdin_data: str | None = None,
    ) -> dict:
        """执行命令，返回 {"output": str, "returncode": int}。"""
        self._before_execute()

        exec_command, sudo_stdin = self._prepare_command(command)
        effective_timeout = timeout or self.timeout
        effective_cwd = cwd or self.cwd

        # 合并 sudo 标准输入和调用方标准输入
        if sudo_stdin is not None and stdin_data is not None:
            effective_stdin = sudo_stdin + stdin_data
        elif sudo_stdin is not None:
            effective_stdin = sudo_stdin
        else:
            effective_stdin = stdin_data

        # 对于需要的后端，将标准输入嵌入为 heredoc
        if effective_stdin and self._stdin_mode == "heredoc":
            exec_command = self._embed_stdin_heredoc(exec_command, effective_stdin)
            effective_stdin = None

        wrapped = self._wrap_command(exec_command, effective_cwd)

        # 如果快照创建失败，使用登录 shell（这样用户的 profile 仍会被加载）
        login = not self._snapshot_ready

        proc = self._run_bash(
            wrapped, login=login, timeout=effective_timeout, stdin_data=effective_stdin
        )
        result = self._wait_for_process(proc, timeout=effective_timeout)
        self._update_cwd(result)

        return result

    # ------------------------------------------------------------------
    # 共享辅助方法
    # ------------------------------------------------------------------

    def stop(self):
        """cleanup 的别名（兼容旧版调用方）。"""
        self.cleanup()

    def __del__(self):
        try:
            self.cleanup()
        except Exception:
            pass

    def _prepare_command(self, command: str) -> tuple[str, str | None]:
        """当 SUDO_PASSWORD 可用时，转换 sudo 命令。"""
        from tools.terminal_tool import _transform_sudo_command

        return _transform_sudo_command(command)

