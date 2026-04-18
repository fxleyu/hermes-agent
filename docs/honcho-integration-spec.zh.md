# honcho-integration-spec

Hermes Agent 与 openclaw-honcho 的对比——以及将 Hermes 模式移植到其他 Honcho 集成中的移植规范。

---

## 概述

两个独立的 Honcho 集成分别为两个不同的代理运行时构建：**Hermes Agent**（Python，内置于运行器中）和 **openclaw-honcho**（TypeScript 插件，通过 hook/tool API）。两者使用相同的 Honcho 对等节点范式——双对等节点模型、`session.context()`、`peer.chat()`——但它们在每一层都做出了不同的权衡。

本文档映射了这些权衡，并定义了一个移植规范：一组源自 Hermes 的模式，每个模式以与集成无关的接口形式表述，任何 Honcho 集成都可以采用，不论运行时或语言。

> **范围** 两个集成目前都能正常工作。本规范关注的是差异——Hermes 中值得传播的模式以及 openclaw-honcho 中 Hermes 最终应采用的模式。本规范是增量性的，而非规定性的。

---

## 架构对比

### Hermes：内置运行器

Honcho 直接在 `AIAgent.__init__` 内初始化。没有插件边界。会话管理、上下文注入、异步预取和 CLI 界面都是运行器的核心关注点。上下文在每个会话中只注入一次（内置于 `_cached_system_prompt` 中），在会话中不会重新获取——这最大限度地提高了 LLM 提供商的前缀缓存命中率。

轮次流程：

```
用户消息
  → _honcho_prefetch()       （读取缓存——无 HTTP）
  → _build_system_prompt()   （仅首轮，已缓存）
  → LLM 调用
  → 响应
  → _honcho_fire_prefetch()  （守护线程，轮次结束）
       → prefetch_context() 线程  ──┐
       → prefetch_dialectic() 线程 ─┴→ _context_cache / _dialectic_cache
```

### openclaw-honcho：基于 hook 的插件

该插件针对 OpenClaw 的事件总线注册 hook。上下文在每个轮次的 `before_prompt_build` 中同步获取。消息捕获在 `agent_end` 中进行。多代理层级通过 `subagent_spawned` 跟踪。此模型是正确的，但每个轮次都要在 LLM 调用开始前承受一次阻塞式的 Honcho 往返。

轮次流程：

```
用户消息
  → before_prompt_build（阻塞式 HTTP——每个轮次）
       → session.context()
  → 组装系统提示词
  → LLM 调用
  → 响应
  → agent_end hook
       → session.addMessages()
       → session.setMetadata()
```

---

## 差异对照表

| 维度 | Hermes Agent | openclaw-honcho |
|---|---|---|
| **上下文注入时机** | 每个会话一次（缓存）。首轮之后响应路径上零 HTTP。 | 每个轮次，阻塞式。每轮获取新上下文但增加延迟。 |
| **预取策略** | 守护线程在轮次结束时触发；下一轮从缓存中消费。 | 无。在提示词构建时进行阻塞调用。 |
| **辩证法（peer.chat）** | 异步预取；结果注入下一轮的系统提示词。 | 通过 `honcho_recall` / `honcho_analyze` 工具按需调用。 |
| **推理级别** | 动态：随消息长度缩放。下限=配置默认值。上限="high"。 | 每个工具固定：recall=minimal，analyze=medium。 |
| **记忆模式** | `user_memory_mode` / `agent_memory_mode`：hybrid / honcho / local。 | 无。始终写入 Honcho。 |
| **写入频率** | async（后台队列）、turn、session、N turns。 | 每次 agent_end 后（无控制）。 |
| **AI 对等节点身份** | `observe_me=True`，`seed_ai_identity()`，`get_ai_representation()`，SOUL.md → AI 对等节点。 | 代理文件在设置时上传到代理对等节点。无持续自我观察。 |
| **上下文范围** | 用户对等节点 + AI 对等节点表示，两者都注入。 | 用户对等节点（owner）表示 + 对话摘要。`context` 调用时使用 `peerPerspective`。 |
| **会话命名** | 按目录 / 全局 / 手动映射 / 基于标题。 | 从平台会话键派生。 |
| **多代理** | 仅单代理。 | 通过 `subagent_spawned` 的父观察者层级。 |
| **工具界面** | 单一 `query_user_context` 工具（按需辩证法）。 | 6 个工具：session、profile、search、context（快速）+ recall、analyze（LLM）。 |
| **平台元数据** | 未剥离。 | 在 Honcho 存储前显式剥离。 |
| **消息去重** | 无。 | 会话元数据中的 `lastSavedIndex` 防止重复发送。 |
| **提示词中的 CLI 界面** | 管理命令注入系统提示词。代理了解自己的 CLI。 | 未注入。 |
| **身份中的 AI 对等节点名称** | 配置后替换 DEFAULT_AGENT_IDENTITY 中的 "Hermes Agent"。 | 未实现。 |
| **QMD / 本地文件搜索** | 未实现。 | 配置 QMD 后端时的透传工具。 |
| **工作区元数据** | 未实现。 | 工作区元数据中的 `agentPeerMap` 跟踪代理→对等节点 ID。 |

