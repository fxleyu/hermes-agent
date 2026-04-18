"""API 错误分类模块，用于智能故障转移与恢复。

提供结构化的 API 错误分类体系和按优先级排列的分类管道，
用于确定正确的恢复操作（重试、轮换凭据、回退到其他提供商、
压缩上下文，或中止）。

替代了分散的内联字符串匹配，提供了一个集中化的分类器，
run_agent.py 中的主重试循环在每次 API 失败时都会查询此分类器。
"""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


# ── 错误分类体系 ──────────────────────────────────────────────────────

class FailoverReason(enum.Enum):
    """API 调用失败原因 —— 决定恢复策略。"""

    # 认证/授权相关
    auth = "auth"                        # 临时性认证失败 (401/403) —— 刷新/轮换
    auth_permanent = "auth_permanent"    # 刷新后认证仍失败 —— 中止

    # 计费/配额相关
    billing = "billing"                  # 402 或确认的额度耗尽 —— 立即轮换
    rate_limit = "rate_limit"            # 429 或基于配额的限流 —— 退避后轮换

    # 服务端相关
    overloaded = "overloaded"            # 503/529 —— 提供商过载，退避等待
    server_error = "server_error"        # 500/502 —— 内部服务器错误，重试

    # 传输层相关
    timeout = "timeout"                  # 连接/读取超时 —— 重建客户端并重试

    # 上下文/负载相关
    context_overflow = "context_overflow"  # 上下文过大 —— 压缩，不做故障转移
    payload_too_large = "payload_too_large"  # 413 —— 压缩负载

    # 模型相关
    model_not_found = "model_not_found"  # 404 或无效模型 —— 回退到其他模型

    # 请求格式相关
    format_error = "format_error"        # 400 错误请求 —— 中止或剥离后重试

    # 提供商特定
    thinking_signature = "thinking_signature"  # Anthropic 思维块签名无效
    long_context_tier = "long_context_tier"    # Anthropic "额外用量"层级限制

    # 兜底
    unknown = "unknown"                  # 无法分类 —— 带退避重试


# ── 分类结果 ───────────────────────────────────────────────

@dataclass
class ClassifiedError:
    """API 错误的结构化分类结果，包含恢复提示。"""

    reason: FailoverReason
    status_code: Optional[int] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    message: str = ""
    error_context: Dict[str, Any] = field(default_factory=dict)

    # 恢复操作提示 —— 重试循环检查这些字段，而不是重新分类错误本身。
    retryable: bool = True
    should_compress: bool = False
    should_rotate_credential: bool = False
    should_fallback: bool = False

    @property
    def is_auth(self) -> bool:
        return self.reason in (FailoverReason.auth, FailoverReason.auth_permanent)



# ── 提供商特定的错误模式 ──────────────────────────────────────────

# 表示计费额度耗尽（非临时性速率限制）的模式
_BILLING_PATTERNS = [
    "insufficient credits",
    "insufficient_quota",
    "credit balance",
    "credits have been exhausted",
    "top up your credits",
    "payment required",
    "billing hard limit",
    "exceeded your current quota",
    "account is deactivated",
    "plan does not include",
]

# 表示速率限制（临时性，会自动恢复）的模式
_RATE_LIMIT_PATTERNS = [
    "rate limit",
    "rate_limit",
    "too many requests",
    "throttled",
    "requests per minute",
    "tokens per minute",
    "requests per day",
    "try again in",
    "please retry after",
    "resource_exhausted",
    "rate increased too quickly",  # 阿里巴巴/DashScope 限流
    # AWS Bedrock 限流
    "throttlingexception",
    "too many concurrent requests",
    "servicequotaexceededexception",
]

# 需要区分（可能是计费问题或速率限制）的用量限制模式
_USAGE_LIMIT_PATTERNS = [
    "usage limit",
    "quota",
    "limit exceeded",
    "key limit exceeded",
]

# 确认用量限制为临时性（非计费问题）的信号模式
_USAGE_LIMIT_TRANSIENT_SIGNALS = [
    "try again",
    "retry",
    "resets at",
    "reset in",
    "wait",
    "requests remaining",
    "periodic",
    "window",
]

# 从消息文本检测的负载过大模式（无 status_code 属性时使用）。
# 代理和某些后端会在错误消息中嵌入 HTTP 状态码。
_PAYLOAD_TOO_LARGE_PATTERNS = [
    "request entity too large",
    "payload too large",
    "error code: 413",
]

