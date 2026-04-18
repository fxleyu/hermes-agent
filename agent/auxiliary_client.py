"""共享辅助客户端路由器，用于侧任务。

提供统一的解析链，使每个使用者（上下文压缩、会话搜索、
网页提取、视觉分析、浏览器视觉）都能选择最佳可用后端，
而无需重复回退逻辑。

文本任务的解析顺序（auto 模式）：
  1. OpenRouter  (OPENROUTER_API_KEY)
  2. Nous Portal (~/.hermes/auth.json 活跃提供者)
  3. 自定义端点 (config.yaml model.base_url + OPENAI_API_KEY)
  4. Codex OAuth (通过 chatgpt.com 的 Responses API 使用 gpt-5.3-codex，
     包装为类似 chat.completions 客户端)
  5. 原生 Anthropic
  6. 直接 API 密钥提供者 (z.ai/GLM、Kimi/Moonshot、MiniMax、MiniMax-CN)
  7. None

视觉/多模态任务的解析顺序（auto 模式）：
  1. 已选择的主提供者，如果它是以下支持的视觉后端之一
  2. OpenRouter
  3. Nous Portal
  4. Codex OAuth（gpt-5.3-codex 通过 Responses API 支持视觉）
  5. 原生 Anthropic
  6. 自定义端点（用于本地视觉模型：Qwen-VL、LLaVA、Pixtral 等）
  7. None

每任务覆盖在 config.yaml 的 ``auxiliary:`` 部分配置
（例如 ``auxiliary.vision.provider``、``auxiliary.compression.model``）。
默认 "auto" 遵循上述链。

支付/额度耗尽回退：
  当解析的提供者返回 HTTP 402 或额度相关错误时，
  call_llm() 自动用自动检测链中的下一个可用提供者重试。
  这处理了用户耗尽 OpenRouter 余额但有 Codex OAuth
  或其他提供者可用的常见情况。
"""

import json
import logging
import os
import threading
import time
from pathlib import Path  # noqa: F401 — used by test mocks
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

from openai import OpenAI

from agent.credential_pool import load_pool
from hermes_cli.config import get_hermes_home
from hermes_constants import OPENROUTER_BASE_URL

logger = logging.getLogger(__name__)

# 模块级标志：每个进程只警告一次过期的 OPENAI_BASE_URL。
_stale_base_url_warned = False

_PROVIDER_ALIASES = {
    "google": "gemini",
    "google-gemini": "gemini",
    "google-ai-studio": "gemini",
    "x-ai": "xai",
    "x.ai": "xai",
    "grok": "xai",
    "glm": "zai",
    "z-ai": "zai",
    "z.ai": "zai",
    "zhipu": "zai",
    "kimi": "kimi-coding",
    "moonshot": "kimi-coding",
    "kimi-cn": "kimi-coding-cn",
    "moonshot-cn": "kimi-coding-cn",
    "minimax-china": "minimax-cn",
    "minimax_cn": "minimax-cn",
    "claude": "anthropic",
    "claude-code": "anthropic",
}


def _normalize_aux_provider(provider: Optional[str]) -> str:
    normalized = (provider or "auto").strip().lower()
    if normalized.startswith("custom:"):
        suffix = normalized.split(":", 1)[1].strip()
        if not suffix:
            return "custom"
        normalized = suffix
    if normalized == "codex":
        return "openai-codex"
    if normalized == "main":
        # 解析为用户的实际主提供者，使命名自定义提供者
        # 和非聚合器提供者（DeepSeek、Alibaba 等）能正确工作。
        main_prov = _read_main_provider()
        if main_prov and main_prov not in ("auto", "main", ""):
            return main_prov
        return "custom"
    return _PROVIDER_ALIASES.get(normalized, normalized)

# 直接 API 密钥提供者的默认辅助模型（用于侧任务的廉价/快速模型）
_API_KEY_PROVIDER_AUX_MODELS: Dict[str, str] = {
    "gemini": "gemini-3-flash-preview",
    "zai": "glm-4.5-flash",
    "kimi-coding": "kimi-k2-turbo-preview",
    "kimi-coding-cn": "kimi-k2-turbo-preview",
    "minimax": "MiniMax-M2.7",
    "minimax-cn": "MiniMax-M2.7",
    "anthropic": "claude-haiku-4-5-20251001",
    "ai-gateway": "google/gemini-3-flash",
    "opencode-zen": "gemini-3-flash",
    "opencode-go": "glm-5",
    "kilocode": "google/gemini-3-flash-preview",
    "ollama-cloud": "nemotron-3-nano:30b",
}

# 视觉特定的模型覆盖，用于直接提供者。
# 当用户的主提供者有一个与其主聊天模型不同的专用视觉/多模态模型时，
# 在此映射。视觉自动检测的"特殊提供者"分支在回退到主模型之前检查此项。
_PROVIDER_VISION_MODELS: Dict[str, str] = {
    "xiaomi": "mimo-v2-omni",
    "zai": "glm-5v-turbo",
}

# OpenRouter 应用归属头
_OR_HEADERS = {
    "HTTP-Referer": "https://hermes-agent.nousresearch.com",
    "X-OpenRouter-Title": "Hermes Agent",
    "X-OpenRouter-Categories": "productivity,cli-agent",
}

# Nous Portal 产品归属的 extra_body。
# 当辅助客户端由 Nous Portal 支持时，调用方应在
# chat.completions.create() 中将此作为 extra_body 传递。
NOUS_EXTRA_BODY = {"tags": ["product=hermes-agent"]}

# 解析时设置——如果辅助客户端指向 Nous Portal 则为 True
auxiliary_is_nous: bool = False

# 每个提供者的默认辅助模型
_OPENROUTER_MODEL = "google/gemini-3-flash-preview"
_NOUS_MODEL = "google/gemini-3-flash-preview"
_NOUS_FREE_TIER_VISION_MODEL = "xiaomi/mimo-v2-omni"
_NOUS_FREE_TIER_AUX_MODEL = "xiaomi/mimo-v2-pro"
_NOUS_DEFAULT_BASE_URL = "https://inference-api.nousresearch.com/v1"
_ANTHROPIC_DEFAULT_BASE_URL = "https://api.anthropic.com"
_AUTH_JSON_PATH = get_hermes_home() / "auth.json"

# Codex 回退：使用 Responses API（Codex OAuth 令牌能访问的唯一端点），
# 并为辅助任务使用快速模型。
# ChatGPT 支持的 Codex 帐户目前拒绝这些辅助流的 gpt-5.3-codex，
# 而 gpt-5.2-codex 仍广泛可用且支持通过 Responses 的视觉功能。
_CODEX_AUX_MODEL = "gpt-5.2-codex"
_CODEX_AUX_BASE_URL = "https://chatgpt.com/backend-api/codex"


def _to_openai_base_url(base_url: str) -> str:
    """将 Anthropic 风格的 base URL 标准化为 OpenAI 兼容格式。

    某些提供者（MiniMax、MiniMax-CN）暴露用于 Anthropic Messages API
    的 ``/anthropic`` 端点和用于 OpenAI chat completions 的独立
    ``/v1`` 端点。辅助客户端使用 OpenAI SDK，因此必须访问
    ``/v1`` 接口。传递原始 ``inference_base_url`` 会导致请求
    落在 ``/anthropic/chat/completions`` 上——返回 404。
    """
    url = str(base_url or "").strip().rstrip("/")
    if url.endswith("/anthropic"):
        rewritten = url[: -len("/anthropic")] + "/v1"
        logger.debug("Auxiliary client: rewrote base URL %s → %s", url, rewritten)
        return rewritten
    return url


def _select_pool_entry(provider: str) -> Tuple[bool, Optional[Any]]:
    """返回 (该提供者是否存在凭证池, 选中的条目)。"""
    try:
        pool = load_pool(provider)
    except Exception as exc:
        logger.debug("Auxiliary client: could not load pool for %s: %s", provider, exc)
        return False, None
    if not pool or not pool.has_credentials():
        return False, None
    try:
        return True, pool.select()
    except Exception as exc:
        logger.debug("Auxiliary client: could not select pool entry for %s: %s", provider, exc)
        return True, None


def _pool_runtime_api_key(entry: Any) -> str:
    if entry is None:
        return ""
    # 使用 PooledCredential.runtime_api_key 属性，该属性处理
    # 提供者特定的回退逻辑（例如 nous 的 agent_key）。
    key = getattr(entry, "runtime_api_key", None) or getattr(entry, "access_token", "")
    return str(key or "").strip()


def _pool_runtime_base_url(entry: Any, fallback: str = "") -> str:
    if entry is None:
        return str(fallback or "").strip().rstrip("/")
    # runtime_base_url 处理提供者特定的逻辑（例如 nous 优先使用 inference_base_url）。
    # 对于非 PooledCredential 条目，回退到 inference_base_url 和 base_url。
    url = (
        getattr(entry, "runtime_base_url", None)
        or getattr(entry, "inference_base_url", None)
        or getattr(entry, "base_url", None)
        or fallback
    )
    return str(url or "").strip().rstrip("/")


# ── Codex Responses → chat.completions 适配器 ─────────────────────────────
# 所有辅助使用者调用 client.chat.completions.create(**kwargs) 并
# 读取 response.choices[0].message.content。此适配器将这些
# 调用转换为 Codex Responses API，使调用方无需任何修改。


def _convert_content_for_responses(content: Any) -> Any:
    """将 chat.completions 内容转换为 Responses API 格式。

    chat.completions 使用:
      {"type": "text", "text": "..."}
      {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}

    Responses API 使用:
      {"type": "input_text", "text": "..."}
      {"type": "input_image", "image_url": "data:image/png;base64,..."}

    如果内容是纯字符串，直接返回（Responses API 对纯文本消息
    直接接受字符串）。
    """
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content) if content else ""

    converted: List[Dict[str, Any]] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        ptype = part.get("type", "")
        if ptype == "text":
            converted.append({"type": "input_text", "text": part.get("text", "")})
        elif ptype == "image_url":
            # chat.completions 中 URL 是嵌套的: {"image_url": {"url": "..."}}
            image_data = part.get("image_url", {})
            url = image_data.get("url", "") if isinstance(image_data, dict) else str(image_data)
            entry: Dict[str, Any] = {"type": "input_image", "image_url": url}
            # 如果指定了 detail 则保留
            detail = image_data.get("detail") if isinstance(image_data, dict) else None
            if detail:
                entry["detail"] = detail
            converted.append(entry)
        elif ptype in ("input_text", "input_image"):
            # 已经是 Responses 格式——直接传递
            converted.append(part)
        else:
            # 未知内容类型——尝试作为文本保留
            text = part.get("text", "")
            if text:
                converted.append({"type": "input_text", "text": text})

    return converted or ""


