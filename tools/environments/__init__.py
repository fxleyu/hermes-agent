"""Hermes 执行环境后端。

每个后端都实现了相同的接口（BaseEnvironment 抽象基类），用于在特定的执行上下文中运行
shell 命令：本地（local）、Docker、Singularity、SSH、Modal 或 Daytona。

terminal_tool.py 中的工厂函数（_create_environment）根据 TERMINAL_ENV 配置选择对应的后端。
"""

from tools.environments.base import BaseEnvironment

__all__ = ["BaseEnvironment"]
