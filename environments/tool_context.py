"""
ToolContext -- 奖励函数的无限制工具访问

每次 rollout 的句柄，为奖励/验证函数提供对所有 hermes-agent 工具的直接访问，
作用域限定在 rollout 的 task_id 下。相同的 task_id 意味着终端/浏览器会话
与模型在 rollout 期间使用的是同一个 -- 所有状态（文件、进程、浏览器标签页）
都被保留。

验证器作者自行决定使用哪些工具，没有硬编码或门控限制。

在 compute_reward() 中的使用示例：
    async def compute_reward(self, item, result, ctx):
        # 在模型的终端沙箱中运行测试
        test = ctx.terminal("pytest -v")
        if test["exit_code"] == 0:
            return 1.0

        # 检查文件是否已创建
        content = ctx.read_file("/workspace/solution.py")
        if content.get("content"):
            return 0.5

        return 0.0
"""

import json
import logging
import os
from typing import Any, Dict, List, Optional

import asyncio
import concurrent.futures

from model_tools import handle_function_call
from tools.terminal_tool import cleanup_vm
from tools.browser_tool import cleanup_browser

logger = logging.getLogger(__name__)

# 用于运行内部使用 asyncio.run() 的同步工具调用的线程池
_tool_executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)


def _run_tool_in_thread(tool_name: str, arguments: Dict[str, Any], task_id: str) -> str:
    """
    在线程池执行器中运行工具调用，使得内部使用 asyncio.run() 的后端
    （modal、docker、daytona）获得干净的事件循环。

    如果已经在异步上下文中，则在一次性工作线程中执行 handle_function_call()
    并阻塞等待结果。
    如果不在异步上下文中（例如从同步代码调用），则直接运行。
    """
    try:
        loop = asyncio.get_running_loop()
        # 在异步上下文中 -- 需要在线程中运行
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                handle_function_call, tool_name, arguments, task_id
            )
            return future.result(timeout=300)
    except RuntimeError:
        # 没有运行中的事件循环 -- 可以安全地直接调用
        return handle_function_call(tool_name, arguments, task_id)


