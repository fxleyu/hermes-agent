"""可插拔记忆提供者的抽象基类。

记忆提供者为智能体（agent）提供跨会话的持久化记忆能力。在任意时刻，
只有一个外部提供者与始终开启的内置记忆（MEMORY.md / USER.md）并存运行。
MemoryManager 负责强制执行此限制。

内置记忆始终作为第一个提供者激活，不可移除。
外部提供者（Honcho、Hindsight、Mem0 等）是叠加式的——它们不会
禁用内置存储。同一时间只运行一个外部提供者，以防止
工具模式膨胀和记忆后端冲突。

注册方式：
  1. 内置：BuiltinMemoryProvider——始终存在，不可移除。
  2. 插件：位于 plugins/memory/<name>/，通过 memory.provider 配置激活。

生命周期（由 MemoryManager 调用，在 run_agent.py 中装配）：
  initialize()          — 连接、创建资源、预热
  system_prompt_block()  — 用于系统提示词的静态文本
  prefetch(query)        — 每轮对话前的后台记忆召回
  sync_turn(user, asst)  — 每轮对话后的异步写入
  get_tool_schemas()     — 暴露给模型的工具模式
  handle_tool_call()     — 分发工具调用
  shutdown()             — 清理退出

可选钩子（重写以启用）：
  on_turn_start(turn, message, **kwargs) — 每轮开始时的回调，携带运行时上下文
  on_session_end(messages)               — 会话结束时的提取
  on_pre_compress(messages) -> str       — 上下文压缩前的提取
  on_memory_write(action, target, content) — 镜像内置记忆的写入操作
  on_delegation(task, result, **kwargs)  — 父智能体侧观察子智能体工作
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


class MemoryProvider(ABC):
    """记忆提供者的抽象基类。"""

    @property
    @abstractmethod
    def name(self) -> str:
        """此提供者的短标识符（例如 'builtin'、'honcho'、'hindsight'）。"""

    # -- 核心生命周期（需实现这些方法） ------------------------------------

    @abstractmethod
    def is_available(self) -> bool:
        """如果此提供者已配置、拥有凭证且准备就绪，返回 True。

        在智能体初始化时调用，用于决定是否激活该提供者。
        不应进行网络调用——仅检查配置和已安装的依赖。
        """

    @abstractmethod
    def initialize(self, session_id: str, **kwargs) -> None:
        """为一个会话执行初始化。

        在智能体启动时调用一次。可以创建资源（数据库、表），
        建立连接、启动后台线程等。

        kwargs 始终包含：
          - hermes_home (str)：当前活动的 HERMES_HOME 目录路径。用于
            配置文件范围的存储，而非硬编码 ``~/.hermes``。
          - platform (str)："cli"、"telegram"、"discord"、"cron" 等。

        kwargs 也可能包含：
          - agent_context (str)："primary"、"subagent"、"cron" 或 "flush"。
            提供者应跳过非主要上下文的写入（cron 系统提示词
            会破坏用户表示）。
          - agent_identity (str)：配置文件名称（例如 "coder"）。用于
            按配置文件范围区分提供者身份。
          - agent_workspace (str)：共享工作区名称（例如 "hermes"）。
          - parent_session_id (str)：对于子智能体，父智能体的 session_id。
          - user_id (str)：平台用户标识符（网关会话）。
        """

    def system_prompt_block(self) -> str:
        """返回要包含在系统提示词中的文本。

        在系统提示词组装时调用。返回空字符串则跳过。
        此方法用于提供者的静态信息（说明、状态）。通过 prefetch()
        注入的预取召回上下文是单独处理的。
        """
        return ""

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        """为即将到来的对话轮次召回相关上下文。

        在每次 API 调用前调用。返回格式化的文本作为上下文注入，
        如果没有相关内容则返回空字符串。实现应当快速——
        使用后台线程执行实际召回，在此处返回缓存结果。

        session_id 是为服务并发会话的提供者准备的
        （网关群聊、缓存智能体）。不需要按会话区分范围的
        提供者可以忽略它。
        """
        return ""

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        """为下一轮对话排队一个后台召回任务。

        在每轮对话完成后调用。结果将在下一轮的 prefetch() 中消费。
        默认为空操作——执行后台预取的提供者应重写此方法。
        """

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        """将已完成的对话轮次持久化到后端。

        在每轮对话后调用。应为非阻塞的——如果后端有延迟，
        则排队进行后台处理。
        """

    @abstractmethod
    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        """返回此提供者暴露的工具模式。

        每个模式遵循 OpenAI 函数调用格式：
        {"name": "...", "description": "...", "parameters": {...}}

        如果此提供者没有工具（仅提供上下文），则返回空列表。
        """

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
        """处理此提供者某个工具的调用。

        必须返回一个 JSON 字符串（工具结果）。
        仅在 get_tool_schemas() 返回的工具名称被调用时触发。
        """
        raise NotImplementedError(f"Provider {self.name} does not handle tool {tool_name}")

    def shutdown(self) -> None:
        """清理关闭——刷新队列、关闭连接。"""

    # -- 可选钩子（重写以启用） ---------------------------------

    def on_turn_start(self, turn_number: int, message: str, **kwargs) -> None:
        """在每轮对话开始时使用用户消息调用。

        用于轮次计数、作用域管理、定期维护。

        kwargs 可能包含：remaining_tokens、model、platform、tool_count。
        提供者按需使用；多余参数会被忽略。
        """

    def on_session_end(self, messages: List[Dict[str, Any]]) -> None:
        """在会话结束时调用（显式退出或超时）。

        用于会话结束时的事实提取、摘要等。
        messages 是完整的对话历史。

        不会在每轮对话后调用——仅在实际的会话边界触发
        （CLI 退出、/reset、网关会话过期）。
        """

    def on_pre_compress(self, messages: List[Dict[str, Any]]) -> str:
        """在上下文压缩丢弃旧消息之前调用。

        用于从即将被压缩的消息中提取洞察。
        messages 是将被摘要/丢弃的消息列表。

        返回文本以包含在压缩摘要提示词中，使压缩器
        保留提供者提取的洞察。返回空字符串表示无贡献
        （向后兼容的默认行为）。
        """
        return ""

    def on_delegation(self, task: str, result: str, *,
                      child_session_id: str = "", **kwargs) -> None:
        """在子智能体完成时，于父智能体上调用。

        父智能体的记忆提供者获得任务+结果对，作为对
        委派内容及返回结果的观察。子智能体本身没有提供者
        会话（skip_memory=True）。

        task：委派提示词
        result：子智能体的最终响应
        child_session_id：子智能体的 session_id
        """

    def get_config_schema(self) -> List[Dict[str, Any]]:
        """返回此提供者设置所需的配置字段。

        由 'hermes memory setup' 使用，引导用户完成配置。
        每个字段是一个字典，包含：
          key:         配置键名（例如 'api_key'、'mode'）
          description: 人类可读的描述
          secret:      如果应写入 .env 则为 True（默认：False）
          required:    如果必填则为 True（默认：False）
          default:     默认值（可选）
          choices:     有效值列表（可选）
          url:         用户获取此凭证的 URL（可选）
          env_var:     密钥的显式环境变量名（默认：自动生成）

        如果不需要配置（例如仅本地的提供者），返回空列表。
        """
        return []

    def save_config(self, values: Dict[str, Any], hermes_home: str) -> None:
        """将非密钥配置写入提供者的原生位置。

        在 'hermes memory setup' 收集用户输入后调用。
        ``values`` 仅包含非密钥字段（密钥写入 .env）。
        ``hermes_home`` 是当前活动的 HERMES_HOME 目录路径。

        有原生配置文件（JSON、YAML）的提供者应重写此方法，
        写入其预期位置。仅使用环境变量的提供者可以保留
        默认值（空操作）。

        所有新的记忆提供者插件必须实现以下之一：
        - save_config() 用于原生配置文件格式，或者
        - 仅使用环境变量（在这种情况下 get_config_schema() 的字段
          都应设置 ``env_var``，此方法保持空操作）。
        """

    def on_memory_write(self, action: str, target: str, content: str) -> None:
        """在内置记忆工具写入条目时调用。

        action: 'add'、'replace' 或 'remove'
        target: 'memory' 或 'user'
        content: 条目内容

        用于将内置记忆写入镜像到你的后端。
        """
