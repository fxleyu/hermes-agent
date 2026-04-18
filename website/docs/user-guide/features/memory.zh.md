---
sidebar_position: 3
title: "持久化记忆"
description: "Hermes Agent 如何跨会话记忆 — MEMORY.md、USER.md 和会话搜索"
---

# 持久化记忆

Hermes Agent 拥有有界的、精心管理的记忆，可跨会话持久化。这使它能够记住你的偏好、项目、环境以及学到的知识。

## 工作原理

两个文件构成代理的记忆：

| 文件 | 用途 | 字符限制 |
|------|---------|------------|
| **MEMORY.md** | 代理的个人笔记 — 环境事实、惯例、学到的知识 | 2,200 字符（~800 tokens） |
| **USER.md** | 用户档案 — 你的偏好、沟通风格、期望 | 1,375 字符（~500 tokens） |

两者都存储在 `~/.hermes/memories/` 中，并在会话开始时作为冻结快照注入系统提示词。代理通过 `memory` 工具管理自己的记忆 — 可以添加、替换或删除条目。

:::info
字符限制使记忆保持聚焦。当记忆满时，代理会合并或替换条目以为新信息腾出空间。
:::

## 记忆在系统提示词中的呈现

在每个会话开始时，记忆条目从磁盘加载并渲染为系统提示词中的冻结块：

```
══════════════════════════════════════════════
MEMORY (your personal notes) [67% — 1,474/2,200 chars]
══════════════════════════════════════════════
User's project is a Rust web service at ~/code/myapi using Axum + SQLx
§
This machine runs Ubuntu 22.04, has Docker and Podman installed
§
User prefers concise responses, dislikes verbose explanations
```

格式包括：
- 显示哪个存储（MEMORY 或 USER PROFILE）的标题
- 使用百分比和字符计数，以便代理了解容量
- 各条目之间用 `§`（节号）分隔符分隔
- 条目可以是多行的

**冻结快照模式：** 系统提示词注入在会话开始时捕获一次，中途不会更改。这是有意为之 — 它为性能保留了 LLM 的前缀缓存。当代理在会话期间添加/删除记忆条目时，更改会立即持久化到磁盘，但在下次会话开始前不会出现在系统提示词中。工具响应始终显示实时状态。

## 记忆工具操作

代理使用 `memory` 工具的这些操作：

- **add** — 添加新的记忆条目
- **replace** — 用更新的内容替换现有条目（通过 `old_text` 使用子串匹配）
- **remove** — 删除不再相关的条目（通过 `old_text` 使用子串匹配）

没有 `read` 操作 — 记忆内容在会话开始时自动注入系统提示词。代理将其记忆作为对话上下文的一部分看到。

### 子串匹配

`replace` 和 `remove` 操作使用短唯一子串匹配 — 你不需要完整的条目文本。`old_text` 参数只需要是一个唯一的子串，能够精确标识一个条目：

```python
# 如果记忆包含 "User prefers dark mode in all editors"
memory(action="replace", target="memory",
       old_text="dark mode",
       content="User prefers light mode in VS Code, dark mode in terminal")
```

如果子串匹配到多个条目，会返回一个错误，要求提供更具体的匹配。

## 两个目标详解

### `memory` — 代理的个人笔记

用于代理需要记住的关于环境、工作流程和经验教训的信息：

- 环境事实（操作系统、工具、项目结构）
- 项目惯例和配置
- 发现的工具问题和解决方法
- 已完成任务的日志条目
- 有效的技能和技术

### `user` — 用户档案

用于关于用户身份、偏好和沟通风格的信息：

- 姓名、角色、时区
- 沟通偏好（简洁 vs 详细、格式偏好）
- 不喜欢的事情和需要避免的事项
- 工作流程习惯
- 技术水平

## 何时保存 vs 跳过

### 保存这些（主动）

代理会自动保存 — 你不需要请求。它在学到以下内容时保存：

- **用户偏好：** "I prefer TypeScript over JavaScript" → 保存到 `user`
- **环境事实：** "This server runs Debian 12 with PostgreSQL 16" → 保存到 `memory`
- **纠正：** "Don't use `sudo` for Docker commands, user is in docker group" → 保存到 `memory`
- **惯例：** "Project uses tabs, 120-char line width, Google-style docstrings" → 保存到 `memory`
- **已完成的工作：** "Migrated database from MySQL to PostgreSQL on 2026-01-15" → 保存到 `memory`
- **明确请求：** "Remember that my API key rotation happens monthly" → 保存到 `memory`

### 跳过这些

