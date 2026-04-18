"""Honcho 客户端初始化和配置。

配置文件解析顺序：
  1. $HERMES_HOME/honcho.json（实例本地，支持隔离的 Hermes 实例）
  2. ~/.honcho/config.json（全局，所有启用 Honcho 的应用共享）
  3. 环境变量（HONCHO_API_KEY、HONCHO_ENVIRONMENT）

主机特定设置解析顺序：
  1. 显式主机块字段（始终优先）
  2. 配置根节点的扁平/全局字段
  3. 默认值（主机名作为 workspace/peer）
"""

from __future__ import annotations

import json
import os
import logging
from dataclasses import dataclass, field
from pathlib import Path

from hermes_constants import get_hermes_home
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from honcho import Honcho

logger = logging.getLogger(__name__)

GLOBAL_CONFIG_PATH = Path.home() / ".honcho" / "config.json"
HOST = "hermes"


def resolve_active_host() -> str:
    """从活跃的 Hermes 配置文件派生 Honcho 主机键。

    解析顺序：
      1. HERMES_HONCHO_HOST 环境变量（显式覆盖）
      2. 通过配置文件系统获取活跃配置文件名 -> ``hermes.<profile>``
      3. 回退：``"hermes"``（默认配置文件）
    """
    explicit = os.environ.get("HERMES_HONCHO_HOST", "").strip()
    if explicit:
        return explicit

    try:
        from hermes_cli.profiles import get_active_profile_name
        profile = get_active_profile_name()
        if profile and profile not in ("default", "custom"):
            return f"{HOST}.{profile}"
    except Exception:
        pass
    return HOST


def resolve_config_path() -> Path:
    """返回活跃的 Honcho 配置文件路径。

    解析顺序：
      1. $HERMES_HOME/honcho.json      （配置文件本地，如存在）
      2. ~/.hermes/honcho.json          （默认配置文件 — 共享主机块位于此处）
      3. ~/.honcho/config.json          （全局，跨应用互操作）

    如果都不存在则返回全局路径（用于首次设置写入）。
    """
    local_path = get_hermes_home() / "honcho.json"
    if local_path.exists():
        return local_path

    # 默认配置文件的配置 — 主机块通过 setup/clone 在此累积
    default_path = Path.home() / ".hermes" / "honcho.json"
    if default_path != local_path and default_path.exists():
        return default_path

    return GLOBAL_CONFIG_PATH


_RECALL_MODE_ALIASES = {"auto": "hybrid"}
_VALID_RECALL_MODES = {"hybrid", "context", "tools"}


def _normalize_recall_mode(val: str) -> str:
    """规范化旧版的 recall mode 值（例如 'auto' -> 'hybrid'）。"""
    val = _RECALL_MODE_ALIASES.get(val, val)
    return val if val in _VALID_RECALL_MODES else "hybrid"


def _resolve_bool(host_val, root_val, *, default: bool) -> bool:
    """解析布尔配置字段：主机块优先，然后根节点，最后默认值。"""
    if host_val is not None:
        return bool(host_val)
    if root_val is not None:
        return bool(root_val)
    return default


def _parse_context_tokens(host_val, root_val) -> int | None:
    """解析 contextTokens：主机块优先，然后根节点，最后 None（无上限）。"""
    for val in (host_val, root_val):
        if val is not None:
            try:
                return int(val)
            except (ValueError, TypeError):
                pass
    return None


def _parse_dialectic_depth(host_val, root_val) -> int:
    """解析 dialecticDepth：主机块优先，然后根节点，最后 1。限制在 1-3。"""
    for val in (host_val, root_val):
        if val is not None:
            try:
                return max(1, min(int(val), 3))
            except (ValueError, TypeError):
                pass
    return 1


_VALID_REASONING_LEVELS = ("minimal", "low", "medium", "high", "max")


