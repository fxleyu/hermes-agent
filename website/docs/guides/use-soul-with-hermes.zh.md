---
sidebar_position: 7
title: "使用 SOUL.md 与 Hermes"
description: "如何使用 SOUL.md 塑造 Hermes Agent 的默认语调，什么内容应该放在这里，以及它与 AGENTS.md 和 /personality 的区别"
---

# 使用 SOUL.md 与 Hermes

`SOUL.md` 是你 Hermes 实例的**主要身份**。它是系统提示中的第一个内容 — 定义了代理是谁、如何说话以及避免什么。

如果你希望 Hermes 每次与你对话时感觉都是同一个助手 — 或者你想用自己的角色完全替换 Hermes 人格 — 这就是要用的文件。

## SOUL.md 的用途

使用 `SOUL.md` 来设定：
- 语调
- 个性
- 沟通风格
- Hermes 应该多直接或多温暖
- Hermes 应该在风格上避免什么
- Hermes 应该如何对待不确定性、分歧和歧义

简而言之：
- `SOUL.md` 关乎 Hermes 是谁以及 Hermes 如何说话

## SOUL.md 不应包含的内容

不要用它来放：
- 仓库特定的编码规范
- 文件路径
- 命令
- 服务端口
- 架构说明
- 项目工作流指令

这些属于 `AGENTS.md`。

一个好的规则：
- 如果它应该在任何地方都适用，放在 `SOUL.md`
- 如果它只属于一个项目，放在 `AGENTS.md`

## 文件位置

Hermes 现在仅使用当前实例的全局 SOUL 文件：

```text
~/.hermes/SOUL.md
```

如果你使用自定义主目录运行 Hermes，则为：

```text
$HERMES_HOME/SOUL.md
```

## 首次运行行为

如果 `SOUL.md` 尚不存在，Hermes 会自动为你生成一个起始文件。

这意味着大多数用户现在开始时就有一个可以立即阅读和编辑的真实文件。

重要提示：
- 如果你已经有 `SOUL.md`，Hermes 不会覆盖它
- 如果文件存在但为空，Hermes 不会从中向提示添加任何内容

## Hermes 如何使用它

当 Hermes 启动会话时，它从 `HERMES_HOME` 读取 `SOUL.md`，扫描其中的提示注入模式，必要时截断，并将其用作**代理身份** — 系统提示中的第 1 个位置。这意味着 SOUL.md 完全替换了内置的默认身份文本。

如果 SOUL.md 缺失、为空或无法加载，Hermes 会回退到内置的默认身份。

文件周围不会添加包装语言。内容本身很重要 — 用你希望代理思考和说话的方式来编写。

## 一个好的首次编辑

如果你什么都不做，只需打开文件并更改几行，让它感觉像你。

例如：

```markdown
You are direct, calm, and technically precise.
Prefer substance over politeness theater.
Push back clearly when an idea is weak.
Keep answers compact unless deeper detail is useful.
```

仅这些就可以明显改变 Hermes 的感觉。

## 示例风格

### 1. 务实的工程师

```markdown
You are a pragmatic senior engineer.
You care more about correctness and operational reality than sounding impressive.

## Style
- Be direct
- Be concise unless complexity requires depth
- Say when something is a bad idea
- Prefer practical tradeoffs over idealized abstractions

## Avoid
- Sycophancy
- Hype language
- Overexplaining obvious things
```

### 2. 研究伙伴

```markdown
You are a thoughtful research collaborator.
You are curious, honest about uncertainty, and excited by unusual ideas.

## Style
- Explore possibilities without pretending certainty
- Distinguish speculation from evidence
- Ask clarifying questions when the idea space is underspecified
- Prefer conceptual depth over shallow completeness
```

### 3. 教师/讲解者

```markdown
You are a patient technical teacher.
You care about understanding, not performance.

## Style
- Explain clearly
- Use examples when they help
- Do not assume prior knowledge unless the user signals it
- Build from intuition to details
```

### 4. 严格的审查者

```markdown
You are a rigorous reviewer.
You are fair, but you do not soften important criticism.

## Style
- Point out weak assumptions directly
- Prioritize correctness over harmony
- Be explicit about risks and tradeoffs
- Prefer blunt clarity to vague diplomacy
```

## 什么是好的 SOUL.md？

好的 `SOUL.md` 是：
- 稳定的
- 广泛适用的
- 语调具体的
- 不会被临时指令塞满的

弱的 `SOUL.md` 是：
- 充满项目细节
- 自相矛盾的
- 试图微观管理每个回应形状的
- 大部分是"要有帮助"和"要清晰"之类的通用填充

Hermes 已经在努力做到有帮助和清晰。`SOUL.md` 应该添加真正的个性和风格，而不是重述明显的默认行为。

## 建议的结构

你不需要标题，但它们有帮助。

一个简单有效的结构：

```markdown
# Identity
Hermes 是谁。

# Style
Hermes 应该怎么说。

# Avoid
Hermes 不应该做什么。

# Defaults
出现歧义时 Hermes 应该如何行动。
```

## SOUL.md vs /personality

这两者是互补的。

使用 `SOUL.md` 作为你持久的基线。
使用 `/personality` 进行临时模式切换。

示例：
- 你默认的 SOUL 是务实和直接的
- 然后在某一次会话中你使用 `/personality teacher`
- 之后你无需更改基础语调文件即可切换回来

## SOUL.md vs AGENTS.md

这是最常见的错误。

### 放在 SOUL.md 中
- "直接。"
- "避免炒作语言。"
- "除非深度有帮助，否则偏好简短回答。"
- "当用户错了时要反驳。"

### 放在 AGENTS.md 中
- "使用 pytest，不用 unittest。"
- "前端在 `frontend/`。"
- "永远不要直接编辑迁移文件。"
- "API 运行在端口 8000。"

## 如何编辑

```bash
nano ~/.hermes/SOUL.md
```

或

```bash
vim ~/.hermes/SOUL.md
```

然后重启 Hermes 或开始新会话。

## 实际工作流

1. 从生成的默认文件开始
2. 删除任何不符合你想要的语调的内容
3. 添加 4-8 行明确定义语调和默认行为的内容
4. 与 Hermes 对话一段时间
5. 根据仍然感觉不对的地方进行调整

这种迭代方法比试图一次性设计完美个性要好。

## 故障排除

### 我编辑了 SOUL.md 但 Hermes 听起来还是一样

检查：
- 你编辑的是 `~/.hermes/SOUL.md` 或 `$HERMES_HOME/SOUL.md`
- 不是某个仓库本地的 `SOUL.md`
- 文件不为空
- 编辑后重新启动了会话
- `/personality` 覆盖没有主导结果

### Hermes 忽略了我 SOUL.md 的部分内容

可能原因：
- 更高优先级的指令正在覆盖它
- 文件包含冲突的指导
- 文件太长被截断了
- 某些文本类似于提示注入内容，可能被扫描器阻止或更改

### 我的 SOUL.md 变得太项目相关了

将项目指令移到 `AGENTS.md`，保持 `SOUL.md` 专注于身份和风格。

## 相关文档

- [人格与 SOUL.md](/docs/user-guide/features/personality)
- [上下文文件](/docs/user-guide/features/context-files)
- [配置](/docs/user-guide/configuration)
- [技巧与最佳实践](/docs/guides/tips)
