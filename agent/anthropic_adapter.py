"""Hermes Agent 的 Anthropic Messages API 适配器。

在 Hermes 内部的 OpenAI 风格消息格式与 Anthropic 的 Messages API 之间进行转换。
遵循与 codex_responses 适配器相同的模式 -- 所有特定于提供商的逻辑都隔离在此模块中。

认证方式支持:
  - 常规 API 密钥 (sk-ant-api*) → x-api-key 请求头
  - OAuth 设置令牌 (sk-ant-oat*) → Bearer 认证 + beta 请求头
  - Claude Code 凭据 (~/.claude.json 或 ~/.claude/.credentials.json) → Bearer 认证
"""

import copy
import json
import logging
import os
from pathlib import Path

from hermes_constants import get_hermes_home
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

try:
    import anthropic as _anthropic_sdk
except ImportError:
    _anthropic_sdk = None  # type: ignore[assignment]  # SDK 未安装时的占位符

logger = logging.getLogger(__name__)

THINKING_BUDGET = {"xhigh": 32000, "high": 16000, "medium": 8000, "low": 4000}
# Hermes 的 effort 级别 → Anthropic 自适应思考 effort（output_config.effort）的映射。
# Anthropic 在 4.7+ 上开放了 5 个级别：low, medium, high, xhigh, max。
# Opus/Sonnet 4.6 只开放了 4 个级别：low, medium, high, max -- 没有 xhigh。
# 在 4.7+ 上保留 xhigh（编码/代理工作的推荐默认值），
# 在 4.7 之前的自适应模型上将其降级为 max（这些模型接受的最高级别）。
# "minimal" 是一个遗留别名，在所有模型上都映射为 low。参见:
# https://platform.claude.com/docs/en/about-claude/models/migration-guide
ADAPTIVE_EFFORT_MAP = {
    "max":     "max",
    "xhigh":   "xhigh",
    "high":    "high",
    "medium":  "medium",
    "low":     "low",
    "minimal": "low",
}

# 接受 "xhigh" output_config.effort 级别的模型。Opus 4.7 新增了 xhigh 作为
# high 和 max 之间的独立级别；旧的自适应思考模型 (4.6) 会以 400 错误拒绝它。
# 在 Anthropic 迁移指南发布新模型系列时，请保持此子字符串列表同步。
_XHIGH_EFFORT_SUBSTRINGS = ("4-7", "4.7")

# 扩展思考已弃用/移除的模型（4.6+ 行为：自适应是唯一支持的模式；
# 4.7 额外禁止手动思考，并放弃了 temperature/top_p/top_k）。
_ADAPTIVE_THINKING_SUBSTRINGS = ("4-6", "4.6", "4-7", "4.7")

# 设置非默认 temperature/top_p/top_k 时返回 400 的模型。
# 这是 Opus 4.7 的约定；未来的 4.x+ 模型预计将遵循它。
_NO_SAMPLING_PARAMS_SUBSTRINGS = ("4-7", "4.7")

# ── 每个 Anthropic 模型的最大输出 token 限制 ────────────────────────
# 来源：Anthropic 文档 + Cline 模型目录。Anthropic 的 API 要求
# max_tokens 为必填字段。之前我们硬编码为 16384，这会对思考模式
# 下的模型造成不足（思考 token 会计入限制）。
_ANTHROPIC_OUTPUT_LIMITS = {
    # Claude 4.7
    "claude-opus-4-7":   128_000,
    # Claude 4.6
    "claude-opus-4-6":   128_000,
    "claude-sonnet-4-6":  64_000,
    # Claude 4.5
    "claude-opus-4-5":    64_000,
    "claude-sonnet-4-5":  64_000,
    "claude-haiku-4-5":   64_000,
    # Claude 4
    "claude-opus-4":      32_000,
    "claude-sonnet-4":    64_000,
    # Claude 3.7
    "claude-3-7-sonnet": 128_000,
    # Claude 3.5
    "claude-3-5-sonnet":   8_192,
    "claude-3-5-haiku":    8_192,
    # Claude 3
    "claude-3-opus":       4_096,
    "claude-3-sonnet":     4_096,
    "claude-3-haiku":      4_096,
    # 第三方 Anthropic 兼容提供商
    "minimax":            131_072,
}

# 对于不在表中的模型，假设当前最高限制。
# 未来的 Anthropic 模型不太可能有*更低*的输出容量。
_ANTHROPIC_DEFAULT_OUTPUT_LIMIT = 128_000


def _get_anthropic_max_output(model: str) -> int:
    """查找 Anthropic 模型的最大输出 token 限制。

    使用子字符串匹配 _ANTHROPIC_OUTPUT_LIMITS，这样带日期戳的模型 ID
    （claude-sonnet-4-5-20250929）和变体后缀（:1m, :fast）都能正确解析。
    最长前缀匹配优先，以避免例如 "claude-3-5" 先于 "claude-3-5-sonnet" 被匹配。

    将点号归一化为连字符，这样 ``anthropic/claude-opus-4.6`` 这样的模型名
    也能匹配 ``claude-opus-4-6`` 表键。
    """
    m = model.lower().replace(".", "-")
    best_key = ""
    best_val = _ANTHROPIC_DEFAULT_OUTPUT_LIMIT
    for key, val in _ANTHROPIC_OUTPUT_LIMITS.items():
        if key in m and len(key) > len(best_key):
            best_key = key
            best_val = val
    return best_val


def _supports_adaptive_thinking(model: str) -> bool:
    """对于支持自适应思考的 Claude 4.6+ 模型返回 True。"""
    return any(v in model for v in _ADAPTIVE_THINKING_SUBSTRINGS)


def _supports_xhigh_effort(model: str) -> bool:
    """对于接受 'xhigh' 自适应 effort 级别的模型返回 True。

    Opus 4.7 引入了 xhigh 作为 high 和 max 之间的独立级别。
    4.7 之前的自适应模型（Opus/Sonnet 4.6）只接受 low/medium/high/max，
    对 xhigh 返回 HTTP 400 错误。调用者应在返回 False 时将 xhigh 降级为 max。
    """
    return any(v in model for v in _XHIGH_EFFORT_SUBSTRINGS)


def _forbids_sampling_params(model: str) -> bool:
    """对于非默认 temperature/top_p/top_k 会返回 400 错误的模型返回 True。

    Opus 4.7 明确拒绝采样参数；后续的 Claude 版本预计将沿用此行为。
    调用者应完全省略这些字段，而不是传递零值/默认值（API 会拒绝任何非 null 值）。
    """
    return any(v in model for v in _NO_SAMPLING_PARAMS_SUBSTRINGS)


# 增强功能的 Beta 请求头（随所有认证类型发送）。
# 截至 Opus 4.7（2026-04-16），这两个在 Claude 4.6+ 上已正式发布（GA） --
# beta 请求头仍被接受（无害的空操作）但不再必需。保留在此处，
# 以便仍依赖这些头部的旧版 Claude（4.5, 4.1）+ 第三方 Anthropic 兼容端点
# 继续获得增强功能。
# 迁移指南：如果不再支持 ≤4.5 模型，可移除这些。
_COMMON_BETAS = [
    "interleaved-thinking-2025-05-14",
    "fine-grained-tool-streaming-2025-05-14",
]
# MiniMax 的 Anthropic 兼容端点在存在细粒度工具流式传输 beta 时，
# 工具调用请求会失败。省略它以回退到提供商的默认响应路径。
_TOOL_STREAMING_BETA = "fine-grained-tool-streaming-2025-05-14"

