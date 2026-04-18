# Hermes Agent - 开发指南

面向 AI 编码助手和 hermes-agent 代码库开发者的指南。

## 开发环境

```bash
source venv/bin/activate  # 运行 Python 前务必先激活虚拟环境
```

## 项目结构

```
hermes-agent/
├── run_agent.py          # AIAgent 类——核心对话循环
├── model_tools.py        # 工具编排，discover_builtin_tools()，handle_function_call()
├── toolsets.py           # 工具集定义，_HERMES_CORE_TOOLS 列表
├── cli.py                # HermesCLI 类——交互式 CLI 编排器
├── hermes_state.py       # SessionDB——SQLite 会话存储（FTS5 搜索）
├── agent/                # 智能体内部模块
│   ├── prompt_builder.py     # 系统提示词组装
│   ├── context_compressor.py # 自动上下文压缩
│   ├── prompt_caching.py     # Anthropic 提示缓存
│   ├── auxiliary_client.py   # 辅助 LLM 客户端（视觉、摘要）
│   ├── model_metadata.py     # 模型上下文长度、token 估算
│   ├── models_dev.py         # models.dev 注册表集成（提供商感知上下文）
│   ├── display.py            # KawaiiSpinner、工具预览格式化
│   ├── skill_commands.py     # 技能斜杠命令（CLI/网关共享）
│   └── trajectory.py         # 轨迹保存辅助工具
├── hermes_cli/           # CLI 子命令和设置
│   ├── main.py           # 入口点——所有 `hermes` 子命令
│   ├── config.py         # DEFAULT_CONFIG、OPTIONAL_ENV_VARS、迁移
│   ├── commands.py       # 斜杠命令定义 + SlashCommandCompleter
│   ├── callbacks.py      # 终端回调（澄清、sudo、审批）
│   ├── setup.py          # 交互式设置向导
│   ├── skin_engine.py    # 皮肤/主题引擎——CLI 视觉自定义
│   ├── skills_config.py  # `hermes skills`——按平台启用/禁用技能
│   ├── tools_config.py   # `hermes tools`——按平台启用/禁用工具
│   ├── skills_hub.py     # `/skills` 斜杠命令（搜索、浏览、安装）
│   ├── models.py         # 模型目录、提供商模型列表
│   ├── model_switch.py   # 共享的 /model 切换流程（CLI + 网关）
│   └── auth.py           # 提供商凭据解析
├── tools/                # 工具实现（每个工具一个文件）
│   ├── registry.py       # 中央工具注册表（模式、处理器、调度）
│   ├── approval.py       # 危险命令检测
│   ├── terminal_tool.py  # 终端编排
│   ├── process_registry.py # 后台进程管理
│   ├── file_tools.py     # 文件读写/搜索/补丁
│   ├── web_tools.py      # 网络搜索/提取（Parallel + Firecrawl）
│   ├── browser_tool.py   # Browserbase 浏览器自动化
│   ├── code_execution_tool.py # execute_code 沙盒
│   ├── delegate_tool.py  # 子智能体委派
│   ├── mcp_tool.py       # MCP 客户端（约 1050 行）
│   └── environments/     # 终端后端（local、docker、ssh、modal、daytona、singularity）
├── gateway/              # 消息平台网关
│   ├── run.py            # 主循环、斜杠命令、消息分发
│   ├── session.py        # SessionStore——对话持久化
│   └── platforms/        # 适配器：telegram、discord、slack、whatsapp、homeassistant、signal、qqbot
├── acp_adapter/          # ACP 服务器（VS Code / Zed / JetBrains 集成）
├── cron/                 # 调度器（jobs.py、scheduler.py）
├── environments/         # 强化学习训练环境（Atropos）
├── tests/                # Pytest 测试套件（约 3000 个测试）
└── batch_runner.py       # 并行批处理
```

**用户配置：** `~/.hermes/config.yaml`（设置），`~/.hermes/.env`（API 密钥）

## 文件依赖链

```
tools/registry.py  （无依赖——被所有工具文件导入）
       ↑
tools/*.py  （每个文件在导入时调用 registry.register()）
       ↑
model_tools.py  （导入 tools/registry + 触发工具发现）
       ↑
run_agent.py、cli.py、batch_runner.py、environments/
```

---

## AIAgent 类 (run_agent.py)

