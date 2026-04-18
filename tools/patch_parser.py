#!/usr/bin/env python3
"""
V4A 补丁格式解析器

解析 codex、cline 和其他编码代理使用的 V4A 补丁格式。

V4A 格式:
    *** Begin Patch
    *** Update File: path/to/file.py
    @@ 可选上下文提示 @@
     上下文行（空格前缀）
    -删除行（减号前缀）
    +添加行（加号前缀）
    *** Add File: path/to/new.py
    +新文件内容
    +第 2 行
    *** Delete File: path/to/old.py
    *** Move File: old/path.py -> new/path.py
    *** End Patch

用法:
    from tools.patch_parser import parse_v4a_patch, apply_v4a_operations

    operations, error = parse_v4a_patch(patch_content)
    if error:
        print(f"解析错误: {error}")
    else:
        result = apply_v4a_operations(operations, file_ops)
"""

import difflib
import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Any
from enum import Enum


class OperationType(Enum):
    ADD = "add"
    UPDATE = "update"
    DELETE = "delete"
    MOVE = "move"


@dataclass
class HunkLine:
    """补丁 hunk 中的单行。"""
    prefix: str  # ' '、'-' 或 '+'
    content: str


@dataclass
class Hunk:
    """文件内的一组变更。"""
    context_hint: Optional[str] = None
    lines: List[HunkLine] = field(default_factory=list)


@dataclass
class PatchOperation:
    """V4A 补丁中的单个操作。"""
    operation: OperationType
    file_path: str
    new_path: Optional[str] = None  # 用于移动操作
    hunks: List[Hunk] = field(default_factory=list)
    content: Optional[str] = None  # 用于添加文件操作


def parse_v4a_patch(patch_content: str) -> Tuple[List[PatchOperation], Optional[str]]:
    """
    解析 V4A 格式的补丁。

    参数:
        patch_content: V4A 格式的补丁文本

    返回:
        元组 (operations, error_message)
        - 成功: (操作列表, None)
        - 失败: ([], 错误描述)
    """
    lines = patch_content.split('\n')
    operations: List[PatchOperation] = []
    
    # 查找补丁边界
    start_idx = None
    end_idx = None
    
    for i, line in enumerate(lines):
        if '*** Begin Patch' in line or '***Begin Patch' in line:
            start_idx = i
        elif '*** End Patch' in line or '***End Patch' in line:
            end_idx = i
            break
    
    if start_idx is None:
        # 尝试在没有显式起始标记的情况下解析
        start_idx = -1
    
    if end_idx is None:
        end_idx = len(lines)
    
    # 解析边界之间的操作
    i = start_idx + 1
    current_op: Optional[PatchOperation] = None
    current_hunk: Optional[Hunk] = None
    
    while i < end_idx:
        line = lines[i]
        
        # 检查文件操作标记
        update_match = re.match(r'\*\*\*\s*Update\s+File:\s*(.+)', line)
        add_match = re.match(r'\*\*\*\s*Add\s+File:\s*(.+)', line)
        delete_match = re.match(r'\*\*\*\s*Delete\s+File:\s*(.+)', line)
        move_match = re.match(r'\*\*\*\s*Move\s+File:\s*(.+?)\s*->\s*(.+)', line)
        
        if update_match:
            # 保存前一个操作
            if current_op:
                if current_hunk and current_hunk.lines:
                    current_op.hunks.append(current_hunk)
                operations.append(current_op)
            
            current_op = PatchOperation(
                operation=OperationType.UPDATE,
                file_path=update_match.group(1).strip()
            )
            current_hunk = None
            
        elif add_match:
            if current_op:
                if current_hunk and current_hunk.lines:
                    current_op.hunks.append(current_hunk)
                operations.append(current_op)
            
            current_op = PatchOperation(
                operation=OperationType.ADD,
                file_path=add_match.group(1).strip()
            )
            current_hunk = Hunk()
            
        elif delete_match:
            if current_op:
                if current_hunk and current_hunk.lines:
                    current_op.hunks.append(current_hunk)
                operations.append(current_op)
            
            current_op = PatchOperation(
                operation=OperationType.DELETE,
                file_path=delete_match.group(1).strip()
            )
            operations.append(current_op)
            current_op = None
            current_hunk = None
            
        elif move_match:
            if current_op:
                if current_hunk and current_hunk.lines:
                    current_op.hunks.append(current_hunk)
                operations.append(current_op)
            
            current_op = PatchOperation(
                operation=OperationType.MOVE,
                file_path=move_match.group(1).strip(),
                new_path=move_match.group(2).strip()
            )
            operations.append(current_op)
            current_op = None
            current_hunk = None
            
        elif line.startswith('@@'):
            # 上下文提示 / hunk 标记
            if current_op:
                if current_hunk and current_hunk.lines:
                    current_op.hunks.append(current_hunk)
                
                # 提取上下文提示
                hint_match = re.match(r'@@\s*(.+?)\s*@@', line)
                hint = hint_match.group(1) if hint_match else None
                current_hunk = Hunk(context_hint=hint)
                
        elif current_op and line:
            # 解析 hunk 行
            if current_hunk is None:
                current_hunk = Hunk()
            
            if line.startswith('+'):
                current_hunk.lines.append(HunkLine('+', line[1:]))
            elif line.startswith('-'):
                current_hunk.lines.append(HunkLine('-', line[1:]))
            elif line.startswith(' '):
                current_hunk.lines.append(HunkLine(' ', line[1:]))
            elif line.startswith('\\'):
                # "\ No newline at end of file" 标记 - 跳过
                pass
            else:
                # 视为上下文行（隐含空格前缀）
                current_hunk.lines.append(HunkLine(' ', line))
        
        i += 1
    
    # 不要遗忘最后一个操作
    if current_op:
        if current_hunk and current_hunk.lines:
            current_op.hunks.append(current_hunk)
        operations.append(current_op)

    # 验证解析结果
    if not operations:
        # 空补丁不是错误——调用方得到 [] 后可自行决定
        return operations, None

    parse_errors: List[str] = []
    for op in operations:
        if not op.file_path:
            parse_errors.append("Operation with empty file path")
        if op.operation == OperationType.UPDATE and not op.hunks:
            parse_errors.append(f"UPDATE {op.file_path!r}: no hunks found")
        if op.operation == OperationType.MOVE and not op.new_path:
            parse_errors.append(f"MOVE {op.file_path!r}: missing destination path (expected 'src -> dst')")

    if parse_errors:
        return [], "Parse error: " + "; ".join(parse_errors)

    return operations, None


