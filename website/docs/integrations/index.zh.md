---
title: "集成"
sidebar_label: "概览"
sidebar_position: 0
---

# 集成

Hermes Agent 连接外部系统，用于 AI 推理、工具服务器、IDE 工作流、编程访问等。这些集成扩展了 Hermes 的功能范围和运行环境。

## AI 提供商和路由

Hermes 开箱即用地支持多个 AI 推理提供商。使用 `hermes model` 交互式配置，或在 `config.yaml` 中设置。

- **[AI 提供商](/docs/user-guide/features/provider-routing)** — OpenRouter、Anthropic、OpenAI、Google 以及任何 OpenAI 兼容端点。Hermes 自动检测每个提供商的视觉、流式传输和工具使用等能力。
- **[提供商路由](/docs/user-guide/features/provider-routing)** — 细粒度控制哪些底层提供商处理你的 OpenRouter 请求。通过排序、白名单、黑名单和显式优先级排序来优化成本、速度或质量。
- **[备用提供商](/docs/user-guide/features/fallback-providers)** — 当主模型遇到错误时自动切换到备用 LLM 提供商。包括主模型备用和独立的辅助任务备用，用于视觉、压缩和网页提取。

## 工具服务器（MCP）

- **[MCP 服务器](/docs/user-guide/features/mcp)** — 通过模型上下文协议将 Hermes 连接到外部工具服务器。无需编写原生 Hermes 工具即可访问 GitHub、数据库、文件系统、浏览器栈、内部 API 等工具。支持 stdio 和 SSE 两种传输方式、按服务器的工具过滤以及感知能力的资源/提示注册。

## 网络搜索后端

`web_search` 和 `web_extract` 工具支持四种后端提供商，通过 `config.yaml` 或 `hermes tools` 配置：

| 后端 | 环境变量 | 搜索 | 提取 | 爬取 |
|------|---------|------|------|------|
| **Firecrawl**（默认） | `FIRECRAWL_API_KEY` | ✔ | ✔ | ✔ |
| **Parallel** | `PARALLEL_API_KEY` | ✔ | ✔ | — |
| **Tavily** | `TAVILY_API_KEY` | ✔ | ✔ | ✔ |
| **Exa** | `EXA_API_KEY` | ✔ | ✔ | — |

快速设置示例：

```yaml
web:
  backend: firecrawl    # firecrawl | parallel | tavily | exa
```

如果未设置 `web.backend`，后端会从可用的 API 密钥自动检测。也支持通过 `FIRECRAWL_API_URL` 使用自托管的 Firecrawl。

## 浏览器自动化

Hermes 包含完整的浏览器自动化功能，提供多种后端选项用于浏览网站、填写表单和提取信息：

- **Browserbase** — 托管云浏览器，配备反机器人工具、验证码解决和住宅代理
- **Browser Use** — 替代云浏览器提供商
- **通过 CDP 的本地 Chrome** — 使用 `/browser connect` 连接到正在运行的 Chrome 实例
- **本地 Chromium** — 通过 `agent-browser` CLI 的无头本地浏览器

设置和使用请参见[浏览器自动化](/docs/user-guide/features/browser)。

## 语音和 TTS 提供商

跨所有消息平台的文本转语音和语音转文本：

| 提供商 | 质量 | 费用 | API 密钥 |
||----------|---------|------|---------|
|| **Edge TTS**（默认） | 良好 | 免费 | 无需 |
|| **ElevenLabs** | 优秀 | 付费 | `ELEVENLABS_API_KEY` |
|| **OpenAI TTS** | 良好 | 付费 | `VOICE_TOOLS_OPENAI_KEY` |
|| **MiniMax** | 良好 | 付费 | `MINIMAX_API_KEY` |
|| **NeuTTS** | 良好 | 免费 | 无需 |

语音转文本支持三种提供商：本地 Whisper（免费，设备端运行）、Groq（快速云端）和 OpenAI Whisper API。语音消息转录支持 Telegram、Discord、WhatsApp 和其他消息平台。详情请参见[语音和 TTS](/docs/user-guide/features/tts) 和[语音模式](/docs/user-guide/features/voice-mode)。

