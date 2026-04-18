"""
HermesAgentLoop -- 可复用的多轮智能体引擎

使用标准 OpenAI 规范的工具调用运行 hermes-agent 的工具调用循环。
兼容任何返回 ChatCompletion 对象（包含 tool_calls）的服务器：
    - 第一阶段：OpenAI 服务器类型（VLLM、SGLang、OpenRouter、OpenAI API）
    - 第二阶段：ManagedServer 配合客户端工具调用解析器

循环通过 tools= 传递工具定义，并检查 response.choices[0].message.tool_calls，
与 hermes-agent 的 run_agent.py 行为一致。工具执行通过 model_tools.py 中的
handle_function_call() 进行分派。
"""

import asyncio
import concurrent.futures
import json
import logging
import os
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from model_tools import handle_function_call
from tools.terminal_tool import get_active_env
from tools.tool_result_storage import maybe_persist_tool_result, enforce_turn_budget

# 用于运行同步工具调用的线程池，这些调用内部使用 asyncio.run()
# （例如 Modal/Docker/Daytona 终端后端）。在单独的线程中运行可以给它们
# 一个干净的事件循环，避免在 Atropos 的循环内部死锁。
# 线程池大小必须足够大以支持并发评估任务（例如 89 个 TB2 任务同时发起工具调用）。
# 太小 = 线程池饥饿，任务排队等待数分钟。
# 在运行时由 HermesAgentBaseEnv.__init__ 通过 resize_tool_pool() 调整大小。
_tool_executor = concurrent.futures.ThreadPoolExecutor(max_workers=128)


def resize_tool_pool(max_workers: int):
    """
    用指定大小的新线程池替换全局工具执行器。

    由 HermesAgentBaseEnv.__init__ 根据 config.tool_pool_size 调用。
    在提交任何任务之前调用是安全的。
    """
    global _tool_executor
    old_executor = _tool_executor
    _tool_executor = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)
    old_executor.shutdown(wait=False)
    logger.info("Tool thread pool resized to %d workers", max_workers)

logger = logging.getLogger(__name__)


@dataclass
class ToolError:
    """智能体循环中工具执行错误的记录。"""

    turn: int                  # 错误发生在哪一轮
    tool_name: str             # 调用了哪个工具
    arguments: str             # 传递的参数（截断后）
    error: str                 # 错误消息
    tool_result: str           # 返回给模型的原始结果


@dataclass
class AgentResult:
    """运行智能体循环的结果。"""

    # OpenAI 消息格式的完整对话历史
    messages: List[Dict[str, Any]]
    # ManagedServer.get_state() 的返回值（第二阶段可用），否则为 None
    managed_state: Optional[Dict[str, Any]] = None
    # 进行了多少次 LLM 调用
    turns_used: int = 0
    # 如果模型自然停止调用工具（而非达到 max_turns 上限）则为 True
    finished_naturally: bool = False
    # 每轮提取的推理内容（来自 PR #297 辅助函数）
    reasoning_per_turn: List[Optional[str]] = field(default_factory=list)
    # 循环期间遇到的工具错误
    tool_errors: List[ToolError] = field(default_factory=list)


def _extract_reasoning_from_message(message) -> Optional[str]:
    """
    从 ChatCompletion 消息中提取推理内容。

    处理多种提供商格式：
    1. message.reasoning_content 字段（部分提供商）
    2. message.reasoning 字段（部分提供商）
    3. message.reasoning_details[].text（OpenRouter 风格）

    注意：这里不从 content 中提取 <think> 块 -- 在第一阶段由服务器处理，
    在第二阶段由 ManagedServer 的补丁处理。

    参数：
        message: ChatCompletion 响应中的助手消息

    返回：
        提取的推理文本，如果未找到则返回 None
    """
    # 检查 reasoning_content 字段（各提供商通用）
    if hasattr(message, "reasoning_content") and message.reasoning_content:
        return message.reasoning_content

    # 检查 reasoning 字段
    if hasattr(message, "reasoning") and message.reasoning:
        return message.reasoning

    # 检查 reasoning_details（OpenRouter 风格）
    if hasattr(message, "reasoning_details") and message.reasoning_details:
        for detail in message.reasoning_details:
            if hasattr(detail, "text") and detail.text:
                return detail.text
            if isinstance(detail, dict) and detail.get("text"):
                return detail["text"]

    return None


