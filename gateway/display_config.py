"""各平台显示/详细程度配置解析器。

提供 ``resolve_display_setting()`` — 读取显示设置的唯一入口点，
支持平台特定覆盖和合理的默认值。

解析顺序（第一个非 None 值生效）：
    1. ``display.platforms.<platform>.<key>``  — 显式的平台级用户覆盖
    2. ``display.<key>``                       — 全局用户设置
    3. ``_PLATFORM_DEFAULTS[<platform>][<key>]``  — 内置的合理默认值
    4. ``_GLOBAL_DEFAULTS[<key>]``              — 内置的全局默认值

例外：``display.streaming`` 仅限 CLI。网关的流式传输遵循
顶级 ``streaming`` 配置，除非 ``display.platforms.<platform>.streaming``
设置了显式的平台级覆盖。

向后兼容：当不存在 ``display.platforms`` 条目时，
``display.tool_progress_overrides`` 仍作为 ``tool_progress`` 的后备读取。
配置迁移（版本升级）会自动将旧格式移入新的
``display.platforms`` 结构。
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# 可覆盖的显示设置及其全局默认值
# ---------------------------------------------------------------------------
# 这些是可以按平台配置的设置。
# 其他显示设置（compact、personality、skin 等）仅限 CLI，
# 不参与平台级解析。

_GLOBAL_DEFAULTS: dict[str, Any] = {
    "tool_progress": "all",
    "show_reasoning": False,
    "tool_preview_length": 0,
    "streaming": None,  # None = 跟随顶级 streaming 配置
}

# ---------------------------------------------------------------------------
# 各平台合理的默认值 — 按平台能力分层
# ---------------------------------------------------------------------------
# 第 1 层（高）：支持消息编辑，通常用于个人/团队
# 第 2 层（中）：支持编辑但通常面向工作区/客户
# 第 3 层（低）：不支持编辑 — 每条进度消息都是永久的
# 第 4 层（最低）：批量/非交互式投递

_TIER_HIGH = {
    "tool_progress": "all",
    "show_reasoning": False,
    "tool_preview_length": 40,
    "streaming": None,  # follow global
}

_TIER_MEDIUM = {
    "tool_progress": "new",
    "show_reasoning": False,
    "tool_preview_length": 40,
    "streaming": None,
}

_TIER_LOW = {
    "tool_progress": "off",
    "show_reasoning": False,
    "tool_preview_length": 40,
    "streaming": False,
}

_TIER_MINIMAL = {
    "tool_progress": "off",
    "show_reasoning": False,
    "tool_preview_length": 0,
    "streaming": False,
}

_PLATFORM_DEFAULTS: dict[str, dict[str, Any]] = {
    # Tier 1 — full edit support, personal/team use
    "telegram":    _TIER_HIGH,
    "discord":     _TIER_HIGH,

    # 第 2 层 — 编辑支持，通常是客户/工作区频道
    "slack":           _TIER_MEDIUM,
    "mattermost":      _TIER_MEDIUM,
    "matrix":          _TIER_MEDIUM,
    "feishu":          _TIER_MEDIUM,

    # 第 3 层 — 不支持编辑，进度消息是永久的
    "signal":          _TIER_LOW,
    "whatsapp":        _TIER_MEDIUM,  # Baileys 桥接支持 /edit
    "bluebubbles":     _TIER_LOW,
    "weixin":          _TIER_LOW,
    "wecom":           _TIER_LOW,
    "wecom_callback":  _TIER_LOW,
    "dingtalk":        _TIER_LOW,

    # 第 4 层 — 批量或非交互式投递
    "email":           _TIER_MINIMAL,
    "sms":             _TIER_MINIMAL,
    "webhook":         _TIER_MINIMAL,
    "homeassistant":   _TIER_MINIMAL,
    "api_server":      {**_TIER_HIGH, "tool_preview_length": 0},
}

# 可按平台覆盖的键的规范集合（用于校验）。
OVERRIDEABLE_KEYS = frozenset(_GLOBAL_DEFAULTS.keys())


def resolve_display_setting(
    user_config: dict,
    platform_key: str,
    setting: str,
    fallback: Any = None,
) -> Any:
    """解析带有平台级覆盖支持的显示设置。

    参数
    ----------
    user_config : dict
        完整的已解析 config.yaml 字典。
    platform_key : str
        平台配置键（例如 ``"telegram"``、``"slack"``）。使用
        gateway/run.py 中的 ``_platform_config_key(source.platform)``。
    setting : str
        显示设置名称（例如 ``"tool_progress"``、``"show_reasoning"``）。
    fallback : Any
        在所有位置都找不到该设置时的兜底值。

    返回
    -------
    解析后的值，或在未配置时返回 *fallback*。
    """
    display_cfg = user_config.get("display") or {}

    # 1. 显式平台级覆盖（display.platforms.<platform>.<key>）
    platforms = display_cfg.get("platforms") or {}
    plat_overrides = platforms.get(platform_key)
    if isinstance(plat_overrides, dict):
        val = plat_overrides.get(setting)
        if val is not None:
            return _normalise(setting, val)

    # 1b. 向后兼容：display.tool_progress_overrides.<platform>
    if setting == "tool_progress":
        legacy = display_cfg.get("tool_progress_overrides")
        if isinstance(legacy, dict):
            val = legacy.get(platform_key)
            if val is not None:
                return _normalise(setting, val)

    # 2. 全局用户设置（display.<key>）。跳过 display.streaming 因为
    # 该键仅控制 CLI 终端流式传输；网关 token 流式传输由
    # 顶级 streaming 配置加平台级覆盖控制。
    if setting != "streaming":
        val = display_cfg.get(setting)
        if val is not None:
            return _normalise(setting, val)

    # 3. 内置平台默认值
    plat_defaults = _PLATFORM_DEFAULTS.get(platform_key)
    if plat_defaults:
        val = plat_defaults.get(setting)
        if val is not None:
            return val

    # 4. 内置全局默认值
    val = _GLOBAL_DEFAULTS.get(setting)
    if val is not None:
        return val

    return fallback


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _normalise(setting: str, value: Any) -> Any:
    """规范化 YAML 的特殊行为（裸 ``off`` 在 YAML 1.1 中变为 False）。"""
    if setting == "tool_progress":
        if value is False:
            return "off"
        if value is True:
            return "all"
        return str(value).lower()
    if setting in ("show_reasoning", "streaming"):
        if isinstance(value, str):
            return value.lower() in ("true", "1", "yes", "on")
        return bool(value)
    if setting == "tool_preview_length":
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0
    return value
