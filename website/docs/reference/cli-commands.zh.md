---
sidebar_position: 1
title: "CLI 命令参考"
description: "Hermes 终端命令及命令族的权威参考"
---

# CLI 命令参考

本页涵盖了你在 shell 中运行的**终端命令**。

有关聊天内的斜杠命令，请参阅[斜杠命令参考](./slash-commands.md)。

## 全局入口

```bash
hermes [全局选项] <命令> [子命令/选项]
```

### 全局选项

| 选项 | 描述 |
|--------|-------------|
| `--version`, `-V` | 显示版本并退出。 |
| `--profile <name>`, `-p <name>` | 选择本次调用使用的 Hermes 配置文件。覆盖 `hermes profile use` 设置的默认值。 |
| `--resume <session>`, `-r <session>` | 通过 ID 或标题恢复先前的会话。 |
| `--continue [name]`, `-c [name]` | 恢复最近的会话，或恢复与标题匹配的最近会话。 |
| `--worktree`, `-w` | 在隔离的 git worktree 中启动，用于并行代理工作流。 |
| `--yolo` | 跳过危险命令的确认提示。 |
| `--pass-session-id` | 在代理的系统提示中包含会话 ID。 |

## 顶级命令

| 命令 | 用途 |
|---------|---------|
| `hermes chat` | 与代理进行交互式或一次性聊天。 |
| `hermes model` | 交互式选择默认提供商和模型。 |
| `hermes gateway` | 运行或管理消息网关服务。 |
| `hermes setup` | 全部或部分配置的交互式设置向导。 |
| `hermes whatsapp` | 配置和配对 WhatsApp 桥接。 |
| `hermes auth` | 管理凭据——添加、列出、移除、重置、设置策略。处理 Codex/Nous/Anthropic 的 OAuth 流程。 |
| `hermes login` / `logout` | **已弃用**——请改用 `hermes auth`。 |
| `hermes status` | 显示代理、认证和平台状态。 |
| `hermes cron` | 检查和触发定时调度器。 |
| `hermes webhook` | 管理动态 webhook 订阅，用于事件驱动激活。 |
| `hermes doctor` | 诊断配置和依赖问题。 |
| `hermes dump` | 可复制粘贴的设置摘要，用于支持/调试。 |
| `hermes debug` | 调试工具——上传日志和系统信息以获取支持。 |
| `hermes backup` | 将 Hermes 主目录备份为 zip 文件。 |
| `hermes import` | 从 zip 文件恢复 Hermes 备份。 |
| `hermes logs` | 查看、跟踪和过滤代理/网关/错误日志文件。 |
| `hermes config` | 显示、编辑、迁移和查询配置文件。 |
| `hermes pairing` | 批准或撤销消息配对码。 |
| `hermes skills` | 浏览、安装、发布、审计和配置技能。 |
| `hermes honcho` | 管理 Honcho 跨会话记忆集成。 |
| `hermes memory` | 配置外部记忆提供商。 |
| `hermes acp` | 将 Hermes 作为 ACP 服务器运行，用于编辑器集成。 |
| `hermes mcp` | 管理 MCP 服务器配置并将 Hermes 作为 MCP 服务器运行。 |
| `hermes plugins` | 管理 Hermes Agent 插件（安装、启用、禁用、移除）。 |
| `hermes tools` | 按平台配置启用的工具。 |
| `hermes sessions` | 浏览、导出、清理、重命名和删除会话。 |
| `hermes insights` | 显示 token/费用/活动分析。 |
| `hermes claw` | OpenClaw 迁移辅助工具。 |
| `hermes dashboard` | 启动 Web 仪表板，用于管理配置、API 密钥和会话。 |
| `hermes debug` | 调试工具——上传日志和系统信息以获取支持。 |
| `hermes backup` | 将 Hermes 主目录备份为 zip 文件。 |
| `hermes import` | 从 zip 文件恢复 Hermes 备份。 |
| `hermes profile` | 管理配置文件——多个隔离的 Hermes 实例。 |
| `hermes completion` | 打印 shell 补全脚本（bash/zsh）。 |
| `hermes version` | 显示版本信息。 |
| `hermes update` | 拉取最新代码并重新安装依赖。 |
| `hermes uninstall` | 从系统中移除 Hermes。 |

## `hermes chat`

```bash
hermes chat [选项]
```

常用选项：

