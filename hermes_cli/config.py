"""
Hermes Agent 的配置管理。

配置文件存储在 ~/.hermes/ 目录下，便于访问:
- ~/.hermes/config.yaml  - 所有设置（模型、工具集、终端等）
- ~/.hermes/.env         - API 密钥和敏感信息

本模块提供:
- hermes config          - 显示当前配置
- hermes config edit     - 在编辑器中打开配置
- hermes config set      - 设置特定的值
- hermes config wizard   - 重新运行配置向导
"""

import os
import platform
import re
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple


_IS_WINDOWS = platform.system() == "Windows"
_ENV_VAR_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# 写入 .env 但不在 OPTIONAL_ENV_VARS 中的环境变量名
# （由 setup/provider 流程直接管理）。
_EXTRA_ENV_KEYS = frozenset({
    "OPENAI_API_KEY", "OPENAI_BASE_URL",
    "ANTHROPIC_API_KEY", "ANTHROPIC_TOKEN",
    "DISCORD_HOME_CHANNEL", "TELEGRAM_HOME_CHANNEL",
    "SIGNAL_ACCOUNT", "SIGNAL_HTTP_URL",
    "SIGNAL_ALLOWED_USERS", "SIGNAL_GROUP_ALLOWED_USERS",
    "DINGTALK_CLIENT_ID", "DINGTALK_CLIENT_SECRET",
    "FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_ENCRYPT_KEY", "FEISHU_VERIFICATION_TOKEN",
    "WECOM_BOT_ID", "WECOM_SECRET",
    "WECOM_CALLBACK_CORP_ID", "WECOM_CALLBACK_CORP_SECRET", "WECOM_CALLBACK_AGENT_ID",
    "WECOM_CALLBACK_TOKEN", "WECOM_CALLBACK_ENCODING_AES_KEY",
    "WECOM_CALLBACK_HOST", "WECOM_CALLBACK_PORT",
    "WEIXIN_ACCOUNT_ID", "WEIXIN_TOKEN", "WEIXIN_BASE_URL", "WEIXIN_CDN_BASE_URL",
    "WEIXIN_HOME_CHANNEL", "WEIXIN_HOME_CHANNEL_NAME", "WEIXIN_DM_POLICY", "WEIXIN_GROUP_POLICY",
    "WEIXIN_ALLOWED_USERS", "WEIXIN_GROUP_ALLOWED_USERS", "WEIXIN_ALLOW_ALL_USERS",
    "BLUEBUBBLES_SERVER_URL", "BLUEBUBBLES_PASSWORD",
    "QQ_APP_ID", "QQ_CLIENT_SECRET", "QQ_HOME_CHANNEL", "QQ_HOME_CHANNEL_NAME",
    "QQ_ALLOWED_USERS", "QQ_GROUP_ALLOWED_USERS", "QQ_ALLOW_ALL_USERS", "QQ_MARKDOWN_SUPPORT",
    "QQ_STT_API_KEY", "QQ_STT_BASE_URL", "QQ_STT_MODEL",
    "TERMINAL_ENV", "TERMINAL_SSH_KEY", "TERMINAL_SSH_PORT",
    "WHATSAPP_MODE", "WHATSAPP_ENABLED",
    "MATTERMOST_HOME_CHANNEL", "MATTERMOST_REPLY_MODE",
    "MATRIX_PASSWORD", "MATRIX_ENCRYPTION", "MATRIX_DEVICE_ID", "MATRIX_HOME_ROOM",
    "MATRIX_REQUIRE_MENTION", "MATRIX_FREE_RESPONSE_ROOMS", "MATRIX_AUTO_THREAD",
    "MATRIX_RECOVERY_KEY",
})
import yaml

from hermes_cli.colors import Colors, color
from hermes_cli.default_soul import DEFAULT_SOUL_MD


# =============================================================================
# 托管模式（NixOS 声明式配置）
# =============================================================================

_MANAGED_TRUE_VALUES = ("true", "1", "yes")
_MANAGED_SYSTEM_NAMES = {
    "brew": "Homebrew",
    "homebrew": "Homebrew",
    "nix": "NixOS",
    "nixos": "NixOS",
}


def get_managed_system() -> Optional[str]:
    """返回管理此安装的包管理器名称（如有）。"""
    raw = os.getenv("HERMES_MANAGED", "").strip()
    if raw:
        normalized = raw.lower()
        if normalized in _MANAGED_TRUE_VALUES:
            return "NixOS"
        return _MANAGED_SYSTEM_NAMES.get(normalized, raw)

    managed_marker = get_hermes_home() / ".managed"
    if managed_marker.exists():
        return "NixOS"
    return None


def is_managed() -> bool:
    """检查 Hermes 是否运行在包管理器托管模式下。

    两个信号源：HERMES_MANAGED 环境变量（由 systemd 服务设置），
    或 HERMES_HOME 中的 .managed 标记文件（由 NixOS 激活脚本设置，
    这样交互式 shell 也能检测到）。
    """
    return get_managed_system() is not None


def get_managed_update_command() -> Optional[str]:
    """返回托管安装的首选升级命令。"""
    managed_system = get_managed_system()
    if managed_system == "Homebrew":
        return "brew upgrade hermes-agent"
    if managed_system == "NixOS":
        return "sudo nixos-rebuild switch"
    return None


def recommended_update_command() -> str:
    """返回当前安装方式下最佳的更新命令。"""
    return get_managed_update_command() or "hermes update"


def format_managed_message(action: str = "modify this Hermes installation") -> str:
    """为托管安装生成面向用户的错误信息。"""
    managed_system = get_managed_system() or "a package manager"
    raw = os.getenv("HERMES_MANAGED", "").strip().lower()

    if managed_system == "NixOS":
        env_hint = "true" if raw in _MANAGED_TRUE_VALUES else raw or "true"
        return (
            f"Cannot {action}: this Hermes installation is managed by NixOS "
            f"(HERMES_MANAGED={env_hint}).\n"
            "Edit services.hermes-agent.settings in your configuration.nix and run:\n"
            "  sudo nixos-rebuild switch"
        )

    if managed_system == "Homebrew":
        env_hint = raw or "homebrew"
        return (
            f"Cannot {action}: this Hermes installation is managed by Homebrew "
            f"(HERMES_MANAGED={env_hint}).\n"
            "Use:\n"
            "  brew upgrade hermes-agent"
        )

    return (
        f"Cannot {action}: this Hermes installation is managed by {managed_system}.\n"
        "Use your package manager to upgrade or reinstall Hermes."
    )

def managed_error(action: str = "modify configuration"):
    """在托管模式下打印友好的错误信息。"""
    print(format_managed_message(action), file=sys.stderr)


# =============================================================================
# 容器感知 CLI（NixOS 容器模式）
# =============================================================================

def get_container_exec_info() -> Optional[dict]:
    """从 HERMES_HOME/.container-mode 读取容器模式元数据。

    返回包含 backend、container_name、exec_user、hermes_bin 键的字典，
    如果容器模式未激活、当前已在容器内部或设置了 HERMES_DEV=1 则返回 None。

    .container-mode 文件由 NixOS 激活脚本在 container.enable = true 时写入。
    它告知宿主机 CLI 应该 exec 进入容器而非在本地运行。
    """
    if os.environ.get("HERMES_DEV") == "1":
        return None

    from hermes_constants import is_container
    if is_container():
        return None

    container_mode_file = get_hermes_home() / ".container-mode"

    try:
        info = {}
        with open(container_mode_file, "r") as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    key, _, value = line.partition("=")
                    info[key.strip()] = value.strip()
    except FileNotFoundError:
        return None
    # 所有其他异常（PermissionError、数据格式错误等）向上传播

    backend = info.get("backend", "docker")
    container_name = info.get("container_name", "hermes-agent")
    exec_user = info.get("exec_user", "hermes")
    hermes_bin = info.get("hermes_bin", "/data/current-package/bin/hermes")

    return {
        "backend": backend,
        "container_name": container_name,
        "exec_user": exec_user,
        "hermes_bin": hermes_bin,
    }


# =============================================================================
# 配置路径
# =============================================================================

# 从 hermes_constants 重新导出 -- 规范定义在那里。
from hermes_constants import get_hermes_home  # noqa: F811,E402

def get_config_path() -> Path:
    """获取主配置文件路径。"""
    return get_hermes_home() / "config.yaml"

def get_env_path() -> Path:
    """获取 .env 文件路径（用于 API 密钥）。"""
    return get_hermes_home() / ".env"

def get_project_root() -> Path:
    """获取项目安装目录。"""
    return Path(__file__).parent.parent.resolve()

def _secure_dir(path):
    """将目录设置为仅所有者访问（默认 0700）。Windows 上无操作。

    在托管模式下跳过 -- NixOS 模块设置组可读权限（0750），
    以便 hermes 组中的交互式用户可以与网关服务共享状态。

    可通过 HERMES_HOME_MODE 环境变量覆盖模式
    （如 HERMES_HOME_MODE=0701），适用于 Web 服务器（nginx、caddy 等）
    需要遍历 HERMES_HOME 以访问子目录的部署场景。
    目录上的仅执行位允许 cd 穿过但不暴露目录列表。
    """
    if is_managed():
        return
    try:
        mode_str = os.environ.get("HERMES_HOME_MODE", "").strip()
        mode = int(mode_str, 8) if mode_str else 0o700
    except ValueError:
        mode = 0o700
    try:
        os.chmod(path, mode)
    except (OSError, NotImplementedError):
        pass


def _is_container() -> bool:
    """检测当前是否运行在 Docker/Podman/LXC 容器内。

    当 Hermes 在挂载了配置文件的容器中运行时，强制 0o600 权限会
    破坏网关和仪表板以不同 UID 运行的多进程部署，或卷挂载需要
    更宽松权限的场景。
    """
    # 显式关闭权限检查
    if os.environ.get("HERMES_CONTAINER") or os.environ.get("HERMES_SKIP_CHMOD"):
        return True
    # Docker / Podman 标记文件
    if os.path.exists("/.dockerenv"):
        return True
    # LXC / 基于 cgroup 的检测
    try:
        with open("/proc/1/cgroup", "r") as f:
            cgroup_content = f.read()
        if "docker" in cgroup_content or "lxc" in cgroup_content or "kubepods" in cgroup_content:
            return True
    except (OSError, IOError):
        pass
    return False


def _secure_file(path):
    """将文件设置为仅所有者可读写（0600）。Windows 上无操作。

    在托管模式下跳过 -- NixOS 激活脚本在配置文件上设置
    组可读权限（0640）。

    在容器中跳过 -- Docker/Podman 卷挂载通常需要更宽松的权限。
    在其他系统上设置 HERMES_SKIP_CHMOD=1 强制跳过。
    """
    if is_managed() or _is_container():
        return
    try:
        if os.path.exists(str(path)):
            os.chmod(path, 0o600)
    except (OSError, NotImplementedError):
        pass


def _ensure_default_soul_md(home: Path) -> None:
    """如果用户还没有 SOUL.md，则在 HERMES_HOME 中写入默认值。"""
    soul_path = home / "SOUL.md"
    if soul_path.exists():
        return
    soul_path.write_text(DEFAULT_SOUL_MD, encoding="utf-8")
    _secure_file(soul_path)


def ensure_hermes_home():
    """确保 ~/.hermes 目录结构存在并具有安全的权限。

    在托管模式（NixOS）下，目录由激活脚本创建，带有 setgid + 组可写权限（2770）。
    我们跳过 mkdir 并设置 umask(0o007)，以便创建的文件（如 SOUL.md）是组可写的（0660）。
    """
    home = get_hermes_home()
    if is_managed():
        old_umask = os.umask(0o007)
        try:
            _ensure_hermes_home_managed(home)
        finally:
            os.umask(old_umask)
    else:
        home.mkdir(parents=True, exist_ok=True)
        _secure_dir(home)
        for subdir in ("cron", "sessions", "logs", "memories"):
            d = home / subdir
            d.mkdir(parents=True, exist_ok=True)
            _secure_dir(d)
        _ensure_default_soul_md(home)


def _ensure_hermes_home_managed(home: Path):
    """托管模式变体：验证目录存在（激活时创建），初始化 SOUL.md。"""
    if not home.is_dir():
        raise RuntimeError(
            f"HERMES_HOME {home} does not exist. "
            "Run 'sudo nixos-rebuild switch' first."
        )
    for subdir in ("cron", "sessions", "logs", "memories"):
        d = home / subdir
        if not d.is_dir():
            raise RuntimeError(
                f"{d} does not exist. "
                "Run 'sudo nixos-rebuild switch' first."
            )
    # 在 umask(0o007) 作用域内 — SOUL.md 将以 0660 权限创建
    _ensure_default_soul_md(home)


# =============================================================================
# 配置加载/保存
# =============================================================================

