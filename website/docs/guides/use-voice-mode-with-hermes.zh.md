---
sidebar_position: 8
title: "使用 Hermes 语音模式"
description: "在 CLI、Telegram、Discord 和 Discord 语音频道中设置和使用 Hermes 语音模式的实用指南"
---

# 使用 Hermes 语音模式

本指南是[语音模式功能参考](/docs/user-guide/features/voice-mode)的实用配套文档。

如果功能页面解释了语音模式能做什么，本指南则展示如何真正用好它。

## 语音模式适用场景

语音模式在以下情况特别有用：
- 你想要免手操作的 CLI 工作流
- 你想在 Telegram 或 Discord 中获得语音回复
- 你想让 Hermes 在 Discord 语音频道中进行实时对话
- 你想在走动时快速捕捉想法、调试或进行交互，而不是打字

## 选择你的语音模式设置

Hermes 中实际上有三种不同的语音体验。

| 模式 | 最适合 | 平台 |
|---|---|---|
| 交互式麦克风循环 | 编码或研究时的个人免手操作 | CLI |
| 聊天中的语音回复 | 与正常消息一起的语音回应 | Telegram、Discord |
| 实时语音频道机器人 | 语音频道中的群组或个人实时对话 | Discord 语音频道 |

推荐的路径：
1. 先让文本模式正常工作
2. 再启用语音回复
3. 最后如果你想要完整体验，再尝试 Discord 语音频道

## 步骤 1：确保普通 Hermes 先正常工作

在接触语音模式之前，验证：
- Hermes 可以启动
- 你的提供商已配置
- 代理可以正常回答文本提示

```bash
hermes
```

问一些简单的问题：

```text
What tools do you have available?
```

如果这还不正常，先修复文本模式。

## 步骤 2：安装正确的扩展

### CLI 麦克风 + 播放

```bash
pip install "hermes-agent[voice]"
```

### 消息平台

```bash
pip install "hermes-agent[messaging]"
```

### 高级 ElevenLabs TTS

```bash
pip install "hermes-agent[tts-premium]"
```

### 本地 NeuTTS（可选）

```bash
python -m pip install -U neutts[all]
```

### 全部

```bash
pip install "hermes-agent[all]"
```

## 步骤 3：安装系统依赖

### macOS

```bash
brew install portaudio ffmpeg opus
brew install espeak-ng
```

### Ubuntu / Debian

```bash
sudo apt install portaudio19-dev ffmpeg libopus0
sudo apt install espeak-ng
```

为什么这些很重要：
- `portaudio` -> CLI 语音模式的麦克风输入/播放
- `ffmpeg` -> TTS 和消息传递的音频转换
- `opus` -> Discord 语音编解码器支持
- `espeak-ng` -> NeuTTS 的音素化后端

## 步骤 4：选择 STT 和 TTS 提供商

Hermes 支持本地和云端语音栈。

### 最简单/最便宜的设置

使用本地 STT 和免费的 Edge TTS：
- STT 提供商：`local`
- TTS 提供商：`edge`

这通常是最好的起点。

### 环境文件示例

添加到 `~/.hermes/.env`：

```bash
# 云端 STT 选项（本地不需要密钥）
GROQ_API_KEY=***
VOICE_TOOLS_OPENAI_KEY=***

# 高级 TTS（可选）
ELEVENLABS_API_KEY=***
```

### 提供商建议

#### 语音转文字

- `local` -> 隐私和零成本使用的最佳默认选择
- `groq` -> 非常快的云端转录
- `openai` -> 好的付费备选

#### 文字转语音

- `edge` -> 免费，对大多数用户够用
- `neutts` -> 免费的本地/设备端 TTS
- `elevenlabs` -> 最佳质量
- `openai` -> 不错的中间选择
- `mistral` -> 多语言，原生 Opus

### 如果你使用 `hermes setup`

如果你在设置向导中选择了 NeuTTS，Hermes 会检查 `neutts` 是否已安装。如果缺失，向导会告诉你 NeuTTS 需要 Python 包 `neutts` 和系统包 `espeak-ng`，提供为你安装的选项，使用你平台的包管理器安装 `espeak-ng`，然后运行：

```bash
python -m pip install -U neutts[all]
```

如果你跳过该安装或安装失败，向导会回退到 Edge TTS。

## 步骤 5：推荐配置

```yaml
voice:
  record_key: "ctrl+b"
  max_recording_seconds: 120
  auto_tts: false
  silence_threshold: 200
  silence_duration: 3.0

stt:
  provider: "local"
  local:
    model: "base"

tts:
  provider: "edge"
  edge:
    voice: "en-US-AriaNeural"
```

这对大多数人来说是一个好的保守默认值。

如果你想要本地 TTS，将 `tts` 块替换为：

```yaml
tts:
  provider: "neutts"
  neutts:
    ref_audio: ''
    ref_text: ''
    model: neuphonic/neutts-air-q4-gguf
    device: cpu
```

## 用例 1：CLI 语音模式

## 开启它

启动 Hermes：

```bash
hermes
```

在 CLI 中：

```text
/voice on
```

### 录音流程

默认按键：
- `Ctrl+B`