def _parse_dialectic_depth_levels(host_val, root_val, depth: int) -> list[str] | None:
    """解析 dialecticDepthLevels：每轮推理级别的可选数组。

    未配置时返回 None（使用比例默认值）。
    配置时验证每个级别并截断/填充以匹配深度。
    """
    for val in (host_val, root_val):
        if val is not None and isinstance(val, list):
            levels = [
                lvl if lvl in _VALID_REASONING_LEVELS else "low"
                for lvl in val[:depth]
            ]
            # 如果数组比深度短则用 "low" 填充
            while len(levels) < depth:
                levels.append("low")
            return levels
    return None


def _resolve_optional_float(*values: Any) -> float | None:
    """返回第一个非空值，强制转换为正浮点数。"""
    for value in values:
        if value is None:
            continue
        if isinstance(value, str):
            value = value.strip()
            if not value:
                continue
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            return parsed
    return None


_VALID_OBSERVATION_MODES = {"unified", "directional"}
_OBSERVATION_MODE_ALIASES = {"shared": "unified", "separate": "directional", "cross": "directional"}


def _normalize_observation_mode(val: str) -> str:
    """规范化观察模式值。"""
    val = _OBSERVATION_MODE_ALIASES.get(val, val)
    return val if val in _VALID_OBSERVATION_MODES else "directional"


# 观察预设 — 从旧版字符串模式派生的细粒度布尔值。
# 显式的每对等方配置始终优先于预设。
_OBSERVATION_PRESETS = {
    "directional": {
        "user_observe_me": True, "user_observe_others": True,
        "ai_observe_me": True, "ai_observe_others": True,
    },
    "unified": {
        "user_observe_me": True, "user_observe_others": False,
        "ai_observe_me": False, "ai_observe_others": True,
    },
}


def _resolve_observation(
    mode: str,
    observation_obj: dict | None,
) -> dict:
    """解析每对等方的观察布尔值。

    配置形式：
      字符串简写：  ``"observationMode": "directional"``
      细粒度对象：  ``"observation": {"user": {"observeMe": true, "observeOthers": true},
                                     "ai": {"observeMe": true, "observeOthers": false}}``

    细粒度字段覆盖预设默认值。
    """
    preset = _OBSERVATION_PRESETS.get(mode, _OBSERVATION_PRESETS["directional"])
    if not observation_obj or not isinstance(observation_obj, dict):
        return dict(preset)

    user_block = observation_obj.get("user") or {}
    ai_block = observation_obj.get("ai") or {}

    return {
        "user_observe_me": user_block.get("observeMe", preset["user_observe_me"]),
        "user_observe_others": user_block.get("observeOthers", preset["user_observe_others"]),
        "ai_observe_me": ai_block.get("observeMe", preset["ai_observe_me"]),
        "ai_observe_others": ai_block.get("observeOthers", preset["ai_observe_others"]),
    }





