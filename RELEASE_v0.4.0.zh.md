# Hermes Agent v0.4.0 (v2026.3.23)

**发布日期：** 2026年3月23日

> 平台扩展发布版 — OpenAI 兼容 API 服务器、6 个新消息适配器、4 个新推理提供商、带 OAuth 2.1 的 MCP 服务器管理、@ 上下文引用、网关提示缓存、默认启用流式传输，以及 200+ 个 Bug 修复的全面可靠性改进。

---

## ✨ 亮点

- **OpenAI 兼容 API 服务器** — 将 Hermes 暴露为 `/v1/chat/completions` 端点，带有用于定时任务管理的新 `/api/jobs` REST API，并通过输入限制、字段白名单、SQLite 支持的响应持久化和 CORS 来源保护进行加固 ([#1756](https://github.com/NousResearch/hermes-agent/pull/1756), [#2450](https://github.com/NousResearch/hermes-agent/pull/2450), [#2456](https://github.com/NousResearch/hermes-agent/pull/2456), [#2451](https://github.com/NousResearch/hermes-agent/pull/2451), [#2472](https://github.com/NousResearch/hermes-agent/pull/2472))

- **6 个新消息平台适配器** — Signal、钉钉、SMS（Twilio）、Mattermost、Matrix 和 Webhook 适配器加入 Telegram、Discord 和 WhatsApp。网关以指数退避自动重连失败的平台 ([#2206](https://github.com/NousResearch/hermes-agent/pull/2206), [#1685](https://github.com/NousResearch/hermes-agent/pull/1685), [#1688](https://github.com/NousResearch/hermes-agent/pull/1688), [#1683](https://github.com/NousResearch/hermes-agent/pull/1683), [#2166](https://github.com/NousResearch/hermes-agent/pull/2166), [#2584](https://github.com/NousResearch/hermes-agent/pull/2584))

- **@ 上下文引用** — Claude Code 风格的 `@file` 和 `@url` 上下文注入，CLI 中支持 Tab 补全 ([#2343](https://github.com/NousResearch/hermes-agent/pull/2343), [#2482](https://github.com/NousResearch/hermes-agent/pull/2482))

- **4 个新推理提供商** — GitHub Copilot（OAuth + token 验证）、阿里云 / DashScope、Kilo Code 和 OpenCode Zen/Go ([#1924](https://github.com/NousResearch/hermes-agent/pull/1924), [#1879](https://github.com/NousResearch/hermes-agent/pull/1879) 由 @mchzimm, [#1673](https://github.com/NousResearch/hermes-agent/pull/1673), [#1666](https://github.com/NousResearch/hermes-agent/pull/1666), [#1650](https://github.com/NousResearch/hermes-agent/pull/1650))

- **MCP 服务器管理 CLI** — `hermes mcp` 命令用于安装、配置和认证 MCP 服务器，支持完整的 OAuth 2.1 PKCE 流程 ([#2465](https://github.com/NousResearch/hermes-agent/pull/2465))

- **网关提示缓存** — 按会话缓存 AIAgent 实例，跨轮次保留 Anthropic 提示缓存，大幅降低长对话的成本 ([#2282](https://github.com/NousResearch/hermes-agent/pull/2282), [#2284](https://github.com/NousResearch/hermes-agent/pull/2284), [#2361](https://github.com/NousResearch/hermes-agent/pull/2361))

- **上下文压缩全面改造** — 结构化摘要及迭代更新、token 预算尾部保护、可配置的摘要端点和备用模型支持 ([#2323](https://github.com/NousResearch/hermes-agent/pull/2323), [#1727](https://github.com/NousResearch/hermes-agent/pull/1727), [#2224](https://github.com/NousResearch/hermes-agent/pull/2224))

- **默认启用流式传输** — CLI 默认启用流式传输，流式模式下正确显示加载动画/工具进度，并修复了大量换行和连接问题 ([#2340](https://github.com/NousResearch/hermes-agent/pull/2340), [#2161](https://github.com/NousResearch/hermes-agent/pull/2161), [#2258](https://github.com/NousResearch/hermes-agent/pull/2258))

---

## 🖥️ CLI 与用户体验

### 新命令与交互
- **@ 上下文补全** — Tab 补全的 `@file`/`@url` 引用，将文件内容或网页注入对话 ([#2482](https://github.com/NousResearch/hermes-agent/pull/2482), [#2343](https://github.com/NousResearch/hermes-agent/pull/2343))
- **`/statusbar`** — 切换显示模型+提供商信息的持久配置栏 ([#2240](https://github.com/NousResearch/hermes-agent/pull/2240), [#1917](https://github.com/NousResearch/hermes-agent/pull/1917))
- **`/queue`** — 在不中断当前运行的情况下为代理排队提示 ([#2191](https://github.com/NousResearch/hermes-agent/pull/2191), [#2469](https://github.com/NousResearch/hermes-agent/pull/2469))
- **`/permission`** — 在会话期间动态切换审批模式 ([#2207](https://github.com/NousResearch/hermes-agent/pull/2207))
- **`/browser`** — 从 CLI 进行交互式浏览器会话 ([#2273](https://github.com/NousResearch/hermes-agent/pull/2273), [#1814](https://github.com/NousResearch/hermes-agent/pull/1814))
- **`/cost`** — 网关模式下的实时定价和使用追踪 ([#2180](https://github.com/NousResearch/hermes-agent/pull/2180))
- **`/approve` 和 `/deny`** — 用显式命令替换网关中的纯文本审批 ([#2002](https://github.com/NousResearch/hermes-agent/pull/2002))

### 流式传输与显示
- CLI 默认启用流式传输 ([#2340](https://github.com/NousResearch/hermes-agent/pull/2340))
- 流式模式下显示加载动画和工具进度 ([#2161](https://github.com/NousResearch/hermes-agent/pull/2161))
- 启用 `show_reasoning` 时显示推理/思考块 ([#2118](https://github.com/NousResearch/hermes-agent/pull/2118))
- CLI 和网关的上下文压力警告 ([#2159](https://github.com/NousResearch/hermes-agent/pull/2159))
- 修复：流式块之间无空格连接 ([#2258](https://github.com/NousResearch/hermes-agent/pull/2258))
- 修复：迭代边界换行阻止流连接 ([#2413](https://github.com/NousResearch/hermes-agent/pull/2413))
- 修复：延迟流式换行防止空行堆叠 ([#2473](https://github.com/NousResearch/hermes-agent/pull/2473))
- 修复：非 TTY 环境下抑制加载动画 ([#2216](https://github.com/NousResearch/hermes-agent/pull/2216))
- 修复：在 API 错误消息中显示提供商和端点 ([#2266](https://github.com/NousResearch/hermes-agent/pull/2266))
- 修复：解决状态打印中乱码的 ANSI 转义码 ([#2448](https://github.com/NousResearch/hermes-agent/pull/2448))
- 修复：将金色 ANSI 颜色更新为真彩色格式 ([#2246](https://github.com/NousResearch/hermes-agent/pull/2246))
- 修复：规范化工具集标签并在横幅中使用皮肤颜色 ([#1912](https://github.com/NousResearch/hermes-agent/pull/1912))

### CLI 完善
- 修复：退出时防止 'Press ENTER to continue...' ([#2555](https://github.com/NousResearch/hermes-agent/pull/2555))
- 修复：代理循环中刷新 stdout 防止 macOS 显示冻结 ([#1654](https://github.com/NousResearch/hermes-agent/pull/1654))
- 修复：`hermes setup` 遇到权限错误时显示可读错误 ([#2196](https://github.com/NousResearch/hermes-agent/pull/2196))
- 修复：`/stop` 命令崩溃 + 流式媒体传递中的 UnboundLocalError ([#2463](https://github.com/NousResearch/hermes-agent/pull/2463))
- 修复：允许没有 API 密钥的自定义/本地端点 ([#2556](https://github.com/NousResearch/hermes-agent/pull/2556))
- 修复：Ghostty/WezTerm 的 Kitty 键盘协议 Shift+Enter（尝试+因 prompt_toolkit 崩溃回退）([#2345](https://github.com/NousResearch/hermes-agent/pull/2345), [#2349](https://github.com/NousResearch/hermes-agent/pull/2349))

### 配置
- **`${ENV_VAR}` 替换** — config.yaml 中的环境变量替换 ([#2684](https://github.com/NousResearch/hermes-agent/pull/2684))
- **实时配置重载** — config.yaml 更改无需重启即可生效 ([#2210](https://github.com/NousResearch/hermes-agent/pull/2210))
- **`custom_models.yaml`** — 用户管理的模型添加 ([#2214](https://github.com/NousResearch/hermes-agent/pull/2214))
- **基于优先级的上下文文件选择** + CLAUDE.md 支持 ([#2301](https://github.com/NousResearch/hermes-agent/pull/2301))
- **合并嵌套 YAML 节** — 配置更新时合并而非替换 ([#2213](https://github.com/NousResearch/hermes-agent/pull/2213))
- 修复：config.yaml 提供商密钥静默覆盖环境变量 ([#2272](https://github.com/NousResearch/hermes-agent/pull/2272))
- 修复：记录警告而非静默吞掉 config.yaml 错误 ([#2683](https://github.com/NousResearch/hermes-agent/pull/2683))
- 修复：已禁用的工具集在 `hermes tools` 后自行重新启用 ([#2268](https://github.com/NousResearch/hermes-agent/pull/2268))
- 修复：平台默认工具集静默覆盖工具取消选择 ([#2624](https://github.com/NousResearch/hermes-agent/pull/2624))
- 修复：遵守纯 YAML `approvals.mode: off` ([#2620](https://github.com/NousResearch/hermes-agent/pull/2620))
- 修复：`hermes update` 使用 `.[all]` extras 并带备用方案 ([#1728](https://github.com/NousResearch/hermes-agent/pull/1728))
- 修复：`hermes update` 在 stash 冲突时重置工作树前先提示 ([#2390](https://github.com/NousResearch/hermes-agent/pull/2390))
- 修复：update/install 中使用 git pull --rebase 避免分支分歧错误 ([#2274](https://github.com/NousResearch/hermes-agent/pull/2274))
- 修复：为全新 macOS 安装添加 zprofile 备用方案并创建 zshrc ([#2320](https://github.com/NousResearch/hermes-agent/pull/2320))
- 修复：移除 `ANTHROPIC_BASE_URL` 环境变量以避免冲突 ([#1675](https://github.com/NousResearch/hermes-agent/pull/1675))
- 修复：IMAP 密码已在密钥环或环境中时不再询问 ([#2212](https://github.com/NousResearch/hermes-agent/pull/2212))
- 修复：OpenCode Zen/Go 显示 OpenRouter 模型而非自己的模型 ([#2277](https://github.com/NousResearch/hermes-agent/pull/2277))

---

## 🏗️ 核心代理与架构

### 新提供商
- **GitHub Copilot** — 完整的 OAuth 认证、API 路由、token 验证和 400k 上下文 ([#1924](https://github.com/NousResearch/hermes-agent/pull/1924), [#1896](https://github.com/NousResearch/hermes-agent/pull/1896), [#1879](https://github.com/NousResearch/hermes-agent/pull/1879) 由 @mchzimm, [#2507](https://github.com/NousResearch/hermes-agent/pull/2507))
- **阿里云 / DashScope** — 完整集成 DashScope v1 运行时，模型名点号保留和 401 认证修复 ([#1673](https://github.com/NousResearch/hermes-agent/pull/1673), [#2332](https://github.com/NousResearch/hermes-agent/pull/2332), [#2459](https://github.com/NousResearch/hermes-agent/pull/2459))
- **Kilo Code** — 一级推理提供商 ([#1666](https://github.com/NousResearch/hermes-agent/pull/1666))
- **OpenCode Zen 和 OpenCode Go** — 新提供商后端 ([#1650](https://github.com/NousResearch/hermes-agent/pull/1650), [#2393](https://github.com/NousResearch/hermes-agent/pull/2393) 由 @0xbyt4)
- **NeuTTS** — 本地 TTS 提供商后端，内置设置流程，替代旧的可选技能 ([#1657](https://github.com/NousResearch/hermes-agent/pull/1657), [#1664](https://github.com/NousResearch/hermes-agent/pull/1664))

### 提供商改进
- **积极备用** — 速率限制错误时切换到备用模型 ([#1730](https://github.com/NousResearch/hermes-agent/pull/1730))
- **端点元数据** — 自定义模型上下文和定价；查询本地服务器获取实际上下文窗口大小 ([#1906](https://github.com/NousResearch/hermes-agent/pull/1906), [#2091](https://github.com/NousResearch/hermes-agent/pull/2091) 由 @dusterbloom)
- **上下文长度检测全面改造** — models.dev 集成、提供商感知解析、自定义端点模糊匹配、llama.cpp 的 `/v1/props` ([#2158](https://github.com/NousResearch/hermes-agent/pull/2158), [#2051](https://github.com/NousResearch/hermes-agent/pull/2051), [#2403](https://github.com/NousResearch/hermes-agent/pull/2403))
- **模型目录更新** — gpt-5.4-mini、gpt-5.4-nano、healer-alpha、haiku-4.5、minimax-m2.7、claude 4.6（1M 上下文）([#1913](https://github.com/NousResearch/hermes-agent/pull/1913), [#1915](https://github.com/NousResearch/hermes-agent/pull/1915), [#1900](https://github.com/NousResearch/hermes-agent/pull/1900), [#2155](https://github.com/NousResearch/hermes-agent/pull/2155), [#2474](https://github.com/NousResearch/hermes-agent/pull/2474))
- **自定义端点改进** — config.yaml 中的 `model.base_url`、responses API 的 `api_mode` 覆盖、允许无 API 密钥的端点、缺少密钥时快速失败 ([#2330](https://github.com/NousResearch/hermes-agent/pull/2330), [#1651](https://github.com/NousResearch/hermes-agent/pull/1651), [#2556](https://github.com/NousResearch/hermes-agent/pull/2556), [#2445](https://github.com/NousResearch/hermes-agent/pull/2445), [#1994](https://github.com/NousResearch/hermes-agent/pull/1994), [#1998](https://github.com/NousResearch/hermes-agent/pull/1998))
- 将模型和提供商注入系统提示 ([#1929](https://github.com/NousResearch/hermes-agent/pull/1929))
- 将 `api_mode` 绑定到提供商配置而非环境变量 ([#1656](https://github.com/NousResearch/hermes-agent/pull/1656))
- 修复：防止 Anthropic token 泄漏到第三方 `anthropic_messages` 提供商 ([#2389](https://github.com/NousResearch/hermes-agent/pull/2389))
- 修复：防止 Anthropic 备用继承非 Anthropic 的 `base_url` ([#2388](https://github.com/NousResearch/hermes-agent/pull/2388))
- 修复：`auxiliary_is_nous` 标志永不重置 — 将 Nous 标签泄漏到其他提供商 ([#1713](https://github.com/NousResearch/hermes-agent/pull/1713))
- 修复：Anthropic `tool_choice 'none'` 仍允许工具调用 ([#1714](https://github.com/NousResearch/hermes-agent/pull/1714))
- 修复：Mistral 解析器嵌套 JSON 备用提取 ([#2335](https://github.com/NousResearch/hermes-agent/pull/2335))
- 修复：MiniMax 401 认证通过默认使用 `anthropic_messages` 解决 ([#2103](https://github.com/NousResearch/hermes-agent/pull/2103))
- 修复：模型家族匹配不区分大小写 ([#2350](https://github.com/NousResearch/hermes-agent/pull/2350))
- 修复：激活检查中忽略占位符提供商密钥 ([#2358](https://github.com/NousResearch/hermes-agent/pull/2358))
- 修复：上下文长度检测中保留 Ollama model:tag 冒号 ([#2149](https://github.com/NousResearch/hermes-agent/pull/2149))
- 修复：在启动门中识别 Claude Code OAuth 凭证 ([#1663](https://github.com/NousResearch/hermes-agent/pull/1663))
- 修复：动态检测 Claude Code 版本用于 OAuth user-agent ([#1670](https://github.com/NousResearch/hermes-agent/pull/1670))
- 修复：刷新/备用后 OAuth 标志过期 ([#1890](https://github.com/NousResearch/hermes-agent/pull/1890))
- 修复：辅助客户端跳过过期的 Codex JWT ([#2397](https://github.com/NousResearch/hermes-agent/pull/2397))

### 代理循环
- **网关提示缓存** — 按会话缓存 AIAgent、保留助手轮次、修复会话恢复 ([#2282](https://github.com/NousResearch/hermes-agent/pull/2282), [#2284](https://github.com/NousResearch/hermes-agent/pull/2284), [#2361](https://github.com/NousResearch/hermes-agent/pull/2361))
- **上下文压缩全面改造** — 结构化摘要、迭代更新、token 预算尾部保护、可配置 `summary_base_url` ([#2323](https://github.com/NousResearch/hermes-agent/pull/2323), [#1727](https://github.com/NousResearch/hermes-agent/pull/1727), [#2224](https://github.com/NousResearch/hermes-agent/pull/2224))
- **预调用清理和调用后工具护栏** ([#1732](https://github.com/NousResearch/hermes-agent/pull/1732))
- **自动恢复** — 提供商拒绝 `tool_choice` 时不带该参数重试 ([#2174](https://github.com/NousResearch/hermes-agent/pull/2174))
- **后台记忆/技能审查**替代内联提示 ([#2235](https://github.com/NousResearch/hermes-agent/pull/2235))
- **SOUL.md 作为主要代理身份**替代硬编码默认值 ([#1922](https://github.com/NousResearch/hermes-agent/pull/1922))
- 修复：上下文压缩期间防止静默工具结果丢失 ([#1993](https://github.com/NousResearch/hermes-agent/pull/1993))
- 修复：工具调用恢复中处理空/null 函数参数 ([#2163](https://github.com/NousResearch/hermes-agent/pull/2163))
- 修复：优雅处理 API 拒绝响应而非崩溃 ([#2156](https://github.com/NousResearch/hermes-agent/pull/2156))
- 修复：防止格式错误的工具调用导致代理循环卡住 ([#2114](https://github.com/NousResearch/hermes-agent/pull/2114))
- 修复：将 JSON 解析错误返回给模型而非用空参数调度 ([#2342](https://github.com/NousResearch/hermes-agent/pull/2342))
- 修复：混合类型时连续助手消息合并丢弃内容 ([#1703](https://github.com/NousResearch/hermes-agent/pull/1703))
- 修复：JSON 恢复和错误处理器中的消息角色交替违规 ([#1722](https://github.com/NousResearch/hermes-agent/pull/1722))
- 修复：`compression_attempts` 每次迭代重置 — 允许无限压缩 ([#1723](https://github.com/NousResearch/hermes-agent/pull/1723))
- 修复：`length_continue_retries` 永不重置 — 后续截断获得更少重试 ([#1717](https://github.com/NousResearch/hermes-agent/pull/1717))
- 修复：压缩器摘要角色违反连续角色约束 ([#1720](https://github.com/NousResearch/hermes-agent/pull/1720), [#1743](https://github.com/NousResearch/hermes-agent/pull/1743))
- 修复：移除硬编码的 `gemini-3-flash-preview` 作为默认摘要模型 ([#2464](https://github.com/NousResearch/hermes-agent/pull/2464))
- 修复：正确处理空工具结果 ([#2201](https://github.com/NousResearch/hermes-agent/pull/2201))
- 修复：`tool_calls` 列表中 None 条目崩溃 ([#2209](https://github.com/NousResearch/hermes-agent/pull/2209) 由 @0xbyt4, [#2316](https://github.com/NousResearch/hermes-agent/pull/2316))
- 修复：工作线程中的每线程持久事件循环 ([#2214](https://github.com/NousResearch/hermes-agent/pull/2214) 由 @jquesnelle)
- 修复：异步工具并行运行时防止 '事件循环已在运行' ([#2207](https://github.com/NousResearch/hermes-agent/pull/2207))
- 修复：从源头剥离 ANSI — 在到达模型前清理终端输出 ([#2115](https://github.com/NousResearch/hermes-agent/pull/2115))
- 修复：跳过 OpenRouter 中 role:tool 的顶级 `cache_control` ([#2391](https://github.com/NousResearch/hermes-agent/pull/2391))
- 修复：委托工具 — 子构造变异全局前保存父工具名称 ([#2083](https://github.com/NousResearch/hermes-agent/pull/2083) 由 @ygd58, [#1894](https://github.com/NousResearch/hermes-agent/pull/1894))
- 修复：仅在空字符串时才剥离最后一条助手消息 ([#2326](https://github.com/NousResearch/hermes-agent/pull/2326))

### 会话与记忆
- **会话搜索**和管理斜杠命令 ([#2198](https://github.com/NousResearch/hermes-agent/pull/2198))
- **自动会话标题**和 `.hermes.md` 项目配置 ([#1712](https://github.com/NousResearch/hermes-agent/pull/1712))
- 修复：并发记忆写入静默丢弃条目 — 添加文件锁 ([#1726](https://github.com/NousResearch/hermes-agent/pull/1726))
- 修复：`session_search` 中默认搜索所有来源 ([#1892](https://github.com/NousResearch/hermes-agent/pull/1892))
- 修复：处理带连字符的 FTS5 查询并保留引号字面量 ([#1776](https://github.com/NousResearch/hermes-agent/pull/1776))
- 修复：`load_transcript` 中跳过损坏行而非崩溃 ([#1744](https://github.com/NousResearch/hermes-agent/pull/1744))
- 修复：规范化会话键防止大小写敏感的重复 ([#2157](https://github.com/NousResearch/hermes-agent/pull/2157))
- 修复：没有会话存在时防止 `session_search` 崩溃 ([#2194](https://github.com/NousResearch/hermes-agent/pull/2194))
- 修复：新会话时重置 token 计数器以准确显示使用情况 ([#2101](https://github.com/NousResearch/hermes-agent/pull/2101) 由 @InB4DevOps)
- 修复：防止刷新代理的过时记忆覆盖 ([#2687](https://github.com/NousResearch/hermes-agent/pull/2687))
- 修复：移除合成错误消息注入，修复重复失败后的会话恢复 ([#2303](https://github.com/NousResearch/hermes-agent/pull/2303))
- 修复：`--resume` 安静模式现在传递 conversation_history ([#2357](https://github.com/NousResearch/hermes-agent/pull/2357))
- 修复：批处理模式中统一恢复逻辑 ([#2331](https://github.com/NousResearch/hermes-agent/pull/2331))

### Honcho 记忆
- Honcho 配置修复和 @ 上下文引用集成 ([#2343](https://github.com/NousResearch/hermes-agent/pull/2343))
- 自托管 / Docker 配置文档 ([#2475](https://github.com/NousResearch/hermes-agent/pull/2475))

---

## 📱 消息平台（网关）

### 新平台适配器
- **Signal Messenger** — 完整适配器，包含附件处理、群消息过滤和"自己发给自己"回声保护 ([#2206](https://github.com/NousResearch/hermes-agent/pull/2206), [#2400](https://github.com/NousResearch/hermes-agent/pull/2400), [#2297](https://github.com/NousResearch/hermes-agent/pull/2297), [#2156](https://github.com/NousResearch/hermes-agent/pull/2156))
- **钉钉** — 适配器，包含网关集成和设置文档 ([#1685](https://github.com/NousResearch/hermes-agent/pull/1685), [#1690](https://github.com/NousResearch/hermes-agent/pull/1690), [#1692](https://github.com/NousResearch/hermes-agent/pull/1692))
- **SMS（Twilio）** ([#1688](https://github.com/NousResearch/hermes-agent/pull/1688))
- **Mattermost** — 支持 @提及频道过滤 ([#1683](https://github.com/NousResearch/hermes-agent/pull/1683), [#2443](https://github.com/NousResearch/hermes-agent/pull/2443))
- **Matrix** — 支持视觉和图像缓存 ([#1683](https://github.com/NousResearch/hermes-agent/pull/1683), [#2520](https://github.com/NousResearch/hermes-agent/pull/2520))
- **Webhook** — 外部事件触发的平台适配器 ([#2166](https://github.com/NousResearch/hermes-agent/pull/2166))
- **OpenAI 兼容 API 服务器** — `/v1/chat/completions` 端点及 `/api/jobs` 定时任务管理 ([#1756](https://github.com/NousResearch/hermes-agent/pull/1756), [#2450](https://github.com/NousResearch/hermes-agent/pull/2450), [#2456](https://github.com/NousResearch/hermes-agent/pull/2456))

### Telegram 改进
- MarkdownV2 支持 — 删除线、剧透、引用块、转义括号/大括号/反斜杠/反引号 ([#2199](https://github.com/NousResearch/hermes-agent/pull/2199), [#2200](https://github.com/NousResearch/hermes-agent/pull/2200) 由 @llbn, [#2386](https://github.com/NousResearch/hermes-agent/pull/2386))
- 自动检测 HTML 标签并使用 `parse_mode=HTML` ([#1709](https://github.com/NousResearch/hermes-agent/pull/1709))
- Telegram 群组视觉支持 + 基于线程的会话 ([#2153](https://github.com/NousResearch/hermes-agent/pull/2153))
- 网络中断后自动重连轮询 ([#2517](https://github.com/NousResearch/hermes-agent/pull/2517))
- 调度前聚合拆分的文本消息 ([#1674](https://github.com/NousResearch/hermes-agent/pull/1674))
- 修复：流式配置桥接、未修改、泛洪控制 ([#1782](https://github.com/NousResearch/hermes-agent/pull/1782), [#1783](https://github.com/NousResearch/hermes-agent/pull/1783))
- 修复：edited_message 事件崩溃 ([#2074](https://github.com/NousResearch/hermes-agent/pull/2074))
- 修复：放弃前重试 409 轮询冲突 ([#2312](https://github.com/NousResearch/hermes-agent/pull/2312))
- 修复：通过 `platform:chat_id:thread_id` 格式投递主题 ([#2455](https://github.com/NousResearch/hermes-agent/pull/2455))

### Discord 改进
- 文档缓存和文本文件注入 ([#2503](https://github.com/NousResearch/hermes-agent/pull/2503))
- DM 的持久打字指示器 ([#2468](https://github.com/NousResearch/hermes-agent/pull/2468))
- Discord DM 视觉 — 内联图片 + 附件分析 ([#2186](https://github.com/NousResearch/hermes-agent/pull/2186))
- 跨网关重启持久化线程参与 ([#1661](https://github.com/NousResearch/hermes-agent/pull/1661))
- 修复：非 ASCII 公会名称导致网关崩溃 ([#2302](https://github.com/NousResearch/hermes-agent/pull/2302))
- 修复：线程权限错误 ([#2073](https://github.com/NousResearch/hermes-agent/pull/2073))
- 修复：线程中的斜杠事件路由 ([#2460](https://github.com/NousResearch/hermes-agent/pull/2460))
- 修复：移除有 Bug 的跟进消息 + `/ask` 命令 ([#1836](https://github.com/NousResearch/hermes-agent/pull/1836))
- 修复：优雅的 WebSocket 重连 ([#2127](https://github.com/NousResearch/hermes-agent/pull/2127))
- 修复：启用流式时语音频道 TTS ([#2322](https://github.com/NousResearch/hermes-agent/pull/2322))

### WhatsApp 及其他适配器
- WhatsApp：出站 `send_message` 路由 ([#1769](https://github.com/NousResearch/hermes-agent/pull/1769) 由 @sai-samarth)、LID 格式自聊 ([#1667](https://github.com/NousResearch/hermes-agent/pull/1667))、`reply_prefix` 配置修复 ([#1923](https://github.com/NousResearch/hermes-agent/pull/1923))、桥接子进程退出时重启 ([#2334](https://github.com/NousResearch/hermes-agent/pull/2334))、图像/桥接改进 ([#2181](https://github.com/NousResearch/hermes-agent/pull/2181))
- Matrix：修正 `reply_to_message_id` 参数 ([#1895](https://github.com/NousResearch/hermes-agent/pull/1895))、裸媒体类型修复 ([#1736](https://github.com/NousResearch/hermes-agent/pull/1736))
- Mattermost：媒体附件 MIME 类型 ([#2329](https://github.com/NousResearch/hermes-agent/pull/2329))

### 网关核心
- **自动重连**失败的平台，带指数退避 ([#2584](https://github.com/NousResearch/hermes-agent/pull/2584))
- **会话自动重置时通知用户** ([#2519](https://github.com/NousResearch/hermes-agent/pull/2519))
- **回复消息上下文**用于会话外回复 ([#1662](https://github.com/NousResearch/hermes-agent/pull/1662))
- **忽略未授权 DM** 配置选项 ([#1919](https://github.com/NousResearch/hermes-agent/pull/1919))
- 修复：线程模式下 `/reset` 重置全局会话而非线程 ([#2254](https://github.com/NousResearch/hermes-agent/pull/2254))
- 修复：流式响应后投递 MEDIA: 文件 ([#2382](https://github.com/NousResearch/hermes-agent/pull/2382))
- 修复：限制中断递归深度防止资源耗尽 ([#1659](https://github.com/NousResearch/hermes-agent/pull/1659))
- 修复：检测已停止的进程并在 `--replace` 时释放过时锁 ([#2406](https://github.com/NousResearch/hermes-agent/pull/2406), [#1908](https://github.com/NousResearch/hermes-agent/pull/1908))
- 修复：基于 PID 的等待及网关重启时的强制终止 ([#1902](https://github.com/NousResearch/hermes-agent/pull/1902))
- 修复：防止 `--replace` 模式杀死调用者进程 ([#2185](https://github.com/NousResearch/hermes-agent/pull/2185))
- 修复：`/model` 显示活跃备用模型而非配置默认值 ([#1660](https://github.com/NousResearch/hermes-agent/pull/1660))
- 修复：SQLite 中尚不存在会话时 `/title` 命令失败 ([#2379](https://github.com/NousResearch/hermes-agent/pull/2379) 由 @ten-jampa)
- 修复：代理完成后处理 `/queue` 消息 ([#2469](https://github.com/NousResearch/hermes-agent/pull/2469))
- 修复：剥离孤立的 `tool_results` + 允许 `/reset` 绕过运行中的代理 ([#2180](https://github.com/NousResearch/hermes-agent/pull/2180))
- 修复：防止代理在 systemd 管理外启动网关 ([#2617](https://github.com/NousResearch/hermes-agent/pull/2617))
- 修复：防止网关连接失败时 systemd 重启风暴 ([#2327](https://github.com/NousResearch/hermes-agent/pull/2327))
- 修复：systemd 单元中包含解析后的 node 路径 ([#1767](https://github.com/NousResearch/hermes-agent/pull/1767) 由 @sai-samarth)
- 修复：在网关外层异常处理器中向用户发送错误详情 ([#1966](https://github.com/NousResearch/hermes-agent/pull/1966))
- 修复：改进 429 使用限制和 500 上下文溢出的错误处理 ([#1839](https://github.com/NousResearch/hermes-agent/pull/1839))
- 修复：在启动警告检查中添加所有缺失的平台允许列表环境变量 ([#2628](https://github.com/NousResearch/hermes-agent/pull/2628))
- 修复：包含空格的文件路径导致媒体投递失败 ([#2621](https://github.com/NousResearch/hermes-agent/pull/2621))
- 修复：多平台网关中的重复会话键冲突 ([#2171](https://github.com/NousResearch/hermes-agent/pull/2171))
- 修复：Matrix 和 Mattermost 永远不报告为已连接 ([#1711](https://github.com/NousResearch/hermes-agent/pull/1711))
- 修复：PII 脱敏配置未被读取 — 缺少 yaml 导入 ([#1701](https://github.com/NousResearch/hermes-agent/pull/1701))
- 修复：技能斜杠命令上的 NameError ([#1697](https://github.com/NousResearch/hermes-agent/pull/1697))
- 修复：在检查点中持久化监视器元数据用于崩溃恢复 ([#1706](https://github.com/NousResearch/hermes-agent/pull/1706))
- 修复：send_image_file、send_document、send_video 中传递 `message_thread_id` ([#2339](https://github.com/NousResearch/hermes-agent/pull/2339))
- 修复：快速连续照片消息的媒体组聚合 ([#2160](https://github.com/NousResearch/hermes-agent/pull/2160))

---

## 🔧 工具系统

### MCP 增强
- **MCP 服务器管理 CLI** + OAuth 2.1 PKCE 认证 ([#2465](https://github.com/NousResearch/hermes-agent/pull/2465))
- **将 MCP 服务器作为独立工具集暴露** ([#1907](https://github.com/NousResearch/hermes-agent/pull/1907))
- **`hermes tools` 中的交互式 MCP 工具配置** ([#1694](https://github.com/NousResearch/hermes-agent/pull/1694))
- 修复：MCP-OAuth 端口不匹配、路径遍历和共享处理器状态 ([#2552](https://github.com/NousResearch/hermes-agent/pull/2552))
- 修复：跨会话重置保留 MCP 工具注册 ([#2124](https://github.com/NousResearch/hermes-agent/pull/2124))
- 修复：并发文件访问崩溃 + 重复 MCP 注册 ([#2154](https://github.com/NousResearch/hermes-agent/pull/2154))
- 修复：规范化 MCP 模式 + 扩展会话列表列 ([#2102](https://github.com/NousResearch/hermes-agent/pull/2102))
- 修复：`tool_choice` 的 `mcp_` 前缀处理 ([#1775](https://github.com/NousResearch/hermes-agent/pull/1775))

### Web 工具后端
- **Tavily** 作为网络搜索/提取/爬取后端 ([#1731](https://github.com/NousResearch/hermes-agent/pull/1731))
- **Parallel** 作为替代网络搜索/提取后端 ([#1696](https://github.com/NousResearch/hermes-agent/pull/1696))
- **可配置的 Web 后端** — Firecrawl/BeautifulSoup/Playwright 选择 ([#2256](https://github.com/NousResearch/hermes-agent/pull/2256))
- 修复：仅含空白的环境变量绕过 Web 后端检测 ([#2341](https://github.com/NousResearch/hermes-agent/pull/2341))

### 新工具
- **IMAP 电子邮件**读取和发送 ([#2173](https://github.com/NousResearch/hermes-agent/pull/2173))
- **STT（语音转文字）**工具，使用 Whisper API ([#2072](https://github.com/NousResearch/hermes-agent/pull/2072))
- **路由感知的定价估算** ([#1695](https://github.com/NousResearch/hermes-agent/pull/1695))

### 工具改进
- TTS：OpenAI TTS 提供商的 `base_url` 支持 ([#2064](https://github.com/NousResearch/hermes-agent/pull/2064) 由 @hanai)
- 视觉：可配置超时、文件路径波浪号展开、DM 视觉支持多图和 base64 备用 ([#2480](https://github.com/NousResearch/hermes-agent/pull/2480), [#2585](https://github.com/NousResearch/hermes-agent/pull/2585), [#2211](https://github.com/NousResearch/hermes-agent/pull/2211))
- 浏览器：会话创建中的竞态条件修复 ([#1721](https://github.com/NousResearch/hermes-agent/pull/1721))、意外 LLM 参数的 TypeError ([#1735](https://github.com/NousResearch/hermes-agent/pull/1735))
- 文件工具：从 write_file 和 patch 内容中剥离 ANSI 转义码 ([#2532](https://github.com/NousResearch/hermes-agent/pull/2532))、在重复搜索键中包含分页参数 ([#1824](https://github.com/NousResearch/hermes-agent/pull/1824) 由 @cutepawss)、改进模糊匹配精度 + 位置计算重构 ([#2096](https://github.com/NousResearch/hermes-agent/pull/2096), [#1681](https://github.com/NousResearch/hermes-agent/pull/1681))
- 代码执行：资源泄漏和双重套接字关闭修复 ([#2381](https://github.com/NousResearch/hermes-agent/pull/2381))
- 委托：并发子代理委托的线程安全 ([#1672](https://github.com/NousResearch/hermes-agent/pull/1672))、委托后保留父代理的工具列表 ([#1778](https://github.com/NousResearch/hermes-agent/pull/1778))
- 修复：使并发工具批处理路径感知用于文件变异 ([#1914](https://github.com/NousResearch/hermes-agent/pull/1914))
- 修复：平台调度前在 `send_message_tool` 中对长消息分块 ([#1646](https://github.com/NousResearch/hermes-agent/pull/1646))
- 修复：添加缺失的 'messaging' 工具集 ([#1718](https://github.com/NousResearch/hermes-agent/pull/1718))
- 修复：防止不可用的工具名称泄漏到模型模式 ([#2072](https://github.com/NousResearch/hermes-agent/pull/2072))
- 修复：通过引用传递 visited 集合防止菱形依赖重复 ([#2311](https://github.com/NousResearch/hermes-agent/pull/2311))
- 修复：Daytona 沙箱查找从 `find_one` 迁移到 `get/list` ([#2063](https://github.com/NousResearch/hermes-agent/pull/2063) 由 @rovle)

---

## 🧩 技能生态系统

### 技能系统改进
- **代理创建的技能** — 允许注意级别的发现，危险技能询问而非阻止 ([#1840](https://github.com/NousResearch/hermes-agent/pull/1840), [#2446](https://github.com/NousResearch/hermes-agent/pull/2446))
- **`--yes` 标志** — 在 `/skills install` 和卸载中绕过确认 ([#1647](https://github.com/NousResearch/hermes-agent/pull/1647))
- **已禁用的技能**在横幅、系统提示和斜杠命令中得到尊重 ([#1897](https://github.com/NousResearch/hermes-agent/pull/1897))
- 修复：技能 custom_tools 导入崩溃 + 沙箱 file_tools 集成 ([#2239](https://github.com/NousResearch/hermes-agent/pull/2239))
- 修复：带 pip 需求的代理创建技能安装时崩溃 ([#2145](https://github.com/NousResearch/hermes-agent/pull/2145))
- 修复：`hub.yaml` 缺失时 `Skills.__init__` 中的竞态条件 ([#2242](https://github.com/NousResearch/hermes-agent/pull/2242))
- 修复：安装前验证技能元数据并阻止重复 ([#2241](https://github.com/NousResearch/hermes-agent/pull/2241))
- 修复：技能中心检查/解析 — 检查、重定向、发现、tap 列表中的 4 个 Bug ([#2447](https://github.com/NousResearch/hermes-agent/pull/2447))
- 修复：代理创建的技能在会话重置后继续工作 ([#2121](https://github.com/NousResearch/hermes-agent/pull/2121))

### 新技能
- **OCR-and-documents** — PDF/DOCX/XLS/PPTX/图像 OCR，可选 GPU ([#2236](https://github.com/NousResearch/hermes-agent/pull/2236), [#2461](https://github.com/NousResearch/hermes-agent/pull/2461))
- **Huggingface-hub** 捆绑技能 ([#1921](https://github.com/NousResearch/hermes-agent/pull/1921))
- **Sherlock OSINT** 用户名搜索 ([#1671](https://github.com/NousResearch/hermes-agent/pull/1671))
- **Meme-generation** — 使用 Pillow 的图像生成器 ([#2344](https://github.com/NousResearch/hermes-agent/pull/2344))
- **Bioinformatics** 网关技能 — 索引 400+ 个生物技能 ([#2387](https://github.com/NousResearch/hermes-agent/pull/2387))
- **Inference.sh** 技能（基于终端）([#1686](https://github.com/NousResearch/hermes-agent/pull/1686))
- **Base 区块链**可选技能 ([#1643](https://github.com/NousResearch/hermes-agent/pull/1643))
- **3D-model-viewer** 可选技能 ([#2226](https://github.com/NousResearch/hermes-agent/pull/2226))
- **FastMCP** 可选技能 ([#2113](https://github.com/NousResearch/hermes-agent/pull/2113))
- **Hermes-agent-setup** 技能 ([#1905](https://github.com/NousResearch/hermes-agent/pull/1905))

---

## 🔌 插件系统增强

- **TUI 扩展钩子** — 在 Hermes 之上构建自定义 CLI ([#2333](https://github.com/NousResearch/hermes-agent/pull/2333))
- **`hermes plugins install/remove/list`** 命令 ([#2337](https://github.com/NousResearch/hermes-agent/pull/2337))
- **插件的斜杠命令注册** ([#2359](https://github.com/NousResearch/hermes-agent/pull/2359))
- **`session:end` 生命周期事件**钩子 ([#1725](https://github.com/NousResearch/hermes-agent/pull/1725))
- 修复：项目插件发现需要选择加入 ([#2215](https://github.com/NousResearch/hermes-agent/pull/2215))

---

## 🔒 安全与可靠性

### 安全
- **SSRF 保护** — 用于 vision_tools 和 web_tools ([#2679](https://github.com/NousResearch/hermes-agent/pull/2679))
- **Shell 注入防护** — `_expand_path` 中通过 `~user` 路径后缀 ([#2685](https://github.com/NousResearch/hermes-agent/pull/2685))
- **阻止不受信任的浏览器来源** API 服务器访问 ([#2451](https://github.com/NousResearch/hermes-agent/pull/2451))
- **阻止沙箱后端凭证**进入子进程环境 ([#1658](https://github.com/NousResearch/hermes-agent/pull/1658))
- **阻止 @ 引用**读取工作区外的密钥 ([#2601](https://github.com/NousResearch/hermes-agent/pull/2601) 由 @Gutslabs)
- **恶意代码模式预执行扫描器** — 用于 terminal_tool ([#2245](https://github.com/NousResearch/hermes-agent/pull/2245))
- **加固终端安全**和沙箱文件写入 ([#1653](https://github.com/NousResearch/hermes-agent/pull/1653))
- **PKCE 验证器泄漏**修复 + OAuth 刷新 Content-Type ([#1775](https://github.com/NousResearch/hermes-agent/pull/1775))
- **消除 `execute()` 调用中的 SQL 字符串格式化** ([#2061](https://github.com/NousResearch/hermes-agent/pull/2061) 由 @dusterbloom)
- **加固 jobs API** — 输入限制、字段白名单、启动检查 ([#2456](https://github.com/NousResearch/hermes-agent/pull/2456))

### 可靠性
- 4 个 SessionDB 方法的线程锁 ([#1704](https://github.com/NousResearch/hermes-agent/pull/1704))
- 并发记忆写入的文件锁 ([#1726](https://github.com/NousResearch/hermes-agent/pull/1726))
- 优雅处理 OpenRouter 错误 ([#2112](https://github.com/NousResearch/hermes-agent/pull/2112))
- 保护 print() 调用免受 OSError ([#1668](https://github.com/NousResearch/hermes-agent/pull/1668))
- 安全处理脱敏格式器中的非字符串输入 ([#2392](https://github.com/NousResearch/hermes-agent/pull/2392), [#1700](https://github.com/NousResearch/hermes-agent/pull/1700))
- ACP：模型切换时保留会话提供商、会话持久化到磁盘 ([#2380](https://github.com/NousResearch/hermes-agent/pull/2380), [#2071](https://github.com/NousResearch/hermes-agent/pull/2071))
- API 服务器：ResponseStore 跨重启持久化到 SQLite ([#2472](https://github.com/NousResearch/hermes-agent/pull/2472))
- 修复：`fetch_nous_models` 始终因位置参数 TypeError ([#1699](https://github.com/NousResearch/hermes-agent/pull/1699))
- 修复：cli.py 中的合并冲突标记导致启动失败 ([#2347](https://github.com/NousResearch/hermes-agent/pull/2347))
- 修复：wheel 中缺少 `minisweagent_path.py` ([#2098](https://github.com/NousResearch/hermes-agent/pull/2098) 由 @JiwaniZakir)

### 定时任务系统
- **`[SILENT]` 响应** — 定时任务代理可以抑制投递 ([#1833](https://github.com/NousResearch/hermes-agent/pull/1833))
- **根据调度频率缩放错过任务的宽限窗口** ([#2449](https://github.com/NousResearch/hermes-agent/pull/2449))
- **恢复最近的一次性任务** ([#1918](https://github.com/NousResearch/hermes-agent/pull/1918))
- 修复：将 `repeat<=0` 规范化为 None — LLM 传递 -1 时任务在首次运行后被删除 ([#2612](https://github.com/NousResearch/hermes-agent/pull/2612) 由 @Mibayy)
- 修复：Matrix 添加到调度器投递 platform_map ([#2167](https://github.com/NousResearch/hermes-agent/pull/2167) 由 @buntingszn)
- 修复：无时区的天真 ISO 时间戳 — 任务在错误时间触发 ([#1729](https://github.com/NousResearch/hermes-agent/pull/1729))
- 修复：`get_due_jobs` 读取 `jobs.json` 两次 — 竞态条件 ([#1716](https://github.com/NousResearch/hermes-agent/pull/1716))
- 修复：静默任务返回空响应以跳过投递 ([#2442](https://github.com/NousResearch/hermes-agent/pull/2442))
- 修复：停止将定时任务输出注入网关会话历史 ([#2313](https://github.com/NousResearch/hermes-agent/pull/2313))
- 修复：`asyncio.run()` 抛出 RuntimeError 时关闭废弃的协程 ([#2317](https://github.com/NousResearch/hermes-agent/pull/2317))

---

## 🧪 测试

- 解决所有持续失败的测试 ([#2488](https://github.com/NousResearch/hermes-agent/pull/2488))
- 用 `monkeypatch` 替换 `FakePath` 以兼容 Python 3.12 ([#2444](https://github.com/NousResearch/hermes-agent/pull/2444))
- 对齐 Hermes 设置和完整套件期望 ([#1710](https://github.com/NousResearch/hermes-agent/pull/1710))

---

## 📚 文档

- 近期功能的全面文档更新 ([#1693](https://github.com/NousResearch/hermes-agent/pull/1693), [#2183](https://github.com/NousResearch/hermes-agent/pull/2183))
- 阿里云和钉钉设置指南 ([#1687](https://github.com/NousResearch/hermes-agent/pull/1687), [#1692](https://github.com/NousResearch/hermes-agent/pull/1692))
- 详细的技能文档 ([#2244](https://github.com/NousResearch/hermes-agent/pull/2244))
- Honcho 自托管 / Docker 配置 ([#2475](https://github.com/NousResearch/hermes-agent/pull/2475))
- 上下文长度检测 FAQ 和快速入门参考 ([#2179](https://github.com/NousResearch/hermes-agent/pull/2179))
- 修复参考和用户指南中的文档不一致 ([#1995](https://github.com/NousResearch/hermes-agent/pull/1995))
- 修复 MCP 安装命令 — 使用 uv 而非裸 pip ([#1909](https://github.com/NousResearch/hermes-agent/pull/1909))
- 用 Mermaid/列表替换 ASCII 图表 ([#2402](https://github.com/NousResearch/hermes-agent/pull/2402))
- Gemini OAuth 提供商实现计划 ([#2467](https://github.com/NousResearch/hermes-agent/pull/2467))
- Discord 服务器成员意图标记为必需 ([#2330](https://github.com/NousResearch/hermes-agent/pull/2330))
- 修复 api-server.md 中的 MDX 构建错误 ([#1787](https://github.com/NousResearch/hermes-agent/pull/1787))
- 对齐 venv 路径以匹配安装器 ([#2114](https://github.com/NousResearch/hermes-agent/pull/2114))
- 新技能添加到中心索引 ([#2281](https://github.com/NousResearch/hermes-agent/pull/2281))

---

## 👥 贡献者

### 核心
- **@teknium1**（Teknium）— 280 个 PR

### 社区贡献者
- **@mchzimm**（to_the_max）— GitHub Copilot 提供商集成 ([#1879](https://github.com/NousResearch/hermes-agent/pull/1879))
- **@jquesnelle**（Jeffrey Quesnelle）— 每线程持久事件循环修复 ([#2214](https://github.com/NousResearch/hermes-agent/pull/2214))
- **@llbn**（lbn）— Telegram MarkdownV2 删除线、剧透、引用块和转义修复 ([#2199](https://github.com/NousResearch/hermes-agent/pull/2199), [#2200](https://github.com/NousResearch/hermes-agent/pull/2200))
- **@dusterbloom** — SQL 注入防护 + 本地服务器上下文窗口查询 ([#2061](https://github.com/NousResearch/hermes-agent/pull/2061), [#2091](https://github.com/NousResearch/hermes-agent/pull/2091))
- **@0xbyt4** — Anthropic tool_calls None 防护 + OpenCode-Go 提供商配置修复 ([#2209](https://github.com/NousResearch/hermes-agent/pull/2209), [#2393](https://github.com/NousResearch/hermes-agent/pull/2393))
- **@sai-samarth**（Saisamarth）— WhatsApp send_message 路由 + systemd node 路径 ([#1769](https://github.com/NousResearch/hermes-agent/pull/1769), [#1767](https://github.com/NousResearch/hermes-agent/pull/1767))
- **@Gutslabs**（Guts）— 阻止 @ 引用读取密钥 ([#2601](https://github.com/NousResearch/hermes-agent/pull/2601))
- **@Mibayy**（Mibay）— 定时任务 repeat 规范化 ([#2612](https://github.com/NousResearch/hermes-agent/pull/2612))
- **@ten-jampa**（Tenzin Jampa）— 网关 /title 命令修复 ([#2379](https://github.com/NousResearch/hermes-agent/pull/2379))
- **@cutepawss**（lila）— 文件工具搜索分页修复 ([#1824](https://github.com/NousResearch/hermes-agent/pull/1824))
- **@hanai**（Hanai）— OpenAI TTS base_url 支持 ([#2064](https://github.com/NousResearch/hermes-agent/pull/2064))
- **@rovle**（Lovre Pesut）— Daytona 沙箱 API 迁移 ([#2063](https://github.com/NousResearch/hermes-agent/pull/2063))
- **@buntingszn**（bunting szn）— Matrix 定时任务投递支持 ([#2167](https://github.com/NousResearch/hermes-agent/pull/2167))
- **@InB4DevOps** — 新会话时 token 计数器重置 ([#2101](https://github.com/NousResearch/hermes-agent/pull/2101))
- **@JiwaniZakir**（Zakir Jiwani）— wheel 中缺失文件修复 ([#2098](https://github.com/NousResearch/hermes-agent/pull/2098))
- **@ygd58**（buray）— 委托工具父工具名称修复 ([#2083](https://github.com/NousResearch/hermes-agent/pull/2083))

---

**完整变更日志**：[v2026.3.17...v2026.3.23](https://github.com/NousResearch/hermes-agent/compare/v2026.3.17...v2026.3.23)
