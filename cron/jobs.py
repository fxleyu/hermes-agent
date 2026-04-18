"""
定时任务存储与管理。

任务存储在 ~/.hermes/cron/jobs.json
输出保存到 ~/.hermes/cron/output/{job_id}/{timestamp}.md
"""

import copy
import json
import logging
import tempfile
import os
import re
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from hermes_constants import get_hermes_home
from typing import Optional, Dict, List, Any

logger = logging.getLogger(__name__)

from hermes_time import now as _hermes_now

try:
    from croniter import croniter
    HAS_CRONITER = True
except ImportError:
    HAS_CRONITER = False

# =============================================================================
# 配置
# =============================================================================

HERMES_DIR = get_hermes_home().resolve()
CRON_DIR = HERMES_DIR / "cron"
JOBS_FILE = CRON_DIR / "jobs.json"
OUTPUT_DIR = CRON_DIR / "output"
ONESHOT_GRACE_SECONDS = 120  # 一次性任务的宽限时间（秒）


def _normalize_skill_list(skill: Optional[str] = None, skills: Optional[Any] = None) -> List[str]:
    """将遗留的单技能和多技能输入规范化为唯一有序列表。"""
    if skills is None:
        raw_items = [skill] if skill else []
    elif isinstance(skills, str):
        raw_items = [skills]
    else:
        raw_items = list(skills)

    normalized: List[str] = []
    for item in raw_items:
        text = str(item or "").strip()
        if text and text not in normalized:
            normalized.append(text)
    return normalized


def _apply_skill_fields(job: Dict[str, Any]) -> Dict[str, Any]:
    """返回一个规范化 `skills` 和遗留 `skill` 字段已对齐的任务字典。"""
    normalized = dict(job)
    skills = _normalize_skill_list(normalized.get("skill"), normalized.get("skills"))
    normalized["skills"] = skills
    normalized["skill"] = skills[0] if skills else None
    return normalized


def _secure_dir(path: Path):
    """将目录设置为仅所有者可访问（0700）。在 Windows 上无操作。"""
    try:
        os.chmod(path, 0o700)
    except (OSError, NotImplementedError):
        pass  # Windows 或其他不支持 chmod 的平台


def _secure_file(path: Path):
    """将文件设置为仅所有者可读写（0600）。在 Windows 上无操作。"""
    try:
        if path.exists():
            os.chmod(path, 0o600)
    except (OSError, NotImplementedError):
        pass


def ensure_dirs():
    """确保定时任务目录存在并具有安全权限。"""
    CRON_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    _secure_dir(CRON_DIR)
    _secure_dir(OUTPUT_DIR)


# =============================================================================
# 调度解析
# =============================================================================

def parse_duration(s: str) -> int:
    """
    将时间段字符串解析为分钟数。

    示例：
        "30m" -> 30
        "2h" -> 120
        "1d" -> 1440
    """
    s = s.strip().lower()
    match = re.match(r'^(\d+)\s*(m|min|mins|minute|minutes|h|hr|hrs|hour|hours|d|day|days)$', s)
    if not match:
        raise ValueError(f"Invalid duration: '{s}'. Use format like '30m', '2h', or '1d'")
    
    value = int(match.group(1))
    unit = match.group(2)[0]  # 首字符：m（分钟）、h（小时）或 d（天）
    
    multipliers = {'m': 1, 'h': 60, 'd': 1440}
    return value * multipliers[unit]


