"""AWS Bedrock Converse API 适配器，用于 Hermes Agent。

提供与 Amazon Bedrock 的原生集成，使用 Converse API，
绕过 OpenAI 兼容端点，直接使用 AWS SDK 调用。
这使得可以完全访问 Bedrock 生态系统：

  - **原生 Converse API**：所有 Bedrock 模型的统一接口
    （Claude、Nova、Llama、Mistral 等），支持流式输出。
  - **AWS 凭据链**：IAM 角色、SSO 配置文件、环境变量、
    实例元数据 —— AWS 原生环境无需管理 API 密钥。
  - **动态模型发现**：通过 Bedrock 控制面板自动发现可用的
    基础模型和跨区域推理配置文件。
  - **Guardrails 支持**：可选的 Bedrock Guardrails 配置，
    用于内容过滤和安全策略。
  - **推理配置文件**：支持跨区域推理配置文件
    （us.anthropic.claude-*、global.anthropic.claude-*），
    提供更好的容量和自动故障转移。

架构遵循与 ``anthropic_adapter.py`` 相同的模式：
  - 所有 Bedrock 特定逻辑都隔离在此模块中。
  - 消息/工具在 OpenAI 格式和 Converse 格式之间转换。
  - 响应被规范化为 OpenAI 兼容对象，供 agent 循环使用。

参考：OpenClaw 的 ``extensions/amazon-bedrock/`` 插件，该插件通过
``@aws-sdk/client-bedrock`` 在 TypeScript 中实现了相同的 Converse API 集成。

依赖：``boto3``（可选依赖 —— 仅在使用 Bedrock 提供商时需要）。
"""

import json
import logging
import os
import re
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 延迟导入 boto3 —— 仅在实际使用 Bedrock 提供商时才加载。
# 这使得不使用 Bedrock 的用户的启动速度更快。
# ---------------------------------------------------------------------------

_bedrock_runtime_client_cache: Dict[str, Any] = {}
_bedrock_control_client_cache: Dict[str, Any] = {}


def _require_boto3():
    """导入 boto3，若未安装则抛出清晰的错误信息。"""
    try:
        import boto3
        return boto3
    except ImportError:
        raise ImportError(
            "The 'boto3' package is required for the AWS Bedrock provider. "
            "Install it with: pip install boto3\n"
            "Or install Hermes with Bedrock support: pip install -e '.[bedrock]'"
        )


def _get_bedrock_runtime_client(region: str):
    """获取或创建给定区域的缓存 ``bedrock-runtime`` 客户端。

    使用默认 AWS 凭据链（环境变量 → 配置文件 → 实例角色）。
    """
    if region not in _bedrock_runtime_client_cache:
        boto3 = _require_boto3()
        _bedrock_runtime_client_cache[region] = boto3.client(
            "bedrock-runtime", region_name=region,
        )
    return _bedrock_runtime_client_cache[region]


def _get_bedrock_control_client(region: str):
    """获取或创建用于模型发现的缓存 ``bedrock`` 控制面板客户端。"""
    if region not in _bedrock_control_client_cache:
        boto3 = _require_boto3()
        _bedrock_control_client_cache[region] = boto3.client(
            "bedrock", region_name=region,
        )
    return _bedrock_control_client_cache[region]


def reset_client_cache():
    """清除缓存的 boto3 客户端。用于测试和配置文件切换。"""
    _bedrock_runtime_client_cache.clear()
    _bedrock_control_client_cache.clear()


# ---------------------------------------------------------------------------
# AWS 凭据检测
# ---------------------------------------------------------------------------

# 优先级顺序匹配 OpenClaw 的 resolveAwsSdkEnvVarName()：
#   1. AWS_BEARER_TOKEN_BEDROCK（Bedrock 专用的 Bearer 令牌）
#   2. AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY（显式 IAM 凭据）
#   3. AWS_PROFILE（命名配置文件 → SSO、assume-role 等）
#   4. 隐式：实例角色、ECS 任务角色、Lambda 执行角色
_AWS_CREDENTIAL_ENV_VARS = [
    "AWS_BEARER_TOKEN_BEDROCK",
    "AWS_ACCESS_KEY_ID",
    "AWS_PROFILE",
    # 这些由 boto3 的默认链检查，但我们在此列出它们用于
    # has_aws_credentials() 检测：
    "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
    "AWS_WEB_IDENTITY_TOKEN_FILE",
]


