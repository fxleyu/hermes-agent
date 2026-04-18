---
sidebar_position: 8
sidebar_label: "检查点与回滚"
title: "检查点与 /rollback"
description: "使用影子 git 仓库和自动快照为破坏性操作提供文件系统安全网"
---

# 检查点与 `/rollback`

Hermes Agent 在**破坏性操作**之前自动快照你的项目，并允许你通过单条命令恢复。检查点**默认启用**——没有文件变更工具触发时零成本。

这个安全网由内部 **Checkpoint Manager** 驱动，它在 `~/.hermes/checkpoints/` 下保持一个独立的影子 git 仓库——你真正的项目 `.git` 永远不会被触碰。

## 什么触发检查点

检查点在以下操作之前自动创建：

- **文件工具** —— `write_file` 和 `patch`
- **破坏性终端命令** —— `rm`、`mv`、`sed -i`、`truncate`、`shred`、输出重定向（`>`）和 `git reset`/`clean`/`checkout`

智能体**每个目录每轮最多创建一个检查点**，因此长时间会话不会产生大量快照。

## 快速参考

| 命令 | 描述 |
|---------|-------------|
| `/rollback` | 列出所有检查点及变更统计 |
| `/rollback <N>` | 恢复到检查点 N（同时撤销上一轮聊天） |
| `/rollback diff <N>` | 预览检查点 N 与当前状态之间的差异 |
| `/rollback <N> <file>` | 从检查点 N 恢复单个文件 |

## 检查点工作原理

概要：

- Hermes 检测工具即将**修改文件**在你的工作树中。
- 每次对话轮次（每个目录），它：
  - 为文件解析合理的项目根目录。
  - 初始化或重用绑定到该目录的**影子 git 仓库**。
  - 暂存并提交当前状态，附带简短、人类可读的原因。
- 这些提交形成检查点历史，你可以通过 `/rollback` 检查和恢复。

```mermaid
flowchart LR
  user["用户命令\n(hermes, gateway)"]
  agent["AIAgent\n(run_agent.py)"]
  tools["文件与终端工具"]
  cpMgr["CheckpointManager"]
  shadowRepo["影子 git 仓库\n~/.hermes/checkpoints/<hash>"]

  user --> agent
  agent -->|"工具调用"| tools
  tools -->|"变更前\nensure_checkpoint()"| cpMgr
  cpMgr -->|"git add/commit"| shadowRepo
  cpMgr -->|"OK / 跳过"| tools
  tools -->|"应用变更"| agent
```

## 配置

检查点默认启用。在 `~/.hermes/config.yaml` 中配置：

```yaml
checkpoints:
  enabled: true          # 主开关（默认：true）
  max_snapshots: 50      # 每个目录最大检查点数
```

要禁用：

```yaml
checkpoints:
  enabled: false
```

禁用后，Checkpoint Manager 是空操作，不会尝试 git 操作。

## 列出检查点

在 CLI 会话中：

```
/rollback
```

Hermes 回复一个格式化的列表，显示变更统计：

```text
📸 Checkpoints for /path/to/project:

  1. 4270a8c  2026-03-16 04:36  before patch  (1 file, +1/-0)
  2. eaf4c1f  2026-03-16 04:35  before write_file
  3. b3f9d2e  2026-03-16 04:34  before terminal: sed -i s/old/new/ config.py  (1 file, +1/-1)

  /rollback <N>             恢复到检查点 N
  /rollback diff <N>        预览自检查点 N 以来的变更
  /rollback <N> <file>      从检查点 N 恢复单个文件
```

每条记录显示：

- 短哈希
- 时间戳
- 原因（触发快照的操作）
- 变更摘要（变更的文件数、插入/删除数）

## 使用 `/rollback diff` 预览变更

在决定恢复之前，预览自某个检查点以来发生了什么变化：

```
/rollback diff 1
```

这显示 git diff 统计摘要，后跟实际差异：

```text
test.py | 2 +-
 1 file changed, 1 insertion(+), 1 deletion(-)

diff --git a/test.py b/test.py
--- a/test.py
+++ b/test.py
@@ -1 +1 @@
-print('original content')
+print('modified content')
```

长差异被限制在 80 行以避免淹没终端。

## 使用 `/rollback` 恢复

通过编号恢复到检查点：

```
/rollback 1
```

在后台，Hermes：

1. 验证目标提交存在于影子仓库中。
2. 对当前状态创建一个**回滚前快照**，以便你稍后可以"撤销撤销"。
3. 恢复工作目录中的跟踪文件。
4. **撤销上一轮对话**，使智能体的上下文与恢复的文件系统状态匹配。

成功时：

```text
✅ Restored to checkpoint 4270a8c5: before patch
A pre-rollback snapshot was saved automatically.
(^_^)b Undid 4 message(s). Removed: "Now update test.py to ..."
  4 message(s) remaining in history.
  Chat turn undone to match restored file state.
```

对话撤销确保智能体不会"记住"已回滚的变更，避免下一轮混淆。

## 单文件恢复

从检查点恢复单个文件而不影响目录的其余部分：

```
/rollback 1 src/broken_file.py
```

当智能体修改了多个文件但只需要回退一个时很有用。

## 安全和性能防护

为保持检查点的安全和快速，Hermes 应用了几个防护措施：

- **Git 可用性** —— 如果在 `PATH` 上找不到 `git`，检查点会透明禁用。
- **目录范围** —— Hermes 跳过过于宽泛的目录（根 `/`、home `$HOME`）。
- **仓库大小** —— 超过 50,000 个文件的目录被跳过以避免缓慢的 git 操作。
- **无变更快照** —— 如果自上次快照以来没有变更，检查点被跳过。
- **非致命错误** —— Checkpoint Manager 内部的所有错误以调试级别记录；你的工具继续运行。

## 检查点存储位置

所有影子仓库位于：

```text
~/.hermes/checkpoints/
  ├── <hash1>/   # 一个工作目录的影子 git 仓库
  ├── <hash2>/
  └── ...
```

每个 `<hash>` 来源于工作目录的绝对路径。每个影子仓库内部有：

- 标准 git 内部结构（`HEAD`、`refs/`、`objects/`）
- 包含精心策划的忽略列表的 `info/exclude` 文件
- 指向原始项目根目录的 `HERMES_WORKDIR` 文件

你通常不需要手动触碰这些。

## 最佳实践

- **保持检查点启用** —— 默认开启，没有文件修改时零成本。
- **恢复前使用 `/rollback diff`** —— 预览将发生什么变化以选择正确的检查点。
- **当你只想撤销智能体驱动的变更时，使用 `/rollback` 而不是 `git reset`**。
- **与 Git worktree 结合使用**以获得最大安全性——让每个 Hermes 会话在自己的 worktree/分支中，检查点作为额外的保护层。

关于在同一仓库上并行运行多个智能体，请参阅 [Git worktrees](./git-worktrees.md) 指南。
