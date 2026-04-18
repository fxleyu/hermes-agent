#!/usr/bin/env python3
"""
技能管理工具 -- 由智能体管理的技能创建与编辑

允许智能体创建、更新和删除技能，将成功的方法转化为可复用的过程性知识。
新技能创建在 ~/.hermes/skills/ 目录下。已有技能（内置的、从 hub 安装的
或用户创建的）可以在其所在位置进行修改或删除。

技能是智能体的过程性记忆：它们记录*如何完成特定类型的任务*，
基于已验证的经验。通用记忆（MEMORY.md、USER.md）是
广泛且声明性的。技能则是狭窄且可操作的。

操作：
  create     -- 创建新技能（SKILL.md + 目录结构）
  edit       -- 替换用户技能的 SKILL.md 内容（完全重写）
  patch      -- 在 SKILL.md 或任何支持文件中进行定向查找替换
  delete     -- 完全删除用户技能
  write_file -- 添加/覆盖支持文件（参考资料、模板、脚本、资产）
  remove_file-- 从用户技能中删除支持文件

用户技能的目录布局：
    ~/.hermes/skills/
    ├── my-skill/
    │   ├── SKILL.md
    │   ├── references/
    │   ├── templates/
    │   ├── scripts/
    │   └── assets/
    └── category-name/
        └── another-skill/
            └── SKILL.md
"""

import json
import logging
import os
import re
import shutil
import tempfile
from pathlib import Path
from hermes_constants import get_hermes_home, display_hermes_home
from typing import Dict, Any, Optional, Tuple

logger = logging.getLogger(__name__)

# 导入安全扫描器——智能体创建的技能与社区 hub 安装的技能接受相同的安全审查。
try:
    from tools.skills_guard import scan_skill, should_allow_install, format_scan_report
    _GUARD_AVAILABLE = True
except ImportError:
    _GUARD_AVAILABLE = False


def _security_scan_skill(skill_dir: Path) -> Optional[str]:
    """写入后扫描技能目录。如果被阻止返回错误字符串，否则返回 None。"""
    if not _GUARD_AVAILABLE:
        return None
    try:
        result = scan_skill(skill_dir, source="agent-created")
        allowed, reason = should_allow_install(result)
        if allowed is False:
            report = format_scan_report(result)
            return f"Security scan blocked this skill ({reason}):\n{report}"
        if allowed is None:
            # "ask" 判定——对于智能体创建的技能，这意味着检测到了危险的
            # 发现。阻止技能并附上报告。
            report = format_scan_report(result)
            logger.warning("Agent-created skill blocked (dangerous findings): %s", reason)
            return f"Security scan blocked this skill ({reason}):\n{report}"
    except Exception as e:
        logger.warning("Security scan failed for %s: %s", skill_dir, e, exc_info=True)
    return None

import yaml


# 所有技能都存放在 ~/.hermes/skills/（单一数据源）
HERMES_HOME = get_hermes_home()
SKILLS_DIR = HERMES_HOME / "skills"

MAX_NAME_LENGTH = 64
MAX_DESCRIPTION_LENGTH = 1024


def _is_local_skill(skill_path: Path) -> bool:
    """检查技能路径是否在本地 SKILLS_DIR 内。

    在 external_dirs 中找到的技能从智能体角度来看是只读的。
    """
    try:
        skill_path.resolve().relative_to(SKILLS_DIR.resolve())
        return True
    except ValueError:
        return False
MAX_SKILL_CONTENT_CHARS = 100_000   # ~36k tokens at 2.75 chars/token
MAX_SKILL_FILE_BYTES = 1_048_576    # 1 MiB per supporting file

# 技能名称允许的字符（文件系统安全、URL 友好）
VALID_NAME_RE = re.compile(r'^[a-z0-9][a-z0-9._-]*$')

# write_file/remove_file 允许的子目录
ALLOWED_SUBDIRS = {"references", "templates", "scripts", "assets"}


# =============================================================================
# 校验辅助函数
# =============================================================================

