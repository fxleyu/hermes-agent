"""
hermes CLI 的状态命令。

显示所有 Hermes Agent 组件的状态。
"""

import os
import sys
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.resolve()

from hermes_cli.auth import AuthError, resolve_provider
from hermes_cli.colors import Colors, color
from hermes_cli.config import get_env_path, get_env_value, get_hermes_home, load_config
from hermes_cli.models import provider_label
from hermes_cli.nous_subscription import get_nous_subscription_features
from hermes_cli.runtime_provider import resolve_requested_provider
from hermes_constants import OPENROUTER_MODELS_URL
from tools.tool_backend_helpers import managed_nous_tools_enabled

def check_mark(ok: bool) -> str:
    """返回绿色对勾或红色叉号。"""
    if ok:
        return color("✓", Colors.GREEN)
    return color("✗", Colors.RED)

def redact_key(key: str) -> str:
    """将 API 密钥脱敏用于显示。"""
    if not key:
        return "(not set)"
    if len(key) < 12:
        return "***"
    # 仅显示前 4 位和后 4 位，中间用省略号替代
    return key[:4] + "..." + key[-4:]


def _format_iso_timestamp(value) -> str:
    """将 ISO 时间戳格式化为状态输出，并转换为本地时区。"""
    if not value or not isinstance(value, str):
        return "(unknown)"
    from datetime import datetime, timezone
    text = value.strip()
    if not text:
        return "(unknown)"
    # 将 Z 后缀转换为标准的 +00:00 格式
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
    except Exception:
        return value
    return parsed.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def _configured_model_label(config: dict) -> str:
    """从 config.yaml 中返回配置的默认模型。"""
    model_cfg = config.get("model")
    # 支持字典格式和字符串格式两种配置方式
    if isinstance(model_cfg, dict):
        model = (model_cfg.get("default") or model_cfg.get("name") or "").strip()
    elif isinstance(model_cfg, str):
        model = model_cfg.strip()
    else:
        model = ""
    return model or "(not set)"


def _effective_provider_label() -> str:
    """返回与当前 CLI 运行时解析结果匹配的提供者标签。"""
    requested = resolve_requested_provider()
    try:
        effective = resolve_provider(requested)
    except AuthError:
        effective = requested or "auto"

    # 如果设置了自定义基础 URL，则视为自定义提供者
    if effective == "openrouter" and get_env_value("OPENAI_BASE_URL"):
        effective = "custom"

    return provider_label(effective)


from hermes_constants import is_termux as _is_termux