class _CodexCompletionsAdapter:
    """直通填充层，接受 chat.completions.create() 的 kwargs 参数，
    并通过 Codex Responses 流式 API 进行路由。"""

    def __init__(self, real_client: OpenAI, model: str):
        self._client = real_client
        self._model = model

    def create(self, **kwargs) -> Any:
        messages = kwargs.get("messages", [])
        model = kwargs.get("model", self._model)

        # 将系统/指令消息与对话消息分开。
        # 将 chat.completions 的多模态内容块转换为 Responses
        # API 格式（input_text / input_image 替代 text / image_url）。
        instructions = "You are a helpful assistant."
        input_msgs: List[Dict[str, Any]] = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content") or ""
            if role == "system":
                instructions = content if isinstance(content, str) else str(content)
            else:
                input_msgs.append({
                    "role": role,
                    "content": _convert_content_for_responses(content),
                })

        resp_kwargs: Dict[str, Any] = {
            "model": model,
            "instructions": instructions,
            "input": input_msgs or [{"role": "user", "content": ""}],
            "store": False,
        }

        # 注意：Codex 端点 (chatgpt.com/backend-api/codex) 不支持
        # max_output_tokens 或 temperature——省略以避免 400 错误。

        # 工具支持，用于 flush_memories 和类似的调用方
        tools = kwargs.get("tools")
        if tools:
            converted = []
            for t in tools:
                fn = t.get("function", {}) if isinstance(t, dict) else {}
                name = fn.get("name")
                if not name:
                    continue
                converted.append({
                    "type": "function",
                    "name": name,
                    "description": fn.get("description", ""),
                    "parameters": fn.get("parameters", {}),
                })
            if converted:
                resp_kwargs["tools"] = converted

        # 流式接收并收集响应
        text_parts: List[str] = []
        tool_calls_raw: List[Any] = []
        usage = None

        try:
            # 在流式传输期间收集输出项和文本增量——
            # Codex 后端可能从 get_final_response() 返回空的 response.output，
            # 即使流式传输了项目也是如此。
            collected_output_items: List[Any] = []
            collected_text_deltas: List[str] = []
            has_function_calls = False
            with self._client.responses.stream(**resp_kwargs) as stream:
                for _event in stream:
                    _etype = getattr(_event, "type", "")
                    if _etype == "response.output_item.done":
                        _done = getattr(_event, "item", None)
                        if _done is not None:
                            collected_output_items.append(_done)
                    elif "output_text.delta" in _etype:
                        _delta = getattr(_event, "delta", "")
                        if _delta:
                            collected_text_deltas.append(_delta)
                    elif "function_call" in _etype:
                        has_function_calls = True
                final = stream.get_final_response()

            # 用收集的流事件回填空的 output
            _output = getattr(final, "output", None)
            if isinstance(_output, list) and not _output:
                if collected_output_items:
                    final.output = list(collected_output_items)
                    logger.debug(
                        "Codex auxiliary: backfilled %d output items from stream events",
                        len(collected_output_items),
                    )
                elif collected_text_deltas and not has_function_calls:
                    # 仅在没有流式工具调用时合成文本——
                    # 带有附带文本的 function_call 响应不应
                    # 被折叠为纯文本消息。
                    assembled = "".join(collected_text_deltas)
                    final.output = [SimpleNamespace(
                        type="message", role="assistant", status="completed",
                        content=[SimpleNamespace(type="output_text", text=assembled)],
                    )]
                    logger.debug(
                        "Codex auxiliary: synthesized from %d deltas (%d chars)",
                        len(collected_text_deltas), len(assembled),
                    )

            # 从 Responses 输出中提取文本和工具调用。
            # 项目可能是 SDK 对象（属性访问）或字典（原始/回退路径），
            # 因此使用一个处理两种形式的辅助函数。
            def _item_get(obj: Any, key: str, default: Any = None) -> Any:
                val = getattr(obj, key, None)
                if val is None and isinstance(obj, dict):
                    val = obj.get(key, default)
                return val if val is not None else default

            for item in getattr(final, "output", []):
                item_type = _item_get(item, "type")
                if item_type == "message":
                    for part in (_item_get(item, "content") or []):
                        ptype = _item_get(part, "type")
                        if ptype in ("output_text", "text"):
                            text_parts.append(_item_get(part, "text", ""))
                elif item_type == "function_call":
                    tool_calls_raw.append(SimpleNamespace(
                        id=_item_get(item, "call_id", ""),
                        type="function",
                        function=SimpleNamespace(
                            name=_item_get(item, "name", ""),
                            arguments=_item_get(item, "arguments", "{}"),
                        ),
                    ))

            resp_usage = getattr(final, "usage", None)
            if resp_usage:
                usage = SimpleNamespace(
                    prompt_tokens=getattr(resp_usage, "input_tokens", 0),
                    completion_tokens=getattr(resp_usage, "output_tokens", 0),
                    total_tokens=getattr(resp_usage, "total_tokens", 0),
                )
        except Exception as exc:
            logger.debug("Codex auxiliary Responses API call failed: %s", exc)
            raise

        content = "".join(text_parts).strip() or None

        # 构建一个看起来像 chat.completions 的响应
        message = SimpleNamespace(
            role="assistant",
            content=content,
            tool_calls=tool_calls_raw or None,
        )
        choice = SimpleNamespace(
            index=0,
            message=message,
            finish_reason="stop" if not tool_calls_raw else "tool_calls",
        )
        return SimpleNamespace(
            choices=[choice],
            model=model,
            usage=usage,
        )


class _CodexChatShim:
    """包装适配器以提供 client.chat.completions.create() 接口。"""

    def __init__(self, adapter: _CodexCompletionsAdapter):
        self.completions = adapter


class CodexAuxiliaryClient:
    """兼容 OpenAI 客户端的包装器，通过 Codex Responses API 路由。

    使用者可以像平常一样调用 client.chat.completions.create(**kwargs)。
    同时暴露 .api_key 和 .base_url 供异步包装器内省使用。
    """

    def __init__(self, real_client: OpenAI, model: str):
        self._real_client = real_client
        adapter = _CodexCompletionsAdapter(real_client, model)
        self.chat = _CodexChatShim(adapter)
        self.api_key = real_client.api_key
        self.base_url = real_client.base_url

    def close(self):
        self._real_client.close()


class _AsyncCodexCompletionsAdapter:
    """Codex Responses 适配器的异步版本。

    通过 asyncio.to_thread() 包装同步适配器，使异步使用者
    （web_tools、session_search）可以正常 await。
    """

    def __init__(self, sync_adapter: _CodexCompletionsAdapter):
        self._sync = sync_adapter

    async def create(self, **kwargs) -> Any:
        import asyncio
        return await asyncio.to_thread(self._sync.create, **kwargs)


class _AsyncCodexChatShim:
    def __init__(self, adapter: _AsyncCodexCompletionsAdapter):
        self.completions = adapter


class AsyncCodexAuxiliaryClient:
    """匹配 AsyncOpenAI.chat.completions.create() 的异步兼容包装器。"""

    def __init__(self, sync_wrapper: "CodexAuxiliaryClient"):
        sync_adapter = sync_wrapper.chat.completions
        async_adapter = _AsyncCodexCompletionsAdapter(sync_adapter)
        self.chat = _AsyncCodexChatShim(async_adapter)
        self.api_key = sync_wrapper.api_key
        self.base_url = sync_wrapper.base_url


class _AnthropicCompletionsAdapter:
    """兼容 OpenAI 客户端的 Anthropic Messages API 适配器。"""

    def __init__(self, real_client: Any, model: str, is_oauth: bool = False):
        self._client = real_client
        self._model = model
        self._is_oauth = is_oauth

    def create(self, **kwargs) -> Any:
        from agent.anthropic_adapter import build_anthropic_kwargs, normalize_anthropic_response

        messages = kwargs.get("messages", [])
        model = kwargs.get("model", self._model)
        tools = kwargs.get("tools")
        tool_choice = kwargs.get("tool_choice")
        max_tokens = kwargs.get("max_tokens") or kwargs.get("max_completion_tokens") or 2000
        temperature = kwargs.get("temperature")

        normalized_tool_choice = None
        if isinstance(tool_choice, str):
            normalized_tool_choice = tool_choice
        elif isinstance(tool_choice, dict):
            choice_type = str(tool_choice.get("type", "")).lower()
            if choice_type == "function":
                normalized_tool_choice = tool_choice.get("function", {}).get("name")
            elif choice_type in {"auto", "required", "none"}:
                normalized_tool_choice = choice_type

        anthropic_kwargs = build_anthropic_kwargs(
            model=model,
            messages=messages,
            tools=tools,
            max_tokens=max_tokens,
            reasoning_config=None,
            tool_choice=normalized_tool_choice,
            is_oauth=self._is_oauth,
        )
        # Opus 4.7+ 拒绝任何非默认的 temperature/top_p/top_k；仅对
        # 仍接受这些参数的模型设置 temperature。build_anthropic_kwargs
        # 还会作为安全网额外剥离这些键——保留两层保护。
        if temperature is not None:
            from agent.anthropic_adapter import _forbids_sampling_params
            if not _forbids_sampling_params(model):
                anthropic_kwargs["temperature"] = temperature

        response = self._client.messages.create(**anthropic_kwargs)
        assistant_message, finish_reason = normalize_anthropic_response(response)

        usage = None
        if hasattr(response, "usage") and response.usage:
            prompt_tokens = getattr(response.usage, "input_tokens", 0) or 0
            completion_tokens = getattr(response.usage, "output_tokens", 0) or 0
            total_tokens = getattr(response.usage, "total_tokens", 0) or (prompt_tokens + completion_tokens)
            usage = SimpleNamespace(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
            )

        choice = SimpleNamespace(
            index=0,
            message=assistant_message,
            finish_reason=finish_reason,
        )
        return SimpleNamespace(
            choices=[choice],
            model=model,
            usage=usage,
        )


class _AnthropicChatShim:
    def __init__(self, adapter: _AnthropicCompletionsAdapter):
        self.completions = adapter


class AnthropicAuxiliaryClient:
    """基于原生 Anthropic 客户端的兼容 OpenAI 客户端的包装器。"""

    def __init__(self, real_client: Any, model: str, api_key: str, base_url: str, is_oauth: bool = False):
        self._real_client = real_client
        adapter = _AnthropicCompletionsAdapter(real_client, model, is_oauth=is_oauth)
        self.chat = _AnthropicChatShim(adapter)
        self.api_key = api_key
        self.base_url = base_url

    def close(self):
        close_fn = getattr(self._real_client, "close", None)
        if callable(close_fn):
            close_fn()


class _AsyncAnthropicCompletionsAdapter:
    def __init__(self, sync_adapter: _AnthropicCompletionsAdapter):
        self._sync = sync_adapter

    async def create(self, **kwargs) -> Any:
        import asyncio
        return await asyncio.to_thread(self._sync.create, **kwargs)


class _AsyncAnthropicChatShim:
    def __init__(self, adapter: _AsyncAnthropicCompletionsAdapter):
        self.completions = adapter


class AsyncAnthropicAuxiliaryClient:
    def __init__(self, sync_wrapper: "AnthropicAuxiliaryClient"):
        sync_adapter = sync_wrapper.chat.completions
        async_adapter = _AsyncAnthropicCompletionsAdapter(sync_adapter)
        self.chat = _AsyncAnthropicChatShim(async_adapter)
        self.api_key = sync_wrapper.api_key
        self.base_url = sync_wrapper.base_url


def _read_nous_auth() -> Optional[dict]:
    """读取并验证 ~/.hermes/auth.json 中的活跃 Nous 提供者。

    如果 Nous 是活跃提供者且有令牌，返回提供者状态字典，
    否则返回 None。
    """
    pool_present, entry = _select_pool_entry("nous")
    if pool_present:
        if entry is None:
            return None
        return {
            "access_token": getattr(entry, "access_token", ""),
            "refresh_token": getattr(entry, "refresh_token", None),
            "agent_key": getattr(entry, "agent_key", None),
            "inference_base_url": _pool_runtime_base_url(entry, _NOUS_DEFAULT_BASE_URL),
            "portal_base_url": getattr(entry, "portal_base_url", None),
            "client_id": getattr(entry, "client_id", None),
            "scope": getattr(entry, "scope", None),
            "token_type": getattr(entry, "token_type", "Bearer"),
            "source": "pool",
        }

    try:
        if not _AUTH_JSON_PATH.is_file():
            return None
        data = json.loads(_AUTH_JSON_PATH.read_text())
        if data.get("active_provider") != "nous":
            return None
        provider = data.get("providers", {}).get("nous", {})
        # 必须至少有 access_token 或 agent_key
        if not provider.get("agent_key") and not provider.get("access_token"):
            return None
        return provider
    except Exception as exc:
        logger.debug("Could not read Nous auth: %s", exc)
        return None


def _nous_api_key(provider: dict) -> str:
    """从 Nous 提供者状态字典中提取最佳 API 密钥。"""
    return provider.get("agent_key") or provider.get("access_token", "")


def _nous_base_url() -> str:
    """从环境变量或默认值解析 Nous 推理 base URL。"""
    return os.getenv("NOUS_INFERENCE_BASE_URL", _NOUS_DEFAULT_BASE_URL)


def _read_codex_access_token() -> Optional[str]:
    """从 Hermes 认证存储中读取有效、未过期的 Codex OAuth 访问令牌。

    如果凭证池存在但当前没有可选择的运行时条目
    （例如所有池槽位被标记为已耗尽），则回退到
    配置文件的 auth.json 令牌，而不是直接失败。这使得
    在池状态过期但存储的 OAuth 令牌仍有效时，
    显式回退到 Codex 仍能工作。
    """
    pool_present, entry = _select_pool_entry("openai-codex")
    if pool_present:
        token = _pool_runtime_api_key(entry)
        if token:
            return token

    try:
        from hermes_cli.auth import _read_codex_tokens
        data = _read_codex_tokens()
        tokens = data.get("tokens", {})
        access_token = tokens.get("access_token")
        if not isinstance(access_token, str) or not access_token.strip():
            return None

        # 检查 JWT 过期时间——过期的令牌会阻塞自动链，
        # 阻止回退到正常工作的提供者（例如 Anthropic）。
        try:
            import base64
            payload = access_token.split(".")[1]
            payload += "=" * (-len(payload) % 4)
            claims = json.loads(base64.urlsafe_b64decode(payload))
            exp = claims.get("exp", 0)
            if exp and time.time() > exp:
                logger.debug("Codex access token expired (exp=%s), skipping", exp)
                return None
        except Exception:
            pass  # 非 JWT 令牌或解码错误——照原样使用

        return access_token.strip()
    except Exception as exc:
        logger.debug("Could not read Codex auth for auxiliary client: %s", exc)
        return None


