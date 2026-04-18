"""
Hermes Agent 的交互式设置向导。

模块化向导，各部分可独立运行：
  1. 模型与提供商 — 选择你的 AI 提供商和模型
  2. 终端后端 — 选择智能体在哪里执行命令
  3. 智能体设置 — 迭代次数、压缩、会话重置
  4. 消息平台 — 连接 Telegram、Discord 等
  5. 工具 — 配置 TTS、网页搜索、图像生成等

配置文件存储在 ~/.hermes/ 中，方便访问。
"""

import importlib.util
import logging
import os
import shutil
import sys
import copy
from pathlib import Path
from typing import Optional, Dict, Any

from hermes_cli.nous_subscription import get_nous_subscription_features
from tools.tool_backend_helpers import managed_nous_tools_enabled
from hermes_constants import get_optional_skills_dir

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent.resolve()

_DOCS_BASE = "https://hermes-agent.nousresearch.com/docs"


def _model_config_dict(config: Dict[str, Any]) -> Dict[str, Any]:
    current_model = config.get("model")
    if isinstance(current_model, dict):
        return dict(current_model)
    if isinstance(current_model, str) and current_model.strip():
        return {"default": current_model.strip()}
    return {}


def _get_credential_pool_strategies(config: Dict[str, Any]) -> Dict[str, str]:
    strategies = config.get("credential_pool_strategies")
    return dict(strategies) if isinstance(strategies, dict) else {}


def _set_credential_pool_strategy(config: Dict[str, Any], provider: str, strategy: str) -> None:
    if not provider:
        return
    strategies = _get_credential_pool_strategies(config)
    strategies[provider] = strategy
    config["credential_pool_strategies"] = strategies


def _supports_same_provider_pool_setup(provider: str) -> bool:
    if not provider or provider == "custom":
        return False
    if provider == "openrouter":
        return True
    from hermes_cli.auth import PROVIDER_REGISTRY

    pconfig = PROVIDER_REGISTRY.get(provider)
    if not pconfig:
        return False
    return pconfig.auth_type in {"api_key", "oauth_device_code"}


# 每个提供商的默认模型列表 — 当无法连接到实时
# /models 端点时用作回退。
_DEFAULT_PROVIDER_MODELS = {
    "copilot-acp": [
        "copilot-acp",
    ],
    "copilot": [
        "gpt-5.4",
        "gpt-5.4-mini",
        "gpt-5-mini",
        "gpt-5.3-codex",
        "gpt-5.2-codex",
        "gpt-4.1",
        "gpt-4o",
        "gpt-4o-mini",
        "claude-opus-4.6",
        "claude-sonnet-4.6",
        "claude-sonnet-4.5",
        "claude-haiku-4.5",
        "gemini-2.5-pro",
        "grok-code-fast-1",
    ],
    "gemini": [
        "gemini-3.1-pro-preview", "gemini-3-flash-preview", "gemini-3.1-flash-lite-preview",
        "gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.5-flash-lite",
        "gemma-4-31b-it", "gemma-4-26b-it",
    ],
    "zai": ["glm-5.1", "glm-5", "glm-4.7", "glm-4.5", "glm-4.5-flash"],
    "kimi-coding": ["kimi-k2.5", "kimi-k2-thinking", "kimi-k2-turbo-preview"],
    "kimi-coding-cn": ["kimi-k2.5", "kimi-k2-thinking", "kimi-k2-turbo-preview"],
    "arcee": ["trinity-large-thinking", "trinity-large-preview", "trinity-mini"],
    "minimax": ["MiniMax-M2.7", "MiniMax-M2.5", "MiniMax-M2.1", "MiniMax-M2"],
    "minimax-cn": ["MiniMax-M2.7", "MiniMax-M2.5", "MiniMax-M2.1", "MiniMax-M2"],
    "ai-gateway": ["anthropic/claude-opus-4.6", "anthropic/claude-sonnet-4.6", "openai/gpt-5", "google/gemini-3-flash"],
    "kilocode": ["anthropic/claude-opus-4.6", "anthropic/claude-sonnet-4.6", "openai/gpt-5.4", "google/gemini-3-pro-preview", "google/gemini-3-flash-preview"],
    "opencode-zen": ["gpt-5.4", "gpt-5.3-codex", "claude-sonnet-4-6", "gemini-3-flash", "glm-5", "kimi-k2.5", "minimax-m2.7"],
    "opencode-go": ["glm-5.1", "glm-5", "kimi-k2.5", "mimo-v2-pro", "mimo-v2-omni", "minimax-m2.5", "minimax-m2.7"],
    "huggingface": [
        "Qwen/Qwen3.5-397B-A17B", "Qwen/Qwen3-235B-A22B-Thinking-2507",
        "Qwen/Qwen3-Coder-480B-A35B-Instruct", "deepseek-ai/DeepSeek-R1-0528",
        "deepseek-ai/DeepSeek-V3.2", "moonshotai/Kimi-K2.5",
    ],
}


def _current_reasoning_effort(config: Dict[str, Any]) -> str:
    agent_cfg = config.get("agent")
    if isinstance(agent_cfg, dict):
        return str(agent_cfg.get("reasoning_effort") or "").strip().lower()
    return ""


def _set_reasoning_effort(config: Dict[str, Any], effort: str) -> None:
    agent_cfg = config.get("agent")
    if not isinstance(agent_cfg, dict):
        agent_cfg = {}
        config["agent"] = agent_cfg
    agent_cfg["reasoning_effort"] = effort




# 导入配置辅助函数
from hermes_cli.config import (
    DEFAULT_CONFIG,
    get_hermes_home,
    get_config_path,
    get_env_path,
    load_config,
    save_config,
    save_env_value,
    get_env_value,
    ensure_hermes_home,
)
# 为了避免 hermes update 期间的过期模块安全问题，display_hermes_home 在调用点懒加载

from hermes_cli.colors import Colors, color


def print_header(title: str):
    """打印一个章节标题。"""
    print()
    print(color(f"◆ {title}", Colors.CYAN, Colors.BOLD))


from hermes_cli.cli_output import (  # noqa: E402
    print_error,
    print_info,
    print_success,
    print_warning,
)


def is_interactive_stdin() -> bool:
    """当 stdin 看起来是可用的交互式 TTY 时返回 True。"""
    stdin = getattr(sys, "stdin", None)
    if stdin is None:
        return False
    try:
        return bool(stdin.isatty())
    except Exception:
        return False


def print_noninteractive_setup_guidance(reason: str | None = None) -> None:
    """为无头/非交互式设置流程打印指导信息。"""
    print()
    print(color("⚕ Hermes Setup — Non-interactive mode", Colors.CYAN, Colors.BOLD))
    print()
    if reason:
        print_info(reason)
    print_info("The interactive wizard cannot be used here.")
    print()
    print_info("Configure Hermes using environment variables or config commands:")
    print_info("  hermes config set model.provider custom")
    print_info("  hermes config set model.base_url http://localhost:8080/v1")
    print_info("  hermes config set model.default your-model-name")
    print()
    print_info("Or set OPENROUTER_API_KEY / OPENAI_API_KEY in your environment.")
    print_info("Run 'hermes setup' in an interactive terminal to use the full wizard.")
    print()


def prompt(question: str, default: str = None, password: bool = False) -> str:
    """带可选默认值的输入提示。"""
    if default:
        display = f"{question} [{default}]: "
    else:
        display = f"{question}: "

    try:
        if password:
            import getpass

            value = getpass.getpass(color(display, Colors.YELLOW))
        else:
            value = input(color(display, Colors.YELLOW))

        return value.strip() or default or ""
    except (KeyboardInterrupt, EOFError):
        print()
        sys.exit(1)


def _curses_prompt_choice(question: str, choices: list, default: int = 0, description: str | None = None) -> int:
    """使用 curses 的单选菜单。委托给 curses_radiolist。"""
    from hermes_cli.curses_ui import curses_radiolist
    return curses_radiolist(question, choices, selected=default, cancel_returns=-1, description=description)



def prompt_choice(question: str, choices: list, default: int = 0, description: str | None = None) -> int:
    """从列表中提示选择，支持方向键导航。

    Escape 保持当前默认值（跳过该问题）。
    Ctrl+C 退出向导。
    """
    idx = _curses_prompt_choice(question, choices, default, description=description)
    if idx >= 0:
        if idx == default:
            print_info("  Skipped (keeping current)")
            print()
            return default
        print()
        return idx

    print(color(question, Colors.YELLOW))
    for i, choice in enumerate(choices):
        marker = "●" if i == default else "○"
        if i == default:
            print(color(f"  {marker} {choice}", Colors.GREEN))
        else:
            print(f"  {marker} {choice}")

    print_info(f"  Enter for default ({default + 1})  Ctrl+C to exit")

    while True:
        try:
            value = input(
                color(f"  Select [1-{len(choices)}] ({default + 1}): ", Colors.DIM)
            )
            if not value:
                return default
            idx = int(value) - 1
            if 0 <= idx < len(choices):
                return idx
            print_error(f"Please enter a number between 1 and {len(choices)}")
        except ValueError:
            print_error("Please enter a number")
        except (KeyboardInterrupt, EOFError):
            print()
            sys.exit(1)


def prompt_yes_no(question: str, default: bool = True) -> bool:
    """提示 yes/no。Ctrl+C 退出，空输入返回默认值。"""
    default_str = "Y/n" if default else "y/N"

    while True:
        try:
            value = (
                input(color(f"{question} [{default_str}]: ", Colors.YELLOW))
                .strip()
                .lower()
            )
        except (KeyboardInterrupt, EOFError):
            print()
            sys.exit(1)

        if not value:
            return default
        if value in ("y", "yes"):
            return True
        if value in ("n", "no"):
            return False
        print_error("Please enter 'y' or 'n'")


def prompt_checklist(title: str, items: list, pre_selected: list = None) -> list:
    """
    显示多选复选列表并返回选中项的索引。

    `items` 中的每个项目是显示字符串。`pre_selected` 是默认勾选的索引列表。
    末尾追加一个 "Continue →" 选项 — 用户用空格切换项目，
    在 "Continue →" 上按回车确认。

    当 simple_term_menu 不可用时，回退到数字切换界面。

    返回:
        选中项的索引列表（不包括 Continue 选项）。
    """
    if pre_selected is None:
        pre_selected = []

    from hermes_cli.curses_ui import curses_checklist

    chosen = curses_checklist(
        title,
        items,
        set(pre_selected),
        cancel_returns=set(pre_selected),
    )
    return sorted(chosen)


def _prompt_api_key(var: dict):
    """为单个环境变量显示格式化的 API 密钥输入界面。"""
    tools = var.get("tools", [])
    tools_str = ", ".join(tools[:3])
    if len(tools) > 3:
        tools_str += f", +{len(tools) - 3} more"

    print()
    print(color(f"  ─── {var.get('description', var['name'])} ───", Colors.CYAN))
    print()
    if tools_str:
        print_info(f"  Enables: {tools_str}")
    if var.get("url"):
        print_info(f"  Get your key at: {var['url']}")
    print()

    if var.get("password"):
        value = prompt(f"  {var.get('prompt', var['name'])}", password=True)
    else:
        value = prompt(f"  {var.get('prompt', var['name'])}")

    if value:
        save_env_value(var["name"], value)
        print_success("  ✓ Saved")
    else:
        print_warning("  Skipped (configure later with 'hermes setup')")


