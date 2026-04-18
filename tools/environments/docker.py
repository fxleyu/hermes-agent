"""Docker 沙箱执行环境，用于隔离命令执行。

安全加固（丢弃所有权限、禁止权限提升、PID 限制），
可配置资源限制（CPU、内存、磁盘），并支持通过 bind mount 实现可选的文件系统持久化。
"""

import logging
import os
import re
import shutil
import subprocess
import sys
import uuid
from typing import Optional

from tools.environments.base import BaseEnvironment, _popen_bash
from tools.environments.local import _HERMES_PROVIDER_ENV_BLOCKLIST

logger = logging.getLogger(__name__)


# 当 'docker' 不在 PATH 中时检查的常见 Docker Desktop 安装路径。
# macOS Intel: /usr/local/bin, macOS Apple Silicon (Homebrew): /opt/homebrew/bin,
# Docker Desktop 应用程序包: /Applications/Docker.app/Contents/Resources/bin
_DOCKER_SEARCH_PATHS = [
    "/usr/local/bin/docker",
    "/opt/homebrew/bin/docker",
    "/Applications/Docker.app/Contents/Resources/bin/docker",
]

_docker_executable: Optional[str] = None  # 解析一次后缓存
_ENV_VAR_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _normalize_forward_env_names(forward_env: list[str] | None) -> list[str]:
    """返回去重后的有效环境变量名称列表。"""
    normalized: list[str] = []
    seen: set[str] = set()

    for item in forward_env or []:
        if not isinstance(item, str):
            logger.warning("Ignoring non-string docker_forward_env entry: %r", item)
            continue

        key = item.strip()
        if not key:
            continue
        if not _ENV_VAR_NAME_RE.match(key):
            logger.warning("Ignoring invalid docker_forward_env entry: %r", item)
            continue
        if key in seen:
            continue

        seen.add(key)
        normalized.append(key)

    return normalized


def _normalize_env_dict(env: dict | None) -> dict[str, str]:
    """验证并规范化 docker_env 字典为 {str: str} 格式。

    过滤掉变量名无效或值为非字符串类型的条目。
    """
    if not env:
        return {}
    if not isinstance(env, dict):
        logger.warning("docker_env is not a dict: %r", env)
        return {}

    normalized: dict[str, str] = {}
    for key, value in env.items():
        if not isinstance(key, str) or not _ENV_VAR_NAME_RE.match(key.strip()):
            logger.warning("Ignoring invalid docker_env key: %r", key)
            continue
        key = key.strip()
        if not isinstance(value, str):
            # 将简单标量类型（int、bool、float）强制转为字符串；
            # 拒绝复杂类型。
            if isinstance(value, (int, float, bool)):
                value = str(value)
            else:
                logger.warning("Ignoring non-string docker_env value for %r: %r", key, value)
                continue
        normalized[key] = value

    return normalized


def _load_hermes_env_vars() -> dict[str, str]:
    """加载 ~/.hermes/.env 的值，不影响 Docker 命令的执行。"""
    try:
        from hermes_cli.config import load_env

        return load_env() or {}
    except Exception:
        return {}


def find_docker() -> Optional[str]:
    """查找 docker（或 podman）CLI 二进制文件。

    查找顺序：
    1. ``HERMES_DOCKER_BINARY`` 环境变量 - 显式覆盖（例如 ``/usr/bin/podman``）
    2. 通过 ``shutil.which`` 在 PATH 中查找 ``docker``
    3. 通过 ``shutil.which`` 在 PATH 中查找 ``podman``
    4. macOS Docker Desktop 的常见安装位置

    返回绝对路径，如果两个运行时都找不到则返回 ``None``。
    """
    global _docker_executable
    if _docker_executable is not None:
        return _docker_executable

    # 1. 通过环境变量显式覆盖（例如用于不可变发行版上的 Podman）
    override = os.getenv("HERMES_DOCKER_BINARY")
    if override and os.path.isfile(override) and os.access(override, os.X_OK):
        _docker_executable = override
        logger.info("Using HERMES_DOCKER_BINARY override: %s", override)
        return override

    # 2. 在 PATH 中查找 docker
    found = shutil.which("docker")
    if found:
        _docker_executable = found
        return found

    # 3. 在 PATH 中查找 podman（对我们的用例来说是兼容替代品）
    found = shutil.which("podman")
    if found:
        _docker_executable = found
        logger.info("Using podman as container runtime: %s", found)
        return found

    # 4. macOS Docker Desktop 的常见安装位置
    for path in _DOCKER_SEARCH_PATHS:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            _docker_executable = path
            logger.info("Found docker at non-PATH location: %s", path)
            return path

    return None


