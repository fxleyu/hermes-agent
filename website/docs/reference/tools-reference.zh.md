---
sidebar_position: 3
title: "内置工具参考"
description: "Hermes 内置工具的权威参考，按工具集分组"
---

# 内置工具参考

本页记录了 Hermes 工具注册表中所有 47 个内置工具，按工具集分组。可用性因平台、凭证和启用的工具集而异。

**快速统计：** 10 个浏览器工具、4 个文件工具、10 个 RL 工具、4 个 Home Assistant 工具、2 个终端工具、2 个 Web 工具，以及 15 个跨其他工具集的独立工具。

:::tip MCP 工具
除了内置工具外，Hermes 还可以从 MCP 服务器动态加载工具。MCP 工具以服务器名称为前缀出现（例如，`github` MCP 服务器的 `github_create_issue`）。配置请参见 [MCP 集成](/docs/user-guide/features/mcp)。
:::

## `browser` 工具集

| 工具 | 描述 | 需要的环境 |
|------|------|-----------|
| `browser_back` | 在浏览器历史中导航到上一页。需要先调用 browser_navigate。 | — |
| `browser_click` | 点击快照中由 ref ID 标识的元素（例如 '@e5'）。ref ID 在快照输出中以方括号显示。需要先调用 browser_navigate 和 browser_snapshot。 | — |
| `browser_console` | 获取当前页面的浏览器控制台输出和 JavaScript 错误。返回 console.log/warn/error/info 消息和未捕获的 JS 异常。用于检测静默 JavaScript 错误、失败的 API 调用和应用警告。需要先调用 browser_navigate。 | — |
| `browser_get_images` | 获取当前页面上所有图片的列表及其 URL 和 alt 文本。用于查找要通过 vision 工具分析的图片。需要先调用 browser_navigate。 | — |
| `browser_navigate` | 在浏览器中导航到 URL。初始化会话并加载页面。必须在其他浏览器工具之前调用。对于简单的信息检索，优先使用 web_search 或 web_extract（更快、更便宜）。当需要交互时使用浏览器工具。 | — |
| `browser_press` | 按下键盘键。用于提交表单（Enter）、导航（Tab）或键盘快捷键。需要先调用 browser_navigate。 | — |
| `browser_scroll` | 向某个方向滚动页面。用于显示当前视口上方或下方的更多内容。需要先调用 browser_navigate。 | — |
| `browser_snapshot` | 获取当前页面可访问性树的文本快照。返回带有 ref ID（如 @e1、@e2）的交互元素，用于 browser_click 和 browser_type。full=false（默认）：仅交互元素的紧凑视图。full=true：包含所有内容的完整视图。 | — |
| `browser_type` | 在由 ref ID 标识的输入字段中输入文本。先清除字段，然后输入新文本。需要先调用 browser_navigate 和 browser_snapshot。 | — |
| `browser_vision` | 对当前页面截图并使用视觉 AI 分析。当需要视觉理解页面内容时使用 — 特别适用于验证码、视觉验证挑战、复杂布局，或当文本快照不够时。 | — |

## `clarify` 工具集

| 工具 | 描述 | 需要的环境 |
|------|------|-----------|
| `clarify` | 当需要澄清、反馈或在继续前做决定时向用户提问。支持两种模式：1. **多选** — 提供最多 4 个选项。2. **开放式** — 自由文本输入。 | — |

## `code_execution` 工具集

| 工具 | 描述 | 需要的环境 |
|------|------|-----------|
| `execute_code` | 运行可以编程调用 Hermes 工具的 Python 脚本。当需要 3+ 个工具调用且中间有处理逻辑、需要过滤/减少大型工具输出、需要条件分支等情况时使用。 | — |

## `cronjob` 工具集

| 工具 | 描述 | 需要的环境 |
|------|------|-----------|
| `cronjob` | 统一的定时任务管理器。使用 `action="create"`、`"list"`、`"update"`、`"pause"`、`"resume"`、`"run"` 或 `"remove"` 管理任务。支持附带技能的任务，`skills=[]` 在更新时清除附带的技能。Cron 运行在全新会话中进行，没有当前聊天上下文。 | — |

## `delegation` 工具集

| 工具 | 描述 | 需要的环境 |
|------|------|-----------|
| `delegate_task` | 生成一个或多个子代理在隔离上下文中工作。每个子代理有自己的对话、终端会话和工具集。只有最终摘要返回 — 中间工具结果永远不会进入你的上下文窗口。 | — |

