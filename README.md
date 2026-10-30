# Financial Market Intelligence（FMI）

**A Lightweight Financial Market Data Retrieval & Research Skill** — 一个以 MCP Server 形式交付的
轻量金融数据检索与研究技能：14 个类型化工具，覆盖 **行情/历史K线 → 财务报表 → 金融公式 → 基本面分析 → 技术面分析**
四个模块，服务 **A股 / 港股 / 美股 / 国际大宗商品**，并且每次都如实交代
**数据来源、报告期、行情时间、获取时间、币种、单位与数据状态**。

---

## 1. 简介

FMI 的目标很窄：让模型需要「查价格、拉 K 线、读财报、算指标、做基本面/技术面分析」时，
有一个**可信、可追溯、不编造**的数据入口。它不做数据库、不做前端、不上云，也不做投资建议。

- 交付形态：本地 **MCP Server**（stdio 传输），**14 个工具**
- 四个模块：数据获取（行情 + 历史 + 财报）、金融公式计算、基本面分析、技术面分析
- 覆盖市场：A股、港股、美股、国际大宗商品（期货参考价）
- 依赖成本：**0 元**，无需任何 API Key（SEC 只需一个说明用途的 User-Agent）
- 代码规模：`src/financial_intelligence` 20 个模块，`tests` 10 个测试文件（**321 个用例**）

## 2. 定位

**是**：行情与财务数据检索、金融公式计算、结构化基本面/技术面描述的「数据底座」。

**不是**：

| 不是 | 原因 |
| --- | --- |
| 实时行情终端 | 免费公开接口无法证明实时性，本服务从不输出 `realtime` |
| 交易/下单/回测系统 | 只读数据检索与计算，不接券商、不做策略回测 |
| 投资建议/预测工具 | 不给买卖点、目标价、收益承诺，不做股价预测 |
| 机构级财务数据库 | 只覆盖免费可得的三张表与 21 个比率，没有行业库、没有一致预期 |
| 高可用生产服务 | 上游是免费接口，会限流、会改字段、会失效 |

## 3. 功能

| 能力 | 说明 |
| --- | --- |
| 单只资产报价 | 股票 / ETF / 指数，统一 `Quote` 结构，含涨跌与涨跌幅 |
| 通用资产报价 | 指数、商品期货参考合约、中文别名（「黄金」「上证指数」「纳指」…） |
| 历史 K 线 | `period` 1mo/3mo/6mo/1y/2y/5y × `interval` 1d/1wk/1mo，周/月由日线聚合 |
| 市场概览 | 固定 11 个参考资产，A股 / 港股 / 美股 / 大宗商品四组，逐项独立容错 |
| **财务报表** | 利润表 / 资产负债表 / 现金流量表，统一 30+ canonical 字段，带报告期、披露日、币种、单位倍数、来源、缺失项 |
| **金融公式引擎** | 27 个注册公式 + 安全表达式沙箱（AST 白名单，**无 `eval`**） |
| **财务指标** | 21 个比率指标，可多期、可系列（含趋势判定） |
| **基本面分析** | 五维度（盈利/偿债/运营/成长/现金流）+ 估值，规则驱动结论标签，多期表与限制清单 |
| **技术面分析** | SMA/EMA/RSI/MACD/BOLL/ATR，数值 + 历史状态解读 |
| **公司对比** | 多公司同指标对比，含期间/币种/单位/口径可比性检查，不强行排名 |
| 代码解析 | 大小写不敏感、港股自动补零、`sh600519` 前缀、中文别名、市场/资产类型提示 |
| 缓存 | 报价 60s / 历史 300s / 财报 12h（内存）+ SEC 元数据磁盘缓存，命中时标 `from_cache` |
| 硬超时 | 每次上游调用都有线程级 wall-clock 超时，避免挂死 |
| 失败可解释 | 所有失败都以 `error`/`calculation_status`/`missing_fields` 给出真实原因 |