def _count_occurrences(text: str, pattern: str) -> int:
    """计算 *pattern* 在 *text* 中的不重叠出现次数。"""
    count = 0
    start = 0
    while True:
        pos = text.find(pattern, start)
        if pos == -1:
            break
        count += 1
        start = pos + 1
    return count


def _validate_operations(
    operations: List[PatchOperation],
    file_ops: Any,
) -> List[str]:
    """验证所有操作而不写入任何文件。

    返回错误字符串列表；空列表意味着所有操作有效，
    应用阶段可以安全进行。

    对于 UPDATE 操作，hunk 按顺序模拟执行，
    以便后续 hunk 针对前面 hunk 之后的内容进行验证（匹配应用顺序）。
    """
    # 延迟导入：打破 patch_parser <-> fuzzy_match 的循环依赖
    from tools.fuzzy_match import fuzzy_find_and_replace

    errors: List[str] = []

    for op in operations:
        if op.operation == OperationType.UPDATE:
            read_result = file_ops.read_file_raw(op.file_path)
            if read_result.error:
                errors.append(f"{op.file_path}: {read_result.error}")
                continue

            simulated = read_result.content
            for hunk in op.hunks:
                search_lines = [l.content for l in hunk.lines if l.prefix in (' ', '-')]
                if not search_lines:
                    # Addition-only hunk: validate context hint uniqueness
                    if hunk.context_hint:
                        occurrences = _count_occurrences(simulated, hunk.context_hint)
                        if occurrences == 0:
                            errors.append(
                                f"{op.file_path}: addition-only hunk context hint "
                                f"'{hunk.context_hint}' not found"
                            )
                        elif occurrences > 1:
                            errors.append(
                                f"{op.file_path}: addition-only hunk context hint "
                                f"'{hunk.context_hint}' is ambiguous "
                                f"({occurrences} occurrences)"
                            )
                    continue

                search_pattern = '\n'.join(search_lines)
                replace_lines = [l.content for l in hunk.lines if l.prefix in (' ', '+')]
                replacement = '\n'.join(replace_lines)

                new_simulated, count, _strategy, match_error = fuzzy_find_and_replace(
                    simulated, search_pattern, replacement, replace_all=False
                )
                if count == 0:
                    label = f"'{hunk.context_hint}'" if hunk.context_hint else "(no hint)"
                    errors.append(
                        f"{op.file_path}: hunk {label} not found"
                        + (f" — {match_error}" if match_error else "")
                    )
                else:
                    # 推进模拟状态，使后续 hunk 正确验证。
                    # 复用上面调用的结果——无需第二次模糊匹配。
                    simulated = new_simulated

        elif op.operation == OperationType.DELETE:
            read_result = file_ops.read_file_raw(op.file_path)
            if read_result.error:
                errors.append(f"{op.file_path}: file not found for deletion")

        elif op.operation == OperationType.MOVE:
            if not op.new_path:
                errors.append(f"{op.file_path}: MOVE operation missing destination path")
                continue
            src_result = file_ops.read_file_raw(op.file_path)
            if src_result.error:
                errors.append(f"{op.file_path}: source file not found for move")
            dst_result = file_ops.read_file_raw(op.new_path)
            if not dst_result.error:
                errors.append(
                    f"{op.new_path}: destination already exists — move would overwrite"
                )

        # ADD：父目录创建由 write_file 处理；无需预检查。

    return errors