def _validate_name(name: str) -> Optional[str]:
    """校验技能名称。返回错误信息或 None（如果有效）。"""
    if not name:
        return "Skill name is required."
    if len(name) > MAX_NAME_LENGTH:
        return f"Skill name exceeds {MAX_NAME_LENGTH} characters."
    if not VALID_NAME_RE.match(name):
        return (
            f"Invalid skill name '{name}'. Use lowercase letters, numbers, "
            f"hyphens, dots, and underscores. Must start with a letter or digit."
        )
    return None


def _validate_category(category: Optional[str]) -> Optional[str]:
    """校验可选的分类名称（用作单个目录段）。"""
    if category is None:
        return None
    if not isinstance(category, str):
        return "Category must be a string."

    category = category.strip()
    if not category:
        return None
    if "/" in category or "\\" in category:
        return (
            f"Invalid category '{category}'. Use lowercase letters, numbers, "
            "hyphens, dots, and underscores. Categories must be a single directory name."
        )
    if len(category) > MAX_NAME_LENGTH:
        return f"Category exceeds {MAX_NAME_LENGTH} characters."
    if not VALID_NAME_RE.match(category):
        return (
            f"Invalid category '{category}'. Use lowercase letters, numbers, "
            "hyphens, dots, and underscores. Categories must be a single directory name."
        )
    return None


def _validate_frontmatter(content: str) -> Optional[str]:
    """
    校验 SKILL.md 内容是否有正确的 frontmatter 且包含必填字段。
    返回错误信息或 None（如果有效）。
    """
    if not content.strip():
        return "Content cannot be empty."

    if not content.startswith("---"):
        return "SKILL.md must start with YAML frontmatter (---). See existing skills for format."

    end_match = re.search(r'\n---\s*\n', content[3:])
    if not end_match:
        return "SKILL.md frontmatter is not closed. Ensure you have a closing '---' line."

    yaml_content = content[3:end_match.start() + 3]

    try:
        parsed = yaml.safe_load(yaml_content)
    except yaml.YAMLError as e:
        return f"YAML frontmatter parse error: {e}"

    if not isinstance(parsed, dict):
        return "Frontmatter must be a YAML mapping (key: value pairs)."

    if "name" not in parsed:
        return "Frontmatter must include 'name' field."
    if "description" not in parsed:
        return "Frontmatter must include 'description' field."
    if len(str(parsed["description"])) > MAX_DESCRIPTION_LENGTH:
        return f"Description exceeds {MAX_DESCRIPTION_LENGTH} characters."

    body = content[end_match.end() + 3:].strip()
    if not body:
        return "SKILL.md must have content after the frontmatter (instructions, procedures, etc.)."

    return None


def _validate_content_size(content: str, label: str = "SKILL.md") -> Optional[str]:
    """检查内容是否超出智能体写入的字符限制。

    返回错误信息或 None（如果在限制范围内）。
    """
    if len(content) > MAX_SKILL_CONTENT_CHARS:
        return (
            f"{label} content is {len(content):,} characters "
            f"(limit: {MAX_SKILL_CONTENT_CHARS:,}). "
            f"Consider splitting into a smaller SKILL.md with supporting files "
            f"in references/ or templates/."
        )
    return None


def _resolve_skill_dir(name: str, category: str = None) -> Path:
    """构建新技能的目录路径，可选地放在某个分类下。"""
    if category:
        return SKILLS_DIR / category / name
    return SKILLS_DIR / name


def _find_skill(name: str) -> Optional[Dict[str, Any]]:
    """
    在所有技能目录中按名称查找技能。

    先搜索本地技能目录（~/.hermes/skills/），然后搜索
    通过 skills.external_dirs 配置的外部目录。
    返回 {"path": Path} 或 None。
    """
    from agent.skill_utils import get_all_skills_dirs
    for skills_dir in get_all_skills_dirs():
        if not skills_dir.exists():
            continue
        for skill_md in skills_dir.rglob("SKILL.md"):
            if skill_md.parent.name == name:
                return {"path": skill_md.parent}
    return None


