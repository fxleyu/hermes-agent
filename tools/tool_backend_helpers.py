"""工具后端选择的共享辅助函数。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict


_DEFAULT_BROWSER_PROVIDER = "local"
_DEFAULT_MODAL_MODE = "auto"
_VALID_MODAL_MODES = {"auto", "direct", "managed"}


def managed_nous_tools_enabled() -> bool:
    """当用户拥有活跃的付费 Nous 订阅时返回 True。

    工具网关对任何非免费层级的 Nous 订阅者可用。
    我们有意捕获所有异常并返回 False——永远不阻塞代理启动路径。
    """
    try:
        from hermes_cli.auth import get_nous_auth_status

        status = get_nous_auth_status()
        if not status.get("logged_in"):
            return False

        from hermes_cli.models import check_nous_free_tier

        if check_nous_free_tier():
            return False  # 免费层级用户无法访问网关
        return True
    except Exception:
        return False


def normalize_browser_cloud_provider(value: object | None) -> str:
    """返回规范化的浏览器提供者键。"""
    provider = str(value or _DEFAULT_BROWSER_PROVIDER).strip().lower()
    return provider or _DEFAULT_BROWSER_PROVIDER


def coerce_modal_mode(value: object | None) -> str:
    """如果请求的 modal 模式有效则返回它，否则返回默认值。"""
    mode = str(value or _DEFAULT_MODAL_MODE).strip().lower()
    if mode in _VALID_MODAL_MODES:
        return mode
    return _DEFAULT_MODAL_MODE


def normalize_modal_mode(value: object | None) -> str:
    """返回规范化的 modal 执行模式。"""
    return coerce_modal_mode(value)


def has_direct_modal_credentials() -> bool:
    """当直接 Modal 凭证/配置可用时返回 True。"""
    return bool(
        (os.getenv("MODAL_TOKEN_ID") and os.getenv("MODAL_TOKEN_SECRET"))
        or (Path.home() / ".modal.toml").exists()
    )


def resolve_modal_backend_state(
    modal_mode: object | None,
    *,
    has_direct: bool,
    managed_ready: bool,
) -> Dict[str, Any]:
    """解析直接模式 vs 托管模式的 Modal 后端选择。

    语义:
    - ``direct`` 表示仅直接模式
    - ``managed`` 表示仅托管模式
    - ``auto`` 优先使用可用的托管模式，然后回退到直接模式
    """
    requested_mode = coerce_modal_mode(modal_mode)
    normalized_mode = normalize_modal_mode(modal_mode)
    managed_mode_blocked = (
        requested_mode == "managed" and not managed_nous_tools_enabled()
    )

    if normalized_mode == "managed":
        selected_backend = "managed" if managed_nous_tools_enabled() and managed_ready else None
    elif normalized_mode == "direct":
        selected_backend = "direct" if has_direct else None
    else:
        selected_backend = "managed" if managed_nous_tools_enabled() and managed_ready else "direct" if has_direct else None

    return {
        "requested_mode": requested_mode,
        "mode": normalized_mode,
        "has_direct": has_direct,
        "managed_ready": managed_ready,
        "managed_mode_blocked": managed_mode_blocked,
        "selected_backend": selected_backend,
    }


def resolve_openai_audio_api_key() -> str:
    """优先使用语音工具密钥，但回退到普通 OpenAI 密钥。"""
    return (
        os.getenv("VOICE_TOOLS_OPENAI_KEY", "")
        or os.getenv("OPENAI_API_KEY", "")
    ).strip()


def prefers_gateway(config_section: str) -> bool:
    """当用户为此工具选择了工具网关时返回 True。

    从 config.yaml 中读取 ``<section>.use_gateway``。不会抛出异常。
    """
    try:
        from hermes_cli.config import load_config
        section = (load_config() or {}).get(config_section)
        if isinstance(section, dict):
            return bool(section.get("use_gateway"))
    except Exception:
        pass
    return False
