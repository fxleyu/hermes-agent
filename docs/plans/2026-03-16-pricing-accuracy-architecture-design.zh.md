# 定价准确性架构

日期：2026-03-16

## 目标

Hermes 应仅在美元成本有用户实际计费路径的官方来源支持时才显示美元成本。

本设计替换以下文件中当前的静态、启发式定价流程：

- `run_agent.py`
- `agent/usage_pricing.py`
- `agent/insights.py`
- `cli.py`

替换为一个提供商感知的定价系统，该系统：

- 正确处理缓存计费
- 区分 `actual`（实际）vs `estimated`（估算）vs `included`（包含）vs `unknown`（未知）
- 在提供商公开权威计费数据时进行事后成本对账
- 支持直接提供商、OpenRouter、订阅、企业定价和自定义端点

## 当前设计中的问题

当前 Hermes 行为有四个结构性问题：

1. 它仅存储 `prompt_tokens` 和 `completion_tokens`，这对于将缓存读取和缓存写入分开计费的提供商是不够的。
2. 它使用静态模型价格表和模糊启发式方法，可能偏离当前的官方定价。
3. 它假设公共 API 列表价格与用户的实际计费路径匹配。
4. 它没有区分实时估算和对账后的计费成本。

## 设计原则

1. 在定价前标准化用量。
2. 永远不将缓存的 token 计入普通输入成本。
3. 显式跟踪确定性。
4. 将计费路径视为模型身份的一部分。
5. 优先使用官方机器可读来源，而非抓取文档。
6. 在可用时使用事后提供商成本 API。
7. 显示 `n/a` 而非捏造精度。

## 高层架构

新系统有四层：

1. `usage_normalization`（用量标准化）
   将原始提供商用量转换为规范用量记录。
2. `pricing_source_resolution`（定价来源解析）
   确定计费路径、真实来源和适用的定价来源。
3. `cost_estimation_and_reconciliation`（成本估算与对账）
   在可能时产生即时估算，然后用实际计费成本替换或标注。
4. `presentation`（展示）
   `/usage`、`/insights` 和状态栏显示带有确定性元数据的成本。

## 规范用量记录

添加一个规范用量模型，每个提供商路径在进行任何定价计算之前都映射到此模型。

建议结构：

```python
@dataclass
class CanonicalUsage:
    provider: str
    billing_provider: str
    model: str
    billing_route: str

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0
    request_count: int = 1

    raw_usage: dict[str, Any] | None = None
    raw_usage_fields: dict[str, str] | None = None
    computed_fields: set[str] | None = None

    provider_request_id: str | None = None
    provider_generation_id: str | None = None
    provider_response_id: str | None = None
```

规则：

- `input_tokens` 仅表示非缓存输入。
- `cache_read_tokens` 和 `cache_write_tokens` 永远不合并到 `input_tokens` 中。
- `output_tokens` 不包含缓存指标。
- `reasoning_tokens` 是遥测数据，除非提供商正式单独计费。

这与 `opencode` 使用的标准化模式相同，扩展了溯源和对账 ID。

## 提供商标准化规则

### OpenAI 直连

源用量字段：

- `prompt_tokens`
- `completion_tokens`
- `prompt_tokens_details.cached_tokens`

标准化：

- `cache_read_tokens = cached_tokens`
- `input_tokens = prompt_tokens - cached_tokens`
- `cache_write_tokens = 0`，除非 OpenAI 在相关路径中公开此字段
- `output_tokens = completion_tokens`

### Anthropic 直连

源用量字段：

- `input_tokens`
- `output_tokens`
- `cache_read_input_tokens`
- `cache_creation_input_tokens`

标准化：

- `input_tokens = input_tokens`
- `output_tokens = output_tokens`
- `cache_read_tokens = cache_read_input_tokens`
- `cache_write_tokens = cache_creation_input_tokens`

### OpenRouter

估算时的用量标准化应尽可能使用响应用量负载，遵循与底层提供商相同的规则。

对账时的记录还应存储：

- OpenRouter generation id
- 可用时的原生 token 字段
- `total_cost`
- `cache_discount`
- `upstream_inference_cost`
- `is_byok`

### Gemini / Vertex

在可用时使用官方 Gemini 或 Vertex 用量字段。

如果公开了缓存内容 token：

- 映射到 `cache_read_tokens`

如果路径未公开缓存创建指标：

- 存储 `cache_write_tokens = 0`
- 保留原始用量负载以供后续扩展

### DeepSeek 及其他直连提供商

仅标准化官方公开的字段。

如果提供商未公开缓存桶：

- 不推断，除非提供商明确记录了如何派生

### 订阅 / 包含成本路径

这些仍使用规范用量模型。

token 正常跟踪。成本取决于计费模式，而非是否存在用量。

## 计费路径模型

Hermes 必须停止仅以 `model` 作为定价键。

引入计费路径描述符：

