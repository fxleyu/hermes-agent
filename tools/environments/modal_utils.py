"""Modal 传输层的 Hermes 侧共享执行流程。

本模块刻意限制在 Hermes 边界内：
- 命令准备
- 工作目录/超时时间规范化
- 标准输入/sudo 的 shell 封装
- 统一的结果格式
- 中断/取消轮询

直接 Modal 和托管 Modal 在各自的模块中保留独立的传输逻辑、持久化和信任边界决策。
"""

from __future__ import annotations

import shlex
import time
import uuid
from abc import abstractmethod
from dataclasses import dataclass
from typing import Any

from tools.environments.base import BaseEnvironment
from tools.interrupt import is_interrupted


@dataclass(frozen=True)
class PreparedModalExec:
    """传递给特定传输层执行器的规范化命令数据。"""

    command: str
    cwd: str
    timeout: int
    stdin_data: str | None = None


@dataclass(frozen=True)
class ModalExecStart:
    """启动 exec 后传输层的响应。"""

    handle: Any | None = None
    immediate_result: dict | None = None


def wrap_modal_stdin_heredoc(command: str, stdin_data: str) -> str:
    """为不支持 stdin 管道的传输层，将 stdin 作为 shell heredoc 附加。"""
    marker = f"HERMES_EOF_{uuid.uuid4().hex[:8]}"
    while marker in stdin_data:
        marker = f"HERMES_EOF_{uuid.uuid4().hex[:8]}"
    return f"{command} << '{marker}'\n{stdin_data}\n{marker}"


def wrap_modal_sudo_pipe(command: str, sudo_stdin: str) -> str:
    """为不支持直接 stdin 管道的传输层，通过 shell 管道传递 sudo 密码。"""
    return f"printf '%s\\n' {shlex.quote(sudo_stdin.rstrip())} | {command}"


class BaseModalExecutionEnvironment(BaseEnvironment):
    """*托管* Modal 传输层的执行流程。

    此类刻意覆盖了 :meth:`BaseEnvironment.execute`，因为 tool-gateway 在
    服务器端处理命令准备、工作目录跟踪和环境快照管理。基类的
    ``_wrap_command`` / ``_wait_for_process`` / 快照机制在此不适用 -
    网关承担了这些职责。具体子类参见 ``ManagedModalEnvironment``。
    """

    _stdin_mode = "payload"
    _poll_interval_seconds = 0.25
    _client_timeout_grace_seconds: float | None = None
    _interrupt_output = "[Command interrupted]"
    _unexpected_error_prefix = "Modal execution error"

    def execute(
        self,
        command: str,
        cwd: str = "",
        *,
        timeout: int | None = None,
        stdin_data: str | None = None,
    ) -> dict:
        self._before_execute()
        prepared = self._prepare_modal_exec(
            command,
            cwd=cwd,
            timeout=timeout,
            stdin_data=stdin_data,
        )

        try:
            start = self._start_modal_exec(prepared)
        except Exception as exc:
            return self._error_result(f"{self._unexpected_error_prefix}: {exc}")

        if start.immediate_result is not None:
            return start.immediate_result

        if start.handle is None:
            return self._error_result(
                f"{self._unexpected_error_prefix}: transport did not return an exec handle"
            )

        deadline = None
        if self._client_timeout_grace_seconds is not None:
            deadline = time.monotonic() + prepared.timeout + self._client_timeout_grace_seconds

        _now = time.monotonic()
        _activity_state = {
            "last_touch": _now,
            "start": _now,
        }

        while True:
            if is_interrupted():
                try:
                    self._cancel_modal_exec(start.handle)
                except Exception:
                    pass
                return self._result(self._interrupt_output, 130)

            try:
                result = self._poll_modal_exec(start.handle)
            except Exception as exc:
                return self._error_result(f"{self._unexpected_error_prefix}: {exc}")

            if result is not None:
                return result

            if deadline is not None and time.monotonic() >= deadline:
                try:
                    self._cancel_modal_exec(start.handle)
                except Exception:
                    pass
                return self._timeout_result_for_modal(prepared.timeout)

            # 定期发送活动信号，让网关知道我们仍在运行
            try:
                from tools.environments.base import touch_activity_if_due
                touch_activity_if_due(_activity_state, "modal command running")
            except Exception:
                pass

            time.sleep(self._poll_interval_seconds)

    def _before_execute(self) -> None:
        """供需要预执行同步或验证的后端使用的钩子。"""
        pass

    def _prepare_modal_exec(
        self,
        command: str,
        *,
        cwd: str = "",
        timeout: int | None = None,
        stdin_data: str | None = None,
    ) -> PreparedModalExec:
        effective_cwd = cwd or self.cwd
        effective_timeout = timeout or self.timeout

        exec_command = command
        exec_stdin = stdin_data if self._stdin_mode == "payload" else None
        if stdin_data is not None and self._stdin_mode == "heredoc":
            exec_command = wrap_modal_stdin_heredoc(exec_command, stdin_data)

        exec_command, sudo_stdin = self._prepare_command(exec_command)
        if sudo_stdin is not None:
            exec_command = wrap_modal_sudo_pipe(exec_command, sudo_stdin)

        return PreparedModalExec(
            command=exec_command,
            cwd=effective_cwd,
            timeout=effective_timeout,
            stdin_data=exec_stdin,
        )

    def _result(self, output: str, returncode: int) -> dict:
        return {
            "output": output,
            "returncode": returncode,
        }

    def _error_result(self, output: str) -> dict:
        return self._result(output, 1)

    def _timeout_result_for_modal(self, timeout: int) -> dict:
        return self._result(f"Command timed out after {timeout}s", 124)

    @abstractmethod
    def _start_modal_exec(self, prepared: PreparedModalExec) -> ModalExecStart:
        """启动特定传输层的 exec。"""

    @abstractmethod
    def _poll_modal_exec(self, handle: Any) -> dict | None:
        """完成时返回最终结果字典，否则返回 ``None``。"""

    @abstractmethod
    def _cancel_modal_exec(self, handle: Any) -> None:
        """取消或终止正在进行的传输层 exec。"""
