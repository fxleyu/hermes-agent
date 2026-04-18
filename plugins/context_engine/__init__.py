"""上下文引擎插件发现模块。

扫描 ``plugins/context_engine/<name>/`` 目录以查找上下文引擎插件。
每个子目录必须包含 ``__init__.py``，其中有一个实现了 ContextEngine 抽象基类的类。

上下文引擎与通用插件系统分离 — 它们存放在代码仓库中，无需用户安装即可使用。
同一时间只能激活一个引擎，通过 config.yaml 中的 ``context.engine`` 选项选择。
默认引擎为 ``"compressor"``（内置的 ContextCompressor）。

用法:
    from plugins.context_engine import discover_context_engines, load_context_engine

    available = discover_context_engines()   # [(名称, 描述, 是否可用), ...]
    engine = load_context_engine("lcm")      # ContextEngine 实例
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import sys
from pathlib import Path
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

_CONTEXT_ENGINE_PLUGINS_DIR = Path(__file__).parent


def discover_context_engines() -> List[Tuple[str, str, bool]]:
    """扫描 plugins/context_engine/ 目录以查找可用引擎。

    返回 (名称, 描述, 是否可用) 元组的列表。
    不会导入引擎模块 — 仅读取 plugin.yaml 获取元数据，
    并进行轻量级的可用性检查。
    """
    results = []
    if not _CONTEXT_ENGINE_PLUGINS_DIR.is_dir():
        return results

    for child in sorted(_CONTEXT_ENGINE_PLUGINS_DIR.iterdir()):
        # 跳过以下划线或点号开头的目录（私有目录/隐藏目录）
        if not child.is_dir() or child.name.startswith(("_", ".")):
            continue
        init_file = child / "__init__.py"
        if not init_file.exists():
            continue

        # 如果有 plugin.yaml，从中读取描述信息
        desc = ""
        yaml_file = child / "plugin.yaml"
        if yaml_file.exists():
            try:
                import yaml
                with open(yaml_file) as f:
                    meta = yaml.safe_load(f) or {}
                desc = meta.get("description", "")
            except Exception:
                pass

        # 快速可用性检查 — 尝试加载并调用 is_available()
        available = True
        try:
            engine = _load_engine_from_dir(child)
            if engine is None:
                available = False
            elif hasattr(engine, "is_available"):
                available = engine.is_available()
        except Exception:
            available = False

        results.append((child.name, desc, available))

    return results


def load_context_engine(name: str) -> Optional["ContextEngine"]:
    """根据名称加载并返回 ContextEngine 实例。

    如果引擎未找到或加载失败，返回 None。
    """
    engine_dir = _CONTEXT_ENGINE_PLUGINS_DIR / name
    if not engine_dir.is_dir():
        logger.debug("Context engine '%s' not found in %s", name, _CONTEXT_ENGINE_PLUGINS_DIR)
        return None

    try:
        engine = _load_engine_from_dir(engine_dir)
        if engine:
            return engine
        logger.warning("Context engine '%s' loaded but no engine instance found", name)
        return None
    except Exception as e:
        logger.warning("Failed to load context engine '%s': %s", name, e)
        return None


def _load_engine_from_dir(engine_dir: Path) -> Optional["ContextEngine"]:
    """导入引擎模块并提取 ContextEngine 实例。

    模块必须具有以下其一：
    - register(ctx) 函数（插件风格）— 我们模拟一个 ctx 对象
    - 继承 ContextEngine 的顶层类 — 我们将其实例化
    """
    name = engine_dir.name
    module_name = f"plugins.context_engine.{name}"
    init_file = engine_dir / "__init__.py"

    if not init_file.exists():
        return None

    # 检查是否已加载过该模块
    if module_name in sys.modules:
        mod = sys.modules[module_name]
    else:
        # 处理插件内的相对导入
        # 首先确保父包已注册到 sys.modules
        for parent in ("plugins", "plugins.context_engine"):
            if parent not in sys.modules:
                parent_path = Path(__file__).parent
                if parent == "plugins":
                    parent_path = parent_path.parent
                parent_init = parent_path / "__init__.py"
                if parent_init.exists():
                    spec = importlib.util.spec_from_file_location(
                        parent, str(parent_init),
                        submodule_search_locations=[str(parent_path)]
                    )
                    if spec:
                        parent_mod = importlib.util.module_from_spec(spec)
                        sys.modules[parent] = parent_mod
                        try:
                            spec.loader.exec_module(parent_mod)
                        except Exception:
                            pass

        # 现在加载引擎模块本身
        spec = importlib.util.spec_from_file_location(
            module_name, str(init_file),
            submodule_search_locations=[str(engine_dir)]
        )
        if not spec:
            return None

        mod = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = mod

        # 注册子模块以便相对导入能正常工作
        for sub_file in engine_dir.glob("*.py"):
            if sub_file.name == "__init__.py":
                continue
            sub_name = sub_file.stem
            full_sub_name = f"{module_name}.{sub_name}"
            if full_sub_name not in sys.modules:
                sub_spec = importlib.util.spec_from_file_location(
                    full_sub_name, str(sub_file)
                )
                if sub_spec:
                    sub_mod = importlib.util.module_from_spec(sub_spec)
                    sys.modules[full_sub_name] = sub_mod
                    try:
                        sub_spec.loader.exec_module(sub_mod)
                    except Exception as e:
                        logger.debug("Failed to load submodule %s: %s", full_sub_name, e)

        try:
            spec.loader.exec_module(mod)
        except Exception as e:
            logger.debug("Failed to exec_module %s: %s", module_name, e)
            sys.modules.pop(module_name, None)
            return None

    # 首先尝试 register(ctx) 模式（插件的标准编写方式）
    if hasattr(mod, "register"):
        collector = _EngineCollector()
        try:
            mod.register(collector)
            if collector.engine:
                return collector.engine
        except Exception as e:
            logger.debug("register() failed for %s: %s", name, e)

    # 回退方案：查找 ContextEngine 子类并实例化
    from agent.context_engine import ContextEngine
    for attr_name in dir(mod):
        attr = getattr(mod, attr_name, None)
        if (isinstance(attr, type) and issubclass(attr, ContextEngine)
                and attr is not ContextEngine):
            try:
                return attr()
            except Exception:
                pass

    return None


class _EngineCollector:
    """模拟的插件上下文对象，用于捕获 register_context_engine 调用。"""

    def __init__(self):
        self.engine = None

    def register_context_engine(self, engine):
        self.engine = engine

    # 其他注册方法的空操作实现
    def register_tool(self, *args, **kwargs):
        pass

    def register_hook(self, *args, **kwargs):
        pass

    def register_cli_command(self, *args, **kwargs):
        pass

    def register_memory_provider(self, *args, **kwargs):
        pass