## `file` 工具集

| 工具 | 描述 | 需要的环境 |
|------|------|-----------|
| `patch` | 文件中的目标查找和替换编辑。使用此工具而不是终端中的 sed/awk。使用模糊匹配（9 种策略），因此轻微的空白/缩进差异不会破坏它。返回统一 diff。编辑后自动运行语法检查。 | — |
| `read_file` | 读取带行号和分页的文本文件。使用此工具而不是终端中的 cat/head/tail。输出格式：'LINE_NUM\|CONTENT'。如果未找到会建议相似文件名。对大文件使用 offset 和 limit。注意：不能读取图片或二进制文件。 | — |
| `search_files` | 搜索文件内容或按名称查找文件。使用此工具而不是终端中的 grep/rg/find/ls。基于 Ripgrep，比 shell 等效项更快。内容搜索（target='content'）：文件内的正则搜索。 | — |
| `write_file` | 将内容写入文件，完全替换现有内容。使用此工具而不是终端中的 echo/cat heredoc。自动创建父目录。覆盖整个文件 — 使用 'patch' 进行目标编辑。 | — |

## `homeassistant` 工具集

| 工具 | 描述 | 需要的环境 |
|------|------|-----------|
| `ha_call_service` | 调用 Home Assistant 服务来控制设备。使用 ha_list_services 发现每个域的可用服务及其参数。 | — |
| `ha_get_state` | 获取单个 Home Assistant 实体的详细状态，包括所有属性（亮度、颜色、温度设定点、传感器读数等）。 | — |
| `ha_list_entities` | 列出 Home Assistant 实体。可选按域（light、switch、climate、sensor、binary_sensor、cover、fan 等）或按区域名称（客厅、厨房、卧室等）过滤。 | — |
| `ha_list_services` | 列出可用的 Home Assistant 服务（操作）用于设备控制。显示每种设备类型可以执行什么操作及其接受的参数。使用此工具发现如何控制通过 ha_list_entities 找到的设备。 | — |

:::note
**Honcho 工具**（`honcho_profile`、`honcho_search`、`honcho_context`、`honcho_reasoning`、`honcho_conclude`）不再是内置工具。它们通过 `plugins/memory/honcho/` 的 Honcho 记忆提供商插件提供。参见[记忆提供商](../user-guide/features/memory-providers.md)了解安装和使用。
:::

## `image_gen` 工具集

| 工具 | 描述 | 需要的环境 |
|------|------|-----------|
| `image_generate` | 使用 FAL.ai 从文本提示生成高质量图片。底层模型由用户配置（默认：FLUX 2 Klein 9B，亚秒级生成），代理不可选择。返回单个图片 URL。 | FAL_KEY |

## `memory` 工具集

| 工具 | 描述 | 需要的环境 |
|------|------|-----------|
| `memory` | 将重要信息保存到跨会话持久的记忆中。你的记忆在会话开始时出现在系统提示中 — 这是你在对话间记住关于用户和环境的事物的方式。 | — |

## `messaging` 工具集

| 工具 | 描述 | 需要的环境 |
|------|------|-----------|
| `send_message` | 向已连接的消息平台发送消息，或列出可用目标。重要：当用户要求发送到特定频道或个人（不仅仅是裸平台名称）时，先调用 send_message(action='list') 查看可用目标。 | — |

## `moa` 工具集

| 工具 | 描述 | 需要的环境 |
|------|------|-----------|
| `mixture_of_agents` | 将困难问题通过多个前沿 LLM 协作路由。进行 5 次 API 调用（4 个参考模型 + 1 个聚合器）并使用最大推理强度 — 谨慎使用，仅用于真正困难的问题。 | OPENROUTER_API_KEY |

## `rl` 工具集

