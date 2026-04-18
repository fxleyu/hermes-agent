"""网关流式消费者 — 将同步代理回调桥接到异步平台投递。

代理从其工作线程同步触发 stream_delta_callback(text)。
GatewayStreamConsumer：
  1. 通过 on_delta() 接收增量文本（线程安全，同步）
  2. 通过 queue.Queue 将增量排队到异步任务
  3. 异步 run() 任务进行缓冲、限速，并渐进式编辑目标平台上的单条消息

设计：使用编辑传输方式（发送初始消息，然后 editMessageText）。
这在 Telegram、Discord 和 Slack 上都通用支持。

致谢：jobless0x (#774, #1312)、OutThisLife (#798)、clicksingh (#697)。
"""

from __future__ import annotations

import asyncio
import logging
import queue
import re
import time
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger("gateway.stream_consumer")

# 表示流已完成的哨兵值
_DONE = object()

# 表示工具边界的哨兵值 — 结束当前消息并开始新消息，
# 使后续文本出现在工具进度消息下方。
_NEW_SEGMENT = object()

# 队列标记：在 API/工具迭代之间发出的已完成助手评论消息
# （例如："我先检查一下仓库。"）。
_COMMENTARY = object()


@dataclass
class StreamConsumerConfig:
    """单个流式消费者实例的运行时配置。"""
    edit_interval: float = 1.0
    buffer_threshold: int = 40
    cursor: str = " ▉"
    buffer_only: bool = False


