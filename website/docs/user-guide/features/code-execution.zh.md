---
sidebar_position: 8
title: "代码执行"
description: "具有 RPC 工具访问的沙盒化 Python 执行 — 将多步骤工作流压缩为单轮"
---

# 代码执行（程序化工具调用）

`execute_code` 工具让代理编写 Python 脚本以程序化方式调用 Hermes 工具，将多步骤工作流压缩为单次 LLM 轮次。脚本在代理主机上的沙盒化子进程中运行，通过 Unix 域套接字 RPC 通信。

## 工作原理

1. 代理编写使用 `from hermes_tools import ...` 的 Python 脚本
2. Hermes 生成一个带有 RPC 函数的 `hermes_tools.py` 存根模块
3. Hermes 打开 Unix 域套接字并启动 RPC 监听线程
4. 脚本在子进程中运行 — 工具调用通过套接字传回 Hermes
5. 只有脚本的 `print()` 输出返回给 LLM；中间工具结果永远不进入上下文窗口

```python
# 代理可以编写这样的脚本：
from hermes_tools import web_search, web_extract

results = web_search("Python 3.13 features", limit=5)
for r in results["data"]["web"]:
    content = web_extract([r["url"]])
    # ... 过滤和处理 ...
print(summary)
```

**沙盒中可用的工具：** `web_search`、`web_extract`、`read_file`、`write_file`、`search_files`、`patch`、`terminal`（仅前台）。

## 代理何时使用

代理在以下情况使用 `execute_code`：

- **3+ 次工具调用**之间有处理逻辑
- 批量数据过滤或条件分支
- 循环处理结果

关键优势：中间工具结果永远不进入上下文窗口 — 只有最终的 `print()` 输出返回，大幅减少 token 使用。

## 实际示例

### 数据处理流水线

```python
from hermes_tools import search_files, read_file
import json

# 查找所有配置文件并提取数据库设置
matches = search_files("database", path=".", file_glob="*.yaml", limit=20)
configs = []
for match in matches.get("matches", []):
    content = read_file(match["path"])
    configs.append({"file": match["path"], "preview": content["content"][:200]})

print(json.dumps(configs, indent=2))
```

### 多步骤网页研究

```python
from hermes_tools import web_search, web_extract
import json

# 在一轮中搜索、提取和摘要
results = web_search("Rust async runtime comparison 2025", limit=5)
summaries = []
for r in results["data"]["web"]:
    page = web_extract([r["url"]])
    for p in page.get("results", []):
        if p.get("content"):
            summaries.append({
                "title": r["title"],
                "url": r["url"],
                "excerpt": p["content"][:500]
            })

print(json.dumps(summaries, indent=2))
```

### 批量文件重构

```python
from hermes_tools import search_files, read_file, patch

# 查找所有使用废弃 API 的 Python 文件并修复
matches = search_files("old_api_call", path="src/", file_glob="*.py")
fixed = 0
for match in matches.get("matches", []):
    result = patch(
        path=match["path"],
        old_string="old_api_call(",
        new_string="new_api_call(",
        replace_all=True
    )
    if "error" not in str(result):
        fixed += 1

print(f"Fixed {fixed} files out of {len(matches.get('matches', []))} matches")
```

### 构建和测试流水线

```python
from hermes_tools import terminal, read_file
import json

# 运行测试、解析结果并报告
result = terminal("cd /project && python -m pytest --tb=short -q 2>&1", timeout=120)
output = result.get("output", "")

# 解析测试输出
passed = output.count(" passed")
failed = output.count(" failed")
errors = output.count(" error")

report = {
    "passed": passed,
    "failed": failed,
    "errors": errors,
    "exit_code": result.get("exit_code", -1),
    "summary": output[-500:] if len(output) > 500 else output
}

print(json.dumps(report, indent=2))
```

## 资源限制

