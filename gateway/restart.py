"""网关重启的共享常量和解析辅助函数。"""

from hermes_cli.config import DEFAULT_CONFIG

# sysexits.h 中的 EX_TEMPFAIL — 用于请求服务管理器在
# 优雅排空/重载路径完成后重启网关。
GATEWAY_SERVICE_RESTART_EXIT_CODE = 75

DEFAULT_GATEWAY_RESTART_DRAIN_TIMEOUT = float(
    DEFAULT_CONFIG["agent"]["restart_drain_timeout"]
)


def parse_restart_drain_timeout(raw: object) -> float:
    """解析配置的排空超时，如果无效则回退到共享默认值。"""
    try:
        value = float(raw) if str(raw or "").strip() else DEFAULT_GATEWAY_RESTART_DRAIN_TIMEOUT
    except (TypeError, ValueError):
        return DEFAULT_GATEWAY_RESTART_DRAIN_TIMEOUT
    return max(0.0, value)
