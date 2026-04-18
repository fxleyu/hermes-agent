"""
Hermes Agent 定时任务调度系统。

本模块提供定时任务执行功能，允许代理：
- 按计划运行自动化任务（cron 表达式、间隔、一次性）
- 自行调度提醒和后续任务
- 在隔离会话中执行任务（无先前上下文）

定时任务由网关守护进程自动执行：
    hermes gateway install    # 安装为用户服务
    sudo hermes gateway install --system  # Linux 服务器：开机启动的系统服务
    hermes gateway            # 或前台运行

网关每 60 秒触发一次调度器。文件锁可防止
多个进程重叠时的重复执行。
"""

from cron.jobs import (
    create_job,
    get_job,
    list_jobs,
    remove_job,
    update_job,
    pause_job,
    resume_job,
    trigger_job,
    JOBS_FILE,
)
from cron.scheduler import tick

__all__ = [
    "create_job",
    "get_job", 
    "list_jobs",
    "remove_job",
    "update_job",
    "pause_job",
    "resume_job",
    "trigger_job",
    "tick",
    "JOBS_FILE",
]
