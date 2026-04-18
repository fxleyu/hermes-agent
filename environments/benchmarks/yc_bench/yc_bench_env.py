"""
YCBenchEvalEnv -- YC-Bench 长期决策智能体基准评估环境

在 YC-Bench 上评估智能体 LLM：一个确定性、长期决策基准测试，
智能体扮演 AI 初创公司的 CEO，在模拟的 1-3 年运营期间，
管理现金流、员工、任务和声望（横跨 4 个领域），
完全通过 CLI 子进程调用与基于 SQLite 的离散事件模拟交互。

与 TerminalBench2（按任务的二值通过/失败）不同，YC-Bench 衡量的是
持续的多轮战略一致性——智能体能否在数百轮中管理复合决策而不破产。

这是一个纯评估环境。运行方式：

    python environments/benchmarks/yc_bench/yc_bench_env.py evaluate \\
        --config environments/benchmarks/yc_bench/default.yaml

评估流程：
    1. setup()     -- 验证 yc-bench 已安装，构建评估矩阵（预设 x 种子）
    2. evaluate()  -- 按顺序遍历所有运行：
        a. rollout_and_score_eval()  -- 单次运行的智能体循环
            - 通过 `sim init`（而非 `run`）初始化新的 yc-bench 模拟
            - 仅使用终端工具运行 HermesAgentLoop
            - 读取最终 SQLite 数据库提取分数
            - 返回存活状态（0/1）+ 标准化资金分数
        b. 聚合每预设和整体指标
        c. 通过 evaluate_log() 和 wandb 记录结果

核心特性：
  - 仅 CLI 接口：智能体通过终端工具调用 yc-bench 子命令
  - 确定性：相同种子 + 预设 = 相同世界（基于 SHA256 的随机数生成器）
  - 多维度评分：存活状态 + 标准化最终资金
  - 按预设的难度分组结果
  - 每次运行独立的 SQLite 数据库（无跨运行状态泄漏）

依赖：pip install hermes-agent[yc-bench]
"""

import asyncio
import datetime
import json
import logging
import math
import os
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_repo_root = Path(__file__).resolve().parent.parent.parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from pydantic import Field

from atroposlib.envs.base import EvalHandlingEnum
from atroposlib.envs.server_handling.server_manager import APIServerConfig

from environments.agent_loop import HermesAgentLoop
from environments.hermes_base_env import HermesAgentBaseEnv, HermesAgentEnvConfig

logger = logging.getLogger(__name__)

# =============================================================================
# 系统提示词
# =============================================================================

