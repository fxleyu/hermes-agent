#!/usr/bin/env python3
"""
文件操作的模糊匹配模块

实现了一条多策略匹配链，以稳健地查找和替换文本，
适应 LLM 生成代码中常见的空白、缩进和转义差异。

8 策略链（灵感来自 OpenCode），按顺序尝试：
1. 精确匹配 - 直接字符串比较
2. 行级修剪 - 逐行去除首尾空白
3. 空白规范化 - 将多个空格/制表符折叠为单个空格
4. 缩进灵活 - 完全忽略缩进差异
5. 转义规范化 - 将 \\n 字面量转换为实际换行符
6. 边界修剪 - 仅修剪首行和末行的空白
7. 块锚定 - 匹配首行+末行，使用相似度比较中间部分
8. 上下文感知 - 50% 行相似度阈值

多次出现的匹配通过 replace_all 标志处理。

用法:
    from tools.fuzzy_match import fuzzy_find_and_replace

    new_content, match_count, strategy, error = fuzzy_find_and_replace(
        content="def foo():\\n    pass",
        old_string="def foo():",
        new_string="def bar():",
        replace_all=False
    )
"""

import re
from typing import Tuple, Optional, List, Callable
from difflib import SequenceMatcher

UNICODE_MAP = {
    "\u201c": '"', "\u201d": '"',  # 智能双引号
    "\u2018": "'", "\u2019": "'",  # 智能单引号
    "\u2014": "--", "\u2013": "-", # 长破折号/短破折号
    "\u2026": "...", "\u00a0": " ", # 省略号和不间断空格
}

def _unicode_normalize(text: str) -> str:
    """将 Unicode 字符规范化为对应的标准 ASCII 等价物。"""
    for char, repl in UNICODE_MAP.items():
        text = text.replace(char, repl)
    return text


def fuzzy_find_and_replace(content: str, old_string: str, new_string: str,
                            replace_all: bool = False) -> Tuple[str, int, Optional[str], Optional[str]]:
    """
    使用一系列逐渐宽松的模糊匹配策略查找并替换文本。

    参数:
        content: 要搜索的文件内容
        old_string: 要查找的文本
        new_string: 替换文本
        replace_all: 如果为 True，替换所有出现的位置；如果为 False，要求匹配唯一

    返回:
        元组 (new_content, match_count, strategy_name, error_message)
        - 成功: (修改后的内容, 替换次数, 使用的策略名, None)
        - 失败: (原始内容, 0, None, 错误描述)
    """
    if not old_string:
        return content, 0, None, "old_string cannot be empty"

    if old_string == new_string:
        return content, 0, None, "old_string and new_string are identical"

    # 按顺序尝试每种匹配策略
    strategies: List[Tuple[str, Callable]] = [
        ("exact", _strategy_exact),
        ("line_trimmed", _strategy_line_trimmed),
        ("whitespace_normalized", _strategy_whitespace_normalized),
        ("indentation_flexible", _strategy_indentation_flexible),
        ("escape_normalized", _strategy_escape_normalized),
        ("trimmed_boundary", _strategy_trimmed_boundary),
        ("unicode_normalized", _strategy_unicode_normalized),
        ("block_anchor", _strategy_block_anchor),
        ("context_aware", _strategy_context_aware),
    ]

    for strategy_name, strategy_fn in strategies:
        matches = strategy_fn(content, old_string)

        if matches:
            # 使用当前策略找到了匹配
            if len(matches) > 1 and not replace_all:
                return content, 0, None, (
                    f"Found {len(matches)} matches for old_string. "
                    f"Provide more context to make it unique, or use replace_all=True."
                )

            # 执行替换
            new_content = _apply_replacements(content, matches, new_string)
            return new_content, len(matches), strategy_name, None

    # 没有策略找到匹配
    return content, 0, None, "Could not find a match for old_string in the file"


