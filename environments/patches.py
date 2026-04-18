"""
用于使 hermes-agent 工具在异步框架（Atropos）中正常工作的猴子补丁。

问题：
    一些工具内部使用 asyncio.run()（例如通过 SWE-ReX 的 Modal 后端、
    web_extract）。当从 Atropos 的事件循环内部调用时会崩溃，因为
    asyncio.run() 不能嵌套。

解决方案：
    Modal 环境（tools/environments/modal.py）现在内部使用专用的
    _AsyncWorker 线程，使其在 CLI 和 Atropos 中都能安全使用。
    不需要猴子补丁。

    此模块保留用于向后兼容。apply_patches() 是一个空操作。

用法：
    在导入时调用一次 apply_patches()（由 hermes_base_env.py 自动完成）。
    此操作是幂等的，可以安全地多次调用。
"""

import logging

logger = logging.getLogger(__name__)

_patches_applied = False


def apply_patches():
    """应用所有 Atropos 兼容性所需的猴子补丁。"""
    global _patches_applied
    if _patches_applied:
        return

    logger.debug("apply_patches() 已调用；无需补丁（异步安全性已内置）")
    _patches_applied = True
