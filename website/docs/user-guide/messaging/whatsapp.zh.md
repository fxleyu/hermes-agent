---
sidebar_position: 5
title: "WhatsApp"
description: "通过内置 Baileys 桥接将 Hermes Agent 设置为 WhatsApp 机器人"
---

# WhatsApp 设置

Hermes 通过基于 **Baileys** 的内置桥接连接到 WhatsApp。这通过模拟 WhatsApp Web 会话来工作 —— **不是**通过官方的 WhatsApp Business API。不需要 Meta 开发者账户或企业验证。

:::warning 非官方 API —— 封号风险
WhatsApp **不**官方支持 Business API 以外的第三方机器人。使用第三方桥接有少量账户受限风险。为降低风险：
- **使用专用手机号码**作为机器人（不要用你的个人号码）
- **不要发送批量/垃圾消息** —— 保持会话式使用
- **不要自动化外发消息**给未主动发消息的人
:::

:::warning WhatsApp Web 协议更新
WhatsApp 会定期更新其 Web 协议，这可能暂时破坏与第三方桥接的兼容性。发生这种情况时，Hermes 会更新桥接依赖。如果机器人在 WhatsApp 更新后停止工作，请拉取最新版本的 Hermes 并重新配对。
:::

## 两种模式

| 模式 | 工作方式 | 最适合 |
|------|-------------|----------|
| **独立机器人号码**（推荐） | 为机器人专用一个手机号码。人们直接给该号码发消息。 | 清晰的用户体验，多用户，较低封号风险 |
| **个人自聊** | 使用你自己的 WhatsApp。你给自己发消息与代理交谈。 | 快速设置，单用户，测试 |

---

## 前提条件

- **Node.js v18+** 和 **npm** —— WhatsApp 桥接作为 Node.js 进程运行
- **安装了 WhatsApp 的手机**（用于扫描二维码）

与旧的浏览器驱动桥接不同，当前基于 Baileys 的桥接**不**需要本地 Chromium 或 Puppeteer 依赖栈。

---

## 第一步：运行设置向导

```bash
hermes whatsapp
```

向导将：

1. 询问你想要哪种模式（**bot** 或 **self-chat**）
2. 如果需要，安装桥接依赖
3. 在终端中显示**二维码**
4. 等待你扫描

**扫描二维码：**

1. 打开手机上的 WhatsApp
2. 进入 **Settings → Linked Devices**
3. 点击 **Link a Device**
4. 将摄像头对准终端二维码

配对成功后，向导确认连接并退出。你的会话会自动保存。

:::tip
如果二维码显示乱码，确保你的终端至少 60 列宽并支持 Unicode。你也可以尝试不同的终端模拟器。
:::

---

## 第二步：获取第二个手机号码（Bot 模式）

对于 bot 模式，你需要一个尚未注册 WhatsApp 的手机号码。三种选择：

