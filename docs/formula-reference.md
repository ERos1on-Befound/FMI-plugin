# 公式参考

公式引擎位于 `analysis/financial_formulas.py`，通过注册表暴露 27 个公式，可用 `calculate_financial_formula` 直接调用，或用 `get_supported_indicators` 列出。每个公式返回 `FormulaResult`：`value`、`unit`、`display`、`calculation_status`、`expression`、`inputs`、`explanation`。

完整版（给 Agent 使用）见 `skills/financial-market-intelligence/references/formula-reference.md`。

## 基础数学

| formula_name | 数学定义 | 输入 |
| --- | --- | --- |
| `percentage` | `part / whole × 100` | `part`, `whole` |
| `percentage_change` | `(current − previous) / |previous| × 100` | `current`, `previous` |
| `yoy_growth` | 同上（同比语义） | `current`, `previous` |
| `qoq_growth` | 同上（环比语义） | `current`, `previous` |
| `cagr` | `(ending / beginning) ** (1 / years) − 1` | `beginning`, `ending`, `years` |
| `compound_interest` | `FV = PV × (1 + r) ** n` | `present_value`, `annual_rate`, `years` |
| `annualized_return` | `(1 + total_return) ** (1 / years) − 1` | `total_return`, `years` |

## 盈利

| formula_name | 定义 | 输入 |
| --- | --- | --- |
| `gross_margin` | `(revenue − cost_of_revenue) / revenue` | `revenue`, `cost_of_revenue` |
| `operating_margin` | `operating_income / revenue` | `operating_income`, `revenue` |
| `net_margin` | `net_income / revenue` | `net_income`, `revenue` |
| `return_on_equity` | `net_income / average(equity)` | `net_income`, `equity_average` / `equity_begin`+`equity_end` |
| `return_on_assets` | `net_income / average(assets)` | `net_income`, `assets_average` / `assets_begin`+`assets_end` |
| `eps_growth` | `(eps_current − eps_previous) / |eps_previous| × 100` | `eps_current`, `eps_previous` |

## 偿债

| formula_name | 定义 | 输入 |
| --- | --- | --- |
| `debt_ratio` | `total_liabilities / total_assets × 100` | `total_liabilities`, `total_assets` |
| `current_ratio` | `current_assets / current_liabilities` | `current_assets`, `current_liabilities` |
| `quick_ratio` | `(current_assets − inventory − prepayments) / current_liabilities` | `current_assets`, `inventory`, `current_liabilities`, `prepayments`（可选） |
| `interest_coverage` | `EBIT / interest_expense` | `ebit`, `interest_expense` |

## 运营

| formula_name | 定义 | 输入 |
| --- | --- | --- |
| `asset_turnover` | `revenue / average(total_assets)` | `revenue`, `assets_average` / `assets_begin`+`assets_end` |
| `receivables_turnover` | `revenue / average(accounts_receivable)` | `revenue`, `receivables_average` / `receivables_begin`+`receivables_end` |
| `inventory_turnover` | `cost_of_revenue / average(inventory)` | `cost_of_revenue`, `inventory_average` / `inventory_begin`+`inventory_end` |

## 现金流

| formula_name | 定义 | 输入 |
| --- | --- | --- |
| `free_cash_flow` | `OCF − |CapEx|` | `operating_cash_flow`, `capital_expenditure` |
| `cfo_to_net_income` | `OCF / net_income` | `operating_cash_flow`, `net_income` |

## 估值

| formula_name | 定义 | 输入 |
| --- | --- | --- |
| `pe_ratio` | `price / EPS` | `price`, `eps` |
| `pb_ratio` | `price / 每股净资产` | `price`, `book_value_per_share` |
| `ps_ratio` | `market_cap / revenue` | `market_cap`, `revenue` |
| `dividend_yield` | `每股年股息 / price × 100` | `dividend_per_share`, `price` |
| `market_cap` | `price × shares_outstanding` | `price`, `shares_outstanding` |

## 不可计算的情形

| 情形 | 返回 |
| --- | --- |
| 分母为 0 | `calculation_status="not_applicable"`，说明哪个分母为 0 |
| 输入缺失 / 非数值 / NaN | `insufficient_data`，并列出缺了哪些输入 |
| EPS ≤ 0 求 PE | `not_applicable`（"亏损公司没有可解释的正市盈率"） |
| 利息费用 ≤ 0 求利息保障倍数 | `not_applicable` |
| CAGR 起止值 ≤ 0 | `not_applicable`（跨符号区间没有可解释的增长率） |
| 净利 ≤ 0 求 OCF/净利 | `not_applicable`，同时给出两项原始金额 |

## 别名与自定义表达式

- 别名：`pe`→`pe_ratio`、`pb`→`pb_ratio`、`ps`→`ps_ratio`、`roe`→`return_on_equity`、`roa`→`return_on_assets`、`fcf`→`free_cash_flow`、`cfo_to_ni`→`cfo_to_net_income`。
- 自定义表达式用 `formula_name="custom_expression"`，参数 `{"expression": "(a - b) / b * 100", "variables": {"a": 120, "b": 100}}`，也可把变量放在顶层。
- 表达式在 AST 白名单下求值（只允许数字、`+ - * / % **`、括号、以及 `sqrt/abs/min/max/round/log/ln/log10/exp`），**不使用 `eval`**，拒绝属性访问、下标、推导式、lambda、字符串与任何函数之外的调用，长度上限 500 字符。
