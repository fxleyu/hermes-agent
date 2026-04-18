# Hermes Agent 早在三月就有了"例行任务"

Anthropic 刚刚宣布了 [Claude Code Routines](https://claude.com/blog/introducing-routines-in-claude-code) — 定时任务、GitHub 事件触发器和 API 触发的代理运行。捆绑提示词 + 仓库 + 连接器，运行在他们的基础设施上。

这是一个好功能。我们两个月前就发布了。

---

## 三种触发类型 — 并排对比

Claude Code Routines 提供三种触发自动化的方式：

**1. 定时（cron）**
> "每晚凌晨 2 点：从 Linear 拉取最高优先级的 bug，尝试修复，并打开一个草稿 PR。"

Hermes 等价方案 — 今天就能用：
```bash
hermes cron create "0 2 * * *" \
  "Pull the top bug from the issue tracker, attempt a fix, and open a draft PR." \
  --name "Nightly bug fix" \
  --deliver telegram
```

**2. GitHub 事件（webhook）**
> "标记触及 /auth-provider 模块的 PR 并发布到 #auth-changes。"

Hermes 等价方案 — 今天就能用：
```bash
hermes webhook subscribe auth-watch \
  --events "pull_request" \
  --prompt "PR #{pull_request.number}: {pull_request.title} by {pull_request.user.login}. Check if it touches the auth-provider module. If yes, summarize the changes." \
  --deliver slack
```

**3. API 触发器**
> "读取告警负载，找到负责的服务，向 #oncall 发布分诊摘要。"

Hermes 等价方案 — 今天就能用：
```bash
hermes webhook subscribe alert-triage \
  --prompt "Alert: {alert.name} — Severity: {alert.severity}. Find the owning service, investigate, and post a triage summary with proposed first steps." \
  --deliver slack
```

他们博客文章中的每个用例 — 积压任务分诊、文档漂移、部署验证、告警关联、库移植、定制化 PR 审查 — 都有可工作的 Hermes 实现。不需要新功能。自 2026 年 3 月起就已发布。

---

## 有什么不同

| | Claude Code Routines | Hermes Agent |
|---|---|---|
| **定时任务** | 基于计划 | 任意 cron 表达式 + 人类可读间隔 |
| **GitHub 触发器** | PR、issue、push 事件 | 通过 webhook 订阅的任意 GitHub 事件 |
| **API 触发器** | POST 到唯一端点 | POST 到带 HMAC 认证的 webhook 路由 |
| **MCP 连接器** | 原生连接器 | 完整 MCP 客户端支持 |
| **脚本预处理** | 不支持 | Python 脚本在代理前运行，注入上下文 |
| **技能链接** | 不支持 | 每个自动化加载多个技能 |
| **每日限制** | 5-25 次/天 | **无限制** |
| **模型选择** | 仅 Claude | **任何模型** — Claude、GPT、Gemini、DeepSeek、Qwen、本地 |
| **投递目标** | GitHub 评论 | Telegram、Discord、Slack、短信、邮件、GitHub 评论、webhooks、本地文件 |
| **基础设施** | Anthropic 的服务器 | **你的基础设施** — VPS、家庭服务器、笔记本电脑 |
| **数据驻留** | Anthropic 的云 | **你的机器** |
| **成本** | Pro/Max/Team/Enterprise 订阅 | 你的 API 密钥，你的费率 |
| **开源** | 否 | **是** — MIT 许可证 |

---

## Hermes 能做而 Routines 不能做的事

### 脚本注入

在代理*之前*运行 Python 脚本。脚本的标准输出成为上下文。脚本处理机械性工作（获取、比较、计算）；代理处理推理。

```bash
hermes cron create "every 1h" \
  "If CHANGE DETECTED, summarize what changed. If NO_CHANGE, respond with [SILENT]." \
  --script ~/.hermes/scripts/watch-site.py \
  --name "Pricing monitor" \
  --deliver telegram
```

`[SILENT]` 模式意味着只有在实际发生变化时才会通知你。没有垃圾消息。

### 多技能工作流

将专业技能链接在一起。每个技能教会代理一种特定能力，提示词将它们串联起来。

```bash
hermes cron create "0 8 * * *" \
  "Search arXiv for papers on language model reasoning. Save the top 3 as Obsidian notes." \
  --skills "arxiv,obsidian" \
  --name "Paper digest"
```

### 投递到任何地方

一个自动化，任何目的地：

```bash
--deliver telegram                      # Telegram 主频道
--deliver discord                       # Discord 主频道
--deliver slack                         # Slack 频道
--deliver sms:+15551234567              # 短信
--deliver telegram:-1001234567890:42    # 特定 Telegram 论坛话题
--deliver local                         # 保存到文件，无通知
```

### 模型无关

你的夜间分诊可以运行在 Claude 上。你的部署验证可以运行在 GPT 上。你的成本敏感监控可以运行在 DeepSeek 或本地模型上。相同的自动化系统，任何后端。

---

## 限制说明一切

Claude Code Routines：Pro 版**每天 5 个例行任务**。**Enterprise 版 25 个。**这就是他们的上限。

Hermes 没有每日限制。如果你想的话，一天运行 500 个自动化。唯一的约束是你的 API 预算，而你可以选择哪些模型用于哪些任务。

Sonnet 上的夜间积压任务分诊大约花费 $0.02-0.05。DeepSeek 上的监控检查花费几分之一美分。你控制经济学。

---

## 开始使用

Hermes Agent 是开源且免费的。自动化基础设施 — cron 调度器、webhook 平台、技能系统、多平台投递 — 都是内置的。

```bash
pip install hermes-agent
hermes setup
```

30 秒内设置定时任务：
```bash
hermes cron create "0 9 * * 1" \
  "Generate a weekly AI news digest. Search the web for major announcements, trending repos, and notable papers. Keep it under 500 words with links." \
  --name "Weekly digest" \
  --deliver telegram
```

60 秒内设置 GitHub webhook：
```bash
hermes gateway setup    # 启用 webhooks
hermes webhook subscribe pr-review \
  --events "pull_request" \
  --prompt "Review PR #{pull_request.number}: {pull_request.title}" \
  --skills "github-code-review" \
  --deliver github_comment
```

完整自动化模板库：[hermes-agent.nousresearch.com/docs/guides/automation-templates](https://hermes-agent.nousresearch.com/docs/guides/automation-templates)

文档：[hermes-agent.nousresearch.com](https://hermes-agent.nousresearch.com)

GitHub：[github.com/NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent)

---

*Hermes Agent 由 [Nous Research](https://nousresearch.com) 构建。开源、模型无关、运行在你的基础设施上。*