def _print_setup_summary(config: dict, hermes_home):
    """打印设置完成摘要。"""
    # 工具可用性摘要
    print()
    print_header("Tool Availability Summary")

    tool_status = []
    subscription_features = get_nous_subscription_features(config)

    # 视觉能力 — 使用与实际视觉工具相同的运行时解析器
    try:
        from agent.auxiliary_client import get_available_vision_backends

        _vision_backends = get_available_vision_backends()
    except Exception:
        _vision_backends = []

    if _vision_backends:
        tool_status.append(("Vision (image analysis)", True, None))
    else:
        tool_status.append(("Vision (image analysis)", False, "run 'hermes setup' to configure"))

    # 混合智能体 — 特别需要 OpenRouter（调用多个模型）
    if get_env_value("OPENROUTER_API_KEY"):
        tool_status.append(("Mixture of Agents", True, None))
    else:
        tool_status.append(("Mixture of Agents", False, "OPENROUTER_API_KEY"))

    # 网页工具（Exa、Parallel、Firecrawl 或 Tavily）
    if subscription_features.web.managed_by_nous:
        tool_status.append(("Web Search & Extract (Nous subscription)", True, None))
    elif subscription_features.web.available:
        label = "Web Search & Extract"
        if subscription_features.web.current_provider:
            label = f"Web Search & Extract ({subscription_features.web.current_provider})"
        tool_status.append((label, True, None))
    else:
        tool_status.append(("Web Search & Extract", False, "EXA_API_KEY, PARALLEL_API_KEY, FIRECRAWL_API_KEY/FIRECRAWL_API_URL, or TAVILY_API_KEY"))

    # 浏览器工具（本地 Chromium、Camofox、Browserbase、Browser Use 或 Firecrawl）
    browser_provider = subscription_features.browser.current_provider
    if subscription_features.browser.managed_by_nous:
        tool_status.append(("Browser Automation (Nous Browser Use)", True, None))
    elif subscription_features.browser.available:
        label = "Browser Automation"
        if browser_provider:
            label = f"Browser Automation ({browser_provider})"
        tool_status.append((label, True, None))
    else:
        missing_browser_hint = "npm install -g agent-browser, set CAMOFOX_URL, or configure Browser Use or Browserbase"
        if browser_provider == "Browserbase":
            missing_browser_hint = (
                "npm install -g agent-browser and set "
                "BROWSERBASE_API_KEY/BROWSERBASE_PROJECT_ID"
            )
        elif browser_provider == "Browser Use":
            missing_browser_hint = (
                "npm install -g agent-browser and set BROWSER_USE_API_KEY"
            )
        elif browser_provider == "Camofox":
            missing_browser_hint = "CAMOFOX_URL"
        elif browser_provider == "Local browser":
            missing_browser_hint = "npm install -g agent-browser"
        tool_status.append(
            ("Browser Automation", False, missing_browser_hint)
        )

    # FAL（图像生成）
    if subscription_features.image_gen.managed_by_nous:
        tool_status.append(("Image Generation (Nous subscription)", True, None))
    elif subscription_features.image_gen.available:
        tool_status.append(("Image Generation", True, None))
    else:
        tool_status.append(("Image Generation", False, "FAL_KEY"))

    # TTS — 显示已配置的提供商
    tts_provider = config.get("tts", {}).get("provider", "edge")
    if subscription_features.tts.managed_by_nous:
        tool_status.append(("Text-to-Speech (OpenAI via Nous subscription)", True, None))
    elif tts_provider == "elevenlabs" and get_env_value("ELEVENLABS_API_KEY"):
        tool_status.append(("Text-to-Speech (ElevenLabs)", True, None))
    elif tts_provider == "openai" and (
        get_env_value("VOICE_TOOLS_OPENAI_KEY") or get_env_value("OPENAI_API_KEY")
    ):
        tool_status.append(("Text-to-Speech (OpenAI)", True, None))
    elif tts_provider == "minimax" and get_env_value("MINIMAX_API_KEY"):
        tool_status.append(("Text-to-Speech (MiniMax)", True, None))
    elif tts_provider == "mistral" and get_env_value("MISTRAL_API_KEY"):
        tool_status.append(("Text-to-Speech (Mistral Voxtral)", True, None))
    elif tts_provider == "gemini" and (get_env_value("GEMINI_API_KEY") or get_env_value("GOOGLE_API_KEY")):
        tool_status.append(("Text-to-Speech (Google Gemini)", True, None))
    elif tts_provider == "neutts":
        try:
            import importlib.util
            neutts_ok = importlib.util.find_spec("neutts") is not None
        except Exception:
            neutts_ok = False
        if neutts_ok:
            tool_status.append(("Text-to-Speech (NeuTTS local)", True, None))
        else:
            tool_status.append(("Text-to-Speech (NeuTTS — not installed)", False, "run 'hermes setup tts'"))
    else:
        tool_status.append(("Text-to-Speech (Edge TTS)", True, None))

    if subscription_features.modal.managed_by_nous:
        tool_status.append(("Modal Execution (Nous subscription)", True, None))
    elif config.get("terminal", {}).get("backend") == "modal":
        if subscription_features.modal.direct_override:
            tool_status.append(("Modal Execution (direct Modal)", True, None))
        else:
            tool_status.append(("Modal Execution", False, "run 'hermes setup terminal'"))
    elif managed_nous_tools_enabled() and subscription_features.nous_auth_present:
        tool_status.append(("Modal Execution (optional via Nous subscription)", True, None))

    # Tinker + WandB（强化学习训练）
    if get_env_value("TINKER_API_KEY") and get_env_value("WANDB_API_KEY"):
        tool_status.append(("RL Training (Tinker)", True, None))
    elif get_env_value("TINKER_API_KEY"):
        tool_status.append(("RL Training (Tinker)", False, "WANDB_API_KEY"))
    else:
        tool_status.append(("RL Training (Tinker)", False, "TINKER_API_KEY"))

    # 智能家居
    if get_env_value("HASS_TOKEN"):
        tool_status.append(("Smart Home (Home Assistant)", True, None))

    # 技能中心
    if get_env_value("GITHUB_TOKEN"):
        tool_status.append(("Skills Hub (GitHub)", True, None))
    else:
        tool_status.append(("Skills Hub (GitHub)", False, "GITHUB_TOKEN"))

    # 终端（系统依赖满足则始终可用）
    tool_status.append(("Terminal/Commands", True, None))

    # 任务规划（始终可用，内存中）
    tool_status.append(("Task Planning (todo)", True, None))

    # 技能（始终可用 -- 内置技能 + 用户创建的技能）
    tool_status.append(("Skills (view, create, edit)", True, None))

    # 打印状态
    available_count = sum(1 for _, avail, _ in tool_status if avail)
    total_count = len(tool_status)

    print_info(f"{available_count}/{total_count} tool categories available:")
    print()

    for name, available, missing_var in tool_status:
        if available:
            print(f"   {color('✓', Colors.GREEN)} {name}")
        else:
            print(
                f"   {color('✗', Colors.RED)} {name} {color(f'(missing {missing_var})', Colors.DIM)}"
            )

    print()

    disabled_tools = [(name, var) for name, avail, var in tool_status if not avail]
    if disabled_tools:
        print_warning(
            "Some tools are disabled. Run 'hermes setup tools' to configure them,"
        )
        from hermes_constants import display_hermes_home as _dhh
        print_warning(f"or edit {_dhh()}/.env directly to add the missing API keys.")
        print()

    # 完成横幅
    print()
    print(
        color(
            "┌─────────────────────────────────────────────────────────┐", Colors.GREEN
        )
    )
    print(
        color(
            "│              ✓ Setup Complete!                          │", Colors.GREEN
        )
    )
    print(
        color(
            "└─────────────────────────────────────────────────────────┘", Colors.GREEN
        )
    )
    print()

    # 醒目显示文件位置
    from hermes_constants import display_hermes_home as _dhh
    print(color(f"📁 All your files are in {_dhh()}/:", Colors.CYAN, Colors.BOLD))
    print()
    print(f"   {color('Settings:', Colors.YELLOW)}  {get_config_path()}")
    print(f"   {color('API Keys:', Colors.YELLOW)}  {get_env_path()}")
    print(
        f"   {color('Data:', Colors.YELLOW)}      {hermes_home}/cron/, sessions/, logs/"
    )
    print()

    print(color("─" * 60, Colors.DIM))
    print()
    print(color("📝 To edit your configuration:", Colors.CYAN, Colors.BOLD))
    print()
    print(f"   {color('hermes setup', Colors.GREEN)}          Re-run the full wizard")
    print(f"   {color('hermes setup model', Colors.GREEN)}    Change model/provider")
    print(f"   {color('hermes setup terminal', Colors.GREEN)} Change terminal backend")
    print(f"   {color('hermes setup gateway', Colors.GREEN)}  Configure messaging")
    print(f"   {color('hermes setup tools', Colors.GREEN)}    Configure tool providers")
    print()
    print(f"   {color('hermes config', Colors.GREEN)}         View current settings")
    print(
        f"   {color('hermes config edit', Colors.GREEN)}    Open config in your editor"
    )
    print(f"   {color('hermes config set <key> <value>', Colors.GREEN)}")
    print("                          Set a specific value")
    print()
    print("   Or edit the files directly:")
    print(f"   {color(f'nano {get_config_path()}', Colors.DIM)}")
    print(f"   {color(f'nano {get_env_path()}', Colors.DIM)}")
    print()

    print(color("─" * 60, Colors.DIM))
    print()
    print(color("🚀 Ready to go!", Colors.CYAN, Colors.BOLD))
    print()
    print(f"   {color('hermes', Colors.GREEN)}              Start chatting")
    print(f"   {color('hermes gateway', Colors.GREEN)}      Start messaging gateway")
    print(f"   {color('hermes doctor', Colors.GREEN)}       Check for issues")
    print()


def _prompt_container_resources(config: dict):
    """提示配置容器资源设置（Docker、Singularity、Modal、Daytona）。"""
    terminal = config.setdefault("terminal", {})

    print()
    print_info("Container Resource Settings:")

    # 持久化
    current_persist = terminal.get("container_persistent", True)
    persist_label = "yes" if current_persist else "no"
    print_info("  Persistent filesystem keeps files between sessions.")
    print_info("  Set to 'no' for ephemeral sandboxes that reset each time.")
    persist_str = prompt(
        "  Persist filesystem across sessions? (yes/no)", persist_label
    )
    terminal["container_persistent"] = persist_str.lower() in ("yes", "true", "y", "1")

    # CPU 核数
    current_cpu = terminal.get("container_cpu", 1)
    cpu_str = prompt("  CPU cores", str(current_cpu))
    try:
        terminal["container_cpu"] = float(cpu_str)
    except ValueError:
        pass

    # 内存
    current_mem = terminal.get("container_memory", 5120)
    mem_str = prompt("  Memory in MB (5120 = 5GB)", str(current_mem))
    try:
        terminal["container_memory"] = int(mem_str)
    except ValueError:
        pass

    # 磁盘
    current_disk = terminal.get("container_disk", 51200)
    disk_str = prompt("  Disk in MB (51200 = 50GB)", str(current_disk))
    try:
        terminal["container_disk"] = int(disk_str)
    except ValueError:
        pass


# 工具分类和提供商配置现在在 tools_config.py 中（在
# `hermes tools` 和 `hermes setup tools` 之间共享）。


# =============================================================================
# 第 1 节：模型与提供商配置
# =============================================================================



def setup_model_provider(config: dict, *, quick: bool = False):
    """配置推理提供商和默认模型。

    委托给 ``cmd_model()``（与 ``hermes model`` 使用的相同流程）
    进行提供商选择、凭证提示和模型选择。
    这确保所有提供商设置使用单一代码路径 — 添加到
    ``hermes model`` 的任何新提供商会自动在此处可用。

    当 *quick* 为 True 时，跳过凭证轮换、视觉和 TTS
    配置 — 用于精简的首次快速设置。
    """
    from hermes_cli.config import load_config, save_config

    print_header("Inference Provider")
    print_info("Choose how to connect to your main chat model.")
    print_info(f"   Guide: {_DOCS_BASE}/integrations/providers")
    print()

    # 委托给共享的 hermes model 流程 — 处理提供商选择器、
    # 凭证提示、模型选择和配置持久化。
    from hermes_cli.main import select_provider_and_model
    try:
        select_provider_and_model()
    except (SystemExit, KeyboardInterrupt):
        print()
        print_info("Provider setup skipped.")
    except Exception as exc:
        logger.debug("select_provider_and_model error during setup: %s", exc)
        print_warning(f"Provider setup encountered an error: {exc}")
        print_info("You can try again later with: hermes model")

    # 从 cmd_model 保存到磁盘的内容重新同步向导的配置字典。
    # 这很关键：cmd_model 通过自己的加载/保存周期写入磁盘，
    # 向导最终的 save_config(config) 不能用过期值覆盖那些
    # 更改（#4172）。
    _refreshed = load_config()
    config["model"] = _refreshed.get("model", config.get("model"))
    if "custom_providers" in _refreshed:
        config["custom_providers"] = _refreshed["custom_providers"]
    else:
        config.pop("custom_providers", None)

    # 推导已选提供商用于下游步骤（视觉设置）。
    selected_provider = None
    _m = config.get("model")
    if isinstance(_m, dict):
        selected_provider = _m.get("provider")

    nous_subscription_selected = selected_provider == "nous"

    # ── 同提供商回退和轮换设置（仅完整设置）──
    if not quick and _supports_same_provider_pool_setup(selected_provider):
        try:
            from types import SimpleNamespace
            from agent.credential_pool import load_pool
            from hermes_cli.auth_commands import auth_add_command

            pool = load_pool(selected_provider)
            entries = pool.entries()
            entry_count = len(entries)
            manual_count = sum(1 for entry in entries if str(getattr(entry, "source", "")).startswith("manual"))
            auto_count = entry_count - manual_count
            print()
            print_header("Same-Provider Fallback & Rotation")
            print_info(
                "Hermes can keep multiple credentials for one provider and rotate between"
            )
            print_info(
                "them when a credential is exhausted or rate-limited. This preserves"
            )
            print_info(
                "your primary provider while reducing interruptions from quota issues."
            )
            print()
            if auto_count > 0:
                print_info(
                    f"Current pooled credentials for {selected_provider}: {entry_count} "
                    f"({manual_count} manual, {auto_count} auto-detected from env/shared auth)"
                )
            else:
                print_info(f"Current pooled credentials for {selected_provider}: {entry_count}")

            while prompt_yes_no("Add another credential for same-provider fallback?", False):
                auth_add_command(
                    SimpleNamespace(
                        provider=selected_provider,
                        auth_type="",
                        label=None,
                        api_key=None,
                        portal_url=None,
                        inference_url=None,
                        client_id=None,
                        scope=None,
                        no_browser=False,
                        timeout=15.0,
                        insecure=False,
                        ca_bundle=None,
                        min_key_ttl_seconds=5 * 60,
                    )
                )
                pool = load_pool(selected_provider)
                entry_count = len(pool.entries())
                print_info(f"Provider pool now has {entry_count} credential(s).")

            if entry_count > 1:
                strategy_labels = [
                    "Fill-first / sticky — keep using the first healthy credential until it is exhausted",
                    "Round robin — rotate to the next healthy credential after each selection",
                    "Random — pick a random healthy credential each time",
                ]
                current_strategy = _get_credential_pool_strategies(config).get(selected_provider, "fill_first")
                default_strategy_idx = {
                    "fill_first": 0,
                    "round_robin": 1,
                    "random": 2,
                }.get(current_strategy, 0)
                strategy_idx = prompt_choice(
                    "Select same-provider rotation strategy:",
                    strategy_labels,
                    default_strategy_idx,
                )
                strategy_value = ["fill_first", "round_robin", "random"][strategy_idx]
                _set_credential_pool_strategy(config, selected_provider, strategy_value)
                print_success(f"Saved {selected_provider} rotation strategy: {strategy_value}")
        except Exception as exc:
            logger.debug("Could not configure same-provider fallback in setup: %s", exc)

    # ── 视觉和图像分析设置（仅完整设置）──
    if quick:
        _vision_needs_setup = False
    else:
        try:
            from agent.auxiliary_client import get_available_vision_backends
            _vision_backends = set(get_available_vision_backends())
        except Exception:
            _vision_backends = set()

        _vision_needs_setup = not bool(_vision_backends)

        if selected_provider in _vision_backends:
            _vision_needs_setup = False

    if _vision_needs_setup:
        _prov_names = {
            "nous-api": "Nous Portal API key",
            "copilot": "GitHub Copilot",
            "copilot-acp": "GitHub Copilot ACP",
            "zai": "Z.AI / GLM",
            "kimi-coding": "Kimi / Moonshot",
            "kimi-coding-cn": "Kimi / Moonshot (China)",
            "minimax": "MiniMax",
            "minimax-cn": "MiniMax CN",
            "anthropic": "Anthropic",
            "ai-gateway": "Vercel AI Gateway",
            "custom": "your custom endpoint",
        }
        _prov_display = _prov_names.get(selected_provider, selected_provider or "your provider")

        print()
        print_header("Vision & Image Analysis (optional)")
        print_info(f"Vision uses a separate multimodal backend. {_prov_display}")
        print_info("doesn't currently provide one Hermes can auto-use for vision,")
        print_info("so choose a backend now or skip and configure later.")
        print()

        _vision_choices = [
            "OpenRouter — uses Gemini (free tier at openrouter.ai/keys)",
            "OpenAI-compatible endpoint — base URL, API key, and vision model",
            "Skip for now",
        ]
        _vision_idx = prompt_choice("Configure vision:", _vision_choices, 2)

        if _vision_idx == 0:  # OpenRouter
            _or_key = prompt("  OpenRouter API key", password=True).strip()
            if _or_key:
                save_env_value("OPENROUTER_API_KEY", _or_key)
                print_success("OpenRouter key saved — vision will use Gemini")
            else:
                print_info("Skipped — vision won't be available")
        elif _vision_idx == 1:  # OpenAI-compatible endpoint
            _base_url = prompt("  Base URL (blank for OpenAI)").strip() or "https://api.openai.com/v1"
            _api_key_label = "  API key"
            if "api.openai.com" in _base_url.lower():
                _api_key_label = "  OpenAI API key"
            _oai_key = prompt(_api_key_label, password=True).strip()
            if _oai_key:
                save_env_value("OPENAI_API_KEY", _oai_key)
            # 将视觉 base URL 保存到配置（不是 .env — 只有密钥放那里）
                _vaux = config.setdefault("auxiliary", {}).setdefault("vision", {})
                _vaux["base_url"] = _base_url
                if "api.openai.com" in _base_url.lower():
                    _oai_vision_models = ["gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-4.1-mini", "gpt-4.1-nano"]
                    _vm_choices = _oai_vision_models + ["Use default (gpt-4o-mini)"]
                    _vm_idx = prompt_choice("Select vision model:", _vm_choices, 0)
                    _selected_vision_model = (
                        _oai_vision_models[_vm_idx]
                        if _vm_idx < len(_oai_vision_models)
                        else "gpt-4o-mini"
                    )
                else:
                    _selected_vision_model = prompt("  Vision model (blank = use main/custom default)").strip()
                save_env_value("AUXILIARY_VISION_MODEL", _selected_vision_model)
                print_success(
                    f"Vision configured with {_base_url}"
                    + (f" ({_selected_vision_model})" if _selected_vision_model else "")
                )
            else:
                print_info("Skipped — vision won't be available")
        else:
            print_info("Skipped — add later with 'hermes setup' or configure AUXILIARY_VISION_* settings")


    # 工具网关提示已由上面的 _model_flow_nous() 显示。
    save_config(config)

    if not quick and selected_provider != "nous":
        _setup_tts_provider(config)