```python
@dataclass
class BillingRoute:
    provider: str
    base_url: str | None
    model: str
    billing_mode: str
    organization_hint: str | None = None
```

`billing_mode` 取值：

- `official_cost_api`
- `official_generation_api`
- `official_models_api`
- `official_docs_snapshot`
- `subscription_included`
- `user_override`
- `custom_contract`
- `unknown`

示例：

- OpenAI 直连 API 且有 Costs API 访问权限：`official_cost_api`
- Anthropic 直连 API 且有 Usage & Cost API 访问权限：`official_cost_api`
- 对账前的 OpenRouter 请求：`official_models_api`
- generation 查询后的 OpenRouter 请求：`official_generation_api`
- GitHub Copilot 风格的订阅路径：`subscription_included`
- 本地 OpenAI 兼容服务器：`unknown`
- 有配置费率的企业合同：`custom_contract`

## 成本状态模型

每个显示的成本应包含：

```python
@dataclass
class CostResult:
    amount_usd: Decimal | None
    status: Literal["actual", "estimated", "included", "unknown"]
    source: Literal[
        "provider_cost_api",
        "provider_generation_api",
        "provider_models_api",
        "official_docs_snapshot",
        "user_override",
        "custom_contract",
        "none",
    ]
    label: str
    fetched_at: datetime | None
    pricing_version: str | None
    notes: list[str]
```

展示规则：

- `actual`：将美元金额显示为最终值
- `estimated`：显示美元金额并附带估算标签
- `included`：显示 `included` 或 `$0.00 (included)`，取决于 UX 选择
- `unknown`：显示 `n/a`

## 官方来源层级

按此顺序解析成本：

1. 请求级别或账户级别的官方计费成本
2. 官方机器可读模型定价
3. 官方文档快照
4. 用户覆盖或自定义合同
5. 未知

系统永远不能在当前计费路径存在更高置信来源时跳到更低级别。

## 提供商特定真实来源规则

### OpenAI 直连

首选真实来源：

1. Costs API 用于对账后支出
2. 官方定价页面用于实时估算

### Anthropic 直连

首选真实来源：

1. Usage & Cost API 用于对账后支出
2. 官方定价文档用于实时估算

### OpenRouter

首选真实来源：

1. `GET /api/v1/generation` 用于对账后的 `total_cost`
2. `GET /api/v1/models` 定价用于实时估算

不要使用底层提供商的公共定价作为 OpenRouter 计费的真实来源。

### Gemini / Vertex

首选真实来源：

1. 当路径可用时，官方计费导出或计费 API 用于对账后支出
2. 官方定价文档用于估算

### DeepSeek

首选真实来源：

1. 未来如果可用，官方机器可读成本来源
2. 目前使用官方定价文档快照

### 订阅包含路径

首选真实来源：

1. 明确的路径配置将模型标记为订阅包含

这些应显示 `included`，而非 API 列表价格估算。

### 自定义端点 / 本地模型

首选真实来源：

1. 用户覆盖
2. 自定义合同配置
3. 未知

这些应默认为 `unknown`。

## 定价目录

用更丰富的定价目录替换当前的 `MODEL_PRICING` 字典。

建议记录：

```python
@dataclass
class PricingEntry:
    provider: str
    route_pattern: str
    model_pattern: str

    input_cost_per_million: Decimal | None = None
    output_cost_per_million: Decimal | None = None
    cache_read_cost_per_million: Decimal | None = None
    cache_write_cost_per_million: Decimal | None = None
    request_cost: Decimal | None = None
    image_cost: Decimal | None = None

    source: str = "official_docs_snapshot"
    source_url: str | None = None
    fetched_at: datetime | None = None
    pricing_version: str | None = None
```

目录应具有路径感知能力：

- `openai:gpt-5`
- `anthropic:claude-opus-4-6`
- `openrouter:anthropic/claude-opus-4.6`
- `copilot:gpt-4o`

这避免了将直连提供商计费与聚合器计费混淆。

## 定价同步架构

引入定价同步子系统，而非手动维护一个硬编码表。

建议模块：

- `agent/pricing/catalog.py`
- `agent/pricing/sources.py`
- `agent/pricing/sync.py`
- `agent/pricing/reconcile.py`
- `agent/pricing/types.py`

### 同步来源

- OpenRouter models API
- 当不存在 API 时的官方提供商文档快照
- 来自配置的用户覆盖

### 同步输出

本地缓存定价条目，包含：

- 来源 URL
- 获取时间戳
- 版本/哈希
- 置信度/来源类型

### 同步频率

- 启动时预热缓存
- 根据来源每 6 到 24 小时后台刷新
- 手动 `hermes pricing sync`

## 对账架构

实时请求最初可能只产生估算。Hermes 应在提供商公开实际计费成本时进行后续对账。

建议流程：

