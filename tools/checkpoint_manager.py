"""
检查点管理器 — 通过影子 git 仓库实现透明的文件系统快照。

在文件变更操作（write_file、patch）之前自动创建工作目录快照，
每个对话轮次触发一次。提供回滚到任何先前检查点的功能。

这不是一个工具 — LLM 永远看不到它。它是由 ``checkpoints``
配置标志或 ``--checkpoints`` CLI 标志控制的透明基础设施。

架构:
    ~/.hermes/checkpoints/{sha256(abs_dir)[:16]}/   — 影子 git 仓库
        HEAD, refs/, objects/                        — 标准 git 内部结构
        HERMES_WORKDIR                               — 原始目录路径
        info/exclude                                 — 默认排除规则

影子仓库使用 GIT_DIR + GIT_WORK_TREE，因此没有 git 状态
泄漏到用户的项目目录中。
"""

import hashlib
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from hermes_constants import get_hermes_home
from typing import Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

CHECKPOINT_BASE = get_hermes_home() / "checkpoints"

DEFAULT_EXCLUDES = [
    "node_modules/",
    "dist/",
    "build/",
    ".env",
    ".env.*",
    ".env.local",
    ".env.*.local",
    "__pycache__/",
    "*.pyc",
    "*.pyo",
    ".DS_Store",
    "*.log",
    ".cache/",
    ".next/",
    ".nuxt/",
    "coverage/",
    ".pytest_cache/",
    ".venv/",
    "venv/",
    ".git/",
]

# Git 子进程超时时间（秒）。
_GIT_TIMEOUT: int = max(10, min(60, int(os.getenv("HERMES_CHECKPOINT_TIMEOUT", "30"))))

# 最大快照文件数 — 跳过巨大目录以避免性能下降。
_MAX_FILES = 50_000

# 有效的 git 提交哈希模式: 4-40 个十六进制字符（短或完整 SHA-1/SHA-256）。
_COMMIT_HASH_RE = re.compile(r'^[0-9a-fA-F]{4,64}$')


# ---------------------------------------------------------------------------
# 输入验证辅助函数
# ---------------------------------------------------------------------------

def _validate_commit_hash(commit_hash: str) -> Optional[str]:
    """验证提交哈希以防止 git 参数注入。

    如果无效则返回错误字符串，有效则返回 None。
    以 '-' 开头的值会被解释为 git 标志
    （如 '--patch'、'-p'）而不是修订标识符。
    """
    if not commit_hash or not commit_hash.strip():
        return "Empty commit hash"
    if commit_hash.startswith("-"):
        return f"Invalid commit hash (must not start with '-'): {commit_hash!r}"
    if not _COMMIT_HASH_RE.match(commit_hash):
        return f"Invalid commit hash (expected 4-64 hex characters): {commit_hash!r}"
    return None


def _validate_file_path(file_path: str, working_dir: str) -> Optional[str]:
    """验证文件路径以防止路径遍历到工作目录之外。

    如果无效则返回错误字符串，有效则返回 None。
    """
    if not file_path or not file_path.strip():
        return "Empty file path"
    # 拒绝绝对路径 — 恢复目标必须相对于工作目录
    if os.path.isabs(file_path):
        return f"File path must be relative, got absolute path: {file_path!r}"
    # 解析并检查是否包含在工作目录内
    abs_workdir = _normalize_path(working_dir)
    resolved = (abs_workdir / file_path).resolve()
    try:
        resolved.relative_to(abs_workdir)
    except ValueError:
        return f"File path escapes the working directory via traversal: {file_path!r}"
    return None


# ---------------------------------------------------------------------------
# 影子仓库辅助函数
# ---------------------------------------------------------------------------

def _normalize_path(path_value: str) -> Path:
    """返回用于检查点操作的规范绝对路径。"""
    return Path(path_value).expanduser().resolve()


