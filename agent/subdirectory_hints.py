"""渐进式子目录提示发现。

当智能体通过工具调用（read_file、terminal、search_files 等）导航到子目录时，
此模块会从这些目录中发现并加载项目上下文文件
（AGENTS.md、CLAUDE.md、.cursorrules）。发现的提示会追加到工具结果中，
使模型在开始处理代码库新区域时获得相关上下文。

这是对 ``prompt_builder.py`` 中启动上下文加载的补充，后者仅从
当前工作目录加载。子目录提示是懒加载的，注入到对话中时不修改
系统提示词（以保持提示词缓存的有效性）。

灵感来源于 Block/goose 的 SubdirectoryHintTracker。
"""

import logging
import os
import shlex
from pathlib import Path
from typing import Dict, Any, Optional, Set

from agent.prompt_builder import _scan_context_content

logger = logging.getLogger(__name__)

# 在子目录中查找的上下文文件，按优先级排列。
# 文件名与 prompt_builder.py 相同，但我们加载所有找到的文件（非首个匹配优先），
# 因为不同的子目录可能使用不同的约定。
_HINT_FILENAMES = [
    "AGENTS.md", "agents.md",
    "CLAUDE.md", "claude.md",
    ".cursorrules",
]

# 每个提示文件的最大字符数，防止上下文膨胀
_MAX_HINT_CHARS = 8_000

# 工具参数中通常包含文件路径的键名
_PATH_ARG_KEYS = {"path", "file_path", "workdir"}

# 接受 shell 命令的工具，需要从中提取路径
_COMMAND_TOOLS = {"terminal"}

# 查找提示时向上遍历的最大父目录层数。
# 防止对深层嵌套路径一直扫描到根目录 /。
_MAX_ANCESTOR_WALK = 5

class SubdirectoryHintTracker:
    """跟踪智能体访问的目录，并在首次访问时加载提示文件。

    用法::

        tracker = SubdirectoryHintTracker(working_dir="/path/to/project")

        # 每次工具调用后：
        hints = tracker.check_tool_call("read_file", {"path": "backend/src/main.py"})
        if hints:
            tool_result += hints  # 追加到工具结果字符串
    """

    def __init__(self, working_dir: Optional[str] = None):
        self.working_dir = Path(working_dir or os.getcwd()).resolve()
        self._loaded_dirs: Set[Path] = set()
        # 预先标记工作目录为已加载（启动上下文会处理它）
        self._loaded_dirs.add(self.working_dir)

    def check_tool_call(
        self,
        tool_name: str,
        tool_args: Dict[str, Any],
    ) -> Optional[str]:
        """检查工具调用参数中的新目录，并加载任何提示文件。

        返回要追加到工具结果的格式化提示文本，或返回 None。
        """
        dirs = self._extract_directories(tool_name, tool_args)
        if not dirs:
            return None

        all_hints = []
        for d in dirs:
            hints = self._load_hints_for_directory(d)
            if hints:
                all_hints.append(hints)

        if not all_hints:
            return None

        return "\n\n" + "\n\n".join(all_hints)

    def _extract_directories(
        self, tool_name: str, args: Dict[str, Any]
    ) -> list:
        """从工具调用参数中提取目录路径。"""
        candidates: Set[Path] = set()

        # 直接路径参数
        for key in _PATH_ARG_KEYS:
            val = args.get(key)
            if isinstance(val, str) and val.strip():
                self._add_path_candidate(val, candidates)

        # Shell 命令——提取类路径的词元
        if tool_name in _COMMAND_TOOLS:
            cmd = args.get("command", "")
            if isinstance(cmd, str):
                self._extract_paths_from_command(cmd, candidates)

        return list(candidates)

    def _add_path_candidate(self, raw_path: str, candidates: Set[Path]):
        """解析原始路径并将其目录及祖先目录添加到候选集。

        从解析后的目录向上遍历到文件系统根目录，
        在遇到第一个已在 ``_loaded_dirs`` 中的目录时停止（或在
        ``_MAX_ANCESTOR_WALK`` 层后停止）。这确保读取
        ``project/src/main.py`` 时能发现 ``project/AGENTS.md``，
        即使 ``project/src/`` 本身没有提示文件。
        """
        try:
            p = Path(raw_path).expanduser()
            if not p.is_absolute():
                p = self.working_dir / p
            p = p.resolve()
            # 如果是文件路径（有扩展名或不作为目录存在），则使用其父目录
            if p.suffix or (p.exists() and p.is_file()):
                p = p.parent
            # 向上遍历祖先目录——在已加载的目录或根目录处停止
            for _ in range(_MAX_ANCESTOR_WALK):
                if p in self._loaded_dirs:
                    break
                if self._is_valid_subdir(p):
                    candidates.add(p)
                parent = p.parent
                if parent == p:
                    break  # 文件系统根目录
                p = parent
        except (OSError, ValueError):
            pass

    def _extract_paths_from_command(self, cmd: str, candidates: Set[Path]):
        """从 shell 命令字符串中提取类路径的词元。"""
        try:
            tokens = shlex.split(cmd)
        except ValueError:
            tokens = cmd.split()

        for token in tokens:
            # 跳过标志参数
            if token.startswith("-"):
                continue
            # 必须看起来像路径（包含 / 或 .）
            if "/" not in token and "." not in token:
                continue
            # 跳过 URL
            if token.startswith(("http://", "https://", "git@")):
                continue
            self._add_path_candidate(token, candidates)

    def _is_valid_subdir(self, path: Path) -> bool:
        """检查路径是否是可扫描提示文件的有效目录。"""
        try:
            if not path.is_dir():
                return False
        except OSError:
            return False
        if path in self._loaded_dirs:
            return False
        return True

    def _load_hints_for_directory(self, directory: Path) -> Optional[str]:
        """从目录加载提示文件。返回格式化文本或 None。"""
        # 标记此目录为已加载，避免重复处理
        self._loaded_dirs.add(directory)

        found_hints = []
        for filename in _HINT_FILENAMES:
            hint_path = directory / filename
            try:
                if not hint_path.is_file():
                    continue
            except OSError:
                continue
            try:
                content = hint_path.read_text(encoding="utf-8").strip()
                if not content:
                    continue
                # 与启动上下文加载相同的安全扫描
                content = _scan_context_content(content, filename)
                if len(content) > _MAX_HINT_CHARS:
                    content = (
                        content[:_MAX_HINT_CHARS]
                        + f"\n\n[...truncated {filename}: {len(content):,} chars total]"
                    )
                # 尽力获取相对路径用于显示
                rel_path = str(hint_path)
                try:
                    rel_path = str(hint_path.relative_to(self.working_dir))
                except ValueError:
                    try:
                        rel_path = str(hint_path.relative_to(Path.home()))
                        rel_path = "~/" + rel_path
                    except ValueError:
                        pass  # 保持绝对路径
                found_hints.append((rel_path, content))
                # 每个目录取第一个匹配（与启动加载一致）
                break
            except Exception as exc:
                logger.debug("Could not read %s: %s", hint_path, exc)

        if not found_hints:
            return None

        sections = []
        for rel_path, content in found_hints:
            sections.append(
                f"[Subdirectory context discovered: {rel_path}]\n{content}"
            )

        logger.debug(
            "Loaded subdirectory hints from %s: %s",
            directory,
            [h[0] for h in found_hints],
        )
        return "\n\n".join(sections)