- **琐碎/显而易见的信息：** "User asked about Python" — 太模糊，没有用处
- **容易重新发现的事实：** "Python 3.12 supports f-string nesting" — 可以网页搜索
- **原始数据转储：** 大段代码块、日志文件、数据表 — 太大，不适合记忆
- **会话特定的临时信息：** 临时文件路径、一次性调试上下文
- **已在上下文文件中的信息：** SOUL.md 和 AGENTS.md 内容

## 容量管理

记忆有严格的字符限制以保持系统提示词有界：

| 存储 | 限制 | 典型条目数 |
|-------|-------|----------------|
| memory | 2,200 字符 | 8-15 条 |
| user | 1,375 字符 | 5-10 条 |

### 记忆满时会发生什么

当你尝试添加一个会超过限制的条目时，工具返回错误：

```json
{
  "success": false,
  "error": "Memory at 2,100/2,200 chars. Adding this entry (250 chars) would exceed the limit. Replace or remove existing entries first.",
  "current_entries": ["..."],
  "usage": "2,100/2,200"
}
```

代理应该：
1. 读取当前条目（显示在错误响应中）
2. 识别可以删除或合并的条目
3. 使用 `replace` 将相关条目合并为更短的版本
4. 然后 `add` 新条目

**最佳实践：** 当记忆超过 80% 容量时（在系统提示词标题中可见），在添加新条目前先合并现有条目。例如，将三个单独的"项目使用 X"条目合并为一个全面的项目描述条目。

### 良好记忆条目的实际示例

**紧凑、信息密集的条目效果最好：**

```
# 好：打包多个相关事实
User runs macOS 14 Sonoma, uses Homebrew, has Docker Desktop and Podman. Shell: zsh with oh-my-zsh. Editor: VS Code with Vim keybindings.

# 好：具体的、可操作的惯例
Project ~/code/api uses Go 1.22, sqlc for DB queries, chi router. Run tests with 'make test'. CI via GitHub Actions.

# 好：带上下文的经验教训
The staging server (10.0.1.50) needs SSH port 2222, not 22. Key is at ~/.ssh/staging_ed25519.

# 差：太模糊
User has a project.

# 差：太冗长
On January 5th, 2026, the user asked me to look at their project which is
located at ~/code/api. I discovered it uses Go version 1.22 and...
```

## 重复防止

记忆系统自动拒绝完全重复的条目。如果你尝试添加已存在的内容，它返回成功并附带"无重复添加"消息。

## 安全扫描

记忆条目在被接受之前会被扫描注入和泄露模式，因为它们被注入到系统提示词中。匹配威胁模式（提示注入、凭据泄露、SSH 后门）或包含不可见 Unicode 字符的内容会被阻止。

## 会话搜索

除了 MEMORY.md 和 USER.md，代理还可以使用 `session_search` 工具搜索过去的对话：

- 所有 CLI 和消息会话都存储在 SQLite（`~/.hermes/state.db`）中，带有 FTS5 全文搜索
- 搜索查询返回相关的过去对话，带有 Gemini Flash 摘要
- 代理可以找到几周前讨论的内容，即使它不在活跃记忆中

```bash
hermes sessions list    # 浏览过去的会话
```

### session_search vs memory

| 特性 | 持久化记忆 | 会话搜索 |
|---------|------------------|----------------|
| **容量** | 总计 ~1,300 tokens | 无限（所有会话） |
| **速度** | 即时（在系统提示词中） | 需要搜索 + LLM 摘要 |
| **用途** | 始终可用的关键事实 | 查找特定的过去对话 |
| **管理** | 由代理手动管理 | 自动 — 所有会话都被存储 |
| **Token 成本** | 每会话固定（~1,300 tokens） | 按需（需要时搜索） |

**记忆**用于应该始终在上下文中的关键事实。**会话搜索**用于"我们上周讨论过 X 吗？"的查询，代理需要回忆过去对话的具体内容。

## 配置

```yaml
# 在 ~/.hermes/config.yaml 中
memory:
  memory_enabled: true
  user_profile_enabled: true
  memory_char_limit: 2200   # ~800 tokens
  user_char_limit: 1375     # ~500 tokens
```

## 外部记忆服务商

对于超越 MEMORY.md 和 USER.md 的更深层次的持久记忆，Hermes 附带了 8 个外部记忆服务商插件 — 包括 Honcho、OpenViking、Mem0、Hindsight、Holographic、RetainDB、ByteRover 和 Supermemory。

外部服务商与内置记忆**并行运行**（永远不会替换它），并添加知识图谱、语义搜索、自动事实提取和跨会话用户建模等能力。

```bash
hermes memory setup      # 选择服务商并配置
hermes memory status     # 检查当前活跃的服务商
```

有关每个服务商的完整详情、设置说明和比较，请参阅[记忆服务商](./memory-providers.md)指南。
