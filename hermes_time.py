"""
Hermes 的时区感知时钟。

提供一个 ``now()`` 辅助函数，根据用户配置的 IANA 时区（例如 ``Asia/Kolkata``）
返回一个时区感知的 datetime。

解析优先级:
  1. ``HERMES_TIMEZONE`` 环境变量
  2. ``~/.hermes/config.yaml`` 中的 ``timezone`` 键
  3. 回退到服务器本地时间（``datetime.now().astimezone()``）

无效的时区值会记录警告并安全回退 -- Hermes 不会因为错误的时区字符串而崩溃。
"""

import logging
import os
from datetime import datetime
from hermes_constants import get_config_path
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from zoneinfo import ZoneInfo
except ImportError:
    # Python 3.8 回退方案（通常不需要 -- Hermes 要求 3.9+）
    from backports.zoneinfo import ZoneInfo  # type: ignore[no-redef]

# 缓存状态 -- 解析一次，后续每次调用复用。
# 调用 reset_cache() 可强制重新解析（例如，配置变更后）。
_cached_tz: Optional[ZoneInfo] = None
_cached_tz_name: Optional[str] = None
_cache_resolved: bool = False


def _resolve_timezone_name() -> str:
    """读取配置的 IANA 时区字符串（或空字符串）。

    回退到 config.yaml 时会进行文件 I/O，因此调用方应缓存结果，
    而不是每次调用 ``now()`` 都重新读取。
    """
    # 1. 环境变量（最高优先级 -- 由 Supervisor 等设置）
    tz_env = os.getenv("HERMES_TIMEZONE", "").strip()
    if tz_env:
        return tz_env

    # 2. config.yaml 中的 ``timezone`` 键
    try:
        import yaml
        config_path = get_config_path()
        if config_path.exists():
            with open(config_path) as f:
                cfg = yaml.safe_load(f) or {}
            tz_cfg = cfg.get("timezone", "")
            if isinstance(tz_cfg, str) and tz_cfg.strip():
                return tz_cfg.strip()
    except Exception:
        pass

    return ""


def _get_zoneinfo(name: str) -> Optional[ZoneInfo]:
    """验证并返回一个 ZoneInfo 对象，无效时返回 None。"""
    if not name:
        return None
    try:
        return ZoneInfo(name)
    except (KeyError, Exception) as exc:
        logger.warning(
            "Invalid timezone '%s': %s. Falling back to server local time.",
            name, exc,
        )
        return None


def get_timezone() -> Optional[ZoneInfo]:
    """返回用户配置的 ZoneInfo，或返回 None（表示使用服务器本地时间）。

    仅解析一次并缓存。配置变更后调用 ``reset_cache()``。
    """
    global _cached_tz, _cached_tz_name, _cache_resolved
    if not _cache_resolved:
        _cached_tz_name = _resolve_timezone_name()
        _cached_tz = _get_zoneinfo(_cached_tz_name)
        _cache_resolved = True
    return _cached_tz


def now() -> datetime:
    """
    返回当前时间，作为时区感知的 datetime。

    如果配置了有效的时区，返回该时区的本地时间。
    否则返回服务器的本地时间（通过 ``astimezone()``）。
    """
    tz = get_timezone()
    if tz is not None:
        return datetime.now(tz)
    # 未配置时区 -- 使用服务器本地时间（仍然是时区感知的）
    return datetime.now().astimezone()
