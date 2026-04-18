---
sidebar_position: 1
title: "消息网关"
description: "通过 Telegram、Discord、Slack、WhatsApp、Signal、SMS、Email、Home Assistant、Mattermost、Matrix、钉钉、Webhooks 或任何 OpenAI 兼容前端与 Hermes 聊天——架构与设置概览"
---

# 消息网关

通过 Telegram、Discord、Slack、WhatsApp、Signal、SMS、Email、Home Assistant、Mattermost、Matrix、钉钉、飞书/Lark、企业微信、微信、BlueBubbles (iMessage)、QQ 或浏览器与 Hermes 聊天。网关是一个单一的后台进程，连接到所有已配置的平台，处理会话，运行定时任务，并传递语音消息。

有关完整的语音功能集——包括 CLI 麦克风模式、消息中的语音回复和 Discord 语音频道对话——请参阅[语音模式](/docs/user-guide/features/voice-mode)和[在 Hermes 中使用语音模式](/docs/guides/use-voice-mode-with-hermes)。

## 平台对比

| 平台 | 语音 | 图片 | 文件 | 话题 | 表情回复 | 输入指示 | 流式传输 |
|----------|:-----:|:------:|:-----:|:-------:|:---------:|:------:|:---------:|
| Telegram | ✅ | ✅ | ✅ | ✅ | — | ✅ | ✅ |
| Discord | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Slack | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| WhatsApp | — | ✅ | ✅ | — | — | ✅ | ✅ |
| Signal | — | ✅ | ✅ | — | — | ✅ | ✅ |
| SMS | — | — | — | — | — | — | — |
| Email | — | ✅ | ✅ | ✅ | — | — | — |
| Home Assistant | — | — | — | — | — | — | — |
| Mattermost | ✅ | ✅ | ✅ | ✅ | — | ✅ | ✅ |
| Matrix | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 钉钉 | — | — | — | — | — | ✅ | ✅ |
| 飞书/Lark | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 企业微信 | ✅ | ✅ | ✅ | — | — | ✅ | ✅ |
| 企业微信回调 | — | — | — | — | — | — | — |
| 微信 | ✅ | ✅ | ✅ | — | — | ✅ | ✅ |
| BlueBubbles | — | ✅ | ✅ | — | ✅ | ✅ | — |
| QQ | ✅ | ✅ | ✅ | — | — | ✅ | — |

**语音** = TTS 音频回复和/或语音消息转录。**图片** = 收发图片。**文件** = 收发文件附件。**话题** = 话题式对话。**表情回复** = 消息表情回复。**输入指示** = 处理时的输入指示器。**流式传输** = 通过编辑实现的渐进式消息更新。

## 架构

```mermaid
flowchart TB
    subgraph Gateway["Hermes 网关"]
        subgraph Adapters["平台适配器"]
            tg[Telegram]
            dc[Discord]
            wa[WhatsApp]
            sl[Slack]
            sig[Signal]
            sms[SMS]
            em[Email]
            ha[Home Assistant]
            mm[Mattermost]
            mx[Matrix]
            dt[钉钉]
    fs[飞书/Lark]
    wc[企业微信]
    wcb[企业微信回调]
    wx[微信]
    bb[BlueBubbles]
    qq[QQ]
            api["API 服务器<br/>(OpenAI 兼容)"]
            wh[Webhooks]
        end

        store["会话存储<br/>按聊天"]
        agent["AIAgent<br/>run_agent.py"]
        cron["定时调度器<br/>每 60 秒触发"]
    end

    tg --> store
    dc --> store
    wa --> store
    sl --> store
    sig --> store
    sms --> store
    em --> store
    ha --> store
    mm --> store
    mx --> store
    dt --> store
    fs --> store
    wc --> store
    wcb --> store
    wx --> store
    bb --> store
    qq --> store
    api --> store
    wh --> store
    store --> agent
    cron --> store
```

每个平台适配器接收消息，通过按聊天的会话存储路由它们，并将它们分发给 AIAgent 进行处理。网关还运行定时调度器，每 60 秒触发一次以执行任何到期的任务。

## 快速设置

配置消息平台最简单的方式是交互式向导：

```bash
hermes gateway setup        # 所有消息平台的交互式设置
```

它会引导你配置每个平台（使用方向键选择），显示哪些平台已配置，并在完成后提供启动/重启网关的选项。

