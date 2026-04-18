---
sidebar_position: 12
title: "批量处理"
description: "大规模生成代理轨迹——并行处理、检查点和工具集分布"
---

# 批量处理

批量处理允许你将 Hermes 代理并行运行在数百或数千个提示上，生成结构化的轨迹数据。这主要用于**训练数据生成**——生成包含工具使用统计信息的 ShareGPT 格式轨迹，可用于微调或评估。

## 概述

批量运行器（`batch_runner.py`）处理 JSONL 数据集中的提示，每个提示通过完整的代理会话和工具访问运行。每个提示都有自己的隔离环境。输出是结构化的轨迹数据，包含完整的对话历史、工具调用统计和推理覆盖率指标。

## 快速开始

```bash
# 基本批量运行
python batch_runner.py \
    --dataset_file=data/prompts.jsonl \
    --batch_size=10 \
    --run_name=my_first_run \
    --model=anthropic/claude-sonnet-4.6 \
    --num_workers=4

# 恢复中断的运行
python batch_runner.py \
    --dataset_file=data/prompts.jsonl \
    --batch_size=10 \
    --run_name=my_first_run \
    --resume

# 列出可用的工具集分布
python batch_runner.py --list_distributions
```

## 数据集格式

输入数据集是 JSONL 文件（每行一个 JSON 对象）。每个条目必须有 `prompt` 字段：

```jsonl
{"prompt": "Write a Python function that finds the longest palindromic substring"}
{"prompt": "Create a REST API endpoint for user authentication using Flask"}
{"prompt": "Debug this error: TypeError: cannot unpack non-iterable NoneType object"}
```

条目可以选择性地包含：
- `image` 或 `docker_image`：用于此提示沙箱的容器镜像（适用于 Docker、Modal 和 Singularity 后端）
- `cwd`：任务终端会话的工作目录覆盖

## 配置选项

| 参数 | 默认值 | 描述 |
|------|--------|------|
| `--dataset_file` | (必需) | JSONL 数据集路径 |
| `--batch_size` | (必需) | 每批提示数 |
| `--run_name` | (必需) | 本次运行的名称（用于输出目录和检查点） |
| `--distribution` | `"default"` | 要采样的工具集分布 |
| `--model` | `claude-sonnet-4.6` | 使用的模型 |
| `--base_url` | `https://openrouter.ai/api/v1` | API 基础 URL |
| `--api_key` | (环境变量) | 模型的 API 密钥 |
| `--max_turns` | `10` | 每个提示的最大工具调用迭代次数 |
| `--num_workers` | `4` | 并行工作进程数 |
| `--resume` | `false` | 从检查点恢复 |
| `--verbose` | `false` | 启用详细日志 |
| `--max_samples` | 全部 | 仅处理数据集中的前 N 个样本 |
| `--max_tokens` | 模型默认值 | 每次模型响应的最大 token 数 |

### 提供商路由（OpenRouter）

| 参数 | 描述 |
|------|------|
| `--providers_allowed` | 以逗号分隔的允许提供商（例如 `"anthropic,openai"`） |
| `--providers_ignored` | 以逗号分隔的忽略提供商（例如 `"together,deepinfra"`） |
| `--providers_order` | 以逗号分隔的首选提供商顺序 |
| `--provider_sort` | 按 `"price"`、`"throughput"` 或 `"latency"` 排序 |

### 推理控制

| 参数 | 描述 |
|------|------|
| `--reasoning_effort` | 推理级别：`none`、`minimal`、`low`、`medium`、`high`、`xhigh` |
| `--reasoning_disabled` | 完全禁用推理/思考 token |

### 高级选项

| 参数 | 描述 |
|------|------|
| `--ephemeral_system_prompt` | 执行期间使用但不保存到轨迹中的系统提示 |
| `--log_prefix_chars` | 日志预览中显示的字符数（默认：100） |
| `--prefill_messages_file` | 用于 few-shot 预填充消息的 JSON 文件路径 |

## 工具集分布

每个提示从**分布**中随机采样一组工具集。这确保训练数据覆盖多样化的工具组合。使用 `--list_distributions` 查看所有可用分布。

在当前实现中，分布为**每个单独的工具集**分配概率。采样器独立地翻转每个工具集，然后保证至少启用一个工具集。这与手工编写的预构建组合表不同。

## 输出格式

所有输出保存到 `data/<run_name>/`：

