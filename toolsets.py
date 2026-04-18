#!/usr/bin/env python3
"""
工具集模块 (Toolsets Module)

本模块提供了一个灵活的系统，用于定义和管理工具别名/工具集。
工具集允许你将工具分组以适应特定场景，可以由单个工具或其他工具集组合而成。

功能特性:
- 定义包含特定工具的自定义工具集
- 通过组合其他工具集来构建工具集
- 内置常见使用场景的工具集
- 易于扩展新的工具集
- 支持动态工具集解析

使用方法:
    from toolsets import get_toolset, resolve_toolset, get_all_toolsets

    # 获取特定工具集的工具
    tools = get_toolset("research")

    # 解析工具集以获取所有工具名称（包括来自组合工具集的）
    all_tools = resolve_toolset("full_stack")
"""

from typing import List, Dict, Any, Set, Optional


# CLI 和所有消息平台工具集共享的核心工具列表。
# 编辑此列表即可同时更新所有平台。
_HERMES_CORE_TOOLS = [
    # 网络搜索
    "web_search", "web_extract",
    # 终端 + 进程管理
    "terminal", "process",
    # 文件操作
    "read_file", "write_file", "patch", "search_files",
    # 图像分析 + 图像生成 + 图生图
    "vision_analyze", "image_generate", "image_transform",
    # 技能
    "skills_list", "skill_view", "skill_manage",
    # 浏览器自动化
    "browser_navigate", "browser_snapshot", "browser_click",
    "browser_type", "browser_scroll", "browser_back",
    "browser_press", "browser_get_images",
    "browser_vision", "browser_console",
    # 文本转语音
    "text_to_speech",
    # 规划与记忆
    "todo", "memory",
    # 会话历史搜索
    "session_search",
    # 澄清提问
    "clarify",
    # 代码执行 + 任务委派
    "execute_code", "delegate_task",
    # 定时任务管理
    "cronjob",
    # 跨平台消息发送（通过 check_fn 检测网关是否运行来控制可用性）
    "send_message",
    # Home Assistant 智能家居控制（通过 check_fn 检测 HASS_TOKEN 来控制可用性）
    "ha_list_entities", "ha_get_state", "ha_list_services", "ha_call_service",
]