## 网关命令

```bash
hermes gateway              # 前台运行
hermes gateway setup        # 交互式配置消息平台
hermes gateway install      # 安装为用户服务 (Linux) / launchd 服务 (macOS)
sudo hermes gateway install --system   # 仅限 Linux：安装开机启动系统服务
hermes gateway start        # 启动默认服务
hermes gateway stop         # 停止默认服务
hermes gateway status       # 检查默认服务状态
hermes gateway status --system         # 仅限 Linux：显式检查系统服务
```

## 聊天命令（消息中使用）

| 命令 | 说明 |
|---------|-------------|
| `/new` 或 `/reset` | 开始新对话 |
| `/model [provider:model]` | 显示或更改模型（支持 `provider:model` 语法） |
| `/provider` | 显示可用提供商及认证状态 |
| `/personality [name]` | 设置人格 |
| `/retry` | 重试上一条消息 |
| `/undo` | 撤销上一次交互 |
| `/status` | 显示会话信息 |
| `/stop` | 停止正在运行的代理 |
| `/approve` | 批准待处理的危险命令 |
| `/deny` | 拒绝待处理的危险命令 |
| `/sethome` | 将此聊天设为主频道 |
| `/compress` | 手动压缩对话上下文 |
| `/title [name]` | 设置或显示会话标题 |
| `/resume [name]` | 恢复先前命名的会话 |
| `/usage` | 显示本会话的 token 使用量 |
| `/insights [days]` | 显示使用洞察和分析 |
| `/reasoning [level\|show\|hide]` | 更改推理力度或切换推理显示 |
| `/voice [on\|off\|tts\|join\|leave\|status]` | 控制消息语音回复和 Discord 语音频道行为 |
| `/rollback [number]` | 列出或恢复文件系统检查点 |
| `/background <prompt>` | 在单独的后台会话中运行提示 |
| `/reload-mcp` | 从配置重新加载 MCP 服务器 |
| `/update` | 将 Hermes Agent 更新到最新版本 |
| `/help` | 显示可用命令 |
| `/<skill-name>` | 调用任何已安装的技能 |

## 会话管理

### 会话持久化

会话在消息之间持久化，直到重置。代理会记住你的对话上下文。

### 重置策略

会话根据可配置的策略重置：

| 策略 | 默认值 | 说明 |
|--------|---------|-------------|
| 每日 | 凌晨 4:00 | 每天在指定时间重置 |
| 空闲 | 1440 分钟 | 空闲 N 分钟后重置 |
| 两者 | （组合） | 先触发的为准 |

在 `~/.hermes/gateway.json` 中配置每平台覆盖：

```json
{
  "reset_by_platform": {
    "telegram": { "mode": "idle", "idle_minutes": 240 },
    "discord": { "mode": "idle", "idle_minutes": 60 }
  }
}
```

## 安全性

**默认情况下，网关拒绝所有不在白名单中或未通过 DM 配对的用户。** 这是具有终端访问权限的机器人的安全默认设置。

```bash
# 限制为特定用户（推荐）：
TELEGRAM_ALLOWED_USERS=123456789,987654321
DISCORD_ALLOWED_USERS=123456789012345678
SIGNAL_ALLOWED_USERS=+155****4567,+155****6543
SMS_ALLOWED_USERS=+155****4567,+155****6543
EMAIL_ALLOWED_USERS=trusted@example.com,colleague@work.com
MATTERMOST_ALLOWED_USERS=3uo8dkh1p7g1mfk49ear5fzs5c
MATRIX_ALLOWED_USERS=@alice:matrix.org
DINGTALK_ALLOWED_USERS=user-id-1
FEISHU_ALLOWED_USERS=ou_xxxxxxxx,ou_yyyyyyyy
WECOM_ALLOWED_USERS=user-id-1,user-id-2
WECOM_CALLBACK_ALLOWED_USERS=user-id-1,user-id-2

# 或允许
GATEWAY_ALLOWED_USERS=123456789,987654321

# 或显式允许所有用户（不推荐用于有终端访问权限的机器人）：
GATEWAY_ALLOW_ALL_USERS=true
```

### DM 配对（白名单的替代方案）

无需手动配置用户 ID，未知用户在 DM 机器人时会收到一次性配对码：

