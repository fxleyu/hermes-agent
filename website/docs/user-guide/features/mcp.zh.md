---
sidebar_position: 4
title: "MCP（模型上下文协议）"
description: "通过 MCP 将 Hermes Agent 连接到外部工具服务器 — 并精确控制 Hermes 加载哪些 MCP 工具"
---

# MCP（模型上下文协议）

MCP 让 Hermes Agent 连接到外部工具服务器，以便代理可以使用 Hermes 自身之外的工具 — GitHub、数据库、文件系统、浏览器栈、内部 API 等。

如果你曾想让 Hermes 使用已存在于其他地方的工具，MCP 通常是最简洁的方式。

## MCP 为你提供什么

- 无需先编写原生 Hermes 工具即可访问外部工具生态系统
- 在同一配置中使用本地 stdio 服务器和远程 HTTP MCP 服务器
- 启动时自动发现和注册工具
- 在服务器支持时为 MCP 资源和提示提供实用工具包装器
- 每服务器过滤，以便你只暴露希望 Hermes 看到的 MCP 工具

## 快速入门

1. 安装 MCP 支持（如果使用标准安装脚本则已包含）：

```bash
cd ~/.hermes/hermes-agent
uv pip install -e ".[mcp]"
```

2. 在 `~/.hermes/config.yaml` 中添加 MCP 服务器：

```yaml
mcp_servers:
  filesystem:
    command: "npx"
    args: ["-y", "@modelcontextprotocol/server-filesystem", "/home/user/projects"]
```

3. 启动 Hermes：

```bash
hermes chat
```

4. 让 Hermes 使用 MCP 支持的功能。

例如：

```text
列出 /home/user/projects 中的文件并总结仓库结构。
```

Hermes 将发现 MCP 服务器的工具并像使用任何其他工具一样使用它们。

## 两种 MCP 服务器

### Stdio 服务器

Stdio 服务器作为本地子进程运行，通过 stdin/stdout 通信。

```yaml
mcp_servers:
  github:
    command: "npx"
    args: ["-y", "@modelcontextprotocol/server-github"]
    env:
      GITHUB_PERSONAL_ACCESS_TOKEN: "***"
```

在以下情况使用 stdio 服务器：
- 服务器安装在本地
- 你想要低延迟访问本地资源
- 你正在遵循显示 `command`、`args` 和 `env` 的 MCP 服务器文档

### HTTP 服务器

HTTP MCP 服务器是 Hermes 直接连接的远程端点。

```yaml
mcp_servers:
  remote_api:
    url: "https://mcp.example.com/mcp"
    headers:
      Authorization: "Bearer ***"
```

在以下情况使用 HTTP 服务器：
- MCP 服务器托管在其他地方
- 你的组织暴露了内部 MCP 端点
- 你不希望 Hermes 为该集成生成本地子进程

## 基本配置参考

Hermes 从 `~/.hermes/config.yaml` 中的 `mcp_servers` 读取 MCP 配置。

### 常用键

| 键 | 类型 | 含义 |
|---|---|---|
| `command` | string | stdio MCP 服务器的可执行文件 |
| `args` | list | stdio 服务器的参数 |
| `env` | mapping | 传递给 stdio 服务器的环境变量 |
| `url` | string | HTTP MCP 端点 |
| `headers` | mapping | 远程服务器的 HTTP 头 |
| `timeout` | number | 工具调用超时 |
| `connect_timeout` | number | 初始连接超时 |
| `enabled` | bool | 如果为 `false`，Hermes 完全跳过该服务器 |
| `tools` | mapping | 每服务器工具过滤和实用策略 |

### 最小 stdio 示例

```yaml
mcp_servers:
  filesystem:
    command: "npx"
    args: ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
```

### 最小 HTTP 示例

```yaml
mcp_servers:
  company_api:
    url: "https://mcp.internal.example.com"
    headers:
      Authorization: "Bearer ***"
```

## Hermes 如何注册 MCP 工具

Hermes 为 MCP 工具添加前缀以避免与内置名称冲突：

```text
mcp_<server_name>_<tool_name>
```

示例：

