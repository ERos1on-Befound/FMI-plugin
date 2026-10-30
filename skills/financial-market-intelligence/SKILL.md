---
name: financial-market-intelligence
description: 通过本地 MCP 服务做轻量金融数据检索与研究：行情与历史K线（A股/港股/美股/商品）、公司财务报表（美股 SEC EDGAR、A股/港股交易所与披露易镜像）、金融公式计算、基本面五维分析、技术指标计算与公司横向对比。需要"查价格、看K线、读财报、算财务指标、分析某公司基本面或技术面、比较两家公司"时使用本技能。所有数值来自免费公开接口，一律按延迟/历史数据处理，不构成投资建议。
---

# Financial Market Intelligence（FMI）

一个**轻量**的金融数据检索与研究技能：14 个 MCP 工具，覆盖四个模块 ——
**数据获取**（行情、历史K线、财务报表）、**金融公式计算**、**基本面分析**、**技术面分析**。

它把「数据从哪来、报告期是哪一段、用什么币种和单位、公式怎么算、结论依据是什么」全部写进返回值，
让模型只做**转述与整理**，不做心算、不编数据、不给买卖建议。

## 1. 什么时候用

| 用户想做的事 | 用本技能 | 用什么 |
| --- | --- | --- |
| 「AAPL 现在多少钱」「腾讯股价」「茅台今天涨了多少」 | ✅ | `get_stock_quote` |
| 「黄金/原油什么价」 | ✅ | `get_asset_quote`（**期货合约参考价**，见第 8 节） |
| 「给我茅台最近三个月的周K」「苹果近一年日线」 | ✅ | `get_historical_data` |
| 「今天大盘什么情况」 | ✅ | `get_market_overview` |
| 「苹果 2025 财年营收多少」「茅台利润表」 | ✅ | `get_company_financials` |
| 「算一下毛利率/ROE/资产负债率」「做一个三年营收 CAGR」 | ✅ | `calculate_financial_metrics` / `calculate_financial_formula` |
| 「分析一下苹果的基本面」「比较苹果和微软的盈利能力」 | ✅ | `analyze_fundamentals` / `compare_companies` |
| 「英伟达近半年技术面」「RSI 多少、MACD 金叉没有」 | ✅ | `get_technical_indicators` / `analyze_technicals` |
| 「画一张茅台近一年 K 线」「把苹果和微软的相对走势画出来」 | ✅ | `generate_financial_chart` / `generate_comparison_chart` |
| 「生成苹果的基本面分析报告」「出一份英伟达技术面报告」 | ✅ | `generate_analysis_report` |
| 「有哪些指标可以算」「你能画哪些图/出哪些报告」 | ✅ | `get_supported_indicators` / `get_available_chart_types` / `get_report_templates` |
| 「该不该买」「预测明天涨跌」「给个目标价」「帮我回测」 | ❌ | 不做投资建议、不做预测、不做回测，只能提供数据、计算与方法说明 |
| 逐笔/盘口五档/Level-2/分钟级实时、行业基准与同业百分位 | ❌ | 免费源不提供，如实说明 |
| 新闻、研报、政策解读 | ❌ | 不在范围内 |

## 2. 十九个工具

### 行情类（第一阶段）

| 工具 | 用途 | 关键参数 | 主要返回 |
| --- | --- | --- | --- |
| `get_server_status` | 健康检查：版本、启用的数据源 | — | `status`、`providers`、`notes` |
| `get_supported_markets` | 支持的市场/资产类型/周期/间隔/代码写法 | — | `markets`、`periods`、`intervals`、`notes` |
| `get_stock_quote` | 单只股票/ETF/指数报价 | `symbol`、`market?` | `Quote` |
| `get_asset_quote` | 通用资产报价（指数、期货、中文别名） | `symbol`、`asset_type?` | `Quote` |
| `get_historical_data` | 历史 OHLCV | `symbol`、`period`、`interval` | `bars`、`count`、`truncated` |
| `get_market_overview` | 四组固定横截面概览（A股/港股/美股/商品） | — | `groups`、`ok_count`、`unavailable_count` |

### 研究与计算类（第二阶段）

