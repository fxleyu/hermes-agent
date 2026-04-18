"""模型元数据、上下文长度和 token 估算工具。

不依赖 AIAgent 的纯工具函数。由 ContextCompressor
和 run_agent.py 用于预检上下文检查。
"""

import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import requests
import yaml

from hermes_constants import OPENROUTER_MODELS_URL

logger = logging.getLogger(__name__)

# 可作为模型 ID 前的 "provider:" 前缀出现的提供者名称。
# 仅这些会被剥离——Ollama 风格的 "model:tag" 冒号（例如 "qwen3.5:27b"）
# 会被保留，使完整模型名称可到达缓存查找和服务器查询。
_PROVIDER_PREFIXES: frozenset[str] = frozenset({
    "openrouter", "nous", "openai-codex", "copilot", "copilot-acp",
    "gemini", "ollama-cloud", "zai", "kimi-coding", "kimi-coding-cn", "minimax", "minimax-cn", "anthropic", "deepseek",
    "opencode-zen", "opencode-go", "ai-gateway", "kilocode", "alibaba",
    "qwen-oauth",
    "xiaomi",
    "arcee",
    "custom", "local",
    # Common aliases
    # 常见别名
    "google", "google-gemini", "google-ai-studio",
    "glm", "z-ai", "z.ai", "zhipu", "github", "github-copilot",
    "github-models", "kimi", "moonshot", "kimi-cn", "moonshot-cn", "claude", "deep-seek",
    "ollama",
    "opencode", "zen", "go", "vercel", "kilo", "dashscope", "aliyun", "qwen",
    "mimo", "xiaomi-mimo",
    "arcee-ai", "arceeai",
    "xai", "x-ai", "x.ai", "grok",
    "qwen-portal",
})


_OLLAMA_TAG_PATTERN = re.compile(
    r"^(\d+\.?\d*b|latest|stable|q\d|fp?\d|instruct|chat|coder|vision|text)",
    re.IGNORECASE,
)


def _strip_provider_prefix(model: str) -> str:
    """剥离已识别的提供者前缀。

    ``"local:my-model"`` → ``"my-model"``
    ``"qwen3.5:27b"``   → ``"qwen3.5:27b"``  （不变——不是提供者前缀）
    ``"qwen:0.5b"``     → ``"qwen:0.5b"``    （不变——Ollama model:tag）
    ``"deepseek:latest"``→ ``"deepseek:latest"``（不变——Ollama model:tag）
    """
    if ":" not in model or model.startswith("http"):
        return model
    prefix, suffix = model.split(":", 1)
    prefix_lower = prefix.strip().lower()
    if prefix_lower in _PROVIDER_PREFIXES:
        # 如果后缀看起来像 Ollama 标签则不剥离（例如 "7b"、"latest"、"q4_0"）
        if _OLLAMA_TAG_PATTERN.match(suffix.strip()):
            return model
        return suffix
    return model

_model_metadata_cache: Dict[str, Dict[str, Any]] = {}
_model_metadata_cache_time: float = 0
_MODEL_CACHE_TTL = 3600
_endpoint_model_metadata_cache: Dict[str, Dict[str, Dict[str, Any]]] = {}
_endpoint_model_metadata_cache_time: Dict[str, float] = {}
_ENDPOINT_MODEL_CACHE_TTL = 300

# 当模型未知时，上下文长度探测的降序层级。
# 从 128K 开始（大多数现代模型的安全默认值），
# 遇到上下文长度错误时逐步降低直到成功。
CONTEXT_PROBE_TIERS = [
    128_000,
    64_000,
    32_000,
    16_000,
    8_000,
]

# 没有检测方法成功时的默认上下文长度。
DEFAULT_FALLBACK_CONTEXT = CONTEXT_PROBE_TIERS[0]

# 运行 Hermes Agent 所需的最小上下文长度。token 数更少的
# 模型无法维持足够的工作内存来支持工具调用工作流。
# 会话、模型切换和定时任务应拒绝低于此值的模型。
MINIMUM_CONTEXT_LENGTH = 64_000

