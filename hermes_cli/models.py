"""
规范的模型目录和轻量级验证辅助工具。

在此处添加、删除或重新排序条目 —— ``hermes setup`` 和
``hermes`` 提供商选择都会自动获取更改。
"""

from __future__ import annotations

import json
import os
import urllib.request
import urllib.error
import time
from difflib import get_close_matches
from pathlib import Path
from typing import Any, NamedTuple, Optional

COPILOT_BASE_URL = "https://api.githubcopilot.com"
COPILOT_MODELS_URL = f"{COPILOT_BASE_URL}/models"
COPILOT_EDITOR_VERSION = "vscode/1.104.1"
COPILOT_REASONING_EFFORTS_GPT5 = ["minimal", "low", "medium", "high"]
COPILOT_REASONING_EFFORTS_O_SERIES = ["low", "medium", "high"]


# 当在线目录不可用时使用的 OpenRouter 回退快照。
# (模型 ID, 菜单中显示的描述)
OPENROUTER_MODELS: list[tuple[str, str]] = [
    ("anthropic/claude-opus-4.7",       "recommended"),
    ("anthropic/claude-opus-4.6",       ""),
    ("anthropic/claude-sonnet-4.6",     ""),
    ("qwen/qwen3.6-plus",               ""),
    ("anthropic/claude-sonnet-4.5",     ""),
    ("anthropic/claude-haiku-4.5",      ""),
    ("openrouter/elephant-alpha",       "free"),
    ("openai/gpt-5.4",                  ""),
    ("openai/gpt-5.4-mini",             ""),
    ("xiaomi/mimo-v2-pro",               ""),
    ("openai/gpt-5.3-codex",            ""),
    ("google/gemini-3-pro-image-preview", ""),
    ("google/gemini-3-flash-preview",   ""),
    ("google/gemini-3.1-pro-preview",     ""),
    ("google/gemini-3.1-flash-lite-preview",   ""),
    ("qwen/qwen3.5-plus-02-15",         ""),
    ("qwen/qwen3.5-35b-a3b",            ""),
    ("stepfun/step-3.5-flash",          ""),
    ("minimax/minimax-m2.7",            ""),
    ("minimax/minimax-m2.5",            ""),
    ("z-ai/glm-5.1",                    ""),
    ("z-ai/glm-5v-turbo",               ""),
    ("z-ai/glm-5-turbo",                ""),
    ("moonshotai/kimi-k2.5",            ""),
    ("x-ai/grok-4.20",                  ""),
    ("nvidia/nemotron-3-super-120b-a12b",      ""),
    ("nvidia/nemotron-3-super-120b-a12b:free", "free"),
    ("arcee-ai/trinity-large-preview:free", "free"),
    ("arcee-ai/trinity-large-thinking",  ""),
    ("openai/gpt-5.4-pro",              ""),
    ("openai/gpt-5.4-nano",             ""),
]

_openrouter_catalog_cache: list[tuple[str, str]] | None = None


def _codex_curated_models() -> list[str]:
    """从 codex_models.py 派生 openai-codex 的精选列表。

    单一事实来源：DEFAULT_CODEX_MODELS + 前向兼容合成。
    这使网关 /model 选择器与 CLI `hermes model` 流程保持同步，
    无需维护单独的静态列表。
    """
    from hermes_cli.codex_models import DEFAULT_CODEX_MODELS, _add_forward_compat_models
    return _add_forward_compat_models(list(DEFAULT_CODEX_MODELS))


_PROVIDER_MODELS: dict[str, list[str]] = {
    "nous": [
        "xiaomi/mimo-v2-pro",
        "anthropic/claude-opus-4.7",
        "anthropic/claude-opus-4.6",
        "anthropic/claude-sonnet-4.6",
        "anthropic/claude-sonnet-4.5",
        "anthropic/claude-haiku-4.5",
        "openai/gpt-5.4",
        "openai/gpt-5.4-mini",
        "openai/gpt-5.3-codex",
        "google/gemini-3-pro-preview",
        "google/gemini-3-flash-preview",
        "google/gemini-3.1-pro-preview",
        "google/gemini-3.1-flash-lite-preview",
        "qwen/qwen3.5-plus-02-15",
        "qwen/qwen3.5-35b-a3b",
        "stepfun/step-3.5-flash",
        "minimax/minimax-m2.7",
        "minimax/minimax-m2.5",
        "z-ai/glm-5.1",
        "z-ai/glm-5v-turbo",
        "z-ai/glm-5-turbo",
        "moonshotai/kimi-k2.5",
        "x-ai/grok-4.20-beta",
        "nvidia/nemotron-3-super-120b-a12b",
        "nvidia/nemotron-3-super-120b-a12b:free",
        "arcee-ai/trinity-large-preview:free",
        "arcee-ai/trinity-large-thinking",
        "openai/gpt-5.4-pro",
        "openai/gpt-5.4-nano",
        "openrouter/elephant-alpha",
    ],
    "openai-codex": _codex_curated_models(),
    "copilot-acp": [
        "copilot-acp",
    ],
    "copilot": [
        "gpt-5.4",
        "gpt-5.4-mini",
        "gpt-5-mini",
        "gpt-5.3-codex",
        "gpt-5.2-codex",
        "gpt-4.1",
        "gpt-4o",
        "gpt-4o-mini",
        "claude-opus-4.6",
        "claude-sonnet-4.6",
        "claude-sonnet-4.5",
        "claude-haiku-4.5",
        "gemini-2.5-pro",
        "grok-code-fast-1",
    ],
    "gemini": [
        "gemini-3.1-pro-preview",
        "gemini-3-flash-preview",
        "gemini-3.1-flash-lite-preview",
        "gemini-2.5-pro",
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
        # Gemma 开源模型（也通过 AI Studio 提供服务）
        "gemma-4-31b-it",
        "gemma-4-26b-it",
    ],
    "google-gemini-cli": [
        "gemini-2.5-pro",
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
    ],
    "zai": [
        "glm-5.1",
        "glm-5",
        "glm-5v-turbo",
        "glm-5-turbo",
        "glm-4.7",
        "glm-4.5",
        "glm-4.5-flash",
    ],
    "xai": [
        "grok-4.20-reasoning",
        "grok-4-1-fast-reasoning",
    ],
    "kimi-coding": [
        "kimi-for-coding",
        "kimi-k2.5",
        "kimi-k2-thinking",
        "kimi-k2-thinking-turbo",
        "kimi-k2-turbo-preview",
        "kimi-k2-0905-preview",
    ],
    "kimi-coding-cn": [
        "kimi-k2.5",
        "kimi-k2-thinking",
        "kimi-k2-turbo-preview",
        "kimi-k2-0905-preview",
    ],
    "moonshot": [
        "kimi-k2.5",
        "kimi-k2-thinking",
        "kimi-k2-turbo-preview",
        "kimi-k2-0905-preview",
    ],
    "minimax": [
        "MiniMax-M2.7",
        "MiniMax-M2.5",
        "MiniMax-M2.1",
        "MiniMax-M2",
    ],
    "minimax-cn": [
        "MiniMax-M2.7",
        "MiniMax-M2.5",
        "MiniMax-M2.1",
        "MiniMax-M2",
    ],
    "anthropic": [
        "claude-opus-4-7",
        "claude-opus-4-6",
        "claude-sonnet-4-6",
        "claude-opus-4-5-20251101",
        "claude-sonnet-4-5-20250929",
        "claude-opus-4-20250514",
        "claude-sonnet-4-20250514",
        "claude-haiku-4-5-20251001",
    ],
    "deepseek": [
        "deepseek-chat",
        "deepseek-reasoner",
    ],
    "xiaomi": [
        "mimo-v2-pro",
        "mimo-v2-omni",
        "mimo-v2-flash",
    ],
    "arcee": [
        "trinity-large-thinking",
        "trinity-large-preview",
        "trinity-mini",
    ],
    "opencode-zen": [
        "gpt-5.4-pro",
        "gpt-5.4",
        "gpt-5.3-codex",
        "gpt-5.3-codex-spark",
        "gpt-5.2",
        "gpt-5.2-codex",
        "gpt-5.1",
        "gpt-5.1-codex",
        "gpt-5.1-codex-max",
        "gpt-5.1-codex-mini",
        "gpt-5",
        "gpt-5-codex",
        "gpt-5-nano",
        "claude-opus-4-6",
        "claude-opus-4-5",
        "claude-opus-4-1",
        "claude-sonnet-4-6",
        "claude-sonnet-4-5",
        "claude-sonnet-4",
        "claude-haiku-4-5",
        "claude-3-5-haiku",
        "gemini-3.1-pro",
        "gemini-3-pro",
        "gemini-3-flash",
        "minimax-m2.7",
        "minimax-m2.5",
        "minimax-m2.5-free",
        "minimax-m2.1",
        "glm-5",
        "glm-4.7",
        "glm-4.6",
        "kimi-k2.5",
        "kimi-k2-thinking",
        "kimi-k2",
        "qwen3-coder",
        "big-pickle",
    ],
    "opencode-go": [
        "glm-5.1",
        "glm-5",
        "kimi-k2.5",
        "mimo-v2-pro",
        "mimo-v2-omni",
        "minimax-m2.7",
        "minimax-m2.5",
    ],
    "ai-gateway": [
        "anthropic/claude-opus-4.6",
        "anthropic/claude-sonnet-4.6",
        "anthropic/claude-sonnet-4.5",
        "anthropic/claude-haiku-4.5",
        "openai/gpt-5",
        "openai/gpt-4.1",
        "openai/gpt-4.1-mini",
        "google/gemini-3-pro-preview",
        "google/gemini-3-flash",
        "google/gemini-2.5-pro",
        "google/gemini-2.5-flash",
        "deepseek/deepseek-v3.2",
    ],
    "kilocode": [
        "anthropic/claude-opus-4.6",
        "anthropic/claude-sonnet-4.6",
        "openai/gpt-5.4",
        "google/gemini-3-pro-preview",
        "google/gemini-3-flash-preview",
    ],
    # 阿里巴巴 DashScope 编码平台 (coding-intl) — 默认端点。
    # 支持 Qwen 模型 + 第三方提供商（GLM、Kimi、MiniMax）。
    # 使用传统 DashScope 密钥的用户应将 DASHSCOPE_BASE_URL 覆盖为
    # https://dashscope-intl.aliyuncs.com/compatible-mode/v1（OpenAI 兼容）
    # 或 https://dashscope-intl.aliyuncs.com/apps/anthropic（Anthropic 兼容）。
    "alibaba": [
        "qwen3.5-plus",
        "qwen3-coder-plus",
        "qwen3-coder-next",
        # 编码国际版上可用的第三方模型
        "glm-5",
        "glm-4.7",
        "kimi-k2.5",
        "MiniMax-M2.5",
    ],
    # 精选的 HF 模型列表 — 仅包含映射到 OpenRouter 默认值的智能体模型。
    "huggingface": [
        "Qwen/Qwen3.5-397B-A17B",
        "Qwen/Qwen3.5-35B-A3B",
        "deepseek-ai/DeepSeek-V3.2",
        "moonshotai/Kimi-K2.5",
        "MiniMaxAI/MiniMax-M2.5",
        "zai-org/GLM-5",
        "XiaomiMiMo/MiMo-V2-Flash",
        "moonshotai/Kimi-K2-Thinking",
    ],
    # AWS Bedrock — 当动态发现不可用时（无 boto3、无凭证或 API 错误）使用的静态回退列表。
    # 智能体优先通过 ListFoundationModels + ListInferenceProfiles 进行实时发现。
    # 使用推理配置文件 ID（us.*），因为大多数模型需要它们。
    "bedrock": [
        "us.anthropic.claude-sonnet-4-6",
        "us.anthropic.claude-opus-4-6-v1",
        "us.anthropic.claude-haiku-4-5-20251001-v1:0",
        "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
        "us.amazon.nova-pro-v1:0",
        "us.amazon.nova-lite-v1:0",
        "us.amazon.nova-micro-v1:0",
        "deepseek.v3.2",
        "us.meta.llama4-maverick-17b-instruct-v1:0",
        "us.meta.llama4-scout-17b-instruct-v1:0",
    ],
}

