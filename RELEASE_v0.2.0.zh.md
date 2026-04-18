# Hermes Agent v0.2.0 (v2026.3.12)

**发布日期：** 2026年3月12日

> 自 v0.1.0（初始预公开基础版本）以来的首个标记发布版本。在短短两周多的时间里，Hermes Agent 从一个小型内部项目发展成为功能齐全的 AI 代理平台——这要归功于社区贡献的爆发式增长。本次发布涵盖了来自 **63 位贡献者** 的 **216 个已合并的拉取请求**，解决了 **119 个问题**。

---

## ✨ 亮点

- **多平台消息网关** — Telegram、Discord、Slack、WhatsApp、Signal、Email（IMAP/SMTP）和 Home Assistant 平台，具有统一的会话管理、媒体附件和按平台工具配置功能。

- **MCP（模型上下文协议）客户端** — 原生 MCP 支持，包括 stdio 和 HTTP 传输、重连、资源/提示发现以及采样（服务器发起的 LLM 请求）。([#291](https://github.com/NousResearch/hermes-agent/pull/291) — @0xbyt4, [#301](https://github.com/NousResearch/hermes-agent/pull/301), [#753](https://github.com/NousResearch/hermes-agent/pull/753))

- **技能生态系统** — 70+ 个内置和可选技能，涵盖 15+ 个分类，配有社区发现的技能中心、按平台启用/禁用、基于工具可用性的条件激活以及前置条件验证。([#743](https://github.com/NousResearch/hermes-agent/pull/743) — @teyrebaz33, [#785](https://github.com/NousResearch/hermes-agent/pull/785) — @teyrebaz33)

- **集中式提供商路由** — 统一的 `call_llm()`/`async_call_llm()` API 替代了分散在视觉、摘要、压缩和轨迹保存中的提供商逻辑。所有辅助消费者通过单一代码路径路由，并自动解析凭证。([#1003](https://github.com/NousResearch/hermes-agent/pull/1003))

- **ACP 服务器** — 通过代理通信协议标准集成 VS Code、Zed 和 JetBrains 编辑器。([#949](https://github.com/NousResearch/hermes-agent/pull/949))

- **CLI 皮肤/主题引擎** — 数据驱动的视觉自定义：横幅、加载动画、颜色、品牌。7 个内置皮肤 + 自定义 YAML 皮肤。

- **Git 工作树隔离** — `hermes -w` 在 git 工作树中启动隔离的代理会话，实现同一仓库上的安全并行工作。([#654](https://github.com/NousResearch/hermes-agent/pull/654))

- **文件系统检查点与回滚** — 在破坏性操作前自动快照，使用 `/rollback` 恢复。([#824](https://github.com/NousResearch/hermes-agent/pull/824))

- **3,289 项测试** — 从几乎零覆盖到全面的测试套件，涵盖代理、网关、工具、定时任务和 CLI。

---

## 🏗️ 核心代理与架构

### 提供商与模型支持
- 集中式提供商路由，包含 `resolve_provider_client()` + `call_llm()` API ([#1003](https://github.com/NousResearch/hermes-agent/pull/1003))
- Nous Portal 作为一级提供商加入设置 ([#644](https://github.com/NousResearch/hermes-agent/issues/644))
- OpenAI Codex（Responses API）支持 ChatGPT 订阅 ([#43](https://github.com/NousResearch/hermes-agent/pull/43)) — @grp06
- Codex OAuth 视觉支持 + 多模态内容适配器
- 针对实时 API 验证 `/model`，而非硬编码列表
- 自托管 Firecrawl 支持 ([#460](https://github.com/NousResearch/hermes-agent/pull/460)) — @caentzminger
- Kimi Code API 支持 ([#635](https://github.com/NousResearch/hermes-agent/pull/635)) — @christomitov
- MiniMax 模型 ID 更新 ([#473](https://github.com/NousResearch/hermes-agent/pull/473)) — @tars90percent
- OpenRouter 提供商路由配置 (provider_preferences)
- Nous 凭证在 401 错误时刷新 ([#571](https://github.com/NousResearch/hermes-agent/pull/571), [#269](https://github.com/NousResearch/hermes-agent/pull/269)) — @rewbs
- z.ai/GLM、Kimi/Moonshot、MiniMax、Azure OpenAI 作为一级提供商
- 将 `/model` 和 `/provider` 统一为单一视图

### 代理循环与对话
- 简单的备用模型以提高提供商弹性 ([#740](https://github.com/NousResearch/hermes-agent/pull/740))
- 父代理与子代理委托间的共享迭代预算
- 通过工具结果注入实现迭代预算压力
- 可配置的子代理提供商/模型，具有完整的凭证解析
- 通过压缩而非中止来处理 413 负载过大错误 ([#153](https://github.com/NousResearch/hermes-agent/pull/153)) — @tekelala
- 压缩后使用重建的负载重试 ([#616](https://github.com/NousResearch/hermes-agent/pull/616)) — @tripledoublev
- 自动压缩病态性大型网关会话 ([#628](https://github.com/NousResearch/hermes-agent/issues/628))
- 工具调用修复中间件 — 自动小写化和无效工具处理器
- 推理力度配置和 `/reasoning` 命令 ([#921](https://github.com/NousResearch/hermes-agent/pull/921))
- 在上下文压缩后检测并阻止文件重读/搜索循环 ([#705](https://github.com/NousResearch/hermes-agent/pull/705)) — @0xbyt4

### 会话与记忆
- 会话命名，包含唯一标题、自动血统、丰富列表和按名称恢复 ([#720](https://github.com/NousResearch/hermes-agent/pull/720))
- 交互式会话浏览器，带搜索过滤 ([#733](https://github.com/NousResearch/hermes-agent/pull/733))
- 恢复会话时显示之前的消息 ([#734](https://github.com/NousResearch/hermes-agent/pull/734))
- Honcho AI 原生跨会话用户建模 ([#38](https://github.com/NousResearch/hermes-agent/pull/38)) — @erosika
- 会话过期时的主动异步记忆刷新
- 带有持久缓存和横幅显示的智能上下文长度探测
- 用于在网关中切换命名会话的 `/resume` 命令
- 消息平台的会话重置策略

---

## 📱 消息平台（网关）

### Telegram
- 原生文件附件：send_document + send_video
- 文档文件处理，支持 PDF、文本和 Office 文件 — @tekelala
- 论坛主题会话隔离 ([#766](https://github.com/NousResearch/hermes-agent/pull/766)) — @spanishflu-est1918
- 通过 MEDIA: 协议分享浏览器截图 ([#657](https://github.com/NousResearch/hermes-agent/pull/657))
- 位置支持用于附近查找技能
- TTS 语音消息累积修复 ([#176](https://github.com/NousResearch/hermes-agent/pull/176)) — @Bartok9
- 改进的错误处理和日志记录 ([#763](https://github.com/NousResearch/hermes-agent/pull/763)) — @aydnOktay
- 斜体正则换行修复 + 43 项格式测试 ([#204](https://github.com/NousResearch/hermes-agent/pull/204)) — @0xbyt4

### Discord
- 频道主题包含在会话上下文中 ([#248](https://github.com/NousResearch/hermes-agent/pull/248)) — @Bartok9
- DISCORD_ALLOW_BOTS 配置用于机器人消息过滤 ([#758](https://github.com/NousResearch/hermes-agent/pull/758))
- 文档和视频支持 ([#784](https://github.com/NousResearch/hermes-agent/pull/784))
- 改进的错误处理和日志记录 ([#761](https://github.com/NousResearch/hermes-agent/pull/761)) — @aydnOktay

### Slack
- App_mention 404 修复 + 文档/视频支持 ([#784](https://github.com/NousResearch/hermes-agent/pull/784))
- 结构化日志替代 print 语句 — @aydnOktay

### WhatsApp
- 原生媒体发送 — 图片、视频、文档 ([#292](https://github.com/NousResearch/hermes-agent/pull/292)) — @satelerd
- 多用户会话隔离 ([#75](https://github.com/NousResearch/hermes-agent/pull/75)) — @satelerd
- 跨平台端口清理替代仅限 Linux 的 fuser ([#433](https://github.com/NousResearch/hermes-agent/pull/433)) — @Farukest
- DM 中断键不匹配修复 ([#350](https://github.com/NousResearch/hermes-agent/pull/350)) — @Farukest

### Signal
- 通过 signal-cli-rest-api 实现完整的 Signal 消息网关 ([#405](https://github.com/NousResearch/hermes-agent/issues/405))
- 消息事件中的媒体 URL 支持 ([#871](https://github.com/NousResearch/hermes-agent/pull/871))

### Email（IMAP/SMTP）
- 新的电子邮件网关平台 — @0xbyt4

### Home Assistant
- REST 工具 + WebSocket 网关集成 ([#184](https://github.com/NousResearch/hermes-agent/pull/184)) — @0xbyt4
- 服务发现和增强的设置
- 工具集映射修复 ([#538](https://github.com/NousResearch/hermes-agent/pull/538)) — @Himess

### 网关核心
- 向用户暴露子代理工具调用和思考过程 ([#186](https://github.com/NousResearch/hermes-agent/pull/186)) — @cutepawss
- 可配置的后台进程监视器通知 ([#840](https://github.com/NousResearch/hermes-agent/pull/840))
- 用于 Telegram/Discord/Slack 的 `edit_message()` 及备用方案
- `/compress`、`/usage`、`/update` 斜杠命令
- 消除网关会话中 3 倍的 SQLite 消息重复 ([#873](https://github.com/NousResearch/hermes-agent/pull/873))
- 稳定网关轮次间的系统提示以获取缓存命中 ([#754](https://github.com/NousResearch/hermes-agent/pull/754))
- 网关退出时关闭 MCP 服务器 ([#796](https://github.com/NousResearch/hermes-agent/pull/796)) — @0xbyt4
- 将 session_db 传递给 AIAgent，修复 session_search 错误 ([#108](https://github.com/NousResearch/hermes-agent/pull/108)) — @Bartok9
- 在 /retry、/undo 中持久化脚本更改；修复 /reset 属性 ([#217](https://github.com/NousResearch/hermes-agent/pull/217)) — @Farukest
- UTF-8 编码修复防止 Windows 崩溃 ([#369](https://github.com/NousResearch/hermes-agent/pull/369)) — @ch3ronsa

---

## 🖥️ CLI 与用户体验

### 交互式 CLI
- 数据驱动的皮肤/主题引擎 — 7 个内置皮肤（default、ares、mono、slate、poseidon、sisyphus、charizard）+ 自定义 YAML 皮肤
- `/personality` 命令，支持自定义个性 + 禁用选项 ([#773](https://github.com/NousResearch/hermes-agent/pull/773)) — @teyrebaz33
- 用户自定义快捷命令，绕过代理循环 ([#746](https://github.com/NousResearch/hermes-agent/pull/746)) — @teyrebaz33
- `/reasoning` 命令用于力度级别和显示切换 ([#921](https://github.com/NousResearch/hermes-agent/pull/921))
- `/verbose` 斜杠命令在运行时切换调试模式 ([#94](https://github.com/NousResearch/hermes-agent/pull/94)) — @cesareth
- `/insights` 命令 — 使用分析、费用估算和活动模式 ([#552](https://github.com/NousResearch/hermes-agent/pull/552))
- `/background` 命令用于管理后台进程
- `/help` 按命令分类格式化
- 完成时响铃 — 代理完成时终端响铃 ([#738](https://github.com/NousResearch/hermes-agent/pull/738))
- 上/下箭头历史导航
- 剪贴板图片粘贴（Alt+V / Ctrl+V）
- 慢速斜杠命令的加载指示器 ([#882](https://github.com/NousResearch/hermes-agent/pull/882))
- 修复 patch_stdout 下的加载动画闪烁 ([#91](https://github.com/NousResearch/hermes-agent/pull/91)) — @0xbyt4
- `--quiet/-Q` 标志用于编程式单查询模式
- `--fuck-it-ship-it` 标志绕过所有审批提示 ([#724](https://github.com/NousResearch/hermes-agent/pull/724)) — @dmahan93
- 工具摘要标志 ([#767](https://github.com/NousResearch/hermes-agent/pull/767)) — @luisv-1
- SSH 上的终端闪烁修复 ([#284](https://github.com/NousResearch/hermes-agent/pull/284)) — @ygd58
- 多行粘贴检测修复 ([#84](https://github.com/NousResearch/hermes-agent/pull/84)) — @0xbyt4

### 设置与配置
- 模块化设置向导，带有分节子命令和工具优先的用户体验
- 容器资源配置提示
- 必需二进制文件的后端验证
- 配置迁移系统（当前 v7）
- API 密钥正确路由到 .env 而非 config.yaml ([#469](https://github.com/NousResearch/hermes-agent/pull/469)) — @ygd58
- .env 的原子写入防止崩溃时 API 密钥丢失 ([#954](https://github.com/NousResearch/hermes-agent/pull/954))
- `hermes tools` — 按平台启用/禁用工具，带有 curses UI
- `hermes doctor` 用于检查所有已配置提供商的健康状态
- `hermes update` 带有网关服务的自动重启
- 在 CLI 横幅中显示可用更新通知
- 多个命名的自定义提供商
- 改进 PATH 设置的 Shell 配置检测 ([#317](https://github.com/NousResearch/hermes-agent/pull/317)) — @mehmetkr-31
- 一致的 HERMES_HOME 和 .env 路径解析 ([#51](https://github.com/NousResearch/hermes-agent/pull/51), [#48](https://github.com/NousResearch/hermes-agent/pull/48)) — @deankerr
- macOS 上的 Docker 后端修复 + Nous Portal 的子代理认证 ([#46](https://github.com/NousResearch/hermes-agent/pull/46)) — @rsavitt

---

## 🔧 工具系统

### MCP（模型上下文协议）
- 原生 MCP 客户端，支持 stdio + HTTP 传输 ([#291](https://github.com/NousResearch/hermes-agent/pull/291) — @0xbyt4, [#301](https://github.com/NousResearch/hermes-agent/pull/301))
- 采样支持 — 服务器发起的 LLM 请求 ([#753](https://github.com/NousResearch/hermes-agent/pull/753))
- 资源和提示发现
- 自动重连和安全加固
- 横幅集成、`/reload-mcp` 命令
- `hermes tools` UI 集成

### 浏览器
- 本地浏览器后端 — 零成本无头 Chromium（无需 Browserbase）
- 控制台/错误工具、带注释截图、自动录制、dogfood QA 技能 ([#745](https://github.com/NousResearch/hermes-agent/pull/745))
- 通过 MEDIA: 在所有消息平台上分享截图 ([#657](https://github.com/NousResearch/hermes-agent/pull/657))

### 终端与执行
- `execute_code` 沙箱，包含 json_parse、shell_quote、retry 辅助工具
- Docker：自定义卷挂载 ([#158](https://github.com/NousResearch/hermes-agent/pull/158)) — @Indelwin
- Daytona 云沙箱后端 ([#451](https://github.com/NousResearch/hermes-agent/pull/451)) — @rovle
- SSH 后端修复 ([#59](https://github.com/NousResearch/hermes-agent/pull/59)) — @deankerr
- Shell 噪音过滤和登录 shell 执行以保持环境一致性
- execute_code 标准输出溢出的首尾截断
- 可配置的后台进程通知模式

### 文件操作
- 文件系统检查点和 `/rollback` 命令 ([#824](https://github.com/NousResearch/hermes-agent/pull/824))
- 结构化工具结果提示（下一步操作指导）用于 patch 和 search_files ([#722](https://github.com/NousResearch/hermes-agent/issues/722))
- Docker 卷传递到沙箱容器配置 ([#687](https://github.com/NousResearch/hermes-agent/pull/687)) — @manuelschipper

---

## 🧩 技能生态系统

### 技能系统
- 按平台启用/禁用技能 ([#743](https://github.com/NousResearch/hermes-agent/pull/743)) — @teyrebaz33
- 基于工具可用性的条件技能激活 ([#785](https://github.com/NousResearch/hermes-agent/pull/785)) — @teyrebaz33
- 技能前置条件 — 隐藏未满足依赖的技能 ([#659](https://github.com/NousResearch/hermes-agent/pull/659)) — @kshitijk4poor
- 可选技能 — 随附但默认不激活
- `hermes skills browse` — 分页浏览中心
- 技能子分类组织
- 按平台条件加载技能
- 技能文件原子写入 ([#551](https://github.com/NousResearch/hermes-agent/pull/551)) — @aydnOktay
- 技能同步数据丢失防护 ([#563](https://github.com/NousResearch/hermes-agent/pull/563)) — @0xbyt4
- CLI 和网关的动态技能斜杠命令

### 新技能（精选）
- **ASCII 艺术** — pyfiglet（571 种字体）、cowsay、图片转 ASCII ([#209](https://github.com/NousResearch/hermes-agent/pull/209)) — @0xbyt4
- **ASCII 视频** — 完整的生产流水线 ([#854](https://github.com/NousResearch/hermes-agent/pull/854)) — @SHL0MS
- **DuckDuckGo 搜索** — Firecrawl 备用方案 ([#267](https://github.com/NousResearch/hermes-agent/pull/267)) — @gamedevCloudy; DDGS API 扩展 ([#598](https://github.com/NousResearch/hermes-agent/pull/598)) — @areu01or00
- **Solana 区块链** — 钱包余额、USD 定价、代币名称 ([#212](https://github.com/NousResearch/hermes-agent/pull/212)) — @gizdusum
- **AgentMail** — 代理拥有的电子邮件收件箱 ([#330](https://github.com/NousResearch/hermes-agent/pull/330)) — @teyrebaz33
- **Polymarket** — 预测市场数据（只读）([#629](https://github.com/NousResearch/hermes-agent/pull/629))
- **OpenClaw 迁移** — 官方迁移工具 ([#570](https://github.com/NousResearch/hermes-agent/pull/570)) — @unmodeled-tyler
- **域名情报** — 被动侦查：子域名、SSL、WHOIS、DNS ([#136](https://github.com/NousResearch/hermes-agent/pull/136)) — @FurkanL0
- **超能力** — 软件开发技能 ([#137](https://github.com/NousResearch/hermes-agent/pull/137)) — @kaos35
- **Hermes-Atropos** — RL 环境开发技能 ([#815](https://github.com/NousResearch/hermes-agent/pull/815))
- 此外还有：arXiv 搜索、OCR/文档、Excalidraw 图表、YouTube 字幕、GIF 搜索、宝可梦玩家、Minecraft 模组服务器、OpenHue（飞利浦 Hue）、Google Workspace、Notion、PowerPoint、Obsidian、附近查找以及 40+ 个 MLOps 技能

---

## 🔒 安全与可靠性

### 安全加固
- skill_view 中的路径遍历修复 — 阻止读取任意文件 ([#220](https://github.com/NousResearch/hermes-agent/issues/220)) — @Farukest
- sudo 密码管道中的 Shell 注入防护 ([#65](https://github.com/NousResearch/hermes-agent/pull/65)) — @leonsgithub
- 危险命令检测：多行绕过修复 ([#233](https://github.com/NousResearch/hermes-agent/pull/233)) — @Farukest; tee/进程替换模式 ([#280](https://github.com/NousResearch/hermes-agent/pull/280)) — @dogiladeveloper
- skills_guard 中的符号链接边界检查修复 ([#386](https://github.com/NousResearch/hermes-agent/pull/386)) — @Farukest
- macOS 上写入拒绝列表中的符号链接绕过修复 ([#61](https://github.com/NousResearch/hermes-agent/pull/61)) — @0xbyt4
- 多词提示注入绕过防护 ([#192](https://github.com/NousResearch/hermes-agent/pull/192)) — @0xbyt4
- 定时任务提示注入扫描器绕过修复 ([#63](https://github.com/NousResearch/hermes-agent/pull/63)) — @0xbyt4
- 敏感文件强制 0600/0700 文件权限 ([#757](https://github.com/NousResearch/hermes-agent/pull/757))
- .env 文件权限限制为仅所有者 ([#529](https://github.com/NousResearch/hermes-agent/pull/529)) — @Himess
- `--force` 标志被正确阻止覆盖危险判定 ([#388](https://github.com/NousResearch/hermes-agent/pull/388)) — @Farukest
- FTS5 查询清理 + 数据库连接泄漏修复 ([#565](https://github.com/NousResearch/hermes-agent/pull/565)) — @0xbyt4
- 扩展密钥编辑模式 + 配置开关以禁用
- 内存中的永久允许列表防止数据泄露 ([#600](https://github.com/NousResearch/hermes-agent/pull/600)) — @alireza78a

### 原子写入（数据丢失防护）
- sessions.json ([#611](https://github.com/NousResearch/hermes-agent/pull/611)) — @alireza78a
- 定时任务 ([#146](https://github.com/NousResearch/hermes-agent/pull/146)) — @alireza78a
- .env 配置 ([#954](https://github.com/NousResearch/hermes-agent/pull/954))
- 进程检查点 ([#298](https://github.com/NousResearch/hermes-agent/pull/298)) — @aydnOktay
- 批处理运行器 ([#297](https://github.com/NousResearch/hermes-agent/pull/297)) — @aydnOktay
- 技能文件 ([#551](https://github.com/NousResearch/hermes-agent/pull/551)) — @aydnOktay

### 可靠性
- 为 systemd/无头环境中的所有 print() 添加 OSError 防护 ([#963](https://github.com/NousResearch/hermes-agent/pull/963))
- 在 run_conversation 开始时重置所有重试计数器 ([#607](https://github.com/NousResearch/hermes-agent/pull/607)) — @0xbyt4
- 审批回调超时时返回拒绝而非 None ([#603](https://github.com/NousResearch/hermes-agent/pull/603)) — @0xbyt4
- 修复代码库中的 None 消息内容崩溃 ([#277](https://github.com/NousResearch/hermes-agent/pull/277))
- 修复本地 LLM 后端的上下文溢出崩溃 ([#403](https://github.com/NousResearch/hermes-agent/pull/403)) — @ch3ronsa
- 防止 `_flush_sentinel` 泄漏到外部 API ([#227](https://github.com/NousResearch/hermes-agent/pull/227)) — @Farukest
- 防止调用者中的 conversation_history 变异 ([#229](https://github.com/NousResearch/hermes-agent/pull/229)) — @Farukest
- 修复 systemd 重启循环 ([#614](https://github.com/NousResearch/hermes-agent/pull/614)) — @voidborne-d
- 关闭文件句柄和套接字以防止 fd 泄漏 ([#568](https://github.com/NousResearch/hermes-agent/pull/568) — @alireza78a, [#296](https://github.com/NousResearch/hermes-agent/pull/296) — @alireza78a, [#709](https://github.com/NousResearch/hermes-agent/pull/709) — @memosr)
- 防止剪贴板 PNG 转换中的数据丢失 ([#602](https://github.com/NousResearch/hermes-agent/pull/602)) — @0xbyt4
- 消除终端输出中的 Shell 噪音 ([#293](https://github.com/NousResearch/hermes-agent/pull/293)) — @0xbyt4
- 提示、定时任务和 execute_code 的时区感知 now() ([#309](https://github.com/NousResearch/hermes-agent/pull/309)) — @areu01or00

### Windows 兼容性
- 保护仅限 POSIX 的进程函数 ([#219](https://github.com/NousResearch/hermes-agent/pull/219)) — @Farukest
- 通过 Git Bash + 基于 ZIP 的更新备用方案实现 Windows 原生支持
- pywinpty 用于 PTY 支持 ([#457](https://github.com/NousResearch/hermes-agent/pull/457)) — @shitcoinsherpa
- 所有配置/数据文件 I/O 的显式 UTF-8 编码 ([#458](https://github.com/NousResearch/hermes-agent/pull/458)) — @shitcoinsherpa
- Windows 兼容的路径处理 ([#354](https://github.com/NousResearch/hermes-agent/pull/354), [#390](https://github.com/NousResearch/hermes-agent/pull/390)) — @Farukest
- 驱动器号路径的基于正则的搜索输出解析 ([#533](https://github.com/NousResearch/hermes-agent/pull/533)) — @Himess
- Windows 的认证存储文件锁 ([#455](https://github.com/NousResearch/hermes-agent/pull/455)) — @shitcoinsherpa

---

## 🐛 值得注意的 Bug 修复

- 修复 DeepSeek V3 工具调用解析器静默丢弃多行 JSON 参数 ([#444](https://github.com/NousResearch/hermes-agent/pull/444)) — @PercyDikec
- 修复网关脚本因偏移不匹配每轮丢失 1 条消息 ([#395](https://github.com/NousResearch/hermes-agent/pull/395)) — @PercyDikec
- 修复 /retry 命令静默丢弃代理的最终响应 ([#441](https://github.com/NousResearch/hermes-agent/pull/441)) — @PercyDikec
- 修复最大迭代重试在 think-block 剥离后返回空字符串 ([#438](https://github.com/NousResearch/hermes-agent/pull/438)) — @PercyDikec
- 修复最大迭代重试使用硬编码的 max_tokens ([#436](https://github.com/NousResearch/hermes-agent/pull/436)) — @Farukest
- 修复 Codex 状态字典键不匹配 ([#448](https://github.com/NousResearch/hermes-agent/pull/448)) 和可见性过滤器 ([#446](https://github.com/NousResearch/hermes-agent/pull/446)) — @PercyDikec
- 从最终面向用户的响应中剥离 \<think\> 块 ([#174](https://github.com/NousResearch/hermes-agent/pull/174)) — @Bartok9
- 修复 \<think\> 块正则在模型字面讨论标签时剥离可见内容 ([#786](https://github.com/NousResearch/hermes-agent/issues/786))
- 修复 Mistral 422 错误，由助手消息中残留的 finish_reason 引起 ([#253](https://github.com/NousResearch/hermes-agent/pull/253)) — @Sertug17
- 修复所有代码路径中的 OPENROUTER_API_KEY 解析顺序 ([#295](https://github.com/NousResearch/hermes-agent/pull/295)) — @0xbyt4
- 修复 OPENAI_BASE_URL API 密钥优先级 ([#420](https://github.com/NousResearch/hermes-agent/pull/420)) — @manuelschipper
- 修复 Anthropic "prompt is too long" 400 错误未被检测为上下文长度错误 ([#813](https://github.com/NousResearch/hermes-agent/issues/813))
- 修复 SQLite 会话脚本累积重复消息 — 3-4 倍 token 膨胀 ([#860](https://github.com/NousResearch/hermes-agent/issues/860))
- 修复设置向导在首次安装时跳过 API 密钥提示 ([#748](https://github.com/NousResearch/hermes-agent/pull/748))
- 修复设置向导为 Nous Portal 显示 OpenRouter 模型列表 ([#575](https://github.com/NousResearch/hermes-agent/pull/575)) — @PercyDikec
- 修复通过 hermes model 切换时提供商选择未持久化 ([#881](https://github.com/NousResearch/hermes-agent/pull/881))
- 修复 macOS 上 docker 不在 PATH 中时 Docker 后端失败 ([#889](https://github.com/NousResearch/hermes-agent/pull/889))
- 修复 ClawHub 技能中心适配器的 API 端点变更 ([#286](https://github.com/NousResearch/hermes-agent/pull/286)) — @BP602
- 修复 API 密钥存在时 Honcho 自动启用 ([#243](https://github.com/NousResearch/hermes-agent/pull/243)) — @Bartok9
- 修复 Python 3.11+ 上重复 'skills' 子解析器崩溃 ([#898](https://github.com/NousResearch/hermes-agent/issues/898))
- 修复内容包含节号时的记忆工具条目解析 ([#162](https://github.com/NousResearch/hermes-agent/pull/162)) — @aydnOktay
- 修复管道安装在交互提示失败时静默中止 ([#72](https://github.com/NousResearch/hermes-agent/pull/72)) — @cutepawss
- 修复递归删除检测中的误报 ([#68](https://github.com/NousResearch/hermes-agent/pull/68)) — @cutepawss
- 修复代码库中的 Ruff lint 警告 ([#608](https://github.com/NousResearch/hermes-agent/pull/608)) — @JackTheGit
- 修复 Anthropic 原生 base URL 快速失败 ([#173](https://github.com/NousResearch/hermes-agent/pull/173)) — @adavyas
- 修复 install.sh 在移动 Node.js 目录前创建 ~/.hermes ([#53](https://github.com/NousResearch/hermes-agent/pull/53)) — @JoshuaMart
- 修复 Ctrl+C 时 atexit 清理期间的 SystemExit 回溯 ([#55](https://github.com/NousResearch/hermes-agent/pull/55)) — @bierlingm
- 恢复缺失的 MIT 许可证文件 ([#620](https://github.com/NousResearch/hermes-agent/pull/620)) — @stablegenius49

---

## 🧪 测试

- **3,289 项测试**，涵盖代理、网关、工具、定时任务和 CLI
- 使用 pytest-xdist 并行化测试套件 ([#802](https://github.com/NousResearch/hermes-agent/pull/802)) — @OutThisLife
- 单元测试批次 1：8 个核心模块 ([#60](https://github.com/NousResearch/hermes-agent/pull/60)) — @0xbyt4
- 单元测试批次 2：另外 8 个模块 ([#62](https://github.com/NousResearch/hermes-agent/pull/62)) — @0xbyt4
- 单元测试批次 3：8 个未测试模块 ([#191](https://github.com/NousResearch/hermes-agent/pull/191)) — @0xbyt4
- 单元测试批次 4：5 个安全/逻辑关键模块 ([#193](https://github.com/NousResearch/hermes-agent/pull/193)) — @0xbyt4
- AIAgent (run_agent.py) 单元测试 ([#67](https://github.com/NousResearch/hermes-agent/pull/67)) — @0xbyt4
- 轨迹压缩器测试 ([#203](https://github.com/NousResearch/hermes-agent/pull/203)) — @0xbyt4
- Clarify 工具测试 ([#121](https://github.com/NousResearch/hermes-agent/pull/121)) — @Bartok9
- Telegram 格式测试 — 43 项斜体/粗体/代码渲染测试 ([#204](https://github.com/NousResearch/hermes-agent/pull/204)) — @0xbyt4
- 视觉工具类型提示 + 42 项测试 ([#792](https://github.com/NousResearch/hermes-agent/pull/792))
- 压缩器工具调用边界回归测试 ([#648](https://github.com/NousResearch/hermes-agent/pull/648)) — @intertwine
- 测试结构重组 ([#34](https://github.com/NousResearch/hermes-agent/pull/34)) — @0xbyt4
- Shell 噪音消除 + 修复 36 项测试失败 ([#293](https://github.com/NousResearch/hermes-agent/pull/293)) — @0xbyt4

---

## 🔬 RL 与评估环境

- WebResearchEnv — 多步骤网络研究 RL 环境 ([#434](https://github.com/NousResearch/hermes-agent/pull/434)) — @jackx707
- Modal 沙箱并发限制以避免死锁 ([#621](https://github.com/NousResearch/hermes-agent/pull/621)) — @voteblake
- Hermes-atropos-environments 捆绑技能 ([#815](https://github.com/NousResearch/hermes-agent/pull/815))
- 用于评估的本地 vLLM 实例支持 — @dmahan93
- YC-Bench 长时程代理基准环境
- OpenThoughts-TBLite 评估环境和脚本

---

## 📚 文档

- 完整的文档网站（Docusaurus），37+ 页
- Telegram、Discord、Slack、WhatsApp、Signal、Email 的全面平台设置指南
- AGENTS.md — AI 编码助手开发指南
- CONTRIBUTING.md ([#117](https://github.com/NousResearch/hermes-agent/pull/117)) — @Bartok9
- 斜杠命令参考 ([#142](https://github.com/NousResearch/hermes-agent/pull/142)) — @Bartok9
- 全面的 AGENTS.md 准确性审计 ([#732](https://github.com/NousResearch/hermes-agent/pull/732))
- 皮肤/主题系统文档
- MCP 文档和示例
- 文档准确性审计 — 35+ 项修正
- 文档错别字修复 ([#825](https://github.com/NousResearch/hermes-agent/pull/825), [#439](https://github.com/NousResearch/hermes-agent/pull/439)) — @JackTheGit
- CLI 配置优先级和术语标准化 ([#166](https://github.com/NousResearch/hermes-agent/pull/166), [#167](https://github.com/NousResearch/hermes-agent/pull/167), [#168](https://github.com/NousResearch/hermes-agent/pull/168)) — @Jr-kenny
- Telegram 令牌正则文档 ([#713](https://github.com/NousResearch/hermes-agent/pull/713)) — @VolodymyrBg

---

## 👥 贡献者

感谢 63 位贡献者使本次发布成为可能！在短短两周多的时间里，Hermes Agent 社区齐聚一堂，完成了非凡的工作量。

### 核心
- **@teknium1** — 43 个 PR：项目负责人、核心架构、提供商路由、会话、技能、CLI、文档

### 顶级社区贡献者
- **@0xbyt4** — 40 个 PR：MCP 客户端、Home Assistant、安全修复（符号链接、提示注入、定时任务）、广泛的测试覆盖（6 个批次）、ascii-art 技能、Shell 噪音消除、技能同步、Telegram 格式化等数十项
- **@Farukest** — 16 个 PR：安全加固（路径遍历、危险命令检测、符号链接边界）、Windows 兼容性（POSIX 防护、路径处理）、WhatsApp 修复、最大迭代重试、网关修复
- **@aydnOktay** — 11 个 PR：原子写入（进程检查点、批处理运行器、技能文件）、Telegram、Discord、代码执行、转录、TTS 和技能的错误处理改进
- **@Bartok9** — 9 个 PR：CONTRIBUTING.md、斜杠命令参考、Discord 频道主题、think-block 剥离、TTS 修复、Honcho 修复、会话计数修复、clarify 测试
- **@PercyDikec** — 7 个 PR：DeepSeek V3 解析器修复、/retry 响应丢弃、网关脚本偏移、Codex 状态/可见性、最大迭代重试、设置向导修复
- **@teyrebaz33** — 5 个 PR：技能启用/禁用系统、快捷命令、个性自定义、条件技能激活
- **@alireza78a** — 5 个 PR：原子写入（定时任务、会话）、fd 泄漏防护、安全允许列表、代码执行套接字清理
- **@shitcoinsherpa** — 3 个 PR：Windows 支持（pywinpty、UTF-8 编码、认证存储锁）
- **@Himess** — 3 个 PR：Cron/HomeAssistant/Daytona 修复、Windows 驱动器号解析、.env 权限
- **@satelerd** — 2 个 PR：WhatsApp 原生媒体、多用户会话隔离
- **@rovle** — 1 个 PR：Daytona 云沙箱后端（4 个提交）
- **@erosika** — 1 个 PR：Honcho AI 原生记忆集成
- **@dmahan93** — 1 个 PR：--fuck-it-ship-it 标志 + RL 环境工作
- **@SHL0MS** — 1 个 PR：ASCII 视频技能

### 所有贡献者
@0xbyt4, @BP602, @Bartok9, @Farukest, @FurkanL0, @Himess, @Indelwin, @JackTheGit, @JoshuaMart, @Jr-kenny, @OutThisLife, @PercyDikec, @SHL0MS, @Sertug17, @VencentSoliman, @VolodymyrBg, @adavyas, @alireza78a, @areu01or00, @aydnOktay, @batuhankocyigit, @bierlingm, @caentzminger, @cesareth, @ch3ronsa, @christomitov, @cutepawss, @deankerr, @dmahan93, @dogiladeveloper, @dragonkhoi, @erosika, @gamedevCloudy, @gizdusum, @grp06, @intertwine, @jackx707, @jdblackstar, @johnh4098, @kaos35, @kshitijk4poor, @leonsgithub, @luisv-1, @manuelschipper, @mehmetkr-31, @memosr, @PeterFile, @rewbs, @rovle, @rsavitt, @satelerd, @spanishflu-est1918, @stablegenius49, @tars90percent, @tekelala, @teknium1, @teyrebaz33, @tripledoublev, @unmodeled-tyler, @voidborne-d, @voteblake, @ygd58

---

**完整变更日志**：[v0.1.0...v2026.3.12](https://github.com/NousResearch/hermes-agent/compare/v0.1.0...v2026.3.12)