| 服务器 | MCP 工具 | 注册名称 |
|---|---|---|
| `filesystem` | `read_file` | `mcp_filesystem_read_file` |
| `github` | `create-issue` | `mcp_github_create_issue` |
| `my-api` | `query.data` | `mcp_my_api_query_data` |

实际上，你通常不需要手动调用带前缀的名称 — Hermes 看到工具并在正常推理过程中选择它。

## MCP 实用工具

在支持时，Hermes 还会围绕 MCP 资源和提示注册实用工具：

- `list_resources`
- `read_resource`
- `list_prompts`
- `get_prompt`

这些以相同的前缀模式按服务器注册，例如：

- `mcp_github_list_resources`
- `mcp_github_get_prompt`

### 重要

这些实用工具现在是能力感知的：
- Hermes 仅在 MCP 会话实际支持资源操作时才注册资源实用工具
- Hermes 仅在 MCP 会话实际支持提示操作时才注册提示实用工具

因此，暴露可调用工具但不暴露资源/提示的服务器不会获得那些额外的包装器。

## 每服务器过滤

你可以控制每个 MCP 服务器向 Hermes 贡献哪些工具，实现工具命名空间的细粒度管理。

### 完全禁用服务器

```yaml
mcp_servers:
  legacy:
    url: "https://mcp.legacy.internal"
    enabled: false
```

如果 `enabled: false`，Hermes 完全跳过该服务器，甚至不会尝试连接。

### 白名单服务器工具

```yaml
mcp_servers:
  github:
    command: "npx"
    args: ["-y", "@modelcontextprotocol/server-github"]
    env:
      GITHUB_PERSONAL_ACCESS_TOKEN: "***"
    tools:
      include: [create_issue, list_issues]
```

仅注册这些 MCP 服务器工具。

### 黑名单服务器工具

```yaml
mcp_servers:
  stripe:
    url: "https://mcp.stripe.com"
    tools:
      exclude: [delete_customer]
```

注册所有服务器工具，被排除的除外。

### 优先规则

如果两者都存在：

```yaml
tools:
  include: [create_issue]
  exclude: [create_issue, delete_issue]
```

`include` 优先。

### 也过滤实用工具

你还可以单独禁用 Hermes 添加的实用包装器：

```yaml
mcp_servers:
  docs:
    url: "https://mcp.docs.example.com"
    tools:
      prompts: false
      resources: false
```

这意味着：
- `tools.resources: false` 禁用 `list_resources` 和 `read_resource`
- `tools.prompts: false` 禁用 `list_prompts` 和 `get_prompt`

### 完整示例

```yaml
mcp_servers:
  github:
    command: "npx"
    args: ["-y", "@modelcontextprotocol/server-github"]
    env:
      GITHUB_PERSONAL_ACCESS_TOKEN: "***"
    tools:
      include: [create_issue, list_issues, search_code]
      prompts: false

  stripe:
    url: "https://mcp.stripe.com"
    headers:
      Authorization: "Bearer ***"
    tools:
      exclude: [delete_customer]
      resources: false

  legacy:
    url: "https://mcp.legacy.internal"
    enabled: false
```

## 如果所有工具都被过滤了会怎样？

如果你的配置过滤掉了所有可调用工具并禁用或省略了所有支持的实用工具，Hermes 不会为该服务器创建空的运行时 MCP 工具集。

这保持了工具列表的整洁。

## 运行时行为

### 发现时间

Hermes 在启动时发现 MCP 服务器并将其工具注册到正常的工具注册表中。

### 动态工具发现

MCP 服务器可以通过发送 `notifications/tools/list_changed` 通知来告知 Hermes 其可用工具在运行时发生了变化。当 Hermes 收到此通知时，它会自动重新获取服务器的工具列表并更新注册表 — 无需手动 `/reload-mcp`。

这对于能力动态变化的 MCP 服务器很有用（例如，当加载新的数据库模式时添加工具的服务器，或当服务离线时删除工具的服务器）。

刷新受锁保护，因此来自同一服务器的快速连续通知不会导致重叠刷新。提示和资源变更通知（`prompts/list_changed`、`resources/list_changed`）已接收但尚未采取操作。

### 重新加载

如果更改了 MCP 配置，使用：

```text
/reload-mcp
```