# ---------------------------------------------------------------------------
# Nous Portal 免费模型过滤
# ---------------------------------------------------------------------------
# 在 Nous Portal 上标记为免费时，允许出现的模型列表。
# 其他任何免费模型都会被隐藏 — 防止促销/临时免费模型
# 在用户是付费订阅者时干扰选择界面。
# 此列表中的模型如果不是免费的也会被过滤掉（即它们只应
# 在真正免费时出现在菜单中）。
_NOUS_ALLOWED_FREE_MODELS: frozenset[str] = frozenset({
    "xiaomi/mimo-v2-pro",
    "xiaomi/mimo-v2-omni",
})


def _is_model_free(model_id: str, pricing: dict[str, dict[str, str]]) -> bool:
    """当 *model_id* 的提示和补全定价均为零时返回 True。"""
    p = pricing.get(model_id)
    if not p:
        return False
    try:
        return float(p.get("prompt", "1")) == 0 and float(p.get("completion", "1")) == 0
    except (TypeError, ValueError):
        return False


def filter_nous_free_models(
    model_ids: list[str],
    pricing: dict[str, dict[str, str]],
) -> list[str]:
    """根据免费模型策略过滤 Nous Portal 模型列表。

    规则：
      * 不在允许列表中的付费模型 -> 保留（正常情况）。
      * 不在允许列表中的免费模型 -> 移除。
      * 在允许列表中且确实免费的模型 -> 保留。
      * 在允许列表中但不免费的模型 -> 移除。
    """
    if not pricing:
        return model_ids  # 无定价数据——无法过滤，显示全部

    result: list[str] = []
    for mid in model_ids:
        free = _is_model_free(mid, pricing)
        if mid in _NOUS_ALLOWED_FREE_MODELS:
            # 允许列表中的模型：仅在实际免费时显示
            if free:
                result.append(mid)
        else:
            # 常规模型：仅在非免费时保留
            if not free:
                result.append(mid)
    return result


# ---------------------------------------------------------------------------
# Nous Portal 账户等级检测
# ---------------------------------------------------------------------------

def fetch_nous_account_tier(access_token: str, portal_base_url: str = "") -> dict[str, Any]:
    """获取用户的 Nous Portal 账户/订阅信息。

    使用 OAuth 访问令牌调用 ``<portal>/api/oauth/account``。

    成功时返回解析后的 JSON 字典，例如::

        {
            "subscription": {
                "plan": "Plus",
                "tier": 2,
                "monthly_charge": 20,
                "credits_remaining": 1686.60,
                ...
            },
            ...
        }

    任何失败情况（网络、认证、解析）均返回空字典。
    """
    base = (portal_base_url or "https://portal.nousresearch.com").rstrip("/")
    url = f"{base}/api/oauth/account"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=8) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return {}


def is_nous_free_tier(account_info: dict[str, Any]) -> bool:
    """当账户信息表明是免费（未付费）等级时返回 True。

    检查 ``subscription.monthly_charge == 0``。当字段缺失
    或无法解析时返回 False（假定为付费——不阻止用户）。
    """
    sub = account_info.get("subscription")
    if not isinstance(sub, dict):
        return False
    charge = sub.get("monthly_charge")
    if charge is None:
        return False
    try:
        return float(charge) == 0
    except (TypeError, ValueError):
        return False


def partition_nous_models_by_tier(
    model_ids: list[str],
    pricing: dict[str, dict[str, str]],
    free_tier: bool,
) -> tuple[list[str], list[str]]:
    """根据用户等级将 Nous 模型拆分为（可选的, 不可用的）。

    付费等级用户：所有模型均可选，无不可用模型
    （免费模型过滤由 ``filter_nous_free_models`` 单独处理）。

    免费等级用户：仅免费模型可选；付费模型
    作为不可用返回（在菜单中灰显）。
    """
    if not free_tier:
        return (model_ids, [])

    if not pricing:
        return (model_ids, [])  # 无法判断，显示全部

    selectable: list[str] = []
    unavailable: list[str] = []
    for mid in model_ids:
        if _is_model_free(mid, pricing):
            selectable.append(mid)
        else:
            unavailable.append(mid)
    return (selectable, unavailable)


# ---------------------------------------------------------------------------
# 免费等级检测的 TTL 缓存 — 避免在同一会话中重复 API 调用，
# 同时仍能快速获取升级信息。
# ---------------------------------------------------------------------------
_FREE_TIER_CACHE_TTL: int = 180  # 秒（3 分钟）
_free_tier_cache: tuple[bool, float] | None = None  # (结果, 时间戳)


def check_nous_free_tier() -> bool:
    """检查当前 Nous Portal 用户是否为免费（未付费）等级。

    结果缓存 ``_FREE_TIER_CACHE_TTL`` 秒，以避免每次调用都请求
    Portal API。缓存生命周期较短，以便账户升级能在几分钟内反映。

    任何错误时返回 False（假定为付费）——绝不阻止付费用户。
    """
    global _free_tier_cache
    import time

    now = time.monotonic()
    if _free_tier_cache is not None:
        cached_result, cached_at = _free_tier_cache
        if now - cached_at < _FREE_TIER_CACHE_TTL:
            return cached_result

    try:
        from hermes_cli.auth import get_provider_auth_state, resolve_nous_runtime_credentials

        # 确保有新鲜的令牌（如需要则触发刷新）
        resolve_nous_runtime_credentials(min_key_ttl_seconds=60)

        state = get_provider_auth_state("nous")
        if not state:
            _free_tier_cache = (False, now)
            return False
        access_token = state.get("access_token", "")
        portal_url = state.get("portal_base_url", "")
        if not access_token:
            _free_tier_cache = (False, now)
            return False

        account_info = fetch_nous_account_tier(access_token, portal_url)
        result = is_nous_free_tier(account_info)
        _free_tier_cache = (result, now)
        return result
    except Exception:
        _free_tier_cache = (False, now)
        return False  # 出错时默认为付费——不阻止用户


# ---------------------------------------------------------------------------
# 规范提供商列表 — 提供商身份的单一事实来源。
# 每个列出、显示或遍历提供商的代码路径都来源于此列表：
# hermes model、/model、/provider、list_authenticated_providers。
#
# 字段：
#   slug        — 内部提供商 ID（用于 config.yaml、--provider 标志）
#   label       — 简短的显示名称
#   tui_desc    — `hermes model` 交互式选择器的详细描述
# ---------------------------------------------------------------------------

class ProviderEntry(NamedTuple):
    slug: str
    label: str
    tui_desc: str   # `hermes model` TUI 的详细描述


