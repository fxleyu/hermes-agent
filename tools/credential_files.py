"""远程终端后端的文件透传注册表。

远程后端（Docker、Modal、SSH）创建的沙箱中没有宿主机文件。
本模块确保凭证文件、技能目录和宿主机端缓存目录（文档、图片、音频、截图）
被挂载或同步到这些沙箱中，以便代理可以访问它们。

**凭证和技能** — 会话范围的注册表，由技能声明（``required_credential_files``）
和用户配置（``terminal.credential_files``）填充。

**缓存目录** — 网关缓存的上传文件、浏览器截图、TTS 音频和处理后的图片。
以只读方式挂载，使远程终端可以引用宿主机端创建的文件
（例如对上传的压缩包执行 ``unzip``）。

远程后端在沙箱创建时和每个命令执行前（Modal 的重新同步）调用
:func:`get_credential_file_mounts`、
:func:`get_skills_directory_mount` / :func:`iter_skills_files` 以及
:func:`get_cache_directory_mounts` / :func:`iter_cache_files`。
"""

from __future__ import annotations

import logging
import os
from contextvars import ContextVar
from pathlib import Path
from typing import Dict, List

logger = logging.getLogger(__name__)

# 会话范围的待挂载凭证文件列表。
# 使用 ContextVar 以防止网关管道中跨会话的数据泄漏。
_registered_files_var: ContextVar[Dict[str, str]] = ContextVar("_registered_files")


def _get_registered() -> Dict[str, str]:
    """获取或创建当前上下文/会话的已注册凭证文件字典。"""
    try:
        return _registered_files_var.get()
    except LookupError:
        val: Dict[str, str] = {}
        _registered_files_var.set(val)
        return val


# 基于配置的文件列表缓存（每个进程加载一次）。
_config_files: List[Dict[str, str]] | None = None


def _resolve_hermes_home() -> Path:
    from hermes_constants import get_hermes_home
    return get_hermes_home()


def register_credential_file(
    relative_path: str,
    container_base: str = "/root/.hermes",
) -> bool:
    """注册凭证文件以挂载到远程沙箱中。

    *relative_path* 是相对于 ``HERMES_HOME`` 的路径（例如 ``google_token.json``）。
    如果文件在宿主机上存在并已注册，则返回 True。

    安全性：拒绝绝对路径和路径遍历序列（``..``）。
    解析后的宿主机路径必须保持在 HERMES_HOME 内部，这样恶意技能就无法
    声明 ``required_credential_files: ['../../.ssh/id_rsa']`` 并将敏感的
    宿主机文件泄露到容器沙箱中。
    """
    hermes_home = _resolve_hermes_home()

    # 拒绝绝对路径——它们完全绕过了 HERMES_HOME 沙箱。
    if os.path.isabs(relative_path):
        logger.warning(
            "credential_files: rejected absolute path %r (must be relative to HERMES_HOME)",
            relative_path,
        )
        return False

    host_path = hermes_home / relative_path

    # 在安全包含检查前解析符号链接并规范化 ``..``，
    # 以防止类似 ``../. ssh/id_rsa`` 的遍历逃逸 HERMES_HOME。
    from tools.path_security import validate_within_dir

    containment_error = validate_within_dir(host_path, hermes_home)
    if containment_error:
        logger.warning(
            "credential_files: rejected path traversal %r (%s)",
            relative_path,
            containment_error,
        )
        return False

    resolved = host_path.resolve()
    if not resolved.is_file():
        logger.debug("credential_files: skipping %s (not found)", resolved)
        return False

    container_path = f"{container_base.rstrip('/')}/{relative_path}"
    _get_registered()[container_path] = str(resolved)
    logger.debug("credential_files: registered %s -> %s", resolved, container_path)
    return True


def register_credential_files(
    entries: list,
    container_base: str = "/root/.hermes",
) -> List[str]:
    """从技能前置元数据条目中批量注册多个凭证文件。

    每个条目可以是字符串（相对路径）或包含 ``path`` 键的字典。
    返回宿主机上未找到的相对路径列表（即缺失的文件）。
    """
    missing = []
    for entry in entries:
        if isinstance(entry, str):
            rel_path = entry.strip()
        elif isinstance(entry, dict):
            rel_path = (entry.get("path") or entry.get("name") or "").strip()
        else:
            continue
        if not rel_path:
            continue
        if not register_credential_file(rel_path, container_base):
            missing.append(rel_path)
    return missing