---

## 模式

来自 Hermes 的六个模式值得在任何 Honcho 集成中采用。每个模式都以与集成无关的接口形式描述。

**Hermes 贡献：**
- 异步预取（零延迟）
- 动态推理级别
- 按对等节点的记忆模式
- AI 对等节点身份构建
- 会话命名策略
- CLI 界面注入

**openclaw-honcho 反向贡献（Hermes 应采用）：**
- `lastSavedIndex` 去重
- 平台元数据剥离
- 多代理观察者层级
- `context()` 上的 `peerPerspective`
- 分层工具界面（快速/LLM）
- 工作区 `agentPeerMap`

---

## 规范：异步预取

### 问题

在每次 LLM 调用前同步调用 `session.context()` 和 `peer.chat()` 会为每个轮次增加 200-800ms 的 Honcho 往返延迟。

### 模式

在每个轮次 **结束时** 将两个调用作为非阻塞后台任务触发。将结果存储在按会话 ID 索引的每会话缓存中。在 **下一轮开始时**，从缓存中弹出——HTTP 请求已经完成。第一轮是冷启动（空缓存）；所有后续轮次在响应路径上都是零延迟。

### 接口契约

```typescript
interface AsyncPrefetch {
  // 在轮次结束时触发上下文 + 辩证法获取。非阻塞。
  firePrefetch(sessionId: string, userMessage: string): void;

  // 在轮次开始时弹出缓存结果。缓存为冷时返回空。
  popContextResult(sessionId: string): ContextResult | null;
  popDialecticResult(sessionId: string): string | null;
}

type ContextResult = {
  representation: string;
  card: string[];
  aiRepresentation?: string;  // 如果启用了 AI 对等节点上下文
  summary?: string;           // 如果获取了对话摘要
};
```

### 实现说明

- **Python：**`threading.Thread(daemon=True)`。写入 `dict[session_id, result]`——GIL 使简单写入操作是安全的。
- **TypeScript：**`Promise` 存储在 `Map<string, Promise<ContextResult>>` 中。在弹出时 await。如果尚未 resolve，返回 null——不阻塞。
- 弹出是破坏性的：读取后清除缓存条目，确保过期数据不会累积。
- 预取也应在第一轮触发（即使要到第二轮才会被消费）。

### openclaw-honcho 采用方案

将 `session.context()` 从 `before_prompt_build` 移到 `agent_end` 之后的后台任务中。将结果存储在 `state.contextCache` 中。在 `before_prompt_build` 中从缓存读取而非调用 Honcho。如果缓存为空（第一轮），不注入任何内容——没有 Honcho 上下文的提示词在第一轮仍然有效。

---

## 规范：动态推理级别

### 问题

Honcho 的辩证法端点支持从 `minimal` 到 `max` 的推理级别。每个工具固定一个级别会在简单查询上浪费预算，在复杂查询上服务不足。

### 模式

根据用户消息动态选择推理级别。使用配置的默认值作为下限。按消息长度提升。自动选择的上限为 `high`——永远不自动选择 `max`。

### 逻辑

```
< 120 字符  → 默认值（通常为 "low"）
120-400 字符 → 比默认值高一级（上限为 "high"）
> 400 字符  → 比默认值高两级（上限为 "high"）
```

### 配置键