```python
class AIAgent:
    def __init__(self,
        model: str = "anthropic/claude-opus-4.6",
        max_iterations: int = 90,
        enabled_toolsets: list = None,
        disabled_toolsets: list = None,
        quiet_mode: bool = False,
        save_trajectories: bool = False,
        platform: str = None,           # "cli"、"telegram" 等
        session_id: str = None,
        skip_context_files: bool = False,
        skip_memory: bool = False,
        # ... 加上 provider、api_mode、callbacks、routing 参数
    ): ...

    def chat(self, message: str) -> str:
        """简单接口——返回最终响应字符串。"""

    def run_conversation(self, user_message: str, system_message: str = None,
                         conversation_history: list = None, task_id: str = None) -> dict:
        """完整接口——返回包含 final_response + messages 的字典。"""
```

### 智能体循环

核心循环在 `run_conversation()` 内部——完全同步：

```python
while api_call_count < self.max_iterations and self.iteration_budget.remaining > 0:
    response = client.chat.completions.create(model=model, messages=messages, tools=tool_schemas)
    if response.tool_calls:
        for tool_call in response.tool_calls:
            result = handle_function_call(tool_call.name, tool_call.args, task_id)
            messages.append(tool_result_message(result))
        api_call_count += 1
    else:
        return response.content
```

消息遵循 OpenAI 格式：`{"role": "system/user/assistant/tool", ...}`。推理内容存储在 `assistant_msg["reasoning"]` 中。

---

## CLI 架构 (cli.py)

- **Rich** 用于横幅/面板，**prompt_toolkit** 用于带自动补全的输入
- **KawaiiSpinner**（`agent/display.py`）——API 调用期间的动画表情，`┊` 工具结果的活动提要
- `cli.py` 中的 `load_cli_config()` 合并硬编码默认值 + 用户配置 YAML
- **皮肤引擎**（`hermes_cli/skin_engine.py`）——数据驱动的 CLI 主题化；启动时从 `display.skin` 配置键初始化；皮肤自定义横幅颜色、加载动画表情/动词/翅膀、工具前缀、响应框、品牌文字
- `process_command()` 是 `HermesCLI` 的方法——通过中央注册表的 `resolve_command()` 解析规范命令名后进行分发
- 技能斜杠命令：`agent/skill_commands.py` 扫描 `~/.hermes/skills/`，作为**用户消息**注入（而非系统提示词）以保留提示缓存

### 斜杠命令注册表 (`hermes_cli/commands.py`)

所有斜杠命令在 `COMMAND_REGISTRY` 的 `CommandDef` 对象列表中定义。所有下游使用者自动从此注册表派生：

- **CLI** — `process_command()` 通过 `resolve_command()` 解析别名，按规范名分发
- **网关** — `GATEWAY_KNOWN_COMMANDS` frozenset 用于钩子触发，`resolve_command()` 用于分发
- **网关帮助** — `gateway_help_lines()` 生成 `/help` 输出
- **Telegram** — `telegram_bot_commands()` 生成 BotCommand 菜单
- **Slack** — `slack_subcommand_map()` 生成 `/hermes` 子命令路由
- **自动补全** — `COMMANDS` 扁平字典供给 `SlashCommandCompleter`
- **CLI 帮助** — `COMMANDS_BY_CATEGORY` 字典供给 `show_help()`

### 添加斜杠命令

1. 在 `hermes_cli/commands.py` 的 `COMMAND_REGISTRY` 中添加 `CommandDef` 条目：
```python
CommandDef("mycommand", "描述它的功能", "Session",
           aliases=("mc",), args_hint="[arg]"),
```
2. 在 `cli.py` 的 `HermesCLI.process_command()` 中添加处理器：
```python
elif canonical == "mycommand":
    self._handle_mycommand(cmd_original)
```
3. 如果该命令在网关中也可用，在 `gateway/run.py` 中添加处理器：
```python
if canonical == "mycommand":
    return await self._handle_mycommand(event)
```
4. 对于持久化设置，使用 `cli.py` 中的 `save_config_value()`

**CommandDef 字段：**
- `name` — 不带斜杠的规范名称（例如 `"background"`）
- `description` — 人类可读的描述
- `category` — `"Session"`、`"Configuration"`、`"Tools & Skills"`、`"Info"`、`"Exit"` 之一
- `aliases` — 替代名称元组（例如 `("bg",)`）
- `args_hint` — 帮助中显示的参数占位符（例如 `"<prompt>"`、`"[name]"`）
- `cli_only` — 仅在交互式 CLI 中可用
- `gateway_only` — 仅在消息平台中可用
- `gateway_config_gate` — 配置点路径（例如 `"display.tool_progress_command"`）；设置在 `cli_only` 命令上时，如果配置值为真值，该命令将在网关中可用。`GATEWAY_KNOWN_COMMANDS` 始终包含配置门控命令，以便网关可以分发它们；帮助/菜单仅在门控打开时显示。