def _load_config_files() -> List[Dict[str, str]]:
    """从 config.yaml 加载 ``terminal.credential_files``（已缓存）。"""
    global _config_files
    if _config_files is not None:
        return _config_files

    result: List[Dict[str, str]] = []
    try:
        from hermes_cli.config import read_raw_config
        hermes_home = _resolve_hermes_home()
        cfg = read_raw_config()
        cred_files = cfg.get("terminal", {}).get("credential_files")
        if isinstance(cred_files, list):
            from tools.path_security import validate_within_dir

            for item in cred_files:
                if isinstance(item, str) and item.strip():
                    rel = item.strip()
                    if os.path.isabs(rel):
                        logger.warning(
                            "credential_files: rejected absolute config path %r", rel,
                        )
                        continue
                    host_path = hermes_home / rel
                    containment_error = validate_within_dir(host_path, hermes_home)
                    if containment_error:
                        logger.warning(
                            "credential_files: rejected config path traversal %r (%s)",
                            rel, containment_error,
                        )
                        continue
                    resolved_path = host_path.resolve()
                    if resolved_path.is_file():
                        container_path = f"/root/.hermes/{rel}"
                        result.append({
                            "host_path": str(resolved_path),
                            "container_path": container_path,
                        })
    except Exception as e:
        logger.warning("Could not read terminal.credential_files from config: %s", e)

    _config_files = result
    return _config_files


def get_credential_file_mounts() -> List[Dict[str, str]]:
    """返回所有应挂载到远程沙箱中的凭证文件。

    每个条目包含 ``host_path`` 和 ``container_path`` 键。
    合并了技能注册的文件和用户配置的文件。
    """
    mounts: Dict[str, str] = {}

    # 技能注册的文件
    for container_path, host_path in _get_registered().items():
        # 重新检查文件是否存在（注册后文件可能已被删除）
        if Path(host_path).is_file():
            mounts[container_path] = host_path

    # 基于配置的文件
    for entry in _load_config_files():
        cp = entry["container_path"]
        if cp not in mounts and Path(entry["host_path"]).is_file():
            mounts[cp] = entry["host_path"]

    return [
        {"host_path": hp, "container_path": cp}
        for cp, hp in mounts.items()
    ]


def get_skills_directory_mount(
    container_base: str = "/root/.hermes",
) -> list[Dict[str, str]]:
    """返回所有技能目录（本地 + 外部）的挂载信息。

    技能可能包含 ``scripts/``、``templates/`` 和 ``references/``
    子目录，代理需要在远程沙箱中执行它们。

    **安全性：** 绑定挂载会跟随符号链接，因此技能目录树中的恶意符号链接
    可能会将宿主机的任意文件暴露给容器。当检测到符号链接时，此函数会在
    临时目录中创建一个经过净化的副本（仅包含常规文件），并返回该路径。
    当不存在符号链接时（常见情况），直接返回原始目录，零开销。

    返回包含 ``host_path`` 和 ``container_path`` 键的字典列表。
    本地技能目录挂载到 ``<container_base>/skills``，外部目录
    挂载到 ``<container_base>/external_skills/<index>``。
    """
    mounts = []
    hermes_home = _resolve_hermes_home()
    skills_dir = hermes_home / "skills"
    if skills_dir.is_dir():
        host_path = _safe_skills_path(skills_dir)
        mounts.append({
            "host_path": host_path,
            "container_path": f"{container_base.rstrip('/')}/skills",
        })

    # 挂载外部技能目录
    try:
        from agent.skill_utils import get_external_skills_dirs
        for idx, ext_dir in enumerate(get_external_skills_dirs()):
            if ext_dir.is_dir():
                host_path = _safe_skills_path(ext_dir)
                mounts.append({
                    "host_path": host_path,
                    "container_path": f"{container_base.rstrip('/')}/external_skills/{idx}",
                })
    except ImportError:
        pass

    return mounts


_safe_skills_tempdir: Path | None = None


def _safe_skills_path(skills_dir: Path) -> str:
    """如果没有符号链接则返回 *skills_dir*，否则返回经净化的临时副本。"""
    global _safe_skills_tempdir

    symlinks = [p for p in skills_dir.rglob("*") if p.is_symlink()]
    if not symlinks:
        return str(skills_dir)

    for link in symlinks:
        logger.warning("credential_files: skipping symlink in skills dir: %s -> %s",
                       link, os.readlink(link))

    import atexit
    import shutil
    import tempfile

    # 跨调用复用同一临时目录以避免累积。
    if _safe_skills_tempdir and _safe_skills_tempdir.is_dir():
        shutil.rmtree(_safe_skills_tempdir, ignore_errors=True)

    safe_dir = Path(tempfile.mkdtemp(prefix="hermes-skills-safe-"))
    _safe_skills_tempdir = safe_dir

    for item in skills_dir.rglob("*"):
        if item.is_symlink():
            continue
        rel = item.relative_to(skills_dir)
        target = safe_dir / rel
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif item.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(item), str(target))

    def _cleanup():
        if safe_dir.is_dir():
            shutil.rmtree(safe_dir, ignore_errors=True)

    atexit.register(_cleanup)
    logger.info("credential_files: created symlink-safe skills copy at %s", safe_dir)
    return str(safe_dir)