def _apply_replacements(content: str, matches: List[Tuple[int, int]], new_string: str) -> str:
    """
    在指定位置应用替换。

    参数:
        content: 原始内容
        matches: 要替换的 (start, end) 位置列表
        new_string: 替换文本

    返回:
        应用替换后的内容
    """
    # 按位置降序排列匹配项，从末尾向前替换
    # 这样可以保持前面匹配项的位置不变
    sorted_matches = sorted(matches, key=lambda x: x[0], reverse=True)
    
    result = content
    for start, end in sorted_matches:
        result = result[:start] + new_string + result[end:]
    
    return result


# =============================================================================
# 匹配策略
# =============================================================================

def _strategy_exact(content: str, pattern: str) -> List[Tuple[int, int]]:
    """策略 1：精确字符串匹配。"""
    matches = []
    start = 0
    while True:
        pos = content.find(pattern, start)
        if pos == -1:
            break
        matches.append((pos, pos + len(pattern)))
        start = pos + 1
    return matches


def _strategy_line_trimmed(content: str, pattern: str) -> List[Tuple[int, int]]:
    """
    策略 2：逐行空白修剪匹配。

    匹配前去除每行的首尾空白。
    """
    # 通过修剪每行来规范化模式和内容
    pattern_lines = [line.strip() for line in pattern.split('\n')]
    pattern_normalized = '\n'.join(pattern_lines)
    
    content_lines = content.split('\n')
    content_normalized_lines = [line.strip() for line in content_lines]
    
    # 构建从规范化位置回到原始位置的映射
    return _find_normalized_matches(
        content, content_lines, content_normalized_lines,
        pattern, pattern_normalized
    )


def _strategy_whitespace_normalized(content: str, pattern: str) -> List[Tuple[int, int]]:
    """
    策略 3：将多个空白折叠为单个空格。
    """
    def normalize(s):
        # 将多个空格/制表符折叠为单个空格，保留换行符
        return re.sub(r'[ \t]+', ' ', s)
    
    pattern_normalized = normalize(pattern)
    content_normalized = normalize(content)
    
    # 在规范化内容中查找，然后映射回原始位置
    matches_in_normalized = _strategy_exact(content_normalized, pattern_normalized)
    
    if not matches_in_normalized:
        return []
    
    # 将位置映射回原始内容
    return _map_normalized_positions(content, content_normalized, matches_in_normalized)


def _strategy_indentation_flexible(content: str, pattern: str) -> List[Tuple[int, int]]:
    """
    策略 4：完全忽略缩进差异。

    匹配前去除所有行的前导空白。
    """
    content_lines = content.split('\n')
    content_stripped_lines = [line.lstrip() for line in content_lines]
    pattern_lines = [line.lstrip() for line in pattern.split('\n')]
    
    return _find_normalized_matches(
        content, content_lines, content_stripped_lines,
        pattern, '\n'.join(pattern_lines)
    )


def _strategy_escape_normalized(content: str, pattern: str) -> List[Tuple[int, int]]:
    """
    策略 5：将转义序列转换为实际字符。

    处理 \\n -> 换行, \\t -> 制表符等。
    """
    def unescape(s):
        # 转换常见的转义序列
        return s.replace('\\n', '\n').replace('\\t', '\t').replace('\\r', '\r')
    
    pattern_unescaped = unescape(pattern)
    
    if pattern_unescaped == pattern:
        # 没有需要转换的转义序列，跳过此策略
        return []
    
    return _strategy_exact(content, pattern_unescaped)


