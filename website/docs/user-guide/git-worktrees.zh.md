---
sidebar_position: 3
sidebar_label: "Git Worktrees"
title: "Git Worktrees"
description: "使用 git worktree 和隔离检出在同一仓库上安全运行多个 Hermes 智能体"
---

# Git Worktrees

Hermes Agent 经常用于大型、长期维护的仓库。当你想要：

- 在同一项目上**并行运行多个智能体**，或
- 将实验性重构与主分支隔离，

Git **worktree** 是为每个智能体提供自己的检出而不复制整个仓库的最安全方式。

本页展示如何将 worktree 与 Hermes 结合使用，使每个会话都有一个干净、隔离的工作目录。

## 为什么在 Hermes 中使用 Worktree？

Hermes 将**当前工作目录**作为项目根目录：

- CLI：你运行 `hermes` 或 `hermes chat` 的目录
- 消息网关：由 `MESSAGING_CWD` 设置的目录

如果你在**同一检出**中运行多个智能体，它们的变更可能相互干扰：

- 一个智能体可能删除或重写另一个正在使用的文件。
- 更难理解哪些变更属于哪个实验。

使用 worktree，每个智能体获得：

- 自己的**分支和工作目录**
- 自己的 **Checkpoint Manager 历史**用于 `/rollback`

另见：[检查点与 /rollback](./checkpoints-and-rollback.md)。

## 快速开始：创建 Worktree

从你的主仓库（包含 `.git/`），为功能分支创建新的 worktree：

```bash
# 从主仓库根目录
cd /path/to/your/repo

# 创建新分支和 worktree 在 ../repo-feature
git worktree add ../repo-feature feature/hermes-experiment
```

这创建了：

- 一个新目录：`../repo-feature`
- 一个新分支：`feature/hermes-experiment` 在该目录中检出

现在你可以 `cd` 进入新 worktree 并在那里运行 Hermes：

```bash
cd ../repo-feature

# 在 worktree 中启动 Hermes
hermes
```

Hermes 将：

- 将 `../repo-feature` 视为项目根目录。
- 使用该目录进行上下文文件、代码编辑和工具操作。
- 使用作用域到此 worktree 的**独立检查点历史**用于 `/rollback`。

## 并行运行多个智能体

你可以创建多个 worktree，每个有自己的分支：

```bash
cd /path/to/your/repo

git worktree add ../repo-experiment-a feature/hermes-a
git worktree add ../repo-experiment-b feature/hermes-b
```

在不同终端中：

```bash
# 终端 1
cd ../repo-experiment-a
hermes

# 终端 2
cd ../repo-experiment-b
hermes
```

每个 Hermes 进程：

- 在自己的分支上工作（`feature/hermes-a` vs `feature/hermes-b`）。
- 在不同的影子仓库哈希下写入检查点（来源于 worktree 路径）。
- 可以独立使用 `/rollback` 而不影响另一个。

这在以下情况特别有用：

- 运行批量重构。
- 对同一任务尝试不同方法。
- 配对 CLI + 网关会话针对同一上游仓库。

## 安全清理 Worktree

当你完成实验后：

1. 决定是否保留或丢弃工作。
2. 如果你想保留：
   - 像平常一样将分支合并到主分支。
3. 删除 worktree：

```bash
cd /path/to/your/repo

# 删除 worktree 目录及其引用
git worktree remove ../repo-feature
```

注意：

- `git worktree remove` 会拒绝删除有未提交更改的 worktree，除非你强制执行。
- 删除 worktree **不会**自动删除分支；你可以使用正常的 `git branch` 命令删除或保留分支。
- `~/.hermes/checkpoints/` 下的 Hermes 检查点数据在你删除 worktree 时不会自动清理，但通常非常小。

## 最佳实践

- **每个 Hermes 实验一个 worktree**
  - 为每个重大变更创建专用分支/worktree。
  - 这使差异集中，PR 小而易于审查。
- **以实验命名分支**
  - 例如 `feature/hermes-checkpoints-docs`、`feature/hermes-refactor-tests`。
- **频繁提交**
  - 使用 git 提交标记高级里程碑。
  - 使用[检查点和 /rollback](./checkpoints-and-rollback.md)作为工具驱动编辑之间的安全网。
- **使用 worktree 时避免从裸仓库根目录运行 Hermes**
  - 优先使用 worktree 目录，使每个智能体有明确的作用域。

## 使用 `hermes -w`（自动 Worktree 模式）

Hermes 有一个内置的 `-w` 标志，可以**自动创建一个临时 git worktree**及其自己的分支。你不需要手动设置 worktree——只需 `cd` 到你的仓库并运行：

```bash
cd /path/to/your/repo
hermes -w
```

Hermes 将：

- 在仓库内的 `.worktrees/` 下创建临时 worktree。
- 检出隔离分支（例如 `hermes/hermes-<hash>`）。
- 在该 worktree 内运行完整的 CLI 会话。

这是获得 worktree 隔离的最简单方式。你也可以将其与单次查询结合：

```bash
hermes -w -q "Fix issue #123"
```

要并行运行智能体，打开多个终端并在每个中运行 `hermes -w`——每次调用都自动获得自己的 worktree 和分支。

## 综合使用

- 使用 **git worktree** 为每个 Hermes 会话提供自己的干净检出。
- 使用**分支**捕获实验的高级历史。
- 使用**检查点 + `/rollback`**从每个 worktree 内的错误中恢复。

这种组合为你提供：

- 强有力的保证，不同智能体和实验不会相互干扰。
- 快速的迭代周期，轻松从糟糕的编辑中恢复。
- 干净、可审查的 Pull Request。