| 工具 | 描述 | 需要的环境 |
|------|------|-----------|
| `rl_check_status` | 获取训练运行的状态和指标。有速率限制：同一运行最少间隔 30 分钟。返回 WandB 指标：step、state、reward_mean、loss、percent_correct。 | TINKER_API_KEY、WANDB_API_KEY |
| `rl_edit_config` | 更新配置字段。先使用 rl_get_current_config() 查看所选环境的所有可用字段。 | TINKER_API_KEY、WANDB_API_KEY |
| `rl_get_current_config` | 获取当前环境配置。仅返回可修改的字段：group_size、max_token_length、total_steps、steps_per_eval、use_wandb、wandb_name、max_num_workers。 | TINKER_API_KEY、WANDB_API_KEY |
| `rl_get_results` | 获取已完成训练运行的最终结果和指标。返回最终指标和训练权重路径。 | TINKER_API_KEY、WANDB_API_KEY |
| `rl_list_environments` | 列出所有可用的 RL 环境。返回环境名称、路径和描述。 | TINKER_API_KEY、WANDB_API_KEY |
| `rl_list_runs` | 列出所有训练运行（活跃和已完成）及其状态。 | TINKER_API_KEY、WANDB_API_KEY |
| `rl_select_environment` | 选择用于训练的 RL 环境。加载环境的默认配置。 | TINKER_API_KEY、WANDB_API_KEY |
| `rl_start_training` | 使用当前环境和配置开始新的 RL 训练运行。 | TINKER_API_KEY、WANDB_API_KEY |
| `rl_stop_training` | 停止运行中的训练任务。 | TINKER_API_KEY、WANDB_API_KEY |
| `rl_test_inference` | 任何环境的快速推理测试。运行几步推理 + 评分。 | TINKER_API_KEY、WANDB_API_KEY |

## `session_search` 工具集

| 工具 | 描述 | 需要的环境 |
|------|------|-----------|
| `session_search` | 搜索过去对话的长期记忆。这是你的回忆 — 每个过去的会话都可搜索，此工具总结了发生的事情。 | — |

## `skills` 工具集

| 工具 | 描述 | 需要的环境 |
|------|------|-----------|
| `skill_manage` | 管理技能（创建、更新、删除）。技能是你的程序性记忆 — 用于重复任务类型的可重用方法。新技能进入 ~/.hermes/skills/；现有技能可以在其所在位置修改。 | — |
| `skill_view` | 加载技能的完整内容或访问其链接文件（参考、模板、脚本）。 | — |
| `skills_list` | 列出可用技能（名称 + 描述）。使用 skill_view(name) 加载完整内容。 | — |

## `terminal` 工具集

| 工具 | 描述 | 需要的环境 |
|------|------|-----------|
| `process` | 管理用 terminal(background=true) 启动的后台进程。操作：'list'（显示所有）、'poll'（检查状态 + 新输出）、'log'（带分页的完整输出）、'wait'（阻塞直到完成或超时）、'kill'（终止）、'write'（发送输入）。 | — |
| `terminal` | 在 Linux 环境上执行 shell 命令。文件系统在调用间持久化。设置 `background=true` 用于长时间运行的服务器。设置 `notify_on_complete=true`（与 `background=true` 一起）在进程完成时获得自动通知 — 无需轮询。不要使用 cat/head/tail — 使用 read_file。不要使用 grep/rg/find — 使用 search_files。 | — |

## `todo` 工具集

| 工具 | 描述 | 需要的环境 |
|------|------|-----------|
| `todo` | 管理当前会话的任务列表。用于包含 3+ 步骤的复杂任务或用户提供多个任务时。 | — |

## `vision` 工具集

| 工具 | 描述 | 需要的环境 |
|------|------|-----------|
| `vision_analyze` | 使用 AI 视觉分析图片。提供全面的描述并回答关于图片内容的特定问题。 | — |

## `web` 工具集

| 工具 | 描述 | 需要的环境 |
|------|------|-----------|
| `web_search` | 搜索任何主题的网络信息。返回最多 5 个相关结果，包含标题、URL 和描述。 | EXA_API_KEY 或 PARALLEL_API_KEY 或 FIRECRAWL_API_KEY 或 TAVILY_API_KEY |
| `web_extract` | 从网页 URL 提取内容。返回 markdown 格式的页面内容。也适用于 PDF URL — 直接传递 PDF 链接，它会转换为 markdown 文本。5000 字符以下的页面返回完整 markdown；较大的页面经过 LLM 摘要。 | EXA_API_KEY 或 PARALLEL_API_KEY 或 FIRECRAWL_API_KEY 或 TAVILY_API_KEY |

## `tts` 工具集

| 工具 | 描述 | 需要的环境 |
|------|------|-----------|
| `text_to_speech` | 将文本转换为语音音频。返回平台作为语音消息投递的 MEDIA: 路径。在 Telegram 上作为语音气泡播放，在 Discord/WhatsApp 上作为音频附件。在 CLI 模式下保存到 ~/voice-memos/。 | — |