DEFAULT_CONFIG = {
    "model": "",
    "providers": {},
    "fallback_providers": [],
    "credential_pool_strategies": {},
    "toolsets": ["hermes-cli"],
    "agent": {
        "max_turns": 90,
        # 网关 agent 执行的空闲超时（秒）。
        # agent 可以无限运行，只要它正在主动调用工具或接收 API 响应。
        # 仅在 agent 完全空闲达到此时长时触发。0 = 无限制。
        "gateway_timeout": 1800,
        # 网关停止/重启的优雅排空超时（秒）。
        # 网关停止接受新任务，等待运行中的 agent 完成，
        # 超时后中断剩余运行。0 = 不排空，立即中断。
        "restart_drain_timeout": 60,
        "service_tier": "",
        # 工具使用强制：注入系统提示引导，告诉模型
        # 实际调用工具而非描述预期操作。
        # 值："auto"（默认 — 应用于 gpt/codex 模型），true/false
        #（对所有模型强制开/关），或模型名称子字符串列表
        # 用于匹配（例如 ["gpt", "codex", "gemini", "qwen"]）。
        "tool_use_enforcement": "auto",
        # 分阶段空闲警告：在此阈值处向用户发送警告，
        # 然后再升级为完全超时。警告每次运行只触发一次，
        # 不会中断 agent。0 = 禁用警告。
        "gateway_timeout_warning": 900,
        # 定期"仍在工作"通知间隔（秒）。
        # 每 N 秒发送一条状态消息，让用户知道
        # agent 在长时间任务中没有死掉。0 = 禁用通知。
        "gateway_notify_interval": 600,
    },
    
    "terminal": {
        "backend": "local",
        "modal_mode": "auto",
        "cwd": ".",  # 使用当前目录
        "timeout": 180,
        # 传递到沙箱执行环境的环境变量
        # （终端和 execute_code）。技能声明的 required_environment_variables
        # 会自动传递；此列表用于非技能用例。
        "env_passthrough": [],
        "docker_image": "nikolaik/python-nodejs:python3.11-nodejs20",
        "docker_forward_env": [],
        # 在 Docker 容器内设置的显式环境变量。
        # 与 docker_forward_env（从主机进程读取值）不同，
        # docker_env 允许指定精确的键值对 — 适用于 Hermes
        # 作为 systemd 服务运行且无法访问用户 shell 环境的场景。
        # 示例：{"SSH_AUTH_SOCK": "/run/user/1000/ssh-agent.sock"}
        "docker_env": {},
        "singularity_image": "docker://nikolaik/python-nodejs:python3.11-nodejs20",
        "modal_image": "nikolaik/python-nodejs:python3.11-nodejs20",
        "daytona_image": "nikolaik/python-nodejs:python3.11-nodejs20",
        # 容器资源限制（docker、singularity、modal、daytona — 对 local/ssh 无效）
        "container_cpu": 1,
        "container_memory": 5120,       # MB（默认 5GB）
        "container_disk": 51200,        # MB（默认 50GB）
        "container_persistent": True,   # 跨会话持久化文件系统
        # Docker 卷挂载 — 与容器共享主机目录。
        # 每个条目为 "host_path:container_path"（标准 Docker -v 语法）。
        # 示例：["/home/user/projects:/workspace/projects", "/data:/data"]
        "docker_volumes": [],
        # 显式选择加入：将主机 cwd 挂载到 Docker 会话的 /workspace。
        # 默认关闭，因为将主机目录传入沙箱会削弱隔离性。
        "docker_mount_cwd_to_workspace": False,
        # 持久化 shell — 在 execute() 调用之间保持长期运行的 bash shell，
        # 使 cwd/环境变量/shell 变量在命令之间保持不变。
        # 非本地后端（SSH）默认启用；本地模式始终需要通过
        # TERMINAL_LOCAL_PERSISTENT 环境变量显式选择加入。
        "persistent_shell": True,
    },
    
    "browser": {
        "inactivity_timeout": 120,
        "command_timeout": 30,  # 浏览器命令超时秒数（截图、导航等）
        "record_sessions": False,  # 自动录制浏览器会话为 WebM 视频
        "allow_private_urls": False,  # 允许导航到私有/内部 IP（localhost、192.168.x.x 等）
        "camofox": {
            # 为 true 时，Hermes 向 Camofox 发送稳定的 profile 级别 userId，
            # 使服务器自动映射到持久化的 Firefox 配置文件。
            # 为 false（默认）时，每个会话获得随机 userId（临时性的）。
            "managed_persistence": False,
        },
    },

    # 文件系统检查点 — 破坏性文件操作前的自动快照。
    # 启用时，agent 在每个对话轮次中（首次 write_file/patch 调用时）
    # 对工作目录进行一次快照。使用 /rollback 恢复。
    "checkpoints": {
        "enabled": True,
        "max_snapshots": 50,  # 每个目录保留的最大检查点数
    },

    # 单次 read_file 调用返回的最大字符数。超过此限制的读取
    # 将被拒绝并引导使用 offset+limit。
    # 100K 字符 ≈ 在典型分词器中约 25-35K token。
    "file_read_max_chars": 100_000,
    
    "compression": {
        "enabled": True,
        "threshold": 0.50,            # 上下文使用率超过此比例时压缩
        "target_ratio": 0.20,         # 保留为最近尾部的阈值比例
        "protect_last_n": 20,         # 保持不压缩的最少最近消息数

    },

    # AWS Bedrock 提供者配置。
    # 仅在 model.provider 为 "bedrock" 时使用。
    "bedrock": {
        "region": "",  # Bedrock API 调用的 AWS 区域（空 = AWS_REGION 环境变量 → us-east-1）
        "discovery": {
            "enabled": True,           # 通过 ListFoundationModels 自动发现模型
            "provider_filter": [],     # 仅显示这些提供者的模型（例如 ["anthropic", "amazon"]）
            "refresh_interval": 3600,  # 缓存发现结果的秒数
        },
        "guardrail": {
            # Amazon Bedrock Guardrails — 内容过滤和安全策略。
            # 在 Bedrock 控制台创建 guardrail，然后在此设置 ID 和版本。
            # 参见：https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails.html
            "guardrail_identifier": "",  # e.g. "abc123def456"
            "guardrail_version": "",     # e.g. "1" or "DRAFT"
            "stream_processing_mode": "async",  # "sync" or "async"
            "trace": "disabled",         # "enabled", "disabled", or "enabled_full"
        },
    },

    "smart_model_routing": {
        "enabled": False,
        "max_simple_chars": 160,
        "max_simple_words": 28,
        "cheap_model": {},
    },
    
    # 辅助模型配置 — 每个辅助任务的 provider:model。
    # 格式：provider 是提供者名称，model 是模型标识符。
    # provider 为 "auto" = 自动检测最佳可用提供者。
    # model 为空 = 使用提供者的默认辅助模型。
    # 如果配置的提供者不可用，所有任务回退到
    # openrouter:google/gemini-3-flash-preview。
    "auxiliary": {
        "vision": {
            "provider": "auto",    # auto | openrouter | nous | codex | custom
            "model": "",           # 例如 "google/gemini-2.5-flash"、"gpt-4o"
            "base_url": "",        # 直接的 OpenAI 兼容端点（优先于 provider）
            "api_key": "",         # base_url 的 API 密钥（回退到 OPENAI_API_KEY）
            "timeout": 120,        # 秒 — LLM API 调用超时；视觉负载需要较长超时
            "download_timeout": 30,  # 秒 — 图像 HTTP 下载超时；慢连接时增大此值
        },
        "web_extract": {
            "provider": "auto",
            "model": "",
            "base_url": "",
            "api_key": "",
            "timeout": 360,        # 秒（6分钟）— 每次尝试的 LLM 摘要超时；慢速本地模型时增大此值
        },
        "compression": {
            "provider": "auto",
            "model": "",
            "base_url": "",
            "api_key": "",
            "timeout": 120,        # 秒 — 压缩会摘要大型上下文；本地模型时增大此值
        },
        "session_search": {
            "provider": "auto",
            "model": "",
            "base_url": "",
            "api_key": "",
            "timeout": 30,
        },
        "skills_hub": {
            "provider": "auto",
            "model": "",
            "base_url": "",
            "api_key": "",
            "timeout": 30,
        },
        "approval": {
            "provider": "auto",
            "model": "",           # 推荐使用快速/低成本模型（例如 gemini-flash、haiku）
            "base_url": "",
            "api_key": "",
            "timeout": 30,
        },
        "mcp": {
            "provider": "auto",
            "model": "",
            "base_url": "",
            "api_key": "",
            "timeout": 30,
        },
        "flush_memories": {
            "provider": "auto",
            "model": "",
            "base_url": "",
            "api_key": "",
            "timeout": 30,
        },
    },
    
    "display": {
        "compact": False,
        "personality": "kawaii",
        "resume_display": "full",
        "busy_input_mode": "interrupt",
        "bell_on_complete": False,
        "show_reasoning": False,
        "streaming": False,
        "inline_diffs": True,     # 为写入操作显示内联差异预览（write_file、patch、skill_manage）
        "show_cost": False,       # 在状态栏显示 $ 费用（默认关闭）
        "skin": "default",
        "interim_assistant_messages": True,  # 网关：显示自然的中间轮次助手状态消息
        "tool_progress_command": False,  # 在消息网关中启用 /verbose 命令
        "tool_progress_overrides": {},  # 已弃用 — 使用 display.platforms 替代
        "tool_preview_length": 0,  # 工具调用预览的最大字符数（0 = 无限制，显示完整路径/命令）
        "platforms": {},  # 每平台显示覆盖：{"telegram": {"tool_progress": "all"}, "slack": {"tool_progress": "off"}}
    },

    # Web 仪表板设置
    "dashboard": {
        "theme": "default",  # 仪表板视觉主题："default"、"midnight"、"ember"、"mono"、"cyberpunk"、"rose"
    },

    # 隐私设置
    "privacy": {
        "redact_pii": False,  # 为 True 时，对用户 ID 进行哈希处理并从 LLM 上下文中去除电话号码
    },

    # 文本转语音配置
    "tts": {
        "provider": "edge",  # "edge" (free) | "elevenlabs" (premium) | "openai" | "xai" | "minimax" | "mistral" | "neutts" (local)
        "edge": {
            "voice": "en-US-AriaNeural",
            # 热门语音：AriaNeural、JennyNeural、AndrewNeural、BrianNeural、SoniaNeural
        },
        "elevenlabs": {
            "voice_id": "pNInz6obpgDQGcFmaJgB",  # Adam
            "model_id": "eleven_multilingual_v2",
        },
        "openai": {
            "model": "gpt-4o-mini-tts",
            "voice": "alloy",
            # 语音选项：alloy、echo、fable、onyx、nova、shimmer
        },
        "xai": {
            "voice_id": "eve",
            "language": "en",
            "sample_rate": 24000,
            "bit_rate": 128000,
        },
        "mistral": {
            "model": "voxtral-mini-tts-2603",
            "voice_id": "c69964a6-ab8b-4f8a-9465-ec0925096ec8",  # Paul - Neutral
        },
        "neutts": {
            "ref_audio": "",  # 参考语音音频路径（空 = 内置默认值）
            "ref_text": "",   # 参考语音文本路径（空 = 内置默认值）
            "model": "neuphonic/neutts-air-q4-gguf",  # HuggingFace 模型仓库
            "device": "cpu",  # cpu、cuda 或 mps
        },
    },
    
    "stt": {
        "enabled": True,
        "provider": "local",  # "local" (free, faster-whisper) | "groq" | "openai" (Whisper API) | "mistral" (Voxtral Transcribe)
        "local": {
            "model": "base",  # tiny, base, small, medium, large-v3
            "language": "",  # auto-detect by default; set to "en", "es", "fr", etc. to force
        },
        "openai": {
            "model": "whisper-1",  # whisper-1, gpt-4o-mini-transcribe, gpt-4o-transcribe
        },
        "mistral": {
            "model": "voxtral-mini-latest",  # voxtral-mini-latest, voxtral-mini-2602
        },
    },

    "voice": {
        "record_key": "ctrl+b",
        "max_recording_seconds": 120,
        "auto_tts": False,
        "silence_threshold": 200,     # 低于此 RMS 值为静音（0-32767）
        "silence_duration": 3.0,      # 自动停止前的静音秒数
    },
    
    "human_delay": {
        "mode": "off",
        "min_ms": 800,
        "max_ms": 2500,
    },
    
    # 上下文引擎 -- 控制接近模型 token 限制时如何管理上下文窗口。
    # "compressor" = 内置有损摘要（默认）。
    # 设置为插件名称以激活替代引擎（例如 "lcm"
    # 用于无损上下文管理）。引擎必须作为插件安装在
    # plugins/context_engine/<name>/ 或 ~/.hermes/plugins/ 中。
    "context": {
        "engine": "compressor",
    },

    # 持久化记忆 -- 注入系统提示的有界策展记忆
    "memory": {
        "memory_enabled": True,
        "user_profile_enabled": True,
        "memory_char_limit": 2200,   # ~800 tokens at 2.75 chars/token
        "user_char_limit": 1375,     # ~500 tokens at 2.75 chars/token
        # 外部记忆提供者插件（空 = 仅内置）。
        # 设置为提供者名称以激活："openviking"、"mem0"、
        # "hindsight"、"holographic"、"retaindb"、"byterover"。
        # 同时只允许一个外部提供者。
        "provider": "",
    },

    # 子 agent 委派 — 覆盖 delegate_task 使用的 provider:model，
    # 使子 agent 可以在不同的（更便宜/更快的）提供者和模型上运行。
    # 使用与 CLI/网关启动相同的运行时提供者解析，因此所有
    # 已配置的提供者（OpenRouter、Nous、Z.ai、Kimi 等）都支持。
    "delegation": {
        "model": "",       # 例如 "google/gemini-3-flash-preview"（空 = 继承父 agent 模型）
        "provider": "",    # 例如 "openrouter"（空 = 继承父 agent 提供者 + 凭证）
        "base_url": "",    # 子 agent 的直接 OpenAI 兼容端点
        "api_key": "",     # delegation.base_url 的 API 密钥（回退到 OPENAI_API_KEY）
        "max_iterations": 50,  # 每个子 agent 的迭代上限（每个子 agent 有独立预算，
                               # 与父 agent 的 max_iterations 无关）
        "reasoning_effort": "",  # 子 agent 的推理力度："xhigh"、"high"、"medium"、
                                 # "low"、"minimal"、"none"（空 = 继承父 agent 级别）
    },

    # 临时预填充消息文件 — {role, content} 字典的 JSON 列表，
    # 在每次 API 调用开始时注入，用于少样本预热。
    # 不会保存到会话、日志或轨迹中。
    "prefill_messages_file": "",
    
    # 技能 — 用于跨工具/agent 共享技能的外部技能目录。
    # 每个路径会被展开（~、${VAR}）并解析。只读 — 技能创建
    # 始终写入 ~/.hermes/skills/。
    "skills": {
        "external_dirs": [],   # e.g. ["~/.agents/skills", "/shared/team-skills"]
    },

    # Honcho AI 原生记忆 -- 以 ~/.honcho/config.json 为唯一数据源。
    # 此部分仅用于 hermes 特定的覆盖；其他所有内容
    #（apiKey、workspace、peerName、sessions、enabled）来自全局配置。
    "honcho": {},

    # IANA 时区（例如 "Asia/Kolkata"、"America/New_York"）。
    # 空字符串表示使用服务器本地时间。
    "timezone": "",

    # Discord 平台设置（网关模式）
    "discord": {
        "require_mention": True,       # Require @mention to respond in server channels
        "free_response_channels": "",  # Comma-separated channel IDs where bot responds without mention
        "allowed_channels": "",        # If set, bot ONLY responds in these channel IDs (whitelist)
        "auto_thread": True,           # Auto-create threads on @mention in channels (like Slack)
        "reactions": True,             # Add 👀/✅/❌ reactions to messages during processing
        "channel_prompts": {},         # Per-channel ephemeral system prompts (forum parents apply to child threads)
    },

    # WhatsApp 平台设置（网关模式）
    "whatsapp": {
        # 每条 WhatsApp 外发消息前置的回复前缀。
        # 默认（None）使用内置的 "⚕ *Hermes Agent*" 标题。
        # 设为 ""（空字符串）可完全禁用标题。
        # 支持 \n 换行，例如 "🤖 *My Bot*\n──────\n"
    },

    # Telegram 平台设置（网关模式）
    "telegram": {
        "channel_prompts": {},         # Per-chat/topic ephemeral system prompts (topics inherit from parent group)
    },

    # Slack 平台设置（网关模式）
    "slack": {
        "channel_prompts": {},         # Per-channel ephemeral system prompts
    },

    # Mattermost 平台设置（网关模式）
    "mattermost": {
        "channel_prompts": {},         # Per-channel ephemeral system prompts
    },

    # 危险命令审批模式：
    #   manual — 始终提示用户（默认）
    #   smart  — 使用辅助 LLM 自动批准低风险命令，高风险时提示
    #   off    — 跳过所有审批提示（等同于 --yolo）
    "approvals": {
        "mode": "manual",
        "timeout": 60,
    },

    # 永久允许的危险命令模式（通过 "always" 审批添加）
    "command_allowlist": [],
    # 用户定义的快速命令，绕过 agent 循环（仅限 type: exec）
    "quick_commands": {},
    # 自定义人格 — 在此添加自定义条目
    # 支持字符串格式：{"name": "system prompt"}
    # 或字典格式：{"name": {"description": "...", "system_prompt": "...", "tone": "...", "style": "..."}}
    "personalities": {},

    # 通过 tirith 进行预执行安全扫描
    "security": {
        "redact_secrets": True,
        "tirith_enabled": True,
        "tirith_path": "tirith",
        "tirith_timeout": 5,
        "tirith_fail_open": True,
        "website_blocklist": {
            "enabled": False,
            "domains": [],
            "shared_files": [],
        },
    },

    "cron": {
        # 将已投递的 cron 响应包装上标题（任务名称）和页脚
        # （"The agent cannot see this message"）。设为 false 可获得干净输出。
        "wrap_response": True,
    },

    # 日志 — 控制文件日志记录到 ~/.hermes/logs/。
    # agent.log 捕获 INFO+（所有 agent 活动）；errors.log 捕获 WARNING+。
    "logging": {
        "level": "INFO",       # agent.log 的最低级别：DEBUG、INFO、WARNING
        "max_size_mb": 5,      # 轮转前每个日志文件的最大大小
        "backup_count": 3,     # 保留的轮转备份文件数量
    },

    # 网络设置 — 连接问题的解决方案。
    "network": {
        # 强制使用 IPv4 连接。在 IPv6 不可达或损坏的服务器上，
        # Python 会先尝试 AAAA 记录并在整个 TCP 超时期间挂起，
        # 然后才回退到 IPv4。设为 true 可完全跳过 IPv6。
        "force_ipv4": False,
    },

    # 配置 schema 版本 - 添加新的必需字段时递增此值
    "_config_version": 18,
}

# =============================================================================
# 配置迁移系统
# =============================================================================

# 跟踪每个配置版本引入了哪些环境变量。
# 迁移仅提及自用户上一版本以来新增的变量。
ENV_VARS_BY_VERSION: Dict[int, List[str]] = {
    3: ["FIRECRAWL_API_KEY", "BROWSERBASE_API_KEY", "BROWSERBASE_PROJECT_ID", "FAL_KEY"],
    4: ["VOICE_TOOLS_OPENAI_KEY", "ELEVENLABS_API_KEY"],
    5: ["WHATSAPP_ENABLED", "WHATSAPP_MODE", "WHATSAPP_ALLOWED_USERS",
        "SLACK_BOT_TOKEN", "SLACK_APP_TOKEN", "SLACK_ALLOWED_USERS"],
    10: ["TAVILY_API_KEY"],
    11: ["TERMINAL_MODAL_MODE"],
}

# 带有迁移提示元数据的必需环境变量。
# LLM 提供者是必需的，但在设置向导的提供者选择步骤中处理
#（Nous Portal / OpenRouter / 自定义端点），因此此字典故意为空 —
# 没有单个环境变量是普遍必需的。
REQUIRED_ENV_VARS = {}

