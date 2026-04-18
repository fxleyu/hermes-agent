---
sidebar_position: 4
title: "工具集参考"
description: "Hermes 核心、复合、平台和动态工具集参考"
---

# 工具集参考

工具集是命名的工具包，控制代理能做什么。它们是按平台、按会话或按任务配置工具可用性的主要机制。

## 工具集如何工作

每个工具恰好属于一个工具集。当你启用一个工具集时，该包中的所有工具都变得对代理可用。工具集分为三种：

- **核心** — 一组相关工具的单一逻辑分组（例如，`file` 包含 `read_file`、`write_file`、`patch`、`search_files`）
- **复合** — 为常见场景组合多个核心工具集（例如，`debugging` 包含 file、terminal 和 web 工具）
- **平台** — 针对特定部署上下文的完整工具配置（例如，`hermes-cli` 是交互式 CLI 会话的默认值）

## 配置工具集

### 每会话（CLI）

```bash
hermes chat --toolsets web,file,terminal
hermes chat --toolsets debugging        # 复合 — 展开为 file + terminal + web
hermes chat --toolsets all              # 所有
```

### 每平台（config.yaml）

```yaml
toolsets:
  - hermes-cli          # CLI 默认
  # - hermes-telegram   # Telegram 网关覆盖
```

### 交互式管理

```bash
hermes tools                            # curses UI 按平台启用/禁用
```

或在会话中：

```
/tools list
/tools disable browser
/tools enable rl
```

## 核心工具集

| 工具集 | 工具 | 用途 |
|--------|------|------|
| `browser` | `browser_back`、`browser_click`、`browser_console`、`browser_get_images`、`browser_navigate`、`browser_press`、`browser_scroll`、`browser_snapshot`、`browser_type`、`browser_vision`、`web_search` | 完整浏览器自动化。包含 `web_search` 作为快速查询的备选。 |
| `clarify` | `clarify` | 当代理需要澄清时向用户提问。 |
| `code_execution` | `execute_code` | 运行可以编程调用 Hermes 工具的 Python 脚本。 |
| `cronjob` | `cronjob` | 调度和管理重复任务。 |
| `delegation` | `delegate_task` | 生成隔离的子代理实例进行并行工作。 |
| `file` | `patch`、`read_file`、`search_files`、`write_file` | 文件读取、写入、搜索和编辑。 |
| `homeassistant` | `ha_call_service`、`ha_get_state`、`ha_list_entities`、`ha_list_services` | 通过 Home Assistant 进行智能家居控制。仅在设置 `HASS_TOKEN` 时可用。 |
| `image_gen` | `image_generate` | 通过 FAL.ai 进行文本到图像生成。 |
| `memory` | `memory` | 持久跨会话记忆管理。 |
| `messaging` | `send_message` | 在会话内向其他平台（Telegram、Discord 等）发送消息。 |
| `moa` | `mixture_of_agents` | 通过 Mixture of Agents 实现多模型共识。 |
| `rl` | `rl_check_status`、`rl_edit_config`、`rl_get_current_config`、`rl_get_results`、`rl_list_environments`、`rl_list_runs`、`rl_select_environment`、`rl_start_training`、`rl_stop_training`、`rl_test_inference` | RL 训练环境管理（Atropos）。 |
| `search` | `web_search` | 仅网络搜索（不含 extract）。 |
| `session_search` | `session_search` | 搜索过去的对话会话。 |
| `skills` | `skill_manage`、`skill_view`、`skills_list` | 技能 CRUD 和浏览。 |
| `terminal` | `process`、`terminal` | Shell 命令执行和后台进程管理。 |
| `todo` | `todo` | 会话内任务列表管理。 |
| `tts` | `text_to_speech` | 文本转语音音频生成。 |
| `vision` | `vision_analyze` | 通过视觉模型进行图像分析。 |
| `web` | `web_extract`、`web_search` | 网络搜索和页面内容提取。 |

## 复合工具集

这些展开为多个核心工具集，为常见场景提供便捷的简写：