class ToolContext:
    """
    为特定 rollout 提供对所有 hermes-agent 工具的开放式访问。

    传递给 compute_reward()，以便验证器可以使用任何所需工具：
    终端命令、文件读写、网页搜索、浏览器自动化等。
    所有调用共享 rollout 的 task_id 以实现会话隔离。
    """

    def __init__(self, task_id: str):
        self.task_id = task_id

    # -------------------------------------------------------------------------
    # 终端工具
    # -------------------------------------------------------------------------

    def terminal(self, command: str, timeout: int = 180) -> Dict[str, Any]:
        """
        在 rollout 的终端会话中运行命令。

        参数：
            command: 要执行的 shell 命令
            timeout: 命令超时时间（秒）

        返回：
            包含 'exit_code'（int）和 'output'（str）的字典
        """
        import os
        backend = os.getenv("TERMINAL_ENV", "local")
        logger.debug("ToolContext.terminal [%s backend] task=%s: %s", backend, self.task_id[:8], command[:100])

        # 通过线程辅助运行，避免 modal/docker/daytona 后端的 asyncio.run() 死锁
        result = _run_tool_in_thread(
            "terminal",
            {"command": command, "timeout": timeout},
            self.task_id,
        )
        try:
            return json.loads(result)
        except json.JSONDecodeError:
            return {"exit_code": -1, "output": result}

    # -------------------------------------------------------------------------
    # 文件工具
    # -------------------------------------------------------------------------

    def read_file(self, path: str) -> Dict[str, Any]:
        """
        从 rollout 的文件系统中读取文件。

        参数：
            path: 要读取的文件路径

        返回：
            包含文件内容或错误的字典
        """
        result = handle_function_call(
            "read_file", {"path": path}, task_id=self.task_id
        )
        try:
            return json.loads(result)
        except json.JSONDecodeError:
            return {"error": result}

    def write_file(self, path: str, content: str) -> Dict[str, Any]:
        """
        在 rollout 的文件系统中写入文本文件。

        底层使用 shell heredoc，因此仅适用于文本内容。
        对于二进制文件（图片、编译产物等），请改用 upload_file()。

        参数：
            path: 要写入的文件路径
            content: 要写入的文本内容

        返回：
            包含成功状态或错误的字典
        """
        result = handle_function_call(
            "write_file", {"path": path, "content": content}, task_id=self.task_id
        )
        try:
            return json.loads(result)
        except json.JSONDecodeError:
            return {"error": result}

    def upload_file(self, local_path: str, remote_path: str) -> Dict[str, Any]:
        """
        将本地文件上传到 rollout 的沙箱（支持二进制文件）。

        与通过 shell heredoc 传递内容（仅文本）的 write_file() 不同，
        此方法对文件进行 base64 编码并在沙箱内解码。
        适用于任何文件类型：二进制文件、图片、压缩包等。

        对于大文件（>1MB），内容会被分块以避免超出 shell 命令长度限制。

        参数：
            local_path: 主机上的本地文件路径
            remote_path: 沙箱内的目标路径

        返回：
            包含 'exit_code' 和 'output' 的字典
        """
        import base64
        from pathlib import Path as _Path

        local = _Path(local_path)
        if not local.exists():
            return {"exit_code": -1, "output": f"Local file not found: {local_path}"}

        raw = local.read_bytes()
        b64 = base64.b64encode(raw).decode("ascii")

        # 确保沙箱中的父目录存在
        parent = str(_Path(remote_path).parent)
        if parent not in (".", "/"):
            self.terminal(f"mkdir -p {parent}", timeout=10)

        # 对于小文件，单条命令即可
        chunk_size = 60_000  # 每块约 60KB（远在 shell 限制之内）
        if len(b64) <= chunk_size:
            result = self.terminal(
                f"printf '%s' '{b64}' | base64 -d > {remote_path}",
                timeout=30,
            )
        else:
            # 对于大文件，分块写入 base64 数据再解码
            tmp_b64 = "/tmp/_hermes_upload.b64"
            self.terminal(f": > {tmp_b64}", timeout=5)  # 清空文件
            for i in range(0, len(b64), chunk_size):
                chunk = b64[i : i + chunk_size]
                self.terminal(f"printf '%s' '{chunk}' >> {tmp_b64}", timeout=15)
            result = self.terminal(
                f"base64 -d {tmp_b64} > {remote_path} && rm -f {tmp_b64}",
                timeout=30,
            )

        return result

    def upload_dir(self, local_dir: str, remote_dir: str) -> List[Dict[str, Any]]:
        """
        将整个本地目录上传到 rollout 的沙箱（支持二进制文件）。

        递归上传所有文件，保持目录结构。

        参数：
            local_dir: 主机上的本地目录路径
            remote_dir: 沙箱内的目标目录

        返回：
            结果列表，每个上传的文件对应一个结果
        """
        from pathlib import Path as _Path

        local = _Path(local_dir)
        if not local.exists() or not local.is_dir():
            return [{"exit_code": -1, "output": f"Local directory not found: {local_dir}"}]

        results = []
        for file_path in sorted(local.rglob("*")):
            if file_path.is_file():
                relative = file_path.relative_to(local)
                target = f"{remote_dir}/{relative}"
                results.append(self.upload_file(str(file_path), target))
        return results

    def download_file(self, remote_path: str, local_path: str) -> Dict[str, Any]:
        """
        从 rollout 的沙箱下载文件到主机（支持二进制文件）。

        upload_file() 的反向操作。在沙箱内对文件进行 base64 编码，
        通过终端读取编码数据，然后在本地解码。
        适用于任何文件类型。

        参数：
            remote_path: 沙箱内的文件路径
            local_path: 主机上的目标路径

        返回：
            包含 'success'（bool）和 'bytes'（int）或 'error'（str）的字典
        """
        import base64
        from pathlib import Path as _Path

        # 在沙箱内对文件进行 Base64 编码并捕获输出
        result = self.terminal(
            f"base64 {remote_path} 2>/dev/null",
            timeout=30,
        )

        if result.get("exit_code", -1) != 0:
            return {
                "success": False,
                "error": f"Failed to read remote file: {result.get('output', '')}",
            }

        b64_data = result.get("output", "").strip()
        if not b64_data:
            return {"success": False, "error": f"Remote file is empty or missing: {remote_path}"}

        try:
            raw = base64.b64decode(b64_data)
        except Exception as e:
            return {"success": False, "error": f"Base64 decode failed: {e}"}

        # 写入本地主机文件系统
        local = _Path(local_path)
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(raw)

        return {"success": True, "bytes": len(raw)}

    def download_dir(self, remote_dir: str, local_dir: str) -> List[Dict[str, Any]]:
        """
        从 rollout 的沙箱下载目录到主机（支持二进制文件）。

        列出远程目录中的所有文件，然后逐个下载。
        保持目录结构。

        参数：
            remote_dir: 沙箱内的目录路径
            local_dir: 主机上的目标目录

        返回：
            结果列表，每个下载的文件对应一个结果
        """
        from pathlib import Path as _Path

        # 列出远程目录中的文件
        ls_result = self.terminal(
            f"find {remote_dir} -type f 2>/dev/null",
            timeout=15,
        )

        if ls_result.get("exit_code", -1) != 0:
            return [{"success": False, "error": f"Failed to list remote dir: {remote_dir}"}]

        file_list = ls_result.get("output", "").strip()
        if not file_list:
            return [{"success": False, "error": f"Remote directory is empty or missing: {remote_dir}"}]

        results = []
        for remote_file in file_list.splitlines():
            remote_file = remote_file.strip()
            if not remote_file:
                continue
            # 计算相对路径以保持目录结构
            if remote_file.startswith(remote_dir):
                relative = remote_file[len(remote_dir):].lstrip("/")
            else:
                relative = _Path(remote_file).name
            local_file = str(_Path(local_dir) / relative)
            results.append(self.download_file(remote_file, local_file))

        return results

    def search(self, query: str, path: str = ".") -> Dict[str, Any]:
        """
        在 rollout 的文件系统中搜索文本。

        参数：
            query: 搜索查询
            path: 要搜索的目录

        返回：
            包含搜索结果的字典
        """
        result = handle_function_call(
            "search_files", {"pattern": query, "path": path}, task_id=self.task_id
        )
        try:
            return json.loads(result)
        except json.JSONDecodeError:
            return {"error": result}

    # -------------------------------------------------------------------------
    # 网页工具
    # -------------------------------------------------------------------------

    def web_search(self, query: str) -> Dict[str, Any]:
        """
        搜索网页。

        参数：
            query: 搜索查询

        返回：
            包含搜索结果的字典
        """
        result = handle_function_call("web_search", {"query": query})
        try:
            return json.loads(result)
        except json.JSONDecodeError:
            return {"error": result}

    def web_extract(self, urls: List[str]) -> Dict[str, Any]:
        """
        从 URL 中提取内容。

        参数：
            urls: 要提取内容的 URL 列表

        返回：
            包含提取内容的字典
        """
        result = handle_function_call("web_extract", {"urls": urls})
        try:
            return json.loads(result)
        except json.JSONDecodeError:
            return {"error": result}

    # -------------------------------------------------------------------------
    # 浏览器工具
    # -------------------------------------------------------------------------

    def browser_navigate(self, url: str) -> Dict[str, Any]:
        """
        将 rollout 的浏览器会话导航到指定 URL。

        参数：
            url: 要导航到的 URL

        返回：
            包含页面快照或错误的字典
        """
        result = handle_function_call(
            "browser_navigate", {"url": url}, task_id=self.task_id
        )
        try:
            return json.loads(result)
        except json.JSONDecodeError:
            return {"error": result}

    def browser_snapshot(self) -> Dict[str, Any]:
        """
        对当前浏览器页面进行快照。

        返回：
            包含页面内容/无障碍快照的字典
        """
        result = handle_function_call(
            "browser_snapshot", {}, task_id=self.task_id
        )
        try:
            return json.loads(result)
        except json.JSONDecodeError:
            return {"error": result}

    # -------------------------------------------------------------------------
    # 通用工具访问
    # -------------------------------------------------------------------------

    def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> str:
        """
        按名称调用任何 hermes-agent 工具。

        这是通用的应急接口 -- 如果某个工具上面没有便捷包装方法，
        可以在这里直接调用。

        参数：
            tool_name: 工具名称（例如 "vision_analyze"、"skills_list"）
            arguments: 工具的参数字典

        返回：
            工具返回的原始 JSON 字符串结果
        """
        return _run_tool_in_thread(tool_name, arguments, self.task_id)

    # -------------------------------------------------------------------------
    # 清理
    # -------------------------------------------------------------------------

    def cleanup(self):
        """
        释放此 rollout 的所有资源（终端虚拟机、浏览器会话、后台进程）。

        在 compute_reward() 完成后由基础环境通过 try/finally 自动调用。
        通常不需要手动调用此方法。
        """
        # 终止此 rollout 的所有后台进程（安全网）
        try:
            from tools.process_registry import process_registry
            killed = process_registry.kill_all(task_id=self.task_id)
            if killed:
                logger.debug("Process cleanup for task %s: killed %d process(es)", self.task_id, killed)
        except Exception as e:
            logger.debug("Process cleanup for task %s: %s", self.task_id, e)

        try:
            cleanup_vm(self.task_id)
        except Exception as e:
            logger.debug("VM cleanup for task %s: %s", self.task_id, e)

        # 在清理期间抑制 browser_tool 的嘈杂调试输出。
        # 清理仍然会运行（安全的），只是不会在控制台刷屏。
        _prev_quiet = os.environ.get("HERMES_QUIET")
        os.environ["HERMES_QUIET"] = "1"
        try:
            cleanup_browser(self.task_id)
        except Exception as e:
            logger.debug("Browser cleanup for task %s: %s", self.task_id, e)
        finally:
            if _prev_quiet is None:
                os.environ.pop("HERMES_QUIET", None)
            else:
                os.environ["HERMES_QUIET"] = _prev_quiet