# 上下文溢出模式
_CONTEXT_OVERFLOW_PATTERNS = [
    "context length",
    "context size",
    "maximum context",
    "token limit",
    "too many tokens",
    "reduce the length",
    "exceeds the limit",
    "context window",
    "prompt is too long",
    "prompt exceeds max length",
    "max_tokens",
    "maximum number of tokens",
    # vLLM / 本地推理服务器模式
    "exceeds the max_model_len",
    "max_model_len",
    "prompt length",             # "engine prompt length X exceeds"
    "input is too long",
    "maximum model length",
    # Ollama 模式
    "context length exceeded",
    "truncating input",
    # llama.cpp / llama-server 模式
    "slot context",              # "slot context: N tokens, prompt N tokens"
    "n_ctx_slot",
    # 中文错误消息（部分提供商会返回这些）
    "超过最大长度",
    "上下文长度",
    # AWS Bedrock Converse API 错误模式
    "input is too long",
    "max input token",
    "input token",
    "exceeds the maximum number of input tokens",
]

# 模型未找到模式
_MODEL_NOT_FOUND_PATTERNS = [
    "is not a valid model",
    "invalid model",
    "model not found",
    "model_not_found",
    "does not exist",
    "no such model",
    "unknown model",
    "unsupported model",
]

# 认证模式（非状态码信号）
_AUTH_PATTERNS = [
    "invalid api key",
    "invalid_api_key",
    "authentication",
    "unauthorized",
    "forbidden",
    "invalid token",
    "token expired",
    "token revoked",
    "access denied",
]

# Anthropic 思维块签名模式
_THINKING_SIG_PATTERNS = [
    "signature",  # 需结合 "thinking" 一起检查
]

# 传输层错误类型名
_TRANSPORT_ERROR_TYPES = frozenset({
    "ReadTimeout", "ConnectTimeout", "PoolTimeout",
    "ConnectError", "RemoteProtocolError",
    "ConnectionError", "ConnectionResetError",
    "ConnectionAbortedError", "BrokenPipeError",
    "TimeoutError", "ReadError",
    "ServerDisconnectedError",
    # OpenAI SDK 错误（非 Python 内置类型的子类）
    "APIConnectionError",
    "APITimeoutError",
})

# 服务器断开连接模式（无状态码，但属于传输层问题）
_SERVER_DISCONNECT_PATTERNS = [
    "server disconnected",
    "peer closed connection",
    "connection reset by peer",
    "connection was closed",
    "network connection lost",
    "unexpected eof",
    "incomplete chunked read",
]


# ── 分类管道 ─────────────────────────────────────────────────

