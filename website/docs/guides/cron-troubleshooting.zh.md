---
sidebar_position: 12
title: "Cron 故障排除"
description: "诊断和修复常见的 Hermes cron 问题 — 任务不触发、投递失败、技能加载错误和性能问题"
---

# Cron 故障排除

当 cron 任务行为不符预期时，按顺序检查以下内容。大多数问题属于四类之一：时间、投递、权限或技能加载。

---

## 任务不触发

### 检查 1：验证任务存在且处于活跃状态

```bash
hermes cron list
```

查找任务并确认其状态为 `[active]`（非 `[paused]` 或 `[completed]`）。如果显示 `[completed]`，可能重复次数已耗尽 — 编辑任务以重置。

### 检查 2：确认计划正确

格式错误的计划会静默默认为一次性或直接被拒绝。测试你的表达式：

| 你的表达式 | 应该解析为 |
|----------------|-------------------|
| `0 9 * * *` | 每天上午 9:00 |
| `0 9 * * 1` | 每周一上午 9:00 |
| `every 2h` | 从现在起每 2 小时 |
| `30m` | 从现在起 30 分钟 |
| `2025-06-01T09:00:00` | 2025 年 6 月 1 日上午 9:00 UTC |

如果任务触发一次后从列表中消失，说明是一次性计划（`30m`、`1d` 或 ISO 时间戳）— 这是预期行为。

### 检查 3：网关是否在运行？

Cron 任务由网关的后台定时线程触发，该线程每 60 秒跳动一次。普通的 CLI 聊天会话**不会**自动触发 cron 任务。

如果你期望任务自动触发，需要运行中的网关（`hermes gateway` 或 `hermes serve`）。对于一次性调试，可以用 `hermes cron tick` 手动触发跳动。

### 检查 4：检查系统时钟和时区

任务使用本地时区。如果你的机器时钟错误或时区与预期不同，任务会在错误的时间触发。验证：

```bash
date
hermes cron list   # 将 next_run 时间与本地时间对比
```

---

## 投递失败

### 检查 1：验证投递目标正确

投递目标区分大小写，需要正确配置相应平台。配置错误的目标会静默丢弃响应。

| 目标 | 需要 |
|--------|----------|
| `telegram` | `~/.hermes/.env` 中的 `TELEGRAM_BOT_TOKEN` |
| `discord` | `~/.hermes/.env` 中的 `DISCORD_BOT_TOKEN` |
| `slack` | `~/.hermes/.env` 中的 `SLACK_BOT_TOKEN` |
| `whatsapp` | 已配置 WhatsApp 网关 |
| `signal` | 已配置 Signal 网关 |
| `matrix` | 已配置 Matrix 主服务器 |
| `email` | `config.yaml` 中已配置 SMTP |
| `sms` | 已配置 SMS 提供商 |
| `local` | 对 `~/.hermes/cron/output/` 有写权限 |
| `origin` | 投递到创建任务的聊天 |

其他支持的平台包括 `mattermost`、`homeassistant`、`dingtalk`、`feishu`、`wecom`、`weixin`、`bluebubbles`、`qqbot` 和 `webhook`。你也可以用 `platform:chat_id` 语法指定特定聊天（例如 `telegram:-1001234567890`）。

如果投递失败，任务仍然运行 — 只是不会发送到任何地方。检查 `hermes cron list` 的更新 `last_error` 字段（如可用）。

### 检查 2：检查 `[SILENT]` 用法

如果你的 cron 任务没有产生输出或代理回复了 `[SILENT]`，投递会被抑制。这对监控任务是有意为之的 — 但确保你的提示没有意外地抑制所有内容。

一个说"如果没有变化就回复 [SILENT]"的提示也可能静默吞掉非空响应。检查你的条件逻辑。

### 检查 3：平台令牌权限

每个消息平台机器人需要特定权限才能接收消息。如果投递静默失败：

- **Telegram**：机器人必须是目标群组/频道的管理员
- **Discord**：机器人必须有权限在目标频道中发送消息
- **Slack**：机器人必须已添加到工作区并有 `chat:write` 范围

### 检查 4：响应包装

默认情况下，cron 响应会用头部和尾部包装（`config.yaml` 中的 `cron.wrap_response: true`）。某些平台或集成可能无法很好地处理这个。要禁用：

```yaml
cron:
  wrap_response: false
```

---

## 技能加载失败

### 检查 1：验证技能已安装

```bash
hermes skills list
```

技能必须在附加到 cron 任务之前安装。如果技能缺失，先用 `hermes skills install <skill-name>` 或在 CLI 中通过 `/skills` 安装。

### 检查 2：检查技能名称与技能文件夹名称