def iter_skills_files(
    container_base: str = "/root/.hermes",
) -> List[Dict[str, str]]:
    """逐个生成技能文件的 (host_path, container_path) 条目。

    包含本地技能目录以及通过 skills.external_dirs 配置的所有外部目录。
    完全跳过符号链接。适用于逐个上传文件的后端（Daytona、Modal），
    而非挂载整个目录的场景。
    """
    result: List[Dict[str, str]] = []

    hermes_home = _resolve_hermes_home()
    skills_dir = hermes_home / "skills"
    if skills_dir.is_dir():
        container_root = f"{container_base.rstrip('/')}/skills"
        for item in skills_dir.rglob("*"):
            if item.is_symlink() or not item.is_file():
                continue
            rel = item.relative_to(skills_dir)
            result.append({
                "host_path": str(item),
                "container_path": f"{container_root}/{rel}",
            })

    # 包含外部技能目录
    try:
        from agent.skill_utils import get_external_skills_dirs
        for idx, ext_dir in enumerate(get_external_skills_dirs()):
            if not ext_dir.is_dir():
                continue
            container_root = f"{container_base.rstrip('/')}/external_skills/{idx}"
            for item in ext_dir.rglob("*"):
                if item.is_symlink() or not item.is_file():
                    continue
                rel = item.relative_to(ext_dir)
                result.append({
                    "host_path": str(item),
                    "container_path": f"{container_root}/{rel}",
                })
    except ImportError:
        pass

    return result


# ---------------------------------------------------------------------------
# 缓存目录挂载（文档、图片、音频、截图）
# ---------------------------------------------------------------------------

# 应镜像到远程后端的四个缓存子目录。
# 每个元组为 (新子路径, 旧名称)，对应 hermes_constants.get_hermes_dir()。
_CACHE_DIRS: list[tuple[str, str]] = [
    ("cache/documents", "document_cache"),
    ("cache/images", "image_cache"),
    ("cache/audio", "audio_cache"),
    ("cache/screenshots", "browser_screenshots"),
]


def get_cache_directory_mounts(
    container_base: str = "/root/.hermes",
) -> List[Dict[str, str]]:
    """返回磁盘上存在的每个缓存目录的挂载条目。

    Docker 用于创建绑定挂载。每个条目包含 ``host_path`` 和
    ``container_path`` 键。宿主机路径通过 ``get_hermes_dir()`` 解析，
    以向后兼容旧的目录布局。
    """
    from hermes_constants import get_hermes_dir

    mounts: List[Dict[str, str]] = []
    for new_subpath, old_name in _CACHE_DIRS:
        host_dir = get_hermes_dir(new_subpath, old_name)
        if host_dir.is_dir():
            # 无论宿主机布局如何，容器内始终使用*新*布局映射。
            container_path = f"{container_base.rstrip('/')}/{new_subpath}"
            mounts.append({
                "host_path": str(host_dir),
                "container_path": container_path,
            })
    return mounts


def iter_cache_files(
    container_base: str = "/root/.hermes",
) -> List[Dict[str, str]]:
    """返回缓存文件的逐个 (host_path, container_path) 条目。

    Modal 用于逐个上传文件并在每次命令前重新同步。
    跳过符号链接。容器路径使用新的 ``cache/<subdir>`` 布局。
    """
    from hermes_constants import get_hermes_dir

    result: List[Dict[str, str]] = []
    for new_subpath, old_name in _CACHE_DIRS:
        host_dir = get_hermes_dir(new_subpath, old_name)
        if not host_dir.is_dir():
            continue
        container_root = f"{container_base.rstrip('/')}/{new_subpath}"
        for item in host_dir.rglob("*"):
            if item.is_symlink() or not item.is_file():
                continue
            rel = item.relative_to(host_dir)
            result.append({
                "host_path": str(item),
                "container_path": f"{container_root}/{rel}",
            })
    return result


def clear_credential_files() -> None:
    """重置技能范围的注册表（例如在会话重置时调用）。"""
    _get_registered().clear()


