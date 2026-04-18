---
sidebar_position: 2
title: "添加工具"
description: "如何向 Hermes Agent 添加新工具 — Schema、处理函数、注册和工具集"
---

# 添加工具

在编写工具之前，先问自己：**它应该是一个[技能](creating-skills.md)吗？**

当功能可以通过指令 + shell 命令 + 现有工具表达时，做成**技能**（arXiv 搜索、git 工作流、Docker 管理、PDF 处理）。

当功能需要端到端集成 API 密钥、自定义处理逻辑、二进制数据处理或流式传输时，做成**工具**（浏览器自动化、TTS、视觉分析）。

## 概述

添加一个工具需要修改 **2 个文件**：

1. **`tools/your_tool.py`** — 处理函数、Schema、检查函数、`registry.register()` 调用
2. **`toolsets.py`** — 将工具名称添加到 `_HERMES_CORE_TOOLS`（或特定工具集）

任何在 `tools/*.py` 文件中包含顶层 `registry.register()` 调用的文件都会在启动时自动发现 — 无需手动维护导入列表。

## 步骤 1：创建工具文件

每个工具文件遵循相同的结构：

```python
# tools/weather_tool.py
"""Weather Tool -- 查询指定位置的当前天气。"""

import json
import os
import logging

logger = logging.getLogger(__name__)


# --- 可用性检查 ---

def check_weather_requirements() -> bool:
    """如果工具的依赖可用则返回 True。"""
    return bool(os.getenv("WEATHER_API_KEY"))


# --- 处理函数 ---

def weather_tool(location: str, units: str = "metric") -> str:
    """获取指定位置的天气。返回 JSON 字符串。"""
    api_key = os.getenv("WEATHER_API_KEY")
    if not api_key:
        return json.dumps({"error": "WEATHER_API_KEY not configured"})
    try:
        # ... 调用天气 API ...
        return json.dumps({"location": location, "temp": 22, "units": units})
    except Exception as e:
        return json.dumps({"error": str(e)})


# --- Schema ---

WEATHER_SCHEMA = {
    "name": "weather",
    "description": "Get current weather for a location.",
    "parameters": {
        "type": "object",
        "properties": {
            "location": {
                "type": "string",
                "description": "City name or coordinates (e.g. 'London' or '51.5,-0.1')"
            },
            "units": {
                "type": "string",
                "enum": ["metric", "imperial"],
                "description": "Temperature units (default: metric)",
                "default": "metric"
            }
        },
        "required": ["location"]
    }
}


# --- 注册 ---

from tools.registry import registry

registry.register(
    name="weather",
    toolset="weather",
    schema=WEATHER_SCHEMA,
    handler=lambda args, **kw: weather_tool(
        location=args.get("location", ""),
        units=args.get("units", "metric")),
    check_fn=check_weather_requirements,
    requires_env=["WEATHER_API_KEY"],
)
```

### 关键规则

:::danger 重要
- 处理函数**必须**返回 JSON 字符串（通过 `json.dumps()`），不要返回原始字典
- 错误**必须**以 `{"error": "message"}` 形式返回，不要抛出异常
- `check_fn` 在构建工具定义时被调用 — 如果返回 `False`，该工具将被静默排除
- `handler` 接收 `(args: dict, **kwargs)`，其中 `args` 是 LLM 的工具调用参数
:::

## 步骤 2：添加到工具集

在 `toolsets.py` 中添加工具名称：

```python
# 如果应在所有平台（CLI + 消息）上可用：
_HERMES_CORE_TOOLS = [
    ...
    "weather",  # <-- 在此添加
]

# 或创建一个新的独立工具集：
"weather": {
    "description": "Weather lookup tools",
    "tools": ["weather"],
    "includes": []
},
```

## ~~步骤 3：添加发现导入~~（不再需要）

包含顶层 `registry.register()` 调用的工具模块会被 `tools/registry.py` 中的 `discover_builtin_tools()` 自动发现。无需维护手动导入列表 — 只需在 `tools/` 中创建文件，启动时即可被识别。

## 异步处理函数

如果处理函数需要异步代码，使用 `is_async=True` 标记：

```python
async def weather_tool_async(location: str) -> str:
    async with aiohttp.ClientSession() as session:
        ...
    return json.dumps(result)

registry.register(
    name="weather",
    toolset="weather",
    schema=WEATHER_SCHEMA,
    handler=lambda args, **kw: weather_tool_async(args.get("location", "")),
    check_fn=check_weather_requirements,
    is_async=True,  # 注册表会自动调用 _run_async()
)
```

注册表透明地处理异步桥接 — 你无需自己调用 `asyncio.run()`。

## 需要 task_id 的处理函数

管理每会话状态的工具通过 `**kwargs` 接收 `task_id`：

```python
def _handle_weather(args, **kw):
    task_id = kw.get("task_id")
    return weather_tool(args.get("location", ""), task_id=task_id)

registry.register(
    name="weather",
    ...
    handler=_handle_weather,
)
```

## Agent 循环拦截的工具

某些工具（`todo`、`memory`、`session_search`、`delegate_task`）需要访问每会话的 Agent 状态。这些工具在到达注册表之前由 `run_agent.py` 拦截。注册表仍然保存它们的 Schema，但如果拦截被绕过，`dispatch()` 会返回回退错误。

## 可选：设置向导集成

如果工具需要 API 密钥，将其添加到 `hermes_cli/config.py`：

```python
OPTIONAL_ENV_VARS = {
    ...
    "WEATHER_API_KEY": {
        "description": "Weather API key for weather lookup",
        "prompt": "Weather API key",
        "url": "https://weatherapi.com/",
        "tools": ["weather"],
        "password": True,
    },
}
```

## 检查清单

- [ ] 创建工具文件，包含处理函数、Schema、检查函数和注册
- [ ] 在 `toolsets.py` 中添加到适当的工具集
- [ ] 在 `model_tools.py` 中添加发现导入
- [ ] 处理函数返回 JSON 字符串，错误以 `{"error": "..."}` 形式返回
- [ ] 可选：在 `hermes_cli/config.py` 的 `OPTIONAL_ENV_VARS` 中添加 API 密钥
- [ ] 可选：在 `toolset_distributions.py` 中添加用于批量处理
- [ ] 使用 `hermes chat -q "Use the weather tool for London"` 测试
