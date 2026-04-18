---
sidebar_position: 3
title: "Discord"
description: "将 Hermes Agent 设置为 Discord 机器人"
---

# Discord 设置

Hermes Agent 可与 Discord 集成为机器人，让你通过私信或服务器频道与 AI 助手聊天。机器人接收你的消息，通过 Hermes Agent 管道处理（包括工具使用、记忆和推理），并实时响应。支持文本、语音消息、文件附件和斜杠命令。

在设置之前，先了解大多数人最想知道的：Hermes 进入你的服务器后的行为方式。

## Hermes 的行为方式

| 场景 | 行为 |
|---------|----------|
| **私信** | Hermes 回复每条消息。无需 `@提及`。每个私信有自己的会话。 |
| **服务器频道** | 默认情况下，Hermes 只在你 `@提及` 它时响应。如果你在频道中发帖但未提及它，Hermes 会忽略消息。 |
| **自由回复频道** | 你可以通过 `DISCORD_FREE_RESPONSE_CHANNELS` 使特定频道免于提及要求，或通过 `DISCORD_REQUIRE_MENTION=false` 全局禁用提及要求。 |
| **帖子/线程** | Hermes 在同一线程中回复。提及规则仍然适用，除非该线程或其父频道被配置为自由回复。线程的会话历史与父频道隔离。 |
| **多用户共享频道** | 默认情况下，Hermes 在频道内按用户隔离会话历史以确保安全和清晰。两个人在同一频道聊天不会共享一份对话记录，除非你明确禁用此功能。 |
| **提及其他用户的消息** | 当 `DISCORD_IGNORE_NO_MENTION` 为 `true`（默认）时，如果消息 @提及了其他用户但**未**提及机器人，Hermes 会保持沉默。这防止机器人插入针对其他人的对话。设为 `false` 则不管提及了谁，机器人都会响应所有消息。此设置仅在服务器频道中适用，不影响私信。 |

:::tip
如果你想要一个普通的机器人帮助频道，让人们无需每次都标记 Hermes 就能与之交谈，请将该频道添加到 `DISCORD_FREE_RESPONSE_CHANNELS`。
:::

### Discord 网关模型

Discord 上的 Hermes 不是无状态回复的 webhook。它通过完整的消息网关运行，这意味着每条入站消息都会经过：

1. 授权检查（`DISCORD_ALLOWED_USERS`）
2. 提及/自由回复检查
3. 会话查找
4. 会话对话记录加载
5. 正常的 Hermes 代理执行，包括工具、记忆和斜杠命令
6. 响应发送回 Discord

这很重要，因为繁忙服务器中的行为取决于 Discord 路由和 Hermes 会话策略。

### Discord 中的会话模型

默认情况下：

- 每个私信有自己的会话
- 每个服务器线程有自己的会话命名空间
- 共享频道中每个用户有自己的会话

因此，如果 Alice 和 Bob 都在 `#research` 中与 Hermes 交谈，Hermes 默认会将这些视为独立对话，即使他们使用的是同一个可见的 Discord 频道。

这通过 `config.yaml` 控制：

```yaml
group_sessions_per_user: true
```

仅在你明确需要整个房间共享一个对话时才设为 `false`：

```yaml
group_sessions_per_user: false
```

共享会话对协作房间很有用，但也意味着：

- 用户共享上下文增长和令牌成本
- 一个人的长工具密集型任务可能膨胀其他人的上下文
- 一个人的进行中请求可能中断另一个人在同一房间的后续消息

### 中断和并发

Hermes 按会话键跟踪运行中的代理。

使用默认的 `group_sessions_per_user: true` 时：

- Alice 中断她自己的进行中请求只影响 Alice 在该频道中的会话
- Bob 可以在同一频道继续交谈而不继承 Alice 的历史或中断 Alice 的运行

使用 `group_sessions_per_user: false` 时：

- 整个房间共享该频道/线程的一个运行代理槽位
- 不同人的后续消息可能相互中断或排队

本指南将带你完成从在 Discord 开发者门户创建机器人到发送第一条消息的完整设置过程。

## 第一步：创建 Discord 应用

