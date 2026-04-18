"""hermes-agent 的共享工具函数。"""

import json
import logging
import os
import stat
import tempfile
from pathlib import Path
from typing import Any, Union

import yaml

logger = logging.getLogger(__name__)

# 被视为"真"的字符串集合，用于将字符串转换为布尔值
TRUTHY_STRINGS = frozenset({"1", "true", "yes", "on"})


def is_truthy_value(value: Any, default: bool = False) -> bool:
    """使用项目共享的"真值"字符串集合，将类布尔值强制转换为 bool 类型。"""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    # 将字符串去除空白后转为小写，检查是否在"真值"集合中
    if isinstance(value, str):
        return value.strip().lower() in TRUTHY_STRINGS
    return bool(value)


def env_var_enabled(name: str, default: str = "") -> bool:
    """当环境变量被设置为"真值"时返回 True。"""
    return is_truthy_value(os.getenv(name, default), default=False)


def _preserve_file_mode(path: Path) -> "int | None":
    """如果 *path* 存在，则捕获其权限位；否则返回 ``None``。"""
    try:
        return stat.S_IMODE(path.stat().st_mode) if path.exists() else None
    except OSError:
        return None


def _restore_file_mode(path: Path, mode: "int | None") -> None:
    """在原子替换后重新应用 *mode* 到 *path*。

    ``tempfile.mkstemp`` 创建的文件权限为 0o600（仅所有者可访问）。
    ``os.replace`` 将临时文件替换到目标位置后，目标文件会继承这些
    限制性权限，这可能导致依赖更宽松权限的 Docker / NAS 卷挂载出错。
    在 ``os.replace`` 之后立即调用此函数可恢复原始权限。
    """
    if mode is None:
        return
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def atomic_json_write(
    path: Union[str, Path],
    data: Any,
    *,
    indent: int = 2,
    **dump_kwargs: Any,
) -> None:
    """以原子方式将 JSON 数据写入文件。

    使用临时文件 + fsync + os.replace 确保目标文件不会处于
    部分写入的状态。如果进程在写入过程中崩溃，文件的先前版本将保持完整。

    参数:
        path: 目标文件路径（将被创建或覆盖）。
        data: 可 JSON 序列化的数据。
        indent: JSON 缩进（默认 2）。
        **dump_kwargs: 传递给 json.dump() 的额外关键字参数，
            例如 default=str 用于非原生类型。
    """
    path = Path(path)
    # 确保父目录存在
    path.parent.mkdir(parents=True, exist_ok=True)

    # 保存原始文件权限，以便写入后恢复
    original_mode = _preserve_file_mode(path)

    # 在同一目录下创建临时文件，确保原子替换在同一文件系统上完成
    fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=f".{path.stem}_",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(
                data,
                f,
                indent=indent,
                ensure_ascii=False,
                **dump_kwargs,
            )
            # 刷新缓冲区并同步到磁盘，确保数据持久化
            f.flush()
            os.fsync(f.fileno())
        # 原子替换：将临时文件移动到目标路径
        os.replace(tmp_path, path)
        # 恢复原始文件权限
        _restore_file_mode(path, original_mode)
    except BaseException:
        # 特意捕获 BaseException，以便在重新抛出原始信号之前，
        # 对 KeyboardInterrupt/SystemExit 也能清理临时文件。
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def atomic_yaml_write(
    path: Union[str, Path],
    data: Any,
    *,
    default_flow_style: bool = False,
    sort_keys: bool = False,
    extra_content: str | None = None,
) -> None:
    """以原子方式将 YAML 数据写入文件。

    使用临时文件 + fsync + os.replace 确保目标文件不会处于
    部分写入的状态。如果进程在写入过程中崩溃，文件的先前版本将保持完整。

    参数:
        path: 目标文件路径（将被创建或覆盖）。
        data: 可 YAML 序列化的数据。
        default_flow_style: YAML 流样式（默认 False）。
        sort_keys: 是否对字典键排序（默认 False）。
        extra_content: 可选字符串，追加到 YAML 输出之后
            （例如，供用户参考的注释掉的配置段落）。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    original_mode = _preserve_file_mode(path)

    fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=f".{path.stem}_",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.dump(data, f, default_flow_style=default_flow_style, sort_keys=sort_keys)
            if extra_content:
                f.write(extra_content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
        _restore_file_mode(path, original_mode)
    except BaseException:
        # 与 atomic_json_write 保持一致：在重新抛出进程级中断之前也必须清理临时文件。
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ─── JSON 辅助函数 ────────────────────────────────────────────────────────────


def safe_json_loads(text: str, default: Any = None) -> Any:
    """解析 JSON，任何解析错误时返回 *default*。

    替代了在 display.py、anthropic_adapter.py、auxiliary_client.py 等多个文件中
    重复出现的 ``try: json.loads(x) except (JSONDecodeError, TypeError)`` 模式。
    """
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError, ValueError):
        return default


# ─── 环境变量辅助函数 ─────────────────────────────────────────────────────────


def env_int(key: str, default: int = 0) -> int:
    """读取环境变量并转换为整数，带有回退默认值。"""
    raw = os.getenv(key, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except (ValueError, TypeError):
        return default


def env_bool(key: str, default: bool = False) -> bool:
    """读取环境变量并转换为布尔值。"""
    return is_truthy_value(os.getenv(key, ""), default=default)
