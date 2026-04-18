---
sidebar_position: 6
title: "使用 MCP 与 Hermes"
description: "将 MCP 服务器连接到 Hermes Agent 的实用指南，包括工具过滤和在实际工作流中安全使用"
---

# 使用 MCP 与 Hermes

本指南介绍如何在日常工作流中实际使用 MCP 与 Hermes Agent。

如果功能页面解释了什么是 MCP，那么本指南则是关于如何快速安全地从中获取价值。

## 什么时候应该使用 MCP？

使用 MCP 的场景：
- 工具已经以 MCP 形式存在，你不想构建原生 Hermes 工具
- 你希望 Hermes 通过干净的 RPC 层操作本地或远程系统
- 你需要细粒度的服务器级别暴露控制
- 你想将 Hermes 连接到内部 API、数据库或公司系统，而不修改 Hermes 核心

不建议使用 MCP 的场景：
- 内置的 Hermes 工具已经能很好地完成工作
- 服务器暴露了大量危险的工具表面，而你还没准备好进行过滤
- 你只需要一个非常窄的集成，原生工具会更简单、更安全

## 心智模型

把 MCP 想象成一个适配层：

- Hermes 仍然是代理
- MCP 服务器贡献工具
- Hermes 在启动或重新加载时发现这些工具
- 模型可以像使用普通工具一样使用它们
- 你控制每个服务器的可见程度

最后一点很重要。好的 MCP 使用不是"连接所有东西"，而是"连接正确的东西，使用最小的有用表面"。

## 步骤 1：安装 MCP 支持

如果你使用标准安装脚本安装了 Hermes，MCP 支持已经包含在内（安装器运行了 `uv pip install -e ".[all]"`）。

如果你安装时没有额外依赖，需要单独添加 MCP：

```bash
cd ~/.hermes/hermes-agent
uv pip install -e ".[mcp]"
```

对于基于 npm 的服务器，确保 Node.js 和 `npx` 可用。

对于许多 Python MCP 服务器，`uvx` 是一个不错的默认选择。

## 步骤 2：先添加一个服务器

从一个安全的单一服务器开始。

示例：仅对一个项目目录的文件系统访问。

```yaml
mcp_servers:
  project_fs:
    command: "npx"
    args: ["-y", "@modelcontextprotocol/server-filesystem", "/home/user/my-project"]
```

然后启动 Hermes：

```bash
hermes chat
```

现在问一些具体的问题：

```text
Inspect this project and summarize the repo layout.
```

## 步骤 3：验证 MCP 已加载

你可以通过几种方式验证 MCP：

- Hermes 横幅/状态应在配置后显示 MCP 集成
- 问 Hermes 它有哪些可用的工具
- 在配置更改后使用 `/reload-mcp`
- 如果服务器连接失败，检查日志

一个实际的测试提示：

```text
Tell me which MCP-backed tools are available right now.
```

## 步骤 4：立即开始过滤

如果服务器暴露了很多工具，不要等到以后再处理。

### 示例：只允许你需要的

```yaml
mcp_servers:
  github:
    command: "npx"
    args: ["-y", "@modelcontextprotocol/server-github"]
    env:
      GITHUB_PERSONAL_ACCESS_TOKEN: "***"
    tools:
      include: [list_issues, create_issue, search_code]
```

对于敏感系统，这通常是最佳默认策略。

### 示例：排除危险操作

```yaml
mcp_servers:
  stripe:
    url: "https://mcp.stripe.com"
    headers:
      Authorization: "Bearer ***"
    tools:
      exclude: [delete_customer, refund_payment]
```

### 示例：禁用工具包装器

```yaml
mcp_servers:
  docs:
    url: "https://mcp.docs.example.com"
    tools:
      prompts: false
      resources: false
```

## 过滤实际影响什么？

Hermes 中 MCP 暴露的功能有两类：

1. 服务器原生 MCP 工具
- 通过以下方式过滤：
  - `tools.include`
  - `tools.exclude`

2. Hermes 添加的工具包装器
- 通过以下方式过滤：
  - `tools.resources`
  - `tools.prompts`

### 你可能看到的工具包装器

资源：
- `list_resources`
- `read_resource`

提示：
- `list_prompts`
- `get_prompt`

这些包装器仅在以下情况下出现：
- 你的配置允许它们，且
- MCP 服务器会话实际支持这些能力

所以如果服务器不支持资源/提示，Hermes 不会假装它有。

## 常见模式

### 模式 1：本地项目助手

当你想让 Hermes 在有限的工作空间上推理时，使用 MCP 连接仓库本地的文件系统或 git 服务器。

```yaml
mcp_servers:
  fs:
    command: "npx"
    args: ["-y", "@modelcontextprotocol/server-filesystem", "/home/user/project"]

  git:
    command: "uvx"
    args: ["mcp-server-git", "--repository", "/home/user/project"]
```

好的提示：

```text
Review the project structure and identify where configuration lives.
```

```text
Check the local git state and summarize what changed recently.
```

### 模式 2：GitHub 分类助手

