---
sidebar_position: 11
title: "Cron 内部机制"
description: "Hermes 如何存储、调度、编辑、暂停、加载技能和投递 cron 任务"
---

# Cron 内部机制

Cron 子系统提供计划任务执行 — 从简单的一次性延迟到带有技能注入和跨平台投递的循环 cron 表达式任务。

## 关键文件

| 文件 | 用途 |
|------|------|
| `cron/jobs.py` | 任务模型、存储、对 `jobs.json` 的原子读写 |
| `cron/scheduler.py` | 调度器循环 — 到期任务检测、执行、重复跟踪 |
| `tools/cronjob_tools.py` | 面向模型的 `cronjob` 工具注册和处理器 |
| `gateway/run.py` | 网关集成 — 在长运行循环中的 cron tick |
| `hermes_cli/cron.py` | CLI `hermes cron` 子命令 |

## 调度模型

支持四种调度格式：

| 格式 | 示例 | 行为 |
|------|------|------|
| **相对延迟** | `30m`、`2h`、`1d` | 一次性，在指定持续时间后触发 |
| **间隔** | `every 2h`、`every 30m` | 循环，按固定间隔触发 |
| **Cron 表达式** | `0 9 * * *` | 标准 5 字段 cron 语法（分、时、日、月、周） |
| **ISO 时间戳** | `2025-01-15T09:00:00` | 一次性，在精确时间触发 |

面向模型的接口是一个带有操作风格的 `cronjob` 工具：`create`、`list`、`update`、`pause`、`resume`、`run`、`remove`。

## 任务存储

任务存储在 `~/.hermes/cron/jobs.json` 中，使用原子写入语义（先写入临时文件，然后重命名）。每条任务记录包含：

```json
{
  "id": "a1b2c3d4e5f6",
  "name": "Daily briefing",
  "prompt": "Summarize today's AI news and funding rounds",
  "schedule": {
    "kind": "cron",
    "expr": "0 9 * * *",
    "display": "0 9 * * *"
  },
  "skills": ["ai-funding-daily-report"],
  "deliver": "telegram:-1001234567890",
  "repeat": {
    "times": null,
    "completed": 42
  },
  "state": "scheduled",
  "enabled": true,
  "next_run_at": "2025-01-16T09:00:00Z",
  "last_run_at": "2025-01-15T09:00:00Z",
  "last_status": "ok",
  "created_at": "2025-01-01T00:00:00Z",
  "model": null,
  "provider": null,
  "script": null
}
```

### 任务生命周期状态

| 状态 | 含义 |
|------|------|
| `scheduled` | 活跃，将在下次计划时间触发 |
| `paused` | 已暂停 — 在恢复前不会触发 |
| `completed` | 重复次数已用完或已触发的一次性任务 |
| `running` | 当前正在执行（临时状态） |

### 向后兼容性

旧版任务可能有一个 `skill` 字段而不是 `skills` 数组。调度器在加载时对其进行规范化 — 单个 `skill` 被提升为 `skills: [skill]`。

## 调度器运行时

### Tick 周期

调度器以周期性 tick 运行（默认：每 60 秒）：

```text
tick()
  1. 获取调度器锁（防止重叠 tick）
  2. 从 jobs.json 加载所有任务
  3. 过滤到期任务（next_run <= now 且 state == "scheduled"）
  4. 对每个到期任务：
     a. 设置状态为 "running"
     b. 创建新的 AIAgent 会话（无对话历史）
     c. 按顺序加载附加的技能（作为用户消息注入）
     d. 通过代理运行任务提示
     e. 将响应投递到配置的目标
     f. 更新 run_count，计算 next_run
     g. 如果重复次数已用完 → state = "completed"
     h. 否则 → state = "scheduled"
  5. 将更新后的任务写回 jobs.json
  6. 释放调度器锁
```

### 网关集成

在网关模式下，调度器 tick 集成到网关的主事件循环中。网关在其周期性维护周期中调用 `scheduler.tick()`，与消息处理并行运行。

在 CLI 模式下，cron 任务仅在运行 `hermes cron` 命令或活跃 CLI 会话期间触发。

### 新会话隔离

每个 cron 任务在完全新的代理会话中运行：

- 没有之前运行的对话历史
- 没有之前 cron 执行的记忆（除非持久化到记忆/文件中）
- 提示必须是自包含的 — cron 任务不能提出澄清问题
- `cronjob` 工具集被禁用（递归保护）

## 技能支持的任务

Cron 任务可以通过 `skills` 字段附加一个或多个技能。在执行时：

1. 按指定顺序加载技能
2. 每个技能的 SKILL.md 内容作为上下文注入
3. 任务的提示作为任务指令附加
4. 代理处理组合的技能上下文 + 提示

这使得可复用、经过测试的工作流无需将完整指令粘贴到 cron 提示中。例如：

```
创建每日融资报告 → 附加 "ai-funding-daily-report" 技能
```