def resolve_aws_auth_env_var(env: Optional[Dict[str, str]] = None) -> Optional[str]:
    """返回当前活跃的 AWS 认证源名称，如果没有则返回 None。

    先检查环境变量，然后回退到 boto3 的凭据链以检查隐式来源
    （EC2 IMDS、ECS 任务角色等）。

    这对应 OpenClaw 的 ``resolveAwsSdkEnvVarName()`` —— 用于检测
    用户是否配置了任何 AWS 凭据，而无需实际尝试认证。
    """
    env = env if env is not None else os.environ
    # Bearer 令牌优先级最高
    if env.get("AWS_BEARER_TOKEN_BEDROCK", "").strip():
        return "AWS_BEARER_TOKEN_BEDROCK"
    # 显式访问密钥对
    if (env.get("AWS_ACCESS_KEY_ID", "").strip()
            and env.get("AWS_SECRET_ACCESS_KEY", "").strip()):
        return "AWS_ACCESS_KEY_ID"
    # 命名配置文件（SSO、assume-role 等）
    if env.get("AWS_PROFILE", "").strip():
        return "AWS_PROFILE"
    # 容器凭据（ECS、CodeBuild）
    if env.get("AWS_CONTAINER_CREDENTIALS_RELATIVE_URI", "").strip():
        return "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI"
    # Web 身份（EKS IRSA）
    if env.get("AWS_WEB_IDENTITY_TOKEN_FILE", "").strip():
        return "AWS_WEB_IDENTITY_TOKEN_FILE"
    # 没有环境变量 —— 检查 boto3 是否能通过 IMDS 或其他
    # 隐式来源（EC2 实例角色、ECS 任务角色、Lambda 等）解析凭据
    try:
        import botocore.session
        session = botocore.session.get_session()
        credentials = session.get_credentials()
        if credentials is not None:
            resolved = credentials.get_frozen_credentials()
            if resolved and resolved.access_key:
                return "iam-role"
    except Exception:
        pass
    return None


def has_aws_credentials(env: Optional[Dict[str, str]] = None) -> bool:
    """如果检测到任何 AWS 凭据源则返回 True。

    先检查环境变量（快速，无 I/O），然后回退到 boto3 的凭据链，
    该凭据链覆盖不设置环境变量的 EC2 实例角色、ECS 任务角色、
    Lambda 执行角色和其他基于 IMDS 的来源。

    这种两层方法对应 OpenClaw PR #62673 中的模式：
    云环境（EC2、ECS、Lambda）通过实例元数据提供凭据，
    而非环境变量。环境变量检查是本地开发的快速路径；
    boto3 回退覆盖所有云部署。
    """
    if resolve_aws_auth_env_var(env) is not None:
        return True
    # 回退到 boto3 的凭据解析器 —— 这覆盖了 EC2 实例
    # 元数据（IMDS）、ECS 容器凭据和其他不设置环境变量的隐式来源。
    try:
        import botocore.session
        session = botocore.session.get_session()
        credentials = session.get_credentials()
        if credentials is not None:
            resolved = credentials.get_frozen_credentials()
            if resolved and resolved.access_key:
                return True
    except Exception:
        pass
    return False


def resolve_bedrock_region(env: Optional[Dict[str, str]] = None) -> str:
    """解析 Bedrock API 调用使用的 AWS 区域。

    优先级：AWS_REGION → AWS_DEFAULT_REGION → us-east-1（兜底）。
    """
    env = env if env is not None else os.environ
    return (
        env.get("AWS_REGION", "").strip()
        or env.get("AWS_DEFAULT_REGION", "").strip()
        or "us-east-1"
    )


# ---------------------------------------------------------------------------
# 工具调用能力检测
# ---------------------------------------------------------------------------
# 某些 Bedrock 模型不支持工具/函数调用。向这些模型发送 toolConfig
# 会导致 ValidationException。我们维护一个已知不支持工具调用的
# 模型模式黑名单，并为它们剥离工具。
#
# 这是保守的做法：未知模型默认假设支持工具。
# 如果模型因工具相关的 ValidationException 失败，请将其添加到此处。

_NON_TOOL_CALLING_PATTERNS = [
    "deepseek.r1",          # DeepSeek R1 —— 仅推理，不支持工具
    "deepseek-r1",          # 备用 ID 格式
    "stability.",           # 图像生成模型
    "cohere.embed",         # 嵌入模型
    "amazon.titan-embed",   # 嵌入模型
]


def _model_supports_tool_use(model_id: str) -> bool:
    """Return True if the model is expected to support tool/function calling.

    Models in the denylist are known to reject toolConfig in the Converse API.
    Unknown models default to True (assume tool support).
    """
    model_lower = model_id.lower()
    return not any(pattern in model_lower for pattern in _NON_TOOL_CALLING_PATTERNS)


def is_anthropic_bedrock_model(model_id: str) -> bool:
    """Return True if the model is an Anthropic Claude model on Bedrock.

    These models should use the AnthropicBedrock SDK path for full feature
    parity (prompt caching, thinking budgets, adaptive thinking).
    Non-Claude models use the Converse API path.

    Matches:
      - ``anthropic.claude-*`` (foundation model IDs)
      - ``us.anthropic.claude-*`` (US inference profiles)
      - ``global.anthropic.claude-*`` (global inference profiles)
      - ``eu.anthropic.claude-*`` (EU inference profiles)
    """
    model_lower = model_id.lower()
    # Strip regional prefix if present
    for prefix in ("us.", "global.", "eu.", "ap.", "jp."):
        if model_lower.startswith(prefix):
            model_lower = model_lower[len(prefix):]
            break
    return model_lower.startswith("anthropic.claude")