def classify_api_error(
    error: Exception,
    *,
    provider: str = "",
    model: str = "",
    approx_tokens: int = 0,
    context_length: int = 200000,
    num_messages: int = 0,
) -> ClassifiedError:
    """将 API 错误分类为结构化的恢复建议。

    按优先级排列的分类管道：
      1. 特殊处理提供商特定模式（思维签名、层级限制）
      2. HTTP 状态码 + 消息感知的细化
      3. 错误码分类（来自响应体）
      4. 消息模式匹配（计费 vs 速率限制 vs 上下文 vs 认证）
      5. 传输层错误启发式
      6. 服务器断开 + 大会话 → 上下文溢出
      7. 兜底：未知（可重试，带退避）

    参数：
        error: API 调用抛出的异常。
        provider: 当前提供商名称（如 "openrouter"、"anthropic"）。
        model: 当前模型标识。
        approx_tokens: 当前上下文的近似 token 数。
        context_length: 当前模型的最大上下文长度。

    返回：
        ClassifiedError，包含原因和恢复操作提示。
    """
    status_code = _extract_status_code(error)
    error_type = type(error).__name__
    body = _extract_error_body(error)
    error_code = _extract_error_code(body)

    # 构建用于模式匹配的综合错误消息字符串。
    # 仅 str(error) 可能不包含响应体消息（如 OpenAI SDK 的
    # APIStatusError.__str__ 返回第一个参数而非响应体）。追加
    # 响应体消息，使得像 "try again" 这样的模式在 402 区分中能被检测到。
    #
    # 同时提取 metadata.raw —— OpenRouter 将上游提供商错误包装在
    # {"error": {"message": "Provider returned error", "metadata":
    # {"raw": "<实际错误 JSON>"}}} 中，真正的错误消息（如
    # "context length exceeded"）仅在内部 JSON 中。
    _raw_msg = str(error).lower()
    _body_msg = ""
    _metadata_msg = ""
    if isinstance(body, dict):
        _err_obj = body.get("error", {})
        if isinstance(_err_obj, dict):
            _body_msg = (_err_obj.get("message") or "").lower()
            # 解析 metadata.raw 中包装的提供商错误
            _metadata = _err_obj.get("metadata", {})
            if isinstance(_metadata, dict):
                _raw_json = _metadata.get("raw") or ""
                if isinstance(_raw_json, str) and _raw_json.strip():
                    try:
                        import json
                        _inner = json.loads(_raw_json)
                        if isinstance(_inner, dict):
                            _inner_err = _inner.get("error", {})
                            if isinstance(_inner_err, dict):
                                _metadata_msg = (_inner_err.get("message") or "").lower()
                    except (json.JSONDecodeError, TypeError):
                        pass
        if not _body_msg:
            _body_msg = (body.get("message") or "").lower()
    # 合并所有消息来源用于模式匹配
    parts = [_raw_msg]
    if _body_msg and _body_msg not in _raw_msg:
        parts.append(_body_msg)
    if _metadata_msg and _metadata_msg not in _raw_msg and _metadata_msg not in _body_msg:
        parts.append(_metadata_msg)
    error_msg = " ".join(parts)
    provider_lower = (provider or "").strip().lower()
    model_lower = (model or "").strip().lower()

    def _result(reason: FailoverReason, **overrides) -> ClassifiedError:
        defaults = {
            "reason": reason,
            "status_code": status_code,
            "provider": provider,
            "model": model,
            "message": _extract_message(error, body),
        }
        defaults.update(overrides)
        return ClassifiedError(**defaults)

    # ── 1. 提供商特定模式（最高优先级）────────────────

    # Anthropic 思维块签名无效（400）。
    # 不限制提供商 —— OpenRouter 代理 Anthropic 错误，所以
    # 即使错误来自 Anthropic，提供商也可能是 "openrouter"。
    # 消息模式（"signature" + "thinking"）足够唯一。
    if (
        status_code == 400
        and "signature" in error_msg
        and "thinking" in error_msg
    ):
        return _result(
            FailoverReason.thinking_signature,
            retryable=True,
            should_compress=False,
        )

    # Anthropic 长上下文层级限制（429 "extra usage" + "long context"）
    if (
        status_code == 429
        and "extra usage" in error_msg
        and "long context" in error_msg
    ):
        return _result(
            FailoverReason.long_context_tier,
            retryable=True,
            should_compress=True,
        )

    # ── 2. HTTP 状态码分类 ──────────────────────────

    if status_code is not None:
        classified = _classify_by_status(
            status_code, error_msg, error_code, body,
            provider=provider_lower, model=model_lower,
            approx_tokens=approx_tokens, context_length=context_length,
            num_messages=num_messages,
            result_fn=_result,
        )
        if classified is not None:
            return classified

    # ── 3. 错误码分类 ────────────────────────────────

    if error_code:
        classified = _classify_by_error_code(error_code, error_msg, _result)
        if classified is not None:
            return classified

    # ── 4. 消息模式匹配（无状态码时）────────────────

    classified = _classify_by_message(
        error_msg, error_type,
        approx_tokens=approx_tokens,
        context_length=context_length,
        result_fn=_result,
    )
    if classified is not None:
        return classified

    # ── 5. 服务器断开 + 大会话 → 上下文溢出 ─────
    # 必须在通用传输层错误捕获之前 —— 大会话上的断开连接
    # 更可能是上下文溢出，而非临时性传输故障。没有此排序，
    # RemoteProtocolError 总是映射为 timeout 而忽略会话大小。

    is_disconnect = any(p in error_msg for p in _SERVER_DISCONNECT_PATTERNS)
    if is_disconnect and not status_code:
        is_large = approx_tokens > context_length * 0.6 or approx_tokens > 120000 or num_messages > 200
        if is_large:
            return _result(
                FailoverReason.context_overflow,
                retryable=True,
                should_compress=True,
            )
        return _result(FailoverReason.timeout, retryable=True)

    # ── 6. 传输层/超时启发式 ───────────────────────────

    if error_type in _TRANSPORT_ERROR_TYPES or isinstance(error, (TimeoutError, ConnectionError, OSError)):
        return _result(FailoverReason.timeout, retryable=True)

    # ── 7. 兜底：未知 ────────────────────────────────────

    return _result(FailoverReason.unknown, retryable=True)


