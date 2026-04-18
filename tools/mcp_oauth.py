#!/usr/bin/env python3
"""
MCP OAuth 2.1 客户端支持

为需要 OAuth 认证（而非静态持有者令牌）的 MCP 服务器实现基于浏览器的
OAuth 2.1 授权码流程（带 PKCE）。

使用 MCP Python SDK 的 ``OAuthClientProvider``（一个 ``httpx.Auth`` 子类），
自动处理发现、动态客户端注册、PKCE、令牌交换、刷新和升级授权。

本模块提供粘合层：
    - ``HermesTokenStorage``：将令牌/客户端信息持久化到磁盘，
      使其在进程重启后仍然有效。
    - 回调服务器：临时 localhost HTTP 服务器，用于捕获携带授权码的
      OAuth 重定向。
    - ``build_oauth_auth()``：由 ``mcp_tool.py`` 调用的入口点，
      将所有组件连接在一起并返回 ``httpx.Auth`` 对象。

config.yaml 中的配置::

    mcp_servers:
      my_server:
        url: "https://mcp.example.com/mcp"
        auth: oauth
        oauth:                                  # 所有字段均可选
          client_id: "pre-registered-id"        # 跳过动态注册
          client_secret: "secret"               # 仅限机密客户端
          scope: "read write"                   # 默认：服务器提供
          redirect_port: 0                      # 0 = 自动选择空闲端口
          client_name: "My Custom Client"       # 默认: "Hermes Agent"
"""

import asyncio
import json
import logging
import os
import re
import socket
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 延迟导入 -- 带 OAuth 支持的 MCP SDK 是可选的
# ---------------------------------------------------------------------------

_OAUTH_AVAILABLE = False
try:
    from mcp.client.auth import OAuthClientProvider
    from mcp.shared.auth import (
        OAuthClientInformationFull,
        OAuthClientMetadata,
        OAuthToken,
    )
    from pydantic import AnyUrl

    _OAUTH_AVAILABLE = True
except ImportError:
    logger.debug("MCP OAuth types not available -- OAuth MCP auth disabled")


# ---------------------------------------------------------------------------
# 异常类
# ---------------------------------------------------------------------------


class OAuthNonInteractiveError(RuntimeError):
    """在非交互式环境中 OAuth 需要浏览器交互时抛出。"""


# ---------------------------------------------------------------------------
# 模块级状态
# ---------------------------------------------------------------------------

# 最近一次 build_oauth_auth() 调用使用的端口。暴露出来以便测试可以
# 验证回调服务器和 redirect_uri 共享同一端口。
_oauth_port: int | None = None


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _get_token_dir() -> Path:
    """返回 MCP OAuth 令牌文件的目录。

    使用 HERMES_HOME，使每个配置文件拥有自己的 OAuth 令牌。
    布局：``HERMES_HOME/mcp-tokens/``
    """
    try:
        from hermes_constants import get_hermes_home
        base = Path(get_hermes_home())
    except ImportError:
        base = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))
    return base / "mcp-tokens"


def _safe_filename(name: str) -> str:
    """将服务器名称净化为可用作文件名的形式（不含路径分隔符）。"""
    return re.sub(r"[^\w\-]", "_", name).strip("_")[:128] or "default"


