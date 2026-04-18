"""按提供商进行的模型名称规范化。

不同的 LLM 提供商对模型标识符的格式要求各不相同：

- **聚合器**（OpenRouter、Nous、AI Gateway、Kilo Code）需要
  ``vendor/model`` 格式的 slug，如 ``anthropic/claude-sonnet-4.6``。
- **Anthropic** 原生 API 要求裸名称，点号替换为连字符：
  ``claude-sonnet-4-6``。
- **Copilot** 要求裸名称，但*保留*点号：
  ``claude-sonnet-4.6``。
- **OpenCode Zen** 对 GPT/GLM/Gemini/Kimi/MiniMax 风格的模型 ID 保留点号，
  但 Claude 仍使用连字符形式的原生名称如 ``claude-sonnet-4-6``。
- **OpenCode Go** 保留模型名称中的点号：``minimax-m2.7``。
- **DeepSeek** 只接受两个模型标识符：
  ``deepseek-chat`` 和 ``deepseek-reasoner``。
- **Custom** 及其他提供商直接透传名称。

本模块集中处理上述转换，调用者只需简单地编写::

    api_model = normalize_model_for_provider(user_input, provider)

灵感来源于 Clawdbot 的 ``normalizeAnthropicModelId`` 模式。
"""

from __future__ import annotations

from typing import Optional

# ---------------------------------------------------------------------------
# 厂商前缀映射
# ---------------------------------------------------------------------------
# 将裸模型名称的第一个连字符分隔的 token 映射到聚合器 API
# （OpenRouter、Nous 等）使用的厂商 slug。
#
# 示例: "claude-sonnet-4.6" -> 第一个 token "claude" -> 厂商 "anthropic"
#       -> 聚合器 slug: "anthropic/claude-sonnet-4.6"

_VENDOR_PREFIXES: dict[str, str] = {
    "claude": "anthropic",
    "gpt": "openai",
    "o1": "openai",
    "o3": "openai",
    "o4": "openai",
    "gemini": "google",
    "gemma": "google",
    "deepseek": "deepseek",
    "glm": "z-ai",
    "kimi": "moonshotai",
    "minimax": "minimax",
    "grok": "x-ai",
    "qwen": "qwen",
    "mimo": "xiaomi",
    "trinity": "arcee-ai",
    "nemotron": "nvidia",
    "llama": "meta-llama",
    "step": "stepfun",
    "trinity": "arcee-ai",
}

# API 消费 vendor/model 格式 slug 的提供商。
_AGGREGATOR_PROVIDERS: frozenset[str] = frozenset({
    "openrouter",
    "nous",
    "ai-gateway",
    "kilocode",
})

# 需要裸名称且点号替换为连字符的提供商。
_DOT_TO_HYPHEN_PROVIDERS: frozenset[str] = frozenset({
    "anthropic",
})

# 需要裸名称且保留点号的提供商。
_STRIP_VENDOR_ONLY_PROVIDERS: frozenset[str] = frozenset({
    "copilot",
    "copilot-acp",
    "openai-codex",
})

# 原生命名具有权威性的提供商——直接透传不做修改。
_AUTHORITATIVE_NATIVE_PROVIDERS: frozenset[str] = frozenset({
    "gemini",
    "huggingface",
})

# 接受裸原生名称的直连提供商，但当用户将聚合器格式的
# provider/ 前缀复制到 config.yaml 中时，应修复匹配的前缀。
_MATCHING_PREFIX_STRIP_PROVIDERS: frozenset[str] = frozenset({
    "zai",
    "kimi-coding",
    "kimi-coding-cn",
    "minimax",
    "minimax-cn",
    "alibaba",
    "qwen-oauth",
    "xiaomi",
    "arcee",
    "ollama-cloud",
    "custom",
})

# ---------------------------------------------------------------------------
# DeepSeek 特殊处理
# ---------------------------------------------------------------------------
# DeepSeek 的 API 只识别恰好两个模型标识符。我们将常见的别名
# 和模式映射到规范名称。

_DEEPSEEK_REASONER_KEYWORDS: frozenset[str] = frozenset({
    "reasoner",
    "r1",
    "think",
    "reasoning",
    "cot",
})

_DEEPSEEK_CANONICAL_MODELS: frozenset[str] = frozenset({
    "deepseek-chat",
    "deepseek-reasoner",
})


