"""Hermes Agent 的共享常量。

无依赖的安全导入模块 -- 可以从任何地方导入而不会引发循环导入。
"""

import os
from pathlib import Path


def get_hermes_home() -> Path:
    """返回 Hermes 主目录（默认: ~/.hermes）。

    读取 HERMES_HOME 环境变量，回退到 ~/.hermes。
    这是唯一的权威来源 -- 所有其他副本都应从此处导入。
    """
    return Path(os.getenv("HERMES_HOME", Path.home() / ".hermes"))


def get_default_hermes_root() -> Path:
    """返回用于 profile 级别操作的 Hermes 根目录。

    在标准部署中，这是 ``~/.hermes``。

    在 Docker 或自定义部署中，当 ``HERMES_HOME`` 指向 ``~/.hermes`` 之外
    （例如 ``/opt/data``）时，直接返回 ``HERMES_HOME`` -- 它本身就是根目录。

    在 profile 模式下，当 ``HERMES_HOME`` 为 ``<root>/profiles/<name>`` 时，
    返回 ``<root>``，以便 ``profile list`` 能看到所有 profile。
    同时适用于标准布局（``~/.hermes/profiles/coder``）和 Docker 布局
    （``/opt/data/profiles/coder``）。

    安全导入 -- 除标准库外无其他依赖。
    """
    native_home = Path.home() / ".hermes"
    env_home = os.environ.get("HERMES_HOME", "")
    if not env_home:
        return native_home
    env_path = Path(env_home)
    try:
        env_path.resolve().relative_to(native_home.resolve())
        # HERMES_HOME 在 ~/.hermes 下（正常模式或 profile 模式）
        return native_home
    except ValueError:
        pass

    # Docker / 自定义部署场景。
    # 检查是否为 profile 路径: <root>/profiles/<name>
    # 如果直接父目录名为 "profiles"，则根目录是祖父目录 --
    # 这能正确处理 Docker profile 的情况。
    if env_path.parent.name == "profiles":
        return env_path.parent.parent

    # 不是 profile 路径 -- HERMES_HOME 本身就是根目录
    return env_path


def get_optional_skills_dir(default: Path | None = None) -> Path:
    """返回可选技能目录，支持包管理器封装。

    打包安装可能会将 ``optional-skills`` 放在 Python 包目录树之外，
    并通过 ``HERMES_OPTIONAL_SKILLS`` 环境变量暴露其路径。
    """
    override = os.getenv("HERMES_OPTIONAL_SKILLS", "").strip()
    if override:
        return Path(override)
    if default is not None:
        return default
    return get_hermes_home() / "optional-skills"


def get_hermes_dir(new_subpath: str, old_name: str) -> Path:
    """解析 Hermes 子目录，支持向后兼容。

    新安装使用统一布局（例如 ``cache/images``）。
    已有旧路径（例如 ``image_cache``）的现有安装会继续使用旧路径 --
    无需迁移。

    参数:
        new_subpath: 相对于 HERMES_HOME 的首选路径（例如 ``"cache/images"``）。
        old_name: 相对于 HERMES_HOME 的旧版路径（例如 ``"image_cache"``）。

    返回:
        绝对 ``Path`` -- 如果旧位置存在则返回旧位置，否则返回新位置。
    """
    home = get_hermes_home()
    old_path = home / old_name
    # 优先使用已存在的旧路径，保持向后兼容
    if old_path.exists():
        return old_path
    return home / new_subpath


def display_hermes_home() -> str:
    """返回当前 HERMES_HOME 的用户友好显示字符串。

    使用 ``~/`` 简写以提高可读性::

        默认:    ``~/.hermes``
        profile: ``~/.hermes/profiles/coder``
        自定义:  ``/opt/hermes-custom``

    在**面向用户**的打印/日志消息中使用此函数，而非硬编码 ``~/.hermes``。
    如果代码需要真实的 ``Path``，请改用 :func:`get_hermes_home`。
    """
    home = get_hermes_home()
    try:
        return "~/" + str(home.relative_to(Path.home()))
    except ValueError:
        return str(home)


def get_subprocess_home() -> str | None:
    """返回子进程使用的 per-profile HOME 目录，或返回 None。

    当 ``{HERMES_HOME}/home/`` 目录存在时，子进程应将其用作 ``HOME``，
    这样系统工具（git、ssh、gh、npm 等）就会将配置写入 Hermes 数据目录，
    而不是操作系统级别的 ``/root`` 或 ``~/``。这提供了:

    * **Docker 持久化** -- 工具配置存储在持久化卷中。
    * **Profile 隔离** -- 每个 profile 拥有独立的 git 身份、SSH
      密钥、gh 令牌等。

    Python 进程自身的 ``os.environ["HOME"]`` 和 ``Path.home()``
    **不会**被修改 -- 只有子进程环境才应注入此值。
    激活方式基于目录: 如果 ``home/`` 子目录不存在，返回 ``None``，行为不变。
    """
    hermes_home = os.getenv("HERMES_HOME")
    if not hermes_home:
        return None
    profile_home = os.path.join(hermes_home, "home")
    if os.path.isdir(profile_home):
        return profile_home
    return None


# 有效的推理力度级别元组
VALID_REASONING_EFFORTS = ("minimal", "low", "medium", "high", "xhigh")


