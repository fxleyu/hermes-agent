#!/usr/bin/env python3
"""
视觉工具模块

本模块提供基于图片 URL 的视觉分析工具。
使用集中式辅助视觉路由器，可选择 OpenRouter、Nous、Codex、
原生 Anthropic 或自定义 OpenAI 兼容端点。

可用工具：
- vision_analyze_tool: 使用自定义提示分析 URL 图片

特性：
- 从 URL 下载图片并转换为 base64 以兼容 API
- 综合图片描述
- 基于用户查询的上下文感知分析
- 自动清理临时文件
- 完善的错误处理和校验
- 调试日志支持

用法：
    from vision_tools import vision_analyze_tool
    import asyncio

    # 分析图片
    result = await vision_analyze_tool(
        image_url="https://example.com/image.jpg",
        user_prompt="这栋建筑是什么建筑风格？"
    )
"""

import base64
import json
import logging
import os
import uuid
from pathlib import Path
from typing import Any, Awaitable, Dict, Optional
from urllib.parse import urlparse
import httpx
from agent.auxiliary_client import async_call_llm, extract_content_or_reasoning
from tools.debug_helpers import DebugSession
from tools.website_policy import check_website_access

logger = logging.getLogger(__name__)

_debug = DebugSession("vision_tools", env_var="VISION_TOOLS_DEBUG")

# 可配置的 HTTP 下载超时，用于 _download_image()。
# 与 auxiliary.vision.timeout（管控 LLM API 调用）分开。
# 解析优先级：config.yaml auxiliary.vision.download_timeout → 环境变量 → 30秒默认值。
def _resolve_download_timeout() -> float:
    env_val = os.getenv("HERMES_VISION_DOWNLOAD_TIMEOUT", "").strip()
    if env_val:
        try:
            return float(env_val)
        except ValueError:
            pass
    try:
        from hermes_cli.config import load_config
        cfg = load_config()
        val = cfg.get("auxiliary", {}).get("vision", {}).get("download_timeout")
        if val is not None:
            return float(val)
    except Exception:
        pass
    return 30.0

_VISION_DOWNLOAD_TIMEOUT = _resolve_download_timeout()

# 下载图片文件大小硬上限（50 MB）。防止攻击者托管的
# 超大文件或解压炸弹导致内存溢出。
_VISION_MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024


def _validate_image_url(url: str) -> bool:
    """
    基本校验图片 URL 格式。

    参数：
        url (str): 要校验的 URL

    返回：
        bool: 如果 URL 看起来有效返回 True，否则返回 False
    """
    if not url or not isinstance(url, str):
        return False

    # 基本 HTTP/HTTPS URL 检查
    if not url.startswith(("http://", "https://")):
        return False

    # 解析以确保至少有网络位置；仍允许没有文件扩展名的 URL
    # （例如重定向到图片的 CDN 端点）。
    parsed = urlparse(url)
    if not parsed.netloc:
        return False

    # 阻止私有/内部地址以防止 SSRF 攻击
    from tools.url_safety import is_safe_url
    if not is_safe_url(url):
        return False

    return True


def _detect_image_mime_type(image_path: Path) -> Optional[str]:
    """当文件看起来是支持的图片格式时返回 MIME 类型。"""
    with image_path.open("rb") as f:
        header = f.read(64)

    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if header.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if header.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if header.startswith(b"BM"):
        return "image/bmp"
    if len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return "image/webp"
    if image_path.suffix.lower() == ".svg":
        head = image_path.read_text(encoding="utf-8", errors="ignore")[:4096].lower()
        if "<svg" in head:
            return "image/svg+xml"
    return None


