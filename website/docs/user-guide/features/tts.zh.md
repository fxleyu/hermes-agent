---
sidebar_position: 9
title: "语音与 TTS"
description: "跨所有平台的文字转语音和语音消息转录"
---

# 语音与 TTS

Hermes Agent 支持跨所有消息平台的文字转语音输出和语音消息转录。

:::tip Nous 订阅用户
如果你拥有 [Nous Portal](https://portal.nousresearch.com) 付费订阅，可以通过**[工具网关](tool-gateway.md)**使用 OpenAI TTS，无需单独的 OpenAI API 密钥。运行 `hermes model` 或 `hermes tools` 来启用。
:::

## 文字转语音

通过七个提供商将文字转换为语音：

| 提供商 | 质量 | 费用 | API 密钥 |
|--------|------|------|----------|
| **Edge TTS**（默认） | 良好 | 免费 | 无需密钥 |
| **ElevenLabs** | 优秀 | 付费 | `ELEVENLABS_API_KEY` |
| **OpenAI TTS** | 良好 | 付费 | `VOICE_TOOLS_OPENAI_KEY` |
| **MiniMax TTS** | 优秀 | 付费 | `MINIMAX_API_KEY` |
| **Mistral (Voxtral TTS)** | 优秀 | 付费 | `MISTRAL_API_KEY` |
| **Google Gemini TTS** | 优秀 | 免费层级 | `GEMINI_API_KEY` |
| **NeuTTS** | 良好 | 免费 | 无需密钥 |

### 平台分发

| 平台 | 分发方式 | 格式 |
|------|---------|------|
| Telegram | 语音气泡（内联播放） | Opus `.ogg` |
| Discord | 语音气泡（Opus/OGG），失败时回退为文件附件 | Opus/MP3 |
| WhatsApp | 音频文件附件 | MP3 |
| CLI | 保存到 `~/.hermes/audio_cache/` | MP3 |

### 配置

```yaml
# 在 ~/.hermes/config.yaml 中
tts:
  provider: "edge"              # "edge" | "elevenlabs" | "openai" | "minimax" | "mistral" | "gemini" | "neutts"
  speed: 1.0                    # 全局速度倍率（提供商特定设置会覆盖此值）
  edge:
    voice: "en-US-AriaNeural"   # 322 种声音，74 种语言
    speed: 1.0                  # 转换为速率百分比（+/-%）
  elevenlabs:
    voice_id: "pNInz6obpgDQGcFmaJgB"  # Adam
    model_id: "eleven_multilingual_v2"
  openai:
    model: "gpt-4o-mini-tts"
    voice: "alloy"              # alloy、echo、fable、onyx、nova、shimmer
    base_url: "https://api.openai.com/v1"  # 覆盖 OpenAI 兼容 TTS 端点
    speed: 1.0                  # 0.25 - 4.0
  minimax:
    model: "speech-2.8-hd"     # speech-2.8-hd（默认）、speech-2.8-turbo
    voice_id: "English_Graceful_Lady"  # 参见 https://platform.minimax.io/faq/system-voice-id
    speed: 1                    # 0.5 - 2.0
    vol: 1                      # 0 - 10
    pitch: 0                    # -12 - 12
  mistral:
    model: "voxtral-mini-tts-2603"
    voice_id: "c69964a6-ab8b-4f8a-9465-ec0925096ec8"  # Paul - Neutral（默认）
  gemini:
    model: "gemini-2.5-flash-preview-tts"  # 或 gemini-2.5-pro-preview-tts
    voice: "Kore"               # 30 种预建声音：Zephyr、Puck、Kore、Enceladus、Gacrux 等
  neutts:
    ref_audio: ''
    ref_text: ''
    model: neuphonic/neutts-air-q4-gguf
    device: cpu
```

**速度控制**：全局 `tts.speed` 值默认应用于所有提供商。每个提供商可以用自己的 `speed` 设置覆盖它（例如 `tts.openai.speed: 1.5`）。提供商特定的速度优先于全局值。默认值为 `1.0`（正常速度）。

### Telegram 语音气泡与 ffmpeg

Telegram 语音气泡需要 Opus/OGG 音频格式：

- **OpenAI、ElevenLabs 和 Mistral** 原生生成 Opus -- 无需额外设置
- **Edge TTS**（默认）输出 MP3，需要 **ffmpeg** 进行转换：
- **MiniMax TTS** 输出 MP3，需要 **ffmpeg** 转换为 Telegram 语音气泡格式
- **Google Gemini TTS** 输出原始 PCM，使用 **ffmpeg** 直接编码为 Opus 用于 Telegram 语音气泡
- **NeuTTS** 输出 WAV，同样需要 **ffmpeg** 转换为 Telegram 语音气泡格式

```bash
# Ubuntu/Debian
sudo apt install ffmpeg

# macOS
brew install ffmpeg

# Fedora
sudo dnf install ffmpeg
```

没有 ffmpeg 时，Edge TTS、MiniMax TTS 和 NeuTTS 音频将作为普通音频文件发送（可播放，但显示为矩形播放器而非语音气泡）。

:::tip
如果你想在不安装 ffmpeg 的情况下使用语音气泡，请切换到 OpenAI、ElevenLabs 或 Mistral 提供商。
:::

## 语音消息转录 (STT)

在 Telegram、Discord、WhatsApp、Slack 或 Signal 上发送的语音消息会自动转录并作为文本注入对话。智能体将转录文本视为普通文本。

| 提供商 | 质量 | 费用 | API 密钥 |
|--------|------|------|----------|
| **本地 Whisper**（默认） | 良好 | 免费 | 无需密钥 |
| **Groq Whisper API** | 良好至最佳 | 免费层级 | `GROQ_API_KEY` |
| **OpenAI Whisper API** | 良好至最佳 | 付费 | `VOICE_TOOLS_OPENAI_KEY` 或 `OPENAI_API_KEY` |

:::info 零配置
安装 `faster-whisper` 后即可开箱即用进行本地转录。如果不可用，Hermes 也可以使用常见安装路径（如 `/opt/homebrew/bin`）中的本地 `whisper` CLI 或通过 `HERMES_LOCAL_STT_COMMAND` 指定的自定义命令。
:::

### 配置

```yaml
# 在 ~/.hermes/config.yaml 中
stt:
  provider: "local"           # "local" | "groq" | "openai" | "mistral"
  local:
    model: "base"             # tiny、base、small、medium、large-v3
  openai:
    model: "whisper-1"        # whisper-1、gpt-4o-mini-transcribe、gpt-4o-transcribe
  mistral:
    model: "voxtral-mini-latest"  # voxtral-mini-latest、voxtral-mini-2602
```

### 提供商详情

**本地 (faster-whisper)** -- 通过 [faster-whisper](https://github.com/SYSTRAN/faster-whisper) 在本地运行 Whisper。默认使用 CPU，如有 GPU 则使用 GPU。模型大小：

| 模型 | 大小 | 速度 | 质量 |
|------|------|------|------|
| `tiny` | ~75 MB | 最快 | 基础 |
| `base` | ~150 MB | 快速 | 良好（默认） |
| `small` | ~500 MB | 中等 | 较好 |
| `medium` | ~1.5 GB | 较慢 | 很好 |
| `large-v3` | ~3 GB | 最慢 | 最佳 |

**Groq API** -- 需要 `GROQ_API_KEY`。当你需要免费的云端 STT 选项时，这是很好的云端备选方案。

**OpenAI API** -- 优先使用 `VOICE_TOOLS_OPENAI_KEY`，回退到 `OPENAI_API_KEY`。支持 `whisper-1`、`gpt-4o-mini-transcribe` 和 `gpt-4o-transcribe`。

**Mistral API (Voxtral Transcribe)** -- 需要 `MISTRAL_API_KEY`。使用 Mistral 的 [Voxtral Transcribe](https://docs.mistral.ai/capabilities/audio/speech_to_text/) 模型。支持 13 种语言、说话人分离和词级时间戳。通过 `pip install hermes-agent[mistral]` 安装。

**自定义本地 CLI 备选** -- 如果你想让 Hermes 直接调用本地转录命令，请设置 `HERMES_LOCAL_STT_COMMAND`。命令模板支持 `{input_path}`、`{output_dir}`、`{language}` 和 `{model}` 占位符。

### 回退行为

如果你配置的提供商不可用，Hermes 会自动回退：
- **本地 faster-whisper 不可用** -> 在尝试云端提供商之前，先尝试本地 `whisper` CLI 或 `HERMES_LOCAL_STT_COMMAND`
- **Groq 密钥未设置** -> 回退到本地转录，然后是 OpenAI
- **OpenAI 密钥未设置** -> 回退到本地转录，然后是 Groq
- **Mistral 密钥/SDK 未设置** -> 在自动检测中跳过；继续尝试下一个可用提供商
- **没有可用选项** -> 语音消息将透传，并附带准确的提示信息告知用户
