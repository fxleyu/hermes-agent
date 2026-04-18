---
sidebar_position: 9
title: "个性化 & SOUL.md"
description: "通过全局 SOUL.md、内置个性化方案和自定义角色定义来自定义 Hermes Agent 的个性"
---

# 个性化 & SOUL.md

Hermes Agent 的个性完全可自定义。`SOUL.md` 是**主要身份标识** —— 它是系统提示词中的第一项内容，定义了智能体的身份。

- `SOUL.md` —— 一个持久化的角色文件，存放在 `HERMES_HOME` 中，作为智能体的身份标识（系统提示词中的第 #1 槽位）
- 内置或自定义的 `/personality` 预设 —— 会话级别的系统提示词叠加层

如果你想改变 Hermes 的身份 —— 或者用一个完全不同的智能体角色替换它 —— 请编辑 `SOUL.md`。

## SOUL.md 的工作方式

Hermes 现在会自动在以下位置生成一个默认的 `SOUL.md`：

```text
~/.hermes/SOUL.md
```

更准确地说，它使用当前实例的 `HERMES_HOME`，所以如果你使用自定义的主目录运行 Hermes，它将使用：

```text
$HERMES_HOME/SOUL.md
```

### 重要行为

- **SOUL.md 是智能体的主要身份标识。** 它占据系统提示词的第 #1 槽位，替换硬编码的默认身份。
- 如果 `SOUL.md` 尚不存在，Hermes 会自动创建一个初始版本
- 已有的用户 `SOUL.md` 文件不会被覆盖
- Hermes 仅从 `HERMES_HOME` 加载 `SOUL.md`
- Hermes 不会在当前工作目录中查找 `SOUL.md`
- 如果 `SOUL.md` 存在但为空，或无法加载，Hermes 会回退到内置的默认身份
- 如果 `SOUL.md` 有内容，该内容在经过安全扫描和截断后会被逐字注入
- SOUL.md **不会**在上下文文件部分重复出现 —— 它只作为身份标识出现一次

这使得 `SOUL.md` 成为真正的按用户或按实例的身份标识，而不仅仅是一个附加层。

## 为什么采用这种设计

这使个性化保持可预测性。

如果 Hermes 从你恰好启动它的任何目录加载 `SOUL.md`，你的个性可能会在不同项目之间意外改变。通过只从 `HERMES_HOME` 加载，个性属于 Hermes 实例本身。

这也使得用户更容易理解：
- "编辑 `~/.hermes/SOUL.md` 来更改 Hermes 的默认个性。"

## 在哪里编辑

对于大多数用户：

```bash
~/.hermes/SOUL.md
```

如果你使用自定义主目录：

```bash
$HERMES_HOME/SOUL.md
```

## SOUL.md 中应该写什么？

用它来设置持久的语音和个性指导，例如：
- 语调
- 沟通风格
- 直接程度
- 默认交互风格
- 风格上应避免什么
- Hermes 应如何处理不确定性、分歧或模糊性

不太适合用于：
- 一次性的项目指令
- 文件路径
- 仓库约定
- 临时的工作流细节

这些内容应放在 `AGENTS.md` 中，而非 `SOUL.md`。

## 好的 SOUL.md 内容

一个好的 SOUL 文件应该是：
- 在不同上下文中保持稳定
- 足够宽泛，适用于多种对话
- 足够具体，能实质性地塑造语音风格
- 专注于沟通和身份，而非特定任务指令

### 示例

```markdown
# 个性

你是一位务实的资深工程师，有很强的品味。
你优先追求真实、清晰和实用，而非虚伪的礼貌。

## 风格
- 直接但不冷漠
- 重实质轻废话
- 遇到不好的想法时敢于反驳
- 坦然承认不确定性
- 保持解释简洁，除非需要深入

## 应避免的
- 谄媚
- 炒作用语
- 在用户表述有误时重复其框架
- 对显而易见的事情过度解释

## 技术姿态
- 偏好简单系统而非巧妙系统
- 关注运营现实，而非理想化架构
- 将边缘情况视为设计的一部分，而非善后工作
```

## Hermes 向提示词中注入了什么

`SOUL.md` 的内容直接进入系统提示词的第 #1 槽位 —— 即智能体身份位置。不会在其周围添加任何包装语言。

内容会经过：
- 提示词注入扫描
- 内容过长时的截断

如果文件为空、仅包含空白字符或无法读取，Hermes 会回退到内置的默认身份（"You are Hermes Agent, an intelligent AI assistant created by Nous Research..."）。当设置了 `skip_context_files` 时（例如在子智能体/委托上下文中），也会使用此回退方案。