async def _download_image(image_url: str, destination: Path, max_retries: int = 3) -> Path:
    """
    从 URL 异步下载图片到本地目标路径，带有重试逻辑。

    参数：
        image_url (str): 要下载的图片 URL
        destination (Path): 图片保存路径
        max_retries (int): 最大重试次数（默认: 3）

    返回：
        Path: 已下载图片的路径

    异常：
        Exception: 如果所有重试后仍然下载失败
    """
    import asyncio
    
    # 如果父目录不存在则创建
    destination.parent.mkdir(parents=True, exist_ok=True)
    
    async def _ssrf_redirect_guard(response):
        """重新验证每个重定向目标以防止基于重定向的 SSRF 攻击。

        没有此检查，攻击者可以托管一个公共 URL 然后 302 重定向
        到 http://169.254.169.254/，从而绕过预检的 is_safe_url 检查。

        必须是异步的，因为 httpx.AsyncClient 会 await 事件钩子。
        """
        if response.is_redirect and response.next_request:
            redirect_url = str(response.next_request.url)
            from tools.url_safety import is_safe_url
            if not is_safe_url(redirect_url):
                raise ValueError(
                    f"Blocked redirect to private/internal address: {redirect_url}"
                )

    last_error = None
    for attempt in range(max_retries):
        try:
            blocked = check_website_access(image_url)
            if blocked:
                raise PermissionError(blocked["message"])

            # 使用适当的请求头异步下载图片
            # 启用 follow_redirects 以处理重定向的图片 CDN（如 Imgur、Picsum）
            # SSRF 防护：event_hooks 对每个重定向目标验证是否为私有 IP 范围
            async with httpx.AsyncClient(
                timeout=_VISION_DOWNLOAD_TIMEOUT,
                follow_redirects=True,
                event_hooks={"response": [_ssrf_redirect_guard]},
            ) as client:
                response = await client.get(
                    image_url,
                    headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                        "Accept": "image/*,*/*;q=0.8",
                    },
                )
                response.raise_for_status()

                # 通过 Content-Length 请求头提前拒绝过大的图片。
                cl = response.headers.get("content-length")
                if cl and int(cl) > _VISION_MAX_DOWNLOAD_BYTES:
                    raise ValueError(
                        f"Image too large ({int(cl)} bytes, max {_VISION_MAX_DOWNLOAD_BYTES})"
                    )

                final_url = str(response.url)
                blocked = check_website_access(final_url)
                if blocked:
                    raise PermissionError(blocked["message"])
                
                # 保存图片内容（再次检查实际大小）
                body = response.content
                if len(body) > _VISION_MAX_DOWNLOAD_BYTES:
                    raise ValueError(
                        f"Image too large ({len(body)} bytes, max {_VISION_MAX_DOWNLOAD_BYTES})"
                    )
                destination.write_bytes(body)
            
            return destination
        except Exception as e:
            last_error = e
            if attempt < max_retries - 1:
                wait_time = 2 ** (attempt + 1)  # 2s, 4s, 8s
                logger.warning("Image download failed (attempt %s/%s): %s", attempt + 1, max_retries, str(e)[:50])
                logger.warning("Retrying in %ss...", wait_time)
                await asyncio.sleep(wait_time)
            else:
                logger.error(
                    "Image download failed after %s attempts: %s",
                    max_retries,
                    str(e)[:100],
                    exc_info=True,
                )
    
    if last_error is None:
        raise RuntimeError(
            f"_download_image exited retry loop without attempting (max_retries={max_retries})"
        )
    raise last_error


def _determine_mime_type(image_path: Path) -> str:
    """
    根据文件扩展名确定图片的 MIME 类型。

    参数：
        image_path (Path): 图片文件路径

    返回：
        str: MIME 类型（如果未知则默认为 image/jpeg）
    """
    extension = image_path.suffix.lower()
    mime_types = {
        '.jpg': 'image/jpeg',
        '.jpeg': 'image/jpeg',
        '.png': 'image/png',
        '.gif': 'image/gif',
        '.bmp': 'image/bmp',
        '.webp': 'image/webp',
        '.svg': 'image/svg+xml'
    }
    return mime_types.get(extension, 'image/jpeg')


