---
sidebar_position: 3
title: '学习路径'
description: '根据你的经验水平和目标，选择在 Hermes Agent 文档中的学习路径。'
---

# 学习路径

Hermes Agent 功能强大——CLI 助手、Telegram/Discord 机器人、任务自动化、RL 训练等等。本页帮助你根据经验水平和想要完成的目标，找到从哪里开始以及阅读什么。

:::tip 从这里开始
如果你还没有安装 Hermes Agent，请先阅读 [安装指南](/docs/getting-started/installation)，然后按照 [快速入门](/docs/getting-started/quickstart) 操作。以下所有内容都假设你已有一个可用的安装。
:::

## 如何使用本页

- **知道你的水平？** 跳转到[按经验水平](#by-experience-level)表格，按照你所在层级的阅读顺序进行。
- **有特定目标？** 跳到[按使用场景](#by-use-case)，找到匹配的场景。
- **随便浏览？** 查看[功能概览](#key-features-at-a-glance)表格，快速了解 Hermes Agent 能做的一切。

## 按经验水平

| 水平 | 目标 | 推荐阅读 | 预计时间 |
|---|---|---|---|
| **初学者** | 启动并运行，进行基本对话，使用内置工具 | [安装](/docs/getting-started/installation) → [快速入门](/docs/getting-started/quickstart) → [CLI 使用](/docs/user-guide/cli) → [配置](/docs/user-guide/configuration) | 约 1 小时 |
| **中级** | 设置消息机器人，使用高级功能如记忆、定时任务和技能 | [会话](/docs/user-guide/sessions) → [消息](/docs/user-guide/messaging) → [工具](/docs/user-guide/features/tools) → [技能](/docs/user-guide/features/skills) → [记忆](/docs/user-guide/features/memory) → [定时任务](/docs/user-guide/features/cron) | 约 2-3 小时 |
| **高级** | 构建自定义工具，创建技能，使用 RL 训练模型，为项目做贡献 | [架构](/docs/developer-guide/architecture) → [添加工具](/docs/developer-guide/adding-tools) → [创建技能](/docs/developer-guide/creating-skills) → [RL 训练](/docs/user-guide/features/rl-training) → [贡献](/docs/developer-guide/contributing) | 约 4-6 小时 |

## 按使用场景

选择与你想做的事情匹配的场景。每个场景都按你应该阅读的顺序链接到相关文档。

### "我想要一个 CLI 编码助手"

使用 Hermes Agent 作为交互式终端助手来编写、审查和运行代码。

1. [安装](/docs/getting-started/installation)
2. [快速入门](/docs/getting-started/quickstart)
3. [CLI 使用](/docs/user-guide/cli)
4. [代码执行](/docs/user-guide/features/code-execution)
5. [上下文文件](/docs/user-guide/features/context-files)
6. [技巧与窍门](/docs/guides/tips)

:::tip
通过上下文文件将文件直接传入对话。Hermes Agent 可以读取、编辑和运行你项目中的代码。
:::

### "我想要一个 Telegram/Discord 机器人"

将 Hermes Agent 部署为你最喜欢的消息平台上的机器人。

1. [安装](/docs/getting-started/installation)
2. [配置](/docs/user-guide/configuration)
3. [消息概述](/docs/user-guide/messaging)
4. [Telegram 设置](/docs/user-guide/messaging/telegram)
5. [Discord 设置](/docs/user-guide/messaging/discord)
6. [语音模式](/docs/user-guide/features/voice-mode)
7. [在 Hermes 中使用语音模式](/docs/guides/use-voice-mode-with-hermes)
8. [安全](/docs/user-guide/security)

完整项目示例请参见：
- [每日简报机器人](/docs/guides/daily-briefing-bot)
- [团队 Telegram 助手](/docs/guides/team-telegram-assistant)

### "我想要自动化任务"

安排重复任务、运行批处理作业或链接智能体操作。

1. [快速入门](/docs/getting-started/quickstart)
2. [定时调度](/docs/user-guide/features/cron)
3. [批处理](/docs/user-guide/features/batch-processing)
4. [委托](/docs/user-guide/features/delegation)
5. [钩子](/docs/user-guide/features/hooks)

:::tip
定时任务让 Hermes Agent 按计划运行任务——每日摘要、定期检查、自动化报告——无需你在场。
:::

### "我想要构建自定义工具/技能"

用你自己的工具和可重用技能包扩展 Hermes Agent。

1. [工具概述](/docs/user-guide/features/tools)
2. [技能概述](/docs/user-guide/features/skills)
3. [MCP (Model Context Protocol)](/docs/user-guide/features/mcp)
4. [架构](/docs/developer-guide/architecture)
5. [添加工具](/docs/developer-guide/adding-tools)
6. [创建技能](/docs/developer-guide/creating-skills)

:::tip
工具是智能体可以调用的单个函数。技能是工具、提示和配置打包在一起的组合。从工具开始，进阶到技能。
:::

### "我想要训练模型"

使用强化学习通过 Hermes Agent 内置的 RL 训练管道微调模型行为。

1. [快速入门](/docs/getting-started/quickstart)
2. [配置](/docs/user-guide/configuration)
3. [RL 训练](/docs/user-guide/features/rl-training)
4. [提供商路由](/docs/user-guide/features/provider-routing)
5. [架构](/docs/developer-guide/architecture)

:::tip
RL 训练在你已经了解 Hermes Agent 如何处理对话和工具调用的基础上效果最好。如果你是新手，请先完成初学者路径。
:::

### "我想将它作为 Python 库使用"

以编程方式将 Hermes Agent 集成到你自己的 Python 应用中。

1. [安装](/docs/getting-started/installation)
2. [快速入门](/docs/getting-started/quickstart)
3. [Python 库指南](/docs/guides/python-library)
4. [架构](/docs/developer-guide/architecture)
5. [工具](/docs/user-guide/features/tools)
6. [会话](/docs/user-guide/sessions)

## 功能概览

不确定有什么可用的？这是主要功能的快速目录：

| 功能 | 描述 | 链接 |
|---|---|---|
| **工具** | 智能体可调用的内置工具（文件 I/O、搜索、shell 等） | [工具](/docs/user-guide/features/tools) |
| **技能** | 添加新功能的可安装插件包 | [技能](/docs/user-guide/features/skills) |
| **记忆** | 跨会话的持久化记忆 | [记忆](/docs/user-guide/features/memory) |
| **上下文文件** | 将文件和目录输入对话 | [上下文文件](/docs/user-guide/features/context-files) |
| **MCP** | 通过 Model Context Protocol 连接外部工具服务器 | [MCP](/docs/user-guide/features/mcp) |
| **定时任务** | 安排重复的智能体任务 | [定时任务](/docs/user-guide/features/cron) |
| **委托** | 生成子智能体进行并行工作 | [委托](/docs/user-guide/features/delegation) |
| **代码执行** | 在沙箱环境中运行代码 | [代码执行](/docs/user-guide/features/code-execution) |
| **浏览器** | 网页浏览和抓取 | [浏览器](/docs/user-guide/features/browser) |
| **钩子** | 事件驱动的回调和中间件 | [钩子](/docs/user-guide/features/hooks) |
| **批处理** | 批量处理多个输入 | [批处理](/docs/user-guide/features/batch-processing) |
| **RL 训练** | 使用强化学习微调模型 | [RL 训练](/docs/user-guide/features/rl-training) |
| **提供商路由** | 在多个 LLM 提供商之间路由请求 | [提供商路由](/docs/user-guide/features/provider-routing) |

## 下一步阅读

根据你目前所处的位置：

- **刚完成安装？** → 前往 [快速入门](/docs/getting-started/quickstart) 进行第一次对话。
- **完成了快速入门？** → 阅读 [CLI 使用](/docs/user-guide/cli) 和 [配置](/docs/user-guide/configuration) 来自定义你的设置。
- **对基础已经熟悉？** → 探索 [工具](/docs/user-guide/features/tools)、[技能](/docs/user-guide/features/skills) 和 [记忆](/docs/user-guide/features/memory) 来释放智能体的全部潜力。
- **为团队设置？** → 阅读 [安全](/docs/user-guide/security) 和 [会话](/docs/user-guide/sessions) 了解访问控制和对话管理。
- **准备好构建？** → 跳转到 [开发者指南](/docs/developer-guide/architecture) 了解内部原理并开始贡献。
- **想要实际示例？** → 查看 [指南](/docs/guides/tips) 部分了解实际项目和技巧。

:::tip
你不需要阅读所有内容。选择与你目标匹配的路径，按顺序跟随链接，你将很快变得高效。你可以随时回到本页找到下一步。
:::
