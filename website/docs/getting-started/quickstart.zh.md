---
sidebar_position: 1
title: "快速入门"
description: "你与 Hermes Agent 的第一次对话——从安装到聊天只需 2 分钟"
---

# 快速入门

本指南将引导你完成安装 Hermes Agent、设置提供商和进行第一次对话。读完后，你将了解关键功能以及如何进一步探索。

## 1. 安装 Hermes Agent

运行一行命令安装程序：

```bash
# Linux / macOS / WSL2 / Android (Termux)
curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash
```

:::tip Android / Termux
如果你在手机上安装，请参阅专门的 [Termux 指南](./termux.md) 了解经过测试的手动安装路径、支持的额外依赖以及当前 Android 特定的限制。
:::

:::tip Windows 用户
先安装 [WSL2](https://learn.microsoft.com/en-us/windows/wsl/install)，然后在 WSL2 终端内运行上述命令。
:::

安装完成后，重新加载你的 shell：

```bash
source ~/.bashrc   # 或 source ~/.zshrc
```

## 2. 设置提供商

安装程序会自动配置你的 LLM 提供商。要稍后更改，使用以下命令之一：

```bash
hermes model       # 选择你的 LLM 提供商和模型
hermes tools       # 配置启用哪些工具
hermes setup       # 或一次性配置所有内容
```

`hermes model` 会引导你选择推理提供商：

| 提供商 | 说明 | 设置方式 |
|----------|-----------|---------------|
| **Nous Portal** | 基于订阅，零配置 | 通过 `hermes model` 进行 OAuth 登录 |
| **OpenAI Codex** | ChatGPT OAuth，使用 Codex 模型 | 通过 `hermes model` 进行设备码认证 |
| **Anthropic** | 直接使用 Claude 模型（Pro/Max 或 API 密钥） | 通过 `hermes model` 使用 Claude Code 认证，或 Anthropic API 密钥 |
| **OpenRouter** | 跨多种模型的多提供商路由 | 输入你的 API 密钥 |
| **Z.AI** | GLM / 智谱托管模型 | 设置 `GLM_API_KEY` / `ZAI_API_KEY` |
| **Kimi / Moonshot** | Moonshot 托管的编码和对话模型 | 设置 `KIMI_API_KEY` |
| **Kimi / Moonshot 中国** | 中国区 Moonshot 端点 | 设置 `KIMI_CN_API_KEY` |
| **Arcee AI** | Trinity 模型 | 设置 `ARCEEAI_API_KEY` |
| **MiniMax** | 国际版 MiniMax 端点 | 设置 `MINIMAX_API_KEY` |
| **MiniMax 中国** | 中国区 MiniMax 端点 | 设置 `MINIMAX_CN_API_KEY` |
| **阿里云** | 通过 DashScope 使用通义千问模型 | 设置 `DASHSCOPE_API_KEY` |
| **Hugging Face** | 通过统一路由使用 20+ 开源模型（Qwen、DeepSeek、Kimi 等） | 设置 `HF_TOKEN` |
| **Kilo Code** | KiloCode 托管的模型 | 设置 `KILOCODE_API_KEY` |
| **OpenCode Zen** | 按量付费访问精选模型 | 设置 `OPENCODE_ZEN_API_KEY` |
| **OpenCode Go** | 每月 $10 订阅开源模型 | 设置 `OPENCODE_GO_API_KEY` |
| **DeepSeek** | 直接 DeepSeek API 访问 | 设置 `DEEPSEEK_API_KEY` |
| **GitHub Copilot** | GitHub Copilot 订阅（GPT-5.x、Claude、Gemini 等） | 通过 `hermes model` 进行 OAuth，或 `COPILOT_GITHUB_TOKEN` / `GH_TOKEN` |
| **GitHub Copilot ACP** | Copilot ACP 智能体后端（生成本地 `copilot` CLI） | `hermes model`（需要 `copilot` CLI + `copilot login`） |
| **Vercel AI Gateway** | Vercel AI Gateway 路由 | 设置 `AI_GATEWAY_API_KEY` |
| **自定义端点** | VLLM、SGLang、Ollama 或任何 OpenAI 兼容 API | 设置基础 URL + API 密钥 |

:::caution 最低上下文窗口：64K token
Hermes Agent 要求模型至少具有 **64,000 个 token** 的上下文窗口。上下文窗口较小的模型无法为多步骤工具调用工作流维持足够的工作记忆，将在启动时被拒绝。大多数托管模型（Claude、GPT、Gemini、Qwen、DeepSeek）轻松满足此要求。如果你运行本地模型，请将其上下文大小设置为至少 64K（例如 llama.cpp 的 `--ctx-size 65536` 或 Ollama 的 `-c 65536`）。
:::

:::tip
你可以随时通过 `hermes model` 切换提供商——无需更改代码，无供应商锁定。配置自定义端点时，Hermes 会提示输入上下文窗口大小，并在可能时自动检测。详见 [上下文长度检测](../integrations/providers.md#context-length-detection)。
:::

## 3. 开始聊天

```bash
hermes
```

就这样！你会看到一个欢迎横幅，显示你的模型、可用工具和已安装技能。输入消息并按回车。

```
❯ What can you help me with?
```

智能体开箱即用，可以访问网页搜索、文件操作、终端命令等工具。

## 4. 试用关键功能

### 让它使用终端

```
❯ What's my disk usage? Show the top 5 largest directories.
```

智能体将代表你运行终端命令并显示结果。

### 使用斜杠命令

输入 `/` 查看所有命令的自动补全下拉列表：

| 命令 | 功能 |
|---------|-------------|
| `/help` | 显示所有可用命令 |
| `/tools` | 列出可用工具 |
| `/model` | 交互式切换模型 |
| `/personality pirate` | 尝试有趣的个性 |
| `/save` | 保存对话 |

### 多行输入

按 `Alt+Enter` 或 `Ctrl+J` 添加新行。非常适合粘贴代码或编写详细提示。

### 中断智能体

如果智能体耗时过长，只需输入新消息并按回车——它会中断当前任务并切换到你的新指令。`Ctrl+C` 也可以。

### 恢复会话

退出时，hermes 会打印恢复命令：

```bash
hermes --continue    # 恢复最近的会话
hermes -c            # 简写形式
```

## 5. 深入探索

接下来可以尝试这些：

### 设置沙箱终端

为了安全，在 Docker 容器或远程服务器中运行智能体：

```bash
hermes config set terminal.backend docker    # Docker 隔离
hermes config set terminal.backend ssh       # 远程服务器
```

### 连接消息平台

通过 Telegram、Discord、Slack、WhatsApp、Signal、Email 或 Home Assistant 从手机或其他界面与 Hermes 聊天：

```bash
hermes gateway setup    # 交互式平台配置
```

### 添加语音模式

想要在 CLI 中使用麦克风输入或在消息中使用语音回复？

```bash
pip install "hermes-agent[voice]"
# 包含 faster-whisper 用于免费本地语音转文字
```

然后启动 Hermes 并在 CLI 中启用：

```text
/voice on
```

按 `Ctrl+B` 录音，或使用 `/voice tts` 让 Hermes 朗读回复。详见 [语音模式](../user-guide/features/voice-mode.md) 了解 CLI、Telegram、Discord 和 Discord 语音频道的完整设置。

### 安排自动化任务

```
❯ Every morning at 9am, check Hacker News for AI news and send me a summary on Telegram.
```

智能体将设置一个通过网关自动运行的 cron 任务。

### 浏览和安装技能

```bash
hermes skills search kubernetes
hermes skills search react --source skills-sh
hermes skills search https://mintlify.com/docs --source well-known
hermes skills install openai/skills/k8s
hermes skills install official/security/1password
hermes skills install skills-sh/vercel-labs/json-render/json-render-react --force
```

提示：
- 使用 `--source skills-sh` 搜索公共 `skills.sh` 目录。
- 使用 `--source well-known` 配合文档/网站 URL 从 `/.well-known/skills/index.json` 发现技能。
- 仅在审查第三方技能后使用 `--force`。它可以覆盖非危险的策略阻止，但不能覆盖 `dangerous` 扫描判定。

或在聊天中使用 `/skills` 斜杠命令。

### 在编辑器中通过 ACP 使用 Hermes

Hermes 还可以作为 ACP 服务器运行，供 VS Code、Zed 和 JetBrains 等 ACP 兼容编辑器使用：

```bash
pip install -e '.[acp]'
hermes acp
```

详见 [ACP 编辑器集成](../user-guide/features/acp.md) 了解设置细节。

### 试用 MCP 服务器

通过 Model Context Protocol 连接外部工具：

```yaml
# 添加到 ~/.hermes/config.yaml
mcp_servers:
  github:
    command: npx
    args: ["-y", "@modelcontextprotocol/server-github"]
    env:
      GITHUB_PERSONAL_ACCESS_TOKEN: "ghp_xxx"
```

---

## 快速参考

| 命令 | 描述 |
|---------|-------------|
| `hermes` | 开始聊天 |
| `hermes model` | 选择你的 LLM 提供商和模型 |
| `hermes tools` | 配置每个平台启用哪些工具 |
| `hermes setup` | 完整设置向导（一次性配置所有内容） |
| `hermes doctor` | 诊断问题 |
| `hermes update` | 更新到最新版本 |
| `hermes gateway` | 启动消息网关 |
| `hermes --continue` | 恢复上次会话 |

## 下一步

- **[CLI 指南](../user-guide/cli.md)** —— 掌握终端界面
- **[配置](../user-guide/configuration.md)** —— 自定义你的设置
- **[消息网关](../user-guide/messaging/index.md)** —— 连接 Telegram、Discord、Slack、WhatsApp、Signal、Email 或 Home Assistant
- **[工具与工具集](../user-guide/features/tools.md)** —— 探索可用功能
