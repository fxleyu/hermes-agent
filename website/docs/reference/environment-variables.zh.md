---
sidebar_position: 2
title: "环境变量"
description: "Hermes Agent 使用的所有环境变量完整参考"
---

# 环境变量参考

所有变量放在 `~/.hermes/.env` 中。你也可以使用 `hermes config set VAR value` 来设置。

## LLM 提供商

| 变量 | 描述 |
|------|------|
| `OPENROUTER_API_KEY` | OpenRouter API 密钥（推荐，灵活性最高） |
| `OPENROUTER_BASE_URL` | 覆盖 OpenRouter 兼容的基础 URL |
| `AI_GATEWAY_API_KEY` | Vercel AI Gateway API 密钥（[ai-gateway.vercel.sh](https://ai-gateway.vercel.sh)） |
| `AI_GATEWAY_BASE_URL` | 覆盖 AI Gateway 基础 URL（默认：`https://ai-gateway.vercel.sh/v1`） |
| `OPENAI_API_KEY` | 用于自定义 OpenAI 兼容端点的 API 密钥（与 `OPENAI_BASE_URL` 一起使用） |
| `OPENAI_BASE_URL` | 自定义端点的基础 URL（VLLM、SGLang 等） |
| `COPILOT_GITHUB_TOKEN` | Copilot API 的 GitHub 令牌 — 第一优先级（OAuth `gho_*` 或细粒度 PAT `github_pat_*`；经典 PAT `ghp_*` **不支持**） |
| `GH_TOKEN` | GitHub 令牌 — Copilot 的第二优先级（也被 `gh` CLI 使用） |
| `GITHUB_TOKEN` | GitHub 令牌 — Copilot 的第三优先级 |
| `HERMES_COPILOT_ACP_COMMAND` | 覆盖 Copilot ACP CLI 二进制路径（默认：`copilot`） |
| `COPILOT_CLI_PATH` | `HERMES_COPILOT_ACP_COMMAND` 的别名 |
| `HERMES_COPILOT_ACP_ARGS` | 覆盖 Copilot ACP 参数（默认：`--acp --stdio`） |
| `COPILOT_ACP_BASE_URL` | 覆盖 Copilot ACP 基础 URL |
| `GLM_API_KEY` | z.ai / 智谱 AI GLM API 密钥（[z.ai](https://z.ai)） |
| `ZAI_API_KEY` | `GLM_API_KEY` 的别名 |
| `Z_AI_API_KEY` | `GLM_API_KEY` 的别名 |
| `GLM_BASE_URL` | 覆盖 z.ai 基础 URL（默认：`https://api.z.ai/api/paas/v4`） |
| `KIMI_API_KEY` | Kimi / Moonshot AI API 密钥（[moonshot.ai](https://platform.moonshot.ai)） |
| `KIMI_BASE_URL` | 覆盖 Kimi 基础 URL（默认：`https://api.moonshot.ai/v1`） |
| `KIMI_CN_API_KEY` | Kimi / Moonshot 中国 API 密钥（[moonshot.cn](https://platform.moonshot.cn)） |
| `ARCEEAI_API_KEY` | Arcee AI API 密钥（[chat.arcee.ai](https://chat.arcee.ai/)） |
| `ARCEE_BASE_URL` | 覆盖 Arcee 基础 URL（默认：`https://api.arcee.ai/api/v1`） |
| `MINIMAX_API_KEY` | MiniMax API 密钥 — 全球端点（[minimax.io](https://www.minimax.io)） |
| `MINIMAX_BASE_URL` | 覆盖 MiniMax 基础 URL（默认：`https://api.minimax.io/v1`） |
| `MINIMAX_CN_API_KEY` | MiniMax API 密钥 — 中国端点（[minimaxi.com](https://www.minimaxi.com)） |
| `MINIMAX_CN_BASE_URL` | 覆盖 MiniMax 中国基础 URL（默认：`https://api.minimaxi.com/v1`） |
| `KILOCODE_API_KEY` | Kilo Code API 密钥（[kilo.ai](https://kilo.ai)） |
| `KILOCODE_BASE_URL` | 覆盖 Kilo Code 基础 URL（默认：`https://api.kilo.ai/api/gateway`） |
| `XIAOMI_API_KEY` | 小米 MiMo API 密钥（[platform.xiaomimimo.com](https://platform.xiaomimimo.com)） |
| `XIAOMI_BASE_URL` | 覆盖小米 MiMo 基础 URL（默认：`https://api.xiaomimimo.com/v1`） |
| `HF_TOKEN` | Hugging Face 推理提供商令牌（[huggingface.co/settings/tokens](https://huggingface.co/settings/tokens)） |
| `HF_BASE_URL` | 覆盖 Hugging Face 基础 URL（默认：`https://router.huggingface.co/v1`） |
| `GOOGLE_API_KEY` | Google AI Studio API 密钥（[aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey)） |
| `GEMINI_API_KEY` | `GOOGLE_API_KEY` 的别名 |
| `GEMINI_BASE_URL` | 覆盖 Google AI Studio 基础 URL |
| `HERMES_GEMINI_CLIENT_ID` | `google-gemini-cli` PKCE 登录的 OAuth 客户端 ID（可选；默认使用 Google 公开的 gemini-cli 客户端） |
| `HERMES_GEMINI_CLIENT_SECRET` | `google-gemini-cli` 的 OAuth 客户端密钥（可选） |
| `HERMES_GEMINI_PROJECT_ID` | 付费 Gemini 层级的 GCP 项目 ID（免费层自动配置） |
| `ANTHROPIC_API_KEY` | Anthropic Console API 密钥（[console.anthropic.com](https://console.anthropic.com/)） |
| `ANTHROPIC_TOKEN` | 手动或旧版 Anthropic OAuth/setup-token 覆盖 |
| `DASHSCOPE_API_KEY` | 阿里云 DashScope API 密钥，用于 Qwen 模型（[modelstudio.console.alibabacloud.com](https://modelstudio.console.alibabacloud.com/)） |
| `DASHSCOPE_BASE_URL` | 自定义 DashScope 基础 URL（默认：`https://coding-intl.dashscope.aliyuncs.com/v1`） |
| `DEEPSEEK_API_KEY` | DeepSeek API 密钥（[platform.deepseek.com](https://platform.deepseek.com/api_keys)） |
| `DEEPSEEK_BASE_URL` | 自定义 DeepSeek API 基础 URL |
| `OPENCODE_ZEN_API_KEY` | OpenCode Zen API 密钥 — 按量付费访问精选模型（[opencode.ai](https://opencode.ai/auth)） |
| `OPENCODE_ZEN_BASE_URL` | 覆盖 OpenCode Zen 基础 URL |
| `OPENCODE_GO_API_KEY` | OpenCode Go API 密钥 — $10/月订阅开放模型（[opencode.ai](https://opencode.ai/auth)） |
| `OPENCODE_GO_BASE_URL` | 覆盖 OpenCode Go 基础 URL |
| `CLAUDE_CODE_OAUTH_TOKEN` | 手动导出时的显式 Claude Code 令牌覆盖 |
| `HERMES_MODEL` | 在进程级别覆盖模型名称（cron 调度器使用；正常使用请优先使用 `config.yaml`） |
| `VOICE_TOOLS_OPENAI_KEY` | OpenAI 语音转文本和文本转语音提供商的首选 OpenAI 密钥 |
| `HERMES_LOCAL_STT_COMMAND` | 可选的本地语音转文本命令模板。支持 `{input_path}`、`{output_dir}`、`{language}` 和 `{model}` 占位符 |
| `HERMES_LOCAL_STT_LANGUAGE` | 传递给 `HERMES_LOCAL_STT_COMMAND` 或自动检测的本地 `whisper` CLI 后备的默认语言（默认：`en`） |
| `HERMES_HOME` | 覆盖 Hermes 配置目录（默认：`~/.hermes`）。同时限定网关 PID 文件和 systemd 服务名称，因此多个安装可以并发运行 |

## 提供商认证（OAuth）

对于原生 Anthropic 认证，Hermes 在 Claude Code 的凭证文件存在时优先使用它们，因为这些凭证可以自动刷新。`ANTHROPIC_TOKEN` 等环境变量仍可作为手动覆盖使用，但不再是 Claude Pro/Max 登录的首选路径。

| 变量 | 描述 |
|------|------|
| `HERMES_INFERENCE_PROVIDER` | 覆盖提供商选择：`auto`、`openrouter`、`nous`、`openai-codex`、`copilot`、`copilot-acp`、`anthropic`、`huggingface`、`zai`、`kimi-coding`、`kimi-coding-cn`、`minimax`、`minimax-cn`、`kilocode`、`xiaomi`、`arcee`、`alibaba`、`deepseek`、`opencode-zen`、`opencode-go`、`ai-gateway`（默认：`auto`） |
| `HERMES_PORTAL_BASE_URL` | 覆盖 Nous Portal URL（用于开发/测试） |
| `NOUS_INFERENCE_BASE_URL` | 覆盖 Nous 推理 API URL |
| `HERMES_NOUS_MIN_KEY_TTL_SECONDS` | 重新铸造前的最小代理密钥 TTL（默认：1800 = 30 分钟） |
| `HERMES_NOUS_TIMEOUT_SECONDS` | Nous 凭证/令牌流的 HTTP 超时 |
| `HERMES_DUMP_REQUESTS` | 将 API 请求负载转储到日志文件（`true`/`false`） |
| `HERMES_PREFILL_MESSAGES_FILE` | 在 API 调用时注入的临时预填充消息 JSON 文件路径 |
| `HERMES_TIMEZONE` | IANA 时区覆盖（例如 `America/New_York`） |

## 工具 API

| 变量 | 描述 |
|------|------|
| `PARALLEL_API_KEY` | AI 原生网络搜索（[parallel.ai](https://parallel.ai/)） |
| `FIRECRAWL_API_KEY` | 网页抓取和云浏览器（[firecrawl.dev](https://firecrawl.dev/)） |
| `FIRECRAWL_API_URL` | 自托管实例的自定义 Firecrawl API 端点（可选） |
| `TAVILY_API_KEY` | Tavily API 密钥，用于 AI 原生网络搜索、提取和爬取（[app.tavily.com](https://app.tavily.com/home)） |
| `EXA_API_KEY` | Exa API 密钥，用于 AI 原生网络搜索和内容（[exa.ai](https://exa.ai/)） |
| `BROWSERBASE_API_KEY` | 浏览器自动化（[browserbase.com](https://browserbase.com/)） |
| `BROWSERBASE_PROJECT_ID` | Browserbase 项目 ID |
| `BROWSER_USE_API_KEY` | Browser Use 云浏览器 API 密钥（[browser-use.com](https://browser-use.com/)） |
| `FIRECRAWL_BROWSER_TTL` | Firecrawl 浏览器会话 TTL，单位秒（默认：300） |
| `BROWSER_CDP_URL` | 本地浏览器的 Chrome DevTools Protocol URL（通过 `/browser connect` 设置，例如 `ws://localhost:9222`） |
| `CAMOFOX_URL` | Camofox 本地反检测浏览器 URL（默认：`http://localhost:9377`） |
| `BROWSER_INACTIVITY_TIMEOUT` | 浏览器会话不活跃超时，单位秒 |
| `FAL_KEY` | 图像生成（[fal.ai](https://fal.ai/)） |
| `GROQ_API_KEY` | Groq Whisper STT API 密钥（[groq.com](https://groq.com/)） |
| `ELEVENLABS_API_KEY` | ElevenLabs 高级 TTS 语音（[elevenlabs.io](https://elevenlabs.io/)） |
| `STT_GROQ_MODEL` | 覆盖 Groq STT 模型（默认：`whisper-large-v3-turbo`） |
| `GROQ_BASE_URL` | 覆盖 Groq OpenAI 兼容 STT 端点 |
| `STT_OPENAI_MODEL` | 覆盖 OpenAI STT 模型（默认：`whisper-1`） |
| `STT_OPENAI_BASE_URL` | 覆盖 OpenAI 兼容 STT 端点 |
| `GITHUB_TOKEN` | Skills Hub 的 GitHub 令牌（更高的 API 速率限制、技能发布） |
| `HONCHO_API_KEY` | 跨会话用户建模（[honcho.dev](https://honcho.dev/)） |
| `HONCHO_BASE_URL` | 自托管 Honcho 实例的基础 URL（默认：Honcho 云）。本地实例无需 API 密钥 |
| `SUPERMEMORY_API_KEY` | 语义长期记忆，带配置文件回忆和会话摄入（[supermemory.ai](https://supermemory.ai)） |
| `TINKER_API_KEY` | RL 训练（[tinker-console.thinkingmachines.ai](https://tinker-console.thinkingmachines.ai/)） |
| `WANDB_API_KEY` | RL 训练指标（[wandb.ai](https://wandb.ai/)） |
| `DAYTONA_API_KEY` | Daytona 云沙箱（[daytona.io](https://daytona.io/)） |

### Nous 工具网关

这些变量配置付费 Nous 订阅者或自托管网关部署的[工具网关](/docs/user-guide/features/tool-gateway)。大多数用户不需要设置这些 — 网关通过 `hermes model` 或 `hermes tools` 自动配置。

| 变量 | 描述 |
|------|------|
| `TOOL_GATEWAY_DOMAIN` | 工具网关路由的基础域名（默认：`nousresearch.com`） |
| `TOOL_GATEWAY_SCHEME` | 网关 URL 的 HTTP 或 HTTPS 方案（默认：`https`） |
| `TOOL_GATEWAY_USER_TOKEN` | 工具网关的认证令牌（通常从 Nous 认证自动填充） |
| `FIRECRAWL_GATEWAY_URL` | 专门覆盖 Firecrawl 网关端点的 URL |

## 终端后端

| 变量 | 描述 |
|------|------|
| `TERMINAL_ENV` | 后端：`local`、`docker`、`ssh`、`singularity`、`modal`、`daytona` |
| `TERMINAL_DOCKER_IMAGE` | Docker 镜像（默认：`nikolaik/python-nodejs:python3.11-nodejs20`） |
| `TERMINAL_DOCKER_FORWARD_ENV` | 要显式转发到 Docker 终端会话的环境变量名称的 JSON 数组。注意：技能声明的 `required_environment_variables` 会自动转发 — 你只需要为未被任何技能声明的变量设置此项。 |
| `TERMINAL_DOCKER_VOLUMES` | 额外的 Docker 卷挂载（逗号分隔的 `host:container` 对） |
| `TERMINAL_DOCKER_MOUNT_CWD_TO_WORKSPACE` | 高级选项：将启动 cwd 挂载到 Docker `/workspace`（`true`/`false`，默认：`false`） |
| `TERMINAL_SINGULARITY_IMAGE` | Singularity 镜像或 `.sif` 路径 |
| `TERMINAL_MODAL_IMAGE` | Modal 容器镜像 |
| `TERMINAL_DAYTONA_IMAGE` | Daytona 沙箱镜像 |
| `TERMINAL_TIMEOUT` | 命令超时，单位秒 |
| `TERMINAL_LIFETIME_SECONDS` | 终端会话的最大生命周期，单位秒 |
| `TERMINAL_CWD` | 所有终端会话的工作目录 |
| `SUDO_PASSWORD` | 启用无交互提示的 sudo |

对于云沙箱后端，持久化是面向文件系统的。`TERMINAL_LIFETIME_SECONDS` 控制 Hermes 何时清理空闲的终端会话，后续恢复可能重新创建沙箱而不是保持相同的实时进程运行。

## SSH 后端

| 变量 | 描述 |
|------|------|
| `TERMINAL_SSH_HOST` | 远程服务器主机名 |
| `TERMINAL_SSH_USER` | SSH 用户名 |
| `TERMINAL_SSH_PORT` | SSH 端口（默认：22） |
| `TERMINAL_SSH_KEY` | 私钥路径 |
| `TERMINAL_SSH_PERSISTENT` | 覆盖 SSH 的持久 shell（默认：跟随 `TERMINAL_PERSISTENT_SHELL`） |

## 容器资源（Docker、Singularity、Modal、Daytona）

| 变量 | 描述 |
|------|------|
| `TERMINAL_CONTAINER_CPU` | CPU 核心数（默认：1） |
| `TERMINAL_CONTAINER_MEMORY` | 内存，单位 MB（默认：5120） |
| `TERMINAL_CONTAINER_DISK` | 磁盘，单位 MB（默认：51200） |
| `TERMINAL_CONTAINER_PERSISTENT` | 跨会话持久化容器文件系统（默认：`true`） |
| `TERMINAL_SANDBOX_DIR` | 工作区和覆盖层的宿主机目录（默认：`~/.hermes/sandboxes/`） |

## 持久 Shell

| 变量 | 描述 |
|------|------|
| `TERMINAL_PERSISTENT_SHELL` | 为非本地后端启用持久 shell（默认：`true`）。也可通过 `config.yaml` 中的 `terminal.persistent_shell` 设置 |
| `TERMINAL_LOCAL_PERSISTENT` | 为本地后端启用持久 shell（默认：`false`） |
| `TERMINAL_SSH_PERSISTENT` | 覆盖 SSH 后端的持久 shell（默认：跟随 `TERMINAL_PERSISTENT_SHELL`） |

## 消息

| 变量 | 描述 |
|------|------|
| `TELEGRAM_BOT_TOKEN` | Telegram 机器人令牌（来自 @BotFather） |
| `TELEGRAM_ALLOWED_USERS` | 逗号分隔的允许使用机器人的用户 ID |
| `TELEGRAM_HOME_CHANNEL` | 定时任务投递的默认 Telegram 聊天/频道 |
| `TELEGRAM_HOME_CHANNEL_NAME` | Telegram 主频道的显示名称 |
| `TELEGRAM_WEBHOOK_URL` | webhook 模式的公共 HTTPS URL（启用 webhook 而非轮询） |
| `TELEGRAM_WEBHOOK_PORT` | webhook 服务器的本地监听端口（默认：`8443`） |
| `TELEGRAM_WEBHOOK_SECRET` | 用于验证更新来自 Telegram 的密钥令牌 |
| `TELEGRAM_REACTIONS` | 处理期间在消息上启用表情反应（默认：`false`） |
| `TELEGRAM_IGNORED_THREADS` | 逗号分隔的机器人不响应的 Telegram 论坛主题/线程 ID |
| `TELEGRAM_PROXY` | Telegram 连接的代理 URL — 覆盖 `HTTPS_PROXY`。支持 `http://`、`https://`、`socks5://` |
| `DISCORD_BOT_TOKEN` | Discord 机器人令牌 |
| `DISCORD_ALLOWED_USERS` | 逗号分隔的允许使用机器人的 Discord 用户 ID |
| `DISCORD_HOME_CHANNEL` | 定时任务投递的默认 Discord 频道 |
| `DISCORD_HOME_CHANNEL_NAME` | Discord 主频道的显示名称 |
| `DISCORD_REQUIRE_MENTION` | 在服务器频道中需要 @提及才响应 |
| `DISCORD_FREE_RESPONSE_CHANNELS` | 逗号分隔的不需要提及的频道 ID |
| `DISCORD_AUTO_THREAD` | 支持时自动为长回复创建线程 |
| `DISCORD_REACTIONS` | 处理期间在消息上启用表情反应（默认：`true`） |
| `DISCORD_IGNORED_CHANNELS` | 逗号分隔的机器人不响应的频道 ID |
| `DISCORD_NO_THREAD_CHANNELS` | 逗号分隔的机器人不自动创建线程即可响应的频道 ID |
| `DISCORD_REPLY_TO_MODE` | 引用回复行为：`off`、`first`（默认）或 `all` |
| `SLACK_BOT_TOKEN` | Slack 机器人令牌（`xoxb-...`） |
| `SLACK_APP_TOKEN` | Slack 应用级令牌（`xapp-...`，Socket Mode 必需） |
| `SLACK_ALLOWED_USERS` | 逗号分隔的 Slack 用户 ID |
| `SLACK_HOME_CHANNEL` | 定时任务投递的默认 Slack 频道 |
| `SLACK_HOME_CHANNEL_NAME` | Slack 主频道的显示名称 |
| `WHATSAPP_ENABLED` | 启用 WhatsApp 桥接（`true`/`false`） |
| `WHATSAPP_MODE` | `bot`（独立号码）或 `self-chat`（给自己发消息） |
| `WHATSAPP_ALLOWED_USERS` | 逗号分隔的电话号码（带国家代码，不带 `+`），或 `*` 允许所有发送者 |
| `WHATSAPP_ALLOW_ALL_USERS` | 无需白名单允许所有 WhatsApp 发送者（`true`/`false`） |
| `WHATSAPP_DEBUG` | 在桥接中记录原始消息事件用于故障排除（`true`/`false`） |
| `SIGNAL_HTTP_URL` | signal-cli 守护进程 HTTP 端点（例如 `http://127.0.0.1:8080`） |
| `SIGNAL_ACCOUNT` | E.164 格式的机器人电话号码 |
| `SIGNAL_ALLOWED_USERS` | 逗号分隔的 E.164 电话号码或 UUID |
| `SIGNAL_GROUP_ALLOWED_USERS` | 逗号分隔的群组 ID，或 `*` 表示所有群组 |
| `SIGNAL_HOME_CHANNEL_NAME` | Signal 主频道的显示名称 |
| `SIGNAL_IGNORE_STORIES` | 忽略 Signal 故事/状态更新 |
| `SIGNAL_ALLOW_ALL_USERS` | 无需白名单允许所有 Signal 用户 |
| `TWILIO_ACCOUNT_SID` | Twilio Account SID（与电话技能共享） |
| `TWILIO_AUTH_TOKEN` | Twilio Auth Token（与电话技能共享；也用于 webhook 签名验证） |
| `TWILIO_PHONE_NUMBER` | E.164 格式的 Twilio 电话号码（与电话技能共享） |
| `SMS_WEBHOOK_URL` | Twilio 签名验证的公共 URL — 必须与 Twilio Console 中的 webhook URL 匹配（必需） |
| `SMS_WEBHOOK_PORT` | 入站 SMS 的 webhook 监听端口（默认：`8080`） |
| `SMS_WEBHOOK_HOST` | webhook 绑定地址（默认：`0.0.0.0`） |
| `SMS_INSECURE_NO_SIGNATURE` | 设为 `true` 禁用 Twilio 签名验证（仅限本地开发 — 不适用于生产环境） |
| `SMS_ALLOWED_USERS` | 逗号分隔的允许聊天的 E.164 电话号码 |
| `SMS_ALLOW_ALL_USERS` | 无需白名单允许所有 SMS 发送者 |
| `SMS_HOME_CHANNEL` | 定时任务/通知投递的电话号码 |
| `SMS_HOME_CHANNEL_NAME` | SMS 主频道的显示名称 |
| `EMAIL_ADDRESS` | Email 网关适配器的电子邮件地址 |
| `EMAIL_PASSWORD` | 邮箱账户的密码或应用密码 |
| `EMAIL_IMAP_HOST` | 邮件适配器的 IMAP 主机名 |
| `EMAIL_IMAP_PORT` | IMAP 端口 |
| `EMAIL_SMTP_HOST` | 邮件适配器的 SMTP 主机名 |
| `EMAIL_SMTP_PORT` | SMTP 端口 |
| `EMAIL_ALLOWED_USERS` | 逗号分隔的允许与机器人通信的电子邮件地址 |
| `EMAIL_HOME_ADDRESS` | 主动邮件投递的默认收件人 |
| `EMAIL_HOME_ADDRESS_NAME` | 邮件主目标的显示名称 |
| `EMAIL_POLL_INTERVAL` | 邮件轮询间隔，单位秒 |
| `EMAIL_ALLOW_ALL_USERS` | 允许所有入站邮件发送者 |
| `DINGTALK_CLIENT_ID` | 来自开发者门户的钉钉机器人 AppKey（[open.dingtalk.com](https://open.dingtalk.com)） |
| `DINGTALK_CLIENT_SECRET` | 来自开发者门户的钉钉机器人 AppSecret |
| `DINGTALK_ALLOWED_USERS` | 逗号分隔的允许与机器人通信的钉钉用户 ID |
| `FEISHU_APP_ID` | 来自 [open.feishu.cn](https://open.feishu.cn/) 的飞书/Lark 机器人 App ID |
| `FEISHU_APP_SECRET` | 飞书/Lark 机器人 App Secret |
| `FEISHU_DOMAIN` | `feishu`（中国）或 `lark`（国际）。默认：`feishu` |
| `FEISHU_CONNECTION_MODE` | `websocket`（推荐）或 `webhook`。默认：`websocket` |
| `FEISHU_ENCRYPT_KEY` | webhook 模式的可选加密密钥 |
| `FEISHU_VERIFICATION_TOKEN` | webhook 模式的可选验证令牌 |
| `FEISHU_ALLOWED_USERS` | 逗号分隔的允许与机器人通信的飞书用户 ID |
| `FEISHU_HOME_CHANNEL` | 定时任务投递和通知的飞书聊天 ID |
| `WECOM_BOT_ID` | 来自管理控制台的企微 AI Bot ID |
| `WECOM_SECRET` | 企微 AI Bot 密钥 |
| `WECOM_WEBSOCKET_URL` | 自定义 WebSocket URL（默认：`wss://openws.work.weixin.qq.com`） |
| `WECOM_ALLOWED_USERS` | 逗号分隔的允许与机器人通信的企微用户 ID |
| `WECOM_HOME_CHANNEL` | 定时任务投递和通知的企微聊天 ID |
| `WECOM_CALLBACK_CORP_ID` | 企微回调自建应用的企业 Corp ID |
| `WECOM_CALLBACK_CORP_SECRET` | 自建应用的 Corp secret |
| `WECOM_CALLBACK_AGENT_ID` | 自建应用的 Agent ID |
| `WECOM_CALLBACK_TOKEN` | 回调验证令牌 |
| `WECOM_CALLBACK_ENCODING_AES_KEY` | 回调加密的 AES 密钥 |
| `WECOM_CALLBACK_HOST` | 回调服务器绑定地址（默认：`0.0.0.0`） |
| `WECOM_CALLBACK_PORT` | 回调服务器端口（默认：`8645`） |
| `WECOM_CALLBACK_ALLOWED_USERS` | 逗号分隔的白名单用户 ID |
| `WECOM_CALLBACK_ALLOW_ALL_USERS` | 设为 `true` 无需白名单允许所有用户 |
| `WEIXIN_ACCOUNT_ID` | 通过 iLink Bot API 的 QR 登录获取的微信账号 ID |
| `WEIXIN_TOKEN` | 通过 iLink Bot API 的 QR 登录获取的微信认证令牌 |
| `WEIXIN_BASE_URL` | 覆盖微信 iLink Bot API 基础 URL（默认：`https://ilinkai.weixin.qq.com`） |
| `WEIXIN_CDN_BASE_URL` | 覆盖微信 CDN 媒体基础 URL（默认：`https://novac2c.cdn.weixin.qq.com/c2c`） |
| `WEIXIN_DM_POLICY` | 私信策略：`open`、`allowlist`、`pairing`、`disabled`（默认：`open`） |
| `WEIXIN_GROUP_POLICY` | 群组消息策略：`open`、`allowlist`、`disabled`（默认：`disabled`） |
| `WEIXIN_ALLOWED_USERS` | 逗号分隔的允许私信机器人的微信用户 ID |
| `WEIXIN_GROUP_ALLOWED_USERS` | 逗号分隔的允许与机器人交互的微信群组 ID |
| `WEIXIN_HOME_CHANNEL` | 定时任务投递和通知的微信聊天 ID |
| `WEIXIN_HOME_CHANNEL_NAME` | 微信主频道的显示名称 |
| `WEIXIN_ALLOW_ALL_USERS` | 无需白名单允许所有微信用户（`true`/`false`） |
| `BLUEBUBBLES_SERVER_URL` | BlueBubbles 服务器 URL（例如 `http://192.168.1.10:1234`） |
| `BLUEBUBBLES_PASSWORD` | BlueBubbles 服务器密码 |
| `BLUEBUBBLES_WEBHOOK_HOST` | webhook 监听绑定地址（默认：`127.0.0.1`） |
| `BLUEBUBBLES_WEBHOOK_PORT` | webhook 监听端口（默认：`8645`） |
| `BLUEBUBBLES_HOME_CHANNEL` | 定时任务/通知投递的电话号码/邮箱 |
| `BLUEBUBBLES_ALLOWED_USERS` | 逗号分隔的授权用户 |
| `BLUEBUBBLES_ALLOW_ALL_USERS` | 允许所有用户（`true`/`false`） |
| `QQ_APP_ID` | 来自 [q.qq.com](https://q.qq.com) 的 QQ Bot App ID |
| `QQ_CLIENT_SECRET` | 来自 [q.qq.com](https://q.qq.com) 的 QQ Bot App Secret |
| `QQ_STT_API_KEY` | 外部 STT 后备提供商的 API 密钥（可选，当 QQ 内置 ASR 不返回文本时使用） |
| `QQ_STT_BASE_URL` | 外部 STT 提供商的基础 URL（可选） |
| `QQ_STT_MODEL` | 外部 STT 提供商的模型名称（可选） |
| `QQ_ALLOWED_USERS` | 逗号分隔的允许与机器人通信的 QQ 用户 openID |
| `QQ_GROUP_ALLOWED_USERS` | 逗号分隔的群组 @消息访问的 QQ 群组 ID |
| `QQ_ALLOW_ALL_USERS` | 允许所有用户（`true`/`false`，覆盖 `QQ_ALLOWED_USERS`） |
| `QQ_HOME_CHANNEL` | 定时任务投递和通知的 QQ 用户/群组 openID |
| `MATTERMOST_URL` | Mattermost 服务器 URL（例如 `https://mm.example.com`） |
| `MATTERMOST_TOKEN` | Mattermost 的 Bot 令牌或个人访问令牌 |
| `MATTERMOST_ALLOWED_USERS` | 逗号分隔的允许与机器人通信的 Mattermost 用户 ID |
| `MATTERMOST_HOME_CHANNEL` | 主动消息投递（定时任务、通知）的频道 ID |
| `MATTERMOST_REQUIRE_MENTION` | 在频道中需要 `@mention`（默认：`true`）。设为 `false` 响应所有消息。 |
| `MATTERMOST_FREE_RESPONSE_CHANNELS` | 逗号分隔的机器人无需 `@mention` 即可响应的频道 ID |
| `MATTERMOST_REPLY_MODE` | 回复样式：`thread`（线程回复）或 `off`（扁平消息，默认） |
| `MATRIX_HOMESERVER` | Matrix 主服务器 URL（例如 `https://matrix.org`） |
| `MATRIX_ACCESS_TOKEN` | 机器人认证的 Matrix 访问令牌 |
| `MATRIX_USER_ID` | Matrix 用户 ID（例如 `@hermes:matrix.org`）— 密码登录必需，使用访问令牌时可选 |
| `MATRIX_PASSWORD` | Matrix 密码（访问令牌的替代方案） |
| `MATRIX_ALLOWED_USERS` | 逗号分隔的允许与机器人通信的 Matrix 用户 ID（例如 `@alice:matrix.org`） |
| `MATRIX_HOME_ROOM` | 主动消息投递的房间 ID（例如 `!abc123:matrix.org`） |
| `MATRIX_ENCRYPTION` | 启用端到端加密（`true`/`false`，默认：`false`） |
| `MATRIX_REQUIRE_MENTION` | 在房间中需要 `@mention`（默认：`true`）。设为 `false` 响应所有消息。 |
| `MATRIX_FREE_RESPONSE_ROOMS` | 逗号分隔的机器人无需 `@mention` 即可响应的房间 ID |
| `MATRIX_AUTO_THREAD` | 为房间消息自动创建线程（默认：`true`） |
| `MATRIX_DM_MENTION_THREADS` | 在私信中被 `@mention` 时创建线程（默认：`false`） |
| `MATRIX_RECOVERY_KEY` | 设备密钥轮换后的跨签名验证恢复密钥。建议在启用跨签名的 E2EE 设置中使用。 |
| `HASS_TOKEN` | Home Assistant 长期访问令牌（启用 HA 平台 + 工具） |
| `HASS_URL` | Home Assistant URL（默认：`http://homeassistant.local:8123`） |
| `WEBHOOK_ENABLED` | 启用 webhook 平台适配器（`true`/`false`） |
| `WEBHOOK_PORT` | 接收 webhook 的 HTTP 服务器端口（默认：`8644`） |
| `WEBHOOK_SECRET` | webhook 签名验证的全局 HMAC 密钥（当路由未指定自己的密钥时作为后备使用） |
| `API_SERVER_ENABLED` | 启用 OpenAI 兼容 API 服务器（`true`/`false`）。与其他平台一起运行。 |
| `API_SERVER_KEY` | API 服务器认证的 Bearer 令牌。非回环绑定时强制要求。 |
| `API_SERVER_CORS_ORIGINS` | 逗号分隔的允许直接调用 API 服务器的浏览器来源（例如 `http://localhost:3000,http://127.0.0.1:3000`）。默认：禁用。 |
| `API_SERVER_PORT` | API 服务器端口（默认：`8642`） |
| `API_SERVER_HOST` | API 服务器主机/绑定地址（默认：`127.0.0.1`）。使用 `0.0.0.0` 进行网络访问 — 需要 `API_SERVER_KEY` 和窄范围的 `API_SERVER_CORS_ORIGINS` 白名单。 |
| `API_SERVER_MODEL_NAME` | `/v1/models` 上公布的模型名称。默认为配置文件名称（默认配置文件为 `hermes-agent`）。对于多用户设置很有用，前端如 Open WebUI 需要每个连接有不同的模型名称。 |
| `GATEWAY_PROXY_URL` | 转发消息的远程 Hermes API 服务器 URL（[代理模式](/docs/user-guide/messaging/matrix#proxy-mode-e2ee-on-macos)）。设置后，网关仅处理平台 I/O — 所有代理工作委派给远程服务器。也可通过 `config.yaml` 中的 `gateway.proxy_url` 配置。 |
| `GATEWAY_PROXY_KEY` | 代理模式中与远程 API 服务器认证的 Bearer 令牌。必须与远程主机上的 `API_SERVER_KEY` 匹配。 |
| `MESSAGING_CWD` | 消息模式中终端命令的工作目录（默认：`~`） |
| `GATEWAY_ALLOWED_USERS` | 逗号分隔的跨所有平台允许的用户 ID |
| `GATEWAY_ALLOW_ALL_USERS` | 无需白名单允许所有用户（`true`/`false`，默认：`false`） |

## 代理行为

| 变量 | 描述 |
|------|------|
| `HERMES_MAX_ITERATIONS` | 每次对话的最大工具调用迭代次数（默认：90） |
| `HERMES_TOOL_PROGRESS` | 已弃用的工具进度显示兼容变量。请优先使用 `config.yaml` 中的 `display.tool_progress`。 |
| `HERMES_TOOL_PROGRESS_MODE` | 已弃用的工具进度模式兼容变量。请优先使用 `config.yaml` 中的 `display.tool_progress`。 |
| `HERMES_HUMAN_DELAY_MODE` | 响应节奏：`off`/`natural`/`custom` |
| `HERMES_HUMAN_DELAY_MIN_MS` | 自定义延迟范围最小值（毫秒） |
| `HERMES_HUMAN_DELAY_MAX_MS` | 自定义延迟范围最大值（毫秒） |
| `HERMES_QUIET` | 抑制非必要输出（`true`/`false`） |
| `HERMES_API_TIMEOUT` | LLM API 调用超时，单位秒（默认：`1800`） |
| `HERMES_STREAM_READ_TIMEOUT` | 流式套接字读取超时，单位秒（默认：`120`）。本地提供商自动增加到 `HERMES_API_TIMEOUT`。如果本地 LLM 在长代码生成时超时，请增加此值。 |
| `HERMES_STREAM_STALE_TIMEOUT` | 过期流检测超时，单位秒（默认：`180`）。本地提供商自动禁用。如果在此窗口内没有 chunk 到达，触发连接终止。 |
| `HERMES_EXEC_ASK` | 在网关模式中启用执行审批提示（`true`/`false`） |
| `HERMES_ENABLE_PROJECT_PLUGINS` | 启用从 `./.hermes/plugins/` 自动发现项目本地插件（`true`/`false`，默认：`false`） |
| `HERMES_BACKGROUND_NOTIFICATIONS` | 网关中后台进程通知模式：`all`（默认）、`result`、`error`、`off` |
| `HERMES_EPHEMERAL_SYSTEM_PROMPT` | 在 API 调用时注入的临时系统提示（从不持久化到会话） |

## 定时任务调度器

| 变量 | 描述 |
|------|------|
| `HERMES_CRON_TIMEOUT` | 定时任务代理运行的不活跃超时，单位秒（默认：`600`）。代理在积极调用工具或接收流式令牌时可以无限运行 — 这仅在空闲时触发。设为 `0` 表示无限。 |
| `HERMES_CRON_SCRIPT_TIMEOUT` | 附加到定时任务的预运行脚本超时，单位秒（默认：`120`）。为需要较长执行时间的脚本覆盖（例如反机器人计时的随机延迟）。也可通过 `config.yaml` 中的 `cron.script_timeout_seconds` 配置。 |

## 会话设置

| 变量 | 描述 |
|------|------|
| `SESSION_IDLE_MINUTES` | N 分钟不活跃后重置会话（默认：1440） |
| `SESSION_RESET_HOUR` | 每日重置时间，24 小时格式（默认：4 = 凌晨 4 点） |

## 上下文压缩（仅 config.yaml）

上下文压缩完全通过 `config.yaml` 配置 — 没有对应的环境变量。阈值设置在 `compression:` 块中，而摘要模型/提供商在 `auxiliary.compression:` 下。

```yaml
compression:
  enabled: true
  threshold: 0.50
  target_ratio: 0.20         # 保留为近期尾部的阈值比例
  protect_last_n: 20         # 保持未压缩的最少近期消息数
```

:::info 旧版迁移
使用 `compression.summary_model`、`compression.summary_provider` 和 `compression.summary_base_url` 的旧配置在首次加载时自动迁移到 `auxiliary.compression.*`。
:::

## 辅助任务覆盖

| 变量 | 描述 |
|------|------|
| `AUXILIARY_VISION_PROVIDER` | 覆盖视觉任务的提供商 |
| `AUXILIARY_VISION_MODEL` | 覆盖视觉任务的模型 |
| `AUXILIARY_VISION_BASE_URL` | 视觉任务的直接 OpenAI 兼容端点 |
| `AUXILIARY_VISION_API_KEY` | 与 `AUXILIARY_VISION_BASE_URL` 配对的 API 密钥 |
| `AUXILIARY_WEB_EXTRACT_PROVIDER` | 覆盖网页提取/摘要的提供商 |
| `AUXILIARY_WEB_EXTRACT_MODEL` | 覆盖网页提取/摘要的模型 |
| `AUXILIARY_WEB_EXTRACT_BASE_URL` | 网页提取/摘要的直接 OpenAI 兼容端点 |
| `AUXILIARY_WEB_EXTRACT_API_KEY` | 与 `AUXILIARY_WEB_EXTRACT_BASE_URL` 配对的 API 密钥 |

对于任务特定的直接端点，Hermes 使用任务配置的 API 密钥或 `OPENAI_API_KEY`。它不会为这些自定义端点复用 `OPENROUTER_API_KEY`。

## 备用模型（仅 config.yaml）

主模型备用完全通过 `config.yaml` 配置 — 没有对应的环境变量。添加带有 `provider` 和 `model` 键的 `fallback_model` 部分以启用主模型遇到错误时的自动切换。

```yaml
fallback_model:
  provider: openrouter
  model: anthropic/claude-sonnet-4
```

完整详情请参见[备用提供商](/docs/user-guide/features/fallback-providers)。

## 提供商路由（仅 config.yaml）

这些配置放在 `~/.hermes/config.yaml` 的 `provider_routing` 部分下：

| 键 | 描述 |
|----|------|
| `sort` | 排序提供商：`"price"`（默认）、`"throughput"` 或 `"latency"` |
| `only` | 允许的提供商标识列表（例如 `["anthropic", "google"]`） |
| `ignore` | 要跳过的提供商标识列表 |
| `order` | 按顺序尝试的提供商标识列表 |
| `require_parameters` | 仅使用支持所有请求参数的提供商（`true`/`false`） |
| `data_collection` | `"allow"`（默认）或 `"deny"` 排除存储数据的提供商 |

:::tip
使用 `hermes config set` 设置环境变量 — 它会自动保存到正确的文件（密钥保存到 `.env`，其他保存到 `config.yaml`）。
:::