# 增强功能的可选环境变量
OPTIONAL_ENV_VARS = {
    # ── Provider (handled in provider selection, not shown in checklists) ──
    "NOUS_BASE_URL": {
        "description": "Nous Portal base URL override",
        "prompt": "Nous Portal base URL (leave empty for default)",
        "url": None,
        "password": False,
        "category": "provider",
        "advanced": True,
    },
    "OPENROUTER_API_KEY": {
        "description": "OpenRouter API key (for vision, web scraping helpers, and MoA)",
        "prompt": "OpenRouter API key",
        "url": "https://openrouter.ai/keys",
        "password": True,
        "tools": ["vision_analyze", "mixture_of_agents"],
        "category": "provider",
        "advanced": True,
    },
    "GOOGLE_API_KEY": {
        "description": "Google AI Studio API key (also recognized as GEMINI_API_KEY)",
        "prompt": "Google AI Studio API key",
        "url": "https://aistudio.google.com/app/apikey",
        "password": True,
        "category": "provider",
        "advanced": True,
    },
    "GEMINI_API_KEY": {
        "description": "Google AI Studio API key (alias for GOOGLE_API_KEY)",
        "prompt": "Gemini API key",
        "url": "https://aistudio.google.com/app/apikey",
        "password": True,
        "category": "provider",
        "advanced": True,
    },
    "GEMINI_BASE_URL": {
        "description": "Google AI Studio base URL override",
        "prompt": "Gemini base URL (leave empty for default)",
        "url": None,
        "password": False,
        "category": "provider",
        "advanced": True,
    },
    "XAI_API_KEY": {
        "description": "xAI API key",
        "prompt": "xAI API key",
        "url": "https://console.x.ai/",
        "password": True,
        "category": "provider",
        "advanced": True,
    },
    "XAI_BASE_URL": {
        "description": "xAI base URL override",
        "prompt": "xAI base URL (leave empty for default)",
        "url": None,
        "password": False,
        "category": "provider",
        "advanced": True,
    },
    "GLM_API_KEY": {
        "description": "Z.AI / GLM API key (also recognized as ZAI_API_KEY / Z_AI_API_KEY)",
        "prompt": "Z.AI / GLM API key",
        "url": "https://z.ai/",
        "password": True,
        "category": "provider",
        "advanced": True,
    },
    "ZAI_API_KEY": {
        "description": "Z.AI API key (alias for GLM_API_KEY)",
        "prompt": "Z.AI API key",
        "url": "https://z.ai/",
        "password": True,
        "category": "provider",
        "advanced": True,
    },
    "Z_AI_API_KEY": {
        "description": "Z.AI API key (alias for GLM_API_KEY)",
        "prompt": "Z.AI API key",
        "url": "https://z.ai/",
        "password": True,
        "category": "provider",
        "advanced": True,
    },
    "GLM_BASE_URL": {
        "description": "Z.AI / GLM base URL override",
        "prompt": "Z.AI / GLM base URL (leave empty for default)",
        "url": None,
        "password": False,
        "category": "provider",
        "advanced": True,
    },
    "KIMI_API_KEY": {
        "description": "Kimi / Moonshot API key",
        "prompt": "Kimi API key",
        "url": "https://platform.moonshot.cn/",
        "password": True,
        "category": "provider",
        "advanced": True,
    },
    "KIMI_BASE_URL": {
        "description": "Kimi / Moonshot base URL override",
        "prompt": "Kimi base URL (leave empty for default)",
        "url": None,
        "password": False,
        "category": "provider",
        "advanced": True,
    },
    "KIMI_CN_API_KEY": {
        "description": "Kimi / Moonshot China API key",
        "prompt": "Kimi (China) API key",
        "url": "https://platform.moonshot.cn/",
        "password": True,
        "category": "provider",
        "advanced": True,
    },
    "ARCEEAI_API_KEY": {
        "description": "Arcee AI API key",
        "prompt": "Arcee AI API key",
        "url": "https://chat.arcee.ai/",
        "password": True,
        "category": "provider",
        "advanced": True,
    },
    "ARCEE_BASE_URL": {
        "description": "Arcee AI base URL override",
        "prompt": "Arcee base URL (leave empty for default)",
        "url": None,
        "password": False,
        "category": "provider",
        "advanced": True,
    },
    "MINIMAX_API_KEY": {
        "description": "MiniMax API key (international)",
        "prompt": "MiniMax API key",
        "url": "https://www.minimax.io/",
        "password": True,
        "category": "provider",
        "advanced": True,
    },
    "MINIMAX_BASE_URL": {
        "description": "MiniMax base URL override",
        "prompt": "MiniMax base URL (leave empty for default)",
        "url": None,
        "password": False,
        "category": "provider",
        "advanced": True,
    },
    "MINIMAX_CN_API_KEY": {
        "description": "MiniMax API key (China endpoint)",
        "prompt": "MiniMax (China) API key",
        "url": "https://www.minimaxi.com/",
        "password": True,
        "category": "provider",
        "advanced": True,
    },
    "MINIMAX_CN_BASE_URL": {
        "description": "MiniMax (China) base URL override",
        "prompt": "MiniMax (China) base URL (leave empty for default)",
        "url": None,
        "password": False,
        "category": "provider",
        "advanced": True,
    },
    "DEEPSEEK_API_KEY": {
        "description": "DeepSeek API key for direct DeepSeek access",
        "prompt": "DeepSeek API Key",
        "url": "https://platform.deepseek.com/api_keys",
        "password": True,
        "category": "provider",
    },
    "DEEPSEEK_BASE_URL": {
        "description": "Custom DeepSeek API base URL (advanced)",
        "prompt": "DeepSeek Base URL",
        "url": "",
        "password": False,
        "category": "provider",
    },
    "DASHSCOPE_API_KEY": {
        "description": "Alibaba Cloud DashScope API key (Qwen + multi-provider models)",
        "prompt": "DashScope API Key",
        "url": "https://modelstudio.console.alibabacloud.com/",
        "password": True,
        "category": "provider",
    },
    "DASHSCOPE_BASE_URL": {
        "description": "Custom DashScope base URL (default: coding-intl OpenAI-compat endpoint)",
        "prompt": "DashScope Base URL",
        "url": "",
        "password": False,
        "category": "provider",
        "advanced": True,
    },
    "HERMES_QWEN_BASE_URL": {
        "description": "Qwen Portal base URL override (default: https://portal.qwen.ai/v1)",
        "prompt": "Qwen Portal base URL (leave empty for default)",
        "url": None,
        "password": False,
        "category": "provider",
        "advanced": True,
    },
    "HERMES_GEMINI_CLIENT_ID": {
        "description": "Google OAuth client ID for google-gemini-cli (optional; defaults to Google's public gemini-cli client)",
        "prompt": "Google OAuth client ID (optional — leave empty to use the public default)",
        "url": "https://console.cloud.google.com/apis/credentials",
        "password": False,
        "category": "provider",
        "advanced": True,
    },
    "HERMES_GEMINI_CLIENT_SECRET": {
        "description": "Google OAuth client secret for google-gemini-cli (optional)",
        "prompt": "Google OAuth client secret (optional)",
        "url": "https://console.cloud.google.com/apis/credentials",
        "password": True,
        "category": "provider",
        "advanced": True,
    },
    "HERMES_GEMINI_PROJECT_ID": {
        "description": "GCP project ID for paid Gemini tiers (free tier auto-provisions)",
        "prompt": "GCP project ID for Gemini OAuth (leave empty for free tier)",
        "url": None,
        "password": False,
        "category": "provider",
        "advanced": True,
    },
    "OPENCODE_ZEN_API_KEY": {
        "description": "OpenCode Zen API key (pay-as-you-go access to curated models)",
        "prompt": "OpenCode Zen API key",
        "url": "https://opencode.ai/auth",
        "password": True,
        "category": "provider",
        "advanced": True,
    },
    "OPENCODE_ZEN_BASE_URL": {
        "description": "OpenCode Zen base URL override",
        "prompt": "OpenCode Zen base URL (leave empty for default)",
        "url": None,
        "password": False,
        "category": "provider",
        "advanced": True,
    },
    "OPENCODE_GO_API_KEY": {
        "description": "OpenCode Go API key ($10/month subscription for open models)",
        "prompt": "OpenCode Go API key",
        "url": "https://opencode.ai/auth",
        "password": True,
        "category": "provider",
        "advanced": True,
    },
    "OPENCODE_GO_BASE_URL": {
        "description": "OpenCode Go base URL override",
        "prompt": "OpenCode Go base URL (leave empty for default)",
        "url": None,
        "password": False,
        "category": "provider",
        "advanced": True,
    },
    "HF_TOKEN": {
        "description": "Hugging Face token for Inference Providers (20+ open models via router.huggingface.co)",
        "prompt": "Hugging Face Token",
        "url": "https://huggingface.co/settings/tokens",
        "password": True,
        "category": "provider",
    },
    "HF_BASE_URL": {
        "description": "Hugging Face Inference Providers base URL override",
        "prompt": "HF base URL (leave empty for default)",
        "url": None,
        "password": False,
        "category": "provider",
        "advanced": True,
    },
    "OLLAMA_API_KEY": {
        "description": "Ollama Cloud API key (ollama.com — cloud-hosted open models)",
        "prompt": "Ollama Cloud API key",
        "url": "https://ollama.com/settings",
        "password": True,
        "category": "provider",
        "advanced": True,
    },
    "OLLAMA_BASE_URL": {
        "description": "Ollama Cloud base URL override (default: https://ollama.com/v1)",
        "prompt": "Ollama base URL (leave empty for default)",
        "url": None,
        "password": False,
        "category": "provider",
        "advanced": True,
    },
    "XIAOMI_API_KEY": {
        "description": "Xiaomi MiMo API key for MiMo models (mimo-v2-pro, mimo-v2-omni, mimo-v2-flash)",
        "prompt": "Xiaomi MiMo API Key",
        "url": "https://platform.xiaomimimo.com",
        "password": True,
        "category": "provider",
    },
    "XIAOMI_BASE_URL": {
        "description": "Xiaomi MiMo base URL override (default: https://api.xiaomimimo.com/v1)",
        "prompt": "Xiaomi base URL (leave empty for default)",
        "url": None,
        "password": False,
        "category": "provider",
        "advanced": True,
    },
    "AWS_REGION": {
        "description": "AWS region for Bedrock API calls (e.g. us-east-1, eu-central-1)",
        "prompt": "AWS Region",
        "url": "https://docs.aws.amazon.com/bedrock/latest/userguide/bedrock-regions.html",
        "password": False,
        "category": "provider",
        "advanced": True,
    },
    "AWS_PROFILE": {
        "description": "AWS named profile for Bedrock authentication (from ~/.aws/credentials)",
        "prompt": "AWS Profile",
        "url": None,
        "password": False,
        "category": "provider",
        "advanced": True,
    },

    # ── Tool API keys ──
    "EXA_API_KEY": {
        "description": "Exa API key for AI-native web search and contents",
        "prompt": "Exa API key",
        "url": "https://exa.ai/",
        "tools": ["web_search", "web_extract"],
        "password": True,
        "category": "tool",
    },
    "PARALLEL_API_KEY": {
        "description": "Parallel API key for AI-native web search and extract",
        "prompt": "Parallel API key",
        "url": "https://parallel.ai/",
        "tools": ["web_search", "web_extract"],
        "password": True,
        "category": "tool",
    },
    "FIRECRAWL_API_KEY": {
        "description": "Firecrawl API key for web search and scraping",
        "prompt": "Firecrawl API key",
        "url": "https://firecrawl.dev/",
        "tools": ["web_search", "web_extract"],
        "password": True,
        "category": "tool",
    },
    "FIRECRAWL_API_URL": {
        "description": "Firecrawl API URL for self-hosted instances (optional)",
        "prompt": "Firecrawl API URL (leave empty for cloud)",
        "url": None,
        "password": False,
        "category": "tool",
        "advanced": True,
    },
    "FIRECRAWL_GATEWAY_URL": {
        "description": "Exact Firecrawl tool-gateway origin override for Nous Subscribers only (optional)",
        "prompt": "Firecrawl gateway URL (leave empty to derive from domain)",
        "url": None,
        "password": False,
        "category": "tool",
        "advanced": True,
    },
    "TOOL_GATEWAY_DOMAIN": {
        "description": "Shared tool-gateway domain suffix for Nous Subscribers only, used to derive vendor hosts, e.g. nousresearch.com -> firecrawl-gateway.nousresearch.com",
        "prompt": "Tool-gateway domain suffix",
        "url": None,
        "password": False,
        "category": "tool",
        "advanced": True,
    },
    "TOOL_GATEWAY_SCHEME": {
        "description": "Shared tool-gateway URL scheme for Nous Subscribers only, used to derive vendor hosts (`https` by default, set `http` for local gateway testing)",
        "prompt": "Tool-gateway URL scheme",
        "url": None,
        "password": False,
        "category": "tool",
        "advanced": True,
    },
    "TOOL_GATEWAY_USER_TOKEN": {
        "description": "Explicit Nous Subscriber access token for tool-gateway requests (optional; otherwise read from the Hermes auth store)",
        "prompt": "Tool-gateway user token",
        "url": None,
        "password": True,
        "category": "tool",
        "advanced": True,
    },
    "TAVILY_API_KEY": {
        "description": "Tavily API key for AI-native web search, extract, and crawl",
        "prompt": "Tavily API key",
        "url": "https://app.tavily.com/home",
        "tools": ["web_search", "web_extract", "web_crawl"],
        "password": True,
        "category": "tool",
    },
    "BROWSERBASE_API_KEY": {
        "description": "Browserbase API key for cloud browser (optional — local browser works without this)",
        "prompt": "Browserbase API key",
        "url": "https://browserbase.com/",
        "tools": ["browser_navigate", "browser_click"],
        "password": True,
        "category": "tool",
    },
    "BROWSERBASE_PROJECT_ID": {
        "description": "Browserbase project ID (optional — only needed for cloud browser)",
        "prompt": "Browserbase project ID",
        "url": "https://browserbase.com/",
        "tools": ["browser_navigate", "browser_click"],
        "password": False,
        "category": "tool",
    },
    "BROWSER_USE_API_KEY": {
        "description": "Browser Use API key for cloud browser (optional — local browser works without this)",
        "prompt": "Browser Use API key",
        "url": "https://browser-use.com/",
        "tools": ["browser_navigate", "browser_click"],
        "password": True,
        "category": "tool",
    },
    "FIRECRAWL_BROWSER_TTL": {
        "description": "Firecrawl browser session TTL in seconds (optional, default 300)",
        "prompt": "Browser session TTL (seconds)",
        "tools": ["browser_navigate", "browser_click"],
        "password": False,
        "category": "tool",
    },
    "CAMOFOX_URL": {
        "description": "Camofox browser server URL for local anti-detection browsing (e.g. http://localhost:9377)",
        "prompt": "Camofox server URL",
        "url": "https://github.com/jo-inc/camofox-browser",
        "tools": ["browser_navigate", "browser_click"],
        "password": False,
        "category": "tool",
    },
    "FAL_KEY": {
        "description": "FAL API key for image generation",
        "prompt": "FAL API key",
        "url": "https://fal.ai/",
        "tools": ["image_generate"],
        "password": True,
        "category": "tool",
    },
    "TINKER_API_KEY": {
        "description": "Tinker API key for RL training",
        "prompt": "Tinker API key",
        "url": "https://tinker-console.thinkingmachines.ai/keys",
        "tools": ["rl_start_training", "rl_check_status", "rl_stop_training"],
        "password": True,
        "category": "tool",
    },
    "WANDB_API_KEY": {
        "description": "Weights & Biases API key for experiment tracking",
        "prompt": "WandB API key",
        "url": "https://wandb.ai/authorize",
        "tools": ["rl_get_results", "rl_check_status"],
        "password": True,
        "category": "tool",
    },
    "VOICE_TOOLS_OPENAI_KEY": {
        "description": "OpenAI API key for voice transcription (Whisper) and OpenAI TTS",
        "prompt": "OpenAI API Key (for Whisper STT + TTS)",
        "url": "https://platform.openai.com/api-keys",
        "tools": ["voice_transcription", "openai_tts"],
        "password": True,
        "category": "tool",
    },
    "ELEVENLABS_API_KEY": {
        "description": "ElevenLabs API key for premium text-to-speech voices",
        "prompt": "ElevenLabs API key",
        "url": "https://elevenlabs.io/",
        "password": True,
        "category": "tool",
    },
    "MISTRAL_API_KEY": {
        "description": "Mistral API key for Voxtral TTS and transcription (STT)",
        "prompt": "Mistral API key",
        "url": "https://console.mistral.ai/",
        "password": True,
        "category": "tool",
    },
    "GITHUB_TOKEN": {
        "description": "GitHub token for Skills Hub (higher API rate limits, skill publish)",
        "prompt": "GitHub Token",
        "url": "https://github.com/settings/tokens",
        "password": True,
        "category": "tool",
    },

    # ── Honcho ──
    "HONCHO_API_KEY": {
        "description": "Honcho API key for AI-native persistent memory",
        "prompt": "Honcho API key",
        "url": "https://app.honcho.dev",
        "tools": ["honcho_context"],
        "password": True,
        "category": "tool",
    },
    "HONCHO_BASE_URL": {
        "description": "Base URL for self-hosted Honcho instances (no API key needed)",
        "prompt": "Honcho base URL (e.g. http://localhost:8000)",
        "category": "tool",
    },

    # ── Messaging platforms ──
    "TELEGRAM_BOT_TOKEN": {
        "description": "Telegram bot token from @BotFather",
        "prompt": "Telegram bot token",
        "url": "https://t.me/BotFather",
        "password": True,
        "category": "messaging",
    },
    "TELEGRAM_ALLOWED_USERS": {
        "description": "Comma-separated Telegram user IDs allowed to use the bot (get ID from @userinfobot)",
        "prompt": "Allowed Telegram user IDs (comma-separated)",
        "url": "https://t.me/userinfobot",
        "password": False,
        "category": "messaging",
    },
    "TELEGRAM_PROXY": {
        "description": "Proxy URL for Telegram connections (overrides HTTPS_PROXY). Supports http://, https://, socks5://",
        "prompt": "Telegram proxy URL (optional)",
        "password": False,
        "category": "messaging",
    },
    "DISCORD_BOT_TOKEN": {
        "description": "Discord bot token from Developer Portal",
        "prompt": "Discord bot token",
        "url": "https://discord.com/developers/applications",
        "password": True,
        "category": "messaging",
    },
    "DISCORD_ALLOWED_USERS": {
        "description": "Comma-separated Discord user IDs allowed to use the bot",
        "prompt": "Allowed Discord user IDs (comma-separated)",
        "url": None,
        "password": False,
        "category": "messaging",
    },
    "DISCORD_REPLY_TO_MODE": {
        "description": "Discord reply threading mode: 'off' (no reply references), 'first' (reply on first message only, default), 'all' (reply on every chunk)",
        "prompt": "Discord reply mode (off/first/all)",
        "url": None,
        "password": False,
        "category": "messaging",
    },
    "SLACK_BOT_TOKEN": {
        "description": "Slack bot token (xoxb-). Get from OAuth & Permissions after installing your app. "
                       "Required scopes: chat:write, app_mentions:read, channels:history, groups:history, "
                       "im:history, im:read, im:write, users:read, files:read, files:write",
        "prompt": "Slack Bot Token (xoxb-...)",
        "url": "https://api.slack.com/apps",
        "password": True,
        "category": "messaging",
    },
    "SLACK_APP_TOKEN": {
        "description": "Slack app-level token (xapp-) for Socket Mode. Get from Basic Information → "
                       "App-Level Tokens. Also ensure Event Subscriptions include: message.im, "
                       "message.channels, message.groups, app_mention",
        "prompt": "Slack App Token (xapp-...)",
        "url": "https://api.slack.com/apps",
        "password": True,
        "category": "messaging",
    },
    "MATTERMOST_URL": {
        "description": "Mattermost server URL (e.g. https://mm.example.com)",
        "prompt": "Mattermost server URL",
        "url": "https://mattermost.com/deploy/",
        "password": False,
        "category": "messaging",
    },
    "MATTERMOST_TOKEN": {
        "description": "Mattermost bot token or personal access token",
        "prompt": "Mattermost bot token",
        "url": None,
        "password": True,
        "category": "messaging",
    },
    "MATTERMOST_ALLOWED_USERS": {
        "description": "Comma-separated Mattermost user IDs allowed to use the bot",
        "prompt": "Allowed Mattermost user IDs (comma-separated)",
        "url": None,
        "password": False,
        "category": "messaging",
    },
    "MATTERMOST_REQUIRE_MENTION": {
        "description": "Require @mention in Mattermost channels (default: true). Set to false to respond to all messages.",
        "prompt": "Require @mention in channels",
        "url": None,
        "password": False,
        "category": "messaging",
    },
    "MATTERMOST_FREE_RESPONSE_CHANNELS": {
        "description": "Comma-separated Mattermost channel IDs where bot responds without @mention",
        "prompt": "Free-response channel IDs (comma-separated)",
        "url": None,
        "password": False,
        "category": "messaging",
    },
    "MATRIX_HOMESERVER": {
        "description": "Matrix homeserver URL (e.g. https://matrix.example.org)",
        "prompt": "Matrix homeserver URL",
        "url": "https://matrix.org/ecosystem/servers/",
        "password": False,
        "category": "messaging",
    },
    "MATRIX_ACCESS_TOKEN": {
        "description": "Matrix access token (preferred over password login)",
        "prompt": "Matrix access token",
        "url": None,
        "password": True,
        "category": "messaging",
    },
    "MATRIX_USER_ID": {
        "description": "Matrix user ID (e.g. @hermes:example.org)",
        "prompt": "Matrix user ID (@user:server)",
        "url": None,
        "password": False,
        "category": "messaging",
    },
    "MATRIX_ALLOWED_USERS": {
        "description": "Comma-separated Matrix user IDs allowed to use the bot (@user:server format)",
        "prompt": "Allowed Matrix user IDs (comma-separated)",
        "url": None,
        "password": False,
        "category": "messaging",
    },
    "MATRIX_REQUIRE_MENTION": {
        "description": "Require @mention in Matrix rooms (default: true). Set to false to respond to all messages.",
        "prompt": "Require @mention in rooms (true/false)",
        "url": None,
        "password": False,
        "category": "messaging",
        "advanced": True,
    },
    "MATRIX_FREE_RESPONSE_ROOMS": {
        "description": "Comma-separated Matrix room IDs where bot responds without @mention",
        "prompt": "Free-response room IDs (comma-separated)",
        "url": None,
        "password": False,
        "category": "messaging",
        "advanced": True,
    },
    "MATRIX_AUTO_THREAD": {
        "description": "Auto-create threads for messages in Matrix rooms (default: true)",
        "prompt": "Auto-create threads in rooms (true/false)",
        "url": None,
        "password": False,
        "category": "messaging",
        "advanced": True,
    },
    "MATRIX_DEVICE_ID": {
        "description": "Stable Matrix device ID for E2EE persistence across restarts (e.g. HERMES_BOT)",
        "prompt": "Matrix device ID (stable across restarts)",
        "url": None,
        "password": False,
        "category": "messaging",
        "advanced": True,
    },
    "MATRIX_RECOVERY_KEY": {
        "description": "Matrix recovery key for cross-signing verification after device key rotation (from Element: Settings → Security → Recovery Key)",
        "prompt": "Matrix recovery key",
        "url": None,
        "password": True,
        "category": "messaging",
        "advanced": True,
    },
    "BLUEBUBBLES_SERVER_URL": {
        "description": "BlueBubbles server URL for iMessage integration (e.g. http://192.168.1.10:1234)",
        "prompt": "BlueBubbles server URL",
        "url": "https://bluebubbles.app/",
        "password": False,
        "category": "messaging",
    },
    "BLUEBUBBLES_PASSWORD": {
        "description": "BlueBubbles server password (from BlueBubbles Server → Settings → API)",
        "prompt": "BlueBubbles server password",
        "url": None,
        "password": True,
        "category": "messaging",
    },
    "BLUEBUBBLES_ALLOWED_USERS": {
        "description": "Comma-separated iMessage addresses (email or phone) allowed to use the bot",
        "prompt": "Allowed iMessage addresses (comma-separated)",
        "url": None,
        "password": False,
        "category": "messaging",
    },
    "BLUEBUBBLES_ALLOW_ALL_USERS": {
        "description": "Allow all BlueBubbles users without allowlist",
        "prompt": "Allow All BlueBubbles Users",
        "category": "messaging",
    },
    "QQ_APP_ID": {
        "description": "QQ Bot App ID from QQ Open Platform (q.qq.com)",
        "prompt": "QQ App ID",
        "url": "https://q.qq.com",
        "category": "messaging",
    },
    "QQ_CLIENT_SECRET": {
        "description": "QQ Bot Client Secret from QQ Open Platform",
        "prompt": "QQ Client Secret",
        "password": True,
        "category": "messaging",
    },
    "QQ_ALLOWED_USERS": {
        "description": "Comma-separated QQ user IDs allowed to use the bot",
        "prompt": "QQ Allowed Users",
        "category": "messaging",
    },
    "QQ_GROUP_ALLOWED_USERS": {
        "description": "Comma-separated QQ group IDs allowed to interact with the bot",
        "prompt": "QQ Group Allowed Users",
        "category": "messaging",
    },
    "QQ_ALLOW_ALL_USERS": {
        "description": "Allow all QQ users without an allowlist (true/false)",
        "prompt": "Allow All QQ Users",
        "category": "messaging",
    },
    "QQ_HOME_CHANNEL": {
        "description": "Default QQ channel/group for cron delivery and notifications",
        "prompt": "QQ Home Channel",
        "category": "messaging",
    },
    "QQ_HOME_CHANNEL_NAME": {
        "description": "Display name for the QQ home channel",
        "prompt": "QQ Home Channel Name",
        "category": "messaging",
    },
    "QQ_SANDBOX": {
        "description": "Enable QQ sandbox mode for development testing (true/false)",
        "prompt": "QQ Sandbox Mode",
        "category": "messaging",
    },
    "GATEWAY_ALLOW_ALL_USERS": {
        "description": "Allow all users to interact with messaging bots (true/false). Default: false.",
        "prompt": "Allow all users (true/false)",
        "url": None,
        "password": False,
        "category": "messaging",
        "advanced": True,
    },
    "API_SERVER_ENABLED": {
        "description": "Enable the OpenAI-compatible API server (true/false). Allows frontends like Open WebUI, LobeChat, etc. to connect.",
        "prompt": "Enable API server (true/false)",
        "url": None,
        "password": False,
        "category": "messaging",
        "advanced": True,
    },
    "API_SERVER_KEY": {
        "description": "Bearer token for API server authentication. Required for non-loopback binding; server refuses to start without it. On loopback (127.0.0.1), all requests are allowed if empty.",
        "prompt": "API server auth key (required for network access)",
        "url": None,
        "password": True,
        "category": "messaging",
        "advanced": True,
    },
    "API_SERVER_PORT": {
        "description": "Port for the API server (default: 8642).",
        "prompt": "API server port",
        "url": None,
        "password": False,
        "category": "messaging",
        "advanced": True,
    },
    "API_SERVER_HOST": {
        "description": "Host/bind address for the API server (default: 127.0.0.1). Use 0.0.0.0 for network access — server refuses to start without API_SERVER_KEY.",
        "prompt": "API server host",
        "url": None,
        "password": False,
        "category": "messaging",
        "advanced": True,
    },
    "API_SERVER_MODEL_NAME": {
        "description": "Model name advertised on /v1/models. Defaults to the profile name (or 'hermes-agent' for the default profile). Useful for multi-user setups with OpenWebUI.",
        "prompt": "API server model name",
        "url": None,
        "password": False,
        "category": "messaging",
        "advanced": True,
    },
    "GATEWAY_PROXY_URL": {
        "description": "URL of a remote Hermes API server to forward messages to (proxy mode). When set, the gateway handles platform I/O only — all agent work is delegated to the remote server. Use for Docker E2EE containers that relay to a host agent. Also configurable via gateway.proxy_url in config.yaml.",
        "prompt": "Remote Hermes API server URL (e.g. http://192.168.1.100:8642)",
        "url": None,
        "password": False,
        "category": "messaging",
        "advanced": True,
    },
    "GATEWAY_PROXY_KEY": {
        "description": "Bearer token for authenticating with the remote Hermes API server (proxy mode). Must match the API_SERVER_KEY on the remote host.",
        "prompt": "Remote API server auth key",
        "url": None,
        "password": True,
        "category": "messaging",
        "advanced": True,
    },
    "WEBHOOK_ENABLED": {
        "description": "Enable the webhook platform adapter for receiving events from GitHub, GitLab, etc.",
        "prompt": "Enable webhooks (true/false)",
        "url": None,
        "password": False,
        "category": "messaging",
    },
    "WEBHOOK_PORT": {
        "description": "Port for the webhook HTTP server (default: 8644).",
        "prompt": "Webhook port",
        "url": None,
        "password": False,
        "category": "messaging",
    },
    "WEBHOOK_SECRET": {
        "description": "Global HMAC secret for webhook signature validation (overridable per route in config.yaml).",
        "prompt": "Webhook secret",
        "url": None,
        "password": True,
        "category": "messaging",
    },

    # ── Agent settings ──
    # NOTE: MESSAGING_CWD was removed here — use terminal.cwd in config.yaml
    # instead.  The gateway reads TERMINAL_CWD (bridged from terminal.cwd).
    "SUDO_PASSWORD": {
        "description": "Sudo password for terminal commands requiring root access; set to an explicit empty string to try empty without prompting",
        "prompt": "Sudo password",
        "url": None,
        "password": True,
        "category": "setting",
    },
    "HERMES_MAX_ITERATIONS": {
        "description": "Maximum tool-calling iterations per conversation (default: 90)",
        "prompt": "Max iterations",
        "url": None,
        "password": False,
        "category": "setting",
    },
    # HERMES_TOOL_PROGRESS and HERMES_TOOL_PROGRESS_MODE are deprecated —
    # now configured via display.tool_progress in config.yaml (off|new|all|verbose).
    # Gateway falls back to these env vars for backward compatibility.
    "HERMES_TOOL_PROGRESS": {
        "description": "(deprecated) Use display.tool_progress in config.yaml instead",
        "prompt": "Tool progress (deprecated — use config.yaml)",
        "url": None,
        "password": False,
        "category": "setting",
    },
    "HERMES_TOOL_PROGRESS_MODE": {
        "description": "(deprecated) Use display.tool_progress in config.yaml instead",
        "prompt": "Progress mode (deprecated — use config.yaml)",
        "url": None,
        "password": False,
        "category": "setting",
    },
    "HERMES_PREFILL_MESSAGES_FILE": {
        "description": "Path to JSON file with ephemeral prefill messages for few-shot priming",
        "prompt": "Prefill messages file path",
        "url": None,
        "password": False,
        "category": "setting",
    },
    "HERMES_EPHEMERAL_SYSTEM_PROMPT": {
        "description": "Ephemeral system prompt injected at API-call time (never persisted to sessions)",
        "prompt": "Ephemeral system prompt",
        "url": None,
        "password": False,
        "category": "setting",
    },
}

