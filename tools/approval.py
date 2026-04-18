"""危险命令审批 -- 检测、提示和按会话状态管理。

此模块是危险命令系统的唯一事实来源:
- 模式检测 (DANGEROUS_PATTERNS, detect_dangerous_command)
- 按会话审批状态（线程安全，按 session_key 索引）
- 审批提示（CLI 交互式 + 网关异步）
- 智能审批：通过辅助 LLM 自动批准低风险命令
- 永久允许列表持久化 (config.yaml)
"""

import contextvars
import logging
import os
import re
import sys
import threading
import time
import unicodedata
from typing import Optional

logger = logging.getLogger(__name__)

# 按线程/任务的网关会话身份标识。
# 网关在执行器线程中并发运行代理轮次，因此读取进程全局环境变量
# 获取会话身份是不安全的（存在竞态条件）。保留环境变量回退用于
# 旧版单线程调用方，但优先使用上下文本地值。
_approval_session_key: contextvars.ContextVar[str] = contextvars.ContextVar(
    "approval_session_key",
    default="",
)


def set_current_session_key(session_key: str) -> contextvars.Token[str]:
    """将活动审批会话密钥绑定到当前上下文。"""
    return _approval_session_key.set(session_key or "")


def reset_current_session_key(token: contextvars.Token[str]) -> None:
    """恢复之前的审批会话密钥上下文。"""
    _approval_session_key.reset(token)


def get_current_session_key(default: str = "default") -> str:
    """返回活动会话密钥，优先使用上下文本地状态。

    解析优先级:
    1. 审批专用的 contextvars（网关在 agent.run 之前设置）
    2. session_context 的 contextvars（由 _set_session_env 设置）
    3. os.environ 回退（CLI、定时任务、测试）
    """
    session_key = _approval_session_key.get()
    if session_key:
        return session_key
    from gateway.session_context import get_session_env
    return get_session_env("HERMES_SESSION_KEY", default)

# 即使通过 $HOME 或 $HERMES_HOME 等 shell 展开引用，
# 也应触发审批的敏感写入目标。
_SSH_SENSITIVE_PATH = r'(?:~|\$home|\$\{home\})/\.ssh(?:/|$)'
_HERMES_ENV_PATH = (
    r'(?:~\/\.hermes/|'
    r'(?:\$home|\$\{home\})/\.hermes/|'
    r'(?:\$hermes_home|\$\{hermes_home\})/)'
    r'\.env\b'
)
_SENSITIVE_WRITE_TARGET = (
    r'(?:/etc/|/dev/sd|'
    rf'{_SSH_SENSITIVE_PATH}|'
    rf'{_HERMES_ENV_PATH})'
)

# =========================================================================
# 危险命令模式列表
# =========================================================================

