---
sidebar_position: 3
title: "创建技能"
description: "如何为 Hermes Agent 创建技能 — SKILL.md 格式、指南和发布"
---

# 创建技能

技能是向 Hermes Agent 添加新功能的首选方式。它们比工具更容易创建，不需要修改 Agent 代码，并且可以与社区共享。

## 应该做成技能还是工具？

做成**技能**当：
- 功能可以通过指令 + shell 命令 + 现有工具表达
- 它封装了 Agent 可以通过 `terminal` 或 `web_extract` 调用的外部 CLI 或 API
- 不需要 Agent 内置的自定义 Python 集成或 API 密钥管理
- 示例：arXiv 搜索、git 工作流、Docker 管理、PDF 处理、通过 CLI 工具发送邮件

做成**工具**当：
- 需要端到端集成 API 密钥、认证流程或多组件配置
- 需要每次都精确执行的自定义处理逻辑
- 处理二进制数据、流式传输或实时事件
- 示例：浏览器自动化、TTS、视觉分析

## 技能目录结构

内置技能位于 `skills/` 中，按类别组织。官方可选技能在 `optional-skills/` 中使用相同结构：

```text
skills/
├── research/
│   └── arxiv/
│       ├── SKILL.md              # 必需：主要指令
│       └── scripts/              # 可选：辅助脚本
│           └── search_arxiv.py
├── productivity/
│   └── ocr-and-documents/
│       ├── SKILL.md
│       ├── scripts/
│       └── references/
└── ...
```

## SKILL.md 格式

```markdown
---
name: my-skill
description: 简短描述（显示在技能搜索结果中）
version: 1.0.0
author: Your Name
license: MIT
platforms: [macos, linux]          # 可选 — 限制为特定操作系统平台
                                   #   有效值：macos、linux、windows
                                   #   省略则在所有平台加载（默认）
metadata:
  hermes:
    tags: [Category, Subcategory, Keywords]
    related_skills: [other-skill-name]
    requires_toolsets: [web]            # 可选 — 仅在这些工具集激活时显示
    requires_tools: [web_search]        # 可选 — 仅在这些工具可用时显示
    fallback_for_toolsets: [browser]    # 可选 — 当这些工具集激活时隐藏
    fallback_for_tools: [browser_navigate]  # 可选 — 当这些工具存在时隐藏
    config:                              # 可选 — 技能需要的 config.yaml 设置
      - key: my.setting
        description: "此设置控制什么"
        default: "sensible-default"
        prompt: "设置的显示提示"
required_environment_variables:          # 可选 — 技能需要的环境变量
  - name: MY_API_KEY
    prompt: "输入你的 API 密钥"
    help: "在 https://example.com 获取"
    required_for: "API 访问"
---

# 技能标题

简要介绍。

## 何时使用
触发条件 — Agent 何时应该加载此技能？

## 快速参考
常用命令或 API 调用表。

## 过程
Agent 遵循的分步指令。

## 陷阱
已知的失败模式及其处理方法。

## 验证
Agent 如何确认它已成功工作。
```

### 平台特定技能

技能可以使用 `platforms` 字段限制为特定操作系统：

```yaml
platforms: [macos]            # 仅 macOS（例如 iMessage、Apple 提醒事项）
platforms: [macos, linux]     # macOS 和 Linux
platforms: [windows]          # 仅 Windows
```

设置后，该技能会自动从不兼容平台的系统提示词、`skills_list()` 和斜杠命令中隐藏。如果省略或为空，技能在所有平台加载（向后兼容）。

### 条件式技能激活

技能可以声明对特定工具或工具集的依赖。这控制技能是否出现在给定会话的系统提示词中。

```yaml
metadata:
  hermes:
    requires_toolsets: [web]           # 如果 web 工具集未激活则隐藏
    requires_tools: [web_search]       # 如果 web_search 工具不可用则隐藏
    fallback_for_toolsets: [browser]   # 如果 browser 工具集已激活则隐藏
    fallback_for_tools: [browser_navigate]  # 如果 browser_navigate 已可用则隐藏
```

| 字段 | 行为 |
|------|------|
| `requires_toolsets` | 当列出的任何工具集**不可用**时，技能被**隐藏** |
| `requires_tools` | 当列出的任何工具**不可用**时，技能被**隐藏** |
| `fallback_for_toolsets` | 当列出的任何工具集**已可用**时，技能被**隐藏** |
| `fallback_for_tools` | 当列出的任何工具**已可用**时，技能被**隐藏** |

