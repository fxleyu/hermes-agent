---
sidebar_position: 4
title: "Slack"
description: "使用 Socket Mode 将 Hermes Agent 设置为 Slack 机器人"
---

# Slack 设置

将 Hermes Agent 作为机器人连接到 Slack，使用 Socket Mode。Socket Mode 使用 WebSocket 而非公共 HTTP 端点，因此你的 Hermes 实例不需要可公开访问 —— 它可以在防火墙后、笔记本电脑上或私有服务器上工作。

:::warning 经典 Slack 应用已弃用
经典 Slack 应用（使用 RTM API）已于 **2025 年 3 月完全弃用**。Hermes 使用现代 Bolt SDK 配合 Socket Mode。如果你有旧的经典应用，必须按照以下步骤创建新的。
:::

## 概览

| 组件 | 值 |
|-----------|-------|
| **库** | `slack-bolt` / `slack_sdk` for Python (Socket Mode) |
| **连接** | WebSocket —— 无需公共 URL |
| **所需认证令牌** | Bot Token (`xoxb-`) + App-Level Token (`xapp-`) |
| **用户标识** | Slack 成员 ID（例如 `U01ABC2DEF3`） |

---

## 第一步：创建 Slack 应用

1. 访问 [https://api.slack.com/apps](https://api.slack.com/apps)
2. 点击 **Create New App**
3. 选择 **From scratch**
4. 输入应用名称（例如 "Hermes Agent"）并选择你的工作区
5. 点击 **Create App**

你将进入应用的 **Basic Information** 页面。

---

## 第二步：配置 Bot Token 权限范围

在侧边栏中导航到 **Features → OAuth & Permissions**。向下滚动到 **Scopes → Bot Token Scopes** 并添加以下权限：

| 权限范围 | 用途 |
|-------|---------|
| `chat:write` | 以机器人身份发送消息 |
| `app_mentions:read` | 检测在频道中被 @提及 |
| `channels:history` | 读取机器人所在公共频道中的消息 |
| `channels:read` | 列出并获取公共频道信息 |
| `groups:history` | 读取机器人被邀请的私有频道中的消息 |
| `im:history` | 读取私信历史 |
| `im:read` | 查看基本私信信息 |
| `im:write` | 打开和管理私信 |
| `users:read` | 查找用户信息 |
| `files:read` | 读取和下载附件文件，包括语音笔记/音频 |
| `files:write` | 上传文件（图片、音频、文档） |

:::caution 缺少权限范围 = 缺少功能
没有 `channels:history` 和 `groups:history`，机器人**将无法接收频道中的消息** ——
它只能在私信中工作。这些是最常被遗漏的权限范围。
:::

**可选权限范围：**

| 权限范围 | 用途 |
|-------|---------|
| `groups:read` | 列出并获取私有频道信息 |

---

## 第三步：启用 Socket Mode

Socket Mode 让机器人通过 WebSocket 连接，而不需要公共 URL。

1. 在侧边栏中，进入 **Settings → Socket Mode**
2. 将 **Enable Socket Mode** 切换为 ON
3. 你会被提示创建一个 **App-Level Token**：
   - 命名为类似 `hermes-socket` 的名称（名称无关紧要）
   - 添加 **`connections:write`** 权限范围
   - 点击 **Generate**
4. **复制令牌** —— 它以 `xapp-` 开头。这是你的 `SLACK_APP_TOKEN`

:::tip
你可以随时在 **Settings → Basic Information → App-Level Tokens** 下找到或重新生成应用级令牌。
:::

---

## 第四步：订阅事件

此步骤至关重要 —— 它控制机器人能看到哪些消息。

1. 在侧边栏中，进入 **Features → Event Subscriptions**
2. 将 **Enable Events** 切换为 ON
3. 展开 **Subscribe to bot events** 并添加：

| 事件 | 是否必需？ | 用途 |
|-------|-----------|---------|
| `message.im` | **是** | 机器人接收私信 |
| `message.channels` | **是** | 机器人接收它所在**公共**频道中的消息 |
| `message.groups` | **推荐** | 机器人接收它被邀请的**私有**频道中的消息 |
| `app_mention` | **是** | 防止机器人被 @提及时 Bolt SDK 出错 |

4. 点击页面底部的 **Save Changes**

:::danger 缺少事件订阅是头号设置问题
如果机器人在私信中工作但**在频道中不工作**，你几乎肯定忘记添加 `message.channels`（公共频道）和/或 `message.groups`（私有频道）。没有这些事件，Slack 根本不会将频道消息传递给机器人。
:::

---

## 第五步：启用 Messages 选项卡

此步骤启用向机器人发送私信。没有它，用户尝试给机器人发私信时会看到 **"Sending messages to this app has been turned off"**。

1. 在侧边栏中，进入 **Features → App Home**
2. 滚动到 **Show Tabs**
3. 将 **Messages Tab** 切换为 ON
4. 勾选 **"Allow users to send Slash commands and messages from the messages tab"**

:::danger 没有此步骤，私信将完全被阻止
即使有所有正确的权限范围和事件订阅，除非启用 Messages Tab，Slack 也不允许用户向机器人发送私信。这是 Slack 平台要求，不是 Hermes 配置问题。
:::

---

## 第六步：将应用安装到工作区

1. 在侧边栏中，进入 **Settings → Install App**
2. 点击 **Install to Workspace**
3. 审查权限并点击 **Allow**
4. 授权后，你会看到一个以 `xoxb-` 开头的 **Bot User OAuth Token**
5. **复制此令牌** —— 这是你的 `SLACK_BOT_TOKEN`

:::tip
如果你之后更改了权限范围或事件订阅，你**必须重新安装应用**才能使更改生效。Install App 页面会显示横幅提示你这样做。
:::

---

## 第七步：为白名单查找用户 ID

Hermes 使用 Slack **成员 ID**（不是用户名或显示名称）作为白名单。

要查找成员 ID：

1. 在 Slack 中，点击用户的名称或头像
2. 点击 **View full profile**
3. 点击 **⋮**（更多）按钮
4. 选择 **Copy member ID**

成员 ID 看起来像 `U01ABC2DEF3`。你至少需要自己的成员 ID。

---

## 第八步：配置 Hermes

将以下内容添加到 `~/.hermes/.env` 文件：

```bash
# 必需
SLACK_BOT_TOKEN=xoxb-your-bot-token-here
SLACK_APP_TOKEN=xapp-your-app-token-here
SLACK_ALLOWED_USERS=U01ABC2DEF3              # 逗号分隔的成员 ID

# 可选
SLACK_HOME_CHANNEL=C01234567890              # cron/定时消息的默认频道
SLACK_HOME_CHANNEL_NAME=general              # 主频道的可读名称（可选）
```

或运行交互式设置：

```bash
hermes gateway setup    # 出现提示时选择 Slack
```

然后启动网关：

```bash
hermes gateway              # 前台运行
hermes gateway install      # 安装为用户服务
sudo hermes gateway install --system   # 仅 Linux：开机启动的系统服务
```

---

## 第九步：邀请机器人到频道

启动网关后，你需要**邀请机器人**到你希望它响应的任何频道：

```
/invite @Hermes Agent
```

机器人**不会**自动加入频道。你必须逐个邀请它到每个频道。

---

## 机器人如何响应

了解 Hermes 在不同场景中的行为：

| 场景 | 行为 |
|---------|----------|
| **私信** | 机器人响应每条消息 —— 无需 @提及 |
| **频道** | 机器人**仅在被 @提及时响应**（例如 `@Hermes Agent 现在几点？`）。在频道中，Hermes 在该消息下的线程中回复。 |
| **线程** | 如果你在已有线程中 @提及 Hermes，它在同一线程中回复。一旦机器人在线程中有活跃会话，**线程中的后续回复不需要 @提及** —— 机器人会自然地跟随对话。 |

:::tip
在频道中，始终 @提及机器人来开始对话。一旦机器人在线程中活跃，你可以在该线程中回复而不用提及它。在线程之外，没有 @提及的消息会被忽略以避免在繁忙频道中产生噪音。
:::

---

## 配置选项

除了第八步中的必需环境变量，你可以通过 `~/.hermes/config.yaml` 自定义 Slack 机器人行为。

### 线程和回复行为

```yaml
platforms:
  slack:
    # 控制多部分回复的线程方式
    # "off"   -- 从不将回复线程化到原始消息
    # "first" -- 第一块线程化到用户消息（默认）
    # "all"   -- 所有块都线程化到用户消息
    reply_to_mode: "first"

    extra:
      # 是否在线程中回复（默认：true）。
      # 设为 false 时，频道消息获得直接频道回复而非线程。
      # 已在现有线程中的消息仍在线程中回复。
      reply_in_thread: true

      # 同时将线程回复发布到主频道
      # （Slack 的 "Also send to channel" 功能）。
      # 只有第一个回复的第一块会被广播。
      reply_broadcast: false
```

| 键 | 默认值 | 描述 |
|-----|---------|-------------|
| `platforms.slack.reply_to_mode` | `"first"` | 多部分消息的线程模式：`"off"`、`"first"` 或 `"all"` |
| `platforms.slack.extra.reply_in_thread` | `true` | 为 `false` 时，频道消息获得直接回复而非线程。已在现有线程中的消息仍在线程中回复。 |
| `platforms.slack.extra.reply_broadcast` | `false` | 为 `true` 时，线程回复也会发布到主频道。只有第一块会被广播。 |

### 会话隔离

```yaml
# 全局设置 -- 适用于 Slack 和所有其他平台
group_sessions_per_user: true
```

为 `true`（默认）时，共享频道中每个用户获得自己的隔离对话会话。两个人在 `#general` 中与 Hermes 交谈将有独立的历史记录和上下文。

设为 `false` 如果你想要协作模式，整个频道共享一个对话会话。注意这意味着用户共享上下文增长和令牌成本，一个用户的 `/reset` 会清除所有人的会话。

### 提及和触发行为

```yaml
slack:
  # 在频道中需要 @提及（这是默认行为；
  # Slack 适配器无论如何都在频道中强制 @提及检查，
  # 但你可以为与其他平台的一致性而显式设置此项）
  require_mention: true

  # 触发机器人的自定义提及模式
  # （除默认的 @提及检测外）
  mention_patterns:
    - "hey hermes"
    - "hermes,"

  # 添加到每条发出消息前面的文本
  reply_prefix: ""
```

:::info
与 Discord 和 Telegram 不同，Slack 没有 `free_response_channels` 等效设置。Slack 适配器要求在频道中用 `@提及` 来开始对话。但是，一旦机器人在线程中有活跃会话，后续的线程回复不需要提及。在私信中，机器人始终响应而不需要提及。
:::

### 未授权用户处理

```yaml
slack:
  # 未授权用户（不在 SLACK_ALLOWED_USERS 中）给机器人发私信时的处理方式
  # "pair"   -- 提示他们输入配对码（默认）
  # "ignore" -- 静默丢弃消息
  unauthorized_dm_behavior: "pair"
```

你也可以为所有平台全局设置：

```yaml
unauthorized_dm_behavior: "pair"
```

`slack:` 下的平台特定设置优先于全局设置。

### 语音转录

```yaml
# 全局设置 -- 启用/禁用接收语音消息的自动转录
stt_enabled: true
```

为 `true`（默认）时，接收的音频消息在代理处理前使用配置的 STT 提供商自动转录。

### 完整示例

```yaml
# 全局网关设置
group_sessions_per_user: true
unauthorized_dm_behavior: "pair"
stt_enabled: true

# Slack 特定设置
slack:
  require_mention: true
  unauthorized_dm_behavior: "pair"

# 平台配置
platforms:
  slack:
    reply_to_mode: "first"
    extra:
      reply_in_thread: true
      reply_broadcast: false
```

---

## 主频道

将 `SLACK_HOME_CHANNEL` 设为一个频道 ID，Hermes 将在此发送定时消息、cron 任务结果和其他主动通知。要查找频道 ID：

1. 在 Slack 中右键点击频道名称
2. 点击 **View channel details**
3. 滚动到底部 —— 频道 ID 显示在那里

```bash
SLACK_HOME_CHANNEL=C01234567890
```

确保机器人已被**邀请到该频道**（`/invite @Hermes Agent`）。

---

## 多工作区支持

Hermes 可以使用单个网关实例同时连接到**多个 Slack 工作区**。每个工作区使用自己的机器人用户 ID 独立认证。

### 配置

在 `SLACK_BOT_TOKEN` 中提供多个 bot token，以**逗号分隔**：

```bash
# 多个 bot token -- 每个工作区一个
SLACK_BOT_TOKEN=xoxb-workspace1-token,xoxb-workspace2-token,xoxb-workspace3-token

# 仍然使用单个 app-level token 用于 Socket Mode
SLACK_APP_TOKEN=xapp-your-app-token
```

或在 `~/.hermes/config.yaml` 中：

```yaml
platforms:
  slack:
    token: "xoxb-workspace1-token,xoxb-workspace2-token"
```

### OAuth Token 文件

除了环境变量或配置中的令牌，Hermes 还从以下位置的 **OAuth token 文件**加载令牌：

```
~/.hermes/slack_tokens.json
```

此文件是将团队 ID 映射到令牌条目的 JSON 对象：

```json
{
  "T01ABC2DEF3": {
    "token": "xoxb-workspace-token-here",
    "team_name": "My Workspace"
  }
}
```

此文件中的令牌与通过 `SLACK_BOT_TOKEN` 指定的令牌合并。重复令牌会自动去重。

### 工作原理

- 列表中的**第一个令牌**是主令牌，用于 Socket Mode 连接（AsyncApp）。
- 每个令牌在启动时通过 `auth.test` 认证。网关将每个 `team_id` 映射到自己的 `WebClient` 和 `bot_user_id`。
- 当消息到达时，Hermes 使用正确的工作区特定客户端来响应。
- 主 `bot_user_id`（来自第一个令牌）用于与期望单一机器人身份的功能的向后兼容。

---

## 语音消息

Hermes 支持 Slack 上的语音功能：

- **接收：** 语音/音频消息使用配置的 STT 提供商自动转录：本地 `faster-whisper`、Groq Whisper（`GROQ_API_KEY`）或 OpenAI Whisper（`VOICE_TOOLS_OPENAI_KEY`）
- **发送：** TTS 回复以音频文件附件形式发送

---

## 频道专属提示词

为特定 Slack 频道分配临时系统提示词。提示词在每轮运行时注入 —— 永远不会持久化到对话记录 —— 因此更改会立即生效。

```yaml
slack:
  channel_prompts:
    "C01RESEARCH": |
      你是一个研究助手。专注于学术来源、
      引用和简洁的综合。
    "C02ENGINEERING": |
      代码审查模式。对边界情况和性能影响
      要精确。
```

键是 Slack 频道 ID（通过频道详情 → "About" → 滚动到底部找到）。匹配频道中的所有消息都会注入该提示词作为临时系统指令。

## 故障排除

| 问题 | 解决方案 |
|---------|----------|
| 机器人不响应私信 | 验证 `message.im` 在你的事件订阅中且应用已重新安装 |
| 机器人在私信中工作但在频道中不工作 | **最常见的问题。** 将 `message.channels` 和 `message.groups` 添加到事件订阅，重新安装应用，并用 `/invite @Hermes Agent` 邀请机器人到频道 |
| 机器人不响应频道中的 @提及 | 1) 检查 `message.channels` 事件是否已订阅。2) 机器人必须被邀请到频道。3) 确保添加了 `channels:history` 权限范围。4) 权限范围/事件更改后重新安装应用 |
| 机器人忽略私有频道中的消息 | 同时添加 `message.groups` 事件订阅和 `groups:history` 权限范围，然后重新安装应用并 `/invite` 机器人 |
| 私信中显示 "Sending messages to this app has been turned off" | 在 App Home 设置中启用 **Messages Tab**（见第五步） |
| "not_authed" 或 "invalid_auth" 错误 | 重新生成 Bot Token 和 App Token，更新 `.env` |
| 机器人响应但无法在频道中发帖 | 用 `/invite @Hermes Agent` 邀请机器人到频道 |
| "missing_scope" 错误 | 在 OAuth & Permissions 中添加所需的权限范围，然后**重新安装**应用 |
| Socket 频繁断开 | 检查网络；Bolt 自动重连但不稳定的连接会导致延迟 |
| 更改了权限范围/事件但没有变化 | 任何权限范围或事件订阅更改后，你**必须重新安装**应用到工作区 |

### 快速检查清单

如果机器人在频道中不工作，请验证以下**所有**条件：

1. ✅ `message.channels` 事件已订阅（公共频道）
2. ✅ `message.groups` 事件已订阅（私有频道）
3. ✅ `app_mention` 事件已订阅
4. ✅ `channels:history` 权限范围已添加（公共频道）
5. ✅ `groups:history` 权限范围已添加（私有频道）
6. ✅ 添加权限范围/事件后应用已**重新安装**
7. ✅ 机器人已被**邀请**到频道（`/invite @Hermes Agent`）
8. ✅ 你在消息中**@提及**了机器人

---

## 安全

:::warning
**始终设置 `SLACK_ALLOWED_USERS`**，填入授权用户的成员 ID。未设置此项时，网关将**默认拒绝所有消息**作为安全措施。永远不要分享你的 bot token ——
像对待密码一样对待它们。
:::

- 令牌应存储在 `~/.hermes/.env` 中（文件权限 `600`）
- 通过 Slack 应用设置定期轮换令牌
- 审计谁有权访问你的 Hermes 配置目录
- Socket Mode 意味着没有公共端点暴露 —— 少了一个攻击面
