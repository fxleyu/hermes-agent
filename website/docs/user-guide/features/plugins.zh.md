---
sidebar_position: 11
sidebar_label: "插件"
title: "插件"
description: "通过插件系统使用自定义工具、钩子和集成来扩展 Hermes"
---

# 插件

Hermes 有一个插件系统，用于添加自定义工具、钩子和集成，无需修改核心代码。

**-> [构建 Hermes 插件](/docs/guides/build-a-hermes-plugin)** ——包含完整工作示例的分步指南。

## 快速概览

将一个目录放入 `~/.hermes/plugins/`，包含 `plugin.yaml` 和 Python 代码：

```
~/.hermes/plugins/my-plugin/
├── plugin.yaml      # 清单
├── __init__.py      # register() ——将模式连接到处理器
├── schemas.py       # 工具模式（LLM 看到的内容）
└── tools.py         # 工具处理器（调用时运行的代码）
```

启动 Hermes——你的工具与内置工具一起出现。模型可以立即调用它们。

### 最小工作示例

这是一个完整的插件，添加了 `hello_world` 工具并通过钩子记录每个工具调用。

**`~/.hermes/plugins/hello-world/plugin.yaml`**

```yaml
name: hello-world
version: "1.0"
description: A minimal example plugin
```

**`~/.hermes/plugins/hello-world/__init__.py`**

```python
"""最小 Hermes 插件——注册一个工具和一个钩子。"""


def register(ctx):
    # --- 工具：hello_world ---
    schema = {
        "name": "hello_world",
        "description": "Returns a friendly greeting for the given name.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Name to greet",
                }
            },
            "required": ["name"],
        },
    }

    def handle_hello(params):
        name = params.get("name", "World")
        return f"Hello, {name}! (from the hello-world plugin)"

    ctx.register_tool("hello_world", schema, handle_hello)

    # --- 钩子：记录每个工具调用 ---
    def on_tool_call(tool_name, params, result):
        print(f"[hello-world] tool called: {tool_name}")

    ctx.register_hook("post_tool_call", on_tool_call)
```

将两个文件放入 `~/.hermes/plugins/hello-world/`，重启 Hermes，模型可以立即调用 `hello_world`。钩子在每次工具调用后打印一行日志。

`./.hermes/plugins/` 下的项目本地插件默认禁用。通过在启动 Hermes 前设置 `HERMES_ENABLE_PROJECT_PLUGINS=true` 仅为受信任的仓库启用它们。

## 插件能做什么

