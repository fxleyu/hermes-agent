# Hermes Agent v0.5.0 (v2026.3.28)

**发布日期：** 2026年3月28日

> 加固发布版 — Hugging Face 提供商、/model 命令全面改版、Telegram 私聊话题、原生 Modal SDK、插件生命周期钩子、GPT 模型工具使用强制、Nix flake、50+ 安全性和可靠性修复，以及全面的供应链审计。

---

## ✨ 亮点

- **Nous Portal 现已支持 400+ 模型** — Nous Research 推理门户已大幅扩展，使 Hermes Agent 用户可以通过单一提供商端点访问超过 400 个模型

- **Hugging Face 作为一流推理提供商** — 完整集成 HF Inference API，包括映射到 OpenRouter 类似模型的精选代理模型选择器、实时 `/models` 端点探测和设置向导流程 ([#3419](https://github.com/NousResearch/hermes-agent/pull/3419)，[#3440](https://github.com/NousResearch/hermes-agent/pull/3440))

- **Telegram 私聊话题** — 基于项目的对话，支持每个话题的功能技能绑定，在单个 Telegram 聊天中实现隔离的工作流程 ([#3163](https://github.com/NousResearch/hermes-agent/pull/3163))

- **原生 Modal SDK 后端** — 用原生 Modal SDK（`Sandbox.create.aio` + `exec.aio`）替换了 swe-rex 依赖，消除了隧道并简化了 Modal 终端后端 ([#3538](https://github.com/NousResearch/hermes-agent/pull/3538))

- **插件生命周期钩子已激活** — `pre_llm_call`、`post_llm_call`、`on_session_start` 和 `on_session_end` 钩子现在在代理循环和 CLI/网关中触发，完成了插件钩子系统 ([#3542](https://github.com/NousResearch/hermes-agent/pull/3542))

- **改进的 OpenAI 模型可靠性** — 添加了 `GPT_TOOL_USE_GUIDANCE` 以防止 GPT 模型描述预期操作而不进行工具调用，并自动从对话历史中剥离过时的预算警告，这些警告导致模型在各轮中避免使用工具 ([#3528](https://github.com/NousResearch/hermes-agent/pull/3528))

- **Nix flake** — 完整的 uv2nix 构建、带有持久容器模式的 NixOS 模块、从 Python 源代码自动生成的配置键，以及为代理友好性添加的后缀 PATH ([#20](https://github.com/NousResearch/hermes-agent/pull/20)，[#3274](https://github.com/NousResearch/hermes-agent/pull/3274)，[#3061](https://github.com/NousResearch/hermes-agent/pull/3061))，由 @alt-glitch 贡献

- **供应链加固** — 移除了受损的 `litellm` 依赖，固定了所有依赖版本范围，使用哈希重新生成了 `uv.lock`，添加了扫描 PR 供应链攻击模式的 CI 工作流，并更新了依赖以修复 CVE ([#2796](https://github.com/NousResearch/hermes-agent/pull/2796)，[#2810](https://github.com/NousResearch/hermes-agent/pull/2810)，[#2812](https://github.com/NousResearch/hermes-agent/pull/2812)，[#2816](https://github.com/NousResearch/hermes-agent/pull/2816)，[#3073](https://github.com/NousResearch/hermes-agent/pull/3073))

- **Anthropic 输出限制修复** — 用每个模型的原生输出限制（Opus 4.6 为 128K，Sonnet 4.6 为 64K）替换了硬编码的 16K `max_tokens`，修复了直接 Anthropic API 上的"响应被截断"和思维预算耗尽问题 ([#3426](https://github.com/NousResearch/hermes-agent/pull/3426)，[#3444](https://github.com/NousResearch/hermes-agent/pull/3444))

---

## 🏗️ 核心代理与架构

### 新提供商：Hugging Face
- 一流的 Hugging Face Inference API 集成，包含身份验证、设置向导和模型选择器 ([#3419](https://github.com/NousResearch/hermes-agent/pull/3419))
- 将 OpenRouter 代理默认模型映射到 HF 等效模型的精选模型列表 — 拥有 8+ 精选模型的提供商会跳过实时 `/models` 探测以提高速度 ([#3440](https://github.com/NousResearch/hermes-agent/pull/3440))
- 向 Z.AI 提供商模型列表添加了 glm-5-turbo ([#3095](https://github.com/NousResearch/hermes-agent/pull/3095))

### 提供商与模型改进
- `/model` 命令全面改版 — 为 CLI 和网关提取了共享的 `switch_model()` 管道，支持自定义端点、提供商感知路由 ([#2795](https://github.com/NousResearch/hermes-agent/pull/2795)，[#2799](https://github.com/NousResearch/hermes-agent/pull/2799))
- 从 CLI 和网关中移除了 `/model` 斜杠命令，改用 `hermes model` 子命令 ([#3080](https://github.com/NousResearch/hermes-agent/pull/3080))
- 保留 `custom` 提供商而不是静默重映射到 `openrouter` ([#2792](https://github.com/NousResearch/hermes-agent/pull/2792))
- 从 config.yaml 读取根级 `provider` 和 `base_url` 到模型配置中 ([#3112](https://github.com/NousResearch/hermes-agent/pull/3112))
- 将 Nous Portal 模型标识符与 OpenRouter 命名对齐 ([#3253](https://github.com/NousResearch/hermes-agent/pull/3253))
- 修复 Alibaba 提供商默认端点和模型列表 ([#3484](https://github.com/NousResearch/hermes-agent/pull/3484))
- 允许 MiniMax 用户覆盖 `/v1` → `/anthropic` 自动修正 ([#3553](https://github.com/NousResearch/hermes-agent/pull/3553))
- 将 OAuth 令牌刷新迁移到 `platform.claude.com` 并提供回退 ([#3246](https://github.com/NousResearch/hermes-agent/pull/3246))

### 代理循环与对话
- **改进的 OpenAI 模型可靠性** — `GPT_TOOL_USE_GUIDANCE` 防止 GPT 模型描述操作而不调用工具 + 自动从历史中剥离预算警告 ([#3528](https://github.com/NousResearch/hermes-agent/pull/3528))
- **表面化生命周期事件** — 所有重试、回退和压缩事件现在作为格式化消息呈现给用户 ([#3153](https://github.com/NousResearch/hermes-agent/pull/3153))
- **Anthropic 输出限制** — 每个模型的原生输出限制，而非硬编码的 16K `max_tokens` ([#3426](https://github.com/NousResearch/hermes-agent/pull/3426))
- **思维预算耗尽检测** — 当模型将所有输出令牌用于推理时，跳过无用的继续重试 ([#3444](https://github.com/NousResearch/hermes-agent/pull/3444))
- 始终优先使用流式传输进行 API 调用以防止子代理挂起 ([#3120](https://github.com/NousResearch/hermes-agent/pull/3120))
- 在流式传输失败后恢复安全的非流式回退 ([#3020](https://github.com/NousResearch/hermes-agent/pull/3020))
- 给予子代理独立的迭代预算 ([#3004](https://github.com/NousResearch/hermes-agent/pull/3004))
- 在 `_try_activate_fallback` 中更新 `api_key` 以用于子代理身份验证 ([#3103](https://github.com/NousResearch/hermes-agent/pull/3103))
- 在最大重试次数时优雅返回而不是崩溃线程 ([未标记的提交](https://github.com/NousResearch/hermes-agent))
- 将压缩重启计入重试限制 ([#3070](https://github.com/NousResearch/hermes-agent/pull/3070))
- 在预检估算中包含工具令牌，保护上下文探测持久性 ([#3164](https://github.com/NousResearch/hermes-agent/pull/3164))
- 在回退激活后更新上下文压缩器限制 ([#3305](https://github.com/NousResearch/hermes-agent/pull/3305))
- 验证空用户消息以防止 Anthropic API 400 错误 ([#3322](https://github.com/NousResearch/hermes-agent/pull/3322))
- GLM 纯推理和最大长度处理 ([#3010](https://github.com/NousResearch/hermes-agent/pull/3010))
- 将 API 超时默认值从 900 秒增加到 1800 秒以适应慢思考模型 ([#3431](https://github.com/NousResearch/hermes-agent/pull/3431))
- 为 Claude/OpenRouter 发送 `max_tokens` + 重试 SSE 连接错误 ([#3497](https://github.com/NousResearch/hermes-agent/pull/3497))
- 防止网关模式下的 AsyncOpenAI/httpx 跨循环死锁 ([#2701](https://github.com/NousResearch/hermes-agent/pull/2701))，由 @ctlst 贡献

### 流式传输与推理
- **跨网关会话轮次持久化推理**，新增 schema v6 列（`reasoning`、`reasoning_details`、`codex_reasoning_items`）([#2974](https://github.com/NousResearch/hermes-agent/pull/2974))
- 检测并终止过时的 SSE 连接 ([未标记的提交](https://github.com/NousResearch/hermes-agent))
- 修复过时流检测器竞态导致的虚假 `RemoteProtocolError` ([未标记的提交](https://github.com/NousResearch/hermes-agent))
- 在流式传输期间跳过 `<think>` 提取推理的重复回调 ([#3116](https://github.com/NousResearch/hermes-agent/pull/3116))
- 在 `rewrite_transcript` 中保留推理字段 ([#3311](https://github.com/NousResearch/hermes-agent/pull/3311))
- 在流式工具调用中保留 Gemini 思考签名 ([#2997](https://github.com/NousResearch/hermes-agent/pull/2997))
- 确保在推理更新期间触发第一个增量 ([未标记的提交](https://github.com/NousResearch/hermes-agent))

### 会话与记忆
- **会话搜索近期会话模式** — 省略查询以浏览带有标题、预览和时间戳的近期会话 ([#2533](https://github.com/NousResearch/hermes-agent/pull/2533))
- **会话配置呈现**在 `/new`、`/reset` 和自动重置时 ([#3321](https://github.com/NousResearch/hermes-agent/pull/3321))
- **第三方会话隔离** — `--source` 标志用于按来源隔离会话 ([#3255](https://github.com/NousResearch/hermes-agent/pull/3255))
- 添加 `/resume` CLI 处理器、会话日志截断保护、`reopen_session` API ([#3315](https://github.com/NousResearch/hermes-agent/pull/3315))
- 在 `/clear` 和 `/new` 时清除压缩器摘要和轮次计数器 ([#3102](https://github.com/NousResearch/hermes-agent/pull/3102))
- 表面化导致会话数据丢失的静默 SessionDB 失败 ([#2999](https://github.com/NousResearch/hermes-agent/pull/2999))
- 摘要失败时的会话搜索回退预览 ([#3478](https://github.com/NousResearch/hermes-agent/pull/3478))
- 防止刷新代理造成的过时记忆覆写 ([#2687](https://github.com/NousResearch/hermes-agent/pull/2687))

### 上下文压缩
- 用基于比率的缩放替换失效的 `summary_target_tokens` ([#2554](https://github.com/NousResearch/hermes-agent/pull/2554))
- 在 `DEFAULT_CONFIG` 中公开 `compression.target_ratio`、`protect_last_n` 和 `threshold` ([未标记的提交](https://github.com/NousResearch/hermes-agent))
- 恢复合理的默认值并将摘要上限设为 12K 令牌 ([未标记的提交](https://github.com/NousResearch/hermes-agent))
- 在 `/compress` 和卫生压缩时保留转录内容 ([#3556](https://github.com/NousResearch/hermes-agent/pull/3556))
- 在压缩后更新上下文压力警告和令牌估计 ([未标记的提交](https://github.com/NousResearch/hermes-agent))

### 架构与依赖
- **移除 mini-swe-agent 依赖** — 直接内联 Docker 和 Modal 后端 ([#2804](https://github.com/NousResearch/hermes-agent/pull/2804))
- **用原生 Modal SDK 替换 swe-rex** 用于 Modal 后端 ([#3538](https://github.com/NousResearch/hermes-agent/pull/3538))
- **插件生命周期钩子** — `pre_llm_call`、`post_llm_call`、`on_session_start`、`on_session_end` 现在在代理循环中触发 ([#3542](https://github.com/NousResearch/hermes-agent/pull/3542))
- 修复插件工具集在 `hermes tools` 和独立进程中不可见的问题 ([#3457](https://github.com/NousResearch/hermes-agent/pull/3457))
- 合并 `get_hermes_home()` 和 `parse_reasoning_effort()` ([#3062](https://github.com/NousResearch/hermes-agent/pull/3062))
- 移除未使用的 Hermes 原生 PKCE OAuth 流程 ([#3107](https://github.com/NousResearch/hermes-agent/pull/3107))
- 在 55 个文件中移除约 100 个未使用的导入 ([#3016](https://github.com/NousResearch/hermes-agent/pull/3016))
- 修复 154 个 f-string，简化 getattr/URL 模式，移除死代码 ([#3119](https://github.com/NousResearch/hermes-agent/pull/3119))

---

## 📱 消息平台（网关）

### Telegram
- **私聊话题** — 基于项目的对话，支持每个话题的功能技能绑定，在单个 Telegram 聊天中实现隔离的工作流程 ([#3163](https://github.com/NousResearch/hermes-agent/pull/3163))
- **通过 DNS-over-HTTPS 自动发现回退 IP**，当 `api.telegram.org` 不可达时 ([#3376](https://github.com/NousResearch/hermes-agent/pull/3376))
- **可配置的回复线程模式** ([#2907](https://github.com/NousResearch/hermes-agent/pull/2907))
- 在"Message thread not found"BadRequest 时回退到无 `thread_id` ([#3390](https://github.com/NousResearch/hermes-agent/pull/3390))
- 当 `start_polling` 在 502 后失败时自行重新安排重连 ([#3268](https://github.com/NousResearch/hermes-agent/pull/3268))

### Discord
- 在代理轮次完成后停止幽灵输入指示器 ([#3003](https://github.com/NousResearch/hermes-agent/pull/3003))

### Slack
- 将工具调用进度消息发送到正确的 Slack 线程 ([#3063](https://github.com/NousResearch/hermes-agent/pull/3063))
- 将进度线程回退范围限制为仅 Slack ([#3488](https://github.com/NousResearch/hermes-agent/pull/3488))

### WhatsApp
- 从消息中下载文档、音频和视频媒体 ([#2978](https://github.com/NousResearch/hermes-agent/pull/2978))

### Matrix
- 在 `PLATFORMS` 字典中添加缺失的 Matrix 条目 ([#3473](https://github.com/NousResearch/hermes-agent/pull/3473))
- 加固 e2ee 访问令牌处理 ([#3562](https://github.com/NousResearch/hermes-agent/pull/3562))
- 在同步循环中为 `SyncError` 添加退避 ([#3280](https://github.com/NousResearch/hermes-agent/pull/3280))

### Signal
- 将 SSE 保活注释作为连接活动进行跟踪 ([#3316](https://github.com/NousResearch/hermes-agent/pull/3316))

### Email
- 防止 EmailAdapter 中 `_seen_uids` 的无限增长 ([#3490](https://github.com/NousResearch/hermes-agent/pull/3490))

### 网关核心
- **配置门控的 `/verbose` 命令**用于消息平台 — 从聊天中切换工具输出详细程度 ([#3262](https://github.com/NousResearch/hermes-agent/pull/3262))
- **后台审查通知**发送到用户聊天 ([#3293](https://github.com/NousResearch/hermes-agent/pull/3293))
- **重试瞬态发送失败**并在耗尽时通知用户 ([#3288](https://github.com/NousResearch/hermes-agent/pull/3288))
- 从挂起的代理中恢复 — `/stop` 强制终止会话锁 ([#3104](https://github.com/NousResearch/hermes-agent/pull/3104))
- 线程安全的 `SessionStore` — 用 `threading.Lock` 保护 `_entries` ([#3052](https://github.com/NousResearch/hermes-agent/pull/3052))
- 修复缓存代理的网关令牌双重计数 — 使用绝对集合而非增量 ([#3306](https://github.com/NousResearch/hermes-agent/pull/3306)，[#3317](https://github.com/NousResearch/hermes-agent/pull/3317))
- 在代理缓存签名中指纹完整的身份验证令牌 ([#3247](https://github.com/NousResearch/hermes-agent/pull/3247))
- 静默后台代理终端输出 ([#3297](https://github.com/NousResearch/hermes-agent/pull/3297))
- 在启动允许列表检查中包含每平台 `ALLOW_ALL` 和 `SIGNAL_GROUP` ([#3313](https://github.com/NousResearch/hermes-agent/pull/3313))
- 在 systemd 单元 PATH 中包含用户本地 bin 路径 ([#3527](https://github.com/NousResearch/hermes-agent/pull/3527))
- 在 `GatewayRunner` 中跟踪后台任务引用 ([#3254](https://github.com/NousResearch/hermes-agent/pull/3254))
- 为 HA、Email、Mattermost、SMS 适配器添加请求超时 ([#3258](https://github.com/NousResearch/hermes-agent/pull/3258))
- 为 Mattermost、Slack 和基础缓存添加媒体下载重试 ([#3323](https://github.com/NousResearch/hermes-agent/pull/3323))
- 检测虚拟环境路径而不是硬编码 `venv/` ([#2797](https://github.com/NousResearch/hermes-agent/pull/2797))
- 使用 `TERMINAL_CWD` 进行上下文文件发现，而非进程 cwd ([未标记的提交](https://github.com/NousResearch/hermes-agent))
- 停止将 hermes 仓库 AGENTS.md 加载到网关会话中（约 10k 浪费的令牌）([#2891](https://github.com/NousResearch/hermes-agent/pull/2891))

---

## 🖥️ CLI 与用户体验

### 交互式 CLI
- **可配置的忙碌输入模式** + 修复 `/queue` 始终生效的问题 ([#3298](https://github.com/NousResearch/hermes-agent/pull/3298))
- **在多行粘贴时保留用户输入** ([#3065](https://github.com/NousResearch/hermes-agent/pull/3065))
- **工具生成回调** — 工具参数生成期间的流式"准备终端..."更新 ([未标记的提交](https://github.com/NousResearch/hermes-agent))
- 为实质性工具显示工具进度，而不仅仅是"准备中" ([未标记的提交](https://github.com/NousResearch/hermes-agent))
- 缓冲推理预览块并修复重复显示 ([#3013](https://github.com/NousResearch/hermes-agent/pull/3013))
- 防止推理框在工具调用循环中渲染 3 次 ([#3405](https://github.com/NousResearch/hermes-agent/pull/3405))
- 消除空闲时的"Event loop is closed"/"Press ENTER to continue" — 使用 `neuter_async_httpx_del()`、自定义异常处理器和过时客户端清理的三层修复 ([#3398](https://github.com/NousResearch/hermes-agent/pull/3398))
- 修复状态栏对尾随零的令牌计数显示 26K 而非 260K 的问题 ([#3024](https://github.com/NousResearch/hermes-agent/pull/3024))
- 修复长会话期间状态栏重复和降级的问题 ([#3291](https://github.com/NousResearch/hermes-agent/pull/3291))
- 在后台任务输出前刷新 TUI 以防止状态栏重叠 ([#3048](https://github.com/NousResearch/hermes-agent/pull/3048))
- 在 `patch_stdout` 下抑制 KawaiiSpinner 动画 ([#2994](https://github.com/NousResearch/hermes-agent/pull/2994))
- 当 TUI 处理工具进度时跳过 KawaiiSpinner ([#2973](https://github.com/NousResearch/hermes-agent/pull/2973))
- 通过 `_is_tty` 属性保护 `isatty()` 免受关闭流的影响 ([#3056](https://github.com/NousResearch/hermes-agent/pull/3056))
- 确保工具生成期间流式框的单次关闭 ([未标记的提交](https://github.com/NousResearch/hermes-agent))
- 在显示中将上下文压力百分比上限设为 100% ([#3480](https://github.com/NousResearch/hermes-agent/pull/3480))
- 清理 CLI 显示中的 HTML 错误消息 ([#3069](https://github.com/NousResearch/hermes-agent/pull/3069))
- 在 API 错误输出中显示 HTTP 状态码和 400 响应体 ([#3096](https://github.com/NousResearch/hermes-agent/pull/3096))
- 从 HTML 错误页面提取有用信息，在最大重试次数时转储调试信息 ([未标记的提交](https://github.com/NousResearch/hermes-agent))
- 防止 `base_url` 为 None 时的启动 TypeError ([#3068](https://github.com/NousResearch/hermes-agent/pull/3068))
- 防止非 TTY 环境中的更新崩溃 ([#3094](https://github.com/NousResearch/hermes-agent/pull/3094))
- 处理会话删除/清理确认提示中的 EOFError ([#3101](https://github.com/NousResearch/hermes-agent/pull/3101))
- 在退出时的 `flush_memories` 和退出清理处理器中捕获 KeyboardInterrupt ([#3025](https://github.com/NousResearch/hermes-agent/pull/3025)，[#3257](https://github.com/NousResearch/hermes-agent/pull/3257))
- 保护 `.strip()` 免受 YAML 配置中 None 值的影响 ([#3552](https://github.com/NousResearch/hermes-agent/pull/3552))
- 保护 `config.get()` 免受 YAML null 值的影响以防止 AttributeError ([#3377](https://github.com/NousResearch/hermes-agent/pull/3377))
- 存储 asyncio 任务引用以防止 GC 中途执行 ([#3267](https://github.com/NousResearch/hermes-agent/pull/3267))

### 设置与配置
- 使用显式键映射进行返回用户菜单分发，而非位置索引 ([#3083](https://github.com/NousResearch/hermes-agent/pull/3083))
- 在更新命令中使用 `sys.executable` 运行 pip 以修复 PEP 668 ([#3099](https://github.com/NousResearch/hermes-agent/pull/3099))
- 加固 `hermes update` 以应对分叉历史、非 main 分支和网关边缘情况 ([#3492](https://github.com/NousResearch/hermes-agent/pull/3492))
- OpenClaw 迁移覆盖默认值和设置向导跳过导入部分 — 已修复 ([#3282](https://github.com/NousResearch/hermes-agent/pull/3282))
- 停止递归 AGENTS.md 遍历，仅加载顶层 ([#3110](https://github.com/NousResearch/hermes-agent/pull/3110))
- 在浏览器和终端 PATH 解析中添加 macOS Homebrew 路径 ([#2713](https://github.com/NousResearch/hermes-agent/pull/2713))
- `tool_progress` 配置的 YAML 布尔值处理 ([#3300](https://github.com/NousResearch/hermes-agent/pull/3300))
- 将默认 SOUL.md 重置为基线身份文本 ([#3159](https://github.com/NousResearch/hermes-agent/pull/3159))
- 拒绝容器终端后端的相对 cwd 路径 ([未标记的提交](https://github.com/NousResearch/hermes-agent))
- 为 API 服务器平台添加显式 `hermes-api-server` 工具集 ([#3304](https://github.com/NousResearch/hermes-agent/pull/3304))
- 重新排序设置向导提供商 — OpenRouter 优先 ([未标记的提交](https://github.com/NousResearch/hermes-agent))

---

## 🔧 工具系统

### API 服务器
- **Idempotency-Key 支持**、请求体大小限制和 OpenAI 错误封装 ([#2903](https://github.com/NousResearch/hermes-agent/pull/2903))
- 在 CORS 头中允许 Idempotency-Key ([#3530](https://github.com/NousResearch/hermes-agent/pull/3530))
- 取消孤立代理 + SSE 断连时的真正中断 ([#3427](https://github.com/NousResearch/hermes-agent/pull/3427))
- 修复代理进行工具调用时流式传输中断的问题 ([#2985](https://github.com/NousResearch/hermes-agent/pull/2985))

### 终端与文件操作
- 处理 V4A 补丁解析器中的仅添加块 ([#3325](https://github.com/NousResearch/hermes-agent/pull/3325))
- 持久 shell 轮询的指数退避 ([#2996](https://github.com/NousResearch/hermes-agent/pull/2996))
- 为 `context_references` 中的子进程调用添加超时 ([#3469](https://github.com/NousResearch/hermes-agent/pull/3469))

### 浏览器与视觉
- 处理视觉工具中的 402 余额不足错误 ([#2802](https://github.com/NousResearch/hermes-agent/pull/2802))
- 修复 `browser_vision` 忽略 `auxiliary.vision.timeout` 配置的问题 ([#2901](https://github.com/NousResearch/hermes-agent/pull/2901))
- 使浏览器命令超时可通过 config.yaml 配置 ([#2801](https://github.com/NousResearch/hermes-agent/pull/2801))

### MCP
- MCP 工具集运行时和配置解析 ([#3252](https://github.com/NousResearch/hermes-agent/pull/3252))
- 添加 MCP 工具名称冲突保护 ([#3077](https://github.com/NousResearch/hermes-agent/pull/3077))

### 辅助 LLM
- 保护辅助 LLM 调用免受 None 内容影响 + 推理回退 + 重试 ([#3449](https://github.com/NousResearch/hermes-agent/pull/3449))
- 在视觉自动检测中捕获 `build_anthropic_client` 的 ImportError ([#3312](https://github.com/NousResearch/hermes-agent/pull/3312))

### 其他工具
- 为 `send_message_tool` HTTP 调用添加请求超时 ([#3162](https://github.com/NousResearch/hermes-agent/pull/3162))，由 @memosr 贡献
- 自动修复包含无效控制字符的 `jobs.json` ([#3537](https://github.com/NousResearch/hermes-agent/pull/3537))
- 为 Claude/OpenRouter 启用细粒度工具流式传输 ([#3497](https://github.com/NousResearch/hermes-agent/pull/3497))

---

## 🧩 技能生态系统

### 技能系统
- **环境变量透传**用于技能和用户配置 — 技能可以声明要透传的环境变量 ([#2807](https://github.com/NousResearch/hermes-agent/pull/2807))
- 使用共享的 `skill_utils` 模块缓存技能提示以加快 TTFT ([#3421](https://github.com/NousResearch/hermes-agent/pull/3421))
- 避免技能条件的冗余文件重读 ([#2992](https://github.com/NousResearch/hermes-agent/pull/2992))
- 使用 Git Trees API 防止安装期间的静默子目录丢失 ([#2995](https://github.com/NousResearch/hermes-agent/pull/2995))
- 修复深度嵌套仓库结构的 skills-sh 安装 ([#2980](https://github.com/NousResearch/hermes-agent/pull/2980))
- 处理技能前置元数据中的 null 元数据 ([未标记的提交](https://github.com/NousResearch/hermes-agent))
- 保留 skills-sh 标识符的信任 + 减少解析流失 ([#3251](https://github.com/NousResearch/hermes-agent/pull/3251))
- 代理创建的技能被错误地视为不受信任的社区内容 — 已修复 ([未标记的提交](https://github.com/NousResearch/hermes-agent))

### 新技能
- **G0DM0D3 godmode 越狱技能** + 文档 ([#3157](https://github.com/NousResearch/hermes-agent/pull/3157))
- **Docker 管理技能**已添加到可选技能中 ([#3060](https://github.com/NousResearch/hermes-agent/pull/3060))
- **OpenClaw 迁移 v2** — 17 个新模块，用于从 OpenClaw 迁移到 Hermes 的终端回顾 ([#2906](https://github.com/NousResearch/hermes-agent/pull/2906))

---

## 🔒 安全与可靠性

### 安全加固
- **SSRF 防护**已添加到 `browser_navigate` ([#3058](https://github.com/NousResearch/hermes-agent/pull/3058))
- **SSRF 防护**已添加到 `vision_tools` 和 `web_tools`（加固版）([#2679](https://github.com/NousResearch/hermes-agent/pull/2679))
- **限制子代理工具集**为父代理的已启用集合 ([#3269](https://github.com/NousResearch/hermes-agent/pull/3269))
- **防止自更新中的 zip-slip 路径遍历** ([#3250](https://github.com/NousResearch/hermes-agent/pull/3250))
- **防止 `_expand_path` 中通过 `~user` 路径后缀的 shell 注入** ([#2685](https://github.com/NousResearch/hermes-agent/pull/2685))
- **在危险命令检测前规范化输入** ([#3260](https://github.com/NousResearch/hermes-agent/pull/3260))
- 使 tirith 阻止裁定可批准而非硬阻止 ([#3428](https://github.com/NousResearch/hermes-agent/pull/3428))
- 从依赖中移除受损的 `litellm`/`typer`/`platformdirs` ([#2796](https://github.com/NousResearch/hermes-agent/pull/2796))
- 固定所有依赖版本范围 ([#2810](https://github.com/NousResearch/hermes-agent/pull/2810))
- 使用哈希重新生成 `uv.lock`，在设置中使用锁文件 ([#2812](https://github.com/NousResearch/hermes-agent/pull/2812))
- 更新依赖以修复 CVE + 重新生成 `uv.lock` ([#3073](https://github.com/NousResearch/hermes-agent/pull/3073))
- 用于 PR 扫描的供应链审计 CI 工作流 ([#2816](https://github.com/NousResearch/hermes-agent/pull/2816))

### 可靠性
- **SQLite WAL 写锁争用**导致 15-20 秒 TUI 冻结 — 已修复 ([#3385](https://github.com/NousResearch/hermes-agent/pull/3385))
- **SQLite 并发加固** + 会话转录完整性 ([#3249](https://github.com/NousResearch/hermes-agent/pull/3249))
- 防止网关崩溃/重启循环中的重复 cron 任务重新触发 ([#3396](https://github.com/NousResearch/hermes-agent/pull/3396))
- 在任务完成后将 cron 会话标记为已结束 ([#2998](https://github.com/NousResearch/hermes-agent/pull/2998))

---

## ⚡ 性能

- **TTFT 启动优化** — 挽救了简易启动改进 ([#3395](https://github.com/NousResearch/hermes-agent/pull/3395))
- 使用共享的 `skill_utils` 模块缓存技能提示 ([#3421](https://github.com/NousResearch/hermes-agent/pull/3421))
- 避免提示构建器中技能条件的冗余文件重读 ([#2992](https://github.com/NousResearch/hermes-agent/pull/2992))

---

## 🐛 重要 Bug 修复

- 修复缓存代理的网关令牌双重计数 ([#3306](https://github.com/NousResearch/hermes-agent/pull/3306)，[#3317](https://github.com/NousResearch/hermes-agent/pull/3317))
- 修复空闲会话期间的"Event loop is closed"/"Press ENTER to continue" ([#3398](https://github.com/NousResearch/hermes-agent/pull/3398))
- 修复推理框在工具调用循环中渲染 3 次的问题 ([#3405](https://github.com/NousResearch/hermes-agent/pull/3405))
- 修复状态栏令牌计数显示 26K 而非 260K 的问题 ([#3024](https://github.com/NousResearch/hermes-agent/pull/3024))
- 修复 `/queue` 无论配置如何始终生效的问题 ([#3298](https://github.com/NousResearch/hermes-agent/pull/3298))
- 修复代理轮次后的 Discord 幽灵输入指示器 ([#3003](https://github.com/NousResearch/hermes-agent/pull/3003))
- 修复 Slack 进度消息出现在错误线程中的问题 ([#3063](https://github.com/NousResearch/hermes-agent/pull/3063))
- 修复 WhatsApp 媒体下载（文档、音频、视频）([#2978](https://github.com/NousResearch/hermes-agent/pull/2978))
- 修复 Telegram "Message thread not found" 终止进度消息的问题 ([#3390](https://github.com/NousResearch/hermes-agent/pull/3390))
- 修复 OpenClaw 迁移覆盖默认值的问题 ([#3282](https://github.com/NousResearch/hermes-agent/pull/3282))
- 修复返回用户设置菜单分发错误部分的问题 ([#3083](https://github.com/NousResearch/hermes-agent/pull/3083))
- 修复 `hermes update` PEP 668 "externally-managed-environment" 错误 ([#3099](https://github.com/NousResearch/hermes-agent/pull/3099))
- 修复子代理通过共享预算过早达到 `max_iterations` 的问题 ([#3004](https://github.com/NousResearch/hermes-agent/pull/3004))
- 修复 `tool_progress` 配置的 YAML 布尔值处理 ([#3300](https://github.com/NousResearch/hermes-agent/pull/3300))
- 修复 `config.get()` 在 YAML null 值上的崩溃 ([#3377](https://github.com/NousResearch/hermes-agent/pull/3377))
- 修复 YAML 配置中 None 值的 `.strip()` 崩溃 ([#3552](https://github.com/NousResearch/hermes-agent/pull/3552))
- 修复网关挂起的代理 — `/stop` 现在强制终止会话锁 ([#3104](https://github.com/NousResearch/hermes-agent/pull/3104))
- 修复 `_custom` 提供商被静默重映射到 `openrouter` 的问题 ([#2792](https://github.com/NousResearch/hermes-agent/pull/2792))
- 修复 `PLATFORMS` 字典中缺失 Matrix 的问题 ([#3473](https://github.com/NousResearch/hermes-agent/pull/3473))
- 修复 Email 适配器 `_seen_uids` 无限增长的问题 ([#3490](https://github.com/NousResearch/hermes-agent/pull/3490))

---

## 🧪 测试

- 固定 `agent-client-protocol` < 0.9 以处理上游破坏性发布 ([#3320](https://github.com/NousResearch/hermes-agent/pull/3320))
- 在视觉自动检测测试中捕获 anthropic ImportError ([#3312](https://github.com/NousResearch/hermes-agent/pull/3312))
- 更新重试耗尽测试以适应新的优雅返回行为 ([#3320](https://github.com/NousResearch/hermes-agent/pull/3320))
- 添加 null 元数据前置的回归测试 ([未标记的提交](https://github.com/NousResearch/hermes-agent))

---

## 📚 文档

- 更新所有文档以适应 `/model` 命令改版和自定义提供商支持 ([#2800](https://github.com/NousResearch/hermes-agent/pull/2800))
- 修复 18 个文件中的过时和不正确文档 ([#2805](https://github.com/NousResearch/hermes-agent/pull/2805))
- 记录 9 个先前未记录的功能 ([#2814](https://github.com/NousResearch/hermes-agent/pull/2814))
- 在文档中添加缺失的技能、CLI 命令和消息环境变量 ([#2809](https://github.com/NousResearch/hermes-agent/pull/2809))
- 修复 api-server 响应存储文档 — SQLite，而非内存 ([#2819](https://github.com/NousResearch/hermes-agent/pull/2819))
- 引用 pip install extras 以修复 zsh glob 错误 ([#2815](https://github.com/NousResearch/hermes-agent/pull/2815))
- 统一钩子文档 — 在钩子页面添加插件钩子，添加 `session:end` 事件 ([未标记的提交](https://github.com/NousResearch/hermes-agent))
- 在 `session_search` schema 描述中澄清双模式行为 ([未标记的提交](https://github.com/NousResearch/hermes-agent))
- 修复 Discord 提供的邀请链接的 Discord Public Bot 设置 ([#3519](https://github.com/NousResearch/hermes-agent/pull/3519))，由 @mehmoodosman 贡献
- 修订 v0.4.0 变更日志 — 修复功能归属，重新排序部分 ([未标记的提交](https://github.com/NousResearch/hermes-agent))

---

## 👥 贡献者

### 核心
- **@teknium1** — 157 个 PR，涵盖本次发布的全部范围

### 社区贡献者
- **@alt-glitch**（Siddharth Balyan）— 2 个 PR：使用 uv2nix 构建的 Nix flake、NixOS 模块和持久容器模式 ([#20](https://github.com/NousResearch/hermes-agent/pull/20))；自动生成的配置键和 Nix 构建的后缀 PATH ([#3061](https://github.com/NousResearch/hermes-agent/pull/3061)，[#3274](https://github.com/NousResearch/hermes-agent/pull/3274))
- **@ctlst** — 1 个 PR：防止网关模式下的 AsyncOpenAI/httpx 跨循环死锁 ([#2701](https://github.com/NousResearch/hermes-agent/pull/2701))
- **@memosr**（memosr.eth）— 1 个 PR：为 `send_message_tool` HTTP 调用添加请求超时 ([#3162](https://github.com/NousResearch/hermes-agent/pull/3162))
- **@mehmoodosman**（Osman Mehmood）— 1 个 PR：修复 Discord 文档的 Public Bot 设置 ([#3519](https://github.com/NousResearch/hermes-agent/pull/3519))

### 所有贡献者
@alt-glitch, @ctlst, @mehmoodosman, @memosr, @teknium1

---

**完整变更日志**：[v2026.3.23...v2026.3.28](https://github.com/NousResearch/hermes-agent/compare/v2026.3.23...v2026.3.28)
