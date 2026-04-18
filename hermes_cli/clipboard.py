"""macOS、Windows、Linux 和 WSL2 的剪贴板图片提取。

提供单一函数 `save_clipboard_image(dest)`，检查系统剪贴板中的
图片数据，将其保存为 PNG 到 *dest*，成功时返回 True。
无需额外 Python 依赖 —— 仅使用操作系统自带的 CLI 工具
（或常见安装的工具）。

平台支持:
  macOS   — osascript（始终可用），pngpaste（如已安装）
  Windows — 通过 .NET System.Windows.Forms.Clipboard 的 PowerShell
  WSL2    — 通过 .NET System.Windows.Forms.Clipboard 的 powershell.exe
  Linux   — wl-paste（Wayland），xclip（X11）
"""

import base64
import logging
import os
import subprocess
import sys
from pathlib import Path

from hermes_constants import is_wsl as _is_wsl

logger = logging.getLogger(__name__)


def save_clipboard_image(dest: Path) -> bool:
    """从系统剪贴板提取图片并保存为 PNG。

    如果找到并保存了图片则返回 True，否则返回 False。
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    if sys.platform == "darwin":
        return _macos_save(dest)
    if sys.platform == "win32":
        return _windows_save(dest)
    return _linux_save(dest)


def has_clipboard_image() -> bool:
    """快速检查：剪贴板当前是否包含图片？

    比 save_clipboard_image 更轻量 —— 不提取也不写入任何内容。
    """
    if sys.platform == "darwin":
        return _macos_has_image()
    if sys.platform == "win32":
        return _windows_has_image()
    if _is_wsl():
        return _wsl_has_image()
    if os.environ.get("WAYLAND_DISPLAY"):
        return _wayland_has_image()
    return _xclip_has_image()


# ── macOS ────────────────────────────────────────────────────────────────

def _macos_save(dest: Path) -> bool:
    """优先使用 pngpaste（更快，支持更多格式），失败时回退到 osascript。"""
    return _macos_pngpaste(dest) or _macos_osascript(dest)


def _macos_has_image() -> bool:
    """检查 macOS 剪贴板是否包含图片数据。"""
    try:
        info = subprocess.run(
            ["osascript", "-e", "clipboard info"],
            capture_output=True, text=True, timeout=3,
        )
        return "«class PNGf»" in info.stdout or "«class TIFF»" in info.stdout
    except Exception:
        return False


def _macos_pngpaste(dest: Path) -> bool:
    """使用 pngpaste（brew install pngpaste）—— 最快、最干净。"""
    try:
        r = subprocess.run(
            ["pngpaste", str(dest)],
            capture_output=True, timeout=3,
        )
        if r.returncode == 0 and dest.exists() and dest.stat().st_size > 0:
            return True
    except FileNotFoundError:
        pass  # pngpaste 未安装
    except Exception as e:
        logger.debug("pngpaste failed: %s", e)
    return False


def _macos_osascript(dest: Path) -> bool:
    """使用 osascript 从剪贴板提取 PNG 数据（始终可用）。"""
    if not _macos_has_image():
        return False

    # 提取为 PNG
    script = (
        'try\n'
        '  set imgData to the clipboard as «class PNGf»\n'
        f'  set f to open for access POSIX file "{dest}" with write permission\n'
        '  write imgData to f\n'
        '  close access f\n'
        'on error\n'
        '  return "fail"\n'
        'end try\n'
    )
    try:
        r = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0 and "fail" not in r.stdout and dest.exists() and dest.stat().st_size > 0:
            return True
    except Exception as e:
        logger.debug("osascript clipboard extract failed: %s", e)
    return False


# ── 共享 PowerShell 脚本（原生 Windows + WSL2）─────────────────────

# .NET System.Windows.Forms.Clipboard —— 原生 Windows（powershell）
# 和 WSL2（powershell.exe）路径共用。
_PS_CHECK_IMAGE = (
    "Add-Type -AssemblyName System.Windows.Forms;"
    "[System.Windows.Forms.Clipboard]::ContainsImage()"
)

_PS_EXTRACT_IMAGE = (
    "Add-Type -AssemblyName System.Windows.Forms;"
    "Add-Type -AssemblyName System.Drawing;"
    "$img = [System.Windows.Forms.Clipboard]::GetImage();"
    "if ($null -eq $img) { exit 1 }"
    "$ms = New-Object System.IO.MemoryStream;"
    "$img.Save($ms, [System.Drawing.Imaging.ImageFormat]::Png);"
    "[System.Convert]::ToBase64String($ms.ToArray())"
)


# ── 原生 Windows ────────────────────────────────────────────────────────

# 原生 Windows 使用 ``powershell``（Windows PowerShell 5.1，始终存在）
# 或 ``pwsh``（PowerShell 7+，可选）。发现结果按进程缓存。


def _find_powershell() -> str | None:
    """返回第一个可用的 PowerShell 可执行文件，若无则返回 None。"""
    for name in ("powershell", "pwsh"):
        try:
            r = subprocess.run(
                [name, "-NoProfile", "-NonInteractive", "-Command", "echo ok"],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0 and "ok" in r.stdout:
                return name
        except FileNotFoundError:
            continue
        except Exception:
            continue
    return None


# 缓存已解析的 PowerShell 可执行文件（每进程仅检查一次）
_ps_exe: str | None | bool = False  # False = 尚未检查


def _get_ps_exe() -> str | None:
    global _ps_exe
    if _ps_exe is False:
        _ps_exe = _find_powershell()
    return _ps_exe


def _windows_has_image() -> bool:
    """检查 Windows 剪贴板是否包含图片。"""
    ps = _get_ps_exe()
    if ps is None:
        return False
    try:
        r = subprocess.run(
            [ps, "-NoProfile", "-NonInteractive", "-Command", _PS_CHECK_IMAGE],
            capture_output=True, text=True, timeout=5,
        )
        return r.returncode == 0 and "True" in r.stdout
    except Exception as e:
        logger.debug("Windows clipboard image check failed: %s", e)
    return False


def _windows_save(dest: Path) -> bool:
    """在原生 Windows 上通过 PowerShell -> base64 PNG 提取剪贴板图片。"""
    ps = _get_ps_exe()
    if ps is None:
        logger.debug("No PowerShell found — Windows clipboard image paste unavailable")
        return False
    try:
        r = subprocess.run(
            [ps, "-NoProfile", "-NonInteractive", "-Command", _PS_EXTRACT_IMAGE],
            capture_output=True, text=True, timeout=15,
        )
        if r.returncode != 0:
            return False

        b64_data = r.stdout.strip()
        if not b64_data:
            return False

        png_bytes = base64.b64decode(b64_data)
        dest.write_bytes(png_bytes)
        return dest.exists() and dest.stat().st_size > 0

    except Exception as e:
        logger.debug("Windows clipboard image extraction failed: %s", e)
        dest.unlink(missing_ok=True)
    return False


# ── Linux ────────────────────────────────────────────────────────────────

def _linux_save(dest: Path) -> bool:
    """按优先级尝试剪贴板后端：WSL -> Wayland -> X11。"""
    if _is_wsl():
        if _wsl_save(dest):
            return True
        # 回退 — WSLg 可能有可用的 wl-paste 或 xclip

    if os.environ.get("WAYLAND_DISPLAY"):
        if _wayland_save(dest):
            return True

    return _xclip_save(dest)


# ── WSL2 (powershell.exe) ────────────────────────────────────────────────
# 复用上面定义的 _PS_CHECK_IMAGE / _PS_EXTRACT_IMAGE。

def _wsl_has_image() -> bool:
    """检查 Windows 剪贴板是否有图片（通过 powershell.exe）。"""
    try:
        r = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
             _PS_CHECK_IMAGE],
            capture_output=True, text=True, timeout=8,
        )
        return r.returncode == 0 and "True" in r.stdout
    except FileNotFoundError:
        logger.debug("powershell.exe not found — WSL clipboard unavailable")
    except Exception as e:
        logger.debug("WSL clipboard check failed: %s", e)
    return False


def _wsl_save(dest: Path) -> bool:
    """通过 powershell.exe -> base64 -> 解码为 PNG 提取剪贴板图片。"""
    try:
        r = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
             _PS_EXTRACT_IMAGE],
            capture_output=True, text=True, timeout=15,
        )
        if r.returncode != 0:
            return False

        b64_data = r.stdout.strip()
        if not b64_data:
            return False

        png_bytes = base64.b64decode(b64_data)
        dest.write_bytes(png_bytes)
        return dest.exists() and dest.stat().st_size > 0

    except FileNotFoundError:
        logger.debug("powershell.exe not found — WSL clipboard unavailable")
    except Exception as e:
        logger.debug("WSL clipboard extraction failed: %s", e)
        dest.unlink(missing_ok=True)
    return False


# ── Wayland (wl-paste) ──────────────────────────────────────────────────

def _wayland_has_image() -> bool:
    """检查 Wayland 剪贴板是否有图片内容。"""
    try:
        r = subprocess.run(
            ["wl-paste", "--list-types"],
            capture_output=True, text=True, timeout=3,
        )
        return r.returncode == 0 and any(
            t.startswith("image/") for t in r.stdout.splitlines()
        )
    except FileNotFoundError:
        logger.debug("wl-paste not installed — Wayland clipboard unavailable")
    except Exception:
        pass
    return False


def _wayland_save(dest: Path) -> bool:
    """使用 wl-paste 提取剪贴板图片（Wayland 会话）。"""
    try:
        # 检查可用的 MIME 类型
        types_r = subprocess.run(
            ["wl-paste", "--list-types"],
            capture_output=True, text=True, timeout=3,
        )
        if types_r.returncode != 0:
            return False
        types = types_r.stdout.splitlines()

        # 优先 PNG，回退到其他图片格式
        mime = None
        for preferred in ("image/png", "image/jpeg", "image/bmp",
                          "image/gif", "image/webp"):
            if preferred in types:
                mime = preferred
                break

        if not mime:
            return False

        # 提取图片数据
        with open(dest, "wb") as f:
            subprocess.run(
                ["wl-paste", "--type", mime],
                stdout=f, stderr=subprocess.DEVNULL, timeout=5, check=True,
            )

        if not dest.exists() or dest.stat().st_size == 0:
            dest.unlink(missing_ok=True)
            return False

        # BMP 需要转换为 PNG（在 WSLg 中常见，因为只有 BMP
        # 通过 RDP 从 Windows 剪贴板桥接过来）。
        if mime == "image/bmp":
            return _convert_to_png(dest)

        return True

    except FileNotFoundError:
        logger.debug("wl-paste not installed — Wayland clipboard unavailable")
    except Exception as e:
        logger.debug("wl-paste clipboard extraction failed: %s", e)
        dest.unlink(missing_ok=True)
    return False


def _convert_to_png(path: Path) -> bool:
    """将图片文件原地转换为 PNG（需要 Pillow 或 ImageMagick）。"""
    # 首先尝试 Pillow（可能已安装在虚拟环境中）
    try:
        from PIL import Image
        img = Image.open(path)
        img.save(path, "PNG")
        return True
    except ImportError:
        pass
    except Exception as e:
        logger.debug("Pillow BMP→PNG conversion failed: %s", e)

    # 回退到 ImageMagick convert
    tmp = path.with_suffix(".bmp")
    try:
        path.rename(tmp)
        r = subprocess.run(
            ["convert", str(tmp), "png:" + str(path)],
            capture_output=True, timeout=5,
        )
        if r.returncode == 0 and path.exists() and path.stat().st_size > 0:
            tmp.unlink(missing_ok=True)
            return True
        else:
            # 转换失败 — 恢复原文件
            tmp.rename(path)
    except FileNotFoundError:
        logger.debug("ImageMagick not installed — cannot convert BMP to PNG")
        if tmp.exists() and not path.exists():
            tmp.rename(path)
    except Exception as e:
        logger.debug("ImageMagick BMP→PNG conversion failed: %s", e)
        if tmp.exists() and not path.exists():
            tmp.rename(path)

    # 无法转换 — BMP 对大多数 API 仍然可用
    return path.exists() and path.stat().st_size > 0


# ── X11 (xclip) ─────────────────────────────────────────────────────────

def _xclip_has_image() -> bool:
    """检查 X11 剪贴板是否有图片内容。"""
    try:
        r = subprocess.run(
            ["xclip", "-selection", "clipboard", "-t", "TARGETS", "-o"],
            capture_output=True, text=True, timeout=3,
        )
        return r.returncode == 0 and "image/png" in r.stdout
    except FileNotFoundError:
        pass
    except Exception:
        pass
    return False


def _xclip_save(dest: Path) -> bool:
    """使用 xclip 提取剪贴板图片（X11 会话）。"""
    # 检查剪贴板是否有图片内容
    try:
        targets = subprocess.run(
            ["xclip", "-selection", "clipboard", "-t", "TARGETS", "-o"],
            capture_output=True, text=True, timeout=3,
        )
        if "image/png" not in targets.stdout:
            return False
    except FileNotFoundError:
        logger.debug("xclip not installed — X11 clipboard image paste unavailable")
        return False
    except Exception:
        return False

    # 提取 PNG 数据
    try:
        with open(dest, "wb") as f:
            subprocess.run(
                ["xclip", "-selection", "clipboard", "-t", "image/png", "-o"],
                stdout=f, stderr=subprocess.DEVNULL, timeout=5, check=True,
            )
        if dest.exists() and dest.stat().st_size > 0:
            return True
    except Exception as e:
        logger.debug("xclip image extraction failed: %s", e)
        dest.unlink(missing_ok=True)
    return False
