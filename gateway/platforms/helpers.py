"""网关平台适配器的共享辅助类。

提取了在 5-7 个适配器中重复出现的通用模式：
消息去重、文本批量聚合、Markdown 格式剥离、
以及线程参与跟踪。
"""

import asyncio
import json
import logging
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Dict, Optional

if TYPE_CHECKING:
    from gateway.platforms.base import BasePlatformAdapter, MessageEvent

logger = logging.getLogger(__name__)


# ─── 消息去重 ────────────────────────────────────────────────────────────────


class MessageDeduplicator:
    """基于 TTL（存活时间）的消息去重缓存。

    替换了之前在 discord、slack、dingtalk、wecom、weixin、
    mattermost 和 feishu 适配器中重复出现的
    ``_seen_messages`` / ``_is_duplicate()`` 模式。

    用法示例::

        self._dedup = MessageDeduplicator()

        # 在消息处理器中：
        if self._dedup.is_duplicate(msg_id):
            return
    """

    def __init__(self, max_size: int = 2000, ttl_seconds: float = 300):
        # 已见消息字典：key 为消息 ID，value 为首次见到的时间戳
        self._seen: Dict[str, float] = {}
        self._max_size = max_size  # 缓存最大容量
        self._ttl = ttl_seconds  # 消息过期时间（秒）

    def is_duplicate(self, msg_id: str) -> bool:
        """如果 *msg_id* 在 TTL 窗口内已经出现过，则返回 True。"""
        if not msg_id:
            return False
        now = time.time()
        if msg_id in self._seen:
            if now - self._seen[msg_id] < self._ttl:
                # 消息在 TTL 窗口内，判定为重复
                return True
            # 条目已过期——删除它并视为新消息
            del self._seen[msg_id]
        # 记录新消息及其时间戳
        self._seen[msg_id] = now
        # 当缓存超过最大容量时，清理过期条目
        if len(self._seen) > self._max_size:
            cutoff = now - self._ttl
            self._seen = {k: v for k, v in self._seen.items() if v > cutoff}
        return False

    def clear(self):
        """清除所有已跟踪的消息。"""
        self._seen.clear()


# ─── 文本批量聚合 ──────────────────────────────────────────────────────────


class TextBatchAggregator:
    """将快速连续的文本事件聚合为单条消息。

    替换了之前在 telegram、discord、matrix、wecom 和 feishu
    适配器中重复出现的
    ``_enqueue_text_event`` / ``_flush_text_batch`` 模式。

    用法示例::

        self._text_batcher = TextBatchAggregator(
            handler=self._message_handler,
            batch_delay=0.6,
            split_threshold=1900,
        )

        # 在消息分发逻辑中：
        if msg_type == MessageType.TEXT and self._text_batcher.is_enabled():
            self._text_batcher.enqueue(event, session_key)
            return
    """

    def __init__(
        self,
        handler,
        *,
        batch_delay: float = 0.6,
        split_delay: float = 2.0,
        split_threshold: int = 4000,
    ):
        self._handler = handler  # 批次完成后调用的消息处理回调
        self._batch_delay = batch_delay  # 普通消息的聚合延迟（秒）
        self._split_delay = split_delay  # 分片消息（大段文本）的聚合延迟（秒）
        self._split_threshold = split_threshold  # 判定为分片消息的字符长度阈值
        self._pending: Dict[str, "MessageEvent"] = {}  # 按会话 key 存储的待处理事件
        self._pending_tasks: Dict[str, asyncio.Task] = {}  # 按会话 key 存储的刷新定时任务

    def is_enabled(self) -> bool:
        """如果批量聚合功能已启用（延迟 > 0），则返回 True。"""
        return self._batch_delay > 0

    def enqueue(self, event: "MessageEvent", key: str) -> None:
        """将 *event* 添加到 *key* 对应的待处理批次中。"""
        chunk_len = len(event.text or "")
        existing = self._pending.get(key)
        if not existing:
            # 首条消息，直接存入待处理字典
            event._last_chunk_len = chunk_len  # type: ignore[attr-defined]
            self._pending[key] = event
        else:
            # 后续消息，用换行符拼接到已有文本后
            existing.text = f"{existing.text}\n{event.text}"
            existing._last_chunk_len = chunk_len  # type: ignore[attr-defined]

        # 取消之前的刷新定时器，启动新的定时器
        prior = self._pending_tasks.get(key)
        if prior and not prior.done():
            prior.cancel()
        self._pending_tasks[key] = asyncio.create_task(self._flush(key))

    async def _flush(self, key: str) -> None:
        """等待延迟后分发 *key* 对应的聚合事件。"""
        current_task = self._pending_tasks.get(key)
        pending = self._pending.get(key)
        last_len = getattr(pending, "_last_chunk_len", 0) if pending else 0

        # 当最后一个片段看起来像分片消息时，使用更长的延迟等待后续片段
        delay = self._split_delay if last_len >= self._split_threshold else self._batch_delay
        await asyncio.sleep(delay)

        event = self._pending.pop(key, None)
        if event:
            try:
                await self._handler(event)
            except Exception:
                logger.exception("[TextBatchAggregator] 分发 %s 的聚合事件时出错", key)

        # 清理已完成的任务引用
        if self._pending_tasks.get(key) is current_task:
            self._pending_tasks.pop(key, None)

    def cancel_all(self) -> None:
        """取消所有待处理的刷新任务。"""
        for task in self._pending_tasks.values():
            if not task.done():
                task.cancel()
        self._pending_tasks.clear()
        self._pending.clear()


