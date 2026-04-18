# Hermes Agent v0.6.0 (v2026.3.30)

**发布日期：** 2026年3月30日

> 多实例发布版 — 用于运行隔离代理实例的配置文件、MCP 服务器模式、Docker 容器、回退提供商链、两个新消息平台（飞书/Lark 和企业微信）、Telegram webhook 模式、Slack 多工作区 OAuth、95 个 PR 和 16 个已解决的问题，仅用 2 天完成。

---

## ✨ 亮点

- **配置文件 — 多实例 Hermes** — 从同一安装运行多个隔离的 Hermes 实例。每个配置文件拥有自己的配置、记忆、会话、技能和网关服务。使用 `hermes profile create` 创建，使用 `hermes -p <name>` 切换，支持导出/导入以便共享。完整的令牌锁隔离防止两个配置文件使用相同的机器人凭据。([#3681](https://github.com/NousResearch/hermes-agent/pull/3681))

- **MCP 服务器模式** — 通过 `hermes mcp serve` 将 Hermes 对话和会话暴露给任何 MCP 兼容客户端（Claude Desktop、Cursor、VS Code 等）。浏览对话、读取消息、跨会话搜索和管理附件 — 全部通过 Model Context Protocol 实现。支持 stdio 和 Streamable HTTP 两种传输方式。([#3795](https://github.com/NousResearch/hermes-agent/pull/3795))

- **Docker 容器** — 用于在容器中运行 Hermes Agent 的官方 Dockerfile。支持 CLI 和网关模式，配置通过卷挂载。([#3668](https://github.com/NousResearch/hermes-agent/pull/3668)，关闭 [#850](https://github.com/NousResearch/hermes-agent/issues/850))

- **有序回退提供商链** — 配置多个推理提供商并自动故障转移。当主要提供商返回错误或不可达时，Hermes 自动尝试链中的下一个提供商。通过 config.yaml 中的 `fallback_providers` 进行配置。([#3813](https://github.com/NousResearch/hermes-agent/pull/3813)，关闭 [#1734](https://github.com/NousResearch/hermes-agent/issues/1734))

- **飞书/Lark 平台支持** — 完整的飞书和 Lark 网关适配器，支持事件订阅、消息卡片、群聊、图片/文件附件和交互式卡片回调。([#3799](https://github.com/NousResearch/hermes-agent/pull/3799)，[#3817](https://github.com/NousResearch/hermes-agent/pull/3817)，关闭 [#1788](https://github.com/NousResearch/hermes-agent/issues/1788))

- **企业微信（WeCom）平台支持** — 新的企业微信网关适配器，支持文本/图片/语音消息、群聊和回调验证。([#3847](https://github.com/NousResearch/hermes-agent/pull/3847))

- **Slack 多工作区 OAuth** — 通过 OAuth 令牌文件将单个 Hermes 网关连接到多个 Slack 工作区。每个工作区获得自己的机器人令牌，根据传入事件动态解析。([#3903](https://github.com/NousResearch/hermes-agent/pull/3903))

- **Telegram Webhook 模式与群组控制** — 以 webhook 模式运行 Telegram 适配器作为轮询的替代方案 — 响应时间更快，更适合反向代理后的生产部署。新的群组提及门控控制机器人何时响应：始终、仅 @提及时或通过正则触发。([#3880](https://github.com/NousResearch/hermes-agent/pull/3880)，[#3870](https://github.com/NousResearch/hermes-agent/pull/3870))

- **Exa 搜索后端** — 添加 Exa 作为 Firecrawl 和 DuckDuckGo 之外的替代网络搜索和内容提取后端。设置 `EXA_API_KEY` 并配置为首选后端。([#3648](https://github.com/NousResearch/hermes-agent/pull/3648))

- **远程后端上的技能与凭据** — 将技能目录和凭据文件挂载到 Modal 和 Docker 容器中，使远程终端会话可以访问与本地执行相同的技能和密钥。([#3890](https://github.com/NousResearch/hermes-agent/pull/3890)，[#3671](https://github.com/NousResearch/hermes-agent/pull/3671)，关闭 [#3665](https://github.com/NousResearch/hermes-agent/issues/3665)，[#3433](https://github.com/NousResearch/hermes-agent/issues/3433))

---

## 🏗️ 核心代理与架构

### 提供商与模型支持
- **有序回退提供商链** — 跨多个配置提供商的自动故障转移 ([#3813](https://github.com/NousResearch/hermes-agent/pull/3813))
- **修复提供商切换时的 api_mode** — 通过 `hermes model` 切换提供商时现在正确清除过时的 `api_mode`，而不是硬编码 `chat_completions`，修复了具有 Anthropic 兼容端点的提供商的 404 错误 ([#3726](https://github.com/NousResearch/hermes-agent/pull/3726)，[#3857](https://github.com/NousResearch/hermes-agent/pull/3857)，关闭 [#3685](https://github.com/NousResearch/hermes-agent/issues/3685))
- **停止静默 OpenRouter 回退** — 当没有配置提供商时，Hermes 现在引发清晰的错误而不是静默路由到 OpenRouter ([#3807](https://github.com/NousResearch/hermes-agent/pull/3807)，[#3862](https://github.com/NousResearch/hermes-agent/pull/3862))
- **Gemini 3.1 预览模型** — 添加到 OpenRouter 和 Nous Portal 目录 ([#3803](https://github.com/NousResearch/hermes-agent/pull/3803)，关闭 [#3753](https://github.com/NousResearch/hermes-agent/issues/3753))
- **Gemini 直接 API 上下文长度** — 直接 Google AI 端点的完整上下文长度解析 ([#3876](https://github.com/NousResearch/hermes-agent/pull/3876))
- **gpt-5.4-mini** 添加到 Codex 回退目录 ([#3855](https://github.com/NousResearch/hermes-agent/pull/3855))
- **精选模型列表优先**于实时 API 探测（当探测返回更少模型时）([#3856](https://github.com/NousResearch/hermes-agent/pull/3856)，[#3867](https://github.com/NousResearch/hermes-agent/pull/3867))
- **友好的 429 速率限制消息**，带有 Retry-After 倒计时 ([#3809](https://github.com/NousResearch/hermes-agent/pull/3809))
- **辅助客户端占位符密钥**，用于无需身份验证的本地服务器 ([#3842](https://github.com/NousResearch/hermes-agent/pull/3842))
- **INFO 级别日志记录**用于辅助提供商解析 ([#3866](https://github.com/NousResearch/hermes-agent/pull/3866))

### 代理循环与对话
- **子代理状态报告** — 当摘要存在时报告 `completed` 状态而非通用失败 ([#3829](https://github.com/NousResearch/hermes-agent/pull/3829))
- **会话日志文件在压缩期间更新** — 防止上下文压缩后的过时文件引用 ([#3835](https://github.com/NousResearch/hermes-agent/pull/3835))
- **省略空 tools 参数** — 当为空时不发送 `tools` 参数而非发送 `None`，修复了与严格提供商的兼容性 ([#3820](https://github.com/NousResearch/hermes-agent/pull/3820))

### 配置文件与多实例
- **配置文件系统** — `hermes profile create/list/switch/delete/export/import/rename`。每个配置文件获得隔离的 HERMES_HOME、网关服务、CLI 包装器。令牌锁防止凭据冲突。配置文件名支持 Tab 补全。([#3681](https://github.com/NousResearch/hermes-agent/pull/3681))
- **配置文件感知的显示路径** — 所有面向用户的 `~/.hermes` 路径替换为 `display_hermes_home()` 以显示正确的配置文件目录 ([#3623](https://github.com/NousResearch/hermes-agent/pull/3623))
- **延迟 display_hermes_home 导入** — 防止 `hermes update` 期间模块缓存过时字节码时的 `ImportError` ([#3776](https://github.com/NousResearch/hermes-agent/pull/3776))
- **受保护路径的 HERMES_HOME** — `.env` 写入拒绝路径现在尊重 HERMES_HOME 而不是硬编码的 `~/.hermes` ([#3840](https://github.com/NousResearch/hermes-agent/pull/3840))

---

## 📱 消息平台（网关）

### 新平台
- **飞书/Lark** — 完整适配器，支持事件订阅、消息卡片、群聊、图片/文件附件、交互式卡片回调 ([#3799](https://github.com/NousResearch/hermes-agent/pull/3799)，[#3817](https://github.com/NousResearch/hermes-agent/pull/3817))
- **企业微信（WeCom）** — 文本/图片/语音消息、群聊、回调验证 ([#3847](https://github.com/NousResearch/hermes-agent/pull/3847))

### Telegram
- **Webhook 模式** — 以 webhook 端点运行而非轮询，适用于生产部署 ([#3880](https://github.com/NousResearch/hermes-agent/pull/3880))
- **群组提及门控与正则触发** — 可配置的群组中机器人响应行为：始终、仅 @提及或正则匹配 ([#3870](https://github.com/NousResearch/hermes-agent/pull/3870))
- **优雅处理已删除的回复目标** — 当被回复的消息已删除时不再崩溃 ([#3858](https://github.com/NousResearch/hermes-agent/pull/3858)，关闭 [#3229](https://github.com/NousResearch/hermes-agent/issues/3229))

### Discord
- **消息处理反应** — 处理时添加反应 emoji，完成后移除，在频道中提供视觉反馈 ([#3871](https://github.com/NousResearch/hermes-agent/pull/3871))
- **DISCORD_IGNORE_NO_MENTION** — 跳过 @提及其他用户/机器人但未提及 Hermes 的消息 ([#3640](https://github.com/NousResearch/hermes-agent/pull/3640))
- **清理延迟的 "thinking..."** — 在斜杠命令完成后正确移除"思考中..."指示器 ([#3674](https://github.com/NousResearch/hermes-agent/pull/3674)，关闭 [#3595](https://github.com/NousResearch/hermes-agent/issues/3595))

### Slack
- **多工作区 OAuth** — 通过 OAuth 令牌文件从单个网关连接到多个 Slack 工作区 ([#3903](https://github.com/NousResearch/hermes-agent/pull/3903))

### WhatsApp
- **持久 aiohttp 会话** — 跨请求重用 HTTP 会话而非每条消息创建新会话 ([#3818](https://github.com/NousResearch/hermes-agent/pull/3818))
- **LID↔电话号码别名解析** — 在允许列表中正确匹配 Linked ID 和电话号码格式 ([#3830](https://github.com/NousResearch/hermes-agent/pull/3830))
- **机器人模式下跳过回复前缀** — 作为 WhatsApp 机器人运行时更清洁的消息格式 ([#3931](https://github.com/NousResearch/hermes-agent/pull/3931))

### Matrix
- **通过 MSC3245 的原生语音消息** — 将语音消息作为正确的 Matrix 语音事件发送而非文件附件 ([#3877](https://github.com/NousResearch/hermes-agent/pull/3877))

### Mattermost
- **可配置的提及行为** — 无需 @提及即可响应消息 ([#3664](https://github.com/NousResearch/hermes-agent/pull/3664))

### Signal
- **URL 编码电话号码**并修正附件 RPC 参数 — 修复某些电话号码格式的发送失败 ([#3670](https://github.com/NousResearch/hermes-agent/pull/3670)) — @kshitijk4poor

### Email
- **失败时关闭 SMTP/IMAP 连接** — 防止错误场景中的连接泄漏 ([#3804](https://github.com/NousResearch/hermes-agent/pull/3804))

### 网关核心
- **原子配置写入** — 使用原子文件写入 config.yaml 以防止崩溃时的数据丢失 ([#3800](https://github.com/NousResearch/hermes-agent/pull/3800))
- **主频道环境变量覆盖** — 一致地应用主频道的环境变量覆盖 ([#3796](https://github.com/NousResearch/hermes-agent/pull/3796)，[#3808](https://github.com/NousResearch/hermes-agent/pull/3808))
- **用 logger 替换 print()** — BasePlatformAdapter 现在使用正确的日志记录而非 print 语句 ([#3669](https://github.com/NousResearch/hermes-agent/pull/3669))
- **Cron 投递标签** — 通过频道目录解析人类友好的投递标签 ([#3860](https://github.com/NousResearch/hermes-agent/pull/3860)，关闭 [#1945](https://github.com/NousResearch/hermes-agent/issues/1945))
- **Cron [SILENT] 收紧** — 防止代理在报告前添加 [SILENT] 前缀以抑制投递 ([#3901](https://github.com/NousResearch/hermes-agent/pull/3901))
- **后台任务媒体投递**和视觉下载超时修复 ([#3919](https://github.com/NousResearch/hermes-agent/pull/3919))
- **Boot-md 钩子** — 在网关启动时运行 BOOT.md 文件的示例内置钩子 ([#3733](https://github.com/NousResearch/hermes-agent/pull/3733))

---

## 🖥️ CLI 与用户体验

### 交互式 CLI
- **可配置的工具预览长度** — 默认显示完整文件路径而非截断到 40 个字符 ([#3841](https://github.com/NousResearch/hermes-agent/pull/3841))
- **工具令牌上下文显示** — `hermes tools` 清单现在显示每个工具集的估计令牌成本 ([#3805](https://github.com/NousResearch/hermes-agent/pull/3805))
- **/bg 旋转器 TUI 修复** — 将后台任务旋转器通过 TUI 小部件路由以防止状态栏冲突 ([#3643](https://github.com/NousResearch/hermes-agent/pull/3643))
- **防止状态栏换行**成重复行 ([#3883](https://github.com/NousResearch/hermes-agent/pull/3883)) — @kshitijk4poor
- **处理关闭的 stdout ValueError** — 修复网关线程关闭期间 stdout 关闭时的崩溃 ([#3843](https://github.com/NousResearch/hermes-agent/pull/3843)，关闭 [#3534](https://github.com/NousResearch/hermes-agent/issues/3534))
- **从 /tools disable 中移除 input()** — 消除禁用工具时终端冻结 ([#3918](https://github.com/NousResearch/hermes-agent/pull/3918))
- **交互式 CLI 命令的 TTY 保护** — 防止在没有终端的情况下启动时的 CPU 空转 ([#3933](https://github.com/NousResearch/hermes-agent/pull/3933))
- **Argparse 入口点** — 在顶级启动器中使用 argparse 以获得更清洁的错误处理 ([#3874](https://github.com/NousResearch/hermes-agent/pull/3874))
- **延迟初始化的工具在横幅中显示黄色**而非红色，减少关于"缺失"工具的虚假警报 ([#3822](https://github.com/NousResearch/hermes-agent/pull/3822))
- **配置后 Honcho 工具在横幅中显示** ([#3810](https://github.com/NousResearch/hermes-agent/pull/3810))

### 设置与配置
- **在 `hermes setup` 期间自动安装 matrix-nio**（当选择 Matrix 时）([#3802](https://github.com/NousResearch/hermes-agent/pull/3802)，[#3873](https://github.com/NousResearch/hermes-agent/pull/3873))
- **会话导出 stdout 支持** — 使用 `-` 导出会话到 stdout 以便管道传输 ([#3641](https://github.com/NousResearch/hermes-agent/pull/3641)，关闭 [#3609](https://github.com/NousResearch/hermes-agent/issues/3609))
- **可配置的审批超时** — 设置危险命令审批提示在自动拒绝前等待的时间 ([#3886](https://github.com/NousResearch/hermes-agent/pull/3886)，关闭 [#3765](https://github.com/NousResearch/hermes-agent/issues/3765))
- **更新时清除 __pycache__** — 防止 `hermes update` 后过时字节码导致的 ImportError ([#3819](https://github.com/NousResearch/hermes-agent/pull/3819))

---

## 🔧 工具系统

### MCP
- **MCP 服务器模式** — `hermes mcp serve` 通过 stdio 或 Streamable HTTP 向 MCP 客户端暴露对话、会话和附件 ([#3795](https://github.com/NousResearch/hermes-agent/pull/3795))
- **动态工具发现** — 响应 `notifications/tools/list_changed` 事件以从 MCP 服务器获取新工具而无需重新连接 ([#3812](https://github.com/NousResearch/hermes-agent/pull/3812))
- **非弃用的 HTTP 传输** — 从 `sse_client` 切换到 `streamable_http_client` ([#3646](https://github.com/NousResearch/hermes-agent/pull/3646))

### 网络工具
- **Exa 搜索后端** — 作为 Firecrawl 和 DuckDuckGo 之外的网络搜索和提取替代方案 ([#3648](https://github.com/NousResearch/hermes-agent/pull/3648))

### 浏览器
- **防止浏览器快照和视觉工具中的 None LLM 响应** ([#3642](https://github.com/NousResearch/hermes-agent/pull/3642))

### 终端与远程后端
- **将技能目录挂载**到 Modal 和 Docker 容器中 ([#3890](https://github.com/NousResearch/hermes-agent/pull/3890))
- **将凭据文件挂载**到远程后端，带有 mtime+size 缓存 ([#3671](https://github.com/NousResearch/hermes-agent/pull/3671))
- **命令超时时保留部分输出**而非丢失所有内容 ([#3868](https://github.com/NousResearch/hermes-agent/pull/3868))
- **停止将持久化的环境变量标记为缺失**（远程后端）([#3650](https://github.com/NousResearch/hermes-agent/pull/3650))

### 音频
- 转录工具中的 **.aac 格式支持** ([#3865](https://github.com/NousResearch/hermes-agent/pull/3865)，关闭 [#1963](https://github.com/NousResearch/hermes-agent/issues/1963))
- **音频下载重试** — `cache_audio_from_url` 的重试逻辑，匹配现有的图片下载模式 ([#3401](https://github.com/NousResearch/hermes-agent/pull/3401)) — @binhnt92

### 视觉
- **拒绝非图片文件**并对视觉分析强制执行仅网站策略 ([#3845](https://github.com/NousResearch/hermes-agent/pull/3845))

### 工具 Schema
- **确保 name 字段**始终存在于工具定义中，修复 `KeyError: 'name'` 崩溃 ([#3811](https://github.com/NousResearch/hermes-agent/pull/3811)，关闭 [#3729](https://github.com/NousResearch/hermes-agent/issues/3729))

### ACP（编辑器集成）
- **完整的会话管理界面**用于 VS Code/Zed/JetBrains 客户端 — 正确的任务生命周期、取消支持、会话持久化 ([#3675](https://github.com/NousResearch/hermes-agent/pull/3675))

---

## 🧩 技能与插件

### 技能系统
- **外部技能目录** — 通过 config.yaml 中的 `skills.external_dirs` 配置额外的技能目录 ([#3678](https://github.com/NousResearch/hermes-agent/pull/3678))
- **分类路径遍历已阻止** — 防止技能分类名称中的 `../` 攻击 ([#3844](https://github.com/NousResearch/hermes-agent/pull/3844))
- **parallel-cli 移至可选技能** — 减少默认技能占用 ([#3673](https://github.com/NousResearch/hermes-agent/pull/3673)) — @kshitijk4poor

### 新技能
- **memento-flashcards** — 间隔重复闪卡系统 ([#3827](https://github.com/NousResearch/hermes-agent/pull/3827))
- **songwriting-and-ai-music** — 歌曲创作技巧和 AI 音乐生成提示 ([#3834](https://github.com/NousResearch/hermes-agent/pull/3834))
- **SiYuan Note** — 思源笔记应用集成 ([#3742](https://github.com/NousResearch/hermes-agent/pull/3742))
- **Scrapling** — 使用 Scrapling 库的网络抓取技能 ([#3742](https://github.com/NousResearch/hermes-agent/pull/3742))
- **one-three-one-rule** — 沟通框架技能 ([#3797](https://github.com/NousResearch/hermes-agent/pull/3797))

### 插件系统
- **插件启用/禁用命令** — `hermes plugins enable/disable <name>` 用于管理插件状态而无需移除它们 ([#3747](https://github.com/NousResearch/hermes-agent/pull/3747))
- **插件消息注入** — 插件现在可以通过 `ctx.inject_message()` 代表用户向对话流注入消息 ([#3778](https://github.com/NousResearch/hermes-agent/pull/3778)) — @winglian
- **Honcho 自托管支持** — 允许无需 API 密钥的本地 Honcho 实例 ([#3644](https://github.com/NousResearch/hermes-agent/pull/3644))

---

## 🔒 安全与可靠性

### 安全加固
- **加固危险命令检测** — 扩展了危险 shell 命令的模式匹配，并为敏感位置（`/etc/`、`/boot/`、docker.sock）添加了文件工具路径保护 ([#3872](https://github.com/NousResearch/hermes-agent/pull/3872))
- **审批系统中的敏感路径写入检查** — 通过文件工具捕获对系统配置文件的写入，而不仅仅是终端 ([#3859](https://github.com/NousResearch/hermes-agent/pull/3859))
- **密钥编辑扩展** — 现在覆盖 ElevenLabs、Tavily 和 Exa API 密钥 ([#3920](https://github.com/NousResearch/hermes-agent/pull/3920))
- **视觉文件拒绝** — 拒绝传递给视觉分析的非图片文件以防止信息泄露 ([#3845](https://github.com/NousResearch/hermes-agent/pull/3845))
- **分类路径遍历阻止** — 防止技能分类名称中的目录遍历 ([#3844](https://github.com/NousResearch/hermes-agent/pull/3844))

### 可靠性
- **原子 config.yaml 写入** — 防止网关崩溃时的数据丢失 ([#3800](https://github.com/NousResearch/hermes-agent/pull/3800))
- **更新时清除 __pycache__** — 防止更新后过时字节码导致的 ImportError ([#3819](https://github.com/NousResearch/hermes-agent/pull/3819))
- **更新安全的延迟导入** — 防止 `hermes update` 期间模块引用新函数时的 ImportError 链 ([#3776](https://github.com/NousResearch/hermes-agent/pull/3776))
- **从补丁损坏中恢复 terminalbench2** — 恢复了被补丁工具的密钥编辑损坏的文件 ([#3801](https://github.com/NousResearch/hermes-agent/pull/3801))
- **终端超时保留部分输出** — 不再丢失超时时的命令输出 ([#3868](https://github.com/NousResearch/hermes-agent/pull/3868))

---

## 🐛 重要 Bug 修复

- **OpenClaw 迁移模型配置覆盖** — 迁移不再用字符串覆盖模型配置字典 ([#3924](https://github.com/NousResearch/hermes-agent/pull/3924)) — @0xbyt4
- **OpenClaw 迁移扩展** — 覆盖完整数据足迹，包括会话、cron、记忆 ([#3869](https://github.com/NousResearch/hermes-agent/pull/3869))
- **Telegram 已删除的回复目标** — 优雅处理对已删除消息的回复而非崩溃 ([#3858](https://github.com/NousResearch/hermes-agent/pull/3858))
- **Discord "thinking..." 持续** — 正确清理延迟的响应指示器 ([#3674](https://github.com/NousResearch/hermes-agent/pull/3674))
- **WhatsApp LID↔电话号码别名** — 修复 Linked ID 格式的允许列表匹配失败 ([#3830](https://github.com/NousResearch/hermes-agent/pull/3830))
- **Signal URL 编码的电话号码** — 修复某些格式的发送失败 ([#3670](https://github.com/NousResearch/hermes-agent/pull/3670))
- **Email 连接泄漏** — 错误时正确关闭 SMTP/IMAP 连接 ([#3804](https://github.com/NousResearch/hermes-agent/pull/3804))
- **_safe_print ValueError** — 不再因关闭的 stdout 导致网关线程崩溃 ([#3843](https://github.com/NousResearch/hermes-agent/pull/3843))
- **工具 schema KeyError 'name'** — 确保 name 字段始终存在于工具定义中 ([#3811](https://github.com/NousResearch/hermes-agent/pull/3811))
- **提供商切换时 api_mode 过时** — 通过 `hermes model` 切换提供商时正确清除 ([#3857](https://github.com/NousResearch/hermes-agent/pull/3857))

---

## 🧪 测试

- 解决了钩子、tiktoken、插件和技能测试中的 10+ 个 CI 失败 ([#3848](https://github.com/NousResearch/hermes-agent/pull/3848)，[#3721](https://github.com/NousResearch/hermes-agent/pull/3721)，[#3936](https://github.com/NousResearch/hermes-agent/pull/3936))

---

## 📚 文档

- **全面的 OpenClaw 迁移指南** — 从 OpenClaw/Claw3D 迁移到 Hermes Agent 的分步指南 ([#3864](https://github.com/NousResearch/hermes-agent/pull/3864)，[#3900](https://github.com/NousResearch/hermes-agent/pull/3900))
- **凭据文件透传文档** — 记录如何将凭据文件和环境变量转发到远程后端 ([#3677](https://github.com/NousResearch/hermes-agent/pull/3677))
- **DuckDuckGo 需求说明** — 说明对 duckduckgo-search 包的运行时依赖 ([#3680](https://github.com/NousResearch/hermes-agent/pull/3680))
- **技能目录更新** — 添加了红队测试分类和可选技能列表 ([#3745](https://github.com/NousResearch/hermes-agent/pull/3745))
- **飞书文档 MDX 修复** — 转义破坏 Docusaurus 构建的尖括号 URL ([#3902](https://github.com/NousResearch/hermes-agent/pull/3902))

---

## 👥 贡献者

### 核心
- **@teknium1** — 90 个 PR，涵盖所有子系统

### 社区贡献者
- **@kshitijk4poor** — 3 个 PR：Signal 电话号码修复 ([#3670](https://github.com/NousResearch/hermes-agent/pull/3670))、parallel-cli 移至可选技能 ([#3673](https://github.com/NousResearch/hermes-agent/pull/3673))、状态栏换行修复 ([#3883](https://github.com/NousResearch/hermes-agent/pull/3883))
- **@winglian** — 1 个 PR：插件消息注入接口 ([#3778](https://github.com/NousResearch/hermes-agent/pull/3778))
- **@binhnt92** — 1 个 PR：音频下载重试逻辑 ([#3401](https://github.com/NousResearch/hermes-agent/pull/3401))
- **@0xbyt4** — 1 个 PR：OpenClaw 迁移模型配置修复 ([#3924](https://github.com/NousResearch/hermes-agent/pull/3924))

### 社区解决的问题
@Material-Scientist ([#850](https://github.com/NousResearch/hermes-agent/issues/850)), @hanxu98121 ([#1734](https://github.com/NousResearch/hermes-agent/issues/1734)), @penwyp ([#1788](https://github.com/NousResearch/hermes-agent/issues/1788)), @dan-and ([#1945](https://github.com/NousResearch/hermes-agent/issues/1945)), @AdrianScott ([#1963](https://github.com/NousResearch/hermes-agent/issues/1963)), @clawdbot47 ([#3229](https://github.com/NousResearch/hermes-agent/issues/3229)), @alanfwilliams ([#3404](https://github.com/NousResearch/hermes-agent/issues/3404)), @kentimsit ([#3433](https://github.com/NousResearch/hermes-agent/issues/3433)), @hayka-pacha ([#3534](https://github.com/NousResearch/hermes-agent/issues/3534)), @primmer ([#3595](https://github.com/NousResearch/hermes-agent/issues/3595)), @dagelf ([#3609](https://github.com/NousResearch/hermes-agent/issues/3609)), @HenkDz ([#3685](https://github.com/NousResearch/hermes-agent/issues/3685)), @tmdgusya ([#3729](https://github.com/NousResearch/hermes-agent/issues/3729)), @TypQxQ ([#3753](https://github.com/NousResearch/hermes-agent/issues/3753)), @acsezen ([#3765](https://github.com/NousResearch/hermes-agent/issues/3765))

---

**完整变更日志**：[v2026.3.28...v2026.3.30](https://github.com/NousResearch/hermes-agent/compare/v2026.3.28...v2026.3.30)
