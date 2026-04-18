#!/usr/bin/env python3
"""
文本转语音工具模块

支持七种 TTS 提供商：
- Edge TTS（默认，免费，无需 API 密钥）：Microsoft Edge 神经语音
- ElevenLabs（高级版）：高品质语音，需要 ELEVENLABS_API_KEY
- OpenAI TTS：良好品质，需要 OPENAI_API_KEY
- MiniMax TTS：高品质带语音克隆，需要 MINIMAX_API_KEY
- Mistral（Voxtral TTS）：多语言，原生 Opus，需要 MISTRAL_API_KEY
- Google Gemini TTS：可控性强，30 种预置语音，需要 GEMINI_API_KEY
- NeuTTS（本地，免费，无需 API 密钥）：通过 neutts_cli 的设备端 TTS，需要安装 neutts

输出格式：
- Opus（.ogg）用于 Telegram 语音气泡（Edge TTS 需要 ffmpeg）
- MP3（.mp3）用于其他场景（CLI、Discord、WhatsApp）

配置从 ~/.hermes/config.yaml 的 'tts:' 键下加载。
用户选择提供商和声音；模型只需发送文本。

用法：
    from tools.tts_tool import text_to_speech_tool, check_tts_requirements

    result = text_to_speech_tool(text="Hello world")
"""

import asyncio
import base64
import datetime
import json
import logging
import os
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Callable, Dict, Any, Optional
from urllib.parse import urljoin

from hermes_constants import display_hermes_home

logger = logging.getLogger(__name__)
from tools.managed_tool_gateway import resolve_managed_tool_gateway
from tools.tool_backend_helpers import managed_nous_tools_enabled, prefers_gateway, resolve_openai_audio_api_key
from tools.xai_http import hermes_xai_user_agent

# ---------------------------------------------------------------------------
# 延迟导入 -- 仅在实际使用时导入提供商，以避免
# 在无头环境中崩溃（SSH、Docker、WSL、无 PortAudio）。
# ---------------------------------------------------------------------------

def _import_edge_tts():
    """延迟导入 edge_tts。返回模块或抛出 ImportError。"""
    import edge_tts
    return edge_tts

def _import_elevenlabs():
    """延迟导入 ElevenLabs 客户端。返回类或抛出 ImportError。"""
    from elevenlabs.client import ElevenLabs
    return ElevenLabs

def _import_openai_client():
    """延迟导入 OpenAI 客户端。返回类或抛出 ImportError。"""
    from openai import OpenAI as OpenAIClient
    return OpenAIClient

def _import_mistral_client():
    """延迟导入 Mistral 客户端。返回类或抛出 ImportError。"""
    from mistralai.client import Mistral
    return Mistral

def _import_sounddevice():
    """延迟导入 sounddevice。返回模块或抛出 ImportError/OSError。"""
    import sounddevice as sd
    return sd


# ===========================================================================
# 默认值
# ===========================================================================
DEFAULT_PROVIDER = "edge"
DEFAULT_EDGE_VOICE = "en-US-AriaNeural"
DEFAULT_ELEVENLABS_VOICE_ID = "pNInz6obpgDQGcFmaJgB"  # Adam
DEFAULT_ELEVENLABS_MODEL_ID = "eleven_multilingual_v2"
DEFAULT_ELEVENLABS_STREAMING_MODEL_ID = "eleven_flash_v2_5"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini-tts"
DEFAULT_OPENAI_VOICE = "alloy"
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MINIMAX_MODEL = "speech-2.8-hd"
DEFAULT_MINIMAX_VOICE_ID = "English_Graceful_Lady"
DEFAULT_MINIMAX_BASE_URL = "https://api.minimax.io/v1/t2a_v2"
DEFAULT_MISTRAL_TTS_MODEL = "voxtral-mini-tts-2603"
DEFAULT_MISTRAL_TTS_VOICE_ID = "c69964a6-ab8b-4f8a-9465-ec0925096ec8"  # Paul - Neutral
DEFAULT_XAI_VOICE_ID = "eve"
DEFAULT_XAI_LANGUAGE = "en"
DEFAULT_XAI_SAMPLE_RATE = 24000
DEFAULT_XAI_BIT_RATE = 128000
DEFAULT_XAI_BASE_URL = "https://api.x.ai/v1"
DEFAULT_GEMINI_TTS_MODEL = "gemini-2.5-flash-preview-tts"
DEFAULT_GEMINI_TTS_VOICE = "Kore"
DEFAULT_GEMINI_TTS_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
# Gemini TTS 的 PCM 输出规格（由 API 固定）
GEMINI_TTS_SAMPLE_RATE = 24000
GEMINI_TTS_CHANNELS = 1
GEMINI_TTS_SAMPLE_WIDTH = 2  # 16-bit PCM (L16)

def _get_default_output_dir() -> str:
    from hermes_constants import get_hermes_dir
    return str(get_hermes_dir("cache/audio", "audio_cache"))

DEFAULT_OUTPUT_DIR = _get_default_output_dir()
MAX_TEXT_LENGTH = 4000


# ===========================================================================
# 配置加载器 -- 从 ~/.hermes/config.yaml 读取 tts: 部分
# ===========================================================================
def _load_tts_config() -> Dict[str, Any]:
    """
    从 ~/.hermes/config.yaml 加载 TTS 配置。

    返回包含提供商设置的字典。缺少的字段回退到默认值。
    """
    try:
        from hermes_cli.config import load_config
        config = load_config()
        return config.get("tts", {})
    except ImportError:
        logger.debug("hermes_cli.config not available, using default TTS config")
        return {}
    except Exception as e:
        logger.warning("Failed to load TTS config: %s", e, exc_info=True)
        return {}


def _get_provider(tts_config: Dict[str, Any]) -> str:
    """获取已配置的 TTS 提供商名称。"""
    return (tts_config.get("provider") or DEFAULT_PROVIDER).lower().strip()