DANGEROUS_PATTERNS = [
    (r'\brm\s+(-[^\s]*\s+)*/', "delete in root path"),                                  # 在根路径下删除
    (r'\brm\s+-[^\s]*r', "recursive delete"),                                            # 递归删除
    (r'\brm\s+--recursive\b', "recursive delete (long flag)"),                           # 递归删除（长标志）
    (r'\bchmod\s+(-[^\s]*\s+)*(777|666|o\+[rwx]*w|a\+[rwx]*w)\b', "world/other-writable permissions"),  # 全局/其他用户可写权限
    (r'\bchmod\s+--recursive\b.*(777|666|o\+[rwx]*w|a\+[rwx]*w)', "recursive world/other-writable (long flag)"),  # 递归全局可写（长标志）
    (r'\bchown\s+(-[^\s]*)?R\s+root', "recursive chown to root"),                       # 递归更改所有者为 root
    (r'\bchown\s+--recursive\b.*root', "recursive chown to root (long flag)"),           # 递归更改所有者为 root（长标志）
    (r'\bmkfs\b', "format filesystem"),                                                  # 格式化文件系统
    (r'\bdd\s+.*if=', "disk copy"),                                                      # 磁盘拷贝
    (r'>\s*/dev/sd', "write to block device"),                                           # 写入块设备
    (r'\bDROP\s+(TABLE|DATABASE)\b', "SQL DROP"),                                        # SQL 删除表/数据库
    (r'\bDELETE\s+FROM\b(?!.*\bWHERE\b)', "SQL DELETE without WHERE"),                  # 无 WHERE 条件的 SQL DELETE
    (r'\bTRUNCATE\s+(TABLE)?\s*\w', "SQL TRUNCATE"),                                     # SQL 截断表
    (r'>\s*/etc/', "overwrite system config"),                                           # 覆盖系统配置
    (r'\bsystemctl\s+(-[^\s]+\s+)*(stop|restart|disable|mask)\b', "stop/restart system service"),  # 停止/重启系统服务
    (r'\bkill\s+-9\s+-1\b', "kill all processes"),                                       # 杀死所有进程
    (r'\bpkill\s+-9\b', "force kill processes"),                                         # 强制杀死进程
    (r':\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:', "fork bomb"),                          # fork 炸弹
    # 通过 -c 或组合标志（如 -lc, -ic 等）进行的 shell 调用
    (r'\b(bash|sh|zsh|ksh)\s+-[^\s]*c(\s+|$)', "shell command via -c/-lc flag"),
    # 通过 -e/-c 标志执行脚本
    (r'\b(python[23]?|perl|ruby|node)\s+-[ec]\s+', "script execution via -e/-c flag"),
    # 将远程内容管道传输到 shell 执行
    (r'\b(curl|wget)\b.*\|\s*(ba)?sh\b', "pipe remote content to shell"),
    # 通过进程替换执行远程脚本
    (r'\b(bash|sh|zsh|ksh)\s+<\s*<?\s*\(\s*(curl|wget)\b', "execute remote script via process substitution"),
    # 通过 tee 覆盖系统文件
    (rf'\btee\b.*["\']?{_SENSITIVE_WRITE_TARGET}', "overwrite system file via tee"),
    # 通过重定向覆盖系统文件
    (rf'>>?\s*["\']?{_SENSITIVE_WRITE_TARGET}', "overwrite system file via redirection"),
    (r'\bxargs\s+.*\brm\b', "xargs with rm"),                                           # xargs 配合 rm
    (r'\bfind\b.*-exec\s+(/\S*/)?rm\b', "find -exec rm"),                               # find -exec rm
    (r'\bfind\b.*-delete\b', "find -delete"),                                            # find -delete
    # 网关生命周期保护: 防止代理杀死自己的网关进程。
    # 这些命令会触发网关重启/停止，终止所有正在运行的代理。
    (r'\bhermes\s+gateway\s+(stop|restart)\b', "stop/restart hermes gateway (kills running agents)"),
    (r'\bhermes\s+update\b', "hermes update (restarts gateway, kills running agents)"),
    # 网关保护: 永远不要在 systemd 管理之外启动网关
    (r'gateway\s+run\b.*(&\s*$|&\s*;|\bdisown\b|\bsetsid\b)', "start gateway outside systemd (use 'systemctl --user restart hermes-gateway')"),
    (r'\bnohup\b.*gateway\s+run\b', "start gateway outside systemd (use 'systemctl --user restart hermes-gateway')"),
    # 自杀保护: 防止代理杀死自己的进程
    (r'\b(pkill|killall)\b.*\b(hermes|gateway|cli\.py)\b', "kill hermes/gateway process (self-termination)"),
    # 通过 kill + 命令替换（pgrep/pidof）进行自杀。
    # 上面的名称模式能捕获 `pkill hermes` 但无法捕获
    # `kill -9 $(pgrep -f hermes)`，因为替换在检测时是不透明的。
    # 改为捕获结构性模式。
    (r'\bkill\b.*\$\(\s*pgrep\b', "kill process via pgrep expansion (self-termination)"),
    (r'\bkill\b.*`\s*pgrep\b', "kill process via backtick pgrep expansion (self-termination)"),
    # 复制/移动/安装文件到敏感系统路径
    (r'\b(cp|mv|install)\b.*\s/etc/', "copy/move file into /etc/"),
    # 就地编辑系统配置
    (r'\bsed\s+-[^\s]*i.*\s/etc/', "in-place edit of system config"),
    (r'\bsed\s+--in-place\b.*\s/etc/', "in-place edit of system config (long flag)"),
    # 通过 heredoc 执行脚本 — 绕过上面的 -e/-c 标志模式。
    # `python3 << 'EOF'` 通过 stdin 传入任意代码，不需要 -c/-e 标志。
    (r'\b(python[23]?|perl|ruby|node)\s+<<', "script execution via heredoc"),
    # Git 破坏性操作：可能丢失未提交的工作或重写共享历史。
    # 不被 rm/chmod 等模式捕获。
    (r'\bgit\s+reset\s+--hard\b', "git reset --hard (destroys uncommitted changes)"),
    (r'\bgit\s+push\b.*--force\b', "git force push (rewrites remote history)"),
    (r'\bgit\s+push\b.*-f\b', "git force push short flag (rewrites remote history)"),
    (r'\bgit\s+clean\s+-[^\s]*f', "git clean with force (deletes untracked files)"),
    (r'\bgit\s+branch\s+-D\b', "git branch force delete"),
    # chmod +x 后立即执行脚本 — 捕获两步模式：先使脚本可执行然后立即运行。
    # 脚本内容可能包含单个模式无法捕获的危险命令。
    (r'\bchmod\s+\+x\b.*[;&|]+\s*\./', "chmod +x followed by immediate execution"),
]


