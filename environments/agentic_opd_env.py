"""
AgenticOPDEnv — 智能体工具调用任务的在线策略蒸馏
=====================================================================

首个在 ScoredDataGroup 上填充 distill_token_ids / distill_logprobs
字段的 Atropos 环境，实现在线策略蒸馏（OPD）训练。

核心思想（来自 OpenClaw-RL，普林斯顿 2026）：
  每次智能体收到下一状态信号（工具结果、错误追踪、测试判定），
  该信号包含关于智能体上一次回复如何改进的事后信息。本环境：

  1. 运行标准智能体 rollout（工具调用智能体循环）
  2. 遍历对话以找到（助手轮次，下一状态）对
  3. 使用 LLM 评判从下一状态信号中提取"提示"
  4. 构建增强提示（原始上下文 + 提示）
  5. 使用 VLLM 的 prompt_logprobs（通过 Atropos 的 get_logprobs API）
     在增强分布下评分学生的回复 tokens
  6. 将教师的 top-K 预测打包为 ScoredDataGroup 上的
     distill_token_ids / distill_logprobs

训练器随后计算每个 token 的优势：
  A_t = teacher_logprob(token_t) - student_logprob(token_t)
  正值 → 教师赞同此 token（增加权重）
  负值 → 教师不赞同（降低权重）

这从每次工具交互中提供密集的、token 级别的训练信号，
而非仅在轨迹末尾给出标量奖励。

任务：带测试验证的编程任务（来自测试结果、错误消息、终端输出的
丰富下一状态信号）。如果未配置 HuggingFace 数据集则回退到
内置编程问题。

要求：
  - VLLM 后端（server_type: vllm）— 需要用于 prompt logprob 评分
  - 第二阶段模式（ManagedServer）— 需要用于 token 级别跟踪

用法：
    # Process 模式（离线数据生成配合 OPD）
    python environments/agentic_opd_env.py process \\
        --env.total_steps 10 --env.group_size 2 \\
        --env.data_path_to_save_groups output.jsonl \\
        --openai.base_url http://localhost:8000/v1 \\
        --openai.model_name Qwen/Qwen3-4B

    # Serve 模式（连接到 Atropos 训练器）
    python environments/agentic_opd_env.py serve \\
        --openai.base_url http://localhost:8000/v1 \\
        --openai.model_name Qwen/Qwen3-4B

    # 评估模式
    python environments/agentic_opd_env.py evaluate \\
        --env.eval_size 10 \\
        --openai.base_url http://localhost:8000/v1 \\
        --openai.model_name Qwen/Qwen3-4B

参考文献：Wang et al., "OpenClaw-RL: Train Any Agent Simply by Talking"
           arXiv:2603.10165, March 2026
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
import random
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from pydantic import Field

# 确保 hermes-agent 根目录在路径中
_repo_root = Path(__file__).resolve().parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from atroposlib.envs.base import ScoredDataGroup, ScoredDataItem
from atroposlib.envs.server_handling.server_manager import APIServerConfig
from atroposlib.type_definitions import Item

from environments.hermes_base_env import HermesAgentBaseEnv, HermesAgentEnvConfig
from environments.agent_loop import AgentResult, HermesAgentLoop
from environments.tool_context import ToolContext

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════
# 内置编程任务（未配置 HF 数据集时的回退方案）
# ═══════════════════════════════════════════════════════════════════════

BUILTIN_CODING_TASKS = [
    {
        "task": "Write a Python function `fizzbuzz(n)` that returns a list of strings from 1 to n. "
        "For multiples of 3 return 'Fizz', for multiples of 5 return 'Buzz', "
        "for multiples of both return 'FizzBuzz', otherwise the number as a string.",
        "test_code": (
            "from solution import fizzbuzz\n"
            "assert fizzbuzz(15) == ['1','2','Fizz','4','Buzz','Fizz','7','8','Fizz','Buzz','11','Fizz','13','14','FizzBuzz']\n"
            "assert fizzbuzz(1) == ['1']\n"
            "assert fizzbuzz(0) == []\n"
            "print('All tests passed!')\n"
        ),
        "difficulty": "easy",
    },
    {
        "task": "Write a Python function `is_palindrome(s)` that checks if a string is a palindrome, "
        "ignoring case and non-alphanumeric characters. Return True or False.",
        "test_code": (
            "from solution import is_palindrome\n"
            "assert is_palindrome('A man, a plan, a canal: Panama') == True\n"
            "assert is_palindrome('race a car') == False\n"
            "assert is_palindrome('') == True\n"
            "assert is_palindrome('Was it a car or a cat I saw?') == True\n"
            "print('All tests passed!')\n"
        ),
        "difficulty": "easy",
    },
    {
        "task": "Write a Python function `two_sum(nums, target)` that returns the indices of the two "
        "numbers in `nums` that add up to `target`. Assume exactly one solution exists. "
        "Return a list of two indices [i, j] where i < j.",
        "test_code": (
            "from solution import two_sum\n"
            "assert two_sum([2, 7, 11, 15], 9) == [0, 1]\n"
            "assert two_sum([3, 2, 4], 6) == [1, 2]\n"
            "assert two_sum([3, 3], 6) == [0, 1]\n"
            "print('All tests passed!')\n"
        ),
        "difficulty": "easy",
    },
    {
        "task": "Write a Python function `flatten(lst)` that takes an arbitrarily nested list and "
        "returns a flat list of all elements. For example, flatten([1, [2, [3, 4], 5]]) "
        "should return [1, 2, 3, 4, 5].",
        "test_code": (
            "from solution import flatten\n"
            "assert flatten([1, [2, [3, 4], 5]]) == [1, 2, 3, 4, 5]\n"
            "assert flatten([]) == []\n"
            "assert flatten([1, 2, 3]) == [1, 2, 3]\n"
            "assert flatten([[[[1]]]]) == [1]\n"
            "assert flatten([1, [2], [[3]], [[[4]]]]) == [1, 2, 3, 4]\n"
            "print('All tests passed!')\n"
        ),
        "difficulty": "medium",
    },
    {
        "task": "Write a Python function `longest_common_prefix(strs)` that finds the longest "
        "common prefix string amongst a list of strings. If there is no common prefix, "
        "return an empty string.",
        "test_code": (
            "from solution import longest_common_prefix\n"
            "assert longest_common_prefix(['flower', 'flow', 'flight']) == 'fl'\n"
            "assert longest_common_prefix(['dog', 'racecar', 'car']) == ''\n"
            "assert longest_common_prefix(['interspecies', 'interstellar', 'interstate']) == 'inters'\n"
            "assert longest_common_prefix(['a']) == 'a'\n"
            "assert longest_common_prefix([]) == ''\n"
            "print('All tests passed!')\n"
        ),
        "difficulty": "easy",
    },
    {
        "task": "Write a Python function `group_anagrams(strs)` that groups anagrams together. "
        "Return a list of lists, where each inner list contains strings that are anagrams of "
        "each other. The order of groups and strings within groups does not matter.",
        "test_code": (
            "from solution import group_anagrams\n"
            "result = group_anagrams(['eat', 'tea', 'tan', 'ate', 'nat', 'bat'])\n"
            "result_sorted = sorted([sorted(g) for g in result])\n"
            "assert result_sorted == [['ate', 'eat', 'tea'], ['bat'], ['nat', 'tan']]\n"
            "assert group_anagrams([]) == []\n"
            "assert group_anagrams(['a']) == [['a']]\n"
            "print('All tests passed!')\n"
        ),
        "difficulty": "medium",
    },
    {
        "task": "Write a Python function `valid_parentheses(s)` that determines if a string "
        "containing just '(', ')', '{', '}', '[' and ']' is valid. A string is valid if "
        "open brackets are closed by the same type and in the correct order.",
        "test_code": (
            "from solution import valid_parentheses\n"
            "assert valid_parentheses('()') == True\n"
            "assert valid_parentheses('()[]{}') == True\n"
            "assert valid_parentheses('(]') == False\n"
            "assert valid_parentheses('([)]') == False\n"
            "assert valid_parentheses('{[]}') == True\n"
            "assert valid_parentheses('') == True\n"
            "print('All tests passed!')\n"
        ),
        "difficulty": "easy",
    },
    {
        "task": "Write a Python function `merge_intervals(intervals)` that merges overlapping "
        "intervals. Each interval is a list [start, end]. Return the merged intervals sorted "
        "by start time.",
        "test_code": (
            "from solution import merge_intervals\n"
            "assert merge_intervals([[1,3],[2,6],[8,10],[15,18]]) == [[1,6],[8,10],[15,18]]\n"
            "assert merge_intervals([[1,4],[4,5]]) == [[1,5]]\n"
            "assert merge_intervals([[1,4],[0,4]]) == [[0,4]]\n"
            "assert merge_intervals([]) == []\n"
            "assert merge_intervals([[1,2]]) == [[1,2]]\n"
            "print('All tests passed!')\n"
        ),
        "difficulty": "medium",
    },
]


# ═══════════════════════════════════════════════════════════════════════
# 提示提取的 prompt（改编自 OpenClaw-RL）
# ═══════════════════════════════════════════════════════════════════════

_HINT_JUDGE_SYSTEM = (
    "You are a process reward model used for hindsight hint extraction.\n"
    "You are given:\n"
    "1) The assistant response at turn t.\n"
    "2) The next state at turn t+1, along with its **role**.\n\n"
    "## Understanding the next state's role\n"
    "- role='user': A reply from the user (follow-up, correction, new request, etc.).\n"
    "- role='tool': The return value of a tool the assistant invoked. "
    "This content was NOT available before the assistant's action — "
    "it exists BECAUSE the assistant called the tool. "
    "A successful, non-error tool output generally means the assistant's "
    "action was appropriate; do NOT treat it as information the assistant "
    "should have already known.\n\n"
    "Your goal is to decide whether the next state reveals useful hindsight information\n"
    "that could have helped improve the assistant response at turn t.\n\n"
    "Output format rules (strict):\n"
    "- You MUST include exactly one final decision token: \\boxed{1} or \\boxed{-1}.\n"
    "- If and only if decision is \\boxed{1}, provide a concise, information-dense hint in 1-3 sentences,\n"
    "  wrapped between [HINT_START] and [HINT_END].\n"
    "- If decision is \\boxed{-1}, do not provide a hint block.\n"
    "- Hint must be concrete and actionable for improving the previous response."
)

_BOXED_RE = re.compile(r"\\boxed\{(-?\d+)\}")
_HINT_RE = re.compile(r"\[HINT_START\](.*?)\[HINT_END\]", re.DOTALL)


def _build_hint_judge_messages(
    response_text: str, next_state_text: str, next_state_role: str = "tool"
) -> list[dict]:
    """构建提示提取评判所需的消息。"""
    user = (
        f"## Assistant response (turn t)\n{response_text}\n\n"
        f"## Next state (turn t+1) [role: {next_state_role}]\n{next_state_text}\n\n"
        "Now output your decision and (if positive) the hint in the required format."
    )
    return [
        {"role": "system", "content": _HINT_JUDGE_SYSTEM},
        {"role": "user", "content": user},
    ]


def _parse_hint_result(text: str) -> tuple[int | None, str]:
    """解析评判的 boxed 决定和提示文本。"""
    boxed = _BOXED_RE.findall(text)
    score = int(boxed[-1]) if boxed else None
    if score not in (1, -1):
        score = None
    hint_matches = _HINT_RE.findall(text)
    hint = hint_matches[-1].strip() if hint_matches else ""
    return score, hint


def _select_best_hint(votes: list[dict]) -> dict | None:
    """从多数投票的评判结果中选择最佳提示。"""
    good = [
        v
        for v in votes
        if v.get("score") == 1
        and isinstance(v.get("hint"), str)
        and len(v["hint"].strip()) > 10
    ]
    if not good:
        return None
    return max(good, key=lambda v: len(v["hint"].strip()))


def _append_hint_to_messages(messages: list[dict], hint: str) -> list[dict]:
    """克隆消息并将提示追加到最后一条用户消息中。"""
    cloned = copy.deepcopy(messages)
    if not cloned:
        return [{"role": "user", "content": f"[user's hint / instruction]\n{hint}"}]

    # 找到最后一条用户消息
    target_idx = None
    for i in range(len(cloned) - 1, -1, -1):
        if cloned[i].get("role") == "user":
            target_idx = i
            break
    if target_idx is None:
        target_idx = len(cloned) - 1

    content = cloned[target_idx].get("content", "")
    if isinstance(content, list):
        content = " ".join(
            c.get("text", "") if isinstance(c, dict) else str(c) for c in content
        )
    suffix = f"\n\n[user's hint / instruction]\n{hint.strip()}"
    cloned[target_idx]["content"] = (content + suffix).strip()
    return cloned


# ═══════════════════════════════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════════════════════════════


class AgenticOPDConfig(HermesAgentEnvConfig):
    """智能体 OPD 环境的配置。"""

    # --- OPD 设置 ---
    opd_enabled: bool = Field(
        default=True,
        description="Enable on-policy distillation pipeline. When disabled, "
        "the environment behaves like a standard agentic env (no distill fields).",
    )
    distill_topk: int = Field(
        default=50,
        description="Number of top-K teacher logprobs per position for distillation.",
    )
    prm_votes: int = Field(
        default=3,
        description="Number of independent judge queries for majority-voted hint extraction.",
    )
    hint_max_next_state_chars: int = Field(
        default=4000,
        description="Maximum characters of next-state text to include in the hint judge prompt. "
        "Tool results can be very long — truncating prevents judge context overflow.",
    )

    # --- 奖励设置 ---
    correctness_weight: float = Field(
        default=0.7,
        description="Weight for test pass/fail in reward.",
    )
    efficiency_weight: float = Field(
        default=0.15,
        description="Weight for efficiency (fewer turns = better).",
    )
    tool_usage_weight: float = Field(
        default=0.15,
        description="Weight for appropriate tool usage signal.",
    )

    # --- 数据集 ---

    # --- 评估 ---
    eval_size: int = Field(
        default=10,
        description="Number of held-out items for evaluation.",
    )
    eval_split_ratio: float = Field(
        default=0.15,
        description="Fraction of dataset to hold out for evaluation.",
    )


# ═══════════════════════════════════════════════════════════════════════
# 环境
# ═══════════════════════════════════════════════════════════════════════


class AgenticOPDEnv(HermesAgentBaseEnv):
    """
    带有来自下一状态信号的在线策略蒸馏的强化学习环境。

    运行编程任务，智能体编写代码并运行测试。
    工具结果（测试通过/失败、错误追踪）作为下一状态信号
    用于提示提取和教师 logprob 评分。

    这是首个在 ScoredDataGroup 上填充 distill_token_ids
    和 distill_logprobs 的 Atropos 环境，用于 OPD 训练。
    """

    name = "agentic-opd"
    env_config_cls = AgenticOPDConfig

    # 默认工具集：终端用于运行代码，文件用于写代码
    default_toolsets = ["terminal", "file"]

    @classmethod
    def config_init(cls) -> Tuple[AgenticOPDConfig, List[APIServerConfig]]:
        """默认配置。"""
        env_config = AgenticOPDConfig(
            # Toolsets
            enabled_toolsets=["terminal", "file"],
            # Agent loop
            max_agent_turns=15,
            agent_temperature=1.0,
            system_prompt=(
                "You are a skilled Python programmer. When given a coding task:\n"
                "1. Write the solution to a file called 'solution.py'\n"
                "2. Write the test code to a file called 'test_solution.py'\n"
                "3. Run the tests with: python test_solution.py\n"
                "4. If tests fail, read the error output carefully, fix your code, and re-run\n"
                "5. Once all tests pass, report success\n\n"
                "Be efficient — write clean code and fix errors methodically."
            ),
            # OPD
            opd_enabled=True,
            distill_topk=50,
            prm_votes=3,
            # Training
            group_size=4,
            total_steps=500,
            steps_per_eval=50,
            use_wandb=True,
            wandb_name="agentic-opd",
        )

        server_configs = [
            APIServerConfig(
                base_url="http://localhost:8000/v1",
                model_name="Qwen/Qwen3-4B",
                server_type="vllm",
            )
        ]

        return env_config, server_configs

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._items: list[dict] = []
        self._eval_items: list[dict] = []
        self._index: int = 0

        # 指标缓冲区
        self._reward_buffer: list[float] = []
        self._correctness_buffer: list[float] = []
        self._efficiency_buffer: list[float] = []
        self._tool_usage_buffer: list[float] = []
        self._hints_extracted_buffer: list[int] = []
        self._opd_turns_scored_buffer: list[int] = []

    # ═══════════════════════════════════════════════════════════════════
    # 1. setup — 加载数据集
    # ═══════════════════════════════════════════════════════════════════

    async def setup(self) -> None:
        """从 HuggingFace 加载编程任务或使用内置集。"""
        if self.config.dataset_name:
            try:
                from datasets import load_dataset

                logger.info(
                    "Loading dataset '%s'...", self.config.dataset_name
                )
                ds = load_dataset(
                    self.config.dataset_name, split=self.config.dataset_split
                )
                task_field = self.config.prompt_field
                self._items = [
                    {
                        "task": row.get(task_field, row.get("task", "")),
                        "test_code": row.get("test_code", row.get("tests", "")),
                        "difficulty": row.get("difficulty", "unknown"),
                    }
                    for row in ds
                    if row.get(task_field, row.get("task", ""))
                ]
                if self._items:
                    random.shuffle(self._items)
                    eval_size = max(
                        self.config.eval_size,
                        int(len(self._items) * self.config.eval_split_ratio),
                    )
                    self._eval_items = self._items[:eval_size]
                    self._items = self._items[eval_size:]
                    logger.info(
                        "Loaded %d train / %d eval items from '%s'",
                        len(self._items),
                        len(self._eval_items),
                        self.config.dataset_name,
                    )
                    return
            except Exception as e:
                logger.warning(
                    "Could not load dataset '%s': %s. Using built-in tasks.",
                    self.config.dataset_name,
                    e,
                )

        # 回退到内置任务
        items = copy.deepcopy(BUILTIN_CODING_TASKS)
        random.shuffle(items)
        split = max(1, len(items) * 85 // 100)
        self._items = items[:split]
        self._eval_items = items[split:]
        logger.info(
            "Using built-in coding tasks: %d train / %d eval items",
            len(self._items),
            len(self._eval_items),
        )

    # ═══════════════════════════════════════════════════════════════════
    # 2. get_next_item
    # ═══════════════════════════════════════════════════════════════════

    async def get_next_item(self) -> dict:
        """返回下一个编程任务，循环遍历数据集。"""
        if not self._items:
            raise RuntimeError("Dataset is empty. Did you call setup()?")
        item = self._items[self._index % len(self._items)]
        self._index += 1
        return item

    # ═══════════════════════════════════════════════════════════════════
    # 3. format_prompt
    # ═══════════════════════════════════════════════════════════════════

    def format_prompt(self, item: dict) -> str:
        """将编程任务格式化为用户提示。"""
        prompt = (
            f"Solve the following coding task.\n\n"
            f"## Task\n{item['task']}\n\n"
        )
        if item.get("test_code"):
            prompt += (
                f"## Tests\nThe following test code will be used to verify your solution:\n"
                f"```python\n{item['test_code']}```\n\n"
            )
        prompt += (
            "## Instructions\n"
            "1. Write your solution to `solution.py`\n"
            "2. Write the test code to `test_solution.py`\n"
            "3. Run `python test_solution.py` to verify\n"
            "4. Fix any failures and re-run until all tests pass\n"
        )
        return prompt

    # ═══════════════════════════════════════════════════════════════════
    # 4. compute_reward
    # ═══════════════════════════════════════════════════════════════════

    async def compute_reward(
        self,
        item: dict,
        result: AgentResult,
        ctx: ToolContext,
    ) -> float:
        """
        多信号奖励：
          - 正确性 (0.7)：测试是否通过？
          - 效率 (0.15)：更少轮次 = 更好
          - 工具使用 (0.15)：智能体是否实际编写并运行了代码？
        """
        cfg = self.config

        # ---- 信号 1：测试正确性 ----
        # 检查 test_solution.py 是否存在并在智能体的沙箱中通过
        correctness = 0.0
        try:
            test_result = ctx.terminal("python test_solution.py 2>&1", timeout=30)
            output = test_result.get("output", "")
            exit_code = test_result.get("exit_code", 1)
            if exit_code == 0 and "passed" in output.lower():
                correctness = 1.0
            elif exit_code == 0:
                correctness = 0.8  # 运行无错误但没有显式的 "passed"
            elif "assert" in output.lower() and "error" in output.lower():
                correctness = 0.2  # 部分正确 — 代码运行但断言失败
            else:
                correctness = 0.1  # 代码完全报错
        except Exception as e:
            logger.debug("Test execution failed in reward: %s", e)
            correctness = 0.0

        # ---- 信号 2：效率 ----
        max_turns = cfg.max_agent_turns
        turns_used = result.turns_used
        if turns_used <= 3:
            efficiency = 1.0
        elif turns_used <= max_turns // 2:
            efficiency = 0.8
        elif turns_used <= max_turns * 3 // 4:
            efficiency = 0.5
        else:
            efficiency = 0.2

        # ---- 信号 3：工具使用 ----
        tools_used = set()
        for msg in result.messages:
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                for tc in msg["tool_calls"]:
                    fn = tc.get("function", {}) if isinstance(tc, dict) else {}
                    name = fn.get("name", "")
                    if name:
                        tools_used.add(name)

        # 好的：同时使用了终端和文件工具
        if "terminal" in tools_used and ("write_file" in tools_used or "patch" in tools_used):
            tool_usage = 1.0
        elif "terminal" in tools_used:
            tool_usage = 0.6
        elif tools_used:
            tool_usage = 0.3
        else:
            tool_usage = 0.0

        # ---- 综合 ----
        reward = (
            cfg.correctness_weight * correctness
            + cfg.efficiency_weight * efficiency
            + cfg.tool_usage_weight * tool_usage
        )
        reward = min(1.0, max(0.0, reward))

        # 跟踪指标
        self._reward_buffer.append(reward)
        self._correctness_buffer.append(correctness)
        self._efficiency_buffer.append(efficiency)
        self._tool_usage_buffer.append(tool_usage)

        logger.debug(
            "Reward: correctness=%.2f, efficiency=%.2f, tool_usage=%.2f → %.3f",
            correctness,
            efficiency,
            tool_usage,
            reward,
        )
        return reward

    # ═══════════════════════════════════════════════════════════════════
    # 5. collect_trajectories — OPD 管道
    # ═══════════════════════════════════════════════════════════════════

    async def collect_trajectories(
        self, item: Item
    ) -> Tuple[
        Union[Optional[ScoredDataGroup], List[Optional[ScoredDataGroup]]],
        List[Item],
    ]:
        """
        重写 collect_trajectories 以添加 OPD 管道。

        1. 通过 super() 运行标准 rollout → 带 tokens/masks/scores 的 ScoredDataGroup
        2. 对每个 rollout，从下一状态信号中提取提示
        3. 在增强（提示增强）分布下评分学生 tokens
        4. 将 distill_token_ids / distill_logprobs 添加到 ScoredDataGroup
        """
        # 步骤 1：运行标准 rollout
        scored_group, backlog = await super().collect_trajectories(item)

        # 步骤 2：OPD 管道（仅当启用且有 VLLM 服务器时）
        if (
            self.config.opd_enabled
            and scored_group is not None
            and isinstance(scored_group, dict)
            and self._use_managed_server()
        ):
            await self._apply_opd_pipeline(scored_group)

        return scored_group, backlog

    async def _apply_opd_pipeline(self, group: ScoredDataGroup) -> None:
        """
        对组中的每个 rollout 应用在线策略蒸馏。

        对每个 rollout 的消息：
        1. 找到（助手，下一状态）轮次对
        2. 通过多数投票的 LLM 评判提取提示
        3. 构建增强提示（原始 + 提示）
        4. 通过 get_logprobs 在增强分布下评分学生 tokens
        5. 将 distill_token_ids / distill_logprobs 添加到组中
        """
        messages_list = group.get("messages", [])
        tokens_list = group.get("tokens", [])

        if not messages_list or not tokens_list:
            logger.debug("OPD: No messages or tokens to process")
            return

        all_distill_token_ids: List[Optional[List[List[int]]]] = []
        all_distill_logprobs: List[Optional[List[List[float]]]] = []

        for seq_idx, (messages, student_tokens) in enumerate(
            zip(messages_list, tokens_list)
        ):
            try:
                distill_ids, distill_lps = await self._opd_for_sequence(
                    messages, student_tokens
                )
                all_distill_token_ids.append(distill_ids)
                all_distill_logprobs.append(distill_lps)
            except Exception as e:
                logger.warning(
                    "OPD failed for sequence %d: %s", seq_idx, e
                )
                all_distill_token_ids.append(None)
                all_distill_logprobs.append(None)

        # 仅当至少一个序列成功时设置蒸馏字段
        any_succeeded = any(d is not None for d in all_distill_token_ids)
        if any_succeeded:
            # 用零填充数组替换 None 条目，匹配 token 长度
            for i in range(len(all_distill_token_ids)):
                if all_distill_token_ids[i] is None and i < len(tokens_list):
                    seq_len = len(tokens_list[i])
                    k = self.config.distill_topk
                    all_distill_token_ids[i] = [[0] * k] * seq_len
                    all_distill_logprobs[i] = [[0.0] * k] * seq_len

            group["distill_token_ids"] = all_distill_token_ids
            group["distill_logprobs"] = all_distill_logprobs
            logger.info(
                "OPD: Set distill fields on %d/%d sequences",
                sum(1 for d in all_distill_token_ids if d is not None),
                len(all_distill_token_ids),
            )

    async def _opd_for_sequence(
        self, messages: List[Dict], student_tokens: List[int]
    ) -> Tuple[List[List[int]], List[List[float]]]:
        """
        对单个 rollout 序列运行 OPD。

        1. 遍历对话以找到（助手，下一状态）对
        2. 从下一状态信号中提取提示
        3. 对每个提示增强的轮次，通过 get_logprobs 评分学生 tokens
        4. 将每轮教师 logprobs 合并到完整序列的蒸馏数组中

        返回：
            (distill_token_ids, distill_logprobs) 各形状为 [seq_len][top_k]
        """
        k = self.config.distill_topk
        seq_len = len(student_tokens)

        # 用零初始化（无蒸馏信息 = 中性）
        distill_token_ids: List[List[int]] = [[0] * k for _ in range(seq_len)]
        distill_logprobs: List[List[float]] = [[0.0] * k for _ in range(seq_len)]

        # 找到（助手，下一状态）轮次对
        turn_pairs = self._extract_turn_pairs(messages)
        if not turn_pairs:
            return distill_token_ids, distill_logprobs

        hints_extracted = 0
        turns_scored = 0

        for pair in turn_pairs:
            try:
                hint = await self._extract_hint(
                    pair["assistant_text"],
                    pair["next_state_text"],
                    pair["next_state_role"],
                )
                if not hint:
                    continue

                hints_extracted += 1

                # 用提示构建增强提示
                enhanced_messages = _append_hint_to_messages(
                    pair["context_messages"], hint
                )

                # 将增强提示分词
                if not self.tokenizer:
                    logger.warning("OPD: No tokenizer available, skipping scoring")
                    continue

                enhanced_prompt = self.tokenizer.apply_chat_template(
                    enhanced_messages,
                    tokenize=False,
                    add_generation_prompt=True,
                )

                # 将要评分的助手回复分词
                response_text = pair["assistant_text"]
                enhanced_full_text = enhanced_prompt + response_text
                enhanced_ids = self.tokenizer(
                    enhanced_full_text, add_special_tokens=False
                )["input_ids"]

                response_ids = self.tokenizer(
                    response_text, add_special_tokens=False
                )["input_ids"]
                response_len = len(response_ids)

                if response_len == 0:
                    continue

                # 通过 get_logprobs 评分 — 在增强（提示增强）分布下
                # 评分学生 tokens
                try:
                    logprob_result = await self.server.get_logprobs(
                        input_ids=enhanced_ids,
                        top_k=k,
                        split="eval",  # Use eval semaphore to not block training
                    )
                except Exception as e:
                    logger.debug("get_logprobs failed: %s", e)
                    continue

                teacher_topk_ids = logprob_result.get("prompt_topk_token_ids", [])
                teacher_topk_lps = logprob_result.get("prompt_topk_logprobs", [])

                if not teacher_topk_ids:
                    continue

                # 仅提取回复位置（最后 response_len 个条目）
                if len(teacher_topk_ids) >= response_len:
                    resp_topk_ids = teacher_topk_ids[-response_len:]
                    resp_topk_lps = teacher_topk_lps[-response_len:]
                else:
                    # 如果回复比预期短则左侧填充
                    pad_len = response_len - len(teacher_topk_ids)
                    resp_topk_ids = [[0] * k] * pad_len + teacher_topk_ids
                    resp_topk_lps = [[0.0] * k] * pad_len + teacher_topk_lps

                # 将这些映射回学生完整序列中的位置
                # 找到此助手轮次 tokens 在完整序列中出现的位置
                turn_start = self._find_token_span(
                    student_tokens, response_ids
                )
                if turn_start is not None:
                    for j in range(min(response_len, seq_len - turn_start)):
                        pos = turn_start + j
                        if pos < seq_len and j < len(resp_topk_ids):
                            # 填充/截断到恰好 k 个条目
                            ids = resp_topk_ids[j][:k]
                            lps = resp_topk_lps[j][:k]
                            while len(ids) < k:
                                ids.append(0)
                                lps.append(0.0)
                            distill_token_ids[pos] = ids
                            distill_logprobs[pos] = lps
                    turns_scored += 1

            except Exception as e:
                logger.debug("OPD turn processing failed: %s", e)
                continue

        # 跟踪 OPD 指标
        self._hints_extracted_buffer.append(hints_extracted)
        self._opd_turns_scored_buffer.append(turns_scored)

        logger.debug(
            "OPD sequence: %d turn pairs, %d hints extracted, %d turns scored",
            len(turn_pairs),
            hints_extracted,
            turns_scored,
        )
        return distill_token_ids, distill_logprobs

    def _extract_turn_pairs(
        self, messages: List[Dict]
    ) -> List[Dict[str, Any]]:
        """
        遍历对话消息以找到（助手，下一状态）对。

        "轮次对"是一条有内容的助手消息（回复），
        后面跟着一个或多个工具结果或用户回复（下一状态）。

        返回字典列表：
          {
            "context_messages": 助手轮次之前（不包括）的消息,
            "assistant_text": 助手的回复文本,
            "next_state_text": 下一状态内容（工具结果或用户回复）,
            "next_state_role": "tool" 或 "user",
          }
        """
        pairs = []
        i = 0
        while i < len(messages):
            msg = messages[i]
            if msg.get("role") == "assistant" and msg.get("content"):
                # 找到一条有内容的助手消息
                assistant_text = msg["content"]
                context = messages[:i]  # 此轮次之前的所有消息

                # 向前查找下一状态
                j = i + 1
                # 跳过仅有 tool_calls 的助手消息并收集工具结果
                next_states = []
                while j < len(messages):
                    next_msg = messages[j]
                    if next_msg.get("role") == "tool":
                        next_states.append(next_msg)
                        j += 1
                    elif next_msg.get("role") == "user":
                        next_states.append(next_msg)
                        break
                    else:
                        break

                if next_states:
                    # 合并所有下一状态内容
                    next_text_parts = []
                    next_role = next_states[0].get("role", "tool")
                    for ns in next_states:
                        content = ns.get("content", "")
                        if content:
                            # 截断过长的工具输出
                            max_chars = self.config.hint_max_next_state_chars
                            if len(content) > max_chars:
                                content = content[:max_chars] + "\n...[truncated]"
                            next_text_parts.append(content)

                    next_text = "\n---\n".join(next_text_parts)
                    if next_text.strip():
                        pairs.append(
                            {
                                "context_messages": context,
                                "assistant_text": assistant_text,
                                "next_state_text": next_text,
                                "next_state_role": next_role,
                            }
                        )
            i += 1
        return pairs

    async def _extract_hint(
        self,
        assistant_text: str,
        next_state_text: str,
        next_state_role: str,
    ) -> Optional[str]:
        """
        使用多数投票的 LLM 评判从下一状态信号中提取事后提示。

        如果评判正面投票则返回提示字符串，否则返回 None。
        """
        judge_messages = _build_hint_judge_messages(
            response_text=assistant_text,
            next_state_text=next_state_text,
            next_state_role=next_state_role,
        )

        # 跨多个评判查询进行多数投票
        votes = []
        tasks = []
        for _ in range(self.config.prm_votes):
            tasks.append(
                self.server.chat_completion(
                    messages=judge_messages,
                    n=1,
                    max_tokens=500,
                    temperature=0.7,
                    split="eval",
                )
            )

        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, Exception):
                logger.debug("Hint judge call failed: %s", result)
                votes.append({"score": None, "hint": ""})
                continue
            try:
                text = result.choices[0].message.content or ""
                score, hint = _parse_hint_result(text)
                votes.append({"score": score, "hint": hint})
            except Exception as e:
                logger.debug("Hint parse failed: %s", e)
                votes.append({"score": None, "hint": ""})

        selected = _select_best_hint(votes)
        if selected is None:
            return None
        return selected["hint"]

    @staticmethod
    def _find_token_span(
        full_tokens: List[int], sub_tokens: List[int]
    ) -> Optional[int]:
        """
        在 full_tokens 中找到 sub_tokens 出现的位置。
        返回起始索引，如果未找到则返回 None。

        使用滑动窗口搜索。对于长序列，从末尾搜索，
        因为助手回复通常在序列末尾。
        """
        if not sub_tokens or not full_tokens:
            return None
        sub_len = len(sub_tokens)
        full_len = len(full_tokens)
        if sub_len > full_len:
            return None

        # 从后向前搜索（助手回复通常在末尾附近）
        for i in range(full_len - sub_len, -1, -1):
            if full_tokens[i : i + sub_len] == sub_tokens:
                return i
        return None

    # ═══════════════════════════════════════════════════════════════════
    # 6. evaluate
    # ═══════════════════════════════════════════════════════════════════

    async def evaluate(self, *args, **kwargs) -> None:
        """
        在保留的编程任务上进行评估，使用完整智能体循环。
        评估期间不进行 OPD — 仅标准智能体评估。
        """
        if not self._eval_items:
            logger.warning("No eval items available.")
            return

        eval_size = min(self.config.eval_size, len(self._eval_items))
        eval_items = self._eval_items[:eval_size]

        logger.info("Running eval on %d coding tasks...", len(eval_items))
        start_time = time.time()
        samples = []

        tools, valid_names = self._resolve_tools_for_group()

        for i, item in enumerate(eval_items):
            task_id = str(uuid.uuid4())
            logger.info(
                "Eval [%d/%d]: %s...", i + 1, len(eval_items), item["task"][:60]
            )

            try:
                messages: List[Dict[str, Any]] = []
                if self.config.system_prompt:
                    messages.append(
                        {"role": "system", "content": self.config.system_prompt}
                    )
                messages.append(
                    {"role": "user", "content": self.format_prompt(item)}
                )

                agent = HermesAgentLoop(
                    server=self.server,
                    tool_schemas=tools,
                    valid_tool_names=valid_names,
                    max_turns=self.config.max_agent_turns,
                    task_id=task_id,
                    temperature=0.0,
                    max_tokens=self.config.max_token_length,
                    extra_body=self.config.extra_body,
                    budget_config=self.config.build_budget_config(),
                )
                result = await agent.run(messages)

                # 计算奖励（跟踪缓冲区长度以回滚评估污染）
                buf_len = len(self._correctness_buffer)
                ctx = ToolContext(task_id)
                try:
                    reward = await self.compute_reward(item, result, ctx)
                finally:
                    ctx.cleanup()

                # 提取正确性并回滚训练缓冲区
                correctness = (
                    self._correctness_buffer[buf_len]
                    if len(self._correctness_buffer) > buf_len
                    else 0.0
                )
                for buf in (
                    self._reward_buffer,
                    self._correctness_buffer,
                    self._efficiency_buffer,
                    self._tool_usage_buffer,
                ):
                    if len(buf) > buf_len:
                        buf.pop()

                # 如果 OPD 缓冲区被触及也回滚
                for buf in (
                    self._hints_extracted_buffer,
                    self._opd_turns_scored_buffer,
                ):
                    if len(buf) > buf_len:
                        buf.pop()

                # 提取最终回复
                final_response = ""
                for msg in reversed(result.messages):
                    if (
                        msg.get("role") == "assistant"
                        and msg.get("content")
                        and not final_response
                    ):
                        final_response = msg["content"]
                        break

                samples.append(
                    {
                        "prompt": item["task"][:200],
                        "response": final_response[:500],
                        "correctness": correctness,
                        "reward": reward,
                        "turns": result.turns_used,
                    }
                )

                logger.info(
                    "  → correctness=%.2f, reward=%.3f, turns=%d",
                    correctness,
                    reward,
                    result.turns_used,
                )

            except Exception as e:
                logger.error("Eval error: %s", e)
                samples.append(
                    {
                        "prompt": item["task"][:200],
                        "response": f"ERROR: {e}",
                        "correctness": 0.0,
                        "reward": 0.0,
                        "turns": 0,
                    }
                )

        end_time = time.time()

        correctness_scores = [s["correctness"] for s in samples]
        rewards = [s["reward"] for s in samples]
        n = len(samples)

        eval_metrics = {
            "eval/mean_correctness": sum(correctness_scores) / n if n else 0.0,
            "eval/mean_reward": sum(rewards) / n if n else 0.0,
            "eval/pass_rate": (
                sum(1 for c in correctness_scores if c >= 0.8) / n if n else 0.0
            ),
            "eval/n_items": n,
        }

        logger.info(
            "Eval complete — correctness=%.3f, reward=%.3f, pass_rate=%.0f%%",
            eval_metrics["eval/mean_correctness"],
            eval_metrics["eval/mean_reward"],
            eval_metrics["eval/pass_rate"] * 100,
        )

        await self.evaluate_log(
            metrics=eval_metrics,
            samples=samples,
            start_time=start_time,
            end_time=end_time,
        )

    # ═══════════════════════════════════════════════════════════════════
    # 7. wandb_log — 自定义 OPD 指标
    # ═══════════════════════════════════════════════════════════════════

    async def wandb_log(self, wandb_metrics: Optional[Dict] = None) -> None:
        """将奖励分解和 OPD 特定指标记录到 wandb。"""
        if wandb_metrics is None:
            wandb_metrics = {}

        if self._reward_buffer:
            n = len(self._reward_buffer)
            wandb_metrics["train/mean_reward"] = sum(self._reward_buffer) / n
            wandb_metrics["train/mean_correctness"] = (
                sum(self._correctness_buffer) / n
            )
            wandb_metrics["train/mean_efficiency"] = (
                sum(self._efficiency_buffer) / n
            )
            wandb_metrics["train/mean_tool_usage"] = (
                sum(self._tool_usage_buffer) / n
            )
            wandb_metrics["train/pass_rate"] = (
                sum(1 for c in self._correctness_buffer if c >= 0.8) / n
            )
            wandb_metrics["train/total_rollouts"] = n

            self._reward_buffer.clear()
            self._correctness_buffer.clear()
            self._efficiency_buffer.clear()
            self._tool_usage_buffer.clear()

        # OPD 特定指标
        if self._hints_extracted_buffer:
            n = len(self._hints_extracted_buffer)
            wandb_metrics["opd/mean_hints_per_rollout"] = (
                sum(self._hints_extracted_buffer) / n
            )
            wandb_metrics["opd/mean_turns_scored"] = (
                sum(self._opd_turns_scored_buffer) / n
            )
            wandb_metrics["opd/hint_rate"] = (
                sum(1 for h in self._hints_extracted_buffer if h > 0) / n
            )
            wandb_metrics["opd/total_hints"] = sum(self._hints_extracted_buffer)
            wandb_metrics["opd/total_scored_turns"] = sum(
                self._opd_turns_scored_buffer
            )

            self._hints_extracted_buffer.clear()
            self._opd_turns_scored_buffer.clear()

        await super().wandb_log(wandb_metrics)


# ═══════════════════════════════════════════════════════════════════════
# 入口点
# ═══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    AgenticOPDEnv.cli()
