---
sidebar_position: 1
title: "架构"
description: "Hermes Agent 内部机制 — 主要子系统、执行路径、数据流，以及后续阅读指引"
---

# 架构

本页是 Hermes Agent 内部机制的顶层导览图。利用它在代码库中定位自己，然后深入各子系统的专题文档了解实现细节。

## 系统概览

```text
┌─────────────────────────────────────────────────────────────────────┐
│                          入口点                                      │
│                                                                      │
│  CLI (cli.py)    网关 (gateway/run.py)    ACP (acp_adapter/)        │
│  批量运行器      API 服务器               Python 库                  │
└──────────┬──────────────┬───────────────────────┬───────────────────┘
           │              │                       │
           ▼              ▼                       ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     AIAgent (run_agent.py)                          │
│                                                                     │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐               │
│  │ 提示词       │  │ 提供商       │  │ 工具         │               │
│  │ 构建器       │  │ 解析         │  │ 调度         │               │
│  │ (prompt_     │  │ (runtime_    │  │ (model_      │               │
│  │  builder.py) │  │  provider.py)│  │  tools.py)   │               │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘               │
│         │                 │                 │                       │
│  ┌──────┴───────┐  ┌──────┴───────┐  ┌──────┴───────┐               │
│  │ 压缩         │  │ 3 种 API 模式│  │ 工具注册表   │               │
│  │ 与缓存       │  │ chat_compl.  │  │ (registry.py)│               │
│  │              │  │ codex_resp.  │  │ 47 个工具    │               │
│  │              │  │ anthropic    │  │ 19 个工具集  │               │
│  └──────────────┘  └──────────────┘  └──────────────┘               │
└─────────────────────────────────────────────────────────────────────┘
           │                                    │
           ▼                                    ▼
┌───────────────────┐              ┌──────────────────────┐
│ 会话存储           │              │ 工具后端              │
│ (SQLite + FTS5)   │              │ 终端 (6 种后端)       │
│ hermes_state.py   │              │ 浏览器 (5 种后端)     │
│ gateway/session.py│              │ Web (4 种后端)        │
└───────────────────┘              │ MCP (动态)            │
                                   │ 文件、视觉等          │
                                   └──────────────────────┘
```

## 目录结构

```text
hermes-agent/
├── run_agent.py              # AIAgent — 核心对话循环 (~10,700 行)
├── cli.py                    # HermesCLI — 交互式终端 UI (~10,000 行)
├── model_tools.py            # 工具发现、Schema 收集、调度
├── toolsets.py               # 工具分组和平台预设
├── hermes_state.py           # SQLite 会话/状态数据库（含 FTS5）
├── hermes_constants.py       # HERMES_HOME、配置文件感知路径
├── batch_runner.py           # 批量轨迹生成
│
├── agent/                    # Agent 内部模块
│   ├── prompt_builder.py     # 系统提示词组装
│   ├── context_engine.py     # ContextEngine 抽象基类（可插拔）
│   ├── context_compressor.py # 默认引擎 — 有损摘要
│   ├── prompt_caching.py     # Anthropic 提示词缓存
│   ├── auxiliary_client.py   # 辅助 LLM 用于附加任务（视觉、摘要）
│   ├── model_metadata.py     # 模型上下文长度、token 估算
│   ├── models_dev.py         # models.dev 注册表集成
│   ├── anthropic_adapter.py  # Anthropic Messages API 格式转换
│   ├── display.py            # KawaiiSpinner、工具预览格式化
│   ├── skill_commands.py     # 技能斜杠命令
│   ├── memory_manager.py     # 记忆管理器编排
│   ├── memory_provider.py    # 记忆提供商抽象基类
│   └── trajectory.py         # 轨迹保存辅助函数
│
├── hermes_cli/               # CLI 子命令和设置
│   ├── main.py               # 入口点 — 所有 `hermes` 子命令 (~6,000 行)
│   ├── config.py             # DEFAULT_CONFIG、OPTIONAL_ENV_VARS、迁移
│   ├── commands.py           # COMMAND_REGISTRY — 斜杠命令集中定义
│   ├── auth.py               # PROVIDER_REGISTRY、凭证解析
│   ├── runtime_provider.py   # 提供商 → api_mode + 凭证
│   ├── models.py             # 模型目录、提供商模型列表
│   ├── model_switch.py       # /model 命令逻辑（CLI + 网关共享）
│   ├── setup.py              # 交互式设置向导 (~3,100 行)
│   ├── skin_engine.py        # CLI 主题引擎
│   ├── skills_config.py      # hermes skills — 按平台启用/禁用
│   ├── skills_hub.py         # /skills 斜杠命令
│   ├── tools_config.py       # hermes tools — 按平台启用/禁用
│   ├── plugins.py            # PluginManager — 发现、加载、钩子
│   ├── callbacks.py          # 终端回调（澄清、sudo、审批）
│   └── gateway.py            # hermes gateway start/stop
│
├── tools/                    # 工具实现（每个工具一个文件）
│   ├── registry.py           # 中央工具注册表
│   ├── approval.py           # 危险命令检测
│   ├── terminal_tool.py      # 终端编排
│   ├── process_registry.py   # 后台进程管理
│   ├── file_tools.py         # read_file、write_file、patch、search_files
│   ├── web_tools.py          # web_search、web_extract
│   ├── browser_tool.py       # 10 个浏览器自动化工具
│   ├── code_execution_tool.py # execute_code 沙箱
│   ├── delegate_tool.py      # 子 Agent 委派
│   ├── mcp_tool.py           # MCP 客户端 (~2,200 行)
│   ├── credential_files.py   # 基于文件的凭证透传
│   ├── env_passthrough.py    # 沙箱环境变量透传
│   ├── ansi_strip.py         # ANSI 转义序列剥离
│   └── environments/         # 终端后端（local、docker、ssh、modal、daytona、singularity）
│
├── gateway/                  # 消息平台网关
│   ├── run.py                # GatewayRunner — 消息调度 (~9,000 行)
│   ├── session.py            # SessionStore — 会话持久化
│   ├── delivery.py           # 出站消息投递
│   ├── pairing.py            # DM 配对授权
│   ├── hooks.py              # 钩子发现和生命周期事件
│   ├── mirror.py             # 跨会话消息镜像
│   ├── status.py             # Token 锁、配置文件范围的进程追踪
│   ├── builtin_hooks/        # 始终注册的钩子
│   └── platforms/            # 18 个适配器：telegram、discord、slack、whatsapp、
│                             #   signal、matrix、mattermost、email、sms、
│                             #   dingtalk、feishu、wecom、wecom_callback、weixin、
│                             #   bluebubbles、qqbot、homeassistant、webhook、api_server
│
├── acp_adapter/              # ACP 服务器（VS Code / Zed / JetBrains）
├── cron/                     # 调度器（jobs.py、scheduler.py）
├── plugins/memory/           # 记忆提供商插件
├── plugins/context_engine/   # 上下文引擎插件
├── environments/             # RL 训练环境（Atropos）
├── skills/                   # 内置技能（始终可用）
├── optional-skills/          # 官方可选技能（需显式安装）
├── website/                  # Docusaurus 文档站点
└── tests/                    # Pytest 测试套件 (~3,000+ 个测试)
```

