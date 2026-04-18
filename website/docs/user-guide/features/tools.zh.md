---
sidebar_position: 1
title: "工具与工具集"
description: "Hermes Agent 工具概览 — 可用工具、工具集工作原理及终端后端"
---

# 工具与工具集

工具是扩展代理能力的函数。它们被组织为逻辑**工具集**，可按平台启用或禁用。

## 可用工具

Hermes 内置了广泛的工具注册表，涵盖网页搜索、浏览器自动化、终端执行、文件编辑、记忆、委派、RL 训练、消息投递、Home Assistant 等。

:::note
**Honcho 跨会话记忆**作为记忆服务商插件提供（`plugins/memory/honcho/`），而非内置工具集。安装方法请参阅[插件](./plugins.md)。
:::

高级分类：

| 分类 | 示例 | 描述 |
|----------|----------|-------------|
| **网页** | `web_search`、`web_extract` | 搜索网页并提取页面内容。 |
| **终端与文件** | `terminal`、`process`、`read_file`、`patch` | 执行命令和操作文件。 |
| **浏览器** | `browser_navigate`、`browser_snapshot`、`browser_vision` | 支持文本和视觉的交互式浏览器自动化。 |
| **媒体** | `vision_analyze`、`image_generate`、`text_to_speech` | 多模态分析和生成。 |
| **代理编排** | `todo`、`clarify`、`execute_code`、`delegate_task` | 规划、澄清、代码执行和子代理委派。 |
| **记忆与回忆** | `memory`、`session_search` | 持久化记忆和会话搜索。 |
| **自动化与投递** | `cronjob`、`send_message` | 支持创建/列表/更新/暂停/恢复/运行/删除操作的定时任务，以及出站消息投递。 |
| **集成** | `ha_*`、MCP 服务器工具、`rl_*` | Home Assistant、MCP、RL 训练及其他集成。 |

有关权威的代码派生注册表，请参阅[内置工具参考](/docs/reference/tools-reference)和[工具集参考](/docs/reference/toolsets-reference)。

:::tip Nous 工具网关
付费 [Nous Portal](https://portal.nousresearch.com) 订阅用户可以通过 **[工具网关](tool-gateway.md)** 使用网页搜索、图像生成、TTS 和浏览器自动化 — 无需额外的 API 密钥。运行 `hermes model` 启用它，或使用 `hermes tools` 配置各个工具。
:::

## 使用工具集

```bash
# 使用特定工具集
hermes chat --toolsets "web,terminal"

# 查看所有可用工具
hermes tools

# 按平台配置工具（交互式）
hermes tools
```

常用工具集包括 `web`、`terminal`、`file`、`browser`、`vision`、`image_gen`、`moa`、`skills`、`tts`、`todo`、`memory`、`session_search`、`cronjob`、`code_execution`、`delegation`、`clarify`、`homeassistant` 和 `rl`。

完整工具集（包括 `hermes-cli`、`hermes-telegram` 等平台预设和 `mcp-<server>` 等动态 MCP 工具集）请参阅[工具集参考](/docs/reference/toolsets-reference)。

## 终端后端

终端工具可以在不同环境中执行命令：

| 后端 | 描述 | 用途 |
|---------|-------------|----------|
| `local` | 在本机运行（默认） | 开发、受信任的任务 |
| `docker` | 隔离容器 | 安全性、可重现性 |
| `ssh` | 远程服务器 | 沙盒化，使代理远离自身代码 |
| `singularity` | HPC 容器 | 集群计算、无 root |
| `modal` | 云端执行 | 无服务器、可扩展 |
| `daytona` | 云沙盒工作区 | 持久化远程开发环境 |

### 配置

```yaml
# 在 ~/.hermes/config.yaml 中
terminal:
  backend: local    # 或：docker、ssh、singularity、modal、daytona
  cwd: "."          # 工作目录
  timeout: 180      # 命令超时时间（秒）
```

### Docker 后端

```yaml
terminal:
  backend: docker
  docker_image: python:3.11-slim
```

### SSH 后端

推荐用于安全性 — 代理无法修改自身代码：

```yaml
terminal:
  backend: ssh
```
```bash
# 在 ~/.hermes/.env 中设置凭据
TERMINAL_SSH_HOST=my-server.example.com
TERMINAL_SSH_USER=myuser
TERMINAL_SSH_KEY=~/.ssh/id_rsa
```

### Singularity/Apptainer

```bash
# 为并行 worker 预构建 SIF
apptainer build ~/python.sif docker://python:3.11-slim

# 配置
hermes config set terminal.backend singularity
hermes config set terminal.singularity_image ~/python.sif
```

### Modal（无服务器云）

```bash
uv pip install modal
modal setup
hermes config set terminal.backend modal
```

### 容器资源

为所有容器后端配置 CPU、内存、磁盘和持久化：

```yaml
terminal:
  backend: docker  # 或 singularity、modal、daytona
  container_cpu: 1              # CPU 核数（默认：1）
  container_memory: 5120        # 内存，MB（默认：5GB）
  container_disk: 51200         # 磁盘，MB（默认：50GB）
  container_persistent: true    # 跨会话持久化文件系统（默认：true）
```

当 `container_persistent: true` 时，已安装的包、文件和配置在会话间保留。

### 容器安全

所有容器后端都以安全强化模式运行：

- 只读根文件系统（Docker）
- 丢弃所有 Linux 能力
- 无特权提升
- PID 限制（256 个进程）
- 完整的命名空间隔离
- 通过卷实现持久化工作区，而非可写的根层

Docker 可以通过 `terminal.docker_forward_env` 选择性地接收显式的环境变量白名单，但转发的变量在容器内对命令可见，应视为对该会话公开。

## 后台进程管理

启动后台进程并管理它们：

```python
terminal(command="pytest -v tests/", background=true)
# 返回：{"session_id": "proc_abc123", "pid": 12345}

# 然后使用 process 工具管理：
process(action="list")       # 显示所有运行中的进程
process(action="poll", session_id="proc_abc123")   # 检查状态
process(action="wait", session_id="proc_abc123")   # 阻塞等待完成
process(action="log", session_id="proc_abc123")    # 完整输出
process(action="kill", session_id="proc_abc123")   # 终止
process(action="write", session_id="proc_abc123", data="y")  # 发送输入
```

PTY 模式（`pty=true`）支持 Codex 和 Claude Code 等交互式 CLI 工具。

## Sudo 支持

如果命令需要 sudo，系统会提示你输入密码（在会话期间缓存）。或者在 `~/.hermes/.env` 中设置 `SUDO_PASSWORD`。

:::warning
在消息平台上，如果 sudo 失败，输出会包含一个提示，建议将 `SUDO_PASSWORD` 添加到 `~/.hermes/.env`。
:::