# ===========================================================================
# ffmpeg Opus conversion (Edge TTS MP3 -> OGG Opus for Telegram)
# ===========================================================================
def _has_ffmpeg() -> bool:
    """检查系统上是否有 ffmpeg 可用。"""
    return shutil.which("ffmpeg") is not None


def _convert_to_opus(mp3_path: str) -> Optional[str]:
    """
    Convert an MP3 file to OGG Opus format for Telegram voice bubbles.

    Args:
        mp3_path: Path to the input MP3 file.

    Returns:
        Path to the .ogg file, or None if conversion fails.
    """
    if not _has_ffmpeg():
        return None

    ogg_path = mp3_path.rsplit(".", 1)[0] + ".ogg"
    try:
        result = subprocess.run(
            ["ffmpeg", "-i", mp3_path, "-acodec", "libopus",
             "-ac", "1", "-b:a", "64k", "-vbr", "off", ogg_path, "-y"],
            capture_output=True, timeout=30,
        )
        if result.returncode != 0:
            logger.warning("ffmpeg conversion failed with return code %d: %s", 
                          result.returncode, result.stderr.decode('utf-8', errors='ignore')[:200])
            return None
        if os.path.exists(ogg_path) and os.path.getsize(ogg_path) > 0:
            return ogg_path
    except subprocess.TimeoutExpired:
        logger.warning("ffmpeg OGG conversion timed out after 30s")
    except FileNotFoundError:
        logger.warning("ffmpeg not found in PATH")
    except Exception as e:
        logger.warning("ffmpeg OGG conversion failed: %s", e, exc_info=True)
    return None


# ===========================================================================
# 提供商：Edge TTS（免费）
# ===========================================================================
async def _generate_edge_tts(text: str, output_path: str, tts_config: Dict[str, Any]) -> str:
    """
    Generate audio using Edge TTS.

    Args:
        text: Text to convert.
        output_path: Where to save the MP3 file.
        tts_config: TTS config dict.

    Returns:
        Path to the saved audio file.
    """
    _edge_tts = _import_edge_tts()
    edge_config = tts_config.get("edge", {})
    voice = edge_config.get("voice", DEFAULT_EDGE_VOICE)
    speed = float(edge_config.get("speed", tts_config.get("speed", 1.0)))

    kwargs = {"voice": voice}
    if speed != 1.0:
        pct = round((speed - 1.0) * 100)
        kwargs["rate"] = f"{pct:+d}%"

    communicate = _edge_tts.Communicate(text, **kwargs)
    await communicate.save(output_path)
    return output_path


# ===========================================================================
# 提供商：ElevenLabs（高级）
# ===========================================================================
def _generate_elevenlabs(text: str, output_path: str, tts_config: Dict[str, Any]) -> str:
    """
    Generate audio using ElevenLabs.

    Args:
        text: Text to convert.
        output_path: Where to save the audio file.
        tts_config: TTS config dict.

    Returns:
        Path to the saved audio file.
    """
    api_key = os.getenv("ELEVENLABS_API_KEY", "")
    if not api_key:
        raise ValueError("ELEVENLABS_API_KEY not set. Get one at https://elevenlabs.io/")

    el_config = tts_config.get("elevenlabs", {})
    voice_id = el_config.get("voice_id", DEFAULT_ELEVENLABS_VOICE_ID)
    model_id = el_config.get("model_id", DEFAULT_ELEVENLABS_MODEL_ID)

    # 根据文件扩展名确定输出格式
    if output_path.endswith(".ogg"):
        output_format = "opus_48000_64"
    else:
        output_format = "mp3_44100_128"

    ElevenLabs = _import_elevenlabs()
    client = ElevenLabs(api_key=api_key)
    audio_generator = client.text_to_speech.convert(
        text=text,
        voice_id=voice_id,
        model_id=model_id,
        output_format=output_format,
    )

    # audio_generator yields chunks -- write them all
    with open(output_path, "wb") as f:
        for chunk in audio_generator:
            f.write(chunk)

    return output_path


# ===========================================================================
# 提供商：OpenAI TTS
# ===========================================================================
def _generate_openai_tts(text: str, output_path: str, tts_config: Dict[str, Any]) -> str:
    """
    Generate audio using OpenAI TTS.

    Args:
        text: Text to convert.
        output_path: Where to save the audio file.
        tts_config: TTS config dict.

    Returns:
        Path to the saved audio file.
    """
    api_key, base_url = _resolve_openai_audio_client_config()

    oai_config = tts_config.get("openai", {})
    model = oai_config.get("model", DEFAULT_OPENAI_MODEL)
    voice = oai_config.get("voice", DEFAULT_OPENAI_VOICE)
    base_url = oai_config.get("base_url", base_url)
    speed = float(oai_config.get("speed", tts_config.get("speed", 1.0)))

    # 从扩展名确定响应格式
    if output_path.endswith(".ogg"):
        response_format = "opus"
    else:
        response_format = "mp3"

    OpenAIClient = _import_openai_client()
    client = OpenAIClient(api_key=api_key, base_url=base_url)
    try:
        create_kwargs = dict(
            model=model,
            voice=voice,
            input=text,
            response_format=response_format,
            extra_headers={"x-idempotency-key": str(uuid.uuid4())},
        )
        if speed != 1.0:
            create_kwargs["speed"] = max(0.25, min(4.0, speed))
        response = client.audio.speech.create(**create_kwargs)

        response.stream_to_file(output_path)
        return output_path
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()


