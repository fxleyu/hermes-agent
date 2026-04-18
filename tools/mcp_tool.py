#!/usr/bin/env python3
"""
MCP（模型上下文协议）客户端支持

通过 stdio 或 HTTP/StreamableHTTP 传输连接到外部 MCP 服务器，
发现其工具，并将它们注册到 hermes-agent 工具注册表中，
使代理可以像调用任何内置工具一样调用它们。

配置从 ~/.hermes/config.yaml 的 ``mcp_servers`` 键下读取。
``mcp`` Python 包是可选的——如果未安装，此模块是空操作
并记录调试消息。

配置示例::

    mcp_servers:
      filesystem:
        command: "npx"
        args: ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
        env: {}
        timeout: 120         # 每次工具调用超时（秒）（默认：120）
        connect_timeout: 60  # 初始连接超时（默认：60）
      github:
        command: "npx"
        args: ["-y", "@modelcontextprotocol/server-github"]
        env:
          GITHUB_PERSONAL_ACCESS_TOKEN: "ghp_..."
      remote_api:
        url: "https://my-mcp-server.example.com/mcp"
        headers:
          Authorization: "Bearer sk-..."
        timeout: 180
      analysis:
        command: "npx"
        args: ["-y", "analysis-server"]
        sampling:                    # 服务器发起的 LLM 请求
          enabled: true              # 默认：true
          model: "gemini-3-flash"    # 覆盖模型（可选）
          max_tokens_cap: 4096       # 每次请求的最大 token 数
          timeout: 30                # LLM 调用超时（秒）
          max_rpm: 10                # 每分钟最大请求数
          allowed_models: []         # 模型白名单（空 = 全部）
          max_tool_rounds: 5         # 工具循环限制（0 = 禁用）
          log_level: "info"          # 审计详细级别

功能：
    - Stdio 传输（command + args）和 HTTP/StreamableHTTP 传输（url）
    - 带指数退避的自动重连（最多 5 次重试）
    - stdio 子进程的环境变量过滤（安全性）
    - 返回给 LLM 的错误消息中的凭证剥离
    - 可配置的每服务器工具调用和连接超时
    - 带有专用后台事件循环的线程安全架构
    - Sampling 支持：MCP 服务器可通过 sampling/createMessage
      请求 LLM 补全（文本和工具使用响应）

架构：
    一个专用的后台事件循环（_mcp_loop）在守护线程中运行。
    每个 MCP 服务器作为长生命周期的 asyncio Task 在此循环上运行，
    保持其传输上下文存活。工具调用协程通过
    ``run_coroutine_threadsafe()`` 调度到循环上。

    关闭时，每个服务器 Task 被信号要求退出其 ``async with``
    块，确保 anyio 取消域清理发生在打开连接的*同一*
    Task 中（anyio 要求）。

线程安全：
    _servers 和 _mcp_loop/_mcp_thread 从 MCP 后台线程和
    调用者线程访问。所有修改都受 _lock 保护，因此代码无论
    GIL 是否存在（如 Python 3.13+ 自由线程）都是安全的。
"""

import asyncio
import concurrent.futures
import inspect
import json
import logging
import math
import os
import re
import shutil
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 优雅导入 -- MCP SDK 是可选依赖
# ---------------------------------------------------------------------------

_MCP_AVAILABLE = False
_MCP_HTTP_AVAILABLE = False
_MCP_SAMPLING_TYPES = False
_MCP_NOTIFICATION_TYPES = False
_MCP_MESSAGE_HANDLER_SUPPORTED = False
try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    _MCP_AVAILABLE = True
    try:
        from mcp.client.streamable_http import streamablehttp_client
        _MCP_HTTP_AVAILABLE = True
    except ImportError:
        _MCP_HTTP_AVAILABLE = False
    # 优先使用非弃用 API（mcp >= 1.24.0）；旧版 SDK 回退到已弃用的封装器
    try:
        from mcp.client.streamable_http import streamable_http_client
        _MCP_NEW_HTTP = True
    except ImportError:
        _MCP_NEW_HTTP = False
    # Sampling 类型 -- 单独导入以避免旧版 SDK 破坏 MCP 支持
    try:
        from mcp.types import (
            CreateMessageResult,
            CreateMessageResultWithTools,
            ErrorData,
            SamplingCapability,
            SamplingToolsCapability,
            TextContent,
            ToolUseContent,
        )
        _MCP_SAMPLING_TYPES = True
    except ImportError:
        logger.debug("MCP sampling types not available -- sampling disabled")
    # 动态工具发现的通知类型（tools/list_changed）
    try:
        from mcp.types import (
            ServerNotification,
            ToolListChangedNotification,
            PromptListChangedNotification,
            ResourceListChangedNotification,
        )
        _MCP_NOTIFICATION_TYPES = True
    except ImportError:
        logger.debug("MCP notification types not available -- dynamic tool discovery disabled")
except ImportError:
    logger.debug("mcp package not installed -- MCP tool support disabled")


def _check_message_handler_support() -> bool:
    """检查 ClientSession 是否接受 ``message_handler`` 关键字参数。

    通过检查构造函数签名来保持与不支持通知处理器的旧版 MCP SDK 的向后兼容性。
    """
    if not _MCP_AVAILABLE:
        return False
    try:
        return "message_handler" in inspect.signature(ClientSession).parameters
    except (TypeError, ValueError):
        return False


_MCP_MESSAGE_HANDLER_SUPPORTED = _check_message_handler_support()
if _MCP_AVAILABLE and not _MCP_MESSAGE_HANDLER_SUPPORTED:
    logger.debug("MCP SDK does not support message_handler -- dynamic tool discovery disabled")

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

_DEFAULT_TOOL_TIMEOUT = 120      # 工具调用超时（秒）
_DEFAULT_CONNECT_TIMEOUT = 60    # 每个服务器初始连接超时（秒）
_MAX_RECONNECT_RETRIES = 5
_MAX_INITIAL_CONNECT_RETRIES = 3 # 首次连接尝试的重试次数
_MAX_BACKOFF_SECONDS = 60

# 可安全传递给 stdio 子进程的环境变量
_SAFE_ENV_KEYS = frozenset({
    "PATH", "HOME", "USER", "LANG", "LC_ALL", "TERM", "SHELL", "TMPDIR",
})

