---
sidebar_position: 7
title: "会话"
description: "会话持久化、恢复、搜索、管理和每平台会话跟踪"
---

# 会话

Hermes Agent 自动将每次对话保存为会话。会话支持对话恢复、跨会话搜索和完整的对话历史管理。

## 会话工作原理

每次对话 — 无论是来自 CLI、Telegram、Discord、Slack、WhatsApp、Signal、Matrix 还是任何其他消息平台 — 都作为会话存储，包含完整的消息历史。会话在两个互补系统中跟踪：

1. **SQLite 数据库**（`~/.hermes/state.db`）— 结构化会话元数据，带 FTS5 全文搜索
2. **JSONL 转录文件**（`~/.hermes/sessions/`）— 包含工具调用的原始对话转录（网关）

SQLite 数据库存储：
- 会话 ID、来源平台、用户 ID
- **会话标题**（唯一的、人类可读的名称）
- 模型名称和配置
- 系统提示快照
- 完整消息历史（角色、内容、工具调用、工具结果）
- Token 计数（输入/输出）
- 时间戳（started_at、ended_at）
- 父会话 ID（用于压缩触发的会话分裂）

### 会话来源

每个会话都标记了其来源平台：

| 来源 | 描述 |
|------|------|
| `cli` | 交互式 CLI（`hermes` 或 `hermes chat`） |
| `telegram` | Telegram 即时通讯 |
| `discord` | Discord 服务器/私信 |
| `slack` | Slack 工作区 |
| `whatsapp` | WhatsApp 即时通讯 |
| `signal` | Signal 即时通讯 |
| `matrix` | Matrix 房间和私信 |
| `mattermost` | Mattermost 频道 |
| `email` | 电子邮件（IMAP/SMTP） |
| `sms` | 通过 Twilio 的短信 |
| `dingtalk` | 钉钉即时通讯 |
| `feishu` | 飞书/Lark 即时通讯 |
| `wecom` | 企业微信 |
| `weixin` | 微信（个人） |
| `bluebubbles` | 通过 BlueBubbles macOS 服务器的 Apple iMessage |
| `qqbot` | QQ 机器人（腾讯 QQ）通过官方 API v2 |
| `homeassistant` | Home Assistant 对话 |
| `webhook` | 传入的 webhook |
| `api-server` | API 服务器请求 |
| `acp` | ACP 编辑器集成 |
| `cron` | 计划的 cron 任务 |
| `batch` | 批处理运行 |

## CLI 会话恢复

使用 `--continue` 或 `--resume` 从 CLI 恢复之前的对话：

### 继续上一个会话

```bash
# 恢复最近的 CLI 会话
hermes --continue
hermes -c

# 或使用 chat 子命令
hermes chat --continue
hermes chat -c
```

这从 SQLite 数据库中查找最近的 `cli` 会话并加载其完整的对话历史。

### 按名称恢复

