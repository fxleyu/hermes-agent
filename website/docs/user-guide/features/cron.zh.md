---
sidebar_position: 5
title: "定时任务（Cron）"
description: "使用自然语言安排自动化任务，通过单个 cron 工具管理它们，并附加一个或多个技能"
---

# 定时任务（Cron）

使用自然语言或 cron 表达式安排任务自动运行。Hermes 通过单个 `cronjob` 工具以操作方式暴露 cron 管理，而不是使用单独的 schedule/list/remove 工具。

## Cron 的能力

Cron 任务可以：

- 安排一次性或重复性任务
- 暂停、恢复、编辑、触发和删除任务
- 为任务附加零个、一个或多个技能
- 将结果投递回原始聊天、本地文件或配置的平台目标
- 在带有正常静态工具列表的全新代理会话中运行

:::warning
Cron 运行的会话不能递归创建更多的 cron 任务。Hermes 在 cron 执行内部禁用 cron 管理工具以防止失控的调度循环。
:::

## 创建定时任务

### 在聊天中使用 `/cron`

```bash
/cron add 30m "提醒我检查构建"
/cron add "every 2h" "检查服务器状态"
/cron add "every 1h" "总结新的 feed 项目" --skill blogwatcher
/cron add "every 1h" "使用两个技能并合并结果" --skill blogwatcher --skill find-nearby
```

### 从独立 CLI

```bash
hermes cron create "every 2h" "检查服务器状态"
hermes cron create "every 1h" "总结新的 feed 项目" --skill blogwatcher
hermes cron create "every 1h" "使用两个技能并合并结果" \
  --skill blogwatcher \
  --skill find-nearby \
  --name "技能组合"
```

### 通过自然对话

正常地向 Hermes 提问：

```text
每天早上 9 点，检查 Hacker News 的 AI 新闻并在 Telegram 上给我发一个摘要。
```

Hermes 将在内部使用统一的 `cronjob` 工具。

## 技能支持的 cron 任务

Cron 任务可以在运行提示之前加载一个或多个技能。

### 单个技能

```python
cronjob(
    action="create",
    skill="blogwatcher",
    prompt="检查配置的 feed 并总结任何新内容。",
    schedule="0 9 * * *",
    name="早间 feed",
)
```

### 多个技能

技能按顺序加载。提示成为叠加在这些技能之上的任务指令。

```python
cronjob(
    action="create",
    skills=["blogwatcher", "find-nearby"],
    prompt="查找新的本地事件和有趣的附近地点，然后将它们合并为一个简短的简报。",
    schedule="every 6h",
    name="本地简报",
)
```

当你想让定时代理继承可重用的工作流程而不将完整的技能文本塞入 cron 提示本身时，这很有用。

## 编辑任务

你不需要删除并重新创建任务来更改它们。

### 聊天

```bash
/cron edit <job_id> --schedule "every 4h"
/cron edit <job_id> --prompt "使用修改后的任务"
/cron edit <job_id> --skill blogwatcher --skill find-nearby
/cron edit <job_id> --remove-skill blogwatcher
/cron edit <job_id> --clear-skills
```

### 独立 CLI

```bash
hermes cron edit <job_id> --schedule "every 4h"
hermes cron edit <job_id> --prompt "使用修改后的任务"
hermes cron edit <job_id> --skill blogwatcher --skill find-nearby
hermes cron edit <job_id> --add-skill find-nearby
hermes cron edit <job_id> --remove-skill blogwatcher
hermes cron edit <job_id> --clear-skills
```

说明：

- 重复的 `--skill` 替换任务的附加技能列表
- `--add-skill` 追加到现有列表而不替换
- `--remove-skill` 删除特定的附加技能
- `--clear-skills` 删除所有附加技能

## 生命周期操作

Cron 任务现在有比仅创建/删除更完整的生命周期。

### 聊天

```bash
/cron list
/cron pause <job_id>
/cron resume <job_id>
/cron run <job_id>
/cron remove <job_id>
```