**添加别名**只需在现有 `CommandDef` 的 `aliases` 元组中添加。无需其他文件更改——分发、帮助文本、Telegram 菜单、Slack 映射和自动补全全部自动更新。

---

## 添加新工具

需要在 **2 个文件**中修改：

**1. 创建 `tools/your_tool.py`：**
```python
import json, os
from tools.registry import registry

def check_requirements() -> bool:
    return bool(os.getenv("EXAMPLE_API_KEY"))

def example_tool(param: str, task_id: str = None) -> str:
    return json.dumps({"success": True, "data": "..."})

registry.register(
    name="example_tool",
    toolset="example",
    schema={"name": "example_tool", "description": "...", "parameters": {...}},
    handler=lambda args, **kw: example_tool(param=args.get("param", ""), task_id=kw.get("task_id")),
    check_fn=check_requirements,
    requires_env=["EXAMPLE_API_KEY"],
)
```

**2. 添加到 `toolsets.py`** —— 要么加入 `_HERMES_CORE_TOOLS`（所有平台），要么创建新的工具集。

自动发现：任何包含顶层 `registry.register()` 调用的 `tools/*.py` 文件会被自动导入——无需维护手动导入列表。

注册表处理模式收集、分发、可用性检查和错误封装。所有处理器**必须**返回 JSON 字符串。

**工具模式中的路径引用**：如果模式描述中提到文件路径（例如默认输出目录），使用 `display_hermes_home()` 使其支持配置文件感知。模式在导入时生成，这是在 `_apply_profile_override()` 设置 `HERMES_HOME` 之后。

**状态文件**：如果工具存储持久状态（缓存、日志、检查点），使用 `get_hermes_home()` 作为基础目录——永远不要使用 `Path.home() / ".hermes"`。这确保每个配置文件获得自己的状态。

**智能体级工具**（todo、memory）：在 `handle_function_call()` 之前被 `run_agent.py` 拦截。参见 `todo_tool.py` 了解模式。

---

## 添加配置

### config.yaml 选项：
1. 添加到 `hermes_cli/config.py` 中的 `DEFAULT_CONFIG`
2. 增加 `_config_version`（当前为 5）以触发现有用户的迁移

### .env 变量：
1. 使用元数据添加到 `hermes_cli/config.py` 中的 `OPTIONAL_ENV_VARS`：
```python
"NEW_API_KEY": {
    "description": "用途说明",
    "prompt": "显示名称",
    "url": "https://...",
    "password": True,
    "category": "tool",  # provider、tool、messaging、setting
},
```

### 配置加载器（两个独立系统）：

| 加载器 | 使用方 | 位置 |
|--------|---------|----------|
| `load_cli_config()` | CLI 模式 | `cli.py` |
| `load_config()` | `hermes tools`、`hermes setup` | `hermes_cli/config.py` |
| 直接 YAML 加载 | 网关 | `gateway/run.py` |

---

## 皮肤/主题系统

皮肤引擎（`hermes_cli/skin_engine.py`）提供数据驱动的 CLI 视觉自定义。皮肤是**纯数据**——添加新皮肤无需修改代码。

### 架构

```
hermes_cli/skin_engine.py    # SkinConfig 数据类、内置皮肤、YAML 加载器
~/.hermes/skins/*.yaml       # 用户安装的自定义皮肤（直接放入即可）
```

- `init_skin_from_config()` — 在 CLI 启动时调用，从配置中读取 `display.skin`
- `get_active_skin()` — 返回当前皮肤的缓存 `SkinConfig`
- `set_active_skin(name)` — 运行时切换皮肤（由 `/skin` 命令使用）
- `load_skin(name)` — 先从用户皮肤加载，然后内置皮肤，最后降级到默认
- 缺失的皮肤值自动从 `default` 皮肤继承

### 皮肤可自定义的内容