def apply_v4a_operations(operations: List[PatchOperation],
                          file_ops: Any) -> 'PatchResult':
    """应用 V4A 补丁操作，使用文件操作接口。

    采用两阶段的验证-然后-应用方式：
    - 阶段 1：在不写入任何内容的情况下验证所有操作。如果发现任何
      验证错误，立即返回且不修改文件系统。
    - 阶段 2：应用所有操作。此处的失败（例如验证和应用之间的竞态条件）
      会附带运行 ``git diff`` 的提示进行报告。

    参数:
        operations: 来自 parse_v4a_patch 的 PatchOperation 列表
        file_ops: 具有 read_file_raw、write_file 方法的对象

    返回:
        包含所有操作结果的 PatchResult
    """
    # 此处导入以避免循环导入
    from tools.file_operations import PatchResult

    # ---- 阶段 1：验证 ----
    validation_errors = _validate_operations(operations, file_ops)
    if validation_errors:
        return PatchResult(
            success=False,
            error="Patch validation failed (no files were modified):\n"
                  + "\n".join(f"  • {e}" for e in validation_errors),
        )

    # ---- 阶段 2：应用 ----
    files_modified = []
    files_created = []
    files_deleted = []
    all_diffs = []
    errors = []

    for op in operations:
        try:
            if op.operation == OperationType.ADD:
                result = _apply_add(op, file_ops)
                if result[0]:
                    files_created.append(op.file_path)
                    all_diffs.append(result[1])
                else:
                    errors.append(f"Failed to add {op.file_path}: {result[1]}")

            elif op.operation == OperationType.DELETE:
                result = _apply_delete(op, file_ops)
                if result[0]:
                    files_deleted.append(op.file_path)
                    all_diffs.append(result[1])
                else:
                    errors.append(f"Failed to delete {op.file_path}: {result[1]}")

            elif op.operation == OperationType.MOVE:
                result = _apply_move(op, file_ops)
                if result[0]:
                    files_modified.append(f"{op.file_path} -> {op.new_path}")
                    all_diffs.append(result[1])
                else:
                    errors.append(f"Failed to move {op.file_path}: {result[1]}")

            elif op.operation == OperationType.UPDATE:
                result = _apply_update(op, file_ops)
                if result[0]:
                    files_modified.append(op.file_path)
                    all_diffs.append(result[1])
                else:
                    errors.append(f"Failed to update {op.file_path}: {result[1]}")

        except Exception as e:
            errors.append(f"Error processing {op.file_path}: {str(e)}")

    # 对所有修改/创建的文件运行 lint
    lint_results = {}
    for f in files_modified + files_created:
        if hasattr(file_ops, '_check_lint'):
            lint_result = file_ops._check_lint(f)
            lint_results[f] = lint_result.to_dict()

    combined_diff = '\n'.join(all_diffs)

    if errors:
        return PatchResult(
            success=False,
            diff=combined_diff,
            files_modified=files_modified,
            files_created=files_created,
            files_deleted=files_deleted,
            lint=lint_results if lint_results else None,
            error="Apply phase failed (state may be inconsistent — run `git diff` to assess):\n"
                  + "\n".join(f"  • {e}" for e in errors),
        )

    return PatchResult(
        success=True,
        diff=combined_diff,
        files_modified=files_modified,
        files_created=files_created,
        files_deleted=files_deleted,
        lint=lint_results if lint_results else None,
    )


def _apply_add(op: PatchOperation, file_ops: Any) -> Tuple[bool, str]:
    """应用添加文件操作。"""
    # 从 hunk 中提取内容（所有 + 行）
    content_lines = []
    for hunk in op.hunks:
        for line in hunk.lines:
            if line.prefix == '+':
                content_lines.append(line.content)
    
    content = '\n'.join(content_lines)
    
    result = file_ops.write_file(op.file_path, content)
    if result.error:
        return False, result.error
    
    diff = f"--- /dev/null\n+++ b/{op.file_path}\n"
    diff += '\n'.join(f"+{line}" for line in content_lines)
    
    return True, diff


