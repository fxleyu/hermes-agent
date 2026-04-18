---
sidebar_position: 5
title: "提示词组装"
description: "Hermes 如何构建系统提示词、保持缓存稳定性以及注入临时层"
---

# 提示词组装

Hermes 刻意将以下两者分离：

- **缓存的系统提示词状态**
- **临时的 API 调用时附加内容**

这是项目中最重要的设计决策之一，因为它影响：

- token 用量
- 提示词缓存效果
- 会话连续性
- 记忆正确性

主要文件：

- `run_agent.py`
- `agent/prompt_builder.py`
- `tools/memory_tool.py`

## 缓存的系统提示词层

缓存的系统提示词大致按以下顺序组装：

1. Agent 身份 — 当 `HERMES_HOME` 中有 `SOUL.md` 时使用该文件，否则回退到 `prompt_builder.py` 中的 `DEFAULT_AGENT_IDENTITY`
2. 工具感知行为指导
3. Honcho 静态块（激活时）
4. 可选系统消息
5. 冻结的 MEMORY 快照
6. 冻结的 USER 个人资料快照
7. 技能索引
8. 上下文文件（`AGENTS.md`、`.cursorrules`、`.cursor/rules/*.mdc`）— 当 SOUL.md 已在步骤 1 中作为身份加载时，此处**不会**重复包含
9. 时间戳 / 可选会话 ID
10. 平台提示

当设置了 `skip_context_files` 时（例如子 Agent 委派），SOUL.md 不会被加载，而是使用硬编码的 `DEFAULT_AGENT_IDENTITY`。

### 具体示例：组装后的系统提示词

以下是当所有层都存在时，最终系统提示词的简化视图（注释说明了每个部分的来源）：

```
# 第 1 层：Agent 身份（来自 ~/.hermes/SOUL.md）
You are Hermes, an AI assistant created by Nous Research.
You are an expert software engineer and researcher.
You value correctness, clarity, and efficiency.
...

# 第 2 层：工具感知行为指导
You have persistent memory across sessions. Save durable facts using
the memory tool: user preferences, environment details, tool quirks,
and stable conventions. Memory is injected into every turn, so keep
it compact and focused on facts that will still matter later.
...
When the user references something from a past conversation or you
suspect relevant cross-session context exists, use session_search
to recall it before asking them to repeat themselves.

# 工具使用强制（仅适用于 GPT/Codex 模型）
You MUST use your tools to take action — do not describe what you
would do or plan to do without actually doing it.
...

# 第 3 层：Honcho 静态块（激活时）
[Honcho 个性/上下文数据]

# 第 4 层：可选系统消息（来自配置或 API）
[用户配置的系统消息覆盖]

# 第 5 层：冻结的 MEMORY 快照
## Persistent Memory
- User prefers Python 3.12, uses pyproject.toml
- Default editor is nvim
- Working on project "atlas" in ~/code/atlas
- Timezone: US/Pacific

# 第 6 层：冻结的 USER 个人资料快照
## User Profile
- Name: Alice
- GitHub: alice-dev

# 第 7 层：技能索引
## Skills (mandatory)
Before replying, scan the skills below. If one clearly matches
your task, load it with skill_view(name) and follow its instructions.
...
<available_skills>
  software-development:
    - code-review: Structured code review workflow
    - test-driven-development: TDD methodology
  research:
    - arxiv: Search and summarize arXiv papers
</available_skills>

# 第 8 层：上下文文件（来自项目目录）
# Project Context
The following project context files have been loaded and should be followed:

## AGENTS.md
This is the atlas project. Use pytest for testing. The main
entry point is src/atlas/main.py. Always run `make lint` before
committing.

# 第 9 层：时间戳 + 会话
Current time: 2026-03-30T14:30:00-07:00
Session: abc123

# 第 10 层：平台提示
You are a CLI AI Agent. Try not to use markdown but simple text
renderable inside a terminal.
```

## SOUL.md 在提示词中的呈现方式

`SOUL.md` 位于 `~/.hermes/SOUL.md`，作为 Agent 的身份 — 系统提示词的最前面部分。`prompt_builder.py` 中的加载逻辑如下：

```python
# 来自 agent/prompt_builder.py（简化版）
def load_soul_md() -> Optional[str]:
    soul_path = get_hermes_home() / "SOUL.md"
    if not soul_path.exists():
        return None
    content = soul_path.read_text(encoding="utf-8").strip()
    content = _scan_context_content(content, "SOUL.md")  # 安全扫描
    content = _truncate_content(content, "SOUL.md")       # 上限 20k 字符
    return content
```

当 `load_soul_md()` 返回内容时，它会替换硬编码的 `DEFAULT_AGENT_IDENTITY`。然后以 `skip_soul=True` 调用 `build_context_files_prompt()` 函数，以防止 SOUL.md 出现两次（一次作为身份，一次作为上下文文件）。