# ===========================================================================
# 提供商：xAI TTS
# ===========================================================================
def _generate_xai_tts(text: str, output_path: str, tts_config: Dict[str, Any]) -> str:
    """
    Generate audio using xAI TTS.

    xAI exposes a dedicated /v1/tts endpoint instead of the OpenAI audio.speech
    API shape, so this is implemented as a separate backend.
    """
    import requests

    api_key = os.getenv("XAI_API_KEY", "").strip()
    if not api_key:
        raise ValueError("XAI_API_KEY not set. Get one at https://console.x.ai/")

    xai_config = tts_config.get("xai", {})
    voice_id = str(xai_config.get("voice_id", DEFAULT_XAI_VOICE_ID)).strip() or DEFAULT_XAI_VOICE_ID
    language = str(xai_config.get("language", DEFAULT_XAI_LANGUAGE)).strip() or DEFAULT_XAI_LANGUAGE
    sample_rate = int(xai_config.get("sample_rate", DEFAULT_XAI_SAMPLE_RATE))
    bit_rate = int(xai_config.get("bit_rate", DEFAULT_XAI_BIT_RATE))
    base_url = str(
        xai_config.get("base_url")
        or os.getenv("XAI_BASE_URL")
        or DEFAULT_XAI_BASE_URL
    ).strip().rstrip("/")

    # 默认匹配文档中最小的 POST /v1/tts 格式。仅在
    # output_format when Hermes actually needs a non-default format/override.
    codec = "wav" if output_path.endswith(".wav") else "mp3"
    payload: Dict[str, Any] = {
        "text": text,
        "voice_id": voice_id,
        "language": language,
    }
    if (
        codec != "mp3"
        or sample_rate != DEFAULT_XAI_SAMPLE_RATE
        or (codec == "mp3" and bit_rate != DEFAULT_XAI_BIT_RATE)
    ):
        output_format: Dict[str, Any] = {"codec": codec}
        if sample_rate:
            output_format["sample_rate"] = sample_rate
        if codec == "mp3" and bit_rate:
            output_format["bit_rate"] = bit_rate
        payload["output_format"] = output_format

    response = requests.post(
        f"{base_url}/tts",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": hermes_xai_user_agent(),
        },
        json=payload,
        timeout=60,
    )
    response.raise_for_status()

    with open(output_path, "wb") as f:
        f.write(response.content)

    return output_path


# ===========================================================================
# 提供商：MiniMax TTS
# ===========================================================================
def _generate_minimax_tts(text: str, output_path: str, tts_config: Dict[str, Any]) -> str:
    """
    Generate audio using MiniMax TTS API.

    MiniMax returns hex-encoded audio data. Supports streaming (SSE) and
    non-streaming modes. This implementation uses non-streaming for simplicity.

    Args:
        text: Text to convert (max 10,000 characters).
        output_path: Where to save the audio file.
        tts_config: TTS config dict.

    Returns:
        Path to the saved audio file.
    """
    import requests

    api_key = os.getenv("MINIMAX_API_KEY", "")
    if not api_key:
        raise ValueError("MINIMAX_API_KEY not set. Get one at https://platform.minimax.io/")

    mm_config = tts_config.get("minimax", {})
    model = mm_config.get("model", DEFAULT_MINIMAX_MODEL)
    voice_id = mm_config.get("voice_id", DEFAULT_MINIMAX_VOICE_ID)
    speed = mm_config.get("speed", tts_config.get("speed", 1))
    vol = mm_config.get("vol", 1)
    pitch = mm_config.get("pitch", 0)
    base_url = mm_config.get("base_url", DEFAULT_MINIMAX_BASE_URL)

    # 从输出扩展名确定音频格式
    if output_path.endswith(".wav"):
        audio_format = "wav"
    elif output_path.endswith(".flac"):
        audio_format = "flac"
    else:
        audio_format = "mp3"

    payload = {
        "model": model,
        "text": text,
        "stream": False,
        "voice_setting": {
            "voice_id": voice_id,
            "speed": speed,
            "vol": vol,
            "pitch": pitch,
        },
        "audio_setting": {
            "sample_rate": 32000,
            "bitrate": 128000,
            "format": audio_format,
            "channel": 1,
        },
    }

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    response = requests.post(base_url, json=payload, headers=headers, timeout=60)
    response.raise_for_status()

    result = response.json()
    base_resp = result.get("base_resp", {})
    status_code = base_resp.get("status_code", -1)

    if status_code != 0:
        status_msg = base_resp.get("status_msg", "unknown error")
        raise RuntimeError(f"MiniMax TTS API error (code {status_code}): {status_msg}")

    hex_audio = result.get("data", {}).get("audio", "")
    if not hex_audio:
        raise RuntimeError("MiniMax TTS returned empty audio data")

    # MiniMax 返回十六进制编码的音频（不是 base64）
    audio_bytes = bytes.fromhex(hex_audio)

    with open(output_path, "wb") as f:
        f.write(audio_bytes)

    return output_path


# ===========================================================================
# 提供商：Mistral (Voxtral TTS)
# ===========================================================================
def _generate_mistral_tts(text: str, output_path: str, tts_config: Dict[str, Any]) -> str:
    """使用 Mistral Voxtral TTS API 生成音频。

    The API returns base64-encoded audio; this function decodes it
    and writes the raw bytes to *output_path*.
    Supports native Opus output for Telegram voice bubbles.
    """
    api_key = os.getenv("MISTRAL_API_KEY", "")
    if not api_key:
        raise ValueError("MISTRAL_API_KEY not set. Get one at https://console.mistral.ai/")

    mi_config = tts_config.get("mistral", {})
    model = mi_config.get("model", DEFAULT_MISTRAL_TTS_MODEL)
    voice_id = mi_config.get("voice_id") or DEFAULT_MISTRAL_TTS_VOICE_ID

    if output_path.endswith(".ogg"):
        response_format = "opus"
    elif output_path.endswith(".wav"):
        response_format = "wav"
    elif output_path.endswith(".flac"):
        response_format = "flac"
    else:
        response_format = "mp3"

    Mistral = _import_mistral_client()
    try:
        with Mistral(api_key=api_key) as client:
            response = client.audio.speech.complete(
                model=model,
                input=text,
                voice_id=voice_id,
                response_format=response_format,
            )
            audio_bytes = base64.b64decode(response.audio_data)
    except ValueError:
        raise
    except Exception as e:
        logger.error("Mistral TTS failed: %s", e, exc_info=True)
        raise RuntimeError(f"Mistral TTS failed: {type(e).__name__}") from e

    with open(output_path, "wb") as f:
        f.write(audio_bytes)

    return output_path