CANONICAL_PROVIDERS: list[ProviderEntry] = [
    ProviderEntry("nous",           "Nous Portal",              "Nous Portal (Nous Research subscription)"),
    ProviderEntry("openrouter",     "OpenRouter",               "OpenRouter (100+ models, pay-per-use)"),
    ProviderEntry("anthropic",      "Anthropic",                "Anthropic (Claude models — API key or Claude Code)"),
    ProviderEntry("openai-codex",   "OpenAI Codex",             "OpenAI Codex"),
    ProviderEntry("xiaomi",         "Xiaomi MiMo",              "Xiaomi MiMo (MiMo-V2 models — pro, omni, flash)"),
    ProviderEntry("qwen-oauth",     "Qwen OAuth (Portal)",      "Qwen OAuth (reuses local Qwen CLI login)"),
    ProviderEntry("copilot",        "GitHub Copilot",           "GitHub Copilot (uses GITHUB_TOKEN or gh auth token)"),
    ProviderEntry("copilot-acp",    "GitHub Copilot ACP",       "GitHub Copilot ACP (spawns `copilot --acp --stdio`)"),
    ProviderEntry("huggingface",    "Hugging Face",             "Hugging Face Inference Providers (20+ open models)"),
    ProviderEntry("gemini",         "Google AI Studio",         "Google AI Studio (Gemini models — OpenAI-compatible endpoint)"),
    ProviderEntry("google-gemini-cli", "Google Gemini (OAuth)",   "Google Gemini via OAuth + Code Assist (free tier supported; no API key needed)"),
    ProviderEntry("deepseek",       "DeepSeek",                 "DeepSeek (DeepSeek-V3, R1, coder — direct API)"),
    ProviderEntry("xai",            "xAI",                      "xAI (Grok models — direct API)"),
    ProviderEntry("zai",            "Z.AI / GLM",               "Z.AI / GLM (Zhipu AI direct API)"),
    ProviderEntry("kimi-coding",    "Kimi / Kimi Coding Plan",  "Kimi Coding Plan (api.kimi.com) & Moonshot API"),
    ProviderEntry("kimi-coding-cn", "Kimi / Moonshot (China)",  "Kimi / Moonshot China (Moonshot CN direct API)"),
    ProviderEntry("minimax",        "MiniMax",                  "MiniMax (global direct API)"),
    ProviderEntry("minimax-cn",     "MiniMax (China)",          "MiniMax China (domestic direct API)"),
    ProviderEntry("alibaba",        "Alibaba Cloud (DashScope)","Alibaba Cloud / DashScope Coding (Qwen + multi-provider)"),
    ProviderEntry("ollama-cloud",   "Ollama Cloud",             "Ollama Cloud (cloud-hosted open models — ollama.com)"),
    ProviderEntry("arcee",          "Arcee AI",                 "Arcee AI (Trinity models — direct API)"),
    ProviderEntry("kilocode",       "Kilo Code",                "Kilo Code (Kilo Gateway API)"),
    ProviderEntry("opencode-zen",   "OpenCode Zen",             "OpenCode Zen (35+ curated models, pay-as-you-go)"),
    ProviderEntry("opencode-go",    "OpenCode Go",              "OpenCode Go (open models, $10/month subscription)"),
    ProviderEntry("ai-gateway",     "Vercel AI Gateway",        "Vercel AI Gateway (200+ models, pay-per-use)"),
    ProviderEntry("bedrock",        "AWS Bedrock",              "AWS Bedrock (Claude, Nova, Llama, DeepSeek — IAM or API key)"),
]

# 派生字典 — 在整个代码库中使用
_PROVIDER_LABELS = {p.slug: p.label for p in CANONICAL_PROVIDERS}
_PROVIDER_LABELS["custom"] = "Custom endpoint"  # 特殊情况：不是命名提供商


_PROVIDER_ALIASES = {
    "glm": "zai",
    "z-ai": "zai",
    "z.ai": "zai",
    "zhipu": "zai",
    "github": "copilot",
    "github-copilot": "copilot",
    "github-models": "copilot",
    "github-model": "copilot",
    "github-copilot-acp": "copilot-acp",
    "copilot-acp-agent": "copilot-acp",
    "google": "gemini",
    "google-gemini": "gemini",
    "google-ai-studio": "gemini",
    "kimi": "kimi-coding",
    "moonshot": "kimi-coding",
    "kimi-cn": "kimi-coding-cn",
    "moonshot-cn": "kimi-coding-cn",
    "arcee-ai": "arcee",
    "arceeai": "arcee",
    "minimax-china": "minimax-cn",
    "minimax_cn": "minimax-cn",
    "claude": "anthropic",
    "claude-code": "anthropic",
    "deep-seek": "deepseek",
    "opencode": "opencode-zen",
    "zen": "opencode-zen",
    "go": "opencode-go",
    "opencode-go-sub": "opencode-go",
    "aigateway": "ai-gateway",
    "vercel": "ai-gateway",
    "vercel-ai-gateway": "ai-gateway",
    "kilo": "kilocode",
    "kilo-code": "kilocode",
    "kilo-gateway": "kilocode",
    "dashscope": "alibaba",
    "aliyun": "alibaba",
    "qwen": "alibaba",
    "alibaba-cloud": "alibaba",
    "qwen-portal": "qwen-oauth",
    "gemini-cli": "google-gemini-cli",
    "gemini-oauth": "google-gemini-cli",
    "hf": "huggingface",
    "hugging-face": "huggingface",
    "huggingface-hub": "huggingface",
    "mimo": "xiaomi",
    "xiaomi-mimo": "xiaomi",
    "aws": "bedrock",
    "aws-bedrock": "bedrock",
    "amazon-bedrock": "bedrock",
    "amazon": "bedrock",
    "grok": "xai",
    "x-ai": "xai",
    "x.ai": "xai",
    "ollama": "custom",  # 裸 "ollama" = 本地；云端请用 "ollama-cloud"
    "ollama_cloud": "ollama-cloud",
}


def get_default_model_for_provider(provider: str) -> str:
    """返回提供商的默认模型，未知时返回空字符串。

    使用 _PROVIDER_MODELS 中的第一个条目作为默认值。这是
    用户在 ``hermes model`` 选择器中首先看到的模型。

    当用户配置了提供商但从未选择模型时用作回退
    （例如 ``hermes auth add openai-codex`` 但未执行 ``hermes model``）。
    """
    models = _PROVIDER_MODELS.get(provider, [])
    return models[0] if models else ""


def _openrouter_model_is_free(pricing: Any) -> bool:
    """当提示和补全定价均为零时返回 True。"""
    if not isinstance(pricing, dict):
        return False
    try:
        return float(pricing.get("prompt", "0")) == 0 and float(pricing.get("completion", "0")) == 0
    except (TypeError, ValueError):
        return False


def fetch_openrouter_models(
    timeout: float = 8.0,
    *,
    force_refresh: bool = False,
) -> list[tuple[str, str]]:
    """返回精选的 OpenRouter 选择列表，尽可能从在线目录刷新。"""
    global _openrouter_catalog_cache

    if _openrouter_catalog_cache is not None and not force_refresh:
        return list(_openrouter_catalog_cache)

    fallback = list(OPENROUTER_MODELS)
    preferred_ids = [mid for mid, _ in fallback]

    try:
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/models",
            headers={"Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode())
    except Exception:
        return list(_openrouter_catalog_cache or fallback)

    live_items = payload.get("data", [])
    if not isinstance(live_items, list):
        return list(_openrouter_catalog_cache or fallback)

    live_by_id: dict[str, dict[str, Any]] = {}
    for item in live_items:
        if not isinstance(item, dict):
            continue
        mid = str(item.get("id") or "").strip()
        if not mid:
            continue
        live_by_id[mid] = item

    curated: list[tuple[str, str]] = []
    for preferred_id in preferred_ids:
        live_item = live_by_id.get(preferred_id)
        if live_item is None:
            continue
        desc = "free" if _openrouter_model_is_free(live_item.get("pricing")) else ""
        curated.append((preferred_id, desc))

    if not curated:
        return list(_openrouter_catalog_cache or fallback)

    first_id, _ = curated[0]
    curated[0] = (first_id, "recommended")
    _openrouter_catalog_cache = curated
    return list(curated)


def model_ids(*, force_refresh: bool = False) -> list[str]:
    """仅返回 OpenRouter 模型 ID 字符串。"""
    return [mid for mid, _ in fetch_openrouter_models(force_refresh=force_refresh)]




# ---------------------------------------------------------------------------
# 定价辅助工具 — 从 OpenRouter 兼容的 /v1/models 获取实时定价
# ---------------------------------------------------------------------------

# 缓存：按端点将 model_id 映射为 {"prompt": str, "completion": str}
_pricing_cache: dict[str, dict[str, dict[str, str]]] = {}


def _format_price_per_mtok(per_token_str: str) -> str:
    """将每 token 价格字符串转换为人类友好的 $/Mtok 字符串。

    始终使用 2 位小数，以便在列中右对齐时价格垂直对齐
    （小数点保持在同一位置）。

    示例:
        "0.000003"   -> "$3.00"      (每百万 token)
        "0.00003"    -> "$30.00"
        "0.00000015" -> "$0.15"
        "0.0000001"  -> "$0.10"
        "0.00018"    -> "$180.00"
        "0"          -> "free"
    """
    try:
        val = float(per_token_str)
    except (TypeError, ValueError):
        return "?"
    if val == 0:
        return "free"
    per_m = val * 1_000_000
    return f"${per_m:.2f}"


def format_model_pricing_table(
    models: list[tuple[str, str]],
    pricing_map: dict[str, dict[str, str]],
    current_model: str = "",
    indent: str = "      ",
) -> list[str]:
    """构建列对齐的模型+定价表，用于终端显示。

    返回预格式化的行列表，可直接打印。
    *models* 格式为 ``[(model_id, description), ...]``。
    """
    if not models:
        return []

    # 构建行: (model_id, input_price, output_price, cache_price, is_current)
    rows: list[tuple[str, str, str, str, bool]] = []
    has_cache = False
    for mid, _desc in models:
        is_cur = mid == current_model
        p = pricing_map.get(mid)
        if p:
            inp = _format_price_per_mtok(p.get("prompt", ""))
            out = _format_price_per_mtok(p.get("completion", ""))
            cache_read = p.get("input_cache_read", "")
            cache = _format_price_per_mtok(cache_read) if cache_read else ""
            if cache:
                has_cache = True
        else:
            inp, out, cache = "", "", ""
        rows.append((mid, inp, out, cache, is_cur))

    name_col = max(len(r[0]) for r in rows) + 2
    # 根据实际数据计算价格列宽度，以便小数点对齐
    price_col = max(
        max((len(r[1]) for r in rows if r[1]), default=4),
        max((len(r[2]) for r in rows if r[2]), default=4),
        3,  # 最小值: "In" / "Out" 表头
    )
    cache_col = max(
        max((len(r[3]) for r in rows if r[3]), default=4),
        5,  # 最小值: "Cache" 表头
    ) if has_cache else 0
    lines: list[str] = []

    # 表头
    if has_cache:
        lines.append(f"{indent}{'Model':<{name_col}} {'In':>{price_col}}  {'Out':>{price_col}}  {'Cache':>{cache_col}}  /Mtok")
        lines.append(f"{indent}{'-' * name_col} {'-' * price_col}  {'-' * price_col}  {'-' * cache_col}")
    else:
        lines.append(f"{indent}{'Model':<{name_col}} {'In':>{price_col}}  {'Out':>{price_col}}  /Mtok")
        lines.append(f"{indent}{'-' * name_col} {'-' * price_col}  {'-' * price_col}")

    for mid, inp, out, cache, is_cur in rows:
        marker = "  ← current" if is_cur else ""
        if has_cache:
            lines.append(f"{indent}{mid:<{name_col}} {inp:>{price_col}}  {out:>{price_col}}  {cache:>{cache_col}}{marker}")
        else:
            lines.append(f"{indent}{mid:<{name_col}} {inp:>{price_col}}  {out:>{price_col}}{marker}")

    return lines


def fetch_models_with_pricing(
    api_key: str | None = None,
    base_url: str = "https://openrouter.ai/api",
    timeout: float = 8.0,
    *,
    force_refresh: bool = False,
) -> dict[str, dict[str, str]]:
    """获取 ``/v1/models`` 并返回 ``{model_id: {prompt, completion}}`` 定价。

    结果按 *base_url* 缓存，因此重复调用无开销。
    适用于任何 OpenRouter 兼容端点（OpenRouter、Nous Portal）。
    """
    cache_key = (base_url or "").rstrip("/")
    if not force_refresh and cache_key in _pricing_cache:
        return _pricing_cache[cache_key]

    url = cache_key.rstrip("/") + "/v1/models"
    headers: dict[str, str] = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode())
    except Exception:
        _pricing_cache[cache_key] = {}
        return {}

    result: dict[str, dict[str, str]] = {}
    for item in payload.get("data", []):
        mid = item.get("id")
        pricing = item.get("pricing")
        if mid and isinstance(pricing, dict):
            entry: dict[str, str] = {
                "prompt": str(pricing.get("prompt", "")),
                "completion": str(pricing.get("completion", "")),
            }
            if pricing.get("input_cache_read"):
                entry["input_cache_read"] = str(pricing["input_cache_read"])
            if pricing.get("input_cache_write"):
                entry["input_cache_write"] = str(pricing["input_cache_write"])
            result[mid] = entry

    _pricing_cache[cache_key] = result
    return result


