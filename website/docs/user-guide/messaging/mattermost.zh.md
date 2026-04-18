---
sidebar_position: 8
title: "Mattermost"
description: "将 Hermes Agent 设置为 Mattermost 机器人"
---

# Mattermost 设置

Hermes Agent 作为机器人与 Mattermost 集成，让你可以通过私信或团队频道与 AI 助手聊天。Mattermost 是一个自托管的开源 Slack 替代方案 —— 你在自己的基础设施上运行它，完全控制你的数据。机器人通过 Mattermost 的 REST API（v4）和 WebSocket 实时事件连接，通过 Hermes Agent 管道（包括工具使用、记忆和推理）处理消息，并实时回复。它支持文本、文件附件、图片和斜杠命令。

不需要外部 Mattermost 库 —— 适配器使用 `aiohttp`，它已经是 Hermes 的依赖。

在开始设置之前，先了解大多数人最想知道的部分：Hermes 在你的 Mattermost 实例中的行为方式。

## Hermes 的行为方式

| 场景 | 行为 |
|---------|----------|
| **私信** | Hermes 回复每条消息。无需 `@提及`。每个私信有自己的会话。 |
| **公开/私有频道** | Hermes 在你 `@提及` 它时回复。没有提及时，Hermes 忽略消息。 |
| **主题帖** | 如果 `MATTERMOST_REPLY_MODE=thread`，Hermes 在你消息下方的主题帖中回复。主题帖上下文与父频道保持隔离。 |
| **多用户共享频道** | 默认情况下，Hermes 在频道内按用户隔离会话历史。两个人在同一频道聊天不会共享一个对话记录，除非你明确禁用此功能。 |

:::tip
如果你希望 Hermes 以主题帖形式回复（嵌套在原始消息下方），请设置 `MATTERMOST_REPLY_MODE=thread`。默认值为 `off`，即在频道中发送平面消息。
:::

### Mattermost 中的会话模型

默认情况下：

- 每个私信有自己的会话
- 每个主题帖有自己的会话命名空间
- 共享频道中的每个用户在该频道内有自己的会话

这由 `config.yaml` 控制：

```yaml
group_sessions_per_user: true
```

仅当你明确需要整个频道共享一个对话时，才将其设为 `false`：

```yaml
group_sessions_per_user: false
```

共享会话可用于协作频道，但这也意味着：

- 用户共享上下文增长和令牌成本
- 一个人的长时间工具密集型任务可能会膨胀其他人的上下文
- 一个人的进行中任务可能会中断另一个人在同一频道中的后续操作

本指南将引导你完成从在 Mattermost 上创建机器人到发送第一条消息的完整设置过程。

## 步骤 1：启用机器人帐户

在创建机器人之前，必须在 Mattermost 服务器上启用机器人帐户。

1. 以**系统管理员**身份登录 Mattermost。
2. 进入 **System Console** → **Integrations** → **Bot Accounts**。
3. 将 **Enable Bot Account Creation** 设为 **true**。
4. 点击 **Save**。

:::info
如果你没有系统管理员权限，请让 Mattermost 管理员启用机器人帐户并为你创建一个。
:::

## 步骤 2：创建机器人帐户

1. 在 Mattermost 中，点击 **☰** 菜单（左上角）→ **Integrations** → **Bot Accounts**。
2. 点击 **Add Bot Account**。
3. 填写详细信息：
   - **Username**：例如 `hermes`
   - **Display Name**：例如 `Hermes Agent`
   - **Description**：可选
   - **Role**：`Member` 即可
4. 点击 **Create Bot Account**。
5. Mattermost 将显示**机器人令牌**。**立即复制。**

:::warning[令牌仅显示一次]
机器人令牌仅在创建机器人帐户时显示一次。如果丢失，你需要从机器人帐户设置中重新生成。切勿公开分享或提交到 Git —— 任何拥有此令牌的人都能完全控制机器人。
:::

将令牌保存在安全的地方（例如密码管理器）。你将在步骤 5 中用到它。

:::tip
你也可以使用**个人访问令牌**代替机器人帐户。进入 **Profile** → **Security** → **Personal Access Tokens** → **Create Token**。如果你希望 Hermes 以你自己的用户身份发帖而不是单独的机器人用户，这很有用。
:::

## 步骤 3：将机器人添加到频道

机器人需要成为你希望它回复的任何频道的成员：