def parse_schedule(schedule: str) -> Dict[str, Any]:
    """
    将调度字符串解析为结构化格式。

    返回字典包含：
        - kind: "once" | "interval" | "cron"
        - 对于 "once"："run_at"（ISO 时间戳）
        - 对于 "interval"："minutes"（整数）
        - 对于 "cron"："expr"（cron 表达式）

    示例：
        "30m"              -> 30 分钟后执行一次
        "2h"               -> 2 小时后执行一次
        "every 30m"        -> 每 30 分钟重复执行
        "every 2h"         -> 每 2 小时重复执行
        "0 9 * * *"        -> cron 表达式
        "2026-02-03T14:00" -> 在指定时间执行一次
    """
    schedule = schedule.strip()
    original = schedule
    schedule_lower = schedule.lower()
    
    # "every X" 模式 -> 周期性间隔
    if schedule_lower.startswith("every "):
        duration_str = schedule[6:].strip()
        minutes = parse_duration(duration_str)
        return {
            "kind": "interval",
            "minutes": minutes,
            "display": f"every {minutes}m"
        }
    
    # 检查是否为 cron 表达式（5 或 6 个空格分隔的字段）
    # cron 字段：分钟 小时 日 月 星期 [年]
    parts = schedule.split()
    if len(parts) >= 5 and all(
        re.match(r'^[\d\*\-,/]+$', p) for p in parts[:5]
    ):
        if not HAS_CRONITER:
            raise ValueError("Cron expressions require 'croniter' package. Install with: pip install croniter")
        # 验证 cron 表达式
        try:
            croniter(schedule)
        except Exception as e:
            raise ValueError(f"Invalid cron expression '{schedule}': {e}")
        return {
            "kind": "cron",
            "expr": schedule,
            "display": schedule
        }
    
    # ISO 时间戳（包含 T 或看起来像日期）
    if 'T' in schedule or re.match(r'^\d{4}-\d{2}-\d{2}', schedule):
        try:
            # 解析并验证
            dt = datetime.fromisoformat(schedule.replace('Z', '+00:00'))
            # 在解析时将无时区的时间戳转为带时区感知的，这样存储的值
            # 不会依赖于检查时系统时区是否匹配。
            if dt.tzinfo is None:
                dt = dt.astimezone()  # 解释为本地时区
            return {
                "kind": "once",
                "run_at": dt.isoformat(),
                "display": f"once at {dt.strftime('%Y-%m-%d %H:%M')}"
            }
        except ValueError as e:
            raise ValueError(f"Invalid timestamp '{schedule}': {e}")
    
    # 类似 "30m"、"2h"、"1d" 的时间段 -> 从现在开始的一次性任务
    try:
        minutes = parse_duration(schedule)
        run_at = _hermes_now() + timedelta(minutes=minutes)
        return {
            "kind": "once",
            "run_at": run_at.isoformat(),
            "display": f"once in {original}"
        }
    except ValueError:
        pass
    
    raise ValueError(
        f"Invalid schedule '{original}'. Use:\n"
        f"  - Duration: '30m', '2h', '1d' (one-shot)\n"
        f"  - Interval: 'every 30m', 'every 2h' (recurring)\n"
        f"  - Cron: '0 9 * * *' (cron expression)\n"
        f"  - Timestamp: '2026-02-03T14:00:00' (one-shot at time)"
    )


def _ensure_aware(dt: datetime) -> datetime:
    """返回 Hermes 配置时区下的时区感知 datetime。

    向后兼容：
    - 旧版存储的时间戳可能是无时区的。
    - 无时区值被解释为*系统本地挂钟时间*（即创建时
      `datetime.now()` 使用的时区），然后转换为
      Hermes 配置的时区。

    这在时区变更时保持了遗留无时区时间戳的相对顺序，
    避免了错误的"未到期"判断结果。
    """
    target_tz = _hermes_now().tzinfo
    if dt.tzinfo is None:
        local_tz = datetime.now().astimezone().tzinfo
        return dt.replace(tzinfo=local_tz).astimezone(target_tz)
    return dt.astimezone(target_tz)


def _recoverable_oneshot_run_at(
    schedule: Dict[str, Any],
    now: datetime,
    *,
    last_run_at: Optional[str] = None,
) -> Optional[str]:
    """如果一次性任务仍有资格触发，返回其运行时间。

    一次性任务有一个小的宽限窗口，这样在请求的分钟之后几秒钟
    创建的任务仍然会在下一个 tick 时运行。一旦一次性任务
    已经运行过，就不再有资格触发。
    """
    if schedule.get("kind") != "once":
        return None
    if last_run_at:
        return None

    run_at = schedule.get("run_at")
    if not run_at:
        return None

    run_at_dt = _ensure_aware(datetime.fromisoformat(run_at))
    if run_at_dt >= now - timedelta(seconds=ONESHOT_GRACE_SECONDS):
        return run_at
    return None