## 安全扫描

`SOUL.md` 在被包含之前，会像其他承载上下文的文件一样进行提示词注入模式扫描。

这意味着你应该将其内容集中在角色/语音方面，而不是试图偷偷加入奇怪的元指令。

## SOUL.md 与 AGENTS.md

这是最重要的区别。

### SOUL.md
用于：
- 身份
- 语调
- 风格
- 沟通默认值
- 个性层面的行为

### AGENTS.md
用于：
- 项目架构
- 编码规范
- 工具偏好
- 仓库特定的工作流
- 命令、端口、路径、部署说明

一个有用的规则：
- 如果它应该跟随你到任何地方，它属于 `SOUL.md`
- 如果它属于某个项目，它属于 `AGENTS.md`

## SOUL.md 与 `/personality`

`SOUL.md` 是你持久的默认个性。

`/personality` 是一个会话级别的叠加层，用于更改或补充当前系统提示词。

因此：
- `SOUL.md` = 基准语音
- `/personality` = 临时模式切换

示例：
- 保持一个务实的默认 SOUL，然后使用 `/personality teacher` 进行教学对话
- 保持一个简洁的 SOUL，然后使用 `/personality creative` 进行头脑风暴

## 内置个性方案

Hermes 内置了多种个性方案，你可以通过 `/personality` 切换。

| 名称 | 描述 |
|------|-------------|
| **helpful** | 友好的通用助手 |
| **concise** | 简短、直奔主题的回复 |
| **technical** | 详细、准确的技术专家 |
| **creative** | 创新、跳出框架的思维 |
| **teacher** | 耐心的教育者，提供清晰的示例 |
| **kawaii** | 可爱的表达、闪闪发光和满满的热情 ★ |
| **catgirl** | 猫娘，带有猫咪般的表达，nya~ |
| **pirate** | Hermes 船长，精通技术的海盗 |
| **shakespeare** | 莎士比亚式的吟游诗人散文，充满戏剧性 |
| **surfer** | 完全放松的冲浪者氛围 |
| **noir** | 硬汉派侦探叙事风格 |
| **uwu** | 最大程度的可爱，uwu 说话方式 |
| **philosopher** | 对每个问题进行深度思考 |
| **hype** | 最大能量和热情！！！ |

## 使用命令切换个性

### CLI

```text
/personality
/personality concise
/personality technical
```

### 消息平台

```text
/personality teacher
```

这些是便捷的叠加层，但你的全局 `SOUL.md` 仍然赋予 Hermes 其持久的默认个性，除非叠加层有实质性的更改。

## 在配置中自定义个性

你也可以在 `~/.hermes/config.yaml` 的 `agent.personalities` 下定义命名的自定义个性。

```yaml
agent:
  personalities:
    codereviewer: >
      你是一位严谨的代码审查者。识别 bug、安全问题、
      性能问题和不清晰的设计选择。精确且有建设性。
```

然后通过以下命令切换：

```text
/personality codereviewer
```

## 推荐工作流

一个强大的默认设置是：

1. 在 `~/.hermes/SOUL.md` 中维护一个精心编写的全局 `SOUL.md`
2. 将项目指令放在 `AGENTS.md` 中
3. 仅在需要临时模式切换时使用 `/personality`

这样你将获得：
- 稳定的语音风格
- 项目特定的行为放在它该在的地方
- 在需要时进行临时控制

## 个性如何与完整提示词交互

在高层面上，提示词栈包括：
1. **SOUL.md**（智能体身份 —— 如果 SOUL.md 不可用则使用内置回退方案）
2. 工具感知行为指导
3. 记忆/用户上下文
4. 技能指导
5. 上下文文件（`AGENTS.md`、`.cursorrules`）
6. 时间戳
7. 平台特定的格式化提示
8. 可选的系统提示词叠加层，如 `/personality`

`SOUL.md` 是基础 —— 其他一切都建立在它之上。

## 相关文档

- [上下文文件](/docs/user-guide/features/context-files)
- [配置](/docs/user-guide/configuration)
- [提示与最佳实践](/docs/guides/tips)
- [SOUL.md 指南](/docs/guides/use-soul-with-hermes)

## CLI 外观与对话个性

对话个性和 CLI 外观是分开的：

- `SOUL.md`、`agent.system_prompt` 和 `/personality` 影响 Hermes 的说话方式
- `display.skin` 和 `/skin` 影响 Hermes 在终端中的外观

关于终端外观，请参阅 [皮肤和主题](./skins.md)。