### 独立 CLI

```bash
hermes cron list
hermes cron pause <job_id>
hermes cron resume <job_id>
hermes cron run <job_id>
hermes cron remove <job_id>
hermes cron status
hermes cron tick
```

它们的作用：

- `pause` — 保留任务但停止调度
- `resume` — 重新启用任务并计算下次未来运行时间
- `run` — 在下一个调度器 tick 时触发任务
- `remove` — 完全删除

## 工作原理

**Cron 执行由网关守护进程处理。** 网关每 60 秒 tick 一次调度器，在隔离的代理会话中运行任何到期的任务。

```bash
hermes gateway install     # 作为用户服务安装
sudo hermes gateway install --system   # Linux：服务器的开机系统服务
hermes gateway             # 或前台运行

hermes cron list
hermes cron status
```

### 网关调度器行为

每次 tick 时 Hermes：

1. 从 `~/.hermes/cron/jobs.json` 加载任务
2. 检查 `next_run_at` 与当前时间的对比
3. 为每个到期任务启动一个新的 `AIAgent` 会话
4. 可选地将一个或多个附加技能注入该新会话
5. 运行提示直到完成
6. 投递最终响应
7. 更新运行元数据和下次计划时间

`~/.hermes/cron/.tick.lock` 上的文件锁防止重叠的调度器 tick 重复运行同一批任务。

## 投递选项

安排任务时，你指定输出去向：

| 选项 | 描述 | 示例 |
|--------|-------------|---------|
| `"origin"` | 回到创建任务的位置 | 消息平台上的默认值 |
| `"local"` | 仅保存到本地文件（`~/.hermes/cron/output/`） | CLI 上的默认值 |
| `"telegram"` | Telegram 主频道 | 使用 `TELEGRAM_HOME_CHANNEL` |
| `"telegram:123456"` | 按 ID 指定的 Telegram 聊天 | 直接投递 |
| `"telegram:-100123:17585"` | 指定的 Telegram 话题 | `chat_id:thread_id` 格式 |
| `"discord"` | Discord 主频道 | 使用 `DISCORD_HOME_CHANNEL` |
| `"discord:#engineering"` | 指定的 Discord 频道 | 按频道名称 |
| `"slack"` | Slack 主频道 | |
| `"whatsapp"` | WhatsApp 主页 | |
| `"signal"` | Signal | |
| `"matrix"` | Matrix 主房间 | |
| `"mattermost"` | Mattermost 主频道 | |
| `"email"` | 电子邮件 | |
| `"sms"` | 通过 Twilio 的短信 | |
| `"homeassistant"` | Home Assistant | |
| `"dingtalk"` | 钉钉 | |
| `"feishu"` | 飞书/Lark | |
| `"wecom"` | 企业微信 | |
| `"weixin"` | 微信 | |
| `"bluebubbles"` | BlueBubbles (iMessage) | |
| `"qqbot"` | QQ 机器人（腾讯 QQ） | |

代理的最终响应会自动投递。你不需要在 cron 提示中调用 `send_message`。

### 响应包装

默认情况下，投递的 cron 输出用标题和页脚包装，以便收件人知道它来自定时任务：

```
Cronjob Response: 早间 feed
-------------

<代理输出>

Note: The agent cannot see this message, and therefore cannot respond to it.
```

要投递不带包装的原始代理输出，将 `cron.wrap_response` 设置为 `false`：

```yaml
# ~/.hermes/config.yaml
cron:
  wrap_response: false
```

### 静默抑制

如果代理的最终响应以 `[SILENT]` 开头，投递将完全被抑制。输出仍然保存在本地以供审计（在 `~/.hermes/cron/output/` 中），但不会向投递目标发送消息。

这对于应该仅在出现问题时报告的监控任务很有用：

```text
检查 nginx 是否在运行。如果一切正常，仅回复 [SILENT]。
否则，报告问题。
```

失败的任务始终投递，不管 `[SILENT]` 标记 — 只有成功的运行可以被静默。