| 选项 | 费用 | 备注 |
|--------|------|-------|
| **Google Voice** | 免费 | 仅限美国。在 [voice.google.com](https://voice.google.com) 获取号码。通过 Google Voice 应用短信验证 WhatsApp。 |
| **预付费 SIM 卡** | 一次性 $5-15 | 任何运营商。激活，验证 WhatsApp，然后 SIM 卡可以放在抽屉里。号码需要保持活跃（每 90 天打一个电话）。 |
| **VoIP 服务** | 免费至 $5/月 | TextNow、TextFree 或类似服务。某些 VoIP 号码会被 WhatsApp 屏蔽 —— 如果第一个不行就多试几个。 |

获取号码后：

1. 在手机上安装 WhatsApp（或使用双 SIM 的 WhatsApp Business 应用）
2. 用新号码注册 WhatsApp
3. 运行 `hermes whatsapp` 并从该 WhatsApp 账户扫描二维码

---

## 第三步：配置 Hermes

将以下内容添加到 `~/.hermes/.env` 文件：

```bash
# 必需
WHATSAPP_ENABLED=true
WHATSAPP_MODE=bot                          # "bot" 或 "self-chat"

# 访问控制 -- 选择以下选项之一：
WHATSAPP_ALLOWED_USERS=15551234567         # 逗号分隔的手机号码（带国家代码，不带 +）
# WHATSAPP_ALLOWED_USERS=*                 # 或使用 * 允许所有人
# WHATSAPP_ALLOW_ALL_USERS=true            # 或设置此标志（与 * 效果相同）
```

:::tip 允许所有用户的简写
设置 `WHATSAPP_ALLOWED_USERS=*` 允许**所有**发送者（等同于 `WHATSAPP_ALLOW_ALL_USERS=true`）。
这与 [Signal 群组白名单](/docs/reference/environment-variables)一致。
要使用配对流程，请移除这两个变量并依赖
[DM 配对系统](/docs/user-guide/security#dm-pairing-system)。
:::

`~/.hermes/config.yaml` 中的可选行为设置：

```yaml
unauthorized_dm_behavior: pair

whatsapp:
  unauthorized_dm_behavior: ignore
```

- `unauthorized_dm_behavior: pair` 是全局默认值。未知的私信发送者会收到配对码。
- `whatsapp.unauthorized_dm_behavior: ignore` 使 WhatsApp 对未授权私信保持沉默，这对私人号码通常是更好的选择。

然后启动网关：

```bash
hermes gateway              # 前台运行
hermes gateway install      # 安装为用户服务
sudo hermes gateway install --system   # 仅 Linux：开机启动的系统服务
```

网关使用保存的会话自动启动 WhatsApp 桥接。

---

## 会话持久化

Baileys 桥接将会话保存在 `~/.hermes/platforms/whatsapp/session` 下。这意味着：

- **会话在重启后保留** —— 你不需要每次都重新扫描二维码
- 会话数据包含加密密钥和设备凭证
- **不要分享或提交此会话目录** —— 它授予对 WhatsApp 账户的完全访问权限

---

## 重新配对

如果会话断开（手机重置、WhatsApp 更新、手动解除关联），你会在网关日志中看到连接错误。修复方法：

```bash
hermes whatsapp
```

这会生成新的二维码。再次扫描即可重新建立会话。网关会自动处理**临时**断连（网络波动、手机短暂离线），通过重连逻辑自动恢复。

---

## 语音消息

Hermes 支持 WhatsApp 上的语音功能：

- **接收：** 语音消息（`.ogg` opus）使用配置的 STT 提供商自动转录：本地 `faster-whisper`、Groq Whisper（`GROQ_API_KEY`）或 OpenAI Whisper（`VOICE_TOOLS_OPENAI_KEY`）
- **发送：** TTS 回复以 MP3 音频文件附件形式发送
- 代理回复默认以 "⚕ **Hermes Agent**" 为前缀。你可以在 `config.yaml` 中自定义或禁用：

```yaml
# ~/.hermes/config.yaml
whatsapp:
  reply_prefix: ""                          # 空字符串禁用前缀
  # reply_prefix: "🤖 *My Bot*\n──────\n"  # 自定义前缀（支持 \n 换行）
```

---

## 消息格式化与发送

WhatsApp 支持**流式（渐进式）回复** —— 机器人在 AI 生成文本时实时编辑消息，就像 Discord 和 Telegram 一样。内部而言，WhatsApp 被分类为 TIER_MEDIUM 平台以定义发送能力。

### 分块

长回复自动按每块 **4,096 字符**拆分（WhatsApp 的实际显示限制）。你不需要配置任何东西 —— 网关处理拆分并按顺序发送块。

### WhatsApp 兼容的 Markdown

AI 回复中的标准 Markdown 自动转换为 WhatsApp 的原生格式：

| Markdown | WhatsApp | 渲染效果 |
|----------|----------|------------|
| `**bold**` | `*bold*` | **粗体** |
| `~~strikethrough~~` | `~strikethrough~` | ~~删除线~~ |
| `# Heading` | `*Heading*` | 粗体文本（无原生标题） |
| `[link text](url)` | `link text (url)` | 内联 URL |

代码块和内联代码原样保留，因为 WhatsApp 原生支持三反引号格式。

### 工具进度

当代理调用工具（网络搜索、文件操作等）时，WhatsApp 显示实时进度指示器，显示正在运行的工具。这默认启用 —— 无需配置。

---

## 故障排除

| 问题 | 解决方案 |
|---------|----------|
| **二维码无法扫描** | 确保终端足够宽（60+ 列）。尝试不同的终端。确保你是从正确的 WhatsApp 账户扫描（机器人号码，不是个人号码）。 |
| **二维码过期** | 二维码大约每 20 秒刷新一次。如果超时，重新运行 `hermes whatsapp`。 |
| **会话不持久** | 检查 `~/.hermes/platforms/whatsapp/session` 是否存在且可写。如果容器化，将其挂载为持久卷。 |
| **意外退出登录** | WhatsApp 在长时间不活跃后会解除设备关联。保持手机开启并连接网络，然后如需要用 `hermes whatsapp` 重新配对。 |
| **桥接崩溃或重连循环** | 重启网关，更新 Hermes，如果 WhatsApp 协议变更导致会话失效则重新配对。 |
| **WhatsApp 更新后机器人停止工作** | 更新 Hermes 以获取最新桥接版本，然后重新配对。 |
| **macOS："Node.js not installed" 但终端中 node 可用** | launchd 服务不继承你的 shell PATH。运行 `hermes gateway install` 将当前 PATH 快照到 plist 中，然后 `hermes gateway start`。详见[网关服务文档](./index.md#macos-launchd)。 |
| **消息未被接收** | 验证 `WHATSAPP_ALLOWED_USERS` 包含发送者的号码（带国家代码，不带 `+` 或空格），或设为 `*` 允许所有人。在 `.env` 中设置 `WHATSAPP_DEBUG=true` 并重启网关以在 `bridge.log` 中查看原始消息事件。 |
| **机器人给陌生人回复配对码** | 如果你想让未授权私信被静默忽略，在 `~/.hermes/config.yaml` 中设置 `whatsapp.unauthorized_dm_behavior: ignore`。 |

---

## 安全

:::warning
**上线前配置访问控制。** 设置 `WHATSAPP_ALLOWED_USERS` 为特定手机号码（包含国家代码，不带 `+`），使用 `*` 允许所有人，或设置 `WHATSAPP_ALLOW_ALL_USERS=true`。没有这些设置，网关会**拒绝所有入站消息**作为安全措施。
:::

默认情况下，未授权的私信仍会收到配对码回复。如果你想让私人 WhatsApp 号码对陌生人完全保持沉默，设置：

```yaml
whatsapp:
  unauthorized_dm_behavior: ignore
```

- `~/.hermes/platforms/whatsapp/session` 目录包含完整的会话凭证 —— 像密码一样保护它
- 设置文件权限：`chmod 700 ~/.hermes/platforms/whatsapp/session`
- 使用**专用手机号码**作为机器人以隔离个人账户风险
- 如果你怀疑泄露，从 WhatsApp → Settings → Linked Devices 解除设备关联
- 日志中的手机号码会部分遮蔽，但请审查你的日志保留策略
