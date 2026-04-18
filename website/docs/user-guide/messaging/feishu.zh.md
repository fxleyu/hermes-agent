---
sidebar_position: 11
title: "飞书 / Lark"
description: "将 Hermes Agent 设置为飞书或 Lark 机器人"
---

# 飞书 / Lark 设置

Hermes Agent 可以作为功能完善的机器人与飞书和 Lark 集成。连接后，你可以在私聊或群聊中与代理对话，在主聊天中接收定时任务结果，并通过常规网关流程发送文本、图片、音频和文件附件。

该集成支持两种连接模式：

- `websocket` —— 推荐；Hermes 发起出站连接，无需公网 Webhook 端点
- `webhook` —— 当你希望飞书/Lark 通过 HTTP 将事件推送到网关时使用

## Hermes 的行为方式

| 场景 | 行为 |
|---------|----------|
| 私聊 | Hermes 回复每条消息。 |
| 群聊 | Hermes 仅在机器人被 @提及时回复。 |
| 共享群聊 | 默认情况下，共享聊天中的会话历史按用户隔离。 |

此共享聊天行为由 `config.yaml` 控制：

```yaml
group_sessions_per_user: true
```

仅当你明确需要每个聊天共享一个对话时，才将其设为 `false`。

## 步骤 1：创建飞书 / Lark 应用

### 推荐方式：扫码创建（一条命令）

```bash
hermes gateway setup
```

选择 **Feishu / Lark** 并使用飞书或 Lark 手机应用扫描二维码。Hermes 会自动创建具有正确权限的机器人应用并保存凭据。

### 备选方式：手动设置

如果扫码创建不可用，向导会退回到手动输入：

