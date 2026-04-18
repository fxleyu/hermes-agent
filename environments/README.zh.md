# Hermes-Agent Atropos 环境

本目录包含 **hermes-agent** 的工具调用能力与 **Atropos** RL 训练框架之间的集成层。它提供了运行代理式 LLM 进行多轮工具调用循环、使用任意奖励函数评分输出，以及将结果馈入 Atropos 进行训练或评估所需的一切。

## 架构概览

```
                        Atropos 框架
                    ┌───────────────────────┐
                    │       BaseEnv          │  (atroposlib)
                    │  - 服务器管理          │
                    │  - 工作器调度          │
                    │  - Wandb 日志          │
                    │  - CLI (serve/process/ │
                    │    evaluate)           │
                    └───────────┬───────────┘
                                │ 继承
                    ┌───────────┴───────────┐
                    │  HermesAgentBaseEnv    │  hermes_base_env.py
                    │  - 终端后端            │
                    │  - 工具解析            │
                    │  - 代理循环            │
                    │  - ToolContext          │
                    │  - 异步补丁            │
                    └───────────┬───────────┘
                                │ 继承
              ┌─────────────────┼─────────────────┐
              │                 │                  │
     TerminalTestEnv     HermesSweEnv    TerminalBench2EvalEnv
     （栈测试）          （SWE 训练）    （TB2 基准评估）
```

### 继承链

**BaseEnv**（来自 `atroposlib`）是 Atropos 基类。它提供：
- 服务器管理（兼容 OpenAI 的 API 服务器、VLLM、SGLang）
- 并行 rollout 的工作器调度
- Wandb 集成，用于指标和 rollout 日志
- CLI 接口，包含三个子命令：`serve`、`process`、`evaluate`
- `evaluate_log()` 用于将评估结果保存为 JSON + samples.jsonl

**HermesAgentBaseEnv**（`hermes_base_env.py`）用 hermes-agent 特定功能扩展了 BaseEnv：
- 设置 `os.environ["TERMINAL_ENV"]` 以配置终端后端（local、docker、modal、daytona、ssh、singularity）
- 通过 `_resolve_tools_for_group()` 解析 hermes-agent 工具集（调用 `get_tool_definitions()` 查询 `tools/registry.py`）
- 实现 `collect_trajectory()` 运行完整的代理循环并计算奖励
- 支持两阶段操作（阶段 1：OpenAI 服务器，阶段 2：VLLM ManagedServer）
- 在导入时应用猴子补丁以实现异步安全的工具操作

具体环境继承自 `HermesAgentBaseEnv` 并实现：
- `setup()` -- 加载数据集，初始化状态
- `get_next_item()` -- 返回下一个用于 rollout 的项目
- `format_prompt()` -- 将数据集项目转换为用户消息
- `compute_reward()` -- 使用 ToolContext 为 rollout 评分
- `evaluate()` -- 周期性评估逻辑

## 核心组件

### 代理循环（`agent_loop.py`）

`HermesAgentLoop` 是可复用的多轮代理引擎。它运行与 hermes-agent 的 `run_agent.py` 相同的模式：

1. 通过 `server.chat_completion()` 将消息 + 工具发送到 API
2. 如果响应包含 `tool_calls`，通过 `handle_function_call()` 执行每个调用（委托给 `tools/registry.py` 的 `dispatch()`）
3. 将工具结果追加到对话中并返回步骤 1
4. 如果响应没有 tool_calls，代理完成

工具调用在线程池（`run_in_executor`）中执行，这样内部使用 `asyncio.run()` 的后端（Modal、Docker）不会在 Atropos 的事件循环中死锁。

返回一个 `AgentResult`，包含完整的对话历史、轮次计数、每轮的推理内容、工具错误，以及可选的 ManagedServer 状态（用于阶段 2）。

### 工具上下文（`tool_context.py`）

`ToolContext` 是每个 rollout 的句柄，它为奖励/验证函数提供对 **所有** hermes-agent 工具的直接访问，作用域为 rollout 的 `task_id`。相同的 `task_id` 意味着终端/浏览器会话与模型在 rollout 期间使用的是同一个——所有状态（文件、进程、浏览器标签）都被保留。

```python
async def compute_reward(self, item, result, ctx: ToolContext):
    # 在模型的终端沙箱中运行测试
    test = ctx.terminal("pytest -v")
    if test["exit_code"] == 0:
        return 1.0

    # 检查文件是否已创建
    content = ctx.read_file("/workspace/solution.py")
    if content.get("content"):
        return 0.5

    # 将文件下载到本地进行验证（二进制安全）
    ctx.download_file("/remote/output.bin", "/local/output.bin")

    return 0.0
```

可用方法：
- **终端**：`terminal(command, timeout)` -- 运行 shell 命令
- **文件**：`read_file(path)`、`write_file(path, content)`、`search(query, path)`
- **传输**：`upload_file()`、`upload_dir()`、`download_file()`、`download_dir()` -- 主机与沙箱之间的二进制安全文件传输
- **网络**：`web_search(query)`、`web_extract(urls)`
- **浏览器**：`browser_navigate(url)`、`browser_snapshot()`
- **通用**：`call_tool(name, args)` -- 按名称调用任何 hermes-agent 工具
- **清理**：`cleanup()` -- 释放所有资源（在 `compute_reward` 后自动调用）

