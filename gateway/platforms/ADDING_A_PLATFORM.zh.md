# 添加新的消息平台

将新消息平台集成到 Hermes 网关的检查清单。
在构建新适配器时将此作为参考——这里的每一项都是代码库中存在的真实集成点。遗漏任何一项都会导致功能损坏、缺少特性或行为不一致。

---

## 1. 核心适配器（`gateway/platforms/<platform>.py`）

适配器是 `gateway/platforms/base.py` 中 `BasePlatformAdapter` 的子类。

### 必须实现的方法

| 方法 | 用途 |
|------|------|
| `__init__(self, config)` | 解析配置，初始化状态。调用 `super().__init__(config, Platform.YOUR_PLATFORM)` |
| `connect() -> bool` | 连接到平台，启动监听器。成功返回 True |
| `disconnect()` | 停止监听器，关闭连接，取消任务 |
| `send(chat_id, text, ...) -> SendResult` | 发送文本消息 |
| `send_typing(chat_id)` | 发送输入中指示器 |
| `send_image(chat_id, image_url, caption) -> SendResult` | 发送图片 |
| `get_chat_info(chat_id) -> dict` | 返回 `{name, type, chat_id}` 的字典 |

### 可选方法（基类中有默认桩实现）

| 方法 | 用途 |
|------|------|
| `send_document(chat_id, path, caption)` | 发送文件附件 |
| `send_voice(chat_id, path)` | 发送语音消息 |
| `send_video(chat_id, path, caption)` | 发送视频 |
| `send_animation(chat_id, path, caption)` | 发送 GIF/动画 |
| `send_image_file(chat_id, path, caption)` | 从本地文件发送图片 |

### 必须实现的函数

```python
def check_<platform>_requirements() -> bool:
    """检查此平台的依赖是否可用。"""
```

### 需要遵循的关键模式

- 使用 `self.build_source(...)` 构造 `SessionSource` 对象
- 调用 `self.handle_message(event)` 将入站消息分发到网关
- 使用 `MessageEvent`、`MessageType`、`SendResult` 来自 base
- 使用 `cache_image_from_bytes`、`cache_audio_from_bytes`、`cache_document_from_bytes` 处理附件
- 过滤自身消息（防止回复循环）
- 如果平台有同步/回显消息，过滤它们
- 在所有日志输出中编辑敏感标识符（电话号码、令牌）
- 对流式连接实现指数退避 + 抖动的重连
- 如果平台有消息大小限制，设置 `MAX_MESSAGE_LENGTH`

---

## 2. Platform 枚举（`gateway/config.py`）

将平台添加到 `Platform` 枚举：

```python
class Platform(Enum):
    ...
    YOUR_PLATFORM = "your_platform"
```

在 `_apply_env_overrides()` 中添加环境变量加载：

```python
# 你的平台
your_token = os.getenv("YOUR_PLATFORM_TOKEN")
if your_token:
    if Platform.YOUR_PLATFORM not in config.platforms:
        config.platforms[Platform.YOUR_PLATFORM] = PlatformConfig()
    config.platforms[Platform.YOUR_PLATFORM].enabled = True
    config.platforms[Platform.YOUR_PLATFORM].token = your_token
```

如果你的平台不使用 token/api_key（例如 WhatsApp 使用 `enabled` 标志，Signal 使用 `extra` 字典），更新 `get_connected_platforms()`。

---

## 3. 适配器工厂（`gateway/run.py`）

在 `_create_adapter()` 中添加：

```python
elif platform == Platform.YOUR_PLATFORM:
    from gateway.platforms.your_platform import YourAdapter, check_your_requirements
    if not check_your_requirements():
        logger.warning("Your Platform: dependencies not met")
        return None
    return YourAdapter(config)
```

---

## 4. 授权映射（`gateway/run.py`）

在 `_is_user_authorized()` 的两个字典中都添加：

```python
platform_env_map = {
    ...
    Platform.YOUR_PLATFORM: "YOUR_PLATFORM_ALLOWED_USERS",
}
platform_allow_all_map = {
    ...
    Platform.YOUR_PLATFORM: "YOUR_PLATFORM_ALLOW_ALL_USERS",
}
```

---

## 5. 会话源（`gateway/session.py`）

如果你的平台需要额外的身份字段（例如 Signal 除了电话号码还有 UUID），将其作为 `Optional` 默认值添加到 `SessionSource` 数据类，并更新 base.py 中的 `to_dict()`、`from_dict()` 和 `build_source()`。

---

## 6. 系统提示词提示（`agent/prompt_builder.py`）

添加 `PLATFORM_HINTS` 条目，让代理知道它在什么平台上：

```python
PLATFORM_HINTS = {
    ...
    "your_platform": (
        "You are on Your Platform. "
        "Describe formatting capabilities, media support, etc."
    ),
}
```