## 数据流

### CLI 会话

```text
用户输入 → HermesCLI.process_input()
  → AIAgent.run_conversation()
    → prompt_builder.build_system_prompt()
    → runtime_provider.resolve_runtime_provider()
    → API 调用 (chat_completions / codex_responses / anthropic_messages)
    → tool_calls? → model_tools.handle_function_call() → 循环
    → 最终响应 → 显示 → 保存到 SessionDB
```

### 网关消息

```text
平台事件 → Adapter.on_message() → MessageEvent
  → GatewayRunner._handle_message()
    → 授权用户
    → 解析会话键
    → 创建带有会话历史的 AIAgent
    → AIAgent.run_conversation()
    → 通过适配器返回响应
```

### 定时任务

```text
调度器 tick → 从 jobs.json 加载到期任务
  → 创建全新的 AIAgent（无历史记录）
  → 注入附加的技能作为上下文
  → 运行任务提示词
  → 将响应投递到目标平台
  → 更新任务状态和 next_run
```

## 推荐阅读顺序

如果你是代码库新手：

1. **本页** — 建立整体认知
2. **[Agent 循环内部机制](./agent-loop.md)** — AIAgent 的工作原理
3. **[提示词组装](./prompt-assembly.md)** — 系统提示词构建
4. **[提供商运行时解析](./provider-runtime.md)** — 提供商如何被选择
5. **[添加提供商](./adding-providers.md)** — 添加新提供商的实践指南
6. **[工具运行时](./tools-runtime.md)** — 工具注册表、调度、环境
7. **[会话存储](./session-storage.md)** — SQLite Schema、FTS5、会话谱系
8. **[网关内部机制](./gateway-internals.md)** — 消息平台网关
9. **[上下文压缩与提示词缓存](./context-compression-and-caching.md)** — 压缩和缓存
10. **[ACP 内部机制](./acp-internals.md)** — IDE 集成
11. **[环境、基准测试与数据生成](./environments.md)** — RL 训练

## 主要子系统

### Agent 循环

同步编排引擎（`run_agent.py` 中的 `AIAgent`）。处理提供商选择、提示词构建、工具执行、重试、回退、回调、压缩和持久化。支持三种 API 模式以适配不同的提供商后端。