1. 打开你希望机器人所在的频道。
2. 点击频道名称 → **Add Members**。
3. 搜索你的机器人用户名（例如 `hermes`）并添加。

对于私信，只需打开与机器人的私信 —— 它将能立即回复。

## 步骤 4：查找你的 Mattermost 用户 ID

Hermes Agent 使用你的 Mattermost 用户 ID 来控制谁可以与机器人交互。查找方法：

1. 点击你的**头像**（左上角）→ **Profile**。
2. 你的用户 ID 显示在个人资料对话框中 —— 点击它即可复制。

你的用户 ID 是一个 26 位的字母数字字符串，如 `3uo8dkh1p7g1mfk49ear5fzs5c`。

:::warning
你的用户 ID **不是**你的用户名。用户名是 `@` 后面的内容（例如 `@alice`）。用户 ID 是 Mattermost 内部使用的长字母数字标识符。
:::

**替代方法**：你也可以通过 API 获取用户 ID：

```bash
curl -H "Authorization: Bearer YOUR_TOKEN" \
  https://your-mattermost-server/api/v4/users/me | jq .id
```

:::tip
要获取**频道 ID**：点击频道名称 → **View Info**。频道 ID 显示在信息面板中。如果你想手动设置主频道，你将需要它。
:::

## 步骤 5：配置 Hermes Agent

### 方式 A：交互式设置（推荐）

运行引导式设置命令：

```bash
hermes gateway setup
```

出现提示时选择 **Mattermost**，然后粘贴你的服务器 URL、机器人令牌和用户 ID。

### 方式 B：手动配置

在你的 `~/.hermes/.env` 文件中添加以下内容：

```bash
# 必填
MATTERMOST_URL=https://mm.example.com
MATTERMOST_TOKEN=***
MATTERMOST_ALLOWED_USERS=3uo8dkh1p7g1mfk49ear5fzs5c

# 多个允许的用户（逗号分隔）
# MATTERMOST_ALLOWED_USERS=3uo8dkh1p7g1mfk49ear5fzs5c,8fk2jd9s0a7bncm1xqw4tp6r3e

# 可选：回复模式（thread 或 off，默认：off）
# MATTERMOST_REPLY_MODE=thread

# 可选：无需 @提及即可回复（默认：true = 需要提及）
# MATTERMOST_REQUIRE_MENTION=false

# 可选：机器人无需 @提及即可回复的频道（逗号分隔的频道 ID）
# MATTERMOST_FREE_RESPONSE_CHANNELS=channel_id_1,channel_id_2
```

`~/.hermes/config.yaml` 中的可选行为设置：

```yaml
group_sessions_per_user: true
```

- `group_sessions_per_user: true` 在共享频道和主题帖中保持每个参与者的上下文隔离

### 启动网关

配置完成后，启动 Mattermost 网关：

```bash
hermes gateway
```

机器人应在几秒内连接到你的 Mattermost 服务器。给它发一条消息 —— 无论是私信还是在它已被添加的频道中 —— 来测试。

:::tip
你可以在后台运行 `hermes gateway` 或将其设为 systemd 服务以保持持久运行。详见部署文档。
:::

## 主频道

你可以指定一个"主频道"，机器人在其中发送主动消息（如定时任务输出、提醒和通知）。有两种设置方式：

### 使用斜杠命令

在机器人所在的任何 Mattermost 频道中输入 `/sethome`。该频道将成为主频道。

### 手动配置

在你的 `~/.hermes/.env` 中添加：

```bash
MATTERMOST_HOME_CHANNEL=abc123def456ghi789jkl012mn
```

将 ID 替换为实际的频道 ID（点击频道名称 → View Info → 复制 ID）。

## 回复模式

`MATTERMOST_REPLY_MODE` 设置控制 Hermes 如何发布回复：

| 模式 | 行为 |
|------|----------|
| `off`（默认） | Hermes 在频道中发布平面消息，像普通用户一样。 |
| `thread` | Hermes 在你原始消息下方的主题帖中回复。当有大量来回交流时，保持频道整洁。 |

在你的 `~/.hermes/.env` 中设置：

```bash
MATTERMOST_REPLY_MODE=thread
```

## 提及行为

默认情况下，机器人仅在频道中被 `@提及` 时回复。你可以更改此行为：