| 选项 | 描述 |
|--------|-------------|
| `-q`, `--query "..."` | 一次性、非交互式提示。 |
| `-m`, `--model <model>` | 本次运行覆盖模型。 |
| `-t`, `--toolsets <csv>` | 启用以逗号分隔的工具集。 |
| `--provider <provider>` | 强制使用提供商：`auto`、`openrouter`、`nous`、`openai-codex`、`copilot-acp`、`copilot`、`anthropic`、`gemini`、`huggingface`、`zai`、`kimi-coding`、`minimax`、`minimax-cn`、`kilocode`、`xiaomi`、`arcee`。 |
| `-s`, `--skills <name>` | 为会话预加载一个或多个技能（可重复或逗号分隔）。 |
| `-v`, `--verbose` | 详细输出。 |
| `-Q`, `--quiet` | 编程模式：抑制横幅/加载动画/工具预览。 |
| `--image <path>` | 为单次查询附加本地图片。 |
| `--resume <session>` / `--continue [name]` | 直接从 `chat` 恢复会话。 |
| `--worktree` | 为本次运行创建隔离的 git worktree。 |
| `--checkpoints` | 在破坏性文件更改前启用文件系统检查点。 |
| `--yolo` | 跳过确认提示。 |
| `--pass-session-id` | 将会话 ID 传入系统提示。 |
| `--source <tag>` | 会话来源标签，用于过滤（默认：`cli`）。第三方集成使用 `tool`，这些集成不会出现在用户会话列表中。 |
| `--max-turns <N>` | 每次对话轮次的最大工具调用迭代次数（默认：90，或配置中的 `agent.max_turns`）。 |

示例：

```bash
hermes
hermes chat -q "Summarize the latest PRs"
hermes chat --provider openrouter --model anthropic/claude-sonnet-4.6
hermes chat --toolsets web,terminal,skills
hermes chat --quiet -q "Return only JSON"
hermes chat --worktree -q "Review this repo and open a PR"
```

## `hermes model`

交互式提供商 + 模型选择器。**这是用于添加新提供商、设置 API 密钥和运行 OAuth 流程的命令。** 请从终端运行——而不是在活跃的 Hermes 聊天会话中运行。

```bash
hermes model
```

适用场景：
- **添加新提供商**（OpenRouter、Anthropic、Copilot、DeepSeek、自定义等）
- 登录 OAuth 支持的提供商（Anthropic、Copilot、Codex、Nous Portal）
- 输入或更新 API 密钥
- 从提供商特定的模型列表中选择
- 配置自定义/自托管端点
- 将新默认值保存到配置中

:::warning hermes model 与 /model——了解区别
**`hermes model`**（从终端运行，在任何 Hermes 会话之外）是**完整的提供商设置向导**。它可以添加新提供商、运行 OAuth 流程、提示输入 API 密钥和配置端点。

**`/model`**（在活跃的 Hermes 聊天会话中输入）只能**在已设置的提供商和模型之间切换**。它无法添加新提供商、运行 OAuth 或提示输入 API 密钥。

**如果需要添加新提供商：** 请先退出 Hermes 会话（`Ctrl+C` 或 `/quit`），然后在终端提示符下运行 `hermes model`。
:::

### `/model` 斜杠命令（会话中使用）

在不离开会话的情况下切换已配置的模型：

```
/model                              # 显示当前模型和可用选项
/model claude-sonnet-4              # 切换模型（自动检测提供商）
/model zai:glm-5                    # 切换提供商和模型
/model custom:qwen-2.5              # 使用自定义端点上的模型
/model custom                       # 从自定义端点自动检测模型
/model custom:local:qwen-2.5        # 使用命名的自定义提供商
/model openrouter:anthropic/claude-sonnet-4  # 切换回云端
```

默认情况下，`/model` 的更改**仅适用于当前会话**。添加 `--global` 将更改持久化到 `config.yaml`：

```
/model claude-sonnet-4 --global     # 切换并保存为新默认值
```

:::info 如果只看到 OpenRouter 模型怎么办？
如果你只配置了 OpenRouter，`/model` 将只显示 OpenRouter 模型。要添加其他提供商（Anthropic、DeepSeek、Copilot 等），请退出会话并从终端运行 `hermes model`。
:::

提供商和 base URL 的更改会自动持久化到 `config.yaml`。从自定义端点切换到其他提供商时，过期的 base URL 会被清除，以防止泄露到其他提供商。

## `hermes gateway`

```bash
hermes gateway <子命令>
```

子命令：

| 子命令 | 描述 |
|------------|-------------|
| `run` | 在前台运行网关。推荐用于 WSL、Docker 和 Termux。 |
| `start` | 启动已安装的 systemd/launchd 后台服务。 |
| `stop` | 停止服务（或前台进程）。 |
| `restart` | 重启服务。 |
| `status` | 显示服务状态。 |
| `install` | 安装为 systemd（Linux）或 launchd（macOS）后台服务。 |
| `uninstall` | 移除已安装的服务。 |
| `setup` | 交互式消息平台设置。 |

