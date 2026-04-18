"""工具结果持久化的可配置预算常量。

可在 RL 环境层通过 HermesAgentEnvConfig 字段覆盖。
每个工具的解析优先级：固定值 > 配置覆盖 > 注册表 > 默认值。
"""

from dataclasses import dataclass, field
from typing import Dict

# 阈值永远不可被覆盖的工具。
# read_file=inf 防止出现无限的 persist->read->persist 循环。
PINNED_THRESHOLDS: Dict[str, float] = {
    "read_file": float("inf"),
}

# 与 tool_result_storage.py 中当前硬编码值匹配的默认值。
# 作为唯一的真实来源保存在此处；tool_result_storage.py 导入这些常量。
DEFAULT_RESULT_SIZE_CHARS: int = 100_000
DEFAULT_TURN_BUDGET_CHARS: int = 200_000
DEFAULT_PREVIEW_SIZE_CHARS: int = 1_500


@dataclass(frozen=True)
class BudgetConfig:
    """三层工具结果持久化系统的不可变预算常量。

    第 2 层（每结果）：resolve_threshold(tool_name) -> 以字符数为单位的阈值。
    第 3 层（每轮次）：turn_budget -> 单个助手轮次中所有工具结果的
                      总字符预算。
    预览：            preview_size -> 持久化后的内联预览片段大小。
    """

    default_result_size: int = DEFAULT_RESULT_SIZE_CHARS
    turn_budget: int = DEFAULT_TURN_BUDGET_CHARS
    preview_size: int = DEFAULT_PREVIEW_SIZE_CHARS
    tool_overrides: Dict[str, int] = field(default_factory=dict)

    def resolve_threshold(self, tool_name: str) -> int | float:
        """解析某个工具的持久化阈值。

        优先级：固定值 -> tool_overrides -> 注册表中的每工具设定 -> 默认值。
        """
        # 固定阈值的工具（如 read_file）不可被覆盖
        if tool_name in PINNED_THRESHOLDS:
            return PINNED_THRESHOLDS[tool_name]
        if tool_name in self.tool_overrides:
            return self.tool_overrides[tool_name]
        from tools.registry import registry
        return registry.get_max_result_size(tool_name, default=self.default_result_size)


# 默认配置——与当前硬编码行为完全一致。
DEFAULT_BUDGET = BudgetConfig()