class GatewayStreamConsumer:
    """异步消费者，通过渐进式编辑平台消息来展示流式 token。

    用法::

        consumer = GatewayStreamConsumer(adapter, chat_id, config, metadata=metadata)
        # 将 consumer.on_delta 作为 stream_delta_callback 传给 AIAgent
        agent = AIAgent(..., stream_delta_callback=consumer.on_delta)
        # 将消费者作为 asyncio 任务启动
        task = asyncio.create_task(consumer.run())
        # ... 在线程池中运行代理 ...
        consumer.finish()  # 发信号表示完成
        await task         # 等待最终编辑
    """

    # 连续洪水控制失败达到此次数后，在本次流的剩余部分
    # 永久禁用渐进式编辑。
    _MAX_FLOOD_STRIKES = 3

    # 模型在内容中内联发出的推理/思考标签。
    # 必须与 cli.py 中的 _OPEN_TAGS/_CLOSE_TAGS 以及
    # run_agent.py 中 _strip_think_blocks() 的标签变体保持同步。
    _OPEN_THINK_TAGS = (
        "<REASONING_SCRATCHPAD>", "<think>", "<reasoning>",
        "<THINKING>", "<thinking>", "<thought>",
    )
    _CLOSE_THINK_TAGS = (
        "</REASONING_SCRATCHPAD>", "</think>", "</reasoning>",
        "</THINKING>", "</thinking>", "</thought>",
    )

    def __init__(
        self,
        adapter: Any,
        chat_id: str,
        config: Optional[StreamConsumerConfig] = None,
        metadata: Optional[dict] = None,
    ):
        self.adapter = adapter
        self.chat_id = chat_id
        self.cfg = config or StreamConsumerConfig()
        self.metadata = metadata
        self._queue: queue.Queue = queue.Queue()
        self._accumulated = ""
        self._message_id: Optional[str] = None
        self._already_sent = False
        self._edit_supported = True  # 当渐进式编辑不可用时禁用
        self._last_edit_time = 0.0
        self._last_sent_text = ""   # 跟踪上次发送的文本，跳过重复编辑
        self._fallback_final_send = False
        self._fallback_prefix = ""
        self._flood_strikes = 0         # 连续洪水控制编辑失败次数
        self._current_edit_interval = self.cfg.edit_interval  # 自适应退避间隔
        self._final_response_sent = False

        # 思考块过滤器状态（镜像 CLI 的 _stream_delta 标签抑制）
        self._in_think_block = False
        self._think_buffer = ""

    @property
    def already_sent(self) -> bool:
        """如果在运行期间至少发送或编辑了一条消息，则为 True。"""
        return self._already_sent

    @property
    def final_response_sent(self) -> bool:
        """当流式消费者投递了最终助手回复时为 True。"""
        return self._final_response_sent

    def on_segment_break(self) -> None:
        """结束当前流段并开始一条新消息。"""
        self._queue.put(_NEW_SEGMENT)

    def on_commentary(self, text: str) -> None:
        """将已完成的中间助手评论消息加入队列。"""
        if text:
            self._queue.put((_COMMENTARY, text))

    def _reset_segment_state(self, *, preserve_no_edit: bool = False) -> None:
        if preserve_no_edit and self._message_id == "__no_edit__":
            return
        self._message_id = None
        self._accumulated = ""
        self._last_sent_text = ""
        self._fallback_final_send = False
        self._fallback_prefix = ""

    def on_delta(self, text: str) -> None:
        """线程安全的回调 — 从代理的工作线程调用。

        当 *text* 为 ``None`` 时，表示工具边界：当前消息被终结，
        后续文本将作为新消息发送，使其出现在网关在中间发送的
        工具进度消息下方。
        """
        if text:
            self._queue.put(text)
        elif text is None:
            self.on_segment_break()

    def finish(self) -> None:
        """发信号表示流已完成。"""
        self._queue.put(_DONE)

    # ── 思考块过滤 ────────────────────────────────────────
    # MiniMax 等模型在内容中内联发出 <think>...</think> 块。
    # CLI 的 _stream_delta 通过状态机抑制这些内容；
    # 我们在这里做同样的事，让网关用户永远看不到原始推理标签。
    # 代理也会从最终响应中去除它们（run_agent.py 的 _strip_think_blocks），
    # 但流式消费者在去除操作之前就发送了中间编辑。

    def _filter_and_accumulate(self, text: str) -> None:
        """将文本增量添加到累积缓冲区，同时抑制思考块。

        使用状态机跟踪是否在推理/思考块内部。
        块内的文本被静默丢弃。缓冲区边界处的部分标签
        被保存在 ``_think_buffer`` 中，直到收到足够的字符来做出判断。
        """
        buf = self._think_buffer + text
        self._think_buffer = ""

        while buf:
            if self._in_think_block:
                # Look for the earliest closing tag
                best_idx = -1
                best_len = 0
                for tag in self._CLOSE_THINK_TAGS:
                    idx = buf.find(tag)
                    if idx != -1 and (best_idx == -1 or idx < best_idx):
                        best_idx = idx
                        best_len = len(tag)

                # 找到关闭标签 — 丢弃块内容，处理剩余部分
                    # 找到关闭标签 — 丢弃块内容，处理剩余部分
                    self._in_think_block = False
                    buf = buf[best_idx + best_len:]
                else:
                    # 尚未找到关闭标签 — 保留可能是部分关闭标签前缀的尾部，丢弃其余部分。
                    max_tag = max(len(t) for t in self._CLOSE_THINK_TAGS)
                    self._think_buffer = buf[-max_tag:] if len(buf) > max_tag else buf
                    return
            else:
                # 在块边界处查找最早的开启标签
                # （文本开头 / 前面是换行符 + 可选空白符）。
                # 防止模型在行文中*提到*标签时产生误报
                # （例如"<think> 标签用于..."）。
                best_idx = -1
                best_len = 0
                for tag in self._OPEN_THINK_TAGS:
                    search_start = 0
                    while True:
                        idx = buf.find(tag, search_start)
                        if idx == -1:
                            break
                        # 块边界检查（镜像 cli.py 的逻辑）
                        if idx == 0:
                            is_boundary = (
                                not self._accumulated
                                or self._accumulated.endswith("\n")
                            )
                        else:
                            preceding = buf[:idx]
                            last_nl = preceding.rfind("\n")
                            if last_nl == -1:
                                is_boundary = (
                                    (not self._accumulated
                                     or self._accumulated.endswith("\n"))
                                    and preceding.strip() == ""
                                )
                            else:
                                is_boundary = preceding[last_nl + 1:].strip() == ""

                        if is_boundary and (best_idx == -1 or idx < best_idx):
                            best_idx = idx
                            best_len = len(tag)
                            break  # first boundary hit for this tag is enough
                        search_start = idx + 1

                if best_len:
                    # 输出标签之前的文本，进入思考块
                    self._accumulated += buf[:best_idx]
                    self._in_think_block = True
                    buf = buf[best_idx + best_len:]
                else:
                    # 没有开启标签 — 检查尾部是否有部分标签
                    held_back = 0
                    for tag in self._OPEN_THINK_TAGS:
                        for i in range(1, len(tag)):
                            if buf.endswith(tag[:i]) and i > held_back:
                                held_back = i
                    if held_back:
                        self._accumulated += buf[:-held_back]
                        self._think_buffer = buf[-held_back:]
                    else:
                        self._accumulated += buf
                    return

    def _flush_think_buffer(self) -> None:
        """将保留的部分标签缓冲区刷入累积文本。

        在流结束时（got_done）调用，确保因等待可能的开启标签
        而被保留的部分文本不会丢失。
        """
        if self._think_buffer and not self._in_think_block:
            self._accumulated += self._think_buffer
            self._think_buffer = ""

    async def run(self) -> None:
        """异步任务，消耗队列并编辑平台消息。"""
        # 平台消息长度限制 — 预留光标和格式化的空间
        _raw_limit = getattr(self.adapter, "MAX_MESSAGE_LENGTH", 4096)
        _safe_limit = max(500, _raw_limit - len(self.cfg.cursor) - 100)

        try:
            while True:
                # 从队列中取出所有可用项
                got_done = False
                got_segment_break = False
                commentary_text = None
                while True:
                    try:
                        item = self._queue.get_nowait()
                        if item is _DONE:
                            got_done = True
                            break
                        if item is _NEW_SEGMENT:
                            got_segment_break = True
                            break
                        if isinstance(item, tuple) and len(item) == 2 and item[0] is _COMMENTARY:
                            commentary_text = item[1]
                            break
                        self._filter_and_accumulate(item)
                    except queue.Empty:
                        break

                # 在流结束时刷新保留的部分标签缓冲区，
                # 确保等待潜在开启标签的尾部文本不会丢失。
                if got_done:
                    self._flush_think_buffer()

                # 决定是否触发一次编辑
                now = time.monotonic()
                elapsed = now - self._last_edit_time
                should_edit = (
                    got_done
                    or got_segment_break
                    or commentary_text is not None
                )
                if not self.cfg.buffer_only:
                    should_edit = should_edit or (
                        (elapsed >= self._current_edit_interval
                            and self._accumulated)
                        or len(self._accumulated) >= self.cfg.buffer_threshold
                    )

                current_update_visible = False
                if should_edit and self._accumulated:
                    # 溢出分割：如果累积文本超过平台限制，分割成合适大小的块。
                    if (
                        len(self._accumulated) > _safe_limit
                        and self._message_id is None
                    ):
                        # 没有现有消息可编辑（首条消息或段落断开后）。
                        # 使用 truncate_message — 非流式路径使用的
                        # 同一辅助函数 — 按正确的单词/代码围栏边界
                        # 和块指示器如 "(1/2)" 进行分割。
                        chunks = self.adapter.truncate_message(
                            self._accumulated, _safe_limit
                        )
                        for chunk in chunks:
                            await self._send_new_chunk(chunk, self._message_id)
                        self._accumulated = ""
                        self._last_sent_text = ""
                        self._last_edit_time = time.monotonic()
                        if got_done:
                            self._final_response_sent = self._already_sent
                            return
                        if got_segment_break:
                            self._message_id = None
                            self._fallback_final_send = False
                            self._fallback_prefix = ""
                        continue

                    # 已有消息：用第一个块编辑它，然后为溢出的剩余部分开始新消息。
                    while (
                        len(self._accumulated) > _safe_limit
                        and self._message_id is not None
                        and self._edit_supported
                    ):
                        split_at = self._accumulated.rfind("\n", 0, _safe_limit)
                        if split_at < _safe_limit // 2:
                            split_at = _safe_limit
                        chunk = self._accumulated[:split_at]
                        ok = await self._send_or_edit(chunk)
                        if self._fallback_final_send or not ok:
                            # Edit failed (or backed off due to flood control)
                            # while attempting to split an oversized message.
                            # Keep the full accumulated text intact so the
                            # fallback final-send path can deliver the remaining
                            # continuation without dropping content.
                            break
                        self._accumulated = self._accumulated[split_at:].lstrip("\n")
                        self._message_id = None
                        self._last_sent_text = ""

                    display_text = self._accumulated
                    if not got_done and not got_segment_break and commentary_text is None:
                        display_text += self.cfg.cursor

                    current_update_visible = await self._send_or_edit(display_text)
                    self._last_edit_time = time.monotonic()

                if got_done:
                    # 最终编辑（不带光标）。如果渐进式编辑在流中途失败，
                    # 在此发送一条续传/兜底消息，而不是让基础网关路径
                    # 再次发送完整响应。
                    if self._accumulated:
                        if self._fallback_final_send:
                            await self._send_fallback_final(self._accumulated)
                        elif current_update_visible:
                            self._final_response_sent = True
                        elif self._message_id:
                            self._final_response_sent = await self._send_or_edit(self._accumulated)
                        elif not self._already_sent:
                            self._final_response_sent = await self._send_or_edit(self._accumulated)
                    return

                if commentary_text is not None:
                    self._reset_segment_state()
                    await self._send_commentary(commentary_text)
                    self._last_edit_time = time.monotonic()
                    self._reset_segment_state()

                # 工具边界：重置消息状态，使下一个文本块
                # 在任何工具进度消息下方创建新消息。
                #
                # 例外：当 _message_id 为 "__no_edit__" 时，平台从未返回
                # 真实的消息 ID（如 Signal、使用 github_comment 投递的 webhook）。
                # 重置为 None 会在每个工具边界重新进入"首次发送"路径，
                # 并在每次工具调用时发送一条平台消息 — 这正是导致单个 PR
                # 下产生 155 条评论的原因。改为保留哨兵值，
                # 使完整续传通过 _send_fallback_final 一次性投递。
                # （当渐进式编辑因洪水控制在流中途失败时，id 是真实字符串
                # 如 "msg_1" 而非 "__no_edit__"，因此该情况仍会重置并按
                # 预期创建新段落。）
                if got_segment_break:
                    self._reset_segment_state(preserve_no_edit=True)

                await asyncio.sleep(0.05)  # 小幅让出，避免忙循环

        except asyncio.CancelledError:
            # 取消时的尽力而为最终编辑
            _best_effort_ok = False
            if self._accumulated and self._message_id:
                try:
                    _best_effort_ok = bool(await self._send_or_edit(self._accumulated))
                except Exception:
                    pass
            # 仅在上述尽力而为发送确实成功时，或最终响应在我们被取消
            # 之前已确认时，才确认最终投递。之前这里将任何部分发送
            # （already_sent=True）提升为 final_response_sent —
            # 即使只有中间文本（如"让我搜索..."）被投递而非真正的答案，
            # 也会抑制网关的兜底发送。
            if _best_effort_ok and not self._final_response_sent:
                self._final_response_sent = True
        except Exception as e:
            logger.error("Stream consumer error: %s", e)

    # 用于去除 MEDIA:<路径> 标签（包括可选的引号包围）的正则模式。
    # 匹配非流式路径中 gateway/platforms/base.py 后处理使用的简单清理正则。
    _MEDIA_RE = re.compile(r'''[`"']?MEDIA:\s*\S+[`"']?''')

    @staticmethod
    def _clean_for_display(text: str) -> str:
        """在显示前去除文本中的 MEDIA: 指令和内部标记。

        流式路径投递的原始文本块可能包含 ``MEDIA:<路径>`` 标签和
        ``[[audio_as_voice]]`` 指令，这些是给平台适配器后处理用的。
        实际的媒体文件在流结束后通过 ``_deliver_media_from_response()``
        单独投递 — 我们只需要在用户面前隐藏原始指令。
        """
        if "MEDIA:" not in text and "[[audio_as_voice]]" not in text:
            return text
        cleaned = text.replace("[[audio_as_voice]]", "")
        cleaned = GatewayStreamConsumer._MEDIA_RE.sub("", cleaned)
        # 移除标签后折叠多余的空行
        cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
        # 去除尾部空白/换行符但保留前导内容
        return cleaned.rstrip()

    async def _send_new_chunk(self, text: str, reply_to_id: Optional[str]) -> Optional[str]:
        """发送新的消息块，可选线程化到前一条消息。

        返回 message_id 以便调用方可以将后续块线程化。
        """
        text = self._clean_for_display(text)
        if not text.strip():
            return reply_to_id
        try:
            meta = dict(self.metadata) if self.metadata else {}
            result = await self.adapter.send(
                chat_id=self.chat_id,
                content=text,
                reply_to=reply_to_id,
                metadata=meta,
            )
            if result.success and result.message_id:
                self._message_id = str(result.message_id)
                self._already_sent = True
                self._last_sent_text = text
                return str(result.message_id)
            else:
                self._edit_supported = False
                return reply_to_id
        except Exception as e:
            logger.error("Stream send chunk error: %s", e)
            return reply_to_id

    def _visible_prefix(self) -> str:
        """返回流式消息中已显示的可见文本。"""
        prefix = self._last_sent_text or ""
        if self.cfg.cursor and prefix.endswith(self.cfg.cursor):
            prefix = prefix[:-len(self.cfg.cursor)]
        return self._clean_for_display(prefix)

    def _continuation_text(self, final_text: str) -> str:
        """返回 final_text 中用户尚未看到的部分。"""
        prefix = self._fallback_prefix or self._visible_prefix()
        if prefix and final_text.startswith(prefix):
            return final_text[len(prefix):].lstrip()
        return final_text

    @staticmethod
    def _split_text_chunks(text: str, limit: int) -> list[str]:
        """将文本分割成合理大小的块，用于兜底发送。"""
        if len(text) <= limit:
            return [text]
        chunks: list[str] = []
        remaining = text
        while len(remaining) > limit:
            split_at = remaining.rfind("\n", 0, limit)
            if split_at < limit // 2:
                split_at = limit
            chunks.append(remaining[:split_at])
            remaining = remaining[split_at:].lstrip("\n")
        if remaining:
            chunks.append(remaining)
        return chunks

    async def _send_fallback_final(self, text: str) -> None:
        """在流式编辑停止工作后发送最终续传内容。

        对每个块在洪水控制失败时短暂延迟后重试一次。
        """
        final_text = self._clean_for_display(text)
        continuation = self._continuation_text(final_text)
        self._fallback_final_send = False
        if not continuation.strip():
            # Nothing new to send — the visible partial already matches final text.
            # BUT: if final_text itself has meaningful content (e.g. a timeout
            # message after a long tool call), the prefix-based continuation
            # calculation may wrongly conclude "already shown" because the
            # streamed prefix was from a *previous* segment (before the tool
            # boundary).  In that case, send the full final_text as-is (#10807).
            if final_text.strip() and final_text != self._visible_prefix():
                continuation = final_text
            else:
                self._already_sent = True
                self._final_response_sent = True
                return

        raw_limit = getattr(self.adapter, "MAX_MESSAGE_LENGTH", 4096)
        safe_limit = max(500, raw_limit - 100)
        chunks = self._split_text_chunks(continuation, safe_limit)

        last_message_id: Optional[str] = None
        last_successful_chunk = ""
        sent_any_chunk = False
        for chunk in chunks:
            # Try sending with one retry on flood-control errors.
            result = None
            for attempt in range(2):
                result = await self.adapter.send(
                    chat_id=self.chat_id,
                    content=chunk,
                    metadata=self.metadata,
                )
                if result.success:
                    break
                if attempt == 0 and self._is_flood_error(result):
                    logger.debug(
                        "Flood control on fallback send, retrying in 3s"
                    )
                    await asyncio.sleep(3.0)
                else:
                    break  # non-flood error or second attempt failed

            if not result or not result.success:
                if sent_any_chunk:
                    # Some continuation text already reached the user. Suppress
                    # the base gateway final-send path so we don't resend the
                    # full response and create another duplicate.
                    self._already_sent = True
                    self._final_response_sent = True
                    self._message_id = last_message_id
                    self._last_sent_text = last_successful_chunk
                    self._fallback_prefix = ""
                    return
                # No fallback chunk reached the user — allow the normal gateway
                # final-send path to try one more time.
                self._already_sent = False
                self._message_id = None
                self._last_sent_text = ""
                self._fallback_prefix = ""
                return
            sent_any_chunk = True
            last_successful_chunk = chunk
            last_message_id = result.message_id or last_message_id

        self._message_id = last_message_id
        self._already_sent = True
        self._final_response_sent = True
        self._last_sent_text = chunks[-1]
        self._fallback_prefix = ""

    def _is_flood_error(self, result) -> bool:
        """检查 SendResult 失败是否由洪水控制/限速引起。"""
        err = getattr(result, "error", "") or ""
        err_lower = err.lower()
        return "flood" in err_lower or "retry after" in err_lower or "rate" in err_lower

    async def _try_strip_cursor(self) -> None:
        """尽力编辑以移除最后可见消息中的光标。

        在进入兜底模式时调用，确保用户不会看到卡住的光标（▉）。
        """
        if not self._message_id or self._message_id == "__no_edit__":
            return
        prefix = self._visible_prefix()
        if not prefix or not prefix.strip():
            return
        try:
            await self.adapter.edit_message(
                chat_id=self.chat_id,
                message_id=self._message_id,
                content=prefix,
            )
            self._last_sent_text = prefix
        except Exception:
            pass  # 尽力而为 — 不要让此操作阻塞兜底路径

    async def _send_commentary(self, text: str) -> bool:
        """发送已完成的中间助手评论消息。"""
        text = self._clean_for_display(text)
        if not text.strip():
            return False
        try:
            result = await self.adapter.send(
                chat_id=self.chat_id,
                content=text,
                metadata=self.metadata,
            )
            # Note: do NOT set _already_sent = True here.
            # Commentary messages are interim status updates (e.g. "Using browser
            # tool..."), not the final response. Setting already_sent would cause
            # the final response to be incorrectly suppressed when there are
            # multiple tool calls. See: https://github.com/NousResearch/hermes-agent/issues/10454
            return result.success
        except Exception as e:
            logger.error("Commentary send error: %s", e)
            return False

    async def _send_or_edit(self, text: str) -> bool:
        """发送或编辑流式消息。

        如果文本成功投递（发送或编辑），返回 True，
        否则返回 False。溢出分割循环等调用方据此决定
        是否推进到已投递的块之后。
        """
        # 去除 MEDIA: 指令，避免它们作为可见文本出现。
        # 媒体文件在流结束后作为原生附件投递
        # （通过 gateway/run.py 中的 _deliver_media_from_response）。
        text = self._clean_for_display(text)
        # A bare streaming cursor is not meaningful user-visible content and
        # can render as a stray tofu/white-box message on some clients.
        visible_without_cursor = text
        if self.cfg.cursor:
            visible_without_cursor = visible_without_cursor.replace(self.cfg.cursor, "")
        _visible_stripped = visible_without_cursor.strip()
        if not _visible_stripped:
            return True  # cursor-only / whitespace-only update
        if not text.strip():
            return True  # nothing to send is "success"
        # Guard: do not create a brand-new standalone message when the only
        # visible content is a handful of characters alongside the streaming
        # cursor.  During rapid tool-calling the model often emits 1-2 tokens
        # before switching to tool calls; the resulting "X ▉" message risks
        # leaving the cursor permanently visible if the follow-up edit (to
        # strip the cursor on segment break) is rate-limited by the platform.
        # This was reported on Telegram, Matrix, and other clients where the
        # ▉ block character renders as a visible white box ("tofu").
        # Existing messages (edits) are unaffected — only first sends gated.
        _MIN_NEW_MSG_CHARS = 4
        if (self._message_id is None
                and self.cfg.cursor
                and self.cfg.cursor in text
                and len(_visible_stripped) < _MIN_NEW_MSG_CHARS):
            return True  # too short for a standalone message — accumulate more
        try:
            if self._message_id is not None:
                if self._edit_supported:
                    # Skip if text is identical to what we last sent
                    if text == self._last_sent_text:
                        return True
                    # Edit existing message
                    result = await self.adapter.edit_message(
                        chat_id=self.chat_id,
                        message_id=self._message_id,
                        content=text,
                    )
                    if result.success:
                        self._already_sent = True
                        self._last_sent_text = text
                        # Successful edit — reset flood strike counter
                        self._flood_strikes = 0
                        return True
                    else:
                        # Edit failed.  If this looks like flood control / rate
                        # limiting, use adaptive backoff: double the edit interval
                        # and retry on the next cycle.  Only permanently disable
                        # edits after _MAX_FLOOD_STRIKES consecutive failures.
                        if self._is_flood_error(result):
                            self._flood_strikes += 1
                            self._current_edit_interval = min(
                                self._current_edit_interval * 2, 10.0,
                            )
                            logger.debug(
                                "Flood control on edit (strike %d/%d), "
                                "backoff interval → %.1fs",
                                self._flood_strikes,
                                self._MAX_FLOOD_STRIKES,
                                self._current_edit_interval,
                            )
                            if self._flood_strikes < self._MAX_FLOOD_STRIKES:
                                # Don't disable edits yet — just slow down.
                                # Update _last_edit_time so the next edit
                                # respects the new interval.
                                self._last_edit_time = time.monotonic()
                                return False

                        # Non-flood error OR flood strikes exhausted: enter
                        # fallback mode — send only the missing tail once the
                        # final response is available.
                        logger.debug(
                            "Edit failed (strikes=%d), entering fallback mode",
                            self._flood_strikes,
                        )
                        self._fallback_prefix = self._visible_prefix()
                        self._fallback_final_send = True
                        self._edit_supported = False
                        self._already_sent = True
                        # Best-effort: strip the cursor from the last visible
                        # message so the user doesn't see a stuck ▉.
                        await self._try_strip_cursor()
                        return False
                else:
                    # Editing not supported — skip intermediate updates.
                    # The final response will be sent by the fallback path.
                    return False
            else:
                # First message — send new
                result = await self.adapter.send(
                    chat_id=self.chat_id,
                    content=text,
                    metadata=self.metadata,
                )
                if result.success:
                    if result.message_id:
                        self._message_id = result.message_id
                    else:
                        self._edit_supported = False
                    self._already_sent = True
                    self._last_sent_text = text
                    if not result.message_id:
                        self._fallback_prefix = self._visible_prefix()
                        self._fallback_final_send = True
                        # Sentinel prevents re-entering the first-send path on
                        # every delta/tool boundary when platforms accept a
                        # message but do not return an editable message id.
                        self._message_id = "__no_edit__"
                    return True
                else:
                    # Initial send failed — disable streaming for this session
                    self._edit_supported = False
                    return False
        except Exception as e:
            logger.error("Stream send/edit error: %s", e)
            return False
