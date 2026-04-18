"""
定时任务调度器 -- 执行到期的任务。

提供 tick() 函数，用于检查到期任务并运行它们。网关
每 60 秒从后台线程调用一次。

使用基于文件的锁（~/.hermes/cron/.tick.lock），确保
多个进程重叠时只有一个 tick 在运行。
"""

import asyncio
import concurrent.futures
import contextvars
import json
import logging
import os
import subprocess
import sys

# fcntl 仅在 Unix 上可用；在 Windows 上使用 msvcrt 进行文件锁定
try:
    import fcntl
except ImportError:
    fcntl = None
    try:
        import msvcrt
    except ImportError:
        msvcrt = None
from pathlib import Path
from typing import Optional

# 将父目录添加到路径以支持导入（在仓库级别导入之前）。
# 没有这一步，独立调用（例如 `hermes update` 重新加载模块后）
# 会因为 hermes_time 等模块而报 ModuleNotFoundError。
sys.path.insert(0, str(Path(__file__).parent.parent))

from hermes_constants import get_hermes_home
from hermes_cli.config import load_config
from hermes_time import now as _hermes_now

logger = logging.getLogger(__name__)

# 有效的投递平台 -- 用于验证用户提供的平台名称，
# 防止通过精心构造的名称枚举环境变量。
_KNOWN_DELIVERY_PLATFORMS = frozenset({
    "telegram", "discord", "slack", "whatsapp", "signal",
    "matrix", "mattermost", "homeassistant", "dingtalk", "feishu",
    "wecom", "wecom_callback", "weixin", "sms", "email", "webhook", "bluebubbles",
    "qqbot",
})

from cron.jobs import get_due_jobs, mark_job_run, save_job_output, advance_next_run

# 标记值：当定时任务代理没有新内容需要报告时，可以在响应开头
# 使用此标记来抑制投递。输出仍然会保存在本地用于审计。
SILENT_MARKER = "[SILENT]"

# 解析 Hermes 主目录（支持 HERMES_HOME 环境变量覆盖）
_hermes_home = get_hermes_home()

# 基于文件的锁，防止网关 + 守护进程 + systemd 定时器并发执行 tick
_LOCK_DIR = _hermes_home / "cron"
_LOCK_FILE = _LOCK_DIR / ".tick.lock"


def _resolve_origin(job: dict) -> Optional[dict]:
    """从任务中提取来源信息，保留所有额外的路由元数据。"""
    origin = job.get("origin")
    if not origin:
        return None
    platform = origin.get("platform")
    chat_id = origin.get("chat_id")
    if platform and chat_id:
        return origin
    return None


def _resolve_delivery_target(job: dict) -> Optional[dict]:
    """解析定时任务的具体自动投递目标（如果有的话）。"""
    deliver = job.get("deliver", "local")
    origin = _resolve_origin(job)

    if deliver == "local":
        return None

    if deliver == "origin":
        if origin:
            return {
                "platform": origin["platform"],
                "chat_id": str(origin["chat_id"]),
                "thread_id": origin.get("thread_id"),
            }
        # 来源信息缺失（例如通过 API/脚本创建的任务）-- 尝试
        # 使用各平台的主频道作为回退，而不是静默丢弃。
        for platform_name in ("matrix", "telegram", "discord", "slack", "bluebubbles"):
            chat_id = os.getenv(f"{platform_name.upper()}_HOME_CHANNEL", "")
            if chat_id:
                logger.info(
                    "Job '%s' has deliver=origin but no origin; falling back to %s home channel",
                    job.get("name", job.get("id", "?")),
                    platform_name,
                )
                return {
                    "platform": platform_name,
                    "chat_id": chat_id,
                    "thread_id": None,
                }
        return None

    if ":" in deliver:
        platform_name, rest = deliver.split(":", 1)
        platform_key = platform_name.lower()

        from tools.send_message_tool import _parse_target_ref

        parsed_chat_id, parsed_thread_id, is_explicit = _parse_target_ref(platform_key, rest)
        if is_explicit:
            chat_id, thread_id = parsed_chat_id, parsed_thread_id
        else:
            chat_id, thread_id = rest, None

        # 将人类友好的标签（如 "Alice (dm)"）解析为真实 ID。
        try:
            from gateway.channel_directory import resolve_channel_name
            resolved = resolve_channel_name(platform_key, chat_id)
            if resolved:
                parsed_chat_id, parsed_thread_id, resolved_is_explicit = _parse_target_ref(platform_key, resolved)
                if resolved_is_explicit:
                    chat_id, thread_id = parsed_chat_id, parsed_thread_id
                else:
                    chat_id = resolved
        except Exception:
            pass

        return {
            "platform": platform_name,
            "chat_id": chat_id,
            "thread_id": thread_id,
        }

    platform_name = deliver
    if origin and origin.get("platform") == platform_name:
        return {
            "platform": platform_name,
            "chat_id": str(origin["chat_id"]),
            "thread_id": origin.get("thread_id"),
        }

    if platform_name.lower() not in _KNOWN_DELIVERY_PLATFORMS:
        return None
    chat_id = os.getenv(f"{platform_name.upper()}_HOME_CHANNEL", "")
    if not chat_id:
        return None

    return {
        "platform": platform_name,
        "chat_id": chat_id,
        "thread_id": None,
    }