# Tool Gateway env vars are always visible — they're useful for
# self-hosted / custom gateway setups regardless of subscription state.


def get_missing_env_vars(required_only: bool = False) -> List[Dict[str, Any]]:
    """
    Check which environment variables are missing.
    
    Returns list of dicts with var info for missing variables.
    """
    missing = []
    
    # 检查必需变量
    for var_name, info in REQUIRED_ENV_VARS.items():
        if not get_env_value(var_name):
            missing.append({"name": var_name, **info, "is_required": True})
    
    # 检查可选变量（如果不是 required_only）
    if not required_only:
        for var_name, info in OPTIONAL_ENV_VARS.items():
            if not get_env_value(var_name):
                missing.append({"name": var_name, **info, "is_required": False})
    
    return missing


def _set_nested(config: dict, dotted_key: str, value):
    """在任意嵌套的点分隔键路径上设置值。

    根据需要创建中间字典，例如 ``_set_nested(c, "a.b.c", 1)``
    确保 ``c["a"]["b"]["c"] == 1``。
    """
    parts = dotted_key.split(".")
    current = config
    for part in parts[:-1]:
        if part not in current or not isinstance(current.get(part), dict):
            current[part] = {}
        current = current[part]
    current[parts[-1]] = value


def get_missing_config_fields() -> List[Dict[str, Any]]:
    """
    检查哪些配置字段缺失或过时（递归检查）。

    以任意深度遍历 DEFAULT_CONFIG 树，报告默认配置中存在
    但用户已加载配置中缺失的键。
    """
    config = load_config()
    missing = []

    def _check(defaults: dict, current: dict, prefix: str = ""):
        for key, default_value in defaults.items():
            if key.startswith('_'):
                continue
            full_key = key if not prefix else f"{prefix}.{key}"
            if key not in current:
                missing.append({
                    "key": full_key,
                    "default": default_value,
                    "description": f"New config option: {full_key}",
                })
            elif isinstance(default_value, dict) and isinstance(current.get(key), dict):
                _check(default_value, current[key], full_key)

    _check(DEFAULT_CONFIG, config)
    return missing