# 用于从错误消息中剥离凭证模式的正则表达式
_CREDENTIAL_PATTERN = re.compile(
    r"(?:"
    r"ghp_[A-Za-z0-9_]{1,255}"           # GitHub 个人访问令牌
    r"|sk-[A-Za-z0-9_]{1,255}"           # OpenAI 风格密钥
    r"|Bearer\s+\S+"                      # Bearer 令牌
    r"|token=[^\s&,;\"']{1,255}"         # token=...
    r"|key=[^\s&,;\"']{1,255}"           # key=...
    r"|API_KEY=[^\s&,;\"']{1,255}"       # API_KEY=...
    r"|password=[^\s&,;\"']{1,255}"      # password=...
    r"|secret=[^\s&,;\"']{1,255}"        # secret=...
    r")",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# 安全辅助函数
# ---------------------------------------------------------------------------

def _build_safe_env(user_env: Optional[dict]) -> dict:
    """为 stdio 子进程构建经过过滤的环境变量字典。

    只从当前进程环境中传递安全的基础变量（PATH、HOME 等）和 XDG_* 变量，
    以及用户在服务器配置中显式指定的变量。

    这可以防止意外将 API 密钥、令牌或凭证等敏感信息泄露给 MCP 服务器子进程。
    """
    env = {}
    for key, value in os.environ.items():
        if key in _SAFE_ENV_KEYS or key.startswith("XDG_"):
            env[key] = value
    if user_env:
        env.update(user_env)
    return env


def _sanitize_error(text: str) -> str:
    """在返回给 LLM 之前，从错误文本中剥离类似凭证的模式。

    将令牌、密钥和其他敏感信息替换为 [REDACTED]，以防止
    工具错误响应中的凭证意外暴露。
    """
    return _CREDENTIAL_PATTERN.sub("[REDACTED]", text)


# ---------------------------------------------------------------------------
# MCP 工具描述内容扫描
# ---------------------------------------------------------------------------

# 指示 MCP 工具描述中可能存在提示词注入的模式。
# 这些是 WARNING 级别 -- 仅记录但不阻止，因为误报会破坏合法的 MCP 服务器。
_MCP_INJECTION_PATTERNS = [
    (re.compile(r"ignore\s+(all\s+)?previous\s+instructions", re.I),
     "prompt override attempt ('ignore previous instructions')"),
    (re.compile(r"you\s+are\s+now\s+a", re.I),
     "identity override attempt ('you are now a...')"),
    (re.compile(r"your\s+new\s+(task|role|instructions?)\s+(is|are)", re.I),
     "task override attempt"),
    (re.compile(r"system\s*:\s*", re.I),
     "system prompt injection attempt"),
    (re.compile(r"<\s*(system|human|assistant)\s*>", re.I),
     "role tag injection attempt"),
    (re.compile(r"do\s+not\s+(tell|inform|mention|reveal)", re.I),
     "concealment instruction"),
    (re.compile(r"(curl|wget|fetch)\s+https?://", re.I),
     "network command in description"),
    (re.compile(r"base64\.(b64decode|decodebytes)", re.I),
     "base64 decode reference"),
    (re.compile(r"exec\s*\(|eval\s*\(", re.I),
     "code execution reference"),
    (re.compile(r"import\s+(subprocess|os|shutil|socket)", re.I),
     "dangerous import reference"),
]


def _scan_mcp_description(server_name: str, tool_name: str, description: str) -> List[str]:
    """扫描 MCP 工具描述中的提示词注入模式。

    返回发现的问题字符串列表（空列表 = 安全）。
    """
    findings = []
    if not description:
        return findings
    for pattern, reason in _MCP_INJECTION_PATTERNS:
        if pattern.search(description):
            findings.append(reason)
    if findings:
        logger.warning(
            "MCP server '%s' tool '%s': suspicious description content — %s. "
            "Description: %.200s",
            server_name, tool_name, "; ".join(findings),
            description,
        )
    return findings


def _prepend_path(env: dict, directory: str) -> dict:
    """如果 *directory* 尚不存在，则将其添加到环境变量 PATH 的开头。"""
    updated = dict(env or {})
    if not directory:
        return updated

    existing = updated.get("PATH", "")
    parts = [part for part in existing.split(os.pathsep) if part]
    if directory not in parts:
        parts = [directory, *parts]
    updated["PATH"] = os.pathsep.join(parts) if parts else directory
    return updated


def _resolve_stdio_command(command: str, env: dict) -> tuple[str, dict]:
    """根据实际子进程环境解析 stdio MCP 命令。

    主要用于确保裸命令 ``npx``/``npm``/``node`` 即使在 MCP 子进程
    运行在经过过滤的 PATH 下也能可靠工作。
    """
    resolved_command = os.path.expanduser(str(command).strip())
    resolved_env = dict(env or {})

    if os.sep not in resolved_command:
        path_arg = resolved_env["PATH"] if "PATH" in resolved_env else None
        which_hit = shutil.which(resolved_command, path=path_arg)
        if which_hit:
            resolved_command = which_hit
        elif resolved_command in {"npx", "npm", "node"}:
            hermes_home = os.path.expanduser(
                os.getenv(
                    "HERMES_HOME", os.path.join(os.path.expanduser("~"), ".hermes")
                )
            )
            candidates = [
                os.path.join(hermes_home, "node", "bin", resolved_command),
                os.path.join(os.path.expanduser("~"), ".local", "bin", resolved_command),
            ]
            for candidate in candidates:
                if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                    resolved_command = candidate
                    break

    command_dir = os.path.dirname(resolved_command)
    if command_dir:
        resolved_env = _prepend_path(resolved_env, command_dir)

    return resolved_command, resolved_env


def _format_connect_error(exc: BaseException) -> str:
    """将嵌套的 MCP 连接错误渲染为可操作的简短消息。"""

    def _find_missing(current: BaseException) -> Optional[str]:
        nested = getattr(current, "exceptions", None)
        if nested:
            for child in nested:
                missing = _find_missing(child)
                if missing:
                    return missing
            return None
        if isinstance(current, FileNotFoundError):
            if getattr(current, "filename", None):
                return str(current.filename)
            match = re.search(r"No such file or directory: '([^']+)'", str(current))
            if match:
                return match.group(1)
        for attr in ("__cause__", "__context__"):
            nested_exc = getattr(current, attr, None)
            if isinstance(nested_exc, BaseException):
                missing = _find_missing(nested_exc)
                if missing:
                    return missing
        return None

    def _flatten_messages(current: BaseException) -> List[str]:
        nested = getattr(current, "exceptions", None)
        if nested:
            flattened: List[str] = []
            for child in nested:
                flattened.extend(_flatten_messages(child))
            return flattened
        messages = []
        text = str(current).strip()
        if text:
            messages.append(text)
        for attr in ("__cause__", "__context__"):
            nested_exc = getattr(current, attr, None)
            if isinstance(nested_exc, BaseException):
                messages.extend(_flatten_messages(nested_exc))
        return messages or [current.__class__.__name__]

    missing = _find_missing(exc)
    if missing:
        message = f"missing executable '{missing}'"
        if os.path.basename(missing) in {"npx", "npm", "node"}:
            message += (
                " (ensure Node.js is installed and PATH includes its bin directory, "
                "or set mcp_servers.<name>.command to an absolute path and include "
                "that directory in mcp_servers.<name>.env.PATH)"
            )
        return _sanitize_error(message)

    deduped: List[str] = []
    for item in _flatten_messages(exc):
        if item not in deduped:
            deduped.append(item)
    return _sanitize_error("; ".join(deduped[:3]))


# ---------------------------------------------------------------------------
# Sampling -- 服务器发起的 LLM 请求（MCP sampling/createMessage）
# ---------------------------------------------------------------------------

def _safe_numeric(value, default, coerce=int, minimum=1):
    """将配置值强制转换为数值类型，失败时返回 *default*。

    处理来自 YAML 的字符串值（例如 ``"10"`` 而非 ``10``），
    非有限浮点数，以及低于 *minimum* 的值。
    """
    try:
        result = coerce(value)
        if isinstance(result, float) and not math.isfinite(result):
            return default
        return max(result, minimum)
    except (TypeError, ValueError, OverflowError):
        return default


class SamplingHandler:
    """处理单个 MCP 服务器的 sampling/createMessage 请求。

    每个启用了 sampling 的 MCPServerTask 创建一个 SamplingHandler。
    处理器可调用并直接作为 ``sampling_callback`` 传递给 ``ClientSession``。
    所有状态（速率限制时间戳、指标、工具循环计数器）都存储在实例上
    -- 无模块级全局变量。

    回调是异步的，运行在 MCP 后台事件循环上。同步 LLM 调用通过
    ``asyncio.to_thread()`` 卸载到线程中，以避免阻塞事件循环。
    """

    _STOP_REASON_MAP = {"stop": "endTurn", "length": "maxTokens", "tool_calls": "toolUse"}

    def __init__(self, server_name: str, config: dict):
        self.server_name = server_name
        self.max_rpm = _safe_numeric(config.get("max_rpm", 10), 10, int)
        self.timeout = _safe_numeric(config.get("timeout", 30), 30, float)
        self.max_tokens_cap = _safe_numeric(config.get("max_tokens_cap", 4096), 4096, int)
        self.max_tool_rounds = _safe_numeric(
            config.get("max_tool_rounds", 5), 5, int, minimum=0,
        )
        self.model_override = config.get("model")
        self.allowed_models = config.get("allowed_models", [])

        _log_levels = {"debug": logging.DEBUG, "info": logging.INFO, "warning": logging.WARNING}
        self.audit_level = _log_levels.get(
            str(config.get("log_level", "info")).lower(), logging.INFO,
        )

        # 每个实例的状态
        self._rate_timestamps: List[float] = []
        self._tool_loop_count = 0
        self.metrics = {"requests": 0, "errors": 0, "tokens_used": 0, "tool_use_count": 0}

    # -- 速率限制 -------------------------------------------------------

    def _check_rate_limit(self) -> bool:
        """滑动窗口速率限制器。如果允许请求则返回 True。"""
        now = time.time()
        window = now - 60
        self._rate_timestamps[:] = [t for t in self._rate_timestamps if t > window]
        if len(self._rate_timestamps) >= self.max_rpm:
            return False
        self._rate_timestamps.append(now)
        return True

    # -- 模型解析 ----------------------------------------------------

    def _resolve_model(self, preferences) -> Optional[str]:
        """配置覆盖 > 服务器提示 > None（使用默认值）。"""
        if self.model_override:
            return self.model_override
        if preferences and hasattr(preferences, "hints") and preferences.hints:
            for hint in preferences.hints:
                if hasattr(hint, "name") and hint.name:
                    return hint.name
        return None

    # -- 消息转换 --------------------------------------------------

    @staticmethod
    def _extract_tool_result_text(block) -> str:
        """从 ToolResultContent 块中提取文本。"""
        if not hasattr(block, "content") or block.content is None:
            return ""
        items = block.content if isinstance(block.content, list) else [block.content]
        return "\n".join(item.text for item in items if hasattr(item, "text"))

    def _convert_messages(self, params) -> List[dict]:
        """将 MCP SamplingMessages 转换为 OpenAI 格式。

        使用 ``msg.content_as_list``（SDK 辅助方法），因此单块和列表块
        被统一处理。按块类型分发，当 SDK 类型可用时使用 ``isinstance``
        进行判断，否则通过 ``hasattr`` 进行鸭子类型回退以保持兼容性。
        """
        messages: List[dict] = []
        for msg in params.messages:
            blocks = msg.content_as_list if hasattr(msg, "content_as_list") else (
                msg.content if isinstance(msg.content, list) else [msg.content]
            )

            # 按类型分离块
            tool_results = [b for b in blocks if hasattr(b, "toolUseId")]
            tool_uses = [b for b in blocks if hasattr(b, "name") and hasattr(b, "input") and not hasattr(b, "toolUseId")]
            content_blocks = [b for b in blocks if not hasattr(b, "toolUseId") and not (hasattr(b, "name") and hasattr(b, "input"))]

            # 生成工具结果消息（role: tool）
            for tr in tool_results:
                messages.append({
                    "role": "tool",
                    "tool_call_id": tr.toolUseId,
                    "content": self._extract_tool_result_text(tr),
                })

            # 生成 assistant tool_calls 消息
            if tool_uses:
                tc_list = []
                for tu in tool_uses:
                    tc_list.append({
                        "id": getattr(tu, "id", f"call_{len(tc_list)}"),
                        "type": "function",
                        "function": {
                            "name": tu.name,
                            "arguments": json.dumps(tu.input, ensure_ascii=False) if isinstance(tu.input, dict) else str(tu.input),
                        },
                    })
                msg_dict: dict = {"role": msg.role, "tool_calls": tc_list}
                # 包含附带的文本
                text_parts = [b.text for b in content_blocks if hasattr(b, "text")]
                if text_parts:
                    msg_dict["content"] = "\n".join(text_parts)
                messages.append(msg_dict)
            elif content_blocks:
                # 纯文本/图片内容
                if len(content_blocks) == 1 and hasattr(content_blocks[0], "text"):
                    messages.append({"role": msg.role, "content": content_blocks[0].text})
                else:
                    parts = []
                    for block in content_blocks:
                        if hasattr(block, "text"):
                            parts.append({"type": "text", "text": block.text})
                        elif hasattr(block, "data") and hasattr(block, "mimeType"):
                            parts.append({
                                "type": "image_url",
                                "image_url": {"url": f"data:{block.mimeType};base64,{block.data}"},
                            })
                        else:
                            logger.warning(
                                "Unsupported sampling content block type: %s (skipped)",
                                type(block).__name__,
                            )
                    if parts:
                        messages.append({"role": msg.role, "content": parts})

        return messages

    # -- 错误辅助方法 --------------------------------------------------------

    @staticmethod
    def _error(message: str, code: int = -1):
        """返回 ErrorData（MCP 规范）或作为后备抛出异常。"""
        if _MCP_SAMPLING_TYPES:
            return ErrorData(code=code, message=message)
        raise Exception(message)

    # -- 响应构建 ---------------------------------------------------

    def _build_tool_use_result(self, choice, response):
        """从 LLM 的 tool_calls 响应构建 CreateMessageResultWithTools。"""
        self.metrics["tool_use_count"] += 1

        # 工具循环治理
        if self.max_tool_rounds == 0:
            self._tool_loop_count = 0
            return self._error(
                f"Tool loops disabled for server '{self.server_name}' (max_tool_rounds=0)"
            )

        self._tool_loop_count += 1
        if self._tool_loop_count > self.max_tool_rounds:
            self._tool_loop_count = 0
            return self._error(
                f"Tool loop limit exceeded for server '{self.server_name}' "
                f"(max {self.max_tool_rounds} rounds)"
            )

        content_blocks = []
        for tc in choice.message.tool_calls:
            args = tc.function.arguments
            if isinstance(args, str):
                try:
                    parsed = json.loads(args)
                except (json.JSONDecodeError, ValueError):
                    logger.warning(
                        "MCP server '%s': malformed tool_calls arguments "
                        "from LLM (wrapping as raw): %.100s",
                        self.server_name, args,
                    )
                    parsed = {"_raw": args}
            else:
                parsed = args if isinstance(args, dict) else {"_raw": str(args)}

            content_blocks.append(ToolUseContent(
                type="tool_use",
                id=tc.id,
                name=tc.function.name,
                input=parsed,
            ))

        logger.log(
            self.audit_level,
            "MCP server '%s' sampling response: model=%s, tokens=%s, tool_calls=%d",
            self.server_name, response.model,
            getattr(getattr(response, "usage", None), "total_tokens", "?"),
            len(content_blocks),
        )

        return CreateMessageResultWithTools(
            role="assistant",
            content=content_blocks,
            model=response.model,
            stopReason="toolUse",
        )

    def _build_text_result(self, choice, response):
        """从普通文本响应构建 CreateMessageResult。"""
        self._tool_loop_count = 0  # 收到文本响应时重置
        response_text = choice.message.content or ""

        logger.log(
            self.audit_level,
            "MCP server '%s' sampling response: model=%s, tokens=%s",
            self.server_name, response.model,
            getattr(getattr(response, "usage", None), "total_tokens", "?"),
        )

        return CreateMessageResult(
            role="assistant",
            content=TextContent(type="text", text=_sanitize_error(response_text)),
            model=response.model,
            stopReason=self._STOP_REASON_MAP.get(choice.finish_reason, "endTurn"),
        )

    # -- 会话参数辅助方法 -----------------------------------------------

    def session_kwargs(self) -> dict:
        """返回传递给 ClientSession 以支持 sampling 的关键字参数。"""
        return {
            "sampling_callback": self,
            "sampling_capabilities": SamplingCapability(
                tools=SamplingToolsCapability(),
            ),
        }

    # -- 主回调 -------------------------------------------------------

    async def __call__(self, context, params):
        """MCP SDK 调用的 sampling 回调。

        符合 ``SamplingFnT`` 协议。返回
        ``CreateMessageResult``、``CreateMessageResultWithTools`` 或
        ``ErrorData``。
        """
        # 速率限制
        if not self._check_rate_limit():
            logger.warning(
                "MCP server '%s' sampling rate limit exceeded (%d/min)",
                self.server_name, self.max_rpm,
            )
            self.metrics["errors"] += 1
            return self._error(
                f"Sampling rate limit exceeded for server '{self.server_name}' "
                f"({self.max_rpm} requests/minute)"
            )

        # 解析模型
        model = self._resolve_model(getattr(params, "modelPreferences", None))

        # 通过集中路由获取辅助 LLM 客户端
        from agent.auxiliary_client import call_llm

        # 模型白名单检查（需要先解析模型才能检查）
        resolved_model = model or self.model_override or ""

        if self.allowed_models and resolved_model and resolved_model not in self.allowed_models:
            logger.warning(
                "MCP server '%s' requested model '%s' not in allowed_models",
                self.server_name, resolved_model,
            )
            self.metrics["errors"] += 1
            return self._error(
                f"Model '{resolved_model}' not allowed for server "
                f"'{self.server_name}'. Allowed: {', '.join(self.allowed_models)}"
            )

        # 转换消息
        messages = self._convert_messages(params)
        if hasattr(params, "systemPrompt") and params.systemPrompt:
            messages.insert(0, {"role": "system", "content": params.systemPrompt})

        # 构建 LLM 调用参数
        max_tokens = min(params.maxTokens, self.max_tokens_cap)
        call_temperature = None
        if hasattr(params, "temperature") and params.temperature is not None:
            call_temperature = params.temperature

        # 转发服务器提供的工具
        call_tools = None
        server_tools = getattr(params, "tools", None)
        if server_tools:
            call_tools = [
                {
                    "type": "function",
                    "function": {
                        "name": getattr(t, "name", ""),
                        "description": getattr(t, "description", "") or "",
                        "parameters": _normalize_mcp_input_schema(
                            getattr(t, "inputSchema", None)
                        ),
                    },
                }
                for t in server_tools
            ]

        logger.log(
            self.audit_level,
            "MCP server '%s' sampling request: model=%s, max_tokens=%d, messages=%d",
            self.server_name, resolved_model, max_tokens, len(messages),
        )

        # 将同步 LLM 调用卸载到线程中（非阻塞）
        def _sync_call():
            return call_llm(
                task="mcp",
                model=resolved_model or None,
                messages=messages,
                temperature=call_temperature,
                max_tokens=max_tokens,
                tools=call_tools,
                timeout=self.timeout,
            )

        try:
            response = await asyncio.wait_for(
                asyncio.to_thread(_sync_call), timeout=self.timeout,
            )
        except asyncio.TimeoutError:
            self.metrics["errors"] += 1
            return self._error(
                f"Sampling LLM call timed out after {self.timeout}s "
                f"for server '{self.server_name}'"
            )
        except Exception as exc:
            self.metrics["errors"] += 1
            return self._error(
                f"Sampling LLM call failed: {_sanitize_error(str(exc))}"
            )

        # 防止空 choices（内容过滤、提供者错误等情况）
        if not getattr(response, "choices", None):
            self.metrics["errors"] += 1
            return self._error(
                f"LLM returned empty response (no choices) for server "
                f"'{self.server_name}'"
            )

        # 跟踪指标
        choice = response.choices[0]
        self.metrics["requests"] += 1
        total_tokens = getattr(getattr(response, "usage", None), "total_tokens", 0)
        if isinstance(total_tokens, int):
            self.metrics["tokens_used"] += total_tokens

        # 根据响应类型分发
        if (
            choice.finish_reason == "tool_calls"
            and hasattr(choice.message, "tool_calls")
            and choice.message.tool_calls
        ):
            return self._build_tool_use_result(choice, response)

        return self._build_text_result(choice, response)


# ---------------------------------------------------------------------------
# 服务器任务 -- 每个 MCP 服务器运行在一个长生命周期的 asyncio Task 中
# ---------------------------------------------------------------------------

class MCPServerTask:
    """管理在专用 asyncio Task 中运行的单个 MCP 服务器连接。

    整个连接生命周期（连接、发现、服务、断开）在一个 asyncio Task 中运行，
    以确保传输客户端创建的 anyio 取消域在同一个 Task 上下文中进入和退出。

    支持 stdio 和 HTTP/StreamableHTTP 两种传输方式。
    """

    __slots__ = (
        "name", "session", "tool_timeout",
        "_task", "_ready", "_shutdown_event", "_reconnect_event",
        "_tools", "_error", "_config",
        "_sampling", "_registered_tool_names", "_auth_type", "_refresh_lock",
    )

    def __init__(self, name: str):
        self.name = name
        self.session: Optional[Any] = None
        self.tool_timeout: float = _DEFAULT_TOOL_TIMEOUT
        self._task: Optional[asyncio.Task] = None
        self._ready = asyncio.Event()
        self._shutdown_event = asyncio.Event()
        # 工具处理器在 auth 失败后由 manager.handle_401() 确认恢复可行时设置。
        # 设置后，_run_http / _run_stdio 会干净地退出其 async-with 块（无异常），
        # 外层 run() 循环重新进入传输，以便使用新凭证重建 MCP 会话。
        self._reconnect_event = asyncio.Event()
        self._tools: list = []
        self._error: Optional[Exception] = None
        self._config: dict = {}
        self._sampling: Optional[SamplingHandler] = None
        self._registered_tool_names: list[str] = []
        self._auth_type: str = ""
        self._refresh_lock = asyncio.Lock()

    def _is_http(self) -> bool:
        """检查此服务器是否使用 HTTP 传输。"""
        return "url" in self._config

    # ----- 动态工具发现（notifications/tools/list_changed）-----

    def _make_message_handler(self):
        """为 ``ClientSession`` 构建 ``message_handler`` 回调。

        按通知类型分发。只有 ``ToolListChangedNotification`` 触发刷新；
        prompt 和 resource 变更通知作为桩记录日志，留待未来实现。
        """
        async def _handler(message):
            try:
                if isinstance(message, Exception):
                    logger.debug("MCP message handler (%s): exception: %s", self.name, message)
                    return
                if _MCP_NOTIFICATION_TYPES and isinstance(message, ServerNotification):
                    match message.root:
                        case ToolListChangedNotification():
                            logger.info(
                                "MCP server '%s': received tools/list_changed notification",
                                self.name,
                            )
                            await self._refresh_tools()
                        case PromptListChangedNotification():
                            logger.debug("MCP server '%s': prompts/list_changed (ignored)", self.name)
                        case ResourceListChangedNotification():
                            logger.debug("MCP server '%s': resources/list_changed (ignored)", self.name)
                        case _:
                            pass
            except Exception:
                logger.exception("Error in MCP message handler for '%s'", self.name)
        return _handler

    async def _refresh_tools(self):
        """从服务器重新获取工具并更新注册表。

        当服务器发送 ``notifications/tools/list_changed`` 时调用。
        锁防止快速连续通知导致的重叠刷新。
        在初始 ``await``（list_tools）之后，所有变更都是同步的
        -- 从事件循环的角度来看是原子的。
        """
        from tools.registry import registry

        async with self._refresh_lock:
            # 捕获旧工具名称用于变更对比
            old_tool_names = set(self._registered_tool_names)

            # 1. 从服务器获取当前工具列表
            tools_result = await self.session.list_tools()
            new_mcp_tools = tools_result.tools if hasattr(tools_result, "tools") else []

            # 2. 从中央注册表中注销旧工具
            for prefixed_name in self._registered_tool_names:
                registry.deregister(prefixed_name)

            # 3. 使用新工具列表重新注册
            self._tools = new_mcp_tools
            self._registered_tool_names = _register_server_tools(
                self.name, self, self._config
            )

            # 5. 记录变更内容（用户可见通知）
            new_tool_names = set(self._registered_tool_names)
            added = new_tool_names - old_tool_names
            removed = old_tool_names - new_tool_names
            changes = []
            if added:
                changes.append(f"added: {', '.join(sorted(added))}")
            if removed:
                changes.append(f"removed: {', '.join(sorted(removed))}")
            if changes:
                logger.warning(
                    "MCP server '%s': tools changed dynamically — %s. "
                    "Verify these changes are expected.",
                    self.name, "; ".join(changes),
                )
            else:
                logger.info(
                    "MCP server '%s': dynamically refreshed %d tool(s) (no changes)",
                    self.name, len(self._registered_tool_names),
                )

    async def _wait_for_lifecycle_event(self) -> str:
        """阻塞直到 _shutdown_event 或 _reconnect_event 触发。

        返回：
            "shutdown"  如果服务器应完全退出运行循环。
            "reconnect" 如果服务器应拆除当前 MCP 会话并重新进入传输
                        （新的 OAuth 令牌、新的会话 ID 等）。重连事件
                        在返回前被清除，以便下一个循环从新信号开始。

        如果两个事件同时被设置，shutdown 优先。
        """
        shutdown_task = asyncio.create_task(self._shutdown_event.wait())
        reconnect_task = asyncio.create_task(self._reconnect_event.wait())
        try:
            await asyncio.wait(
                {shutdown_task, reconnect_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            for t in (shutdown_task, reconnect_task):
                if not t.done():
                    t.cancel()
                    try:
                        await t
                    except (asyncio.CancelledError, Exception):
                        pass

        if self._shutdown_event.is_set():
            return "shutdown"
        self._reconnect_event.clear()
        return "reconnect"

    async def _run_stdio(self, config: dict):
        """使用 stdio 传输运行服务器。"""
        command = config.get("command")
        args = config.get("args", [])
        user_env = config.get("env")

        if not command:
            raise ValueError(
                f"MCP server '{self.name}' has no 'command' in config"
            )

        safe_env = _build_safe_env(user_env)
        command, safe_env = _resolve_stdio_command(command, safe_env)

        # 在生成子进程前对 OSV 恶意软件数据库进行包检查
        from tools.osv_check import check_package_for_malware
        malware_error = check_package_for_malware(command, args)
        if malware_error:
            raise ValueError(
                f"MCP server '{self.name}': {malware_error}"
            )

        server_params = StdioServerParameters(
            command=command,
            args=args,
            env=safe_env if safe_env else None,
        )

        sampling_kwargs = self._sampling.session_kwargs() if self._sampling else {}
        if _MCP_NOTIFICATION_TYPES and _MCP_MESSAGE_HANDLER_SUPPORTED:
            sampling_kwargs["message_handler"] = self._make_message_handler()

        # 在生成子进程前快照子进程 PID，以便跟踪新进程。
        pids_before = _snapshot_child_pids()
        async with stdio_client(server_params) as (read_stream, write_stream):
            # 捕获新生成的子进程 PID，用于强制终止清理。
            new_pids = _snapshot_child_pids() - pids_before
            if new_pids:
                with _lock:
                    _stdio_pids.update(new_pids)
            async with ClientSession(read_stream, write_stream, **sampling_kwargs) as session:
                await session.initialize()
                self.session = session
                await self._discover_tools()
                self._ready.set()
                # stdio 传输不使用 OAuth，但为了与 _run_http 保持一致，
                # 我们仍然响应 _reconnect_event（例如未来的手动 /mcp refresh）。
                await self._wait_for_lifecycle_event()
        # 上下文正常退出 -- 子进程已被 SDK 终止。
        if new_pids:
            with _lock:
                _stdio_pids.difference_update(new_pids)

    async def _run_http(self, config: dict):
        """使用 HTTP/StreamableHTTP 传输运行服务器。"""
        if not _MCP_HTTP_AVAILABLE:
            raise ImportError(
                f"MCP server '{self.name}' requires HTTP transport but "
                "mcp.client.streamable_http is not available. "
                "Upgrade the mcp package to get HTTP support."
            )

        url = config["url"]
        headers = dict(config.get("headers") or {})
        connect_timeout = config.get("connect_timeout", _DEFAULT_CONNECT_TIMEOUT)

        # OAuth 2.1 PKCE：通过集中的 MCPOAuthManager 路由，以便在重连间
        # 复用同一提供者实例，配置时 CLI 代码路径共享状态，
        # 且预流磁盘监视处于活动状态。
        # 如果 OAuth 设置失败（例如无缓存令牌的非交互环境），
        # 重新抛出，以便该服务器报告失败而不阻塞其他 MCP 服务器连接。
        _oauth_auth = None
        if self._auth_type == "oauth":
            try:
                from tools.mcp_oauth_manager import get_manager
                _oauth_auth = get_manager().get_or_build_provider(
                    self.name, url, config.get("oauth"),
                )
            except Exception as exc:
                logger.warning("MCP OAuth setup failed for '%s': %s", self.name, exc)
                raise

        sampling_kwargs = self._sampling.session_kwargs() if self._sampling else {}
        if _MCP_NOTIFICATION_TYPES and _MCP_MESSAGE_HANDLER_SUPPORTED:
            sampling_kwargs["message_handler"] = self._make_message_handler()

        if _MCP_NEW_HTTP:
            # 新 API（mcp >= 1.24.0）：构建显式的 httpx.AsyncClient，
            # 匹配 SDK 自身 create_mcp_http_client 的默认值。
            import httpx

            client_kwargs: dict = {
                "follow_redirects": True,
                "timeout": httpx.Timeout(float(connect_timeout), read=300.0),
            }
            if headers:
                client_kwargs["headers"] = headers
            if _oauth_auth is not None:
                client_kwargs["auth"] = _oauth_auth

            # 调用者拥有客户端生命周期 -- 当提供 http_client 时 SDK 跳过清理，
            # 因此我们用 async-with 包装。
            async with httpx.AsyncClient(**client_kwargs) as http_client:
                async with streamable_http_client(url, http_client=http_client) as (
                    read_stream, write_stream, _get_session_id,
                ):
                    async with ClientSession(read_stream, write_stream, **sampling_kwargs) as session:
                        await session.initialize()
                        self.session = session
                        await self._discover_tools()
                        self._ready.set()
                        reason = await self._wait_for_lifecycle_event()
                        if reason == "reconnect":
                            logger.info(
                                "MCP server '%s': reconnect requested — "
                                "tearing down HTTP session", self.name,
                            )
        else:
            # 弃用 API（mcp < 1.24.0）：内部管理 httpx 客户端。
            _http_kwargs: dict = {
                "headers": headers,
                "timeout": float(connect_timeout),
            }
            if _oauth_auth is not None:
                _http_kwargs["auth"] = _oauth_auth
            async with streamablehttp_client(url, **_http_kwargs) as (
                read_stream, write_stream, _get_session_id,
            ):
                async with ClientSession(read_stream, write_stream, **sampling_kwargs) as session:
                    await session.initialize()
                    self.session = session
                    await self._discover_tools()
                    self._ready.set()
                    reason = await self._wait_for_lifecycle_event()
                    if reason == "reconnect":
                        logger.info(
                            "MCP server '%s': reconnect requested — "
                            "tearing down legacy HTTP session", self.name,
                        )

    async def _discover_tools(self):
        """从已连接的会话发现工具。"""
        if self.session is None:
            return
        tools_result = await self.session.list_tools()
        self._tools = (
            tools_result.tools
            if hasattr(tools_result, "tools")
            else []
        )

    async def run(self, config: dict):
        """长生命周期协程：连接、发现工具、等待、断开。

        包含指数退避的自动重连，当连接意外断开时触发
        （除非已请求关闭）。
        """
        self._config = config
        self.tool_timeout = config.get("timeout", _DEFAULT_TOOL_TIMEOUT)
        self._auth_type = (config.get("auth") or "").lower().strip()

        # 如果启用了 sampling 且 SDK 类型可用，则设置 sampling 处理器
        sampling_config = config.get("sampling", {})
        if sampling_config.get("enabled", True) and _MCP_SAMPLING_TYPES:
            self._sampling = SamplingHandler(self.name, sampling_config)
        else:
            self._sampling = None

        # 验证：如果同时存在 url 和 command 则发出警告
        if "url" in config and "command" in config:
            logger.warning(
                "MCP server '%s' has both 'url' and 'command' in config. "
                "Using HTTP transport ('url'). Remove 'command' to silence "
                "this warning.",
                self.name,
            )
        retries = 0
        initial_retries = 0
        backoff = 1.0

        while True:
            try:
                if self._is_http():
                    await self._run_http(config)
                else:
                    await self._run_stdio(config)
                # 传输正常返回。两种情况：
                #  - _shutdown_event 已设置：完全退出运行循环。
                #  - _reconnect_event 已设置（auth 恢复）：循环回去使用新凭证
                #    重建 MCP 会话。不要修改重试计数器 -- 这不是失败。
                if self._shutdown_event.is_set():
                    break
                logger.info(
                    "MCP server '%s': reconnecting (OAuth recovery or "
                    "manual refresh)",
                    self.name,
                )
                # 重置会话引用；_run_http/_run_stdio 会在成功重新进入后重新填充。
                self.session = None
                # 在重连间保持 _ready 为已设置状态，以便工具处理器仍能
                # 检测到瞬态的进行中状态 -- 新会话初始化后会重新设置。
                continue
            except Exception as exc:
                self.session = None

                # 如果这是首次连接尝试，在放弃前带退避重试。
                # 启动时的瞬时 DNS/网络故障不应永久终止服务器。
                # （移植自 Kilo Code 的 MCP 弹性修复。）
                if not self._ready.is_set():
                    initial_retries += 1
                    if initial_retries > _MAX_INITIAL_CONNECT_RETRIES:
                        logger.warning(
                            "MCP server '%s' failed initial connection after "
                            "%d attempts, giving up: %s",
                            self.name, _MAX_INITIAL_CONNECT_RETRIES, exc,
                        )
                        self._error = exc
                        self._ready.set()
                        return

                    logger.warning(
                        "MCP server '%s' initial connection failed "
                        "(attempt %d/%d), retrying in %.0fs: %s",
                        self.name, initial_retries,
                        _MAX_INITIAL_CONNECT_RETRIES, backoff, exc,
                    )
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, _MAX_BACKOFF_SECONDS)

                    # 检查等待期间是否请求了关闭
                    if self._shutdown_event.is_set():
                        self._error = exc
                        self._ready.set()
                        return
                    continue

                # 如果已请求关闭，不要重连
                if self._shutdown_event.is_set():
                    logger.debug(
                        "MCP server '%s' disconnected during shutdown: %s",
                        self.name, exc,
                    )
                    return

                retries += 1
                if retries > _MAX_RECONNECT_RETRIES:
                    logger.warning(
                        "MCP server '%s' failed after %d reconnection attempts, "
                        "giving up: %s",
                        self.name, _MAX_RECONNECT_RETRIES, exc,
                    )
                    return

                logger.warning(
                    "MCP server '%s' connection lost (attempt %d/%d), "
                    "reconnecting in %.0fs: %s",
                    self.name, retries, _MAX_RECONNECT_RETRIES,
                    backoff, exc,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, _MAX_BACKOFF_SECONDS)

                # 等待后再次检查
                if self._shutdown_event.is_set():
                    return
            finally:
                self.session = None

    async def start(self, config: dict):
        """创建后台 Task 并等待就绪（或失败）。"""
        self._task = asyncio.ensure_future(self.run(config))
        await self._ready.wait()
        if self._error:
            raise self._error

    async def shutdown(self):
        """通知 Task 退出并等待资源的干净拆除。"""
        from tools.registry import registry

        self._shutdown_event.set()
        # 防御性：如果 _wait_for_lifecycle_event 正在阻塞，我们需要任何事件
        # 来解除阻塞。单独的 _shutdown_event 就足够了（辅助函数先检查 shutdown），
        # 但同时设置 reconnect 可确保不会出现辅助函数在返回 "reconnect"
        # 后错过 shutdown 标志的竞态条件。
        self._reconnect_event.set()
        if self._task and not self._task.done():
            try:
                await asyncio.wait_for(self._task, timeout=10)
            except asyncio.TimeoutError:
                logger.warning(
                    "MCP server '%s' shutdown timed out, cancelling task",
                    self.name,
                )
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass
        for tool_name in list(getattr(self, "_registered_tool_names", [])):
            registry.deregister(tool_name)
        self._registered_tool_names = []
        self.session = None


# ---------------------------------------------------------------------------
# 模块级状态
# ---------------------------------------------------------------------------

_servers: Dict[str, MCPServerTask] = {}

# 熔断器：每个服务器的连续错误计数。在连续 _CIRCUIT_BREAKER_THRESHOLD
# 次失败后，处理器返回"服务器不可达"消息，告诉模型停止重试，
# 防止 #10447 中描述的 90 次迭代烧毁循环。
# 任何成功调用时重置为 0。
_server_error_counts: Dict[str, int] = {}
_CIRCUIT_BREAKER_THRESHOLD = 3

# ---------------------------------------------------------------------------
# 认证失败检测辅助函数（MCP OAuth 整合的任务 6）
# ---------------------------------------------------------------------------

# 缓存的认证相关异常类型元组。延迟加载以便在 MCP SDK OAuth 模块
# 不存在时此模块仍能正常导入。
_AUTH_ERROR_TYPES: tuple = ()


def _get_auth_error_types() -> tuple:
    """返回指示 MCP OAuth 失败的异常类型元组。

    首次调用后缓存。包括：
      - ``mcp.client.auth.OAuthFlowError`` / ``OAuthTokenError`` -- 当发现、
        刷新或完全重新认证失败时由 SDK 的 auth 流引发。
      - ``mcp.client.auth.UnauthorizedError``（旧版 MCP SDK）-- 作为可选
        导入保留，用于向前/向后兼容。
      - ``tools.mcp_oauth.OAuthNonInteractiveError`` -- 当没有用户完成
        浏览器流程时由我们的回调处理器引发。
      - ``httpx.HTTPStatusError`` -- 调用者还必须通过 :func:`_is_auth_error`
        额外检查 ``status_code == 401``。
    """
    global _AUTH_ERROR_TYPES
    if _AUTH_ERROR_TYPES:
        return _AUTH_ERROR_TYPES
    types: list = []
    try:
        from mcp.client.auth import OAuthFlowError, OAuthTokenError
        types.extend([OAuthFlowError, OAuthTokenError])
    except ImportError:
        pass
    try:
        # 旧版 MCP SDK 变体导出了此类型
        from mcp.client.auth import UnauthorizedError  # type: ignore
        types.append(UnauthorizedError)
    except ImportError:
        pass
    try:
        from tools.mcp_oauth import OAuthNonInteractiveError
        types.append(OAuthNonInteractiveError)
    except ImportError:
        pass
    try:
        import httpx
        types.append(httpx.HTTPStatusError)
    except ImportError:
        pass
    _AUTH_ERROR_TYPES = tuple(types)
    return _AUTH_ERROR_TYPES


def _is_auth_error(exc: BaseException) -> bool:
    """当 ``exc`` 指示 MCP OAuth 失败时返回 True。

    ``httpx.HTTPStatusError`` 仅在响应状态码为 401 时才视为认证相关。
    其他 HTTP 错误会进入工具处理器中的通用错误路径。
    """
    types = _get_auth_error_types()
    if not types or not isinstance(exc, types):
        return False
    try:
        import httpx
        if isinstance(exc, httpx.HTTPStatusError):
            return getattr(exc.response, "status_code", None) == 401
    except ImportError:
        pass
    return True


def _handle_auth_error_and_retry(
    server_name: str,
    exc: BaseException,
    retry_call,
    op_description: str,
):
    """尝试认证恢复并重试一次；返回 None 以回退到通用路径。

    当 ``session.<op>()`` 抛出认证相关异常时，由 5 个 MCP 工具处理器调用。
    工作流：

      1. 询问 :class:`tools.mcp_oauth_manager.MCPOAuthManager.handle_401` 恢复
         是否可行（即磁盘上有新令牌，或 SDK 可以就地刷新）。
      2. 如果可行，设置服务器的 ``_reconnect_event``，使服务器任务拆除当前
         MCP 会话并使用新凭证重建。短暂等待 ``_ready`` 重新触发。
      3. 重试操作一次。如果产生了非错误的 JSON 负载则返回重试结果。
         否则返回 ``needs_reauth`` 错误字典，使模型停止幻觉式手动刷新。
      4. 如果 ``exc`` 不是认证错误则返回 None，通知调用者使用通用错误路径。

    参数：
        server_name: 引发异常的 MCP 服务器名称。
        exc: 失败的工具调用引发的异常。
        retry_call: 零参数可调用对象，重新运行工具调用，返回
            与处理器相同的 JSON 字符串格式。
        op_description: 操作的人类可读名称（用于日志）。

    返回：
        如果尝试了认证恢复则返回 JSON 字符串，否则返回 None
        以回退到调用者的通用错误路径。
    """
    if not _is_auth_error(exc):
        return None

    from tools.mcp_oauth_manager import get_manager
    manager = get_manager()

    async def _recover():
        return await manager.handle_401(server_name, None)

    try:
        recovered = _run_on_mcp_loop(_recover(), timeout=10)
    except Exception as rec_exc:
        logger.warning(
            "MCP OAuth '%s': recovery attempt failed: %s",
            server_name, rec_exc,
        )
        recovered = False

    if recovered:
        with _lock:
            srv = _servers.get(server_name)
        if srv is not None and hasattr(srv, "_reconnect_event"):
            loop = _mcp_loop
            if loop is not None and loop.is_running():
                loop.call_soon_threadsafe(srv._reconnect_event.set)
                # 短暂等待会话恢复就绪。设置上限以便卡住的
                # 重连回退到错误路径而非挂起调用者。
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    if srv.session is not None and srv._ready.is_set():
                        break
                    time.sleep(0.25)

        try:
            result = retry_call()
            try:
                parsed = json.loads(result)
                if "error" not in parsed:
                    _server_error_counts[server_name] = 0
                    return result
            except (json.JSONDecodeError, TypeError):
                _server_error_counts[server_name] = 0
                return result
        except Exception as retry_exc:
            logger.warning(
                "MCP %s/%s retry after auth recovery failed: %s",
                server_name, op_description, retry_exc,
            )

    # 无法恢复，或重试也失败：返回结构化的 needs_reauth 错误。
    # 增加熔断器计数以使模型停止重试工具。
    _server_error_counts[server_name] = _server_error_counts.get(server_name, 0) + 1
    return json.dumps({
        "error": (
            f"MCP server '{server_name}' requires re-authentication. "
            f"Run `hermes mcp login {server_name}` (or delete the tokens "
            f"file under ~/.hermes/mcp-tokens/ and restart). Do NOT retry "
            f"this tool — ask the user to re-authenticate."
        ),
        "needs_reauth": True,
        "server": server_name,
    }, ensure_ascii=False)

# 运行在后台守护线程中的专用事件循环。
_mcp_loop: Optional[asyncio.AbstractEventLoop] = None
_mcp_thread: Optional[threading.Thread] = None

# 保护 _mcp_loop、_mcp_thread、_servers 和 _stdio_pids 的锁。
_lock = threading.Lock()

# stdio MCP 服务器子进程的 PID。用于在关闭时如果优雅清理
#（SDK 上下文管理器拆除）失败或超时，可以强制终止它们。
# PID 在连接后添加，在正常服务器关闭时移除。
_stdio_pids: set = set()


def _snapshot_child_pids() -> set:
    """返回当前子进程 PID 的集合。

    在 Linux 上使用 /proc，回退到 psutil，最后返回空集合。
    由 _run_stdio 用于识别 stdio_client 生成的子进程。
    """
    my_pid = os.getpid()

    # Linux：从 /proc 读取
    try:
        children_path = f"/proc/{my_pid}/task/{my_pid}/children"
        with open(children_path) as f:
            return {int(p) for p in f.read().split() if p.strip()}
    except (FileNotFoundError, OSError, ValueError):
        pass

    # 回退：psutil
    try:
        import psutil
        return {c.pid for c in psutil.Process(my_pid).children()}
    except Exception:
        pass

    return set()


def _mcp_loop_exception_handler(loop, context):
    """抑制关闭期间无害的 'Event loop is closed' 噪音。

    当 MCP 事件循环被停止和关闭时，httpx/httpcore 异步传输可能会
    触发 __del__ 终结器，在已关闭的循环上调用 call_soon()。
    asyncio 捕获该 RuntimeError 并路由到此处。
    我们抑制它，因为连接正在被拆除；所有其他异常转发到默认处理器。
    """
    exc = context.get("exception")
    if isinstance(exc, RuntimeError) and "Event loop is closed" in str(exc):
        return  # 无害的关闭竞态 -- 抑制
    loop.default_exception_handler(context)


def _ensure_mcp_loop():
    """如果后台事件循环线程尚未运行，则启动它。"""
    global _mcp_loop, _mcp_thread
    with _lock:
        if _mcp_loop is not None and _mcp_loop.is_running():
            return
        _mcp_loop = asyncio.new_event_loop()
        _mcp_loop.set_exception_handler(_mcp_loop_exception_handler)
        _mcp_thread = threading.Thread(
            target=_mcp_loop.run_forever,
            name="mcp-event-loop",
            daemon=True,
        )
        _mcp_thread.start()


def _run_on_mcp_loop(coro, timeout: float = 30):
    """将协程调度到 MCP 事件循环上并阻塞直到完成。

    以短间隔轮询，以便调用代理线程在 MCP 工作仍在后台循环运行时
    可以响应用户中断。
    """
    from tools.interrupt import is_interrupted

    with _lock:
        loop = _mcp_loop
    if loop is None or not loop.is_running():
        raise RuntimeError("MCP event loop is not running")
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    deadline = None if timeout is None else time.monotonic() + timeout

    while True:
        if is_interrupted():
            future.cancel()
            raise InterruptedError("User sent a new message")

        wait_timeout = 0.1
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return future.result(timeout=0)
            wait_timeout = min(wait_timeout, remaining)

        try:
            return future.result(timeout=wait_timeout)
        except concurrent.futures.TimeoutError:
            continue


def _interrupted_call_result() -> str:
    """用户中断 MCP 工具调用时的标准化 JSON 错误。"""
    return json.dumps({
        "error": "MCP call interrupted: user sent a new message"
    }, ensure_ascii=False)


# ---------------------------------------------------------------------------
# 配置加载
# ---------------------------------------------------------------------------

def _interpolate_env_vars(value):
    """递归解析 ``os.environ`` 中的 ``${VAR}`` 占位符。"""
    if isinstance(value, str):
        import re
        def _replace(m):
            return os.environ.get(m.group(1), m.group(0))
        return re.sub(r"\$\{([^}]+)\}", _replace, value)
    if isinstance(value, dict):
        return {k: _interpolate_env_vars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_interpolate_env_vars(v) for v in value]
    return value


def _load_mcp_config() -> Dict[str, dict]:
    """从 Hermes 配置文件读取 ``mcp_servers``。

    返回 ``{server_name: server_config}`` 字典或空字典。
    服务器配置可以包含 ``command``/``args``/``env``（stdio 传输）
    或 ``url``/``headers``（HTTP 传输），以及可选的
    ``timeout``、``connect_timeout`` 和 ``auth`` 覆盖。

    字符串值中的 ``${ENV_VAR}`` 占位符从 ``os.environ`` 解析
    （启动时已加载 ``~/.hermes/.env``）。
    """
    try:
        from hermes_cli.config import load_config
        config = load_config()
        servers = config.get("mcp_servers")
        if not servers or not isinstance(servers, dict):
            return {}
        # 确保 .env 变量可用于插值
        try:
            from hermes_cli.env_loader import load_hermes_dotenv
            load_hermes_dotenv()
        except Exception:
            pass
        return {name: _interpolate_env_vars(cfg) for name, cfg in servers.items()}
    except Exception as exc:
        logger.debug("Failed to load MCP config: %s", exc)
        return {}


# ---------------------------------------------------------------------------
# 服务器连接辅助函数
# ---------------------------------------------------------------------------

async def _connect_server(name: str, config: dict) -> MCPServerTask:
    """创建 MCPServerTask，启动它，并在就绪时返回。

    服务器 Task 在后台保持连接存活。
    调用 ``server.shutdown()``（在同一事件循环上）以拆除它。

    异常：
        ValueError：如果缺少必需的配置键。
        ImportError：如果需要 HTTP 传输但不可用。
        Exception：连接或初始化失败时。
    """
    server = MCPServerTask(name)
    await server.start(config)
    return server


# ---------------------------------------------------------------------------
# 处理器 / 检查函数工厂
# ---------------------------------------------------------------------------

def _make_tool_handler(server_name: str, tool_name: str, tool_timeout: float):
    """返回通过后台循环调用 MCP 工具的同步处理器。

    处理器符合注册表的调度接口：
    ``handler(args_dict, **kwargs) -> str``
    """

    def _handler(args: dict, **kwargs) -> str:
        # 熔断器：如果此服务器连续失败次数过多，直接短路并返回
        # 明确消息，使模型停止重试并使用替代方案（#10447）。
        if _server_error_counts.get(server_name, 0) >= _CIRCUIT_BREAKER_THRESHOLD:
            return json.dumps({
                "error": (
                    f"MCP server '{server_name}' is unreachable after "
                    f"{_CIRCUIT_BREAKER_THRESHOLD} consecutive failures. "
                    f"Do NOT retry this tool — use alternative approaches "
                    f"or ask the user to check the MCP server."
                )
            }, ensure_ascii=False)

        with _lock:
            server = _servers.get(server_name)
        if not server or not server.session:
            _server_error_counts[server_name] = _server_error_counts.get(server_name, 0) + 1
            return json.dumps({
                "error": f"MCP server '{server_name}' is not connected"
            }, ensure_ascii=False)

        async def _call():
            result = await server.session.call_tool(tool_name, arguments=args)
            # MCP CallToolResult 有 .content（内容块列表）和 .isError
            if result.isError:
                error_text = ""
                for block in (result.content or []):
                    if hasattr(block, "text"):
                        error_text += block.text
                return json.dumps({
                    "error": _sanitize_error(
                        error_text or "MCP tool returned an error"
                    )
                }, ensure_ascii=False)

            # 从内容块中收集文本
            parts: List[str] = []
            for block in (result.content or []):
                if hasattr(block, "text"):
                    parts.append(block.text)
            text_result = "\n".join(parts) if parts else ""

            # 当 content 和 structuredContent 同时存在时合并两者。
            # MCP 规范：content 面向模型（文本），structuredContent
            # 面向机器（JSON 元数据）。对于 AI 代理，content 是主要负载；
            # structuredContent 作为补充。
            structured = getattr(result, "structuredContent", None)
            if structured is not None:
                if text_result:
                    return json.dumps({
                        "result": text_result,
                        "structuredContent": structured,
                    }, ensure_ascii=False)
                return json.dumps({"result": structured}, ensure_ascii=False)
            return json.dumps({"result": text_result}, ensure_ascii=False)

        def _call_once():
            return _run_on_mcp_loop(_call(), timeout=tool_timeout)

        try:
            result = _call_once()
            # 检查 MCP 工具本身是否返回了错误
            try:
                parsed = json.loads(result)
                if "error" in parsed:
                    _server_error_counts[server_name] = _server_error_counts.get(server_name, 0) + 1
                else:
                    _server_error_counts[server_name] = 0  # 成功 -- 重置
            except (json.JSONDecodeError, TypeError):
                _server_error_counts[server_name] = 0  # 非 JSON = 成功
            return result
        except InterruptedError:
            return _interrupted_call_result()
        except Exception as exc:
            # 认证特定恢复路径：咨询管理器，如果可行则发出重连信号，
            # 重试一次。对于非认证异常返回 None 以回退。
            recovered = _handle_auth_error_and_retry(
                server_name, exc, _call_once,
                f"tools/call {tool_name}",
            )
            if recovered is not None:
                return recovered

            _server_error_counts[server_name] = _server_error_counts.get(server_name, 0) + 1
            logger.error(
                "MCP tool %s/%s call failed: %s",
                server_name, tool_name, exc,
            )
            return json.dumps({
                "error": _sanitize_error(
                    f"MCP call failed: {type(exc).__name__}: {exc}"
                )
            }, ensure_ascii=False)

    return _handler


def _make_list_resources_handler(server_name: str, tool_timeout: float):
    """返回从 MCP 服务器列出资源的同步处理器。"""

    def _handler(args: dict, **kwargs) -> str:
        with _lock:
            server = _servers.get(server_name)
        if not server or not server.session:
            return json.dumps({
                "error": f"MCP server '{server_name}' is not connected"
            }, ensure_ascii=False)

        async def _call():
            result = await server.session.list_resources()
            resources = []
            for r in (result.resources if hasattr(result, "resources") else []):
                entry = {}
                if hasattr(r, "uri"):
                    entry["uri"] = str(r.uri)
                if hasattr(r, "name"):
                    entry["name"] = r.name
                if hasattr(r, "description") and r.description:
                    entry["description"] = r.description
                if hasattr(r, "mimeType") and r.mimeType:
                    entry["mimeType"] = r.mimeType
                resources.append(entry)
            return json.dumps({"resources": resources}, ensure_ascii=False)

        def _call_once():
            return _run_on_mcp_loop(_call(), timeout=tool_timeout)

        try:
            return _call_once()
        except InterruptedError:
            return _interrupted_call_result()
        except Exception as exc:
            recovered = _handle_auth_error_and_retry(
                server_name, exc, _call_once, "resources/list",
            )
            if recovered is not None:
                return recovered
            logger.error(
                "MCP %s/list_resources failed: %s", server_name, exc,
            )
            return json.dumps({
                "error": _sanitize_error(
                    f"MCP call failed: {type(exc).__name__}: {exc}"
                )
            }, ensure_ascii=False)

    return _handler


def _make_read_resource_handler(server_name: str, tool_timeout: float):
    """返回通过 URI 从 MCP 服务器读取资源的同步处理器。"""

    def _handler(args: dict, **kwargs) -> str:
        from tools.registry import tool_error

        with _lock:
            server = _servers.get(server_name)
        if not server or not server.session:
            return json.dumps({
                "error": f"MCP server '{server_name}' is not connected"
            }, ensure_ascii=False)

        uri = args.get("uri")
        if not uri:
            return tool_error("Missing required parameter 'uri'")

        async def _call():
            result = await server.session.read_resource(uri)
            # read_resource 返回 ReadResourceResult，其中有 .contents 列表
            parts: List[str] = []
            contents = result.contents if hasattr(result, "contents") else []
            for block in contents:
                if hasattr(block, "text"):
                    parts.append(block.text)
                elif hasattr(block, "blob"):
                    parts.append(f"[binary data, {len(block.blob)} bytes]")
            return json.dumps({"result": "\n".join(parts) if parts else ""}, ensure_ascii=False)

        def _call_once():
            return _run_on_mcp_loop(_call(), timeout=tool_timeout)

        try:
            return _call_once()
        except InterruptedError:
            return _interrupted_call_result()
        except Exception as exc:
            recovered = _handle_auth_error_and_retry(
                server_name, exc, _call_once, "resources/read",
            )
            if recovered is not None:
                return recovered
            logger.error(
                "MCP %s/read_resource failed: %s", server_name, exc,
            )
            return json.dumps({
                "error": _sanitize_error(
                    f"MCP call failed: {type(exc).__name__}: {exc}"
                )
            }, ensure_ascii=False)

    return _handler


def _make_list_prompts_handler(server_name: str, tool_timeout: float):
    """返回从 MCP 服务器列出 prompts 的同步处理器。"""

    def _handler(args: dict, **kwargs) -> str:
        with _lock:
            server = _servers.get(server_name)
        if not server or not server.session:
            return json.dumps({
                "error": f"MCP server '{server_name}' is not connected"
            }, ensure_ascii=False)

        async def _call():
            result = await server.session.list_prompts()
            prompts = []
            for p in (result.prompts if hasattr(result, "prompts") else []):
                entry = {}
                if hasattr(p, "name"):
                    entry["name"] = p.name
                if hasattr(p, "description") and p.description:
                    entry["description"] = p.description
                if hasattr(p, "arguments") and p.arguments:
                    entry["arguments"] = [
                        {
                            "name": a.name,
                            **({"description": a.description} if hasattr(a, "description") and a.description else {}),
                            **({"required": a.required} if hasattr(a, "required") else {}),
                        }
                        for a in p.arguments
                    ]
                prompts.append(entry)
            return json.dumps({"prompts": prompts}, ensure_ascii=False)

        def _call_once():
            return _run_on_mcp_loop(_call(), timeout=tool_timeout)

        try:
            return _call_once()
        except InterruptedError:
            return _interrupted_call_result()
        except Exception as exc:
            recovered = _handle_auth_error_and_retry(
                server_name, exc, _call_once, "prompts/list",
            )
            if recovered is not None:
                return recovered
            logger.error(
                "MCP %s/list_prompts failed: %s", server_name, exc,
            )
            return json.dumps({
                "error": _sanitize_error(
                    f"MCP call failed: {type(exc).__name__}: {exc}"
                )
            }, ensure_ascii=False)

    return _handler


def _make_get_prompt_handler(server_name: str, tool_timeout: float):
    """返回通过名称从 MCP 服务器获取 prompt 的同步处理器。"""

    def _handler(args: dict, **kwargs) -> str:
        from tools.registry import tool_error

        with _lock:
            server = _servers.get(server_name)
        if not server or not server.session:
            return json.dumps({
                "error": f"MCP server '{server_name}' is not connected"
            }, ensure_ascii=False)

        name = args.get("name")
        if not name:
            return tool_error("Missing required parameter 'name'")
        arguments = args.get("arguments", {})

        async def _call():
            result = await server.session.get_prompt(name, arguments=arguments)
            # GetPromptResult 有 .messages 列表
            messages = []
            for msg in (result.messages if hasattr(result, "messages") else []):
                entry = {}
                if hasattr(msg, "role"):
                    entry["role"] = msg.role
                if hasattr(msg, "content"):
                    content = msg.content
                    if hasattr(content, "text"):
                        entry["content"] = content.text
                    elif isinstance(content, str):
                        entry["content"] = content
                    else:
                        entry["content"] = str(content)
                messages.append(entry)
            resp = {"messages": messages}
            if hasattr(result, "description") and result.description:
                resp["description"] = result.description
            return json.dumps(resp, ensure_ascii=False)

        def _call_once():
            return _run_on_mcp_loop(_call(), timeout=tool_timeout)

        try:
            return _call_once()
        except InterruptedError:
            return _interrupted_call_result()
        except Exception as exc:
            recovered = _handle_auth_error_and_retry(
                server_name, exc, _call_once, "prompts/get",
            )
            if recovered is not None:
                return recovered
            logger.error(
                "MCP %s/get_prompt failed: %s", server_name, exc,
            )
            return json.dumps({
                "error": _sanitize_error(
                    f"MCP call failed: {type(exc).__name__}: {exc}"
                )
            }, ensure_ascii=False)

    return _handler


def _make_check_fn(server_name: str):
    """返回验证 MCP 连接是否存活的检查函数。"""

    def _check() -> bool:
        with _lock:
            server = _servers.get(server_name)
        return server is not None and server.session is not None

    return _check


# ---------------------------------------------------------------------------
# 发现与注册
# ---------------------------------------------------------------------------

def _normalize_mcp_input_schema(schema: dict | None) -> dict:
    """规范化 MCP 输入 schema 以兼容 LLM 工具调用。"""
    if not schema:
        return {"type": "object", "properties": {}}

    if schema.get("type") == "object" and "properties" not in schema:
        return {**schema, "properties": {}}

    return schema


def sanitize_mcp_name_component(value: str) -> str:
    """返回安全用于工具和前缀生成的 MCP 名称组件。

    保留 Hermes 历史行为——将连字符转换为下划线，
    同时将 ``[A-Za-z0-9_]`` 之外的任何字符替换为 ``_``，
    以使生成的工具名称与提供者验证规则兼容。
    """
    return re.sub(r"[^A-Za-z0-9_]", "_", str(value or ""))


def _convert_mcp_schema(server_name: str, mcp_tool) -> dict:
    """将 MCP 工具列表条目转换为 Hermes 注册表 schema 格式。

    参数：
        server_name: 用于前缀的逻辑服务器名称。
        mcp_tool:    具有 ``.name``、``.description``
                     和 ``.inputSchema`` 的 MCP ``Tool`` 对象。

    返回：
        适用于 ``registry.register(schema=...)`` 的字典。
    """
    safe_tool_name = sanitize_mcp_name_component(mcp_tool.name)
    safe_server_name = sanitize_mcp_name_component(server_name)
    prefixed_name = f"mcp_{safe_server_name}_{safe_tool_name}"
    return {
        "name": prefixed_name,
        "description": mcp_tool.description or f"MCP tool {mcp_tool.name} from {server_name}",
        "parameters": _normalize_mcp_input_schema(mcp_tool.inputSchema),
    }


def _build_utility_schemas(server_name: str) -> List[dict]:
    """构建 MCP 实用工具（resources 和 prompts）的 schema。

    返回 (schema, handler_factory_name) 元组列表，编码为
    具有 schema、handler_key 键的字典。
    """
    safe_name = sanitize_mcp_name_component(server_name)
    return [
        {
            "schema": {
                "name": f"mcp_{safe_name}_list_resources",
                "description": f"List available resources from MCP server '{server_name}'",
                "parameters": {
                    "type": "object",
                    "properties": {},
                },
            },
            "handler_key": "list_resources",
        },
        {
            "schema": {
                "name": f"mcp_{safe_name}_read_resource",
                "description": f"Read a resource by URI from MCP server '{server_name}'",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "uri": {
                            "type": "string",
                            "description": "URI of the resource to read",
                        },
                    },
                    "required": ["uri"],
                },
            },
            "handler_key": "read_resource",
        },
        {
            "schema": {
                "name": f"mcp_{safe_name}_list_prompts",
                "description": f"List available prompts from MCP server '{server_name}'",
                "parameters": {
                    "type": "object",
                    "properties": {},
                },
            },
            "handler_key": "list_prompts",
        },
        {
            "schema": {
                "name": f"mcp_{safe_name}_get_prompt",
                "description": f"Get a prompt by name from MCP server '{server_name}'",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Name of the prompt to retrieve",
                        },
                        "arguments": {
                            "type": "object",
                            "description": "Optional arguments to pass to the prompt",
                        },
                    },
                    "required": ["name"],
                },
            },
            "handler_key": "get_prompt",
        },
    ]