def _resolve_openrouter_api_key() -> str:
    """尽力获取用于定价查询的 OpenRouter API 密钥。"""
    return os.getenv("OPENROUTER_API_KEY", "").strip()


def _resolve_nous_pricing_credentials() -> tuple[str, str]:
    """返回 Nous Portal 定价的 ``(api_key, base_url)``，或空字符串。"""
    try:
        from hermes_cli.auth import resolve_nous_runtime_credentials
        creds = resolve_nous_runtime_credentials()
        if creds:
            return (creds.get("api_key", ""), creds.get("base_url", ""))
    except Exception:
        pass
    return ("", "")


def get_pricing_for_provider(provider: str, *, force_refresh: bool = False) -> dict[str, dict[str, str]]:
    """返回支持定价的提供商（openrouter、nous）的实时定价。"""
    normalized = normalize_provider(provider)
    if normalized == "openrouter":
        return fetch_models_with_pricing(
            api_key=_resolve_openrouter_api_key(),
            base_url="https://openrouter.ai/api",
            force_refresh=force_refresh,
        )
    if normalized == "nous":
        api_key, base_url = _resolve_nous_pricing_credentials()
        if base_url:
            # Nous 的 base_url 通常形如 https://inference-api.nousresearch.com/v1
            # 我们的获取函数需要 /v1 之前的部分
            stripped = base_url.rstrip("/")
            if stripped.endswith("/v1"):
                stripped = stripped[:-3]
            return fetch_models_with_pricing(
                api_key=api_key,
                base_url=stripped,
                force_refresh=force_refresh,
            )
    return {}


# 所有对 provider:model 语法有效的提供商 ID 和别名。
_KNOWN_PROVIDER_NAMES: set[str] = (
    set(_PROVIDER_LABELS.keys())
    | set(_PROVIDER_ALIASES.keys())
    | {"openrouter", "custom"}
)


def list_available_providers() -> list[dict[str, str]]:
    """返回用户可通过 ``provider:model`` 使用的所有提供商信息。

    每个字典包含 ``id``、``label`` 和 ``aliases``。
    检查哪些提供商已配置有效凭证。

    提供商列表来源于 :data:`CANONICAL_PROVIDERS`（与
    ``hermes model``、``/model`` 等共享的单一事实来源）。
    """
    # 从规范列表 + custom 派生显示顺序
    provider_order = [p.slug for p in CANONICAL_PROVIDERS] + ["custom"]

    # 构建反向别名映射
    aliases_for: dict[str, list[str]] = {}
    for alias, canonical in _PROVIDER_ALIASES.items():
        aliases_for.setdefault(canonical, []).append(alias)

    result = []
    for pid in provider_order:
        label = _PROVIDER_LABELS.get(pid, pid)
        alias_list = aliases_for.get(pid, [])
        # 检查此提供商是否有可用凭证
        has_creds = False
        try:
            from hermes_cli.auth import get_auth_status, has_usable_secret
            if pid == "custom":
                custom_base_url = _get_custom_base_url() or ""
                has_creds = bool(custom_base_url.strip())
            elif pid == "openrouter":
                has_creds = has_usable_secret(os.getenv("OPENROUTER_API_KEY", ""))
            else:
                status = get_auth_status(pid)
                has_creds = bool(status.get("logged_in") or status.get("configured"))
        except Exception:
            pass
        result.append({
            "id": pid,
            "label": label,
            "aliases": alias_list,
            "authenticated": has_creds,
        })
    return result


def parse_model_input(raw: str, current_provider: str) -> tuple[str, str]:
    """解析 ``/model`` 输入为 ``(provider, model)``。

    支持 ``provider:model`` 语法在运行时切换提供商::

        openrouter:anthropic/claude-sonnet-4.5  ->  ("openrouter", "anthropic/claude-sonnet-4.5")
        nous:hermes-3                           ->  ("nous", "hermes-3")
        anthropic/claude-sonnet-4.5             ->  (current_provider, "anthropic/claude-sonnet-4.5")
        gpt-5.4                                 ->  (current_provider, "gpt-5.4")

    冒号仅在左侧是已识别的提供商名称或别名时才被视为提供商分隔符。
    这避免了误解释碰巧包含冒号的模型名称
    （如 ``anthropic/claude-3.5-sonnet:beta``）。

    返回 ``(provider, model)``，其中 *provider* 是输入中的显式提供商，
    如果未指定则为 *current_provider*。
    """
    stripped = raw.strip()
    colon = stripped.find(":")
    if colon > 0:
        provider_part = stripped[:colon].strip().lower()
        model_part = stripped[colon + 1:].strip()
        if provider_part and model_part and provider_part in _KNOWN_PROVIDER_NAMES:
            # 支持 custom:name:model 三段式语法用于命名的自定义
            # 提供商。``custom:local:qwen`` -> ("custom:local", "qwen")。
            # 单冒号 ``custom:qwen`` -> ("custom", "qwen") 保持不变。
            if provider_part == "custom" and ":" in model_part:
                second_colon = model_part.find(":")
                custom_name = model_part[:second_colon].strip()
                actual_model = model_part[second_colon + 1:].strip()
                if custom_name and actual_model:
                    return (f"custom:{custom_name}", actual_model)
            return (normalize_provider(provider_part), model_part)
    return (current_provider, stripped)


def _get_custom_base_url() -> str:
    """从 config.yaml 获取自定义端点的 base_url。"""
    try:
        from hermes_cli.config import load_config
        config = load_config()
        model_cfg = config.get("model", {})
        if isinstance(model_cfg, dict):
            return str(model_cfg.get("base_url", "")).strip()
    except Exception:
        pass
    return ""


def curated_models_for_provider(
    provider: Optional[str],
    *,
    force_refresh: bool = False,
) -> list[tuple[str, str]]:
    """返回提供商模型列表的 ``(model_id, description)`` 元组。

    首先尝试从提供商的 API 获取在线模型列表，
    如果 API 不可达则回退到静态 ``_PROVIDER_MODELS`` 目录。
    """
    normalized = normalize_provider(provider)
    if normalized == "openrouter":
        return fetch_openrouter_models(force_refresh=force_refresh)

    # 先尝试在线 API（Codex、Nous 等都支持 /models）
    live = provider_model_ids(normalized)
    if live:
        return [(m, "") for m in live]

    # 回退到静态目录
    models = _PROVIDER_MODELS.get(normalized, [])
    return [(m, "") for m in models]