def _legacy_pattern_key(pattern: str) -> str:
    """为向后兼容性生成旧版基于正则表达式的审批密钥。"""
    return pattern.split(r'\b')[1] if r'\b' in pattern else pattern[:20]


_PATTERN_KEY_ALIASES: dict[str, set[str]] = {}
for _pattern, _description in DANGEROUS_PATTERNS:
    _legacy_key = _legacy_pattern_key(_pattern)
    _canonical_key = _description
    _PATTERN_KEY_ALIASES.setdefault(_canonical_key, set()).update({_canonical_key, _legacy_key})
    _PATTERN_KEY_ALIASES.setdefault(_legacy_key, set()).update({_legacy_key, _canonical_key})


def _approval_key_aliases(pattern_key: str) -> set[str]:
    """返回应与此模式匹配的所有审批密钥。

    新的审批使用人类可读的描述字符串，但旧的
    command_allowlist 条目和会话审批可能仍包含
    历史的基于正则表达式的密钥。
    """
    return _PATTERN_KEY_ALIASES.get(pattern_key, {pattern_key})


# =========================================================================
# 检测
# =========================================================================

def _normalize_command_for_detection(command: str) -> str:
    """在危险模式匹配之前标准化命令字符串。

    去除 ANSI 转义序列（通过 tools.ansi_strip 支持完整 ECMA-48），
    空字节，并标准化 Unicode 全角字符，以防止
    混淆技术绕过基于模式的检测。
    """
    from tools.ansi_strip import strip_ansi

    # 去除所有 ANSI 转义序列（CSI、OSC、DCS、8 位 C1 等）
    command = strip_ansi(command)
    # 去除空字节
    command = command.replace('\x00', '')
    # 标准化 Unicode（全角拉丁字母、半角片假名等）
    command = unicodedata.normalize('NFKC', command)
    return command


def detect_dangerous_command(command: str) -> tuple:
    """检查命令是否匹配任何危险模式。

    返回:
        (is_dangerous, pattern_key, description) 或 (False, None, None)
    """
    command_lower = _normalize_command_for_detection(command).lower()
    for pattern, description in DANGEROUS_PATTERNS:
        if re.search(pattern, command_lower, re.IGNORECASE | re.DOTALL):
            pattern_key = description
            return (True, pattern_key, description)
    return (False, None, None)


# =========================================================================
# 按会话审批状态（线程安全）
# =========================================================================

_lock = threading.Lock()
_pending: dict[str, dict] = {}           # 待处理的审批请求
_session_approved: dict[str, set] = {}   # 按会话已批准的模式
_session_yolo: set[str] = set()          # 启用 YOLO 绕过的会话
_permanent_approved: set = set()         # 永久允许列表

# =========================================================================
# 阻塞式网关审批（镜像 CLI 的同步 input() 流程）
# =========================================================================
# 按会话的待审批队列。多个线程（并行子代理、execute_code RPC 处理器）
# 可以并发阻塞 — 每个线程获得自己的 threading.Event。
# /approve 解决最旧的一个，/approve all 解决会话中所有待处理的审批。


class _ApprovalEntry:
    """网关会话中的一条待审批危险命令。"""
    __slots__ = ("event", "data", "result")

    def __init__(self, data: dict):
        self.event = threading.Event()
        self.data = data          # command, description, pattern_keys, ...
        self.result: Optional[str] = None  # "once"|"session"|"always"|"deny"


_gateway_queues: dict[str, list] = {}        # session_key -> [_ApprovalEntry, ...]
_gateway_notify_cbs: dict[str, object] = {}  # session_key -> callable(approval_data)


def register_gateway_notify(session_key: str, cb) -> None:
    """注册按会话的回调，用于向用户发送审批请求。

    回调签名为 ``cb(approval_data: dict) -> None``，其中
    *approval_data* 包含 ``command``、``description`` 和
    ``pattern_keys``。回调桥接同步到异步（在代理线程中运行，
    必须在事件循环上调度实际发送）。
    """
    with _lock:
        _gateway_notify_cbs[session_key] = cb


def unregister_gateway_notify(session_key: str) -> None:
    """取消注册按会话的网关审批回调。

    向该会话的所有阻塞线程发送信号，防止它们永远挂起
    （例如当代理运行完成或被中断时）。
    """
    with _lock:
        _gateway_notify_cbs.pop(session_key, None)
        entries = _gateway_queues.pop(session_key, [])
        for entry in entries:
            entry.event.set()


