---
sidebar_position: 2
title: "斜杠命令参考"
description: "交互式 CLI 和消息平台斜杠命令的完整参考"
---

# 斜杠命令参考

Hermes 有两个斜杠命令界面，都由 `hermes_cli/commands.py` 中的中央 `COMMAND_REGISTRY` 驱动：

- **交互式 CLI 斜杠命令** — 由 `cli.py` 调度，从注册表获取自动补全
- **消息平台斜杠命令** — 由 `gateway/run.py` 调度，从注册表生成帮助文本和平台菜单

已安装的技能也作为动态斜杠命令暴露在两个界面上。这包括像 `/plan` 这样的内置技能，它会打开计划模式并将 markdown 计划保存到相对于活跃工作区/后端工作目录的 `.hermes/plans/` 下。

## 交互式 CLI 斜杠命令

在 CLI 中输入 `/` 打开自动补全菜单。内置命令不区分大小写。

### 会话

| 命令 | 描述 |
|------|------|
| `/new`（别名：`/reset`） | 开始新会话（全新会话 ID + 历史） |
| `/clear` | 清屏并开始新会话 |
| `/history` | 显示对话历史 |
| `/save` | 保存当前对话 |
| `/retry` | 重试上一条消息（重新发送给代理） |
| `/undo` | 移除最后一组用户/助手交互 |
| `/title` | 为当前会话设置标题（用法：/title My Session Name） |
| `/compress [focus topic]` | 手动压缩对话上下文（刷新记忆 + 摘要）。可选的焦点主题缩小摘要保留的内容。 |
| `/rollback` | 列出或恢复文件系统检查点（用法：/rollback [number]） |
| `/snapshot [create\|restore <id>\|prune]`（别名：`/snap`） | 创建或恢复 Hermes 配置/状态快照。`create [label]` 保存快照，`restore <id>` 恢复到它，`prune [N]` 移除旧快照，或无参数列出所有。 |
| `/stop` | 终止所有运行中的后台进程 |
| `/queue <prompt>`（别名：`/q`） | 将提示排队到下一轮（不中断当前代理响应）。**注意：** `/q` 被 `/queue` 和 `/quit` 同时声明；最后注册的获胜，所以实际上 `/q` 解析为 `/quit`。请明确使用 `/queue`。 |
| `/resume [name]` | 恢复之前命名的会话 |
| `/status` | 显示会话信息 |
| `/snapshot`（别名：`/snap`） | 创建或恢复 Hermes 配置/状态快照（用法：/snapshot [create\|restore \<id\>\|prune]） |
| `/background <prompt>`（别名：`/bg`） | 在独立的后台会话中运行提示。代理独立处理你的提示 — 当前会话可以继续其他工作。任务完成后结果以面板形式出现。参见 [CLI 后台会话](/docs/user-guide/cli#background-sessions)。 |
| `/btw <question>` | 使用会话上下文的临时旁问（无工具，不持久化）。用于快速澄清而不影响对话历史。 |
| `/plan [request]` | 加载内置的 `plan` 技能，编写 markdown 计划而不是执行工作。计划保存到相对于活跃工作区/后端工作目录的 `.hermes/plans/` 下。 |
| `/branch [name]`（别名：`/fork`） | 分支当前会话（探索不同路径） |

### 配置

| 命令 | 描述 |
|------|------|
| `/config` | 显示当前配置 |
| `/model [model-name]` | 显示或更改当前模型。支持：`/model claude-sonnet-4`、`/model provider:model`（切换提供商）、`/model custom:model`（自定义端点）、`/model custom:name:model`（命名自定义提供商）、`/model custom`（从端点自动检测）。使用 `--global` 将更改持久化到 config.yaml。**注意：** `/model` 只能在已配置的提供商之间切换。要添加新提供商，退出会话并从终端运行 `hermes model`。 |
| `/provider` | 显示可用提供商和当前提供商 |
| `/personality` | 设置预定义个性 |
| `/verbose` | 循环工具进度显示：off → new → all → verbose。可通过配置[为消息平台启用](#说明)。 |
| `/fast` | 切换快速模式 — OpenAI Priority Processing / Anthropic Fast Mode（用法：/fast [normal\|fast\|status]） |
| `/reasoning` | 管理推理强度和显示（用法：/reasoning [level\|show\|hide]） |
| `/fast [normal\|fast\|status]` | 切换快速模式 — OpenAI Priority Processing / Anthropic Fast Mode。选项：`normal`、`fast`、`status`、`on`、`off`。 |
| `/skin` | 显示或更改显示皮肤/主题 |
| `/statusbar`（别名：`/sb`） | 切换上下文/模型状态栏的开关 |
| `/voice [on\|off\|tts\|status]` | 切换 CLI 语音模式和语音回放。录音使用 `voice.record_key`（默认：`Ctrl+B`）。 |
| `/yolo` | 切换 YOLO 模式 — 跳过所有危险命令审批提示。 |

### 工具与技能

| 命令 | 描述 |
|------|------|
| `/tools [list\|disable\|enable] [name...]` | 管理工具：列出可用工具，或为当前会话禁用/启用特定工具。禁用工具会将其从代理的工具集中移除并触发会话重置。 |
| `/toolsets` | 列出可用工具集 |
| `/browser [connect\|disconnect\|status]` | 管理本地 Chrome CDP 连接。`connect` 将浏览器工具连接到运行中的 Chrome 实例（默认：`ws://localhost:9222`）。`disconnect` 断开连接。`status` 显示当前连接。如果未检测到调试器，自动启动 Chrome。 |
| `/skills` | 搜索、安装、检查或管理在线注册表中的技能 |
| `/cron` | 管理定时任务（list、add/create、edit、pause、resume、run、remove） |
| `/reload-mcp`（别名：`/reload_mcp`） | 从 config.yaml 重新加载 MCP 服务器 |
| `/reload` | 将 `.env` 变量重新加载到运行中的会话（无需重启即可使用新 API 密钥） |
| `/plugins` | 列出已安装的插件及其状态 |

### 信息

| 命令 | 描述 |
|------|------|
| `/help` | 显示帮助信息 |
| `/usage` | 显示 token 使用量、成本明细和会话时长 |
| `/insights` | 显示使用分析（最近 30 天） |
| `/platforms`（别名：`/gateway`） | 显示网关/消息平台状态 |
| `/paste` | 检查剪贴板中的图片并附加 |
| `/image <path>` | 附加本地图片文件用于下一个提示。 |
| `/debug` | 上传调试报告（系统信息 + 日志）并获取可分享链接。也可在消息平台中使用。 |
| `/profile` | 显示活跃配置文件名称和主目录 |

### 退出

| 命令 | 描述 |
|------|------|
| `/quit` | 退出 CLI（也可用：`/exit`）。参见上方 `/queue` 下关于 `/q` 的说明。 |

### 动态 CLI 斜杠命令

| 命令 | 描述 |
|------|------|
| `/<skill-name>` | 将任何已安装的技能作为按需命令加载。示例：`/gif-search`、`/github-pr-workflow`、`/excalidraw`。 |
| `/skills ...` | 从注册表和官方可选技能目录中搜索、浏览、检查、安装、审计、发布和配置技能。 |

### 快捷命令

用户定义的快捷命令将短别名映射到更长的提示。在 `~/.hermes/config.yaml` 中配置：

```yaml
quick_commands:
  review: "Review my latest git diff and suggest improvements"
  deploy: "Run the deployment script at scripts/deploy.sh and verify the output"
  morning: "Check my calendar, unread emails, and summarize today's priorities"
```

然后在 CLI 中输入 `/review`、`/deploy` 或 `/morning`。快捷命令在调度时解析，不显示在内置自动补全/帮助表中。

### 别名解析

命令支持前缀匹配：输入 `/h` 解析为 `/help`，`/mod` 解析为 `/model`。当前缀不明确（匹配多个命令）时，注册表顺序中的第一个匹配获胜。完整命令名和注册的别名始终优先于前缀匹配。

## 消息平台斜杠命令

消息网关在 Telegram、Discord、Slack、WhatsApp、Signal、Email 和 Home Assistant 聊天中支持以下内置命令：

| 命令 | 描述 |
|------|------|
| `/new` | 开始新对话。 |
| `/reset` | 重置对话历史。 |
| `/status` | 显示会话信息。 |
| `/stop` | 终止所有运行中的后台进程并中断运行中的代理。 |
| `/model [provider:model]` | 显示或更改模型。支持提供商切换（`/model zai:glm-5`）、自定义端点（`/model custom:model`）、命名自定义提供商（`/model custom:local:qwen`）和自动检测（`/model custom`）。使用 `--global` 将更改持久化到 config.yaml。**注意：** `/model` 只能在已配置的提供商之间切换。要添加新提供商或设置 API 密钥，从终端（聊天会话外）使用 `hermes model`。 |
| `/provider` | 显示提供商可用性和认证状态。 |
| `/personality [name]` | 为会话设置个性覆盖。 |
| `/fast [normal\|fast\|status]` | 切换快速模式 — OpenAI Priority Processing / Anthropic Fast Mode。 |
| `/retry` | 重试上一条消息。 |
| `/undo` | 移除最后一次交互。 |
| `/sethome`（别名：`/set-home`） | 将当前聊天标记为平台主频道用于投递。 |
| `/compress [focus topic]` | 手动压缩对话上下文。可选的焦点主题缩小摘要保留的内容。 |
| `/title [name]` | 设置或显示会话标题。 |
| `/resume [name]` | 恢复之前命名的会话。 |
| `/usage` | 显示 token 使用量、估算成本明细（输入/输出）、上下文窗口状态和会话时长。 |
| `/insights [days]` | 显示使用分析。 |
| `/reasoning [level\|show\|hide]` | 更改推理强度或切换推理显示。 |
| `/voice [on\|off\|tts\|join\|channel\|leave\|status]` | 控制聊天中的语音回复。`join`/`channel`/`leave` 管理 Discord 语音频道模式。 |
| `/rollback [number]` | 列出或恢复文件系统检查点。 |
| `/snapshot [create\|restore <id>\|prune]`（别名：`/snap`） | 创建或恢复 Hermes 配置/状态快照。 |
| `/background <prompt>` | 在独立的后台会话中运行提示。任务完成后结果投递回同一聊天。参见[消息平台后台会话](/docs/user-guide/messaging/#background-sessions)。 |
| `/plan [request]` | 加载内置的 `plan` 技能，编写 markdown 计划而不是执行工作。计划保存到相对于活跃工作区/后端工作目录的 `.hermes/plans/` 下。 |
| `/reload-mcp`（别名：`/reload_mcp`） | 从配置重新加载 MCP 服务器。 |
| `/reload` | 将 `.env` 变量重新加载到运行中的会话。 |
| `/yolo` | 切换 YOLO 模式 — 跳过所有危险命令审批提示。 |
| `/commands [page]` | 浏览所有命令和技能（分页）。 |
| `/approve [session\|always]` | 审批并执行待处理的危险命令。`session` 仅为本会话审批；`always` 添加到永久白名单。 |
| `/deny` | 拒绝待处理的危险命令。 |
| `/update` | 将 Hermes Agent 更新到最新版本。 |
| `/restart` | 在排空活跃运行后优雅地重启网关。网关恢复在线后，向请求者的聊天/线程发送确认。 |
| `/fast [normal\|fast\|status]` | 切换快速模式 — OpenAI Priority Processing / Anthropic Fast Mode。 |
| `/debug` | 上传调试报告（系统信息 + 日志）并获取可分享链接。 |
| `/help` | 显示消息平台帮助。 |
| `/<skill-name>` | 按名称调用任何已安装的技能。 |

## 说明

- `/skin`、`/tools`、`/toolsets`、`/browser`、`/config`、`/cron`、`/skills`、`/platforms`、`/paste`、`/image`、`/statusbar` 和 `/plugins` 是 **仅 CLI** 命令。
- `/verbose` **默认仅 CLI**，但可以通过在 `config.yaml` 中设置 `display.tool_progress_command: true` 为消息平台启用。启用后，它循环 `display.tool_progress` 模式并保存到配置。
- `/sethome`、`/update`、`/restart`、`/approve`、`/deny` 和 `/commands` 是**仅消息平台**命令。
- `/status`、`/background`、`/voice`、`/reload-mcp`、`/rollback`、`/snapshot`、`/debug`、`/fast` 和 `/yolo` 在 CLI 和消息网关中**都可用**。
- `/voice join`、`/voice channel` 和 `/voice leave` 仅在 Discord 上有意义。