def _normalize_name_filter(value: Any, label: str) -> set[str]:
    """将 include/exclude 配置规范化为工具名称集合。"""
    if value is None:
        return set()
    if isinstance(value, str):
        return {value}
    if isinstance(value, (list, tuple, set)):
        return {str(item) for item in value}
    logger.warning("MCP config %s must be a string or list of strings; ignoring %r", label, value)
    return set()


def _parse_boolish(value: Any, default: bool = True) -> bool:
    """解析类布尔值的配置值，失败时安全回退。"""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off"}:
            return False
    logger.warning("MCP config expected a boolean-ish value, got %r; using default=%s", value, default)
    return default


_UTILITY_CAPABILITY_METHODS = {
    "list_resources": "list_resources",
    "read_resource": "read_resource",
    "list_prompts": "list_prompts",
    "get_prompt": "get_prompt",
}


def _select_utility_schemas(server_name: str, server: MCPServerTask, config: dict) -> List[dict]:
    """根据配置和服务器能力选择实用工具 schema。"""
    tools_filter = config.get("tools") or {}
    resources_enabled = _parse_boolish(tools_filter.get("resources"), default=True)
    prompts_enabled = _parse_boolish(tools_filter.get("prompts"), default=True)

    selected: List[dict] = []
    for entry in _build_utility_schemas(server_name):
        handler_key = entry["handler_key"]
        if handler_key in {"list_resources", "read_resource"} and not resources_enabled:
            logger.debug("MCP server '%s': skipping utility '%s' (resources disabled)", server_name, handler_key)
            continue
        if handler_key in {"list_prompts", "get_prompt"} and not prompts_enabled:
            logger.debug("MCP server '%s': skipping utility '%s' (prompts disabled)", server_name, handler_key)
            continue

        required_method = _UTILITY_CAPABILITY_METHODS[handler_key]
        if not hasattr(server.session, required_method):
            logger.debug(
                "MCP server '%s': skipping utility '%s' (session lacks %s)",
                server_name,
                handler_key,
                required_method,
            )
            continue
        selected.append(entry)
    return selected