没有这个，代理不会知道它在你的平台上，可能会使用不当的格式（例如在不渲染 markdown 的平台上使用 markdown）。

---

## 7. 工具集（`toolsets.py`）

为你的平台添加命名工具集：

```python
"hermes-your-platform": {
    "description": "Your Platform bot toolset",
    "tools": _HERMES_CORE_TOOLS,
    "includes": []
},
```

并将其添加到 `hermes-gateway` 组合中：

```python
"hermes-gateway": {
    "includes": [..., "hermes-your-platform"]
}
```

---

## 8. 定时任务投递（`cron/scheduler.py`）

在 `_deliver_result()` 中添加到 `platform_map`：

```python
platform_map = {
    ...
    "your_platform": Platform.YOUR_PLATFORM,
}
```

没有这个，`cronjob(action="create", deliver="your_platform", ...)` 会静默失败。

---

## 9. 发送消息工具（`tools/send_message_tool.py`）

在 `send_message_tool()` 中添加到 `platform_map`：

```python
platform_map = {
    ...
    "your_platform": Platform.YOUR_PLATFORM,
}
```

在 `_send_to_platform()` 中添加路由：

```python
elif platform == Platform.YOUR_PLATFORM:
    return await _send_your_platform(pconfig, chat_id, message)
```

实现 `_send_your_platform()` — 一个独立的异步函数，无需完整适配器即可发送单条消息（供定时任务和网关进程外的 send_message 工具使用）。

更新工具 schema 的 `target` 描述以包含你的平台示例。

---

## 10. 定时任务工具 Schema（`tools/cronjob_tools.py`）

更新 `deliver` 参数描述和文档字符串，将你的平台作为投递选项提及。

---

## 11. 频道目录（`gateway/channel_directory.py`）

如果你的平台无法枚举聊天（大多数不能），将其添加到基于会话的发现列表：

```python
for plat_name in ("telegram", "whatsapp", "signal", "your_platform"):
```

---

## 12. 状态显示（`hermes_cli/status.py`）

在消息平台部分的 `platforms` 字典中添加：

```python
platforms = {
    ...
    "Your Platform": ("YOUR_PLATFORM_TOKEN", "YOUR_PLATFORM_HOME_CHANNEL"),
}
```

---

## 13. 网关设置向导（`hermes_cli/gateway.py`）

添加到 `_PLATFORMS` 列表：

```python
{
    "key": "your_platform",
    "label": "Your Platform",
    "emoji": "📱",
    "token_var": "YOUR_PLATFORM_TOKEN",
    "setup_instructions": [...],
    "vars": [...],
}
```

如果你的平台需要自定义设置逻辑（连接测试、二维码、策略选择），添加 `_setup_your_platform()` 函数并在平台选择开关中路由到它。

如果你的平台的"已配置"检查与标准 `bool(get_env_value(token_var))` 不同，更新 `_platform_status()`。

---

## 14. 电话/ID 编辑（`agent/redact.py`）

如果你的平台使用敏感标识符（电话号码等），在 `agent/redact.py` 中添加正则表达式模式和编辑函数。这确保标识符在所有日志输出中被掩码，而不仅是你的适配器日志。

---

## 15. 文档

| 文件 | 需要更新的内容 |
|------|---------------|
| `README.md` | 功能表中的平台列表 + 文档表 |
| `AGENTS.md` | 网关描述 + 环境变量配置部分 |
| `website/docs/user-guide/messaging/<platform>.md` | **新建** — 完整的设置指南（参考现有平台文档作为模板） |
| `website/docs/user-guide/messaging/index.md` | 架构图、工具集表、安全示例、下一步链接 |
| `website/docs/reference/environment-variables.md` | 该平台的所有环境变量 |

---

## 16. 测试（`tests/gateway/test_<platform>.py`）

建议的测试覆盖：

- Platform 枚举存在且值正确
- 通过 `_apply_env_overrides` 从环境变量加载配置
- 适配器初始化（配置解析、允许列表处理、默认值）
- 辅助函数（编辑、解析、文件类型检测）
- 会话源往返（to_dict → from_dict）
- 授权集成（平台在允许列表映射中）
- 发送消息工具路由（平台在 platform_map 中）

可选但有价值：
- 消息处理流程的异步测试（mock 平台 API）
- SSE/WebSocket 重连逻辑
- 附件处理
- 群组消息过滤

---

## 快速验证

实现完成后，验证：

```bash
# 所有测试通过
python -m pytest tests/ -q

# 搜索你的平台名称以查找任何遗漏的集成点
grep -r "telegram\|discord\|whatsapp\|slack" gateway/ tools/ agent/ cron/ hermes_cli/ toolsets.py \
  --include="*.py" -l | sort -u
# 检查输出中的每个文件——如果它提到了其他平台但没有你的，说明你遗漏了
```