如果你给会话设置了标题（见下方[会话命名](#session-naming)），可以按名称恢复：

```bash
# 恢复命名的会话
hermes -c "my project"

# 如果有血统变体（my project、my project #2、my project #3），
# 这会自动恢复最新的
hermes -c "my project"   # → 恢复 "my project #3"
```

### 恢复特定会话

```bash
# 按 ID 恢复特定会话
hermes --resume 20250305_091523_a1b2c3d4
hermes -r 20250305_091523_a1b2c3d4

# 按标题恢复
hermes --resume "refactoring auth"

# 或使用 chat 子命令
hermes chat --resume 20250305_091523_a1b2c3d4
```

会话 ID 在你退出 CLI 会话时显示，也可以通过 `hermes sessions list` 找到。

### 恢复时的对话回顾

当你恢复会话时，Hermes 在输入提示之前以样式化面板显示之前对话的紧凑回顾：

<img className="docs-terminal-figure" src="/img/docs/session-recap.svg" alt="Stylized preview of the Previous Conversation recap panel shown when resuming a Hermes session." />
<p className="docs-figure-caption">恢复模式在返回实时提示之前显示一个紧凑的回顾面板，包含最近的用户和助手轮次。</p>

回顾：
- 显示**用户消息**（金色 `●`）和**助手回复**（绿色 `◆`）
- **截断**长消息（用户 300 字符，助手 200 字符 / 3 行）
- **折叠工具调用**为计数和工具名称（例如 `[3 tool calls: terminal, web_search]`）
- **隐藏**系统消息、工具结果和内部推理
- **限制**在最近 10 次交换，带有 "... N earlier messages ..." 指示器
- 使用**暗淡样式**以区别于活跃对话

要禁用回顾并保持最小化的单行行为，在 `~/.hermes/config.yaml` 中设置：

```yaml
display:
  resume_display: minimal   # 默认：full
```

:::tip
会话 ID 遵循格式 `YYYYMMDD_HHMMSS_<8位十六进制>`，例如 `20250305_091523_a1b2c3d4`。你可以按 ID 或标题恢复 — 两者都可以用 `-c` 和 `-r`。
:::

## 会话命名

给会话设置人类可读的标题，以便于查找和恢复。

### 自动生成标题

Hermes 在首次交换后自动为每个会话生成简短的描述性标题（3-7 个词）。这在后台线程中使用快速辅助模型运行，不会增加延迟。你在使用 `hermes sessions list` 或 `hermes sessions browse` 浏览会话时会看到自动生成的标题。

自动标题每个会话只触发一次，如果你已经手动设置了标题则会跳过。

### 手动设置标题

在任何聊天会话（CLI 或网关）中使用 `/title` 斜杠命令：

```
/title my research project
```

标题立即应用。如果会话尚未在数据库中创建（例如，你在发送第一条消息之前运行 `/title`），它会排队等待会话开始后应用。

你也可以从命令行重命名现有会话：

```bash
hermes sessions rename 20250305_091523_a1b2c3d4 "refactoring auth module"
```

### 标题规则

- **唯一** — 没有两个会话可以共享相同的标题
- **最多 100 个字符** — 保持列表输出整洁
- **已清理** — 控制字符、零宽字符和 RTL 覆盖会自动剥离
- **正常 Unicode 可用** — 表情符号、CJK、重音字符都可以

### 压缩时的自动血统

当会话的上下文被压缩（通过 `/compress` 手动或自动），Hermes 创建一个新的延续会话。如果原始会话有标题，新会话自动获得编号标题：

```
"my project" → "my project #2" → "my project #3"
```

当你按名称恢复时（`hermes -c "my project"`），它自动选择血统中最新的会话。

### 消息平台中的 /title

`/title` 命令在所有网关平台（Telegram、Discord、Slack、WhatsApp）中都可用：

- `/title My Research` — 设置会话标题
- `/title` — 显示当前标题

## 会话管理命令

Hermes 通过 `hermes sessions` 提供完整的会话管理命令集：

### 列出会话

```bash
# 列出最近的会话（默认：最近 20 个）
hermes sessions list

# 按平台过滤
hermes sessions list --source telegram

# 显示更多会话
hermes sessions list --limit 50
```

当会话有标题时，输出显示标题、预览和相对时间戳：

```
Title                  Preview                                  Last Active   ID
────────────────────────────────────────────────────────────────────────────────────────────────
refactoring auth       Help me refactor the auth module please   2h ago        20250305_091523_a
my project #3          Can you check the test failures?          yesterday     20250304_143022_e
—                      What's the weather in Las Vegas?          3d ago        20250303_101500_f
```

当没有会话有标题时，使用更简单的格式：

```
Preview                                            Last Active   Src    ID
──────────────────────────────────────────────────────────────────────────────────────
Help me refactor the auth module please             2h ago        cli    20250305_091523_a
What's the weather in Las Vegas?                    3d ago        tele   20250303_101500_f
```

### 导出会话

```bash
# 将所有会话导出到 JSONL 文件
hermes sessions export backup.jsonl

# 导出特定平台的会话
hermes sessions export telegram-history.jsonl --source telegram

# 导出单个会话
hermes sessions export session.jsonl --session-id 20250305_091523_a1b2c3d4
```

导出的文件每行包含一个 JSON 对象，包含完整的会话元数据和所有消息。

### 删除会话

```bash
# 删除特定会话（带确认）
hermes sessions delete 20250305_091523_a1b2c3d4

# 不带确认删除
hermes sessions delete 20250305_091523_a1b2c3d4 --yes
```

### 重命名会话

```bash
# 设置或更改会话标题
hermes sessions rename 20250305_091523_a1b2c3d4 "debugging auth flow"

# 多词标题在 CLI 中不需要引号
hermes sessions rename 20250305_091523_a1b2c3d4 debugging auth flow
```

如果标题已被其他会话使用，会显示错误。

### 清理旧会话

```bash
# 删除 90 天前结束的会话（默认）
hermes sessions prune

# 自定义年龄阈值
hermes sessions prune --older-than 30

# 只清理特定平台的会话
hermes sessions prune --source telegram --older-than 60

# 跳过确认
hermes sessions prune --older-than 30 --yes
```

:::info
清理只删除**已结束**的会话（已显式结束或自动重置的会话）。活跃会话永远不会被清理。
:::

### 会话统计

```bash
hermes sessions stats
```

输出：

```
Total sessions: 142
Total messages: 3847
  cli: 89 sessions
  telegram: 38 sessions
  discord: 15 sessions
Database size: 12.4 MB
```

要获得更深入的分析 — token 使用、成本估计、工具分解和活动模式 — 使用 [`hermes insights`](/docs/reference/cli-commands#hermes-insights)。

## 会话搜索工具

代理有一个内置的 `session_search` 工具，使用 SQLite 的 FTS5 引擎在所有过去的对话中执行全文搜索。

### 工作原理

1. FTS5 搜索按相关性排名的匹配消息
2. 按会话分组结果，取前 N 个唯一会话（默认 3）
3. 加载每个会话的对话，截断至匹配处为中心的约 100K 字符
4. 发送到快速摘要模型获取聚焦摘要
5. 返回每个会话的摘要，包含元数据和周围上下文

### FTS5 查询语法

搜索支持标准 FTS5 查询语法：

- 简单关键词：`docker deployment`
- 短语：`"exact phrase"`
- 布尔：`docker OR kubernetes`、`python NOT java`
- 前缀：`deploy*`

### 何时使用

代理被提示自动使用会话搜索：

> *"当用户引用过去对话中的内容或你怀疑存在相关的先前上下文时，使用 session_search 回忆它，而不是让用户重复。"*

## 每平台会话跟踪

### 网关会话

在消息平台上，会话由从消息来源构建的确定性会话键索引：

| 聊天类型 | 默认键格式 | 行为 |
|----------|-----------|------|
| Telegram 私信 | `agent:main:telegram:dm:<chat_id>` | 每个私信聊天一个会话 |
| Discord 私信 | `agent:main:discord:dm:<chat_id>` | 每个私信聊天一个会话 |
| WhatsApp 私信 | `agent:main:whatsapp:dm:<chat_id>` | 每个私信聊天一个会话 |
| 群聊 | `agent:main:<platform>:group:<chat_id>:<user_id>` | 当平台暴露用户 ID 时，群内每用户一个会话 |
| 群聊线程/主题 | `agent:main:<platform>:group:<chat_id>:<thread_id>` | 所有线程参与者共享会话（默认）。使用 `thread_sessions_per_user: true` 时每用户独立。 |
| 频道 | `agent:main:<platform>:channel:<chat_id>:<user_id>` | 当平台暴露用户 ID 时，频道内每用户一个会话 |

当 Hermes 无法获取共享聊天的参与者标识符时，回退到该房间的单个共享会话。

### 共享 vs 隔离的群组会话

默认情况下，Hermes 在 `config.yaml` 中使用 `group_sessions_per_user: true`。这意味着：

- Alice 和 Bob 都可以在同一个 Discord 频道中与 Hermes 对话，而不共享转录历史
- 一个用户的长时间工具密集型任务不会污染另一个用户的上下文窗口
- 中断处理也保持每用户独立，因为运行中代理的键与隔离的会话键匹配

如果你想要一个共享的"房间大脑"，设置：

```yaml
group_sessions_per_user: false
```

这将群组/频道恢复为每房间单个共享会话，保留共享的对话上下文，但也共享 token 成本、中断状态和上下文增长。

### 会话重置策略

网关会话基于可配置的策略自动重置：

- **idle** — 在 N 分钟不活动后重置
- **daily** — 在每天特定时间重置
- **both** — 在两者中先到的重置（idle 或 daily）
- **none** — 从不自动重置

在会话自动重置之前，代理会获得一个轮次来保存对话中的重要记忆或技能。

**有活跃后台进程**的会话永远不会自动重置，无论策略如何。

## 存储位置

| 内容 | 路径 | 描述 |
|------|------|------|
| SQLite 数据库 | `~/.hermes/state.db` | 所有会话元数据 + 带 FTS5 的消息 |
| 网关转录文件 | `~/.hermes/sessions/` | 每会话的 JSONL 转录 + sessions.json 索引 |
| 网关索引 | `~/.hermes/sessions/sessions.json` | 将会话键映射到活跃会话 ID |

SQLite 数据库使用 WAL 模式支持并发读取器和单个写入器，这很适合网关的多平台架构。

### 数据库 Schema

`state.db` 中的关键表：

- **sessions** — 会话元数据（id、source、user_id、model、title、timestamps、token counts）。标题有唯一索引（允许 NULL 标题，只有非 NULL 必须唯一）。
- **messages** — 完整消息历史（role、content、tool_calls、tool_name、token_count）
- **messages_fts** — FTS5 虚拟表，用于跨消息内容的全文搜索

## 会话过期和清理

### 自动清理

- 网关会话基于配置的重置策略自动重置
- 重置前，代理保存过期会话中的记忆和技能
- 已结束的会话保留在数据库中直到被清理

### 手动清理

```bash
# 清理 90 天前的会话
hermes sessions prune

# 删除特定会话
hermes sessions delete <session_id>

# 清理前导出（备份）
hermes sessions export backup.jsonl
hermes sessions prune --older-than 30 --yes
```

:::tip
数据库增长缓慢（典型值：数百个会话约 10-15 MB）。清理主要用于删除你不再需要用于搜索回忆的旧对话。
:::