def _resolve_api_key_provider() -> Tuple[Optional[OpenAI], Optional[str]]:
    """按 PROVIDER_REGISTRY 顺序尝试每个 API 密钥提供者。

    返回第一个有可用运行时凭证的提供者的 (client, model)，
    如果均未配置则返回 (None, None)。
    """
    try:
        from hermes_cli.auth import PROVIDER_REGISTRY, resolve_api_key_provider_credentials
    except ImportError:
        logger.debug("Could not import PROVIDER_REGISTRY for API-key fallback")
        return None, None

    for provider_id, pconfig in PROVIDER_REGISTRY.items():
        if pconfig.auth_type != "api_key":
            continue
        if provider_id == "anthropic":
            # 仅在用户显式配置 anthropic 时尝试。
            # 没有此门控，Claude Code 凭证会在用户的主提供者
            # 失败时被静默用作辅助回退。
            try:
                from hermes_cli.auth import is_provider_explicitly_configured
                if not is_provider_explicitly_configured("anthropic"):
                    continue
            except ImportError:
                pass
            return _try_anthropic()

        pool_present, entry = _select_pool_entry(provider_id)
        if pool_present:
            api_key = _pool_runtime_api_key(entry)
            if not api_key:
                continue

            base_url = _to_openai_base_url(
                _pool_runtime_base_url(entry, pconfig.inference_base_url) or pconfig.inference_base_url
            )
            model = _API_KEY_PROVIDER_AUX_MODELS.get(provider_id)
            if model is None:
                continue  # 如果不知道有效的辅助模型则跳过该提供者
            logger.debug("Auxiliary text client: %s (%s) via pool", pconfig.name, model)
            extra = {}
            if "api.kimi.com" in base_url.lower():
                extra["default_headers"] = {"User-Agent": "KimiCLI/1.30.0"}
            elif "api.githubcopilot.com" in base_url.lower():
                from hermes_cli.models import copilot_default_headers

                extra["default_headers"] = copilot_default_headers()
            return OpenAI(api_key=api_key, base_url=base_url, **extra), model

        creds = resolve_api_key_provider_credentials(provider_id)
        api_key = str(creds.get("api_key", "")).strip()
        if not api_key:
            continue

        base_url = _to_openai_base_url(
            str(creds.get("base_url", "")).strip().rstrip("/") or pconfig.inference_base_url
        )
        model = _API_KEY_PROVIDER_AUX_MODELS.get(provider_id)
        if model is None:
            continue  # 如果不知道有效的辅助模型则跳过该提供者
        logger.debug("Auxiliary text client: %s (%s)", pconfig.name, model)
        extra = {}
        if "api.kimi.com" in base_url.lower():
            extra["default_headers"] = {"User-Agent": "KimiCLI/1.30.0"}
        elif "api.githubcopilot.com" in base_url.lower():
            from hermes_cli.models import copilot_default_headers

            extra["default_headers"] = copilot_default_headers()
        return OpenAI(api_key=api_key, base_url=base_url, **extra), model

    return None, None


# ── 提供者解析辅助函数 ─────────────────────────────────────────────



def _try_openrouter() -> Tuple[Optional[OpenAI], Optional[str]]:
    pool_present, entry = _select_pool_entry("openrouter")
    if pool_present:
        or_key = _pool_runtime_api_key(entry)
        if not or_key:
            return None, None
        base_url = _pool_runtime_base_url(entry, OPENROUTER_BASE_URL) or OPENROUTER_BASE_URL
        logger.debug("Auxiliary client: OpenRouter via pool")
        return OpenAI(api_key=or_key, base_url=base_url,
                       default_headers=_OR_HEADERS), _OPENROUTER_MODEL

    or_key = os.getenv("OPENROUTER_API_KEY")
    if not or_key:
        return None, None
    logger.debug("Auxiliary client: OpenRouter")
    return OpenAI(api_key=or_key, base_url=OPENROUTER_BASE_URL,
                   default_headers=_OR_HEADERS), _OPENROUTER_MODEL


def _try_nous(vision: bool = False) -> Tuple[Optional[OpenAI], Optional[str]]:
    # 在尝试 Nous 之前检查跨会话速率限制保护——
    # 如果另一个会话已经记录了 429，完全跳过 Nous
    # 以避免在已达到 RPH 桶限制上堆积更多请求。
    try:
        from agent.nous_rate_guard import nous_rate_limit_remaining
        _remaining = nous_rate_limit_remaining()
        if _remaining is not None and _remaining > 0:
            logger.debug(
                "Auxiliary: skipping Nous Portal (rate-limited, resets in %.0fs)",
                _remaining,
            )
            return None, None
    except Exception:
        pass

    nous = _read_nous_auth()
    if not nous:
        return None, None
    global auxiliary_is_nous
    auxiliary_is_nous = True
    logger.debug("Auxiliary client: Nous Portal")
    if nous.get("source") == "pool":
        model = "gemini-3-flash"
    else:
        model = _NOUS_MODEL
    # 免费层用户不能使用付费辅助模型——改用免费模型：
    # mimo-v2-omni 用于视觉，mimo-v2-pro 用于文本任务。
    try:
        from hermes_cli.models import check_nous_free_tier
        if check_nous_free_tier():
            model = _NOUS_FREE_TIER_VISION_MODEL if vision else _NOUS_FREE_TIER_AUX_MODEL
            logger.debug("Free-tier Nous account — using %s for auxiliary/%s",
                         model, "vision" if vision else "text")
    except Exception:
        pass
    return (
        OpenAI(
            api_key=_nous_api_key(nous),
            base_url=str(nous.get("inference_base_url") or _nous_base_url()).rstrip("/"),
        ),
        model,
    )


def _read_main_model() -> str:
    """从 config.yaml 读取用户配置的主模型。

    config.yaml 的 model.default 是活跃模型的唯一真相来源。
    不再查询环境变量。
    """
    try:
        from hermes_cli.config import load_config
        cfg = load_config()
        model_cfg = cfg.get("model", {})
        if isinstance(model_cfg, str) and model_cfg.strip():
            return model_cfg.strip()
        if isinstance(model_cfg, dict):
            default = model_cfg.get("default", "")
            if isinstance(default, str) and default.strip():
                return default.strip()
    except Exception:
        pass
    return ""


def _read_main_provider() -> str:
    """从 config.yaml 读取用户配置的主提供者。

    返回小写的提供者 ID（例如 "alibaba"、"openrouter"），
    如果未配置则返回 ""。
    """
    try:
        from hermes_cli.config import load_config
        cfg = load_config()
        model_cfg = cfg.get("model", {})
        if isinstance(model_cfg, dict):
            provider = model_cfg.get("provider", "")
            if isinstance(provider, str) and provider.strip():
                return provider.strip().lower()
    except Exception:
        pass
    return ""


def _resolve_custom_runtime() -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """解析活跃的自定义/主端点，方式与主 CLI 相同。

    这涵盖了环境变量驱动的 OPENAI_BASE_URL 设置和 config 保存的
    自定义端点（base URL 存在于 config.yaml 而非实时环境中）两种情况。
    """
    try:
        from hermes_cli.runtime_provider import resolve_runtime_provider

        runtime = resolve_runtime_provider(requested="custom")
    except Exception as exc:
        logger.debug("Auxiliary client: custom runtime resolution failed: %s", exc)
        runtime = None

    if not isinstance(runtime, dict):
        openai_base = os.getenv("OPENAI_BASE_URL", "").strip().rstrip("/")
        openai_key = os.getenv("OPENAI_API_KEY", "").strip()
        if not openai_base:
            return None, None, None
        runtime = {
            "base_url": openai_base,
            "api_key": openai_key,
        }

    custom_base = runtime.get("base_url")
    custom_key = runtime.get("api_key")
    custom_mode = runtime.get("api_mode")
    if not isinstance(custom_base, str) or not custom_base.strip():
        return None, None, None

    custom_base = custom_base.strip().rstrip("/")
    if "openrouter.ai" in custom_base.lower():
        # requested='custom' 在没有自定义端点配置时回退到 OpenRouter。
        # 对辅助路由将其视为"无自定义端点"。
        return None, None, None

    # 本地服务器（Ollama、llama.cpp、vLLM、LM Studio）不需要认证。
    # 使用占位密钥——OpenAI SDK 要求非空字符串但
    # 本地服务器忽略 Authorization 头。与 cli.py
    # _ensure_runtime_credentials() 的修复相同（PR #2556）。
    if not isinstance(custom_key, str) or not custom_key.strip():
        custom_key = "no-key-required"

    if not isinstance(custom_mode, str) or not custom_mode.strip():
        custom_mode = None

    return custom_base, custom_key.strip(), custom_mode


def _current_custom_base_url() -> str:
    custom_base, _, _ = _resolve_custom_runtime()
    return custom_base or ""


def _validate_proxy_env_urls() -> None:
    """当代理环境变量包含格式错误的 URL 时快速失败并给出清晰的错误。

    常见原因：shell 配置（例如 .zshrc）中的拼写错误，如
    ``export HTTP_PROXY=http://127.0.0.1:6153export NEXT_VAR=...``
    将 'export' 连接到端口号中。没有此检查，
    OpenAI/httpx 客户端会抛出一个不指明
    有问题的环境变量的晦涩 ``Invalid port`` 错误。
    """
    from urllib.parse import urlparse

    for key in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY",
                "https_proxy", "http_proxy", "all_proxy"):
        value = str(os.environ.get(key) or "").strip()
        if not value:
            continue
        try:
            parsed = urlparse(value)
            if parsed.scheme:
                _ = parsed.port          # 对例如 '6153export' 会抛出 ValueError
        except ValueError as exc:
            raise RuntimeError(
                f"Malformed proxy environment variable {key}={value!r}. "
                "Fix or unset your proxy settings and try again."
            ) from exc


def _validate_base_url(base_url: str) -> None:
    """在明显损坏的自定义端点 URL 到达 httpx 之前拒绝它们。"""
    from urllib.parse import urlparse

    candidate = str(base_url or "").strip()
    if not candidate or candidate.startswith("acp://"):
        return
    try:
        parsed = urlparse(candidate)
        if parsed.scheme in {"http", "https"}:
            _ = parsed.port              # 对格式错误的端口抛出 ValueError
    except ValueError as exc:
        raise RuntimeError(
            f"Malformed custom endpoint URL: {candidate!r}. "
            "Run `hermes setup` or `hermes model` and enter a valid http(s) base URL."
        ) from exc


def _try_custom_endpoint() -> Tuple[Optional[OpenAI], Optional[str]]:
    runtime = _resolve_custom_runtime()
    if len(runtime) == 2:
        custom_base, custom_key = runtime
        custom_mode = None
    else:
        custom_base, custom_key, custom_mode = runtime
    if not custom_base or not custom_key:
        return None, None
    if custom_base.lower().startswith(_CODEX_AUX_BASE_URL.lower()):
        return None, None
    model = _read_main_model() or "gpt-4o-mini"
    logger.debug("Auxiliary client: custom endpoint (%s, api_mode=%s)", model, custom_mode or "chat_completions")
    if custom_mode == "codex_responses":
        real_client = OpenAI(api_key=custom_key, base_url=custom_base)
        return CodexAuxiliaryClient(real_client, model), model
    return OpenAI(api_key=custom_key, base_url=custom_base), model


def _try_codex() -> Tuple[Optional[Any], Optional[str]]:
    pool_present, entry = _select_pool_entry("openai-codex")
    if pool_present:
        codex_token = _pool_runtime_api_key(entry)
        if codex_token:
            base_url = _pool_runtime_base_url(entry, _CODEX_AUX_BASE_URL) or _CODEX_AUX_BASE_URL
        else:
            codex_token = _read_codex_access_token()
            if not codex_token:
                return None, None
            base_url = _CODEX_AUX_BASE_URL
    else:
        codex_token = _read_codex_access_token()
        if not codex_token:
            return None, None
        base_url = _CODEX_AUX_BASE_URL
    logger.debug("Auxiliary client: Codex OAuth (%s via Responses API)", _CODEX_AUX_MODEL)
    real_client = OpenAI(api_key=codex_token, base_url=base_url)
    return CodexAuxiliaryClient(real_client, _CODEX_AUX_MODEL), _CODEX_AUX_MODEL


def _try_anthropic() -> Tuple[Optional[Any], Optional[str]]:
    try:
        from agent.anthropic_adapter import build_anthropic_client, resolve_anthropic_token
    except ImportError:
        return None, None

    pool_present, entry = _select_pool_entry("anthropic")
    if pool_present:
        if entry is None:
            return None, None
        token = _pool_runtime_api_key(entry)
    else:
        entry = None
        token = resolve_anthropic_token()
    if not token:
        return None, None

    # 允许从 config.yaml model.base_url 覆盖 base URL，但仅当
    # 配置的提供者是 anthropic 时——否则非 Anthropic 的
    # base_url（例如 Codex 端点）会泄漏到 Anthropic 请求中。
    base_url = _pool_runtime_base_url(entry, _ANTHROPIC_DEFAULT_BASE_URL) if pool_present else _ANTHROPIC_DEFAULT_BASE_URL
    try:
        from hermes_cli.config import load_config
        cfg = load_config()
        model_cfg = cfg.get("model")
        if isinstance(model_cfg, dict):
            cfg_provider = str(model_cfg.get("provider") or "").strip().lower()
            if cfg_provider == "anthropic":
                cfg_base_url = (model_cfg.get("base_url") or "").strip().rstrip("/")
                if cfg_base_url:
                    base_url = cfg_base_url
    except Exception:
        pass

    from agent.anthropic_adapter import _is_oauth_token
    is_oauth = _is_oauth_token(token)
    model = _API_KEY_PROVIDER_AUX_MODELS.get("anthropic", "claude-haiku-4-5-20251001")
    logger.debug("Auxiliary client: Anthropic native (%s) at %s (oauth=%s)", model, base_url, is_oauth)
    try:
        real_client = build_anthropic_client(token, base_url)
    except ImportError:
        # anthropic_adapter 模块导入正常但 SDK 本身缺失——
        # build_anthropic_client 在调用时当 _anthropic_sdk 为 None 时
        # 抛出 ImportError。视为不可用。
        return None, None
    return AnthropicAuxiliaryClient(real_client, model, token, base_url, is_oauth=is_oauth), model


