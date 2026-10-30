# 公式参考（formula-reference）

代码：`src/financial_intelligence/analysis/financial_formulas.py`（注册表 `FORMULAS`，
版本号 `FORMULA_VERSION = "fmi-formulas/1.0"`）。工具：`calculate_financial_formula(formula_name, inputs)`。

- 所有公式通过**注册机制**注册，函数名为 `snake_case`，别名为 `pe` / `pb` / `ps` / `roe` / `roa` / `fcf` 等。
- **不存在 `eval()`**：未注册公式用 `custom_expression`，在 AST 白名单沙箱里求值。
- 返回 `FormulaResult`：`formula_name`、`expression`、`inputs`、`value`、`unit`、`display`、
  `calculation_status`（`ok` / `insufficient_data` / `not_applicable` / `unavailable`）、`explanation`。

## 1. 内置公式（27 个）

### 基础数学

| 名称 | 说明 | 输入 | 公式 | 单位 |
| --- | --- | --- | --- | --- |
| `percentage` | 占比 / 百分比 | `part`, `whole` | part / whole × 100 | % |
| `percentage_change` | 变化百分比 | `current`, `previous` | (current − previous) / \|previous\| × 100 | % |
| `yoy_growth` | 同比增长率 | `current`, `previous` | 同上 | % |
| `qoq_growth` | 环比增长率 | `current`, `previous` | 同上 | % |
| `cagr` | 复合年均增长率 | `beginning`, `ending`, `years` | (ending / beginning)^(1/years) − 1，×100 | % |
| `compound_interest` | 复利终值 | `present_value`, `annual_rate`, `years` | FV = PV × (1 + r)^n | 金额 |
| `annualized_return` | 年化收益率 | `total_return`, `years` | (1 + total_return)^(1/years) − 1 | % |

### 盈利能力

| 名称 | 输入 | 公式 | 单位 |
| --- | --- | --- | --- |
| `gross_margin` | `revenue`, `cost_of_revenue` | (revenue − COGS) / revenue | % |
| `operating_margin` | `operating_income`, `revenue` | 营业利润 / 营收 | % |
| `net_margin` | `net_income`, `revenue` | 净利 / 营收 | % |
| `return_on_equity` | `net_income`，`equity_average` 或 `equity_begin`+`equity_end` | 净利 / 平均股东权益 | % |
| `return_on_assets` | `net_income`，`assets_average` 或 `assets_begin`+`assets_end` | 净利 / 平均总资产 | % |

### 偿债能力

| 名称 | 输入 | 公式 | 单位 |
| --- | --- | --- | --- |
| `debt_ratio` | `total_liabilities`, `total_assets` | 负债 / 资产 | % |
| `current_ratio` | `current_assets`, `current_liabilities` | CA / CL | x |
| `quick_ratio` | `current_assets`, `inventory`, `current_liabilities`, `prepayments`(可选) | (CA − 存货 − 预付款) / CL | x |
| `interest_coverage` | `ebit`, `interest_expense` | EBIT / 利息费用 | x |

### 运营效率

| 名称 | 输入 | 公式 | 单位 |
| --- | --- | --- | --- |
| `asset_turnover` | `revenue`，`assets_average` 或两期 | 营收 / 平均总资产 | x |
| `receivables_turnover` | `revenue`，`receivables_average` 或两期 | 营收 / 平均应收账款 | x |
| `inventory_turnover` | `cost_of_revenue`，`inventory_average` 或两期 | COGS / 平均存货 | x |

### 现金流

| 名称 | 输入 | 公式 | 单位 |
| --- | --- | --- | --- |
| `free_cash_flow` | `operating_cash_flow`, `capital_expenditure` | OCF − \|CapEx\| | 金额 |
| `cfo_to_net_income` | `operating_cash_flow`, `net_income` | OCF / 净利 | x |

### 成长与估值

| 名称 | 输入 | 公式 | 单位 |
| --- | --- | --- | --- |
| `eps_growth` | `eps_current`, `eps_previous` | (cur − prev) / \|prev\| × 100 | % |
| `pe_ratio` | `price`, `eps` | price / EPS | x |
| `pb_ratio` | `price`, `book_value_per_share` | price / 每股净资产 | x |
| `ps_ratio` | `market_cap`, `revenue` | 市值 / 营收 | x |
| `dividend_yield` | `dividend_per_share`, `price` | 每股股息 / 股价 × 100 | % |
| `market_cap` | `price`, `shares_outstanding` | 股价 × 股本 | 金额 |

## 2. 输入约定

- 输入名不区分大小写前后的**空格/连字符/点**（内部统一转下划线），必须是已知输入名，否则报"未识别的输入字段"。
- 数值可以是数字或数字字符串（`"100"`）；`bool`、非数值、`NaN`/`Inf` 会被拒绝。
- 可选输入（如 `prepayments`、`equity_average`）缺失时走简化口径或按缺失处理，不报错。
- 金额类公式不做单位换算：调用方必须保证输入同币种、同单位。

## 3. 不可计算的判定

| 情况 | 返回 |
| --- | --- |
| 必填输入缺失/为空 | `insufficient_data`，`explanation` 列出缺的字段 |
| 分母为 0 | `not_applicable`（"分母为 0，比率不可定义"） |
| `cagr` 起止值 ≤ 0 | `not_applicable`（CAGR 对负值无意义） |
| `pe_ratio` 的 EPS ≤ 0 | `not_applicable`（亏损公司没有可解释的正市盈率） |
| `cfo_to_net_income` 净利 ≤ 0 | `not_applicable`，同时给出原始金额 |
| `interest_coverage` 利息 ≤ 0 | `not_applicable` |
| 结果非有限数 | `not_applicable` |
| 公式名未注册 | `unavailable`，列出可用公式并提示 `custom_expression` |

负基数的增长率会照实计算，但解释里会提示"基期为负时百分比方向可能失真，应同时核对绝对金额"。

## 4. 自定义表达式（`custom_expression`）

```json
{"formula_name": "custom_expression",
 "inputs": {"expression": "(revenue - cost) / revenue * 100",
            "variables": {"revenue": 1000, "cost": 600}}}
```

- 变量也可以直接写在 `inputs` 顶层（`{"expression": "...", "revenue": 1000, "cost": 600}`）。
- 值返回后 `unit` 由调用方自己解释；工具会同时回显 `inputs`。

### 语法白名单

- 允许：数字常量、变量名、`+ - * / ** %`、一元正负号、括号。
- 允许的函数：`sqrt`、`abs`、`min`、`max`、`round`、`log`、`ln`、`log10`、`exp`。
- 禁止：属性访问（`a.b`）、下标（`a[0]`）、推导式、lambda、条件表达式、字符串、关键字参数、
  任何 import / 文件 / 网络 / 系统调用。表达式最长 500 字符。
- 除零、结果非有限数都会报错并返回 `not_applicable`，不会抛给调用方。

> 示例（被拒绝）：`__import__('os').system('...')`、`open('/etc/passwd')`、`a.__class__`。
