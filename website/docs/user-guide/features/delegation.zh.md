---
sidebar_position: 7
title: "子代理委派"
description: "使用 delegate_task 生成隔离的子代理实例进行并行工作流"
---

# 子代理委派

`delegate_task` 工具生成具有隔离上下文、受限工具集和独立终端会话的子 AIAgent 实例。每个子代理获得一个全新的对话并独立工作 — 只有其最终摘要进入父代理的上下文。

## 单个任务

```python
delegate_task(
    goal="调试测试失败的原因",
    context="Error: assertion in test_foo.py line 42",
    toolsets=["terminal", "file"]
)
```

## 并行批处理

最多 3 个并发子代理：

```python
delegate_task(tasks=[
    {"goal": "研究主题 A", "toolsets": ["web"]},
    {"goal": "研究主题 B", "toolsets": ["web"]},
    {"goal": "修复构建", "toolsets": ["terminal", "file"]}
])
```

## 子代理上下文的工作方式

:::warning 关键：子代理一无所知
子代理以**完全全新的对话**开始。它们对父代理的对话历史、之前的工具调用或委派前讨论的任何内容都没有任何了解。子代理唯一的上下文来自你提供的 `goal` 和 `context` 字段。
:::

这意味着你必须传递子代理需要的**一切**：

```python
# 差 - 子代理不知道"错误"是什么
delegate_task(goal="修复错误")

# 好 - 子代理拥有所需的所有上下文
delegate_task(
    goal="修复 api/handlers.py 中的 TypeError",
    context="""文件 api/handlers.py 在第 47 行有一个 TypeError：
    'NoneType' object has no attribute 'get'.
    函数 process_request() 从 parse_body() 接收 dict，
    但 parse_body() 在 Content-Type 缺失时返回 None。
    项目位于 /home/user/myproject，使用 Python 3.11。"""
)
```

子代理接收一个从你的目标和上下文构建的聚焦系统提示，指示它完成任务并提供结构化摘要，说明做了什么、发现了什么、修改了哪些文件以及遇到了什么问题。

## 实际示例

### 并行研究

同时研究多个主题并收集摘要：

```python
delegate_task(tasks=[
    {
        "goal": "研究 2025 年 WebAssembly 的现状",
        "context": "重点关注：浏览器支持、非浏览器运行时、语言支持",
        "toolsets": ["web"]
    },
    {
        "goal": "研究 2025 年 RISC-V 的采用现状",
        "context": "重点关注：服务器芯片、嵌入式系统、软件生态系统",
        "toolsets": ["web"]
    },
    {
        "goal": "研究 2025 年量子计算的进展",
        "context": "重点关注：纠错突破、实际应用、主要参与者",
        "toolsets": ["web"]
    }
])
```

### 代码审查 + 修复

将审查并修复的工作流委派到全新的上下文：

```python
delegate_task(
    goal="审查认证模块的安全问题并修复发现的任何问题",
    context="""项目位于 /home/user/webapp。
    认证模块文件：src/auth/login.py、src/auth/jwt.py、src/auth/middleware.py。
    项目使用 Flask、PyJWT 和 bcrypt。
    重点关注：SQL 注入、JWT 验证、密码处理、会话管理。
    修复发现的任何问题并运行测试套件（pytest tests/auth/）。""",
    toolsets=["terminal", "file"]
)
```

### 多文件重构

委派一个会淹没父代理上下文的大型重构任务：

```python
delegate_task(
    goal="重构 src/ 中所有 Python 文件，将 print() 替换为正确的日志记录",
    context="""项目位于 /home/user/myproject。
    使用 'logging' 模块，logger = logging.getLogger(__name__)。
    用适当的日志级别替换 print() 调用：
    - print(f"Error: ...") -> logger.error(...)
    - print(f"Warning: ...") -> logger.warning(...)
    - print(f"Debug: ...") -> logger.debug(...)
    - 其他 print -> logger.info(...)
    不要更改测试文件或 CLI 输出中的 print()。
    之后运行 pytest 验证没有破坏任何内容。""",
    toolsets=["terminal", "file"]
)
```

## 批处理模式详情

当你提供 `tasks` 数组时，子代理使用线程池**并行**运行：