def _strategy_trimmed_boundary(content: str, pattern: str) -> List[Tuple[int, int]]:
    """
    策略 6：仅修剪首行和末行的空白。

    当模式边界存在空白差异时特别有用。
    """
    pattern_lines = pattern.split('\n')
    if not pattern_lines:
        return []
    
    # 仅修剪首行和末行
    pattern_lines[0] = pattern_lines[0].strip()
    if len(pattern_lines) > 1:
        pattern_lines[-1] = pattern_lines[-1].strip()
    
    modified_pattern = '\n'.join(pattern_lines)
    
    content_lines = content.split('\n')
    
    # 在内容中搜索匹配的块
    matches = []
    pattern_line_count = len(pattern_lines)
    
    for i in range(len(content_lines) - pattern_line_count + 1):
        block_lines = content_lines[i:i + pattern_line_count]
        
        # 修剪此块的首行和末行
        check_lines = block_lines.copy()
        check_lines[0] = check_lines[0].strip()
        if len(check_lines) > 1:
            check_lines[-1] = check_lines[-1].strip()
        
        if '\n'.join(check_lines) == modified_pattern:
            # 找到匹配 - 计算原始位置
            start_pos, end_pos = _calculate_line_positions(
                content_lines, i, i + pattern_line_count, len(content)
            )
            matches.append((start_pos, end_pos))
    
    return matches


def _build_orig_to_norm_map(original: str) -> List[int]:
    """构建一个将每个原始字符索引映射到其规范化索引的列表。

    因为 UNICODE_MAP 的替换可能会扩展字符（例如长破折号 -> '--'、
    省略号 -> '...'），规范化后的字符串可能比原始字符串更长。
    此映射让我们能够将规范化字符串中的位置转换回原始字符串中的
    对应位置。

    返回一个长度为 ``len(original) + 1`` 的列表；条目 ``i`` 是
    字符 ``i`` 映射到的规范化索引。
    """
    result: List[int] = []
    norm_pos = 0
    for char in original:
        result.append(norm_pos)
        repl = UNICODE_MAP.get(char)
        norm_pos += len(repl) if repl is not None else 1
    result.append(norm_pos)  # 哨兵值：最后一个字符之后的位置
    return result


def _map_positions_norm_to_orig(
    orig_to_norm: List[int],
    norm_matches: List[Tuple[int, int]],
) -> List[Tuple[int, int]]:
    """将规范化字符串中的 (start, end) 位置转换回原始字符串中的位置。"""
    # 反转映射：规范化位置 -> 第一个具有该规范化位置的原始位置
    norm_to_orig_start: dict[int, int] = {}
    for orig_pos, norm_pos in enumerate(orig_to_norm[:-1]):
        if norm_pos not in norm_to_orig_start:
            norm_to_orig_start[norm_pos] = orig_pos

    results: List[Tuple[int, int]] = []
    orig_len = len(orig_to_norm) - 1  # 原始字符的数量

    for norm_start, norm_end in norm_matches:
        if norm_start not in norm_to_orig_start:
            continue
        orig_start = norm_to_orig_start[norm_start]

        # 向前遍历直到 orig_to_norm[orig_end] >= norm_end
        orig_end = orig_start
        while orig_end < orig_len and orig_to_norm[orig_end] < norm_end:
            orig_end += 1

        results.append((orig_start, orig_end))

    return results


def _strategy_unicode_normalized(content: str, pattern: str) -> List[Tuple[int, int]]:
    """策略 7：Unicode 规范化。

    将智能引号、长/短破折号、省略号和不间断空格在 *content* 和 *pattern*
    中规范化为其 ASCII 等价物，然后在规范化副本上运行精确匹配和行级修剪匹配。

    通过 ``_build_orig_to_norm_map`` 将位置映射回*原始*字符串——这是
    必要的，因为某些 UNICODE_MAP 替换会将单个字符扩展为多个 ASCII 字符，
    直接复制位置会导致不正确的结果。
    """
    # 对两侧进行规范化。内容或模式（或两者）都可能包含 Unicode 变体——
    # 例如内容中有长破折号应匹配 LLM 的 ASCII '--'，反之亦然。
    # 仅当两者都未变化时才跳过。
    norm_pattern = _unicode_normalize(pattern)
    norm_content = _unicode_normalize(content)
    if norm_content == content and norm_pattern == pattern:
        return []

    norm_matches = _strategy_exact(norm_content, norm_pattern)
    if not norm_matches:
        norm_matches = _strategy_line_trimmed(norm_content, norm_pattern)

    if not norm_matches:
        return []

    orig_to_norm = _build_orig_to_norm_map(content)
    return _map_positions_norm_to_orig(orig_to_norm, norm_matches)


