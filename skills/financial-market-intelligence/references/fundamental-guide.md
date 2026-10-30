# 基本面指南（fundamental-guide）

本文件说明：统一财务字段、科目映射机制、五维指标的判定规则、可比性与限制。
文件名与代码对应关系：`src/financial_intelligence/models/financial_statements.py`（字段与别名）、
`analysis/financial_ratios.py`（21 个指标）、`analysis/fundamental.py`（判定规则）。

## 1. 统一财务字段（canonical fields）

### 利润表 `income_statement`

| 字段 | 中文 | 说明 |
| --- | --- | --- |
| `revenue` | 营业收入 | 口径按来源原样保留（含/不含其他业务收入可能不同） |
| `cost_of_revenue` | 营业成本 | 与 revenue 口径要匹配 |
| `gross_profit` | 毛利 | 缺失时推导 `revenue - cost_of_revenue` 并记入 `derived_fields` |
| `operating_income` | 营业利润 | US GAAP operating income / CAS 营业利润，口径不完全等同 |
| `pretax_income` | 税前利润 | |
| `net_income` | 净利润 | 含少数股东权益 |
| `net_income_to_parent` | 归母净利润 | A股/港股常见 |
| `eps_basic` / `eps_diluted` | 基本/稀释每股收益 | 优先使用披露值 |
| `interest_expense` | 利息费用 | 不是所有公司都披露（如 AAPL 仅 FY2023 及以前） |
| `income_tax_expense` | 所得税费用 | |
| `rd_expense` / `selling_expense` / `admin_expense` | 研发/销售/管理费用 | |
| `shares_basic` / `shares_diluted` | 加权平均股数 | 自算 EPS 必须用它，不能用期末股本 |

### 资产负债表 `balance_sheet`

`total_assets`、`total_liabilities`、`total_equity`、`equity_to_parent`、`current_assets`、
`current_liabilities`、`inventory`、`cash_and_equivalents`、`accounts_receivable`、`prepayments`、
`short_term_debt`、`long_term_debt`、`total_debt`、`shares_outstanding`。

- `total_liabilities = total_assets - total_equity`、`total_equity = total_assets - total_liabilities`、
  `total_debt = short_term_debt + long_term_debt` 属于**推导值**，出现在 `derived_fields` 里。
- 推导只在输入充分时进行；推导值引用时要写明是推导。

### 现金流量表 `cash_flow_statement`

`operating_cash_flow`、`capital_expenditure`、`investing_cash_flow`、`financing_cash_flow`、
`dividends_paid`、`depreciation_amortization`。

- **CapEx 符号统一**：无论来源记正还是记负，统一为"正的支出金额"；FCF = OCF − |CapEx|。
- A股/港股季报的现金流量表是**年初至今累计**，`cumulative=true` 时不可当单季用。

## 2. 科目映射机制

不同市场的科目名不同，映射表按来源分三套维护（`FIELD_ALIASES` 的 `sec` / `cn_sina` / `hk_em`），
每个 canonical 字段对应一个**有序别名列表**，命中第一个即可。

| canonical | SEC（us-gaap 标签） | A股（新浪镜像中文科目） | 港股（披露易镜像中文科目） |
| --- | --- | --- | --- |
| `revenue` | `RevenueFromContractWithCustomerExcludingAssessedTax` → `Revenues` → `SalesRevenueNet` | 营业总收入 → 营业收入 | 营业额 → 营运收入 |
| `cost_of_revenue` | `CostOfRevenue` → `CostOfGoodsAndServicesSold` | 营业成本 → 营业总成本 | 销售成本 |
| `gross_profit` | `GrossProfit` | 营业毛利 | 毛利 |
| `operating_income` | `OperatingIncomeLoss` | 营业利润 | 经营溢利 |
| `net_income` | `NetIncomeLoss` | 净利润 | 除税后溢利 |
| `net_income_to_parent` | `NetIncomeLossAvailableToCommonStockholdersBasic` | 归属于母公司所有者的净利润 | 股东应占溢利 |
| `eps_diluted` | `EarningsPerShareDiluted` | 稀释每股收益 | 每股摊薄盈利 |
| `total_assets` | `Assets` | 资产总计 | 总资产 |
| `current_assets` | `AssetsCurrent` | 流动资产合计 | 流动资产 |
| `operating_cash_flow` | `NetCashProvidedByUsedInOperatingActivities` | 经营活动产生的现金流量净额 | 经营业务现金净额 |
| `capital_expenditure` | `PaymentsToAcquirePropertyPlantAndEquipment` | 购建固定资产、无形资产和其他长期资产所支付的现金 | 购买物业厂房及设备 |

**映射的三条纪律**：

1. 科目名不同**不等于**数据不存在 → 先查别名表。
2. **含义不同不得强映射**：`Revenues` 对 AAPL 在 FY2018 后停用，不能拿 2018 年的值充当 2025 年营收。
3. 映射不到就是 `missing_fields`，**不用 0 或估算值填充**。

### 已知的缺失（不要硬凑）

| 来源 | 真实缺失字段 |
| --- | --- |
| `sec` | `prepayments`（多数公司无此标签）；`interest_expense`（部分公司停披露） |
| `cn_sina` | `shares_basic` / `shares_diluted` / `shares_outstanding`、`total_debt`、`depreciation_amortization` |
| `hk_em` | `rd_expense`、`shares_basic` / `shares_diluted` / `shares_outstanding` |

