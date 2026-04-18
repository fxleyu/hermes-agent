# Mem0 记忆提供商

服务端 LLM 事实提取，具有语义搜索、重排序和自动去重。

## 要求

- `pip install mem0ai`
- 来自 [app.mem0.ai](https://app.mem0.ai) 的 Mem0 API 密钥

## 设置

```bash
hermes memory setup    # 选择 "mem0"
```

或手动设置：
```bash
hermes config set memory.provider mem0
echo "MEM0_API_KEY=your-key" >> ~/.hermes/.env
```

## 配置

配置文件：`$HERMES_HOME/mem0.json`

| 键 | 默认值 | 描述 |
|----|--------|------|
| `user_id` | `hermes-user` | Mem0 上的用户标识符 |
| `agent_id` | `hermes` | 代理标识符 |
| `rerank` | `true` | 启用召回的重排序 |

## 工具

| 工具 | 描述 |
|------|------|
| `mem0_profile` | 关于用户的所有存储记忆 |
| `mem0_search` | 带可选重排序的语义搜索 |
| `mem0_conclude` | 逐字存储事实（无 LLM 提取） |
