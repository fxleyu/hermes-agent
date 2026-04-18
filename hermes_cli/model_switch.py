"""CLI 和 gateway /model 命令共享的模型切换逻辑。

CLI (cli.py) 和 gateway (gateway/run.py) 的 /model 处理程序
共享相同的核心流水线：

  解析标志 -> 别名解析 -> 提供商解析 ->
  凭证解析 -> 规范化模型名称 ->
  元数据查找 -> 构建结果

本模块整合了以下基础层：

- ``agent.models_dev``            -- models.dev 目录, ModelInfo, ProviderInfo
- ``hermes_cli.providers``        -- 规范提供商身份 + 覆盖层
- ``hermes_cli.model_normalize``  -- 按提供商格式化名称

提供商切换专门使用 ``--provider`` 标志。
不使用冒号格式的 ``provider:model`` 语法 — 冒号保留给
OpenRouter 变体后缀 (``:free``, ``:extended``, ``:fast``)。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import List, NamedTuple, Optional

from hermes_cli.providers import (
    custom_provider_slug,
    determine_api_mode,
    get_label,
    is_aggregator,
    resolve_provider_full,
)
from hermes_cli.model_normalize import (
    normalize_model_for_provider,
)
from agent.models_dev import (
    ModelCapabilities,
    ModelInfo,
    get_model_capabilities,
    get_model_info,
    list_provider_models,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 非代理模型警告
# ---------------------------------------------------------------------------

_HERMES_MODEL_WARNING = (
    "Nous Research Hermes 3 & 4 models are NOT agentic and are not designed "
    "for use with Hermes Agent. They lack the tool-calling capabilities "
    "required for agent workflows. Consider using an agentic model instead "
    "(Claude, GPT, Gemini, DeepSeek, etc.)."
)

# 仅匹配真正的 Nous Research Hermes 3 / Hermes 4 聊天系列。
# 之前的子串检查（`"hermes" in name.lower()`）会对无关的本地
# Modelfile（如 ``hermes-brain:qwen3-14b-ctx16k``）产生误报，
# 这些模型只是在标签中碰巧包含 "hermes"，但具有完整的工具调用能力。
#
# 正则表达式必须匹配的正例：
#   NousResearch/Hermes-3-Llama-3.1-70B, hermes-4-405b, openrouter/hermes3:70b
# 正则表达式不应匹配的反例：
#   hermes-brain:qwen3-14b-ctx16k, qwen3:14b, claude-opus-4-6
_NOUS_HERMES_NON_AGENTIC_RE = re.compile(
    r"(?:^|[/:])hermes[-_ ]?[34](?:[-_.:]|$)",
    re.IGNORECASE,
)


def is_nous_hermes_non_agentic(model_name: str) -> bool:
    """当 *model_name* 是真正的 Nous Hermes 3/4 聊天模型时返回 True。

    用于决定是否在启动时显示非代理警告。
    :mod:`cli.py` 和此处的调用者应使用此单一辅助函数，
    以避免两个位置的逻辑不一致。
    """
    if not model_name:
        return False
    return bool(_NOUS_HERMES_NON_AGENTIC_RE.search(model_name))


def _check_hermes_model_warning(model_name: str) -> str:
    """当 *model_name* 是 Nous Hermes 3/4 聊天模型时返回警告字符串。"""
    if is_nous_hermes_non_agentic(model_name):
        return _HERMES_MODEL_WARNING
    return ""


# ---------------------------------------------------------------------------
# 模型别名 -- 短名称 -> (供应商, 系列)，不包含版本号。
# 根据实时 models.dev 目录动态解析。
# ---------------------------------------------------------------------------

class ModelIdentity(NamedTuple):
    """用于目录解析的供应商标识和系列前缀。"""
    vendor: str
    family: str


MODEL_ALIASES: dict[str, ModelIdentity] = {
    # Anthropic
    "sonnet":    ModelIdentity("anthropic", "claude-sonnet"),
    "opus":      ModelIdentity("anthropic", "claude-opus"),
    "haiku":     ModelIdentity("anthropic", "claude-haiku"),
    "claude":    ModelIdentity("anthropic", "claude"),

    # OpenAI
    "gpt5":      ModelIdentity("openai", "gpt-5"),
    "gpt":       ModelIdentity("openai", "gpt"),
    "codex":     ModelIdentity("openai", "codex"),
    "o3":        ModelIdentity("openai", "o3"),
    "o4":        ModelIdentity("openai", "o4"),

    # Google
    "gemini":    ModelIdentity("google", "gemini"),

    # DeepSeek
    "deepseek":  ModelIdentity("deepseek", "deepseek-chat"),

    # X.AI
    "grok":      ModelIdentity("x-ai", "grok"),

    # Meta
    "llama":     ModelIdentity("meta-llama", "llama"),

    # Qwen / Alibaba
    "qwen":      ModelIdentity("qwen", "qwen"),

    # MiniMax
    "minimax":   ModelIdentity("minimax", "minimax"),

    # Nvidia
    "nemotron":  ModelIdentity("nvidia", "nemotron"),

    # Moonshot / Kimi
    "kimi":      ModelIdentity("moonshotai", "kimi"),

    # Z.AI / GLM
    "glm":       ModelIdentity("z-ai", "glm"),

    # StepFun
    "step":      ModelIdentity("stepfun", "step"),

    # Xiaomi
    "mimo":      ModelIdentity("xiaomi", "mimo"),

    # Arcee
    "trinity":   ModelIdentity("arcee-ai", "trinity"),
}


# ---------------------------------------------------------------------------
# 直接别名 — 精确的 model+provider+base_url，用于不在
# models.dev 目录中的端点（如 Ollama Cloud, 本地服务器）。
# 在目录解析之前检查。格式：
#   别名 -> (model_id, provider, base_url)
# 也可以从 config.yaml ``model_aliases:`` 部分加载。
# ---------------------------------------------------------------------------

class DirectAlias(NamedTuple):
    """跳过目录解析的精确模型映射。"""
    model: str
    provider: str
    base_url: str


# 内置直接别名（可通过 config.yaml model_aliases: 扩展）
_BUILTIN_DIRECT_ALIASES: dict[str, DirectAlias] = {}

# 合并后的字典（内置 + 用户配置）；由 _load_direct_aliases() 填充
DIRECT_ALIASES: dict[str, DirectAlias] = {}


def _load_direct_aliases() -> dict[str, DirectAlias]:
    """从 config.yaml ``model_aliases:`` 部分加载直接别名。

    配置格式::

        model_aliases:
          qwen:
            model: "qwen3.5:397b"
            provider: custom
            base_url: "https://ollama.com/v1"
          minimax:
            model: "minimax-m2.7"
            provider: custom
            base_url: "https://ollama.com/v1"
    """
    merged = dict(_BUILTIN_DIRECT_ALIASES)
    try:
        from hermes_cli.config import load_config
        cfg = load_config()
        user_aliases = cfg.get("model_aliases")
        if isinstance(user_aliases, dict):
            for name, entry in user_aliases.items():
                if not isinstance(entry, dict):
                    continue
                model = entry.get("model", "")
                provider = entry.get("provider", "custom")
                base_url = entry.get("base_url", "")
                if model:
                    merged[name.strip().lower()] = DirectAlias(
                        model=model, provider=provider, base_url=base_url,
                    )
    except Exception:
        pass
    return merged


def _ensure_direct_aliases() -> None:
    """首次使用时懒加载直接别名。"""
    global DIRECT_ALIASES
    if not DIRECT_ALIASES:
        DIRECT_ALIASES = _load_direct_aliases()


# ---------------------------------------------------------------------------
# 结果数据类
# ---------------------------------------------------------------------------

@dataclass
class ModelSwitchResult:
    """模型切换尝试的结果。"""

    success: bool
    new_model: str = ""
    target_provider: str = ""
    provider_changed: bool = False
    api_key: str = ""
    base_url: str = ""
    api_mode: str = ""
    error_message: str = ""
    warning_message: str = ""
    provider_label: str = ""
    resolved_via_alias: str = ""
    capabilities: Optional[ModelCapabilities] = None
    model_info: Optional[ModelInfo] = None
    is_global: bool = False


@dataclass
class CustomAutoResult:
    """切换到裸 'custom' 提供商并自动检测的结果。"""

    success: bool
    model: str = ""
    base_url: str = ""
    api_key: str = ""
    error_message: str = ""


# ---------------------------------------------------------------------------
# 标志解析
# ---------------------------------------------------------------------------

def parse_model_flags(raw_args: str) -> tuple[str, str, bool]:
    """从 /model 命令参数中解析 --provider 和 --global 标志。

    返回 (model_input, explicit_provider, is_global)。

    示例::

        "sonnet"                         -> ("sonnet", "", False)
        "sonnet --global"                -> ("sonnet", "", True)
        "sonnet --provider anthropic"    -> ("sonnet", "anthropic", False)
        "--provider my-ollama"           -> ("", "my-ollama", False)
        "sonnet --provider anthropic --global" -> ("sonnet", "anthropic", True)
    """
    is_global = False
    explicit_provider = ""

    # 规范化 Unicode 破折号（Telegram/iOS 会自动将 -- 转换为 em/en 破折号）
    # 标志关键词前的单个 Unicode 破折号变为 "--"
    import re as _re
    raw_args = _re.sub(r'[\u2012\u2013\u2014\u2015](provider|global)', r'--\1', raw_args)

    # 提取 --global
    if "--global" in raw_args:
        is_global = True
        raw_args = raw_args.replace("--global", "").strip()

    # 提取 --provider <name>
    parts = raw_args.split()
    i = 0
    filtered: list[str] = []
    while i < len(parts):
        if parts[i] == "--provider" and i + 1 < len(parts):
            explicit_provider = parts[i + 1]
            i += 2
        else:
            filtered.append(parts[i])
            i += 1

    model_input = " ".join(filtered).strip()
    return (model_input, explicit_provider, is_global)


# ---------------------------------------------------------------------------
# 别名解析
# ---------------------------------------------------------------------------

def resolve_alias(
    raw_input: str,
    current_provider: str,
) -> Optional[tuple[str, str, str]]:
    """根据当前提供商的目录解析短别名。

    在 :data:`MODEL_ALIASES` 中查找 *raw_input*，然后在当前提供商的
    models.dev 目录中搜索 ID 以 ``vendor/family``（或对于非聚合器
    提供商仅以 ``family``）开头的第一个模型。

    返回:
        如果在当前提供商找到匹配，返回 ``(provider, resolved_model_id, alias_name)``，
        如果别名不存在或没有可用的匹配模型，返回 ``None``。
    """
    key = raw_input.strip().lower()

    # 首先检查直接别名（精确的 model+provider+base_url 映射）
    _ensure_direct_aliases()
    direct = DIRECT_ALIASES.get(key)
    if direct is not None:
        return (direct.provider, direct.model, key)

    # 反向查找：按模型 ID 匹配，使完整名称（如 "kimi-k2.5",
    # "glm-4.7"）通过直接别名路由，而不是回退到目录/OpenRouter。
    for alias_name, da in DIRECT_ALIASES.items():
        if da.model.lower() == key:
            return (da.provider, da.model, alias_name)

    identity = MODEL_ALIASES.get(key)
    if identity is None:
        return None

    vendor, family = identity

    # 在提供商的 models.dev 目录中搜索
    catalog = list_provider_models(current_provider)
    if not catalog:
        return None

    # 对于聚合器，模型格式为 vendor/model-name
    aggregator = is_aggregator(current_provider)

    for model_id in catalog:
        mid_lower = model_id.lower()
        if aggregator:
            # 匹配 vendor/family 前缀 -- 例如 "anthropic/claude-sonnet"
            prefix = f"{vendor}/{family}".lower()
            if mid_lower.startswith(prefix):
                return (current_provider, model_id, key)
        else:
            # 非聚合器：裸名称 -- 例如 "claude-sonnet-4-6"
            family_lower = family.lower()
            if mid_lower.startswith(family_lower):
                return (current_provider, model_id, key)

    return None


def get_authenticated_provider_slugs(
    current_provider: str = "",
    user_providers: dict = None,
    custom_providers: list | None = None,
) -> list[str]:
    """返回拥有凭证的提供商标识列表。

    使用 ``list_authenticated_providers()``，该函数由 models.dev
    内存缓存（1 小时 TTL）支持 — 无额外网络开销。
    """
    try:
        providers = list_authenticated_providers(
            current_provider=current_provider,
            user_providers=user_providers,
            custom_providers=custom_providers,
            max_models=0,
        )
        return [p["slug"] for p in providers]
    except Exception:
        return []


def _resolve_alias_fallback(
    raw_input: str,
    authenticated_providers: list[str] = (),
) -> Optional[tuple[str, str, str]]:
    """尝试在用户已认证的提供商上解析别名。

    仅当未提供已认证提供商时回退到 ``("openrouter", "nous")``
    （向后兼容非交互式调用者）。
    """
    providers = authenticated_providers or ("openrouter", "nous")
    for provider in providers:
        result = resolve_alias(raw_input, provider)
        if result is not None:
            return result
    return None


# ---------------------------------------------------------------------------
# 核心模型切换流水线
# ---------------------------------------------------------------------------

def switch_model(
    raw_input: str,
    current_provider: str,
    current_model: str,
    current_base_url: str = "",
    current_api_key: str = "",
    is_global: bool = False,
    explicit_provider: str = "",
    user_providers: dict = None,
    custom_providers: list | None = None,
) -> ModelSwitchResult:
    """CLI 和 gateway 共享的核心模型切换流水线。

    解析链：

      如果指定了 --provider：
        a. 通过 resolve_provider_full() 解析提供商
        b. 解析凭证
        c. 如果指定了模型，在目标提供商上解析别名或按原样使用
        d. 如果没有指定模型，从端点自动检测

      如果没有指定 --provider：
        a. 在当前提供商上尝试别名解析
        b. 如果别名存在但不在当前提供商上 -> 回退
        c. 在聚合器上，尝试 vendor/model 格式转换
        d. 聚合器目录搜索
        e. detect_provider_for_model() 作为最后手段
        f. 解析凭证
        g. 为目标提供商规范化模型名称

      最后：
        h. 从 models.dev 获取完整模型元数据
        i. 构建结果

    参数:
        raw_input: 模型名称（标志解析后）。
        current_provider: 当前活跃的提供商。
        current_model: 当前活跃的模型名称。
        current_base_url: 当前活跃的基础 URL。
        current_api_key: 当前活跃的 API 密钥。
        is_global: 是否持久化切换。
        explicit_provider: 来自 --provider 标志（空 = 未显式指定提供商）。
        user_providers: config.yaml 中的 ``providers:`` 字典（用于用户端点）。
        custom_providers: config.yaml 中的 ``custom_providers:`` 列表。

    返回:
        包含调用者所需全部信息的 ModelSwitchResult。
    """
    from hermes_cli.models import (
        copilot_model_api_mode,
        detect_provider_for_model,
        validate_requested_model,
        opencode_model_api_mode,
    )
    from hermes_cli.runtime_provider import resolve_runtime_provider

    resolved_alias = ""
    new_model = raw_input.strip()
    target_provider = current_provider

    # =================================================================
    # 路径 A：显式指定了 --provider
    # =================================================================
    if explicit_provider:
        # 解析提供商
        pdef = resolve_provider_full(
            explicit_provider,
            user_providers,
            custom_providers,
        )
        if pdef is None:
            _switch_err = (
                f"Unknown provider '{explicit_provider}'. "
                f"Check 'hermes model' for available providers, or define it "
                f"in config.yaml under 'providers:'."
            )
            # 检查常见的配置问题，这些问题会导致提供商解析失败
            try:
                from hermes_cli.config import validate_config_structure
                _cfg_issues = validate_config_structure()
                if _cfg_issues:
                    _switch_err += "\n\nRun 'hermes doctor' — config issues detected:"
                    for _ci in _cfg_issues[:3]:
                        _switch_err += f"\n  • {_ci.message}"
            except Exception:
                pass
            return ModelSwitchResult(
                success=False,
                is_global=is_global,
                error_message=_switch_err,
            )

        target_provider = pdef.id

        # 如果没有指定模型，尝试从端点自动检测
        if not new_model:
            if pdef.base_url:
                from hermes_cli.runtime_provider import _auto_detect_local_model
                detected = _auto_detect_local_model(pdef.base_url)
                if detected:
                    new_model = detected
                else:
                    return ModelSwitchResult(
                        success=False,
                        target_provider=target_provider,
                        provider_label=pdef.name,
                        is_global=is_global,
                        error_message=(
                            f"No model detected on {pdef.name} ({pdef.base_url}). "
                            f"Specify the model explicitly: /model <model-name> --provider {explicit_provider}"
                        ),
                    )
            else:
                return ModelSwitchResult(
                    success=False,
                    target_provider=target_provider,
                    provider_label=pdef.name,
                    is_global=is_global,
                    error_message=(
                        f"Provider '{pdef.name}' has no base URL configured. "
                        f"Specify a model: /model <model-name> --provider {explicit_provider}"
                    ),
                )

        # 在目标提供商上解析别名
        alias_result = resolve_alias(new_model, target_provider)
        if alias_result is not None:
            _, new_model, resolved_alias = alias_result

    # =================================================================
    # 路径 B：没有显式指定提供商 — 从模型输入推断
    # =================================================================
    else:
        # --- 步骤 a：在当前提供商上尝试别名解析 ---
        alias_result = resolve_alias(raw_input, current_provider)

        if alias_result is not None:
            target_provider, new_model, resolved_alias = alias_result
            logger.debug(
                "Alias '%s' resolved to %s on %s",
                resolved_alias, new_model, target_provider,
            )
        else:
            # --- 步骤 b：别名存在但不在当前提供商上 -> 回退 ---
            key = raw_input.strip().lower()
            if key in MODEL_ALIASES:
                authed = get_authenticated_provider_slugs(
                    current_provider=current_provider,
                    user_providers=user_providers,
                    custom_providers=custom_providers,
                )
                fallback_result = _resolve_alias_fallback(raw_input, authed)
                if fallback_result is not None:
                    target_provider, new_model, resolved_alias = fallback_result
                    logger.debug(
                        "Alias '%s' resolved via fallback to %s on %s",
                        resolved_alias, new_model, target_provider,
                    )
                else:
                    identity = MODEL_ALIASES[key]
                    return ModelSwitchResult(
                        success=False,
                        is_global=is_global,
                        error_message=(
                            f"Alias '{key}' maps to {identity.vendor}/{identity.family} "
                            f"but no matching model was found in any provider catalog. "
                            f"Try specifying the full model name."
                        ),
                    )
            else:
                # --- 步骤 c：在聚合器上，将 vendor:model 转换为 vendor/model ---
                # 仅在没有斜杠时转换 — 斜杠表示名称已是 vendor/model 格式，
                # 冒号是变体标签（:free, :extended, :fast），必须保留。
                colon_pos = raw_input.find(":")
                if colon_pos > 0 and "/" not in raw_input and is_aggregator(current_provider):
                    left = raw_input[:colon_pos].strip().lower()
                    right = raw_input[colon_pos + 1:].strip()
                    if left and right:
                        # 冒号在聚合器标识中变为斜杠
                        new_model = f"{left}/{right}"
                        logger.debug(
                            "Converted vendor:model '%s' to aggregator slug '%s'",
                            raw_input, new_model,
                        )

        # --- 步骤 d：聚合器目录搜索 ---
        if is_aggregator(target_provider) and not resolved_alias:
            catalog = list_provider_models(target_provider)
            if catalog:
                new_model_lower = new_model.lower()
                for mid in catalog:
                    if mid.lower() == new_model_lower:
                        new_model = mid
                        break
                else:
                    for mid in catalog:
                        if "/" in mid:
                            _, bare = mid.split("/", 1)
                            if bare.lower() == new_model_lower:
                                new_model = mid
                                break

        # --- 步骤 e：detect_provider_for_model() 作为最后手段 ---
        _base = current_base_url or ""
        is_custom = current_provider in ("custom", "local") or (
            "localhost" in _base or "127.0.0.1" in _base
        )

        if (
            target_provider == current_provider
            and not is_custom
            and not resolved_alias
        ):
            detected = detect_provider_for_model(new_model, current_provider)
            if detected:
                target_provider, new_model = detected

    # =================================================================
    # 公共路径：解析凭证、规范化名称、获取元数据
    # =================================================================

    provider_changed = target_provider != current_provider
    provider_label = get_label(target_provider)
    if target_provider.startswith("custom:"):
        custom_pdef = resolve_provider_full(
            target_provider,
            user_providers,
            custom_providers,
        )
        if custom_pdef is not None:
            provider_label = custom_pdef.name

    # --- 解析凭证 ---
    api_key = current_api_key
    base_url = current_base_url
    api_mode = ""

    if provider_changed or explicit_provider:
        try:
            runtime = resolve_runtime_provider(requested=target_provider)
            api_key = runtime.get("api_key", "")
            base_url = runtime.get("base_url", "")
            api_mode = runtime.get("api_mode", "")
        except Exception as e:
            return ModelSwitchResult(
                success=False,
                target_provider=target_provider,
                provider_label=provider_label,
                is_global=is_global,
                error_message=(
                    f"Could not resolve credentials for provider "
                    f"'{provider_label}': {e}"
                ),
            )
    else:
        try:
            runtime = resolve_runtime_provider(requested=current_provider)
            api_key = runtime.get("api_key", "")
            base_url = runtime.get("base_url", "")
            api_mode = runtime.get("api_mode", "")
        except Exception:
            pass

    # --- 直接别名覆盖：如果别名设置了 base_url 则使用精确值 ---
    if resolved_alias:
        _ensure_direct_aliases()
        _da = DIRECT_ALIASES.get(resolved_alias)
        if _da is not None and _da.base_url:
            base_url = _da.base_url
            if not api_key:
                api_key = "no-key-required"

    # --- 为目标提供商规范化模型名称 ---
    new_model = normalize_model_for_provider(new_model, target_provider)

    # --- 验证 ---
    try:
        validation = validate_requested_model(
            new_model,
            target_provider,
            api_key=api_key,
            base_url=base_url,
        )
    except Exception:
        validation = {
            "accepted": True,
            "persist": True,
            "recognized": False,
            "message": None,
        }

    if not validation.get("accepted"):
        msg = validation.get("message", "Invalid model")
        return ModelSwitchResult(
            success=False,
            new_model=new_model,
            target_provider=target_provider,
            provider_label=provider_label,
            is_global=is_global,
            error_message=msg,
        )

    # 如果验证找到了更接近的匹配，应用自动修正
    if validation.get("corrected_model"):
        new_model = validation["corrected_model"]

    # --- Copilot api_mode 覆盖 ---
    if target_provider in {"copilot", "github-copilot"}:
        api_mode = copilot_model_api_mode(new_model, api_key=api_key)

    # --- OpenCode api_mode 覆盖 ---
    if target_provider in {"opencode-zen", "opencode-go", "opencode"}:
        api_mode = opencode_model_api_mode(target_provider, new_model)

    # --- 如果尚未设置则确定 api_mode ---
    if not api_mode:
        api_mode = determine_api_mode(target_provider, base_url)

    # OpenCode 基础 URL 以 /v1 结尾用于 OpenAI 兼容模型，但
    # Anthropic SDK 会在 base_url 前添加自己的 /v1/messages。去掉
    # 末尾的 /v1，这样 SDK 才能构建正确的路径（例如
    # https://opencode.ai/zen/go/v1/messages 而不是 .../v1/v1/messages）。
    # 与 hermes_cli.runtime_provider.resolve_runtime_provider 中的相同逻辑一致；
    # 如果不这样做，/model 切换到 anthropic_messages 路由的 OpenCode
    # 模型（例如在 opencode-go 上 `/model minimax-m2.7`，在 opencode-zen
    # 上 `/model claude-sonnet-4-6`）会命中双重 /v1 并返回 OpenCode 网站的 404 页面。
    if (
        api_mode == "anthropic_messages"
        and target_provider in {"opencode-zen", "opencode-go"}
        and isinstance(base_url, str)
        and base_url
    ):
        base_url = re.sub(r"/v1/?$", "", base_url)

    # --- 获取能力信息（旧版） ---
    capabilities = get_model_capabilities(target_provider, new_model)

    # --- 从 models.dev 获取完整模型信息 ---
    model_info = get_model_info(target_provider, new_model)

    # --- 收集警告 ---
    warnings: list[str] = []
    if validation.get("message"):
        warnings.append(validation["message"])
    hermes_warn = _check_hermes_model_warning(new_model)
    if hermes_warn:
        warnings.append(hermes_warn)

    # --- 构建结果 ---
    return ModelSwitchResult(
        success=True,
        new_model=new_model,
        target_provider=target_provider,
        provider_changed=provider_changed,
        api_key=api_key,
        base_url=base_url,
        api_mode=api_mode,
        warning_message=" | ".join(warnings) if warnings else "",
        provider_label=provider_label,
        resolved_via_alias=resolved_alias,
        capabilities=capabilities,
        model_info=model_info,
        is_global=is_global,
    )


# ---------------------------------------------------------------------------
# 已认证提供商列表（用于 /model 无参数时的显示）
# ---------------------------------------------------------------------------

def list_authenticated_providers(
    current_provider: str = "",
    user_providers: dict = None,
    custom_providers: list | None = None,
    max_models: int = 8,
) -> List[dict]:
    """检测哪些提供商有凭证，并列出其精选模型。

    使用 hermes_cli/models.py 中的精选模型列表（OPENROUTER_MODELS,
    _PROVIDER_MODELS）— 不是完整的 models.dev 目录。这些是精心挑选的
    适合作为代理后端的代理模型。

    返回一个字典列表，每个字典包含：
      - slug: str — 要使用的 --provider 值
      - name: str — 显示名称
      - is_current: bool
      - is_user_defined: bool
      - models: list[str] — 精选模型 ID（最多 max_models 个）
      - total_models: int — 精选总数
      - source: str — "built-in", "models.dev", "user-config"

    仅包含设置了 API 密钥或为用户自定义端点的提供商。
    """
    import os
    from agent.models_dev import (
        PROVIDER_TO_MODELS_DEV,
        fetch_models_dev,
        get_provider_info as _mdev_pinfo,
    )
    from hermes_cli.auth import PROVIDER_REGISTRY
    from hermes_cli.models import OPENROUTER_MODELS, _PROVIDER_MODELS

    results: List[dict] = []
    seen_slugs: set = set()  # 小写规范化以捕获大小写变体 (#9545)
    seen_mdev_ids: set = set()  # 防止别名的重复条目（如 kimi-coding + kimi-coding-cn）

    data = fetch_models_dev()

    # 构建以 hermes 提供商 ID 为键的精选模型列表
    curated: dict[str, list[str]] = dict(_PROVIDER_MODELS)
    curated["openrouter"] = [mid for mid, _ in OPENROUTER_MODELS]
    # "nous" 如果没有单独定义则共享 OpenRouter 的精选列表
    if "nous" not in curated:
        curated["nous"] = curated["openrouter"]
    # Ollama Cloud 使用动态发现（没有静态精选列表）
    if "ollama-cloud" not in curated:
        from hermes_cli.models import fetch_ollama_cloud_models
        curated["ollama-cloud"] = fetch_ollama_cloud_models()

    # --- 1. 检查 Hermes 映射的提供商 ---
    for hermes_id, mdev_id in PROVIDER_TO_MODELS_DEV.items():
        # 跳过映射到同一 models.dev 提供商的别名（如
        # kimi-coding 和 kimi-coding-cn 都映射到 kimi-for-coding）。
        # 第一个有有效凭证的获胜 (#10526)。
        if mdev_id in seen_mdev_ids:
            continue
        pdata = data.get(mdev_id)
        if not isinstance(pdata, dict):
            continue

        # 优先使用 auth.py 的 PROVIDER_REGISTRY 获取环境变量名称 — 这是我们的
        # 权威来源。models.dev 可能有错误的映射（如
        # minimax-cn → MINIMAX_API_KEY 而不是 MINIMAX_CN_API_KEY）。
        pconfig = PROVIDER_REGISTRY.get(hermes_id)
        if pconfig and pconfig.api_key_env_vars:
            env_vars = list(pconfig.api_key_env_vars)
        else:
            env_vars = pdata.get("env", [])
            if not isinstance(env_vars, list):
                continue

        # 检查是否有任何环境变量被设置
        has_creds = any(os.environ.get(ev) for ev in env_vars)
        if not has_creds:
            continue

        # 使用精选列表，如果没有精选列表则回退到 models.dev
        model_ids = curated.get(hermes_id, [])
        total = len(model_ids)
        top = model_ids[:max_models]

        slug = hermes_id
        pinfo = _mdev_pinfo(mdev_id)
        display_name = pinfo.name if pinfo else mdev_id

        results.append({
            "slug": slug,
            "name": display_name,
            "is_current": slug == current_provider or mdev_id == current_provider,
            "is_user_defined": False,
            "models": top,
            "total_models": total,
            "source": "built-in",
        })
        seen_slugs.add(slug.lower())
        seen_mdev_ids.add(mdev_id)

    # --- 2. 检查仅 Hermes 的提供商（nous, openai-codex, copilot, opencode-go）---
    from hermes_cli.providers import HERMES_OVERLAYS
    from hermes_cli.auth import PROVIDER_REGISTRY as _auth_registry

    # 构建反向映射：models.dev ID → Hermes 提供商 ID。
    # HERMES_OVERLAYS 键可能是 models.dev ID（如 "github-copilot"）
    # 而 _PROVIDER_MODELS 和 config.yaml 使用 Hermes ID（"copilot"）。
    _mdev_to_hermes = {v: k for k, v in PROVIDER_TO_MODELS_DEV.items()}

    for pid, overlay in HERMES_OVERLAYS.items():
        if pid.lower() in seen_slugs:
            continue

        # 解析 Hermes slug — 如 "github-copilot" → "copilot"
        hermes_slug = _mdev_to_hermes.get(pid, pid)
        if hermes_slug.lower() in seen_slugs:
            continue

        # 检查凭证是否存在
        has_creds = False
        if overlay.extra_env_vars:
            has_creds = any(os.environ.get(ev) for ev in overlay.extra_env_vars)
        # 也检查 PROVIDER_REGISTRY 中 api_key 认证类型的 api_key_env_vars
        if not has_creds and overlay.auth_type == "api_key":
            for _key in (pid, hermes_slug):
                pcfg = _auth_registry.get(_key)
                if pcfg and pcfg.api_key_env_vars:
                    if any(os.environ.get(ev) for ev in pcfg.api_key_env_vars):
                        has_creds = True
                        break
        # 检查认证存储和凭证池中非环境变量的凭证。
        # 这适用于 OAuth 提供商以及同时支持 OAuth 的 api_key 提供商
        # （如 anthropic 同时支持 API 密钥和通过外部凭证文件的
        # Claude Code OAuth）。
        if not has_creds:
            try:
                from hermes_cli.auth import _load_auth_store
                store = _load_auth_store()
                providers_store = store.get("providers", {})
                pool_store = store.get("credential_pool", {})
                if store and (
                    pid in providers_store or hermes_slug in providers_store
                    or pid in pool_store or hermes_slug in pool_store
                ):
                    has_creds = True
            except Exception as exc:
                logger.debug("Auth store check failed for %s: %s", pid, exc)
        # 回退：使用完整的自动播种检查凭证池。
        # 这可以捕获存在于外部存储中的凭证（如
        # Codex CLI 的 ~/.codex/auth.json），这些凭证由
        # _seed_from_singletons() 按需导入，但尚未在原始 auth.json 中。
        if not has_creds:
            try:
                from agent.credential_pool import load_pool
                pool = load_pool(hermes_slug)
                if pool.has_credentials():
                    has_creds = True
            except Exception as exc:
                logger.debug("Credential pool check failed for %s: %s", hermes_slug, exc)
        # 回退：直接检查外部凭证文件。
        # 凭证池会通过 is_provider_explicitly_configured() 来限制 anthropic，
        # 以防止辅助任务静默消耗 Claude Code 令牌（PR #4210）。
        # 但 /model 选择器是面向发现的 — 我们希望显示
        # 用户可以切换到的提供商，即使它们当前未配置。
        if not has_creds and hermes_slug == "anthropic":
            try:
                from agent.anthropic_adapter import (
                    read_claude_code_credentials,
                    read_hermes_oauth_credentials,
                )
                hermes_creds = read_hermes_oauth_credentials()
                cc_creds = read_claude_code_credentials()
                if (hermes_creds and hermes_creds.get("accessToken")) or \
                   (cc_creds and cc_creds.get("accessToken")):
                    has_creds = True
            except Exception as exc:
                logger.debug("Anthropic external creds check failed: %s", exc)
        if not has_creds:
            continue

        # 使用精选列表 — 先按 Hermes slug 查找，回退到 overlay 键
        model_ids = curated.get(hermes_slug, []) or curated.get(pid, [])
        total = len(model_ids)
        top = model_ids[:max_models]

        results.append({
            "slug": hermes_slug,
            "name": get_label(hermes_slug),
            "is_current": hermes_slug == current_provider or pid == current_provider,
            "is_user_defined": False,
            "models": top,
            "total_models": total,
            "source": "hermes",
        })
        seen_slugs.add(pid.lower())
        seen_slugs.add(hermes_slug.lower())

    # --- 2b. 交叉检查规范提供商列表 ---
    # 捕获在 CANONICAL_PROVIDERS 中但未在 PROVIDER_TO_MODELS_DEV
    # 或 HERMES_OVERLAYS 中找到的提供商（保持 /model 与
    # `hermes model` 同步）。
    try:
        from hermes_cli.models import CANONICAL_PROVIDERS as _canon_provs
    except ImportError:
        _canon_provs = []

    for _cp in _canon_provs:
        if _cp.slug.lower() in seen_slugs:
            continue

        # 通过 PROVIDER_REGISTRY (auth.py) 检查凭证
        _cp_config = _auth_registry.get(_cp.slug)
        _cp_has_creds = False
        if _cp_config and _cp_config.api_key_env_vars:
            _cp_has_creds = any(os.environ.get(ev) for ev in _cp_config.api_key_env_vars)
        # 也检查认证存储和凭证池
        if not _cp_has_creds:
            try:
                from hermes_cli.auth import _load_auth_store
                _cp_store = _load_auth_store()
                _cp_providers_store = _cp_store.get("providers", {})
                _cp_pool_store = _cp_store.get("credential_pool", {})
                if _cp_store and (
                    _cp.slug in _cp_providers_store
                    or _cp.slug in _cp_pool_store
                ):
                    _cp_has_creds = True
            except Exception:
                pass
        if not _cp_has_creds:
            try:
                from agent.credential_pool import load_pool
                _cp_pool = load_pool(_cp.slug)
                if _cp_pool.has_credentials():
                    _cp_has_creds = True
            except Exception:
                pass

        if not _cp_has_creds:
            continue

        _cp_model_ids = curated.get(_cp.slug, [])
        _cp_total = len(_cp_model_ids)
        _cp_top = _cp_model_ids[:max_models]

        results.append({
            "slug": _cp.slug,
            "name": _cp.label,
            "is_current": _cp.slug == current_provider,
            "is_user_defined": False,
            "models": _cp_top,
            "total_models": _cp_total,
            "source": "canonical",
        })
        seen_slugs.add(_cp.slug.lower())

    # --- 3. 配置中用户自定义的端点 ---
    if user_providers and isinstance(user_providers, dict):
        for ep_name, ep_cfg in user_providers.items():
            if not isinstance(ep_cfg, dict):
                continue
            display_name = ep_cfg.get("name", "") or ep_name
            api_url = ep_cfg.get("api", "") or ep_cfg.get("url", "") or ""
            default_model = ep_cfg.get("default_model", "")

            # 从 default_model 和完整 models 数组构建模型列表
            models_list = []
            if default_model:
                models_list.append(default_model)
            # 也包含配置中的完整模型列表
            cfg_models = ep_cfg.get("models", [])
            if isinstance(cfg_models, list):
                for m in cfg_models:
                    if m and m not in models_list:
                        models_list.append(m)

            # 如果设置了 URL，尝试探测 /v1/models（但不阻塞）
            # 目前只显示配置中已知的内容
            results.append({
                "slug": ep_name,
                "name": display_name,
                "is_current": ep_name == current_provider,
                "is_user_defined": True,
                "models": models_list,
                "total_models": len(models_list) if models_list else 0,
                "source": "user-config",
                "api_url": api_url,
            })

    # --- 4. 配置中保存的自定义提供商 ---
    # 每个 ``custom_providers`` 条目代表一个命名提供商下的一个模型。
    # 共享相同提供商名称的条目被分组到单个选择器行中，
    # 这样例如四个 Ollama Cloud 条目
    # (qwen3-coder, glm-5.1, kimi-k2, minimax-m2.7) 会显示为一个
    # "Ollama Cloud" 行，包含四个模型，而不是四个重复的
    # "Ollama Cloud" 行。具有不同提供商名称的条目
    # 仍然产生单独的行（如 Ollama Cloud vs Moonshot）。
    if custom_providers and isinstance(custom_providers, list):
        from collections import OrderedDict

        groups: "OrderedDict[str, dict]" = OrderedDict()
        for entry in custom_providers:
            if not isinstance(entry, dict):
                continue

            display_name = (entry.get("name") or "").strip()
            api_url = (
                entry.get("base_url", "")
                or entry.get("url", "")
                or entry.get("api", "")
                or ""
            ).strip()
            if not display_name or not api_url:
                continue

            slug = custom_provider_slug(display_name)
            if slug not in groups:
                groups[slug] = {
                    "name": display_name,
                    "api_url": api_url,
                    "models": [],
                }
            default_model = (entry.get("model") or "").strip()
            if default_model and default_model not in groups[slug]["models"]:
                groups[slug]["models"].append(default_model)

        for slug, grp in groups.items():
            if slug.lower() in seen_slugs:
                continue
            results.append({
                "slug": slug,
                "name": grp["name"],
                "is_current": slug == current_provider,
                "is_user_defined": True,
                "models": grp["models"],
                "total_models": len(grp["models"]),
                "source": "user-config",
                "api_url": grp["api_url"],
            })
            seen_slugs.add(slug.lower())

    # 排序：当前提供商优先，然后按模型数量降序
    results.sort(key=lambda r: (not r["is_current"], -r["total_models"]))

    return results