```text
data/my_run/
├── trajectories.jsonl    # 合并后的最终输出（所有批次合并）
├── batch_0.jsonl         # 单个批次结果
├── batch_1.jsonl
├── ...
├── checkpoint.json       # 恢复检查点
└── statistics.json       # 聚合工具使用统计
```

### 轨迹格式

`trajectories.jsonl` 中的每一行是一个 JSON 对象：

```json
{
  "prompt_index": 42,
  "conversations": [
    {"from": "human", "value": "Write a function..."},
    {"from": "gpt", "value": "I'll create that function...",
     "tool_calls": [...]},
    {"from": "tool", "value": "..."},
    {"from": "gpt", "value": "Here's the completed function..."}
  ],
  "metadata": {
    "batch_num": 2,
    "timestamp": "2026-01-15T10:30:00",
    "model": "anthropic/claude-sonnet-4.6"
  },
  "completed": true,
  "partial": false,
  "api_calls": 3,
  "toolsets_used": ["terminal", "file"],
  "tool_stats": {
    "terminal": {"count": 2, "success": 2, "failure": 0},
    "read_file": {"count": 1, "success": 1, "failure": 0}
  },
  "tool_error_counts": {
    "terminal": 0,
    "read_file": 0
  }
}
```

`conversations` 字段使用类 ShareGPT 格式，包含 `from` 和 `value` 字段。工具统计被规范化以包含所有可能工具的零值默认值，确保条目间的一致模式以兼容 HuggingFace 数据集。

## 检查点

批量运行器具有强大的检查点机制以实现容错：

- **检查点文件：**每个批次完成后保存，跟踪哪些提示索引已完成
- **基于内容的恢复：**使用 `--resume` 时，运行器扫描现有批次文件并通过实际文本内容（而不仅仅是索引）匹配已完成的提示，即使数据集顺序更改也能恢复
- **失败的提示：**只有成功完成的提示才被标记为已完成——失败的提示将在恢复时重试
- **批次合并：**完成后，所有批次文件（包括之前运行的）合并为单个 `trajectories.jsonl`

### 恢复工作原理

1. 扫描所有 `batch_*.jsonl` 文件中已完成的提示（按内容匹配）
2. 过滤数据集以排除已完成的提示
3. 重新对剩余提示进行批处理
4. 仅处理剩余提示
5. 将所有批次文件（旧的 + 新的）合并为最终输出

## 质量过滤

批量运行器应用自动质量过滤：

- **无推理过滤器：**零个助手轮次包含推理（没有 `<REASONING_SCRATCHPAD>` 或原生思考 token）的样本将被丢弃
- **损坏条目过滤器：**在最终合并期间，包含虚构工具名称（不在有效工具列表中）的条目将被过滤掉
- **推理统计：**跟踪整个运行中有/无推理的轮次百分比

## 统计

完成后，运行器会打印综合统计信息：

- **工具使用：**每个工具的调用次数、成功/失败率
- **推理覆盖率：**包含推理的助手轮次百分比
- **丢弃的样本：**因缺少推理而被过滤的样本数
- **持续时间：**总处理时间

统计信息也保存到 `statistics.json` 以供编程分析。

## 使用场景

### 训练数据生成

生成多样化的工具使用轨迹用于微调：

```bash
python batch_runner.py \
    --dataset_file=data/coding_prompts.jsonl \
    --batch_size=20 \
    --run_name=coding_v1 \
    --model=anthropic/claude-sonnet-4.6 \
    --num_workers=8 \
    --distribution=default \
    --max_turns=15
```

### 模型评估

评估模型在标准化提示中使用工具的表现：

```bash
python batch_runner.py \
    --dataset_file=data/eval_suite.jsonl \
    --batch_size=10 \
    --run_name=eval_gpt4 \
    --model=openai/gpt-4o \
    --num_workers=4 \
    --max_turns=10
```

### 每提示容器镜像

对于需要特定环境的基准测试，每个提示可以指定自己的容器镜像：

```jsonl
{"prompt": "Install numpy and compute eigenvalues of a 3x3 matrix", "image": "python:3.11-slim"}
{"prompt": "Compile this Rust program and run it", "image": "rust:1.75"}
{"prompt": "Set up a Node.js Express server", "image": "node:20-alpine", "cwd": "/app"}
```

批量运行器在运行每个提示之前会验证 Docker 镜像是否可访问。
