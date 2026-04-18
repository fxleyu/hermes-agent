"""
Home Assistant 平台适配器。

通过 HA WebSocket API 连接，实现实时事件监控。
状态变更事件被转换为 MessageEvent 对象并转发给
Agent 进行处理。出站消息通过 HA 持久通知投递。

依赖项：
- aiohttp（已包含在 messaging extras 中）
- HASS_TOKEN 环境变量（长期访问令牌）
- HASS_URL 环境变量（默认：http://homeassistant.local:8123）
"""

import asyncio
import json
import logging
import os
import time
import uuid
from datetime import datetime
from typing import Any, Dict, Optional, Set

try:
    import aiohttp
    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False
    aiohttp = None  # type: ignore[assignment]

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import (
    BasePlatformAdapter,
    MessageEvent,
    MessageType,
    SendResult,
)

logger = logging.getLogger(__name__)


def check_ha_requirements() -> bool:
    """检查 Home Assistant 依赖项是否可用且已正确配置。"""
    if not AIOHTTP_AVAILABLE:
        return False
    if not os.getenv("HASS_TOKEN"):
        return False
    return True


class HomeAssistantAdapter(BasePlatformAdapter):
    """
    Home Assistant WebSocket 适配器。

    订阅 ``state_changed`` 事件并将其转发为
    MessageEvent 对象。支持域/实体过滤以及
    每个实体的冷却时间，避免事件洪泛。
    """

    MAX_MESSAGE_LENGTH = 4096

    # 重连退避时间表（秒）
    _BACKOFF_STEPS = [5, 10, 30, 60]

    def __init__(self, config: PlatformConfig):
        super().__init__(config, Platform.HOMEASSISTANT)

        # 连接状态
        self._session: Optional["aiohttp.ClientSession"] = None
        self._ws: Optional["aiohttp.ClientWebSocketResponse"] = None
        self._rest_session: Optional["aiohttp.ClientSession"] = None
        self._listen_task: Optional[asyncio.Task] = None
        self._msg_id: int = 0  # WebSocket 消息 ID 计数器

        # 从配置中读取连接参数
        extra = config.extra or {}
        token = config.token or os.getenv("HASS_TOKEN", "")
        url = extra.get("url") or os.getenv("HASS_URL", "http://homeassistant.local:8123")
        self._hass_url: str = url.rstrip("/")
        self._hass_token: str = token

        # 事件过滤配置
        self._watch_domains: Set[str] = set(extra.get("watch_domains", []))    # 要监听的设备域
        self._watch_entities: Set[str] = set(extra.get("watch_entities", []))  # 要监听的特定实体
        self._ignore_entities: Set[str] = set(extra.get("ignore_entities", []))  # 要忽略的实体
        self._watch_all: bool = bool(extra.get("watch_all", False))  # 是否监听所有事件
        self._cooldown_seconds: int = int(extra.get("cooldown_seconds", 30))  # 每个实体的冷却时间

        # 冷却时间跟踪：entity_id -> 上次事件的时间戳
        self._last_event_time: Dict[str, float] = {}

    def _next_id(self) -> int:
        """返回下一个 WebSocket 消息 ID。"""
        self._msg_id += 1
        return self._msg_id

    # ------------------------------------------------------------------
    # 连接生命周期
    # ------------------------------------------------------------------

    async def connect(self) -> bool:
        """连接到 HA WebSocket API 并订阅事件。"""
        if not AIOHTTP_AVAILABLE:
            logger.warning("[%s] aiohttp 未安装。请运行：pip install aiohttp", self.name)
            return False

        if not self._hass_token:
            logger.warning("[%s] 未配置 HASS_TOKEN", self.name)
            return False

        try:
            success = await self._ws_connect()
            if not success:
                return False

            # 创建专用的 REST 会话用于 send() 调用
            self._rest_session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30)
            )

            # 如果没有配置任何事件过滤器，发出警告
            if not self._watch_domains and not self._watch_entities and not self._watch_all:
                logger.warning(
                    "[%s] 未配置 watch_domains、watch_entities 或 watch_all。"
                    "所有 state_changed 事件都将被丢弃。请在 HA 平台配置中"
                    "设置过滤器以接收事件。",
                    self.name,
                )

            # 启动后台事件监听器
            self._listen_task = asyncio.create_task(self._listen_loop())
            self._running = True
            logger.info("[%s] 已连接到 %s", self.name, self._hass_url)
            return True

        except Exception as e:
            logger.error("[%s] 连接失败：%s", self.name, e)
            return False

    async def _ws_connect(self) -> bool:
        """建立 WebSocket 连接并完成认证。"""
        # 将 HTTP URL 转换为 WebSocket URL
        ws_url = self._hass_url.replace("http://", "ws://").replace("https://", "wss://")
        ws_url = f"{ws_url}/api/websocket"

        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30)
        )
        self._ws = await self._session.ws_connect(ws_url, heartbeat=30, timeout=30)

        # 第一步：接收 auth_required 消息
        msg = await self._ws.receive_json()
        if msg.get("type") != "auth_required":
            logger.error("预期收到 auth_required，实际收到：%s", msg.get("type"))
            await self._cleanup_ws()
            return False

        # 第二步：发送认证令牌
        await self._ws.send_json({
            "type": "auth",
            "access_token": self._hass_token,
        })

        # 第三步：等待 auth_ok 确认
        msg = await self._ws.receive_json()
        if msg.get("type") != "auth_ok":
            logger.error("认证失败：%s", msg)
            await self._cleanup_ws()
            return False

        # 第四步：订阅 state_changed 事件
        sub_id = self._next_id()
        await self._ws.send_json({
            "id": sub_id,
            "type": "subscribe_events",
            "event_type": "state_changed",
        })

        # 验证订阅确认
        msg = await self._ws.receive_json()
        if not msg.get("success"):
            logger.error("订阅事件失败：%s", msg)
            await self._cleanup_ws()
            return False

        return True

    async def _cleanup_ws(self) -> None:
        """关闭 WebSocket 连接和会话。"""
        if self._ws and not self._ws.closed:
            await self._ws.close()
        self._ws = None
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    async def disconnect(self) -> None:
        """断开与 Home Assistant 的连接。"""
        self._running = False
        if self._listen_task:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
            self._listen_task = None

        await self._cleanup_ws()
        if self._rest_session and not self._rest_session.closed:
            await self._rest_session.close()
        self._rest_session = None
        logger.info("[%s] 已断开连接", self.name)

    # ------------------------------------------------------------------
    # 事件监听器
    # ------------------------------------------------------------------

    async def _listen_loop(self) -> None:
        """主事件循环，支持自动重连。"""
        backoff_idx = 0

        while self._running:
            try:
                await self._read_events()
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.warning("[%s] WebSocket 错误：%s", self.name, e)

            if not self._running:
                return

            # 按退避时间表等待后重连
            delay = self._BACKOFF_STEPS[min(backoff_idx, len(self._BACKOFF_STEPS) - 1)]
            logger.info("[%s] 将在 %d 秒后重连...", self.name, delay)
            await asyncio.sleep(delay)
            backoff_idx += 1

            try:
                await self._cleanup_ws()
                success = await self._ws_connect()
                if success:
                    backoff_idx = 0  # 重连成功后重置退避计数
                    logger.info("[%s] 已重新连接", self.name)
            except Exception as e:
                logger.warning("[%s] 重连失败：%s", self.name, e)

    async def _read_events(self) -> None:
        """从 WebSocket 读取事件直到断开连接。"""
        if self._ws is None or self._ws.closed:
            return
        async for ws_msg in self._ws:
            if ws_msg.type == aiohttp.WSMsgType.TEXT:
                try:
                    data = json.loads(ws_msg.data)
                    if data.get("type") == "event":
                        await self._handle_ha_event(data.get("event", {}))
                except json.JSONDecodeError:
                    logger.debug("来自 HA WebSocket 的无效 JSON：%s", ws_msg.data[:200])
            elif ws_msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                break

    async def _handle_ha_event(self, event: Dict[str, Any]) -> None:
        """处理来自 Home Assistant 的 state_changed 事件。"""
        event_data = event.get("data", {})
        entity_id: str = event_data.get("entity_id", "")

        if not entity_id:
            return

        # 应用忽略过滤器
        if entity_id in self._ignore_entities:
            return

        # 应用域/实体监听过滤器（默认关闭——需要显式配置
        # watch_domains、watch_entities 或 watch_all 才会转发事件）
        domain = entity_id.split(".")[0] if "." in entity_id else ""
        if self._watch_domains or self._watch_entities:
            domain_match = domain in self._watch_domains if self._watch_domains else False
            entity_match = entity_id in self._watch_entities if self._watch_entities else False
            if not domain_match and not entity_match:
                return
        elif not self._watch_all:
            # 未配置任何过滤器且 watch_all 关闭——丢弃事件
            return

        # 应用冷却时间（避免同一实体的事件过于频繁）
        now = time.time()
        last = self._last_event_time.get(entity_id, 0)
        if (now - last) < self._cooldown_seconds:
            return
        self._last_event_time[entity_id] = now

        # 构建人类可读的消息描述
        old_state = event_data.get("old_state", {})
        new_state = event_data.get("new_state", {})
        message = self._format_state_change(entity_id, old_state, new_state)

        if not message:
            return

        # 构建 MessageEvent 并转发给消息处理器
        source = self.build_source(
            chat_id="ha_events",
            chat_name="Home Assistant Events",
            chat_type="channel",
            user_id="homeassistant",
            user_name="Home Assistant",
        )

        msg_event = MessageEvent(
            text=message,
            message_type=MessageType.TEXT,
            source=source,
            message_id=f"ha_{entity_id}_{int(now)}",
            timestamp=datetime.now(),
        )

        await self.handle_message(msg_event)

    @staticmethod
    def _format_state_change(
        entity_id: str,
        old_state: Dict[str, Any],
        new_state: Dict[str, Any],
    ) -> Optional[str]:
        """将 state_changed 事件转换为人类可读的描述文本。"""
        if not new_state:
            return None

        old_val = old_state.get("state", "unknown") if old_state else "unknown"
        new_val = new_state.get("state", "unknown")

        # 如果状态实际上没有变化，跳过
        if old_val == new_val:
            return None

        friendly_name = new_state.get("attributes", {}).get("friendly_name", entity_id)
        domain = entity_id.split(".")[0] if "." in entity_id else ""

        # 按设备域进行特定格式化
        if domain == "climate":
            # 空调/暖通设备：显示模式变化及当前/目标温度
            attrs = new_state.get("attributes", {})
            temp = attrs.get("current_temperature", "?")
            target = attrs.get("temperature", "?")
            return (
                f"[Home Assistant] {friendly_name}: HVAC mode changed from "
                f"'{old_val}' to '{new_val}' (current: {temp}, target: {target})"
            )

        if domain == "sensor":
            # 传感器：显示数值变化及单位
            unit = new_state.get("attributes", {}).get("unit_of_measurement", "")
            return (
                f"[Home Assistant] {friendly_name}: changed from "
                f"{old_val}{unit} to {new_val}{unit}"
            )

        if domain == "binary_sensor":
            # 二元传感器：显示触发/清除状态
            return (
                f"[Home Assistant] {friendly_name}: "
                f"{'triggered' if new_val == 'on' else 'cleared'} "
                f"(was {'triggered' if old_val == 'on' else 'cleared'})"
            )

        if domain in ("light", "switch", "fan"):
            # 灯光/开关/风扇：显示开/关状态
            return (
                f"[Home Assistant] {friendly_name}: turned "
                f"{'on' if new_val == 'on' else 'off'}"
            )

        if domain == "alarm_control_panel":
            # 报警面板：显示报警状态变化
            return (
                f"[Home Assistant] {friendly_name}: alarm state changed from "
                f"'{old_val}' to '{new_val}'"
            )

        # 通用兜底格式
        return (
            f"[Home Assistant] {friendly_name} ({entity_id}): "
            f"changed from '{old_val}' to '{new_val}'"
        )

    # ------------------------------------------------------------------
    # 出站消息发送
    # ------------------------------------------------------------------

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """通过 HA REST API 发送通知（persistent_notification.create）。

        使用 REST API 而非 WebSocket 发送，以避免与事件监听循环
        共用同一个 WS 连接时的竞态条件。
        """
        url = f"{self._hass_url}/api/services/persistent_notification/create"
        headers = {
            "Authorization": f"Bearer {self._hass_token}",
            "Content-Type": "application/json",
        }
        payload = {
            "title": "Hermes Agent",
            "message": content[:self.MAX_MESSAGE_LENGTH],
        }

        try:
            if self._rest_session:
                async with self._rest_session.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status < 300:
                        return SendResult(success=True, message_id=uuid.uuid4().hex[:12])
                    else:
                        body = await resp.text()
                        return SendResult(success=False, error=f"HTTP {resp.status}: {body}")
            else:
                # 没有持久化 REST 会话时，创建临时会话
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        url,
                        headers=headers,
                        json=payload,
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as resp:
                        if resp.status < 300:
                            return SendResult(success=True, message_id=uuid.uuid4().hex[:12])
                        else:
                            body = await resp.text()
                            return SendResult(success=False, error=f"HTTP {resp.status}: {body}")

        except asyncio.TimeoutError:
            return SendResult(success=False, error="向 HA 发送通知超时")
        except Exception as e:
            return SendResult(success=False, error=str(e))

    async def send_typing(self, chat_id: str, metadata=None) -> None:
        """Home Assistant 不支持正在输入指示器。"""

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        """返回 HA 事件通道的基本信息。"""
        return {
            "name": "Home Assistant Events",
            "type": "channel",
            "url": self._hass_url,
        }
