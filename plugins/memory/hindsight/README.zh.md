# Hindsight 记忆提供商

具有知识图谱、实体解析和多策略检索的长期记忆。支持云端、本地嵌入和本地外部三种模式。

## 要求

- **云端：**来自 [ui.hindsight.vectorize.io](https://ui.hindsight.vectorize.io) 的 API 密钥
- **本地嵌入：**支持的 LLM 提供商的 API 密钥（OpenAI、Anthropic、Gemini、Groq、OpenRouter、MiniMax、Ollama 或任何 OpenAI 兼容端点）。嵌入和重排序在本地运行——不需要额外的 API 密钥。
- **本地外部：**一个运行中的 Hindsight 实例（Docker 或自托管），可通过 HTTP 访问。

## 设置

```bash
hermes memory setup    # 选择 "hindsight"
```

设置向导会自动通过 `uv` 安装依赖并引导您完成配置。

或手动设置（云端模式，使用默认值）：
```bash
hermes config set memory.provider hindsight
echo "HINDSIGHT_API_KEY=your-key" >> ~/.hermes/.env
```

### 云端

连接到 Hindsight Cloud API。需要来自 [ui.hindsight.vectorize.io](https://ui.hindsight.vectorize.io) 的 API 密钥。

### 本地嵌入

Hermes 启动一个带有内置 PostgreSQL 的本地 Hindsight 守护进程。需要 LLM API 密钥用于记忆提取和合成。守护进程在首次使用时自动在后台启动，闲置 5 分钟后停止。

支持任何 OpenAI 兼容的 LLM 端点（llama.cpp、vLLM、LM Studio 等）——选择 `openai_compatible` 作为提供商并输入基础 URL。

守护进程启动日志：`~/.hermes/logs/hindsight-embed.log`
守护进程运行时日志：`~/.hindsight/profiles/<profile>.log`

要打开 Hindsight Web UI（仅本地嵌入模式）：
```bash
hindsight-embed -p hermes ui start
```

### 本地外部

将插件指向您已在运行的现有 Hindsight 实例（Docker、自托管等）。无守护进程管理——只需一个 URL 和一个可选的 API 密钥。

## 配置

配置文件：`~/.hermes/hindsight/config.json`

### 连接

| 键 | 默认值 | 描述 |
|----|--------|------|
| `mode` | `cloud` | `cloud`、`local_embedded` 或 `local_external` |
| `api_url` | `https://api.hindsight.vectorize.io` | API URL（cloud 和 local_external 模式） |

### 记忆库

| 键 | 默认值 | 描述 |
|----|--------|------|
| `bank_id` | `hermes` | 记忆库名称 |
| `bank_mission` | — | Reflect 使命（reflect 推理的身份/框架）。通过 Banks API 应用。 |
| `bank_retain_mission` | — | Retain 使命（引导提取内容）。通过 Banks API 应用。 |

### 召回

| 键 | 默认值 | 描述 |
|----|--------|------|
| `recall_budget` | `mid` | 召回彻底性：`low` / `mid` / `high` |
| `recall_prefetch_method` | `recall` | 自动召回方法：`recall`（原始事实）或 `reflect`（LLM 合成） |
| `recall_max_tokens` | `4096` | 召回结果的最大 token 数 |
| `recall_max_input_chars` | `800` | 自动召回的最大输入查询长度 |
| `recall_prompt_preamble` | — | 上下文中已召回记忆的自定义前言 |
| `recall_tags` | — | 搜索记忆时的标签过滤 |
| `recall_tags_match` | `any` | 标签匹配模式：`any` / `all` / `any_strict` / `all_strict` |
| `auto_recall` | `true` | 在每个轮次前自动召回记忆 |

### 保留

| 键 | 默认值 | 描述 |
|----|--------|------|
| `auto_retain` | `true` | 自动保留对话轮次 |
| `retain_async` | `true` | 在 Hindsight 服务器上异步处理保留 |
| `retain_every_n_turns` | `1` | 每 N 轮保留一次（1 = 每轮） |
| `retain_context` | `conversation between Hermes Agent and the User` | 已保留记忆的上下文标签 |
| `tags` | — | 存储记忆时应用的标签 |

### 集成

| 键 | 默认值 | 描述 |
|----|--------|------|
| `memory_mode` | `hybrid` | 记忆如何集成到代理中 |

**memory_mode：**
- `hybrid` — 自动上下文注入 + 工具对 LLM 可用
- `context` — 仅自动注入，不暴露工具
- `tools` — 仅工具，无自动注入

### 本地嵌入 LLM

| 键 | 默认值 | 描述 |
|----|--------|------|
| `llm_provider` | `openai` | `openai`、`anthropic`、`gemini`、`groq`、`openrouter`、`minimax`、`ollama`、`lmstudio`、`openai_compatible` |
| `llm_model` | 按提供商不同 | 模型名称（例如 `gpt-4o-mini`、`qwen/qwen3.5-9b`） |
| `llm_base_url` | — | `openai_compatible` 的端点 URL（例如 `http://192.168.1.10:8080/v1`） |

LLM API 密钥存储在 `~/.hermes/.env` 中，键名为 `HINDSIGHT_LLM_API_KEY`。

## 工具

在 `hybrid` 和 `tools` 记忆模式下可用：

| 工具 | 描述 |
|------|------|
| `hindsight_retain` | 存储信息并自动提取实体 |
| `hindsight_recall` | 多策略搜索（语义 + 实体图谱） |
| `hindsight_reflect` | 跨记忆合成（LLM 驱动） |

## 环境变量

| 变量 | 描述 |
|------|------|
| `HINDSIGHT_API_KEY` | Hindsight Cloud 的 API 密钥 |
| `HINDSIGHT_LLM_API_KEY` | 本地模式的 LLM API 密钥 |
| `HINDSIGHT_API_LLM_BASE_URL` | 本地模式的 LLM 基础 URL（例如 OpenRouter） |
| `HINDSIGHT_API_URL` | 覆盖 API 端点 |
| `HINDSIGHT_BANK_ID` | 覆盖记忆库名称 |
| `HINDSIGHT_BUDGET` | 覆盖召回预算 |
| `HINDSIGHT_MODE` | 覆盖模式（`cloud`、`local_embedded`、`local_external`） |

## 客户端版本

需要 `hindsight-client >= 0.4.22`。如果检测到旧版本，插件会在会话启动时自动升级。