# 精简回退默认值——仅包含宽泛的模型家族模式。
# 仅在提供者未知且 models.dev/OpenRouter/Anthropic
# 全部未命中时触发。替代了之前 80+ 条目的字典。
# 对于提供者特定的上下文长度，models.dev 是主要来源。
DEFAULT_CONTEXT_LENGTHS = {
    # Anthropic Claude 4.6（1M 上下文）——仅裸 ID 以避免
    # 模糊匹配冲突（例如 "anthropic/claude-sonnet-4" 是
    # "anthropic/claude-sonnet-4.6" 的子串）。
    # OpenRouter 前缀模型通过 OpenRouter 实时 API 或 models.dev 解析。
    "claude-opus-4-7": 1000000,
    "claude-opus-4.7": 1000000,
    "claude-opus-4-6": 1000000,
    "claude-sonnet-4-6": 1000000,
    "claude-opus-4.6": 1000000,
    "claude-sonnet-4.6": 1000000,
    # 旧版 Claude 模型的兜底（必须排在特定条目之后）
    "claude": 200000,
    # OpenAI — GPT-5 系列（大多数有 400k；特定覆盖在前）
    # 来源: https://developers.openai.com/api/docs/models
    "gpt-5.4-nano": 400000,           # 400k（不同于完整 5.4 的 1.05M）
    "gpt-5.4-mini": 400000,           # 400k（不同于完整 5.4 的 1.05M）
    "gpt-5.4": 1050000,               # GPT-5.4、GPT-5.4 Pro（1.05M 上下文）
    "gpt-5.3-codex-spark": 128000,    # Spark 变体有较小的 128k 上下文
    "gpt-5.1-chat": 128000,           # Chat 变体有 128k 上下文
    "gpt-5": 400000,                  # GPT-5.x base、mini、codex 变体（400k）
    "gpt-4.1": 1047576,
    "gpt-4": 128000,
    # Google Gemini
    "gemini": 1048576,
    # Gemma（通过 AI Studio 提供的开放模型）
    "gemma-4-31b": 256000,
    "gemma-4-26b": 256000,
    "gemma-3": 131072,
    "gemma": 8192,  # 旧版 gemma 模型的回退
    # DeepSeek
    "deepseek": 128000,
    # Meta Llama
    "llama": 131072,
    # Qwen —— 特定模型家族在兜底之前。
    # 官方文档: https://help.aliyun.com/zh/model-studio/developer-reference/
    "qwen3-coder-plus": 1000000,  # 1M 上下文
    "qwen3-coder": 262144,        # 256K 上下文
    "qwen": 131072,
    # MiniMax —— 官方文档：所有模型 204,800 上下文
    # https://platform.minimax.io/docs/api-reference/text-anthropic-api
    "minimax": 204800,
    # GLM
    "glm": 202752,
    # xAI Grok —— xAI /v1/models 不返回 context_length 元数据，
    # 所以这些硬编码回退防止 Hermes 在用户通过自定义提供者
    # 指向 https://api.x.ai/v1 时探测降级到默认 128k。
    # 数值来源于 models.dev（2026-04）。
    # 键使用子串匹配（最长优先），例如 "grok-4.20"
    # 匹配 "grok-4.20-0309-reasoning" / "-non-reasoning" / "-multi-agent-0309"。
    "grok-code-fast": 256000,   # grok-code-fast-1
    "grok-4-1-fast": 2000000,   # grok-4-1-fast-(non-)reasoning
    "grok-2-vision": 8192,      # grok-2-vision, -1212, -latest
    "grok-4-fast": 2000000,     # grok-4-fast-(non-)reasoning
    "grok-4.20": 2000000,       # grok-4.20-0309-(non-)reasoning, -multi-agent-0309
    "grok-4": 256000,           # grok-4, grok-4-0709
    "grok-3": 131072,           # grok-3, grok-3-mini, grok-3-fast, grok-3-mini-fast
    "grok-2": 131072,           # grok-2, grok-2-1212, grok-2-latest
    "grok": 131072,             # 兜底（grok-beta、未知 grok-*）
    # Kimi
    "kimi": 262144,
    # Arcee
    "trinity": 262144,
    # OpenRouter
    "elephant": 262144,
    # Hugging Face 推理提供者——模型 ID 使用 org/name 格式
    "Qwen/Qwen3.5-397B-A17B": 131072,
    "Qwen/Qwen3.5-35B-A3B": 131072,
    "deepseek-ai/DeepSeek-V3.2": 65536,
    "moonshotai/Kimi-K2.5": 262144,
    "moonshotai/Kimi-K2-Thinking": 262144,
    "MiniMaxAI/MiniMax-M2.5": 204800,
    "XiaomiMiMo/MiMo-V2-Flash": 256000,
    "mimo-v2-pro": 1000000,
    "mimo-v2-omni": 256000,
    "mimo-v2-flash": 256000,
    "zai-org/GLM-5": 202752,
}

_CONTEXT_LENGTH_KEYS = (
    "context_length",
    "context_window",
    "max_context_length",
    "max_position_embeddings",
    "max_model_len",
    "max_input_tokens",
    "max_sequence_length",
    "max_seq_len",
    "n_ctx_train",
    "n_ctx",
)

_MAX_COMPLETION_KEYS = (
    "max_completion_tokens",
    "max_output_tokens",
    "max_tokens",
)

# 本地服务器主机名/地址模式
_LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1", "0.0.0.0")
# Docker / Podman / Lima 解析到宿主机的 DNS 名称
_CONTAINER_LOCAL_SUFFIXES = (
    ".docker.internal",
    ".containers.internal",
    ".lima.internal",
)


def _normalize_base_url(base_url: str) -> str:
    return (base_url or "").strip().rstrip("/")


def _is_openrouter_base_url(base_url: str) -> bool:
    return "openrouter.ai" in _normalize_base_url(base_url).lower()


def _is_custom_endpoint(base_url: str) -> bool:
    normalized = _normalize_base_url(base_url)
    return bool(normalized) and not _is_openrouter_base_url(normalized)


_URL_TO_PROVIDER: Dict[str, str] = {
    "api.openai.com": "openai",
    "chatgpt.com": "openai",
    "api.anthropic.com": "anthropic",
    "api.z.ai": "zai",
    "api.moonshot.ai": "kimi-coding",
    "api.moonshot.cn": "kimi-coding-cn",
    "api.kimi.com": "kimi-coding",
    "api.arcee.ai": "arcee",
    "api.minimax": "minimax",
    "dashscope.aliyuncs.com": "alibaba",
    "dashscope-intl.aliyuncs.com": "alibaba",
    "portal.qwen.ai": "qwen-oauth",
    "openrouter.ai": "openrouter",
    "generativelanguage.googleapis.com": "gemini",
    "inference-api.nousresearch.com": "nous",
    "api.deepseek.com": "deepseek",
    "api.githubcopilot.com": "copilot",
    "models.github.ai": "copilot",
    "api.fireworks.ai": "fireworks",
    "opencode.ai": "opencode-go",
    "api.x.ai": "xai",
    "api.xiaomimimo.com": "xiaomi",
    "xiaomimimo.com": "xiaomi",
    "ollama.com": "ollama-cloud",
}


