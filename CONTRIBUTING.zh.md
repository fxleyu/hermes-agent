# Hermes Agent 贡献指南

感谢你为 Hermes Agent 做出贡献！本指南涵盖了你需要的一切：设置开发环境、理解架构、决定构建什么以及让你的 PR 被合并。

---

## 贡献优先级

我们按以下顺序评估贡献的价值：

1. **Bug 修复** — 崩溃、错误行为、数据丢失。始终是最高优先级。
2. **跨平台兼容性** — Windows、macOS、不同的 Linux 发行版、不同的终端模拟器。我们希望 Hermes 能在任何地方运行。
3. **安全加固** — Shell 注入、提示注入、路径遍历、权限提升。参见[安全注意事项](#安全注意事项)。
4. **性能和健壮性** — 重试逻辑、错误处理、优雅降级。
5. **新技能** — 但仅限于广泛有用的技能。参见[应该做成技能还是工具？](#应该做成技能还是工具)
6. **新工具** — 很少需要。大多数功能应该是技能。参见下文。
7. **文档** — 修正、澄清、新示例。

---

## 应该做成技能还是工具？

这是新贡献者最常问的问题。答案几乎总是**技能**。

### 在以下情况下做成技能：

- 该功能可以用指令 + Shell 命令 + 现有工具来表达
- 它封装了一个外部 CLI 或 API，智能体可以通过 `terminal` 或 `web_extract` 调用
- 它不需要自定义 Python 集成或内置于智能体中的 API 密钥管理
- 示例：arXiv 搜索、git 工作流、Docker 管理、PDF 处理、通过 CLI 工具发送邮件

### 在以下情况下做成工具：

- 它需要端到端的集成，包括 API 密钥、认证流程或由智能体框架管理的多组件配置
- 它需要自定义处理逻辑，必须每次精确执行（而非 LLM 解释的"尽力而为"）
- 它处理二进制数据、流式传输或无法通过终端传递的实时事件
- 示例：浏览器自动化（Browserbase 会话管理）、TTS（音频编码 + 平台投递）、视觉分析（base64 图像处理）

### 技能是否应该内置？

内置技能（在 `skills/` 中）随每次 Hermes 安装分发。它们应该**对大多数用户广泛有用**：

- 文档处理、网络研究、常见开发工作流、系统管理
- 被广泛人群经常使用

如果你的技能是官方的且有用，但并非所有人都需要（例如付费服务集成、重量级依赖），请将其放在 **`optional-skills/`** 中——它随仓库一起发布但默认不激活。用户可以通过 `hermes skills browse`（标记为"official"）发现它，并使用 `hermes skills install` 安装（无第三方警告，内置信任）。

如果你的技能是专业的、社区贡献的或小众的，更适合放在**技能中心**——将其上传到技能注册表并在 [Nous Research Discord](https://discord.gg/NousResearch) 中分享。用户可以使用 `hermes skills install` 安装。

---

## 开发环境设置

### 前置条件

| 要求 | 说明 |
|-------------|-------|
| **Git** | 需要支持 `--recurse-submodules` |
| **Python 3.11+** | 如果缺失，uv 会自动安装 |
| **uv** | 快速 Python 包管理器（[安装](https://docs.astral.sh/uv/)） |
| **Node.js 18+** | 可选——浏览器工具和 WhatsApp 桥接需要 |

### 克隆和安装

```bash
git clone --recurse-submodules https://github.com/NousResearch/hermes-agent.git
cd hermes-agent

# 使用 Python 3.11 创建虚拟环境
uv venv venv --python 3.11
export VIRTUAL_ENV="$(pwd)/venv"

# 安装所有额外依赖（消息、定时任务、CLI 菜单、开发工具）
uv pip install -e ".[all,dev]"

# 可选：强化学习训练子模块
# git submodule update --init tinker-atropos && uv pip install -e "./tinker-atropos"

# 可选：浏览器工具
npm install
```

### 配置开发环境

```bash
mkdir -p ~/.hermes/{cron,sessions,logs,memories,skills}
cp cli-config.yaml.example ~/.hermes/config.yaml
touch ~/.hermes/.env

# 至少添加一个 LLM 提供商密钥：
echo 'OPENROUTER_API_KEY=sk-or-v1-your-key' >> ~/.hermes/.env
```

### 运行

```bash
# 创建符号链接以便全局访问
mkdir -p ~/.local/bin
ln -sf "$(pwd)/venv/bin/hermes" ~/.local/bin/hermes

# 验证
hermes doctor
hermes chat -q "Hello"
```

### 运行测试

```bash
pytest tests/ -v
```

---

## 项目结构

```
hermes-agent/
├── run_agent.py              # AIAgent 类——核心对话循环、工具调度、会话持久化
├── cli.py                    # HermesCLI 类——交互式 TUI、prompt_toolkit 集成
├── model_tools.py            # 工具编排（tools/registry.py 的薄封装层）
├── toolsets.py               # 工具分组和预设（hermes-cli、hermes-telegram 等）
├── hermes_state.py           # SQLite 会话数据库，支持 FTS5 全文搜索和会话标题
├── batch_runner.py           # 用于轨迹生成的并行批处理
│
├── agent/                    # 智能体内部模块（提取的模块）
│   ├── prompt_builder.py         # 系统提示词组装（身份、技能、上下文文件、记忆）
│   ├── context_compressor.py     # 接近上下文限制时自动摘要
│   ├── auxiliary_client.py       # 解析辅助 OpenAI 客户端（摘要、视觉）
│   ├── display.py                # KawaiiSpinner、工具进度格式化
│   ├── model_metadata.py         # 模型上下文长度、token 估算
│   └── trajectory.py             # 轨迹保存辅助工具
│
├── hermes_cli/               # CLI 命令实现
│   ├── main.py                   # 入口点、参数解析、命令分发
│   ├── config.py                 # 配置管理、迁移、环境变量定义
│   ├── setup.py                  # 交互式设置向导
│   ├── auth.py                   # 提供商解析、OAuth、Nous Portal
│   ├── models.py                 # OpenRouter 模型选择列表
│   ├── banner.py                 # 欢迎横幅、ASCII 艺术字
│   ├── commands.py               # 中央斜杠命令注册表（CommandDef）、自动补全、网关辅助
│   ├── callbacks.py              # 交互式回调（澄清、sudo、审批）
│   ├── doctor.py                 # 诊断工具
│   ├── skills_hub.py             # 技能中心 CLI + /skills 斜杠命令
│   └── skin_engine.py            # 皮肤/主题引擎——数据驱动的 CLI 视觉自定义
│
├── tools/                    # 工具实现（自注册）
│   ├── registry.py               # 中央工具注册表（模式、处理器、调度）
│   ├── approval.py               # 危险命令检测 + 每会话审批
│   ├── terminal_tool.py          # 终端编排（sudo、环境生命周期、后端）
│   ├── file_operations.py        # read_file、write_file、search、patch 等
│   ├── web_tools.py              # web_search、web_extract（Parallel/Firecrawl + Gemini 摘要）
│   ├── vision_tools.py           # 通过多模态模型进行图像分析
│   ├── delegate_tool.py          # 子智能体生成和并行任务执行
│   ├── code_execution_tool.py    # 带有 RPC 工具访问的沙盒 Python
│   ├── session_search_tool.py    # 使用 FTS5 + 摘要搜索历史对话
│   ├── cronjob_tools.py          # 定时任务管理
│   ├── skill_tools.py            # 技能搜索、加载、管理
│   └── environments/             # 终端执行后端
│       ├── base.py                   # BaseEnvironment 抽象基类
│       ├── local.py、docker.py、ssh.py、singularity.py、modal.py、daytona.py
│
├── gateway/                  # 消息网关
│   ├── run.py                    # GatewayRunner——平台生命周期、消息路由、定时任务
│   ├── config.py                 # 平台配置解析
│   ├── session.py                # 会话存储、上下文提示词、重置策略
│   └── platforms/                # 平台适配器
│       ├── telegram.py、discord_adapter.py、slack.py、whatsapp.py
│
├── scripts/                  # 安装程序和桥接脚本
│   ├── install.sh                # Linux/macOS 安装程序
│   ├── install.ps1               # Windows PowerShell 安装程序
│   └── whatsapp-bridge/          # Node.js WhatsApp 桥接（Baileys）
│
├── skills/                   # 内置技能（安装时复制到 ~/.hermes/skills/）
├── optional-skills/          # 官方可选技能（可通过技能中心发现，默认不激活）
├── environments/             # 强化学习训练环境（Atropos 集成）
├── tests/                    # 测试套件
├── website/                  # 文档网站（hermes-agent.nousresearch.com）
│
├── cli-config.yaml.example   # 示例配置（复制到 ~/.hermes/config.yaml）
└── AGENTS.md                 # AI 编码助手开发指南
```

### 用户配置（存储在 `~/.hermes/` 中）

| 路径 | 用途 |
|------|---------|
| `~/.hermes/config.yaml` | 设置（模型、终端、工具集、压缩等） |
| `~/.hermes/.env` | API 密钥和密钥 |
| `~/.hermes/auth.json` | OAuth 凭据（Nous Portal） |
| `~/.hermes/skills/` | 所有激活的技能（内置 + 从中心安装 + 智能体创建） |
| `~/.hermes/memories/` | 持久记忆（MEMORY.md、USER.md） |
| `~/.hermes/state.db` | SQLite 会话数据库 |
| `~/.hermes/sessions/` | JSON 会话日志 |
| `~/.hermes/cron/` | 定时任务数据 |
| `~/.hermes/whatsapp/session/` | WhatsApp 桥接凭据 |

---

## 架构概览

### 核心循环

```
用户消息 → AIAgent._run_agent_loop()
  ├── 构建系统提示词 (prompt_builder.py)
  ├── 构建 API 参数（模型、消息、工具、推理配置）
  ├── 调用 LLM（OpenAI 兼容 API）
  ├── 如果响应中包含 tool_calls：
  │     ├── 通过注册表调度执行每个工具
  │     ├── 将工具结果添加到对话中
  │     └── 循环回到 LLM 调用
  ├── 如果是文本响应：
  │     ├── 将会话持久化到数据库
  │     └── 返回 final_response
  └── 如果接近 token 限制则进行上下文压缩
```

### 关键设计模式

- **自注册工具**：每个工具文件在导入时调用 `registry.register()`。`model_tools.py` 通过导入所有工具模块触发发现。
- **工具集分组**：工具被分组为工具集（`web`、`terminal`、`file`、`browser` 等），可以按平台启用/禁用。
- **会话持久化**：所有对话存储在 SQLite（`hermes_state.py`）中，支持全文搜索和唯一会话标题。JSON 日志保存到 `~/.hermes/sessions/`。
- **临时注入**：系统提示词和预填充消息在 API 调用时注入，不会持久化到数据库或日志中。
- **提供商抽象**：智能体与任何 OpenAI 兼容 API 配合工作。提供商解析在初始化时发生（Nous Portal OAuth、OpenRouter API 密钥或自定义端点）。
- **提供商路由**：使用 OpenRouter 时，`config.yaml` 中的 `provider_routing` 控制提供商选择（按吞吐量/延迟/价格排序、允许/忽略特定提供商、数据保留策略）。这些作为 `extra_body.provider` 注入 API 请求。

---

## 代码风格

- **PEP 8**，有实用性例外（我们不强制严格的行长度限制）
- **注释**：仅在解释非显而易见的意图、权衡或 API 特殊行为时使用。不要复述代码做了什么——`# 递增计数器` 没有任何价值
- **错误处理**：捕获特定异常。使用 `logger.warning()`/`logger.error()` 记录日志——对意外错误使用 `exc_info=True`，以便堆栈跟踪出现在日志中
- **跨平台**：永远不要假设 Unix。参见[跨平台兼容性](#跨平台兼容性)

---

## 添加新工具

在编写工具之前，先问问：[这应该做成技能吗？](#应该做成技能还是工具)

工具通过中央注册表自注册。每个工具文件将其模式、处理器和注册放在一起：

```python
"""my_tool — 简要描述这个工具的功能。"""

import json
from tools.registry import registry


def my_tool(param1: str, param2: int = 10, **kwargs) -> str:
    """处理器。返回字符串结果（通常是 JSON）。"""
    result = do_work(param1, param2)
    return json.dumps(result)


MY_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "my_tool",
        "description": "这个工具做什么以及智能体何时应该使用它。",
        "parameters": {
            "type": "object",
            "properties": {
                "param1": {"type": "string", "description": "param1 是什么"},
                "param2": {"type": "integer", "description": "param2 是什么", "default": 10},
            },
            "required": ["param1"],
        },
    },
}


def _check_requirements() -> bool:
    """如果此工具的依赖可用，返回 True。"""
    return True


registry.register(
    name="my_tool",
    toolset="my_toolset",
    schema=MY_TOOL_SCHEMA,
    handler=lambda args, **kw: my_tool(**args, **kw),
    check_fn=_check_requirements,
)
```

然后在 `model_tools.py` 的 `_modules` 列表中添加导入：

```python
_modules = [
    # ... 现有模块 ...
    "tools.my_tool",
]
```

如果是新的工具集，需要将其添加到 `toolsets.py` 以及相关的平台预设中。

---

## 添加技能

内置技能按类别组织在 `skills/` 中。官方可选技能使用相同的结构放在 `optional-skills/` 中：

```
skills/
├── research/
│   └── arxiv/
│       ├── SKILL.md              # 必需：主要指令
│       └── scripts/              # 可选：辅助脚本
│           └── search_arxiv.py
├── productivity/
│   └── ocr-and-documents/
│       ├── SKILL.md
│       ├── scripts/
│       └── references/
└── ...
```

### SKILL.md 格式

```markdown
---
name: my-skill
description: 简要描述（显示在技能搜索结果中）
version: 1.0.0
author: 你的名字
license: MIT
platforms: [macos, linux]          # 可选——限制到特定操作系统平台
                                   #   有效值：macos、linux、windows
                                   #   省略则在所有平台加载（默认）
required_environment_variables:    # 可选——安全的加载时设置元数据
  - name: MY_API_KEY
    prompt: API 密钥
    help: 在哪里获取
    required_for: 完整功能
prerequisites:                     # 可选的旧版运行时要求
  env_vars: [MY_API_KEY]           #   向后兼容的环境变量别名
  commands: [curl, jq]             #   仅供参考；不会隐藏技能
metadata:
  hermes:
    tags: [类别, 子类别, 关键词]
    related_skills: [other-skill-name]
    fallback_for_toolsets: [web]       # 可选——仅在工具集不可用时显示
    requires_toolsets: [terminal]      # 可选——仅在工具集可用时显示
---

# 技能标题

简要介绍。

## 何时使用
触发条件——智能体何时应该加载此技能？

## 快速参考
常见命令或 API 调用表。

## 步骤
智能体遵循的分步指令。

## 陷阱
已知的失败模式及处理方法。

## 验证
智能体如何确认操作成功。
```

### 平台特定技能

技能可以通过 `platforms` 前置元数据字段声明支持哪些操作系统平台。带有此字段的技能在不兼容的平台上会自动从系统提示词、`skills_list()` 和斜杠命令中隐藏。

```yaml
platforms: [macos]            # 仅 macOS（例如 iMessage、Apple 提醒事项）
platforms: [macos, linux]     # macOS 和 Linux
platforms: [windows]          # 仅 Windows
```

如果省略该字段或为空，技能将在所有平台加载（向后兼容）。参见 `skills/apple/` 中 macOS 专属技能的示例。

### 条件技能激活

技能可以声明条件来控制其在系统提示词中的显示时机，基于当前会话中可用的工具和工具集。这主要用于**降级技能**——仅在主要工具不可用时才显示的替代方案。

在 `metadata.hermes` 下支持四个字段：

```yaml
metadata:
  hermes:
    fallback_for_toolsets: [web]      # 仅在这些工具集不可用时显示
    requires_toolsets: [terminal]     # 仅在这些工具集可用时显示
    fallback_for_tools: [web_search]  # 仅在这些特定工具不可用时显示
    requires_tools: [terminal]        # 仅在这些特定工具可用时显示
```

**语义：**
- `fallback_for_*`：该技能是备选方案。当列出的工具/工具集可用时**隐藏**，不可用时**显示**。用于免费替代付费工具。
- `requires_*`：该技能需要特定工具才能运行。当列出的工具/工具集不可用时**隐藏**。用于依赖特定功能的技能（例如仅在有终端访问时才有意义的技能）。
- 如果同时指定两者，则两个条件都必须满足才能显示技能。
- 如果都未指定，技能始终显示（向后兼容）。

**示例：**

```yaml
# DuckDuckGo 搜索——当 Firecrawl（web 工具集）不可用时显示
metadata:
  hermes:
    fallback_for_toolsets: [web]

# 智能家居技能——仅在终端可用时有用
metadata:
  hermes:
    requires_toolsets: [terminal]

# 本地浏览器降级——当 Browserbase 不可用时显示
metadata:
  hermes:
    fallback_for_toolsets: [browser]
```

过滤在 `agent/prompt_builder.py` 的提示词构建时发生。`build_skills_system_prompt()` 函数从智能体接收可用工具和工具集的集合，并使用 `_skill_should_show()` 来评估每个技能的条件。

### 技能设置元数据

技能可以通过 `required_environment_variables` 前置元数据字段声明安全的加载时设置元数据。缺失的值不会阻止技能被发现；它们在技能实际加载时触发仅限 CLI 的安全提示。

```yaml
required_environment_variables:
  - name: TENOR_API_KEY
    prompt: Tenor API 密钥
    help: 从 https://developers.google.com/tenor 获取密钥
    required_for: 完整功能
```

用户可以跳过设置并继续加载技能。Hermes 仅向模型暴露元数据（`stored_as`、`skipped`、`validated`）——永远不暴露密钥值。

旧版 `prerequisites.env_vars` 仍然受支持，并会被规范化为新的表示形式。

```yaml
prerequisites:
  env_vars: [TENOR_API_KEY]       # required_environment_variables 的旧版别名
  commands: [curl, jq]            # 参考性 CLI 检查
```

网关和消息会话永远不会在带内收集密钥；它们指示用户在本地运行 `hermes setup` 或更新 `~/.hermes/.env`。

**何时声明必需的环境变量：**
- 技能使用应在加载时安全收集的 API 密钥或令牌
- 技能在用户跳过设置时仍然可用，但可能优雅降级

**何时声明命令前置条件：**
- 技能依赖可能未安装的 CLI 工具（例如 `himalaya`、`openhue`、`ddgs`）
- 将命令检查视为指导，而非发现时的隐藏条件

参见 `skills/gifs/gif-search/` 和 `skills/email/himalaya/` 中的示例。

### 技能指南

- **除非绝对必要，不要引入外部依赖。** 优先使用标准库 Python、curl 和现有 Hermes 工具（`web_extract`、`terminal`、`read_file`）。
- **渐进式披露。** 将最常用的工作流放在最前面。边缘情况和高级用法放在底部。
- **包含辅助脚本**用于 XML/JSON 解析或复杂逻辑——不要期望 LLM 每次都内联编写解析器。
- **测试它。** 运行 `hermes --toolsets skills -q "Use the X skill to do Y"` 并验证智能体正确地遵循指令。

---

## 添加皮肤 / 主题

Hermes 使用数据驱动的皮肤系统——添加新皮肤无需修改代码。

**方案 A：用户皮肤（YAML 文件）**

创建 `~/.hermes/skins/<name>.yaml`：

```yaml
name: mytheme
description: 主题的简短描述

colors:
  banner_border: "#HEX"     # 面板边框颜色
  banner_title: "#HEX"      # 面板标题颜色
  banner_accent: "#HEX"     # 章节标题颜色
  banner_dim: "#HEX"        # 暗淡/弱化文字颜色
  banner_text: "#HEX"       # 正文颜色
  response_border: "#HEX"   # 响应框边框

spinner:
  waiting_faces: ["(⚔)", "(⛨)"]
  thinking_faces: ["(⚔)", "(⌁)"]
  thinking_verbs: ["forging", "plotting"]
  wings:                     # 可选的左右装饰
    - ["⟪⚔", "⚔⟫"]

branding:
  agent_name: "My Agent"
  welcome: "欢迎信息"
  response_label: " ⚔ Agent "
  prompt_symbol: "⚔ ❯ "

tool_prefix: "╎"             # 工具输出行前缀
```

所有字段都是可选的——缺失的值从默认皮肤继承。

**方案 B：内置皮肤**

添加到 `hermes_cli/skin_engine.py` 中的 `_BUILTIN_SKINS` 字典。使用与上面相同的模式但以 Python 字典形式。内置皮肤随软件包一起分发，始终可用。

**激活方法：**
- CLI：`/skin mytheme` 或在 config.yaml 中设置 `display.skin: mytheme`
- 配置：`display: { skin: mytheme }`

参见 `hermes_cli/skin_engine.py` 了解完整模式和现有皮肤示例。

---

## 跨平台兼容性

Hermes 在 Linux、macOS 和 Windows 上运行。编写涉及操作系统的代码时：

### 关键规则

1. **`termios` 和 `fcntl` 仅限 Unix。** 始终同时捕获 `ImportError` 和 `NotImplementedError`：
   ```python
   try:
       from simple_term_menu import TerminalMenu
       menu = TerminalMenu(options)
       idx = menu.show()
   except (ImportError, NotImplementedError):
       # 降级方案：为 Windows 提供数字菜单
       for i, opt in enumerate(options):
           print(f"  {i+1}. {opt}")
       idx = int(input("Choice: ")) - 1
   ```

2. **文件编码。** Windows 可能以 `cp1252` 编码保存 `.env` 文件。始终处理编码错误：
   ```python
   try:
       load_dotenv(env_path)
   except UnicodeDecodeError:
       load_dotenv(env_path, encoding="latin-1")
   ```

3. **进程管理。** `os.setsid()`、`os.killpg()` 和信号处理在 Windows 上有所不同。使用平台检查：
   ```python
   import platform
   if platform.system() != "Windows":
       kwargs["preexec_fn"] = os.setsid
   ```

4. **路径分隔符。** 使用 `pathlib.Path` 而非用 `/` 进行字符串拼接。

5. **安装脚本中的 Shell 命令。** 如果你修改了 `scripts/install.sh`，检查 `scripts/install.ps1` 中是否需要等效更改。

---

## 安全注意事项

Hermes 具有终端访问权限。安全性至关重要。

### 现有保护措施

| 层级 | 实现 |
|-------|---------------|
| **Sudo 密码传递** | 使用 `shlex.quote()` 防止 Shell 注入 |
| **危险命令检测** | `tools/approval.py` 中的正则表达式模式，带有用户审批流程 |
| **Cron 提示注入** | `tools/cronjob_tools.py` 中的扫描器阻止指令覆盖模式 |
| **写入拒绝列表** | 受保护路径（`~/.ssh/authorized_keys`、`/etc/shadow`）通过 `os.path.realpath()` 解析以防止符号链接绕过 |
| **技能防护** | 从技能中心安装的技能的安全扫描器（`tools/skills_guard.py`） |
| **代码执行沙盒** | `execute_code` 子进程在 API 密钥已从环境中移除的情况下运行 |
| **容器加固** | Docker：移除所有能力、禁止权限提升、PID 限制、大小受限的 tmpfs |

### 贡献涉及安全的代码时

- **始终使用 `shlex.quote()`** 将用户输入插入 Shell 命令时
- **使用 `os.path.realpath()` 解析符号链接**，在基于路径的访问控制检查之前
- **不要记录密钥。** API 密钥、令牌和密码永远不应出现在日志输出中
- **在工具执行周围捕获宽泛的异常**，以防单个失败导致智能体循环崩溃
- **在所有平台上测试**，如果你的更改涉及文件路径、进程管理或 Shell 命令

如果你的 PR 影响安全性，请在描述中明确说明。

---

## Pull Request 流程

### 分支命名

```
fix/description        # Bug 修复
feat/description       # 新功能
docs/description       # 文档
test/description       # 测试
refactor/description   # 代码重构
```

### 提交前

1. **运行测试**：`pytest tests/ -v`
2. **手动测试**：运行 `hermes` 并验证你修改的代码路径
3. **检查跨平台影响**：如果涉及文件 I/O、进程管理或终端处理，考虑 Windows 和 macOS
4. **保持 PR 聚焦**：每个 PR 一个逻辑变更。不要将 bug 修复、重构和新功能混在一起。

### PR 描述

包含：
- 改了**什么**以及**为什么**
- **如何测试**（bug 的复现步骤、功能的使用示例）
- 在**什么平台**上测试过
- 引用相关的 issue

### 提交消息

我们使用[约定式提交](https://www.conventionalcommits.org/)：

```
<type>(<scope>): <description>
```

| 类型 | 用于 |
|------|---------|
| `fix` | Bug 修复 |
| `feat` | 新功能 |
| `docs` | 文档 |
| `test` | 测试 |
| `refactor` | 代码重构（无行为变更） |
| `chore` | 构建、CI、依赖更新 |

作用域：`cli`、`gateway`、`tools`、`skills`、`agent`、`install`、`whatsapp`、`security` 等。

示例：
```
fix(cli): prevent crash in save_config_value when model is a string
feat(gateway): add WhatsApp multi-user session isolation
fix(security): prevent shell injection in sudo password piping
test(tools): add unit tests for file_operations
```

---

## 报告问题

- 使用 [GitHub Issues](https://github.com/NousResearch/hermes-agent/issues)
- 包含：操作系统、Python 版本、Hermes 版本（`hermes version`）、完整错误追踪
- 包含复现步骤
- 创建前检查是否已有相同的 issue
- 安全漏洞请私密报告

---

## 社区

- **Discord**：[discord.gg/NousResearch](https://discord.gg/NousResearch) — 用于提问、展示项目和分享技能
- **GitHub Discussions**：用于设计提案和架构讨论
- **技能中心**：将专业技能上传到注册表并与社区分享

---

## 许可证

通过贡献，你同意你的贡献将在 [MIT 许可证](LICENSE) 下许可。