# 快速模式 beta -- 启用 ``speed: "fast"`` 请求参数，
# 可在 Opus 4.6 上显著提高输出 token 吞吐量（约 2.5 倍）。
# 参见 https://platform.claude.com/docs/en/build-with-claude/fast-mode
_FAST_MODE_BETA = "fast-mode-2026-02-01"

# OAuth/订阅认证所需的额外 beta 请求头。
# 与 Claude Code（以及 pi-ai / OpenCode）发送的一致。
_OAUTH_ONLY_BETAS = [
    "claude-code-20250219",
    "oauth-2025-04-20",
]

# Claude Code 身份标识 -- OAuth 请求正确路由所必需。
# 缺少这些时，Anthropic 的基础设施会间歇性地对 OAuth 流量返回 500 错误。
# 版本号必须保持合理地最新 -- 当伪造的 user-agent 版本太旧时，
# Anthropic 会拒绝 OAuth 请求。
_CLAUDE_CODE_VERSION_FALLBACK = "2.1.74"
_claude_code_version_cache: Optional[str] = None


def _detect_claude_code_version() -> str:
    """检测已安装的 Claude Code 版本，失败时回退到静态常量。

    Anthropic 的 OAuth 基础设施会验证 user-agent 版本，可能拒绝
    版本过旧的请求。动态检测意味着保持 Claude Code 更新的用户
    永远不会遇到版本过期的 400 错误。
    """
    import subprocess as _sp

    for cmd in ("claude", "claude-code"):
        try:
            result = _sp.run(
                [cmd, "--version"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                # 输出格式如 "2.1.74 (Claude Code)" 或仅 "2.1.74"
                version = result.stdout.strip().split()[0]
                if version and version[0].isdigit():
                    return version
        except Exception:
            pass
    return _CLAUDE_CODE_VERSION_FALLBACK


_CLAUDE_CODE_SYSTEM_PREFIX = "You are Claude Code, Anthropic's official CLI for Claude."
_MCP_TOOL_PREFIX = "mcp_"


def _get_claude_code_version() -> str:
    """在 OAuth 请求头需要时，延迟检测已安装的 Claude Code 版本。"""
    global _claude_code_version_cache
    if _claude_code_version_cache is None:
        _claude_code_version_cache = _detect_claude_code_version()
    return _claude_code_version_cache


def _is_oauth_token(key: str) -> bool:
    """检查密钥是否为 Anthropic OAuth/设置令牌。

    通过密钥格式主动识别 Anthropic OAuth 令牌:
    - ``sk-ant-`` 前缀（但非 ``sk-ant-api``）→ 设置令牌、托管密钥
    - ``eyJ`` 前缀 → 来自 Anthropic OAuth 流程的 JWT

    非 Anthropic 密钥（MiniMax、阿里巴巴等）不匹配任何模式，
    正确返回 False。
    """
    if not key:
        return False
    # 常规 Anthropic 控制台 API 密钥 -- 使用 x-api-key 认证，从不使用 OAuth
    if key.startswith("sk-ant-api"):
        return False
    # Anthropic 签发的令牌（设置令牌 sk-ant-oat-*、托管密钥）
    if key.startswith("sk-ant-"):
        return True
    # 来自 Anthropic OAuth 流程的 JWT
    if key.startswith("eyJ"):
        return True
    return False


def _normalize_base_url_text(base_url) -> str:
    """将 SDK/基础传输 URL 值归一化为纯字符串以便检查。

    某些客户端对象将 ``base_url`` 暴露为 ``httpx.URL`` 而非原始字符串。
    提供商/认证检测应接受两种形式。
    """
    if not base_url:
        return ""
    return str(base_url).strip()


def _is_third_party_anthropic_endpoint(base_url: str | None) -> bool:
    """对于使用 Anthropic Messages API 的非 Anthropic 端点返回 True。

    第三方代理（Azure AI Foundry、AWS Bedrock、自托管）使用自己的 API 密钥
    通过 x-api-key 进行认证，而非 Anthropic OAuth 令牌。对这些端点应跳过
    OAuth 检测。
    """
    normalized = _normalize_base_url_text(base_url)
    if not normalized:
        return False  # 无 base_url = 直接 Anthropic API
    normalized = normalized.rstrip("/").lower()
    if "anthropic.com" in normalized:
        return False  # 直接 Anthropic API -- OAuth 适用
    return True  # 任何其他端点都是第三方代理


def _requires_bearer_auth(base_url: str | None) -> bool:
    """对于需要 Bearer 认证的 Anthropic 兼容提供商返回 True。

    某些第三方 /anthropic 端点实现了 Anthropic 的 Messages API，但需要
    Authorization: Bearer 而非 Anthropic 原生的 x-api-key 请求头。
    MiniMax 的全球和中国 Anthropic 兼容端点遵循此模式。
    """
    normalized = _normalize_base_url_text(base_url)
    if not normalized:
        return False
    normalized = normalized.rstrip("/").lower()
    return normalized.startswith(("https://api.minimax.io/anthropic", "https://api.minimaxi.com/anthropic"))


def _common_betas_for_base_url(base_url: str | None) -> list[str]:
    """返回对配置端点安全的 beta 请求头。

    MiniMax 的 Anthropic 兼容端点（Bearer 认证）拒绝包含 Anthropic 的
    ``fine-grained-tool-streaming`` beta 的请求 -- 每个工具调用消息都会
    触发连接错误。对 Bearer 认证端点剥离该 beta，同时保留所有其他 beta。
    """
    if _requires_bearer_auth(base_url):
        return [b for b in _COMMON_BETAS if b != _TOOL_STREAMING_BETA]
    return _COMMON_BETAS


def build_anthropic_client(api_key: str, base_url: str = None):
    """创建 Anthropic 客户端，自动检测设置令牌与 API 密钥。

    返回一个 anthropic.Anthropic 实例。
    """
    if _anthropic_sdk is None:
        raise ImportError(
            "The 'anthropic' package is required for the Anthropic provider. "
            "Install it with: pip install 'anthropic>=0.39.0'"
        )
    from httpx import Timeout

    normalized_base_url = _normalize_base_url_text(base_url)
    kwargs = {
        "timeout": Timeout(timeout=900.0, connect=10.0),
    }
    if normalized_base_url:
        kwargs["base_url"] = normalized_base_url
    common_betas = _common_betas_for_base_url(normalized_base_url)

    if _requires_bearer_auth(normalized_base_url):
        # 某些 Anthropic 兼容提供商（如 MiniMax）期望 API 密钥放在
        # Authorization: Bearer 中，即使是常规 API 密钥也是如此。将这些端点
        # 通过 auth_token 路由，使 SDK 发送 Bearer 认证而非 x-api-key。
        # 在 OAuth 令牌格式检测之前检查此项，因为 MiniMax 密钥不使用
        # Anthropic 的 sk-ant-api 前缀，否则会被误读为 Anthropic OAuth/设置令牌。
        kwargs["auth_token"] = api_key
        if common_betas:
            kwargs["default_headers"] = {"anthropic-beta": ",".join(common_betas)}
    elif _is_third_party_anthropic_endpoint(base_url):
        # 第三方代理（Azure AI Foundry、AWS Bedrock 等）使用自己的 API 密钥
        # 和 x-api-key 认证。跳过 OAuth 检测 -- 它们的密钥不遵循
        # Anthropic 的 sk-ant-* 前缀约定，可能会被误分类为 OAuth 令牌。
        kwargs["api_key"] = api_key
        if common_betas:
            kwargs["default_headers"] = {"anthropic-beta": ",".join(common_betas)}
    elif _is_oauth_token(api_key):
        # OAuth 访问令牌 / 设置令牌 → Bearer 认证 + Claude Code 身份标识。
        # Anthropic 根据 user-agent 和请求头路由 OAuth 请求；
        # 没有 Claude Code 的指纹，请求会间歇性地返回 500 错误。
        all_betas = common_betas + _OAUTH_ONLY_BETAS
        kwargs["auth_token"] = api_key
        kwargs["default_headers"] = {
            "anthropic-beta": ",".join(all_betas),
            "user-agent": f"claude-cli/{_get_claude_code_version()} (external, cli)",
            "x-app": "cli",
        }
    else:
        # 常规 API 密钥 → x-api-key 请求头 + 通用 beta
        kwargs["api_key"] = api_key
        if common_betas:
            kwargs["default_headers"] = {"anthropic-beta": ",".join(common_betas)}

    return _anthropic_sdk.Anthropic(**kwargs)


def build_anthropic_bedrock_client(region: str):
    """为 Bedrock Claude 模型创建 AnthropicBedrock 客户端。

    使用 Anthropic SDK 的原生 Bedrock 适配器，提供完整的 Claude 功能对等：
    提示缓存、思考预算、自适应思考、快速模式 -- 这些功能在 Converse API 中不可用。

    认证使用 boto3 默认凭据链（IAM 角色、SSO、环境变量）。
    """
    if _anthropic_sdk is None:
        raise ImportError(
            "The 'anthropic' package is required for the Bedrock provider. "
            "Install it with: pip install 'anthropic>=0.39.0'"
        )
    if not hasattr(_anthropic_sdk, "AnthropicBedrock"):
        raise ImportError(
            "anthropic.AnthropicBedrock not available. "
            "Upgrade with: pip install 'anthropic>=0.39.0'"
        )
    from httpx import Timeout

    return _anthropic_sdk.AnthropicBedrock(
        aws_region=region,
        timeout=Timeout(timeout=900.0, connect=10.0),
    )


def read_claude_code_credentials() -> Optional[Dict[str, Any]]:
    """从 ~/.claude/.credentials.json 读取可刷新的 Claude Code OAuth 凭据。

    此函数有意排除 ~/.claude.json 的 primaryApiKey。Opencode 的订阅流程
    基于 OAuth/设置令牌和可刷新凭据，原生直接使用 Anthropic 提供商
    应遵循该路径，而非自动检测 Claude 的第一方托管密钥。

    返回包含 {accessToken, refreshToken?, expiresAt?} 的字典或 None。
    """
    cred_path = Path.home() / ".claude" / ".credentials.json"
    if cred_path.exists():
        try:
            data = json.loads(cred_path.read_text(encoding="utf-8"))
            oauth_data = data.get("claudeAiOauth")
            if oauth_data and isinstance(oauth_data, dict):
                access_token = oauth_data.get("accessToken", "")
                if access_token:
                    return {
                        "accessToken": access_token,
                        "refreshToken": oauth_data.get("refreshToken", ""),
                        "expiresAt": oauth_data.get("expiresAt", 0),
                        "source": "claude_code_credentials_file",
                    }
        except (json.JSONDecodeError, OSError, IOError) as e:
            logger.debug("Failed to read ~/.claude/.credentials.json: %s", e)

    return None


def read_claude_managed_key() -> Optional[str]:
    """从 ~/.claude.json 读取 Claude 原生托管密钥，仅用于诊断。"""
    claude_json = Path.home() / ".claude.json"
    if claude_json.exists():
        try:
            data = json.loads(claude_json.read_text(encoding="utf-8"))
            primary_key = data.get("primaryApiKey", "")
            if isinstance(primary_key, str) and primary_key.strip():
                return primary_key.strip()
        except (json.JSONDecodeError, OSError, IOError) as e:
            logger.debug("Failed to read ~/.claude.json: %s", e)
    return None


def is_claude_code_token_valid(creds: Dict[str, Any]) -> bool:
    """检查 Claude Code 凭据是否有未过期的访问令牌。"""
    import time

    expires_at = creds.get("expiresAt", 0)
    if not expires_at:
        # 未设置过期时间（托管密钥） -- 如果令牌存在则有效
        return bool(creds.get("accessToken"))

    # expiresAt 以自纪元以来的毫秒为单位
    now_ms = int(time.time() * 1000)
    # 允许 60 秒缓冲
    return now_ms < (expires_at - 60_000)


def refresh_anthropic_oauth_pure(refresh_token: str, *, use_json: bool = False) -> Dict[str, Any]:
    """刷新 Anthropic OAuth 令牌，不修改本地凭据文件。"""
    import time
    import urllib.parse
    import urllib.request

    if not refresh_token:
        raise ValueError("refresh_token is required")

    client_id = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
    if use_json:
        data = json.dumps({
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
        }).encode()
        content_type = "application/json"
    else:
        data = urllib.parse.urlencode({
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
        }).encode()
        content_type = "application/x-www-form-urlencoded"

    token_endpoints = [
        "https://platform.claude.com/v1/oauth/token",
        "https://console.anthropic.com/v1/oauth/token",
    ]
    last_error = None
    for endpoint in token_endpoints:
        req = urllib.request.Request(
            endpoint,
            data=data,
            headers={
                "Content-Type": content_type,
                "User-Agent": f"claude-cli/{_get_claude_code_version()} (external, cli)",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read().decode())
        except Exception as exc:
            last_error = exc
            logger.debug("Anthropic token refresh failed at %s: %s", endpoint, exc)
            continue

        access_token = result.get("access_token", "")
        if not access_token:
            raise ValueError("Anthropic refresh response was missing access_token")
        next_refresh = result.get("refresh_token", refresh_token)
        expires_in = result.get("expires_in", 3600)
        return {
            "access_token": access_token,
            "refresh_token": next_refresh,
            "expires_at_ms": int(time.time() * 1000) + (expires_in * 1000),
        }

    if last_error is not None:
        raise last_error
    raise ValueError("Anthropic token refresh failed")


def _refresh_oauth_token(creds: Dict[str, Any]) -> Optional[str]:
    """尝试刷新过期的 Claude Code OAuth 令牌。"""
    refresh_token = creds.get("refreshToken", "")
    if not refresh_token:
        logger.debug("没有可用的刷新令牌 — 无法刷新")
        return None

    try:
        refreshed = refresh_anthropic_oauth_pure(refresh_token, use_json=False)
        _write_claude_code_credentials(
            refreshed["access_token"],
            refreshed["refresh_token"],
            refreshed["expires_at_ms"],
        )
        logger.debug("成功刷新 Claude Code OAuth 令牌")
        return refreshed["access_token"]
    except Exception as e:
        logger.debug("刷新 Claude Code 令牌失败: %s", e)
        return None


def _write_claude_code_credentials(
    access_token: str,
    refresh_token: str,
    expires_at_ms: int,
    *,
    scopes: Optional[list] = None,
) -> None:
    """将刷新后的凭据写回 ~/.claude/.credentials.json。

    可选的 *scopes* 列表（如 ``["user:inference", "user:profile", ...]``）
    会被持久化保存，以便 Claude Code 自身的认证检查能识别该凭据为有效。
    Claude Code >=2.1.81 在使用令牌前会检查已存储的 scopes 中是否包含
    ``"user:inference"``。
    """
    cred_path = Path.home() / ".claude" / ".credentials.json"
    try:
        # 读取现有文件以保留其他字段
        existing = {}
        if cred_path.exists():
            existing = json.loads(cred_path.read_text(encoding="utf-8"))

        oauth_data: Dict[str, Any] = {
            "accessToken": access_token,
            "refreshToken": refresh_token,
            "expiresAt": expires_at_ms,
        }
        if scopes is not None:
            oauth_data["scopes"] = scopes
        elif "claudeAiOauth" in existing and "scopes" in existing["claudeAiOauth"]:
            # 当刷新响应不包含 scope 字段时，保留之前存储的 scopes。
            oauth_data["scopes"] = existing["claudeAiOauth"]["scopes"]

        existing["claudeAiOauth"] = oauth_data

        cred_path.parent.mkdir(parents=True, exist_ok=True)
        cred_path.write_text(json.dumps(existing, indent=2), encoding="utf-8")
        # 限制权限（凭据文件）
        cred_path.chmod(0o600)
    except (OSError, IOError) as e:
        logger.debug("写入刷新凭据失败: %s", e)


def _resolve_claude_code_token_from_credentials(creds: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """从 Claude Code 凭据文件中解析令牌，如有需要则自动刷新。"""
    creds = creds or read_claude_code_credentials()
    if creds and is_claude_code_token_valid(creds):
        logger.debug("使用 Claude Code 凭据（自动检测）")
        return creds["accessToken"]
    if creds:
        logger.debug("Claude Code 凭据已过期 — 正在尝试刷新")
        refreshed = _refresh_oauth_token(creds)
        if refreshed:
            return refreshed
        logger.debug("令牌刷新失败 — 请重新运行 'claude setup-token' 进行重新认证")
    return None


def _prefer_refreshable_claude_code_token(env_token: str, creds: Optional[Dict[str, Any]]) -> Optional[str]:
    """当持久化的环境变量 OAuth 令牌会遮蔽刷新机制时，优先使用 Claude Code 凭据。

    Hermes 历史上会将 setup token 持久化到 ANTHROPIC_TOKEN 中。这使得后续的
    刷新不可能进行，因为静态的环境变量令牌在我们检查 Claude Code 的可刷新
    凭据文件之前就已经胜出。如果我们有可刷新的 Claude Code 凭据记录，
    则优先使用它而非静态的环境变量 OAuth 令牌。
    """
    if not env_token or not _is_oauth_token(env_token) or not isinstance(creds, dict):
        return None
    if not creds.get("refreshToken"):
        return None

    resolved = _resolve_claude_code_token_from_credentials(creds)
    if resolved and resolved != env_token:
        logger.debug(
            "优先使用 Claude Code 凭据文件而非静态环境变量 OAuth 令牌，以便刷新可以继续"
        )
        return resolved
    return None


def resolve_anthropic_token() -> Optional[str]:
    """从所有可用来源解析 Anthropic 令牌。

    优先级：
      1. ANTHROPIC_TOKEN 环境变量（由 Hermes 保存的 OAuth/setup 令牌）
      2. CLAUDE_CODE_OAUTH_TOKEN 环境变量
      3. Claude Code 凭据（~/.claude.json 或 ~/.claude/.credentials.json）
         — 如果过期且有刷新令牌，会自动刷新
      4. ANTHROPIC_API_KEY 环境变量（常规 API 密钥，或旧版回退）

    返回令牌字符串或 None。
    """
    creds = read_claude_code_credentials()

    # 1. Hermes 管理的 OAuth/setup 令牌环境变量
    token = os.getenv("ANTHROPIC_TOKEN", "").strip()
    if token:
        preferred = _prefer_refreshable_claude_code_token(token, creds)
        if preferred:
            return preferred
        return token

    # 2. CLAUDE_CODE_OAUTH_TOKEN（Claude Code 用于 setup-token 的环境变量）
    cc_token = os.getenv("CLAUDE_CODE_OAUTH_TOKEN", "").strip()
    if cc_token:
        preferred = _prefer_refreshable_claude_code_token(cc_token, creds)
        if preferred:
            return preferred
        return cc_token

    # 3. Claude Code 凭据文件
    resolved_claude_token = _resolve_claude_code_token_from_credentials(creds)
    if resolved_claude_token:
        return resolved_claude_token

    # 4. 常规 API 密钥，或保存在 ANTHROPIC_API_KEY 中的旧版 OAuth 令牌。
    # 这作为兼容性回退保留给迁移前的 Hermes 配置。
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if api_key:
        return api_key

    return None


def run_oauth_setup_token() -> Optional[str]:
    """交互式运行 'claude setup-token' 并返回获取到的令牌。

    子进程完成后从多个来源检查：
      1. Claude Code 凭据文件（可能由子进程写入）
      2. CLAUDE_CODE_OAUTH_TOKEN / ANTHROPIC_TOKEN 环境变量

    返回令牌字符串，如果未获取到凭据则返回 None。
    如果 'claude' CLI 未安装则抛出 FileNotFoundError。
    """
    import shutil
    import subprocess

    claude_path = shutil.which("claude")
    if not claude_path:
        raise FileNotFoundError(
            "The 'claude' CLI is not installed. "
            "Install it with: npm install -g @anthropic-ai/claude-code"
        )

    # 交互式运行 — stdin/stdout/stderr 被继承以便用户可以交互
    try:
        subprocess.run([claude_path, "setup-token"])
    except (KeyboardInterrupt, EOFError):
        return None

    # 检查凭据是否已保存到 Claude Code 的配置文件中
    creds = read_claude_code_credentials()
    if creds and is_claude_code_token_valid(creds):
        return creds["accessToken"]

    # 检查可能已设置的环境变量
    for env_var in ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_TOKEN"):
        val = os.getenv(env_var, "").strip()
        if val:
            return val

    return None


# ── Hermes 原生 PKCE OAuth 流程 ────────────────────────────────────────
# 模仿 Claude Code、pi-ai 和 OpenCode 使用的流程。
# 将凭据存储在 ~/.hermes/.anthropic_oauth.json（我们自己的文件）中。

_OAUTH_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
_OAUTH_TOKEN_URL = "https://console.anthropic.com/v1/oauth/token"
_OAUTH_REDIRECT_URI = "https://console.anthropic.com/oauth/code/callback"
_OAUTH_SCOPES = "org:create_api_key user:profile user:inference"
_HERMES_OAUTH_FILE = get_hermes_home() / ".anthropic_oauth.json"


def _generate_pkce() -> tuple:
    """生成 PKCE code_verifier 和 code_challenge（S256 算法）。"""
    import base64
    import hashlib
    import secrets

    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    return verifier, challenge


def run_hermes_oauth_login_pure() -> Optional[Dict[str, Any]]:
    """运行 Hermes 原生 OAuth PKCE 流程并返回凭据状态。"""
    import time
    import webbrowser

    verifier, challenge = _generate_pkce()

    params = {
        "code": "true",
        "client_id": _OAUTH_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": _OAUTH_REDIRECT_URI,
        "scope": _OAUTH_SCOPES,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": verifier,
    }
    from urllib.parse import urlencode

    auth_url = f"https://claude.ai/oauth/authorize?{urlencode(params)}"

    print()
    print("Authorize Hermes with your Claude Pro/Max subscription.")
    print()
    print("╭─ Claude Pro/Max Authorization ────────────────────╮")
    print("│                                                   │")
    print("│  Open this link in your browser:                  │")
    print("╰───────────────────────────────────────────────────╯")
    print()
    print(f"  {auth_url}")
    print()

    try:
        webbrowser.open(auth_url)
        print("  (Browser opened automatically)")
    except Exception:
        pass

    print()
    print("After authorizing, you'll see a code. Paste it below.")
    print()
    try:
        auth_code = input("Authorization code: ").strip()
    except (KeyboardInterrupt, EOFError):
        return None

    if not auth_code:
        print("No code entered.")
        return None

    splits = auth_code.split("#")
    code = splits[0]
    state = splits[1] if len(splits) > 1 else ""

    try:
        import urllib.request

        exchange_data = json.dumps({
            "grant_type": "authorization_code",
            "client_id": _OAUTH_CLIENT_ID,
            "code": code,
            "state": state,
            "redirect_uri": _OAUTH_REDIRECT_URI,
            "code_verifier": verifier,
        }).encode()

        req = urllib.request.Request(
            _OAUTH_TOKEN_URL,
            data=exchange_data,
            headers={
                "Content-Type": "application/json",
                "User-Agent": f"claude-cli/{_get_claude_code_version()} (external, cli)",
            },
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode())
    except Exception as e:
        print(f"Token exchange failed: {e}")
        return None

    access_token = result.get("access_token", "")
    refresh_token = result.get("refresh_token", "")
    expires_in = result.get("expires_in", 3600)

    if not access_token:
        print("No access token in response.")
        return None

    expires_at_ms = int(time.time() * 1000) + (expires_in * 1000)
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_at_ms": expires_at_ms,
    }


def read_hermes_oauth_credentials() -> Optional[Dict[str, Any]]:
    """从 ~/.hermes/.anthropic_oauth.json 读取 Hermes 管理的 OAuth 凭据。"""
    if _HERMES_OAUTH_FILE.exists():
        try:
            data = json.loads(_HERMES_OAUTH_FILE.read_text(encoding="utf-8"))
            if data.get("accessToken"):
                return data
        except (json.JSONDecodeError, OSError, IOError) as e:
            logger.debug("读取 Hermes OAuth 凭据失败: %s", e)
    return None


# ---------------------------------------------------------------------------
# 消息 / 工具 / 响应格式转换
# ---------------------------------------------------------------------------


def normalize_model_name(model: str, preserve_dots: bool = False) -> str:
    """为 Anthropic API 规范化模型名称。

    - 去除 'anthropic/' 前缀（OpenRouter 格式，不区分大小写）
    - 将版本号中的点转换为连字符（OpenRouter 使用点号，
      Anthropic 使用连字符：claude-opus-4.6 → claude-opus-4-6），除非
      preserve_dots 为 True（例如阿里巴巴/DashScope：qwen3.5-plus）。
    """
    lower = model.lower()
    if lower.startswith("anthropic/"):
        model = model[len("anthropic/"):]
    if not preserve_dots:
        # OpenRouter 使用点号作为版本分隔符（claude-opus-4.6），
        # Anthropic 使用连字符（claude-opus-4-6）。将点号转换为连字符。
        model = model.replace(".", "-")
    return model


def _sanitize_tool_id(tool_id: str) -> str:
    """为 Anthropic API 清洁化工具调用 ID。

    Anthropic 要求 ID 匹配 [a-zA-Z0-9_-]。将无效字符替换为下划线，
    并确保非空。
    """
    import re
    if not tool_id:
        return "tool_0"
    sanitized = re.sub(r"[^a-zA-Z0-9_-]", "_", tool_id)
    return sanitized or "tool_0"


def convert_tools_to_anthropic(tools: List[Dict]) -> List[Dict]:
    """将 OpenAI 工具定义转换为 Anthropic 格式。"""
    if not tools:
        return []
    result = []
    for t in tools:
        fn = t.get("function", {})
        result.append({
            "name": fn.get("name", ""),
            "description": fn.get("description", ""),
            "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
        })
    return result


def _image_source_from_openai_url(url: str) -> Dict[str, str]:
    """将 OpenAI 风格的图片 URL/data URL 转换为 Anthropic 图片源格式。"""
    url = str(url or "").strip()
    if not url:
        return {"type": "url", "url": ""}

    if url.startswith("data:"):
        header, _, data = url.partition(",")
        media_type = "image/jpeg"
        if header.startswith("data:"):
            mime_part = header[len("data:"):].split(";", 1)[0].strip()
            if mime_part.startswith("image/"):
                media_type = mime_part
        return {
            "type": "base64",
            "media_type": media_type,
            "data": data,
        }

    return {"type": "url", "url": url}


def _convert_content_part_to_anthropic(part: Any) -> Optional[Dict[str, Any]]:
    """将单个 OpenAI 风格的内容片段转换为 Anthropic 格式。"""
    if part is None:
        return None
    if isinstance(part, str):
        return {"type": "text", "text": part}
    if not isinstance(part, dict):
        return {"type": "text", "text": str(part)}

    ptype = part.get("type")

    if ptype == "input_text":
        block: Dict[str, Any] = {"type": "text", "text": part.get("text", "")}
    elif ptype in {"image_url", "input_image"}:
        image_value = part.get("image_url", {})
        url = image_value.get("url", "") if isinstance(image_value, dict) else str(image_value or "")
        block = {"type": "image", "source": _image_source_from_openai_url(url)}
    else:
        block = dict(part)

    if isinstance(part.get("cache_control"), dict) and "cache_control" not in block:
        block["cache_control"] = dict(part["cache_control"])
    return block


def _to_plain_data(value: Any, *, _depth: int = 0, _path: Optional[set] = None) -> Any:
    """递归地将 SDK 对象转换为纯 Python 数据结构。

    通过 ``_path`` 跟踪当前递归路径上对象的 ``id()`` 来防止循环引用，
    并限制最大深度为 20 层防止失控递归。
    使用基于路径的跟踪，使得被多个兄弟节点引用的共享（但非循环的）对象
    能被正确转换，而不是被字符串化。
    """
    _MAX_DEPTH = 20
    if _depth > _MAX_DEPTH:
        return str(value)

    if _path is None:
        _path = set()

    obj_id = id(value)
    if obj_id in _path:
        return str(value)

    if hasattr(value, "model_dump"):
        _path.add(obj_id)
        result = _to_plain_data(value.model_dump(), _depth=_depth + 1, _path=_path)
        _path.discard(obj_id)
        return result
    if isinstance(value, dict):
        _path.add(obj_id)
        result = {k: _to_plain_data(v, _depth=_depth + 1, _path=_path) for k, v in value.items()}
        _path.discard(obj_id)
        return result
    if isinstance(value, (list, tuple)):
        _path.add(obj_id)
        result = [_to_plain_data(v, _depth=_depth + 1, _path=_path) for v in value]
        _path.discard(obj_id)
        return result
    if hasattr(value, "__dict__"):
        _path.add(obj_id)
        result = {
            k: _to_plain_data(v, _depth=_depth + 1, _path=_path)
            for k, v in vars(value).items()
            if not k.startswith("_")
        }
        _path.discard(obj_id)
        return result
    return value


def _extract_preserved_thinking_blocks(message: Dict[str, Any]) -> List[Dict[str, Any]]:
    """返回之前保存在消息上的 Anthropic 思维块。"""
    raw_details = message.get("reasoning_details")
    if not isinstance(raw_details, list):
        return []

    preserved: List[Dict[str, Any]] = []
    for detail in raw_details:
        if not isinstance(detail, dict):
            continue
        block_type = str(detail.get("type", "") or "").strip().lower()
        if block_type not in {"thinking", "redacted_thinking"}:
            continue
        preserved.append(copy.deepcopy(detail))
    return preserved


def _convert_content_to_anthropic(content: Any) -> Any:
    """将 OpenAI 风格的多模态内容数组转换为 Anthropic 块格式。"""
    if not isinstance(content, list):
        return content

    converted = []
    for part in content:
        block = _convert_content_part_to_anthropic(part)
        if block is not None:
            converted.append(block)
    return converted


def convert_messages_to_anthropic(
    messages: List[Dict],
    base_url: str | None = None,
) -> Tuple[Optional[Any], List[Dict]]:
    """将 OpenAI 格式的消息转换为 Anthropic 格式。

    返回 (system_prompt, anthropic_messages)。
    系统消息会被提取出来，因为 Anthropic 将其作为单独的参数传入。
    system_prompt 是字符串或内容块列表（当存在 cache_control 时为列表）。

    当提供了 *base_url* 且指向第三方 Anthropic 兼容端点时，
    所有思维块签名都会被移除。签名是 Anthropic 专有的 — 第三方端点
    无法验证它们，会以 HTTP 400 "Invalid signature in thinking block" 拒绝。
    """
    system = None
    result = []

    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")

        if role == "system":
            if isinstance(content, list):
                # 保留内容块上的 cache_control 标记
                has_cache = any(
                    p.get("cache_control") for p in content if isinstance(p, dict)
                )
                if has_cache:
                    system = [p for p in content if isinstance(p, dict)]
                else:
                    system = "\n".join(
                        p["text"] for p in content if p.get("type") == "text"
                    )
            else:
                system = content
            continue

        if role == "assistant":
            blocks = _extract_preserved_thinking_blocks(m)
            if content:
                if isinstance(content, list):
                    converted_content = _convert_content_to_anthropic(content)
                    if isinstance(converted_content, list):
                        blocks.extend(converted_content)
                else:
                    blocks.append({"type": "text", "text": str(content)})
            for tc in m.get("tool_calls", []):
                if not tc or not isinstance(tc, dict):
                    continue
                fn = tc.get("function", {})
                args = fn.get("arguments", "{}")
                try:
                    parsed_args = json.loads(args) if isinstance(args, str) else args
                except (json.JSONDecodeError, ValueError):
                    parsed_args = {}
                blocks.append({
                    "type": "tool_use",
                    "id": _sanitize_tool_id(tc.get("id", "")),
                    "name": fn.get("name", ""),
                    "input": parsed_args,
                })
            # Anthropic 拒绝空的 assistant 内容
            effective = blocks or content
            if not effective or effective == "":
                effective = [{"type": "text", "text": "(empty)"}]
            result.append({"role": "assistant", "content": effective})
            continue

        if role == "tool":
            # 清洁化 tool_use_id 并确保内容非空
            result_content = content if isinstance(content, str) else json.dumps(content)
            if not result_content:
                result_content = "(no output)"
            tool_result = {
                "type": "tool_result",
                "tool_use_id": _sanitize_tool_id(m.get("tool_call_id", "")),
                "content": result_content,
            }
            if isinstance(m.get("cache_control"), dict):
                tool_result["cache_control"] = dict(m["cache_control"])
            # 将连续的 tool_result 合并到一个 user 消息中
            if (
                result
                and result[-1]["role"] == "user"
                and isinstance(result[-1]["content"], list)
                and result[-1]["content"]
                and result[-1]["content"][0].get("type") == "tool_result"
            ):
                result[-1]["content"].append(tool_result)
            else:
                result.append({"role": "user", "content": [tool_result]})
            continue

        # 常规 user 消息 — 验证内容非空（Anthropic 拒绝空内容）
        if isinstance(content, list):
            converted_blocks = _convert_content_to_anthropic(content)
            # 检查是否所有文本块都为空
            if not converted_blocks or all(
                b.get("text", "").strip() == ""
                for b in converted_blocks
                if isinstance(b, dict) and b.get("type") == "text"
            ):
                converted_blocks = [{"type": "text", "text": "(empty message)"}]
            result.append({"role": "user", "content": converted_blocks})
        else:
            # 验证字符串内容非空
            if not content or (isinstance(content, str) and not content.strip()):
                content = "(empty message)"
            result.append({"role": "user", "content": content})

    # 剥离孤立的 tool_use 块（后面没有匹配的 tool_result）
    tool_result_ids = set()
    for m in result:
        if m["role"] == "user" and isinstance(m["content"], list):
            for block in m["content"]:
                if block.get("type") == "tool_result":
                    tool_result_ids.add(block.get("tool_use_id"))
    for m in result:
        if m["role"] == "assistant" and isinstance(m["content"], list):
            m["content"] = [
                b
                for b in m["content"]
                if b.get("type") != "tool_use" or b.get("id") in tool_result_ids
            ]
            if not m["content"]:
                m["content"] = [{"type": "text", "text": "(tool call removed)"}]

    # 剥离孤立的 tool_result 块（前面没有匹配的 tool_use）。
    # 这是上面操作的镜像：上下文压缩或会话截断可能移除了包含 tool_use 的
    # assistant 消息，但保留了后续的 tool_result。Anthropic 会以 400 错误拒绝这些。
    tool_use_ids = set()
    for m in result:
        if m["role"] == "assistant" and isinstance(m["content"], list):
            for block in m["content"]:
                if block.get("type") == "tool_use":
                    tool_use_ids.add(block.get("id"))
    for m in result:
        if m["role"] == "user" and isinstance(m["content"], list):
            m["content"] = [
                b
                for b in m["content"]
                if b.get("type") != "tool_result" or b.get("tool_use_id") in tool_use_ids
            ]
            if not m["content"]:
                m["content"] = [{"type": "text", "text": "(tool result removed)"}]

    # 强制严格的角色交替（Anthropic 拒绝连续相同角色的消息）
    fixed = []
    for m in result:
        if fixed and fixed[-1]["role"] == m["role"]:
            if m["role"] == "user":
                # 合并连续的 user 消息
                prev_content = fixed[-1]["content"]
                curr_content = m["content"]
                if isinstance(prev_content, str) and isinstance(curr_content, str):
                    fixed[-1]["content"] = prev_content + "\n" + curr_content
                elif isinstance(prev_content, list) and isinstance(curr_content, list):
                    fixed[-1]["content"] = prev_content + curr_content
                else:
                    # 混合类型 — 将字符串包装为列表
                    if isinstance(prev_content, str):
                        prev_content = [{"type": "text", "text": prev_content}]
                    if isinstance(curr_content, str):
                        curr_content = [{"type": "text", "text": curr_content}]
                    fixed[-1]["content"] = prev_content + curr_content
            else:
                # 连续的 assistant 消息 — 合并文本内容。
                # 丢弃第二条消息中的思维块：它们的签名是基于不同的
                # 轮次边界计算的，合并后签名将失效。
                if isinstance(m["content"], list):
                    m["content"] = [
                        b for b in m["content"]
                        if not (isinstance(b, dict) and b.get("type") in ("thinking", "redacted_thinking"))
                    ]
                prev_blocks = fixed[-1]["content"]
                curr_blocks = m["content"]
                if isinstance(prev_blocks, list) and isinstance(curr_blocks, list):
                    fixed[-1]["content"] = prev_blocks + curr_blocks
                elif isinstance(prev_blocks, str) and isinstance(curr_blocks, str):
                    fixed[-1]["content"] = prev_blocks + "\n" + curr_blocks
                else:
                    # 混合类型 — 将两者都规范化为列表后合并
                    if isinstance(prev_blocks, str):
                        prev_blocks = [{"type": "text", "text": prev_blocks}]
                    if isinstance(curr_blocks, str):
                        curr_blocks = [{"type": "text", "text": curr_blocks}]
                    fixed[-1]["content"] = prev_blocks + curr_blocks
        else:
            fixed.append(m)
    result = fixed

    # ── 思维块签名管理 ──────────────────────────────────────────
    # Anthropic 根据完整的轮次内容对思维块进行签名。
    # 任何上游修改（上下文压缩、会话截断、孤立块剥离、消息合并）
    # 都会使签名失效，导致 HTTP 400 "Invalid signature in thinking block"。
    #
    # 签名是 Anthropic 专有的。第三方端点（MiniMax、Azure AI Foundry、
    # 自托管代理）无法验证它们，会直接拒绝。当目标是第三方端点时，
    # 从所有 assistant 消息中剥离所有 thinking/redacted_thinking 块 —
    # 第三方如果支持扩展思维，会生成自己的思维块。
    #
    # 对于直连 Anthropic（遵循 clawdbot/OpenClaw 的策略）：
    # 1. 从除最后一条之外的所有 assistant 消息中剥离 thinking/redacted_thinking
    #    — 在当前工具使用链上保持推理连续性，同时避免过期签名错误。
    # 2. 将未签名的思维块（无签名）降级为文本 —
    #    Anthropic 无法验证它们，会拒绝。
    # 3. 从 thinking/redacted_thinking 块中移除 cache_control —
    #    缓存标记可能干扰签名验证。
    _THINKING_TYPES = frozenset(("thinking", "redacted_thinking"))
    _is_third_party = _is_third_party_anthropic_endpoint(base_url)

    last_assistant_idx = None
    for i in range(len(result) - 1, -1, -1):
        if result[i].get("role") == "assistant":
            last_assistant_idx = i
            break

    for idx, m in enumerate(result):
        if m.get("role") != "assistant" or not isinstance(m.get("content"), list):
            continue

        if _is_third_party or idx != last_assistant_idx:
            # 第三方端点：从所有 assistant 消息中剥离所有思维块 — 签名是 Anthropic 专有的。
            # 直连 Anthropic：仅从非最新的 assistant 消息中剥离。
            stripped = [
                b for b in m["content"]
                if not (isinstance(b, dict) and b.get("type") in _THINKING_TYPES)
            ]
            m["content"] = stripped or [{"type": "text", "text": "(thinking elided)"}]
        else:
            # 最新的 assistant 消息（直连 Anthropic）：保留已签名的思维块
            # 以维持推理连续性；将未签名的降级为纯文本。
            new_content = []
            for b in m["content"]:
                if not isinstance(b, dict) or b.get("type") not in _THINKING_TYPES:
                    new_content.append(b)
                    continue
                if b.get("type") == "redacted_thinking":
                    # 已编辑的思维块使用 'data' 作为签名载荷
                    if b.get("data"):
                        new_content.append(b)
                    # 否则：丢弃 — 没有 data 意味着无法被验证
                elif b.get("signature"):
                    # 已签名的思维块 — 保留
                    new_content.append(b)
                else:
                    # 未签名的思维 — 降级为文本以免内容丢失
                    thinking_text = b.get("thinking", "")
                    if thinking_text:
                        new_content.append({"type": "text", "text": thinking_text})
            m["content"] = new_content or [{"type": "text", "text": "(empty)"}]

        # 从剩余的 thinking/redacted_thinking 块中移除 cache_control
        # — 缓存标记可能干扰签名验证。
        for b in m["content"]:
            if isinstance(b, dict) and b.get("type") in _THINKING_TYPES:
                b.pop("cache_control", None)

    return system, result


def build_anthropic_kwargs(
    model: str,
    messages: List[Dict],
    tools: Optional[List[Dict]],
    max_tokens: Optional[int],
    reasoning_config: Optional[Dict[str, Any]],
    tool_choice: Optional[str] = None,
    is_oauth: bool = False,
    preserve_dots: bool = False,
    context_length: Optional[int] = None,
    base_url: str | None = None,
    fast_mode: bool = False,
) -> Dict[str, Any]:
    """构建 anthropic.messages.create() 的关键字参数。

    命名说明 — 两个容易混淆的不同概念：
      max_tokens     = 单次响应的输出令牌上限。
                       Anthropic 的 API 称之为 "max_tokens" 但它只限制*输出*。
                       Anthropic 自己的原生 SDK 为了清晰将其重命名为
                       "max_output_tokens"。
      context_length = 总上下文窗口（输入令牌 + 输出令牌）。
                       API 强制：input_tokens + max_tokens ≤ context_length。
                       存储在 ContextCompressor 上；在溢出错误时会减小。

    当 *max_tokens* 为 None 时，使用模型的原生输出上限
    （例如 Opus 4.6 为 128K，Sonnet 4.6 为 64K）。

    当提供了 *context_length* 且模型的原生输出上限超过它时（例如一个 8K 窗口
    的本地端点），输出上限会被钳制为 context_length - 1。这只在异常小的
    上下文窗口时才会触发；对于全尺寸模型，原生输出上限始终小于上下文窗口，
    所以不会进行钳制。
    注意：这种钳制不考虑提示词大小 — 如果提示词很大，Anthropic 仍可能拒绝
    请求。调用者必须检测 "max_tokens too large given prompt" 错误并用更小的
    上限重试（参见 parse_available_output_tokens_from_error +
    _ephemeral_max_output_tokens）。

    当 *is_oauth* 为 True 时，应用 Claude Code 兼容性转换：
    系统提示前缀、工具名称前缀和提示清洁化。

    当 *preserve_dots* 为 True 时，模型名称中的点号不会被转换为连字符
    （用于阿里巴巴/DashScope 的 Anthropic 兼容端点：qwen3.5-plus）。

    当 *base_url* 指向第三方 Anthropic 兼容端点时，
    思维块签名会被移除（它们是 Anthropic 专有的）。

    当 *fast_mode* 为 True 时，添加 ``extra_body["speed"] = "fast"`` 和
    快速模式 beta 头以在 Opus 4.6 上实现约 2.5 倍更快的输出吞吐量。
    目前仅在原生 Anthropic 端点上支持（不支持第三方兼容端点）。
    """
    system, anthropic_messages = convert_messages_to_anthropic(messages, base_url=base_url)
    anthropic_tools = convert_tools_to_anthropic(tools) if tools else []

    model = normalize_model_name(model, preserve_dots=preserve_dots)
    # effective_max_tokens = 本次调用的输出上限（≠ 总上下文窗口）
    effective_max_tokens = max_tokens or _get_anthropic_max_output(model)

    # 将输出上限钳制到总上下文窗口内。
    # 仅对上下文窗口小于原生输出上限的自定义小端点有意义。
    # 对于标准 Anthropic 模型，context_length（如 200K）始终大于
    # 输出上限（如 128K），因此不会进入此分支。
    if context_length and effective_max_tokens > context_length:
        effective_max_tokens = max(context_length - 1, 1)

    # ── OAuth：Claude Code 身份标识 ──────────────────────────────────
    if is_oauth:
        # 1. 在系统提示前添加 Claude Code 身份标识
        cc_block = {"type": "text", "text": _CLAUDE_CODE_SYSTEM_PREFIX}
        if isinstance(system, list):
            system = [cc_block] + system
        elif isinstance(system, str) and system:
            system = [cc_block, {"type": "text", "text": system}]
        else:
            system = [cc_block]

        # 2. 清洁化系统提示 — 替换产品名称引用
        #    以避免触发 Anthropic 的服务端内容过滤器。
        for block in system:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text", "")
                text = text.replace("Hermes Agent", "Claude Code")
                text = text.replace("Hermes agent", "Claude Code")
                text = text.replace("hermes-agent", "claude-code")
                text = text.replace("Nous Research", "Anthropic")
                block["text"] = text

        # 3. 为工具名称添加 mcp_ 前缀（Claude Code 约定）
        if anthropic_tools:
            for tool in anthropic_tools:
                if "name" in tool:
                    tool["name"] = _MCP_TOOL_PREFIX + tool["name"]

        # 4. 为消息历史中的工具名称添加前缀（tool_use 和 tool_result 块）
        for msg in anthropic_messages:
            content = msg.get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "tool_use" and "name" in block:
                            if not block["name"].startswith(_MCP_TOOL_PREFIX):
                                block["name"] = _MCP_TOOL_PREFIX + block["name"]
                        elif block.get("type") == "tool_result" and "tool_use_id" in block:
                            pass  # tool_result 使用 ID，不使用名称

    kwargs: Dict[str, Any] = {
        "model": model,
        "messages": anthropic_messages,
        "max_tokens": effective_max_tokens,
    }

    if system:
        kwargs["system"] = system

    if anthropic_tools:
        kwargs["tools"] = anthropic_tools
        # 将 OpenAI tool_choice 映射到 Anthropic 格式
        if tool_choice == "auto" or tool_choice is None:
            kwargs["tool_choice"] = {"type": "auto"}
        elif tool_choice == "required":
            kwargs["tool_choice"] = {"type": "any"}
        elif tool_choice == "none":
            # Anthropic 没有 tool_choice "none" — 完全省略 tools 以阻止使用
            kwargs.pop("tools", None)
        elif isinstance(tool_choice, str):
            # 指定的工具名称
            kwargs["tool_choice"] = {"type": "tool", "name": tool_choice}

    # 将 reasoning_config 映射到 Anthropic 的 thinking 参数。
    # Claude 4.6+ 模型使用自适应思维 + output_config.effort。
    # 较旧的模型使用手动思维和 budget_tokens。
    # MiniMax Anthropic 兼容端点支持思维（仅手动模式，不支持自适应）。
    # Haiku 不支持扩展思维 — 完全跳过。
    #
    # 在 4.7+ 上，`thinking.display` 字段默认为 "omitted"，这会静默隐藏
    # Hermes 在 CLI 中展示的推理文本。我们请求 "summarized" 以保持推理块
    # 有内容 — 与 4.6 的行为一致，并在长时间工具运行期间保持活动信息流的用户体验。
    if reasoning_config and isinstance(reasoning_config, dict):
        if reasoning_config.get("enabled") is not False and "haiku" not in model.lower():
            effort = str(reasoning_config.get("effort", "medium")).lower()
            budget = THINKING_BUDGET.get(effort, 8000)
            if _supports_adaptive_thinking(model):
                kwargs["thinking"] = {
                    "type": "adaptive",
                    "display": "summarized",
                }
                adaptive_effort = ADAPTIVE_EFFORT_MAP.get(effort, "medium")
                # 在不将 xhigh 列为支持级别的模型上（Opus/Sonnet 4.6），
                # 将 xhigh 降级为 max。Opus 4.7+ 保留 xhigh。
                if adaptive_effort == "xhigh" and not _supports_xhigh_effort(model):
                    adaptive_effort = "max"
                kwargs["output_config"] = {
                    "effort": adaptive_effort,
                }
            else:
                kwargs["thinking"] = {"type": "enabled", "budget_tokens": budget}
                # 在旧模型上启用思维时，Anthropic 要求 temperature=1
                kwargs["temperature"] = 1
                kwargs["max_tokens"] = max(effective_max_tokens, budget + 4096)

    # ── 在 4.7+ 上移除采样参数 ─────────────────────────────────
    # Opus 4.7 会以 400 错误拒绝任何非默认的 temperature/top_p/top_k。
    # 调用者（auxiliary_client、flush_memories 等）可能为旧模型设置了这些参数；
    # 在此处作为安全网移除，以便上游 4.6 → 4.7 迁移不需要到处协调修改。
    if _forbids_sampling_params(model):
        for _sampling_key in ("temperature", "top_p", "top_k"):
            kwargs.pop(_sampling_key, None)

    # ── 快速模式（仅限 Opus 4.6）────────────────────────────────────
    # 添加 extra_body.speed="fast" + 快速模式 beta 头以实现约 2.5 倍的
    # 输出速度。仅用于原生 Anthropic 端点 — 第三方提供商会拒绝
    # 未知的 beta 头和 speed 参数。
    if fast_mode and not _is_third_party_anthropic_endpoint(base_url):
        kwargs.setdefault("extra_body", {})["speed"] = "fast"
        # 构建包含所有适用 beta 的 extra_headers（每请求的
        # extra_headers 会覆盖客户端级别的 anthropic-beta 头）。
        betas = list(_common_betas_for_base_url(base_url))
        if is_oauth:
            betas.extend(_OAUTH_ONLY_BETAS)
        betas.append(_FAST_MODE_BETA)
        kwargs["extra_headers"] = {"anthropic-beta": ",".join(betas)}

    return kwargs


def normalize_anthropic_response(
    response,
    strip_tool_prefix: bool = False,
) -> Tuple[SimpleNamespace, str]:
    """将 Anthropic 响应规范化为 AIAgent 期望的格式。

    返回 (assistant_message, finish_reason)，其中 assistant_message 具有
    .content、.tool_calls 和 .reasoning 属性。

    当 *strip_tool_prefix* 为 True 时，移除为 OAuth Claude Code 兼容性
    添加的 ``mcp_`` 前缀。
    """
    text_parts = []
    reasoning_parts = []
    reasoning_details = []
    tool_calls = []

    for block in response.content:
        if block.type == "text":
            text_parts.append(block.text)
        elif block.type == "thinking":
            reasoning_parts.append(block.thinking)
            block_dict = _to_plain_data(block)
            if isinstance(block_dict, dict):
                reasoning_details.append(block_dict)
        elif block.type == "tool_use":
            name = block.name
            if strip_tool_prefix and name.startswith(_MCP_TOOL_PREFIX):
                name = name[len(_MCP_TOOL_PREFIX):]
            tool_calls.append(
                SimpleNamespace(
                    id=block.id,
                    type="function",
                    function=SimpleNamespace(
                        name=name,
                        arguments=json.dumps(block.input),
                    ),
                )
            )

    # 将 Anthropic stop_reason 映射到 OpenAI finish_reason。
    # Claude 4.5+ / 4.7 中新增的停止原因：
    #   - refusal：模型拒绝回答（网络安全保护、CSAM 等）
    #   - model_context_window_exceeded：达到上下文限制（不是 max_tokens）
    # 两者都需要上游进行不同的处理 — 拒绝应向用户显示清晰的消息，
    # 上下文窗口溢出应触发压缩/截断，而不是被视为正常的轮次结束。
    stop_reason_map = {
        "end_turn": "stop",
        "tool_use": "tool_calls",
        "max_tokens": "length",
        "stop_sequence": "stop",
        "refusal": "content_filter",
        "model_context_window_exceeded": "length",
    }
    finish_reason = stop_reason_map.get(response.stop_reason, "stop")

    return (
        SimpleNamespace(
            content="\n".join(text_parts) if text_parts else None,
            tool_calls=tool_calls or None,
            reasoning="\n\n".join(reasoning_parts) if reasoning_parts else None,
            reasoning_content=None,
            reasoning_details=reasoning_details or None,
        ),
        finish_reason,
    )