def _existing_tool_names() -> List[str]:
    """返回所有当前已连接服务器的工具名称。"""
    names: List[str] = []
    for _sname, server in _servers.items():
        if hasattr(server, "_registered_tool_names"):
            names.extend(server._registered_tool_names)
            continue
        for mcp_tool in server._tools:
            schema = _convert_mcp_schema(server.name, mcp_tool)
            names.append(schema["name"])
    return names


def _register_server_tools(name: str, server: MCPServerTask, config: dict) -> List[str]:
    """将已连接服务器的工具注册到注册表中。

    处理 include/exclude 过滤和实用工具。``mcp-{server}`` 和原始
    服务器名称别名的工具集解析来自实时注册表，而不是在运行时
    修改 ``toolsets.TOOLSETS``。

    用于初始发现和动态刷新（list_changed）。

    返回：
        注册的带前缀工具名称列表。
    """
    from tools.registry import registry

    registered_names: List[str] = []
    toolset_name = f"mcp-{name}"

    # 选择性工具加载：遵循配置中的 include/exclude 列表。
    # 规则（匹配 issue #690 规范）：
    #   tools.include -- 白名单：只注册这些工具名称
    #   tools.exclude -- 黑名单：注册除这些之外的所有工具
    #   include 优先于 exclude
    #   都未设置 → 注册所有工具（向后兼容默认值）
    tools_filter = config.get("tools") or {}
    include_set = _normalize_name_filter(tools_filter.get("include"), f"mcp_servers.{name}.tools.include")
    exclude_set = _normalize_name_filter(tools_filter.get("exclude"), f"mcp_servers.{name}.tools.exclude")

    def _should_register(tool_name: str) -> bool:
        if include_set:
            return tool_name in include_set
        if exclude_set:
            return tool_name not in exclude_set
        return True

    for mcp_tool in server._tools:
        if not _should_register(mcp_tool.name):
            logger.debug("MCP server '%s': skipping tool '%s' (filtered by config)", name, mcp_tool.name)
            continue

        # 扫描工具描述中的提示词注入模式
        _scan_mcp_description(name, mcp_tool.name, mcp_tool.description or "")

        schema = _convert_mcp_schema(name, mcp_tool)
        tool_name_prefixed = schema["name"]

        # 防止与内置（非 MCP）工具冲突。
        existing_toolset = registry.get_toolset_for_tool(tool_name_prefixed)
        if existing_toolset and not existing_toolset.startswith("mcp-"):
            logger.warning(
                "MCP server '%s': tool '%s' (→ '%s') collides with built-in "
                "tool in toolset '%s' — skipping to preserve built-in",
                name, mcp_tool.name, tool_name_prefixed, existing_toolset,
            )
            continue

        registry.register(
            name=tool_name_prefixed,
            toolset=toolset_name,
            schema=schema,
            handler=_make_tool_handler(name, mcp_tool.name, server.tool_timeout),
            check_fn=_make_check_fn(name),
            is_async=False,
            description=schema["description"],
        )
        registered_names.append(tool_name_prefixed)

    # 注册 MCP Resources 和 Prompts 实用工具，根据配置过滤，
    # 且仅在服务器实际支持相应能力时注册。
    _handler_factories = {
        "list_resources": _make_list_resources_handler,
        "read_resource": _make_read_resource_handler,
        "list_prompts": _make_list_prompts_handler,
        "get_prompt": _make_get_prompt_handler,
    }
    check_fn = _make_check_fn(name)
    for entry in _select_utility_schemas(name, server, config):
        schema = entry["schema"]
        handler_key = entry["handler_key"]
        handler = _handler_factories[handler_key](name, server.tool_timeout)
        util_name = schema["name"]

        # 实用工具使用相同的冲突保护。
        existing_toolset = registry.get_toolset_for_tool(util_name)
        if existing_toolset and not existing_toolset.startswith("mcp-"):
            logger.warning(
                "MCP server '%s': utility tool '%s' collides with built-in "
                "tool in toolset '%s' — skipping to preserve built-in",
                name, util_name, existing_toolset,
            )
            continue

        registry.register(
            name=util_name,
            toolset=toolset_name,
            schema=schema,
            handler=handler,
            check_fn=check_fn,
            is_async=False,
            description=schema["description"],
        )
        registered_names.append(util_name)

    if registered_names:
        registry.register_toolset_alias(name, toolset_name)

    return registered_names