# ---------------------------------------------------------------------------
# 消息格式转换：OpenAI → Bedrock Converse
# ---------------------------------------------------------------------------

def convert_tools_to_converse(tools: List[Dict]) -> List[Dict]:
    """将 OpenAI 格式的工具定义转换为 Bedrock Converse 的 ``toolConfig``。

    OpenAI 格式::

        {"type": "function", "function": {"name": "...", "description": "...",
         "parameters": {"type": "object", "properties": {...}}}}

    Converse 格式::

        {"toolSpec": {"name": "...", "description": "...",
         "inputSchema": {"json": {"type": "object", "properties": {...}}}}}
    """
    if not tools:
        return []
    result = []
    for t in tools:
        fn = t.get("function", {})
        name = fn.get("name", "")
        description = fn.get("description", "")
        parameters = fn.get("parameters", {"type": "object", "properties": {}})
        result.append({
            "toolSpec": {
                "name": name,
                "description": description,
                "inputSchema": {"json": parameters},
            }
        })
    return result


def _convert_content_to_converse(content) -> List[Dict]:
    """将 OpenAI 消息内容（字符串或列表）转换为 Converse 内容块。

    处理:
      - 纯文本字符串 → [{"text": "..."}]
      - 包含 text/image_url 部分的内容数组 → 混合文本/图片块

    过滤空文本块——Bedrock 的 Converse API 会拒绝文本内容块中
    ``text`` 字段为空的消息（ValidationException:
    "text content blocks must be non-empty"）。参考：issue #9486。
    """
    if content is None:
        return [{"text": " "}]
    if isinstance(content, str):
        return [{"text": content}] if content.strip() else [{"text": " "}]
    if isinstance(content, list):
        blocks = []
        for part in content:
            if isinstance(part, str):
                blocks.append({"text": part})
                continue
            if not isinstance(part, dict):
                continue
            part_type = part.get("type", "")
            if part_type == "text":
                text = part.get("text", "")
                blocks.append({"text": text if text else " "})
            elif part_type == "image_url":
                image_url = part.get("image_url", {})
                url = image_url.get("url", "") if isinstance(image_url, dict) else ""
                if url.startswith("data:"):
                    # data:image/jpeg;base64,/9j/4AAQ... — base64 编码的内联图片
                    header, _, data = url.partition(",")
                    media_type = "image/jpeg"
                    if header.startswith("data:"):
                        mime_part = header[5:].split(";")[0]
                        if mime_part:
                            media_type = mime_part
                    blocks.append({
                        "image": {
                            "format": media_type.split("/")[-1] if "/" in media_type else "jpeg",
                            "source": {"bytes": data},
                        }
                    })
                else:
                    # 远程 URL —— Converse 不支持直接使用 URL，
                    # 作为文本引用包含给模型使用。
                    blocks.append({"text": f"[Image: {url}]"})
        return blocks if blocks else [{"text": " "}]
    return [{"text": str(content)}]