# 应用于每个容器的安全标志。
# 容器本身是安全边界（与宿主机隔离）。
# 我们丢弃所有权限，然后添加回所需的最低权限：
#   DAC_OVERRIDE - root 可以写入由宿主机用户拥有的 bind mount 目录
#   CHOWN/FOWNER - 包管理器（pip、npm、apt）需要设置文件所有权
# 阻止权限提升并限制 PID 数量。
# /tmp 设有大小限制和 nosuid，但允许 exec（pip/npm 构建需要）。
_SECURITY_ARGS = [
    "--cap-drop", "ALL",
    "--cap-add", "DAC_OVERRIDE",
    "--cap-add", "CHOWN",
    "--cap-add", "FOWNER",
    "--security-opt", "no-new-privileges",
    "--pids-limit", "256",
    "--tmpfs", "/tmp:rw,nosuid,size=512m",
    "--tmpfs", "/var/tmp:rw,noexec,nosuid,size=256m",
    "--tmpfs", "/run:rw,noexec,nosuid,size=64m",
]


_storage_opt_ok: Optional[bool] = None  # 跨实例缓存的结果


def _ensure_docker_available() -> None:
    """尽力检查 docker CLI 在使用前是否可用。

    复用 ``find_docker()``，使此预检与 Docker 后端的其余部分保持一致，
    包括已知的非 PATH Docker Desktop 安装位置。
    """
    docker_exe = find_docker()
    if not docker_exe:
        logger.error(
            "Docker backend selected but no docker executable was found in PATH "
            "or known install locations. Install Docker Desktop and ensure the "
            "CLI is available."
        )
        raise RuntimeError(
            "Docker executable not found in PATH or known install locations. "
            "Install Docker and ensure the 'docker' command is available."
        )

    try:
        result = subprocess.run(
            [docker_exe, "version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except FileNotFoundError:
        logger.error(
            "Docker backend selected but the resolved docker executable '%s' could "
            "not be executed.",
            docker_exe,
            exc_info=True,
        )
        raise RuntimeError(
            "Docker executable could not be executed. Check your Docker installation."
        )
    except subprocess.TimeoutExpired:
        logger.error(
            "Docker backend selected but '%s version' timed out. "
            "The Docker daemon may not be running.",
            docker_exe,
            exc_info=True,
        )
        raise RuntimeError(
            "Docker daemon is not responding. Ensure Docker is running and try again."
        )
    except Exception:
        logger.error(
            "Unexpected error while checking Docker availability.",
            exc_info=True,
        )
        raise
    else:
        if result.returncode != 0:
            logger.error(
                "Docker backend selected but '%s version' failed "
                "(exit code %d, stderr=%s)",
                docker_exe,
                result.returncode,
                result.stderr.strip(),
            )
            raise RuntimeError(
                "Docker command is available but 'docker version' failed. "
                "Check your Docker installation."
            )


class DockerEnvironment(BaseEnvironment):
    """安全加固的 Docker 容器执行环境，支持资源限制和持久化。

    安全性：丢弃所有权限、禁止权限提升、PID 限制、大小受限的 tmpfs 用于临时目录。
    容器本身是安全边界 - 内部文件系统可写，便于代理安装软件包
    （pip、npm、apt）。通过 tmpfs 或 bind mount 实现可写工作空间。

    持久化：启用时，bind mount 会在容器重启后保留 /workspace 和 /root 的内容。
    """

    def __init__(
        self,
        image: str,
        cwd: str = "/root",
        timeout: int = 60,
        cpu: float = 0,
        memory: int = 0,
        disk: int = 0,
        persistent_filesystem: bool = False,
        task_id: str = "default",
        volumes: list = None,
        forward_env: list[str] | None = None,
        env: dict | None = None,
        network: bool = True,
        host_cwd: str = None,
        auto_mount_cwd: bool = False,
    ):
        if cwd == "~":
            cwd = "/root"
        super().__init__(cwd=cwd, timeout=timeout)
        self._persistent = persistent_filesystem
        self._task_id = task_id
        self._forward_env = _normalize_forward_env_names(forward_env)
        self._env = _normalize_env_dict(env)
        self._container_id: Optional[str] = None
        logger.info(f"DockerEnvironment volumes: {volumes}")
        # 确保 volumes 是列表（config.yaml 可能格式错误）
        if volumes is not None and not isinstance(volumes, list):
            logger.warning(f"docker_volumes config is not a list: {volumes!r}")
            volumes = []

        # 如果 Docker 不可用，立即失败。
        _ensure_docker_available()

        # 构建资源限制参数
        resource_args = []
        if cpu > 0:
            resource_args.extend(["--cpus", str(cpu)])
        if memory > 0:
            resource_args.extend(["--memory", f"{memory}m"])
        if disk > 0 and sys.platform != "darwin":
            if self._storage_opt_supported():
                resource_args.extend(["--storage-opt", f"size={disk}m"])
            else:
                logger.warning(
                    "Docker storage driver does not support per-container disk limits "
                    "(requires overlay2 on XFS with pquota). Container will run without disk quota."
                )
        if not network:
            resource_args.append("--network=none")

        # 通过 bind mount 实现持久化工作空间，挂载到可配置的宿主机目录
        # （TERMINAL_SANDBOX_DIR，默认 ~/.hermes/sandboxes/）。非持久化模式
        # 使用 tmpfs（临时性的、速度快、清理后即消失）。
        from tools.environments.base import get_sandbox_dir

        # 用户配置的卷挂载（来自 config.yaml 的 docker_volumes）
        volume_args = []
        workspace_explicitly_mounted = False
        for vol in (volumes or []):
            if not isinstance(vol, str):
                logger.warning(f"Docker volume entry is not a string: {vol!r}")
                continue
            vol = vol.strip()
            if not vol:
                continue
            if ":" in vol:
                volume_args.extend(["-v", vol])
                if ":/workspace" in vol:
                    workspace_explicitly_mounted = True
            else:
                logger.warning(f"Docker volume '{vol}' missing colon, skipping")

        host_cwd_abs = os.path.abspath(os.path.expanduser(host_cwd)) if host_cwd else ""
        bind_host_cwd = (
            auto_mount_cwd
            and bool(host_cwd_abs)
            and os.path.isdir(host_cwd_abs)
            and not workspace_explicitly_mounted
        )
        if auto_mount_cwd and host_cwd and not os.path.isdir(host_cwd_abs):
            logger.debug(f"Skipping docker cwd mount: host_cwd is not a valid directory: {host_cwd}")

        self._workspace_dir: Optional[str] = None
        self._home_dir: Optional[str] = None
        writable_args = []
        if self._persistent:
            sandbox = get_sandbox_dir() / "docker" / task_id
            self._home_dir = str(sandbox / "home")
            os.makedirs(self._home_dir, exist_ok=True)
            writable_args.extend([
                "-v", f"{self._home_dir}:/root",
            ])
            if not bind_host_cwd and not workspace_explicitly_mounted:
                self._workspace_dir = str(sandbox / "workspace")
                os.makedirs(self._workspace_dir, exist_ok=True)
                writable_args.extend([
                    "-v", f"{self._workspace_dir}:/workspace",
                ])
        else:
            if not bind_host_cwd and not workspace_explicitly_mounted:
                writable_args.extend([
                    "--tmpfs", "/workspace:rw,exec,size=10g",
                ])
            writable_args.extend([
                "--tmpfs", "/home:rw,exec,size=1g",
                "--tmpfs", "/root:rw,exec,size=1g",
            ])

        if bind_host_cwd:
            logger.info(f"Mounting configured host cwd to /workspace: {host_cwd_abs}")
            volume_args = ["-v", f"{host_cwd_abs}:/workspace", *volume_args]
        elif workspace_explicitly_mounted:
            logger.debug("Skipping docker cwd mount: /workspace already mounted by user config")

        # 挂载由技能（skills）声明的凭证文件（OAuth 令牌等）。
        # 以只读方式挂载，容器可以进行认证但不能修改宿主机凭证。
        try:
            from tools.credential_files import (
                get_credential_file_mounts,
                get_skills_directory_mount,
                get_cache_directory_mounts,
            )

            for mount_entry in get_credential_file_mounts():
                volume_args.extend([
                    "-v",
                    f"{mount_entry['host_path']}:{mount_entry['container_path']}:ro",
                ])
                logger.info(
                    "Docker: mounting credential %s -> %s",
                    mount_entry["host_path"],
                    mount_entry["container_path"],
                )

            # 挂载技能目录（本地 + 外部），使技能脚本/模板在容器内可用。
            for skills_mount in get_skills_directory_mount():
                volume_args.extend([
                    "-v",
                    f"{skills_mount['host_path']}:{skills_mount['container_path']}:ro",
                ])
                logger.info(
                    "Docker: mounting skills dir %s -> %s",
                    skills_mount["host_path"],
                    skills_mount["container_path"],
                )

            # 挂载宿主机侧的缓存目录（文档、图片、音频、截图），
            # 使代理能从容器内部访问上传的文件和其他缓存媒体。
            # 只读 - 容器读取这些内容，但宿主机网关管理写入。
            for cache_mount in get_cache_directory_mounts():
                volume_args.extend([
                    "-v",
                    f"{cache_mount['host_path']}:{cache_mount['container_path']}:ro",
                ])
                logger.info(
                    "Docker: mounting cache dir %s -> %s",
                    cache_mount["host_path"],
                    cache_mount["container_path"],
                )
        except Exception as e:
            logger.debug("Docker: could not load credential file mounts: %s", e)

        # 显式环境变量（docker_env 配置）- 在容器创建时设置，
        # 以便所有进程（包括入口点）都能使用。
        env_args = []
        for key in sorted(self._env):
            env_args.extend(["-e", f"{key}={self._env[key]}"])

        logger.info(f"Docker volume_args: {volume_args}")
        all_run_args = list(_SECURITY_ARGS) + writable_args + resource_args + volume_args + env_args
        logger.info(f"Docker run_args: {all_run_args}")

        # 只解析一次 docker 可执行文件，即使 /usr/local/bin 不在 PATH 中
        # 也能正常工作（在 macOS 网关/服务中很常见）。
        self._docker_exe = find_docker() or "docker"

        # 通过 `docker run -d` 直接启动容器。
        container_name = f"hermes-{uuid.uuid4().hex[:8]}"
        run_cmd = [
            self._docker_exe, "run", "-d",
            "--init",           # tini/catatonit 作为 PID 1 - 回收僵尸子进程
            "--name", container_name,
            "-w", cwd,
            *all_run_args,
            image,
            "sleep", "infinity",  # 无固定生命周期 - 由空闲回收器处理清理
        ]
        logger.debug(f"Starting container: {' '.join(run_cmd)}")
        result = subprocess.run(
            run_cmd,
            capture_output=True,
            text=True,
            timeout=120,  # 镜像拉取可能需要较长时间
            check=True,
        )
        self._container_id = result.stdout.strip()
        logger.info(f"Started container {container_name} ({self._container_id[:12]})")

        # 构建初始化时的环境变量转发参数（仅在 init_session 中使用，
        # 用于将宿主机环境变量注入到快照中；后续命令从快照文件获取这些变量）。
        self._init_env_args = self._build_init_env_args()

        # 在容器内初始化会话快照
        self.init_session()

    def _build_init_env_args(self) -> list[str]:
        """构建 -e KEY=VALUE 参数，用于在 init_session 中注入宿主机环境变量。

        这些参数仅在 init_session() 期间使用一次，以便 export -p 能将它们
        捕获到快照中。后续的 execute() 调用不需要 -e 标志。
        """
        exec_env: dict[str, str] = dict(self._env)

        explicit_forward_keys = set(self._forward_env)
        passthrough_keys: set[str] = set()
        try:
            from tools.env_passthrough import get_all_passthrough
            passthrough_keys = set(get_all_passthrough())
        except Exception:
            pass
        # 显式的 docker_forward_env 条目是用户有意选择的，必须优先于通用的
        # Hermes 密钥黑名单。只有隐式的 passthrough 键才会被过滤。
        forward_keys = explicit_forward_keys | (passthrough_keys - _HERMES_PROVIDER_ENV_BLOCKLIST)
        hermes_env = _load_hermes_env_vars() if forward_keys else {}
        for key in sorted(forward_keys):
            value = os.getenv(key)
            if value is None:
                value = hermes_env.get(key)
            if value is not None:
                exec_env[key] = value

        args = []
        for key in sorted(exec_env):
            args.extend(["-e", f"{key}={exec_env[key]}"])
        return args

    def _run_bash(self, cmd_string: str, *, login: bool = False,
                  timeout: int = 120,
                  stdin_data: str | None = None) -> subprocess.Popen:
        """在 Docker 容器内启动一个 bash 进程。"""
        assert self._container_id, "Container not started"
        cmd = [self._docker_exe, "exec"]
        if stdin_data is not None:
            cmd.append("-i")

        # 仅在 init_session 期间（login=True）注入 -e 环境变量参数。
        # 后续命令从快照获取环境变量。
        if login:
            cmd.extend(self._init_env_args)

        cmd.extend([self._container_id])

        if login:
            cmd.extend(["bash", "-l", "-c", cmd_string])
        else:
            cmd.extend(["bash", "-c", cmd_string])

        return _popen_bash(cmd, stdin_data)

    @staticmethod
    def _storage_opt_supported() -> bool:
        """检查 Docker 的存储驱动是否支持 --storage-opt size= 选项。

        只有在 XFS 文件系统上使用 pquota 的 overlay2 驱动才支持按容器的磁盘配额。
        Ubuntu（及大多数发行版）默认使用 ext4，该标志会导致错误。
        """
        global _storage_opt_ok
        if _storage_opt_ok is not None:
            return _storage_opt_ok
        try:
            docker = find_docker() or "docker"
            result = subprocess.run(
                [docker, "info", "--format", "{{.Driver}}"],
                capture_output=True, text=True, timeout=10,
            )
            driver = result.stdout.strip().lower()
            if driver != "overlay2":
                _storage_opt_ok = False
                return False
            # overlay2 仅在 XFS + pquota 上支持 storage-opt。
            # 通过尝试运行来探测 - 这是最快的可靠检查方式。
            probe = subprocess.run(
                [docker, "create", "--storage-opt", "size=1m", "hello-world"],
                capture_output=True, text=True, timeout=15,
            )
            if probe.returncode == 0:
                # 清理创建的容器
                container_id = probe.stdout.strip()
                if container_id:
                    subprocess.run([docker, "rm", container_id],
                                   capture_output=True, timeout=5)
                _storage_opt_ok = True
            else:
                _storage_opt_ok = False
        except Exception:
            _storage_opt_ok = False
        logger.debug("Docker --storage-opt support: %s", _storage_opt_ok)
        return _storage_opt_ok

    def cleanup(self):
        """停止并移除容器。persistent=True 时 bind mount 目录会保留。"""
        if self._container_id:
            try:
                # 在后台停止，避免清理时阻塞
                stop_cmd = (
                    f"(timeout 60 {self._docker_exe} stop {self._container_id} || "
                    f"{self._docker_exe} rm -f {self._container_id}) >/dev/null 2>&1 &"
                )
                subprocess.Popen(stop_cmd, shell=True)
            except Exception as e:
                logger.warning("Failed to stop container %s: %s", self._container_id, e)

            if not self._persistent:
                # 同时安排移除操作（stop 只是停止，不会删除容器）
                try:
                    subprocess.Popen(
                        f"sleep 3 && {self._docker_exe} rm -f {self._container_id} >/dev/null 2>&1 &",
                        shell=True,
                    )
                except Exception:
                    pass
            self._container_id = None

        if not self._persistent:
            for d in (self._workspace_dir, self._home_dir):
                if d:
                    shutil.rmtree(d, ignore_errors=True)