def _infer_provider_from_url(base_url: str) -> Optional[str]:
    """从 base URL 推断 models.dev 提供者名称。

    这允许通过 models.dev 为自定义端点（如 DashScope（阿里巴巴）、
    Z.AI、Kimi 等）解析上下文长度，无需用户在配置中
    显式设置提供者名称。
    """
    normalized = _normalize_base_url(base_url)
    if not normalized:
        return None
    parsed = urlparse(normalized if "://" in normalized else f"https://{normalized}")
    host = parsed.netloc.lower() or parsed.path.lower()
    for url_part, provider in _URL_TO_PROVIDER.items():
        if url_part in host:
            return provider
    return None


def _is_known_provider_base_url(base_url: str) -> bool:
    return _infer_provider_from_url(base_url) is not None


def is_local_endpoint(base_url: str) -> bool:
    """如果 base_url 指向本地机器（localhost / RFC-1918 / WSL）则返回 True。"""
    normalized = _normalize_base_url(base_url)
    if not normalized:
        return False
    url = normalized if "://" in normalized else f"http://{normalized}"
    try:
        parsed = urlparse(url)
        host = parsed.hostname or ""
    except Exception:
        return False
    if host in _LOCAL_HOSTS:
        return True
    # Docker / Podman / Lima 内部 DNS 名称（例如 host.docker.internal）
    if any(host.endswith(suffix) for suffix in _CONTAINER_LOCAL_SUFFIXES):
        return True
    # RFC-1918 私有范围和链路本地
    import ipaddress
    try:
        addr = ipaddress.ip_address(host)
        return addr.is_private or addr.is_loopback or addr.is_link_local
    except ValueError:
        pass
    # 看起来像私有范围的裸 IP（例如 WSL 的 172.26.x.x）
    parts = host.split(".")
    if len(parts) == 4:
        try:
            first, second = int(parts[0]), int(parts[1])
            if first == 10:
                return True
            if first == 172 and 16 <= second <= 31:
                return True
            if first == 192 and second == 168:
                return True
        except ValueError:
            pass
    return False


def detect_local_server_type(base_url: str) -> Optional[str]:
    """通过探测已知端点检测 base_url 运行的本地服务器类型。

    返回: "ollama"、"lm-studio"、"vllm"、"llamacpp" 或 None。
    """
    import httpx

    normalized = _normalize_base_url(base_url)
    server_url = normalized
    if server_url.endswith("/v1"):
        server_url = server_url[:-3]

    try:
        with httpx.Client(timeout=2.0) as client:
            # LM Studio 暴露 /api/v1/models——优先检查（最特异）
            try:
                r = client.get(f"{server_url}/api/v1/models")
                if r.status_code == 200:
                    return "lm-studio"
            except Exception:
                pass
            # Ollama 暴露 /api/tags 并以 {"models": [...]} 格式响应
            # LM Studio 在该路径返回 {"error": "Unexpected endpoint"}（状态 200），
            # 因此必须验证响应包含 "models"。
            try:
                r = client.get(f"{server_url}/api/tags")
                if r.status_code == 200:
                    try:
                        data = r.json()
                        if "models" in data:
                            return "ollama"
                    except Exception:
                        pass
            except Exception:
                pass
            # llama.cpp 暴露 /v1/props（旧版构建使用不带 /v1 前缀的 /props）
            try:
                r = client.get(f"{server_url}/v1/props")
                if r.status_code != 200:
                    r = client.get(f"{server_url}/props")  # 旧版构建的回退
                if r.status_code == 200 and "default_generation_settings" in r.text:
                    return "llamacpp"
            except Exception:
                pass
            # vLLM: /version 端点
            try:
                r = client.get(f"{server_url}/version")
                if r.status_code == 200:
                    data = r.json()
                    if "version" in data:
                        return "vllm"
            except Exception:
                pass
    except Exception:
        pass

    return None


def _iter_nested_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from _iter_nested_dicts(nested)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_nested_dicts(item)


def _coerce_reasonable_int(value: Any, minimum: int = 1024, maximum: int = 10_000_000) -> Optional[int]:
    try:
        if isinstance(value, bool):
            return None
        if isinstance(value, str):
            value = value.strip().replace(",", "")
        result = int(value)
    except (TypeError, ValueError):
        return None
    if minimum <= result <= maximum:
        return result
    return None


def _extract_first_int(payload: Dict[str, Any], keys: tuple[str, ...]) -> Optional[int]:
    keyset = {key.lower() for key in keys}
    for mapping in _iter_nested_dicts(payload):
        for key, value in mapping.items():
            if str(key).lower() not in keyset:
                continue
            coerced = _coerce_reasonable_int(value)
            if coerced is not None:
                return coerced
    return None


def _extract_context_length(payload: Dict[str, Any]) -> Optional[int]:
    return _extract_first_int(payload, _CONTEXT_LENGTH_KEYS)


def _extract_max_completion_tokens(payload: Dict[str, Any]) -> Optional[int]:
    return _extract_first_int(payload, _MAX_COMPLETION_KEYS)