def convert_messages_to_converse(
    messages: List[Dict],
) -> Tuple[Optional[List[Dict]], List[Dict]]:
    """将 OpenAI 格式的消息转换为 Bedrock Converse 格式。

    返回 ``(system_prompt, converse_messages)``，其中：
      - ``system_prompt`` 是系统内容块列表（或 None）
      - ``converse_messages`` 是 Converse 格式的对话

    处理:
      - 系统消息 → 提取为系统提示词
      - 用户消息 → ``{"role": "user", "content": [...]}``
      - 助手消息 → ``{"role": "assistant", "content": [...]}``
      - 工具调用 → ``{"toolUse": {"toolUseId": ..., "name": ..., "input": ...}}``
      - 工具结果 → ``{"toolResult": {"toolUseId": ..., "content": [...]}}``

    Converse 要求严格的用户/助手交替。连续的相同角色消息
    会被合并为单条消息。
    """
    system_blocks: List[Dict] = []
    converse_msgs: List[Dict] = []

    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content")

        if role == "system":
            # 系统消息作为系统提示词
            if isinstance(content, str) and content.strip():
                system_blocks.append({"text": content})
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        system_blocks.append({"text": part.get("text", "")})
                    elif isinstance(part, str):
                        system_blocks.append({"text": part})
            continue

        if role == "tool":
            # 工具结果消息 → 合并到前一个用户轮次中
            tool_call_id = msg.get("tool_call_id", "")
            result_content = content if isinstance(content, str) else json.dumps(content)
            tool_result_block = {
                "toolResult": {
                    "toolUseId": tool_call_id,
                    "content": [{"text": result_content}],
                }
            }
            # 在 Converse 中，工具结果放在 "user" 角色的消息中
            if converse_msgs and converse_msgs[-1]["role"] == "user":
                converse_msgs[-1]["content"].append(tool_result_block)
            else:
                converse_msgs.append({
                    "role": "user",
                    "content": [tool_result_block],
                })
            continue

        if role == "assistant":
            content_blocks = []
            # 转换文本内容
            if isinstance(content, str) and content.strip():
                content_blocks.append({"text": content})
            elif isinstance(content, list):
                content_blocks.extend(_convert_content_to_converse(content))

            # 转换工具调用
            tool_calls = msg.get("tool_calls", [])
            for tc in (tool_calls or []):
                fn = tc.get("function", {})
                args_str = fn.get("arguments", "{}")
                try:
                    args_dict = json.loads(args_str) if isinstance(args_str, str) else args_str
                except (json.JSONDecodeError, TypeError):
                    args_dict = {}
                content_blocks.append({
                    "toolUse": {
                        "toolUseId": tc.get("id", ""),
                        "name": fn.get("name", ""),
                        "input": args_dict,
                    }
                })

            if not content_blocks:
                content_blocks = [{"text": " "}]

            # 如果需要，与前一条助手消息合并（严格交替要求）
            if converse_msgs and converse_msgs[-1]["role"] == "assistant":
                converse_msgs[-1]["content"].extend(content_blocks)
            else:
                converse_msgs.append({
                    "role": "assistant",
                    "content": content_blocks,
                })
            continue

        if role == "user":
            content_blocks = _convert_content_to_converse(content)
            # 如果需要，与前一条用户消息合并（严格交替要求）
            if converse_msgs and converse_msgs[-1]["role"] == "user":
                converse_msgs[-1]["content"].extend(content_blocks)
            else:
                converse_msgs.append({
                    "role": "user",
                    "content": content_blocks,
                })
            continue

    # Converse 要求第一条消息来自用户
    if converse_msgs and converse_msgs[0]["role"] != "user":
        converse_msgs.insert(0, {"role": "user", "content": [{"text": " "}]})

    # Converse 要求最后一条消息来自用户
    if converse_msgs and converse_msgs[-1]["role"] != "user":
        converse_msgs.append({"role": "user", "content": [{"text": " "}]})

    return (system_blocks if system_blocks else None, converse_msgs)


# ---------------------------------------------------------------------------
# 响应格式转换：Bedrock Converse → OpenAI
# ---------------------------------------------------------------------------

def _converse_stop_reason_to_openai(stop_reason: str) -> str:
    """将 Bedrock Converse 停止原因映射为 OpenAI 的 finish_reason 值。"""
    mapping = {
        "end_turn": "stop",
        "stop_sequence": "stop",
        "tool_use": "tool_calls",
        "max_tokens": "length",
        "content_filtered": "content_filter",
        "guardrail_intervened": "content_filter",
    }
    return mapping.get(stop_reason, "stop")


def normalize_converse_response(response: Dict) -> SimpleNamespace:
    """将 Bedrock Converse API 响应转换为 OpenAI 兼容对象。

    ``run_agent.py`` 中的代理循环期望响应形状类似
    ``openai.ChatCompletion``——此函数充当桥梁。

    返回包含以下属性的 SimpleNamespace：
      - ``.choices[0].message.content`` — 文本响应
      - ``.choices[0].message.tool_calls`` — 工具调用列表（如有）
      - ``.choices[0].finish_reason`` — stop/tool_calls/length
      - ``.usage`` — token 使用统计
    """
    output = response.get("output", {})
    message = output.get("message", {})
    content_blocks = message.get("content", [])
    stop_reason = response.get("stopReason", "end_turn")

    text_parts = []
    tool_calls = []

    for block in content_blocks:
        if "text" in block:
            text_parts.append(block["text"])
        elif "toolUse" in block:
            tu = block["toolUse"]
            tool_calls.append(SimpleNamespace(
                id=tu.get("toolUseId", ""),
                type="function",
                function=SimpleNamespace(
                    name=tu.get("name", ""),
                    arguments=json.dumps(tu.get("input", {})),
                ),
            ))

    # 构建消息对象
    msg = SimpleNamespace(
        role="assistant",
        content="\n".join(text_parts) if text_parts else None,
        tool_calls=tool_calls if tool_calls else None,
    )

    # 构建 token 使用统计
    usage_data = response.get("usage", {})
    usage = SimpleNamespace(
        prompt_tokens=usage_data.get("inputTokens", 0),
        completion_tokens=usage_data.get("outputTokens", 0),
        total_tokens=(
            usage_data.get("inputTokens", 0) + usage_data.get("outputTokens", 0)
        ),
    )

    finish_reason = _converse_stop_reason_to_openai(stop_reason)
    if tool_calls and finish_reason == "stop":
        finish_reason = "tool_calls"

    choice = SimpleNamespace(
        index=0,
        message=msg,
        finish_reason=finish_reason,
    )

    return SimpleNamespace(
        choices=[choice],
        usage=usage,
        model=response.get("modelId", ""),
    )