def show_status(args):
    """显示所有 Hermes Agent 组件的状态。"""
    show_all = getattr(args, 'all', False)
    deep = getattr(args, 'deep', False)

    print()
    print(color("┌─────────────────────────────────────────────────────────┐", Colors.CYAN))
    print(color("│                 ⚕ Hermes Agent Status                  │", Colors.CYAN))
    print(color("└─────────────────────────────────────────────────────────┘", Colors.CYAN))

    # =========================================================================
    # 环境信息
    # =========================================================================
    print()
    print(color("◆ Environment", Colors.CYAN, Colors.BOLD))
    print(f"  Project:      {PROJECT_ROOT}")
    print(f"  Python:       {sys.version.split()[0]}")

    env_path = get_env_path()
    print(f"  .env file:    {check_mark(env_path.exists())} {'exists' if env_path.exists() else 'not found'}")

    try:
        config = load_config()
    except Exception:
        config = {}

    print(f"  Model:        {_configured_model_label(config)}")
    print(f"  Provider:     {_effective_provider_label()}")

    # =========================================================================
    # API 密钥
    # =========================================================================
    print()
    print(color("◆ API Keys", Colors.CYAN, Colors.BOLD))

    keys = {
        "OpenRouter": "OPENROUTER_API_KEY",
        "OpenAI": "OPENAI_API_KEY",
        "Z.AI/GLM": "GLM_API_KEY",
        "Kimi": "KIMI_API_KEY",
        "MiniMax": "MINIMAX_API_KEY",
        "MiniMax-CN": "MINIMAX_CN_API_KEY",
        "Firecrawl": "FIRECRAWL_API_KEY",
        "Tavily": "TAVILY_API_KEY",
        "Browser Use": "BROWSER_USE_API_KEY",  # 可选 —— 本地浏览器无需此密钥
        "Browserbase": "BROWSERBASE_API_KEY",  # 可选 —— 仅直接凭据
        "FAL": "FAL_KEY",
        "Tinker": "TINKER_API_KEY",
        "WandB": "WANDB_API_KEY",
        "ElevenLabs": "ELEVENLABS_API_KEY",
        "GitHub": "GITHUB_TOKEN",
    }

    for name, env_var in keys.items():
        value = get_env_value(env_var) or ""
        has_key = bool(value)
        display = redact_key(value) if not show_all else value
        print(f"  {name:<12}  {check_mark(has_key)} {display}")

    from hermes_cli.auth import get_anthropic_key
    anthropic_value = get_anthropic_key()
    anthropic_display = redact_key(anthropic_value) if not show_all else anthropic_value
    print(f"  {'Anthropic':<12}  {check_mark(bool(anthropic_value))} {anthropic_display}")

    # =========================================================================
    # 认证提供者（OAuth）
    # =========================================================================
    print()
    print(color("◆ Auth Providers", Colors.CYAN, Colors.BOLD))

    try:
        from hermes_cli.auth import get_nous_auth_status, get_codex_auth_status, get_qwen_auth_status
        nous_status = get_nous_auth_status()
        codex_status = get_codex_auth_status()
        qwen_status = get_qwen_auth_status()
    except Exception:
        nous_status = {}
        codex_status = {}
        qwen_status = {}

    # Nous Portal 认证状态
    nous_logged_in = bool(nous_status.get("logged_in"))
    print(
        f"  {'Nous Portal':<12}  {check_mark(nous_logged_in)} "
        f"{'logged in' if nous_logged_in else 'not logged in (run: hermes model)'}"
    )
    if nous_logged_in:
        portal_url = nous_status.get("portal_base_url") or "(unknown)"
        access_exp = _format_iso_timestamp(nous_status.get("access_expires_at"))
        key_exp = _format_iso_timestamp(nous_status.get("agent_key_expires_at"))
        refresh_label = "yes" if nous_status.get("has_refresh_token") else "no"
        print(f"    Portal URL: {portal_url}")
        print(f"    Access exp: {access_exp}")
        print(f"    Key exp:    {key_exp}")
        print(f"    Refresh:    {refresh_label}")

    # OpenAI Codex 认证状态
    codex_logged_in = bool(codex_status.get("logged_in"))
    print(
        f"  {'OpenAI Codex':<12}  {check_mark(codex_logged_in)} "
        f"{'logged in' if codex_logged_in else 'not logged in (run: hermes model)'}"
    )
    codex_auth_file = codex_status.get("auth_store")
    if codex_auth_file:
        print(f"    Auth file:  {codex_auth_file}")
    codex_last_refresh = _format_iso_timestamp(codex_status.get("last_refresh"))
    if codex_status.get("last_refresh"):
        print(f"    Refreshed:  {codex_last_refresh}")
    if codex_status.get("error") and not codex_logged_in:
        print(f"    Error:      {codex_status.get('error')}")

    # Qwen OAuth 认证状态
    qwen_logged_in = bool(qwen_status.get("logged_in"))
    print(
        f"  {'Qwen OAuth':<12}  {check_mark(qwen_logged_in)} "
        f"{'logged in' if qwen_logged_in else 'not logged in (run: qwen auth qwen-oauth)'}"
    )
    qwen_auth_file = qwen_status.get("auth_file")
    if qwen_auth_file:
        print(f"    Auth file:  {qwen_auth_file}")
    qwen_exp = qwen_status.get("expires_at_ms")
    if qwen_exp:
        from datetime import datetime, timezone
        print(f"    Access exp: {datetime.fromtimestamp(int(qwen_exp) / 1000, tz=timezone.utc).isoformat()}")
    if qwen_status.get("error") and not qwen_logged_in:
        print(f"    Error:      {qwen_status.get('error')}")

    # =========================================================================
    # Nous 订阅功能
    # =========================================================================
    if managed_nous_tools_enabled():
        features = get_nous_subscription_features(config)
        print()
        print(color("◆ Nous Tool Gateway", Colors.CYAN, Colors.BOLD))
        if not features.nous_auth_present:
            print("  Nous Portal   ✗ not logged in")
        else:
            print("  Nous Portal   ✓ managed tools available")
        for feature in features.items():
            if feature.managed_by_nous:
                state = "active via Nous subscription"
            elif feature.active:
                current = feature.current_provider or "configured provider"
                state = f"active via {current}"
            elif feature.included_by_default and features.nous_auth_present:
                state = "included by subscription, not currently selected"
            elif feature.key == "modal" and features.nous_auth_present:
                state = "available via subscription (optional)"
            else:
                state = "not configured"
            print(f"  {feature.label:<15} {check_mark(feature.available or feature.active or feature.managed_by_nous)} {state}")
    elif nous_logged_in:
        # 已登录 Nous 但在免费层级 —— 显示升级提示
        print()
        print(color("◆ Nous Tool Gateway", Colors.CYAN, Colors.BOLD))
        print("  Your free-tier Nous account does not include Tool Gateway access.")
        print("  Upgrade your subscription to unlock managed web, image, TTS, and browser tools.")
        try:
            portal_url = nous_status.get("portal_base_url", "").rstrip("/")
            if portal_url:
                print(f"  Upgrade: {portal_url}")
        except Exception:
            pass

    # =========================================================================
    # API 密钥提供者
    # =========================================================================
    print()
    print(color("◆ API-Key Providers", Colors.CYAN, Colors.BOLD))

    apikey_providers = {
        "Z.AI / GLM":       ("GLM_API_KEY", "ZAI_API_KEY", "Z_AI_API_KEY"),
        "Kimi / Moonshot":  ("KIMI_API_KEY",),
        "MiniMax":          ("MINIMAX_API_KEY",),
        "MiniMax (China)":  ("MINIMAX_CN_API_KEY",),
    }
    for pname, env_vars in apikey_providers.items():
        # 按顺序检查多个环境变量，使用第一个有值的
        key_val = ""
        for ev in env_vars:
            key_val = get_env_value(ev) or ""
            if key_val:
                break
        configured = bool(key_val)
        label = "configured" if configured else "not configured (run: hermes model)"
        print(f"  {pname:<16} {check_mark(configured)} {label}")

    # =========================================================================
    # 终端配置
    # =========================================================================
    print()
    print(color("◆ Terminal Backend", Colors.CYAN, Colors.BOLD))

    terminal_env = os.getenv("TERMINAL_ENV", "")
    if not terminal_env:
        # 环境变量未设置时回退到配置文件中的值
        # （hermes status 不经过 cli.py 的配置加载流程）
        try:
            _cfg = load_config()
            terminal_env = _cfg.get("terminal", {}).get("backend", "local")
        except Exception:
            terminal_env = "local"
    print(f"  Backend:      {terminal_env}")

    # 根据后端类型显示相应的配置详情
    if terminal_env == "ssh":
        ssh_host = os.getenv("TERMINAL_SSH_HOST", "")
        ssh_user = os.getenv("TERMINAL_SSH_USER", "")
        print(f"  SSH Host:     {ssh_host or '(not set)'}")
        print(f"  SSH User:     {ssh_user or '(not set)'}")
    elif terminal_env == "docker":
        docker_image = os.getenv("TERMINAL_DOCKER_IMAGE", "python:3.11-slim")
        print(f"  Docker Image: {docker_image}")
    elif terminal_env == "daytona":
        daytona_image = os.getenv("TERMINAL_DAYTONA_IMAGE", "nikolaik/python-nodejs:python3.11-nodejs20")
        print(f"  Daytona Image: {daytona_image}")

    sudo_password = os.getenv("SUDO_PASSWORD", "")
    print(f"  Sudo:         {check_mark(bool(sudo_password))} {'enabled' if sudo_password else 'disabled'}")

    # =========================================================================
    # 消息平台
    # =========================================================================
    print()
    print(color("◆ Messaging Platforms", Colors.CYAN, Colors.BOLD))

    platforms = {
        "Telegram": ("TELEGRAM_BOT_TOKEN", "TELEGRAM_HOME_CHANNEL"),
        "Discord": ("DISCORD_BOT_TOKEN", "DISCORD_HOME_CHANNEL"),
        "WhatsApp": ("WHATSAPP_ENABLED", None),
        "Signal": ("SIGNAL_HTTP_URL", "SIGNAL_HOME_CHANNEL"),
        "Slack": ("SLACK_BOT_TOKEN", None),
        "Email": ("EMAIL_ADDRESS", "EMAIL_HOME_ADDRESS"),
        "SMS": ("TWILIO_ACCOUNT_SID", "SMS_HOME_CHANNEL"),
        "DingTalk": ("DINGTALK_CLIENT_ID", None),
        "Feishu": ("FEISHU_APP_ID", "FEISHU_HOME_CHANNEL"),
        "WeCom": ("WECOM_BOT_ID", "WECOM_HOME_CHANNEL"),
        "WeCom Callback": ("WECOM_CALLBACK_CORP_ID", None),
        "Weixin": ("WEIXIN_ACCOUNT_ID", "WEIXIN_HOME_CHANNEL"),
        "BlueBubbles": ("BLUEBUBBLES_SERVER_URL", "BLUEBUBBLES_HOME_CHANNEL"),
        "QQBot": ("QQ_APP_ID", "QQ_HOME_CHANNEL"),
    }

    for name, (token_var, home_var) in platforms.items():
        token = os.getenv(token_var, "")
        has_token = bool(token)

        home_channel = ""
        if home_var:
            home_channel = os.getenv(home_var, "")

        status = "configured" if has_token else "not configured"
        if home_channel:
            status += f" (home: {home_channel})"

        print(f"  {name:<12}  {check_mark(has_token)} {status}")

    # =========================================================================
    # 网关状态
    # =========================================================================
    print()
    print(color("◆ Gateway Service", Colors.CYAN, Colors.BOLD))

    # 根据不同平台（Termux、Linux、macOS）使用不同的方式检查网关状态
    if _is_termux():
        try:
            from hermes_cli.gateway import find_gateway_pids
            gateway_pids = find_gateway_pids()
        except Exception:
            gateway_pids = []
        is_running = bool(gateway_pids)
        print(f"  Status:       {check_mark(is_running)} {'running' if is_running else 'stopped'}")
        print("  Manager:      Termux / manual process")
        if gateway_pids:
            rendered = ", ".join(str(pid) for pid in gateway_pids[:3])
            if len(gateway_pids) > 3:
                rendered += ", ..."
            print(f"  PID(s):       {rendered}")
        else:
            print("  Start with:   hermes gateway")
            print("  Note:         Android may stop background jobs when Termux is suspended")

    elif sys.platform.startswith('linux'):
        from hermes_constants import is_container
        if is_container():
            # Docker/Podman 环境：无 systemd —— 检查运行中的网关进程
            try:
                from hermes_cli.gateway import find_gateway_pids
                gateway_pids = find_gateway_pids()
                is_active = len(gateway_pids) > 0
            except Exception:
                is_active = False
            print(f"  Status:       {check_mark(is_active)} {'running' if is_active else 'stopped'}")
            print("  Manager:      docker (foreground)")
        else:
            # 常规 Linux：通过 systemd 检查服务状态
            try:
                from hermes_cli.gateway import get_service_name
                _gw_svc = get_service_name()
            except Exception:
                _gw_svc = "hermes-gateway"
            try:
                result = subprocess.run(
                    ["systemctl", "--user", "is-active", _gw_svc],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                is_active = result.stdout.strip() == "active"
            except (FileNotFoundError, subprocess.TimeoutExpired):
                is_active = False
            print(f"  Status:       {check_mark(is_active)} {'running' if is_active else 'stopped'}")
            print("  Manager:      systemd (user)")

    elif sys.platform == 'darwin':
        # macOS：通过 launchd 检查服务状态
        from hermes_cli.gateway import get_launchd_label
        try:
            result = subprocess.run(
                ["launchctl", "list", get_launchd_label()],
                capture_output=True,
                text=True,
                timeout=5
            )
            is_loaded = result.returncode == 0
        except subprocess.TimeoutExpired:
            is_loaded = False
        print(f"  Status:       {check_mark(is_loaded)} {'loaded' if is_loaded else 'not loaded'}")
        print("  Manager:      launchd")
    else:
        print(f"  Status:       {color('N/A', Colors.DIM)}")
        print("  Manager:      (not supported on this platform)")

    # =========================================================================
    # 定时任务
    # =========================================================================
    print()
    print(color("◆ Scheduled Jobs", Colors.CYAN, Colors.BOLD))

    jobs_file = get_hermes_home() / "cron" / "jobs.json"
    if jobs_file.exists():
        import json
        try:
            with open(jobs_file, encoding="utf-8") as f:
                data = json.load(f)
                jobs = data.get("jobs", [])
                enabled_jobs = [j for j in jobs if j.get("enabled", True)]
                print(f"  Jobs:         {len(enabled_jobs)} active, {len(jobs)} total")
        except Exception:
            print("  Jobs:         (error reading jobs file)")
    else:
        print("  Jobs:         0")

    # =========================================================================
    # 会话
    # =========================================================================
    print()
    print(color("◆ Sessions", Colors.CYAN, Colors.BOLD))

    sessions_file = get_hermes_home() / "sessions" / "sessions.json"
    if sessions_file.exists():
        import json
        try:
            with open(sessions_file, encoding="utf-8") as f:
                data = json.load(f)
                print(f"  Active:       {len(data)} session(s)")
        except Exception:
            print("  Active:       (error reading sessions file)")
    else:
        print("  Active:       0")

    # =========================================================================
    # 深度检查
    # =========================================================================
    if deep:
        print()
        print(color("◆ Deep Checks", Colors.CYAN, Colors.BOLD))

        # 检查 OpenRouter 连通性
        openrouter_key = os.getenv("OPENROUTER_API_KEY", "")
        if openrouter_key:
            try:
                import httpx
                response = httpx.get(
                    OPENROUTER_MODELS_URL,
                    headers={"Authorization": f"Bearer {openrouter_key}"},
                    timeout=10
                )
                ok = response.status_code == 200
                print(f"  OpenRouter:   {check_mark(ok)} {'reachable' if ok else f'error ({response.status_code})'}")
            except Exception as e:
                print(f"  OpenRouter:   {check_mark(False)} error: {e}")

        # 检查网关端口
        try:
            import socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex(('127.0.0.1', 18789))
            sock.close()
            # 端口被占用 = 网关可能正在运行
            port_in_use = result == 0
            # 这是信息性的，不一定表示有问题
            print(f"  Port 18789:   {'in use' if port_in_use else 'available'}")
        except OSError:
            pass

    print()
    print(color("─" * 60, Colors.DIM))
    print(color("  Run 'hermes doctor' for detailed diagnostics", Colors.DIM))
    print(color("  Run 'hermes setup' to configure", Colors.DIM))
    print()
