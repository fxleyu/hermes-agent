#!/usr/bin/env python3
"""每服务器 MCP OAuth 状态的中央管理器。

整个进程共享一个实例。持有每服务器的 OAuth 提供者实例并协调：

- **跨进程令牌重新加载**：通过基于 mtime 的磁盘监视。当外部进程
  （例如用户的 cron 任务）在磁盘上刷新令牌时，下次认证流程会自动
  获取它们，无需重启进程。
- **401 去重**：通过进行中的 future。当 N 个并发工具调用都使用
  相同的 access_token 命中 401 时，只有一次恢复尝试会触发；
  其余调用等待同一个结果。
- **重连信号**：用于长期运行的 MCP 会话。管理器本身不驱动重连——
  `mcp_tool.py` 中的 `MCPServerTask` 负责——但管理器是决定何时
  需要重连的唯一真实来源。

替代了之前分散在 `mcp_oauth.py`、`mcp_tool.py` 和
`hermes_cli/mcp_config.py` 八个调用点中的代码。本模块是唯一
实例化 MCP SDK 的 `OAuthClientProvider` 的地方——所有其他代码
路径都通过 `get_manager()`。

设计参考：

- Claude Code 的 ``invalidateOAuthCacheIfDiskChanged``
  (``claude-code/src/utils/auth.ts:1320``，CC-1096 / GH#24317)。
  相同的外部刷新过期 bug 类别。
- Codex 的 ``refresh_oauth_if_needed`` / ``persist_if_needed``
  (``codex-rs/rmcp-client/src/rmcp_client.rs:805``)。我们依赖
  MCP SDK 的延迟刷新，而非在每次操作前调用刷新，因为每次工具调用
  一个 ``stat()`` 比一次 ``await`` + 潜在的刷新往返更便宜，
  而且 SDK 的内存过期路径已经是正确的。
"""

from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 每服务器条目
# ---------------------------------------------------------------------------


@dataclass
class _ProviderEntry:
    """管理器跟踪的每服务器 OAuth 状态。

    字段:
        server_url: 用于构建提供者的 MCP 服务器 URL。跟踪此字段
            以便在 URL 变化时丢弃缓存的提供者。
        oauth_config: 来自 ``mcp_servers.<name>.oauth`` 的可选字典。
        provider: 包装 MCP SDK 的兼容 ``httpx.Auth`` 的提供者。
            首次使用前为 None。
        last_mtime_ns: 磁盘令牌文件最后看到的 ``st_mtime_ns``。
            未读取时为零。由 :meth:`MCPOAuthManager.invalidate_if_disk_changed`
            用于检测外部刷新。
        lock: 串行化对此条目状态的并发访问。绑定到首次等待它的
            asyncio 循环（MCP 事件循环）。
        pending_401: 以失败的 access_token 为键的进行中 401 处理器
            future，用于去重雷群效应 401。镜像 Claude Code 的
            ``pending401Handlers`` 映射。
    """

    server_url: str
    oauth_config: Optional[dict]
    provider: Optional[Any] = None
    last_mtime_ns: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    pending_401: dict[str, "asyncio.Future[bool]"] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# HermesMCPOAuthProvider — 带磁盘监视的 OAuthClientProvider 子类
# ---------------------------------------------------------------------------


def _make_hermes_provider_class() -> Optional[type]:
    """延迟导入 SDK 基类并返回我们的子类。

    包装在函数中，以便即使 MCP SDK 的 OAuth 模块不可用
    （例如旧版 mcp），本模块也能正常导入。
    """
    try:
        from mcp.client.auth.oauth2 import OAuthClientProvider
    except ImportError:  # pragma: no cover — SDK required in CI
        return None

    class HermesMCPOAuthProvider(OAuthClientProvider):
        """带预流程磁盘 mtime 重载的 OAuthClientProvider。

        在每次 ``async_auth_flow`` 调用之前，请求管理器检查磁盘上的
        令牌文件是否被外部修改。如果是，管理器会重置 ``_initialized``，
        使下次流程从存储重新读取。

        这使得外部进程刷新（cron、另一个 CLI 实例）对正在运行的
        MCP 会话可见，无需重启。

        参考：Claude Code 的 ``invalidateOAuthCacheIfDiskChanged``
        (``src/utils/auth.ts:1320``，CC-1096 / GH#24317)。
        """

        def __init__(self, *args: Any, server_name: str = "", **kwargs: Any):
            super().__init__(*args, **kwargs)
            self._hermes_server_name = server_name

        async def async_auth_flow(self, request):  # type: ignore[override]
            # 预流程钩子：请求管理器在需要时从磁盘刷新。
            # 此处的任何失败都是非致命的——只记录日志并继续使用
            # SDK 已有的状态。
            try:
                await get_manager().invalidate_if_disk_changed(
                    self._hermes_server_name
                )
            except Exception as exc:  # pragma: no cover — defensive
                logger.debug(
                    "MCP OAuth '%s': pre-flow disk-watch failed (non-fatal): %s",
                    self._hermes_server_name, exc,
                )

            # 委托给 SDK 的认证流程
            async for item in super().async_auth_flow(request):
                yield item

    return HermesMCPOAuthProvider