这会从配置重新加载 MCP 服务器并刷新可用工具列表。对于服务器本身推送的运行时工具变更，请参阅上方的[动态工具发现](#动态工具发现)。

### 工具集

每个配置的 MCP 服务器在贡献至少一个注册工具时也会创建一个运行时工具集：

```text
mcp-<server>
```

这使得在工具集级别更容易理解 MCP 服务器。

## 安全模型

### Stdio 环境过滤

对于 stdio 服务器，Hermes 不会盲目传递你的完整 shell 环境。

只有显式配置的 `env` 加上安全基线被传递。这减少了意外的密钥泄露。

### 配置级暴露控制

新的过滤支持也是安全控制：
- 禁用你不希望模型看到的危险工具
- 仅为敏感服务器暴露最小白名单
- 当你不希望暴露该表面时禁用资源/提示包装器

## 使用场景示例

### 具有最小问题管理界面的 GitHub 服务器

```yaml
mcp_servers:
  github:
    command: "npx"
    args: ["-y", "@modelcontextprotocol/server-github"]
    env:
      GITHUB_PERSONAL_ACCESS_TOKEN: "***"
    tools:
      include: [list_issues, create_issue, update_issue]
      prompts: false
      resources: false
```

这样使用：

```text
显示标记为 bug 的开放问题，然后为不稳定的 MCP 重连行为起草一个新问题。
```

### 移除危险操作的 Stripe 服务器

```yaml
mcp_servers:
  stripe:
    url: "https://mcp.stripe.com"
    headers:
      Authorization: "Bearer ***"
    tools:
      exclude: [delete_customer, refund_payment]
```

这样使用：

```text
查找最近 10 次失败的支付并总结常见的失败原因。
```

### 用于单个项目根目录的文件系统服务器

```yaml
mcp_servers:
  project_fs:
    command: "npx"
    args: ["-y", "@modelcontextprotocol/server-filesystem", "/home/user/my-project"]
```

这样使用：

```text
检查项目根目录并解释目录布局。
```

## 故障排除

### MCP 服务器未连接

检查：

```bash
# 验证 MCP 依赖已安装（标准安装中已包含）
cd ~/.hermes/hermes-agent && uv pip install -e ".[mcp]"

node --version
npx --version
```

然后验证你的配置并重启 Hermes。

### 工具未出现

可能的原因：
- 服务器连接失败
- 发现失败
- 你的过滤配置排除了这些工具
- 该服务器不存在该实用能力
- 服务器被 `enabled: false` 禁用

如果你是有意过滤的，这是预期行为。

### 为什么资源或提示实用工具没有出现？

因为 Hermes 现在只在以下两个条件都满足时才注册这些包装器：
1. 你的配置允许它们
2. 服务器会话实际支持该能力

这是有意为之的，保持工具列表的真实性。

## MCP 采样支持

MCP 服务器可以通过 `sampling/createMessage` 协议从 Hermes 请求 LLM 推理。这允许 MCP 服务器请求 Hermes 代表它生成文本 — 对于需要 LLM 能力但没有自己模型访问权限的服务器很有用。

采样对所有 MCP 服务器**默认启用**（当 MCP SDK 支持时）。在 `sampling` 键下按服务器配置：

```yaml
mcp_servers:
  my_server:
    command: "my-mcp-server"
    sampling:
      enabled: true            # 启用采样（默认：true）
      model: "openai/gpt-4o"  # 覆盖采样请求的模型（可选）
      max_tokens_cap: 4096     # 每个采样响应的最大 token 数（默认：4096）
      timeout: 30              # 每个请求的超时秒数（默认：30）
      max_rpm: 10              # 速率限制：每分钟最大请求数（默认：10）
      max_tool_rounds: 5       # 采样循环中的最大工具使用轮数（默认：5）
      allowed_models: []       # 服务器可请求的模型名称白名单（空 = 任意）
      log_level: "info"        # 审计日志级别：debug、info 或 warning（默认：info）
```

采样处理器包括滑动窗口速率限制器、每请求超时和工具循环深度限制，以防止失控使用。指标（请求计数、错误、使用的 token）按服务器实例跟踪。

要为特定服务器禁用采样：

```yaml
mcp_servers:
  untrusted_server:
    url: "https://mcp.example.com"
    sampling:
      enabled: false
```

## 将 Hermes 作为 MCP 服务器运行

除了连接**到** MCP 服务器外，Hermes 还可以**成为** MCP 服务器。这让其他 MCP 兼容的代理（Claude Code、Cursor、Codex 或任何 MCP 客户端）使用 Hermes 的消息能力 — 列出对话、读取消息历史，并跨所有已连接的平台发送消息。

### 何时使用

- 你想让 Claude Code、Cursor 或另一个编码代理通过 Hermes 发送和读取 Telegram/Discord/Slack 消息
- 你想要一个桥接到 Hermes 所有已连接消息平台的单一 MCP 服务器
- 你已经有一个运行中的 Hermes 网关并连接了平台

### 快速入门

```bash
hermes mcp serve
```

这启动一个 stdio MCP 服务器。MCP 客户端（不是你）管理进程生命周期。

### MCP 客户端配置

将 Hermes 添加到你的 MCP 客户端配置。例如，在 Claude Code 的 `~/.claude/claude_desktop_config.json` 中：

```json
{
  "mcpServers": {
    "hermes": {
      "command": "hermes",
      "args": ["mcp", "serve"]
    }
  }
}
```

或者如果你将 Hermes 安装在特定位置：

```json
{
  "mcpServers": {
    "hermes": {
      "command": "/home/user/.hermes/hermes-agent/venv/bin/hermes",
      "args": ["mcp", "serve"]
    }
  }
}
```

### 可用工具

MCP 服务器暴露 10 个工具，匹配 OpenClaw 的频道桥接界面加上 Hermes 特定的频道浏览器：

| 工具 | 描述 |
|------|-------------|
| `conversations_list` | 列出活跃的消息对话。按平台过滤或按名称搜索。 |
| `conversation_get` | 通过会话键获取单个对话的详细信息。 |
| `messages_read` | 读取对话的最近消息历史。 |
| `attachments_fetch` | 从特定消息中提取非文本附件（图片、媒体）。 |
| `events_poll` | 轮询自某个游标位置以来的新对话事件。 |
| `events_wait` | 长轮询/阻塞直到下一个事件到达（近实时）。 |
| `messages_send` | 通过平台发送消息（例如 `telegram:123456`、`discord:#general`）。 |
| `channels_list` | 列出所有平台上可用的消息目标。 |
| `permissions_list_open` | 列出此桥接会话期间观察到的待审批请求。 |
| `permissions_respond` | 允许或拒绝待审批请求。 |

### 事件系统

MCP 服务器包括一个实时事件桥接，轮询 Hermes 的会话数据库以获取新消息。这为 MCP 客户端提供近实时的传入对话感知：

```
# 轮询新事件（非阻塞）
events_poll(after_cursor=0)

# 等待下一个事件（最多阻塞到超时）
events_wait(after_cursor=42, timeout_ms=30000)
```

事件类型：`message`、`approval_requested`、`approval_resolved`

事件队列在内存中，在桥接连接时启动。较旧的消息可通过 `messages_read` 获取。

### 选项

```bash
hermes mcp serve              # 普通模式
hermes mcp serve --verbose    # 在 stderr 上输出调试日志
```

### 工作原理

MCP 服务器直接从 Hermes 的会话存储（`~/.hermes/sessions/sessions.json` 和 SQLite 数据库）读取对话数据。后台线程轮询数据库以获取新消息并维护内存中的事件队列。对于发送消息，它使用与 Hermes 代理本身相同的 `send_message` 基础设施。

网关不需要运行即可进行读取操作（列出对话、读取历史、轮询事件）。它**需要**运行才能进行发送操作，因为平台适配器需要活跃的连接。

### 当前限制

- 仅 Stdio 传输（尚无 HTTP MCP 传输）
- 通过 mtime 优化的数据库轮询以约 200ms 间隔轮询事件（文件未更改时跳过工作）
- 尚无 `claude/channel` 推送通知协议
- 仅文本发送（无法通过 `messages_send` 发送媒体/附件）

## 相关文档

- [将 MCP 与 Hermes 一起使用](/docs/guides/use-mcp-with-hermes)
- [CLI 命令](/docs/reference/cli-commands)
- [斜杠命令](/docs/reference/slash-commands)
- [常见问题](/docs/reference/faq)
