"""
HermesAgentBaseEnv -- Hermes-Agent + Atropos 的抽象基础环境

提供所有 hermes-agent 环境共享的 Atropos 集成基础设施：
- 双模式运行（第一阶段使用 OpenAI 服务器，第二阶段使用 VLLM ManagedServer）
- 每组工具集/分布解析
- 通过 HermesAgentLoop 编排智能体循环
- 为奖励函数创建 ToolContext
- 从 ManagedServer 状态构建 ScoredDataGroup

子类只需要实现：
    setup()           -- 加载数据集，初始化状态
    get_next_item()   -- 从数据集返回下一条数据
    format_prompt()   -- 将数据集条目转换为用户消息
    compute_reward()  -- 评分 rollout（具有完整的 ToolContext 访问权限）
    evaluate()        -- 定期评估
"""

import asyncio
import json
import logging
import os
import sys
import uuid
from abc import abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

# 确保 hermes-agent 仓库根目录在 sys.path 中，以便
# `from model_tools import ...` 和 `from environments.X import ...` 等导入
# 无论脚本从哪里调用都能正常工作。
_repo_root = Path(__file__).resolve().parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from dotenv import load_dotenv
from pydantic import Field

# 从 hermes-agent/.env 加载 API 密钥，使所有环境都能访问
_env_path = _repo_root / ".env"
if _env_path.exists():
    load_dotenv(dotenv_path=_env_path)

# 应用猴子补丁以实现 Atropos 事件循环内的异步安全工具操作。
# 这会给 SwerexModalEnvironment 打补丁，使用后台线程而非 asyncio.run()，
# 后者会在 Atropos 内部死锁。对普通 CLI 也是安全的。
from environments.patches import apply_patches
apply_patches()

from atroposlib.envs.base import (
    BaseEnv,
    BaseEnvConfig,
    ScoredDataGroup,
    ScoredDataItem,
)
from atroposlib.envs.server_handling.server_manager import (
    APIServerConfig,
    ServerBaseline,
    ServerManager,
)
from atroposlib.type_definitions import Item

from environments.agent_loop import AgentResult, HermesAgentLoop
from environments.tool_context import ToolContext
from tools.budget_config import (
    DEFAULT_RESULT_SIZE_CHARS,
    DEFAULT_TURN_BUDGET_CHARS,
    DEFAULT_PREVIEW_SIZE_CHARS,
)

# 导入 hermes-agent 工具集基础设施
from model_tools import get_tool_definitions
from toolset_distributions import sample_toolsets_from_distribution

logger = logging.getLogger(__name__)


