---
sidebar_position: 4
title: "教程：团队 Telegram 助手"
description: "逐步指南，设置一个你整个团队都可以使用的 Telegram 机器人，用于代码帮助、研究、系统管理等"
---

# 设置团队 Telegram 助手

本教程将引导你设置一个由 Hermes Agent 驱动的 Telegram 机器人，多个团队成员可以使用。完成后，你的团队将拥有一个共享的 AI 助手，可以发消息获取代码、研究、系统管理等方面的帮助 — 通过每用户授权来保障安全。

## 我们要构建什么

一个 Telegram 机器人，它：

- **任何授权的团队成员**都可以私信获取帮助 — 代码审查、研究、shell 命令、调试
- **在你的服务器上运行**，拥有完整的工具访问权 — 终端、文件编辑、网络搜索、代码执行
- **每用户会话** — 每个人都有自己的对话上下文
- **默认安全** — 只有批准的用户可以交互，有两种授权方式
- **定时任务** — 每日站会、健康检查和提醒投递到团队频道

---

## 前提条件

开始前，确保你有：

- **Hermes Agent 已安装**在服务器或 VPS 上（不是你的笔记本电脑 — 机器人需要持续运行）。如果还没有，请按照[安装指南](/docs/getting-started/installation)操作。
- **一个 Telegram 账号**（机器人所有者）
- **已配置的 LLM 提供商** — 至少在 `~/.hermes/.env` 中有 OpenAI、Anthropic 或其他支持的提供商的 API 密钥

:::tip
$5/月的 VPS 就足够运行网关了。Hermes 本身很轻量 — LLM API 调用才是花钱的地方，而且它们是远程发生的。
:::

---

## 步骤 1：创建 Telegram 机器人

每个 Telegram 机器人都从 **@BotFather** 开始 — Telegram 官方的创建机器人的机器人。

