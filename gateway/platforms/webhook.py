"""通用 Webhook 平台适配器。

运行一个 aiohttp HTTP 服务器，接收来自外部服务（GitHub、GitLab、JIRA、
Stripe 等）的 Webhook POST 请求，验证 HMAC 签名，将载荷转换为
Agent 提示词，并将响应路由回源端或其他已配置的平台。

配置位于 config.yaml 的 platforms.webhook.extra.routes 下。
每个路由定义：
  - events：要接受的事件类型（基于请求头过滤）
  - secret：HMAC 签名验证密钥（必填）
  - prompt：使用 Webhook 载荷格式化的模板字符串
  - skills：可选的技能列表，供 Agent 加载
  - deliver：响应发送目标（github_comment、telegram 等）
  - deliver_extra：额外的投递配置（repo、pr_number、chat_id）

安全性：
  - 每个路由必须配置 HMAC 密钥（启动时验证）
  - 每路由限速（固定窗口，可配置）
  - 幂等性缓存防止 Webhook 重试导致重复的 Agent 运行
  - 读取载荷前检查请求体大小限制
  - 将 secret 设为 "INSECURE_NO_AUTH" 可跳过验证（仅限测试）
"""

import asyncio
import hashlib
import hmac
import json
import logging
import re
import subprocess
import time
from typing import Any, Dict, List, Optional

try:
    from aiohttp import web

    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False
    web = None  # type: ignore[assignment]

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import (
    BasePlatformAdapter,
    MessageEvent,
    MessageType,
    SendResult,
)

logger = logging.getLogger(__name__)

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8644
_INSECURE_NO_AUTH = "INSECURE_NO_AUTH"
_DYNAMIC_ROUTES_FILENAME = "webhook_subscriptions.json"


def check_webhook_requirements() -> bool:
    """检查 Webhook 适配器的依赖项是否可用。"""
    return AIOHTTP_AVAILABLE