| 工具 | 用途 | 关键参数 | 主要返回 |
| --- | --- | --- | --- |
| `get_company_financials` | 三大报表 + 报告期/披露日/币种/单位/来源 | `symbol`、`period_type`、`limit` | `statements[]`（利润表/资产负债表/现金流量表） |
| `calculate_financial_metrics` | 按指标名算财务指标，可多期 | `symbol`、`metrics`、`period_type`、`periods` | `MetricSeries[]` |
| `calculate_financial_formula` | 算单个已注册公式或安全自定义表达式 | `formula_name`、`inputs` | `FormulaResult` |
| `get_technical_indicators` | 技术指标数值 | `symbol`、`period`、`interval`、`indicators` | `IndicatorSeries[]` |
| `analyze_fundamentals` | 五维基本面 + 多期表 + 结构化结论 | `symbol`、`analysis_scope`、`period_type`、`periods` | `FundamentalsResult` |
| `analyze_technicals` | 技术指标 + 历史状态解读 | `symbol`、`period`、`interval`、`indicators` | `TechnicalResult` |
| `compare_companies` | 多公司同指标对比（含可比性检查） | `symbols`、`metrics`、`period_type` | `ComparisonResult` |
| `get_supported_indicators` | 公式/指标/技术指标清单与可用性 | — | `formulas`、`metrics`、`technical`、`data_sources` |

### 图表与报告类（第三阶段）

| 工具 | 用途 | 关键参数 | 主要返回 |
| --- | --- | --- | --- |
| `generate_financial_chart` | 生成单标的图表并落盘 | `symbol`、`chart_type`、`period`、`interval`、`indicators?` | `ChartResult`（`html_path`/`png_path`/`metadata`） |
| `generate_comparison_chart` | 多标的对比图 | `symbols`、`metric`、`period`、`normalization` | `ChartResult` |
| `get_available_chart_types` | 图表类型、技术指标、财务指标、归一化方式、PNG 是否可用 | — | `AvailableChartsResult` |
| `generate_analysis_report` | 生成 Markdown/HTML 报告并落盘 | `symbol`、`report_type`、`output_format`、`include_charts?`、`periods?` | `ReportResult`（`markdown_path`/`html_path`/`charts`/`missing_data`） |
| `get_report_templates` | 四种报告模板及其所需数据 | — | `ReportTemplatesResult` |

`chart_type` 取值：`line`（收盘价折线）、`area`、`candlestick`（K 线，可带成交量副图）、`volume`、`technical`（指标面板，`indicators` 可用 `ma`/`macd`/`rsi`/`bollinger`/`atr`）、`fundamental`（财务趋势，可用 `revenue`/`net_income`/`gross_margin`/`net_margin`/`roe`/`cash_flow`/`free_cash_flow`）。
`report_type` 取值：`fundamental`、`technical`、`comprehensive`、`comparison`（对比报告用 `symbols` 指定多家公司）。

## 3. 工具选择规则

| 用户需求 | 工具 | 注意 |
| --- | --- | --- |
| 看当前价格 | `get_stock_quote` / `get_asset_quote` | 永远说明是延迟行情 |
| 看历史价格 | `get_historical_data` | `1wk`/`1mo` 由日线聚合 |
| 看财报 | `get_company_financials` | 先确认 `period_type` 与报告期 |
| 算财务指标 | `calculate_financial_metrics` | **只算用户问的指标**，不要一次全算 |
| 算自定义公式 | `calculate_financial_formula` | 未注册公式用 `custom_expression`，只支持四则/幂/平方根等安全表达式 |
| 基本面分析 | `analyze_fundamentals` | `analysis_scope` 选 `full` 或某一维度 |
| 技术面分析 | `analyze_technicals` | 指标只描述历史状态 |
| 公司对比 | `compare_companies` | 先看 `comparable`，不可比不要排名 |
| 查可用指标 | `get_supported_indicators` | 不确定指标名时先查 |
| 画图（K 线/走势/指标/财务趋势） | `generate_financial_chart` | 画图前先确认数据够不够；**不要在绘图代码里另算指标** |
| 画多标的对比图 | `generate_comparison_chart` | 不同币种金额不能同轴，改用 `normalization="base_100"` 或换比例指标 |
| 生成分析报告 | `generate_analysis_report` | 只问一个指标时**不要**生成整份报告 |
| 问能画什么/能出什么报告 | `get_available_chart_types` / `get_report_templates` | 不要在回复里凭印象列能力 |