# 核心工具集定义
# 可以包含单个工具或引用其他工具集
TOOLSETS = {
    # 基础工具集 - 单个工具类别
    "web": {
        "description": "Web research and content extraction tools",
        "tools": ["web_search", "web_extract"],
        "includes": []  # 不包含其他工具集
    },

    "search": {
        "description": "Web search only (no content extraction/scraping)",
        "tools": ["web_search"],
        "includes": []
    },

    "vision": {
        "description": "Image analysis and vision tools",
        "tools": ["vision_analyze"],
        "includes": []
    },

    "image_gen": {
        "description": "Creative generation tools (images)",
        "tools": ["image_generate"],
        "includes": []
    },

    "image_transform": {
        "description": "AI image transformation and editing (img2img)",
        "tools": ["image_transform"],
        "includes": []
    },

    "terminal": {
        "description": "Terminal/command execution and process management tools",
        "tools": ["terminal", "process"],
        "includes": []
    },

    "moa": {
        "description": "Advanced reasoning and problem-solving tools",
        "tools": ["mixture_of_agents"],
        "includes": []
    },

    "skills": {
        "description": "Access, create, edit, and manage skill documents with specialized instructions and knowledge",
        "tools": ["skills_list", "skill_view", "skill_manage"],
        "includes": []
    },

    "browser": {
        "description": "Browser automation for web interaction (navigate, click, type, scroll, iframes, hold-click) with web search for finding URLs",
        "tools": [
            "browser_navigate", "browser_snapshot", "browser_click",
            "browser_type", "browser_scroll", "browser_back",
            "browser_press", "browser_get_images",
            "browser_vision", "browser_console", "web_search"
        ],
        "includes": []
    },

    "cronjob": {
        "description": "Cronjob management tool - create, list, update, pause, resume, remove, and trigger scheduled tasks",
        "tools": ["cronjob"],
        "includes": []
    },

    "messaging": {
        "description": "Cross-platform messaging: send messages to Telegram, Discord, Slack, SMS, etc.",
        "tools": ["send_message"],
        "includes": []
    },

    "rl": {
        "description": "RL training tools for running reinforcement learning on Tinker-Atropos",
        "tools": [
            "rl_list_environments", "rl_select_environment",
            "rl_get_current_config", "rl_edit_config",
            "rl_start_training", "rl_check_status",
            "rl_stop_training", "rl_get_results",
            "rl_list_runs", "rl_test_inference"
        ],
        "includes": []
    },

    "file": {
        "description": "File manipulation tools: read, write, patch (with fuzzy matching), and search (content + files)",
        "tools": ["read_file", "write_file", "patch", "search_files"],
        "includes": []
    },

    "tts": {
        "description": "Text-to-speech: convert text to audio with Edge TTS (free), ElevenLabs, OpenAI, or xAI",
        "tools": ["text_to_speech"],
        "includes": []
    },

    "todo": {
        "description": "Task planning and tracking for multi-step work",
        "tools": ["todo"],
        "includes": []
    },

    "memory": {
        "description": "Persistent memory across sessions (personal notes + user profile)",
        "tools": ["memory"],
        "includes": []
    },

    "session_search": {
        "description": "Search and recall past conversations with summarization",
        "tools": ["session_search"],
        "includes": []
    },

    "clarify": {
        "description": "Ask the user clarifying questions (multiple-choice or open-ended)",
        "tools": ["clarify"],
        "includes": []
    },

    "code_execution": {
        "description": "Run Python scripts that call tools programmatically (reduces LLM round trips)",
        "tools": ["execute_code"],
        "includes": []
    },

    "delegation": {
        "description": "Spawn subagents with isolated context for complex subtasks",
        "tools": ["delegate_task"],
        "includes": []
    },

    # "honcho" 工具集已移除 —— Honcho 现在是一个记忆提供者插件。
    # 工具通过 MemoryManager 注入，而非通过工具集系统。

    "homeassistant": {
        "description": "Home Assistant smart home control and monitoring",
        "tools": ["ha_list_entities", "ha_get_state", "ha_list_services", "ha_call_service"],
        "includes": []
    },


    # 面向特定场景的工具集

    "debugging": {
        "description": "Debugging and troubleshooting toolkit",
        "tools": ["terminal", "process"],
        "includes": ["web", "file"]  # 用于搜索错误消息和解决方案，以及文件操作
    },

    "safe": {
        "description": "Safe toolkit without terminal access",
        "tools": [],
        "includes": ["web", "vision", "image_gen"]
    },

    # ==========================================================================
    # 完整的 Hermes 工具集（CLI + 消息平台）
    #
    # 所有平台共享相同的核心工具（包括 send_message，
    # 通过 check_fn 检测网关是否运行来控制可用性）。
    # ==========================================================================

    "hermes-acp": {
        "description": "Editor integration (VS Code, Zed, JetBrains) — coding-focused tools without messaging, audio, or clarify UI",
        "tools": [
            "web_search", "web_extract",
            "terminal", "process",
            "read_file", "write_file", "patch", "search_files",
            "vision_analyze",
            "skills_list", "skill_view", "skill_manage",
            "browser_navigate", "browser_snapshot", "browser_click",
            "browser_type", "browser_scroll", "browser_back",
            "browser_press", "browser_get_images",
            "browser_vision", "browser_console",
            "todo", "memory",
            "session_search",
            "execute_code", "delegate_task",
        ],
        "includes": []
    },

    "hermes-api-server": {
        "description": "OpenAI-compatible API server — full agent tools accessible via HTTP (no interactive UI tools like clarify or send_message)",
        "tools": [
            # 网络搜索
            "web_search", "web_extract",
            # 终端 + 进程管理
            "terminal", "process",
            # 文件操作
            "read_file", "write_file", "patch", "search_files",
            # 图像分析 + 图像生成
            "vision_analyze", "image_generate",
            # 技能
            "skills_list", "skill_view", "skill_manage",
            # 浏览器自动化
            "browser_navigate", "browser_snapshot", "browser_click",
            "browser_type", "browser_scroll", "browser_back",
            "browser_press", "browser_get_images",
            "browser_vision", "browser_console",
            # 规划与记忆
            "todo", "memory",
            # 会话历史搜索
            "session_search",
            # 代码执行 + 任务委派
            "execute_code", "delegate_task",
            # 定时任务管理
            "cronjob",
            # Home Assistant 智能家居控制（通过 check_fn 检测 HASS_TOKEN）
            "ha_list_entities", "ha_get_state", "ha_list_services", "ha_call_service",

        ],
        "includes": []
    },

    "hermes-cli": {
        "description": "Full interactive CLI toolset - all default tools plus cronjob management",
        "tools": _HERMES_CORE_TOOLS,
        "includes": []
    },

    "hermes-telegram": {
        "description": "Telegram bot toolset - full access for personal use (terminal has safety checks)",
        "tools": _HERMES_CORE_TOOLS,
        "includes": []
    },

    "hermes-discord": {
        "description": "Discord bot toolset - full access (terminal has safety checks via dangerous command approval)",
        "tools": _HERMES_CORE_TOOLS,
        "includes": []
    },

    "hermes-whatsapp": {
        "description": "WhatsApp bot toolset - similar to Telegram (personal messaging, more trusted)",
        "tools": _HERMES_CORE_TOOLS,
        "includes": []
    },

    "hermes-slack": {
        "description": "Slack bot toolset - full access for workspace use (terminal has safety checks)",
        "tools": _HERMES_CORE_TOOLS,
        "includes": []
    },

    "hermes-signal": {
        "description": "Signal bot toolset - encrypted messaging platform (full access)",
        "tools": _HERMES_CORE_TOOLS,
        "includes": []
    },

    "hermes-bluebubbles": {
        "description": "BlueBubbles iMessage bot toolset - Apple iMessage via local BlueBubbles server",
        "tools": _HERMES_CORE_TOOLS,
        "includes": []
    },

    "hermes-homeassistant": {
        "description": "Home Assistant bot toolset - smart home event monitoring and control",
        "tools": _HERMES_CORE_TOOLS,
        "includes": []
    },

    "hermes-email": {
        "description": "Email bot toolset - interact with Hermes via email (IMAP/SMTP)",
        "tools": _HERMES_CORE_TOOLS,
        "includes": []
    },

    "hermes-mattermost": {
        "description": "Mattermost bot toolset - self-hosted team messaging (full access)",
        "tools": _HERMES_CORE_TOOLS,
        "includes": []
    },

    "hermes-matrix": {
        "description": "Matrix bot toolset - decentralized encrypted messaging (full access)",
        "tools": _HERMES_CORE_TOOLS,
        "includes": []
    },

    "hermes-dingtalk": {
        "description": "DingTalk bot toolset - enterprise messaging platform (full access)",
        "tools": _HERMES_CORE_TOOLS,
        "includes": []
    },

    "hermes-feishu": {
        "description": "Feishu/Lark bot toolset - enterprise messaging via Feishu/Lark (full access)",
        "tools": _HERMES_CORE_TOOLS,
        "includes": []
    },

    "hermes-weixin": {
        "description": "Weixin bot toolset - personal WeChat messaging via iLink (full access)",
        "tools": _HERMES_CORE_TOOLS,
        "includes": []
    },

    "hermes-qqbot": {
        "description": "QQBot toolset - QQ messaging via Official Bot API v2 (full access)",
        "tools": _HERMES_CORE_TOOLS,
        "includes": []
    },

    "hermes-wecom": {
        "description": "WeCom bot toolset - enterprise WeChat messaging (full access)",
        "tools": _HERMES_CORE_TOOLS,
        "includes": []
    },

    "hermes-wecom-callback": {
        "description": "WeCom callback toolset - enterprise self-built app messaging (full access)",
        "tools": _HERMES_CORE_TOOLS,
        "includes": []
    },

    "hermes-sms": {
        "description": "SMS bot toolset - interact with Hermes via SMS (Twilio)",
        "tools": _HERMES_CORE_TOOLS,
        "includes": []
    },

    "hermes-webhook": {
        "description": "Webhook toolset - receive and process external webhook events",
        "tools": _HERMES_CORE_TOOLS,
        "includes": []
    },

    "hermes-gateway": {
        "description": "Gateway toolset - union of all messaging platform tools",
        "tools": [],
        "includes": ["hermes-telegram", "hermes-discord", "hermes-whatsapp", "hermes-slack", "hermes-signal", "hermes-bluebubbles", "hermes-homeassistant", "hermes-email", "hermes-sms", "hermes-mattermost", "hermes-matrix", "hermes-dingtalk", "hermes-feishu", "hermes-wecom", "hermes-wecom-callback", "hermes-weixin", "hermes-qqbot", "hermes-webhook"]
    }
}



