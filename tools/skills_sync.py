#!/usr/bin/env python3
"""
技能同步 -- 基于清单的内置技能种子和更新。

将仓库 skills/ 目录中的内置技能复制到 ~/.hermes/skills/，
并使用清单跟踪哪些技能已同步及其源哈希。

清单格式（v2）：每行为 "skill_name:origin_hash"，其中 origin_hash
是内置技能上次同步到用户目录时的 MD5 值。
旧版 v1 清单（不含哈希的纯名称）会自动迁移。

更新逻辑：
  - 新技能（不在清单中）：复制到用户目录，记录源哈希。
  - 已有技能（在清单中，用户目录中存在）：
      * 如果用户副本与源哈希匹配：用户未修改 -> 如果内置版本有变化则安全更新。记录新的源哈希。
      * 如果用户副本与源哈希不同：用户已自定义 -> 跳过。
  - 被用户删除（在清单中，用户目录中不存在）：尊重用户选择，不重新添加。
  - 从内置中移除（在清单中，仓库中已不存在）：从清单中清理。

清单文件位于 ~/.hermes/skills/.bundled_manifest。
"""

import hashlib
import logging
import os
import shutil
from pathlib import Path
from hermes_constants import get_hermes_home
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)


HERMES_HOME = get_hermes_home()
SKILLS_DIR = HERMES_HOME / "skills"
MANIFEST_FILE = SKILLS_DIR / ".bundled_manifest"


def _get_bundled_dir() -> Path:
    """定位内置 skills/ 目录。

    优先检查 HERMES_BUNDLED_SKILLS 环境变量（由 Nix 包装器设置），
    然后回退到相对于此源文件的路径。
    """
    env_override = os.getenv("HERMES_BUNDLED_SKILLS")
    if env_override:
        return Path(env_override)
    return Path(__file__).parent.parent / "skills"


def _read_manifest() -> Dict[str, str]:
    """
    读取清单，返回 {skill_name: origin_hash} 字典。

    同时处理 v1（纯名称）和 v2（name:hash）格式。
    v1 条目会获得空哈希字符串，这会在下次同步时触发迁移。
    """
    if not MANIFEST_FILE.exists():
        return {}
    try:
        result = {}
        for line in MANIFEST_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            if ":" in line:
                # v2 格式：name:hash
                name, _, hash_val = line.partition(":")
                result[name.strip()] = hash_val.strip()
            else:
                # v1 格式：纯名称 -- 空哈希触发迁移
                result[line] = ""
        return result
    except (OSError, IOError):
        return {}


def _write_manifest(entries: Dict[str, str]):
    """以 v2 格式（name:hash）原子性写入清单文件。

    使用临时文件 + os.replace() 避免进程崩溃或中途中断时损坏文件。
    """
    import tempfile

    MANIFEST_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = "\n".join(f"{name}:{hash_val}" for name, hash_val in sorted(entries.items())) + "\n"

    try:
        fd, tmp_path = tempfile.mkstemp(
            dir=str(MANIFEST_FILE.parent),
            prefix=".bundled_manifest_",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, MANIFEST_FILE)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except Exception as e:
        logger.debug("Failed to write skills manifest %s: %s", MANIFEST_FILE, e, exc_info=True)


def _read_skill_name(skill_md: Path, fallback: str) -> str:
    """从 SKILL.md 的 YAML frontmatter 中读取 name 字段，失败时回退到 *fallback*。"""
    try:
        content = skill_md.read_text(encoding="utf-8", errors="replace")[:4000]
    except OSError:
        return fallback
    in_frontmatter = False
    for line in content.split("\n"):
        stripped = line.strip()
        if stripped == "---":
            if in_frontmatter:
                break
            in_frontmatter = True
            continue
        if in_frontmatter and stripped.startswith("name:"):
            value = stripped.split(":", 1)[1].strip().strip("\"'")
            if value:
                return value
    return fallback


def _discover_bundled_skills(bundled_dir: Path) -> List[Tuple[str, Path]]:
    """
    在内置目录中查找所有 SKILL.md 文件。
    返回 (skill_name, skill_directory_path) 元组列表。
    """
    skills = []
    if not bundled_dir.exists():
        return skills

    for skill_md in bundled_dir.rglob("SKILL.md"):
        path_str = str(skill_md)
        if "/.git/" in path_str or "/.github/" in path_str or "/.hub/" in path_str:
            continue
        skill_dir = skill_md.parent
        skill_name = _read_skill_name(skill_md, skill_dir.name)
        skills.append((skill_name, skill_dir))

    return skills


