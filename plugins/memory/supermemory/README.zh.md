# Supermemory 记忆提供商

具有档案召回、语义搜索、显式记忆工具和会话结束对话摄取的语义长期记忆。

## 要求

- `pip install supermemory`
- 来自 [supermemory.ai](https://supermemory.ai) 的 Supermemory API 密钥

## 设置

```bash
hermes memory setup    # 选择 "supermemory"
```

或手动设置：

```bash
hermes config set memory.provider supermemory
echo 'SUPERMEMORY_API_KEY=***' >> ~/.hermes/.env
```

## 配置

配置文件：`$HERMES_HOME/supermemory.json`

| 键 | 默认值 | 描述 |
|----|--------|------|
| `container_tag` | `hermes` | 用于搜索和写入的容器标签。支持 `{identity}` 模板用于按配置文件限定范围的标签（例如 `hermes-{identity}` → `hermes-coder`）。 |
| `auto_recall` | `true` | 在轮次前注入相关记忆上下文 |
| `auto_capture` | `true` | 每次响应后存储清理过的用户-助手轮次 |
| `max_recall_results` | `10` | 格式化到上下文中的最大召回项目数 |
| `profile_frequency` | `50` | 在第一轮和每 N 轮包含档案事实 |
| `capture_mode` | `all` | 默认跳过微小或无关紧要的轮次 |
| `search_mode` | `hybrid` | 搜索模式：`hybrid`（档案 + 记忆）、`memories`（仅记忆）、`documents`（仅文档） |
| `entity_context` | 内置默认值 | 传递给 Supermemory 的提取指导 |
| `api_timeout` | `5.0` | SDK 和摄取请求的超时时间 |

### 环境变量

| 变量 | 描述 |
|------|------|
| `SUPERMEMORY_API_KEY` | API 密钥（必需） |
| `SUPERMEMORY_CONTAINER_TAG` | 覆盖容器标签（优先于配置文件） |

## 工具

| 工具 | 描述 |
|------|------|
| `supermemory_store` | 存储显式记忆 |
| `supermemory_search` | 按语义相似度搜索记忆 |
| `supermemory_forget` | 通过 ID 或最佳匹配查询遗忘记忆 |
| `supermemory_profile` | 检索持久化档案和近期上下文 |

## 行为

启用后，Hermes 可以：

- 在每个轮次前预取相关记忆上下文
- 在每次完成的响应后存储清理过的对话轮次
- 在会话结束时摄取完整会话以获得更丰富的图谱更新
- 暴露显式工具用于搜索、存储、遗忘和档案访问

## 按配置文件限定范围的容器

在 `container_tag` 中使用 `{identity}` 以按 Hermes 配置文件限定记忆范围：

```json
{
  "container_tag": "hermes-{identity}"
}
```

对于名为 `coder` 的配置文件，这会解析为 `hermes-coder`。默认配置文件解析为 `hermes-default`。没有 `{identity}` 时，所有配置文件共享同一个容器。

## 多容器模式

对于高级设置（例如 OpenClaw 风格的多工作区），您可以启用自定义容器标签，让代理可以跨多个命名容器读写：

```json
{
  "container_tag": "hermes",
  "enable_custom_container_tags": true,
  "custom_containers": ["project-alpha", "project-beta", "shared-knowledge"],
  "custom_container_instructions": "Use project-alpha for coding tasks, project-beta for research, and shared-knowledge for team-wide facts."
}
```

启用后：
- `supermemory_search`、`supermemory_store`、`supermemory_forget` 和 `supermemory_profile` 接受可选的 `container_tag` 参数
- 标签必须在白名单中：主容器 + `custom_containers`
- 自动操作（轮次同步、预取、记忆写入镜像、会话摄取）始终仅使用**主**容器
- 自定义容器说明注入到系统提示词中

## 支持

- [Supermemory Discord](https://supermemory.link/discord)
- [support@supermemory.com](mailto:support@supermemory.com)
- [supermemory.ai](https://supermemory.ai)