**`fallback_for_*` 的使用场景：**创建一个在主要工具不可用时作为替代方案的技能。例如，带有 `fallback_for_tools: [web_search]` 的 `duckduckgo-search` 技能只在网页搜索工具（需要 API 密钥）未配置时显示。

**`requires_*` 的使用场景：**创建一个只有在某些工具存在时才有意义的技能。例如，带有 `requires_toolsets: [web]` 的网页抓取工作流技能在 web 工具禁用时不会让提示词变得杂乱。

### 环境变量要求

技能可以声明它们需要的环境变量。当技能通过 `skill_view` 加载时，其所需变量会自动注册以透传到沙箱执行环境（terminal、execute_code）。

```yaml
required_environment_variables:
  - name: TENOR_API_KEY
    prompt: "Tenor API 密钥"               # 提示用户时显示
    help: "在 https://tenor.com 获取密钥"  # 帮助文本或 URL
    required_for: "GIF 搜索功能"           # 什么功能需要此变量
```

每个条目支持：
- `name`（必需）— 环境变量名称
- `prompt`（可选）— 向用户请求值时的提示文本
- `help`（可选）— 获取值的帮助文本或 URL
- `required_for`（可选）— 描述哪个功能需要此变量

用户也可以在 `config.yaml` 中手动配置透传变量：

```yaml
terminal:
  env_passthrough:
    - MY_CUSTOM_VAR
    - ANOTHER_VAR
```

参见 `skills/apple/` 中 macOS 专用技能的示例。

## 加载时的安全设置

当技能需要 API 密钥或 token 时使用 `required_environment_variables`。缺失的值**不会**从发现中隐藏技能。相反，Hermes 在本地 CLI 中加载技能时会安全地提示输入。

```yaml
required_environment_variables:
  - name: TENOR_API_KEY
    prompt: Tenor API key
    help: Get a key from https://developers.google.com/tenor
    required_for: full functionality
```

用户可以跳过设置继续加载技能。Hermes 永远不会将原始密钥值暴露给模型。网关和消息会话显示本地设置指导而不是在线收集密钥。