def _compute_relative_dest(skill_dir: Path, bundled_dir: Path) -> Path:
    """
    计算 SKILLS_DIR 中的目标路径，保留分类结构。
    例如：bundled/skills/mlops/axolotl -> ~/.hermes/skills/mlops/axolotl
    """
    rel = skill_dir.relative_to(bundled_dir)
    return SKILLS_DIR / rel


def _dir_hash(directory: Path) -> str:
    """计算目录中所有文件内容的哈希值，用于变更检测。"""
    hasher = hashlib.md5()
    try:
        for fpath in sorted(directory.rglob("*")):
            if fpath.is_file():
                rel = fpath.relative_to(directory)
                hasher.update(str(rel).encode("utf-8"))
                hasher.update(fpath.read_bytes())
    except (OSError, IOError):
        pass
    return hasher.hexdigest()


def sync_skills(quiet: bool = False) -> dict:
    """
    使用清单将内置技能同步到 ~/.hermes/skills/。

    返回：
        包含以下键的字典：copied（列表）、updated（列表）、skipped（整数）、
                        user_modified（列表）、cleaned（列表）、total_bundled（整数）
    """
    bundled_dir = _get_bundled_dir()
    if not bundled_dir.exists():
        return {
            "copied": [], "updated": [], "skipped": 0,
            "user_modified": [], "cleaned": [], "total_bundled": 0,
        }

    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    manifest = _read_manifest()
    bundled_skills = _discover_bundled_skills(bundled_dir)
    bundled_names = {name for name, _ in bundled_skills}

    copied = []
    updated = []
    user_modified = []
    skipped = 0

    for skill_name, skill_src in bundled_skills:
        dest = _compute_relative_dest(skill_src, bundled_dir)
        bundled_hash = _dir_hash(skill_src)

        if skill_name not in manifest:
            # ── 新技能 — 之前从未提供过 ──
            try:
                if dest.exists():
                    # 用户目录中已有同名技能 — 不覆盖
                    skipped += 1
                    manifest[skill_name] = bundled_hash
                else:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copytree(skill_src, dest)
                    copied.append(skill_name)
                    manifest[skill_name] = bundled_hash
                    if not quiet:
                        print(f"  + {skill_name}")
            except (OSError, IOError) as e:
                if not quiet:
                    print(f"  ! Failed to copy {skill_name}: {e}")
                # 不要添加到清单 — 下次同步应重试

        elif dest.exists():
            # ── 已有技能 — 在清单中且在磁盘上 ──
            origin_hash = manifest.get(skill_name, "")
            user_hash = _dir_hash(dest)

            if not origin_hash:
                # v1 迁移：未记录源哈希。从用户当前副本设置基线，
                # 以便未来同步能检测到修改。
                manifest[skill_name] = user_hash
                if user_hash == bundled_hash:
                    skipped += 1  # 已经同步
                else:
                    # 无法判断是用户修改还是内置版本变化 — 保守处理
                    skipped += 1
                continue

            if user_hash != origin_hash:
                # 用户修改了此技能 — 不覆盖他们的更改
                user_modified.append(skill_name)
                if not quiet:
                    print(f"  ~ {skill_name} (user-modified, skipping)")
                continue

            # 用户副本与源匹配 — 检查内置版本是否有更新
            if bundled_hash != origin_hash:
                try:
                    # 将旧副本移到备份位置，以便失败时恢复
                    backup = dest.with_suffix(".bak")
                    shutil.move(str(dest), str(backup))
                    try:
                        shutil.copytree(skill_src, dest)
                        manifest[skill_name] = bundled_hash
                        updated.append(skill_name)
                        if not quiet:
                            print(f"  ↑ {skill_name} (updated)")
                        # 成功复制后删除备份
                        shutil.rmtree(backup, ignore_errors=True)
                    except (OSError, IOError):
                        # 从备份恢复
                        if backup.exists() and not dest.exists():
                            shutil.move(str(backup), str(dest))
                        raise
                except (OSError, IOError) as e:
                    if not quiet:
                        print(f"  ! Failed to update {skill_name}: {e}")
            else:
                skipped += 1  # 内置版本未变，用户副本未变

        else:
            # ── 在清单中但不在磁盘上 — 用户已删除 ──
            skipped += 1

    # 清理过时的清单条目（从内置目录中移除的技能）
    cleaned = sorted(set(manifest.keys()) - bundled_names)
    for name in cleaned:
        del manifest[name]

    # 同时复制分类的 DESCRIPTION.md 文件（如果尚不存在）
    for desc_md in bundled_dir.rglob("DESCRIPTION.md"):
        rel = desc_md.relative_to(bundled_dir)
        dest_desc = SKILLS_DIR / rel
        if not dest_desc.exists():
            try:
                dest_desc.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(desc_md, dest_desc)
            except (OSError, IOError) as e:
                logger.debug("Could not copy %s: %s", desc_md, e)

    _write_manifest(manifest)

    return {
        "copied": copied,
        "updated": updated,
        "skipped": skipped,
        "user_modified": user_modified,
        "cleaned": cleaned,
        "total_bundled": len(bundled_skills),
    }


