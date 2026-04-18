# ByteRover 记忆提供商

通过 `brv` CLI 实现的持久化记忆——层级知识树，具有分层检索（模糊文本 → LLM 驱动搜索）。

## 要求

安装 ByteRover CLI：
```bash
curl -fsSL https://byterover.dev/install.sh | sh
# 或
npm install -g byterover-cli
```

## 设置

```bash
hermes memory setup    # 选择 "byterover"
```

或手动设置：
```bash
hermes config set memory.provider byterover
# 可选的云端同步：
echo "BRV_API_KEY=your-key" >> ~/.hermes/.env
```

## 配置

| 环境变量 | 必需 | 描述 |
|---------|------|------|
| `BRV_API_KEY` | 否 | 云端同步密钥（可选，默认本地优先） |

工作目录：`$HERMES_HOME/byterover/`（按配置文件限定范围）。

## 工具

| 工具 | 描述 |
|------|------|
| `brv_query` | 搜索知识树 |
| `brv_curate` | 存储事实、决策、模式 |
| `brv_status` | CLI 版本、树统计信息、同步状态 |
