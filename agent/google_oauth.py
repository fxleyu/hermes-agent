"""Gemini（google-gemini-cli）推理提供者的 Google OAuth PKCE 流程。

本模块实现了针对 Google accounts.google.com 端点的
授权码 + PKCE (S256) OAuth 流程。生成的访问令牌由
``agent.gemini_cloudcode_adapter`` 使用，用于与 ``cloudcode-pa.googleapis.com``
通信（即为 Gemini CLI 免费和付费层提供支持的 Google Code Assist 后端）。

参考来源：
- jenslys/opencode-gemini-auth (MIT) — 总体流程结构、公共 OAuth 凭据、请求格式
- clawdbot/extensions/google/ — 刷新令牌轮换、VPC-SC 处理参考
- PRs #10176 (@sliverp) 和 #10779 (@newarthur) — PKCE 模块结构、跨进程锁

存储位置（``~/.hermes/auth/google_oauth.json``，权限 0o600）：

    {
      "refresh": "refreshToken|projectId|managedProjectId",
      "access": "...",
      "expires": 1744848000000,   // unix 毫秒时间戳
      "email": "user@example.com"
    }

``refresh`` 字段将 refresh_token 与已解析的 GCP 项目 ID 打包在一起，
使得后续会话无需重新发现项目。这与 opencode-gemini-auth 的存储契约完全一致。

打包格式即使在没有项目 ID 时也可解析——裸 refresh_token 被视为"打包了空 ID"。

公共客户端凭据
-------------------------
下面的 client_id 和 client_secret 是 Google 为其自有开源 gemini-cli
提供的公共桌面 OAuth 客户端。它们内置在 gemini-cli npm 包的每份副本中，
不是机密信息——桌面 OAuth 客户端不需要保密（PKCE 提供安全保证）。
在此处内置它们与 opencode-gemini-auth 和官方 Google gemini-cli 一致。

政策说明：Google 认为使用此 OAuth 客户端与第三方软件属于政策违规。
用户在授权开始前会看到带有 ``confirm(default=False)`` 的前置警告。
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import http.server
import json
import logging
import os
import secrets
import socket
import stat
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from hermes_constants import get_hermes_home

logger = logging.getLogger(__name__)


# =============================================================================
# OAuth 客户端凭据解析。
#
# 解析优先级：
#   1. HERMES_GEMINI_CLIENT_ID / HERMES_GEMINI_CLIENT_SECRET 环境变量（高级用户）
#   2. 内置默认值 — Google 的公共 gemini-cli 桌面 OAuth 客户端
#      （内置在 Google 开源 gemini-cli 的每份副本中；非机密——
#      桌面 OAuth 客户端使用 PKCE 而非 client_secret 保证安全）。
#      使用这些与 opencode-gemini-auth 行为一致。
#   3. 回退：从本地安装的 gemini-cli 二进制文件中抓取（帮助
#      故意删除内置默认值的分支）。
#   4. 以帮助性错误信息失败。
# =============================================================================

ENV_CLIENT_ID = "HERMES_GEMINI_CLIENT_ID"
ENV_CLIENT_SECRET = "HERMES_GEMINI_CLIENT_SECRET"

# 公共 gemini-cli 桌面 OAuth 客户端（内置在 Google 开源
# gemini-cli MIT 仓库中）。分段组合以保持常量可读性，
# 并为每段明确注释其为何是非机密的。
# 参见: https://github.com/google-gemini/gemini-cli/blob/main/packages/core/src/code_assist/oauth2.ts
_PUBLIC_CLIENT_ID_PROJECT_NUM = "681255809395"
_PUBLIC_CLIENT_ID_HASH = "oo8ft2oprdrnp9e3aqf6av3hmdib135j"
_PUBLIC_CLIENT_SECRET_SUFFIX = "4uHgMPm-1o7Sk-geV6Cu5clXFsxl"

_DEFAULT_CLIENT_ID = (
    f"{_PUBLIC_CLIENT_ID_PROJECT_NUM}-{_PUBLIC_CLIENT_ID_HASH}"
    ".apps.googleusercontent.com"
)
_DEFAULT_CLIENT_SECRET = f"GOCSPX-{_PUBLIC_CLIENT_SECRET_SUFFIX}"

# 从已安装的 gemini-cli 进行回退抓取的正则表达式模式。
import re as _re
_CLIENT_ID_PATTERN = _re.compile(
    r"OAUTH_CLIENT_ID\s*=\s*['\"]([0-9]+-[a-z0-9]+\.apps\.googleusercontent\.com)['\"]"
)
_CLIENT_SECRET_PATTERN = _re.compile(
    r"OAUTH_CLIENT_SECRET\s*=\s*['\"](GOCSPX-[A-Za-z0-9_-]+)['\"]"
)
_CLIENT_ID_SHAPE = _re.compile(r"([0-9]{8,}-[a-z0-9]{20,}\.apps\.googleusercontent\.com)")
_CLIENT_SECRET_SHAPE = _re.compile(r"(GOCSPX-[A-Za-z0-9_-]{20,})")


# =============================================================================
# 端点与常量
# =============================================================================

AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
USERINFO_ENDPOINT = "https://www.googleapis.com/oauth2/v1/userinfo"

OAUTH_SCOPES = (
    "https://www.googleapis.com/auth/cloud-platform "
    "https://www.googleapis.com/auth/userinfo.email "
    "https://www.googleapis.com/auth/userinfo.profile"
)

DEFAULT_REDIRECT_PORT = 8085
REDIRECT_HOST = "127.0.0.1"
CALLBACK_PATH = "/oauth2callback"

# 60 秒时钟偏差缓冲（与 opencode-gemini-auth 一致）。
REFRESH_SKEW_SECONDS = 60

TOKEN_REQUEST_TIMEOUT_SECONDS = 20.0
CALLBACK_WAIT_SECONDS = 300
LOCK_TIMEOUT_SECONDS = 30.0

# 无头环境检测
_HEADLESS_ENV_VARS = ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY", "HERMES_HEADLESS")


# =============================================================================
# 错误类型
# =============================================================================

class GoogleOAuthError(RuntimeError):
    """Google OAuth 流程中任何失败时抛出。"""

    def __init__(self, message: str, *, code: str = "google_oauth_error") -> None:
        super().__init__(message)
        self.code = code


# =============================================================================
# 文件路径与跨进程锁
# =============================================================================

def _credentials_path() -> Path:
    return get_hermes_home() / "auth" / "google_oauth.json"


def _lock_path() -> Path:
    return _credentials_path().with_suffix(".json.lock")


_lock_state = threading.local()


@contextlib.contextmanager
def _credentials_lock(timeout_seconds: float = LOCK_TIMEOUT_SECONDS):
    """凭据文件的跨进程锁（POSIX 使用 fcntl / Windows 使用 msvcrt）。"""
    depth = getattr(_lock_state, "depth", 0)
    if depth > 0:
        _lock_state.depth = depth + 1
        try:
            yield
        finally:
            _lock_state.depth -= 1
        return

    lock_file_path = _lock_path()
    lock_file_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_file_path), os.O_CREAT | os.O_RDWR, 0o600)
    acquired = False
    try:
        try:
            import fcntl
        except ImportError:
            fcntl = None

        if fcntl is not None:
            deadline = time.monotonic() + max(0.0, float(timeout_seconds))
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(
                            f"Timed out acquiring Google OAuth credentials lock at {lock_file_path}."
                        )
                    time.sleep(0.05)
        else:
            try:
                import msvcrt  # type: ignore[import-not-found]

                deadline = time.monotonic() + max(0.0, float(timeout_seconds))
                while True:
                    try:
                        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                        acquired = True
                        break
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise TimeoutError(
                                f"Timed out acquiring Google OAuth credentials lock at {lock_file_path}."
                            )
                        time.sleep(0.05)
            except ImportError:
                acquired = True

        _lock_state.depth = 1
        yield
    finally:
        try:
            if acquired:
                try:
                    import fcntl

                    fcntl.flock(fd, fcntl.LOCK_UN)
                except ImportError:
                    try:
                        import msvcrt  # type: ignore[import-not-found]

                        try:
                            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                        except OSError:
                            pass
                    except ImportError:
                        pass
        finally:
            os.close(fd)
            _lock_state.depth = 0


# =============================================================================
# 客户端 ID 解析
# =============================================================================

_scraped_creds_cache: Dict[str, str] = {}


def _locate_gemini_cli_oauth_js() -> Optional[Path]:
    """遍历用户的 gemini 二进制安装路径以查找其 oauth2.js。

    如果 gemini 未安装则返回 None。同时支持 npm 安装
    （``node_modules/@google/gemini-cli-core/dist/**/code_assist/oauth2.js``）
    和 Homebrew 的 ``bundle/`` 布局。
    """
    import shutil

    gemini = shutil.which("gemini")
    if not gemini:
        return None

    try:
        real = Path(gemini).resolve()
    except OSError:
        return None

    # 从二进制文件向上遍历以找到 npm 安装根目录
    search_dirs: list[Path] = []
    cur = real.parent
    for _ in range(8):  # 不要遍历太远
        search_dirs.append(cur)
        if (cur / "node_modules").exists():
            search_dirs.append(cur / "node_modules" / "@google" / "gemini-cli-core")
            break
        if cur.parent == cur:
            break
        cur = cur.parent

    for root in search_dirs:
        if not root.exists():
            continue
        # 常见已知路径
        candidates = [
            root / "dist" / "src" / "code_assist" / "oauth2.js",
            root / "dist" / "code_assist" / "oauth2.js",
            root / "src" / "code_assist" / "oauth2.js",
        ]
        for c in candidates:
            if c.exists():
                return c
        # 递归回退：在 10 层目录深度内查找 oauth2.js
        try:
            for path in root.rglob("oauth2.js"):
                return path
        except (OSError, ValueError):
            continue

    return None


def _scrape_client_credentials() -> Tuple[str, str]:
    """从本地 gemini-cli 安装中提取 client_id + client_secret。"""
    if _scraped_creds_cache.get("resolved"):
        return _scraped_creds_cache.get("client_id", ""), _scraped_creds_cache.get("client_secret", "")

    oauth_js = _locate_gemini_cli_oauth_js()
    if oauth_js is None:
        _scraped_creds_cache["resolved"] = "1"  # 不要每次调用都重试
        return "", ""

    try:
        content = oauth_js.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.debug("Failed to read oauth2.js at %s: %s", oauth_js, exc)
        _scraped_creds_cache["resolved"] = "1"
        return "", ""

    # 先尝试精确模式匹配，再回退到形状匹配
    cid_match = _CLIENT_ID_PATTERN.search(content) or _CLIENT_ID_SHAPE.search(content)
    cs_match = _CLIENT_SECRET_PATTERN.search(content) or _CLIENT_SECRET_SHAPE.search(content)

    client_id = cid_match.group(1) if cid_match else ""
    client_secret = cs_match.group(1) if cs_match else ""

    _scraped_creds_cache["client_id"] = client_id
    _scraped_creds_cache["client_secret"] = client_secret
    _scraped_creds_cache["resolved"] = "1"

    if client_id:
        logger.info("Scraped Gemini OAuth client from %s", oauth_js)

    return client_id, client_secret


def _get_client_id() -> str:
    env_val = (os.getenv(ENV_CLIENT_ID) or "").strip()
    if env_val:
        return env_val
    if _DEFAULT_CLIENT_ID:
        return _DEFAULT_CLIENT_ID
    scraped, _ = _scrape_client_credentials()
    return scraped


def _get_client_secret() -> str:
    env_val = (os.getenv(ENV_CLIENT_SECRET) or "").strip()
    if env_val:
        return env_val
    if _DEFAULT_CLIENT_SECRET:
        return _DEFAULT_CLIENT_SECRET
    _, scraped = _scrape_client_credentials()
    return scraped


def _require_client_id() -> str:
    cid = _get_client_id()
    if not cid:
        raise GoogleOAuthError(
            "Google OAuth client ID is not available.\n"
            "Hermes looks for a locally installed gemini-cli to source the OAuth client. "
            "Either:\n"
            "  1. Install it: npm install -g @google/gemini-cli  (or brew install gemini-cli)\n"
            "  2. Set HERMES_GEMINI_CLIENT_ID and HERMES_GEMINI_CLIENT_SECRET in ~/.hermes/.env\n"
            "\n"
            "Register a Desktop OAuth client at:\n"
            "  https://console.cloud.google.com/apis/credentials\n"
            "(enable the Generative Language API on the project).",
            code="google_oauth_client_id_missing",
        )
    return cid


# =============================================================================
# PKCE 密钥对生成
# =============================================================================

def _generate_pkce_pair() -> Tuple[str, str]:
    """使用 S256 算法生成 (verifier, challenge) 密钥对。"""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


# =============================================================================
# 打包刷新格式：refresh_token[|project_id[|managed_project_id]]
# =============================================================================

@dataclass
class RefreshParts:
    refresh_token: str
    project_id: str = ""
    managed_project_id: str = ""

    @classmethod
    def parse(cls, packed: str) -> "RefreshParts":
        if not packed:
            return cls(refresh_token="")
        parts = packed.split("|", 2)
        return cls(
            refresh_token=parts[0],
            project_id=parts[1] if len(parts) > 1 else "",
            managed_project_id=parts[2] if len(parts) > 2 else "",
        )

    def format(self) -> str:
        if not self.refresh_token:
            return ""
        if not self.project_id and not self.managed_project_id:
            return self.refresh_token
        return f"{self.refresh_token}|{self.project_id}|{self.managed_project_id}"


# =============================================================================
# 凭据（包装磁盘格式的数据类）
# =============================================================================

@dataclass
class GoogleCredentials:
    access_token: str
    refresh_token: str
    expires_ms: int  # unix 毫秒时间戳
    email: str = ""
    project_id: str = ""
    managed_project_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "refresh": RefreshParts(
                refresh_token=self.refresh_token,
                project_id=self.project_id,
                managed_project_id=self.managed_project_id,
            ).format(),
            "access": self.access_token,
            "expires": int(self.expires_ms),
            "email": self.email,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GoogleCredentials":
        refresh_packed = str(data.get("refresh", "") or "")
        parts = RefreshParts.parse(refresh_packed)
        return cls(
            access_token=str(data.get("access", "") or ""),
            refresh_token=parts.refresh_token,
            expires_ms=int(data.get("expires", 0) or 0),
            email=str(data.get("email", "") or ""),
            project_id=parts.project_id,
            managed_project_id=parts.managed_project_id,
        )

    def expires_unix_seconds(self) -> float:
        return self.expires_ms / 1000.0

    def access_token_expired(self, skew_seconds: int = REFRESH_SKEW_SECONDS) -> bool:
        if not self.access_token or not self.expires_ms:
            return True
        return (time.time() + max(0, skew_seconds)) * 1000 >= self.expires_ms


# =============================================================================
# 凭据读写（原子 + 加锁）
# =============================================================================

def load_credentials() -> Optional[GoogleCredentials]:
    """从磁盘加载凭据。如果缺失或损坏则返回 None。"""
    path = _credentials_path()
    if not path.exists():
        return None
    try:
        with _credentials_lock():
            raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (json.JSONDecodeError, OSError, IOError) as exc:
        logger.warning("Failed to read Google OAuth credentials at %s: %s", path, exc)
        return None
    if not isinstance(data, dict):
        return None
    creds = GoogleCredentials.from_dict(data)
    if not creds.access_token:
        return None
    return creds


def save_credentials(creds: GoogleCredentials) -> Path:
    """原子写入凭据到磁盘，权限 0o600。"""
    path = _credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(creds.to_dict(), indent=2, sort_keys=True) + "\n"

    with _credentials_lock():
        tmp_path = path.with_suffix(f".tmp.{os.getpid()}.{secrets.token_hex(4)}")
        try:
            with open(tmp_path, "w", encoding="utf-8") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
            os.chmod(tmp_path, stat.S_IRUSR | stat.S_IWUSR)
            os.replace(tmp_path, path)
        finally:
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except OSError:
                pass
    return path


def clear_credentials() -> None:
    """删除凭据文件。幂等操作。"""
    path = _credentials_path()
    with _credentials_lock():
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.warning("Failed to remove Google OAuth credentials at %s: %s", path, exc)


# =============================================================================
# HTTP 辅助函数
# =============================================================================

def _post_form(url: str, data: Dict[str, str], timeout: float) -> Dict[str, Any]:
    """发送 x-www-form-urlencoded POST 请求并返回解析后的 JSON 响应。"""
    body = urllib.parse.urlencode(data).encode("ascii")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return json.loads(raw)
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        # 检测 invalid_grant 以指示凭据已被撤销
        code = "google_oauth_token_http_error"
        if "invalid_grant" in detail.lower():
            code = "google_oauth_invalid_grant"
        raise GoogleOAuthError(
            f"Google OAuth token endpoint returned HTTP {exc.code}: {detail or exc.reason}",
            code=code,
        ) from exc
    except urllib.error.URLError as exc:
        raise GoogleOAuthError(
            f"Google OAuth token request failed: {exc}",
            code="google_oauth_token_network_error",
        ) from exc


def exchange_code(
    code: str,
    verifier: str,
    redirect_uri: str,
    *,
    client_id: Optional[str] = None,
    client_secret: Optional[str] = None,
    timeout: float = TOKEN_REQUEST_TIMEOUT_SECONDS,
) -> Dict[str, Any]:
    """用授权码交换访问令牌 + 刷新令牌。"""
    cid = client_id if client_id is not None else _get_client_id()
    csecret = client_secret if client_secret is not None else _get_client_secret()
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "code_verifier": verifier,
        "client_id": cid,
        "redirect_uri": redirect_uri,
    }
    if csecret:
        data["client_secret"] = csecret
    return _post_form(TOKEN_ENDPOINT, data, timeout)


def refresh_access_token(
    refresh_token: str,
    *,
    client_id: Optional[str] = None,
    client_secret: Optional[str] = None,
    timeout: float = TOKEN_REQUEST_TIMEOUT_SECONDS,
) -> Dict[str, Any]:
    """刷新访问令牌。"""
    if not refresh_token:
        raise GoogleOAuthError(
            "Cannot refresh: refresh_token is empty. Re-run OAuth login.",
            code="google_oauth_refresh_token_missing",
        )
    cid = client_id if client_id is not None else _get_client_id()
    csecret = client_secret if client_secret is not None else _get_client_secret()
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": cid,
    }
    if csecret:
        data["client_secret"] = csecret
    return _post_form(TOKEN_ENDPOINT, data, timeout)


def _fetch_user_email(access_token: str, timeout: float = TOKEN_REQUEST_TIMEOUT_SECONDS) -> str:
    """尽力获取用户信息用于显示。失败时返回空字符串。"""
    try:
        request = urllib.request.Request(
            USERINFO_ENDPOINT + "?alt=json",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
        data = json.loads(raw)
        return str(data.get("email", "") or "")
    except Exception as exc:
        logger.debug("Userinfo fetch failed (non-fatal): %s", exc)
        return ""


# =============================================================================
# 并发刷新去重
# =============================================================================

_refresh_inflight: Dict[str, threading.Event] = {}
_refresh_inflight_lock = threading.Lock()


def get_valid_access_token(*, force_refresh: bool = False) -> str:
    """加载凭据，如果接近过期则刷新，返回有效的 bearer 令牌。

    通过 refresh_token 去重并发刷新。当遇到 ``invalid_grant`` 时，
    清除凭据文件并抛出 ``google_oauth_invalid_grant`` 错误
    （调用方应触发重新登录流程）。
    """
    creds = load_credentials()
    if creds is None:
        raise GoogleOAuthError(
            "No Google OAuth credentials found. Run `hermes login --provider google-gemini-cli` first.",
            code="google_oauth_not_logged_in",
        )

    if not force_refresh and not creds.access_token_expired():
        return creds.access_token

    # 通过 refresh_token 去重并发刷新
    rt = creds.refresh_token
    with _refresh_inflight_lock:
        event = _refresh_inflight.get(rt)
        if event is None:
            event = threading.Event()
            _refresh_inflight[rt] = event
            owner = True
        else:
            owner = False

    if not owner:
        # 另一个线程正在刷新——等待，然后从磁盘重新读取。
        event.wait(timeout=LOCK_TIMEOUT_SECONDS)
        fresh = load_credentials()
        if fresh is not None and not fresh.access_token_expired():
            return fresh.access_token
        # 如果其他尝试失败，则继续执行自己的刷新

    try:
        try:
            resp = refresh_access_token(rt)
        except GoogleOAuthError as exc:
            if exc.code == "google_oauth_invalid_grant":
                logger.warning(
                    "Google OAuth refresh token invalid (revoked/expired). "
                    "Clearing credentials at %s — user must re-login.",
                    _credentials_path(),
                )
                clear_credentials()
            raise

        new_access = str(resp.get("access_token", "") or "").strip()
        if not new_access:
            raise GoogleOAuthError(
                "Refresh response did not include an access_token.",
                code="google_oauth_refresh_empty",
            )
        # Google 有时会轮换 refresh_token；如果省略则保留现有的。
        new_refresh = str(resp.get("refresh_token", "") or "").strip() or creds.refresh_token
        expires_in = int(resp.get("expires_in", 0) or 0)

        creds.access_token = new_access
        creds.refresh_token = new_refresh
        creds.expires_ms = int((time.time() + max(60, expires_in)) * 1000)
        save_credentials(creds)
        return creds.access_token
    finally:
        if owner:
            with _refresh_inflight_lock:
                _refresh_inflight.pop(rt, None)
            event.set()


# =============================================================================
# 更新存储凭据中的项目 ID
# =============================================================================

def update_project_ids(project_id: str = "", managed_project_id: str = "") -> None:
    """将已解析/发现的项目 ID 持久化回凭据文件。"""
    creds = load_credentials()
    if creds is None:
        return
    if project_id:
        creds.project_id = project_id
    if managed_project_id:
        creds.managed_project_id = managed_project_id
    save_credentials(creds)


# =============================================================================
# 回调服务器
# =============================================================================

class _OAuthCallbackHandler(http.server.BaseHTTPRequestHandler):
    expected_state: str = ""
    captured_code: Optional[str] = None
    captured_error: Optional[str] = None
    ready: Optional[threading.Event] = None

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002, N802
        logger.debug("OAuth callback: " + format, *args)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != CALLBACK_PATH:
            self.send_response(404)
            self.end_headers()
            return

        params = urllib.parse.parse_qs(parsed.query)
        state = (params.get("state") or [""])[0]
        error = (params.get("error") or [""])[0]
        code = (params.get("code") or [""])[0]

        if state != type(self).expected_state:
            type(self).captured_error = "state_mismatch"
            self._respond_html(400, _ERROR_PAGE.format(message="State mismatch — aborting for safety."))
        elif error:
            type(self).captured_error = error
            # 对错误值进行简单 HTML 转义
            safe_err = (
                str(error)
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
            )
            self._respond_html(400, _ERROR_PAGE.format(message=f"Authorization denied: {safe_err}"))
        elif code:
            type(self).captured_code = code
            self._respond_html(200, _SUCCESS_PAGE)
        else:
            type(self).captured_error = "no_code"
            self._respond_html(400, _ERROR_PAGE.format(message="Callback received no authorization code."))

        if type(self).ready is not None:
            type(self).ready.set()

    def _respond_html(self, status: int, body: str) -> None:
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


_SUCCESS_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Hermes — signed in</title>
<style>
body { font: 16px/1.5 system-ui, sans-serif; margin: 10vh auto; max-width: 32rem; text-align: center; color: #222; }
h1 { color: #1a7f37; } p { color: #555; }
</style></head>
<body><h1>Signed in to Google.</h1>
<p>You can close this tab and return to your terminal.</p></body></html>
"""

