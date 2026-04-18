"""云端浏览器提供者的抽象基类。"""

from abc import ABC, abstractmethod
from typing import Dict


class CloudBrowserProvider(ABC):
    """云端浏览器后端接口（Browserbase、Steel 等）。

    各实现位于同级模块中，并在 ``browser_tool._PROVIDER_REGISTRY`` 中注册。
    用户通过 ``hermes setup`` / ``hermes tools`` 选择提供者；
    选择结果持久化为 ``config["browser"]["cloud_provider"]``。
    """

    @abstractmethod
    def provider_name(self) -> str:
        """简短、人类可读的名称，显示在日志和诊断信息中。"""

    @abstractmethod
    def is_configured(self) -> bool:
        """当所有必需的环境变量/凭证都存在时返回 True。

        在工具注册时（``check_browser_requirements``）调用以控制可用性。
        必须足够轻量 - 不进行网络调用。
        """

    @abstractmethod
    def create_session(self, task_id: str) -> Dict[str, object]:
        """创建云端浏览器会话并返回会话元数据。

        必须返回至少包含以下内容的字典::

            {
                "session_name": str,   # agent-browser --session 的唯一名称
                "bb_session_id": str,  # 提供者会话 ID（用于关闭/清理）
                "cdp_url": str,        # CDP websocket URL
                "features": dict,      # 已启用的功能标志
            }

        ``bb_session_id`` 是为了与 browser_tool.py 的其余部分向后兼容而保留的
        旧版键名 - 无论使用哪个提供者，它都保存提供者的会话 ID。
        """

    @abstractmethod
    def close_session(self, session_id: str) -> bool:
        """通过提供者会话 ID 释放/终止云端会话。

        成功返回 True，失败返回 False。不应抛出异常。
        """

    @abstractmethod
    def emergency_cleanup(self, session_id: str) -> None:
        """进程退出时的尽力会话清理。

        从 atexit / signal handler 中调用。必须容忍缺失的凭证、
        网络错误等 - 记录日志后继续。
        """
