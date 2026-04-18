"""CLI、网关、定时任务和辅助工具的共享运行时提供商解析。"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

from hermes_cli import auth as auth_mod
from agent.credential_pool import CredentialPool, PooledCredential, get_custom_provider_pool_key, load_pool
from hermes_cli.auth import (
    AuthError,
    DEFAULT_CODEX_BASE_URL,
    DEFAULT_QWEN_BASE_URL,
    PROVIDER_REGISTRY,
    _agent_key_is_usable,
    format_auth_error,
    resolve_provider,
    resolve_nous_runtime_credentials,
    resolve_codex_runtime_credentials,
    resolve_qwen_runtime_credentials,
    resolve_gemini_oauth_runtime_credentials,
    resolve_api_key_provider_credentials,
    resolve_external_process_provider_credentials,
    has_usable_secret,
)
from hermes_cli.config import get_compatible_custom_providers, load_config
from hermes_constants import OPENROUTER_BASE_URL


def _normalize_custom_provider_name(value: str) -> str:
    return value.strip().lower().replace(" ", "-")


def _detect_api_mode_for_url(base_url: str) -> Optional[str]:
    """从解析后的 base URL 自动检测 api_mode。

    直连 api.openai.com 端点需要 Responses API 来处理带推理的
    GPT-5.x 工具调用（chat/completions 会返回 400）。
    """
    normalized = (base_url or "").strip().lower().rstrip("/")
    if "api.x.ai" in normalized:
        return "codex_responses"
    if "api.openai.com" in normalized and "openrouter" not in normalized:
        return "codex_responses"
    return None


def _auto_detect_local_model(base_url: str) -> str:
    """当本地服务器只加载了一个模型时，查询其模型名称。"""
    if not base_url:
        return ""
    try:
        import requests
        url = base_url.rstrip("/")
        if not url.endswith("/v1"):
            url += "/v1"
        resp = requests.get(url + "/models", timeout=5)
        if resp.ok:
            models = resp.json().get("data", [])
            if len(models) == 1:
                model_id = models[0].get("id", "")
                if model_id:
                    return model_id
    except Exception:
        pass
    return ""


def _get_model_config() -> Dict[str, Any]:
    config = load_config()
    model_cfg = config.get("model")
    if isinstance(model_cfg, dict):
        cfg = dict(model_cfg)
        # 接受 "model" 作为 "default" 的别名（用户习惯写 model.model）
        if not cfg.get("default") and cfg.get("model"):
            cfg["default"] = cfg["model"]
        default = (cfg.get("default") or "").strip()
        base_url = (cfg.get("base_url") or "").strip()
        is_local = "localhost" in base_url or "127.0.0.1" in base_url
        is_fallback = not default
        if is_local and is_fallback and base_url:
            detected = _auto_detect_local_model(base_url)
            if detected:
                cfg["default"] = detected
        return cfg
    if isinstance(model_cfg, str) and model_cfg.strip():
        return {"default": model_cfg.strip()}
    return {}


def _provider_supports_explicit_api_mode(provider: Optional[str], configured_provider: Optional[str] = None) -> bool:
    """检查给定提供商是否应遵守持久化的 api_mode。

    防止前一个提供商的过期 api_mode 泄漏到切换后的新提供商。
    仅当配置的提供商与运行时提供商匹配（或未记录配置提供商时）
    才应用持久化的模式。
    """
    normalized_provider = (provider or "").strip().lower()
    normalized_configured = (configured_provider or "").strip().lower()
    if not normalized_configured:
        return True
    if normalized_provider == "custom":
        return normalized_configured == "custom" or normalized_configured.startswith("custom:")
    return normalized_configured == normalized_provider


def _copilot_runtime_api_mode(model_cfg: Dict[str, Any], api_key: str) -> str:
    configured_provider = str(model_cfg.get("provider") or "").strip().lower()
    configured_mode = _parse_api_mode(model_cfg.get("api_mode"))
    if configured_mode and _provider_supports_explicit_api_mode("copilot", configured_provider):
        return configured_mode

    model_name = str(model_cfg.get("default") or "").strip()
    if not model_name:
        return "chat_completions"

    try:
        from hermes_cli.models import copilot_model_api_mode

        return copilot_model_api_mode(model_name, api_key=api_key)
    except Exception:
        return "chat_completions"


_VALID_API_MODES = {"chat_completions", "codex_responses", "anthropic_messages", "bedrock_converse"}


def _parse_api_mode(raw: Any) -> Optional[str]:
    """验证来自配置的 api_mode 值。无效时返回 None。"""
    if isinstance(raw, str):
        normalized = raw.strip().lower()
        if normalized in _VALID_API_MODES:
            return normalized
    return None


def _resolve_runtime_from_pool_entry(
    *,
    provider: str,
    entry: PooledCredential,
    requested_provider: str,
    model_cfg: Optional[Dict[str, Any]] = None,
    pool: Optional[CredentialPool] = None,
) -> Dict[str, Any]:
    model_cfg = model_cfg or _get_model_config()
    base_url = (getattr(entry, "runtime_base_url", None) or getattr(entry, "base_url", None) or "").rstrip("/")
    api_key = getattr(entry, "runtime_api_key", None) or getattr(entry, "access_token", "")
    api_mode = "chat_completions"
    if provider == "openai-codex":
        api_mode = "codex_responses"
        base_url = base_url or DEFAULT_CODEX_BASE_URL
    elif provider == "qwen-oauth":
        api_mode = "chat_completions"
        base_url = base_url or DEFAULT_QWEN_BASE_URL
    elif provider == "google-gemini-cli":
        api_mode = "chat_completions"
        base_url = base_url or "cloudcode-pa://google"
    elif provider == "anthropic":
        api_mode = "anthropic_messages"
        cfg_provider = str(model_cfg.get("provider") or "").strip().lower()
        cfg_base_url = ""
        if cfg_provider == "anthropic":
            cfg_base_url = str(model_cfg.get("base_url") or "").strip().rstrip("/")
        base_url = cfg_base_url or base_url or "https://api.anthropic.com"
    elif provider == "openrouter":
        base_url = base_url or OPENROUTER_BASE_URL
    elif provider == "xai":
        api_mode = "codex_responses"
    elif provider == "nous":
        api_mode = "chat_completions"
    elif provider == "copilot":
        api_mode = _copilot_runtime_api_mode(model_cfg, getattr(entry, "runtime_api_key", ""))
        base_url = base_url or PROVIDER_REGISTRY["copilot"].inference_base_url
    else:
        configured_provider = str(model_cfg.get("provider") or "").strip().lower()
        # 当配置的提供商与此提供商匹配时，遵循 config.yaml 中的
        # model.base_url — 与上面 Anthropic 分支相同的模式。
        # 仅当池条目没有显式 base_url（即回退到硬编码默认值）时
        # 才覆盖。环境变量覆盖优先（#6039）。
        pconfig = PROVIDER_REGISTRY.get(provider)
        pool_url_is_default = pconfig and base_url.rstrip("/") == pconfig.inference_base_url.rstrip("/")
        if configured_provider == provider and pool_url_is_default:
            cfg_base_url = str(model_cfg.get("base_url") or "").strip().rstrip("/")
            if cfg_base_url:
                base_url = cfg_base_url
        configured_mode = _parse_api_mode(model_cfg.get("api_mode"))
        if configured_mode and _provider_supports_explicit_api_mode(provider, configured_provider):
            api_mode = configured_mode
        elif provider in ("opencode-zen", "opencode-go"):
            from hermes_cli.models import opencode_model_api_mode
            api_mode = opencode_model_api_mode(provider, model_cfg.get("default", ""))
        elif base_url.rstrip("/").endswith("/anthropic"):
            api_mode = "anthropic_messages"

    # OpenCode 的 base URL 以 /v1 结尾用于 OpenAI 兼容模型，但
    # Anthropic SDK 会在 base_url 前加上自己的 /v1/messages。剥离
    # 尾部的 /v1，使 SDK 构造正确的路径（例如
    # https://opencode.ai/zen/go/v1/messages 而非 .../v1/v1/messages）。
    if api_mode == "anthropic_messages" and provider in ("opencode-zen", "opencode-go"):
        base_url = re.sub(r"/v1/?$", "", base_url)

    return {
        "provider": provider,
        "api_mode": api_mode,
        "base_url": base_url,
        "api_key": api_key,
        "source": getattr(entry, "source", "pool"),
        "credential_pool": pool,
        "requested_provider": requested_provider,
    }


def resolve_requested_provider(requested: Optional[str] = None) -> str:
    """从显式参数、配置、然后环境变量解析提供商请求。"""
    if requested and requested.strip():
        return requested.strip().lower()

    model_cfg = _get_model_config()
    cfg_provider = model_cfg.get("provider")
    if isinstance(cfg_provider, str) and cfg_provider.strip():
        return cfg_provider.strip().lower()

    # 优先使用持久化配置选择，而非过期的 shell/.env 提供商覆盖，
    # 以确保聊天使用用户最后保存的端点。
    env_provider = os.getenv("HERMES_INFERENCE_PROVIDER", "").strip().lower()
    if env_provider:
        return env_provider

    return "auto"


def _try_resolve_from_custom_pool(
    base_url: str,
    provider_label: str,
    api_mode_override: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """检查自定义端点是否存在凭证池，如果存在则返回运行时字典。"""
    pool_key = get_custom_provider_pool_key(base_url)
    if not pool_key:
        return None
    try:
        pool = load_pool(pool_key)
        if not pool.has_credentials():
            return None
        entry = pool.select()
        if entry is None:
            return None
        pool_api_key = getattr(entry, "runtime_api_key", None) or getattr(entry, "access_token", "")
        if not pool_api_key:
            return None
        return {
            "provider": provider_label,
            "api_mode": api_mode_override or _detect_api_mode_for_url(base_url) or "chat_completions",
            "base_url": base_url,
            "api_key": pool_api_key,
            "source": f"pool:{pool_key}",
            "credential_pool": pool,
        }
    except Exception:
        return None


def _get_named_custom_provider(requested_provider: str) -> Optional[Dict[str, Any]]:
    requested_norm = _normalize_custom_provider_name(requested_provider or "")
    if not requested_norm or requested_norm == "custom":
        return None

    # 原始名称只在不是已有内置提供商或别名时才映射到自定义提供商。
    # 显式菜单键如 ``custom:local`` 始终指向已保存的自定义提供商。
    if requested_norm == "auto":
        return None
    if not requested_norm.startswith("custom:"):
        try:
            auth_mod.resolve_provider(requested_norm)
        except AuthError:
            pass
        else:
            return None

    config = load_config()
    
    # 首先检查 providers: dict（新式用户自定义提供商）
    providers = config.get("providers")
    if isinstance(providers, dict):
        for ep_name, entry in providers.items():
            if not isinstance(entry, dict):
                continue
            # 匹配精确名称或规范化名称
            name_norm = _normalize_custom_provider_name(ep_name)
            # 从 key_env 中存储的环境变量名解析 API 密钥
            key_env = str(entry.get("key_env", "") or "").strip()
            resolved_api_key = os.getenv(key_env, "").strip() if key_env else ""
            # 当 key_env 不存在或无法解析时，回退到内联 api_key
            if not resolved_api_key:
                resolved_api_key = str(entry.get("api_key", "") or "").strip()

            if requested_norm in {ep_name, name_norm, f"custom:{name_norm}"}:
                # 通过提供商键找到匹配
                base_url = entry.get("api") or entry.get("url") or entry.get("base_url") or ""
                if base_url:
                    return {
                        "name": entry.get("name", ep_name),
                        "base_url": base_url.strip(),
                        "api_key": resolved_api_key,
                        "model": entry.get("default_model", ""),
                    }
            # 如果存在 'name' 字段，也检查它
            display_name = entry.get("name", "")
            if display_name:
                display_norm = _normalize_custom_provider_name(display_name)
                if requested_norm in {display_name, display_norm, f"custom:{display_norm}"}:
                    # 通过显示名称找到匹配
                    base_url = entry.get("api") or entry.get("url") or entry.get("base_url") or ""
                    if base_url:
                        return {
                            "name": display_name,
                            "base_url": base_url.strip(),
                            "api_key": resolved_api_key,
                            "model": entry.get("default_model", ""),
                        }

    # 回退到 custom_providers: list（旧格式）
    custom_providers = config.get("custom_providers")
    if isinstance(custom_providers, dict):
        logger.warning(
            "custom_providers in config.yaml is a dict, not a list. "
            "Each entry must be prefixed with '-' in YAML. "
            "Run 'hermes doctor' for details."
        )
        return None

    custom_providers = get_compatible_custom_providers(config)
    if not custom_providers:
        return None

    for entry in custom_providers:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        base_url = entry.get("base_url")
        if not isinstance(name, str) or not isinstance(base_url, str):
            continue
        name_norm = _normalize_custom_provider_name(name)
        menu_key = f"custom:{name_norm}"
        provider_key = str(entry.get("provider_key", "") or "").strip()
        provider_key_norm = _normalize_custom_provider_name(provider_key) if provider_key else ""
        provider_menu_key = f"custom:{provider_key_norm}" if provider_key_norm else ""
        if requested_norm not in {name_norm, menu_key, provider_key_norm, provider_menu_key}:
            continue
        result = {
            "name": name.strip(),
            "base_url": base_url.strip(),
            "api_key": str(entry.get("api_key", "") or "").strip(),
        }
        key_env = str(entry.get("key_env", "") or "").strip()
        if key_env:
            result["key_env"] = key_env
        if provider_key:
            result["provider_key"] = provider_key
        api_mode = _parse_api_mode(entry.get("api_mode"))
        if api_mode:
            result["api_mode"] = api_mode
        model_name = str(entry.get("model", "") or "").strip()
        if model_name:
            result["model"] = model_name
        return result

    return None


def _resolve_named_custom_runtime(
    *,
    requested_provider: str,
    explicit_api_key: Optional[str] = None,
    explicit_base_url: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    custom_provider = _get_named_custom_provider(requested_provider)
    if not custom_provider:
        return None

    base_url = (
        (explicit_base_url or "").strip()
        or custom_provider.get("base_url", "")
    ).rstrip("/")
    if not base_url:
        return None

    # 检查自定义端点是否存在凭证池
    pool_result = _try_resolve_from_custom_pool(base_url, "custom", custom_provider.get("api_mode"))
    if pool_result:
        # 即使使用池化凭证也传播模型名称 —
        # 凭证池不知道 custom_providers 的 model 字段。
        model_name = custom_provider.get("model")
        if model_name:
            pool_result["model"] = model_name
        return pool_result

    api_key_candidates = [
        (explicit_api_key or "").strip(),
        str(custom_provider.get("api_key", "") or "").strip(),
        os.getenv(str(custom_provider.get("key_env", "") or "").strip(), "").strip(),
        os.getenv("OPENAI_API_KEY", "").strip(),
        os.getenv("OPENROUTER_API_KEY", "").strip(),
    ]
    api_key = next((candidate for candidate in api_key_candidates if has_usable_secret(candidate)), "")

    result = {
        "provider": "custom",
        "api_mode": custom_provider.get("api_mode")
        or _detect_api_mode_for_url(base_url)
        or "chat_completions",
        "base_url": base_url,
        "api_key": api_key or "no-key-required",
        "source": f"custom_provider:{custom_provider.get('name', requested_provider)}",
    }
    # 传播模型名称，以便调用者在提供商名称与 API 期望的实际模型字符串
    # 不同时可以覆盖 self.model。
    if custom_provider.get("model"):
        result["model"] = custom_provider["model"]
    return result


def _resolve_openrouter_runtime(
    *,
    requested_provider: str,
    explicit_api_key: Optional[str] = None,
    explicit_base_url: Optional[str] = None,
) -> Dict[str, Any]:
    model_cfg = _get_model_config()
    cfg_base_url = model_cfg.get("base_url") if isinstance(model_cfg.get("base_url"), str) else ""
    cfg_provider = model_cfg.get("provider") if isinstance(model_cfg.get("provider"), str) else ""
    cfg_api_key = ""
    for k in ("api_key", "api"):
        v = model_cfg.get(k)
        if isinstance(v, str) and v.strip():
            cfg_api_key = v.strip()
            break
    requested_norm = (requested_provider or "").strip().lower()
    cfg_provider = cfg_provider.strip().lower()

    env_openrouter_base_url = os.getenv("OPENROUTER_BASE_URL", "").strip()

    # 当有可用配置 base_url 且提供商上下文匹配时使用它。
    # 不再查询 OPENAI_BASE_URL 环境变量 — config.yaml 是
    # 端点 URL 的唯一事实来源。
    use_config_base_url = False
    if cfg_base_url.strip() and not explicit_base_url:
        if requested_norm == "auto":
            if not cfg_provider or cfg_provider == "auto":
                use_config_base_url = True
        elif requested_norm == "custom" and cfg_provider == "custom":
            use_config_base_url = True

    base_url = (
        (explicit_base_url or "").strip()
        or (cfg_base_url.strip() if use_config_base_url else "")
        or env_openrouter_base_url
        or OPENROUTER_BASE_URL
    ).rstrip("/")

    # 根据解析后的 base_url 是否指向 OpenRouter 来选择 API 密钥。
    # 访问 OpenRouter 时，优先使用 OPENROUTER_API_KEY（issue #289）。
    # 访问自定义端点（如 Z.ai、本地 LLM）时，优先使用
    # OPENAI_API_KEY，避免 OpenRouter 密钥泄漏到不相关的
    # 提供商（issues #420, #560）。
    _is_openrouter_url = "openrouter.ai" in base_url
    if _is_openrouter_url:
        api_key_candidates = [
            explicit_api_key,
            os.getenv("OPENROUTER_API_KEY"),
            os.getenv("OPENAI_API_KEY"),
        ]
    else:
        # 自定义端点：当使用配置的 base_url 时使用配置中的 api_key（#1760）。
        # 当端点是 Ollama Cloud 时，检查 OLLAMA_API_KEY — 它是
        # ollama.com 认证的规范环境变量。
        _is_ollama_url = "ollama.com" in base_url.lower()
        api_key_candidates = [
            explicit_api_key,
            (cfg_api_key if use_config_base_url else ""),
            (os.getenv("OLLAMA_API_KEY") if _is_ollama_url else ""),
            os.getenv("OPENAI_API_KEY"),
            os.getenv("OPENROUTER_API_KEY"),
        ]
    api_key = next(
        (str(candidate or "").strip() for candidate in api_key_candidates if has_usable_secret(candidate)),
        "",
    )

    source = "explicit" if (explicit_api_key or explicit_base_url) else "env/config"

    # 当明确请求 "custom" 时，保留其作为提供商名称，
    # 而不是静默地重标记为 "openrouter"（#2562）。
    # 同时为不需要认证的本地服务器提供占位 API 密钥 —
    # OpenAI SDK 要求 api_key 字符串非空。
    effective_provider = "custom" if requested_norm == "custom" else "openrouter"

    # 对于自定义端点，检查是否存在凭证池
    if effective_provider == "custom" and base_url:
        pool_result = _try_resolve_from_custom_pool(
            base_url, effective_provider, _parse_api_mode(model_cfg.get("api_mode")),
        )
        if pool_result:
            return pool_result

    if effective_provider == "custom" and not api_key and not _is_openrouter_url:
        api_key = "no-key-required"

    return {
        "provider": effective_provider,
        "api_mode": _parse_api_mode(model_cfg.get("api_mode"))
        or _detect_api_mode_for_url(base_url)
        or "chat_completions",
        "base_url": base_url,
        "api_key": api_key,
        "source": source,
    }


def _resolve_explicit_runtime(
    *,
    provider: str,
    requested_provider: str,
    model_cfg: Dict[str, Any],
    explicit_api_key: Optional[str] = None,
    explicit_base_url: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    explicit_api_key = str(explicit_api_key or "").strip()
    explicit_base_url = str(explicit_base_url or "").strip().rstrip("/")
    if not explicit_api_key and not explicit_base_url:
        return None

    if provider == "anthropic":
        cfg_provider = str(model_cfg.get("provider") or "").strip().lower()
        cfg_base_url = ""
        if cfg_provider == "anthropic":
            cfg_base_url = str(model_cfg.get("base_url") or "").strip().rstrip("/")
        base_url = explicit_base_url or cfg_base_url or "https://api.anthropic.com"
        api_key = explicit_api_key
        if not api_key:
            from agent.anthropic_adapter import resolve_anthropic_token

            api_key = resolve_anthropic_token()
            if not api_key:
                raise AuthError(
                    "No Anthropic credentials found. Set ANTHROPIC_TOKEN or ANTHROPIC_API_KEY, "
                    "run 'claude setup-token', or authenticate with 'claude /login'."
                )
        return {
            "provider": "anthropic",
            "api_mode": "anthropic_messages",
            "base_url": base_url,
            "api_key": api_key,
            "source": "explicit",
            "requested_provider": requested_provider,
        }

    if provider == "openai-codex":
        base_url = explicit_base_url or DEFAULT_CODEX_BASE_URL
        api_key = explicit_api_key
        last_refresh = None
        if not api_key:
            creds = resolve_codex_runtime_credentials()
            api_key = creds.get("api_key", "")
            last_refresh = creds.get("last_refresh")
            if not explicit_base_url:
                base_url = creds.get("base_url", "").rstrip("/") or base_url
        return {
            "provider": "openai-codex",
            "api_mode": "codex_responses",
            "base_url": base_url,
            "api_key": api_key,
            "source": "explicit",
            "last_refresh": last_refresh,
            "requested_provider": requested_provider,
        }

    if provider == "nous":
        state = auth_mod.get_provider_auth_state("nous") or {}
        base_url = (
            explicit_base_url
            or str(state.get("inference_base_url") or auth_mod.DEFAULT_NOUS_INFERENCE_URL).strip().rstrip("/")
        )
    # 仅推理使用 agent_key — access_token 是门户 API 的 OAuth 令牌
    # （用于铸造密钥、刷新令牌），不是推理 API 的。
    # 回退到 access_token 会将 OAuth 承载令牌发送到推理端点，
    # 该端点会返回 404，因为它不是有效的推理凭证。
        api_key = explicit_api_key or str(state.get("agent_key") or "").strip()
        expires_at = state.get("agent_key_expires_at") or state.get("expires_at")
        if not api_key:
            creds = resolve_nous_runtime_credentials(
                min_key_ttl_seconds=max(60, int(os.getenv("HERMES_NOUS_MIN_KEY_TTL_SECONDS", "1800"))),
                timeout_seconds=float(os.getenv("HERMES_NOUS_TIMEOUT_SECONDS", "15")),
            )
            api_key = creds.get("api_key", "")
            expires_at = creds.get("expires_at")
            if not explicit_base_url:
                base_url = creds.get("base_url", "").rstrip("/") or base_url
        return {
            "provider": "nous",
            "api_mode": "chat_completions",
            "base_url": base_url,
            "api_key": api_key,
            "source": "explicit",
            "expires_at": expires_at,
            "requested_provider": requested_provider,
        }

    pconfig = PROVIDER_REGISTRY.get(provider)
    if pconfig and pconfig.auth_type == "api_key":
        env_url = ""
        if pconfig.base_url_env_var:
            env_url = os.getenv(pconfig.base_url_env_var, "").strip().rstrip("/")

        base_url = explicit_base_url
        if not base_url:
            if provider in ("kimi-coding", "kimi-coding-cn"):
                creds = resolve_api_key_provider_credentials(provider)
                base_url = creds.get("base_url", "").rstrip("/")
            else:
                base_url = env_url or pconfig.inference_base_url

        api_key = explicit_api_key
        if not api_key:
            creds = resolve_api_key_provider_credentials(provider)
            api_key = creds.get("api_key", "")
            if not base_url:
                base_url = creds.get("base_url", "").rstrip("/")

        api_mode = "chat_completions"
        if provider == "copilot":
            api_mode = _copilot_runtime_api_mode(model_cfg, api_key)
        elif provider == "xai":
            api_mode = "codex_responses"
        else:
            configured_mode = _parse_api_mode(model_cfg.get("api_mode"))
            if configured_mode:
                api_mode = configured_mode
            elif base_url.rstrip("/").endswith("/anthropic"):
                api_mode = "anthropic_messages"

        return {
            "provider": provider,
            "api_mode": api_mode,
            "base_url": base_url.rstrip("/"),
            "api_key": api_key,
            "source": "explicit",
            "requested_provider": requested_provider,
        }

    return None


def resolve_runtime_provider(
    *,
    requested: Optional[str] = None,
    explicit_api_key: Optional[str] = None,
    explicit_base_url: Optional[str] = None,
) -> Dict[str, Any]:
    """解析智能体执行的运行时提供商凭证。"""
    requested_provider = resolve_requested_provider(requested)

    custom_runtime = _resolve_named_custom_runtime(
        requested_provider=requested_provider,
        explicit_api_key=explicit_api_key,
        explicit_base_url=explicit_base_url,
    )
    if custom_runtime:
        custom_runtime["requested_provider"] = requested_provider
        return custom_runtime

    provider = resolve_provider(
        requested_provider,
        explicit_api_key=explicit_api_key,
        explicit_base_url=explicit_base_url,
    )
    model_cfg = _get_model_config()
    explicit_runtime = _resolve_explicit_runtime(
        provider=provider,
        requested_provider=requested_provider,
        model_cfg=model_cfg,
        explicit_api_key=explicit_api_key,
        explicit_base_url=explicit_base_url,
    )
    if explicit_runtime:
        return explicit_runtime

    should_use_pool = provider != "openrouter"
    if provider == "openrouter":
        cfg_provider = str(model_cfg.get("provider") or "").strip().lower()
        cfg_base_url = str(model_cfg.get("base_url") or "").strip()
        env_openai_base_url = os.getenv("OPENAI_BASE_URL", "").strip()
        env_openrouter_base_url = os.getenv("OPENROUTER_BASE_URL", "").strip()
        has_custom_endpoint = bool(
            explicit_base_url
            or env_openai_base_url
            or env_openrouter_base_url
        )
        if cfg_base_url and cfg_provider in {"auto", "custom"}:
            has_custom_endpoint = True
        has_runtime_override = bool(explicit_api_key or explicit_base_url)
        should_use_pool = (
            requested_provider in {"openrouter", "auto"}
            and not has_custom_endpoint
            and not has_runtime_override
        )

    try:
        pool = load_pool(provider) if should_use_pool else None
    except Exception:
        pool = None
    if pool and pool.has_credentials():
        entry = pool.select()
        pool_api_key = ""
        if entry is not None:
            pool_api_key = (
                getattr(entry, "runtime_api_key", None)
                or getattr(entry, "access_token", "")
            )
    # 对于 Nous，池条目的 runtime_api_key 是 agent_key — 一个
    # 短期推理凭证（约 30 分钟 TTL）。池在选择时不会刷新它
    # （那会在非运行时上下文如 `hermes auth list` 中触发网络调用）。
    # 如果密钥已过期，清除 pool_api_key 以便回退到
    # resolve_nous_runtime_credentials()，它会处理刷新和铸造。
        if provider == "nous" and entry is not None and pool_api_key:
            min_ttl = max(60, int(os.getenv("HERMES_NOUS_MIN_KEY_TTL_SECONDS", "1800")))
            nous_state = {
                "agent_key": getattr(entry, "agent_key", None),
                "agent_key_expires_at": getattr(entry, "agent_key_expires_at", None),
            }
            if not _agent_key_is_usable(nous_state, min_ttl):
                logger.debug("Nous pool entry agent_key expired/missing, falling through to runtime resolution")
                pool_api_key = ""
        if entry is not None and pool_api_key:
            return _resolve_runtime_from_pool_entry(
                provider=provider,
                entry=entry,
                requested_provider=requested_provider,
                model_cfg=model_cfg,
                pool=pool,
            )

    if provider == "nous":
        try:
            creds = resolve_nous_runtime_credentials(
                min_key_ttl_seconds=max(60, int(os.getenv("HERMES_NOUS_MIN_KEY_TTL_SECONDS", "1800"))),
                timeout_seconds=float(os.getenv("HERMES_NOUS_TIMEOUT_SECONDS", "15")),
            )
            return {
                "provider": "nous",
                "api_mode": "chat_completions",
                "base_url": creds.get("base_url", "").rstrip("/"),
                "api_key": creds.get("api_key", ""),
                "source": creds.get("source", "portal"),
                "expires_at": creds.get("expires_at"),
                "requested_provider": requested_provider,
            }
        except AuthError:
            if requested_provider != "auto":
                raise
            # 自动检测到 Nous 但凭证过期/已撤销 —
            # 回退到环境变量提供商（如 OpenRouter）。
            logger.info("Auto-detected Nous provider but credentials failed; "
                        "falling through to next provider.")

    if provider == "openai-codex":
        try:
            creds = resolve_codex_runtime_credentials()
            return {
                "provider": "openai-codex",
                "api_mode": "codex_responses",
                "base_url": creds.get("base_url", "").rstrip("/"),
                "api_key": creds.get("api_key", ""),
                "source": creds.get("source", "hermes-auth-store"),
                "last_refresh": creds.get("last_refresh"),
                "requested_provider": requested_provider,
            }
        except AuthError:
            if requested_provider != "auto":
                raise
            # 自动检测到 Codex 但凭证过期/已撤销 —
            # 回退到环境变量提供商（如 OpenRouter）。
            logger.info("Auto-detected Codex provider but credentials failed; "
                        "falling through to next provider.")

    if provider == "qwen-oauth":
        try:
            creds = resolve_qwen_runtime_credentials()
            return {
                "provider": "qwen-oauth",
                "api_mode": "chat_completions",
                "base_url": creds.get("base_url", "").rstrip("/"),
                "api_key": creds.get("api_key", ""),
                "source": creds.get("source", "qwen-cli"),
                "expires_at_ms": creds.get("expires_at_ms"),
                "requested_provider": requested_provider,
            }
        except AuthError:
            if requested_provider != "auto":
                raise
            logger.info("Qwen OAuth credentials failed; "
                        "falling through to next provider.")

    if provider == "google-gemini-cli":
        try:
            creds = resolve_gemini_oauth_runtime_credentials()
            return {
                "provider": "google-gemini-cli",
                "api_mode": "chat_completions",
                "base_url": creds.get("base_url", ""),
                "api_key": creds.get("api_key", ""),
                "source": creds.get("source", "google-oauth"),
                "expires_at_ms": creds.get("expires_at_ms"),
                "email": creds.get("email", ""),
                "project_id": creds.get("project_id", ""),
                "requested_provider": requested_provider,
            }
        except AuthError:
            if requested_provider != "auto":
                raise
            logger.info("Google Gemini OAuth credentials failed; "
                        "falling through to next provider.")

    if provider == "copilot-acp":
        creds = resolve_external_process_provider_credentials(provider)
        return {
            "provider": "copilot-acp",
            "api_mode": "chat_completions",
            "base_url": creds.get("base_url", "").rstrip("/"),
            "api_key": creds.get("api_key", ""),
            "command": creds.get("command", ""),
            "args": list(creds.get("args") or []),
            "source": creds.get("source", "process"),
            "requested_provider": requested_provider,
        }

    # Anthropic（原生 Messages API）
    if provider == "anthropic":
        from agent.anthropic_adapter import resolve_anthropic_token
        token = resolve_anthropic_token()
        if not token:
            raise AuthError(
                "No Anthropic credentials found. Set ANTHROPIC_TOKEN or ANTHROPIC_API_KEY, "
                "run 'claude setup-token', or authenticate with 'claude /login'."
            )
        # 允许从 config.yaml 的 model.base_url 覆盖 base URL，但仅当
        # 配置的提供商是 anthropic 时 — 否则非 Anthropic 的
        # base_url（如 Codex 端点）会泄漏到 Anthropic 请求中。
        cfg_provider = str(model_cfg.get("provider") or "").strip().lower()
        cfg_base_url = ""
        if cfg_provider == "anthropic":
            cfg_base_url = (model_cfg.get("base_url") or "").strip().rstrip("/")
        base_url = cfg_base_url or "https://api.anthropic.com"
        return {
            "provider": "anthropic",
            "api_mode": "anthropic_messages",
            "base_url": base_url,
            "api_key": token,
            "source": "env",
            "requested_provider": requested_provider,
        }

    # AWS Bedrock（通过 boto3 的原生 Converse API）
    if provider == "bedrock":
        from agent.bedrock_adapter import (
            has_aws_credentials,
            resolve_aws_auth_env_var,
            resolve_bedrock_region,
            is_anthropic_bedrock_model,
        )
        # 当用户明确选择 bedrock（非自动检测）时，
        # 信任 boto3 的凭证链 — 它处理 IMDS、ECS 任务角色、
        # Lambda 执行角色、SSO 和其他我们环境变量检查无法检测的隐式来源。
        is_explicit = requested_provider in ("bedrock", "aws", "aws-bedrock", "amazon-bedrock", "amazon")
        if not is_explicit and not has_aws_credentials():
            raise AuthError(
                "No AWS credentials found for Bedrock. Configure one of:\n"
                "  - AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY\n"
                "  - AWS_PROFILE (for SSO / named profiles)\n"
                "  - IAM instance role (EC2, ECS, Lambda)\n"
                "Or run 'aws configure' to set up credentials.",
                code="no_aws_credentials",
            )
        # 从 config.yaml 读取 bedrock 特定配置
        from hermes_cli.config import load_config as _load_bedrock_config
        _bedrock_cfg = _load_bedrock_config().get("bedrock", {})
        # 区域优先级：config.yaml bedrock.region → 环境变量 → us-east-1
        region = (_bedrock_cfg.get("region") or "").strip() or resolve_bedrock_region()
        auth_source = resolve_aws_auth_env_var() or "aws-sdk-default-chain"
        # 如果已配置则构建护栏配置
        _gr = _bedrock_cfg.get("guardrail", {})
        guardrail_config = None
        if _gr.get("guardrail_identifier") and _gr.get("guardrail_version"):
            guardrail_config = {
                "guardrailIdentifier": _gr["guardrail_identifier"],
                "guardrailVersion": _gr["guardrail_version"],
            }
            if _gr.get("stream_processing_mode"):
                guardrail_config["streamProcessingMode"] = _gr["stream_processing_mode"]
            if _gr.get("trace"):
                guardrail_config["trace"] = _gr["trace"]
        # 双路径路由：Claude 模型使用 AnthropicBedrock SDK 以获得完整
        # 功能兼容性（提示缓存、思考预算、自适应思考）。
        # 非 Claude 模型使用 Converse API 以支持多模型。
        _current_model = str(model_cfg.get("default") or "").strip()
        if is_anthropic_bedrock_model(_current_model):
            # Bedrock 上的 Claude → AnthropicBedrock SDK → anthropic_messages 路径
            runtime = {
                "provider": "bedrock",
                "api_mode": "anthropic_messages",
                "base_url": f"https://bedrock-runtime.{region}.amazonaws.com",
                "api_key": "aws-sdk",
                "source": auth_source,
                "region": region,
                "bedrock_anthropic": True,  # Signal to use AnthropicBedrock client
                "requested_provider": requested_provider,
            }
        else:
            # 非 Claude（Nova、DeepSeek、Llama 等） → Converse API
            runtime = {
                "provider": "bedrock",
                "api_mode": "bedrock_converse",
                "base_url": f"https://bedrock-runtime.{region}.amazonaws.com",
                "api_key": "aws-sdk",
                "source": auth_source,
                "region": region,
                "requested_provider": requested_provider,
            }
        if guardrail_config:
            runtime["guardrail_config"] = guardrail_config
        return runtime

    # API 密钥提供商（z.ai/GLM、Kimi、MiniMax、MiniMax-CN）
    pconfig = PROVIDER_REGISTRY.get(provider)
    if pconfig and pconfig.auth_type == "api_key":
        creds = resolve_api_key_provider_credentials(provider)
        # 当配置的提供商与此提供商匹配时，遵循 config.yaml 中的
        # model.base_url — 与上面 Anthropic 路径相同。没有这个设置，
        # 将 model.base_url 设为例如 api.minimaxi.com/anthropic
        # （中国端点）的用户仍会得到硬编码的 api.minimax.io 默认值（#6039）。
        cfg_provider = str(model_cfg.get("provider") or "").strip().lower()
        cfg_base_url = ""
        if cfg_provider == provider:
            cfg_base_url = (model_cfg.get("base_url") or "").strip().rstrip("/")
        base_url = cfg_base_url or creds.get("base_url", "").rstrip("/")
        api_mode = "chat_completions"
        if provider == "copilot":
            api_mode = _copilot_runtime_api_mode(model_cfg, creds.get("api_key", ""))
        elif provider == "xai":
            api_mode = "codex_responses"
        else:
            configured_provider = str(model_cfg.get("provider") or "").strip().lower()
            # 仅当持久化 api_mode 属于同一提供商家族时才遵循。
            configured_mode = _parse_api_mode(model_cfg.get("api_mode"))
            if configured_mode and _provider_supports_explicit_api_mode(provider, configured_provider):
                api_mode = configured_mode
            elif provider in ("opencode-zen", "opencode-go"):
                from hermes_cli.models import opencode_model_api_mode
                api_mode = opencode_model_api_mode(provider, model_cfg.get("default", ""))
            # 通过 URL 约定自动检测 Anthropic 兼容端点
            # （如 https://api.minimax.io/anthropic, https://dashscope.../anthropic）
            elif base_url.rstrip("/").endswith("/anthropic"):
                api_mode = "anthropic_messages"
        # 为 OpenCode Anthropic 模型剥离尾部 /v1（见上方注释）。
        if api_mode == "anthropic_messages" and provider in ("opencode-zen", "opencode-go"):
            base_url = re.sub(r"/v1/?$", "", base_url)
        return {
            "provider": provider,
            "api_mode": api_mode,
            "base_url": base_url,
            "api_key": creds.get("api_key", ""),
            "source": creds.get("source", "env"),
            "requested_provider": requested_provider,
        }

    runtime = _resolve_openrouter_runtime(
        requested_provider=requested_provider,
        explicit_api_key=explicit_api_key,
        explicit_base_url=explicit_base_url,
    )
    runtime["requested_provider"] = requested_provider
    return runtime


def format_runtime_provider_error(error: Exception) -> str:
    if isinstance(error, AuthError):
        return format_auth_error(error)
    return str(error)