class WebhookAdapter(BasePlatformAdapter):
    """通用 Webhook 接收器，通过 HTTP POST 触发 Agent 运行。"""

    def __init__(self, config: PlatformConfig):
        super().__init__(config, Platform.WEBHOOK)
        self._host: str = config.extra.get("host", DEFAULT_HOST)
        self._port: int = int(config.extra.get("port", DEFAULT_PORT))
        self._global_secret: str = config.extra.get("secret", "")
        self._static_routes: Dict[str, dict] = config.extra.get("routes", {})
        self._dynamic_routes: Dict[str, dict] = {}
        self._dynamic_routes_mtime: float = 0.0
        self._routes: Dict[str, dict] = dict(self._static_routes)
        self._runner = None

        # 按会话 chat_id 索引的投递信息。
        #
        # 每次 send() 调用都会读取对应 chat_id 的信息（包括状态消息
        # 和最终响应）。通过每次 POST 时的 TTL 清理来保持字典有界——
        # 参见 _prune_delivery_info()。不要在 send() 中 pop，否则
        # 临时状态消息（如回退通知、上下文压力警告）会在最终响应
        # 到达前消耗掉该条目，导致响应静默降级为 "log" 投递类型。
        self._delivery_info: Dict[str, dict] = {}
        self._delivery_info_created: Dict[str, float] = {}

        # 网关运行器引用，用于跨平台投递（由外部设置）
        self.gateway_runner = None

        # 幂等性：最近处理过的投递 ID 的 TTL 缓存。
        # 防止 Webhook 提供商重试时产生重复的 Agent 运行。
        self._seen_deliveries: Dict[str, float] = {}
        self._idempotency_ttl: int = 3600  # 1 小时

        # 限速：每路由的时间戳列表（固定窗口）。
        self._rate_counts: Dict[str, List[float]] = {}
        self._rate_limit: int = int(config.extra.get("rate_limit", 30))  # 每分钟

        # 请求体大小限制（先认证后读取模式）
        self._max_body_bytes: int = int(
            config.extra.get("max_body_bytes", 1_048_576)
        )  # 1MB

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def connect(self) -> bool:
        # 在验证之前加载 Agent 创建的订阅
        self._reload_dynamic_routes()

        # 启动时验证路由——每个路由都必须配置密钥
        for name, route in self._routes.items():
            secret = route.get("secret", self._global_secret)
            if not secret:
                raise ValueError(
                    f"[webhook] 路由 '{name}' 没有 HMAC 密钥。"
                    f"请在路由或全局级别设置 'secret'。"
                    f"如需在测试环境中跳过认证，请将 secret 设为 '{_INSECURE_NO_AUTH}'。"
                )

        app = web.Application()
        app.router.add_get("/health", self._handle_health)
        app.router.add_post("/webhooks/{route_name}", self._handle_webhook)

        # 端口冲突检测——如果端口已被占用则快速失败
        import socket as _socket
        try:
            with _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM) as _s:
                _s.settimeout(1)
                _s.connect(('127.0.0.1', self._port))
            logger.error('[webhook] 端口 %d 已被占用。请在 config.yaml 中设置不同的端口：platforms.webhook.port', self._port)
            return False
        except (ConnectionRefusedError, OSError):
            pass  # 端口可用

        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self._host, self._port)
        await site.start()
        self._mark_connected()

        route_names = ", ".join(self._routes.keys()) or "（无已配置路由）"
        logger.info(
            "[webhook] 已在 %s:%d 上监听——路由：%s",
            self._host,
            self._port,
            route_names,
        )
        return True

    async def disconnect(self) -> None:
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
        self._mark_disconnected()
        logger.info("[webhook] 已断开连接")

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """将 Agent 的响应投递到配置的目标。

        chat_id 格式为 ``webhook:{route}:{delivery_id}``。Webhook 接收时
        存储的投递信息通过 ``.get()``（而非 pop）读取，这样在最终响应
        之前发出的临时状态消息——回退模型通知、上下文压力警告等——
        不会消耗该条目并静默将最终响应降级为 ``log`` 投递类型。
        TTL 清理在 POST 时进行。
        """
        delivery = self._delivery_info.get(chat_id, {})
        deliver_type = delivery.get("deliver", "log")

        if deliver_type == "log":
            logger.info("[webhook] %s 的响应：%s", chat_id, content[:200])
            return SendResult(success=True)

        if deliver_type == "github_comment":
            return await self._deliver_github_comment(content, delivery)

        # 跨平台投递——任何有网关适配器的平台
        if self.gateway_runner and deliver_type in (
            "telegram",
            "discord",
            "slack",
            "signal",
            "sms",
            "whatsapp",
            "matrix",
            "mattermost",
            "homeassistant",
            "email",
            "dingtalk",
            "feishu",
            "wecom",
            "wecom_callback",
            "weixin",
            "bluebubbles",
            "qqbot",
        ):
            return await self._deliver_cross_platform(
                deliver_type, content, delivery
            )

        logger.warning("[webhook] 未知的投递类型：%s", deliver_type)
        return SendResult(
            success=False, error=f"Unknown deliver type: {deliver_type}"
        )

    def _prune_delivery_info(self, now: float) -> None:
        """删除超过幂等性 TTL 的投递信息条目。

        与 ``_seen_deliveries`` 使用相同的清理模式。在每次 POST 时调用，
        确保即使大量 Webhook 触发且从未收到最终响应，
        字典大小也被限制在 ``rate_limit * TTL`` 以内。
        """
        cutoff = now - self._idempotency_ttl
        stale = [
            k
            for k, t in self._delivery_info_created.items()
            if t < cutoff
        ]
        for k in stale:
            self._delivery_info.pop(k, None)
            self._delivery_info_created.pop(k, None)

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        return {"name": chat_id, "type": "webhook"}

    # ------------------------------------------------------------------
    # HTTP 处理器
    # ------------------------------------------------------------------

    async def _handle_health(self, request: "web.Request") -> "web.Response":
        """GET /health——简单的健康检查。"""
        return web.json_response({"status": "ok", "platform": "webhook"})

    def _reload_dynamic_routes(self) -> None:
        """如果文件有变更，从磁盘重新加载 Agent 创建的订阅。"""
        from hermes_constants import get_hermes_home
        hermes_home = get_hermes_home()
        subs_path = hermes_home / _DYNAMIC_ROUTES_FILENAME
        if not subs_path.exists():
            if self._dynamic_routes:
                self._dynamic_routes = {}
                self._routes = dict(self._static_routes)
                logger.debug("[webhook] 动态订阅文件已删除，已清空动态路由")
            return
        try:
            mtime = subs_path.stat().st_mtime
            if mtime <= self._dynamic_routes_mtime:
                return  # 文件没有变更
            data = json.loads(subs_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return
            # 合并：静态路由优先于动态路由
            self._dynamic_routes = {
                k: v for k, v in data.items()
                if k not in self._static_routes
            }
            self._routes = {**self._dynamic_routes, **self._static_routes}
            self._dynamic_routes_mtime = mtime
            logger.info(
                "[webhook] 已重新加载 %d 条动态路由：%s",
                len(self._dynamic_routes),
                ", ".join(self._dynamic_routes.keys()) or "（无）",
            )
        except Exception as e:
            logger.error("[webhook] 重新加载动态路由失败：%s", e)

    async def _handle_webhook(self, request: "web.Request") -> "web.Response":
        """POST /webhooks/{route_name}——接收并处理 Webhook 事件。"""
        # 每次请求时热重载动态订阅（通过 mtime 门控，开销很小）
        self._reload_dynamic_routes()

        route_name = request.match_info.get("route_name", "")
        route_config = self._routes.get(route_name)

        if not route_config:
            return web.json_response(
                {"error": f"Unknown route: {route_name}"}, status=404
            )

        # ── 先认证后读取 ─────────────────────────────────────
        # 在读取完整载荷之前检查 Content-Length。
        content_length = request.content_length or 0
        if content_length > self._max_body_bytes:
            return web.json_response(
                {"error": "Payload too large"}, status=413
            )

        # ── 限速 ────────────────────────────────────────────
        now = time.time()
        window = self._rate_counts.setdefault(route_name, [])
        # 清除 60 秒前的时间戳
        window[:] = [t for t in window if now - t < 60]
        if len(window) >= self._rate_limit:
            return web.json_response(
                {"error": "Rate limit exceeded"}, status=429
            )
        window.append(now)

        # 读取请求体
        try:
            raw_body = await request.read()
        except Exception as e:
            logger.error("[webhook] 读取请求体失败：%s", e)
            return web.json_response({"error": "Bad request"}, status=400)

        # 验证 HMAC 签名（INSECURE_NO_AUTH 测试模式下跳过）
        secret = route_config.get("secret", self._global_secret)
        if secret and secret != _INSECURE_NO_AUTH:
            if not self._validate_signature(request, raw_body, secret):
                logger.warning(
                    "[webhook] 路由 %s 的签名无效", route_name
                )
                return web.json_response(
                    {"error": "Invalid signature"}, status=401
                )

        # 解析载荷
        try:
            payload = json.loads(raw_body)
        except json.JSONDecodeError:
            # 尝试表单编码格式作为兜底
            try:
                import urllib.parse

                payload = dict(
                    urllib.parse.parse_qsl(raw_body.decode("utf-8"))
                )
            except Exception:
                return web.json_response(
                    {"error": "Cannot parse body"}, status=400
                )

        # 检查事件类型过滤器
        event_type = (
            request.headers.get("X-GitHub-Event", "")
            or request.headers.get("X-GitLab-Event", "")
            or payload.get("event_type", "")
            or "unknown"
        )
        allowed_events = route_config.get("events", [])
        if allowed_events and event_type not in allowed_events:
            logger.debug(
                "[webhook] 忽略路由 %s 的事件 %s（允许的事件：%s）",
                route_name,
                event_type,
                allowed_events,
            )
            return web.json_response(
                {"status": "ignored", "event": event_type}
            )

        # 从模板格式化提示词
        prompt_template = route_config.get("prompt", "")
        prompt = self._render_prompt(
            prompt_template, payload, event_type, route_name
        )

        # 如果配置了技能，注入技能内容。
        # 我们直接调用 build_skill_invocation_message() 而不是
        # 使用 /skill-name 斜杠命令——网关的命令解析器会拦截
        # 那些命令并破坏流程。
        skills = route_config.get("skills", [])
        if skills:
            try:
                from agent.skill_commands import (
                    build_skill_invocation_message,
                    get_skill_commands,
                )

                skill_cmds = get_skill_commands()
                for skill_name in skills:
                    cmd_key = f"/{skill_name}"
                    if cmd_key in skill_cmds:
                        skill_content = build_skill_invocation_message(
                            cmd_key, user_instruction=prompt
                        )
                        if skill_content:
                            prompt = skill_content
                            break  # 加载第一个匹配的技能
                    else:
                        logger.warning(
                            "[webhook] 未找到技能 '%s'", skill_name
                        )
            except Exception as e:
                logger.warning("[webhook] 技能加载失败：%s", e)

        # 构建唯一的投递 ID
        delivery_id = request.headers.get(
            "X-GitHub-Delivery",
            request.headers.get("X-Request-ID", str(int(time.time() * 1000))),
        )

        # ── 幂等性 ─────────────────────────────────────────
        # 跳过重复投递（Webhook 重试）。
        now = time.time()
        # 清除过期条目
        self._seen_deliveries = {
            k: v
            for k, v in self._seen_deliveries.items()
            if now - v < self._idempotency_ttl
        }
        if delivery_id in self._seen_deliveries:
            logger.info(
                "[webhook] 跳过重复投递 %s", delivery_id
            )
            return web.json_response(
                {"status": "duplicate", "delivery_id": delivery_id},
                status=200,
            )
        self._seen_deliveries[delivery_id] = now

        # 在会话 key 中使用 delivery_id，这样同一路由上的并发
        # Webhook 会获得独立的 Agent 运行（不会排队/中断）。
        session_chat_id = f"webhook:{route_name}:{delivery_id}"

        # 为 send() 存储投递信息。每次 send() 调用都会读取
        # 此 chat_id 的信息（临时状态消息和最终响应），
        # 所以我们不在 send 中 pop。基于 TTL 的清理保持字典有界。
        deliver_config = {
            "deliver": route_config.get("deliver", "log"),
            "deliver_extra": self._render_delivery_extra(
                route_config.get("deliver_extra", {}), payload
            ),
            "payload": payload,
        }
        self._delivery_info[session_chat_id] = deliver_config
        self._delivery_info_created[session_chat_id] = now
        self._prune_delivery_info(now)

        # 构建来源信息和事件
        source = self.build_source(
            chat_id=session_chat_id,
            chat_name=f"webhook/{route_name}",
            chat_type="webhook",
            user_id=f"webhook:{route_name}",
            user_name=route_name,
        )
        event = MessageEvent(
            text=prompt,
            message_type=MessageType.TEXT,
            source=source,
            raw_message=payload,
            message_id=delivery_id,
        )

        logger.info(
            "[webhook] %s 事件=%s 路由=%s 提示词长度=%d 投递=%s",
            request.method,
            event_type,
            route_name,
            len(prompt),
            delivery_id,
        )

        # 非阻塞——立即返回 202 Accepted
        task = asyncio.create_task(self.handle_message(event))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

        return web.json_response(
            {
                "status": "accepted",
                "route": route_name,
                "event": event_type,
                "delivery_id": delivery_id,
            },
            status=202,
        )

    # ------------------------------------------------------------------
    # 签名验证
    # ------------------------------------------------------------------

    def _validate_signature(
        self, request: "web.Request", body: bytes, secret: str
    ) -> bool:
        """验证 Webhook 签名（支持 GitHub、GitLab 和通用 HMAC-SHA256）。"""
        # GitHub：X-Hub-Signature-256 = sha256=<十六进制>
        gh_sig = request.headers.get("X-Hub-Signature-256", "")
        if gh_sig:
            expected = "sha256=" + hmac.new(
                secret.encode(), body, hashlib.sha256
            ).hexdigest()
            return hmac.compare_digest(gh_sig, expected)

        # GitLab：X-Gitlab-Token = <明文密钥>
        gl_token = request.headers.get("X-Gitlab-Token", "")
        if gl_token:
            return hmac.compare_digest(gl_token, secret)

        # 通用格式：X-Webhook-Signature = <HMAC-SHA256 十六进制>
        generic_sig = request.headers.get("X-Webhook-Signature", "")
        if generic_sig:
            expected = hmac.new(
                secret.encode(), body, hashlib.sha256
            ).hexdigest()
            return hmac.compare_digest(generic_sig, expected)

        # 配置了密钥但没有找到认可的签名头——拒绝请求
        logger.debug(
            "[webhook] 已配置密钥但未找到签名请求头"
        )
        return False

    # ------------------------------------------------------------------
    # 提示词渲染
    # ------------------------------------------------------------------

    def _render_prompt(
        self,
        template: str,
        payload: dict,
        event_type: str,
        route_name: str,
    ) -> str:
        """使用 Webhook 载荷渲染提示词模板。

        支持点号分隔的嵌套字典访问：
        ``{pull_request.title}`` -> ``payload["pull_request"]["title"]``

        特殊令牌 ``{__raw__}`` 将整个载荷以缩进 JSON 格式输出
        （截断为 4000 字符）。适用于监控告警或任何需要 Agent
        查看完整载荷的 Webhook。
        """
        if not template:
            truncated = json.dumps(payload, indent=2)[:4000]
            return (
                f"Webhook event '{event_type}' on route "
                f"'{route_name}':\n\n```json\n{truncated}\n```"
            )

        def _resolve(match: re.Match) -> str:
            key = match.group(1)
            # 特殊令牌：将整个载荷转储为 JSON
            if key == "__raw__":
                return json.dumps(payload, indent=2)[:4000]
            # 按点号分隔逐层访问嵌套字典
            value: Any = payload
            for part in key.split("."):
                if isinstance(value, dict):
                    value = value.get(part, f"{{{key}}}")
                else:
                    return f"{{{key}}}"
            if isinstance(value, (dict, list)):
                return json.dumps(value, indent=2)[:2000]
            return str(value)

        return re.sub(r"\{([a-zA-Z0-9_.]+)\}", _resolve, template)

    def _render_delivery_extra(
        self, extra: dict, payload: dict
    ) -> dict:
        """使用载荷数据渲染 delivery_extra 中的模板值。"""
        rendered: Dict[str, Any] = {}
        for key, value in extra.items():
            if isinstance(value, str):
                rendered[key] = self._render_prompt(value, payload, "", "")
            else:
                rendered[key] = value
        return rendered

    # ------------------------------------------------------------------
    # 响应投递
    # ------------------------------------------------------------------

    async def _deliver_github_comment(
        self, content: str, delivery: dict
    ) -> SendResult:
        """通过 ``gh`` CLI 将 Agent 响应发布为 GitHub PR/Issue 评论。"""
        extra = delivery.get("deliver_extra", {})
        repo = extra.get("repo", "")
        pr_number = extra.get("pr_number", "")

        if not repo or not pr_number:
            logger.error(
                "[webhook] github_comment 投递缺少 repo 或 pr_number"
            )
            return SendResult(
                success=False, error="Missing repo or pr_number"
            )

        try:
            result = subprocess.run(
                [
                    "gh",
                    "pr",
                    "comment",
                    str(pr_number),
                    "--repo",
                    repo,
                    "--body",
                    content,
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                logger.info(
                    "[webhook] 已在 %s#%s 上发布评论", repo, pr_number
                )
                return SendResult(success=True)
            else:
                logger.error(
                    "[webhook] gh pr comment 失败：%s", result.stderr
                )
                return SendResult(success=False, error=result.stderr)
        except FileNotFoundError:
            logger.error(
                "[webhook] 未找到 'gh' CLI——请安装 GitHub CLI 以使用 "
                "github_comment 投递功能"
            )
            return SendResult(
                success=False, error="gh CLI not installed"
            )
        except Exception as e:
            logger.error("[webhook] github_comment 投递错误：%s", e)
            return SendResult(success=False, error=str(e))

    async def _deliver_cross_platform(
        self, platform_name: str, content: str, delivery: dict
    ) -> SendResult:
        """将响应路由到其他平台（telegram、discord 等）。"""
        if not self.gateway_runner:
            return SendResult(
                success=False,
                error="No gateway runner for cross-platform delivery",
            )

        try:
            target_platform = Platform(platform_name)
        except ValueError:
            return SendResult(
                success=False, error=f"Unknown platform: {platform_name}"
            )

        adapter = self.gateway_runner.adapters.get(target_platform)
        if not adapter:
            return SendResult(
                success=False,
                error=f"Platform {platform_name} not connected",
            )

        # 如果 deliver_extra 中没有指定 chat_id，使用默认频道
        extra = delivery.get("deliver_extra", {})
        chat_id = extra.get("chat_id", "")
        if not chat_id:
            home = self.gateway_runner.config.get_home_channel(target_platform)
            if home:
                chat_id = home.chat_id
            else:
                return SendResult(
                    success=False,
                    error=f"No chat_id or home channel for {platform_name}",
                )

        # 从 deliver_extra 中传递 thread_id，以支持 Telegram 论坛话题
        metadata = None
        thread_id = extra.get("message_thread_id") or extra.get("thread_id")
        if thread_id:
            metadata = {"thread_id": thread_id}

        return await adapter.send(chat_id, content, metadata=metadata)