### 调用原则

1. **先取真实数据，再计算**：任何指标都必须来自 `get_company_financials` / `get_historical_data` 的真实返回值。
2. **先检查完整性再算**：`missing_fields`、`insufficient_data`、`not_applicable` 都要如实转述。
3. **只算需要的**：用户只问毛利率就不要顺手把 21 个指标全跑一遍。
4. **复用已取数据**：同一轮对话里同一公司同一报告期不要反复拉，服务端也有缓存（财报 12 小时、行情 60 秒、历史 300 秒）。
5. **不足就说不足**：数据缺失 → `insufficient_data`；公式不适用 → `not_applicable`；源取不到 → `unavailable`。**绝不用 0 填充缺失值。**
6. **不为"完整报告"编数据**：没有估值所需股价/股本就不给 PE/PB/PS，并在限制里说明。
7. **比较先查可比性**：报告期不一致、币种不一致、单位不一致时不要直接横向比，也不要强行排名。
8. **公式由引擎算**：禁止自己心算或用语言模型估算财务比率与技术指标。

## 4. 数据状态语义

### 行情 `data_status`

| 值 | 含义 | 怎么说 |
| --- | --- | --- |
| `delayed` | 有权重延迟的快照（**正常**状态） | 「延迟行情」 |
| `stale` | 行情时间距今过久（>10 天，可能停牌/休市/源不更新） | 「这条数据较旧，行情时间是 …」 |
| `historical` | 历史K线 / 已披露财报 | 「历史数据」 |
| `unknown` | 有价格但拿不到行情时间 | 「价格来自 …，行情时间未知」 |
| `unavailable` | 没有源能给出结果 | 「查不到，原因是 …」 |

**硬规则：永远不要把 `delayed` 说成「实时」。** 本技能从不输出 `realtime`。

### 计算状态 `calculation_status`

| 值 | 含义 |
| --- | --- |
| `ok` | 计算成功，`value` 可用 |
| `insufficient_data` | 缺少必要输入（含 `missing_fields` 说明） |
| `not_applicable` | 公式在语义上不适用（分母为 0、EPS 为负、跨币种估值…），**不是** 0 |
| `unavailable` | 数据源没有给到数据 |

### 结论标签

`Finding.status` 只有五种：`positive` / `negative` / `neutral` / `insufficient_data` / `not_applicable`，
每条都必须带 `rule`（判定规则）与 `explanation`。趋势 `trend` 只有 `rising` / `falling` / `stable` /
`insufficient_data` / `not_applicable`（变化幅度 <5% 记为 stable）。

## 5. 财务数据的三个时间与单位

拿到 `get_company_financials` 后，务必区分：

| 字段 | 含义 | 例子 |
| --- | --- | --- |
| `period_start` / `period_end` | **报告期**（会计期间） | 2024-09-29 → 2025-09-27 |
| `filing_date` | **披露日**（实际申报/公告日期） | 2025-10-31（A股/港股镜像可能为空，见 `notes`） |
| `retrieved_at` | **本次获取时间** | 2026-10-30T12:55:00Z |

其他要点：

- `fiscal_period`：`FY2025`（年报）、`2026Q2`（季报）、`TTM2026-09`（TTM，仅可靠时可标）。
- `unit` + `unit_scale`：原始金额 = 展示值 × `unit_scale`；跨公司/跨市场比较前先统一。
- `currency`：ISO 三字码（USD / CNY / HKD）；**跨币种不生成估值指标**（项目没有免费汇率源，腾讯以人民币报表但以港币交易，因此 PE/PB/PS 会返回 `not_applicable` 并说明原因）。
- `cumulative=true`：A股/港股季报常是**年初至今累计口径**，单季数据需与上一期相减；同比可同期间直接比，环比不可。
- `derived_fields`：由其他科目推导出来的字段（如 `gross_profit=revenue-cost_of_revenue`、`total_debt`），引用时要说明是推导值。
- `missing_fields`：该数据源确实没有的科目，**不等于 0**。

