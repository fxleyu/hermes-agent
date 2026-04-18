"""示例仪表盘插件 — 后端 API 路由。

由仪表盘插件系统挂载到 /api/plugins/example/ 路径。
"""

from fastapi import APIRouter

router = APIRouter()


@router.get("/hello")
async def hello():
    """简单的问候端点，用于演示插件 API 路由。"""
    return {"message": "Hello from the example plugin!", "plugin": "example", "version": "1.0.0"}