## 3. 21 个指标

| 指标名 | 中文 | 维度 | 公式要点 | 单位 |
| --- | --- | --- | --- | --- |
| `gross_margin` | 毛利率 | profitability | (revenue − cost_of_revenue) / revenue | % |
| `operating_margin` | 营业利润率 | profitability | operating_income / revenue | % |
| `net_margin` | 净利率 | profitability | net_income / revenue | % |
| `roe` | ROE | profitability | net_income / 平均股东权益 | % |
| `roa` | ROA | profitability | net_income / 平均总资产 | % |
| `debt_ratio` | 资产负债率 | solvency | total_liabilities / total_assets | % |
| `current_ratio` | 流动比率 | solvency | CA / CL | x |
| `quick_ratio` | 速动比率 | solvency | (CA − inventory − prepayments) / CL | x |
| `interest_coverage` | 利息保障倍数 | solvency | EBIT / interest_expense | x |
| `asset_turnover` | 总资产周转率 | efficiency | revenue / 平均总资产 | x |
| `receivables_turnover` | 应收账款周转率 | efficiency | revenue / 平均应收账款 | x |
| `inventory_turnover` | 存货周转率 | efficiency | COGS / 平均存货 | x |
| `revenue_growth` | 营收增长 | growth | (本期 − 上期) / \|上期\| | % |
| `net_income_growth` | 净利增长 | growth | 同上 | % |
| `eps_growth` | EPS 增长 | growth | 同上（优先稀释 EPS） | % |
| `free_cash_flow` | 自由现金流 | cash_flow | OCF − \|CapEx\| | 币种 |
| `cfo_to_net_income` | 经营现金流/净利润 | cash_flow | OCF / net_income | x |
| `pe` | 市盈率 | valuation | price / EPS | x |
| `pb` | 市净率 | valuation | price / 每股净资产 | x |
| `ps` | 市销率 | valuation | 市值 / revenue | x |
| `dividend_yield` | 股息率 | valuation | 每股股息 / price | % |

**平均余额口径**：优先用两期平均 `(期初 + 期末) / 2`；只有期末值时用期末值并在 `derived`/`explanation`
标注"简化口径（仅用期末余额）"。

**不适用的判定**：

| 情况 | 结果 |
| --- | --- |
| 分母为 0 或缺失 | `insufficient_data` / `not_applicable`（列出缺哪个字段） |
| EPS ≤ 0 | PE `not_applicable`（"亏损公司没有可解释的正市盈率"） |
| net_income ≤ 0 | `cfo_to_net_income` `not_applicable`（同时给出原始金额） |
| interest_expense 缺失或 ≤ 0 | `interest_coverage` `not_applicable` |
| 行情币种 ≠ 财报币种 | PE/PB/PS/股息率 `not_applicable`（无免费汇率源） |
| 没有可用股价 | 估值类 `not_applicable` |
| 只有一期财报 | 增长类与平均余额类退化为简化口径或 `insufficient_data` |

## 4. 结论标签规则（`Finding.status`）

只有五种取值，且必须由代码里的规则给出，**不允许模型自由发挥**：

| 维度 | 规则 | 标签 |
| --- | --- | --- |
| 盈利 | 净利率 > 0 / ≤ 0；ROE > 0 / ≤ 0 | positive / negative |
| 偿债 | 流动比率 ≥ 1 / < 1；利息保障倍数 ≥ 1 / < 1 | positive / negative（只核对覆盖关系） |
| 运营 | 指标可计算 | 一律 neutral（不同行业差异大，不做优劣判断） |
| 成长 | 营收/净利增长率 > 0 / ≤ 0 | positive / negative |
| 现金流 | FCF > 0 / ≤ 0；OCF/净利 ≥ 1 | positive / neutral；净利 ≤ 0 → not_applicable |
| 估值 | 指标可计算 | neutral（只描述，不判断贵贱） |
| 任一维度 | 数据缺失 | insufficient_data / not_applicable |

维度状态聚合：全部 `insufficient_data` → `insufficient_data`；含 `negative` → `negative`；
含 `positive` → `positive`；否则 `neutral`。

趋势 `trend`：相邻两期变化 < 5% 记 `stable`；上升/下降分别 `rising` / `falling`；不足两期 `insufficient_data`。

## 5. 可比性（`compare_companies`）

同一指标跨公司对比前必须核对：

1. **报告期**：`fiscal_period` 必须一致（AAPL FY2025 vs MSFT FY2026 → 不可比）。
2. **币种**：`currency` 必须一致；比率指标可跨币种，金额与估值指标不行。
3. **单位**：`unit_scale` 必须一致（千元 vs 元）。
4. **口径**：累计 vs 单季、合并 vs 母公司、GAAP vs CAS/HKFRS 差异要在 `comparability_notes` 里写明。

不可比时 `ComparisonRow.comparable=false`，**不排名、不下结论**，只说明差异。

## 6. 限制清单（输出时必须转述）

- 没有行业分类与行业基准 → 不评价"优秀/糟糕"，不给同业百分位。
- 一次性损益、资产减值、会计政策变更、非经常性损益会影响单期指标。
- 财务报表是历史披露数据，不代表当期经营现状。
- 跨准则（US GAAP / CAS / HKFRS）科目口径不同，跨市场对比要谨慎。
- `filing_date` 在 A股/港股镜像可能为空（镜像不提供可信逐期公告日）。
