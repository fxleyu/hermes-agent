# QQ Bot

通过**QQ 官方机器人 API（v2）**将 Hermes 连接到 QQ —— 支持私聊（C2C）、群 @提及、频道和私信，并具备语音转写功能。

## 概述

QQ Bot 适配器使用 [QQ 官方机器人 API](https://bot.q.qq.com/wiki/develop/api-v2/) 来：

- 通过与 QQ Gateway 的持久 **WebSocket** 连接接收消息
- 通过 **REST API** 发送文本和 Markdown 回复
- 下载和处理图片、语音消息和文件附件
- 使用腾讯内置 ASR 或可配置的 STT 提供商转写语音消息

## 前提条件

1. **QQ Bot 应用** —— 在 [q.qq.com](https://q.qq.com) 注册：
   - 创建一个新应用并记下你的 **App ID** 和 **App Secret**
   - 启用所需的 intents：C2C 消息、群 @消息、频道消息
   - 配置沙盒模式用于测试，或发布用于生产

2. **依赖** —— 适配器需要 `aiohttp` 和 `httpx`：
   ```bash
   pip install aiohttp httpx
   ```

## 配置

### 交互式设置

```bash
hermes setup gateway
```

从平台列表中选择 **QQ Bot** 并按提示操作。

### 手动配置

在 `~/.hermes/.env` 中设置所需的环境变量：

```bash
QQ_APP_ID=your-app-id
QQ_CLIENT_SECRET=your-app-secret
```

## 环境变量

| 变量 | 说明 | 默认值 |
|---|---|---|
| `QQ_APP_ID` | QQ Bot App ID（必填） | — |
| `QQ_CLIENT_SECRET` | QQ Bot App Secret（必填） | — |
| `QQ_HOME_CHANNEL` | 用于定时任务/通知投递的 OpenID | — |
| `QQ_HOME_CHANNEL_NAME` | 主频道显示名称 | `Home` |
| `QQ_ALLOWED_USERS` | 逗号分隔的用户 OpenID 用于私信访问 | 开放（所有用户） |
| `QQ_ALLOW_ALL_USERS` | 设为 `true` 允许所有私信 | `false` |
| `QQ_MARKDOWN_SUPPORT` | 启用 QQ Markdown（msg_type 2） | `true` |
| `QQ_STT_API_KEY` | 语音转文字提供商的 API 密钥 | — |
| `QQ_STT_BASE_URL` | STT 提供商的基础 URL | `https://open.bigmodel.cn/api/coding/paas/v4` |
| `QQ_STT_MODEL` | STT 模型名称 | `glm-asr` |

## 高级配置

要进行细粒度控制，请在 `~/.hermes/config.yaml` 中添加平台设置：

```yaml
platforms:
  qq:
    enabled: true
    extra:
      app_id: "your-app-id"
      client_secret: "your-secret"
      markdown_support: true
      dm_policy: "open"          # open | allowlist | disabled
      allow_from:
        - "user_openid_1"
      group_policy: "open"       # open | allowlist | disabled
      group_allow_from:
        - "group_openid_1"
      stt:
        provider: "zai"          # zai（GLM-ASR）、openai（Whisper）等
        baseUrl: "https://open.bigmodel.cn/api/coding/paas/v4"
        apiKey: "your-stt-key"
        model: "glm-asr"
```

## 语音消息（STT）

语音转写分两个阶段工作：

1. **QQ 内置 ASR**（免费，始终优先尝试）—— QQ 在语音消息附件中提供 `asr_refer_text`，使用腾讯自己的语音识别
2. **配置的 STT 提供商**（降级方案）—— 如果 QQ 的 ASR 没有返回文本，适配器会调用一个 OpenAI 兼容的 STT API：

   - **智谱/GLM（zai）**：默认提供商，使用 `glm-asr` 模型
   - **OpenAI Whisper**：设置 `QQ_STT_BASE_URL` 和 `QQ_STT_MODEL`
   - 任何 OpenAI 兼容的 STT 端点

## 故障排除

### 机器人立即断开连接（快速断开）

这通常意味着：
- **无效的 App ID / Secret** —— 在 q.qq.com 上仔细检查你的凭据
- **缺少权限** —— 确保机器人启用了所需的 intents
- **仅沙盒模式的机器人** —— 如果机器人处于沙盒模式，它只能从 QQ 的沙盒测试频道接收消息

### 语音消息未转写

1. 检查附件数据中是否存在 QQ 内置的 `asr_refer_text`
2. 如果使用自定义 STT 提供商，验证 `QQ_STT_API_KEY` 是否正确设置
3. 检查网关日志中的 STT 错误消息

### 消息未送达

- 验证机器人的 **intents** 是否在 q.qq.com 上启用
- 如果私信访问受限，检查 `QQ_ALLOWED_USERS`
- 对于群消息，确保机器人被 **@提及**（群策略可能需要白名单）
- 检查 `QQ_HOME_CHANNEL` 用于定时任务/通知投递

### 连接错误

- 确保已安装 `aiohttp` 和 `httpx`：`pip install aiohttp httpx`
- 检查与 `api.sgroup.qq.com` 和 WebSocket 网关的网络连接
- 查看网关日志中的详细错误消息和重连行为
