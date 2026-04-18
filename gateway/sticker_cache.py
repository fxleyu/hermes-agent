"""
Telegram 贴纸描述缓存。

当用户发送贴纸时，我们通过视觉工具对其进行描述，并以
file_unique_id 为键缓存描述结果，避免每次发送同一贴纸时
重复分析。描述内容简洁（1-2 句话）。

缓存位置：~/.hermes/sticker_cache.json
"""

import json
import time
from typing import Optional

from hermes_cli.config import get_hermes_home


CACHE_PATH = get_hermes_home() / "sticker_cache.json"

# 用于描述贴纸的视觉提示词 -- 保持简洁以节省 token
STICKER_VISION_PROMPT = (
    "Describe this sticker in 1-2 sentences. Focus on what it depicts -- "
    "character, action, emotion. Be concise and objective."
)


def _load_cache() -> dict:
    """从磁盘加载贴纸缓存。"""
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_cache(cache: dict) -> None:
    """将贴纸缓存保存到磁盘。"""
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(
        json.dumps(cache, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def get_cached_description(file_unique_id: str) -> Optional[dict]:
    """
    查找已缓存的贴纸描述。

    返回：
        包含 {description, emoji, set_name, cached_at} 键的字典，或 None。
    """
    cache = _load_cache()
    return cache.get(file_unique_id)


def cache_sticker_description(
    file_unique_id: str,
    description: str,
    emoji: str = "",
    set_name: str = "",
) -> None:
    """
    将贴纸描述存入缓存。

    参数：
        file_unique_id: Telegram 的稳定贴纸标识符。
        description:    视觉工具生成的描述文本。
        emoji:          关联的表情符号（如 "😀"）。
        set_name:       贴纸集名称（如有）。
    """
    cache = _load_cache()
    cache[file_unique_id] = {
        "description": description,
        "emoji": emoji,
        "set_name": set_name,
        "cached_at": time.time(),
    }
    _save_cache(cache)


def build_sticker_injection(
    description: str,
    emoji: str = "",
    set_name: str = "",
) -> str:
    """
    构建贴纸描述的暖风格注入文本。

    返回类似如下的字符串：
      [The user sent a sticker 😀 from "MyPack"~ It shows: "A cat waving" (=^.w.^=)]
    """
    context = ""
    if set_name and emoji:
        context = f" {emoji} from \"{set_name}\""
    elif emoji:
        context = f" {emoji}"

    return f"[The user sent a sticker{context}~ It shows: \"{description}\" (=^.w.^=)]"


def build_animated_sticker_injection(emoji: str = "") -> str:
    """
    为无法分析的动画/视频贴纸构建注入文本。
    """
    if emoji:
        return (
            f"[The user sent an animated sticker {emoji}~ "
            f"I can't see animated ones yet, but the emoji suggests: {emoji}]"
        )
    return "[The user sent an animated sticker~ I can't see animated ones yet]"