def get_missing_skill_config_vars() -> List[Dict[str, Any]]:
    """返回 config.yaml 中缺失或为空的技能声明配置变量。

    扫描所有已启用技能的 ``metadata.hermes.config`` 条目，然后检查
    用户 config.yaml 中 ``skills.config.<key>`` 下哪些条目缺失或为空。
    返回适合提示的字典列表。
    """
    try:
        from agent.skill_utils import discover_all_skill_config_vars, SKILL_CONFIG_PREFIX
    except Exception:
        return []

    all_vars = discover_all_skill_config_vars()
    if not all_vars:
        return []

    config = load_config()
    missing: List[Dict[str, Any]] = []
    for var in all_vars:
        # 技能配置存储在 skills.config.<logical_key> 下
        storage_key = f"{SKILL_CONFIG_PREFIX}.{var['key']}"
        parts = storage_key.split(".")
        current = config
        value = None
        for part in parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
                value = current
            else:
                value = None
                break
        # Missing = key doesn't exist or is empty string
        if value is None or (isinstance(value, str) and not value.strip()):
            missing.append(var)
    return missing


def _normalize_custom_provider_entry(
    entry: Any,
    *,
    provider_key: str = "",
) -> Optional[Dict[str, Any]]:
    """返回运行时兼容的自定义提供者条目，或返回 ``None``。"""
    if not isinstance(entry, dict):
        return None

    base_url = ""
    for url_key in ("api", "url", "base_url"):
        raw_url = entry.get(url_key)
        if isinstance(raw_url, str) and raw_url.strip():
            base_url = raw_url.strip()
            break
    if not base_url:
        return None

    name = ""
    raw_name = entry.get("name")
    if isinstance(raw_name, str) and raw_name.strip():
        name = raw_name.strip()
    elif provider_key.strip():
        name = provider_key.strip()
    if not name:
        return None

    normalized: Dict[str, Any] = {
        "name": name,
        "base_url": base_url,
    }

    provider_key = provider_key.strip()
    if provider_key:
        normalized["provider_key"] = provider_key

    api_key = entry.get("api_key")
    if isinstance(api_key, str) and api_key.strip():
        normalized["api_key"] = api_key.strip()

    key_env = entry.get("key_env")
    if isinstance(key_env, str) and key_env.strip():
        normalized["key_env"] = key_env.strip()

    api_mode = entry.get("api_mode") or entry.get("transport")
    if isinstance(api_mode, str) and api_mode.strip():
        normalized["api_mode"] = api_mode.strip()

    model_name = entry.get("model") or entry.get("default_model")
    if isinstance(model_name, str) and model_name.strip():
        normalized["model"] = model_name.strip()

    models = entry.get("models")
    if isinstance(models, dict) and models:
        normalized["models"] = models

    context_length = entry.get("context_length")
    if isinstance(context_length, int) and context_length > 0:
        normalized["context_length"] = context_length

    rate_limit_delay = entry.get("rate_limit_delay")
    if isinstance(rate_limit_delay, (int, float)) and rate_limit_delay >= 0:
        normalized["rate_limit_delay"] = rate_limit_delay

    return normalized


def providers_dict_to_custom_providers(providers_dict: Any) -> List[Dict[str, Any]]:
    """将 ``providers`` 配置条目标准化为旧版自定义提供者格式。"""
    if not isinstance(providers_dict, dict):
        return []

    custom_providers: List[Dict[str, Any]] = []
    for key, entry in providers_dict.items():
        normalized = _normalize_custom_provider_entry(entry, provider_key=str(key))
        if normalized is not None:
            custom_providers.append(normalized)

    return custom_providers


