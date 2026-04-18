"""Hermes CLI 皮肤/主题引擎。

数据驱动的皮肤系统，让用户自定义 CLI 的视觉外观。
皮肤定义为 ~/.hermes/skins/ 中的 YAML 文件或内置预设。
添加新皮肤无需修改代码。

皮肤 YAML 架构
================

所有字段均为可选。缺失的值从 ``default`` 皮肤继承。

.. code-block:: yaml

    # 必需：皮肤标识
    name: mytheme                         # 唯一皮肤名称（小写，可用连字符）
    description: Short description        # 在 /skin 列表中显示

    # 颜色：Rich 标记的十六进制值（横幅、UI、响应框）
    colors:
      banner_border: "#CD7F32"            # 面板边框颜色
      banner_title: "#FFD700"             # 面板标题文本颜色
      banner_accent: "#FFBF00"            # 分段标题（可用工具等）
      banner_dim: "#B8860B"               # 暗淡/弱化文本（分隔符、标签）
      banner_text: "#FFF8DC"              # 正文文本（工具名称、技能名称）
      ui_accent: "#FFBF00"               # 通用 UI 强调色
      ui_label: "#4dd0e1"                # UI 标签
      ui_ok: "#4caf50"                   # 成功指示器
      ui_error: "#ef5350"                # 错误指示器
      ui_warn: "#ffa726"                 # 警告指示器
      prompt: "#FFF8DC"                  # 提示文本颜色
      input_rule: "#CD7F32"              # 输入区域水平线
      response_border: "#FFD700"         # 响应框边框（ANSI）
      session_label: "#DAA520"           # 会话标签颜色
      session_border: "#8B8682"          # 会话 ID 暗淡颜色
      status_bar_bg: "#1a1a2e"          # TUI 状态/用量条背景
      voice_status_bg: "#1a1a2e"        # TUI 语音状态背景
      completion_menu_bg: "#1a1a2e"      # 补全菜单背景
      completion_menu_current_bg: "#333355"  # 活跃补全行背景
      completion_menu_meta_bg: "#1a1a2e"     # 补全元列背景
      completion_menu_meta_current_bg: "#333355"  # 活跃补全元背景

    # 加载动画：自定义 API 调用期间的动画加载器
    spinner:
      waiting_faces:                      # 等待 API 时显示的表情
        - "(⚔)"
        - "(⛨)"
      thinking_faces:                     # 推理期间显示的表情
        - "(⌁)"
        - "(<>)"
      thinking_verbs:                     # 加载消息中的动词
        - "forging"
        - "plotting"
      wings:                              # 可选的左/右加载装饰
        - ["⟪⚔", "⚔⟫"]                  # 每个条目是 [left, right] 对
        - ["⟪▲", "▲⟫"]

    # 品牌：在 CLI 中使用的文本字符串
    branding:
      agent_name: "Hermes Agent"          # 横幅标题、状态显示
      welcome: "Welcome message"          # CLI 启动时显示
      goodbye: "Goodbye! ⚕"              # 退出时显示
      response_label: " ⚕ Hermes "       # 响应框标题标签
      prompt_symbol: "❯ "                # 输入提示符号
      help_header: "(^_^)? Commands"      # /help 标题文本

    # 工具前缀：工具输出行的字符（默认：┊）
    tool_prefix: "┊"

    # 工具表情：覆盖任何工具的默认表情（用于加载器和进度显示）
    tool_emojis:
      terminal: "⚔"           # 覆盖 terminal 工具表情
      web_search: "🔮"        # 覆盖 web_search 工具表情
      # 此处未列出的工具使用注册表默认值

用法
=====

.. code-block:: python

    from hermes_cli.skin_engine import get_active_skin, list_skins, set_active_skin

    skin = get_active_skin()
    print(skin.colors["banner_title"])    # "#FFD700"
    print(skin.get_branding("agent_name"))  # "Hermes Agent"

    set_active_skin("ares")               # 切换到内置 ares 皮肤
    set_active_skin("mytheme")            # 切换到 ~/.hermes/skins/ 中的用户皮肤

内置皮肤
==============

- ``default`` — 经典 Hermes 金色/可爱风格（当前外观）
- ``ares``    — 深红/青铜战神主题，带自定义加载翅膀
- ``mono``    — 简洁的灰度单色风格
- ``slate``   — 冷蓝色开发者主题
- ``daylight`` — 浅色背景主题，深色文本和蓝色强调
- ``warm-lightmode`` — 暖棕色/金色文本，适合浅色终端背景

用户皮肤
==========

将 YAML 文件放入 ``~/.hermes/skins/<name>.yaml``，遵循上述架构。
在 CLI 中用 ``/skin <name>`` 激活，或在 config.yaml 中设置 ``display.skin: <name>``。
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from hermes_constants import get_hermes_home

logger = logging.getLogger(__name__)


# =============================================================================
# 皮肤数据结构
# =============================================================================

@dataclass
class SkinConfig:
    """完整的皮肤配置。"""
    name: str
    description: str = ""
    colors: Dict[str, str] = field(default_factory=dict)
    spinner: Dict[str, Any] = field(default_factory=dict)
    branding: Dict[str, str] = field(default_factory=dict)
    tool_prefix: str = "┊"
    tool_emojis: Dict[str, str] = field(default_factory=dict)  # 每个工具的表情覆盖
    banner_logo: str = ""    # Rich 标记 ASCII 艺术 logo（替换 HERMES_AGENT_LOGO）
    banner_hero: str = ""    # Rich 标记英雄图案（替换 HERMES_CADUCEUS）

    def get_color(self, key: str, fallback: str = "") -> str:
        """获取颜色值，带回退默认值。"""
        return self.colors.get(key, fallback)

    def get_spinner_wings(self) -> List[Tuple[str, str]]:
        """获取加载器翅膀配对列表，无则返回空列表。"""
        raw = self.spinner.get("wings", [])
        result = []
        for pair in raw:
            if isinstance(pair, (list, tuple)) and len(pair) == 2:
                result.append((str(pair[0]), str(pair[1])))
        return result

    def get_branding(self, key: str, fallback: str = "") -> str:
        """获取品牌值，带回退默认值。"""
        return self.branding.get(key, fallback)


# =============================================================================
# 内置皮肤定义
# =============================================================================

_BUILTIN_SKINS: Dict[str, Dict[str, Any]] = {
    "default": {
        "name": "default",
        "description": "Classic Hermes — gold and kawaii",
        "colors": {
            "banner_border": "#CD7F32",
            "banner_title": "#FFD700",
            "banner_accent": "#FFBF00",
            "banner_dim": "#B8860B",
            "banner_text": "#FFF8DC",
            "ui_accent": "#FFBF00",
            "ui_label": "#4dd0e1",
            "ui_ok": "#4caf50",
            "ui_error": "#ef5350",
            "ui_warn": "#ffa726",
            "prompt": "#FFF8DC",
            "input_rule": "#CD7F32",
            "response_border": "#FFD700",
            "session_label": "#DAA520",
            "session_border": "#8B8682",
        },
        "spinner": {
            # 空 = 使用 display.py 中的硬编码默认值
        },
        "branding": {
            "agent_name": "Hermes Agent",
            "welcome": "Welcome to Hermes Agent! Type your message or /help for commands.",
            "goodbye": "Goodbye! ⚕",
            "response_label": " ⚕ Hermes ",
            "prompt_symbol": "❯ ",
            "help_header": "(^_^)? Available Commands",
        },
        "tool_prefix": "┊",
    },
    "ares": {
        "name": "ares",
        "description": "War-god theme — crimson and bronze",
        "colors": {
            "banner_border": "#9F1C1C",
            "banner_title": "#C7A96B",
            "banner_accent": "#DD4A3A",
            "banner_dim": "#6B1717",
            "banner_text": "#F1E6CF",
            "ui_accent": "#DD4A3A",
            "ui_label": "#C7A96B",
            "ui_ok": "#4caf50",
            "ui_error": "#ef5350",
            "ui_warn": "#ffa726",
            "prompt": "#F1E6CF",
            "input_rule": "#9F1C1C",
            "response_border": "#C7A96B",
            "session_label": "#C7A96B",
            "session_border": "#6E584B",
        },
        "spinner": {
            "waiting_faces": ["(⚔)", "(⛨)", "(▲)", "(<>)", "(/)"],
            "thinking_faces": ["(⚔)", "(⛨)", "(▲)", "(⌁)", "(<>)"],
            "thinking_verbs": [
                "forging", "marching", "sizing the field", "holding the line",
                "hammering plans", "tempering steel", "plotting impact", "raising the shield",
            ],
            "wings": [
                ["⟪⚔", "⚔⟫"],
                ["⟪▲", "▲⟫"],
                ["⟪╸", "╺⟫"],
                ["⟪⛨", "⛨⟫"],
            ],
        },
        "branding": {
            "agent_name": "Ares Agent",
            "welcome": "Welcome to Ares Agent! Type your message or /help for commands.",
            "goodbye": "Farewell, warrior! ⚔",
            "response_label": " ⚔ Ares ",
            "prompt_symbol": "⚔ ❯ ",
            "help_header": "(⚔) Available Commands",
        },
        "tool_prefix": "╎",
        "banner_logo": """[bold #A3261F] █████╗ ██████╗ ███████╗███████╗       █████╗  ██████╗ ███████╗███╗   ██╗████████╗[/]
[bold #B73122]██╔══██╗██╔══██╗██╔════╝██╔════╝      ██╔══██╗██╔════╝ ██╔════╝████╗  ██║╚══██╔══╝[/]
[#C93C24]███████║██████╔╝█████╗  ███████╗█████╗███████║██║  ███╗█████╗  ██╔██╗ ██║   ██║[/]
[#D84A28]██╔══██║██╔══██╗██╔══╝  ╚════██║╚════╝██╔══██║██║   ██║██╔══╝  ██║╚██╗██║   ██║[/]
[#E15A2D]██║  ██║██║  ██║███████╗███████║      ██║  ██║╚██████╔╝███████╗██║ ╚████║   ██║[/]
[#EB6C32]╚═╝  ╚═╝╚═╝  ╚═╝╚══════╝╚══════╝      ╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚═╝  ╚═══╝   ╚═╝[/]""",
        "banner_hero": """[#9F1C1C]⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣤⣤⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀[/]
[#9F1C1C]⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣴⣿⠟⠻⣿⣦⡀⠀⠀⠀⠀⠀⠀⠀⠀⠀[/]
[#C7A96B]⠀⠀⠀⠀⠀⠀⠀⣠⣾⡿⠋⠀⠀⠀⠙⢿⣷⣄⠀⠀⠀⠀⠀⠀⠀[/]
[#C7A96B]⠀⠀⠀⠀⠀⢀⣾⡿⠋⠀⠀⢠⡄⠀⠀⠙⢿⣷⡀⠀⠀⠀⠀⠀[/]
[#DD4A3A]⠀⠀⠀⠀⣰⣿⠟⠀⠀⠀⣰⣿⣿⣆⠀⠀⠀⠻⣿⣆⠀⠀⠀⠀[/]
[#DD4A3A]⠀⠀⠀⢰⣿⠏⠀⠀⢀⣾⡿⠉⢿⣷⡀⠀⠀⠹⣿⡆⠀⠀⠀[/]
[#9F1C1C]⠀⠀⠀⣿⡟⠀⠀⣠⣿⠟⠀⠀⠀⠻⣿⣄⠀⠀⢻⣿⠀⠀⠀[/]
[#9F1C1C]⠀⠀⠀⣿⡇⠀⠀⠙⠋⠀⠀⚔⠀⠀⠙⠋⠀⠀⢸⣿⠀⠀⠀[/]
[#6B1717]⠀⠀⠀⢿⣧⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣼⡿⠀⠀⠀[/]
[#6B1717]⠀⠀⠀⠘⢿⣷⣄⠀⠀⠀⠀⠀⠀⠀⠀⠀⣠⣾⡿⠃⠀⠀⠀[/]
[#C7A96B]⠀⠀⠀⠀⠈⠻⣿⣷⣦⣤⣀⣀⣤⣤⣶⣿⠿⠋⠀⠀⠀⠀[/]
[#C7A96B]⠀⠀⠀⠀⠀⠀⠀⠉⠛⠿⠿⠿⠿⠛⠉⠀⠀⠀⠀⠀⠀⠀[/]
[#DD4A3A]⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⚔⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀[/]
[dim #6B1717]⠀⠀⠀⠀⠀⠀⠀⠀war god online⠀⠀⠀⠀⠀⠀⠀⠀[/]""",
    },
    "mono": {
        "name": "mono",
        "description": "Monochrome — clean grayscale",
        "colors": {
            "banner_border": "#555555",
            "banner_title": "#e6edf3",
            "banner_accent": "#aaaaaa",
            "banner_dim": "#444444",
            "banner_text": "#c9d1d9",
            "ui_accent": "#aaaaaa",
            "ui_label": "#888888",
            "ui_ok": "#888888",
            "ui_error": "#cccccc",
            "ui_warn": "#999999",
            "prompt": "#c9d1d9",
            "input_rule": "#444444",
            "response_border": "#aaaaaa",
            "session_label": "#888888",
            "session_border": "#555555",
        },
        "spinner": {},
        "branding": {
            "agent_name": "Hermes Agent",
            "welcome": "Welcome to Hermes Agent! Type your message or /help for commands.",
            "goodbye": "Goodbye! ⚕",
            "response_label": " ⚕ Hermes ",
            "prompt_symbol": "❯ ",
            "help_header": "[?] Available Commands",
        },
        "tool_prefix": "┊",
    },
    "slate": {
        "name": "slate",
        "description": "Cool blue — developer-focused",
        "colors": {
            "banner_border": "#4169e1",
            "banner_title": "#7eb8f6",
            "banner_accent": "#8EA8FF",
            "banner_dim": "#4b5563",
            "banner_text": "#c9d1d9",
            "ui_accent": "#7eb8f6",
            "ui_label": "#8EA8FF",
            "ui_ok": "#63D0A6",
            "ui_error": "#F7A072",
            "ui_warn": "#e6a855",
            "prompt": "#c9d1d9",
            "input_rule": "#4169e1",
            "response_border": "#7eb8f6",
            "session_label": "#7eb8f6",
            "session_border": "#4b5563",
        },
        "spinner": {},
        "branding": {
            "agent_name": "Hermes Agent",
            "welcome": "Welcome to Hermes Agent! Type your message or /help for commands.",
            "goodbye": "Goodbye! ⚕",
            "response_label": " ⚕ Hermes ",
            "prompt_symbol": "❯ ",
            "help_header": "(^_^)? Available Commands",
        },
        "tool_prefix": "┊",
    },
    "daylight": {
        "name": "daylight",
        "description": "Light theme for bright terminals with dark text and cool blue accents",
        "colors": {
            "banner_border": "#2563EB",
            "banner_title": "#0F172A",
            "banner_accent": "#1D4ED8",
            "banner_dim": "#475569",
            "banner_text": "#111827",
            "ui_accent": "#2563EB",
            "ui_label": "#0F766E",
            "ui_ok": "#15803D",
            "ui_error": "#B91C1C",
            "ui_warn": "#B45309",
            "prompt": "#111827",
            "input_rule": "#93C5FD",
            "response_border": "#2563EB",
            "session_label": "#1D4ED8",
            "session_border": "#64748B",
            "status_bar_bg": "#E5EDF8",
            "voice_status_bg": "#E5EDF8",
            "completion_menu_bg": "#F8FAFC",
            "completion_menu_current_bg": "#DBEAFE",
            "completion_menu_meta_bg": "#EEF2FF",
            "completion_menu_meta_current_bg": "#BFDBFE",
        },
        "spinner": {},
        "branding": {
            "agent_name": "Hermes Agent",
            "welcome": "Welcome to Hermes Agent! Type your message or /help for commands.",
            "goodbye": "Goodbye! ⚕",
            "response_label": " ⚕ Hermes ",
            "prompt_symbol": "❯ ",
            "help_header": "[?] Available Commands",
        },
        "tool_prefix": "│",
    },
    "warm-lightmode": {
        "name": "warm-lightmode",
        "description": "Warm light mode — dark brown/gold text for light terminal backgrounds",
        "colors": {
            "banner_border": "#8B6914",
            "banner_title": "#5C3D11",
            "banner_accent": "#8B4513",
            "banner_dim": "#8B7355",
            "banner_text": "#2C1810",
            "ui_accent": "#8B4513",
            "ui_label": "#5C3D11",
            "ui_ok": "#2E7D32",
            "ui_error": "#C62828",
            "ui_warn": "#E65100",
            "prompt": "#2C1810",
            "input_rule": "#8B6914",
            "response_border": "#8B6914",
            "session_label": "#5C3D11",
            "session_border": "#A0845C",
            "status_bar_bg": "#F5F0E8",
            "voice_status_bg": "#F5F0E8",
            "completion_menu_bg": "#F5EFE0",
            "completion_menu_current_bg": "#E8DCC8",
            "completion_menu_meta_bg": "#F0E8D8",
            "completion_menu_meta_current_bg": "#DFCFB0",
        },
        "spinner": {},
        "branding": {
            "agent_name": "Hermes Agent",
            "welcome": "Welcome to Hermes Agent! Type your message or /help for commands.",
            "goodbye": "Goodbye! \u2695",
            "response_label": " \u2695 Hermes ",
            "prompt_symbol": "\u276f ",
            "help_header": "(^_^)? Available Commands",
        },
        "tool_prefix": "\u250a",
    },
    "poseidon": {
        "name": "poseidon",
        "description": "Ocean-god theme — deep blue and seafoam",
        "colors": {
            "banner_border": "#2A6FB9",
            "banner_title": "#A9DFFF",
            "banner_accent": "#5DB8F5",
            "banner_dim": "#153C73",
            "banner_text": "#EAF7FF",
            "ui_accent": "#5DB8F5",
            "ui_label": "#A9DFFF",
            "ui_ok": "#4caf50",
            "ui_error": "#ef5350",
            "ui_warn": "#ffa726",
            "prompt": "#EAF7FF",
            "input_rule": "#2A6FB9",
            "response_border": "#5DB8F5",
            "session_label": "#A9DFFF",
            "session_border": "#496884",
        },
        "spinner": {
            "waiting_faces": ["(≈)", "(Ψ)", "(∿)", "(◌)", "(◠)"],
            "thinking_faces": ["(Ψ)", "(∿)", "(≈)", "(⌁)", "(◌)"],
            "thinking_verbs": [
                "charting currents", "sounding the depth", "reading foam lines",
                "steering the trident", "tracking undertow", "plotting sea lanes",
                "calling the swell", "measuring pressure",
            ],
            "wings": [
                ["⟪≈", "≈⟫"],
                ["⟪Ψ", "Ψ⟫"],
                ["⟪∿", "∿⟫"],
                ["⟪◌", "◌⟫"],
            ],
        },
        "branding": {
            "agent_name": "Poseidon Agent",
            "welcome": "Welcome to Poseidon Agent! Type your message or /help for commands.",
            "goodbye": "Fair winds! Ψ",
            "response_label": " Ψ Poseidon ",
            "prompt_symbol": "Ψ ❯ ",
            "help_header": "(Ψ) Available Commands",
        },
        "tool_prefix": "│",
        "banner_logo": """[bold #B8E8FF]██████╗  ██████╗ ███████╗███████╗██╗██████╗  ██████╗ ███╗   ██╗       █████╗  ██████╗ ███████╗███╗   ██╗████████╗[/]
[bold #97D6FF]██╔══██╗██╔═══██╗██╔════╝██╔════╝██║██╔══██╗██╔═══██╗████╗  ██║      ██╔══██╗██╔════╝ ██╔════╝████╗  ██║╚══██╔══╝[/]
[#75C1F6]██████╔╝██║   ██║███████╗█████╗  ██║██║  ██║██║   ██║██╔██╗ ██║█████╗███████║██║  ███╗█████╗  ██╔██╗ ██║   ██║[/]
[#4FA2E0]██╔═══╝ ██║   ██║╚════██║██╔══╝  ██║██║  ██║██║   ██║██║╚██╗██║╚════╝██╔══██║██║   ██║██╔══╝  ██║╚██╗██║   ██║[/]
[#2E7CC7]██║     ╚██████╔╝███████║███████╗██║██████╔╝╚██████╔╝██║ ╚████║      ██║  ██║╚██████╔╝███████╗██║ ╚████║   ██║[/]
[#1B4F95]╚═╝      ╚═════╝ ╚══════╝╚══════╝╚═╝╚═════╝  ╚═════╝ ╚═╝  ╚═══╝      ╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚═╝  ╚═══╝   ╚═╝[/]""",
        "banner_hero": """[#2A6FB9]⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣀⡀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀[/]
[#5DB8F5]⠀⠀⠀⠀⠀⠀⠀⠀⠀⣠⣾⣿⣷⣄⠀⠀⠀⠀⠀⠀⠀⠀⠀[/]
[#5DB8F5]⠀⠀⠀⠀⠀⠀⠀⢠⣿⠏⠀Ψ⠀⠹⣿⡄⠀⠀⠀⠀⠀⠀⠀[/]
[#A9DFFF]⠀⠀⠀⠀⠀⠀⠀⣿⡟⠀⠀⠀⠀⠀⢻⣿⠀⠀⠀⠀⠀⠀⠀[/]
[#A9DFFF]⠀⠀⠀≈≈≈≈≈⣿⡇⠀⠀⠀⠀⠀⢸⣿≈≈≈≈≈⠀⠀⠀[/]
[#5DB8F5]⠀⠀⠀⠀⠀⠀⠀⣿⡇⠀⠀⠀⠀⠀⢸⣿⠀⠀⠀⠀⠀⠀⠀[/]
[#2A6FB9]⠀⠀⠀⠀⠀⠀⠀⢿⣧⠀⠀⠀⠀⠀⣼⡿⠀⠀⠀⠀⠀⠀⠀[/]
[#2A6FB9]⠀⠀⠀⠀⠀⠀⠀⠘⢿⣷⣄⣀⣠⣾⡿⠃⠀⠀⠀⠀⠀⠀⠀[/]
[#153C73]⠀⠀⠀⠀⠀⠀⠀⠀⠈⠻⣿⣿⡿⠟⠁⠀⠀⠀⠀⠀⠀⠀⠀[/]
[#153C73]⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠈⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀[/]
[#5DB8F5]⠀⠀⠀⠀⠀≈≈≈≈≈≈≈≈≈≈≈≈≈≈≈⠀⠀⠀⠀⠀[/]
[#A9DFFF]⠀⠀⠀⠀⠀⠀≈≈≈≈≈≈≈≈≈≈≈≈≈⠀⠀⠀⠀⠀⠀[/]
[dim #153C73]⠀⠀⠀⠀⠀⠀⠀deep waters hold⠀⠀⠀⠀⠀⠀⠀[/]""",
    },
    "sisyphus": {
        "name": "sisyphus",
        "description": "Sisyphean theme — austere grayscale with persistence",
        "colors": {
            "banner_border": "#B7B7B7",
            "banner_title": "#F5F5F5",
            "banner_accent": "#E7E7E7",
            "banner_dim": "#4A4A4A",
            "banner_text": "#D3D3D3",
            "ui_accent": "#E7E7E7",
            "ui_label": "#D3D3D3",
            "ui_ok": "#919191",
            "ui_error": "#E7E7E7",
            "ui_warn": "#B7B7B7",
            "prompt": "#F5F5F5",
            "input_rule": "#656565",
            "response_border": "#B7B7B7",
            "session_label": "#919191",
            "session_border": "#656565",
        },
        "spinner": {
            "waiting_faces": ["(◉)", "(◌)", "(◬)", "(⬤)", "(::)"],
            "thinking_faces": ["(◉)", "(◬)", "(◌)", "(○)", "(●)"],
            "thinking_verbs": [
                "finding traction", "measuring the grade", "resetting the boulder",
                "counting the ascent", "testing leverage", "setting the shoulder",
                "pushing uphill", "enduring the loop",
            ],
            "wings": [
                ["⟪◉", "◉⟫"],
                ["⟪◬", "◬⟫"],
                ["⟪◌", "◌⟫"],
                ["⟪⬤", "⬤⟫"],
            ],
        },
        "branding": {
            "agent_name": "Sisyphus Agent",
            "welcome": "Welcome to Sisyphus Agent! Type your message or /help for commands.",
            "goodbye": "The boulder waits. ◉",
            "response_label": " ◉ Sisyphus ",
            "prompt_symbol": "◉ ❯ ",
            "help_header": "(◉) Available Commands",
        },
        "tool_prefix": "│",
        "banner_logo": """[bold #F5F5F5]███████╗██╗███████╗██╗   ██╗██████╗ ██╗  ██╗██╗   ██╗███████╗       █████╗  ██████╗ ███████╗███╗   ██╗████████╗[/]
[bold #E7E7E7]██╔════╝██║██╔════╝╚██╗ ██╔╝██╔══██╗██║  ██║██║   ██║██╔════╝      ██╔══██╗██╔════╝ ██╔════╝████╗  ██║╚══██╔══╝[/]
[#D7D7D7]███████╗██║███████╗ ╚████╔╝ ██████╔╝███████║██║   ██║███████╗█████╗███████║██║  ███╗█████╗  ██╔██╗ ██║   ██║[/]
[#BFBFBF]╚════██║██║╚════██║  ╚██╔╝  ██╔═══╝ ██╔══██║██║   ██║╚════██║╚════╝██╔══██║██║   ██║██╔══╝  ██║╚██╗██║   ██║[/]
[#8F8F8F]███████║██║███████║   ██║   ██║     ██║  ██║╚██████╔╝███████║      ██║  ██║╚██████╔╝███████╗██║ ╚████║   ██║[/]
[#626262]╚══════╝╚═╝╚══════╝   ╚═╝   ╚═╝     ╚═╝  ╚═╝ ╚═════╝ ╚══════╝      ╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚═╝  ╚═══╝   ╚═╝[/]""",
        "banner_hero": """[#B7B7B7]⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣀⣀⣀⡀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀[/]
[#D3D3D3]⠀⠀⠀⠀⠀⠀⠀⣠⣾⣿⣿⣿⣿⣷⣄⠀⠀⠀⠀⠀⠀⠀⠀[/]
[#E7E7E7]⠀⠀⠀⠀⠀⠀⣾⣿⣿⣿⣿⣿⣿⣿⣷⠀⠀⠀⠀⠀⠀⠀[/]
[#F5F5F5]⠀⠀⠀⠀⠀⢸⣿⣿⣿⣿⣿⣿⣿⣿⣿⡇⠀⠀⠀⠀⠀⠀[/]
[#E7E7E7]⠀⠀⠀⠀⠀⠀⣿⣿⣿⣿⣿⣿⣿⣿⣿⠀⠀⠀⠀⠀⠀⠀[/]
[#D3D3D3]⠀⠀⠀⠀⠀⠀⠘⢿⣿⣿⣿⣿⣿⡿⠃⠀⠀⠀⠀⠀⠀⠀[/]
[#B7B7B7]⠀⠀⠀⠀⠀⠀⠀⠀⠙⠿⣿⠿⠋⠀⠀⠀⠀⠀⠀⠀⠀⠀[/]
[#919191]⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀[/]
[#656565]⠀⠀⠀⠀⠀⠀⠀⠀⠀⣰⡄⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀[/]
[#656565]⠀⠀⠀⠀⠀⠀⠀⠀⣰⣿⣿⣆⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀[/]
[#4A4A4A]⠀⠀⠀⠀⠀⠀⠀⣰⣿⣿⣿⣿⣆⠀⠀⠀⠀⠀⠀⠀⠀⠀[/]
[#4A4A4A]⠀⠀⠀⠀⠀⣀⣴⣿⣿⣿⣿⣿⣿⣦⣀⠀⠀⠀⠀⠀⠀[/]
[#656565]⠀⠀⠀━━━━━━━━━━━━━━━━━━━━━━━⠀⠀⠀[/]
[dim #4A4A4A]⠀⠀⠀⠀⠀⠀⠀⠀⠀the boulder⠀⠀⠀⠀⠀⠀⠀⠀⠀[/]""",
    },
    "charizard": {
        "name": "charizard",
        "description": "Volcanic theme — burnt orange and ember",
        "colors": {
            "banner_border": "#C75B1D",
            "banner_title": "#FFD39A",
            "banner_accent": "#F29C38",
            "banner_dim": "#7A3511",
            "banner_text": "#FFF0D4",
            "ui_accent": "#F29C38",
            "ui_label": "#FFD39A",
            "ui_ok": "#4caf50",
            "ui_error": "#ef5350",
            "ui_warn": "#ffa726",
            "prompt": "#FFF0D4",
            "input_rule": "#C75B1D",
            "response_border": "#F29C38",
            "session_label": "#FFD39A",
            "session_border": "#6C4724",
        },
        "spinner": {
            "waiting_faces": ["(✦)", "(▲)", "(◇)", "(<>)", "(🔥)"],
            "thinking_faces": ["(✦)", "(▲)", "(◇)", "(⌁)", "(🔥)"],
            "thinking_verbs": [
                "banking into the draft", "measuring burn", "reading the updraft",
                "tracking ember fall", "setting wing angle", "holding the flame core",
                "plotting a hot landing", "coiling for lift",
            ],
            "wings": [
                ["⟪✦", "✦⟫"],
                ["⟪▲", "▲⟫"],
                ["⟪◌", "◌⟫"],
                ["⟪◇", "◇⟫"],
            ],
        },
        "branding": {
            "agent_name": "Charizard Agent",
            "welcome": "Welcome to Charizard Agent! Type your message or /help for commands.",
            "goodbye": "Flame out! ✦",
            "response_label": " ✦ Charizard ",
            "prompt_symbol": "✦ ❯ ",
            "help_header": "(✦) Available Commands",
        },
        "tool_prefix": "│",
        "banner_logo": """[bold #FFF0D4] ██████╗██╗  ██╗ █████╗ ██████╗ ██╗███████╗ █████╗ ██████╗ ██████╗        █████╗  ██████╗ ███████╗███╗   ██╗████████╗[/]
[bold #FFD39A]██╔════╝██║  ██║██╔══██╗██╔══██╗██║╚══███╔╝██╔══██╗██╔══██╗██╔══██╗      ██╔══██╗██╔════╝ ██╔════╝████╗  ██║╚══██╔══╝[/]
[#F29C38]██║     ███████║███████║██████╔╝██║  ███╔╝ ███████║██████╔╝██║  ██║█████╗███████║██║  ███╗█████╗  ██╔██╗ ██║   ██║[/]
[#E2832B]██║     ██╔══██║██╔══██║██╔══██╗██║ ███╔╝  ██╔══██║██╔══██╗██║  ██║╚════╝██╔══██║██║   ██║██╔══╝  ██║╚██╗██║   ██║[/]
[#C75B1D]╚██████╗██║  ██║██║  ██║██║  ██║██║███████╗██║  ██║██║  ██║██████╔╝      ██║  ██║╚██████╔╝███████╗██║ ╚████║   ██║[/]
[#7A3511] ╚═════╝╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═╝╚═╝╚══════╝╚═╝  ╚═╝╚═╝  ╚═╝╚═════╝       ╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚═╝  ╚═══╝   ╚═╝[/]""",
        "banner_hero": """[#FFD39A]⠀⠀⠀⠀⠀⠀⠀⠀⣀⣤⠶⠶⠶⣤⣀⠀⠀⠀⠀⠀⠀⠀⠀[/]
[#F29C38]⠀⠀⠀⠀⠀⠀⣴⠟⠁⠀⠀⠀⠀⠈⠻⣦⠀⠀⠀⠀⠀⠀[/]
[#F29C38]⠀⠀⠀⠀⠀⣼⠏⠀⠀⠀✦⠀⠀⠀⠀⠹⣧⠀⠀⠀⠀⠀[/]
[#E2832B]⠀⠀⠀⠀⢰⡟⠀⠀⣀⣤⣤⣤⣀⠀⠀⠀⢻⡆⠀⠀⠀⠀[/]
[#E2832B]⠀⠀⣠⡾⠛⠁⣠⣾⠟⠉⠀⠉⠻⣷⣄⠀⠈⠛⢷⣄⠀⠀[/]
[#C75B1D]⠀⣼⠟⠀⢀⣾⠟⠁⠀⠀⠀⠀⠀⠈⠻⣷⡀⠀⠻⣧⠀[/]
[#C75B1D]⢸⡟⠀⠀⣿⡟⠀⠀⠀🔥⠀⠀⠀⠀⢻⣿⠀⠀⢻⡇[/]
[#7A3511]⠀⠻⣦⡀⠘⢿⣧⡀⠀⠀⠀⠀⠀⢀⣼⡿⠃⢀⣴⠟⠀[/]
[#7A3511]⠀⠀⠈⠻⣦⣀⠙⢿⣷⣤⣤⣤⣾⡿⠋⣀⣴⠟⠁⠀⠀[/]
[#C75B1D]⠀⠀⠀⠀⠈⠙⠛⠶⠤⠭⠭⠤⠶⠛⠋⠁⠀⠀⠀⠀[/]
[#F29C38]⠀⠀⠀⠀⠀⠀⠀⠀⣰⡿⢿⣆⠀⠀⠀⠀⠀⠀⠀⠀⠀[/]
[#F29C38]⠀⠀⠀⠀⠀⠀⠀⣼⡟⠀⠀⢻⣧⠀⠀⠀⠀⠀⠀⠀⠀[/]
[dim #7A3511]⠀⠀⠀⠀⠀⠀⠀tail flame lit⠀⠀⠀⠀⠀⠀⠀⠀[/]""",
    },
}


# =============================================================================
# 皮肤加载和管理
# =============================================================================

_active_skin: Optional[SkinConfig] = None
_active_skin_name: str = "default"


def _skins_dir() -> Path:
    """用户皮肤目录。"""
    return get_hermes_home() / "skins"


def _load_skin_from_yaml(path: Path) -> Optional[Dict[str, Any]]:
    """从 YAML 文件加载皮肤定义。"""
    try:
        import yaml
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if isinstance(data, dict) and "name" in data:
            return data
    except Exception as e:
        logger.debug("Failed to load skin from %s: %s", path, e)
    return None


def _build_skin_config(data: Dict[str, Any]) -> SkinConfig:
    """从原始字典（内置或从 YAML 加载）构建 SkinConfig。"""
    # 以默认皮肤作为缺失键的基础值
    default = _BUILTIN_SKINS["default"]
    colors = dict(default.get("colors", {}))
    colors.update(data.get("colors", {}))
    spinner = dict(default.get("spinner", {}))
    spinner.update(data.get("spinner", {}))
    branding = dict(default.get("branding", {}))
    branding.update(data.get("branding", {}))

    return SkinConfig(
        name=data.get("name", "unknown"),
        description=data.get("description", ""),
        colors=colors,
        spinner=spinner,
        branding=branding,
        tool_prefix=data.get("tool_prefix", default.get("tool_prefix", "┊")),
        tool_emojis=data.get("tool_emojis", {}),
        banner_logo=data.get("banner_logo", ""),
        banner_hero=data.get("banner_hero", ""),
    )


def list_skins() -> List[Dict[str, str]]:
    """列出所有可用皮肤（内置 + 用户安装）。

    返回 {"name": ..., "description": ..., "source": "builtin"|"user"} 的列表。
    """
    result = []
    for name, data in _BUILTIN_SKINS.items():
        result.append({
            "name": name,
            "description": data.get("description", ""),
            "source": "builtin",
        })

    skins_path = _skins_dir()
    if skins_path.is_dir():
        for f in sorted(skins_path.glob("*.yaml")):
            data = _load_skin_from_yaml(f)
            if data:
                skin_name = data.get("name", f.stem)
                # 如果与内置皮肤同名则跳过
                if any(s["name"] == skin_name for s in result):
                    continue
                result.append({
                    "name": skin_name,
                    "description": data.get("description", ""),
                    "source": "user",
                })

    return result


def load_skin(name: str) -> SkinConfig:
    """按名称加载皮肤。优先检查用户皮肤，然后内置皮肤。"""
    # 检查用户皮肤目录
    skins_path = _skins_dir()
    user_file = skins_path / f"{name}.yaml"
    if user_file.is_file():
        data = _load_skin_from_yaml(user_file)
        if data:
            return _build_skin_config(data)

    # 检查内置皮肤
    if name in _BUILTIN_SKINS:
        return _build_skin_config(_BUILTIN_SKINS[name])

    # 回退到默认皮肤
    logger.warning("Skin '%s' not found, using default", name)
    return _build_skin_config(_BUILTIN_SKINS["default"])


def get_active_skin() -> SkinConfig:
    """获取当前活跃的皮肤配置（带缓存）。"""
    global _active_skin
    if _active_skin is None:
        _active_skin = load_skin(_active_skin_name)
    return _active_skin


def set_active_skin(name: str) -> SkinConfig:
    """切换活跃皮肤。返回新的 SkinConfig。"""
    global _active_skin, _active_skin_name
    _active_skin_name = name
    _active_skin = load_skin(name)
    return _active_skin


def get_active_skin_name() -> str:
    """获取当前活跃皮肤的名称。"""
    return _active_skin_name


def init_skin_from_config(config: dict) -> None:
    """在启动时从 CLI 配置初始化活跃皮肤。

    在 CLI 初始化期间使用加载的配置字典调用一次。
    """
    display = config.get("display") or {}
    if not isinstance(display, dict):
        display = {}
    skin_name = display.get("skin", "default")
    if isinstance(skin_name, str) and skin_name.strip():
        set_active_skin(skin_name.strip())
    else:
        set_active_skin("default")


# =============================================================================
# CLI 模块的便捷辅助函数
# =============================================================================


def get_active_prompt_symbol(fallback: str = "❯ ") -> str:
    """从活跃皮肤获取交互式提示符号。"""
    try:
        return get_active_skin().get_branding("prompt_symbol", fallback)
    except Exception:
        return fallback



def get_active_help_header(fallback: str = "(^_^)? Available Commands") -> str:
    """从活跃皮肤获取 /help 标题。"""
    try:
        return get_active_skin().get_branding("help_header", fallback)
    except Exception:
        return fallback



def get_active_goodbye(fallback: str = "Goodbye! ⚕") -> str:
    """从活跃皮肤获取告别语。"""
    try:
        return get_active_skin().get_branding("goodbye", fallback)
    except Exception:
        return fallback



def get_prompt_toolkit_style_overrides() -> Dict[str, str]:
    """返回从活跃皮肤派生的 prompt_toolkit 样式覆盖。

    这些样式叠加在 CLI 的基础 TUI 样式之上，使 /skin 命令可以
    立即刷新实时的 prompt_toolkit UI，无需重建应用。
    """
    try:
        skin = get_active_skin()
    except Exception:
        return {}

    prompt = skin.get_color("prompt", "#FFF8DC")
    input_rule = skin.get_color("input_rule", "#CD7F32")
    title = skin.get_color("banner_title", "#FFD700")
    text = skin.get_color("banner_text", prompt)
    dim = skin.get_color("banner_dim", "#555555")
    label = skin.get_color("ui_label", title)
    warn = skin.get_color("ui_warn", "#FF8C00")
    error = skin.get_color("ui_error", "#FF6B6B")
    status_bg = skin.get_color("status_bar_bg", "#1a1a2e")
    voice_bg = skin.get_color("voice_status_bg", status_bg)
    menu_bg = skin.get_color("completion_menu_bg", "#1a1a2e")
    menu_current_bg = skin.get_color("completion_menu_current_bg", "#333355")
    menu_meta_bg = skin.get_color("completion_menu_meta_bg", menu_bg)
    menu_meta_current_bg = skin.get_color("completion_menu_meta_current_bg", menu_current_bg)

    return {
        "input-area": prompt,
        "placeholder": f"{dim} italic",
        "prompt": prompt,
        "prompt-working": f"{dim} italic",
        "hint": f"{dim} italic",
        "status-bar": f"bg:{status_bg} {text}",
        "status-bar-strong": f"bg:{status_bg} {title} bold",
        "status-bar-dim": f"bg:{status_bg} {dim}",
        "status-bar-good": f"bg:{status_bg} {skin.get_color('ui_ok', '#8FBC8F')} bold",
        "status-bar-warn": f"bg:{status_bg} {warn} bold",
        "status-bar-bad": f"bg:{status_bg} {skin.get_color('banner_accent', warn)} bold",
        "status-bar-critical": f"bg:{status_bg} {error} bold",
        "input-rule": input_rule,
        "image-badge": f"{label} bold",
        "completion-menu": f"bg:{menu_bg} {text}",
        "completion-menu.completion": f"bg:{menu_bg} {text}",
        "completion-menu.completion.current": f"bg:{menu_current_bg} {title}",
        "completion-menu.meta.completion": f"bg:{menu_meta_bg} {dim}",
        "completion-menu.meta.completion.current": f"bg:{menu_meta_current_bg} {label}",
        "clarify-border": input_rule,
        "clarify-title": f"{title} bold",
        "clarify-question": f"{text} bold",
        "clarify-choice": dim,
        "clarify-selected": f"{title} bold",
        "clarify-active-other": f"{title} italic",
        "clarify-countdown": input_rule,
        "sudo-prompt": f"{error} bold",
        "sudo-border": input_rule,
        "sudo-title": f"{error} bold",
        "sudo-text": text,
        "approval-border": input_rule,
        "approval-title": f"{warn} bold",
        "approval-desc": f"{text} bold",
        "approval-cmd": f"{dim} italic",
        "approval-choice": dim,
        "approval-selected": f"{title} bold",
        "voice-status": f"bg:{voice_bg} {label}",
        "voice-status-recording": f"bg:{voice_bg} {error} bold",
    }
