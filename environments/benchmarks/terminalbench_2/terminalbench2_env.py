"""
TerminalBench2Env -- Terminal-Bench 2.0 评估环境

在 Terminal-Bench 2.0 的高难度终端任务上评估智能体 LLM。
每个任务提供一个唯一的 Docker 环境（Docker Hub 上的预构建镜像）、一条自然语言指令
和一个测试套件用于验证。智能体使用终端 + 文件工具完成任务，然后测试套件在同一
沙箱中运行。

这是一个纯评估环境（非训练环境），设计为通过 `evaluate` 子命令运行：

    python environments/terminalbench2_env.py evaluate \\
        --env.dataset_name NousResearch/terminal-bench-2

评估流程：
    1. setup()     -- 从 HuggingFace 加载 TB2 数据集
    2. evaluate()  -- 遍历所有任务，每个任务执行以下步骤：
        a. rollout_and_score_eval()  -- 单任务智能体循环 + 测试验证
            - 解析 Docker 镜像（优先使用预构建 Hub 镜像，回退到 Dockerfile 构建）
            - 通过 register_task_env_overrides() 注册每任务的 Modal 沙箱
            - 运行 HermesAgentLoop（终端 + 文件工具）
            - 上传测试套件并在同一沙箱中运行 test.sh
            - 返回二值通过/失败结果
        b. 聚合每任务、每类别和整体通过率
        c. 通过 evaluate_log() 和 wandb 记录结果

核心特性：
  - 基于预构建 Docker Hub 镜像的每任务 Modal 沙箱
  - 二值奖励：所有测试通过为 1.0，否则为 0.0
  - 通过 asyncio.Semaphore 控制并发的并行评估
  - 每任务、每类别和聚合通过率追踪
"""

import asyncio
import base64
import io
import json
import logging
import os
import shutil
import sys
import tarfile
import tempfile
import time
import uuid
from collections import defaultdict
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Dict, List, Optional, Tuple, Union

# 确保仓库根目录在 sys.path 中，以便导入项目模块
_repo_root = Path(__file__).resolve().parent.parent.parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from pydantic import Field

from atroposlib.envs.base import EvalHandlingEnum
from atroposlib.envs.server_handling.server_manager import APIServerConfig

from environments.agent_loop import AgentResult, HermesAgentLoop
from environments.hermes_base_env import HermesAgentBaseEnv, HermesAgentEnvConfig
from environments.tool_context import ToolContext
from tools.terminal_tool import (
    register_task_env_overrides,
    clear_task_env_overrides,
    cleanup_vm,
)

logger = logging.getLogger(__name__)


# =============================================================================
# 配置
# =============================================================================

class TerminalBench2EvalConfig(HermesAgentEnvConfig):
    """
    Terminal-Bench 2.0 评估环境的配置类。

    继承 HermesAgentEnvConfig，并添加 TB2 特有的数据集加载、
    测试执行、任务筛选和评估并发等配置项。
    """

    # --- 数据集 ---
    dataset_name: str = Field(
        default="NousResearch/terminal-bench-2",
        description="HuggingFace dataset containing TB2 tasks.",
    )

    # --- 测试执行 ---
    test_timeout: int = Field(
        default=180,
        description="Timeout in seconds for running the test suite after agent completes.",
    )

    # --- 镜像策略 ---
    force_build: bool = Field(
        default=False,
        description="If True, always build from Dockerfile (ignore docker_image). "
        "Useful for testing custom Dockerfiles.",
    )

    # --- 任务筛选（CLI 中以逗号分隔） ---
    task_filter: Optional[str] = Field(
        default=None,
        description="Comma-separated task names to run (e.g., 'fix-git,git-multibranch'). "
        "If not set, all tasks are run.",
    )
    skip_tasks: Optional[str] = Field(
        default=None,
        description="Comma-separated task names to skip on top of the default skip list.",
    )

    # --- 单任务挂钟超时 ---
    task_timeout: int = Field(
        default=1800,
        description="Maximum wall-clock seconds per task (agent loop + verification). "
        "Tasks exceeding this are scored as FAIL. Default 30 minutes.",
    )

    # --- 并发控制 ---
    max_concurrent_tasks: int = Field(
        default=8,
        description="Maximum number of tasks to run concurrently. "
        "Limits concurrent Modal sandbox creations to avoid async/threading deadlocks. "
        "Modal has internal limits and creating too many sandboxes simultaneously "
        "causes blocking calls to deadlock inside the thread pool.",
    )

    # --- 评估并发 ---
    eval_concurrency: int = Field(
        default=0,
        description="Maximum number of tasks to evaluate in parallel. "
        "0 means unlimited (all tasks run concurrently). "
        "Set to 8 for local backends to avoid overwhelming the machine.",
    )