### 补丁（`patches.py`）

**问题**：某些 hermes-agent 工具内部使用 `asyncio.run()`（例如 Modal 后端）。当从 Atropos 的事件循环内部调用时会崩溃，因为 `asyncio.run()` 不能嵌套。

**解决方案**：`ModalEnvironment` 使用专用的 `_AsyncWorker` 后台线程和自己的事件循环。调用代码看到的是同步接口，但内部所有异步 Modal SDK 调用都在工作线程上执行，不会与 Atropos 的循环冲突。这直接内置于 `tools/environments/modal.py` 中——不需要猴子补丁。

`patches.py` 现在是空操作（为了向后兼容导入而保留）。

### 工具调用解析器（`tool_call_parsers/`）

客户端解析器，从原始模型输出文本中提取结构化的 `tool_calls`。在 **阶段 2**（VLLM 服务器类型）中使用，此时 ManagedServer 的 `/generate` 端点返回原始文本，不进行工具调用解析。

每个解析器都是对应 VLLM 解析器的 `extract_tool_calls()` 逻辑的独立重新实现。不依赖 VLLM——仅使用标准库（`re`、`json`、`uuid`）和 `openai` 类型。

可用的解析器：
- `hermes` -- Hermes/ChatML `<tool_call>` XML 格式
- `mistral` -- Mistral `[TOOL_CALLS]` 格式
- `llama3_json` -- Llama 3 JSON 工具调用
- `qwen` -- Qwen 工具调用格式
- `qwen3_coder` -- Qwen3 Coder 格式
- `deepseek_v3` -- DeepSeek V3 格式
- `deepseek_v3_1` -- DeepSeek V3.1 格式
- `kimi_k2` -- Kimi K2 格式
- `longcat` -- Longcat 格式
- `glm45` / `glm47` -- GLM 模型格式

使用方式：
```python
from environments.tool_call_parsers import get_parser

parser = get_parser("hermes")
content, tool_calls = parser.parse(raw_model_output)
```

在阶段 1（OpenAI 服务器类型）中，不需要这些解析器——服务器原生处理工具调用解析。

## 两阶段操作

### 阶段 1：OpenAI 服务器（评估 / SFT 数据生成）

使用 `server.chat_completion()` 配合 `tools=` 参数。服务器（VLLM、SGLang、OpenRouter、OpenAI）原生处理工具调用解析。返回带有结构化 `tool_calls` 的 `ChatCompletion` 对象。

- 适用于：评估、SFT 数据生成、测试
- 使用以下子命令运行：`serve`（配合 `run-api`）、`process` 或 `evaluate`
- 为 Atropos 管道创建占位 token

### 阶段 2：VLLM ManagedServer（完整 RL 训练）

使用 ManagedServer 通过 `/generate` 获取精确的 token ID + logprobs。客户端工具调用解析器（来自 `tool_call_parsers/`）从原始输出重建结构化 `tool_calls`。

- 适用于：使用 GRPO/PPO 的完整 RL 训练
- 使用 `serve` 子命令运行
- 真实 token、掩码和 logprobs 流经管道

## 目录结构

```
environments/
├── README.md                     # 本文件
├── __init__.py                   # 包导出
├── hermes_base_env.py            # 抽象基类（HermesAgentBaseEnv）
├── agent_loop.py                 # 多轮代理引擎（HermesAgentLoop）
├── tool_context.py               # 每个 rollout 的工具访问，用于奖励函数
├── patches.py                    # Modal 后端的异步安全补丁
│
├── tool_call_parsers/            # 阶段 2 客户端解析器
│   ├── __init__.py               # 注册表 + 基类
│   ├── hermes_parser.py
│   ├── mistral_parser.py
│   ├── llama_parser.py
│   ├── qwen_parser.py
│   ├── qwen3_coder_parser.py
│   ├── deepseek_v3_parser.py
│   ├── deepseek_v3_1_parser.py
│   ├── kimi_k2_parser.py
│   ├── longcat_parser.py
│   ├── glm45_parser.py
│   └── glm47_parser.py
│
├── terminal_test_env/            # 栈验证环境
│   └── terminal_test_env.py
│
├── hermes_swe_env/               # SWE-bench 风格训练环境
│   └── hermes_swe_env.py
│
└── benchmarks/                   # 评估基准
    ├── terminalbench_2/          # 89 个终端任务，Modal 沙箱
    │   └── terminalbench2_env.py
    ├── tblite/                   # 100 个校准任务（快速 TB2 代理）
    │   └── tblite_env.py
    └── yc_bench/                 # 长时段战略基准
        └── yc_bench_env.py
```

## 具体环境

### TerminalTestEnv（`terminal_test_env/`）

一个自包含环境，带有内联任务（不需要外部数据集），用于端到端验证完整栈。每个任务要求模型在已知路径创建文件，验证器检查内容是否匹配。

