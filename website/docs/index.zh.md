---
slug: /zh
sidebar_position: 0
title: "Hermes Agent 文档"
description: "由 Nous Research 打造的自我进化 AI 智能体。内置学习循环，从经验中创建技能，在使用过程中改进技能，并跨会话记忆。"
hide_table_of_contents: true
displayed_sidebar: docs
---

# Hermes Agent

由 [Nous Research](https://nousresearch.com) 打造的自我进化 AI 智能体。唯一内置学习循环的智能体——它从经验中创建技能，在使用过程中改进技能，主动推动自身持久化知识，并在跨会话中构建对你的深入理解模型。

<div style={{display: 'flex', gap: '1rem', marginBottom: '2rem', flexWrap: 'wrap'}}>
  <a href="/docs/getting-started/installation" style={{display: 'inline-block', padding: '0.6rem 1.2rem', backgroundColor: '#FFD700', color: '#07070d', borderRadius: '8px', fontWeight: 600, textDecoration: 'none'}}>快速开始 →</a>
  <a href="https://github.com/NousResearch/hermes-agent" style={{display: 'inline-block', padding: '0.6rem 1.2rem', border: '1px solid rgba(255,215,0,0.2)', borderRadius: '8px', textDecoration: 'none'}}>在 GitHub 上查看</a>
</div>

## 什么是 Hermes Agent？

它不是绑定在 IDE 上的编码助手，也不是单一 API 的聊天机器人包装器。它是一个**自主智能体**，运行时间越长越强大。它可以运行在任何地方——5 美元的 VPS、GPU 集群，或者闲置时几乎零成本的无服务器基础设施（Daytona、Modal）。你可以通过 Telegram 与它对话，而它在你从未 SSH 登录的云虚拟机上工作。它不局限于你的笔记本电脑。

## 快速链接

| | |
|---|---|
| 🚀 **[安装](/docs/getting-started/installation)** | 在 Linux、macOS 或 WSL2 上 60 秒完成安装 |
| 📖 **[快速入门教程](/docs/getting-started/quickstart)** | 你的第一次对话和要尝试的关键功能 |
| 🗺️ **[学习路径](/docs/getting-started/learning-path)** | 根据你的经验水平找到合适的文档 |
| ⚙️ **[配置](/docs/user-guide/configuration)** | 配置文件、服务提供商、模型和选项 |
| 💬 **[消息网关](/docs/user-guide/messaging)** | 设置 Telegram、Discord、Slack 或 WhatsApp |
| 🔧 **[工具与工具集](/docs/user-guide/features/tools)** | 47 个内置工具及其配置方法 |
| 🧠 **[记忆系统](/docs/user-guide/features/memory)** | 跨会话增长的持久化记忆 |
| 📚 **[技能系统](/docs/user-guide/features/skills)** | 智能体创建并复用的程序性记忆 |
| 🔌 **[MCP 集成](/docs/user-guide/features/mcp)** | 连接 MCP 服务器，过滤其工具，安全扩展 Hermes |
| 🧭 **[在 Hermes 中使用 MCP](/docs/guides/use-mcp-with-hermes)** | 实用的 MCP 设置模式、示例和教程 |
| 🎙️ **[语音模式](/docs/user-guide/features/voice-mode)** | CLI、Telegram、Discord 和 Discord 语音频道中的实时语音交互 |
| 🗣️ **[在 Hermes 中使用语音模式](/docs/guides/use-voice-mode-with-hermes)** | Hermes 语音工作流的实操设置和使用模式 |
| 🎭 **[个性与 SOUL.md](/docs/user-guide/features/personality)** | 通过全局 SOUL.md 定义 Hermes 的默认语气 |
| 📄 **[上下文文件](/docs/user-guide/features/context-files)** | 影响每次对话的项目上下文文件 |
| 🔒 **[安全](/docs/user-guide/security)** | 命令审批、授权、容器隔离 |
| 💡 **[技巧与最佳实践](/docs/guides/tips)** | 充分利用 Hermes 的速效技巧 |
| 🏗️ **[架构](/docs/developer-guide/architecture)** | 底层工作原理 |
| ❓ **[常见问题与故障排除](/docs/reference/faq)** | 常见问题和解决方案 |

## 核心特性

- **闭环学习循环** —— 智能体策划的记忆与定期推动、自主技能创建、使用中的技能自我改进、基于 FTS5 的跨会话召回与 LLM 摘要，以及 [Honcho](https://github.com/plastic-labs/honcho) 辩证式用户建模
- **随处运行，不局限于笔记本电脑** —— 6 种终端后端：本地、Docker、SSH、Daytona、Singularity、Modal。Daytona 和 Modal 提供无服务器持久化——你的环境在闲置时休眠，几乎零成本
- **与你同在** —— CLI、Telegram、Discord、Slack、WhatsApp、Signal、Matrix、Mattermost、Email、SMS、DingTalk、飞书、企业微信、BlueBubbles、Home Assistant —— 一个网关覆盖 15+ 平台
- **由模型训练者打造** —— 由 [Nous Research](https://nousresearch.com) 创建，这是 Hermes、Nomos 和 Psyche 背后的实验室。兼容 [Nous Portal](https://portal.nousresearch.com)、[OpenRouter](https://openrouter.ai)、OpenAI 或任何端点
- **定时自动化** —— 内置 cron，可投递到任何平台
- **委托与并行化** —— 生成隔离的子智能体用于并行工作流。通过 `execute_code` 实现编程式工具调用，将多步骤管道压缩为单次推理调用
- **开放标准技能** —— 兼容 [agentskills.io](https://agentskills.io)。技能可移植、可共享，并通过技能中心进行社区贡献
- **全面的网页控制** —— 搜索、提取、浏览、视觉、图像生成、TTS
- **MCP 支持** —— 连接任何 MCP 服务器以扩展工具能力
- **面向研究** —— 批处理、轨迹导出、使用 Atropos 进行 RL 训练。由 [Nous Research](https://nousresearch.com) 打造——Hermes、Nomos 和 Psyche 模型背后的实验室