- **最大并发度：** 3 个任务（如果超过 3 个，`tasks` 数组会被截断）
- **线程池：** 使用 `ThreadPoolExecutor`，`MAX_CONCURRENT_CHILDREN = 3` 个 worker
- **进度显示：** 在 CLI 模式下，树视图实时显示每个子代理的工具调用并带有每个任务的完成行。在网关模式下，进度被批量处理并传递给父代理的进度回调
- **结果排序：** 结果按任务索引排序以匹配输入顺序，无论完成顺序如何
- **中断传播：** 中断父代理（例如发送新消息）会中断所有活跃的子代理

单任务委派直接运行，没有线程池开销。

## 模型覆盖

你可以通过 `config.yaml` 为子代理配置不同的模型 — 对于将简单任务委派给更便宜/更快的模型很有用：

```yaml
# 在 ~/.hermes/config.yaml 中
delegation:
  model: "google/gemini-flash-2.0"    # 子代理使用更便宜的模型
  provider: "openrouter"              # 可选：将子代理路由到不同的服务商
```

如果省略，子代理使用与父代理相同的模型。

## 工具集选择技巧

`toolsets` 参数控制子代理可以访问哪些工具。根据任务选择：

| 工具集模式 | 用途 |
|----------------|----------|
| `["terminal", "file"]` | 代码工作、调试、文件编辑、构建 |
| `["web"]` | 研究、事实核查、文档查找 |
| `["terminal", "file", "web"]` | 全栈任务（默认） |
| `["file"]` | 只读分析、无执行的代码审查 |
| `["terminal"]` | 系统管理、进程管理 |

无论你指定什么，某些工具集始终对子代理**阻止**：
- `delegation` — 不允许递归委派（防止无限生成）
- `clarify` — 子代理不能与用户交互
- `memory` — 不写入共享的持久记忆
- `code_execution` — 子代理应该逐步推理
- `send_message` — 不产生跨平台副作用（例如发送 Telegram 消息）

## 最大迭代次数

每个子代理有一个迭代限制（默认：50），控制它可以进行多少轮工具调用：

```python
delegate_task(
    goal="快速文件检查",
    context="检查 /etc/nginx/nginx.conf 是否存在并打印前 10 行",
    max_iterations=10  # 简单任务，不需要多轮
)
```

## 深度限制

委派有 **2 级深度限制** — 父代理（深度 0）可以生成子代理（深度 1），但子代理不能进一步委派。这防止了失控的递归委派链。

## 关键属性

- 每个子代理获得自己的**终端会话**（与父代理分开）
- **不能嵌套委派** — 子代理不能进一步委派（没有"孙代理"）
- 子代理**不能**调用：`delegate_task`、`clarify`、`memory`、`send_message`、`execute_code`
- **中断传播** — 中断父代理会中断所有活跃的子代理
- 只有最终摘要进入父代理的上下文，保持 token 使用效率
- 子代理继承父代理的 **API 密钥、服务商配置和凭据池**（使速率限制时的密钥轮换成为可能）

## 委派 vs execute_code

| 因素 | delegate_task | execute_code |
|--------|--------------|-------------|
| **推理** | 完整 LLM 推理循环 | 仅 Python 代码执行 |
| **上下文** | 全新隔离的对话 | 无对话，仅脚本 |
| **工具访问** | 所有非阻止的工具，带推理 | 7 个工具通过 RPC，无推理 |
| **并行** | 最多 3 个并发子代理 | 单个脚本 |
| **最适合** | 需要判断的复杂任务 | 机械化的多步骤流水线 |
| **Token 成本** | 较高（完整 LLM 循环） | 较低（仅返回 stdout） |
| **用户交互** | 无（子代理不能澄清） | 无 |

**经验法则：** 当子任务需要推理、判断或多步骤问题解决时使用 `delegate_task`。当你需要机械化的数据处理或脚本工作流时使用 `execute_code`。

## 配置

```yaml
# 在 ~/.hermes/config.yaml 中
delegation:
  max_iterations: 50                        # 每个子代理的最大轮数（默认：50）
  default_toolsets: ["terminal", "file", "web"]  # 默认工具集
  model: "google/gemini-3-flash-preview"             # 可选的服务商/模型覆盖
  provider: "openrouter"                             # 可选的内置服务商

# 或使用直接自定义端点代替 provider：
delegation:
  model: "qwen2.5-coder"
  base_url: "http://localhost:1234/v1"
  api_key: "local-key"
```

:::tip
代理根据任务复杂性自动处理委派。你不需要明确要求它委派 — 当合理时它会自动执行。
:::