| 工具集 | 展开为 | 用例 |
|--------|--------|------|
| `debugging` | `patch`、`process`、`read_file`、`search_files`、`terminal`、`web_extract`、`web_search`、`write_file` | 调试会话 — 文件访问、终端和网络研究，无浏览器或委派开销。 |
| `safe` | `image_generate`、`vision_analyze`、`web_extract`、`web_search` | 只读研究和媒体生成。无文件写入、无终端访问、无代码执行。适合不受信任或受限的环境。 |

## 平台工具集

平台工具集为部署目标定义完整的工具配置。大多数消息平台使用与 `hermes-cli` 相同的集合：

| 工具集 | 与 `hermes-cli` 的区别 |
|--------|----------------------|
| `hermes-cli` | 完整工具集 — 所有 36 个工具包括 `clarify`。交互式 CLI 会话的默认值。 |
| `hermes-acp` | 移除 `clarify`、`cronjob`、`image_generate`、`send_message`、`text_to_speech`、homeassistant 工具。专注于 IDE 上下文中的编码任务。 |
| `hermes-api-server` | 移除 `clarify`、`send_message` 和 `text_to_speech`。添加其他所有 — 适合无法进行用户交互的编程访问。 |
| `hermes-telegram` | 与 `hermes-cli` 相同。 |
| `hermes-discord` | 与 `hermes-cli` 相同。 |
| `hermes-slack` | 与 `hermes-cli` 相同。 |
| `hermes-whatsapp` | 与 `hermes-cli` 相同。 |
| `hermes-signal` | 与 `hermes-cli` 相同。 |
| `hermes-matrix` | 与 `hermes-cli` 相同。 |
| `hermes-mattermost` | 与 `hermes-cli` 相同。 |
| `hermes-email` | 与 `hermes-cli` 相同。 |
| `hermes-sms` | 与 `hermes-cli` 相同。 |
| `hermes-dingtalk` | 与 `hermes-cli` 相同。 |
| `hermes-feishu` | 与 `hermes-cli` 相同。 |
| `hermes-wecom` | 与 `hermes-cli` 相同。 |
| `hermes-wecom-callback` | 企微回调工具集 — 企业自建应用消息（完全访问）。 |
| `hermes-weixin` | 与 `hermes-cli` 相同。 |
| `hermes-bluebubbles` | 与 `hermes-cli` 相同。 |
| `hermes-qqbot` | 与 `hermes-cli` 相同。 |
| `hermes-homeassistant` | 与 `hermes-cli` 相同。 |
| `hermes-webhook` | 与 `hermes-cli` 相同。 |
| `hermes-gateway` | 所有消息平台工具集的并集。网关需要最广泛工具集时内部使用。 |

## 动态工具集

### MCP 服务器工具集

每个配置的 MCP 服务器在运行时生成一个 `mcp-<server>` 工具集。例如，如果你配置了一个 `github` MCP 服务器，就会创建一个包含该服务器暴露的所有工具的 `mcp-github` 工具集。

```yaml
# config.yaml
mcp:
  servers:
    github:
      command: npx
      args: ["-y", "@modelcontextprotocol/server-github"]
```

这创建了一个 `mcp-github` 工具集，你可以在 `--toolsets` 或平台配置中引用。

### 插件工具集

插件可以在初始化期间通过 `ctx.register_tool()` 注册自己的工具集。这些与内置工具集一起出现，可以以相同方式启用/禁用。

### 自定义工具集

在 `config.yaml` 中定义自定义工具集以创建项目特定的包：

```yaml
toolsets:
  - hermes-cli
custom_toolsets:
  data-science:
    - file
    - terminal
    - code_execution
    - web
    - vision
```

### 通配符

- `all` 或 `*` — 展开为所有已注册的工具集（内置 + 动态 + 插件）

## 与 `hermes tools` 的关系

`hermes tools` 命令提供基于 curses 的 UI，按平台切换单个工具的开关。这在工具级别操作（比工具集更细粒度）并持久化到 `config.yaml`。即使其工具集已启用，禁用的工具也会被过滤掉。

另请参见：[工具参考](./tools-reference.md) 了解单个工具及其参数的完整列表。