def _image_to_base64_data_url(image_path: Path, mime_type: Optional[str] = None) -> str:
    """
    将图片文件转换为 base64 编码的 data URL。

    参数：
        image_path (Path): 图片文件路径
        mime_type (Optional[str]): 图片的 MIME 类型（如果为 None 则自动检测）

    返回：
        str: Base64 编码的 data URL（例如 "data:image/jpeg;base64,..."）
    """
    # 以字节方式读取图片
    data = image_path.read_bytes()
    
    # 编码为 base64
    encoded = base64.b64encode(data).decode("ascii")

    # 确定 MIME 类型
    mime = mime_type or _determine_mime_type(image_path)

    # 创建 data URL
    data_url = f"data:{mime};base64,{encoded}"
    
    return data_url


# 视觉 API 载荷硬限制（20 MB）—— 对齐最严格的主流提供商（Gemini 内联数据限制）。
# 超过此大小的图片将被拒绝。
_MAX_BASE64_BYTES = 20 * 1024 * 1024

# API 失败时自动缩放的目标大小（5 MB）。当提供商拒绝图片后，
# 会将图片缩小到此目标大小并重试一次。
_RESIZE_TARGET_BYTES = 5 * 1024 * 1024


def _is_image_size_error(error: Exception) -> bool:
    """检测 API 错误是否与图片或负载大小相关。"""
    err_str = str(error).lower()
    return any(hint in err_str for hint in (
        "too large", "payload", "413", "content_too_large",
        "request_too_large", "image_url", "invalid_request",
        "exceeds", "size limit",
    ))


