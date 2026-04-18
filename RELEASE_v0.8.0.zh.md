# Hermes Agent v0.8.0 (v2026.4.8)

**发布日期：** 2026年4月8日

> 智能发布版 — 后台任务自动通知、Nous Portal 免费 MiMo v2 Pro、全平台实时模型切换、自优化的 GPT/Codex 指导、原生 Google AI Studio、智能不活跃超时、审批按钮、MCP OAuth 2.1，以及 209 个合并 PR 和 82 个已解决的问题。

---

## ✨ 亮点

- **后台进程自动通知（`notify_on_complete`）** — 后台任务现在可以在完成时自动通知代理。启动一个长时间运行的进程（AI 模型训练、测试套件、部署、构建），代理会在完成时收到通知 — 无需轮询。代理可以继续处理其他事务，并在结果到达时接收。([#5779](https://github.com/NousResearch/hermes-agent/pull/5779))

- **Nous Portal 免费小米 MiMo v2 Pro** — Nous Portal 现在支持免费层级的小米 MiMo v2 Pro 模型用于辅助任务（压缩、视觉、摘要），带有免费层级模型门控和模型选择中的定价显示。([#6018](https://github.com/NousResearch/hermes-agent/pull/6018)，[#5880](https://github.com/NousResearch/hermes-agent/pull/5880))

- **实时模型切换（`/model` 命令）** — 从 CLI、Telegram、Discord、Slack 或任何网关平台在会话中切换模型和提供商。聚合器感知的解析尽可能保持在 OpenRouter/Nous 上，需要时自动跨提供商回退。Telegram 和 Discord 上带有内联按钮的交互式模型选择器。([#5181](https://github.com/NousResearch/hermes-agent/pull/5181)，[#5742](https://github.com/NousResearch/hermes-agent/pull/5742))

- **自优化的 GPT/Codex 工具使用指导** — 代理通过自动化行为基准测试诊断并修补了 GPT 和 Codex 工具调用中的 5 个失败模式，显著提高了 OpenAI 模型的可靠性。包含执行纪律指导和用于结构化推理的仅思考预填续写。([#6120](https://github.com/NousResearch/hermes-agent/pull/6120)，[#5414](https://github.com/NousResearch/hermes-agent/pull/5414)，[#5931](https://github.com/NousResearch/hermes-agent/pull/5931))

- **Google AI Studio（Gemini）原生提供商** — 通过 Google AI Studio API 直接访问 Gemini 模型。包含自动的 models.dev 注册表集成，用于跨任何提供商的实时上下文长度检测。([#5577](https://github.com/NousResearch/hermes-agent/pull/5577))

- **基于不活跃的代理超时** — 网关和 cron 超时现在跟踪实际工具活动而非挂钟时间。正在积极工作的长时间运行任务永远不会被终止 — 只有真正空闲的代理才会超时。([#5389](https://github.com/NousResearch/hermes-agent/pull/5389)，[#5440](https://github.com/NousResearch/hermes-agent/pull/5440))

- **Slack 和 Telegram 的审批按钮** — 通过原生平台按钮而非输入 `/approve` 来审批危险命令。Slack 获得线程上下文保留；Telegram 获得审批状态的 emoji 反应。([#5890](https://github.com/NousResearch/hermes-agent/pull/5890)，[#5975](https://github.com/NousResearch/hermes-agent/pull/5975))

- **MCP OAuth 2.1 PKCE + OSV 恶意软件扫描** — 用于 MCP 服务器身份验证的完全符合标准的 OAuth，加上通过 OSV 漏洞数据库对 MCP 扩展包的自动恶意软件扫描。([#5420](https://github.com/NousResearch/hermes-agent/pull/5420)，[#5305](https://github.com/NousResearch/hermes-agent/pull/5305))

- **集中式日志与配置验证** — 结构化日志到 `~/.hermes/logs/`（agent.log + errors.log），配合 `hermes logs` 命令进行尾部查看和过滤。配置结构验证在启动时捕获格式错误的 YAML，防止其导致隐晦的故障。([#5430](https://github.com/NousResearch/hermes-agent/pull/5430)，[#5426](https://github.com/NousResearch/hermes-agent/pull/5426))

- **插件系统扩展** — 插件现在可以注册 CLI 子命令、接收带有关联 ID 的请求范围 API 钩子、在安装期间提示必需的环境变量，并钩入会话生命周期事件（完成/重置）。([#5295](https://github.com/NousResearch/hermes-agent/pull/5295)，[#5427](https://github.com/NousResearch/hermes-agent/pull/5427)，[#5470](https://github.com/NousResearch/hermes-agent/pull/5470)，[#6129](https://github.com/NousResearch/hermes-agent/pull/6129))

- **Matrix 一级支持与平台加固** — Matrix 获得反应、已读回执、富文本格式和房间管理。Discord 添加频道控制和忽略频道。Signal 获得完整的 MEDIA: 标签投递。Mattermost 获得文件附件。所有平台的全面可靠性修复。([#5275](https://github.com/NousResearch/hermes-agent/pull/5275)，[#5975](https://github.com/NousResearch/hermes-agent/pull/5975)，[#5602](https://github.com/NousResearch/hermes-agent/pull/5602))

- **安全加固轮次** — 整合 SSRF 防护、时序攻击缓解、tar 遍历预防、凭据泄漏保护、cron 路径遍历加固和跨会话隔离。终端工作目录在所有后端进行清理。([#5944](https://github.com/NousResearch/hermes-agent/pull/5944)，[#5613](https://github.com/NousResearch/hermes-agent/pull/5613)，[#5629](https://github.com/NousResearch/hermes-agent/pull/5629))

---

## 🏗️ 核心代理与架构

### 提供商与模型支持
- **原生 Google AI Studio（Gemini）提供商**，带有 models.dev 集成用于自动上下文长度检测 ([#5577](https://github.com/NousResearch/hermes-agent/pull/5577))
- **`/model` 命令 — 完整的提供商+模型系统改版** — 跨 CLI 和所有网关平台的实时切换，带有聚合器感知解析 ([#5181](https://github.com/NousResearch/hermes-agent/pull/5181))
- **Telegram 和 Discord 的交互式模型选择器** — 基于内联按钮的模型选择 ([#5742](https://github.com/NousResearch/hermes-agent/pull/5742))
- **Nous Portal 免费层级模型门控**，带有模型选择中的定价显示 ([#5880](https://github.com/NousResearch/hermes-agent/pull/5880))
- **模型定价显示**用于 OpenRouter 和 Nous Portal 提供商 ([#5416](https://github.com/NousResearch/hermes-agent/pull/5416))
- **xAI（Grok）提示缓存**通过 `x-grok-conv-id` 头 ([#5604](https://github.com/NousResearch/hermes-agent/pull/5604))
- **Grok 添加到工具使用强制模型**用于直接 xAI 使用 ([#5595](https://github.com/NousResearch/hermes-agent/pull/5595))
- **MiniMax TTS 提供商**（speech-2.8）([#4963](https://github.com/NousResearch/hermes-agent/pull/4963))
- **非代理模型警告** — 当加载非工具使用设计的 Hermes LLM 模型时警告用户 ([#5378](https://github.com/NousResearch/hermes-agent/pull/5378))
- **Ollama Cloud 身份验证、/model 切换持久化**和别名 Tab 补全 ([#5269](https://github.com/NousResearch/hermes-agent/pull/5269))
- **保留 OpenCode Go 模型名称中的点**（minimax-m2.7、glm-4.5、kimi-k2.5）([#5597](https://github.com/NousResearch/hermes-agent/pull/5597))
- **MiniMax 模型 404 修复** — 为 OpenCode Go 从 Anthropic base URL 剥离 /v1 ([#4918](https://github.com/NousResearch/hermes-agent/pull/4918))
- **提供商凭据重置窗口**在池化故障转移中被尊重 ([#5188](https://github.com/NousResearch/hermes-agent/pull/5188))
- **OAuth 令牌同步**在凭据池和凭据文件之间 ([#4981](https://github.com/NousResearch/hermes-agent/pull/4981))
- **过时的 OAuth 凭据**不再阻塞 OpenRouter 用户的自动检测 ([#5746](https://github.com/NousResearch/hermes-agent/pull/5746))
- **Codex OAuth 凭据池断开** + 过期令牌导入修复 ([#5681](https://github.com/NousResearch/hermes-agent/pull/5681))
- **Codex 池条目同步**在耗尽时从 `~/.codex/auth.json` 同步 — @GratefulDave ([#5610](https://github.com/NousResearch/hermes-agent/pull/5610))
- **辅助客户端付费回退** — 在 402 时重试下一个提供商 ([#5599](https://github.com/NousResearch/hermes-agent/pull/5599))
- **辅助客户端解析命名的自定义提供商**和 'main' 别名 ([#5978](https://github.com/NousResearch/hermes-agent/pull/5978))
- **使用 mimo-v2-pro** 用于 Nous 免费层级上的非视觉辅助任务 ([#6018](https://github.com/NousResearch/hermes-agent/pull/6018))
- **视觉自动检测**优先尝试主要提供商 ([#6041](https://github.com/NousResearch/hermes-agent/pull/6041))
- **提供商重排序和快速安装** — @austinpickett ([#4664](https://github.com/NousResearch/hermes-agent/pull/4664))
- **Nous OAuth access_token** 不再用作推理 API 密钥 — @SHL0MS ([#5564](https://github.com/NousResearch/hermes-agent/pull/5564))
- **HERMES_PORTAL_BASE_URL 环境变量**在 Nous 登录期间被尊重 — @benbarclay ([#5745](https://github.com/NousResearch/hermes-agent/pull/5745))
- **环境变量覆盖**用于 Nous 门户/推理 URL ([#5419](https://github.com/NousResearch/hermes-agent/pull/5419))
- **Z.AI 端点自动检测**通过探测和缓存 ([#5763](https://github.com/NousResearch/hermes-agent/pull/5763))
- **MiniMax 上下文长度、模型目录、思维保护、辅助模型和配置 base_url** 修正 ([#6082](https://github.com/NousResearch/hermes-agent/pull/6082))
- **社区提供商/模型解析修复** — 挽救了 4 个社区 PR + MiniMax 辅助 URL ([#5983](https://github.com/NousResearch/hermes-agent/pull/5983))

### 代理循环与对话
- **自优化的 GPT/Codex 工具使用指导**通过自动化行为基准测试 — 代理自诊断并修补了 5 个失败模式 ([#6120](https://github.com/NousResearch/hermes-agent/pull/6120))
- **GPT/Codex 执行纪律指导**在系统提示中 ([#5414](https://github.com/NousResearch/hermes-agent/pull/5414))
- **仅思考预填续写**用于结构化推理响应 ([#5931](https://github.com/NousResearch/hermes-agent/pull/5931))
- **接受仅推理响应**而不重试 — 将内容设为 "(empty)" 而非无限重试 ([#5278](https://github.com/NousResearch/hermes-agent/pull/5278))
- **带抖动的重试退避** — API 重试的指数退避加抖动 ([#6048](https://github.com/NousResearch/hermes-agent/pull/6048))
- **智能思维块签名管理** — 跨轮次保留和管理 Anthropic 思维签名 ([#6112](https://github.com/NousResearch/hermes-agent/pull/6112))
- **强制工具调用参数匹配 JSON Schema 类型** — 修复模型发送字符串而非数字/布尔值的情况 ([#5265](https://github.com/NousResearch/hermes-agent/pull/5265))
- **将超大工具结果保存到文件**而非破坏性截断 ([#5210](https://github.com/NousResearch/hermes-agent/pull/5210))
- **沙盒感知的工具结果持久化** ([#6085](https://github.com/NousResearch/hermes-agent/pull/6085))
- **编辑失败后的流式回退**改进 ([#6110](https://github.com/NousResearch/hermes-agent/pull/6110))
- **Codex 空输出间隙**在回退 + 标准化器 + 辅助客户端中覆盖 ([#5724](https://github.com/NousResearch/hermes-agent/pull/5724)，[#5730](https://github.com/NousResearch/hermes-agent/pull/5730)，[#5734](https://github.com/NousResearch/hermes-agent/pull/5734))
- **Codex 流输出回填**来自 output_item.done 事件 ([#5689](https://github.com/NousResearch/hermes-agent/pull/5689))
- **流消费者在工具边界后创建新消息** ([#5739](https://github.com/NousResearch/hermes-agent/pull/5739))
- **Codex 验证与标准化对齐**用于空流输出 ([#5940](https://github.com/NousResearch/hermes-agent/pull/5940))
- **在 copilot-acp 适配器中桥接工具调用** ([#5460](https://github.com/NousResearch/hermes-agent/pull/5460))
- **从 chat-completions 负载中过滤仅转录角色** ([#4880](https://github.com/NousResearch/hermes-agent/pull/4880))
- **上下文压缩失败修复**在温度受限模型上 — @MadKangYu ([#5608](https://github.com/NousResearch/hermes-agent/pull/5608))
- **为所有严格 API 清理 tool_calls**（Fireworks、Mistral 等）— @lumethegreat ([#5183](https://github.com/NousResearch/hermes-agent/pull/5183))

### 记忆与会话
- **Supermemory 记忆提供商** — 新的记忆插件，带有多容器、search_mode、身份模板和环境变量覆盖 ([#5737](https://github.com/NousResearch/hermes-agent/pull/5737)，[#5933](https://github.com/NousResearch/hermes-agent/pull/5933))
- **默认共享线程会话** — 跨网关平台的多用户线程支持 ([#5391](https://github.com/NousResearch/hermes-agent/pull/5391))
- **子代理会话链接到父级**并从会话列表中隐藏 ([#5309](https://github.com/NousResearch/hermes-agent/pull/5309))
- **配置文件范围的记忆隔离**和克隆支持 ([#4845](https://github.com/NousResearch/hermes-agent/pull/4845))
- **将网关 user_id 线程化到记忆插件**用于每用户范围 ([#5895](https://github.com/NousResearch/hermes-agent/pull/5895))
- **Honcho 插件漂移大修** + 插件 CLI 注册系统 ([#5295](https://github.com/NousResearch/hermes-agent/pull/5295))
- **Honcho 全息提示和信任评分**渲染保留 ([#4872](https://github.com/NousResearch/hermes-agent/pull/4872))
- **Honcho doctor 修复** — 使用 recall_mode 而非 memory_mode — @techguysimon ([#5645](https://github.com/NousResearch/hermes-agent/pull/5645))
- **RetainDB** — API 路由、写入队列、辩证法、代理模型、文件工具修复 ([#5461](https://github.com/NousResearch/hermes-agent/pull/5461))
- **Hindsight 记忆插件大修** + 记忆设置向导修复 ([#5094](https://github.com/NousResearch/hermes-agent/pull/5094))
- **mem0 API v2 兼容**、预取上下文围栏、密钥编辑 ([#5423](https://github.com/NousResearch/hermes-agent/pull/5423))
- **mem0 环境变量合并**到 mem0.json 而非二选一 ([#4939](https://github.com/NousResearch/hermes-agent/pull/4939))
- **干净的用户消息**用于所有记忆提供商操作 ([#4940](https://github.com/NousResearch/hermes-agent/pull/4940))
- **静默记忆刷新失败**在 /new 和 /resume 时已修复 — @ryanautomated ([#5640](https://github.com/NousResearch/hermes-agent/pull/5640))
- **OpenViking atexit 安全网**用于会话提交 ([#5664](https://github.com/NousResearch/hermes-agent/pull/5664))
- **OpenViking 租户范围头**用于多租户服务器 ([#4936](https://github.com/NousResearch/hermes-agent/pull/4936))
- **ByteRover brv 查询**在 LLM 调用前同步运行 ([#4831](https://github.com/NousResearch/hermes-agent/pull/4831))

---

## 📱 消息平台（网关）

### 网关核心
- **基于不活跃的代理超时** — 用智能活动跟踪替代挂钟超时；长时间运行的活跃任务永不被终止 ([#5389](https://github.com/NousResearch/hermes-agent/pull/5389))
- **Slack 和 Telegram 的审批按钮** + Slack 线程上下文保留 ([#5890](https://github.com/NousResearch/hermes-agent/pull/5890))
- **实时流式 /update 输出** + 将交互式提示转发给用户 ([#5180](https://github.com/NousResearch/hermes-agent/pull/5180))
- **无限超时支持** + 定期通知 + 可操作的错误消息 ([#4959](https://github.com/NousResearch/hermes-agent/pull/4959))
- **重复消息预防** — 网关去重 + 部分流保护 ([#4878](https://github.com/NousResearch/hermes-agent/pull/4878))
- **Webhook delivery_info 持久化** + /status 中的完整会话 ID ([#5942](https://github.com/NousResearch/hermes-agent/pull/5942))
- **工具预览截断**在所有/新进度模式中尊重 tool_preview_length ([#5937](https://github.com/NousResearch/hermes-agent/pull/5937))
- **短预览截断**为所有/新工具进度模式恢复 ([#4935](https://github.com/NousResearch/hermes-agent/pull/4935))
- **更新待定状态**原子写入以防止损坏 ([#4923](https://github.com/NousResearch/hermes-agent/pull/4923))
- **审批会话键每轮隔离** ([#4884](https://github.com/NousResearch/hermes-agent/pull/4884))
- **活跃会话保护绕过**用于 /approve、/deny、/stop、/new ([#4926](https://github.com/NousResearch/hermes-agent/pull/4926)，[#5765](https://github.com/NousResearch/hermes-agent/pull/5765))
- **审批等待期间暂停输入指示器** ([#5893](https://github.com/NousResearch/hermes-agent/pull/5893))
- **标题检查**使用精确的逐行匹配而非子字符串（所有平台）([#5939](https://github.com/NousResearch/hermes-agent/pull/5939))
- **从流式网关消息中剥离 MEDIA: 标签** ([#5152](https://github.com/NousResearch/hermes-agent/pull/5152))
- **发送前从 cron 投递中提取 MEDIA: 标签** ([#5598](https://github.com/NousResearch/hermes-agent/pull/5598))
- **配置文件感知的服务单元** + 语音转录清理 ([#5972](https://github.com/NousResearch/hermes-agent/pull/5972))
- **线程安全的 PairingStore**，带有原子写入 — @CharlieKerfoot ([#5656](https://github.com/NousResearch/hermes-agent/pull/5656))
- **在基础平台日志中清理媒体 URL** — @WAXLYY ([#5631](https://github.com/NousResearch/hermes-agent/pull/5631))
- **减少 Telegram 回退 IP 激活日志噪声** — @MadKangYu ([#5615](https://github.com/NousResearch/hermes-agent/pull/5615))
- **Cron 静态方法包装器**以防止 self 绑定 ([#5299](https://github.com/NousResearch/hermes-agent/pull/5299))
- **过时的 'hermes login' 替换**为 'hermes auth' + 凭据移除重播种修复 ([#5670](https://github.com/NousResearch/hermes-agent/pull/5670))

### Telegram
- **群组话题技能绑定**用于超级群组论坛话题 ([#4886](https://github.com/NousResearch/hermes-agent/pull/4886))
- **Emoji 反应**用于审批状态和通知 ([#5975](https://github.com/NousResearch/hermes-agent/pull/5975))
- **发送超时时防止重复消息投递** ([#5153](https://github.com/NousResearch/hermes-agent/pull/5153))
- **命令名称清理**以剥离无效字符 ([#5596](https://github.com/NousResearch/hermes-agent/pull/5596))
- **每平台禁用的技能**在 Telegram 菜单和网关分发中被尊重 ([#4799](https://github.com/NousResearch/hermes-agent/pull/4799))
- **/approve 和 /deny** 通过运行代理保护路由 ([#4798](https://github.com/NousResearch/hermes-agent/pull/4798))

### Discord
- **频道控制** — ignored_channels 和 no_thread_channels 配置选项 ([#5975](https://github.com/NousResearch/hermes-agent/pull/5975))
- **技能注册为原生斜杠命令**通过共享网关逻辑 ([#5603](https://github.com/NousResearch/hermes-agent/pull/5603))
- **/approve、/deny、/queue、/background、/btw** 注册为原生斜杠命令 ([#4800](https://github.com/NousResearch/hermes-agent/pull/4800)，[#5477](https://github.com/NousResearch/hermes-agent/pull/5477))
- **启动时移除不必要的 members intent** + 令牌锁泄漏修复 ([#5302](https://github.com/NousResearch/hermes-agent/pull/5302))

### Slack
- **线程参与** — 在机器人发起的线程和被提及的线程中自动响应 ([#5897](https://github.com/NousResearch/hermes-agent/pull/5897))
- **edit_message 中的 mrkdwn** + 无 @提及的线程回复 ([#5733](https://github.com/NousResearch/hermes-agent/pull/5733))

### Matrix
- **一级功能对等** — 反应、已读回执、富文本格式、房间管理 ([#5275](https://github.com/NousResearch/hermes-agent/pull/5275))
- **MATRIX_REQUIRE_MENTION 和 MATRIX_AUTO_THREAD** 支持 ([#5106](https://github.com/NousResearch/hermes-agent/pull/5106))
- **全面可靠性** — 加密媒体、身份验证恢复、cron E2EE、Synapse 兼容 ([#5271](https://github.com/NousResearch/hermes-agent/pull/5271))
- **CJK 输入、E2EE 和重连**修复 ([#5665](https://github.com/NousResearch/hermes-agent/pull/5665))

### Signal
- **完整 MEDIA: 标签投递** — send_image_file、send_voice 和 send_video 已实现 ([#5602](https://github.com/NousResearch/hermes-agent/pull/5602))

### Mattermost
- **文件附件** — 当帖子有文件附件时将消息类型设为 DOCUMENT — @nericervin ([#5609](https://github.com/NousResearch/hermes-agent/pull/5609))

### 飞书
- **交互式卡片审批按钮** ([#6043](https://github.com/NousResearch/hermes-agent/pull/6043))
- **重连和 ACL** 修复 ([#5665](https://github.com/NousResearch/hermes-agent/pull/5665))

### Webhooks
- **`{__raw__}` 模板令牌**和论坛话题的 thread_id 透传 ([#5662](https://github.com/NousResearch/hermes-agent/pull/5662))

---

## 🖥️ CLI 与用户体验

### 交互式 CLI
- **推迟响应内容**直到推理块完成 ([#5773](https://github.com/NousResearch/hermes-agent/pull/5773))
- **终端调整大小时清除幽灵状态栏行** ([#4960](https://github.com/NousResearch/hermes-agent/pull/4960))
- **标准化粘贴文本中的 \r\n 和 \r 行尾** ([#4849](https://github.com/NousResearch/hermes-agent/pull/4849))
- **ChatConsole 错误、curses 滚动、皮肤感知横幅、git 状态**横幅修复 ([#5974](https://github.com/NousResearch/hermes-agent/pull/5974))
- **原生 Windows 图片粘贴**支持 ([#5917](https://github.com/NousResearch/hermes-agent/pull/5917))
- **--yolo 和其他标志**放在 'chat' 子命令前不再被静默丢弃 ([#5145](https://github.com/NousResearch/hermes-agent/pull/5145))

### 设置与配置
- **配置结构验证** — 在启动时用可操作的错误消息检测格式错误的 YAML ([#5426](https://github.com/NousResearch/hermes-agent/pull/5426))
- **集中式日志**到 `~/.hermes/logs/` — agent.log（INFO+）、errors.log（WARNING+），配合 `hermes logs` 命令 ([#5430](https://github.com/NousResearch/hermes-agent/pull/5430))
- **文档链接添加**到设置向导各部分 ([#5283](https://github.com/NousResearch/hermes-agent/pull/5283))
- **Doctor 诊断** — 同步提供商检查、配置迁移、WAL 和 mem0 诊断 ([#5077](https://github.com/NousResearch/hermes-agent/pull/5077))
- **超时调试日志**和面向用户的诊断改进 ([#5370](https://github.com/NousResearch/hermes-agent/pull/5370))
- **推理努力度统一**为仅 config.yaml ([#6118](https://github.com/NousResearch/hermes-agent/pull/6118))
- **永久命令允许列表**在启动时加载 ([#5076](https://github.com/NousResearch/hermes-agent/pull/5076))
- **`hermes auth remove`** 现在永久清除环境播种的凭据 ([#5285](https://github.com/NousResearch/hermes-agent/pull/5285))
- **捆绑技能同步到所有配置文件**在更新期间 ([#5795](https://github.com/NousResearch/hermes-agent/pull/5795))
- **`hermes update` 不再终止**新重启的网关服务 ([#5448](https://github.com/NousResearch/hermes-agent/pull/5448))
- **Subprocess.run() 超时**添加到所有网关 CLI 命令 ([#5424](https://github.com/NousResearch/hermes-agent/pull/5424))
- **Codex 刷新令牌重用时的可操作错误消息** — @tymrtn ([#5612](https://github.com/NousResearch/hermes-agent/pull/5612))
- **Google-workspace 技能脚本**现在可以直接运行 — @xinbenlv ([#5624](https://github.com/NousResearch/hermes-agent/pull/5624))

### Cron 系统
- **基于不活跃的 cron 超时** — 替代挂钟；活跃任务无限期运行 ([#5440](https://github.com/NousResearch/hermes-agent/pull/5440))
- **运行前脚本注入**用于数据收集和变更检测 ([#5082](https://github.com/NousResearch/hermes-agent/pull/5082))
- **投递失败跟踪**在任务状态中 ([#6042](https://github.com/NousResearch/hermes-agent/pull/6042))
- **投递指导**在 cron 提示中 — 停止 send_message 频繁调用 ([#5444](https://github.com/NousResearch/hermes-agent/pull/5444))
- **MEDIA 文件投递**为原生平台附件 ([#5921](https://github.com/NousResearch/hermes-agent/pull/5921))
- **[SILENT] 抑制**在响应中的任何位置生效 — @auspic7 ([#5654](https://github.com/NousResearch/hermes-agent/pull/5654))
- **Cron 路径遍历**加固 ([#5147](https://github.com/NousResearch/hermes-agent/pull/5147))

---

## 🔧 工具系统

### 终端与执行
- **远程后端上的 Execute_code** — 代码执行现在可以在 Docker、SSH、Modal 和其他远程终端后端上工作 ([#5088](https://github.com/NousResearch/hermes-agent/pull/5088))
- **常见 CLI 工具的退出码上下文**在终端结果中 — 帮助代理理解出了什么问题 ([#5144](https://github.com/NousResearch/hermes-agent/pull/5144))
- **渐进式子目录提示发现** — 代理在导航时学习项目结构 ([#5291](https://github.com/NousResearch/hermes-agent/pull/5291))
- **后台进程的 notify_on_complete** — 长时间运行任务完成时获得通知 ([#5779](https://github.com/NousResearch/hermes-agent/pull/5779))
- **Docker 环境配置** — 通过 docker_env 配置显式的容器环境变量 ([#4738](https://github.com/NousResearch/hermes-agent/pull/4738))
- **审批元数据包含**在终端工具结果中 ([#5141](https://github.com/NousResearch/hermes-agent/pull/5141))
- **workdir 参数清理**在终端工具的所有后端中 ([#5629](https://github.com/NousResearch/hermes-agent/pull/5629))
- **分离进程崩溃恢复**状态修正 ([#6101](https://github.com/NousResearch/hermes-agent/pull/6101))
- **带空格的代理浏览器路径**保留 — @Vasanthdev2004 ([#6077](https://github.com/NousResearch/hermes-agent/pull/6077))
- **macOS 上的可移植 base64 编码**用于图片读取 — @CharlieKerfoot ([#5657](https://github.com/NousResearch/hermes-agent/pull/5657))

### 浏览器
- **将托管浏览器提供商从 Browserbase 切换到 Browser Use** — @benbarclay ([#5750](https://github.com/NousResearch/hermes-agent/pull/5750))
- **Firecrawl 云浏览器**提供商 — @alt-glitch ([#5628](https://github.com/NousResearch/hermes-agent/pull/5628))
- **JS 评估**通过 browser_console 表达式参数 ([#5303](https://github.com/NousResearch/hermes-agent/pull/5303))
- **Windows 浏览器**修复 ([#5665](https://github.com/NousResearch/hermes-agent/pull/5665))

### MCP
- **MCP OAuth 2.1 PKCE** — 完全符合标准的 OAuth 客户端支持 ([#5420](https://github.com/NousResearch/hermes-agent/pull/5420))
- **OSV 恶意软件检查**用于 MCP 扩展包 ([#5305](https://github.com/NousResearch/hermes-agent/pull/5305))
- **优先使用 structuredContent 而非 text** + no_mcp 哨兵 ([#5979](https://github.com/NousResearch/hermes-agent/pull/5979))
- **对 MCP 服务器名称抑制未知工具集警告** ([#5279](https://github.com/NousResearch/hermes-agent/pull/5279))

### 网络与文件
- **.zip 文档支持** + 自动将缓存目录挂载到远程后端 ([#4846](https://github.com/NousResearch/hermes-agent/pull/4846))
- **在 send_message 错误中编辑查询密钥** — @WAXLYY ([#5650](https://github.com/NousResearch/hermes-agent/pull/5650))

### 委托
- **凭据池共享** + 子代理的工作区路径提示 ([#5748](https://github.com/NousResearch/hermes-agent/pull/5748))

### ACP（VS Code / Zed / JetBrains）
- **聚合 ACP 改进** — 身份验证兼容、协议修复、命令广告、委托、SSE 事件 ([#5292](https://github.com/NousResearch/hermes-agent/pull/5292))

---

## 🧩 技能生态系统

### 技能系统
- **技能配置接口** — 技能可以声明必需的 config.yaml 设置，在设置期间提示，在加载时注入 ([#5635](https://github.com/NousResearch/hermes-agent/pull/5635))
- **插件 CLI 注册系统** — 插件注册自己的 CLI 子命令而无需修改 main.py ([#5295](https://github.com/NousResearch/hermes-agent/pull/5295))
- **请求范围的 API 钩子**，带有工具调用关联 ID，用于插件 ([#5427](https://github.com/NousResearch/hermes-agent/pull/5427))
- **会话生命周期钩子** — on_session_finalize 和 on_session_reset，用于 CLI + 网关 ([#6129](https://github.com/NousResearch/hermes-agent/pull/6129))
- **安装期间提示必需的环境变量** — @kshitijk4poor ([#5470](https://github.com/NousResearch/hermes-agent/pull/5470))
- **插件名称验证** — 拒绝解析为插件根目录的名称 ([#5368](https://github.com/NousResearch/hermes-agent/pull/5368))
- **pre_llm_call 插件上下文**移至用户消息以保留提示缓存 ([#5146](https://github.com/NousResearch/hermes-agent/pull/5146))

### 新增与更新技能
- **popular-web-designs** — 54 个生产网站设计系统 ([#5194](https://github.com/NousResearch/hermes-agent/pull/5194))
- **p5js creative coding** — @SHL0MS ([#5600](https://github.com/NousResearch/hermes-agent/pull/5600))
- **manim-video** — 数学和技术动画 — @SHL0MS ([#4930](https://github.com/NousResearch/hermes-agent/pull/4930))
- **llm-wiki** — Karpathy 的 LLM Wiki 技能 ([#5635](https://github.com/NousResearch/hermes-agent/pull/5635))
- **gitnexus-explorer** — 代码库索引和知识服务 ([#5208](https://github.com/NousResearch/hermes-agent/pull/5208))
- **research-paper-writing** — AI-Scientist 和 GPT-Researcher 模式 — @SHL0MS ([#5421](https://github.com/NousResearch/hermes-agent/pull/5421))
- **blogwatcher** 更新到 JulienTant 的 fork ([#5759](https://github.com/NousResearch/hermes-agent/pull/5759))
- **claude-code 技能**全面重写 v2.0 + v2.2 ([#5155](https://github.com/NousResearch/hermes-agent/pull/5155)，[#5158](https://github.com/NousResearch/hermes-agent/pull/5158))
- **代码验证技能**合并为一个 ([#4854](https://github.com/NousResearch/hermes-agent/pull/4854))
- **Manim CE 参考文档**扩展 — 几何、动画、LaTeX — @leotrs ([#5791](https://github.com/NousResearch/hermes-agent/pull/5791))
- **Manim-video 参考** — 设计思维、更新器、论文解说、装饰、生产质量 — @SHL0MS ([#5588](https://github.com/NousResearch/hermes-agent/pull/5588)，[#5408](https://github.com/NousResearch/hermes-agent/pull/5408))

---

## 🔒 安全与可靠性

### 安全加固
- **整合安全** — SSRF 防护、时序攻击缓解、tar 遍历预防、凭据泄漏保护 ([#5944](https://github.com/NousResearch/hermes-agent/pull/5944))
- **跨会话隔离** + cron 路径遍历加固 ([#5613](https://github.com/NousResearch/hermes-agent/pull/5613))
- **workdir 参数清理**在终端工具的所有后端中 ([#5629](https://github.com/NousResearch/hermes-agent/pull/5629))
- **审批 'once' 会话升级**预防 + cron 投递平台验证 ([#5280](https://github.com/NousResearch/hermes-agent/pull/5280))
- **配置文件范围的 Google Workspace OAuth 令牌**保护 ([#4910](https://github.com/NousResearch/hermes-agent/pull/4910))

### 可靠性
- **激进的工作树和分支清理**以防止累积 ([#6134](https://github.com/NousResearch/hermes-agent/pull/6134))
- **编辑正则中的 O(n^2) 灾难性回溯**已修复 — 大输出上 100 倍改进 ([#4962](https://github.com/NousResearch/hermes-agent/pull/4962))
- **跨核心、网络、委托和浏览器工具的运行时稳定性修复** ([#4843](https://github.com/NousResearch/hermes-agent/pull/4843))
- **API 服务器流式修复** + 对话历史支持 ([#5977](https://github.com/NousResearch/hermes-agent/pull/5977))
- **OpenViking API 端点路径**和响应解析修正 ([#5078](https://github.com/NousResearch/hermes-agent/pull/5078))

---

## 🐛 重要 Bug 修复

- **9 个社区 Bug 修复被挽救** — 网关、cron、依赖、macOS launchd 合并为一批 ([#5288](https://github.com/NousResearch/hermes-agent/pull/5288))
- **批量核心 Bug 修复** — 模型配置、会话重置、别名回退、launchctl、委托、原子写入 ([#5630](https://github.com/NousResearch/hermes-agent/pull/5630))
- **批量网关/平台修复** — matrix E2EE、CJK 输入、Windows 浏览器、飞书重连 + ACL ([#5665](https://github.com/NousResearch/hermes-agent/pull/5665))
- **移除过时的测试跳过**，正则回溯、文件搜索 Bug 和测试不稳定性 ([#4969](https://github.com/NousResearch/hermes-agent/pull/4969))
- **Nix flake** — 读取版本、重新生成 uv.lock、添加 hermes_logging — @alt-glitch ([#5651](https://github.com/NousResearch/hermes-agent/pull/5651))
- **小写变量编辑**回归测试 ([#5185](https://github.com/NousResearch/hermes-agent/pull/5185))

---

## 🧪 测试

- **修复 14 个文件中的 57 个失败 CI 测试** ([#5823](https://github.com/NousResearch/hermes-agent/pull/5823))
- **测试套件重架构** + CI 失败修复 — @alt-glitch ([#5946](https://github.com/NousResearch/hermes-agent/pull/5946))
- **代码库范围的 lint 清理** — 未使用的导入、死代码和低效模式 ([#5821](https://github.com/NousResearch/hermes-agent/pull/5821))
- **移除 browser_close 工具** — 自动清理处理 ([#5792](https://github.com/NousResearch/hermes-agent/pull/5792))

---

## 📚 文档

- **全面的文档审计** — 修复过时信息、扩展薄弱页面、增加深度 ([#5393](https://github.com/NousResearch/hermes-agent/pull/5393))
- **修复文档和代码库之间的 40+ 差异** ([#5818](https://github.com/NousResearch/hermes-agent/pull/5818))
- **记录上周 PR 中的 13 个功能** ([#5815](https://github.com/NousResearch/hermes-agent/pull/5815))
- **指南部分大修** — 修复现有 + 添加 3 个新教程 ([#5735](https://github.com/NousResearch/hermes-agent/pull/5735))
- **挽救 4 个文档 PR** — Docker 设置、更新后验证、本地 LLM 指南、signal-cli 安装 ([#5727](https://github.com/NousResearch/hermes-agent/pull/5727))
- **Discord 配置参考** ([#5386](https://github.com/NousResearch/hermes-agent/pull/5386))
- **社区 FAQ 条目**用于常见工作流和故障排除 ([#4797](https://github.com/NousResearch/hermes-agent/pull/4797))
- **WSL2 网络指南**用于本地模型服务器 ([#5616](https://github.com/NousResearch/hermes-agent/pull/5616))
- **Honcho CLI 参考** + 插件 CLI 注册文档 ([#5308](https://github.com/NousResearch/hermes-agent/pull/5308))
- **Obsidian Headless 设置**用于 llm-wiki 中的服务器 ([#5660](https://github.com/NousResearch/hermes-agent/pull/5660))
- **Hermes Mod 视觉皮肤编辑器**添加到皮肤页面 ([#6095](https://github.com/NousResearch/hermes-agent/pull/6095))

---

## 👥 贡献者

### 核心
- **@teknium1** — 179 个 PR

### 顶级社区贡献者
- **@SHL0MS**（7 个 PR）— p5js creative coding 技能、manim-video 技能 + 5 个参考扩展、research-paper-writing、Nous OAuth 修复、manim 字体修复
- **@alt-glitch**（3 个 PR）— Firecrawl 云浏览器提供商、测试重架构 + CI 修复、Nix flake 修复
- **@benbarclay**（2 个 PR）— Browser Use 托管提供商切换、Nous portal base URL 修复
- **@CharlieKerfoot**（2 个 PR）— macOS 可移植 base64 编码、线程安全的 PairingStore
- **@WAXLYY**（2 个 PR）— send_message 密钥编辑、网关媒体 URL 清理
- **@MadKangYu**（2 个 PR）— Telegram 日志噪声减少、温度受限模型的上下文压缩修复

### 所有贡献者
@alt-glitch, @austinpickett, @auspic7, @benbarclay, @CharlieKerfoot, @GratefulDave, @kshitijk4poor, @leotrs, @lumethegreat, @MadKangYu, @nericervin, @ryanautomated, @SHL0MS, @techguysimon, @tymrtn, @Vasanthdev2004, @WAXLYY, @xinbenlv

---

**完整变更日志**：[v2026.4.3...v2026.4.8](https://github.com/NousResearch/hermes-agent/compare/v2026.4.3...v2026.4.8)