def _compute_grace_seconds(schedule: dict) -> int:
    """计算任务可以延迟多久仍然追赶执行而不是快进跳过。

    使用调度周期的一半，限制在 120 秒和 2 小时之间。
    这确保每日任务在错过最多 2 小时后仍可追赶执行，
    而频繁任务（每 5-10 分钟）则快速快进。
    """
    MIN_GRACE = 120
    MAX_GRACE = 7200  # 2 小时

    kind = schedule.get("kind")

    if kind == "interval":
        period_seconds = schedule.get("minutes", 1) * 60
        grace = period_seconds // 2
        return max(MIN_GRACE, min(grace, MAX_GRACE))

    if kind == "cron" and HAS_CRONITER:
        try:
            now = _hermes_now()
            cron = croniter(schedule["expr"], now)
            first = cron.get_next(datetime)
            second = cron.get_next(datetime)
            period_seconds = int((second - first).total_seconds())
            grace = period_seconds // 2
            return max(MIN_GRACE, min(grace, MAX_GRACE))
        except Exception:
            pass

    return MIN_GRACE


def compute_next_run(schedule: Dict[str, Any], last_run_at: Optional[str] = None) -> Optional[str]:
    """
    计算调度的下次运行时间。

    返回 ISO 时间戳字符串，如果没有更多运行则返回 None。
    """
    now = _hermes_now()

    if schedule["kind"] == "once":
        return _recoverable_oneshot_run_at(schedule, now, last_run_at=last_run_at)

    elif schedule["kind"] == "interval":
        minutes = schedule["minutes"]
        if last_run_at:
            # 下次运行 = 上次运行时间 + 间隔
            last = _ensure_aware(datetime.fromisoformat(last_run_at))
            next_run = last + timedelta(minutes=minutes)
        else:
            # 首次运行 = 现在 + 间隔
            next_run = now + timedelta(minutes=minutes)
        return next_run.isoformat()

    elif schedule["kind"] == "cron":
        if not HAS_CRONITER:
            return None
        cron = croniter(schedule["expr"], now)
        next_run = cron.get_next(datetime)
        return next_run.isoformat()

    return None


# =============================================================================
# 任务 CRUD 操作
# =============================================================================

