"""``hermes debug`` —— Hermes Agent 的调试工具。

目前支持:
    hermes debug share    上传调试报告（系统信息 + 日志）到
                          粘贴服务并打印可分享的 URL。
"""

import io
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

from hermes_constants import get_hermes_home


# ---------------------------------------------------------------------------
# 粘贴服务 — 优先尝试 paste.rs，dpaste.com 作为回退。
# ---------------------------------------------------------------------------

_PASTE_RS_URL = "https://paste.rs/"
_DPASTE_COM_URL = "https://dpaste.com/api/"

# 单个日志文件上传时读取的最大字节数。
# paste.rs 上限约 1 MB；我们留有余量保持在此之下。
_MAX_LOG_BYTES = 512_000

# 粘贴在此秒数后自动删除（6 小时）。
_AUTO_DELETE_SECONDS = 21600


# ---------------------------------------------------------------------------
# 隐私 / 删除辅助函数
# ---------------------------------------------------------------------------

_PRIVACY_NOTICE = """\
⚠️  This will upload the following to a public paste service:
  • System info (OS, Python version, Hermes version, provider, which API keys
    are configured — NOT the actual keys)
  • Recent log lines (agent.log, errors.log, gateway.log — may contain
    conversation fragments and file paths)
  • Full agent.log and gateway.log (up to 512 KB each — likely contains
    conversation content, tool outputs, and file paths)

Pastes auto-delete after 6 hours.
"""

_GATEWAY_PRIVACY_NOTICE = (
    "⚠️ **Privacy notice:** This uploads system info + recent log tails "
    "(may contain conversation fragments) to a public paste service. "
    "Full logs are NOT included from the gateway — use `hermes debug share` "
    "from the CLI for full log uploads.\n"
    "Pastes auto-delete after 6 hours."
)


def _extract_paste_id(url: str) -> Optional[str]:
    """从 paste.rs 或 dpaste.com URL 中提取粘贴 ID。

    返回 ID 字符串，若 URL 不匹配已知服务则返回 None。
    """
    url = url.strip().rstrip("/")
    for prefix in ("https://paste.rs/", "http://paste.rs/"):
        if url.startswith(prefix):
            return url[len(prefix):]
    return None


