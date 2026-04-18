---
sidebar_position: 10
title: "钉钉"
description: "将 Hermes Agent 设置为钉钉聊天机器人"
---

# 钉钉设置

Hermes Agent 可与钉钉集成为聊天机器人，让你通过私信或群聊与 AI 助手交谈。机器人通过钉钉的 Stream Mode 连接 —— 一个长驻的 WebSocket 连接，不需要公共 URL 或 webhook 服务器 —— 并通过钉钉的会话 webhook API 使用 markdown 格式消息回复。

在设置之前，先了解大多数人最想知道的：Hermes 进入你的钉钉工作区后的行为方式。

## Hermes 的行为方式

| 场景 | 行为 |
|---------|----------|
| **私信（1:1 聊天）** | Hermes 回复每条消息。无需 `@提及`。每个私信有自己的会话。 |
| **群聊** | Hermes 在你 `@提及` 它时才回复。没有提及时，Hermes 忽略消息。 |
| **多用户共享群组** | 默认情况下，Hermes 在群组内按用户隔离会话历史。两个人在同一群组中聊天不会共享一份对话记录，除非你明确禁用此功能。 |

### 钉钉中的会话模型

默认情况下：

- 每个私信有自己的会话
- 共享群聊中每个用户有自己的会话

这通过 `config.yaml` 控制：

```yaml
group_sessions_per_user: true
```

仅在你明确需要整个群组共享一个对话时才设为 `false`：

```yaml
group_sessions_per_user: false
```

本指南将带你完成从创建钉钉机器人到发送第一条消息的完整设置过程。

## 前提条件

安装所需的 Python 包：

```bash
pip install dingtalk-stream httpx
```

- `dingtalk-stream` —— 钉钉官方 Stream Mode SDK（基于 WebSocket 的实时消息）
- `httpx` —— 异步 HTTP 客户端，用于通过会话 webhook 发送回复

## 第一步：创建钉钉应用