_AUTO_PROVIDER_LABELS = {
    "_try_openrouter": "openrouter",
    "_try_nous": "nous",
    "_try_custom_endpoint": "local/custom",
    "_try_codex": "openai-codex",
    "_resolve_api_key_provider": "api-key",
}

_AGGREGATOR_PROVIDERS = frozenset({"openrouter", "nous"})

_MAIN_RUNTIME_FIELDS = ("provider", "model", "base_url", "api_key", "api_mode")


def _normalize_main_runtime(main_runtime: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """返回实时主运行时覆盖的清理副本。"""
    if not isinstance(main_runtime, dict):
        return {}
    normalized: Dict[str, str] = {}
    for field in _MAIN_RUNTIME_FIELDS:
        value = main_runtime.get(field)
        if isinstance(value, str) and value.strip():
            normalized[field] = value.strip()
    provider = normalized.get("provider")
    if provider:
        normalized["provider"] = provider.lower()
    return normalized


def _get_provider_chain() -> List[tuple]:
    """返回有序的提供者检测链。

    在调用时构建（而非模块级别），以确保测试中对
    ``_try_*`` 函数的补丁能被正确捕获。
    """
    return [
        ("openrouter", _try_openrouter),
        ("nous", _try_nous),
        ("local/custom", _try_custom_endpoint),
        ("openai-codex", _try_codex),
        ("api-key", _resolve_api_key_provider),
    ]


def _is_payment_error(exc: Exception) -> bool:
    """检测支付/额度/配额耗尽错误。

    对 HTTP 402（需要支付）以及消息表明账单耗尽
    而非速率限制的 429/其他错误返回 True。
    """
    status = getattr(exc, "status_code", None)
    if status == 402:
        return True
    err_lower = str(exc).lower()
    # OpenRouter 和其他提供者在 402 正文中包含 "credits" 或 "afford"，
    # 但有时将其包装在 429 或其他状态码中。
    if status in (402, 429, None):
        if any(kw in err_lower for kw in ("credits", "insufficient funds",
                                           "can only afford", "billing",
                                           "payment required")):
            return True
    return False


def _is_connection_error(exc: Exception) -> bool:
    """检测需要提供者回退的连接/网络错误。

    对表明提供者端点不可达的错误返回 True
    （DNS 失败、连接被拒绝、TLS 错误、超时）。这些
    不同于 API 错误（4xx/5xx），后者表明提供者可达
    但返回了错误。
    """
    from openai import APIConnectionError, APITimeoutError

    if isinstance(exc, (APIConnectionError, APITimeoutError)):
        return True
    # urllib3 / httpx / httpcore 连接错误
    err_type = type(exc).__name__
    if any(kw in err_type for kw in ("Connection", "Timeout", "DNS", "SSL")):
        return True
    err_lower = str(exc).lower()
    if any(kw in err_lower for kw in (
        "connection refused", "name or service not known",
        "no route to host", "network is unreachable",
        "timed out", "connection reset",
    )):
        return True
    return False


def _try_payment_fallback(
    failed_provider: str,
    task: str = None,
    reason: str = "payment error",
) -> Tuple[Optional[Any], Optional[str], str]:
    """在支付/额度或连接错误后尝试替代提供者。

    遍历标准自动检测链，跳过失败的提供者。

    返回:
        (client, model, provider_label) 或 (None, None, "") 如果没有回退。
    """
    # 标准化失败的提供者标签用于匹配。
    skip = failed_provider.lower().strip()
    # 如果主提供者映射到同一后端，也跳过 Step-1 的主提供者路径。
    # （例如 main_provider="openrouter" → 跳过链中的 "openrouter"）
    main_provider = _read_main_provider()
    skip_labels = {skip}
    if main_provider and main_provider.lower() in skip:
        skip_labels.add(main_provider.lower())
    # 将常见的 resolved_provider 值映射回链标签。
    _alias_to_label = {"openrouter": "openrouter", "nous": "nous",
                       "openai-codex": "openai-codex", "codex": "openai-codex",
                       "custom": "local/custom", "local/custom": "local/custom"}
    skip_chain_labels = {_alias_to_label.get(s, s) for s in skip_labels}

    tried = []
    for label, try_fn in _get_provider_chain():
        if label in skip_chain_labels:
            continue
        client, model = try_fn()
        if client is not None:
            logger.info(
                "Auxiliary %s: %s on %s — falling back to %s (%s)",
                task or "call", reason, failed_provider, label, model or "default",
            )
            return client, model, label
        tried.append(label)

    logger.warning(
        "Auxiliary %s: %s on %s and no fallback available (tried: %s)",
        task or "call", reason, failed_provider, ", ".join(tried),
    )
    return None, None, ""


def _resolve_auto(main_runtime: Optional[Dict[str, Any]] = None) -> Tuple[Optional[OpenAI], Optional[str]]:
    """完整的自动检测链。

    优先级：
      1. 如果用户的主提供者不是聚合器（OpenRouter / Nous），
         直接使用其主提供者 + 主模型。这确保使用 Alibaba、
         DeepSeek、ZAI 等的用户的辅助任务由其已有凭证的
         同一提供者处理——无需 OpenRouter 密钥。
      2. OpenRouter → Nous → 自定义 → Codex → API 密钥提供者（原始链）。
    """
    global auxiliary_is_nous, _stale_base_url_warned
    auxiliary_is_nous = False  # 重置——_try_nous() 如果胜出会设置为 True
    runtime = _normalize_main_runtime(main_runtime)
    runtime_provider = runtime.get("provider", "")
    runtime_model = runtime.get("model", "")
    runtime_base_url = runtime.get("base_url", "")
    runtime_api_key = runtime.get("api_key", "")
    runtime_api_mode = runtime.get("api_mode", "")

    # ── 当 OPENAI_BASE_URL 已设置但 config.yaml 使用命名提供者
    #    （非 'custom'）时，警告一次。这捕获了常见的"环境污染"
    #    场景：用户通过 `hermes model` 切换提供者，但旧的
    #    OPENAI_BASE_URL 残留在 ~/.hermes/.env 中。──
    if not _stale_base_url_warned:
        _env_base = os.getenv("OPENAI_BASE_URL", "").strip()
        _cfg_provider = runtime_provider or _read_main_provider()
        if (_env_base and _cfg_provider
                and _cfg_provider != "custom"
                and not _cfg_provider.startswith("custom:")):
            logger.warning(
                "OPENAI_BASE_URL is set (%s) but model.provider is '%s'. "
                "Auxiliary clients may route to the wrong endpoint. "
                "Run: hermes model to reconfigure, or remove "
                "OPENAI_BASE_URL from ~/.hermes/.env",
                _env_base, _cfg_provider,
            )
            _stale_base_url_warned = True

    # ── 步骤 1: 非聚合器主提供者 → 直接使用主模型 ──
    main_provider = runtime_provider or _read_main_provider()
    main_model = runtime_model or _read_main_model()
    if (main_provider and main_model
            and main_provider not in _AGGREGATOR_PROVIDERS
            and main_provider not in ("auto", "")):
        resolved_provider = main_provider
        explicit_base_url = None
        explicit_api_key = None
        if runtime_base_url and (main_provider == "custom" or main_provider.startswith("custom:")):
            resolved_provider = "custom"
            explicit_base_url = runtime_base_url
            explicit_api_key = runtime_api_key or None
        client, resolved = resolve_provider_client(
            resolved_provider,
            main_model,
            explicit_base_url=explicit_base_url,
            explicit_api_key=explicit_api_key,
            api_mode=runtime_api_mode or None,
        )
        if client is not None:
            logger.info("Auxiliary auto-detect: using main provider %s (%s)",
                        main_provider, resolved or main_model)
            return client, resolved or main_model

    # ── 步骤 2: 聚合器 / 回退链 ──────────────────────────────
    tried = []
    for label, try_fn in _get_provider_chain():
        client, model = try_fn()
        if client is not None:
            if tried:
                logger.info("Auxiliary auto-detect: using %s (%s) — skipped: %s",
                            label, model or "default", ", ".join(tried))
            else:
                logger.info("Auxiliary auto-detect: using %s (%s)", label, model or "default")
            return client, model
        tried.append(label)
    logger.warning("Auxiliary auto-detect: no provider available (tried: %s). "
                   "Compression, summarization, and memory flush will not work. "
                   "Set OPENROUTER_API_KEY or configure a local model in config.yaml.",
                   ", ".join(tried))
    return None, None


# ── 集中式提供者路由器 ─────────────────────────────────────────────
#
# resolve_provider_client() 是给定 (provider, model) 对创建正确配置客户端的
# 唯一入口点。它处理认证查找、base URL 解析、提供者特定头部，
# 以及 API 格式差异（Chat Completions vs Responses API for Codex）。
#
# 所有辅助使用代码应通过此函数或下面的公共辅助函数——
# 永远不要临时查找认证环境变量。


def _to_async_client(sync_client, model: str):
    """将同步客户端转换为其异步对应物，保留 Codex 路由。"""
    from openai import AsyncOpenAI

    if isinstance(sync_client, CodexAuxiliaryClient):
        return AsyncCodexAuxiliaryClient(sync_client), model
    if isinstance(sync_client, AnthropicAuxiliaryClient):
        return AsyncAnthropicAuxiliaryClient(sync_client), model
    try:
        from agent.copilot_acp_client import CopilotACPClient
        if isinstance(sync_client, CopilotACPClient):
            return sync_client, model
    except ImportError:
        pass

    async_kwargs = {
        "api_key": sync_client.api_key,
        "base_url": str(sync_client.base_url),
    }
    base_lower = str(sync_client.base_url).lower()
    if "openrouter" in base_lower:
        async_kwargs["default_headers"] = dict(_OR_HEADERS)
    elif "api.githubcopilot.com" in base_lower:
        from hermes_cli.models import copilot_default_headers

        async_kwargs["default_headers"] = copilot_default_headers()
    elif "api.kimi.com" in base_lower:
        async_kwargs["default_headers"] = {"User-Agent": "KimiCLI/1.30.0"}
    return AsyncOpenAI(**async_kwargs), model


def _normalize_resolved_model(model_name: Optional[str], provider: str) -> Optional[str]:
    """为将接收请求的提供者标准化已解析的模型名称。"""
    if not model_name:
        return model_name
    try:
        from hermes_cli.model_normalize import normalize_model_for_provider

        return normalize_model_for_provider(model_name, provider)
    except Exception:
        return model_name


def resolve_provider_client(
    provider: str,
    model: str = None,
    async_mode: bool = False,
    raw_codex: bool = False,
    explicit_base_url: str = None,
    explicit_api_key: str = None,
    api_mode: str = None,
    main_runtime: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[Any], Optional[str]]:
    """中央路由器：给定提供者名称和可选模型，返回一个
    配置了正确认证、base URL 和 API 格式的客户端。

    返回的客户端始终暴露 ``.chat.completions.create()``——对于
    Codex/Responses API 提供者，适配器透明地处理转换。

    参数:
        provider: 提供者标识符。可选值：
            "openrouter"、"nous"、"openai-codex"（或 "codex"）、
            "zai"、"kimi-coding"、"minimax"、"minimax-cn"、
            "custom"（OPENAI_BASE_URL + OPENAI_API_KEY）、
            "auto"（完整自动检测链）。
        model: 模型标识符覆盖。如果为 None，使用提供者的默认辅助模型。
        async_mode: 如果为 True，返回异步兼容客户端。
        raw_codex: 如果为 True，对 Codex 提供者返回原始 OpenAI 客户端
            而非包装在 CodexAuxiliaryClient 中。当调用方需要直接访问
            responses.stream() 时使用（例如主代理循环）。
        explicit_base_url: 可选的直接 OpenAI 兼容端点。
        explicit_api_key: 与 explicit_base_url 配对的可选 API 密钥。
        api_mode: API 模式覆盖。可选值："chat_completions"、
            "codex_responses" 或 None（自动检测）。设置为
            "codex_responses" 时，客户端被包装在
            CodexAuxiliaryClient 中以通过 Responses API 路由。

    返回:
        (client, resolved_model)，如果认证不可用则为 (None, None)。
    """
    _validate_proxy_env_urls()
    # 标准化别名
    provider = _normalize_aux_provider(provider)

    def _needs_codex_wrap(client_obj, base_url_str: str, model_str: str) -> bool:
        """判断纯 OpenAI 客户端是否需要为 Responses API 进行包装。

        当 api_mode 显式为 "codex_responses" 时返回 True，或当
        自动检测（api.openai.com + codex 系列模型）建议时也返回 True。
        已包装的客户端（CodexAuxiliaryClient）会被跳过。
        """
        if isinstance(client_obj, CodexAuxiliaryClient):
            return False
        if raw_codex:
            return False
        if api_mode == "codex_responses":
            return True
        # 自动检测：api.openai.com + codex 模型名称模式
        if api_mode and api_mode != "codex_responses":
            return False  # 显式的非 codex 模式
        normalized_base = (base_url_str or "").strip().lower()
        if "api.openai.com" in normalized_base and "openrouter" not in normalized_base:
            model_lower = (model_str or "").lower()
            if "codex" in model_lower:
                return True
        return False

    def _wrap_if_needed(client_obj, final_model_str: str, base_url_str: str = ""):
        """如果需要 Responses API，将纯 OpenAI 客户端包装在 CodexAuxiliaryClient 中。"""
        if _needs_codex_wrap(client_obj, base_url_str, final_model_str):
            logger.debug(
                "resolve_provider_client: wrapping client in CodexAuxiliaryClient "
                "(api_mode=%s, model=%s, base_url=%s)",
                api_mode or "auto-detected", final_model_str,
                base_url_str[:60] if base_url_str else "")
            return CodexAuxiliaryClient(client_obj, final_model_str)
        return client_obj

    # ── Auto: 按优先级顺序尝试所有提供者 ────────────────────
    if provider == "auto":
        client, resolved = _resolve_auto(main_runtime=main_runtime)
        if client is None:
            return None, None
        # 当自动检测落在非 OpenRouter 提供者上（例如本地服务器）时，
        # OpenRouter 格式的模型覆盖（如 "google/gemini-3-flash-preview"）
        # 不会工作。丢弃它并使用提供者自己的默认模型。
        if model and "/" in model and resolved and "/" not in resolved:
            logger.debug(
                "Dropping OpenRouter-format model %r for non-OpenRouter "
                "auxiliary provider (using %r instead)", model, resolved)
            model = None
        final_model = model or resolved
        return (_to_async_client(client, final_model) if async_mode
                else (client, final_model))

    # ── OpenRouter ───────────────────────────────────────────────────
    if provider == "openrouter":
        client, default = _try_openrouter()
        if client is None:
            logger.warning("resolve_provider_client: openrouter requested "
                           "but OPENROUTER_API_KEY not set")
            return None, None
        final_model = _normalize_resolved_model(model or default, provider)
        return (_to_async_client(client, final_model) if async_mode
                else (client, final_model))

    # ── Nous Portal (OAuth) ──────────────────────────────────────────
    if provider == "nous":
        client, default = _try_nous()
        if client is None:
            logger.warning("resolve_provider_client: nous requested "
                           "but Nous Portal not configured (run: hermes auth)")
            return None, None
        final_model = _normalize_resolved_model(model or default, provider)
        return (_to_async_client(client, final_model) if async_mode
                else (client, final_model))

    # ── OpenAI Codex (OAuth → Responses API) ─────────────────────────
    if provider == "openai-codex":
        if raw_codex:
            # 返回原始 OpenAI 客户端，供需要直接访问
            # responses.stream() 的调用方使用（例如主代理循环）。
            codex_token = _read_codex_access_token()
            if not codex_token:
                logger.warning("resolve_provider_client: openai-codex requested "
                               "but no Codex OAuth token found (run: hermes model)")
                return None, None
            final_model = _normalize_resolved_model(model or _CODEX_AUX_MODEL, provider)
            raw_client = OpenAI(api_key=codex_token, base_url=_CODEX_AUX_BASE_URL)
            return (raw_client, final_model)
        # 标准路径：包装在 CodexAuxiliaryClient 适配器中
        client, default = _try_codex()
        if client is None:
            logger.warning("resolve_provider_client: openai-codex requested "
                           "but no Codex OAuth token found (run: hermes model)")
            return None, None
        final_model = _normalize_resolved_model(model or default, provider)
        return (_to_async_client(client, final_model) if async_mode
                else (client, final_model))

    # ── 自定义端点 (OPENAI_BASE_URL + OPENAI_API_KEY) ───────────
    if provider == "custom":
        if explicit_base_url:
            custom_base = explicit_base_url.strip()
            custom_key = (
                (explicit_api_key or "").strip()
                or os.getenv("OPENAI_API_KEY", "").strip()
                or "no-key-required"  # 本地服务器不需要认证
            )
            if not custom_base:
                logger.warning(
                    "resolve_provider_client: explicit custom endpoint requested "
                    "but base_url is empty"
                )
                return None, None
            final_model = _normalize_resolved_model(
                model or _read_main_model() or "gpt-4o-mini",
                provider,
            )
            extra = {}
            if "api.kimi.com" in custom_base.lower():
                extra["default_headers"] = {"User-Agent": "KimiCLI/1.30.0"}
            elif "api.githubcopilot.com" in custom_base.lower():
                from hermes_cli.models import copilot_default_headers
                extra["default_headers"] = copilot_default_headers()
            client = OpenAI(api_key=custom_key, base_url=custom_base, **extra)
            client = _wrap_if_needed(client, final_model, custom_base)
            return (_to_async_client(client, final_model) if async_mode
                    else (client, final_model))
        # 先尝试自定义，然后 codex，最后 API 密钥提供者
        for try_fn in (_try_custom_endpoint, _try_codex,
                       _resolve_api_key_provider):
            client, default = try_fn()
            if client is not None:
                final_model = _normalize_resolved_model(model or default, provider)
                _cbase = str(getattr(client, "base_url", "") or "")
                client = _wrap_if_needed(client, final_model, _cbase)
                return (_to_async_client(client, final_model) if async_mode
                        else (client, final_model))
        logger.warning("resolve_provider_client: custom/main requested "
                       "but no endpoint credentials found")
        return None, None

    # ── 命名自定义提供者（config.yaml custom_providers 列表）───
    try:
        from hermes_cli.runtime_provider import _get_named_custom_provider
        custom_entry = _get_named_custom_provider(provider)
        if custom_entry:
            custom_base = custom_entry.get("base_url", "").strip()
            custom_key = custom_entry.get("api_key", "").strip()
            custom_key_env = custom_entry.get("key_env", "").strip()
            if not custom_key and custom_key_env:
                custom_key = os.getenv(custom_key_env, "").strip()
            custom_key = custom_key or "no-key-required"
            if custom_base:
                final_model = _normalize_resolved_model(
                    model or custom_entry.get("model") or _read_main_model() or "gpt-4o-mini",
                    provider,
                )
                client = OpenAI(api_key=custom_key, base_url=custom_base)
                client = _wrap_if_needed(client, final_model, custom_base)
                logger.debug(
                    "resolve_provider_client: named custom provider %r (%s)",
                    provider, final_model)
                return (_to_async_client(client, final_model) if async_mode
                        else (client, final_model))
            logger.warning(
                "resolve_provider_client: named custom provider %r has no base_url",
                provider)
            return None, None
    except ImportError:
        pass

    # ── 来自 PROVIDER_REGISTRY 的 API 密钥提供者 ─────────────────────
    try:
        from hermes_cli.auth import (
            PROVIDER_REGISTRY,
            resolve_api_key_provider_credentials,
            resolve_external_process_provider_credentials,
        )
    except ImportError:
        logger.debug("hermes_cli.auth not available for provider %s", provider)
        return None, None

    pconfig = PROVIDER_REGISTRY.get(provider)
    if pconfig is None:
        logger.warning("resolve_provider_client: unknown provider %r", provider)
        return None, None

    if pconfig.auth_type == "api_key":
        if provider == "anthropic":
            client, default_model = _try_anthropic()
            if client is None:
                logger.warning("resolve_provider_client: anthropic requested but no Anthropic credentials found")
                return None, None
            final_model = _normalize_resolved_model(model or default_model, provider)
            return (_to_async_client(client, final_model) if async_mode else (client, final_model))

        creds = resolve_api_key_provider_credentials(provider)
        api_key = str(creds.get("api_key", "")).strip()
        if not api_key:
            tried_sources = list(pconfig.api_key_env_vars)
            if provider == "copilot":
                tried_sources.append("gh auth token")
            logger.debug("resolve_provider_client: provider %s has no API "
                         "key configured (tried: %s)",
                         provider, ", ".join(tried_sources))
            return None, None

        base_url = _to_openai_base_url(
            str(creds.get("base_url", "")).strip().rstrip("/") or pconfig.inference_base_url
        )

        default_model = _API_KEY_PROVIDER_AUX_MODELS.get(provider, "")
        final_model = _normalize_resolved_model(model or default_model, provider)

        # 提供者特定头部
        headers = {}
        if "api.kimi.com" in base_url.lower():
            headers["User-Agent"] = "KimiCLI/1.30.0"
        elif "api.githubcopilot.com" in base_url.lower():
            from hermes_cli.models import copilot_default_headers

            headers.update(copilot_default_headers())

        client = OpenAI(api_key=api_key, base_url=base_url,
                        **({"default_headers": headers} if headers else {}))

        # Copilot GPT-5+ 模型（gpt-5-mini 除外）需要 Responses
        # API——它们无法通过 /chat/completions 访问。将纯客户端
        # 包装在 CodexAuxiliaryClient 中，使 call_llm() 透明地
        # 通过 responses.stream() 路由。
        if provider == "copilot" and final_model and not raw_codex:
            try:
                from hermes_cli.models import _should_use_copilot_responses_api
                if _should_use_copilot_responses_api(final_model):
                    logger.debug(
                        "resolve_provider_client: copilot model %s needs "
                        "Responses API — wrapping with CodexAuxiliaryClient",
                        final_model)
                    client = CodexAuxiliaryClient(client, final_model)
            except ImportError:
                pass

        # 为任何 API 密钥提供者兑现 api_mode（例如使用 codex 系列模型
        # 的直接 OpenAI）。上面的 copilot 特定包装处理了
        # copilot；这覆盖了通用情况（#6800）。
        client = _wrap_if_needed(client, final_model, base_url)

        logger.debug("resolve_provider_client: %s (%s)", provider, final_model)
        return (_to_async_client(client, final_model) if async_mode
                else (client, final_model))

    if pconfig.auth_type == "external_process":
        creds = resolve_external_process_provider_credentials(provider)
        final_model = _normalize_resolved_model(model or _read_main_model(), provider)
        if provider == "copilot-acp":
            api_key = str(creds.get("api_key", "")).strip()
            base_url = str(creds.get("base_url", "")).strip()
            command = str(creds.get("command", "")).strip() or None
            args = list(creds.get("args") or [])
            if not final_model:
                logger.warning(
                    "resolve_provider_client: copilot-acp requested but no model "
                    "was provided or configured"
                )
                return None, None
            if not api_key or not base_url:
                logger.warning(
                    "resolve_provider_client: copilot-acp requested but external "
                    "process credentials are incomplete"
                )
                return None, None
            from agent.copilot_acp_client import CopilotACPClient

            client = CopilotACPClient(
                api_key=api_key,
                base_url=base_url,
                command=command,
                args=args,
            )
            logger.debug("resolve_provider_client: %s (%s)", provider, final_model)
            return (_to_async_client(client, final_model) if async_mode
                    else (client, final_model))
        logger.warning("resolve_provider_client: external-process provider %s not "
                       "directly supported", provider)
        return None, None

    elif pconfig.auth_type in ("oauth_device_code", "oauth_external"):
        # OAuth 提供者——通过它们的特定 try 函数路由
        if provider == "nous":
            return resolve_provider_client("nous", model, async_mode)
        if provider == "openai-codex":
            return resolve_provider_client("openai-codex", model, async_mode)
        # 其他 OAuth 提供者不直接支持
        logger.warning("resolve_provider_client: OAuth provider %s not "
                       "directly supported, try 'auto'", provider)
        return None, None

    logger.warning("resolve_provider_client: unhandled auth_type %s for %s",
                   pconfig.auth_type, provider)
    return None, None


# ── 公共 API ──────────────────────────────────────────────────────────────

def get_text_auxiliary_client(
    task: str = "",
    *,
    main_runtime: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[OpenAI], Optional[str]]:
    """返回 (client, default_model_slug) 用于纯文本辅助任务。

    参数:
        task: 可选的任务名称（"compression"、"web_extract"）用于检查
              任务特定的提供者覆盖。

    调用方可通过 config.yaml 覆盖返回的模型
    （例如 auxiliary.compression.model、auxiliary.web_extract.model）。
    """
    provider, model, base_url, api_key, api_mode = _resolve_task_provider_model(task or None)
    return resolve_provider_client(
        provider,
        model=model,
        explicit_base_url=base_url,
        explicit_api_key=api_key,
        api_mode=api_mode,
        main_runtime=main_runtime,
    )


def get_async_text_auxiliary_client(task: str = "", *, main_runtime: Optional[Dict[str, Any]] = None):
    """返回 (async_client, model_slug) 用于异步使用者。

    对标准提供者返回 (AsyncOpenAI, model)。对 Codex 返回
    (AsyncCodexAuxiliaryClient, model) 用于包装 Responses API。
    当无提供者可用时返回 (None, None)。
    """
    provider, model, base_url, api_key, api_mode = _resolve_task_provider_model(task or None)
    return resolve_provider_client(
        provider,
        model=model,
        async_mode=True,
        explicit_base_url=base_url,
        explicit_api_key=api_key,
        api_mode=api_mode,
        main_runtime=main_runtime,
    )


_VISION_AUTO_PROVIDER_ORDER = (
    "openrouter",
    "nous",
)


def _normalize_vision_provider(provider: Optional[str]) -> str:
    return _normalize_aux_provider(provider)


def _resolve_strict_vision_backend(provider: str) -> Tuple[Optional[Any], Optional[str]]:
    provider = _normalize_vision_provider(provider)
    if provider == "openrouter":
        return _try_openrouter()
    if provider == "nous":
        return _try_nous(vision=True)
    if provider == "openai-codex":
        return _try_codex()
    if provider == "anthropic":
        return _try_anthropic()
    if provider == "custom":
        return _try_custom_endpoint()
    return None, None


def _strict_vision_backend_available(provider: str) -> bool:
    return _resolve_strict_vision_backend(provider)[0] is not None


def get_available_vision_backends() -> List[str]:
    """返回当前按自动选择顺序排列的可用视觉后端。

    顺序：活跃提供者 → OpenRouter → Nous → 停止。这是
    设置、工具门控和视觉任务运行时自动路由的唯一真相来源。
    """
    available: List[str] = []
    # 1. 活跃提供者——如果用户配置了提供者，优先尝试。
    main_provider = _read_main_provider()
    if main_provider and main_provider not in ("auto", ""):
        if main_provider in _VISION_AUTO_PROVIDER_ORDER:
            if _strict_vision_backend_available(main_provider):
                available.append(main_provider)
        else:
            client, _ = resolve_provider_client(main_provider, _read_main_model())
            if client is not None:
                available.append(main_provider)
    # 2. OpenRouter, 3. Nous——如果已被主提供者覆盖则跳过。
    for p in _VISION_AUTO_PROVIDER_ORDER:
        if p not in available and _strict_vision_backend_available(p):
            available.append(p)
    return available


def resolve_vision_provider_client(
    provider: Optional[str] = None,
    model: Optional[str] = None,
    *,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    async_mode: bool = False,
) -> Tuple[Optional[str], Optional[Any], Optional[str]]:
    """解析实际用于视觉任务的客户端。

    直接端点覆盖优先于提供者选择。显式提供者覆盖仍使用
    通用提供者路由器处理非标准后端，使用户可以有意强制使用
    实验性提供者。自动模式保持保守，仅尝试已知目前能工作的视觉后端。
    """
    requested, resolved_model, resolved_base_url, resolved_api_key, resolved_api_mode = _resolve_task_provider_model(
        "vision", provider, model, base_url, api_key
    )
    requested = _normalize_vision_provider(requested)

    def _finalize(resolved_provider: str, sync_client: Any, default_model: Optional[str]):
        if sync_client is None:
            return resolved_provider, None, None
        final_model = resolved_model or default_model
        if async_mode:
            async_client, async_model = _to_async_client(sync_client, final_model)
            return resolved_provider, async_client, async_model
        return resolved_provider, sync_client, final_model

    if resolved_base_url:
        client, final_model = resolve_provider_client(
            "custom",
            model=resolved_model,
            async_mode=async_mode,
            explicit_base_url=resolved_base_url,
            explicit_api_key=resolved_api_key,
            api_mode=resolved_api_mode,
        )
        if client is None:
            return "custom", None, None
        return "custom", client, final_model

    if requested == "auto":
        # 视觉自动检测顺序：
        #   1. 活跃提供者 + 模型（用户的主聊天配置）
        #   2. OpenRouter（已知支持视觉的默认模型）
        #   3. Nous Portal（已知支持视觉的默认模型）
        #   4. 停止
        main_provider = _read_main_provider()
        main_model = _read_main_model()
        if main_provider and main_provider not in ("auto", ""):
            if main_provider in _VISION_AUTO_PROVIDER_ORDER:
                # 已知的严格后端——使用其默认值。
                sync_client, default_model = _resolve_strict_vision_backend(main_provider)
                if sync_client is not None:
                    return _finalize(main_provider, sync_client, default_model)
            else:
                # 特殊提供者（DeepSeek、Alibaba、Xiaomi、命名自定义等）
                # 如果可用则使用提供者特定的视觉模型，否则使用主模型。
                vision_model = _PROVIDER_VISION_MODELS.get(main_provider, main_model)
                rpc_client, rpc_model = resolve_provider_client(
                    main_provider, vision_model,
                    api_mode=resolved_api_mode)
                if rpc_client is not None:
                    logger.info(
                        "Vision auto-detect: using active provider %s (%s)",
                        main_provider, rpc_model or vision_model,
                    )
                    return _finalize(
                        main_provider, rpc_client, rpc_model or vision_model)

        # 回退到聚合器。
        for candidate in _VISION_AUTO_PROVIDER_ORDER:
            if candidate == main_provider:
                continue  # already tried above
            sync_client, default_model = _resolve_strict_vision_backend(candidate)
            if sync_client is not None:
                return _finalize(candidate, sync_client, default_model)

        logger.debug("Auxiliary vision client: none available")
        return None, None, None

    if requested in _VISION_AUTO_PROVIDER_ORDER:
        sync_client, default_model = _resolve_strict_vision_backend(requested)
        return _finalize(requested, sync_client, default_model)

    client, final_model = _get_cached_client(requested, resolved_model, async_mode,
                                             api_mode=resolved_api_mode)
    if client is None:
        return requested, None, None
    return requested, client, final_model


def get_auxiliary_extra_body() -> dict:
    """返回辅助 API 调用的 extra_body kwargs。

    当辅助客户端由 Nous Portal 支持时包含 Nous Portal 产品标签。
    否则返回空字典。
    """
    return dict(NOUS_EXTRA_BODY) if auxiliary_is_nous else {}


def auxiliary_max_tokens_param(value: int) -> dict:
    """返回辅助客户端提供者的正确 max tokens 参数。

    OpenRouter 和本地模型使用 'max_tokens'。直接使用较新模型
    （gpt-4o、o 系列、gpt-5+）的 OpenAI 需要 'max_completion_tokens'。
    Codex 适配器内部处理 max_tokens 转换，因此我们也对其使用 max_tokens。
    """
    custom_base = _current_custom_base_url()
    or_key = os.getenv("OPENROUTER_API_KEY")
    # 仅对直接 OpenAI 自定义端点使用 max_completion_tokens
    if (not or_key
            and _read_nous_auth() is None
            and "api.openai.com" in custom_base.lower()):
        return {"max_completion_tokens": value}
    return {"max_tokens": value}


# ── 集中式 LLM 调用 API ────────────────────────────────────────────────
#
# call_llm() 和 async_call_llm() 管理完整的请求生命周期：
#   1. 从任务配置（或显式参数）解析提供者 + 模型
#   2. 获取或创建该提供者的缓存客户端
#   3. 为提供者 + 模型格式化请求参数（max_tokens 处理等）
#   4. 发起 API 调用
#   5. 返回响应
#
# 每个辅助 LLM 使用者都应使用这些函数，而不是手动
# 构造客户端并调用 .chat.completions.create()。

# 客户端缓存：(provider, async_mode, base_url, api_key, api_mode, runtime_key) -> (client, default_model, loop)
# 注意：loop 标识不是缓存键的一部分。异步缓存命中时我们检查
# 缓存的 loop 是否是*当前* loop；如果不是，过期条目将被
# 原地替换。这将缓存增长限制在每个唯一提供者配置一个条目，
# 而不是之前导致长时间运行网关进程中 fd 无限积累的
# 每 (配置 × 事件循环) 一个条目（#10200）。
_client_cache: Dict[tuple, tuple] = {}
_client_cache_lock = threading.Lock()
_CLIENT_CACHE_MAX_SIZE = 64  # 安全带——超过时淘汰最旧的


def neuter_async_httpx_del() -> None:
    """将 ``AsyncHttpxClientWrapper.__del__`` 猴子补丁为空操作。

    OpenAI SDK 的 ``AsyncHttpxClientWrapper.__del__`` 通过
    ``asyncio.get_running_loop().create_task()`` 调度
    ``self.aclose()``。当 ``AsyncOpenAI`` 客户端在
    prompt_toolkit 的事件循环运行时被垃圾回收（常见的 CLI 空闲状态），
    ``aclose()`` 任务在 prompt_toolkit 的循环上运行，但底层
    TCP 传输绑定在*另一个*循环（客户端最初创建时的工作线程循环）上。
    如果该循环已关闭或其线程已死亡，传输的
    ``self._loop.call_soon()`` 会抛出 ``RuntimeError("Event loop is
    closed")``，prompt_toolkit 将其显示为 "Unhandled exception
    in event loop ... Press ENTER to continue..."。

    使 ``__del__`` 无效化是安全的，因为：
    - 缓存的客户端在过期循环检测时通过 ``_force_close_async_httpx``
      显式清理，退出时通过 ``shutdown_cached_clients`` 清理。
    - 未缓存客户端的 TCP 连接在进程退出时由操作系统清理。
    - OpenAI SDK 本身将此标记为 TODO（``# TODO(someday):
      support non asyncio runtimes here``）。

    在 CLI 启动时调用一次，在创建任何 ``AsyncOpenAI`` 客户端之前。
    """
    try:
        from openai._base_client import AsyncHttpxClientWrapper
        AsyncHttpxClientWrapper.__del__ = lambda self: None  # type: ignore[assignment]
    except (ImportError, AttributeError):
        pass  # 如果 SDK 更改了其内部结构则优雅降级


def _force_close_async_httpx(client: Any) -> None:
    """将 AsyncOpenAI 客户端内部的 httpx AsyncClient 标记为已关闭。

    这防止 ``AsyncHttpxClientWrapper.__del__`` 在（可能已关闭的）
    事件循环上调度 ``aclose()``，从而导致
    ``RuntimeError: Event loop is closed`` → prompt_toolkit 的
    "Press ENTER to continue..." 处理器。

    我们故意不运行完整的异步关闭路径——连接将在进程退出时
    由操作系统丢弃。
    """
    try:
        from httpx._client import ClientState
        inner = getattr(client, "_client", None)
        if inner is not None and not getattr(inner, "is_closed", True):
            inner._state = ClientState.CLOSED
    except Exception:
        pass


def shutdown_cached_clients() -> None:
    """关闭所有缓存的客户端（同步和异步）以防止事件循环错误。

    在 CLI 关闭期间调用，*在*事件循环关闭之前，以避免
    ``AsyncHttpxClientWrapper.__del__`` 在死循环上抛出异常。
    """
    import inspect

    with _client_cache_lock:
        for key, entry in list(_client_cache.items()):
            client = entry[0]
            if client is None:
                continue
            # 首先将任何异步 httpx 传输标记为已关闭（防止 __del__
            # 在死事件循环上调度 aclose()）。
            _force_close_async_httpx(client)
            # 同步客户端：干净地关闭 httpx 连接池。
            # 异步客户端：跳过——我们已在上面使 __del__ 无效化。
            try:
                close_fn = getattr(client, "close", None)
                if close_fn and not inspect.iscoroutinefunction(close_fn):
                    close_fn()
            except Exception:
                pass
        _client_cache.clear()


def cleanup_stale_async_clients() -> None:
    """强制关闭事件循环已关闭的缓存异步客户端。

    在每个代理回合后调用，主动清理过期客户端，
    防止 GC 在它们上触发 ``AsyncHttpxClientWrapper.__del__``。
    这是纵深防御——主要修复是 ``neuter_async_httpx_del``
    它完全禁用了 ``__del__``。
    """
    with _client_cache_lock:
        stale_keys = []
        for key, entry in _client_cache.items():
            client, _default, cached_loop = entry
            if cached_loop is not None and cached_loop.is_closed():
                _force_close_async_httpx(client)
                stale_keys.append(key)
        for key in stale_keys:
            del _client_cache[key]


def _is_openrouter_client(client: Any) -> bool:
    for obj in (client, getattr(client, "_client", None), getattr(client, "client", None)):
        if obj and "openrouter" in str(getattr(obj, "base_url", "") or "").lower():
            return True
    return False


def _compat_model(client: Any, model: Optional[str], cached_default: Optional[str]) -> Optional[str]:
    """对非 OpenRouter 客户端丢弃带 '/' 的 OpenRouter 格式模型标识符。

    镜像 resolve_provider_client() 中在缓存命中时被跳过的保护。
    """
    if model and "/" in model and not _is_openrouter_client(client):
        return cached_default
    return model or cached_default


def _get_cached_client(
    provider: str,
    model: str = None,
    async_mode: bool = False,
    base_url: str = None,
    api_key: str = None,
    api_mode: str = None,
    main_runtime: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[Any], Optional[str]]:
    """获取或创建给定提供者的缓存客户端。

    异步客户端（AsyncOpenAI）内部使用 httpx.AsyncClient，它
    绑定到客户端创建时的当前事件循环。在*不同*循环上使用
    此类客户端会导致死锁或 RuntimeError。为防止跨循环问题，
    缓存在每次异步命中时验证缓存的循环是当前*打开的*循环。
    如果循环已更改（例如新的网关工作线程循环），过期条目
    将被原地替换而非创建额外条目。

    这将缓存大小限制在每个唯一提供者配置一个条目，
    防止之前长时间运行网关中回收的工作线程创建
    无限条目导致的 fd 耗尽（#10200）。
    """
    # 解析异步客户端的当前事件循环，以便验证缓存条目。
    # Loop 标识不在缓存键中——而是在命中时检查缓存的
    # loop 是否仍是当前且打开的。这防止回收的工作线程循环
    # 导致的无限缓存增长，同时仍保证我们永远不会在错误的
    # 循环上重用客户端（会导致死锁，参见 #2681）。
    current_loop = None
    if async_mode:
        try:
            import asyncio as _aio
            current_loop = _aio.get_event_loop()
        except RuntimeError:
            pass
    runtime = _normalize_main_runtime(main_runtime)
    runtime_key = tuple(runtime.get(field, "") for field in _MAIN_RUNTIME_FIELDS) if provider == "auto" else ()
    cache_key = (provider, async_mode, base_url or "", api_key or "", api_mode or "", runtime_key)
    with _client_cache_lock:
        if cache_key in _client_cache:
            cached_client, cached_default, cached_loop = _client_cache[cache_key]
            if async_mode:
                # 验证：缓存的客户端必须绑定到当前、打开的循环。
                # 如果循环已更改或已关闭，内部的 httpx 传输已死——
                # 强制关闭并替换。
                loop_ok = (
                    cached_loop is not None
                    and cached_loop is current_loop
                    and not cached_loop.is_closed()
                )
                if loop_ok:
                    effective = _compat_model(cached_client, model, cached_default)
                    return cached_client, effective
                # 过期——淘汰并向下执行以创建新客户端。
                _force_close_async_httpx(cached_client)
                del _client_cache[cache_key]
            else:
                effective = _compat_model(cached_client, model, cached_default)
                return cached_client, effective
    # 在锁外构建
    client, default_model = resolve_provider_client(
        provider,
        model,
        async_mode,
        explicit_base_url=base_url,
        explicit_api_key=api_key,
        api_mode=api_mode,
        main_runtime=runtime,
    )
    if client is not None:
        # 对异步客户端，记住它们创建时的循环，以便
        # 稍后检测过期条目。
        bound_loop = current_loop
        with _client_cache_lock:
            if cache_key not in _client_cache:
                # 安全带：如果缓存增长超过最大值，淘汰
                # 最旧的条目（FIFO——dict 保持插入顺序）。
                while len(_client_cache) >= _CLIENT_CACHE_MAX_SIZE:
                    evict_key, evict_entry = next(iter(_client_cache.items()))
                    _force_close_async_httpx(evict_entry[0])
                    del _client_cache[evict_key]
                _client_cache[cache_key] = (client, default_model, bound_loop)
            else:
                client, default_model, _ = _client_cache[cache_key]
    return client, model or default_model


def _resolve_task_provider_model(
    task: str = None,
    provider: str = None,
    model: str = None,
    base_url: str = None,
    api_key: str = None,
) -> Tuple[str, Optional[str], Optional[str], Optional[str], Optional[str]]:
    """确定调用的提供者 + 模型。

    优先级：
      1. 显式的 provider/model/base_url/api_key 参数（始终优先）
      2. 配置文件 (auxiliary.{task}.provider/model/base_url)
      3. "auto"（完整自动检测链）

    返回 (provider, model, base_url, api_key, api_mode)，其中 model 可能
    为 None（使用提供者默认值）。当设置了 base_url 时，provider 强制
    为 "custom" 并且任务使用该直接端点。api_mode 为
    "chat_completions"、"codex_responses" 或 None（自动检测）之一。
    """
    config = {}
    cfg_provider = None
    cfg_model = None
    cfg_base_url = None
    cfg_api_key = None
    cfg_api_mode = None

    if task:
        try:
            from hermes_cli.config import load_config
            config = load_config()
        except ImportError:
            config = {}

        aux = config.get("auxiliary", {}) if isinstance(config, dict) else {}
        task_config = aux.get(task, {}) if isinstance(aux, dict) else {}
        if not isinstance(task_config, dict):
            task_config = {}
        cfg_provider = str(task_config.get("provider", "")).strip() or None
        cfg_model = str(task_config.get("model", "")).strip() or None
        cfg_base_url = str(task_config.get("base_url", "")).strip() or None
        cfg_api_key = str(task_config.get("api_key", "")).strip() or None
        cfg_api_mode = str(task_config.get("api_mode", "")).strip() or None

    resolved_model = model or cfg_model
    resolved_api_mode = cfg_api_mode

    if base_url:
        return "custom", resolved_model, base_url, api_key, resolved_api_mode
    if provider:
        return provider, resolved_model, base_url, api_key, resolved_api_mode

    if task:
        # Config.yaml 是每任务覆盖的主要来源。
        if cfg_base_url:
            return "custom", resolved_model, cfg_base_url, cfg_api_key, resolved_api_mode
        if cfg_provider and cfg_provider != "auto":
            return cfg_provider, resolved_model, None, None, resolved_api_mode

        return "auto", resolved_model, None, None, resolved_api_mode

    return "auto", resolved_model, None, None, resolved_api_mode


_DEFAULT_AUX_TIMEOUT = 30.0


def _get_task_timeout(task: str, default: float = _DEFAULT_AUX_TIMEOUT) -> float:
    """从配置的 auxiliary.{task}.timeout 读取超时，回退到 *default*。"""
    if not task:
        return default
    try:
        from hermes_cli.config import load_config
        config = load_config()
    except ImportError:
        return default
    aux = config.get("auxiliary", {}) if isinstance(config, dict) else {}
    task_config = aux.get(task, {}) if isinstance(aux, dict) else {}
    raw = task_config.get("timeout")
    if raw is not None:
        try:
            return float(raw)
        except (ValueError, TypeError):
            pass
    return default


# ---------------------------------------------------------------------------
# Anthropic 兼容端点检测 + 图片块转换
# ---------------------------------------------------------------------------

# 使用 Anthropic 兼容端点的提供者（通过 OpenAI SDK 包装器）。
# 它们的图片内容块必须使用 Anthropic 格式，而非 OpenAI 格式。
_ANTHROPIC_COMPAT_PROVIDERS = frozenset({"minimax", "minimax-cn"})


def _is_anthropic_compat_endpoint(provider: str, base_url: str) -> bool:
    """检测端点是否期望 Anthropic 格式的内容块。

    对已知的 Anthropic 兼容提供者（MiniMax）以及 URL 路径中
    包含 ``/anthropic`` 的任何端点返回 True。
    """
    if provider in _ANTHROPIC_COMPAT_PROVIDERS:
        return True
    url_lower = (base_url or "").lower()
    return "/anthropic" in url_lower


def _convert_openai_images_to_anthropic(messages: list) -> list:
    """将 OpenAI ``image_url`` 内容块转换为 Anthropic ``image`` 块。

    仅处理具有列表类型内容且包含 ``image_url`` 块的消息；
    纯文本消息直接传递。
    """
    converted = []
    for msg in messages:
        content = msg.get("content")
        if not isinstance(content, list):
            converted.append(msg)
            continue
        new_content = []
        changed = False
        for block in content:
            if block.get("type") == "image_url":
                image_url_val = (block.get("image_url") or {}).get("url", "")
                if image_url_val.startswith("data:"):
                    # 解析 data URI: data:<media_type>;base64,<data>
                    header, _, b64data = image_url_val.partition(",")
                    media_type = "image/png"
                    if ":" in header and ";" in header:
                        media_type = header.split(":", 1)[1].split(";", 1)[0]
                    new_content.append({
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": b64data,
                        },
                    })
                else:
                    # 基于 URL 的图片
                    new_content.append({
                        "type": "image",
                        "source": {
                            "type": "url",
                            "url": image_url_val,
                        },
                    })
                changed = True
            else:
                new_content.append(block)
        converted.append({**msg, "content": new_content} if changed else msg)
    return converted