1. **打开 Telegram** 搜索 `@BotFather`，或访问 [t.me/BotFather](https://t.me/BotFather)

2. **发送 `/newbot`** — BotFather 会问你两件事：
   - **显示名称** — 用户看到的名字（例如 `Team Hermes Assistant`）
   - **用户名** — 必须以 `bot` 结尾（例如 `myteam_hermes_bot`）

3. **复制机器人令牌** — BotFather 会回复类似：
   ```
   Use this token to access the HTTP API:
   7123456789:AAH1bGciOiJSUzI1NiIsInR5cCI6Ikp...
   ```
   保存这个令牌 — 下一步需要它。

4. **设置描述**（可选但推荐）：
   ```
   /setdescription
   ```
   选择你的机器人，然后输入类似：
   ```
   Team AI assistant powered by Hermes Agent. DM me for help with code, research, debugging, and more.
   ```

5. **设置机器人命令**（可选 — 为用户提供命令菜单）：
   ```
   /setcommands
   ```
   选择你的机器人，然后粘贴：
   ```
   new - Start a fresh conversation
   model - Show or change the AI model
   status - Show session info
   help - Show available commands
   stop - Stop the current task
   ```

:::warning
保密你的机器人令牌。任何拥有令牌的人都可以控制机器人。如果泄露了，在 BotFather 中使用 `/revoke` 生成新的。
:::

---

## 步骤 2：配置网关

你有两个选择：交互式设置向导（推荐）或手动配置。

### 选项 A：交互式设置（推荐）

```bash
hermes gateway setup
```

这会通过方向键选择引导你完成所有步骤。选择 **Telegram**，粘贴你的机器人令牌，提示时输入你的用户 ID。

### 选项 B：手动配置

在 `~/.hermes/.env` 中添加这些行：

```bash
# 来自 BotFather 的 Telegram 机器人令牌
TELEGRAM_BOT_TOKEN=7123456789:AAH1bGciOiJSUzI1NiIsInR5cCI6Ikp...

# 你的 Telegram 用户 ID（数字）
TELEGRAM_ALLOWED_USERS=123456789
```

### 查找你的用户 ID

你的 Telegram 用户 ID 是一个数字值（不是你的用户名）。要找到它：

1. 在 Telegram 上给 [@userinfobot](https://t.me/userinfobot) 发消息
2. 它会立即回复你的数字用户 ID
3. 将那个数字复制到 `TELEGRAM_ALLOWED_USERS`

:::info
Telegram 用户 ID 是永久数字如 `123456789`。它们与你可以更改的 `@username` 不同。始终使用数字 ID 作为允许列表。
:::

---

## 步骤 3：启动网关

### 快速测试

首先在前台运行网关以确保一切正常：

```bash
hermes gateway
```

你应该看到类似这样的输出：

```
[Gateway] Starting Hermes Gateway...
[Gateway] Telegram adapter connected
[Gateway] Cron scheduler started (tick every 60s)
```

打开 Telegram，找到你的机器人，给它发一条消息。如果它回复了，说明一切正常。按 `Ctrl+C` 停止。

### 生产环境：安装为服务

对于能在重启后存活的持久部署：

```bash
hermes gateway install
sudo hermes gateway install --system   # 仅 Linux：开机时启动的系统服务
```

这创建了一个后台服务：Linux 上默认是用户级 **systemd** 服务，macOS 上是 **launchd** 服务，如果传递 `--system` 则是 Linux 开机启动的系统服务。

```bash
# Linux — 管理默认用户服务
hermes gateway start
hermes gateway stop
hermes gateway status

# 查看实时日志
journalctl --user -u hermes-gateway -f

# SSH 登出后继续运行
sudo loginctl enable-linger $USER

# Linux 服务器 — 显式系统服务命令
sudo hermes gateway start --system
sudo hermes gateway status --system
journalctl -u hermes-gateway -f
```

```bash
# macOS — 管理服务
hermes gateway start
hermes gateway stop
tail -f ~/.hermes/logs/gateway.log
```

:::tip macOS PATH
launchd plist 在安装时捕获你的 shell PATH，以便网关子进程可以找到 Node.js 和 ffmpeg 等工具。如果你之后安装了新工具，重新运行 `hermes gateway install` 来更新 plist。
:::

### 验证它在运行

```bash
hermes gateway status
```

然后在 Telegram 上给你的机器人发送测试消息。你应该在几秒内收到回复。

---

## 步骤 4：设置团队访问

现在让我们给你的队友开通访问。有两种方法。

### 方法 A：静态允许列表

收集每个团队成员的 Telegram 用户 ID（让他们给 [@userinfobot](https://t.me/userinfobot) 发消息）并以逗号分隔的列表添加：

```bash
# 在 ~/.hermes/.env 中
TELEGRAM_ALLOWED_USERS=123456789,987654321,555555555
```

更改后重启网关：

```bash
hermes gateway stop && hermes gateway start
```

### 方法 B：DM 配对（团队推荐）

DM 配对更灵活 — 你不需要预先收集用户 ID。工作原理：

1. **队友私信机器人** — 由于他们不在允许列表上，机器人回复一个一次性配对码：
   ```
   Pairing code: XKGH5N7P
   Send this code to the bot owner for approval.
   ```

2. **队友把代码发给你**（通过任何渠道 — Slack、邮件、当面）

3. **你在服务器上批准**：
   ```bash
   hermes pairing approve telegram XKGH5N7P
   ```

4. **他们就进去了** — 机器人立即开始回复他们的消息

**管理配对用户：**

```bash
# 查看所有待定和已批准的用户
hermes pairing list

# 撤销某人的访问
hermes pairing revoke telegram 987654321

# 清除过期的待定代码
hermes pairing clear-pending
```

:::tip
DM 配对非常适合团队，因为添加新用户时不需要重启网关。批准立即生效。
:::

### 安全注意事项

- **永远不要在有终端访问权限的机器人上设置 `GATEWAY_ALLOW_ALL_USERS=true`** — 任何找到你机器人的人都可以在你的服务器上运行命令
- 配对码在 **1 小时**后过期，使用密码学随机性
- 速率限制防止暴力攻击：每用户每 10 分钟 1 次请求，每平台最多 3 个待定代码
- 5 次失败的批准尝试后，平台进入 1 小时锁定
- 所有配对数据以 `chmod 0600` 权限存储

---

## 步骤 5：配置机器人

### 设置主频道

**主频道**是机器人投递 cron 任务结果和主动消息的地方。没有它，定时任务无处发送输出。

**选项 1：** 在机器人所在的任何 Telegram 群组或聊天中使用 `/sethome` 命令。

**选项 2：** 在 `~/.hermes/.env` 中手动设置：

```bash
TELEGRAM_HOME_CHANNEL=-1001234567890
TELEGRAM_HOME_CHANNEL_NAME="Team Updates"
```

要查找频道 ID，将 [@userinfobot](https://t.me/userinfobot) 添加到群组 — 它会报告群组的聊天 ID。

### 配置工具进度显示

控制机器人使用工具时显示多少细节。在 `~/.hermes/config.yaml` 中：

```yaml
display:
  tool_progress: new    # off | new | all | verbose
```

| 模式 | 你看到的 |
|------|-------------|
| `off` | 仅干净的回复 — 没有工具活动 |
| `new` | 每个新工具调用的简短状态（消息平台推荐） |
| `all` | 所有工具活动包含详情 |
| `verbose` | 完整工具输出包括命令结果 |

用户也可以在聊天中使用 `/verbose` 命令按会话更改。

### 使用 SOUL.md 设置个性

通过编辑 `~/.hermes/SOUL.md` 自定义机器人的沟通方式：

完整指南请参阅[使用 SOUL.md 与 Hermes](/docs/guides/use-soul-with-hermes)。

```markdown
# Soul
You are a helpful team assistant. Be concise and technical.
Use code blocks for any code. Skip pleasantries — the team
values directness. When debugging, always ask for error logs
before guessing at solutions.
```

### 添加项目上下文

如果你的团队在特定项目上工作，创建上下文文件让机器人了解你的技术栈：

```markdown
<!-- ~/.hermes/AGENTS.md -->
# Team Context
- We use Python 3.12 with FastAPI and SQLAlchemy
- Frontend is React with TypeScript
- CI/CD runs on GitHub Actions
- Production deploys to AWS ECS
- Always suggest writing tests for new code
```

:::info
上下文文件注入到每个会话的系统提示中。保持简洁 — 每个字符都计入你的 token 预算。
:::

---

## 步骤 6：设置定时任务

网关运行后，你可以安排将结果投递到团队频道的重复任务。

### 每日站会摘要

在 Telegram 上给机器人发消息：

```
Every weekday at 9am, check the GitHub repository at
github.com/myorg/myproject for:
1. Pull requests opened/merged in the last 24 hours
2. Issues created or closed
3. Any CI/CD failures on the main branch
Format as a brief standup-style summary.
```

代理会自动创建 cron 任务，并将结果投递到你发送请求的聊天（或主频道）。

### 服务器健康检查

```
Every 6 hours, check disk usage with 'df -h', memory with 'free -h',
and Docker container status with 'docker ps'. Report anything unusual —
partitions above 80%, containers that have restarted, or high memory usage.
```

### 管理定时任务

```bash
# 从 CLI
hermes cron list          # 查看所有定时任务
hermes cron status        # 检查调度器是否在运行

# 从 Telegram 聊天
/cron list                # 查看任务
/cron remove <job_id>     # 移除任务
```

:::warning
Cron 任务提示在完全全新的会话中运行，没有先前对话的记忆。确保每个提示包含代理所需的**所有**上下文 — 文件路径、URL、服务器地址和清晰的指令。
:::

---

## 生产环境技巧

### 使用 Docker 确保安全

在共享团队机器人上，使用 Docker 作为终端后端，这样代理命令在容器中运行而不是在你的主机上：

```bash
# 在 ~/.hermes/.env 中
TERMINAL_BACKEND=docker
TERMINAL_DOCKER_IMAGE=nikolaik/python-nodejs:python3.11-nodejs20
```

或在 `~/.hermes/config.yaml` 中：

```yaml
terminal:
  backend: docker
  container_cpu: 1
  container_memory: 5120
  container_persistent: true
```

这样，即使有人让机器人运行破坏性操作，你的主机系统也是受保护的。

### 监控网关

```bash
# 检查网关是否在运行
hermes gateway status

# 查看实时日志（Linux）
journalctl --user -u hermes-gateway -f

# 查看实时日志（macOS）
tail -f ~/.hermes/logs/gateway.log
```

### 保持 Hermes 更新

从 Telegram 发送 `/update` 给机器人 — 它会拉取最新版本并重启。或从服务器：

```bash
hermes update
hermes gateway stop && hermes gateway start
```

### 日志位置

| 内容 | 位置 |
|------|----------|
| 网关日志 | `journalctl --user -u hermes-gateway`（Linux）或 `~/.hermes/logs/gateway.log`（macOS） |
| Cron 任务输出 | `~/.hermes/cron/output/{job_id}/{timestamp}.md` |
| Cron 任务定义 | `~/.hermes/cron/jobs.json` |
| 配对数据 | `~/.hermes/pairing/` |
| 会话历史 | `~/.hermes/sessions/` |

---

## 更进一步

你已经有了一个可工作的团队 Telegram 助手。以下是一些后续步骤：

- **[安全指南](/docs/user-guide/security)** — 深入了解授权、容器隔离和命令批准
- **[消息网关](/docs/user-guide/messaging)** — 网关架构、会话管理和聊天命令的完整参考
- **[Telegram 设置](/docs/user-guide/messaging/telegram)** — 平台特定细节包括语音消息和 TTS
- **[定时任务](/docs/user-guide/features/cron)** — 高级 cron 调度，包括投递选项和 cron 表达式
- **[上下文文件](/docs/user-guide/features/context-files)** — AGENTS.md、SOUL.md 和 .cursorrules 用于项目知识
- **[个性](/docs/user-guide/features/personality)** — 内置个性预设和自定义角色定义
- **添加更多平台** — 同一个网关可以同时运行 [Discord](/docs/user-guide/messaging/discord)、[Slack](/docs/user-guide/messaging/slack) 和 [WhatsApp](/docs/user-guide/messaging/whatsapp)

---

*有问题？在 GitHub 上提交 issue — 欢迎社区贡献。*
