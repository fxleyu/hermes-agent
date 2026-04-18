"""环境变量透传注册表。

在前置元数据中声明了 ``required_environment_variables`` 的技能需要这些变量
在沙箱执行环境（execute_code、terminal）中可用。默认情况下，两种沙箱出于
安全考虑都会从子进程环境中剥离敏感信息。本模块提供了一个会话范围的白名单，
使技能声明的变量（以及用户配置的覆盖）能够透传。

白名单由两个来源填充：

1. **技能声明** — 当通过 ``skill_view`` 加载技能时，其
   ``required_environment_variables`` 会自动注册到此处。
2. **用户配置** — config.yaml 中的 ``terminal.env_passthrough`` 允许用户
   为非技能场景显式添加白名单变量。

``code_execution_tool.py`` 和 ``tools/environments/local.py`` 在剥离变量
之前都会查询 :func:`is_env_passthrough`。
"""

from __future__ import annotations

import logging
from contextvars import ContextVar
from typing import Iterable

logger = logging.getLogger(__name__)

# 会话范围的允许透传到沙箱的环境变量名集合。
# 使用 ContextVar 以防止网关管道中跨会话的数据泄漏。
_allowed_env_vars_var: ContextVar[set[str]] = ContextVar("_allowed_env_vars")


def _get_allowed() -> set[str]:
    """获取或创建当前上下文/会话的允许环境变量集合。"""
    try:
        return _allowed_env_vars_var.get()
    except LookupError:
        val: set[str] = set()
        _allowed_env_vars_var.set(val)
        return val


# 基于配置的白名单缓存（每个进程加载一次）。
_config_passthrough: frozenset[str] | None = None


def register_env_passthrough(var_names: Iterable[str]) -> None:
    """将环境变量名注册为沙箱环境中允许透传的变量。

    通常在技能声明 ``required_environment_variables`` 时调用。
    """
    for name in var_names:
        name = name.strip()
        if name:
            _get_allowed().add(name)
            logger.debug("env passthrough: registered %s", name)


def _load_config_passthrough() -> frozenset[str]:
    """从 config.yaml 加载 ``tools.env_passthrough``（已缓存）。"""
    global _config_passthrough
    if _config_passthrough is not None:
        return _config_passthrough

    result: set[str] = set()
    try:
        from hermes_cli.config import read_raw_config
        cfg = read_raw_config()
        passthrough = cfg.get("terminal", {}).get("env_passthrough")
        if isinstance(passthrough, list):
            for item in passthrough:
                if isinstance(item, str) and item.strip():
                    result.add(item.strip())
    except Exception as e:
        logger.debug("Could not read tools.env_passthrough from config: %s", e)

    _config_passthrough = frozenset(result)
    return _config_passthrough


def is_env_passthrough(var_name: str) -> bool:
    """检查 *var_name* 是否允许透传到沙箱。

    如果该变量由技能注册或在用户的 ``tools.env_passthrough`` 配置中
    列出，则返回 ``True``。
    """
    if var_name in _get_allowed():
        return True
    return var_name in _load_config_passthrough()


def get_all_passthrough() -> frozenset[str]:
    """返回技能注册和基于配置的透传变量的并集。"""
    return frozenset(_get_allowed()) | _load_config_passthrough()


def clear_env_passthrough() -> None:
    """重置技能范围的白名单（例如在会话重置时调用）。"""
    _get_allowed().clear()


