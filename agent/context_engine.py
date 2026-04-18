"""可插拔上下文引擎的抽象基类。

上下文引擎控制当对话接近模型的令牌上限时，如何管理对话上下文。
内置的 ContextCompressor 是默认实现。第三方引擎（如 LCM）可以
通过插件系统或放置在 ``plugins/context_engine/<name>/`` 目录下来替换它。

选择由配置驱动：config.yaml 中的 ``context.engine``。
默认值为 ``"compressor"``（内置引擎）。同一时间只有一个引擎活跃。

引擎负责：
  - 决定何时触发压缩
  - 执行压缩（摘要、DAG 构建等）
  - 可选地暴露智能体可调用的工具（如 lcm_grep）
  - 跟踪 API 响应中的令牌使用量

生命周期：
  1. 引擎被实例化并注册（插件 register() 或默认注册）
  2. on_session_start() 在对话开始时调用
  3. update_from_response() 在每次 API 响应后使用 usage 数据调用
  4. should_compress() 在每轮对话后检查
  5. compress() 在 should_compress() 返回 True 时调用
  6. on_session_end() 在真正的会话边界调用（CLI 退出、/reset、
     网关会话过期）——不会在每轮对话后调用
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List


class ContextEngine(ABC):
    """所有上下文引擎必须实现的基类。"""

    # -- 身份标识 ----------------------------------------------------------

    @property
    @abstractmethod
    def name(self) -> str:
        """短标识符（例如 'compressor'、'lcm'）。"""

    # -- 令牌状态（由 run_agent.py 读取，用于显示/日志） ------------
    #
    # 引擎必须维护这些属性。run_agent.py 直接读取它们。

    last_prompt_tokens: int = 0
    last_completion_tokens: int = 0
    last_total_tokens: int = 0
    threshold_tokens: int = 0
    context_length: int = 0
    compression_count: int = 0

    # -- 压缩参数（由 run_agent.py 在预检查时读取） --------
    #
    # 这些参数控制预检压缩检查。子类可通过 __init__ 或属性重写；
    # 默认值对大多数引擎是合理的。

    threshold_percent: float = 0.75
    protect_first_n: int = 3
    protect_last_n: int = 6

    # -- 核心接口 ----------------------------------------------------

    @abstractmethod
    def update_from_response(self, usage: Dict[str, Any]) -> None:
        """从 API 响应更新跟踪的令牌使用量。

        在每次 LLM 调用后，使用响应中的 usage 字典调用。
        """

    @abstractmethod
    def should_compress(self, prompt_tokens: int = None) -> bool:
        """如果本轮应触发压缩，返回 True。"""

    @abstractmethod
    def compress(
        self,
        messages: List[Dict[str, Any]],
        current_tokens: int = None,
    ) -> List[Dict[str, Any]]:
        """压缩消息列表并返回新的消息列表。

        这是主入口点。引擎接收完整的消息列表，返回一个
        （可能更短的）在上下文预算内的列表。实现可以自由地
        进行摘要、构建 DAG 或做任何其他操作——只要返回的列表
        是有效的 OpenAI 格式消息序列即可。
        """

    # -- 可选：预检查 ----------------------------------------

    def should_compress_preflight(self, messages: List[Dict[str, Any]]) -> bool:
        """API 调用前的快速粗略检查（尚无实际令牌计数）。

        默认返回 False（跳过预检查）。如果你的引擎能做快速估算，
        可重写此方法。
        """
        return False

    # -- 可选：会话生命周期 ---------------------------------------

    def on_session_start(self, session_id: str, **kwargs) -> None:
        """在新的对话会话开始时调用。

        用于加载会话的持久化状态（DAG、存储）。
        kwargs 可能包含 hermes_home、platform、model 等。
        """

    def on_session_end(self, session_id: str, messages: List[Dict[str, Any]]) -> None:
        """在真正的会话边界调用（CLI 退出、/reset、网关过期）。

        用于刷新状态、关闭数据库连接等。
        不会在每轮对话后调用——仅在会话真正结束时触发。
        """

    def on_session_reset(self) -> None:
        """在 /new 或 /reset 时调用。重置每会话状态。

        默认重置 compression_count 和令牌跟踪。
        """
        self.last_prompt_tokens = 0
        self.last_completion_tokens = 0
        self.last_total_tokens = 0
        self.compression_count = 0

    # -- 可选：工具 ---------------------------------------------------

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        """返回此引擎提供给智能体的工具模式。

        默认返回空列表（无工具）。LCM 引擎会在此返回
        lcm_grep、lcm_describe、lcm_expand 的模式。
        """
        return []

    def handle_tool_call(self, name: str, args: Dict[str, Any], **kwargs) -> str:
        """处理来自智能体的工具调用。

        仅对 get_tool_schemas() 返回的工具名称调用。
        必须返回 JSON 字符串。

        kwargs 可能包含：
          messages：当前内存中的消息列表（用于实时摄入）
        """
        import json
        return json.dumps({"error": f"Unknown context engine tool: {name}"})

    # -- 可选：状态/显示 ----------------------------------------

    def get_status(self) -> Dict[str, Any]:
        """返回用于显示/日志的状态字典。

        默认返回 run_agent.py 期望的标准字段。
        """
        return {
            "last_prompt_tokens": self.last_prompt_tokens,
            "threshold_tokens": self.threshold_tokens,
            "context_length": self.context_length,
            "usage_percent": (
                min(100, self.last_prompt_tokens / self.context_length * 100)
                if self.context_length else 0
            ),
            "compression_count": self.compression_count,
        }

    # -- 可选：模型切换支持 ------------------------------------

    def update_model(
        self,
        model: str,
        context_length: int,
        base_url: str = "",
        api_key: str = "",
        provider: str = "",
    ) -> None:
        """在用户切换模型或备用模型激活时调用。

        默认更新 context_length 并根据 threshold_percent 重新计算
        threshold_tokens。如果你的引擎需要更多操作（例如重新计算
        DAG 预算、切换摘要模型），请重写此方法。
        """
        self.context_length = context_length
        self.threshold_tokens = int(context_length * self.threshold_percent)