# 媒体文件扩展名集合 -- 需与 gateway/platforms/base.py:_process_message_background 保持同步
_AUDIO_EXTS = frozenset({'.ogg', '.opus', '.mp3', '.wav', '.m4a'})
_VIDEO_EXTS = frozenset({'.mp4', '.mov', '.avi', '.mkv', '.webm', '.3gp'})
_IMAGE_EXTS = frozenset({'.jpg', '.jpeg', '.png', '.webp', '.gif'})


def _send_media_via_adapter(adapter, chat_id: str, media_files: list, metadata: dict | None, loop, job: dict) -> None:
    """通过活跃的平台适配器将提取的媒体文件作为原生平台附件发送。

    根据文件扩展名将每个文件路由到相应的适配器方法（send_voice、send_image_file、
    send_video、send_document）-- 与 ``BasePlatformAdapter._process_message_background``
    中的路由逻辑保持一致。
    """
    from pathlib import Path

    for media_path, _is_voice in media_files:
        try:
            ext = Path(media_path).suffix.lower()
            if ext in _AUDIO_EXTS:
                coro = adapter.send_voice(chat_id=chat_id, audio_path=media_path, metadata=metadata)
            elif ext in _VIDEO_EXTS:
                coro = adapter.send_video(chat_id=chat_id, video_path=media_path, metadata=metadata)
            elif ext in _IMAGE_EXTS:
                coro = adapter.send_image_file(chat_id=chat_id, image_path=media_path, metadata=metadata)
            else:
                coro = adapter.send_document(chat_id=chat_id, file_path=media_path, metadata=metadata)

            future = asyncio.run_coroutine_threadsafe(coro, loop)
            result = future.result(timeout=30)
            if result and not getattr(result, "success", True):
                logger.warning(
                    "Job '%s': media send failed for %s: %s",
                    job.get("id", "?"), media_path, getattr(result, "error", "unknown"),
                )
        except Exception as e:
            logger.warning("Job '%s': failed to send media %s: %s", job.get("id", "?"), media_path, e)