# =============================================================================
# 第 1b 节：TTS 提供商配置
# =============================================================================


def _check_espeak_ng() -> bool:
    """检查是否安装了 espeak-ng。"""
    import shutil
    return shutil.which("espeak-ng") is not None or shutil.which("espeak") is not None


def _install_neutts_deps() -> bool:
    """经用户确认后安装 NeuTTS 依赖。成功返回 True。"""
    import subprocess
    import sys

    # 检查 espeak-ng
    if not _check_espeak_ng():
        print()
        print_warning("NeuTTS requires espeak-ng for phonemization.")
        if sys.platform == "darwin":
            print_info("Install with: brew install espeak-ng")
        elif sys.platform == "win32":
            print_info("Install with: choco install espeak-ng")
        else:
            print_info("Install with: sudo apt install espeak-ng")
        print()
        if prompt_yes_no("Install espeak-ng now?", True):
            try:
                if sys.platform == "darwin":
                    subprocess.run(["brew", "install", "espeak-ng"], check=True)
                elif sys.platform == "win32":
                    subprocess.run(["choco", "install", "espeak-ng", "-y"], check=True)
                else:
                    subprocess.run(["sudo", "apt", "install", "-y", "espeak-ng"], check=True)
                print_success("espeak-ng installed")
            except (subprocess.CalledProcessError, FileNotFoundError) as e:
                print_warning(f"Could not install espeak-ng automatically: {e}")
                print_info("Please install it manually and re-run setup.")
                return False
        else:
            print_warning("espeak-ng is required for NeuTTS. Install it manually before using NeuTTS.")

    # 安装 neutts Python 包
    print()
    print_info("Installing neutts Python package...")
    print_info("This will also download the TTS model (~300MB) on first use.")
    print()
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-U", "neutts[all]", "--quiet"],
            check=True, timeout=300,
        )
        print_success("neutts installed successfully")
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        print_error(f"Failed to install neutts: {e}")
        print_info("Try manually: python -m pip install -U neutts[all]")
        return False


def _setup_tts_provider(config: dict):
    """交互式 TTS 提供商选择，包含 NeuTTS 的安装流程。"""
    tts_config = config.get("tts", {})
    current_provider = tts_config.get("provider", "edge")
    subscription_features = get_nous_subscription_features(config)

    provider_labels = {
        "edge": "Edge TTS",
        "elevenlabs": "ElevenLabs",
        "openai": "OpenAI TTS",
        "xai": "xAI TTS",
        "minimax": "MiniMax TTS",
        "mistral": "Mistral Voxtral TTS",
        "gemini": "Google Gemini TTS",
        "neutts": "NeuTTS",
    }
    current_label = provider_labels.get(current_provider, current_provider)

    print()
    print_header("Text-to-Speech Provider (optional)")
    print_info(f"Current: {current_label}")
    print()

    choices = []
    providers = []
    if managed_nous_tools_enabled() and subscription_features.nous_auth_present:
        choices.append("Nous Subscription (managed OpenAI TTS, billed to your subscription)")
        providers.append("nous-openai")
    choices.extend(
        [
            "Edge TTS (free, cloud-based, no setup needed)",
            "ElevenLabs (premium quality, needs API key)",
            "OpenAI TTS (good quality, needs API key)",
            "xAI TTS (Grok voices, needs API key)",
            "MiniMax TTS (high quality with voice cloning, needs API key)",
            "Mistral Voxtral TTS (multilingual, native Opus, needs API key)",
            "Google Gemini TTS (30 prebuilt voices, prompt-controllable, needs API key)",
            "NeuTTS (local on-device, free, ~300MB model download)",
        ]
    )
    providers.extend(["edge", "elevenlabs", "openai", "xai", "minimax", "mistral", "gemini", "neutts"])
    choices.append(f"Keep current ({current_label})")
    keep_current_idx = len(choices) - 1
    idx = prompt_choice("Select TTS provider:", choices, keep_current_idx)

    if idx == keep_current_idx:
        return

    selected = providers[idx]
    selected_via_nous = selected == "nous-openai"
    if selected == "nous-openai":
        selected = "openai"
        print_info("OpenAI TTS will use the managed Nous gateway and bill to your subscription.")
        if get_env_value("VOICE_TOOLS_OPENAI_KEY") or get_env_value("OPENAI_API_KEY"):
            print_warning(
                "Direct OpenAI credentials are still configured and may take precedence until removed from ~/.hermes/.env."
            )

    if selected == "neutts":
    # 检查是否已安装
        try:
            import importlib.util
            already_installed = importlib.util.find_spec("neutts") is not None
        except Exception:
            already_installed = False

        if already_installed:
            print_success("NeuTTS is already installed")
        else:
            print()
            print_info("NeuTTS requires:")
            print_info("  • Python package: neutts (~50MB install + ~300MB model on first use)")
            print_info("  • System package: espeak-ng (phonemizer)")
            print()
            if prompt_yes_no("Install NeuTTS dependencies now?", True):
                if not _install_neutts_deps():
                    print_warning("NeuTTS installation incomplete. Falling back to Edge TTS.")
                    selected = "edge"
            else:
                print_info("Skipping install. Set tts.provider to 'neutts' after installing manually.")
                selected = "edge"

    elif selected == "elevenlabs":
        existing = get_env_value("ELEVENLABS_API_KEY")
        if not existing:
            print()
            api_key = prompt("ElevenLabs API key", password=True)
            if api_key:
                save_env_value("ELEVENLABS_API_KEY", api_key)
                print_success("ElevenLabs API key saved")
            else:
                print_warning("No API key provided. Falling back to Edge TTS.")
                selected = "edge"

    elif selected == "openai" and not selected_via_nous:
        existing = get_env_value("VOICE_TOOLS_OPENAI_KEY") or get_env_value("OPENAI_API_KEY")
        if not existing:
            print()
            api_key = prompt("OpenAI API key for TTS", password=True)
            if api_key:
                save_env_value("VOICE_TOOLS_OPENAI_KEY", api_key)
                print_success("OpenAI TTS API key saved")
            else:
                print_warning("No API key provided. Falling back to Edge TTS.")
                selected = "edge"

    elif selected == "xai":
        existing = get_env_value("XAI_API_KEY")
        if not existing:
            print()
            api_key = prompt("xAI API key for TTS", password=True)
            if api_key:
                save_env_value("XAI_API_KEY", api_key)
                print_success("xAI TTS API key saved")
            else:
                from hermes_constants import display_hermes_home as _dhh
                print_warning(
                    "No xAI API key provided for TTS. Configure XAI_API_KEY via "
                    f"hermes setup model or {_dhh()}/.env to use xAI TTS. "
                    "Falling back to Edge TTS."
                )
                selected = "edge"

    elif selected == "minimax":
        existing = get_env_value("MINIMAX_API_KEY")
        if not existing:
            print()
            api_key = prompt("MiniMax API key for TTS", password=True)
            if api_key:
                save_env_value("MINIMAX_API_KEY", api_key)
                print_success("MiniMax TTS API key saved")
            else:
                print_warning("No API key provided. Falling back to Edge TTS.")
                selected = "edge"

    elif selected == "mistral":
        existing = get_env_value("MISTRAL_API_KEY")
        if not existing:
            print()
            api_key = prompt("Mistral API key for TTS", password=True)
            if api_key:
                save_env_value("MISTRAL_API_KEY", api_key)
                print_success("Mistral TTS API key saved")
            else:
                print_warning("No API key provided. Falling back to Edge TTS.")
                selected = "edge"

    elif selected == "gemini":
        existing = get_env_value("GEMINI_API_KEY") or get_env_value("GOOGLE_API_KEY")
        if not existing:
            print()
            print_info("Get a free API key at https://aistudio.google.com/app/apikey")
            api_key = prompt("Gemini API key for TTS", password=True)
            if api_key:
                save_env_value("GEMINI_API_KEY", api_key)
                print_success("Gemini TTS API key saved")
            else:
                print_warning("No API key provided. Falling back to Edge TTS.")
                selected = "edge"

    # 保存选择
    if "tts" not in config:
        config["tts"] = {}
    config["tts"]["provider"] = selected
    save_config(config)
    print_success(f"TTS provider set to: {provider_labels.get(selected, selected)}")


def setup_tts(config: dict):
    """独立 TTS 设置（用于 'hermes setup tts'）。"""
    _setup_tts_provider(config)


# =============================================================================
# 第 2 节：终端后端配置
# =============================================================================