# ===========================================================================
# 提供商：Google Gemini TTS
# ===========================================================================
def _wrap_pcm_as_wav(
    pcm_bytes: bytes,
    sample_rate: int = GEMINI_TTS_SAMPLE_RATE,
    channels: int = GEMINI_TTS_CHANNELS,
    sample_width: int = GEMINI_TTS_SAMPLE_WIDTH,
) -> bytes:
    """将原始有符号小端序 PCM 封装为标准 WAV RIFF 头。

    Gemini TTS returns audio/L16;codec=pcm;rate=24000 -- raw PCM samples with
    no container. We add a minimal WAV header so the file is playable and
    ffmpeg can re-encode it to MP3/Opus downstream.
    """
    import struct

    byte_rate = sample_rate * channels * sample_width
    block_align = channels * sample_width
    data_size = len(pcm_bytes)
    fmt_chunk = struct.pack(
        "<4sIHHIIHH",
        b"fmt ",
        16,             # fmt chunk size (PCM)
        1,              # audio format (PCM)
        channels,
        sample_rate,
        byte_rate,
        block_align,
        sample_width * 8,
    )
    data_chunk_header = struct.pack("<4sI", b"data", data_size)
    riff_size = 4 + len(fmt_chunk) + len(data_chunk_header) + data_size
    riff_header = struct.pack("<4sI4s", b"RIFF", riff_size, b"WAVE")
    return riff_header + fmt_chunk + data_chunk_header + pcm_bytes