async def _discover_and_register_server(name: str, config: dict) -> List[str]:
    """连接到单个 MCP 服务器，发现工具并注册它们。

    返回注册的工具名称列表。
    """
    connect_timeout = config.get("connect_timeout", _DEFAULT_CONNECT_TIMEOUT)
    server = await asyncio.wait_for(
        _connect_server(name, config),
        timeout=connect_timeout,
    )
    with _lock:
        _servers[name] = server

    registered_names = _register_server_tools(name, server, config)
    server._registered_tool_names = list(registered_names)

    transport_type = "HTTP" if "url" in config else "stdio"
    logger.info(
        "MCP server '%s' (%s): registered %d tool(s): %s",
        name, transport_type, len(registered_names),
        ", ".join(registered_names),
    )
    return registered_names


# ---------------------------------------------------------------------------
# 公共 API
# ---------------------------------------------------------------------------

def register_mcp_servers(servers: Dict[str, dict]) -> List[str]:
    """连接到显式 MCP 服务器并注册其工具。

    对已连接的服务器名称具有幂等性。``enabled: false`` 的服务器
    会被跳过而不断开现有会话。

    参数：
        servers: ``{server_name: server_config}`` 映射。

    返回：
        所有当前注册的 MCP 工具名称列表。
    """
    if not _MCP_AVAILABLE:
        logger.debug("MCP SDK not available -- skipping explicit MCP registration")
        return []

    if not servers:
        logger.debug("No explicit MCP servers provided")
        return []

    # 只尝试尚未连接且已启用的服务器
    # （enabled: false 完全跳过服务器而不移除其配置）
    with _lock:
        new_servers = {
            k: v
            for k, v in servers.items()
            if k not in _servers and _parse_boolish(v.get("enabled", True), default=True)
        }

    if not new_servers:
        return _existing_tool_names()

    # 启动 MCP 连接的后台事件循环
    _ensure_mcp_loop()

    async def _discover_one(name: str, cfg: dict) -> List[str]:
        """连接到单个服务器并返回其注册的工具名称。"""
        return await _discover_and_register_server(name, cfg)

    async def _discover_all():
        server_names = list(new_servers.keys())
        # 并行连接所有服务器
        results = await asyncio.gather(
            *(_discover_one(name, cfg) for name, cfg in new_servers.items()),
            return_exceptions=True,
        )
        for name, result in zip(server_names, results):
            if isinstance(result, Exception):
                command = new_servers.get(name, {}).get("command")
                logger.warning(
                    "Failed to connect to MCP server '%s'%s: %s",
                    name,
                    f" (command={command})" if command else "",
                    _format_connect_error(result),
                )

    # 每个服务器的超时在 _discover_and_register_server 内部处理。
    # 外部超时比较宽松：并行发现总共 120 秒。
    _run_on_mcp_loop(_discover_all(), timeout=120)

    # 记录摘要以便 ACP 调用者了解注册了什么。
    with _lock:
        connected = [n for n in new_servers if n in _servers]
        new_tool_count = sum(
            len(getattr(_servers[n], "_registered_tool_names", []))
            for n in connected
        )
    failed = len(new_servers) - len(connected)
    if new_tool_count or failed:
        summary = f"MCP: registered {new_tool_count} tool(s) from {len(connected)} server(s)"
        if failed:
            summary += f" ({failed} failed)"
        logger.info(summary)

    return _existing_tool_names()