def get_toolset(name: str) -> Optional[Dict[str, Any]]:
    """
    根据名称获取工具集定义。

    参数:
        name (str): 工具集名称

    返回:
        Dict: 包含 description、tools 和 includes 的工具集定义
        None: 如果未找到该工具集
    """
    toolset = TOOLSETS.get(name)
    if toolset:
        return toolset

    # 尝试从工具注册表中查找（可能是插件注册的工具集）
    try:
        from tools.registry import registry
    except Exception:
        return None

    registry_toolset = name
    description = f"Plugin toolset: {name}"
    alias_target = registry.get_toolset_alias_target(name)

    if name not in _get_plugin_toolset_names():
        # 不是插件工具集，尝试作为别名解析
        registry_toolset = alias_target
        if not registry_toolset:
            return None
        description = f"MCP server '{name}' tools"
    else:
        # 是插件工具集，检查是否有对应的 MCP 别名
        reverse_aliases = {
            canonical: alias
            for alias, canonical in _get_registry_toolset_aliases().items()
            if alias not in TOOLSETS
        }
        alias = reverse_aliases.get(name)
        if alias:
            description = f"MCP server '{alias}' tools"

    return {
        "description": description,
        "tools": registry.get_tool_names_for_toolset(registry_toolset),
        "includes": [],
    }