def _deliver_result(job: dict, content: str, adapters=None, loop=None) -> Optional[str]:
    """
    将任务输出投递到配置的目标（来源聊天、指定平台等）。

    当提供 ``adapters`` 和 ``loop`` 时（网关正在运行），优先尝试
    使用活跃的适配器 -- 这支持端到端加密的房间（例如 Matrix），
    因为独立的 HTTP 路径无法加密。如果适配器路径失败或不可用，
    则回退到独立发送。

    成功时返回 None，失败时返回错误字符串。
    """
    target = _resolve_delivery_target(job)
    if not target:
        if job.get("deliver", "local") != "local":
            msg = f"no delivery target resolved for deliver={job.get('deliver', 'local')}"
            logger.warning("Job '%s': %s", job["id"], msg)
            return msg
        return None  # 仅本地投递的任务不需要投递 -- 不算失败

    platform_name = target["platform"]
    chat_id = target["chat_id"]
    thread_id = target.get("thread_id")

    # 诊断：记录 thread_id 用于主题感知投递调试
    origin = job.get("origin") or {}
    origin_thread = origin.get("thread_id")
    if origin_thread and not thread_id:
        logger.warning(
            "Job '%s': origin has thread_id=%s but delivery target lost it "
            "(deliver=%s, target=%s)",
            job["id"], origin_thread, job.get("deliver", "local"), target,
        )
    elif thread_id:
        logger.debug(
            "Job '%s': delivering to %s:%s thread_id=%s",
            job["id"], platform_name, chat_id, thread_id,
        )

    from tools.send_message_tool import _send_to_platform
    from gateway.config import load_gateway_config, Platform

    platform_map = {
        "telegram": Platform.TELEGRAM,
        "discord": Platform.DISCORD,
        "slack": Platform.SLACK,
        "whatsapp": Platform.WHATSAPP,
        "signal": Platform.SIGNAL,
        "matrix": Platform.MATRIX,
        "mattermost": Platform.MATTERMOST,
        "homeassistant": Platform.HOMEASSISTANT,
        "dingtalk": Platform.DINGTALK,
        "feishu": Platform.FEISHU,
        "wecom": Platform.WECOM,
        "wecom_callback": Platform.WECOM_CALLBACK,
        "weixin": Platform.WEIXIN,
        "email": Platform.EMAIL,
        "sms": Platform.SMS,
        "bluebubbles": Platform.BLUEBUBBLES,
        "qqbot": Platform.QQBOT,
    }
    platform = platform_map.get(platform_name.lower())
    if not platform:
        msg = f"unknown platform '{platform_name}'"
        logger.warning("Job '%s': %s", job["id"], msg)
        return msg

    try:
        config = load_gateway_config()
    except Exception as e:
        msg = f"failed to load gateway config: {e}"
        logger.error("Job '%s': %s", job["id"], msg)
        return msg

    pconfig = config.platforms.get(platform)
    if not pconfig or not pconfig.enabled:
        msg = f"platform '{platform_name}' not configured/enabled"
        logger.warning("Job '%s': %s", job["id"], msg)
        return msg

    # 可选地用头部/尾部包装内容，让用户知道这是定时任务投递。
    # 默认启用包装；在 config.yaml 中设置 cron.wrap_response: false 可获得干净输出。
    wrap_response = True
    try:
        user_cfg = load_config()
        wrap_response = user_cfg.get("cron", {}).get("wrap_response", True)
    except Exception:
        pass

    if wrap_response:
        task_name = job.get("name", job["id"])
        job_id = job.get("id", "")
        delivery_content = (
            f"Cronjob Response: {task_name}\n"
            f"(job_id: {job_id})\n"
            f"-------------\n\n"
            f"{content}\n\n"
            f"To stop or manage this job, send me a new message (e.g. \"stop reminder {task_name}\")."
        )
    else:
        delivery_content = content

    # 提取 MEDIA: 标签，以便将附件作为文件转发，而不是原始文本
    from gateway.platforms.base import BasePlatformAdapter
    media_files, cleaned_delivery_content = BasePlatformAdapter.extract_media(delivery_content)

    # 当网关运行时优先使用活跃的适配器 -- 这支持端到端加密的
    # 房间（例如 Matrix），因为独立的 HTTP 路径无法加密。
    runtime_adapter = (adapters or {}).get(platform)
    if runtime_adapter is not None and loop is not None and getattr(loop, "is_running", lambda: False)():
        send_metadata = {"thread_id": thread_id} if thread_id else None
        try:
            # 发送清理后的文本（已去除 MEDIA 标签） -- 不是原始内容
            text_to_send = cleaned_delivery_content.strip()
            adapter_ok = True
            if text_to_send:
                future = asyncio.run_coroutine_threadsafe(
                    runtime_adapter.send(chat_id, text_to_send, metadata=send_metadata),
                    loop,
                )
                send_result = future.result(timeout=60)
                if send_result and not getattr(send_result, "success", True):
                    err = getattr(send_result, "error", "unknown")
                    logger.warning(
                        "Job '%s': live adapter send to %s:%s failed (%s), falling back to standalone",
                        job["id"], platform_name, chat_id, err,
                    )
                    adapter_ok = False  # 回退到独立路径

            # 通过活跃适配器将提取的媒体文件作为原生附件发送
            if adapter_ok and media_files:
                _send_media_via_adapter(runtime_adapter, chat_id, media_files, send_metadata, loop, job)

            if adapter_ok:
                logger.info("Job '%s': delivered to %s:%s via live adapter", job["id"], platform_name, chat_id)
                return None
        except Exception as e:
            logger.warning(
                "Job '%s': live adapter delivery to %s:%s failed (%s), falling back to standalone",
                job["id"], platform_name, chat_id, e,
            )

    # 独立路径：在新的事件循环中运行异步发送（在任何线程中都安全）
    coro = _send_to_platform(platform, pconfig, chat_id, cleaned_delivery_content, thread_id=thread_id, media_files=media_files)
    try:
        result = asyncio.run(coro)
    except RuntimeError:
        # asyncio.run() 在等待协程之前检查是否有运行中的循环；
        # 当它抛出异常时，原始协程从未启动 -- 关闭它以
        # 防止 "coroutine was never awaited" 运行时警告，然后在
        # 没有运行中循环的新线程中重试。
        coro.close()
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, _send_to_platform(platform, pconfig, chat_id, cleaned_delivery_content, thread_id=thread_id, media_files=media_files))
            result = future.result(timeout=30)
    except Exception as e:
        msg = f"delivery to {platform_name}:{chat_id} failed: {e}"
        logger.error("Job '%s': %s", job["id"], msg)
        return msg

    if result and result.get("error"):
        msg = f"delivery error: {result['error']}"
        logger.error("Job '%s': %s", job["id"], msg)
        return msg

    logger.info("Job '%s': delivered to %s:%s", job["id"], platform_name, chat_id)
    return None