_ERROR_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Hermes — sign-in failed</title>
<style>
body {{ font: 16px/1.5 system-ui, sans-serif; margin: 10vh auto; max-width: 32rem; text-align: center; color: #222; }}
h1 {{ color: #b42318; }} p {{ color: #555; }}
</style></head>
<body><h1>Sign-in failed</h1><p>{message}</p>
<p>Return to your terminal — Hermes will walk you through a manual paste fallback.</p></body></html>
"""


def _bind_callback_server(preferred_port: int = DEFAULT_REDIRECT_PORT) -> Tuple[http.server.HTTPServer, int]:
    try:
        server = http.server.HTTPServer((REDIRECT_HOST, preferred_port), _OAuthCallbackHandler)
        return server, preferred_port
    except OSError as exc:
        logger.info(
            "Preferred OAuth callback port %d unavailable (%s); requesting ephemeral port",
            preferred_port, exc,
        )
    server = http.server.HTTPServer((REDIRECT_HOST, 0), _OAuthCallbackHandler)
    return server, server.server_address[1]


def _is_headless() -> bool:
    return any(os.getenv(k) for k in _HEADLESS_ENV_VARS)


# =============================================================================
# 主登录流程
# =============================================================================

def start_oauth_flow(
    *,
    force_relogin: bool = False,
    open_browser: bool = True,
    callback_wait_seconds: float = CALLBACK_WAIT_SECONDS,
    project_id: str = "",
) -> GoogleCredentials:
    """运行交互式浏览器 OAuth 流程并持久化凭据。

    参数:
        force_relogin: 如果为 False 且已存在有效凭据，直接返回。
        open_browser: 如果为 False，跳过 webbrowser.open 仅打印 URL。
        callback_wait_seconds: 等待浏览器回调的最大秒数。
        project_id: 初始 GCP 项目 ID，会写入存储的凭据中。
                    可以稍后通过 update_project_ids() 发现/更新。
    """
    if not force_relogin:
        existing = load_credentials()
        if existing and existing.access_token:
            logger.info("Google OAuth credentials already present; skipping login.")
            return existing

    client_id = _require_client_id()  # 如果缺失则抛出带安装提示的 GoogleOAuthError
    client_secret = _get_client_secret()

    verifier, challenge = _generate_pkce_pair()
    state = secrets.token_urlsafe(16)

    # 如果是无头环境，跳过监听器直接进入粘贴模式
    if _is_headless() and open_browser:
        logger.info("Headless environment detected; using paste-mode OAuth fallback.")
        return _paste_mode_login(verifier, challenge, state, client_id, client_secret, project_id)

    server, port = _bind_callback_server(DEFAULT_REDIRECT_PORT)
    redirect_uri = f"http://{REDIRECT_HOST}:{port}{CALLBACK_PATH}"

    _OAuthCallbackHandler.expected_state = state
    _OAuthCallbackHandler.captured_code = None
    _OAuthCallbackHandler.captured_error = None
    ready = threading.Event()
    _OAuthCallbackHandler.ready = ready

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": OAUTH_SCOPES,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "access_type": "offline",
        "prompt": "consent",
    }
    auth_url = AUTH_ENDPOINT + "?" + urllib.parse.urlencode(params) + "#hermes"

    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    print()
    print("Opening your browser to sign in to Google…")
    print(f"If it does not open automatically, visit:\n  {auth_url}")
    print()

    if open_browser:
        try:
            import webbrowser

            webbrowser.open(auth_url, new=1, autoraise=True)
        except Exception as exc:
            logger.debug("webbrowser.open failed: %s", exc)

    code: Optional[str] = None
    try:
        if ready.wait(timeout=callback_wait_seconds):
            code = _OAuthCallbackHandler.captured_code
            error = _OAuthCallbackHandler.captured_error
            if error:
                raise GoogleOAuthError(
                    f"Authorization failed: {error}",
                    code="google_oauth_authorization_failed",
                )
        else:
            logger.info("Callback server timed out — offering manual paste fallback.")
            code = _prompt_paste_fallback()
    finally:
        try:
            server.shutdown()
        except Exception:
            pass
        try:
            server.server_close()
        except Exception:
            pass
        server_thread.join(timeout=2.0)

    if not code:
        raise GoogleOAuthError(
            "No authorization code received. Aborting.",
            code="google_oauth_no_code",
        )

    token_resp = exchange_code(
        code, verifier, redirect_uri,
        client_id=client_id, client_secret=client_secret,
    )
    return _persist_token_response(token_resp, project_id=project_id)


def _paste_mode_login(
    verifier: str,
    challenge: str,
    state: str,
    client_id: str,
    client_secret: str,
    project_id: str,
) -> GoogleCredentials:
    """不使用本地回调服务器运行 OAuth 流程。"""
    # 使用占位 redirect URI；用户会将完整 URL 粘贴回来
    redirect_uri = f"http://{REDIRECT_HOST}:{DEFAULT_REDIRECT_PORT}{CALLBACK_PATH}"
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": OAUTH_SCOPES,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "access_type": "offline",
        "prompt": "consent",
    }
    auth_url = AUTH_ENDPOINT + "?" + urllib.parse.urlencode(params) + "#hermes"

    print()
    print("Open this URL in a browser on any device:")
    print(f"  {auth_url}")
    print()
    print("After signing in, Google will redirect to localhost (which won't load).")
    print("Copy the full URL from your browser and paste it below.")
    print()

    code = _prompt_paste_fallback()
    if not code:
        raise GoogleOAuthError("No authorization code provided.", code="google_oauth_no_code")

    token_resp = exchange_code(
        code, verifier, redirect_uri,
        client_id=client_id, client_secret=client_secret,
    )
    return _persist_token_response(token_resp, project_id=project_id)


def _prompt_paste_fallback() -> Optional[str]:
    print()
    print("Paste the full redirect URL Google showed you, OR just the 'code=' parameter value.")
    raw = input("Callback URL or code: ").strip()
    if not raw:
        return None
    if raw.startswith("http://") or raw.startswith("https://"):
        parsed = urllib.parse.urlparse(raw)
        params = urllib.parse.parse_qs(parsed.query)
        return (params.get("code") or [""])[0] or None
    # 也接受裸查询字符串
    if raw.startswith("?"):
        params = urllib.parse.parse_qs(raw[1:])
        return (params.get("code") or [""])[0] or None
    return raw


def _persist_token_response(
    token_resp: Dict[str, Any],
    *,
    project_id: str = "",
) -> GoogleCredentials:
    access_token = str(token_resp.get("access_token", "") or "").strip()
    refresh_token = str(token_resp.get("refresh_token", "") or "").strip()
    expires_in = int(token_resp.get("expires_in", 0) or 0)
    if not access_token or not refresh_token:
        raise GoogleOAuthError(
            "Google token response missing access_token or refresh_token.",
            code="google_oauth_incomplete_token_response",
        )
    creds = GoogleCredentials(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_ms=int((time.time() + max(60, expires_in)) * 1000),
        email=_fetch_user_email(access_token),
        project_id=project_id,
        managed_project_id="",
    )
    save_credentials(creds)
    logger.info("Google OAuth credentials saved to %s", _credentials_path())
    return creds


# =============================================================================
# 连接池兼容变体
# =============================================================================

def run_gemini_oauth_login_pure() -> Dict[str, Any]:
    """运行登录流程并返回匹配凭据池结构的字典。"""
    creds = start_oauth_flow(force_relogin=True)
    return {
        "access_token": creds.access_token,
        "refresh_token": creds.refresh_token,
        "expires_at_ms": creds.expires_ms,
        "email": creds.email,
        "project_id": creds.project_id,
    }


# =============================================================================
# 项目 ID 解析
# =============================================================================

def resolve_project_id_from_env() -> str:
    """按优先级从环境变量返回 GCP 项目 ID。"""
    for var in (
        "HERMES_GEMINI_PROJECT_ID",
        "GOOGLE_CLOUD_PROJECT",
        "GOOGLE_CLOUD_PROJECT_ID",
    ):
        val = (os.getenv(var) or "").strip()
        if val:
            return val
    return ""