def detect_provider_for_model(
    model_name: str,
    current_provider: str,
) -> Optional[tuple[str, str]]:
    """自动检测模型名称的最佳提供商。

    返回 ``(provider_id, model_name)`` — 模型名称可能被重新映射
    （如裸名 ``deepseek-chat`` -> OpenRouter 的 ``deepseek/deepseek-chat``）。
    无法可靠匹配时返回 ``None``。

    优先级：
    0. 裸提供商名称 -> 切换到该提供商的默认模型
    1. 有凭证的直连提供商（最高优先级）
    2. 无凭证的直连提供商 -> 重新映射到 OpenRouter slug
    3. OpenRouter 目录匹配
    """
    name = (model_name or "").strip()
    if not name:
        return None

    name_lower = name.lower()

    # --- 步骤 0：裸提供商名称作为模型输入 ---
    # 如果用户输入 `/model nous` 或 `/model anthropic`，将其视为
    # 提供商切换并选择该提供商目录的第一个模型。
    # 跳过 "custom" 和 "openrouter" — custom 没有模型目录，
    # openrouter 需要显式的模型名称才有意义。
    resolved_provider = _PROVIDER_ALIASES.get(name_lower, name_lower)
    if resolved_provider not in {"custom", "openrouter"}:
        default_models = _PROVIDER_MODELS.get(resolved_provider, [])
        if (
            resolved_provider in _PROVIDER_LABELS
            and default_models
            and resolved_provider != normalize_provider(current_provider)
        ):
            return (resolved_provider, default_models[0])

    # 聚合器列出其他提供商的模型——永远不要自动切换到它们
    _AGGREGATORS = {"nous", "openrouter", "ai-gateway", "copilot", "kilocode"}

    # 如果模型属于当前提供商的目录，不建议切换
    current_models = _PROVIDER_MODELS.get(current_provider, [])
    if any(name_lower == m.lower() for m in current_models):
        return None

    # --- 步骤 1：在静态提供商目录中检查直接匹配 ---
    direct_match: Optional[str] = None
    for pid, models in _PROVIDER_MODELS.items():
        if pid == current_provider or pid in _AGGREGATORS:
            continue
        if any(name_lower == m.lower() for m in models):
            direct_match = pid
            break

    if direct_match:
        # 检查是否有此提供商的凭证——环境变量、
        # 凭证池或认证存储条目。
        has_creds = False
        try:
            from hermes_cli.auth import PROVIDER_REGISTRY
            pconfig = PROVIDER_REGISTRY.get(direct_match)
            if pconfig:
                import os
                for env_var in pconfig.api_key_env_vars:
                    if os.getenv(env_var, "").strip():
                        has_creds = True
                        break
        except Exception:
            pass
        # 还要检查凭证池和认证存储——涵盖 OAuth、
        # Claude Code 令牌和其他非环境变量凭证 (#10300)。
        if not has_creds:
            try:
                from agent.credential_pool import load_pool
                pool = load_pool(direct_match)
                if pool.has_credentials():
                    has_creds = True
            except Exception:
                pass
        if not has_creds:
            try:
                from hermes_cli.auth import _load_auth_store
                store = _load_auth_store()
                if direct_match in store.get("providers", {}) or direct_match in store.get("credential_pool", {}):
                    has_creds = True
            except Exception:
                pass

        # 始终返回直连提供商匹配结果。如果缺少凭证，
        # 客户端初始化会给出明确的错误，而不是
        # 静默地通过错误的提供商路由 (#10300)。
        return (direct_match, name)

    # --- 步骤 2：检查 OpenRouter 目录 ---
    # 首先尝试精确匹配（处理 provider/model 格式）
    or_slug = _find_openrouter_slug(name)
    if or_slug:
        if current_provider != "openrouter":
            return ("openrouter", or_slug)
        # 已经在 openrouter 上，只需返回解析后的 slug
        if or_slug != name:
            return ("openrouter", or_slug)
        return None  # 已经在 openrouter 上且名称匹配

    return None


def _find_openrouter_slug(model_name: str) -> Optional[str]:
    """查找裸名或部分模型名称对应的完整 OpenRouter 模型 slug。

    处理:
    - 精确匹配: ``anthropic/claude-opus-4.6`` -> 原样
    - 裸名称: ``deepseek-chat`` -> ``deepseek/deepseek-chat``
    - 裸名称: ``claude-opus-4.6`` -> ``anthropic/claude-opus-4.6``
    """
    name_lower = model_name.strip().lower()
    if not name_lower:
        return None

    # 精确匹配（已有 provider/ 前缀）
    for mid in model_ids():
        if name_lower == mid.lower():
            return mid

    # 尝试仅匹配模型部分（/ 之后的部分）
    for mid in model_ids():
        if "/" in mid:
            _, model_part = mid.split("/", 1)
            if name_lower == model_part.lower():
                return mid

    return None


def normalize_provider(provider: Optional[str]) -> str:
    """将提供商别名规范化为 Hermes 的规范提供商 ID。

    注意: ``"auto"`` 直接透传——使用
    ``hermes_cli.auth.resolve_provider()`` 来根据凭证和环境
    将其解析为具体的提供商。
    """
    normalized = (provider or "openrouter").strip().lower()
    return _PROVIDER_ALIASES.get(normalized, normalized)


def provider_label(provider: Optional[str]) -> str:
    """返回提供商 ID 或别名的人类友好标签。"""
    original = (provider or "openrouter").strip()
    normalized = original.lower()
    if normalized == "auto":
        return "Auto"
    normalized = normalize_provider(normalized)
    return _PROVIDER_LABELS.get(normalized, original or "OpenRouter")


# 支持 OpenAI 优先处理（service_tier="priority"）的模型。
# 参见 https://openai.com/api-priority-processing/ 获取规范列表。
# 仅存储裸模型 slug（无厂商前缀）。
_PRIORITY_PROCESSING_MODELS: frozenset[str] = frozenset({
    "gpt-5.4",
    "gpt-5.4-mini",
    "gpt-5.2",
    "gpt-5.1",
    "gpt-5",
    "gpt-5-mini",
    "gpt-4.1",
    "gpt-4.1-mini",
    "gpt-4.1-nano",
    "gpt-4o",
    "gpt-4o-mini",
    "o3",
    "o4-mini",
})

# 支持 Anthropic 快速模式（speed="fast"）的模型。
# 参见 https://platform.claude.com/docs/en/build-with-claude/fast-mode
# 目前仅限 Claude Opus 4.6。存储连字符和点号两种变体，
# 以处理原生 Anthropic（claude-opus-4-6）和 OpenRouter（claude-opus-4.6）。
_ANTHROPIC_FAST_MODE_MODELS: frozenset[str] = frozenset({
    "claude-opus-4-6",
    "claude-opus-4.6",
})


def _strip_vendor_prefix(model_id: str) -> str:
    """移除模型 ID 的 vendor/ 前缀（如 'anthropic/claude-opus-4-6' -> 'claude-opus-4-6'）。"""
    raw = str(model_id or "").strip().lower()
    if "/" in raw:
        raw = raw.split("/", 1)[1]
    return raw


def model_supports_fast_mode(model_id: Optional[str]) -> bool:
    """返回 Hermes 是否应为此模型暴露 /fast 切换。"""
    raw = _strip_vendor_prefix(str(model_id or ""))
    if raw in _PRIORITY_PROCESSING_MODELS:
        return True
    # Anthropic 快速模式——去除日期后缀（如 claude-opus-4-6-20260401）
    # 和 OpenRouter 变体标签（:fast, :beta）以进行匹配。
    base = raw.split(":")[0]
    return base in _ANTHROPIC_FAST_MODE_MODELS


def _is_anthropic_fast_model(model_id: Optional[str]) -> bool:
    """当模型支持 Anthropic 快速模式（speed='fast'）时返回 True。"""
    raw = _strip_vendor_prefix(str(model_id or ""))
    base = raw.split(":")[0]
    return base in _ANTHROPIC_FAST_MODE_MODELS


def resolve_fast_mode_overrides(model_id: Optional[str]) -> dict[str, Any] | None:
    """返回快速/优先模式的 request_overrides，不支持时返回 None。

    返回适合提供商的覆盖参数：
    - OpenAI 模型: ``{"service_tier": "priority"}``（优先处理）
    - Anthropic 模型: ``{"speed": "fast"}``（Anthropic 快速模式 beta）

    这些覆盖参数通过 run_agent.py 中的 ``_build_api_kwargs`` 注入到
    API 请求的 kwargs 中——每个 API 路径处理自己的键
    （OpenAI/Codex 用 service_tier，Anthropic Messages 用 speed）。
    """
    if not model_supports_fast_mode(model_id):
        return None
    if _is_anthropic_fast_model(model_id):
        return {"speed": "fast"}
    return {"service_tier": "priority"}


def _resolve_copilot_catalog_api_key() -> str:
    """尽力获取 Copilot 模型目录的 GitHub 令牌。"""
    try:
        from hermes_cli.auth import resolve_api_key_provider_credentials

        creds = resolve_api_key_provider_credentials("copilot")
        return str(creds.get("api_key") or "").strip()
    except Exception:
        return ""