def _shadow_repo_path(working_dir: str) -> Path:
    """确定性的影子仓库路径: sha256(abs_path)[:16]。"""
    abs_path = str(_normalize_path(working_dir))
    dir_hash = hashlib.sha256(abs_path.encode()).hexdigest()[:16]
    return CHECKPOINT_BASE / dir_hash


def _git_env(shadow_repo: Path, working_dir: str) -> dict:
    """构建将 git 重定向到影子仓库的环境变量字典。

    影子仓库是 Hermes 的内部基础设施 — 它不能继承用户的
    全局或系统 git 配置。用户级设置如 ``commit.gpgsign = true``、
    签名钩子或凭证助手要么会中断后台快照，要么更糟，
    在每次写入文件时产生交互式提示（pinentry GUI 窗口）。

    隔离策略:
    * ``GIT_CONFIG_GLOBAL=<os.devnull>`` — 忽略 ``~/.gitconfig``（git 2.32+）。
    * ``GIT_CONFIG_SYSTEM=<os.devnull>`` — 忽略 ``/etc/gitconfig``（git 2.32+）。
    * ``GIT_CONFIG_NOSYSTEM=1`` — 用于旧版 git 的双重保险。

    影子仓库仍有自己的仓库级配置（user.email、user.name、
    commit.gpgsign=false），在 ``_init_shadow_repo`` 中设置。
    """
    normalized_working_dir = _normalize_path(working_dir)
    env = os.environ.copy()
    env["GIT_DIR"] = str(shadow_repo)
    env["GIT_WORK_TREE"] = str(normalized_working_dir)
    env.pop("GIT_INDEX_FILE", None)
    env.pop("GIT_NAMESPACE", None)
    env.pop("GIT_ALTERNATE_OBJECT_DIRECTORIES", None)
    # 将影子仓库与用户的全局/系统 git 配置隔离。
    # 防止 commit.gpgsign、钩子、别名、凭证助手等
    # 泄漏到后台快照中。使用 os.devnull 实现跨平台支持
    # （POSIX 上是 ``/dev/null``，Windows 上是 ``nul``）。
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_SYSTEM"] = os.devnull
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    return env


def _run_git(
    args: List[str],
    shadow_repo: Path,
    working_dir: str,
    timeout: int = _GIT_TIMEOUT,
    allowed_returncodes: Optional[Set[int]] = None,
) -> tuple:
    """对影子仓库运行 git 命令。返回 (ok, stdout, stderr)。

    ``allowed_returncodes`` 抑制已知/预期的非零退出码的错误日志，
    同时保留正常的 ``ok = (returncode == 0)`` 约定。
    示例: ``git diff --cached --quiet`` 在存在变更时返回 1。
    """
    normalized_working_dir = _normalize_path(working_dir)
    if not normalized_working_dir.exists():
        msg = f"working directory not found: {normalized_working_dir}"
        logger.error("Git command skipped: %s (%s)", " ".join(["git"] + list(args)), msg)
        return False, "", msg
    if not normalized_working_dir.is_dir():
        msg = f"working directory is not a directory: {normalized_working_dir}"
        logger.error("Git command skipped: %s (%s)", " ".join(["git"] + list(args)), msg)
        return False, "", msg

    env = _git_env(shadow_repo, str(normalized_working_dir))
    cmd = ["git"] + list(args)
    allowed_returncodes = allowed_returncodes or set()
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            cwd=str(normalized_working_dir),
        )
        ok = result.returncode == 0
        stdout = result.stdout.strip()
        stderr = result.stderr.strip()
        if not ok and result.returncode not in allowed_returncodes:
            logger.error(
                "Git command failed: %s (rc=%d) stderr=%s",
                " ".join(cmd), result.returncode, stderr,
            )
        return ok, stdout, stderr
    except subprocess.TimeoutExpired:
        msg = f"git timed out after {timeout}s: {' '.join(cmd)}"
        logger.error(msg, exc_info=True)
        return False, "", msg
    except FileNotFoundError as exc:
        missing_target = getattr(exc, "filename", None)
        if missing_target == "git":
            logger.error("Git executable not found: %s", " ".join(cmd), exc_info=True)
            return False, "", "git not found"
        msg = f"working directory not found: {normalized_working_dir}"
        logger.error("Git command failed before execution: %s (%s)", " ".join(cmd), msg, exc_info=True)
        return False, "", msg
    except Exception as exc:
        logger.error("Unexpected git error running %s: %s", " ".join(cmd), exc, exc_info=True)
        return False, "", str(exc)