def setup_terminal_backend(config: dict):
    """配置终端执行后端。"""
    import platform as _platform
    import shutil

    print_header("Terminal Backend")
    print_info("Choose where Hermes runs shell commands and code.")
    print_info("This affects tool execution, file access, and isolation.")
    print_info(f"   Guide: {_DOCS_BASE}/developer-guide/environments")
    print()

    current_backend = config.get("terminal", {}).get("backend", "local")
    is_linux = _platform.system() == "Linux"

    # 构建带描述的后端选项
    terminal_choices = [
        "Local - run directly on this machine (default)",
        "Docker - isolated container with configurable resources",
        "Modal - serverless cloud sandbox",
        "SSH - run on a remote machine",
        "Daytona - persistent cloud development environment",
    ]
    idx_to_backend = {0: "local", 1: "docker", 2: "modal", 3: "ssh", 4: "daytona"}
    backend_to_idx = {"local": 0, "docker": 1, "modal": 2, "ssh": 3, "daytona": 4}

    next_idx = 5
    if is_linux:
        terminal_choices.append("Singularity/Apptainer - HPC-friendly container")
        idx_to_backend[next_idx] = "singularity"
        backend_to_idx["singularity"] = next_idx
        next_idx += 1

    # 添加保留当前选项
    keep_current_idx = next_idx
    terminal_choices.append(f"Keep current ({current_backend})")
    idx_to_backend[keep_current_idx] = current_backend

    terminal_idx = prompt_choice(
        "Select terminal backend:", terminal_choices, keep_current_idx
    )

    selected_backend = idx_to_backend.get(terminal_idx)

    if terminal_idx == keep_current_idx:
        print_info(f"Keeping current backend: {current_backend}")
        return

    config.setdefault("terminal", {})["backend"] = selected_backend

    if selected_backend == "local":
        print_success("Terminal backend: Local")
        print_info("Commands run directly on this machine.")

        # 消息传递的工作目录
        print()
        print_info("Working directory for messaging sessions:")
        print_info("  When using Hermes via Telegram/Discord, this is where")
        print_info(
            "  the agent starts. CLI mode always starts in the current directory."
        )
        current_cwd = config.get("terminal", {}).get("cwd", "")
        cwd = prompt("  Messaging working directory", current_cwd or str(Path.home()))
        if cwd:
            config["terminal"]["cwd"] = cwd

        # Sudo 支持
        print()
        existing_sudo = get_env_value("SUDO_PASSWORD")
        if existing_sudo:
            print_info("Sudo password: configured")
        else:
            if prompt_yes_no(
                "Enable sudo support? (stores password for apt install, etc.)", False
            ):
                sudo_pass = prompt("  Sudo password", password=True)
                if sudo_pass:
                    save_env_value("SUDO_PASSWORD", sudo_pass)
                    print_success("Sudo password saved")

    elif selected_backend == "docker":
        print_success("Terminal backend: Docker")

        # 检查 Docker 是否可用
        docker_bin = shutil.which("docker")
        if not docker_bin:
            print_warning("Docker not found in PATH!")
            print_info("Install Docker: https://docs.docker.com/get-docker/")
        else:
            print_info(f"Docker found: {docker_bin}")

        # Docker image
        current_image = config.get("terminal", {}).get(
            "docker_image", "nikolaik/python-nodejs:python3.11-nodejs20"
        )
        image = prompt("  Docker image", current_image)
        config["terminal"]["docker_image"] = image
        save_env_value("TERMINAL_DOCKER_IMAGE", image)

        _prompt_container_resources(config)

    elif selected_backend == "singularity":
        print_success("Terminal backend: Singularity/Apptainer")

        # Check if singularity/apptainer is available
        sing_bin = shutil.which("apptainer") or shutil.which("singularity")
        if not sing_bin:
            print_warning("Singularity/Apptainer not found in PATH!")
            print_info(
                "Install: https://apptainer.org/docs/admin/main/installation.html"
            )
        else:
            print_info(f"Found: {sing_bin}")

        current_image = config.get("terminal", {}).get(
            "singularity_image", "docker://nikolaik/python-nodejs:python3.11-nodejs20"
        )
        image = prompt("  Container image", current_image)
        config["terminal"]["singularity_image"] = image
        save_env_value("TERMINAL_SINGULARITY_IMAGE", image)

        _prompt_container_resources(config)

    elif selected_backend == "modal":
        print_success("Terminal backend: Modal")
        print_info("Serverless cloud sandboxes. Each session gets its own container.")
        from tools.managed_tool_gateway import is_managed_tool_gateway_ready
        from tools.tool_backend_helpers import normalize_modal_mode

        managed_modal_available = bool(
            managed_nous_tools_enabled()
            and
            get_nous_subscription_features(config).nous_auth_present
            and is_managed_tool_gateway_ready("modal")
        )
        modal_mode = normalize_modal_mode(config.get("terminal", {}).get("modal_mode"))
        use_managed_modal = False
        if managed_modal_available:
            modal_choices = [
                "Use my Nous subscription",
                "Use my own Modal account",
            ]
            if modal_mode == "managed":
                default_modal_idx = 0
            elif modal_mode == "direct":
                default_modal_idx = 1
            else:
                default_modal_idx = 1 if get_env_value("MODAL_TOKEN_ID") else 0
            modal_mode_idx = prompt_choice(
                "Select how Modal execution should be billed:",
                modal_choices,
                default_modal_idx,
            )
            use_managed_modal = modal_mode_idx == 0

        if use_managed_modal:
            config["terminal"]["modal_mode"] = "managed"
            print_info("Modal execution will use the managed Nous gateway and bill to your subscription.")
            if get_env_value("MODAL_TOKEN_ID") or get_env_value("MODAL_TOKEN_SECRET"):
                print_info(
                    "Direct Modal credentials are still configured, but this backend is pinned to managed mode."
                )
        else:
            config["terminal"]["modal_mode"] = "direct"
            print_info("Requires a Modal account: https://modal.com")

            # Check if modal SDK is installed
            try:
                __import__("modal")
            except ImportError:
                print_info("Installing modal SDK...")
                import subprocess

                uv_bin = shutil.which("uv")
                if uv_bin:
                    result = subprocess.run(
                        [
                            uv_bin,
                            "pip",
                            "install",
                            "--python",
                            sys.executable,
                            "modal",
                        ],
                        capture_output=True,
                        text=True,
                    )
                else:
                    result = subprocess.run(
                        [sys.executable, "-m", "pip", "install", "modal"],
                        capture_output=True,
                        text=True,
                    )
                if result.returncode == 0:
                    print_success("modal SDK installed")
                else:
                    print_warning("Install failed — run manually: pip install modal")

            # Modal token
            print()
            print_info("Modal authentication:")
            print_info("  Get your token at: https://modal.com/settings")
            existing_token = get_env_value("MODAL_TOKEN_ID")
            if existing_token:
                print_info("  Modal token: already configured")
                if prompt_yes_no("  Update Modal credentials?", False):
                    token_id = prompt("    Modal Token ID", password=True)
                    token_secret = prompt("    Modal Token Secret", password=True)
                    if token_id:
                        save_env_value("MODAL_TOKEN_ID", token_id)
                    if token_secret:
                        save_env_value("MODAL_TOKEN_SECRET", token_secret)
            else:
                token_id = prompt("    Modal Token ID", password=True)
                token_secret = prompt("    Modal Token Secret", password=True)
                if token_id:
                    save_env_value("MODAL_TOKEN_ID", token_id)
                if token_secret:
                    save_env_value("MODAL_TOKEN_SECRET", token_secret)

        _prompt_container_resources(config)

    elif selected_backend == "daytona":
        print_success("Terminal backend: Daytona")
        print_info("Persistent cloud development environments.")
        print_info("Each session gets a dedicated sandbox with filesystem persistence.")
        print_info("Sign up at: https://daytona.io")

        # Check if daytona SDK is installed
        try:
            __import__("daytona")
        except ImportError:
            print_info("Installing daytona SDK...")
            import subprocess

            uv_bin = shutil.which("uv")
            if uv_bin:
                result = subprocess.run(
                    [uv_bin, "pip", "install", "--python", sys.executable, "daytona"],
                    capture_output=True,
                    text=True,
                )
            else:
                result = subprocess.run(
                    [sys.executable, "-m", "pip", "install", "daytona"],
                    capture_output=True,
                    text=True,
                )
            if result.returncode == 0:
                print_success("daytona SDK installed")
            else:
                print_warning("Install failed — run manually: pip install daytona")
                if result.stderr:
                    print_info(f"  Error: {result.stderr.strip().splitlines()[-1]}")

        # Daytona API key
        print()
        existing_key = get_env_value("DAYTONA_API_KEY")
        if existing_key:
            print_info("  Daytona API key: already configured")
            if prompt_yes_no("  Update API key?", False):
                api_key = prompt("    Daytona API key", password=True)
                if api_key:
                    save_env_value("DAYTONA_API_KEY", api_key)
                    print_success("    Updated")
        else:
            api_key = prompt("    Daytona API key", password=True)
            if api_key:
                save_env_value("DAYTONA_API_KEY", api_key)
                print_success("    Configured")

        # Daytona image
        current_image = config.get("terminal", {}).get(
            "daytona_image", "nikolaik/python-nodejs:python3.11-nodejs20"
        )
        image = prompt("  Sandbox image", current_image)
        config["terminal"]["daytona_image"] = image
        save_env_value("TERMINAL_DAYTONA_IMAGE", image)

        _prompt_container_resources(config)

    elif selected_backend == "ssh":
        print_success("Terminal backend: SSH")
        print_info("Run commands on a remote machine via SSH.")

        # SSH host
        current_host = get_env_value("TERMINAL_SSH_HOST") or ""
        host = prompt("  SSH host (hostname or IP)", current_host)
        if host:
            save_env_value("TERMINAL_SSH_HOST", host)

        # SSH user
        current_user = get_env_value("TERMINAL_SSH_USER") or ""
        user = prompt("  SSH user", current_user or os.getenv("USER", ""))
        if user:
            save_env_value("TERMINAL_SSH_USER", user)

        # SSH port
        current_port = get_env_value("TERMINAL_SSH_PORT") or "22"
        port = prompt("  SSH port", current_port)
        if port and port != "22":
            save_env_value("TERMINAL_SSH_PORT", port)

        # SSH key
        current_key = get_env_value("TERMINAL_SSH_KEY") or ""
        default_key = str(Path.home() / ".ssh" / "id_rsa")
        ssh_key = prompt("  SSH private key path", current_key or default_key)
        if ssh_key:
            save_env_value("TERMINAL_SSH_KEY", ssh_key)

        # Test connection
        if host and prompt_yes_no("  Test SSH connection?", True):
            print_info("  Testing connection...")
            import subprocess

            ssh_cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5"]
            if ssh_key:
                ssh_cmd.extend(["-i", ssh_key])
            if port and port != "22":
                ssh_cmd.extend(["-p", port])
            ssh_cmd.append(f"{user}@{host}" if user else host)
            ssh_cmd.append("echo ok")
            result = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                print_success("  SSH connection successful!")
            else:
                print_warning(f"  SSH connection failed: {result.stderr.strip()}")
                print_info("  Check your SSH key and host settings.")

    # 同步终端后端到 .env 以便 terminal_tool 直接读取。
    # config.yaml 是事实来源，但 terminal_tool 读取 TERMINAL_ENV。
    save_env_value("TERMINAL_ENV", selected_backend)
    if selected_backend == "modal":
        save_env_value("TERMINAL_MODAL_MODE", config["terminal"].get("modal_mode", "auto"))
    save_config(config)
    print()
    print_success(f"Terminal backend set to: {selected_backend}")


# =============================================================================
# 第 3 节：智能体设置
# =============================================================================


def _apply_default_agent_settings(config: dict):
    """无需提示，直接应用所有智能体设置的推荐默认值。"""
    config.setdefault("agent", {})["max_turns"] = 90
    save_env_value("HERMES_MAX_ITERATIONS", "90")

    config.setdefault("display", {})["tool_progress"] = "all"

    config.setdefault("compression", {})["enabled"] = True
    config["compression"]["threshold"] = 0.50

    config.setdefault("session_reset", {}).update({
        "mode": "both",
        "idle_minutes": 1440,
        "at_hour": 4,
    })

    save_config(config)
    print_success("Applied recommended defaults:")
    print_info("  Max iterations: 90")
    print_info("  Tool progress: all")
    print_info("  Compression threshold: 0.50")
    print_info("  Session reset: inactivity (1440 min) + daily (4:00)")
    print_info("  Run `hermes setup agent` later to customize.")


def setup_agent_settings(config: dict):
    """配置智能体行为：迭代次数、进度显示、压缩、会话重置。"""

    print_header("Agent Settings")
    print_info(f"   Guide: {_DOCS_BASE}/user-guide/configuration")
    print()

    # ── 最大迭代次数 ──
    current_max = get_env_value("HERMES_MAX_ITERATIONS") or str(
        config.get("agent", {}).get("max_turns", 90)
    )
    print_info("Maximum tool-calling iterations per conversation.")
    print_info("Higher = more complex tasks, but costs more tokens.")
    print_info("Default is 90, which works for most tasks. Use 150+ for open exploration.")

    max_iter_str = prompt("Max iterations", current_max)
    try:
        max_iter = int(max_iter_str)
        if max_iter > 0:
            save_env_value("HERMES_MAX_ITERATIONS", str(max_iter))
            config.setdefault("agent", {})["max_turns"] = max_iter
            config.pop("max_turns", None)
            print_success(f"Max iterations set to {max_iter}")
    except ValueError:
        print_warning("Invalid number, keeping current value")

    # ── 工具进度显示 ──
    print_info("")
    print_info("Tool Progress Display")
    print_info("Controls how much tool activity is shown (CLI and messaging).")
    print_info("  off     — Silent, just the final response")
    print_info("  new     — Show tool name only when it changes (less noise)")
    print_info("  all     — Show every tool call with a short preview")
    print_info("  verbose — Full args, results, and debug logs")

    current_mode = config.get("display", {}).get("tool_progress", "all")
    mode = prompt("Tool progress mode", current_mode)
    if mode.lower() in ("off", "new", "all", "verbose"):
        if "display" not in config:
            config["display"] = {}
        config["display"]["tool_progress"] = mode.lower()
        save_config(config)
        print_success(f"Tool progress set to: {mode.lower()}")
    else:
        print_warning(f"Unknown mode '{mode}', keeping '{current_mode}'")

    # ── 上下文压缩 ──
    print_header("Context Compression")
    print_info("Automatically summarizes old messages when context gets too long.")
    print_info(
        "Higher threshold = compress later (use more context). Lower = compress sooner."
    )

    config.setdefault("compression", {})["enabled"] = True

    current_threshold = config.get("compression", {}).get("threshold", 0.50)
    threshold_str = prompt("Compression threshold (0.5-0.95)", str(current_threshold))
    try:
        threshold = float(threshold_str)
        if 0.5 <= threshold <= 0.95:
            config["compression"]["threshold"] = threshold
    except ValueError:
        pass

    print_success(
        f"Context compression threshold set to {config['compression'].get('threshold', 0.50)}"
    )

    # ── 会话重置策略 ──
    print_header("Session Reset Policy")
    print_info(
        "Messaging sessions (Telegram, Discord, etc.) accumulate context over time."
    )
    print_info(
        "Each message adds to the conversation history, which means growing API costs."
    )
    print_info("")
    print_info(
        "To manage this, sessions can automatically reset after a period of inactivity"
    )
    print_info(
        "or at a fixed time each day. When a reset happens, the agent saves important"
    )
    print_info(
        "things to its persistent memory first — but the conversation context is cleared."
    )
    print_info("")
    print_info("You can also manually reset anytime by typing /reset in chat.")
    print_info("")

    reset_choices = [
        "Inactivity + daily reset (recommended - reset whichever comes first)",
        "Inactivity only (reset after N minutes of no messages)",
        "Daily only (reset at a fixed hour each day)",
        "Never auto-reset (context lives until /reset or context compression)",
        "Keep current settings",
    ]

    current_policy = config.get("session_reset", {})
    current_mode = current_policy.get("mode", "both")
    current_idle = current_policy.get("idle_minutes", 1440)
    current_hour = current_policy.get("at_hour", 4)

    default_reset = {"both": 0, "idle": 1, "daily": 2, "none": 3}.get(current_mode, 0)

    reset_idx = prompt_choice("Session reset mode:", reset_choices, default_reset)

    config.setdefault("session_reset", {})

    if reset_idx == 0:  # Both
        config["session_reset"]["mode"] = "both"
        idle_str = prompt("  Inactivity timeout (minutes)", str(current_idle))
        try:
            idle_val = int(idle_str)
            if idle_val > 0:
                config["session_reset"]["idle_minutes"] = idle_val
        except ValueError:
            pass
        hour_str = prompt("  Daily reset hour (0-23, local time)", str(current_hour))
        try:
            hour_val = int(hour_str)
            if 0 <= hour_val <= 23:
                config["session_reset"]["at_hour"] = hour_val
        except ValueError:
            pass
        print_success(
            f"Sessions reset after {config['session_reset'].get('idle_minutes', 1440)} min idle or daily at {config['session_reset'].get('at_hour', 4)}:00"
        )
    elif reset_idx == 1:  # Idle only
        config["session_reset"]["mode"] = "idle"
        idle_str = prompt("  Inactivity timeout (minutes)", str(current_idle))
        try:
            idle_val = int(idle_str)
            if idle_val > 0:
                config["session_reset"]["idle_minutes"] = idle_val
        except ValueError:
            pass
        print_success(
            f"Sessions reset after {config['session_reset'].get('idle_minutes', 1440)} min of inactivity"
        )
    elif reset_idx == 2:  # Daily only
        config["session_reset"]["mode"] = "daily"
        hour_str = prompt("  Daily reset hour (0-23, local time)", str(current_hour))
        try:
            hour_val = int(hour_str)
            if 0 <= hour_val <= 23:
                config["session_reset"]["at_hour"] = hour_val
        except ValueError:
            pass
        print_success(
            f"Sessions reset daily at {config['session_reset'].get('at_hour', 4)}:00"
        )
    elif reset_idx == 3:  # None
        config["session_reset"]["mode"] = "none"
        print_info(
            "Sessions will never auto-reset. Context is managed only by compression."
        )
        print_warning(
            "Long conversations will grow in cost. Use /reset manually when needed."
        )
    # else: keep current (idx == 4)

    save_config(config)


