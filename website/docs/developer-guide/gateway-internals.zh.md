---
sidebar_position: 7
title: "网关内部机制"
description: "消息网关如何启动、授权用户、路由会话和投递消息"
---

# 网关内部机制

消息网关是通过统一架构将 Hermes 连接到 14+ 个外部消息平台的长运行进程。

## 关键文件

| 文件 | 用途 |
|------|------|
| `gateway/run.py` | `GatewayRunner` — 主循环、斜杠命令、消息分发（约 9,000 行） |
| `gateway/session.py` | `SessionStore` — 对话持久化和会话键构建 |
| `gateway/delivery.py` | 向目标平台/频道的出站消息投递 |
| `gateway/pairing.py` | 用户授权的 DM 配对流程 |
| `gateway/channel_directory.py` | 将聊天 ID 映射到人类可读名称用于 cron 投递 |
| `gateway/hooks.py` | Hook 发现、加载和生命周期事件分发 |
| `gateway/mirror.py` | `send_message` 的跨会话消息镜像 |
| `gateway/status.py` | 配置文件作用域网关实例的 token 锁管理 |
| `gateway/builtin_hooks/` | 始终注册的 hook（例如 BOOT.md 系统提示 hook） |
| `gateway/platforms/` | 平台适配器（每个消息平台一个） |

## 架构概述

```text
┌─────────────────────────────────────────────────┐
│                  GatewayRunner                  │
│                                                 │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐       │
│  │ Telegram │  │ Discord  │  │  Slack   │       │
│  │ 适配器   │  │ 适配器   │  │ 适配器   │       │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘       │
│       │             │             │             │
│       └─────────────┼─────────────┘             │
│                     ▼                           │
│              _handle_message()                  │
│                     │                           │
│         ┌───────────┼───────────┐               │
│         ▼           ▼           ▼               │
│  斜杠命令       AIAgent      队列/后台          │
│    分发          创建        会话               │
│                     │                           │
│                     ▼                           │
│                 SessionStore                    │
│              (SQLite 持久化)                    │
└─────────────────────────────────────────────────┘
```

## 消息流程

当消息从任何平台到达时：

1. **平台适配器**接收原始事件，将其规范化为 `MessageEvent`
2. **基础适配器**检查活跃会话守卫：
   - 如果代理正在为此会话运行 → 排队消息，设置中断事件
   - 如果是 `/approve`、`/deny`、`/stop` → 绕过守卫（内联分发）
3. **GatewayRunner._handle_message()** 接收事件：
   - 通过 `_session_key_for_source()` 解析会话键（格式：`agent:main:{platform}:{chat_type}:{chat_id}`）
   - 检查授权（见下方授权部分）
   - 检查是否为斜杠命令 → 分发到命令处理器
   - 检查代理是否已在运行 → 拦截 `/stop`、`/status` 等命令
   - 否则 → 创建 `AIAgent` 实例并运行对话
4. **响应**通过平台适配器发回

### 会话键格式

会话键编码完整的路由上下文：

```
agent:main:{platform}:{chat_type}:{chat_id}
```

例如：`agent:main:telegram:private:123456789`

支持线程的平台（Telegram 论坛主题、Discord 线程、Slack 线程）可能在 chat_id 部分包含线程 ID。**永远不要手动构建会话键** — 始终使用 `gateway/session.py` 中的 `build_session_key()`。

### 两级消息守卫

当代理正在运行时，传入消息通过两个顺序守卫：

1. **级别 1 — 基础适配器**（`gateway/platforms/base.py`）：检查 `_active_sessions`。如果会话活跃，将消息排入 `_pending_messages` 并设置中断事件。这在消息到达网关运行器*之前*捕获它们。

2. **级别 2 — 网关运行器**（`gateway/run.py`）：检查 `_running_agents`。拦截特定命令（`/stop`、`/new`、`/queue`、`/status`、`/approve`、`/deny`）并适当路由。其他一切触发 `running_agent.interrupt()`。

