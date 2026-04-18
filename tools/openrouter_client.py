"""Hermes 工具的共享 OpenRouter API 客户端。

提供一个延迟初始化的共享 AsyncOpenAI 客户端供所有工具模块使用。
通过 agent/auxiliary_client.py 中的集中式提供者路由，
统一处理认证、请求头和 API 格式。
"""

import os

_client = None


def get_async_client():
    """返回一个用于 OpenRouter 的共享异步 OpenAI 兼容客户端。

    客户端在首次调用时延迟创建，之后复用同一实例。
    使用集中式提供者路由进行认证和客户端构造。
    如果 OPENROUTER_API_KEY 未设置，则抛出 ValueError。
    """
    global _client
    if _client is None:
        from agent.auxiliary_client import resolve_provider_client
        client, _model = resolve_provider_client("openrouter", async_mode=True)
        if client is None:
            raise ValueError("OPENROUTER_API_KEY environment variable not set")
        _client = client
    return _client


def check_api_key() -> bool:
    """检查 OpenRouter API 密钥是否存在。"""
    return bool(os.getenv("OPENROUTER_API_KEY"))