class HermesAgentLoop:
    """
    使用标准 OpenAI 规范的工具调用运行 hermes-agent 的工具调用循环。

    与 run_agent.py 相同的模式：
    - 将 tools= 传递给 API
    - 检查 response.choices[0].message.tool_calls
    - 通过 handle_function_call() 分派执行

    兼容任何服务器类型 -- OpenAI、VLLM、SGLang、OpenRouter 或带解析器的
    ManagedServer。服务器决定如何在响应中填充 tool_calls。
    """

    def __init__(
        self,
        server,
        tool_schemas: List[Dict[str, Any]],
        valid_tool_names: Set[str],
        max_turns: int = 30,
        task_id: Optional[str] = None,
        temperature: float = 1.0,
        max_tokens: Optional[int] = None,
        extra_body: Optional[Dict[str, Any]] = None,
        budget_config: Optional["BudgetConfig"] = None,
    ):
        """
        初始化智能体循环。

        参数：
            server: 具有 chat_completion() 方法的服务器对象（OpenAIServer、
                    ManagedServer、ServerManager 等）
            tool_schemas: 来自 get_tool_definitions() 的 OpenAI 格式工具定义
            valid_tool_names: 模型允许调用的工具名称集合
            max_turns: 停止前的最大 LLM 调用次数
            task_id: 用于终端/浏览器会话隔离的唯一 ID
            temperature: 生成时的采样温度
            max_tokens: 每次生成的最大 token 数（None 使用服务器默认值）
            extra_body: 传递给 OpenAI 客户端 create() 调用的额外参数。
                        用于 OpenRouter 提供商偏好、转换等。
                        例如 {"provider": {"ignore": ["DeepInfra"]}}
            budget_config: 工具结果持久化预算。控制每个工具的阈值、
                        每轮聚合预算和预览大小。
                        如果为 None，使用 DEFAULT_BUDGET（当前硬编码值）。
        """
        from tools.budget_config import DEFAULT_BUDGET
        self.server = server
        self.tool_schemas = tool_schemas
        self.valid_tool_names = valid_tool_names
        self.max_turns = max_turns
        self.task_id = task_id or str(uuid.uuid4())
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.extra_body = extra_body
        self.budget_config = budget_config or DEFAULT_BUDGET

    async def run(self, messages: List[Dict[str, Any]]) -> AgentResult:
        """
        使用标准 OpenAI 工具调用执行完整的智能体循环。

        参数：
            messages: 初始对话消息（system + user）。
                      随着对话推进会被原地修改。

        返回：
            包含完整对话历史、管理状态和元数据的 AgentResult
        """
        reasoning_per_turn = []
        tool_errors: List[ToolError] = []

        # 每次循环独立的 TodoStore，用于 todo 工具（临时的，随循环结束销毁）
        from tools.todo_tool import TodoStore, todo_tool as _todo_tool
        _todo_store = TodoStore()

        # 从第一条用户消息中提取用户任务，用于 browser_snapshot 上下文
        _user_task = None
        for msg in messages:
            if msg.get("role") == "user":
                content = msg.get("content", "")
                if isinstance(content, str) and content.strip():
                    _user_task = content.strip()[:500]  # 截断以避免超大字符串
                break

        import time as _time

        for turn in range(self.max_turns):
            turn_start = _time.monotonic()

            # 构建 chat_completion 的关键字参数
            chat_kwargs = {
                "messages": messages,
                "n": 1,
                "temperature": self.temperature,
            }

            # 仅在有工具定义时传递 tools 参数
            if self.tool_schemas:
                chat_kwargs["tools"] = self.tool_schemas

            # 仅在显式设置时传递 max_tokens
            if self.max_tokens is not None:
                chat_kwargs["max_tokens"] = self.max_tokens

            # 注入 extra_body 用于提供商特定参数（例如 OpenRouter
            # 提供商偏好，如禁用/首选提供商、转换等）
            if self.extra_body:
                chat_kwargs["extra_body"] = self.extra_body

            # 发起 API 调用 -- 标准 OpenAI 规范
            api_start = _time.monotonic()
            try:
                response = await self.server.chat_completion(**chat_kwargs)
            except Exception as e:
                api_elapsed = _time.monotonic() - api_start
                logger.error("API call failed on turn %d (%.1fs): %s", turn + 1, api_elapsed, e)
                return AgentResult(
                    messages=messages,
                    managed_state=self._get_managed_state(),
                    turns_used=turn + 1,
                    finished_naturally=False,
                    reasoning_per_turn=reasoning_per_turn,
                    tool_errors=tool_errors,
                )

            api_elapsed = _time.monotonic() - api_start

            if not response or not response.choices:
                logger.warning("Empty response on turn %d (api=%.1fs)", turn + 1, api_elapsed)
                return AgentResult(
                    messages=messages,
                    managed_state=self._get_managed_state(),
                    turns_used=turn + 1,
                    finished_naturally=False,
                    reasoning_per_turn=reasoning_per_turn,
                    tool_errors=tool_errors,
                )

            assistant_msg = response.choices[0].message

            # 从响应中提取推理内容（所有提供商格式）
            reasoning = _extract_reasoning_from_message(assistant_msg)
            reasoning_per_turn.append(reasoning)

            # 检查工具调用 -- 标准 OpenAI 规范。
            # 回退机制：如果响应没有结构化的 tool_calls，但 content 中
            # 包含原始工具调用标签（例如 <tool_call>），则使用 hermes-agent
            # 的独立解析器解析。这处理了 ManagedServer 的 ToolCallTranslator
            # 因为未安装 vLLM 而无法解析的情况。
            if (
                not assistant_msg.tool_calls
                and assistant_msg.content
                and self.tool_schemas
                and "<tool_call>" in (assistant_msg.content or "")
            ):
                try:
                    from environments.tool_call_parsers import get_parser
                    fallback_parser = get_parser("hermes")
                    parsed_content, parsed_calls = fallback_parser.parse(
                        assistant_msg.content
                    )
                    if parsed_calls:
                        assistant_msg.tool_calls = parsed_calls
                        if parsed_content is not None:
                            assistant_msg.content = parsed_content
                        logger.debug(
                            "Fallback parser extracted %d tool calls from raw content",
                            len(parsed_calls),
                        )
                except Exception:
                    pass  # 继续执行，作为没有工具调用处理

            if assistant_msg.tool_calls:
                # 将工具调用规范化为字典 — 它们可能是对象
                # （OpenAI API）或字典（vLLM ToolCallTranslator）。
                def _tc_to_dict(tc):
                    if isinstance(tc, dict):
                        return {
                            "id": tc.get("id", f"call_{uuid.uuid4().hex[:8]}"),
                            "type": "function",
                            "function": {
                                "name": tc.get("function", {}).get("name", tc.get("name", "")),
                                "arguments": tc.get("function", {}).get("arguments", tc.get("arguments", "{}")),
                            },
                        }
                    return {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }

                # 构建助手消息字典用于对话历史
                msg_dict: Dict[str, Any] = {
                    "role": "assistant",
                    "content": assistant_msg.content or "",
                    "tool_calls": [_tc_to_dict(tc) for tc in assistant_msg.tool_calls],
                }

                # 保留 reasoning_content 用于多轮聊天模板处理
                # （例如 Kimi-K2 的模板根据此字段以不同方式渲染
                # 历史消息和最新轮次的 <think> 块）
                if reasoning:
                    msg_dict["reasoning_content"] = reasoning

                messages.append(msg_dict)

                # 通过 hermes-agent 的分派机制执行每个工具调用
                for tc in assistant_msg.tool_calls:
                    # 处理对象（OpenAI）和字典（vLLM）两种格式
                    if isinstance(tc, dict):
                        tool_name = tc.get("function", {}).get("name", tc.get("name", ""))
                        tool_args_raw = tc.get("function", {}).get("arguments", tc.get("arguments", "{}"))
                    else:
                        tool_name = tc.function.name
                        tool_args_raw = tc.function.arguments

                    # 验证工具名称
                    if tool_name not in self.valid_tool_names:
                        tool_result = json.dumps(
                            {
                                "error": f"Unknown tool '{tool_name}'. "
                                f"Available tools: {sorted(self.valid_tool_names)}"
                            }
                        )
                        tool_errors.append(ToolError(
                            turn=turn + 1, tool_name=tool_name,
                            arguments=tool_args_raw[:200],
                            error=f"Unknown tool '{tool_name}'",
                            tool_result=tool_result,
                        ))
                        logger.warning(
                            "Model called unknown tool '%s' on turn %d",
                            tool_name, turn + 1,
                        )
                    else:
                        # 解析参数
                        try:
                            args = json.loads(tool_args_raw)
                        except json.JSONDecodeError as e:
                            args = None
                            tool_result = json.dumps(
                                {"error": f"Invalid JSON in tool arguments: {e}. Please retry with valid JSON."}
                            )
                            tool_errors.append(ToolError(
                                turn=turn + 1, tool_name=tool_name,
                                arguments=tool_args_raw[:200],
                                error=f"Invalid JSON: {e}",
                                tool_result=tool_result,
                            ))
                            logger.warning(
                                "Invalid JSON in tool call arguments for '%s': %s",
                                tool_name, tool_args_raw[:200],
                            )

                        # 仅在参数解析成功时分派工具
                        if args is not None:
                            try:
                                if tool_name == "terminal":
                                    backend = os.getenv("TERMINAL_ENV", "local")
                                    cmd_preview = args.get("command", "")[:80]
                                    logger.info(
                                        "[%s] $ %s", self.task_id[:8], cmd_preview,
                                    )

                                tool_submit_time = _time.monotonic()

                                # Todo 工具 -- 本地处理（需要每次循环独立的 TodoStore）
                                if tool_name == "todo":
                                    tool_result = _todo_tool(
                                        todos=args.get("todos"),
                                        merge=args.get("merge", False),
                                        store=_todo_store,
                                    )
                                    tool_elapsed = _time.monotonic() - tool_submit_time
                                elif tool_name == "memory":
                                    tool_result = json.dumps({"error": "Memory is not available in RL environments."})
                                    tool_elapsed = _time.monotonic() - tool_submit_time
                                elif tool_name == "session_search":
                                    tool_result = json.dumps({"error": "Session search is not available in RL environments."})
                                    tool_elapsed = _time.monotonic() - tool_submit_time
                                else:
                                    # 在线程池中运行工具调用，使得内部使用
                                    # asyncio.run() 的后端（modal、docker、daytona）
                                    # 获得干净的事件循环而不会死锁。
                                    loop = asyncio.get_event_loop()
                                    # 捕获当前的 tool_name/args 供 lambda 使用
                                    _tn, _ta, _tid = tool_name, args, self.task_id
                                    tool_result = await loop.run_in_executor(
                                        _tool_executor,
                                        lambda: handle_function_call(
                                            _tn, _ta, task_id=_tid,
                                            user_task=_user_task,
                                        ),
                                    )
                                    tool_elapsed = _time.monotonic() - tool_submit_time

                                # 记录慢工具和线程池统计信息用于调试
                                pool_active = _tool_executor._work_queue.qsize()
                                if tool_elapsed > 30:
                                    logger.warning(
                                        "[%s] turn %d: %s took %.1fs (pool queue=%d)",
                                        self.task_id[:8], turn + 1, tool_name,
                                        tool_elapsed, pool_active,
                                    )
                            except Exception as e:
                                tool_result = json.dumps(
                                    {"error": f"Tool execution failed: {type(e).__name__}: {str(e)}"}
                                )
                                tool_errors.append(ToolError(
                                    turn=turn + 1, tool_name=tool_name,
                                    arguments=tool_args_raw[:200],
                                    error=f"{type(e).__name__}: {str(e)}",
                                    tool_result=tool_result,
                                ))
                                logger.error(
                                    "Tool '%s' execution failed on turn %d: %s",
                                    tool_name, turn + 1, e,
                                )

                        # 同时检查工具是否在 JSON 结果中返回了错误
                        try:
                            result_data = json.loads(tool_result)
                            if isinstance(result_data, dict):
                                err = result_data.get("error")
                                exit_code = result_data.get("exit_code")
                                if err and exit_code and exit_code < 0:
                                    tool_errors.append(ToolError(
                                        turn=turn + 1, tool_name=tool_name,
                                        arguments=tool_args_raw[:200],
                                        error=str(err),
                                        tool_result=tool_result[:500],
                                    ))
                        except (json.JSONDecodeError, TypeError):
                            pass

                    tc_id = tc.get("id", "") if isinstance(tc, dict) else tc.id
                    tool_result = maybe_persist_tool_result(
                        content=tool_result,
                        tool_name=tool_name,
                        tool_use_id=tc_id,
                        env=get_active_env(self.task_id),
                        config=self.budget_config,
                    )

                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc_id,
                            "content": tool_result,
                        }
                    )

                num_tcs = len(assistant_msg.tool_calls)
                if num_tcs > 0:
                    enforce_turn_budget(
                        messages[-num_tcs:],
                        env=get_active_env(self.task_id),
                        config=self.budget_config,
                    )

                turn_elapsed = _time.monotonic() - turn_start
                logger.info(
                    "[%s] turn %d: api=%.1fs, %d tools, turn_total=%.1fs",
                    self.task_id[:8], turn + 1, api_elapsed,
                    len(assistant_msg.tool_calls), turn_elapsed,
                )

            else:
                # 没有工具调用 -- 模型已完成
                msg_dict = {
                    "role": "assistant",
                    "content": assistant_msg.content or "",
                }
                if reasoning:
                    msg_dict["reasoning_content"] = reasoning
                messages.append(msg_dict)

                turn_elapsed = _time.monotonic() - turn_start
                logger.info(
                    "[%s] turn %d: api=%.1fs, no tools (finished), turn_total=%.1fs",
                    self.task_id[:8], turn + 1, api_elapsed, turn_elapsed,
                )

                return AgentResult(
                    messages=messages,
                    managed_state=self._get_managed_state(),
                    turns_used=turn + 1,
                    finished_naturally=True,
                    reasoning_per_turn=reasoning_per_turn,
                    tool_errors=tool_errors,
                )

        # 达到最大轮次但模型未自行停止
        logger.info("Agent hit max_turns (%d) without finishing", self.max_turns)
        return AgentResult(
            messages=messages,
            managed_state=self._get_managed_state(),
            turns_used=self.max_turns,
            finished_naturally=False,
            reasoning_per_turn=reasoning_per_turn,
            tool_errors=tool_errors,
        )

    def _get_managed_state(self) -> Optional[Dict[str, Any]]:
        """
        获取 ManagedServer 状态（如果服务器支持）。

        返回包含 SequenceNodes（含 tokens/logprobs/masks）的状态字典，
        如果服务器不支持 get_state()（例如普通 OpenAI 服务器）则返回 None。
        """
        if hasattr(self.server, "get_state"):
            return self.server.get_state()
        return None
