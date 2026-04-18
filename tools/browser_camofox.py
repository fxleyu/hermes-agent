"""Camofox 浏览器后端 — 通过 REST API 控制本地反检测浏览器。

Camofox-browser 是一个自托管的 Node.js 服务器，封装了 Camoufox
（带有 C++ 指纹欺骗的 Firefox 分支）。它暴露了一个 REST API，
与我们的浏览器工具接口一一映射：无障碍快照配合元素引用、
按引用点击/输入/滚动、截图等。

当设置了 ``CAMOFOX_URL``（如 ``http://localhost:9377``）时，
浏览器工具将通过此模块路由，而不是 ``agent-browser`` CLI。

安装::

    # 方式 1: npm
    git clone https://github.com/jo-inc/camofox-browser && cd camofox-browser
    npm install && npm start   # 首次运行时下载 Camoufox (~300MB)

    # 方式 2: Docker
    docker run -p 9377:9377 -e CAMOFOX_PORT=9377 jo-inc/camofox-browser

然后在 ``~/.hermes/.env`` 中设置 ``CAMOFOX_URL=http://localhost:9377``。
"""

from __future__ import annotations

import base64
import json
import logging
import os
import threading
import uuid
from typing import Any, Dict, Optional

import requests

from hermes_cli.config import load_config
from tools.browser_camofox_state import get_camofox_identity
from tools.registry import tool_error

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

_DEFAULT_TIMEOUT = 30  # 每个 HTTP 请求的超时秒数
_SNAPSHOT_MAX_CHARS = 80_000  # camofox 在此限制处分页
_vnc_url: Optional[str] = None  # 从 /health 响应中缓存
_vnc_url_checked = False  # 每个进程仅探测一次


def get_camofox_url() -> str:
    """返回配置的 Camofox 服务器 URL，或空字符串。"""
    return os.getenv("CAMOFOX_URL", "").rstrip("/")


def is_camofox_mode() -> bool:
    """当 Camofox 后端已配置且无 CDP 覆盖时返回 True。

    当用户通过 ``/browser connect``（设置 ``BROWSER_CDP_URL``）显式
    连接到实时 Chrome 实例时，CDP 连接优先于 Camofox，
    这样浏览器工具操作的是真实浏览器，而不是被静默路由到 Camofox 后端。
    """
    if os.getenv("BROWSER_CDP_URL", "").strip():
        return False
    return bool(get_camofox_url())


def check_camofox_available() -> bool:
    """验证 Camofox 服务器是否可达。"""
    global _vnc_url, _vnc_url_checked
    url = get_camofox_url()
    if not url:
        return False
    try:
        resp = requests.get(f"{url}/health", timeout=5)
        if resp.status_code == 200 and not _vnc_url_checked:
            try:
                data = resp.json()
                vnc_port = data.get("vncPort")
                if isinstance(vnc_port, int) and 1 <= vnc_port <= 65535:
                    from urllib.parse import urlparse
                    parsed = urlparse(url)
                    host = parsed.hostname or "localhost"
                    _vnc_url = f"http://{host}:{vnc_port}"
            except (ValueError, KeyError):
                pass
            _vnc_url_checked = True
        return resp.status_code == 200
    except Exception:
        return False


def get_vnc_url() -> Optional[str]:
    """如果 Camofox 服务器暴露了 VNC，则返回其 URL，否则返回 None。"""
    if not _vnc_url_checked:
        check_camofox_available()
    return _vnc_url


def _managed_persistence_enabled() -> bool:
    """返回 Camofox 是否启用了 Hermes 管理的持久化。

    启用时，会话使用稳定的配置文件作用域 userId，这样 Camofox
    服务器可以将其映射到持久化的浏览器配置文件目录。
    禁用时（默认），每个会话获得随机 userId（临时的）。

    通过 config.yaml 中的 ``browser.camofox.managed_persistence`` 控制。
    """
    try:
        camofox_cfg = load_config().get("browser", {}).get("camofox", {})
    except Exception as exc:
        logger.warning("managed_persistence check failed, defaulting to disabled: %s", exc)
        return False
    return bool(camofox_cfg.get("managed_persistence"))


