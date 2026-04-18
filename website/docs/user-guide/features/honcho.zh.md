---
sidebar_position: 99
title: "Honcho 记忆"
description: "通过 Honcho 实现 AI 原生持久记忆——辩证推理、多代理用户建模和深度个性化"
---

# Honcho 记忆

[Honcho](https://github.com/plastic-labs/honcho) 是一个 AI 原生记忆后端，在 Hermes 内置记忆系统之上添加辩证推理和深度用户建模。Honcho 不是简单的键值存储，而是维护一个关于用户是谁的运行模型——包括他们的偏好、沟通风格、目标和模式——通过在对话发生后进行推理。

:::info Honcho 是记忆提供商插件
Honcho 集成到[记忆提供商](./memory-providers.md)系统中。以下所有功能都可通过统一的记忆提供商接口使用。
:::

## Honcho 添加了什么

| 功能 | 内置记忆 | Honcho |
|------|---------|--------|
| 跨会话持久化 | ✔ 基于文件的 MEMORY.md/USER.md | ✔ 带 API 的服务端 |
| 用户画像 | ✔ 手动代理整理 | ✔ 自动辩证推理 |
| 会话摘要 | — | ✔ 会话范围的上下文注入 |
| 多代理隔离 | — | ✔ 每对等节点配置文件隔离 |
| 观察模式 | — | ✔ 统一或定向观察 |
| 结论（衍生洞察） | — | ✔ 服务端关于模式的推理 |
| 跨历史搜索 | ✔ FTS5 会话搜索 | ✔ 对结论进行语义搜索 |

**辩证推理**：在每个对话轮次后（受 `dialecticCadence` 控制），Honcho 分析交换并衍生关于用户偏好、习惯和目标的洞察。这些随时间积累，使代理对用户的理解超越用户明确陈述的内容。辩证支持多通道深度（1-3 通道），自动冷/暖提示选择——冷启动查询关注一般用户事实，暖查询优先考虑会话范围的上下文。

**会话范围上下文**：基础上下文现在包含会话摘要以及用户表示和对等卡片。这使代理了解当前会话中已讨论的内容，减少重复并实现连续性。

**多代理画像**：当多个 Hermes 实例与同一用户通信时（例如编码助手和个人助手），Honcho 维护独立的"对等"画像。每个对等方只看到自己的观察和结论，防止上下文交叉污染。

## 设置

```bash
hermes memory setup    # 从提供商列表中选择 "honcho"
```

或手动配置：

```yaml
# ~/.hermes/config.yaml
memory:
  provider: honcho
```

```bash
echo "HONCHO_API_KEY=*** >> ~/.hermes/.env
```

在 [honcho.dev](https://honcho.dev) 获取 API 密钥。

## 架构

### 两层上下文注入

每个轮次（在 `hybrid` 或 `context` 模式下），Honcho 组装两层注入系统提示的上下文：

1. **基础上下文** ——会话摘要、用户表示、用户对等卡片、AI 自我表示和 AI 身份卡片。按 `contextCadence` 刷新。这是"这个用户是谁"层。
2. **辩证补充** ——LLM 合成的关于用户当前状态和需求的推理。按 `dialecticCadence` 刷新。这是"现在什么最重要"层。

两层被连接并截断到 `contextTokens` 预算（如果设置）。

### 冷/暖提示选择

辩证在两种提示策略之间自动选择：

- **冷启动**（尚无基础上下文）：一般查询——"这个人是谁？他们的偏好、目标和工作风格是什么？"
- **暖会话**（基础上下文已存在）：会话范围查询——"根据此会话中已讨论的内容，关于此用户的哪些上下文最相关？"

这根据基础上下文是否已填充自动发生。

### 三个正交配置旋钮

成本和深度由三个独立旋钮控制：

| 旋钮 | 控制 | 默认值 |
|------|------|--------|
| `contextCadence` | `context()` API 调用之间的轮次（基础层刷新） | `1` |
| `dialecticCadence` | `peer.chat()` LLM 调用之间的轮次（辩证层刷新） | `3` |
| `dialecticDepth` | 每次辩证调用的 `.chat()` 通道数（1-3） | `1` |

这些是正交的——你可以有频繁的上下文刷新配合不频繁的辩证，或低频率的深度多通道辩证。示例：`contextCadence: 1, dialecticCadence: 5, dialecticDepth: 2` 每轮刷新基础上下文，每 5 轮运行辩证，每次辩证运行进行 2 个通道。

### 辩证深度（多通道）

当 `dialecticDepth` > 1 时，每次辩证调用运行多个 `.chat()` 通道：

- **通道 0**：冷或暖提示（见上文）
- **通道 1**：自我审计——识别初始评估中的差距并从最近会话中综合证据
- **通道 2**：协调——检查先前通道之间的矛盾并产生最终综合

每个通道使用按比例的推理级别（早期通道较轻，主通道使用基础级别）。使用 `dialecticDepthLevels` 覆盖每通道级别——例如，深度 3 运行使用 `["minimal", "medium", "high"]`。

如果先前通道返回强信号（长且结构化的输出），通道会提前退出，因此深度 3 并不总是意味着 3 次 LLM 调用。

## 配置选项

Honcho 在 `~/.honcho/config.json`（全局）或 `$HERMES_HOME/honcho.json`（配置文件本地）中配置。设置向导为你处理此事。

### 完整配置参考

| 键 | 默认值 | 描述 |
|----|--------|------|
| `contextTokens` | `null`（无上限） | 每轮自动注入上下文的 token 预算。设为整数（例如 1200）以限制。在词边界截断 |
| `contextCadence` | `1` | `context()` API 调用之间的最小轮次（基础层刷新） |
| `dialecticCadence` | `3` | `peer.chat()` LLM 调用之间的最小轮次（辩证层）。在 `tools` 模式下无关——模型显式调用 |
| `dialecticDepth` | `1` | 每次辩证调用的 `.chat()` 通道数。限制在 1-3 |
| `dialecticDepthLevels` | `null` | 每通道推理级别的可选数组，例如 `["minimal", "low", "medium"]`。覆盖比例默认值 |
| `dialecticReasoningLevel` | `'low'` | 基础推理级别：`minimal`、`low`、`medium`、`high`、`max` |
| `dialecticDynamic` | `true` | 为 `true` 时，模型可通过工具参数覆盖每次调用的推理级别 |
| `dialecticMaxChars` | `600` | 注入系统提示的辩证结果最大字符数 |
| `recallMode` | `'hybrid'` | `hybrid`（自动注入 + 工具）、`context`（仅注入）、`tools`（仅工具） |
| `writeFrequency` | `'async'` | 何时刷新消息：`async`（后台线程）、`turn`（同步）、`session`（结束时批量）或整数 N |
| `saveMessages` | `true` | 是否将消息持久化到 Honcho API |
| `observationMode` | `'directional'` | `directional`（全部开启）或 `unified`（共享池）。使用 `observation` 对象进行细粒度控制 |
| `messageMaxChars` | `25000` | 通过 `add_messages()` 发送的每条消息最大字符数。超过时分块 |
| `dialecticMaxInputChars` | `10000` | `peer.chat()` 辩证查询输入的最大字符数 |
| `sessionStrategy` | `'per-directory'` | `per-directory`、`per-repo`、`per-session` 或 `global` |

**会话策略**控制 Honcho 会话如何映射到你的工作：
- `per-session` ——每次 `hermes` 运行获得全新会话。干净启动，通过工具使用记忆。推荐新用户使用。
- `per-directory` ——每个工作目录一个 Honcho 会话。上下文跨运行积累。
- `per-repo` ——每个 git 仓库一个会话。
- `global` ——所有目录共享单个会话。

**回忆模式**控制记忆如何流入对话：
- `hybrid` ——上下文自动注入系统提示，工具也可用（模型决定何时查询）。
- `context` ——仅自动注入，工具隐藏。
- `tools` ——仅工具，无自动注入。代理必须显式调用 `honcho_reasoning`、`honcho_search` 等。

**每种回忆模式的设置：**

| 设置 | `hybrid` | `context` | `tools` |
|------|----------|-----------|---------|
| `writeFrequency` | 刷新消息 | 刷新消息 | 刷新消息 |
| `contextCadence` | 控制基础上下文刷新 | 控制基础上下文刷新 | 无关——无注入 |
| `dialecticCadence` | 控制自动 LLM 调用 | 控制自动 LLM 调用 | 无关——模型显式调用 |
| `dialecticDepth` | 每次调用多通道 | 每次调用多通道 | 无关——模型显式调用 |
| `contextTokens` | 限制注入 | 限制注入 | 无关——无注入 |
| `dialecticDynamic` | 控制模型覆盖 | 不适用（无工具） | 控制模型覆盖 |

在 `tools` 模式下，模型完全控制——它在需要时调用 `honcho_reasoning`，选择任何 `reasoning_level`。节奏和预算设置仅适用于具有自动注入的模式（`hybrid` 和 `context`）。

## 工具

当 Honcho 作为记忆提供商激活时，五个工具可用：

| 工具 | 用途 |
|------|------|
| `honcho_profile` | 读取或更新对等卡片——传入 `card`（事实列表）更新，省略则读取 |
| `honcho_search` | 对上下文进行语义搜索——原始摘录，无 LLM 合成 |
| `honcho_context` | 完整会话上下文——摘要、表示、卡片、最近消息 |
| `honcho_reasoning` | 来自 Honcho LLM 的合成答案——传入 `reasoning_level`（minimal/low/medium/high/max）控制深度 |
| `honcho_conclude` | 创建或删除结论——传入 `conclusion` 创建，`delete_id` 删除（仅 PII） |

## CLI 命令

```bash
hermes honcho status          # 连接状态、配置和关键设置
hermes honcho setup           # 交互式设置向导
hermes honcho strategy        # 显示或设置会话策略
hermes honcho peer            # 更新多代理设置的对等名称
hermes honcho mode            # 显示或设置回忆模式
hermes honcho tokens          # 显示或设置上下文 token 预算
hermes honcho identity        # 显示 Honcho 对等身份
hermes honcho sync            # 同步所有配置文件的主机块
hermes honcho enable          # 启用 Honcho
hermes honcho disable         # 禁用 Honcho
```

## 从 `hermes honcho` 迁移

如果你之前使用独立的 `hermes honcho setup`：

1. 你现有的配置（`honcho.json` 或 `~/.honcho/config.json`）被保留
2. 你的服务端数据（记忆、结论、用户画像）完好无损
3. 在 config.yaml 中设置 `memory.provider: honcho` 以重新激活

无需重新登录或重新设置。运行 `hermes memory setup` 并选择 "honcho"——向导会检测你现有的配置。

## 完整文档

参见[记忆提供商 — Honcho](./memory-providers.md#honcho) 获取完整参考。