def resolve_gateway_approval(session_key: str, choice: str,
                             resolve_all: bool = False) -> int:
    """由网关的 /approve 或 /deny 处理器调用，解除等待中的代理线程阻塞。

    当 *resolve_all* 为 True 时，会话中所有待审批的请求一次性
    全部解决（``/approve all``）。否则只解决最旧的一个（FIFO）。

    返回已解决的审批数量（0 表示没有待处理的）。
    """
    with _lock:
        queue = _gateway_queues.get(session_key)
        if not queue:
            return 0
        if resolve_all:
            targets = list(queue)
            queue.clear()
        else:
            targets = [queue.pop(0)]
        if not queue:
            _gateway_queues.pop(session_key, None)

    for entry in targets:
        entry.result = choice
        entry.event.set()
    return len(targets)


def has_blocking_approval(session_key: str) -> bool:
    """检查会话是否有一个或多个等待中的阻塞式网关审批。"""
    with _lock:
        return bool(_gateway_queues.get(session_key))


def submit_pending(session_key: str, approval: dict):
    """存储会话的待审批请求。"""
    with _lock:
        _pending[session_key] = approval


def approve_session(session_key: str, pattern_key: str):
    """仅为本会话批准某个模式。"""
    with _lock:
        _session_approved.setdefault(session_key, set()).add(pattern_key)


def enable_session_yolo(session_key: str) -> None:
    """为单个会话密钥启用 YOLO 绕过。"""
    if not session_key:
        return
    with _lock:
        _session_yolo.add(session_key)


def disable_session_yolo(session_key: str) -> None:
    """为单个会话密钥禁用 YOLO 绕过。"""
    if not session_key:
        return
    with _lock:
        _session_yolo.discard(session_key)


def clear_session(session_key: str) -> None:
    """移除给定会话的所有审批和 YOLO 状态。"""
    if not session_key:
        return
    with _lock:
        _session_approved.pop(session_key, None)
        _session_yolo.discard(session_key)
        _pending.pop(session_key, None)
        _gateway_queues.pop(session_key, None)


def is_session_yolo_enabled(session_key: str) -> bool:
    """当特定会话启用了 YOLO 绕过时返回 True。"""
    if not session_key:
        return False
    with _lock:
        return session_key in _session_yolo


def is_current_session_yolo_enabled() -> bool:
    """当活动审批会话启用了 YOLO 绕过时返回 True。"""
    return is_session_yolo_enabled(get_current_session_key(default=""))


def is_approved(session_key: str, pattern_key: str) -> bool:
    """检查模式是否已被批准（会话范围或永久）。

    同时接受当前规范密钥和旧版基于正则表达式的密钥，
    确保现有 command_allowlist 条目在密钥迁移后继续有效。
    """
    aliases = _approval_key_aliases(pattern_key)
    with _lock:
        if any(alias in _permanent_approved for alias in aliases):
            return True
        session_approvals = _session_approved.get(session_key, set())
        return any(alias in session_approvals for alias in aliases)


def approve_permanent(pattern_key: str):
    """将模式添加到永久允许列表。"""
    with _lock:
        _permanent_approved.add(pattern_key)


def load_permanent(patterns: set):
    """从配置中批量加载永久允许列表条目。"""
    with _lock:
        _permanent_approved.update(patterns)



# =========================================================================
# 永久允许列表的配置持久化
# =========================================================================

def load_permanent_allowlist() -> set:
    """从配置中加载永久允许的命令模式。

    同时同步到审批模块，使 is_approved() 对
    之前会话中通过 'always' 添加的模式生效。
    """
    try:
        from hermes_cli.config import load_config
        config = load_config()
        patterns = set(config.get("command_allowlist", []) or [])
        if patterns:
            load_permanent(patterns)
        return patterns
    except Exception as e:
        logger.warning("Failed to load permanent allowlist: %s", e)
        return set()


def save_permanent_allowlist(patterns: set):
    """将永久允许的命令模式保存到配置。"""
    try:
        from hermes_cli.config import load_config, save_config
        config = load_config()
        config["command_allowlist"] = list(patterns)
        save_config(config)
    except Exception as e:
        logger.warning("Could not save allowlist: %s", e)


# =========================================================================
# 审批提示 + 编排
# =========================================================================

