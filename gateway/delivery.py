"""
定时任务输出和代理响应的投递路由。

根据以下条件将消息路由到适当的目标：
- 显式目标（例如 "telegram:123456789"）
- 平台主频道（例如 "telegram" → 主频道）
- 来源（返回到任务创建的位置）
- 本地（始终保存到文件）
"""

import logging
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass
from typing import Dict, List, Optional, Any

from hermes_cli.config import get_hermes_home

logger = logging.getLogger(__name__)

MAX_PLATFORM_OUTPUT = 4000
TRUNCATED_VISIBLE = 3800

from .config import Platform, GatewayConfig
from .session import SessionSource


@dataclass
class DeliveryTarget:
    """
    单个投递目标。

    表示消息应发送到哪里：
    - "origin" → 返回来源
    - "local" → 保存到本地文件
    - "telegram" → Telegram 主频道
    - "telegram:123456" → 指定的 Telegram 聊天
    """
    platform: Platform
    chat_id: Optional[str] = None  # None 表示使用主频道
    thread_id: Optional[str] = None
    is_origin: bool = False
    is_explicit: bool = False  # 为 True 表示 chat_id 是显式指定的
    
    @classmethod
    def parse(cls, target: str, origin: Optional[SessionSource] = None) -> "DeliveryTarget":
        """
        解析投递目标字符串。

        支持格式：
        - "origin" → 返回来源
        - "local" → 仅本地文件
        - "telegram" → Telegram 主频道
        - "telegram:123456" → 指定的 Telegram 聊天
        """
        target = target.strip().lower()
        
        if target == "origin":
            if origin:
                return cls(
                    platform=origin.platform,
                    chat_id=origin.chat_id,
                    thread_id=origin.thread_id,
                    is_origin=True,
                )
            else:
                # 没有来源时兜底到本地
                return cls(platform=Platform.LOCAL, is_origin=True)
        
        if target == "local":
            return cls(platform=Platform.LOCAL)
        
        # 检查 platform:chat_id 或 platform:chat_id:thread_id 格式
        if ":" in target:
            parts = target.split(":", 2)
            platform_str = parts[0]
            chat_id = parts[1] if len(parts) > 1 else None
            thread_id = parts[2] if len(parts) > 2 else None
            try:
                platform = Platform(platform_str)
                return cls(platform=platform, chat_id=chat_id, thread_id=thread_id, is_explicit=True)
            except ValueError:
                # 未知平台，当作本地处理
                return cls(platform=Platform.LOCAL)
        
        # 仅平台名称（使用主频道）
        try:
            platform = Platform(target)
            return cls(platform=platform)
        except ValueError:
            # Unknown platform, treat as local
            return cls(platform=Platform.LOCAL)
    
    def to_string(self) -> str:
        """转换回字符串格式。"""
        if self.is_origin:
            return "origin"
        if self.platform == Platform.LOCAL:
            return "local"
        if self.chat_id and self.thread_id:
            return f"{self.platform.value}:{self.chat_id}:{self.thread_id}"
        if self.chat_id:
            return f"{self.platform.value}:{self.chat_id}"
        return self.platform.value


class DeliveryRouter:
    """
    将消息路由到适当目标。

    处理投递目标的解析和消息分发到正确的平台适配器的逻辑。
    """

    def __init__(self, config: GatewayConfig, adapters: Dict[Platform, Any] = None):
        """
        初始化投递路由器。

        参数：
            config: 网关配置
            adapters: 平台到其适配器实例的映射字典
        """
        self.config = config
        self.adapters = adapters or {}
        self.output_dir = get_hermes_home() / "cron" / "output"
    
    async def deliver(
        self,
        content: str,
        targets: List[DeliveryTarget],
        job_id: Optional[str] = None,
        job_name: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        向所有指定目标投递内容。

        参数：
            content: 要投递的消息/输出
            targets: 投递目标列表
            job_id: 可选的任务 ID（用于定时任务）
            job_name: 可选的任务名称
            metadata: 附加的元数据

        返回：
            每个目标的投递结果字典
        """
        results = {}
        
        for target in targets:
            try:
                if target.platform == Platform.LOCAL:
                    result = self._deliver_local(content, job_id, job_name, metadata)
                else:
                    result = await self._deliver_to_platform(target, content, metadata)
                
                results[target.to_string()] = {
                    "success": True,
                    "result": result
                }
            except Exception as e:
                results[target.to_string()] = {
                    "success": False,
                    "error": str(e)
                }
        
        return results
    
    def _deliver_local(
        self,
        content: str,
        job_id: Optional[str],
        job_name: Optional[str],
        metadata: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """将内容保存到本地文件。"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        if job_id:
            output_path = self.output_dir / job_id / f"{timestamp}.md"
        else:
            output_path = self.output_dir / "misc" / f"{timestamp}.md"
        
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        # 构建输出文档
        lines = []
        if job_name:
            lines.append(f"# {job_name}")
        else:
            lines.append("# Delivery Output")
        
        lines.append("")
        lines.append(f"**Timestamp:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        if job_id:
            lines.append(f"**Job ID:** {job_id}")
        
        if metadata:
            for key, value in metadata.items():
                lines.append(f"**{key}:** {value}")
        
        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append(content)
        
        output_path.write_text("\n".join(lines))
        
        return {
            "path": str(output_path),
            "timestamp": timestamp
        }
    
    def _save_full_output(self, content: str, job_id: str) -> Path:
        """将完整的定时任务输出保存到磁盘并返回文件路径。"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir = get_hermes_home() / "cron" / "output"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{job_id}_{timestamp}.txt"
        path.write_text(content)
        return path

    async def _deliver_to_platform(
        self,
        target: DeliveryTarget,
        content: str,
        metadata: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """将内容投递到消息平台。"""
        adapter = self.adapters.get(target.platform)
        
        if not adapter:
            raise ValueError(f"No adapter configured for {target.platform.value}")
        
        if not target.chat_id:
            raise ValueError(f"No chat ID for {target.platform.value} delivery")
        
        # 安全措施：截断过大的定时任务输出以保持在平台限制内
        if len(content) > MAX_PLATFORM_OUTPUT:
            job_id = (metadata or {}).get("job_id", "unknown")
            saved_path = self._save_full_output(content, job_id)
            logger.info("Cron output truncated (%d chars) — full output: %s", len(content), saved_path)
            content = (
                content[:TRUNCATED_VISIBLE]
                + f"\n\n... [truncated, full output saved to {saved_path}]"
            )
        
        send_metadata = dict(metadata or {})
        if target.thread_id and "thread_id" not in send_metadata:
            send_metadata["thread_id"] = target.thread_id
        return await adapter.send(target.chat_id, content, metadata=send_metadata or None)




