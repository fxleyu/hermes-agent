"""
多个隔离 Hermes 实例的配置文件管理。

每个配置文件是一个完全独立的 HERMES_HOME 目录，拥有自己的
config.yaml、.env、记忆、会话、技能、网关、定时任务和日志。
配置文件默认位于 ``~/.hermes/profiles/<name>/`` 下。

"default" 配置文件就是 ``~/.hermes`` 本身——向后兼容，
无需迁移。

用法::

    hermes profile create coder          # 新配置文件 + 内置技能
    hermes profile create coder --clone  # 同时复制 config、.env、SOUL.md
    hermes profile create coder --clone-all  # 完整复制源配置文件
    coder chat                           # 通过包装器别名使用
    hermes -p coder chat                 # 或通过标志使用
    hermes profile use coder             # 设为粘性默认值
    hermes profile delete coder          # 删除配置文件 + 别名 + 服务
"""

import json
import os
import re
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import List, Optional

_PROFILE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

# 每个新配置文件中引导创建的目录
_PROFILE_DIRS = [
    "memories",
    "sessions",
    "skills",
    "skins",
    "logs",
    "plans",
    "workspace",
    "cron",
    # 子进程的每配置文件 HOME：隔离系统工具配置（git、
    # ssh、gh、npm 等），使凭证不会在配置文件间泄漏。在 Docker 中
    # 这还确保工具配置落入持久化卷中。
    # 参见 hermes_constants.get_subprocess_home() 和 issue #4426。
    "home",
]

# --clone 时复制的文件（如果源中存在）
_CLONE_CONFIG_FILES = [
    "config.yaml",
    ".env",
    "SOUL.md",
]

# --clone 时复制的子目录文件（相对于配置文件根目录的路径）。
# 记忆文件是智能体精心策划的身份的一部分——与
# SOUL.md 对克隆配置文件的连续性同样重要。
_CLONE_SUBDIR_FILES = [
    "memories/MEMORY.md",
    "memories/USER.md",
]

# --clone-all 后剥离的运行时文件（不应携带过去）
_CLONE_ALL_STRIP = [
    "gateway.pid",
    "gateway_state.json",
    "processes.json",
]

# 导出默认（~/.hermes）配置文件时排除的目录/文件。
# 默认配置文件包含基础设施（仓库检出、工作树、数据库、
# 缓存、二进制文件），而命名配置文件没有这些。我们排除它们
# 使导出成为一个便携的、合理大小的实际配置文件数据存档。
_DEFAULT_EXPORT_EXCLUDE_ROOT = frozenset({
    # 基础设施
    "hermes-agent",         # 仓库检出（多 GB）
    ".worktrees",           # git 工作树
    "profiles",             # 其他配置文件——永不递归导出
    "bin",                  # 已安装的二进制文件（tirith 等）
    "node_modules",         # npm 包
    # 数据库和运行时状态
    "state.db", "state.db-shm", "state.db-wal",
    "hermes_state.db",
    "response_store.db", "response_store.db-shm", "response_store.db-wal",
    "gateway.pid", "gateway_state.json", "processes.json",
    "auth.json",            # API 密钥、OAuth 令牌、凭证池
    ".env",                 # API 密钥（dotenv）
    "auth.lock", "active_profile", ".update_check",
    "errors.log",
    ".hermes_history",
    # 缓存（使用时重新生成）
    "image_cache", "audio_cache", "document_cache",
    "browser_screenshots", "checkpoints",
    "sandboxes",
    "logs",                 # 网关日志
})

# 不能用作配置文件别名的名称
_RESERVED_NAMES = frozenset({
    "hermes", "default", "test", "tmp", "root", "sudo",
})

# 不能用作配置文件名称/别名的 Hermes 子命令
_HERMES_SUBCOMMANDS = frozenset({
    "chat", "model", "gateway", "setup", "whatsapp", "login", "logout",
    "status", "cron", "doctor", "dump", "config", "pairing", "skills", "tools",
    "mcp", "sessions", "insights", "version", "update", "uninstall",
    "profile", "plugins", "honcho", "acp",
})


# ---------------------------------------------------------------------------
# 路径辅助工具
# ---------------------------------------------------------------------------