_DEFAULT_SCRIPT_TIMEOUT = 120  # 秒
# 向后兼容的模块级覆盖，用于测试和紧急猴子补丁。
_SCRIPT_TIMEOUT = _DEFAULT_SCRIPT_TIMEOUT


def _get_script_timeout() -> int:
    """从模块/环境变量/配置中解析定时任务预运行脚本超时时间，带安全默认值。"""
    if _SCRIPT_TIMEOUT != _DEFAULT_SCRIPT_TIMEOUT:
        try:
            timeout = int(float(_SCRIPT_TIMEOUT))
            if timeout > 0:
                return timeout
        except Exception:
            logger.warning("Invalid patched _SCRIPT_TIMEOUT=%r; using env/config/default", _SCRIPT_TIMEOUT)

    env_value = os.getenv("HERMES_CRON_SCRIPT_TIMEOUT", "").strip()
    if env_value:
        try:
            timeout = int(float(env_value))
            if timeout > 0:
                return timeout
        except Exception:
            logger.warning("Invalid HERMES_CRON_SCRIPT_TIMEOUT=%r; using config/default", env_value)

    try:
        cfg = load_config() or {}
        cron_cfg = cfg.get("cron", {}) if isinstance(cfg, dict) else {}
        configured = cron_cfg.get("script_timeout_seconds")
        if configured is not None:
            timeout = int(float(configured))
            if timeout > 0:
                return timeout
    except Exception as exc:
        logger.debug("Failed to load cron script timeout from config: %s", exc)

    return _DEFAULT_SCRIPT_TIMEOUT


def _run_job_script(script_path: str) -> tuple[bool, str]:
    """执行定时任务的数据采集脚本并捕获其输出。

    脚本必须位于 HERMES_HOME/scripts/ 目录内。相对路径和
    绝对路径都会被解析并验证是否在此目录内，以
    防止通过路径遍历或绝对路径注入执行任意脚本。

    Args:
        script_path: Python 脚本的路径。相对路径相对于
            HERMES_HOME/scripts/ 解析。绝对路径和 ~ 前缀路径
            也会被验证以确保它们在 scripts 目录内。

    Returns:
        (success, output) -- 失败时 *output* 包含错误消息，以便
        LLM 可以向用户报告问题。
    """
    from hermes_constants import get_hermes_home

    scripts_dir = get_hermes_home() / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    scripts_dir_resolved = scripts_dir.resolve()

    raw = Path(script_path).expanduser()
    if raw.is_absolute():
        path = raw.resolve()
    else:
        path = (scripts_dir / raw).resolve()

    # 防止路径遍历、绝对路径注入和符号链接逃逸
    # -- 脚本必须位于 HERMES_HOME/scripts/ 内。
    try:
        path.relative_to(scripts_dir_resolved)
    except ValueError:
        return False, (
            f"Blocked: script path resolves outside the scripts directory "
            f"({scripts_dir_resolved}): {script_path!r}"
        )

    if not path.exists():
        return False, f"Script not found: {path}"
    if not path.is_file():
        return False, f"Script path is not a file: {path}"

    script_timeout = _get_script_timeout()

    try:
        result = subprocess.run(
            [sys.executable, str(path)],
            capture_output=True,
            text=True,
            timeout=script_timeout,
            cwd=str(path.parent),
        )
        stdout = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()

        # 从 stdout 和 stderr 中脱敏敏感信息（在任何返回路径之前）。
        try:
            from agent.redact import redact_sensitive_text
            stdout = redact_sensitive_text(stdout)
            stderr = redact_sensitive_text(stderr)
        except Exception:
            pass

        if result.returncode != 0:
            parts = [f"Script exited with code {result.returncode}"]
            if stderr:
                parts.append(f"stderr:\n{stderr}")
            if stdout:
                parts.append(f"stdout:\n{stdout}")
            return False, "\n".join(parts)

        return True, stdout

    except subprocess.TimeoutExpired:
        return False, f"Script timed out after {script_timeout}s: {path}"
    except Exception as exc:
        return False, f"Script execution failed: {exc}"