→ [Agent 循环内部机制](./agent-loop.md)

### 提示词系统

在会话生命周期中进行提示词构建和维护：

- **`prompt_builder.py`** — 从以下来源组装系统提示词：个性（SOUL.md）、记忆（MEMORY.md、USER.md）、技能、上下文文件（AGENTS.md、.hermes.md）、工具使用指导和模型特定指令
- **`prompt_caching.py`** — 为前缀缓存应用 Anthropic 缓存断点
- **`context_compressor.py`** — 当上下文超过阈值时，对中间对话轮次进行摘要

→ [提示词组装](./prompt-assembly.md)、[上下文压缩与提示词缓存](./context-compression-and-caching.md)

### 提供商解析

CLI、网关、定时任务、ACP 和辅助调用共享的运行时解析器。将 `(provider, model)` 元组映射到 `(api_mode, api_key, base_url)`。处理 18+ 个提供商、OAuth 流程、凭证池和别名解析。

→ [提供商运行时解析](./provider-runtime.md)

### 工具系统

中央工具注册表（`tools/registry.py`），包含 19 个工具集中的 47 个注册工具。每个工具文件在导入时自注册。注册表处理 Schema 收集、调度、可用性检查和错误包装。终端工具支持 6 种后端（local、Docker、SSH、Daytona、Modal、Singularity）。

→ [工具运行时](./tools-runtime.md)

### 会话持久化

基于 SQLite 的会话存储，支持 FTS5 全文搜索。会话具有谱系追踪功能（跨压缩的父/子关系）、按平台隔离和带竞争处理的原子写入。

→ [会话存储](./session-storage.md)

### 消息网关

长时间运行的进程，包含 18 个平台适配器、统一会话路由、用户授权（白名单 + DM 配对）、斜杠命令调度、钩子系统、定时任务 tick 和后台维护。

→ [网关内部机制](./gateway-internals.md)

### 插件系统

三个发现来源：`~/.hermes/plugins/`（用户级）、`.hermes/plugins/`（项目级）和 pip 入口点。插件通过上下文 API 注册工具、钩子和 CLI 命令。存在两种专门的插件类型：记忆提供商（`plugins/memory/`）和上下文引擎（`plugins/context_engine/`）。两者都是单选的 — 每种类型同时只能激活一个，通过 `hermes plugins` 或 `config.yaml` 配置。

→ [插件指南](/docs/guides/build-a-hermes-plugin)、[记忆提供商插件](./memory-provider-plugin.md)

### 定时任务

一等公民的 Agent 任务（非 shell 任务）。任务以 JSON 存储，支持多种调度格式，可附加技能和脚本，并可投递到任何平台。

→ [定时任务内部机制](./cron-internals.md)

### ACP 集成

通过 stdio/JSON-RPC 将 Hermes 暴露为编辑器原生 Agent，支持 VS Code、Zed 和 JetBrains。

→ [ACP 内部机制](./acp-internals.md)

### RL / 环境 / 轨迹

完整的评估和 RL 训练环境框架。与 Atropos 集成，支持多种工具调用解析器，并生成 ShareGPT 格式的轨迹。

→ [环境、基准测试与数据生成](./environments.md)、[轨迹与训练格式](./trajectory-format.md)

## 设计原则

| 原则 | 实践意义 |
|-----------|--------------------------|
| **提示词稳定性** | 系统提示词在对话过程中不会改变。除了用户显式操作（`/model`）外，不会产生破坏缓存的变更。 |
| **可观察的执行** | 每个工具调用都通过回调对用户可见。CLI 中有进度更新（旋转器），网关中有聊天消息。 |
| **可中断** | API 调用和工具执行可以被用户输入或信号在执行中取消。 |
| **平台无关的核心** | 一个 AIAgent 类服务于 CLI、网关、ACP、批量处理和 API 服务器。平台差异存在于入口点，而非 Agent。 |
| **松耦合** | 可选子系统（MCP、插件、记忆提供商、RL 环境）使用注册表模式和 check_fn 门控，而非硬依赖。 |
| **配置文件隔离** | 每个配置文件（`hermes -p <name>`）拥有自己的 HERMES_HOME、配置、记忆、会话和网关 PID。多个配置文件可并发运行。 |

## 文件依赖链

```text
tools/registry.py  (无依赖 — 被所有工具文件导入)
       ↑
tools/*.py  (每个文件在导入时调用 registry.register())
       ↑
model_tools.py  (导入 tools/registry + 触发工具发现)
       ↑
run_agent.py、cli.py、batch_runner.py、environments/
```

这条链意味着工具注册在导入时发生，在任何 Agent 实例创建之前。任何在顶层调用 `registry.register()` 的 `tools/*.py` 文件都会被自动发现 — 不需要手动维护导入列表。
