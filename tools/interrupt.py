"""所有工具的线程级中断信号。

提供线程范围的中断跟踪，使中断一个代理会话不会杀死在其他会话中
运行的工具。这在网关中至关重要，因为多个代理在同一进程中并发运行。

代理在 run_conversation() 开始时存储其执行线程 ID，并将其传递给
set_interrupt()/clear_interrupt()。工具调用 is_interrupted()
来检查当前线程——无需传递参数。

工具中的用法：
    from tools.interrupt import is_interrupted
    if is_interrupted():
        return {"output": "[interrupted]", "returncode": 130}
"""

import threading

# 已被中断的线程标识符集合。
_interrupted_threads: set[int] = set()
_lock = threading.Lock()


def set_interrupt(active: bool, thread_id: int | None = None) -> None:
    """设置或清除特定线程的中断状态。

    参数:
        active: True 表示发出中断信号，False 表示清除中断。
        thread_id: 目标线程标识符。为 None 时针对当前线程
                   （向后兼容 CLI/测试）。
    """
    tid = thread_id if thread_id is not None else threading.current_thread().ident
    with _lock:
        if active:
            _interrupted_threads.add(tid)
        else:
            _interrupted_threads.discard(tid)


def is_interrupted() -> bool:
    """检查当前线程是否有中断请求。

    可从任何线程安全调用——每个线程只能看到自己的中断状态。
    """
    tid = threading.current_thread().ident
    with _lock:
        return tid in _interrupted_threads


# ---------------------------------------------------------------------------
# 向后兼容的 _interrupt_event 代理
# ---------------------------------------------------------------------------
# 一些遗留调用点（code_execution_tool、process_registry、测试）
# 直接导入 _interrupt_event 并调用 .is_set() / .set() / .clear()。
# 此垫片将这些调用映射到上面的线程级函数，使现有代码在底层机制
# 为线程范围的情况下继续工作。

class _ThreadAwareEventProxy:
    """将 threading.Event 方法映射到线程级状态的直接替换代理。"""

    def is_set(self) -> bool:
        return is_interrupted()

    def set(self) -> None:  # noqa: A003
        set_interrupt(True)

    def clear(self) -> None:
        set_interrupt(False)

    def wait(self, timeout: float | None = None) -> bool:
        """实际上不支持——立即返回当前状态。"""
        return self.is_set()


_interrupt_event = _ThreadAwareEventProxy()
