"""
hermes CLI 的备份和导入命令。

`hermes backup` 创建整个 ~/.hermes/ 目录的 zip 归档
（排除 hermes-agent 代码库和临时文件）。

`hermes import` 从备份 zip 恢复，覆盖到当前
HERMES_HOME 根目录。
"""

import json
import logging
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from hermes_constants import get_default_hermes_root, get_hermes_home, display_hermes_home

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 排除规则
# ---------------------------------------------------------------------------

# 需要完全跳过的目录名称（匹配路径中的每个组成部分）
_EXCLUDED_DIRS = {
    "hermes-agent",     # 代码仓库 —— 重新克隆即可
    "__pycache__",      # 字节码缓存 —— 导入后会重新生成
    ".git",             # 嵌套的 git 目录（配置文件不应有这些，但以防万一）
    "node_modules",     # JS 依赖（以防 website/ 不小心包含进来）
}

# 需要跳过的文件后缀
_EXCLUDED_SUFFIXES = (
    ".pyc",
    ".pyo",
)

# 需要跳过的文件名（运行时状态文件，在其他机器上无意义）
_EXCLUDED_NAMES = {
    "gateway.pid",
    "cron.pid",
}


def _should_exclude(rel_path: Path) -> bool:
    """判断 *rel_path*（相对于 hermes 根目录）是否应被跳过。"""
    parts = rel_path.parts

    # 路径中的任何部分匹配排除目录名
    for part in parts:
        if part in _EXCLUDED_DIRS:
            return True

    name = rel_path.name

    if name in _EXCLUDED_NAMES:
        return True

    if name.endswith(_EXCLUDED_SUFFIXES):
        return True

    return False


# ---------------------------------------------------------------------------
# SQLite 安全拷贝
# ---------------------------------------------------------------------------

def _safe_copy_db(src: Path, dst: Path) -> bool:
    """使用 backup() API 安全拷贝 SQLite 数据库。

    处理 WAL 模式 —— 即使数据库正在被写入，也能产生一致的快照。
    失败时回退到原始文件拷贝。
    """
    try:
        conn = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
        backup_conn = sqlite3.connect(str(dst))
        conn.backup(backup_conn)
        backup_conn.close()
        conn.close()
        return True
    except Exception as exc:
        logger.warning("SQLite safe copy failed for %s: %s", src, exc)
        try:
            # 回退到直接文件拷贝
            shutil.copy2(src, dst)
            return True
        except Exception as exc2:
            logger.error("Raw copy also failed for %s: %s", src, exc2)
            return False


# ---------------------------------------------------------------------------
# 备份
# ---------------------------------------------------------------------------

def _format_size(nbytes: int) -> str:
    """将字节数格式化为人类可读的文件大小。"""
    for unit in ("B", "KB", "MB", "GB"):
        if nbytes < 1024:
            return f"{nbytes:.1f} {unit}" if unit != "B" else f"{nbytes} {unit}"
        nbytes /= 1024
    return f"{nbytes:.1f} TB"


