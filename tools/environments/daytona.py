"""Daytona 云端执行环境。

使用 Daytona Python SDK 在云端沙箱中运行命令。
支持持久化沙箱：启用时，清理操作会停止沙箱而非删除，下次创建时恢复运行，
从而在会话之间保留文件系统状态。
"""

import logging
import math
import os
import shlex
import threading
from pathlib import Path

from tools.environments.base import (
    BaseEnvironment,
    _ThreadedProcessHandle,
)
from tools.environments.file_sync import (
    FileSyncManager,
    iter_sync_files,
    quoted_mkdir_command,
    quoted_rm_command,
    unique_parent_dirs,
)

logger = logging.getLogger(__name__)


class DaytonaEnvironment(BaseEnvironment):
    """Daytona 云端沙箱执行后端。

    每次调用创建新进程：通过 _ThreadedProcessHandle 封装阻塞的 SDK 调用。
    cancel_fn 连接到 sandbox.stop() 以支持中断。
    保留 shell 超时封装器（SDK 超时不可靠）。
    """

    _stdin_mode = "heredoc"

    def __init__(
        self,
        image: str,
        cwd: str = "/home/daytona",
        timeout: int = 60,
        cpu: int = 1,
        memory: int = 5120,
        disk: int = 10240,
        persistent_filesystem: bool = True,
        task_id: str = "default",
    ):
        requested_cwd = cwd
        super().__init__(cwd=cwd, timeout=timeout)

        from daytona import (
            Daytona,
            CreateSandboxFromImageParams,
            DaytonaError,
            Resources,
            SandboxState,
        )

        self._persistent = persistent_filesystem
        self._task_id = task_id
        self._SandboxState = SandboxState
        self._daytona = Daytona()
        self._sandbox = None
        self._lock = threading.Lock()

        memory_gib = max(1, math.ceil(memory / 1024))
        disk_gib = max(1, math.ceil(disk / 1024))
        if disk_gib > 10:
            logger.warning(
                "Daytona: requested disk (%dGB) exceeds platform limit (10GB). "
                "Capping to 10GB.", disk_gib,
            )
            disk_gib = 10
        resources = Resources(cpu=cpu, memory=memory_gib, disk=disk_gib)

        labels = {"hermes_task_id": task_id}
        sandbox_name = f"hermes-{task_id}"

        if self._persistent:
            try:
                self._sandbox = self._daytona.get(sandbox_name)
                self._sandbox.start()
                logger.info("Daytona: resumed sandbox %s for task %s",
                            self._sandbox.id, task_id)
            except DaytonaError:
                self._sandbox = None
            except Exception as e:
                logger.warning("Daytona: failed to resume sandbox for task %s: %s",
                               task_id, e)
                self._sandbox = None

            if self._sandbox is None:
                try:
                    page = self._daytona.list(labels=labels, page=1, limit=1)
                    if page.items:
                        self._sandbox = page.items[0]
                        self._sandbox.start()
                        logger.info("Daytona: resumed legacy sandbox %s for task %s",
                                    self._sandbox.id, task_id)
                except Exception as e:
                    logger.debug("Daytona: no legacy sandbox found for task %s: %s",
                                 task_id, e)
                    self._sandbox = None

        if self._sandbox is None:
            self._sandbox = self._daytona.create(
                CreateSandboxFromImageParams(
                    image=image,
                    name=sandbox_name,
                    labels=labels,
                    auto_stop_interval=0,
                    resources=resources,
                )
            )
            logger.info("Daytona: created sandbox %s for task %s",
                        self._sandbox.id, task_id)

        # 检测远程 home 目录
        self._remote_home = "/root"
        try:
            home = self._sandbox.process.exec("echo $HOME").result.strip()
            if home:
                self._remote_home = home
                if requested_cwd in ("~", "/home/daytona"):
                    self.cwd = home
        except Exception:
            pass
        logger.info("Daytona: resolved home to %s, cwd to %s", self._remote_home, self.cwd)

        self._sync_manager = FileSyncManager(
            get_files_fn=lambda: iter_sync_files(f"{self._remote_home}/.hermes"),
            upload_fn=self._daytona_upload,
            delete_fn=self._daytona_delete,
            bulk_upload_fn=self._daytona_bulk_upload,
            bulk_download_fn=self._daytona_bulk_download,
        )
        self._sync_manager.sync(force=True)
        self.init_session()

    def _daytona_upload(self, host_path: str, remote_path: str) -> None:
        """通过 Daytona SDK 上传单个文件。"""
        parent = str(Path(remote_path).parent)
        self._sandbox.process.exec(f"mkdir -p {parent}")
        self._sandbox.fs.upload_file(host_path, remote_path)

    def _daytona_bulk_upload(self, files: list[tuple[str, str]]) -> None:
        """通过 Daytona SDK 在单次 HTTP 调用中批量上传文件。

        使用 ``sandbox.fs.upload_files()`` 将所有文件合并到一个 multipart POST 中，
        避免逐文件的 TLS/HTTP 开销（约 580 个文件从约 5 分钟缩减到不到 2 秒）。
        """
        from daytona.common.filesystem import FileUpload

        if not files:
            return

        parents = unique_parent_dirs(files)
        if parents:
            self._sandbox.process.exec(quoted_mkdir_command(parents))

        uploads = [
            FileUpload(source=host_path, destination=remote_path)
            for host_path, remote_path in files
        ]
        self._sandbox.fs.upload_files(uploads)

    def _daytona_bulk_download(self, dest: Path) -> None:
        """将远程 .hermes/ 目录作为 tar 归档下载。"""
        rel_base = f"{self._remote_home}/.hermes".lstrip("/")
        # 使用 PID 后缀的远程临时路径，避免 sync_back 并发执行时
        # 对同一沙箱发生冲突（例如部分失败后重试）。
        remote_tar = f"/tmp/.hermes_sync.{os.getpid()}.tar"
        self._sandbox.process.exec(
            f"tar cf {shlex.quote(remote_tar)} -C / {shlex.quote(rel_base)}"
        )
        self._sandbox.fs.download_file(remote_tar, str(dest))
        # 清理远程临时文件
        try:
            self._sandbox.process.exec(f"rm -f {shlex.quote(remote_tar)}")
        except Exception:
            pass  # 尽力清理

    def _daytona_delete(self, remote_paths: list[str]) -> None:
        """通过 SDK exec 批量删除远程文件。"""
        self._sandbox.process.exec(quoted_rm_command(remote_paths))

    # ------------------------------------------------------------------
    # 沙箱生命周期管理
    # ------------------------------------------------------------------

    def _ensure_sandbox_ready(self) -> None:
        """如果沙箱已停止（例如被之前的中断操作停止），则重新启动。"""
        self._sandbox.refresh_data()
        if self._sandbox.state in (self._SandboxState.STOPPED, self._SandboxState.ARCHIVED):
            self._sandbox.start()
            logger.info("Daytona: restarted sandbox %s", self._sandbox.id)

    def _before_execute(self) -> None:
        """确保沙箱就绪，然后通过 FileSyncManager 同步文件。"""
        with self._lock:
            self._ensure_sandbox_ready()
        self._sync_manager.sync()

    def _run_bash(self, cmd_string: str, *, login: bool = False,
                  timeout: int = 120,
                  stdin_data: str | None = None):
        """返回一个封装了阻塞 Daytona SDK 调用的 _ThreadedProcessHandle。"""
        sandbox = self._sandbox
        lock = self._lock

        def cancel():
            with lock:
                try:
                    sandbox.stop()
                except Exception:
                    pass

        if login:
            shell_cmd = f"bash -l -c {shlex.quote(cmd_string)}"
        else:
            shell_cmd = f"bash -c {shlex.quote(cmd_string)}"

        def exec_fn() -> tuple[str, int]:
            response = sandbox.process.exec(shell_cmd, timeout=timeout)
            return (response.result or "", response.exit_code)

        return _ThreadedProcessHandle(exec_fn, cancel_fn=cancel)

    def cleanup(self):
        with self._lock:
            if self._sandbox is None:
                return

            # 在关闭前将远程变更同步回宿主机。在锁内运行（且在
            # _sandbox is None 检查之后），避免对已清理完毕的环境触发
            # sync_back，否则会导致对空沙箱的 3 次重试风暴。
            if self._sync_manager:
                logger.info("Daytona: syncing files from sandbox...")
                try:
                    self._sync_manager.sync_back()
                except Exception as e:
                    logger.warning("Daytona: sync_back failed: %s", e)

            try:
                if self._persistent:
                    self._sandbox.stop()
                    logger.info("Daytona: stopped sandbox %s (filesystem preserved)",
                                self._sandbox.id)
                else:
                    self._daytona.delete(self._sandbox)
                    logger.info("Daytona: deleted sandbox %s", self._sandbox.id)
            except Exception as e:
                logger.warning("Daytona: cleanup failed: %s", e)
            self._sandbox = None