def provider_model_ids(provider: Optional[str], *, force_refresh: bool = False) -> list[str]:
    """返回提供商的最佳已知模型目录。

    为支持在线端点的提供商（Codex、Nous）尝试实时 API，
    回退到静态列表。
    """
    normalized = normalize_provider(provider)
    if normalized == "openrouter":
        return model_ids(force_refresh=force_refresh)
    if normalized == "openai-codex":
        from hermes_cli.codex_models import get_codex_model_ids

        return get_codex_model_ids()
    if normalized in {"copilot", "copilot-acp"}:
        try:
            live = _fetch_github_models(_resolve_copilot_catalog_api_key())
            if live:
                return live
        except Exception:
            pass
        if normalized == "copilot-acp":
            return list(_PROVIDER_MODELS.get("copilot", []))
    if normalized == "nous":
        # 尝试在线 Nous Portal /models 端点
        try:
            from hermes_cli.auth import fetch_nous_models, resolve_nous_runtime_credentials
            creds = resolve_nous_runtime_credentials()
            if creds:
                live = fetch_nous_models(api_key=creds.get("api_key", ""), inference_base_url=creds.get("base_url", ""))
                if live:
                    return live
        except Exception:
            pass
    if normalized == "anthropic":
        live = _fetch_anthropic_models()
        if live:
            return live
    if normalized == "ai-gateway":
        live = _fetch_ai_gateway_models()
        if live:
            return live
    if normalized == "ollama-cloud":
        live = fetch_ollama_cloud_models(force_refresh=force_refresh)
        if live:
            return live
    if normalized == "custom":
        base_url = _get_custom_base_url()
        if base_url:
            # 尝试自定义端点的常见 API 密钥环境变量
            api_key = (
                os.getenv("CUSTOM_API_KEY", "")
                or os.getenv("OPENAI_API_KEY", "")
                or os.getenv("OPENROUTER_API_KEY", "")
            )
            live = fetch_api_models(api_key, base_url)
            if live:
                return live
    return list(_PROVIDER_MODELS.get(normalized, []))


def _fetch_anthropic_models(timeout: float = 5.0) -> Optional[list[str]]:
    """从 Anthropic /v1/models 端点获取可用模型。

    使用 resolve_anthropic_token() 查找凭证（环境变量或
    Claude Code 自动发现）。返回排序后的模型 ID 列表或 None。
    """
    try:
        from agent.anthropic_adapter import resolve_anthropic_token, _is_oauth_token
    except ImportError:
        return None

    token = resolve_anthropic_token()
    if not token:
        return None

    headers: dict[str, str] = {"anthropic-version": "2023-06-01"}
    if _is_oauth_token(token):
        headers["Authorization"] = f"Bearer {token}"
        from agent.anthropic_adapter import _COMMON_BETAS, _OAUTH_ONLY_BETAS
        headers["anthropic-beta"] = ",".join(_COMMON_BETAS + _OAUTH_ONLY_BETAS)
    else:
        headers["x-api-key"] = token

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/models",
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
            models = [m["id"] for m in data.get("data", []) if m.get("id")]
            # 排序：最新/最大的优先（opus > sonnet > haiku，更高版本优先）
            return sorted(models, key=lambda m: (
                "opus" not in m,      # opus 优先
                "sonnet" not in m,    # 然后 sonnet
                "haiku" not in m,     # 然后 haiku
                m,                    # 同等级内按字母顺序
            ))
    except Exception as e:
        import logging
        logging.getLogger(__name__).debug("Failed to fetch Anthropic models: %s", e)
        return None


def _payload_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        data = payload.get("data", [])
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
    return []


def copilot_default_headers() -> dict[str, str]:
    """Copilot API 请求的标准请求头。

    包含 opencode 和 Copilot CLI 在每个请求中发送的
    Openai-Intent 和 x-initiator 请求头。
    """
    try:
        from hermes_cli.copilot_auth import copilot_request_headers
        return copilot_request_headers(is_agent_turn=True)
    except ImportError:
        return {
            "Editor-Version": COPILOT_EDITOR_VERSION,
            "User-Agent": "HermesAgent/1.0",
            "Openai-Intent": "conversation-edits",
            "x-initiator": "agent",
        }


def _copilot_catalog_item_is_text_model(item: dict[str, Any]) -> bool:
    model_id = str(item.get("id") or "").strip()
    if not model_id:
        return False

    if item.get("model_picker_enabled") is False:
        return False

    capabilities = item.get("capabilities")
    if isinstance(capabilities, dict):
        model_type = str(capabilities.get("type") or "").strip().lower()
        if model_type and model_type != "chat":
            return False

    supported_endpoints = item.get("supported_endpoints")
    if isinstance(supported_endpoints, list):
        normalized_endpoints = {
            str(endpoint).strip()
            for endpoint in supported_endpoints
            if str(endpoint).strip()
        }
        if normalized_endpoints and not normalized_endpoints.intersection(
            {"/chat/completions", "/responses", "/v1/messages"}
        ):
            return False

    return True


def fetch_github_model_catalog(
    api_key: Optional[str] = None, timeout: float = 5.0
) -> Optional[list[dict[str, Any]]]:
    """获取此账户的实时 GitHub Copilot 模型目录。"""
    attempts: list[dict[str, str]] = []
    if api_key:
        attempts.append({
            **copilot_default_headers(),
            "Authorization": f"Bearer {api_key}",
        })
    attempts.append(copilot_default_headers())

    for headers in attempts:
        req = urllib.request.Request(COPILOT_MODELS_URL, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode())
                items = _payload_items(data)
                models: list[dict[str, Any]] = []
                seen_ids: set[str] = set()
                for item in items:
                    if not _copilot_catalog_item_is_text_model(item):
                        continue
                    model_id = str(item.get("id") or "").strip()
                    if not model_id or model_id in seen_ids:
                        continue
                    seen_ids.add(model_id)
                    models.append(item)
                if models:
                    return models
        except Exception:
            continue
    return None


def _is_github_models_base_url(base_url: Optional[str]) -> bool:
    normalized = (base_url or "").strip().rstrip("/").lower()
    return (
        normalized.startswith(COPILOT_BASE_URL)
        or normalized.startswith("https://models.github.ai/inference")
    )


def _fetch_github_models(api_key: Optional[str] = None, timeout: float = 5.0) -> Optional[list[str]]:
    catalog = fetch_github_model_catalog(api_key=api_key, timeout=timeout)
    if not catalog:
        return None
    return [item.get("id", "") for item in catalog if item.get("id")]


_COPILOT_MODEL_ALIASES = {
    "openai/gpt-5": "gpt-5-mini",
    "openai/gpt-5-chat": "gpt-5-mini",
    "openai/gpt-5-mini": "gpt-5-mini",
    "openai/gpt-5-nano": "gpt-5-mini",
    "openai/gpt-4.1": "gpt-4.1",
    "openai/gpt-4.1-mini": "gpt-4.1",
    "openai/gpt-4.1-nano": "gpt-4.1",
    "openai/gpt-4o": "gpt-4o",
    "openai/gpt-4o-mini": "gpt-4o-mini",
    "openai/o1": "gpt-5.2",
    "openai/o1-mini": "gpt-5-mini",
    "openai/o1-preview": "gpt-5.2",
    "openai/o3": "gpt-5.3-codex",
    "openai/o3-mini": "gpt-5-mini",
    "openai/o4-mini": "gpt-5-mini",
    "anthropic/claude-opus-4.6": "claude-opus-4.6",
    "anthropic/claude-sonnet-4.6": "claude-sonnet-4.6",
    "anthropic/claude-sonnet-4.5": "claude-sonnet-4.5",
    "anthropic/claude-haiku-4.5": "claude-haiku-4.5",
}


def _copilot_catalog_ids(
    catalog: Optional[list[dict[str, Any]]] = None,
    api_key: Optional[str] = None,
) -> set[str]:
    if catalog is None and api_key:
        catalog = fetch_github_model_catalog(api_key=api_key)
    if not catalog:
        return set()
    return {
        str(item.get("id") or "").strip()
        for item in catalog
        if str(item.get("id") or "").strip()
    }


def normalize_copilot_model_id(
    model_id: Optional[str],
    *,
    catalog: Optional[list[dict[str, Any]]] = None,
    api_key: Optional[str] = None,
) -> str:
    raw = str(model_id or "").strip()
    if not raw:
        return ""

    catalog_ids = _copilot_catalog_ids(catalog=catalog, api_key=api_key)
    alias = _COPILOT_MODEL_ALIASES.get(raw)
    if alias:
        return alias

    candidates = [raw]
    if "/" in raw:
        candidates.append(raw.split("/", 1)[1].strip())

    if raw.endswith("-mini"):
        candidates.append(raw[:-5])
    if raw.endswith("-nano"):
        candidates.append(raw[:-5])
    if raw.endswith("-chat"):
        candidates.append(raw[:-5])

    seen: set[str] = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        if candidate in _COPILOT_MODEL_ALIASES:
            return _COPILOT_MODEL_ALIASES[candidate]
        if candidate in catalog_ids:
            return candidate

    if "/" in raw:
        return raw.split("/", 1)[1].strip()
    return raw


def _github_reasoning_efforts_for_model_id(model_id: str) -> list[str]:
    raw = (model_id or "").strip().lower()
    if raw.startswith(("openai/o1", "openai/o3", "openai/o4", "o1", "o3", "o4")):
        return list(COPILOT_REASONING_EFFORTS_O_SERIES)
    normalized = normalize_copilot_model_id(model_id).lower()
    if normalized.startswith("gpt-5"):
        return list(COPILOT_REASONING_EFFORTS_GPT5)
    return []


def _should_use_copilot_responses_api(model_id: str) -> bool:
    """判断 Copilot 模型是否应使用 Responses API。

    复制 opencode 的 ``shouldUseCopilotResponsesApi`` 逻辑：
    GPT-5+ 模型使用 Responses API，但 ``gpt-5-mini`` 使用
    Chat Completions。所有非 GPT 模型（Claude、Gemini 等）使用
    Chat Completions。
    """
    import re

    match = re.match(r"^gpt-(\d+)", model_id)
    if not match:
        return False
    major = int(match.group(1))
    return major >= 5 and not model_id.startswith("gpt-5-mini")