def _build_call_kwargs(
    provider: str,
    model: str,
    messages: list,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    tools: Optional[list] = None,
    timeout: float = 30.0,
    extra_body: Optional[dict] = None,
    base_url: Optional[str] = None,
) -> dict:
    """为 .chat.completions.create() 构建 kwargs，包含模型/提供者调整。"""
    kwargs: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "timeout": timeout,
    }

    # Opus 4.7+ 拒绝任何非默认的 temperature/top_p/top_k——在此
    # 静默丢弃，使硬编码 temperature 的辅助调用方（例如
    # flush_memories 的 0.3、结构化 JSON 提取的 0）不会在
    # 辅助模型切换到 4.7 时收到 400 错误。
    if temperature is not None:
        from agent.anthropic_adapter import _forbids_sampling_params
        if _forbids_sampling_params(model):
            temperature = None

    if temperature is not None:
        kwargs["temperature"] = temperature

    if max_tokens is not None:
        # Codex 适配器内部处理 max_tokens；OpenRouter/Nous 使用 max_tokens。
        # 直接使用较新模型的 OpenAI api.openai.com 需要 max_completion_tokens。
        if provider == "custom":
            custom_base = base_url or _current_custom_base_url()
            if "api.openai.com" in custom_base.lower():
                kwargs["max_completion_tokens"] = max_tokens
            else:
                kwargs["max_tokens"] = max_tokens
        else:
            kwargs["max_tokens"] = max_tokens

    if tools:
        kwargs["tools"] = tools

    # 提供者特定的 extra_body
    merged_extra = dict(extra_body or {})
    if provider == "nous" or auxiliary_is_nous:
        merged_extra.setdefault("tags", []).extend(["product=hermes-agent"])
    if merged_extra:
        kwargs["extra_body"] = merged_extra

    return kwargs


