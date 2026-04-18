---
sidebar_position: 9
title: "Matrix"
description: "将 Hermes Agent 设置为 Matrix 机器人"
---

# Matrix 设置

Hermes Agent 与 Matrix 集成，Matrix 是一种开放的联邦制消息协议。Matrix 让你可以运行自己的主服务器或使用公共服务器（如 matrix.org）—— 无论哪种方式，你都能控制自己的通信。机器人通过 `mautrix` Python SDK 连接，通过 Hermes Agent 管道（包括工具使用、记忆和推理）处理消息，并实时回复。它支持文本、文件附件、图片、音频、视频以及可选的端到端加密（E2EE）。

Hermes 可与任何 Matrix 主服务器配合使用 —— Synapse、Conduit、Dendrite 或 matrix.org。

在开始设置之前，先了解大多数人最想知道的部分：连接后 Hermes 的行为方式。

## Hermes 的行为方式

| 场景 | 行为 |
|---------|----------|
| **私聊** | Hermes 回复每条消息。无需 `@提及`。每个私聊有自己的会话。设置 `MATRIX_DM_MENTION_THREADS=true` 可在私聊中被 `@提及` 时创建主题帖。 |
| **房间** | 默认情况下，Hermes 需要 `@提及` 才会回复。设置 `MATRIX_REQUIRE_MENTION=false` 或将房间 ID 添加到 `MATRIX_FREE_RESPONSE_ROOMS` 以启用自由回复。房间邀请会自动接受。 |
| **主题帖** | Hermes 支持 Matrix 主题帖（MSC3440）。如果你在主题帖中回复，Hermes 会将主题帖上下文与主房间时间线隔离。机器人已参与的主题帖不需要提及。 |
| **自动创建主题帖** | 默认情况下，Hermes 在房间中回复消息时会自动创建主题帖。这保持了对话的隔离性。设置 `MATRIX_AUTO_THREAD=false` 可禁用此功能。 |
| **多用户共享房间** | 默认情况下，Hermes 在房间内按用户隔离会话历史。两个人在同一房间聊天不会共享一个对话记录，除非你明确禁用此功能。 |

:::tip
机器人在被邀请时会自动加入房间。只需邀请机器人的 Matrix 用户到任何房间，它就会加入并开始回复。
:::

### Matrix 中的会话模型

默认情况下：

- 每个私聊有自己的会话
- 每个主题帖有自己的会话命名空间
- 共享房间中的每个用户在该房间内有自己的会话

这由 `config.yaml` 控制：

```yaml
group_sessions_per_user: true
```

仅当你明确需要整个房间共享一个对话时，才将其设为 `false`：

```yaml
group_sessions_per_user: false
```

共享会话可用于协作房间，但这也意味着：

- 用户共享上下文增长和令牌成本
- 一个人的长时间工具密集型任务可能会膨胀其他人的上下文
- 一个人的进行中任务可能会中断另一个人在同一房间中的后续操作

### 提及和主题帖配置

你可以通过环境变量或 `config.yaml` 配置提及和自动主题帖行为：

```yaml
matrix:
  require_mention: true           # 在房间中需要 @提及（默认：true）
  free_response_rooms:            # 免除提及要求的房间
    - "!abc123:matrix.org"
  auto_thread: true               # 自动为回复创建主题帖（默认：true）
  dm_mention_threads: false       # 在私聊中被 @提及时创建主题帖（默认：false）
```

或通过环境变量：

```bash
MATRIX_REQUIRE_MENTION=true
MATRIX_FREE_RESPONSE_ROOMS=!abc123:matrix.org,!def456:matrix.org
MATRIX_AUTO_THREAD=true
MATRIX_DM_MENTION_THREADS=false
```

:::note
如果你从没有 `MATRIX_REQUIRE_MENTION` 的版本升级，机器人之前会回复房间中的所有消息。要保留该行为，请设置 `MATRIX_REQUIRE_MENTION=false`。
:::