def copilot_model_api_mode(
    model_id: Optional[str],
    *,
    catalog: Optional[list[dict[str, Any]]] = None,
    api_key: Optional[str] = None,
) -> str:
    """确定 Copilot 模型的 API 模式。

    使用模型 ID 模式（匹配 opencode 的方法）作为主要信号。
    仅对模式检查未覆盖的模型回退到目录的 ``supported_endpoints``。
    """
    # 获取目录一次，使 normalize + endpoint 检查可以共享
    # （避免对非 GPT-5 模型进行两次冗余网络调用）。
    if catalog is None and api_key:
        catalog = fetch_github_model_catalog(api_key=api_key)

    normalized = normalize_copilot_model_id(model_id, catalog=catalog, api_key=api_key)
    if not normalized:
        return "chat_completions"

    # 主要方式：模型 ID 模式（匹配 opencode 的 shouldUseCopilotResponsesApi）
    if _should_use_copilot_responses_api(normalized):
        return "codex_responses"

    # 备选方式：对非 GPT-5 模型检查目录（Claude 通过 /v1/messages 等）
    if catalog:
        catalog_entry = next((item for item in catalog if item.get("id") == normalized), None)
        if isinstance(catalog_entry, dict):
            supported_endpoints = {
                str(endpoint).strip()
                for endpoint in (catalog_entry.get("supported_endpoints") or [])
                if str(endpoint).strip()
            }
            # 对非 GPT-5 模型，检查是否仅支持 messages API
            if "/v1/messages" in supported_endpoints and "/chat/completions" not in supported_endpoints:
                return "anthropic_messages"

    return "chat_completions"


def normalize_opencode_model_id(provider_id: Optional[str], model_id: Optional[str]) -> str:
    """将 OpenCode 配置 ID 规范化为 API 请求中使用的裸模型 slug。"""
    provider = normalize_provider(provider_id)
    current = str(model_id or "").strip()
    if not current or provider not in {"opencode-zen", "opencode-go"}:
        return current

    prefix = f"{provider}/"
    if current.lower().startswith(prefix):
        return current[len(prefix):]
    return current


def opencode_model_api_mode(provider_id: Optional[str], model_id: Optional[str]) -> str:
    """确定 OpenCode Zen / Go 模型的 API 模式。

    OpenCode 将不同模型路由到不同的 API 接口：

    - Zen 上的 GPT-5 / Codex 模型使用 ``/v1/responses``
    - Zen 上的 Claude 模型使用 ``/v1/messages``
    - Go 上的 MiniMax 模型使用 ``/v1/messages``
    - Go 上的 GLM / Kimi 使用 ``/v1/chat/completions``
    - 其他 Zen 模型（Gemini、GLM、Kimi、MiniMax、Qwen 等）使用
      ``/v1/chat/completions``

    这遵循 OpenCode 发布的 Zen 和 Go 端点文档。
    """
    provider = normalize_provider(provider_id)
    normalized = normalize_opencode_model_id(provider_id, model_id).lower()
    if not normalized:
        return "chat_completions"

    if provider == "opencode-go":
        if normalized.startswith("minimax-"):
            return "anthropic_messages"
        return "chat_completions"

    if provider == "opencode-zen":
        if normalized.startswith("claude-"):
            return "anthropic_messages"
        if normalized.startswith("gpt-"):
            return "codex_responses"
        return "chat_completions"

    return "chat_completions"


def github_model_reasoning_efforts(
    model_id: Optional[str],
    *,
    catalog: Optional[list[dict[str, Any]]] = None,
    api_key: Optional[str] = None,
) -> list[str]:
    """返回 Copilot 可见模型支持的推理力度级别。"""
    normalized = normalize_copilot_model_id(model_id, catalog=catalog, api_key=api_key)
    if not normalized:
        return []

    catalog_entry = None
    if catalog is not None:
        catalog_entry = next((item for item in catalog if item.get("id") == normalized), None)
    elif api_key:
        fetched_catalog = fetch_github_model_catalog(api_key=api_key)
        if fetched_catalog:
            catalog_entry = next((item for item in fetched_catalog if item.get("id") == normalized), None)

    if catalog_entry is not None:
        capabilities = catalog_entry.get("capabilities")
        if isinstance(capabilities, dict):
            supports = capabilities.get("supports")
            if isinstance(supports, dict):
                efforts = supports.get("reasoning_effort")
                if isinstance(efforts, list):
                    normalized_efforts = [
                        str(effort).strip().lower()
                        for effort in efforts
                        if str(effort).strip()
                    ]
                    return list(dict.fromkeys(normalized_efforts))
            return []
        legacy_capabilities = {
            str(capability).strip().lower()
            for capability in catalog_entry.get("capabilities", [])
            if str(capability).strip()
        }
        if "reasoning" not in legacy_capabilities:
            return []

    return _github_reasoning_efforts_for_model_id(str(model_id or normalized))


def probe_api_models(
    api_key: Optional[str],
    base_url: Optional[str],
    timeout: float = 5.0,
) -> dict[str, Any]:
    """使用轻量级 URL 启发式探测 OpenAI 兼容的 ``/models`` 端点。"""
    normalized = (base_url or "").strip().rstrip("/")
    if not normalized:
        return {
            "models": None,
            "probed_url": None,
            "resolved_base_url": "",
            "suggested_base_url": None,
            "used_fallback": False,
        }

    if _is_github_models_base_url(normalized):
        models = _fetch_github_models(api_key=api_key, timeout=timeout)
        return {
            "models": models,
            "probed_url": COPILOT_MODELS_URL,
            "resolved_base_url": COPILOT_BASE_URL,
            "suggested_base_url": None,
            "used_fallback": False,
        }

    if normalized.endswith("/v1"):
        alternate_base = normalized[:-3].rstrip("/")
    else:
        alternate_base = normalized + "/v1"

    candidates: list[tuple[str, bool]] = [(normalized, False)]
    if alternate_base and alternate_base != normalized:
        candidates.append((alternate_base, True))

    tried: list[str] = []
    headers: dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    if normalized.startswith(COPILOT_BASE_URL):
        headers.update(copilot_default_headers())

    for candidate_base, is_fallback in candidates:
        url = candidate_base.rstrip("/") + "/models"
        tried.append(url)
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode())
                return {
                    "models": [m.get("id", "") for m in data.get("data", [])],
                    "probed_url": url,
                    "resolved_base_url": candidate_base.rstrip("/"),
                    "suggested_base_url": alternate_base if alternate_base != candidate_base else normalized,
                    "used_fallback": is_fallback,
                }
        except Exception:
            continue

    return {
        "models": None,
        "probed_url": tried[0] if tried else normalized.rstrip("/") + "/models",
        "resolved_base_url": normalized,
        "suggested_base_url": alternate_base if alternate_base != normalized else None,
        "used_fallback": False,
    }


def _fetch_ai_gateway_models(timeout: float = 5.0) -> Optional[list[str]]:
    """从 AI Gateway 获取支持工具调用的可用语言模型。"""
    api_key = os.getenv("AI_GATEWAY_API_KEY", "").strip()
    if not api_key:
        return None
    base_url = os.getenv("AI_GATEWAY_BASE_URL", "").strip()
    if not base_url:
        from hermes_constants import AI_GATEWAY_BASE_URL
        base_url = AI_GATEWAY_BASE_URL

    url = base_url.rstrip("/") + "/models"
    headers: dict[str, str] = {"Authorization": f"Bearer {api_key}"}
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
            return [
                m["id"]
                for m in data.get("data", [])
                if m.get("id")
                and m.get("type") == "language"
                and "tool-use" in (m.get("tags") or [])
            ]
    except Exception:
        return None


def fetch_api_models(
    api_key: Optional[str],
    base_url: Optional[str],
    timeout: float = 5.0,
) -> Optional[list[str]]:
    """从提供商的 ``/models`` 端点获取可用模型 ID 列表。

    返回模型 ID 字符串列表，如果无法到达端点
    （网络错误、超时、认证失败等）则返回 ``None``。
    """
    return probe_api_models(api_key, base_url, timeout=timeout).get("models")


# ---------------------------------------------------------------------------
# Ollama Cloud — 合并的模型发现与磁盘缓存
# ---------------------------------------------------------------------------



_OLLAMA_CLOUD_CACHE_TTL = 3600  # 1 小时


def _ollama_cloud_cache_path() -> Path:
    """返回 Ollama Cloud 模型缓存的路径。"""
    from hermes_constants import get_hermes_home
    return get_hermes_home() / "ollama_cloud_models_cache.json"