def _validate_llm_response(response: Any, task: str = None) -> Any:
    """验证 LLM 响应具有预期的 .choices[0].message 结构。

    通过清晰的错误快速失败，而不是让格式错误的负载
    传播到下游使用者，在那里它们会因误导性的
    AttributeError 崩溃（例如 "'str' object has no attribute 'choices'"）。

    参见 #7264。
    """
    if response is None:
        raise RuntimeError(
            f"Auxiliary {task or 'call'}: LLM returned None response"
        )
    # 允许来自适配器（CodexAuxiliaryClient、
    # AnthropicAuxiliaryClient）的 SimpleNamespace 响应——它们有 .choices[0].message。
    try:
        choices = response.choices
        if not choices or not hasattr(choices[0], "message"):
            raise AttributeError("missing choices[0].message")
    except (AttributeError, TypeError, IndexError) as exc:
        response_type = type(response).__name__
        response_preview = str(response)[:120]
        raise RuntimeError(
            f"Auxiliary {task or 'call'}: LLM returned invalid response "
            f"(type={response_type}): {response_preview!r}. "
            f"Expected object with .choices[0].message — check provider "
            f"adapter or custom endpoint compatibility."
        ) from exc
    return response


def call_llm(
    task: str = None,
    *,
    provider: str = None,
    model: str = None,
    base_url: str = None,
    api_key: str = None,
    main_runtime: Optional[Dict[str, Any]] = None,
    messages: list,
    temperature: float = None,
    max_tokens: int = None,
    tools: list = None,
    timeout: float = None,
    extra_body: dict = None,
) -> Any:
    """集中式同步 LLM 调用。

    解析提供者 + 模型（从任务配置、显式参数或自动检测），
    处理认证、请求格式化和模型特定的参数调整。

    参数:
        task: 辅助任务名称（"compression"、"vision"、"web_extract"、
              "session_search"、"skills_hub"、"mcp"、"flush_memories"）。
              从配置/环境读取 provider:model。如果设置了 provider 则忽略。
        provider: 显式提供者覆盖。
        model: 显式模型覆盖。
        messages: 聊天消息列表。
        temperature: 采样温度（None = 提供者默认值）。
        max_tokens: 最大输出 token 数（处理 max_tokens vs max_completion_tokens）。
        tools: 工具定义（用于函数调用）。
        timeout: 请求超时秒数（None = 从 auxiliary.{task}.timeout 配置读取）。
        extra_body: 额外的请求体字段。

    返回:
        带有 .choices[0].message.content 的响应对象

    抛出:
        RuntimeError: 如果没有配置提供者。
    """
    resolved_provider, resolved_model, resolved_base_url, resolved_api_key, resolved_api_mode = _resolve_task_provider_model(
        task, provider, model, base_url, api_key)

    if task == "vision":
        effective_provider, client, final_model = resolve_vision_provider_client(
            provider=resolved_provider if resolved_provider != "auto" else provider,
            model=resolved_model or model,
            base_url=resolved_base_url or base_url,
            api_key=resolved_api_key or api_key,
            async_mode=False,
        )
        if client is None and resolved_provider != "auto" and not resolved_base_url:
            logger.warning(
                "Vision provider %s unavailable, falling back to auto vision backends",
                resolved_provider,
            )
            effective_provider, client, final_model = resolve_vision_provider_client(
                provider="auto",
                model=resolved_model,
                async_mode=False,
            )
        if client is None:
            raise RuntimeError(
                f"No LLM provider configured for task={task} provider={resolved_provider}. "
                f"Run: hermes setup"
            )
        resolved_provider = effective_provider or resolved_provider
    else:
        client, final_model = _get_cached_client(
            resolved_provider,
            resolved_model,
            base_url=resolved_base_url,
            api_key=resolved_api_key,
            api_mode=resolved_api_mode,
            main_runtime=main_runtime,
        )
        if client is None:
            # 当用户显式选择了非 OpenRouter 提供者但未找到凭证时，
            # 快速失败，而不是静默地通过 OpenRouter 路由
            # （会导致令人困惑的 404）。
            _explicit = (resolved_provider or "").strip().lower()
            if _explicit and _explicit not in ("auto", "openrouter", "custom"):
                raise RuntimeError(
                    f"Provider '{_explicit}' is set in config.yaml but no API key "
                    f"was found. Set the {_explicit.upper()}_API_KEY environment "
                    f"variable, or switch to a different provider with `hermes model`."
                )
            # 对没有凭证的 auto/custom，尝试完整自动链
            # 而不是硬编码 OpenRouter（可能已耗尽）。
            # 传递 model=None 使每个提供者使用自己的默认值——
            # resolved_model 可能是 OpenRouter 格式的标识符，
            # 在其他提供者上不工作。
            if not resolved_base_url:
                logger.info("Auxiliary %s: provider %s unavailable, trying auto-detection chain",
                            task or "call", resolved_provider)
                client, final_model = _get_cached_client("auto", main_runtime=main_runtime)
        if client is None:
            raise RuntimeError(
                f"No LLM provider configured for task={task} provider={resolved_provider}. "
                f"Run: hermes setup")

    effective_timeout = timeout if timeout is not None else _get_task_timeout(task)

    # 记录即将执行的操作——使辅助操作可见
    _base_info = str(getattr(client, "base_url", resolved_base_url) or "")
    if task:
        logger.info("Auxiliary %s: using %s (%s)%s",
                     task, resolved_provider or "auto", final_model or "default",
                     f" at {_base_info}" if _base_info and "openrouter" not in _base_info else "")

    kwargs = _build_call_kwargs(
        resolved_provider, final_model, messages,
        temperature=temperature, max_tokens=max_tokens,
        tools=tools, timeout=effective_timeout, extra_body=extra_body,
        base_url=resolved_base_url)

    # 为 Anthropic 兼容端点（例如 MiniMax）转换图片块
    _client_base = str(getattr(client, "base_url", "") or "")
    if _is_anthropic_compat_endpoint(resolved_provider, _client_base):
        kwargs["messages"] = _convert_openai_images_to_anthropic(kwargs["messages"])

    # 处理 max_tokens vs max_completion_tokens 重试，然后支付回退。
    try:
        return _validate_llm_response(
            client.chat.completions.create(**kwargs), task)
    except Exception as first_err:
        err_str = str(first_err)
        if "max_tokens" in err_str or "unsupported_parameter" in err_str:
            kwargs.pop("max_tokens", None)
            kwargs["max_completion_tokens"] = max_tokens
            try:
                return _validate_llm_response(
                    client.chat.completions.create(**kwargs), task)
            except Exception as retry_err:
                # 如果 max_tokens 重试也遇到支付或连接错误，
                # 向下执行到下面的回退链。
                if not (_is_payment_error(retry_err) or _is_connection_error(retry_err)):
                    raise
                first_err = retry_err

        # ── 支付 / 额度耗尽回退 ──────────────────────
        # 当解析的提供者返回 402 或额度相关错误时，
        # 尝试替代提供者而不是放弃。这处理了用户耗尽
        # OpenRouter 额度但有 Codex OAuth 或其他提供者
        # 可用的常见情况。
        #
        # ── 连接错误回退 ────────────────────────────────
        # 当提供者端点不可达时（DNS 失败、连接被拒绝、
        # 超时），尝试替代提供者。这处理了认证有效但端点
        # 宕机的过期 Codex/OAuth 令牌，以及用户从未配置但
        # 被自动检测链选中的提供者。
        should_fallback = _is_payment_error(first_err) or _is_connection_error(first_err)
        # 仅在用户没有显式配置此任务的提供者时才尝试替代提供者。
        # 显式提供者 = 硬约束；auto（默认值）= 尽力回退链。（#7559）
        is_auto = resolved_provider in ("auto", "", None)
        if should_fallback and is_auto:
            reason = "payment error" if _is_payment_error(first_err) else "connection error"
            logger.info("Auxiliary %s: %s on %s (%s), trying fallback",
                        task or "call", reason, resolved_provider, first_err)
            fb_client, fb_model, fb_label = _try_payment_fallback(
                resolved_provider, task, reason=reason)
            if fb_client is not None:
                fb_kwargs = _build_call_kwargs(
                    fb_label, fb_model, messages,
                    temperature=temperature, max_tokens=max_tokens,
                    tools=tools, timeout=effective_timeout,
                    extra_body=extra_body)
                return _validate_llm_response(
                    fb_client.chat.completions.create(**fb_kwargs), task)
        raise