# ---------------------------------------------------------------------------
# 流式响应转换
# ---------------------------------------------------------------------------

def normalize_converse_stream_events(event_stream) -> SimpleNamespace:
    """消费 Bedrock ConverseStream 事件流并构建 OpenAI 兼容响应。

    按顺序处理流事件：
      - ``messageStart`` — 角色信息
      - ``contentBlockStart`` — 新的文本或 toolUse 块
      - ``contentBlockDelta`` — 增量文本或 toolUse 输入
      - ``contentBlockStop`` — 块完成
      - ``messageStop`` — 停止原因
      - ``metadata`` — 使用统计

    返回与 ``normalize_converse_response()`` 相同形状的对象。
    """
    return stream_converse_with_callbacks(event_stream)


def stream_converse_with_callbacks(
    event_stream,
    on_text_delta=None,
    on_tool_start=None,
    on_reasoning_delta=None,
    on_interrupt_check=None,
) -> SimpleNamespace:
    """带实时回调处理 Bedrock ConverseStream 事件流。

    这是核心流处理函数，同时驱动 CLI 的实时 token 显示
    和网关的渐进式消息更新。

    参数:
        event_stream: boto3 ``converse_stream()`` 的响应，包含一个
            ``stream`` 键，其值为可迭代的事件。
        on_text_delta: 每个文本块到达时调用。仅在未见到 tool_use 块时触发
            （与 Anthropic 和 chat_completions 流式路径语义一致）。
        on_tool_start: 当 toolUse 块开始时以工具名称调用。
            让 TUI 在生成工具参数时显示 spinner。
        on_reasoning_delta: 推理/思考文本块的回调。
            Bedrock 在支持的模型上通过 ``reasoning`` 内容块 delta
            输出思考过程（Claude 4.6+）。
        on_interrupt_check: 每个事件时调用。如果代理被中断
            且需要停止流式传输，应返回 True。

    返回:
        OpenAI 兼容的 SimpleNamespace 响应，形状与
        ``normalize_converse_response()`` 一致。
    """
    text_parts: List[str] = []
    tool_calls: List[SimpleNamespace] = []
    current_tool: Optional[Dict] = None
    current_text_buffer: List[str] = []
    has_tool_use = False
    stop_reason = "end_turn"
    usage_data: Dict[str, int] = {}

    for event in event_stream.get("stream", []):
        # 检查是否被中断
        if on_interrupt_check and on_interrupt_check():
            break

        if "contentBlockStart" in event:
            start = event["contentBlockStart"].get("start", {})
            if "toolUse" in start:
                has_tool_use = True
                # 刷新已累积的文本
                if current_text_buffer:
                    text_parts.append("".join(current_text_buffer))
                    current_text_buffer = []
                current_tool = {
                    "toolUseId": start["toolUse"].get("toolUseId", ""),
                    "name": start["toolUse"].get("name", ""),
                    "input_json": "",
                }
                if on_tool_start:
                    on_tool_start(current_tool["name"])

        elif "contentBlockDelta" in event:
            delta = event["contentBlockDelta"].get("delta", {})
            if "text" in delta:
                text = delta["text"]
                current_text_buffer.append(text)
                # 仅在没有工具调用时触发文本 delta 回调
                # （与 Anthropic/chat_completions 流式语义一致）
                if on_text_delta and not has_tool_use:
                    on_text_delta(text)
            elif "toolUse" in delta:
                if current_tool is not None:
                    current_tool["input_json"] += delta["toolUse"].get("input", "")
            elif "reasoningContent" in delta:
                # Claude 4.6+ 在 Bedrock 上通过 reasoningContent 输出思考过程
                reasoning = delta["reasoningContent"]
                if isinstance(reasoning, dict):
                    thinking_text = reasoning.get("text", "")
                    if thinking_text and on_reasoning_delta:
                        on_reasoning_delta(thinking_text)

        elif "contentBlockStop" in event:
            if current_tool is not None:
                try:
                    input_dict = json.loads(current_tool["input_json"]) if current_tool["input_json"] else {}
                except (json.JSONDecodeError, TypeError):
                    input_dict = {}
                tool_calls.append(SimpleNamespace(
                    id=current_tool["toolUseId"],
                    type="function",
                    function=SimpleNamespace(
                        name=current_tool["name"],
                        arguments=json.dumps(input_dict),
                    ),
                ))
                current_tool = None
            elif current_text_buffer:
                text_parts.append("".join(current_text_buffer))
                current_text_buffer = []

        elif "messageStop" in event:
            stop_reason = event["messageStop"].get("stopReason", "end_turn")

        elif "metadata" in event:
            meta_usage = event["metadata"].get("usage", {})
            usage_data = {
                "inputTokens": meta_usage.get("inputTokens", 0),
                "outputTokens": meta_usage.get("outputTokens", 0),
            }

    # 刷新剩余文本
    if current_text_buffer:
        text_parts.append("".join(current_text_buffer))

    msg = SimpleNamespace(
        role="assistant",
        content="\n".join(text_parts) if text_parts else None,
        tool_calls=tool_calls if tool_calls else None,
    )

    usage = SimpleNamespace(
        prompt_tokens=usage_data.get("inputTokens", 0),
        completion_tokens=usage_data.get("outputTokens", 0),
        total_tokens=(
            usage_data.get("inputTokens", 0) + usage_data.get("outputTokens", 0)
        ),
    )

    finish_reason = _converse_stop_reason_to_openai(stop_reason)
    if tool_calls and finish_reason == "stop":
        finish_reason = "tool_calls"

    choice = SimpleNamespace(
        index=0,
        message=msg,
        finish_reason=finish_reason,
    )

    return SimpleNamespace(
        choices=[choice],
        usage=usage,
        model="",
    )