def _build_job_prompt(job: dict) -> str:
    """构建定时任务的有效提示词，可选择先加载一个或多个技能。"""
    prompt = job.get("prompt", "")
    skills = job.get("skills")

    # 如果配置了数据采集脚本，运行它并将输出作为上下文注入。
    script_path = job.get("script")
    if script_path:
        success, script_output = _run_job_script(script_path)
        if success:
            if script_output:
                prompt = (
                    "## Script Output\n"
                    "The following data was collected by a pre-run script. "
                    "Use it as context for your analysis.\n\n"
                    f"```\n{script_output}\n```\n\n"
                    f"{prompt}"
                )
            else:
                prompt = (
                    "[Script ran successfully but produced no output.]\n\n"
                    f"{prompt}"
                )
        else:
            prompt = (
                "## Script Error\n"
                "The data-collection script failed. Report this to the user.\n\n"
                f"```\n{script_output}\n```\n\n"
                f"{prompt}"
            )

    # 始终前置定时任务执行指导，让代理知道投递机制，
    # 并在适当时可以抑制投递。
    cron_hint = (
        "[SYSTEM: You are running as a scheduled cron job. "
        "DELIVERY: Your final response will be automatically delivered "
        "to the user — do NOT use send_message or try to deliver "
        "the output yourself. Just produce your report/output as your "
        "final response and the system handles the rest. "
        "SILENT: If there is genuinely nothing new to report, respond "
        "with exactly \"[SILENT]\" (nothing else) to suppress delivery. "
        "Never combine [SILENT] with content — either report your "
        "findings normally, or say [SILENT] and nothing more.]\n\n"
    )
    prompt = cron_hint + prompt
    if skills is None:
        legacy = job.get("skill")
        skills = [legacy] if legacy else []

    skill_names = [str(name).strip() for name in skills if str(name).strip()]
    if not skill_names:
        return prompt

    from tools.skills_tool import skill_view

    parts = []
    skipped: list[str] = []
    for skill_name in skill_names:
        loaded = json.loads(skill_view(skill_name))
        if not loaded.get("success"):
            error = loaded.get("error") or f"Failed to load skill '{skill_name}'"
            logger.warning("Cron job '%s': skill not found, skipping — %s", job.get("name", job.get("id")), error)
            skipped.append(skill_name)
            continue

        content = str(loaded.get("content") or "").strip()
        if parts:
            parts.append("")
        parts.extend(
            [
                f'[SYSTEM: The user has invoked the "{skill_name}" skill, indicating they want you to follow its instructions. The full skill content is loaded below.]',
                "",
                content,
            ]
        )

    if skipped:
        notice = (
            f"[SYSTEM: The following skill(s) were listed for this job but could not be found "
            f"and were skipped: {', '.join(skipped)}. "
            f"Start your response with a brief notice so the user is aware, e.g.: "
            f"'⚠️ Skill(s) not found and skipped: {', '.join(skipped)}']"
        )
        parts.insert(0, notice)

    if prompt:
        parts.extend(["", f"The user has provided the following instruction alongside the skill invocation: {prompt}"])
    return "\n".join(parts)