def _extract_pricing(payload: Dict[str, Any]) -> Dict[str, Any]:
    alias_map = {
        "prompt": ("prompt", "input", "input_cost_per_token", "prompt_token_cost"),
        "completion": ("completion", "output", "output_cost_per_token", "completion_token_cost"),
        "request": ("request", "request_cost"),
        "cache_read": ("cache_read", "cached_prompt", "input_cache_read", "cache_read_cost_per_token"),
        "cache_write": ("cache_write", "cache_creation", "input_cache_write", "cache_write_cost_per_token"),
    }
    for mapping in _iter_nested_dicts(payload):
        normalized = {str(key).lower(): value for key, value in mapping.items()}
        if not any(any(alias in normalized for alias in aliases) for aliases in alias_map.values()):
            continue
        pricing: Dict[str, Any] = {}
        for target, aliases in alias_map.items():
            for alias in aliases:
                if alias in normalized and normalized[alias] not in (None, ""):
                    pricing[target] = normalized[alias]
                    break
        if pricing:
            return pricing
    return {}


def _add_model_aliases(cache: Dict[str, Dict[str, Any]], model_id: str, entry: Dict[str, Any]) -> None:
    cache[model_id] = entry
    if "/" in model_id:
        bare_model = model_id.split("/", 1)[1]
        cache.setdefault(bare_model, entry)


def fetch_model_metadata(force_refresh: bool = False) -> Dict[str, Dict[str, Any]]:
    """从 OpenRouter 获取模型元数据（缓存 1 小时）。"""
    global _model_metadata_cache, _model_metadata_cache_time

    if not force_refresh and _model_metadata_cache and (time.time() - _model_metadata_cache_time) < _MODEL_CACHE_TTL:
        return _model_metadata_cache

    try:
        response = requests.get(OPENROUTER_MODELS_URL, timeout=10)
        response.raise_for_status()
        data = response.json()

        cache = {}
        for model in data.get("data", []):
            model_id = model.get("id", "")
            entry = {
                "context_length": model.get("context_length", 128000),
                "max_completion_tokens": model.get("top_provider", {}).get("max_completion_tokens", 4096),
                "name": model.get("name", model_id),
                "pricing": model.get("pricing", {}),
            }
            _add_model_aliases(cache, model_id, entry)
            canonical = model.get("canonical_slug", "")
            if canonical and canonical != model_id:
                _add_model_aliases(cache, canonical, entry)

        _model_metadata_cache = cache
        _model_metadata_cache_time = time.time()
        logger.debug("Fetched metadata for %s models from OpenRouter", len(cache))
        return cache

    except Exception as e:
        logging.warning(f"Failed to fetch model metadata from OpenRouter: {e}")
        return _model_metadata_cache or {}


def fetch_endpoint_model_metadata(
    base_url: str,
    api_key: str = "",
    force_refresh: bool = False,
) -> Dict[str, Dict[str, Any]]:
    """从 OpenAI 兼容的 ``/models`` 端点获取模型元数据。

    用于硬编码全局模型名称默认值不可靠的显式自定义端点。
    结果按 base URL 缓存在内存中。
    """
    normalized = _normalize_base_url(base_url)
    if not normalized or _is_openrouter_base_url(normalized):
        return {}

    if not force_refresh:
        cached = _endpoint_model_metadata_cache.get(normalized)
        cached_at = _endpoint_model_metadata_cache_time.get(normalized, 0)
        if cached is not None and (time.time() - cached_at) < _ENDPOINT_MODEL_CACHE_TTL:
            return cached

    candidates = [normalized]
    if normalized.endswith("/v1"):
        alternate = normalized[:-3].rstrip("/")
    else:
        alternate = normalized + "/v1"
    if alternate and alternate not in candidates:
        candidates.append(alternate)

    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    last_error: Optional[Exception] = None

    for candidate in candidates:
        url = candidate.rstrip("/") + "/models"
        try:
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            payload = response.json()
            cache: Dict[str, Dict[str, Any]] = {}
            for model in payload.get("data", []):
                if not isinstance(model, dict):
                    continue
                model_id = model.get("id")
                if not model_id:
                    continue
                entry: Dict[str, Any] = {"name": model.get("name", model_id)}
                context_length = _extract_context_length(model)
                if context_length is not None:
                    entry["context_length"] = context_length
                max_completion_tokens = _extract_max_completion_tokens(model)
                if max_completion_tokens is not None:
                    entry["max_completion_tokens"] = max_completion_tokens
                pricing = _extract_pricing(model)
                if pricing:
                    entry["pricing"] = pricing
                _add_model_aliases(cache, model_id, entry)

            # 如果是 llama.cpp 服务器，查询 /props 获取实际分配的上下文
            is_llamacpp = any(
                m.get("owned_by") == "llamacpp"
                for m in payload.get("data", []) if isinstance(m, dict)
            )
            if is_llamacpp:
                try:
                    # 先尝试 /v1/props（当前 llama.cpp）；旧版构建回退到 /props
                    base = candidate.rstrip("/").replace("/v1", "")
                    props_resp = requests.get(base + "/v1/props", headers=headers, timeout=5)
                    if not props_resp.ok:
                        props_resp = requests.get(base + "/props", headers=headers, timeout=5)
                    if props_resp.ok:
                        props = props_resp.json()
                        gen_settings = props.get("default_generation_settings", {})
                        n_ctx = gen_settings.get("n_ctx")
                        model_alias = props.get("model_alias", "")
                        if n_ctx and model_alias and model_alias in cache:
                            cache[model_alias]["context_length"] = n_ctx
                except Exception:
                    pass

            _endpoint_model_metadata_cache[normalized] = cache
            _endpoint_model_metadata_cache_time[normalized] = time.time()
            return cache
        except Exception as exc:
            last_error = exc

    if last_error:
        logger.debug("Failed to fetch model metadata from %s/models: %s", normalized, last_error)
    _endpoint_model_metadata_cache[normalized] = {}
    _endpoint_model_metadata_cache_time[normalized] = time.time()
    return {}


