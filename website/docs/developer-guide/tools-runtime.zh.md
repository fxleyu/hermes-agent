---
sidebar_position: 9
title: "工具运行时"
description: "工具注册表、工具集、调度和终端环境的运行时行为"
---

# 工具运行时

Hermes 工具是按工具集分组的自注册函数，通过中央注册表/调度系统执行。

主要文件：

- `tools/registry.py`
- `model_tools.py`
- `toolsets.py`
- `tools/terminal_tool.py`
- `tools/environments/*`

## 工具注册模型

每个工具模块在导入时调用 `registry.register(...)`。

`model_tools.py` 负责导入/发现工具模块并构建模型使用的 Schema 列表。

### `registry.register()` 的工作原理

`tools/` 中的每个工具文件在模块级别调用 `registry.register()` 来声明自己。函数签名为：

```python
registry.register(
    name="terminal",               # 唯一工具名称（用于 API Schema）
    toolset="terminal",            # 此工具所属的工具集
    schema={...},                  # OpenAI 函数调用 Schema（描述、参数）
    handler=handle_terminal,       # 工具被调用时执行的函数
    check_fn=check_terminal,       # 可选：返回 True/False 表示可用性
    requires_env=["SOME_VAR"],     # 可选：需要的环境变量（供 UI 显示）
    is_async=False,                # 处理函数是否为异步协程
    description="Run commands",    # 人类可读的描述
    emoji="💻",                    # 用于旋转器/进度显示的 emoji
)
```

每次调用创建一个 `ToolEntry`，存储在单例 `ToolRegistry._tools` 字典中，以工具名称为键。如果跨工具集发生名称冲突，会记录警告，后注册的胜出。

### 发现：`discover_builtin_tools()`

当 `model_tools.py` 被导入时，它调用 `tools/registry.py` 中的 `discover_builtin_tools()`。此函数使用 AST 解析扫描每个 `tools/*.py` 文件，找到包含顶层 `registry.register()` 调用的模块，然后导入它们：

```python
# tools/registry.py（简化版）
def discover_builtin_tools(tools_dir=None):
    tools_path = Path(tools_dir) if tools_dir else Path(__file__).parent
    for path in sorted(tools_path.glob("*.py")):
        if path.name in {"__init__.py", "registry.py", "mcp_tool.py"}:
            continue
        if _module_registers_tools(path):  # AST 检查顶层 registry.register()
            importlib.import_module(f"tools.{path.stem}")
```

这种自动发现意味着新的工具文件会被自动识别 — 无需维护手动列表。AST 检查只匹配顶层 `registry.register()` 调用（不匹配函数内部的调用），因此 `tools/` 中的辅助模块不会被导入。

每次导入触发模块的 `registry.register()` 调用。可选工具中的错误（例如缺少 `fal_client` 用于图像生成）被捕获并记录 — 它们不会阻止其他工具加载。

核心工具发现之后，MCP 工具和插件工具也会被发现：

1. **MCP 工具** — `tools.mcp_tool.discover_mcp_tools()` 读取 MCP 服务器配置并从外部服务器注册工具。
2. **插件工具** — `hermes_cli.plugins.discover_plugins()` 加载可能注册额外工具的用户/项目/pip 插件。

## 工具可用性检查（`check_fn`）

每个工具可以可选地提供 `check_fn` — 一个在工具可用时返回 `True`、不可用时返回 `False` 的可调用对象。典型检查包括：

- **API 密钥存在** — 例如 `lambda: bool(os.environ.get("SERP_API_KEY"))` 用于网页搜索
- **服务运行中** — 例如检查 Honcho 服务器是否已配置
- **二进制已安装** — 例如验证 `playwright` 对浏览器工具可用

当 `registry.get_definitions()` 为模型构建 Schema 列表时，它运行每个工具的 `check_fn()`：

```python
# 来自 registry.py 的简化版
if entry.check_fn:
    try:
        available = bool(entry.check_fn())
    except Exception:
        available = False   # 异常 = 不可用
    if not available:
        continue            # 完全跳过此工具
```

关键行为：
- 检查结果**按调用缓存** — 如果多个工具共享相同的 `check_fn`，它只运行一次。
- `check_fn()` 中的异常被视为"不可用"（故障安全）。
- `is_toolset_available()` 方法检查工具集的 `check_fn` 是否通过，用于 UI 显示和工具集解析。

## 工具集解析

工具集是命名的工具包。Hermes 通过以下方式解析它们：

- 显式的启用/禁用工具集列表
- 平台预设（`hermes-cli`、`hermes-telegram` 等）
- 动态 MCP 工具集
- 精选的特殊用途集如 `hermes-acp`

### `get_tool_definitions()` 如何过滤工具

主入口是 `model_tools.get_tool_definitions(enabled_toolsets, disabled_toolsets, quiet_mode)`：

1. **如果提供了 `enabled_toolsets`** — 只包含这些工具集中的工具。每个工具集名称通过 `resolve_toolset()` 解析，它将组合工具集展开为单个工具名称。

2. **如果提供了 `disabled_toolsets`** — 从所有工具集开始，然后减去禁用的。

