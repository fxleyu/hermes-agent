# RetainDB 记忆提供商

云端记忆 API，具有混合搜索（向量 + BM25 + 重排序）和 7 种记忆类型。

## 要求

- RetainDB 账户（$20/月），来自 [retaindb.com](https://www.retaindb.com)
- `pip install requests`

## 设置

```bash
hermes memory setup    # 选择 "retaindb"
```

或手动设置：
```bash
hermes config set memory.provider retaindb
echo "RETAINDB_API_KEY=your-key" >> ~/.hermes/.env
```

## 配置

所有配置通过 `.env` 中的环境变量设置：

| 环境变量 | 默认值 | 描述 |
|---------|--------|------|
| `RETAINDB_API_KEY` | （必需） | API 密钥 |
| `RETAINDB_BASE_URL` | `https://api.retaindb.com` | API 端点 |
| `RETAINDB_PROJECT` | auto（按配置文件限定范围） | 项目标识符 |

## 工具

| 工具 | 描述 |
|------|------|
| `retaindb_profile` | 用户的稳定档案 |
| `retaindb_search` | 语义搜索 |
| `retaindb_context` | 与任务相关的上下文 |
| `retaindb_remember` | 存储带类型 + 重要性的事实 |
| `retaindb_forget` | 通过 ID 删除记忆 |