:::tip 沙箱透传
当你的技能被加载时，任何已设置的已声明 `required_environment_variables` 会**自动透传**到 `execute_code` 和 `terminal` 沙箱 — 包括 Docker 和 Modal 等远程后端。你的技能脚本可以访问 `$TENOR_API_KEY`（或 Python 中的 `os.environ["TENOR_API_KEY"]`）而无需用户额外配置。详见[环境变量透传](/docs/user-guide/security#environment-variable-passthrough)。
:::

旧版 `prerequisites.env_vars` 作为向后兼容别名仍然支持。

### 配置设置（config.yaml）

技能可以声明存储在 `config.yaml` 的 `skills.config` 命名空间下的非密钥设置。与环境变量（存储在 `.env` 中的密钥）不同，配置设置用于路径、偏好和其他非敏感值。

```yaml
metadata:
  hermes:
    config:
      - key: myplugin.path
        description: 插件数据目录路径
        default: "~/myplugin-data"
        prompt: 插件数据目录路径
      - key: myplugin.domain
        description: 插件运作的领域
        default: ""
        prompt: 插件领域（例如 AI/ML 研究）
```

每个条目支持：
- `key`（必需）— 设置的点路径（例如 `myplugin.path`）
- `description`（必需）— 解释设置控制什么
- `default`（可选）— 用户未配置时的默认值
- `prompt`（可选）— `hermes config migrate` 期间显示的提示文本；回退到 `description`

**工作原理：**

1. **存储：**值写入 `config.yaml` 的 `skills.config.<key>` 下：
   ```yaml
   skills:
     config:
       myplugin:
         path: ~/my-data
   ```

2. **发现：**`hermes config migrate` 扫描所有启用的技能，找到未配置的设置并提示用户。设置也显示在 `hermes config show` 的"技能设置"下。

3. **运行时注入：**技能加载时，其配置值被解析并追加到技能消息：
   ```
   [Skill config (from ~/.hermes/config.yaml):
     myplugin.path = /home/user/my-data
   ]
   ```
   Agent 无需自己读取 `config.yaml` 即可看到配置的值。

4. **手动设置：**用户也可以直接设置值：
   ```bash
   hermes config set skills.config.myplugin.path ~/my-data
   ```

:::tip 何时使用哪个
使用 `required_environment_variables` 用于 API 密钥、token 和其他**密钥**（存储在 `~/.hermes/.env` 中，永远不会显示给模型）。使用 `config` 用于**路径、偏好和非敏感设置**（存储在 `config.yaml` 中，在 config show 中可见）。
:::

### 凭证文件要求（OAuth token 等）

使用 OAuth 或基于文件的凭证的技能可以声明需要挂载到远程沙箱的文件。这适用于存储为**文件**（非环境变量）的凭证 — 通常是设置脚本产生的 OAuth token 文件。

```yaml
required_credential_files:
  - path: google_token.json
    description: Google OAuth2 token（由设置脚本创建）
  - path: google_client_secret.json
    description: Google OAuth2 客户端凭证
```

每个条目支持：
- `path`（必需）— 相对于 `~/.hermes/` 的文件路径
- `description`（可选）— 解释文件是什么以及如何创建

加载时，Hermes 检查这些文件是否存在。缺失的文件触发 `setup_needed`。已有的文件会自动：
- 以只读绑定挂载方式**挂载到 Docker** 容器
- **同步到 Modal** 沙箱（创建时 + 每个命令前，因此会话中的 OAuth 也能工作）
- 在**本地**后端无需特殊处理

:::tip 何时使用哪个
使用 `required_environment_variables` 用于简单的 API 密钥和 token（存储在 `~/.hermes/.env` 中的字符串）。使用 `required_credential_files` 用于 OAuth token 文件、客户端密钥、服务账户 JSON、证书或任何磁盘上的凭证文件。
:::

参见 `skills/productivity/google-workspace/SKILL.md` 查看同时使用两者的完整示例。

## 技能指南

### 无外部依赖

优先使用标准库 Python、curl 和现有 Hermes 工具（`web_extract`、`terminal`、`read_file`）。如果需要依赖，在技能中记录安装步骤。

### 渐进式披露

将最常用的工作流放在最前面。边缘情况和高级用法放在底部。这可以减少常见任务的 token 使用量。

### 包含辅助脚本

对于 XML/JSON 解析或复杂逻辑，在 `scripts/` 中包含辅助脚本 — 不要指望 LLM 每次都内联编写解析器。

### 测试它

运行技能并验证 Agent 正确遵循指令：

```bash
hermes chat --toolsets skills -q "Use the X skill to do Y"
```

## 技能应该放在哪里？

内置技能（在 `skills/` 中）随每次 Hermes 安装一起分发。它们应该**对大多数用户广泛有用**：

- 文档处理、网络研究、常见开发工作流、系统管理
- 被广泛的人群经常使用

如果你的技能是官方的且有用但不是普遍需要的（例如付费服务集成、重量级依赖），放在 **`optional-skills/`** 中 — 它随仓库分发，可通过 `hermes skills browse` 发现（标记为"official"），并以内置信任安装。

如果你的技能是专业的、社区贡献的或小众的，更适合放在**技能中心** — 上传到注册表并通过 `hermes skills install` 共享。

## 发布技能

### 到技能中心

```bash
hermes skills publish skills/my-skill --to github --repo owner/repo
```

### 到自定义仓库

将你的仓库添加为 tap：

```bash
hermes skills tap add owner/repo
```

用户然后可以从你的仓库搜索和安装。

## 安全扫描

所有从中心安装的技能都经过安全扫描检查：

- 数据外泄模式
- 提示词注入尝试
- 破坏性命令
- Shell 注入

信任级别：
- `builtin` — 随 Hermes 分发（始终信任）
- `official` — 来自仓库的 `optional-skills/`（内置信任，无第三方警告）
- `trusted` — 来自 openai/skills、anthropics/skills
- `community` — 非危险发现可通过 `--force` 覆盖；`dangerous` 判定仍然被阻止

Hermes 现在可以从多个外部发现模型消费第三方技能：
- 直接 GitHub 标识符（例如 `openai/skills/k8s`）
- `skills.sh` 标识符（例如 `skills-sh/vercel-labs/json-render/json-render-react`）
- 从 `/.well-known/skills/index.json` 提供的知名端点

如果你希望你的技能在不依赖 GitHub 特定安装器的情况下可被发现，除了在仓库或市场发布外，还可以考虑从知名端点提供它们。
