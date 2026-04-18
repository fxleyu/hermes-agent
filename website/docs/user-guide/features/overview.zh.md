---
title: "功能概览"
sidebar_label: "概览"
sidebar_position: 1
---

# 功能概览

Hermes Agent 包含丰富的功能集，远超基本聊天。从持久化记忆和文件感知上下文，到浏览器自动化和语音对话，这些功能协同工作，使 Hermes 成为一个强大的自主助手。

## 核心功能

- **[工具与工具集](tools.md)** — 工具是扩展代理能力的函数。它们被组织为逻辑工具集，可按平台启用或禁用，涵盖网页搜索、终端执行、文件编辑、记忆、委派等。
- **[技能系统](skills.md)** — 代理可按需加载的知识文档。技能遵循渐进式披露模式以最小化 token 使用量，并兼容 [agentskills.io](https://agentskills.io/specification) 开放标准。
- **[持久化记忆](memory.md)** — 有界的、精心管理的记忆，可跨会话持久化。Hermes 通过 `MEMORY.md` 和 `USER.md` 记住你的偏好、项目、环境以及学到的知识。
- **[上下文文件](context-files.md)** — Hermes 自动发现并加载项目上下文文件（`.hermes.md`、`AGENTS.md`、`CLAUDE.md`、`SOUL.md`、`.cursorrules`），这些文件影响其在项目中的行为方式。
- **[上下文引用](context-references.md)** — 输入 `@` 后跟引用，可将文件、文件夹、git diff 和 URL 直接注入到消息中。Hermes 会内联展开引用并自动附加内容。
- **[检查点](../checkpoints-and-rollback.md)** — Hermes 在进行文件修改前自动对工作目录进行快照，为你提供安全网，出错时可使用 `/rollback` 回滚。

## 自动化

- **[定时任务（Cron）](cron.md)** — 使用自然语言或 cron 表达式安排任务自动运行。任务可附加技能、将结果投递到任何平台，并支持暂停/恢复/编辑操作。
- **[子代理委派](delegation.md)** — `delegate_task` 工具生成具有隔离上下文、受限工具集和独立终端会话的子代理实例。最多可运行 3 个并发子代理进行并行工作流。
- **[代码执行](code-execution.md)** — `execute_code` 工具允许代理编写 Python 脚本，以编程方式调用 Hermes 工具，通过沙盒 RPC 执行将多步骤工作流压缩为单次 LLM 轮次。
- **[事件钩子](hooks.md)** — 在关键生命周期节点运行自定义代码。网关钩子处理日志、告警和 webhook；插件钩子处理工具拦截、指标和防护措施。
- **[批处理](batch-processing.md)** — 在数百或数千个提示词上并行运行 Hermes 代理，生成结构化的 ShareGPT 格式轨迹数据，用于训练数据生成或评估。

## 媒体与网页

- **[语音模式](voice-mode.md)** — 跨 CLI 和消息平台的完整语音交互。使用麦克风与代理对话、听取语音回复，并在 Discord 语音频道中进行实时语音对话。
- **[浏览器自动化](browser.md)** — 支持多种后端的完整浏览器自动化：Browserbase 云端、Browser Use 云端、通过 CDP 连接本地 Chrome 或本地 Chromium。浏览网站、填写表单并提取信息。
- **[视觉与图片粘贴](vision.md)** — 多模态视觉支持。从剪贴板粘贴图片到 CLI，让代理使用任何支持视觉的模型进行分析、描述或处理。
- **[图像生成](image-generation.md)** — 通过 FAL.ai 从文本提示生成图像。支持八个模型（FLUX 2 Klein/Pro、GPT-Image 1.5、Nano Banana Pro、Ideogram V3、Recraft V4 Pro、Qwen、Z-Image Turbo）；通过 `hermes tools` 选择。
- **[语音与 TTS](tts.md)** — 跨所有消息平台的文字转语音输出和语音消息转录，提供五种服务商选项：Edge TTS（免费）、ElevenLabs、OpenAI TTS、MiniMax 和 NeuTTS。

## 集成

- **[MCP 集成](mcp.md)** — 通过 stdio 或 HTTP 传输连接任何 MCP 服务器。访问来自 GitHub、数据库、文件系统和内部 API 的外部工具，无需编写原生 Hermes 工具。包括每服务器工具过滤和采样支持。
- **[服务商路由](provider-routing.md)** — 对哪些 AI 服务商处理你的请求进行细粒度控制。通过排序、白名单、黑名单和优先级排序优化成本、速度或质量。
- **[备用服务商](fallback-providers.md)** — 当主要模型遇到错误时自动故障转移到备用 LLM 服务商，包括对视觉和压缩等辅助任务的独立备用支持。
- **[凭据池](credential-pools.md)** — 为同一服务商分配多个 API 密钥进行调用。在遇到速率限制或故障时自动轮换。
- **[记忆服务商](memory-providers.md)** — 接入外部记忆后端（Honcho、OpenViking、Mem0、Hindsight、Holographic、RetainDB、ByteRover），用于超越内置记忆系统的跨会话用户建模和个性化。
- **[API 服务器](api-server.md)** — 将 Hermes 暴露为兼容 OpenAI 的 HTTP 端点。连接任何支持 OpenAI 格式的前端 — Open WebUI、LobeChat、LibreChat 等。
- **[IDE 集成（ACP）](acp.md)** — 在 ACP 兼容的编辑器（如 VS Code、Zed 和 JetBrains）中使用 Hermes。聊天、工具活动、文件差异和终端命令都在编辑器中渲染。
- **[RL 训练](rl-training.md)** — 从代理会话生成轨迹数据，用于强化学习和模型微调。

## 自定义

- **[个性与 SOUL.md](personality.md)** — 完全可自定义的代理个性。`SOUL.md` 是主要身份文件 — 系统提示词中的第一项 — 你可以按会话切换内置或自定义的 `/personality` 预设。
- **[皮肤与主题](skins.md)** — 自定义 CLI 的视觉展示：横幅颜色、加载动画表情和动词、响应框标签、品牌文字以及工具活动前缀。
- **[插件](plugins.md)** — 无需修改核心代码即可添加自定义工具、钩子和集成。三种插件类型：通用插件（工具/钩子）、记忆服务商（跨会话知识）和上下文引擎（替代上下文管理）。通过统一的 `hermes plugins` 交互式 UI 管理。