| 元素 | 皮肤键 | 使用方 |
|---------|----------|---------|
| 横幅面板边框 | `colors.banner_border` | `banner.py` |
| 横幅面板标题 | `colors.banner_title` | `banner.py` |
| 横幅章节标题 | `colors.banner_accent` | `banner.py` |
| 横幅暗淡文字 | `colors.banner_dim` | `banner.py` |
| 横幅正文 | `colors.banner_text` | `banner.py` |
| 响应框边框 | `colors.response_border` | `cli.py` |
| 等待时的加载动画表情 | `spinner.waiting_faces` | `display.py` |
| 思考时的加载动画表情 | `spinner.thinking_faces` | `display.py` |
| 加载动画动词 | `spinner.thinking_verbs` | `display.py` |
| 加载动画翅膀（可选） | `spinner.wings` | `display.py` |
| 工具输出前缀 | `tool_prefix` | `display.py` |
| 每工具表情符号 | `tool_emojis` | `display.py` → `get_tool_emoji()` |
| 智能体名称 | `branding.agent_name` | `banner.py`、`cli.py` |
| 欢迎消息 | `branding.welcome` | `cli.py` |
| 响应框标签 | `branding.response_label` | `cli.py` |
| 提示符号 | `branding.prompt_symbol` | `cli.py` |

### 内置皮肤

- `default` — 经典 Hermes 金色/可爱风格（当前外观）
- `ares` — 深红色/青铜色战神主题，带自定义加载动画翅膀
- `mono` — 简洁的灰度单色
- `slate` — 冷蓝色开发者主题

### 添加内置皮肤

添加到 `hermes_cli/skin_engine.py` 中的 `_BUILTIN_SKINS` 字典：

```python
"mytheme": {
    "name": "mytheme",
    "description": "简短描述",
    "colors": { ... },
    "spinner": { ... },
    "branding": { ... },
    "tool_prefix": "┊",
},
```

### 用户皮肤（YAML）

用户创建 `~/.hermes/skins/<name>.yaml`：

```yaml
name: cyberpunk
description: 霓虹风格终端主题

colors:
  banner_border: "#FF00FF"
  banner_title: "#00FFFF"
  banner_accent: "#FF1493"

spinner:
  thinking_verbs: ["jacking in", "decrypting", "uploading"]
  wings:
    - ["⟨⚡", "⚡⟩"]

branding:
  agent_name: "Cyber Agent"
  response_label: " ⚡ Cyber "

tool_prefix: "▏"
```

使用 `/skin cyberpunk` 或在 config.yaml 中设置 `display.skin: cyberpunk` 激活。

---

## 重要策略
### 提示缓存不可破坏

Hermes-Agent 确保缓存在整个对话过程中保持有效。**不要实现以下会导致缓存失效的更改：**
- 在对话中途更改过去的上下文
- 在对话中途更改工具集
- 在对话中途重新加载记忆或重建系统提示词

缓存失效会导致显著增加的成本。我们唯一更改上下文的时机是在上下文压缩期间。

### 工作目录行为
- **CLI**：使用当前目录（`.` → `os.getcwd()`）
- **消息平台**：使用 `MESSAGING_CWD` 环境变量（默认：主目录）

### 后台进程通知（网关）

当使用 `terminal(background=true, notify_on_complete=true)` 时，网关运行一个监视器来检测进程完成并触发新的智能体回合。通过 config.yaml 中的 `display.background_process_notifications`（或 `HERMES_BACKGROUND_NOTIFICATIONS` 环境变量）控制后台进程消息的详细程度：

- `all` — 运行中的输出更新 + 最终消息（默认）
- `result` — 仅最终完成消息
- `error` — 仅在退出码 != 0 时的最终消息
- `off` — 完全不显示监视器消息

---

## 配置文件：多实例支持

Hermes 支持**配置文件**——多个完全隔离的实例，每个都有自己的 `HERMES_HOME` 目录（配置、API 密钥、记忆、会话、技能、网关等）。

核心机制：`hermes_cli/main.py` 中的 `_apply_profile_override()` 在任何模块导入之前设置 `HERMES_HOME`。所有 119+ 处对 `get_hermes_home()` 的引用自动限定在活动配置文件范围内。

### 配置文件安全代码规则

1. **所有 HERMES_HOME 路径使用 `get_hermes_home()`。** 从 `hermes_constants` 导入。
   永远不要在读写状态的代码中硬编码 `~/.hermes` 或 `Path.home() / ".hermes"`。
   ```python
   # 正确
   from hermes_constants import get_hermes_home
   config_path = get_hermes_home() / "config.yaml"

   # 错误——破坏配置文件功能
   config_path = Path.home() / ".hermes" / "config.yaml"
   ```