def _normalize_for_deepseek(model_name: str) -> str:
    """将任意模型输入映射到 DeepSeek 接受的两个标识符之一。

    规则:
    - 已经是 ``deepseek-chat`` 或 ``deepseek-reasoner`` -> 直接透传。
    - 包含任何推理关键词 (r1, think, reasoning, cot, reasoner)
      -> ``deepseek-reasoner``。
    - 其他所有情况 -> ``deepseek-chat``。

    Args:
        model_name: 裸模型名称（厂商前缀已移除）。

    Returns:
        ``"deepseek-chat"`` 或 ``"deepseek-reasoner"`` 之一。
    """
    bare = _strip_vendor_prefix(model_name).lower()

    if bare in _DEEPSEEK_CANONICAL_MODELS:
        return bare

    # 检查名称中是否包含任何推理相关的关键词
    for keyword in _DEEPSEEK_REASONER_KEYWORDS:
        if keyword in bare:
            return "deepseek-reasoner"

    return "deepseek-chat"


# ---------------------------------------------------------------------------
# 辅助工具函数
# ---------------------------------------------------------------------------

def _strip_vendor_prefix(model_name: str) -> str:
    """移除 ``vendor/`` 前缀（如果存在）。

    示例::

        >>> _strip_vendor_prefix("anthropic/claude-sonnet-4.6")
        'claude-sonnet-4.6'
        >>> _strip_vendor_prefix("claude-sonnet-4.6")
        'claude-sonnet-4.6'
        >>> _strip_vendor_prefix("meta-llama/llama-4-scout")
        'llama-4-scout'
    """
    if "/" in model_name:
        return model_name.split("/", 1)[1]
    return model_name


def _dots_to_hyphens(model_name: str) -> str:
    """将模型名称中的点号替换为连字符。

    Anthropic 的原生 API 使用连字符，而市场营销名称使用点号：
    ``claude-sonnet-4.6`` -> ``claude-sonnet-4-6``。
    """
    return model_name.replace(".", "-")


def _normalize_provider_alias(provider_name: str) -> str:
    """将提供商别名解析为 Hermes 的规范 ID。"""
    raw = (provider_name or "").strip().lower()
    if not raw:
        return raw
    try:
        from hermes_cli.models import normalize_provider

        return normalize_provider(raw)
    except Exception:
        return raw


def _strip_matching_provider_prefix(model_name: str, target_provider: str) -> str:
    """仅当前缀匹配目标提供商时，才移除 ``provider/`` 前缀。

    这样可以防止在原生提供商上对带斜杠的任意模型 ID 进行错误处理，
    同时仍能修复用户手动配置的值，如在 ``zai`` 提供商上使用
    ``zai/glm-5.1`` 的情况。
    """
    if "/" not in model_name:
        return model_name

    prefix, remainder = model_name.split("/", 1)
    if not prefix.strip() or not remainder.strip():
        return model_name

    normalized_prefix = _normalize_provider_alias(prefix)
    normalized_target = _normalize_provider_alias(target_provider)
    if normalized_prefix and normalized_prefix == normalized_target:
        return remainder.strip()
    return model_name


def detect_vendor(model_name: str) -> Optional[str]:
    """从裸模型名称中检测厂商 slug。

    使用模型名称的第一个连字符分隔 token 在 ``_VENDOR_PREFIXES``
    中查找对应的厂商。同时支持大小写不敏感匹配和特殊模式。

    Args:
        model_name: 模型名称，可选地已包含 ``vendor/`` 前缀。
            如果存在前缀，则直接使用。

    Returns:
        厂商 slug（如 ``"anthropic"``、``"openai"``），
        若无法可靠检测则返回 ``None``。

    示例::

        >>> detect_vendor("claude-sonnet-4.6")
        'anthropic'
        >>> detect_vendor("gpt-5.4-mini")
        'openai'
        >>> detect_vendor("anthropic/claude-sonnet-4.6")
        'anthropic'
        >>> detect_vendor("my-custom-model")
    """
    name = model_name.strip()
    if not name:
        return None

    # 如果已有 vendor/ 前缀，直接提取
    if "/" in name:
        return name.split("/", 1)[0].lower() or None

    name_lower = name.lower()

    # 尝试第一个连字符分隔的 token（精确匹配）
    first_token = name_lower.split("-")[0]
    if first_token in _VENDOR_PREFIXES:
        return _VENDOR_PREFIXES[first_token]

    # 处理第一个 token 包含版本数字的情况，
    # 例如 "qwen3.5-plus" -> 第一个 token "qwen3.5"，但前缀是 "qwen"
    for prefix, vendor in _VENDOR_PREFIXES.items():
        if name_lower.startswith(prefix):
            return vendor

    return None


def _prepend_vendor(model_name: str) -> str:
    """当缺少 ``vendor/`` 前缀时自动添加。

    用于需要 ``vendor/model`` 格式的聚合器提供商。
    如果名称已包含 ``/``，则原样返回。
    如果无法检测到厂商，则名称不变返回
    （聚合器可能仍会接受或返回错误）。

    示例::

        >>> _prepend_vendor("claude-sonnet-4.6")
        'anthropic/claude-sonnet-4.6'
        >>> _prepend_vendor("anthropic/claude-sonnet-4.6")
        'anthropic/claude-sonnet-4.6'
        >>> _prepend_vendor("my-custom-thing")
        'my-custom-thing'
    """
    if "/" in model_name:
        return model_name

    vendor = detect_vendor(model_name)
    if vendor:
        return f"{vendor}/{model_name}"
    return model_name


