---
sidebar_position: 3
title: "Android / Termux"
description: "通过 Termux 在 Android 手机上直接运行 Hermes Agent"
---

# 在 Android 上通过 Termux 运行 Hermes

这是通过 [Termux](https://termux.dev/) 在 Android 手机上直接运行 Hermes Agent 的经过测试的路径。

它为你提供手机上可用的本地 CLI，以及目前已知在 Android 上可以顺利安装的核心额外依赖。

## 经过测试的路径支持什么？

经过测试的 Termux 套件安装了：
- Hermes CLI
- 定时任务支持
- PTY/后台终端支持
- Telegram 网关支持（手动 / 尽力后台运行）
- MCP 支持
- Honcho 记忆支持
- ACP 支持

具体来说，它对应于：

```bash
python -m pip install -e '.[termux]' -c constraints-termux.txt
```

## 经过测试的路径尚不包括什么？

一些功能仍然需要桌面/服务器类型的依赖，这些依赖尚未发布 Android 版本，或者尚未在手机上验证：

- `.[all]` 目前在 Android 上不受支持
- `voice` 额外依赖被 `faster-whisper -> ctranslate2` 阻塞，而 `ctranslate2` 未发布 Android wheel
- 自动浏览器 / Playwright 引导在 Termux 安装程序中被跳过
- Docker 终端隔离在 Termux 内不可用
- Android 可能仍会暂停 Termux 后台任务，因此网关持久性是尽力而为，而非正常的托管服务

这并不影响 Hermes 作为手机原生 CLI 智能体的良好运行——只是意味着推荐的移动安装有意比桌面/服务器安装范围更窄。

---

## 选项 1：一行命令安装

Hermes 现已提供 Termux 感知的安装路径：

```bash
curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash
```

在 Termux 上，安装程序会自动：
- 使用 `pkg` 安装系统包
- 使用 `python -m venv` 创建虚拟环境
- 使用 `pip` 安装 `.[termux]`
- 将 `hermes` 链接到 `$PREFIX/bin` 使其保持在你的 Termux PATH 上
- 跳过未经测试的浏览器 / WhatsApp 引导

如果你想要显式的命令或需要调试失败的安装，请使用以下手动路径。

---

## 选项 2：手动安装（完全显式）

### 1. 更新 Termux 并安装系统包

```bash
pkg update
pkg install -y git python clang rust make pkg-config libffi openssl nodejs ripgrep ffmpeg
```

为什么需要这些包？
- `python` —— 运行时 + 虚拟环境支持
- `git` —— 克隆/更新仓库
- `clang`、`rust`、`make`、`pkg-config`、`libffi`、`openssl` —— 在 Android 上构建部分 Python 依赖时需要
- `nodejs` —— 可选的 Node 运行时，用于超出经测试核心路径的实验
- `ripgrep` —— 快速文件搜索
- `ffmpeg` —— 媒体 / TTS 转换

### 2. 克隆 Hermes

```bash
git clone --recurse-submodules https://github.com/NousResearch/hermes-agent.git
cd hermes-agent
```

如果你已经在没有子模块的情况下克隆了：

```bash
git submodule update --init --recursive
```

### 3. 创建虚拟环境

```bash
python -m venv venv
source venv/bin/activate
export ANDROID_API_LEVEL="$(getprop ro.build.version.sdk)"
python -m pip install --upgrade pip setuptools wheel
```

`ANDROID_API_LEVEL` 对于基于 Rust / maturin 的包（如 `jiter`）很重要。

### 4. 安装经过测试的 Termux 套件

```bash
python -m pip install -e '.[termux]' -c constraints-termux.txt
```

如果你只想要最小化的核心智能体，这也可以：

```bash
python -m pip install -e '.' -c constraints-termux.txt
```

### 5. 将 `hermes` 放到你的 Termux PATH 上

```bash
ln -sf "$PWD/venv/bin/hermes" "$PREFIX/bin/hermes"
```

`$PREFIX/bin` 在 Termux 中已经在 PATH 上，因此这使得 `hermes` 命令在新 shell 中持久存在，无需每次都重新激活虚拟环境。

### 6. 验证安装

```bash
hermes version
hermes doctor
```

### 7. 启动 Hermes

```bash
hermes
```

---

## 推荐的后续设置

### 配置模型

```bash
hermes model
```

或直接在 `~/.hermes/.env` 中设置密钥。

### 稍后重新运行完整的交互式设置向导

```bash
hermes setup
```

### 手动安装可选的 Node 依赖

经过测试的 Termux 路径有意跳过 Node/浏览器引导。如果你想稍后试验浏览器工具：

```bash
pkg install nodejs-lts
npm install
```

浏览器工具会自动在其 PATH 搜索中包含 Termux 目录（`/data/data/com.termux/files/usr/bin`），因此 `agent-browser` 和 `npx` 无需任何额外 PATH 配置即可被发现。

在另有文档说明之前，请将 Android 上的浏览器 / WhatsApp 工具视为实验性功能。

---

## 故障排除

### 安装 `.[all]` 时出现 `No solution found`

改用经过测试的 Termux 套件：

```bash
python -m pip install -e '.[termux]' -c constraints-termux.txt
```

当前的阻塞因素是 `voice` 额外依赖：
- `voice` 引入了 `faster-whisper`
- `faster-whisper` 依赖 `ctranslate2`
- `ctranslate2` 未发布 Android wheel

### `uv pip install` 在 Android 上失败

使用标准库虚拟环境 + `pip` 的 Termux 路径替代：

```bash
python -m venv venv
source venv/bin/activate
export ANDROID_API_LEVEL="$(getprop ro.build.version.sdk)"
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e '.[termux]' -c constraints-termux.txt
```

### `jiter` / `maturin` 抱怨 `ANDROID_API_LEVEL`

在安装前显式设置 API 级别：

```bash
export ANDROID_API_LEVEL="$(getprop ro.build.version.sdk)"
python -m pip install -e '.[termux]' -c constraints-termux.txt
```

### `hermes doctor` 说 ripgrep 或 Node 缺失

使用 Termux 包安装它们：

```bash
pkg install ripgrep nodejs
```

### 安装 Python 包时构建失败

确保构建工具链已安装：

```bash
pkg install clang rust make pkg-config libffi openssl
```

然后重试：

```bash
python -m pip install -e '.[termux]' -c constraints-termux.txt
```

---

## 手机上的已知限制

- Docker 后端不可用
- 在经过测试的路径中，通过 `faster-whisper` 的本地语音转写不可用
- 安装程序有意跳过浏览器自动化设置
- 一些可选额外依赖可能可以工作，但目前只有 `.[termux]` 被记录为经过测试的 Android 套件

如果你遇到新的 Android 特定问题，请在 GitHub 上提交 issue，附上：
- 你的 Android 版本
- `termux-info`
- `python --version`
- `hermes doctor`
- 精确的安装命令和完整错误输出
