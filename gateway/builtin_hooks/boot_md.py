"""内置 boot-md 钩子 — 在网关启动时运行 ~/.hermes/BOOT.md。

此钩子始终注册。如果 BOOT.md 不存在，则静默跳过。
要激活此功能，请创建 ``~/.hermes/BOOT.md``，写入希望代理
在每次网关重启时执行的指令。

BOOT.md 示例::

    # 启动检查清单

    1. 检查昨晚是否有定时任务失败
    2. 向 Discord #general 发送状态更新
    3. 如果 /opt/app/deploy.log 中有错误，进行汇总

代理在后台线程中运行，不会阻塞网关启动。
如果没有需要关注的事项，代理会回复 [SILENT] 以抑制消息投递。
"""

import logging
import threading

logger = logging.getLogger("hooks.boot-md")

from hermes_constants import get_hermes_home
HERMES_HOME = get_hermes_home()
BOOT_FILE = HERMES_HOME / "BOOT.md"


def _build_boot_prompt(content: str) -> str:
    """将 BOOT.md 内容包装为系统级指令。"""
    return (
        "You are running a startup boot checklist. Follow the BOOT.md "
        "instructions below exactly.\n\n"
        "---\n"
        f"{content}\n"
        "---\n\n"
        "Execute each instruction. If you need to send a message to a "
        "platform, use the send_message tool.\n"
        "If nothing needs attention and there is nothing to report, "
        "reply with ONLY: [SILENT]"
    )


def _run_boot_agent(content: str) -> None:
    """启动一次性代理会话来执行启动指令。"""
    try:
        from run_agent import AIAgent

        prompt = _build_boot_prompt(content)
        agent = AIAgent(
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
            max_iterations=20,
        )
        result = agent.run_conversation(prompt)
        response = result.get("final_response", "")
        if response and "[SILENT]" not in response:
            logger.info("boot-md completed: %s", response[:200])
        else:
            logger.info("boot-md completed (nothing to report)")
    except Exception as e:
        logger.error("boot-md agent failed: %s", e)


async def handle(event_type: str, context: dict) -> None:
    """网关启动处理器 — 如果 BOOT.md 存在则运行它。"""
    if not BOOT_FILE.exists():
        return

    content = BOOT_FILE.read_text(encoding="utf-8").strip()
    if not content:
        return

    logger.info("Running BOOT.md (%d chars)", len(content))

    # 在后台线程中运行，避免阻塞网关启动。
    thread = threading.Thread(
        target=_run_boot_agent,
        args=(content,),
        name="boot-md",
        daemon=True,
    )
    thread.start()