def _get_context_cache_path() -> Path:
    """返回持久化上下文长度缓存文件的路径。"""
    from hermes_constants import get_hermes_home
    return get_hermes_home() / "context_length_cache.yaml"


def _load_context_cache() -> Dict[str, int]:
    """从磁盘加载 模型+提供者 -> 上下文长度 的缓存。"""
    path = _get_context_cache_path()
    if not path.exists():
        return {}
    try:
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        return data.get("context_lengths", {})
    except Exception as e:
        logger.debug("Failed to load context length cache: %s", e)
        return {}


def save_context_length(model: str, base_url: str, length: int) -> None:
    """持久化已发现的模型+提供者组合的上下文长度。

    缓存键为 ``model@base_url``，使得相同模型名称由
    不同提供者提供时可以有不同限制。
    """
    key = f"{model}@{base_url}"
    cache = _load_context_cache()
    if cache.get(key) == length:
        return  # 已存储
    cache[key] = length
    path = _get_context_cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            yaml.dump({"context_lengths": cache}, f, default_flow_style=False)
        logger.info("Cached context length %s -> %s tokens", key, f"{length:,}")
    except Exception as e:
        logger.debug("Failed to save context length cache: %s", e)


def get_cached_context_length(model: str, base_url: str) -> Optional[int]:
    """查找之前发现的模型+提供者的上下文长度。"""
    key = f"{model}@{base_url}"
    cache = _load_context_cache()
    return cache.get(key)


def get_next_probe_tier(current_length: int) -> Optional[int]:
    """返回下一个更低的探测层级，如果已在最低层则返回 None。"""
    for tier in CONTEXT_PROBE_TIERS:
        if tier < current_length:
            return tier
    return None


def parse_context_limit_from_error(error_msg: str) -> Optional[int]:
    """尝试从 API 错误消息中提取实际上下文限制。

    许多提供者在错误文本中包含限制值，例如：
      - "maximum context length is 32768 tokens"
      - "context_length_exceeded: 131072"
      - "Maximum context size 32768 exceeded"
      - "model's max context length is 65536"
    """
    error_lower = error_msg.lower()
    # 模式：在上下文相关关键词附近查找数字
    patterns = [
        r'(?:max(?:imum)?|limit)\s*(?:context\s*)?(?:length|size|window)?\s*(?:is|of|:)?\s*(\d{4,})',
        r'context\s*(?:length|size|window)\s*(?:is|of|:)?\s*(\d{4,})',
        r'(\d{4,})\s*(?:token)?\s*(?:context|limit)',
        r'>\s*(\d{4,})\s*(?:max|limit|token)',  # "250000 tokens > 200000 maximum"
        r'(\d{4,})\s*(?:max(?:imum)?)\b',  # "200000 maximum"
    ]
    for pattern in patterns:
        match = re.search(pattern, error_lower)
        if match:
            limit = int(match.group(1))
            # 合理性检查：必须是合理的上下文长度
            if 1024 <= limit <= 10_000_000:
                return limit
    return None


def parse_available_output_tokens_from_error(error_msg: str) -> Optional[int]:
    """检测"输出上限过大"错误并返回可用的输出 token 数。

    背景——存在两种不同的上下文错误：
      1. "提示词过长"——输入本身超过了上下文窗口。
           修复：压缩历史和/或减半 context_length。
      2. "max_tokens 过大"——输入正常，但 输入 + 请求的输出 > 窗口。
           修复：减少本次调用的 max_tokens（输出上限）。
           不要修改 context_length——窗口没有缩小。

    Anthropic 的 API 返回如下错误：
      "max_tokens: 32768 > context_window: 200000 - input_tokens: 190000 = available_tokens: 10000"

    返回可以容纳的输出 token 数（如上例中的 10000），如果错误
    不像 max_tokens 过大错误则返回 None。
    """
    error_lower = error_msg.lower()

    # 必须看起来像输出上限错误，而非提示词长度错误。
    is_output_cap_error = (
        "max_tokens" in error_lower
        and ("available_tokens" in error_lower or "available tokens" in error_lower)
    )
    if not is_output_cap_error:
        return None

    # 提取 available_tokens 值。
    # Anthropic 格式："… = available_tokens: 10000"
    patterns = [
        r'available_tokens[:\s]+(\d+)',
        r'available\s+tokens[:\s]+(\d+)',
        # 回退：表达式如 "200000 - 190000 = 10000" 中 "=" 后的最后一个数字
        r'=\s*(\d+)\s*$',
    ]
    for pattern in patterns:
        match = re.search(pattern, error_lower)
        if match:
            tokens = int(match.group(1))
            if tokens >= 1:
                return tokens
    return None


