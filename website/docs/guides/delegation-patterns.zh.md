---
sidebar_position: 13
title: "委派与并行工作"
description: "何时以及如何使用子代理委派 — 并行研究、代码审查和多文件工作的模式"
---

# 委派与并行工作

Hermes 可以生成隔离的子代理来并行处理任务。每个子代理都有自己的对话、终端会话和工具集。只有最终摘要会返回 — 中间的工具调用不会进入你的上下文窗口。

完整功能参考请参阅[子代理委派](/docs/user-guide/features/delegation)。

---

## 何时委派

**委派的好候选：**
- 推理密集型子任务（调试、代码审查、研究综合）
- 会用中间数据淹没你上下文的任务
- 并行的独立工作流（同时研究 A 和 B）
- 需要新上下文的任务，希望代理无偏见地处理

**使用其他方式：**
- 单个工具调用 -> 直接使用工具
- 步骤间有逻辑的机械多步骤工作 -> `execute_code`
- 需要用户交互的任务 -> 子代理不能使用 `clarify`
- 快速文件编辑 -> 直接操作

---

## 模式：并行研究

同时研究三个主题并获取结构化摘要：

```
Research these three topics in parallel:
1. Current state of WebAssembly outside the browser
2. RISC-V server chip adoption in 2025
3. Practical quantum computing applications

Focus on recent developments and key players.
```

在幕后，Hermes 使用：

```python
delegate_task(tasks=[
    {
        "goal": "Research WebAssembly outside the browser in 2025",
        "context": "Focus on: runtimes (Wasmtime, Wasmer), cloud/edge use cases, WASI progress",
        "toolsets": ["web"]
    },
    {
        "goal": "Research RISC-V server chip adoption",
        "context": "Focus on: server chips shipping, cloud providers adopting, software ecosystem",
        "toolsets": ["web"]
    },
    {
        "goal": "Research practical quantum computing applications",
        "context": "Focus on: error correction breakthroughs, real-world use cases, key companies",
        "toolsets": ["web"]
    }
])
```

三个任务并发运行。每个子代理独立搜索网络并返回摘要。父代理然后将它们综合为一份连贯的简报。

---

## 模式：代码审查

将安全审查委派给一个全新上下文的子代理，它会不带预设地处理代码：

```
Review the authentication module at src/auth/ for security issues.
Check for SQL injection, JWT validation problems, password handling,
and session management. Fix anything you find and run the tests.
```

关键是 `context` 字段 — 它必须包含子代理需要的一切：

```python
delegate_task(
    goal="Review src/auth/ for security issues and fix any found",
    context="""Project at /home/user/webapp. Python 3.11, Flask, PyJWT, bcrypt.
    Auth files: src/auth/login.py, src/auth/jwt.py, src/auth/middleware.py
    Test command: pytest tests/auth/ -v
    Focus on: SQL injection, JWT validation, password hashing, session management.
    Fix issues found and verify tests pass.""",
    toolsets=["terminal", "file"]
)
```

:::warning 上下文问题
子代理对你的对话**一无所知**。它们完全从零开始。如果你委派"修复我们讨论的那个 bug"，子代理不知道你指的是哪个 bug。始终显式传递文件路径、错误消息、项目结构和约束条件。
:::

---

## 模式：比较替代方案

并行评估同一问题的多种方法，然后选择最好的：

```
I need to add full-text search to our Django app. Evaluate three approaches
in parallel:
1. PostgreSQL tsvector (built-in)
2. Elasticsearch via django-elasticsearch-dsl
3. Meilisearch via meilisearch-python

For each: setup complexity, query capabilities, resource requirements,
and maintenance overhead. Compare them and recommend one.
```

每个子代理独立研究一个选项。因为它们是隔离的，不会交叉污染 — 每个评估都基于自身的优缺点。父代理获得所有三个摘要后进行比较。

---

## 模式：多文件重构

将大型重构任务分配给并行子代理，每个处理代码库的不同部分：