3. **如果都未提供** — 包含所有已知工具集。

4. **注册表过滤** — 解析后的工具名称集传给 `registry.get_definitions()`，它应用 `check_fn` 过滤并返回 OpenAI 格式的 Schema。

5. **动态 Schema 修补** — 过滤后，`execute_code` 和 `browser_navigate` 的 Schema 被动态调整，只引用实际通过过滤的工具（防止模型幻觉出不可用的工具）。

### 旧版工具集名称

带 `_tools` 后缀的旧工具集名称（例如 `web_tools`、`terminal_tools`）通过 `_LEGACY_TOOLSET_MAP` 映射到现代工具名称，以保持向后兼容。

## 调度

在运行时，工具通过中央注册表调度，但某些 Agent 级工具（如记忆/待办/会话搜索处理）有 Agent 循环级别的例外。

### 调度流程：模型 tool_call -> 处理函数执行

当模型返回 `tool_call` 时，流程为：

```
模型响应带有 tool_call
    ↓
run_agent.py Agent 循环
    ↓
model_tools.handle_function_call(name, args, task_id, user_task)
    ↓
[Agent 循环工具?] → 由 Agent 循环直接处理（todo、memory、session_search、delegate_task）
    ↓
[插件 pre-hook] → invoke_hook("pre_tool_call", ...)
    ↓
registry.dispatch(name, args, **kwargs)
    ↓
按名称查找 ToolEntry
    ↓
[异步处理函数?] → 通过 _run_async() 桥接
[同步处理函数?]  → 直接调用
    ↓
返回结果字符串（或 JSON 错误）
    ↓
[插件 post-hook] → invoke_hook("post_tool_call", ...)
```

### 错误包装

所有工具执行在两个级别包装错误处理：

1. **`registry.dispatch()`** — 捕获处理函数中的任何异常并返回 `{"error": "Tool execution failed: ExceptionType: message"}` 作为 JSON。

2. **`handle_function_call()`** — 将整个调度包装在二级 try/except 中，返回 `{"error": "Error executing tool_name: message"}`。

这确保模型始终收到格式良好的 JSON 字符串，而不是未处理的异常。

### Agent 循环工具

四个工具在注册表调度之前被拦截，因为它们需要 Agent 级状态（TodoStore、MemoryStore 等）：

- `todo` — 计划/任务跟踪
- `memory` — 持久化记忆写入
- `session_search` — 跨会话召回
- `delegate_task` — 生成子 Agent 会话

这些工具的 Schema 仍然在注册表中注册（用于 `get_tool_definitions`），但如果调度以某种方式直接到达它们，它们的处理函数会返回存根错误。

### 异步桥接

当工具处理函数是异步的时，`_run_async()` 将其桥接到同步调度路径：

- **CLI 路径（无运行中的循环）** — 使用持久事件循环以保持缓存的异步客户端存活
- **网关路径（运行中的循环）** — 启动一个一次性线程运行 `asyncio.run()`
- **工作线程（并行工具）** — 使用存储在线程本地存储中的每线程持久循环

## DANGEROUS_PATTERNS 审批流程

终端工具集成了在 `tools/approval.py` 中定义的危险命令审批系统：

1. **模式检测** — `DANGEROUS_PATTERNS` 是一个 `(regex, description)` 元组列表，覆盖破坏性操作：
   - 递归删除（`rm -rf`）
   - 文件系统格式化（`mkfs`、`dd`）
   - SQL 破坏性操作（无 `WHERE` 的 `DROP TABLE`、`DELETE FROM`）
   - 系统配置覆盖（`> /etc/`）
   - 服务操作（`systemctl stop`）
   - 远程代码执行（`curl | sh`）
   - Fork 炸弹、进程杀死等

2. **检测** — 在执行任何终端命令之前，`detect_dangerous_command(command)` 检查所有模式。

3. **审批提示** — 如果找到匹配：
   - **CLI 模式** — 交互式提示要求用户批准、拒绝或永久允许
   - **网关模式** — 异步审批回调将请求发送到消息平台
   - **智能审批** — 可选地，辅助 LLM 可以自动批准匹配模式但低风险的命令（例如 `rm -rf node_modules/` 是安全的但匹配"递归删除"）

4. **会话状态** — 审批按会话跟踪。一旦你为一个会话批准了"递归删除"，后续的 `rm -rf` 命令不会重新提示。

5. **永久允许列表** — "永久允许"选项将模式写入 `config.yaml` 的 `command_allowlist`，跨会话持久化。

## 终端/运行时环境

终端系统支持多种后端：

- local
- docker
- ssh
- singularity
- modal
- daytona

它还支持：

- 每任务 cwd 覆盖
- 后台进程管理
- PTY 模式
- 危险命令的审批回调

## 并发

工具调用可以按顺序或并发执行，取决于工具组合和交互要求。

## 相关文档

- [工具集参考](../reference/toolsets-reference.md)
- [内置工具参考](../reference/tools-reference.md)
- [Agent 循环内部机制](./agent-loop.md)
- [ACP 内部机制](./acp-internals.md)