def _model_id_matches(candidate_id: str, lookup_model: str) -> bool:
    """如果 *candidate_id*（来自服务器）匹配 *lookup_model*（已配置的）则返回 True。

    支持两种形式：
    - 精确匹配：  "nvidia-nemotron-super-49b-v1" == "nvidia-nemotron-super-49b-v1"
    - 后缀匹配：   "nvidia/nvidia-nemotron-super-49b-v1" 匹配 "nvidia-nemotron-super-49b-v1"
                    （最后一个 "/" 之后的部分等于 lookup_model）

    这覆盖了 LM Studio 的原生 API，它将模型存储为 "publisher/slug"
    而用户通常只在 "local:" 前缀后配置 slug。
    """
    if candidate_id == lookup_model:
        return True
    # 后缀匹配：候选项的基本名称等于查找名称
    if "/" in candidate_id and candidate_id.rsplit("/", 1)[1] == lookup_model:
        return True
    return False


def query_ollama_num_ctx(model: str, base_url: str) -> Optional[int]:
    """查询 Ollama 服务器获取模型的上下文长度。

    返回通过 ``/api/show`` 从 GGUF 元数据获取的模型最大上下文，
    或 Modelfile 中显式设置的 ``num_ctx``。如果服务器不可达
    或不是 Ollama 则返回 None。

    这个值应该作为 ``num_ctx`` 传递给 Ollama 聊天请求，
    以覆盖默认的 2048。
    """
    import httpx

    bare_model = _strip_provider_prefix(model)
    server_url = base_url.rstrip("/")
    if server_url.endswith("/v1"):
        server_url = server_url[:-3]

    try:
        server_type = detect_local_server_type(base_url)
    except Exception:
        return None
    if server_type != "ollama":
        return None

    try:
        with httpx.Client(timeout=3.0) as client:
            resp = client.post(f"{server_url}/api/show", json={"name": bare_model})
            if resp.status_code != 200:
                return None
            data = resp.json()

            # 优先使用 Modelfile 参数中的显式 num_ctx（用户覆盖）
            params = data.get("parameters", "")
            if "num_ctx" in params:
                for line in params.split("\n"):
                    if "num_ctx" in line:
                        parts = line.strip().split()
                        if len(parts) >= 2:
                            try:
                                return int(parts[-1])
                            except ValueError:
                                pass

            # 回退到 GGUF model_info 的 context_length（训练最大值）
            model_info = data.get("model_info", {})
            for key, value in model_info.items():
                if "context_length" in key and isinstance(value, (int, float)):
                    return int(value)
    except Exception:
        pass
    return None


def _query_local_context_length(model: str, base_url: str) -> Optional[int]:
    """查询本地服务器获取模型的上下文长度。"""
    import httpx

    # 剥离已识别的提供者前缀（例如 "local:model-name" → "model-name"）。
    # Ollama 的 "model:tag" 冒号（例如 "qwen3.5:27b"）会被有意保留。
    model = _strip_provider_prefix(model)

    # 去掉 /v1 后缀以获取服务器根路径
    server_url = base_url.rstrip("/")
    if server_url.endswith("/v1"):
        server_url = server_url[:-3]

    try:
        server_type = detect_local_server_type(base_url)
    except Exception:
        server_type = None

    try:
        with httpx.Client(timeout=3.0) as client:
            # Ollama：/api/show 返回包含上下文信息的模型详情
            if server_type == "ollama":
                resp = client.post(f"{server_url}/api/show", json={"name": model})
                if resp.status_code == 200:
                    data = resp.json()
                    # 优先使用 Modelfile 参数中的显式 num_ctx：这是
                    # Ollama 实际分配 KV 缓存的*运行时*上下文。
                    # GGUF model_info.context_length 是训练最大值，
                    # 可能大于 num_ctx——在此使用它会让 Hermes 将对话
                    # 增长超过运行时限制，Ollama 会静默截断。
                    # 与 query_ollama_num_ctx() 一致。
                    params = data.get("parameters", "")
                    if "num_ctx" in params:
                        for line in params.split("\n"):
                            if "num_ctx" in line:
                                parts = line.strip().split()
                                if len(parts) >= 2:
                                    try:
                                        return int(parts[-1])
                                    except ValueError:
                                        pass
                    # 回退到 GGUF model_info 的 context_length（训练最大值）
                    model_info = data.get("model_info", {})
                    for key, value in model_info.items():
                        if "context_length" in key and isinstance(value, (int, float)):
                            return int(value)

            # LM Studio 原生 API：/api/v1/models 返回 max_context_length。
            # 这比 OpenAI 兼容的 /v1/models 更可靠，因为后者不包含
            # LM Studio 服务器的上下文窗口信息。
            # 使用 _model_id_matches 进行模糊匹配：LM Studio 将模型存储为
            # "publisher/slug"，但用户通常只在 "local:" 前缀后配置 "slug"。
            if server_type == "lm-studio":
                resp = client.get(f"{server_url}/api/v1/models")
                if resp.status_code == 200:
                    data = resp.json()
                    for m in data.get("models", []):
                        if _model_id_matches(m.get("key", ""), model) or _model_id_matches(m.get("id", ""), model):
                            # 优先使用已加载实例的上下文（实际运行时值）
                            for inst in m.get("loaded_instances", []):
                                cfg = inst.get("config", {})
                                ctx = cfg.get("context_length")
                                if ctx and isinstance(ctx, (int, float)):
                                    return int(ctx)
                            # 回退到 max_context_length（理论模型最大值）
                            ctx = m.get("max_context_length") or m.get("context_length")
                            if ctx and isinstance(ctx, (int, float)):
                                return int(ctx)

            # LM Studio / vLLM / llama.cpp：尝试 /v1/models/{model}
            resp = client.get(f"{server_url}/v1/models/{model}")
            if resp.status_code == 200:
                data = resp.json()
                # vLLM 返回 max_model_len
                ctx = data.get("max_model_len") or data.get("context_length") or data.get("max_tokens")
                if ctx and isinstance(ctx, (int, float)):
                    return int(ctx)

            # 尝试 /v1/models 并在列表中查找模型。
            # 使用 _model_id_matches 处理 "publisher/slug" vs 裸 "slug"。
            resp = client.get(f"{server_url}/v1/models")
            if resp.status_code == 200:
                data = resp.json()
                models_list = data.get("data", [])
                for m in models_list:
                    if _model_id_matches(m.get("id", ""), model):
                        ctx = m.get("max_model_len") or m.get("context_length") or m.get("max_tokens")
                        if ctx and isinstance(ctx, (int, float)):
                            return int(ctx)
    except Exception:
        pass

    return None


