"""
Hermes Agent 中提供者（Provider）身份的唯一真实来源。

两个数据源，在运行时合并：

1. **models.dev 目录** —— 109+ 个提供者，包含基础 URL、环境变量、显示
   名称和完整的模型元数据（上下文、成本、能力）。这是主数据库。

2. **Hermes 覆盖层** —— 传输类型、认证模式、聚合器标志，
   以及 models.dev 未跟踪的额外环境变量。小型字典，
   在此维护。

3. **用户配置**（config.yaml 中的 ``providers:`` 部分）—— 用户自定义
   端点和覆盖项。合并在所有其他配置之上。

其他模块从此文件导入。不存在并行的注册表。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# -- Hermes 覆盖层 ----------------------------------------------------------
# models.dev 未提供的 Hermes 特有元数据。

@dataclass(frozen=True)
class HermesOverlay:
    """叠加在 models.dev 之上的 Hermes 特有提供者元数据。"""

    transport: str = "openai_chat"        # openai_chat | anthropic_messages | codex_responses
    is_aggregator: bool = False
    auth_type: str = "api_key"            # api_key | oauth_device_code | oauth_external | external_process
    extra_env_vars: Tuple[str, ...] = ()  # models.dev 未列出的环境变量
    base_url_override: str = ""           # 当 models.dev URL 错误或缺失时的覆盖值
    base_url_env_var: str = ""            # 用户自定义基础 URL 的环境变量


HERMES_OVERLAYS: Dict[str, HermesOverlay] = {
    "openrouter": HermesOverlay(
        transport="openai_chat",
        is_aggregator=True,
        extra_env_vars=("OPENAI_API_KEY",),
        base_url_env_var="OPENROUTER_BASE_URL",
    ),
    "nous": HermesOverlay(
        transport="openai_chat",
        auth_type="oauth_device_code",
        base_url_override="https://inference-api.nousresearch.com/v1",
    ),
    "openai-codex": HermesOverlay(
        transport="codex_responses",
        auth_type="oauth_external",
        base_url_override="https://chatgpt.com/backend-api/codex",
    ),
    "qwen-oauth": HermesOverlay(
        transport="openai_chat",
        auth_type="oauth_external",
        base_url_override="https://portal.qwen.ai/v1",
        base_url_env_var="HERMES_QWEN_BASE_URL",
    ),
    "google-gemini-cli": HermesOverlay(
        transport="openai_chat",
        auth_type="oauth_external",
        base_url_override="cloudcode-pa://google",
    ),
    "copilot-acp": HermesOverlay(
        transport="codex_responses",
        auth_type="external_process",
        base_url_override="acp://copilot",
        base_url_env_var="COPILOT_ACP_BASE_URL",
    ),
    "github-copilot": HermesOverlay(
        transport="openai_chat",
        extra_env_vars=("COPILOT_GITHUB_TOKEN", "GH_TOKEN"),
    ),
    "anthropic": HermesOverlay(
        transport="anthropic_messages",
        extra_env_vars=("ANTHROPIC_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"),
    ),
    "zai": HermesOverlay(
        transport="openai_chat",
        extra_env_vars=("GLM_API_KEY", "ZAI_API_KEY", "Z_AI_API_KEY"),
        base_url_env_var="GLM_BASE_URL",
    ),
    "kimi-for-coding": HermesOverlay(
        transport="openai_chat",
        base_url_env_var="KIMI_BASE_URL",
    ),
    "minimax": HermesOverlay(
        transport="anthropic_messages",
        base_url_env_var="MINIMAX_BASE_URL",
    ),
    "minimax-cn": HermesOverlay(
        transport="anthropic_messages",
        base_url_env_var="MINIMAX_CN_BASE_URL",
    ),
    "deepseek": HermesOverlay(
        transport="openai_chat",
        base_url_env_var="DEEPSEEK_BASE_URL",
    ),
    "alibaba": HermesOverlay(
        transport="openai_chat",
        base_url_env_var="DASHSCOPE_BASE_URL",
    ),
    "vercel": HermesOverlay(
        transport="openai_chat",
        is_aggregator=True,
    ),
    "opencode": HermesOverlay(
        transport="openai_chat",
        is_aggregator=True,
        base_url_env_var="OPENCODE_ZEN_BASE_URL",
    ),
    "opencode-go": HermesOverlay(
        transport="openai_chat",
        is_aggregator=True,
        base_url_env_var="OPENCODE_GO_BASE_URL",
    ),
    "kilo": HermesOverlay(
        transport="openai_chat",
        is_aggregator=True,
        base_url_env_var="KILOCODE_BASE_URL",
    ),
    "huggingface": HermesOverlay(
        transport="openai_chat",
        is_aggregator=True,
        base_url_env_var="HF_BASE_URL",
    ),
    "xai": HermesOverlay(
        transport="codex_responses",
        base_url_override="https://api.x.ai/v1",
        base_url_env_var="XAI_BASE_URL",
    ),
    "xiaomi": HermesOverlay(
        transport="openai_chat",
        base_url_env_var="XIAOMI_BASE_URL",
    ),
    "arcee": HermesOverlay(
        transport="openai_chat",
        base_url_override="https://api.arcee.ai/api/v1",
        base_url_env_var="ARCEE_BASE_URL",
    ),
    "ollama-cloud": HermesOverlay(
        transport="openai_chat",
        base_url_env_var="OLLAMA_BASE_URL",
    ),
}


# -- 已解析的提供者 -------------------------------------------------------
# models.dev + 覆盖层 + 用户配置合并后的结果。

@dataclass
class ProviderDef:
    """完整的提供者定义 —— 从所有数据源合并而来。"""

    id: str
    name: str
    transport: str                        # openai_chat | anthropic_messages | codex_responses
    api_key_env_vars: Tuple[str, ...]     # 需要检查的所有 API 密钥环境变量
    base_url: str = ""
    base_url_env_var: str = ""
    is_aggregator: bool = False
    auth_type: str = "api_key"
    doc: str = ""
    source: str = ""                      # "models.dev"、"hermes"、"user-config"


# -- 别名 ------------------------------------------------------------------
# 将人类友好的/旧版名称映射到规范的提供者 ID。
# 尽可能使用 models.dev 的 ID。

ALIASES: Dict[str, str] = {
    # openrouter
    "openai": "openrouter",     # 裸写 "openai" → 通过聚合器路由

    # zai
    "glm": "zai",
    "z-ai": "zai",
    "z.ai": "zai",
    "zhipu": "zai",

    # xai
    "x-ai": "xai",
    "x.ai": "xai",
    "grok": "xai",

    # kimi-for-coding（models.dev ID）
    "kimi": "kimi-for-coding",
    "kimi-coding": "kimi-for-coding",
    "kimi-coding-cn": "kimi-for-coding",
    "moonshot": "kimi-for-coding",

    # minimax-cn
    "minimax-china": "minimax-cn",
    "minimax_cn": "minimax-cn",

    # anthropic
    "claude": "anthropic",
    "claude-code": "anthropic",

    # github-copilot（models.dev ID）
    "copilot": "github-copilot",
    "github": "github-copilot",
    "github-copilot-acp": "copilot-acp",

    # vercel（models.dev 中 AI Gateway 的 ID）
    "ai-gateway": "vercel",
    "aigateway": "vercel",
    "vercel-ai-gateway": "vercel",

    # opencode（models.dev 中 OpenCode Zen 的 ID）
    "opencode-zen": "opencode",
    "zen": "opencode",

    # opencode-go
    "go": "opencode-go",
    "opencode-go-sub": "opencode-go",

    # kilo（models.dev 中 KiloCode 的 ID）
    "kilocode": "kilo",
    "kilo-code": "kilo",
    "kilo-gateway": "kilo",

    # deepseek
    "deep-seek": "deepseek",

    # alibaba
    "dashscope": "alibaba",
    "aliyun": "alibaba",
    "qwen": "alibaba",
    "alibaba-cloud": "alibaba",

    # google-gemini-cli（OAuth + Code Assist）
    "gemini-cli": "google-gemini-cli",
    "gemini-oauth": "google-gemini-cli",


    # huggingface
    "hf": "huggingface",
    "hugging-face": "huggingface",
    "huggingface-hub": "huggingface",

    # xiaomi
    "mimo": "xiaomi",
    "xiaomi-mimo": "xiaomi",

    # bedrock
    "aws": "bedrock",
    "aws-bedrock": "bedrock",
    "amazon-bedrock": "bedrock",
    "amazon": "bedrock",

    # arcee
    "arcee-ai": "arcee",
    "arceeai": "arcee",

    # 本地服务别名 → 虚拟的 "local" 概念（通过用户配置解析）
    "lmstudio": "lmstudio",
    "lm-studio": "lmstudio",
    "lm_studio": "lmstudio",
    "ollama": "custom",  # 裸写 "ollama" = 本地; 使用 "ollama-cloud" 表示云端
    "vllm": "local",
    "llamacpp": "local",
    "llama.cpp": "local",
    "llama-cpp": "local",
}


# -- 显示标签 -----------------------------------------------------------
# 从 models.dev + 覆盖层动态构建。作为目录中未包含的
# 提供者的后备方案。

_LABEL_OVERRIDES: Dict[str, str] = {
    "nous": "Nous Portal",
    "openai-codex": "OpenAI Codex",
    "copilot-acp": "GitHub Copilot ACP",
    "xiaomi": "Xiaomi MiMo",
    "local": "Local endpoint",
    "bedrock": "AWS Bedrock",
    "ollama-cloud": "Ollama Cloud",
}


# -- 传输协议 → API 模式映射 ---------------------------------------------

TRANSPORT_TO_API_MODE: Dict[str, str] = {
    "openai_chat": "chat_completions",
    "anthropic_messages": "anthropic_messages",
    "codex_responses": "codex_responses",
    "bedrock_converse": "bedrock_converse",
}


# -- 辅助函数 ---------------------------------------------------------

def normalize_provider(name: str) -> str:
    """解析别名并标准化大小写为规范的提供者 ID。

    返回规范的 ID 字符串。不验证该 ID 是否对应已知的提供者。
    """
    key = name.strip().lower()
    return ALIASES.get(key, key)


def get_provider(name: str) -> Optional[ProviderDef]:
    """通过 ID 或别名查找提供者，合并所有数据源。

    解析顺序:
      1. Hermes 覆盖层（用于不在 models.dev 中的提供者: nous、openai-codex 等）
      2. models.dev 目录 + Hermes 覆盖层
      3. 用户在配置中自定义的提供者（TODO: 第 4 阶段）

    返回完全解析的 ProviderDef 或 None。
    """
    canonical = normalize_provider(name)

    # 尝试获取 models.dev 数据
    try:
        from agent.models_dev import get_provider_info as _mdev_provider
        mdev_info = _mdev_provider(canonical)
    except Exception:
        mdev_info = None

    overlay = HERMES_OVERLAYS.get(canonical)

    if mdev_info is not None:
        # 合并 models.dev + 覆盖层数据
        transport = overlay.transport if overlay else "openai_chat"
        is_agg = overlay.is_aggregator if overlay else False
        auth = overlay.auth_type if overlay else "api_key"
        base_url_env = overlay.base_url_env_var if overlay else ""
        base_url_override = overlay.base_url_override if overlay else ""

        # 组合环境变量：models.dev 环境变量 + hermes 额外环境变量
        env_vars = list(mdev_info.env)
        if overlay and overlay.extra_env_vars:
            for ev in overlay.extra_env_vars:
                if ev not in env_vars:
                    env_vars.append(ev)

        return ProviderDef(
            id=canonical,
            name=mdev_info.name,
            transport=transport,
            api_key_env_vars=tuple(env_vars),
            base_url=base_url_override or mdev_info.api,
            base_url_env_var=base_url_env,
            is_aggregator=is_agg,
            auth_type=auth,
            doc=mdev_info.doc,
            source="models.dev",
        )

    if overlay is not None:
        # 仅在 Hermes 中定义的提供者（不在 models.dev 中）
        return ProviderDef(
            id=canonical,
            name=_LABEL_OVERRIDES.get(canonical, canonical),
            transport=overlay.transport,
            api_key_env_vars=overlay.extra_env_vars,
            base_url=overlay.base_url_override,
            base_url_env_var=overlay.base_url_env_var,
            is_aggregator=overlay.is_aggregator,
            auth_type=overlay.auth_type,
            source="hermes",
        )

    return None


def get_label(provider_id: str) -> str:
    """获取提供者的人类可读显示名称。"""
    canonical = normalize_provider(provider_id)

    # 优先检查标签覆盖
    if canonical in _LABEL_OVERRIDES:
        return _LABEL_OVERRIDES[canonical]

    # 尝试 models.dev
    pdef = get_provider(canonical)
    if pdef:
        return pdef.name

    return canonical




def is_aggregator(provider: str) -> bool:
    """当提供者是多模型聚合器时返回 True。"""
    pdef = get_provider(provider)
    return pdef.is_aggregator if pdef else False


def determine_api_mode(provider: str, base_url: str = "") -> str:
    """确定提供者/端点的 API 模式（通信协议）。

    解析顺序:
      1. 已知提供者 → 传输协议 → TRANSPORT_TO_API_MODE。
      2. 对未知/自定义提供者使用 URL 启发式判断。
      3. 默认: 'chat_completions'。
    """
    pdef = get_provider(provider)
    if pdef is not None:
        return TRANSPORT_TO_API_MODE.get(pdef.transport, "chat_completions")

    # 直接检查不在 HERMES_OVERLAYS 中的提供者
    if provider == "bedrock":
        return "bedrock_converse"

    # 对自定义/未知提供者使用基于 URL 的启发式判断
    if base_url:
        url_lower = base_url.rstrip("/").lower()
        if url_lower.endswith("/anthropic") or "api.anthropic.com" in url_lower:
            return "anthropic_messages"
        if "api.openai.com" in url_lower:
            return "codex_responses"
        if "bedrock-runtime" in url_lower and "amazonaws.com" in url_lower:
            return "bedrock_converse"

    return "chat_completions"


# -- 从用户配置解析提供者 ------------------------------------------------

def resolve_user_provider(name: str, user_config: Dict[str, Any]) -> Optional[ProviderDef]:
    """从用户 config.yaml 的 ``providers:`` 部分解析提供者。

    参数:
        name: 用户指定的提供者名称。
        user_config: config.yaml 中的 ``providers:`` 字典。

    返回:
        如果找到则返回 ProviderDef，否则返回 None。
    """
    if not user_config or not isinstance(user_config, dict):
        return None

    entry = user_config.get(name)
    if not isinstance(entry, dict):
        return None

    # 提取字段
    display_name = entry.get("name", "") or name
    api_url = entry.get("api", "") or entry.get("url", "") or entry.get("base_url", "") or ""
    key_env = entry.get("key_env", "") or ""
    transport = entry.get("transport", "openai_chat") or "openai_chat"

    env_vars: List[str] = []
    if key_env:
        env_vars.append(key_env)

    return ProviderDef(
        id=name,
        name=display_name,
        transport=transport,
        api_key_env_vars=tuple(env_vars),
        base_url=api_url,
        is_aggregator=False,
        auth_type="api_key",
        source="user-config",
    )


def custom_provider_slug(display_name: str) -> str:
    """为 custom_providers 条目构建规范的 slug。

    匹配 runtime_provider 和 credential_pool 使用的惯例
    （``custom:<normalized-name>``）。集中在此处以确保所有调用
    位置生成相同的 slug。
    """
    return "custom:" + display_name.strip().lower().replace(" ", "-")


def resolve_custom_provider(
    name: str,
    custom_providers: Optional[List[Dict[str, Any]]],
) -> Optional[ProviderDef]:
    """从用户 config.yaml 的 ``custom_providers`` 列表中解析提供者。"""
    if not custom_providers or not isinstance(custom_providers, list):
        return None

    requested = (name or "").strip().lower()
    if not requested:
        return None

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
        # 名称和 URL 都是必需的，缺少任一则跳过
        if not display_name or not api_url:
            continue

        slug = custom_provider_slug(display_name)
        # 同时匹配显示名称和 slug 形式
        if requested not in {display_name.lower(), slug}:
            continue

        return ProviderDef(
            id=slug,
            name=display_name,
            transport="openai_chat",
            api_key_env_vars=(),
            base_url=api_url,
            is_aggregator=False,
            auth_type="api_key",
            source="user-config",
        )

    return None


def resolve_provider_full(
    name: str,
    user_providers: Optional[Dict[str, Any]] = None,
    custom_providers: Optional[List[Dict[str, Any]]] = None,
) -> Optional[ProviderDef]:
    """完整的解析链：内置 → models.dev → 用户配置。

    这是 --provider 标志解析的主入口点。

    参数:
        name: 提供者名称或别名。
        user_providers: config.yaml 中的 ``providers:`` 字典（可选）。
        custom_providers: config.yaml 中的 ``custom_providers:`` 列表（可选）。

    返回:
        如果找到则返回 ProviderDef，否则返回 None。
    """
    canonical = normalize_provider(name)

    # 1. 内置提供者（models.dev + 覆盖层）
    pdef = get_provider(canonical)
    if pdef is not None:
        return pdef

    # 2. 用户在配置中自定义的提供者
    if user_providers:
        # 尝试用规范名称查找
        user_pdef = resolve_user_provider(canonical, user_providers)
        if user_pdef is not None:
            return user_pdef
        # 尝试用原始名称查找（以防别名不匹配）
        user_pdef = resolve_user_provider(name.strip().lower(), user_providers)
        if user_pdef is not None:
            return user_pdef

    # 2b. 配置中保存的自定义提供者
    custom_pdef = resolve_custom_provider(name, custom_providers)
    if custom_pdef is not None:
        return custom_pdef

    # 3. 直接尝试 models.dev（用于不在我们 ALIASES 中的提供者）
    try:
        from agent.models_dev import get_provider_info as _mdev_provider
        mdev_info = _mdev_provider(canonical)
        if mdev_info is not None:
            return ProviderDef(
                id=canonical,
                name=mdev_info.name,
                transport="openai_chat",
                api_key_env_vars=mdev_info.env,
                base_url=mdev_info.api,
                source="models.dev",
            )
    except Exception:
        pass

    return None