def delete_paste(url: str) -> bool:
    """从 paste.rs 删除粘贴。成功时返回 True。

    仅 paste.rs 支持未认证的 DELETE。dpaste.com 的粘贴
    会自动过期但无法通过 API 删除。
    """
    paste_id = _extract_paste_id(url)
    if not paste_id:
        raise ValueError(
            f"Cannot delete: only paste.rs URLs are supported.  Got: {url}"
        )

    target = f"{_PASTE_RS_URL}{paste_id}"
    req = urllib.request.Request(
        target, method="DELETE",
        headers={"User-Agent": "hermes-agent/debug-share"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return 200 <= resp.status < 300


def _schedule_auto_delete(urls: list[str], delay_seconds: int = _AUTO_DELETE_SECONDS):
    """启动一个分离进程，在 *delay_seconds* 秒后删除 paste.rs 粘贴。

    子进程完全分离（``start_new_session=True``），因此在父进程
    退出后仍然存活（对 CLI 模式很重要）。仅尝试 paste.rs URL ——
    dpaste.com 的粘贴会自行过期。
    """
    import subprocess

    paste_rs_urls = [u for u in urls if _extract_paste_id(u)]
    if not paste_rs_urls:
        return

    # 构建一个小型内联 Python 脚本。仅需标准库导入。
    url_list = ", ".join(f'"{u}"' for u in paste_rs_urls)
    script = (
        "import time, urllib.request; "
        f"time.sleep({delay_seconds}); "
        f"[urllib.request.urlopen(urllib.request.Request(u, method='DELETE', "
        f"headers={{'User-Agent': 'hermes-agent/auto-delete'}}), timeout=15) "
        f"for u in [{url_list}]]"
    )

    try:
        subprocess.Popen(
            [sys.executable, "-c", script],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass  # 尽力而为；手动删除仍然可用。


def _delete_hint(url: str) -> str:
    """返回给定粘贴 URL 的一行删除命令。"""
    paste_id = _extract_paste_id(url)
    if paste_id:
        return f"hermes debug delete {url}"
    # dpaste.com — 无 API 删除，按其策略自动过期。
    return "(auto-expires per dpaste.com policy)"


def _upload_paste_rs(content: str) -> str:
    """上传到 paste.rs。返回粘贴 URL。

    paste.rs 接受纯文本 POST 正文并直接返回 URL。
    """
    data = content.encode("utf-8")
    req = urllib.request.Request(
        _PASTE_RS_URL, data=data, method="POST",
        headers={
            "Content-Type": "text/plain; charset=utf-8",
            "User-Agent": "hermes-agent/debug-share",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        url = resp.read().decode("utf-8").strip()
    if not url.startswith("http"):
        raise ValueError(f"Unexpected response from paste.rs: {url[:200]}")
    return url


def _upload_dpaste_com(content: str, expiry_days: int = 7) -> str:
    """上传到 dpaste.com。返回粘贴 URL。

    dpaste.com 使用 multipart 表单数据。
    """
    boundary = "----HermesDebugBoundary9f3c"

    def _field(name: str, value: str) -> str:
        return (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n'
            f"\r\n"
            f"{value}\r\n"
        )

    body = (
        _field("content", content)
        + _field("syntax", "text")
        + _field("expiry_days", str(expiry_days))
        + f"--{boundary}--\r\n"
    ).encode("utf-8")

    req = urllib.request.Request(
        _DPASTE_COM_URL, data=body, method="POST",
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": "hermes-agent/debug-share",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        url = resp.read().decode("utf-8").strip()
    if not url.startswith("http"):
        raise ValueError(f"Unexpected response from dpaste.com: {url[:200]}")
    return url


def upload_to_pastebin(content: str, expiry_days: int = 7) -> str:
    """将 *content* 上传到粘贴服务，先尝试 paste.rs 再尝试 dpaste.com。

    成功时返回粘贴 URL，全部失败时抛出异常。
    """
    errors: list[str] = []

    # 先尝试 paste.rs（简单、快速）
    try:
        return _upload_paste_rs(content)
    except Exception as exc:
        errors.append(f"paste.rs: {exc}")

    # 回退：dpaste.com（支持过期时间）
    try:
        return _upload_dpaste_com(content, expiry_days=expiry_days)
    except Exception as exc:
        errors.append(f"dpaste.com: {exc}")

    raise RuntimeError(
        "Failed to upload to any paste service:\n  " + "\n  ".join(errors)
    )


# ---------------------------------------------------------------------------
# 日志文件读取
# ---------------------------------------------------------------------------

def _resolve_log_path(log_name: str) -> Optional[Path]:
    """查找 *log_name* 的日志文件，回退到 .1 轮转文件。

    找到时返回路径，否则返回 None。
    """
    from hermes_cli.logs import LOG_FILES

    filename = LOG_FILES.get(log_name)
    if not filename:
        return None

    log_dir = get_hermes_home() / "logs"
    primary = log_dir / filename
    if primary.exists() and primary.stat().st_size > 0:
        return primary

    # 回退到最近的轮转文件（.1）。
    rotated = log_dir / f"{filename}.1"
    if rotated.exists() and rotated.stat().st_size > 0:
        return rotated

    return None


def _read_log_tail(log_name: str, num_lines: int) -> str:
    """读取日志文件的最后 *num_lines* 行，或返回占位符。"""
    from hermes_cli.logs import _read_last_n_lines

    log_path = _resolve_log_path(log_name)
    if log_path is None:
        return "(file not found)"

    try:
        lines = _read_last_n_lines(log_path, num_lines)
        return "".join(lines).rstrip("\n")
    except Exception as exc:
        return f"(error reading: {exc})"


def _read_full_log(log_name: str, max_bytes: int = _MAX_LOG_BYTES) -> Optional[str]:
    """读取日志文件用于独立上传。

    返回文件内容（若截断则为最后 *max_bytes* 字节），
    若文件不存在或为空则返回 None。
    """
    log_path = _resolve_log_path(log_name)
    if log_path is None:
        return None

    try:
        size = log_path.stat().st_size
        if size == 0:
            return None

        if size <= max_bytes:
            return log_path.read_text(encoding="utf-8", errors="replace")

        # 文件大于 max_bytes — 读取尾部。
        with open(log_path, "rb") as f:
            f.seek(size - max_bytes)
            # 跳过定位点处的不完整行。
            f.readline()
            content = f.read().decode("utf-8", errors="replace")
        return f"[... truncated — showing last ~{max_bytes // 1024}KB ...]\n{content}"
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 调试报告收集
# ---------------------------------------------------------------------------

def _capture_dump() -> str:
    """运行 ``hermes dump`` 并将其标准输出作为字符串返回。"""
    from hermes_cli.dump import run_dump

    class _FakeArgs:
        show_keys = False

    old_stdout = sys.stdout
    sys.stdout = capture = io.StringIO()
    try:
        run_dump(_FakeArgs())
    except SystemExit:
        pass
    finally:
        sys.stdout = old_stdout

    return capture.getvalue()


def collect_debug_report(*, log_lines: int = 200, dump_text: str = "") -> str:
    """构建摘要调试报告：系统转储 + 日志尾部。

    参数
    ----------
    log_lines
        每个日志文件包含的最近行数。
    dump_text
        预先捕获的转储输出。若为空，则内部运行 ``hermes dump``。

    返回可直接上传的纯文本报告字符串。
    """
    buf = io.StringIO()

    if not dump_text:
        dump_text = _capture_dump()
    buf.write(dump_text)

    # ── 最近的日志尾部（仅摘要）──────────────────────────────────
    buf.write("\n\n")
    buf.write(f"--- agent.log (last {log_lines} lines) ---\n")
    buf.write(_read_log_tail("agent", log_lines))
    buf.write("\n\n")

    errors_lines = min(log_lines, 100)
    buf.write(f"--- errors.log (last {errors_lines} lines) ---\n")
    buf.write(_read_log_tail("errors", errors_lines))
    buf.write("\n\n")

    buf.write(f"--- gateway.log (last {errors_lines} lines) ---\n")
    buf.write(_read_log_tail("gateway", errors_lines))
    buf.write("\n")

    return buf.getvalue()


# ---------------------------------------------------------------------------
# CLI 入口点
# ---------------------------------------------------------------------------

def run_debug_share(args):
    """收集调试报告 + 完整日志，逐个上传，打印 URL。"""
    log_lines = getattr(args, "lines", 200)
    expiry = getattr(args, "expire", 7)
    local_only = getattr(args, "local", False)

    if not local_only:
        print(_PRIVACY_NOTICE)

    print("Collecting debug report...")

    # Capture dump once — prepended to every paste for context.
    dump_text = _capture_dump()

    report = collect_debug_report(log_lines=log_lines, dump_text=dump_text)
    agent_log = _read_full_log("agent")
    gateway_log = _read_full_log("gateway")

    # Prepend dump header to each full log so every paste is self-contained.
    if agent_log:
        agent_log = dump_text + "\n\n--- full agent.log ---\n" + agent_log
    if gateway_log:
        gateway_log = dump_text + "\n\n--- full gateway.log ---\n" + gateway_log

    if local_only:
        print(report)
        if agent_log:
            print(f"\n\n{'=' * 60}")
            print("FULL agent.log")
            print(f"{'=' * 60}\n")
            print(agent_log)
        if gateway_log:
            print(f"\n\n{'=' * 60}")
            print("FULL gateway.log")
            print(f"{'=' * 60}\n")
            print(gateway_log)
        return

    print("Uploading...")
    urls: dict[str, str] = {}
    failures: list[str] = []

    # 1. Summary report (required)
    try:
        urls["Report"] = upload_to_pastebin(report, expiry_days=expiry)
    except RuntimeError as exc:
        print(f"\nUpload failed: {exc}", file=sys.stderr)
        print("\nFull report printed below — copy-paste it manually:\n")
        print(report)
        sys.exit(1)

    # 2. Full agent.log (optional)
    if agent_log:
        try:
            urls["agent.log"] = upload_to_pastebin(agent_log, expiry_days=expiry)
        except Exception as exc:
            failures.append(f"agent.log: {exc}")

    # 3. Full gateway.log (optional)
    if gateway_log:
        try:
            urls["gateway.log"] = upload_to_pastebin(gateway_log, expiry_days=expiry)
        except Exception as exc:
            failures.append(f"gateway.log: {exc}")

    # Print results
    label_width = max(len(k) for k in urls)
    print(f"\nDebug report uploaded:")
    for label, url in urls.items():
        print(f"  {label:<{label_width}}  {url}")

    if failures:
        print(f"\n  (failed to upload: {', '.join(failures)})")

    # Schedule auto-deletion after 6 hours
    _schedule_auto_delete(list(urls.values()))
    print(f"\n⏱  Pastes will auto-delete in 6 hours.")

    # Manual delete fallback
    print(f"To delete now:  hermes debug delete <url>")

    print(f"\nShare these links with the Hermes team for support.")


def run_debug_delete(args):
    """Delete one or more paste URLs uploaded by /debug."""
    urls = getattr(args, "urls", [])
    if not urls:
        print("Usage: hermes debug delete <url> [<url> ...]")
        print("  Deletes paste.rs pastes uploaded by 'hermes debug share'.")
        return

    for url in urls:
        try:
            ok = delete_paste(url)
            if ok:
                print(f"  ✓ Deleted: {url}")
            else:
                print(f"  ✗ Failed to delete: {url} (unexpected response)")
        except ValueError as exc:
            print(f"  ✗ {exc}")
        except Exception as exc:
            print(f"  ✗ Could not delete {url}: {exc}")


def run_debug(args):
    """Route debug subcommands."""
    subcmd = getattr(args, "debug_command", None)
    if subcmd == "share":
        run_debug_share(args)
    elif subcmd == "delete":
        run_debug_delete(args)
    else:
        # Default: show help
        print("Usage: hermes debug <command>")
        print()
        print("Commands:")
        print("  share    Upload debug report to a paste service and print URL")
        print("  delete   Delete a previously uploaded paste")
        print()
        print("Options (share):")
        print("  --lines N    Number of log lines to include (default: 200)")
        print("  --expire N   Paste expiry in days (default: 7)")
        print("  --local      Print report locally instead of uploading")
        print()
        print("Options (delete):")
        print("  <url> ...    One or more paste URLs to delete")