## IDE 和编辑器集成

- **[IDE 集成（ACP）](/docs/user-guide/features/acp)** — 在 VS Code、Zed 和 JetBrains 等 ACP 兼容编辑器中使用 Hermes Agent。Hermes 作为 ACP 服务器运行，在编辑器中渲染聊天消息、工具活动、文件差异和终端命令。

## 编程访问

- **[API 服务器](/docs/user-guide/features/api-server)** — 将 Hermes 暴露为 OpenAI 兼容的 HTTP 端点。任何使用 OpenAI 格式的前端 — Open WebUI、LobeChat、LibreChat、NextChat、ChatBox — 都可以连接并使用 Hermes 作为后端及其完整工具集。

## 记忆和个性化

- **[内置记忆](/docs/user-guide/features/memory)** — 通过 `MEMORY.md` 和 `USER.md` 文件实现的持久化策展记忆。代理维护跨会话存活的有界个人笔记和用户配置文件数据存储。
- **[记忆提供商](/docs/user-guide/features/memory-providers)** — 接入外部记忆后端以实现更深度的个性化。支持七种提供商：Honcho（辩证推理）、OpenViking（分层检索）、Mem0（云端提取）、Hindsight（知识图谱）、Holographic（本地 SQLite）、RetainDB（混合搜索）和 ByteRover（基于 CLI）。

## 消息平台

Hermes 作为网关机器人运行在 15+ 消息平台上，所有平台通过相同的 `gateway` 子系统配置：

- **[Telegram](/docs/user-guide/messaging/telegram)**、**[Discord](/docs/user-guide/messaging/discord)**、**[Slack](/docs/user-guide/messaging/slack)**、**[WhatsApp](/docs/user-guide/messaging/whatsapp)**、**[Signal](/docs/user-guide/messaging/signal)**、**[Matrix](/docs/user-guide/messaging/matrix)**、**[Mattermost](/docs/user-guide/messaging/mattermost)**、**[Email](/docs/user-guide/messaging/email)**、**[SMS](/docs/user-guide/messaging/sms)**、**[DingTalk](/docs/user-guide/messaging/dingtalk)**、**[Feishu/Lark](/docs/user-guide/messaging/feishu)**、**[WeCom](/docs/user-guide/messaging/wecom)**、**[WeCom Callback](/docs/user-guide/messaging/wecom-callback)**、**[Weixin](/docs/user-guide/messaging/weixin)**、**[BlueBubbles](/docs/user-guide/messaging/bluebubbles)**、**[QQ Bot](/docs/user-guide/messaging/qqbot)**、**[Home Assistant](/docs/user-guide/messaging/homeassistant)**、**[Webhooks](/docs/user-guide/messaging/webhooks)**

平台对比表和设置指南请参见[消息网关概览](/docs/user-guide/messaging)。

## 家庭自动化

- **[Home Assistant](/docs/user-guide/messaging/homeassistant)** — 通过四个专用工具（`ha_list_entities`、`ha_get_state`、`ha_list_services`、`ha_call_service`）控制智能家居设备。配置 `HASS_TOKEN` 后 Home Assistant 工具集自动激活。

## 插件

- **[插件系统](/docs/user-guide/features/plugins)** — 无需修改核心代码即可使用自定义工具、生命周期钩子和 CLI 命令扩展 Hermes。插件从 `~/.hermes/plugins/`、项目本地的 `.hermes/plugins/` 和 pip 安装的入口点中发现。
- **[构建插件](/docs/guides/build-a-hermes-plugin)** — 使用工具、钩子和 CLI 命令创建 Hermes 插件的分步指南。

## 训练和评估

- **[RL 训练](/docs/user-guide/features/rl-training)** — 从代理会话生成轨迹数据，用于强化学习和模型微调。支持具有可自定义奖励函数的 Atropos 环境。
- **[批处理](/docs/user-guide/features/batch-processing)** — 跨数百个提示并行运行代理，生成结构化的 ShareGPT 格式轨迹数据，用于训练数据生成或评估。
