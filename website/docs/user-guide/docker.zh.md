---
sidebar_position: 7
title: "Docker"
description: "在 Docker 中运行 Hermes Agent 以及使用 Docker 作为终端后端"
---

# Hermes Agent — Docker

Docker 与 Hermes Agent 有两种不同的交互方式：

1. **在 Docker 中运行 Hermes** —— 智能体本身在容器内运行（本页的主要内容）
2. **Docker 作为终端后端** —— 智能体在主机上运行，但在 Docker 沙箱内执行命令（见 [配置 → terminal.backend](./configuration.md)）

本页涵盖选项 1。容器将所有用户数据（配置、API 密钥、会话、技能、记忆）存储在从主机挂载到 `/opt/data` 的单个目录中。镜像本身是无状态的，可以通过拉取新版本升级而不会丢失任何配置。

## 快速开始

如果这是你第一次运行 Hermes Agent，在主机上创建数据目录并以交互方式启动容器来运行设置向导：

```sh
mkdir -p ~/.hermes
docker run -it --rm \
  -v ~/.hermes:/opt/data \
  nousresearch/hermes-agent setup
```

这会进入设置向导，提示你输入 API 密钥并写入 `~/.hermes/.env`。你只需要做一次。强烈建议此时为网关设置一个聊天系统。

## 以网关模式运行

配置完成后，以后台方式运行容器作为持久网关（Telegram、Discord、Slack、WhatsApp 等）：

```sh
docker run -d \
  --name hermes \
  --restart unless-stopped \
  -v ~/.hermes:/opt/data \
  -p 8642:8642 \
  nousresearch/hermes-agent gateway run
```

端口 8642 暴露网关的 [OpenAI 兼容 API 服务器](./api-server.md)和健康端点。如果你只使用聊天平台（Telegram、Discord 等），它是可选的，但如果你想要仪表盘或外部工具访问网关则是必需的。

在面向互联网的机器上开放任何端口都有安全风险。除非你了解风险，否则不应这样做。

## 运行仪表盘

内置的 Web 仪表盘可以作为单独的容器与网关一起运行。

要将仪表盘作为自己的容器运行，将其指向网关的健康端点，以便它可以跨容器检测网关状态：

```sh
docker run -d \
  --name hermes-dashboard \
  --restart unless-stopped \
  -v ~/.hermes:/opt/data \
  -p 9119:9119 \
  -e GATEWAY_HEALTH_URL=http://$HOST_IP:8642 \
  nousresearch/hermes-agent dashboard
```