def run_job(job: dict) -> tuple[bool, str, str, Optional[str]]:
    """
    执行单个定时任务。

    Returns:
        元组 (success, full_output_doc, final_response, error_message)
    """
    from run_agent import AIAgent
    
    # 初始化 SQLite 会话存储，以便定时任务消息被持久化
    # 并可通过 session_search 发现（与 gateway/run.py 相同的模式）。
    _session_db = None
    try:
        from hermes_state import SessionDB
        _session_db = SessionDB()
    except Exception as e:
        logger.debug("Job '%s': SQLite session store not available: %s", job.get("id", "?"), e)
    
    job_id = job["id"]
    job_name = job["name"]
    prompt = _build_job_prompt(job)
    origin = _resolve_origin(job)
    _cron_session_id = f"cron_{job_id}_{_hermes_now().strftime('%Y%m%d_%H%M%S')}"

    logger.info("Running job '%s' (ID: %s)", job_name, job_id)
    logger.info("Prompt: %s", prompt[:100])

    try:
        # 注入来源上下文，使代理的 send_message 工具知道聊天信息。
        # 必须在 try 块内，这样 finally 清理总会运行。
        if origin:
            os.environ["HERMES_SESSION_PLATFORM"] = origin["platform"]
            os.environ["HERMES_SESSION_CHAT_ID"] = str(origin["chat_id"])
            if origin.get("chat_name"):
                os.environ["HERMES_SESSION_CHAT_NAME"] = origin["chat_name"]
        # 每次运行时重新读取 .env 和 config.yaml，以便提供商/密钥
        # 更改无需重启网关即可生效。
        from dotenv import load_dotenv
        try:
            load_dotenv(str(_hermes_home / ".env"), override=True, encoding="utf-8")
        except UnicodeDecodeError:
            load_dotenv(str(_hermes_home / ".env"), override=True, encoding="latin-1")

        delivery_target = _resolve_delivery_target(job)
        if delivery_target:
            os.environ["HERMES_CRON_AUTO_DELIVER_PLATFORM"] = delivery_target["platform"]
            os.environ["HERMES_CRON_AUTO_DELIVER_CHAT_ID"] = str(delivery_target["chat_id"])
            if delivery_target.get("thread_id") is not None:
                os.environ["HERMES_CRON_AUTO_DELIVER_THREAD_ID"] = str(delivery_target["thread_id"])

        model = job.get("model") or os.getenv("HERMES_MODEL") or ""

        # 从 config.yaml 加载模型、推理、预填充、工具集、提供商路由配置
        _cfg = {}
        try:
            import yaml
            _cfg_path = str(_hermes_home / "config.yaml")
            if os.path.exists(_cfg_path):
                with open(_cfg_path) as _f:
                    _cfg = yaml.safe_load(_f) or {}
                _model_cfg = _cfg.get("model", {})
                if not job.get("model"):
                    if isinstance(_model_cfg, str):
                        model = _model_cfg
                    elif isinstance(_model_cfg, dict):
                        model = _model_cfg.get("default", model)
        except Exception as e:
            logger.warning("Job '%s': failed to load config.yaml, using defaults: %s", job_id, e)

        # 如果配置了 IPv4 偏好，则应用。
        try:
            from hermes_constants import apply_ipv4_preference
            _net_cfg = _cfg.get("network", {})
            if isinstance(_net_cfg, dict) and _net_cfg.get("force_ipv4"):
                apply_ipv4_preference(force=True)
        except Exception:
            pass

        # 从 config.yaml 读取推理配置
        from hermes_constants import parse_reasoning_effort
        effort = str(_cfg.get("agent", {}).get("reasoning_effort", "")).strip()
        reasoning_config = parse_reasoning_effort(effort)

        # 从环境变量或 config.yaml 读取预填充消息
        prefill_messages = None
        prefill_file = os.getenv("HERMES_PREFILL_MESSAGES_FILE", "") or _cfg.get("prefill_messages_file", "")
        if prefill_file:
            import json as _json
            pfpath = Path(prefill_file).expanduser()
            if not pfpath.is_absolute():
                pfpath = _hermes_home / pfpath
            if pfpath.exists():
                try:
                    with open(pfpath, "r", encoding="utf-8") as _pf:
                        prefill_messages = _json.load(_pf)
                    if not isinstance(prefill_messages, list):
                        prefill_messages = None
                except Exception as e:
                    logger.warning("Job '%s': failed to parse prefill messages file '%s': %s", job_id, pfpath, e)
                    prefill_messages = None

        # 最大迭代次数
        max_iterations = _cfg.get("agent", {}).get("max_turns") or _cfg.get("max_turns") or 90

        # 提供商路由
        pr = _cfg.get("provider_routing", {})
        smart_routing = _cfg.get("smart_model_routing", {}) or {}

        from hermes_cli.runtime_provider import (
            resolve_runtime_provider,
            format_runtime_provider_error,
        )
        try:
            runtime_kwargs = {
                "requested": job.get("provider") or os.getenv("HERMES_INFERENCE_PROVIDER"),
            }
            if job.get("base_url"):
                runtime_kwargs["explicit_base_url"] = job.get("base_url")
            runtime = resolve_runtime_provider(**runtime_kwargs)
        except Exception as exc:
            message = format_runtime_provider_error(exc)
            raise RuntimeError(message) from exc

        from agent.smart_model_routing import resolve_turn_route
        turn_route = resolve_turn_route(
            prompt,
            smart_routing,
            {
                "model": model,
                "api_key": runtime.get("api_key"),
                "base_url": runtime.get("base_url"),
                "provider": runtime.get("provider"),
                "api_mode": runtime.get("api_mode"),
                "command": runtime.get("command"),
                "args": list(runtime.get("args") or []),
            },
        )

        fallback_model = _cfg.get("fallback_providers") or _cfg.get("fallback_model") or None
        credential_pool = None
        runtime_provider = str(turn_route["runtime"].get("provider") or "").strip().lower()
        if runtime_provider:
            try:
                from agent.credential_pool import load_pool
                pool = load_pool(runtime_provider)
                if pool.has_credentials():
                    credential_pool = pool
                    logger.info(
                        "Job '%s': loaded credential pool for provider %s with %d entries",
                        job_id,
                        runtime_provider,
                        len(pool.entries()),
                    )
            except Exception as e:
                logger.debug("Job '%s': failed to load credential pool for %s: %s", job_id, runtime_provider, e)

        agent = AIAgent(
            model=turn_route["model"],
            api_key=turn_route["runtime"].get("api_key"),
            base_url=turn_route["runtime"].get("base_url"),
            provider=turn_route["runtime"].get("provider"),
            api_mode=turn_route["runtime"].get("api_mode"),
            acp_command=turn_route["runtime"].get("command"),
            acp_args=turn_route["runtime"].get("args"),
            max_iterations=max_iterations,
            reasoning_config=reasoning_config,
            prefill_messages=prefill_messages,
            fallback_model=fallback_model,
            credential_pool=credential_pool,
            providers_allowed=pr.get("only"),
            providers_ignored=pr.get("ignore"),
            providers_order=pr.get("order"),
            provider_sort=pr.get("sort"),
            disabled_toolsets=["cronjob", "messaging", "clarify"],
            quiet_mode=True,
            skip_context_files=True,  # 不从调度器工作目录注入 SOUL.md/AGENTS.md
            skip_memory=True,  # 定时任务系统提示会破坏用户表征
            platform="cron",
            session_id=_cron_session_id,
            session_db=_session_db,
        )
        
        # 使用基于*不活动*的超时运行代理：如果任务在积极调用工具/
        # 接收流式令牌，可以运行数小时，但如果 API 调用挂起或工具
        # 在配置的时间内无活动，则会被捕获并终止。
        # 默认 600 秒（10 分钟不活动）；通过 HERMES_CRON_TIMEOUT 环境变量覆盖。
        # 0 = 无限制。
        #
        # 使用代理的内置活动追踪器（每次工具调用、API 调用和
        # 流式增量时由 _touch_activity() 更新）。
        _cron_timeout = float(os.getenv("HERMES_CRON_TIMEOUT", 600))
        _cron_inactivity_limit = _cron_timeout if _cron_timeout > 0 else None
        _POLL_INTERVAL = 5.0
        _cron_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        # 保留调度器作用域的 ContextVar 状态（例如技能声明的
        # 环境变量透传注册），当定时任务跳转到用于不活动超时
        # 监控的工作线程时。
        _cron_context = contextvars.copy_context()
        _cron_future = _cron_pool.submit(_cron_context.run, agent.run_conversation, prompt)
        _inactivity_timeout = False
        try:
            if _cron_inactivity_limit is None:
                # 无限制 -- 只需等待结果。
                result = _cron_future.result()
            else:
                result = None
                while True:
                    done, _ = concurrent.futures.wait(
                        {_cron_future}, timeout=_POLL_INTERVAL,
                    )
                    if done:
                        result = _cron_future.result()
                        break
                    # 代理仍在运行 -- 检查不活动状态。
                    _idle_secs = 0.0
                    if hasattr(agent, "get_activity_summary"):
                        try:
                            _act = agent.get_activity_summary()
                            _idle_secs = _act.get("seconds_since_activity", 0.0)
                        except Exception:
                            pass
                    if _idle_secs >= _cron_inactivity_limit:
                        _inactivity_timeout = True
                        break
        except Exception:
            _cron_pool.shutdown(wait=False, cancel_futures=True)
            raise
        finally:
            _cron_pool.shutdown(wait=False, cancel_futures=True)

        if _inactivity_timeout:
            # 从代理的活动追踪器构建诊断摘要。
            _activity = {}
            if hasattr(agent, "get_activity_summary"):
                try:
                    _activity = agent.get_activity_summary()
                except Exception:
                    pass
            _last_desc = _activity.get("last_activity_desc", "unknown")
            _secs_ago = _activity.get("seconds_since_activity", 0)
            _cur_tool = _activity.get("current_tool")
            _iter_n = _activity.get("api_call_count", 0)
            _iter_max = _activity.get("max_iterations", 0)

            logger.error(
                "Job '%s' idle for %.0fs (inactivity limit %.0fs) "
                "| last_activity=%s | iteration=%s/%s | tool=%s",
                job_name, _secs_ago, _cron_inactivity_limit,
                _last_desc, _iter_n, _iter_max,
                _cur_tool or "none",
            )
            if hasattr(agent, "interrupt"):
                agent.interrupt("Cron job timed out (inactivity)")
            raise TimeoutError(
                f"Cron job '{job_name}' idle for "
                f"{int(_secs_ago)}s (limit {int(_cron_inactivity_limit)}s) "
                f"— last activity: {_last_desc}"
            )

        final_response = result.get("final_response", "") or ""
        # 去除上游可能在空补全时注入的泄露占位符文本。
        if final_response.strip() == "(No response generated)":
            final_response = ""
        # 使用单独的变量用于日志显示；保持 final_response 干净
        # 以用于投递逻辑（空响应 = 不投递）。
        logged_response = final_response if final_response else "(No response generated)"
        
        output = f"""# Cron Job: {job_name}

**Job ID:** {job_id}
**Run Time:** {_hermes_now().strftime('%Y-%m-%d %H:%M:%S')}
**Schedule:** {job.get('schedule_display', 'N/A')}

## Prompt

{prompt}

## Response

{logged_response}
"""
        
        logger.info("Job '%s' completed successfully", job_name)
        return True, output, final_response, None
        
    except Exception as e:
        error_msg = f"{type(e).__name__}: {str(e)}"
        logger.exception("Job '%s' failed: %s", job_name, error_msg)
        
        output = f"""# Cron Job: {job_name} (FAILED)

**Job ID:** {job_id}
**Run Time:** {_hermes_now().strftime('%Y-%m-%d %H:%M:%S')}
**Schedule:** {job.get('schedule_display', 'N/A')}

## Prompt

{prompt}

## Error

```
{error_msg}
```
"""
        return False, output, "", error_msg

    finally:
        # 清理注入的环境变量，防止泄露到其他任务
        for key in (
            "HERMES_SESSION_PLATFORM",
            "HERMES_SESSION_CHAT_ID",
            "HERMES_SESSION_CHAT_NAME",
            "HERMES_CRON_AUTO_DELIVER_PLATFORM",
            "HERMES_CRON_AUTO_DELIVER_CHAT_ID",
            "HERMES_CRON_AUTO_DELIVER_THREAD_ID",
        ):
            os.environ.pop(key, None)
        if _session_db:
            try:
                _session_db.end_session(_cron_session_id, "cron_complete")
            except (Exception, KeyboardInterrupt) as e:
                logger.debug("Job '%s': failed to end session: %s", job_id, e)
            try:
                _session_db.close()
            except (Exception, KeyboardInterrupt) as e:
                logger.debug("Job '%s': failed to close SQLite session store: %s", job_id, e)


