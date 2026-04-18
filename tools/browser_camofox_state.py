"""Hermes 管理的 Camofox 状态辅助工具。

提供基于配置文件作用域的身份标识和状态目录路径，用于 Camofox
持久化浏览器配置文件。当启用托管持久化时，Hermes 会发送一个
根据活动配置文件派生的确定性 userId，使 Camofox 能够在
重启后将其映射到相同的持久化浏览器配置文件目录。
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Dict, Optional

from hermes_constants import get_hermes_home

CAMOFOX_STATE_DIR_NAME = "browser_auth"
CAMOFOX_STATE_SUBDIR = "camofox"


def get_camofox_state_dir() -> Path:
    """返回基于配置文件作用域的 Camofox 持久化根目录。"""
    return get_hermes_home() / CAMOFOX_STATE_DIR_NAME / CAMOFOX_STATE_SUBDIR


def get_camofox_identity(task_id: Optional[str] = None) -> Dict[str, str]:
    """返回当前配置文件对应的稳定 Hermes 管理的 Camofox 身份标识。

    用户身份标识的作用域是配置文件级别的（相同的 Hermes 配置文件 = 相同的 userId）。
    会话密钥的作用域是逻辑浏览器任务级别的，因此在同一配置文件中
    新创建的标签页会复用相同的身份契约。
    """
    # 使用状态目录路径作为作用域根来生成确定性标识
    scope_root = str(get_camofox_state_dir())
    logical_scope = task_id or "default"
    # 基于作用域根生成用户级别的确定性摘要（UUID5 保证相同输入产生相同输出）
    user_digest = uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"camofox-user:{scope_root}",
    ).hex[:10]
    # 基于作用域根和任务 ID 生成会话级别的确定性摘要
    session_digest = uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"camofox-session:{scope_root}:{logical_scope}",
    ).hex[:16]
    return {
        "user_id": f"hermes_{user_digest}",
        "session_key": f"task_{session_digest}",
    }