必须在代理阻塞时到达运行器的命令（如 `/approve`）通过 `await self._message_handler(event)` **内联**分发 — 它们绕过后台任务系统以避免竞态条件。

## 授权

网关使用多层授权检查，按顺序评估：

1. **每平台全允许标志**（例如 `TELEGRAM_ALLOW_ALL_USERS`）— 如果设置，该平台上的所有用户都被授权
2. **平台允许列表**（例如 `TELEGRAM_ALLOWED_USERS`）— 逗号分隔的用户 ID
3. **DM 配对** — 已认证用户可以通过配对码配对新用户
4. **全局全允许**（`GATEWAY_ALLOW_ALL_USERS`）— 如果设置，所有平台上的所有用户都被授权
5. **默认：拒绝** — 未授权用户被拒绝

### DM 配对流程

```text
管理员：/pair
网关："配对码：ABC123。分享给用户。"
新用户：ABC123
网关："已配对！你现在已被授权。"
```

配对状态持久化在 `gateway/pairing.py` 中，重启后仍然有效。

## 斜杠命令分发

网关中所有斜杠命令通过相同的解析流水线：

1. `hermes_cli/commands.py` 中的 `resolve_command()` 将输入映射到规范名称（处理别名、前缀匹配）
2. 规范名称与 `GATEWAY_KNOWN_COMMANDS` 对照检查
3. `_handle_message()` 中的处理器根据规范名称分发
4. 某些命令受配置门控（`CommandDef` 上的 `gateway_config_gate`）

### 运行中代理守卫

在代理处理期间不能执行的命令会被提前拒绝：

```python
if _quick_key in self._running_agents:
    if canonical == "model":
        return "⏳ Agent is running — wait for it to finish or /stop first."
```

绕过命令（`/stop`、`/new`、`/approve`、`/deny`、`/queue`、`/status`）有特殊处理。

## 配置来源

网关从多个来源读取配置：

| 来源 | 提供内容 |
|------|----------|
| `~/.hermes/.env` | API 密钥、机器人 token、平台凭证 |
| `~/.hermes/config.yaml` | 模型设置、工具配置、显示选项 |
| 环境变量 | 覆盖以上任何内容 |

与 CLI（使用带硬编码默认值的 `load_cli_config()`）不同，网关通过 YAML 加载器直接读取 `config.yaml`。这意味着存在于 CLI 默认值字典中但不在用户配置文件中的配置键在 CLI 和网关之间可能表现不同。

## 平台适配器

每个消息平台在 `gateway/platforms/` 中有一个适配器：

```text
gateway/platforms/
├── base.py              # BaseAdapter — 所有平台的共享逻辑
├── telegram.py          # Telegram Bot API（长轮询或 webhook）
├── discord.py           # 通过 discord.py 的 Discord 机器人
├── slack.py             # Slack Socket Mode
├── whatsapp.py          # WhatsApp Business Cloud API
├── signal.py            # 通过 signal-cli REST API 的 Signal
├── matrix.py            # 通过 mautrix 的 Matrix（可选 E2EE）
├── mattermost.py        # Mattermost WebSocket API
├── email.py             # 通过 IMAP/SMTP 的电子邮件
├── sms.py               # 通过 Twilio 的短信
├── dingtalk.py          # 钉钉 WebSocket
├── feishu.py            # 飞书/Lark WebSocket 或 webhook
├── wecom.py             # 企业微信回调
├── weixin.py            # 通过 iLink Bot API 的微信
├── bluebubbles.py       # 通过 BlueBubbles macOS 服务器的 Apple iMessage
├── qqbot.py             # 通过官方 API v2 的 QQ 机器人（腾讯 QQ）
├── webhook.py           # 入站/出站 webhook 适配器
├── api_server.py        # REST API 服务器适配器
└── homeassistant.py     # Home Assistant 对话集成
```