# =============================================================================
# 第 4 节：消息平台（网关）
# =============================================================================


def _setup_telegram():
    """配置 Telegram 机器人凭证和允许列表。"""
    print_header("Telegram")
    existing = get_env_value("TELEGRAM_BOT_TOKEN")
    if existing:
        print_info("Telegram: already configured")
        if not prompt_yes_no("Reconfigure Telegram?", False):
    # 检查已有配置上缺失的允许列表
            if not get_env_value("TELEGRAM_ALLOWED_USERS"):
                print_info("⚠️  Telegram has no user allowlist - anyone can use your bot!")
                if prompt_yes_no("Add allowed users now?", True):
                    print_info("   To find your Telegram user ID: message @userinfobot")
                    allowed_users = prompt("Allowed user IDs (comma-separated)")
                    if allowed_users:
                        save_env_value("TELEGRAM_ALLOWED_USERS", allowed_users.replace(" ", ""))
                        print_success("Telegram allowlist configured")
            return

    print_info("Create a bot via @BotFather on Telegram")
    import re

    while True:
        token = prompt("Telegram bot token", password=True)
        if not token:
            return
        if not re.match(r"^\d+:[A-Za-z0-9_-]{30,}$", token):
            print_error(
                "Invalid token format. Expected: <numeric_id>:<alphanumeric_hash> "
                "(e.g., 123456789:ABCdefGHI-jklMNOpqrSTUvwxYZ)"
            )
            continue
        break
    save_env_value("TELEGRAM_BOT_TOKEN", token)
    print_success("Telegram token saved")

    print()
    print_info("🔒 Security: Restrict who can use your bot")
    print_info("   To find your Telegram user ID:")
    print_info("   1. Message @userinfobot on Telegram")
    print_info("   2. It will reply with your numeric ID (e.g., 123456789)")
    print()
    allowed_users = prompt(
        "Allowed user IDs (comma-separated, leave empty for open access)"
    )
    if allowed_users:
        save_env_value("TELEGRAM_ALLOWED_USERS", allowed_users.replace(" ", ""))
        print_success("Telegram allowlist configured - only listed users can use the bot")
    else:
        print_info("⚠️  No allowlist set - anyone who finds your bot can use it!")

    print()
    print_info("📬 Home Channel: where Hermes delivers cron job results,")
    print_info("   cross-platform messages, and notifications.")
    print_info("   For Telegram DMs, this is your user ID (same as above).")

    first_user_id = allowed_users.split(",")[0].strip() if allowed_users else ""
    if first_user_id:
        if prompt_yes_no(f"Use your user ID ({first_user_id}) as the home channel?", True):
            save_env_value("TELEGRAM_HOME_CHANNEL", first_user_id)
            print_success(f"Telegram home channel set to {first_user_id}")
        else:
            home_channel = prompt("Home channel ID (or leave empty to set later with /set-home in Telegram)")
            if home_channel:
                save_env_value("TELEGRAM_HOME_CHANNEL", home_channel)
    else:
        print_info("   You can also set this later by typing /set-home in your Telegram chat.")
        home_channel = prompt("Home channel ID (leave empty to set later)")
        if home_channel:
            save_env_value("TELEGRAM_HOME_CHANNEL", home_channel)


def _setup_discord():
    """配置 Discord 机器人凭证和允许列表。"""
    print_header("Discord")
    existing = get_env_value("DISCORD_BOT_TOKEN")
    if existing:
        print_info("Discord: already configured")
        if not prompt_yes_no("Reconfigure Discord?", False):
            if not get_env_value("DISCORD_ALLOWED_USERS"):
                print_info("⚠️  Discord has no user allowlist - anyone can use your bot!")
                if prompt_yes_no("Add allowed users now?", True):
                    print_info("   To find Discord ID: Enable Developer Mode, right-click name → Copy ID")
                    allowed_users = prompt("Allowed user IDs (comma-separated)")
                    if allowed_users:
                        cleaned_ids = _clean_discord_user_ids(allowed_users)
                        save_env_value("DISCORD_ALLOWED_USERS", ",".join(cleaned_ids))
                        print_success("Discord allowlist configured")
            return

    print_info("Create a bot at https://discord.com/developers/applications")
    token = prompt("Discord bot token", password=True)
    if not token:
        return
    save_env_value("DISCORD_BOT_TOKEN", token)
    print_success("Discord token saved")

    print()
    print_info("🔒 Security: Restrict who can use your bot")
    print_info("   To find your Discord user ID:")
    print_info("   1. Enable Developer Mode in Discord settings")
    print_info("   2. Right-click your name → Copy ID")
    print()
    print_info("   You can also use Discord usernames (resolved on gateway start).")
    print()
    allowed_users = prompt(
        "Allowed user IDs or usernames (comma-separated, leave empty for open access)"
    )
    if allowed_users:
        cleaned_ids = _clean_discord_user_ids(allowed_users)
        save_env_value("DISCORD_ALLOWED_USERS", ",".join(cleaned_ids))
        print_success("Discord allowlist configured")
    else:
        print_info("⚠️  No allowlist set - anyone in servers with your bot can use it!")

    print()
    print_info("📬 Home Channel: where Hermes delivers cron job results,")
    print_info("   cross-platform messages, and notifications.")
    print_info("   To get a channel ID: right-click a channel → Copy Channel ID")
    print_info("   (requires Developer Mode in Discord settings)")
    print_info("   You can also set this later by typing /set-home in a Discord channel.")
    home_channel = prompt("Home channel ID (leave empty to set later with /set-home)")
    if home_channel:
        save_env_value("DISCORD_HOME_CHANNEL", home_channel)


def _clean_discord_user_ids(raw: str) -> list:
    """从逗号分隔的 ID 字符串中去除常见的 Discord 提及前缀。"""
    cleaned = []
    for uid in raw.replace(" ", "").split(","):
        uid = uid.strip()
        if uid.startswith("<@") and uid.endswith(">"):
            uid = uid.lstrip("<@!").rstrip(">")
        if uid.lower().startswith("user:"):
            uid = uid[5:]
        if uid:
            cleaned.append(uid)
    return cleaned


def _setup_slack():
    """配置 Slack 机器人凭证。"""
    print_header("Slack")
    existing = get_env_value("SLACK_BOT_TOKEN")
    if existing:
        print_info("Slack: already configured")
        if not prompt_yes_no("Reconfigure Slack?", False):
            return

    print_info("Steps to create a Slack app:")
    print_info("   1. Go to https://api.slack.com/apps → Create New App (from scratch)")
    print_info("   2. Enable Socket Mode: Settings → Socket Mode → Enable")
    print_info("      • Create an App-Level Token with 'connections:write' scope")
    print_info("   3. Add Bot Token Scopes: Features → OAuth & Permissions")
    print_info("      Required scopes: chat:write, app_mentions:read,")
    print_info("      channels:history, channels:read, im:history,")
    print_info("      im:read, im:write, users:read, files:read, files:write")
    print_info("      Optional for private channels: groups:history")
    print_info("   4. Subscribe to Events: Features → Event Subscriptions → Enable")
    print_info("      Required events: message.im, message.channels, app_mention")
    print_info("      Optional for private channels: message.groups")
    print_warning("   ⚠ Without message.channels the bot will ONLY work in DMs,")
    print_warning("     not public channels.")
    print_info("   5. Install to Workspace: Settings → Install App")
    print_info("   6. Reinstall the app after any scope or event changes")
    print_info("   7. After installing, invite the bot to channels: /invite @YourBot")
    print()
    print_info("   Full guide: https://hermes-agent.nousresearch.com/docs/user-guide/messaging/slack/")
    print()
    bot_token = prompt("Slack Bot Token (xoxb-...)", password=True)
    if not bot_token:
        return
    save_env_value("SLACK_BOT_TOKEN", bot_token)
    app_token = prompt("Slack App Token (xapp-...)", password=True)
    if app_token:
        save_env_value("SLACK_APP_TOKEN", app_token)
    print_success("Slack tokens saved")

    print()
    print_info("🔒 Security: Restrict who can use your bot")
    print_info("   To find a Member ID: click a user's name → View full profile → ⋮ → Copy member ID")
    print()
    allowed_users = prompt(
        "Allowed user IDs (comma-separated, leave empty to deny everyone except paired users)"
    )
    if allowed_users:
        save_env_value("SLACK_ALLOWED_USERS", allowed_users.replace(" ", ""))
        print_success("Slack allowlist configured")
    else:
        print_warning("⚠️  No Slack allowlist set - unpaired users will be denied by default.")
        print_info("   Set SLACK_ALLOW_ALL_USERS=true or GATEWAY_ALLOW_ALL_USERS=true only if you intentionally want open workspace access.")


def _setup_matrix():
    """配置 Matrix 凭证。"""
    print_header("Matrix")
    existing = get_env_value("MATRIX_ACCESS_TOKEN") or get_env_value("MATRIX_PASSWORD")
    if existing:
        print_info("Matrix: already configured")
        if not prompt_yes_no("Reconfigure Matrix?", False):
            return

    print_info("Works with any Matrix homeserver (Synapse, Conduit, Dendrite, or matrix.org).")
    print_info("   1. Create a bot user on your homeserver, or use your own account")
    print_info("   2. Get an access token from Element, or provide user ID + password")
    print()
    homeserver = prompt("Homeserver URL (e.g. https://matrix.example.org)")
    if homeserver:
        save_env_value("MATRIX_HOMESERVER", homeserver.rstrip("/"))

    print()
    print_info("Auth: provide an access token (recommended), or user ID + password.")
    token = prompt("Access token (leave empty for password login)", password=True)
    if token:
        save_env_value("MATRIX_ACCESS_TOKEN", token)
        user_id = prompt("User ID (@bot:server — optional, will be auto-detected)")
        if user_id:
            save_env_value("MATRIX_USER_ID", user_id)
        print_success("Matrix access token saved")
    else:
        user_id = prompt("User ID (@bot:server)")
        if user_id:
            save_env_value("MATRIX_USER_ID", user_id)
        password = prompt("Password", password=True)
        if password:
            save_env_value("MATRIX_PASSWORD", password)
            print_success("Matrix credentials saved")

    if token or get_env_value("MATRIX_PASSWORD"):
        print()
        want_e2ee = prompt_yes_no("Enable end-to-end encryption (E2EE)?", False)
        if want_e2ee:
            save_env_value("MATRIX_ENCRYPTION", "true")
            print_success("E2EE enabled")

        matrix_pkg = "mautrix[encryption]" if want_e2ee else "mautrix"
        try:
            __import__("mautrix")
        except ImportError:
            print_info(f"Installing {matrix_pkg}...")
            import subprocess
            uv_bin = shutil.which("uv")
            if uv_bin:
                result = subprocess.run(
                    [uv_bin, "pip", "install", "--python", sys.executable, matrix_pkg],
                    capture_output=True, text=True,
                )
            else:
                result = subprocess.run(
                    [sys.executable, "-m", "pip", "install", matrix_pkg],
                    capture_output=True, text=True,
                )
            if result.returncode == 0:
                print_success(f"{matrix_pkg} installed")
            else:
                print_warning(f"Install failed — run manually: pip install '{matrix_pkg}'")
                if result.stderr:
                    print_info(f"  Error: {result.stderr.strip().splitlines()[-1]}")

        print()
        print_info("🔒 Security: Restrict who can use your bot")
        print_info("   Matrix user IDs look like @username:server")
        print()
        allowed_users = prompt("Allowed user IDs (comma-separated, leave empty for open access)")
        if allowed_users:
            save_env_value("MATRIX_ALLOWED_USERS", allowed_users.replace(" ", ""))
            print_success("Matrix allowlist configured")
        else:
            print_info("⚠️  No allowlist set - anyone who can message the bot can use it!")

        print()
        print_info("📬 Home Room: where Hermes delivers cron job results and notifications.")
        print_info("   Room IDs look like !abc123:server (shown in Element room settings)")
        print_info("   You can also set this later by typing /set-home in a Matrix room.")
        home_room = prompt("Home room ID (leave empty to set later with /set-home)")
        if home_room:
            save_env_value("MATRIX_HOME_ROOM", home_room)


def _setup_mattermost():
    """配置 Mattermost 机器人凭证。"""
    print_header("Mattermost")
    existing = get_env_value("MATTERMOST_TOKEN")
    if existing:
        print_info("Mattermost: already configured")
        if not prompt_yes_no("Reconfigure Mattermost?", False):
            return

    print_info("Works with any self-hosted Mattermost instance.")
    print_info("   1. In Mattermost: Integrations → Bot Accounts → Add Bot Account")
    print_info("   2. Copy the bot token")
    print()
    mm_url = prompt("Mattermost server URL (e.g. https://mm.example.com)")
    if mm_url:
        save_env_value("MATTERMOST_URL", mm_url.rstrip("/"))
    token = prompt("Bot token", password=True)
    if not token:
        return
    save_env_value("MATTERMOST_TOKEN", token)
    print_success("Mattermost token saved")

    print()
    print_info("🔒 Security: Restrict who can use your bot")
    print_info("   To find your user ID: click your avatar → Profile")
    print_info("   or use the API: GET /api/v4/users/me")
    print()
    allowed_users = prompt("Allowed user IDs (comma-separated, leave empty for open access)")
    if allowed_users:
        save_env_value("MATTERMOST_ALLOWED_USERS", allowed_users.replace(" ", ""))
        print_success("Mattermost allowlist configured")
    else:
        print_info("⚠️  No allowlist set - anyone who can message the bot can use it!")

    print()
    print_info("📬 Home Channel: where Hermes delivers cron job results and notifications.")
    print_info("   To get a channel ID: click channel name → View Info → copy the ID")
    print_info("   You can also set this later by typing /set-home in a Mattermost channel.")
    home_channel = prompt("Home channel ID (leave empty to set later with /set-home)")
    if home_channel:
        save_env_value("MATTERMOST_HOME_CHANNEL", home_channel)


def _setup_whatsapp():
    """配置 WhatsApp 桥接。"""
    print_header("WhatsApp")
    existing = get_env_value("WHATSAPP_ENABLED")
    if existing:
        print_info("WhatsApp: already enabled")
        return

    print_info("WhatsApp connects via a built-in bridge (Baileys).")
    print_info("Requires Node.js. Run 'hermes whatsapp' for guided setup.")
    print()
    if prompt_yes_no("Enable WhatsApp now?", True):
        save_env_value("WHATSAPP_ENABLED", "true")
        print_success("WhatsApp enabled")
        print_info("Run 'hermes whatsapp' to choose your mode (separate bot number")
        print_info("or personal self-chat) and pair via QR code.")


