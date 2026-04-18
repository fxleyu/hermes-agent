---
sidebar_position: 4
title: "记忆服务商"
description: "外部记忆服务商插件 — Honcho、OpenViking、Mem0、Hindsight、Holographic、RetainDB、ByteRover、Supermemory"
---

# 记忆服务商

Hermes Agent 附带 8 个外部记忆服务商插件，为代理提供超越内置 MEMORY.md 和 USER.md 的持久化跨会话知识。同一时间只能有**一个**外部服务商处于活跃状态 — 内置记忆始终与之并行活跃。

## 快速入门

```bash
hermes memory setup      # 交互式选择器 + 配置
hermes memory status     # 检查当前活跃的服务商
hermes memory off        # 禁用外部服务商
```

你也可以通过 `hermes plugins` → 服务商插件 → 记忆服务商来选择活跃的记忆服务商。

或在 `~/.hermes/config.yaml` 中手动设置：

```yaml
memory:
  provider: openviking   # 或 honcho、mem0、hindsight、holographic、retaindb、byterover、supermemory
```

## 工作原理

当记忆服务商处于活跃状态时，Hermes 自动：

1. **将服务商上下文注入**系统提示词（服务商知道的内容）
2. **预取相关记忆**在每轮之前（后台、非阻塞）
3. **同步对话轮次**到服务商，在每次响应之后
4. **在会话结束时提取记忆**（对于支持的服务商）
5. **镜像内置记忆写入**到外部服务商
6. **添加服务商特定工具**以便代理可以搜索、存储和管理记忆

内置记忆（MEMORY.md / USER.md）继续与以前完全一样工作。外部服务商是附加的。

## 可用服务商

### Honcho

AI 原生的跨会话用户建模，具有辩证推理、会话范围的上下文注入、语义搜索和持久结论。基础上下文现在包括会话摘要以及用户表示和对等卡，使代理能够了解已经讨论过的内容。

