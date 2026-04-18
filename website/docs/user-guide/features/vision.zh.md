---
title: 视觉与图片粘贴
description: 将剪贴板中的图片粘贴到 Hermes CLI 中进行多模态视觉分析。
sidebar_label: 视觉与图片粘贴
sidebar_position: 7
---

# 视觉与图片粘贴

Hermes Agent 支持**多模态视觉** -- 你可以将剪贴板中的图片直接粘贴到 CLI 中，要求智能体分析、描述或处理它们。图片以 base64 编码的内容块形式发送到模型，因此任何支持视觉的模型都能处理它们。

## 工作原理

1. 将图片复制到剪贴板（截图、浏览器图片等）
2. 使用以下方法之一进行附加
3. 输入你的问题并按回车
4. 图片以 `[📎 Image #1]` 徽章的形式显示在输入上方
5. 提交时，图片作为视觉内容块发送到模型

你可以在发送前附加多张图片 -- 每张都有自己的徽章。按 `Ctrl+C` 清除所有已附加的图片。

图片以带时间戳的文件名保存为 PNG 格式，存储在 `~/.hermes/images/` 目录下。

## 粘贴方法

如何附加图片取决于你的终端环境。并非所有方法在所有环境中都可用 -- 以下是完整说明：

### `/paste` 命令

**最可靠的方法。在所有环境中都能使用。**

```
/paste
```

输入 `/paste` 并按回车。Hermes 会检查剪贴板中是否有图片并进行附加。这在所有环境中都有效，因为它显式调用剪贴板后端 -- 无需担心终端键绑定拦截问题。

### Ctrl+V / Cmd+V（括号粘贴）

当你粘贴剪贴板上与图片同时存在的文本时，Hermes 也会自动检查是否有图片。这在以下情况下有效：
- 你的剪贴板同时包含**文本和图片**（某些应用在复制时会同时放入两者）
- 你的终端支持括号粘贴（大多数现代终端都支持）

:::warning
如果你的剪贴板**只有图片**（没有文本），Ctrl+V 在大多数终端中不会执行任何操作。终端只能粘贴文本 -- 没有标准机制来粘贴二进制图片数据。请改用 `/paste` 或 Alt+V。
:::

### Alt+V

Alt 键组合可以通过大多数终端模拟器（它们以 ESC + 键的形式发送，而不是被拦截）。按 `Alt+V` 检查剪贴板中的图片。

:::caution
**在 VSCode 的集成终端中不可用。** VSCode 会拦截许多 Alt+键组合用于自身 UI。请改用 `/paste`。
:::

### Ctrl+V（原始 -- 仅限 Linux）

在 Linux 桌面终端（GNOME Terminal、Konsole、Alacritty 等）中，`Ctrl+V` **不是**粘贴快捷键 -- `Ctrl+Shift+V` 才是。因此 `Ctrl+V` 会向应用发送一个原始字节，Hermes 会捕获它来检查剪贴板。这仅在具有 X11 或 Wayland 剪贴板访问权限的 Linux 桌面终端上有效。

## 平台兼容性

| 环境 | `/paste` | Ctrl+V 文本+图片 | Alt+V | 备注 |
|------|:--------:|:----------------:|:-----:|------|
| **macOS Terminal / iTerm2** | ✅ | ✅ | ✅ | 最佳体验 -- `osascript` 始终可用 |
| **Linux X11 桌面** | ✅ | ✅ | ✅ | 需要 `xclip`（`apt install xclip`） |
| **Linux Wayland 桌面** | ✅ | ✅ | ✅ | 需要 `wl-paste`（`apt install wl-clipboard`） |
| **WSL2 (Windows Terminal)** | ✅ | ✅¹ | ✅ | 使用 `powershell.exe` -- 无需额外安装 |
| **VSCode 终端（本地）** | ✅ | ✅¹ | ❌ | VSCode 拦截 Alt+键 |
| **VSCode 终端（SSH）** | ❌² | ❌² | ❌ | 远程剪贴板不可访问 |
| **SSH 终端（任意）** | ❌² | ❌² | ❌² | 远程剪贴板不可访问 |