def _setup_weixin():
    """通过 iLink Bot API 二维码登录配置微信。"""
    from hermes_cli.gateway import _setup_weixin as _gateway_setup_weixin
    _gateway_setup_weixin()


def _setup_signal():
    """通过网关设置配置 Signal。"""
    from hermes_cli.gateway import _setup_signal as _gateway_setup_signal
    _gateway_setup_signal()


def _setup_email():
    """通过网关设置配置 Email。"""
    from hermes_cli.gateway import _setup_email as _gateway_setup_email
    _gateway_setup_email()


def _setup_sms():
    """通过网关设置配置 SMS（Twilio）。"""
    from hermes_cli.gateway import _setup_sms as _gateway_setup_sms
    _gateway_setup_sms()


def _setup_dingtalk():
    """通过网关设置配置钉钉。"""
    from hermes_cli.gateway import _setup_dingtalk as _gateway_setup_dingtalk
    _gateway_setup_dingtalk()


def _setup_feishu():
    """通过网关设置配置飞书 / Lark。"""
    from hermes_cli.gateway import _setup_feishu as _gateway_setup_feishu
    _gateway_setup_feishu()


def _setup_wecom():
    """通过网关设置配置企业微信。"""
    from hermes_cli.gateway import _setup_wecom as _gateway_setup_wecom
    _gateway_setup_wecom()


def _setup_wecom_callback():
    """通过网关设置配置企业微信回调（自建应用）。"""
    from hermes_cli.gateway import _setup_wecom_callback as _gw_setup
    _gw_setup()


def _setup_qqbot():
    """配置 QQ 机器人网关。"""
    print_header("QQ Bot")
    existing = get_env_value("QQ_APP_ID")
    if existing:
        print_info("QQ Bot: already configured")
        if not prompt_yes_no("Reconfigure QQ Bot?", False):
            return

    print_info("Connects Hermes to QQ via the Official QQ Bot API (v2).")
    print_info("   Requires a QQ Bot application at q.qq.com")
    print_info("   Reference: https://bot.q.qq.com/wiki/develop/api-v2/")
    print()

    app_id = prompt("QQ Bot App ID")
    if not app_id:
        print_warning("App ID is required — skipping QQ Bot setup")
        return
    save_env_value("QQ_APP_ID", app_id.strip())

    client_secret = prompt("QQ Bot App Secret", password=True)
    if not client_secret:
        print_warning("App Secret is required — skipping QQ Bot setup")
        return
    save_env_value("QQ_CLIENT_SECRET", client_secret)
    print_success("QQ Bot credentials saved")

    print()
    print_info("🔒 Security: Restrict who can DM your bot")
    print_info("   Use QQ user OpenIDs (found in event payloads)")
    print()
    allowed_users = prompt("Allowed user OpenIDs (comma-separated, leave empty for open access)")
    if allowed_users:
        save_env_value("QQ_ALLOWED_USERS", allowed_users.replace(" ", ""))
        print_success("QQ Bot allowlist configured")
    else:
        print_info("⚠️  No allowlist set — anyone can DM the bot!")

    print()
    print_info("📬 Home Channel: OpenID for cron job delivery and notifications.")
    home_channel = prompt("Home channel OpenID (leave empty to set later)")
    if home_channel:
        save_env_value("QQ_HOME_CHANNEL", home_channel)

    print()
    print_success("QQ Bot configured!")


def _setup_bluebubbles():
    """配置 BlueBubbles iMessage 网关。"""
    print_header("BlueBubbles (iMessage)")
    existing = get_env_value("BLUEBUBBLES_SERVER_URL")
    if existing:
        print_info("BlueBubbles: already configured")
        if not prompt_yes_no("Reconfigure BlueBubbles?", False):
            return

    print_info("Connects Hermes to iMessage via BlueBubbles — a free, open-source")
    print_info("macOS server that bridges iMessage to any device.")
    print_info("   Requires a Mac running BlueBubbles Server v1.0.0+")
    print_info("   Download: https://bluebubbles.app/")
    print()
    print_info("In BlueBubbles Server → Settings → API, note your Server URL and Password.")
    print()

    server_url = prompt("BlueBubbles server URL (e.g. http://192.168.1.10:1234)")
    if not server_url:
        print_warning("Server URL is required — skipping BlueBubbles setup")
        return
    save_env_value("BLUEBUBBLES_SERVER_URL", server_url.rstrip("/"))

    password = prompt("BlueBubbles server password", password=True)
    if not password:
        print_warning("Password is required — skipping BlueBubbles setup")
        return
    save_env_value("BLUEBUBBLES_PASSWORD", password)
    print_success("BlueBubbles credentials saved")

    print()
    print_info("🔒 Security: Restrict who can message your bot")
    print_info("   Use iMessage addresses: email (user@icloud.com) or phone (+15551234567)")
    print()
    allowed_users = prompt("Allowed iMessage addresses (comma-separated, leave empty for open access)")
    if allowed_users:
        save_env_value("BLUEBUBBLES_ALLOWED_USERS", allowed_users.replace(" ", ""))
        print_success("BlueBubbles allowlist configured")
    else:
        print_info("⚠️  No allowlist set — anyone who can iMessage you can use the bot!")

    print()
    print_info("📬 Home Channel: phone or email for cron job delivery and notifications.")
    print_info("   You can also set this later with /set-home in your iMessage chat.")
    home_channel = prompt("Home channel address (leave empty to set later)")
    if home_channel:
        save_env_value("BLUEBUBBLES_HOME_CHANNEL", home_channel)

    print()
    print_info("Advanced settings (defaults are fine for most setups):")
    if prompt_yes_no("Configure webhook listener settings?", False):
        webhook_port = prompt("Webhook listener port (default: 8645)")
        if webhook_port:
            try:
                save_env_value("BLUEBUBBLES_WEBHOOK_PORT", str(int(webhook_port)))
                print_success(f"Webhook port set to {webhook_port}")
            except ValueError:
                print_warning("Invalid port number, using default 8645")

    print()
    print_info("Requires the BlueBubbles Private API helper for typing indicators,")
    print_info("read receipts, and tapback reactions. Basic messaging works without it.")
    print_info("   Install: https://docs.bluebubbles.app/helper-bundle/installation")


def _setup_qqbot():
    """通过标准平台设置配置 QQ 机器人（官方 API v2）。"""
    from hermes_cli.gateway import _PLATFORMS
    qq_platform = next((p for p in _PLATFORMS if p["key"] == "qqbot"), None)
    if qq_platform:
        from hermes_cli.gateway import _setup_standard_platform
        _setup_standard_platform(qq_platform)


def _setup_webhooks():
    """配置 webhook 集成。"""
    print_header("Webhooks")
    existing = get_env_value("WEBHOOK_ENABLED")
    if existing:
        print_info("Webhooks: already configured")
        if not prompt_yes_no("Reconfigure webhooks?", False):
            return

    print()
    print_warning("⚠  Webhook and SMS platforms require exposing gateway ports to the")
    print_warning("   internet. For security, run the gateway in a sandboxed environment")
    print_warning("   (Docker, VM, etc.) to limit blast radius from prompt injection.")
    print()
    print_info("   Full guide: https://hermes-agent.nousresearch.com/docs/user-guide/messaging/webhooks/")
    print()

    port = prompt("Webhook port (default 8644)")
    if port:
        try:
            save_env_value("WEBHOOK_PORT", str(int(port)))
            print_success(f"Webhook port set to {port}")
        except ValueError:
            print_warning("Invalid port number, using default 8644")

    secret = prompt("Global HMAC secret (shared across all routes)", password=True)
    if secret:
        save_env_value("WEBHOOK_SECRET", secret)
        print_success("Webhook secret saved")
    else:
        print_warning("No secret set — you must configure per-route secrets in config.yaml")

    save_env_value("WEBHOOK_ENABLED", "true")
    print()
    print_success("Webhooks enabled! Next steps:")
    from hermes_constants import display_hermes_home as _dhh
    print_info(f"   1. Define webhook routes in {_dhh()}/config.yaml")
    print_info("   2. Point your service (GitHub, GitLab, etc.) at:")
    print_info("      http://your-server:8644/webhooks/<route-name>")
    print()
    print_info("   Route configuration guide:")
    print_info("   https://hermes-agent.nousresearch.com/docs/user-guide/messaging/webhooks/#configuring-routes")
    print()
    print_info("   Open config in your editor:  hermes config edit")


# 网关清单的平台注册表
_GATEWAY_PLATFORMS = [
    ("Telegram", "TELEGRAM_BOT_TOKEN", _setup_telegram),
    ("Discord", "DISCORD_BOT_TOKEN", _setup_discord),
    ("Slack", "SLACK_BOT_TOKEN", _setup_slack),
    ("Signal", "SIGNAL_HTTP_URL", _setup_signal),
    ("Email", "EMAIL_ADDRESS", _setup_email),
    ("SMS (Twilio)", "TWILIO_ACCOUNT_SID", _setup_sms),
    ("Matrix", "MATRIX_ACCESS_TOKEN", _setup_matrix),
    ("Mattermost", "MATTERMOST_TOKEN", _setup_mattermost),
    ("WhatsApp", "WHATSAPP_ENABLED", _setup_whatsapp),
    ("DingTalk", "DINGTALK_CLIENT_ID", _setup_dingtalk),
    ("Feishu / Lark", "FEISHU_APP_ID", _setup_feishu),
    ("WeCom (Enterprise WeChat)", "WECOM_BOT_ID", _setup_wecom),
    ("WeCom Callback (Self-Built App)", "WECOM_CALLBACK_CORP_ID", _setup_wecom_callback),
    ("Weixin (WeChat)", "WEIXIN_ACCOUNT_ID", _setup_weixin),
    ("BlueBubbles (iMessage)", "BLUEBUBBLES_SERVER_URL", _setup_bluebubbles),
    ("QQ Bot", "QQ_APP_ID", _setup_qqbot),
    ("Webhooks (GitHub, GitLab, etc.)", "WEBHOOK_ENABLED", _setup_webhooks),
]


def setup_gateway(config: dict):
    """配置消息平台集成。"""
    print_header("Messaging Platforms")
    print_info("Connect to messaging platforms to chat with Hermes from anywhere.")
    print_info("Toggle with Space, confirm with Enter.")
    print()

    # 构建清单项目，预选已配置的平台
    items = []
    pre_selected = []
    for i, (name, env_var, _func) in enumerate(_GATEWAY_PLATFORMS):
        # Matrix 有两个可能的环境变量
        is_configured = bool(get_env_value(env_var))
        if name == "Matrix" and not is_configured:
            is_configured = bool(get_env_value("MATRIX_PASSWORD"))
        label = f"{name}  (configured)" if is_configured else name
        items.append(label)
        if is_configured:
            pre_selected.append(i)

    selected = prompt_checklist("Select platforms to configure:", items, pre_selected)

    if not selected:
        print_info("No platforms selected. Run 'hermes setup gateway' later to configure.")
        return

    for idx in selected:
        name, _env_var, setup_func = _GATEWAY_PLATFORMS[idx]
        setup_func()

    # ── 网关服务设置 ──
    any_messaging = (
        get_env_value("TELEGRAM_BOT_TOKEN")
        or get_env_value("DISCORD_BOT_TOKEN")
        or get_env_value("SLACK_BOT_TOKEN")
        or get_env_value("SIGNAL_HTTP_URL")
        or get_env_value("EMAIL_ADDRESS")
        or get_env_value("TWILIO_ACCOUNT_SID")
        or get_env_value("MATTERMOST_TOKEN")
        or get_env_value("MATRIX_ACCESS_TOKEN")
        or get_env_value("MATRIX_PASSWORD")
        or get_env_value("WHATSAPP_ENABLED")
        or get_env_value("DINGTALK_CLIENT_ID")
        or get_env_value("FEISHU_APP_ID")
        or get_env_value("WECOM_BOT_ID")
        or get_env_value("WEIXIN_ACCOUNT_ID")
        or get_env_value("BLUEBUBBLES_SERVER_URL")
        or get_env_value("QQ_APP_ID")
        or get_env_value("WEBHOOK_ENABLED")
    )
    if any_messaging:
        print()
        print_info("━" * 50)
        print_success("Messaging platforms configured!")

        # 检查是否有缺失的 home 频道
        missing_home = []
        if get_env_value("TELEGRAM_BOT_TOKEN") and not get_env_value(
            "TELEGRAM_HOME_CHANNEL"
        ):
            missing_home.append("Telegram")
        if get_env_value("DISCORD_BOT_TOKEN") and not get_env_value(
            "DISCORD_HOME_CHANNEL"
        ):
            missing_home.append("Discord")
        if get_env_value("SLACK_BOT_TOKEN") and not get_env_value("SLACK_HOME_CHANNEL"):
            missing_home.append("Slack")
        if get_env_value("BLUEBUBBLES_SERVER_URL") and not get_env_value("BLUEBUBBLES_HOME_CHANNEL"):
            missing_home.append("BlueBubbles")
        if get_env_value("QQ_APP_ID") and not get_env_value("QQ_HOME_CHANNEL"):
            missing_home.append("QQBot")

        if missing_home:
            print()
            print_warning(f"No home channel set for: {', '.join(missing_home)}")
            print_info("   Without a home channel, cron jobs and cross-platform")
            print_info("   messages can't be delivered to those platforms.")
            print_info("   Set one later with /set-home in your chat, or:")
            for plat in missing_home:
                print_info(
                    f"     hermes config set {plat.upper()}_HOME_CHANNEL <channel_id>"
                )

        # 提供将网关安装为系统服务的选项
        import platform as _platform

        _is_linux = _platform.system() == "Linux"
        _is_macos = _platform.system() == "Darwin"

        from hermes_cli.gateway import (
            _is_service_installed,
            _is_service_running,
            supports_systemd_services,
            has_conflicting_systemd_units,
            install_linux_gateway_from_setup,
            print_systemd_scope_conflict_warning,
            systemd_start,
            systemd_restart,
            launchd_install,
            launchd_start,
            launchd_restart,
        )

        service_installed = _is_service_installed()
        service_running = _is_service_running()
        supports_systemd = supports_systemd_services()
        supports_service_manager = supports_systemd or _is_macos

        print()
        if supports_systemd and has_conflicting_systemd_units():
            print_systemd_scope_conflict_warning()
            print()

        if service_running:
            if prompt_yes_no("  Restart the gateway to pick up changes?", True):
                try:
                    if supports_systemd:
                        systemd_restart()
                    elif _is_macos:
                        launchd_restart()
                except Exception as e:
                    print_error(f"  Restart failed: {e}")
        elif service_installed:
            if prompt_yes_no("  Start the gateway service?", True):
                try:
                    if supports_systemd:
                        systemd_start()
                    elif _is_macos:
                        launchd_start()
                except Exception as e:
                    print_error(f"  Start failed: {e}")
        elif supports_service_manager:
            svc_name = "systemd" if supports_systemd else "launchd"
            if prompt_yes_no(
                f"  Install the gateway as a {svc_name} service? (runs in background, starts on boot)",
                True,
            ):
                try:
                    installed_scope = None
                    did_install = False
                    if supports_systemd:
                        installed_scope, did_install = install_linux_gateway_from_setup(force=False)
                    else:
                        launchd_install(force=False)
                        did_install = True
                    print()
                    if did_install and prompt_yes_no("  Start the service now?", True):
                        try:
                            if supports_systemd:
                                systemd_start(system=installed_scope == "system")
                            elif _is_macos:
                                launchd_start()
                        except Exception as e:
                            print_error(f"  Start failed: {e}")
                except Exception as e:
                    print_error(f"  Install failed: {e}")
                    print_info("  You can try manually: hermes gateway install")
            else:
                print_info("  You can install later: hermes gateway install")
                if supports_systemd:
                    print_info("  Or as a boot-time service: sudo hermes gateway install --system")
                print_info("  Or run in foreground:  hermes gateway")
        else:
            from hermes_constants import is_container
            if is_container():
                print_info("Start the gateway to bring your bots online:")
                print_info("   hermes gateway run          # Run as container main process")
                print_info("")
                print_info("For automatic restarts, use a Docker restart policy:")
                print_info("   docker run --restart unless-stopped ...")
                print_info("   docker restart <container>  # Manual restart")
            else:
                print_info("Start the gateway to bring your bots online:")
                print_info("   hermes gateway              # Run in foreground")

        print_info("━" * 50)