添加 `dialecticReasoningLevel`（字符串，默认值 `"low"`）。这设置了下限。动态提升始终在此基础上应用。

### openclaw-honcho 采用方案

在 `honcho_recall` 和 `honcho_analyze` 中应用：将固定的 `reasoningLevel` 替换为动态选择器。`honcho_recall` 使用下限 `"minimal"`，`honcho_analyze` 使用下限 `"medium"`——两者仍然按消息长度提升。

---

## 规范：按对等节点的记忆模式

### 问题

用户希望独立控制用户上下文和代理上下文是写入本地、写入 Honcho，还是两者都写。

### 模式

| 模式 | 效果 |
|---|---|
| `hybrid` | 同时写入本地文件和 Honcho（默认） |
| `honcho` | 仅 Honcho——禁用相应的本地文件写入 |
| `local` | 仅本地文件——跳过此对等节点的 Honcho 同步 |

### 配置架构

```json
{
  "memoryMode": "hybrid",
  "userMemoryMode": "honcho",
  "agentMemoryMode": "hybrid"
}
```

解析顺序：按对等节点字段优先 → 简写 `memoryMode` → 默认值 `"hybrid"`。

### 对 Honcho 同步的影响

- `userMemoryMode=local`：跳过向 Honcho 添加用户对等节点消息
- `agentMemoryMode=local`：跳过向 Honcho 添加助手对等节点消息
- 两者都为 local：完全跳过 `session.addMessages()`
- `userMemoryMode=honcho`：禁用本地 USER.md 写入
- `agentMemoryMode=honcho`：禁用本地 MEMORY.md / SOUL.md 写入

---

## 规范：AI 对等节点身份构建

### 问题

Honcho 通过观察用户的发言来有机地构建用户的表示。AI 对等节点也存在相同的机制——但前提是为代理对等节点设置了 `observe_me=True`。否则，代理对等节点不会积累任何内容。

此外，现有的人格文件（SOUL.md、IDENTITY.md）应在首次激活时为 AI 对等节点的 Honcho 表示提供种子。

### 部分 A：为代理对等节点设置 observe_me=True

```typescript
await session.addPeers([
  [ownerPeer.id, { observeMe: true,  observeOthers: false }],
  [agentPeer.id, { observeMe: true,  observeOthers: true  }], // 之前为 false
]);
```

一行更改。基础性的。没有它，无论代理说什么，AI 对等节点的表示都保持为空。

### 部分 B：seedAiIdentity()

```typescript
async function seedAiIdentity(
  agentPeer: Peer,
  content: string,
  source: string
): Promise<boolean> {
  const wrapped = [
    `<ai_identity_seed>`,
    `<source>${source}</source>`,
    ``,
    content.trim(),
    `</ai_identity_seed>`,
  ].join("\n");

  await agentPeer.addMessage("assistant", wrapped);
  return true;
}
```

### 部分 C：设置时迁移代理文件

在 `honcho setup` 期间，通过 `seedAiIdentity()` 而非 `session.uploadFile()` 将代理自身文件（SOUL.md、IDENTITY.md、AGENTS.md）上传到代理对等节点。这将内容通过 Honcho 的观察管道进行路由。

### 部分 D：身份中的 AI 对等节点名称

当代理有配置的名称时，将其添加到注入的系统提示词前面：

```typescript
const namePrefix = agentName ? `You are ${agentName}.\n\n` : "";
return { systemPrompt: namePrefix + "## User Memory Context\n\n" + sections };
```

### CLI 界面

```
honcho identity <file>    # 从文件初始化
honcho identity --show    # 显示当前 AI 对等节点表示
```

---

## 规范：会话命名策略

### 问题

单一的全局会话意味着每个项目共享相同的 Honcho 上下文。按目录的会话提供隔离，而不需要用户手动命名会话。

### 策略

| 策略 | 会话键 | 使用场景 |
|---|---|---|
| `per-directory` | CWD 的基本名称 | 默认。每个项目获得自己的会话。 |
| `global` | 固定字符串 `"global"` | 单一跨项目会话。 |
| 手动映射 | 用户按路径配置 | `sessions` 配置映射覆盖目录基本名称。 |
| 基于标题 | 清理后的会话标题 | 当代理支持在对话中设置命名会话时。 |

### 配置架构