YC_BENCH_SYSTEM_PROMPT = """\
You are the autonomous CEO of an early-stage AI startup in a deterministic
business simulation. You manage the company exclusively through the `yc-bench`
CLI tool. Your primary goal is to **survive** until the simulation horizon ends
without going bankrupt, while **maximising final funds**.

## Simulation Mechanics

- **Funds**: You start with $250,000 seed capital. Revenue comes from completing
  tasks. Rewards scale with your prestige: `base × (1 + scale × (prestige − 1))`.
- **Domains**: There are 4 skill domains: **research**, **inference**,
  **data_environment**, and **training**. Each has its own prestige level
  (1.0-10.0). Higher prestige unlocks better-paying tasks.
- **Employees**: You have employees (Junior/Mid/Senior) with domain-specific
  skill rates. **Throughput splits**: `effective_rate = base_rate / N` where N
  is the number of active tasks assigned to that employee. Focus beats breadth.
- **Payroll**: Deducted automatically on the first business day of each month.
  Running out of funds = bankruptcy = game over.
- **Time**: The simulation runs on business days (Mon-Fri), 09:00-18:00.
  Time only advances when you call `yc-bench sim resume`.

## Task Lifecycle

1. Browse market tasks with `market browse`
2. Accept a task with `task accept` (this sets its deadline)
3. Assign employees with `task assign`
4. Dispatch with `task dispatch` to start work
5. Call `sim resume` to advance time and let employees make progress
6. Tasks complete when all domain requirements are fulfilled

**Penalties for failure vary by difficulty preset.** Completing a task on time
earns full reward + prestige gain. Missing a deadline or cancelling a task
incurs prestige penalties -- cancelling is always more costly than letting a
task fail, so cancel only as a last resort.

## CLI Commands

### Observe
- `yc-bench company status`                                         -- funds, prestige, runway
- `yc-bench employee list`                                          -- skills, salary, active tasks
- `yc-bench market browse [--domain D] [--required-prestige-lte N]` -- available tasks
- `yc-bench task list [--status active|planned]`                    -- your tasks
- `yc-bench task inspect --task-id UUID`                            -- progress, deadline, assignments
- `yc-bench finance ledger [--category monthly_payroll|task_reward]` -- transaction history
- `yc-bench report monthly`                                         -- monthly P&L

### Act
- `yc-bench task accept --task-id UUID`                              -- accept from market
- `yc-bench task assign --task-id UUID --employee-id UUID`           -- assign employee
- `yc-bench task dispatch --task-id UUID`                            -- start work (needs >=1 assignment)
- `yc-bench task cancel --task-id UUID --reason "text"`              -- cancel (prestige penalty)
- `yc-bench sim resume`                                              -- advance simulation clock

### Memory (persists across context truncation)
- `yc-bench scratchpad read`            -- read your persistent notes
- `yc-bench scratchpad write --content "text"`  -- overwrite notes
- `yc-bench scratchpad append --content "text"` -- append to notes
- `yc-bench scratchpad clear`           -- clear notes

## Strategy Guidelines

1. **Specialise in 2-3 domains** to climb the prestige ladder faster and unlock
   high-reward tasks. Don't spread thin across all 4 domains early on.
2. **Focus employees** -- assigning one employee to many tasks halves their
   throughput per additional task. Keep assignments concentrated.
3. **Use the scratchpad** to track your strategy, upcoming deadlines, and
   employee assignments. This persists even if conversation context is truncated.
4. **Monitor runway** -- always know how many months of payroll you can cover.
   Accept high-reward tasks before payroll dates.
5. **Don't over-accept** -- taking too many tasks and missing deadlines cascades
   into prestige loss, locking you out of profitable contracts.
6. Use `finance ledger` and `report monthly` to track revenue trends.

## Your Turn

Each turn:
1. Call `yc-bench company status` and `yc-bench task list` to orient yourself.
2. Check for completed tasks and pending deadlines.
3. Browse market for profitable tasks within your prestige level.
4. Accept, assign, and dispatch tasks strategically.
5. Call `yc-bench sim resume` to advance time.
6. Repeat until the simulation ends.

Think step by step before acting."""

# 初始资金（美分），即 $250,000
INITIAL_FUNDS_CENTS = 25_000_000

# 每个预设的默认模拟期限（年）
_PRESET_HORIZONS = {
    "tutorial": 1,
    "easy": 1,
    "medium": 1,
    "hard": 1,
    "nightmare": 1,
    "fast_test": 1,
    "default": 3,
    "high_reward": 1,
}


# =============================================================================
# 配置
# =============================================================================

class YCBenchEvalConfig(HermesAgentEnvConfig):
    """
    YC-Bench 评估环境的配置类。

    继承 HermesAgentEnvConfig，并添加 YC-Bench 特有的
    预设选择、种子控制、评分和模拟参数等配置项。
    """

    presets: List[str] = Field(
        default=["fast_test", "medium", "hard"],
        description="YC-Bench preset names to evaluate.",
    )
    seeds: List[int] = Field(
        default=[1, 2, 3],
        description="Random seeds -- each preset x seed = one run.",
    )
    run_timeout: int = Field(
        default=3600,
        description="Maximum wall-clock seconds per run. Default 60 minutes.",
    )
    survival_weight: float = Field(
        default=0.5,
        description="Weight of survival (0/1) in composite score.",
    )
    funds_weight: float = Field(
        default=0.5,
        description="Weight of normalised final funds in composite score.",
    )
    db_dir: str = Field(
        default="/tmp/yc_bench_dbs",
        description="Directory for per-run SQLite databases.",
    )
    horizon_years: Optional[int] = Field(
        default=None,
        description=(
            "Simulation horizon in years. If None (default), inferred from "
            "preset name (1 year for most, 3 for 'default')."
        ),
    )
    company_name: str = Field(
        default="BenchCo",
        description="Name of the simulated company.",
    )
    start_date: str = Field(
        default="01/01/2025",
        description="Simulation start date in MM/DD/YYYY format (yc-bench convention).",
    )