## 6. 支持范围（实测）

| 项目 | 美股 | A股 | 港股 |
| --- | --- | --- | --- |
| 财务数据源 | SEC EDGAR companyfacts（XBRL，10-K/10-Q） | AKShare（交易所定期报告镜像） | AKShare（披露易定期报告镜像） |
| 年报 | ✅ `annual` | ✅ `annual` | ✅ `annual` |
| 季报 | ✅ `quarterly` | ✅ `quarterly`（多为累计口径） | ⚠️ 取决于接口返回的 `DATE_TYPE_CODE` |
| TTM | ❌ | ❌ | ❌ |
| 披露日 `filing_date` | ✅ 取自申报记录 | ⚠️ 镜像无可信逐期公告日，留空并在 notes 说明 | ⚠️ 同 A股 |
| 常见缺失 | 利息费用（部分公司停披露）、预付款项 | 股本、有息负债、折旧摊销 | 研发费用、股本 |

行情/技术面：三市场与商品全覆盖（`1d/1wk/1mo` × `1mo/3mo/6mo/1y/2y/5y`），技术指标需要至少：
SMA 5 根、EMA 12 根、RSI 15 根、MACD 35 根、BOLL 20 根、ATR 15 根日线。

## 7. 输出规则

### 7.1 行情类（沿用第一阶段）

1. 数字用 Markdown 表格承载；长序列只展示最近若干根并说明总数与 `truncated`。
2. 每条数据交代：`source`、`timestamp`（行情时间）、`retrieved_at`（获取时间）、`currency`、`data_status`。
3. 期货写「期货合约参考价」，保留 `status_note`。
4. `price=null` 时禁止补 0、禁止估算、禁止用旧价顶替；转述 `error`。
5. 不说「实时」。
6. `from_cache=true` 说明命中缓存，但**行情时间不变**。
7. 不做投资建议。

### 7.2 基本面默认 10 段

1. **公司概况**：名称、代码、市场、币种、报告期类型
2. **财务概览**：营收、净利润、总资产、总负债、经营现金流（最近期 + 同比）
3. **盈利能力**：毛利率、营业利润率、净利率、ROE、ROA
4. **偿债能力**：资产负债率、流动比率、速动比率、利息保障倍数（不可算就说明原因）
5. **运营效率**：总资产周转率、应收账款周转率、存货周转率（注明期间与平均余额口径）
6. **成长性**：营收/净利/EPS 增长与多期趋势
7. **现金流质量**：OCF、FCF、OCF÷净利（净利 ≤0 时不得据此判断质量）
8. **估值**（数据允许时）：PE/PB/PS/股息率，注明 EPS 口径与是否 TTM；亏损公司标 `not_applicable`
9. **主要观察点**：只描述指标变化与结构特征，不下「优秀/糟糕」结论
10. **数据限制与来源**：报告期、披露日、来源、缺失科目、累计口径、跨准则差异

### 7.3 技术面默认 9 段

1. **标的与时间范围**（含数据最后一根日期）
2. **行情概况**（区间开/高/低/收、成交量特征）
3. **均线系统**（SMA5/10/20/60、EMA12/26 的数值与相对位置）
4. **RSI14**（数值 + 高位/中性/低位区间说明）
5. **MACD**（DIF、DEA、柱体正负）
6. **布林带**（中轨/上轨/下轨/带宽/价格位置）
7. **ATR14**（波动幅度数值）
8. **技术状态总结**（只描述历史状态：价格在 MA20 上方、短均线高于长均线、柱体为正…）
9. **数据限制与来源**（bar 数、来源、最后交易日、指标口径）

只问单个指标时不必铺满 10/9 段，给数值 + 一句位置说明即可。

### 7.4 图表输出规则

