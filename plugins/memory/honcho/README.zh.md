# Honcho 记忆提供商

AI 原生的跨会话用户建模，具有多轮辩证推理、会话摘要、双向对等节点工具和持久化结论。

> **Honcho 文档：**<https://docs.honcho.dev/v3/guides/integrations/hermes>

## 要求

- `pip install honcho-ai`
- 从 [app.honcho.dev](https://app.honcho.dev) 获取 Honcho API 密钥，或使用自托管实例

## 设置

```bash
hermes honcho setup    # 完整交互式向导（云端或本地）
hermes memory setup    # 通用选择器，也可使用
```

或手动设置：
```bash
hermes config set memory.provider honcho
echo "HONCHO_API_KEY=***" >> ~/.hermes/.env
```

## 架构概览

### 两层上下文注入

上下文在 API 调用时注入到**用户消息**中（而非系统提示词），以保留提示词缓存。系统提示词中只放置一个静态模式头。注入的块用 `<memory-context>` 围栏包裹，附带系统注释说明这是背景数据，而非新的用户输入。

两个独立层，各有自己的节奏：

**第 1 层 — 基础上下文**（每 `contextCadence` 轮刷新）：
1. **会话摘要** — 来自 `session.context(summary=True)`，置于首位
2. **用户表示** — Honcho 对用户的演化模型
3. **用户对等节点卡片** — 关键事实快照
4. **AI 自我表示** — Honcho 对 AI 对等节点的模型
5. **AI 身份卡片** — AI 对等节点事实

**第 2 层 — 辩证法补充**（每 `dialecticCadence` 轮触发）：
对用户的多轮 `.chat()` 推理，附加在基础上下文之后。

两层合并，然后通过 `_truncate_to_budget` 截断以适应 `contextTokens` 预算（token 数 x 4 字符，按词边界安全截断）。

### 冷启动 vs 热会话提示词

辩证法第 0 轮根据会话状态自动选择提示词：

- **冷启动**（无缓存的基础上下文）："Who is this person? What are their preferences, goals, and working style? Focus on facts that would help an AI assistant be immediately useful."
- **热会话**（基础上下文存在）："Given what's been discussed in this session so far, what context about this user is most relevant to the current conversation? Prioritize active context over biographical facts."

不可配置——自动确定。

### 辩证法深度（多轮推理）

`dialecticDepth`（1-3，限制范围）控制每个辩证法周期触发多少次 `.chat()` 调用：

| 深度 | 轮次 | 行为 |
|------|------|------|
| 1 | 单次 `.chat()` | 仅基础查询（冷启动或热会话提示词） |
| 2 | 审计 + 合成 | 第 0 轮结果进行自我审计；第 1 轮进行有针对性的合成。当第 0 轮返回强信号时有条件地提前退出（>300 字符或带有项目符号/章节 >100 字符的结构化内容） |
| 3 | 审计 + 合成 + 调和 | 第 2 轮调和先前各轮之间的矛盾，形成最终合成 |

### 按比例推理级别

当未设置 `dialecticDepthLevels` 时，每轮使用相对于 `dialecticReasoningLevel`（"基准"）的按比例级别：

| 深度 | 各轮级别 |
|------|---------|
| 1 | [基准] |
| 2 | [minimal, 基准] |
| 3 | [minimal, 基准, low] |

使用 `dialecticDepthLevels` 覆盖：每轮推理级别字符串的显式数组。

### 三个正交的辩证法调节旋钮

| 旋钮 | 控制 | 类型 |
|------|------|------|
| `dialecticCadence` | 频率——辩证法触发之间的最小轮次间隔 | int |
| `dialecticDepth` | 数量——每次触发的轮次数（1-3） | int |
| `dialecticReasoningLevel` | 强度——每次 `.chat()` 调用的推理上限 | string |

### 输入清理

`run_conversation` 在处理前从用户输入中剥离泄漏的 `<memory-context>` 块。当 `saveMessages` 持久化包含注入上下文的轮次时，该块可能通过消息历史重新出现在后续轮次中。清理器移除 `<memory-context>` 块及相关的系统注释。

## 工具

五个双向工具。所有工具接受可选的 `peer` 参数（`"user"` 或 `"ai"`，默认 `"user"`）。

| 工具 | 是否调用 LLM？ | 描述 |
|------|---------------|------|
| `honcho_profile` | 否 | 对等节点卡片——关键事实快照 |
| `honcho_search` | 否 | 对存储上下文的语义搜索（默认 800 token，最大 2000） |
| `honcho_context` | 否 | 完整会话上下文：摘要、表示、卡片、消息 |
| `honcho_reasoning` | 是 | 通过辩证法 `.chat()` 的 LLM 合成答案 |
| `honcho_conclude` | 否 | 写入关于用户的持久化事实/结论 |

工具可见性取决于 `recallMode`：在 `context` 模式下隐藏，在 `tools` 和 `hybrid` 模式下始终显示。

## 配置解析

配置从第一个存在的文件读取：

| 优先级 | 路径 | 范围 |
|--------|------|------|
| 1 | `$HERMES_HOME/honcho.json` | 配置文件本地（隔离的 Hermes 实例） |
| 2 | `~/.hermes/honcho.json` | 默认配置文件（共享主机块） |
| 3 | `~/.honcho/config.json` | 全局（跨应用互操作） |

主机键从活跃的 Hermes 配置文件派生：`hermes`（默认）或 `hermes.<profile>`。

对于每个键，解析顺序为：**主机块 > 根 > 环境变量 > 默认值**。

## 完整配置参考

### 身份与连接

| 键 | 类型 | 默认值 | 描述 |
|----|------|--------|------|
| `apiKey` | string | — | API 密钥。回退到 `HONCHO_API_KEY` 环境变量 |
| `baseUrl` | string | — | 自托管 Honcho 的基础 URL。本地 URL 自动跳过 API 密钥认证 |
| `environment` | string | `"production"` | SDK 环境映射 |
| `enabled` | bool | auto | 主开关。当 `apiKey` 或 `baseUrl` 存在时自动启用 |
| `workspace` | string | 主机键 | Honcho 工作区 ID。共享环境——同一工作区中的所有配置文件可以看到相同的用户身份和相关记忆 |
| `peerName` | string | — | 用户对等节点身份 |
| `aiPeer` | string | 主机键 | AI 对等节点身份 |

### 记忆与召回

| 键 | 类型 | 默认值 | 描述 |
|----|------|--------|------|
| `recallMode` | string | `"hybrid"` | `"hybrid"`（自动注入 + 工具）、`"context"`（仅自动注入，工具隐藏）、`"tools"`（仅工具，无注入）。遗留 `"auto"` → `"hybrid"` |
| `observationMode` | string | `"directional"` | 预设：`"directional"`（全部开启）或 `"unified"`（共享池）。使用 `observation` 对象进行精细控制 |
| `observation` | object | — | 每对等节点的观察配置（见观察部分） |

### 写入行为

| 键 | 类型 | 默认值 | 描述 |
|----|------|--------|------|
| `writeFrequency` | string/int | `"async"` | `"async"`（后台）、`"turn"`（每轮同步）、`"session"`（结束时批量）或整数 N（每 N 轮） |
| `saveMessages` | bool | `true` | 将消息持久化到 Honcho API |

### 会话解析

| 键 | 类型 | 默认值 | 描述 |
|----|------|--------|------|
| `sessionStrategy` | string | `"per-directory"` | `"per-directory"`、`"per-session"`、`"per-repo"`（git 根目录）、`"global"` |
| `sessionPeerPrefix` | bool | `false` | 在会话键前添加对等节点名称 |
| `sessions` | object | `{}` | 手动目录到会话名称的映射 |

#### 会话名称解析

Honcho 会话名称决定了记忆落入哪个对话桶。解析遵循优先级链——第一个匹配获胜：

| 优先级 | 来源 | 示例会话名称 |
|--------|------|-------------|
| 1 | 手动映射（`sessions` 配置） | `"myproject-main"` |
| 2 | `/title` 命令（会话中重命名） | `"refactor-auth"` |
| 3 | 网关会话键（Telegram、Discord 等） | `"agent-main-telegram-dm-8439114563"` |
| 4 | `per-session` 策略 | Hermes 会话 ID（`20260415_a3f2b1`） |
| 5 | `per-repo` 策略 | Git 根目录名称（`hermes-agent`） |
| 6 | `per-directory` 策略 | 当前目录基本名称（`src`） |
| 7 | `global` 策略 | 工作区名称（`hermes`） |

网关平台始终通过优先级 3（按聊天隔离）解析，无论 `sessionStrategy` 设置如何。策略设置仅影响 CLI 会话。

如果 `sessionPeerPrefix` 为 `true`，对等节点名称会添加前缀：`eri-hermes-agent`。

#### 每种策略产生的结果

- **`per-directory`** — `$PWD` 的基本名称。在 `~/code/myapp` 和 `~/code/other` 中打开 hermes 会得到两个独立会话。相同目录 = 跨运行的相同会话。
- **`per-repo`** — git 根目录名称。仓库内的所有子目录共享一个会话。如果不在 git 仓库内，回退到 `per-directory`。
- **`per-session`** — Hermes 会话 ID（时间戳 + 十六进制）。每次 `hermes` 调用都会启动一个新的 Honcho 会话。如果没有可用的会话 ID，回退到 `per-directory`。
- **`global`** — 工作区名称。所有内容一个会话。记忆在所有目录和运行中累积。

### 多配置文件模式

多个 Hermes 配置文件可以共享一个工作区，同时保持独立的 AI 身份。配置解析为**主机块 > 根 > 环境变量 > 默认值**——主机块从根继承，因此共享设置只需声明一次：

```json
{
  "apiKey": "***",
  "workspace": "hermes",
  "peerName": "yourname",
  "hosts": {
    "hermes": {
      "aiPeer": "hermes",
      "recallMode": "hybrid",
      "sessionStrategy": "per-directory"
    },
    "hermes.coder": {
      "aiPeer": "coder",
      "recallMode": "tools",
      "sessionStrategy": "per-repo"
    }
  }
}
```

两个配置文件在相同的共享环境（`hermes`）中看到相同的用户（`yourname`），但每个 AI 对等节点构建自己的观察、结论和行为模式。编码器的记忆保持面向代码；主代理的保持广泛。

主机键从活跃的 Hermes 配置文件派生：`hermes`（默认）或 `hermes.<profile>`（例如 `hermes -p coder` → 主机键 `hermes.coder`）。

### 辩证法与推理

| 键 | 类型 | 默认值 | 描述 |
|----|------|--------|------|
| `dialecticDepth` | int | `1` | 每个辩证法周期的轮次数（1-3，限制范围）。1=单次查询，2=审计+合成，3=审计+合成+调和 |
| `dialecticDepthLevels` | array | — | 可选的每轮推理级别字符串数组。覆盖按比例默认值。示例：`["minimal", "low", "medium"]` |
| `dialecticReasoningLevel` | string | `"low"` | `.chat()` 的基准推理级别：`"minimal"`、`"low"`、`"medium"`、`"high"`、`"max"` |
| `dialecticDynamic` | bool | `true` | 为 `true` 时，模型可以通过 `honcho_reasoning` 工具按调用覆盖推理级别。为 `false` 时，始终使用 `dialecticReasoningLevel` |
| `dialecticMaxChars` | int | `600` | 注入系统提示词的辩证法结果最大字符数 |
| `dialecticMaxInputChars` | int | `10000` | 辩证法查询输入到 `.chat()` 的最大字符数。Honcho 云限制：10k |

### Token 预算

| 键 | 类型 | 默认值 | 描述 |
|----|------|--------|------|
| `contextTokens` | int | SDK 默认值 | `context()` API 调用的 token 预算。也控制预取截断（token 数 x 4 字符） |
| `messageMaxChars` | int | `25000` | 通过 `add_messages()` 发送的每条消息的最大字符数。超过此值会触发带 `[continued]` 标记的分块。Honcho 云限制：25k |

### 节奏（成本控制）

| 键 | 类型 | 默认值 | 描述 |
|----|------|--------|------|
| `contextCadence` | int | `1` | 基础上下文刷新之间的最小轮次间隔（会话摘要 + 表示 + 卡片） |
| `dialecticCadence` | int | `1` | 辩证法 `.chat()` 触发之间的最小轮次间隔 |
| `injectionFrequency` | string | `"every-turn"` | `"every-turn"` 或 `"first-turn"`（仅在第一条用户消息时注入上下文，从第 2 轮起跳过） |
| `reasoningLevelCap` | string | — | 推理级别硬上限：`"minimal"`、`"low"`、`"medium"`、`"high"` |

### 观察（精细控制）

与 Honcho 的每对等节点 `SessionPeerConfig` 1:1 映射。存在时覆盖 `observationMode` 预设。

```json
"observation": {
  "user": { "observeMe": true, "observeOthers": true },
  "ai":   { "observeMe": true, "observeOthers": true }
}
```

| 字段 | 默认值 | 描述 |
|------|--------|------|
| `user.observeMe` | `true` | 用户对等节点自我观察（Honcho 构建用户表示） |
| `user.observeOthers` | `true` | 用户对等节点观察 AI 消息 |
| `ai.observeMe` | `true` | AI 对等节点自我观察（Honcho 构建 AI 表示） |
| `ai.observeOthers` | `true` | AI 对等节点观察用户消息（启用跨对等节点辩证法） |

预设：
- `"directional"`（默认）：四个都为 `true`
- `"unified"`：用户 `observeMe=true`，AI `observeOthers=true`，其余 `false`

### 硬编码限制

| 限制 | 值 |
|------|-----|
| 搜索工具最大 token | 2000（硬上限），800（默认） |
| 对等节点卡片获取 token | 200 |

## 环境变量

| 变量 | 回退用于 |
|------|---------|
| `HONCHO_API_KEY` | `apiKey` |
| `HONCHO_BASE_URL` | `baseUrl` |
| `HONCHO_ENVIRONMENT` | `environment` |
| `HERMES_HONCHO_HOST` | 主机键覆盖 |

## CLI 命令

| 命令 | 描述 |
|------|------|
| `hermes honcho setup` | 完整交互式设置向导 |
| `hermes honcho status` | 显示活跃配置文件的已解析配置 |
| `hermes honcho enable` / `disable` | 切换活跃配置文件的 Honcho |
| `hermes honcho mode <mode>` | 更改召回或观察模式 |
| `hermes honcho peer --user <name>` | 更新用户对等节点名称 |
| `hermes honcho peer --ai <name>` | 更新 AI 对等节点名称 |
| `hermes honcho tokens --context <N>` | 设置上下文 token 预算 |
| `hermes honcho tokens --dialectic <N>` | 设置辩证法最大字符数 |
| `hermes honcho map <name>` | 将当前目录映射到会话名称 |
| `hermes honcho sync` | 为所有 Hermes 配置文件创建主机块 |

## 配置示例

```json
{
  "apiKey": "***",
  "workspace": "hermes",
  "peerName": "username",
  "contextCadence": 2,
  "dialecticCadence": 3,
  "dialecticDepth": 2,
  "hosts": {
    "hermes": {
      "enabled": true,
      "aiPeer": "hermes",
      "recallMode": "hybrid",
      "observation": {
        "user": { "observeMe": true, "observeOthers": true },
        "ai": { "observeMe": true, "observeOthers": true }
      },
      "writeFrequency": "async",
      "sessionStrategy": "per-directory",
      "dialecticReasoningLevel": "low",
      "dialecticDepth": 2,
      "dialecticMaxChars": 600,
      "saveMessages": true
    },
    "hermes.coder": {
      "enabled": true,
      "aiPeer": "coder",
      "sessionStrategy": "per-repo",
      "dialecticDepth": 1,
      "dialecticDepthLevels": ["low"],
      "observation": {
        "user": { "observeMe": true, "observeOthers": false },
        "ai": { "observeMe": true, "observeOthers": true }
      }
    }
  },
  "sessions": {
    "/home/user/myproject": "myproject-main"
  }
}
```