class HermesAgentEnvConfig(BaseEnvConfig):
    """
    hermes-agent Atropos 环境的配置。

    在 BaseEnvConfig 基础上扩展了工具集、终端后端、数据集加载
    和工具调用解析的智能体特定设置。
    """

    # --- 工具集配置 ---
    # 互斥：使用 enabled_toolsets 或 distribution 其中之一
    enabled_toolsets: Optional[List[str]] = Field(
        default=None,
        description="Explicit list of hermes toolsets to enable (e.g., ['terminal', 'file', 'web']). "
        "If None and distribution is also None, all available toolsets are enabled.",
    )
    disabled_toolsets: Optional[List[str]] = Field(
        default=None,
        description="Toolsets to disable. Applied as a filter on top of enabled_toolsets or distribution.",
    )
    distribution: Optional[str] = Field(
        default=None,
        description="Name of a toolset distribution from toolset_distributions.py "
        "(e.g., 'development', 'terminal_tasks'). Sampled once per group. "
        "Mutually exclusive with enabled_toolsets.",
    )

    # --- 智能体循环配置 ---
    max_agent_turns: int = Field(
        default=30,
        description="Maximum number of LLM calls (tool-calling iterations) per rollout.",
    )
    system_prompt: Optional[str] = Field(
        default=None,
        description="System prompt for the agent. Tools are handled via the tools= parameter, "
        "not embedded in the prompt text.",
    )
    agent_temperature: float = Field(
        default=1.0,
        description="Sampling temperature for agent generation during rollouts.",
    )

    # --- 终端后端 ---
    terminal_backend: str = Field(
        default="local",
        description="Terminal backend: 'local', 'docker', 'modal', 'daytona', 'ssh', 'singularity'. "
        "Modal or Daytona recommended for production RL (cloud isolation per rollout).",
    )
    terminal_timeout: int = Field(
        default=120,
        description="Per-command timeout in seconds for terminal tool calls. "
        "Commands exceeding this are killed. Increase for tasks with long-running "
        "commands (compilation, pip install, etc.).",
    )
    terminal_lifetime: int = Field(
        default=3600,
        description="Sandbox inactivity lifetime in seconds. The cleanup thread kills "
        "sandboxes that have been idle longer than this. Must be longer than "
        "the longest gap between tool calls (e.g., waiting for LLM response).",
    )

    # --- 数据集 ---
    dataset_split: str = Field(
        default="train",
        description="Dataset split to use.",
    )
    prompt_field: str = Field(
        default="prompt",
        description="Which field in the dataset contains the prompt.",
    )

    # --- 线程池 ---
    tool_pool_size: int = Field(
        default=128,
        description="Thread pool size for tool execution. Each concurrent task needs a "
        "thread for tool calls. Must be large enough for parallel evaluation. "
        "Too small = thread pool starvation.",
    )

    # --- 第二阶段：工具调用解析 ---
    tool_call_parser: str = Field(
        default="hermes",
        description="Tool call parser name for Phase 2 (VLLM server type). "
        "Ignored in Phase 1 (OpenAI server type where VLLM parses natively). "
        "Options: hermes, mistral, llama3_json, qwen, deepseek_v3, etc.",
    )

    # --- 工具结果预算 ---
    # 默认值从 tools.budget_config 导入（单一事实来源）。
    default_result_size_chars: int = Field(
        default=DEFAULT_RESULT_SIZE_CHARS,
        description="Default per-tool threshold (chars) for persisting large results "
        "to sandbox. Results exceeding this are written to /tmp/hermes-results/ "
        "and replaced with a preview. Per-tool registry values take precedence "
        "unless overridden via tool_result_overrides.",
    )
    turn_budget_chars: int = Field(
        default=DEFAULT_TURN_BUDGET_CHARS,
        description="Aggregate char budget per assistant turn. If all tool results "
        "in a single turn exceed this, the largest are persisted to disk first.",
    )
    preview_size_chars: int = Field(
        default=DEFAULT_PREVIEW_SIZE_CHARS,
        description="Size of the inline preview shown after a tool result is persisted.",
    )
    tool_result_overrides: Optional[Dict[str, int]] = Field(
        default=None,
        description="Per-tool threshold overrides (chars). Keys are tool names, "
        "values are char thresholds. Overrides both the default and registry "
        "per-tool values. Example: {'terminal': 10000, 'search_files': 5000}. "
        "Note: read_file is pinned to infinity and cannot be overridden.",
    )

    # --- 提供商特定参数 ---
    # 作为 extra_body 传递给 OpenAI 客户端的 chat.completions.create() 调用。
    # 用于 OpenRouter 提供商偏好、转换、路由设置等。
    # YAML 示例：
    #   extra_body:
    #     provider:
    #       ignore: ["DeepInfra", "Fireworks"]
    #       order: ["Together"]
    #     transforms: ["middle-out"]
    extra_body: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Extra body parameters passed to the OpenAI client's "
        "chat.completions.create(). Used for OpenRouter provider preferences, "
        "transforms, and other provider-specific settings.",
    )

    def build_budget_config(self):
        """从环境配置字段构建 BudgetConfig。"""
        from tools.budget_config import BudgetConfig
        return BudgetConfig(
            default_result_size=self.default_result_size_chars,
            turn_budget=self.turn_budget_chars,
            preview_size=self.preview_size_chars,
            tool_overrides=dict(self.tool_result_overrides) if self.tool_result_overrides else {},
        )