如果 `SOUL.md` 不存在，系统回退到：

```
You are Hermes Agent, an intelligent AI assistant created by Nous Research.
You are helpful, knowledgeable, and direct. You assist users with a wide
range of tasks including answering questions, writing and editing code,
analyzing information, creative work, and executing actions via your tools.
You communicate clearly, admit uncertainty when appropriate, and prioritize
being genuinely useful over being verbose unless otherwise directed below.
Be targeted and efficient in your exploration and investigations.
```

## 上下文文件的注入方式

`build_context_files_prompt()` 使用**优先级系统** — 只加载一种项目上下文类型（首个匹配胜出）：

```python
# 来自 agent/prompt_builder.py（简化版）
def build_context_files_prompt(cwd=None, skip_soul=False):
    cwd_path = Path(cwd).resolve()

    # 优先级：首个匹配胜出 — 只加载一种项目上下文
    project_context = (
        _load_hermes_md(cwd_path)       # 1. .hermes.md / HERMES.md（向上遍历至 git 根目录）
        or _load_agents_md(cwd_path)    # 2. AGENTS.md（仅当前目录）
        or _load_claude_md(cwd_path)    # 3. CLAUDE.md（仅当前目录）
        or _load_cursorrules(cwd_path)  # 4. .cursorrules / .cursor/rules/*.mdc
    )

    sections = []
    if project_context:
        sections.append(project_context)

    # 来自 HERMES_HOME 的 SOUL.md（独立于项目上下文）
    if not skip_soul:
        soul_content = load_soul_md()
        if soul_content:
            sections.append(soul_content)

    if not sections:
        return ""

    return (
        "# Project Context\n\n"
        "The following project context files have been loaded "
        "and should be followed:\n\n"
        + "\n".join(sections)
    )
```

### 上下文文件发现详情

| 优先级 | 文件 | 搜索范围 | 说明 |
|--------|------|---------|------|
| 1 | `.hermes.md`、`HERMES.md` | 从 CWD 向上至 git 根目录 | Hermes 原生项目配置 |
| 2 | `AGENTS.md` | 仅 CWD | 通用 Agent 指令文件 |
| 3 | `CLAUDE.md` | 仅 CWD | Claude Code 兼容 |
| 4 | `.cursorrules`、`.cursor/rules/*.mdc` | 仅 CWD | Cursor 兼容 |

所有上下文文件都会经过：
- **安全扫描** — 检查提示词注入模式（不可见 Unicode、"忽略之前的指令"、凭证窃取尝试）
- **截断处理** — 上限 20,000 字符，使用 70/20 头/尾比例加截断标记
- **YAML 前置信息剥离** — `.hermes.md` 的前置信息会被移除（保留用于未来的配置覆盖）

## 仅 API 调用时的层

以下内容被刻意*不*持久化为缓存系统提示词的一部分：

- `ephemeral_system_prompt`
- 预填充消息
- 网关派生的会话上下文覆盖层
- 后续轮次中 Honcho 召回的内容注入到当前轮次的用户消息中

这种分离使稳定前缀保持稳定，有利于缓存。

## 记忆快照

本地记忆和用户资料数据在会话开始时作为冻结快照注入。会话中的写入操作会更新磁盘状态，但不会修改已构建的系统提示词，直到新会话开始或强制重建。

## 上下文文件

`agent/prompt_builder.py` 使用**优先级系统**扫描和清理项目上下文文件 — 只加载一种类型（首个匹配胜出）：

1. `.hermes.md` / `HERMES.md`（向上遍历至 git 根目录）
2. `AGENTS.md`（启动时的 CWD；子目录在会话期间通过 `agent/subdirectory_hints.py` 渐进式发现）
3. `CLAUDE.md`（仅 CWD）
4. `.cursorrules` / `.cursor/rules/*.mdc`（仅 CWD）

`SOUL.md` 通过 `load_soul_md()` 单独加载用于身份槽位。当它成功加载时，`build_context_files_prompt(skip_soul=True)` 会防止其重复出现。

长文件在注入前会被截断。

## 技能索引

当技能工具可用时，技能系统会向提示词贡献一个紧凑的技能索引。

## 为什么提示词组装要这样拆分

这种架构经过有意优化，目的是：

- 保持提供商侧的提示词缓存
- 避免不必要地修改历史记录
- 保持记忆语义的可理解性
- 让网关/ACP/CLI 能够添加上下文而不污染持久化的提示词状态

## 相关文档

- [上下文压缩与提示词缓存](./context-compression-and-caching.md)
- [会话存储](./session-storage.md)
- [网关内部机制](./gateway-internals.md)
