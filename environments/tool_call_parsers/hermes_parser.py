"""
Hermes 工具调用解析器。

格式：<tool_call>{"name": "func", "arguments": {...}}</tool_call>
基于 VLLM 的 Hermes2ProToolParser.extract_tool_calls()
"""

import json
import re
import uuid
from typing import List, Optional, Tuple

from openai.types.chat.chat_completion_message_tool_call import (
    ChatCompletionMessageToolCall,
    Function,
)

from environments.tool_call_parsers import ParseResult, ToolCallParser, register_parser


@register_parser("hermes")
class HermesToolCallParser(ToolCallParser):
    """
    Hermes 格式工具调用的解析器。

    匹配包含 "name" 和 "arguments" 的 JSON 的 <tool_call>...</tool_call> 标签。
    同时处理字符串末尾未闭合的 <tool_call>（生成被截断的情况）。
    """

    # 匹配闭合和未闭合的 tool_call 标签
    PATTERN = re.compile(
        r"<tool_call>\s*(.*?)\s*</tool_call>|<tool_call>\s*(.*)", re.DOTALL
    )

    def parse(self, text: str) -> ParseResult:
        # 快速检查：如果文本中不包含 <tool_call> 标签，直接返回原文
        if "<tool_call>" not in text:
            return text, None

        try:
            matches = self.PATTERN.findall(text)
            if not matches:
                return text, None

            tool_calls: List[ChatCompletionMessageToolCall] = []
            for match in matches:
                # match 是一个元组：(闭合标签内容, 未闭合标签内容)
                raw_json = match[0] if match[0] else match[1]
                if not raw_json.strip():
                    continue

                # 将匹配到的 JSON 字符串解析为字典
                tc_data = json.loads(raw_json)
                if "name" not in tc_data:
                    continue
                # 构造标准的 ChatCompletionMessageToolCall 对象
                tool_calls.append(
                    ChatCompletionMessageToolCall(
                        id=f"call_{uuid.uuid4().hex[:8]}",
                        type="function",
                        function=Function(
                            name=tc_data["name"],
                            arguments=json.dumps(
                                tc_data.get("arguments", {}), ensure_ascii=False
                            ),
                        ),
                    )
                )

            if not tool_calls:
                return text, None

            # content 是第一个 <tool_call> 标签之前的所有内容
            content = text[: text.find("<tool_call>")].strip()
            return content if content else None, tool_calls

        except Exception:
            # 解析失败时返回原始文本，不丢失信息
            return text, None