将 `$HOST_IP` 替换为运行网关容器的机器的 IP 地址（例如 `192.168.1.100`），或者如果两个容器共享同一网络则使用 Docker 网络主机名（见下面的 [Compose 示例](#docker-compose-example)）。

| 环境变量 | 描述 | 默认值 |
|---------------------|-------------|---------|
| `GATEWAY_HEALTH_URL` | 网关 API 服务器的基础 URL，例如 `http://gateway:8642` | *（未设置——仅本地 PID 检查）* |
| `GATEWAY_HEALTH_TIMEOUT` | 健康探测超时（秒） | `3` |

没有 `GATEWAY_HEALTH_URL` 时，仪表盘回退到本地进程检测——这只在网关运行在同一容器或同一主机上时有效。

## 交互式运行（CLI 聊天）

要对正在运行的数据目录打开交互式聊天会话：

```sh
docker run -it --rm \
  -v ~/.hermes:/opt/data \
  nousresearch/hermes-agent
```

## 持久化卷

`/opt/data` 卷是所有 Hermes 状态的唯一真实来源。它映射到主机的 `~/.hermes/` 目录，包含：

| 路径 | 内容 |
|------|----------|
| `.env` | API 密钥和机密 |
| `config.yaml` | 所有 Hermes 配置 |
| `SOUL.md` | 智能体个性/身份 |
| `sessions/` | 对话历史 |
| `memories/` | 持久化记忆存储 |
| `skills/` | 已安装的技能 |
| `cron/` | 定时任务定义 |
| `hooks/` | 事件钩子 |
| `logs/` | 运行时日志 |
| `skins/` | 自定义 CLI 皮肤 |

:::warning
永远不要同时对同一数据目录运行两个 Hermes **网关**容器——会话文件和记忆存储不是为并发写入设计的。在网关旁运行仪表盘容器是安全的，因为仪表盘只读取数据。
:::

## 环境变量转发

API 密钥从容器内的 `/opt/data/.env` 读取。你也可以直接传递环境变量：

```sh
docker run -it --rm \
  -v ~/.hermes:/opt/data \
  -e ANTHROPIC_API_KEY="sk-ant-..." \
  -e OPENAI_API_KEY="sk-..." \
  nousresearch/hermes-agent
```

直接的 `-e` 标志覆盖 `.env` 中的值。这对于不想将密钥存储在磁盘上的 CI/CD 或密钥管理器集成很有用。

## Docker Compose 示例

对于同时包含网关和仪表盘的持久化部署，`docker-compose.yaml` 很方便：

```yaml
services:
  hermes:
    image: nousresearch/hermes-agent:latest
    container_name: hermes
    restart: unless-stopped
    command: gateway run
    ports:
      - "8642:8642"
    volumes:
      - ~/.hermes:/opt/data
    networks:
      - hermes-net
    # 取消注释以转发特定环境变量而不是使用 .env 文件：
    # environment:
    #   - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
    #   - OPENAI_API_KEY=${OPENAI_API_KEY}
    #   - TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN}
    deploy:
      resources:
        limits:
          memory: 4G
          cpus: "2.0"

  dashboard:
    image: nousresearch/hermes-agent:latest
    container_name: hermes-dashboard
    restart: unless-stopped
    command: dashboard --host 0.0.0.0
    ports:
      - "9119:9119"
    volumes:
      - ~/.hermes:/opt/data
    environment:
      - GATEWAY_HEALTH_URL=http://hermes:8642
    networks:
      - hermes-net
    depends_on:
      - hermes
    deploy:
      resources:
        limits:
          memory: 512M
          cpus: "0.5"

networks:
  hermes-net:
    driver: bridge
```

使用 `docker compose up -d` 启动，使用 `docker compose logs -f` 查看日志。

## 资源限制

Hermes 容器需要适度的资源。推荐最低配置：

| 资源 | 最低 | 推荐 |
|----------|---------|-------------|
| 内存 | 1 GB | 2-4 GB |
| CPU | 1 核 | 2 核 |
| 磁盘（数据卷） | 500 MB | 2+ GB（随会话/技能增长） |

浏览器自动化（Playwright/Chromium）是最耗内存的功能。如果你不需要浏览器工具，1 GB 就足够了。启用浏览器工具时，至少分配 2 GB。

在 Docker 中设置限制：

```sh
docker run -d \
  --name hermes \
  --restart unless-stopped \
  --memory=4g --cpus=2 \
  -v ~/.hermes:/opt/data \
  nousresearch/hermes-agent gateway run
```

## Dockerfile 做了什么

官方镜像基于 `debian:13.4`，包含：

- Python 3 及所有 Hermes 依赖（`pip install -e ".[all]"`）
- Node.js + npm（用于浏览器自动化和 WhatsApp 桥接）
- Playwright 及 Chromium（`npx playwright install --with-deps chromium`）
- ripgrep 和 ffmpeg 作为系统工具
- WhatsApp 桥接（`scripts/whatsapp-bridge/`）

入口脚本（`docker/entrypoint.sh`）在首次运行时引导数据卷：
- 创建目录结构（`sessions/`、`memories/`、`skills/` 等）
- 如果没有 `.env` 则复制 `.env.example` → `.env`
- 如果缺少则复制默认 `config.yaml`
- 如果缺少则复制默认 `SOUL.md`
- 使用基于清单的方法同步打包技能（保留用户编辑）
- 然后用你传递的参数运行 `hermes`

## 升级

拉取最新镜像并重新创建容器。你的数据目录不受影响。

```sh
docker pull nousresearch/hermes-agent:latest
docker rm -f hermes
docker run -d \
  --name hermes \
  --restart unless-stopped \
  -v ~/.hermes:/opt/data \
  nousresearch/hermes-agent gateway run
```

或使用 Docker Compose：

```sh
docker compose pull
docker compose up -d
```

## 技能和凭据文件

使用 Docker 作为执行环境时（不是上述方法，而是当智能体在 Docker 沙箱内运行命令时），Hermes 自动将技能目录（`~/.hermes/skills/`）和技能声明的任何凭据文件作为只读卷绑定挂载到容器中。这意味着技能脚本、模板和引用在沙箱内无需手动配置即可使用。

SSH 和 Modal 后端也进行相同的同步——技能和凭据文件在每个命令之前通过 rsync 或 Modal 挂载 API 上传。

## 故障排除

### 容器立即退出

检查日志：`docker logs hermes`。常见原因：
- `.env` 文件缺失或无效——先以交互方式运行完成设置
- 使用暴露端口时的端口冲突

### "Permission denied" 错误

容器默认以 root 运行。如果你的主机 `~/.hermes/` 由非 root 用户创建，权限应该正常工作。如果出现错误，确保数据目录可写：

```sh
chmod -R 755 ~/.hermes
```

### 浏览器工具不工作

Playwright 需要共享内存。在 Docker run 命令中添加 `--shm-size=1g`：

```sh
docker run -d \
  --name hermes \
  --shm-size=1g \
  -v ~/.hermes:/opt/data \
  nousresearch/hermes-agent gateway run
```

### 网络问题后网关不重连

`--restart unless-stopped` 标志处理大多数瞬态故障。如果网关卡住，重启容器：

```sh
docker restart hermes
```

### 检查容器健康状态

```sh
docker logs --tail 50 hermes          # 最近日志
docker run -it --rm nousresearch/hermes-agent:latest version     # 验证版本
docker stats hermes                    # 资源使用
```