def run_backup(args) -> None:
    """创建 Hermes 主目录的 zip 备份。"""
    hermes_root = get_default_hermes_root()

    if not hermes_root.is_dir():
        print(f"Error: Hermes home directory not found at {hermes_root}")
        sys.exit(1)

    # 确定输出路径
    if args.output:
        out_path = Path(args.output).expanduser().resolve()
        # 如果用户给的是目录，则将 zip 文件放入其中
        if out_path.is_dir():
            stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
            out_path = out_path / f"hermes-backup-{stamp}.zip"
    else:
        stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
        out_path = Path.home() / f"hermes-backup-{stamp}.zip"

    # 确保后缀为 .zip
    if out_path.suffix.lower() != ".zip":
        out_path = out_path.with_suffix(out_path.suffix + ".zip")

    # 确保父目录存在
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # 收集文件
    print(f"Scanning {display_hermes_home()} ...")
    files_to_add: list[tuple[Path, Path]] = []  # (绝对路径, 相对路径)
    skipped_dirs = set()

    for dirpath, dirnames, filenames in os.walk(hermes_root, followlinks=False):
        dp = Path(dirpath)
        rel_dir = dp.relative_to(hermes_root)

        # 就地裁剪排除目录，使 os.walk 不会深入遍历
        orig_dirnames = dirnames[:]
        dirnames[:] = [
            d for d in dirnames
            if d not in _EXCLUDED_DIRS
        ]
        for removed in set(orig_dirnames) - set(dirnames):
            skipped_dirs.add(str(rel_dir / removed))

        for fname in filenames:
            fpath = dp / fname
            rel = fpath.relative_to(hermes_root)

            if _should_exclude(rel):
                continue

            # 跳过输出 zip 文件本身（如果它恰好在 hermes 根目录内）
            try:
                if fpath.resolve() == out_path.resolve():
                    continue
            except (OSError, ValueError):
                pass

            files_to_add.append((fpath, rel))

    if not files_to_add:
        print("No files to back up.")
        return

    # 创建 zip 归档
    file_count = len(files_to_add)
    print(f"Backing up {file_count} files ...")

    total_bytes = 0
    errors = []
    t0 = time.monotonic()

    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for i, (abs_path, rel_path) in enumerate(files_to_add, 1):
            try:
                # 对 SQLite 数据库使用安全拷贝（处理 WAL 模式）
                if abs_path.suffix == ".db":
                    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
                        tmp_db = Path(tmp.name)
                    if _safe_copy_db(abs_path, tmp_db):
                        zf.write(tmp_db, arcname=str(rel_path))
                        total_bytes += tmp_db.stat().st_size
                        tmp_db.unlink(missing_ok=True)
                    else:
                        tmp_db.unlink(missing_ok=True)
                        errors.append(f"  {rel_path}: SQLite safe copy failed")
                        continue
                else:
                    zf.write(abs_path, arcname=str(rel_path))
                    total_bytes += abs_path.stat().st_size
            except (PermissionError, OSError) as exc:
                errors.append(f"  {rel_path}: {exc}")
                continue

            # 每 500 个文件显示一次进度
            if i % 500 == 0:
                print(f"  {i}/{file_count} files ...")

    elapsed = time.monotonic() - t0
    zip_size = out_path.stat().st_size

    # 摘要
    print()
    print(f"Backup complete: {out_path}")
    print(f"  Files:       {file_count}")
    print(f"  Original:    {_format_size(total_bytes)}")
    print(f"  Compressed:  {_format_size(zip_size)}")
    print(f"  Time:        {elapsed:.1f}s")

    if skipped_dirs:
        print(f"\n  Excluded directories:")
        for d in sorted(skipped_dirs):
            print(f"    {d}/")

    if errors:
        print(f"\n  Warnings ({len(errors)} files skipped):")
        for e in errors[:10]:
            print(e)
        if len(errors) > 10:
            print(f"  ... and {len(errors) - 10} more")

    print(f"\nRestore with: hermes import {out_path.name}")


# ---------------------------------------------------------------------------
# 导入
# ---------------------------------------------------------------------------

def _validate_backup_zip(zf: zipfile.ZipFile) -> tuple[bool, str]:
    """检查 zip 文件是否看起来像 Hermes 备份。

    返回 (是否有效, 原因)。
    """
    names = zf.namelist()
    if not names:
        return False, "zip archive is empty"

    # 查找 hermes 主目录中应有的标志性文件
    markers = {"config.yaml", ".env", "state.db"}
    found = set()
    for n in names:
        # 可能在根级别或深一级（如果有人打包了整个目录）
        basename = Path(n).name
        if basename in markers:
            found.add(basename)

    if not found:
        return False, (
            "zip does not appear to be a Hermes backup "
            "(no config.yaml, .env, or state databases found)"
        )

    return True, ""