def _init_shadow_repo(shadow_repo: Path, working_dir: str) -> Optional[str]:
    """如需要则初始化影子仓库。返回错误字符串或 None。"""
    if (shadow_repo / "HEAD").exists():
        return None

    shadow_repo.mkdir(parents=True, exist_ok=True)

    ok, _, err = _run_git(["init"], shadow_repo, working_dir)
    if not ok:
        return f"Shadow repo init failed: {err}"

    _run_git(["config", "user.email", "hermes@local"], shadow_repo, working_dir)
    _run_git(["config", "user.name", "Hermes Checkpoint"], shadow_repo, working_dir)
    # 在影子仓库中显式禁用提交/标签签名。_git_env 已经
    # 与用户全局配置隔离，但将这些写入影子仓库自身的配置
    # 是双重保险 — 即使有人直接（不使用 GIT_CONFIG_* 环境变量）
    # 检查或对影子仓库运行 git 命令，也能保证正确。
    _run_git(["config", "commit.gpgsign", "false"], shadow_repo, working_dir)
    _run_git(["config", "tag.gpgSign", "false"], shadow_repo, working_dir)

    info_dir = shadow_repo / "info"
    info_dir.mkdir(exist_ok=True)
    (info_dir / "exclude").write_text(
        "\n".join(DEFAULT_EXCLUDES) + "\n", encoding="utf-8"
    )

    (shadow_repo / "HERMES_WORKDIR").write_text(
        str(_normalize_path(working_dir)) + "\n", encoding="utf-8"
    )

    logger.debug("Initialised checkpoint repo at %s for %s", shadow_repo, working_dir)
    return None


def _dir_file_count(path: str) -> int:
    """快速估算文件数量（超过 _MAX_FILES 时提前停止）。"""
    count = 0
    try:
        for _ in Path(path).rglob("*"):
            count += 1
            if count > _MAX_FILES:
                return count
    except (PermissionError, OSError):
        pass
    return count


# ---------------------------------------------------------------------------
# 检查点管理器
# ---------------------------------------------------------------------------

