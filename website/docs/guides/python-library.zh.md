---
sidebar_position: 5
title: "将 Hermes 作为 Python 库使用"
description: "在你自己的 Python 脚本、Web 应用或自动化管道中嵌入 AIAgent — 无需 CLI"
---

# 将 Hermes 作为 Python 库使用

Hermes 不仅仅是一个 CLI 工具。你可以直接导入 `AIAgent` 并在你自己的 Python 脚本、Web 应用或自动化管道中以编程方式使用它。本指南展示了方法。

---

## 安装

直接从仓库安装 Hermes：

```bash
pip install git+https://github.com/NousResearch/hermes-agent.git
```

或使用 [uv](https://docs.astral.sh/uv/)：

```bash
uv pip install git+https://github.com/NousResearch/hermes-agent.git
```

你也可以在 `requirements.txt` 中固定它：

```text
hermes-agent @ git+https://github.com/NousResearch/hermes-agent.git
```

:::tip
CLI 使用的相同环境变量在将 Hermes 作为库使用时也是必需的。至少设置 `OPENROUTER_API_KEY`（或如果使用直接提供商访问则设置 `OPENAI_API_KEY` / `ANTHROPIC_API_KEY`）。
:::

---

## 基本用法

使用 Hermes 最简单的方式是 `chat()` 方法 — 传入消息，获取字符串返回：

```python
from run_agent import AIAgent

agent = AIAgent(
    model="anthropic/claude-sonnet-4",
    quiet_mode=True,
)
response = agent.chat("What is the capital of France?")
print(response)
```

`chat()` 在内部处理完整的对话循环 — 工具调用、重试等 — 只返回最终的文本回复。

:::warning
在你自己的代码中嵌入 Hermes 时始终设置 `quiet_mode=True`。没有它，代理会打印 CLI 旋转器、进度指示器和其他终端输出，这会弄乱你应用的输出。
:::

---

## 完整对话控制

要获得更多对话控制权，直接使用 `run_conversation()`。它返回一个包含完整回复、消息历史和元数据的字典：

```python
agent = AIAgent(
    model="anthropic/claude-sonnet-4",
    quiet_mode=True,
)

result = agent.run_conversation(
    user_message="Search for recent Python 3.13 features",
    task_id="my-task-1",
)

print(result["final_response"])
print(f"Messages exchanged: {len(result['messages'])}")
```

返回的字典包含：
- **`final_response`** — 代理的最终文本回复
- **`messages`** — 完整的消息历史（系统、用户、助手、工具调用）
- **`task_id`** — 用于 VM 隔离的任务标识符

你也可以传入自定义系统消息来覆盖该调用的临时系统提示：

```python
result = agent.run_conversation(
    user_message="Explain quicksort",
    system_message="You are a computer science tutor. Use simple analogies.",
)
```

---

## 配置工具

使用 `enabled_toolsets` 或 `disabled_toolsets` 控制代理可以访问的工具集：

```python
# 只启用网络工具（浏览、搜索）
agent = AIAgent(
    model="anthropic/claude-sonnet-4",
    enabled_toolsets=["web"],
    quiet_mode=True,
)

# 启用除终端访问外的一切
agent = AIAgent(
    model="anthropic/claude-sonnet-4",
    disabled_toolsets=["terminal"],
    quiet_mode=True,
)
```

:::tip
当你想要一个最小化、锁定的代理时使用 `enabled_toolsets`（例如只有网络搜索的研究机器人）。当你想要大多数能力但需要限制特定功能时使用 `disabled_toolsets`（例如共享环境中不允许终端访问）。
:::

---

## 多轮对话

通过将消息历史传回来维护跨多轮的对话状态：

```python
agent = AIAgent(
    model="anthropic/claude-sonnet-4",
    quiet_mode=True,
)

# 第一轮
result1 = agent.run_conversation("My name is Alice")
history = result1["messages"]

# 第二轮 — 代理记住上下文
result2 = agent.run_conversation(
    "What's my name?",
    conversation_history=history,
)
print(result2["final_response"])  # "Your name is Alice."
```

`conversation_history` 参数接受来自先前结果的 `messages` 列表。代理会在内部复制它，所以你的原始列表不会被修改。

---

## 保存轨迹

启用轨迹保存以 ShareGPT 格式捕获对话 — 用于生成训练数据或调试：

```python
agent = AIAgent(
    model="anthropic/claude-sonnet-4",
    save_trajectories=True,
    quiet_mode=True,
)

agent.chat("Write a Python function to sort a list")
# 以 ShareGPT 格式保存到 trajectory_samples.jsonl
```

每个对话作为单个 JSONL 行追加，便于从自动化运行中收集数据集。

---

## 自定义系统提示

使用 `ephemeral_system_prompt` 设置自定义系统提示，引导代理的行为但**不会**保存到轨迹文件（保持训练数据干净）：

```python
agent = AIAgent(
    model="anthropic/claude-sonnet-4",
    ephemeral_system_prompt="You are a SQL expert. Only answer database questions.",
    quiet_mode=True,
)

response = agent.chat("How do I write a JOIN query?")
print(response)
```

这非常适合构建专用代理 — 代码审查员、文档编写者、SQL 助手 — 都使用相同的底层工具。

---

## 批量处理

要并行运行多个提示，Hermes 包含 `batch_runner.py`。它管理并发的 `AIAgent` 实例，具有适当的资源隔离：

```bash
python batch_runner.py --input prompts.jsonl --output results.jsonl
```

每个提示获得自己的 `task_id` 和隔离环境。如果你需要自定义批处理逻辑，可以直接使用 `AIAgent` 构建：

```python
import concurrent.futures
from run_agent import AIAgent

prompts = [
    "Explain recursion",
    "What is a hash table?",
    "How does garbage collection work?",
]

def process_prompt(prompt):
    # 每个任务创建新的代理以确保线程安全
    agent = AIAgent(
        model="anthropic/claude-sonnet-4",
        quiet_mode=True,
        skip_memory=True,
    )
    return agent.chat(prompt)

with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
    results = list(executor.map(process_prompt, prompts))

for prompt, result in zip(prompts, results):
    print(f"Q: {prompt}\nA: {result}\n")
```

:::warning
始终**每个线程或任务创建一个新的 `AIAgent` 实例**。代理维护内部状态（对话历史、工具会话、迭代计数器），共享跨并发调用不是线程安全的。
:::

---

## 集成示例

### FastAPI 端点

```python
from fastapi import FastAPI
from pydantic import BaseModel
from run_agent import AIAgent

app = FastAPI()

class ChatRequest(BaseModel):
    message: str
    model: str = "anthropic/claude-sonnet-4"

@app.post("/chat")
async def chat(request: ChatRequest):
    agent = AIAgent(
        model=request.model,
        quiet_mode=True,
        skip_context_files=True,
        skip_memory=True,
    )
    response = agent.chat(request.message)
    return {"response": response}
```

### Discord 机器人

```python
import discord
from run_agent import AIAgent

client = discord.Client(intents=discord.Intents.default())

@client.event
async def on_message(message):
    if message.author == client.user:
        return
    if message.content.startswith("!hermes "):
        query = message.content[8:]
        agent = AIAgent(
            model="anthropic/claude-sonnet-4",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
            platform="discord",
        )
        response = agent.chat(query)
        await message.channel.send(response[:2000])

client.run("YOUR_DISCORD_TOKEN")
```

### CI/CD 管道步骤

```python
#!/usr/bin/env python3
"""CI step: auto-review a PR diff."""
import subprocess
from run_agent import AIAgent

diff = subprocess.check_output(["git", "diff", "main...HEAD"]).decode()

agent = AIAgent(
    model="anthropic/claude-sonnet-4",
    quiet_mode=True,
    skip_context_files=True,
    skip_memory=True,
    disabled_toolsets=["terminal", "browser"],
)

review = agent.chat(
    f"Review this PR diff for bugs, security issues, and style problems:\n\n{diff}"
)
print(review)
```

---

## 关键构造函数参数

| 参数 | 类型 | 默认值 | 描述 |
|-----------|------|---------|-------------|
| `model` | `str` | `"anthropic/claude-opus-4.6"` | OpenRouter 格式的模型 |
| `quiet_mode` | `bool` | `False` | 抑制 CLI 输出 |
| `enabled_toolsets` | `List[str]` | `None` | 白名单特定工具集 |
| `disabled_toolsets` | `List[str]` | `None` | 黑名单特定工具集 |
| `save_trajectories` | `bool` | `False` | 保存对话到 JSONL |
| `ephemeral_system_prompt` | `str` | `None` | 自定义系统提示（不保存到轨迹） |
| `max_iterations` | `int` | `90` | 每次对话的最大工具调用迭代次数 |
| `skip_context_files` | `bool` | `False` | 跳过加载 AGENTS.md 文件 |
| `skip_memory` | `bool` | `False` | 禁用持久记忆读写 |
| `api_key` | `str` | `None` | API 密钥（回退到环境变量） |
| `base_url` | `str` | `None` | 自定义 API 端点 URL |
| `platform` | `str` | `None` | 平台提示（`"discord"`、`"telegram"` 等） |

---

## 重要说明

:::tip
- 如果你不想让工作目录中的 `AGENTS.md` 文件加载到系统提示中，设置 **`skip_context_files=True`**。
- 设置 **`skip_memory=True`** 防止代理读写持久记忆 — 推荐用于无状态 API 端点。
- `platform` 参数（例如 `"discord"`、`"telegram"`）注入平台特定的格式提示，让代理适应其输出风格。
:::

:::warning
- **线程安全**：每个线程或任务创建一个 `AIAgent`。不要跨并发调用共享实例。
- **资源清理**：对话结束时代理自动清理资源（终端会话、浏览器实例）。如果你在长时间运行的进程中使用，确保每个对话正常完成。
- **迭代限制**：默认的 `max_iterations=90` 相当宽裕。对于简单的问答场景，考虑降低它（例如 `max_iterations=10`）以防止失控的工具调用循环并控制成本。
:::
