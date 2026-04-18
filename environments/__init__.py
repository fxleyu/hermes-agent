"""
Hermes-Agent Atropos 环境模块

提供 hermes-agent 的工具调用能力与 Atropos 强化学习训练框架之间的分层集成。

核心层：
    - agent_loop: 可复用的多轮智能体循环，使用标准 OpenAI 规范的工具调用
    - tool_context: 每次 rollout 的工具访问句柄，供奖励/验证函数使用
    - hermes_base_env: Atropos 的抽象基础环境（BaseEnv 子类）
    - tool_call_parsers: 客户端工具调用解析器注册表，用于第二阶段（VLLM /generate）

具体环境：
    - terminal_test_env/: 用于测试栈的简单文件创建任务
    - hermes_swe_env/: 使用 Modal 沙箱的 SWE-bench 风格任务

基准测试（仅评估）：
    - benchmarks/terminalbench_2/: Terminal-Bench 2.0 评估
"""

try:
    from environments.agent_loop import AgentResult, HermesAgentLoop
    from environments.tool_context import ToolContext
    from environments.hermes_base_env import HermesAgentBaseEnv, HermesAgentEnvConfig
except ImportError:
    # atroposlib 未安装 — 环境模块不可用，但
    # 子模块（如 tool_call_parsers）仍可直接导入。
    pass

__all__ = [
    "AgentResult",
    "HermesAgentLoop",
    "ToolContext",
    "HermesAgentBaseEnv",
    "HermesAgentEnvConfig",
]