# ---------------------------------------------------------------------------
# 会话管理
# ---------------------------------------------------------------------------
# 映射 task_id -> {"user_id": str, "tab_id": str|None}
_sessions: Dict[str, Dict[str, Any]] = {}
_sessions_lock = threading.Lock()


def _get_session(task_id: Optional[str]) -> Dict[str, Any]:
    """获取或创建给定任务的 camofox 会话。

    当启用托管持久化时，使用从 Hermes 配置文件派生的确定性 userId，
    这样 Camofox 服务器可以在重启后将其映射到相同的持久化浏览器配置文件。
    """
    task_id = task_id or "default"
    with _sessions_lock:
        if task_id in _sessions:
            return _sessions[task_id]
        if _managed_persistence_enabled():
            identity = get_camofox_identity(task_id)
            session = {
                "user_id": identity["user_id"],
                "tab_id": None,
                "session_key": identity["session_key"],
                "managed": True,
            }
        else:
            session = {
                "user_id": f"hermes_{uuid.uuid4().hex[:10]}",
                "tab_id": None,
                "session_key": f"task_{task_id[:16]}",
                "managed": False,
            }
        _sessions[task_id] = session
        return session


def _ensure_tab(task_id: Optional[str], url: str = "about:blank") -> Dict[str, Any]:
    """确保会话中存在标签页，如果不存在则创建一个。"""
    session = _get_session(task_id)
    if session["tab_id"]:
        return session
    base = get_camofox_url()
    resp = requests.post(
        f"{base}/tabs",
        json={
            "userId": session["user_id"],
            "sessionKey": session["session_key"],
            "url": url,
        },
        timeout=_DEFAULT_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    session["tab_id"] = data.get("tabId")
    return session


def _drop_session(task_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """移除并返回会话信息。"""
    task_id = task_id or "default"
    with _sessions_lock:
        return _sessions.pop(task_id, None)


def camofox_soft_cleanup(task_id: Optional[str] = None) -> bool:
    """释放内存中的会话，但不销毁服务器端上下文。

    当启用托管持久化时，浏览器配置文件（及其 cookie）
    必须在代理任务之间存活。此辅助函数仅丢弃本地跟踪
    条目并返回 ``True``。当托管持久化*未*启用时，
    什么也不做并返回 ``False``，调用方可以回退到
    :func:`camofox_close`。
    """
    if _managed_persistence_enabled():
        _drop_session(task_id)
        logger.debug("Camofox soft cleanup for task %s (managed persistence)", task_id)
        return True
    return False


# ---------------------------------------------------------------------------
# HTTP 辅助方法
# ---------------------------------------------------------------------------

def _post(path: str, body: dict, timeout: int = _DEFAULT_TIMEOUT) -> dict:
    """向 camofox 发送 POST JSON 请求并返回解析后的响应。"""
    url = f"{get_camofox_url()}{path}"
    resp = requests.post(url, json=body, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _get(path: str, params: dict = None, timeout: int = _DEFAULT_TIMEOUT) -> dict:
    """从 camofox 发送 GET 请求并返回解析后的响应。"""
    url = f"{get_camofox_url()}{path}"
    resp = requests.get(url, params=params, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _get_raw(path: str, params: dict = None, timeout: int = _DEFAULT_TIMEOUT) -> requests.Response:
    """从 camofox 发送 GET 请求并返回原始响应（用于二进制数据）。"""
    url = f"{get_camofox_url()}{path}"
    resp = requests.get(url, params=params, timeout=timeout)
    resp.raise_for_status()
    return resp


def _delete(path: str, body: dict = None, timeout: int = _DEFAULT_TIMEOUT) -> dict:
    """向 camofox 发送 DELETE 请求并返回解析后的响应。"""
    url = f"{get_camofox_url()}{path}"
    resp = requests.delete(url, json=body, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# 工具实现
# ---------------------------------------------------------------------------

def camofox_navigate(url: str, task_id: Optional[str] = None) -> str:
    """通过 Camofox 导航到指定 URL。"""
    try:
        session = _get_session(task_id)
        if not session["tab_id"]:
            # 直接使用目标 URL 创建标签页
            session = _ensure_tab(task_id, url)
            data = {"ok": True, "url": url}
        else:
            # 导航现有标签页
            data = _post(
                f"/tabs/{session['tab_id']}/navigate",
                {"userId": session["user_id"], "url": url},
                timeout=60,
            )
        result = {
            "success": True,
            "url": data.get("url", url),
            "title": data.get("title", ""),
        }
        vnc = get_vnc_url()
        if vnc:
            result["vnc_url"] = vnc
            result["vnc_hint"] = (
                "Browser is visible via VNC. "
                "Share this link with the user so they can watch the browser live."
            )

        # 自动获取紧凑快照以便模型可以立即操作
        try:
            snap_data = _get(
                f"/tabs/{session['tab_id']}/snapshot",
                params={"userId": session["user_id"]},
            )
            snapshot_text = snap_data.get("snapshot", "")
            from tools.browser_tool import (
                SNAPSHOT_SUMMARIZE_THRESHOLD,
                _truncate_snapshot,
            )
            if len(snapshot_text) > SNAPSHOT_SUMMARIZE_THRESHOLD:
                snapshot_text = _truncate_snapshot(snapshot_text)
            result["snapshot"] = snapshot_text
            result["element_count"] = snap_data.get("refsCount", 0)
        except Exception:
            pass  # 导航成功；快照是额外的奖励

        return json.dumps(result)
    except requests.HTTPError as e:
        return tool_error(f"Navigation failed: {e}", success=False)
    except requests.ConnectionError:
        return json.dumps({
            "success": False,
            "error": f"Cannot connect to Camofox at {get_camofox_url()}. "
                     "Is the server running? Start with: npm start (in camofox-browser dir) "
                     "or: docker run -p 9377:9377 -e CAMOFOX_PORT=9377 jo-inc/camofox-browser",
        })
    except Exception as e:
        return tool_error(str(e), success=False)


def camofox_snapshot(full: bool = False, task_id: Optional[str] = None,
                     user_task: Optional[str] = None) -> str:
    """从 Camofox 获取无障碍树快照。"""
    try:
        session = _get_session(task_id)
        if not session["tab_id"]:
            return tool_error("No browser session. Call browser_navigate first.", success=False)

        data = _get(
            f"/tabs/{session['tab_id']}/snapshot",
            params={"userId": session["user_id"]},
        )

        snapshot = data.get("snapshot", "")
        refs_count = data.get("refsCount", 0)

        # 应用与主浏览器工具相同的摘要逻辑
        from tools.browser_tool import (
            SNAPSHOT_SUMMARIZE_THRESHOLD,
            _extract_relevant_content,
            _truncate_snapshot,
        )

        if len(snapshot) > SNAPSHOT_SUMMARIZE_THRESHOLD:
            if user_task:
                snapshot = _extract_relevant_content(snapshot, user_task)
            else:
                snapshot = _truncate_snapshot(snapshot)

        return json.dumps({
            "success": True,
            "snapshot": snapshot,
            "element_count": refs_count,
        })
    except Exception as e:
        return tool_error(str(e), success=False)


def camofox_click(ref: str, task_id: Optional[str] = None) -> str:
    """通过 Camofox 按引用点击元素。"""
    try:
        session = _get_session(task_id)
        if not session["tab_id"]:
            return tool_error("No browser session. Call browser_navigate first.", success=False)

        # 去除 @ 前缀（我们的工具约定）
        clean_ref = ref.lstrip("@")

        data = _post(
            f"/tabs/{session['tab_id']}/click",
            {"userId": session["user_id"], "ref": clean_ref},
        )
        return json.dumps({
            "success": True,
            "clicked": clean_ref,
            "url": data.get("url", ""),
        })
    except Exception as e:
        return tool_error(str(e), success=False)


def camofox_type(ref: str, text: str, task_id: Optional[str] = None) -> str:
    """通过 Camofox 在指定引用的元素中输入文本。"""
    try:
        session = _get_session(task_id)
        if not session["tab_id"]:
            return tool_error("No browser session. Call browser_navigate first.", success=False)

        clean_ref = ref.lstrip("@")

        _post(
            f"/tabs/{session['tab_id']}/type",
            {"userId": session["user_id"], "ref": clean_ref, "text": text},
        )
        return json.dumps({
            "success": True,
            "typed": text,
            "element": clean_ref,
        })
    except Exception as e:
        return tool_error(str(e), success=False)


def camofox_scroll(direction: str, task_id: Optional[str] = None) -> str:
    """通过 Camofox 滚动页面。"""
    try:
        session = _get_session(task_id)
        if not session["tab_id"]:
            return tool_error("No browser session. Call browser_navigate first.", success=False)

        _post(
            f"/tabs/{session['tab_id']}/scroll",
            {"userId": session["user_id"], "direction": direction},
        )
        return json.dumps({"success": True, "scrolled": direction})
    except Exception as e:
        return tool_error(str(e), success=False)


def camofox_back(task_id: Optional[str] = None) -> str:
    """通过 Camofox 后退导航。"""
    try:
        session = _get_session(task_id)
        if not session["tab_id"]:
            return tool_error("No browser session. Call browser_navigate first.", success=False)

        data = _post(
            f"/tabs/{session['tab_id']}/back",
            {"userId": session["user_id"]},
        )
        return json.dumps({"success": True, "url": data.get("url", "")})
    except Exception as e:
        return tool_error(str(e), success=False)


def camofox_press(key: str, task_id: Optional[str] = None) -> str:
    """通过 Camofox 按下键盘按键。"""
    try:
        session = _get_session(task_id)
        if not session["tab_id"]:
            return tool_error("No browser session. Call browser_navigate first.", success=False)

        _post(
            f"/tabs/{session['tab_id']}/press",
            {"userId": session["user_id"], "key": key},
        )
        return json.dumps({"success": True, "pressed": key})
    except Exception as e:
        return tool_error(str(e), success=False)


def camofox_close(task_id: Optional[str] = None) -> str:
    """通过 Camofox 关闭浏览器会话。"""
    try:
        session = _drop_session(task_id)
        if not session:
            return json.dumps({"success": True, "closed": True})

        _delete(
            f"/sessions/{session['user_id']}",
        )
        return json.dumps({"success": True, "closed": True})
    except Exception as e:
        return json.dumps({"success": True, "closed": True, "warning": str(e)})


def camofox_get_images(task_id: Optional[str] = None) -> str:
    """通过 Camofox 获取当前页面的图片。

    从无障碍树快照中提取图片信息，
    因为 Camofox 没有暴露专用的 /images 端点。
    """
    try:
        session = _get_session(task_id)
        if not session["tab_id"]:
            return tool_error("No browser session. Call browser_navigate first.", success=False)

        import re

        data = _get(
            f"/tabs/{session['tab_id']}/snapshot",
            params={"userId": session["user_id"]},
        )
        snapshot = data.get("snapshot", "")

        # 从无障碍树中解析 img 元素。
        # 格式: img "alt text" 或 img "alt text" [eN]
        # URL 出现在 img 条目后的 /url: 行上
        images = []
        lines = snapshot.split("\n")
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith(("- img ", "img ")):
                alt_match = re.search(r'img\s+"([^"]*)"', stripped)
                alt = alt_match.group(1) if alt_match else ""
                # 在下一行查找 URL
                src = ""
                if i + 1 < len(lines):
                    url_match = re.search(r'/url:\s*(\S+)', lines[i + 1].strip())
                    if url_match:
                        src = url_match.group(1)
                if alt or src:
                    images.append({"src": src, "alt": alt})

        return json.dumps({
            "success": True,
            "images": images,
            "count": len(images),
        })
    except Exception as e:
        return tool_error(str(e), success=False)


def camofox_vision(question: str, annotate: bool = False,
                   task_id: Optional[str] = None) -> str:
    """截取屏幕截图并通过视觉 AI 进行分析。"""
    try:
        session = _get_session(task_id)
        if not session["tab_id"]:
            return tool_error("No browser session. Call browser_navigate first.", success=False)

        # 获取 PNG 格式的截图二进制数据
        resp = _get_raw(
            f"/tabs/{session['tab_id']}/screenshot",
            params={"userId": session["user_id"]},
        )

        # 保存截图到缓存
        from hermes_constants import get_hermes_home
        screenshots_dir = get_hermes_home() / "browser_screenshots"
        screenshots_dir.mkdir(parents=True, exist_ok=True)
        screenshot_path = str(screenshots_dir / f"browser_screenshot_{uuid.uuid4().hex[:8]}.png")

        with open(screenshot_path, "wb") as f:
            f.write(resp.content)

        # 编码为 base64 供视觉 LLM 使用
        img_b64 = base64.b64encode(resp.content).decode("utf-8")

        # 如果需要，同时获取带注释的快照
        annotation_context = ""
        if annotate:
            try:
                snap_data = _get(
                    f"/tabs/{session['tab_id']}/snapshot",
                    params={"userId": session["user_id"]},
                )
                annotation_context = f"\n\nAccessibility tree (element refs for interaction):\n{snap_data.get('snapshot', '')[:3000]}"
            except Exception:
                pass

        # 在发送给视觉 LLM 之前，从注释上下文中脱敏密钥。
        # 截图图像本身无法脱敏，但至少基于文本的
        # 无障碍树片段不会泄露密钥值。
        from agent.redact import redact_sensitive_text
        annotation_context = redact_sensitive_text(annotation_context)

        # 发送给视觉 LLM
        from agent.auxiliary_client import call_llm

        vision_prompt = (
            f"Analyze this browser screenshot and answer: {question}"
            f"{annotation_context}"
        )

        try:
            from hermes_cli.config import load_config
            _cfg = load_config()
            _vision_timeout = int(_cfg.get("auxiliary", {}).get("vision", {}).get("timeout", 120))
        except Exception:
            _vision_timeout = 120

        response = call_llm(
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": vision_prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{img_b64}",
                        },
                    },
                ],
            }],
            task="vision",
            timeout=_vision_timeout,
        )
        analysis = (response.choices[0].message.content or "").strip() if response.choices else ""

        # 脱敏视觉 LLM 可能从截图中读取到的密钥。
        from agent.redact import redact_sensitive_text
        analysis = redact_sensitive_text(analysis)

        return json.dumps({
            "success": True,
            "analysis": analysis,
            "screenshot_path": screenshot_path,
        })
    except Exception as e:
        return tool_error(str(e), success=False)


def camofox_console(clear: bool = False, task_id: Optional[str] = None) -> str:
    """获取控制台输出 — 在 Camofox 中支持有限。

    Camofox 不通过其 REST API 暴露浏览器控制台日志。
    返回空结果并附带说明。
    """
    return json.dumps({
        "success": True,
        "console_messages": [],
        "js_errors": [],
        "total_messages": 0,
        "total_errors": 0,
        "note": "Console log capture is not available with the Camofox backend. "
                "Use browser_snapshot or browser_vision to inspect page state.",
    })