# 无法在 Modal 上正常运行的任务，将从评分中排除。
MODAL_INCOMPATIBLE_TASKS = {
    "qemu-startup",        # 需要 KVM/硬件虚拟化
    "qemu-alpine-ssh",     # 需要 KVM/硬件虚拟化
    "crack-7z-hash",       # 密码暴力破解——在云沙箱超时内速度太慢
}


# =============================================================================
# Tar 解压辅助函数
# =============================================================================

def _normalize_tar_member_parts(member_name: str) -> list:
    """返回 tar 成员的安全路径组件，如果路径不安全则抛出 ValueError。"""
    # 统一将反斜杠替换为正斜杠，兼容 Windows 路径
    normalized_name = member_name.replace("\\", "/")
    posix_path = PurePosixPath(normalized_name)
    windows_path = PureWindowsPath(member_name)

    # 检查是否为绝对路径或带盘符（防止路径穿越攻击）
    if (
        not normalized_name
        or posix_path.is_absolute()
        or windows_path.is_absolute()
        or windows_path.drive
    ):
        raise ValueError(f"Unsafe archive member path: {member_name}")

    # 过滤空字符串和当前目录标记，拒绝父目录遍历
    parts = [part for part in posix_path.parts if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        raise ValueError(f"Unsafe archive member path: {member_name}")
    return parts


def _safe_extract_tar(tar: tarfile.TarFile, target_dir: Path) -> None:
    """安全解压 tar 归档，禁止路径穿越和符号链接条目。"""
    target_dir.mkdir(parents=True, exist_ok=True)
    target_root = target_dir.resolve()

    for member in tar.getmembers():
        parts = _normalize_tar_member_parts(member.name)
        target = target_dir.joinpath(*parts)
        target_real = target.resolve(strict=False)

        # 确保解压目标在目标根目录内部（防止路径穿越）
        try:
            target_real.relative_to(target_root)
        except ValueError as exc:
            raise ValueError(f"Unsafe archive member path: {member.name}") from exc

        if member.isdir():
            target_real.mkdir(parents=True, exist_ok=True)
            continue

        # 只允许普通文件（拒绝符号链接等特殊类型）
        if not member.isfile():
            raise ValueError(f"Unsupported archive member type: {member.name}")

        target_real.parent.mkdir(parents=True, exist_ok=True)
        extracted = tar.extractfile(member)
        if extracted is None:
            raise ValueError(f"Cannot read archive member: {member.name}")

        with extracted, open(target_real, "wb") as dst:
            shutil.copyfileobj(extracted, dst)

        # 保留原始文件权限（仅保留低 9 位 rwx 权限）
        try:
            os.chmod(target_real, member.mode & 0o777)
        except OSError:
            pass


def _extract_base64_tar(b64_data: str, target_dir: Path):
    """将 base64 编码的 tar.gz 归档解压到 target_dir 目录。"""
    if not b64_data:
        return
    raw = base64.b64decode(b64_data)
    buf = io.BytesIO(raw)
    with tarfile.open(fileobj=buf, mode="r:gz") as tar:
        _safe_extract_tar(tar, target_dir)


# =============================================================================
# 主评估环境
# =============================================================================

class TerminalBench2EvalEnv(HermesAgentBaseEnv):
    """
    Terminal-Bench 2.0 评估环境（仅评估，非训练）。

    继承 HermesAgentBaseEnv 以获得：
      - 终端后端设置（os.environ["TERMINAL_ENV"]）
      - 通过 _resolve_tools_for_group() 解析工具
      - 异步安全工具操作的猴子补丁
      - Wandb 轨迹格式化

    评估流程（通过 `environment.py evaluate` 触发）：
      1. setup()    -- 从 HuggingFace 加载数据集
      2. evaluate() -- 对所有任务执行 rollout_and_score_eval()

    每个任务在 rollout_and_score_eval() 中的流程：
      1. 解析 Docker 镜像（预构建 Hub 镜像或 Dockerfile 回退）
      2. 注册每任务的 Modal 沙箱覆盖配置
      3. 使用终端 + 文件工具运行 HermesAgentLoop
      4. 上传测试套件并在同一沙箱中执行 test.sh
      5. 检查 /logs/verifier/reward.txt 获取通过/失败结果
      6. 清理沙箱、覆盖配置和临时文件
    """

    name = "terminal-bench-2"
    env_config_cls = TerminalBench2EvalConfig

    @classmethod
    def config_init(cls) -> Tuple[TerminalBench2EvalConfig, List[APIServerConfig]]:
        """
        Terminal-Bench 2.0 评估的默认配置。

        使用仅评估设置：
          - eval_handling=STOP_TRAIN 使评估流程正常运行
          - steps_per_eval=1, total_steps=1 使评估立即触发
          - group_size=1（每组一次推演，每个任务成本较高）

        使用 Modal 终端后端（每任务云隔离沙箱）和
        OpenRouter + Claude 进行推理。
        """
        env_config = TerminalBench2EvalConfig(
            # 仅启用终端 + 文件工具（智能体通过 shell 命令交互）
            enabled_toolsets=["terminal", "file"],
            disabled_toolsets=None,
            distribution=None,

            # 智能体设置——TB2 任务复杂，需要多轮对话
            max_agent_turns=60,
            max_token_length=16000,
            agent_temperature=0.6,
            system_prompt=None,

            # Modal 后端用于每任务云隔离沙箱
            terminal_backend="modal",
            terminal_timeout=300,   # 每条命令 5 分钟（构建、pip install 等）

            # 测试执行超时（TB2 测试脚本可能会安装 pytest 等依赖）
            test_timeout=180,

            # 89 个任务并行运行，每个都需要一个线程来执行工具调用
            tool_pool_size=128,

            # --- 仅评估的 Atropos 设置 ---
            # 这些设置使环境以仅评估模式工作：
            #   - STOP_TRAIN: 评估期间暂停训练（评估环境的标准做法）
            #   - steps_per_eval=1, total_steps=1: 评估立即触发
            #   - group_size=1: 每组一次推演（每个任务成本较高）
            eval_handling=EvalHandlingEnum.STOP_TRAIN,
            group_size=1,
            steps_per_eval=1,
            total_steps=1,

            tokenizer_name="NousResearch/Hermes-3-Llama-3.1-8B",
            use_wandb=True,
            wandb_name="terminal-bench-2",
            ensure_scores_are_not_same=False,  # 二值奖励可能全为 0 或全为 1
        )

        # OpenRouter + Claude——API 密钥从 .env 加载
        server_configs = [
            APIServerConfig(
                base_url="https://openrouter.ai/api/v1",
                model_name="anthropic/claude-sonnet-4",
                server_type="openai",
                api_key=os.getenv("OPENROUTER_API_KEY", ""),
                health_check=False,
            )
        ]

        return env_config, server_configs

    # =========================================================================
    # 初始化——加载数据集
    # =========================================================================

    async def setup(self):
        """从 HuggingFace 加载 Terminal-Bench 2.0 数据集。"""
        from datasets import load_dataset

        # 自动将 terminal_lifetime 设为 task_timeout + 120 秒，
        # 确保沙箱在活跃任务期间不会被杀死，但在任务超时后能及时清理。
        lifetime = self.config.task_timeout + 120
        self.config.terminal_lifetime = lifetime
        os.environ["TERMINAL_LIFETIME_SECONDS"] = str(lifetime)
        print(f"  Terminal lifetime auto-set to {lifetime}s (task_timeout + 120s)")

        print(f"Loading TB2 dataset from: {self.config.dataset_name}")
        ds = load_dataset(self.config.dataset_name, split="train")

        # 应用任务筛选器（CLI 中以逗号分隔的字符串）
        tasks = list(ds)
        if self.config.task_filter:
            allowed = {name.strip() for name in self.config.task_filter.split(",")}
            tasks = [t for t in tasks if t["task_name"] in allowed]
            print(f"  Filtered to {len(tasks)} tasks: {sorted(allowed)}")

        # 跳过与当前后端不兼容的任务（例如 Modal 上的 QEMU）
        # 以及用户指定的 skip_tasks
        skip = set(MODAL_INCOMPATIBLE_TASKS) if self.config.terminal_backend == "modal" else set()
        if self.config.skip_tasks:
            skip |= {name.strip() for name in self.config.skip_tasks.split(",")}
        if skip:
            before = len(tasks)
            tasks = [t for t in tasks if t["task_name"] not in skip]
            skipped = before - len(tasks)
            if skipped > 0:
                print(f"  Skipped {skipped} incompatible tasks: {sorted(skip & {t['task_name'] for t in ds})}")

        self.all_eval_items = tasks
        self.iter = 0

        # 构建类别索引，用于按类别统计指标
        self.category_index: Dict[str, List[int]] = defaultdict(list)
        for i, task in enumerate(self.all_eval_items):
            self.category_index[task.get("category", "unknown")].append(i)

        # 奖励追踪，用于 wandb 日志记录
        self.eval_metrics: List[Tuple[str, float]] = []

        # 流式 JSONL 写入器——每完成一个任务立即保存完整对话，
        # 即使 Ctrl+C 中断也能保留数据。
        # 使用时间戳文件名，确保每次运行生成唯一文件。
        import datetime
        log_dir = os.path.join(os.path.dirname(__file__), "logs")
        os.makedirs(log_dir, exist_ok=True)
        run_ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self._streaming_path = os.path.join(log_dir, f"samples_{run_ts}.jsonl")
        self._streaming_file = open(self._streaming_path, "w")
        self._streaming_lock = __import__("threading").Lock()
        print(f"  Streaming results to: {self._streaming_path}")

        print(f"TB2 ready: {len(self.all_eval_items)} tasks across {len(self.category_index)} categories")
        for cat, indices in sorted(self.category_index.items()):
            print(f"  {cat}: {len(indices)} tasks")

    def _save_result(self, result: Dict[str, Any]):
        """将单个任务结果立即写入流式 JSONL 文件。"""
        if not hasattr(self, "_streaming_file") or self._streaming_file.closed:
            return
        with self._streaming_lock:
            self._streaming_file.write(json.dumps(result, ensure_ascii=False, default=str) + "\n")
            self._streaming_file.flush()

    # =========================================================================
    # 训练管线桩函数——仅评估模式下不使用
    # =========================================================================
    # 这些方法满足 HermesAgentBaseEnv 的抽象方法要求。
    # evaluate 子命令直接调用 setup() -> evaluate()，
    # 完全绕过训练管线。

    async def get_next_item(self):
        """返回下一个项目（桩函数——仅评估模式下不使用）。"""
        item = self.all_eval_items[self.iter % len(self.all_eval_items)]
        self.iter += 1
        return item

    def format_prompt(self, item: Dict[str, Any]) -> str:
        """将任务的指令作为用户提示返回。"""
        return item["instruction"]

    async def compute_reward(self, item, result, ctx) -> float:
        """计算奖励（桩函数——实际验证在 rollout_and_score_eval 中）。"""
        return 0.0

    async def collect_trajectories(self, item):
        """收集轨迹（桩函数——仅评估模式下不使用）。"""
        return None, []

    async def score(self, rollout_group_data):
        """评分推演（桩函数——仅评估模式下不使用）。"""
        return None

    # =========================================================================
    # Docker 镜像解析
    # =========================================================================

    def _resolve_task_image(
        self, item: Dict[str, Any], task_name: str
    ) -> Tuple[str, Optional[Path]]:
        """
        解析任务的 Docker 镜像，支持回退到 Dockerfile 构建。

        策略（与 Harbor 的方式一致）：
        1. 如果 force_build=True，始终从 environment_tar 中的 Dockerfile 构建
        2. 如果 docker_image 可用，使用预构建的 Docker Hub 镜像（快速）
        3. 否则，从 environment_tar 中提取 Dockerfile 并构建（慢速）

        返回：
            (modal_image, temp_dir) -- modal_image 是 Docker Hub 镜像名
            或 Dockerfile 路径。temp_dir 在提取了需要后续清理的文件时设置。
        """
        docker_image = item.get("docker_image", "")
        environment_tar = item.get("environment_tar", "")

        # 快速路径：使用预构建的 Docker Hub 镜像
        if docker_image and not self.config.force_build:
            logger.info("Task %s: using pre-built image %s", task_name, docker_image)
            return docker_image, None

        # 慢速路径：从 environment_tar 中提取 Dockerfile 并构建
        if environment_tar:
            task_dir = Path(tempfile.mkdtemp(prefix=f"tb2-{task_name}-"))
            _extract_base64_tar(environment_tar, task_dir)
            dockerfile_path = task_dir / "Dockerfile"
            if dockerfile_path.exists():
                logger.info(
                    "Task %s: building from Dockerfile (force_build=%s, docker_image=%s)",
                    task_name, self.config.force_build, bool(docker_image),
                )
                return str(dockerfile_path), task_dir

        # 两者都不可用——如果 force_build 为 True 则回退到 Hub 镜像
        if docker_image:
            logger.warning(
                "Task %s: force_build=True but no environment_tar, "
                "falling back to docker_image %s", task_name, docker_image,
            )
            return docker_image, None

        return "", None

    # =========================================================================
    # 单任务评估——智能体循环 + 测试验证
    # =========================================================================

    async def rollout_and_score_eval(self, eval_item: Dict[str, Any]) -> Dict:
        """
        评估单个 TB2 任务：运行智能体循环，然后通过测试验证。

        这是核心评估方法。对于每个任务：
        1. 解析 Docker 镜像并注册 Modal 沙箱覆盖配置
        2. 使用终端 + 文件工具运行 HermesAgentLoop
        3. 将测试套件上传到沙箱中
        4. 执行 test.sh 并检查结果
        5. 清理沙箱和临时文件

        参数：
            eval_item: 数据集中的单个 TB2 任务字典

        返回：
            包含 'passed'（布尔值）、'reward'（浮点数）、'task_name'（字符串）、
            'category'（字符串）和可选调试信息的字典
        """
        task_name = eval_item.get("task_name", "unknown")
        category = eval_item.get("category", "unknown")
        task_id = str(uuid.uuid4())
        task_dir = None  # 如果提取了 Dockerfile 则需要清理

        from tqdm import tqdm
        tqdm.write(f"  [START] {task_name} (task_id={task_id[:8]})")
        task_start = time.time()

        try:
            # --- 1. 解析 Docker 镜像 ---
            modal_image, task_dir = self._resolve_task_image(eval_item, task_name)
            if not modal_image:
                logger.error("Task %s: no docker_image or environment_tar, skipping", task_name)
                return {
                    "passed": False, "reward": 0.0,
                    "task_name": task_name, "category": category,
                    "error": "no_image",
                }

            # --- 2. 注册每任务的镜像覆盖配置 ---
            # 同时设置 modal_image 和 docker_image，确保无论配置哪个后端
            # 都使用正确的任务镜像。
            register_task_env_overrides(task_id, {
                "modal_image": modal_image,
                "docker_image": modal_image,
                "cwd": "/app",
            })
            logger.info(
                "Task %s: registered image override for task_id %s",
                task_name, task_id[:8],
            )

            # --- 3. 解析工具并构建消息 ---
            tools, valid_names = self._resolve_tools_for_group()

            messages: List[Dict[str, Any]] = []
            if self.config.system_prompt:
                messages.append({"role": "system", "content": self.config.system_prompt})
            messages.append({"role": "user", "content": self.format_prompt(eval_item)})

            # --- 4. 运行智能体循环 ---
            # 对 vLLM/SGLang 后端使用 ManagedServer（第 2 阶段），
            # 通过 /generate 获取 token 级别的追踪。对 OpenAI 端点
            # 回退到直接使用 ServerManager（第 1 阶段）。
            if self._use_managed_server():
                async with self.server.managed_server(
                    tokenizer=self.tokenizer,
                    preserve_think_blocks=bool(self.config.thinking_mode),
                ) as managed:
                    agent = HermesAgentLoop(
                        server=managed,
                        tool_schemas=tools,
                        valid_tool_names=valid_names,
                        max_turns=self.config.max_agent_turns,
                        task_id=task_id,
                        temperature=self.config.agent_temperature,
                        max_tokens=self.config.max_token_length,
                        extra_body=self.config.extra_body,
                        budget_config=self.config.build_budget_config(),
                    )
                    result = await agent.run(messages)
            else:
                agent = HermesAgentLoop(
                    server=self.server,
                    tool_schemas=tools,
                    valid_tool_names=valid_names,
                    max_turns=self.config.max_agent_turns,
                    task_id=task_id,
                    temperature=self.config.agent_temperature,
                    max_tokens=self.config.max_token_length,
                    extra_body=self.config.extra_body,
                    budget_config=self.config.build_budget_config(),
                )
                result = await agent.run(messages)

            # --- 5. 验证——在智能体的沙箱中运行测试套件 ---
            # 如果智能体没有产生有意义的输出则跳过验证
            only_system_and_user = all(
                msg.get("role") in ("system", "user") for msg in result.messages
            )
            if result.turns_used == 0 or only_system_and_user:
                logger.warning(
                    "Task %s: agent produced no output (turns=%d). Reward=0.",
                    task_name, result.turns_used,
                )
                reward = 0.0
            else:
                # 在线程中运行测试，避免阻塞式的 ctx.terminal() 调用
                # 冻结整个事件循环（那会阻塞所有其他任务、tqdm 更新和超时计时器）。
                ctx = ToolContext(task_id)
                try:
                    loop = asyncio.get_event_loop()
                    reward = await loop.run_in_executor(
                        None,  # 使用默认线程池
                        self._run_tests, eval_item, ctx, task_name,
                    )
                except Exception as e:
                    logger.error("Task %s: test verification failed: %s", task_name, e)
                    reward = 0.0
                finally:
                    ctx.cleanup()

            passed = reward == 1.0
            status = "PASS" if passed else "FAIL"
            elapsed = time.time() - task_start
            tqdm.write(f"  [{status}] {task_name} (turns={result.turns_used}, {elapsed:.0f}s)")
            logger.info(
                "Task %s: reward=%.1f, turns=%d, finished=%s",
                task_name, reward, result.turns_used, result.finished_naturally,
            )

            out = {
                "passed": passed,
                "reward": reward,
                "task_name": task_name,
                "category": category,
                "turns_used": result.turns_used,
                "finished_naturally": result.finished_naturally,
                "messages": result.messages,
            }
            self._save_result(out)
            return out

        except Exception as e:
            elapsed = time.time() - task_start
            logger.error("Task %s: rollout failed: %s", task_name, e, exc_info=True)
            tqdm.write(f"  [ERROR] {task_name}: {e} ({elapsed:.0f}s)")
            out = {
                "passed": False, "reward": 0.0,
                "task_name": task_name, "category": category,
                "error": str(e),
            }
            self._save_result(out)
            return out

        finally:
            # --- 清理：清除覆盖配置、沙箱和临时文件 ---
            clear_task_env_overrides(task_id)
            try:
                cleanup_vm(task_id)
            except Exception as e:
                logger.debug("VM cleanup for %s: %s", task_id[:8], e)
            if task_dir and task_dir.exists():
                shutil.rmtree(task_dir, ignore_errors=True)

    def _run_tests(
        self, item: Dict[str, Any], ctx: ToolContext, task_name: str
    ) -> float:
        """
        将测试套件上传到智能体的沙箱中并执行，然后下载验证器输出到本地读取奖励。

        遵循 Harbor 的验证模式：
        1. 将 tests/ 目录上传到沙箱中
        2. 在沙箱内执行 test.sh
        3. 将 /logs/verifier/ 目录下载到本地临时目录
        4. 使用原生 Python I/O 在本地读取 reward.txt

        下载到本地避免了 Modal VM 上 file_read 工具的问题，
        并与 Harbor 的验证方式一致。

        TB2 测试脚本（test.sh）通常：
        1. 通过 uv/pip 安装 pytest
        2. 对 /tests/ 中的测试文件运行 pytest
        3. 将结果写入 /logs/verifier/reward.txt

        参数：
            item: TB2 任务字典（包含 tests_tar、test_sh）
            ctx: 绑定到该任务沙箱的 ToolContext
            task_name: 用于日志记录

        返回：
            测试通过返回 1.0，否则返回 0.0
        """
        tests_tar = item.get("tests_tar", "")
        test_sh = item.get("test_sh", "")

        if not test_sh:
            logger.warning("Task %s: no test_sh content, reward=0", task_name)
            return 0.0

        # 在沙箱中创建所需目录
        ctx.terminal("mkdir -p /tests /logs/verifier")

        # 将测试文件上传到沙箱（通过 base64 实现二进制安全传输）
        if tests_tar:
            tests_temp = Path(tempfile.mkdtemp(prefix=f"tb2-tests-{task_name}-"))
            try:
                _extract_base64_tar(tests_tar, tests_temp)
                ctx.upload_dir(str(tests_temp), "/tests")
            except Exception as e:
                logger.warning("Task %s: failed to upload test files: %s", task_name, e)
            finally:
                shutil.rmtree(tests_temp, ignore_errors=True)

        # 写入测试运行脚本（test.sh）
        ctx.write_file("/tests/test.sh", test_sh)
        ctx.terminal("chmod +x /tests/test.sh")

        # 执行测试套件
        logger.info(
            "Task %s: running test suite (timeout=%ds)",
            task_name, self.config.test_timeout,
        )
        test_result = ctx.terminal(
            "bash /tests/test.sh",
            timeout=self.config.test_timeout,
        )

        exit_code = test_result.get("exit_code", -1)
        output = test_result.get("output", "")

        # 将验证器输出目录下载到本地，然后用原生 Python I/O 读取 reward.txt。
        # 这避免了 Modal VM 上 file_read 的问题，并与 Harbor 的验证模式一致。
        reward = 0.0
        local_verifier_dir = Path(tempfile.mkdtemp(prefix=f"tb2-verifier-{task_name}-"))
        try:
            ctx.download_dir("/logs/verifier", str(local_verifier_dir))

            reward_file = local_verifier_dir / "reward.txt"
            if reward_file.exists() and reward_file.stat().st_size > 0:
                content = reward_file.read_text().strip()
                if content == "1":
                    reward = 1.0
                elif content == "0":
                    reward = 0.0
                else:
                    # 内容非预期——尝试解析为浮点数
                    try:
                        reward = float(content)
                    except (ValueError, TypeError):
                        logger.warning(
                            "Task %s: reward.txt content unexpected (%r), "
                            "falling back to exit_code=%d",
                            task_name, content, exit_code,
                        )
                        reward = 1.0 if exit_code == 0 else 0.0
            else:
                # reward.txt 未写入——回退到退出码判断
                logger.warning(
                    "Task %s: reward.txt not found after download, "
                    "falling back to exit_code=%d",
                    task_name, exit_code,
                )
                reward = 1.0 if exit_code == 0 else 0.0
        except Exception as e:
            logger.warning(
                "Task %s: failed to download verifier dir: %s, "
                "falling back to exit_code=%d",
                task_name, e, exit_code,
            )
            reward = 1.0 if exit_code == 0 else 0.0
        finally:
            shutil.rmtree(local_verifier_dir, ignore_errors=True)

        # 记录测试输出以便调试失败原因
        if reward == 0.0:
            output_preview = output[-500:] if output else "(no output)"
            logger.info(
                "Task %s: FAIL (exit_code=%d)\n%s",
                task_name, exit_code, output_preview,
            )

        return reward

    # =========================================================================
    # 评估——evaluate 子命令的主入口
    # =========================================================================

    async def _eval_with_timeout(self, item: Dict[str, Any]) -> Dict:
        """
        为 rollout_and_score_eval 包装单任务挂钟超时。

        如果任务超过 task_timeout 秒，自动标记为 FAIL。
        防止单个任务无限挂起。
        """
        task_name = item.get("task_name", "unknown")
        category = item.get("category", "unknown")
        try:
            return await asyncio.wait_for(
                self.rollout_and_score_eval(item),
                timeout=self.config.task_timeout,
            )
        except asyncio.TimeoutError:
            from tqdm import tqdm
            elapsed = self.config.task_timeout
            tqdm.write(f"  [TIMEOUT] {task_name} (exceeded {elapsed}s wall-clock limit)")
            logger.error("Task %s: wall-clock timeout after %ds", task_name, elapsed)
            out = {
                "passed": False, "reward": 0.0,
                "task_name": task_name, "category": category,
                "error": f"timeout ({elapsed}s)",
            }
            self._save_result(out)
            return out

    async def evaluate(self, *args, **kwargs) -> None:
        """
        在所有任务上运行 Terminal-Bench 2.0 评估。

        这是通过以下命令调用时的主入口：
            python environments/terminalbench2_env.py evaluate

        通过 asyncio.gather() 对所有任务执行 rollout_and_score_eval()
        （与 GPQA 和其他 Atropos 评估环境相同的模式）。每个任务都包装了
        挂钟超时，超时任务自动标记为失败。

        抑制 Modal/终端的噪音输出（HERMES_QUIET），使 tqdm 进度条保持可见。
        """
        start_time = time.time()

        # 将所有日志通过 tqdm.write() 输出，使进度条固定在底部，
        # 日志行在上方滚动。
        from tqdm import tqdm

        class _TqdmHandler(logging.Handler):
            def emit(self, record):
                try:
                    tqdm.write(self.format(record))
                except Exception:
                    self.handleError(record)

        handler = _TqdmHandler()
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(name)s] %(levelname)s: %(message)s",
            datefmt="%H:%M:%S",
        ))
        root = logging.getLogger()
        root.handlers = [handler]  # 替换所有现有的处理器
        root.setLevel(logging.INFO)

        # 静默噪音过多的第三方日志记录器
        logging.getLogger("httpx").setLevel(logging.WARNING)      # 每个 HTTP 请求
        logging.getLogger("openai").setLevel(logging.WARNING)     # OpenAI 客户端重试
        logging.getLogger("rex-deploy").setLevel(logging.WARNING) # Swerex 部署
        logging.getLogger("rex_image_builder").setLevel(logging.WARNING)  # 镜像构建

        print(f"\n{'='*60}")
        print("Starting Terminal-Bench 2.0 Evaluation")
        print(f"{'='*60}")
        print(f"  Dataset: {self.config.dataset_name}")
        print(f"  Total tasks: {len(self.all_eval_items)}")
        print(f"  Max agent turns: {self.config.max_agent_turns}")
        print(f"  Task timeout: {self.config.task_timeout}s")
        print(f"  Terminal backend: {self.config.terminal_backend}")
        print(f"  Tool thread pool: {self.config.tool_pool_size}")
        print(f"  Terminal timeout: {self.config.terminal_timeout}s/cmd")
        print(f"  Terminal lifetime: {self.config.terminal_lifetime}s (auto: task_timeout + 120)")
        print(f"  Max concurrent tasks: {self.config.max_concurrent_tasks}")
        print(f"{'='*60}\n")

        # 信号量控制并发的 Modal 沙箱创建。
        # 如果不限制，所有 86 个任务会同时启动，每个都在线程池工作线程中
        # 通过 asyncio.run() 创建 Modal 沙箱。Modal 的阻塞调用
        # （App.lookup 等）在同时创建过多沙箱时会在线程池中死锁。
        semaphore = asyncio.Semaphore(self.config.max_concurrent_tasks)

        async def _eval_with_semaphore(item):
            async with semaphore:
                return await self._eval_with_timeout(item)

        # 启动所有任务（带挂钟超时），在进度条上实时跟踪准确率
        total_tasks = len(self.all_eval_items)
        eval_tasks = [
            asyncio.ensure_future(_eval_with_semaphore(item))
            for item in self.all_eval_items
        ]

        results = []
        passed_count = 0
        pbar = tqdm(total=total_tasks, desc="Evaluating TB2", dynamic_ncols=True)
        try:
            for coro in asyncio.as_completed(eval_tasks):
                result = await coro
                results.append(result)
                if result and result.get("passed"):
                    passed_count += 1
                done = len(results)
                pct = (passed_count / done * 100) if done else 0
                pbar.set_postfix_str(f"pass={passed_count}/{done} ({pct:.1f}%)")
                pbar.update(1)
        except (KeyboardInterrupt, asyncio.CancelledError):
            pbar.close()
            print(f"\n\nInterrupted! Cleaning up {len(eval_tasks)} tasks...")
            # 取消所有待执行的任务
            for task in eval_tasks:
                task.cancel()
            # 等待取消传播（finally 块会运行 cleanup_vm）
            await asyncio.gather(*eval_tasks, return_exceptions=True)
            # 双重保险：清理所有剩余沙箱
            from tools.terminal_tool import cleanup_all_environments
            cleanup_all_environments()
            print("All sandboxes cleaned up.")
            return
        finally:
            pbar.close()

        end_time = time.time()

        # 过滤掉 None 结果（不应发生，但做好防御）
        valid_results = [r for r in results if r is not None]

        if not valid_results:
            print("Warning: No valid evaluation results obtained")
            return

        # ---- 计算指标 ----
        total = len(valid_results)
        passed = sum(1 for r in valid_results if r.get("passed"))
        overall_pass_rate = passed / total if total > 0 else 0.0

        # 按类别分组
        cat_results: Dict[str, List[Dict]] = defaultdict(list)
        for r in valid_results:
            cat_results[r.get("category", "unknown")].append(r)

        # 构建指标字典
        eval_metrics = {
            "eval/pass_rate": overall_pass_rate,
            "eval/total_tasks": total,
            "eval/passed_tasks": passed,
            "eval/evaluation_time_seconds": end_time - start_time,
        }

        # 按类别统计指标
        for category, cat_items in sorted(cat_results.items()):
            cat_passed = sum(1 for r in cat_items if r.get("passed"))
            cat_total = len(cat_items)
            cat_pass_rate = cat_passed / cat_total if cat_total > 0 else 0.0
            cat_key = category.replace(" ", "_").replace("-", "_").lower()
            eval_metrics[f"eval/pass_rate_{cat_key}"] = cat_pass_rate

        # 存储指标供 wandb_log 使用
        self.eval_metrics = [(k, v) for k, v in eval_metrics.items()]

        # ---- 打印摘要 ----
        print(f"\n{'='*60}")
        print("Terminal-Bench 2.0 Evaluation Results")
        print(f"{'='*60}")
        print(f"Overall Pass Rate: {overall_pass_rate:.4f} ({passed}/{total})")
        print(f"Evaluation Time: {end_time - start_time:.1f} seconds")

        print("\nCategory Breakdown:")
        for category, cat_items in sorted(cat_results.items()):
            cat_passed = sum(1 for r in cat_items if r.get("passed"))
            cat_total = len(cat_items)
            cat_rate = cat_passed / cat_total if cat_total > 0 else 0.0
            print(f"  {category}: {cat_rate:.1%} ({cat_passed}/{cat_total})")

        # 打印各任务结果
        print("\nTask Results:")
        for r in sorted(valid_results, key=lambda x: x.get("task_name", "")):
            status = "PASS" if r.get("passed") else "FAIL"
            turns = r.get("turns_used", "?")
            error = r.get("error", "")
            extra = f" (error: {error})" if error else ""
            print(f"  [{status}] {r['task_name']} (turns={turns}){extra}")

        print(f"{'='*60}\n")

        # 构建样本记录用于 evaluate_log（包含完整对话）
        samples = [
            {
                "task_name": r.get("task_name"),
                "category": r.get("category"),
                "passed": r.get("passed"),
                "reward": r.get("reward"),
                "turns_used": r.get("turns_used"),
                "error": r.get("error"),
                "messages": r.get("messages"),
            }
            for r in valid_results
        ]

        # 记录评估结果
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
                    "terminal_backend": self.config.terminal_backend,
                },
            )
        except Exception as e:
            print(f"Error logging evaluation results: {e}")

        # 关闭流式文件
        if hasattr(self, "_streaming_file") and not self._streaming_file.closed:
            self._streaming_file.close()
            print(f"  Live results saved to: {self._streaming_path}")

        # 终止所有剩余沙箱。超时任务会留下孤立的线程池工作线程
        # 仍在执行命令——cleanup_all 会停止它们。
        from tools.terminal_tool import cleanup_all_environments
        print("\nCleaning up all sandboxes...")
        cleanup_all_environments()

        # 关闭工具线程池，使超时任务留下的孤立工作线程立即停止，
        # 而不是继续对已销毁的沙箱重试并在控制台中刷出 TimeoutError 警告。
        from environments.agent_loop import _tool_executor
        _tool_executor.shutdown(wait=False, cancel_futures=True)
        print("Done.")

    # =========================================================================
    # Wandb 日志记录
    # =========================================================================

    async def wandb_log(self, wandb_metrics: Optional[Dict] = None):
        """将 TB2 特有的指标记录到 wandb。"""
        if wandb_metrics is None:
            wandb_metrics = {}

        # 添加存储的评估指标
        for metric_name, metric_value in self.eval_metrics:
            wandb_metrics[metric_name] = metric_value
        self.eval_metrics = []

        await super().wandb_log(wandb_metrics)


if __name__ == "__main__":
    TerminalBench2EvalEnv.cli()