| 变量 | 默认值 | 说明 |
|----------|---------|-------------|
| `MATTERMOST_REQUIRE_MENTION` | `true` | 设为 `false` 以回复频道中的所有消息（私信始终有效）。 |
| `MATTERMOST_FREE_RESPONSE_CHANNELS` | _(无)_ | 逗号分隔的频道 ID，机器人在这些频道中无需 `@提及` 即可回复，即使 require_mention 为 true。 |

在 Mattermost 中查找频道 ID：打开频道，点击频道名称标题，在 URL 或频道详情中查找 ID。

当机器人被 `@提及` 时，提及内容在处理前会自动从消息中去除。

## 故障排除

### 机器人不回复消息

**原因**：机器人不是频道成员，或 `MATTERMOST_ALLOWED_USERS` 不包含你的用户 ID。

**修复**：将机器人添加到频道（频道名称 → Add Members → 搜索机器人）。验证你的用户 ID 在 `MATTERMOST_ALLOWED_USERS` 中。重启网关。

### 403 Forbidden 错误

**原因**：机器人令牌无效，或机器人没有在频道中发帖的权限。

**修复**：检查 `.env` 文件中的 `MATTERMOST_TOKEN` 是否正确。确保机器人帐户未被停用。验证机器人已被添加到频道。如果使用个人访问令牌，确保你的帐户具有所需权限。

### WebSocket 断连 / 重连循环

**原因**：网络不稳定、Mattermost 服务器重启或防火墙/代理阻止 WebSocket 连接。

**修复**：适配器自动使用指数退避重连（2秒 → 60秒）。检查服务器的 WebSocket 配置 —— 反向代理（nginx、Apache）需要配置 WebSocket 升级头部。确认没有防火墙阻止 Mattermost 服务器上的 WebSocket 连接。

对于 nginx，确保配置包含：

```nginx
location /api/v4/websocket {
    proxy_pass http://mattermost-backend;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_read_timeout 600s;
}
```

### 启动时 "Failed to authenticate"

**原因**：令牌或服务器 URL 不正确。

**修复**：验证 `MATTERMOST_URL` 指向你的 Mattermost 服务器（包含 `https://`，末尾无斜杠）。检查 `MATTERMOST_TOKEN` 是否有效 —— 用 curl 测试：

```bash
curl -H "Authorization: Bearer YOUR_TOKEN" \
  https://your-server/api/v4/users/me
```

如果返回你机器人的用户信息，则令牌有效。如果返回错误，则需要重新生成令牌。

### 机器人离线

**原因**：Hermes 网关未运行，或连接失败。

**修复**：检查 `hermes gateway` 是否在运行。查看终端输出中的错误消息。常见问题：错误的 URL、过期的令牌、Mattermost 服务器不可达。

### "User not allowed" / 机器人忽略你

**原因**：你的用户 ID 不在 `MATTERMOST_ALLOWED_USERS` 中。

**修复**：在 `~/.hermes/.env` 中将你的用户 ID 添加到 `MATTERMOST_ALLOWED_USERS`，然后重启网关。记住：用户 ID 是 26 位字母数字字符串，不是你的 `@username`。

## 按频道提示词

为特定 Mattermost 频道分配临时系统提示词。提示词在运行时注入到每个轮次中 —— 从不持久化到对话记录 —— 因此更改立即生效。

```yaml
mattermost:
  channel_prompts:
    "channel_id_abc123": |
      你是一个研究助理。专注于学术来源、
      引用和简洁的综合。
    "channel_id_def456": |
      代码审查模式。精确关注边界情况和
      性能影响。
```

键是 Mattermost 频道 ID（在频道 URL 或通过 API 找到）。匹配频道中的所有消息都会注入提示词作为临时系统指令。

## 安全

:::warning
始终设置 `MATTERMOST_ALLOWED_USERS` 以限制谁可以与机器人交互。如果不设置，网关默认拒绝所有用户作为安全措施。仅添加你信任的用户 ID —— 授权用户可以完全访问代理的能力，包括工具使用和系统访问。
:::

有关保护 Hermes Agent 部署的更多信息，请参见[安全指南](../security.md)。

## 注意事项

- **自托管友好**：可与任何自托管的 Mattermost 实例配合使用。不需要 Mattermost Cloud 帐户或订阅。
- **无额外依赖**：适配器使用 `aiohttp` 进行 HTTP 和 WebSocket 通信，它已包含在 Hermes Agent 中。
- **兼容 Team 版**：可与 Mattermost Team Edition（免费版）和 Enterprise Edition 配合使用。