适配器实现通用接口：
- `connect()` / `disconnect()` — 生命周期管理
- `send_message()` — 出站消息投递
- `on_message()` — 入站消息规范化 → `MessageEvent`

### Token 锁

使用唯一凭证连接的适配器在 `connect()` 中调用 `acquire_scoped_lock()`，在 `disconnect()` 中调用 `release_scoped_lock()`。这防止两个配置文件同时使用相同的机器人 token。

## 投递路径

出站投递（`gateway/delivery.py`）处理：

- **直接回复** — 将响应发回到发起聊天
- **主频道投递** — 将 cron 任务输出和后台结果路由到配置的主频道
- **显式目标投递** — `send_message` 工具指定 `telegram:-1001234567890`
- **跨平台投递** — 投递到与发起消息不同的平台

Cron 任务投递不会镜像到网关会话历史中 — 它们仅存在于自己的 cron 会话中。这是一个有意的设计选择，以避免消息交替违规。

## Hook

网关 hook 是响应生命周期事件的 Python 模块：

### 网关 Hook 事件

| 事件 | 触发时机 |
|------|----------|
| `gateway:startup` | 网关进程启动 |
| `session:start` | 新对话会话开始 |
| `session:end` | 会话完成或超时 |
| `session:reset` | 用户使用 `/new` 重置会话 |
| `agent:start` | 代理开始处理消息 |
| `agent:step` | 代理完成一次工具调用迭代 |
| `agent:end` | 代理完成并返回响应 |
| `command:*` | 任何斜杠命令被执行 |

Hook 从 `gateway/builtin_hooks/`（始终活跃）和 `~/.hermes/hooks/`（用户安装）中发现。每个 hook 是一个包含 `HOOK.yaml` 清单和 `handler.py` 的目录。

## 记忆提供者集成

当记忆提供者插件（例如 Honcho）启用时：

1. 网关使用会话 ID 为每条消息创建 `AIAgent`
2. `MemoryManager` 使用会话上下文初始化提供者
3. 提供者工具（例如 `honcho_profile`、`viking_search`）通过以下路径路由：

```text
AIAgent._invoke_tool()
  → self._memory_manager.handle_tool_call(name, args)
    → provider.handle_tool_call(name, args)
```

4. 在会话结束/重置时，`on_session_end()` 触发清理和最终数据刷新

### 记忆刷新生命周期

当会话被重置、恢复或过期时：
1. 内置记忆刷新到磁盘
2. 记忆提供者的 `on_session_end()` hook 触发
3. 临时 `AIAgent` 运行仅记忆的对话轮次
4. 然后上下文被丢弃或归档

## 后台维护

网关在消息处理之外运行周期性维护：

- **Cron tick** — 检查任务调度并触发到期任务
- **会话过期** — 在超时后清理废弃会话
- **记忆刷新** — 在会话过期前主动刷新记忆
- **缓存刷新** — 刷新模型列表和提供者状态

## 进程管理

网关作为长期运行进程运行，通过以下方式管理：

- `hermes gateway start` / `hermes gateway stop` — 手动控制
- `systemctl`（Linux）或 `launchctl`（macOS）— 服务管理
- PID 文件位于 `~/.hermes/gateway.pid` — 配置文件作用域的进程跟踪

**配置文件作用域 vs 全局**：`start_gateway()` 使用配置文件作用域的 PID 文件。`hermes gateway stop` 仅停止当前配置文件的网关。`hermes gateway stop --all` 使用全局 `ps aux` 扫描来终止所有网关进程（在更新期间使用）。

## 相关文档

- [会话存储](./session-storage.md)
- [Cron 内部机制](./cron-internals.md)
- [ACP 内部机制](./acp-internals.md)
- [代理循环内部机制](./agent-loop.md)
- [消息网关（用户指南）](/docs/user-guide/messaging)
