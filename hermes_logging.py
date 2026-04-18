"""Hermes Agent 的集中式日志配置。

提供一个 ``setup_logging()`` 入口点，供 CLI 和网关在启动早期调用。
所有日志文件存放在 ``~/.hermes/logs/`` 下（通过 ``get_hermes_home()`` 支持 profile 感知）。

生成的日志文件:
    agent.log   — INFO+，所有 agent/工具/会话活动（主日志）
    errors.log  — WARNING+，仅错误和警告（快速问题排查）
    gateway.log — INFO+，仅网关事件（当 mode="gateway" 时创建）

所有文件使用 ``RotatingFileHandler`` 配合 ``RedactingFormatter``，
确保敏感信息不会被写入磁盘。

组件分离:
    gateway.log 仅接收来自 ``gateway.*`` 日志记录器的记录 --
    平台适配器、会话管理、斜杠命令、消息投递。
    agent.log 保持为全局兜底日志（所有内容都会写入其中）。

会话上下文:
    在对话开始时调用 ``set_session_context(session_id)``，
    结束时调用 ``clear_session_context()``。该线程上产生的所有日志行
    都会包含 ``[session_id]`` 用于过滤/关联。
"""

import logging
import os
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional, Sequence

from hermes_constants import get_config_path, get_hermes_home

# 哨兵标记，用于追踪 setup_logging() 是否已执行。
# 该函数是幂等的 -- 调用两次是安全的，但第二次调用为空操作，
# 除非传入 ``force=True``。
_logging_initialized = False

# 线程本地存储，用于保存每个对话的会话上下文。
_session_context = threading.local()

# 默认日志格式 -- 包含时间戳、级别、可选的会话标签、日志记录器名称和消息。
# ``%(session_tag)s`` 字段通过下方的 _install_session_record_factory() 确保
# 在每条 LogRecord 上都存在。
_LOG_FORMAT = "%(asctime)s %(levelname)s%(session_tag)s %(name)s: %(message)s"
_LOG_FORMAT_VERBOSE = "%(asctime)s - %(name)s - %(levelname)s%(session_tag)s - %(message)s"

# 在 DEBUG/INFO 级别输出较多噪音的第三方日志记录器。
_NOISY_LOGGERS = (
    "openai",
    "openai._base_client",
    "httpx",
    "httpcore",
    "asyncio",
    "hpack",
    "hpack.hpack",
    "grpc",
    "modal",
    "urllib3",
    "urllib3.connectionpool",
    "websockets",
    "charset_normalizer",
    "markdown_it",
)


# ---------------------------------------------------------------------------
# 公共会话上下文 API
# ---------------------------------------------------------------------------

def set_session_context(session_id: str) -> None:
    """设置当前线程的会话 ID。

    此后该线程上的所有日志记录将在格式化输出中包含 ``[session_id]``。
    在 ``run_conversation()`` 开始时调用。
    """
    _session_context.session_id = session_id


def clear_session_context() -> None:
    """清除当前线程的会话 ID。"""
    _session_context.session_id = None


# ---------------------------------------------------------------------------
# 记录工厂 -- 在创建每条 LogRecord 时注入 session_tag
# ---------------------------------------------------------------------------

def _install_session_record_factory() -> None:
    """用一个添加了 ``session_tag`` 的工厂替换全局 LogRecord 工厂。

    与 handler 或 logger 上的 ``logging.Filter`` 不同，记录工厂对
    进程中的每条记录都生效 -- 包括从子日志记录器传播的记录和
    第三方 handler 处理的记录。这保证 ``%(session_tag)s`` 在
    格式字符串中始终可用，避免了在 handler 使用我们的格式但未
    附加 ``_SessionFilter`` 时产生的 KeyError。

    幂等 -- 通过标记属性检查来避免在模块重新加载时重复包装。
    """
    current_factory = logging.getLogRecordFactory()
    if getattr(current_factory, "_hermes_session_injector", False):
        return  # 已安装

    def _session_record_factory(*args, **kwargs):
        record = current_factory(*args, **kwargs)
        sid = getattr(_session_context, "session_id", None)
        record.session_tag = f" [{sid}]" if sid else ""  # type: ignore[attr-defined]
        return record

    _session_record_factory._hermes_session_injector = True  # type: ignore[attr-defined]
    logging.setLogRecordFactory(_session_record_factory)


