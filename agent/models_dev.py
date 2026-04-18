"""Models.dev 注册表集成——提供商和模型的主数据库。

从 https://models.dev/api.json 获取数据——一个社区维护的数据库，
包含 109+ 提供商的 4000+ 模型。提供：

- **提供商元数据**：名称、基础 URL、环境变量、文档链接
- **模型元数据**：上下文窗口、最大输出、每百万 token 费用、功能
  （推理、工具、视觉、PDF、音频）、模态、知识截止日期、
  开放权重标志、系列分组、弃用状态

数据解析顺序（与 TypeScript OpenCode 类似）：
  1. 打包快照（随包发布——离线优先）
  2. 磁盘缓存（~/.hermes/models_dev_cache.json）
  3. 网络获取（https://models.dev/api.json）
  4. 每 60 分钟后台刷新

其他模块应从此处导入数据类和查询函数，而非自行解析原始 JSON。
"""

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from utils import atomic_json_write

import requests

logger = logging.getLogger(__name__)

MODELS_DEV_URL = "https://models.dev/api.json"
_MODELS_DEV_CACHE_TTL = 3600  # 内存缓存 1 小时

# 内存缓存
_models_dev_cache: Dict[str, Any] = {}
_models_dev_cache_time: float = 0


# ---------------------------------------------------------------------------
# 数据类——提供商和模型的丰富元数据
# ---------------------------------------------------------------------------

@dataclass
class ModelInfo:
    """来自 models.dev 的单个模型的完整元数据。"""

    id: str
    name: str
    family: str
    provider_id: str        # models.dev 提供商 ID（如 "anthropic"）

    # 功能
    reasoning: bool = False
    tool_call: bool = False
    attachment: bool = False       # 支持图片/文件附件（视觉）
    temperature: bool = False
    structured_output: bool = False
    open_weights: bool = False

    # 模态
    input_modalities: Tuple[str, ...] = ()    # ("text", "image", "pdf", ...)
    output_modalities: Tuple[str, ...] = ()

    # 限制
    context_window: int = 0
    max_output: int = 0
    max_input: Optional[int] = None

    # 费用（每百万 token，美元）
    cost_input: float = 0.0
    cost_output: float = 0.0
    cost_cache_read: Optional[float] = None
    cost_cache_write: Optional[float] = None

    # 元数据
    knowledge_cutoff: str = ""
    release_date: str = ""
    status: str = ""          # "alpha", "beta", "deprecated", or ""
    interleaved: Any = False  # True or {"field": "reasoning_content"}

    def has_cost_data(self) -> bool:
        return self.cost_input > 0 or self.cost_output > 0

    def supports_vision(self) -> bool:
        return self.attachment or "image" in self.input_modalities

    def supports_pdf(self) -> bool:
        return "pdf" in self.input_modalities

    def supports_audio_input(self) -> bool:
        return "audio" in self.input_modalities

    def format_cost(self) -> str:
        """人类可读的费用字符串，例如 '$3.00/M in, $15.00/M out'。"""
        if not self.has_cost_data():
            return "unknown"
        parts = [f"${self.cost_input:.2f}/M in", f"${self.cost_output:.2f}/M out"]
        if self.cost_cache_read is not None:
            parts.append(f"cache read ${self.cost_cache_read:.2f}/M")
        return ", ".join(parts)

    def format_capabilities(self) -> str:
        """人类可读的功能描述，例如 'reasoning, tools, vision, PDF'。"""
        caps = []
        if self.reasoning:
            caps.append("reasoning")
        if self.tool_call:
            caps.append("tools")
        if self.supports_vision():
            caps.append("vision")
        if self.supports_pdf():
            caps.append("PDF")
        if self.supports_audio_input():
            caps.append("audio")
        if self.structured_output:
            caps.append("structured output")
        if self.open_weights:
            caps.append("open weights")
        return ", ".join(caps) if caps else "basic"


@dataclass
class ProviderInfo:
    """来自 models.dev 的提供商完整元数据。"""

    id: str                         # models.dev 提供商 ID
    name: str                       # 显示名称
    env: Tuple[str, ...]            # API 密钥的环境变量名
    api: str                        # 基础 URL
    doc: str = ""                   # 文档 URL
    model_count: int = 0


# ---------------------------------------------------------------------------
# 提供商 ID 映射：Hermes <-> models.dev
# ---------------------------------------------------------------------------