```bash
# Serve 模式（需要 run-api）
run-api
python environments/terminal_test_env/terminal_test_env.py serve

# Process 模式（不需要 run-api，保存为 JSONL）
python environments/terminal_test_env/terminal_test_env.py process \
    --env.data_path_to_save_groups terminal_test_output.jsonl
```

### HermesSweEnv（`hermes_swe_env/`）

SWE-bench 风格训练环境。模型获得编码任务，使用终端 + 文件 + 网络工具来解决，奖励函数在同一个 Modal 沙箱中运行测试。

```bash
python environments/hermes_swe_env/hermes_swe_env.py serve \
    --openai.model_name YourModel \
    --env.dataset_name bigcode/humanevalpack \
    --env.terminal_backend modal
```

### TerminalBench2EvalEnv（`benchmarks/terminalbench_2/`）

Terminal-Bench 2.0 基准测试的**仅评估**环境（89 个任务）。每个任务有一个预构建的 Docker Hub 镜像、一条自然语言指令和一个测试套件。代理使用终端 + 文件工具来解决任务，然后测试套件验证正确性。

遵循标准的 Atropos 评估模式（如 GPQA、MMLU 等）：
- 通过 `evaluate` 子命令运行（不需要 `run-api`）
- `setup()` 加载数据集，`evaluate()` 运行所有任务
- `rollout_and_score_eval()` 处理每个任务的代理循环 + 测试验证
- 将验证器输出下载到本地以进行可靠的奖励检查（Harbor 模式）

```bash
# 运行完整基准测试
python environments/benchmarks/terminalbench_2/terminalbench2_env.py evaluate \
    --openai.model_name anthropic/claude-opus-4.6

# 运行任务子集
python environments/benchmarks/terminalbench_2/terminalbench2_env.py evaluate \
    --openai.model_name anthropic/claude-opus-4.6 \
    --env.task_filter fix-git,git-multibranch

# 跳过特定任务
python environments/benchmarks/terminalbench_2/terminalbench2_env.py evaluate \
    --openai.model_name anthropic/claude-opus-4.6 \
    --env.skip_tasks heavy-task,slow-task
```

## 创建新环境

### 训练环境

1. 在 `environments/` 下创建新目录
2. 创建继承自 `HermesAgentBaseEnv` 的环境文件
3. 实现四个抽象方法 + `evaluate()`

```python
from environments.hermes_base_env import HermesAgentBaseEnv, HermesAgentEnvConfig

class MyEnvConfig(HermesAgentEnvConfig):
    pass  # 按需添加自定义字段

class MyEnv(HermesAgentBaseEnv):
    name = "my-env"
    env_config_cls = MyEnvConfig

    @classmethod
    def config_init(cls):
        env_config = MyEnvConfig(
            enabled_toolsets=["terminal", "file"],
            terminal_backend="modal",
            # ... 其他配置
        )
        server_configs = [APIServerConfig(...)]
        return env_config, server_configs

    async def setup(self):
        self.dataset = load_dataset(...)
        self.iter = 0

    async def get_next_item(self):
        item = self.dataset[self.iter % len(self.dataset)]
        self.iter += 1
        return item

    def format_prompt(self, item):
        return item["instruction"]

    async def compute_reward(self, item, result, ctx):
        # ctx 为您提供对 rollout 沙箱的完整工具访问
        test = ctx.terminal("pytest -v")
        return 1.0 if test["exit_code"] == 0 else 0.0

    async def evaluate(self, *args, **kwargs):
        # 周期性评估逻辑
        ...

if __name__ == "__main__":
    MyEnv.cli()
```

### 仅评估环境（基准测试）

对于评估基准，遵循 `terminalbench2_env.py` 中的模式：
1. 在 `environments/benchmarks/your-benchmark/` 下创建
2. 继承自 `HermesAgentBaseEnv`
3. 设置仅评估配置：`eval_handling=STOP_TRAIN`、`steps_per_eval=1`、`total_steps=1`
4. 桩实现训练方法（`collect_trajectories`、`score`）
5. 实现 `rollout_and_score_eval()` 和 `evaluate()`
6. 使用 `evaluate` 子命令运行

## 关键配置字段

| 字段 | 描述 | 默认值 |
|------|------|--------|
| `enabled_toolsets` | 启用哪些 hermes 工具集 | `None`（全部） |
| `disabled_toolsets` | 禁用的工具集 | `None` |
| `distribution` | 概率性工具集分布名称 | `None` |
| `max_agent_turns` | 每个 rollout 的最大 LLM 调用次数 | `30` |
| `agent_temperature` | 采样温度 | `1.0` |
| `terminal_backend` | `local`、`docker`、`modal`、`daytona`、`ssh`、`singularity` | `local` |
| `system_prompt` | 代理的系统消息 | `None` |
| `tool_call_parser` | 阶段 2 的解析器名称 | `hermes` |
| `eval_handling` | `STOP_TRAIN`、`LIMIT_TRAIN`、`NONE` | `STOP_TRAIN` |