def _resize_image_for_vision(image_path: Path, mime_type: Optional[str] = None,
                              max_base64_bytes: int = _RESIZE_TARGET_BYTES) -> str:
    """将图片转换为 base64 data URL，如果过大则自动调整尺寸。

    优先使用 Pillow 逐步缩小超大图片。如果 Pillow 未安装或调整大小后
    仍超出限制，则回退到原始字节并让调用方处理大小检查。

    返回 base64 data URL 字符串。
    """
    # 快速文件大小估算：base64 扩展约 4/3，加上 data URL 头部开销。
    # 如果 Pillow 可以直接调整大小，则跳过昂贵的完整读取+编码。
    file_size = image_path.stat().st_size
    estimated_b64 = (file_size * 4) // 3 + 100  # ~header overhead
    if estimated_b64 <= max_base64_bytes:
        # 足够小——直接编码。
        data_url = _image_to_base64_data_url(image_path, mime_type=mime_type)
        if len(data_url) <= max_base64_bytes:
            return data_url
    else:
        data_url = None  # 延迟完整编码；先尝试 Pillow 调整大小

    # 尝试使用 Pillow 自动调整大小（软依赖）
    try:
        from PIL import Image
        import io as _io
    except ImportError:
        logger.info("Pillow not installed — cannot auto-resize oversized image")
        if data_url is None:
            data_url = _image_to_base64_data_url(image_path, mime_type=mime_type)
        return data_url  # caller will raise the size error

    logger.info("Image file is %.1f MB (estimated base64 %.1f MB, limit %.1f MB), auto-resizing...",
                file_size / (1024 * 1024), estimated_b64 / (1024 * 1024),
                max_base64_bytes / (1024 * 1024))

    mime = mime_type or _determine_mime_type(image_path)
    # 选择输出格式：照片用 JPEG（更小），需要透明度用 PNG
    pil_format = "PNG" if mime == "image/png" else "JPEG"
    out_mime = "image/png" if pil_format == "PNG" else "image/jpeg"

    try:
        img = Image.open(image_path)
    except Exception as exc:
        logger.info("Pillow cannot open image for resizing: %s", exc)
        if data_url is None:
            data_url = _image_to_base64_data_url(image_path, mime_type=mime_type)
        return data_url  # fall through to size-check in caller
    # 将 RGBA 转换为 RGB 以输出 JPEG
    if pil_format == "JPEG" and img.mode in ("RGBA", "P"):
        img = img.convert("RGB")

    # 策略：每次将尺寸减半直到 base64 符合要求，最多 4 轮。
    # 对于 JPEG，还在每个尺寸步骤尝试降低质量。
    # 对于 PNG，质量无关——只有尺寸缩减有效。
    quality_steps = (85, 70, 50) if pil_format == "JPEG" else (None,)
    prev_dims = (img.width, img.height)
    candidate = None  # will be set on first loop iteration

    for attempt in range(5):
        if attempt > 0:
            # 等比缩放：将较长边减半，按比例缩放较短边
            # 以保持宽高比（最小尺寸 64）。
            scale = 0.5
            new_w = max(int(img.width * scale), 64)
            new_h = max(int(img.height * scale), 64)
            # 从触及下限的维度重新推导缩放因子，
            # 确保两个轴按相同因子缩小。
            if new_w == 64 and img.width > 0:
                effective_scale = 64 / img.width
                new_h = max(int(img.height * effective_scale), 64)
            elif new_h == 64 and img.height > 0:
                effective_scale = 64 / img.height
                new_w = max(int(img.width * effective_scale), 64)
            # 如果尺寸无法进一步缩小则停止
            if (new_w, new_h) == prev_dims:
                break
            img = img.resize((new_w, new_h), Image.LANCZOS)
            prev_dims = (new_w, new_h)
            logger.info("Resized to %dx%d (attempt %d)", new_w, new_h, attempt)

        for q in quality_steps:
            buf = _io.BytesIO()
            save_kwargs = {"format": pil_format}
            if q is not None:
                save_kwargs["quality"] = q
            img.save(buf, **save_kwargs)
            encoded = base64.b64encode(buf.getvalue()).decode("ascii")
            candidate = f"data:{out_mime};base64,{encoded}"
            if len(candidate) <= max_base64_bytes:
                logger.info("Auto-resized image fits: %.1f MB (quality=%s, %dx%d)",
                            len(candidate) / (1024 * 1024), q,
                            img.width, img.height)
                return candidate

    # 如果仍然无法足够小，返回最佳尝试并让调用方决定
    if candidate is not None:
        logger.warning("Auto-resize could not fit image under %.1f MB (best: %.1f MB)",
                       max_base64_bytes / (1024 * 1024), len(candidate) / (1024 * 1024))
        return candidate

    # 不应到达这里，但回退到完整编码
    return data_url or _image_to_base64_data_url(image_path, mime_type=mime_type)