```json
{
  "sessionStrategy": "per-directory",
  "sessionPeerPrefix": false,
  "sessions": {
    "/home/user/projects/foo": "foo-project"
  }
}
```

### CLI 界面

```
honcho sessions              # 列出所有映射
honcho map <name>            # 将 cwd 映射到会话名称
honcho map                   # 无参数 = 列出映射
```

解析顺序：手动映射 → 会话标题 → 目录基本名称 → 平台键。

---

## 规范：CLI 界面注入

### 问题

当用户询问"如何更改我的记忆设置？"时，代理要么产生幻觉，要么说它不知道。代理应该了解自己的管理界面。

### 模式

当 Honcho 激活时，在系统提示词后附加一个紧凑的命令参考。保持在 300 字符以内。

```
# Honcho 记忆集成
已激活。会话：{sessionKey}。模式：{mode}。
管理命令：
  honcho status                    — 显示配置 + 连接状态
  honcho mode [hybrid|honcho|local] — 显示或设置记忆模式
  honcho sessions                  — 列出会话映射
  honcho map <name>                — 将目录映射到会话
  honcho identity [file] [--show]  — 初始化或显示 AI 身份
  honcho setup                     — 完整的交互式向导
```

---

## openclaw-honcho 检查清单

按影响排序：

- [ ] **异步预取** — 将 `session.context()` 从 `before_prompt_build` 移到 `agent_end` 之后的后台 Promise 中
- [ ] **代理对等节点的 observe_me=True** — 在 `session.addPeers()` 中一行更改
- [ ] **动态推理级别** — 添加辅助函数；在 `honcho_recall` 和 `honcho_analyze` 中应用；将 `dialecticReasoningLevel` 添加到配置
- [ ] **按对等节点的记忆模式** — 将 `userMemoryMode` / `agentMemoryMode` 添加到配置；控制 Honcho 同步和本地写入
- [ ] **seedAiIdentity()** — 添加辅助函数；在设置迁移期间用于 SOUL.md / IDENTITY.md
- [ ] **会话命名策略** — 添加 `sessionStrategy`、`sessions` 映射、`sessionPeerPrefix`
- [ ] **CLI 界面注入** — 将命令参考附加到 `before_prompt_build` 返回值
- [ ] **honcho identity 子命令** — 从文件初始化或 `--show` 当前表示
- [ ] **AI 对等节点名称注入** — 如果配置了 `aiPeer` 名称，将其添加到注入的系统提示词前面
- [ ] **honcho mode / sessions / map** — 与 Hermes 的 CLI 一致

openclaw-honcho 中已完成（不需重新实现）：`lastSavedIndex` 去重、平台元数据剥离、多代理父观察者、`context()` 上的 `peerPerspective`、分层工具界面、工作区 `agentPeerMap`、QMD 透传、自托管 Honcho。

---

## nanobot-honcho 检查清单

全新集成。从 openclaw-honcho 的架构开始，从第一天就应用所有 Hermes 模式。

### 阶段 1 — 核心正确性

- [ ] 双对等节点模型（owner + 代理对等节点），两者都启用 `observe_me=True`
- [ ] 轮次结束时的消息捕获，使用 `lastSavedIndex` 去重
- [ ] 在 Honcho 存储前剥离平台元数据
- [ ] 从第一天就使用异步预取——不实现阻塞式上下文注入
- [ ] 首次激活时的遗留文件迁移（USER.md → owner 对等节点，SOUL.md → `seedAiIdentity()`）

### 阶段 2 — 配置

- [ ] 配置架构：`apiKey`、`workspaceId`、`baseUrl`、`memoryMode`、`userMemoryMode`、`agentMemoryMode`、`dialecticReasoningLevel`、`sessionStrategy`、`sessions`
- [ ] 按对等节点的记忆模式控制
- [ ] 动态推理级别
- [ ] 会话命名策略

### 阶段 3 — 工具和 CLI

- [ ] 工具界面：`honcho_profile`、`honcho_recall`、`honcho_analyze`、`honcho_search`、`honcho_context`
- [ ] CLI：`setup`、`status`、`sessions`、`map`、`mode`、`identity`
- [ ] CLI 界面注入到系统提示词
- [ ] AI 对等节点名称连接到代理身份