def resolve_toolset(name: str, visited: Set[str] = None) -> List[str]:
    """
    递归解析工具集，获取所有工具名称。

    此函数通过递归解析所包含的工具集并合并所有工具来处理工具集组合。

    参数:
        name (str): 要解析的工具集名称
        visited (Set[str]): 已访问的工具集集合（用于循环检测）

    返回:
        List[str]: 工具集中所有工具名称的列表
    """
    if visited is None:
        visited = set()

    # 特殊别名，代表所有工具集中的全部工具。
    # 这确保未来新增的工具集会自动包含，无需修改代码。
    if name in {"all", "*"}:
        all_tools: Set[str] = set()
        for toolset_name in get_toolset_names():
            # 每个分支使用独立的 visited 集合，避免跨分支污染
            resolved = resolve_toolset(toolset_name, visited.copy())
            all_tools.update(resolved)
        return sorted(all_tools)

    # 检查循环 / 已解析过（菱形依赖）。
    # 静默返回 [] —— 要么是菱形依赖（不是 bug，工具已通过其他路径收集），
    # 要么是真正的循环（跳过是安全的）。
    if name in visited:
        return []

    visited.add(name)

    # 获取工具集定义
    toolset = get_toolset(name)
    if not toolset:
        return []

    # 收集直接包含的工具
    tools = set(toolset.get("tools", []))

    # 递归解析包含的工具集，在兄弟 includes 之间共享 visited 集合，
    # 这样菱形依赖只解析一次，循环警告也不会为同一循环多次触发。
    for included_name in toolset.get("includes", []):
        included_tools = resolve_toolset(included_name, visited)
        tools.update(included_tools)

    return sorted(tools)


def resolve_multiple_toolsets(toolset_names: List[str]) -> List[str]:
    """
    解析多个工具集并合并其工具。

    参数:
        toolset_names (List[str]): 要解析的工具集名称列表

    返回:
        List[str]: 合并后的所有工具名称列表（已去重）
    """
    all_tools = set()

    for name in toolset_names:
        tools = resolve_toolset(name)
        all_tools.update(tools)

    return sorted(all_tools)


def _get_plugin_toolset_names() -> Set[str]:
    """返回由插件注册的工具集名称（来自工具注册表）。

    这些工具集存在于注册表中但不在静态 ``TOOLSETS`` 字典中 ——
    即它们是在加载时由插件添加的。
    """
    try:
        from tools.registry import registry
        return {
            toolset_name
            for toolset_name in registry.get_registered_toolset_names()
            if toolset_name not in TOOLSETS
        }
    except Exception:
        return set()


def _get_registry_toolset_aliases() -> Dict[str, str]:
    """返回在活跃注册表中注册的显式工具集别名。"""
    try:
        from tools.registry import registry
        return registry.get_registered_toolset_aliases()
    except Exception:
        return {}


