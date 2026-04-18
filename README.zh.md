<p align="center">
  <img src="assets/banner.png" alt="Hermes Agent" width="100%">
</p>

# Hermes Agent ☤

<p align="center">
  <a href="https://hermes-agent.nousresearch.com/docs/"><img src="https://img.shields.io/badge/Docs-hermes--agent.nousresearch.com-FFD700?style=for-the-badge" alt="文档"></a>
  <a href="https://discord.gg/NousResearch"><img src="https://img.shields.io/badge/Discord-5865F2?style=for-the-badge&logo=discord&logoColor=white" alt="Discord"></a>
  <a href="https://github.com/NousResearch/hermes-agent/blob/main/LICENSE"><img src="https://img.shields.io/badge/License-MIT-green?style=for-the-badge" alt="许可证: MIT"></a>
  <a href="https://nousresearch.com"><img src="https://img.shields.io/badge/Built%20by-Nous%20Research-blueviolet?style=for-the-badge" alt="由 Nous Research 构建"></a>
</p>

**由 [Nous Research](https://nousresearch.com) 构建的自我进化 AI 智能体。** 它是唯一内置学习循环的智能体——能从经验中创建技能，在使用过程中改进技能，主动提醒自己持久化知识，搜索自己的历史对话，并在跨会话中构建对你越来越深入的用户画像。可以在 5 美元的 VPS、GPU 集群或空闲时几乎零成本的无服务器基础设施上运行。它不依赖你的笔记本电脑——你可以通过 Telegram 与它对话，而它在云端虚拟机上工作。

使用任何你想要的模型——[Nous Portal](https://portal.nousresearch.com)、[OpenRouter](https://openrouter.ai)（200+ 模型）、[小米 MiMo](https://platform.xiaomimimo.com)、[z.ai/GLM](https://z.ai)、[Kimi/Moonshot](https://platform.moonshot.ai)、[MiniMax](https://www.minimax.io)、[Hugging Face](https://huggingface.co)、OpenAI，或你自己的端点。使用 `hermes model` 切换——无需修改代码，没有供应商锁定。

<table>
<tr><td><b>真正的终端界面</b></td><td>完整的 TUI，支持多行编辑、斜杠命令自动补全、对话历史记录、中断并重定向，以及流式工具输出。</td></tr>
<tr><td><b>随时随地与你同在</b></td><td>Telegram、Discord、Slack、WhatsApp、Signal 和 CLI——全部通过单一网关进程。语音消息转录，跨平台对话延续。</td></tr>
<tr><td><b>闭环学习循环</b></td><td>智能体自管理的记忆系统，支持定期提醒。复杂任务完成后自主创建技能。技能在使用中自我改进。FTS5 会话搜索与 LLM 摘要功能实现跨会话回忆。<a href="https://github.com/plastic-labs/honcho">Honcho</a> 辩证式用户建模。兼容 <a href="https://agentskills.io">agentskills.io</a> 开放标准。</td></tr>
<tr><td><b>定时自动化任务</b></td><td>内置 cron 调度器，可向任意平台投递消息。日报、夜间备份、每周审计——全部用自然语言描述，无人值守运行。</td></tr>
<tr><td><b>委派与并行执行</b></td><td>为并行工作流生成隔离的子智能体。编写通过 RPC 调用工具的 Python 脚本，将多步骤流水线压缩为零上下文成本的单轮操作。</td></tr>
<tr><td><b>随处运行，不仅限于你的笔记本</b></td><td>六种终端后端——本地、Docker、SSH、Daytona、Singularity 和 Modal。Daytona 和 Modal 提供无服务器持久化——你的智能体环境在空闲时休眠，按需唤醒，会话间几乎零成本。可在 5 美元的 VPS 或 GPU 集群上运行。</td></tr>
<tr><td><b>面向研究</b></td><td>批量轨迹生成、Atropos 强化学习环境、轨迹压缩，用于训练下一代工具调用模型。</td></tr>
</table>

---

## 快速安装

```bash
curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash
```

支持 Linux、macOS、WSL2 和通过 Termux 运行的 Android。安装程序会为你处理平台相关的设置。

> **Android / Termux：** 经过测试的手动安装路径记录在 [Termux 指南](https://hermes-agent.nousresearch.com/docs/getting-started/termux) 中。在 Termux 上，Hermes 安装的是精选的 `.[termux]` 额外依赖，因为完整的 `.[all]` 额外依赖目前会拉取与 Android 不兼容的语音依赖。
>
> **Windows：** 不支持原生 Windows。请安装 [WSL2](https://learn.microsoft.com/en-us/windows/wsl/install) 并运行上述命令。

安装完成后：

```bash
source ~/.bashrc    # 重新加载 shell（或：source ~/.zshrc）
hermes              # 开始聊天！
```

---

## 快速上手

```bash
hermes              # 交互式 CLI —— 开始对话
hermes model        # 选择你的 LLM 提供商和模型
hermes tools        # 配置启用哪些工具
hermes config set   # 设置单个配置值
hermes gateway      # 启动消息网关（Telegram、Discord 等）
hermes setup        # 运行完整的设置向导（一次性配置所有内容）
hermes claw migrate # 从 OpenClaw 迁移（如果你之前使用 OpenClaw）
hermes update       # 更新到最新版本
hermes doctor       # 诊断任何问题
```

📖 **[完整文档 →](https://hermes-agent.nousresearch.com/docs/)**

## CLI 与消息平台快速参考

Hermes 有两个入口：使用 `hermes` 启动终端 UI，或运行网关并通过 Telegram、Discord、Slack、WhatsApp、Signal 或电子邮件与它对话。进入对话后，许多斜杠命令在两种界面中通用。

| 操作 | CLI | 消息平台 |
|---------|-----|---------------------|
| 开始聊天 | `hermes` | 运行 `hermes gateway setup` + `hermes gateway start`，然后向机器人发消息 |
| 开始新对话 | `/new` 或 `/reset` | `/new` 或 `/reset` |
| 切换模型 | `/model [provider:model]` | `/model [provider:model]` |
| 设置个性 | `/personality [name]` | `/personality [name]` |
| 重试或撤销上一轮 | `/retry`、`/undo` | `/retry`、`/undo` |
| 压缩上下文 / 查看用量 | `/compress`、`/usage`、`/insights [--days N]` | `/compress`、`/usage`、`/insights [days]` |
| 浏览技能 | `/skills` 或 `/<skill-name>` | `/skills` 或 `/<skill-name>` |
| 中断当前工作 | `Ctrl+C` 或发送新消息 | `/stop` 或发送新消息 |
| 平台相关状态 | `/platforms` | `/status`、`/sethome` |

完整命令列表请参阅 [CLI 指南](https://hermes-agent.nousresearch.com/docs/user-guide/cli) 和 [消息网关指南](https://hermes-agent.nousresearch.com/docs/user-guide/messaging)。

---

## 文档

所有文档位于 **[hermes-agent.nousresearch.com/docs](https://hermes-agent.nousresearch.com/docs/)**：

| 章节 | 涵盖内容 |
|---------|---------------|
| [快速开始](https://hermes-agent.nousresearch.com/docs/getting-started/quickstart) | 安装 → 设置 → 2 分钟内开始第一次对话 |
| [CLI 使用](https://hermes-agent.nousresearch.com/docs/user-guide/cli) | 命令、快捷键、个性设置、会话管理 |
| [配置](https://hermes-agent.nousresearch.com/docs/user-guide/configuration) | 配置文件、提供商、模型、所有选项 |
| [消息网关](https://hermes-agent.nousresearch.com/docs/user-guide/messaging) | Telegram、Discord、Slack、WhatsApp、Signal、Home Assistant |
| [安全性](https://hermes-agent.nousresearch.com/docs/user-guide/security) | 命令审批、私信配对、容器隔离 |
| [工具与工具集](https://hermes-agent.nousresearch.com/docs/user-guide/features/tools) | 40+ 工具、工具集系统、终端后端 |
| [技能系统](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills) | 程序化记忆、技能中心、创建技能 |
| [记忆](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory) | 持久记忆、用户画像、最佳实践 |
| [MCP 集成](https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp) | 连接任意 MCP 服务器以扩展功能 |
| [Cron 调度](https://hermes-agent.nousresearch.com/docs/user-guide/features/cron) | 支持平台投递的定时任务 |
| [上下文文件](https://hermes-agent.nousresearch.com/docs/user-guide/features/context-files) | 影响每次对话的项目上下文 |
| [架构](https://hermes-agent.nousresearch.com/docs/developer-guide/architecture) | 项目结构、智能体循环、关键类 |
| [贡献指南](https://hermes-agent.nousresearch.com/docs/developer-guide/contributing) | 开发环境设置、PR 流程、代码风格 |
| [CLI 参考](https://hermes-agent.nousresearch.com/docs/reference/cli-commands) | 所有命令和标志 |
| [环境变量](https://hermes-agent.nousresearch.com/docs/reference/environment-variables) | 完整环境变量参考 |

---

## 从 OpenClaw 迁移

如果你之前使用 OpenClaw，Hermes 可以自动导入你的设置、记忆、技能和 API 密钥。

**首次设置时：** 设置向导（`hermes setup`）会自动检测 `~/.openclaw` 并在配置开始前提供迁移选项。

**安装后随时执行：**

```bash
hermes claw migrate              # 交互式迁移（完整预设）
hermes claw migrate --dry-run    # 预览将要迁移的内容
hermes claw migrate --preset user-data   # 迁移但不包含密钥
hermes claw migrate --overwrite  # 覆盖已存在的冲突项
```

导入的内容：
- **SOUL.md** — 角色文件
- **记忆** — MEMORY.md 和 USER.md 条目
- **技能** — 用户创建的技能 → `~/.hermes/skills/openclaw-imports/`
- **命令允许列表** — 审批模式
- **消息设置** — 平台配置、允许的用户、工作目录
- **API 密钥** — 白名单中的密钥（Telegram、OpenRouter、OpenAI、Anthropic、ElevenLabs）
- **TTS 资源** — 工作区音频文件
- **工作区指令** — AGENTS.md（使用 `--workspace-target`）

查看 `hermes claw migrate --help` 了解所有选项，或使用 `openclaw-migration` 技能进行交互式的智能体引导迁移，支持预览。

---

## 贡献

我们欢迎贡献！请参阅[贡献指南](https://hermes-agent.nousresearch.com/docs/developer-guide/contributing)了解开发环境设置、代码风格和 PR 流程。

贡献者快速入门：

```bash
git clone https://github.com/NousResearch/hermes-agent.git
cd hermes-agent
curl -LsSf https://astral.sh/uv/install.sh | sh
uv venv venv --python 3.11
source venv/bin/activate
uv pip install -e ".[all,dev]"
python -m pytest tests/ -q
```

> **强化学习训练（可选）：** 如需使用 RL/Tinker-Atropos 集成：
> ```bash
> git submodule update --init tinker-atropos
> uv pip install -e "./tinker-atropos"
> ```

---

## 社区

- 💬 [Discord](https://discord.gg/NousResearch)
- 📚 [技能中心](https://agentskills.io)
- 🐛 [问题反馈](https://github.com/NousResearch/hermes-agent/issues)
- 💡 [讨论区](https://github.com/NousResearch/hermes-agent/discussions)
- 🔌 [HermesClaw](https://github.com/AaronWong1999/hermesclaw) — 社区微信桥接：在同一个微信账号上运行 Hermes Agent 和 OpenClaw。

---

## 许可证

MIT — 参见 [LICENSE](LICENSE)。

由 [Nous Research](https://nousresearch.com) 构建。
