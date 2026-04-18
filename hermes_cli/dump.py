"""
hermes CLI 的 dump 命令。

输出用户 Hermes 配置的紧凑纯文本摘要，
可直接复制粘贴到 Discord/GitHub/Telegram 用于技术支持。
不含 ANSI 颜色和特殊符号 -- 只有数据。
"""

import json
import os
import platform
import subprocess
import sys
from pathlib import Path

from hermes_cli.config import get_hermes_home, get_env_path, get_project_root, load_config
from hermes_constants import display_hermes_home


def _get_git_commit(project_root: Path) -> str:
    """返回简短的 git 提交哈希值，失败则返回 '(unknown)'。"""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short=8", "HEAD"],
            capture_output=True, text=True, timeout=5,
            cwd=str(project_root),
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return "(unknown)"


def _redact(value: str) -> str:
    """对敏感值脱敏处理，只保留前 4 位和后 4 位字符。"""
    if not value:
        return ""
    if len(value) < 12:
        return "***"
    return value[:4] + "..." + value[-4:]


def _gateway_status() -> str:
    """返回网关状态的简短描述字符串。"""
    if sys.platform.startswith("linux"):
        from hermes_constants import is_container
        if is_container():
            try:
                from hermes_cli.gateway import find_gateway_pids
                pids = find_gateway_pids()
                if pids:
                    return f"running (docker, pid {pids[0]})"
                return "stopped (docker)"
            except Exception:
                return "stopped (docker)"
        try:
            from hermes_cli.gateway import get_service_name
            svc = get_service_name()
        except Exception:
            svc = "hermes-gateway"
        try:
            r = subprocess.run(
                ["systemctl", "--user", "is-active", svc],
                capture_output=True, text=True, timeout=5,
            )
            return "running (systemd)" if r.stdout.strip() == "active" else "stopped"
        except Exception:
            return "unknown"
    elif sys.platform == "darwin":
        try:
            from hermes_cli.gateway import get_launchd_label
            r = subprocess.run(
                ["launchctl", "list", get_launchd_label()],
                capture_output=True, text=True, timeout=5,
            )
            return "loaded (launchd)" if r.returncode == 0 else "not loaded"
        except Exception:
            return "unknown"
    return "N/A"


def _count_skills(hermes_home: Path) -> int:
    """统计已安装的技能数量。"""
    skills_dir = hermes_home / "skills"
    if not skills_dir.is_dir():
        return 0
    count = 0
    for item in skills_dir.rglob("SKILL.md"):
        count += 1
    return count


def _count_mcp_servers(config: dict) -> int:
    """统计已配置的 MCP 服务器数量。"""
    mcp = config.get("mcp", {})
    servers = mcp.get("servers", {})
    return len(servers)


def _cron_summary(hermes_home: Path) -> str:
    """返回定时任务汇总信息。"""
    jobs_file = hermes_home / "cron" / "jobs.json"
    if not jobs_file.exists():
        return "0"
    try:
        with open(jobs_file, encoding="utf-8") as f:
            data = json.load(f)
        jobs = data.get("jobs", [])
        active = sum(1 for j in jobs if j.get("enabled", True))
        return f"{active} active / {len(jobs)} total"
    except Exception:
        return "(error reading)"


def _configured_platforms() -> list[str]:
    """返回已配置的消息平台名称列表。"""
    checks = {
        "telegram": "TELEGRAM_BOT_TOKEN",
        "discord": "DISCORD_BOT_TOKEN",
        "slack": "SLACK_BOT_TOKEN",
        "whatsapp": "WHATSAPP_ENABLED",
        "signal": "SIGNAL_HTTP_URL",
        "email": "EMAIL_ADDRESS",
        "sms": "TWILIO_ACCOUNT_SID",
        "matrix": "MATRIX_HOMESERVER_URL",
        "mattermost": "MATTERMOST_URL",
        "homeassistant": "HASS_TOKEN",
        "dingtalk": "DINGTALK_CLIENT_ID",
        "feishu": "FEISHU_APP_ID",
        "wecom": "WECOM_BOT_ID",
        "wecom_callback": "WECOM_CALLBACK_CORP_ID",
        "weixin": "WEIXIN_ACCOUNT_ID",
        "qqbot": "QQ_APP_ID",
    }
    return [name for name, env in checks.items() if os.getenv(env)]


