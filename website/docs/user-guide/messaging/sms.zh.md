---
sidebar_position: 8
sidebar_label: "SMS (Twilio)"
title: "SMS (Twilio)"
description: "通过 Twilio 将 Hermes Agent 设置为短信聊天机器人"
---

# SMS 设置 (Twilio)

Hermes 通过 [Twilio](https://www.twilio.com/) API 连接到 SMS。人们给你的 Twilio 手机号码发短信，然后收到 AI 回复 —— 与 Telegram 或 Discord 相同的对话体验，但通过标准短信。

:::info 共享凭证
SMS 网关与可选的[电话技能](/docs/reference/skills-catalog)共享凭证。如果你已经为语音通话或一次性短信设置了 Twilio，网关使用相同的 `TWILIO_ACCOUNT_SID`、`TWILIO_AUTH_TOKEN` 和 `TWILIO_PHONE_NUMBER`。
:::

---

## 前提条件

- **Twilio 账户** —— [在 twilio.com 注册](https://www.twilio.com/try-twilio)（有免费试用）
- **具有 SMS 能力的 Twilio 手机号码**
- **可公开访问的服务器** —— Twilio 在短信到达时向你的服务器发送 webhook
- **aiohttp** —— `pip install 'hermes-agent[sms]'`

---

## 第一步：获取 Twilio 凭证

1. 访问 [Twilio 控制台](https://console.twilio.com/)
2. 从仪表板复制你的 **Account SID** 和 **Auth Token**
3. 进入 **Phone Numbers → Manage → Active Numbers** —— 记下你的手机号码（E.164 格式，例如 `+15551234567`）

---

## 第二步：配置 Hermes

### 交互式设置（推荐）

```bash
hermes gateway setup
```

从平台列表选择 **SMS (Twilio)**。向导将提示你输入凭证。

### 手动设置

添加到 `~/.hermes/.env`：

```bash
TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=your_auth_token_here
TWILIO_PHONE_NUMBER=+15551234567

# 安全：限制特定手机号码（推荐）
SMS_ALLOWED_USERS=+15559876543,+15551112222

# 可选：设置 cron 任务发送的主频道
SMS_HOME_CHANNEL=+15559876543
```

---

## 第三步：配置 Twilio Webhook

Twilio 需要知道将入站消息发送到哪里。在 [Twilio 控制台](https://console.twilio.com/) 中：

1. 进入 **Phone Numbers → Manage → Active Numbers**
2. 点击你的手机号码
3. 在 **Messaging → A MESSAGE COMES IN** 下，设置：
   - **Webhook**：`https://your-server:8080/webhooks/twilio`
   - **HTTP Method**：`POST`

:::tip 暴露你的 Webhook
如果你在本地运行 Hermes，使用隧道暴露 webhook：

```bash
# 使用 cloudflared
cloudflared tunnel --url http://localhost:8080

# 使用 ngrok
ngrok http 8080
```

将生成的公共 URL 设为你的 Twilio webhook。
:::

**将 `SMS_WEBHOOK_URL` 设置为你在 Twilio 中配置的相同 URL。** 这是 Twilio 签名验证所必需的 —— 适配器没有它将拒绝启动：

```bash
# 必须匹配你的 Twilio 控制台中的 webhook URL
SMS_WEBHOOK_URL=https://your-server:8080/webhooks/twilio
```

webhook 端口默认为 `8080`。可通过以下方式覆盖：

```bash
SMS_WEBHOOK_PORT=3000
```

---

## 第四步：启动网关

```bash
hermes gateway
```

你应该看到：

```
[sms] Twilio webhook server listening on 0.0.0.0:8080, from: +1555***4567
```

如果看到 `Refusing to start: SMS_WEBHOOK_URL is required`，请将 `SMS_WEBHOOK_URL` 设置为 Twilio 控制台中配置的公共 URL（见第三步）。

给你的 Twilio 号码发条短信 —— Hermes 将通过 SMS 回复。

---

## 环境变量

| 变量 | 必需 | 描述 |
|----------|----------|-------------|
| `TWILIO_ACCOUNT_SID` | 是 | Twilio Account SID（以 `AC` 开头） |
| `TWILIO_AUTH_TOKEN` | 是 | Twilio Auth Token（也用于 webhook 签名验证） |
| `TWILIO_PHONE_NUMBER` | 是 | 你的 Twilio 手机号码（E.164 格式） |
| `SMS_WEBHOOK_URL` | 是 | 用于 Twilio 签名验证的公共 URL —— 必须匹配 Twilio 控制台中的 webhook URL |
| `SMS_WEBHOOK_PORT` | 否 | Webhook 监听端口（默认：`8080`） |
| `SMS_WEBHOOK_HOST` | 否 | Webhook 绑定地址（默认：`0.0.0.0`） |
| `SMS_INSECURE_NO_SIGNATURE` | 否 | 设为 `true` 禁用签名验证（仅限本地开发 —— **不用于生产**） |
| `SMS_ALLOWED_USERS` | 否 | 允许聊天的逗号分隔的 E.164 手机号码 |
| `SMS_ALLOW_ALL_USERS` | 否 | 设为 `true` 允许任何人（不推荐） |
| `SMS_HOME_CHANNEL` | 否 | cron 任务/通知发送的手机号码 |
| `SMS_HOME_CHANNEL_NAME` | 否 | 主频道的显示名称（默认：`Home`） |

---

## SMS 特定行为

- **仅纯文本** —— Markdown 会自动去除，因为 SMS 会将其渲染为字面字符
- **1600 字符限制** —— 更长的回复会在自然边界（换行符、然后空格）处拆分为多条消息
- **回声防护** —— 来自你自己 Twilio 号码的消息会被忽略以防止循环
- **手机号码脱敏** —— 日志中的手机号码会被脱敏以保护隐私

---

## 安全

### Webhook 签名验证

Hermes 通过验证 `X-Twilio-Signature` 头（HMAC-SHA1）来确认入站 webhook 确实来自 Twilio。这防止攻击者注入伪造消息。

**`SMS_WEBHOOK_URL` 是必需的。** 将其设置为 Twilio 控制台中配置的公共 URL。适配器没有它将拒绝启动。

对于没有公共 URL 的本地开发，你可以禁用验证：

```bash
# 仅限本地开发 -- 不用于生产
SMS_INSECURE_NO_SIGNATURE=true
```

### 用户白名单

**网关默认拒绝所有用户。** 配置白名单：

```bash
# 推荐：限制特定手机号码
SMS_ALLOWED_USERS=+15559876543,+15551112222

# 或允许所有人（不推荐用于具有终端访问的机器人）
SMS_ALLOW_ALL_USERS=true
```

:::warning
SMS 没有内置加密。除非你了解安全影响，否则不要使用 SMS 进行敏感操作。对于敏感场景，优先使用 Signal 或 Telegram。
:::

---

## 故障排除

### 消息未到达

1. 检查你的 Twilio webhook URL 是否正确且可公开访问
2. 验证 `TWILIO_ACCOUNT_SID` 和 `TWILIO_AUTH_TOKEN` 是否正确
3. 检查 Twilio 控制台 → **Monitor → Logs → Messaging** 中的发送错误
4. 确保你的手机号码在 `SMS_ALLOWED_USERS` 中（或 `SMS_ALLOW_ALL_USERS=true`）

### 回复未发送

1. 检查 `TWILIO_PHONE_NUMBER` 是否正确设置（E.164 格式带 `+`）
2. 验证你的 Twilio 账户有具有 SMS 能力的号码
3. 检查 Hermes 网关日志中的 Twilio API 错误

### Webhook 端口冲突

如果端口 8080 已被占用，更改它：

```bash
SMS_WEBHOOK_PORT=3001
```

在 Twilio 控制台中更新 webhook URL 以匹配。