def _find_free_port() -> int:
    """在 localhost 上查找一个可用的 TCP 端口。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _is_interactive() -> bool:
    """如果可以合理地期望与用户进行交互，则返回 True。"""
    try:
        return sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


def _can_open_browser() -> bool:
    """如果打开浏览器很可能成功，则返回 True。"""
    # 显式 SSH 会话 → 没有本地显示器
    if os.environ.get("SSH_CLIENT") or os.environ.get("SSH_TTY"):
        return False
    # macOS 和 Windows 通常都有显示器
    if os.name == "nt":
        return True
    try:
        if os.uname().sysname == "Darwin":
            return True
    except AttributeError:
        pass
    # Linux/其他 posix：需要 DISPLAY 或 WAYLAND_DISPLAY
    if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
        return True
    return False


def _read_json(path: Path) -> dict | None:
    """读取 JSON 文件，如果不存在或无效则返回 None。"""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to read %s: %s", path, exc)
        return None


def _write_json(path: Path, data: dict) -> None:
    """以受限权限（0o600）将字典写入 JSON 文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        os.chmod(tmp, 0o600)
        tmp.rename(path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


# ---------------------------------------------------------------------------
# HermesTokenStorage -- 将令牌/客户端信息持久化到磁盘
# ---------------------------------------------------------------------------


class HermesTokenStorage:
    """将 OAuth 令牌和客户端注册信息持久化到 JSON 文件。

    文件布局::

        HERMES_HOME/mcp-tokens/<server_name>.json         -- 令牌
        HERMES_HOME/mcp-tokens/<server_name>.client.json   -- 客户端信息
    """

    def __init__(self, server_name: str):
        self._server_name = _safe_filename(server_name)

    def _tokens_path(self) -> Path:
        return _get_token_dir() / f"{self._server_name}.json"

    def _client_info_path(self) -> Path:
        return _get_token_dir() / f"{self._server_name}.client.json"

    # -- 令牌 ------------------------------------------------------------

    async def get_tokens(self) -> "OAuthToken | None":
        data = _read_json(self._tokens_path())
        if data is None:
            return None
        try:
            return OAuthToken.model_validate(data)
        except (ValueError, TypeError, KeyError) as exc:
            logger.warning("Corrupt tokens at %s -- ignoring: %s", self._tokens_path(), exc)
            return None

    async def set_tokens(self, tokens: "OAuthToken") -> None:
        _write_json(self._tokens_path(), tokens.model_dump(exclude_none=True))
        logger.debug("OAuth tokens saved for %s", self._server_name)

    # -- 客户端信息 -------------------------------------------------------

    async def get_client_info(self) -> "OAuthClientInformationFull | None":
        data = _read_json(self._client_info_path())
        if data is None:
            return None
        try:
            return OAuthClientInformationFull.model_validate(data)
        except (ValueError, TypeError, KeyError) as exc:
            logger.warning("Corrupt client info at %s -- ignoring: %s", self._client_info_path(), exc)
            return None

    async def set_client_info(self, client_info: "OAuthClientInformationFull") -> None:
        _write_json(self._client_info_path(), client_info.model_dump(exclude_none=True))
        logger.debug("OAuth client info saved for %s", self._server_name)

    # -- 清理 -----------------------------------------------------------

    def remove(self) -> None:
        """删除此服务器的所有已存储 OAuth 状态。"""
        for p in (self._tokens_path(), self._client_info_path()):
            p.unlink(missing_ok=True)

    def has_cached_tokens(self) -> bool:
        """如果磁盘上有令牌（可能已过期），返回 True。"""
        return self._tokens_path().exists()


# ---------------------------------------------------------------------------
# 回调处理器工厂 -- 每次调用获得自己的结果字典
# ---------------------------------------------------------------------------


def _make_callback_handler() -> tuple[type, dict]:
    """创建每次流程专用的回调 HTTP 处理器类及其结果字典。

    返回 ``(HandlerClass, result_dict)``，其中 *result_dict* 是一个可变字典，
    当 OAuth 重定向到达时处理器将 ``auth_code`` 和 ``state`` 写入其中。
    每次调用返回全新的一对，这样并发流程不会互相覆盖。
    """
    result: dict[str, Any] = {"auth_code": None, "state": None, "error": None}

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            params = parse_qs(urlparse(self.path).query)
            code = params.get("code", [None])[0]
            state = params.get("state", [None])[0]
            error = params.get("error", [None])[0]

            result["auth_code"] = code
            result["state"] = state
            result["error"] = error

            body = (
                "<html><body><h2>Authorization Successful</h2>"
                "<p>You can close this tab and return to Hermes.</p></body></html>"
            ) if code else (
                "<html><body><h2>Authorization Failed</h2>"
                f"<p>Error: {error or 'unknown'}</p></body></html>"
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body.encode())

        def log_message(self, fmt: str, *args: Any) -> None:
            logger.debug("OAuth callback: %s", fmt % args)

    return _Handler, result


# ---------------------------------------------------------------------------
# OAuthClientProvider 的异步重定向和回调处理器
# ---------------------------------------------------------------------------


async def _redirect_handler(authorization_url: str) -> None:
    """向用户展示授权 URL。

    在可能的情况下自动打开浏览器；始终打印 URL 作为
    无头/SSH/网关环境的后备方案。
    """
    msg = (
        f"\n  MCP OAuth: authorization required.\n"
        f"  Open this URL in your browser:\n\n"
        f"    {authorization_url}\n"
    )
    print(msg, file=sys.stderr)

    if _can_open_browser():
        try:
            opened = webbrowser.open(authorization_url)
            if opened:
                print("  (Browser opened automatically.)\n", file=sys.stderr)
            else:
                print("  (Could not open browser — please open the URL manually.)\n", file=sys.stderr)
        except Exception:
            print("  (Could not open browser — please open the URL manually.)\n", file=sys.stderr)
    else:
        print("  (Headless environment detected — open the URL manually.)\n", file=sys.stderr)


async def _wait_for_callback() -> tuple[str, str | None]:
    """等待本地回调服务器上的 OAuth 回调到达。

    使用模块级 ``_oauth_port``，该端口在此函数被调用之前由
    ``build_oauth_auth`` 设置。在不阻塞事件循环的情况下轮询结果。

    抛出:
        OAuthNonInteractiveError: 如果回调超时（没有用户在场
            完成浏览器授权）。
    """
    assert _oauth_port is not None, "OAuth callback port not set"

    # 回调服务器已经在运行（在 build_oauth_auth 中启动）。
    # 我们只需要轮询结果。
    handler_cls, result = _make_callback_handler()

    # 在已知端口上启动临时服务器
    try:
        server = HTTPServer(("127.0.0.1", _oauth_port), handler_cls)
    except OSError:
        # 端口已被占用——build_oauth_auth 的服务器正在运行。
        # 回退到轮询 build_oauth_auth 启动的服务器。
        raise OAuthNonInteractiveError(
            "OAuth callback timed out — could not bind callback port. "
            "Complete the authorization in a browser first, then retry."
        )

    server_thread = threading.Thread(target=server.handle_request, daemon=True)
    server_thread.start()

    timeout = 300.0
    poll_interval = 0.5
    elapsed = 0.0
    try:
        while elapsed < timeout:
            if result["auth_code"] is not None or result["error"] is not None:
                break
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval
    finally:
        server.server_close()

    if result["error"]:
        raise RuntimeError(f"OAuth authorization failed: {result['error']}")
    if result["auth_code"] is None:
        raise OAuthNonInteractiveError(
            "OAuth callback timed out — no authorization code received. "
            "Ensure you completed the browser authorization flow."
        )

    return result["auth_code"], result["state"]


# ---------------------------------------------------------------------------
# 公共 API
# ---------------------------------------------------------------------------


def remove_oauth_tokens(server_name: str) -> None:
    """删除指定服务器已存储的 OAuth 令牌和客户端信息。"""
    storage = HermesTokenStorage(server_name)
    storage.remove()
    logger.info("OAuth tokens removed for '%s'", server_name)


# ---------------------------------------------------------------------------
# 提取的辅助函数（MCP OAuth 整合的第 3 个任务）
#
# 这些函数组合成下面的 ``build_oauth_auth``，也被
# ``tools.mcp_oauth_manager.MCPOAuthManager._build_provider`` 使用，
# 使两条构造路径共享同一套实现。
# ---------------------------------------------------------------------------


def _configure_callback_port(cfg: dict) -> int:
    """选择或验证 OAuth 回调端口。

    将解析后的端口存储到 ``cfg['_resolved_port']``，以便同级辅助函数
    （和管理器）可以从同一字典中读取。返回解析后的端口。

    注意：也设置了遗留的模块级 ``_oauth_port``，使现有的
    ``_wait_for_callback`` 调用继续工作。遗留的全局变量是
    问题 #5344（并发 OAuth 流上的端口冲突）的根本原因；
    将其替换为 ContextVar 超出了本次整合 PR 的范围。
    """
    global _oauth_port
    requested = int(cfg.get("redirect_port", 0))
    port = _find_free_port() if requested == 0 else requested
    cfg["_resolved_port"] = port
    _oauth_port = port  # 遗留消费者：_wait_for_callback 读取此变量
    return port


def _build_client_metadata(cfg: dict) -> "OAuthClientMetadata":
    """从 oauth 配置字典构建 OAuthClientMetadata。

    要求 ``cfg['_resolved_port']`` 已由
    :func:`_configure_callback_port` 填充。
    """
    port = cfg.get("_resolved_port")
    if port is None:
        raise ValueError(
            "_configure_callback_port() must be called before _build_client_metadata()"
        )
    client_name = cfg.get("client_name", "Hermes Agent")
    scope = cfg.get("scope")
    redirect_uri = f"http://127.0.0.1:{port}/callback"

    metadata_kwargs: dict[str, Any] = {
        "client_name": client_name,
        "redirect_uris": [AnyUrl(redirect_uri)],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    }
    if scope:
        metadata_kwargs["scope"] = scope
    if cfg.get("client_secret"):
        metadata_kwargs["token_endpoint_auth_method"] = "client_secret_post"

    return OAuthClientMetadata.model_validate(metadata_kwargs)


def _maybe_preregister_client(
    storage: "HermesTokenStorage",
    cfg: dict,
    client_metadata: "OAuthClientMetadata",
) -> None:
    """如果 cfg 中有预注册的 client_id，将其持久化到存储中。"""
    client_id = cfg.get("client_id")
    if not client_id:
        return
    port = cfg["_resolved_port"]
    redirect_uri = f"http://127.0.0.1:{port}/callback"

    info_dict: dict[str, Any] = {
        "client_id": client_id,
        "redirect_uris": [redirect_uri],
        "grant_types": client_metadata.grant_types,
        "response_types": client_metadata.response_types,
        "token_endpoint_auth_method": client_metadata.token_endpoint_auth_method,
    }
    if cfg.get("client_secret"):
        info_dict["client_secret"] = cfg["client_secret"]
    if cfg.get("client_name"):
        info_dict["client_name"] = cfg["client_name"]
    if cfg.get("scope"):
        info_dict["scope"] = cfg["scope"]

    client_info = OAuthClientInformationFull.model_validate(info_dict)
    _write_json(storage._client_info_path(), client_info.model_dump(exclude_none=True))
    logger.debug("Pre-registered client_id=%s for '%s'", client_id, storage._server_name)


def _parse_base_url(server_url: str) -> str:
    """从服务器 URL 中去除路径部分，返回基础来源。"""
    parsed = urlparse(server_url)
    return f"{parsed.scheme}://{parsed.netloc}"


def build_oauth_auth(
    server_name: str,
    server_url: str,
    oauth_config: dict | None = None,
) -> "OAuthClientProvider | None":
    """为 MCP 服务器构建兼容 ``httpx.Auth`` 的 OAuth 处理器。

    保留公共 API 以向后兼容。新代码应使用
    :func:`tools.mcp_oauth_manager.get_manager`，以便 OAuth 状态在
    配置时、运行时和重连路径之间共享。

    参数:
        server_name: mcp_servers 配置中的服务器键（用于存储）。
        server_url: MCP 服务器端点 URL。
        oauth_config: config.yaml 中 ``oauth:`` 块的可选字典。

    返回:
        一个 ``OAuthClientProvider`` 实例，如果 MCP SDK 缺少 OAuth
        支持则返回 None。
    """
    if not _OAUTH_AVAILABLE:
        logger.warning(
            "MCP OAuth requested for '%s' but SDK auth types are not available. "
            "Install with: pip install 'mcp>=1.26.0'",
            server_name,
        )
        return None

    cfg = dict(oauth_config or {})  # 复制——我们会修改 _resolved_port
    storage = HermesTokenStorage(server_name)

    if not _is_interactive() and not storage.has_cached_tokens():
        logger.warning(
            "MCP OAuth for '%s': non-interactive environment and no cached tokens "
            "found. The OAuth flow requires browser authorization. Run "
            "interactively first to complete the initial authorization, then "
            "cached tokens will be reused.",
            server_name,
        )

    _configure_callback_port(cfg)
    client_metadata = _build_client_metadata(cfg)
    _maybe_preregister_client(storage, cfg, client_metadata)

    return OAuthClientProvider(
        server_url=_parse_base_url(server_url),
        client_metadata=client_metadata,
        storage=storage,
        redirect_handler=_redirect_handler,
        callback_handler=_wait_for_callback,
        timeout=float(cfg.get("timeout", 300)),
    )