1. 访问 [Discord 开发者门户](https://discord.com/developers/applications) 并使用你的 Discord 账户登录。
2. 点击右上角的 **New Application**。
3. 输入应用名称（例如 "Hermes Agent"）并接受开发者服务条款。
4. 点击 **Create**。

你将进入 **General Information** 页面。记下 **Application ID** —— 稍后构建邀请 URL 时需要。

## 第二步：创建机器人

1. 在左侧边栏中，点击 **Bot**。
2. Discord 会自动为你的应用创建一个机器人用户。你会看到机器人的用户名，可以自定义。
3. 在 **Authorization Flow** 下：
   - 将 **Public Bot** 设为 **ON** —— 使用 Discord 提供的邀请链接时需要（推荐）。这允许 Installation 选项卡生成默认授权 URL。
   - 将 **Require OAuth2 Code Grant** 保持为 **OFF**。

:::tip
你可以在此页面为机器人设置自定义头像和横幅。这是用户在 Discord 中看到的内容。
:::

:::info[私有机器人替代方案]
如果你更喜欢保持机器人私有（Public Bot = OFF），你**必须**在第五步中使用**手动 URL** 方法而不是 Installation 选项卡。Discord 提供的链接需要启用 Public Bot。
:::

## 第三步：启用特权网关意图

这是整个设置中最关键的步骤。没有正确的意图启用，你的机器人将连接到 Discord 但**无法读取消息内容**。

在 **Bot** 页面上，向下滚动到 **Privileged Gateway Intents**。你会看到三个开关：

| 意图 | 用途 | 是否必需？ |
|--------|---------|-----------| 
| **Presence Intent** | 查看用户在线/离线状态 | 可选 |
| **Server Members Intent** | 访问成员列表、解析用户名 | **必需** |
| **Message Content Intent** | 读取消息的文本内容 | **必需** |

**启用 Server Members Intent 和 Message Content Intent**，将它们切换为 **ON**。

- 没有 **Message Content Intent**，你的机器人收到消息事件但消息文本为空 —— 机器人实际上无法看到你输入的内容。
- 没有 **Server Members Intent**，机器人无法为允许用户列表解析用户名，可能无法识别谁在发消息。

:::warning[这是 Discord 机器人不工作的头号原因]
如果你的机器人在线但从不响应消息，**Message Content Intent** 几乎肯定是禁用的。回到[开发者门户](https://discord.com/developers/applications)，选择你的应用 → Bot → Privileged Gateway Intents，确保 **Message Content Intent** 已切换为 ON。点击 **Save Changes**。
:::

**关于服务器数量：**
- 如果你的机器人在**少于 100 个服务器**中，你可以自由切换意图开关。
- 如果你的机器人在 **100 个或更多服务器**中，Discord 要求你提交验证申请才能使用特权意图。对于个人使用，这不是问题。

点击页面底部的 **Save Changes**。

## 第四步：获取机器人令牌

机器人令牌是 Hermes Agent 用来以你的机器人身份登录的凭证。仍在 **Bot** 页面上：

1. 在 **Token** 部分，点击 **Reset Token**。
2. 如果你的 Discord 账户启用了双因素认证，输入你的 2FA 代码。
3. Discord 将显示你的新令牌。**立即复制。**

:::warning[令牌只显示一次]
令牌只显示一次。如果丢失，你需要重置并生成新的。永远不要公开分享令牌或提交到 Git —— 任何拥有此令牌的人都可以完全控制你的机器人。
:::

将令牌保存在安全的地方（例如密码管理器）。第八步会用到它。

## 第五步：生成邀请 URL

你需要一个 OAuth2 URL 来将机器人邀请到你的服务器。有两种方式：

### 选项 A：使用 Installation 选项卡（推荐）

:::note[需要 Public Bot]
此方法需要在第二步中将 **Public Bot** 设为 **ON**。如果你将 Public Bot 设为 OFF，请使用下面的手动 URL 方法。
:::

1. 在左侧边栏中，点击 **Installation**。
2. 在 **Installation Contexts** 下，启用 **Guild Install**。
3. 对于 **Install Link**，选择 **Discord Provided Link**。
4. 在 Guild Install 的 **Default Install Settings** 下：
   - **Scopes**：选择 `bot` 和 `applications.commands`
   - **Permissions**：选择下面列出的权限。

### 选项 B：手动 URL

你可以使用以下格式直接构建邀请 URL：

```
https://discord.com/oauth2/authorize?client_id=YOUR_APP_ID&scope=bot+applications.commands&permissions=274878286912
```

将 `YOUR_APP_ID` 替换为第一步中的 Application ID。

### 必需权限

你的机器人需要的最低权限：

- **View Channels** —— 查看它有权访问的频道
- **Send Messages** —— 响应你的消息
- **Embed Links** —— 格式化富文本回复
- **Attach Files** —— 发送图片、音频和文件输出
- **Read Message History** —— 维护对话上下文

### 推荐的额外权限

- **Send Messages in Threads** —— 在线程对话中回复
- **Add Reactions** —— 添加表情回应作为确认

### 权限整数

| 级别 | 权限整数 | 包含内容 |
|-------|-------------------|-----------------|
| 最低 | `117760` | View Channels、Send Messages、Read Message History、Attach Files |
| 推荐 | `274878286912` | 以上所有加上 Embed Links、Send Messages in Threads、Add Reactions |

## 第六步：邀请到你的服务器

1. 在浏览器中打开邀请 URL（从 Installation 选项卡或你构建的手动 URL）。
2. 在 **Add to Server** 下拉菜单中选择你的服务器。
3. 点击 **Continue**，然后点击 **Authorize**。
4. 如果提示，完成验证码。

:::info
你需要在 Discord 服务器上拥有 **Manage Server** 权限才能邀请机器人。如果你在下拉菜单中看不到你的服务器，请让服务器管理员使用邀请链接。
:::

授权后，机器人将出现在你服务器的成员列表中（在你启动 Hermes 网关之前会显示为离线）。

## 第七步：找到你的 Discord 用户 ID

Hermes Agent 使用你的 Discord 用户 ID 来控制谁可以与机器人交互。要找到它：

1. 打开 Discord（桌面或网页应用）。
2. 进入 **Settings** → **Advanced** → 将 **Developer Mode** 切换为 **ON**。
3. 关闭设置。
4. 右键点击你自己的用户名（在消息中、成员列表中或你的个人资料中） → **Copy User ID**。

你的用户 ID 是一个类似 `284102345871466496` 的长数字。

:::tip
开发者模式还允许你以同样方式复制**频道 ID** 和**服务器 ID** —— 右键点击频道或服务器名称并选择 Copy ID。如果你想手动设置主频道，需要频道 ID。
:::

## 第八步：配置 Hermes Agent

### 选项 A：交互式设置（推荐）

运行引导式设置命令：

```bash
hermes gateway setup
```

出现提示时选择 **Discord**，然后在询问时粘贴你的机器人令牌和用户 ID。

### 选项 B：手动配置

将以下内容添加到 `~/.hermes/.env` 文件：

```bash
# 必需
DISCORD_BOT_TOKEN=your-bot-token
DISCORD_ALLOWED_USERS=284102345871466496

# 多个允许的用户（逗号分隔）
# DISCORD_ALLOWED_USERS=284102345871466496,198765432109876543
```

然后启动网关：

```bash
hermes gateway
```

机器人应该会在几秒内在 Discord 中上线。给它发条消息 —— 私信或在它能看到的频道中 —— 来测试。

:::tip
你可以在后台运行 `hermes gateway` 或作为 systemd 服务以持久运行。详见部署文档。
:::

## 配置参考

Discord 行为通过两个文件控制：**`~/.hermes/.env`** 用于凭证和环境级开关，**`~/.hermes/config.yaml`** 用于结构化设置。当两者都设置时，环境变量始终优先于 config.yaml 值。

### 环境变量（`.env`）

| 变量 | 必需 | 默认值 | 描述 |
|----------|----------|---------|-------------|
| `DISCORD_BOT_TOKEN` | **是** | — | 来自 [Discord 开发者门户](https://discord.com/developers/applications) 的机器人令牌。 |
| `DISCORD_ALLOWED_USERS` | **是** | — | 允许与机器人交互的逗号分隔的 Discord 用户 ID。未设置时，网关默认拒绝所有用户。 |
| `DISCORD_HOME_CHANNEL` | 否 | — | 机器人发送主动消息（cron 输出、提醒、通知）的频道 ID。 |
| `DISCORD_HOME_CHANNEL_NAME` | 否 | `"Home"` | 日志和状态输出中主频道的显示名称。 |
| `DISCORD_REQUIRE_MENTION` | 否 | `true` | 为 `true` 时，机器人只在服务器频道中被 `@提及` 时才响应。设为 `false` 则响应每个频道中的所有消息。 |
| `DISCORD_FREE_RESPONSE_CHANNELS` | 否 | — | 逗号分隔的频道 ID，在这些频道中机器人无需 `@提及` 即可响应，即使 `DISCORD_REQUIRE_MENTION` 为 `true`。 |
| `DISCORD_IGNORE_NO_MENTION` | 否 | `true` | 为 `true` 时，如果消息 `@提及` 了其他用户但**未**提及机器人，机器人保持沉默。防止机器人插入针对其他人的对话。仅在服务器频道中适用，不影响私信。 |
| `DISCORD_AUTO_THREAD` | 否 | `true` | 为 `true` 时，在文本频道中对每个 `@提及` 自动创建新线程，使每个对话隔离（类似 Slack 行为）。已在线程或私信中的消息不受影响。 |
| `DISCORD_ALLOW_BOTS` | 否 | `"none"` | 控制机器人如何处理来自其他 Discord 机器人的消息。`"none"` —— 忽略所有其他机器人。`"mentions"` —— 只接受 `@提及` Hermes 的机器人消息。`"all"` —— 接受所有机器人消息。 |
| `DISCORD_REACTIONS` | 否 | `true` | 为 `true` 时，机器人在处理过程中向消息添加表情回应（👀 开始时、✅ 成功时、❌ 出错时）。设为 `false` 完全禁用表情回应。 |
| `DISCORD_IGNORED_CHANNELS` | 否 | — | 逗号分隔的频道 ID，机器人在这些频道中**从不**响应，即使被 `@提及`。优先于所有其他频道设置。 |
| `DISCORD_NO_THREAD_CHANNELS` | 否 | — | 逗号分隔的频道 ID，机器人在这些频道中直接回复而不创建线程。仅在 `DISCORD_AUTO_THREAD` 为 `true` 时相关。 |
| `DISCORD_REPLY_TO_MODE` | 否 | `"first"` | 控制回复引用行为：`"off"` —— 从不引用原始消息，`"first"` —— 仅在第一个消息块上引用（默认），`"all"` —— 在每个块上引用。 |

### 配置文件（`config.yaml`）

`~/.hermes/config.yaml` 中的 `discord` 部分与上述环境变量对应。config.yaml 设置作为默认值应用 —— 如果等效的环境变量已设置，环境变量优先。

```yaml
# Discord 特定设置
discord:
  require_mention: true           # 在服务器频道中需要 @提及
  free_response_channels: ""      # 逗号分隔的频道 ID（或 YAML 列表）
  auto_thread: true               # 在 @提及 时自动创建线程
  reactions: true                 # 处理过程中添加表情回应
  ignored_channels: []            # 机器人从不响应的频道 ID
  no_thread_channels: []          # 机器人不创建线程直接回复的频道 ID
  channel_prompts: {}             # 频道专属临时系统提示词

# 会话隔离（适用于所有网关平台，不仅是 Discord）
group_sessions_per_user: true     # 在共享频道中按用户隔离会话
```

#### `discord.require_mention`

**类型：** boolean — **默认值：** `true`

启用时，机器人只在服务器频道中被直接 `@提及` 时才响应。无论此设置如何，私信始终会得到回复。

#### `discord.free_response_channels`

**类型：** 字符串或列表 — **默认值：** `""`

机器人无需 `@提及` 即响应所有消息的频道 ID。接受逗号分隔的字符串或 YAML 列表：

```yaml
# 字符串格式
discord:
  free_response_channels: "1234567890,9876543210"

# 列表格式
discord:
  free_response_channels:
    - 1234567890
    - 9876543210
```

如果线程的父频道在此列表中，该线程也变为免提及。

#### `discord.auto_thread`

**类型：** boolean — **默认值：** `true`

启用时，常规文本频道中的每个 `@提及` 都会自动创建新线程。这使主频道保持整洁，并为每个对话提供隔离的会话历史。线程创建后，该线程中的后续消息无需 `@提及` —— 机器人知道它已经在参与。

已存在线程或私信中发送的消息不受此设置影响。

#### `discord.reactions`

**类型：** boolean — **默认值：** `true`

控制机器人是否向消息添加表情回应作为视觉反馈：
- 👀 机器人开始处理你的消息时添加
- ✅ 回复成功发送时添加
- ❌ 处理过程中发生错误时添加

如果你觉得表情回应令人分心或机器人的角色没有 **Add Reactions** 权限，请禁用此项。

#### `discord.ignored_channels`

**类型：** 字符串或列表 — **默认值：** `[]`

机器人**从不**响应的频道 ID，即使被直接 `@提及`。此设置具有最高优先级 —— 如果频道在此列表中，机器人会静默忽略该处的所有消息，无论 `require_mention`、`free_response_channels` 或任何其他设置如何。

```yaml
# 字符串格式
discord:
  ignored_channels: "1234567890,9876543210"

# 列表格式
discord:
  ignored_channels:
    - 1234567890
    - 9876543210
```

如果线程的父频道在此列表中，该线程中的消息也会被忽略。

#### `discord.no_thread_channels`

**类型：** 字符串或列表 — **默认值：** `[]`

机器人直接在频道中回复而不自动创建线程的频道 ID。此设置仅在 `auto_thread` 为 `true`（默认）时有效。在这些频道中，机器人像普通消息一样内联回复而不是创建新线程。

```yaml
discord:
  no_thread_channels:
    - 1234567890  # 机器人在此处内联回复
```

适用于专门用于机器人交互的频道，线程会增加不必要的噪音。

#### `discord.channel_prompts`

**类型：** 映射 — **默认值：** `{}`

频道专属临时系统提示词，在匹配的 Discord 频道或线程中的每轮注入，不会持久化到对话记录。

```yaml
discord:
  channel_prompts:
    "1234567890": |
      此频道用于研究任务。偏好深度比较、
      引用和简洁综合。
    "9876543210": |
      此论坛用于治疗式支持。要温暖、扎实、
      不评判。
```

行为：
- 精确的线程/频道 ID 匹配优先。
- 如果消息在线程或论坛帖子中到达，但该线程没有显式条目，Hermes 会回退到父频道/论坛 ID。
- 提示词在运行时临时应用，因此更改会立即影响未来的回合，无需重写过去的会话历史。

#### `group_sessions_per_user`

**类型：** boolean — **默认值：** `true`

这是一个全局网关设置（不是 Discord 特定的），控制同一频道中的用户是否获得隔离的会话历史。

为 `true` 时：Alice 和 Bob 在 `#research` 中各自与 Hermes 有独立的对话。为 `false` 时：整个频道共享一份对话记录和一个运行代理槽位。

```yaml
group_sessions_per_user: true
```

详见上方[会话模型](#session-model-in-discord)部分了解每种模式的完整影响。

#### `display.tool_progress`

**类型：** 字符串 — **默认值：** `"all"` — **可选值：** `off`、`new`、`all`、`verbose`

控制机器人在处理时是否在聊天中发送进度消息（例如 "Reading file..."、"Running terminal command..."）。这是适用于所有平台的全局网关设置。

```yaml
display:
  tool_progress: "all"    # off | new | all | verbose
```

- `off` —— 无进度消息
- `new` —— 每轮只显示第一个工具调用
- `all` —— 显示所有工具调用（在网关消息中截断为 40 个字符）
- `verbose` —— 显示完整工具调用详情（可能产生长消息）

#### `display.tool_progress_command`

**类型：** boolean — **默认值：** `false`

启用时，使 `/verbose` 斜杠命令在网关中可用，让你可以在工具进度模式之间循环切换（`off → new → all → verbose → off`），无需编辑 config.yaml。

```yaml
display:
  tool_progress_command: true
```

## 交互式模型选择器

在 Discord 频道中发送不带参数的 `/model` 以打开基于下拉菜单的模型选择器：

1. **提供商选择** —— 显示可用提供商的 Select 下拉菜单（最多 25 个）。
2. **模型选择** —— 所选提供商的模型的第二个下拉菜单（最多 25 个）。

选择器在 120 秒后超时。只有授权用户（`DISCORD_ALLOWED_USERS` 中的用户）可以与之交互。如果你知道模型名称，直接输入 `/model <name>`。

## 技能的原生斜杠命令

Hermes 自动将已安装的技能注册为**原生 Discord 应用命令**。这意味着技能会出现在 Discord 的自动完成 `/` 菜单中，与内置命令并列。

- 每个技能成为一个 Discord 斜杠命令（例如 `/code-review`、`/ascii-art`）
- 技能接受一个可选的 `args` 字符串参数
- Discord 每个机器人限制 100 个应用命令 —— 如果你的技能多于可用槽位，多余的技能会在日志中发出警告并被跳过
- 技能在机器人启动时与内置命令（如 `/model`、`/reset` 和 `/background`）一起注册

无需额外配置 —— 通过 `hermes skills install` 安装的任何技能在下次网关重启时自动注册为 Discord 斜杠命令。

## 主频道

你可以指定一个 "主频道"，机器人在此发送主动消息（如 cron 任务输出、提醒和通知）。有两种设置方式：

### 使用斜杠命令

在机器人所在的任何 Discord 频道中输入 `/sethome`。该频道即成为主频道。

### 手动配置

将以下内容添加到 `~/.hermes/.env`：

```bash
DISCORD_HOME_CHANNEL=123456789012345678
DISCORD_HOME_CHANNEL_NAME="#bot-updates"
```

将 ID 替换为实际频道 ID（在开发者模式下右键 → Copy Channel ID）。

## 语音消息

Hermes Agent 支持 Discord 语音消息：

- **接收语音消息**会使用配置的 STT 提供商自动转录：本地 `faster-whisper`（无需密钥）、Groq Whisper（`GROQ_API_KEY`）或 OpenAI Whisper（`VOICE_TOOLS_OPENAI_KEY`）。
- **文字转语音**：使用 `/voice tts` 让机器人在文本回复旁发送语音音频回复。
- **Discord 语音频道**：Hermes 还可以加入语音频道，听用户说话，并在频道中回话。

完整的设置和操作指南，请参阅：
- [语音模式](/docs/user-guide/features/voice-mode)
- [使用 Hermes 语音模式](/docs/guides/use-voice-mode-with-hermes)

## 故障排除

### 机器人在线但不响应消息

**原因**：Message Content Intent 被禁用。

**修复**：访问[开发者门户](https://discord.com/developers/applications) → 你的应用 → Bot → Privileged Gateway Intents → 启用 **Message Content Intent** → Save Changes。重启网关。

### 启动时出现 "Disallowed Intents" 错误

**原因**：你的代码请求了开发者门户中未启用的意图。

**修复**：在 Bot 设置中启用所有三个特权网关意图（Presence、Server Members、Message Content），然后重启。

### 机器人无法看到特定频道的消息

**原因**：机器人的角色没有查看该频道的权限。

**修复**：在 Discord 中，进入频道设置 → Permissions → 为机器人的角色添加 **View Channel** 和 **Read Message History** 权限。

### 403 Forbidden 错误

**原因**：机器人缺少必需的权限。

**修复**：使用第五步中的 URL 重新邀请机器人并设置正确的权限，或在 Server Settings → Roles 中手动调整机器人的角色权限。

### 机器人离线

**原因**：Hermes 网关未运行，或令牌不正确。

**修复**：检查 `hermes gateway` 是否正在运行。验证 `.env` 文件中的 `DISCORD_BOT_TOKEN`。如果你最近重置了令牌，请更新它。

### "User not allowed" / 机器人忽略你

**原因**：你的用户 ID 不在 `DISCORD_ALLOWED_USERS` 中。

**修复**：将你的用户 ID 添加到 `~/.hermes/.env` 中的 `DISCORD_ALLOWED_USERS` 并重启网关。

### 同一频道中的人意外共享上下文

**原因**：`group_sessions_per_user` 被禁用，或平台无法在该上下文中提供消息的用户 ID。

**修复**：在 `~/.hermes/config.yaml` 中设置以下内容并重启网关：

```yaml
group_sessions_per_user: true
```

如果你有意要共享房间对话，保持关闭 —— 但预期会有共享的对话记录和共享的中断行为。

## 安全

:::warning
始终设置 `DISCORD_ALLOWED_USERS` 来限制谁可以与机器人交互。未设置时，网关默认拒绝所有用户作为安全措施。只添加你信任的人的用户 ID —— 授权用户可以完全访问代理的能力，包括工具使用和系统访问。
:::

关于保护 Hermes Agent 部署的更多信息，请参阅[安全指南](../security.md)。