# ---------------------------------------------------------------------------
# 主规范化入口
# ---------------------------------------------------------------------------

def normalize_model_for_provider(model_input: str, target_provider: str) -> str:
    """将模型名称转换为目标提供商 API 所期望的格式。

    这是模型名称规范化的主入口。它接受任何用户侧的模型标识符，
    并将其转换为特定提供商接收 API 调用时所需的格式。

    Args:
        model_input: 用户或配置提供的模型名称。
            可以是裸名称（``"claude-sonnet-4.6"``）、带厂商前缀
            （``"anthropic/claude-sonnet-4.6"``）、或已是原生格式
            （``"claude-sonnet-4-6"``）。
        target_provider: Hermes 的规范提供商 ID，如
            ``"openrouter"``、``"anthropic"``、``"copilot"``、
            ``"deepseek"``、``"custom"``。应已通过
            ``hermes_cli.models.normalize_provider()`` 规范化。

    Returns:
        目标提供商 API 所期望的模型标识符字符串。

    Raises:
        不会抛出异常——始终返回尽力而为的字符串。

    示例::

        >>> normalize_model_for_provider("claude-sonnet-4.6", "openrouter")
        'anthropic/claude-sonnet-4.6'

        >>> normalize_model_for_provider("anthropic/claude-sonnet-4.6", "anthropic")
        'claude-sonnet-4-6'

        >>> normalize_model_for_provider("anthropic/claude-sonnet-4.6", "copilot")
        'claude-sonnet-4.6'

        >>> normalize_model_for_provider("openai/gpt-5.4", "copilot")
        'gpt-5.4'

        >>> normalize_model_for_provider("claude-sonnet-4.6", "opencode-zen")
        'claude-sonnet-4-6'

        >>> normalize_model_for_provider("minimax-m2.5-free", "opencode-zen")
        'minimax-m2.5-free'

        >>> normalize_model_for_provider("deepseek-v3", "deepseek")
        'deepseek-chat'

        >>> normalize_model_for_provider("deepseek-r1", "deepseek")
        'deepseek-reasoner'

        >>> normalize_model_for_provider("my-model", "custom")
        'my-model'

        >>> normalize_model_for_provider("claude-sonnet-4.6", "zai")
        'claude-sonnet-4.6'
    """
    name = (model_input or "").strip()
    if not name:
        return name

    provider = _normalize_provider_alias(target_provider)

    # --- 聚合器：需要 vendor/model 格式 ---
    if provider in _AGGREGATOR_PROVIDERS:
        return _prepend_vendor(name)

    # --- OpenCode Zen：Claude 保持连字符形式；其他模型保留点号 ---
    if provider == "opencode-zen":
        bare = _strip_matching_provider_prefix(name, provider)
        if "/" in bare:
            return bare
        if bare.lower().startswith("claude-"):
            return _dots_to_hyphens(bare)
        return bare

    # --- Anthropic：移除匹配的提供商前缀，点号转连字符 ---
    if provider in _DOT_TO_HYPHEN_PROVIDERS:
        bare = _strip_matching_provider_prefix(name, provider)
        if "/" in bare:
            return bare
        return _dots_to_hyphens(bare)

    # --- Copilot：移除匹配的提供商前缀，保留点号 ---
    if provider in _STRIP_VENDOR_ONLY_PROVIDERS:
        stripped = _strip_matching_provider_prefix(name, provider)
        if stripped == name and name.startswith("openai/"):
            # openai-codex 将 openai/gpt-5.4 映射为 gpt-5.4
            return name.split("/", 1)[1]
        return stripped

    # --- DeepSeek：映射到两个规范名称之一 ---
    if provider == "deepseek":
        bare = _strip_matching_provider_prefix(name, provider)
        if "/" in bare:
            return bare
        return _normalize_for_deepseek(bare)

    # --- 直连提供商：仅修复匹配的提供商前缀 ---
    if provider in _MATCHING_PREFIX_STRIP_PROVIDERS:
        return _strip_matching_provider_prefix(name, provider)

    # --- 权威原生提供商：原样保留用户侧 slug ---
    if provider in _AUTHORITATIVE_NATIVE_PROVIDERS:
        return name

    # --- Custom 及其他所有提供商：直接透传 ---
    return name


# ---------------------------------------------------------------------------
# 批量 / 便捷辅助函数
# ---------------------------------------------------------------------------