# ---------------------------------------------------------------------------
# 高层 API：调用 Bedrock Converse
# ---------------------------------------------------------------------------

def build_converse_kwargs(
    model: str,
    messages: List[Dict],
    tools: Optional[List[Dict]] = None,
    max_tokens: int = 4096,
    temperature: Optional[float] = None,
    top_p: Optional[float] = None,
    stop_sequences: Optional[List[str]] = None,
    guardrail_config: Optional[Dict] = None,
) -> Dict[str, Any]:
    """构建 ``bedrock-runtime.converse()`` 或 ``converse_stream()`` 的参数字典。

    将 OpenAI 格式的输入转换为 Converse API 参数。
    """
    system_prompt, converse_messages = convert_messages_to_converse(messages)

    kwargs: Dict[str, Any] = {
        "modelId": model,
        "messages": converse_messages,
        "inferenceConfig": {
            "maxTokens": max_tokens,
        },
    }

    if system_prompt:
        kwargs["system"] = system_prompt

    if temperature is not None:
        kwargs["inferenceConfig"]["temperature"] = temperature

    if top_p is not None:
        kwargs["inferenceConfig"]["topP"] = top_p

    if stop_sequences:
        kwargs["inferenceConfig"]["stopSequences"] = stop_sequences

    if tools:
        converse_tools = convert_tools_to_converse(tools)
        if converse_tools:
            # 某些 Bedrock 模型不支持工具/函数调用（例如
            # DeepSeek R1、纯推理模型）。向这些模型发送 toolConfig
            # 会导致 ValidationException → 重试循环 → 失败。
            # 为已知不支持工具调用的模型移除工具并警告用户。
            # 参考：PR #7920 来自 @ptlally 的反馈，模式来自 PR #4346。
            if _model_supports_tool_use(model):
                kwargs["toolConfig"] = {"tools": converse_tools}
            else:
                logger.warning(
                    "Model %s does not support tool calling — tools stripped. "
                    "The agent will operate in text-only mode.", model
                )

    if guardrail_config:
        kwargs["guardrailConfig"] = guardrail_config

    return kwargs


def call_converse(
    region: str,
    model: str,
    messages: List[Dict],
    tools: Optional[List[Dict]] = None,
    max_tokens: int = 4096,
    temperature: Optional[float] = None,
    top_p: Optional[float] = None,
    stop_sequences: Optional[List[str]] = None,
    guardrail_config: Optional[Dict] = None,
) -> SimpleNamespace:
    """调用 Bedrock Converse API（非流式）并返回 OpenAI 兼容响应。

    这是代理循环使用 Bedrock 提供者时的主要入口点。
    """
    client = _get_bedrock_runtime_client(region)
    kwargs = build_converse_kwargs(
        model=model,
        messages=messages,
        tools=tools,
        max_tokens=max_tokens,
        temperature=temperature,
        top_p=top_p,
        stop_sequences=stop_sequences,
        guardrail_config=guardrail_config,
    )

    response = client.converse(**kwargs)
    return normalize_converse_response(response)


def call_converse_stream(
    region: str,
    model: str,
    messages: List[Dict],
    tools: Optional[List[Dict]] = None,
    max_tokens: int = 4096,
    temperature: Optional[float] = None,
    top_p: Optional[float] = None,
    stop_sequences: Optional[List[str]] = None,
    guardrail_config: Optional[Dict] = None,
) -> SimpleNamespace:
    """调用 Bedrock ConverseStream API 并返回 OpenAI 兼容响应。

    消费完整的流并返回组装后的响应。如需带 delta 回调的
    真正流式传输，请使用 ``iter_converse_stream()``。
    """
    client = _get_bedrock_runtime_client(region)
    kwargs = build_converse_kwargs(
        model=model,
        messages=messages,
        tools=tools,
        max_tokens=max_tokens,
        temperature=temperature,
        top_p=top_p,
        stop_sequences=stop_sequences,
        guardrail_config=guardrail_config,
    )

    response = client.converse_stream(**kwargs)
    return normalize_converse_stream_events(response)


# ---------------------------------------------------------------------------
# 模型发现
# ---------------------------------------------------------------------------

