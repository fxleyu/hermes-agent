# Hermes Agent v0.9.0 (v2026.4.13)

**发布日期：** 2026年4月13日
**自 v0.8.0 以来：** 487 个提交 · 269 个合并 PR · 167 个已解决问题 · 493 个更改文件 · 63,281 行插入 · 24 位贡献者

> 无处不在发布版 — Hermes 通过 Termux/Android 走向移动端，新增 iMessage 和微信，推出 OpenAI 和 Anthropic 的快速模式，引入后台进程监控，发布用于管理代理的本地 Web 仪表盘，并在 16 个受支持平台上提供了迄今最深入的安全加固。

---

## ✨ 亮点

- **本地 Web 仪表盘** — 用于在本地管理 Hermes Agent 的全新浏览器端仪表盘。配置设置、监控会话、浏览技能和管理网关 — 全部通过简洁的 Web 界面完成，无需触碰配置文件或终端。这是开始使用 Hermes 最简单的方式。

- **快速模式（`/fast`）** — OpenAI 和 Anthropic 模型的优先处理。切换 `/fast` 可通过优先队列路由，在支持的模型（GPT-5.4、Codex、Claude）上显著降低延迟。扩展到所有 OpenAI 优先处理模型和 Anthropic 的快速层级。([#6875](https://github.com/NousResearch/hermes-agent/pull/6875)，[#6960](https://github.com/NousResearch/hermes-agent/pull/6960)，[#7037](https://github.com/NousResearch/hermes-agent/pull/7037))

- **通过 BlueBubbles 的 iMessage** — 通过 BlueBubbles 的完整 iMessage 集成，将 Hermes 带入 Apple 消息生态系统。自动 webhook 注册、设置向导集成和崩溃恢复能力。([#6437](https://github.com/NousResearch/hermes-agent/pull/6437)，[#6460](https://github.com/NousResearch/hermes-agent/pull/6460)，[#6494](https://github.com/NousResearch/hermes-agent/pull/6494))

- **微信（Weixin）与企业微信回调模式** — 通过 iLink Bot API 的原生微信支持以及用于自建企业应用的企业微信回调模式适配器。流式光标、媒体上传、Markdown 链接处理和原子状态持久化。Hermes 现已端到端覆盖中国消息生态系统。([#7166](https://github.com/NousResearch/hermes-agent/pull/7166)，[#7943](https://github.com/NousResearch/hermes-agent/pull/7943))

- **Termux / Android 支持** — 通过 Termux 在 Android 上原生运行 Hermes。适配安装路径、移动屏幕 TUI 优化、语音后端支持，`/image` 命令可在设备上使用。([#6834](https://github.com/NousResearch/hermes-agent/pull/6834))

- **后台进程监控（`watch_patterns`）** — 设置要在后台进程输出中监视的模式，并在匹配时实时获得通知。监视错误、等待特定事件（"listening on port"）或观察构建日志 — 全部无需轮询。([#7635](https://github.com/NousResearch/hermes-agent/pull/7635))

- **原生 xAI 和小米 MiMo 提供商** — xAI（Grok）和小米 MiMo 的一流提供商支持，带有直接 API 访问、模型目录和设置向导集成。加上带有门户请求支持的 Qwen OAuth。([#7372](https://github.com/NousResearch/hermes-agent/pull/7372)，[#7855](https://github.com/NousResearch/hermes-agent/pull/7855))

- **可插拔上下文引擎** — 上下文管理现在是通过 `hermes plugins` 的可插拔插槽。替换自定义上下文引擎来控制代理每轮看到的内容 — 过滤、摘要或特定领域的上下文注入。([#7464](https://github.com/NousResearch/hermes-agent/pull/7464))

- **统一代理支持** — SOCKS 代理、`DISCORD_PROXY` 和所有网关平台的系统代理自动检测。企业防火墙后的 Hermes 开箱即用。([#6814](https://github.com/NousResearch/hermes-agent/pull/6814))

- **全面安全加固** — 检查点管理器中的路径遍历保护、沙盒写入中的 shell 注入中和、Slack 图片上传中的 SSRF 重定向保护、Twilio webhook 签名验证（SMS RCE 修复）、API 服务器身份验证强制、git 参数注入预防和审批按钮授权。([#7933](https://github.com/NousResearch/hermes-agent/pull/7933)，[#7944](https://github.com/NousResearch/hermes-agent/pull/7944)，[#7940](https://github.com/NousResearch/hermes-agent/pull/7940)，[#7151](https://github.com/NousResearch/hermes-agent/pull/7151)，[#7156](https://github.com/NousResearch/hermes-agent/pull/7156))

- **`hermes backup` 和 `hermes import`** — 完整备份和恢复您的 Hermes 配置、会话、技能和记忆。在机器间迁移或在重大更改前创建快照。([#7997](https://github.com/NousResearch/hermes-agent/pull/7997))

- **16 个受支持平台** — 随着 BlueBubbles（iMessage）和微信加入 Telegram、Discord、Slack、WhatsApp、Signal、Matrix、Email、SMS、钉钉、飞书、企业微信、Mattermost、Home Assistant 和 Webhooks，Hermes 现在开箱即用地在 16 个消息平台上运行。

- **`/debug` 和 `hermes debug share`** — 新的调试工具包：所有平台上的 `/debug` 斜杠命令用于快速诊断，加上 `hermes debug share` 上传完整调试报告到 pastebin 以便故障排除时轻松分享。([#8681](https://github.com/NousResearch/hermes-agent/pull/8681))

---

## 🏗️ 核心代理与架构

### 提供商与模型支持
- **原生 xAI（Grok）提供商**，带有直接 API 访问和模型目录 ([#7372](https://github.com/NousResearch/hermes-agent/pull/7372))
- **小米 MiMo 作为一流提供商** — 设置向导、模型目录、空响应恢复 ([#7855](https://github.com/NousResearch/hermes-agent/pull/7855))
- **Qwen OAuth 提供商**，带有门户请求支持 ([#6282](https://github.com/NousResearch/hermes-agent/pull/6282))
- **快速模式** — `/fast` 切换用于 OpenAI 优先处理 + Anthropic 快速层级 ([#6875](https://github.com/NousResearch/hermes-agent/pull/6875)，[#6960](https://github.com/NousResearch/hermes-agent/pull/6960)，[#7037](https://github.com/NousResearch/hermes-agent/pull/7037))
- **结构化 API 错误分类**用于智能故障转移决策 ([#6514](https://github.com/NousResearch/hermes-agent/pull/6514))
- **速率限制头捕获**在 `/usage` 中显示 ([#6541](https://github.com/NousResearch/hermes-agent/pull/6541))
- **API 服务器模型名称**从配置文件名派生 ([#6857](https://github.com/NousResearch/hermes-agent/pull/6857))
- **自定义提供商**现在包含在 `/model` 列表和解析中 ([#7088](https://github.com/NousResearch/hermes-agent/pull/7088))
- **回退提供商激活**在重复空响应时带有用户可见状态 ([#7505](https://github.com/NousResearch/hermes-agent/pull/7505))
- **OpenRouter 变体标签**（`:free`、`:extended`、`:fast`）在模型切换期间保留 ([#6383](https://github.com/NousResearch/hermes-agent/pull/6383))
- **凭据耗尽 TTL** 从 24 小时减少到 1 小时 ([#6504](https://github.com/NousResearch/hermes-agent/pull/6504))
- **OAuth 凭据生命周期**加固 — 过时的池密钥、auth.json 同步、Codex CLI 竞态修复 ([#6874](https://github.com/NousResearch/hermes-agent/pull/6874))
- 推理模型（MiMo、Qwen、GLM）的空响应恢复 ([#8609](https://github.com/NousResearch/hermes-agent/pull/8609))
- MiniMax 上下文长度、思维保护、端点修正 ([#6082](https://github.com/NousResearch/hermes-agent/pull/6082)，[#7126](https://github.com/NousResearch/hermes-agent/pull/7126))
- Z.AI 端点通过探测和缓存自动检测 ([#5763](https://github.com/NousResearch/hermes-agent/pull/5763))

### 代理循环与对话
- **可插拔上下文引擎插槽**通过 `hermes plugins` ([#7464](https://github.com/NousResearch/hermes-agent/pull/7464))
- **后台进程监控** — `watch_patterns` 用于实时输出警报 ([#7635](https://github.com/NousResearch/hermes-agent/pull/7635))
- **改进的上下文压缩** — 更高限制、工具跟踪、降级警告、令牌预算尾部保护 ([#6395](https://github.com/NousResearch/hermes-agent/pull/6395)，[#6453](https://github.com/NousResearch/hermes-agent/pull/6453))
- **`/compress <focus>`** — 带有焦点主题的引导式压缩 ([#8017](https://github.com/NousResearch/hermes-agent/pull/8017))
- **分层上下文压力警告**，带有网关去重 ([#6411](https://github.com/NousResearch/hermes-agent/pull/6411))
- **超时升级前的分阶段不活跃警告** ([#6387](https://github.com/NousResearch/hermes-agent/pull/6387))
- **防止代理在任务中途停止** — 压缩下限、预算大修、活动跟踪 ([#7983](https://github.com/NousResearch/hermes-agent/pull/7983))
- **在 `delegate_task` 期间将子活动传播到父级** ([#7295](https://github.com/NousResearch/hermes-agent/pull/7295))
- **执行前的截断流式工具调用检测** ([#6847](https://github.com/NousResearch/hermes-agent/pull/6847))
- 空响应重试（3 次尝试加提示）([#6488](https://github.com/NousResearch/hermes-agent/pull/6488))
- 自适应流式退避 + 光标剥离以防止消息截断 ([#7683](https://github.com/NousResearch/hermes-agent/pull/7683))
- 压缩使用实时会话模型而非过时的持久化配置 ([#8258](https://github.com/NousResearch/hermes-agent/pull/8258))
- 从 Gemma 4 响应中剥离 `<thought>` 标签 ([#8562](https://github.com/NousResearch/hermes-agent/pull/8562))
- 防止散文中的 `<think>` 抑制响应输出 ([#6968](https://github.com/NousResearch/hermes-agent/pull/6968))
- 代理循环中的轮次退出诊断日志记录 ([#6549](https://github.com/NousResearch/hermes-agent/pull/6549))
- 按线程限定工具中断信号范围以防止跨会话泄漏 ([#7930](https://github.com/NousResearch/hermes-agent/pull/7930))

### 记忆与会话
- **Hindsight 记忆插件** — 功能对等、设置向导、配置改进 — @nicoloboschi ([#6428](https://github.com/NousResearch/hermes-agent/pull/6428))
- **Honcho** — 工具模式的可选 `initOnSessionStart` — @Kathie-yu ([#6995](https://github.com/NousResearch/hermes-agent/pull/6995))
- 在清理/删除时孤立子项而非级联删除 ([#6513](https://github.com/NousResearch/hermes-agent/pull/6513))
- Doctor 命令仅检查活跃的记忆提供商 ([#6285](https://github.com/NousResearch/hermes-agent/pull/6285))

---

## 📱 消息平台（网关）

### 新平台
- **BlueBubbles（iMessage）** — 完整适配器，带有自动 webhook 注册、设置向导和崩溃恢复能力 ([#6437](https://github.com/NousResearch/hermes-agent/pull/6437)，[#6460](https://github.com/NousResearch/hermes-agent/pull/6460)，[#6494](https://github.com/NousResearch/hermes-agent/pull/6494)，[#7107](https://github.com/NousResearch/hermes-agent/pull/7107))
- **微信（Weixin）** — 通过 iLink Bot API 的原生支持，带有流式传输、媒体上传、Markdown 链接 ([#7166](https://github.com/NousResearch/hermes-agent/pull/7166)，[#8665](https://github.com/NousResearch/hermes-agent/pull/8665))
- **企业微信回调模式** — 自建企业应用适配器，带有原子状态持久化 ([#7943](https://github.com/NousResearch/hermes-agent/pull/7943)，[#7928](https://github.com/NousResearch/hermes-agent/pull/7928))

### Discord
- **允许频道白名单**配置 — @jarvis-phw ([#7044](https://github.com/NousResearch/hermes-agent/pull/7044))
- **论坛频道话题继承**在线程会话中 — @hermes-agent-dhabibi ([#6377](https://github.com/NousResearch/hermes-agent/pull/6377))
- **DISCORD_REPLY_TO_MODE** 设置 ([#6333](https://github.com/NousResearch/hermes-agent/pull/6333))
- 接受 `.log` 附件、提高文档大小限制 — @kira-ariaki ([#6467](https://github.com/NousResearch/hermes-agent/pull/6467))
- 将就绪状态与斜杠同步解耦 ([#8016](https://github.com/NousResearch/hermes-agent/pull/8016))

### Slack
- **整合 Slack 改进** — 7 个社区 PR 挽救为一个 ([#6809](https://github.com/NousResearch/hermes-agent/pull/6809))
- 处理助手线程生命周期事件 ([#6433](https://github.com/NousResearch/hermes-agent/pull/6433))

### Matrix
- **从 matrix-nio 迁移到 mautrix-python** ([#7518](https://github.com/NousResearch/hermes-agent/pull/7518))
- SQLite 加密存储替换 pickle（修复 E2EE 解密）— @alt-glitch ([#7981](https://github.com/NousResearch/hermes-agent/pull/7981))
- 用于 E2EE 迁移的交叉签名恢复密钥验证 ([#8282](https://github.com/NousResearch/hermes-agent/pull/8282))
- DM 提及线程 + 飞书的群聊事件 ([#7423](https://github.com/NousResearch/hermes-agent/pull/7423))

### 网关核心
- **统一代理支持** — SOCKS、DISCORD_PROXY、多平台，带有 macOS 自动检测 ([#6814](https://github.com/NousResearch/hermes-agent/pull/6814))
- **入站文本批处理**用于 Discord、Matrix、企业微信 + 自适应延迟 ([#6979](https://github.com/NousResearch/hermes-agent/pull/6979))
- **在聊天平台中呈现自然的轮中助手消息** ([#7978](https://github.com/NousResearch/hermes-agent/pull/7978))
- **WSL 感知网关**，带有智能 systemd 检测 ([#7510](https://github.com/NousResearch/hermes-agent/pull/7510))
- **所有缺失平台添加到设置向导** ([#7949](https://github.com/NousResearch/hermes-agent/pull/7949))
- **每平台 `tool_progress` 覆盖** ([#6348](https://github.com/NousResearch/hermes-agent/pull/6348))
- **可配置的"仍在工作"通知间隔** ([#8572](https://github.com/NousResearch/hermes-agent/pull/8572))
- `/model` 切换跨消息持久化 ([#7081](https://github.com/NousResearch/hermes-agent/pull/7081))
- `/usage` 在轮次间显示速率限制、成本和令牌详情 ([#7038](https://github.com/NousResearch/hermes-agent/pull/7038))
- 重启前排空进行中的工作 ([#7503](https://github.com/NousResearch/hermes-agent/pull/7503))
- 失败运行时不驱逐缓存的代理 — 防止 MCP 重启循环 ([#7539](https://github.com/NousResearch/hermes-agent/pull/7539))
- 用 `contextvars` 替换 `os.environ` 会话状态 ([#7454](https://github.com/NousResearch/hermes-agent/pull/7454))
- 从枚举派生频道目录平台而非硬编码列表 ([#7450](https://github.com/NousResearch/hermes-agent/pull/7450))
- 缓存前验证图片下载（跨平台）([#7125](https://github.com/NousResearch/hermes-agent/pull/7125))
- 所有平台的跨平台 webhook 投递 ([#7095](https://github.com/NousResearch/hermes-agent/pull/7095))
- Cron Discord thread_id 投递支持 ([#7106](https://github.com/NousResearch/hermes-agent/pull/7106))
- 飞书基于二维码的机器人入职 ([#8570](https://github.com/NousResearch/hermes-agent/pull/8570))
- 网关状态限定为活跃配置文件 ([#7951](https://github.com/NousResearch/hermes-agent/pull/7951))
- 防止后台进程通知触发错误的配对请求 ([#6434](https://github.com/NousResearch/hermes-agent/pull/6434))

---

## 🖥️ CLI 与用户体验

### 交互式 CLI
- **Termux / Android 支持** — 适配安装路径、TUI、语音、`/image` ([#6834](https://github.com/NousResearch/hermes-agent/pull/6834))
- **原生 `/model` 选择器模态**用于提供商 → 模型选择 ([#8003](https://github.com/NousResearch/hermes-agent/pull/8003))
- **实时每工具耗时计时器**在 TUI 旋转器中恢复 ([#7359](https://github.com/NousResearch/hermes-agent/pull/7359))
- **堆叠的工具进度回滚**在 TUI 中 ([#8201](https://github.com/NousResearch/hermes-agent/pull/8201))
- **新会话启动时的随机提示**（CLI + 网关，279 条提示）([#8225](https://github.com/NousResearch/hermes-agent/pull/8225)，[#8237](https://github.com/NousResearch/hermes-agent/pull/8237))
- **`hermes dump`** — 可复制粘贴的设置摘要用于调试 ([#6550](https://github.com/NousResearch/hermes-agent/pull/6550))
- **`hermes backup` / `hermes import`** — 完整配置备份和恢复 ([#7997](https://github.com/NousResearch/hermes-agent/pull/7997))
- **WSL 环境提示**在系统提示中 ([#8285](https://github.com/NousResearch/hermes-agent/pull/8285))
- **配置文件创建 UX** — 播种 SOUL.md + 凭据警告 ([#8553](https://github.com/NousResearch/hermes-agent/pull/8553))
- Shell 感知的 sudo 检测、空密码支持 ([#6517](https://github.com/NousResearch/hermes-agent/pull/6517))
- curses/终端菜单后刷新 stdin 以防止转义序列泄漏 ([#7167](https://github.com/NousResearch/hermes-agent/pull/7167))
- 处理 prompt_toolkit 启动时损坏的 stdin ([#8560](https://github.com/NousResearch/hermes-agent/pull/8560))

### 设置与配置
- **每平台显示详细程度**配置 ([#8006](https://github.com/NousResearch/hermes-agent/pull/8006))
- **组件分离的日志记录**，带有会话上下文和过滤 ([#7991](https://github.com/NousResearch/hermes-agent/pull/7991))
- **`network.force_ipv4`** 配置以修复 IPv6 超时问题 ([#8196](https://github.com/NousResearch/hermes-agent/pull/8196))
- **标准化消息空白和 JSON 格式** ([#7988](https://github.com/NousResearch/hermes-agent/pull/7988))
- **品牌重塑 OpenClaw → Hermes** 在迁移期间 ([#8210](https://github.com/NousResearch/hermes-agent/pull/8210))
- 辅助设置中 Config.yaml 优先于环境变量 ([#7889](https://github.com/NousResearch/hermes-agent/pull/7889))
- 加固设置提供商流程 + 实时 OpenRouter 目录刷新 ([#7078](https://github.com/NousResearch/hermes-agent/pull/7078))
- 在所有界面中标准化推理努力度排序 ([#6804](https://github.com/NousResearch/hermes-agent/pull/6804))
- 移除死掉的 `LLM_MODEL` 环境变量 + 迁移以清除过时条目 ([#6543](https://github.com/NousResearch/hermes-agent/pull/6543))
- 移除 `/prompt` 斜杠命令 — 前缀扩展陷阱 ([#6752](https://github.com/NousResearch/hermes-agent/pull/6752))
- `HERMES_HOME_MODE` 环境变量以覆盖权限 — @ygd58 ([#6993](https://github.com/NousResearch/hermes-agent/pull/6993))
- 当模型配置为空时回退到默认模型 ([#8303](https://github.com/NousResearch/hermes-agent/pull/8303))
- 当压缩模型上下文太小时发出警告 ([#7894](https://github.com/NousResearch/hermes-agent/pull/7894))

---

## 🔧 工具系统

### 环境与执行
- **统一的每次调用生成执行层**用于环境 ([#6343](https://github.com/NousResearch/hermes-agent/pull/6343))
- **统一文件同步**，带有 mtime 跟踪、删除和事务状态 ([#7087](https://github.com/NousResearch/hermes-agent/pull/7087))
- **持久沙盒环境**在轮次间存活 ([#6412](https://github.com/NousResearch/hermes-agent/pull/6412))
- **批量文件同步**通过 tar 管道用于 SSH/Modal 后端 — @alt-glitch ([#8014](https://github.com/NousResearch/hermes-agent/pull/8014))
- **Daytona** — 批量上传、配置桥接、静默磁盘上限 ([#7538](https://github.com/NousResearch/hermes-agent/pull/7538))
- 前台超时上限以防止会话死锁 ([#7082](https://github.com/NousResearch/hermes-agent/pull/7082))
- 保护无效命令值 ([#6417](https://github.com/NousResearch/hermes-agent/pull/6417))

### MCP
- **`hermes mcp add --env` 和 `--preset`** 支持 ([#7970](https://github.com/NousResearch/hermes-agent/pull/7970))
- 当两者都存在时合并 `content` 和 `structuredContent` ([#7118](https://github.com/NousResearch/hermes-agent/pull/7118))
- MCP 工具名称去冲突修复 ([#7654](https://github.com/NousResearch/hermes-agent/pull/7654))

### 浏览器
- 浏览器加固 — 死代码移除、缓存、滚动性能、安全、线程安全 ([#7354](https://github.com/NousResearch/hermes-agent/pull/7354))
- `/browser connect` 自动启动使用专用 Chrome 配置文件目录 ([#6821](https://github.com/NousResearch/hermes-agent/pull/6821))
- 启动时回收孤立的浏览器会话 ([#7931](https://github.com/NousResearch/hermes-agent/pull/7931))

### 语音与视觉
- **Voxtral TTS 提供商**（Mistral AI）([#7653](https://github.com/NousResearch/hermes-agent/pull/7653))
- **TTS 速度支持**用于 Edge TTS、OpenAI TTS、MiniMax ([#8666](https://github.com/NousResearch/hermes-agent/pull/8666))
- **视觉自动调整大小**用于超大图片，提高限制到 20 MB，失败重试 ([#7883](https://github.com/NousResearch/hermes-agent/pull/7883)，[#7902](https://github.com/NousResearch/hermes-agent/pull/7902))
- STT 提供商-模型不匹配修复（whisper-1 vs faster-whisper）([#7113](https://github.com/NousResearch/hermes-agent/pull/7113))

### 其他工具
- **`hermes dump`** 命令用于设置摘要 ([#6550](https://github.com/NousResearch/hermes-agent/pull/6550))
- TODO 存储在替换操作期间强制 ID 唯一性 ([#7986](https://github.com/NousResearch/hermes-agent/pull/7986))
- 在 `delegate_task` schema 描述中列出所有可用工具集 ([#8231](https://github.com/NousResearch/hermes-agent/pull/8231))
- API 服务器：工具进度作为自定义 SSE 事件以防止模型损坏 ([#7500](https://github.com/NousResearch/hermes-agent/pull/7500))
- API 服务器：所有对话共享一个 Docker 容器 ([#7127](https://github.com/NousResearch/hermes-agent/pull/7127))

---

## 🧩 技能生态系统

- **集中式技能索引 + 树缓存** — 消除安装时的速率限制失败 ([#8575](https://github.com/NousResearch/hermes-agent/pull/8575))
- **更积极的技能加载指令**在系统提示中（v3）([#8209](https://github.com/NousResearch/hermes-agent/pull/8209)，[#8286](https://github.com/NousResearch/hermes-agent/pull/8286))
- **Google Workspace 技能**迁移到 GWS CLI 后端 ([#6788](https://github.com/NousResearch/hermes-agent/pull/6788))
- **Creative divergence strategies** 技能 — @SHL0MS ([#6882](https://github.com/NousResearch/hermes-agent/pull/6882))
- **Creative ideation** — 约束驱动的项目生成 — @SHL0MS ([#7555](https://github.com/NousResearch/hermes-agent/pull/7555))
- 并行化技能浏览/搜索以防止挂起 ([#7301](https://github.com/NousResearch/hermes-agent/pull/7301))
- 在 skills_sync 中从 SKILL.md frontmatter 读取名称 ([#7623](https://github.com/NousResearch/hermes-agent/pull/7623))

---

## 🔒 安全与可靠性

### 安全加固
- **Twilio webhook 签名验证** — SMS RCE 修复 ([#7933](https://github.com/NousResearch/hermes-agent/pull/7933))
- **沙盒写入中的 Shell 注入中和**通过路径引用 ([#7940](https://github.com/NousResearch/hermes-agent/pull/7940))
- **Git 参数注入**和检查点管理器中的路径遍历预防 ([#7944](https://github.com/NousResearch/hermes-agent/pull/7944))
- **Slack 图片上传中的 SSRF 重定向绕过** + base.py 缓存助手 ([#7151](https://github.com/NousResearch/hermes-agent/pull/7151))
- **路径遍历、凭据门控、DANGEROUS_PATTERNS 间隙** ([#7156](https://github.com/NousResearch/hermes-agent/pull/7156))
- **API 绑定保护** — 对非回环绑定强制 `API_SERVER_KEY` ([#7455](https://github.com/NousResearch/hermes-agent/pull/7455))
- **审批按钮授权** — 要求会话继续的身份验证 — @Cafexss ([#6930](https://github.com/NousResearch/hermes-agent/pull/6930))
- 技能管理器操作中的路径边界强制 ([#7156](https://github.com/NousResearch/hermes-agent/pull/7156))
- 钉钉/API webhook URL 来源验证、头注入拒绝 ([#7455](https://github.com/NousResearch/hermes-agent/pull/7455))

### 可靠性
- **无效 API 响应的上下文错误诊断** ([#8565](https://github.com/NousResearch/hermes-agent/pull/8565))
- **防止 400 格式错误**在 Codex 上触发压缩循环 ([#6751](https://github.com/NousResearch/hermes-agent/pull/6751))
- **不要在输出上限过大错误时减半 context_length** — @KUSH42 ([#6664](https://github.com/NousResearch/hermes-agent/pull/6664))
- **在 OpenAI 传输错误时恢复主要客户端** ([#7108](https://github.com/NousResearch/hermes-agent/pull/7108))
- **计费分类 400 上的凭据池轮换** ([#7112](https://github.com/NousResearch/hermes-agent/pull/7112))
- **本地 LLM 提供商自动增加流读取超时** ([#6967](https://github.com/NousResearch/hermes-agent/pull/6967))
- **当 CA 包路径不存在时回退到默认证书** ([#7352](https://github.com/NousResearch/hermes-agent/pull/7352))
- **消除错误分类器中的使用限制模式歧义** — @sprmn24 ([#6836](https://github.com/NousResearch/hermes-agent/pull/6836))
- 加固 cron 脚本超时和提供商恢复 ([#7079](https://github.com/NousResearch/hermes-agent/pull/7079))
- 网关中断检测对监控任务失败具有弹性 ([#8208](https://github.com/NousResearch/hermes-agent/pull/8208))
- 防止优雅网关重启后不需要的会话自动重置 ([#8299](https://github.com/NousResearch/hermes-agent/pull/8299))
- 防止网关监视器中的重复更新提示垃圾 ([#8343](https://github.com/NousResearch/hermes-agent/pull/8343))
- Responses API 输入中的推理项去重 ([#7946](https://github.com/NousResearch/hermes-agent/pull/7946))

### 基础设施
- **多架构 Docker 镜像** — amd64 + arm64 ([#6124](https://github.com/NousResearch/hermes-agent/pull/6124))
- **Docker 以非 root 用户运行**，带有虚拟环境 — @benbarclay 贡献 ([#8226](https://github.com/NousResearch/hermes-agent/pull/8226))
- **使用 `uv`** 进行 Docker 依赖解析以修复解析过深问题 ([#6965](https://github.com/NousResearch/hermes-agent/pull/6965))
- **容器感知的 Nix CLI** — 自动路由到托管容器 — @alt-glitch ([#7543](https://github.com/NousResearch/hermes-agent/pull/7543))
- **Nix 共享状态权限模型**用于交互式 CLI 用户 — @alt-glitch ([#6796](https://github.com/NousResearch/hermes-agent/pull/6796))
- **每配置文件的子进程 HOME 隔离** ([#7357](https://github.com/NousResearch/hermes-agent/pull/7357))
- Docker 中的配置文件路径修复 — 配置文件放入挂载卷 ([#7170](https://github.com/NousResearch/hermes-agent/pull/7170))
- Docker 容器网关路径加固 ([#8614](https://github.com/NousResearch/hermes-agent/pull/8614))
- 启用无缓冲 stdout 用于实时 Docker 日志 ([#6749](https://github.com/NousResearch/hermes-agent/pull/6749))
- 在 Docker 镜像中安装 procps — @HiddenPuppy ([#7032](https://github.com/NousResearch/hermes-agent/pull/7032))
- 浅层 git 克隆以加快安装 — @sosyz ([#8396](https://github.com/NousResearch/hermes-agent/pull/8396))
- `hermes update` 在 stash 冲突时始终重置 ([#7010](https://github.com/NousResearch/hermes-agent/pull/7010))
- 在网关重启前写入更新退出码（cgroup 终止竞态）([#8288](https://github.com/NousResearch/hermes-agent/pull/8288))
- Nix：`setupSecrets` 可选、tirith 运行时依赖 — @devorun, @ethernet8023 ([#6261](https://github.com/NousResearch/hermes-agent/pull/6261)，[#6721](https://github.com/NousResearch/hermes-agent/pull/6721))
- launchd 停止使用 `bootout` 使 `KeepAlive` 不重新生成 ([#7119](https://github.com/NousResearch/hermes-agent/pull/7119))

---

## 🐛 重要 Bug 修复

- 修复：`/model` 切换不跨网关消息持久化 ([#7081](https://github.com/NousResearch/hermes-agent/pull/7081))
- 修复：会话范围的网关模型覆盖被忽略 — @Hygaard ([#7662](https://github.com/NousResearch/hermes-agent/pull/7662))
- 修复：压缩模型上下文长度忽略配置 — 3 个相关问题 ([#8258](https://github.com/NousResearch/hermes-agent/pull/8258)，[#8107](https://github.com/NousResearch/hermes-agent/pull/8107))
- 修复：OpenCode.ai 上下文窗口解析为 128K 而非 1M ([#6472](https://github.com/NousResearch/hermes-agent/pull/6472))
- 修复：Codex 回退 auth-store 查找 — @cherifya ([#6462](https://github.com/NousResearch/hermes-agent/pull/6462))
- 修复：进程终止时的重复完成通知 ([#7124](https://github.com/NousResearch/hermes-agent/pull/7124))
- 修复：代理守护线程防止关闭 Tab 时的孤立 CLI 进程 ([#8557](https://github.com/NousResearch/hermes-agent/pull/8557))
- 修复：文本粘贴和语音输入时的过时图片附件 ([#7077](https://github.com/NousResearch/hermes-agent/pull/7077))
- 修复：DM 线程会话播种导致跨线程污染 ([#7084](https://github.com/NousResearch/hermes-agent/pull/7084))
- 修复：OpenClaw 迁移在执行前显示模拟运行预览 ([#6769](https://github.com/NousResearch/hermes-agent/pull/6769))
- 修复：身份验证错误被误分类为可重试 — @kuishou68 ([#7027](https://github.com/NousResearch/hermes-agent/pull/7027))
- 修复：Copilot-Integration-Id 头缺失 ([#7083](https://github.com/NousResearch/hermes-agent/pull/7083))
- 修复：ACP 会话能力 — @luyao618 ([#6985](https://github.com/NousResearch/hermes-agent/pull/6985))
- 修复：ACP PromptResponse 从顶级字段取用法 ([#7086](https://github.com/NousResearch/hermes-agent/pull/7086))
- 修复：main 上的多个失败/不稳定测试 — @dsocolobsky ([#6777](https://github.com/NousResearch/hermes-agent/pull/6777))
- 修复：备份标记文件名 — @sprmn24 ([#8600](https://github.com/NousResearch/hermes-agent/pull/8600))
- 修复：fast_mode 检查中的 `NoneType` — @0xbyt4 ([#7350](https://github.com/NousResearch/hermes-agent/pull/7350))
- 修复：uninstall.py 中的缺失导入 — @JiayuuWang ([#7034](https://github.com/NousResearch/hermes-agent/pull/7034))

---

## 📚 文档

- 平台适配器开发指南 + 企业微信回调文档 ([#7969](https://github.com/NousResearch/hermes-agent/pull/7969))
- Cron 故障排除指南 ([#7122](https://github.com/NousResearch/hermes-agent/pull/7122))
- 本地 LLM 的流式超时自动检测 ([#6990](https://github.com/NousResearch/hermes-agent/pull/6990))
- 工具使用强制文档扩展 ([#7984](https://github.com/NousResearch/hermes-agent/pull/7984))
- BlueBubbles 配对说明 ([#6548](https://github.com/NousResearch/hermes-agent/pull/6548))
- Telegram 代理支持部分 ([#6348](https://github.com/NousResearch/hermes-agent/pull/6348))
- `hermes dump` 和 `hermes logs` CLI 参考 ([#6552](https://github.com/NousResearch/hermes-agent/pull/6552))
- `tool_progress_overrides` 配置参考 ([#6364](https://github.com/NousResearch/hermes-agent/pull/6364))
- 压缩模型上下文长度警告文档 ([#7879](https://github.com/NousResearch/hermes-agent/pull/7879))

---

## 👥 贡献者

**269 个合并 PR**，来自 **24 位贡献者**，跨 **487 个提交**。

### 社区贡献者
- **@alt-glitch**（6 个 PR）— Nix 容器感知 CLI、共享状态权限、Matrix SQLite 加密存储、批量 SSH/Modal 文件同步、Matrix mautrix 兼容
- **@SHL0MS**（2 个 PR）— Creative divergence strategies 技能、creative ideation 技能
- **@sprmn24**（2 个 PR）— 错误分类器消歧义、备份标记修复
- **@nicoloboschi** — Hindsight 记忆插件功能对等
- **@Hygaard** — 会话范围的网关模型覆盖修复
- **@jarvis-phw** — Discord 允许频道白名单
- **@Kathie-yu** — Honcho initOnSessionStart 工具模式
- **@hermes-agent-dhabibi** — Discord 论坛频道话题继承
- **@kira-ariaki** — Discord .log 附件和大小限制
- **@cherifya** — Codex 回退 auth-store 查找
- **@Cafexss** — 安全：会话继续的身份验证
- **@KUSH42** — 压缩 context_length 修复
- **@kuishou68** — 身份验证错误可重试分类修复
- **@luyao618** — ACP 会话能力
- **@ygd58** — HERMES_HOME_MODE 环境变量覆盖
- **@0xbyt4** — 快速模式 NoneType 修复
- **@JiayuuWang** — CLI 卸载导入修复
- **@HiddenPuppy** — Docker procps 安装
- **@dsocolobsky** — 测试套件修复
- **@bobashopcashier**（1 个 PR）— 重启前的优雅网关排空（从 #7290 挽救到 #7503）
- **@benbarclay** — Docker 镜像标签简化
- **@sosyz** — 浅层 git 克隆以加快安装
- **@devorun** — Nix setupSecrets 可选
- **@ethernet8023** — Nix tirith 运行时依赖

---

**完整变更日志**：[v2026.4.8...v2026.4.13](https://github.com/NousResearch/hermes-agent/compare/v2026.4.8...v2026.4.13)
