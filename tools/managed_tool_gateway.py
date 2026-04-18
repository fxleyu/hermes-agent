"""通用托管工具网关辅助模块，用于 Nous 托管的供应商代理透传。"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Callable, Optional

logger = logging.getLogger(__name__)

from hermes_constants import get_hermes_home
from tools.tool_backend_helpers import managed_nous_tools_enabled

# 默认网关域名
_DEFAULT_TOOL_GATEWAY_DOMAIN = "nousresearch.com"
# 默认 URL 协议方案
_DEFAULT_TOOL_GATEWAY_SCHEME = "https"
# 访问令牌刷新提前量（秒），在令牌过期前提前刷新以避免请求失败
_NOUS_ACCESS_TOKEN_REFRESH_SKEW_SECONDS = 120


@dataclass(frozen=True)
class ManagedToolGatewayConfig:
    """托管工具网关的不可变配置数据类。"""
    vendor: str              # 供应商名称
    gateway_origin: str      # 网关来源 URL
    nous_user_token: str     # Nous 用户访问令牌
    managed_mode: bool       # 是否为托管模式


def auth_json_path():
    """返回 Hermes 认证存储路径，支持 HERMES_HOME 环境变量覆盖。"""
    return get_hermes_home() / "auth.json"


def _read_nous_provider_state() -> Optional[dict]:
    """从 auth.json 中读取 Nous 提供者的认证状态信息。"""
    try:
        path = auth_json_path()
        if not path.is_file():
            return None
        data = json.loads(path.read_text())
        # 从 JSON 中提取 providers.nous 字段
        providers = data.get("providers", {})
        if not isinstance(providers, dict):
            return None
        nous_provider = providers.get("nous", {})
        if isinstance(nous_provider, dict):
            return nous_provider
    except Exception:
        pass
    return None


def _parse_timestamp(value: object) -> Optional[datetime]:
    """将时间戳字符串解析为 UTC datetime 对象。

    支持 ISO 8601 格式，自动处理 'Z' 后缀和缺少时区信息的情况。
    """
    if not isinstance(value, str) or not value.strip():
        return None
    # 标准化处理：去除首尾空白
    normalized = value.strip()
    # 将 'Z' 后缀转换为 '+00:00' 以兼容 fromisoformat
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    # 若缺少时区信息，默认设为 UTC
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _access_token_is_expiring(expires_at: object, skew_seconds: int) -> bool:
    """判断访问令牌是否即将过期（在 skew_seconds 秒内过期则视为即将过期）。"""
    expires = _parse_timestamp(expires_at)
    if expires is None:
        return True
    # 计算剩余有效时间
    remaining = (expires - datetime.now(timezone.utc)).total_seconds()
    # 若剩余时间小于等于提前量，则认为即将过期
    return remaining <= max(0, int(skew_seconds))


def read_nous_access_token() -> Optional[str]:
    """从认证存储或环境变量覆盖中读取 Nous 订阅用户的 OAuth 访问令牌。

    优先级：
    1. 环境变量 TOOL_GATEWAY_USER_TOKEN（显式覆盖）
    2. auth.json 中缓存的未过期令牌
    3. 通过 hermes_cli.auth 模块刷新令牌
    4. 回退到缓存的令牌（即使可能已过期）
    """
    # 优先使用环境变量中的显式令牌
    explicit = os.getenv("TOOL_GATEWAY_USER_TOKEN")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()

    # 从 auth.json 中读取缓存的令牌
    nous_provider = _read_nous_provider_state() or {}
    access_token = nous_provider.get("access_token")
    cached_token = access_token.strip() if isinstance(access_token, str) and access_token.strip() else None

    # 若缓存令牌存在且未过期，直接返回
    if cached_token and not _access_token_is_expiring(
        nous_provider.get("expires_at"),
        _NOUS_ACCESS_TOKEN_REFRESH_SKEW_SECONDS,
    ):
        return cached_token

    # 尝试通过 hermes_cli 刷新令牌
    try:

        refreshed_token = resolve_nous_access_token(
            refresh_skew_seconds=_NOUS_ACCESS_TOKEN_REFRESH_SKEW_SECONDS,
        )
        if isinstance(refreshed_token, str) and refreshed_token.strip():
            return refreshed_token.strip()
    except Exception as exc:
        logger.debug("Nous 访问令牌刷新失败: %s", exc)

    # 回退：返回缓存令牌（可能已过期，但总比没有好）
    return cached_token


def get_tool_gateway_scheme() -> str:
    """返回共享网关的 URL 协议方案（http 或 https）。"""
    scheme = os.getenv("TOOL_GATEWAY_SCHEME", "").strip().lower()
    if not scheme:
        return _DEFAULT_TOOL_GATEWAY_SCHEME

    if scheme in {"http", "https"}:
        return scheme

    raise ValueError("TOOL_GATEWAY_SCHEME must be 'http' or 'https'")


def build_vendor_gateway_url(vendor: str) -> str:
    """返回特定供应商的网关来源 URL。

    URL 解析优先级：
    1. 环境变量 {VENDOR}_GATEWAY_URL（供应商专属覆盖）
    2. TOOL_GATEWAY_DOMAIN + 共享协议方案（自定义域名）
    3. 默认域名 nousresearch.com
    """
    # 检查供应商专属的环境变量覆盖（如 FAL_GATEWAY_URL）
    vendor_key = f"{vendor.upper().replace('-', '_')}_GATEWAY_URL"
    explicit_vendor_url = os.getenv(vendor_key, "").strip().rstrip("/")
    if explicit_vendor_url:
        return explicit_vendor_url

    # 检查共享域名环境变量
    shared_scheme = get_tool_gateway_scheme()
    shared_domain = os.getenv("TOOL_GATEWAY_DOMAIN", "").strip().strip("/")
    if shared_domain:
        return f"{shared_scheme}://{vendor}-gateway.{shared_domain}"

    # 回退到默认域名
    return f"{shared_scheme}://{vendor}-gateway.{_DEFAULT_TOOL_GATEWAY_DOMAIN}"


def resolve_managed_tool_gateway(
    vendor: str,
    gateway_builder: Optional[Callable[[str], str]] = None,
    token_reader: Optional[Callable[[], Optional[str]]] = None,
) -> Optional[ManagedToolGatewayConfig]:
    """解析指定供应商的共享托管工具网关配置。

    仅当托管模式启用且网关 URL 和令牌均可用时返回配置对象，否则返回 None。
    """
    # 检查是否启用了 Nous 托管工具
    if not managed_nous_tools_enabled():
        return None

    # 使用提供的构建器或默认构建器解析网关 URL 和令牌
    resolved_gateway_builder = gateway_builder or build_vendor_gateway_url
    resolved_token_reader = token_reader or read_nous_access_token

    # 网关 URL 和令牌都必须可用
    gateway_origin = resolved_gateway_builder(vendor)
    nous_user_token = resolved_token_reader()
    if not gateway_origin or not nous_user_token:
        return None

    return ManagedToolGatewayConfig(
        vendor=vendor,
        gateway_origin=gateway_origin,
        nous_user_token=nous_user_token,
        managed_mode=True,
    )


def is_managed_tool_gateway_ready(
    vendor: str,
    gateway_builder: Optional[Callable[[str], str]] = None,
    token_reader: Optional[Callable[[], Optional[str]]] = None,
) -> bool:
    """当网关 URL 和 Nous 访问令牌均可用时返回 True。"""
    return resolve_managed_tool_gateway(
        vendor,
        gateway_builder=gateway_builder,
        token_reader=token_reader,
    ) is not None