def _load_ollama_cloud_cache(*, ignore_ttl: bool = False) -> Optional[dict]:
    """从磁盘加载缓存的 Ollama Cloud 模型。

    Args:
        ignore_ttl: 如果为 True，即使 TTL 已过期也返回数据（过期回退）。
    """
    try:
        cache_path = _ollama_cloud_cache_path()
        if not cache_path.exists():
            return None
        with open(cache_path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return None
        models = data.get("models")
        if not (isinstance(models, list) and models):
            return None
        if not ignore_ttl:
            cached_at = data.get("cached_at", 0)
            if (time.time() - cached_at) > _OLLAMA_CLOUD_CACHE_TTL:
                return None  # 已过期
        return data
    except Exception:
        pass
    return None


def _save_ollama_cloud_cache(models: list[str]) -> None:
    """将合并的 Ollama Cloud 模型列表持久化到磁盘。"""
    try:
        from utils import atomic_json_write
        cache_path = _ollama_cloud_cache_path()
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_json_write(cache_path, {"models": models, "cached_at": time.time()}, indent=None)
    except Exception:
        pass


def fetch_ollama_cloud_models(
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    *,
    force_refresh: bool = False,
) -> list[str]:
    """通过合并在线 API + models.dev 获取 Ollama Cloud 模型，带磁盘缓存。

    解析顺序：
      1. 磁盘缓存（如果新鲜，< 1 小时，且非 force_refresh）
      2. 在线 ``/v1/models`` 端点（主要——最新来源）
      3. models.dev 注册表（次要——填补未列出模型的空白）
      4. 合并：在线模型优先，然后是 models.dev 补充（去重）

    返回模型 ID 列表（不会返回 None——完全失败时返回空列表）。
    """
    # 1. 检查磁盘缓存
    if not force_refresh:
        cached = _load_ollama_cloud_cache()
        if cached is not None:
            return cached["models"]

    # 2. 在线 API 探测
    if not api_key:
        api_key = os.getenv("OLLAMA_API_KEY", "")
    if not base_url:
        base_url = os.getenv("OLLAMA_BASE_URL", "") or "https://ollama.com/v1"

    live_models: list[str] = []
    if api_key:
        result = fetch_api_models(api_key, base_url, timeout=8.0)
        if result:
            live_models = result

    # 3. models.dev 注册表
    mdev_models: list[str] = []
    try:
        from agent.models_dev import list_agentic_models
        mdev_models = list_agentic_models("ollama-cloud")
    except Exception:
        pass

    # 4. 合并：在线模型优先，然后是 models.dev 补充（去重，保序）
    if live_models or mdev_models:
        seen: set[str] = set()
        merged: list[str] = []
        for m in live_models:
            if m and m not in seen:
                seen.add(m)
                merged.append(m)
        for m in mdev_models:
            if m and m not in seen:
                seen.add(m)
                merged.append(m)
        if merged:
            _save_ollama_cloud_cache(merged)
            return merged

    # 完全失败——如果有过期缓存则返回（忽略 TTL）
    stale = _load_ollama_cloud_cache(ignore_ttl=True)
    if stale is not None:
        return stale["models"]

    return []


def validate_requested_model(
    model_name: str,
    provider: Optional[str],
    *,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
) -> dict[str, Any]:
    """
    验证活跃提供商的 ``/model`` 值。

    先执行格式检查，然后探测在线 API 以确认模型实际存在。

    返回字典包含：
      - accepted: CLI 是否应立即切换到请求的模型
      - persist: 是否可以安全保存到配置
      - recognized: 是否匹配已知的提供商目录
      - message: 可选的警告/指导信息
    """
    requested = (model_name or "").strip()
    normalized = normalize_provider(provider)
    if normalized == "openrouter" and base_url and "openrouter.ai" not in base_url:
        normalized = "custom"
    requested_for_lookup = requested
    if normalized == "copilot":
        requested_for_lookup = normalize_copilot_model_id(
            requested,
            api_key=api_key,
        ) or requested

    if not requested:
        return {
            "accepted": False,
            "persist": False,
            "recognized": False,
            "message": "Model name cannot be empty.",
        }

    if any(ch.isspace() for ch in requested):
        return {
            "accepted": False,
            "persist": False,
            "recognized": False,
            "message": "Model names cannot contain spaces.",
        }

    if normalized == "custom":
        probe = probe_api_models(api_key, base_url)
        api_models = probe.get("models")
        if api_models is not None:
            if requested_for_lookup in set(api_models):
                return {
                    "accepted": True,
                    "persist": True,
                    "recognized": True,
                    "message": None,
                }

            # 如果最佳匹配非常相似则自动纠正（如打字错误）
            auto = get_close_matches(requested_for_lookup, api_models, n=1, cutoff=0.9)
            if auto:
                return {
                    "accepted": True,
                    "persist": True,
                    "recognized": True,
                    "corrected_model": auto[0],
                    "message": f"Auto-corrected `{requested}` → `{auto[0]}`",
                }

            suggestions = get_close_matches(requested, api_models, n=3, cutoff=0.5)
            suggestion_text = ""
            if suggestions:
                suggestion_text = "\n  Similar models: " + ", ".join(f"`{s}`" for s in suggestions)

            message = (
                f"Note: `{requested}` was not found in this custom endpoint's model listing "
                f"({probe.get('probed_url')}). It may still work if the server supports hidden or aliased models."
                f"{suggestion_text}"
            )
            if probe.get("used_fallback"):
                message += (
                    f"\n  Endpoint verification succeeded after trying `{probe.get('resolved_base_url')}`. "
                    f"Consider saving that as your base URL."
                )

            return {
                "accepted": True,
                "persist": True,
                "recognized": False,
                "message": message,
            }

        message = (
            f"Note: could not reach this custom endpoint's model listing at `{probe.get('probed_url')}`. "
            f"Hermes will still save `{requested}`, but the endpoint should expose `/models` for verification."
        )
        if probe.get("suggested_base_url"):
            message += f"\n  If this server expects `/v1`, try base URL: `{probe.get('suggested_base_url')}`"

        return {
            "accepted": True,
            "persist": True,
            "recognized": False,
            "message": message,
        }

    # OpenAI Codex 有自己的目录路径；/v1/models 探测不是正确的验证路径。
    if normalized == "openai-codex":
        try:
            codex_models = provider_model_ids("openai-codex")
        except Exception:
            codex_models = []
        if codex_models:
            if requested_for_lookup in set(codex_models):
                return {
                    "accepted": True,
                    "persist": True,
                    "recognized": True,
                    "message": None,
                }
            # 如果最佳匹配非常相似则自动纠正（如打字错误）
            auto = get_close_matches(requested_for_lookup, codex_models, n=1, cutoff=0.9)
            if auto:
                return {
                    "accepted": True,
                    "persist": True,
                    "recognized": True,
                    "corrected_model": auto[0],
                    "message": f"Auto-corrected `{requested}` → `{auto[0]}`",
                }
            suggestions = get_close_matches(requested_for_lookup, codex_models, n=3, cutoff=0.5)
            suggestion_text = ""
            if suggestions:
                suggestion_text = "\n  Similar models: " + ", ".join(f"`{s}`" for s in suggestions)
            return {
                "accepted": True,
                "persist": True,
                "recognized": False,
                "message": (
                    f"Note: `{requested}` was not found in the OpenAI Codex model listing. "
                    f"It may still work if your account has access to it."
                    f"{suggestion_text}"
                ),
            }

    # 探测在线 API 以检查模型是否实际存在
    api_models = fetch_api_models(api_key, base_url)

    if api_models is not None:
        if requested_for_lookup in set(api_models):
            # API 确认模型存在
            return {
                "accepted": True,
                "persist": True,
                "recognized": True,
                "message": None,
            }
        else:
            # API 已响应但模型未列出。仍然接受——
            # 用户可能有权访问未在公开列表中显示的模型
            # （如 Z.AI Pro/Max 套餐可以在编码端点使用 glm-5，
            # 即使它不在 /models 中）。警告但允许。

            # 如果最佳匹配非常相似则自动纠正（如打字错误）
            auto = get_close_matches(requested_for_lookup, api_models, n=1, cutoff=0.9)
            if auto:
                return {
                    "accepted": True,
                    "persist": True,
                    "recognized": True,
                    "corrected_model": auto[0],
                    "message": f"Auto-corrected `{requested}` → `{auto[0]}`",
                }

            suggestions = get_close_matches(requested, api_models, n=3, cutoff=0.5)
            suggestion_text = ""
            if suggestions:
                suggestion_text = "\n  Similar models: " + ", ".join(f"`{s}`" for s in suggestions)

            return {
                "accepted": True,
                "persist": True,
                "recognized": False,
                "message": (
                    f"Note: `{requested}` was not found in this provider's model listing. "
                    f"It may still work if your plan supports it."
                    f"{suggestion_text}"
                ),
            }

    # api_models 为 None——无法到达 API。接受并持久化，
    # 但发出警告以免打字错误悄悄破坏功能。

    # Bedrock：使用我们自己的发现而非 HTTP /models 端点。
    # Bedrock 的 bedrock-runtime URL 不支持 /models——它使用
    # AWS SDK 控制面板（ListFoundationModels + ListInferenceProfiles）。
    if normalized == "bedrock":
        try:
            from agent.bedrock_adapter import discover_bedrock_models, resolve_bedrock_region
            region = resolve_bedrock_region()
            discovered = discover_bedrock_models(region)
            discovered_ids = {m["id"] for m in discovered}
            if requested in discovered_ids:
                return {
                    "accepted": True,
                    "persist": True,
                    "recognized": True,
                    "message": None,
                }
            # 不在发现列表中——仍然接受（用户可能有自定义
            # 推理配置文件或跨账户访问），但发出警告。
            suggestions = get_close_matches(requested, list(discovered_ids), n=3, cutoff=0.4)
            suggestion_text = ""
            if suggestions:
                suggestion_text = "\n  Similar models: " + ", ".join(f"`{s}`" for s in suggestions)
            return {
                "accepted": True,
                "persist": True,
                "recognized": False,
                "message": (
                    f"Note: `{requested}` was not found in Bedrock model discovery for {region}. "
                    f"It may still work with custom inference profiles or cross-account access."
                    f"{suggestion_text}"
                ),
            }
        except Exception:
            pass  # 回退到通用警告

    provider_label = _PROVIDER_LABELS.get(normalized, normalized)
    return {
        "accepted": True,
        "persist": True,
        "recognized": False,
        "message": (
            f"Could not reach the {provider_label} API to validate `{requested}`. "
            f"If the service isn't down, this model may not be valid."
        ),
    }