def _strategy_block_anchor(content: str, pattern: str) -> List[Tuple[int, int]]:
    """
    策略 8：通过锚定首行和末行进行匹配。
    使用宽松阈值和 Unicode 规范化进行调整。
    """
    # 对两个字符串进行规范化以用于比较，同时保留原始内容用于偏移量计算
    norm_pattern = _unicode_normalize(pattern)
    norm_content = _unicode_normalize(content)
    
    pattern_lines = norm_pattern.split('\n')
    if len(pattern_lines) < 2:
        return []
    
    first_line = pattern_lines[0].strip()
    last_line = pattern_lines[-1].strip()
    
    # 使用规范化的行进行匹配逻辑
    norm_content_lines = norm_content.split('\n')
    # 但使用原始行来计算 start/end 位置，以防止索引偏移
    orig_content_lines = content.split('\n')
    
    pattern_line_count = len(pattern_lines)
    
    potential_matches = []
    for i in range(len(norm_content_lines) - pattern_line_count + 1):
        if (norm_content_lines[i].strip() == first_line and 
            norm_content_lines[i + pattern_line_count - 1].strip() == last_line):
            potential_matches.append(i)
            
    matches = []
    candidate_count = len(potential_matches)
    
    # 阈值逻辑：唯一匹配使用 0.50，多个候选使用 0.70。
    # 之前的值（0.10 / 0.30）过于宽松——10% 的中间段相似度
    # 可能会匹配到完全不相关的代码块。
    threshold = 0.50 if candidate_count == 1 else 0.70

    for i in potential_matches:
        if pattern_line_count <= 2:
            similarity = 1.0
        else:
            # 比较规范化后的中间部分
            content_middle = '\n'.join(norm_content_lines[i+1:i+pattern_line_count-1])
            pattern_middle = '\n'.join(pattern_lines[1:-1])
            similarity = SequenceMatcher(None, content_middle, pattern_middle).ratio()
        
        if similarity >= threshold:
            # 使用原始行计算位置，以确保文件中的字符偏移量正确
            start_pos, end_pos = _calculate_line_positions(
                orig_content_lines, i, i + pattern_line_count, len(content)
            )
            matches.append((start_pos, end_pos))
    
    return matches


def _strategy_context_aware(content: str, pattern: str) -> List[Tuple[int, int]]:
    """
    策略 9：逐行相似度匹配，50% 阈值。

    查找至少 50% 的行具有高相似度的代码块。
    """
    pattern_lines = pattern.split('\n')
    content_lines = content.split('\n')
    
    if not pattern_lines:
        return []
    
    matches = []
    pattern_line_count = len(pattern_lines)
    
    for i in range(len(content_lines) - pattern_line_count + 1):
        block_lines = content_lines[i:i + pattern_line_count]
        
        # 计算逐行相似度
        high_similarity_count = 0
        for p_line, c_line in zip(pattern_lines, block_lines):
            sim = SequenceMatcher(None, p_line.strip(), c_line.strip()).ratio()
            if sim >= 0.80:
                high_similarity_count += 1
        
        # 需要至少 50% 的行具有高相似度
        if high_similarity_count >= len(pattern_lines) * 0.5:
            start_pos, end_pos = _calculate_line_positions(
                content_lines, i, i + pattern_line_count, len(content)
            )
            matches.append((start_pos, end_pos))
    
    return matches


# =============================================================================
# 辅助函数
# =============================================================================

def _calculate_line_positions(content_lines: List[str], start_line: int,
                              end_line: int, content_length: int) -> Tuple[int, int]:
    """从行索引计算起始和结束字符位置。

    参数:
        content_lines: 行列表（不含换行符）
        start_line: 起始行索引（从 0 开始）
        end_line: 结束行索引（不含，从 0 开始）
        content_length: 原始内容字符串的总长度

    返回:
        原始内容中的 (start_pos, end_pos) 元组
    """
    start_pos = sum(len(line) + 1 for line in content_lines[:start_line])
    end_pos = sum(len(line) + 1 for line in content_lines[:end_line]) - 1
    if end_pos >= content_length:
        end_pos = content_length
    return start_pos, end_pos