# Hermes 提供商名称 → models.dev 提供商 ID
PROVIDER_TO_MODELS_DEV: Dict[str, str] = {
    "openrouter": "openrouter",
    "anthropic": "anthropic",
    "openai": "openai",
    "openai-codex": "openai",
    "zai": "zai",
    "kimi-coding": "kimi-for-coding",
    "kimi-coding-cn": "kimi-for-coding",
    "minimax": "minimax",
    "minimax-cn": "minimax-cn",
    "deepseek": "deepseek",
    "alibaba": "alibaba",
    "qwen-oauth": "alibaba",
    "copilot": "github-copilot",
    "ai-gateway": "vercel",
    "opencode-zen": "opencode",
    "opencode-go": "opencode-go",
    "kilocode": "kilo",
    "fireworks": "fireworks-ai",
    "huggingface": "huggingface",
    "gemini": "google",
    "google": "google",
    "xai": "xai",
    "xiaomi": "xiaomi",
    "nvidia": "nvidia",
    "groq": "groq",
    "mistral": "mistral",
    "togetherai": "togetherai",
    "perplexity": "perplexity",
    "cohere": "cohere",
    "ollama-cloud": "ollama-cloud",
}

# 反向映射：models.dev → Hermes（延迟构建）
_MODELS_DEV_TO_PROVIDER: Optional[Dict[str, str]] = None



def _get_cache_path() -> Path:
    """返回磁盘缓存文件的路径。"""
    from hermes_constants import get_hermes_home
    return get_hermes_home() / "models_dev_cache.json"


