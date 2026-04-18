---
sidebar_position: 1
title: "Telegram"
description: "将 Hermes Agent 设置为 Telegram 机器人"
---

# Telegram 设置

Hermes Agent 可与 Telegram 集成为功能完善的对话机器人。连接后，你可以从任何设备与代理聊天，发送语音消息并自动转录文字，接收定时任务结果，以及在群聊中使用代理。该集成基于 [python-telegram-bot](https://python-telegram-bot.org/) 构建，支持文本、语音、图片和文件附件。

## 第一步：通过 BotFather 创建机器人

每个 Telegram 机器人都需要一个由 [@BotFather](https://t.me/BotFather) 颁发的 API 令牌，BotFather 是 Telegram 的官方机器人管理工具。

1. 打开 Telegram，搜索 **@BotFather**，或访问 [t.me/BotFather](https://t.me/BotFather)
2. 发送 `/newbot`
3. 选择一个**显示名称**（例如 "Hermes Agent"）—— 可以是任意名称
4. 选择一个**用户名** —— 必须唯一且以 `bot` 结尾（例如 `my_hermes_bot`）
5. BotFather 会回复你的 **API 令牌**。格式如下：

```
123456789:ABCdefGHIjklMNOpqrSTUvwxYZ
```

:::warning
请妥善保管你的机器人令牌。任何拥有此令牌的人都可以控制你的机器人。如果泄露，请立即通过 BotFather 的 `/revoke` 命令撤销。
:::

## 第二步：自定义你的机器人（可选）

以下 BotFather 命令可以改善用户体验。向 @BotFather 发送消息并使用：

| 命令 | 用途 |
|---------|---------|
| `/setdescription` | 用户开始聊天前显示的 "这个机器人能做什么？" 文本 |
| `/setabouttext` | 机器人个人资料页上的简短文本 |
| `/setuserpic` | 为机器人上传头像 |
| `/setcommands` | 定义命令菜单（聊天中的 `/` 按钮） |
| `/setprivacy` | 控制机器人是否能看到所有群组消息（见第三步） |

:::tip
对于 `/setcommands`，推荐的初始命令集：

```
help - 显示帮助信息
new - 开始新对话
sethome - 将此聊天设为主频道
```
:::

## 第三步：隐私模式（群组使用的关键设置）

Telegram 机器人有一个**默认启用**的**隐私模式**。这是在群组中使用机器人时最常见的困惑来源。

**隐私模式开启时**，你的机器人只能看到：
- 以 `/` 命令开头的消息
- 直接回复机器人自身消息的回复
- 服务消息（成员加入/离开、置顶消息等）
- 机器人作为管理员的频道中的消息

**隐私模式关闭时**，机器人可以接收群组中的每条消息。

### 如何关闭隐私模式

1. 向 **@BotFather** 发消息
2. 发送 `/mybots`
3. 选择你的机器人
4. 进入 **Bot Settings → Group Privacy → Turn off**

:::warning
**更改隐私设置后，你必须将机器人从群组中移除并重新添加。** Telegram 在机器人加入群组时缓存隐私状态，在机器人被移除并重新添加之前不会更新。
:::

:::tip
关闭隐私模式的替代方案：将机器人提升为**群组管理员**。管理员机器人无论隐私设置如何都能接收所有消息，这样就不需要切换全局隐私模式。
:::

## 第四步：找到你的用户 ID

Hermes Agent 使用数字 Telegram 用户 ID 来控制访问权限。你的用户 ID **不是**你的用户名 —— 它是一个类似 `123456789` 的数字。

**方法 1（推荐）：** 向 [@userinfobot](https://t.me/userinfobot) 发送消息 —— 它会立即回复你的用户 ID。

**方法 2：** 向 [@get_id_bot](https://t.me/get_id_bot) 发送消息 —— 另一个可靠的选择。

保存这个数字，下一步会用到。

## 第五步：配置 Hermes

### 选项 A：交互式设置（推荐）

```bash
hermes gateway setup
```

出现提示时选择 **Telegram**。向导会询问你的机器人令牌和允许的用户 ID，然后为你写入配置。

### 选项 B：手动配置

将以下内容添加到 `~/.hermes/.env`：

```bash
TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrSTUvwxYZ
TELEGRAM_ALLOWED_USERS=123456789    # 多个用户用逗号分隔
```

### 启动网关

```bash
hermes gateway
```

机器人应该会在几秒内上线。在 Telegram 上给它发条消息来验证。

## Webhook 模式

默认情况下，Hermes 使用**长轮询**连接 Telegram —— 网关向 Telegram 服务器发出出站请求以获取新更新。这适用于本地和常驻部署。

对于**云部署**（Fly.io、Railway、Render 等），**webhook 模式**更经济。这些平台可以在收到入站 HTTP 流量时自动唤醒暂停的机器，但无法在出站连接时唤醒。由于轮询是出站的，轮询机器人永远无法休眠。Webhook 模式翻转了方向 —— Telegram 将更新推送到你机器人的 HTTPS URL，从而实现空闲时休眠的部署。

| | 轮询（默认） | Webhook |
|---|---|---|
| 方向 | 网关 → Telegram（出站） | Telegram → 网关（入站） |
| 最适合 | 本地、常驻服务器 | 带自动唤醒的云平台 |
| 设置 | 无需额外配置 | 设置 `TELEGRAM_WEBHOOK_URL` |
| 空闲成本 | 机器必须保持运行 | 消息间机器可以休眠 |

### 配置

将以下内容添加到 `~/.hermes/.env`：

```bash
TELEGRAM_WEBHOOK_URL=https://my-app.fly.dev/telegram
# TELEGRAM_WEBHOOK_PORT=8443        # 可选，默认 8443
# TELEGRAM_WEBHOOK_SECRET=mysecret  # 可选，推荐使用
```

| 变量 | 必需 | 描述 |
|----------|----------|-------------|
| `TELEGRAM_WEBHOOK_URL` | 是 | Telegram 发送更新的公共 HTTPS URL。URL 路径会自动提取（例如上面示例中的 `/telegram`）。 |
| `TELEGRAM_WEBHOOK_PORT` | 否 | webhook 服务器监听的本地端口（默认：`8443`）。 |
| `TELEGRAM_WEBHOOK_SECRET` | 否 | 用于验证更新确实来自 Telegram 的密钥令牌。**强烈建议**在生产部署中使用。 |

设置了 `TELEGRAM_WEBHOOK_URL` 时，网关会启动 HTTP webhook 服务器而不是轮询。未设置时使用轮询模式 —— 与之前版本行为无变化。

### 云部署示例（Fly.io）

1. 将环境变量添加到 Fly.io 应用密钥中：

```bash
fly secrets set TELEGRAM_WEBHOOK_URL=https://my-app.fly.dev/telegram
fly secrets set TELEGRAM_WEBHOOK_SECRET=$(openssl rand -hex 32)
```

2. 在 `fly.toml` 中暴露 webhook 端口：

```toml
[[services]]
  internal_port = 8443
  protocol = "tcp"

  [[services.ports]]
    handlers = ["tls", "http"]
    port = 443
```

3. 部署：

```bash
fly deploy
```

网关日志应显示：`[telegram] Connected to Telegram (webhook mode)`。

## 代理支持

如果 Telegram API 被阻断或你需要通过代理路由流量，请设置 Telegram 专用代理 URL。这会优先于通用的 `HTTPS_PROXY` / `HTTP_PROXY` 环境变量。

**选项 1：config.yaml（推荐）**

```yaml
telegram:
  proxy_url: "socks5://127.0.0.1:1080"
```

**选项 2：环境变量**

```bash
TELEGRAM_PROXY=socks5://127.0.0.1:1080
```

支持的协议：`http://`、`https://`、`socks5://`。

代理适用于主 Telegram 连接和备用 IP 传输。如果未设置 Telegram 专用代理，网关会回退到 `HTTPS_PROXY` / `HTTP_PROXY` / `ALL_PROXY`（或 macOS 系统代理自动检测）。

## 主频道

在任何 Telegram 聊天（私聊或群组）中使用 `/sethome` 命令将其指定为**主频道**。定时任务（cron 任务）会将结果发送到此频道。

你也可以在 `~/.hermes/.env` 中手动设置：

```bash
TELEGRAM_HOME_CHANNEL=-1001234567890
TELEGRAM_HOME_CHANNEL_NAME="My Notes"
```

:::tip
群聊 ID 是负数（例如 `-1001234567890`）。你的私聊 ID 与用户 ID 相同。
:::

## 语音消息

### 接收语音（语音转文字）

你在 Telegram 上发送的语音消息会由 Hermes 配置的 STT 提供商自动转录，并作为文本注入对话。

- `local` 使用运行 Hermes 机器上的 `faster-whisper` —— 无需 API 密钥
- `groq` 使用 Groq Whisper，需要 `GROQ_API_KEY`
- `openai` 使用 OpenAI Whisper，需要 `VOICE_TOOLS_OPENAI_KEY`

### 发送语音（文字转语音）

当代理通过 TTS 生成音频时，会以原生 Telegram **语音气泡**形式发送 —— 圆形、可内联播放的那种。

- **OpenAI 和 ElevenLabs** 原生产出 Opus —— 无需额外设置
- **Edge TTS**（默认的免费提供商）输出 MP3，需要 **ffmpeg** 转换为 Opus：

```bash
# Ubuntu/Debian
sudo apt install ffmpeg

# macOS
brew install ffmpeg
```

没有 ffmpeg 时，Edge TTS 音频会作为普通音频文件发送（仍可播放，但使用矩形播放器而非语音气泡）。

在 `config.yaml` 中的 `tts.provider` 键下配置 TTS 提供商。

## 群聊使用

Hermes Agent 可在 Telegram 群聊中工作，但有一些注意事项：

- **隐私模式**决定机器人能看到哪些消息（见[第三步](#step-3-privacy-mode-critical-for-groups)）
- `TELEGRAM_ALLOWED_USERS` 仍然适用 —— 即使在群组中，只有授权用户才能触发机器人
- 你可以通过 `telegram.require_mention: true` 防止机器人回应普通群聊
- 设置 `telegram.require_mention: true` 时，群组消息在以下情况下会被接受：
  - 斜杠命令
  - 直接回复机器人消息的回复
  - `@botusername` 提及
  - 匹配 `telegram.mention_patterns` 中配置的正则唤醒词
- 使用 `telegram.ignored_threads` 可以让 Hermes 在特定 Telegram 论坛话题中保持沉默，即使群组原本允许自由回复或提及触发回复
- 如果 `telegram.require_mention` 未设置或为 false，Hermes 保持之前的开放群组行为，回应它能看到的普通群组消息

### 群组触发配置示例

将以下内容添加到 `~/.hermes/config.yaml`：

```yaml
telegram:
  require_mention: true
  mention_patterns:
    - "^\\s*chompy\\b"
  ignored_threads:
    - 31
    - "42"
```

此示例除了允许所有常规直接触发外，还允许以 `chompy` 开头的消息，即使它们没有使用 `@mention`。
话题 `31` 和 `42` 中的消息在提及和自由回复检查运行之前总是被忽略。

### `mention_patterns` 注意事项

- 模式使用 Python 正则表达式
- 匹配不区分大小写
- 模式会检查文本消息和媒体标题
- 无效的正则模式会在网关日志中发出警告而忽略，不会导致机器人崩溃
- 如果你想让模式只匹配消息开头，请用 `^` 锚定

## 私聊话题（Bot API 9.4）

Telegram Bot API 9.4（2026 年 2 月）引入了**私聊话题** —— 机器人可以直接在一对一私聊中创建论坛式话题线程，无需超级群组。这让你可以在与 Hermes 的现有私聊中运行多个隔离的工作区。

### 使用场景

如果你同时进行多个长期项目，话题可以保持上下文分离：

- **话题 "Website"** —— 处理你的生产 Web 服务
- **话题 "Research"** —— 文献综述和论文探索
- **话题 "General"** —— 杂项任务和快速提问

每个话题都有自己的对话会话、历史记录和上下文 —— 彼此完全隔离。

### 配置

在 `~/.hermes/config.yaml` 中的 `platforms.telegram.extra.dm_topics` 下添加话题：

```yaml
platforms:
  telegram:
    extra:
      dm_topics:
      - chat_id: 123456789        # 你的 Telegram 用户 ID
        topics:
        - name: General
          icon_color: 7322096
        - name: Website
          icon_color: 9367192
        - name: Research
          icon_color: 16766590
          skill: arxiv              # 在此话题中自动加载技能
```

**字段：**

| 字段 | 必需 | 描述 |
|-------|----------|-------------|
| `name` | 是 | 话题显示名称 |
| `icon_color` | 否 | Telegram 图标颜色代码（整数） |
| `icon_custom_emoji_id` | 否 | 话题图标的自定义表情 ID |
| `skill` | 否 | 在此话题的新会话中自动加载的技能 |
| `thread_id` | 否 | 话题创建后自动填充 —— 不要手动设置 |

### 工作原理

1. 网关启动时，Hermes 为每个尚未有 `thread_id` 的话题调用 `createForumTopic`
2. `thread_id` 会自动保存回 `config.yaml` —— 后续重启跳过 API 调用
3. 每个话题映射到隔离的会话键：`agent:main:telegram:dm:{chat_id}:{thread_id}`
4. 每个话题中的消息有自己的对话历史、内存刷新和上下文窗口

### 技能绑定

带有 `skill` 字段的话题在该话题中开始新会话时会自动加载该技能。这与在对话开始时输入 `/skill-name` 完全相同 —— 技能内容被注入到第一条消息中，后续消息在对话历史中可以看到它。

例如，设置了 `skill: arxiv` 的话题在会话重置时（由于空闲超时、每日重置或手动 `/reset`）会预加载 arxiv 技能。

:::tip
在配置之外创建的话题（例如通过手动调用 Telegram API）在 `forum_topic_created` 服务消息到达时会被自动发现。你也可以在网关运行时向配置添加话题 —— 它们会在下次缓存未命中时被获取。
:::

## 群组论坛话题技能绑定

启用了**话题模式**（也称为 "论坛话题"）的超级群组已经获得每个话题的会话隔离 —— 每个 `thread_id` 映射到自己的对话。但你可能想在特定群组话题中收到消息时**自动加载技能**，就像私聊话题技能绑定一样。

### 使用场景

一个为不同工作流设置论坛话题的团队超级群组：

- **Engineering** 话题 → 自动加载 `software-development` 技能
- **Research** 话题 → 自动加载 `arxiv` 技能
- **General** 话题 → 无技能，通用助手

### 配置

在 `~/.hermes/config.yaml` 中的 `platforms.telegram.extra.group_topics` 下添加话题绑定：

```yaml
platforms:
  telegram:
    extra:
      group_topics:
      - chat_id: -1001234567890       # 超级群组 ID
        topics:
        - name: Engineering
          thread_id: 5
          skill: software-development
        - name: Research
          thread_id: 12
          skill: arxiv
        - name: General
          thread_id: 1
          # 无技能 -- 通用用途
```

**字段：**

| 字段 | 必需 | 描述 |
|-------|----------|-------------|
| `chat_id` | 是 | 超级群组的数字 ID（以 `-100` 开头的负数） |
| `name` | 否 | 话题的可读标签（仅供参考） |
| `thread_id` | 是 | Telegram 论坛话题 ID —— 在 `t.me/c/<group_id>/<thread_id>` 链接中可见 |
| `skill` | 否 | 在此话题的新会话中自动加载的技能 |

### 工作原理

1. 当消息到达已映射的群组话题时，Hermes 在 `group_topics` 配置中查找 `chat_id` 和 `thread_id`
2. 如果匹配的条目有 `skill` 字段，该技能会为会话自动加载 —— 与私聊话题技能绑定相同
3. 没有 `skill` 键的话题只获得会话隔离（现有行为，不变）
4. 未映射的 `thread_id` 值或 `chat_id` 值会静默通过 —— 无错误、无技能

### 与私聊话题的区别

| | 私聊话题 | 群组话题 |
|---|---|---|
| 配置键 | `extra.dm_topics` | `extra.group_topics` |
| 话题创建 | Hermes 在缺少 `thread_id` 时通过 API 创建话题 | 管理员在 Telegram 界面中创建话题 |
| `thread_id` | 创建后自动填充 | 必须手动设置 |
| `icon_color` / `icon_custom_emoji_id` | 支持 | 不适用（管理员控制外观） |
| 技能绑定 | ✓ | ✓ |
| 会话隔离 | ✓ | ✓（论坛话题已内置） |

:::tip
要查找话题的 `thread_id`，在 Telegram Web 或桌面版中打开话题并查看 URL：`https://t.me/c/1234567890/5` —— 最后一个数字（`5`）就是 `thread_id`。超级群组的 `chat_id` 是群组 ID 前缀加 `-100`（例如群组 `1234567890` 变为 `-1001234567890`）。
:::

## 近期 Bot API 功能

- **Bot API 9.4（2026 年 2 月）：** 私聊话题 —— 机器人可以通过 `createForumTopic` 在一对一私聊中创建论坛话题。见上方[私聊话题](#private-chat-topics-bot-api-94)。
- **隐私政策：** Telegram 现在要求机器人有隐私政策。通过 BotFather 的 `/setprivacy_policy` 设置，否则 Telegram 可能会自动生成占位内容。如果你的机器人面向公众，这一点尤为重要。
- **消息流式传输：** Bot API 9.x 增加了对流式传输长回复的支持，可以改善长篇代理回复的感知延迟。

## 交互式模型选择器

在 Telegram 聊天中发送不带参数的 `/model` 时，Hermes 会显示用于切换模型的交互式内联键盘：

1. **提供商选择** —— 显示每个可用提供商及模型数量的按钮（例如 "OpenAI (15)"、"✓ Anthropic (12)" 表示当前提供商）。
2. **模型选择** —— 分页模型列表，带 **Prev**/**Next** 导航、**Back** 按钮返回提供商、以及 **Cancel**。

当前模型和提供商显示在顶部。所有导航通过原地编辑同一条消息完成（不会产生聊天杂乱）。

:::tip
如果你知道确切的模型名称，直接输入 `/model <name>` 跳过选择器。你也可以输入 `/model <name> --global` 将更改持久化到跨会话。
:::

## Webhook 模式

默认情况下，Telegram 适配器通过**长轮询**连接 —— 网关向 Telegram 服务器发起出站连接。这在任何地方都能工作，但会保持持久连接。

**Webhook 模式**是一种替代方案，Telegram 通过 HTTPS 将更新推送到你的服务器。这非常适合**无服务器和云部署**（Fly.io、Railway 等），入站 HTTP 可以唤醒暂停的机器。

### 配置

设置 `TELEGRAM_WEBHOOK_URL` 环境变量以启用 webhook 模式：

```bash
# 必需 -- 你的公共 HTTPS 端点
TELEGRAM_WEBHOOK_URL=https://app.fly.dev/telegram

# 可选 -- 本地监听端口（默认：8443）
TELEGRAM_WEBHOOK_PORT=8443

# 可选 -- 用于更新验证的密钥令牌（未设置则自动生成）
TELEGRAM_WEBHOOK_SECRET=my-secret-token
```

或在 `~/.hermes/config.yaml` 中：

```yaml
telegram:
  webhook_mode: true
```

设置了 `TELEGRAM_WEBHOOK_URL` 时，网关会启动一个 HTTP 服务器监听 `0.0.0.0:<port>` 并向 Telegram 注册 webhook URL。URL 路径从 webhook URL 中提取（默认为 `/telegram`）。

:::warning
Telegram 要求 webhook 端点具有**有效的 TLS 证书**。自签名证书将被拒绝。使用反向代理（nginx、Caddy）或提供 TLS 终止的平台（Fly.io、Railway、Cloudflare Tunnel）。
:::

## DNS-over-HTTPS 备用 IP

在某些受限网络中，`api.telegram.org` 可能解析到不可达的 IP。Telegram 适配器包含**备用 IP** 机制，可以在保持正确的 TLS 主机名和 SNI 的同时，透明地对备用 IP 重试连接。

### 工作原理

1. 如果设置了 `TELEGRAM_FALLBACK_IPS`，直接使用这些 IP。
2. 否则，适配器通过 DNS-over-HTTPS (DoH) 自动查询 **Google DNS** 和 **Cloudflare DNS** 以发现 `api.telegram.org` 的备用 IP。
3. DoH 返回的与系统 DNS 结果不同的 IP 被用作备用。
4. 如果 DoH 也被阻断，使用硬编码的种子 IP（`149.154.167.220`）作为最后手段。
5. 一旦备用 IP 成功，它就变成"粘性的" —— 后续请求直接使用它，不再先重试主路径。

### 配置

```bash
# 显式备用 IP（逗号分隔）
TELEGRAM_FALLBACK_IPS=149.154.167.220,149.154.167.221
```

或在 `~/.hermes/config.yaml` 中：

```yaml
platforms:
  telegram:
    extra:
      fallback_ips:
        - "149.154.167.220"
```

:::tip
通常你不需要手动配置此项。通过 DoH 的自动发现可以处理大多数受限网络场景。`TELEGRAM_FALLBACK_IPS` 环境变量仅在你的网络也阻断 DoH 时才需要。
:::

## 代理支持

如果你的网络需要 HTTP 代理才能访问互联网（在企业环境中很常见），Telegram 适配器会自动读取标准代理环境变量并通过代理路由所有连接。

### 支持的变量

适配器按以下顺序检查这些环境变量，使用第一个设置的变量：

1. `HTTPS_PROXY`
2. `HTTP_PROXY`
3. `ALL_PROXY`
4. `https_proxy` / `http_proxy` / `all_proxy`（小写变体）

### 配置

在启动网关前设置环境中的代理：

```bash
export HTTPS_PROXY=http://proxy.example.com:8080
hermes gateway
```

或添加到 `~/.hermes/.env`：

```bash
HTTPS_PROXY=http://proxy.example.com:8080
```

代理适用于主传输和所有备用 IP 传输。无需额外的 Hermes 配置 —— 如果环境变量已设置，就会自动使用。

:::note
这涵盖了 Hermes 用于 Telegram 连接的自定义备用传输层。标准的 `httpx` 客户端在其他地方使用时已经原生支持代理环境变量。
:::

## 消息表情回应

机器人可以向消息添加表情回应作为视觉处理反馈：

- 👀 当机器人开始处理你的消息时
- ✅ 当回复成功发送时
- ❌ 如果处理过程中发生错误

表情回应**默认禁用**。在 `config.yaml` 中启用：

```yaml
telegram:
  reactions: true
```

或通过环境变量：

```bash
TELEGRAM_REACTIONS=true
```

:::note
与 Discord（表情回应是累加的）不同，Telegram 的 Bot API 在单次调用中替换所有机器人表情回应。从 👀 到 ✅/❌ 的转换是原子性的 —— 你不会同时看到两者。
:::

:::tip
如果机器人没有在群组中添加表情回应的权限，表情回应调用会静默失败，消息处理正常继续。
:::

## 频道专属提示词

为特定 Telegram 群组或论坛话题分配临时系统提示词。提示词在每轮运行时注入 —— 永远不会持久化到对话记录 —— 因此更改会立即生效。

```yaml
telegram:
  channel_prompts:
    "-1001234567890": |
      你是一个研究助手。专注于学术来源、
      引用和简洁的综合。
    "42":  |
      这个话题用于创意写作反馈。要温暖和
      有建设性。
```

键是聊天 ID（群组/超级群组）或论坛话题 ID。对于论坛群组，话题级提示词覆盖群组级提示词：

- 群组 `-1001234567890` 中话题 `42` 的消息 → 使用话题 `42` 的提示词
- 话题 `99`（无显式条目）中的消息 → 回退到群组 `-1001234567890` 的提示词
- 没有条目的群组中的消息 → 不应用频道提示词

数字 YAML 键会自动规范化为字符串。

## 故障排除

| 问题 | 解决方案 |
|---------|----------|
| 机器人完全没有响应 | 验证 `TELEGRAM_BOT_TOKEN` 是否正确。检查 `hermes gateway` 日志中的错误。 |
| 机器人回复 "unauthorized" | 你的用户 ID 不在 `TELEGRAM_ALLOWED_USERS` 中。通过 @userinfobot 再次确认。 |
| 机器人忽略群组消息 | 隐私模式可能是开启的。关闭它（第三步）或将机器人设为群组管理员。**记得在更改隐私设置后移除并重新添加机器人。** |
| 语音消息未被转录 | 验证 STT 是否可用：安装 `faster-whisper` 进行本地转录，或在 `~/.hermes/.env` 中设置 `GROQ_API_KEY` / `VOICE_TOOLS_OPENAI_KEY`。 |
| 语音回复是文件而非气泡 | 安装 `ffmpeg`（Edge TTS Opus 转换所需）。 |
| 机器人令牌被撤销/无效 | 通过 BotFather 的 `/revoke` 然后 `/newbot` 或 `/token` 生成新令牌。更新你的 `.env` 文件。 |
| Webhook 未收到更新 | 验证 `TELEGRAM_WEBHOOK_URL` 是否可公开访问（用 `curl` 测试）。确保你的平台/反向代理将入站 HTTPS 流量从 URL 的端口路由到 `TELEGRAM_WEBHOOK_PORT` 配置的本地监听端口（它们不需要是相同的端口号）。确保 SSL/TLS 处于活动状态 —— Telegram 只向 HTTPS URL 发送。检查防火墙规则。 |

## 执行审批

当代理尝试运行潜在危险的命令时，它会在聊天中请求你的审批：

> ⚠️ 此命令存在潜在危险（递归删除）。回复 "yes" 以批准。

回复 "yes"/"y" 批准或 "no"/"n" 拒绝。

## 安全

:::warning
始终设置 `TELEGRAM_ALLOWED_USERS` 来限制谁可以与你的机器人交互。未设置时，网关默认拒绝所有用户作为安全措施。
:::

永远不要公开分享你的机器人令牌。如果泄露，请立即通过 BotFather 的 `/revoke` 命令撤销。

更多详情，请参阅[安全文档](/user-guide/security)。你也可以使用 [DM 配对](/user-guide/messaging#dm-pairing-alternative-to-allowlists)进行更动态的用户授权方式。
