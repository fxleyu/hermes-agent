# Holographic 记忆提供商

本地 SQLite 事实存储，具有 FTS5 搜索、信任评分、实体解析和基于 HRR 的组合检索。

## 要求

无——使用 SQLite（始终可用）。NumPy 可选，用于 HRR 代数运算。

## 设置

```bash
hermes memory setup    # 选择 "holographic"
```

或手动设置：
```bash
hermes config set memory.provider holographic
```

## 配置

配置在 `config.yaml` 中的 `plugins.hermes-memory-store` 下：

| 键 | 默认值 | 描述 |
|----|--------|------|
| `db_path` | `$HERMES_HOME/memory_store.db` | SQLite 数据库路径 |
| `auto_extract` | `false` | 在会话结束时自动提取事实 |
| `default_trust` | `0.5` | 新事实的默认信任分数 |
| `hrr_dim` | `1024` | HRR 向量维度 |

## 工具

| 工具 | 描述 |
|------|------|
| `fact_store` | 9 种操作：add、search、probe、related、reason、contradict、update、remove、list |
| `fact_feedback` | 将事实评为有用/无用（训练信任分数） |