# =============================================================================
# 评分辅助函数
# =============================================================================

def _read_final_score(db_path: str) -> Dict[str, Any]:
    """
    从 YC-Bench SQLite 数据库中读取最终游戏状态。

    返回包含 final_funds_cents（整数）、survived（布尔值）、
    terminal_reason（字符串）的字典。

    注意：yc-bench 的表名是复数形式——'companies' 而非 'company'，
    'sim_events' 而非 'simulation_log'。
    """
    if not os.path.exists(db_path):
        logger.warning("DB not found at %s", db_path)
        return {
            "final_funds_cents": 0,
            "survived": False,
            "terminal_reason": "db_missing",
        }

    conn = None
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()

        # 从 'companies' 表读取最终资金
        cur.execute("SELECT funds_cents FROM companies LIMIT 1")
        row = cur.fetchone()
        funds = row[0] if row else 0

        # 从 'sim_events' 表确定终止原因
        terminal_reason = "unknown"
        try:
            cur.execute(
                "SELECT event_type FROM sim_events "
                "WHERE event_type IN ('bankruptcy', 'horizon_end') "
                "ORDER BY scheduled_at DESC LIMIT 1"
            )
            event_row = cur.fetchone()
            if event_row:
                terminal_reason = event_row[0]
        except sqlite3.OperationalError:
            # 如果模拟未推进，表可能不存在
            pass

        survived = funds >= 0 and terminal_reason != "bankruptcy"
        return {
            "final_funds_cents": funds,
            "survived": survived,
            "terminal_reason": terminal_reason,
        }

    except Exception as e:
        logger.error("Failed to read DB %s: %s", db_path, e)
        return {
            "final_funds_cents": 0,
            "survived": False,
            "terminal_reason": f"db_error: {e}",
        }
    finally:
        if conn:
            conn.close()


def _compute_composite_score(
    final_funds_cents: int,
    survived: bool,
    survival_weight: float = 0.5,
    funds_weight: float = 0.5,
    initial_funds_cents: int = INITIAL_FUNDS_CENTS,
) -> float:
    """
    根据存活状态和最终资金计算综合分数。

    分数 = survival_weight * 存活分数
          + funds_weight * 标准化资金分数

    标准化资金使用对数尺度相对于初始资本：
    - 资金 <= 0:          0.0
    - 资金 == 初始值:    ~0.15
    - 资金 == 10 倍:     ~0.52
    - 资金 == 100 倍:     1.0
    """
    survival_score = 1.0 if survived else 0.0

    if final_funds_cents <= 0:
        funds_score = 0.0
    else:
        # 使用对数尺度将资金比率映射到 [0, 1] 范围
        max_ratio = 100.0
        ratio = final_funds_cents / max(initial_funds_cents, 1)
        funds_score = min(math.log1p(ratio) / math.log1p(max_ratio), 1.0)

    return survival_weight * survival_score + funds_weight * funds_score


# =============================================================================
# 主评估环境
# =============================================================================