| 功能 | 方式 |
|------|------|
| 添加工具 | `ctx.register_tool(name, schema, handler)` |
| 添加钩子 | `ctx.register_hook("post_tool_call", callback)` |
| 添加斜杠命令 | `ctx.register_command(name, handler, description)` ——在 CLI 和网关会话中添加 `/name` |
| 添加 CLI 命令 | `ctx.register_cli_command(name, help, setup_fn, handler_fn)` ——添加 `hermes <plugin> <subcommand>` |
| 注入消息 | `ctx.inject_message(content, role="user")` ——参见[注入消息](#注入消息) |
| 附带数据文件 | `Path(__file__).parent / "data" / "file.yaml"` |
| 捆绑技能 | `ctx.register_skill(name, path)` ——以 `plugin:skill` 命名空间，通过 `skill_view("plugin:skill")` 加载 |
| 依赖环境变量 | plugin.yaml 中的 `requires_env: [API_KEY]`——在 `hermes plugins install` 期间提示 |
| 通过 pip 分发 | `[project.entry-points."hermes_agent.plugins"]` |

## 插件发现

| 来源 | 路径 | 使用场景 |
|------|------|---------|
| 用户 | `~/.hermes/plugins/` | 个人插件 |
| 项目 | `.hermes/plugins/` | 项目特定插件（需要 `HERMES_ENABLE_PROJECT_PLUGINS=true`） |
| pip | `hermes_agent.plugins` entry_points | 分发包 |

## 可用钩子

插件可以为这些生命周期事件注册回调。参见 **[事件钩子页面](/docs/user-guide/features/hooks#plugin-hooks)** 获取完整详情、回调签名和示例。

| 钩子 | 触发时机 |
|------|---------|
| [`pre_tool_call`](/docs/user-guide/features/hooks#pre_tool_call) | 任何工具执行之前 |
| [`post_tool_call`](/docs/user-guide/features/hooks#post_tool_call) | 任何工具返回之后 |
| [`pre_llm_call`](/docs/user-guide/features/hooks#pre_llm_call) | 每轮一次，在 LLM 循环之前——可以返回 `{"context": "..."}` 来[将上下文注入用户消息](/docs/user-guide/features/hooks#pre_llm_call) |
| [`post_llm_call`](/docs/user-guide/features/hooks#post_llm_call) | 每轮一次，在 LLM 循环之后（仅成功的轮次） |
| [`on_session_start`](/docs/user-guide/features/hooks#on_session_start) | 新会话创建（仅第一轮） |
| [`on_session_end`](/docs/user-guide/features/hooks#on_session_end) | 每次 `run_conversation` 调用结束 + CLI 退出处理器 |

## 插件类型

Hermes 有三种插件：

| 类型 | 功能 | 选择方式 | 位置 |
|------|------|---------|------|
| **通用插件** | 添加工具、钩子、斜杠命令、CLI 命令 | 多选（启用/禁用） | `~/.hermes/plugins/` |
| **记忆提供商** | 替换或增强内置记忆 | 单选（一个激活） | `plugins/memory/` |
| **上下文引擎** | 替换内置上下文压缩器 | 单选（一个激活） | `plugins/context_engine/` |

记忆提供商和上下文引擎是**提供商插件**——每种类型一次只能有一个激活。通用插件可以任意组合启用。

## 管理插件

```bash
hermes plugins                  # 统一交互式 UI
hermes plugins list             # 带启用/禁用状态的表格视图
hermes plugins install user/repo  # 从 Git 安装
hermes plugins update my-plugin   # 拉取最新版本
hermes plugins remove my-plugin   # 卸载
hermes plugins enable my-plugin   # 重新启用已禁用的插件
hermes plugins disable my-plugin  # 不删除而禁用
```

### 交互式 UI

不带参数运行 `hermes plugins` 会打开一个复合交互界面：

```
Plugins
  ↑↓ navigate  SPACE toggle  ENTER configure/confirm  ESC done

  General Plugins
 → [✓] my-tool-plugin — Custom search tool
   [ ] webhook-notifier — Event hooks

  Provider Plugins
     Memory Provider          ▸ honcho
     Context Engine           ▸ compressor
```

- **通用插件部分** ——复选框，用空格键切换
- **提供商插件部分** ——显示当前选择。按 Enter 进入单选选择器，选择一个激活的提供商。

提供商插件选择保存到 `config.yaml`：

```yaml
memory:
  provider: "honcho"      # 空字符串 = 仅内置

context:
  engine: "compressor"    # 默认内置压缩器
```

### 禁用通用插件

已禁用的插件保持安装但在加载期间被跳过。禁用列表存储在 `config.yaml` 的 `plugins.disabled` 下：

```yaml
plugins:
  disabled:
    - my-noisy-plugin
```

在运行的会话中，`/plugins` 显示当前加载了哪些插件。

## 注入消息

插件可以使用 `ctx.inject_message()` 将消息注入活跃对话：

```python
ctx.inject_message("New data arrived from the webhook", role="user")
```

**签名：**`ctx.inject_message(content: str, role: str = "user") -> bool`

工作原理：

- 如果代理**空闲**（等待用户输入），消息被排队作为下一个输入并开始新一轮。
- 如果代理**正在运行**（主动执行中），消息会中断当前操作——与用户输入新消息并按 Enter 相同。
- 对于非 `"user"` 角色，内容以 `[role]` 为前缀（例如 `[system] ...`）。
- 如果消息成功排队返回 `True`，如果没有可用的 CLI 引用（例如在网关模式下）返回 `False`。

这使插件如远程控制查看器、消息桥接或 Webhook 接收器能够从外部源向对话输入消息。

:::note
`inject_message` 仅在 CLI 模式下可用。在网关模式下，没有 CLI 引用，方法返回 `False`。
:::

参见**[完整指南](/docs/guides/build-a-hermes-plugin)** 了解处理器契约、模式格式、钩子行为、错误处理和常见错误。