async def vision_analyze_tool(
    image_url: str,
    user_prompt: str,
    model: str = None,
) -> str:
    """
    使用视觉 AI 分析来自 URL 或本地文件路径的图片。

    此工具接受 HTTP/HTTPS URL 或本地文件路径。对于 URL，
    会先下载图片。两种情况下，图片都会被转换为 base64
    并通过 OpenRouter API 使用 Gemini 3 Flash Preview 处理。

    user_prompt 参数预期已由调用函数（通常是 model_tools.py）
    预格式化，包含完整描述请求和特定问题。

    参数：
        image_url (str): 要分析的图片 URL 或本地文件路径。
                         接受 http://、https:// URL 或绝对/相对文件路径。
        user_prompt (str): 为视觉模型预格式化的提示
        model (str): 使用的视觉模型（默认: google/gemini-3-flash-preview）

    返回：
        str: 包含分析结果的 JSON 字符串，结构如下：
             {
                 "success": bool,
                 "analysis": str（如果为 None 则默认为错误消息）
             }

    异常：
        Exception: 下载失败、分析失败或 API 密钥未设置时

    注意：
        - 对于 URL，临时图片存储在 ./temp_vision_images/ 并会被清理
        - 对于本地文件路径，直接使用文件且不会删除
        - 支持常见图片格式（JPEG、PNG、GIF、WebP 等）
    """
    debug_call_data = {
        "parameters": {
            "image_url": image_url,
            "user_prompt": user_prompt[:200] + "..." if len(user_prompt) > 200 else user_prompt,
            "model": model
        },
        "error": None,
        "success": False,
        "analysis_length": 0,
        "model_used": model,
        "image_size_bytes": 0
    }
    
    temp_image_path = None
    # 跟踪处理后是否需要清理文件。
    # 本地文件（如来自图片缓存的）不应被删除。
    should_cleanup = True
    detected_mime_type = None
    
    try:
        from tools.interrupt import is_interrupted
        if is_interrupted():
            return tool_error("Interrupted", success=False)

        logger.info("Analyzing image: %s", image_url[:60])
        logger.info("User prompt: %s", user_prompt[:100])
        
        # 判断是本地文件路径还是远程 URL
        # 去除 file:// 协议头，以便 file URI 解析为本地路径。
        resolved_url = image_url
        if resolved_url.startswith("file://"):
            resolved_url = resolved_url[len("file://"):]
        local_path = Path(os.path.expanduser(resolved_url))
        if local_path.is_file():
            # 本地文件路径（如来自平台图片缓存）——跳过下载
            logger.info("Using local image file: %s", image_url)
            temp_image_path = local_path
            should_cleanup = False  # 不删除缓存/本地文件
        elif _validate_image_url(image_url):
            # 远程 URL——下载到临时位置
            blocked = check_website_access(image_url)
            if blocked:
                raise PermissionError(blocked["message"])
            logger.info("Downloading image from URL...")
            temp_dir = Path("./temp_vision_images")
            temp_image_path = temp_dir / f"temp_image_{uuid.uuid4()}.jpg"
            await _download_image(image_url, temp_image_path)
            should_cleanup = True
        else:
            raise ValueError(
                "Invalid image source. Provide an HTTP/HTTPS URL or a valid local file path."
            )
        
        # 获取图片文件大小用于日志记录
        image_size_bytes = temp_image_path.stat().st_size
        image_size_kb = image_size_bytes / 1024
        logger.info("Image ready (%.1f KB)", image_size_kb)

        detected_mime_type = _detect_image_mime_type(temp_image_path)
        if not detected_mime_type:
            raise ValueError("Only real image files are supported for vision analysis.")
        
        # 将图片转换为 base64——先以原始分辨率发送。
        # 如果提供商拒绝（太大），则自动调整大小并重试。
        logger.info("Converting image to base64...")
        image_data_url = _image_to_base64_data_url(temp_image_path, mime_type=detected_mime_type)
        data_size_kb = len(image_data_url) / 1024
        logger.info("Image converted to base64 (%.1f KB)", data_size_kb)

        # 硬上限（20 MB）——没有提供商接受这么大的负载。
        if len(image_data_url) > _MAX_BASE64_BYTES:
            # 尝试缩小到 5 MB 再放弃。
            image_data_url = _resize_image_for_vision(
                temp_image_path, mime_type=detected_mime_type)
            if len(image_data_url) > _MAX_BASE64_BYTES:
                raise ValueError(
                    f"Image too large for vision API: base64 payload is "
                    f"{len(image_data_url) / (1024 * 1024):.1f} MB "
                    f"(limit {_MAX_BASE64_BYTES / (1024 * 1024):.0f} MB) "
                    f"even after resizing. "
                    f"Install Pillow (`pip install Pillow`) for better auto-resize, "
                    f"or compress the image manually."
                )

        debug_call_data["image_size_bytes"] = image_size_bytes
        
        # 直接使用提供的提示（model_tools.py 现在处理完整描述格式化）
        comprehensive_prompt = user_prompt
        
        # 准备包含 base64 编码图片的消息
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": comprehensive_prompt
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": image_data_url
                        }
                    }
                ]
            }
        ]
        
        logger.info("Processing image with vision model...")
        
        # 通过集中路由器调用视觉 API。
        # 从 config.yaml 读取超时（auxiliary.vision.timeout），默认 120秒。
        # 本地视觉模型（llama.cpp、ollama）可能需要超过 30秒。
        vision_timeout = 120.0
        try:
            from hermes_cli.config import load_config
            _cfg = load_config()
            _vt = _cfg.get("auxiliary", {}).get("vision", {}).get("timeout")
            if _vt is not None:
                vision_timeout = float(_vt)
        except Exception:
            pass
        call_kwargs = {
            "task": "vision",
            "messages": messages,
            "temperature": 0.1,
            "max_tokens": 2000,
            "timeout": vision_timeout,
        }
        if model:
            call_kwargs["model"] = model
        # 先尝试原始大小图片；如果因大小被拒绝，则缩小并重试。
        try:
            response = await async_call_llm(**call_kwargs)
        except Exception as _api_err:
            if (_is_image_size_error(_api_err)
                    and len(image_data_url) > _RESIZE_TARGET_BYTES):
                logger.info(
                    "API rejected image (%.1f MB, likely too large); "
                    "auto-resizing to ~%.0f MB and retrying...",
                    len(image_data_url) / (1024 * 1024),
                    _RESIZE_TARGET_BYTES / (1024 * 1024),
                )
                image_data_url = _resize_image_for_vision(
                    temp_image_path, mime_type=detected_mime_type)
                messages[0]["content"][1]["image_url"]["url"] = image_data_url
                response = await async_call_llm(**call_kwargs)
            else:
                raise
        
        # 提取分析结果——如果内容为空则回退到推理
        analysis = extract_content_or_reasoning(response)

        # 内容为空时重试一次（仅推理响应）
        if not analysis:
            logger.warning("Vision LLM returned empty content, retrying once")
            response = await async_call_llm(**call_kwargs)
            analysis = extract_content_or_reasoning(response)

        analysis_length = len(analysis)
        
        logger.info("Image analysis completed (%s characters)", analysis_length)
        
        # 准备成功响应
        result = {
            "success": True,
            "analysis": analysis or "There was a problem with the request and the image could not be analyzed."
        }
        
        debug_call_data["success"] = True
        debug_call_data["analysis_length"] = analysis_length

        # 记录调试信息
        _debug.log_call("vision_analyze_tool", debug_call_data)
        _debug.save()

        return json.dumps(result, indent=2, ensure_ascii=False)

    except Exception as e:
        error_msg = f"Error analyzing image: {str(e)}"
        logger.error("%s", error_msg, exc_info=True)
        
        # 检测视觉能力错误——给模型一个清晰的消息，
        # 以便它能告知用户而非返回晦涩的 API 错误。
        err_str = str(e).lower()
        if any(hint in err_str for hint in (
            "402", "insufficient", "payment required", "credits", "billing",
        )):
            analysis = (
                "Insufficient credits or payment required. Please top up your "
                f"API provider account and try again. Error: {e}"
            )
        elif any(hint in err_str for hint in (
            "does not support", "not support image",
            "content_policy", "multimodal",
            "unrecognized request argument", "image input",
        )):
            analysis = (
                f"{model} does not support vision or our request was not "
                f"accepted by the server. Error: {e}"
            )
        elif "invalid_request" in err_str or "image_url" in err_str:
            analysis = (
                "The vision API rejected the image. This can happen when the "
                "image is in an unsupported format, corrupted, or still too "
                "large after auto-resize. Try a smaller JPEG/PNG and retry. "
                f"Error: {e}"
            )
        else:
            analysis = (
                "There was a problem with the request and the image could not "
                f"be analyzed. Error: {e}"
            )
        
        # 准备错误响应
        result = {
            "success": False,
            "error": error_msg,
            "analysis": analysis,
        }
        
        debug_call_data["error"] = error_msg
        _debug.log_call("vision_analyze_tool", debug_call_data)
        _debug.save()
        
        return json.dumps(result, indent=2, ensure_ascii=False)
    
    finally:
        # 清理临时图片文件（但不清理本地/缓存文件）
        if should_cleanup and temp_image_path and temp_image_path.exists():
            try:
                temp_image_path.unlink()
                logger.debug("Cleaned up temporary image file")
            except Exception as cleanup_error:
                logger.warning(
                    "Could not delete temporary file: %s", cleanup_error, exc_info=True
                )