class YCBenchEvalEnv(HermesAgentBaseEnv):
    """
    YC-Bench 长期决策智能体基准评估环境（仅评估）。

    每个评估项是一个 (preset, seed) 对。环境通过 ``yc-bench sim init``
    （而非 ``yc-bench run``，后者会启动竞争的内建智能体循环）初始化模拟。
    然后 HermesAgentLoop 通过终端工具调用各个 yc-bench CLI 命令驱动交互。

    智能体循环结束后，读取 SQLite 数据库提取最终分数。

    评分：
      综合分数 = 0.5 * 存活状态 + 0.5 * 标准化资金
    """

    name = "yc-bench"
    env_config_cls = YCBenchEvalConfig

    @classmethod
    def config_init(cls) -> Tuple[YCBenchEvalConfig, List[APIServerConfig]]:
        env_config = YCBenchEvalConfig(
            enabled_toolsets=["terminal"],
            disabled_toolsets=None,
            distribution=None,
            max_agent_turns=200,
            max_token_length=32000,
            agent_temperature=0.0,
            system_prompt=YC_BENCH_SYSTEM_PROMPT,
            terminal_backend="local",
            terminal_timeout=60,
            presets=["fast_test", "medium", "hard"],
            seeds=[1, 2, 3],
            run_timeout=3600,
            survival_weight=0.5,
            funds_weight=0.5,
            db_dir="/tmp/yc_bench_dbs",
            eval_handling=EvalHandlingEnum.STOP_TRAIN,
            group_size=1,
            steps_per_eval=1,
            total_steps=1,
            tokenizer_name="NousResearch/Hermes-3-Llama-3.1-8B",
            use_wandb=True,
            wandb_name="yc-bench",
            ensure_scores_are_not_same=False,
        )

        server_configs = [
            APIServerConfig(
                base_url="https://openrouter.ai/api/v1",
                model_name="anthropic/claude-sonnet-4.6",
                server_type="openai",
                api_key=os.getenv("OPENROUTER_API_KEY", ""),
                health_check=False,
            )
        ]

        return env_config, server_configs

    # =========================================================================
    # 初始化
    # =========================================================================

    async def setup(self):
        """验证 yc-bench 已安装并构建评估矩阵。"""
        # 验证 yc-bench CLI 可用
        try:
            result = subprocess.run(
                ["yc-bench", "--help"], capture_output=True, text=True, timeout=10
            )
            if result.returncode != 0:
                raise FileNotFoundError
        except (FileNotFoundError, subprocess.TimeoutExpired):
            raise RuntimeError(
                "yc-bench CLI not found. Install with:\n"
                '  pip install "hermes-agent[yc-bench]"\n'
                "Or: git clone https://github.com/collinear-ai/yc-bench "
                "&& cd yc-bench && pip install -e ."
            )
        print("yc-bench CLI verified.")

        # 构建评估矩阵：预设 x 种子
        self.all_eval_items = [
            {"preset": preset, "seed": seed}
            for preset in self.config.presets
            for seed in self.config.seeds
        ]
        self.iter = 0

        os.makedirs(self.config.db_dir, exist_ok=True)
        self.eval_metrics: List[Tuple[str, float]] = []

        # 流式 JSONL 日志，确保崩溃时结果不丢失
        log_dir = os.path.join(os.path.dirname(__file__), "logs")
        os.makedirs(log_dir, exist_ok=True)
        run_ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self._streaming_path = os.path.join(log_dir, f"samples_{run_ts}.jsonl")
        self._streaming_file = open(self._streaming_path, "w")
        self._streaming_lock = threading.Lock()

        print(f"\nYC-Bench eval matrix: {len(self.all_eval_items)} runs")
        for item in self.all_eval_items:
            print(f"  preset={item['preset']!r}  seed={item['seed']}")
        print(f"Streaming results to: {self._streaming_path}\n")

    def _save_result(self, result: Dict[str, Any]):
        """将单次运行结果立即写入流式 JSONL 文件。"""
        if not hasattr(self, "_streaming_file") or self._streaming_file.closed:
            return
        with self._streaming_lock:
            self._streaming_file.write(
                json.dumps(result, ensure_ascii=False, default=str) + "\n"
            )
            self._streaming_file.flush()

    # =========================================================================
    # 训练管线桩函数（仅评估——不使用）
    # =========================================================================

    async def get_next_item(self):
        item = self.all_eval_items[self.iter % len(self.all_eval_items)]
        self.iter += 1
        return item

    def format_prompt(self, item: Dict[str, Any]) -> str:
        preset = item["preset"]
        seed = item["seed"]
        return (
            f"A new YC-Bench simulation has been initialized "
            f"(preset='{preset}', seed={seed}).\n"
            f"Your company '{self.config.company_name}' is ready.\n\n"
            "Begin by calling:\n"
            "1. `yc-bench company status` -- see your starting funds and prestige\n"
            "2. `yc-bench employee list` -- see your team and their skills\n"
            "3. `yc-bench market browse --required-prestige-lte 1` -- find tasks "
            "you can take\n\n"
            "Then accept 2-3 tasks, assign employees, dispatch them, and call "
            "`yc-bench sim resume` to advance time. Repeat this loop until the "
            "simulation ends (horizon reached or bankruptcy)."
        )

    async def compute_reward(self, item, result, ctx) -> float:
        return 0.0

    async def collect_trajectories(self, item):
        return None, []

    async def score(self, rollout_group_data):
        return None

    # =========================================================================
    # 单次运行评估
    # =========================================================================

    async def rollout_and_score_eval(self, eval_item: Dict[str, Any]) -> Dict:
        """
        评估单个 (preset, seed) 运行。

        1. 设置 DATABASE_URL 和 YC_BENCH_EXPERIMENT 环境变量
        2. 通过 ``yc-bench sim init``（而非 ``run``）初始化模拟
        3. 使用终端工具运行 HermesAgentLoop
        4. 读取 SQLite 数据库计算最终分数
        5. 返回包含存活状态、资金和综合分数的结果字典
        """
        preset = eval_item["preset"]
        seed = eval_item["seed"]
        run_id = str(uuid.uuid4())[:8]
        run_key = f"{preset}_seed{seed}_{run_id}"

        from tqdm import tqdm
        tqdm.write(f"  [START] preset={preset!r} seed={seed} (run_id={run_id})")
        run_start = time.time()

        # 每次运行使用独立数据库——防止跨运行状态泄漏
        db_path = os.path.join(self.config.db_dir, f"yc_bench_{run_key}.db")
        os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
        os.environ["YC_BENCH_EXPERIMENT"] = preset

        # 确定模拟期限：显式配置覆盖 > 预设查找 > 默认 1 年
        horizon = self.config.horizon_years or _PRESET_HORIZONS.get(preset, 1)

        try:
            # ----------------------------------------------------------
            # 步骤 1：通过 CLI 初始化模拟
            # 重要：使用 `sim init`，而非 `yc-bench run`。
            # `yc-bench run` 会启动 yc-bench 自带的 LLM 智能体循环
            # （通过 LiteLLM），会与我们的 HermesAgentLoop 竞争。
            # `sim init` 只是设置世界然后返回。
            # ----------------------------------------------------------
            init_cmd = [
                "yc-bench", "sim", "init",
                "--seed", str(seed),
                "--start-date", self.config.start_date,
                "--company-name", self.config.company_name,
                "--horizon-years", str(horizon),
            ]
            init_result = subprocess.run(
                init_cmd, capture_output=True, text=True, timeout=30,
            )
            if init_result.returncode != 0:
                error_msg = (init_result.stderr or init_result.stdout).strip()
                raise RuntimeError(f"yc-bench sim init failed: {error_msg}")

            tqdm.write(f"    Simulation initialized (horizon={horizon}yr)")

            # ----------------------------------------------------------
            # 步骤 2：运行 HermesAgentLoop
            # ----------------------------------------------------------
            tools, valid_names = self._resolve_tools_for_group()

            messages: List[Dict[str, Any]] = [
                {"role": "system", "content": YC_BENCH_SYSTEM_PROMPT},
                {"role": "user", "content": self.format_prompt(eval_item)},
            ]

            agent = HermesAgentLoop(
                server=self.server,
                tool_schemas=tools,
                valid_tool_names=valid_names,
                max_turns=self.config.max_agent_turns,
                task_id=run_id,
                temperature=self.config.agent_temperature,
                max_tokens=self.config.max_token_length,
                extra_body=self.config.extra_body,
                budget_config=self.config.build_budget_config(),
            )
            result = await agent.run(messages)

            # ----------------------------------------------------------
            # 步骤 3：从模拟数据库中读取最终分数
            # ----------------------------------------------------------
            score_data = _read_final_score(db_path)
            final_funds = score_data["final_funds_cents"]
            survived = score_data["survived"]
            terminal_reason = score_data["terminal_reason"]

            composite = _compute_composite_score(
                final_funds_cents=final_funds,
                survived=survived,
                survival_weight=self.config.survival_weight,
                funds_weight=self.config.funds_weight,
            )

            elapsed = time.time() - run_start
            status = "SURVIVED" if survived else "BANKRUPT"
            if final_funds >= 0:
                funds_str = f"${final_funds / 100:,.0f}"
            else:
                funds_str = f"-${abs(final_funds) / 100:,.0f}"

            tqdm.write(
                f"  [{status}] preset={preset!r} seed={seed} "
                f"funds={funds_str} score={composite:.3f} "
                f"turns={result.turns_used} ({elapsed:.0f}s)"
            )

            out = {
                "preset": preset,
                "seed": seed,
                "survived": survived,
                "final_funds_cents": final_funds,
                "final_funds_usd": final_funds / 100,
                "terminal_reason": terminal_reason,
                "composite_score": composite,
                "turns_used": result.turns_used,
                "finished_naturally": result.finished_naturally,
                "elapsed_seconds": elapsed,
                "db_path": db_path,
                "messages": result.messages,
            }
            self._save_result(out)
            return out

        except Exception as e:
            elapsed = time.time() - run_start
            logger.error("Run %s failed: %s", run_key, e, exc_info=True)
            tqdm.write(
                f"  [ERROR] preset={preset!r} seed={seed}: {e} ({elapsed:.0f}s)"
            )
            out = {
                "preset": preset,
                "seed": seed,
                "survived": False,
                "final_funds_cents": 0,
                "final_funds_usd": 0.0,
                "terminal_reason": f"error: {e}",
                "composite_score": 0.0,
                "turns_used": 0,
                "error": str(e),
                "elapsed_seconds": elapsed,
            }
            self._save_result(out)
            return out

    # =========================================================================
    # 评估
    # =========================================================================

    async def _run_with_timeout(self, item: Dict[str, Any]) -> Dict:
        """为单次推演包装挂钟超时。"""
        preset = item["preset"]
        seed = item["seed"]
        try:
            return await asyncio.wait_for(
                self.rollout_and_score_eval(item),
                timeout=self.config.run_timeout,
            )
        except asyncio.TimeoutError:
            from tqdm import tqdm
            tqdm.write(
                f"  [TIMEOUT] preset={preset!r} seed={seed} "
                f"(exceeded {self.config.run_timeout}s)"
            )
            out = {
                "preset": preset,
                "seed": seed,
                "survived": False,
                "final_funds_cents": 0,
                "final_funds_usd": 0.0,
                "terminal_reason": f"timeout ({self.config.run_timeout}s)",
                "composite_score": 0.0,
                "turns_used": 0,
                "error": "timeout",
            }
            self._save_result(out)
            return out

    async def evaluate(self, *args, **kwargs) -> None:
        """
        在所有 (preset, seed) 组合上运行 YC-Bench 评估。

        按顺序运行——每次运行 100-500 轮，并行化成本过高
        且会导致环境变量冲突。
        """
        start_time = time.time()
        from tqdm import tqdm

        # --- 兼容 tqdm 的日志处理器（TB2 模式） ---
        class _TqdmHandler(logging.Handler):
            def emit(self, record):
                try:
                    tqdm.write(self.format(record))
                except Exception:
                    self.handleError(record)

        root = logging.getLogger()
        handler = _TqdmHandler()
        handler.setFormatter(
            logging.Formatter("%(levelname)s %(name)s: %(message)s")
        )
        root.handlers = [handler]
        for noisy in ("httpx", "openai"):
            logging.getLogger(noisy).setLevel(logging.WARNING)

        # --- 打印配置摘要 ---
        print(f"\n{'='*60}")
        print("Starting YC-Bench Evaluation")
        print(f"{'='*60}")
        print(f"  Presets: {self.config.presets}")
        print(f"  Seeds: {self.config.seeds}")
        print(f"  Total runs: {len(self.all_eval_items)}")
        print(f"  Max turns/run: {self.config.max_agent_turns}")
        print(f"  Run timeout: {self.config.run_timeout}s")
        print(f"{'='*60}\n")

        results = []
        pbar = tqdm(
            total=len(self.all_eval_items), desc="YC-Bench", dynamic_ncols=True
        )

        try:
            for item in self.all_eval_items:
                result = await self._run_with_timeout(item)
                results.append(result)
                survived_count = sum(1 for r in results if r.get("survived"))
                pbar.set_postfix_str(
                    f"survived={survived_count}/{len(results)}"
                )
                pbar.update(1)

        except (KeyboardInterrupt, asyncio.CancelledError):
            tqdm.write("\n[INTERRUPTED] Stopping evaluation...")
            pbar.close()
            try:
                from tools.terminal_tool import cleanup_all_environments
                cleanup_all_environments()
            except Exception:
                pass
            if hasattr(self, "_streaming_file") and not self._streaming_file.closed:
                self._streaming_file.close()
            return

        pbar.close()
        end_time = time.time()

        # --- 计算指标 ---
        valid = [r for r in results if r is not None]
        if not valid:
            print("Warning: No valid results.")
            return

        total = len(valid)
        survived_total = sum(1 for r in valid if r.get("survived"))
        survival_rate = survived_total / total if total else 0.0
        avg_score = (
            sum(r.get("composite_score", 0) for r in valid) / total
            if total
            else 0.0
        )

        preset_results: Dict[str, List[Dict]] = defaultdict(list)
        for r in valid:
            preset_results[r["preset"]].append(r)

        eval_metrics = {
            "eval/survival_rate": survival_rate,
            "eval/avg_composite_score": avg_score,
            "eval/total_runs": total,
            "eval/survived_runs": survived_total,
            "eval/evaluation_time_seconds": end_time - start_time,
        }

        for preset, items in sorted(preset_results.items()):
            ps = sum(1 for r in items if r.get("survived"))
            pt = len(items)
            pa = (
                sum(r.get("composite_score", 0) for r in items) / pt
                if pt
                else 0
            )
            key = preset.replace("-", "_")
            eval_metrics[f"eval/survival_rate_{key}"] = ps / pt if pt else 0
            eval_metrics[f"eval/avg_score_{key}"] = pa

        self.eval_metrics = [(k, v) for k, v in eval_metrics.items()]

        # --- 打印摘要 ---
        print(f"\n{'='*60}")
        print("YC-Bench Evaluation Results")
        print(f"{'='*60}")
        print(
            f"Overall survival rate: {survival_rate:.1%} "
            f"({survived_total}/{total})"
        )
        print(f"Average composite score: {avg_score:.4f}")
        print(f"Evaluation time: {end_time - start_time:.1f}s")

        print("\nPer-preset breakdown:")
        for preset, items in sorted(preset_results.items()):
            ps = sum(1 for r in items if r.get("survived"))
            pt = len(items)
            pa = (
                sum(r.get("composite_score", 0) for r in items) / pt
                if pt
                else 0
            )
            print(f"  {preset}: {ps}/{pt} survived  avg_score={pa:.4f}")
            for r in items:
                status = "SURVIVED" if r.get("survived") else "BANKRUPT"
                funds = r.get("final_funds_usd", 0)
                print(
                    f"    seed={r['seed']}  [{status}]  "
                    f"${funds:,.0f}  "
                    f"score={r.get('composite_score', 0):.3f}"
                )

        print(f"{'='*60}\n")

        # --- 记录结果 ---
        samples = [
            {k: v for k, v in r.items() if k != "messages"} for r in valid
        ]

        try:
            await self.evaluate_log(
                metrics=eval_metrics,
                samples=samples,
                start_time=start_time,
                end_time=end_time,
                generation_parameters={
                    "temperature": self.config.agent_temperature,
                    "max_tokens": self.config.max_token_length,
                    "max_agent_turns": self.config.max_agent_turns,
                },
            )
        except Exception as e:
            print(f"Error logging results: {e}")

        # --- 清理（TB2 模式） ---
        if hasattr(self, "_streaming_file") and not self._streaming_file.closed:
            self._streaming_file.close()
            print(f"Results saved to: {self._streaming_path}")

        try:
            from tools.terminal_tool import cleanup_all_environments
            cleanup_all_environments()
        except Exception:
            pass

        try:
            from environments.agent_loop import _tool_executor
            _tool_executor.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass

    # =========================================================================
    # Wandb 日志记录
    # =========================================================================

    async def wandb_log(self, wandb_metrics: Optional[Dict] = None):
        """将 YC-Bench 特有的指标记录到 wandb。"""
        if wandb_metrics is None:
            wandb_metrics = {}
        for k, v in self.eval_metrics:
            wandb_metrics[k] = v
        self.eval_metrics = []
        await super().wandb_log(wandb_metrics)


if __name__ == "__main__":
    YCBenchEvalEnv.cli()