1. 打开飞书或 Lark 开发者控制台：
   - 飞书：[https://open.feishu.cn/](https://open.feishu.cn/)
   - Lark：[https://open.larksuite.com/](https://open.larksuite.com/)
2. 创建一个新应用。
3. 在**凭证与基本信息**中，复制 **App ID** 和 **App Secret**。
4. 为应用启用**机器人**能力。
5. 运行 `hermes gateway setup`，选择 **Feishu / Lark**，并在提示时输入凭据。

:::warning
请保管好 App Secret。任何拥有它的人都可以冒充你的应用。
:::

## 步骤 2：选择连接模式

### 推荐方式：WebSocket 模式

当 Hermes 运行在你的笔记本电脑、工作站或私有服务器上时，使用 WebSocket 模式。不需要公网 URL。官方 Lark SDK 会打开并维护一个持久的出站 WebSocket 连接，并具有自动重连功能。

```bash
FEISHU_CONNECTION_MODE=websocket
```

**要求：** 必须安装 `websockets` Python 包。SDK 在内部处理连接生命周期、心跳和自动重连。

**工作原理：** 适配器在后台执行器线程中运行 Lark SDK 的 WebSocket 客户端。入站事件（消息、回应、卡片操作）被分发到主 asyncio 循环。断开连接时，SDK 会自动尝试重新连接。

### 可选方式：Webhook 模式

仅当你已经在可达的 HTTP 端点后面运行 Hermes 时才使用 Webhook 模式。

```bash
FEISHU_CONNECTION_MODE=webhook
```

在 Webhook 模式下，Hermes 启动一个 HTTP 服务器（通过 `aiohttp`）并提供一个飞书端点：

```text
/feishu/webhook
```

**要求：** 必须安装 `aiohttp` Python 包。

你可以自定义 Webhook 服务器的绑定地址和路径：

```bash
FEISHU_WEBHOOK_HOST=127.0.0.1   # 默认：127.0.0.1
FEISHU_WEBHOOK_PORT=8765         # 默认：8765
FEISHU_WEBHOOK_PATH=/feishu/webhook  # 默认：/feishu/webhook
```

当飞书发送 URL 验证质询（`type: url_verification`）时，Webhook 会自动响应，以便你在飞书开发者控制台中完成订阅设置。

## 步骤 3：配置 Hermes

### 方式 A：交互式设置

```bash
hermes gateway setup
```

选择 **Feishu / Lark** 并填写提示信息。

### 方式 B：手动配置

在 `~/.hermes/.env` 中添加以下内容：

```bash
FEISHU_APP_ID=cli_xxx
FEISHU_APP_SECRET=secret_xxx
FEISHU_DOMAIN=feishu
FEISHU_CONNECTION_MODE=websocket

# 可选但强烈推荐
FEISHU_ALLOWED_USERS=ou_xxx,ou_yyy
FEISHU_HOME_CHANNEL=oc_xxx
```

`FEISHU_DOMAIN` 接受：

- `feishu` —— 飞书（中国）
- `lark` —— Lark（国际版）

## 步骤 4：启动网关

```bash
hermes gateway
```

然后从飞书/Lark 向机器人发消息以确认连接正常。

## 主聊天

在飞书/Lark 聊天中使用 `/set-home` 将其标记为定时任务结果和跨平台通知的主频道。

你也可以预先配置：

```bash
FEISHU_HOME_CHANNEL=oc_xxx
```

## 安全

### 用户白名单

用于生产环境时，设置飞书 Open ID 的白名单：

```bash
FEISHU_ALLOWED_USERS=ou_xxx,ou_yyy
```

如果白名单为空，任何能访问机器人的人都可能使用它。在群聊中，白名单会在消息处理前检查发送者的 open_id。

### Webhook 加密密钥

在 Webhook 模式下运行时，设置加密密钥以启用入站 Webhook 载荷的签名验证：

```bash
FEISHU_ENCRYPT_KEY=your-encrypt-key
```

此密钥位于飞书应用配置的**事件订阅**部分。设置后，适配器使用以下签名算法验证每个 Webhook 请求：

```
SHA256(timestamp + nonce + encrypt_key + body)
```

计算的哈希值与 `x-lark-signature` 头部使用时间安全比较进行对比。签名无效或缺失的请求将被拒绝并返回 HTTP 401。

:::tip
在 WebSocket 模式下，签名验证由 SDK 自身处理，因此 `FEISHU_ENCRYPT_KEY` 是可选的。在 Webhook 模式下，强烈建议在生产环境中使用。
:::

### 验证令牌

一个额外的身份验证层，用于检查 Webhook 载荷中的 `token` 字段：

```bash
FEISHU_VERIFICATION_TOKEN=your-verification-token
```

此令牌也位于飞书应用的**事件订阅**部分。设置后，每个入站 Webhook 载荷的 `header` 对象中必须包含匹配的 `token`。令牌不匹配的请求将被拒绝并返回 HTTP 401。

`FEISHU_ENCRYPT_KEY` 和 `FEISHU_VERIFICATION_TOKEN` 可以同时使用以实现纵深防御。

## 群消息策略

`FEISHU_GROUP_POLICY` 环境变量控制 Hermes 是否以及如何在群聊中回复：

```bash
FEISHU_GROUP_POLICY=allowlist   # 默认
```

| 值 | 行为 |
|-------|----------|
| `open` | Hermes 回复任何群中任何用户的 @提及。 |
| `allowlist` | Hermes 仅回复 `FEISHU_ALLOWED_USERS` 中列出的用户的 @提及。 |
| `disabled` | Hermes 完全忽略所有群消息。 |

在所有模式下，机器人必须在群中被明确 @提及（或 @所有人），消息才会被处理。私聊不受此限制。

### 用于 @提及检测的机器人身份

为了在群中精确检测 @提及，适配器需要知道机器人的身份。可以显式提供：

```bash
FEISHU_BOT_OPEN_ID=ou_xxx
FEISHU_BOT_USER_ID=xxx
FEISHU_BOT_NAME=MyBot
```

如果未设置这些参数，适配器将在启动时通过应用信息 API 尝试自动发现机器人名称。为使其正常工作，请授予 `admin:app.info:readonly` 或 `application:application:self_manage` 权限范围。

## 交互式卡片操作

当用户点击按钮或与机器人发送的交互式卡片交互时，适配器将这些操作路由为合成的 `/card` 命令事件：

- 按钮点击变为：`/card button {"key": "value", ...}`
- 卡片定义中操作的 `value` 载荷以 JSON 形式包含。
- 卡片操作使用 15 分钟窗口进行去重以防止重复处理。

卡片操作事件以 `MessageType.COMMAND` 分发，因此它们通过正常的命令处理管道流转。

这也是**命令审批**的工作方式 —— 当代理需要运行危险命令时，它会发送一张带有"允许一次"/"本次会话"/"始终允许"/"拒绝"按钮的交互式卡片。用户点击按钮，卡片操作回调将审批决定传回代理。

### 飞书应用必需配置

交互式卡片需要在飞书开发者控制台中完成**三个**配置步骤。缺少任何一个都会在用户点击卡片按钮时导致错误 **200340**。

1. **订阅卡片操作事件：**
   在**事件订阅**中，将 `card.action.trigger` 添加到你的订阅事件中。

2. **启用交互式卡片能力：**
   在**应用功能 > 机器人**中，确保**交互式卡片**开关已启用。这告诉飞书你的应用可以接收卡片操作回调。

3. **配置卡片请求 URL（仅 Webhook 模式）：**
   在**应用功能 > 机器人 > 消息卡片请求 URL** 中，将 URL 设置为与事件 Webhook 相同的端点（例如 `https://your-server:8765/feishu/webhook`）。在 WebSocket 模式下，SDK 会自动处理此步骤。

:::warning
如果缺少这三个步骤中的任何一个，飞书将成功*发送*交互式卡片（发送仅需要 `im:message:send` 权限），但点击任何按钮将返回错误 200340。卡片看起来可以正常工作 —— 错误仅在用户与其交互时出现。
:::

## 媒体支持

### 入站（接收）

适配器从用户接收并缓存以下媒体类型：

| 类型 | 扩展名 | 处理方式 |
|------|-----------|-------------------|
| **图片** | .jpg, .jpeg, .png, .gif, .webp, .bmp | 通过飞书 API 下载并本地缓存 |
| **音频** | .ogg, .mp3, .wav, .m4a, .aac, .flac, .opus, .webm | 下载并缓存；小型文本文件会自动提取 |
| **视频** | .mp4, .mov, .avi, .mkv, .webm, .m4v, .3gp | 下载并作为文档缓存 |
| **文件** | .pdf, .doc, .docx, .xls, .xlsx, .ppt, .pptx 等 | 下载并作为文档缓存 |

富文本（post）消息中的媒体（包括内嵌图片和文件附件）也会被提取和缓存。

对于小型文本文档（.txt、.md），文件内容会自动注入到消息文本中，以便代理无需使用工具即可直接阅读。

### 出站（发送）

| 方法 | 发送内容 |
|--------|--------------|
| `send` | 文本或富文本消息（根据 Markdown 内容自动检测） |
| `send_image` / `send_image_file` | 将图片上传到飞书，然后作为原生图片气泡发送（可带标题） |
| `send_document` | 将文件上传到飞书 API，然后作为文件附件发送 |
| `send_voice` | 将音频文件作为飞书文件附件上传 |
| `send_video` | 上传视频并作为原生媒体消息发送 |
| `send_animation` | GIF 会降级为文件附件（飞书没有原生 GIF 气泡） |

文件上传路由根据扩展名自动进行：

- `.ogg`、`.opus` → 作为 `opus` 音频上传
- `.mp4`、`.mov`、`.avi`、`.m4v` → 作为 `mp4` 媒体上传
- `.pdf`、`.doc(x)`、`.xls(x)`、`.ppt(x)` → 以其文档类型上传
- 其他所有文件 → 作为通用流文件上传

## Markdown 渲染和 Post 降级

当出站文本包含 Markdown 格式（标题、粗体、列表、代码块、链接等）时，适配器会自动将其作为飞书 **post** 消息发送（内嵌 `md` 标签），而不是纯文本。这样可以在飞书客户端实现富文本渲染。

如果飞书 API 拒绝 post 载荷（例如由于不支持的 Markdown 结构），适配器会自动降级为去除 Markdown 后发送纯文本。这种两阶段降级确保消息始终能被送达。

纯文本消息（未检测到 Markdown）以简单的 `text` 消息类型发送。

## ACK 表情回应

当适配器收到入站消息时，它会立即添加一个 ✅（OK）表情回应，以表示消息已收到并正在处理。这在代理完成回复之前提供了视觉反馈。

该回应是持久的 —— 在回复发送后它仍保留在消息上，作为接收标记。

用户对机器人消息的回应也会被跟踪。如果用户在机器人发送的消息上添加或删除表情回应，它会作为合成文本事件路由（`reaction:added:EMOJI_TYPE` 或 `reaction:removed:EMOJI_TYPE`），以便代理可以响应反馈。

## 消息突发保护和批处理

适配器包含对快速消息突发的去抖功能，以避免代理过载：

### 文本批处理

当用户快速连续发送多条文本消息时，它们会在分发前合并为单个事件：

| 设置 | 环境变量 | 默认值 |
|---------|---------|---------|
| 静默期 | `HERMES_FEISHU_TEXT_BATCH_DELAY_SECONDS` | 0.6秒 |
| 每批最大消息数 | `HERMES_FEISHU_TEXT_BATCH_MAX_MESSAGES` | 8 |
| 每批最大字符数 | `HERMES_FEISHU_TEXT_BATCH_MAX_CHARS` | 4000 |

### 媒体批处理

快速连续发送的多个媒体附件（例如拖放多张图片）会合并为单个事件：

| 设置 | 环境变量 | 默认值 |
|---------|---------|---------|
| 静默期 | `HERMES_FEISHU_MEDIA_BATCH_DELAY_SECONDS` | 0.8秒 |

### 按聊天串行化

同一聊天中的消息按顺序处理（一次一条），以保持对话的连贯性。每个聊天有自己的锁，因此不同聊天中的消息可以并发处理。

## 速率限制（Webhook 模式）

在 Webhook 模式下，适配器对每个 IP 进行速率限制以防止滥用：

- **窗口：** 60 秒滑动窗口
- **限制：** 每个（app_id、路径、IP）三元组每窗口 120 个请求
- **跟踪上限：** 最多跟踪 4096 个唯一键（防止内存无限增长）

超过限制的请求将收到 HTTP 429（请求过多）。

### Webhook 异常跟踪

适配器跟踪每个 IP 地址的连续错误响应。在 6 小时窗口内，同一 IP 出现 25 次连续错误后，会记录一条警告。这有助于检测错误配置的客户端或探测尝试。

额外的 Webhook 保护措施：
- **请求体大小限制：** 最大 1 MB
- **请求体读取超时：** 30 秒
- **Content-Type 强制：** 仅接受 `application/json`

## WebSocket 调优

使用 `websocket` 模式时，你可以自定义重连和 ping 行为：

```yaml
platforms:
  feishu:
    extra:
      ws_reconnect_interval: 120   # 重连尝试之间的秒数（默认：120）
      ws_ping_interval: 30         # WebSocket ping 之间的秒数（可选；未设置时使用 SDK 默认值）
```

| 设置 | 配置键 | 默认值 | 说明 |
|---------|-----------|---------|-------------|
| 重连间隔 | `ws_reconnect_interval` | 120秒 | 重连尝试之间的等待时间 |
| Ping 间隔 | `ws_ping_interval` | _(SDK 默认值)_ | WebSocket 保活 ping 的频率 |

## 按群访问控制

除了全局 `FEISHU_GROUP_POLICY` 之外，你还可以使用 config.yaml 中的 `group_rules` 为每个群聊设置细粒度规则：

```yaml
platforms:
  feishu:
    extra:
      default_group_policy: "open"     # 不在 group_rules 中的群的默认策略
      admins:                          # 可以管理机器人设置的用户
        - "ou_admin_open_id"
      group_rules:
        "oc_group_chat_id_1":
          policy: "allowlist"          # open | allowlist | blacklist | admin_only | disabled
          allowlist:
            - "ou_user_open_id_1"
            - "ou_user_open_id_2"
        "oc_group_chat_id_2":
          policy: "admin_only"
        "oc_group_chat_id_3":
          policy: "blacklist"
          blacklist:
            - "ou_blocked_user"
```

| 策略 | 说明 |
|--------|-------------|
| `open` | 群中任何人都可以使用机器人 |
| `allowlist` | 只有群 `allowlist` 中的用户可以使用机器人 |
| `blacklist` | 除了群 `blacklist` 中的用户外，所有人都可以使用机器人 |
| `admin_only` | 只有全局 `admins` 列表中的用户可以在此群中使用机器人 |
| `disabled` | 机器人忽略此群中的所有消息 |

未在 `group_rules` 中列出的群将回退到 `default_group_policy`（默认值为 `FEISHU_GROUP_POLICY` 的值）。

## 去重

入站消息使用消息 ID 进行去重，TTL 为 24 小时。去重状态持久化到 `~/.hermes/feishu_seen_message_ids.json`，可跨重启保留。

| 设置 | 环境变量 | 默认值 |
|---------|---------|---------|
| 缓存大小 | `HERMES_FEISHU_DEDUP_CACHE_SIZE` | 2048 条 |

## 所有环境变量

| 变量 | 必填 | 默认值 | 说明 |
|----------|----------|---------|-------------|
| `FEISHU_APP_ID` | ✅ | — | 飞书/Lark App ID |
| `FEISHU_APP_SECRET` | ✅ | — | 飞书/Lark App Secret |
| `FEISHU_DOMAIN` | — | `feishu` | `feishu`（中国）或 `lark`（国际版） |
| `FEISHU_CONNECTION_MODE` | — | `websocket` | `websocket` 或 `webhook` |
| `FEISHU_ALLOWED_USERS` | — | _(空)_ | 以逗号分隔的 open_id 列表用于用户白名单 |
| `FEISHU_HOME_CHANNEL` | — | — | 用于定时任务/通知输出的聊天 ID |
| `FEISHU_ENCRYPT_KEY` | — | _(空)_ | 用于 Webhook 签名验证的加密密钥 |
| `FEISHU_VERIFICATION_TOKEN` | — | _(空)_ | 用于 Webhook 载荷认证的验证令牌 |
| `FEISHU_GROUP_POLICY` | — | `allowlist` | 群消息策略：`open`、`allowlist`、`disabled` |
| `FEISHU_BOT_OPEN_ID` | — | _(空)_ | 机器人的 open_id（用于 @提及检测） |
| `FEISHU_BOT_USER_ID` | — | _(空)_ | 机器人的 user_id（用于 @提及检测） |
| `FEISHU_BOT_NAME` | — | _(空)_ | 机器人的显示名称（用于 @提及检测） |
| `FEISHU_WEBHOOK_HOST` | — | `127.0.0.1` | Webhook 服务器绑定地址 |
| `FEISHU_WEBHOOK_PORT` | — | `8765` | Webhook 服务器端口 |
| `FEISHU_WEBHOOK_PATH` | — | `/feishu/webhook` | Webhook 端点路径 |
| `HERMES_FEISHU_DEDUP_CACHE_SIZE` | — | `2048` | 最大跟踪的去重消息 ID 数量 |
| `HERMES_FEISHU_TEXT_BATCH_DELAY_SECONDS` | — | `0.6` | 文本突发去抖静默期 |
| `HERMES_FEISHU_TEXT_BATCH_MAX_MESSAGES` | — | `8` | 每批文本合并的最大消息数 |
| `HERMES_FEISHU_TEXT_BATCH_MAX_CHARS` | — | `4000` | 每批文本合并的最大字符数 |
| `HERMES_FEISHU_MEDIA_BATCH_DELAY_SECONDS` | — | `0.8` | 媒体突发去抖静默期 |

WebSocket 和按群 ACL 设置通过 `config.yaml` 中的 `platforms.feishu.extra` 配置（参见上方的 [WebSocket 调优](#websocket-调优) 和 [按群访问控制](#按群访问控制)）。

## 故障排除

| 问题 | 修复 |
|---------|-----|
| `lark-oapi not installed` | 安装 SDK：`pip install lark-oapi` |
| `websockets not installed; websocket mode unavailable` | 安装 websockets：`pip install websockets` |
| `aiohttp not installed; webhook mode unavailable` | 安装 aiohttp：`pip install aiohttp` |
| `FEISHU_APP_ID or FEISHU_APP_SECRET not set` | 设置两个环境变量或通过 `hermes gateway setup` 配置 |
| `Another local Hermes gateway is already using this Feishu app_id` | 同一 app_id 同时只能由一个 Hermes 实例使用。先停止另一个网关。 |
| 机器人在群中不回复 | 确保机器人被 @提及，检查 `FEISHU_GROUP_POLICY`，并在策略为 `allowlist` 时验证发送者是否在 `FEISHU_ALLOWED_USERS` 中 |
| `Webhook rejected: invalid verification token` | 确保 `FEISHU_VERIFICATION_TOKEN` 与飞书应用事件订阅配置中的令牌匹配 |
| `Webhook rejected: invalid signature` | 确保 `FEISHU_ENCRYPT_KEY` 与飞书应用配置中的加密密钥匹配 |
| Post 消息显示为纯文本 | 飞书 API 拒绝了 post 载荷；这是正常的降级行为。查看日志了解详情。 |
| 机器人未收到图片/文件 | 为飞书应用授予 `im:message` 和 `im:resource` 权限范围 |
| 机器人身份未自动检测 | 授予 `admin:app.info:readonly` 权限范围，或手动设置 `FEISHU_BOT_OPEN_ID` / `FEISHU_BOT_NAME` |
| 点击审批按钮时出现错误 200340 | 在飞书开发者控制台中启用**交互式卡片**能力并配置**卡片请求 URL**。参见上方的[飞书应用必需配置](#飞书应用必需配置)。 |
| `Webhook rate limit exceeded` | 同一 IP 每分钟超过 120 个请求。这通常是配置错误或循环。 |

## 工具集

飞书 / Lark 使用 `hermes-feishu` 平台预设，其中包含与 Telegram 和其他基于网关的消息平台相同的核心工具。
