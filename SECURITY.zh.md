# Hermes Agent 安全策略

本文档概述了 **Hermes Agent** 项目的安全协议、信任模型和部署加固指南。

## 1. 漏洞报告

Hermes Agent **不运营**漏洞赏金计划。安全问题应通过 [GitHub 安全咨询 (GHSA)](https://github.com/NousResearch/hermes-agent/security/advisories/new) 报告或发送邮件至 **security@nousresearch.com**。请勿为安全漏洞创建公开的 issue。

### 提交所需信息
- **标题与严重性：** 简明描述及 CVSS 评分/等级。
- **受影响组件：** 准确的文件路径和行范围（例如 `tools/approval.py:120-145`）。
- **环境：** `hermes version` 的输出、提交 SHA、操作系统和 Python 版本。
- **复现方法：** 针对 `main` 分支或最新发布版的逐步概念验证 (PoC)。
- **影响：** 说明越过了什么信任边界。

---

## 2. 信任模型

核心假设是 Hermes 是一个**个人智能体**，有一个受信任的操作者。

### 操作者与会话信任
- **单租户：** 系统保护操作者免受 LLM 行为的影响，而非防范恶意的共租户。多用户隔离必须在操作系统/主机层面实现。
- **网关安全：** 授权的调用方（Telegram、Discord、Slack 等）享有同等信任。会话密钥用于路由，而非作为授权边界。
- **执行：** 默认使用 `terminal.backend: local`（直接在主机上执行）。容器隔离（Docker、Modal、Daytona）是可选的沙盒方案。

### 危险命令审批
审批系统（`tools/approval.py`）是核心安全边界。终端命令、文件操作和其他潜在破坏性操作在执行前需要经过用户明确确认。审批模式可通过 `config.yaml` 中的 `approvals.mode` 配置：
- `"on"`（默认） — 提示用户批准危险命令。
- `"auto"` — 在可配置的延迟后自动批准。
- `"off"` — 完全禁用该门控（紧急模式；参见第 3 节）。

### 输出脱敏
`agent/redact.py` 从所有显示输出中剥离类似密钥的模式（API 密钥、令牌、凭据），在内容到达终端或网关平台之前。这可以防止聊天日志、工具预览和响应文本中的意外凭据泄露。脱敏仅在显示层操作——底层值在内部智能体操作中保持不变。

### 技能与 MCP 服务器
- **已安装的技能：** 高信任级别。等同于本地主机代码；技能可以读取环境变量并运行任意命令。
- **MCP 服务器：** 较低信任级别。MCP 子进程接收经过过滤的环境（`tools/mcp_tool.py` 中的 `_build_safe_env()`）——仅传递安全的基线变量（`PATH`、`HOME`、`XDG_*`）加上在服务器 `env` 配置块中显式声明的变量。默认情况下会剥离主机凭据。此外，通过 `npx`/`uvx` 调用的包在生成进程前会针对 OSV 恶意软件数据库进行检查。

### 代码执行沙盒
`execute_code` 工具（`tools/code_execution_tool.py`）在子进程中运行 LLM 生成的 Python 脚本，API 密钥和令牌已从环境中剥离，以防止凭据窃取。仅通过已加载技能显式声明的环境变量（通过 `env_passthrough`）或用户在 `config.yaml` 中配置的环境变量（`terminal.env_passthrough`）才会传递。子进程通过 RPC 访问 Hermes 工具，而非直接 API 调用。

### 子智能体
- **禁止递归委派：** 子智能体中禁用 `delegate_task` 工具。
- **深度限制：** `MAX_DEPTH = 2` — 父级（深度 0）可以生成子级（深度 1）；孙级请求会被拒绝。
- **记忆隔离：** 子智能体以 `skip_memory=True` 运行，无法访问父级的持久记忆提供者。父级仅接收任务提示和最终响应作为观察结果。

---

## 3. 超出范围（非漏洞）

以下场景**不被视为**安全违规：
- **提示注入：** 除非导致审批系统、工具集限制或容器沙盒的实际绕过。
- **公开暴露：** 在没有外部认证或网络保护的情况下将网关部署到公共互联网。
- **受信任状态访问：** 需要预先拥有 `~/.hermes/`、`.env` 或 `config.yaml` 写入权限的报告（这些是操作者拥有的文件）。
- **默认行为：** 当 `terminal.backend` 设置为 `local` 时的主机级命令执行——这是文档记载的默认行为，不是漏洞。
- **配置权衡：** 故意的紧急配置如生产环境中的 `approvals.mode: "off"` 或 `terminal.backend: local`。
- **工具级读取/访问限制：** 智能体通过 `terminal` 工具设计上拥有不受限制的 Shell 访问权限。报告某个特定工具（如 `read_file`）可以访问某资源不构成漏洞，如果通过 `terminal` 也能获得同样的访问权限。工具级拒绝列表仅在终端侧配合等效限制时才构成有意义的安全边界（如写入操作中，`WRITE_DENIED_PATHS` 与危险命令审批系统配合使用）。

---

## 4. 部署加固与最佳实践

### 文件系统与网络
- **生产环境沙盒化：** 对不受信任的工作负载使用容器后端（`docker`、`modal`、`daytona`）代替 `local`。
- **文件权限：** 以非 root 用户运行（Docker 镜像使用 UID 10000）；在本地安装中使用 `chmod 600 ~/.hermes/.env` 保护凭据。
- **网络暴露：** 不要在没有 VPN、Tailscale 或防火墙保护的情况下将网关或 API 服务器暴露到公共互联网。所有网关平台适配器（Telegram、Discord、Slack、Matrix、Mattermost 等）默认启用 SSRF 保护并带有重定向验证。注意：本地终端后端不应用 SSRF 过滤，因为它在受信任的操作者环境中运行。

### 技能与供应链
- **技能安装：** 在安装第三方技能前查看技能防护报告（`tools/skills_guard.py`）。`~/.hermes/skills/.hub/audit.log` 中的审计日志跟踪每次安装和移除。
- **MCP 安全：** 在 MCP 服务器进程生成前，`npx`/`uvx` 包会自动运行 OSV 恶意软件检查。
- **CI/CD：** GitHub Actions 固定到完整的提交 SHA。`supply-chain-audit.yml` 工作流会阻止包含 `.pth` 文件或可疑的 `base64`+`exec` 模式的 PR。

### 凭据存储
- API 密钥和令牌只应存放在 `~/.hermes/.env` 中——永远不要放在 `config.yaml` 中或提交到版本控制。
- 凭据池系统（`agent/credential_pool.py`）处理密钥轮换和降级。凭据从环境变量中解析，不存储在明文数据库中。

---

## 5. 披露流程

- **协调披露：** 90 天窗口或直到修复发布，以先到者为准。
- **沟通：** 所有更新通过 GHSA 线程或与 security@nousresearch.com 的电子邮件通信进行。
- **致谢：** 除非要求匿名，报告者将在发布说明中获得致谢。