def prompt_dangerous_approval(command: str, description: str,
                              timeout_seconds: int | None = None,
                              allow_permanent: bool = True,
                              approval_callback=None) -> str:
    """提示用户批准危险命令（仅限 CLI）。

    参数:
        allow_permanent: 为 False 时隐藏 [a]lways 选项（当存在
            tirith 告警时使用，因为宽泛的永久允许不适合
            内容级别的安全发现）。
        approval_callback: CLI 注册的可选回调，用于
            prompt_toolkit 集成。签名:
            (command, description, *, allow_permanent=True) -> str。

    返回: 'once'、'session'、'always' 或 'deny'
    """
    if timeout_seconds is None:
        timeout_seconds = _get_approval_timeout()

    if approval_callback is not None:
        try:
            return approval_callback(command, description,
                                     allow_permanent=allow_permanent)
        except Exception as e:
            logger.error("Approval callback failed: %s", e, exc_info=True)
            return "deny"

    os.environ["HERMES_SPINNER_PAUSE"] = "1"
    try:
        while True:
            print()
            print(f"  ⚠️  DANGEROUS COMMAND: {description}")
            print(f"      {command}")
            print()
            if allow_permanent:
                print("      [o]nce  |  [s]ession  |  [a]lways  |  [d]eny")
            else:
                print("      [o]nce  |  [s]ession  |  [d]eny")
            print()
            sys.stdout.flush()

            result = {"choice": ""}

            def get_input():
                try:
                    prompt = "      Choice [o/s/a/D]: " if allow_permanent else "      Choice [o/s/D]: "
                    result["choice"] = input(prompt).strip().lower()
                except (EOFError, OSError):
                    result["choice"] = ""

            thread = threading.Thread(target=get_input, daemon=True)
            thread.start()
            thread.join(timeout=timeout_seconds)

            if thread.is_alive():
                print("\n      ⏱ Timeout - denying command")
                return "deny"

            choice = result["choice"]
            if choice in ('o', 'once'):
                print("      ✓ Allowed once")
                return "once"
            elif choice in ('s', 'session'):
                print("      ✓ Allowed for this session")
                return "session"
            elif choice in ('a', 'always'):
                if not allow_permanent:
                    print("      ✓ Allowed for this session")
                    return "session"
                print("      ✓ Added to permanent allowlist")
                return "always"
            else:
                print("      ✗ Denied")
                return "deny"

    except (EOFError, KeyboardInterrupt):
        print("\n      ✗ Cancelled")
        return "deny"
    finally:
        if "HERMES_SPINNER_PAUSE" in os.environ:
            del os.environ["HERMES_SPINNER_PAUSE"]
        print()
        sys.stdout.flush()


def _normalize_approval_mode(mode) -> str:
    """标准化从 YAML/配置加载的审批模式值。

    YAML 1.1 将 `off` 等裸词当作布尔值处理，因此像
    `approvals:\n  mode: off` 这样的配置条目会被解析为 False
    （除非加引号）。将其视为预期的字符串模式，
    而不是回退到手动审批。
    """
    if isinstance(mode, bool):
        return "off" if mode is False else "manual"
    if isinstance(mode, str):
        normalized = mode.strip().lower()
        return normalized or "manual"
    return "manual"


def _get_approval_config() -> dict:
    """读取 approvals 配置块。返回包含 'mode'、'timeout' 等的字典。"""
    try:
        from hermes_cli.config import load_config
        config = load_config()
        return config.get("approvals", {}) or {}
    except Exception as e:
        logger.warning("Failed to load approval config: %s", e)
        return {}


def _get_approval_mode() -> str:
    """从配置读取审批模式。返回 'manual'、'smart' 或 'off'。"""
    mode = _get_approval_config().get("mode", "manual")
    return _normalize_approval_mode(mode)


def _get_approval_timeout() -> int:
    """从配置读取审批超时时间。默认 60 秒。"""
    try:
        return int(_get_approval_config().get("timeout", 60))
    except (ValueError, TypeError):
        return 60


def _smart_approve(command: str, description: str) -> str:
    """使用辅助 LLM 评估风险并决定是否批准。

    如果 LLM 判定命令安全则返回 'approve'，
    如果确实危险则返回 'deny'，如果不确定则返回 'escalate'。

    灵感来自 OpenAI Codex 的智能审批守护子代理
    (openai/codex#13860)。
    """
    try:
        from agent.auxiliary_client import get_text_auxiliary_client, auxiliary_max_tokens_param

        client, model = get_text_auxiliary_client(task="approval")
        if not client or not model:
            logger.debug("Smart approvals: no aux client available, escalating")
            return "escalate"

        prompt = f"""You are a security reviewer for an AI coding agent. A terminal command was flagged by pattern matching as potentially dangerous.

Command: {command}
Flagged reason: {description}

Assess the ACTUAL risk of this command. Many flagged commands are false positives — for example, `python -c "print('hello')"` is flagged as "script execution via -c flag" but is completely harmless.

Rules:
- APPROVE if the command is clearly safe (benign script execution, safe file operations, development tools, package installs, git operations, etc.)
- DENY if the command could genuinely damage the system (recursive delete of important paths, overwriting system files, fork bombs, wiping disks, dropping databases, etc.)
- ESCALATE if you're uncertain

Respond with exactly one word: APPROVE, DENY, or ESCALATE"""

        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            **auxiliary_max_tokens_param(16),
            temperature=0,
        )

        answer = (response.choices[0].message.content or "").strip().upper()

        if "APPROVE" in answer:
            return "approve"
        elif "DENY" in answer:
            return "deny"
        else:
            return "escalate"

    except Exception as e:
        logger.debug("Smart approvals: LLM call failed (%s), escalating", e)
        return "escalate"