def _find_normalized_matches(content: str, content_lines: List[str],
                              content_normalized_lines: List[str],
                              pattern: str, pattern_normalized: str) -> List[Tuple[int, int]]:
    """
    在规范化内容中查找匹配并映射回原始位置。

    参数:
        content: 原始内容字符串
        content_lines: 原始内容按行分割
        content_normalized_lines: 规范化后的内容行
        pattern: 原始模式
        pattern_normalized: 规范化后的模式

    返回:
        原始内容中的 (start, end) 位置列表
    """
    pattern_norm_lines = pattern_normalized.split('\n')
    num_pattern_lines = len(pattern_norm_lines)
    
    matches = []
    
    for i in range(len(content_normalized_lines) - num_pattern_lines + 1):
        # 检查此块是否匹配
        block = '\n'.join(content_normalized_lines[i:i + num_pattern_lines])
        
        if block == pattern_normalized:
            # 找到匹配 - 计算原始位置
            start_pos, end_pos = _calculate_line_positions(
                content_lines, i, i + num_pattern_lines, len(content)
            )
            matches.append((start_pos, end_pos))
    
    return matches


def _map_normalized_positions(original: str, normalized: str,
                               normalized_matches: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
    """
    将规范化字符串中的位置映射回原始字符串。

    这是一种尽力而为的映射，适用于空白规范化场景。
    """
    if not normalized_matches:
        return []
    
    # 构建从规范化到原始的字符映射
    orig_to_norm = []  # orig_to_norm[i] = 规范化字符串中的位置
    
    orig_idx = 0
    norm_idx = 0
    
    while orig_idx < len(original) and norm_idx < len(normalized):
        if original[orig_idx] == normalized[norm_idx]:
            orig_to_norm.append(norm_idx)
            orig_idx += 1
            norm_idx += 1
        elif original[orig_idx] in ' \t' and normalized[norm_idx] == ' ':
            # 原始文本中有空格/制表符，规范化后折叠为一个空格
            orig_to_norm.append(norm_idx)
            orig_idx += 1
            # 暂不推进 norm_idx——等所有空白字符被消费完毕
            if orig_idx < len(original) and original[orig_idx] not in ' \t':
                norm_idx += 1
        elif original[orig_idx] in ' \t':
            # 原始文本中的多余空白
            orig_to_norm.append(norm_idx)
            orig_idx += 1
        else:
            # 不匹配——在我们的规范化下不应发生
            orig_to_norm.append(norm_idx)
            orig_idx += 1
    
    # 填充剩余部分
    while orig_idx < len(original):
        orig_to_norm.append(len(normalized))
        orig_idx += 1
    
    # 反向映射：对于每个规范化位置，找到原始范围
    norm_to_orig_start = {}
    norm_to_orig_end = {}
    
    for orig_pos, norm_pos in enumerate(orig_to_norm):
        if norm_pos not in norm_to_orig_start:
            norm_to_orig_start[norm_pos] = orig_pos
        norm_to_orig_end[norm_pos] = orig_pos
    
    # 映射匹配结果
    original_matches = []
    for norm_start, norm_end in normalized_matches:
        # 查找原始起始位置
        if norm_start in norm_to_orig_start:
            orig_start = norm_to_orig_start[norm_start]
        else:
            # 查找最近的位置
            orig_start = min(i for i, n in enumerate(orig_to_norm) if n >= norm_start)
        
        # 查找原始结束位置
        if norm_end - 1 in norm_to_orig_end:
            orig_end = norm_to_orig_end[norm_end - 1] + 1
        else:
            orig_end = orig_start + (norm_end - norm_start)
        
        # 扩展以包含被规范化的尾部空白
        while orig_end < len(original) and original[orig_end] in ' \t':
            orig_end += 1
        
        original_matches.append((orig_start, min(orig_end, len(original))))
    
    return original_matches
