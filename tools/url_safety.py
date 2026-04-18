"""URL 安全检查——阻止对私有/内部网络地址的请求。

防止 SSRF（服务端请求伪造），即恶意提示或技能可能诱骗代理
获取内部资源，如云元数据端点（169.254.169.254）、localhost 服务
或私有网络主机。

局限性（已记录，在预检层面无法修复）：
  - DNS 重绑定（TOCTOU）：攻击者控制的 DNS 服务器设置 TTL=0，
    检查时返回公共 IP，实际连接时返回私有 IP。修复此问题需要
    连接级验证（例如 Python 的 Champion 库或出口代理如
    Stripe 的 Smokescreen）。
  - 基于重定向的绕过通过 httpx 事件钩子来缓解，这些钩子在
    vision_tools、网关平台适配器和媒体缓存辅助工具中重新验证
    每个重定向目标。Web 工具使用第三方 SDK（Firecrawl/Tavily），
    重定向处理在它们的服务器上进行。
"""

import ipaddress
import logging
import socket
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# 无论 IP 解析结果如何，始终应阻止的主机名
_BLOCKED_HOSTNAMES = frozenset({
    "metadata.google.internal",
    "metadata.goog",
})

# 100.64.0.0/10（CGNAT / 共享地址空间，RFC 6598）不在
# ipaddress.is_private 的覆盖范围内——对 is_private 和 is_global
# 都返回 False。必须显式阻止。被运营商级 NAT、Tailscale/WireGuard
# VPN 和某些云内部网络使用。
_CGNAT_NETWORK = ipaddress.ip_network("100.64.0.0/10")


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """如果该 IP 应因 SSRF 保护而被阻止，返回 True。"""
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
        return True
    if ip.is_multicast or ip.is_unspecified:
        return True
    # 不在 is_private 覆盖范围内的 CGNAT 地址段
    if ip in _CGNAT_NETWORK:
        return True
    return False


def is_safe_url(url: str) -> bool:
    """如果 URL 目标不是私有/内部地址，返回 True。

    将主机名解析为 IP 并检查是否属于私有范围。
    失败关闭：DNS 错误和意外异常会阻止请求。
    """
    try:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").strip().lower()
        if not hostname:
            return False

        # 阻止已知的内部主机名
        if hostname in _BLOCKED_HOSTNAMES:
            logger.warning("Blocked request to internal hostname: %s", hostname)
            return False

        # 尝试解析并检查 IP
        try:
            addr_info = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        except socket.gaierror:
            # DNS 解析失败——失败关闭。如果 DNS 无法解析，
            # HTTP 客户端也会失败，所以阻止不会造成任何损失。
            logger.warning("Blocked request — DNS resolution failed for: %s", hostname)
            return False

        for family, _, _, _, sockaddr in addr_info:
            ip_str = sockaddr[0]
            try:
                ip = ipaddress.ip_address(ip_str)
            except ValueError:
                continue

            if _is_blocked_ip(ip):
                logger.warning(
                    "Blocked request to private/internal address: %s -> %s",
                    hostname, ip_str,
                )
                return False

        return True

    except Exception as exc:
        # 失败关闭——不要让解析边缘情况成为 SSRF 绕过向量
        logger.warning("Blocked request — URL safety check error for %s: %s", url, exc)
        return False