# 在导入时立即安装 -- 从此刻起 session_tag 在所有记录上可用，
# 甚至在调用 setup_logging() 之前。
_install_session_record_factory()


# ---------------------------------------------------------------------------
# 过滤器
# ---------------------------------------------------------------------------

class _ComponentFilter(logging.Filter):
    """仅通过日志记录器名称以 *prefixes* 中某一个开头的记录。

    用于将网关特定的记录路由到 ``gateway.log``，
    同时保持 ``agent.log`` 作为全局兜底日志。
    """

    def __init__(self, prefixes: Sequence[str]) -> None:
        super().__init__()
        self._prefixes = tuple(prefixes)

    def filter(self, record: logging.LogRecord) -> bool:
        return record.name.startswith(self._prefixes)


# 属于各组件的日志记录器名称前缀。
# 由 _ComponentFilter 使用，也对外暴露给 ``hermes logs --component``。
COMPONENT_PREFIXES = {
    "gateway": ("gateway",),
    "agent": ("agent", "run_agent", "model_tools", "batch_runner"),
    "tools": ("tools",),
    "cli": ("hermes_cli", "cli"),
    "cron": ("cron",),
}


# ---------------------------------------------------------------------------
# 主要配置
# ---------------------------------------------------------------------------

def setup_logging(
    *,
    hermes_home: Optional[Path] = None,
    log_level: Optional[str] = None,
    max_size_mb: Optional[int] = None,
    backup_count: Optional[int] = None,
    mode: Optional[str] = None,
    force: bool = False,
) -> Path:
    """配置 Hermes 日志子系统。

    可安全多次调用 -- 第二次调用为空操作，
    除非 *force* 为 ``True``。

    参数
    ----------
    hermes_home
        覆盖 Hermes 主目录。回退到
        ``get_hermes_home()``（支持 profile 感知）。
    log_level
        ``agent.log`` 文件处理器的最低级别。接受任何
        标准 Python 级别名称（``"DEBUG"``、``"INFO"``、``"WARNING"``）。
        默认为 ``"INFO"`` 或 config.yaml ``logging.level`` 中的值。
    max_size_mb
        每个日志文件在轮转前的最大大小（MB）。
        默认为 5 或 config.yaml ``logging.max_size_mb`` 中的值。
    backup_count
        保留的轮转备份文件数量。
        默认为 3 或 config.yaml ``logging.backup_count`` 中的值。
    mode
        调用方上下文: ``"cli"``、``"gateway"``、``"cron"``。
        当为 ``"gateway"`` 时，会额外创建 ``gateway.log`` 文件，
        仅接收网关组件的记录。
    force
        即使已调用过也重新执行配置。

    返回
    -------
    Path
        写入日志文件的 ``logs/`` 目录。
    """
    global _logging_initialized
    if _logging_initialized and not force:
        home = hermes_home or get_hermes_home()
        return home / "logs"

    home = hermes_home or get_hermes_home()
    log_dir = home / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    # 尽力读取配置默认值（配置可能尚未加载）。
    cfg_level, cfg_max_size, cfg_backup = _read_logging_config()

    level_name = (log_level or cfg_level or "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    max_bytes = (max_size_mb or cfg_max_size or 5) * 1024 * 1024
    backups = backup_count or cfg_backup or 3

    # 延迟导入以避免模块加载时的循环依赖。
    from agent.redact import RedactingFormatter

    root = logging.getLogger()

    # --- agent.log (INFO+) — 主活动日志 ------------------------------------
    _add_rotating_handler(
        root,
        log_dir / "agent.log",
        level=level,
        max_bytes=max_bytes,
        backup_count=backups,
        formatter=RedactingFormatter(_LOG_FORMAT),
    )

    # --- errors.log (WARNING+) — 快速问题排查日志 --------------------------
    _add_rotating_handler(
        root,
        log_dir / "errors.log",
        level=logging.WARNING,
        max_bytes=2 * 1024 * 1024,
        backup_count=2,
        formatter=RedactingFormatter(_LOG_FORMAT),
    )

    # --- gateway.log (INFO+, 仅网关组件) -----------------------------------
    if mode == "gateway":
        _add_rotating_handler(
            root,
            log_dir / "gateway.log",
            level=logging.INFO,
            max_bytes=5 * 1024 * 1024,
            backup_count=3,
            formatter=RedactingFormatter(_LOG_FORMAT),
            log_filter=_ComponentFilter(COMPONENT_PREFIXES["gateway"]),
        )

    # 确保根日志记录器级别足够低，使处理器能触发。
    if root.level == logging.NOTSET or root.level > level:
        root.setLevel(level)

    # 抑制产生大量噪音的第三方日志记录器。
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    _logging_initialized = True
    return log_dir


def setup_verbose_logging() -> None:
    """为 ``--verbose`` / ``-v`` 模式启用 DEBUG 级别的控制台日志。

    由 ``AIAgent.__init__()`` 在 ``verbose_logging=True`` 时调用。
    """
    from agent.redact import RedactingFormatter

    root = logging.getLogger()

    # 避免重复添加控制台处理器。
    for h in root.handlers:
        if isinstance(h, logging.StreamHandler) and not isinstance(h, RotatingFileHandler):
            if getattr(h, "_hermes_verbose", False):
                return

    handler = logging.StreamHandler()
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(RedactingFormatter(_LOG_FORMAT_VERBOSE, datefmt="%H:%M:%S"))
    handler._hermes_verbose = True  # type: ignore[attr-defined]
    root.addHandler(handler)

    # 降低根日志记录器级别以使 DEBUG 记录到达所有处理器。
    if root.level > logging.DEBUG:
        root.setLevel(logging.DEBUG)

    # 保持第三方库为 WARNING 级别以减少噪音。
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    # rex-deploy 设为 INFO 以显示沙箱状态。
    logging.getLogger("rex-deploy").setLevel(logging.INFO)


# ---------------------------------------------------------------------------
# 内部辅助函数
# ---------------------------------------------------------------------------

class _ManagedRotatingFileHandler(RotatingFileHandler):
    """在托管模式下确保组可写权限的 RotatingFileHandler。

    在托管模式 (NixOS) 下，stateDir 使用 setgid (2770)，
    因此新文件会继承 hermes 组。但 _open()（初始创建）和
    doRollover() 都通过 open() 创建文件，使用进程的 umask --
    通常是 0022，产生 0644 权限。此子类在两种操作后都应用
    chmod 0660，以便网关和交互用户可以共享日志文件。
    """

    def __init__(self, *args, **kwargs):
        from hermes_cli.config import is_managed
        self._managed = is_managed()
        super().__init__(*args, **kwargs)

    def _chmod_if_managed(self):
        """如果处于托管模式，则修改文件权限为 0660。"""
        if self._managed:
            try:
                os.chmod(self.baseFilename, 0o660)
            except OSError:
                pass

    def _open(self):
        stream = super()._open()
        self._chmod_if_managed()
        return stream

    def doRollover(self):
        super().doRollover()
        self._chmod_if_managed()


def _add_rotating_handler(
    logger: logging.Logger,
    path: Path,
    *,
    level: int,
    max_bytes: int,
    backup_count: int,
    formatter: logging.Formatter,
    log_filter: Optional[logging.Filter] = None,
) -> None:
    """向 *logger* 添加一个 ``RotatingFileHandler``，如果同一解析路径
    已存在相同处理器则跳过（幂等）。

    参数
    ----------
    log_filter
        附加到处理器的可选过滤器（例如 ``_ComponentFilter``
        用于 gateway.log）。
    """
    resolved = path.resolve()
    # 检查是否已存在相同文件路径的处理器，避免重复
    for existing in logger.handlers:
        if (
            isinstance(existing, RotatingFileHandler)
            and Path(getattr(existing, "baseFilename", "")).resolve() == resolved
        ):
            return  # 已附加

    path.parent.mkdir(parents=True, exist_ok=True)
    handler = _ManagedRotatingFileHandler(
        str(path), maxBytes=max_bytes, backupCount=backup_count,
        encoding="utf-8",
    )
    handler.setLevel(level)
    handler.setFormatter(formatter)
    if log_filter is not None:
        handler.addFilter(log_filter)
    logger.addHandler(handler)


def _read_logging_config():
    """尽力读取 config.yaml 中的 ``logging.*`` 配置。

    返回 ``(level, max_size_mb, backup_count)`` -- 任何一项都可能为 ``None``。
    """
    try:
        import yaml
        config_path = get_config_path()
        if config_path.exists():
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            log_cfg = cfg.get("logging", {})
            if isinstance(log_cfg, dict):
                return (
                    log_cfg.get("level"),
                    log_cfg.get("max_size_mb"),
                    log_cfg.get("backup_count"),
                )
    except Exception:
        pass
    return (None, None, None)