## 4. 数据源

五个免费公开源，按用途分工（本机实测于 2026-10-30）：

| 数据源 | 角色 | 用途 | 实测状态 |
| --- | --- | --- | --- |
| **新浪财经** `hq.sinajs.cn` | 报价主源 | A股/港股/美股/指数/外盘期货快照 | ✅ 可用，0.07–0.42s |
| **SEC EDGAR** `data.sec.gov` | 美股财报主源 | companyfacts XBRL（10-K/10-Q） | ✅ 可用，`companyfacts` 3.8MB/0.6s |
| **AKShare**（新浪/腾讯/东财后端） | 历史主源 + A股/港股财报 | 历史日线、A股与港股三大报表 | ✅ 可用，历史 0.3–1.0s，财报 0.3–2.1s |
| **东方财富** `push2*.eastmoney.com` | A股报价/历史（单标的） | A股与 A股指数快照 + 日线 | ⚠️ 本机 IP 被限流，按「限速 + 熔断」实现，见 [docs/eastmoney-diagnosis.md](docs/eastmoney-diagnosis.md) |
| **yfinance**（Yahoo Finance） | 可移植回退 | 报价 + 历史 | ❌ 本机 IP 被限流（`YFRateLimitError`），代码保留 |

### 财务数据来源（第二阶段）

| 市场 | 来源 | 覆盖 | 说明 |
| --- | --- | --- | --- |
| 美股 | SEC EDGAR `companyfacts` | 年报（10-K）、季报（10-Q） | 需 `FMI_SEC_USER_AGENT` 写明用途与联系方式；遵守 SEC 访问频率 |
| A股 | AKShare `stock_financial_report_sina` | 年报、季报（多为年初至今累计口径） | 交易所定期报告镜像；镜像的「公告日期」按报告类型重复，**不能当披露日** |
| 港股 | AKShare `stock_financial_hk_report_em` | 年报（`DATE_TYPE_CODE=001`） | 披露易定期报告镜像；币种由 `stock_financial_hk_analysis_indicator_em` 的 `CURRENCY` 列确认 |

**架构决策**：不新建 `providers/cninfo.py`、`providers/hkex.py`。A股/港股的财报实际通过 AKShare
对交易所与披露易定期报告的镜像获得，把 provider 命名为 cninfo/hkex 会**误导数据来源**；
因此命名保持 `akshare`，来源说明写在 `notes` 与本文档里。

### 已知限制（本机实测，非设计缺陷）

- **Yahoo Finance**：直连 chart API 返回 `403`，yfinance 抛 `Too Many Requests`，等待后仍限流。
- **东方财富 push2**：请求过多时**不返回 429，而是直接断开连接**（无状态码、无 `Retry-After`）。
  因此本项目只做「单标的端点 + 最小间隔 1s + 熔断冷却」，**不做盲目重试**，也不用翻页端点。
- 所有快照一律标记为 `delayed`：免费源无法证明实时性。
- 详细证据、修复与验证结果见 [docs/eastmoney-diagnosis.md](docs/eastmoney-diagnosis.md)。

## 5. 目录结构

