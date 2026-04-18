# YC-Bench：长时段代理基准测试

[YC-Bench](https://github.com/collinear-ai/yc-bench) 由 [Collinear AI](https://collinear.ai/) 开发，是一个确定性长时段基准测试，测试 LLM 代理作为科技初创公司 CEO 的能力。代理管理一家模拟公司 1-3 年，在 4 个技能领域做出关于资源分配、现金流、任务管理和声望专业化的复合决策。

与 TerminalBench2（通过二元通过/失败评估每个任务的编码能力）不同，YC-Bench 衡量的是**长期战略一致性**——代理是否能保持一致的策略、管理复合后果，并在数百个轮次中调整计划。

## 设置

```bash
# 安装 yc-bench（可选依赖）
pip install "hermes-agent[yc-bench]"

# 或从源码安装
git clone https://github.com/collinear-ai/yc-bench
cd yc-bench && pip install -e .

# 验证
yc-bench --help
```

## 运行

```bash
# 从仓库根目录：
bash environments/benchmarks/yc_bench/run_eval.sh

# 或直接运行：
python environments/benchmarks/yc_bench/yc_bench_env.py evaluate \
    --config environments/benchmarks/yc_bench/default.yaml

# 覆盖模型：
bash environments/benchmarks/yc_bench/run_eval.sh \
    --openai.model_name anthropic/claude-opus-4-20250514

# 快速单预设测试：
bash environments/benchmarks/yc_bench/run_eval.sh \
    --env.presets '["fast_test"]' --env.seeds '[1]'
```

## 工作原理

### 架构

```
HermesAgentLoop（我们的代理）
  -> terminal 工具 -> subprocess("yc-bench company status") -> JSON 输出
  -> terminal 工具 -> subprocess("yc-bench task accept --task-id X") -> JSON
  -> terminal 工具 -> subprocess("yc-bench sim resume") -> JSON（推进时间）
  -> ...（每次运行 100-500 轮）
```

环境通过 `yc-bench sim init` 初始化模拟（不是 `yc-bench run`，后者会启动 yc-bench 自己内置的代理循环）。然后我们的 `HermesAgentLoop` 通过 CLI 命令驱动所有交互。

### 模拟机制

- **4 个技能领域**：research、inference、data_environment、training
- **声望系统**（1.0-10.0）：门控高薪任务的访问权限
- **员工管理**：初级/中级/高级，具有特定领域的技能速率
- **吞吐量分割**：`effective_rate = base_rate / N`（每个员工的活跃任务数）
- **财务压力**：月度工资，破产 = 游戏结束
- **确定性**：基于 SHA256 的 RNG——相同的种子 + 预设 = 相同的世界

### 难度预设

| 预设 | 员工数 | 任务数 | 重点 |
|------|--------|--------|------|
| tutorial | 3 | 50 | 基本循环机制 |
| easy | 5 | 100 | 吞吐量意识 |
| **medium** | 5 | 150 | 声望攀升 + 领域专业化 |
| **hard** | 7 | 200 | 精确 ETA 推理 |
| nightmare | 8 | 300 | 工资压力下的持续完美 |
| fast_test | （不同） | （不同） | 快速验证（约 50 轮） |

默认评估运行 **fast_test + medium + hard** x 3 个种子 = 9 次运行。

### 评分

```
composite = 0.5 x survival + 0.5 x normalised_funds
```

- **存活**（二元）：公司是否避免了破产？
- **标准化资金**（0.0-1.0）：相对于初始 $250K 资本的对数尺度

## 配置

`default.yaml` 中的关键字段：

| 字段 | 默认值 | 描述 |
|------|--------|------|
| `presets` | `["fast_test", "medium", "hard"]` | 评估哪些预设 |
| `seeds` | `[1, 2, 3]` | 每个预设的 RNG 种子 |
| `max_agent_turns` | 200 | 每次运行的最大 LLM 调用次数 |
| `run_timeout` | 3600 | 每次运行的墙钟超时（秒） |
| `survival_weight` | 0.5 | 存活在综合分数中的权重 |
| `funds_weight` | 0.5 | 标准化资金在综合分数中的权重 |
| `horizon_years` | null | 覆盖时间跨度（null = 从预设自动获取） |

## 成本与时间估算

每次运行 100-500 个 LLM 轮次。按典型 API 费率的每次运行近似成本：

| 预设 | 轮次 | 时间 | 估算成本 |
|------|------|------|----------|
| fast_test | ~50 | 5-10 分钟 | $1-5 |
| medium | ~200 | 20-40 分钟 | $5-15 |
| hard | ~300 | 30-60 分钟 | $10-25 |

完整默认评估（9 次运行）：约 3-6 小时，$50-200，取决于模型。

## 参考资料

- [collinear-ai/yc-bench](https://github.com/collinear-ai/yc-bench) — 官方仓库
- [Collinear AI](https://collinear.ai/) — yc-bench 背后的公司
- [TerminalBench2](../terminalbench_2/) — 按任务的编码基准测试（互补）