def extract_content_or_reasoning(response) -> str:
    """从 LLM 响应中提取内容，回退到推理字段。

    镜像主代理循环在推理模型（DeepSeek-R1、Qwen-QwQ 等）
    返回 ``content=None`` 且推理在结构化字段中时的行为。

    解析顺序：
      1. ``message.content`` —— 剥离内联 think/reasoning 块，检查
         是否有剩余的非空白文本。
      2. ``message.reasoning`` / ``message.reasoning_content`` —— 直接
         结构化推理字段（DeepSeek、Moonshot、Novita 等）。
      3. ``message.reasoning_details`` —— OpenRouter 统一数组格式。

    返回最佳可用文本，如果未找到则返回 ``""``。
    """
    import re

    msg = response.choices[0].message
    content = (msg.content or "").strip()

    if content:
        # 剥离内联 think/reasoning 块（镜像 _strip_think_blocks）
        cleaned = re.sub(
            r"<(?:think|thinking|reasoning|thought|REASONING_SCRATCHPAD)>"
            r".*?"
            r"</(?:think|thinking|reasoning|thought|REASONING_SCRATCHPAD)>",
            "", content, flags=re.DOTALL | re.IGNORECASE,
        ).strip()
        if cleaned:
            return cleaned

    # 内容为空或仅包含推理——尝试结构化推理字段
    reasoning_parts: list[str] = []
    for field in ("reasoning", "reasoning_content"):
        val = getattr(msg, field, None)
        if val and isinstance(val, str) and val.strip() and val not in reasoning_parts:
            reasoning_parts.append(val.strip())

    details = getattr(msg, "reasoning_details", None)
    if details and isinstance(details, list):
        for detail in details:
            if isinstance(detail, dict):
                summary = (
                    detail.get("summary")
                    or detail.get("content")
                    or detail.get("text")
                )
                if summary and summary not in reasoning_parts:
                    reasoning_parts.append(summary.strip() if isinstance(summary, str) else str(summary))

    if reasoning_parts:
        return "\n\n".join(reasoning_parts)

    return ""