1. **先取数再画**：图表工具会自己取真实行情/财报；不要手写数据点，也不要用文字描述代替图。
2. 回复里给出：图表名称、数据区间、来源、行情时间（`data_timestamp`）、**本机文件路径**（`html_path`、成功时的 `png_path`）。
3. 说明路径只在生成它的这台机器上有效，**不要假设对方能直接打开**；需要跨机器分享时让用户自己上传。
4. `status="unavailable"` 时说明原因（数据不足 / 全缺失 / 跨币种），**不要说"图已生成"**。
5. `png_path=null` 说明 PNG 未生成（配置关闭或无 Chromium），HTML 仍然可用。
6. 多标的对比：不同币种或不同价格水平不要放同一纵轴，用 `normalization="base_100"`，并说明"归一化不是汇率调整后的回报"。
7. 图表里的指标数值来自 `analysis.technical`，与工具返回的数值必须一致；不要在文字里给出与图不同的数字。

### 7.5 报告输出规则

1. 报告基于真实数据生成；`status="partial"` 时给出 `missing_data`，并把"数据限制"一节转述给用户。
2. 给出 `markdown_path` 与（若要求）`html_path`、`include_charts` 生成的图表路径。
3. 报告里的"主要观察点"只描述指标状态与结构，**不写投资建议、目标价、评级**。
4. 对比报告必须转述"数据可比性说明"；期间/币种不一致时明确说不可直接比较。
5. 用户只问一个指标时**不要**生成整份报告。
6. `error` 非空且没有文件时，如实说明失败原因，不要声称已生成。

## 8. 代码格式速查

| 市场 | 写法 | 例子 | 中文别名 |
| --- | --- | --- | --- |
| A股/ETF | 6 位 + `.SS`（沪）/ `.SZ`（深） | `600519.SS`、`000001.SZ` | 贵州茅台、平安银行 |
| A股指数 | `.SS` / `.SZ` | `000001.SS`、`399001.SZ`、`000300.SS` | 上证指数、深证成指、沪深300 |
| 港股 | 4-5 位 + `.HK` | `0700.HK`、`9988.HK`、`1810.HK` | 腾讯控股、阿里巴巴 |
| 美股 | 字母代码 | `AAPL`、`NVDA`、`MSFT`、`BRK.B` | 苹果、英伟达、微软 |
| 美股指数 | `^` 开头 | `^GSPC`、`^IXIC`、`^DJI` | 标普500、纳指、道指 |
| 港股指数 | `^` 开头 | `^HSI`、`^HSTECH` | 恒生指数、恒生科技 |
| 大宗商品 | 期货代码 `=F` | `GC=F`、`CL=F`、`BZ=F` | 黄金、WTI原油、布伦特原油 |

易错点：裸 `000001` 按深市股票（平安银行）解释，上证指数要写 `000001.SS`；港股 `700.HK` 自动补零；
`period=1mo` 不能配 `interval=1mo`；`GC=F` 是**期货参考价**，不是现货。

## 9. 失败与降级

| 现象 | 处理 |
| --- | --- |
| 行情条目 `unavailable` | 读 `error` 如实说明，可换等价代码重试一次，**不要编造数字** |
| 概览部分失败 | `unavailable_count` 与各条 `error` 已隔离，逐条说明，不要把失败说成 0 |
| 限流（Too Many Requests / push2 断连） | 说明"数据源临时限流，约 5 分钟后恢复"，建议稍后再查 |
| `filing_date` 为空 | 说明该镜像不提供可信逐期公告日，用 `notes` 里的更新日期补充 |
| 指标 `insufficient_data` | 说明缺哪个科目（看 `missing_fields` / `explanation`） |
| 指标 `not_applicable` | 说明为什么不适用（分母为 0、EPS 为负、跨币种、利息未披露…） |
| `compare_companies` 全部不可比 | 列出 `comparability_notes`，不给排名 |
| 图表 `unavailable` | 说明数据不足的具体原因（K 线太少、成交量全空、指标全缺失、跨币种…），**不生成空图** |
| 图表有 HTML 但无 PNG | 说明 PNG 导出被关闭或本机缺少 Chromium；HTML 仍可打开 |
| 报告 `partial` | 给出 `missing_data`，并把"数据限制"转述清楚 |
| 报告 `unavailable` / `error` 非空 | 说明失败原因，不要声称已生成文件 |
| 技术指标 `insufficient_data` | 说明需要多少根 K 线，建议扩大 `period` |
| 参数被拒（不支持 period/interval、ttm） | 按 `error` 修正参数重试，不要硬编 |