def _generate_gemini_tts(text: str, output_path: str, tts_config: Dict[str, Any]) -> str:
    """使用 Google Gemini TTS 生成音频。

    Gemini's generateContent endpoint with responseModalities=["AUDIO"] returns
    raw 24kHz mono 16-bit PCM (L16) as base64. We wrap it with a WAV RIFF
    header to produce a playable file, then ffmpeg-convert to MP3 / Opus if
    the caller requested those formats (same pattern as NeuTTS).

    Args:
        text: Text to convert (prompt-style; supports inline direction like
              "Say cheerfully:" and audio tags like [whispers]).
        output_path: Where to save the audio file (.wav, .mp3, or .ogg).
        tts_config: TTS config dict.

    Returns:
        Path to the saved audio file.
    """
    import requests

    api_key = (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip()
    if not api_key:
        raise ValueError(
            "GEMINI_API_KEY not set. Get one at https://aistudio.google.com/app/apikey"
        )

    gemini_config = tts_config.get("gemini", {})
    model = str(gemini_config.get("model", DEFAULT_GEMINI_TTS_MODEL)).strip() or DEFAULT_GEMINI_TTS_MODEL
    voice = str(gemini_config.get("voice", DEFAULT_GEMINI_TTS_VOICE)).strip() or DEFAULT_GEMINI_TTS_VOICE
    base_url = str(
        gemini_config.get("base_url")
        or os.getenv("GEMINI_BASE_URL")
        or DEFAULT_GEMINI_TTS_BASE_URL
    ).strip().rstrip("/")

    payload: Dict[str, Any] = {
        "contents": [{"parts": [{"text": text}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {
                    "prebuiltVoiceConfig": {"voiceName": voice},
                },
            },
        },
    }

    endpoint = f"{base_url}/models/{model}:generateContent"
    response = requests.post(
        endpoint,
        params={"key": api_key},
        headers={"Content-Type": "application/json"},
        json=payload,
        timeout=60,
    )
    if response.status_code != 200:
        # 当 API 返回错误消息时展示出来
        try:
            err = response.json().get("error", {})
            detail = err.get("message") or response.text[:300]
        except Exception:
            detail = response.text[:300]
        raise RuntimeError(
            f"Gemini TTS API error (HTTP {response.status_code}): {detail}"
        )

    try:
        data = response.json()
        parts = data["candidates"][0]["content"]["parts"]
        audio_part = next((p for p in parts if "inlineData" in p or "inline_data" in p), None)
        if audio_part is None:
            raise RuntimeError("Gemini TTS response contained no audio data")
        inline = audio_part.get("inlineData") or audio_part.get("inline_data") or {}
        audio_b64 = inline.get("data", "")
    except (KeyError, IndexError, TypeError) as e:
        raise RuntimeError(f"Gemini TTS response was malformed: {e}") from e

    if not audio_b64:
        raise RuntimeError("Gemini TTS returned empty audio data")

    pcm_bytes = base64.b64decode(audio_b64)
    wav_bytes = _wrap_pcm_as_wav(pcm_bytes)

    # 快速路径：调用者要求直接输出 WAV，直接写入。
    if output_path.lower().endswith(".wav"):
        with open(output_path, "wb") as f:
            f.write(wav_bytes)
        return output_path

    # 否则将 WAV 写入临时文件，然后用 ffmpeg 转换为目标格式
    # format (.mp3 or .ogg). If ffmpeg is missing, fall back to renaming the
    # WAV -- this matches the NeuTTS behavior and keeps the tool usable on
    # systems without ffmpeg (audio still plays, just with a misleading
    # extension).
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp.write(wav_bytes)
        wav_path = tmp.name

    try:
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg:
            # 对于 .ogg 输出，强制使用 libopus 编码（Telegram 语音气泡
            # require Opus specifically; ffmpeg's default for .ogg is Vorbis).
            if output_path.lower().endswith(".ogg"):
                cmd = [
                    ffmpeg, "-i", wav_path,
                    "-acodec", "libopus", "-ac", "1",
                    "-b:a", "64k", "-vbr", "off",
                    "-y", "-loglevel", "error",
                    output_path,
                ]
            else:
                cmd = [ffmpeg, "-i", wav_path, "-y", "-loglevel", "error", output_path]
            result = subprocess.run(cmd, capture_output=True, timeout=30)
            if result.returncode != 0:
                stderr = result.stderr.decode("utf-8", errors="ignore")[:300]
                raise RuntimeError(f"ffmpeg conversion failed: {stderr}")
        else:
            logger.warning(
                "ffmpeg not found; writing raw WAV to %s (extension may be misleading)",
                output_path,
            )
            shutil.copyfile(wav_path, output_path)
    finally:
        try:
            os.remove(wav_path)
        except OSError:
            pass

    return output_path


# ===========================================================================
# NeuTTS（本地设备端 TTS，通过 neutts_cli）
# ===========================================================================

def _check_neutts_available() -> bool:
    """检查 neutts 引擎是否可导入（本地安装）。"""
    try:
        import importlib.util
        return importlib.util.find_spec("neutts") is not None
    except Exception:
        return False


def _default_neutts_ref_audio() -> str:
    """返回内置默认语音参考音频的路径。"""
    return str(Path(__file__).parent / "neutts_samples" / "jo.wav")


def _default_neutts_ref_text() -> str:
    """返回内置默认语音参考文本的路径。"""
    return str(Path(__file__).parent / "neutts_samples" / "jo.txt")


def _generate_neutts(text: str, output_path: str, tts_config: Dict[str, Any]) -> str:
    """使用本地 NeuTTS 引擎生成语音。

    Runs synthesis in a subprocess via tools/neutts_synth.py to keep the
    ~500MB model in a separate process that exits after synthesis.
    Outputs WAV; the caller handles conversion for Telegram if needed.
    """
    import sys

    neutts_config = tts_config.get("neutts", {})
    ref_audio = neutts_config.get("ref_audio", "") or _default_neutts_ref_audio()
    ref_text = neutts_config.get("ref_text", "") or _default_neutts_ref_text()
    model = neutts_config.get("model", "neuphonic/neutts-air-q4-gguf")
    device = neutts_config.get("device", "cpu")

    # NeuTTS 原生输出 WAV -- 使用 .wav 路径生成，
    # let the caller convert to the final format afterward.
    wav_path = output_path
    if not output_path.endswith(".wav"):
        wav_path = output_path.rsplit(".", 1)[0] + ".wav"

    synth_script = str(Path(__file__).parent / "neutts_synth.py")
    cmd = [
        sys.executable, synth_script,
        "--text", text,
        "--out", wav_path,
        "--ref-audio", ref_audio,
        "--ref-text", ref_text,
        "--model", model,
        "--device", device,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        stderr = result.stderr.strip()
        # 过滤掉 stderr 中的 "OK:" 行
        error_lines = [l for l in stderr.splitlines() if not l.startswith("OK:")]
        raise RuntimeError(f"NeuTTS synthesis failed: {chr(10).join(error_lines) or 'unknown error'}")

    # 如果调用者要求 .mp3 或 .ogg，则从 WAV 转换
    if wav_path != output_path:
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg:
            conv_cmd = [ffmpeg, "-i", wav_path, "-y", "-loglevel", "error", output_path]
            subprocess.run(conv_cmd, check=True, timeout=30)
            os.remove(wav_path)
        else:
            # 没有 ffmpeg -- 只将 WAV 重命名为预期路径
            os.rename(wav_path, output_path)

    return output_path


# ===========================================================================
# 主要工具函数
# ===========================================================================
def text_to_speech_tool(
    text: str,
    output_path: Optional[str] = None,
) -> str:
    """
    Convert text to speech audio.

    Reads provider/voice config from ~/.hermes/config.yaml (tts: section).
    The model sends text; the user configures voice and provider.

    On messaging platforms, the returned MEDIA:<path> tag is intercepted
    by the send pipeline and delivered as a native voice message.
    In CLI mode, the file is saved to ~/voice-memos/.

    Args:
        text: The text to convert to speech.
        output_path: Optional custom save path. Defaults to ~/voice-memos/<timestamp>.mp3

    Returns:
        str: JSON result with success, file_path, and optionally MEDIA tag.
    """
    if not text or not text.strip():
        return tool_error("Text is required", success=False)

    # 截断过长的文本并附带警告
    if len(text) > MAX_TEXT_LENGTH:
        logger.warning("TTS text too long (%d chars), truncating to %d", len(text), MAX_TEXT_LENGTH)
        text = text[:MAX_TEXT_LENGTH]

    tts_config = _load_tts_config()
    provider = _get_provider(tts_config)

    # 从网关环境变量检测平台，以选择最佳输出格式。
    # Telegram 语音气泡需要 Opus (.ogg)；OpenAI 和 ElevenLabs 可以
    # produce Opus natively (no ffmpeg needed).  Edge TTS always outputs MP3
    # and needs ffmpeg for conversion.
    from gateway.session_context import get_session_env
    platform = get_session_env("HERMES_SESSION_PLATFORM", "").lower()
    want_opus = (platform == "telegram")

    # 确定输出路径
    if output_path:
        file_path = Path(output_path).expanduser()
    else:
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir = Path(DEFAULT_OUTPUT_DIR)
        out_dir.mkdir(parents=True, exist_ok=True)
        # 对于 Telegram，使用 .ogg 格式（适用于支持原生 Opus 输出的提供商），
        # otherwise fall back to .mp3 (Edge TTS will attempt ffmpeg conversion later).
        if want_opus and provider in ("openai", "elevenlabs", "mistral", "gemini"):
            file_path = out_dir / f"tts_{timestamp}.ogg"
        else:
            file_path = out_dir / f"tts_{timestamp}.mp3"

    # 确保父目录存在
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_str = str(file_path)

    try:
        # 使用配置的提供商生成音频
        if provider == "elevenlabs":
            try:
                _import_elevenlabs()
            except ImportError:
                return json.dumps({
                    "success": False,
                    "error": "ElevenLabs provider selected but 'elevenlabs' package not installed. Run: pip install elevenlabs"
                }, ensure_ascii=False)
            logger.info("Generating speech with ElevenLabs...")
            _generate_elevenlabs(text, file_str, tts_config)

        elif provider == "openai":
            try:
                _import_openai_client()
            except ImportError:
                return json.dumps({
                    "success": False,
                    "error": "OpenAI provider selected but 'openai' package not installed."
                }, ensure_ascii=False)
            logger.info("Generating speech with OpenAI TTS...")
            _generate_openai_tts(text, file_str, tts_config)

        elif provider == "minimax":
            logger.info("Generating speech with MiniMax TTS...")
            _generate_minimax_tts(text, file_str, tts_config)

        elif provider == "xai":
            logger.info("Generating speech with xAI TTS...")
            _generate_xai_tts(text, file_str, tts_config)

        elif provider == "mistral":
            try:
                _import_mistral_client()
            except ImportError:
                return json.dumps({
                    "success": False,
                    "error": "Mistral provider selected but 'mistralai' package not installed. "
                             "Run: pip install 'hermes-agent[mistral]'"
                }, ensure_ascii=False)
            logger.info("Generating speech with Mistral Voxtral TTS...")
            _generate_mistral_tts(text, file_str, tts_config)

        elif provider == "gemini":
            logger.info("Generating speech with Google Gemini TTS...")
            _generate_gemini_tts(text, file_str, tts_config)

        elif provider == "neutts":
            if not _check_neutts_available():
                return json.dumps({
                    "success": False,
                    "error": "NeuTTS provider selected but neutts is not installed. "
                             "Run hermes setup and choose NeuTTS, or install espeak-ng and run python -m pip install -U neutts[all]."
                }, ensure_ascii=False)
            logger.info("Generating speech with NeuTTS (local)...")
            _generate_neutts(text, file_str, tts_config)

        else:
            # 默认：Edge TTS（免费），NeuTTS 作为本地回退
            edge_available = True
            try:
                _import_edge_tts()
            except ImportError:
                edge_available = False

            if edge_available:
                logger.info("Generating speech with Edge TTS...")
                try:
                    import concurrent.futures
                    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                        pool.submit(
                            lambda: asyncio.run(_generate_edge_tts(text, file_str, tts_config))
                        ).result(timeout=60)
                except RuntimeError:
                    asyncio.run(_generate_edge_tts(text, file_str, tts_config))
            elif _check_neutts_available():
                logger.info("Edge TTS not available, falling back to NeuTTS (local)...")
                provider = "neutts"
                _generate_neutts(text, file_str, tts_config)
            else:
                return json.dumps({
                    "success": False,
                    "error": "No TTS provider available. Install edge-tts (pip install edge-tts) "
                             "or set up NeuTTS for local synthesis."
                }, ensure_ascii=False)

        # 检查文件是否实际已创建
        if not os.path.exists(file_str) or os.path.getsize(file_str) == 0:
            return json.dumps({
                "success": False,
                "error": f"TTS generation produced no output (provider: {provider})"
            }, ensure_ascii=False)

        # 尝试 Opus 转换以兼容 Telegram
        # Edge TTS 输出 MP3，NeuTTS 输出 WAV -- 都需要 ffmpeg 转换
        voice_compatible = False
        if provider in ("edge", "neutts", "minimax", "xai") and not file_str.endswith(".ogg"):
            opus_path = _convert_to_opus(file_str)
            if opus_path:
                file_str = opus_path
                voice_compatible = True
        elif provider in ("elevenlabs", "openai", "mistral", "gemini"):
            voice_compatible = file_str.endswith(".ogg")

        file_size = os.path.getsize(file_str)
        logger.info("TTS audio saved: %s (%s bytes, provider: %s)", file_str, f"{file_size:,}", provider)

        # 构建带有 MEDIA 标签的响应以便平台投递
        media_tag = f"MEDIA:{file_str}"
        if voice_compatible:
            media_tag = f"[[audio_as_voice]]\n{media_tag}"

        return json.dumps({
            "success": True,
            "file_path": file_str,
            "media_tag": media_tag,
            "provider": provider,
            "voice_compatible": voice_compatible,
        }, ensure_ascii=False)

    except ValueError as e:
        # 配置错误（缺少 API 密钥等）
        error_msg = f"TTS configuration error ({provider}): {e}"
        logger.error("%s", error_msg)
        return tool_error(error_msg, success=False)
    except FileNotFoundError as e:
        # 缺少依赖或文件
        error_msg = f"TTS dependency missing ({provider}): {e}"
        logger.error("%s", error_msg, exc_info=True)
        return tool_error(error_msg, success=False)
    except Exception as e:
        # 意外错误
        error_msg = f"TTS generation failed ({provider}): {e}"
        logger.error("%s", error_msg, exc_info=True)
        return tool_error(error_msg, success=False)


# ===========================================================================
# 依赖检查
# ===========================================================================
def check_tts_requirements() -> bool:
    """
    Check if at least one TTS provider is available.

    Edge TTS needs no API key and is the default, so if the package
    is installed, TTS is available.

    Returns:
        bool: True if at least one provider can work.
    """
    try:
        _import_edge_tts()
        return True
    except ImportError:
        pass
    try:
        _import_elevenlabs()
        if os.getenv("ELEVENLABS_API_KEY"):
            return True
    except ImportError:
        pass
    try:
        _import_openai_client()
        if _has_openai_audio_backend():
            return True
    except ImportError:
        pass
    if os.getenv("MINIMAX_API_KEY"):
        return True
    if os.getenv("XAI_API_KEY"):
        return True
    if os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"):
        return True
    try:
        _import_mistral_client()
        if os.getenv("MISTRAL_API_KEY"):
            return True
    except ImportError:
        pass
    if _check_neutts_available():
        return True
    return False


def _resolve_openai_audio_client_config() -> tuple[str, str]:
    """返回直接 OpenAI 音频配置或托管网关回退。

    When ``tts.use_gateway`` is set in config, the Tool Gateway is preferred
    even if direct OpenAI credentials are present.
    """
    direct_api_key = resolve_openai_audio_api_key()
    if direct_api_key and not prefers_gateway("tts"):
        return direct_api_key, DEFAULT_OPENAI_BASE_URL

    managed_gateway = resolve_managed_tool_gateway("openai-audio")
    if managed_gateway is None:
        message = "Neither VOICE_TOOLS_OPENAI_KEY nor OPENAI_API_KEY is set"
        if managed_nous_tools_enabled():
            message += ", and the managed OpenAI audio gateway is unavailable"
        raise ValueError(message)

    return managed_gateway.nous_user_token, urljoin(
        f"{managed_gateway.gateway_origin.rstrip('/')}/", "v1"
    )


def _has_openai_audio_backend() -> bool:
    """当 OpenAI 音频可以使用直接凭证或托管网关时返回 True。"""
    return bool(resolve_openai_audio_api_key() or resolve_managed_tool_gateway("openai-audio"))


# ===========================================================================
# Streaming TTS: sentence-by-sentence pipeline for ElevenLabs
# ===========================================================================
# Sentence boundary pattern: punctuation followed by space or newline
_SENTENCE_BOUNDARY_RE = re.compile(r'(?<=[.!?])(?:\s|\n)|(?:\n\n)')

# Markdown 去除模式（与 cli.py _voice_speak_response 相同）
_MD_CODE_BLOCK = re.compile(r'```[\s\S]*?```')
_MD_LINK = re.compile(r'\[([^\]]+)\]\([^)]+\)')
_MD_URL = re.compile(r'https?://\S+')
_MD_BOLD = re.compile(r'\*\*(.+?)\*\*')
_MD_ITALIC = re.compile(r'\*(.+?)\*')
_MD_INLINE_CODE = re.compile(r'`(.+?)`')
_MD_HEADER = re.compile(r'^#+\s*', flags=re.MULTILINE)
_MD_LIST_ITEM = re.compile(r'^\s*[-*]\s+', flags=re.MULTILINE)
_MD_HR = re.compile(r'---+')
_MD_EXCESS_NL = re.compile(r'\n{3,}')


def _strip_markdown_for_tts(text: str) -> str:
    """去除不应朗读的 markdown 格式。"""
    text = _MD_CODE_BLOCK.sub(' ', text)
    text = _MD_LINK.sub(r'\1', text)
    text = _MD_URL.sub('', text)
    text = _MD_BOLD.sub(r'\1', text)
    text = _MD_ITALIC.sub(r'\1', text)
    text = _MD_INLINE_CODE.sub(r'\1', text)
    text = _MD_HEADER.sub('', text)
    text = _MD_LIST_ITEM.sub('', text)
    text = _MD_HR.sub('', text)
    text = _MD_EXCESS_NL.sub('\n\n', text)
    return text.strip()


def stream_tts_to_speaker(
    text_queue: queue.Queue,
    stop_event: threading.Event,
    tts_done_event: threading.Event,
    display_callback: Optional[Callable[[str], None]] = None,
):
    """从 *text_queue* 消费文本增量，将它们缓冲为句子，
    and stream each sentence through ElevenLabs TTS to the speaker in
    real-time.

    Protocol:
        * The producer puts ``str`` deltas onto *text_queue*.
        * A ``None`` sentinel signals end-of-text (flush remaining buffer).
        * *stop_event* can be set to abort early (e.g. user interrupt).
        * *tts_done_event* is **set** in the ``finally`` block so callers
          waiting on it (continuous voice mode) know playback is finished.
    """
    tts_done_event.clear()

    try:
        # --- TTS client setup (optional -- display_callback works without it) ---
        client = None
        output_stream = None
        voice_id = DEFAULT_ELEVENLABS_VOICE_ID
        model_id = DEFAULT_ELEVENLABS_STREAMING_MODEL_ID

        tts_config = _load_tts_config()
        el_config = tts_config.get("elevenlabs", {})
        voice_id = el_config.get("voice_id", voice_id)
        model_id = el_config.get("streaming_model_id",
                                 el_config.get("model_id", model_id))

        api_key = os.getenv("ELEVENLABS_API_KEY", "")
        if not api_key:
            logger.warning("ELEVENLABS_API_KEY not set; streaming TTS audio disabled")
        else:
            try:
                ElevenLabs = _import_elevenlabs()
                client = ElevenLabs(api_key=api_key)
            except ImportError:
                logger.warning("elevenlabs package not installed; streaming TTS disabled")

            # 为整个生命周期打开一个 sounddevice 输出流
            # this function.  ElevenLabs pcm_24000 produces signed 16-bit
            # little-endian mono PCM at 24 kHz.
            if client is not None:
                try:
                    sd = _import_sounddevice()
                    output_stream = sd.OutputStream(
                        samplerate=24000, channels=1, dtype="int16",
                    )
                    output_stream.start()
                except (ImportError, OSError) as exc:
                    logger.debug("sounddevice not available: %s", exc)
                    output_stream = None
                except Exception as exc:
                    logger.warning("sounddevice OutputStream failed: %s", exc)
                    output_stream = None

        sentence_buf = ""
        min_sentence_len = 20
        long_flush_len = 100
        queue_timeout = 0.5
        _spoken_sentences: list[str] = []  # track spoken sentences to skip duplicates
        # 正则表达式：去除缓冲区中完整的 <think>...</think> 块
        _think_block_re = re.compile(r'<think[\s>].*?</think>', flags=re.DOTALL)

        def _speak_sentence(sentence: str):
            """Display sentence and optionally generate + play audio."""
            if stop_event.is_set():
                return
            cleaned = _strip_markdown_for_tts(sentence).strip()
            if not cleaned:
                return
            # 跳过重复/近似重复的句子（LLM 重复输出）
            cleaned_lower = cleaned.lower().rstrip(".!,")
            for prev in _spoken_sentences:
                if prev.lower().rstrip(".!,") == cleaned_lower:
                    return
            _spoken_sentences.append(cleaned)
            # TTS 处理前在屏幕上显示原始句子
            if display_callback is not None:
                display_callback(sentence)
            # 如果没有可用的 TTS 客户端则跳过音频生成
            if client is None:
                return
            # 截断过长的句子
            if len(cleaned) > MAX_TEXT_LENGTH:
                cleaned = cleaned[:MAX_TEXT_LENGTH]
            try:
                audio_iter = client.text_to_speech.convert(
                    text=cleaned,
                    voice_id=voice_id,
                    model_id=model_id,
                    output_format="pcm_24000",
                )
                if output_stream is not None:
                    for chunk in audio_iter:
                        if stop_event.is_set():
                            break
                        import numpy as _np
                        audio_array = _np.frombuffer(chunk, dtype=_np.int16)
                        output_stream.write(audio_array.reshape(-1, 1))
                else:
                    # 回退：将 PCM 块写入临时文件并通过系统播放器播放
                    _play_via_tempfile(audio_iter, stop_event)
            except Exception as exc:
                logger.warning("Streaming TTS sentence failed: %s", exc)

        def _play_via_tempfile(audio_iter, stop_evt):
            """Write PCM chunks to a temp WAV file and play it."""
            tmp_path = None
            try:
                import wave
                tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
                tmp_path = tmp.name
                with wave.open(tmp, "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)  # 16-bit
                    wf.setframerate(24000)
                    for chunk in audio_iter:
                        if stop_evt.is_set():
                            break
                        wf.writeframes(chunk)
                from tools.voice_mode import play_audio_file
                play_audio_file(tmp_path)
            except Exception as exc:
                logger.warning("Temp-file TTS fallback failed: %s", exc)
            finally:
                if tmp_path:
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass

        while not stop_event.is_set():
            # 从队列读取下一个增量
            try:
                delta = text_queue.get(timeout=queue_timeout)
            except queue.Empty:
                # 超时：如果累积了很长的缓冲区，刷新它
                if len(sentence_buf) > long_flush_len:
                    _speak_sentence(sentence_buf)
                    sentence_buf = ""
                continue

            if delta is None:
                # 文本结束哨兵：去除剩余的 think 块，刷新
                sentence_buf = _think_block_re.sub('', sentence_buf)
                if sentence_buf.strip():
                    _speak_sentence(sentence_buf)
                break

            sentence_buf += delta

            # --- Think block filtering ---
            # 去除缓冲区中完整的 <think>...</think> 块。
            # 即使标签跨多个增量也能正确工作。
            sentence_buf = _think_block_re.sub('', sentence_buf)

            # 如果末尾有不完整的 <think 标签，等待更多数据
            # before extracting sentences (the closing tag may arrive next).
            if '<think' in sentence_buf and '</think>' not in sentence_buf:
                continue

            # 检查句子边界
            while True:
                m = _SENTENCE_BOUNDARY_RE.search(sentence_buf)
                if m is None:
                    break
                end_pos = m.end()
                sentence = sentence_buf[:end_pos]
                sentence_buf = sentence_buf[end_pos:]
                # 将短片段合并到下一个句子
                if len(sentence.strip()) < min_sentence_len:
                    sentence_buf = sentence + sentence_buf
                    break
                _speak_sentence(sentence)

        # 清空队列中剩余的项
        while True:
            try:
                text_queue.get_nowait()
            except queue.Empty:
                break

        # output_stream is closed in the finally block below

    except Exception as exc:
        logger.warning("Streaming TTS pipeline error: %s", exc)
    finally:
        # 始终关闭音频输出流以避免锁定设备
        if output_stream is not None:
            try:
                output_stream.stop()
                output_stream.close()
            except Exception:
                pass
        tts_done_event.set()


# ===========================================================================
# 主函数 -- 快速诊断
# ===========================================================================
if __name__ == "__main__":
    print("🔊 Text-to-Speech Tool Module")
    print("=" * 50)

    def _check(importer, label):
        try:
            importer()
            return True
        except ImportError:
            return False

    print("\nProvider availability:")
    print(f"  Edge TTS:   {'installed' if _check(_import_edge_tts, 'edge') else 'not installed (pip install edge-tts)'}")
    print(f"  ElevenLabs: {'installed' if _check(_import_elevenlabs, 'el') else 'not installed (pip install elevenlabs)'}")
    print(f"    API Key:  {'set' if os.getenv('ELEVENLABS_API_KEY') else 'not set'}")
    print(f"  OpenAI:     {'installed' if _check(_import_openai_client, 'oai') else 'not installed'}")
    print(
        "    API Key:  "
        f"{'set' if resolve_openai_audio_api_key() else 'not set (VOICE_TOOLS_OPENAI_KEY or OPENAI_API_KEY)'}"
    )
    print(f"  MiniMax:    {'API key set' if os.getenv('MINIMAX_API_KEY') else 'not set (MINIMAX_API_KEY)'}")
    print(f"  ffmpeg:     {'✅ found' if _has_ffmpeg() else '❌ not found (needed for Telegram Opus)'}")
    print(f"\n  Output dir: {DEFAULT_OUTPUT_DIR}")

    config = _load_tts_config()
    provider = _get_provider(config)
    print(f"  Configured provider: {provider}")


# ---------------------------------------------------------------------------
# 注册
# ---------------------------------------------------------------------------
from tools.registry import registry, tool_error

TTS_SCHEMA = {
    "name": "text_to_speech",
    "description": "Convert text to speech audio. Returns a MEDIA: path that the platform delivers as a voice message. On Telegram it plays as a voice bubble, on Discord/WhatsApp as an audio attachment. In CLI mode, saves to ~/voice-memos/. Voice and provider are user-configured, not model-selected.",
    "parameters": {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "The text to convert to speech. Keep under 4000 characters."
            },
            "output_path": {
                "type": "string",
                "description": f"Optional custom file path to save the audio. Defaults to {display_hermes_home()}/audio_cache/<timestamp>.mp3"
            }
        },
        "required": ["text"]
    }
}

registry.register(
    name="text_to_speech",
    toolset="tts",
    schema=TTS_SCHEMA,
    handler=lambda args, **kw: text_to_speech_tool(
        text=args.get("text", ""),
        output_path=args.get("output_path")),
    check_fn=check_tts_requirements,
    emoji="🔊",
)