| 资源 | 限制 | 说明 |
|----------|-------|-------|
| **超时** | 5 分钟（300 秒） | 脚本被 SIGTERM 杀死，5 秒宽限后 SIGKILL |
| **Stdout** | 50 KB | 输出截断并附带 `[output truncated at 50KB]` 通知 |
| **Stderr** | 10 KB | 非零退出时包含在输出中用于调试 |
| **工具调用** | 每次执行 50 次 | 达到限制时返回错误 |

所有限制可通过 `config.yaml` 配置：

```yaml
# 在 ~/.hermes/config.yaml 中
code_execution:
  timeout: 300       # 每个脚本的最大秒数（默认：300）
  max_tool_calls: 50 # 每次执行的最大工具调用数（默认：50）
```

## 脚本内的工具调用如何工作

当你的脚本调用类似 `web_search("query")` 的函数时：

1. 调用被序列化为 JSON 并通过 Unix 域套接字发送到父进程
2. 父进程通过标准的 `handle_function_call` 处理器分派
3. 结果通过套接字返回
4. 函数返回解析后的结果

这意味着脚本内的工具调用与正常工具调用行为相同 — 相同的速率限制、相同的错误处理、相同的功能。唯一的限制是 `terminal()` 仅前台（不支持 `background` 或 `pty` 参数）。

## 错误处理

当脚本失败时，代理接收结构化的错误信息：

- **非零退出码**：stderr 包含在输出中，以便代理看到完整的堆栈跟踪
- **超时**：脚本被杀死，代理看到 `"Script timed out after 300s and was killed."`
- **中断**：如果用户在执行期间发送新消息，脚本被终止，代理看到 `[execution interrupted — user sent a new message]`
- **工具调用限制**：达到 50 次限制后，后续工具调用返回错误消息

响应始终包括 `status`（success/error/timeout/interrupted）、`output`、`tool_calls_made` 和 `duration_seconds`。

## 安全

:::danger 安全模型
子进程以**最小环境**运行。API 密钥、token 和凭据默认被剥离。脚本仅通过 RPC 通道访问工具 — 除非明确允许，否则无法从环境变量读取密钥。
:::

名称中包含 `KEY`、`TOKEN`、`SECRET`、`PASSWORD`、`CREDENTIAL`、`PASSWD` 或 `AUTH` 的环境变量被排除。只有安全的系统变量（`PATH`、`HOME`、`LANG`、`SHELL`、`PYTHONPATH`、`VIRTUAL_ENV` 等）被传递。

### 技能环境变量传递

当技能在其前言中声明 `required_environment_variables` 时，这些变量在技能加载后**自动传递**到 `execute_code` 和 `terminal` 沙盒。这让技能使用其声明的 API 密钥而不会削弱任意代码的安全态势。

对于非技能用途，你可以在 `config.yaml` 中显式白名单变量：

```yaml
terminal:
  env_passthrough:
    - MY_CUSTOM_KEY
    - ANOTHER_TOKEN
```

完整详情请参阅[安全指南](/docs/user-guide/security#environment-variable-passthrough)。

脚本在临时目录中运行，执行后会被清理。子进程在自己的进程组中运行，因此可以在超时或中断时被干净地杀死。

## execute_code vs terminal

| 用途 | execute_code | terminal |
|----------|-------------|----------|
| 工具调用之间有多步骤工作流 | OK | 不行 |
| 简单 shell 命令 | 不行 | OK |
| 过滤/处理大量工具输出 | OK | 不行 |
| 运行构建或测试套件 | 不行 | OK |
| 循环搜索结果 | OK | 不行 |
| 交互/后台进程 | 不行 | OK |
| 需要环境中的 API 密钥 | 仅通过[传递](/docs/user-guide/security#environment-variable-passthrough) | OK（大多数会传递） |

**经验法则：** 当你需要以编程方式调用 Hermes 工具并在调用之间有逻辑时使用 `execute_code`。运行 shell 命令、构建和进程时使用 `terminal`。

## 平台支持

代码执行需要 Unix 域套接字，仅在 **Linux 和 macOS** 上可用。在 Windows 上自动禁用 — 代理退回到常规的顺序工具调用。