| | |
|---|---|
| **最适合** | 具有跨会话上下文的多代理系统、用户-代理对齐 |
| **需要** | `pip install honcho-ai` + [API 密钥](https://app.honcho.dev) 或自托管实例 |
| **数据存储** | Honcho Cloud 或自托管 |
| **费用** | Honcho 定价（云端）/ 免费（自托管） |

**工具（5个）：** `honcho_profile`（读取/更新对等卡）、`honcho_search`（语义搜索）、`honcho_context`（会话上下文 — 摘要、表示、卡片、消息）、`honcho_reasoning`（LLM 合成的）、`honcho_conclude`（创建/删除结论）

**架构：** 两层上下文注入 — 基础层（会话摘要 + 表示 + 对等卡，在 `contextCadence` 时刷新）加上辩证补充层（LLM 推理，在 `dialecticCadence` 时刷新）。辩证层根据基础上下文是否存在自动选择冷启动提示（一般用户事实）与暖提示（会话范围上下文）。

**三个正交配置旋钮**独立控制成本和深度：

- `contextCadence` — 基础层刷新的频率（API 调用频率）
- `dialecticCadence` — 辩证 LLM 触发的频率（LLM 调用频率）
- `dialecticDepth` — 每次辩证调用的 `.chat()` 轮数（1-3，推理深度）

**设置向导：**
```bash
hermes honcho setup        # （旧命令）
# 或
hermes memory setup        # 选择 "honcho"
```

**配置：** `$HERMES_HOME/honcho.json`（配置文件本地）或 `~/.honcho/config.json`（全局）。解析顺序：`$HERMES_HOME/honcho.json` > `~/.hermes/honcho.json` > `~/.honcho/config.json`。参见[配置参考](https://github.com/hermes-ai/hermes-agent/blob/main/plugins/memory/honcho/README.md)和 [Honcho 集成指南](https://docs.honcho.dev/v3/guides/integrations/hermes)。

<details>
<summary>完整配置参考</summary>

| 键 | 默认值 | 描述 |
|-----|---------|-------------|
| `apiKey` | -- | 来自 [app.honcho.dev](https://app.honcho.dev) 的 API 密钥 |
| `baseUrl` | -- | 自托管 Honcho 的基础 URL |
| `peerName` | -- | 用户对等身份 |
| `aiPeer` | host key | AI 对等身份（每个配置文件一个） |
| `workspace` | host key | 共享工作区 ID |
| `contextTokens` | `null`（无上限） | 每轮自动注入上下文的 token 预算。在词边界处截断 |
| `contextCadence` | `1` | `context()` API 调用之间的最小轮数（基础层刷新） |
| `dialecticCadence` | `3` | `peer.chat()` LLM 调用之间的最小轮数。仅适用于 `hybrid`/`context` 模式 |
| `dialecticDepth` | `1` | 每次辩证调用的 `.chat()` 轮数。限制在 1-3 之间。轮 0：冷/暖提示，轮 1：自我审计，轮 2：调和 |
| `dialecticDepthLevels` | `null` | 每轮推理级别的可选数组，例如 `["minimal", "low", "medium"]`。覆盖比例默认值 |
| `dialecticReasoningLevel` | `'low'` | 基础推理级别：`minimal`、`low`、`medium`、`high`、`max` |
| `dialecticDynamic` | `true` | 当为 `true` 时，模型可以通过工具参数在每次调用时覆盖推理级别 |
| `dialecticMaxChars` | `600` | 注入系统提示词的辩证结果最大字符数 |
| `recallMode` | `'hybrid'` | `hybrid`（自动注入 + 工具）、`context`（仅注入）、`tools`（仅工具） |
| `writeFrequency` | `'async'` | 刷新消息的时机：`async`（后台线程）、`turn`（同步）、`session`（会话结束时批量）或整数 N |
| `saveMessages` | `true` | 是否将消息持久化到 Honcho API |
| `observationMode` | `'directional'` | `directional`（全部开启）或 `unified`（共享池）。使用 `observation` 对象覆盖 |
| `messageMaxChars` | `25000` | 每条消息的最大字符数（超过时分块） |
| `dialecticMaxInputChars` | `10000` | 发送给 `peer.chat()` 的辩证查询输入最大字符数 |
| `sessionStrategy` | `'per-directory'` | `per-directory`、`per-repo`、`per-session`、`global` |

</details>

<details>
<summary>最小化 honcho.json（云端）</summary>

```json
{
  "apiKey": "your-key-from-app.honcho.dev",
  "hosts": {
    "hermes": {
      "enabled": true,
      "aiPeer": "hermes",
      "peerName": "your-name",
      "workspace": "hermes"
    }
  }
}
```

</details>

<details>
<summary>最小化 honcho.json（自托管）</summary>

```json
{
  "baseUrl": "http://localhost:8000",
  "hosts": {
    "hermes": {
      "enabled": true,
      "aiPeer": "hermes",
      "peerName": "your-name",
      "workspace": "hermes"
    }
  }
}
```

</details>

:::tip 从 `hermes honcho` 迁移
如果你之前使用了 `hermes honcho setup`，你的配置和所有服务器端数据都完好无损。只需通过设置向导重新启用或手动设置 `memory.provider: honcho` 即可通过新系统重新激活。
:::

**多代理/配置文件：**

每个 Hermes 配置文件获得自己的 Honcho AI 对等体，同时共享同一工作区 — 所有配置文件看到相同的用户表示，但每个代理构建自己的身份和观察。

```bash
hermes profile create coder --clone   # 在 honcho.json 中创建 "coder" 对等体，从默认配置继承
```

`--clone` 的作用：在 `honcho.json` 中创建 `hermes.coder` host 块，`aiPeer: "coder"`，共享 `workspace`，继承 `peerName`、`recallMode`、`writeFrequency`、`observation` 等。对等体在 Honcho 中被急切创建，因此在第一条消息之前就已存在。

对于在 Honcho 设置之前创建的配置文件：

```bash
hermes honcho sync   # 扫描所有配置文件，为缺失的配置文件创建 host 块
```

这从默认的 `hermes` host 块继承设置，并为每个配置文件创建新的 AI 对等体。幂等的 — 跳过已有 host 块的配置文件。

<details>
<summary>完整 honcho.json 示例（多配置文件）</summary>

```json
{
  "apiKey": "your-key",
  "workspace": "hermes",
  "peerName": "eri",
  "hosts": {
    "hermes": {
      "enabled": true,
      "aiPeer": "hermes",
      "workspace": "hermes",
      "peerName": "eri",
      "recallMode": "hybrid",
      "writeFrequency": "async",
      "sessionStrategy": "per-directory",
      "observation": {
        "user": { "observeMe": true, "observeOthers": true },
        "ai": { "observeMe": true, "observeOthers": true }
      },
      "dialecticReasoningLevel": "low",
      "dialecticDynamic": true,
      "dialecticCadence": 3,
      "dialecticDepth": 1,
      "dialecticMaxChars": 600,
      "contextCadence": 1,
      "messageMaxChars": 25000,
      "saveMessages": true
    },
    "hermes.coder": {
      "enabled": true,
      "aiPeer": "coder",
      "workspace": "hermes",
      "peerName": "eri",
      "recallMode": "tools",
      "observation": {
        "user": { "observeMe": true, "observeOthers": false },
        "ai": { "observeMe": true, "observeOthers": true }
      }
    },
    "hermes.writer": {
      "enabled": true,
      "aiPeer": "writer",
      "workspace": "hermes",
      "peerName": "eri"
    }
  },
  "sessions": {
    "/home/user/myproject": "myproject-main"
  }
}
```

</details>

参见[配置参考](https://github.com/hermes-ai/hermes-agent/blob/main/plugins/memory/honcho/README.md)和 [Honcho 集成指南](https://docs.honcho.dev/v3/guides/integrations/hermes)。


---

### OpenViking

由 Volcengine（字节跳动）提供的上下文数据库，具有文件系统式知识层级、分层检索和自动记忆提取（6 个类别）。

| | |
|---|---|
| **最适合** | 具有结构化浏览的自托管知识管理 |
| **需要** | `pip install openviking` + 运行中的服务器 |
| **数据存储** | 自托管（本地或云端） |
| **费用** | 免费（开源，AGPL-3.0） |

**工具：** `viking_search`（语义搜索）、`viking_read`（分层：摘要/概览/完整）、`viking_browse`（文件系统导航）、`viking_remember`（存储事实）、`viking_add_resource`（摄取 URL/文档）

**设置：**
```bash
# 首先启动 OpenViking 服务器
pip install openviking
openviking-server

# 然后配置 Hermes
hermes memory setup    # 选择 "openviking"
# 或手动：
hermes config set memory.provider openviking
echo "OPENVIKING_ENDPOINT=http://localhost:1933" >> ~/.hermes/.env
```

**主要特性：**
- 分层上下文加载：L0（~100 tokens）→ L1（~2k）→ L2（完整）
- 会话提交时自动记忆提取（档案、偏好、实体、事件、案例、模式）
- `viking://` URI 方案用于层级知识浏览

---

### Mem0

服务器端 LLM 事实提取，具有语义搜索、重排序和自动去重。

| | |
|---|---|
| **最适合** | 无需手动操作的记忆管理 — Mem0 自动处理提取 |
| **需要** | `pip install mem0ai` + API 密钥 |
| **数据存储** | Mem0 Cloud |
| **费用** | Mem0 定价 |

**工具：** `mem0_profile`（所有存储的记忆）、`mem0_search`（语义搜索 + 重排序）、`mem0_conclude`（存储精确事实）

**设置：**
```bash
hermes memory setup    # 选择 "mem0"
# 或手动：
hermes config set memory.provider mem0
echo "MEM0_API_KEY=your-key" >> ~/.hermes/.env
```

**配置：** `$HERMES_HOME/mem0.json`

| 键 | 默认值 | 描述 |
|-----|---------|-------------|
| `user_id` | `hermes-user` | 用户标识符 |
| `agent_id` | `hermes` | 代理标识符 |

---

### Hindsight

长期记忆，具有知识图谱、实体解析和多策略检索。`hindsight_reflect` 工具提供其他服务商没有的跨记忆综合。自动保留完整对话轮次（包括工具调用）并进行会话级文档跟踪。

| | |
|---|---|
| **最适合** | 基于知识图谱的回忆，具有实体关系 |
| **需要** | 云端：来自 [ui.hindsight.vectorize.io](https://ui.hindsight.vectorize.io) 的 API 密钥。本地：LLM API 密钥（OpenAI、Groq、OpenRouter 等） |
| **数据存储** | Hindsight Cloud 或本地嵌入式 PostgreSQL |
| **费用** | Hindsight 定价（云端）或免费（本地） |

**工具：** `hindsight_retain`（带实体提取的存储）、`hindsight_recall`（多策略搜索）、`hindsight_reflect`（跨记忆综合）

**设置：**
```bash
hermes memory setup    # 选择 "hindsight"
# 或手动：
hermes config set memory.provider hindsight
echo "HINDSIGHT_API_KEY=your-key" >> ~/.hermes/.env
```

设置向导自动安装依赖项，仅安装所选模式所需的内容（云端为 `hindsight-client`，本地为 `hindsight-all`）。需要 `hindsight-client >= 0.4.22`（如果过时，在会话启动时自动升级）。

**本地模式 UI：** `hindsight-embed -p hermes ui start`

**配置：** `$HERMES_HOME/hindsight/config.json`

| 键 | 默认值 | 描述 |
|-----|---------|-------------|
| `mode` | `cloud` | `cloud` 或 `local` |
| `bank_id` | `hermes` | 记忆库标识符 |
| `recall_budget` | `mid` | 回忆详尽度：`low` / `mid` / `high` |
| `memory_mode` | `hybrid` | `hybrid`（上下文 + 工具）、`context`（仅自动注入）、`tools`（仅工具） |
| `auto_retain` | `true` | 自动保留对话轮次 |
| `auto_recall` | `true` | 每轮之前自动回忆记忆 |
| `retain_async` | `true` | 在服务器上异步处理保留 |
| `tags` | — | 存储记忆时应用的标签 |
| `recall_tags` | — | 回忆时过滤的标签 |

参见[插件 README](https://github.com/NousResearch/hermes-agent/blob/main/plugins/memory/hindsight/README.md) 获取完整配置参考。

---

### Holographic

本地 SQLite 事实存储，具有 FTS5 全文搜索、信任评分和 HRR（全息简化表示）用于组合代数查询。

| | |
|---|---|
| **最适合** | 本地纯记忆，具有高级检索，无外部依赖 |
| **需要** | 无（SQLite 始终可用）。NumPy 对于 HRR 代数是可选的。 |
| **数据存储** | 本地 SQLite |
| **费用** | 免费 |

**工具：** `fact_store`（9 个操作：add、search、probe、related、reason、contradict、update、remove、list）、`fact_feedback`（有用/无用评分，训练信任分数）

**设置：**
```bash
hermes memory setup    # 选择 "holographic"
# 或手动：
hermes config set memory.provider holographic
```

**配置：** `config.yaml` 中的 `plugins.hermes-memory-store` 下

| 键 | 默认值 | 描述 |
|-----|---------|-------------|
| `db_path` | `$HERMES_HOME/memory_store.db` | SQLite 数据库路径 |
| `auto_extract` | `false` | 会话结束时自动提取事实 |
| `default_trust` | `0.5` | 默认信任分数（0.0-1.0） |

**独特功能：**
- `probe` — 实体特定的代数回忆（关于某人/某物的所有事实）
- `reason` — 跨多个实体的组合 AND 查询
- `contradict` — 自动检测冲突事实
- 带有不对称反馈的信任评分（+0.05 有用 / -0.10 无用）

---

### RetainDB

云记忆 API，具有混合搜索（向量 + BM25 + 重排序）、7 种记忆类型和增量压缩。

| | |
|---|---|
| **最适合** | 已经使用 RetainDB 基础设施的团队 |
| **需要** | RetainDB 账户 + API 密钥 |
| **数据存储** | RetainDB Cloud |
| **费用** | $20/月 |

**工具：** `retaindb_profile`（用户档案）、`retaindb_search`（语义搜索）、`retaindb_context`（任务相关上下文）、`retaindb_remember`（带类型 + 重要性的存储）、`retaindb_forget`（删除记忆）

**设置：**
```bash
hermes memory setup    # 选择 "retaindb"
# 或手动：
hermes config set memory.provider retaindb
echo "RETAINDB_API_KEY=your-key" >> ~/.hermes/.env
```

---

### ByteRover

通过 `brv` CLI 的持久记忆 — 具有分层检索（模糊文本 → LLM 驱动搜索）的层级知识树。本地优先，带可选云同步。

| | |
|---|---|
| **最适合** | 想要可移植的本地优先记忆并带有 CLI 的开发者 |
| **需要** | ByteRover CLI（`npm install -g byterover-cli` 或 [安装脚本](https://byterover.dev)） |
| **数据存储** | 本地（默认）或 ByteRover Cloud（可选同步） |
| **费用** | 免费（本地）或 ByteRover 定价（云端） |

**工具：** `brv_query`（搜索知识树）、`brv_curate`（存储事实/决策/模式）、`brv_status`（CLI 版本 + 树统计）

**设置：**
```bash
# 首先安装 CLI
curl -fsSL https://byterover.dev/install.sh | sh

# 然后配置 Hermes
hermes memory setup    # 选择 "byterover"
# 或手动：
hermes config set memory.provider byterover
```

**主要特性：**
- 自动预压缩提取（在上下文压缩丢弃见解之前保存它们）
- 知识树存储在 `$HERMES_HOME/byterover/`（配置文件范围）
- SOC2 Type II 认证的云同步（可选）

---

### Supermemory

语义长期记忆，具有档案回忆、语义搜索、显式记忆工具和通过 Supermemory 图 API 的会话结束对话摄取。

| | |
|---|---|
| **最适合** | 具有用户分析和会话级图构建的语义回忆 |
| **需要** | `pip install supermemory` + [API 密钥](https://supermemory.ai) |
| **数据存储** | Supermemory Cloud |
| **费用** | Supermemory 定价 |

**工具：** `supermemory_store`（保存显式记忆）、`supermemory_search`（语义相似度搜索）、`supermemory_forget`（按 ID 或最佳匹配查询遗忘）、`supermemory_profile`（持久档案 + 最近上下文）

**设置：**
```bash
hermes memory setup    # 选择 "supermemory"
# 或手动：
hermes config set memory.provider supermemory
echo 'SUPERMEMORY_API_KEY=***' >> ~/.hermes/.env
```

**配置：** `$HERMES_HOME/supermemory.json`

| 键 | 默认值 | 描述 |
|-----|---------|-------------|
| `container_tag` | `hermes` | 用于搜索和写入的容器标签。支持 `{identity}` 模板用于配置文件范围的标签。 |
| `auto_recall` | `true` | 在轮次之前注入相关记忆上下文 |
| `auto_capture` | `true` | 每次响应后存储清理的用户-助手轮次 |
| `max_recall_results` | `10` | 格式化为上下文的最大回忆项数 |
| `profile_frequency` | `50` | 在第一轮和每 N 轮包含档案事实 |
| `capture_mode` | `all` | 默认跳过微小或琐碎的轮次 |
| `search_mode` | `hybrid` | 搜索模式：`hybrid`、`memories` 或 `documents` |
| `api_timeout` | `5.0` | SDK 和摄取请求的超时时间 |

**环境变量：** `SUPERMEMORY_API_KEY`（必需）、`SUPERMEMORY_CONTAINER_TAG`（覆盖配置）。

**主要特性：**
- 自动上下文隔离 — 从捕获的轮次中去除回忆的记忆，防止递归记忆污染
- 会话结束对话摄取用于更丰富的图级知识构建
- 档案事实在第一轮和可配置间隔注入
- 琐碎消息过滤（跳过"ok"、"thanks"等）
- **配置文件范围的容器** — 在 `container_tag` 中使用 `{identity}`（例如 `hermes-{identity}` → `hermes-coder`）以按 Hermes 配置文件隔离记忆
- **多容器模式** — 启用 `enable_custom_container_tags` 并配合 `custom_containers` 列表，让代理跨命名容器读写。自动操作（同步、预取）保持在主容器上。

<details>
<summary>多容器示例</summary>

```json
{
  "container_tag": "hermes",
  "enable_custom_container_tags": true,
  "custom_containers": ["project-alpha", "shared-knowledge"],
  "custom_container_instructions": "Use project-alpha for coding context."
}
```

</details>

**支持：** [Discord](https://supermemory.link/discord) · [support@supermemory.com](mailto:support@supermemory.com)

---

## 服务商比较

| 服务商 | 存储 | 费用 | 工具数 | 依赖 | 独特功能 |
|----------|---------|------|-------|-------------|----------------|
| **Honcho** | 云端 | 付费 | 5 | `honcho-ai` | 辩证用户建模 + 会话范围上下文 |
| **OpenViking** | 自托管 | 免费 | 5 | `openviking` + 服务器 | 文件系统层级 + 分层加载 |
| **Mem0** | 云端 | 付费 | 3 | `mem0ai` | 服务器端 LLM 提取 |
| **Hindsight** | 云端/本地 | 免费/付费 | 3 | `hindsight-client` | 知识图谱 + 反思综合 |
| **Holographic** | 本地 | 免费 | 2 | 无 | HRR 代数 + 信任评分 |
| **RetainDB** | 云端 | $20/月 | 5 | `requests` | 增量压缩 |
| **ByteRover** | 本地/云端 | 免费/付费 | 3 | `brv` CLI | 预压缩提取 |
| **Supermemory** | 云端 | 付费 | 4 | `supermemory` | 上下文隔离 + 会话图摄取 + 多容器 |

## 配置文件隔离

每个服务商的数据按[配置文件](/docs/user-guide/profiles)隔离：

- **本地存储服务商**（Holographic、ByteRover）使用 `$HERMES_HOME/` 路径，每个配置文件不同
- **配置文件服务商**（Honcho、Mem0、Hindsight、Supermemory）在 `$HERMES_HOME/` 存储配置，每个配置文件有自己的凭据
- **云端服务商**（RetainDB）自动推导配置文件范围的项目名称
- **环境变量服务商**（OpenViking）通过每个配置文件的 `.env` 文件配置

## 构建记忆服务商

如何创建自己的记忆服务商，请参阅[开发者指南：记忆服务商插件](/docs/developer-guide/memory-provider-plugin)。