def parse_reasoning_effort(effort: str) -> dict | None:
    """将推理力度级别解析为配置字典。

    有效级别: "none"、"minimal"、"low"、"medium"、"high"、"xhigh"。
    当输入为空或无法识别时返回 None（调用方使用默认值）。
    "none" 返回 {"enabled": False}。
    有效力度级别返回 {"enabled": True, "effort": <级别>}。
    """
    if not effort or not effort.strip():
        return None
    effort = effort.strip().lower()
    if effort == "none":
        return {"enabled": False}
    if effort in VALID_REASONING_EFFORTS:
        return {"enabled": True, "effort": effort}
    return None


def is_termux() -> bool:
    """当运行在 Termux (Android) 环境中时返回 True。

    检查 ``TERMUX_VERSION``（由 Termux 设置）或 Termux 特有的
    ``PREFIX`` 路径。安全导入 -- 无重量级依赖。
    """
    prefix = os.getenv("PREFIX", "")
    return bool(os.getenv("TERMUX_VERSION") or "com.termux/files/usr" in prefix)


# 缓存的 WSL 检测结果，避免重复读取 /proc/version
_wsl_detected: bool | None = None


def is_wsl() -> bool:
    """当运行在 WSL (Windows Subsystem for Linux) 中时返回 True。

    检查 ``/proc/version`` 中 WSL1 和 WSL2 都会注入的 ``microsoft`` 标记。
    结果在进程生命周期内缓存。安全导入 -- 无重量级依赖。
    """
    global _wsl_detected
    if _wsl_detected is not None:
        return _wsl_detected
    try:
        with open("/proc/version", "r") as f:
            _wsl_detected = "microsoft" in f.read().lower()
    except Exception:
        _wsl_detected = False
    return _wsl_detected


# 缓存的容器检测结果
_container_detected: bool | None = None


def is_container() -> bool:
    """当运行在 Docker/Podman 容器中时返回 True。

    检查 ``/.dockerenv`` (Docker)、``/run/.containerenv`` (Podman)
    以及 ``/proc/1/cgroup`` 中的容器运行时标记。
    结果在进程生命周期内缓存。安全导入 -- 无重量级依赖。
    """
    global _container_detected
    if _container_detected is not None:
        return _container_detected
    # 检查 Docker 环境标记文件
    if os.path.exists("/.dockerenv"):
        _container_detected = True
        return True
    # 检查 Podman 环境标记文件
    if os.path.exists("/run/.containerenv"):
        _container_detected = True
        return True
    try:
        # 检查 cgroup 中的容器运行时标记
        with open("/proc/1/cgroup", "r") as f:
            cgroup = f.read()
            if "docker" in cgroup or "podman" in cgroup or "/lxc/" in cgroup:
                _container_detected = True
                return True
    except OSError:
        pass
    _container_detected = False
    return False


# ─── 常用路径 ─────────────────────────────────────────────────────────────────


def get_config_path() -> Path:
    """返回 HERMES_HOME 下 ``config.yaml`` 的路径。

    替代了在 7+ 个文件（skill_utils.py、hermes_logging.py、hermes_time.py 等）中
    重复出现的 ``get_hermes_home() / "config.yaml"`` 模式。
    """
    return get_hermes_home() / "config.yaml"


def get_skills_dir() -> Path:
    """返回 HERMES_HOME 下技能目录的路径。"""
    return get_hermes_home() / "skills"



def get_env_path() -> Path:
    """返回 HERMES_HOME 下 ``.env`` 文件的路径。"""
    return get_hermes_home() / ".env"


# ─── 网络偏好设置 ─────────────────────────────────────────────────────────────


def apply_ipv4_preference(force: bool = False) -> None:
    """猴子补丁 ``socket.getaddrinfo`` 以优先使用 IPv4 连接。

    在 IPv6 不可达或有故障的服务器上，Python 会先尝试 AAAA 记录，
    等待完整的 TCP 超时后才回退到 IPv4。这会影响 httpx、requests、
    urllib、OpenAI SDK -- 所有使用 ``socket.getaddrinfo`` 的库。

    当 *force* 为 True 时，补丁 ``getaddrinfo``，使 ``family=AF_UNSPEC``
    （默认值）的调用改为以 ``AF_INET`` 解析，完全跳过 IPv6。
    如果不存在 A 记录，则回退到原始的无过滤解析，以确保纯 IPv6 主机仍可用。

    可安全多次调用 -- 仅补丁一次。
    在 ``config.yaml`` 中设置 ``network.force_ipv4: true`` 以启用。
    """
    if not force:
        return

    import socket

    # 防止重复补丁
    if getattr(socket.getaddrinfo, "_hermes_ipv4_patched", False):
        return

    _original_getaddrinfo = socket.getaddrinfo

    def _ipv4_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        if family == 0:  # AF_UNSPEC -- 调用方未指定特定的地址族
            try:
                return _original_getaddrinfo(
                    host, port, socket.AF_INET, type, proto, flags
                )
            except socket.gaierror:
                # 没有 A 记录 -- 回退到完整解析（纯 IPv6 主机）
                return _original_getaddrinfo(host, port, family, type, proto, flags)
        return _original_getaddrinfo(host, port, family, type, proto, flags)

    _ipv4_getaddrinfo._hermes_ipv4_patched = True  # type: ignore[attr-defined]
    socket.getaddrinfo = _ipv4_getaddrinfo  # type: ignore[assignment]


OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_MODELS_URL = f"{OPENROUTER_BASE_URL}/models"

AI_GATEWAY_BASE_URL = "https://ai-gateway.vercel.sh/v1"
