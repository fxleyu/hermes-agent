"""ACP 代理服务器 -- 通过代理客户端协议暴露 Hermes Agent。"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Deque, Optional

import acp
from acp.schema import (
    AgentCapabilities,
    AuthenticateResponse,
    AvailableCommand,
    AvailableCommandsUpdate,
    ClientCapabilities,
    EmbeddedResourceContentBlock,
    ForkSessionResponse,
    ImageContentBlock,
    AudioContentBlock,
    Implementation,
    InitializeResponse,
    ListSessionsResponse,
    LoadSessionResponse,
    McpServerHttp,
    McpServerSse,
    McpServerStdio,
    NewSessionResponse,
    PromptResponse,
    ResumeSessionResponse,
    SetSessionConfigOptionResponse,
    SetSessionModelResponse,
    SetSessionModeResponse,
    ResourceContentBlock,
    SessionCapabilities,
    SessionForkCapabilities,
    SessionListCapabilities,
    SessionResumeCapabilities,
    SessionInfo,
    TextContentBlock,
    UnstructuredCommandInput,
    Usage,
)

# AuthMethodAgent 在 agent-client-protocol 0.9.0 中从 AuthMethod 重命名而来
try:
    from acp.schema import AuthMethodAgent
except ImportError:
    from acp.schema import AuthMethod as AuthMethodAgent  # type: ignore[attr-defined]

from acp_adapter.auth import detect_provider, has_provider
from acp_adapter.events import (
    make_message_cb,
    make_step_cb,
    make_thinking_cb,
    make_tool_progress_cb,
)
from acp_adapter.permissions import make_approval_callback
from acp_adapter.session import SessionManager, SessionState

logger = logging.getLogger(__name__)

try:
    from hermes_cli import __version__ as HERMES_VERSION
except Exception:
    HERMES_VERSION = "0.0.0"

# 用于并行运行 AIAgent（同步）的线程池。
_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="acp-agent")


def _extract_text(
    prompt: list[
        TextContentBlock
        | ImageContentBlock
        | AudioContentBlock
        | ResourceContentBlock
        | EmbeddedResourceContentBlock
    ],
) -> str:
    """从 ACP 内容块中提取纯文本。"""
    parts: list[str] = []
    for block in prompt:
        if isinstance(block, TextContentBlock):
            parts.append(block.text)
        elif hasattr(block, "text"):
            parts.append(str(block.text))
        # 非文本块目前被忽略。
    return "\n".join(parts)


class HermesACPAgent(acp.Agent):
    """包装 Hermes AIAgent 的 ACP Agent 实现。"""

    _SLASH_COMMANDS = {
        "help": "Show available commands",
        "model": "Show or change current model",
        "tools": "List available tools",
        "context": "Show conversation context info",
        "reset": "Clear conversation history",
        "compact": "Compress conversation context",
        "version": "Show Hermes version",
    }

    _ADVERTISED_COMMANDS = (
        {
            "name": "help",
            "description": "List available commands",
        },
        {
            "name": "model",
            "description": "Show current model and provider, or switch models",
            "input_hint": "model name to switch to",
        },
        {
            "name": "tools",
            "description": "List available tools with descriptions",
        },
        {
            "name": "context",
            "description": "Show conversation message counts by role",
        },
        {
            "name": "reset",
            "description": "Clear conversation history",
        },
        {
            "name": "compact",
            "description": "Compress conversation context",
        },
        {
            "name": "version",
            "description": "Show Hermes version",
        },
    )

    def __init__(self, session_manager: SessionManager | None = None):
        super().__init__()
        self.session_manager = session_manager or SessionManager()
        self._conn: Optional[acp.Client] = None

    # ---- 连接生命周期 -----------------------------------------------

    def on_connect(self, conn: acp.Client) -> None:
        """存储客户端连接，用于发送会话更新。"""
        self._conn = conn
        logger.info("ACP client connected")

    async def _register_session_mcp_servers(
        self,
        state: SessionState,
        mcp_servers: list[McpServerStdio | McpServerHttp | McpServerSse] | None,
    ) -> None:
        """注册 ACP 提供的 MCP 服务器并刷新代理工具面板。"""
        if not mcp_servers:
            return

        try:
            from tools.mcp_tool import register_mcp_servers

            config_map: dict[str, dict] = {}
            for server in mcp_servers:
                name = server.name
                if isinstance(server, McpServerStdio):
                    config = {
                        "command": server.command,
                        "args": list(server.args),
                        "env": {item.name: item.value for item in server.env},
                    }
                else:
                    config = {
                        "url": server.url,
                        "headers": {item.name: item.value for item in server.headers},
                    }
                config_map[name] = config

            await asyncio.to_thread(register_mcp_servers, config_map)
        except Exception:
            logger.warning(
                "Session %s: failed to register ACP MCP servers",
                state.session_id,
                exc_info=True,
            )
            return

        try:
            from model_tools import get_tool_definitions

            enabled_toolsets = getattr(state.agent, "enabled_toolsets", None) or ["hermes-acp"]
            disabled_toolsets = getattr(state.agent, "disabled_toolsets", None)
            state.agent.tools = get_tool_definitions(
                enabled_toolsets=enabled_toolsets,
                disabled_toolsets=disabled_toolsets,
                quiet_mode=True,
            )
            state.agent.valid_tool_names = {
                tool["function"]["name"] for tool in state.agent.tools or []
            }
            invalidate = getattr(state.agent, "_invalidate_system_prompt", None)
            if callable(invalidate):
                invalidate()
            logger.info(
                "Session %s: refreshed tool surface after ACP MCP registration (%d tools)",
                state.session_id,
                len(state.agent.tools or []),
            )
        except Exception:
            logger.warning(
                "Session %s: failed to refresh tool surface after ACP MCP registration",
                state.session_id,
                exc_info=True,
            )

    # ---- ACP 生命周期 ------------------------------------------------------

    async def initialize(
        self,
        protocol_version: int | None = None,
        client_capabilities: ClientCapabilities | None = None,
        client_info: Implementation | None = None,
        **kwargs: Any,
    ) -> InitializeResponse:
        resolved_protocol_version = (
            protocol_version if isinstance(protocol_version, int) else acp.PROTOCOL_VERSION
        )
        provider = detect_provider()
        auth_methods = None
        if provider:
            auth_methods = [
                AuthMethodAgent(
                    id=provider,
                    name=f"{provider} runtime credentials",
                    description=f"Authenticate Hermes using the currently configured {provider} runtime credentials.",
                )
            ]

        client_name = client_info.name if client_info else "unknown"
        logger.info(
            "Initialize from %s (protocol v%s)",
            client_name,
            resolved_protocol_version,
        )

        return InitializeResponse(
            protocol_version=acp.PROTOCOL_VERSION,
            agent_info=Implementation(name="hermes-agent", version=HERMES_VERSION),
            agent_capabilities=AgentCapabilities(
                load_session=True,
                session_capabilities=SessionCapabilities(
                    fork=SessionForkCapabilities(),
                    list=SessionListCapabilities(),
                    resume=SessionResumeCapabilities(),
                ),
            ),
            auth_methods=auth_methods,
        )

    async def authenticate(self, method_id: str, **kwargs: Any) -> AuthenticateResponse | None:
        if has_provider():
            return AuthenticateResponse()
        return None

    # ---- 会话管理 -------------------------------------------------

    async def new_session(
        self,
        cwd: str,
        mcp_servers: list | None = None,
        **kwargs: Any,
    ) -> NewSessionResponse:
        state = self.session_manager.create_session(cwd=cwd)
        await self._register_session_mcp_servers(state, mcp_servers)
        logger.info("New session %s (cwd=%s)", state.session_id, cwd)
        self._schedule_available_commands_update(state.session_id)
        return NewSessionResponse(session_id=state.session_id)

    async def load_session(
        self,
        cwd: str,
        session_id: str,
        mcp_servers: list | None = None,
        **kwargs: Any,
    ) -> LoadSessionResponse | None:
        state = self.session_manager.update_cwd(session_id, cwd)
        if state is None:
            logger.warning("load_session: session %s not found", session_id)
            return None
        await self._register_session_mcp_servers(state, mcp_servers)
        logger.info("Loaded session %s", session_id)
        self._schedule_available_commands_update(session_id)
        return LoadSessionResponse()

    async def resume_session(
        self,
        cwd: str,
        session_id: str,
        mcp_servers: list | None = None,
        **kwargs: Any,
    ) -> ResumeSessionResponse:
        state = self.session_manager.update_cwd(session_id, cwd)
        if state is None:
            logger.warning("resume_session: session %s not found, creating new", session_id)
            state = self.session_manager.create_session(cwd=cwd)
        await self._register_session_mcp_servers(state, mcp_servers)
        logger.info("Resumed session %s", state.session_id)
        self._schedule_available_commands_update(state.session_id)
        return ResumeSessionResponse()

    async def cancel(self, session_id: str, **kwargs: Any) -> None:
        state = self.session_manager.get_session(session_id)
        if state and state.cancel_event:
            state.cancel_event.set()
            try:
                if getattr(state, "agent", None) and hasattr(state.agent, "interrupt"):
                    state.agent.interrupt()
            except Exception:
                logger.debug("Failed to interrupt ACP session %s", session_id, exc_info=True)
            logger.info("Cancelled session %s", session_id)

    async def fork_session(
        self,
        cwd: str,
        session_id: str,
        mcp_servers: list | None = None,
        **kwargs: Any,
    ) -> ForkSessionResponse:
        state = self.session_manager.fork_session(session_id, cwd=cwd)
        new_id = state.session_id if state else ""
        if state is not None:
            await self._register_session_mcp_servers(state, mcp_servers)
        logger.info("Forked session %s -> %s", session_id, new_id)
        if new_id:
            self._schedule_available_commands_update(new_id)
        return ForkSessionResponse(session_id=new_id)

    async def list_sessions(
        self,
        cursor: str | None = None,
        cwd: str | None = None,
        **kwargs: Any,
    ) -> ListSessionsResponse:
        infos = self.session_manager.list_sessions()
        sessions = [
            SessionInfo(session_id=s["session_id"], cwd=s["cwd"])
            for s in infos
        ]
        return ListSessionsResponse(sessions=sessions)

    # ---- 提示处理（核心） ------------------------------------------------------

    async def prompt(
        self,
        prompt: list[
            TextContentBlock
            | ImageContentBlock
            | AudioContentBlock
            | ResourceContentBlock
            | EmbeddedResourceContentBlock
        ],
        session_id: str,
        **kwargs: Any,
    ) -> PromptResponse:
        """对用户提示运行 Hermes，并将事件流式返回给编辑器。"""
        state = self.session_manager.get_session(session_id)
        if state is None:
            logger.error("prompt: session %s not found", session_id)
            return PromptResponse(stop_reason="refusal")

        user_text = _extract_text(prompt).strip()
        if not user_text:
            return PromptResponse(stop_reason="end_turn")

        # 拦截斜杠命令 -- 在本地处理，不调用 LLM
        if user_text.startswith("/"):
            response_text = self._handle_slash_command(user_text, state)
            if response_text is not None:
                if self._conn:
                    update = acp.update_agent_message_text(response_text)
                    await self._conn.session_update(session_id, update)
                return PromptResponse(stop_reason="end_turn")

        logger.info("Prompt on session %s: %s", session_id, user_text[:100])

        conn = self._conn
        loop = asyncio.get_running_loop()

        if state.cancel_event:
            state.cancel_event.clear()

        tool_call_ids: dict[str, Deque[str]] = defaultdict(deque)
        previous_approval_cb = None

        if conn:
            tool_progress_cb = make_tool_progress_cb(conn, session_id, loop, tool_call_ids)
            thinking_cb = make_thinking_cb(conn, session_id, loop)
            step_cb = make_step_cb(conn, session_id, loop, tool_call_ids)
            message_cb = make_message_cb(conn, session_id, loop)
            approval_cb = make_approval_callback(conn.request_permission, loop, session_id)
        else:
            tool_progress_cb = None
            thinking_cb = None
            step_cb = None
            message_cb = None
            approval_cb = None

        agent = state.agent
        agent.tool_progress_callback = tool_progress_cb
        agent.thinking_callback = thinking_cb
        agent.step_callback = step_cb
        agent.message_callback = message_cb

        if approval_cb:
            try:
                from tools import terminal_tool as _terminal_tool
                previous_approval_cb = getattr(_terminal_tool, "_approval_callback", None)
                _terminal_tool.set_approval_callback(approval_cb)
            except Exception:
                logger.debug("Could not set ACP approval callback", exc_info=True)

        def _run_agent() -> dict:
            try:
                result = agent.run_conversation(
                    user_message=user_text,
                    conversation_history=state.history,
                    task_id=session_id,
                )
                return result
            except Exception as e:
                logger.exception("Agent error in session %s", session_id)
                return {"final_response": f"Error: {e}", "messages": state.history}
            finally:
                if approval_cb:
                    try:
                        from tools import terminal_tool as _terminal_tool
                        _terminal_tool.set_approval_callback(previous_approval_cb)
                    except Exception:
                        logger.debug("Could not restore approval callback", exc_info=True)

        try:
            result = await loop.run_in_executor(_executor, _run_agent)
        except Exception:
            logger.exception("Executor error for session %s", session_id)
            return PromptResponse(stop_reason="end_turn")

        if result.get("messages"):
            state.history = result["messages"]
            # 持久化更新后的历史记录，使会话在进程重启后存活。
            self.session_manager.save_session(session_id)

        final_response = result.get("final_response", "")
        if final_response and conn:
            update = acp.update_agent_message_text(final_response)
            await conn.session_update(session_id, update)

        usage = None
        if any(result.get(key) is not None for key in ("prompt_tokens", "completion_tokens", "total_tokens")):
            usage = Usage(
                input_tokens=result.get("prompt_tokens", 0),
                output_tokens=result.get("completion_tokens", 0),
                total_tokens=result.get("total_tokens", 0),
                thought_tokens=result.get("reasoning_tokens"),
                cached_read_tokens=result.get("cache_read_tokens"),
            )

        stop_reason = "cancelled" if state.cancel_event and state.cancel_event.is_set() else "end_turn"
        return PromptResponse(stop_reason=stop_reason, usage=usage)

    # ---- 斜杠命令（无头模式）-------------------------------------------

    @classmethod
    def _available_commands(cls) -> list[AvailableCommand]:
        commands: list[AvailableCommand] = []
        for spec in cls._ADVERTISED_COMMANDS:
            input_hint = spec.get("input_hint")
            commands.append(
                AvailableCommand(
                    name=spec["name"],
                    description=spec["description"],
                    input=UnstructuredCommandInput(hint=input_hint)
                    if input_hint
                    else None,
                )
            )
        return commands

    async def _send_available_commands_update(self, session_id: str) -> None:
        """向已连接的 ACP 客户端通告支持的斜杠命令。"""
        if not self._conn:
            return

        try:
            await self._conn.session_update(
                session_id=session_id,
                update=AvailableCommandsUpdate(
                    sessionUpdate="available_commands_update",
                    availableCommands=self._available_commands(),
                ),
            )
        except Exception:
            logger.warning(
                "Failed to advertise ACP slash commands for session %s",
                session_id,
                exc_info=True,
            )

    def _schedule_available_commands_update(self, session_id: str) -> None:
        """在会话响应排队后发送命令通告。"""
        if not self._conn:
            return
        loop = asyncio.get_running_loop()
        loop.call_soon(
            asyncio.create_task, self._send_available_commands_update(session_id)
        )

    def _handle_slash_command(self, text: str, state: SessionState) -> str | None:
        """分发斜杠命令并返回响应文本。

        对于未识别的命令返回 ``None``，使其直接传递给 LLM
        （用户可能将 ``/something`` 作为普通文本输入）。
        """
        parts = text.split(maxsplit=1)
        cmd = parts[0].lstrip("/").lower()
        args = parts[1].strip() if len(parts) > 1 else ""

        handler = {
            "help": self._cmd_help,
            "model": self._cmd_model,
            "tools": self._cmd_tools,
            "context": self._cmd_context,
            "reset": self._cmd_reset,
            "compact": self._cmd_compact,
            "version": self._cmd_version,
        }.get(cmd)

        if handler is None:
            return None  # 不是已知命令 -- 让 LLM 处理

        try:
            return handler(args, state)
        except Exception as e:
            logger.error("Slash command /%s error: %s", cmd, e, exc_info=True)
            return f"Error executing /{cmd}: {e}"

    def _cmd_help(self, args: str, state: SessionState) -> str:
        lines = ["Available commands:", ""]
        for cmd, desc in self._SLASH_COMMANDS.items():
            lines.append(f"  /{cmd:10s}  {desc}")
        lines.append("")
        lines.append("Unrecognized /commands are sent to the model as normal messages.")
        return "\n".join(lines)

    def _cmd_model(self, args: str, state: SessionState) -> str:
        if not args:
            model = state.model or getattr(state.agent, "model", "unknown")
            provider = getattr(state.agent, "provider", None) or "auto"
            return f"Current model: {model}\nProvider: {provider}"

        new_model = args.strip()
        target_provider = None
        current_provider = getattr(state.agent, "provider", None) or "openrouter"

        # 自动检测请求模型的提供商
        try:
            from hermes_cli.models import parse_model_input, detect_provider_for_model
            target_provider, new_model = parse_model_input(new_model, current_provider)
            if target_provider == current_provider:
                detected = detect_provider_for_model(new_model, current_provider)
                if detected:
                    target_provider, new_model = detected
        except Exception:
            logger.debug("Provider detection failed, using model as-is", exc_info=True)

        state.model = new_model
        state.agent = self.session_manager._make_agent(
            session_id=state.session_id,
            cwd=state.cwd,
            model=new_model,
            requested_provider=target_provider or current_provider,
        )
        self.session_manager.save_session(state.session_id)
        provider_label = getattr(state.agent, "provider", None) or target_provider or current_provider
        logger.info("Session %s: model switched to %s", state.session_id, new_model)
        return f"Model switched to: {new_model}\nProvider: {provider_label}"

    def _cmd_tools(self, args: str, state: SessionState) -> str:
        try:
            from model_tools import get_tool_definitions
            toolsets = getattr(state.agent, "enabled_toolsets", None) or ["hermes-acp"]
            tools = get_tool_definitions(enabled_toolsets=toolsets, quiet_mode=True)
            if not tools:
                return "No tools available."
            lines = [f"Available tools ({len(tools)}):"]
            for t in tools:
                name = t.get("function", {}).get("name", "?")
                desc = t.get("function", {}).get("description", "")
                # 截断过长的描述
                if len(desc) > 80:
                    desc = desc[:77] + "..."
                lines.append(f"  {name}: {desc}")
            return "\n".join(lines)
        except Exception as e:
            return f"Could not list tools: {e}"

    def _cmd_context(self, args: str, state: SessionState) -> str:
        n_messages = len(state.history)
        if n_messages == 0:
            return "Conversation is empty (no messages yet)."
        # 按角色统计
        roles: dict[str, int] = {}
        for msg in state.history:
            role = msg.get("role", "unknown")
            roles[role] = roles.get(role, 0) + 1
        lines = [
            f"Conversation: {n_messages} messages",
            f"  user: {roles.get('user', 0)}, assistant: {roles.get('assistant', 0)}, "
            f"tool: {roles.get('tool', 0)}, system: {roles.get('system', 0)}",
        ]
        model = state.model or getattr(state.agent, "model", "")
        if model:
            lines.append(f"Model: {model}")
        return "\n".join(lines)

    def _cmd_reset(self, args: str, state: SessionState) -> str:
        state.history.clear()
        self.session_manager.save_session(state.session_id)
        return "Conversation history cleared."

    def _cmd_compact(self, args: str, state: SessionState) -> str:
        if not state.history:
            return "Nothing to compress — conversation is empty."
        try:
            agent = state.agent
            if not getattr(agent, "compression_enabled", True):
                return "Context compression is disabled for this agent."
            if not hasattr(agent, "_compress_context"):
                return "Context compression not available for this agent."

            from agent.model_metadata import estimate_messages_tokens_rough

            original_count = len(state.history)
            approx_tokens = estimate_messages_tokens_rough(state.history)
            original_session_db = getattr(agent, "_session_db", None)

            try:
                # ACP sessions must keep a stable session id, so avoid the
                # SQLite session-splitting side effect inside _compress_context.
                agent._session_db = None
                compressed, _ = agent._compress_context(
                    state.history,
                    getattr(agent, "_cached_system_prompt", "") or "",
                    approx_tokens=approx_tokens,
                    task_id=state.session_id,
                )
            finally:
                agent._session_db = original_session_db

            state.history = compressed
            self.session_manager.save_session(state.session_id)

            new_count = len(state.history)
            new_tokens = estimate_messages_tokens_rough(state.history)
            return (
                f"Context compressed: {original_count} -> {new_count} messages\n"
                f"~{approx_tokens:,} -> ~{new_tokens:,} tokens"
            )
        except Exception as e:
            return f"Compression failed: {e}"

    def _cmd_version(self, args: str, state: SessionState) -> str:
        return f"Hermes Agent v{HERMES_VERSION}"

    # ---- 模型切换（ACP 协议方法）-------------------------------

    async def set_session_model(
        self, model_id: str, session_id: str, **kwargs: Any
    ) -> SetSessionModelResponse | None:
        """切换会话的模型（由 ACP 协议调用）。"""
        state = self.session_manager.get_session(session_id)
        if state:
            state.model = model_id
            current_provider = getattr(state.agent, "provider", None)
            current_base_url = getattr(state.agent, "base_url", None)
            current_api_mode = getattr(state.agent, "api_mode", None)
            state.agent = self.session_manager._make_agent(
                session_id=session_id,
                cwd=state.cwd,
                model=model_id,
                requested_provider=current_provider,
                base_url=current_base_url,
                api_mode=current_api_mode,
            )
            self.session_manager.save_session(session_id)
            logger.info("Session %s: model switched to %s", session_id, model_id)
            return SetSessionModelResponse()
        logger.warning("Session %s: model switch requested for missing session", session_id)
        return None

    async def set_session_mode(
        self, mode_id: str, session_id: str, **kwargs: Any
    ) -> SetSessionModeResponse | None:
        """持久化编辑器请求的模式，使 ACP 客户端在模式切换时不会失败。"""
        state = self.session_manager.get_session(session_id)
        if state is None:
            logger.warning("Session %s: mode switch requested for missing session", session_id)
            return None
        setattr(state, "mode", mode_id)
        self.session_manager.save_session(session_id)
        logger.info("Session %s: mode switched to %s", session_id, mode_id)
        return SetSessionModeResponse()

    async def set_config_option(
        self, config_id: str, session_id: str, value: str, **kwargs: Any
    ) -> SetSessionConfigOptionResponse | None:
        """接受 ACP 配置选项更新，即使 Hermes 尚未有类型化的 ACP 配置界面。"""
        state = self.session_manager.get_session(session_id)
        if state is None:
            logger.warning("Session %s: config update requested for missing session", session_id)
            return None

        options = getattr(state, "config_options", None)
        if not isinstance(options, dict):
            options = {}
        options[str(config_id)] = value
        setattr(state, "config_options", options)
        self.session_manager.save_session(session_id)
        logger.info("Session %s: config option %s updated", session_id, config_id)
        return SetSessionConfigOptionResponse(config_options=[])
