---
sidebar_position: 12
title: "使用技能"
description: "查找、安装、使用和创建技能 — 按需知识，教会 Hermes 新的工作流"
---

# 使用技能

技能是按需知识文档，教会 Hermes 如何处理特定任务 — 从生成 ASCII 艺术到管理 GitHub PR。本指南带你了解日常使用方法。

完整技术参考请参阅 [技能系统](/docs/user-guide/features/skills)。

---

## 查找技能

每个 Hermes 安装都自带内置技能。查看可用的技能：

```bash
# 在任何聊天会话中：
/skills

# 或从 CLI：
hermes skills list
```

这会显示带有名称和描述的紧凑列表：

```
ascii-art         Generate ASCII art using pyfiglet, cowsay, boxes...
arxiv             Search and retrieve academic papers from arXiv...
github-pr-workflow Full PR lifecycle — create branches, commit...
plan              Plan mode — inspect context, write a markdown...
excalidraw        Create hand-drawn style diagrams using Excalidraw...
```

### 搜索技能

```bash
# 按关键字搜索
/skills search docker
/skills search music
```

### 技能中心

官方可选技能（较重或利基的技能，默认不激活）通过中心提供：

```bash
# 浏览官方可选技能
/skills browse

# 搜索中心
/skills search blockchain
```

---

## 使用技能

每个已安装的技能自动成为斜杠命令。只需输入其名称：

```bash
# 加载技能并给它一个任务
/ascii-art Make a banner that says "HELLO WORLD"
/plan Design a REST API for a todo app
/github-pr-workflow Create a PR for the auth refactor

# 只输入技能名称（不带任务）加载它，让你描述需求
/excalidraw
```

你也可以通过自然对话触发技能 — 让 Hermes 使用特定技能，它会通过 `skill_view` 工具加载。

### 渐进式披露

技能使用 token 高效的加载模式。代理不会一次加载所有内容：

1. **`skills_list()`** — 所有技能的紧凑列表（约 3k token）。在会话开始时加载。
2. **`skill_view(name)`** — 一个技能的完整 SKILL.md 内容。当代理决定需要该技能时加载。
3. **`skill_view(name, file_path)`** — 技能内的特定参考文件。仅在需要时加载。

这意味着技能在实际使用前不会消耗 token。

---

## 从中心安装

官方可选技能随 Hermes 一起发布，但默认不激活。需要显式安装：

```bash
# 安装官方可选技能
hermes skills install official/research/arxiv

# 在聊天会话中安装
/skills install official/creative/songwriting-and-ai-music
```

安装后会发生什么：
1. 技能目录被复制到 `~/.hermes/skills/`
2. 它出现在你的 `skills_list` 输出中
3. 它作为斜杠命令可用

:::tip
已安装的技能在新会话中生效。如果你想在当前会话中使用，运行 `/reset` 重新开始，或添加 `--now` 立即使提示缓存失效（下一轮会消耗更多 token）。
:::

### 验证安装

```bash
# 检查是否存在
hermes skills list | grep arxiv

# 或在聊天中
/skills search arxiv
```

---

## 插件提供的技能

插件可以使用命名空间名称（`plugin:skill`）捆绑自己的技能。这防止与内置技能的名称冲突。

```bash
# 通过限定名称加载插件技能
skill_view("superpowers:writing-plans")

# 同名的内置技能不受影响
skill_view("writing-plans")
```

插件技能**不会**列在系统提示中，也不会出现在 `skills_list` 中。它们是可选的 — 当你知道插件提供某个技能时显式加载。加载时，代理会看到一个横幅，列出同一插件的兄弟技能。