```python
delegate_task(tasks=[
    {
        "goal": "Refactor all API endpoint handlers to use the new response format",
        "context": """Project at /home/user/api-server.
        Files: src/handlers/users.py, src/handlers/auth.py, src/handlers/billing.py
        Old format: return {"data": result, "status": "ok"}
        New format: return APIResponse(data=result, status=200).to_dict()
        Import: from src.responses import APIResponse
        Run tests after: pytest tests/handlers/ -v""",
        "toolsets": ["terminal", "file"]
    },
    {
        "goal": "Update all client SDK methods to handle the new response format",
        "context": """Project at /home/user/api-server.
        Files: sdk/python/client.py, sdk/python/models.py
        Old parsing: result = response.json()["data"]
        New parsing: result = response.json()["data"] (same key, but add status code checking)
        Also update sdk/python/tests/test_client.py""",
        "toolsets": ["terminal", "file"]
    },
    {
        "goal": "Update API documentation to reflect the new response format",
        "context": """Project at /home/user/api-server.
        Docs at: docs/api/. Format: Markdown with code examples.
        Update all response examples from old format to new format.
        Add a 'Response Format' section to docs/api/overview.md explaining the schema.""",
        "toolsets": ["terminal", "file"]
    }
])
```

:::tip
每个子代理都有自己的终端会话。它们可以在同一个项目目录上工作而不互相干扰 — 只要它们编辑不同的文件。如果两个子代理可能涉及同一个文件，在并行工作完成后自己处理那个文件。
:::

---

## 模式：先收集再分析

使用 `execute_code` 进行机械性数据收集，然后委派推理密集型分析：

```python
# 步骤 1：机械性收集（execute_code 在这里更好 — 不需要推理）
execute_code("""
from hermes_tools import web_search, web_extract

results = []
for query in ["AI funding Q1 2026", "AI startup acquisitions 2026", "AI IPOs 2026"]:
    r = web_search(query, limit=5)
    for item in r["data"]["web"]:
        results.append({"title": item["title"], "url": item["url"], "desc": item["description"]})

# 提取最相关的前 5 个的完整内容
urls = [r["url"] for r in results[:5]]
content = web_extract(urls)

# 保存给分析步骤
import json
with open("/tmp/ai-funding-data.json", "w") as f:
    json.dump({"search_results": results, "extracted": content["results"]}, f)
print(f"Collected {len(results)} results, extracted {len(content['results'])} pages")
""")

# 步骤 2：推理密集型分析（委派在这里更好）
delegate_task(
    goal="Analyze AI funding data and write a market report",
    context="""Raw data at /tmp/ai-funding-data.json contains search results and
    extracted web pages about AI funding, acquisitions, and IPOs in Q1 2026.
    Write a structured market report: key deals, trends, notable players,
    and outlook. Focus on deals over $100M.""",
    toolsets=["terminal", "file"]
)
```

这通常是最高效的模式：`execute_code` 以低成本处理 10+ 个顺序工具调用，然后子代理用干净的上下文完成一个昂贵的推理任务。

---

## 工具集选择

根据子代理需要选择工具集：

| 任务类型 | 工具集 | 原因 |
|-----------|----------|-----|
| 网络研究 | `["web"]` | 仅 web_search + web_extract |
| 代码工作 | `["terminal", "file"]` | Shell 访问 + 文件操作 |
| 全栈 | `["terminal", "file", "web"]` | 除消息外的一切 |
| 只读分析 | `["file"]` | 只能读文件，不能执行 shell |

限制工具集使子代理保持专注，防止意外的副作用（如研究子代理运行 shell 命令）。

---

## 约束

- **默认 3 个并行任务** — 批次默认为 3 个并发子代理（可通过 `config.yaml` 中的 `delegation.max_concurrent_children` 配置）
- **不可嵌套** — 子代理不能调用 `delegate_task`、`clarify`、`memory`、`send_message` 或 `execute_code`
- **独立终端** — 每个子代理都有自己的终端会话，带有独立的工作目录和状态
- **无对话历史** — 子代理只看到你在 `goal` 和 `context` 中提供的内容
- **默认 50 次迭代** — 对简单任务设置更低的 `max_iterations` 以节省成本

---

## 技巧

**目标要具体。** "修复 bug"太模糊。"修复 api/handlers.py 第 47 行的 TypeError，process_request() 从 parse_body() 收到 None"给了子代理足够的信息来工作。

**包含文件路径。** 子代理不了解你的项目结构。始终包含相关文件的绝对路径、项目根目录和测试命令。

**使用委派进行上下文隔离。** 有时你需要一个全新的视角。委派迫使你清晰地表述问题，子代理会不带你对话中积累的假设来处理它。

**检查结果。** 子代理摘要就是摘要。如果子代理说"修复了 bug 并且测试通过了"，通过自己运行测试或阅读 diff 来验证。

---

*完整的委派参考 — 所有参数、ACP 集成和高级配置 — 请参阅[子代理委派](/docs/user-guide/features/delegation)。*