def check_dangerous_command(command: str, env_type: str,
                            approval_callback=None) -> dict:
    """检查命令是否危险并处理审批流程。

    这是 terminal_tool 在执行任何命令前调用的主要入口点。
    它编排检测、会话检查和提示流程。

    参数:
        command: 要检查的 shell 命令。
        env_type: 终端后端类型（'local'、'ssh'、'docker' 等）。
        approval_callback: 可选的 CLI 回调，用于交互式提示。

    返回:
        {"approved": True/False, "message": str or None, ...}
    """
    # 容器化环境跳过审批
    if env_type in ("docker", "singularity", "modal", "daytona"):
        return {"approved": True, "message": None}

    # --yolo: 绕过所有审批提示。网关 /yolo 是会话范围的；
    # CLI --yolo 通过环境变量保持进程范围，用于本地使用。
    if os.getenv("HERMES_YOLO_MODE") or is_current_session_yolo_enabled():
        return {"approved": True, "message": None}

    is_dangerous, pattern_key, description = detect_dangerous_command(command)
    if not is_dangerous:
        return {"approved": True, "message": None}

    session_key = get_current_session_key()
    if is_approved(session_key, pattern_key):
        return {"approved": True, "message": None}

    is_cli = os.getenv("HERMES_INTERACTIVE")
    is_gateway = os.getenv("HERMES_GATEWAY_SESSION")

    if not is_cli and not is_gateway:
        return {"approved": True, "message": None}

    if is_gateway or os.getenv("HERMES_EXEC_ASK"):
        submit_pending(session_key, {
            "command": command,
            "pattern_key": pattern_key,
            "description": description,
        })
        return {
            "approved": False,
            "pattern_key": pattern_key,
            "status": "approval_required",
            "command": command,
            "description": description,
            "message": (
                f"⚠️ This command is potentially dangerous ({description}). "
                f"Asking the user for approval.\n\n**Command:**\n```\n{command}\n```"
            ),
        }

    choice = prompt_dangerous_approval(command, description,
                                       approval_callback=approval_callback)

    if choice == "deny":
        return {
            "approved": False,
            "message": f"BLOCKED: User denied this potentially dangerous command (matched '{description}' pattern). Do NOT retry this command - the user has explicitly rejected it.",
            "pattern_key": pattern_key,
            "description": description,
        }

    if choice == "session":
        approve_session(session_key, pattern_key)
    elif choice == "always":
        approve_session(session_key, pattern_key)
        approve_permanent(pattern_key)
        save_permanent_allowlist(_permanent_approved)

    return {"approved": True, "message": None}


# =========================================================================
# 组合预执行守卫（tirith + 危险命令检测）
# =========================================================================

def _format_tirith_description(tirith_result: dict) -> str:
    """从 tirith 发现中构建人类可读的描述。

    包含每个发现的严重程度、标题和描述，以便用户
    做出知情的审批决定。
    """
    findings = tirith_result.get("findings") or []
    if not findings:
        summary = tirith_result.get("summary") or "security issue detected"
        return f"Security scan: {summary}"

    parts = []
    for f in findings:
        severity = f.get("severity", "")
        title = f.get("title", "")
        desc = f.get("description", "")
        if title and desc:
            parts.append(f"[{severity}] {title}: {desc}" if severity else f"{title}: {desc}")
        elif title:
            parts.append(f"[{severity}] {title}" if severity else title)
    if not parts:
        summary = tirith_result.get("summary") or "security issue detected"
        return f"Security scan: {summary}"

    return "Security scan — " + "; ".join(parts)


