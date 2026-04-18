"""重试工具——用于去关联重试的抖动退避。

用抖动延迟替代固定指数退避，以防止多个会话同时命中
同一速率限制提供者时产生的"惊群"重试尖峰。
"""

import random
import threading
import time

# 进程内抖动种子唯一性的单调计数器。
# 受锁保护以避免并发重试路径中的竞态条件
# （例如多个网关会话同时重试）。
_jitter_counter = 0
_jitter_lock = threading.Lock()


def jittered_backoff(
    attempt: int,
    *,
    base_delay: float = 5.0,
    max_delay: float = 120.0,
    jitter_ratio: float = 0.5,
) -> float:
    """计算带抖动的指数退避延迟。

    参数：
        attempt：基于 1 的重试次数。
        base_delay：第 1 次尝试的基础延迟秒数。
        max_delay：最大延迟上限秒数。
        jitter_ratio：用作随机抖动范围的已计算延迟的比例。
            0.5 表示抖动在 [0, 0.5 * delay] 内均匀分布。

    返回：
        延迟秒数：min(base * 2^(attempt-1), max_delay) + 抖动。

    抖动使并发重试去关联，使多个会话命中同一提供者时
    不会在同一时刻全部重试。
    """
    global _jitter_counter
    # 使用锁安全地递增计数器，确保线程安全
    with _jitter_lock:
        _jitter_counter += 1
        tick = _jitter_counter

    # 计算指数退避基础延迟，限制在最大值以内
    exponent = max(0, attempt - 1)
    if exponent >= 63 or base_delay <= 0:
        delay = max_delay
    else:
        delay = min(base_delay * (2 ** exponent), max_delay)

    # 使用时间 + 计数器作为种子，即使时钟精度粗糙也能实现去关联。
    seed = (time.time_ns() ^ (tick * 0x9E3779B9)) & 0xFFFFFFFF
    rng = random.Random(seed)
    jitter = rng.uniform(0, jitter_ratio * delay)

    return delay + jitter