def _memory_provider(config: dict) -> str:
    """返回当前激活的记忆提供者名称。"""
    mem = config.get("memory", {})
    provider = mem.get("provider", "")
    return provider if provider else "built-in"


def _get_model_and_provider(config: dict) -> tuple[str, str]:
    """从配置中提取模型名称和提供者信息。"""
    model_cfg = config.get("model", "")
    if isinstance(model_cfg, dict):
        model = model_cfg.get("default") or model_cfg.get("model") or model_cfg.get("name") or "(not set)"
        provider = model_cfg.get("provider") or "(auto)"
    elif isinstance(model_cfg, str):
        model = model_cfg or "(not set)"
        provider = "(auto)"
    else:
        model = "(not set)"
        provider = "(auto)"
    return model, provider


def _config_overrides(config: dict) -> dict[str, str]:
    """查找值得上报的非默认配置项。

    返回一个扁平字典，键为点分路径（dotpath），值为对应的配置值。
    """
    from hermes_cli.config import DEFAULT_CONFIG

    overrides = {}

    # 包含有意义的用户自定义覆盖项的配置段
    interesting_paths = [
        ("agent", "max_turns"),
        ("agent", "gateway_timeout"),
        ("agent", "tool_use_enforcement"),
        ("terminal", "backend"),
        ("terminal", "docker_image"),
        ("terminal", "persistent_shell"),
        ("browser", "allow_private_urls"),
        ("compression", "enabled"),
        ("compression", "threshold"),
        ("display", "streaming"),
        ("display", "skin"),
        ("display", "show_reasoning"),
        ("smart_model_routing", "enabled"),
        ("privacy", "redact_pii"),
        ("tts", "provider"),
    ]

    for section, key in interesting_paths:
        default_section = DEFAULT_CONFIG.get(section, {})
        user_section = config.get(section, {})
        if not isinstance(default_section, dict) or not isinstance(user_section, dict):
            continue
        default_val = default_section.get(key)
        user_val = user_section.get(key)
        if user_val is not None and user_val != default_val:
            overrides[f"{section}.{key}"] = str(user_val)

    # 工具集（如果与默认值不同）
    default_toolsets = DEFAULT_CONFIG.get("toolsets", [])
    user_toolsets = config.get("toolsets", [])
    if user_toolsets != default_toolsets:
        overrides["toolsets"] = str(user_toolsets)

    # 备用提供者列表
    fallbacks = config.get("fallback_providers", [])
    if fallbacks:
        overrides["fallback_providers"] = str(fallbacks)

    return overrides