关于如何在你自己的插件中发布技能，请参阅 [构建 Hermes 插件 → 捆绑技能](/docs/guides/build-a-hermes-plugin#bundle-skills)。

---

## 配置技能设置

某些技能在其 frontmatter 中声明需要的配置：

```yaml
metadata:
  hermes:
    config:
      - key: tenor.api_key
        description: "Tenor API key for GIF search"
        prompt: "Enter your Tenor API key"
        url: "https://developers.google.com/tenor/guides/quickstart"
```

当带配置的技能首次加载时，Hermes 会提示你输入值。它们存储在 `config.yaml` 的 `skills.config.*` 下。

从 CLI 管理技能配置：

```bash
# 特定技能的交互式配置
hermes skills config gif-search

# 查看所有技能配置
hermes config get skills.config
```

---

## 创建你自己的技能

技能只是带有 YAML frontmatter 的 markdown 文件。创建一个不到五分钟。

### 1. 创建目录

```bash
mkdir -p ~/.hermes/skills/my-category/my-skill
```

### 2. 编写 SKILL.md

```markdown title="~/.hermes/skills/my-category/my-skill/SKILL.md"
---
name: my-skill
description: Brief description of what this skill does
version: 1.0.0
metadata:
  hermes:
    tags: [my-tag, automation]
    category: my-category
---

# My Skill

## When to Use
Use this skill when the user asks about [specific topic] or needs to [specific task].

## Procedure
1. First, check if [prerequisite] is available
2. Run `command --with-flags`
3. Parse the output and present results

## Pitfalls
- Common failure: [description]. Fix: [solution]
- Watch out for [edge case]

## Verification
Run `check-command` to confirm the result is correct.
```

### 3. 添加参考文件（可选）

技能可以包含代理按需加载的支持文件：

```
my-skill/
├── SKILL.md                    # 主技能文档
├── references/
│   ├── api-docs.md             # 代理可查阅的 API 参考
│   └── examples.md             # 输入/输出示例
├── templates/
│   └── config.yaml             # 代理可使用的模板文件
└── scripts/
    └── setup.sh                # 代理可执行的脚本
```

在 SKILL.md 中引用：

```markdown
For API details, load the reference: `skill_view("my-skill", "references/api-docs.md")`
```

### 4. 测试

启动新会话并尝试你的技能：

```bash
hermes chat -q "/my-skill help me with the thing"
```

技能会自动出现 — 无需注册。将其放入 `~/.hermes/skills/` 即可生效。

:::info
代理也可以使用 `skill_manage` 自行创建和更新技能。在解决复杂问题后，Hermes 可能会主动将方法保存为技能供下次使用。
:::

---

## 按平台管理技能

控制哪些技能在哪些平台上可用：

```bash
hermes skills
```

这会打开一个交互式 TUI，你可以在其中按平台（CLI、Telegram、Discord 等）启用或禁用技能。当你想让某些技能只在特定场景中可用时很有用 — 例如，让开发技能不出现在 Telegram 上。

---

## 技能 vs 记忆

两者都跨会话持久化，但服务于不同目的：

| | 技能 | 记忆 |
|---|---|---|
| **内容** | 程序性知识 — 如何做事 | 事实性知识 — 事物是什么 |
| **时机** | 按需加载，仅在相关时 | 自动注入到每个会话 |
| **大小** | 可以很大（数百行） | 应保持紧凑（仅关键事实） |
| **成本** | 加载前零 token | 小但持续的 token 成本 |
| **示例** | "如何部署到 Kubernetes" | "用户喜欢深色模式，位于 PST 时区" |
| **谁创建** | 你、代理或从中心安装 | 代理，基于对话 |

**经验法则：** 如果你会把它放在参考文档中，那它是技能。如果你会把它写在便签上，那它是记忆。

---

## 技巧

**保持技能聚焦。** 试图涵盖"所有 DevOps"的技能太长太模糊。涵盖"将 Python 应用部署到 Fly.io"的技能足够具体，真正有用。

**让代理创建技能。** 在复杂的多步骤任务后，Hermes 通常会提议将方法保存为技能。同意 — 这些代理编写的技能捕获了确切的工作流，包括沿途发现的陷阱。

**使用分类。** 将技能组织到子目录中（`~/.hermes/skills/devops/`、`~/.hermes/skills/research/` 等）。这使列表可管理，并帮助代理更快找到相关技能。

**技能过时时更新。** 如果你使用技能时遇到它未涵盖的问题，告诉 Hermes 用你学到的更新技能。不维护的技能会变成负担。

---

*完整技能参考 — frontmatter 字段、条件激活、外部目录等 — 请参阅 [技能系统](/docs/user-guide/features/skills)。*