def check_vision_requirements() -> bool:
    """检查配置的运行时视觉路径是否能解析出客户端。"""
    try:
        from agent.auxiliary_client import resolve_vision_provider_client

        _provider, client, _model = resolve_vision_provider_client()
        return client is not None
    except Exception:
        return False



if __name__ == "__main__":
    """
    直接运行时的简单测试/演示
    """
    print("👁️ Vision Tools Module")
    print("=" * 40)
    
    # 检查视觉模型是否可用
    api_available = check_vision_requirements()
    
    if not api_available:
        print("❌ No auxiliary vision model available")
        print("Configure a supported multimodal backend (OpenRouter, Nous, Codex, Anthropic, or a custom OpenAI-compatible endpoint).")
        exit(1)
    else:
        print("✅ Vision model available")
    
    print("🛠️ Vision tools ready for use!")
    
    # 显示调试模式状态
    if _debug.active:
        print(f"🐛 Debug mode ENABLED - Session ID: {_debug.session_id}")
        print(f"   Debug logs will be saved to: ./logs/vision_tools_debug_{_debug.session_id}.json")
    else:
        print("🐛 Debug mode disabled (set VISION_TOOLS_DEBUG=true to enable)")
    
    print("\nBasic usage:")
    print("  from vision_tools import vision_analyze_tool")
    print("  import asyncio")
    print("")
    print("  async def main():")
    print("      result = await vision_analyze_tool(")
    print("          image_url='https://example.com/image.jpg',")
    print("          user_prompt='What do you see in this image?'")
    print("      )")
    print("      print(result)")
    print("  asyncio.run(main())")
    
    print("\nExample prompts:")
    print("  - 'What architectural style is this building?'")
    print("  - 'Describe the emotions and mood in this image'")
    print("  - 'What text can you read in this image?'")
    print("  - 'Identify any safety hazards visible'")
    print("  - 'What products or brands are shown?'")
    
    print("\nDebug mode:")
    print("  # Enable debug logging")
    print("  export VISION_TOOLS_DEBUG=true")
    print("  # Debug logs capture all vision analysis calls and results")
    print("  # Logs saved to: ./logs/vision_tools_debug_UUID.json")