```text
financial-market-intelligence/
├── README.md
├── LICENSE                          # MIT
├── pyproject.toml                   # 依赖、entry point、pytest 配置
├── uv.lock
├── .python-version                  # 3.12
├── .env.example                     # 可选运行参数（项目不自动加载 .env）
├── .gitignore
├── skills/
│   └── financial-market-intelligence/
│       ├── SKILL.md                 # 技能说明：工具选择、输出规则、边界
│       └── references/
│           ├── data-policy.md       # 数据来源/状态/缓存/失败策略
│           ├── fundamental-guide.md # 字段映射、21 指标、判定规则、可比性
│           ├── technical-guide.md   # 六个技术指标的口径与公式
│           └── formula-reference.md # 27 个公式与安全表达式语法
├── docs/
│   └── eastmoney-diagnosis.md       # 东财限流专项：证据、修复、验证结果
├── examples/
│   ├── sample_market_data.md        # 真实行情示例（脚本生成）
│   ├── fundamental_analysis.md      # 真实公司基本面分析（脚本生成）
│   ├── technical_analysis.md        # 真实标的技术面分析（脚本生成）
│   └── financial_calculation.md     # 真实财务公式计算（脚本生成）
├── scripts/
│   ├── check_real_data.py           # 行情联调检查
│   ├── check_financials.py          # 财报取数检查（指定代码与周期）
│   ├── check_fallback.py            # 东财不可用时的替代源验证
│   ├── check_default_fallback.py    # 默认顺序下东财冷却时的回退验证
│   ├── verify_eastmoney.py          # 东财限定次数（≤3 次请求）真实验证
│   ├── diagnose_eastmoney.py        # 东财端点 DNS/TLS/HTTP 诊断
│   ├── make_example.py              # 生成 examples/sample_market_data.md
│   ├── make_phase2_examples.py      # 生成 examples/ 下三个第二阶段示例
│   ├── probe_sina.py                # 数据源探测（新浪）
│   ├── probe_akshare.py             # 数据源探测（AKShare）
│   ├── probe_yahoo.py               # 数据源探测（yfinance）
│   └── probe_financials*.py         # 财务接口探测（SEC / A股 / 港股）
├── src/financial_intelligence/
│   ├── __init__.py
│   ├── server.py                    # MCP 服务与 14 个工具
│   ├── config.py                    # 设置、日志（只写 stderr）
│   ├── schemas.py                   # 行情数据契约（Quote / HistoricalData / Overview…）
│   ├── models/
│   │   ├── financial_statements.py  # 统一财务模型 + 科目别名映射 + 推导规则
│   │   └── analysis_result.py       # 分析结果契约（Metric / Formula / Technical / Fundamental / Comparison）
│   ├── providers/
│   │   ├── base.py                  # Provider 抽象、硬超时、历史后处理
│   │   ├── sina.py                  # 报价主源
│   │   ├── eastmoney.py             # A股单标的报价/历史（限速 + 熔断）
│   │   ├── akshare.py               # 历史主源 + A股/港股财报
│   │   ├── sec.py                   # 美股财报（SEC companyfacts XBRL）
│   │   └── yahoo.py                 # yfinance 回退
│   ├── services/
│   │   ├── market_service.py        # 行情编排：回退、缓存、概览、失败聚合
│   │   ├── fundamental_service.py   # 财报取数、指标、基本面、公司对比
│   │   ├── technical_service.py     # 历史行情 → 技术指标
│   │   └── calculation_service.py   # 公式计算、能力目录
│   ├── analysis/
│   │   ├── financial_formulas.py    # 27 个注册公式 + AST 白名单表达式引擎
│   │   ├── financial_ratios.py      # 21 个财务指标（多期系列、趋势）
│   │   ├── fundamental.py           # 五维度规则式结论、表格、限制清单
│   │   └── technical.py             # SMA/EMA/RSI/MACD/BOLL/ATR
│   └── utils/
│       ├── symbol_mapper.py         # 代码解析与别名
│       ├── validators.py            # 参数校验
│       ├── bars.py                  # K 线规整、重采样、窗口裁剪
│       └── cache.py                 # 内存 TTL 缓存 + 磁盘 JSON 缓存
└── tests/
    ├── conftest.py
    ├── test_schemas.py              # 行情契约
    ├── test_providers.py            # 代码映射、字段解析、K 线聚合
    ├── test_eastmoney.py            # 东财 secid、字段解码、限速、熔断
    ├── test_services.py             # 行情服务：回退、缓存、容错
    ├── test_tools.py                # 行情类 MCP 工具 + stdio 握手
    ├── test_fundamentals.py         # 财报模型、科目映射、服务层
    ├── test_financial_ratios.py     # 21 个指标的数值与边界
    ├── test_calculations.py         # 27 个公式 + 表达式沙箱
    ├── test_technical.py            # 六个技术指标的数值校验
    └── test_mcp_tools.py            # 8 个研究类 MCP 工具
```

