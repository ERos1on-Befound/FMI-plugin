# 图表指南

图表模块位于 `services/chart_service.py` 与 `visualization/`，用 Plotly 生成 HTML（可选 PNG/JSON）并落盘。**所有指标都来自 `analysis/technical.py` 等既有计算模块，绘图代码不另算一套公式。**

## 一、图表类型

| chart_type | 数据来源 | 用途 | 主要参数 |
| --- | --- | --- | --- |
| `line` | `MarketService.get_historical_data` | 单标的收盘价走势 | `period`、`interval` |
| `area` | 同上 | 强调区间面积的走势图 | `period`、`interval` |
| `candlestick` | 同上（OHLCV） | K 线，可选成交量副图 | `period`、`interval` |
| `volume` | 同上 | 单独成交量柱 | `period`、`interval` |
| `technical` | 行情 + `compute_indicators` | 指标面板 | `indicators` = `ma`/`macd`/`rsi`/`bollinger`/`atr`，默认 `ma,macd,rsi` |
| `fundamental` | `FundamentalService.get_financials` + `compute_metric` | 财务趋势 | `metrics` = `revenue`/`net_income`/`gross_margin`/`net_margin`/`roe`/`cash_flow`/`free_cash_flow`，默认 `revenue,net_income` |

对比图走 `generate_comparison_chart`：

| metric | 含义 | 归一化 |
| --- | --- | --- |
| `price` / `relative` / `performance` | 多标的相对表现 | `base_100` 时按各自首个可用收盘价归一化为 100 |
| 金额类（`revenue`、`net_income`、`free_cash_flow`…） | 多公司财务规模对比 | 不支持归一化；**跨币种直接拒绝** |
| 比率类（`gross_margin`、`net_margin`、`roe`、`debt_ratio`…） | 多公司比率对比 | 不需要归一化 |

技术面板标题（固定）：`均线系统（收盘价与 MA5/10/20/60）`、`MACD（DIF / DEA / Histogram，柱体 = DIF − DEA）`、`RSI14（30/50/70 仅作参考区间）`、`布林带（20 日，2 倍总体标准差）`、`ATR14（历史波动幅度，不代表预测）`。

## 二、输出与文件

- 目录：`outputs/charts/stock/`（line/area/candlestick/volume）、`outputs/charts/technical/`、`outputs/charts/fundamental/`。
- 命名：`{SYMBOL}_{chart_type}_{YYYYMMDD_HHMMSS}.html`，重名自动追加 `_1`、`_2`，**绝不覆盖已有文件**。
- HTML 内联 plotly.js，单文件可离线打开，不依赖 CDN。
- PNG 由 kaleido 导出（需要本机有 Chromium/Chrome）；失败**不影响 HTML**，`png_path` 为 `null` 并在 `notes` 里写原因。可用 `FMI_ENABLE_PNG_EXPORT=0` 关闭。
- JSON 为 Plotly figure 的序列化结果，便于其他程序复用。

## 三、返回结构（`ChartResult`）

| 字段 | 说明 |
| --- | --- |
| `chart_name`、`symbol`、`asset_name`、`chart_type` | 图表标识 |
| `status` | `ok` / `partial` / `unavailable` |
| `data_range` | 数据覆盖区间（交易日） |
| `source` | 行情/财报来源（sina、akshare、sec…） |
| `generated_at` | 图表生成时间（**不是行情时间**） |
| `html_path`、`png_path`、`json_path` | 真实落盘路径；未生成时为 `null` |
| `metadata` | `symbol`、`asset_name`、`chart_type`、`period`、`interval`、`currency`、`source`、`retrieved_at`、`data_timestamp`、`data_range`、`generated_at`、`fiscal_period`、`reporting_currency`、`reporting_unit`、`points`、`notes` |
| `error` | 失败原因（已中文化） |

## 四、数据不足与不可比

| 情形 | 行为 |
| --- | --- |
| K 线少于 2 根 | 返回 `unavailable`，不生成空白 K 线 |
| 成交量整列为空 | 返回 `unavailable`，**不用 0 伪造** |
| 技术指标历史不足 | 对应面板缺失，`notes` 说明缺多少根 |
| 财务指标全缺失 | 返回 `unavailable` |
| 多标的币种不同且未归一化 | 返回 `unavailable`，提示改用 `base_100` 或比例指标 |
| 财务金额跨币种对比 | 返回 `unavailable`，不做汇率换算 |

## 五、样式约定

- 涨用 `#c0392b`（红）、跌用 `#1e8449`（绿），全项目一致。
- 网格线浅灰、无多余装饰；标题含资产名与代码；副标题含区间与币种。
- 图下方固定注明：来源、行情时间、获取时间、数据状态；财务图另注报告期、报表币种与单位。
- 不只靠颜色传达关键信息：RSI 有 30/50/70 参考线，MACD 柱体正负同时由位置表达。

## 六、示例调用

```python
from financial_intelligence.services import ChartService

charts = ChartService()
charts.generate("AAPL", chart_type="line", period="6mo", interval="1d")
charts.generate("NVDA", chart_type="candlestick", period="3mo", interval="1d")
charts.generate("NVDA", chart_type="technical", period="6mo", indicators="ma,rsi,macd")
charts.generate("AAPL", chart_type="fundamental", metrics="revenue,gross_margin")
charts.comparison("AAPL,MSFT,NVDA", metric="price", period="6mo", normalization="base_100")
```