def discover_mcp_tools() -> List[str]:
    """入口点：加载配置，连接 MCP 服务器，注册工具。

    在 ``discover_builtin_tools()`` 之后从 ``model_tools`` 调用。
    即使 ``mcp`` 包未安装也可安全调用（返回空列表）。

    对已连接的服务器具有幂等性。如果某些服务器在之前的调用中失败，
    只重试缺失的服务器。

    返回：
        所有注册的 MCP 工具名称列表。
    """
    if not _MCP_AVAILABLE:
        logger.debug("MCP SDK not available -- skipping MCP tool discovery")
        return []

    servers = _load_mcp_config()
    if not servers:
        logger.debug("No MCP servers configured")
        return []

    with _lock:
        new_server_names = [
            name
            for name, cfg in servers.items()
            if name not in _servers and _parse_boolish(cfg.get("enabled", True), default=True)
        ]

    tool_names = register_mcp_servers(servers)
    if not new_server_names:
        return tool_names

    with _lock:
        connected_server_names = [name for name in new_server_names if name in _servers]
        new_tool_count = sum(
            len(getattr(_servers[name], "_registered_tool_names", []))
            for name in connected_server_names
        )

    failed_count = len(new_server_names) - len(connected_server_names)
    if new_tool_count or failed_count:
        summary = f"  MCP: {new_tool_count} tool(s) from {len(connected_server_names)} server(s)"
        if failed_count:
            summary += f" ({failed_count} failed)"
        logger.info(summary)

    return tool_names


