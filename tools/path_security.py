"""工具实现的共享路径验证辅助函数。

提取了之前在 skill_manager_tool、skills_tool、skills_hub、
cronjob_tools 和 credential_files 中重复的
``resolve() + relative_to()`` 和 ``..`` 遍历检查模式。
"""

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def validate_within_dir(path: Path, root: Path) -> Optional[str]:
    """确保 *path* 解析到 *root* 目录内的位置。

    如果验证失败返回错误消息字符串，如果路径安全则返回 ``None``。
    使用 ``Path.resolve()`` 跟随符号链接并规范化 ``..`` 组件。

    用法::

        error = validate_within_dir(user_path, allowed_root)
        if error:
            return json.dumps({"error": error})
    """
    try:
        resolved = path.resolve()
        root_resolved = root.resolve()
        resolved.relative_to(root_resolved)
    except (ValueError, OSError) as exc:
        return f"Path escapes allowed directory: {exc}"
    return None


def has_traversal_component(path_str: str) -> bool:
    """如果 *path_str* 包含 ``..`` 遍历组件则返回 True。

    在进行完整路径解析之前的快速检查，用于发现明显的遍历尝试。
    """
    parts = Path(path_str).parts
    return ".." in parts
