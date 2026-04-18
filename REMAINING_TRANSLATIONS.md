# 中文化未完成文件清单

> 生成时间：2026-04-18

## 总体进度

| 类别 | 已完成 | 总计 | 完成率 |
|------|--------|------|--------|
| Python 注释中文化（排除 tests/） | 307 | 307 | 100% |
| Markdown .zh.md 中文副本 | 151 | 153 | 98.7% |

## 未完成的 .zh.md 文件（2 个）

这两个文件因内容过长（各 1000+ 行），agent 多次尝试均因超时被终止，需手动处理。

| 原文件 | 目标文件 | 约行数 | 说明 |
|--------|----------|--------|------|
| `website/docs/integrations/providers.md` | `website/docs/integrations/providers.zh.md` | ~1097 | AI 提供商配置文档（含 Ollama、vLLM、llama.cpp 等详细说明） |
| `website/docs/user-guide/configuration.md` | `website/docs/user-guide/configuration.zh.md` | ~1314 | 完整配置参考（终端后端、压缩、TTS、显示、安全等） |

## 用户明确排除的内容

| 类别 | 文件数 | 原因 |
|------|--------|------|
| `tests/` 目录下所有文件 | ~600 .py | 用户要求不翻译 |
| `skills/` 目录下 .md 文件 | 288 | 用户要求不翻译 |
| `optional-skills/` 目录下 .md 文件 | 135 | 用户要求不翻译 |
| `.plans/` 内部计划文件 | 3 | 内部开发文件，非用户文档 |

## 处理建议

上述 2 个文件内容较长，建议：
1. 手动拆分为多个小节分别翻译
2. 或使用支持长文本输出的工具一次性翻译