工作流：
1. 按 `Ctrl+B`
2. 说话
3. 等待静音检测自动停止录音
4. Hermes 转录并回应
5. 如果 TTS 已开启，它会朗读答案
6. 循环可以自动重启以持续使用

### 有用的命令

```text
/voice
/voice on
/voice off
/voice tts
/voice status
```

### 好的 CLI 工作流

#### 即时调试

说：

```text
I keep getting a docker permission error. Help me debug it.
```

然后继续免手操作：
- "Read the last error again"
- "Explain the root cause in simpler terms"
- "Now give me the exact fix"

#### 研究/头脑风暴

适合：
- 边走边思考
- 口述半成形的想法
- 让 Hermes 实时组织你的思路

#### 无障碍/少打字会话

如果打字不方便，语音模式是保持在完整 Hermes 循环中最快的方式之一。

## 调整 CLI 行为

### 静音阈值

如果 Hermes 启动/停止太敏感，调整：

```yaml
voice:
  silence_threshold: 250
```

阈值越高 = 越不敏感。

### 静音持续时间

如果你在句子之间经常停顿，增加：

```yaml
voice:
  silence_duration: 4.0
```

### 录音按键

如果 `Ctrl+B` 与你的终端或 tmux 习惯冲突：

```yaml
voice:
  record_key: "ctrl+space"
```

## 用例 2：Telegram 或 Discord 中的语音回复

这个模式比完整的语音频道更简单。

Hermes 保持普通聊天机器人的身份，但可以朗读回复。

### 启动网关

```bash
hermes gateway
```

### 开启语音回复

在 Telegram 或 Discord 中：

```text
/voice on
```

或

```text
/voice tts
```

### 模式

| 模式 | 含义 |
|---|---|
| `off` | 仅文字 |
| `voice_only` | 仅在用户发送语音时朗读 |
| `all` | 朗读每条回复 |

### 何时使用哪种模式

- `/voice on` 如果你只想对语音发起的消息获得语音回复
- `/voice tts` 如果你想要一个全时语音助手

### 好的消息工作流

#### 手机上的 Telegram 助手

适用场景：
- 你不在电脑旁
- 你想发送语音消息并获得快速的语音回复
- 你想让 Hermes 充当便携式研究或运维助手

#### 带语音输出的 Discord 私信

当你想要私密交互而不需要服务器频道 @提及行为时很有用。

## 用例 3：Discord 语音频道

这是最高级的模式。

Hermes 加入 Discord 语音频道，监听用户语音，转录它，运行正常的代理管道，并将回复朗读到频道中。

## 需要的 Discord 权限

除了正常的文字机器人设置外，确保机器人拥有：
- 连接
- 说话
- 最好有使用语音活动

还需要在开发者门户中启用特权意图：
- 在线状态意图
- 服务器成员意图
- 消息内容意图

## 加入和离开

在机器人所在的 Discord 文字频道中：

```text
/voice join
/voice leave
/voice status
```

### 加入后会发生什么

- 用户在语音频道中说话
- Hermes 检测语音边界
- 转录发布在关联的文字频道中
- Hermes 以文字和音频回应
- 文字频道是发出 `/voice join` 的那个

### Discord 语音频道使用的最佳实践

- 保持 `DISCORD_ALLOWED_USERS` 严格
- 先使用专用的机器人/测试频道
- 在尝试语音频道模式之前，先验证 STT 和 TTS 在普通文字聊天语音模式中正常工作

## 语音质量建议

### 最佳质量设置

- STT：本地 `large-v3` 或 Groq `whisper-large-v3`
- TTS：ElevenLabs

### 最佳速度/便利性设置

- STT：本地 `base` 或 Groq
- TTS：Edge

### 最佳零成本设置

- STT：本地
- TTS：Edge

## 常见故障模式

### "找不到音频设备"

安装 `portaudio`。

### "机器人加入了但听不到任何声音"

检查：
- 你的 Discord 用户 ID 在 `DISCORD_ALLOWED_USERS` 中
- 你没有被静音
- 特权意图已启用
- 机器人有连接/说话权限

### "它转录了但不说话"

检查：
- TTS 提供商配置
- ElevenLabs 或 OpenAI 的 API 密钥/配额
- Edge 转换路径的 `ffmpeg` 安装

### "Whisper 输出乱码"

尝试：
- 更安静的环境
- 更高的 `silence_threshold`
- 不同的 STT 提供商/模型
- 更短、更清晰的语句

### "在私信中可以但在服务器频道中不行"

这通常是 @提及策略的问题。

默认情况下，除非另有配置，否则机器人在 Discord 服务器文字频道中需要 `@提及`。

## 建议的第一周设置

如果你想要最短的成功路径：

1. 让文本 Hermes 正常工作
2. 安装 `hermes-agent[voice]`
3. 使用本地 STT + Edge TTS 的 CLI 语音模式
4. 然后在 Telegram 或 Discord 中启用 `/voice on`
5. 之后才尝试 Discord 语音频道模式

这种渐进方式使调试范围保持较小。

## 延伸阅读

- [语音模式功能参考](/docs/user-guide/features/voice-mode)
- [消息网关](/docs/user-guide/messaging)
- [Discord 设置](/docs/user-guide/messaging/discord)
- [Telegram 设置](/docs/user-guide/messaging/telegram)
- [配置](/docs/user-guide/configuration)
