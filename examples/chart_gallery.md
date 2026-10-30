# 图表示例（真实数据）

生成时间：2026-10-30 21:45 北京时间

以下图表全部由 `ChartService` 用真实行情/财报生成，指标来自 `analysis.technical` 与 `analysis.financial_ratios`，绘图代码不另算公式。HTML 内联 plotly.js，可离线打开。

| 图表 | 标的 | 类型 | 数据区间 | 数据点 | 来源 | 状态 | HTML | PNG |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| AAPL line | `AAPL` | line | 2026-04-30 至 2026-10-29 | 127 | akshare | ok | outputs\charts\stock\AAPL_line_20261030_214509.html | outputs\charts\stock\AAPL_line_20261030_214509.png |
| NVDA candlestick | `NVDA` | candlestick | 2026-07-29 至 2026-10-29 | 66 | akshare | ok | outputs\charts\stock\NVDA_candlestick_20261030_215047.html | outputs\charts\stock\NVDA_candlestick_20261030_215047.png |
| NVDA technical | `NVDA` | technical | 2026-04-30 至 2026-10-29 | 127 | akshare | ok | outputs\charts\technical\NVDA_technical_20261030_215441.html | outputs\charts\technical\NVDA_technical_20261030_215441.png |
| Apple Inc. fundamental | `AAPL` | fundamental | FY2018 至 FY2025 | 8 | sec | ok | outputs\charts\fundamental\AAPL_fundamental_20261030_215603.html | outputs\charts\fundamental\AAPL_fundamental_20261030_215603.png |
| AAPL vs MSFT vs NVDA comparison | 多标的 | comparison | 2026-10-29 至 2026-10-29 | 381 | akshare | ok | outputs\charts\stock\AAPL_comparison_20261030_215723.html | — |

说明：

- 归一化对比（`base_100`）把各标的起点都设为 100，只反映相对涨跌，**不代表汇率调整后的投资回报**。
- 不同币种的金额不能放在同一纵轴；比率指标与归一化走势可以。
- 文件路径是本机路径；其他机器不保证可访问。