1. 代理调用完成。
2. Hermes 存储规范用量加对账 ID。
3. 如果存在定价来源，Hermes 计算即时估算。
4. 对账工作器在支持时获取实际成本。
5. 会话和消息记录以 `actual` 成本更新。

这可以运行：

- 对廉价查询内联运行
- 对延迟的提供商记账异步运行

## 持久化变更

会话存储应停止仅存储聚合的 prompt/completion 总量。

为用量和成本确定性添加字段：

- `input_tokens`
- `output_tokens`
- `cache_read_tokens`
- `cache_write_tokens`
- `reasoning_tokens`
- `estimated_cost_usd`
- `actual_cost_usd`
- `cost_status`
- `cost_source`
- `pricing_version`
- `billing_provider`
- `billing_mode`

如果一个 PR 的架构扩展太大，添加一个新的定价事件表：

```text
session_cost_events
  id
  session_id
  request_id
  provider
  model
  billing_mode
  input_tokens
  output_tokens
  cache_read_tokens
  cache_write_tokens
  estimated_cost_usd
  actual_cost_usd
  cost_status
  cost_source
  pricing_version
  created_at
  updated_at
```

## Hermes 接触点

### `run_agent.py`

当前职责：

- 解析原始提供商用量
- 更新会话 token 计数器

新职责：

- 构建 `CanonicalUsage`
- 更新规范计数器
- 存储对账 ID
- 向定价子系统发送用量事件

### `agent/usage_pricing.py`

当前职责：

- 静态查找表
- 直接成本计算

新职责：

- 迁移或替换为定价目录门面
- 不使用模糊的模型族启发式方法
- 没有计费路径上下文时不直接定价

### `cli.py`

当前职责：

- 直接从 prompt/completion 总量计算会话成本

新职责：

- 显示 `CostResult`
- 显示状态标记：
  - `actual`
  - `estimated`
  - `included`
  - `n/a`

### `agent/insights.py`

当前职责：

- 从静态定价重新计算历史估算

新职责：

- 聚合存储的定价事件
- 优先使用实际成本而非估算
- 仅在对账不可用时显示估算

## UX 规则

### 状态栏

显示以下之一：

- `$1.42`
- `~$1.42`
- `included`
- `cost n/a`

其中：

- `$1.42` 表示 `actual`
- `~$1.42` 表示 `estimated`
- `included` 表示订阅支持或明确的零成本路径
- `cost n/a` 表示未知

### `/usage`

显示：

- token 分桶
- 估算成本
- 如果可用则显示实际成本
- 成本状态
- 定价来源

### `/insights`

聚合：

- 实际成本总计
- 仅估算总计
- 未知成本会话数
- 包含成本会话数

## 配置与覆盖

在配置中添加用户可配置的定价覆盖：

```yaml
pricing:
  mode: hybrid
  sync_on_startup: true
  sync_interval_hours: 12
  overrides:
    - provider: openrouter
      model: anthropic/claude-opus-4.6
      billing_mode: custom_contract
      input_cost_per_million: 4.25
      output_cost_per_million: 22.0
      cache_read_cost_per_million: 0.5
      cache_write_cost_per_million: 6.0
  included_routes:
    - provider: copilot
      model: "*"
    - provider: codex-subscription
      model: "*"
```

对于匹配的计费路径，覆盖必须优先于目录默认值。

## 推出计划

### 阶段 1

- 添加规范用量模型
- 在 `run_agent.py` 中拆分缓存 token 分桶
- 停止对缓存膨胀的 prompt 总量进行定价
- 在改进后端计算的同时保留当前 UI

### 阶段 2

- 添加路径感知的定价目录
- 集成 OpenRouter models API 同步
- 添加 `estimated` vs `included` vs `unknown`

### 阶段 3

- 添加 OpenRouter generation 成本的对账
- 添加实际成本持久化
- 更新 `/insights` 以优先使用实际成本

### 阶段 4

- 添加直连 OpenAI 和 Anthropic 的对账路径
- 添加用户覆盖和合同定价
- 添加定价同步 CLI 命令

## 测试策略

为以下添加测试：

- OpenAI 缓存 token 减法
- Anthropic 缓存读/写分离
- OpenRouter 估算 vs 实际对账
- 订阅支持模型显示 `included`
- 自定义端点显示 `n/a`
- 覆盖优先级
- 过期目录的回退行为

假设启发式定价的现有测试应替换为路径感知的预期。

## 非目标

- 没有官方来源或用户覆盖时的精确企业计费重建
- 为缺少缓存分桶数据的旧会话回填完美的历史成本
- 在请求时抓取任意提供商网页

## 建议

不要扩展现有的 `MODEL_PRICING` 字典。

该路径无法满足产品需求。Hermes 应该迁移到：

- 规范用量标准化
- 路径感知的定价来源
- 估算后对账的成本生命周期
- UI 中的显式确定性状态

这是使"Hermes 定价在可能的情况下由官方来源支持，否则明确标注"这一声明站得住脚的最小架构。