def _detect_prefix(zf: zipfile.ZipFile) -> str:
    """检测 zip 中是否有包裹所有条目的公共目录前缀。

    某些工具会把文件打包为 `.hermes/config.yaml` 而不是 `config.yaml`。
    返回需要去除的前缀（如果没有则返回空字符串）。
    """
    names = [n for n in zf.namelist() if not n.endswith("/")]
    if not names:
        return ""

    # 查找公共前缀
    parts_list = [Path(n).parts for n in names]

    # 检查所有条目是否共享同一个首级目录
    first_parts = {p[0] for p in parts_list if len(p) > 1}
    if len(first_parts) == 1:
        prefix = first_parts.pop()
        # 只有当它看起来像 hermes 目录名时才去除
        if prefix in (".hermes", "hermes"):
            return prefix + "/"

    return ""


def run_import(args) -> None:
    """从 zip 文件恢复 Hermes 备份。"""
    zip_path = Path(args.zipfile).expanduser().resolve()

    if not zip_path.is_file():
        print(f"Error: File not found: {zip_path}")
        sys.exit(1)

    if not zipfile.is_zipfile(zip_path):
        print(f"Error: Not a valid zip file: {zip_path}")
        sys.exit(1)

    hermes_root = get_default_hermes_root()

    with zipfile.ZipFile(zip_path, "r") as zf:
        # 验证备份文件
        ok, reason = _validate_backup_zip(zf)
        if not ok:
            print(f"Error: {reason}")
            sys.exit(1)

        prefix = _detect_prefix(zf)
        members = [n for n in zf.namelist() if not n.endswith("/")]
        file_count = len(members)

        print(f"Backup contains {file_count} files")
        print(f"Target: {display_hermes_home()}")

        if prefix:
            print(f"Detected archive prefix: {prefix!r} (will be stripped)")

        # 检查是否存在已有安装
        has_config = (hermes_root / "config.yaml").exists()
        has_env = (hermes_root / ".env").exists()

        if (has_config or has_env) and not args.force:
            print()
            print("Warning: Target directory already has Hermes configuration.")
            print("Importing will overwrite existing files with backup contents.")
            print()
            try:
                answer = input("Continue? [y/N] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print("\nAborted.")
                sys.exit(1)
            if answer not in ("y", "yes"):
                print("Aborted.")
                return

        # 解压文件
        print(f"\nImporting {file_count} files ...")
        hermes_root.mkdir(parents=True, exist_ok=True)

        errors = []
        restored = 0
        t0 = time.monotonic()

        for member in members:
            # 去除已检测到的前缀
            if prefix and member.startswith(prefix):
                rel = member[len(prefix):]
            else:
                rel = member

            if not rel:
                continue

            target = hermes_root / rel

            # 安全检查：拒绝绝对路径和路径穿越攻击
            try:
                target.resolve().relative_to(hermes_root.resolve())
            except ValueError:
                errors.append(f"  {rel}: path traversal blocked")
                continue

            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as src, open(target, "wb") as dst:
                    dst.write(src.read())
                restored += 1
            except (PermissionError, OSError) as exc:
                errors.append(f"  {rel}: {exc}")

            if restored % 500 == 0:
                print(f"  {restored}/{file_count} files ...")

        elapsed = time.monotonic() - t0

        # 摘要
        print()
        print(f"Import complete: {restored} files restored in {elapsed:.1f}s")
        print(f"  Target: {display_hermes_home()}")

        if errors:
            print(f"\n  Warnings ({len(errors)} files skipped):")
            for e in errors[:10]:
                print(e)
            if len(errors) > 10:
                print(f"  ... and {len(errors) - 10} more")

        # 导入后操作：恢复配置文件的包装脚本
        profiles_dir = hermes_root / "profiles"
        restored_profiles = []
        if profiles_dir.is_dir():
            try:
                from hermes_cli.profiles import (
                    create_wrapper_script, check_alias_collision,
                    _is_wrapper_dir_in_path, _get_wrapper_dir,
                )
                for entry in sorted(profiles_dir.iterdir()):
                    if not entry.is_dir():
                        continue
                    profile_name = entry.name
                    # 只为有配置的目录创建包装脚本
                    if not (entry / "config.yaml").exists() and not (entry / ".env").exists():
                        continue
                    collision = check_alias_collision(profile_name)
                    if collision:
                        print(f"  Skipped alias '{profile_name}': {collision}")
                        restored_profiles.append((profile_name, False))
                    else:
                        wrapper = create_wrapper_script(profile_name)
                        restored_profiles.append((profile_name, wrapper is not None))

                if restored_profiles:
                    created = [n for n, ok in restored_profiles if ok]
                    skipped = [n for n, ok in restored_profiles if not ok]
                    if created:
                        print(f"\n  Profile aliases restored: {', '.join(created)}")
                    if skipped:
                        print(f"  Profile aliases skipped:  {', '.join(skipped)}")
                    if not _is_wrapper_dir_in_path():
                        print(f"\n  Note: {_get_wrapper_dir()} is not in your PATH.")
                        print('  Add to your shell config (~/.bashrc or ~/.zshrc):')
                        print('    export PATH="$HOME/.local/bin:$PATH"')
            except ImportError:
                # hermes_cli.profiles 可能不可用（全新安装时）
                if any(profiles_dir.iterdir()):
                    print(f"\n  Profiles detected but aliases could not be created.")
                    print(f"  Run: hermes profile list  (after installing hermes)")

        # 后续指引
        print()
        if not (hermes_root / "hermes-agent").is_dir():
            print("Note: The hermes-agent codebase was not included in the backup.")
            print("  If this is a fresh install, run: hermes update")

        if restored_profiles:
            gw_profiles = [n for n, _ in restored_profiles]
            print("\nTo re-enable gateway services for profiles:")
            for pname in gw_profiles:
                print(f"  hermes -p {pname} gateway install")

        print("Done. Your Hermes configuration has been restored.")


# ---------------------------------------------------------------------------
# 快速状态快照（供 /snapshot 斜杠命令和 hermes backup --quick 使用）
# ---------------------------------------------------------------------------

# 快速快照中需要包含的关键状态文件（相对于 HERMES_HOME）。
# 其他文件要么可重新生成（日志、缓存），要么单独管理
# （技能、代码库、sessions/）。
_QUICK_STATE_FILES = (
    "state.db",
    "config.yaml",
    ".env",
    "auth.json",
    "cron/jobs.json",
    "gateway_state.json",
    "channel_directory.json",
    "processes.json",
)

_QUICK_SNAPSHOTS_DIR = "state-snapshots"
_QUICK_DEFAULT_KEEP = 20


def _quick_snapshot_root(hermes_home: Optional[Path] = None) -> Path:
    """获取快照存储根目录。"""
    home = hermes_home or get_hermes_home()
    return home / _QUICK_SNAPSHOTS_DIR


def create_quick_snapshot(
    label: Optional[str] = None,
    hermes_home: Optional[Path] = None,
) -> Optional[str]:
    """创建关键文件的快速状态快照。

    将 STATE_FILES 拷贝到 state-snapshots/ 下的带时间戳目录中。
    自动清理超过保留上限的旧快照。

    返回:
        快照 ID（基于时间戳），如果没有找到文件则返回 None。
    """
    home = hermes_home or get_hermes_home()
    root = _quick_snapshot_root(home)

    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    snap_id = f"{ts}-{label}" if label else ts
    snap_dir = root / snap_id
    snap_dir.mkdir(parents=True, exist_ok=True)

    manifest: Dict[str, int] = {}  # 相对路径 -> 文件大小

    for rel in _QUICK_STATE_FILES:
        src = home / rel
        if not src.exists() or not src.is_file():
            continue

        dst = snap_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)

        try:
            if src.suffix == ".db":
                # SQLite 数据库使用安全拷贝
                if not _safe_copy_db(src, dst):
                    continue
            else:
                shutil.copy2(src, dst)
            manifest[rel] = dst.stat().st_size
        except (OSError, PermissionError) as exc:
            logger.warning("Could not snapshot %s: %s", rel, exc)

    if not manifest:
        # 没有任何文件被快照，清理空目录
        shutil.rmtree(snap_dir, ignore_errors=True)
        return None

    # 写入清单文件
    meta = {
        "id": snap_id,
        "timestamp": ts,
        "label": label,
        "file_count": len(manifest),
        "total_size": sum(manifest.values()),
        "files": manifest,
    }
    with open(snap_dir / "manifest.json", "w") as f:
        json.dump(meta, f, indent=2)

    # 自动清理超过保留上限的旧快照
    _prune_quick_snapshots(root, keep=_QUICK_DEFAULT_KEEP)

    logger.info("State snapshot created: %s (%d files)", snap_id, len(manifest))
    return snap_id