# ── 状态码分类 ──────────────────────────────────────────

def _classify_by_status(
    status_code: int,
    error_msg: str,
    error_code: str,
    body: dict,
    *,
    provider: str,
    model: str,
    approx_tokens: int,
    context_length: int,
    num_messages: int = 0,
    result_fn,
) -> Optional[ClassifiedError]:
    """基于 HTTP 状态码进行分类，结合消息感知细化。"""

    if status_code == 401:
        # 401 本身不可重试 —— 凭据池轮换和提供商特定刷新
        # （Codex、Anthropic、Nous）在 run_agent.py 的可重试性
        # 检查之前运行。如果它们成功，循环 `continue`。
        # 如果失败，retryable=False 确保命中客户端错误中止路径
        #（该路径会先尝试回退）。
        return result_fn(
            FailoverReason.auth,
            retryable=False,
            should_rotate_credential=True,
            should_fallback=True,
        )

    if status_code == 403:
        # OpenRouter 403 "key limit exceeded" 实际上是计费问题
        if "key limit exceeded" in error_msg or "spending limit" in error_msg:
            return result_fn(
                FailoverReason.billing,
                retryable=False,
                should_rotate_credential=True,
                should_fallback=True,
            )
        return result_fn(
            FailoverReason.auth,
            retryable=False,
            should_fallback=True,
        )

    if status_code == 402:
        return _classify_402(error_msg, result_fn)

    if status_code == 404:
        if any(p in error_msg for p in _MODEL_NOT_FOUND_PATTERNS):
            return result_fn(
                FailoverReason.model_not_found,
                retryable=False,
                should_fallback=True,
            )
        # 通用 404 —— 可能是模型或端点问题
        return result_fn(
            FailoverReason.model_not_found,
            retryable=False,
            should_fallback=True,
        )

    if status_code == 413:
        return result_fn(
            FailoverReason.payload_too_large,
            retryable=True,
            should_compress=True,
        )

    if status_code == 429:
        # 长上下文层级已在上面检查过；这里是普通速率限制
        return result_fn(
            FailoverReason.rate_limit,
            retryable=True,
            should_rotate_credential=True,
            should_fallback=True,
        )

    if status_code == 400:
        return _classify_400(
            error_msg, error_code, body,
            provider=provider, model=model,
            approx_tokens=approx_tokens,
            context_length=context_length,
            num_messages=num_messages,
            result_fn=result_fn,
        )

    if status_code in (500, 502):
        return result_fn(FailoverReason.server_error, retryable=True)

    if status_code in (503, 529):
        return result_fn(FailoverReason.overloaded, retryable=True)

    # 其他 4xx —— 不可重试
    if 400 <= status_code < 500:
        return result_fn(
            FailoverReason.format_error,
            retryable=False,
            should_fallback=True,
        )

    # 其他 5xx —— 可重试
    if 500 <= status_code < 600:
        return result_fn(FailoverReason.server_error, retryable=True)

    return None


def _classify_402(error_msg: str, result_fn) -> ClassifiedError:
    """区分 402：计费额度耗尽 vs 临时性用量限制。

    来自 OpenClaw 的关键洞察：某些 402 是伪装成付款错误的临时性
    速率限制。"Usage limit, try again in 5 minutes" 不是计费问题
    —— 而是一个会自动重置的周期性配额。
    """
    # 先检查临时性用量限制信号
    has_usage_limit = any(p in error_msg for p in _USAGE_LIMIT_PATTERNS)
    has_transient_signal = any(p in error_msg for p in _USAGE_LIMIT_TRANSIENT_SIGNALS)

    if has_usage_limit and has_transient_signal:
        # 临时性配额 —— 视为速率限制，而非计费问题
        return result_fn(
            FailoverReason.rate_limit,
            retryable=True,
            should_rotate_credential=True,
            should_fallback=True,
        )

    # 确认是计费额度耗尽
    return result_fn(
        FailoverReason.billing,
        retryable=False,
        should_rotate_credential=True,
        should_fallback=True,
    )


