---
title: 图像生成
description: 通过 FAL.ai 生成图像——8 种模型，包括 FLUX 2、GPT-Image、Nano Banana Pro、Ideogram、Recraft V4 Pro 等，可通过 `hermes tools` 选择。
sidebar_label: 图像生成
sidebar_position: 6
---

# 图像生成

Hermes Agent 通过 FAL.ai 从文本提示生成图像。开箱即用支持八种模型，每种在速度、质量和成本方面有不同的权衡。活跃模型可通过 `hermes tools` 由用户配置并持久化在 `config.yaml` 中。

## 支持的模型

| 模型 | 速度 | 优势 | 价格 |
|------|------|------|------|
| `fal-ai/flux-2/klein/9b` *（默认）* | `<1s` | 快速，清晰的文字 | $0.006/MP |
| `fal-ai/flux-2-pro` | ~6s | 工作室级逼真 | $0.03/MP |
| `fal-ai/z-image/turbo` | ~2s | 双语中英文，60亿参数 | $0.005/MP |
| `fal-ai/nano-banana-pro` | ~8s | Gemini 3 Pro，推理深度，文字渲染 | $0.15/张（1K） |
| `fal-ai/gpt-image-1.5` | ~15s | 提示遵循 | $0.034/张 |
| `fal-ai/ideogram/v3` | ~5s | 最佳排版 | $0.03-0.09/张 |
| `fal-ai/recraft/v4/pro/text-to-image` | ~8s | 设计、品牌系统、生产就绪 | $0.25/张 |
| `fal-ai/qwen-image` | ~12s | 基于 LLM，复杂文字 | $0.02/MP |

价格为撰写时 FAL 的定价；请查看 [fal.ai](https://fal.ai/) 获取当前价格。

## 设置

:::tip Nous 订阅用户
如果你有付费的 [Nous Portal](https://portal.nousresearch.com) 订阅，你可以通过 **[工具网关](tool-gateway.md)** 使用图像生成而无需 FAL API 密钥。你的模型选择在两种路径中都会保留。

如果托管网关对特定模型返回 `HTTP 4xx`，该模型尚未在门户端代理——代理会告诉你并提供补救步骤（设置 `FAL_KEY` 进行直接访问，或选择其他模型）。
:::

### 获取 FAL API 密钥

1. 在 [fal.ai](https://fal.ai/) 注册
2. 从仪表板生成 API 密钥

### 配置并选择模型

运行工具命令：

```bash
hermes tools
```

导航到 **Image Generation**，选择你的后端（Nous Subscription 或 FAL.ai），然后选择器在列对齐的表格中显示所有支持的模型——使用方向键导航，Enter 选择：

```
  Model                          Speed    Strengths                    Price
  fal-ai/flux-2/klein/9b         <1s      Fast, crisp text             $0.006/MP   ← currently in use
  fal-ai/flux-2-pro              ~6s      Studio photorealism          $0.03/MP
  fal-ai/z-image/turbo           ~2s      Bilingual EN/CN, 6B          $0.005/MP
  ...
```

你的选择保存到 `config.yaml`：

```yaml
image_gen:
  model: fal-ai/flux-2/klein/9b
  use_gateway: false            # 使用 Nous Subscription 时为 true
```

### GPT-Image 质量

`fal-ai/gpt-image-1.5` 请求质量固定为 `medium`（1024x1024 约 $0.034/张）。我们不将 `low` / `high` 层级作为用户可选项暴露，以便 Nous Portal 计费在所有用户中保持可预测——层级之间的成本差异约为 22 倍。如果你想要更便宜的 GPT-Image 选项，选择其他模型；如果你想要更高质量，使用 Klein 9B 或 Imagen 级别的模型。

## 使用

面向代理的模式有意保持简约——模型会使用你配置的设置：

```
Generate an image of a serene mountain landscape with cherry blossoms
```

```
Create a square portrait of a wise old owl — use the typography model
```

```
Make me a futuristic cityscape, landscape orientation
```

## 宽高比

从代理的角度，每个模型接受相同的三种宽高比。在内部，每个模型的原生尺寸规格会自动填充：

| 代理输入 | image_size（flux/z-image/qwen/recraft/ideogram） | aspect_ratio（nano-banana-pro） | image_size（gpt-image） |
|---------|------------------------------------------------|-------------------------------|----------------------|
| `landscape` | `landscape_16_9` | `16:9` | `1536x1024` |
| `square` | `square_hd` | `1:1` | `1024x1024` |
| `portrait` | `portrait_16_9` | `9:16` | `1024x1536` |

此转换发生在 `_build_fal_payload()` 中——代理代码无需了解每个模型的模式差异。

## 自动放大

通过 FAL 的 **Clarity Upscaler** 进行放大按模型控制：

| 模型 | 放大？ | 原因 |
|------|--------|------|
| `fal-ai/flux-2-pro` | ✓ | 向后兼容（是选择器之前的默认值） |
| 所有其他 | ✗ | 快速模型会失去亚秒级的价值主张；高分辨率模型不需要 |

放大运行时使用以下设置：

| 设置 | 值 |
|------|-----|
| 放大倍数 | 2x |
| 创造力 | 0.35 |
| 相似度 | 0.6 |
| 引导比例 | 4 |
| 推理步骤 | 18 |

如果放大失败（网络问题、速率限制），会自动返回原始图像。

## 内部工作原理

1. **模型解析** ——`_resolve_fal_model()` 从 `config.yaml` 读取 `image_gen.model`，回退到 `FAL_IMAGE_MODEL` 环境变量，然后回退到 `fal-ai/flux-2/klein/9b`。
2. **负载构建** ——`_build_fal_payload()` 将你的 `aspect_ratio` 转换为模型的原生格式（预设枚举、宽高比枚举或 GPT 字面值），合并模型的默认参数，应用调用者覆盖，然后过滤到模型的 `supports` 白名单，确保不发送不支持的键。
3. **提交** ——`_submit_fal_request()` 通过直接 FAL 凭证或托管 Nous 网关路由。
4. **放大** ——仅在模型的元数据中 `upscale: True` 时运行。
5. **投递** ——最终图像 URL 返回给代理，代理发出 `MEDIA:<url>` 标签，平台适配器将其转换为原生媒体。

## 调试

启用调试日志：

```bash
export IMAGE_TOOLS_DEBUG=true
```

调试日志输出到 `./logs/image_tools_debug_<session_id>.json`，包含每次调用的详情（模型、参数、时间、错误）。

## 平台投递

| 平台 | 投递方式 |
|------|---------|
| **CLI** | 图像 URL 以 markdown `![](url)` 打印——点击打开 |
| **Telegram** | 带提示作为标题的照片消息 |
| **Discord** | 嵌入消息中 |
| **Slack** | Slack 展开的 URL |
| **WhatsApp** | 媒体消息 |
| **其他** | 纯文本中的 URL |

## 限制

- **需要 FAL 凭证**（直接 `FAL_KEY` 或 Nous 订阅）
- **仅文生图** ——不支持通过此工具进行图像修复、图生图或编辑
- **临时 URL** ——FAL 返回的托管 URL 在数小时/天后过期；如需保存请本地保存
- **每模型限制** ——某些模型不支持 `seed`、`num_inference_steps` 等。`supports` 过滤器会静默丢弃不支持的参数；这是预期行为