def _apply_delete(op: PatchOperation, file_ops: Any) -> Tuple[bool, str]:
    """应用删除文件操作。"""
    # 删除前先读取内容以便生成真正的 unified diff。
    # 验证阶段已确认文件存在；这里防止竞态条件。
    read_result = file_ops.read_file_raw(op.file_path)
    if read_result.error:
        return False, f"Cannot delete {op.file_path}: file not found"

    result = file_ops.delete_file(op.file_path)
    if result.error:
        return False, result.error

    removed_lines = read_result.content.splitlines(keepends=True)
    diff = ''.join(difflib.unified_diff(
        removed_lines, [],
        fromfile=f"a/{op.file_path}",
        tofile="/dev/null",
    ))
    return True, diff or f"# Deleted: {op.file_path}"


def _apply_move(op: PatchOperation, file_ops: Any) -> Tuple[bool, str]:
    """应用移动文件操作。"""
    result = file_ops.move_file(op.file_path, op.new_path)
    if result.error:
        return False, result.error

    diff = f"# Moved: {op.file_path} -> {op.new_path}"
    return True, diff


def _apply_update(op: PatchOperation, file_ops: Any) -> Tuple[bool, str]:
    """应用更新文件操作。"""
    # 延迟导入：打破 patch_parser <-> fuzzy_match 的循环依赖
    from tools.fuzzy_match import fuzzy_find_and_replace

    # 读取当前内容——raw 模式，无行号前缀或逐行截断
    read_result = file_ops.read_file_raw(op.file_path)

    if read_result.error:
        return False, f"Cannot read file: {read_result.error}"

    current_content = read_result.content

    # 应用每个 hunk
    new_content = current_content

    for hunk in op.hunks:
        # 从上下文行和删除行构建搜索模式
        search_lines = []
        replace_lines = []

        for line in hunk.lines:
            if line.prefix == ' ':
                search_lines.append(line.content)
                replace_lines.append(line.content)
            elif line.prefix == '-':
                search_lines.append(line.content)
            elif line.prefix == '+':
                replace_lines.append(line.content)

        if search_lines:
            search_pattern = '\n'.join(search_lines)
            replacement = '\n'.join(replace_lines)

            new_content, count, _strategy, error = fuzzy_find_and_replace(
                new_content, search_pattern, replacement, replace_all=False
            )

            if error and count == 0:
                # 如果有上下文提示，尝试使用上下文提示
                if hunk.context_hint:
                    # 查找上下文提示的位置并在附近搜索
                    hint_pos = new_content.find(hunk.context_hint)
                    if hint_pos != -1:
                        # 在提示位置附近的窗口中搜索
                        window_start = max(0, hint_pos - 500)
                        window_end = min(len(new_content), hint_pos + 2000)
                        window = new_content[window_start:window_end]

                        window_new, count, _strategy, error = fuzzy_find_and_replace(
                            window, search_pattern, replacement, replace_all=False
                        )
                        
                        if count > 0:
                            new_content = new_content[:window_start] + window_new + new_content[window_end:]
                            error = None
                
                if error:
                    return False, f"Could not apply hunk: {error}"
        else:
            # 纯添加 hunk（无上下文或删除行）。
            # 在上下文提示指示的位置插入，或在文件末尾插入。
            insert_text = '\n'.join(replace_lines)
            if hunk.context_hint:
                occurrences = _count_occurrences(new_content, hunk.context_hint)
                if occurrences == 0:
                    # 提示未找到——作为安全后备追加到末尾
                    new_content = new_content.rstrip('\n') + '\n' + insert_text + '\n'
                elif occurrences > 1:
                    return False, (
                        f"Addition-only hunk: context hint '{hunk.context_hint}' is ambiguous "
                        f"({occurrences} occurrences) — provide a more unique hint"
                    )
                else:
                    hint_pos = new_content.find(hunk.context_hint)
                    # 在包含上下文提示的行之后插入
                    eol = new_content.find('\n', hint_pos)
                    if eol != -1:
                        new_content = new_content[:eol + 1] + insert_text + '\n' + new_content[eol + 1:]
                    else:
                        new_content = new_content + '\n' + insert_text
            else:
                new_content = new_content.rstrip('\n') + '\n' + insert_text + '\n'
    
    # 写入新内容
    write_result = file_ops.write_file(op.file_path, new_content)
    if write_result.error:
        return False, write_result.error
    
    # 生成 diff
    diff_lines = difflib.unified_diff(
        current_content.splitlines(keepends=True),
        new_content.splitlines(keepends=True),
        fromfile=f"a/{op.file_path}",
        tofile=f"b/{op.file_path}"
    )
    diff = ''.join(diff_lines)
    
    return True, diff
