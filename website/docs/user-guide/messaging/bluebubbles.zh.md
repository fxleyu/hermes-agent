# BlueBubbles (iMessage)

通过 [BlueBubbles](https://bluebubbles.app/) 将 Hermes 连接到 Apple iMessage —— 一个免费、开源的 macOS 服务器，用于将 iMessage 桥接到任何设备。

## 前提条件

- 一台**始终在线的 Mac**，运行 [BlueBubbles Server](https://bluebubbles.app/)
- 在该 Mac 的 Messages.app 上登录 Apple ID
- BlueBubbles Server v1.0.0+（Webhook 功能需要此版本）
- Hermes 与 BlueBubbles 服务器之间的网络连接

## 设置

### 1. 安装 BlueBubbles Server

从 [bluebubbles.app](https://bluebubbles.app/) 下载并安装。完成设置向导 —— 使用你的 Apple ID 登录并配置连接方式（局域网、Ngrok、Cloudflare 或动态 DNS）。

### 2. 获取服务器 URL 和密码

在 BlueBubbles Server → **Settings → API** 中，记下：
- **Server URL**（例如 `http://192.168.1.10:1234`）
- **Server Password**

### 3. 配置 Hermes

运行设置向导：

```bash
hermes gateway setup
```

选择 **BlueBubbles (iMessage)** 并输入你的服务器 URL 和密码。

或者直接在 `~/.hermes/.env` 中设置环境变量：

```bash
BLUEBUBBLES_SERVER_URL=http://192.168.1.10:1234
BLUEBUBBLES_PASSWORD=your-server-password
```

### 4. 授权用户

选择以下方式之一：

**DM 配对（推荐）：**
当有人向你的 iMessage 发消息时，Hermes 会自动向他们发送配对码。使用以下命令批准：
```bash
hermes pairing approve bluebubbles <CODE>
```
使用 `hermes pairing list` 查看待处理的配对码和已批准的用户。

**预授权特定用户**（在 `~/.hermes/.env` 中）：
```bash
BLUEBUBBLES_ALLOWED_USERS=user@icloud.com,+15551234567
```

**开放访问**（在 `~/.hermes/.env` 中）：
```bash
BLUEBUBBLES_ALLOW_ALL_USERS=true
```

### 5. 启动网关

```bash
hermes gateway run
```

Hermes 将连接到你的 BlueBubbles 服务器，注册 Webhook，并开始监听 iMessage 消息。

## 工作原理

```
iMessage → Messages.app → BlueBubbles Server → Webhook → Hermes
Hermes → BlueBubbles REST API → Messages.app → iMessage
```

- **入站：** 当有新消息到达时，BlueBubbles 通过 Webhook 事件发送到本地监听器。无需轮询 —— 即时送达。
- **出站：** Hermes 通过 BlueBubbles REST API 发送消息。
- **媒体：** 支持双向传输图片、语音消息、视频和文档。入站附件会被下载并缓存到本地供代理处理。

## 环境变量

| 变量 | 必填 | 默认值 | 说明 |
|----------|----------|---------|-------------|
| `BLUEBUBBLES_SERVER_URL` | 是 | — | BlueBubbles 服务器 URL |
| `BLUEBUBBLES_PASSWORD` | 是 | — | 服务器密码 |
| `BLUEBUBBLES_WEBHOOK_HOST` | 否 | `127.0.0.1` | Webhook 监听绑定地址 |
| `BLUEBUBBLES_WEBHOOK_PORT` | 否 | `8645` | Webhook 监听端口 |
| `BLUEBUBBLES_WEBHOOK_PATH` | 否 | `/bluebubbles-webhook` | Webhook URL 路径 |
| `BLUEBUBBLES_HOME_CHANNEL` | 否 | — | 用于定时任务投递的电话/邮箱 |
| `BLUEBUBBLES_ALLOWED_USERS` | 否 | — | 以逗号分隔的授权用户列表 |
| `BLUEBUBBLES_ALLOW_ALL_USERS` | 否 | `false` | 允许所有用户 |
| `BLUEBUBBLES_SEND_READ_RECEIPTS` | 否 | `true` | 自动将消息标记为已读 |

## 功能

### 文字消息
发送和接收 iMessage。Markdown 会自动去除以保证纯文本的清晰展示。

### 富媒体
- **图片：** 照片在 iMessage 对话中原生显示
- **语音消息：** 音频文件作为 iMessage 语音消息发送
- **视频：** 视频附件
- **文档：** 文件作为 iMessage 附件发送

### Tapback 回应
爱心、点赞、不喜欢、大笑、强调和疑问回应。需要安装 BlueBubbles [Private API 助手](https://docs.bluebubbles.app/helper-bundle/installation)。

### 正在输入指示器
当代理正在处理时，在 iMessage 对话中显示"正在输入..."。需要 Private API。

### 已读回执
处理完成后自动将消息标记为已读。需要 Private API。

### 聊天寻址
你可以通过邮箱或电话号码寻址聊天 —— Hermes 会自动将其解析为 BlueBubbles 聊天 GUID。无需使用原始 GUID 格式。

## Private API

部分功能需要 BlueBubbles [Private API 助手](https://docs.bluebubbles.app/helper-bundle/installation)：
- Tapback 回应
- 正在输入指示器
- 已读回执
- 通过地址创建新聊天

如果没有 Private API，基本的文字消息和媒体功能仍然可用。

## 故障排除

### "Cannot reach server"
- 确认服务器 URL 正确且 Mac 已开机
- 检查 BlueBubbles Server 是否正在运行
- 确保网络连通（防火墙、端口转发）

### 消息未到达
- 检查 Webhook 是否已在 BlueBubbles Server → Settings → API → Webhooks 中注册
- 确认 Webhook URL 可从 Mac 访问
- 检查 `hermes logs gateway` 中的 Webhook 错误（或使用 `hermes logs -f` 实时跟踪）

### "Private API helper not connected"
- 安装 Private API 助手：[docs.bluebubbles.app](https://docs.bluebubbles.app/helper-bundle/installation)
- 没有它基本消息功能也能使用 —— 只有回应、输入指示器和已读回执需要它