:::tip WSL 用户
使用 `hermes gateway run` 而不是 `hermes gateway start`——WSL 的 systemd 支持不稳定。使用 tmux 保持持久化：`tmux new -s hermes 'hermes gateway run'`。详见 [WSL 常见问题](/docs/reference/faq#wsl-gateway-keeps-disconnecting-or-hermes-gateway-start-fails)。
:::

## `hermes setup`

```bash
hermes setup [model|tts|terminal|gateway|tools|agent] [--non-interactive] [--reset]
```

使用完整向导或跳转到某个部分：

| 部分 | 描述 |
|---------|-------------|
| `model` | 提供商和模型设置。 |
| `terminal` | 终端后端和沙箱设置。 |
| `gateway` | 消息平台设置。 |
| `tools` | 按平台启用/禁用工具。 |
| `agent` | 代理行为设置。 |

选项：

| 选项 | 描述 |
|--------|-------------|
| `--non-interactive` | 使用默认值/环境变量，无需提示。 |
| `--reset` | 设置前将配置重置为默认值。 |

## `hermes whatsapp`

```bash
hermes whatsapp
```

运行 WhatsApp 配对/设置流程，包括模式选择和二维码配对。

## `hermes login` / `hermes logout` *（已弃用）*

:::caution
`hermes login` 已被移除。请使用 `hermes auth` 管理 OAuth 凭据，使用 `hermes model` 选择提供商，或使用 `hermes setup` 进行完整的交互式设置。
:::

## `hermes auth`

管理同一提供商的凭据池，用于密钥轮换。完整文档请参阅[凭据池](/docs/user-guide/features/credential-pools)。

```bash
hermes auth                                              # 交互式向导
hermes auth list                                         # 显示所有池
hermes auth list openrouter                              # 显示特定提供商
hermes auth add openrouter --api-key sk-or-v1-xxx        # 添加 API 密钥
hermes auth add anthropic --type oauth                   # 添加 OAuth 凭据
hermes auth remove openrouter 2                          # 按索引移除
hermes auth reset openrouter                             # 清除冷却时间
```

子命令：`add`、`list`、`remove`、`reset`。不带子命令调用时，启动交互式管理向导。

## `hermes status`

```bash
hermes status [--all] [--deep]
```

| 选项 | 描述 |
|--------|-------------|
| `--all` | 以可分享的脱敏格式显示所有详细信息。 |
| `--deep` | 运行更深层的检查，可能需要更长时间。 |

## `hermes cron`

```bash
hermes cron <list|create|edit|pause|resume|run|remove|status|tick>
```

| 子命令 | 描述 |
|------------|-------------|
| `list` | 显示计划任务。 |
| `create` / `add` | 从提示创建计划任务，可选通过重复 `--skill` 附加一个或多个技能。 |
| `edit` | 更新任务的调度、提示、名称、投递方式、重复次数或附加技能。支持 `--clear-skills`、`--add-skill` 和 `--remove-skill`。 |
| `pause` | 暂停任务但不删除。 |
| `resume` | 恢复已暂停的任务并计算下一次运行时间。 |
| `run` | 在下一个调度器周期触发任务。 |
| `remove` | 删除计划任务。 |
| `status` | 检查定时调度器是否正在运行。 |
| `tick` | 运行到期任务一次后退出。 |

## `hermes webhook`

```bash
hermes webhook <subscribe|list|remove|test>
```

管理动态 webhook 订阅，用于事件驱动的代理激活。需要在配置中启用 webhook 平台——如果未配置，会打印设置说明。

| 子命令 | 描述 |
|------------|-------------|
| `subscribe` / `add` | 创建 webhook 路由。返回 URL 和 HMAC 密钥，用于在你的服务上配置。 |
| `list` / `ls` | 显示代理创建的所有订阅。 |
| `remove` / `rm` | 删除动态订阅。config.yaml 中的静态路由不受影响。 |
| `test` | 发送测试 POST 请求以验证订阅是否正常工作。 |

### `hermes webhook subscribe`

```bash
hermes webhook subscribe <name> [选项]
```

| 选项 | 描述 |
|--------|-------------|
| `--prompt` | 提示模板，使用 `{dot.notation}` 引用有效载荷。 |
| `--events` | 逗号分隔的事件类型（例如 `issues,pull_request`）。留空表示全部。 |
| `--description` | 人类可读的描述。 |
| `--skills` | 逗号分隔的技能名称，用于代理运行时加载。 |
| `--deliver` | 投递目标：`log`（默认）、`telegram`、`discord`、`slack`、`github_comment`。 |
| `--deliver-chat-id` | 跨平台投递的目标聊天/频道 ID。 |
| `--secret` | 自定义 HMAC 密钥。省略时自动生成。 |

订阅持久化到 `~/.hermes/webhook_subscriptions.json`，webhook 适配器会热重载，无需重启网关。

## `hermes doctor`

```bash
hermes doctor [--fix]
```

| 选项 | 描述 |
|--------|-------------|
| `--fix` | 在可能的情况下尝试自动修复。 |

## `hermes dump`

```bash
hermes dump [--show-keys]
```

输出整个 Hermes 设置的紧凑纯文本摘要。专为在 Discord、GitHub issues 或 Telegram 中请求支持时复制粘贴而设计——无 ANSI 颜色、无特殊格式，只有数据。

| 选项 | 描述 |
|--------|-------------|
| `--show-keys` | 显示脱敏的 API 密钥前缀（前 4 位和后 4 位字符），而不仅仅是 `set`/`not set`。 |

### 包含内容

| 部分 | 详情 |
|---------|---------|
| **头部信息** | Hermes 版本、发布日期、git commit 哈希 |
| **环境** | 操作系统、Python 版本、OpenAI SDK 版本 |
| **身份** | 活动配置文件名称、HERMES_HOME 路径 |
| **模型** | 配置的默认模型和提供商 |
| **终端** | 后端类型（local、docker、ssh 等） |
| **API 密钥** | 所有 22 个提供商/工具 API 密钥的存在性检查 |
| **功能** | 已启用的工具集、MCP 服务器数量、记忆提供商 |
| **服务** | 网关状态、已配置的消息平台 |
| **工作负载** | 定时任务数量、已安装技能数量 |
| **配置覆盖** | 与默认值不同的所有配置值 |

### 示例输出

```
--- hermes dump ---
version:          0.8.0 (2026.4.8) [af4abd2f]
os:               Linux 6.14.0-37-generic x86_64
python:           3.11.14
openai_sdk:       2.24.0
profile:          default
hermes_home:      ~/.hermes
model:            anthropic/claude-opus-4.6
provider:         openrouter
terminal:         local

api_keys:
  openrouter           set
  openai               not set
  anthropic            set
  nous                 not set
  firecrawl            set
  ...

features:
  toolsets:           all
  mcp_servers:        0
  memory_provider:    built-in
  gateway:            running (systemd)
  platforms:          telegram, discord
  cron_jobs:          3 active / 5 total
  skills:             42

config_overrides:
  agent.max_turns: 250
  compression.threshold: 0.85
  display.streaming: True
--- end dump ---
```

### 适用场景

- 在 GitHub 上报告 bug——将 dump 粘贴到你的 issue 中
- 在 Discord 中寻求帮助——在代码块中分享
- 与他人比较你的设置
- 某些功能不正常时的快速检查

:::tip
`hermes dump` 专为分享而设计。如需交互式诊断，请使用 `hermes doctor`。如需可视化概览，请使用 `hermes status`。
:::

## `hermes debug`

```bash
hermes debug share [选项]
```

将调试报告（系统信息 + 最近日志）上传到粘贴服务并获取可分享的 URL。适用于快速支持请求——包含帮助者诊断问题所需的一切。

| 选项 | 描述 |
|--------|-------------|
| `--lines <N>` | 每个日志文件包含的日志行数（默认：200）。 |
| `--expire <days>` | 粘贴过期天数（默认：7）。 |
| `--local` | 在本地打印报告而不是上传。 |

报告包含系统信息（操作系统、Python 版本、Hermes 版本）、最近的代理和网关日志（每个文件最大 512 KB）以及脱敏的 API 密钥状态。密钥始终脱敏——不会上传任何密钥。

粘贴服务按顺序尝试：paste.rs、dpaste.com。

### 示例

```bash
hermes debug share              # 上传调试报告，打印 URL
hermes debug share --lines 500  # 包含更多日志行
hermes debug share --expire 30  # 保留粘贴 30 天
hermes debug share --local      # 将报告打印到终端（不上传）
```

## `hermes backup`

```bash
hermes backup [选项]
```

创建 Hermes 配置、技能、会话和数据的 zip 归档。备份不包括 hermes-agent 代码库本身。

| 选项 | 描述 |
|--------|-------------|
| `-o`, `--output <path>` | zip 文件的输出路径（默认：`~/hermes-backup-<timestamp>.zip`）。 |
| `-q`, `--quick` | 快速快照：仅关键状态文件（config.yaml、state.db、.env、auth、定时任务）。比完整备份快得多。 |
| `-l`, `--label <name>` | 快照标签（仅与 `--quick` 一起使用）。 |

备份使用 SQLite 的 `backup()` API 进行安全复制，因此即使 Hermes 正在运行也能正确工作（WAL 模式安全）。

### 示例

```bash
hermes backup                           # 完整备份到 ~/hermes-backup-*.zip
hermes backup -o /tmp/hermes.zip        # 完整备份到指定路径
hermes backup --quick                   # 仅状态的快速快照
hermes backup --quick --label "pre-upgrade"  # 带标签的快速快照
```

## `hermes import`

```bash
hermes import <zipfile> [选项]
```

将之前创建的 Hermes 备份恢复到 Hermes 主目录。

| 选项 | 描述 |
|--------|-------------|
| `-f`, `--force` | 无需确认即覆盖现有文件。 |

## `hermes logs`

```bash
hermes logs [日志名称] [选项]
```

查看、跟踪和过滤 Hermes 日志文件。所有日志存储在 `~/.hermes/logs/`（非默认配置文件则为 `<profile>/logs/`）。

### 日志文件

| 名称 | 文件 | 捕获内容 |
|------|------|-----------------|
| `agent`（默认） | `agent.log` | 所有代理活动——API 调用、工具调度、会话生命周期（INFO 及以上） |
| `errors` | `errors.log` | 仅警告和错误——agent.log 的过滤子集 |
| `gateway` | `gateway.log` | 消息网关活动——平台连接、消息调度、webhook 事件 |

### 选项

| 选项 | 描述 |
|--------|-------------|
| `日志名称` | 要查看的日志：`agent`（默认）、`errors`、`gateway`，或 `list` 显示可用文件及大小。 |
| `-n`, `--lines <N>` | 显示的行数（默认：50）。 |
| `-f`, `--follow` | 实时跟踪日志，类似 `tail -f`。按 Ctrl+C 停止。 |
| `--level <LEVEL>` | 显示的最低日志级别：`DEBUG`、`INFO`、`WARNING`、`ERROR`、`CRITICAL`。 |
| `--session <ID>` | 过滤包含会话 ID 子串的行。 |
| `--since <TIME>` | 显示从相对时间之前的行：`30m`、`1h`、`2d` 等。支持 `s`（秒）、`m`（分钟）、`h`（小时）、`d`（天）。 |
| `--component <NAME>` | 按组件过滤：`gateway`、`agent`、`tools`、`cli`、`cron`。 |

### 示例

```bash
# 查看 agent.log 的最后 50 行（默认）
hermes logs

# 实时跟踪 agent.log
hermes logs -f

# 查看 gateway.log 的最后 100 行
hermes logs gateway -n 100

# 仅显示最近一小时的警告和错误
hermes logs --level WARNING --since 1h

# 按特定会话过滤
hermes logs --session abc123

# 跟踪 errors.log，从 30 分钟前开始
hermes logs errors --since 30m -f

# 列出所有日志文件及其大小
hermes logs list
```

### 过滤

过滤器可以组合使用。当多个过滤器同时激活时，日志行必须通过**所有**过滤器才会显示：

```bash
# 最近 2 小时内包含会话 "tg-12345" 的 WARNING+ 级别日志
hermes logs --level WARNING --since 2h --session tg-12345
```

当 `--since` 激活时，没有可解析时间戳的行会被包含（它们可能是多行日志条目的续行）。当 `--level` 激活时，没有可检测级别的行会被包含。

### 日志轮转

Hermes 使用 Python 的 `RotatingFileHandler`。旧日志会自动轮转——查找 `agent.log.1`、`agent.log.2` 等。`hermes logs list` 子命令会显示所有日志文件，包括已轮转的。

## `hermes config`

```bash
hermes config <子命令>
```

子命令：

| 子命令 | 描述 |
|------------|-------------|
| `show` | 显示当前配置值。 |
| `edit` | 在编辑器中打开 `config.yaml`。 |
| `set <key> <value>` | 设置配置值。 |
| `path` | 打印配置文件路径。 |
| `env-path` | 打印 `.env` 文件路径。 |
| `check` | 检查缺失或过时的配置。 |
| `migrate` | 交互式添加新引入的选项。 |

## `hermes pairing`

```bash
hermes pairing <list|approve|revoke|clear-pending>
```

| 子命令 | 描述 |
|------------|-------------|
| `list` | 显示待处理和已批准的用户。 |
| `approve <platform> <code>` | 批准配对码。 |
| `revoke <platform> <user-id>` | 撤销用户的访问权限。 |
| `clear-pending` | 清除待处理的配对码。 |

## `hermes skills`

```bash
hermes skills <子命令>
```

子命令：

| 子命令 | 描述 |
|------------|-------------|
| `browse` | 分页浏览技能注册表。 |
| `search` | 搜索技能注册表。 |
| `install` | 安装技能。 |
| `inspect` | 预览技能而不安装。 |
| `list` | 列出已安装的技能。 |
| `check` | 检查已安装的 hub 技能是否有上游更新。 |
| `update` | 在有上游更改时重新安装 hub 技能。 |
| `audit` | 重新扫描已安装的 hub 技能。 |
| `uninstall` | 移除通过 hub 安装的技能。 |
| `publish` | 将技能发布到注册表。 |
| `snapshot` | 导出/导入技能配置。 |
| `tap` | 管理自定义技能源。 |
| `config` | 按平台交互式启用/禁用技能配置。 |

常见示例：

```bash
hermes skills browse
hermes skills browse --source official
hermes skills search react --source skills-sh
hermes skills search https://mintlify.com/docs --source well-known
hermes skills inspect official/security/1password
hermes skills inspect skills-sh/vercel-labs/json-render/json-render-react
hermes skills install official/migration/openclaw-migration
hermes skills install skills-sh/anthropics/skills/pdf --force
hermes skills check
hermes skills update
hermes skills config
```

注意事项：
- `--force` 可以覆盖第三方/社区技能的非危险策略限制。
- `--force` 不会覆盖 `dangerous`（危险）扫描结果。
- `--source skills-sh` 搜索公共 `skills.sh` 目录。
- `--source well-known` 允许你将 Hermes 指向暴露 `/.well-known/skills/index.json` 的站点。

## `hermes honcho`

```bash
hermes honcho [--target-profile NAME] <子命令>
```

管理 Honcho 跨会话记忆集成。此命令由 Honcho 记忆提供商插件提供，仅在配置中 `memory.provider` 设置为 `honcho` 时可用。

`--target-profile` 标志允许你管理另一个配置文件的 Honcho 配置，而无需切换到该配置文件。

子命令：

| 子命令 | 描述 |
|------------|-------------|
| `setup` | 重定向到 `hermes memory setup`（统一设置路径）。 |
| `status [--all]` | 显示当前 Honcho 配置和连接状态。`--all` 显示跨配置文件概览。 |
| `peers` | 显示所有配置文件的对等身份。 |
| `sessions` | 列出已知的 Honcho 会话映射。 |
| `map [name]` | 将当前目录映射到 Honcho 会话名称。省略 `name` 可列出当前映射。 |
| `peer` | 显示或更新对等名称和辩证推理级别。选项：`--user NAME`、`--ai NAME`、`--reasoning LEVEL`。 |
| `mode [mode]` | 显示或设置召回模式：`hybrid`、`context` 或 `tools`。省略则显示当前设置。 |
| `tokens` | 显示或设置上下文和辩证的 token 预算。选项：`--context N`、`--dialectic N`。 |
| `identity [file] [--show]` | 播种或显示 AI 对等身份表示。 |
| `enable` | 为活动配置文件启用 Honcho。 |
| `disable` | 为活动配置文件禁用 Honcho。 |
| `sync` | 将 Honcho 配置同步到所有现有配置文件（为缺失的创建主机块）。 |
| `migrate` | 从 openclaw-honcho 迁移到 Hermes Honcho 的分步指南。 |

## `hermes memory`

```bash
hermes memory <子命令>
```

设置和管理外部记忆提供商插件。可用提供商：honcho、openviking、mem0、hindsight、holographic、retaindb、byterover、supermemory。同一时间只能有一个外部提供商处于活动状态。内置记忆（MEMORY.md/USER.md）始终处于活动状态。

子命令：

| 子命令 | 描述 |
|------------|-------------|
| `setup` | 交互式提供商选择和配置。 |
| `status` | 显示当前记忆提供商配置。 |
| `off` | 禁用外部提供商（仅内置）。 |

## `hermes acp`

```bash
hermes acp
```

将 Hermes 作为 ACP（Agent Client Protocol）stdio 服务器启动，用于编辑器集成。

相关入口点：

```bash
hermes-acp
python -m acp_adapter
```

先安装支持：

```bash
pip install -e '.[acp]'
```

参阅 [ACP 编辑器集成](../user-guide/features/acp.md) 和 [ACP 内部机制](../developer-guide/acp-internals.md)。

## `hermes mcp`

```bash
hermes mcp <子命令>
```

管理 MCP（Model Context Protocol）服务器配置并将 Hermes 作为 MCP 服务器运行。

| 子命令 | 描述 |
|------------|-------------|
| `serve [-v\|--verbose]` | 将 Hermes 作为 MCP 服务器运行——向其他代理暴露对话。 |
| `add <name> [--url URL] [--command CMD] [--args ...] [--auth oauth\|header]` | 添加 MCP 服务器并自动发现工具。 |
| `remove <name>`（别名：`rm`） | 从配置中移除 MCP 服务器。 |
| `list`（别名：`ls`） | 列出已配置的 MCP 服务器。 |
| `test <name>` | 测试与 MCP 服务器的连接。 |
| `configure <name>`（别名：`config`） | 切换服务器的工具选择。 |

参阅 [MCP 配置参考](./mcp-config-reference.md)、[在 Hermes 中使用 MCP](../guides/use-mcp-with-hermes.md) 和 [MCP 服务器模式](../user-guide/features/mcp.md#running-hermes-as-an-mcp-server)。

## `hermes plugins`

```bash
hermes plugins [子命令]
```

统一的插件管理——通用插件、记忆提供商和上下文引擎集于一处。不带子命令运行 `hermes plugins` 会打开一个包含两个部分的组合交互界面：

- **通用插件**——多选复选框用于启用/禁用已安装的插件
- **提供商插件**——记忆提供商和上下文引擎的单选配置。按 ENTER 打开单选选择器。

| 子命令 | 描述 |
|------------|-------------|
| *（无）* | 组合交互 UI——通用插件开关 + 提供商插件配置。 |
| `install <identifier> [--force]` | 从 Git URL 或 `owner/repo` 安装插件。 |
| `update <name>` | 拉取已安装插件的最新更改。 |
| `remove <name>`（别名：`rm`、`uninstall`） | 移除已安装的插件。 |
| `enable <name>` | 启用已禁用的插件。 |
| `disable <name>` | 禁用插件但不移除。 |
| `list`（别名：`ls`） | 列出已安装的插件及其启用/禁用状态。 |

提供商插件选择保存到 `config.yaml`：
- `memory.provider`——活动的记忆提供商（空 = 仅内置）
- `context.engine`——活动的上下文引擎（`"compressor"` = 内置默认值）

通用插件禁用列表存储在 `config.yaml` 的 `plugins.disabled` 下。

参阅[插件](../user-guide/features/plugins.md)和[构建 Hermes 插件](../guides/build-a-hermes-plugin.md)。

## `hermes tools`

```bash
hermes tools [--summary]
```

| 选项 | 描述 |
|--------|-------------|
| `--summary` | 打印当前已启用工具的摘要并退出。 |

不带 `--summary` 时，启动交互式的按平台工具配置 UI。

## `hermes sessions`

```bash
hermes sessions <子命令>
```

子命令：

| 子命令 | 描述 |
|------------|-------------|
| `list` | 列出最近的会话。 |
| `browse` | 带搜索和恢复功能的交互式会话选择器。 |
| `export <output> [--session-id ID]` | 将会话导出为 JSONL。 |
| `delete <session-id>` | 删除一个会话。 |
| `prune` | 删除旧会话。 |
| `stats` | 显示会话存储统计信息。 |
| `rename <session-id> <title>` | 设置或更改会话标题。 |

## `hermes insights`

```bash
hermes insights [--days N] [--source platform]
```

| 选项 | 描述 |
|--------|-------------|
| `--days <n>` | 分析最近 `n` 天（默认：30）。 |
| `--source <platform>` | 按来源过滤，如 `cli`、`telegram` 或 `discord`。 |

## `hermes claw`

```bash
hermes claw migrate [选项]
```

将你的 OpenClaw 设置迁移到 Hermes。从 `~/.openclaw`（或自定义路径）读取并写入 `~/.hermes`。自动检测遗留目录名称（`~/.clawdbot`、`~/.moltbot`）和配置文件名（`clawdbot.json`、`moltbot.json`）。

| 选项 | 描述 |
|--------|-------------|
| `--dry-run` | 预览将要迁移的内容而不写入任何内容。 |
| `--preset <name>` | 迁移预设：`full`（默认，包含密钥）或 `user-data`（不包含 API 密钥）。 |
| `--overwrite` | 冲突时覆盖现有 Hermes 文件（默认：跳过）。 |
| `--migrate-secrets` | 在迁移中包含 API 密钥（`--preset full` 默认启用）。 |
| `--source <path>` | 自定义 OpenClaw 目录（默认：`~/.openclaw`）。 |
| `--workspace-target <path>` | 工作区指令（AGENTS.md）的目标目录。 |
| `--skill-conflict <mode>` | 处理技能名称冲突：`skip`（默认）、`overwrite` 或 `rename`。 |
| `--yes` | 跳过确认提示。 |

### 迁移内容

迁移涵盖 30 多个类别，包括人设、记忆、技能、模型提供商、消息平台、代理行为、会话策略、MCP 服务器、TTS 等。项目要么**直接导入**为 Hermes 等效项，要么**归档**以供手动审查。

**直接导入：** SOUL.md、MEMORY.md、USER.md、AGENTS.md、技能（4 个源目录）、默认模型、自定义提供商、MCP 服务器、消息平台令牌和白名单（Telegram、Discord、Slack、WhatsApp、Signal、Matrix、Mattermost）、代理默认值（推理强度、压缩、人类延迟、时区、沙箱）、会话重置策略、审批规则、TTS 配置、浏览器设置、工具设置、执行超时、命令白名单、网关配置以及来自 3 个源的 API 密钥。

**归档以供手动审查：** 定时任务、插件、钩子/webhook、记忆后端（QMD）、技能注册表配置、UI/身份、日志、多代理设置、频道绑定、IDENTITY.md、TOOLS.md、HEARTBEAT.md、BOOTSTRAP.md。

**API 密钥解析**按优先顺序检查三个源：配置值 -> `~/.openclaw/.env` -> `auth-profiles.json`。所有令牌字段处理纯字符串、环境模板（`${VAR}`）和 SecretRef 对象。

有关完整的配置键映射、SecretRef 处理详情和迁移后检查清单，请参阅**[完整迁移指南](../guides/migrate-from-openclaw.md)**。

### 示例

```bash
# 预览将要迁移的内容
hermes claw migrate --dry-run

# 包含 API 密钥的完整迁移
hermes claw migrate --preset full

# 仅迁移用户数据（不含密钥），覆盖冲突
hermes claw migrate --preset user-data --overwrite

# 从自定义 OpenClaw 路径迁移
hermes claw migrate --source /home/user/old-openclaw
```

## `hermes dashboard`

```bash
hermes dashboard [选项]
```

启动 Web 仪表板——基于浏览器的 UI，用于管理配置、API 密钥和监控会话。需要 `pip install hermes-agent[web]`（FastAPI + Uvicorn）。完整文档参阅 [Web 仪表板](/docs/user-guide/features/web-dashboard)。

| 选项 | 默认值 | 描述 |
|--------|---------|-------------|
| `--port` | `9119` | Web 服务器运行端口 |
| `--host` | `127.0.0.1` | 绑定地址 |
| `--no-open` | — | 不自动打开浏览器 |

```bash
# 默认——打开浏览器访问 http://127.0.0.1:9119
hermes dashboard

# 自定义端口，不打开浏览器
hermes dashboard --port 8080 --no-open
```

## `hermes profile`

```bash
hermes profile <子命令>
```

管理配置文件——多个隔离的 Hermes 实例，每个实例拥有独立的配置、会话、技能和主目录。

| 子命令 | 描述 |
|------------|-------------|
| `list` | 列出所有配置文件。 |
| `use <name>` | 设置默认配置文件。 |
| `create <name> [--clone] [--clone-all] [--clone-from <source>] [--no-alias]` | 创建新配置文件。`--clone` 从活动配置文件复制 config、`.env` 和 `SOUL.md`。`--clone-all` 复制所有状态。`--clone-from` 指定源配置文件。 |
| `delete <name> [-y]` | 删除配置文件。 |
| `show <name>` | 显示配置文件详情（主目录、配置等）。 |
| `alias <name> [--remove] [--name NAME]` | 管理快速访问的包装脚本。 |
| `rename <old> <new>` | 重命名配置文件。 |
| `export <name> [-o FILE]` | 将配置文件导出为 `.tar.gz` 归档。 |
| `import <archive> [--name NAME]` | 从 `.tar.gz` 归档导入配置文件。 |

示例：

```bash
hermes profile list
hermes profile create work --clone
hermes profile use work
hermes profile alias work --name h-work
hermes profile export work -o work-backup.tar.gz
hermes profile import work-backup.tar.gz --name restored
hermes -p work chat -q "Hello from work profile"
```

## `hermes completion`

```bash
hermes completion [bash|zsh]
```

将 shell 补全脚本打印到标准输出。在 shell 配置文件中引用该输出，即可对 Hermes 命令、子命令和配置文件名进行 tab 补全。

示例：

```bash
# Bash
hermes completion bash >> ~/.bashrc

# Zsh
hermes completion zsh >> ~/.zshrc
```

## 维护命令

| 命令 | 描述 |
|---------|-------------|
| `hermes version` | 打印版本信息。 |
| `hermes update` | 拉取最新更改并重新安装依赖。 |
| `hermes uninstall [--full] [--yes]` | 移除 Hermes，可选删除所有配置/数据。 |

## 另请参阅

- [斜杠命令参考](./slash-commands.md)
- [CLI 界面](../user-guide/cli.md)
- [会话](../user-guide/sessions.md)
- [技能系统](../user-guide/features/skills.md)
- [皮肤与主题](../user-guide/features/skins.md)