¹ 仅在剪贴板同时包含文本和图片时有效（仅图片的剪贴板 = 无反应）
² 参见下方 [SSH 与远程会话](#ssh--远程会话)

## 特定平台设置

### macOS

**无需设置。** Hermes 使用 `osascript`（macOS 内置）读取剪贴板。为获得更快的性能，可以选择安装 `pngpaste`：

```bash
brew install pngpaste
```

### Linux (X11)

安装 `xclip`：

```bash
# Ubuntu/Debian
sudo apt install xclip

# Fedora
sudo dnf install xclip

# Arch
sudo pacman -S xclip
```

### Linux (Wayland)

现代 Linux 桌面（Ubuntu 22.04+、Fedora 34+）通常默认使用 Wayland。安装 `wl-clipboard`：

```bash
# Ubuntu/Debian
sudo apt install wl-clipboard

# Fedora
sudo dnf install wl-clipboard

# Arch
sudo pacman -S wl-clipboard
```

:::tip 如何检查你是否在 Wayland 上
```bash
echo $XDG_SESSION_TYPE
# "wayland" = Wayland, "x11" = X11, "tty" = 无显示服务器
```
:::

### WSL2

**无需额外设置。** Hermes 自动检测 WSL2（通过 `/proc/version`）并使用 `powershell.exe` 通过 .NET 的 `System.Windows.Forms.Clipboard` 访问 Windows 剪贴板。这是 WSL2 的 Windows 互操作内置功能 -- `powershell.exe` 默认可用。

剪贴板数据以 base64 编码的 PNG 格式通过 stdout 传输，因此不需要文件路径转换或临时文件。

:::info WSLg 说明
如果你运行的是 WSLg（带 GUI 支持的 WSL2），Hermes 先尝试 PowerShell 路径，然后回退到 `wl-paste`。WSLg 的剪贴板桥接仅支持 BMP 格式的图片 -- Hermes 使用 Pillow（如已安装）或 ImageMagick 的 `convert` 命令自动将 BMP 转换为 PNG。
:::

#### 验证 WSL2 剪贴板访问

```bash
# 1. 检查 WSL 检测
grep -i microsoft /proc/version

# 2. 检查 PowerShell 是否可访问
which powershell.exe

# 3. 复制一张图片，然后检查
powershell.exe -NoProfile -Command "Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.Clipboard]::ContainsImage()"
# 应该输出 "True"
```

## SSH 与远程会话

**剪贴板粘贴在 SSH 上不可用。** 当你 SSH 到远程机器时，Hermes CLI 在远程主机上运行。所有剪贴板工具（`xclip`、`wl-paste`、`powershell.exe`、`osascript`）读取的是它们运行所在机器的剪贴板 -- 即远程服务器，而不是你的本地机器。你的本地剪贴板从远程端无法访问。

### SSH 的替代方案

1. **上传图片文件** -- 将图片保存到本地，通过 `scp`、VSCode 的文件资源管理器（拖放）或任何文件传输方式上传到远程服务器。然后通过路径引用它。*（计划在未来版本中推出 `/attach <filepath>` 命令。）*

2. **使用 URL** -- 如果图片可在线访问，只需在消息中粘贴 URL。智能体可以使用 `vision_analyze` 直接查看任何图片 URL。

3. **X11 转发** -- 使用 `ssh -X` 连接以转发 X11。这允许远程机器上的 `xclip` 访问你的本地 X11 剪贴板。需要在本地运行 X 服务器（macOS 上的 XQuartz，Linux X11 桌面上内置）。大图片会较慢。

4. **使用消息平台** -- 通过 Telegram、Discord、Slack 或 WhatsApp 向 Hermes 发送图片。这些平台原生处理图片上传，不受剪贴板/终端限制的影响。

## 为什么终端不能粘贴图片

这是一个常见的困惑来源，以下是技术解释：

终端是**基于文本的**界面。当你按下 Ctrl+V（或 Cmd+V）时，终端模拟器会：

1. 从剪贴板读取**文本内容**
2. 将其包装在[括号粘贴](https://en.wikipedia.org/wiki/Bracketed-paste)转义序列中
3. 通过终端的文本流发送到应用程序

如果剪贴板只包含图片（没有文本），终端就没有内容可以发送。没有标准的终端转义序列用于二进制图片数据。终端什么也不做。

这就是为什么 Hermes 使用单独的剪贴板检查 -- 它不是通过终端粘贴事件接收图片数据，而是通过子进程直接调用操作系统级工具（`osascript`、`powershell.exe`、`xclip`、`wl-paste`）来独立读取剪贴板。

## 支持的模型

图片粘贴适用于任何支持视觉的模型。图片以 base64 编码的数据 URL 以 OpenAI 视觉内容格式发送：

```json
{
  "type": "image_url",
  "image_url": {
    "url": "data:image/png;base64,..."
  }
}
```

大多数现代模型都支持此格式，包括 GPT-4 Vision、Claude（支持视觉）、Gemini 以及通过 OpenRouter 提供的开源多模态模型。
