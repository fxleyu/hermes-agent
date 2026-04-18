# OpenViking 记忆提供商

由 Volcengine（字节跳动）开发的上下文数据库，具有文件系统风格的知识层级、分层检索和自动记忆提取。

## 要求

- `pip install openviking`
- 运行中的 OpenViking 服务器（`openviking-server`）
- 在 `~/.openviking/ov.conf` 中配置了嵌入 + VLM 模型

## 设置

```bash
hermes memory setup    # 选择 "openviking"
```

或手动设置：
```bash
hermes config set memory.provider openviking
echo "OPENVIKING_ENDPOINT=http://localhost:1933" >> ~/.hermes/.env
```

## 配置

所有配置通过 `.env` 中的环境变量设置：

| 环境变量 | 默认值 | 描述 |
|---------|--------|------|
| `OPENVIKING_ENDPOINT` | `http://127.0.0.1:1933` | 服务器 URL |
| `OPENVIKING_API_KEY` | （无） | API 密钥（可选） |

## 工具

| 工具 | 描述 |
|------|------|
| `viking_search` | 支持 fast/deep/auto 模式的语义搜索 |
| `viking_read` | 读取 viking:// URI 处的内容（abstract/overview/full） |
| `viking_browse` | 文件系统风格的导航（list/tree/stat） |
| `viking_remember` | 存储事实，在会话提交时进行提取 |
| `viking_add_resource` | 将 URL/文档摄取到知识库中 |