def _classify_400(
    error_msg: str,
    error_code: str,
    body: dict,
    *,
    provider: str,
    model: str,
    approx_tokens: int,
    context_length: int,
    num_messages: int = 0,
    result_fn,
) -> ClassifiedError:
    """分类 400 Bad Request —— 上下文溢出、格式错误或通用错误。"""

    # 来自 400 的上下文溢出
    if any(p in error_msg for p in _CONTEXT_OVERFLOW_PATTERNS):
        return result_fn(
            FailoverReason.context_overflow,
            retryable=True,
            should_compress=True,
        )

    # 某些提供商将模型未找到作为 400 而非 404 返回（如 OpenRouter）。
    if any(p in error_msg for p in _MODEL_NOT_FOUND_PATTERNS):
        return result_fn(
            FailoverReason.model_not_found,
            retryable=False,
            should_fallback=True,
        )

    # 某些提供商将速率限制/计费错误作为 400 而非 429/402 返回。
    # 在回退到 format_error 之前检查这些模式。
    if any(p in error_msg for p in _RATE_LIMIT_PATTERNS):
        return result_fn(
            FailoverReason.rate_limit,
            retryable=True,
            should_rotate_credential=True,
            should_fallback=True,
        )
    if any(p in error_msg for p in _BILLING_PATTERNS):
        return result_fn(
            FailoverReason.billing,
            retryable=False,
            should_rotate_credential=True,
            should_fallback=True,
        )

    # 通用 400 + 大会话 → 可能的上下文溢出
    # Anthropic 有时在上下文过大时返回简单的 "Error" 消息
    err_body_msg = ""
    if isinstance(body, dict):
        err_obj = body.get("error", {})
        if isinstance(err_obj, dict):
            err_body_msg = (err_obj.get("message") or "").strip().lower()
        # Responses API（以及某些提供商）使用扁平 body: {"message": "..."}
        if not err_body_msg:
            err_body_msg = (body.get("message") or "").strip().lower()
    is_generic = len(err_body_msg) < 30 or err_body_msg in ("error", "")
    is_large = approx_tokens > context_length * 0.4 or approx_tokens > 80000 or num_messages > 80

    if is_generic and is_large:
        return result_fn(
            FailoverReason.context_overflow,
            retryable=True,
            should_compress=True,
        )

    # 不可重试的格式错误
    return result_fn(
        FailoverReason.format_error,
        retryable=False,
        should_fallback=True,
    )


# ── 错误码分类 ───────────────────────────────────────────

def _classify_by_error_code(
    error_code: str, error_msg: str, result_fn,
) -> Optional[ClassifiedError]:
    """基于响应体中的结构化错误码进行分类。"""
    code_lower = error_code.lower()

    if code_lower in ("resource_exhausted", "throttled", "rate_limit_exceeded"):
        return result_fn(
            FailoverReason.rate_limit,
            retryable=True,
            should_rotate_credential=True,
        )

    if code_lower in ("insufficient_quota", "billing_not_active", "payment_required"):
        return result_fn(
            FailoverReason.billing,
            retryable=False,
            should_rotate_credential=True,
            should_fallback=True,
        )

    if code_lower in ("model_not_found", "model_not_available", "invalid_model"):
        return result_fn(
            FailoverReason.model_not_found,
            retryable=False,
            should_fallback=True,
        )

    if code_lower in ("context_length_exceeded", "max_tokens_exceeded"):
        return result_fn(
            FailoverReason.context_overflow,
            retryable=True,
            should_compress=True,
        )

    return None


# ── 消息模式分类 ──────────────────────────────────────────