1. 访问[钉钉开发者控制台](https://open-dev.dingtalk.com/)。
2. 使用你的钉钉管理员账户登录。
3. 点击 **应用开发** → **企业内部应用** → **创建应用**（或根据控制台版本选择 **机器人**）。
4. 填写：
   - **应用名称**：例如 `Hermes Agent`
   - **描述**：可选
5. 创建后，导航到 **凭证与基本信息** 找到你的 **Client ID**（AppKey）和 **Client Secret**（AppSecret）。复制两者。

:::warning[凭证只显示一次]
Client Secret 在创建应用时只显示一次。如果丢失，你需要重新生成。永远不要公开分享这些凭证或提交到 Git。
:::

## 第二步：启用机器人能力

1. 在应用设置页面，进入 **添加能力** → **机器人**。
2. 启用机器人能力。
3. 在 **消息接收模式** 下，选择 **Stream Mode**（推荐 —— 不需要公共 URL）。

:::tip
Stream Mode 是推荐的设置。它使用从你的机器发起的长驻 WebSocket 连接，因此你不需要公网 IP、域名或 webhook 端点。这在 NAT、防火墙后和本地机器上都能工作。
:::

## 第三步：找到你的钉钉用户 ID

Hermes Agent 使用你的钉钉用户 ID 来控制谁可以与机器人交互。钉钉用户 ID 是由你组织管理员设置的字母数字字符串。

要找到你的用户 ID：

1. 询问你的钉钉组织管理员 —— 用户 ID 在钉钉管理控制台的 **通讯录** → **成员** 中配置。
2. 或者，机器人会在日志中记录每条入站消息的 `sender_id`。启动网关，给机器人发条消息，然后检查日志中你的 ID。

## 第四步：配置 Hermes Agent

### 选项 A：交互式设置（推荐）

运行引导式设置命令：

```bash
hermes gateway setup
```

出现提示时选择 **DingTalk**，然后在询问时粘贴你的 Client ID、Client Secret 和允许的用户 ID。

### 选项 B：手动配置

将以下内容添加到 `~/.hermes/.env` 文件：

```bash
# 必需
DINGTALK_CLIENT_ID=your-app-key
DINGTALK_CLIENT_SECRET=your-app-secret

# 安全：限制谁可以与机器人交互
DINGTALK_ALLOWED_USERS=user-id-1

# 多个允许的用户（逗号分隔）
# DINGTALK_ALLOWED_USERS=user-id-1,user-id-2
```

`~/.hermes/config.yaml` 中的可选行为设置：

```yaml
group_sessions_per_user: true
```

- `group_sessions_per_user: true` 在共享群聊中保持每个参与者的上下文隔离

### 启动网关

配置完成后，启动钉钉网关：

```bash
hermes gateway
```

机器人应该会在几秒内连接到钉钉的 Stream Mode。给它发条消息 —— 私信或在它被添加的群组中 —— 来测试。

:::tip
你可以在后台运行 `hermes gateway` 或作为 systemd 服务以持久运行。详见部署文档。
:::

## 故障排除

### 机器人不响应消息

**原因**：机器人能力未启用，或 `DINGTALK_ALLOWED_USERS` 不包含你的用户 ID。

**修复**：验证应用设置中已启用机器人能力且选择了 Stream Mode。检查你的用户 ID 是否在 `DINGTALK_ALLOWED_USERS` 中。重启网关。

### "dingtalk-stream not installed" 错误

**原因**：`dingtalk-stream` Python 包未安装。

**修复**：安装它：

```bash
pip install dingtalk-stream httpx
```

### "DINGTALK_CLIENT_ID and DINGTALK_CLIENT_SECRET required"

**原因**：凭证未在环境变量或 `.env` 文件中设置。

**修复**：验证 `~/.hermes/.env` 中的 `DINGTALK_CLIENT_ID` 和 `DINGTALK_CLIENT_SECRET` 是否正确设置。Client ID 是你的 AppKey，Client Secret 是钉钉开发者控制台中的 AppSecret。

### Stream 断连/重连循环

**原因**：网络不稳定、钉钉平台维护或凭证问题。

**修复**：适配器使用指数退避自动重连（2 秒 → 5 秒 → 10 秒 → 30 秒 → 60 秒）。检查凭证是否有效且应用未被停用。验证网络允许出站 WebSocket 连接。

### 机器人离线

**原因**：Hermes 网关未运行或连接失败。

**修复**：检查 `hermes gateway` 是否正在运行。查看终端输出中的错误消息。常见问题：凭证错误、应用停用、`dingtalk-stream` 或 `httpx` 未安装。

### "No session_webhook available"

**原因**：机器人尝试回复但没有会话 webhook URL。这通常发生在 webhook 过期或机器人在接收消息和发送回复之间重启时。

**修复**：给机器人发一条新消息 —— 每条入站消息提供一个新的会话 webhook 用于回复。这是正常的钉钉限制；机器人只能回复它最近收到的消息。

## 安全

:::warning
始终设置 `DINGTALK_ALLOWED_USERS` 来限制谁可以与机器人交互。未设置时，网关默认拒绝所有用户作为安全措施。只添加你信任的人的用户 ID —— 授权用户可以完全访问代理的能力，包括工具使用和系统访问。
:::

关于保护 Hermes Agent 部署的更多信息，请参阅[安全指南](../security.md)。

## 注意事项

- **Stream Mode**：不需要公网 URL、域名或 webhook 服务器。连接通过 WebSocket 从你的机器发起，因此在 NAT 和防火墙后都能工作。
- **Markdown 回复**：回复以钉钉的 markdown 格式化，提供富文本显示。
- **消息去重**：适配器使用 5 分钟窗口去重消息，防止处理同一消息两次。
- **自动重连**：如果 stream 连接断开，适配器使用指数退避自动重连。
- **消息长度限制**：回复每条消息上限为 20,000 字符。更长的回复会被截断。