def _get_profiles_root() -> Path:
    """返回存储命名配置文件的目录。

    锚定到 hermes 根目录，而非当前 HERMES_HOME
    （当前 HERMES_HOME 本身可能就是一个配置文件）。这确保
    ``coder profile list`` 能看到所有配置文件。

    在 Docker/自定义部署中，当 HERMES_HOME 指向 ``~/.hermes``
    之外时，配置文件位于 ``HERMES_HOME/profiles/`` 下，
    以便在挂载卷上持久化。
    """
    return _get_default_hermes_home() / "profiles"


def _get_default_hermes_home() -> Path:
    """返回默认（配置文件之前的）HERMES_HOME 路径。

    在标准部署中为 ``~/.hermes``。
    在 Docker/自定义部署中，当 HERMES_HOME 位于 ``~/.hermes``
    之外（如 ``/opt/data``）时，直接返回 HERMES_HOME。
    """
    from hermes_constants import get_default_hermes_root
    return get_default_hermes_root()


def _get_active_profile_path() -> Path:
    """返回粘性 active_profile 文件的路径。"""
    return _get_default_hermes_home() / "active_profile"


def _get_wrapper_dir() -> Path:
    """返回包装器脚本的目录。"""
    return Path.home() / ".local" / "bin"


# ---------------------------------------------------------------------------
# 验证
# ---------------------------------------------------------------------------

def validate_profile_name(name: str) -> None:
    """当 *name* 不是有效的配置文件标识符时抛出 ``ValueError``。"""
    if name == "default":
        return  # ~/.hermes 的特殊别名
    if not _PROFILE_ID_RE.match(name):
        raise ValueError(
            f"Invalid profile name {name!r}. Must match "
            f"[a-z0-9][a-z0-9_-]{{0,63}}"
        )


def get_profile_dir(name: str) -> Path:
    """将配置文件名称解析为其 HERMES_HOME 目录。"""
    if name == "default":
        return _get_default_hermes_home()
    return _get_profiles_root() / name


def profile_exists(name: str) -> bool:
    """检查配置文件目录是否存在。"""
    if name == "default":
        return True
    return get_profile_dir(name).is_dir()


# ---------------------------------------------------------------------------
# 别名 / 包装器脚本管理
# ---------------------------------------------------------------------------