def check_all_command_guards(command: str, env_type: str,
                             approval_callback=None) -> dict:
    """运行所有预执行安全检查并返回单一审批决定。

    收集 tirith 和危险命令检测的发现，然后将它们作为
    单个组合审批请求呈现。这防止了网关 force=True 重放
    在只向用户展示了其中一项检查结果时绕过另一项检查。
    """
    # 容器化环境跳过两项检查
    if env_type in ("docker", "singularity", "modal", "daytona"):
        return {"approved": True, "message": None}

    # --yolo 或 approvals.mode=off: 绕过所有审批提示。
    # 网关 /yolo 是会话范围的；CLI --yolo 保持进程范围。
    approval_mode = _get_approval_mode()
    if os.getenv("HERMES_YOLO_MODE") or is_current_session_yolo_enabled() or approval_mode == "off":
        return {"approved": True, "message": None}

    is_cli = os.getenv("HERMES_INTERACTIVE")
    is_gateway = os.getenv("HERMES_GATEWAY_SESSION")
    is_ask = os.getenv("HERMES_EXEC_ASK")

    # 保留现有的非交互行为: 在 CLI/网关/ask 流程之外，
    # 我们不阻塞审批，也跳过外部守卫工作。
    if not is_cli and not is_gateway and not is_ask:
        return {"approved": True, "message": None}

    # --- 阶段 1: 收集两项检查的发现 ---

    # Tirith 检查 — 包装器保证对预期的失败不抛异常。
    # 仅捕获 ImportError（模块未安装）。
    tirith_result = {"action": "allow", "findings": [], "summary": ""}
    try:
        from tools.tirith_security import check_command_security
        tirith_result = check_command_security(command)
    except ImportError:
        pass  # tirith 模块未安装 — 允许

    # 危险命令检查（仅检测，不审批）
    is_dangerous, pattern_key, description = detect_dangerous_command(command)

    # --- 阶段 2: 决策 ---

    # 收集需要审批的告警
    warnings = []  # 列表: (pattern_key, description, is_tirith)

    session_key = get_current_session_key()

    # Tirith block/warn -> 可审批的告警，带有丰富的发现信息。
    # 之前，tirith "block" 是不可审批的硬阻止。
    # 现在 block 和 warn 都经过审批流程，用户可以
    # 查看解释并在理解风险后批准。
    if tirith_result["action"] in ("block", "warn"):
        findings = tirith_result.get("findings") or []
        rule_id = findings[0].get("rule_id", "unknown") if findings else "unknown"
        tirith_key = f"tirith:{rule_id}"
        tirith_desc = _format_tirith_description(tirith_result)
        if not is_approved(session_key, tirith_key):
            warnings.append((tirith_key, tirith_desc, True))

    if is_dangerous:
        if not is_approved(session_key, pattern_key):
            warnings.append((pattern_key, description, False))

    # 没有需要告警的内容
    if not warnings:
        return {"approved": True, "message": None}

    # --- 阶段 2.5: 智能审批（辅助 LLM 风险评估）---
    # 当 approvals.mode=smart 时，在提示用户之前先询问辅助 LLM。
    # 灵感来自 OpenAI Codex 的智能审批守护子代理
    # (openai/codex#13860)。
    if approval_mode == "smart":
        combined_desc_for_llm = "; ".join(desc for _, desc, _ in warnings)
        verdict = _smart_approve(command, combined_desc_for_llm)
        if verdict == "approve":
            # 自动批准并授予这些模式的会话级审批
            for key, _, _ in warnings:
                approve_session(session_key, key)
            logger.debug("Smart approval: auto-approved '%s' (%s)",
                         command[:60], combined_desc_for_llm)
            return {"approved": True, "message": None,
                    "smart_approved": True,
                    "description": combined_desc_for_llm}
        elif verdict == "deny":
            combined_desc_for_llm = "; ".join(desc for _, desc, _ in warnings)
            return {
                "approved": False,
                "message": f"BLOCKED by smart approval: {combined_desc_for_llm}. "
                           "The command was assessed as genuinely dangerous. Do NOT retry.",
                "smart_denied": True,
            }
        # verdict == "escalate" -> 降级到手动提示

    # --- 阶段 3: 审批 ---

    # 合并描述为单个审批提示
    combined_desc = "; ".join(desc for _, desc, _ in warnings)
    primary_key = warnings[0][0]
    all_keys = [key for key, _, _ in warnings]
    has_tirith = any(is_t for _, _, is_t in warnings)

    # 网关/异步审批 — 阻塞代理线程直到用户
    # 通过 /approve 或 /deny 响应，镜像 CLI 的同步
    # input() 流程。代理永远不会看到 "approval_required"；
    # 它要么获得命令输出（已批准）要么获得明确的 "BLOCKED" 消息。
    if is_gateway or is_ask:
        notify_cb = None
        with _lock:
            notify_cb = _gateway_notify_cbs.get(session_key)

        if notify_cb is not None:
            # --- 阻塞式网关审批（基于队列）---
            # 每次调用获得自己的 _ApprovalEntry，这样并行子代理
            # 和 execute_code 线程可以并发阻塞。
            approval_data = {
                "command": command,
                "pattern_key": primary_key,
                "pattern_keys": all_keys,
                "description": combined_desc,
            }
            entry = _ApprovalEntry(approval_data)
            with _lock:
                _gateway_queues.setdefault(session_key, []).append(entry)

            # 通知用户（桥接同步代理线程到异步网关）
            try:
                notify_cb(approval_data)
            except Exception as exc:
                logger.warning("Gateway approval notify failed: %s", exc)
                with _lock:
                    queue = _gateway_queues.get(session_key, [])
                    if entry in queue:
                        queue.remove(entry)
                    if not queue:
                        _gateway_queues.pop(session_key, None)
                return {
                    "approved": False,
                    "message": "BLOCKED: Failed to send approval request to user. Do NOT retry.",
                    "pattern_key": primary_key,
                    "description": combined_desc,
                }

            # 阻塞直到用户响应或超时（默认 5 分钟）。
            # 以短间隔轮询，这样我们可以每 ~10 秒触发一次活动心跳
            # 到代理的不活动跟踪器。否则，阻塞的 event.wait() 不会
            # 触碰活动状态，网关的不活动看门狗（agent.gateway_timeout，
            # 默认 1800 秒）会在用户还在响应审批提示时杀死代理。
            # 镜像 tools/environments/base.py 中 _wait_for_process() 的节奏。
            timeout = _get_approval_config().get("gateway_timeout", 300)
            try:
                timeout = int(timeout)
            except (ValueError, TypeError):
                timeout = 300

            try:
                from tools.environments.base import touch_activity_if_due
            except Exception:  # pragma: no cover
                touch_activity_if_due = None

            _now = time.monotonic()
            _deadline = _now + max(timeout, 0)
            _activity_state = {"last_touch": _now, "start": _now}
            resolved = False
            while True:
                _remaining = _deadline - time.monotonic()
                if _remaining <= 0:
                    break
                # 1 秒轮询间隔 — 当用户响应时 event 会立即被设置，
                # 因此间隔长度只控制心跳频率，不影响用户可见的响应速度。
                if entry.event.wait(timeout=min(1.0, _remaining)):
                    resolved = True
                    break
                if touch_activity_if_due is not None:
                    touch_activity_if_due(
                        _activity_state, "waiting for user approval"
                    )

            # 从队列中清理此条目
            with _lock:
                queue = _gateway_queues.get(session_key, [])
                if entry in queue:
                    queue.remove(entry)
                if not queue:
                    _gateway_queues.pop(session_key, None)

            choice = entry.result
            if not resolved or choice is None or choice == "deny":
                reason = "timed out" if not resolved else "denied by user"
                return {
                    "approved": False,
                    "message": f"BLOCKED: Command {reason}. Do NOT retry this command.",
                    "pattern_key": primary_key,
                    "description": combined_desc,
                }

            # 用户已批准 — 根据范围持久化（与 CLI 逻辑相同）
            for key, _, is_tirith in warnings:
                if choice == "session" or (choice == "always" and is_tirith):
                    approve_session(session_key, key)
                elif choice == "always":
                    approve_session(session_key, key)
                    approve_permanent(key)
                    save_permanent_allowlist(_permanent_approved)
                # choice == "once": 不持久化 — 命令仅此一次允许，
                # 与 CLI 的行为一致。

            return {"approved": True, "message": None,
                    "user_approved": True, "description": combined_desc}

        # 回退: 没有注册网关回调（如定时任务、批处理）。
        # 返回 approval_required 以保持向后兼容。
        submit_pending(session_key, {
            "command": command,
            "pattern_key": primary_key,
            "pattern_keys": all_keys,
            "description": combined_desc,
        })
        return {
            "approved": False,
            "pattern_key": primary_key,
            "status": "approval_required",
            "command": command,
            "description": combined_desc,
            "message": (
                f"⚠️ {combined_desc}. Asking the user for approval.\n\n**Command:**\n```\n{command}\n```"
            ),
        }

    # CLI 交互式: 单个组合提示
    # 当存在 tirith 告警时隐藏 [a]lways
    choice = prompt_dangerous_approval(command, combined_desc,
                                       allow_permanent=not has_tirith,
                                       approval_callback=approval_callback)

    if choice == "deny":
        return {
            "approved": False,
            "message": "BLOCKED: User denied. Do NOT retry.",
            "pattern_key": primary_key,
            "description": combined_desc,
        }

    # 为每个告警单独持久化审批
    for key, _, is_tirith in warnings:
        if choice == "session" or (choice == "always" and is_tirith):
            # tirith: 仅会话范围（不进行宽泛的永久允许列表）
            approve_session(session_key, key)
        elif choice == "always":
            # 危险模式: 永久允许
            approve_session(session_key, key)
            approve_permanent(key)
            save_permanent_allowlist(_permanent_approved)

    return {"approved": True, "message": None,
            "user_approved": True, "description": combined_desc}


# 模块导入时从配置加载永久允许列表
load_permanent_allowlist()