# ---------------------------------------------------------------------------
# 注册
# ---------------------------------------------------------------------------
from tools.registry import registry, tool_error

VISION_ANALYZE_SCHEMA = {
    "name": "vision_analyze",
    "description": "Analyze images using AI vision. Provides a comprehensive description and answers a specific question about the image content.",
    "parameters": {
        "type": "object",
        "properties": {
            "image_url": {
                "type": "string",
                "description": "Image URL (http/https) or local file path to analyze."
            },
            "question": {
                "type": "string",
                "description": "Your specific question or request about the image to resolve. The AI will automatically provide a complete image description AND answer your specific question."
            }
        },
        "required": ["image_url", "question"]
    }
}


def _handle_vision_analyze(args: Dict[str, Any], **kw: Any) -> Awaitable[str]:
    image_url = args.get("image_url", "")
    question = args.get("question", "")
    full_prompt = (
        "Fully describe and explain everything about this image, then answer the "
        f"following question:\n\n{question}"
    )
    model = os.getenv("AUXILIARY_VISION_MODEL", "").strip() or None
    return vision_analyze_tool(image_url, full_prompt, model)


registry.register(
    name="vision_analyze",
    toolset="vision",
    schema=VISION_ANALYZE_SCHEMA,
    handler=_handle_vision_analyze,
    check_fn=check_vision_requirements,
    is_async=True,
    emoji="👁️",
)