def _validate_file_path(file_path: str) -> Optional[str]:
    """
    校验 write_file/remove_file 的文件路径。
    必须在允许的子目录下且不能逃逸出技能目录。
    """
    from tools.path_security import has_traversal_component

    if not file_path:
        return "file_path is required."

    normalized = Path(file_path)

    # 防止路径遍历
    if has_traversal_component(file_path):
        return "Path traversal ('..') is not allowed."

    # 必须在允许的子目录下
    if not normalized.parts or normalized.parts[0] not in ALLOWED_SUBDIRS:
        allowed = ", ".join(sorted(ALLOWED_SUBDIRS))
        return f"File must be under one of: {allowed}. Got: '{file_path}'"

    # 必须有文件名（不能只是目录）
    if len(normalized.parts) < 2:
        return f"Provide a file path, not just a directory. Example: '{normalized.parts[0]}/myfile.md'"

    return None


def _resolve_skill_target(skill_dir: Path, file_path: str) -> Tuple[Optional[Path], Optional[str]]:
    """解析支持文件路径并确保其保持在技能目录内。"""
    from tools.path_security import validate_within_dir

    target = skill_dir / file_path
    error = validate_within_dir(target, skill_dir)
    if error:
        return None, error
    return target, None


def _atomic_write_text(file_path: Path, content: str, encoding: str = "utf-8") -> None:
    """
    原子性地将文本内容写入文件。

    使用同一目录中的临时文件和 os.replace() 来确保在进程崩溃或
    中断时，目标文件不会处于部分写入状态。

    参数：
        file_path: 目标文件路径
        content: 要写入的内容
        encoding: 文本编码（默认: utf-8）
    """
    file_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(
        dir=str(file_path.parent),
        prefix=f".{file_path.name}.tmp.",
        suffix="",
    )
    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(content)
        os.replace(temp_path, file_path)
    except Exception:
        # 出错时清理临时文件
        try:
            os.unlink(temp_path)
        except OSError:
            logger.error("Failed to remove temporary file %s during atomic write", temp_path, exc_info=True)
        raise


# =============================================================================
# 核心操作
# =============================================================================

def _create_skill(name: str, content: str, category: str = None) -> Dict[str, Any]:
    """使用 SKILL.md 内容创建新的用户技能。"""
    # 校验名称
    err = _validate_name(name)
    if err:
        return {"success": False, "error": err}

    err = _validate_category(category)
    if err:
        return {"success": False, "error": err}

    # 校验内容
    err = _validate_frontmatter(content)
    if err:
        return {"success": False, "error": err}

    err = _validate_content_size(content)
    if err:
        return {"success": False, "error": err}

    # 检查所有目录中的名称冲突
    existing = _find_skill(name)
    if existing:
        return {
            "success": False,
            "error": f"A skill named '{name}' already exists at {existing['path']}."
        }

    # 创建技能目录
    skill_dir = _resolve_skill_dir(name, category)
    skill_dir.mkdir(parents=True, exist_ok=True)

    # 原子性写入 SKILL.md
    skill_md = skill_dir / "SKILL.md"
    _atomic_write_text(skill_md, content)

    # 安全扫描——如果被阻止则回滚

    result = {
        "success": True,
        "message": f"Skill '{name}' created.",
        "path": str(skill_dir.relative_to(SKILLS_DIR)),
        "skill_md": str(skill_md),
    }
    if category:
        result["category"] = category
    result["hint"] = (
        "To add reference files, templates, or scripts, use "
        "skill_manage(action='write_file', name='{}', file_path='references/example.md', file_content='...')".format(name)
    )
    return result


def _edit_skill(name: str, content: str) -> Dict[str, Any]:
    """替换任何已有技能的 SKILL.md（完全重写）。"""
    err = _validate_frontmatter(content)
    if err:
        return {"success": False, "error": err}

    err = _validate_content_size(content)
    if err:
        return {"success": False, "error": err}

    existing = _find_skill(name)
    if not existing:
        return {"success": False, "error": f"Skill '{name}' not found. Use skills_list() to see available skills."}

    if not _is_local_skill(existing["path"]):
        return {"success": False, "error": f"Skill '{name}' is in an external directory and cannot be modified. Copy it to your local skills directory first."}

    skill_md = existing["path"] / "SKILL.md"
    # 备份原始内容用于回滚
    original_content = skill_md.read_text(encoding="utf-8") if skill_md.exists() else None
    _atomic_write_text(skill_md, content)

    # 安全扫描——如果被阻止则回滚
    scan_error = _security_scan_skill(existing["path"])
    if scan_error:
        if original_content is not None:
            _atomic_write_text(skill_md, original_content)
        return {"success": False, "error": scan_error}

    return {
        "success": True,
        "message": f"Skill '{name}' updated.",
        "path": str(existing["path"]),
    }