def get_compatible_custom_providers(
    config: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """返回跨旧版和 v12+ 配置的去重自定义提供者视图。

    ``custom_providers`` 仍为磁盘上的旧版格式，而 ``providers``
    是较新的键控 schema。运行时和选择器流程仍需要单一的
    列表形视图，但不应将此兼容层写回 config.yaml，
    因为这会在 UI 中产生重复条目。
    """
    if config is None:
        config = load_config()

    compatible: List[Dict[str, Any]] = []
    seen_provider_keys: set = set()
    seen_name_url_pairs: set = set()

    def _append_if_new(entry: Optional[Dict[str, Any]]) -> None:
        if entry is None:
            return
        provider_key = str(entry.get("provider_key", "") or "").strip().lower()
        name = str(entry.get("name", "") or "").strip().lower()
        base_url = str(entry.get("base_url", "") or "").strip().rstrip("/").lower()
        model = str(entry.get("model", "") or "").strip().lower()
        pair = (name, base_url, model)

        if provider_key and provider_key in seen_provider_keys:
            return
        if name and base_url and pair in seen_name_url_pairs:
            return

        compatible.append(entry)
        if provider_key:
            seen_provider_keys.add(provider_key)
        if name and base_url:
            seen_name_url_pairs.add(pair)

    custom_providers = config.get("custom_providers")
    if custom_providers is not None:
        if not isinstance(custom_providers, list):
            return []
        for entry in custom_providers:
            _append_if_new(_normalize_custom_provider_entry(entry))

    for entry in providers_dict_to_custom_providers(config.get("providers")):
        _append_if_new(entry)

    return compatible


def check_config_version() -> Tuple[int, int]:
    """
    检查配置版本。

    返回 (current_version, latest_version)。
    """
    config = load_config()
    current = config.get("_config_version", 0)
    latest = DEFAULT_CONFIG.get("_config_version", 1)
    return current, latest


# =============================================================================
# 配置结构验证
# =============================================================================

# config.yaml 根级别有效的字段
_KNOWN_ROOT_KEYS = {
    "_config_version", "model", "providers", "fallback_model",
    "fallback_providers", "credential_pool_strategies", "toolsets",
    "agent", "terminal", "display", "compression", "delegation",
    "auxiliary", "custom_providers", "context", "memory", "gateway",
}

# custom_providers 列表条目中的有效字段
_VALID_CUSTOM_PROVIDER_FIELDS = {
    "name", "base_url", "api_key", "api_mode", "model", "models",
    "context_length", "rate_limit_delay",
}

# 看起来应该在 custom_providers 内部而非根级别的字段
_CUSTOM_PROVIDER_LIKE_FIELDS = {"base_url", "api_key", "rate_limit_delay", "api_mode"}


@dataclass
class ConfigIssue:
    """检测到的配置结构问题。"""

    severity: str  # "error", "warning"
    message: str
    hint: str


def validate_config_structure(config: Optional[Dict[str, Any]] = None) -> List["ConfigIssue"]:
    """验证 config.yaml 结构并返回检测到的问题列表。

    捕获常见的 YAML 格式错误，这些错误会产生令人困惑的运行时
    错误（如 "Unknown provider"），而非清晰的诊断信息。

    可以使用预加载的配置字典调用，或从磁盘加载。
    """
    if config is None:
        try:
            config = load_config()
        except Exception:
            return [ConfigIssue("error", "Could not load config.yaml", "Run 'hermes setup' to create a valid config")]

    issues: List[ConfigIssue] = []

    # ── custom_providers must be a list, not a dict ──────────────────────
    cp = config.get("custom_providers")
    if cp is not None:
        if isinstance(cp, dict):
            issues.append(ConfigIssue(
                "error",
                "custom_providers is a dict — it must be a YAML list (items prefixed with '-')",
                "Change to:\n"
                "  custom_providers:\n"
                "    - name: my-provider\n"
                "      base_url: https://...\n"
                "      api_key: ...",
            ))
            # Check if dict keys look like they should be list-entry fields
            cp_keys = set(cp.keys()) if isinstance(cp, dict) else set()
            suspicious = cp_keys & _CUSTOM_PROVIDER_LIKE_FIELDS
            if suspicious:
                issues.append(ConfigIssue(
                    "warning",
                    f"Root-level keys {sorted(suspicious)} look like custom_providers entry fields",
                    "These should be indented under a '- name: ...' list entry, not at root level",
                ))
        elif isinstance(cp, list):
            # Validate each entry in the list
            for i, entry in enumerate(cp):
                if not isinstance(entry, dict):
                    issues.append(ConfigIssue(
                        "warning",
                        f"custom_providers[{i}] is not a dict (got {type(entry).__name__})",
                        "Each entry should have at minimum: name, base_url",
                    ))
                    continue
                if not entry.get("name"):
                    issues.append(ConfigIssue(
                        "warning",
                        f"custom_providers[{i}] is missing 'name' field",
                        "Add a name, e.g.: name: my-provider",
                    ))
                if not entry.get("base_url"):
                    issues.append(ConfigIssue(
                        "warning",
                        f"custom_providers[{i}] is missing 'base_url' field",
                        "Add the API endpoint URL, e.g.: base_url: https://api.example.com/v1",
                    ))

    # ── fallback_model must be a top-level dict with provider + model ────
    fb = config.get("fallback_model")
    if fb is not None:
        if not isinstance(fb, dict):
            issues.append(ConfigIssue(
                "error",
                f"fallback_model should be a dict with 'provider' and 'model', got {type(fb).__name__}",
                "Change to:\n"
                "  fallback_model:\n"
                "    provider: openrouter\n"
                "    model: anthropic/claude-sonnet-4",
            ))
        elif fb:
            if not fb.get("provider"):
                issues.append(ConfigIssue(
                    "warning",
                    "fallback_model is missing 'provider' field — fallback will be disabled",
                    "Add: provider: openrouter (or another provider)",
                ))
            if not fb.get("model"):
                issues.append(ConfigIssue(
                    "warning",
                    "fallback_model is missing 'model' field — fallback will be disabled",
                    "Add: model: anthropic/claude-sonnet-4 (or another model)",
                ))

    # ── Check for fallback_model accidentally nested inside custom_providers ──
    if isinstance(cp, dict) and "fallback_model" not in config and "fallback_model" in (cp or {}):
        issues.append(ConfigIssue(
            "error",
            "fallback_model appears inside custom_providers instead of at root level",
            "Move fallback_model to the top level of config.yaml (no indentation)",
        ))

    # ── model section: should exist when custom_providers is configured ──
    model_cfg = config.get("model")
    if cp and not model_cfg:
        issues.append(ConfigIssue(
            "warning",
            "custom_providers defined but no 'model' section — Hermes won't know which provider to use",
            "Add a model section:\n"
            "  model:\n"
            "    provider: custom\n"
            "    default: your-model-name\n"
            "    base_url: https://...",
        ))

    # ── Root-level keys that look misplaced ──────────────────────────────
    for key in config:
        if key.startswith("_"):
            continue
        if key not in _KNOWN_ROOT_KEYS and key in _CUSTOM_PROVIDER_LIKE_FIELDS:
            issues.append(ConfigIssue(
                "warning",
                f"Root-level key '{key}' looks misplaced — should it be under 'model:' or inside a 'custom_providers' entry?",
                f"Move '{key}' under the appropriate section",
            ))

    return issues


def print_config_warnings(config: Optional[Dict[str, Any]] = None) -> None:
    """在启动时将配置结构警告打印到 stderr。

    在 CLI 和网关初始化早期调用，使用户在遇到
    难以理解的 "Unknown provider" 错误之前看到问题。
    如果配置正常则不打印任何内容。
    """
    try:
        issues = validate_config_structure(config)
    except Exception:
        return
    if not issues:
        return

    import sys
    lines = ["\033[33m⚠ Config issues detected in config.yaml:\033[0m"]
    for ci in issues:
        marker = "\033[31m✗\033[0m" if ci.severity == "error" else "\033[33m⚠\033[0m"
        lines.append(f"  {marker} {ci.message}")
    lines.append("  \033[2mRun 'hermes doctor' for fix suggestions.\033[0m")
    sys.stderr.write("\n".join(lines) + "\n\n")


def warn_deprecated_cwd_env_vars(config: Optional[Dict[str, Any]] = None) -> None:
    """当 MESSAGING_CWD 或 TERMINAL_CWD 设置在 .env 而非 config.yaml 中时发出警告。

    这些环境变量已弃用 — 规范设置为 config.yaml 中的 terminal.cwd。
    将迁移提示打印到 stderr。
    """
    import os, sys
    messaging_cwd = os.environ.get("MESSAGING_CWD")
    terminal_cwd_env = os.environ.get("TERMINAL_CWD")

    if config is None:
        try:
            config = load_config()
        except Exception:
            return

    terminal_cfg = config.get("terminal", {})
    config_cwd = terminal_cfg.get("cwd", ".") if isinstance(terminal_cfg, dict) else "."
    # 仅在 config.yaml 没有显式路径时发出警告
    config_has_explicit_cwd = config_cwd not in (".", "auto", "cwd", "")

    lines: list[str] = []
    if messaging_cwd:
        lines.append(
            f"  \033[33m⚠\033[0m MESSAGING_CWD={messaging_cwd} found in .env — "
            f"this is deprecated."
        )
    if terminal_cwd_env and not config_has_explicit_cwd:
        # TERMINAL_CWD in env but not from config bridge — likely from .env
        lines.append(
            f"  \033[33m⚠\033[0m TERMINAL_CWD={terminal_cwd_env} found in .env — "
            f"this is deprecated."
        )
    if lines:
        hint_path = os.environ.get("HERMES_HOME", "~/.hermes")
        lines.insert(0, "\033[33m⚠ Deprecated .env settings detected:\033[0m")
        lines.append(
            f"  \033[2mMove to config.yaml instead:  "
            f"terminal:\\n    cwd: /your/project/path\033[0m"
        )
        lines.append(
            f"  \033[2mThen remove the old entries from {hint_path}/.env\033[0m"
        )
        sys.stderr.write("\n".join(lines) + "\n\n")


def migrate_config(interactive: bool = True, quiet: bool = False) -> Dict[str, Any]:
    """
    Migrate config to latest version, prompting for new required fields.
    
    Args:
        interactive: If True, prompt user for missing values
        quiet: If True, suppress output
        
    Returns:
        Dict with migration results: {"env_added": [...], "config_added": [...], "warnings": [...]}
    """
    results = {"env_added": [], "config_added": [], "warnings": []}

    # ── Always: sanitize .env (split concatenated keys) ──
    try:
        fixes = sanitize_env_file()
        if fixes and not quiet:
            print(f"  ✓ Repaired .env file ({fixes} corrupted entries fixed)")
    except Exception:
        pass  # best-effort; don't block migration on sanitize failure

    # 检查配置版本
    current_ver, latest_ver = check_config_version()
    
    # ── Version 3 → 4: migrate tool progress from .env to config.yaml ──
    if current_ver < 4:
        config = load_config()
        display = config.get("display", {})
        if not isinstance(display, dict):
            display = {}
        if "tool_progress" not in display:
            old_enabled = get_env_value("HERMES_TOOL_PROGRESS")
            old_mode = get_env_value("HERMES_TOOL_PROGRESS_MODE")
            if old_enabled and old_enabled.lower() in ("false", "0", "no"):
                display["tool_progress"] = "off"
                results["config_added"].append("display.tool_progress=off (from HERMES_TOOL_PROGRESS=false)")
            elif old_mode and old_mode.lower() in ("new", "all"):
                display["tool_progress"] = old_mode.lower()
                results["config_added"].append(f"display.tool_progress={old_mode.lower()} (from HERMES_TOOL_PROGRESS_MODE)")
            else:
                display["tool_progress"] = "all"
                results["config_added"].append("display.tool_progress=all (default)")
            config["display"] = display
            save_config(config)
            if not quiet:
                print(f"  ✓ Migrated tool progress to config.yaml: {display['tool_progress']}")
    
    # ── Version 4 → 5: add timezone field ──
    if current_ver < 5:
        config = load_config()
        if "timezone" not in config:
            old_tz = os.getenv("HERMES_TIMEZONE", "")
            if old_tz and old_tz.strip():
                config["timezone"] = old_tz.strip()
                results["config_added"].append(f"timezone={old_tz.strip()} (from HERMES_TIMEZONE)")
            else:
                config["timezone"] = ""
                results["config_added"].append("timezone= (empty, uses server-local)")
            save_config(config)
            if not quiet:
                tz_display = config["timezone"] or "(server-local)"
                print(f"  ✓ Added timezone to config.yaml: {tz_display}")

    # ── Version 8 → 9: clear ANTHROPIC_TOKEN from .env ──
    # The new Anthropic auth flow no longer uses this env var.
    if current_ver < 9:
        try:
            old_token = get_env_value("ANTHROPIC_TOKEN")
            if old_token:
                save_env_value("ANTHROPIC_TOKEN", "")
                if not quiet:
                    print("  ✓ Cleared ANTHROPIC_TOKEN from .env (no longer used)")
        except Exception:
            pass

    # ── Version 11 → 12: migrate custom_providers list → providers dict ──
    if current_ver < 12:
        config = load_config()
        custom_list = config.get("custom_providers")
        if isinstance(custom_list, list) and custom_list:
            providers_dict = config.get("providers", {})
            if not isinstance(providers_dict, dict):
                providers_dict = {}
            migrated_count = 0
            for entry in custom_list:
                if not isinstance(entry, dict):
                    continue
                old_name = entry.get("name", "")
                old_url = entry.get("base_url", "") or entry.get("url", "") or ""
                old_key = entry.get("api_key", "")
                if not old_url:
                    continue  # skip entries with no URL

                # Generate a kebab-case key from the display name
                key = old_name.strip().lower().replace(" ", "-").replace("(", "").replace(")", "")
                # Remove consecutive hyphens and trailing hyphens
                while "--" in key:
                    key = key.replace("--", "-")
                key = key.strip("-")
                if not key:
                    # Fallback: derive from URL hostname
                    try:
                        from urllib.parse import urlparse
                        parsed = urlparse(old_url)
                        key = (parsed.hostname or "endpoint").replace(".", "-")
                    except Exception:
                        key = f"endpoint-{migrated_count}"

                # Don't overwrite existing entries
                if key in providers_dict:
                    key = f"{key}-{migrated_count}"

                new_entry = {"api": old_url}
                if old_name:
                    new_entry["name"] = old_name
                if old_key and old_key not in ("no-key", "no-key-required", ""):
                    new_entry["api_key"] = old_key

                # Carry over model and api_mode if present
                if entry.get("model"):
                    new_entry["default_model"] = entry["model"]
                if entry.get("api_mode"):
                    new_entry["transport"] = entry["api_mode"]

                providers_dict[key] = new_entry
                migrated_count += 1

            if migrated_count > 0:
                config["providers"] = providers_dict
                # Remove the old list — runtime reads via get_compatible_custom_providers()
                config.pop("custom_providers", None)
                save_config(config)
                if not quiet:
                    print(f"  ✓ Migrated {migrated_count} custom provider(s) to providers: section")
                    for key in list(providers_dict.keys())[-migrated_count:]:
                        ep = providers_dict[key]
                        print(f"    → {key}: {ep.get('api', '')}")

    # ── Version 12 → 13: clear dead LLM_MODEL / OPENAI_MODEL from .env ──
    # These env vars were written by the old setup wizard but nothing reads
    # them anymore (config.yaml is the sole source of truth since March 2026).
    # Stale entries cause user confusion — see issue report.
    if current_ver < 13:
        for dead_var in ("LLM_MODEL", "OPENAI_MODEL"):
            try:
                old_val = get_env_value(dead_var)
                if old_val:
                    save_env_value(dead_var, "")
                    if not quiet:
                        print(f"  ✓ Cleared {dead_var} from .env (no longer used — config.yaml is source of truth)")
            except Exception:
                pass

    # ── Version 13 → 14: migrate legacy flat stt.model to provider section ──
    # Old configs (and cli-config.yaml.example) had a flat `stt.model` key
    # that was provider-agnostic.  When the provider was "local" this caused
    # OpenAI model names (e.g. "whisper-1") to be fed to faster-whisper,
    # crashing with "Invalid model size".  Move the value into the correct
    # provider-specific section and remove the flat key.
    if current_ver < 14:
        # Read raw config (no defaults merged) to check what the user actually
        # wrote, then apply changes to the merged config for saving.
        raw = read_raw_config()
        raw_stt = raw.get("stt", {})
        if isinstance(raw_stt, dict) and "model" in raw_stt:
            legacy_model = raw_stt["model"]
            provider = raw_stt.get("provider", "local")
            config = load_config()
            stt = config.get("stt", {})
            # Remove the legacy flat key
            stt.pop("model", None)
            # Place it in the appropriate provider section only if the
            # user didn't already set a model there
            if provider in ("local", "local_command"):
                # Don't migrate an OpenAI model name into the local section
                _local_models = {
                    "tiny.en", "tiny", "base.en", "base", "small.en", "small",
                    "medium.en", "medium", "large-v1", "large-v2", "large-v3",
                    "large", "distil-large-v2", "distil-medium.en",
                    "distil-small.en", "distil-large-v3", "distil-large-v3.5",
                    "large-v3-turbo", "turbo",
                }
                if legacy_model in _local_models:
                    # Check raw config — only set if user didn't already
                    # have a nested local.model
                    raw_local = raw_stt.get("local", {})
                    if not isinstance(raw_local, dict) or "model" not in raw_local:
                        local_cfg = stt.setdefault("local", {})
                        local_cfg["model"] = legacy_model
                # else: drop it — it was an OpenAI model name, local section
                # already defaults to "base" via DEFAULT_CONFIG
            else:
                # Cloud provider — put it in that provider's section only
                # if user didn't already set a nested model
                raw_provider = raw_stt.get(provider, {})
                if not isinstance(raw_provider, dict) or "model" not in raw_provider:
                    provider_cfg = stt.setdefault(provider, {})
                    provider_cfg["model"] = legacy_model
            config["stt"] = stt
            save_config(config)
            if not quiet:
                print(f"  ✓ Migrated legacy stt.model to provider-specific config")

    # ── Version 14 → 15: add explicit gateway interim-message gate ──
    if current_ver < 15:
        config = read_raw_config()
        display = config.get("display", {})
        if not isinstance(display, dict):
            display = {}
        if "interim_assistant_messages" not in display:
            display["interim_assistant_messages"] = True
            config["display"] = display
            results["config_added"].append("display.interim_assistant_messages=true (default)")
            save_config(config)
            if not quiet:
                print("  ✓ Added display.interim_assistant_messages=true")

    # ── Version 15 → 16: migrate tool_progress_overrides into display.platforms ──
    if current_ver < 16:
        config = read_raw_config()
        display = config.get("display", {})
        if not isinstance(display, dict):
            display = {}
        old_overrides = display.get("tool_progress_overrides")
        if isinstance(old_overrides, dict) and old_overrides:
            platforms = display.get("platforms", {})
            if not isinstance(platforms, dict):
                platforms = {}
            for plat, mode in old_overrides.items():
                if plat not in platforms:
                    platforms[plat] = {}
                if "tool_progress" not in platforms[plat]:
                    platforms[plat]["tool_progress"] = mode
            display["platforms"] = platforms
            config["display"] = display
            save_config(config)
            if not quiet:
                migrated = ", ".join(f"{p}={m}" for p, m in old_overrides.items())
                print(f"  ✓ Migrated tool_progress_overrides → display.platforms: {migrated}")
            results["config_added"].append("display.platforms (migrated from tool_progress_overrides)")

    # ── Version 16 → 17: remove legacy compression.summary_* keys ──
    if current_ver < 17:
        config = read_raw_config()
        comp = config.get("compression", {})
        if isinstance(comp, dict):
            s_model = comp.pop("summary_model", None)
            s_provider = comp.pop("summary_provider", None)
            s_base_url = comp.pop("summary_base_url", None)
            migrated_keys = []
            # Migrate non-empty, non-default values to auxiliary.compression
            if s_model and str(s_model).strip():
                aux = config.setdefault("auxiliary", {})
                aux_comp = aux.setdefault("compression", {})
                if not aux_comp.get("model"):
                    aux_comp["model"] = str(s_model).strip()
                    migrated_keys.append(f"model={s_model}")
            if s_provider and str(s_provider).strip() not in ("", "auto"):
                aux = config.setdefault("auxiliary", {})
                aux_comp = aux.setdefault("compression", {})
                if not aux_comp.get("provider") or aux_comp.get("provider") == "auto":
                    aux_comp["provider"] = str(s_provider).strip()
                    migrated_keys.append(f"provider={s_provider}")
            if s_base_url and str(s_base_url).strip():
                aux = config.setdefault("auxiliary", {})
                aux_comp = aux.setdefault("compression", {})
                if not aux_comp.get("base_url"):
                    aux_comp["base_url"] = str(s_base_url).strip()
                    migrated_keys.append(f"base_url={s_base_url}")
            if migrated_keys or s_model is not None or s_provider is not None or s_base_url is not None:
                config["compression"] = comp
                save_config(config)
                if not quiet:
                    if migrated_keys:
                        print(f"  ✓ Migrated compression.summary_* → auxiliary.compression: {', '.join(migrated_keys)}")
                    else:
                        print("  ✓ Removed unused compression.summary_* keys")

    if current_ver < latest_ver and not quiet:
        print(f"Config version: {current_ver} → {latest_ver}")
    
    # 检查缺失的必需环境变量
    missing_env = get_missing_env_vars(required_only=True)
    
    if missing_env and not quiet:
        print("\n⚠️  Missing required environment variables:")
        for var in missing_env:
            print(f"   • {var['name']}: {var['description']}")
    
    if interactive and missing_env:
        print("\nLet's configure them now:\n")
        for var in missing_env:
            if var.get("url"):
                print(f"  Get your key at: {var['url']}")
            
            if var.get("password"):
                import getpass
                value = getpass.getpass(f"  {var['prompt']}: ")
            else:
                value = input(f"  {var['prompt']}: ").strip()
            
            if value:
                save_env_value(var["name"], value)
                results["env_added"].append(var["name"])
                print(f"  ✓ Saved {var['name']}")
            else:
                results["warnings"].append(f"Skipped {var['name']} - some features may not work")
            print()
    
    # 检查缺失的可选环境变量并提供交互式配置
    # Skip "advanced" vars (like OPENAI_BASE_URL) -- those are for power users
    missing_optional = get_missing_env_vars(required_only=False)
    required_names = {v["name"] for v in missing_env} if missing_env else set()
    missing_optional = [
        v for v in missing_optional
        if v["name"] not in required_names and not v.get("advanced")
    ]
    
    # Only offer to configure env vars that are NEW since the user's previous version
    new_var_names = set()
    for ver in range(current_ver + 1, latest_ver + 1):
        new_var_names.update(ENV_VARS_BY_VERSION.get(ver, []))

    if new_var_names and interactive and not quiet:
        new_and_unset = [
            (name, OPTIONAL_ENV_VARS[name])
            for name in sorted(new_var_names)
            if not get_env_value(name) and name in OPTIONAL_ENV_VARS
        ]
        if new_and_unset:
            print(f"\n  {len(new_and_unset)} new optional key(s) in this update:")
            for name, info in new_and_unset:
                print(f"    • {name} — {info.get('description', '')}")
            print()
            try:
                answer = input("  Configure new keys? [y/N]: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                answer = "n"

            if answer in ("y", "yes"):
                print()
                for name, info in new_and_unset:
                    if info.get("url"):
                        print(f"  {info.get('description', name)}")
                        print(f"  Get your key at: {info['url']}")
                    else:
                        print(f"  {info.get('description', name)}")
                    if info.get("password"):
                        import getpass
                        value = getpass.getpass(f"  {info.get('prompt', name)} (Enter to skip): ")
                    else:
                        value = input(f"  {info.get('prompt', name)} (Enter to skip): ").strip()
                    if value:
                        save_env_value(name, value)
                        results["env_added"].append(name)
                        print(f"  ✓ Saved {name}")
                    print()
            else:
                print("  Set later with: hermes config set <key> <value>")
    
    # Check for missing config fields
    missing_config = get_missing_config_fields()
    
    if missing_config:
        config = load_config()
        
        for field in missing_config:
            key = field["key"]
            default = field["default"]
            
            _set_nested(config, key, default)
            results["config_added"].append(key)
            if not quiet:
                print(f"  ✓ Added {key} = {default}")
        
        # Update version and save
        config["_config_version"] = latest_ver
        save_config(config)
    elif current_ver < latest_ver:
        # Just update version
        config = load_config()
        config["_config_version"] = latest_ver
        save_config(config)

    # ── Skill-declared config vars ──────────────────────────────────────
    # Skills can declare config.yaml settings they need via
    # metadata.hermes.config in their SKILL.md frontmatter.
    # Prompt for any that are missing/empty.
    missing_skill_config = get_missing_skill_config_vars()
    if missing_skill_config and interactive and not quiet:
        print(f"\n  {len(missing_skill_config)} skill setting(s) not configured:")
        for var in missing_skill_config:
            skill_name = var.get("skill", "unknown")
            print(f"    • {var['key']} — {var['description']} (from skill: {skill_name})")
        print()
        try:
            answer = input("  Configure skill settings? [y/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = "n"

        if answer in ("y", "yes"):
            print()
            config = load_config()
            try:
                from agent.skill_utils import SKILL_CONFIG_PREFIX
            except Exception:
                SKILL_CONFIG_PREFIX = "skills.config"
            for var in missing_skill_config:
                default = var.get("default", "")
                default_hint = f" (default: {default})" if default else ""
                value = input(f"  {var['prompt']}{default_hint}: ").strip()
                if not value and default:
                    value = str(default)
                if value:
                    storage_key = f"{SKILL_CONFIG_PREFIX}.{var['key']}"
                    _set_nested(config, storage_key, value)
                    results["config_added"].append(var["key"])
                    print(f"  ✓ Saved {var['key']} = {value}")
                else:
                    results["warnings"].append(
                        f"Skipped {var['key']} — skill '{var.get('skill', '?')}' may ask for it later"
                    )
                print()
            save_config(config)
        else:
            print("  Set later with: hermes config set <key> <value>")

    return results


def _deep_merge(base: dict, override: dict) -> dict:
    """递归地将 *override* 合并到 *base* 中，保留嵌套的默认值。

    *override* 中的键优先。如果两个值都是字典则递归合并，
    因此只覆盖 ``tts.elevenlabs.voice_id`` 的用户将
    保持默认的 ``tts.elevenlabs.model_id`` 不变。
    """
    result = base.copy()
    for key, value in override.items():
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(value, dict)
        ):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _expand_env_vars(obj):
    """递归展开配置值中的 ``${VAR}`` 引用。

    仅处理字符串值；字典键、数字、布尔值和
    None 保持不变。未解析的引用（变量不在
    ``os.environ`` 中）保持原样，以便调用者可以检测到。
    """
    if isinstance(obj, str):
        return re.sub(
            r"\${([^}]+)}",
            lambda m: os.environ.get(m.group(1), m.group(0)),
            obj,
        )
    if isinstance(obj, dict):
        return {k: _expand_env_vars(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_expand_env_vars(item) for item in obj]
    return obj


def _normalize_root_model_keys(config: Dict[str, Any]) -> Dict[str, Any]:
    """将过时的根级别 provider/base_url 移动到 model 部分。

    某些用户（或旧版代码）将 ``provider:`` 和 ``base_url:`` 放在
    配置根级别而非 ``model:`` 内部。这些根级别键仅在
    相应的 ``model.*`` 键为空时作为回退使用 — 它们
    永远不会覆盖已有的 ``model.provider`` 或 ``model.base_url``。
    迁移后根级别键被移除，以免在后续加载中造成混淆。
    """
    # Only act if there are root-level keys to migrate
    has_root = any(config.get(k) for k in ("provider", "base_url"))
    if not has_root:
        return config

    config = dict(config)
    model = config.get("model")
    if not isinstance(model, dict):
        model = {"default": model} if model else {}
        config["model"] = model

    for key in ("provider", "base_url"):
        root_val = config.get(key)
        if root_val and not model.get(key):
            model[key] = root_val
        config.pop(key, None)

    return config


def _normalize_max_turns_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """将旧版根级别 max_turns 标准化到 agent.max_turns。"""
    config = dict(config)
    agent_config = dict(config.get("agent") or {})

    if "max_turns" in config and "max_turns" not in agent_config:
        agent_config["max_turns"] = config["max_turns"]

    if "max_turns" not in agent_config:
        agent_config["max_turns"] = DEFAULT_CONFIG["agent"]["max_turns"]

    config["agent"] = agent_config
    config.pop("max_turns", None)
    return config



def read_raw_config() -> Dict[str, Any]:
    """原样读取 ~/.hermes/config.yaml，不合并默认值或迁移。

    返回原始 YAML 字典，如果文件不存在或无法解析则返回 ``{}``。
    适用于只需要单个值且不想承受 ``load_config()`` 的深度合并
    + 迁移管道开销的轻量级配置读取。
    """
    try:
        config_path = get_config_path()
        if config_path.exists():
            with open(config_path, encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
    except Exception:
        pass
    return {}


def load_config() -> Dict[str, Any]:
    """从 ~/.hermes/config.yaml 加载配置。"""
    import copy
    ensure_hermes_home()
    config_path = get_config_path()
    
    config = copy.deepcopy(DEFAULT_CONFIG)
    
    if config_path.exists():
        try:
            with open(config_path, encoding="utf-8") as f:
                user_config = yaml.safe_load(f) or {}

            if "max_turns" in user_config:
                agent_user_config = dict(user_config.get("agent") or {})
                if agent_user_config.get("max_turns") is None:
                    agent_user_config["max_turns"] = user_config["max_turns"]
                user_config["agent"] = agent_user_config
                user_config.pop("max_turns", None)

            config = _deep_merge(config, user_config)
        except Exception as e:
            print(f"Warning: Failed to load config: {e}")
    
    return _expand_env_vars(_normalize_root_model_keys(_normalize_max_turns_config(config)))


_SECURITY_COMMENT = """
# ── Security ──────────────────────────────────────────────────────────
# API keys, tokens, and passwords are redacted from tool output by default.
# Set to false to see full values (useful for debugging auth issues).
# tirith pre-exec scanning is enabled by default when the tirith binary
# is available. Configure via security.tirith_* keys or env vars
# (TIRITH_ENABLED, TIRITH_BIN, TIRITH_TIMEOUT, TIRITH_FAIL_OPEN).
#
# security:
#   redact_secrets: false
#   tirith_enabled: true
#   tirith_path: "tirith"
#   tirith_timeout: 5
#   tirith_fail_open: true
"""

_FALLBACK_COMMENT = """
# ── Fallback Model ────────────────────────────────────────────────────
# Automatic provider failover when primary is unavailable.
# Uncomment and configure to enable. Triggers on rate limits (429),
# overload (529), service errors (503), or connection failures.
#
# Supported providers:
#   openrouter   (OPENROUTER_API_KEY)  — routes to any model
#   openai-codex (OAuth — hermes auth) — OpenAI Codex
#   nous         (OAuth — hermes auth) — Nous Portal
#   zai          (ZAI_API_KEY)         — Z.AI / GLM
#   kimi-coding  (KIMI_API_KEY)        — Kimi / Moonshot
#   kimi-coding-cn (KIMI_CN_API_KEY)   — Kimi / Moonshot (China)
#   minimax      (MINIMAX_API_KEY)     — MiniMax
#   minimax-cn   (MINIMAX_CN_API_KEY)  — MiniMax (China)
#
# For custom OpenAI-compatible endpoints, add base_url and api_key_env.
#
# fallback_model:
#   provider: openrouter
#   model: anthropic/claude-sonnet-4
#
# ── Smart Model Routing ────────────────────────────────────────────────
# Optional cheap-vs-strong routing for simple turns.
# Keeps the primary model for complex work, but can route short/simple
# messages to a cheaper model across providers.
#
# smart_model_routing:
#   enabled: true
#   max_simple_chars: 160
#   max_simple_words: 28
#   cheap_model:
#     provider: openrouter
#     model: google/gemini-2.5-flash
"""


_COMMENTED_SECTIONS = """
# ── Security ──────────────────────────────────────────────────────────
# API keys, tokens, and passwords are redacted from tool output by default.
# Set to false to see full values (useful for debugging auth issues).
#
# security:
#   redact_secrets: false

# ── Fallback Model ────────────────────────────────────────────────────
# Automatic provider failover when primary is unavailable.
# Uncomment and configure to enable. Triggers on rate limits (429),
# overload (529), service errors (503), or connection failures.
#
# Supported providers:
#   openrouter   (OPENROUTER_API_KEY)  — routes to any model
#   openai-codex (OAuth — hermes auth) — OpenAI Codex
#   nous         (OAuth — hermes auth) — Nous Portal
#   zai          (ZAI_API_KEY)         — Z.AI / GLM
#   kimi-coding  (KIMI_API_KEY)        — Kimi / Moonshot
#   kimi-coding-cn (KIMI_CN_API_KEY)   — Kimi / Moonshot (China)
#   minimax      (MINIMAX_API_KEY)     — MiniMax
#   minimax-cn   (MINIMAX_CN_API_KEY)  — MiniMax (China)
#
# For custom OpenAI-compatible endpoints, add base_url and api_key_env.
#
# fallback_model:
#   provider: openrouter
#   model: anthropic/claude-sonnet-4
#
# ── Smart Model Routing ────────────────────────────────────────────────
# Optional cheap-vs-strong routing for simple turns.
# Keeps the primary model for complex work, but can route short/simple
# messages to a cheaper model across providers.
#
# smart_model_routing:
#   enabled: true
#   max_simple_chars: 160
#   max_simple_words: 28
#   cheap_model:
#     provider: openrouter
#     model: google/gemini-2.5-flash
"""


def save_config(config: Dict[str, Any]):
    """将配置保存到 ~/.hermes/config.yaml。"""
    if is_managed():
        managed_error("save configuration")
        return
    from utils import atomic_yaml_write

    ensure_hermes_home()
    config_path = get_config_path()
    normalized = _normalize_root_model_keys(_normalize_max_turns_config(config))

    # Build optional commented-out sections for features that are off by
    # default or only relevant when explicitly configured.
    parts = []
    sec = normalized.get("security", {})
    if not sec or sec.get("redact_secrets") is None:
        parts.append(_SECURITY_COMMENT)
    fb = normalized.get("fallback_model", {})
    if not fb or not (fb.get("provider") and fb.get("model")):
        parts.append(_FALLBACK_COMMENT)

    atomic_yaml_write(
        config_path,
        normalized,
        extra_content="".join(parts) if parts else None,
    )
    _secure_file(config_path)


def load_env() -> Dict[str, str]:
    """从 ~/.hermes/.env 加载环境变量。

    在解析前清理行，以便损坏的文件（例如
    在单行上连接的 KEY=VALUE 对）能被优雅处理，
    而不是产生错乱的值（如重复的 bot token）。参见 #8908。
    """
    env_path = get_env_path()
    env_vars = {}
    
    if env_path.exists():
        # On Windows, open() defaults to the system locale (cp1252) which can
        # fail on UTF-8 .env files. Use explicit UTF-8 only on Windows.
        open_kw = {"encoding": "utf-8", "errors": "replace"} if _IS_WINDOWS else {}
        with open(env_path, **open_kw) as f:
            raw_lines = f.readlines()
        # Sanitize before parsing: split concatenated lines & drop stale
        # placeholders so corrupted .env files don't produce invalid tokens.
        lines = _sanitize_env_lines(raw_lines)
        for line in lines:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, _, value = line.partition('=')
                env_vars[key.strip()] = value.strip().strip('"\'')
    
    return env_vars


def _sanitize_env_lines(lines: list) -> list:
    """在读取或写入前修复损坏的 .env 行。

    处理两种已知的损坏模式：
    1. 单行上连接的 KEY=VALUE 对（条目之间缺少换行，
       例如 ``ANTHROPIC_API_KEY=sk-...OPENAI_BASE_URL=https://...``）。
    2. 不完整设置运行留下的过时 ``KEY=***`` 占位符条目。

    使用已知键集（OPTIONAL_ENV_VARS + _EXTRA_ENV_KEYS），因此只在
    真实的 Hermes 环境变量名上拆分，避免值中恰好包含
    带 ``=`` 的大写文本时的误报。
    """
    # Build the known keys set lazily from OPTIONAL_ENV_VARS + extras.
    # Done inside the function so OPTIONAL_ENV_VARS is guaranteed to be defined.
    known_keys = set(OPTIONAL_ENV_VARS.keys()) | _EXTRA_ENV_KEYS

    sanitized: list[str] = []
    for line in lines:
        raw = line.rstrip("\r\n")
        stripped = raw.strip()

        # Preserve blank lines and comments
        if not stripped or stripped.startswith("#"):
            sanitized.append(raw + "\n")
            continue

        # Detect concatenated KEY=VALUE pairs on one line.
        # Search for known KEY= patterns at any position in the line.
        split_positions = []
        for key_name in known_keys:
            needle = key_name + "="
            idx = stripped.find(needle)
            while idx >= 0:
                split_positions.append(idx)
                idx = stripped.find(needle, idx + len(needle))

        if len(split_positions) > 1:
            split_positions.sort()
            # Deduplicate (shouldn't happen, but be safe)
            split_positions = sorted(set(split_positions))
            for i, pos in enumerate(split_positions):
                end = split_positions[i + 1] if i + 1 < len(split_positions) else len(stripped)
                part = stripped[pos:end].strip()
                if part:
                    sanitized.append(part + "\n")
        else:
            sanitized.append(stripped + "\n")

    return sanitized


def sanitize_env_file() -> int:
    """读取、清理并原地重写 ~/.hermes/.env。

    返回修复的行数（连接拆分 + 占位符移除）。
    不需要更改时返回 0。
    """
    env_path = get_env_path()
    if not env_path.exists():
        return 0

    read_kw = {"encoding": "utf-8", "errors": "replace"} if _IS_WINDOWS else {}
    write_kw = {"encoding": "utf-8"} if _IS_WINDOWS else {}

    with open(env_path, **read_kw) as f:
        original_lines = f.readlines()

    sanitized = _sanitize_env_lines(original_lines)

    if sanitized == original_lines:
        return 0

    # Count fixes: difference in line count (from splits) + removed lines
    fixes = abs(len(sanitized) - len(original_lines))
    if fixes == 0:
        # Lines changed content (e.g. *** removal) even if count is same
        fixes = sum(1 for a, b in zip(original_lines, sanitized) if a != b)
        fixes += abs(len(sanitized) - len(original_lines))

    fd, tmp_path = tempfile.mkstemp(dir=str(env_path.parent), suffix=".tmp", prefix=".env_")
    try:
        with os.fdopen(fd, "w", **write_kw) as f:
            f.writelines(sanitized)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, env_path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    _secure_file(env_path)
    return fixes


def _check_non_ascii_credential(key: str, value: str) -> str:
    """警告并从凭证值中去除非 ASCII 字符。

    API 密钥和 token 必须是纯 ASCII — 它们作为 HTTP 头部值发送，
    httpx/httpcore 将其编码为 ASCII。非 ASCII 字符
    （通常由从富文本编辑器或 PDF 中复制粘贴引入，
    这些编辑器用相似的 Unicode 字形替代 ASCII 字母）会在
    请求时导致 ``UnicodeEncodeError: 'ascii' codec can't encode character``。

    返回清理后的（仅 ASCII）值。如果发现并移除了
    非 ASCII 字符则打印警告。
    """
    try:
        value.encode("ascii")
        return value  # all ASCII — nothing to do
    except UnicodeEncodeError:
        pass

    # Build a readable list of the offending characters
    bad_chars: list[str] = []
    for i, ch in enumerate(value):
        if ord(ch) > 127:
            bad_chars.append(f"  position {i}: {ch!r} (U+{ord(ch):04X})")
    sanitized = value.encode("ascii", errors="ignore").decode("ascii")

    import sys
    print(
        f"\n  Warning: {key} contains non-ASCII characters that will break API requests.\n"
        f"  This usually happens when copy-pasting from a PDF, rich-text editor,\n"
        f"  or web page that substitutes lookalike Unicode glyphs for ASCII letters.\n"
        f"\n"
        + "\n".join(f"  {line}" for line in bad_chars[:5])
        + ("\n  ... and more" if len(bad_chars) > 5 else "")
        + f"\n\n  The non-ASCII characters have been stripped automatically.\n"
        f"  If authentication fails, re-copy the key from the provider's dashboard.\n",
        file=sys.stderr,
    )
    return sanitized


def save_env_value(key: str, value: str):
    """在 ~/.hermes/.env 中保存或更新一个值。"""
    if is_managed():
        managed_error(f"set {key}")
        return
    if not _ENV_VAR_NAME_RE.match(key):
        raise ValueError(f"Invalid environment variable name: {key!r}")
    value = value.replace("\n", "").replace("\r", "")
    # API keys / tokens must be ASCII — strip non-ASCII with a warning.
    value = _check_non_ascii_credential(key, value)
    ensure_hermes_home()
    env_path = get_env_path()
    
    # On Windows, open() defaults to the system locale (cp1252) which can
    # cause OSError errno 22 on UTF-8 .env files.
    read_kw = {"encoding": "utf-8", "errors": "replace"} if _IS_WINDOWS else {}
    write_kw = {"encoding": "utf-8"} if _IS_WINDOWS else {}

    lines = []
    if env_path.exists():
        with open(env_path, **read_kw) as f:
            lines = f.readlines()
        # Sanitize on every read: split concatenated keys, drop stale placeholders
        lines = _sanitize_env_lines(lines)
    
    # Find and update or append
    found = False
    for i, line in enumerate(lines):
        if line.strip().startswith(f"{key}="):
            lines[i] = f"{key}={value}\n"
            found = True
            break
    
    if not found:
        # Ensure there's a newline at the end of the file before appending
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines.append(f"{key}={value}\n")
    
    fd, tmp_path = tempfile.mkstemp(dir=str(env_path.parent), suffix='.tmp', prefix='.env_')
    # Preserve original permissions so Docker volume mounts aren't clobbered.
    original_mode = None
    if env_path.exists():
        try:
            original_mode = stat.S_IMODE(env_path.stat().st_mode)
        except OSError:
            pass
    try:
        with os.fdopen(fd, 'w', **write_kw) as f:
            f.writelines(lines)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, env_path)
        # Restore original permissions before _secure_file may tighten them.
        if original_mode is not None:
            try:
                os.chmod(env_path, original_mode)
            except OSError:
                pass
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    _secure_file(env_path)

    os.environ[key] = value


def remove_env_value(key: str) -> bool:
    """从 ~/.hermes/.env 和 os.environ 中移除一个键。

    Returns True if the key was found and removed, False otherwise.
    """
    if is_managed():
        managed_error(f"remove {key}")
        return False
    if not _ENV_VAR_NAME_RE.match(key):
        raise ValueError(f"Invalid environment variable name: {key!r}")
    env_path = get_env_path()
    if not env_path.exists():
        os.environ.pop(key, None)
        return False

    read_kw = {"encoding": "utf-8", "errors": "replace"} if _IS_WINDOWS else {}
    write_kw = {"encoding": "utf-8"} if _IS_WINDOWS else {}

    with open(env_path, **read_kw) as f:
        lines = f.readlines()
    lines = _sanitize_env_lines(lines)

    new_lines = [line for line in lines if not line.strip().startswith(f"{key}=")]
    found = len(new_lines) < len(lines)

    if found:
        fd, tmp_path = tempfile.mkstemp(dir=str(env_path.parent), suffix='.tmp', prefix='.env_')
        # Preserve original permissions so Docker volume mounts aren't clobbered.
        original_mode = None
        try:
            original_mode = stat.S_IMODE(env_path.stat().st_mode)
        except OSError:
            pass
        try:
            with os.fdopen(fd, 'w', **write_kw) as f:
                f.writelines(new_lines)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, env_path)
            if original_mode is not None:
                try:
                    os.chmod(env_path, original_mode)
                except OSError:
                    pass
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        _secure_file(env_path)

    os.environ.pop(key, None)
    return found


def save_anthropic_oauth_token(value: str, save_fn=None):
    """持久化 Anthropic OAuth/setup token 并清除 API 密钥槽。"""
    writer = save_fn or save_env_value
    writer("ANTHROPIC_TOKEN", value)
    writer("ANTHROPIC_API_KEY", "")


def use_anthropic_claude_code_credentials(save_fn=None):
    """使用 Claude Code 自己的凭证文件，而非持久化环境 token。"""
    writer = save_fn or save_env_value
    writer("ANTHROPIC_TOKEN", "")
    writer("ANTHROPIC_API_KEY", "")


def save_anthropic_api_key(value: str, save_fn=None):
    """持久化 Anthropic API 密钥并清除 OAuth/setup-token 槽。"""
    writer = save_fn or save_env_value
    writer("ANTHROPIC_API_KEY", value)
    writer("ANTHROPIC_TOKEN", "")


def save_env_value_secure(key: str, value: str) -> Dict[str, Any]:
    save_env_value(key, value)
    return {
        "success": True,
        "stored_as": key,
        "validated": False,
    }



def reload_env() -> int:
    """重新读取 ~/.hermes/.env 到 os.environ。返回更新的变量数。

    添加/更新已更改的变量，并移除已从 .env 文件中删除的变量
    （但仅限 Hermes 已知的变量 — OPTIONAL_ENV_VARS 和
    _EXTRA_ENV_KEYS — 以避免覆盖无关的环境变量）。
    """
    env_vars = load_env()
    known_keys = set(OPTIONAL_ENV_VARS.keys()) | _EXTRA_ENV_KEYS
    count = 0
    for key, value in env_vars.items():
        if os.environ.get(key) != value:
            os.environ[key] = value
            count += 1
    # Remove known Hermes vars that are no longer in .env
    for key in known_keys:
        if key not in env_vars and key in os.environ:
            del os.environ[key]
            count += 1
    return count


def get_env_value(key: str) -> Optional[str]:
    """从 ~/.hermes/.env 或环境变量获取一个值。"""
    # Check environment first
    if key in os.environ:
        return os.environ[key]
    
    # Then check .env file
    env_vars = load_env()
    return env_vars.get(key)


# =============================================================================
# 配置显示
# =============================================================================

def redact_key(key: str) -> str:
    """脱敏 API 密钥以供显示。"""
    if not key:
        return color("(not set)", Colors.DIM)
    if len(key) < 12:
        return "***"
    return key[:4] + "..." + key[-4:]


def show_config():
    """显示当前配置。"""
    config = load_config()
    
    print()
    print(color("┌─────────────────────────────────────────────────────────┐", Colors.CYAN))
    print(color("│              ⚕ Hermes Configuration                    │", Colors.CYAN))
    print(color("└─────────────────────────────────────────────────────────┘", Colors.CYAN))
    
    # Paths
    print()
    print(color("◆ Paths", Colors.CYAN, Colors.BOLD))
    print(f"  Config:       {get_config_path()}")
    print(f"  Secrets:      {get_env_path()}")
    print(f"  Install:      {get_project_root()}")
    
    # API Keys
    print()
    print(color("◆ API Keys", Colors.CYAN, Colors.BOLD))
    
    keys = [
        ("OPENROUTER_API_KEY", "OpenRouter"),
        ("VOICE_TOOLS_OPENAI_KEY", "OpenAI (STT/TTS)"),
        ("EXA_API_KEY", "Exa"),
        ("PARALLEL_API_KEY", "Parallel"),
        ("FIRECRAWL_API_KEY", "Firecrawl"),
        ("TAVILY_API_KEY", "Tavily"),
        ("BROWSERBASE_API_KEY", "Browserbase"),
        ("BROWSER_USE_API_KEY", "Browser Use"),
        ("FAL_KEY", "FAL"),
    ]
    
    for env_key, name in keys:
        value = get_env_value(env_key)
        print(f"  {name:<14} {redact_key(value)}")
    from hermes_cli.auth import get_anthropic_key
    anthropic_value = get_anthropic_key()
    print(f"  {'Anthropic':<14} {redact_key(anthropic_value)}")
    
    # Model settings
    print()
    print(color("◆ Model", Colors.CYAN, Colors.BOLD))
    print(f"  Model:        {config.get('model', 'not set')}")
    print(f"  Max turns:    {config.get('agent', {}).get('max_turns', DEFAULT_CONFIG['agent']['max_turns'])}")
    
    # Display
    print()
    print(color("◆ Display", Colors.CYAN, Colors.BOLD))
    display = config.get('display', {})
    print(f"  Personality:  {display.get('personality', 'kawaii')}")
    print(f"  Reasoning:    {'on' if display.get('show_reasoning', False) else 'off'}")
    print(f"  Bell:         {'on' if display.get('bell_on_complete', False) else 'off'}")

    # Terminal
    print()
    print(color("◆ Terminal", Colors.CYAN, Colors.BOLD))
    terminal = config.get('terminal', {})
    print(f"  Backend:      {terminal.get('backend', 'local')}")
    print(f"  Working dir:  {terminal.get('cwd', '.')}")
    print(f"  Timeout:      {terminal.get('timeout', 60)}s")
    
    if terminal.get('backend') == 'docker':
        print(f"  Docker image: {terminal.get('docker_image', 'nikolaik/python-nodejs:python3.11-nodejs20')}")
    elif terminal.get('backend') == 'singularity':
        print(f"  Image:        {terminal.get('singularity_image', 'docker://nikolaik/python-nodejs:python3.11-nodejs20')}")
    elif terminal.get('backend') == 'modal':
        print(f"  Modal image:  {terminal.get('modal_image', 'nikolaik/python-nodejs:python3.11-nodejs20')}")
        modal_token = get_env_value('MODAL_TOKEN_ID')
        print(f"  Modal token:  {'configured' if modal_token else '(not set)'}")
    elif terminal.get('backend') == 'daytona':
        print(f"  Daytona image: {terminal.get('daytona_image', 'nikolaik/python-nodejs:python3.11-nodejs20')}")
        daytona_key = get_env_value('DAYTONA_API_KEY')
        print(f"  API key:      {'configured' if daytona_key else '(not set)'}")
    elif terminal.get('backend') == 'ssh':
        ssh_host = get_env_value('TERMINAL_SSH_HOST')
        ssh_user = get_env_value('TERMINAL_SSH_USER')
        print(f"  SSH host:     {ssh_host or '(not set)'}")
        print(f"  SSH user:     {ssh_user or '(not set)'}")
    
    # Timezone
    print()
    print(color("◆ Timezone", Colors.CYAN, Colors.BOLD))
    tz = config.get('timezone', '')
    if tz:
        print(f"  Timezone:     {tz}")
    else:
        print(f"  Timezone:     {color('(server-local)', Colors.DIM)}")

    # Compression
    print()
    print(color("◆ Context Compression", Colors.CYAN, Colors.BOLD))
    compression = config.get('compression', {})
    enabled = compression.get('enabled', True)
    print(f"  Enabled:      {'yes' if enabled else 'no'}")
    if enabled:
        print(f"  Threshold:    {compression.get('threshold', 0.50) * 100:.0f}%")
        print(f"  Target ratio: {compression.get('target_ratio', 0.20) * 100:.0f}% of threshold preserved")
        print(f"  Protect last: {compression.get('protect_last_n', 20)} messages")
        _aux_comp = config.get('auxiliary', {}).get('compression', {})
        _sm = _aux_comp.get('model', '') or '(auto)'
        print(f"  Model:        {_sm}")
        comp_provider = _aux_comp.get('provider', 'auto')
        if comp_provider and comp_provider != 'auto':
            print(f"  Provider:     {comp_provider}")
    
    # Auxiliary models
    auxiliary = config.get('auxiliary', {})
    aux_tasks = {
        "Vision":      auxiliary.get('vision', {}),
        "Web extract": auxiliary.get('web_extract', {}),
    }
    has_overrides = any(
        t.get('provider', 'auto') != 'auto' or t.get('model', '')
        for t in aux_tasks.values()
    )
    if has_overrides:
        print()
        print(color("◆ Auxiliary Models (overrides)", Colors.CYAN, Colors.BOLD))
        for label, task_cfg in aux_tasks.items():
            prov = task_cfg.get('provider', 'auto')
            mdl = task_cfg.get('model', '')
            if prov != 'auto' or mdl:
                parts = [f"provider={prov}"]
                if mdl:
                    parts.append(f"model={mdl}")
                print(f"  {label:12s}  {', '.join(parts)}")
    
    # Messaging
    print()
    print(color("◆ Messaging Platforms", Colors.CYAN, Colors.BOLD))
    
    telegram_token = get_env_value('TELEGRAM_BOT_TOKEN')
    discord_token = get_env_value('DISCORD_BOT_TOKEN')
    
    print(f"  Telegram:     {'configured' if telegram_token else color('not configured', Colors.DIM)}")
    print(f"  Discord:      {'configured' if discord_token else color('not configured', Colors.DIM)}")
    
    # Skill config
    try:
        from agent.skill_utils import discover_all_skill_config_vars, resolve_skill_config_values
        skill_vars = discover_all_skill_config_vars()
        if skill_vars:
            resolved = resolve_skill_config_values(skill_vars)
            print()
            print(color("◆ Skill Settings", Colors.CYAN, Colors.BOLD))
            for var in skill_vars:
                key = var["key"]
                value = resolved.get(key, "")
                skill_name = var.get("skill", "")
                display_val = str(value) if value else color("(not set)", Colors.DIM)
                print(f"  {key:<20s} {display_val}  {color(f'[{skill_name}]', Colors.DIM)}")
    except Exception:
        pass

    print()
    print(color("─" * 60, Colors.DIM))
    print(color("  hermes config edit     # Edit config file", Colors.DIM))
    print(color("  hermes config set <key> <value>", Colors.DIM))
    print(color("  hermes setup           # Run setup wizard", Colors.DIM))
    print()


def edit_config():
    """在用户编辑器中打开配置文件。"""
    if is_managed():
        managed_error("edit configuration")
        return
    config_path = get_config_path()
    
    # Ensure config exists
    if not config_path.exists():
        save_config(DEFAULT_CONFIG)
        print(f"Created {config_path}")
    
    # Find editor
    editor = os.getenv('EDITOR') or os.getenv('VISUAL')
    
    if not editor:
        # Try common editors
        for cmd in ['nano', 'vim', 'vi', 'code', 'notepad']:
            import shutil
            if shutil.which(cmd):
                editor = cmd
                break
    
    if not editor:
        print("No editor found. Config file is at:")
        print(f"  {config_path}")
        return
    
    print(f"Opening {config_path} in {editor}...")
    subprocess.run([editor, str(config_path)])


def set_config_value(key: str, value: str):
    """设置一个配置值。"""
    if is_managed():
        managed_error("set configuration values")
        return
    # Check if it's an API key (goes to .env)
    api_keys = [
        'OPENROUTER_API_KEY', 'OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'VOICE_TOOLS_OPENAI_KEY',
        'EXA_API_KEY', 'PARALLEL_API_KEY', 'FIRECRAWL_API_KEY', 'FIRECRAWL_API_URL',
        'FIRECRAWL_GATEWAY_URL', 'TOOL_GATEWAY_DOMAIN', 'TOOL_GATEWAY_SCHEME',
        'TOOL_GATEWAY_USER_TOKEN', 'TAVILY_API_KEY',
        'BROWSERBASE_API_KEY', 'BROWSERBASE_PROJECT_ID', 'BROWSER_USE_API_KEY',
        'FAL_KEY', 'TELEGRAM_BOT_TOKEN', 'DISCORD_BOT_TOKEN',
        'TERMINAL_SSH_HOST', 'TERMINAL_SSH_USER', 'TERMINAL_SSH_KEY',
        'SUDO_PASSWORD', 'SLACK_BOT_TOKEN', 'SLACK_APP_TOKEN',
        'GITHUB_TOKEN', 'HONCHO_API_KEY', 'WANDB_API_KEY',
        'TINKER_API_KEY',
    ]
    
    if key.upper() in api_keys or key.upper().endswith(('_API_KEY', '_TOKEN')) or key.upper().startswith('TERMINAL_SSH'):
        save_env_value(key.upper(), value)
        print(f"✓ Set {key} in {get_env_path()}")
        return
    
    # Otherwise it goes to config.yaml
    # Read the raw user config (not merged with defaults) to avoid
    # dumping all default values back to the file
    config_path = get_config_path()
    user_config = {}
    if config_path.exists():
        try:
            with open(config_path, encoding="utf-8") as f:
                user_config = yaml.safe_load(f) or {}
        except Exception:
            user_config = {}
    
    # Handle nested keys (e.g., "tts.provider")
    parts = key.split('.')
    current = user_config
    
    for part in parts[:-1]:
        if part not in current or not isinstance(current.get(part), dict):
            current[part] = {}
        current = current[part]
    
    # Convert value to appropriate type
    if value.lower() in ('true', 'yes', 'on'):
        value = True
    elif value.lower() in ('false', 'no', 'off'):
        value = False
    elif value.isdigit():
        value = int(value)
    elif value.replace('.', '', 1).isdigit():
        value = float(value)
    
    current[parts[-1]] = value
    
    # Write only user config back (not the full merged defaults)
    ensure_hermes_home()
    from utils import atomic_yaml_write
    atomic_yaml_write(config_path, user_config, sort_keys=False)
    
    # Keep .env in sync for keys that terminal_tool reads directly from env vars.
    # config.yaml is authoritative, but terminal_tool only reads TERMINAL_ENV etc.
    _config_to_env_sync = {
        "terminal.backend": "TERMINAL_ENV",
        "terminal.modal_mode": "TERMINAL_MODAL_MODE",
        "terminal.docker_image": "TERMINAL_DOCKER_IMAGE",
        "terminal.singularity_image": "TERMINAL_SINGULARITY_IMAGE",
        "terminal.modal_image": "TERMINAL_MODAL_IMAGE",
        "terminal.daytona_image": "TERMINAL_DAYTONA_IMAGE",
        "terminal.docker_mount_cwd_to_workspace": "TERMINAL_DOCKER_MOUNT_CWD_TO_WORKSPACE",
        "terminal.cwd": "TERMINAL_CWD",
        "terminal.timeout": "TERMINAL_TIMEOUT",
        "terminal.sandbox_dir": "TERMINAL_SANDBOX_DIR",
        "terminal.persistent_shell": "TERMINAL_PERSISTENT_SHELL",
        "terminal.container_cpu": "TERMINAL_CONTAINER_CPU",
        "terminal.container_memory": "TERMINAL_CONTAINER_MEMORY",
        "terminal.container_disk": "TERMINAL_CONTAINER_DISK",
        "terminal.container_persistent": "TERMINAL_CONTAINER_PERSISTENT",
    }
    if key in _config_to_env_sync:
        save_env_value(_config_to_env_sync[key], str(value))

    print(f"✓ Set {key} = {value} in {config_path}")


# =============================================================================
# 命令处理器
# =============================================================================

def config_command(args):
    """处理配置子命令。"""
    subcmd = getattr(args, 'config_command', None)
    
    if subcmd is None or subcmd == "show":
        show_config()
    
    elif subcmd == "edit":
        edit_config()
    
    elif subcmd == "set":
        key = getattr(args, 'key', None)
        value = getattr(args, 'value', None)
        if not key or value is None:
            print("Usage: hermes config set <key> <value>")
            print()
            print("Examples:")
            print("  hermes config set model anthropic/claude-sonnet-4")
            print("  hermes config set terminal.backend docker")
            print("  hermes config set OPENROUTER_API_KEY sk-or-...")
            sys.exit(1)
        set_config_value(key, value)
    
    elif subcmd == "path":
        print(get_config_path())
    
    elif subcmd == "env-path":
        print(get_env_path())
    
    elif subcmd == "migrate":
        print()
        print(color("🔄 Checking configuration for updates...", Colors.CYAN, Colors.BOLD))
        print()
        
        # Check what's missing
        missing_env = get_missing_env_vars(required_only=False)
        missing_config = get_missing_config_fields()
        current_ver, latest_ver = check_config_version()
        
        if not missing_env and not missing_config and current_ver >= latest_ver:
            print(color("✓ Configuration is up to date!", Colors.GREEN))
            print()
            return
        
        # Show what needs to be updated
        if current_ver < latest_ver:
            print(f"  Config version: {current_ver} → {latest_ver}")
        
        if missing_config:
            print(f"\n  {len(missing_config)} new config option(s) will be added with defaults")
        
        required_missing = [v for v in missing_env if v.get("is_required")]
        optional_missing = [
            v for v in missing_env
            if not v.get("is_required") and not v.get("advanced")
        ]
        
        if required_missing:
            print(f"\n  ⚠️  {len(required_missing)} required API key(s) missing:")
            for var in required_missing:
                print(f"     • {var['name']}")
        
        if optional_missing:
            print(f"\n  ℹ️  {len(optional_missing)} optional API key(s) not configured:")
            for var in optional_missing:
                tools = var.get("tools", [])
                tools_str = f" (enables: {', '.join(tools[:2])})" if tools else ""
                print(f"     • {var['name']}{tools_str}")
        
        print()
        
        # Run migration
        results = migrate_config(interactive=True, quiet=False)
        
        print()
        if results["env_added"] or results["config_added"]:
            print(color("✓ Configuration updated!", Colors.GREEN))
        
        if results["warnings"]:
            print()
            for warning in results["warnings"]:
                print(color(f"  ⚠️  {warning}", Colors.YELLOW))
        
        print()
    
    elif subcmd == "check":
        # Non-interactive check for what's missing
        print()
        print(color("📋 Configuration Status", Colors.CYAN, Colors.BOLD))
        print()
        
        current_ver, latest_ver = check_config_version()
        if current_ver >= latest_ver:
            print(f"  Config version: {current_ver} ✓")
        else:
            print(color(f"  Config version: {current_ver} → {latest_ver} (update available)", Colors.YELLOW))
        
        print()
        print(color("  Required:", Colors.BOLD))
        for var_name in REQUIRED_ENV_VARS:
            if get_env_value(var_name):
                print(f"    ✓ {var_name}")
            else:
                print(color(f"    ✗ {var_name} (missing)", Colors.RED))
        
        print()
        print(color("  Optional:", Colors.BOLD))
        for var_name, info in OPTIONAL_ENV_VARS.items():
            if get_env_value(var_name):
                print(f"    ✓ {var_name}")
            else:
                tools = info.get("tools", [])
                tools_str = f" → {', '.join(tools[:2])}" if tools else ""
                print(color(f"    ○ {var_name}{tools_str}", Colors.DIM))
        
        missing_config = get_missing_config_fields()
        if missing_config:
            print()
            print(color(f"  {len(missing_config)} new config option(s) available", Colors.YELLOW))
            print("    Run 'hermes config migrate' to add them")
        
        print()
    
    else:
        print(f"Unknown config command: {subcmd}")
        print()
        print("Available commands:")
        print("  hermes config           Show current configuration")
        print("  hermes config edit      Open config in editor")
        print("  hermes config set <key> <value>   Set a config value")
        print("  hermes config check     Check for missing/outdated config")
        print("  hermes config migrate   Update config with new options")
        print("  hermes config path      Show config file path")
        print("  hermes config env-path  Show .env file path")
        sys.exit(1)