class CheckpointManager:
    """管理自动文件系统检查点。

    设计为由 AIAgent 拥有。在每个对话轮次开始时调用 ``new_turn()``，
    在任何文件变更工具调用之前调用 ``ensure_checkpoint(dir, reason)``。
    管理器会去重，每个目录每个轮次最多创建一个快照。

    参数
    ----------
    enabled : bool
        主开关（来自配置/CLI 标志）。
    max_snapshots : int
        每个目录最多保留此数量的检查点。
    """

    def __init__(self, enabled: bool = False, max_snapshots: int = 50):
        self.enabled = enabled
        self.max_snapshots = max_snapshots
        self._checkpointed_dirs: Set[str] = set()
        self._git_available: Optional[bool] = None  # 延迟探测

    # ------------------------------------------------------------------
    # 轮次生命周期
    # ------------------------------------------------------------------

    def new_turn(self) -> None:
        """重置每轮次去重。在每个代理迭代开始时调用。"""
        self._checkpointed_dirs.clear()

    # ------------------------------------------------------------------
    # 公共 API
    # ------------------------------------------------------------------

    def ensure_checkpoint(self, working_dir: str, reason: str = "auto") -> bool:
        """如果启用且本轮次尚未完成，则创建检查点。

        成功创建返回 True，否则返回 False。
        永不抛异常 — 所有错误静默记录日志。
        """
        if not self.enabled:
            return False

        # 延迟 git 探测
        if self._git_available is None:
            self._git_available = shutil.which("git") is not None
            if not self._git_available:
                logger.debug("Checkpoints disabled: git not found")
        if not self._git_available:
            return False

        abs_dir = str(_normalize_path(working_dir))

        # 跳过根目录、主目录和其他过于宽泛的目录
        if abs_dir in ("/", str(Path.home())):
            logger.debug("Checkpoint skipped: directory too broad (%s)", abs_dir)
            return False

        # 本轮次已创建过检查点？
        if abs_dir in self._checkpointed_dirs:
            return False

        self._checkpointed_dirs.add(abs_dir)

        try:
            return self._take(abs_dir, reason)
        except Exception as e:
            logger.debug("Checkpoint failed (non-fatal): %s", e)
            return False

    def list_checkpoints(self, working_dir: str) -> List[Dict]:
        """列出目录的可用检查点。

        返回字典列表，包含键: hash、short_hash、timestamp、reason、
        files_changed、insertions、deletions。最新的在前。
        """
        abs_dir = str(_normalize_path(working_dir))
        shadow = _shadow_repo_path(abs_dir)

        if not (shadow / "HEAD").exists():
            return []

        ok, stdout, _ = _run_git(
            ["log", "--format=%H|%h|%aI|%s", "-n", str(self.max_snapshots)],
            shadow, abs_dir,
        )

        if not ok or not stdout:
            return []

        results = []
        for line in stdout.splitlines():
            parts = line.split("|", 3)
            if len(parts) == 4:
                entry = {
                    "hash": parts[0],
                    "short_hash": parts[1],
                    "timestamp": parts[2],
                    "reason": parts[3],
                    "files_changed": 0,
                    "insertions": 0,
                    "deletions": 0,
                }
                # 获取此提交的差异统计
                stat_ok, stat_out, _ = _run_git(
                    ["diff", "--shortstat", f"{parts[0]}~1", parts[0]],
                    shadow, abs_dir,
                    allowed_returncodes={128, 129},  # 第一个提交没有父提交
                )
                if stat_ok and stat_out:
                    self._parse_shortstat(stat_out, entry)
                results.append(entry)
        return results

    @staticmethod
    def _parse_shortstat(stat_line: str, entry: Dict) -> None:
        """解析 git --shortstat 输出到条目字典中。"""
        import re
        m = re.search(r'(\d+) file', stat_line)
        if m:
            entry["files_changed"] = int(m.group(1))
        m = re.search(r'(\d+) insertion', stat_line)
        if m:
            entry["insertions"] = int(m.group(1))
        m = re.search(r'(\d+) deletion', stat_line)
        if m:
            entry["deletions"] = int(m.group(1))

    def diff(self, working_dir: str, commit_hash: str) -> Dict:
        """显示检查点与当前工作树之间的差异。

        返回包含 success、diff 文本和统计摘要的字典。
        """
        # 验证 commit_hash 以防止 git 参数注入
        hash_err = _validate_commit_hash(commit_hash)
        if hash_err:
            return {"success": False, "error": hash_err}

        abs_dir = str(_normalize_path(working_dir))
        shadow = _shadow_repo_path(abs_dir)

        if not (shadow / "HEAD").exists():
            return {"success": False, "error": "No checkpoints exist for this directory"}

        # 验证提交是否存在
        ok, _, err = _run_git(
            ["cat-file", "-t", commit_hash], shadow, abs_dir,
        )
        if not ok:
            return {"success": False, "error": f"Checkpoint '{commit_hash}' not found"}

        # 暂存当前状态以与检查点进行比较
        _run_git(["add", "-A"], shadow, abs_dir, timeout=_GIT_TIMEOUT * 2)

        # 获取统计摘要: 检查点 vs 当前工作树
        ok_stat, stat_out, _ = _run_git(
            ["diff", "--stat", commit_hash, "--cached"],
            shadow, abs_dir,
        )

        # 获取实际差异（限制大小以避免终端溢出）
        ok_diff, diff_out, _ = _run_git(
            ["diff", commit_hash, "--cached", "--no-color"],
            shadow, abs_dir,
        )

        # 取消暂存以避免污染影子仓库索引
        _run_git(["reset", "HEAD", "--quiet"], shadow, abs_dir)

        if not ok_stat and not ok_diff:
            return {"success": False, "error": "Could not generate diff"}

        return {
            "success": True,
            "stat": stat_out if ok_stat else "",
            "diff": diff_out if ok_diff else "",
        }

    def restore(self, working_dir: str, commit_hash: str, file_path: str = None) -> Dict:
        """将文件恢复到检查点状态。

        使用 ``git checkout <hash> -- .``（或特定文件）来恢复
        已跟踪文件而不移动 HEAD — 安全且可逆。

        参数
        ----------
        file_path : str, 可选
            如果提供，只恢复此文件而非整个目录。

        返回包含 success/error 信息的字典。
        """
        # 验证 commit_hash 以防止 git 参数注入
        hash_err = _validate_commit_hash(commit_hash)
        if hash_err:
            return {"success": False, "error": hash_err}

        abs_dir = str(_normalize_path(working_dir))

        # 验证 file_path 以防止路径遍历到工作目录之外
        if file_path:
            path_err = _validate_file_path(file_path, abs_dir)
            if path_err:
                return {"success": False, "error": path_err}

        shadow = _shadow_repo_path(abs_dir)

        if not (shadow / "HEAD").exists():
            return {"success": False, "error": "No checkpoints exist for this directory"}

        # 验证提交是否存在
        ok, _, err = _run_git(
            ["cat-file", "-t", commit_hash], shadow, abs_dir,
        )
        if not ok:
            return {"success": False, "error": f"Checkpoint '{commit_hash}' not found", "debug": err or None}

        # 恢复前先对当前状态创建检查点（这样可以撤销撤销操作）
        self._take(abs_dir, f"pre-rollback snapshot (restoring to {commit_hash[:8]})")

        # 恢复 — 整个目录或单个文件
        restore_target = file_path if file_path else "."
        ok, stdout, err = _run_git(
            ["checkout", commit_hash, "--", restore_target],
            shadow, abs_dir, timeout=_GIT_TIMEOUT * 2,
        )

        if not ok:
            return {"success": False, "error": f"Restore failed: {err}", "debug": err or None}

        # 获取恢复内容的信息
        ok2, reason_out, _ = _run_git(
            ["log", "--format=%s", "-1", commit_hash], shadow, abs_dir,
        )
        reason = reason_out if ok2 else "unknown"

        result = {
            "success": True,
            "restored_to": commit_hash[:8],
            "reason": reason,
            "directory": abs_dir,
        }
        if file_path:
            result["file"] = file_path
        return result

    def get_working_dir_for_path(self, file_path: str) -> str:
        """将文件路径解析到其用于检查点的工作目录。

        从文件的父目录向上查找合理的项目根目录
        （包含 .git、pyproject.toml、package.json 等的目录）。
        回退到文件的父目录。
        """
        path = _normalize_path(file_path)
        if path.is_dir():
            candidate = path
        else:
            candidate = path.parent

        # 向上查找项目根标记
        markers = {".git", "pyproject.toml", "package.json", "Cargo.toml",
                    "go.mod", "Makefile", "pom.xml", ".hg", "Gemfile"}
        check = candidate
        while check != check.parent:
            if any((check / m).exists() for m in markers):
                return str(check)
            check = check.parent

        # 未找到项目根 — 使用文件的父目录
        return str(candidate)

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _take(self, working_dir: str, reason: str) -> bool:
        """创建快照。成功返回 True。"""
        shadow = _shadow_repo_path(working_dir)

        # Init if needed
        err = _init_shadow_repo(shadow, working_dir)
        if err:
            logger.debug("Checkpoint init failed: %s", err)
            return False

        # 快速大小检查 — 不要尝试快照巨大的目录
        if _dir_file_count(working_dir) > _MAX_FILES:
            logger.debug("Checkpoint skipped: >%d files in %s", _MAX_FILES, working_dir)
            return False

        # 暂存所有内容
        ok, _, err = _run_git(
            ["add", "-A"], shadow, working_dir, timeout=_GIT_TIMEOUT * 2,
        )
        if not ok:
            logger.debug("Checkpoint git-add failed: %s", err)
            return False

        # 检查是否有内容需要提交
        ok_diff, diff_out, _ = _run_git(
            ["diff", "--cached", "--quiet"],
            shadow,
            working_dir,
            allowed_returncodes={1},
        )
        if ok_diff:
            # 没有变更需要提交
            logger.debug("Checkpoint skipped: no changes in %s", working_dir)
            return False

        # 提交。``--no-gpg-sign`` 内联覆盖了在 _init_shadow_repo 中
        # 添加 commit.gpgsign=false 配置之前创建的影子仓库 — 这样
        # 拥有现有检查点的用户永远不会遇到 GPG pinentry 弹窗。
        ok, _, err = _run_git(
            ["commit", "-m", reason, "--allow-empty-message", "--no-gpg-sign"],
            shadow, working_dir, timeout=_GIT_TIMEOUT * 2,
        )
        if not ok:
            logger.debug("Checkpoint commit failed: %s", err)
            return False

        logger.debug("Checkpoint taken in %s: %s", working_dir, reason)

        # 清理旧快照
        self._prune(shadow, working_dir)

        return True

    def _prune(self, shadow_repo: Path, working_dir: str) -> None:
        """通过孤立重置只保留最后 max_snapshots 个提交。"""
        ok, stdout, _ = _run_git(
            ["rev-list", "--count", "HEAD"], shadow_repo, working_dir,
        )
        if not ok:
            return

        try:
            count = int(stdout)
        except ValueError:
            return

        if count <= self.max_snapshots:
            return

        # 为简单起见，我们实际上并不修剪 — git 的打包机制
        # 能高效处理这个问题，而且对象都很小。日志列表
        # 已经被 max_snapshots 限制了。
        # 完整的修剪需要 rebase --onto 或 filter-branch，
        # 对于后台功能来说太脆弱了。我们只限制日志视图。
        logger.debug("Checkpoint repo has %d commits (limit %d)", count, self.max_snapshots)


def format_checkpoint_list(checkpoints: List[Dict], directory: str) -> str:
    """格式化检查点列表以向用户展示。"""
    if not checkpoints:
        return f"No checkpoints found for {directory}"

    lines = [f"📸 Checkpoints for {directory}:\n"]
    for i, cp in enumerate(checkpoints, 1):
        # 解析 ISO 时间戳为可读格式
        ts = cp["timestamp"]
        if "T" in ts:
            ts = ts.split("T")[1].split("+")[0].split("-")[0][:5]  # HH:MM
            date = cp["timestamp"].split("T")[0]
            ts = f"{date} {ts}"

        # 构建变更摘要
        files = cp.get("files_changed", 0)
        ins = cp.get("insertions", 0)
        dele = cp.get("deletions", 0)
        if files:
            stat = f"  ({files} file{'s' if files != 1 else ''}, +{ins}/-{dele})"
        else:
            stat = ""

        lines.append(f"  {i}. {cp['short_hash']}  {ts}  {cp['reason']}{stat}")

    lines.append("\n  /rollback <N>             restore to checkpoint N")
    lines.append("  /rollback diff <N>        preview changes since checkpoint N")
    lines.append("  /rollback <N> <file>      restore a single file from checkpoint N")
    return "\n".join(lines)