```bash
# 用户看到："Pairing code: XKGH5N7P"
# 你通过以下命令批准他们：
hermes pairing approve telegram XKGH5N7P

# 其他配对命令：
hermes pairing list          # 查看待处理和已批准的用户
hermes pairing revoke telegram 123456789  # 撤销访问权限
```

配对码在 1 小时后过期，有速率限制，并使用加密随机数。

## 中断代理

在代理工作时发送任何消息即可中断它。关键行为：

- **正在进行的终端命令会被立即终止**（先 SIGTERM，1 秒后 SIGKILL）
- **工具调用会被取消**——只有当前正在执行的会运行，其余的会被跳过
- **多条消息会被合并**——在中断期间发送的消息会合并为一个提示
- **`/stop` 命令**——中断但不排队后续消息

## 工具进度通知

在 `~/.hermes/config.yaml` 中控制显示多少工具活动：

```yaml
display:
  tool_progress: all    # off | new | all | verbose
  tool_progress_command: false  # 设置为 true 以在消息中启用 /verbose
```

启用后，机器人在工作时会发送状态消息：

```text
💻 `ls -la`...
🔍 web_search...
📄 web_extract...
🐍 execute_code...
```

## 后台会话

在单独的后台会话中运行提示，这样代理可以独立处理它，而你的主聊天保持响应：

```
/background Check all servers in the cluster and report any that are down
```

Hermes 立即确认：

```
🔄 后台任务已启动："Check all servers in the cluster..."
   任务 ID：bg_143022_a1b2c3
```

### 工作原理

每个 `/background` 提示会生成一个**独立的代理实例**，异步运行：

- **隔离会话**——后台代理有自己的会话和对话历史。它不了解你当前的聊天上下文，只接收你提供的提示。
- **相同配置**——继承当前网关设置中的模型、提供商、工具集、推理设置和提供商路由。
- **非阻塞**——你的主聊天保持完全交互。在它工作时可以发送消息、运行其他命令或启动更多后台任务。
- **结果投递**——当任务完成时，结果会发送回**你发出命令的同一聊天或频道**，前缀为 "✅ 后台任务完成"。如果失败，你会看到 "❌ 后台任务失败" 及错误信息。

### 后台进程通知

当运行后台会话的代理使用 `terminal(background=true)` 启动长时间运行的进程（服务器、构建等）时，网关可以将状态更新推送到你的聊天。在 `~/.hermes/config.yaml` 中通过 `display.background_process_notifications` 控制：

```yaml
display:
  background_process_notifications: all    # all | result | error | off
```

| 模式 | 你收到的内容 |
|------|-----------------|
| `all` | 运行输出更新**和**最终完成消息（默认） |
| `result` | 仅最终完成消息（无论退出码） |
| `error` | 仅在退出码非零时的最终消息 |
| `off` | 完全没有进程监视消息 |

你也可以通过环境变量设置：

```bash
HERMES_BACKGROUND_NOTIFICATIONS=result
```

### 使用场景

- **服务器监控**——"/background Check the health of all services and alert me if anything is down"
- **长时间构建**——"/background Build and deploy the staging environment"，同时继续聊天
- **研究任务**——"/background Research competitor pricing and summarize in a table"
- **文件操作**——"/background Organize the photos in ~/Downloads by date into folders"

:::tip
消息平台上的后台任务是即发即忘——你不需要等待或检查它们。任务完成时结果会自动到达同一聊天。
:::

## 服务管理

### Linux (systemd)

```bash
hermes gateway install               # 安装为用户服务
hermes gateway start                 # 启动服务
hermes gateway stop                  # 停止服务
hermes gateway status                # 检查状态
journalctl --user -u hermes-gateway -f  # 查看日志

# 启用 lingering（注销后保持运行）
sudo loginctl enable-linger $USER

# 或安装一个仍以你的用户运行的开机启动系统服务
sudo hermes gateway install --system
sudo hermes gateway start --system
sudo hermes gateway status --system
journalctl -u hermes-gateway -f
```

在笔记本电脑和开发机上使用用户服务。在 VPS 或无头主机上使用系统服务，这些主机应在开机时启动而不依赖 systemd linger。

避免同时安装用户和系统网关单元，除非你确实需要。Hermes 会在检测到两者同时存在时发出警告，因为 start/stop/status 行为会变得模糊。