def reset_bundled_skill(name: str, restore: bool = False) -> dict:
    """
    重置内置技能的清单跟踪，使未来的同步正常工作。

    当用户编辑内置技能后，后续同步会将其标记为 ``user_modified``
    并永远跳过 -- 即使用户后来将内置版本复制回去，因为清单中
    仍保留着 *旧的* 源哈希。此函数打破这个循环。

    参数：
        name: 技能名称（匹配清单键/技能 frontmatter 名称）。
        restore: 如果为 True，同时删除 SKILLS_DIR 中的用户副本，让
                 下次同步重新复制当前内置版本。如果为 False（默认），
                 只清除清单条目 -- 保留用户当前的副本，但未来的
                 更新能再次生效。

    返回：
        包含以下键的字典：
          - ok: bool，重置是否成功
          - action: "manifest_cleared"、"restored"、"not_in_manifest"
                    或 "bundled_missing" 之一
          - message: 人类可读的描述
          - synced: 如果触发了同步则为 sync_skills() 的返回值，否则为 None
    """
    manifest = _read_manifest()
    bundled_dir = _get_bundled_dir()
    bundled_skills = _discover_bundled_skills(bundled_dir)
    bundled_by_name = {skill_name: skill_dir for skill_name, skill_dir in bundled_skills}

    in_manifest = name in manifest
    is_bundled = name in bundled_by_name

    if not in_manifest and not is_bundled:
        return {
            "ok": False,
            "action": "not_in_manifest",
            "message": (
                f"'{name}' is not a tracked bundled skill. Nothing to reset. "
                f"(Hub-installed skills use `hermes skills uninstall`.)"
            ),
            "synced": None,
        }

    # 第一步：删除清单条目，使下次同步将其视为新技能
    if in_manifest:
        del manifest[name]
        _write_manifest(manifest)

    # 第二步（可选）：删除用户副本，使下次同步重新从内置复制
    deleted_user_copy = False
    if restore:
        if not is_bundled:
            return {
                "ok": False,
                "action": "bundled_missing",
                "message": (
                    f"'{name}' has no bundled source — manifest entry cleared "
                    f"but cannot restore from bundled (skill was removed upstream)."
                ),
                "synced": None,
            }
        # 目标路径基于内置目录的相对路径镜像。
        dest = _compute_relative_dest(bundled_by_name[name], bundled_dir)
        if dest.exists():
            try:
                shutil.rmtree(dest)
                deleted_user_copy = True
            except (OSError, IOError) as e:
                return {
                    "ok": False,
                    "action": "manifest_cleared",
                    "message": (
                        f"Cleared manifest entry for '{name}' but could not "
                        f"delete user copy at {dest}: {e}"
                    ),
                    "synced": None,
                }

    # 第三步：运行同步以重新设定基线（如果已删除则重新复制）
    synced = sync_skills(quiet=True)

    if restore and deleted_user_copy:
        action = "restored"
        message = f"Restored '{name}' from bundled source."
    elif restore:
        # 磁盘上没有要删除的内容，但我们重新同步了 — 相当于全新安装
        action = "restored"
        message = f"Restored '{name}' (no prior user copy, re-copied from bundled)."
    else:
        action = "manifest_cleared"
        message = (
            f"Cleared manifest entry for '{name}'. Future `hermes update` runs "
            f"will re-baseline against your current copy and accept upstream changes."
        )

    return {"ok": True, "action": action, "message": message, "synced": synced}


if __name__ == "__main__":
    print("Syncing bundled skills into ~/.hermes/skills/ ...")
    result = sync_skills(quiet=False)
    parts = [
        f"{len(result['copied'])} new",
        f"{len(result['updated'])} updated",
        f"{result['skipped']} unchanged",
    ]
    if result["user_modified"]:
        parts.append(f"{len(result['user_modified'])} user-modified (kept)")
    if result["cleaned"]:
        parts.append(f"{len(result['cleaned'])} cleaned from manifest")
    print(f"\nDone: {', '.join(parts)}. {result['total_bundled']} total bundled.")