def tick(verbose: bool = True, adapters=None, loop=None) -> int:
    """
    检查并运行所有到期任务。

    使用文件锁，即使网关的进程内定时器和独立守护进程
    或手动 tick 重叠，也只运行一个 tick。

    Args:
        verbose: 是否打印状态消息
        adapters: 可选的 Platform -> 活跃适配器映射字典（来自网关）
        loop: 可选的 asyncio 事件循环（来自网关），用于活跃适配器发送

    Returns:
        执行的任务数（如果另一个 tick 正在运行则为 0）
    """
    _LOCK_DIR.mkdir(parents=True, exist_ok=True)

    # 跨平台文件锁：Unix 使用 fcntl，Windows 使用 msvcrt
    lock_fd = None
    try:
        lock_fd = open(_LOCK_FILE, "w")
        if fcntl:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        elif msvcrt:
            msvcrt.locking(lock_fd.fileno(), msvcrt.LK_NBLCK, 1)
    except (OSError, IOError):
        logger.debug("Tick skipped — another instance holds the lock")
        if lock_fd is not None:
            lock_fd.close()
        return 0

    try:
        due_jobs = get_due_jobs()

        if verbose and not due_jobs:
            logger.info("%s - No jobs due", _hermes_now().strftime('%H:%M:%S'))
            return 0

        if verbose:
            logger.info("%s - %s job(s) due", _hermes_now().strftime('%H:%M:%S'), len(due_jobs))

        executed = 0
        for job in due_jobs:
            try:
                # 对于周期性任务（cron/interval），在执行前将 next_run_at
                # 推进到下一个未来时间点。这样如果进程在运行中崩溃，
                # 任务不会在重启时重新触发。
                # 一次性任务保持不变，以便它们在重启时可以重试。
                advance_next_run(job["id"])

                success, output, final_response, error = run_job(job)

                output_file = save_job_output(job["id"], output)
                if verbose:
                    logger.info("Output saved to: %s", output_file)

                # 将最终响应投递到来源/目标聊天。
                # 如果代理响应了 [SILENT]，跳过投递（但输出已在上面保存）。
                # 失败的任务始终投递。
                deliver_content = final_response if success else f"⚠️ Cron job '{job.get('name', job['id'])}' failed:\n{error}"
                should_deliver = bool(deliver_content)
                if should_deliver and success and SILENT_MARKER in deliver_content.strip().upper():
                    logger.info("Job '%s': agent returned %s — skipping delivery", job["id"], SILENT_MARKER)
                    should_deliver = False

                delivery_error = None
                if should_deliver:
                    try:
                        delivery_error = _deliver_result(job, deliver_content, adapters=adapters, loop=loop)
                    except Exception as de:
                        delivery_error = str(de)
                        logger.error("Delivery failed for job %s: %s", job["id"], de)

                # 将空的 final_response 视为软失败，这样 last_status
                # 不会是 "ok" -- 代理运行了但没有产生有用的结果。
                # （issue #8585）
                if success and not final_response:
                    success = False
                    error = "Agent completed but produced empty response (model error, timeout, or misconfiguration)"

                mark_job_run(job["id"], success, error, delivery_error=delivery_error)
                executed += 1

            except Exception as e:
                logger.error("Error processing job %s: %s", job['id'], e)
                mark_job_run(job["id"], False, str(e))

        return executed
    finally:
        if fcntl:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        elif msvcrt:
            try:
                msvcrt.locking(lock_fd.fileno(), msvcrt.LK_UNLCK, 1)
            except (OSError, IOError):
                pass
        lock_fd.close()


if __name__ == "__main__":
    tick(verbose=True)