def _load_disk_cache() -> Dict[str, Any]:
    """从磁盘缓存加载 models.dev 数据。"""
    try:
        cache_path = _get_cache_path()
        if cache_path.exists():
            with open(cache_path, encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        logger.debug("Failed to load models.dev disk cache: %s", e)
    return {}


def _save_disk_cache(data: Dict[str, Any]) -> None:
    """原子性地将 models.dev 数据保存到磁盘缓存。"""
    try:
        cache_path = _get_cache_path()
        atomic_json_write(cache_path, data, indent=None, separators=(",", ":"))
    except Exception as e:
        logger.debug("Failed to save models.dev disk cache: %s", e)


def fetch_models_dev(force_refresh: bool = False) -> Dict[str, Any]:
    """获取 models.dev 注册表。内存缓存（1 小时）+ 磁盘回退。

    返回以提供商 ID 为键的完整注册表字典，失败时返回空字典。
    """
    global _models_dev_cache, _models_dev_cache_time

    # 检查内存缓存
    if (
        not force_refresh
        and _models_dev_cache
        and (time.time() - _models_dev_cache_time) < _MODELS_DEV_CACHE_TTL
    ):
        return _models_dev_cache

    # 尝试网络获取
    try:
        response = requests.get(MODELS_DEV_URL, timeout=15)
        response.raise_for_status()
        data = response.json()
        if isinstance(data, dict) and data:
            _models_dev_cache = data
            _models_dev_cache_time = time.time()
            _save_disk_cache(data)
            logger.debug(
                "Fetched models.dev registry: %d providers, %d total models",
                len(data),
                sum(len(p.get("models", {})) for p in data.values() if isinstance(p, dict)),
            )
            return data
    except Exception as e:
        logger.debug("Failed to fetch models.dev: %s", e)

    # 回退到磁盘缓存——使用较短的 TTL（5 分钟），
    # 这样很快就会重试网络获取，而不是一整个小时都使用过期数据。
    if not _models_dev_cache:
        _models_dev_cache = _load_disk_cache()
        if _models_dev_cache:
            _models_dev_cache_time = time.time() - _MODELS_DEV_CACHE_TTL + 300
            logger.debug("Loaded models.dev from disk cache (%d providers)", len(_models_dev_cache))

    return _models_dev_cache


def lookup_models_dev_context(provider: str, model: str) -> Optional[int]:
    """在 models.dev 中查找提供商+模型组合的 context_length。

    返回以 token 为单位的上下文窗口大小，未找到时返回 None。
    支持大小写不敏感匹配，过滤掉 context=0 的条目。
    """
    mdev_provider_id = PROVIDER_TO_MODELS_DEV.get(provider)
    if not mdev_provider_id:
        return None

    data = fetch_models_dev()
    provider_data = data.get(mdev_provider_id)
    if not isinstance(provider_data, dict):
        return None

    models = provider_data.get("models", {})
    if not isinstance(models, dict):
        return None

    # 精确匹配
    entry = models.get(model)
    if entry:
        ctx = _extract_context(entry)
        if ctx:
            return ctx

    # 大小写不敏感匹配
    model_lower = model.lower()
    for mid, mdata in models.items():
        if mid.lower() == model_lower:
            ctx = _extract_context(mdata)
            if ctx:
                return ctx

    return None


def _extract_context(entry: Dict[str, Any]) -> Optional[int]:
    """从 models.dev 模型条目中提取 context_length。

    对于无效/零值返回 None（某些音频/图像模型的 context=0）。
    """
    if not isinstance(entry, dict):
        return None
    limit = entry.get("limit")
    if not isinstance(limit, dict):
        return None
    ctx = limit.get("context")
    if isinstance(ctx, (int, float)) and ctx > 0:
        return int(ctx)
    return None


# ---------------------------------------------------------------------------
# 模型功能元数据
# ---------------------------------------------------------------------------


@dataclass
class ModelCapabilities:
    """来自 models.dev 的模型结构化功能元数据。"""

    supports_tools: bool = True
    supports_vision: bool = False
    supports_reasoning: bool = False
    context_window: int = 200000
    max_output_tokens: int = 8192
    model_family: str = ""


def _get_provider_models(provider: str) -> Optional[Dict[str, Any]]:
    """将 Hermes 提供商 ID 解析为其在 models.dev 中的 models 字典。

    返回 models 字典或 None（如果提供商未知或无数据）。
    """
    mdev_provider_id = PROVIDER_TO_MODELS_DEV.get(provider)
    if not mdev_provider_id:
        return None

    data = fetch_models_dev()
    provider_data = data.get(mdev_provider_id)
    if not isinstance(provider_data, dict):
        return None

    models = provider_data.get("models", {})
    if not isinstance(models, dict):
        return None

    return models


def _find_model_entry(models: Dict[str, Any], model: str) -> Optional[Dict[str, Any]]:
    """通过精确匹配查找模型条目，然后进行大小写不敏感的回退。"""
    # 精确匹配
    entry = models.get(model)
    if isinstance(entry, dict):
        return entry

    # 大小写不敏感匹配
    model_lower = model.lower()
    for mid, mdata in models.items():
        if mid.lower() == model_lower and isinstance(mdata, dict):
            return mdata

    return None


def get_model_capabilities(provider: str, model: str) -> Optional[ModelCapabilities]:
    """从 models.dev 缓存中查找完整的功能元数据。

    使用现有的 fetch_models_dev() 和 PROVIDER_TO_MODELS_DEV 映射。
    未找到模型时返回 None。

    从模型条目字段中提取：
      - reasoning  (bool)  → supports_reasoning
      - tool_call  (bool)  → supports_tools
      - attachment (bool)  → supports_vision
      - limit.context (int) → context_window
      - limit.output  (int) → max_output_tokens
      - family     (str)   → model_family
    """
    models = _get_provider_models(provider)
    if models is None:
        return None

    entry = _find_model_entry(models, model)
    if entry is None:
        return None

    # 提取功能标志（缺失时默认为 False）
    supports_tools = bool(entry.get("tool_call", False))
    # 视觉：同时检查 `attachment` 标志和 `modalities.input` 中的 "image"。
    # 某些模型（如 gemma-4）在输入模态中列出 image 但没有 attachment。
    input_mods = entry.get("modalities", {})
    if isinstance(input_mods, dict):
        input_mods = input_mods.get("input", [])
    else:
        input_mods = []
    supports_vision = bool(entry.get("attachment", False)) or "image" in input_mods
    supports_reasoning = bool(entry.get("reasoning", False))

    # 提取限制
    limit = entry.get("limit", {})
    if not isinstance(limit, dict):
        limit = {}

    ctx = limit.get("context")
    context_window = int(ctx) if isinstance(ctx, (int, float)) and ctx > 0 else 200000

    out = limit.get("output")
    max_output_tokens = int(out) if isinstance(out, (int, float)) and out > 0 else 8192

    model_family = entry.get("family", "") or ""

    return ModelCapabilities(
        supports_tools=supports_tools,
        supports_vision=supports_vision,
        supports_reasoning=supports_reasoning,
        context_window=context_window,
        max_output_tokens=max_output_tokens,
        model_family=model_family,
    )


def list_provider_models(provider: str) -> List[str]:
    """返回提供商在 models.dev 中的所有模型 ID。

    提供商未知或无数据时返回空列表。
    """
    models = _get_provider_models(provider)
    if models is None:
        return []
    return list(models.keys())


# 匹配非智能体或噪声模型的模式（TTS、嵌入、
# 带日期的预览快照、仅直播/流式、仅图像模型）。
import re
_NOISE_PATTERNS: re.Pattern = re.compile(
    r"-tts\b|embedding|live-|-(preview|exp)-\d{2,4}[-_]|"
    r"-image\b|-image-preview\b|-customtools\b",
    re.IGNORECASE,
)


def list_agentic_models(provider: str) -> List[str]:
    """返回 models.dev 中适合智能体使用的模型 ID。

    过滤 tool_call=True 并排除噪声（TTS、嵌入、
    带日期的预览快照、直播/流式、仅图像模型）。
    任何失败时返回空列表。
    """
    models = _get_provider_models(provider)
    if models is None:
        return []

    result = []
    for mid, entry in models.items():
        if not isinstance(entry, dict):
            continue
        if not entry.get("tool_call", False):
            continue
        if _NOISE_PATTERNS.search(mid):
            continue
        result.append(mid)
    return result



# ---------------------------------------------------------------------------
# 丰富数据类构造器——将原始 models.dev JSON 解析为数据类
# ---------------------------------------------------------------------------

def _parse_model_info(model_id: str, raw: Dict[str, Any], provider_id: str) -> ModelInfo:
    """将原始 models.dev 模型条目字典转换为 ModelInfo 数据类。"""
    limit = raw.get("limit") or {}
    if not isinstance(limit, dict):
        limit = {}

    cost = raw.get("cost") or {}
    if not isinstance(cost, dict):
        cost = {}

    modalities = raw.get("modalities") or {}
    if not isinstance(modalities, dict):
        modalities = {}

    input_mods = modalities.get("input") or []
    output_mods = modalities.get("output") or []

    ctx = limit.get("context")
    ctx_int = int(ctx) if isinstance(ctx, (int, float)) and ctx > 0 else 0
    out = limit.get("output")
    out_int = int(out) if isinstance(out, (int, float)) and out > 0 else 0
    inp = limit.get("input")
    inp_int = int(inp) if isinstance(inp, (int, float)) and inp > 0 else None

    return ModelInfo(
        id=model_id,
        name=raw.get("name", "") or model_id,
        family=raw.get("family", "") or "",
        provider_id=provider_id,
        reasoning=bool(raw.get("reasoning", False)),
        tool_call=bool(raw.get("tool_call", False)),
        attachment=bool(raw.get("attachment", False)),
        temperature=bool(raw.get("temperature", False)),
        structured_output=bool(raw.get("structured_output", False)),
        open_weights=bool(raw.get("open_weights", False)),
        input_modalities=tuple(input_mods) if isinstance(input_mods, list) else (),
        output_modalities=tuple(output_mods) if isinstance(output_mods, list) else (),
        context_window=ctx_int,
        max_output=out_int,
        max_input=inp_int,
        cost_input=float(cost.get("input", 0) or 0),
        cost_output=float(cost.get("output", 0) or 0),
        cost_cache_read=float(cost["cache_read"]) if "cache_read" in cost and cost["cache_read"] is not None else None,
        cost_cache_write=float(cost["cache_write"]) if "cache_write" in cost and cost["cache_write"] is not None else None,
        knowledge_cutoff=raw.get("knowledge", "") or "",
        release_date=raw.get("release_date", "") or "",
        status=raw.get("status", "") or "",
        interleaved=raw.get("interleaved", False),
    )


def _parse_provider_info(provider_id: str, raw: Dict[str, Any]) -> ProviderInfo:
    """将原始 models.dev 提供商条目字典转换为 ProviderInfo。"""
    env = raw.get("env") or []
    models = raw.get("models") or {}
    return ProviderInfo(
        id=provider_id,
        name=raw.get("name", "") or provider_id,
        env=tuple(env) if isinstance(env, list) else (),
        api=raw.get("api", "") or "",
        doc=raw.get("doc", "") or "",
        model_count=len(models) if isinstance(models, dict) else 0,
    )


# ---------------------------------------------------------------------------
# 提供商级查询
# ---------------------------------------------------------------------------

def get_provider_info(provider_id: str) -> Optional[ProviderInfo]:
    """获取 models.dev 中的完整提供商元数据。

    接受 Hermes 提供商 ID（如 "kilocode"）或 models.dev ID（如 "kilo"）。
    提供商不在目录中时返回 None。
    """
    # 将 Hermes ID 解析为 models.dev ID
    mdev_id = PROVIDER_TO_MODELS_DEV.get(provider_id, provider_id)

    data = fetch_models_dev()
    raw = data.get(mdev_id)
    if not isinstance(raw, dict):
        return None

    return _parse_provider_info(mdev_id, raw)


# ---------------------------------------------------------------------------
# 模型级查询（丰富的 ModelInfo）
# ---------------------------------------------------------------------------

def get_model_info(
    provider_id: str, model_id: str
) -> Optional[ModelInfo]:
    """获取 models.dev 中的完整模型元数据。

    接受 Hermes 或 models.dev 提供商 ID。先尝试精确匹配，
    然后进行大小写不敏感的回退。未找到时返回 None。
    """
    mdev_id = PROVIDER_TO_MODELS_DEV.get(provider_id, provider_id)

    data = fetch_models_dev()
    pdata = data.get(mdev_id)
    if not isinstance(pdata, dict):
        return None

    models = pdata.get("models", {})
    if not isinstance(models, dict):
        return None

    # 精确匹配
    raw = models.get(model_id)
    if isinstance(raw, dict):
        return _parse_model_info(model_id, raw, mdev_id)

    # 大小写不敏感的回退
    model_lower = model_id.lower()
    for mid, mdata in models.items():
        if mid.lower() == model_lower and isinstance(mdata, dict):
            return _parse_model_info(mid, mdata, mdev_id)

    return None


