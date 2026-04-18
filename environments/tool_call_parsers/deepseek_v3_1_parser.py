"""
DeepSeek V3.1 工具调用解析器。

与 V3 类似，但格式略有不同：
    <｜tool▁call▁begin｜>function_name<｜tool▁sep｜>arguments<｜tool▁call▁end｜>

注意：V3 在分隔符之前有 type+name，V3.1 在分隔符之前是 name，之后是 args。

基于 VLLM 的 DeepSeekV31ToolParser.extract_tool_calls()
"""

import re
import uuid
from typing import List, Optional

from openai.types.chat.chat_completion_message_tool_call import (
    ChatCompletionMessageToolCall,
    Function,
)

from environments.tool_call_parsers import ParseResult, ToolCallParser, register_parser


@register_parser("deepseek_v3_1")
@register_parser("deepseek_v31")
class DeepSeekV31ToolCallParser(ToolCallParser):
    """
    DeepSeek V3.1 工具调用的解析器。

    与 V3 的正则表达式略有不同：function_name 在分隔符之前，
    arguments 在分隔符之后（没有 type 字段，也没有 json 代码块包装）。
    """

    START_TOKEN = "<｜tool▁calls▁begin｜>"

    # 正则表达式捕获：function_name 和 function_arguments
    PATTERN = re.compile(
        r"<｜tool▁call▁begin｜>(?P<function_name>.*?)<｜tool▁sep｜>(?P<function_arguments>.*?)<｜tool▁call▁end｜>",
        re.DOTALL,
    )

    def parse(self, text: str) -> ParseResult:
        # 快速检查：如果文本中不包含起始标记，直接返回原文
        if self.START_TOKEN not in text:
            return text, None

        try:
            # 使用正则表达式查找所有匹配的工具调用
            matches = self.PATTERN.findall(text)
            if not matches:
                return text, None

            tool_calls: List[ChatCompletionMessageToolCall] = []
            for match in matches:
                # 每个 match 是 (function_name, function_arguments) 的元组
                func_name, func_args = match
                tool_calls.append(
                    ChatCompletionMessageToolCall(
                        id=f"call_{uuid.uuid4().hex[:8]}",
                        type="function",
                        function=Function(
                            name=func_name.strip(),
                            arguments=func_args.strip(),
                        ),
                    )
                )

            if not tool_calls:
                return text, None

            # content 是起始标记之前的所有文本
            content = text[: text.find(self.START_TOKEN)].strip()
            return content if content else None, tool_calls

        except Exception:
            return text, None
