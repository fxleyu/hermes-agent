"""
Mistral 工具调用解析器。

根据分词器版本支持两种格式：
- v11 之前：content[TOOL_CALLS] [{"name": ..., "arguments": {...}}, ...]
- v11 及之后：content[TOOL_CALLS]tool_name1{"arg": "val"}[TOOL_CALLS]tool_name2{"arg": "val"}

基于 VLLM 的 MistralToolParser.extract_tool_calls()
[TOOL_CALLS] 标记是 Mistral 模型使用的 bot_token。
"""

import json
import uuid
from typing import List, Optional

from openai.types.chat.chat_completion_message_tool_call import (
    ChatCompletionMessageToolCall,
    Function,
)

from environments.tool_call_parsers import ParseResult, ToolCallParser, register_parser


def _generate_mistral_id() -> str:
    """Mistral 工具调用 ID 是 9 字符的字母数字字符串。"""
    import random
    import string

    return "".join(random.choices(string.ascii_letters + string.digits, k=9))


@register_parser("mistral")
class MistralToolCallParser(ToolCallParser):
    """
    Mistral 格式工具调用的解析器。

    通过检查 [TOOL_CALLS] 之后的内容是否以 '[' 开头（v11 之前的 JSON 数组）
    还是以工具名称开头（v11+ 格式）来检测格式类型。
    """

    # [TOOL_CALLS] 标记 -- 根据分词器可能以不同字符串出现
    BOT_TOKEN = "[TOOL_CALLS]"

    def parse(self, text: str) -> ParseResult:
        if self.BOT_TOKEN not in text:
            return text, None

        try:
            # 以 [TOOL_CALLS] 为分隔符拆分文本
            parts = text.split(self.BOT_TOKEN)
            content = parts[0].strip()
            raw_tool_calls = parts[1:]

            # 检测格式：如果第一个原始部分以 '[' 开头，则为 v11 之前的格式
            first_raw = raw_tool_calls[0].strip() if raw_tool_calls else ""
            is_pre_v11 = first_raw.startswith("[") or first_raw.startswith("{")

            tool_calls: List[ChatCompletionMessageToolCall] = []

            if not is_pre_v11:
                # v11+ 格式：[TOOL_CALLS]tool_name{args}[TOOL_CALLS]tool_name2{args2}
                for raw in raw_tool_calls:
                    raw = raw.strip()
                    if not raw or "{" not in raw:
                        continue

                    # 在第一个 '{' 处分割，前面是工具名，后面是参数 JSON
                    brace_idx = raw.find("{")
                    tool_name = raw[:brace_idx].strip()
                    args_str = raw[brace_idx:]

                    # 验证并清理 JSON 参数
                    try:
                        parsed_args = json.loads(args_str)
                        args_str = json.dumps(parsed_args, ensure_ascii=False)
                    except json.JSONDecodeError:
                        pass  # 解析失败时保留原始字符串

                    tool_calls.append(
                        ChatCompletionMessageToolCall(
                            id=_generate_mistral_id(),
                            type="function",
                            function=Function(name=tool_name, arguments=args_str),
                        )
                    )
            else:
                # v11 之前的格式：[TOOL_CALLS] [{"name": ..., "arguments": {...}}]
                try:
                    parsed = json.loads(first_raw)
                    # 如果解析结果是单个字典，包装成列表
                    if isinstance(parsed, dict):
                        parsed = [parsed]

                    for tc in parsed:
                        if "name" not in tc:
                            continue
                        args = tc.get("arguments", {})
                        if isinstance(args, dict):
                            args = json.dumps(args, ensure_ascii=False)

                        tool_calls.append(
                            ChatCompletionMessageToolCall(
                                id=_generate_mistral_id(),
                                type="function",
                                function=Function(
                                    name=tc["name"], arguments=args
                                ),
                            )
                        )
                except json.JSONDecodeError:
                    # 回退方案：使用 raw_decode 逐个提取 JSON 对象
                    decoder = json.JSONDecoder()
                    idx = 0
                    while idx < len(first_raw):
                        try:
                            obj, end_idx = decoder.raw_decode(first_raw, idx)
                            if isinstance(obj, dict) and "name" in obj:
                                args = obj.get("arguments", {})
                                if isinstance(args, dict):
                                    args = json.dumps(args, ensure_ascii=False)
                                tool_calls.append(
                                    ChatCompletionMessageToolCall(
                                        id=_generate_mistral_id(),
                                        type="function",
                                        function=Function(
                                            name=obj["name"], arguments=args
                                        ),
                                    )
                                )
                            idx = end_idx
                        except json.JSONDecodeError:
                            idx += 1

            if not tool_calls:
                return text, None

            return content if content else None, tool_calls

        except Exception:
            return text, None
