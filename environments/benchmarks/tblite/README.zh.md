# OpenThoughts-TBLite 评估环境

本环境在 [OpenThoughts-TBLite](https://huggingface.co/datasets/open-thoughts/OpenThoughts-TBLite) 基准上评估终端代理，这是 [Terminal-Bench 2.0](https://www.tbench.ai/leaderboard/terminal-bench/2.0) 的一个难度校准子集。

## 来源

OpenThoughts-TBLite 由 [OpenThoughts](https://www.openthoughts.ai/) Agent 团队与 [Snorkel AI](https://snorkel.ai/) 和 [Bespoke Labs](https://bespokelabs.ai/) 合作创建。原始数据集和文档位于：

- **数据集（源）：**[open-thoughts/OpenThoughts-TBLite](https://huggingface.co/datasets/open-thoughts/OpenThoughts-TBLite)
- **GitHub：**[open-thoughts/OpenThoughts-TBLite](https://github.com/open-thoughts/OpenThoughts-TBLite)
- **博客文章：**[openthoughts.ai/blog/openthoughts-tblite](https://www.openthoughts.ai/blog/openthoughts-tblite)

## 我们的数据集

我们将源数据转换为 Terminal-Bench 2.0 环境使用的相同架构（预构建的 Docker Hub 镜像、base64 编码的测试压缩包等），并发布为：

- **数据集（我们的）：**[NousResearch/openthoughts-tblite](https://huggingface.co/datasets/NousResearch/openthoughts-tblite)
- **Docker 镜像：**Docker Hub 上的 `nousresearch/tblite-<task-name>:latest`（100 个镜像）

转换脚本位于 `scripts/prepare_tblite_dataset.py`。

## 为什么选择 TBLite？

Terminal-Bench 2.0 是终端代理最强的前沿评估之一，但当模型得分接近下限（例如 Qwen 3 8B 低于 1%）时，许多改变在总分中看起来是相同的。TBLite 通过使用 Claude Haiku 4.5 作为参考来校准任务难度，解决了这个问题：

| 难度 | 通过率范围 | 任务数 |
|------|-----------|--------|
| 简单 | >= 70% | 40 |
| 中等 | 40-69% | 26 |
| 困难 | 10-39% | 26 |
| 极难 | < 10% | 8 |

这提供了足够多的可解任务来快速检测微小改进，同时保留了足够多的困难任务以避免饱和。TBLite 与 TB2 分数之间的相关性为 **r = 0.911**。

TBLite 的运行速度也比完整 TB2 快 2.6-8 倍，使其适合迭代循环。

## 使用方法

```bash
# 运行完整基准测试
python environments/benchmarks/tblite/tblite_env.py evaluate

# 筛选特定任务
python environments/benchmarks/tblite/tblite_env.py evaluate \
    --env.task_filter "broken-python,pandas-etl"

# 使用不同模型
python environments/benchmarks/tblite/tblite_env.py evaluate \
    --server.model_name "qwen/qwen3-30b"
```

## 架构

`TBLiteEvalEnv` 是 `TerminalBench2EvalEnv` 的轻量子类。所有评估逻辑（代理循环、Docker 沙箱管理、测试验证、指标）都是继承的。仅默认值不同：

| 设置 | TB2 | TBLite |
|------|-----|--------|
| 数据集 | `NousResearch/terminal-bench-2` | `NousResearch/openthoughts-tblite` |
| 任务数 | 89 | 100 |
| 任务超时 | 1800s（30 分钟） | 1200s（20 分钟） |
| Wandb 名称 | `terminal-bench-2` | `openthoughts-tblite` |

## 引用

```bibtex
@software{OpenThoughts-TBLite,
  author = {OpenThoughts-Agent team, Snorkel AI, Bespoke Labs},
  month = Feb,
  title = {{OpenThoughts-TBLite: A High-Signal Benchmark for Iterating on Terminal Agents}},
  howpublished = {https://www.openthoughts.ai/blog/openthoughts-tblite},
  year = {2026}
}
```
