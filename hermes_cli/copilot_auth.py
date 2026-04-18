"""GitHub Copilot 认证工具。

实现 Copilot CLI 使用的 OAuth 设备码流程，并处理
Copilot API 的 token 验证/交换。

Token 类型支持（按 GitHub 文档）:
  gho_          OAuth token           ✓  （默认通过 copilot login）
  github_pat_   细粒度 PAT            ✓  （需要 Copilot Requests 权限）
  ghu_          GitHub App token      ✓  （通过环境变量）
  ghp_          经典 PAT              ✗  不支持

凭证搜索顺序（匹配 Copilot CLI 行为）:
  1. COPILOT_GITHUB_TOKEN 环境变量
  2. GH_TOKEN 环境变量
  3. GITHUB_TOKEN 环境变量
  4. gh auth token CLI 回退
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# OAuth 设备码流程常量（与 opencode/Copilot CLI 使用相同的客户端 ID）
COPILOT_OAUTH_CLIENT_ID = "Ov23li8tweQw6odWQebz"
# Token 类型前缀
_CLASSIC_PAT_PREFIX = "ghp_"
_SUPPORTED_PREFIXES = ("gho_", "github_pat_", "ghu_")

# 环境变量搜索顺序（匹配 Copilot CLI）
COPILOT_ENV_VARS = ("COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN")

# 轮询常量
_DEVICE_CODE_POLL_INTERVAL = 5  # 秒
_DEVICE_CODE_POLL_SAFETY_MARGIN = 3  # 秒


def validate_copilot_token(token: str) -> tuple[bool, str]:
    """验证 token 是否可用于 Copilot API。

    返回 (valid, message)。
    """
    token = token.strip()
    if not token:
        return False, "Empty token"

    if token.startswith(_CLASSIC_PAT_PREFIX):
        return False, (
            "Classic Personal Access Tokens (ghp_*) are not supported by the "
            "Copilot API. Use one of:\n"
            "  → `copilot login` or `hermes model` to authenticate via OAuth\n"
            "  → A fine-grained PAT (github_pat_*) with Copilot Requests permission\n"
            "  → `gh auth login` with the default device code flow (produces gho_* tokens)"
        )

    return True, "OK"


def resolve_copilot_token() -> tuple[str, str]:
    """解析适用于 Copilot API 的 GitHub token。

    返回 (token, source)，其中 source 描述 token 的来源。
    如果仅有经典 PAT 可用则抛出 ValueError。
    """
    # 1. 按优先级顺序检查环境变量
    for env_var in COPILOT_ENV_VARS:
        val = os.getenv(env_var, "").strip()
        if val:
            valid, msg = validate_copilot_token(val)
            if not valid:
                logger.warning(
                    "Token from %s is not supported: %s", env_var, msg
                )
                continue
            return val, env_var

    # 2. 回退到 gh auth token
    token = _try_gh_cli_token()
    if token:
        valid, msg = validate_copilot_token(token)
        if not valid:
            raise ValueError(
                f"Token from `gh auth token` is a classic PAT (ghp_*). {msg}"
            )
        return token, "gh auth token"

    return "", ""


def _gh_cli_candidates() -> list[str]:
    """返回候选的 ``gh`` 二进制路径，包括常见的 Homebrew 安装位置。"""
    candidates: list[str] = []

    resolved = shutil.which("gh")
    if resolved:
        candidates.append(resolved)

    for candidate in (
        "/opt/homebrew/bin/gh",
        "/usr/local/bin/gh",
        str(Path.home() / ".local" / "bin" / "gh"),
    ):
        if candidate in candidates:
            continue
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            candidates.append(candidate)

    return candidates


def _try_gh_cli_token() -> Optional[str]:
    """当 GitHub CLI 可用时，从 ``gh auth token`` 返回 token。

    当设置了 COPILOT_GH_HOST 时，传递 ``--hostname`` 以便 gh 返回
    正确主机的 token。同时从子进程环境中去除 GITHUB_TOKEN / GH_TOKEN，
    使 ``gh`` 从自己的凭证存储（hosts.yml）读取，
    而不是直接回显环境变量。
    """
    hostname = os.getenv("COPILOT_GH_HOST", "").strip()

    # 构建干净的环境，使 gh 不会在 GITHUB_TOKEN / GH_TOKEN 上短路
    clean_env = {k: v for k, v in os.environ.items()
                 if k not in ("GITHUB_TOKEN", "GH_TOKEN")}

    for gh_path in _gh_cli_candidates():
        cmd = [gh_path, "auth", "token"]
        if hostname:
            cmd += ["--hostname", hostname]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=5,
                env=clean_env,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            logger.debug("gh CLI token lookup failed (%s): %s", gh_path, exc)
            continue
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    return None


# ─── OAuth 设备码流程 ────────────────────────────────────────────────

def copilot_device_code_login(
    *,
    host: str = "github.com",
    timeout_seconds: float = 300,
) -> Optional[str]:
    """运行 GitHub OAuth 设备码流程以登录 Copilot。

    打印操作说明给用户，轮询等待完成，成功时返回
    OAuth access token，失败/取消时返回 None。

    此流程复制了 opencode 和 Copilot CLI 使用的流程。
    """
    import urllib.request
    import urllib.parse

    domain = host.rstrip("/")
    device_code_url = f"https://{domain}/login/device/code"
    access_token_url = f"https://{domain}/login/oauth/access_token"

    # 第 1 步：请求设备码
    data = urllib.parse.urlencode({
        "client_id": COPILOT_OAUTH_CLIENT_ID,
        "scope": "read:user",
    }).encode()

    req = urllib.request.Request(
        device_code_url,
        data=data,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "HermesAgent/1.0",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            device_data = json.loads(resp.read().decode())
    except Exception as exc:
        logger.error("Failed to initiate device authorization: %s", exc)
        print(f"  ✗ Failed to start device authorization: {exc}")
        return None

    verification_uri = device_data.get("verification_uri", "https://github.com/login/device")
    user_code = device_data.get("user_code", "")
    device_code = device_data.get("device_code", "")
    interval = max(device_data.get("interval", _DEVICE_CODE_POLL_INTERVAL), 1)

    if not device_code or not user_code:
        print("  ✗ GitHub did not return a device code.")
        return None

    # 第 2 步：显示操作说明
    print()
    print(f"  Open this URL in your browser: {verification_uri}")
    print(f"  Enter this code: {user_code}")
    print()
    print("  Waiting for authorization...", end="", flush=True)

    # 第 3 步：轮询等待完成
    deadline = time.time() + timeout_seconds

    while time.time() < deadline:
        time.sleep(interval + _DEVICE_CODE_POLL_SAFETY_MARGIN)

        poll_data = urllib.parse.urlencode({
            "client_id": COPILOT_OAUTH_CLIENT_ID,
            "device_code": device_code,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        }).encode()

        poll_req = urllib.request.Request(
            access_token_url,
            data=poll_data,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "HermesAgent/1.0",
            },
        )

        try:
            with urllib.request.urlopen(poll_req, timeout=10) as resp:
                result = json.loads(resp.read().decode())
        except Exception:
            print(".", end="", flush=True)
            continue

        if result.get("access_token"):
            print(" ✓")
            return result["access_token"]

        error = result.get("error", "")
        if error == "authorization_pending":
            print(".", end="", flush=True)
            continue
        elif error == "slow_down":
            # RFC 8628：在轮询间隔上增加 5 秒
            server_interval = result.get("interval")
            if isinstance(server_interval, (int, float)) and server_interval > 0:
                interval = int(server_interval)
            else:
                interval += 5
            print(".", end="", flush=True)
            continue
        elif error == "expired_token":
            print()
            print("  ✗ Device code expired. Please try again.")
            return None
        elif error == "access_denied":
            print()
            print("  ✗ Authorization was denied.")
            return None
        elif error:
            print()
            print(f"  ✗ Authorization failed: {error}")
            return None

    print()
    print("  ✗ Timed out waiting for authorization.")
    return None


# ─── Copilot API 请求头 ───────────────────────────────────────────────────

def copilot_request_headers(
    *,
    is_agent_turn: bool = True,
    is_vision: bool = False,
) -> dict[str, str]:
    """构建 Copilot API 请求的标准请求头。

    复制 opencode 和 Copilot CLI 使用的请求头集合。
    """
    headers: dict[str, str] = {
        "Editor-Version": "vscode/1.104.1",
        "User-Agent": "HermesAgent/1.0",
        "Copilot-Integration-Id": "vscode-chat",
        "Openai-Intent": "conversation-edits",
        "x-initiator": "agent" if is_agent_turn else "user",
    }
    if is_vision:
        headers["Copilot-Vision-Request"] = "true"

    return headers