def list_quick_snapshots(
    limit: int = 20,
    hermes_home: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """列出已有的快速状态快照，最新的在前。"""
    root = _quick_snapshot_root(hermes_home)
    if not root.exists():
        return []

    results = []
    for d in sorted(root.iterdir(), reverse=True):
        if not d.is_dir():
            continue
        manifest_path = d / "manifest.json"
        if manifest_path.exists():
            try:
                with open(manifest_path) as f:
                    results.append(json.load(f))
            except (json.JSONDecodeError, OSError):
                results.append({"id": d.name, "file_count": 0, "total_size": 0})
        if len(results) >= limit:
            break

    return results


def restore_quick_snapshot(
    snapshot_id: str,
    hermes_home: Optional[Path] = None,
) -> bool:
    """从快速快照恢复状态。

    用快照的副本覆盖当前的状态文件。
    如果至少恢复了一个文件则返回 True。
    """
    home = hermes_home or get_hermes_home()
    root = _quick_snapshot_root(home)
    snap_dir = root / snapshot_id

    if not snap_dir.is_dir():
        return False

    manifest_path = snap_dir / "manifest.json"
    if not manifest_path.exists():
        return False

    with open(manifest_path) as f:
        meta = json.load(f)

    restored = 0
    for rel in meta.get("files", {}):
        src = snap_dir / rel
        if not src.exists():
            continue

        dst = home / rel
        dst.parent.mkdir(parents=True, exist_ok=True)

        try:
            if dst.suffix == ".db":
                # 数据库文件使用近似原子替换
                tmp = dst.parent / f".{dst.name}.snap_restore"
                shutil.copy2(src, tmp)
                dst.unlink(missing_ok=True)
                shutil.move(str(tmp), str(dst))
            else:
                shutil.copy2(src, dst)
            restored += 1
        except (OSError, PermissionError) as exc:
            logger.error("Failed to restore %s: %s", rel, exc)

    logger.info("Restored %d files from snapshot %s", restored, snapshot_id)
    return restored > 0


def _prune_quick_snapshots(root: Path, keep: int = _QUICK_DEFAULT_KEEP) -> int:
    """删除超过保留上限的最旧快照。返回已删除的数量。"""
    if not root.exists():
        return 0

    # 按名称降序排列（新的在前），超出 keep 数量的都删除
    dirs = sorted(
        (d for d in root.iterdir() if d.is_dir()),
        key=lambda d: d.name,
        reverse=True,
    )

    deleted = 0
    for d in dirs[keep:]:
        try:
            shutil.rmtree(d)
            deleted += 1
        except OSError as exc:
            logger.warning("Failed to prune snapshot %s: %s", d.name, exc)

    return deleted


def prune_quick_snapshots(
    keep: int = _QUICK_DEFAULT_KEEP,
    hermes_home: Optional[Path] = None,
) -> int:
    """手动清理快速快照。返回已删除的数量。"""
    return _prune_quick_snapshots(_quick_snapshot_root(hermes_home), keep=keep)


def run_quick_backup(args) -> None:
    """hermes backup --quick 的 CLI 入口点。"""
    label = getattr(args, "label", None)
    snap_id = create_quick_snapshot(label=label)
    if snap_id:
        print(f"State snapshot created: {snap_id}")
        snaps = list_quick_snapshots()
        print(f"  {len(snaps)} snapshot(s) stored in {display_hermes_home()}/state-snapshots/")
        print(f"  Restore with: /snapshot restore {snap_id}")
    else:
        print("No state files found to snapshot.")