# =============================================================================
# 第 5 节：工具配置（委托给统一的 tools_config.py）
# =============================================================================


def setup_tools(config: dict, first_install: bool = False):
    """配置工具 — 委托给 tools_config.py 中统一的 tools_command()。

    `hermes setup tools` 和 `hermes tools` 使用相同的流程：
    平台选择 → 工具集切换 → 提供商/API 密钥配置。

    参数:
        first_install: 为 True 时，使用简化的首次安装流程
            （无平台菜单，提示所有未配置的 API 密钥）。
    """
    from hermes_cli.tools_config import tools_command

    tools_command(first_install=first_install, config=config)


# =============================================================================
# 迁移后的章节跳过逻辑
# =============================================================================


def _get_section_config_summary(config: dict, section_key: str) -> Optional[str]:
    """如果设置章节已配置则返回简短摘要，否则返回 None。

    用于 OpenClaw 迁移后检测哪些章节可以跳过。
    ``get_env_value`` 是从 hermes_cli.config 模块级导入的，
    以便对 ``setup_mod.get_env_value`` 的测试补丁生效。
    """
    if section_key == "model":
        has_key = bool(
            get_env_value("OPENROUTER_API_KEY")
            or get_env_value("OPENAI_API_KEY")
            or get_env_value("ANTHROPIC_API_KEY")
        )
        if not has_key:
            # Check for OAuth providers
            try:
                from hermes_cli.auth import get_active_provider
                if get_active_provider():
                    has_key = True
            except Exception:
                pass
        if not has_key:
            return None
        model = config.get("model")
        if isinstance(model, str) and model.strip():
            return model.strip()
        if isinstance(model, dict):
            return str(model.get("default") or model.get("model") or "configured")
        return "configured"

    elif section_key == "terminal":
        backend = config.get("terminal", {}).get("backend", "local")
        return f"backend: {backend}"

    elif section_key == "agent":
        max_turns = config.get("agent", {}).get("max_turns", 90)
        return f"max turns: {max_turns}"

    elif section_key == "gateway":
        platforms = []
        if get_env_value("TELEGRAM_BOT_TOKEN"):
            platforms.append("Telegram")
        if get_env_value("DISCORD_BOT_TOKEN"):
            platforms.append("Discord")
        if get_env_value("SLACK_BOT_TOKEN"):
            platforms.append("Slack")
        if get_env_value("SIGNAL_ACCOUNT"):
            platforms.append("Signal")
        if get_env_value("EMAIL_ADDRESS"):
            platforms.append("Email")
        if get_env_value("TWILIO_ACCOUNT_SID"):
            platforms.append("SMS")
        if get_env_value("MATRIX_ACCESS_TOKEN") or get_env_value("MATRIX_PASSWORD"):
            platforms.append("Matrix")
        if get_env_value("MATTERMOST_TOKEN"):
            platforms.append("Mattermost")
        if get_env_value("WHATSAPP_PHONE_NUMBER_ID"):
            platforms.append("WhatsApp")
        if get_env_value("DINGTALK_CLIENT_ID"):
            platforms.append("DingTalk")
        if get_env_value("FEISHU_APP_ID"):
            platforms.append("Feishu")
        if get_env_value("WECOM_BOT_ID"):
            platforms.append("WeCom")
        if get_env_value("WEIXIN_ACCOUNT_ID"):
            platforms.append("Weixin")
        if get_env_value("BLUEBUBBLES_SERVER_URL"):
            platforms.append("BlueBubbles")
        if get_env_value("WEBHOOK_ENABLED"):
            platforms.append("Webhooks")
        if platforms:
            return ", ".join(platforms)
            return None  # 未配置平台 — 章节必须运行

    elif section_key == "tools":
        tools = []
        if get_env_value("ELEVENLABS_API_KEY"):
            tools.append("TTS/ElevenLabs")
        if get_env_value("BROWSERBASE_API_KEY"):
            tools.append("Browser")
        if get_env_value("FIRECRAWL_API_KEY"):
            tools.append("Firecrawl")
        if tools:
            return ", ".join(tools)
        return None

    return None


def _skip_configured_section(
    config: dict, section_key: str, label: str
) -> bool:
    """显示已配置章节的摘要并提供跳过选项。

    如果用户选择跳过返回 True，如果章节应运行返回 False。
    """
    summary = _get_section_config_summary(config, section_key)
    if not summary:
        return False
    print()
    print_success(f"  {label}: {summary}")
    return not prompt_yes_no(f"  Reconfigure {label.lower()}?", default=False)


# =============================================================================
# OpenClaw 迁移
# =============================================================================


_OPENCLAW_SCRIPT = (
    get_optional_skills_dir(PROJECT_ROOT / "optional-skills")
    / "migration"
    / "openclaw-migration"
    / "scripts"
    / "openclaw_to_hermes.py"
)


def _load_openclaw_migration_module():
    """将 openclaw_to_hermes 迁移脚本作为模块加载。

    返回已加载的模块，如果脚本无法加载则返回 None。
    """
    if not _OPENCLAW_SCRIPT.exists():
        return None

    spec = importlib.util.spec_from_file_location(
        "openclaw_to_hermes", _OPENCLAW_SCRIPT
    )
    if spec is None or spec.loader is None:
        return None

    mod = importlib.util.module_from_spec(spec)
    # 在 sys.modules 中注册，以便 @dataclass 可以解析模块
    # （Python 3.11+ 要求动态加载的模块如此）
    import sys as _sys
    _sys.modules[spec.name] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception:
        _sys.modules.pop(spec.name, None)
        raise
    return mod


# 代表高影响变更需要显式警告的条目类型。
# 网关令牌/频道可能从旧智能体劫持消息平台。
# 配置值在 OpenClaw 和 Hermes 之间可能有不同语义。
# 指令/上下文文件（.md）可能包含不兼容的设置流程。
_HIGH_IMPACT_KIND_KEYWORDS = {
    "gateway": "⚠ Gateway/messaging — this will configure Hermes to use your OpenClaw messaging channels",
    "telegram": "⚠ Telegram — this will point Hermes at your OpenClaw Telegram bot",
    "slack": "⚠ Slack — this will point Hermes at your OpenClaw Slack workspace",
    "discord": "⚠ Discord — this will point Hermes at your OpenClaw Discord bot",
    "whatsapp": "⚠ WhatsApp — this will point Hermes at your OpenClaw WhatsApp connection",
    "config": "⚠ Config values — OpenClaw settings may not map 1:1 to Hermes equivalents",
    "soul": "⚠ Instruction file — may contain OpenClaw-specific setup/restart procedures",
    "memory": "⚠ Memory/context file — may reference OpenClaw-specific infrastructure",
    "context": "⚠ Context file — may contain OpenClaw-specific instructions",
}


def _print_migration_preview(report: dict):
    """打印迁移操作的详细预演预览。

    按类别分组条目，并为高影响变更（如网关令牌接管
    和配置值差异）添加显式警告。
    """
    items = report.get("items", [])
    if not items:
        print_info("Nothing to migrate.")
        return

    migrated_items = [i for i in items if i.get("status") == "migrated"]
    conflict_items = [i for i in items if i.get("status") == "conflict"]
    skipped_items = [i for i in items if i.get("status") == "skipped"]

    warnings_shown = set()

    if migrated_items:
        print(color("  Would import:", Colors.GREEN))
        for item in migrated_items:
            kind = item.get("kind", "unknown")
            dest = item.get("destination", "")
            if dest:
                dest_short = str(dest).replace(str(Path.home()), "~")
                print(f"      {kind:<22s} → {dest_short}")
            else:
                print(f"      {kind}")

            # Check for high-impact items and collect warnings
            kind_lower = kind.lower()
            dest_lower = str(dest).lower()
            for keyword, warning in _HIGH_IMPACT_KIND_KEYWORDS.items():
                if keyword in kind_lower or keyword in dest_lower:
                    warnings_shown.add(warning)
        print()

    if conflict_items:
        print(color("  Would overwrite (conflicts with existing Hermes config):", Colors.YELLOW))
        for item in conflict_items:
            kind = item.get("kind", "unknown")
            reason = item.get("reason", "already exists")
            print(f"      {kind:<22s}  {reason}")
        print()

    if skipped_items:
        print(color("  Would skip:", Colors.DIM))
        for item in skipped_items:
            kind = item.get("kind", "unknown")
            reason = item.get("reason", "")
            print(f"      {kind:<22s}  {reason}")
        print()

    # 打印收集的警告
    if warnings_shown:
        print(color("  ── Warnings ──", Colors.YELLOW))
        for warning in sorted(warnings_shown):
            print(color(f"    {warning}", Colors.YELLOW))
        print()
        print(color("  Note: OpenClaw config values may have different semantics in Hermes.", Colors.YELLOW))
        print(color("  For example, OpenClaw's tool_call_execution: \"auto\" ≠ Hermes's yolo mode.", Colors.YELLOW))
        print(color("  Instruction files (.md) from OpenClaw may contain incompatible procedures.", Colors.YELLOW))
        print()


def _offer_openclaw_migration(hermes_home: Path) -> bool:
    """检测 ~/.openclaw 并在首次设置时提供迁移选项。

    先运行预演以向用户展示将要导入、覆盖或接管的内容。
    仅在明确确认后才执行。

    如果迁移成功运行返回 True，否则返回 False。
    """
    openclaw_dir = Path.home() / ".openclaw"
    if not openclaw_dir.is_dir():
        return False

    if not _OPENCLAW_SCRIPT.exists():
        return False

    print()
    print_header("OpenClaw Installation Detected")
    print_info(f"Found OpenClaw data at {openclaw_dir}")
    print_info("Hermes can preview what would be imported before making any changes.")
    print()

    if not prompt_yes_no("Would you like to see what can be imported?", default=True):
        print_info(
            "Skipping migration. You can run it later with: hermes claw migrate --dry-run"
        )
        return False

    # 确保 config.yaml 在迁移尝试读取之前存在
    config_path = get_config_path()
    if not config_path.exists():
        save_config(load_config())

    # 加载迁移模块
    try:
        mod = _load_openclaw_migration_module()
        if mod is None:
            print_warning("Could not load migration script.")
            return False
    except Exception as e:
        print_warning(f"Could not load migration script: {e}")
        logger.debug("OpenClaw migration module load error", exc_info=True)
        return False

    # ── 阶段 1：预演预览 ──
    try:
        selected = mod.resolve_selected_options(None, None, preset="full")
        dry_migrator = mod.Migrator(
            source_root=openclaw_dir.resolve(),
            target_root=hermes_home.resolve(),
            execute=False,  # dry-run — no files modified
            workspace_target=None,
            overwrite=True,  # show everything including conflicts
            migrate_secrets=True,
            output_dir=None,
            selected_options=selected,
            preset_name="full",
        )
        preview_report = dry_migrator.migrate()
    except Exception as e:
        print_warning(f"Migration preview failed: {e}")
        logger.debug("OpenClaw migration preview error", exc_info=True)
        return False

    # 显示完整预览
    preview_summary = preview_report.get("summary", {})
    preview_count = preview_summary.get("migrated", 0)

    if preview_count == 0:
        print()
        print_info("Nothing to import from OpenClaw.")
        return False

    print()
    print_header(f"Migration Preview — {preview_count} item(s) would be imported")
    print_info("No changes have been made yet. Review the list below:")
    print()
    _print_migration_preview(preview_report)

    # ── 阶段 2：确认并执行 ──
    if not prompt_yes_no("Proceed with migration?", default=False):
        print_info(
            "Migration cancelled. You can run it later with: hermes claw migrate"
        )
        print_info(
            "Use --dry-run to preview again, or --preset minimal for a lighter import."
        )
        return False

    # 执行迁移 — overwrite=False 保留现有 Hermes 配置。
    # 用户已看过预览；冲突默认跳过。
    try:
        migrator = mod.Migrator(
            source_root=openclaw_dir.resolve(),
            target_root=hermes_home.resolve(),
            execute=True,
            workspace_target=None,
            overwrite=False,  # preserve existing Hermes config
            migrate_secrets=True,
            output_dir=None,
            selected_options=selected,
            preset_name="full",
        )
        report = migrator.migrate()
    except Exception as e:
        print_warning(f"Migration failed: {e}")
        logger.debug("OpenClaw migration error", exc_info=True)
        return False

    # 打印最终摘要
    summary = report.get("summary", {})
    migrated = summary.get("migrated", 0)
    skipped = summary.get("skipped", 0)
    conflicts = summary.get("conflict", 0)
    errors = summary.get("error", 0)

    print()
    if migrated:
        print_success(f"Imported {migrated} item(s) from OpenClaw.")
    if conflicts:
        print_info(f"Skipped {conflicts} item(s) that already exist in Hermes (use hermes claw migrate --overwrite to force).")
    if skipped:
        print_info(f"Skipped {skipped} item(s) (not found or unchanged).")
    if errors:
        print_warning(f"{errors} item(s) had errors — check the migration report.")

    output_dir = report.get("output_dir")
    if output_dir:
        print_info(f"Full report saved to: {output_dir}")

    print_success("Migration complete! Continuing with setup...")
    return True


