# 数据与输出政策

完整的口径说明在 `skills/financial-market-intelligence/references/data-policy.md`（给 Agent 用的版本）。这里是面向开发者的摘要。

## 四条硬规则

1. **缺失 ≠ 0**：取不到的数值一律是 `null`，并在 `error` / `notes` / `missing_fields` 里说明原因。任何地方都不允许用 0 填充。
2. **不把延迟标成实时**：新浪快照、AKShare 历史都是延迟/历史数据，`data_status` 只能是 `delayed` / `historical` / `stale`；全项目没有 `realtime` 赋值路径。
3. **三个时间必须分开**：报告期（`fiscal_period` / `period_end`）、披露日（`filing_date`）、获取时间（`retrieved_at`）；图表的 `generated_at` 也不等于行情时间 `data_timestamp`。
4. **不确定就说不确定**：`insufficient_data`（数据不足）、`not_applicable`（公式不适用）、`unavailable`（数据源取不到）三态区分清楚，不混用。

## 单位与币种

- 金额保留 `currency` 与 `unit_scale`（元 / 千元 / 百万元），跨源比较前先统一。
- A股季报是**年初至今累计口径**，`FinancialStatement.cumulative=True`；单季数据需与上一期累计值相减。
- 估值指标要求行情币种与财报币种一致；不一致（如腾讯以 CNY 报表、以 HKD 交易）时 PE/PB/PS 返回 `not_applicable`，项目没有可靠的免费汇率来源。
- 金额指标不跨币种放同一纵轴。

## 缓存

| 内容 | 默认 TTL | 环境变量 |
| --- | --- | --- |
| 报价 | 60 秒 | `FMI_CACHE_TTL` |
| 历史行情 | 300 秒 | `FMI_HISTORY_CACHE_TTL` |
| 财务报表 | 12 小时 | `FMI_FINANCIAL_CACHE_TTL` |
| SEC ticker 表 | 7 天（磁盘） | — |
| SEC companyfacts | 24 小时（磁盘） | — |

缓存命中会设置 `from_cache=True` 并刷新 `retrieved_at`，但**不会改变 `timestamp`**（行情时间）。

## 失败与降级

- provider 按顺序回退，每个失败原因都会并入最终的 `error`（用 ` | ` 拼接），不会被吞掉。
- 东财 push2 一旦判定限流即熔断，冷却期内直接返回 `rate_limit` 原因，不重复撞墙。
- 所有工具在参数非法或数据源不可用时返回结构化结果（`data_status="unavailable"` + 中文原因），不向 Agent 抛异常。