def load_jobs() -> List[Dict[str, Any]]:
    """从存储中加载所有任务。"""
    ensure_dirs()
    if not JOBS_FILE.exists():
        return []
    
    try:
        with open(JOBS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return data.get("jobs", [])
    except json.JSONDecodeError:
        # 使用 strict=False 重试，以处理字符串值中的裸控制字符
        try:
            with open(JOBS_FILE, 'r', encoding='utf-8') as f:
                data = json.loads(f.read(), strict=False)
                jobs = data.get("jobs", [])
                if jobs:
                    # 自动修复：以正确的转义重新写入
                    save_jobs(jobs)
                    logger.warning("Auto-repaired jobs.json (had invalid control characters)")
                return jobs
        except Exception as e:
            logger.error("Failed to auto-repair jobs.json: %s", e)
            raise RuntimeError(f"Cron database corrupted and unrepairable: {e}") from e
    except IOError as e:
        logger.error("IOError reading jobs.json: %s", e)
        raise RuntimeError(f"Failed to read cron database: {e}") from e


def save_jobs(jobs: List[Dict[str, Any]]):
    """将所有任务保存到存储中。"""
    ensure_dirs()
    fd, tmp_path = tempfile.mkstemp(dir=str(JOBS_FILE.parent), suffix='.tmp', prefix='.jobs_')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump({"jobs": jobs, "updated_at": _hermes_now().isoformat()}, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, JOBS_FILE)
        _secure_file(JOBS_FILE)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def create_job(
    prompt: str,
    schedule: str,
    name: Optional[str] = None,
    repeat: Optional[int] = None,
    deliver: Optional[str] = None,
    origin: Optional[Dict[str, Any]] = None,
    skill: Optional[str] = None,
    skills: Optional[List[str]] = None,
    model: Optional[str] = None,
    provider: Optional[str] = None,
    base_url: Optional[str] = None,
    script: Optional[str] = None,
) -> Dict[str, Any]:
    """
    创建一个新的定时任务。

    Args:
        prompt: 要运行的提示词（必须自包含，或在设置 skill 时为任务指令）
        schedule: 调度字符串（见 parse_schedule）
        name: 可选的友好名称
        repeat: 运行次数（None = 无限，1 = 一次）
        deliver: 输出投递目标（"origin"、"local"、"telegram" 等）
        origin: 任务创建来源信息（用于 "origin" 投递）
        skill: 可选的遗留单技能名称，在运行提示词前加载
        skills: 可选的有序技能列表，在运行提示词前加载
        model: 可选的每任务模型覆盖
        provider: 可选的每任务提供商覆盖
        base_url: 可选的每任务基础 URL 覆盖
        script: 可选的 Python 脚本路径，其标准输出会在每次运行时注入到提示词中。
                脚本在代理轮次之前运行，其输出作为上下文前置。
                适用于数据采集/变更检测。

    Returns:
        创建的任务字典
    """
    parsed_schedule = parse_schedule(schedule)

    # 规范化 repeat：将 0 或负值视为 None（无限）
    if repeat is not None and repeat <= 0:
        repeat = None

    # 对一次性调度自动设置 repeat=1（如果未指定）
    if parsed_schedule["kind"] == "once" and repeat is None:
        repeat = 1

    # 如果有 origin 信息，默认投递到 origin，否则投递到本地
    if deliver is None:
        deliver = "origin" if origin else "local"

    job_id = uuid.uuid4().hex[:12]
    now = _hermes_now().isoformat()

    normalized_skills = _normalize_skill_list(skill, skills)
    normalized_model = str(model).strip() if isinstance(model, str) else None
    normalized_provider = str(provider).strip() if isinstance(provider, str) else None
    normalized_base_url = str(base_url).strip().rstrip("/") if isinstance(base_url, str) else None
    normalized_model = normalized_model or None
    normalized_provider = normalized_provider or None
    normalized_base_url = normalized_base_url or None
    normalized_script = str(script).strip() if isinstance(script, str) else None
    normalized_script = normalized_script or None

    label_source = (prompt or (normalized_skills[0] if normalized_skills else None)) or "cron job"
    job = {
        "id": job_id,
        "name": name or label_source[:50].strip(),
        "prompt": prompt,
        "skills": normalized_skills,
        "skill": normalized_skills[0] if normalized_skills else None,
        "model": normalized_model,
        "provider": normalized_provider,
        "base_url": normalized_base_url,
        "script": normalized_script,
        "schedule": parsed_schedule,
        "schedule_display": parsed_schedule.get("display", schedule),
        "repeat": {
            "times": repeat,  # None = 无限
            "completed": 0
        },
        "enabled": True,
        "state": "scheduled",
        "paused_at": None,
        "paused_reason": None,
        "created_at": now,
        "next_run_at": compute_next_run(parsed_schedule),
        "last_run_at": None,
        "last_status": None,
        "last_error": None,
        "last_delivery_error": None,
        # 投递配置
        "deliver": deliver,
        "origin": origin,  # 记录任务创建位置，用于 "origin" 投递
    }

    jobs = load_jobs()
    jobs.append(job)
    save_jobs(jobs)

    return job


def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    """根据 ID 获取任务。"""
    jobs = load_jobs()
    for job in jobs:
        if job["id"] == job_id:
            return _apply_skill_fields(job)
    return None


def list_jobs(include_disabled: bool = False) -> List[Dict[str, Any]]:
    """列出所有任务，可选择是否包含已禁用的任务。"""
    jobs = [_apply_skill_fields(j) for j in load_jobs()]
    if not include_disabled:
        jobs = [j for j in jobs if j.get("enabled", True)]
    return jobs


def update_job(job_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """根据 ID 更新任务，在需要时刷新派生的调度字段。"""
    jobs = load_jobs()
    for i, job in enumerate(jobs):
        if job["id"] != job_id:
            continue

        updated = _apply_skill_fields({**job, **updates})
        schedule_changed = "schedule" in updates

        if "skills" in updates or "skill" in updates:
            normalized_skills = _normalize_skill_list(updated.get("skill"), updated.get("skills"))
            updated["skills"] = normalized_skills
            updated["skill"] = normalized_skills[0] if normalized_skills else None

        if schedule_changed:
            updated_schedule = updated["schedule"]
            # API 可能会传入原始字符串形式的 schedule（例如 "every 10m"）
            # 而非预解析的字典。按 create_job() 的方式规范化，
            # 以便下游代码可以安全地调用 .get()。
            if isinstance(updated_schedule, str):
                updated_schedule = parse_schedule(updated_schedule)
                updated["schedule"] = updated_schedule
            updated["schedule_display"] = updates.get(
                "schedule_display",
                updated_schedule.get("display", updated.get("schedule_display")),
            )
            if updated.get("state") != "paused":
                updated["next_run_at"] = compute_next_run(updated_schedule)

        if updated.get("enabled", True) and updated.get("state") != "paused" and not updated.get("next_run_at"):
            updated["next_run_at"] = compute_next_run(updated["schedule"])

        jobs[i] = updated
        save_jobs(jobs)
        return _apply_skill_fields(jobs[i])
    return None


def pause_job(job_id: str, reason: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """暂停任务而不删除。"""
    return update_job(
        job_id,
        {
            "enabled": False,
            "state": "paused",
            "paused_at": _hermes_now().isoformat(),
            "paused_reason": reason,
        },
    )


def resume_job(job_id: str) -> Optional[Dict[str, Any]]:
    """恢复暂停的任务，从当前时间计算下一个未来运行时间。"""
    job = get_job(job_id)
    if not job:
        return None

    next_run_at = compute_next_run(job["schedule"])
    return update_job(
        job_id,
        {
            "enabled": True,
            "state": "scheduled",
            "paused_at": None,
            "paused_reason": None,
            "next_run_at": next_run_at,
        },
    )


def trigger_job(job_id: str) -> Optional[Dict[str, Any]]:
    """安排任务在下一个调度器 tick 时运行。"""
    job = get_job(job_id)
    if not job:
        return None
    return update_job(
        job_id,
        {
            "enabled": True,
            "state": "scheduled",
            "paused_at": None,
            "paused_reason": None,
            "next_run_at": _hermes_now().isoformat(),
        },
    )


def remove_job(job_id: str) -> bool:
    """根据 ID 删除任务。"""
    jobs = load_jobs()
    original_len = len(jobs)
    jobs = [j for j in jobs if j["id"] != job_id]
    if len(jobs) < original_len:
        save_jobs(jobs)
        return True
    return False


def mark_job_run(job_id: str, success: bool, error: Optional[str] = None,
                 delivery_error: Optional[str] = None):
    """
    标记任务已运行。

    更新 last_run_at、last_status，增加已完成计数，
    计算 next_run_at，并在达到重复限制时自动删除。

    ``delivery_error`` 与代理错误分开跟踪 -- 一个任务
    可以成功（代理产生了输出）但投递失败（平台宕机）。
    """
    jobs = load_jobs()
    for i, job in enumerate(jobs):
        if job["id"] == job_id:
            now = _hermes_now().isoformat()
            job["last_run_at"] = now
            job["last_status"] = "ok" if success else "error"
            job["last_error"] = error if not success else None
            # 投递失败单独跟踪 -- 投递成功时清除
            job["last_delivery_error"] = delivery_error

            # 增加已完成计数
            if job.get("repeat"):
                job["repeat"]["completed"] = job["repeat"].get("completed", 0) + 1

                # 检查是否已达到重复限制
                times = job["repeat"].get("times")
                completed = job["repeat"]["completed"]
                if times is not None and times > 0 and completed >= times:
                    # 已达限制，删除任务
                    jobs.pop(i)
                    save_jobs(jobs)
                    return

            # 计算下次运行时间
            job["next_run_at"] = compute_next_run(job["schedule"], now)

            # 如果没有下次运行（一次性任务已完成），禁用该任务
            if job["next_run_at"] is None:
                job["enabled"] = False
                job["state"] = "completed"
            elif job.get("state") != "paused":
                job["state"] = "scheduled"

            save_jobs(jobs)
            return

    logger.warning("mark_job_run: job_id %s not found, skipping save", job_id)


def advance_next_run(job_id: str) -> bool:
    """在执行前预先将周期性任务的 next_run_at 推进到下一个时间点。

    在 run_job() 之前调用此函数，这样如果进程在执行期间崩溃，
    任务不会在下次网关重启时重新触发。这将调度器从
    "至少一次" 转换为 "至多一次" 语义 -- 对于周期性任务来说
    错过一次运行远好于在崩溃循环中触发数十次。

    一次性任务保持不变，以便它们在重启时仍可重试。

    如果 next_run_at 被推进则返回 True，否则返回 False。
    """
    jobs = load_jobs()
    for job in jobs:
        if job["id"] == job_id:
            kind = job.get("schedule", {}).get("kind")
            if kind not in ("cron", "interval"):
                return False
            now = _hermes_now().isoformat()
            new_next = compute_next_run(job["schedule"], now)
            if new_next and new_next != job.get("next_run_at"):
                job["next_run_at"] = new_next
                save_jobs(jobs)
                return True
            return False
    return False


def get_due_jobs() -> List[Dict[str, Any]]:
    """获取所有当前到期需要运行的任务。

    对于周期性任务（cron/interval），如果调度时间过期
    （超过一个周期已过，例如因为网关关闭），任务将被快进到
    下一个未来运行时间，而不是立即触发。这可以防止
    网关重启时一批错过的任务同时触发。
    """
    now = _hermes_now()
    raw_jobs = load_jobs()
    jobs = [_apply_skill_fields(j) for j in copy.deepcopy(raw_jobs)]
    due = []
    needs_save = False

    for job in jobs:
        if not job.get("enabled", True):
            continue

        next_run = job.get("next_run_at")
        if not next_run:
            recovered_next = _recoverable_oneshot_run_at(
                job.get("schedule", {}),
                now,
                last_run_at=job.get("last_run_at"),
            )
            if not recovered_next:
                continue

            job["next_run_at"] = recovered_next
            next_run = recovered_next
            logger.info(
                "Job '%s' had no next_run_at; recovering one-shot run at %s",
                job.get("name", job["id"]),
                recovered_next,
            )
            for rj in raw_jobs:
                if rj["id"] == job["id"]:
                    rj["next_run_at"] = recovered_next
                    needs_save = True
                    break

        next_run_dt = _ensure_aware(datetime.fromisoformat(next_run))
        if next_run_dt <= now:
            schedule = job.get("schedule", {})
            kind = schedule.get("kind")

            # 对于周期性任务，检查调度时间是否过期
            # （网关关闭并错过了窗口）。快进到
            # 下一个未来时间点，而不是执行过期的运行。
            grace = _compute_grace_seconds(schedule)
            if kind in ("cron", "interval") and (now - next_run_dt).total_seconds() > grace:
                # 任务已超过追赶宽限窗口 -- 这是一个过期的错过运行。
                # 宽限时间随调度周期缩放：每日=2h，每小时=30m，10分钟=5m。
                new_next = compute_next_run(schedule, now.isoformat())
                if new_next:
                    logger.info(
                        "Job '%s' missed its scheduled time (%s, grace=%ds). "
                        "Fast-forwarding to next run: %s",
                        job.get("name", job["id"]),
                        next_run,
                        grace,
                        new_next,
                    )
                    # 更新存储中的任务
                    for rj in raw_jobs:
                        if rj["id"] == job["id"]:
                            rj["next_run_at"] = new_next
                            needs_save = True
                            break
                    continue  # 跳过本次运行

            due.append(job)

    if needs_save:
        save_jobs(raw_jobs)

    return due


def save_job_output(job_id: str, output: str):
    """将任务输出保存到文件。"""
    ensure_dirs()
    job_output_dir = OUTPUT_DIR / job_id
    job_output_dir.mkdir(parents=True, exist_ok=True)
    _secure_dir(job_output_dir)
    
    timestamp = _hermes_now().strftime("%Y-%m-%d_%H-%M-%S")
    output_file = job_output_dir / f"{timestamp}.md"
    
    fd, tmp_path = tempfile.mkstemp(dir=str(job_output_dir), suffix='.tmp', prefix='.output_')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(output)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, output_file)
        _secure_file(output_file)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    
    return output_file
