---
sidebar_position: 3
title: "常见问题与故障排除"
description: "Hermes Agent 的常见问题解答及常见问题解决方案"
---

# 常见问题与故障排除

常见问题的快速解答与修复方案。

---

## 常见问题

### Hermes 支持哪些 LLM 提供商？

Hermes Agent 支持任何 OpenAI 兼容的 API。已支持的提供商包括：

- **[OpenRouter](https://openrouter.ai/)** — 通过一个 API 密钥访问数百种模型（推荐，灵活性最佳）
- **Nous Portal** — Nous Research 自有的推理端点
- **OpenAI** — GPT-4o、o1、o3 等
- **Anthropic** — Claude 系列模型（通过 OpenRouter 或兼容代理）
- **Google** — Gemini 系列模型（通过 OpenRouter 或兼容代理）
- **z.ai / ZhipuAI** — GLM 系列模型
- **Kimi / Moonshot AI** — Kimi 系列模型
- **MiniMax** — 全球及中国端点
- **本地模型** — 通过 [Ollama](https://ollama.com/)、[vLLM](https://docs.vllm.ai/)、[llama.cpp](https://github.com/ggerganov/llama.cpp)、[SGLang](https://github.com/sgl-project/sglang) 或任何 OpenAI 兼容服务器

使用 `hermes model` 设置提供商，或编辑 `~/.hermes/.env`。所有提供商密钥请参阅[环境变量](./environment-variables.md)参考文档。

### 支持 Windows 吗？

**不原生支持。** Hermes Agent 需要类 Unix 环境。在 Windows 上，请安装 [WSL2](https://learn.microsoft.com/en-us/windows/wsl/install) 并在其中运行 Hermes。标准安装命令在 WSL2 中可以正常使用：

```bash
curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash
```

### 支持 Android / Termux 吗？

支持 — Hermes 现在已经有经过测试的 Termux 安装方式，适用于 Android 手机。

快速安装：

```bash
curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash
```

完整的手动安装步骤、支持的附加组件及当前限制，请参阅 [Termux 指南](../getting-started/termux.md)。

重要提示：由于 `voice` 附加组件依赖 `faster-whisper` → `ctranslate2`，而 `ctranslate2` 未发布 Android wheels，因此完整的 `.[all]` 附加组件目前在 Android 上不可用。请使用经过测试的 `.[termux]` 附加组件。

### 我的数据会被发送到哪里？

API 调用**仅发送到你配置的 LLM 提供商**（例如 OpenRouter、你本地的 Ollama 实例）。Hermes Agent 不收集遥测数据、使用数据或分析数据。你的对话、记忆和技能均存储在本地 `~/.hermes/` 目录中。

### 可以离线使用 / 配合本地模型使用吗？

可以。运行 `hermes model`，选择 **Custom endpoint**，然后输入你的服务器 URL：

```bash
hermes model
# 选择：Custom endpoint (enter URL manually)
# API base URL: http://localhost:11434/v1
# API key: ollama
# Model name: qwen3.5:27b
# Context length: 32768   ← 设置为与你服务器实际上下文窗口匹配的值
```

或直接在 `config.yaml` 中配置：

```yaml
model:
  default: qwen3.5:27b
  provider: custom
  base_url: http://localhost:11434/v1
```

Hermes 将端点、提供商和 base URL 持久化到 `config.yaml` 中，因此重启后仍然有效。如果你的本地服务器只加载了一个模型，`/model custom` 会自动检测到它。你也可以在 config.yaml 中设置 `provider: custom` — 它是一个一等公民提供商，不是其他任何东西的别名。

此配置适用于 Ollama、vLLM、llama.cpp server、SGLang、LocalAI 等。详情请参阅[配置指南](../user-guide/configuration.md)。

:::tip Ollama 用户
如果你在 Ollama 中设置了自定义的 `num_ctx`（例如 `ollama run --num_ctx 16384`），请确保在 Hermes 中设置匹配的上下文长度 — Ollama 的 `/api/show` 报告的是模型的*最大*上下文，而不是你配置的实际 `num_ctx`。
:::

:::tip 本地模型超时问题
Hermes 会自动检测本地端点并放宽流式超时（读取超时从 120 秒提高到 1800 秒，停滞流检测被禁用）。如果在非常大的上下文中仍然遇到超时，请在 `.env` 中设置 `HERMES_STREAM_READ_TIMEOUT=1800`。详情请参阅[本地 LLM 指南](../guides/local-llm-on-mac.md#timeouts)。
:::

### 费用如何？

Hermes Agent 本身是**免费且开源的**（MIT 许可证）。你只需为所选提供商的 LLM API 使用量付费。本地模型完全免费运行。

### 多人可以共用一个实例吗？

可以。[消息网关](../user-guide/messaging/index.md)允许多个用户通过 Telegram、Discord、Slack、WhatsApp 或 Home Assistant 与同一个 Hermes Agent 实例交互。访问控制通过允许列表（特定用户 ID）和私聊配对（第一个发消息的用户获得访问权限）来实现。

### 记忆和技能有什么区别？

- **记忆**存储**事实** — 代理了解的关于你、你的项目和偏好的信息。记忆会根据相关性自动检索。
- **技能**存储**流程** — 如何做事的分步指令。当代理遇到类似任务时会调用技能。

两者都跨会话持久化。详情请参阅[记忆](../user-guide/features/memory.md)和[技能](../user-guide/features/skills.md)。

### 可以在自己的 Python 项目中使用吗？

可以。导入 `AIAgent` 类并以编程方式使用 Hermes：

```python
from run_agent import AIAgent

agent = AIAgent(model="openrouter/nous/hermes-3-llama-3.1-70b")
response = agent.chat("Explain quantum computing briefly")
```

完整 API 用法请参阅 [Python 库指南](../user-guide/features/code-execution.md)。

---

## 故障排除

### 安装问题

#### 安装后提示 `hermes: command not found`

**原因：** 你的 shell 尚未重新加载更新后的 PATH。

**解决方案：**
```bash
# 重新加载 shell 配置文件
source ~/.bashrc    # bash
source ~/.zshrc     # zsh

# 或者打开一个新的终端会话
```

如果仍然不起作用，请验证安装位置：
```bash
which hermes
ls ~/.local/bin/hermes
```

:::tip
安装程序会将 `~/.local/bin` 添加到你的 PATH。如果你使用非标准的 shell 配置，请手动添加 `export PATH="$HOME/.local/bin:$PATH"`。
:::

#### Python 版本过旧

**原因：** Hermes 需要 Python 3.11 或更新版本。

**解决方案：**
```bash
python3 --version   # 检查当前版本

# 安装更新的 Python
sudo apt install python3.12   # Ubuntu/Debian
brew install python@3.12      # macOS
```

安装程序会自动处理此问题 — 如果在手动安装过程中看到此错误，请先升级 Python。

#### `uv: command not found`

**原因：** `uv` 包管理器未安装或不在 PATH 中。

**解决方案：**
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.bashrc
```

#### 安装时出现权限拒绝错误

**原因：** 没有足够的权限写入安装目录。

**解决方案：**
```bash
# 不要使用 sudo 运行安装程序 — 它会安装到 ~/.local/bin
# 如果之前使用 sudo 安装过，请清理：
sudo rm /usr/local/bin/hermes
# 然后重新运行标准安装程序
curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash
```

---

### 提供商与模型问题

#### `/model` 只显示一个提供商 / 无法切换提供商

**原因：** `/model`（在聊天会话中）只能在你**已经配置好的**提供商之间切换。如果你只设置了 OpenRouter，`/model` 就只会显示它。

**解决方案：** 退出会话，在终端中使用 `hermes model` 添加新的提供商：

```bash
# 先退出 Hermes 聊天会话（Ctrl+C 或 /quit）

# 运行完整的提供商设置向导
hermes model

# 这允许你：添加提供商、运行 OAuth、输入 API 密钥、配置端点
```

通过 `hermes model` 添加新提供商后，启动新的聊天会话 — `/model` 现在会显示你所有配置的提供商。

:::tip 快速参考
| 你想要... | 使用 |
|-----------|-----|
| 添加新的提供商 | `hermes model`（从终端） |
| 输入/更改 API 密钥 | `hermes model`（从终端） |
| 在会话中切换模型 | `/model <name>`（在会话内） |
| 切换到其他已配置的提供商 | `/model provider:model`（在会话内） |
:::

#### API 密钥无法使用

**原因：** 密钥缺失、已过期、设置不正确，或对应了错误的提供商。

**解决方案：**
```bash
# 检查你的配置
hermes config show

# 重新配置提供商
hermes model

# 或直接设置
hermes config set OPENROUTER_API_KEY sk-or-v1-xxxxxxxxxxxx
```

:::warning
确保密钥与提供商匹配。OpenAI 的密钥不能用于 OpenRouter，反之亦然。检查 `~/.hermes/.env` 中是否有冲突的条目。
:::

#### 模型不可用 / 找不到模型

**原因：** 模型标识符不正确，或该模型在你的提供商上不可用。

**解决方案：**
```bash
# 列出你的提供商可用的模型
hermes model

# 设置一个有效的模型
hermes config set HERMES_MODEL openrouter/nous/hermes-3-llama-3.1-70b

# 或在每次会话中指定
hermes chat --model openrouter/meta-llama/llama-3.1-70b-instruct
```

#### 速率限制（429 错误）

**原因：** 你已超过提供商的速率限制。

**解决方案：** 等待片刻后重试。对于持续使用，请考虑：
- 升级你的提供商计划
- 切换到其他模型或提供商
- 使用 `hermes chat --provider <alternative>` 路由到其他后端

#### 上下文长度超限

**原因：** 对话内容过长，超出了模型的上下文窗口，或者 Hermes 检测到了错误的模型上下文长度。

**解决方案：**
```bash
# 压缩当前会话
/compress

# 或开始新的会话
hermes chat

# 使用具有更大上下文窗口的模型
hermes chat --model openrouter/google/gemini-3-flash-preview
```

如果这在第一次长对话时就发生了，Hermes 可能检测到了错误的模型上下文长度。检查它检测到的值：

查看 CLI 启动行 — 它会显示检测到的上下文长度（例如 `📊 Context limit: 128000 tokens`）。你也可以在会话中使用 `/usage` 查看。

要修复上下文检测，请显式设置：

```yaml
# 在 ~/.hermes/config.yaml 中
model:
  default: your-model-name
  context_length: 131072  # 你的模型实际上下文窗口大小
```

或者对于自定义端点，按模型添加：

```yaml
custom_providers:
  - name: "My Server"
    base_url: "http://localhost:11434/v1"
    models:
      qwen3.5:27b:
        context_length: 32768
```

关于自动检测的工作原理及所有覆盖选项，请参阅[上下文长度检测](../integrations/providers.md#context-length-detection)。

---

### 终端问题

#### 命令被标记为危险并被阻止

**原因：** Hermes 检测到了潜在的破坏性命令（例如 `rm -rf`、`DROP TABLE`）。这是一项安全功能。

**解决方案：** 在提示时，审查命令并输入 `y` 批准执行。你也可以：
- 要求代理使用更安全的替代方案
- 在[安全文档](../user-guide/security.md)中查看危险模式的完整列表

:::tip
这是预期行为 — Hermes 永远不会静默执行破坏性命令。批准提示会向你展示将要执行的确切内容。
:::

#### 通过消息网关无法使用 `sudo`

**原因：** 消息网关在没有交互终端的情况下运行，因此 `sudo` 无法提示输入密码。

**解决方案：**
- 在消息传递中避免使用 `sudo` — 要求代理寻找替代方案
- 如果必须使用 `sudo`，请在 `/etc/sudoers` 中为特定命令配置免密码 sudo
- 或者切换到终端界面执行管理任务：`hermes chat`

#### Docker 后端无法连接

**原因：** Docker 守护进程未运行或用户缺少权限。

**解决方案：**
```bash
# 检查 Docker 是否正在运行
docker info

# 将你的用户添加到 docker 组
sudo usermod -aG docker $USER
newgrp docker

# 验证
docker run hello-world
```

---

### 消息问题

#### 机器人不响应消息

**原因：** 机器人未运行、未授权，或你的用户不在允许列表中。

**解决方案：**
```bash
# 检查网关是否正在运行
hermes gateway status

# 启动网关
hermes gateway start

# 检查日志中的错误
cat ~/.hermes/logs/gateway.log | tail -50
```

#### 消息未送达

**原因：** 网络问题、机器人令牌过期或平台 webhook 配置错误。

**解决方案：**
- 使用 `hermes gateway setup` 验证你的机器人令牌是否有效
- 检查网关日志：`cat ~/.hermes/logs/gateway.log | tail -50`
- 对于基于 webhook 的平台（Slack、WhatsApp），确保你的服务器可以公开访问

#### 允许列表困惑 — 谁能和机器人对话？

**原因：** 授权模式决定了谁可以访问。

**解决方案：**

| 模式 | 工作方式 |
|------|-------------|
| **允许列表** | 只有配置中列出的用户 ID 可以交互 |
| **私聊配对** | 第一个在私聊中发消息的用户获得独占访问权 |
| **开放** | 任何人都可以交互（不建议在生产环境中使用） |

在 `~/.hermes/config.yaml` 中你的网关设置下进行配置。参见[消息文档](../user-guide/messaging/index.md)。

#### 网关无法启动

**原因：** 缺少依赖项、端口冲突或令牌配置错误。

**解决方案：**
```bash
# 安装消息依赖项
pip install "hermes-agent[telegram]"   # 或 [discord]、[slack]、[whatsapp]

# 检查端口冲突
lsof -i :8080

# 验证配置
hermes config show
```

#### WSL：网关持续断开连接或 `hermes gateway start` 失败

**原因：** WSL 的 systemd 支持不够稳定。许多 WSL2 安装未启用 systemd，即使启用了，服务也可能在 WSL 重启或 Windows 空闲关机后无法存活。

**解决方案：** 使用前台模式代替 systemd 服务：

```bash
# 方法 1：直接前台运行（最简单）
hermes gateway run

# 方法 2：通过 tmux 持久化（关闭终端后仍可运行）
tmux new -s hermes 'hermes gateway run'
# 稍后重新连接：tmux attach -t hermes

# 方法 3：通过 nohup 后台运行
nohup hermes gateway run > ~/.hermes/logs/gateway.log 2>&1 &
```

如果你想尝试使用 systemd，请确保已启用：

1. 打开 `/etc/wsl.conf`（如果不存在则创建）
2. 添加：
   ```ini
   [boot]
   systemd=true
   ```
3. 在 PowerShell 中执行：`wsl --shutdown`
4. 重新打开 WSL 终端
5. 验证：`systemctl is-system-running` 应显示 "running" 或 "degraded"

:::tip Windows 开机自启动
要实现可靠的自启动，使用 Windows 任务计划程序在登录时启动 WSL + 网关：
1. 创建一个运行 `wsl -d Ubuntu -- bash -lc 'hermes gateway run'` 的任务
2. 设置为用户登录时触发
:::

#### macOS：网关找不到 Node.js / ffmpeg / 其他工具

**原因：** launchd 服务继承的是最小化 PATH（`/usr/bin:/bin:/usr/sbin:/sbin`），不包含 Homebrew、nvm、cargo 或其他用户安装的工具目录。这通常会导致 WhatsApp 桥接失败（`node not found`）或语音转录失败（`ffmpeg not found`）。

**解决方案：** 网关在你运行 `hermes gateway install` 时会捕获你的 shell PATH。如果你在设置网关后安装了工具，请重新运行安装以捕获更新后的 PATH：

```bash
hermes gateway install    # 重新快照你当前的 PATH
hermes gateway start      # 检测到更新的 plist 并重新加载
```

你可以验证 plist 是否包含正确的 PATH：
```bash
/usr/libexec/PlistBuddy -c "Print :EnvironmentVariables:PATH" \
  ~/Library/LaunchAgents/ai.hermes.gateway.plist
```

---

### 性能问题

#### 响应缓慢

**原因：** 模型过大、API 服务器距离远，或系统提示包含大量工具。

**解决方案：**
- 尝试更快/更小的模型：`hermes chat --model openrouter/meta-llama/llama-3.1-8b-instruct`
- 减少活动工具集：`hermes chat -t "terminal"`
- 检查你与提供商之间的网络延迟
- 对于本地模型，确保你有足够的 GPU 显存

#### Token 使用量高

**原因：** 对话过长、系统提示词冗长，或大量工具调用累积上下文。

**解决方案：**
```bash
# 压缩对话以减少 token 用量
/compress

# 检查会话 token 使用情况
/usage
```

:::tip
在长会话中定期使用 `/compress`。它会总结对话历史并显著减少 token 使用量，同时保留上下文。
:::

#### 会话过长

**原因：** 持续的对话会累积消息和工具输出，逐渐接近上下文限制。

**解决方案：**
```bash
# 压缩当前会话（保留关键上下文）
/compress

# 开始新会话并引用旧会话
hermes chat

# 如果需要，稍后恢复特定会话
hermes chat --continue
```

---

### MCP 问题

#### MCP 服务器无法连接

**原因：** 找不到服务器二进制文件、命令路径错误或缺少运行时。

**解决方案：**
```bash
# 确保 MCP 依赖已安装（标准安装中已包含）
cd ~/.hermes/hermes-agent && uv pip install -e ".[mcp]"

# 对于基于 npm 的服务器，确保 Node.js 可用
node --version
npx --version

# 手动测试服务器
npx -y @modelcontextprotocol/server-filesystem /tmp
```

验证你的 `~/.hermes/config.yaml` MCP 配置：
```yaml
mcp_servers:
  filesystem:
    command: "npx"
    args: ["-y", "@modelcontextprotocol/server-filesystem", "/home/user/docs"]
```

#### MCP 服务器的工具未显示

**原因：** 服务器已启动但工具发现失败，工具被配置过滤掉了，或者服务器不支持你期望的 MCP 功能。

**解决方案：**
- 检查网关/代理日志中的 MCP 连接错误
- 确保服务器响应 `tools/list` RPC 方法
- 检查该服务器下的 `tools.include`、`tools.exclude`、`tools.resources`、`tools.prompts` 或 `enabled` 设置
- 请注意，资源/提示工具仅在会话实际支持这些功能时才会注册
- 更改配置后使用 `/reload-mcp`

```bash
# 验证 MCP 服务器已配置
hermes config show | grep -A 12 mcp_servers

# 更改配置后重启 Hermes 或重新加载 MCP
hermes chat
```

另请参阅：
- [MCP（模型上下文协议）](/docs/user-guide/features/mcp)
- [在 Hermes 中使用 MCP](/docs/guides/use-mcp-with-hermes)
- [MCP 配置参考](/docs/reference/mcp-config-reference)

#### MCP 超时错误

**原因：** MCP 服务器响应时间过长，或在执行过程中崩溃。

**解决方案：**
- 如果支持，增加 MCP 服务器配置中的超时时间
- 检查 MCP 服务器进程是否仍在运行
- 对于远程 HTTP MCP 服务器，检查网络连接

:::warning
如果 MCP 服务器在请求过程中崩溃，Hermes 会报告超时。检查服务器自身的日志（不仅仅是 Hermes 日志）来诊断根本原因。
:::

---

## 配置文件（Profiles）

### 配置文件与直接设置 HERMES_HOME 有什么区别？

配置文件是 `HERMES_HOME` 之上的一个管理层。你*可以*在每个命令之前手动设置 `HERMES_HOME=/some/path`，但配置文件为你处理了所有细节：创建目录结构、生成 shell 别名（`hermes-work`）、在 `~/.hermes/active_profile` 中跟踪活动配置文件，以及自动在所有配置文件之间同步技能更新。它们还集成了 Tab 补全，所以你不需要记住路径。

### 两个配置文件可以共享同一个机器人令牌吗？

不可以。每个消息平台（Telegram、Discord 等）需要对机器人令牌的独占访问。如果两个配置文件尝试同时使用同一个令牌，第二个网关将无法连接。请为每个配置文件创建单独的机器人 — 对于 Telegram，与 [@BotFather](https://t.me/BotFather) 对话创建额外的机器人。

### 配置文件之间共享记忆或会话吗？

不共享。每个配置文件有自己的记忆存储、会话数据库和技能目录。它们完全隔离。如果你想用现有的记忆和会话创建新的配置文件，请使用 `hermes profile create newname --clone-all` 从当前配置文件复制所有内容。

### 运行 `hermes update` 时会发生什么？

`hermes update` 会拉取最新代码并重新安装依赖项**一次**（不是按配置文件分别安装）。然后它会自动将更新的技能同步到所有配置文件。你只需要运行一次 `hermes update` — 它会覆盖机器上的所有配置文件。

### 可以将配置文件迁移到另一台机器吗？

可以。将配置文件导出为便携式归档文件，然后在另一台机器上导入：

```bash
# 在源机器上
hermes profile export work ./work-backup.tar.gz

# 将文件复制到目标机器，然后：
hermes profile import ./work-backup.tar.gz work
```

导入的配置文件将包含导出时的所有配置、记忆、会话和技能。如果新机器有不同的设置，你可能需要更新路径或重新向提供商进行身份验证。

### 可以运行多少个配置文件？

没有硬性限制。每个配置文件只是 `~/.hermes/profiles/` 下的一个目录。实际限制取决于你的磁盘空间以及系统可以处理多少个并发网关（每个网关是一个轻量级 Python 进程）。运行数十个配置文件是没问题的；每个空闲的配置文件不消耗任何资源。

---

## 工作流与模式

### 为不同任务使用不同模型（多模型工作流）

**场景：** 你日常使用 GPT-5.4，但 Gemini 或 Grok 写社交媒体内容更好。每次手动切换模型很麻烦。

**解决方案：委派配置。** Hermes 可以自动将子代理路由到不同的模型。在 `~/.hermes/config.yaml` 中设置：

```yaml
delegation:
  model: "google/gemini-3-flash-preview"   # 子代理使用此模型
  provider: "openrouter"                    # 子代理的提供商
```

现在当你告诉 Hermes"帮我写一个关于 X 的 Twitter 帖子串"，它生成一个 `delegate_task` 子代理时，该子代理会在 Gemini 上运行而不是你的主模型。你的主对话仍然使用 GPT-5.4。

你也可以在提示中明确说明：*"委派一个任务来撰写关于我们产品发布的社交媒体帖子。使用你的子代理来实际撰写。"* 代理将使用 `delegate_task`，它会自动采用委派配置。

如果不需要委派而只想临时切换模型，请在 CLI 中使用 `/model`：

```bash
/model google/gemini-3-flash-preview    # 在本次会话中切换
# ... 撰写你的内容 ...
/model openai/gpt-5.4                   # 切换回来
```

关于委派工作原理的更多信息，请参阅[子代理委派](../user-guide/features/delegation.md)。

### 在一个 WhatsApp 号码上运行多个代理（按聊天绑定）

**场景：** 在 OpenClaw 中，你有多个独立的代理绑定到特定的 WhatsApp 聊天 — 一个用于家庭购物清单群组，另一个用于你的私聊。Hermes 可以做到这一点吗？

**当前限制：** Hermes 的每个配置文件都需要自己的 WhatsApp 号码/会话。你无法将多个配置文件绑定到同一个 WhatsApp 号码的不同聊天 — WhatsApp 桥接（Baileys）每个号码使用一个已认证的会话。

**替代方案：**

1. **使用单个配置文件配合人格切换。** 创建不同的 `AGENTS.md` 上下文文件或使用 `/personality` 命令来改变每个聊天的行为。代理能看到它在哪个聊天中并可以做出相应调整。

2. **使用定时任务处理特定任务。** 对于购物清单跟踪器，设置一个定时任务来监控特定聊天并管理清单 — 不需要单独的代理。

3. **使用不同的号码。** 如果你需要真正独立的代理，请为每个配置文件配备自己的 WhatsApp 号码。来自 Google Voice 等服务的虚拟号码可以用于此目的。

4. **使用 Telegram 或 Discord 替代。** 这些平台更自然地支持按聊天绑定 — 每个 Telegram 群组或 Discord 频道都有自己的会话，你可以在同一个账户上运行多个机器人令牌（每个配置文件一个）。

更多详情请参阅[配置文件](../user-guide/profiles.md)和 [WhatsApp 设置](../user-guide/messaging/whatsapp.md)。

### 控制 Telegram 中显示的内容（隐藏日志和推理过程）

**场景：** 你在 Telegram 中看到了网关执行日志、Hermes 推理过程和工具调用详情，而不是仅显示最终输出。

**解决方案：** `config.yaml` 中的 `display.tool_progress` 设置控制显示多少工具活动：

```yaml
display:
  tool_progress: "off"   # 选项：off, new, all, verbose
```

- **`off`** — 仅显示最终响应。不显示工具调用、推理过程或日志。
- **`new`** — 显示新的工具调用（简短的单行信息）。
- **`all`** — 显示所有工具活动，包括结果。
- **`verbose`** — 完整详情，包括工具参数和输出。

对于消息平台，通常 `off` 或 `new` 是你想要的。编辑 `config.yaml` 后，重启网关以使更改生效。

你也可以使用 `/verbose` 命令在每个会话中切换（如果已启用）：

```yaml
display:
  tool_progress_command: true   # 在网关中启用 /verbose
```

### 在 Telegram 上管理技能（斜杠命令限制）

**场景：** Telegram 有 100 个斜杠命令的限制，而你的技能数量已经超过了。你想在 Telegram 上禁用不需要的技能，但 `hermes skills config` 的设置似乎没有生效。

**解决方案：** 使用 `hermes skills config` 按平台禁用技能。这会写入 `config.yaml`：

```yaml
skills:
  disabled: []                    # 全局禁用的技能
  platform_disabled:
    telegram: [skill-a, skill-b]  # 仅在 telegram 上禁用
```

更改后，**重启网关**（`hermes gateway restart` 或杀掉并重新启动）。Telegram 机器人命令菜单在启动时重新构建。

:::tip
描述过长的技能在 Telegram 菜单中会被截断为 40 个字符，以保持在有效载荷大小限制之内。如果技能没有出现，可能是总有效载荷大小问题而不是 100 个命令计数限制 — 禁用未使用的技能对两者都有帮助。
:::

### 共享线程会话（多用户，一个对话）

**场景：** 你有一个 Telegram 或 Discord 线程，多人在其中 @机器人。你希望该线程中的所有 @ 都属于一个共享对话，而不是每个用户独立的会话。

**当前行为：** 在大多数平台上，Hermes 按用户 ID 创建会话，因此每个人都有自己的对话上下文。这是为了隐私和上下文隔离而设计的。

**替代方案：**

1. **使用 Slack。** Slack 的会话按线程而非按用户关联。同一线程中的多个用户共享一个对话 — 这正是你描述的行为。这是最自然的选择。

2. **使用群聊中的单一用户。** 如果一个人是指定的"操作员"来转达问题，会话保持统一。其他人可以旁观。

3. **使用 Discord 频道。** Discord 的会话按频道关联，因此同一频道中的所有用户共享上下文。使用专用频道进行共享对话。

### 将 Hermes 导出到另一台机器

**场景：** 你已经在一台机器上积累了技能、定时任务和记忆，想把所有内容迁移到一台新的专用 Linux 服务器上。

**解决方案：**

1. 在新机器上安装 Hermes Agent：
   ```bash
   curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash
   ```

2. 复制整个 `~/.hermes/` 目录，**但排除** `hermes-agent` 子目录（那是代码仓库 — 新安装会有自己的）：
   ```bash
   # 在源机器上
   rsync -av --exclude='hermes-agent' ~/.hermes/ newmachine:~/.hermes/
   ```

   或者使用配置文件导出/导入：
   ```bash
   # 在源机器上
   hermes profile export default ./hermes-backup.tar.gz

   # 在目标机器上
   hermes profile import ./hermes-backup.tar.gz default
   ```

3. 在新机器上，运行 `hermes setup` 验证 API 密钥和提供商配置是否正常工作。重新认证所有消息平台（特别是使用二维码配对的 WhatsApp）。

`~/.hermes/` 目录包含所有内容：`config.yaml`、`.env`、`SOUL.md`、`memories/`、`skills/`、`state.db`（会话）、`cron/` 以及任何自定义插件。代码本身位于 `~/.hermes/hermes-agent/` 中，是全新安装的。

### 安装后重新加载 shell 时出现权限拒绝

**场景：** 运行 Hermes 安装程序后，`source ~/.zshrc` 出现权限拒绝错误。

**原因：** 这通常发生在 `~/.zshrc`（或 `~/.bashrc`）文件权限不正确，或安装程序无法正确写入时。这不是 Hermes 特有的问题 — 而是 shell 配置文件的权限问题。

**解决方案：**
```bash
# 检查权限
ls -la ~/.zshrc

# 如果需要修复（应为 -rw-r--r-- 或 644）
chmod 644 ~/.zshrc

# 然后重新加载
source ~/.zshrc

# 或者直接打开一个新的终端窗口 — 它会自动获取 PATH 更改
```

如果安装程序添加了 PATH 行但权限有误，你可以手动添加：
```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
```

### 首次运行代理时出现 400 错误

**场景：** 设置顺利完成，但首次聊天尝试失败，出现 HTTP 400 错误。

**原因：** 通常是模型名称不匹配 — 配置的模型在你的提供商上不存在，或 API 密钥没有该模型的访问权限。

**解决方案：**
```bash
# 检查配置的模型和提供商
hermes config show | head -20

# 重新运行模型选择
hermes model

# 或使用已知可用的模型测试
hermes chat -q "hello" --model anthropic/claude-sonnet-4.6
```

如果使用 OpenRouter，请确保你的 API 密钥有余额。OpenRouter 返回 400 通常意味着该模型需要付费计划或模型 ID 有拼写错误。

---

## 仍然遇到问题？

如果你的问题未在此处涵盖：

1. **搜索现有 issue：** [GitHub Issues](https://github.com/NousResearch/hermes-agent/issues)
2. **向社区提问：** [Nous Research Discord](https://discord.gg/nousresearch)
3. **提交 bug 报告：** 包含你的操作系统、Python 版本（`python3 --version`）、Hermes 版本（`hermes --version`）以及完整的错误信息
