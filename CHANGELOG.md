# Changelog

本项目的所有重要变更记录在此。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [Unreleased]

### 待办
- 更多市场覆盖（如新加坡、日本）与更多免费财报来源的可行性验证。
- 图表导出 SVG / 交互式数据表。
- 可选 PDF 报告（需要额外依赖，暂未引入）。

## [0.3.0] - 2026-10-30

第三阶段：图表生成与分析报告。

### Added
- `ChartService` 与 `visualization/`：Plotly 图表，支持 `line`、`area`、`candlestick`、`volume`、`technical`（`ma`/`macd`/`rsi`/`bollinger`/`atr`）、`fundamental` 六类单标的图，以及多标的对比图（`normalization=base_100`）。
- `ReportService`：`fundamental`、`technical`、`comprehensive`、`comparison` 四类报告，输出 Markdown 与独立 HTML（内联样式，不依赖 CDN），可自动插入图表。
- 5 个新 MCP 工具：`generate_financial_chart`、`generate_comparison_chart`、`get_available_chart_types`、`generate_analysis_report`、`get_report_templates`（工具总数 19）。
- `outputs/charts/{stock,technical,fundamental}` 与 `outputs/reports` 输出目录；文件名带时间戳，重名自动加序号，不覆盖已有文件。
- PNG 导出（kaleido），失败不影响 HTML；JSON 图表快照。
- 文档：`docs/` 8 篇（architecture、data-sources、data-policy、fundamental-guide、technical-guide、formula-reference、chart-guide、troubleshooting）、`references/chart-guide.md`、`references/output-format.md`。
- 测试：`tests/test_visualization.py`（26 例）、`tests/test_reports.py`（14 例），MCP 测试扩到 21 例；全量 368 例。

### Changed
- 东方财富（eastmoney）Provider 默认关闭：该源在本环境被 IP 级限流，push2 接口直接断开 TCP，无法稳定使用。`FMI_ENABLE_EASTMONEY=1` 可手动开启（不推荐）。
- README 重写为面向 GitHub 的首页，移除本机绝对路径，补充数据源状态、工具分类与发布说明。
- `MarketService` 默认数据源顺序不再包含 eastmoney。

### Fixed
- K 线图在关闭成交量副图时误传 `row`/`col` 导致 Plotly 报错。
- 报告把 `TechnicalResult.bars`（整数计数）当行情列表使用的问题。
- 对比图在币种不一致时未拦截原始金额比较的问题。

## [0.2.0] - 2026-10-30

第二阶段：财务数据、公式、基本面与技术面。

### Added
- 统一财务模型（`models/financial_statements.py`）与三源科目映射（SEC EDGAR / A股镜像 / 港股镜像），保留币种、单位、报告期、披露日、缺失与推导字段。
- `SecFinancialProvider`（SEC EDGAR companyfacts/XBRL）、`AkshareFinancialProvider`（A股与港股定期报告镜像）。
- 公式引擎（27 个内置公式 + 安全 AST 表达式求值，不使用 `eval`）与 21 个财务指标、6 类技术指标（SMA/EMA/RSI/MACD/Bollinger/ATR）。
- 基本面五维分析（盈利/偿债/运营/成长/现金流 + 估值）与技术面状态描述；`compare_companies` 带可比性检查。
- 8 个新 MCP 工具：`get_company_financials`、`calculate_financial_metrics`、`calculate_financial_formula`、`get_technical_indicators`、`analyze_fundamentals`、`analyze_technicals`、`compare_companies`、`get_supported_indicators`（工具总数 14）。
- 测试：`test_calculations`、`test_financial_ratios`、`test_technical`、`test_fundamentals`、`test_mcp_tools`；全量 321 例。

### Changed
- 东方财富 Provider 作为可选 Provider 接入（默认曾启用，第三阶段改为默认关闭）。

## [0.1.0] - 2026-10-30

第一阶段：MVP。

### Added
- MCP Server（stdio）与统一 `Quote` / `HistoricalData` 模型，`data_status` 区分 `delayed`/`historical`/`stale`/`unavailable`/`unknown`。
- 数据源：新浪财经（报价，快）、AKShare（历史行情与期货参考价）、yfinance（回退，本环境被 Yahoo 限流）。
- 6 个 MCP 工具：`get_server_status`、`get_supported_markets`、`get_stock_quote`、`get_historical_data`、`get_market_overview`、`get_asset_quote`。
- 代码映射（A股/港股/美股/指数/期货 + 中文别名）、TTL 缓存、Provider 回退与 HTTP 超时。
- 测试 109 例；`skills/financial-market-intelligence/SKILL.md`。