# =============================================================================
# 主向导协调器
# =============================================================================

SETUP_SECTIONS = [
    ("model", "Model & Provider", setup_model_provider),
    ("tts", "Text-to-Speech", setup_tts),
    ("terminal", "Terminal Backend", setup_terminal_backend),
    ("gateway", "Messaging Platforms (Gateway)", setup_gateway),
    ("tools", "Tools", setup_tools),
    ("agent", "Agent Settings", setup_agent_settings),
]

# 回访用户菜单故意省略独立 TTS，因为模型设置已包含 TTS 选择，
# 工具设置涵盖了其余的提供商配置。保持此列表与可见菜单项相同的顺序。
RETURNING_USER_MENU_SECTION_KEYS = [
    "model",
    "terminal",
    "gateway",
    "tools",
    "agent",
]


def run_setup_wizard(args):
    """运行交互式设置向导。

    支持完整、快速和特定章节设置：
      hermes setup           — 完整或快速（自动检测）
      hermes setup model     — 仅模型/提供商
      hermes setup tts       — 仅文字转语音
      hermes setup terminal  — 仅终端后端
      hermes setup gateway   — 仅消息平台
      hermes setup tools     — 仅工具配置
      hermes setup agent     — 仅智能体设置
    """
    from hermes_cli.config import is_managed, managed_error
    if is_managed():
        managed_error("run setup wizard")
        return
    ensure_hermes_home()

    reset_requested = bool(getattr(args, "reset", False))
    if reset_requested:
        save_config(copy.deepcopy(DEFAULT_CONFIG))
        print_success("Configuration reset to defaults.")

    config = load_config()
    hermes_home = get_hermes_home()

    # 检测非交互式环境（无头 SSH、Docker、CI/CD）
    non_interactive = getattr(args, 'non_interactive', False)
    if not non_interactive and not is_interactive_stdin():
        non_interactive = True

    if non_interactive:
        print_noninteractive_setup_guidance(
            "Running in a non-interactive environment (no TTY detected)."
        )
        return

    # 检查是否请求了特定章节
    section = getattr(args, "section", None)
    if section:
        for key, label, func in SETUP_SECTIONS:
            if key == section:
                print()
                print(
                    color(
                        "┌─────────────────────────────────────────────────────────┐",
                        Colors.MAGENTA,
                    )
                )
                print(color(f"│     ⚕ Hermes Setup — {label:<34s} │", Colors.MAGENTA))
                print(
                    color(
                        "└─────────────────────────────────────────────────────────┘",
                        Colors.MAGENTA,
                    )
                )
                func(config)
                save_config(config)
                print()
                print_success(f"{label} configuration complete!")
                return

        print_error(f"Unknown setup section: {section}")
        print_info(f"Available sections: {', '.join(k for k, _, _ in SETUP_SECTIONS)}")
        return

    # 检查是否是已有安装且已配置提供商
    from hermes_cli.auth import get_active_provider

    active_provider = get_active_provider()
    is_existing = (
        bool(get_env_value("OPENROUTER_API_KEY"))
        or bool(get_env_value("OPENAI_BASE_URL"))
        or active_provider is not None
    )

    print()
    print(
        color(
            "┌─────────────────────────────────────────────────────────┐",
            Colors.MAGENTA,
        )
    )
    print(
        color(
            "│             ⚕ Hermes Agent Setup Wizard                │", Colors.MAGENTA
        )
    )
    print(
        color(
            "├─────────────────────────────────────────────────────────┤",
            Colors.MAGENTA,
        )
    )
    print(
        color(
            "│  Let's configure your Hermes Agent installation.       │", Colors.MAGENTA
        )
    )
    print(
        color(
            "│  Press Ctrl+C at any time to exit.                     │", Colors.MAGENTA
        )
    )
    print(
        color(
            "└─────────────────────────────────────────────────────────┘",
            Colors.MAGENTA,
        )
    )

    migration_ran = False

    if is_existing:
        # ── 回访用户菜单 ──
        print()
        print_header("Welcome Back!")
        print_success("You already have Hermes configured.")
        print()

        menu_choices = [
            "Quick Setup - configure missing items only",
            "Full Setup - reconfigure everything",
            "Model & Provider",
            "Terminal Backend",
            "Messaging Platforms (Gateway)",
            "Tools",
            "Agent Settings",
            "Exit",
        ]
        choice = prompt_choice("What would you like to do?", menu_choices, 0)

        if choice == 0:
            # 快速设置
            _run_quick_setup(config, hermes_home)
            return
        elif choice == 1:
            # 完整设置 — 继续执行所有章节
            pass
        elif choice == 7:
            print_info("Exiting. Run 'hermes setup' again when ready.")
            return
        elif 2 <= choice <= 6:
            # 单独章节 — 按键映射，不按位置。
            # SETUP_SECTIONS 包含 TTS 但回访用户菜单跳过它，
            # 所以位置索引（choice - 2）会调度到错误的章节。
            section_key = RETURNING_USER_MENU_SECTION_KEYS[choice - 2]
            section = next((s for s in SETUP_SECTIONS if s[0] == section_key), None)
            if section:
                _, label, func = section
                func(config)
                save_config(config)
                _print_setup_summary(config, hermes_home)
            return
    else:
        # ── 首次设置 ──
        print()

        # 在配置开始前提供 OpenClaw 迁移
        migration_ran = _offer_openclaw_migration(hermes_home)
        if migration_ran:
            config = load_config()

        setup_mode = prompt_choice("How would you like to set up Hermes?", [
            "Quick setup — provider, model & messaging (recommended)",
            "Full setup — configure everything",
        ], 0)

        if setup_mode == 0:
            _run_first_time_quick_setup(config, hermes_home, is_existing)
            return

    # ── 完整设置 — 运行所有章节 ──
    print_header("Configuration Location")
    print_info(f"Config file:  {get_config_path()}")
    print_info(f"Secrets file: {get_env_path()}")
    print_info(f"Data folder:  {hermes_home}")
    print_info(f"Install dir:  {PROJECT_ROOT}")
    print()
    print_info("You can edit these files directly or use 'hermes config edit'")

    if migration_ran:
        print()
        print_info("Settings were imported from OpenClaw.")
        print_info("Each section below will show what was imported — press Enter to keep,")
        print_info("or choose to reconfigure if needed.")

    # 第 1 节：模型与提供商
    if not (migration_ran and _skip_configured_section(config, "model", "Model & Provider")):
        setup_model_provider(config)

    # 第 2 节：终端后端
    if not (migration_ran and _skip_configured_section(config, "terminal", "Terminal Backend")):
        setup_terminal_backend(config)

    # 第 3 节：智能体设置
    if not (migration_ran and _skip_configured_section(config, "agent", "Agent Settings")):
        setup_agent_settings(config)

    # 第 4 节：消息平台
    if not (migration_ran and _skip_configured_section(config, "gateway", "Messaging Platforms")):
        setup_gateway(config)

    # 第 5 节：工具
    if not (migration_ran and _skip_configured_section(config, "tools", "Tools")):
        setup_tools(config, first_install=not is_existing)

    # 保存并显示摘要
    save_config(config)
    _print_setup_summary(config, hermes_home)

    _offer_launch_chat()


def _resolve_hermes_chat_argv() -> Optional[list[str]]:
    """解析用于在新进程中启动 ``hermes chat`` 的 argv。"""
    hermes_bin = shutil.which("hermes")
    if hermes_bin:
        return [hermes_bin, "chat"]

    try:
        if importlib.util.find_spec("hermes_cli") is not None:
            return [sys.executable, "-m", "hermes_cli.main", "chat"]
    except Exception:
        pass

    return None


def _offer_launch_chat():
    """设置完成后提示用户直接跳转到聊天。"""
    print()
    if not prompt_yes_no("Launch hermes chat now?", True):
        return

    chat_argv = _resolve_hermes_chat_argv()
    if not chat_argv:
        print_info("Could not relaunch Hermes automatically. Run 'hermes chat' manually.")
        return

    os.execvp(chat_argv[0], chat_argv)


def _run_first_time_quick_setup(config: dict, hermes_home, is_existing: bool):
    """精简的首次设置：仅提供商 + 模型。

    为 TTS（Edge）、终端（本地）、智能体设置和工具应用合理的默认值 —
    用户可以稍后通过 ``hermes setup <section>`` 自定义。
    """
    # 步骤 1：模型与提供商（必需 — 跳过轮换/视觉/TTS）
    setup_model_provider(config, quick=True)

    # 步骤 2：为其他所有内容应用默认值
    _apply_default_agent_settings(config)
    config.setdefault("terminal", {}).setdefault("backend", "local")

    save_config(config)

    # 步骤 3：提供消息网关设置
    print()
    gateway_choice = prompt_choice(
        "Connect a messaging platform? (Telegram, Discord, etc.)",
        [
            "Set up messaging now (recommended)",
            "Skip — set up later with 'hermes setup gateway'",
        ],
        0,
    )

    if gateway_choice == 0:
        setup_gateway(config)
        save_config(config)

    print()
    print_success("Setup complete! You're ready to go.")
    print()
    print_info("  Configure all settings:    hermes setup")
    if gateway_choice != 0:
        print_info("  Connect Telegram/Discord:  hermes setup gateway")
    print()

    _print_setup_summary(config, hermes_home)

    _offer_launch_chat()


def _run_quick_setup(config: dict, hermes_home):
    """快速设置 — 仅配置缺失的项目。"""
    from hermes_cli.config import (
        get_missing_env_vars,
        get_missing_config_fields,
        check_config_version,
    )

    print()
    print_header("Quick Setup — Missing Items Only")

    # 检查缺失内容
    missing_required = [
        v for v in get_missing_env_vars(required_only=False) if v.get("is_required")
    ]
    missing_optional = [
        v for v in get_missing_env_vars(required_only=False) if not v.get("is_required")
    ]
    missing_config = get_missing_config_fields()
    current_ver, latest_ver = check_config_version()

    has_anything_missing = (
        missing_required
        or missing_optional
        or missing_config
        or current_ver < latest_ver
    )

    if not has_anything_missing:
        print_success("Everything is configured! Nothing to do.")
        print()
        print_info("Run 'hermes setup' and choose 'Full Setup' to reconfigure,")
        print_info("or pick a specific section from the menu.")
        return

    # 处理缺失的必需环境变量
    if missing_required:
        print()
        print_info(f"{len(missing_required)} required setting(s) missing:")
        for var in missing_required:
            print(f"     • {var['name']}")
        print()

        for var in missing_required:
            print()
            print(color(f"  {var['name']}", Colors.CYAN))
            print_info(f"  {var.get('description', '')}")
            if var.get("url"):
                print_info(f"  Get key at: {var['url']}")

            if var.get("password"):
                value = prompt(f"  {var.get('prompt', var['name'])}", password=True)
            else:
                value = prompt(f"  {var.get('prompt', var['name'])}")

            if value:
                save_env_value(var["name"], value)
                print_success(f"  Saved {var['name']}")
            else:
                print_warning(f"  Skipped {var['name']}")

    # 按类别分割缺失的可选变量
    missing_tools = [v for v in missing_optional if v.get("category") == "tool"]
    missing_messaging = [
        v
        for v in missing_optional
        if v.get("category") == "messaging" and not v.get("advanced")
    ]

    # ── 工具 API 密钥（清单）──
    if missing_tools:
        print()
        print_header("Tool API Keys")

        checklist_labels = []
        for var in missing_tools:
            tools = var.get("tools", [])
            tools_str = f" → {', '.join(tools[:2])}" if tools else ""
            checklist_labels.append(f"{var.get('description', var['name'])}{tools_str}")

        selected_indices = prompt_checklist(
            "Which tools would you like to configure?",
            checklist_labels,
        )

        for idx in selected_indices:
            var = missing_tools[idx]
            _prompt_api_key(var)

    # ── 消息平台（清单然后提示选中的）──
    if missing_messaging:
        print()
        print_header("Messaging Platforms")
        print_info("Connect Hermes to messaging apps to chat from anywhere.")
        print_info("You can configure these later with 'hermes setup gateway'.")

        # 按平台分组（保持顺序）
        platform_order = []
        platforms = {}
        for var in missing_messaging:
            name = var["name"]
            if "TELEGRAM" in name:
                plat = "Telegram"
            elif "DISCORD" in name:
                plat = "Discord"
            elif "SLACK" in name:
                plat = "Slack"
            else:
                continue
            if plat not in platforms:
                platform_order.append(plat)
            platforms.setdefault(plat, []).append(var)

        platform_labels = [
            {
                "Telegram": "📱 Telegram",
                "Discord": "💬 Discord",
                "Slack": "💼 Slack",
            }.get(p, p)
            for p in platform_order
        ]

        selected_indices = prompt_checklist(
            "Which platforms would you like to set up?",
            platform_labels,
        )

        for idx in selected_indices:
            plat = platform_order[idx]
            vars_list = platforms[plat]
            emoji = {"Telegram": "📱", "Discord": "💬", "Slack": "💼"}.get(plat, "")
            print()
            print(color(f"  ─── {emoji} {plat} ───", Colors.CYAN))
            print()
            for var in vars_list:
                print_info(f"  {var.get('description', '')}")
                if var.get("url"):
                    print_info(f"  {var['url']}")
                if var.get("password"):
                    value = prompt(f"  {var.get('prompt', var['name'])}", password=True)
                else:
                    value = prompt(f"  {var.get('prompt', var['name'])}")
                if value:
                    save_env_value(var["name"], value)
                    print_success("  ✓ Saved")
                else:
                    print_warning("  Skipped")
                print()

    # Handle missing config fields
    if missing_config:
        print()
        print_info(
            f"Adding {len(missing_config)} new config option(s) with defaults..."
        )
        for field in missing_config:
            print_success(f"  Added {field['key']} = {field['default']}")

        # Update config version
        config["_config_version"] = latest_ver
        save_config(config)

    # Jump to summary
    _print_setup_summary(config, hermes_home)
