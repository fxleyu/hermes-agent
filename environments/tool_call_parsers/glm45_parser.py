"""
GLM 4.5 (GLM-4-MoE) 工具调用解析器。

格式使用自定义的 arg_key/arg_value 标签而非标准 JSON：
    <tool_call>function_name
    <arg_key>param1</arg_key><arg_value>value1</arg_value>
    <arg_key>param2</arg_key><arg_value>value2</arg_value>
    </tool_call>

值的反序列化流程：json.loads -> ast.literal_eval -> 原始字符串回退。

基于 VLLM 的 Glm4MoeModelToolParser.extract_tool_calls()
"""

import ast
import json
import re
import uuid
from typing import Any, Dict, List, Optional

from openai.types.chat.chat_completion_message_tool_call import (
    ChatCompletionMessageToolCall,
    Function,
)

from environments.tool_call_parsers import ParseResult, ToolCallParser, register_parser


def _deserialize_value(value: str) -> Any:
    """
    尝试将字符串值反序列化为原生 Python 类型。
    依次尝试 json.loads、ast.literal_eval，最后返回原始字符串。
    """
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        pass

    try:
        return ast.literal_eval(value)
    except (ValueError, SyntaxError, TypeError):
        pass

    return value


@register_parser("glm45")
class Glm45ToolCallParser(ToolCallParser):
    """
    GLM 4.5 (GLM-4-MoE) 工具调用的解析器。

    使用 <tool_call>...</tool_call> 标签，内部以 <arg_key>/<arg_value> 键值对
    代替标准 JSON 参数格式。
    """

    FUNC_CALL_REGEX = re.compile(r"<tool_call>.*?</tool_call>", re.DOTALL)
    FUNC_DETAIL_REGEX = re.compile(r"<tool_call>([^\n]*)\n(.*)</tool_call>", re.DOTALL)
    FUNC_ARG_REGEX = re.compile(
        r"<arg_key>(.*?)</arg_key>\s*<arg_value>(.*?)</arg_value>", re.DOTALL
    )

    START_TOKEN = "<tool_call>"

    def parse(self, text: str) -> ParseResult:
        if self.START_TOKEN not in text:
            return text, None

        try:
            # 查找所有 <tool_call>...</tool_call> 块
            matched_calls = self.FUNC_CALL_REGEX.findall(text)
            if not matched_calls:
                return text, None

            tool_calls: List[ChatCompletionMessageToolCall] = []

            for match in matched_calls:
                # 从 tool_call 块中提取函数名和参数部分
                detail = self.FUNC_DETAIL_REGEX.search(match)
                if not detail:
                    continue

                func_name = detail.group(1).strip()
                func_args_raw = detail.group(2)

                # 解析 arg_key/arg_value 键值对
                pairs = self.FUNC_ARG_REGEX.findall(func_args_raw) if func_args_raw else []
                arg_dict: Dict[str, Any] = {}
                for key, value in pairs:
                    arg_key = key.strip()
                    arg_val = _deserialize_value(value.strip())
                    arg_dict[arg_key] = arg_val

                tool_calls.append(
                    ChatCompletionMessageToolCall(
                        id=f"call_{uuid.uuid4().hex[:8]}",
                        type="function",
                        function=Function(
                            name=func_name,
                            arguments=json.dumps(arg_dict, ensure_ascii=False),
                        ),
                    )
                )

            if not tool_calls:
                return text, None

            # content 是第一个 <tool_call> 之前的文本
            content = text[: text.find(self.START_TOKEN)].strip()
            return content if content else None, tool_calls

        except Exception:
            return text, None