:::info 多个安装
如果你在同一台机器上运行多个 Hermes 安装（使用不同的 `HERMES_HOME` 目录），每个都有自己的 systemd 服务名。默认的 `~/.hermes` 使用 `hermes-gateway`；其他安装使用 `hermes-gateway-<hash>`。`hermes gateway` 命令会自动定位当前 `HERMES_HOME` 对应的正确服务。
:::

### macOS (launchd)

```bash
hermes gateway install               # 安装为 launchd 代理
hermes gateway start                 # 启动服务
hermes gateway stop                  # 停止服务
hermes gateway status                # 检查状态
tail -f ~/.hermes/logs/gateway.log   # 查看日志
```

生成的 plist 位于 `~/Library/LaunchAgents/ai.hermes.gateway.plist`。它包含三个环境变量：

- **PATH**——安装时你的完整 shell PATH，前面加上 venv 的 `bin/` 和 `node_modules/.bin`。这确保用户安装的工具（Node.js、ffmpeg 等）对网关子进程可用，如 WhatsApp 桥接。
- **VIRTUAL_ENV**——指向 Python 虚拟环境，以便工具能正确解析包。
- **HERMES_HOME**——将网关限定到你的 Hermes 安装。

:::tip 安装后 PATH 变更
launchd plist 是静态的——如果你在设置网关后安装了新工具（例如通过 nvm 安装新的 Node.js 版本，或通过 Homebrew 安装 ffmpeg），再次运行 `hermes gateway install` 以捕获更新后的 PATH。网关会检测到过时的 plist 并自动重新加载。
:::

:::info 多个安装
与 Linux systemd 服务一样，每个 `HERMES_HOME` 目录有自己的 launchd 标签。默认的 `~/.hermes` 使用 `ai.hermes.gateway`；其他安装使用 `ai.hermes.gateway-<suffix>`。
:::

## 平台特定工具集

每个平台有自己的工具集：

| 平台 | 工具集 | 能力 |
|----------|---------|--------------|
| CLI | `hermes-cli` | 完全访问 |
| Telegram | `hermes-telegram` | 包括终端的完整工具 |
| Discord | `hermes-discord` | 包括终端的完整工具 |
| WhatsApp | `hermes-whatsapp` | 包括终端的完整工具 |
| Slack | `hermes-slack` | 包括终端的完整工具 |
| Signal | `hermes-signal` | 包括终端的完整工具 |
| SMS | `hermes-sms` | 包括终端的完整工具 |
| Email | `hermes-email` | 包括终端的完整工具 |
| Home Assistant | `hermes-homeassistant` | 完整工具 + HA 设备控制 (ha_list_entities, ha_get_state, ha_call_service, ha_list_services) |
| Mattermost | `hermes-mattermost` | 包括终端的完整工具 |
| Matrix | `hermes-matrix` | 包括终端的完整工具 |
| 钉钉 | `hermes-dingtalk` | 包括终端的完整工具 |
| 飞书/Lark | `hermes-feishu` | 包括终端的完整工具 |
| 企业微信 | `hermes-wecom` | 包括终端的完整工具 |
| 企业微信回调 | `hermes-wecom-callback` | 包括终端的完整工具 |
| 微信 | `hermes-weixin` | 包括终端的完整工具 |
| BlueBubbles | `hermes-bluebubbles` | 包括终端的完整工具 |
| QQBot | `hermes-qqbot` | 包括终端的完整工具 |
| API 服务器 | `hermes`（默认） | 包括终端的完整工具 |
| Webhooks | `hermes-webhook` | 包括终端的完整工具 |

## 后续步骤

- [Telegram 设置](telegram.md)
- [Discord 设置](discord.md)
- [Slack 设置](slack.md)
- [WhatsApp 设置](whatsapp.md)
- [Signal 设置](signal.md)
- [SMS 设置 (Twilio)](sms.md)
- [Email 设置](email.md)
- [Home Assistant 集成](homeassistant.md)
- [Mattermost 设置](mattermost.md)
- [Matrix 设置](matrix.md)
- [钉钉设置](dingtalk.md)
- [飞书/Lark 设置](feishu.md)
- [企业微信设置](wecom.md)
- [企业微信回调设置](wecom-callback.md)
- [微信设置](weixin.md)
- [BlueBubbles 设置 (iMessage)](bluebubbles.md)
- [QQBot 设置](qqbot.md)
- [Open WebUI + API 服务器](open-webui.md)
- [Webhooks](webhooks.md)
