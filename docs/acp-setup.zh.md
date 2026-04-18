# Hermes Agent — ACP（Agent Client Protocol）设置指南

Hermes Agent 支持 **Agent Client Protocol (ACP)**，允许它作为编码代理在您的编辑器中运行。ACP 使您的 IDE 能够将任务发送给 Hermes，而 Hermes 会以文件编辑、终端命令和解释作为响应——所有内容都在编辑器 UI 中原生显示。

---

## 前置要求

- 已安装并配置 Hermes Agent（已完成 `hermes setup`）
- 已在 `~/.hermes/.env` 中或通过 `hermes login` 设置 API 密钥/提供商
- Python 3.11+

安装 ACP 扩展：

```bash
pip install -e ".[acp]"
```

---

## VS Code 设置

### 1. 安装 ACP Client 扩展

打开 VS Code 并从市场安装 **ACP Client**：

- 按 `Ctrl+Shift+X`（macOS 上为 `Cmd+Shift+X`）
- 搜索 **"ACP Client"**
- 点击 **安装**

或从命令行安装：

```bash
code --install-extension anysphere.acp-client
```

### 2. 配置 settings.json

打开您的 VS Code 设置（`Ctrl+,` → 点击 `{}` 图标切换到 JSON）并添加：

```json
{
  "acpClient.agents": [
    {
      "name": "hermes-agent",
      "registryDir": "/path/to/hermes-agent/acp_registry"
    }
  ]
}
```

将 `/path/to/hermes-agent` 替换为您的 Hermes Agent 实际安装路径（例如 `~/.hermes/hermes-agent`）。

或者，如果 `hermes` 在您的 PATH 中，ACP Client 可以通过注册目录自动发现它。

### 3. 重启 VS Code

配置完成后，重启 VS Code。您应该会在聊天/代理面板的 ACP 代理选择器中看到 **Hermes Agent**。

---

## Zed 设置

Zed 内置了 ACP 支持。

### 1. 配置 Zed 设置

打开 Zed 设置（macOS 上为 `Cmd+,` 或 Linux 上为 `Ctrl+,`）并在您的 `settings.json` 中添加：

```json
{
  "agent_servers": {
    "hermes-agent": {
      "type": "custom",
      "command": "hermes",
      "args": ["acp"],
    },
  },
}
```

### 2. 重启 Zed

Hermes Agent 将出现在代理面板中。选择它并开始对话。

---

## JetBrains 设置（IntelliJ、PyCharm、WebStorm 等）

### 1. 安装 ACP 插件

- 打开 **Settings** → **Plugins** → **Marketplace**
- 搜索 **"ACP"** 或 **"Agent Client Protocol"**
- 安装并重启 IDE

### 2. 配置代理

- 打开 **Settings** → **Tools** → **ACP Agents**
- 点击 **+** 添加新代理
- 将注册目录设置为您的 `acp_registry/` 文件夹：
  `/path/to/hermes-agent/acp_registry`
- 点击 **OK**

### 3. 使用代理

打开 ACP 面板（通常在右侧边栏）并选择 **Hermes Agent**。

---

## 您将看到的内容

连接后，您的编辑器将为 Hermes Agent 提供原生界面：

### 聊天面板
一个对话式界面，您可以在此描述任务、提问并给出指令。Hermes 会以解释和操作作为响应。

### 文件差异
当 Hermes 编辑文件时，您会在编辑器中看到标准差异。您可以：
- **接受** 单个更改
- **拒绝** 不需要的更改
- **审查** 应用前的完整差异

### 终端命令
当 Hermes 需要运行 shell 命令（构建、测试、安装）时，编辑器会在集成终端中显示它们。根据您的设置：
- 命令可能自动运行
- 或者您可能被要求 **批准** 每个命令

### 审批流程
对于可能具有破坏性的操作，编辑器会在 Hermes 执行前提示您批准。包括：
- 文件删除
- Shell 命令
- Git 操作

---

## 配置

ACP 模式下的 Hermes Agent 使用与 CLI **相同的配置**：

- **API 密钥/提供商**：`~/.hermes/.env`
- **代理配置**：`~/.hermes/config.yaml`
- **技能**：`~/.hermes/skills/`
- **会话**：`~/.hermes/state.db`

您可以运行 `hermes setup` 来配置提供商，或直接编辑 `~/.hermes/.env`。

### 更改模型

编辑 `~/.hermes/config.yaml`：

```yaml
model: openrouter/nous/hermes-3-llama-3.1-70b
```

或设置 `HERMES_MODEL` 环境变量。

### 工具集

ACP 会话默认使用精选的 `hermes-acp` 工具集。它专为编辑器工作流设计，有意排除了消息传递、定时任务管理和语音优先的用户体验功能。

---

## 故障排除

### 代理未在编辑器中显示

1. **检查注册路径** — 确保编辑器设置中的 `acp_registry/` 目录路径正确且包含 `agent.json`。
2. **检查 `hermes` 是否在 PATH 中** — 在终端中运行 `which hermes`。如果未找到，您可能需要激活虚拟环境或将其添加到 PATH。
3. 更改设置后 **重启编辑器**。

### 代理启动后立即出错

1. 运行 `hermes doctor` 检查您的配置。
2. 检查您是否有有效的 API 密钥：`hermes status`
3. 尝试在终端中直接运行 `hermes acp` 以查看错误输出。

### "Module not found" 错误

确保您已安装 ACP 扩展：

```bash
pip install -e ".[acp]"
```

### 响应缓慢

- ACP 以流式方式传输响应，因此您应该能看到增量输出。如果代理看起来卡住了，请检查您的网络连接和 API 提供商状态。
- 某些提供商有速率限制。尝试切换到不同的模型/提供商。

### 终端命令的权限被拒绝

如果编辑器阻止了终端命令，请检查您的 ACP Client 扩展设置中的自动批准或手动批准偏好。

### 日志

Hermes 在 ACP 模式下运行时将日志写入 stderr。查看：
- VS Code：**输出** 面板 → 选择 **ACP Client** 或 **Hermes Agent**
- Zed：**View** → **Toggle Terminal** 并检查进程输出
- JetBrains：**Event Log** 或 ACP 工具窗口

您还可以启用详细日志：

```bash
HERMES_LOG_LEVEL=DEBUG hermes acp
```

---

## 延伸阅读

- [ACP 规范](https://github.com/anysphere/acp)
- [Hermes Agent 文档](https://github.com/NousResearch/hermes-agent)
- 运行 `hermes --help` 查看所有 CLI 选项