def _normalize_model_version(model: str) -> str:
    """标准化版本分隔符用于匹配。

    Nous 使用短横线：claude-opus-4-6、claude-sonnet-4-5
    OpenRouter 使用点号：claude-opus-4.6、claude-sonnet-4.5
    两者都标准化为短横线进行比较。
    """
    return model.replace(".", "-")


def _query_anthropic_context_length(model: str, base_url: str, api_key: str) -> Optional[int]:
    """查询 Anthropic 的 /v1/models 端点获取上下文长度。

    仅对常规 ANTHROPIC_API_KEY（sk-ant-api*）有效。
    OAuth 令牌（sk-ant-oat*，来自 Claude Code）会返回 401。
    """
    if not api_key or api_key.startswith("sk-ant-oat"):
        return None  # OAuth 令牌无法访问 /v1/models
    try:
        base = base_url.rstrip("/")
        if base.endswith("/v1"):
            base = base[:-3]
        url = f"{base}/v1/models?limit=1000"
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        }
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code != 200:
            return None
        data = resp.json()
        for m in data.get("data", []):
            if m.get("id") == model:
                ctx = m.get("max_input_tokens")
                if isinstance(ctx, int) and ctx > 0:
                    return ctx
    except Exception as e:
        logger.debug("Anthropic /v1/models query failed: %s", e)
    return None


def _resolve_nous_context_length(model: str) -> Optional[int]:
    """通过 OpenRouter 元数据解析 Nous Portal 模型的上下文长度。

    Nous 的模型 ID 是裸名称（例如 'claude-opus-4-6'），而 OpenRouter 使用
    带前缀的 ID（例如 'anthropic/claude-opus-4.6'）。尝试使用
    版本标准化（点↔短横线）进行后缀匹配。
    """
    metadata = fetch_model_metadata()  # OpenRouter 缓存
    # 先精确匹配
    if model in metadata:
        return metadata[model].get("context_length")

    normalized = _normalize_model_version(model).lower()

    for or_id, entry in metadata.items():
        bare = or_id.split("/", 1)[1] if "/" in or_id else or_id
        if bare.lower() == model.lower() or _normalize_model_version(bare).lower() == normalized:
            return entry.get("context_length")

    # 部分前缀匹配，例如 gemini-3-flash → gemini-3-flash-preview
    # 要求匹配在词边界处（后跟 -、: 或字符串结尾）
    model_lower = model.lower()
    for or_id, entry in metadata.items():
        bare = or_id.split("/", 1)[1] if "/" in or_id else or_id
        for candidate, query in [(bare.lower(), model_lower), (_normalize_model_version(bare).lower(), normalized)]:
            if candidate.startswith(query) and (
                len(candidate) == len(query) or candidate[len(query)] in "-:."
            ):
                return entry.get("context_length")

    return None