2. **面向用户的消息使用 `display_hermes_home()`。** 从 `hermes_constants` 导入。
   默认返回 `~/.hermes`，配置文件返回 `~/.hermes/profiles/<name>`。
   ```python
   # 正确
   from hermes_constants import display_hermes_home
   print(f"配置已保存到 {display_hermes_home()}/config.yaml")

   # 错误——配置文件下显示错误路径
   print("配置已保存到 ~/.hermes/config.yaml")
   ```

3. **模块级常量没问题** — 它们在导入时缓存 `get_hermes_home()`，
   这是在 `_apply_profile_override()` 设置环境变量之后。只需使用 `get_hermes_home()`，
   而非 `Path.home() / ".hermes"`。

4. **模拟 `Path.home()` 的测试还必须设置 `HERMES_HOME`** — 因为代码现在使用
   `get_hermes_home()`（读取环境变量），而非 `Path.home() / ".hermes"`：
   ```python
   with patch.object(Path, "home", return_value=tmp_path), \
        patch.dict(os.environ, {"HERMES_HOME": str(tmp_path / ".hermes")}):
       ...
   ```

5. **网关平台适配器应使用令牌锁** — 如果适配器使用唯一凭据（bot token、API 密钥）连接，
   在 `connect()`/`start()` 方法中调用 `gateway.status` 的 `acquire_scoped_lock()`，
   在 `disconnect()`/`stop()` 中调用 `release_scoped_lock()`。这可以防止两个配置文件使用同一凭据。
   参见 `gateway/platforms/telegram.py` 了解规范模式。

6. **配置文件操作以 HOME 为锚点，而非 HERMES_HOME** — `_get_profiles_root()`
   返回 `Path.home() / ".hermes" / "profiles"`，而非 `get_hermes_home() / "profiles"`。
   这是有意为之——它允许 `hermes -p coder profile list` 看到所有配置文件，
   无论当前激活的是哪个。

## 已知陷阱

### 不要硬编码 `~/.hermes` 路径
代码路径使用 `hermes_constants` 中的 `get_hermes_home()`。面向用户的打印/日志消息使用 `display_hermes_home()`。硬编码 `~/.hermes` 会破坏配置文件功能——每个配置文件有自己的 `HERMES_HOME` 目录。这是 PR #3575 中修复的 5 个 bug 的根源。

### 不要使用 `simple_term_menu` 做交互式菜单
在 tmux/iTerm2 中存在渲染 bug——滚动时出现残影。改用 `curses`（标准库）。参见 `hermes_cli/tools_config.py` 了解模式。

### 不要在加载动画/显示代码中使用 `\033[K`（ANSI 擦除到行尾）
在 `prompt_toolkit` 的 `patch_stdout` 下会泄漏为文字 `?[K`。使用空格填充：`f"\r{line}{' ' * pad}"`。

### `_last_resolved_tool_names` 是 `model_tools.py` 中的进程全局变量
`delegate_tool.py` 中的 `_run_single_child()` 在子智能体执行前后保存和恢复此全局变量。如果你添加了读取此全局变量的新代码，请注意在子智能体运行期间它可能暂时过时。

### 不要在模式描述中硬编码跨工具引用
工具模式描述不得按名称提及其他工具集的工具（例如 `browser_navigate` 说"优先使用 web_search"）。这些工具可能不可用（缺少 API 密钥、工具集被禁用），导致模型幻觉调用不存在的工具。如果需要跨引用，在 `model_tools.py` 的 `get_tool_definitions()` 中动态添加——参见 `browser_navigate` / `execute_code` 后处理块了解模式。

### 测试不得写入 `~/.hermes/`
`tests/conftest.py` 中的 `_isolate_hermes_home` autouse fixture 将 `HERMES_HOME` 重定向到临时目录。永远不要在测试中硬编码 `~/.hermes/` 路径。

**配置文件测试**：测试配置文件功能时，还需模拟 `Path.home()`，以便
`_get_profiles_root()` 和 `_get_default_hermes_home()` 在临时目录内解析。
使用 `tests/hermes_cli/test_profiles.py` 中的模式：
```python
@pytest.fixture
def profile_env(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(home))
    return home
```

---

## 测试

```bash
source venv/bin/activate
python -m pytest tests/ -q          # 完整套件（约 3000 个测试，约 3 分钟）
python -m pytest tests/test_model_tools.py -q   # 工具集解析
python -m pytest tests/test_cli_init.py -q       # CLI 配置加载
python -m pytest tests/gateway/ -q               # 网关测试
python -m pytest tests/tools/ -q                 # 工具级测试
```

推送更改前务必运行完整测试套件。
