# Hermes Agent v0.3.0 (v2026.3.17)

**发布日期：** 2026年3月17日

> 流式传输、插件和提供商发布版 — 统一的实时 token 传递、一流的插件架构、重建的提供商系统（含 Vercel AI 网关）、原生 Anthropic 提供商、智能审批、实时 Chrome CDP 浏览器连接、ACP IDE 集成、Honcho 记忆、语音模式、持久 Shell，以及 50+ 个跨所有平台的 Bug 修复。

---

## ✨ 亮点

- **统一流式传输基础设施** — CLI 和所有网关平台中的实时逐 token 传递。响应在生成时即以流式传输，而非作为整块到达。([#1538](https://github.com/NousResearch/hermes-agent/pull/1538))

- **一流的插件架构** — 将 Python 文件放入 `~/.hermes/plugins/` 即可使用自定义工具、命令和钩子扩展 Hermes。无需 fork。([#1544](https://github.com/NousResearch/hermes-agent/pull/1544), [#1555](https://github.com/NousResearch/hermes-agent/pull/1555))

- **原生 Anthropic 提供商** — 直接 Anthropic API 调用，支持 Claude Code 凭证自动发现、OAuth PKCE 流程和原生提示缓存。无需 OpenRouter 中间商。([#1097](https://github.com/NousResearch/hermes-agent/pull/1097))

- **智能审批 + /stop 命令** — 受 Codex 启发的审批系统，可学习哪些命令是安全的并记住您的偏好。`/stop` 可立即终止当前代理运行。([#1543](https://github.com/NousResearch/hermes-agent/pull/1543))

- **Honcho 记忆集成** — 异步记忆写入、可配置的回忆模式、会话标题集成以及网关模式下的多用户隔离。由 @erosika 开发。([#736](https://github.com/NousResearch/hermes-agent/pull/736))

- **语音模式** — CLI 中的按键说话、Telegram/Discord 中的语音消息、Discord 语音频道支持，以及通过 faster-whisper 的本地 Whisper 转录。([#1299](https://github.com/NousResearch/hermes-agent/pull/1299), [#1185](https://github.com/NousResearch/hermes-agent/pull/1185), [#1429](https://github.com/NousResearch/hermes-agent/pull/1429))

- **并发工具执行** — 多个独立的工具调用现在通过 ThreadPoolExecutor 并行运行，显著减少多工具轮次的延迟。([#1152](https://github.com/NousResearch/hermes-agent/pull/1152))

- **PII 脱敏** — 启用 `privacy.redact_pii` 后，个人身份信息在发送上下文给 LLM 提供商之前会被自动清除。([#1542](https://github.com/NousResearch/hermes-agent/pull/1542))

- **`/browser connect` 通过 CDP** — 通过 Chrome DevTools Protocol 将浏览器工具连接到正在运行的 Chrome 实例。调试、检查并与已打开的页面交互。([#1549](https://github.com/NousResearch/hermes-agent/pull/1549))

- **Vercel AI 网关提供商** — 通过 Vercel 的 AI 网关路由 Hermes，以访问其模型目录和基础设施。([#1628](https://github.com/NousResearch/hermes-agent/pull/1628))

- **集中式提供商路由** — 重建的提供商系统，包含 `call_llm` API、统一的 `/model` 命令、模型切换时自动检测提供商，以及辅助/委托客户端的直接端点覆盖。([#1003](https://github.com/NousResearch/hermes-agent/pull/1003), [#1506](https://github.com/NousResearch/hermes-agent/pull/1506), [#1375](https://github.com/NousResearch/hermes-agent/pull/1375))

- **ACP 服务器（IDE 集成）** — VS Code、Zed 和 JetBrains 现在可以作为代理后端连接到 Hermes，完全支持斜杠命令。([#1254](https://github.com/NousResearch/hermes-agent/pull/1254), [#1532](https://github.com/NousResearch/hermes-agent/pull/1532))

- **持久 Shell 模式** — 本地和 SSH 终端后端可以跨工具调用维持 Shell 状态 — cd、环境变量和别名持久保留。由 @alt-glitch 开发。([#1067](https://github.com/NousResearch/hermes-agent/pull/1067), [#1483](https://github.com/NousResearch/hermes-agent/pull/1483))

- **代理在线策略蒸馏（OPD）** — 新的 RL 训练环境，用于蒸馏代理策略，扩展 Atropos 训练生态系统。([#1149](https://github.com/NousResearch/hermes-agent/pull/1149))

---

## 🏗️ 核心代理与架构

### 提供商与模型支持
- **集中式提供商路由**，包含 `call_llm` API 和统一的 `/model` 命令 — 无缝切换模型和提供商 ([#1003](https://github.com/NousResearch/hermes-agent/pull/1003))
- **Vercel AI 网关**提供商支持 ([#1628](https://github.com/NousResearch/hermes-agent/pull/1628))
- **自动检测提供商**，通过 `/model` 切换模型时生效 ([#1506](https://github.com/NousResearch/hermes-agent/pull/1506))
- **直接端点覆盖**用于辅助和委托客户端 — 将视觉/子代理调用指向特定端点 ([#1375](https://github.com/NousResearch/hermes-agent/pull/1375))
- **原生 Anthropic 辅助视觉** — 使用 Claude 的原生视觉 API 而非通过 OpenAI 兼容端点路由 ([#1377](https://github.com/NousResearch/hermes-agent/pull/1377))
- Anthropic OAuth 流程改进 — 自动运行 `claude setup-token`、重新认证、PKCE 状态持久化、身份指纹 ([#1132](https://github.com/NousResearch/hermes-agent/pull/1132), [#1360](https://github.com/NousResearch/hermes-agent/pull/1360), [#1396](https://github.com/NousResearch/hermes-agent/pull/1396), [#1597](https://github.com/NousResearch/hermes-agent/pull/1597))
- 修复 Claude 4.6 模型无 `budget_tokens` 时的自适应思考 — 由 @ASRagab 开发 ([#1128](https://github.com/NousResearch/hermes-agent/pull/1128))
- 修复通过适配器的 Anthropic 缓存标记 — 由 @brandtcormorant 开发 ([#1216](https://github.com/NousResearch/hermes-agent/pull/1216))
- 重试 Anthropic 429/529 错误并向用户显示详情 — 由 @0xbyt4 开发 ([#1585](https://github.com/NousResearch/hermes-agent/pull/1585))
- 修复 Anthropic 适配器 max_tokens、备用崩溃、代理 base_url — 由 @0xbyt4 开发 ([#1121](https://github.com/NousResearch/hermes-agent/pull/1121))
- 修复 DeepSeek V3 解析器丢弃多个并行工具调用 — 由 @mr-emmett-one 开发 ([#1365](https://github.com/NousResearch/hermes-agent/pull/1365), [#1300](https://github.com/NousResearch/hermes-agent/pull/1300))
- 接受未列出的模型并显示警告而非拒绝 ([#1047](https://github.com/NousResearch/hermes-agent/pull/1047), [#1102](https://github.com/NousResearch/hermes-agent/pull/1102))
- 跳过不支持的 OpenRouter 模型的推理参数 ([#1485](https://github.com/NousResearch/hermes-agent/pull/1485))
- MiniMax Anthropic API 兼容性修复 ([#1623](https://github.com/NousResearch/hermes-agent/pull/1623))
- 自定义端点 `/models` 验证和 `/v1` base URL 建议 ([#1480](https://github.com/NousResearch/hermes-agent/pull/1480))
- 从 `custom_providers` 配置解析委托提供商 ([#1328](https://github.com/NousResearch/hermes-agent/pull/1328))
- Kimi 模型添加和 User-Agent 修复 ([#1039](https://github.com/NousResearch/hermes-agent/pull/1039))
- 为 Mistral 兼容性剥离 `call_id`/`response_item_id` ([#1058](https://github.com/NousResearch/hermes-agent/pull/1058))

### 代理循环与对话
- **Anthropic 上下文编辑 API** 支持 ([#1147](https://github.com/NousResearch/hermes-agent/pull/1147))
- 改进的上下文压缩交接摘要 — 压缩器现在保留更多可操作状态 ([#1273](https://github.com/NousResearch/hermes-agent/pull/1273))
- 在运行中上下文压缩后同步 session_id ([#1160](https://github.com/NousResearch/hermes-agent/pull/1160))
- 会话卫生阈值调整为 50% 以实现更主动的压缩 ([#1096](https://github.com/NousResearch/hermes-agent/pull/1096), [#1161](https://github.com/NousResearch/hermes-agent/pull/1161))
- 通过 `--pass-session-id` 标志在系统提示中包含会话 ID ([#1040](https://github.com/NousResearch/hermes-agent/pull/1040))
- 防止关闭的 OpenAI 客户端在重试间被重用 ([#1391](https://github.com/NousResearch/hermes-agent/pull/1391))
- 清理聊天负载和提供商优先级 ([#1253](https://github.com/NousResearch/hermes-agent/pull/1253))
- 处理来自 Codex 和本地后端的字典工具调用参数 ([#1393](https://github.com/NousResearch/hermes-agent/pull/1393), [#1440](https://github.com/NousResearch/hermes-agent/pull/1440))

### 记忆与会话
- **改进记忆优先级** — 用户偏好和修正优先于程序性知识 ([#1548](https://github.com/NousResearch/hermes-agent/pull/1548))
- 系统提示中更严格的记忆和会话回忆指导 ([#1329](https://github.com/NousResearch/hermes-agent/pull/1329))
- 将 CLI token 计数持久化到会话数据库以供 `/insights` 使用 ([#1498](https://github.com/NousResearch/hermes-agent/pull/1498))
- 将 Honcho 回忆保留在缓存系统前缀之外 ([#1201](https://github.com/NousResearch/hermes-agent/pull/1201))
- 修正 `seed_ai_identity` 使用 `session.add_messages()` ([#1475](https://github.com/NousResearch/hermes-agent/pull/1475))
- 为多用户网关隔离 Honcho 会话路由 ([#1500](https://github.com/NousResearch/hermes-agent/pull/1500))

---

## 📱 消息平台（网关）

### 网关核心
- **系统网关服务模式** — 作为系统级 systemd 服务运行，而非仅用户级 ([#1371](https://github.com/NousResearch/hermes-agent/pull/1371))
- **网关安装范围提示** — 在设置时选择用户或系统范围 ([#1374](https://github.com/NousResearch/hermes-agent/pull/1374))
- **推理热重载** — 无需重启网关即可更改推理设置 ([#1275](https://github.com/NousResearch/hermes-agent/pull/1275))
- 默认群组会话为按用户隔离 — 群聊中不再有跨用户的共享状态 ([#1495](https://github.com/NousResearch/hermes-agent/pull/1495), [#1417](https://github.com/NousResearch/hermes-agent/pull/1417))
- 加固网关重启恢复 ([#1310](https://github.com/NousResearch/hermes-agent/pull/1310))
- 关闭时取消活跃运行 ([#1427](https://github.com/NousResearch/hermes-agent/pull/1427))
- NixOS 和非标准系统的 SSL 证书自动检测 ([#1494](https://github.com/NousResearch/hermes-agent/pull/1494))
- 无头服务器上自动检测 D-Bus 会话总线用于 `systemctl --user` ([#1601](https://github.com/NousResearch/hermes-agent/pull/1601))
- 无头服务器上网关安装时自动启用 systemd linger ([#1334](https://github.com/NousResearch/hermes-agent/pull/1334))
- 当 `hermes` 不在 PATH 中时回退到模块入口点 ([#1355](https://github.com/NousResearch/hermes-agent/pull/1355))
- 修复 `hermes update` 后 macOS launchd 上的双网关问题 ([#1567](https://github.com/NousResearch/hermes-agent/pull/1567))
- 从 systemd 单元中移除递归 ExecStop ([#1530](https://github.com/NousResearch/hermes-agent/pull/1530))
- 防止网关模式下日志处理器累积 ([#1251](https://github.com/NousResearch/hermes-agent/pull/1251))
- 在可重试的启动失败时重启 — 由 @jplew 开发 ([#1517](https://github.com/NousResearch/hermes-agent/pull/1517))
- 代理运行后在网关会话中回填模型 ([#1306](https://github.com/NousResearch/hermes-agent/pull/1306))
- 基于 PID 的网关终止和延迟配置写入 ([#1499](https://github.com/NousResearch/hermes-agent/pull/1499))

### Telegram
- 缓冲媒体组以防止照片突发时的自我中断 ([#1341](https://github.com/NousResearch/hermes-agent/pull/1341), [#1422](https://github.com/NousResearch/hermes-agent/pull/1422))
- 在连接和发送期间对瞬态 TLS 故障重试 ([#1535](https://github.com/NousResearch/hermes-agent/pull/1535))
- 加固轮询冲突处理 ([#1339](https://github.com/NousResearch/hermes-agent/pull/1339))
- 在 MarkdownV2 中转义块指示器和内联代码 ([#1478](https://github.com/NousResearch/hermes-agent/pull/1478), [#1626](https://github.com/NousResearch/hermes-agent/pull/1626))
- 在断开连接前检查更新器/应用状态 ([#1389](https://github.com/NousResearch/hermes-agent/pull/1389))

### Discord
- `/thread` 命令，包含 `auto_thread` 配置和媒体元数据修复 ([#1178](https://github.com/NousResearch/hermes-agent/pull/1178))
- @提及时自动创建线程，在机器人线程中跳过提及文本 ([#1438](https://github.com/NousResearch/hermes-agent/pull/1438))
- 对系统消息不带回复引用进行重试 ([#1385](https://github.com/NousResearch/hermes-agent/pull/1385))
- 保留原生文档和视频附件支持 ([#1392](https://github.com/NousResearch/hermes-agent/pull/1392))
- 延迟 discord 适配器注解以避免可选导入崩溃 ([#1314](https://github.com/NousResearch/hermes-agent/pull/1314))

### Slack
- 线程处理全面改造 — 进度消息、响应和会话隔离都遵守线程 ([#1103](https://github.com/NousResearch/hermes-agent/pull/1103))
- 格式化、反应、用户解析和命令改进 ([#1106](https://github.com/NousResearch/hermes-agent/pull/1106))
- 修复 MAX_MESSAGE_LENGTH 3900 → 39000 ([#1117](https://github.com/NousResearch/hermes-agent/pull/1117))
- 文件上传备用方案保留线程上下文 — 由 @0xbyt4 开发 ([#1122](https://github.com/NousResearch/hermes-agent/pull/1122))
- 改进设置指导 ([#1387](https://github.com/NousResearch/hermes-agent/pull/1387))

### Email
- 修复 IMAP UID 跟踪和 SMTP TLS 验证 ([#1305](https://github.com/NousResearch/hermes-agent/pull/1305))
- 通过 config.yaml 添加 `skip_attachments` 选项 ([#1536](https://github.com/NousResearch/hermes-agent/pull/1536))

### Home Assistant
- 事件过滤默认关闭 ([#1169](https://github.com/NousResearch/hermes-agent/pull/1169))

---

## 🖥️ CLI 与用户体验

### 交互式 CLI
- **持久 CLI 状态栏** — 始终可见的模型、提供商和 token 计数 ([#1522](https://github.com/NousResearch/hermes-agent/pull/1522))
- **文件路径自动补全** — 输入提示中的文件路径补全 ([#1545](https://github.com/NousResearch/hermes-agent/pull/1545))
- **`/plan` 命令** — 从规格生成实施计划 ([#1372](https://github.com/NousResearch/hermes-agent/pull/1372), [#1381](https://github.com/NousResearch/hermes-agent/pull/1381))
- **`/rollback` 重大改进** — 更丰富的检查点历史、更清晰的用户体验 ([#1505](https://github.com/NousResearch/hermes-agent/pull/1505))
- **启动时预加载 CLI 技能** — 在第一个提示前技能就已就绪 ([#1359](https://github.com/NousResearch/hermes-agent/pull/1359))
- **集中式斜杠命令注册** — 所有命令定义一次，到处使用 ([#1603](https://github.com/NousResearch/hermes-agent/pull/1603))
- `/bg` 作为 `/background` 的别名 ([#1590](https://github.com/NousResearch/hermes-agent/pull/1590))
- 斜杠命令的前缀匹配 — `/mod` 解析为 `/model` ([#1320](https://github.com/NousResearch/hermes-agent/pull/1320))
- `/new`、`/reset`、`/clear` 现在启动真正的全新会话 ([#1237](https://github.com/NousResearch/hermes-agent/pull/1237))
- 接受会话 ID 前缀用于会话操作 ([#1425](https://github.com/NousResearch/hermes-agent/pull/1425))
- TUI 提示和强调输出现在遵循活跃皮肤 ([#1282](https://github.com/NousResearch/hermes-agent/pull/1282))
- 在注册表中集中工具表情元数据 + 皮肤集成 ([#1484](https://github.com/NousResearch/hermes-agent/pull/1484))
- 危险命令审批中添加"查看完整命令"选项 — 由 @teknium1 基于社区设计开发 ([#887](https://github.com/NousResearch/hermes-agent/pull/887))
- 非阻塞启动更新检查和横幅去重 ([#1386](https://github.com/NousResearch/hermes-agent/pull/1386))
- `/reasoning` 命令输出排序和内联 think 提取修复 ([#1031](https://github.com/NousResearch/hermes-agent/pull/1031))
- 详细模式显示完整未截断输出 ([#1472](https://github.com/NousResearch/hermes-agent/pull/1472))
- 修复 `/status` 以报告实时状态和 token ([#1476](https://github.com/NousResearch/hermes-agent/pull/1476))
- 初始化默认全局 SOUL.md ([#1311](https://github.com/NousResearch/hermes-agent/pull/1311))

### 设置与配置
- **首次设置时的 OpenClaw 迁移** — 由 @kshitijk4poor 开发 ([#981](https://github.com/NousResearch/hermes-agent/pull/981))
- `hermes claw migrate` 命令 + 迁移文档 ([#1059](https://github.com/NousResearch/hermes-agent/pull/1059))
- 尊重用户所选提供商的智能视觉设置 ([#1323](https://github.com/NousResearch/hermes-agent/pull/1323))
- 端到端处理无头设置流程 ([#1274](https://github.com/NousResearch/hermes-agent/pull/1274))
- 在 setup.py 中优先使用 curses 而非 `simple_term_menu` ([#1487](https://github.com/NousResearch/hermes-agent/pull/1487))
- 在 `/status` 中显示生效的模型和提供商 ([#1284](https://github.com/NousResearch/hermes-agent/pull/1284))
- 配置设置示例使用占位符语法 ([#1322](https://github.com/NousResearch/hermes-agent/pull/1322))
- 重新加载 .env 以覆盖过时的 shell 环境变量 ([#1434](https://github.com/NousResearch/hermes-agent/pull/1434))
- 修复 is_coding_plan NameError 崩溃 — 由 @0xbyt4 开发 ([#1123](https://github.com/NousResearch/hermes-agent/pull/1123))
- 添加缺失的包到 setuptools 配置 — 由 @alt-glitch 开发 ([#912](https://github.com/NousResearch/hermes-agent/pull/912))
- 安装器：在每个提示处说明为什么需要 sudo ([#1602](https://github.com/NousResearch/hermes-agent/pull/1602))

---

## 🔧 工具系统

### 终端与执行
- **持久 Shell 模式** — 本地和 SSH 后端跨工具调用维持 Shell 状态 — 由 @alt-glitch 开发 ([#1067](https://github.com/NousResearch/hermes-agent/pull/1067), [#1483](https://github.com/NousResearch/hermes-agent/pull/1483))
- **Tirith 预执行命令扫描** — 在执行前分析命令的安全层 ([#1256](https://github.com/NousResearch/hermes-agent/pull/1256))
- 从所有子进程环境中剥离 Hermes 提供商环境变量 ([#1157](https://github.com/NousResearch/hermes-agent/pull/1157), [#1172](https://github.com/NousResearch/hermes-agent/pull/1172), [#1399](https://github.com/NousResearch/hermes-agent/pull/1399), [#1419](https://github.com/NousResearch/hermes-agent/pull/1419)) — 初始修复由 @eren-karakus0 完成
- SSH 预检检查 ([#1486](https://github.com/NousResearch/hermes-agent/pull/1486))
- Docker 后端：将 cwd 工作区挂载设为显式选择加入 ([#1534](https://github.com/NousResearch/hermes-agent/pull/1534))
- 在 execute_code 沙箱中添加项目根目录到 PYTHONPATH ([#1383](https://github.com/NousResearch/hermes-agent/pull/1383))
- 消除网关平台上 execute_code 的进度刷屏 ([#1098](https://github.com/NousResearch/hermes-agent/pull/1098))
- 更清晰的 Docker 后端预检错误信息 ([#1276](https://github.com/NousResearch/hermes-agent/pull/1276))

### 浏览器
- **`/browser connect`** — 通过 CDP 将浏览器工具连接到正在运行的 Chrome 实例 ([#1549](https://github.com/NousResearch/hermes-agent/pull/1549))
- 改进浏览器清理、本地浏览器 PATH 设置和截图恢复 ([#1333](https://github.com/NousResearch/hermes-agent/pull/1333))

### MCP
- **选择性工具加载**及实用策略 — 过滤可用的 MCP 工具 ([#1302](https://github.com/NousResearch/hermes-agent/pull/1302))
- `mcp_servers` 配置更改时无需重启即可自动重载 MCP 工具 ([#1474](https://github.com/NousResearch/hermes-agent/pull/1474))
- 解决 npx stdio 连接失败 ([#1291](https://github.com/NousResearch/hermes-agent/pull/1291))
- 保存平台工具配置时保留 MCP 工具集 ([#1421](https://github.com/NousResearch/hermes-agent/pull/1421))

### 视觉
- 统一视觉后端门控 ([#1367](https://github.com/NousResearch/hermes-agent/pull/1367))
- 显示实际错误原因而非通用消息 ([#1338](https://github.com/NousResearch/hermes-agent/pull/1338))
- 使 Claude 图像处理端到端工作 ([#1408](https://github.com/NousResearch/hermes-agent/pull/1408))

### 定时任务
- **将定时任务管理压缩为一个工具** — 单个 `cronjob` 工具替代多个命令 ([#1343](https://github.com/NousResearch/hermes-agent/pull/1343))
- 抑制向自动投递目标的重复定时任务发送 ([#1357](https://github.com/NousResearch/hermes-agent/pull/1357))
- 将定时任务会话持久化到 SQLite ([#1255](https://github.com/NousResearch/hermes-agent/pull/1255))
- 每个任务的运行时覆盖（提供商、模型、base_url）([#1398](https://github.com/NousResearch/hermes-agent/pull/1398))
- `save_job_output` 中的原子写入防止崩溃时数据丢失 ([#1173](https://github.com/NousResearch/hermes-agent/pull/1173))
- 为 `deliver=origin` 保留线程上下文 ([#1437](https://github.com/NousResearch/hermes-agent/pull/1437))

### 补丁工具
- 避免在 V4A 补丁应用中损坏管道字符 ([#1286](https://github.com/NousResearch/hermes-agent/pull/1286))
- 宽松的 `block_anchor` 阈值和 Unicode 规范化 ([#1539](https://github.com/NousResearch/hermes-agent/pull/1539))

### 委托
- 为子代理结果添加可观察性元数据（模型、token、持续时间、工具跟踪）([#1175](https://github.com/NousResearch/hermes-agent/pull/1175))

---

## 🧩 技能生态系统

### 技能系统
- **集成 skills.sh** 作为与 ClawHub 并列的中心源 ([#1303](https://github.com/NousResearch/hermes-agent/pull/1303))
- 加载时安全设置技能环境 ([#1153](https://github.com/NousResearch/hermes-agent/pull/1153))
- 为危险判定遵守策略表 ([#1330](https://github.com/NousResearch/hermes-agent/pull/1330))
- 加固 ClawHub 技能搜索精确匹配 ([#1400](https://github.com/NousResearch/hermes-agent/pull/1400))
- 修复 ClawHub 技能安装 — 使用 `/download` ZIP 端点 ([#1060](https://github.com/NousResearch/hermes-agent/pull/1060))
- 避免将本地技能错误标记为内置 — 由 @arceus77-7 开发 ([#862](https://github.com/NousResearch/hermes-agent/pull/862))

### 新技能
- **Linear** 项目管理 ([#1230](https://github.com/NousResearch/hermes-agent/pull/1230))
- **X/Twitter** 通过 x-cli ([#1285](https://github.com/NousResearch/hermes-agent/pull/1285))
- **电话** — Twilio、SMS 和 AI 通话 ([#1289](https://github.com/NousResearch/hermes-agent/pull/1289))
- **1Password** — 由 @arceus77-7 开发 ([#883](https://github.com/NousResearch/hermes-agent/pull/883), [#1179](https://github.com/NousResearch/hermes-agent/pull/1179))
- **NeuroSkill BCI** 集成 ([#1135](https://github.com/NousResearch/hermes-agent/pull/1135))
- **Blender MCP** 用于 3D 建模 ([#1531](https://github.com/NousResearch/hermes-agent/pull/1531))
- **OSS 安全取证** ([#1482](https://github.com/NousResearch/hermes-agent/pull/1482))
- **Parallel CLI** 研究技能 ([#1301](https://github.com/NousResearch/hermes-agent/pull/1301))
- **OpenCode** CLI 技能 ([#1174](https://github.com/NousResearch/hermes-agent/pull/1174))
- **ASCII 视频**技能重构 — 由 @SHL0MS 开发 ([#1213](https://github.com/NousResearch/hermes-agent/pull/1213), [#1598](https://github.com/NousResearch/hermes-agent/pull/1598))

---

## 🎙️ 语音模式

- 语音模式基础 — CLI 按键说话、Telegram/Discord 语音消息 ([#1299](https://github.com/NousResearch/hermes-agent/pull/1299))
- 通过 faster-whisper 的免费本地 Whisper 转录 ([#1185](https://github.com/NousResearch/hermes-agent/pull/1185))
- Discord 语音频道可靠性修复 ([#1429](https://github.com/NousResearch/hermes-agent/pull/1429))
- 恢复网关语音消息的本地 STT 备用方案 ([#1490](https://github.com/NousResearch/hermes-agent/pull/1490))
- 在网关转录中遵守 `stt.enabled: false` ([#1394](https://github.com/NousResearch/hermes-agent/pull/1394))
- 修复 Telegram 语音消息上的虚假无能力消息 (Issue [#1033](https://github.com/NousResearch/hermes-agent/issues/1033))

---

## 🔌 ACP（IDE 集成）

- 恢复 ACP 服务器实现 ([#1254](https://github.com/NousResearch/hermes-agent/pull/1254))
- ACP 适配器中支持斜杠命令 ([#1532](https://github.com/NousResearch/hermes-agent/pull/1532))

---

## 🧪 RL 训练

- **代理在线策略蒸馏（OPD）环境** — 用于代理策略蒸馏的新 RL 训练环境 ([#1149](https://github.com/NousResearch/hermes-agent/pull/1149))
- 使 tinker-atropos RL 训练完全可选 ([#1062](https://github.com/NousResearch/hermes-agent/pull/1062))

---

## 🔒 安全与可靠性

### 安全加固
- **Tirith 预执行命令扫描** — 执行前对终端命令的静态分析 ([#1256](https://github.com/NousResearch/hermes-agent/pull/1256))
- **PII 脱敏** — 启用 `privacy.redact_pii` 时生效 ([#1542](https://github.com/NousResearch/hermes-agent/pull/1542))
- 从所有子进程环境中剥离 Hermes 提供商/网关/工具环境变量 ([#1157](https://github.com/NousResearch/hermes-agent/pull/1157), [#1172](https://github.com/NousResearch/hermes-agent/pull/1172), [#1399](https://github.com/NousResearch/hermes-agent/pull/1399), [#1419](https://github.com/NousResearch/hermes-agent/pull/1419))
- Docker cwd 工作区挂载现为显式选择加入 — 不再自动挂载宿主目录 ([#1534](https://github.com/NousResearch/hermes-agent/pull/1534))
- 在 fork 炸弹正则模式中转义括号和大括号 ([#1397](https://github.com/NousResearch/hermes-agent/pull/1397))
- 加固 `.worktreeinclude` 路径限制 ([#1388](https://github.com/NousResearch/hermes-agent/pull/1388))
- 使用描述作为 `pattern_key` 防止审批冲突 ([#1395](https://github.com/NousResearch/hermes-agent/pull/1395))

### 可靠性
- 保护初始化时的 stdio 写入 ([#1271](https://github.com/NousResearch/hermes-agent/pull/1271))
- 会话日志写入复用共享的原子 JSON 辅助工具 ([#1280](https://github.com/NousResearch/hermes-agent/pull/1280))
- 原子临时清理在中断时受保护 ([#1401](https://github.com/NousResearch/hermes-agent/pull/1401))

---

## 🐛 值得注意的 Bug 修复

- **`/status` 始终显示 0 token** — 现在报告实时状态 (Issue [#1465](https://github.com/NousResearch/hermes-agent/issues/1465), [#1476](https://github.com/NousResearch/hermes-agent/pull/1476))
- **自定义模型端点不工作** — 恢复了配置保存的端点解析 (Issue [#1460](https://github.com/NousResearch/hermes-agent/issues/1460), [#1373](https://github.com/NousResearch/hermes-agent/pull/1373))
- **MCP 工具直到重启才可见** — 配置更改时自动重载 (Issue [#1036](https://github.com/NousResearch/hermes-agent/issues/1036), [#1474](https://github.com/NousResearch/hermes-agent/pull/1474))
- **`hermes tools` 移除 MCP 工具** — 保存时保留 MCP 工具集 (Issue [#1247](https://github.com/NousResearch/hermes-agent/issues/1247), [#1421](https://github.com/NousResearch/hermes-agent/pull/1421))
- **终端子进程继承 `OPENAI_BASE_URL`** 破坏外部工具 (Issue [#1002](https://github.com/NousResearch/hermes-agent/issues/1002), [#1399](https://github.com/NousResearch/hermes-agent/pull/1399))
- **后台进程在网关重启时丢失** — 改进的恢复机制 (Issue [#1144](https://github.com/NousResearch/hermes-agent/issues/1144))
- **定时任务不持久化状态** — 现存储在 SQLite 中 (Issue [#1416](https://github.com/NousResearch/hermes-agent/issues/1416), [#1255](https://github.com/NousResearch/hermes-agent/pull/1255))
- **定时任务 `deliver: origin` 不保留线程上下文** (Issue [#1219](https://github.com/NousResearch/hermes-agent/issues/1219), [#1437](https://github.com/NousResearch/hermes-agent/pull/1437))
- **浏览器进程孤立时网关 systemd 服务无法自动重启** (Issue [#1617](https://github.com/NousResearch/hermes-agent/issues/1617))
- **Telegram 中 `/background` 完成报告被截断** (Issue [#1443](https://github.com/NousResearch/hermes-agent/issues/1443))
- **模型切换不生效** (Issue [#1244](https://github.com/NousResearch/hermes-agent/issues/1244), [#1183](https://github.com/NousResearch/hermes-agent/pull/1183))
- **`hermes doctor` 报告定时任务不可用** (Issue [#878](https://github.com/NousResearch/hermes-agent/issues/878), [#1180](https://github.com/NousResearch/hermes-agent/pull/1180))
- **WhatsApp 桥接消息未从手机接收** (Issue [#1142](https://github.com/NousResearch/hermes-agent/issues/1142))
- **无头 SSH 上设置向导挂起** (Issue [#905](https://github.com/NousResearch/hermes-agent/issues/905), [#1274](https://github.com/NousResearch/hermes-agent/pull/1274))
- **日志处理器累积**降低网关性能 (Issue [#990](https://github.com/NousResearch/hermes-agent/issues/990), [#1251](https://github.com/NousResearch/hermes-agent/pull/1251))
- **网关数据库中模型为 NULL** (Issue [#987](https://github.com/NousResearch/hermes-agent/issues/987), [#1306](https://github.com/NousResearch/hermes-agent/pull/1306))
- **严格端点拒绝重放的 tool_calls** (Issue [#893](https://github.com/NousResearch/hermes-agent/issues/893))
- **残留的硬编码 `~/.hermes` 路径** — 全部现在遵守 `HERMES_HOME` (Issue [#892](https://github.com/NousResearch/hermes-agent/issues/892), [#1233](https://github.com/NousResearch/hermes-agent/pull/1233))
- **委托工具不支持自定义推理提供商** (Issue [#1011](https://github.com/NousResearch/hermes-agent/issues/1011), [#1328](https://github.com/NousResearch/hermes-agent/pull/1328))
- **Skills Guard 阻止官方技能** (Issue [#1006](https://github.com/NousResearch/hermes-agent/issues/1006), [#1330](https://github.com/NousResearch/hermes-agent/pull/1330))
- **设置在模型选择前写入提供商** (Issue [#1182](https://github.com/NousResearch/hermes-agent/issues/1182))
- **`GatewayConfig.get()` AttributeError** 导致所有消息处理崩溃 (Issue [#1158](https://github.com/NousResearch/hermes-agent/issues/1158), [#1287](https://github.com/NousResearch/hermes-agent/pull/1287))
- **`/update` 硬失败显示 "command not found"** (Issue [#1049](https://github.com/NousResearch/hermes-agent/issues/1049))
- **图像分析静默失败** (Issue [#1034](https://github.com/NousResearch/hermes-agent/issues/1034), [#1338](https://github.com/NousResearch/hermes-agent/pull/1338))
- **API `BadRequestError` 由 `'dict'` 对象没有 `'strip'` 属性引起** (Issue [#1071](https://github.com/NousResearch/hermes-agent/issues/1071))
- **斜杠命令需要精确完整名称** — 现使用前缀匹配 (Issue [#928](https://github.com/NousResearch/hermes-agent/issues/928), [#1320](https://github.com/NousResearch/hermes-agent/pull/1320))
- **无头环境中关闭终端后网关停止响应** (Issue [#1005](https://github.com/NousResearch/hermes-agent/issues/1005))

---

## 🧪 测试

- 覆盖空缓存的 Anthropic 工具调用轮次 ([#1222](https://github.com/NousResearch/hermes-agent/pull/1222))
- 修复解析器和快捷命令覆盖中过时的 CI 假设 ([#1236](https://github.com/NousResearch/hermes-agent/pull/1236))
- 修复无隐式事件循环的网关异步测试 ([#1278](https://github.com/NousResearch/hermes-agent/pull/1278))
- 使网关异步测试 xdist 安全 ([#1281](https://github.com/NousResearch/hermes-agent/pull/1281))
- 定时任务的跨时区天真时间戳回归测试 ([#1319](https://github.com/NousResearch/hermes-agent/pull/1319))
- 隔离 codex 提供商测试与本地环境 ([#1335](https://github.com/NousResearch/hermes-agent/pull/1335))
- 锁定重试替换语义 ([#1379](https://github.com/NousResearch/hermes-agent/pull/1379))
- 改进会话搜索工具中的错误日志 — 由 @aydnOktay 开发 ([#1533](https://github.com/NousResearch/hermes-agent/pull/1533))

---

## 📚 文档

- 全面的 SOUL.md 指南 ([#1315](https://github.com/NousResearch/hermes-agent/pull/1315))
- 语音模式文档 ([#1316](https://github.com/NousResearch/hermes-agent/pull/1316), [#1362](https://github.com/NousResearch/hermes-agent/pull/1362))
- 提供商贡献指南 ([#1361](https://github.com/NousResearch/hermes-agent/pull/1361))
- ACP 和内部系统实现指南 ([#1259](https://github.com/NousResearch/hermes-agent/pull/1259))
- 扩展 Docusaurus 覆盖范围，涵盖 CLI、工具、技能和皮肤 ([#1232](https://github.com/NousResearch/hermes-agent/pull/1232))
- 终端后端和 Windows 故障排除 ([#1297](https://github.com/NousResearch/hermes-agent/pull/1297))
- 技能中心参考章节 ([#1317](https://github.com/NousResearch/hermes-agent/pull/1317))
- 检查点、/rollback 和 git 工作树指南 ([#1493](https://github.com/NousResearch/hermes-agent/pull/1493), [#1524](https://github.com/NousResearch/hermes-agent/pull/1524))
- CLI 状态栏和 /usage 参考 ([#1523](https://github.com/NousResearch/hermes-agent/pull/1523))
- 备用提供商 + /background 命令文档 ([#1430](https://github.com/NousResearch/hermes-agent/pull/1430))
- 网关服务范围文档 ([#1378](https://github.com/NousResearch/hermes-agent/pull/1378))
- Slack 线程回复行为文档 ([#1407](https://github.com/NousResearch/hermes-agent/pull/1407))
- 使用 Nous 蓝色调色板重新设计的着陆页 — 由 @austinpickett 开发 ([#974](https://github.com/NousResearch/hermes-agent/pull/974))
- 修复多处文档错别字 — 由 @JackTheGit 开发 ([#953](https://github.com/NousResearch/hermes-agent/pull/953))
- 稳定网站图表 ([#1405](https://github.com/NousResearch/hermes-agent/pull/1405))
- README 中的 CLI 与消息快速参考 ([#1491](https://github.com/NousResearch/hermes-agent/pull/1491))
- 为 Docusaurus 添加搜索 ([#1053](https://github.com/NousResearch/hermes-agent/pull/1053))
- Home Assistant 集成文档 ([#1170](https://github.com/NousResearch/hermes-agent/pull/1170))

---

## 👥 贡献者

### 核心
- **@teknium1** — 220+ 个 PR，涵盖代码库的每个领域

### 顶级社区贡献者

- **@0xbyt4**（4 个 PR）— Anthropic 适配器修复（max_tokens、备用崩溃、429/529 重试）、Slack 文件上传线程上下文、设置 NameError 修复
- **@erosika**（1 个 PR）— Honcho 记忆集成：异步写入、记忆模式、会话标题集成
- **@SHL0MS**（2 个 PR）— ASCII 视频技能设计模式和重构
- **@alt-glitch**（2 个 PR）— 本地/SSH 后端的持久 Shell 模式、setuptools 打包修复
- **@arceus77-7**（2 个 PR）— 1Password 技能、修复技能列表错误标记
- **@kshitijk4poor**（1 个 PR）— 设置向导期间的 OpenClaw 迁移
- **@ASRagab**（1 个 PR）— 修复 Claude 4.6 模型的自适应思考
- **@eren-karakus0**（1 个 PR）— 从子进程环境中剥离 Hermes 提供商环境变量
- **@mr-emmett-one**（1 个 PR）— 修复 DeepSeek V3 解析器多工具调用支持
- **@jplew**（1 个 PR）— 可重试的启动失败时网关重启
- **@brandtcormorant**（1 个 PR）— 修复空文本块的 Anthropic 缓存控制
- **@aydnOktay**（1 个 PR）— 改进会话搜索工具中的错误日志
- **@austinpickett**（1 个 PR）— 使用 Nous 蓝色调色板重新设计着陆页
- **@JackTheGit**（1 个 PR）— 文档错别字修复

### 所有贡献者

@0xbyt4, @alt-glitch, @arceus77-7, @ASRagab, @austinpickett, @aydnOktay, @brandtcormorant, @eren-karakus0, @erosika, @JackTheGit, @jplew, @kshitijk4poor, @mr-emmett-one, @SHL0MS, @teknium1

---

**完整变更日志**：[v2026.3.12...v2026.3.17](https://github.com/NousResearch/hermes-agent/compare/v2026.3.12...v2026.3.17)