@dataclass
class HonchoClientConfig:
    """Honcho 客户端配置，针对特定主机解析。"""

    host: str = HOST
    workspace_id: str = "hermes"
    api_key: str | None = None
    environment: str = "production"
    # 可选的基础 URL，用于自托管 Honcho（覆盖环境映射）
    base_url: str | None = None
    # 可选的请求超时（秒），用于 Honcho SDK HTTP 调用
    timeout: float | None = None
    # 身份标识
    peer_name: str | None = None
    ai_peer: str = "hermes"
    # 开关
    enabled: bool = False
    save_messages: bool = True
    # 写入频率："async"（后台线程）、"turn"（每轮同步）、
    # "session"（仅会话结束时刷新）、或 int（每 N 轮）
    write_frequency: str | int = "async"
    # 预取预算（None = 无上限；设为整数以限制自动注入的上下文）
    context_tokens: int | None = None
    # 辩证（peer.chat）设置
    # reasoning_level: "minimal" | "low" | "medium" | "high" | "max"
    dialectic_reasoning_level: str = "low"
    # 为 true 时，模型可通过 honcho_reasoning 工具参数按调用覆盖
    # reasoning_level（智能体式）。为 false 时始终使用
    # dialecticReasoningLevel，忽略模型提供的覆盖。
    dialectic_dynamic: bool = True
    # 注入到 Hermes 系统提示中的辩证结果最大字符数
    dialectic_max_chars: int = 600
    # 辩证深度：每个辩证周期的 .chat() 调用次数（1-3）。
    # 深度 1：单次调用。深度 2：自审计 + 定向综合。
    # 深度 3：自审计 + 综合 + 调和。
    dialectic_depth: int = 1
    # 可选的每轮推理级别覆盖。推理级别数组
    # 匹配 dialectic_depth 长度。为 None 时使用从
    # dialectic_reasoning_level 派生的比例默认值。
    dialectic_depth_levels: list[str] | None = None
    # Honcho API 限制 — 可为自托管实例配置
    # 通过 add_messages() 发送的每条消息最大字符数（Honcho 云：25000）
    message_max_chars: int = 25000
    # 辩证查询输入到 peer.chat() 的最大字符数（Honcho 云：10000）
    dialectic_max_input_chars: int = 10000
    # 召回模式：Honcho 激活时记忆检索的工作方式。
    # "hybrid"  — 自动注入上下文 + Honcho 工具可用（模型决定）
    # "context" — 仅自动注入上下文，Honcho 工具移除
    # "tools"   — 仅 Honcho 工具，不自动注入上下文
    recall_mode: str = "hybrid"
    # tools 模式下的立即初始化 — 为 true 时在 initialize() 期间
    # 初始化会话而非延迟到首次工具调用
    init_on_session_start: bool = False
    # 观察模式：旧版字符串简写（"directional" 或 "unified"）。
    # 保留用于向后兼容；下面的细粒度每对等方布尔值是首选。
    observation_mode: str = "directional"
    # 每对等方观察布尔值 — 与 Honcho 的 SessionPeerConfig 一一映射。
    # 从配置中的 "observation" 对象解析，回退到 observation_mode 预设。
    user_observe_me: bool = True
    user_observe_others: bool = True
    ai_observe_me: bool = True
    ai_observe_others: bool = True
    # 会话解析
    session_strategy: str = "per-directory"
    session_peer_prefix: bool = False
    sessions: dict[str, str] = field(default_factory=dict)
    # 原始全局配置，供消费者需要的其他内容使用
    raw: dict[str, Any] = field(default_factory=dict)
    # 当 Honcho 为此主机被显式配置时为 True（hosts.hermes
    # 块存在或 enabled 被显式设置），与从零散的
    # HONCHO_API_KEY 环境变量自动启用相区分。
    explicitly_configured: bool = False

    @classmethod
    def from_env(
        cls,
        workspace_id: str = "hermes",
        host: str | None = None,
    ) -> HonchoClientConfig:
        """从环境变量创建配置（回退方式）。"""
        resolved_host = host or resolve_active_host()
        api_key = os.environ.get("HONCHO_API_KEY")
        base_url = os.environ.get("HONCHO_BASE_URL", "").strip() or None
        timeout = _resolve_optional_float(os.environ.get("HONCHO_TIMEOUT"))
        return cls(
            host=resolved_host,
            workspace_id=workspace_id,
            api_key=api_key,
            environment=os.environ.get("HONCHO_ENVIRONMENT", "production"),
            base_url=base_url,
            timeout=timeout,
            ai_peer=resolved_host,
            enabled=bool(api_key or base_url),
        )

    @classmethod
    def from_global_config(
        cls,
        host: str | None = None,
        config_path: Path | None = None,
    ) -> HonchoClientConfig:
        """从解析的 Honcho 配置路径创建配置。

        解析顺序：$HERMES_HOME/honcho.json -> ~/.honcho/config.json -> 环境变量。
        当 host 为 None 时，从活跃的 Hermes 配置文件派生。
        """
        resolved_host = host or resolve_active_host()
        path = config_path or resolve_config_path()
        if not path.exists():
            logger.debug("No global Honcho config at %s, falling back to env", path)
            return cls.from_env(host=resolved_host)

        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to read %s: %s, falling back to env", path, e)
            return cls.from_env(host=resolved_host)

        host_block = (raw.get("hosts") or {}).get(resolved_host, {})
        # hosts.hermes 块或显式 enabled 标志意味着用户
        # 有意为此主机配置了 Honcho。
        _explicitly_configured = bool(host_block) or raw.get("enabled") is True

        # 显式主机块字段优先，然后扁平/全局，最后默认值
        workspace = (
            host_block.get("workspace")
            or raw.get("workspace")
            or resolved_host
        )
        ai_peer = (
            host_block.get("aiPeer")
            or raw.get("aiPeer")
            or resolved_host
        )
        api_key = (
            host_block.get("apiKey")
            or raw.get("apiKey")
            or os.environ.get("HONCHO_API_KEY")
        )

        environment = (
            host_block.get("environment")
            or raw.get("environment", "production")
        )

        base_url = (
            raw.get("baseUrl")
            or raw.get("base_url")
            or os.environ.get("HONCHO_BASE_URL", "").strip()
            or None
        )
        timeout = _resolve_optional_float(
            raw.get("timeout"),
            raw.get("requestTimeout"),
            os.environ.get("HONCHO_TIMEOUT"),
        )

        # 当 API 密钥或 base_url 存在时自动启用（除非显式禁用）
        # 主机级 enabled 优先，然后根级，最后在有密钥/URL 时自动启用。
        host_enabled = host_block.get("enabled")
        root_enabled = raw.get("enabled")
        if host_enabled is not None:
            enabled = host_enabled
        elif root_enabled is not None:
            enabled = root_enabled
        else:
            # 未在任何地方显式设置 -> 有 API 密钥或 base_url 时自动启用
            enabled = bool(api_key or base_url)

        # write_frequency: 接受 int 或 string
        raw_wf = (
            host_block.get("writeFrequency")
            or raw.get("writeFrequency")
            or "async"
        )
        try:
            write_frequency: str | int = int(raw_wf)
        except (TypeError, ValueError):
            write_frequency = str(raw_wf)

        # saveMessages: 主机块优先（注意 None 与 False 的区别）
        host_save = host_block.get("saveMessages")
        save_messages = host_save if host_save is not None else raw.get("saveMessages", True)

        # sessionStrategy / sessionPeerPrefix: 主机块优先，根节点回退
        session_strategy = (
            host_block.get("sessionStrategy")
            or raw.get("sessionStrategy", "per-directory")
        )
        host_prefix = host_block.get("sessionPeerPrefix")
        session_peer_prefix = (
            host_prefix if host_prefix is not None
            else raw.get("sessionPeerPrefix", False)
        )

        return cls(
            host=resolved_host,
            workspace_id=workspace,
            api_key=api_key,
            environment=environment,
            base_url=base_url,
            timeout=timeout,
            peer_name=host_block.get("peerName") or raw.get("peerName"),
            ai_peer=ai_peer,
            enabled=enabled,
            save_messages=save_messages,
            write_frequency=write_frequency,
            context_tokens=_parse_context_tokens(
                host_block.get("contextTokens"),
                raw.get("contextTokens"),
            ),
            dialectic_reasoning_level=(
                host_block.get("dialecticReasoningLevel")
                or raw.get("dialecticReasoningLevel")
                or "low"
            ),
            dialectic_dynamic=_resolve_bool(
                host_block.get("dialecticDynamic"),
                raw.get("dialecticDynamic"),
                default=True,
            ),
            dialectic_max_chars=int(
                host_block.get("dialecticMaxChars")
                or raw.get("dialecticMaxChars")
                or 600
            ),
            dialectic_depth=_parse_dialectic_depth(
                host_block.get("dialecticDepth"),
                raw.get("dialecticDepth"),
            ),
            dialectic_depth_levels=_parse_dialectic_depth_levels(
                host_block.get("dialecticDepthLevels"),
                raw.get("dialecticDepthLevels"),
                depth=_parse_dialectic_depth(host_block.get("dialecticDepth"), raw.get("dialecticDepth")),
            ),
            message_max_chars=int(
                host_block.get("messageMaxChars")
                or raw.get("messageMaxChars")
                or 25000
            ),
            dialectic_max_input_chars=int(
                host_block.get("dialecticMaxInputChars")
                or raw.get("dialecticMaxInputChars")
                or 10000
            ),
            recall_mode=_normalize_recall_mode(
                host_block.get("recallMode")
                or raw.get("recallMode")
                or "hybrid"
            ),
            init_on_session_start=_resolve_bool(
                host_block.get("initOnSessionStart"),
                raw.get("initOnSessionStart"),
                default=False,
            ),
            # 迁移保护：没有显式 observationMode 的现有配置
            # 保留旧的 "unified" 默认值，以免用户被静默切换到
            # 全双向观察。新安装（无主机块、无凭据）获得
            # "directional"（所有观察开启）作为新默认值。
            observation_mode=_normalize_observation_mode(
                host_block.get("observationMode")
                or raw.get("observationMode")
                or ("unified" if _explicitly_configured else "directional")
            ),
            **_resolve_observation(
                _normalize_observation_mode(
                    host_block.get("observationMode")
                    or raw.get("observationMode")
                    or ("unified" if _explicitly_configured else "directional")
                ),
                host_block.get("observation") or raw.get("observation"),
            ),
            session_strategy=session_strategy,
            session_peer_prefix=session_peer_prefix,
            sessions=raw.get("sessions", {}),
            raw=raw,
            explicitly_configured=_explicitly_configured,
        )

    @staticmethod
    def _git_repo_name(cwd: str) -> str | None:
        """返回 git 仓库根目录名称，如果不在仓库中则返回 None。"""
        import subprocess

        try:
            root = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                capture_output=True, text=True, cwd=cwd, timeout=5,
            )
            if root.returncode == 0:
                return Path(root.stdout.strip()).name
        except (OSError, subprocess.TimeoutExpired):
            pass
        return None

    def resolve_session_name(
        self,
        cwd: str | None = None,
        session_title: str | None = None,
        session_id: str | None = None,
        gateway_session_key: str | None = None,
    ) -> str | None:
        """解析 Honcho 会话名称。

        解析顺序：
          1. 会话映射中的手动目录覆盖
          2. Hermes 会话标题（来自 /title 命令）
          3. 网关会话键（来自网关平台的每聊天稳定标识符）
          4. per-session 策略 — Hermes session_id ({timestamp}_{hex})
          5. per-repo 策略 — git 仓库根目录名
          6. per-directory 策略 — 目录基本名
          7. global 策略 — workspace 名称
        """
        import re

        if not cwd:
            cwd = os.getcwd()

        # 手动覆盖始终优先
        manual = self.sessions.get(cwd)
        if manual:
            return manual

        # /title 会话中途重映射
        if session_title:
            sanitized = re.sub(r'[^a-zA-Z0-9_-]+', '-', session_title).strip('-')
            if sanitized:
                if self.session_peer_prefix and self.peer_name:
                    return f"{self.peer_name}-{sanitized}"
                return sanitized

        # 网关会话键：由网关传递的每聊天稳定标识符
        # （例如 "agent:main:telegram:dm:8439114563"）。将冒号替换为连字符
        # 以兼容 Honcho 会话 ID。此项优先于基于策略的
        # 解析，因为网关平台需要 cwd 策略无法提供的每聊天隔离。
        if gateway_session_key:
            sanitized = re.sub(r'[^a-zA-Z0-9_-]+', '-', gateway_session_key).strip('-')
            if sanitized:
                return sanitized

        # per-session: 继承 Hermes session_id（每次运行创建新 Honcho 会话）
        if self.session_strategy == "per-session" and session_id:
            if self.session_peer_prefix and self.peer_name:
                return f"{self.peer_name}-{session_id}"
            return session_id

        # per-repo: 每个 git 仓库一个 Honcho 会话
        if self.session_strategy == "per-repo":
            base = self._git_repo_name(cwd) or Path(cwd).name
            if self.session_peer_prefix and self.peer_name:
                return f"{self.peer_name}-{base}"
            return base

        # per-directory: 每个工作目录一个 Honcho 会话（默认）
        if self.session_strategy in ("per-directory", "per-session"):
            base = Path(cwd).name
            if self.session_peer_prefix and self.peer_name:
                return f"{self.peer_name}-{base}"
            return base

        # global: 跨所有目录的单一会话
        return self.workspace_id