技能名称区分大小写，必须与已安装技能的文件夹名称匹配。如果你的任务指定了 `ai-funding-daily-report` 但技能文件夹是 `ai-funding-daily-report`，从 `hermes skills list` 确认确切名称。

### 检查 3：需要交互式工具的技能

Cron 任务运行时禁用了 `cronjob`、`messaging` 和 `clarify` 工具集。这防止了递归 cron 创建、直接消息发送（投递由调度器处理）和交互式提示。如果技能依赖这些工具集，它在 cron 上下文中不会工作。

检查技能文档以确认它在非交互式（无头）模式下工作。

### 检查 4：多技能排序

使用多个技能时，它们按顺序加载。如果技能 A 依赖技能 B 的上下文，确保 B 先加载：

```bash
/cron add "0 9 * * *" "..." --skill context-skill --skill target-skill
```

在这个例子中，`context-skill` 在 `target-skill` 之前加载。

---

## 任务错误和失败

### 检查 1：查看最近的任务输出

如果任务运行并失败了，你可能在以下位置看到错误上下文：

1. 任务投递的聊天（如果投递成功）
2. `~/.hermes/logs/agent.log` 中的调度器消息（或 `errors.log` 中的警告）
3. 通过 `hermes cron list` 查看任务的 `last_run` 元数据

### 检查 2：常见错误模式

**脚本的"No such file or directory"**
`script` 路径必须是绝对路径（或相对于 Hermes 配置目录）。验证：
```bash
ls ~/.hermes/scripts/your-script.py   # 必须存在
hermes cron edit <job_id> --script ~/.hermes/scripts/your-script.py
```

**任务执行时"Skill not found"**
技能必须安装在运行调度器的机器上。如果你在机器之间迁移，技能不会自动同步 — 使用 `hermes skills install <skill-name>` 重新安装。

**任务运行但不投递**
可能是投递目标问题（参见上面的投递失败）或静默抑制的响应（`[SILENT]`）。

**任务挂起或超时**
调度器使用基于不活跃的超时（默认 600 秒，可通过 `HERMES_CRON_TIMEOUT` 环境变量配置，`0` 表示无限）。只要代理在积极调用工具，它就可以一直运行 — 计时器仅在持续不活跃后触发。长时间运行的任务应使用脚本处理数据采集并只投递结果。

### 检查 3：锁竞争

调度器使用基于文件的锁来防止重叠的跳动。如果两个网关实例在运行（或 CLI 会话与网关冲突），任务可能被延迟或跳过。

终止重复的网关进程：
```bash
ps aux | grep hermes
# 终止重复进程，只保留一个
```

### 检查 4：jobs.json 权限

任务存储在 `~/.hermes/cron/jobs.json` 中。如果该文件对你的用户不可读/写，调度器会静默失败：

```bash
ls -la ~/.hermes/cron/jobs.json
chmod 600 ~/.hermes/cron/jobs.json   # 你的用户应拥有它
```

---

## 性能问题

### 任务启动慢

每个 cron 任务创建一个新的 AIAgent 会话，可能涉及提供商认证和模型加载。对于时间敏感的计划，添加缓冲时间（例如 `0 8 * * *` 而不是 `0 9 * * *`）。

### 太多重叠任务

调度器在每次跳动内按顺序执行任务。如果多个任务同时到期，它们会一个接一个运行。考虑错开计划（例如 `0 9 * * *` 和 `5 9 * * *` 而不是两个都在 `0 9 * * *`）以避免延迟。

### 大量脚本输出

输出数兆字节的脚本会拖慢代理并可能触及 token 限制。在脚本级别进行过滤/总结 — 只输出代理需要推理的内容。

---

## 诊断命令

```bash
hermes cron list                    # 显示所有任务、状态、next_run 时间
hermes cron run <job_id>            # 安排在下次跳动执行（用于测试）
hermes cron edit <job_id>           # 修复配置问题
hermes logs                         # 查看最近的 Hermes 日志
hermes skills list                  # 验证已安装的技能
```

---

## 获取更多帮助

如果你已经走完本指南但问题仍然存在：

1. 用 `hermes cron run <job_id>`（在下次网关跳动时触发）运行任务并观察聊天输出中的错误
2. 检查 `~/.hermes/logs/agent.log` 中的调度器消息和 `~/.hermes/logs/errors.log` 中的警告
3. 在 [github.com/NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) 提交 issue，包含：
   - 任务 ID 和计划
   - 投递目标
   - 你的预期 vs 实际发生的情况
   - 日志中的相关错误消息

---

*完整的 cron 参考请参阅[使用 Cron 自动化一切](/docs/guides/automate-with-cron)和[定时任务（Cron）](/docs/user-guide/features/cron)。*