def run_dump(args):
    """输出一份紧凑的、可直接复制粘贴的配置摘要。"""
    show_keys = getattr(args, "show_keys", False)

    # 从 .env 文件加载环境变量以便后续检查 API 密钥
    from dotenv import load_dotenv
    env_path = get_env_path()
    if env_path.exists():
        try:
            load_dotenv(env_path, encoding="utf-8")
        except UnicodeDecodeError:
            load_dotenv(env_path, encoding="latin-1")
    # 同时尝试加载项目根目录的 .env 作为开发环境的备用
    load_dotenv(get_project_root() / ".env", override=False, encoding="utf-8")

    project_root = get_project_root()
    hermes_home = get_hermes_home()

    try:
        from hermes_cli import __version__, __release_date__
    except ImportError:
        __version__ = "(unknown)"
        __release_date__ = ""

    commit = _get_git_commit(project_root)

    try:
        config = load_config()
    except Exception:
        config = {}

    model, provider = _get_model_and_provider(config)

    # 当前使用的配置文件（Profile）
    try:
        from hermes_cli.profiles import get_active_profile_name
        profile = get_active_profile_name() or "(default)"
    except Exception:
        profile = "(default)"

    # 终端后端
    terminal_cfg = config.get("terminal", {})
    backend = terminal_cfg.get("backend", "local")

    # OpenAI SDK 版本
    try:
        import openai
        openai_ver = openai.__version__
    except ImportError:
        openai_ver = "not installed"

    # 操作系统信息
    os_info = f"{platform.system()} {platform.release()} {platform.machine()}"

    lines = []
    lines.append("--- hermes dump ---")
    ver_str = f"{__version__}"
    if __release_date__:
        ver_str += f" ({__release_date__})"
    ver_str += f" [{commit}]"
    lines.append(f"version:          {ver_str}")
    lines.append(f"os:               {os_info}")
    lines.append(f"python:           {sys.version.split()[0]}")
    lines.append(f"openai_sdk:       {openai_ver}")
    lines.append(f"profile:          {profile}")
    lines.append(f"hermes_home:      {display_hermes_home()}")
    lines.append(f"model:            {model}")
    lines.append(f"provider:         {provider}")
    lines.append(f"terminal:         {backend}")

    # API 密钥状态
    lines.append("")
    lines.append("api_keys:")
    api_keys = [
        ("OPENROUTER_API_KEY", "openrouter"),
        ("OPENAI_API_KEY", "openai"),
        ("ANTHROPIC_API_KEY", "anthropic"),
        ("ANTHROPIC_TOKEN", "anthropic_token"),
        ("NOUS_API_KEY", "nous"),
        ("GLM_API_KEY", "glm/zai"),
        ("ZAI_API_KEY", "zai"),
        ("KIMI_API_KEY", "kimi"),
        ("MINIMAX_API_KEY", "minimax"),
        ("DEEPSEEK_API_KEY", "deepseek"),
        ("DASHSCOPE_API_KEY", "dashscope"),
        ("HF_TOKEN", "huggingface"),
        ("AI_GATEWAY_API_KEY", "ai_gateway"),
        ("OPENCODE_ZEN_API_KEY", "opencode_zen"),
        ("OPENCODE_GO_API_KEY", "opencode_go"),
        ("KILOCODE_API_KEY", "kilocode"),
        ("FIRECRAWL_API_KEY", "firecrawl"),
        ("TAVILY_API_KEY", "tavily"),
        ("BROWSERBASE_API_KEY", "browserbase"),
        ("FAL_KEY", "fal"),
        ("ELEVENLABS_API_KEY", "elevenlabs"),
        ("GITHUB_TOKEN", "github"),
    ]

    for env_var, label in api_keys:
        val = os.getenv(env_var, "")
        if show_keys and val:
            display = _redact(val)
        else:
            display = "set" if val else "not set"
        lines.append(f"  {label:<20} {display}")

    # 功能汇总
    lines.append("")
    lines.append("features:")

    toolsets = config.get("toolsets", ["hermes-cli"])
    lines.append(f"  toolsets:           {', '.join(toolsets) if toolsets else '(default)'}")
    lines.append(f"  mcp_servers:        {_count_mcp_servers(config)}")
    lines.append(f"  memory_provider:    {_memory_provider(config)}")
    lines.append(f"  gateway:            {_gateway_status()}")

    platforms = _configured_platforms()
    lines.append(f"  platforms:          {', '.join(platforms) if platforms else 'none'}")
    lines.append(f"  cron_jobs:          {_cron_summary(hermes_home)}")
    lines.append(f"  skills:             {_count_skills(hermes_home)}")

    # 非默认配置覆盖项
    overrides = _config_overrides(config)
    if overrides:
        lines.append("")
        lines.append("config_overrides:")
        for key, val in overrides.items():
            lines.append(f"  {key}: {val}")

    lines.append("--- end dump ---")

    output = "\n".join(lines)
    print(output)
