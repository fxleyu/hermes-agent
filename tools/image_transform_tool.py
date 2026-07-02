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

_SYSTEM_PROMPT = """\
You are a world-class image editing and style transfer artist. \
Your goal is to preserve the original image as much as possible, making only the minimum changes \
necessary to fulfill the user's request. The final result must be faithful to the source, \
meet the user's expectations, and demonstrate excellent artistic quality.

## Priority (highest to lowest)

1. Accurately execute the user's requested edit or style transformation.
2. Preserve the subject's identity, appearance, pose, proportions, perspective, composition, and recognizable structure.
3. Maintain thematic coherence, visual style consistency, and narrative integrity.
4. Elevate artistic quality, aesthetics, and professional polish.
5. Remove only elements that are unnecessary or degrade image quality — avoid over-editing.

## Editing Principles

### Preserve Source Features

Unless the user explicitly requests otherwise, retain:
- Subject identity and appearance
- Facial features, expressions, and posture
- Recognizable elements: buildings, objects, logos
- Existing text content
- Composition, camera angle, and perspective
- Lighting direction, shadow relationships, and spatial layout

The output must be clearly recognizable as derived from the original — not a completely new creation.

### Minimal Intervention

Modify only what is strictly necessary to achieve the user's goal. \
Do not repaint the entire image for aesthetic reasons. \
Do not alter content unrelated to the request.

### No Unsolicited Additions

Unless the user explicitly asks, do not:
- Add new people, animals, objects, or scene elements
- Insert text, logos, or decorations
- Change the number of subjects
- Alter the narrative context or semantics

Never hallucinate significant content that does not exist in the source.

### Visual Consistency

Ensure all elements remain unified:
- Natural lighting
- Harmonious color palette
- Consistent shadows
- Realistic materials and textures
- Clean edges and seamless transitions
- Coherent fine details

Avoid style clashes, proportion errors, perspective anomalies, or obvious AI artifacts.

### Clean and Focused

If the original contains visual noise that detracts from the theme, simplify moderately. \
Reduce clutter, not substance. Remove irrelevant details only when doing so loses no important information.

## Conflict Resolution

When principles conflict, follow this decision order: \
first satisfy the user's request; then preserve the subject's identity, composition, and structure; \
finally optimize artistic expression. \
Always choose the smallest edit that achieves the user's goal rather than redesigning the entire image."""


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

        # 2. 读取配置
        resolved_model = model
        timeout = DEFAULT_TIMEOUT
        base_url = None
        api_key = None
        try:
            from hermes_cli.config import load_config
            cfg = load_config()
            it_cfg = cfg.get("auxiliary", {}).get("image_transform", {})
            if not resolved_model:
                resolved_model = it_cfg.get("model") or DEFAULT_MODEL
            _t = it_cfg.get("timeout")
            if _t is not None:
                timeout = float(_t)
            base_url = it_cfg.get("base_url") or None
            api_key = it_cfg.get("api_key") or None
        except Exception:
            pass
        if not resolved_model:
            resolved_model = DEFAULT_MODEL

        # 3. 判断是否使用 Images API（gpt-image / dall-e 系列）
        _model_lower = resolved_model.lower()
        _is_gpt_image = "gpt-image" in _model_lower or "dall-e" in _model_lower

        if _is_gpt_image:
            # — Images API 路径 —
            # gpt-image / dall-e 不支持 Chat Completions，必须用 images.edit
            import asyncio
            from agent.auxiliary_client import _resolve_task_provider_model, _get_cached_client

            r_provider, r_model, r_base_url, r_api_key, r_api_mode = _resolve_task_provider_model(
                task="image_transform",
                model=resolved_model,
                base_url=base_url,
                api_key=api_key,
            )
            client, final_model = _get_cached_client(
                r_provider,
                r_model,
                base_url=r_base_url,
                api_key=r_api_key,
                api_mode=r_api_mode,
            )
            if client is None:
                return json.dumps({
                    "success": False,
                    "error": "无法创建 LLM 客户端，请检查配置。",
                })

            # 根据原图宽高比选择最佳输出尺寸
            try:
                from PIL import Image as PILImage
                with PILImage.open(image_path) as img:
                    orig_w, orig_h = img.size
                ratio = orig_w / orig_h
                if ratio > 1.3:
                    output_size = "1536x1024"  # 横图
                elif ratio < 0.77:
                    output_size = "1024x1536"  # 竖图
                else:
                    output_size = "1024x1024"  # 方图
            except Exception:
                orig_w, orig_h = None, None
                output_size = "1024x1024"

            # 为 Images API 增强 prompt，注入保留原图的指令
            enhanced_prompt = (
                f"{prompt}\n\n"
                "IMPORTANT: Preserve the original image's composition, subject identity, "
                "pose, proportions, clothing, camera angle, and key background elements. "
                "Apply only the requested transformation — do not redesign the scene. "
                "No text, watermark, logo, or extra objects unless requested."
            )

            def _call_images_api():
                return client.images.edit(
                    model=final_model or resolved_model,
                    image=image_path.open("rb"),
                    prompt=enhanced_prompt,
                    n=1,
                    size=output_size,
                )

            logger.info("Image transform: calling Images API (model=%s, size=%s)", final_model or resolved_model, output_size)
            response = await asyncio.get_event_loop().run_in_executor(None, _call_images_api)

            # 提取结果
            img_data = response.data[0]
            if getattr(img_data, "b64_json", None):
                image_bytes = base64.b64decode(img_data.b64_json)
            elif getattr(img_data, "url", None):
                import httpx
                async with httpx.AsyncClient(timeout=60) as http_client:
                    dl_resp = await http_client.get(img_data.url)
                    dl_resp.raise_for_status()
                    image_bytes = dl_resp.content
            else:
                return json.dumps({
                    "success": False,
                    "error": "Images API 未返回图片数据。",
                })

            # 后处理：恢复原图宽高比
            out_ext = ".png"
            output_path = _get_output_dir() / f"img2img_{uuid.uuid4().hex[:12]}{out_ext}"
            if orig_w and orig_h:
                try:
                    from PIL import Image as PILImage
                    import io
                    im = PILImage.open(io.BytesIO(image_bytes)).convert("RGB")
                    w, h = im.size
                    target_ratio = orig_w / orig_h
                    current_ratio = w / h
                    if abs(current_ratio - target_ratio) > 0.02:
                        if current_ratio > target_ratio:
                            nw = round(h * target_ratio)
                            left = (w - nw) // 2
                            im = im.crop((left, 0, left + nw, h))
                        else:
                            nh = round(w / target_ratio)
                            top = (h - nh) // 2
                            im = im.crop((0, top, w, top + nh))
                    im = im.resize((orig_w, orig_h), PILImage.LANCZOS)
                    buf = io.BytesIO()
                    im.save(buf, "PNG", optimize=True)
                    image_bytes = buf.getvalue()
                except Exception as e:
                    logger.warning("Post-process resize failed, using raw output: %s", e)

            output_path.write_bytes(image_bytes)

            logger.info("Image transform complete: %s (%d bytes)", output_path, len(image_bytes))
            media_tag = f"MEDIA:{output_path}"
            return json.dumps({
                "success": True,
                "image_path": str(output_path),
                "media_tag": media_tag,
                "description": "图片转换完成。",
            })

        # — Chat Completions API 路径（适用于 Gemini 等模型）—

        # 获取原图尺寸用于后处理
        try:
            from PIL import Image as PILImage
            with PILImage.open(image_path) as img:
                orig_w, orig_h = img.size
        except Exception:
            orig_w, orig_h = None, None

        # 检测 MIME 类型并转为 base64 data URL
        mime_type = _detect_image_mime_type(image_path)
        if not mime_type:
            mime_type = "image/jpeg"
        image_data_url = _image_to_base64_data_url(image_path, mime_type)

        # 构造多模态消息
        messages = [
            {
                "role": "system",
                "content": _SYSTEM_PROMPT,
            },
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

        # 4. 调用 LLM
        call_kwargs: Dict[str, Any] = {
            "task": "image_transform",
            "messages": messages,
            "temperature": 0.6,
            "max_tokens": 16384,
            "timeout": timeout,
            "extra_body": {"modalities": ["text", "image"]},
        }
        if resolved_model:
            call_kwargs["model"] = resolved_model

        logger.info("Image transform: calling LLM (model=%s)", resolved_model or "config default")
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

        # 7. 保存生成的图片（后处理恢复原图宽高比）
        ext_map = {
            "image/png": ".png",
            "image/jpeg": ".jpg",
            "image/webp": ".webp",
            "image/gif": ".gif",
        }
        out_ext = ext_map.get(resp_mime, ".png")
        output_path = _get_output_dir() / f"img2img_{uuid.uuid4().hex[:12]}{out_ext}"

        if orig_w and orig_h:
            try:
                from PIL import Image as PILImage
                import io
                im = PILImage.open(io.BytesIO(image_bytes)).convert("RGB")
                w, h = im.size
                target_ratio = orig_w / orig_h
                current_ratio = w / h
                if abs(current_ratio - target_ratio) > 0.02:
                    if current_ratio > target_ratio:
                        nw = round(h * target_ratio)
                        left = (w - nw) // 2
                        im = im.crop((left, 0, left + nw, h))
                    else:
                        nh = round(w / target_ratio)
                        top = (h - nh) // 2
                        im = im.crop((0, top, w, top + nh))
                im = im.resize((orig_w, orig_h), PILImage.LANCZOS)
                buf = io.BytesIO()
                im.save(buf, "PNG", optimize=True)
                image_bytes = buf.getvalue()
                out_ext = ".png"
                output_path = _get_output_dir() / f"img2img_{uuid.uuid4().hex[:12]}{out_ext}"
            except Exception as e:
                logger.warning("Post-process resize failed, using raw output: %s", e)

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