### 脚本支持的任务

任务也可以通过 `script` 字段附加 Python 脚本。脚本在每个代理轮次之前运行，其标准输出作为上下文注入到提示中。这支持数据收集和变更检测模式：

```python
# ~/.hermes/scripts/check_competitors.py
import requests, json
# 获取竞争对手的发布说明，与上次运行进行对比
# 将摘要打印到标准输出 — 代理分析并报告
```

脚本超时默认为 120 秒。`_get_script_timeout()` 通过三层链解析限制：

1. **模块级覆盖** — `_SCRIPT_TIMEOUT`（用于测试/猴子补丁）。仅在与默认值不同时使用。
2. **环境变量** — `HERMES_CRON_SCRIPT_TIMEOUT`
3. **配置** — `config.yaml` 中的 `cron.script_timeout_seconds`（通过 `load_config()` 读取）
4. **默认值** — 120 秒

### 提供者恢复

`run_job()` 将用户配置的备用提供者和凭证池传递给 `AIAgent` 实例：

- **备用提供者** — 从 `config.yaml` 读取 `fallback_providers`（列表）或 `fallback_model`（旧版字典），匹配网关的 `_load_fallback_model()` 模式。作为 `fallback_model=` 传递给 `AIAgent.__init__`，后者将两种格式规范化为备用链。
- **凭证池** — 使用解析后的运行时提供者名称通过 `agent.credential_pool` 的 `load_pool(provider)` 加载。仅在池有凭证时传递（`pool.has_credentials()`）。在 429/速率限制错误时启用同提供者密钥轮换。

这镜像了网关的行为 — 没有它，cron 代理会在速率限制时失败而不尝试恢复。

## 投递模型

Cron 任务结果可以投递到任何支持的平台：

| 目标 | 语法 | 示例 |
|------|------|------|
| 原始聊天 | `origin` | 投递到创建任务的聊天 |
| 本地文件 | `local` | 保存到 `~/.hermes/cron/output/` |
| Telegram | `telegram` 或 `telegram:<chat_id>` | `telegram:-1001234567890` |
| Discord | `discord` 或 `discord:#channel` | `discord:#engineering` |
| Slack | `slack` | 投递到 Slack 主频道 |
| WhatsApp | `whatsapp` | 投递到 WhatsApp 主频道 |
| Signal | `signal` | 投递到 Signal |
| Matrix | `matrix` | 投递到 Matrix 主房间 |
| Mattermost | `mattermost` | 投递到 Mattermost 主频道 |
| Email | `email` | 通过电子邮件投递 |
| SMS | `sms` | 通过短信投递 |
| Home Assistant | `homeassistant` | 投递到 HA 对话 |
| DingTalk | `dingtalk` | 投递到钉钉 |
| Feishu | `feishu` | 投递到飞书 |
| WeCom | `wecom` | 投递到企业微信 |
| Weixin | `weixin` | 投递到微信 |
| BlueBubbles | `bluebubbles` | 通过 BlueBubbles 投递到 iMessage |
| QQ Bot | `qqbot` | 通过官方 API v2 投递到 QQ（腾讯） |

对于 Telegram 主题，使用格式 `telegram:<chat_id>:<thread_id>`（例如 `telegram:-1001234567890:17585`）。

### 响应包装

默认情况下（`cron.wrap_response: true`），cron 投递会被包装为：
- 标识 cron 任务名称和任务的头部
- 说明代理无法在对话中看到已投递消息的尾部

Cron 响应中的 `[SILENT]` 前缀会完全抑制投递 — 适用于只需写入文件或执行副作用的任务。

### 会话隔离

Cron 投递不会被镜像到网关会话对话历史中。它们仅存在于 cron 任务自己的会话中。这防止了目标聊天对话中的消息交替违规。

## 递归保护

Cron 运行的会话禁用了 `cronjob` 工具集。这防止了：
- 计划任务创建新的 cron 任务
- 可能导致 token 使用激增的递归调度
- 从任务内部意外修改任务调度

## 锁定

调度器使用基于文件的锁定来防止重叠的 tick 执行相同的到期任务批次两次。这在网关模式下很重要，因为如果之前的 tick 耗时超过 tick 间隔，多个维护周期可能会重叠。

## CLI 接口

`hermes cron` CLI 提供直接的任务管理：

```bash
hermes cron list                    # 显示所有任务
hermes cron create                  # 交互式任务创建（别名：add）
hermes cron edit <job_id>           # 编辑任务配置
hermes cron pause <job_id>          # 暂停运行中的任务
hermes cron resume <job_id>         # 恢复已暂停的任务
hermes cron run <job_id>            # 触发立即执行
hermes cron remove <job_id>         # 删除任务
```

## 相关文档

- [Cron 功能指南](/docs/user-guide/features/cron)
- [网关内部机制](./gateway-internals.md)
- [代理循环内部机制](./agent-loop.md)
