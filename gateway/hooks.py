"""
事件钩子系统

一个轻量级事件驱动系统，在关键生命周期节点触发处理器。
钩子从 ~/.hermes/hooks/ 目录中发现，每个目录包含：
  - HOOK.yaml（元数据：名称、描述、事件列表）
  - handler.py（Python 处理器，包含 async def handle(event_type, context)）

事件类型：
  - gateway:startup     -- 网关进程启动
  - session:start       -- 新会话创建（新会话的第一条消息）
  - session:end         -- 会话结束（用户执行了 /new 或 /reset）
  - session:reset       -- 会话重置完成（新的会话条目已创建）
  - agent:start         -- 代理开始处理消息
  - agent:step          -- 工具调用循环中的每一轮
  - agent:end           -- 代理完成处理
  - command:*           -- 任何斜杠命令被执行（通配符匹配）

钩子中的错误会被捕获并记录日志，但绝不会阻塞主管道。
"""

import asyncio
import importlib.util
from typing import Any, Callable, Dict, List, Optional

import yaml

from hermes_cli.config import get_hermes_home


HOOKS_DIR = get_hermes_home() / "hooks"


class HookRegistry:
    """
    发现、加载和触发事件钩子。

    用法：
        registry = HookRegistry()
        registry.discover_and_load()
        await registry.emit("agent:start", {"platform": "telegram", ...})
    """

    def __init__(self):
        # 事件类型 -> [处理器函数, ...]
        self._handlers: Dict[str, List[Callable]] = {}
        self._loaded_hooks: List[dict] = []  # 用于列举的元数据

    @property
    def loaded_hooks(self) -> List[dict]:
        """返回所有已加载钩子的元数据。"""
        return list(self._loaded_hooks)

    def _register_builtin_hooks(self) -> None:
        """注册始终活跃的内置钩子。"""
        try:
            from gateway.builtin_hooks.boot_md import handle as boot_md_handle

            self._handlers.setdefault("gateway:startup", []).append(boot_md_handle)
            self._loaded_hooks.append({
                "name": "boot-md",
                "description": "Run ~/.hermes/BOOT.md on gateway startup",
                "events": ["gateway:startup"],
                "path": "(builtin)",
            })
        except Exception as e:
            print(f"[hooks] Could not load built-in boot-md hook: {e}", flush=True)

    def discover_and_load(self) -> None:
        """
        扫描 hooks 目录查找钩子目录并加载其处理器。

        同时注册始终活跃的内置钩子。

        每个钩子目录必须包含：
          - HOOK.yaml，至少有 'name' 和 'events' 键
          - handler.py，包含顶层 'handle' 函数（同步或异步）
        """
        self._register_builtin_hooks()

        if not HOOKS_DIR.exists():
            return

        for hook_dir in sorted(HOOKS_DIR.iterdir()):
            if not hook_dir.is_dir():
                continue

            manifest_path = hook_dir / "HOOK.yaml"
            handler_path = hook_dir / "handler.py"

            if not manifest_path.exists() or not handler_path.exists():
                continue

            try:
                manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
                if not manifest or not isinstance(manifest, dict):
                    print(f"[hooks] Skipping {hook_dir.name}: invalid HOOK.yaml", flush=True)
                    continue

                hook_name = manifest.get("name", hook_dir.name)
                events = manifest.get("events", [])
                if not events:
                    print(f"[hooks] Skipping {hook_name}: no events declared", flush=True)
                    continue

                # 动态加载处理器模块
                spec = importlib.util.spec_from_file_location(
                    f"hermes_hook_{hook_name}", handler_path
                )
                if spec is None or spec.loader is None:
                    print(f"[hooks] Skipping {hook_name}: could not load handler.py", flush=True)
                    continue

                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)

                handle_fn = getattr(module, "handle", None)
                if handle_fn is None:
                    print(f"[hooks] Skipping {hook_name}: no 'handle' function found", flush=True)
                    continue

                # 为每个声明的事件注册处理器
                for event in events:
                    self._handlers.setdefault(event, []).append(handle_fn)

                self._loaded_hooks.append({
                    "name": hook_name,
                    "description": manifest.get("description", ""),
                    "events": events,
                    "path": str(hook_dir),
                })

                print(f"[hooks] Loaded hook '{hook_name}' for events: {events}", flush=True)

            except Exception as e:
                print(f"[hooks] Error loading hook {hook_dir.name}: {e}", flush=True)

    async def emit(self, event_type: str, context: Optional[Dict[str, Any]] = None) -> None:
        """
        触发为某个事件注册的所有处理器。

        支持通配符匹配：注册到 "command:*" 的处理器会对任何
        "command:..." 事件触发。注册到基本类型如 "agent" 的处理器
        不会对 "agent:start" 触发 — 仅精确匹配和显式通配符生效。

        参数：
            event_type: 事件标识符（例如 "agent:start"）。
            context:    可选的事件特定数据字典。
        """
        if context is None:
            context = {}

        # 收集处理器：精确匹配 + 通配符匹配
        handlers = list(self._handlers.get(event_type, []))

        # 检查通配符模式（例如 "command:*" 匹配 "command:reset"）
        if ":" in event_type:
            base = event_type.split(":")[0]
            wildcard_key = f"{base}:*"
            handlers.extend(self._handlers.get(wildcard_key, []))

        for fn in handlers:
            try:
                result = fn(event_type, context)
                # 支持同步和异步处理器
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                print(f"[hooks] Error in handler for '{event_type}': {e}", flush=True)