# 在导入时缓存。由 :class:`MCPOAuthManager` 测试和使用。
_HERMES_PROVIDER_CLS: Optional[type] = _make_hermes_provider_class()


# ---------------------------------------------------------------------------
# 管理器
# ---------------------------------------------------------------------------


class MCPOAuthManager:
    """每服务器 MCP OAuth 状态的唯一真实来源。

    线程安全：``_entries`` 字典由 ``_entries_lock`` 保护以实现
    获取或创建语义。每个条目的状态由条目自己的 ``asyncio.Lock`` 保护
    （从 MCP 事件循环线程使用）。
    """

    def __init__(self) -> None:
        self._entries: dict[str, _ProviderEntry] = {}
        self._entries_lock = threading.Lock()

    # -- 提供者构造/缓存 -------------------------------------

    def get_or_build_provider(
        self,
        server_name: str,
        server_url: str,
        oauth_config: Optional[dict],
    ) -> Optional[Any]:
        """返回 ``server_name`` 的缓存 OAuth 提供者，或构建一个新的。

        幂等：使用相同名称的重复调用返回相同实例。
        如果某个名称的 ``server_url`` 发生变化，缓存的条目会被
        丢弃并构建新的提供者。

        如果 MCP SDK 的 OAuth 支持不可用，返回 None。
        """
        with self._entries_lock:
            entry = self._entries.get(server_name)
            if entry is not None and entry.server_url != server_url:
                logger.info(
                    "MCP OAuth '%s': URL changed from %s to %s, discarding cache",
                    server_name, entry.server_url, server_url,
                )
                entry = None

            if entry is None:
                entry = _ProviderEntry(
                    server_url=server_url,
                    oauth_config=oauth_config,
                )
                self._entries[server_name] = entry

            if entry.provider is None:
                entry.provider = self._build_provider(server_name, entry)

            return entry.provider

    def _build_provider(
        self,
        server_name: str,
        entry: _ProviderEntry,
    ) -> Optional[Any]:
        """构建底层 OAuth 提供者。

        使用从 ``tools.mcp_oauth`` 提取的辅助函数直接构造
        :class:`HermesMCPOAuthProvider`。子类注入了一个预流程
        磁盘监视钩子，使外部令牌刷新（cron、其他 CLI 实例）
        对运行中的 MCP 会话可见。

        如果 MCP SDK 的 OAuth 支持不可用，返回 None。
        """
        if _HERMES_PROVIDER_CLS is None:
            logger.warning(
                "MCP OAuth '%s': SDK auth module unavailable", server_name,
            )
            return None

        # 局部导入避免模块导入时的循环依赖。
        from tools.mcp_oauth import (
            HermesTokenStorage,
            _OAUTH_AVAILABLE,
            _build_client_metadata,
            _configure_callback_port,
            _is_interactive,
            _maybe_preregister_client,
            _parse_base_url,
            _redirect_handler,
            _wait_for_callback,
        )

        if not _OAUTH_AVAILABLE:
            return None

        cfg = dict(entry.oauth_config or {})
        storage = HermesTokenStorage(server_name)

        if not _is_interactive() and not storage.has_cached_tokens():
            logger.warning(
                "MCP OAuth for '%s': non-interactive environment and no "
                "cached tokens found. Run interactively first to complete "
                "initial authorization.",
                server_name,
            )

        _configure_callback_port(cfg)
        client_metadata = _build_client_metadata(cfg)
        _maybe_preregister_client(storage, cfg, client_metadata)

        return _HERMES_PROVIDER_CLS(
            server_name=server_name,
            server_url=_parse_base_url(entry.server_url),
            client_metadata=client_metadata,
            storage=storage,
            redirect_handler=_redirect_handler,
            callback_handler=_wait_for_callback,
            timeout=float(cfg.get("timeout", 300)),
        )

    def remove(self, server_name: str) -> None:
        """从缓存中驱逐提供者并从磁盘删除令牌。

        由 ``hermes mcp remove <name>`` 和（间接地）
        ``hermes mcp login <name>`` 在强制重新认证时调用。
        """
        with self._entries_lock:
            self._entries.pop(server_name, None)

        from tools.mcp_oauth import remove_oauth_tokens
        remove_oauth_tokens(server_name)
        logger.info(
            "MCP OAuth '%s': evicted from cache and removed from disk",
            server_name,
        )

    # -- 磁盘监视 ----------------------------------------------------------

    async def invalidate_if_disk_changed(self, server_name: str) -> bool:
        """如果磁盘上的令牌文件的 mtime 比最后看到的新，则强制
        MCP SDK 提供者重新加载其内存状态。

        如果缓存被无效化（mtime 不同），返回 True。这是外部刷新
        工作流的核心修复：cron 任务将新令牌写入磁盘，在下次工具调用时
        运行中的 MCP 会话会自动获取它们，无需重启。
        """
        from tools.mcp_oauth import _get_token_dir, _safe_filename

        entry = self._entries.get(server_name)
        if entry is None or entry.provider is None:
            return False

        async with entry.lock:
            tokens_path = _get_token_dir() / f"{_safe_filename(server_name)}.json"
            try:
                mtime_ns = tokens_path.stat().st_mtime_ns
            except (FileNotFoundError, OSError):
                return False

            if mtime_ns != entry.last_mtime_ns:
                old = entry.last_mtime_ns
                entry.last_mtime_ns = mtime_ns
                # 强制 SDK 的 OAuthClientProvider 在下次认证流程中
                # 从存储重新加载。`_initialized` 是私有 API，但在我们
                # 固定的 MCP SDK 版本（>=1.26.0）中是稳定的。
                if hasattr(entry.provider, "_initialized"):
                    entry.provider._initialized = False  # noqa: SLF001
                logger.info(
                    "MCP OAuth '%s': tokens file changed (mtime %d -> %d), "
                    "forcing reload",
                    server_name, old, mtime_ns,
                )
                return True
            return False

    # -- 401 处理器（去重） -----------------------------------------------

    async def handle_401(
        self,
        server_name: str,
        failed_access_token: Optional[str] = None,
    ) -> bool:
        """处理工具调用的 401 响应，在并发调用者之间去重。

        返回:
            True  表示现在有（可能是新的）access token 可用——调用方
                  应触发重连并重试操作。
            False 表示没有恢复路径——调用方应向模型呈现 ``needs_reauth``
                  错误，使其停止幻想手动刷新尝试。

        雷群保护：如果 N 个并发工具调用使用相同的 ``failed_access_token``
        命中 401，只有一次恢复尝试会触发。其他调用等待同一个 future。
        """
        entry = self._entries.get(server_name)
        if entry is None or entry.provider is None:
            return False

        key = failed_access_token or "<unknown>"
        loop = asyncio.get_running_loop()

        async with entry.lock:
            pending = entry.pending_401.get(key)
            if pending is None:
                pending = loop.create_future()
                entry.pending_401[key] = pending

                async def _do_handle() -> None:
                    try:
                        # 步骤 1：磁盘是否有变化？获取外部刷新的令牌。
                        disk_changed = await self.invalidate_if_disk_changed(
                            server_name
                        )
                        if disk_changed:
                            if not pending.done():
                                pending.set_result(True)
                            return

                        # 步骤 2：磁盘无变化——如果 SDK 可以就地刷新，
                        # 让调用方重试。SDK 的 httpx.Auth 流程会在下次
                        # 请求时发起刷新。
                        provider = entry.provider
                        ctx = getattr(provider, "context", None)
                        can_refresh = False
                        if ctx is not None:
                            can_refresh_fn = getattr(ctx, "can_refresh_token", None)
                            if callable(can_refresh_fn):
                                try:
                                    can_refresh = bool(can_refresh_fn())
                                except Exception:
                                    can_refresh = False
                        if not pending.done():
                            pending.set_result(can_refresh)
                    except Exception as exc:  # pragma: no cover — defensive
                        logger.warning(
                            "MCP OAuth '%s': 401 handler failed: %s",
                            server_name, exc,
                        )
                        if not pending.done():
                            pending.set_result(False)
                    finally:
                        entry.pending_401.pop(key, None)

                asyncio.create_task(_do_handle())

        try:
            return await pending
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning(
                "MCP OAuth '%s': awaiting 401 handler failed: %s",
                server_name, exc,
            )
            return False


# ---------------------------------------------------------------------------
# 模块级单例
# ---------------------------------------------------------------------------


_MANAGER: Optional[MCPOAuthManager] = None
_MANAGER_LOCK = threading.Lock()


def get_manager() -> MCPOAuthManager:
    """返回进程范围的 :class:`MCPOAuthManager` 单例。"""
    global _MANAGER
    with _MANAGER_LOCK:
        if _MANAGER is None:
            _MANAGER = MCPOAuthManager()
        return _MANAGER


def reset_manager_for_tests() -> None:
    """仅供测试的辅助函数：丢弃单例以便测试夹具从干净状态开始。"""
    global _MANAGER
    with _MANAGER_LOCK:
        _MANAGER = None