class HermesAgentBaseEnv(BaseEnv):
    """
    hermes-agent Atropos 集成的抽象基础环境。

    处理两种运行模式：
    - 第一阶段（OpenAI 服务器类型）：直接使用 server.chat_completion()。
      服务器（VLLM、SGLang、OpenRouter、OpenAI）原生处理工具调用解析
      和推理提取。DummyManagedServer 提供占位 token。适合 SFT 数据生成、
      验证器测试和评估。

    - 第二阶段（VLLM 服务器类型）：使用 ManagedServer 通过 /generate
      获取精确的 token ID + logprobs。客户端工具调用解析器从原始输出
      重建结构化的 tool_calls。具有完整的 RL 训练能力。

    子类必须实现：
        setup()           -- 加载数据集，初始化状态
        get_next_item()   -- 返回下一个要进行 rollout 的条目
        format_prompt()   -- 将数据集条目转换为用户消息字符串
        compute_reward()  -- 使用 ToolContext 评分 rollout
        evaluate()        -- 定期评估
    """

    name: Optional[str] = "hermes-agent"
    env_config_cls = HermesAgentEnvConfig

    def __init__(
        self,
        config: HermesAgentEnvConfig,
        server_configs: Union[ServerBaseline, List[APIServerConfig]],
        slurm=False,
        testing=False,
    ):
        super().__init__(config, server_configs, slurm, testing)

        # 设置终端环境变量，以便 hermes 工具能读取它们。
        # 这些都可以通过配置字段按环境覆盖，
        # 而不需要用户设置 shell 环境变量。
        if config.terminal_backend:
            os.environ["TERMINAL_ENV"] = config.terminal_backend
        os.environ["TERMINAL_TIMEOUT"] = str(config.terminal_timeout)
        os.environ["TERMINAL_LIFETIME_SECONDS"] = str(config.terminal_lifetime)
        print(
            f"🖥️  Terminal: backend={config.terminal_backend}, "
            f"timeout={config.terminal_timeout}s, lifetime={config.terminal_lifetime}s"
        )

        # 调整智能体循环的工具执行线程池大小。
        # 必须足够大以支持并发任务数量
        # （例如 89 个并行 TB2 评估任务每个都需要一个线程执行工具调用）。
        from environments.agent_loop import resize_tool_pool
        resize_tool_pool(config.tool_pool_size)

        # 在 ServerManager 上设置 tool_parser，使 ManagedServer 用它
        # 进行双向工具调用转换（原始文本 <-> OpenAI tool_calls）。
        if hasattr(self.server, 'tool_parser'):
            self.server.tool_parser = config.tool_call_parser
            print(f"🔧 Tool parser: {config.tool_call_parser}")

        # 当前组已解析的工具（在 collect_trajectories 中设置）
        self._current_group_tools: Optional[Tuple[List[Dict], Set[str]]] = None

        # 工具错误跟踪，用于 wandb 日志记录
        self._tool_error_buffer: List[Dict[str, Any]] = []

    # =========================================================================
    # 工具集解析（按组）
    # =========================================================================

    def _resolve_tools_for_group(self) -> Tuple[List[Dict[str, Any]], Set[str]]:
        """
        为一个组解析工具集。在 collect_trajectories() 中调用一次，
        然后由该组中所有 collect_trajectory() 调用共享。

        如果设置了 distribution，则按概率采样。
        如果设置了 enabled_toolsets，则使用该显式列表。
        disabled_toolsets 作为过滤器应用在上面。

        返回：
            (tool_schemas, valid_tool_names) 元组
        """
        config = self.config

        if config.distribution:
            group_toolsets = sample_toolsets_from_distribution(config.distribution)
            logger.info("Sampled toolsets from '%s': %s", config.distribution, group_toolsets)
        else:
            group_toolsets = config.enabled_toolsets  # None 表示"所有可用"
            if group_toolsets is None:
                logger.warning(
                    "enabled_toolsets is None -- loading ALL tools including messaging. "
                    "Set explicit enabled_toolsets for RL training."
                )

        tools = get_tool_definitions(
            enabled_toolsets=group_toolsets,
            disabled_toolsets=config.disabled_toolsets,
            quiet_mode=True,
        )

        valid_names = {t["function"]["name"] for t in tools} if tools else set()
        logger.info("Resolved %d tools for group: %s", len(valid_names), sorted(valid_names))
        return tools, valid_names

    # =========================================================================
    # 服务器模式检测
    # =========================================================================

    def _use_managed_server(self) -> bool:
        """
        判断应使用 ManagedServer（第二阶段）还是直连服务器（第一阶段）。

        第二阶段（ManagedServer）用于 'vllm' 或 'sglang' 服务器类型，
        它们通过 /generate 端点实现精确的 token 跟踪。

        第一阶段（直连服务器）用于 'openai' 服务器类型，它使用
        /v1/chat/completions 并原生解析工具调用。
        """
        if not self.server.servers:
            return False

        server = self.server.servers[0]
        # 如果服务器是 OpenAI 服务器（非 VLLM/SGLang），使用直连模式
        from atroposlib.envs.server_handling.openai_server import OpenAIServer
        return not isinstance(server, OpenAIServer)

    # =========================================================================
    # 核心 Atropos 集成
    # =========================================================================

    async def collect_trajectories(
        self, item: Item
    ) -> Tuple[
        Union[Optional[ScoredDataGroup], List[Optional[ScoredDataGroup]]],
        List[Item],
    ]:
        """
        重写 collect_trajectories 以在每组解析一次工具集，
        然后委托给标准的组级收集。

        默认的 BaseEnv.collect_trajectories() 会并行调用 collect_trajectory()
        group_size 次。我们在这里一次性解析工具，并存储供所有调用使用。
        """
        # 为此组解析工具集（由组中所有 rollout 共享）
        self._current_group_tools = self._resolve_tools_for_group()

        # 委托给默认实现，它会通过 asyncio.gather 调用
        # collect_trajectory() group_size 次
        return await super().collect_trajectories(item)

    # =========================================================================
    # Wandb rollout 显示 -- 美化格式化轨迹
    # =========================================================================

    @staticmethod
    def _format_trajectory_for_display(messages: List[Dict[str, Any]]) -> str:
        """
        将对话消息格式化为可读的轨迹字符串，用于 wandb rollout 表格。
        以结构化方式显示工具调用、工具结果和推理，
        而非原始 token 解码。
        """
        parts = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")

            if role == "system":
                parts.append(f"[SYSTEM]\n{content}")

            elif role == "user":
                parts.append(f"[USER]\n{content}")

            elif role == "assistant":
                # 如果存在推理内容则显示
                reasoning = msg.get("reasoning_content", "")
                if reasoning:
                    # 截断过长的推理内容用于显示
                    if len(reasoning) > 300:
                        reasoning = reasoning[:300] + "..."
                    parts.append(f"[ASSISTANT thinking]\n{reasoning}")

                # 显示内容
                if content:
                    parts.append(f"[ASSISTANT]\n{content}")

                # 显示工具调用
                tool_calls = msg.get("tool_calls", [])
                for tc in tool_calls:
                    func = tc.get("function", {})
                    name = func.get("name", "?")
                    args = func.get("arguments", "{}")
                    # 截断过长的参数用于显示
                    if len(args) > 200:
                        args = args[:200] + "..."
                    parts.append(f"[TOOL CALL] {name}({args})")

            elif role == "tool":
                tool_id = msg.get("tool_call_id", "")
                result = content
                # 截断过长的工具结果用于显示
                if len(result) > 500:
                    result = result[:500] + "..."
                parts.append(f"[TOOL RESULT] {result}")

        return "\n\n".join(parts)

    async def add_rollouts_for_wandb(
        self,
        scored_data,
        item=None,
    ):
        """
        重写此方法以显示格式化的带工具调用的轨迹，
        而非丢失所有结构的原始 token 解码。
        """
        num_keep = self.config.num_rollouts_per_group_for_logging
        if num_keep == -1:
            num_keep = self.config.group_size

        group = []
        for i in range(min(num_keep, len(scored_data.get("scores", [])))):
            score = scored_data["scores"][i]

            # 如果有可用的 messages 则使用富文本显示
            messages = None
            if scored_data.get("messages") and i < len(scored_data["messages"]):
                messages = scored_data["messages"][i]

            if messages:
                text = self._format_trajectory_for_display(messages)
            elif scored_data.get("tokens") and i < len(scored_data["tokens"]):
                text = self.tokenizer.decode(scored_data["tokens"][i])
            else:
                text = "(no data)"

            group.append((text, score))

        self.rollouts_for_wandb.append(group)
        if len(self.rollouts_for_wandb) > self.config.num_rollouts_to_keep:
            self.rollouts_for_wandb.pop(0)

    async def wandb_log(self, wandb_metrics: Optional[Dict] = None):
        """将基础指标（包括工具错误）记录到 wandb。"""
        if wandb_metrics is None:
            wandb_metrics = {}

        # 记录工具错误统计信息
        if self._tool_error_buffer:
            wandb_metrics["train/tool_errors_count"] = len(self._tool_error_buffer)

            # 将错误详情记录为摘要字符串（表格可能导致 wandb 在临时文件清理时崩溃）
            error_summaries = []
            for err in self._tool_error_buffer:
                error_summaries.append(
                    f"[turn {err['turn']}] {err['tool']}({err['args'][:80]}) -> {err['error'][:150]}"
                )
            wandb_metrics["train/tool_error_details"] = "\n".join(error_summaries)

            # 同时打印到标准输出以便即时可见
            for summary in error_summaries:
                print(f"  Tool Error: {summary}")

            self._tool_error_buffer = []
        else:
            wandb_metrics["train/tool_errors_count"] = 0

        await super().wandb_log(wandb_metrics)

    async def collect_trajectory(
        self, item: Item
    ) -> Tuple[Optional[Union[ScoredDataItem, Any]], List[Item]]:
        """
        运行单次 rollout：智能体循环 + 奖励计算。

        由 collect_trajectories() 并行调用 group_size 次。
        每次调用获得自己的 task_id 用于终端/浏览器会话隔离。
        """
        task_id = str(uuid.uuid4())

        # 获取组级别的工具（在 collect_trajectories 中一次性解析）
        if self._current_group_tools is None:
            # 回退：如果在 collect_trajectories 外部调用则按轨迹解析
            tools, valid_names = self._resolve_tools_for_group()
        else:
            tools, valid_names = self._current_group_tools

        # 构建初始消息
        messages: List[Dict[str, Any]] = []
        if self.config.system_prompt:
            messages.append({"role": "system", "content": self.config.system_prompt})
        messages.append({"role": "user", "content": self.format_prompt(item)})

        # 运行智能体循环
        result: AgentResult
        if self._use_managed_server():
            # 第二阶段：ManagedServer 配合 ToolCallTranslator -- 精确 tokens + logprobs
            # tool_parser 在 __init__ 中设置在 ServerManager 上并传递到
            # ManagedServer，后者使用 ToolCallTranslator 进行原始文本
            # 和 OpenAI tool_calls 之间的双向转换。
            try:
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
            except NotImplementedError:
                # DummyManagedServer 不允许 -- 回退到第一阶段
                logger.warning(
                    "ManagedServer not available (OpenAI server?). "
                    "Falling back to direct server mode."
                )
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
        else:
            # 第一阶段：OpenAI 服务器 -- 原生 tool_calls，占位 tokens
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

        # 如果智能体循环没有产生有意义的输出则跳过奖励计算
        # （例如第一轮 API 调用就失败了）。没必要启动 Modal 沙箱
        # 去验证从未创建的文件。
        only_system_and_user = all(
            msg.get("role") in ("system", "user") for msg in result.messages
        )
        if result.turns_used == 0 or only_system_and_user:
            logger.warning(
                "Agent loop produced no output (turns=%d, msgs=%d). Skipping reward.",
                result.turns_used, len(result.messages),
            )
            reward = 0.0
        else:
            # 使用 ToolContext 计算奖励（给验证器完整的工具访问权限）
            ctx = ToolContext(task_id)
            try:
                reward = await self.compute_reward(item, result, ctx)
            except Exception as e:
                logger.error("compute_reward failed: %s", e)
                reward = 0.0
            finally:
                ctx.cleanup()

        # 跟踪工具错误用于 wandb 日志记录
        if result.tool_errors:
            for err in result.tool_errors:
                self._tool_error_buffer.append({
                    "turn": err.turn,
                    "tool": err.tool_name,
                    "args": err.arguments[:150],
                    "error": err.error[:300],
                    "result": err.tool_result[:300],
                })

        # 从 ManagedServer 状态构建 ScoredDataItem
        # 第二阶段：来自 SequenceNodes 的真实 tokens/masks/logprobs
        # 第一阶段：占位 tokens（仍需要有效的 ScoredDataItem 供管道使用）
        nodes = (result.managed_state or {}).get("nodes", [])

        if nodes:
            # 第二阶段（或 DummyManagedServer）：使用实际节点数据
            node = nodes[-1]  # 最终序列节点 = 完整轨迹
            scored_item: Dict[str, Any] = {
                "tokens": node.tokens,
                "masks": node.masked_tokens,
                "scores": reward,
            }

            # 包含 logprobs（如果可用，第二阶段）
            if hasattr(node, "logprobs") and node.logprobs:
                scored_item["advantages"] = None  # 由训练器计算
                scored_item["ref_logprobs"] = None
        else:
            # 第一阶段没有管理状态：创建占位 tokens
            # 使数据管道不会中断。这些不适合训练，
            # 但允许 process 模式（SFT 数据生成）正常工作。
            # 将完整对话进行分词以获取近似 tokens。
            full_text = "\n".join(
                msg.get("content", "") for msg in result.messages if msg.get("content")
            )
            if self.tokenizer:
                tokens = self.tokenizer.encode(full_text, add_special_tokens=True)
            else:
                tokens = list(range(min(len(full_text) // 4, 128)))

            scored_item = {
                "tokens": tokens,
                "masks": [-100] + tokens[1:],  # 将第一个 token 作为 prompt 掩码
                "scores": reward,
            }

        # 始终包含 messages 用于 wandb rollout 显示和数据记录
        scored_item["messages"] = result.messages

        return scored_item, []

    # =========================================================================
    # 抽象方法 -- 子类必须实现
    # =========================================================================

    @abstractmethod
    async def setup(self):
        """
        加载数据集，初始化状态。

        环境启动时调用一次。典型实现：
            self.dataset = load_dataset(self.config.dataset_name, split=self.config.dataset_split)
            self.iter = 0
        """
        raise NotImplementedError

    @abstractmethod
    async def get_next_item(self) -> Item:
        """
        从数据集返回下一条用于 rollout 的数据。

        由基础环境的主循环调用以为工作者获取条目。
        应循环遍历数据集。
        """
        raise NotImplementedError

    @abstractmethod
    def format_prompt(self, item: Item) -> str:
        """
        将数据集条目转换为智能体的用户消息。

        参数：
            item: 数据集条目（字典、元组等）

        返回：
            发送给智能体的提示字符串
        """
        raise NotImplementedError

    @abstractmethod
    async def compute_reward(
        self, item: Item, result: AgentResult, ctx: ToolContext
    ) -> float:
        """
        评分 rollout。可以完整访问：
        - item: 原始数据集条目（真实标签、测试命令等）
        - result: 包含完整消息、轮次数、推理等的 AgentResult
        - ctx: ToolContext -- 调用任何 hermes-agent 工具（终端、文件、网页、
               浏览器、视觉……），作用域限定在此 rollout 的沙箱。
               没有任何限制。

        参数：
            item: 进行 rollout 的数据集条目
            result: 智能体的 rollout 结果
            ctx: 具有完整工具访问权限的 ToolContext 用于验证

        返回：
            奖励浮点数（通常 0.0 到 1.0，但任何浮点数都有效）
        """
        raise NotImplementedError

    @abstractmethod
    async def evaluate(self, *args, **kwargs):
        """
        定期评估。每 steps_per_eval 步调用一次。

        典型实现在保留的评估集上运行智能体，
        并通过 wandb/evaluate_log 记录指标。
        """
        raise NotImplementedError
