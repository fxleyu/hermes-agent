"""hermes-agent ACP 适配器的 CLI 入口点。

从 ``~/.hermes/.env`` 加载环境变量，配置日志写入 stderr
（以便 stdout 保留给 ACP JSON-RPC 传输），并启动 ACP 代理服务器。

用法::

    python -m acp_adapter.entry
    # 或
    hermes acp
    # 或
    hermes-acp
"""

import asyncio
import logging
import sys
from pathlib import Path
from hermes_constants import get_hermes_home


def _setup_logging() -> None:
    """将所有日志路由到 stderr，以保持 stdout 干净用于 ACP stdio。"""
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)

    # 降低嘈杂库的日志级别
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)


def _load_env() -> None:
    """从 HERMES_HOME（默认 ``~/.hermes``）加载 .env 文件。"""
    from hermes_cli.env_loader import load_hermes_dotenv

    hermes_home = get_hermes_home()
    loaded = load_hermes_dotenv(hermes_home=hermes_home)
    if loaded:
        for env_file in loaded:
            logging.getLogger(__name__).info("Loaded env from %s", env_file)
    else:
        logging.getLogger(__name__).info(
            "No .env found at %s, using system env", hermes_home / ".env"
        )


def main() -> None:
    """入口点：加载环境变量，配置日志，运行 ACP 代理。"""
    _setup_logging()
    _load_env()

    logger = logging.getLogger(__name__)
    logger.info("Starting hermes-agent ACP adapter")

    # 确保项目根目录在 sys.path 中，以便 ``from run_agent import AIAgent`` 可以正常工作
    project_root = str(Path(__file__).resolve().parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    import acp
    from .server import HermesACPAgent

    agent = HermesACPAgent()
    try:
        asyncio.run(acp.run_agent(agent, use_unstable_protocol=True))
    except KeyboardInterrupt:
        logger.info("Shutting down (KeyboardInterrupt)")
    except Exception:
        logger.exception("ACP agent crashed")
        sys.exit(1)


if __name__ == "__main__":
    main()