## 6. 环境要求

| 项目 | 要求 | 本机实测 |
| --- | --- | --- |
| 操作系统 | Windows / macOS / Linux | Windows 11 家庭中文版（NT 10.0.26200） |
| Python | **≥ 3.12**（推荐 3.12） | 3.12.16（由 uv 安装） |
| 包管理 | [uv](https://docs.astral.sh/uv/) | 0.13.6 |
| 网络 | 能访问 `hq.sinajs.cn`、`data.sec.gov`、AKShare 后端、东财接口 | 直连可用 |
| 可选 | Git（版本管理） | 2.56.0 |

## 7. 安装与依赖

```powershell
# 1) 安装 uv（Windows PowerShell）
irm https://astral.sh/uv/install.ps1 | iex

# 2) 准备 Python 3.12（如系统没有）
uv python install 3.12

# 3) 安装项目依赖（创建 .venv 并写入 uv.lock）
cd financial-market-intelligence
uv sync
```

核心依赖（全部免费、无需 Key）：

| 包 | 版本 | 用途 |
| --- | --- | --- |
| `mcp` | ≥ 2.3.0 | 官方 MCP Python SDK（`MCPServer` / `Client`） |
| `pydantic` | ≥ 2.12 | 工具输入/输出 schema 与校验 |
| `httpx` | ≥ 0.28 | 新浪行情文本接口、SEC EDGAR 请求 |
| `akshare` | ≥ 1.20 | 历史行情 + A股/港股财报 |
| `pandas` / `numpy` | ≥ 2.2 / ≥ 1.26 | 数据规整、重采样、技术指标计算 |
| `yfinance` | ≥ 1.7 | 可选回退数据源 |
| `pytest` | ≥ 8（dev 组） | 测试 |

## 8. 启动

```powershell
# A. 用 venv 里的解释器直接启动（推荐给 MCP 宿主）
.\.venv\Scripts\python.exe -m financial_intelligence.server

# B. 用安装出来的命令行入口（等价）
.\.venv\Scripts\financial-intelligence-mcp.exe

# C. 用 uv 运行
uv run financial-intelligence-mcp
```

默认传输是 **stdio**（`stdout` 是 MCP 协议线，日志全部走 `stderr`）。
需要调试时也可以跑 HTTP：

```powershell
.\.venv\Scripts\python.exe -m financial_intelligence.server --transport streamable-http --port 8000
```

快速自检（不依赖 MCP 宿主）：

```powershell
.\.venv\Scripts\python.exe scripts\check_real_data.py            # 6 个报价 + 2 段历史 + 概览
.\.venv\Scripts\python.exe scripts\check_financials.py AAPL 3    # 美股财报（SEC）
.\.venv\Scripts\python.exe scripts\check_financials.py 600519.SS 3   # A股财报（AKShare）
.\.venv\Scripts\python.exe scripts\check_financials.py 0700.HK 3     # 港股财报（AKShare）
.\.venv\Scripts\python.exe scripts\make_example.py               # 重新生成行情示例
.\.venv\Scripts\python.exe scripts\make_phase2_examples.py       # 重新生成三个第二阶段示例
```

## 9. 工具（14 个）

### 9.1 行情类（第一阶段）

| # | 工具 | 参数 | 返回 |
| --- | --- | --- | --- |
| 1 | `get_server_status` | — | `ServerStatus`：版本、启用数据源、市场、模块、说明 |
| 2 | `get_supported_markets` | — | `SupportedMarkets`：市场、资产类型、数据源、periods、intervals、代码写法 |
| 3 | `get_stock_quote` | `symbol`、`market?` | `Quote` |
| 4 | `get_historical_data` | `symbol`、`period=1mo`、`interval=1d` | `HistoricalData`（`bars`、`count`、`truncated`） |
| 5 | `get_market_overview` | — | `MarketOverview`（4 组、`ok_count`、`unavailable_count`） |
| 6 | `get_asset_quote` | `symbol`、`asset_type?` | `Quote`（与 3 共用底层服务） |

### 9.2 研究与计算类（第二阶段）

| # | 工具 | 参数 | 返回 |
| --- | --- | --- | --- |
| 7 | `get_company_financials` | `symbol`、`market?`、`period_type=annual`、`limit=5` | `FinancialStatements`（三张表 + 报告期/披露日/币种/单位/来源/缺失项） |
| 8 | `calculate_financial_metrics` | `symbol`、`metrics?`（逗号分隔）、`period_type`、`periods?` | `MetricsResult`（每个指标 = 多期 `MetricSeries` + 趋势） |
| 9 | `calculate_financial_formula` | `formula_name`、`inputs?` | `FormulaResult`（value/unit/display/calculation_status/explanation） |
| 10 | `get_technical_indicators` | `symbol`、`market?`、`period=6mo`、`interval=1d`、`indicators?` | `TechnicalResult`（`IndicatorSeries[]`） |
| 11 | `analyze_fundamentals` | `symbol`、`market?`、`analysis_scope=full`、`period_type`、`periods?` | `FundamentalsResult`（维度结论 + 多期表 + 观察点 + 限制） |
| 12 | `analyze_technicals` | 同 10 | `TechnicalResult`（+ summary 历史状态解读） |
| 13 | `compare_companies` | `symbols`（逗号分隔）、`market?`、`metrics?`、`period_type` | `ComparisonResult`（对比表 + 可比性说明） |
| 14 | `get_supported_indicators` | — | `SupportedIndicatorsResult`（公式/指标/技术指标/数据源状态） |

### 9.3 统一契约

- `Quote`：`symbol`、`name`、`market`、`asset_type`、`currency`、`price`、`previous_close`、`change`、
  `change_percent`、`timestamp`（行情时间）、`retrieved_at`（获取时间）、`source`、`data_status`、`error`。
  `data_status` ∈ `realtime` / `delayed` / `historical` / `stale` / `unavailable` / `unknown`，
  本服务**只会**产出后五个，`realtime` 永不出现。
- `FinancialStatement`：`symbol`、`company_name`、`market`、`currency`、`unit`、`unit_scale`、
  `fiscal_period`（`FY2025` / `2026Q2`）、`period_type`、`cumulative`、`period_start`、`period_end`、
  `filing_date`、`source`、`income_statement{}`、`balance_sheet{}`、`cash_flow_statement{}`、
  `raw_items{}`、`matched_items`、`derived_fields`、`missing_fields`、`data_status`、`retrieved_at`、`notes`。
- `MetricValue`：`value`、`unit`、`display`、`formula`、`inputs`、`input_fields`、`calculation_status`、
  `explanation`、`period`、`source`、`filing_date`、`currency`、`derived`。

**三个时间必须分开**：报告期（`period_start`/`period_end`）、披露日（`filing_date`）、获取时间（`retrieved_at`）。
**缺失值绝不填 0**：缺失进 `missing_fields`，不可计算进 `calculation_status`。

## 10. 财务数据与科目映射

统一字段（30+）：利润表 16 项、资产负债表 14 项、现金流量表 6 项，见
[skills/…/references/fundamental-guide.md](skills/financial-market-intelligence/references/fundamental-guide.md)。

映射机制：`models/financial_statements.py` 的 `FIELD_ALIASES` 按来源分三套（`sec` / `cn_sina` / `hk_em`），
每个字段维护一个**有序别名列表**，命中第一个即可：

| canonical | SEC 标签 | A股科目 | 港股科目 |
| --- | --- | --- | --- |
| `revenue` | `RevenueFromContractWithCustomerExcludingAssessedTax` → `Revenues` → `SalesRevenueNet` | 营业总收入 → 营业收入 | 营业额 → 营运收入 |
| `cost_of_revenue` | `CostOfRevenue` → `CostOfGoodsAndServicesSold` | 营业成本 | 销售成本 |
| `net_income` | `NetIncomeLoss` | 净利润 | 除税后溢利 |
| `operating_cash_flow` | `NetCashProvidedByUsedInOperatingActivities` | 经营活动产生的现金流量净额 | 经营业务现金净额 |

三条纪律：

1. 科目名不同 ≠ 数据不存在 → 先查别名表（例：AAPL 的 `Revenues` 在 FY2018 后停用，必须用 `RevenueFromContract…`）。
2. 含义不同不得强映射 → 映射不到就是 `missing_fields`。
3. 推导值必须标注 → `gross_profit = revenue - cost_of_revenue`、`total_debt = 短期 + 长期`、
   `total_liabilities = assets - equity` 记入 `derived_fields`。

已知缺失（真实、不硬凑）：`sec` 常见缺 `prepayments` 与部分公司的 `interest_expense`；
`cn_sina` 缺股本与 `total_debt`；`hk_em` 缺 `rd_expense` 与股本。

## 11. 公式与指标清单

### 11.1 公式（27 个，`calculate_financial_formula`）

基础数学：`percentage`、`percentage_change`、`yoy_growth`、`qoq_growth`、`cagr`、
`compound_interest`、`annualized_return`
盈利：`gross_margin`、`operating_margin`、`net_margin`、`return_on_equity`、`return_on_assets`
偿债：`debt_ratio`、`current_ratio`、`quick_ratio`、`interest_coverage`
运营：`asset_turnover`、`receivables_turnover`、`inventory_turnover`
现金流：`free_cash_flow`、`cfo_to_net_income`
成长/估值：`eps_growth`、`pe_ratio`、`pb_ratio`、`ps_ratio`、`dividend_yield`、`market_cap`

- 别名：`pe` / `pb` / `ps` / `roe` / `roa` / `fcf` / `cfo_to_ni`。
- 未注册公式请用 `custom_expression`（AST 白名单：数字、变量、`+ - * / ** %`、括号，
  函数仅 `sqrt abs min max round log ln log10 exp`；禁止属性访问、下标、推导式、lambda、字符串、关键字参数）。
  **全程没有 `eval`**。
- 详见 [references/formula-reference.md](skills/financial-market-intelligence/references/formula-reference.md)。

### 11.2 财务指标（21 个，`calculate_financial_metrics`）

`gross_margin`、`operating_margin`、`net_margin`、`roe`、`roa`、`debt_ratio`、`current_ratio`、
`quick_ratio`、`interest_coverage`、`asset_turnover`、`receivables_turnover`、`inventory_turnover`、
`revenue_growth`、`net_income_growth`、`eps_growth`、`free_cash_flow`、`cfo_to_net_income`、
`pe`、`pb`、`ps`、`dividend_yield`。

不可计算时的语义：`insufficient_data`（缺输入）/ `not_applicable`（分母 0、EPS ≤ 0、
利息未披露、跨币种估值…）/ `unavailable`（取不到数据）。**不会返回 0 冒充结果**。

### 11.3 技术指标（6 族，`get_technical_indicators`）

| 族 | 参数 | 口径 |
| --- | --- | --- |
| SMA | 5 / 10 / 20 / 60 | 收盘价简单移动平均 |
| EMA | 12 / 26 | `α=2/(n+1)`，以首个收盘价为种子（`adjust=False`） |
| RSI | 14 | Wilder 平滑，首值取前 14 个涨跌幅简单平均 |
| MACD | 12 / 26 / 9 | `DIF=EMA12−EMA26`，`DEA=EMA9(DIF)`，**柱体 = DIF − DEA** |
| BOLL | 20 / 2 | 中轨 SMA20，上下轨 ± 2σ，**σ 为总体标准差 ddof=0** |
| ATR | 14 | `TR=max(H−L, |H−Cprev|, |L−Cprev|)`，Wilder 平滑 |

最小 K 线数：SMA 5 / EMA 12 / RSI 15 / MACD 35 / BOLL 20 / ATR 15；不足时整族标 `insufficient_data`。
详见 [references/technical-guide.md](skills/financial-market-intelligence/references/technical-guide.md)。

## 12. 调用示例

```text
用户：分析一下苹果的基本面
→ analyze_fundamentals(symbol="AAPL", analysis_scope="full", period_type="annual", periods=3)
→ 按「公司概况/财务概览/盈利/偿债/运营/成长/现金流/估值/观察点/限制」10 段输出

用户：NVDA 近半年技术面怎么样
→ analyze_technicals(symbol="NVDA", period="6mo", interval="1d")
→ 按「标的与区间/行情概况/均线/RSI/MACD/布林/ATR/总结/限制」9 段输出

用户：比较苹果和微软的盈利能力
→ compare_companies(symbols="AAPL,MSFT", metrics="gross_margin,operating_margin,net_margin,roe,roa")
→ 先看 comparable 与 comparability_notes（AAPL FY2025 vs MSFT FY2026 → 不可比，不做排名）

用户：算一下苹果过去三年营收的 CAGR
→ get_company_financials("AAPL", period_type="annual", limit=4) → 取 FY2022/FY2025 营收
→ calculate_financial_formula("cagr", {"beginning": ..., "ending": ..., "years": 3})
```

完整输出样例见 `examples/fundamental_analysis.md`、`examples/technical_analysis.md`、
`examples/financial_calculation.md`（全部由脚本调用真实数据生成）。

## 13. 测试

```powershell
.\.venv\Scripts\python.exe -m pytest                  # 321 passed
.\.venv\Scripts\python.exe -m pytest -m "not slow"     # 跳过 stdio 子进程用例
.\.venv\Scripts\python.exe -m pytest --co -q | Select-String "^tests/"
```

| 文件 | 覆盖内容 | 用例数 |
| --- | --- | --- |
| `tests/test_schemas.py` | 行情契约、`Quote.unavailable`、涨跌幅、NaN→None、`extra=forbid` | 12 |
| `tests/test_providers.py` | 代码映射/别名、四种行情字段布局、K 线规整与重采样、超时分类 | 63 |
| `tests/test_eastmoney.py` | 东财 secid、字段解码、K 线列序、限速、熔断与跨进程冷却 | 35 |
| `tests/test_services.py` | 行情回退、缓存、参数组合校验、概览容错 | 21 |
| `tests/test_tools.py` | 行情类工具 schema/返回、非法输入、stdio 握手 | 13 |
| `tests/test_fundamentals.py` | 科目映射（三源）、推导、`fiscal_label`、缺失表、服务层回退/缓存/compare | 35 |
| `tests/test_financial_ratios.py` | 21 指标数值、平均余额、简化口径、估值阻断、趋势 | 32 |
| `tests/test_calculations.py` | 27 公式、零分母/负基数/缺失、CAGR、表达式沙箱逃逸 | 68 |
| `tests/test_technical.py` | SMA/EMA/RSI/MACD/BOLL/ATR 数值（独立参考实现核对）、边界与历史不足 | 28 |
| `tests/test_mcp_tools.py` | 8 个新工具：可发现、schema、结构化输出、错误隔离 | 14 |

合计 **321 passed**。除 `test_stdio_transport_starts_and_lists_tools` 只做握手外，
所有用例均离线（假 Provider / 真实报文夹具 / 独立参考实现），可在无网环境运行。

## 14. Harness 接入

在 MCP 宿主（如 DeepSeek Harness）里注册一个 **stdio** 服务即可。使用项目 venv 的**绝对路径**，
不要依赖激活状态或环境变量插值：

```json
{
  "mcpServers": {
    "financial-market-intelligence": {
      "command": "D:\\DSH Workspace\\FMI工作区\\financial-market-intelligence\\.venv\\Scripts\\python.exe",
      "args": ["-m", "financial_intelligence.server"],
      "cwd": "D:\\DSH Workspace\\FMI工作区\\financial-market-intelligence",
      "env": {
        "FMI_SEC_USER_AGENT": "your-name/1.0 (research; contact you@example.com)"
      }
    }
  }
}
```

要点：

- `command` 指向 venv 里的 `python.exe`（或 `financial-intelligence-mcp.exe`），**不要**用系统 `python`。
- `FMI_SEC_USER_AGENT` 建议写明用途与联系方式：SEC 要求可识别的 User-Agent，默认值只是占位。
- **不假设**宿主会做 `${VAR}` 插值：需要覆盖参数时，把 `FMI_*` 直接写进 `env` 字段或系统环境变量。
- 修改宿主配置前**先备份**原文件，并写清加了哪个 server、用了哪个解释器。
- 接入后先调 `get_server_status`，再调 `get_supported_indicators` 确认 14 个工具与能力目录都在。
- stdio 模式下日志只写 `stderr`，不会污染协议。

## 15. 限制

1. **不是实时行情**：`data_status` 只会是 `delayed` / `stale` / `unknown` / `unavailable`。
2. **依赖免费接口**：会限流（本机已遇到 Yahoo 与东财 push2）、会改字段、会临时不可用。
3. **东财源可能长期处于冷却**：被断开即写 `~/.fmi/eastmoney_state.json`（`FMI_STATE_DIR` 可改），
   冷却期内所有进程直接返回 `unavailable` 而不发请求；行情仍由新浪 + AKShare 提供。
4. **期货 ≠ 现货**：`GC=F` / `CL=F` / `BZ=F` 是期货合约参考价，带 `status_note`。
5. **周/月 K 线是聚合结果**（开=首、高=最大、低=最小、收=末、量=合计）。
6. **财务数据覆盖有限**：无 TTM（免费源不保证可拼）、A股/港股披露日通常为空、
   部分公司缺利息费用/股本/折旧摊销；这些都会写进 `missing_fields` 或 `notes`。
7. **无行业基准**：不提供同业对比、百分位或行业均值，因此不判断指标"优秀/糟糕"。
8. **跨币种不生成估值**：没有免费汇率源，腾讯（CNY 报表、HKD 交易）的 PE/PB/PS 返回 `not_applicable`。
9. **无鉴权、无多租户、无持久化**：单进程、内存缓存（SEC 元数据除外），进程退出即清空。
10. **不做预测**：技术指标与基本面结论都只描述历史状态与已披露数据。

## 16. 后续计划

| 优先级 | 事项 |
| --- | --- |
| P1 | 多源交叉校验（新浪 + 腾讯），给 `data_status` 增加"多源一致/不一致"标记 |
| P1 | 探索合规的 TTM 拼接（四个季度累计值差分），仅在数据完整时提供 |
| P1 | A股/港股披露日：从交易所公告列表补齐 `filing_date` |
| P2 | 行业分类与基准（只用公开、可引用的分类，避免编造行业均值） |
| P2 | 更多技术指标（KDJ、OBV、ADX），保持同一套口径与测试 |
| P3 | 可选的 streamable-http 部署 + 简单鉴权 |
| P3 | 东财恢复后重新评估批量端点（当前刻意禁用） |

## 17. 免责声明

本项目仅用于**数据检索、计算与技术研究**，所有数据来自第三方免费公开接口，可能存在延迟、缺失或错误。
本项目**不构成任何投资建议**，不提供买卖点、目标价、收益承诺或股价预测；据此进行的任何投资决策及后果，
由使用者自行承担。引用数据时请保留来源、报告期、行情时间与数据状态说明，并遵守各数据源的使用条款。

---

许可：MIT（见 [LICENSE](LICENSE)）。