def _classify_by_message(
    error_msg: str,
    error_type: str,
    *,
    approx_tokens: int,
    context_length: int,
    result_fn,
) -> Optional[ClassifiedError]:
    """当没有状态码时，基于错误消息模式进行分类。"""

    # 负载过大模式（来自消息文本，无 status_code 时使用）
    if any(p in error_msg for p in _PAYLOAD_TOO_LARGE_PATTERNS):
        return result_fn(
            FailoverReason.payload_too_large,
            retryable=True,
            should_compress=True,
        )

    # 用量限制模式需要与 402 相同的区分：某些提供商在没有
    # HTTP 状态码的情况下返回 "usage limit" 错误。临时性信号
    # （"try again"、"resets at"、…）意味着这是周期性配额，
    # 而非计费额度耗尽。
    has_usage_limit = any(p in error_msg for p in _USAGE_LIMIT_PATTERNS)
    if has_usage_limit:
        has_transient_signal = any(p in error_msg for p in _USAGE_LIMIT_TRANSIENT_SIGNALS)
        if has_transient_signal:
            return result_fn(
                FailoverReason.rate_limit,
                retryable=True,
                should_rotate_credential=True,
                should_fallback=True,
            )
        return result_fn(
            FailoverReason.billing,
            retryable=False,
            should_rotate_credential=True,
            should_fallback=True,
        )

    # 计费模式
    if any(p in error_msg for p in _BILLING_PATTERNS):
        return result_fn(
            FailoverReason.billing,
            retryable=False,
            should_rotate_credential=True,
            should_fallback=True,
        )

    # 速率限制模式
    if any(p in error_msg for p in _RATE_LIMIT_PATTERNS):
        return result_fn(
            FailoverReason.rate_limit,
            retryable=True,
            should_rotate_credential=True,
            should_fallback=True,
        )

    # 上下文溢出模式
    if any(p in error_msg for p in _CONTEXT_OVERFLOW_PATTERNS):
        return result_fn(
            FailoverReason.context_overflow,
            retryable=True,
            should_compress=True,
        )

    # 认证模式
    # 认证错误不应直接重试 —— 凭据无效，用相同密钥重试
    # 总会失败。设置 retryable=False 以触发凭据轮换
    # （should_rotate_credential=True）或提供商回退，
    # 而非立即重试循环。
    if any(p in error_msg for p in _AUTH_PATTERNS):
        return result_fn(
            FailoverReason.auth,
            retryable=False,
            should_rotate_credential=True,
            should_fallback=True,
        )

    # 模型未找到模式
    if any(p in error_msg for p in _MODEL_NOT_FOUND_PATTERNS):
        return result_fn(
            FailoverReason.model_not_found,
            retryable=False,
            should_fallback=True,
        )

    return None


# ── 辅助函数 ─────────────────────────────────────────────────

def _extract_status_code(error: Exception) -> Optional[int]:
    """遍历错误及其原因链查找 HTTP 状态码。"""
    current = error
    for _ in range(5):  # 最大深度，防止无限循环
        code = getattr(current, "status_code", None)
        if isinstance(code, int):
            return code
        # 某些 SDK 使用 .status 而非 .status_code
        code = getattr(current, "status", None)
        if isinstance(code, int) and 100 <= code < 600:
            return code
        # 遍历原因链
        cause = getattr(current, "__cause__", None) or getattr(current, "__context__", None)
        if cause is None or cause is current:
            break
        current = cause
    return None


def _extract_error_body(error: Exception) -> dict:
    """从 SDK 异常中提取结构化错误体。"""
    body = getattr(error, "body", None)
    if isinstance(body, dict):
        return body
    # 某些错误有 .response.json()
    response = getattr(error, "response", None)
    if response is not None:
        try:
            json_body = response.json()
            if isinstance(json_body, dict):
                return json_body
        except Exception:
            pass
    return {}


def _extract_error_code(body: dict) -> str:
    """从响应体中提取错误码字符串。"""
    if not body:
        return ""
    error_obj = body.get("error", {})
    if isinstance(error_obj, dict):
        code = error_obj.get("code") or error_obj.get("type") or ""
        if isinstance(code, str) and code.strip():
            return code.strip()
    # 顶层错误码
    code = body.get("code") or body.get("error_code") or ""
    if isinstance(code, (str, int)):
        return str(code).strip()
    return ""


def _extract_message(error: Exception, body: dict) -> str:
    """提取最具信息量的错误消息。"""
    # 优先使用结构化的响应体
    if body:
        error_obj = body.get("error", {})
        if isinstance(error_obj, dict):
            msg = error_obj.get("message", "")
            if isinstance(msg, str) and msg.strip():
                return msg.strip()[:500]
        msg = body.get("message", "")
        if isinstance(msg, str) and msg.strip():
            return msg.strip()[:500]
    # 回退到 str(error)
    return str(error)[:500]