```yaml
mcp_servers:
  github:
    command: "npx"
    args: ["-y", "@modelcontextprotocol/server-github"]
    env:
      GITHUB_PERSONAL_ACCESS_TOKEN: "***"
    tools:
      include: [list_issues, create_issue, update_issue, search_code]
      prompts: false
      resources: false
```

好的提示：

```text
List open issues about MCP, cluster them by theme, and draft a high-quality issue for the most common bug.
```

```text
Search the repo for uses of _discover_and_register_server and explain how MCP tools are registered.
```

### 模式 3：内部 API 助手

```yaml
mcp_servers:
  internal_api:
    url: "https://mcp.internal.example.com"
    headers:
      Authorization: "Bearer ***"
    tools:
      include: [list_customers, get_customer, list_invoices]
      resources: false
      prompts: false
```

好的提示：

```text
Look up customer ACME Corp and summarize recent invoice activity.
```

这正是严格白名单比排除列表好得多的场景。

### 模式 4：文档/知识服务器

一些 MCP 服务器暴露的提示或资源更像是共享知识资产，而非直接操作。

```yaml
mcp_servers:
  docs:
    url: "https://mcp.docs.example.com"
    tools:
      prompts: true
      resources: true
```

好的提示：

```text
List available MCP resources from the docs server, then read the onboarding guide and summarize it.
```

```text
List prompts exposed by the docs server and tell me which ones would help with incident response.
```

## 教程：端到端设置与过滤

这是一个实际的渐进过程。

### 阶段 1：添加带严格白名单的 GitHub MCP

```yaml
mcp_servers:
  github:
    command: "npx"
    args: ["-y", "@modelcontextprotocol/server-github"]
    env:
      GITHUB_PERSONAL_ACCESS_TOKEN: "***"
    tools:
      include: [list_issues, create_issue, search_code]
      prompts: false
      resources: false
```

启动 Hermes 并询问：

```text
Search the codebase for references to MCP and summarize the main integration points.
```

### 阶段 2：仅在需要时扩展

如果你后来还需要更新 issue：

```yaml
tools:
  include: [list_issues, create_issue, update_issue, search_code]
```

然后重新加载：

```text
/reload-mcp
```

### 阶段 3：添加具有不同策略的第二个服务器

```yaml
mcp_servers:
  github:
    command: "npx"
    args: ["-y", "@modelcontextprotocol/server-github"]
    env:
      GITHUB_PERSONAL_ACCESS_TOKEN: "***"
    tools:
      include: [list_issues, create_issue, update_issue, search_code]
      prompts: false
      resources: false

  filesystem:
    command: "npx"
    args: ["-y", "@modelcontextprotocol/server-filesystem", "/home/user/project"]
```

现在 Hermes 可以将它们结合起来：

```text
Inspect the local project files, then create a GitHub issue summarizing the bug you find.
```

这就是 MCP 强大的地方：无需更改 Hermes 核心即可实现多系统工作流。

## 安全使用建议

### 对危险系统优先使用允许列表

对于任何金融、面向客户或具有破坏性的系统：
- 使用 `tools.include`
- 从尽可能小的集合开始

### 禁用未使用的工具

如果你不想让模型浏览服务器提供的资源/提示，将它们关闭：

```yaml
tools:
  resources: false
  prompts: false
```

### 保持服务器范围狭窄

示例：
- 文件系统服务器只限定到一个项目目录，而非整个主目录
- Git 服务器指向一个仓库
- 内部 API 服务器默认使用读取为主的工具暴露

### 配置更改后重新加载

```text
/reload-mcp
```

在更改以下内容后执行此操作：
- include/exclude 列表
- enabled 标志
- resources/prompts 开关
- 认证头/环境变量

## 按症状排查问题

### "服务器连接了但我期望的工具缺失"

可能原因：
- 被 `tools.include` 过滤
- 被 `tools.exclude` 排除
- 工具包装器通过 `resources: false` 或 `prompts: false` 被禁用
- 服务器实际上不支持资源/提示

### "服务器已配置但什么都没加载"

检查：
- 配置中没有遗留 `enabled: false`
- 命令/运行时存在（`npx`、`uvx` 等）
- HTTP 端点可达
- 认证环境变量或请求头正确

### "为什么我看到的工具比 MCP 服务器公告的少？"

因为 Hermes 现在尊重你的服务器策略和基于能力的注册。这是预期行为，通常也是理想的。

### "如何在不删除配置的情况下移除 MCP 服务器？"

使用：

```yaml
enabled: false
```

这保留了配置但阻止连接和注册。

## 推荐的首次 MCP 设置

大多数用户的好的首选服务器：
- 文件系统
- git
- GitHub
- fetch/文档 MCP 服务器
- 一个窄范围的内部 API

不太好的首选服务器：
- 拥有大量破坏性操作且没有过滤的大型业务系统
- 任何你不够了解而无法约束的系统

## 相关文档

- [MCP（模型上下文协议）](/docs/user-guide/features/mcp)
- [常见问题](/docs/reference/faq)
- [斜杠命令](/docs/reference/slash-commands)
