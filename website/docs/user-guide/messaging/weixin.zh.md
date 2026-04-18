---
sidebar_position: 15
title: "微信 (WeChat)"
description: "通过 iLink Bot API 将 Hermes Agent 连接到个人微信账号"
---

# 微信 (WeChat)

将 Hermes 连接到[微信](https://weixin.qq.com/)，腾讯的个人即时通讯平台。适配器使用腾讯的 **iLink Bot API** 对接个人微信账号——这与企业微信（WeCom）是不同的。消息通过长轮询方式传递，因此不需要公网端点或 Webhook。

:::info
此适配器适用于**个人微信账号**。如果需要企业微信，请参阅 [WeCom 适配器](./wecom.md)。
:::

## 前置条件

- 一个个人微信账号
- Python 包：`aiohttp` 和 `cryptography`
- `qrcode` 包为可选（用于在终端渲染二维码）

安装所需依赖：

```bash
pip install aiohttp cryptography
# 可选：用于终端显示二维码
pip install qrcode
```

## 设置

### 1. 运行设置向导

连接微信账号最简单的方式是通过交互式设置：

```bash
hermes gateway setup
```

出现提示时选择 **Weixin**。向导将：

1. 从 iLink Bot API 请求二维码
2. 在终端显示二维码（或提供 URL）
3. 等待你使用微信手机 App 扫描二维码
4. 提示你在手机上确认登录
5. 自动将账号凭证保存到 `~/.hermes/weixin/accounts/`

确认后，你将看到如下消息：

```
微信连接成功，account_id=your-account-id
```

向导会存储 `account_id`、`token` 和 `base_url`，无需手动配置。

### 2. 配置环境变量

完成初始二维码登录后，至少需要在 `~/.hermes/.env` 中设置账号 ID：

```bash
WEIXIN_ACCOUNT_ID=your-account-id

# 可选：覆盖 token（通常从二维码登录自动保存）
# WEIXIN_TOKEN=your-bot-token

# 可选：限制访问
WEIXIN_DM_POLICY=open
WEIXIN_ALLOWED_USERS=user_id_1,user_id_2

# 可选：恢复旧版多行拆分行为
# WEIXIN_SPLIT_MULTILINE_MESSAGES=true

# 可选：用于定时任务/通知的主频道
WEIXIN_HOME_CHANNEL=chat_id
WEIXIN_HOME_CHANNEL_NAME=Home
```

### 3. 启动网关

```bash
hermes gateway
```

适配器将恢复已保存的凭证，连接到 iLink API，并开始长轮询接收消息。

## 功能特性

- **长轮询传输** — 无需公网端点、Webhook 或 WebSocket
- **二维码登录** — 通过 `hermes gateway setup` 扫码连接
- **私聊和群聊消息** — 可配置的访问策略
- **媒体支持** — 图片、视频、文件和语音消息
- **AES-128-ECB 加密 CDN** — 所有媒体传输自动加解密
- **上下文令牌持久化** — 基于磁盘的回复连续性，跨重启保持
- **Markdown 格式化** — 标题、表格和代码块会重新格式化以适配微信阅读
- **智能消息分块** — 消息在长度限制内保持为单条气泡；仅超大消息在逻辑边界处拆分
- **输入指示器** — 在 Agent 处理消息时，微信客户端显示"正在输入..."状态
- **SSRF 防护** — 下载前会验证出站媒体 URL
- **消息去重** — 5 分钟滑动窗口防止重复处理
- **自动重试与退避** — 从临时 API 错误中恢复

## 配置选项

在 `config.yaml` 的 `platforms.weixin.extra` 下设置：

| 键 | 默认值 | 描述 |
|-----|---------|-------------|
| `account_id` | — | iLink Bot 账号 ID（必填） |
| `token` | — | iLink Bot 令牌（必填，从二维码登录自动保存） |
| `base_url` | `https://ilinkai.weixin.qq.com` | iLink API 基础 URL |
| `cdn_base_url` | `https://novac2c.cdn.weixin.qq.com/c2c` | 媒体传输的 CDN 基础 URL |
| `dm_policy` | `open` | 私聊访问策略：`open`、`allowlist`、`disabled`、`pairing` |
| `group_policy` | `disabled` | 群聊访问策略：`open`、`allowlist`、`disabled` |
| `allow_from` | `[]` | 允许私聊的用户 ID（当 dm_policy=allowlist 时） |
| `group_allow_from` | `[]` | 允许的群 ID（当 group_policy=allowlist 时） |
| `split_multiline_messages` | `false` | 设为 `true` 时，将多行回复拆分为多条聊天消息（旧版行为）。设为 `false` 时，多行回复保持为一条消息，除非超过长度限制。 |

## 访问策略

### 私聊策略

控制谁可以向机器人发送私信：

| 值 | 行为 |
|-------|----------|
| `open` | 任何人都可以向机器人发私信（默认） |
| `allowlist` | 只有 `allow_from` 中的用户 ID 可以发私信 |
| `disabled` | 忽略所有私信 |
| `pairing` | 配对模式（用于初始设置） |

```bash
WEIXIN_DM_POLICY=allowlist
WEIXIN_ALLOWED_USERS=user_id_1,user_id_2
```

### 群聊策略

控制机器人在哪些群中响应：

| 值 | 行为 |
|-------|----------|
| `open` | 机器人在所有群中响应 |
| `allowlist` | 机器人仅在 `group_allow_from` 列表中的群中响应 |
| `disabled` | 忽略所有群消息（默认） |

```bash
WEIXIN_GROUP_POLICY=allowlist
WEIXIN_GROUP_ALLOWED_USERS=group_id_1,group_id_2
```

:::note
微信的默认群聊策略为 `disabled`（与企业微信默认 `open` 不同）。这是刻意设计的，因为个人微信账号可能加入了很多群。
:::

## 媒体支持

### 入站（接收）

适配器从用户接收媒体附件，从微信 CDN 下载，解密后缓存到本地供 Agent 处理：

| 类型 | 处理方式 |
|------|-----------------| 
| **图片** | 下载、AES 解密，缓存为 JPEG。 |
| **视频** | 下载、AES 解密，缓存为 MP4。 |
| **文件** | 下载、AES 解密，并缓存。保留原始文件名。 |
| **语音** | 如果有文字转写，则提取为文本。否则下载音频（SILK 格式）并缓存。 |

**引用消息：** 引用（回复）消息中的媒体也会被提取，以便 Agent 了解用户回复的上下文。

### AES-128-ECB 加密 CDN

微信媒体文件通过加密 CDN 传输。适配器透明处理：

- **入站：** 使用 `encrypted_query_param` URL 从 CDN 下载加密媒体，然后使用消息负载中提供的每文件密钥进行 AES-128-ECB 解密。
- **出站：** 文件使用随机 AES-128-ECB 密钥在本地加密，上传到 CDN，加密引用包含在出站消息中。
- AES 密钥为 16 字节（128 位）。密钥可能以原始 base64 或十六进制编码到达——适配器兼容两种格式。
- 需要 `cryptography` Python 包。

无需配置——加解密自动进行。

### 出站（发送）

| 方法 | 发送内容 |
|--------|--------------|
| `send` | 带 Markdown 格式的文本消息 | 
| `send_image` / `send_image_file` | 原生图片消息（通过 CDN 上传） |
| `send_document` | 文件附件（通过 CDN 上传） |
| `send_video` | 视频消息（通过 CDN 上传） |

所有出站媒体都经过加密 CDN 上传流程：

1. 生成随机 AES-128 密钥
2. 使用 AES-128-ECB + PKCS#7 填充加密文件
3. 从 iLink API 请求上传 URL（`getuploadurl`）
4. 将密文上传到 CDN
5. 发送包含加密媒体引用的消息

## 上下文令牌持久化

iLink Bot API 要求在每条出站消息中回传给定对端的 `context_token`。适配器维护一个基于磁盘的上下文令牌存储：

- 令牌按账号+对端保存到 `~/.hermes/weixin/accounts/<account_id>.context-tokens.json`
- 启动时恢复之前保存的令牌
- 每条入站消息更新该发送者的存储令牌
- 出站消息自动包含最新的上下文令牌

这确保了即使网关重启后也能保持回复连续性。

## Markdown 格式化

微信个人聊天不原生支持完整 Markdown 渲染。适配器会重新格式化内容以提高可读性：

- **标题**（`# Title`）→ 转换为 `【Title】`（一级）或 `**Title**`（二级及以上）
- **表格** → 重新格式化为带标签的键值列表（例如 `- 列: 值`）
- **代码围栏** → 保持原样（微信对此渲染效果尚可）
- **过多空行** → 压缩为双换行

## 消息分块

消息在平台限制内时作为单条聊天消息发送。仅超大消息才会拆分发送：

- 最大消息长度：**4000 字符**
- 未超限的消息即使包含多段或换行也保持完整
- 超大消息在逻辑边界（段落、空行、代码围栏）处拆分
- 代码围栏尽可能保持完整（除非围栏本身超过限制，否则不会在块内拆分）
- 超大的单个块回退到基础适配器的截断逻辑
- 发送多个分块时，分块间有 0.3 秒延迟以防止微信速率限制丢弃

## 输入指示器

适配器在微信客户端显示输入状态：

1. 收到消息时，适配器通过 `getconfig` API 获取 `typing_ticket`
2. 输入票据按用户缓存 10 分钟
3. `send_typing` 发送开始输入信号；`stop_typing` 发送停止输入信号
4. 网关在 Agent 处理消息时自动触发输入指示器

## 长轮询连接

适配器使用 HTTP 长轮询（非 WebSocket）接收消息：

### 工作原理

1. **连接：** 验证凭证并启动轮询循环
2. **轮询：** 调用 `getupdates`，超时 35 秒；服务器保持请求直到有消息到达或超时
3. **分发：** 入站消息通过 `asyncio.create_task` 并发分发
4. **同步缓冲区：** 持久化的同步游标（`get_updates_buf`）保存到磁盘，以便重启后从正确位置恢复

### 重试行为

API 出错时，适配器使用简单的重试策略：

| 条件 | 行为 |
|-----------|----------|
| 临时错误（第 1-2 次） | 2 秒后重试 |
| 重复错误（3 次以上） | 退避 30 秒，然后重置计数器 |
| 会话过期（`errcode=-14`） | 暂停 10 分钟（可能需要重新登录） |
| 超时 | 立即重新轮询（正常的长轮询行为） |

### 去重

入站消息使用消息 ID 进行去重，窗口期为 5 分钟。这可以防止网络故障或重叠轮询响应期间的重复处理。

### 令牌锁

同一时间只有一个微信网关实例可以使用给定令牌。适配器在启动时获取作用域锁，在关闭时释放。如果另一个网关已在使用相同令牌，启动将失败并显示提示性错误消息。

## 所有环境变量

| 变量 | 必填 | 默认值 | 描述 |
|----------|----------|---------|-------------|
| `WEIXIN_ACCOUNT_ID` | ✅ | — | iLink Bot 账号 ID（来自二维码登录） |
| `WEIXIN_TOKEN` | ✅ | — | iLink Bot 令牌（从二维码登录自动保存） |
| `WEIXIN_BASE_URL` | — | `https://ilinkai.weixin.qq.com` | iLink API 基础 URL |
| `WEIXIN_CDN_BASE_URL` | — | `https://novac2c.cdn.weixin.qq.com/c2c` | 媒体传输的 CDN 基础 URL |
| `WEIXIN_DM_POLICY` | — | `open` | 私聊访问策略：`open`、`allowlist`、`disabled`、`pairing` |
| `WEIXIN_GROUP_POLICY` | — | `disabled` | 群聊访问策略：`open`、`allowlist`、`disabled` |
| `WEIXIN_ALLOWED_USERS` | — | _（空）_ | 逗号分隔的私聊允许列表用户 ID |
| `WEIXIN_GROUP_ALLOWED_USERS` | — | _（空）_ | 逗号分隔的群聊允许列表群 ID |
| `WEIXIN_HOME_CHANNEL` | — | — | 用于定时任务/通知输出的聊天 ID |
| `WEIXIN_HOME_CHANNEL_NAME` | — | `Home` | 主频道的显示名称 |
| `WEIXIN_ALLOW_ALL_USERS` | — | — | 网关级标志，允许所有用户（设置向导使用） |

## 故障排除

| 问题 | 解决方法 |
|---------|-----|
| `Weixin startup failed: aiohttp and cryptography are required` | 安装两者：`pip install aiohttp cryptography` |
| `Weixin startup failed: WEIXIN_TOKEN is required` | 运行 `hermes gateway setup` 完成二维码登录，或手动设置 `WEIXIN_TOKEN` |
| `Weixin startup failed: WEIXIN_ACCOUNT_ID is required` | 在 `.env` 中设置 `WEIXIN_ACCOUNT_ID` 或运行 `hermes gateway setup` |
| `Another local Hermes gateway is already using this Weixin token` | 先停止另一个网关实例——每个令牌只允许一个轮询器 |
| 会话过期（`errcode=-14`） | 登录会话已过期。重新运行 `hermes gateway setup` 扫描新二维码 |
| 设置过程中二维码过期 | 二维码最多自动刷新 3 次。如果持续过期，请检查网络连接 |
| 机器人不回复私信 | 检查 `WEIXIN_DM_POLICY`——如果设为 `allowlist`，发送者必须在 `WEIXIN_ALLOWED_USERS` 中 |
| 机器人忽略群消息 | 群聊策略默认为 `disabled`。设置 `WEIXIN_GROUP_POLICY=open` 或 `allowlist` |
| 媒体下载/上传失败 | 确保已安装 `cryptography`。检查对 `novac2c.cdn.weixin.qq.com` 的网络访问 |
| `Blocked unsafe URL (SSRF protection)` | 出站媒体 URL 指向私有/内部地址。只允许公网 URL |
| 语音消息显示为文本 | 如果微信提供了转写，适配器会使用文本。这是预期行为 |
| 消息重复出现 | 适配器按消息 ID 去重。如果看到重复消息，请检查是否有多个网关实例在运行 |
| `iLink POST ... HTTP 4xx/5xx` | iLink 服务的 API 错误。检查令牌有效性和网络连接 |
| 终端二维码无法渲染 | 安装 `qrcode`：`pip install qrcode`。或者打开二维码上方打印的 URL |
