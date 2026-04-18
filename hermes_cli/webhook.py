"""hermes webhook —— 从 CLI 管理动态 webhook 订阅。

用法:
    hermes webhook subscribe <name> [options]
    hermes webhook list
    hermes webhook remove <name>
    hermes webhook test <name> [--payload '{"key": "value"}']

订阅持久化到 ~/.hermes/webhook_subscriptions.json，
webhook 适配器会热加载这些订阅，无需重启网关。
"""

import json
import os
import re
import secrets
import time
from pathlib import Path
from typing import Dict

from hermes_constants import display_hermes_home


_SUBSCRIPTIONS_FILENAME = "webhook_subscriptions.json"


def _hermes_home() -> Path:
    """获取 Hermes 主目录路径。"""
    from hermes_constants import get_hermes_home
    return get_hermes_home()


def _subscriptions_path() -> Path:
    """获取订阅配置文件的路径。"""
    return _hermes_home() / _SUBSCRIPTIONS_FILENAME


def _load_subscriptions() -> Dict[str, dict]:
    """从文件中加载所有 webhook 订阅。"""
    path = _subscriptions_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_subscriptions(subs: Dict[str, dict]) -> None:
    """将 webhook 订阅保存到文件（使用临时文件 + 原子替换确保安全）。"""
    path = _subscriptions_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(
        json.dumps(subs, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    os.replace(str(tmp_path), str(path))


def _get_webhook_config() -> dict:
    """加载 webhook 平台配置。未配置时返回 {}。"""
    try:
        from hermes_cli.config import load_config
        cfg = load_config()
        return cfg.get("platforms", {}).get("webhook", {})
    except Exception:
        return {}


def _is_webhook_enabled() -> bool:
    """检查 webhook 平台是否已启用。"""
    return bool(_get_webhook_config().get("enabled"))


def _get_webhook_base_url() -> str:
    """获取 webhook 服务的基础 URL。"""
    wh = _get_webhook_config().get("extra", {})
    host = wh.get("host", "0.0.0.0")
    port = wh.get("port", 8644)
    # 将 0.0.0.0 显示为 localhost 更加友好
    display_host = "localhost" if host == "0.0.0.0" else host
    return f"http://{display_host}:{port}"


def _setup_hint() -> str:
    """返回 webhook 设置指南文本。"""
    _dhh = display_hermes_home()
    return f"""
  Webhook platform is not enabled. To set it up:

  1. Run the gateway setup wizard:
     hermes gateway setup

  2. Or manually add to {_dhh}/config.yaml:
     platforms:
       webhook:
         enabled: true
         extra:
           host: "0.0.0.0"
           port: 8644
           secret: "your-global-hmac-secret"

  3. Or set environment variables in {_dhh}/.env:
     WEBHOOK_ENABLED=true
     WEBHOOK_PORT=8644
     WEBHOOK_SECRET=your-global-secret

  Then start the gateway: hermes gateway run
"""


def _require_webhook_enabled() -> bool:
    """检查 webhook 是否已启用。未启用时打印设置指南并返回 False。"""
    if _is_webhook_enabled():
        return True
    print(_setup_hint())
    return False


def webhook_command(args):
    """'hermes webhook' 子命令的入口点。"""
    sub = getattr(args, "webhook_action", None)

    if not sub:
        print("Usage: hermes webhook {subscribe|list|remove|test}")
        print("Run 'hermes webhook --help' for details.")
        return

    if not _require_webhook_enabled():
        return

    # 根据子命令分发到对应的处理函数
    if sub in ("subscribe", "add"):
        _cmd_subscribe(args)
    elif sub in ("list", "ls"):
        _cmd_list(args)
    elif sub in ("remove", "rm"):
        _cmd_remove(args)
    elif sub == "test":
        _cmd_test(args)


def _cmd_subscribe(args):
    """创建或更新一个 webhook 订阅。"""
    # 将名称标准化为小写、连字符格式
    name = args.name.strip().lower().replace(" ", "-")
    if not re.match(r'^[a-z0-9][a-z0-9_-]*$', name):
        print(f"Error: Invalid name '{name}'. Use lowercase alphanumeric with hyphens/underscores.")
        return

    subs = _load_subscriptions()
    is_update = name in subs

    # 如果未提供密钥，则自动生成一个安全的随机密钥
    secret = args.secret or secrets.token_urlsafe(32)
    events = [e.strip() for e in args.events.split(",")] if args.events else []

    # 构建订阅路由配置
    route = {
        "description": args.description or f"Agent-created subscription: {name}",
        "events": events,
        "secret": secret,
        "prompt": args.prompt or "",
        "skills": [s.strip() for s in args.skills.split(",")] if args.skills else [],
        "deliver": args.deliver or "log",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    if args.deliver_chat_id:
        route["deliver_extra"] = {"chat_id": args.deliver_chat_id}

    subs[name] = route
    _save_subscriptions(subs)

    base_url = _get_webhook_base_url()
    status = "Updated" if is_update else "Created"

    print(f"\n  {status} webhook subscription: {name}")
    print(f"  URL:    {base_url}/webhooks/{name}")
    print(f"  Secret: {secret}")
    if events:
        print(f"  Events: {', '.join(events)}")
    else:
        print("  Events: (all)")
    print(f"  Deliver: {route['deliver']}")
    if route.get("prompt"):
        prompt_preview = route["prompt"][:80] + ("..." if len(route["prompt"]) > 80 else "")
        print(f"  Prompt: {prompt_preview}")
    print(f"\n  Configure your service to POST to the URL above.")
    print(f"  Use the secret for HMAC-SHA256 signature validation.")
    print(f"  The gateway must be running to receive events (hermes gateway run).\n")


def _cmd_list(args):
    """列出所有动态 webhook 订阅。"""
    subs = _load_subscriptions()
    if not subs:
        print("  No dynamic webhook subscriptions.")
        print("  Create one with: hermes webhook subscribe <name>")
        return

    base_url = _get_webhook_base_url()
    print(f"\n  {len(subs)} webhook subscription(s):\n")
    for name, route in subs.items():
        events = ", ".join(route.get("events", [])) or "(all)"
        deliver = route.get("deliver", "log")
        desc = route.get("description", "")
        print(f"  ◆ {name}")
        if desc:
            print(f"    {desc}")
        print(f"    URL:     {base_url}/webhooks/{name}")
        print(f"    Events:  {events}")
        print(f"    Deliver: {deliver}")
        print()


def _cmd_remove(args):
    """删除一个 webhook 订阅。"""
    name = args.name.strip().lower()
    subs = _load_subscriptions()

    if name not in subs:
        print(f"  No subscription named '{name}'.")
        print("  Note: Static routes from config.yaml cannot be removed here.")
        return

    del subs[name]
    _save_subscriptions(subs)
    print(f"  Removed webhook subscription: {name}")


def _cmd_test(args):
    """向 webhook 路由发送测试 POST 请求。"""
    name = args.name.strip().lower()
    subs = _load_subscriptions()

    if name not in subs:
        print(f"  No subscription named '{name}'.")
        return

    route = subs[name]
    secret = route.get("secret", "")
    base_url = _get_webhook_base_url()
    url = f"{base_url}/webhooks/{name}"

    payload = args.payload or '{"test": true, "event_type": "test", "message": "Hello from hermes webhook test"}'

    # 使用 HMAC-SHA256 生成签名用于请求验证
    import hmac
    import hashlib
    sig = "sha256=" + hmac.new(
        secret.encode(), payload.encode(), hashlib.sha256
    ).hexdigest()

    print(f"  Sending test POST to {url}")
    try:
        import urllib.request
        req = urllib.request.Request(
            url,
            data=payload.encode(),
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": sig,
                "X-GitHub-Event": "test",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode()
            print(f"  Response ({resp.status}): {body}")
    except Exception as e:
        print(f"  Error: {e}")
        print("  Is the gateway running? (hermes gateway run)")
