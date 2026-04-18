"""用于在各个入口点中统一加载 Hermes .env 文件的辅助函数。"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


# 表示凭据值的环境变量名称后缀。这些是我们在加载时会清洗其值的唯一
# 环境变量——我们不能静默修改用户的任意环境变量，但凭据已知需要纯
# ASCII（因为它们会成为 HTTP 头的值）。
_CREDENTIAL_SUFFIXES = ("_API_KEY", "_TOKEN", "_SECRET", "_KEY")


def _sanitize_loaded_credentials() -> None:
    """从 os.environ 中的凭据环境变量中去除非 ASCII 字符。

    在 dotenv 加载后调用，这样代码库的其余部分永远不会看到
    含有非 ASCII 字符的 API 密钥。仅处理名称以已知凭据后缀
    （``_API_KEY``、``_TOKEN`` 等）结尾的环境变量。
    """
    # 遍历所有环境变量，找到凭据类变量并清洗非 ASCII 字符
    for key, value in list(os.environ.items()):
        if not any(key.endswith(suffix) for suffix in _CREDENTIAL_SUFFIXES):
            continue
        try:
            # 尝试将值编码为 ASCII，如果成功则说明无需处理
            value.encode("ascii")
        except UnicodeEncodeError:
            # 编码失败说明含有非 ASCII 字符，用 ignore 策略去除它们
            os.environ[key] = value.encode("ascii", errors="ignore").decode("ascii")


def _load_dotenv_with_fallback(path: Path, *, override: bool) -> None:
    """加载 .env 文件，优先使用 utf-8 编码，失败时回退到 latin-1。"""
    try:
        load_dotenv(dotenv_path=path, override=override, encoding="utf-8")
    except UnicodeDecodeError:
        # utf-8 解码失败时，回退到 latin-1 编码（该编码能处理所有单字节字符）
        load_dotenv(dotenv_path=path, override=override, encoding="latin-1")
    # 从刚加载的凭据环境变量中去除非 ASCII 字符。
    # API 密钥必须是纯 ASCII，因为它们作为 HTTP 头值发送（httpx 将头编码为 ASCII）。
    # 非 ASCII 字符通常来自从 PDF 或富文本编辑器中复制粘贴密钥时，
    # Unicode 外观相似的字形替换（例如 ʋ U+028B 替代 v）。
    _sanitize_loaded_credentials()


def _sanitize_env_file_if_needed(path: Path) -> None:
    """在 python-dotenv 读取之前预先清洗 .env 文件。

    python-dotenv 无法处理损坏的行——即多个 KEY=VALUE 对被拼接在
    同一行上（缺少换行符）。这会产生错误的值——例如 bot token 被
    重复 8 次（参见 #8908）。

    我们委托给 ``hermes_cli.config._sanitize_env_lines``，它已经
    知道所有有效的 Hermes 环境变量名称，能够正确地拆分拼接的行。
    """
    if not path.exists():
        return
    try:
        from hermes_cli.config import _sanitize_env_lines
    except ImportError:
        return  # 早期引导阶段——config 模块尚不可用

    read_kw = {"encoding": "utf-8", "errors": "replace"}
    try:
        # 读取原始文件内容
        with open(path, **read_kw) as f:
            original = f.readlines()
        # 调用清洗函数处理每一行
        sanitized = _sanitize_env_lines(original)
        if sanitized != original:
            # 内容有变化，使用临时文件 + 原子替换的方式安全地写回
            import tempfile
            fd, tmp = tempfile.mkstemp(
                dir=str(path.parent), suffix=".tmp", prefix=".env_"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.writelines(sanitized)
                    f.flush()
                    # 确保数据写入磁盘
                    os.fsync(f.fileno())
                # 原子替换原文件
                os.replace(tmp, path)
            except BaseException:
                # 写入失败时清理临时文件
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
    except Exception:
        pass  # 尽力而为——不阻塞网关启动


def load_hermes_dotenv(
    *,
    hermes_home: str | os.PathLike | None = None,
    project_env: str | os.PathLike | None = None,
) -> list[Path]:
    """加载 Hermes 环境文件，用户配置优先级最高。

    行为：
    - ``~/.hermes/.env`` 存在时会覆盖 shell 中已导出的过时值。
    - 项目 ``.env`` 作为开发回退，仅在用户 env 存在时填充缺失的值。
    - 如果用户 env 不存在，项目 ``.env`` 也会覆盖 shell 中的过时变量。
    """
    loaded: list[Path] = []

    # 确定 Hermes 主目录路径
    home_path = Path(hermes_home or os.getenv("HERMES_HOME", Path.home() / ".hermes"))
    user_env = home_path / ".env"
    project_env_path = Path(project_env) if project_env else None

    # 在 python-dotenv 解析之前修复损坏的 .env 文件（#8908）
    if user_env.exists():
        _sanitize_env_file_if_needed(user_env)

    # 加载用户级 .env（override=True 表示覆盖已有的环境变量）
    if user_env.exists():
        _load_dotenv_with_fallback(user_env, override=True)
        loaded.append(user_env)

    # 加载项目级 .env（仅当用户级 .env 不存在时才覆盖已有变量）
    if project_env_path and project_env_path.exists():
        _load_dotenv_with_fallback(project_env_path, override=not loaded)
        loaded.append(project_env_path)

    return loaded
