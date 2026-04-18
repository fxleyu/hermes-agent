"""SSH 远程执行环境，使用 ControlMaster 实现连接复用和持久化。"""

import logging
import os
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path

from tools.environments.base import BaseEnvironment, _popen_bash
from tools.environments.file_sync import (
    FileSyncManager,
    iter_sync_files,
    quoted_mkdir_command,
    quoted_rm_command,
    unique_parent_dirs,
)

logger = logging.getLogger(__name__)


def _ensure_ssh_available() -> None:
    """当 SSH 客户端不可用时，立即以清晰的错误信息失败。"""
    if not shutil.which("ssh"):
        raise RuntimeError(
            "SSH is not installed or not in PATH. Install OpenSSH client: apt install openssh-client"
        )


class SSHEnvironment(BaseEnvironment):
    """通过 SSH 在远程机器上运行命令。

    每次调用创建新进程：每次 execute() 都启动一个新的 ``ssh ... bash -c`` 进程。
    会话快照在调用间保持环境变量。
    工作目录通过标准输出中的标记来持久化。
    使用 SSH ControlMaster 实现连接复用。
    """

    def __init__(self, host: str, user: str, cwd: str = "~",
                 timeout: int = 60, port: int = 22, key_path: str = ""):
        super().__init__(cwd=cwd, timeout=timeout)
        self.host = host
        self.user = user
        self.port = port
        self.key_path = key_path

        self.control_dir = Path(tempfile.gettempdir()) / "hermes-ssh"
        self.control_dir.mkdir(parents=True, exist_ok=True)
        self.control_socket = self.control_dir / f"{user}@{host}:{port}.sock"
        _ensure_ssh_available()
        self._establish_connection()
        self._remote_home = self._detect_remote_home()

        self._ensure_remote_dirs()
        self._sync_manager = FileSyncManager(
            get_files_fn=lambda: iter_sync_files(f"{self._remote_home}/.hermes"),
            upload_fn=self._scp_upload,
            delete_fn=self._ssh_delete,
            bulk_upload_fn=self._ssh_bulk_upload,
            bulk_download_fn=self._ssh_bulk_download,
        )
        self._sync_manager.sync(force=True)

        self.init_session()

    def _build_ssh_command(self, extra_args: list | None = None) -> list:
        cmd = ["ssh"]
        cmd.extend(["-o", f"ControlPath={self.control_socket}"])
        cmd.extend(["-o", "ControlMaster=auto"])
        cmd.extend(["-o", "ControlPersist=300"])
        cmd.extend(["-o", "BatchMode=yes"])
        cmd.extend(["-o", "StrictHostKeyChecking=accept-new"])
        cmd.extend(["-o", "ConnectTimeout=10"])
        if self.port != 22:
            cmd.extend(["-p", str(self.port)])
        if self.key_path:
            cmd.extend(["-i", self.key_path])
        if extra_args:
            cmd.extend(extra_args)
        cmd.append(f"{self.user}@{self.host}")
        return cmd

    def _establish_connection(self):
        cmd = self._build_ssh_command()
        cmd.append("echo 'SSH connection established'")
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            if result.returncode != 0:
                error_msg = result.stderr.strip() or result.stdout.strip()
                raise RuntimeError(f"SSH connection failed: {error_msg}")
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"SSH connection to {self.user}@{self.host} timed out")

    def _detect_remote_home(self) -> str:
        """检测远程用户的 home 目录。"""
        try:
            cmd = self._build_ssh_command()
            cmd.append("echo $HOME")
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            home = result.stdout.strip()
            if home and result.returncode == 0:
                logger.debug("SSH: remote home = %s", home)
                return home
        except Exception:
            pass
        if self.user == "root":
            return "/root"
        return f"/home/{self.user}"

    # ------------------------------------------------------------------
    # 文件同步（通过 FileSyncManager 实现）
    # ------------------------------------------------------------------

    def _ensure_remote_dirs(self) -> None:
        """在一次 SSH 调用中创建远程 ~/.hermes 基础目录树。"""
        base = f"{self._remote_home}/.hermes"
        dirs = [base, f"{base}/skills", f"{base}/credentials", f"{base}/cache"]
        cmd = self._build_ssh_command()
        cmd.append(quoted_mkdir_command(dirs))
        subprocess.run(cmd, capture_output=True, text=True, timeout=10)

    # _get_sync_files 通过 FileSyncManager init 中的 iter_sync_files 提供

    def _scp_upload(self, host_path: str, remote_path: str) -> None:
        """通过 ControlMaster 使用 scp 上传单个文件。"""
        parent = str(Path(remote_path).parent)
        mkdir_cmd = self._build_ssh_command()
        mkdir_cmd.append(f"mkdir -p {shlex.quote(parent)}")
        subprocess.run(mkdir_cmd, capture_output=True, text=True, timeout=10)

        scp_cmd = ["scp", "-o", f"ControlPath={self.control_socket}"]
        if self.port != 22:
            scp_cmd.extend(["-P", str(self.port)])
        if self.key_path:
            scp_cmd.extend(["-i", self.key_path])
        scp_cmd.extend([host_path, f"{self.user}@{self.host}:{remote_path}"])
        result = subprocess.run(scp_cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            raise RuntimeError(f"scp failed: {result.stderr.strip()}")

    def _ssh_bulk_upload(self, files: list[tuple[str, str]]) -> None:
        """通过单个 tar-over-SSH 流批量上传文件。

        在本地侧通过 SSH 连接将 ``tar c`` 管道传输到远程的 ``tar x``，
        在一个 TCP 流中传输所有文件，而非每个文件都启动一个子进程。
        目录创建被合并到一个 ``mkdir -p`` 调用中。

        典型改进：约 580 个文件从 O(N) 次 scp 往返缩减为单次流式传输。
        """
        if not files:
            return

        parents = unique_parent_dirs(files)
        if parents:
            cmd = self._build_ssh_command()
            cmd.append(quoted_mkdir_command(parents))
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                raise RuntimeError(f"remote mkdir failed: {result.stderr.strip()}")

        # 使用符号链接暂存区避免脆弱的 GNU tar --transform 规则。
        with tempfile.TemporaryDirectory(prefix="hermes-ssh-bulk-") as staging:
            for host_path, remote_path in files:
                staged = os.path.join(staging, remote_path.lstrip("/"))
                os.makedirs(os.path.dirname(staged), exist_ok=True)
                os.symlink(os.path.abspath(host_path), staged)

            tar_cmd = ["tar", "-chf", "-", "-C", staging, "."]
            ssh_cmd = self._build_ssh_command()
            ssh_cmd.append("tar xf - -C /")

            tar_proc = subprocess.Popen(
                tar_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            try:
                ssh_proc = subprocess.Popen(
                    ssh_cmd, stdin=tar_proc.stdout, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
            except Exception:
                tar_proc.kill()
                tar_proc.wait()
                raise

            # 允许 tar_proc 在 ssh_proc 提前退出时接收 SIGPIPE
            tar_proc.stdout.close()

            try:
                _, ssh_stderr = ssh_proc.communicate(timeout=120)
                # 使用 communicate() 而非 wait() 来排空 stderr，
                # 避免当 tar 产生超过 PIPE_BUF 大小的错误时发生死锁。
                tar_stderr_raw = b""
                if tar_proc.poll() is None:
                    _, tar_stderr_raw = tar_proc.communicate(timeout=10)
                else:
                    tar_stderr_raw = tar_proc.stderr.read() if tar_proc.stderr else b""
            except subprocess.TimeoutExpired:
                tar_proc.kill()
                ssh_proc.kill()
                tar_proc.wait()
                ssh_proc.wait()
                raise RuntimeError("SSH bulk upload timed out")

            if tar_proc.returncode != 0:
                raise RuntimeError(
                    f"tar create failed (rc={tar_proc.returncode}): "
                    f"{tar_stderr_raw.decode(errors='replace').strip()}"
                )
            if ssh_proc.returncode != 0:
                raise RuntimeError(
                    f"tar extract over SSH failed (rc={ssh_proc.returncode}): "
                    f"{ssh_stderr.decode(errors='replace').strip()}"
                )

        logger.debug("SSH: bulk-uploaded %d file(s) via tar pipe", len(files))

    def _ssh_bulk_download(self, dest: Path) -> None:
        """将远程 .hermes/ 目录作为 tar 归档下载。"""
        # 从 / 开始 tar 并使用完整路径，使归档条目保留绝对路径
        # （例如 home/user/.hermes/skills/f.py），与 _pushed_hashes 的键匹配。
        rel_base = f"{self._remote_home}/.hermes".lstrip("/")
        ssh_cmd = self._build_ssh_command()
        ssh_cmd.append(f"tar cf - -C / {shlex.quote(rel_base)}")
        with open(dest, "wb") as f:
            result = subprocess.run(ssh_cmd, stdout=f, stderr=subprocess.PIPE, timeout=120)
        if result.returncode != 0:
            raise RuntimeError(f"SSH bulk download failed: {result.stderr.decode(errors='replace').strip()}")

    def _ssh_delete(self, remote_paths: list[str]) -> None:
        """在一次 SSH 调用中批量删除远程文件。"""
        cmd = self._build_ssh_command()
        cmd.append(quoted_rm_command(remote_paths))
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode != 0:
            raise RuntimeError(f"remote rm failed: {result.stderr.strip()}")

    def _before_execute(self) -> None:
        """通过 FileSyncManager 将文件同步到远程（内部有频率限制）。"""
        self._sync_manager.sync()

    # ------------------------------------------------------------------
    # 命令执行
    # ------------------------------------------------------------------

    def _run_bash(self, cmd_string: str, *, login: bool = False,
                  timeout: int = 120,
                  stdin_data: str | None = None) -> subprocess.Popen:
        """启动一个在远程主机上运行 bash 的 SSH 进程。"""
        cmd = self._build_ssh_command()
        if login:
            cmd.extend(["bash", "-l", "-c", shlex.quote(cmd_string)])
        else:
            cmd.extend(["bash", "-c", shlex.quote(cmd_string)])

        return _popen_bash(cmd, stdin_data)

    def cleanup(self):
        if self._sync_manager:
            logger.info("SSH: syncing files from sandbox...")
            self._sync_manager.sync_back()

        if self.control_socket.exists():
            try:
                cmd = ["ssh", "-o", f"ControlPath={self.control_socket}",
                       "-O", "exit", f"{self.user}@{self.host}"]
                subprocess.run(cmd, capture_output=True, timeout=5)
            except (OSError, subprocess.SubprocessError):
                pass
            try:
                self.control_socket.unlink()
            except OSError:
                pass