def check_alias_collision(name: str) -> Optional[str]:
    """返回人类可读的冲突消息，如果名称安全则返回 None。

    检查：保留名称、hermes 子命令、PATH 中的现有二进制文件。
    """
    if name in _RESERVED_NAMES:
        return f"'{name}' is a reserved name"
    if name in _HERMES_SUBCOMMANDS:
        return f"'{name}' conflicts with a hermes subcommand"

    # Check existing commands in PATH
    wrapper_dir = _get_wrapper_dir()
    try:
        result = subprocess.run(
            ["which", name], capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            existing_path = result.stdout.strip()
            # Allow overwriting our own wrappers
            if existing_path == str(wrapper_dir / name):
                try:
                    content = (wrapper_dir / name).read_text()
                    if "hermes -p" in content:
                        return None  # it's our wrapper, safe to overwrite
                except Exception:
                    pass
            return f"'{name}' conflicts with an existing command ({existing_path})"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    return None  # safe


def _is_wrapper_dir_in_path() -> bool:
    """检查 ~/.local/bin 是否在 PATH 中。"""
    wrapper_dir = str(_get_wrapper_dir())
    return wrapper_dir in os.environ.get("PATH", "").split(os.pathsep)


def create_wrapper_script(name: str) -> Optional[Path]:
    """在 ~/.local/bin/<name> 创建 shell 包装器脚本。

    返回创建的包装器路径，失败时返回 None。
    """
    wrapper_dir = _get_wrapper_dir()
    try:
        wrapper_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(f"⚠ Could not create {wrapper_dir}: {e}")
        return None

    wrapper_path = wrapper_dir / name
    try:
        wrapper_path.write_text(f'#!/bin/sh\nexec hermes -p {name} "$@"\n')
        wrapper_path.chmod(wrapper_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        return wrapper_path
    except OSError as e:
        print(f"⚠ Could not create wrapper at {wrapper_path}: {e}")
        return None


def remove_wrapper_script(name: str) -> bool:
    """移除配置文件的包装器脚本。成功移除返回 True。"""
    wrapper_path = _get_wrapper_dir() / name
    if wrapper_path.exists():
        try:
            # Verify it's our wrapper before removing
            content = wrapper_path.read_text()
            if "hermes -p" in content:
                wrapper_path.unlink()
                return True
        except Exception:
            pass
    return False


# ---------------------------------------------------------------------------
# ProfileInfo
# ---------------------------------------------------------------------------

@dataclass
class ProfileInfo:
    """配置文件的摘要信息。"""
    name: str
    path: Path
    is_default: bool
    gateway_running: bool
    model: Optional[str] = None
    provider: Optional[str] = None
    has_env: bool = False
    skill_count: int = 0
    alias_path: Optional[Path] = None


def _read_config_model(profile_dir: Path) -> tuple:
    """从配置文件的 config.yaml 读取模型/提供商。返回 (model, provider)。"""
    config_path = profile_dir / "config.yaml"
    if not config_path.exists():
        return None, None
    try:
        import yaml
        with open(config_path, "r") as f:
            cfg = yaml.safe_load(f) or {}
        model_cfg = cfg.get("model", {})
        if isinstance(model_cfg, str):
            return model_cfg, None
        if isinstance(model_cfg, dict):
            return model_cfg.get("default") or model_cfg.get("model"), model_cfg.get("provider")
        return None, None
    except Exception:
        return None, None


def _check_gateway_running(profile_dir: Path) -> bool:
    """检查给定配置文件目录是否有正在运行的网关。"""
    pid_file = profile_dir / "gateway.pid"
    if not pid_file.exists():
        return False
    try:
        raw = pid_file.read_text().strip()
        if not raw:
            return False
        data = json.loads(raw) if raw.startswith("{") else {"pid": int(raw)}
        pid = int(data["pid"])
        os.kill(pid, 0)  # existence check
        return True
    except (json.JSONDecodeError, KeyError, ValueError, TypeError,
            ProcessLookupError, PermissionError, OSError):
        return False


def _count_skills(profile_dir: Path) -> int:
    """统计配置文件中已安装的技能数量。"""
    skills_dir = profile_dir / "skills"
    if not skills_dir.is_dir():
        return 0
    count = 0
    for md in skills_dir.rglob("SKILL.md"):
        if "/.hub/" not in str(md) and "/.git/" not in str(md):
            count += 1
    return count


# ---------------------------------------------------------------------------
# CRUD 操作
# ---------------------------------------------------------------------------

def list_profiles() -> List[ProfileInfo]:
    """返回所有配置文件的信息，包括默认配置文件。"""
    profiles = []
    wrapper_dir = _get_wrapper_dir()

    # 默认配置文件
    default_home = _get_default_hermes_home()
    if default_home.is_dir():
        model, provider = _read_config_model(default_home)
        profiles.append(ProfileInfo(
            name="default",
            path=default_home,
            is_default=True,
            gateway_running=_check_gateway_running(default_home),
            model=model,
            provider=provider,
            has_env=(default_home / ".env").exists(),
            skill_count=_count_skills(default_home),
        ))

    # 命名配置文件
    profiles_root = _get_profiles_root()
    if profiles_root.is_dir():
        for entry in sorted(profiles_root.iterdir()):
            if not entry.is_dir():
                continue
            name = entry.name
            if not _PROFILE_ID_RE.match(name):
                continue
            model, provider = _read_config_model(entry)
            alias_path = wrapper_dir / name
            profiles.append(ProfileInfo(
                name=name,
                path=entry,
                is_default=False,
                gateway_running=_check_gateway_running(entry),
                model=model,
                provider=provider,
                has_env=(entry / ".env").exists(),
                skill_count=_count_skills(entry),
                alias_path=alias_path if alias_path.exists() else None,
            ))

    return profiles


def create_profile(
    name: str,
    clone_from: Optional[str] = None,
    clone_all: bool = False,
    clone_config: bool = False,
    no_alias: bool = False,
) -> Path:
    """创建新的配置文件目录。

    参数
    ----------
    name:
        配置文件标识符（小写字母、数字、连字符、下划线）。
    clone_from:
        要克隆的源配置文件。如果为 ``None`` 且 clone_config/clone_all
        为 True，则默认为当前活跃的配置文件。
    clone_all:
        如果为 True，对源进行完整的 copytree（所有状态）。
    clone_config:
        如果为 True，仅复制配置文件（config.yaml、.env、SOUL.md）。
    no_alias:
        如果为 True，跳过包装器脚本创建。

    Returns
    -------
    Path
        新创建的配置文件目录。
    """
    validate_profile_name(name)

    if name == "default":
        raise ValueError(
            "Cannot create a profile named 'default' — it is the built-in profile (~/.hermes)."
        )

    profile_dir = get_profile_dir(name)
    if profile_dir.exists():
        raise FileExistsError(f"Profile '{name}' already exists at {profile_dir}")

    # 解析克隆源
    source_dir = None
    if clone_from is not None or clone_all or clone_config:
        if clone_from is None:
            # Default: clone from active profile
            from hermes_constants import get_hermes_home
            source_dir = get_hermes_home()
        else:
            validate_profile_name(clone_from)
            source_dir = get_profile_dir(clone_from)
        if not source_dir.is_dir():
            raise FileNotFoundError(
                f"Source profile '{clone_from or 'active'}' does not exist at {source_dir}"
            )

    if clone_all and source_dir:
        # Full copy of source profile
        shutil.copytree(source_dir, profile_dir)
        # Strip runtime files
        for stale in _CLONE_ALL_STRIP:
            (profile_dir / stale).unlink(missing_ok=True)
    else:
        # Bootstrap directory structure
        profile_dir.mkdir(parents=True, exist_ok=True)
        for subdir in _PROFILE_DIRS:
            (profile_dir / subdir).mkdir(parents=True, exist_ok=True)

        # Clone config files from source
        if source_dir is not None:
            for filename in _CLONE_CONFIG_FILES:
                src = source_dir / filename
                if src.exists():
                    shutil.copy2(src, profile_dir / filename)

            # Clone memory and other subdirectory files
            for relpath in _CLONE_SUBDIR_FILES:
                src = source_dir / relpath
                if src.exists():
                    dst = profile_dir / relpath
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)

    # 植入默认 SOUL.md，使用户有一个可以立即自定义的文件。
    # 当配置文件已有时（来自 --clone / --clone-all）跳过。
    soul_path = profile_dir / "SOUL.md"
    if not soul_path.exists():
        try:
            from hermes_cli.default_soul import DEFAULT_SOUL_MD
            soul_path.write_text(DEFAULT_SOUL_MD, encoding="utf-8")
        except Exception:
            pass  # best-effort — don't fail profile creation over this

    return profile_dir


def seed_profile_skills(profile_dir: Path, quiet: bool = False) -> Optional[dict]:
    """通过子进程将内置技能植入配置文件。

    使用子进程是因为 sync_skills() 在模块级别缓存 HERMES_HOME。
    返回同步结果字典，失败时返回 None。
    """
    project_root = Path(__file__).parent.parent.resolve()
    try:
        result = subprocess.run(
            [sys.executable, "-c",
             "import json; from tools.skills_sync import sync_skills; "
             "r = sync_skills(quiet=True); print(json.dumps(r))"],
            env={**os.environ, "HERMES_HOME": str(profile_dir)},
            cwd=str(project_root),
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout.strip())
        if not quiet:
            print(f"⚠ Skill seeding returned exit code {result.returncode}")
            if result.stderr.strip():
                print(f"  {result.stderr.strip()[:200]}")
        return None
    except subprocess.TimeoutExpired:
        if not quiet:
            print("⚠ Skill seeding timed out (60s)")
        return None
    except Exception as e:
        if not quiet:
            print(f"⚠ Skill seeding failed: {e}")
        return None


def delete_profile(name: str, yes: bool = False) -> Path:
    """删除配置文件、其包装器脚本和网关服务。

    如果网关正在运行则停止。先禁用 systemd/launchd 服务
    以防止自动重启。

    Returns the path that was removed.
    """
    validate_profile_name(name)

    if name == "default":
        raise ValueError(
            "Cannot delete the default profile (~/.hermes).\n"
            "To remove everything, use: hermes uninstall"
        )

    profile_dir = get_profile_dir(name)
    if not profile_dir.is_dir():
        raise FileNotFoundError(f"Profile '{name}' does not exist.")

    # 显示将被删除的内容
    model, provider = _read_config_model(profile_dir)
    gw_running = _check_gateway_running(profile_dir)
    skill_count = _count_skills(profile_dir)

    print(f"\nProfile: {name}")
    print(f"Path:    {profile_dir}")
    if model:
        print(f"Model:   {model}" + (f" ({provider})" if provider else ""))
    if skill_count:
        print(f"Skills:  {skill_count}")

    items = [
        "All config, API keys, memories, sessions, skills, cron jobs",
    ]

    # 检查服务
    wrapper_path = _get_wrapper_dir() / name
    has_wrapper = wrapper_path.exists()
    if has_wrapper:
        items.append(f"Command alias ({wrapper_path})")

    print(f"\nThis will permanently delete:")
    for item in items:
        print(f"  • {item}")
    if gw_running:
        print(f"  ⚠ Gateway is running — it will be stopped.")

    # 确认
    if not yes:
        print()
        try:
            confirm = input(f"Type '{name}' to confirm: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nCancelled.")
            return profile_dir
        if confirm != name:
            print("Cancelled.")
            return profile_dir

    # 1. 禁用服务（防止自动重启）
    _cleanup_gateway_service(name, profile_dir)

    # 2. 停止运行中的网关
    if gw_running:
        _stop_gateway_process(profile_dir)

    # 3. 移除包装器脚本
    if has_wrapper:
        if remove_wrapper_script(name):
            print(f"✓ Removed {wrapper_path}")

    # 4. 移除配置文件目录
    try:
        shutil.rmtree(profile_dir)
        print(f"✓ Removed {profile_dir}")
    except Exception as e:
        print(f"⚠ Could not remove {profile_dir}: {e}")

    # 5. 如果 active_profile 指向此配置文件则清除
    try:
        active = get_active_profile()
        if active == name:
            set_active_profile("default")
            print("✓ Active profile reset to default")
    except Exception:
        pass

    print(f"\nProfile '{name}' deleted.")
    return profile_dir


def _cleanup_gateway_service(name: str, profile_dir: Path) -> None:
    """禁用并移除配置文件的 systemd/launchd 服务。"""
    import platform as _platform

    # 推导此配置文件的服务名称
    # 临时设置 HERMES_HOME 以便 _profile_suffix 正确解析
    old_home = os.environ.get("HERMES_HOME")
    try:
        os.environ["HERMES_HOME"] = str(profile_dir)
        from hermes_cli.gateway import get_service_name, get_launchd_plist_path

        if _platform.system() == "Linux":
            svc_name = get_service_name()
            svc_file = Path.home() / ".config" / "systemd" / "user" / f"{svc_name}.service"
            if svc_file.exists():
                subprocess.run(
                    ["systemctl", "--user", "disable", svc_name],
                    capture_output=True, check=False, timeout=10,
                )
                subprocess.run(
                    ["systemctl", "--user", "stop", svc_name],
                    capture_output=True, check=False, timeout=10,
                )
                svc_file.unlink(missing_ok=True)
                subprocess.run(
                    ["systemctl", "--user", "daemon-reload"],
                    capture_output=True, check=False, timeout=10,
                )
                print(f"✓ Service {svc_name} removed")

        elif _platform.system() == "Darwin":
            plist_path = get_launchd_plist_path()
            if plist_path.exists():
                subprocess.run(
                    ["launchctl", "unload", str(plist_path)],
                    capture_output=True, check=False, timeout=10,
                )
                plist_path.unlink(missing_ok=True)
                print(f"✓ Launchd service removed")
    except Exception as e:
        print(f"⚠ Service cleanup: {e}")
    finally:
        if old_home is not None:
            os.environ["HERMES_HOME"] = old_home
        elif "HERMES_HOME" in os.environ:
            del os.environ["HERMES_HOME"]


def _stop_gateway_process(profile_dir: Path) -> None:
    """通过 PID 文件停止运行中的网关进程。"""
    import signal as _signal
    import time as _time

    pid_file = profile_dir / "gateway.pid"
    if not pid_file.exists():
        return

    try:
        raw = pid_file.read_text().strip()
        data = json.loads(raw) if raw.startswith("{") else {"pid": int(raw)}
        pid = int(data["pid"])
        os.kill(pid, _signal.SIGTERM)
        # Wait up to 10s for graceful shutdown
        # 等待最多 10 秒以实现优雅关闭
        for _ in range(20):
            _time.sleep(0.5)
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                print(f"✓ Gateway stopped (PID {pid})")
                return
        # 强制终止
        try:
            os.kill(pid, _signal.SIGKILL)
        except ProcessLookupError:
            pass
        print(f"✓ Gateway force-stopped (PID {pid})")
    except (ProcessLookupError, PermissionError):
        print("✓ Gateway already stopped")
    except Exception as e:
        print(f"⚠ Could not stop gateway: {e}")


# ---------------------------------------------------------------------------
# 活跃配置文件（粘性默认值）
# ---------------------------------------------------------------------------

def get_active_profile() -> str:
    """读取粘性活跃配置文件名称。

    如果 active_profile 文件不存在或为空，返回 ``"default"``。
    """
    path = _get_active_profile_path()
    try:
        name = path.read_text().strip()
        if not name:
            return "default"
        return name
    except (FileNotFoundError, UnicodeDecodeError, OSError):
        return "default"


def set_active_profile(name: str) -> None:
    """设置粘性活跃配置文件。

    写入 ``~/.hermes/active_profile``。使用 ``"default"`` 来清除。
    """
    validate_profile_name(name)
    if name != "default" and not profile_exists(name):
        raise FileNotFoundError(
            f"Profile '{name}' does not exist. "
            f"Create it with: hermes profile create {name}"
        )

    path = _get_active_profile_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if name == "default":
        # 删除文件以表示使用默认配置
        path.unlink(missing_ok=True)
    else:
        # 原子写入
        tmp = path.with_suffix(".tmp")
        tmp.write_text(name + "\n")
        tmp.replace(path)


def get_active_profile_name() -> str:
    """从 HERMES_HOME 推断当前配置文件名称。

    如果 HERMES_HOME 未设置或指向 ``~/.hermes``，返回 ``"default"``。
    如果 HERMES_HOME 指向 ``~/.hermes/profiles/<name>``，返回该配置文件名称。
    如果 HERMES_HOME 设置为未识别的路径，返回 ``"custom"``。
    """
    from hermes_constants import get_hermes_home
    hermes_home = get_hermes_home()
    resolved = hermes_home.resolve()

    default_resolved = _get_default_hermes_home().resolve()
    if resolved == default_resolved:
        return "default"

    profiles_root = _get_profiles_root().resolve()
    try:
        rel = resolved.relative_to(profiles_root)
        parts = rel.parts
        if len(parts) == 1 and _PROFILE_ID_RE.match(parts[0]):
            return parts[0]
    except ValueError:
        pass

    return "custom"


# ---------------------------------------------------------------------------
# 导出 / 导入
# ---------------------------------------------------------------------------

def _default_export_ignore(root_dir: Path):
    """返回 :func:`shutil.copytree` 的 *ignore* 回调函数。

    在根目录层级排除 ``_DEFAULT_EXPORT_EXCLUDE_ROOT`` 中的所有项。
    在所有层级排除 ``__pycache__``、socket 文件和临时文件。
    """

    def _ignore(directory: str, contents: list) -> set:
        ignored: set = set()
        for entry in contents:
        # 通用排除项（任何深度）
            if entry == "__pycache__" or entry.endswith((".sock", ".tmp")):
                ignored.add(entry)
            # npm 锁文件可能出现在根目录
            elif entry in ("package.json", "package-lock.json"):
                ignored.add(entry)
        # 根目录层级排除项
        if Path(directory) == root_dir:
            ignored.update(c for c in contents if c in _DEFAULT_EXPORT_EXCLUDE_ROOT)
        return ignored

    return _ignore


def export_profile(name: str, output_path: str) -> Path:
    """将配置文件导出为 tar.gz 归档。

    返回输出文件路径。
    """
    import tempfile

    validate_profile_name(name)
    profile_dir = get_profile_dir(name)
    if not profile_dir.is_dir():
        raise FileNotFoundError(f"Profile '{name}' does not exist.")

    output = Path(output_path)
    # shutil.make_archive 需要不带扩展名的基本名称
    base = str(output).removesuffix(".tar.gz").removesuffix(".tgz")

    if name == "default":
        # 默认配置文件就是 ~/.hermes 本身 — 其父目录是 ~/，目录名是
        # ".hermes" 而不是 "default"。我们在临时目录下建立一个干净的副本，
        # 使归档包含 ``default/...``。
        with tempfile.TemporaryDirectory() as tmpdir:
            staged = Path(tmpdir) / "default"
            shutil.copytree(
                profile_dir,
                staged,
                ignore=_default_export_ignore(profile_dir),
            )
            result = shutil.make_archive(base, "gztar", tmpdir, "default")
            return Path(result)

    # 命名配置文件 — stage a filtered copy to exclude credentials
    with tempfile.TemporaryDirectory() as tmpdir:
        staged = Path(tmpdir) / name
        _CREDENTIAL_FILES = {"auth.json", ".env"}
        shutil.copytree(
            profile_dir,
            staged,
            ignore=lambda d, contents: _CREDENTIAL_FILES & set(contents),
        )
        result = shutil.make_archive(base, "gztar", tmpdir, name)
        return Path(result)


def _normalize_profile_archive_parts(member_name: str) -> List[str]:
    """返回配置文件归档成员的安全路径部分。"""
    normalized_name = member_name.replace("\\", "/")
    posix_path = PurePosixPath(normalized_name)
    windows_path = PureWindowsPath(member_name)

    if (
        not normalized_name
        or posix_path.is_absolute()
        or windows_path.is_absolute()
        or windows_path.drive
    ):
        raise ValueError(f"Unsafe archive member path: {member_name}")

    parts = [part for part in posix_path.parts if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        raise ValueError(f"Unsafe archive member path: {member_name}")
    return parts


def _safe_extract_profile_archive(archive: Path, destination: Path) -> None:
    """提取配置文件归档，不允许路径逃逸或链接。"""
    import tarfile

    with tarfile.open(archive, "r:gz") as tf:
        for member in tf.getmembers():
            parts = _normalize_profile_archive_parts(member.name)
            target = destination.joinpath(*parts)

            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue

            if not member.isfile():
                raise ValueError(
                    f"Unsupported archive member type: {member.name}"
                )

            target.parent.mkdir(parents=True, exist_ok=True)
            extracted = tf.extractfile(member)
            if extracted is None:
                raise ValueError(f"Cannot read archive member: {member.name}")

            with extracted, open(target, "wb") as dst:
                shutil.copyfileobj(extracted, dst)

            try:
                os.chmod(target, member.mode & 0o777)
            except OSError:
                pass


def import_profile(archive_path: str, name: Optional[str] = None) -> Path:
    """从 tar.gz 归档导入配置文件。

    如果未指定 *name*，从归档的顶层目录推断。
    返回导入的配置文件目录。
    """
    import tarfile

    archive = Path(archive_path)
    if not archive.exists():
        raise FileNotFoundError(f"Archive not found: {archive}")

    # 查看归档以找到顶层目录名称
    with tarfile.open(archive, "r:gz") as tf:
        top_dirs = {
            parts[0]
            for member in tf.getmembers()
            for parts in [_normalize_profile_archive_parts(member.name)]
            if len(parts) > 1 or member.isdir()
        }
        if not top_dirs:
            top_dirs = {
                _normalize_profile_archive_parts(member.name)[0]
                for member in tf.getmembers()
                if member.isdir()
            }

    inferred_name = name or (top_dirs.pop() if len(top_dirs) == 1 else None)
    if not inferred_name:
        raise ValueError(
            "Cannot determine profile name from archive. "
            "Specify it explicitly: hermes profile import <archive> --name <name>"
        )

    # 从默认配置文件导出的归档以 "default/" 作为顶层目录。
    # 导入为 "default" 会指向 ~/.hermes 本身 — 禁止此操作，
    # 引导用户使用命名配置文件。
    if inferred_name == "default":
        raise ValueError(
            "Cannot import as 'default' — that is the built-in root profile (~/.hermes). "
            "Specify a different name: hermes profile import <archive> --name <name>"
        )

    validate_profile_name(inferred_name)
    profile_dir = get_profile_dir(inferred_name)
    if profile_dir.exists():
        raise FileExistsError(f"Profile '{inferred_name}' already exists at {profile_dir}")

    profiles_root = _get_profiles_root()
    profiles_root.mkdir(parents=True, exist_ok=True)

    _safe_extract_profile_archive(archive, profiles_root)

    # 如果归档提取到不同名称的目录下，进行重命名
    extracted = profiles_root / (top_dirs.pop() if top_dirs else inferred_name)
    if extracted != profile_dir and extracted.exists():
        extracted.rename(profile_dir)

    return profile_dir


# ---------------------------------------------------------------------------
# 重命名
# ---------------------------------------------------------------------------

def rename_profile(old_name: str, new_name: str) -> Path:
    """重命名配置文件：目录、包装器脚本、服务、活跃配置文件。

    返回新的配置文件目录。
    """
    validate_profile_name(old_name)
    validate_profile_name(new_name)

    if old_name == "default":
        raise ValueError("Cannot rename the default profile.")
    if new_name == "default":
        raise ValueError("Cannot rename to 'default' — it is reserved.")

    old_dir = get_profile_dir(old_name)
    new_dir = get_profile_dir(new_name)

    if not old_dir.is_dir():
        raise FileNotFoundError(f"Profile '{old_name}' does not exist.")
    if new_dir.exists():
        raise FileExistsError(f"Profile '{new_name}' already exists.")

    # 1. 如果运行中则停止网关
    if _check_gateway_running(old_dir):
        _cleanup_gateway_service(old_name, old_dir)
        _stop_gateway_process(old_dir)

    # 2. 重命名目录
    old_dir.rename(new_dir)
    print(f"✓ Renamed {old_dir.name} → {new_dir.name}")

    # 3. 更新包装器脚本
    remove_wrapper_script(old_name)
    collision = check_alias_collision(new_name)
    if not collision:
        create_wrapper_script(new_name)
        print(f"✓ Alias updated: {new_name}")
    else:
        print(f"⚠ Cannot create alias '{new_name}' — {collision}")

    # 4. 如果活跃配置文件指向旧名称，则更新
    try:
        if get_active_profile() == old_name:
            set_active_profile(new_name)
            print(f"✓ Active profile updated: {new_name}")
    except Exception:
        pass

    return new_dir


# ---------------------------------------------------------------------------
# Tab 补全
# ---------------------------------------------------------------------------

def generate_bash_completion() -> str:
    """生成 hermes 配置文件名称的 bash 补全脚本。"""
    return '''# Hermes Agent profile completion
# Add to ~/.bashrc: eval "$(hermes completion bash)"

_hermes_profiles() {
    local profiles_dir="$HOME/.hermes/profiles"
    local profiles="default"
    if [ -d "$profiles_dir" ]; then
        profiles="$profiles $(ls "$profiles_dir" 2>/dev/null)"
    fi
    echo "$profiles"
}

_hermes_completion() {
    local cur prev
    cur="${COMP_WORDS[COMP_CWORD]}"
    prev="${COMP_WORDS[COMP_CWORD-1]}"

    # Complete profile names after -p / --profile
    if [[ "$prev" == "-p" || "$prev" == "--profile" ]]; then
        COMPREPLY=($(compgen -W "$(_hermes_profiles)" -- "$cur"))
        return
    fi

    # Complete profile subcommands
    if [[ "${COMP_WORDS[1]}" == "profile" ]]; then
        case "$prev" in
            profile)
                COMPREPLY=($(compgen -W "list use create delete show alias rename export import" -- "$cur"))
                return
                ;;
            use|delete|show|alias|rename|export)
                COMPREPLY=($(compgen -W "$(_hermes_profiles)" -- "$cur"))
                return
                ;;
        esac
    fi

    # Top-level subcommands
    if [[ "$COMP_CWORD" == 1 ]]; then
        local commands="chat model gateway setup status cron doctor dump config skills tools mcp sessions profile update version"
        COMPREPLY=($(compgen -W "$commands" -- "$cur"))
    fi
}

complete -F _hermes_completion hermes
'''


def generate_zsh_completion() -> str:
    """生成 hermes 配置文件名称的 zsh 补全脚本。"""
    return '''#compdef hermes
# Hermes Agent profile completion
# Add to ~/.zshrc: eval "$(hermes completion zsh)"

_hermes() {
    local -a profiles
    profiles=(default)
    if [[ -d "$HOME/.hermes/profiles" ]]; then
        profiles+=("${(@f)$(ls $HOME/.hermes/profiles 2>/dev/null)}")
    fi

    _arguments \\
        '-p[Profile name]:profile:($profiles)' \\
        '--profile[Profile name]:profile:($profiles)' \\
        '1:command:(chat model gateway setup status cron doctor dump config skills tools mcp sessions profile update version)' \\
        '*::arg:->args'

    case $words[1] in
        profile)
            _arguments '1:action:(list use create delete show alias rename export import)' \\
                        '2:profile:($profiles)'
            ;;
    esac
}

_hermes "$@"
'''


# ---------------------------------------------------------------------------
# 配置文件环境解析（从 _apply_profile_override 调用）
# ---------------------------------------------------------------------------

def resolve_profile_env(profile_name: str) -> str:
    """将配置文件名称解析为 HERMES_HOME 路径字符串。

    在 CLI 入口点的早期阶段调用，在导入任何 hermes 模块之前，
    用于设置 HERMES_HOME 环境变量。
    """
    validate_profile_name(profile_name)
    profile_dir = get_profile_dir(profile_name)

    if profile_name != "default" and not profile_dir.is_dir():
        raise FileNotFoundError(
            f"Profile '{profile_name}' does not exist. "
            f"Create it with: hermes profile create {profile_name}"
        )

    return str(profile_dir)
