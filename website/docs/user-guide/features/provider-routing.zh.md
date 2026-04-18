---
title: 提供者路由
description: 配置 OpenRouter 的提供者偏好，以优化成本、速度或质量。
sidebar_label: 提供者路由
sidebar_position: 7
---

# 提供者路由

当使用 [OpenRouter](https://openrouter.ai) 作为你的 LLM 提供者时，Hermes Agent 支持**提供者路由** —— 细粒度控制哪些底层 AI 提供者处理你的请求以及如何对其进行优先级排序。

OpenRouter 将请求路由到许多提供者（例如 Anthropic、Google、AWS Bedrock、Together AI）。提供者路由让你可以针对成本、速度、质量进行优化，或强制要求使用特定提供者。

## 配置

在你的 `~/.hermes/config.yaml` 中添加 `provider_routing` 部分：

```yaml
provider_routing:
  sort: "price"           # 如何对提供者进行排序
  only: []                # 白名单：仅使用这些提供者
  ignore: []              # 黑名单：永不使用这些提供者
  order: []               # 显式的提供者优先级顺序
  require_parameters: false  # 仅使用支持所有参数的提供者
  data_collection: null   # 控制数据收集（"allow" 或 "deny"）
```

:::info
提供者路由仅在使用 OpenRouter 时生效。对直接提供者连接（例如直接连接到 Anthropic API）无效。
:::

## 选项

### `sort`

控制 OpenRouter 如何为你的请求对可用提供者进行排序。

| 值 | 描述 |
|-------|-------------|
| `"price"` | 最便宜的提供者优先 |
| `"throughput"` | 每秒 token 数最快的优先 |
| `"latency"` | 首 token 响应时间最低的优先 |

```yaml
provider_routing:
  sort: "price"
```

### `only`

提供者名称白名单。设置后，**仅**使用这些提供者。其他所有提供者被排除。

```yaml
provider_routing:
  only:
    - "Anthropic"
    - "Google"
```

### `ignore`

提供者名称黑名单。这些提供者将**永远不会**被使用，即使它们提供最便宜或最快的选项。

```yaml
provider_routing:
  ignore:
    - "Together"
    - "DeepInfra"
```

### `order`

显式优先级顺序。列在前面的提供者优先使用。未列出的提供者作为后备。

```yaml
provider_routing:
  order:
    - "Anthropic"
    - "Google"
    - "AWS Bedrock"
```

### `require_parameters`

当设为 `true` 时，OpenRouter 只会路由到支持你请求中**所有**参数的提供者（如 `temperature`、`top_p`、`tools` 等）。这避免了参数被静默丢弃。

```yaml
provider_routing:
  require_parameters: true
```

### `data_collection`

控制提供者是否可以使用你的提示词进行训练。选项为 `"allow"` 或 `"deny"`。

```yaml
provider_routing:
  data_collection: "deny"
```

## 实际示例

### 优化成本

路由到最便宜的可用提供者。适用于高流量使用和开发：

```yaml
provider_routing:
  sort: "price"
```

### 优化速度

优先使用低延迟提供者，适用于交互式使用：

```yaml
provider_routing:
  sort: "latency"
```

### 优化吞吐量

适用于长文本生成，每秒 token 数很重要的场景：

```yaml
provider_routing:
  sort: "throughput"
```

### 锁定到特定提供者

确保所有请求通过特定提供者以保持一致性：

```yaml
provider_routing:
  only:
    - "Anthropic"
```

### 避免特定提供者

排除你不想使用的提供者（例如出于数据隐私考虑）：

```yaml
provider_routing:
  ignore:
    - "Together"
    - "Lepton"
  data_collection: "deny"
```

### 首选顺序及后备

先尝试首选提供者，不可用时回退到其他提供者：

```yaml
provider_routing:
  order:
    - "Anthropic"
    - "Google"
  require_parameters: true
```

## 工作原理

提供者路由偏好通过每次 API 调用的 `extra_body.provider` 字段传递给 OpenRouter API。这适用于：

- **CLI 模式** —— 在 `~/.hermes/config.yaml` 中配置，启动时加载
- **网关模式** —— 同一配置文件，网关启动时加载

路由配置从 `config.yaml` 读取，并在创建 `AIAgent` 时作为参数传递：

```
providers_allowed  ← 来自 provider_routing.only
providers_ignored  ← 来自 provider_routing.ignore
providers_order    ← 来自 provider_routing.order
provider_sort      ← 来自 provider_routing.sort
provider_require_parameters ← 来自 provider_routing.require_parameters
provider_data_collection    ← 来自 provider_routing.data_collection
```

:::tip
你可以组合多个选项。例如，按价格排序但排除某些提供者并要求参数支持：

```yaml
provider_routing:
  sort: "price"
  ignore: ["Together"]
  require_parameters: true
  data_collection: "deny"
```
:::

## 默认行为

当未配置 `provider_routing` 部分时（默认情况），OpenRouter 使用其自身的默认路由逻辑，通常会自动平衡成本和可用性。

:::tip 提供者路由与后备模型
提供者路由控制 **OpenRouter 内的哪些子提供者**处理你的请求。如果你需要在主模型失败时自动故障转移到完全不同的提供者，请参阅[后备提供者](/docs/user-guide/features/fallback-providers)。
:::