# ─── Markdown 格式剥离 ──────────────────────────────────────────────────────

# 预编译正则表达式以提升性能
_RE_BOLD = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
_RE_ITALIC_STAR = re.compile(r"\*(.+?)\*", re.DOTALL)
_RE_BOLD_UNDER = re.compile(r"__(.+?)__", re.DOTALL)
_RE_ITALIC_UNDER = re.compile(r"_(.+?)_", re.DOTALL)
_RE_CODE_BLOCK = re.compile(r"```[a-zA-Z0-9_+-]*\n?")
_RE_INLINE_CODE = re.compile(r"`(.+?)`")
_RE_HEADING = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_RE_LINK = re.compile(r"\[([^\]]+)\]\([^\)]+\)")
_RE_MULTI_NEWLINE = re.compile(r"\n{3,}")


def strip_markdown(text: str) -> str:
    """为纯文本平台（短信、iMessage 等）剥离 Markdown 格式。

    替换了之前在 sms.py、bluebubbles.py 和 feishu.py 中
    重复出现的 ``_strip_markdown()`` 函数。
    """
    # 按优先级依次移除各种 Markdown 标记，保留内部文本
    text = _RE_BOLD.sub(r"\1", text)           # 移除粗体 **text**
    text = _RE_ITALIC_STAR.sub(r"\1", text)    # 移除斜体 *text*
    text = _RE_BOLD_UNDER.sub(r"\1", text)     # 移除粗体 __text__
    text = _RE_ITALIC_UNDER.sub(r"\1", text)   # 移除斜体 _text_
    text = _RE_CODE_BLOCK.sub("", text)        # 移除代码块标记 ```
    text = _RE_INLINE_CODE.sub(r"\1", text)    # 移除行内代码 `text`
    text = _RE_HEADING.sub("", text)           # 移除标题标记 # ## ###
    text = _RE_LINK.sub(r"\1", text)           # 将链接 [text](url) 替换为 text
    text = _RE_MULTI_NEWLINE.sub("\n\n", text) # 将连续多个空行压缩为两个
    return text.strip()


# ─── 线程参与跟踪 ───────────────────────────────────────────────────────────


class ThreadParticipationTracker:
    """持久化跟踪机器人参与过的线程。

    替换了之前在 discord.py 和 matrix.py 中重复出现的
    ``_load/_save_participated_threads`` +
    ``_mark_thread_participated`` 模式。

    用法示例::

        self._threads = ThreadParticipationTracker("discord")

        # 检查是否已参与：
        if thread_id in self._threads:
            ...

        # 标记参与：
        self._threads.mark(thread_id)
    """

    _MAX_TRACKED = 500

    def __init__(self, platform_name: str, max_tracked: int = 500):
        self._platform = platform_name
        self._max_tracked = max_tracked  # 最多跟踪的线程数量
        self._threads: set = self._load()  # 从磁盘加载已参与的线程集合

    def _state_path(self) -> Path:
        """返回持久化状态文件的路径。"""
        from hermes_constants import get_hermes_home
        return get_hermes_home() / f"{self._platform}_threads.json"

    def _load(self) -> set:
        """从 JSON 文件加载已参与的线程 ID 集合。"""
        path = self._state_path()
        if path.exists():
            try:
                return set(json.loads(path.read_text(encoding="utf-8")))
            except Exception:
                pass
        return set()

    def _save(self) -> None:
        """将当前线程集合持久化到 JSON 文件，超出上限时截断。"""
        path = self._state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        thread_list = list(self._threads)
        # 如果超出最大跟踪数量，只保留最近的记录
        if len(thread_list) > self._max_tracked:
            thread_list = thread_list[-self._max_tracked:]
            self._threads = set(thread_list)
        path.write_text(json.dumps(thread_list), encoding="utf-8")

    def mark(self, thread_id: str) -> None:
        """标记 *thread_id* 为已参与并持久化保存。"""
        if thread_id not in self._threads:
            self._threads.add(thread_id)
            self._save()

    def __contains__(self, thread_id: str) -> bool:
        return thread_id in self._threads

    def clear(self) -> None:
        """清除所有已跟踪的线程。"""
        self._threads.clear()


# ─── 电话号码脱敏 ──────────────────────────────────────────────────────────


def redact_phone(phone: str) -> str:
    """对电话号码进行脱敏处理，保留国家代码和最后 4 位。

    替换了之前在 signal.py、sms.py 和 bluebubbles.py 中
    重复出现的 ``_redact_phone()`` 函数。
    """
    if not phone:
        return "<none>"
    # 短号码（8 位及以下）：保留前 2 位和后 2 位
    if len(phone) <= 8:
        return phone[:2] + "****" + phone[-2:] if len(phone) > 4 else "****"
    # 标准长号码：保留前 4 位（通常包含国家代码）和后 4 位
    return phone[:4] + "****" + phone[-4:]