本指南将引导你完成从创建机器人帐户到发送第一条消息的完整设置过程。

## 步骤 1：创建机器人帐户

你需要一个 Matrix 用户帐户作为机器人。有多种方式：

### 方式 A：在你的主服务器上注册（推荐）

如果你运行自己的主服务器（Synapse、Conduit、Dendrite）：

1. 使用管理员 API 或注册工具创建新用户：

```bash
# Synapse 示例
register_new_matrix_user -c /etc/synapse/homeserver.yaml http://localhost:8008
```

2. 选择一个用户名，如 `hermes` —— 完整的用户 ID 将是 `@hermes:your-server.org`。

### 方式 B：使用 matrix.org 或其他公共主服务器

1. 前往 [Element Web](https://app.element.io) 并创建新帐户。
2. 为你的机器人选择一个用户名（例如 `hermes-bot`）。

### 方式 C：使用你自己的帐户

你也可以将 Hermes 作为你自己的用户运行。这意味着机器人以你的身份发帖 —— 适用于个人助理。

## 步骤 2：获取访问令牌

Hermes 需要访问令牌来与主服务器进行身份验证。你有两个选择：

### 方式 A：访问令牌（推荐）

获取令牌最可靠的方式：

**通过 Element：**
1. 使用机器人帐户登录 [Element](https://app.element.io)。
2. 进入 **Settings** → **Help & About**。
3. 向下滚动并展开 **Advanced** —— 访问令牌显示在那里。
4. **立即复制。**

**通过 API：**

```bash
curl -X POST https://your-server/_matrix/client/v3/login \
  -H "Content-Type: application/json" \
  -d '{
    "type": "m.login.password",
    "user": "@hermes:your-server.org",
    "password": "your-password"
  }'
```

响应包含一个 `access_token` 字段 —— 复制它。

:::warning[保管好你的访问令牌]
访问令牌可以完全访问机器人的 Matrix 帐户。切勿公开分享或提交到 Git。如果泄露，通过注销该用户的所有会话来撤销它。
:::

### 方式 B：密码登录

不提供访问令牌，你可以给 Hermes 提供机器人的用户 ID 和密码。Hermes 将在启动时自动登录。这更简单，但意味着密码存储在你的 `.env` 文件中。

```bash
MATRIX_USER_ID=@hermes:your-server.org
MATRIX_PASSWORD=your-password
```

## 步骤 3：查找你的 Matrix 用户 ID

Hermes Agent 使用你的 Matrix 用户 ID 来控制谁可以与机器人交互。Matrix 用户 ID 的格式为 `@username:server`。

查找方法：

1. 打开 [Element](https://app.element.io)（或你首选的 Matrix 客户端）。
2. 点击你的头像 → **Settings**。
3. 你的用户 ID 显示在个人资料顶部（例如 `@alice:matrix.org`）。

:::tip
Matrix 用户 ID 始终以 `@` 开头，并包含 `:` 后跟服务器名称。例如：`@alice:matrix.org`、`@bob:your-server.com`。
:::

## 步骤 4：配置 Hermes Agent

### 方式 A：交互式设置（推荐）

运行引导式设置命令：

```bash
hermes gateway setup
```

出现提示时选择 **Matrix**，然后提供你的主服务器 URL、访问令牌（或用户 ID + 密码）以及允许的用户 ID。

### 方式 B：手动配置

在你的 `~/.hermes/.env` 文件中添加以下内容：

**使用访问令牌：**

```bash
# 必填
MATRIX_HOMESERVER=https://matrix.example.org
MATRIX_ACCESS_TOKEN=***

# 可选：用户 ID（如果省略则从令牌自动检测）
# MATRIX_USER_ID=@hermes:matrix.example.org

# 安全：限制谁可以与机器人交互
MATRIX_ALLOWED_USERS=@alice:matrix.example.org

# 多个允许的用户（逗号分隔）
# MATRIX_ALLOWED_USERS=@alice:matrix.example.org,@bob:matrix.example.org
```

**使用密码登录：**

```bash
# 必填
MATRIX_HOMESERVER=https://matrix.example.org
MATRIX_USER_ID=@hermes:matrix.example.org
MATRIX_PASSWORD=***

# 安全
MATRIX_ALLOWED_USERS=@alice:matrix.example.org
```

`~/.hermes/config.yaml` 中的可选行为设置：

```yaml
group_sessions_per_user: true
```

- `group_sessions_per_user: true` 在共享房间内保持每个参与者的上下文隔离

### 启动网关

配置完成后，启动 Matrix 网关：

```bash
hermes gateway
```

机器人应在几秒内连接到你的主服务器并开始同步。给它发一条消息 —— 无论是私聊还是在它已加入的房间中 —— 来测试。

:::tip
你可以在后台运行 `hermes gateway` 或将其设为 systemd 服务以保持持久运行。详见部署文档。
:::

## 端到端加密（E2EE）

Hermes 支持 Matrix 端到端加密，因此你可以在加密房间中与机器人聊天。

### 要求

E2EE 需要带加密扩展的 `mautrix` 库和 `libolm` C 库：

```bash
# 安装带 E2EE 支持的 mautrix
pip install 'mautrix[encryption]'

# 或使用 hermes 扩展安装
pip install 'hermes-agent[matrix]'
```

你还需要在系统上安装 `libolm`：

```bash
# Debian/Ubuntu
sudo apt install libolm-dev

# macOS
brew install libolm

# Fedora
sudo dnf install libolm-devel
```

### 启用 E2EE

在你的 `~/.hermes/.env` 中添加：

```bash
MATRIX_ENCRYPTION=true
```

启用 E2EE 后，Hermes：

- 在 `~/.hermes/platforms/matrix/store/` 中存储加密密钥（旧版安装：`~/.hermes/matrix/store/`）
- 首次连接时上传设备密钥
- 自动解密传入消息和加密传出消息
- 被邀请时自动加入加密房间

### 交叉签名验证（推荐）

如果你的 Matrix 帐户启用了交叉签名（Element 中的默认设置），请设置恢复密钥以便机器人在启动时可以自签名其设备。如果没有这个，其他 Matrix 客户端可能会在设备密钥轮换后拒绝与机器人共享加密会话。

```bash
MATRIX_RECOVERY_KEY=EsT... 你的恢复密钥
```

**在哪里找到：** 在 Element 中，进入 **Settings** → **Security & Privacy** → **Encryption** → 你的恢复密钥（也称为"安全密钥"）。这是你首次设置交叉签名时被要求保存的密钥。

每次启动时，如果设置了 `MATRIX_RECOVERY_KEY`，Hermes 会从主服务器的安全秘密存储中导入交叉签名密钥并签名当前设备。这是幂等的，可以安全地永久启用。

:::warning[删除加密存储]
如果你删除了 `~/.hermes/platforms/matrix/store/crypto.db`，机器人将丢失其加密身份。仅使用相同设备 ID 重启**不会**完全恢复 —— 主服务器仍持有用旧身份密钥签名的一次性密钥，对端无法建立新的 Olm 会话。

Hermes 在启动时检测到此情况并拒绝启用 E2EE，记录：`device XXXX has stale one-time keys on the server signed with a previous identity key`。

**最简单的恢复方法：生成新的访问令牌**（获得没有陈旧密钥历史的新设备 ID）。参见下方"从之前版本升级 E2EE"部分。这是最可靠的路径，无需触碰主服务器数据库。

**手动恢复**（高级 —— 保持相同设备 ID）：

1. 停止 Synapse 并从其数据库中删除旧设备：
   ```bash
   sudo systemctl stop matrix-synapse
   sudo sqlite3 /var/lib/matrix-synapse/homeserver.db "
     DELETE FROM e2e_device_keys_json WHERE device_id = 'DEVICE_ID' AND user_id = '@hermes:your-server';
     DELETE FROM e2e_one_time_keys_json WHERE device_id = 'DEVICE_ID' AND user_id = '@hermes:your-server';
     DELETE FROM e2e_fallback_keys_json WHERE device_id = 'DEVICE_ID' AND user_id = '@hermes:your-server';
     DELETE FROM devices WHERE device_id = 'DEVICE_ID' AND user_id = '@hermes:your-server';
   "
   sudo systemctl start matrix-synapse
   ```
   或通过 Synapse 管理员 API（注意 URL 编码的用户 ID）：
   ```bash
   curl -X DELETE -H "Authorization: Bearer ADMIN_TOKEN" \
     'https://your-server/_synapse/admin/v2/users/%40hermes%3Ayour-server/devices/DEVICE_ID'
   ```
   注意：通过管理员 API 删除设备可能也会使相关的访问令牌失效。之后你可能需要生成新令牌。

2. 删除本地加密存储并重启 Hermes：
   ```bash
   rm -f ~/.hermes/platforms/matrix/store/crypto.db*
   # 重启 hermes
   ```

其他 Matrix 客户端（Element、matrix-commander）可能缓存了旧的设备密钥。恢复后，在 Element 中输入 `/discardsession` 以强制与机器人建立新的加密会话。
:::

:::info
如果未安装 `mautrix[encryption]` 或缺少 `libolm`，机器人会自动回退到纯文本（未加密）客户端。你会在日志中看到警告。
:::

## 主房间

你可以指定一个"主房间"，机器人在其中发送主动消息（如定时任务输出、提醒和通知）。有两种设置方式：

### 使用斜杠命令

在机器人所在的任何 Matrix 房间中输入 `/sethome`。该房间将成为主房间。

### 手动配置

在你的 `~/.hermes/.env` 中添加：

```bash
MATRIX_HOME_ROOM=!abc123def456:matrix.example.org
```

:::tip
要查找房间 ID：在 Element 中，进入房间 → **Settings** → **Advanced** → **Internal room ID** 显示在那里（以 `!` 开头）。
:::

## 故障排除

### 机器人不回复消息

**原因**：机器人尚未加入房间，或 `MATRIX_ALLOWED_USERS` 不包含你的用户 ID。

**修复**：邀请机器人到房间 —— 它会在收到邀请时自动加入。验证你的用户 ID 在 `MATRIX_ALLOWED_USERS` 中（使用完整的 `@user:server` 格式）。重启网关。

### 启动时 "Failed to authenticate" / "whoami failed"

**原因**：访问令牌或主服务器 URL 不正确。

**修复**：验证 `MATRIX_HOMESERVER` 指向你的主服务器（包含 `https://`，末尾无斜杠）。检查 `MATRIX_ACCESS_TOKEN` 是否有效 —— 用 curl 测试：

```bash
curl -H "Authorization: Bearer YOUR_TOKEN" \
  https://your-server/_matrix/client/v3/account/whoami
```

如果返回你的用户信息，则令牌有效。如果返回错误，则需要生成新令牌。

### "mautrix not installed" 错误

**原因**：未安装 `mautrix` Python 包。

**修复**：安装它：

```bash
pip install 'mautrix[encryption]'
```

或使用 Hermes 扩展：

```bash
pip install 'hermes-agent[matrix]'
```

### 加密错误 / "could not decrypt event"

**原因**：缺少加密密钥、未安装 `libolm`，或机器人的设备不受信任。

**修复**：
1. 验证系统上已安装 `libolm`（参见上方 E2EE 部分）。
2. 确保 `.env` 中设置了 `MATRIX_ENCRYPTION=true`。
3. 在你的 Matrix 客户端（Element）中，进入机器人的个人资料 -> 会话 -> 验证/信任机器人的设备。
4. 如果机器人刚加入加密房间，它只能解密加入*之后*发送的消息。更早的消息不可访问。

### 从之前版本升级 E2EE

:::tip
如果你还手动删除了 `crypto.db`，请参见上方 E2EE 部分中的"删除加密存储"警告 —— 需要额外步骤来清除主服务器上的陈旧一次性密钥。
:::

如果你之前使用 `MATRIX_ENCRYPTION=true` 运行 Hermes 并升级到使用新的基于 SQLite 的加密存储的版本，机器人的加密身份已更改。你的 Matrix 客户端（Element）可能缓存了旧的设备密钥并拒绝与机器人共享加密会话。

**症状**：机器人连接并在日志中显示"E2EE enabled"，但所有消息都显示"could not decrypt event"，机器人从不回复。

**原因**：旧的加密状态（来自之前的 `matrix-nio` 或基于序列化的 `mautrix` 后端）与新的 SQLite 加密存储不兼容。机器人创建了新的加密身份，但你的 Matrix 客户端仍缓存旧密钥，不会与密钥已更改的设备共享房间的加密会话。这是 Matrix 的安全功能 -- 客户端将同一设备的身份密钥更改视为可疑行为。

**修复**（一次性迁移）：

1. **生成新的访问令牌** 以获取新的设备 ID。最简单的方式：

   ```bash
   curl -X POST https://your-server/_matrix/client/v3/login \
     -H "Content-Type: application/json" \
     -d '{
       "type": "m.login.password",
       "identifier": {"type": "m.id.user", "user": "@hermes:your-server.org"},
       "password": "***",
       "initial_device_display_name": "Hermes Agent"
     }'
   ```

   复制新的 `access_token` 并更新 `~/.hermes/.env` 中的 `MATRIX_ACCESS_TOKEN`。

2. **删除旧的加密状态**：

   ```bash
   rm -f ~/.hermes/platforms/matrix/store/crypto.db
   rm -f ~/.hermes/platforms/matrix/store/crypto_store.*
   ```

3. **设置恢复密钥**（如果你使用交叉签名 —— 大多数 Element 用户都是）。在 `~/.hermes/.env` 中添加：

   ```bash
   MATRIX_RECOVERY_KEY=EsT... 你的恢复密钥
   ```

   这让机器人在启动时使用交叉签名密钥自签名，以便 Element 立即信任新设备。否则 Element 可能会将新设备视为未验证并拒绝共享加密会话。在 Element 的 **Settings** → **Security & Privacy** → **Encryption** 中找到恢复密钥。

4. **强制 Matrix 客户端轮换加密会话**。在 Element 中，打开与机器人的私聊房间并输入 `/discardsession`。这强制 Element 创建新的加密会话并与机器人的新设备共享。

5. **重启网关**：

   ```bash
   hermes gateway run
   ```

   如果设置了 `MATRIX_RECOVERY_KEY`，你应该在日志中看到 `Matrix: cross-signing verified via recovery key`。

6. **发送一条新消息**。机器人应该能正常解密和回复。

:::note
迁移后，升级*之前*发送的消息无法解密 -- 旧的加密密钥已丢失。这仅影响过渡期；新消息正常工作。
:::

:::tip
**新安装不受影响。** 此迁移仅在你之前有一个使用 Hermes 早期版本的 E2EE 设置并正在升级时才需要。

**为什么需要新的访问令牌？** 每个 Matrix 访问令牌绑定到一个特定的设备 ID。使用相同设备 ID 但新的加密密钥会导致其他 Matrix 客户端不信任该设备（它们将同一设备的身份密钥更改视为潜在的安全漏洞）。新的访问令牌获得没有陈旧密钥历史的新设备 ID，因此其他客户端会立即信任它。
:::

## 代理模式（macOS 上的 E2EE）

Matrix E2EE 需要 `libolm`，该库无法在 macOS ARM64（Apple Silicon）上编译。`hermes-agent[matrix]` 扩展仅限于 Linux。如果你使用 macOS，代理模式允许你在 Linux 虚拟机的 Docker 容器中运行 E2EE，同时实际代理在 macOS 上原生运行，完全访问你的本地文件、记忆和技能。

### 工作原理

```
macOS（主机）：
  └─ hermes gateway
       ├─ api_server 适配器 ← 监听 0.0.0.0:8642
       ├─ AIAgent ← 唯一的真相来源
       ├─ 会话、记忆、技能
       └─ 本地文件访问（Obsidian、项目等）

Linux 虚拟机（Docker）：
  └─ hermes gateway（代理模式）
       ├─ Matrix 适配器 ← E2EE 解密/加密
       └─ HTTP 转发 → macOS:8642/v1/chat/completions
           （无 LLM API 密钥、无代理、无推理）
```

Docker 容器只处理 Matrix 协议 + E2EE。当消息到达时，它解密消息并通过标准 HTTP 请求将文本转发到主机。主机运行代理、调用工具、生成响应，然后流式传回。容器加密并发送响应到 Matrix。所有会话是统一的 —— CLI、Matrix、Telegram 和任何其他平台共享相同的记忆和对话历史。

### 步骤 1：配置主机（macOS）

启用 API 服务器，以便主机接受来自 Docker 容器的传入请求。

在 `~/.hermes/.env` 中添加：

```bash
API_SERVER_ENABLED=true
API_SERVER_KEY=your-secret-key-here
API_SERVER_HOST=0.0.0.0
```

- `API_SERVER_HOST=0.0.0.0` 绑定到所有接口，以便 Docker 容器可以访问。
- `API_SERVER_KEY` 在非回环绑定时是必需的。选择一个强随机字符串。
- API 服务器默认运行在端口 8642（如需要可使用 `API_SERVER_PORT` 更改）。

启动网关：

```bash
hermes gateway
```

你应该看到 API 服务器与你配置的其他平台一起启动。从虚拟机验证是否可达：

```bash
# 从 Linux 虚拟机
curl http://<mac-ip>:8642/health
```

### 步骤 2：配置 Docker 容器（Linux 虚拟机）

容器需要 Matrix 凭据和代理 URL。它不需要 LLM API 密钥。

**`docker-compose.yml`：**

```yaml
services:
  hermes-matrix:
    build: .
    environment:
      # Matrix 凭据
      MATRIX_HOMESERVER: "https://matrix.example.org"
      MATRIX_ACCESS_TOKEN: "syt_..."
      MATRIX_ALLOWED_USERS: "@you:matrix.example.org"
      MATRIX_ENCRYPTION: "true"
      MATRIX_DEVICE_ID: "HERMES_BOT"

      # 代理模式 — 转发到主机代理
      GATEWAY_PROXY_URL: "http://192.168.1.100:8642"
      GATEWAY_PROXY_KEY: "your-secret-key-here"
    volumes:
      - ./matrix-store:/root/.hermes/platforms/matrix/store
```

**`Dockerfile`：**

```dockerfile
FROM python:3.11-slim

RUN apt-get update && apt-get install -y libolm-dev && rm -rf /var/lib/apt/lists/*
RUN pip install 'hermes-agent[matrix]'

CMD ["hermes", "gateway"]
```

这就是整个容器。不需要 OpenRouter、Anthropic 或任何推理提供商的 API 密钥。

### 步骤 3：启动两者

1. 先启动主机网关：
   ```bash
   hermes gateway
   ```

2. 启动 Docker 容器：
   ```bash
   docker compose up -d
   ```

3. 在加密的 Matrix 房间中发送消息。容器解密消息，转发到主机，并将响应流式传回。

### 配置参考

代理模式在**容器端**（轻量级网关）配置：

| 设置 | 说明 |
|---------|-------------|
| `GATEWAY_PROXY_URL` | 远程 Hermes API 服务器的 URL（例如 `http://192.168.1.100:8642`） |
| `GATEWAY_PROXY_KEY` | 用于认证的 Bearer 令牌（必须与主机上的 `API_SERVER_KEY` 匹配） |
| `gateway.proxy_url` | 与 `GATEWAY_PROXY_URL` 相同，但在 `config.yaml` 中 |

主机端需要：

| 设置 | 说明 |
|---------|-------------|
| `API_SERVER_ENABLED` | 设为 `true` |
| `API_SERVER_KEY` | Bearer 令牌（与容器共享） |
| `API_SERVER_HOST` | 设为 `0.0.0.0` 以允许网络访问 |
| `API_SERVER_PORT` | 端口号（默认：`8642`） |

### 适用于任何平台

代理模式不限于 Matrix。任何平台适配器都可以使用它 —— 在任何网关实例上设置 `GATEWAY_PROXY_URL`，它将转发到远程代理而不是在本地运行一个。这对于平台适配器需要在与代理不同的环境中运行的任何部署都很有用（网络隔离、E2EE 要求、资源限制）。

:::tip
会话连续性通过 `X-Hermes-Session-Id` 头部维护。主机的 API 服务器通过此 ID 跟踪会话，因此对话会像本地代理一样跨消息持久化。
:::

:::note
**限制（v1）：** 来自远程代理的工具进度消息不会被中继回来 —— 用户只看到流式的最终响应，看不到单个工具调用。危险命令审批提示在主机端处理，不会中继给 Matrix 用户。这些可以在未来的更新中解决。
:::

### 同步问题 / 机器人落后

**原因**：长时间运行的工具执行可能延迟同步循环，或主服务器速度慢。

**修复**：同步循环在出错时每 5 秒自动重试。检查 Hermes 日志中与同步相关的警告。如果机器人持续落后，确保主服务器有足够的资源。

### 机器人离线

**原因**：Hermes 网关未运行，或连接失败。

**修复**：检查 `hermes gateway` 是否在运行。查看终端输出中的错误消息。常见问题：错误的主服务器 URL、过期的访问令牌、主服务器不可达。

### "User not allowed" / 机器人忽略你

**原因**：你的用户 ID 不在 `MATRIX_ALLOWED_USERS` 中。

**修复**：在 `~/.hermes/.env` 中将你的用户 ID 添加到 `MATRIX_ALLOWED_USERS`，然后重启网关。使用完整的 `@user:server` 格式。

## 安全

:::warning
始终设置 `MATRIX_ALLOWED_USERS` 以限制谁可以与机器人交互。如果不设置，网关默认拒绝所有用户作为安全措施。仅添加你信任的用户 ID —— 授权用户可以完全访问代理的能力，包括工具使用和系统访问。
:::

有关保护 Hermes Agent 部署的更多信息，请参见[安全指南](../security.md)。

## 注意事项

- **任何主服务器**：可与 Synapse、Conduit、Dendrite、matrix.org 或任何符合规范的 Matrix 主服务器配合使用。无需特定的主服务器软件。
- **联邦**：如果你在联邦主服务器上，机器人可以与其他服务器的用户通信 —— 只需将他们完整的 `@user:server` ID 添加到 `MATRIX_ALLOWED_USERS`。
- **自动加入**：机器人自动接受房间邀请并加入。加入后立即开始回复。
- **媒体支持**：Hermes 可以发送和接收图片、音频、视频和文件附件。媒体使用 Matrix 内容仓库 API 上传到你的主服务器。
- **原生语音消息（MSC3245）**：Matrix 适配器会自动为出站语音消息添加 `org.matrix.msc3245.voice` 标志。这意味着 TTS 响应和语音音频在 Element 和其他支持 MSC3245 的客户端中呈现为**原生语音气泡**，而不是通用音频文件附件。带有 MSC3245 标志的入站语音消息也能被正确识别并路由到语音转文字转录。无需配置 —— 这会自动工作。