## 脚本超时

预运行脚本（通过 `script` 参数附加）的默认超时为 120 秒。如果你的脚本需要更长时间 — 例如，包含随机延迟以避免机器人式时间模式 — 你可以增加此值：

```yaml
# ~/.hermes/config.yaml
cron:
  script_timeout_seconds: 300   # 5 分钟
```

或设置 `HERMES_CRON_SCRIPT_TIMEOUT` 环境变量。解析顺序为：环境变量 → config.yaml → 120 秒默认值。

## 服务商恢复

Cron 任务继承你配置的备用服务商和凭据池轮换。如果主 API 密钥被限速或服务商返回错误，cron 代理可以：

- **回退到备用服务商** — 如果你在 `config.yaml` 中配置了 `fallback_providers`（或旧版 `fallback_model`）
- **轮换到**你的[凭据池](/docs/user-guide/configuration#credential-pool-strategies)中同一服务商的**下一个凭据**

这意味着高频率运行或在高峰时段运行的 cron 任务更具弹性 — 单个被限速的密钥不会导致整个运行失败。

## 调度格式

代理的最终响应会自动投递 — 你**不**需要在 cron 提示中包含 `send_message` 来投递到相同的目标。如果 cron 运行调用 `send_message` 到调度器已经要投递的确切目标，Hermes 会跳过该重复发送，并告诉模型将面向用户的内容放在最终响应中。仅对额外的或不同的目标使用 `send_message`。

### 相对延迟（一次性）

```text
30m     → 30 分钟后运行一次
2h      → 2 小时后运行一次
1d      → 1 天后运行一次
```

### 间隔（重复）

```text
every 30m    → 每 30 分钟
every 2h     → 每 2 小时
every 1d     → 每天
```

### Cron 表达式

```text
0 9 * * *       → 每天上午 9:00
0 9 * * 1-5     → 工作日上午 9:00
0 */6 * * *     → 每 6 小时
30 8 1 * *      → 每月 1 日上午 8:30
0 0 * * 0       → 每周日午夜
```

### ISO 时间戳

```text
2026-03-15T09:00:00    → 2026 年 3 月 15 日上午 9:00 一次性
```

## 重复行为

| 调度类型 | 默认重复 | 行为 |
|--------------|----------------|----------|
| 一次性（`30m`、时间戳） | 1 | 运行一次 |
| 间隔（`every 2h`） | 永久 | 直到删除 |
| Cron 表达式 | 永久 | 直到删除 |

你可以覆盖：

```python
cronjob(
    action="create",
    prompt="...",
    schedule="every 2h",
    repeat=5,
)
```

## 以编程方式管理任务

面向代理的 API 是一个工具：

```python
cronjob(action="create", ...)
cronjob(action="list")
cronjob(action="update", job_id="...")
cronjob(action="pause", job_id="...")
cronjob(action="resume", job_id="...")
cronjob(action="run", job_id="...")
cronjob(action="remove", job_id="...")
```

对于 `update`，传递 `skills=[]` 以删除所有附加技能。

## 任务存储

任务存储在 `~/.hermes/cron/jobs.json`。任务运行的输出保存到 `~/.hermes/cron/output/{job_id}/{timestamp}.md`。

存储使用原子文件写入，因此中断的写入不会留下部分写入的任务文件。

## 自包含的提示仍然很重要

:::warning 重要
Cron 任务在完全全新的代理会话中运行。提示必须包含代理需要的所有内容，除了附加技能已提供的内容。
:::

**差的：** `"检查那个服务器问题"`

**好的：** `"SSH 到服务器 192.168.1.100，用户名 'deploy'，用 'systemctl status nginx' 检查 nginx 是否在运行，并验证 https://example.com 返回 HTTP 200。"`

## 安全

定时任务提示在创建和更新时会被扫描提示注入和凭据泄露模式。包含不可见 Unicode 技巧、SSH 后门尝试或明显密钥泄露载荷的提示会被阻止。
