---
title: 浏览器自动化
description: 使用多种提供商控制浏览器，通过 CDP 连接本地 Chrome，或使用云浏览器进行网页交互、表单填写、抓取等。
sidebar_label: 浏览器
sidebar_position: 5
---

# 浏览器自动化

Hermes Agent 包含完整的浏览器自动化工具集，支持多种后端选项：

- **Browserbase 云模式**，通过 [Browserbase](https://browserbase.com) 提供托管云浏览器和反机器人工具
- **Browser Use 云模式**，通过 [Browser Use](https://browser-use.com) 作为替代云浏览器提供商
- **Firecrawl 云模式**，通过 [Firecrawl](https://firecrawl.dev) 提供内置抓取功能的云浏览器
- **Camofox 本地模式**，通过 [Camofox](https://github.com/jo-inc/camofox-browser) 进行本地反检测浏览（基于 Firefox 的指纹伪装）
- **通过 CDP 连接本地 Chrome** ——使用 `/browser connect` 将浏览器工具连接到你自己的 Chrome 实例
- **本地浏览器模式**，通过 `agent-browser` CLI 和本地 Chromium 安装

在所有模式下，代理都可以浏览网站、与页面元素交互、填写表单和提取信息。

## 概述

页面以**无障碍树**（基于文本的快照）表示，非常适合 LLM 代理。交互元素获得引用 ID（如 `@e1`、`@e2`），代理使用这些 ID 进行点击和输入。

关键功能：

- **多提供商云执行** ——Browserbase、Browser Use 或 Firecrawl——无需本地浏览器
- **本地 Chrome 集成** ——通过 CDP 连接到你正在运行的 Chrome 进行实时浏览
- **内置隐身** ——随机指纹、CAPTCHA 解决、住宅代理（Browserbase）
- **会话隔离** ——每个任务获得自己的浏览器会话
- **自动清理** ——不活跃的会话在超时后关闭
- **视觉分析** ——截图 + AI 分析用于视觉理解

## 设置

:::tip Nous 订阅用户
如果你有付费的 [Nous Portal](https://portal.nousresearch.com) 订阅，你可以通过 **[工具网关](tool-gateway.md)** 使用浏览器自动化，无需单独的 API 密钥。运行 `hermes model` 或 `hermes tools` 来启用。
:::

### Browserbase 云模式

要使用 Browserbase 托管的云浏览器，添加：

```bash
# 添加到 ~/.hermes/.env
BROWSERBASE_API_KEY=***
BROWSERBASE_PROJECT_ID=your-project-id-here
```

在 [browserbase.com](https://browserbase.com) 获取你的凭证。

### Browser Use 云模式

要使用 Browser Use 作为云浏览器提供商，添加：

```bash
# 添加到 ~/.hermes/.env
BROWSER_USE_API_KEY=***
```

在 [browser-use.com](https://browser-use.com) 获取你的 API 密钥。Browser Use 通过其 REST API 提供云浏览器。如果同时设置了 Browserbase 和 Browser Use 凭证，Browserbase 优先。

### Firecrawl 云模式

要使用 Firecrawl 作为云浏览器提供商，添加：

```bash
# 添加到 ~/.hermes/.env
FIRECRAWL_API_KEY=fc-***
```

在 [firecrawl.dev](https://firecrawl.dev) 获取你的 API 密钥。然后选择 Firecrawl 作为浏览器提供商：

```bash
hermes setup tools
# → Browser Automation → Firecrawl
```

可选设置：

```bash
# 自托管 Firecrawl 实例（默认：https://api.firecrawl.dev）
FIRECRAWL_API_URL=http://localhost:3002

# 会话 TTL（秒）（默认：300）
FIRECRAWL_BROWSER_TTL=600
```

### Camofox 本地模式

[Camofox](https://github.com/jo-inc/camofox-browser) 是一个自托管的 Node.js 服务器，封装了 Camoufox（一个带有 C++ 指纹伪装的 Firefox 分支）。它提供无云依赖的本地反检测浏览。

```bash
# 安装并运行
git clone https://github.com/jo-inc/camofox-browser && cd camofox-browser
npm install && npm start   # 首次运行时下载 Camoufox（约 300MB）

# 或通过 Docker
docker run -d --network host -e CAMOFOX_PORT=9377 jo-inc/camofox-browser
```

然后在 `~/.hermes/.env` 中设置：

```bash
CAMOFOX_URL=http://localhost:9377
```

或通过 `hermes tools` -> 浏览器自动化 -> Camofox 配置。

当设置了 `CAMOFOX_URL` 时，所有浏览器工具自动通过 Camofox 路由，而不是 Browserbase 或 agent-browser。

#### 持久浏览器会话

默认情况下，每个 Camofox 会话获得随机身份——Cookie 和登录信息在代理重启后不会保留。要启用持久浏览器会话：

```yaml
# 在 ~/.hermes/config.yaml 中
browser:
  camofox:
    managed_persistence: true
```

启用后，Hermes 向 Camofox 发送稳定的配置文件范围 `userId`。Camofox 服务器自动将每个 `userId` 映射到专用的持久 Firefox 配置文件，因此 Cookie、登录信息和 localStorage 在重启后保留。不同的 Hermes 配置文件获得不同的浏览器配置文件（配置文件隔离）。

#### VNC 实时查看

当 Camofox 在有头模式（带有可见浏览器窗口）运行时，它在健康检查响应中暴露 VNC 端口。Hermes 自动发现并在导航响应中包含 VNC URL，这样代理可以分享链接让你实时观看浏览器。

### 通过 CDP 连接本地 Chrome（`/browser connect`）

你可以通过 Chrome DevTools Protocol（CDP）将 Hermes 浏览器工具连接到你自己正在运行的 Chrome 实例，而不是使用云提供商。当你想实时查看代理在做什么、与需要你自己 Cookie/会话的页面交互、或避免云浏览器成本时，这很有用。

在 CLI 中使用：

```
/browser connect              # 连接到 ws://localhost:9222 的 Chrome
/browser connect ws://host:port  # 连接到特定 CDP 端点
/browser status               # 检查当前连接状态
/browser disconnect            # 断开并返回云/本地模式
```

如果 Chrome 尚未启用远程调试运行，Hermes 将尝试使用 `--remote-debugging-port=9222` 自动启动它。

:::tip
手动启动带有 CDP 的 Chrome：
```bash
# Linux
google-chrome --remote-debugging-port=9222

# macOS
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --remote-debugging-port=9222
```
:::

通过 CDP 连接时，所有浏览器工具（`browser_navigate`、`browser_click` 等）操作你的实时 Chrome 实例，而不是启动云会话。

### 本地浏览器模式

如果你**没有**设置任何云凭证且未使用 `/browser connect`，Hermes 仍然可以通过 `agent-browser` 驱动的本地 Chromium 安装使用浏览器工具。

### 可选环境变量

```bash
# 住宅代理以提高 CAPTCHA 解决率（默认："true"）
BROWSERBASE_PROXIES=true

# 使用自定义 Chromium 的高级隐身——需要 Scale 计划（默认："false"）
BROWSERBASE_ADVANCED_STEALTH=false

# 断开后会话重连——需要付费计划（默认："true"）
BROWSERBASE_KEEP_ALIVE=true

# 自定义会话超时（毫秒）（默认：项目默认值）
# 示例：600000（10分钟）、1800000（30分钟）
BROWSERBASE_SESSION_TIMEOUT=600000

# 自动清理前的不活跃超时（秒）（默认：120）
BROWSER_INACTIVITY_TIMEOUT=120
```

### 安装 agent-browser CLI

```bash
npm install -g agent-browser
# 或在仓库中本地安装：
npm install
```

:::info
`browser` 工具集必须包含在你的配置的 `toolsets` 列表中，或通过 `hermes config set toolsets '["hermes-cli", "browser"]'` 启用。
:::

## 可用工具

### `browser_navigate`

导航到 URL。必须在使用其他浏览器工具之前调用。初始化 Browserbase 会话。

```
Navigate to https://github.com/NousResearch
```

:::tip
对于简单的信息检索，优先使用 `web_search` 或 `web_extract`——它们更快更便宜。当你需要**与页面交互**（点击按钮、填写表单、处理动态内容）时使用浏览器工具。
:::

### `browser_snapshot`

获取当前页面无障碍树的文本快照。返回带有引用 ID（如 `@e1`、`@e2`）的交互元素，用于 `browser_click` 和 `browser_type`。

- **`full=false`**（默认）：精简视图，仅显示交互元素
- **`full=true`**：完整页面内容

超过 8000 个字符的快照会自动由 LLM 进行总结。

### `browser_click`

点击由快照中引用 ID 标识的元素。

```
Click @e5 to press the "Sign In" button
```

### `browser_type`

在输入字段中输入文本。先清除字段，然后输入新文本。

```
Type "hermes agent" into the search field @e3
```

### `browser_scroll`

上下滚动页面以显示更多内容。

```
Scroll down to see more results
```

### `browser_press`

按下键盘按键。用于提交表单或导航。

```
Press Enter to submit the form
```

支持的按键：`Enter`、`Tab`、`Escape`、`ArrowDown`、`ArrowUp` 等。

### `browser_back`

导航回浏览器历史中的上一页。

### `browser_get_images`

列出当前页面上所有图片的 URL 和 alt 文本。用于查找要分析的图片。

### `browser_vision`

截图并使用视觉 AI 分析。当文本快照无法捕获重要的视觉信息时使用——特别适用于 CAPTCHA、复杂布局或视觉验证挑战。

截图被持久保存，文件路径与 AI 分析结果一起返回。在消息平台（Telegram、Discord、Slack、WhatsApp）上，你可以要求代理分享截图——它将通过 `MEDIA:` 机制作为原生图片附件发送。

```
What does the chart on this page show?
```

截图存储在 `~/.hermes/cache/screenshots/` 中，24 小时后自动清理。

### `browser_console`

获取当前页面的浏览器控制台输出（log/warn/error 消息）和未捕获的 JavaScript 异常。对于检测不在无障碍树中显示的静默 JS 错误至关重要。

```
Check the browser console for any JavaScript errors
```

使用 `clear=True` 在读取后清除控制台，这样后续调用只显示新消息。

## 实际示例

### 填写网页表单

```
User: Sign up for an account on example.com with my email john@example.com

Agent workflow:
1. browser_navigate("https://example.com/signup")
2. browser_snapshot()  → sees form fields with refs
3. browser_type(ref="@e3", text="john@example.com")
4. browser_type(ref="@e5", text="SecurePass123")
5. browser_click(ref="@e8")  → clicks "Create Account"
6. browser_snapshot()  → confirms success
```

### 研究动态内容

```
User: What are the top trending repos on GitHub right now?

Agent workflow:
1. browser_navigate("https://github.com/trending")
2. browser_snapshot(full=true)  → reads trending repo list
3. Returns formatted results
```

## 会话录制

自动将浏览器会话录制为 WebM 视频文件：

```yaml
browser:
  record_sessions: true  # 默认：false
```

启用后，录制在第一个 `browser_navigate` 时自动开始，会话关闭时保存到 `~/.hermes/browser_recordings/`。在本地和云（Browserbase）模式下均可工作。超过 72 小时的录制会自动清理。

## 隐身功能

Browserbase 提供自动隐身功能：

| 功能 | 默认值 | 说明 |
|------|--------|------|
| 基本隐身 | 始终开启 | 随机指纹、视口随机化、CAPTCHA 解决 |
| 住宅代理 | 开启 | 通过住宅 IP 路由以获得更好的访问 |
| 高级隐身 | 关闭 | 自定义 Chromium 构建，需要 Scale 计划 |
| 保持活跃 | 开启 | 网络中断后会话重连 |

:::note
如果付费功能在你的计划中不可用，Hermes 会自动回退——首先禁用 `keepAlive`，然后禁用代理——因此浏览在免费计划中仍然可以工作。
:::

## 会话管理

- 每个任务通过 Browserbase 获得隔离的浏览器会话
- 不活跃后会话自动清理（默认：2 分钟）
- 后台线程每 30 秒检查过期会话
- 进程退出时执行紧急清理以防止孤立会话
- 会话通过 Browserbase API 释放（`REQUEST_RELEASE` 状态）

## 限制

- **基于文本的交互** ——依赖无障碍树，而非像素坐标
- **快照大小** ——大型页面可能在 8000 个字符时被截断或由 LLM 总结
- **会话超时** ——云会话根据你的提供商计划设置过期
- **成本** ——云会话消耗提供商积分；当对话结束或不活跃后会话自动清理。使用 `/browser connect` 进行免费的本地浏览。
- **不支持文件下载** ——无法从浏览器下载文件
