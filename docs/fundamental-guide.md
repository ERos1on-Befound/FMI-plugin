# 基本面口径摘要

完整版（给 Agent 使用）见 `skills/financial-market-intelligence/references/fundamental-guide.md`。

## 统一字段与映射

- 三张表统一成 36 个 canonical 字段，定义在 `models/financial_statements.py` 的 `INCOME_FIELDS` / `BALANCE_FIELDS` / `CASHFLOW_FIELDS`，中文口径说明在 `CANONICAL_FIELD_DOCS`。
- 每种数据源有自己的别名表（`FIELD_ALIASES["sec"]`、`["cn_sina"]`、`["hk_em"]`），按优先级匹配。**命中别名才映射；没有就记入 `missing_fields`，不做猜测性映射。**
- 可从其它字段推导的会明确记录在 `derived_fields`，例如 `gross_profit = revenue − cost_of_revenue`、`total_debt = short_term_debt + long_term_debt`。
- CapEx 统一为**正的支出金额**（部分源给负值，取绝对值并记录）。

## 21 个指标

盈利：`gross_margin`、`operating_margin`、`net_margin`、`roe`、`roa`
偿债：`debt_ratio`、`current_ratio`、`quick_ratio`、`interest_coverage`
运营：`asset_turnover`、`receivables_turnover`、`inventory_turnover`
成长：`revenue_growth`、`net_income_growth`、`eps_growth`
现金流：`free_cash_flow`、`cfo_to_net_income`
估值：`pe`、`pb`、`ps`、`dividend_yield`

## 判定规则（不给结论性评价）

- Finding 标签只有 `positive` / `negative` / `neutral` / `insufficient_data` / `not_applicable`，由**明确规则**产出。
- 例：毛利率上升 → `positive`（规则 `gross_margin 上升`）；流动比率 < 1 时对照资产负债率 → `negative`（规则名会写进 `Finding.rule`）。
- 利息保障倍数在利息费用缺失或为 0 时是 `not_applicable`，不是 0。
- **不做行业基准比较**：项目没有可靠的免费行业分类与均值，因此不评价"优秀/糟糕"，也不做同业排名。
- 多期趋势阈值：相对变化 ±5% 以内为 `stable`。

## 估值

- 只在行情币种与财报币种一致、且有可用股价时生成。
- PE 在 EPS ≤ 0 时返回 `not_applicable`（"亏损公司没有可解释的正市盈率"）。
- 只描述数值，不判断买卖。
