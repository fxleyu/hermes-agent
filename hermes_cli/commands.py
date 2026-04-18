"""Hermes CLI 的斜杠命令定义和自动补全。

所有斜杠命令的集中注册表。每个消费者——CLI 帮助、网关分发、
Telegram BotCommands、Slack 子命令映射、自动补全——
都从 ``COMMAND_REGISTRY`` 派生数据。

添加命令：向 ``COMMAND_REGISTRY`` 添加一个 ``CommandDef`` 条目。
添加别名：在现有的 ``CommandDef`` 上设置 ``aliases=("short",)``。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

# prompt_toolkit 是一个可选的 CLI 依赖——仅在
# SlashCommandCompleter 和 SlashCommandAutoSuggest 中需要。网关和测试
# 环境即使缺少它，也必须能够导入此模块
# 来使用 resolve_command、gateway_help_lines 和 COMMAND_REGISTRY。
try:
    from prompt_toolkit.auto_suggest import AutoSuggest, Suggestion
    from prompt_toolkit.completion import Completer, Completion
except ImportError:  # pragma: no cover
    AutoSuggest = object  # type: ignore[assignment,misc]
    Completer = object    # type: ignore[assignment,misc]
    Suggestion = None     # type: ignore[assignment]
    Completion = None     # type: ignore[assignment]


# ---------------------------------------------------------------------------
# CommandDef 数据类
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CommandDef:
    """单个斜杠命令的定义。"""

    name: str                          # 不带斜杠的规范名称："background"
    description: str                   # 人类可读的描述
    category: str                      # "Session"、"Configuration" 等
    aliases: tuple[str, ...] = ()      # 替代名称：("bg",)
    args_hint: str = ""                # 参数占位符："<prompt>"、"[name]"
    subcommands: tuple[str, ...] = ()  # 可 Tab 补全的子命令
    cli_only: bool = False             # 仅在 CLI 中可用
    gateway_only: bool = False         # 仅在网关/消息平台中可用
    gateway_config_gate: str | None = None  # 配置点路径；为真值时，覆盖网关的 cli_only 设置


# ---------------------------------------------------------------------------
# 中央注册表——唯一的数据来源
# ---------------------------------------------------------------------------

COMMAND_REGISTRY: list[CommandDef] = [
    # Session
    CommandDef("new", "Start a new session (fresh session ID + history)", "Session",
               aliases=("reset",)),
    CommandDef("clear", "Clear screen and start a new session", "Session",
               cli_only=True),
    CommandDef("history", "Show conversation history", "Session",
               cli_only=True),
    CommandDef("save", "Save the current conversation", "Session",
               cli_only=True),
    CommandDef("retry", "Retry the last message (resend to agent)", "Session"),
    CommandDef("undo", "Remove the last user/assistant exchange", "Session"),
    CommandDef("title", "Set a title for the current session", "Session",
               args_hint="[name]"),
    CommandDef("branch", "Branch the current session (explore a different path)", "Session",
               aliases=("fork",), args_hint="[name]"),
    CommandDef("compress", "Manually compress conversation context", "Session",
               args_hint="[focus topic]"),
    CommandDef("rollback", "List or restore filesystem checkpoints", "Session",
               args_hint="[number]"),
    CommandDef("snapshot", "Create or restore state snapshots of Hermes config/state", "Session",
               aliases=("snap",), args_hint="[create|restore <id>|prune]"),
    CommandDef("stop", "Kill all running background processes", "Session"),
    CommandDef("approve", "Approve a pending dangerous command", "Session",
               gateway_only=True, args_hint="[session|always]"),
    CommandDef("deny", "Deny a pending dangerous command", "Session",
               gateway_only=True),
    CommandDef("background", "Run a prompt in the background", "Session",
               aliases=("bg",), args_hint="<prompt>"),
    CommandDef("btw", "Ephemeral side question using session context (no tools, not persisted)", "Session",
               args_hint="<question>"),
    CommandDef("queue", "Queue a prompt for the next turn (doesn't interrupt)", "Session",
               aliases=("q",), args_hint="<prompt>"),
    CommandDef("status", "Show session info", "Session"),
    CommandDef("profile", "Show active profile name and home directory", "Info"),
    CommandDef("sethome", "Set this chat as the home channel", "Session",
               gateway_only=True, aliases=("set-home",)),
    CommandDef("resume", "Resume a previously-named session", "Session",
               args_hint="[name]"),

    # Configuration
    CommandDef("config", "Show current configuration", "Configuration",
               cli_only=True),
    CommandDef("model", "Switch model for this session", "Configuration", args_hint="[model] [--global]"),
    CommandDef("provider", "Show available providers and current provider",
               "Configuration"),
    CommandDef("gquota", "Show Google Gemini Code Assist quota usage", "Info"),

    CommandDef("personality", "Set a predefined personality", "Configuration",
               args_hint="[name]"),
    CommandDef("statusbar", "Toggle the context/model status bar", "Configuration",
               cli_only=True, aliases=("sb",)),
    CommandDef("verbose", "Cycle tool progress display: off -> new -> all -> verbose",
               "Configuration", cli_only=True,
               gateway_config_gate="display.tool_progress_command"),
    CommandDef("yolo", "Toggle YOLO mode (skip all dangerous command approvals)",
               "Configuration"),
    CommandDef("reasoning", "Manage reasoning effort and display", "Configuration",
               args_hint="[level|show|hide]",
               subcommands=("none", "minimal", "low", "medium", "high", "xhigh", "show", "hide", "on", "off")),
    CommandDef("fast", "Toggle fast mode — OpenAI Priority Processing / Anthropic Fast Mode (Normal/Fast)", "Configuration",
               args_hint="[normal|fast|status]",
               subcommands=("normal", "fast", "status", "on", "off")),
    CommandDef("skin", "Show or change the display skin/theme", "Configuration",
               cli_only=True, args_hint="[name]"),
    CommandDef("voice", "Toggle voice mode", "Configuration",
               args_hint="[on|off|tts|status]", subcommands=("on", "off", "tts", "status")),

    # Tools & Skills
    CommandDef("tools", "Manage tools: /tools [list|disable|enable] [name...]", "Tools & Skills",
               args_hint="[list|disable|enable] [name...]", cli_only=True),
    CommandDef("toolsets", "List available toolsets", "Tools & Skills",
               cli_only=True),
    CommandDef("skills", "Search, install, inspect, or manage skills",
               "Tools & Skills", cli_only=True,
               subcommands=("search", "browse", "inspect", "install")),
    CommandDef("cron", "Manage scheduled tasks", "Tools & Skills",
               cli_only=True, args_hint="[subcommand]",
               subcommands=("list", "add", "create", "edit", "pause", "resume", "run", "remove")),
    CommandDef("reload", "Reload .env variables into the running session", "Tools & Skills"),
    CommandDef("reload-mcp", "Reload MCP servers from config", "Tools & Skills",
               aliases=("reload_mcp",)),
    CommandDef("browser", "Connect browser tools to your live Chrome via CDP", "Tools & Skills",
               cli_only=True, args_hint="[connect|disconnect|status]",
               subcommands=("connect", "disconnect", "status")),
    CommandDef("plugins", "List installed plugins and their status",
               "Tools & Skills", cli_only=True),

    # Info
    CommandDef("commands", "Browse all commands and skills (paginated)", "Info",
               gateway_only=True, args_hint="[page]"),
    CommandDef("help", "Show available commands", "Info"),
    CommandDef("restart", "Gracefully restart the gateway after draining active runs", "Session",
               gateway_only=True),
    CommandDef("usage", "Show token usage and rate limits for the current session", "Info"),
    CommandDef("insights", "Show usage insights and analytics", "Info",
               args_hint="[days]"),
    CommandDef("platforms", "Show gateway/messaging platform status", "Info",
               cli_only=True, aliases=("gateway",)),
    CommandDef("paste", "Check clipboard for an image and attach it", "Info",
               cli_only=True),
    CommandDef("image", "Attach a local image file for your next prompt", "Info",
               cli_only=True, args_hint="<path>"),
    CommandDef("update", "Update Hermes Agent to the latest version", "Info",
               gateway_only=True),
    CommandDef("debug", "Upload debug report (system info + logs) and get shareable links", "Info"),

    # Exit
    CommandDef("quit", "Exit the CLI", "Exit",
               cli_only=True, aliases=("exit",)),
]


# ---------------------------------------------------------------------------
# 派生查找表——导入时构建一次，通过 rebuild_lookups() 刷新
# ---------------------------------------------------------------------------

def _build_command_lookup() -> dict[str, CommandDef]:
    """将每个名称和别名映射到其 CommandDef。"""
    lookup: dict[str, CommandDef] = {}
    for cmd in COMMAND_REGISTRY:
        lookup[cmd.name] = cmd
        for alias in cmd.aliases:
            lookup[alias] = cmd
    return lookup


_COMMAND_LOOKUP: dict[str, CommandDef] = _build_command_lookup()


def resolve_command(name: str) -> CommandDef | None:
    """将命令名称或别名解析为其 CommandDef。

    接受带或不带前导斜杠的名称。
    """
    return _COMMAND_LOOKUP.get(name.lower().lstrip("/"))


def _build_description(cmd: CommandDef) -> str:
    """构建面向 CLI 的描述字符串，包含用法提示。"""
    if cmd.args_hint:
        return f"{cmd.description} (usage: /{cmd.name} {cmd.args_hint})"
    return cmd.description


# 向后兼容的扁平字典："/command" -> 描述
COMMANDS: dict[str, str] = {}
for _cmd in COMMAND_REGISTRY:
    if not _cmd.gateway_only:
        COMMANDS[f"/{_cmd.name}"] = _build_description(_cmd)
        for _alias in _cmd.aliases:
            COMMANDS[f"/{_alias}"] = f"{_cmd.description} (alias for /{_cmd.name})"

# 向后兼容的分类字典
COMMANDS_BY_CATEGORY: dict[str, dict[str, str]] = {}
for _cmd in COMMAND_REGISTRY:
    if not _cmd.gateway_only:
        _cat = COMMANDS_BY_CATEGORY.setdefault(_cmd.category, {})
        _cat[f"/{_cmd.name}"] = COMMANDS[f"/{_cmd.name}"]
        for _alias in _cmd.aliases:
            _cat[f"/{_alias}"] = COMMANDS[f"/{_alias}"]


# 子命令查找表："/cmd" -> ["sub1", "sub2", ...]
SUBCOMMANDS: dict[str, list[str]] = {}
for _cmd in COMMAND_REGISTRY:
    if _cmd.subcommands:
        SUBCOMMANDS[f"/{_cmd.name}"] = list(_cmd.subcommands)

# 同时从 args_hint 中通过管道分隔模式提取子命令
# 例如 args_hint="[on|off|tts|status]" 用于没有显式子命令的命令。
# 注意：如果命令已有显式子命令，则跳过此回退。
# 使用 CommandDef 上的 `subcommands` 字段来定义有意的 Tab 补全参数。
_PIPE_SUBS_RE = re.compile(r"[a-z]+(?:\|[a-z]+)+")
for _cmd in COMMAND_REGISTRY:
    key = f"/{_cmd.name}"
    if key in SUBCOMMANDS or not _cmd.args_hint:
        continue
    m = _PIPE_SUBS_RE.search(_cmd.args_hint)
    if m:
        SUBCOMMANDS[key] = m.group(0).split("|")


# ---------------------------------------------------------------------------
# 网关辅助函数
# ---------------------------------------------------------------------------

# 网关识别的所有命令名称和别名集合。
# 包含配置门控命令，以便网关可以分发它们
#（处理程序在运行时检查配置门控）。
GATEWAY_KNOWN_COMMANDS: frozenset[str] = frozenset(
    name
    for cmd in COMMAND_REGISTRY
    if not cmd.cli_only or cmd.gateway_config_gate
    for name in (cmd.name, *cmd.aliases)
)


def _resolve_config_gates() -> set[str]:
    """返回 ``gateway_config_gate`` 为真值的命令的规范名称。

    读取 ``config.yaml`` 并遍历每个配置门控命令的点分隔键路径。
    在任何错误时返回空集，以便调用者优雅降级。
    """
    gated = [c for c in COMMAND_REGISTRY if c.gateway_config_gate]
    if not gated:
        return set()
    try:
        from hermes_cli.config import read_raw_config
        cfg = read_raw_config()
    except Exception:
        return set()
    result: set[str] = set()
    for cmd in gated:
        val: Any = cfg
        for key in cmd.gateway_config_gate.split("."):
            if isinstance(val, dict):
                val = val.get(key)
            else:
                val = None
                break
        if val:
            result.add(cmd.name)
    return result


def _is_gateway_available(cmd: CommandDef, config_overrides: set[str] | None = None) -> bool:
    """检查 *cmd* 是否应出现在网关界面（帮助、菜单、映射）中。

    当 ``cli_only`` 为 False 时无条件可用。当 ``cli_only``
    为 True 但设置了 ``gateway_config_gate`` 时，仅在配置值为真时可用。
    传入 *config_overrides*（来自 ``_resolve_config_gates()``）以避免
    为每个命令重复读取配置。
    """
    if not cmd.cli_only:
        return True
    if cmd.gateway_config_gate:
        overrides = config_overrides if config_overrides is not None else _resolve_config_gates()
        return cmd.name in overrides
    return False


def gateway_help_lines() -> list[str]:
    """从注册表生成网关帮助文本行。"""
    overrides = _resolve_config_gates()
    lines: list[str] = []
    for cmd in COMMAND_REGISTRY:
        if not _is_gateway_available(cmd, overrides):
            continue
        args = f" {cmd.args_hint}" if cmd.args_hint else ""
        alias_parts: list[str] = []
        for a in cmd.aliases:
            # 跳过内部别名，如 reload_mcp（下划线变体）
            if a.replace("-", "_") == cmd.name.replace("-", "_") and a != cmd.name:
                continue
            alias_parts.append(f"`/{a}`")
        alias_note = f" (alias: {', '.join(alias_parts)})" if alias_parts else ""
        lines.append(f"`/{cmd.name}{args}` -- {cmd.description}{alias_note}")
    return lines


def telegram_bot_commands() -> list[tuple[str, str]]:
    """返回用于 Telegram setMyCommands 的 (command_name, description) 对。

    Telegram 命令名称不能包含连字符，因此用下划线替换。
    别名被跳过——Telegram 为每个规范命令显示一个菜单条目。
    """
    overrides = _resolve_config_gates()
    result: list[tuple[str, str]] = []
    for cmd in COMMAND_REGISTRY:
        if not _is_gateway_available(cmd, overrides):
            continue
        tg_name = _sanitize_telegram_name(cmd.name)
        if tg_name:
            result.append((tg_name, cmd.description))
    return result


_CMD_NAME_LIMIT = 32
"""Telegram 和 Discord 共享的最大命令名称长度。"""

# 向后兼容别名——测试和外部代码可能引用旧名称。
_TG_NAME_LIMIT = _CMD_NAME_LIMIT

# Telegram Bot API 仅允许命令名称中使用小写 a-z、0-9 和下划线。
# 此正则在初始转换后剥离所有其他字符。
_TG_INVALID_CHARS = re.compile(r"[^a-z0-9_]")
_TG_MULTI_UNDERSCORE = re.compile(r"_{2,}")


def _sanitize_telegram_name(raw: str) -> str:
    """将命令/技能/插件名称转换为有效的 Telegram 命令名称。

    Telegram 要求：1-32 个字符，仅小写 a-z、数字 0-9、下划线。
    步骤：小写化 -> 将连字符替换为下划线 -> 剥离所有其他无效字符
    -> 合并连续下划线 -> 剥离首尾下划线。
    """
    name = raw.lower().replace("-", "_")
    name = _TG_INVALID_CHARS.sub("", name)
    name = _TG_MULTI_UNDERSCORE.sub("_", name)
    return name.strip("_")


def _clamp_command_names(
    entries: list[tuple[str, str]],
    reserved: set[str],
) -> list[tuple[str, str]]:
    """执行 32 字符命令名称限制并避免冲突。

    Telegram 和 Discord 都将斜杠命令名称限制在 32 个字符。
    超过限制的名称会被截断。如果截断造成重复
    （与 *reserved* 名称或同批次中较早的条目冲突），
    名称会被缩短到 31 个字符并追加一个数字 ``0``-``9`` 来区分。
    如果所有 10 个数字槽位都被占用，则静默丢弃该条目。
    """
    used: set[str] = set(reserved)
    result: list[tuple[str, str]] = []
    for name, desc in entries:
        if len(name) > _CMD_NAME_LIMIT:
            candidate = name[:_CMD_NAME_LIMIT]
            if candidate in used:
                prefix = name[:_CMD_NAME_LIMIT - 1]
                for digit in range(10):
                    candidate = f"{prefix}{digit}"
                    if candidate not in used:
                        break
                else:
                    # 所有 10 个数字槽位均已用尽——跳过此条目
                    continue
            name = candidate
        if name in used:
            continue
        used.add(name)
        result.append((name, desc))
    return result


# 向后兼容别名。
_clamp_telegram_names = _clamp_command_names


# ---------------------------------------------------------------------------
# 网关平台共享的技能/插件收集
# ---------------------------------------------------------------------------

def _collect_gateway_skill_entries(
    platform: str,
    max_slots: int,
    reserved_names: set[str],
    desc_limit: int = 100,
    sanitize_name: "Callable[[str], str] | None" = None,
) -> tuple[list[tuple[str, str, str]], int]:
    """为网关平台收集插件和技能条目。

    优先级顺序：
      1. 插件斜杠命令（优先于技能）
      2. 内置技能命令（填充剩余槽位，按字母排序）

    仅在达到上限时裁剪技能。
    Hub 安装的技能被排除。按平台禁用的技能被排除。

    参数：
        platform: 用于按平台技能过滤的平台标识符
            （``"telegram"``、``"discord"`` 等）。
        max_slots: 返回的最大条目数（内置/核心命令后的剩余槽位）。
        reserved_names: 已被内置命令占用的名称。添加新名称时就地修改。
        desc_limit: 最大描述长度（Telegram 为 40，Discord 为 100）。
        sanitize_name: 在截断前应用的可选名称转换，
            例如 Telegram 的 :func:`_sanitize_telegram_name`。
            可返回空字符串表示"跳过此条目"。

    返回：
        ``(entries, hidden_count)``，其中 *entries* 是
        ``(name, description, cmd_key)`` 三元组列表，*hidden_count* 是
        因上限而丢弃的技能条目数。``cmd_key`` 是
        :func:`get_skill_commands` 中的原始 ``/skill-name`` 键。
    """
    all_entries: list[tuple[str, str, str]] = []

    # --- 第 1 层：插件斜杠命令（从不裁剪）---------------------
    plugin_pairs: list[tuple[str, str]] = []
    try:
        from hermes_cli.plugins import get_plugin_manager
        pm = get_plugin_manager()
        plugin_cmds = getattr(pm, "_plugin_commands", {})
        for cmd_name in sorted(plugin_cmds):
            name = sanitize_name(cmd_name) if sanitize_name else cmd_name
            if not name:
                continue
            desc = plugin_cmds[cmd_name].get("description", "Plugin command")
            if len(desc) > desc_limit:
                desc = desc[:desc_limit - 3] + "..."
            plugin_pairs.append((name, desc))
    except Exception:
        pass

    plugin_pairs = _clamp_command_names(plugin_pairs, reserved_names)
    reserved_names.update(n for n, _ in plugin_pairs)
    # 插件没有 cmd_key——使用空字符串作为占位符
    for n, d in plugin_pairs:
        all_entries.append((n, d, ""))

    # --- 第 2 层：内置技能命令（在上限处裁剪）-----------------
    _platform_disabled: set[str] = set()
    try:
        from agent.skill_utils import get_disabled_skill_names
        _platform_disabled = get_disabled_skill_names(platform=platform)
    except Exception:
        pass

    skill_triples: list[tuple[str, str, str]] = []
    try:
        from agent.skill_commands import get_skill_commands
        from tools.skills_tool import SKILLS_DIR
        _skills_dir = str(SKILLS_DIR.resolve())
        _hub_dir = str((SKILLS_DIR / ".hub").resolve())
        skill_cmds = get_skill_commands()
        for cmd_key in sorted(skill_cmds):
            info = skill_cmds[cmd_key]
            skill_path = info.get("skill_md_path", "")
            if not skill_path.startswith(_skills_dir):
                continue
            if skill_path.startswith(_hub_dir):
                continue
            skill_name = info.get("name", "")
            if skill_name in _platform_disabled:
                continue
            raw_name = cmd_key.lstrip("/")
            name = sanitize_name(raw_name) if sanitize_name else raw_name
            if not name:
                continue
            desc = info.get("description", "")
            if len(desc) > desc_limit:
                desc = desc[:desc_limit - 3] + "..."
            skill_triples.append((name, desc, cmd_key))
    except Exception:
        pass

    # 截断名称；_clamp_command_names 处理 (name, desc) 对，
    # 所以需要拆分/合并。
    skill_pairs = [(n, d) for n, d, _ in skill_triples]
    key_by_pair = {(n, d): k for n, d, k in skill_triples}
    skill_pairs = _clamp_command_names(skill_pairs, reserved_names)

    # 技能填充剩余槽位——唯一会被裁剪的层级
    remaining = max(0, max_slots - len(all_entries))
    hidden_count = max(0, len(skill_pairs) - remaining)
    for n, d in skill_pairs[:remaining]:
        all_entries.append((n, d, key_by_pair.get((n, d), "")))

    return all_entries[:max_slots], hidden_count


# ---------------------------------------------------------------------------
# 平台特定包装器
# ---------------------------------------------------------------------------

def telegram_menu_commands(max_commands: int = 100) -> tuple[list[tuple[str, str]], int]:
    """返回受 Bot API 限制的 Telegram 菜单命令。

    优先级顺序（更高优先级 = 永不被溢出挤掉）：
      1. 核心 CommandDef 命令（始终包含）
      2. 插件斜杠命令（优先于技能）
      3. 内置技能命令（填充剩余槽位，按字母排序）

    当达到上限时，仅裁剪技能。
    用户安装的 Hub 技能被排除——可通过 /skills 访问。
    通过 ``hermes skills config`` 为 ``"telegram"`` 平台禁用的技能
    将完全从菜单中排除。

    返回：
        (menu_commands, hidden_count)，其中 hidden_count 是
        因上限而省略的技能命令数。
    """
    core_commands = list(telegram_bot_commands())
    reserved_names = {n for n, _ in core_commands}
    all_commands = list(core_commands)

    remaining_slots = max(0, max_commands - len(all_commands))
    entries, hidden_count = _collect_gateway_skill_entries(
        platform="telegram",
        max_slots=remaining_slots,
        reserved_names=reserved_names,
        desc_limit=40,
        sanitize_name=_sanitize_telegram_name,
    )
    # 丢弃 cmd_key——Telegram 只需要 (name, desc) 对。
    all_commands.extend((n, d) for n, d, _k in entries)
    return all_commands[:max_commands], hidden_count


def discord_skill_commands(
    max_slots: int,
    reserved_names: set[str],
) -> tuple[list[tuple[str, str, str]], int]:
    """返回用于 Discord 斜杠命令注册的技能条目。

    与 :func:`telegram_menu_commands` 相同的优先级和过滤逻辑
    （插件 > 技能，Hub 排除，按平台禁用排除），但
    适配 Discord 的约束：

    - 名称中允许使用连字符（无 ``-`` -> ``_`` 清理）
    - 描述限制在 100 个字符（Discord 的单字段最大值）

    参数：
        max_slots: 可用命令槽位数（100 减去现有内置数量）。
        reserved_names: 已注册的内置命令名称。

    返回：
        ``(entries, hidden_count)``，其中 *entries* 是
        ``(discord_name, description, cmd_key)`` 三元组列表。``cmd_key`` 是
        斜杠处理程序回调所需的原始 ``/skill-name`` 键。
    """
    return _collect_gateway_skill_entries(
        platform="discord",
        max_slots=max_slots,
        reserved_names=set(reserved_names),  # 复制——不修改调用者的集合
        desc_limit=100,
    )


def discord_skill_commands_by_category(
    reserved_names: set[str],
) -> tuple[dict[str, list[tuple[str, str, str]]], list[tuple[str, str, str]], int]:
    """返回按分类组织的技能条目，用于 Discord ``/skill`` 子命令分组。

    技能目录在 ``SKILLS_DIR`` 下至少嵌套 2 层的
    （例如 ``creative/ascii-art/SKILL.md``）按其顶层分类分组。
    根级技能（例如 ``dogfood/SKILL.md``）作为
    *未分类* 返回——调用者应将它们注册为 ``/skill`` 分组的直接子命令。

    应用与 :func:`discord_skill_commands` 相同的过滤逻辑：
    Hub 技能排除、按平台禁用排除、名称截断。

    返回：
        ``(categories, uncategorized, hidden_count)``

        - *categories*: ``{分类名: [(名称, 描述, cmd_key), ...]}``
        - *uncategorized*: ``[(名称, 描述, cmd_key), ...]``
        - *hidden_count*: 因 Discord 分组限制而丢弃的技能数
          （25 个子命令分组，每组 25 个子命令）
    """
    from pathlib import Path as _P

    _platform_disabled: set[str] = set()
    try:
        from agent.skill_utils import get_disabled_skill_names
        _platform_disabled = get_disabled_skill_names(platform="discord")
    except Exception:
        pass

    # 收集原始技能数据 --------------------------------------------------
    categories: dict[str, list[tuple[str, str, str]]] = {}
    uncategorized: list[tuple[str, str, str]] = []
    _names_used: set[str] = set(reserved_names)
    hidden = 0

    try:
        from agent.skill_commands import get_skill_commands
        from tools.skills_tool import SKILLS_DIR
        _skills_dir = SKILLS_DIR.resolve()
        _hub_dir = (SKILLS_DIR / ".hub").resolve()
        skill_cmds = get_skill_commands()

        for cmd_key in sorted(skill_cmds):
            info = skill_cmds[cmd_key]
            skill_path = info.get("skill_md_path", "")
            if not skill_path:
                continue
            sp = _P(skill_path).resolve()
            # 跳过 SKILLS_DIR 外部或来自 Hub 的技能
            if not str(sp).startswith(str(_skills_dir)):
                continue
            if str(sp).startswith(str(_hub_dir)):
                continue

            skill_name = info.get("name", "")
            if skill_name in _platform_disabled:
                continue

            raw_name = cmd_key.lstrip("/")
            # 截断到 32 字符（Discord 限制）
            discord_name = raw_name[:32]
            if discord_name in _names_used:
                continue
            _names_used.add(discord_name)

            desc = info.get("description", "")
            if len(desc) > 100:
                desc = desc[:97] + "..."

            # 从 SKILLS_DIR 内的相对路径确定分类。
            # 例如 creative/ascii-art/SKILL.md -> parts = ("creative", "ascii-art")
            try:
                rel = sp.parent.relative_to(_skills_dir)
            except ValueError:
                continue
            parts = rel.parts
            if len(parts) >= 2:
                cat = parts[0]
                categories.setdefault(cat, []).append((discord_name, desc, cmd_key))
            else:
                uncategorized.append((discord_name, desc, cmd_key))
    except Exception:
        pass

    # 执行 Discord 限制：25 个子命令分组，每组 25 个子命令 ------
    _MAX_GROUPS = 25
    _MAX_PER_GROUP = 25

    trimmed_categories: dict[str, list[tuple[str, str, str]]] = {}
    group_count = 0
    for cat in sorted(categories):
        if group_count >= _MAX_GROUPS:
            hidden += len(categories[cat])
            continue
        entries = categories[cat][:_MAX_PER_GROUP]
        hidden += max(0, len(categories[cat]) - _MAX_PER_GROUP)
        trimmed_categories[cat] = entries
        group_count += 1

    # 未分类技能同样计入 25 个顶层限制
    remaining_slots = _MAX_GROUPS - group_count
    if len(uncategorized) > remaining_slots:
        hidden += len(uncategorized) - remaining_slots
        uncategorized = uncategorized[:remaining_slots]

    return trimmed_categories, uncategorized, hidden


def slack_subcommand_map() -> dict[str, str]:
    """返回 Slack /hermes 处理程序的子命令 -> /command 映射。

    映射规范名称和别名，使得 /hermes bg do stuff 与
    /hermes background do stuff 效果相同。
    """
    overrides = _resolve_config_gates()
    mapping: dict[str, str] = {}
    for cmd in COMMAND_REGISTRY:
        if not _is_gateway_available(cmd, overrides):
            continue
        mapping[cmd.name] = f"/{cmd.name}"
        for alias in cmd.aliases:
            mapping[alias] = f"/{alias}"
    return mapping


# ---------------------------------------------------------------------------
# 自动补全
# ---------------------------------------------------------------------------

class SlashCommandCompleter(Completer):
    """内置斜杠命令、子命令和技能命令的自动补全。"""

    def __init__(
        self,
        skill_commands_provider: Callable[[], Mapping[str, dict[str, Any]]] | None = None,
        command_filter: Callable[[str], bool] | None = None,
    ) -> None:
        self._skill_commands_provider = skill_commands_provider
        self._command_filter = command_filter
        # 缓存的项目文件列表，用于模糊 @ 补全
        self._file_cache: list[str] = []
        self._file_cache_time: float = 0.0
        self._file_cache_cwd: str = ""

    def _command_allowed(self, slash_command: str) -> bool:
        if self._command_filter is None:
            return True
        try:
            return bool(self._command_filter(slash_command))
        except Exception:
            return True

    def _iter_skill_commands(self) -> Mapping[str, dict[str, Any]]:
        if self._skill_commands_provider is None:
            return {}
        try:
            return self._skill_commands_provider() or {}
        except Exception:
            return {}

    @staticmethod
    def _completion_text(cmd_name: str, word: str) -> str:
        """返回补全的替换文本。

        当用户已经精确输入完整命令（``/help``）时，
        返回 ``help`` 将是空操作，prompt_toolkit 会隐藏菜单。
        追加一个尾随空格可保持下拉菜单可见，
        退格也能自然地重新触发它。
        """
        return f"{cmd_name} " if cmd_name == word else cmd_name

    @staticmethod
    def _extract_path_word(text: str) -> str | None:
        """如果当前词看起来像文件路径，则提取它。

        返回光标下的类路径 token，如果当前词不像路径则返回 None。
        当词以 ``./``、``../``、``~/``、``/`` 开头或包含
        ``/`` 分隔符（如 ``src/main.py``）时被认为是类路径的。
        """
        if not text:
            return None
        # Walk backwards to find the start of the current "word".
        # Words are delimited by spaces, but paths can contain almost anything.
        # 向后遍历找到当前"词"的起始位置。
        # 词以空格分隔，但路径几乎可以包含任何字符。
        i = len(text) - 1
        while i >= 0 and text[i] != " ":
            i -= 1
        word = text[i + 1:]
        if not word:
            return None
        # 仅对类路径 token 触发路径补全
        if word.startswith(("./", "../", "~/", "/")) or "/" in word:
            return word
        return None

    @staticmethod
    def _path_completions(word: str, limit: int = 30):
        """为匹配 *word* 的文件路径生成 Completion 对象。"""
        expanded = os.path.expanduser(word)
            # 将目录拆分为目录部分和要在其中匹配的前缀
        if expanded.endswith("/"):
            search_dir = expanded
            prefix = ""
        else:
            search_dir = os.path.dirname(expanded) or "."
            prefix = os.path.basename(expanded)

        try:
            entries = os.listdir(search_dir)
        except OSError:
            return

        count = 0
        prefix_lower = prefix.lower()
        for entry in sorted(entries):
            if prefix and not entry.lower().startswith(prefix_lower):
                continue
            if count >= limit:
                break

            full_path = os.path.join(search_dir, entry)
            is_dir = os.path.isdir(full_path)

            # 构建补全文本（用于替换已输入的词）
            # Build the completion text (what replaces the typed word)
            if word.startswith("~"):
                display_path = "~/" + os.path.relpath(full_path, os.path.expanduser("~"))
            elif os.path.isabs(word):
                display_path = full_path
            else:
                # 保持相对路径
                # Keep relative
                display_path = os.path.relpath(full_path)

            if is_dir:
                display_path += "/"

            suffix = "/" if is_dir else ""
            meta = "dir" if is_dir else _file_size_label(full_path)

            yield Completion(
                display_path,
                start_position=-len(word),
                display=entry + suffix,
                display_meta=meta,
            )
            count += 1

    @staticmethod
    def _extract_context_word(text: str) -> str | None:
        """提取用于上下文引用补全的裸 ``@`` token。"""
        if not text:
            return None
        # 向后遍历找到当前词的起始位置
        # Walk backwards to find the start of the current word
        i = len(text) - 1
        while i >= 0 and text[i] != " ":
            i -= 1
        word = text[i + 1:]
        if not word.startswith("@"):
            return None
        return word

    def _context_completions(self, word: str, limit: int = 30):
        """生成 Claude Code 风格的 @ 上下文补全。

        裸 ``@`` 或 ``@partial`` 显示静态引用和匹配的文件/文件夹。
        ``@file:path`` 和 ``@folder:path`` 由现有的路径补全逻辑处理。
        """
        lowered = word.lower()

        # 静态上下文引用
        # Static context references
        _STATIC_REFS = (
            ("@diff", "Git working tree diff"),
            ("@staged", "Git staged diff"),
            ("@file:", "Attach a file"),
            ("@folder:", "Attach a folder"),
            ("@git:", "Git log with diffs (e.g. @git:5)"),
            ("@url:", "Fetch web content"),
        )
        for candidate, meta in _STATIC_REFS:
            if candidate.lower().startswith(lowered) and candidate.lower() != lowered:
                yield Completion(
                    candidate,
                    start_position=-len(word),
                    display=candidate,
                    display_meta=meta,
                )

        # 如果用户输入了 @file: 或 @folder:，则委托给路径补全
        # If the user typed @file: or @folder:, delegate to path completions
        for prefix in ("@file:", "@folder:"):
            if word.startswith(prefix):
                path_part = word[len(prefix):] or "."
                expanded = os.path.expanduser(path_part)
                if expanded.endswith("/"):
                    search_dir, match_prefix = expanded, ""
                else:
                    search_dir = os.path.dirname(expanded) or "."
                    match_prefix = os.path.basename(expanded)

                try:
                    entries = os.listdir(search_dir)
                except OSError:
                    return

                count = 0
                prefix_lower = match_prefix.lower()
                for entry in sorted(entries):
                    if match_prefix and not entry.lower().startswith(prefix_lower):
                        continue
                    if count >= limit:
                        break
                    full_path = os.path.join(search_dir, entry)
                    is_dir = os.path.isdir(full_path)
                    display_path = os.path.relpath(full_path)
                    suffix = "/" if is_dir else ""
                    kind = "folder" if is_dir else "file"
                    meta = "dir" if is_dir else _file_size_label(full_path)
                    completion = f"@{kind}:{display_path}{suffix}"
                    yield Completion(
                        completion,
                        start_position=-len(word),
                        display=entry + suffix,
                        display_meta=meta,
                    )
                    count += 1
                return

        # 裸 @ 或 @partial —— 项目范围内的模糊文件搜索
        # Bare @ or @partial — fuzzy project-wide file search
        query = word[1:]  # strip the @
        yield from self._fuzzy_file_completions(word, query, limit)

    def _get_project_files(self) -> list[str]:
        """返回缓存的项目文件列表（每 5 秒刷新一次）。"""
        cwd = os.getcwd()
        now = time.monotonic()
        if (
            self._file_cache
            and self._file_cache_cwd == cwd
            and now - self._file_cache_time < 5.0
        ):
            return self._file_cache

        files: list[str] = []
        # 优先尝试 rg（快速，遵循 .gitignore），然后 fd，最后 find。
        # Try rg first (fast, respects .gitignore), then fd, then find.
        for cmd in [
            ["rg", "--files", "--sortr=modified", cwd],
            ["rg", "--files", cwd],
            ["fd", "--type", "f", "--base-directory", cwd],
        ]:
            tool = cmd[0]
            if not shutil.which(tool):
                continue
            try:
                proc = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=2,
                    cwd=cwd,
                )
                if proc.returncode == 0 and proc.stdout.strip():
                    raw = proc.stdout.strip().split("\n")
                    # 存储相对路径
                    # Store relative paths
                    for p in raw[:5000]:
                        rel = os.path.relpath(p, cwd) if os.path.isabs(p) else p
                        files.append(rel)
                    break
            except (subprocess.TimeoutExpired, OSError):
                continue

        self._file_cache = files
        self._file_cache_time = now
        self._file_cache_cwd = cwd
        return files

    @staticmethod
    def _score_path(filepath: str, query: str) -> int:
        """对文件路径与模糊查询进行评分。分数越高匹配越好。"""
        if not query:
            return 1  # 查询为空时显示所有文件

        filename = os.path.basename(filepath)
        lower_file = filename.lower()
        lower_path = filepath.lower()
        lower_q = query.lower()

        # 文件名精确匹配
        # Exact filename match
        if lower_file == lower_q:
            return 100
        # 文件名以查询开头
        # Filename starts with query
        if lower_file.startswith(lower_q):
            return 80
        # 文件名包含查询作为子串
        # Filename contains query as substring
        if lower_q in lower_file:
            return 60
        # 完整路径包含查询
        # Full path contains query
        if lower_q in lower_path:
            return 40
        # 首字母/缩写匹配：例如 "fo" 匹配 "file_operations"
        # 检查查询字符是否按顺序出现在文件名中
        # Initials / abbreviation match: e.g. "fo" matches "file_operations"
        # Check if query chars appear in order in filename
        qi = 0
        for c in lower_file:
            if qi < len(lower_q) and c == lower_q[qi]:
                qi += 1
        if qi == len(lower_q):
            # 如果匹配落在词边界上（在 _, -, /, . 之后）则加分
            # Bonus if matches land on word boundaries (after _, -, /, .)
            boundary_hits = 0
            qi = 0
            prev = "_"  # treat start as boundary
            for c in lower_file:
                if qi < len(lower_q) and c == lower_q[qi]:
                    if prev in "_-./":
                        boundary_hits += 1
                    qi += 1
                prev = c
            if boundary_hits >= len(lower_q) * 0.5:
                return 35
            return 25
        return 0

    def _fuzzy_file_completions(self, word: str, query: str, limit: int = 20):
        """为裸 @query 生成模糊文件补全。"""
        files = self._get_project_files()

        if not query:
            # 没有查询——显示最近修改的文件（已按修改时间排序）
            # No query — show recently modified files (already sorted by mtime)
            for fp in files[:limit]:
                is_dir = fp.endswith("/")
                filename = os.path.basename(fp)
                kind = "folder" if is_dir else "file"
                meta = "dir" if is_dir else _file_size_label(
                    os.path.join(os.getcwd(), fp)
                )
                yield Completion(
                    f"@{kind}:{fp}",
                    start_position=-len(word),
                    display=filename,
                    display_meta=meta,
                )
            return

        # 评分并排序
        # Score and rank
        scored = []
        for fp in files:
            s = self._score_path(fp, query)
            if s > 0:
                scored.append((s, fp))
        scored.sort(key=lambda x: (-x[0], x[1]))

        for _, fp in scored[:limit]:
            is_dir = fp.endswith("/")
            filename = os.path.basename(fp)
            kind = "folder" if is_dir else "file"
            meta = "dir" if is_dir else _file_size_label(
                os.path.join(os.getcwd(), fp)
            )
            yield Completion(
                f"@{kind}:{fp}",
                start_position=-len(word),
                display=filename,
                display_meta=f"{fp}  {meta}" if meta else fp,
            )

    def _model_completions(self, sub_text: str, sub_lower: str):
        """从配置别名和内置别名中为 /model 命令生成补全。"""
        seen = set()
        # 配置中的直接别名（优先——包含提供商信息）
        # Config-based direct aliases (preferred — include provider info)
        try:
            from hermes_cli.model_switch import (
                _ensure_direct_aliases, DIRECT_ALIASES, MODEL_ALIASES,
            )
            _ensure_direct_aliases()
            for name, da in DIRECT_ALIASES.items():
                if name.startswith(sub_lower) and name != sub_lower:
                    seen.add(name)
                    yield Completion(
                        name,
                        start_position=-len(sub_text),
                        display=name,
                        display_meta=f"{da.model} ({da.provider})",
                    )
            # 尚未覆盖的内置目录别名
            # Built-in catalog aliases not already covered
            for name in sorted(MODEL_ALIASES.keys()):
                if name in seen:
                    continue
                if name.startswith(sub_lower) and name != sub_lower:
                    identity = MODEL_ALIASES[name]
                    yield Completion(
                        name,
                        start_position=-len(sub_text),
                        display=name,
                        display_meta=f"{identity.vendor}/{identity.family}",
                    )
        except Exception:
            pass

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if not text.startswith("/"):
            # 尝试 @ 上下文补全（Claude Code 风格）
            # Try @ context completion (Claude Code-style)
            ctx_word = self._extract_context_word(text)
            if ctx_word is not None:
                yield from self._context_completions(ctx_word)
                return
            # 为非斜杠输入尝试文件路径补全
            # Try file path completion for non-slash input
            path_word = self._extract_path_word(text)
            if path_word is not None:
                yield from self._path_completions(path_word)
            return

        # 检查是否正在补全子命令（基础命令已输入）
        # Check if we're completing a subcommand (base command already typed)
        parts = text.split(maxsplit=1)
        base_cmd = parts[0].lower()
        if len(parts) > 1 or (len(parts) == 1 and text.endswith(" ")):
            sub_text = parts[1] if len(parts) > 1 else ""
            sub_lower = sub_text.lower()

            # 为 /model 命令动态生成模型别名补全
            # Dynamic model alias completions for /model
            if " " not in sub_text and base_cmd == "/model":
                yield from self._model_completions(sub_text, sub_lower)
                return

            # 静态子命令补全
            # Static subcommand completions
            if " " not in sub_text and base_cmd in SUBCOMMANDS and self._command_allowed(base_cmd):
                for sub in SUBCOMMANDS[base_cmd]:
                    if sub.startswith(sub_lower) and sub != sub_lower:
                        yield Completion(
                            sub,
                            start_position=-len(sub_text),
                            display=sub,
                        )
            return

        word = text[1:]

        for cmd, desc in COMMANDS.items():
            if not self._command_allowed(cmd):
                continue
            cmd_name = cmd[1:]
            if cmd_name.startswith(word):
                yield Completion(
                    self._completion_text(cmd_name, word),
                    start_position=-len(word),
                    display=cmd,
                    display_meta=desc,
                )

        for cmd, info in self._iter_skill_commands().items():
            cmd_name = cmd[1:]
            if cmd_name.startswith(word):
                description = str(info.get("description", "Skill command"))
                short_desc = description[:50] + ("..." if len(description) > 50 else "")
                yield Completion(
                    self._completion_text(cmd_name, word),
                    start_position=-len(word),
                    display=cmd,
                    display_meta=f"⚡ {short_desc}",
                )

        # 插件注册的斜杠命令
        # Plugin-registered slash commands
        try:
            from hermes_cli.plugins import get_plugin_commands
            for cmd_name, cmd_info in get_plugin_commands().items():
                if cmd_name.startswith(word):
                    desc = str(cmd_info.get("description", "Plugin command"))
                    short_desc = desc[:50] + ("..." if len(desc) > 50 else "")
                    yield Completion(
                        self._completion_text(cmd_name, word),
                        start_position=-len(word),
                        display=f"/{cmd_name}",
                        display_meta=f"🔌 {short_desc}",
                    )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 斜杠命令的内联自动建议（幽灵文本）
# ---------------------------------------------------------------------------

class SlashCommandAutoSuggest(AutoSuggest):
    """斜杠命令及其子命令的内联幽灵文本建议。

    在输入时以灰色文本显示命令或子命令的剩余部分。
    非斜杠输入时回退到基于历史记录的建议。
    """

    def __init__(
        self,
        history_suggest: AutoSuggest | None = None,
        completer: SlashCommandCompleter | None = None,
    ) -> None:
        self._history = history_suggest
        self._completer = completer  # 复用其模型缓存

    def get_suggestion(self, buffer, document):
        text = document.text_before_cursor

        # 仅为斜杠命令提供建议
        # Only suggest for slash commands
        if not text.startswith("/"):
            # 非斜杠文本回退到历史记录
            # Fall back to history for regular text
            if self._history:
                return self._history.get_suggestion(buffer, document)
            return None

        parts = text.split(maxsplit=1)
        base_cmd = parts[0].lower()

        if len(parts) == 1 and not text.endswith(" "):
            # 仍在输入命令名称：/upd -> 建议 "ate"
            # Still typing the command name: /upd → suggest "ate"
            word = text[1:].lower()
            for cmd in COMMANDS:
                if self._completer is not None and not self._completer._command_allowed(cmd):
                    continue
                cmd_name = cmd[1:]  # 去除前导斜杠
                if cmd_name.startswith(word) and cmd_name != word:
                    return Suggestion(cmd_name[len(word):])
            return None

        # 命令已完成——建议子命令或模型名称
        # Command is complete — suggest subcommands or model names
        sub_text = parts[1] if len(parts) > 1 else ""
        sub_lower = sub_text.lower()

        # 静态子命令
        # Static subcommands
        if self._completer is not None and not self._completer._command_allowed(base_cmd):
            return None
        if base_cmd in SUBCOMMANDS and SUBCOMMANDS[base_cmd]:
            if " " not in sub_text:
                for sub in SUBCOMMANDS[base_cmd]:
                    if sub.startswith(sub_lower) and sub != sub_lower:
                        return Suggestion(sub[len(sub_text):])

        # 回退到历史记录
        if self._history:
            return self._history.get_suggestion(buffer, document)
        return None


def _file_size_label(path: str) -> str:
    """返回紧凑的人类可读文件大小，出错时返回空字符串。"""
    try:
        size = os.path.getsize(path)
    except OSError:
        return ""
    if size < 1024:
        return f"{size}B"
    if size < 1024 * 1024:
        return f"{size / 1024:.0f}K"
    if size < 1024 * 1024 * 1024:
        return f"{size / (1024 * 1024):.1f}M"
    return f"{size / (1024 * 1024 * 1024):.1f}G"
