#!/usr/bin/env python3
"""工具包命名空间。

保持包导入的副作用最小化。导入 ``tools`` 时不应急切地导入整个工具栈，
因为在 ``hermes_cli.config`` 仍在初始化期间，若干子系统就会加载工具。

调用方应直接导入具体的子模块，例如：

    import tools.web_tools
    from tools import browser_tool

Python 会通过包路径解析这些子模块，无需在此处重新导出。
"""


def check_file_requirements():
    """文件工具仅需要终端后端可用即可。"""
    from .terminal_tool import check_terminal_requirements

    return check_terminal_requirements()


__all__ = ["check_file_requirements"]