def _patch_skill(
    name: str,
    old_string: str,
    new_string: str,
    file_path: str = None,
    replace_all: bool = False,
) -> Dict[str, Any]:
    """在技能文件中进行定向查找替换。

    默认操作 SKILL.md。使用 file_path 参数可改为操作支持文件。
    除非 replace_all 为 True，否则要求匹配唯一。
    """
    if not old_string:
        return {"success": False, "error": "old_string is required for 'patch'."}
    if new_string is None:
        return {"success": False, "error": "new_string is required for 'patch'. Use an empty string to delete matched text."}

    existing = _find_skill(name)
    if not existing:
        return {"success": False, "error": f"Skill '{name}' not found."}

    if not _is_local_skill(existing["path"]):
        return {"success": False, "error": f"Skill '{name}' is in an external directory and cannot be modified. Copy it to your local skills directory first."}

    skill_dir = existing["path"]

    if file_path:
        # 修补支持文件
        err = _validate_file_path(file_path)
        if err:
            return {"success": False, "error": err}
        target, err = _resolve_skill_target(skill_dir, file_path)
        if err:
            return {"success": False, "error": err}
    else:
        # 修补 SKILL.md
        target = skill_dir / "SKILL.md"

    if not target.exists():
        return {"success": False, "error": f"File not found: {target.relative_to(skill_dir)}"}

    content = target.read_text(encoding="utf-8")

    # 使用与文件补丁工具相同的模糊匹配引擎。
    # 处理空白规范化、缩进差异、转义序列和块锚定匹配——
    # 避免智能体因微小格式差异导致精确匹配失败。
    from tools.fuzzy_match import fuzzy_find_and_replace

    new_content, match_count, _strategy, match_error = fuzzy_find_and_replace(
        content, old_string, new_string, replace_all
    )
    if match_error:
        # 显示文件的简短预览，以便模型可以自我纠正
        preview = content[:500] + ("..." if len(content) > 500 else "")
        return {
            "success": False,
            "error": match_error,
            "file_preview": preview,
        }

    # 检查结果的大小限制
    target_label = "SKILL.md" if not file_path else file_path
    err = _validate_content_size(new_content, label=target_label)
    if err:
        return {"success": False, "error": err}

    # 如果修补的是 SKILL.md，验证 frontmatter 是否仍然完整
    if not file_path:
        err = _validate_frontmatter(new_content)
        if err:
            return {
                "success": False,
                "error": f"Patch would break SKILL.md structure: {err}",
            }

    original_content = content  # 用于回滚
    _atomic_write_text(target, new_content)

    # 安全扫描——如果被阻止则回滚
    scan_error = _security_scan_skill(skill_dir)
    if scan_error:
        _atomic_write_text(target, original_content)
        return {"success": False, "error": scan_error}

    return {
        "success": True,
        "message": f"Patched {'SKILL.md' if not file_path else file_path} in skill '{name}' ({match_count} replacement{'s' if match_count > 1 else ''}).",
    }


def _delete_skill(name: str) -> Dict[str, Any]:
    """删除技能。"""
    existing = _find_skill(name)
    if not existing:
        return {"success": False, "error": f"Skill '{name}' not found."}

    if not _is_local_skill(existing["path"]):
        return {"success": False, "error": f"Skill '{name}' is in an external directory and cannot be deleted."}

    skill_dir = existing["path"]
    shutil.rmtree(skill_dir)

    # 清理空的分类目录（不删除 SKILLS_DIR 本身）
    parent = skill_dir.parent
    if parent != SKILLS_DIR and parent.exists() and not any(parent.iterdir()):
        parent.rmdir()

    return {
        "success": True,
        "message": f"Skill '{name}' deleted.",
    }