_honcho_client: Honcho | None = None


def get_honcho_client(config: HonchoClientConfig | None = None) -> Honcho:
    """获取或创建 Honcho 客户端单例。

    未提供配置时，先尝试加载 ~/.honcho/config.json，
    然后回退到环境变量。
    """
    global _honcho_client

    if _honcho_client is not None:
        return _honcho_client

    if config is None:
        config = HonchoClientConfig.from_global_config()

    if not config.api_key and not config.base_url:
        raise ValueError(
            "Honcho API key not found. "
            "Get your API key at https://app.honcho.dev, "
            "then run 'hermes honcho setup' or set HONCHO_API_KEY. "
            "For local instances, set HONCHO_BASE_URL instead."
        )

    try:
        from honcho import Honcho
    except ImportError:
        raise ImportError(
            "honcho-ai is required for Honcho integration. "
            "Install it with: pip install honcho-ai"
        )

    # 允许 config.yaml 的 honcho.base_url 覆盖 SDK 的环境映射，
    # 使远程自托管 Honcho 部署无需服务器在 localhost 上运行。
    resolved_base_url = config.base_url
    resolved_timeout = config.timeout
    if not resolved_base_url or resolved_timeout is None:
        try:
            from hermes_cli.config import load_config
            hermes_cfg = load_config()
            honcho_cfg = hermes_cfg.get("honcho", {})
            if isinstance(honcho_cfg, dict):
                if not resolved_base_url:
                    resolved_base_url = honcho_cfg.get("base_url", "").strip() or None
                if resolved_timeout is None:
                    resolved_timeout = _resolve_optional_float(
                        honcho_cfg.get("timeout"),
                        honcho_cfg.get("request_timeout"),
                    )
        except Exception:
            pass

    if resolved_base_url:
        logger.info("Initializing Honcho client (base_url: %s, workspace: %s)", resolved_base_url, config.workspace_id)
    else:
        logger.info("Initializing Honcho client (host: %s, workspace: %s)", config.host, config.workspace_id)

    # 本地 Honcho 实例不需要 API 密钥，但 SDK 需要非空字符串。
    # 对本地 URL 使用占位符。
    # 对于本地：仅在主机块显式设置 apiKey 时使用 config.api_key
    # （意味着用户需要本地认证）。否则跳过存储的密钥 —
    # 它可能是会破坏本地连接的云密钥。
    _is_local = resolved_base_url and (
        "localhost" in resolved_base_url
        or "127.0.0.1" in resolved_base_url
        or "::1" in resolved_base_url
    )
    if _is_local:
        # 检查主机块是否有自己的 apiKey（显式本地认证）
        _raw = config.raw or {}
        _host_block = (_raw.get("hosts") or {}).get(config.host, {})
        _host_has_key = bool(_host_block.get("apiKey"))
        effective_api_key = config.api_key if _host_has_key else "local"
    else:
        effective_api_key = config.api_key

    kwargs: dict = {
        "workspace_id": config.workspace_id,
        "api_key": effective_api_key,
        "environment": config.environment,
    }
    if resolved_base_url:
        kwargs["base_url"] = resolved_base_url
    if resolved_timeout is not None:
        kwargs["timeout"] = resolved_timeout

    _honcho_client = Honcho(**kwargs)

    return _honcho_client


def reset_honcho_client() -> None:
    """重置 Honcho 客户端单例（用于测试）。"""
    global _honcho_client
    _honcho_client = None
