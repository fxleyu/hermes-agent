"""Google Code Assist API 客户端——项目发现、注册引导、配额。

Code Assist API 驱动 Google 官方的 gemini-cli。它位于
``cloudcode-pa.googleapis.com``，提供：

- 个人 Google 账户的免费层访问（慷慨的每日配额）
- 通过 GCP 项目的付费层访问（Billing / Workspace / Standard / Enterprise）

此模块处理推理之前所需的控制面协商：

1. ``load_code_assist()``——探测用户账户以了解其所在层级
   以及是否已分配 ``cloudaicompanionProject``。
2. ``onboard_user()``——如果用户尚未注册引导（新账户、新免费层等），
   使用选定的层级 + 项目 ID 调用此方法。支持 LRO（长时间运行操作）轮询
   以应对慢速配置。
3. ``retrieve_user_quota()``——获取 ``buckets[]`` 数组，显示每个模型的
   剩余配额，供 ``/gquota`` 斜杠命令使用。

VPC-SC 处理：在 VPC 服务控制边界下的企业账户在 ``load_code_assist``
时会收到 ``SECURITY_POLICY_VIOLATED``。我们捕获此错误并将账户强制设为
``standard-tier``，使调用链仍能成功。

源自 opencode-gemini-auth（MIT）和 clawdbot/extensions/google。
请求/响应格式特定于 Google 的内部 Code Assist API，
无公开文档——我们从参考实现中复制它们。
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# =============================================================================
# 常量
# =============================================================================

CODE_ASSIST_ENDPOINT = "https://cloudcode-pa.googleapis.com"

# 生产环境返回错误时尝试的备用端点
FALLBACK_ENDPOINTS = [
    "https://daily-cloudcode-pa.sandbox.googleapis.com",
    "https://autopush-cloudcode-pa.sandbox.googleapis.com",
]

# Google API 使用的层级标识符
FREE_TIER_ID = "free-tier"
LEGACY_TIER_ID = "legacy-tier"
STANDARD_TIER_ID = "standard-tier"

# 匹配 gemini-cli 指纹的默认 HTTP 请求头。
# Google 可能在这些内部端点上拒绝不被识别的 User-Agent。
_GEMINI_CLI_USER_AGENT = "google-api-nodejs-client/9.15.1 (gzip)"
_X_GOOG_API_CLIENT = "gl-node/24.0.0"
_DEFAULT_REQUEST_TIMEOUT = 30.0
_ONBOARDING_POLL_ATTEMPTS = 12
_ONBOARDING_POLL_INTERVAL_SECONDS = 5.0


class CodeAssistError(RuntimeError):
    def __init__(self, message: str, *, code: str = "code_assist_error") -> None:
        super().__init__(message)
        self.code = code


class ProjectIdRequiredError(CodeAssistError):
    def __init__(self, message: str = "GCP project id required for this tier") -> None:
        super().__init__(message, code="code_assist_project_id_required")


# =============================================================================
# HTTP 基础方法（认证通过每次调用传递的 Bearer 令牌）
# =============================================================================

def _build_headers(access_token: str, *, user_agent_model: str = "") -> Dict[str, str]:
    ua = _GEMINI_CLI_USER_AGENT
    if user_agent_model:
        ua = f"{ua} model/{user_agent_model}"
    return {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {access_token}",
        "User-Agent": ua,
        "X-Goog-Api-Client": _X_GOOG_API_CLIENT,
        "x-activity-request-id": str(uuid.uuid4()),
    }


def _client_metadata() -> Dict[str, str]:
    """精确匹配 Google 的 gemini-cli——未被识别的 metadata 可能被拒绝。"""
    return {
        "ideType": "IDE_UNSPECIFIED",
        "platform": "PLATFORM_UNSPECIFIED",
        "pluginType": "GEMINI",
    }


def _post_json(
    url: str,
    body: Dict[str, Any],
    access_token: str,
    *,
    timeout: float = _DEFAULT_REQUEST_TIMEOUT,
    user_agent_model: str = "",
) -> Dict[str, Any]:
    data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        url, data=data, method="POST",
        headers=_build_headers(access_token, user_agent_model=user_agent_model),
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        # 特殊情况：VPC-SC 违规应可区分
        if _is_vpc_sc_violation(detail):
            raise CodeAssistError(
                f"VPC-SC policy violation: {detail}",
                code="code_assist_vpc_sc",
            ) from exc
        raise CodeAssistError(
            f"Code Assist HTTP {exc.code}: {detail or exc.reason}",
            code=f"code_assist_http_{exc.code}",
        ) from exc
    except urllib.error.URLError as exc:
        raise CodeAssistError(
            f"Code Assist request failed: {exc}",
            code="code_assist_network_error",
        ) from exc


def _is_vpc_sc_violation(body: str) -> bool:
    """从响应体中检测 VPC 服务控制违规。"""
    if not body:
        return False
    try:
        parsed = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return "SECURITY_POLICY_VIOLATED" in body
    # 遍历 Google 使用的嵌套错误结构
    error = parsed.get("error") if isinstance(parsed, dict) else None
    if not isinstance(error, dict):
        return False
    details = error.get("details") or []
    if isinstance(details, list):
        for item in details:
            if isinstance(item, dict):
                reason = item.get("reason") or ""
                if reason == "SECURITY_POLICY_VIOLATED":
                    return True
    msg = str(error.get("message", ""))
    return "SECURITY_POLICY_VIOLATED" in msg


# =============================================================================
# load_code_assist——发现当前层级 + 已分配的项目
# =============================================================================

@dataclass
class CodeAssistProjectInfo:
    """``load_code_assist`` 的返回结果。"""
    current_tier_id: str = ""
    cloudaicompanion_project: str = ""   # Google 管理的项目（免费层）
    allowed_tiers: List[str] = field(default_factory=list)
    raw: Dict[str, Any] = field(default_factory=dict)


def load_code_assist(
    access_token: str,
    *,
    project_id: str = "",
    user_agent_model: str = "",
) -> CodeAssistProjectInfo:
    """调用 ``POST /v1internal:loadCodeAssist``，带有生产 → 沙盒的回退。

    返回 Google 报告的层级 + 项目信息。对于 VPC-SC 违规，
    返回合成的 ``standard-tier`` 结果，以便调用链能继续。
    """
    body: Dict[str, Any] = {
        "metadata": {
            "duetProject": project_id,
            **_client_metadata(),
        },
    }
    if project_id:
        body["cloudaicompanionProject"] = project_id

    endpoints = [CODE_ASSIST_ENDPOINT] + FALLBACK_ENDPOINTS
    last_err: Optional[Exception] = None
    for endpoint in endpoints:
        url = f"{endpoint}/v1internal:loadCodeAssist"
        try:
            resp = _post_json(url, body, access_token, user_agent_model=user_agent_model)
            return _parse_load_response(resp)
        except CodeAssistError as exc:
            if exc.code == "code_assist_vpc_sc":
                logger.info("VPC-SC violation on %s — defaulting to standard-tier", endpoint)
                return CodeAssistProjectInfo(
                    current_tier_id=STANDARD_TIER_ID,
                    cloudaicompanion_project=project_id,
                )
            last_err = exc
            logger.warning("loadCodeAssist failed on %s: %s", endpoint, exc)
            continue
    if last_err:
        raise last_err
    return CodeAssistProjectInfo()


def _parse_load_response(resp: Dict[str, Any]) -> CodeAssistProjectInfo:
    current_tier = resp.get("currentTier") or {}
    tier_id = str(current_tier.get("id") or "") if isinstance(current_tier, dict) else ""
    project = str(resp.get("cloudaicompanionProject") or "")
    allowed = resp.get("allowedTiers") or []
    allowed_ids: List[str] = []
    if isinstance(allowed, list):
        for t in allowed:
            if isinstance(t, dict):
                tid = str(t.get("id") or "")
                if tid:
                    allowed_ids.append(tid)
    return CodeAssistProjectInfo(
        current_tier_id=tier_id,
        cloudaicompanion_project=project,
        allowed_tiers=allowed_ids,
        raw=resp,
    )


# =============================================================================
# onboard_user——在某个层级上配置新用户（含 LRO 轮询）
# =============================================================================

def onboard_user(
    access_token: str,
    *,
    tier_id: str,
    project_id: str = "",
    user_agent_model: str = "",
) -> Dict[str, Any]:
    """调用 ``POST /v1internal:onboardUser`` 来配置用户。

    对于付费层级，``project_id`` 是必需的（抛出 ProjectIdRequiredError）。
    对于免费层级，``project_id`` 是可选的——Google 会分配一个。

    返回最终的操作响应。轮询 ``/v1internal/<name>``，最多
    ``_ONBOARDING_POLL_ATTEMPTS`` x ``_ONBOARDING_POLL_INTERVAL_SECONDS``
    （默认：12 x 5s = 1 分钟）。
    """
    if tier_id != FREE_TIER_ID and tier_id != LEGACY_TIER_ID and not project_id:
        raise ProjectIdRequiredError(
            f"Tier {tier_id!r} requires a GCP project id. "
            "Set HERMES_GEMINI_PROJECT_ID or GOOGLE_CLOUD_PROJECT."
        )

    body: Dict[str, Any] = {
        "tierId": tier_id,
        "metadata": _client_metadata(),
    }
    if project_id:
        body["cloudaicompanionProject"] = project_id

    endpoint = CODE_ASSIST_ENDPOINT
    url = f"{endpoint}/v1internal:onboardUser"
    resp = _post_json(url, body, access_token, user_agent_model=user_agent_model)

    # 如果是 LRO（长时间运行操作）则轮询
    if not resp.get("done"):
        op_name = resp.get("name", "")
        if not op_name:
            return resp
        for attempt in range(_ONBOARDING_POLL_ATTEMPTS):
            time.sleep(_ONBOARDING_POLL_INTERVAL_SECONDS)
            poll_url = f"{endpoint}/v1internal/{op_name}"
            try:
                poll_resp = _post_json(poll_url, {}, access_token, user_agent_model=user_agent_model)
            except CodeAssistError as exc:
                logger.warning("Onboarding poll attempt %d failed: %s", attempt + 1, exc)
                continue
            if poll_resp.get("done"):
                return poll_resp
        logger.warning("Onboarding did not complete within %d attempts", _ONBOARDING_POLL_ATTEMPTS)
    return resp


# =============================================================================
# retrieve_user_quota——用于 /gquota
# =============================================================================

@dataclass
class QuotaBucket:
    model_id: str
    token_type: str = ""
    remaining_fraction: float = 0.0
    reset_time_iso: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)


def retrieve_user_quota(
    access_token: str,
    *,
    project_id: str = "",
    user_agent_model: str = "",
) -> List[QuotaBucket]:
    """调用 ``POST /v1internal:retrieveUserQuota`` 并解析 ``buckets[]``。"""
    body: Dict[str, Any] = {}
    if project_id:
        body["project"] = project_id
    url = f"{CODE_ASSIST_ENDPOINT}/v1internal:retrieveUserQuota"
    resp = _post_json(url, body, access_token, user_agent_model=user_agent_model)
    raw_buckets = resp.get("buckets") or []
    buckets: List[QuotaBucket] = []
    if not isinstance(raw_buckets, list):
        return buckets
    for b in raw_buckets:
        if not isinstance(b, dict):
            continue
        buckets.append(QuotaBucket(
            model_id=str(b.get("modelId") or ""),
            token_type=str(b.get("tokenType") or ""),
            remaining_fraction=float(b.get("remainingFraction") or 0.0),
            reset_time_iso=str(b.get("resetTime") or ""),
            raw=b,
        ))
    return buckets


# =============================================================================
# 项目上下文解析
# =============================================================================

@dataclass
class ProjectContext:
    """给定 OAuth 会话的已解析状态。"""
    project_id: str = ""           # 请求中使用的有效项目 ID
    managed_project_id: str = ""   # Google 分配的项目（免费层）
    tier_id: str = ""
    source: str = ""               # "env"、"config"、"discovered"、"onboarded"


def resolve_project_context(
    access_token: str,
    *,
    configured_project_id: str = "",
    env_project_id: str = "",
    user_agent_model: str = "",
) -> ProjectContext:
    """确定请求使用的项目 ID + 层级。

    优先级：
      1. 如果设置了 configured_project_id 或 env_project_id，直接使用
         并短路（无需发现流程）。
      2. 否则调用 loadCodeAssist 查看 Google 的响应。
      3. 如果尚未分配层级，注册引导用户（默认免费层级）。
    """
    # 短路：调用方提供了项目 ID
    if configured_project_id:
        return ProjectContext(
            project_id=configured_project_id,
            tier_id=STANDARD_TIER_ID,  # 假设为付费层级，因为用户指定了项目 ID
            source="config",
        )
    if env_project_id:
        return ProjectContext(
            project_id=env_project_id,
            tier_id=STANDARD_TIER_ID,
            source="env",
        )

    # 通过 loadCodeAssist 发现
    info = load_code_assist(access_token, user_agent_model=user_agent_model)

    effective_project = info.cloudaicompanion_project
    tier = info.current_tier_id

    if not tier:
        # 用户尚未注册引导——在免费层级上为其配置
        onboard_resp = onboard_user(
            access_token,
            tier_id=FREE_TIER_ID,
            project_id="",
            user_agent_model=user_agent_model,
        )
        # 从注册引导响应中重新解析
        response_body = onboard_resp.get("response") or {}
        if isinstance(response_body, dict):
            effective_project = (
                effective_project
                or str(response_body.get("cloudaicompanionProject") or "")
            )
        tier = FREE_TIER_ID
        source = "onboarded"
    else:
        source = "discovered"

    return ProjectContext(
        project_id=effective_project,
        managed_project_id=effective_project if tier == FREE_TIER_ID else "",
        tier_id=tier,
        source=source,
    )
