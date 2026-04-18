"""
GLM 4.7 工具调用解析器。

与 GLM 4.5 相同，但正则表达式模式略有不同。
tool_call 标签的包装方式可能不同，参数解析会处理
键/值对之间的换行符。

基于 VLLM 的 Glm47MoeModelToolParser（扩展自 Glm4MoeModelToolParser）。
"""

import re

from environments.tool_call_parsers import ParseResult, register_parser
from environments.tool_call_parsers.glm45_parser import Glm45ToolCallParser


@register_parser("glm47")
class Glm47ToolCallParser(Glm45ToolCallParser):
    """
    GLM 4.7 工具调用的解析器。
    继承自 GLM 4.5，使用更新后的正则表达式模式。
    """

    def __init__(self):
        super().__init__()
        # GLM 4.7 使用略有不同的详情正则表达式，
        # 包含 <tool_call> 包装和可选的 arg_key 内容
        self.FUNC_DETAIL_REGEX = re.compile(
            r"<tool_call>(.*?)(<arg_key>.*?)?</tool_call>", re.DOTALL
        )
        # GLM 4.7 处理 arg_key 和 arg_value 标签之间的换行符
        self.FUNC_ARG_REGEX = re.compile(
            r"<arg_key>(.*?)</arg_key>(?:\\n|\s)*<arg_value>(.*?)</arg_value>",
            re.DOTALL,
        )