_discovery_cache: Dict[str, Any] = {}
_DISCOVERY_CACHE_TTL_SECONDS = 3600


def reset_discovery_cache():
    """清除模型发现缓存。用于测试。"""
    _discovery_cache.clear()


def discover_bedrock_models(
    region: str,
    provider_filter: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """发现可用的 Bedrock 基础模型和推理配置文件。

    返回模型信息字典列表，包含以下键：
      - ``id``: 模型 ID（例如 "anthropic.claude-sonnet-4-6-20250514-v1:0"）
      - ``name``: 人类可读的名称
      - ``provider``: 模型提供者（例如 "Anthropic"、"Amazon"、"Meta"）
      - ``input_modalities``: 输入类型列表（例如 ["TEXT", "IMAGE"]）
      - ``output_modalities``: 输出类型列表
      - ``streaming``: 是否支持流式传输

    每个区域的结果缓存 1 小时，以避免重复 API 调用。

    对应 OpenClaw 中 ``extensions/amazon-bedrock/discovery.ts``
    的 ``discoverBedrockModels()``。
    """
    import time

    cache_key = f"{region}:{','.join(sorted(provider_filter or []))}"
    cached = _discovery_cache.get(cache_key)
    if cached and (time.time() - cached["timestamp"]) < _DISCOVERY_CACHE_TTL_SECONDS:
        return cached["models"]

    try:
        client = _get_bedrock_control_client(region)
    except Exception as e:
        logger.warning("Failed to create Bedrock client for model discovery: %s", e)
        return []

    models = []
    seen_ids = set()
    filter_set = {f.lower() for f in (provider_filter or [])}

    # 1. 发现基础模型
    try:
        response = client.list_foundation_models()
        for summary in response.get("modelSummaries", []):
            model_id = (summary.get("modelId") or "").strip()
            if not model_id:
                continue

            # 应用提供者过滤器
            if filter_set:
                provider_name = (summary.get("providerName") or "").lower()
                model_prefix = model_id.split(".")[0].lower() if "." in model_id else ""
                if provider_name not in filter_set and model_prefix not in filter_set:
                    continue

            # 仅包含活跃的、支持流式传输的、文本输出模型
            lifecycle = summary.get("modelLifecycle", {})
            if lifecycle.get("status", "").upper() != "ACTIVE":
                continue
            if not summary.get("responseStreamingSupported", False):
                continue
            output_mods = summary.get("outputModalities", [])
            if "TEXT" not in output_mods:
                continue

            models.append({
                "id": model_id,
                "name": (summary.get("modelName") or model_id).strip(),
                "provider": (summary.get("providerName") or "").strip(),
                "input_modalities": summary.get("inputModalities", []),
                "output_modalities": output_mods,
                "streaming": True,
            })
            seen_ids.add(model_id.lower())
    except Exception as e:
        logger.warning("Failed to list Bedrock foundation models: %s", e)

    # 2. 发现推理配置文件（跨区域，更好的容量）
    try:
        profiles = []
        next_token = None
        while True:
            kwargs = {}
            if next_token:
                kwargs["nextToken"] = next_token
            response = client.list_inference_profiles(**kwargs)
            for profile in response.get("inferenceProfileSummaries", []):
                profiles.append(profile)
            next_token = response.get("nextToken")
            if not next_token:
                break

        for profile in profiles:
            profile_id = (profile.get("inferenceProfileId") or "").strip()
            if not profile_id:
                continue
            if profile.get("status") != "ACTIVE":
                continue
            if profile_id.lower() in seen_ids:
                continue

            # 对底层模型应用提供者过滤器
            if filter_set:
                profile_models = profile.get("models", [])
                matches = any(
                    _extract_provider_from_arn(m.get("modelArn", "")).lower() in filter_set
                    for m in profile_models
                )
                if not matches:
                    continue

            models.append({
                "id": profile_id,
                "name": (profile.get("inferenceProfileName") or profile_id).strip(),
                "provider": "inference-profile",
                "input_modalities": ["TEXT"],
                "output_modalities": ["TEXT"],
                "streaming": True,
            })
            seen_ids.add(profile_id.lower())
    except Exception as e:
        logger.debug("Skipping inference profile discovery: %s", e)

    # 排序：全局跨区域配置文件优先（推荐），然后按字母顺序
    models.sort(key=lambda m: (
        0 if m["id"].startswith("global.") else 1,
        m["name"].lower(),
    ))

    _discovery_cache[cache_key] = {
        "timestamp": time.time(),
        "models": models,
    }
    return models


def _extract_provider_from_arn(arn: str) -> str:
    """从 Bedrock 模型 ARN 中提取模型提供者。

    示例: "arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-v2"
    → "anthropic"
    """
    match = re.search(r"foundation-model/([^.]+)", arn)
    return match.group(1) if match else ""


def get_bedrock_model_ids(region: str) -> List[str]:
    """返回给定区域可用的 Bedrock 模型 ID 平面列表。

    ``discover_bedrock_models()`` 的便捷包装器，用于模型选择 UI。
    """
    models = discover_bedrock_models(region)
    return [m["id"] for m in models]


# ---------------------------------------------------------------------------
# 错误分类 —— Bedrock 特有的异常
# ---------------------------------------------------------------------------
# 对应 OpenClaw 中 extensions/amazon-bedrock/register.sync.runtime.ts
# 的 classifyFailoverReason() 和 matchesContextOverflowError()。

# 表示输入上下文超过模型 token 限制的模式。
# run_agent.py 使用这些模式来触发上下文压缩而非重试。
CONTEXT_OVERFLOW_PATTERNS = [
    re.compile(r"ValidationException.*(?:input is too long|max input token|input token.*exceed)", re.IGNORECASE),
    re.compile(r"ValidationException.*(?:exceeds? the (?:maximum|max) (?:number of )?(?:input )?tokens)", re.IGNORECASE),
    re.compile(r"ModelStreamErrorException.*(?:Input is too long|too many input tokens)", re.IGNORECASE),
]

# 限流/速率限制错误的模式——应触发退避+重试。
THROTTLE_PATTERNS = [
    re.compile(r"ThrottlingException", re.IGNORECASE),
    re.compile(r"Too many concurrent requests", re.IGNORECASE),
    re.compile(r"ServiceQuotaExceededException", re.IGNORECASE),
]

# 瞬态过载模式——模型暂时不可用。
OVERLOAD_PATTERNS = [
    re.compile(r"ModelNotReadyException", re.IGNORECASE),
    re.compile(r"ModelTimeoutException", re.IGNORECASE),
    re.compile(r"InternalServerException", re.IGNORECASE),
]


def is_context_overflow_error(error_message: str) -> bool:
    """如果错误表示输入上下文过大，返回 True。

    当返回 True 时，代理应压缩上下文后重试，
    而非将其视为致命错误。
    """
    return any(p.search(error_message) for p in CONTEXT_OVERFLOW_PATTERNS)


def classify_bedrock_error(error_message: str) -> str:
    """为重试/故障转移决策对 Bedrock 错误进行分类。

    返回:
      - ``"context_overflow"`` — 输入过长，压缩后重试
      - ``"rate_limit"`` — 被限流，退避后重试
      - ``"overloaded"`` — 模型暂时不可用，延迟后重试
      - ``"unknown"`` — 未分类的错误
    """
    if is_context_overflow_error(error_message):
        return "context_overflow"
    if any(p.search(error_message) for p in THROTTLE_PATTERNS):
        return "rate_limit"
    if any(p.search(error_message) for p in OVERLOAD_PATTERNS):
        return "overloaded"
    return "unknown"


# ---------------------------------------------------------------------------
# Bedrock 模型上下文长度
# ---------------------------------------------------------------------------
# 当 Bedrock API 未暴露上下文窗口大小时的静态回退表。
# 在动态检测不可用时由 agent/model_metadata.py 使用。

BEDROCK_CONTEXT_LENGTHS: Dict[str, int] = {
    # Anthropic Claude 在 Bedrock 上的模型
    "anthropic.claude-opus-4-6":     200_000,
    "anthropic.claude-sonnet-4-6":   200_000,
    "anthropic.claude-sonnet-4-5":   200_000,
    "anthropic.claude-haiku-4-5":    200_000,
    "anthropic.claude-opus-4":       200_000,
    "anthropic.claude-sonnet-4":     200_000,
    "anthropic.claude-3-5-sonnet":   200_000,
    "anthropic.claude-3-5-haiku":    200_000,
    "anthropic.claude-3-opus":       200_000,
    "anthropic.claude-3-sonnet":     200_000,
    "anthropic.claude-3-haiku":      200_000,
    # Amazon Nova 模型
    "amazon.nova-pro":               300_000,
    "amazon.nova-lite":              300_000,
    "amazon.nova-micro":             128_000,
    # Meta Llama 模型
    "meta.llama4-maverick":          128_000,
    "meta.llama4-scout":             128_000,
    "meta.llama3-3-70b-instruct":    128_000,
    # Mistral 模型
    "mistral.mistral-large":         128_000,
    # DeepSeek 模型
    "deepseek.v3":                   128_000,
}

# 未知 Bedrock 模型的默认值
BEDROCK_DEFAULT_CONTEXT_LENGTH = 128_000


def get_bedrock_context_length(model_id: str) -> int:
    """查找 Bedrock 模型的上下文窗口大小。

    使用子字符串匹配，以便带版本号的 ID 如
    ``anthropic.claude-sonnet-4-6-20250514-v1:0`` 能正确解析。
    """
    model_lower = model_id.lower()
    best_key = ""
    best_val = BEDROCK_DEFAULT_CONTEXT_LENGTH
    for key, val in BEDROCK_CONTEXT_LENGTHS.items():
        if key in model_lower and len(key) > len(best_key):
            best_key = key
            best_val = val
    return best_val
