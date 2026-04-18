#!/usr/bin/env python3
"""
图生图工具模块 (Image Transform Tool)

通过 LLM Chat Completions API 实现图片转换和编辑。
将输入图片和文字提示词发送给支持图片生成的模型（如 Gemini Image），
模型返回经过转换/编辑的新图片。

支持的能力：
- 风格转换（如照片转水彩画、卡通化）
- 内容编辑（如更换背景、添加/移除物体）
- 语义局部修改（如"把图中的猫换成狗"）

用法：
    from tools.image_transform_tool import image_transform_tool
    import asyncio

    result = await image_transform_tool(
        image_source="https://example.com/photo.jpg",
        prompt="将这张照片变成梵高星空风格的油画",
    )
"""

import base64
import json
import logging
import os
import uuid
from pathlib import Path
from typing import Any, Awaitable, Dict, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

DEFAULT_MODEL = "pub-gemini-3-pro-image-preview"
DEFAULT_TIMEOUT = 120.0
_OUTPUT_DIR_NAME = "generated_images"


def _get_output_dir() -> Path:
    """获取图片输出目录，确保其存在。"""
    hermes_home = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
    output_dir = hermes_home / _OUTPUT_DIR_NAME
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


# ---------------------------------------------------------------------------
# 核心函数
# ---------------------------------------------------------------------------

async def image_transform_tool(
    image_source: str,
    prompt: str,
    model: Optional[str] = None,
) -> str:
    """对输入图片执行 AI 转换/编辑。

    参数：
        image_source: 图片 URL（http/https）或本地文件路径。
        prompt: 描述期望变换的文字提示词。
        model: 可选的模型名称覆盖。

    返回：
        包含结果的 JSON 字符串。
    """
    from agent.auxiliary_client import async_call_llm, extract_image_from_response
    from tools.vision_tools import (
        _download_image,
        _detect_image_mime_type,
        _image_to_base64_data_url,
    )

    temp_path = None
    try:
        # 1. 准备输入图片
        source = image_source.strip()
        if source.startswith(("http://", "https://")):
            # 远程图片 — 下载到临时目录
            tmp_dir = Path("/tmp/hermes_img2img")
            tmp_dir.mkdir(parents=True, exist_ok=True)
            ext = Path(source.split("?")[0]).suffix or ".jpg"
            temp_path = tmp_dir / f"input_{uuid.uuid4().hex[:12]}{ext}"
            await _download_image(source, temp_path)
            image_path = temp_path
        else:
            # 本地文件路径
            image_path = Path(source)
            if not image_path.exists():
                return json.dumps({
                    "success": False,
                    "error": f"文件不存在: {source}",
                })

        # 2. 检测 MIME 类型并转为 base64 data URL
        mime_type = _detect_image_mime_type(image_path)
        if not mime_type:
            mime_type = "image/jpeg"
        image_data_url = _image_to_base64_data_url(image_path, mime_type)

        # 3. 构造多模态消息
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": prompt,
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": image_data_url},
                    },
                ],
            }
        ]

        # 4. 读取配置中的超时时间
        timeout = DEFAULT_TIMEOUT
        try:
            from hermes_cli.config import load_config
            cfg = load_config()
            _t = cfg.get("auxiliary", {}).get("image_transform", {}).get("timeout")
            if _t is not None:
                timeout = float(_t)
        except Exception:
            pass

        # 5. 调用 LLM
        call_kwargs: Dict[str, Any] = {
            "task": "image_transform",
            "messages": messages,
            "temperature": 0.8,
            "max_tokens": 4096,
            "timeout": timeout,
            "extra_body": {"modalities": ["text", "image"]},
        }
        if model:
            call_kwargs["model"] = model

        logger.info("Image transform: calling LLM (model=%s)", model or "config default")
        response = await async_call_llm(**call_kwargs)

        # 6. 从响应中提取图片
        image_bytes, resp_mime, text_content = extract_image_from_response(response)

        if image_bytes is None:
            return json.dumps({
                "success": False,
                "error": (
                    "模型未返回图片。可能的原因：模型不支持图片生成，"
                    "或提示词需要调整。"
                    + (f" 模型回复: {text_content[:200]}" if text_content else "")
                ),
            })

        # 7. 保存生成的图片
        ext_map = {
            "image/png": ".png",
            "image/jpeg": ".jpg",
            "image/webp": ".webp",
            "image/gif": ".gif",
        }
        out_ext = ext_map.get(resp_mime, ".png")
        output_path = _get_output_dir() / f"img2img_{uuid.uuid4().hex[:12]}{out_ext}"
        output_path.write_bytes(image_bytes)

        logger.info(
            "Image transform complete: %s (%d bytes)",
            output_path, len(image_bytes),
        )

        # 8. 返回结果（包含 MEDIA: 标签供 gateway 发送）
        media_tag = f"MEDIA:{output_path}"
        return json.dumps({
            "success": True,
            "image_path": str(output_path),
            "media_tag": media_tag,
            "description": text_content or "图片转换完成。",
        })

    except Exception as e:
        logger.error("Image transform error: %s", e, exc_info=True)
        return json.dumps({
            "success": False,
            "error": f"图片转换失败: {str(e)}",
        })
    finally:
        # 清理临时下载文件
        if temp_path and temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass


# ---------------------------------------------------------------------------
# check_fn: 检查图生图功能是否可用
# ---------------------------------------------------------------------------

def check_image_transform_requirements() -> bool:
    """检查 image_transform 任务是否已配置可用的提供者。"""
    try:
        from hermes_cli.config import load_config
        cfg = load_config()
        aux = cfg.get("auxiliary", {})
        task_cfg = aux.get("image_transform", {})
        if task_cfg.get("base_url") or task_cfg.get("provider"):
            return True
        return False
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Schema + Handler + 注册
# ---------------------------------------------------------------------------

IMAGE_TRANSFORM_SCHEMA = {
    "name": "image_transform",
    "description": (
        "Transform or edit an existing image using AI. Send an image plus "
        "a text prompt describing the desired changes. Capabilities include "
        "style transfer, content editing, background changes, artistic "
        "filters, object addition/removal, and semantic edits. "
        "Returns a MEDIA: path that the platform delivers as a native image."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "image_url": {
                "type": "string",
                "description": (
                    "Image URL (http/https) or local file path to transform."
                ),
            },
            "prompt": {
                "type": "string",
                "description": (
                    "Description of the desired transformation or edit. "
                    "Be specific about what changes you want."
                ),
            },
        },
        "required": ["image_url", "prompt"],
    },
}


def _handle_image_transform(args: Dict[str, Any], **kw: Any) -> Awaitable[str]:
    """image_transform 工具的调度处理函数。"""
    image_url = args.get("image_url", "")
    prompt = args.get("prompt", "")
    if not image_url:
        from tools.registry import tool_error
        return tool_error("image_url is required")
    if not prompt:
        from tools.registry import tool_error
        return tool_error("prompt is required")
    return image_transform_tool(image_url, prompt)


# 注册到工具注册表
from tools.registry import registry

registry.register(
    name="image_transform",
    toolset="image_transform",
    schema=IMAGE_TRANSFORM_SCHEMA,
    handler=_handle_image_transform,
    check_fn=check_image_transform_requirements,
    requires_env=[],
    is_async=True,
    emoji="🖌️",
)