async def async_call_llm(
    task: str = None,
    *,
    provider: str = None,
    model: str = None,
    base_url: str = None,
    api_key: str = None,
    messages: list,
    temperature: float = None,
    max_tokens: int = None,
    tools: list = None,
    timeout: float = None,
    extra_body: dict = None,
) -> Any:
    """集中式异步 LLM 调用。

    与 call_llm() 相同但为异步版本。完整文档见 call_llm()。
    """
    resolved_provider, resolved_model, resolved_base_url, resolved_api_key, resolved_api_mode = _resolve_task_provider_model(
        task, provider, model, base_url, api_key)

    if task == "vision":
        effective_provider, client, final_model = resolve_vision_provider_client(
            provider=resolved_provider if resolved_provider != "auto" else provider,
            model=resolved_model or model,
            base_url=resolved_base_url or base_url,
            api_key=resolved_api_key or api_key,
            async_mode=True,
        )
        if client is None and resolved_provider != "auto" and not resolved_base_url:
            logger.warning(
                "Vision provider %s unavailable, falling back to auto vision backends",
                resolved_provider,
            )
            effective_provider, client, final_model = resolve_vision_provider_client(
                provider="auto",
                model=resolved_model,
                async_mode=True,
            )
        if client is None:
            raise RuntimeError(
                f"No LLM provider configured for task={task} provider={resolved_provider}. "
                f"Run: hermes setup"
            )
        resolved_provider = effective_provider or resolved_provider
    else:
        client, final_model = _get_cached_client(
            resolved_provider,
            resolved_model,
            async_mode=True,
            base_url=resolved_base_url,
            api_key=resolved_api_key,
            api_mode=resolved_api_mode,
        )
        if client is None:
            _explicit = (resolved_provider or "").strip().lower()
            if _explicit and _explicit not in ("auto", "openrouter", "custom"):
                raise RuntimeError(
                    f"Provider '{_explicit}' is set in config.yaml but no API key "
                    f"was found. Set the {_explicit.upper()}_API_KEY environment "
                    f"variable, or switch to a different provider with `hermes model`."
                )
            if not resolved_base_url:
                logger.info("Auxiliary %s: provider %s unavailable, trying auto-detection chain",
                            task or "call", resolved_provider)
                client, final_model = _get_cached_client("auto", async_mode=True)
        if client is None:
            raise RuntimeError(
                f"No LLM provider configured for task={task} provider={resolved_provider}. "
                f"Run: hermes setup")

    effective_timeout = timeout if timeout is not None else _get_task_timeout(task)

    kwargs = _build_call_kwargs(
        resolved_provider, final_model, messages,
        temperature=temperature, max_tokens=max_tokens,
        tools=tools, timeout=effective_timeout, extra_body=extra_body,
        base_url=resolved_base_url)

    # 为 Anthropic 兼容端点（例如 MiniMax）转换图片块
    _client_base = str(getattr(client, "base_url", "") or "")
    if _is_anthropic_compat_endpoint(resolved_provider, _client_base):
        kwargs["messages"] = _convert_openai_images_to_anthropic(kwargs["messages"])

    try:
        return _validate_llm_response(
            await client.chat.completions.create(**kwargs), task)
    except Exception as first_err:
        err_str = str(first_err)
        if "max_tokens" in err_str or "unsupported_parameter" in err_str:
            kwargs.pop("max_tokens", None)
            kwargs["max_completion_tokens"] = max_tokens
            try:
                return _validate_llm_response(
                    await client.chat.completions.create(**kwargs), task)
            except Exception as retry_err:
                # 如果 max_tokens 重试也遇到支付或连接错误，
                # 向下执行到下面的回退链。
                if not (_is_payment_error(retry_err) or _is_connection_error(retry_err)):
                    raise
                first_err = retry_err

        # ── 支付 / 连接回退（镜像同步 call_llm）─────
        should_fallback = _is_payment_error(first_err) or _is_connection_error(first_err)
        is_auto = resolved_provider in ("auto", "", None)
        if should_fallback and is_auto:
            reason = "payment error" if _is_payment_error(first_err) else "connection error"
            logger.info("Auxiliary %s (async): %s on %s (%s), trying fallback",
                        task or "call", reason, resolved_provider, first_err)
            fb_client, fb_model, fb_label = _try_payment_fallback(
                resolved_provider, task, reason=reason)
            if fb_client is not None:
                fb_kwargs = _build_call_kwargs(
                    fb_label, fb_model, messages,
                    temperature=temperature, max_tokens=max_tokens,
                    tools=tools, timeout=effective_timeout,
                    extra_body=extra_body)
                # 将同步回退客户端转换为异步
                async_fb, async_fb_model = _to_async_client(fb_client, fb_model or "")
                if async_fb_model and async_fb_model != fb_kwargs.get("model"):
                    fb_kwargs["model"] = async_fb_model
                return _validate_llm_response(
                    await async_fb.chat.completions.create(**fb_kwargs), task)
        raise