def _write_file(name: str, file_path: str, file_content: str) -> Dict[str, Any]:
    """在任何技能目录中添加或覆盖支持文件。"""
    err = _validate_file_path(file_path)
    if err:
        return {"success": False, "error": err}

    if not file_content and file_content != "":
        return {"success": False, "error": "file_content is required."}

    # 检查大小限制
    content_bytes = len(file_content.encode("utf-8"))
    if content_bytes > MAX_SKILL_FILE_BYTES:
        return {
            "success": False,
            "error": (
                f"File content is {content_bytes:,} bytes "
                f"(limit: {MAX_SKILL_FILE_BYTES:,} bytes / 1 MiB). "
                f"Consider splitting into smaller files."
            ),
        }
    err = _validate_content_size(file_content, label=file_path)
    if err:
        return {"success": False, "error": err}

    existing = _find_skill(name)
    if not existing:
        return {"success": False, "error": f"Skill '{name}' not found. Create it first with action='create'."}

    if not _is_local_skill(existing["path"]):
        return {"success": False, "error": f"Skill '{name}' is in an external directory and cannot be modified. Copy it to your local skills directory first."}

    target, err = _resolve_skill_target(existing["path"], file_path)
    if err:
        return {"success": False, "error": err}
    target.parent.mkdir(parents=True, exist_ok=True)
    # 备份用于回滚
    original_content = target.read_text(encoding="utf-8") if target.exists() else None
    _atomic_write_text(target, file_content)

    # Security scan — roll back on block
    scan_error = _security_scan_skill(existing["path"])
    if scan_error:
        if original_content is not None:
            _atomic_write_text(target, original_content)
        else:
            target.unlink(missing_ok=True)
        return {"success": False, "error": scan_error}

    return {
        "success": True,
        "message": f"File '{file_path}' written to skill '{name}'.",
        "path": str(target),
    }


def _remove_file(name: str, file_path: str) -> Dict[str, Any]:
    """从任何技能目录中删除支持文件。"""
    err = _validate_file_path(file_path)
    if err:
        return {"success": False, "error": err}

    existing = _find_skill(name)
    if not existing:
        return {"success": False, "error": f"Skill '{name}' not found."}

    if not _is_local_skill(existing["path"]):
        return {"success": False, "error": f"Skill '{name}' is in an external directory and cannot be modified."}

    skill_dir = existing["path"]

    target, err = _resolve_skill_target(skill_dir, file_path)
    if err:
        return {"success": False, "error": err}
    if not target.exists():
        # 列出实际存在的文件供模型查看
        available = []
        for subdir in ALLOWED_SUBDIRS:
            d = skill_dir / subdir
            if d.exists():
                for f in d.rglob("*"):
                    if f.is_file():
                        available.append(str(f.relative_to(skill_dir)))
        return {
            "success": False,
            "error": f"File '{file_path}' not found in skill '{name}'.",
            "available_files": available if available else None,
        }

    target.unlink()

    # 清理空的子目录
    parent = target.parent
    if parent != skill_dir and parent.exists() and not any(parent.iterdir()):
        parent.rmdir()

    return {
        "success": True,
        "message": f"File '{file_path}' removed from skill '{name}'.",
    }


# =============================================================================
# 主入口点
# =============================================================================

def skill_manage(
    action: str,
    name: str,
    content: str = None,
    category: str = None,
    file_path: str = None,
    file_content: str = None,
    old_string: str = None,
    new_string: str = None,
    replace_all: bool = False,
) -> str:
    """
    管理用户创建的技能。分发到相应的操作处理函数。

    返回包含结果的 JSON 字符串。
    """
    if action == "create":
        if not content:
            return tool_error("content is required for 'create'. Provide the full SKILL.md text (frontmatter + body).", success=False)
        result = _create_skill(name, content, category)

    elif action == "edit":
        if not content:
            return tool_error("content is required for 'edit'. Provide the full updated SKILL.md text.", success=False)
        result = _edit_skill(name, content)

    elif action == "patch":
        if not old_string:
            return tool_error("old_string is required for 'patch'. Provide the text to find.", success=False)
        if new_string is None:
            return tool_error("new_string is required for 'patch'. Use empty string to delete matched text.", success=False)
        result = _patch_skill(name, old_string, new_string, file_path, replace_all)

    elif action == "delete":
        result = _delete_skill(name)

    elif action == "write_file":
        if not file_path:
            return tool_error("file_path is required for 'write_file'. Example: 'references/api-guide.md'", success=False)
        if file_content is None:
            return tool_error("file_content is required for 'write_file'.", success=False)
        result = _write_file(name, file_path, file_content)

    elif action == "remove_file":
        if not file_path:
            return tool_error("file_path is required for 'remove_file'.", success=False)
        result = _remove_file(name, file_path)

    else:
        result = {"success": False, "error": f"Unknown action '{action}'. Use: create, edit, patch, delete, write_file, remove_file"}

    if result.get("success"):
        try:
            from agent.prompt_builder import clear_skills_system_prompt_cache
            clear_skills_system_prompt_cache(clear_snapshot=True)
        except Exception:
            pass

    return json.dumps(result, ensure_ascii=False)


