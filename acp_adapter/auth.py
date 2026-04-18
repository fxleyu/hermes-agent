"""ACP 认证辅助工具 -- 检测当前配置的 Hermes 提供商。"""

from __future__ import annotations

from typing import Optional


def detect_provider() -> Optional[str]:
    """解析当前活跃的 Hermes 运行时提供商，如果不可用则返回 None。"""
    try:
        from hermes_cli.runtime_provider import resolve_runtime_provider
        runtime = resolve_runtime_provider()
        api_key = runtime.get("api_key")
        provider = runtime.get("provider")
        if isinstance(api_key, str) and api_key.strip() and isinstance(provider, str) and provider.strip():
            return provider.strip().lower()
    except Exception:
        return None
    return None


def has_provider() -> bool:
    """如果 Hermes 能解析出任何运行时提供商凭据则返回 True。"""
    return detect_provider() is not None
