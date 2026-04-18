"""兼容 OpenAI 接口的门面，底层与 Google Cloud Code Assist 后端通信。

该适配器使 Hermes 可以像使用标准 OpenAI 风格的聊天补全端点一样使用
``google-gemini-cli`` 提供商，而实际的 HTTP 请求发送至
``cloudcode-pa.googleapis.com/v1internal:{generateContent,
streamGenerateContent}``，并通过 OAuth PKCE 获取 Bearer 访问令牌。

架构
----
- ``GeminiCloudCodeClient`` 暴露 ``.chat.completions.create(**kwargs)``
  方法，模拟 ``run_agent.py`` 使用的 OpenAI SDK 子集。
- 传入的 OpenAI ``messages[]`` / ``tools[]`` / ``tool_choice`` 被转换为
  Gemini 原生的 ``contents[]`` / ``tools[].functionDeclarations`` /
  ``toolConfig`` / ``systemInstruction`` 格式。
- 请求体按 Code Assist API 的要求包装为
  ``{project, model, user_prompt_id, request}``。
- 响应（``candidates[].content.parts[]``）被转换回
  OpenAI ``choices[0].message`` 格式，包含 ``content`` + ``tool_calls``。
- 流式传输使用 SSE（``?alt=sse``），产出 OpenAI 格式的增量 chunk。

归属
----
翻译语义参照 jenslys/opencode-gemini-auth（MIT）和公开的 Gemini API 文档。
请求信封格式（``{project, model, user_prompt_id, request}``）无公开文档；
它是从 opencode-gemini-auth 和 clawdbot 实现中逆向工程得来的。
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from types import SimpleNamespace
from typing import Any, Dict, Iterator, List, Optional

import httpx

from agent import google_oauth
from agent.google_code_assist import (
    CODE_ASSIST_ENDPOINT,
    FREE_TIER_ID,
    CodeAssistError,
    ProjectContext,
    resolve_project_context,
)

logger = logging.getLogger(__name__)


# =============================================================================
# 请求转换：OpenAI → Gemini
# =============================================================================

# OpenAI 角色到 Gemini 角色的映射
_ROLE_MAP_OPENAI_TO_GEMINI = {
    "user": "user",
    "assistant": "model",
    "system": "user",   # 通过 systemInstruction 单独处理
    "tool": "user",     # functionResponse 包装在 user 角色的 turn 中
    "function": "user",
}


def _coerce_content_to_text(content: Any) -> str:
    """OpenAI 的 content 可能是字符串或部件列表；将其归约为纯文本。"""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        pieces: List[str] = []
        for p in content:
            if isinstance(p, str):
                pieces.append(p)
            elif isinstance(p, dict):
                if p.get("type") == "text" and isinstance(p.get("text"), str):
                    pieces.append(p["text"])
                # 多模态（image_url 等）——暂时跳过；记录日志
                elif p.get("type") in ("image_url", "input_audio"):
                    logger.debug("Dropping multimodal part (not yet supported): %s", p.get("type"))
        return "\n".join(pieces)
    return str(content)


def _translate_tool_call_to_gemini(tool_call: Dict[str, Any]) -> Dict[str, Any]:
    """将 OpenAI tool_call 转换为 Gemini functionCall 部件。"""
    fn = tool_call.get("function") or {}
    args_raw = fn.get("arguments", "")
    try:
        args = json.loads(args_raw) if isinstance(args_raw, str) and args_raw else {}
    except json.JSONDecodeError:
        args = {"_raw": args_raw}
    if not isinstance(args, dict):
        args = {"_value": args}
    return {
        "functionCall": {
            "name": fn.get("name") or "",
            "args": args,
        },
        # 哨兵签名——与 opencode-gemini-auth 的方式一致。
        # 没有这个标记，Code Assist 会拒绝非自身链路发起的函数调用。
        "thoughtSignature": "skip_thought_signature_validator",
    }


def _translate_tool_result_to_gemini(message: Dict[str, Any]) -> Dict[str, Any]:
    """将 OpenAI tool 角色消息转换为 Gemini functionResponse 部件。

    函数名不直接出现在 OpenAI tool 消息中；它必须通过发起调用的 assistant
    消息传递。简化起见，我们在消息上查找 ``name``（OpenAI SDK 会复制到那里）
    或通过 ``tool_call_id`` 交叉引用。
    """
    name = str(message.get("name") or message.get("tool_call_id") or "tool")
    content = _coerce_content_to_text(message.get("content"))
    # Gemini 期望响应以 dict 形式放在 `response` 下。
    # 纯文本包装为 {"output": "..."}。
    try:
        parsed = json.loads(content) if content.strip().startswith(("{", "[")) else None
    except json.JSONDecodeError:
        parsed = None
    response = parsed if isinstance(parsed, dict) else {"output": content}
    return {
        "functionResponse": {
            "name": name,
            "response": response,
        },
    }


def _build_gemini_contents(
    messages: List[Dict[str, Any]],
) -> tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """将 OpenAI messages[] 转换为 Gemini contents[] + systemInstruction。"""
    system_text_parts: List[str] = []
    contents: List[Dict[str, Any]] = []

    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role") or "user")

        if role == "system":
            system_text_parts.append(_coerce_content_to_text(msg.get("content")))
            continue

        # 工具结果消息——生成包含 functionResponse 的 user 角色 turn
        if role == "tool" or role == "function":
            contents.append({
                "role": "user",
                "parts": [_translate_tool_result_to_gemini(msg)],
            })
            continue

        gemini_role = _ROLE_MAP_OPENAI_TO_GEMINI.get(role, "user")
        parts: List[Dict[str, Any]] = []

        text = _coerce_content_to_text(msg.get("content"))
        if text:
            parts.append({"text": text})

        # assistant 消息可以携带 tool_calls
        tool_calls = msg.get("tool_calls") or []
        if isinstance(tool_calls, list):
            for tc in tool_calls:
                if isinstance(tc, dict):
                    parts.append(_translate_tool_call_to_gemini(tc))

        if not parts:
            # Gemini 拒绝空的 parts；直接跳过该 turn
            continue

        contents.append({"role": gemini_role, "parts": parts})

    system_instruction: Optional[Dict[str, Any]] = None
    joined_system = "\n".join(p for p in system_text_parts if p).strip()
    if joined_system:
        system_instruction = {
            "role": "system",
            "parts": [{"text": joined_system}],
        }

    return contents, system_instruction


def _translate_tools_to_gemini(tools: Any) -> List[Dict[str, Any]]:
    """将 OpenAI tools[] 转换为 Gemini tools[].functionDeclarations[]。"""
    if not isinstance(tools, list) or not tools:
        return []
    declarations: List[Dict[str, Any]] = []
    for t in tools:
        if not isinstance(t, dict):
            continue
        fn = t.get("function") or {}
        if not isinstance(fn, dict):
            continue
        name = fn.get("name")
        if not name:
            continue
        decl = {"name": str(name)}
        if fn.get("description"):
            decl["description"] = str(fn["description"])
        params = fn.get("parameters")
        if isinstance(params, dict):
            decl["parameters"] = params
        declarations.append(decl)
    if not declarations:
        return []
    return [{"functionDeclarations": declarations}]


def _translate_tool_choice_to_gemini(tool_choice: Any) -> Optional[Dict[str, Any]]:
    """将 OpenAI tool_choice 转换为 Gemini toolConfig.functionCallingConfig。"""
    if tool_choice is None:
        return None
    if isinstance(tool_choice, str):
        if tool_choice == "auto":
            return {"functionCallingConfig": {"mode": "AUTO"}}
        if tool_choice == "required":
            return {"functionCallingConfig": {"mode": "ANY"}}
        if tool_choice == "none":
            return {"functionCallingConfig": {"mode": "NONE"}}
    if isinstance(tool_choice, dict):
        fn = tool_choice.get("function") or {}
        name = fn.get("name")
        if name:
            return {
                "functionCallingConfig": {
                    "mode": "ANY",
                    "allowedFunctionNames": [str(name)],
                },
            }
    return None


def _normalize_thinking_config(config: Any) -> Optional[Dict[str, Any]]:
    """接受 thinkingBudget / thinkingLevel / includeThoughts（及 snake_case 形式）。"""
    if not isinstance(config, dict) or not config:
        return None
    budget = config.get("thinkingBudget", config.get("thinking_budget"))
    level = config.get("thinkingLevel", config.get("thinking_level"))
    include = config.get("includeThoughts", config.get("include_thoughts"))
    normalized: Dict[str, Any] = {}
    if isinstance(budget, (int, float)):
        normalized["thinkingBudget"] = int(budget)
    if isinstance(level, str) and level.strip():
        normalized["thinkingLevel"] = level.strip().lower()
    if isinstance(include, bool):
        normalized["includeThoughts"] = include
    return normalized or None


def build_gemini_request(
    *,
    messages: List[Dict[str, Any]],
    tools: Any = None,
    tool_choice: Any = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    top_p: Optional[float] = None,
    stop: Any = None,
    thinking_config: Any = None,
) -> Dict[str, Any]:
    """构建内层 Gemini 请求体（放入 ``request`` 包装器内部）。"""
    contents, system_instruction = _build_gemini_contents(messages)

    body: Dict[str, Any] = {"contents": contents}
    if system_instruction is not None:
        body["systemInstruction"] = system_instruction

    gemini_tools = _translate_tools_to_gemini(tools)
    if gemini_tools:
        body["tools"] = gemini_tools
    tool_cfg = _translate_tool_choice_to_gemini(tool_choice)
    if tool_cfg is not None:
        body["toolConfig"] = tool_cfg

    generation_config: Dict[str, Any] = {}
    if isinstance(temperature, (int, float)):
        generation_config["temperature"] = float(temperature)
    if isinstance(max_tokens, int) and max_tokens > 0:
        generation_config["maxOutputTokens"] = max_tokens
    if isinstance(top_p, (int, float)):
        generation_config["topP"] = float(top_p)
    if isinstance(stop, str) and stop:
        generation_config["stopSequences"] = [stop]
    elif isinstance(stop, list) and stop:
        generation_config["stopSequences"] = [str(s) for s in stop if s]
    normalized_thinking = _normalize_thinking_config(thinking_config)
    if normalized_thinking:
        generation_config["thinkingConfig"] = normalized_thinking
    if generation_config:
        body["generationConfig"] = generation_config

    return body


def wrap_code_assist_request(
    *,
    project_id: str,
    model: str,
    inner_request: Dict[str, Any],
    user_prompt_id: Optional[str] = None,
) -> Dict[str, Any]:
    """将内层 Gemini 请求包装在 Code Assist 信封中。"""
    return {
        "project": project_id,
        "model": model,
        "user_prompt_id": user_prompt_id or str(uuid.uuid4()),
        "request": inner_request,
    }


# =============================================================================
# 响应转换：Gemini → OpenAI
# =============================================================================

def _translate_gemini_response(
    resp: Dict[str, Any],
    model: str,
) -> SimpleNamespace:
    """非流式 Gemini 响应 -> OpenAI 格式的 SimpleNamespace。

    Code Assist 会将实际的 Gemini 响应包装在 ``response`` 中，
    因此如果存在则先解包。
    """
    inner = resp.get("response") if isinstance(resp.get("response"), dict) else resp

    candidates = inner.get("candidates") or []
    if not isinstance(candidates, list) or not candidates:
        return _empty_response(model)

    cand = candidates[0]
    content_obj = cand.get("content") if isinstance(cand, dict) else {}
    parts = content_obj.get("parts") if isinstance(content_obj, dict) else []

    text_pieces: List[str] = []
    reasoning_pieces: List[str] = []
    tool_calls: List[SimpleNamespace] = []

    for i, part in enumerate(parts or []):
        if not isinstance(part, dict):
            continue
        # 思考部件是模型内部推理——作为 reasoning 表面化，
        # 不混入 content。
        if part.get("thought") is True:
            if isinstance(part.get("text"), str):
                reasoning_pieces.append(part["text"])
            continue
        if isinstance(part.get("text"), str):
            text_pieces.append(part["text"])
            continue
        fc = part.get("functionCall")
        if isinstance(fc, dict) and fc.get("name"):
            try:
                args_str = json.dumps(fc.get("args") or {}, ensure_ascii=False)
            except (TypeError, ValueError):
                args_str = "{}"
            tool_calls.append(SimpleNamespace(
                id=f"call_{uuid.uuid4().hex[:12]}",
                type="function",
                index=i,
                function=SimpleNamespace(name=str(fc["name"]), arguments=args_str),
            ))

    finish_reason = "tool_calls" if tool_calls else _map_gemini_finish_reason(
        str(cand.get("finishReason") or "")
    )

    usage_meta = inner.get("usageMetadata") or {}
    usage = SimpleNamespace(
        prompt_tokens=int(usage_meta.get("promptTokenCount") or 0),
        completion_tokens=int(usage_meta.get("candidatesTokenCount") or 0),
        total_tokens=int(usage_meta.get("totalTokenCount") or 0),
        prompt_tokens_details=SimpleNamespace(
            cached_tokens=int(usage_meta.get("cachedContentTokenCount") or 0),
        ),
    )

    message = SimpleNamespace(
        role="assistant",
        content="".join(text_pieces) if text_pieces else None,
        tool_calls=tool_calls or None,
        reasoning="".join(reasoning_pieces) or None,
        reasoning_content="".join(reasoning_pieces) or None,
        reasoning_details=None,
    )
    choice = SimpleNamespace(
        index=0,
        message=message,
        finish_reason=finish_reason,
    )
    return SimpleNamespace(
        id=f"chatcmpl-{uuid.uuid4().hex[:12]}",
        object="chat.completion",
        created=int(time.time()),
        model=model,
        choices=[choice],
        usage=usage,
    )


def _empty_response(model: str) -> SimpleNamespace:
    message = SimpleNamespace(
        role="assistant", content="", tool_calls=None,
        reasoning=None, reasoning_content=None, reasoning_details=None,
    )
    choice = SimpleNamespace(index=0, message=message, finish_reason="stop")
    usage = SimpleNamespace(
        prompt_tokens=0, completion_tokens=0, total_tokens=0,
        prompt_tokens_details=SimpleNamespace(cached_tokens=0),
    )
    return SimpleNamespace(
        id=f"chatcmpl-{uuid.uuid4().hex[:12]}",
        object="chat.completion",
        created=int(time.time()),
        model=model,
        choices=[choice],
        usage=usage,
    )


def _map_gemini_finish_reason(reason: str) -> str:
    mapping = {
        "STOP": "stop",
        "MAX_TOKENS": "length",
        "SAFETY": "content_filter",
        "RECITATION": "content_filter",
        "OTHER": "stop",
    }
    return mapping.get(reason.upper(), "stop")


# =============================================================================
# 流式 SSE 迭代器
# =============================================================================

class _GeminiStreamChunk(SimpleNamespace):
    """模拟 OpenAI ChatCompletionChunk，具有 .choices[0].delta 属性。"""
    pass


def _make_stream_chunk(
    *,
    model: str,
    content: str = "",
    tool_call_delta: Optional[Dict[str, Any]] = None,
    finish_reason: Optional[str] = None,
    reasoning: str = "",
) -> _GeminiStreamChunk:
    delta_kwargs: Dict[str, Any] = {"role": "assistant"}
    if content:
        delta_kwargs["content"] = content
    if tool_call_delta is not None:
        delta_kwargs["tool_calls"] = [SimpleNamespace(
            index=tool_call_delta.get("index", 0),
            id=tool_call_delta.get("id") or f"call_{uuid.uuid4().hex[:12]}",
            type="function",
            function=SimpleNamespace(
                name=tool_call_delta.get("name") or "",
                arguments=tool_call_delta.get("arguments") or "",
            ),
        )]
    if reasoning:
        delta_kwargs["reasoning"] = reasoning
        delta_kwargs["reasoning_content"] = reasoning
    delta = SimpleNamespace(**delta_kwargs)
    choice = SimpleNamespace(index=0, delta=delta, finish_reason=finish_reason)
    return _GeminiStreamChunk(
        id=f"chatcmpl-{uuid.uuid4().hex[:12]}",
        object="chat.completion.chunk",
        created=int(time.time()),
        model=model,
        choices=[choice],
        usage=None,
    )


def _iter_sse_events(response: httpx.Response) -> Iterator[Dict[str, Any]]:
    """从 httpx 流式响应中解析 Server-Sent Events。"""
    buffer = ""
    for chunk in response.iter_text():
        if not chunk:
            continue
        buffer += chunk
        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)
            line = line.rstrip("\r")
            if not line:
                continue
            if line.startswith("data: "):
                data = line[6:]
                if data == "[DONE]":
                    return
                try:
                    yield json.loads(data)
                except json.JSONDecodeError:
                    logger.debug("Non-JSON SSE line: %s", data[:200])


def _translate_stream_event(
    event: Dict[str, Any],
    model: str,
    tool_call_indices: Dict[str, int],
) -> List[_GeminiStreamChunk]:
    """解包 Code Assist 信封并产出 OpenAI 格式的 chunk。"""
    inner = event.get("response") if isinstance(event.get("response"), dict) else event
    candidates = inner.get("candidates") or []
    if not candidates:
        return []
    cand = candidates[0]
    if not isinstance(cand, dict):
        return []

    chunks: List[_GeminiStreamChunk] = []

    content = cand.get("content") or {}
    parts = content.get("parts") if isinstance(content, dict) else []
    for part in parts or []:
        if not isinstance(part, dict):
            continue
        if part.get("thought") is True and isinstance(part.get("text"), str):
            chunks.append(_make_stream_chunk(
                model=model, reasoning=part["text"],
            ))
            continue
        if isinstance(part.get("text"), str) and part["text"]:
            chunks.append(_make_stream_chunk(model=model, content=part["text"]))
        fc = part.get("functionCall")
        if isinstance(fc, dict) and fc.get("name"):
            name = str(fc["name"])
            idx = tool_call_indices.setdefault(name, len(tool_call_indices))
            try:
                args_str = json.dumps(fc.get("args") or {}, ensure_ascii=False)
            except (TypeError, ValueError):
                args_str = "{}"
            chunks.append(_make_stream_chunk(
                model=model,
                tool_call_delta={
                    "index": idx,
                    "name": name,
                    "arguments": args_str,
                },
            ))

    finish_reason_raw = str(cand.get("finishReason") or "")
    if finish_reason_raw:
        mapped = _map_gemini_finish_reason(finish_reason_raw)
        if tool_call_indices:
            mapped = "tool_calls"
        chunks.append(_make_stream_chunk(model=model, finish_reason=mapped))
    return chunks


# =============================================================================
# GeminiCloudCodeClient — 兼容 OpenAI 的门面
# =============================================================================

MARKER_BASE_URL = "cloudcode-pa://google"


class _GeminiChatCompletions:
    def __init__(self, client: "GeminiCloudCodeClient"):
        self._client = client

    def create(self, **kwargs: Any) -> Any:
        return self._client._create_chat_completion(**kwargs)


class _GeminiChatNamespace:
    def __init__(self, client: "GeminiCloudCodeClient"):
        self.completions = _GeminiChatCompletions(client)


class GeminiCloudCodeClient:
    """基于 Code Assist v1internal 的最小化 OpenAI SDK 兼容门面。"""

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        default_headers: Optional[Dict[str, str]] = None,
        project_id: str = "",
        **_: Any,
    ):
        # `api_key` 在这里是占位——真正的认证是通过
        # agent.google_oauth.get_valid_access_token() 在每次调用时获取的 OAuth 访问令牌。
        # 我们接受此参数是为了与 openai.OpenAI 接口保持一致。
        self.api_key = api_key or "google-oauth"
        self.base_url = base_url or MARKER_BASE_URL
        self._default_headers = dict(default_headers or {})
        self._configured_project_id = project_id
        self._project_context: Optional[ProjectContext] = None
        self._project_context_lock = False  # simple single-thread guard
        self.chat = _GeminiChatNamespace(self)
        self.is_closed = False
        self._http = httpx.Client(timeout=httpx.Timeout(connect=15.0, read=600.0, write=30.0, pool=30.0))

    def close(self) -> None:
        self.is_closed = True
        try:
            self._http.close()
        except Exception:
            pass

    # 实现 OpenAI SDK 的上下文管理器式关闭检查
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def _ensure_project_context(self, access_token: str, model: str) -> ProjectContext:
        """延迟解析并缓存此客户端的项目上下文。"""
        if self._project_context is not None:
            return self._project_context

        env_project = google_oauth.resolve_project_id_from_env()
        creds = google_oauth.load_credentials()
        stored_project = creds.project_id if creds else ""

        # 优先使用已存储在凭据中的项目 ID
        if stored_project:
            self._project_context = ProjectContext(
                project_id=stored_project,
                managed_project_id=creds.managed_project_id if creds else "",
                tier_id="",
                source="stored",
            )
            return self._project_context

        ctx = resolve_project_context(
            access_token,
            configured_project_id=self._configured_project_id,
            env_project_id=env_project,
            user_agent_model=model,
        )
        # 将发现的项目 ID 持久化到凭据文件中，
        # 这样下次会话就不需要重新执行发现流程。
        if ctx.project_id or ctx.managed_project_id:
            google_oauth.update_project_ids(
                project_id=ctx.project_id,
                managed_project_id=ctx.managed_project_id,
            )
        self._project_context = ctx
        return ctx

    def _create_chat_completion(
        self,
        *,
        model: str = "gemini-2.5-flash",
        messages: Optional[List[Dict[str, Any]]] = None,
        stream: bool = False,
        tools: Any = None,
        tool_choice: Any = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        top_p: Optional[float] = None,
        stop: Any = None,
        extra_body: Optional[Dict[str, Any]] = None,
        timeout: Any = None,
        **_: Any,
    ) -> Any:
        access_token = google_oauth.get_valid_access_token()
        ctx = self._ensure_project_context(access_token, model)

        thinking_config = None
        if isinstance(extra_body, dict):
            thinking_config = extra_body.get("thinking_config") or extra_body.get("thinkingConfig")

        inner = build_gemini_request(
            messages=messages or [],
            tools=tools,
            tool_choice=tool_choice,
            temperature=temperature,
            max_tokens=max_tokens,
            top_p=top_p,
            stop=stop,
            thinking_config=thinking_config,
        )
        wrapped = wrap_code_assist_request(
            project_id=ctx.project_id,
            model=model,
            inner_request=inner,
        )

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {access_token}",
            "User-Agent": "hermes-agent (gemini-cli-compat)",
            "X-Goog-Api-Client": "gl-python/hermes",
            "x-activity-request-id": str(uuid.uuid4()),
        }
        headers.update(self._default_headers)

        if stream:
            return self._stream_completion(model=model, wrapped=wrapped, headers=headers)

        url = f"{CODE_ASSIST_ENDPOINT}/v1internal:generateContent"
        response = self._http.post(url, json=wrapped, headers=headers)
        if response.status_code != 200:
            raise _gemini_http_error(response)
        try:
            payload = response.json()
        except ValueError as exc:
            raise CodeAssistError(
                f"Invalid JSON from Code Assist: {exc}",
                code="code_assist_invalid_json",
            ) from exc
        return _translate_gemini_response(payload, model=model)

    def _stream_completion(
        self,
        *,
        model: str,
        wrapped: Dict[str, Any],
        headers: Dict[str, str],
    ) -> Iterator[_GeminiStreamChunk]:
        """生成器，产出 OpenAI 格式的流式 chunk。"""
        url = f"{CODE_ASSIST_ENDPOINT}/v1internal:streamGenerateContent?alt=sse"
        stream_headers = dict(headers)
        stream_headers["Accept"] = "text/event-stream"

        def _generator() -> Iterator[_GeminiStreamChunk]:
            try:
                with self._http.stream("POST", url, json=wrapped, headers=stream_headers) as response:
                    if response.status_code != 200:
                        # 读取完整的错误响应体以获得更好的诊断信息
                        response.read()
                        raise _gemini_http_error(response)
                    tool_call_indices: Dict[str, int] = {}
                    for event in _iter_sse_events(response):
                        for chunk in _translate_stream_event(event, model, tool_call_indices):
                            yield chunk
            except httpx.HTTPError as exc:
                raise CodeAssistError(
                    f"Streaming request failed: {exc}",
                    code="code_assist_stream_error",
                ) from exc

        return _generator()


def _gemini_http_error(response: httpx.Response) -> CodeAssistError:
    status = response.status_code
    try:
        body = response.text[:500]
    except Exception:
        body = ""
    # 让 run_agent 的重试逻辑将认证错误识别为可通过 `api_key` 轮换的错误
    code = f"code_assist_http_{status}"
    if status == 401:
        code = "code_assist_unauthorized"
    elif status == 429:
        code = "code_assist_rate_limited"
    return CodeAssistError(
        f"Code Assist returned HTTP {status}: {body}",
        code=code,
    )
