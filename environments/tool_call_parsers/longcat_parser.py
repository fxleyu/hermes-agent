"""
Longcat Flash Chat 工具调用解析器。

与 Hermes 相同，但使用 <longcat_tool_call> 标签代替 <tool_call>。
基于 VLLM 的 LongcatFlashToolParser（扩展自 Hermes2ProToolParser）。
"""

import json
import re
import uuid
from typing import List, Optional

from openai.types.chat.chat_completion_message_tool_call import (
    ChatCompletionMessageToolCall,
    Function,
)

from environments.tool_call_parsers import ParseResult, ToolCallParser, register_parser


@register_parser("longcat")
class LongcatToolCallParser(ToolCallParser):
    """
    Longcat Flash Chat 工具调用的解析器。
    与 Hermes 逻辑完全相同，只是使用不同的标签名。
    """

    # 匹配闭合和未闭合的 longcat_tool_call 标签
    PATTERN = re.compile(
        r"<longcat_tool_call>\s*(.*?)\s*</longcat_tool_call>|<longcat_tool_call>\s*(.*)",
        re.DOTALL,
    )

    def parse(self, text: str) -> ParseResult:
        # 快速检查：如果不包含 longcat_tool_call 标签，直接返回
        if "<longcat_tool_call>" not in text:
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

                # 解析 JSON 并构造工具调用对象
                tc_data = json.loads(raw_json)
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

            # content 是第一个 <longcat_tool_call> 标签之前的文本
            content = text[: text.find("<longcat_tool_call>")].strip()
            return content if content else None, tool_calls

        except Exception:
            return text, None