def get_model_context_length(
    model: str,
    base_url: str = "",
    api_key: str = "",
    config_context_length: int | None = None,
    provider: str = "",
) -> int:
    """获取模型的上下文长度。

    解析优先级：
    0. 显式配置覆盖（model.context_length 或 custom_providers 按模型配置）
    1. 持久化缓存（之前通过探测发现的值）
    2. 活跃端点元数据（显式自定义端点的 /models）
    3. 本地服务器查询（本地端点）
    4. Anthropic /v1/models API（仅 API 密钥用户，非 OAuth）
    5. OpenRouter 实时 API 元数据
    6. Nous 后缀匹配（通过 OpenRouter 缓存）
    7. models.dev 注册表查找（提供者感知）
    8. 精简硬编码默认值（宽泛家族模式）
    9. 默认回退（128K）
    """
    # 0. 显式配置覆盖——用户最了解情况
    if config_context_length is not None and isinstance(config_context_length, int) and config_context_length > 0:
        return config_context_length

    # 标准化带提供者前缀的模型名称（例如 "local:model-name" →
    # "model-name"），使缓存查找和服务器查询使用本地服务器实际
    # 认识的裸 ID。Ollama "model:tag" 冒号被保留。
    model = _strip_provider_prefix(model)

    # 1. 检查持久化缓存（模型+提供者）
    if base_url:
        cached = get_cached_context_length(model, base_url)
        if cached is not None:
            return cached

    # 2. 真正自定义/未知端点的活跃端点元数据。
    # 已知提供者（Copilot、OpenAI、Anthropic 等）跳过此步——它们的
    # /models 端点可能报告提供者强加的限制（例如 Copilot
    # 返回 128k）而非模型的完整上下文（400k）。models.dev
    # 有正确的每提供者值，在步骤 5+ 检查。
    if _is_custom_endpoint(base_url) and not _is_known_provider_base_url(base_url):
        endpoint_metadata = fetch_endpoint_model_metadata(base_url, api_key=api_key)
        matched = endpoint_metadata.get(model)
        if not matched:
            # 单模型服务器：如果只加载了一个模型，使用它
            if len(endpoint_metadata) == 1:
                matched = next(iter(endpoint_metadata.values()))
            else:
                # 模糊匹配：双向子串匹配
                for key, entry in endpoint_metadata.items():
                    if model in key or key in model:
                        matched = entry
                        break
        if matched:
            context_length = matched.get("context_length")
            if isinstance(context_length, int):
                return context_length
        if not _is_known_provider_base_url(base_url):
            # 3. 尝试直接查询本地服务器
            if is_local_endpoint(base_url):
                local_ctx = _query_local_context_length(model, base_url)
                if local_ctx and local_ctx > 0:
                    save_context_length(model, base_url, local_ctx)
                    return local_ctx
            logger.info(
                "Could not detect context length for model %r at %s — "
                "defaulting to %s tokens (probe-down). Set model.context_length "
                "in config.yaml to override.",
                model, base_url, f"{DEFAULT_FALLBACK_CONTEXT:,}",
            )
            return DEFAULT_FALLBACK_CONTEXT

    # 4. Anthropic /v1/models API（仅对常规 API 密钥有效，非 OAuth）
    if provider == "anthropic" or (
        base_url and "api.anthropic.com" in base_url
    ):
        ctx = _query_anthropic_context_length(model, base_url or "https://api.anthropic.com", api_key)
        if ctx:
            return ctx

    # 4b. AWS Bedrock——使用静态上下文长度表。
    # Bedrock 的 ListFoundationModels 不暴露上下文窗口大小，
    # 所以在 bedrock_adapter.py 中维护了一个策划的表。
    if provider == "bedrock" or (base_url and "bedrock-runtime" in base_url):
        try:
            from agent.bedrock_adapter import get_bedrock_context_length
            return get_bedrock_context_length(model)
        except ImportError:
            pass  # 未安装 boto3——回退到通用解析

    # 5. 提供者感知查找（在通用 OpenRouter 缓存之前）
    # 这些是提供者特定的，优先于通用 OR 缓存，
    # 因为相同模型在不同提供者可能有不同的上下文限制
    # （例如 claude-opus-4.6 在 Anthropic 是 1M 但在 GitHub Copilot 是 128K）。
    # 如果提供者是通用的（openrouter/custom/空），尝试从 URL 推断。
    effective_provider = provider
    if not effective_provider or effective_provider in ("openrouter", "custom"):
        if base_url:
            inferred = _infer_provider_from_url(base_url)
            if inferred:
                effective_provider = inferred

    if effective_provider == "nous":
        ctx = _resolve_nous_context_length(model)
        if ctx:
            return ctx
    if effective_provider:
        from agent.models_dev import lookup_models_dev_context
        ctx = lookup_models_dev_context(effective_provider, model)
        if ctx:
            return ctx

    # 6. OpenRouter 实时 API 元数据（不区分提供者的回退）
    metadata = fetch_model_metadata()
    if model in metadata:
        return metadata[model].get("context_length", 128000)

    # 8. 硬编码默认值（模糊匹配——最长键优先以确保特异性）
    # 仅检查 `default_model in model`（键是否为输入的子串）。
    # 反向 (`model in default_model`) 会导致较短名称如
    # "claude-sonnet-4" 错误匹配 "claude-sonnet-4-6" 并返回 1M。
    model_lower = model.lower()
    for default_model, length in sorted(
        DEFAULT_CONTEXT_LENGTHS.items(), key=lambda x: len(x[0]), reverse=True
    ):
        if default_model in model_lower:
            return length

    # 9. 作为最后手段查询本地服务器
    if base_url and is_local_endpoint(base_url):
        local_ctx = _query_local_context_length(model, base_url)
        if local_ctx and local_ctx > 0:
            save_context_length(model, base_url, local_ctx)
            return local_ctx

    # 10. 默认回退——128K
    return DEFAULT_FALLBACK_CONTEXT


def estimate_tokens_rough(text: str) -> int:
    """粗略 token 估算（约 4 字符/token）用于预检检查。

    使用向上取整除法，使短文本（1-3 字符）永远不会估算为
    0 token，否则当存在许多短工具结果时，压缩器和预检
    检查会系统性地少计。
    """
    if not text:
        return 0
    return (len(text) + 3) // 4


def estimate_messages_tokens_rough(messages: List[Dict[str, Any]]) -> int:
    """消息列表的粗略 token 估算（仅用于预检）。"""
    total_chars = sum(len(str(msg)) for msg in messages)
    return (total_chars + 3) // 4


def estimate_request_tokens_rough(
    messages: List[Dict[str, Any]],
    *,
    system_prompt: str = "",
    tools: Optional[List[Dict[str, Any]]] = None,
) -> int:
    """完整 chat-completions 请求的粗略 token 估算。

    包含 Hermes 发送给提供者的主要有效载荷部分：
    系统提示词、对话消息和工具定义。启用 50+ 个工具时，
    仅工具定义就可增加 20-30K token——
    仅计算消息时这是一个重大盲区。
    """
    total_chars = 0
    if system_prompt:
        total_chars += len(system_prompt)
    if messages:
        total_chars += sum(len(str(msg)) for msg in messages)
    if tools:
        total_chars += len(str(tools))
    return (total_chars + 3) // 4