## 10. 典型工作流

1. **「分析苹果基本面」**：`get_company_financials("AAPL","annual",3)` → 需要季度再取 `quarterly`
   → `analyze_fundamentals("AAPL","full")`（或直接一次）→ 按 7.2 的 10 段输出，标注报告期与披露日。
2. **「分析英伟达近半年技术面」**：`analyze_technicals("NVDA", period="6mo", interval="1d")`
   → 检查返回的 `bars` 与各指标 `status` → 按 7.3 的 9 段输出，标注最后交易日与来源。
3. **「比较苹果和微软盈利能力」**：`compare_companies("AAPL,MSFT", metrics="gross_margin,operating_margin,net_margin,roe,roa")`
   → 先看 `comparable` 与 `comparability_notes`（报告期可能一个是 FY2025、一个是 FY2026）
   → 可比才给对比表，不可比就说明期间差异、不做结论性排名。
4. **「计算过去三年营收 CAGR」**：先取三年营收 → 用 `calculate_financial_formula("cagr", {beginning, ending, years})`
   → 确认起止年份与间隔数；起止值为负或不一致时返回 `not_applicable` 并说明。
5. **「经营现金流/净利润，比较近三年」**：`calculate_financial_metrics(symbol, metrics="cfo_to_net_income,free_cash_flow", periods=3)`
   → 逐年列出；净利 ≤0 的年份单独标注"比值不适用"。
6. **「画茅台近一年 K 线」**：`generate_financial_chart("600519.SS", chart_type="candlestick", period="1y", interval="1d")`
   → 检查 `status`；给出数据区间、来源、行情时间与 `html_path`；说明本机路径限制。
7. **「苹果和微软的相对走势」**：`generate_comparison_chart("AAPL,MSFT", metric="price", period="6mo", normalization="base_100")`
   → 说明基准=100、不是汇率调整后的回报；币种不同且未归一化时工具会拒绝，需改参数。
8. **「生成英伟达技术面报告」**：`generate_analysis_report("NVDA", report_type="technical", period="6mo", output_format="html", include_charts=True)`
   → 转述 9 节结构、`missing_data`、图表路径与免责声明；不追加买卖建议。

## 11. 风险提示与边界

- 数据来自**免费公开接口**（新浪财经 / AKShare / SEC EDGAR / yfinance 回退），存在延迟、缺失、
  限流、上游变更风险；关键决策请以交易所公告或付费源为准。
- 财务报表是**已披露的历史数据**，不代表当期经营现状；跨准则（US GAAP / CAS / HKFRS）科目口径不同。
- 期货合约价格受换月、涨跌停与结算规则影响，**不等于现货价格**。
- 技术指标只是对历史价格的统计描述，**不预测未来**；本技能不输出「必然上涨/下跌」「买卖信号」「目标价」「保证收益」。
- 基本面结论只基于真实计算，**不提供行业基准**，因此不会判断某指标"优秀/糟糕"。
- 图表与报告都只是**数据整理**：图表里的走势不代表未来，报告不构成评级或建议。
- 输出文件保存在生成它的那台机器的 `outputs/` 目录下，**路径不保证可被其他机器或 Agent 访问**。
- 每个数字都应能追溯到某次工具调用；**不得凭记忆补全未查到的数据**。
- 本技能只做数据检索、计算与整理，**不构成任何投资建议**；据此操作风险自负。

## 12. 参考文档

- `references/data-policy.md`：数据来源、状态语义、缓存与失败处理策略
- `references/fundamental-guide.md`：五维指标口径、科目映射、可比性与限制
- `references/technical-guide.md`：SMA/EMA/RSI/MACD/BOLL/ATR 的精确公式与参数
- `references/formula-reference.md`：27 个内置公式与安全表达式语法
- `references/chart-guide.md`：六类图表与对比图的参数、输出结构、元数据与限制
- `references/output-format.md`：行情/基本面/技术面/图表/报告的推荐输出格式与禁止表述