def get_mcp_status() -> List[dict]:
    """返回所有已配置 MCP 服务器的状态，用于横幅显示。

    返回字典列表，包含 name、transport、tools、connected 键。
    包括成功连接的服务器和已配置但失败的服务器。
    """
    result: List[dict] = []

    # 从配置中获取已配置的服务器
    configured = _load_mcp_config()
    if not configured:
        return result

    with _lock:
        active_servers = dict(_servers)

    for name, cfg in configured.items():
        transport = "http" if "url" in cfg else "stdio"
        server = active_servers.get(name)
        if server and server.session is not None:
            entry = {
                "name": name,
                "transport": transport,
                "tools": len(server._registered_tool_names) if hasattr(server, "_registered_tool_names") else len(server._tools),
                "connected": True,
            }
            if server._sampling:
                entry["sampling"] = dict(server._sampling.metrics)
            result.append(entry)
        else:
            result.append({
                "name": name,
                "transport": transport,
                "tools": 0,
                "connected": False,
            })

    return result


def probe_mcp_server_tools() -> Dict[str, List[tuple]]:
    """临时连接到已配置的 MCP 服务器并列出其工具。

    专为 ``hermes tools`` 交互式配置设计 -- 连接到每个启用的服务器，
    获取工具名称和描述，然后断开连接。
    不会在 Hermes 注册表中注册工具。

    返回：
        服务器名称到 (tool_name, description) 元组列表的字典映射。
        连接失败的服务器不会出现在结果中。
    """
    if not _MCP_AVAILABLE:
        return {}

    servers_config = _load_mcp_config()
    if not servers_config:
        return {}

    enabled = {
        k: v for k, v in servers_config.items()
        if _parse_boolish(v.get("enabled", True), default=True)
    }
    if not enabled:
        return {}

    _ensure_mcp_loop()

    result: Dict[str, List[tuple]] = {}
    probed_servers: List[MCPServerTask] = []

    async def _probe_all():
        names = list(enabled.keys())
        coros = []
        for name, cfg in enabled.items():
            ct = cfg.get("connect_timeout", _DEFAULT_CONNECT_TIMEOUT)
            coros.append(asyncio.wait_for(_connect_server(name, cfg), timeout=ct))

        outcomes = await asyncio.gather(*coros, return_exceptions=True)

        for name, outcome in zip(names, outcomes):
            if isinstance(outcome, Exception):
                logger.debug("Probe: failed to connect to '%s': %s", name, outcome)
                continue
            probed_servers.append(outcome)
            tools = []
            for t in outcome._tools:
                desc = getattr(t, "description", "") or ""
                tools.append((t.name, desc))
            result[name] = tools

        # 关闭所有已探测的连接
        await asyncio.gather(
            *(s.shutdown() for s in probed_servers),
            return_exceptions=True,
        )

    try:
        _run_on_mcp_loop(_probe_all(), timeout=120)
    except Exception as exc:
        logger.debug("MCP probe failed: %s", exc)
    finally:
        _stop_mcp_loop()

    return result


