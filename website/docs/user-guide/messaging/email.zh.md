---
sidebar_position: 7
title: "Email"
description: "通过 IMAP/SMTP 将 Hermes Agent 设置为电子邮件助手"
---

# 电子邮件设置

Hermes 可以使用标准 IMAP 和 SMTP 协议接收和回复电子邮件。向代理的邮箱地址发送邮件，它会在邮件线程中回复 —— 不需要特殊的客户端或机器人 API。适用于 Gmail、Outlook、Yahoo、Fastmail 或任何支持 IMAP/SMTP 的提供商。

:::info 无外部依赖
Email 适配器使用 Python 内置的 `imaplib`、`smtplib` 和 `email` 模块。不需要额外的包或外部服务。
:::

---

## 前提条件

- **一个专用邮箱账户**用于 Hermes 代理（不要使用你的个人邮箱）
- 邮箱账户上**启用了 IMAP**
- 如果使用 Gmail 或其他启用了 2FA 的提供商，需要**应用专用密码**

### Gmail 设置

1. 在你的 Google 账户上启用两步验证
2. 访问[应用专用密码](https://myaccount.google.com/apppasswords)
3. 创建新的应用专用密码（选择 "Mail" 或 "Other"）
4. 复制 16 位密码 —— 你将用它替代常规密码

### Outlook / Microsoft 365

1. 访问[安全设置](https://account.microsoft.com/security)
2. 如果尚未启用，启用 2FA
3. 在 "Additional security options" 下创建应用专用密码
4. IMAP 主机：`outlook.office365.com`，SMTP 主机：`smtp.office365.com`

### 其他提供商

大多数邮件提供商都支持 IMAP/SMTP。查看你的提供商文档了解：
- IMAP 主机和端口（通常是端口 993 使用 SSL）
- SMTP 主机和端口（通常是端口 587 使用 STARTTLS）
- 是否需要应用专用密码

---

## 第一步：配置 Hermes

最简单的方式：

```bash
hermes gateway setup
```

从平台菜单选择 **Email**。向导会提示你输入邮箱地址、密码、IMAP/SMTP 主机和允许的发送者。

### 手动配置

添加到 `~/.hermes/.env`：

```bash
# 必需
EMAIL_ADDRESS=hermes@gmail.com
EMAIL_PASSWORD=abcd efgh ijkl mnop    # 应用专用密码（不是你的常规密码）
EMAIL_IMAP_HOST=imap.gmail.com
EMAIL_SMTP_HOST=smtp.gmail.com

# 安全（推荐）
EMAIL_ALLOWED_USERS=your@email.com,colleague@work.com

# 可选
EMAIL_IMAP_PORT=993                    # 默认：993（IMAP SSL）
EMAIL_SMTP_PORT=587                    # 默认：587（SMTP STARTTLS）
EMAIL_POLL_INTERVAL=15                 # 收件箱检查间隔秒数（默认：15）
EMAIL_HOME_ADDRESS=your@email.com      # cron 任务的默认发送目标
```

---

## 第二步：启动网关

```bash
hermes gateway              # 前台运行
hermes gateway install      # 安装为用户服务
sudo hermes gateway install --system   # 仅 Linux：开机启动的系统服务
```

启动时，适配器会：
1. 测试 IMAP 和 SMTP 连接
2. 将所有现有收件箱消息标记为 "已读"（只处理新邮件）
3. 开始轮询新消息

---

## 工作原理

### 接收消息

适配器以可配置的间隔（默认：15 秒）轮询 IMAP 收件箱中的 UNSEEN 消息。对于每封新邮件：

- **主题行**作为上下文包含（例如 `[Subject: Deploy to production]`）
- **回复邮件**（主题以 `Re:` 开头）跳过主题前缀 —— 线程上下文已建立
- **附件**在本地缓存：
  - 图片（JPEG、PNG、GIF、WebP）→ 可用于视觉工具
  - 文档（PDF、ZIP 等）→ 可用于文件访问
- **纯 HTML 邮件**会去除标签以提取纯文本
- **自身消息**会被过滤以防止回复循环
- **自动化/noreply 发送者**会被静默忽略 —— `noreply@`、`mailer-daemon@`、`bounce@`、`no-reply@`，以及带有 `Auto-Submitted`、`Precedence: bulk` 或 `List-Unsubscribe` 头的邮件

### 发送回复

回复通过 SMTP 发送，并正确维护邮件线程：

- **In-Reply-To** 和 **References** 头维护线程
- **主题行**保留 `Re:` 前缀（不会出现重复的 `Re: Re:`）
- **Message-ID** 使用代理的域名生成
- 回复以纯文本（UTF-8）形式发送

### 文件附件

代理可以在回复中发送文件附件。在响应中包含 `MEDIA:/path/to/file`，文件将附加到外发邮件中。

### 跳过附件

要忽略所有入站附件（用于恶意软件防护或节省带宽），在 `config.yaml` 中添加：

```yaml
platforms:
  email:
    skip_attachments: true
```

启用后，附件和内联部分在载荷解码前被跳过。邮件正文文本仍正常处理。

---

## 访问控制

邮件访问遵循与所有其他 Hermes 平台相同的模式：

1. **设置了 `EMAIL_ALLOWED_USERS`** → 只处理来自这些地址的邮件
2. **未设置白名单** → 未知发送者收到配对码
3. **`EMAIL_ALLOW_ALL_USERS=true`** → 接受任何发送者（谨慎使用）

:::warning
**始终配置 `EMAIL_ALLOWED_USERS`。** 未设置时，任何知道代理邮箱地址的人都可以发送命令。代理默认具有终端访问权限。
:::

---

## 故障排除

| 问题 | 解决方案 |
|---------|----------|
| **启动时 "IMAP connection failed"** | 验证 `EMAIL_IMAP_HOST` 和 `EMAIL_IMAP_PORT`。确保账户已启用 IMAP。对于 Gmail，在 Settings → Forwarding and POP/IMAP 中启用。 |
| **启动时 "SMTP connection failed"** | 验证 `EMAIL_SMTP_HOST` 和 `EMAIL_SMTP_PORT`。检查密码是否正确（Gmail 使用应用专用密码）。 |
| **消息未被接收** | 检查 `EMAIL_ALLOWED_USERS` 是否包含发送者的邮箱。检查垃圾邮件文件夹 —— 某些提供商会标记自动回复。 |
| **"Authentication failed"** | 对于 Gmail，你必须使用应用专用密码，而非常规密码。确保先启用 2FA。 |
| **重复回复** | 确保只有一个网关实例在运行。检查 `hermes gateway status`。 |
| **响应慢** | 默认轮询间隔为 15 秒。用 `EMAIL_POLL_INTERVAL=5` 减少以加快响应（但会有更多 IMAP 连接）。 |
| **回复未线程化** | 适配器使用 In-Reply-To 头。某些邮件客户端（尤其是网页版）可能无法正确将自动消息线程化。 |

---

## 安全

:::warning
**使用专用邮箱账户。** 不要使用你的个人邮箱 —— 代理将密码存储在 `.env` 中，并通过 IMAP 拥有完整的收件箱访问权限。
:::

- 使用**应用专用密码**而非你的主密码（Gmail 启用 2FA 时必需）
- 设置 `EMAIL_ALLOWED_USERS` 限制谁可以与代理交互
- 密码存储在 `~/.hermes/.env` 中 —— 保护此文件（`chmod 600`）
- IMAP 默认使用 SSL（端口 993），SMTP 默认使用 STARTTLS（端口 587）—— 连接已加密

---

## 环境变量参考

| 变量 | 必需 | 默认值 | 描述 |
|----------|----------|---------|-------------|
| `EMAIL_ADDRESS` | 是 | — | 代理的邮箱地址 |
| `EMAIL_PASSWORD` | 是 | — | 邮箱密码或应用专用密码 |
| `EMAIL_IMAP_HOST` | 是 | — | IMAP 服务器主机（例如 `imap.gmail.com`） |
| `EMAIL_SMTP_HOST` | 是 | — | SMTP 服务器主机（例如 `smtp.gmail.com`） |
| `EMAIL_IMAP_PORT` | 否 | `993` | IMAP 服务器端口 |
| `EMAIL_SMTP_PORT` | 否 | `587` | SMTP 服务器端口 |
| `EMAIL_POLL_INTERVAL` | 否 | `15` | 收件箱检查间隔秒数 |
| `EMAIL_ALLOWED_USERS` | 否 | — | 逗号分隔的允许发送者地址 |
| `EMAIL_HOME_ADDRESS` | 否 | — | cron 任务的默认发送目标 |
| `EMAIL_ALLOW_ALL_USERS` | 否 | `false` | 允许所有发送者（不推荐） |