def get_all_toolsets() -> Dict[str, Dict[str, Any]]:
    """
    获取所有可用工具集及其定义。

    包括静态定义的工具集和插件注册的工具集。

    返回:
        Dict: 所有工具集定义
    """
    result = dict(TOOLSETS)
    aliases = _get_registry_toolset_aliases()
    # 遍历插件注册的工具集名称，尝试用别名作为展示名
    for ts_name in _get_plugin_toolset_names():
        display_name = ts_name
        for alias, canonical in aliases.items():
            if canonical == ts_name and alias not in TOOLSETS:
                display_name = alias
                break
        if display_name in result:
            continue
        toolset = get_toolset(display_name)
        if toolset:
            result[display_name] = toolset
    return result


def get_toolset_names() -> List[str]:
    """
    获取所有可用工具集的名称（不包括别名）。

    包括插件注册的工具集名称。

    返回:
        List[str]: 工具集名称列表
    """
    names = set(TOOLSETS.keys())
    aliases = _get_registry_toolset_aliases()
    for ts_name in _get_plugin_toolset_names():
        for alias, canonical in aliases.items():
            if canonical == ts_name and alias not in TOOLSETS:
                names.add(alias)
                break
        else:
            names.add(ts_name)
    return sorted(names)




def validate_toolset(name: str) -> bool:
    """
    检查工具集名称是否有效。

    参数:
        name (str): 要验证的工具集名称

    返回:
        bool: 有效返回 True，否则返回 False
    """
    # 接受特殊别名以方便使用
    if name in {"all", "*"}:
        return True
    if name in TOOLSETS:
        return True
    if name in _get_plugin_toolset_names():
        return True
    return name in _get_registry_toolset_aliases()


def create_custom_toolset(
    name: str,
    description: str,
    tools: List[str] = None,
    includes: List[str] = None
) -> None:
    """
    在运行时创建自定义工具集。

    参数:
        name (str): 新工具集的名称
        description (str): 工具集的描述
        tools (List[str]): 直接包含的工具列表
        includes (List[str]): 要包含的其他工具集列表
    """
    TOOLSETS[name] = {
        "description": description,
        "tools": tools or [],
        "includes": includes or []
    }




def get_toolset_info(name: str) -> Dict[str, Any]:
    """
    获取工具集的详细信息，包括解析后的工具列表。

    参数:
        name (str): 工具集名称

    返回:
        Dict: 工具集的详细信息
    """
    toolset = get_toolset(name)
    if not toolset:
        return None

    resolved_tools = resolve_toolset(name)

    return {
        "name": name,
        "description": toolset["description"],
        "direct_tools": toolset["tools"],
        "includes": toolset["includes"],
        "resolved_tools": resolved_tools,
        "tool_count": len(resolved_tools),
        "is_composite": bool(toolset["includes"])
    }




if __name__ == "__main__":
    print("Toolsets System Demo")
    print("=" * 60)

    print("\nAvailable Toolsets:")
    print("-" * 40)
    for name, toolset in get_all_toolsets().items():
        info = get_toolset_info(name)
        composite = "[composite]" if info["is_composite"] else "[leaf]"
        print(f"  {composite} {name:20} - {toolset['description']}")
        print(f"     Tools: {len(info['resolved_tools'])} total")

    print("\nToolset Resolution Examples:")
    print("-" * 40)
    for name in ["web", "terminal", "safe", "debugging"]:
        tools = resolve_toolset(name)
        print(f"\n  {name}:")
        print(f"    Resolved to {len(tools)} tools: {', '.join(sorted(tools))}")

    print("\nMultiple Toolset Resolution:")
    print("-" * 40)
    combined = resolve_multiple_toolsets(["web", "vision", "terminal"])
    print("  Combining ['web', 'vision', 'terminal']:")
    print(f"    Result: {', '.join(sorted(combined))}")

    print("\nCustom Toolset Creation:")
    print("-" * 40)
    create_custom_toolset(
        name="my_custom",
        description="My custom toolset for specific tasks",
        tools=["web_search"],
        includes=["terminal", "vision"]
    )
    custom_info = get_toolset_info("my_custom")
    print("  Created 'my_custom' toolset:")
    print(f"    Description: {custom_info['description']}")
    print(f"    Resolved tools: {', '.join(custom_info['resolved_tools'])}")