def shutdown_mcp_servers():
    """关闭所有 MCP 服务器连接并停止后台循环。

    每个服务器 Task 都收到退出其 ``async with`` 块的信号，
    以确保 anyio 取消域清理发生在打开它的同一 Task 中。
    所有服务器通过 ``asyncio.gather`` 并行关闭。
    """
    with _lock:
        servers_snapshot = list(_servers.values())

    # 快速路径：没有需要关闭的内容。
    if not servers_snapshot:
        _stop_mcp_loop()
        return

    async def _shutdown():
        results = await asyncio.gather(
            *(server.shutdown() for server in servers_snapshot),
            return_exceptions=True,
        )
        for server, result in zip(servers_snapshot, results):
            if isinstance(result, Exception):
                logger.debug(
                    "Error closing MCP server '%s': %s", server.name, result,
                )
        with _lock:
            _servers.clear()

    with _lock:
        loop = _mcp_loop
    if loop is not None and loop.is_running():
        try:
            future = asyncio.run_coroutine_threadsafe(_shutdown(), loop)
            future.result(timeout=15)
        except Exception as exc:
            logger.debug("Error during MCP shutdown: %s", exc)

    _stop_mcp_loop()


def _kill_orphaned_mcp_children() -> None:
    """尽力终止在循环关闭后存活的 MCP stdio 子进程。

    MCP 事件循环停止后，stdio 服务器子进程*应该*已被 SDK 的上下文管理器
    清理终止。如果循环卡住或关闭超时，孤儿子进程可能会残留。

    仅终止在 ``_stdio_pids`` 中跟踪的 PID -- 绝不终止任意子进程。
    """
    import signal as _signal
    kill_signal = getattr(_signal, "SIGKILL", _signal.SIGTERM)

    with _lock:
        pids = list(_stdio_pids)
        _stdio_pids.clear()

    for pid in pids:
        try:
            os.kill(pid, kill_signal)
            logger.debug("Force-killed orphaned MCP stdio process %d", pid)
        except (ProcessLookupError, PermissionError, OSError):
            pass  # 进程已退出或不可访问


def _stop_mcp_loop():
    """停止后台事件循环并等待其线程结束。"""
    global _mcp_loop, _mcp_thread
    with _lock:
        loop = _mcp_loop
        thread = _mcp_thread
        _mcp_loop = None
        _mcp_thread = None
    if loop is not None:
        loop.call_soon_threadsafe(loop.stop)
        if thread is not None:
            thread.join(timeout=5)
        try:
            loop.close()
        except Exception:
            pass
        # 关闭循环后，任何在优雅关闭中存活的 stdio 子进程
        # 现在都成了孤儿。强制终止它们。
        _kill_orphaned_mcp_children()