# =============================================================================
# OpenAI 函数调用 Schema
# =============================================================================

SKILL_MANAGE_SCHEMA = {
    "name": "skill_manage",
    "description": (
        "Manage skills (create, update, delete). Skills are your procedural "
        "memory — reusable approaches for recurring task types. "
        f"New skills go to {display_hermes_home()}/skills/; existing skills can be modified wherever they live.\n\n"
        "Actions: create (full SKILL.md + optional category), "
        "patch (old_string/new_string — preferred for fixes), "
        "edit (full SKILL.md rewrite — major overhauls only), "
        "delete, write_file, remove_file.\n\n"
        "Create when: complex task succeeded (5+ calls), errors overcome, "
        "user-corrected approach worked, non-trivial workflow discovered, "
        "or user asks you to remember a procedure.\n"
        "Update when: instructions stale/wrong, OS-specific failures, "
        "missing steps or pitfalls found during use. "
        "If you used a skill and hit issues not covered by it, patch it immediately.\n\n"
        "After difficult/iterative tasks, offer to save as a skill. "
        "Skip for simple one-offs. Confirm with user before creating/deleting.\n\n"
        "Good skills: trigger conditions, numbered steps with exact commands, "
        "pitfalls section, verification steps. Use skill_view() to see format examples."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["create", "patch", "edit", "delete", "write_file", "remove_file"],
                "description": "The action to perform."
            },
            "name": {
                "type": "string",
                "description": (
                    "Skill name (lowercase, hyphens/underscores, max 64 chars). "
                    "Must match an existing skill for patch/edit/delete/write_file/remove_file."
                )
            },
            "content": {
                "type": "string",
                "description": (
                    "Full SKILL.md content (YAML frontmatter + markdown body). "
                    "Required for 'create' and 'edit'. For 'edit', read the skill "
                    "first with skill_view() and provide the complete updated text."
                )
            },
            "old_string": {
                "type": "string",
                "description": (
                    "Text to find in the file (required for 'patch'). Must be unique "
                    "unless replace_all=true. Include enough surrounding context to "
                    "ensure uniqueness."
                )
            },
            "new_string": {
                "type": "string",
                "description": (
                    "Replacement text (required for 'patch'). Can be empty string "
                    "to delete the matched text."
                )
            },
            "replace_all": {
                "type": "boolean",
                "description": "For 'patch': replace all occurrences instead of requiring a unique match (default: false)."
            },
            "category": {
                "type": "string",
                "description": (
                    "Optional category/domain for organizing the skill (e.g., 'devops', "
                    "'data-science', 'mlops'). Creates a subdirectory grouping. "
                    "Only used with 'create'."
                )
            },
            "file_path": {
                "type": "string",
                "description": (
                    "Path to a supporting file within the skill directory. "
                    "For 'write_file'/'remove_file': required, must be under references/, "
                    "templates/, scripts/, or assets/. "
                    "For 'patch': optional, defaults to SKILL.md if omitted."
                )
            },
            "file_content": {
                "type": "string",
                "description": "Content for the file. Required for 'write_file'."
            },
        },
        "required": ["action", "name"],
    },
}


# --- 注册 ---

registry.register(
    name="skill_manage",
    toolset="skills",
    schema=SKILL_MANAGE_SCHEMA,
    handler=lambda args, **kw: skill_manage(
        action=args.get("action", ""),
        name=args.get("name", ""),
        content=args.get("content"),
        category=args.get("category"),
        file_path=args.get("file_path"),
        file_content=args.get("file_content"),
        old_string=args.get("old_string"),
        new_string=args.get("new_string"),
        replace_all=args.get("replace_all", False)),
    emoji="📝",
)
