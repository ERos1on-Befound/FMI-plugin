# 图表指南

图表由 `services/chart_service.py` 生成，绘图逻辑在 `visualization/figures.py`，样式在 `visualization/theme.py`。图表**只画已经取到并算好的数据**：技术指标一律来自 `analysis/technical.py`，财务比率一律来自 `analysis/financial_ratios.py`，绘图代码里不会另算一套。

## 图表类型

| chart_type | 说明 | 需要的数据 | 备注 |
| --- | --- | --- | --- |
| `line` | 历史价格折线 | 历史 OHLCV | 标题含资产名与代码；页脚含来源、行情时间、获取时间、数据状态 |
| `area` | 历史价格面积图 | 历史 OHLCV | 同上 |
| `candlestick` | K 线图（开高低收） | 历史 OHLCV | 有成交量时自动加成交量副图；少于 2 根 K 线直接报错，不生成空图 |
| `volume` | 成交量柱状图 | 历史 OHLCV 且必须有成交量 | 数据源不提供成交量时返回 `unavailable`，**绝不用 0 伪造** |
| `technical` | 技术指标面板 | 历史 OHLCV | `indicators` 选 `ma` / `macd` / `rsi` / `bollinger` / `atr`；不指定时默认 `ma,macd,rsi` |
| `fundamental` | 财务趋势 | 定期报告 | `indicators` 选 `revenue` / `net_income` / `gross_margin` / `net_margin` / `roe` / `cash_flow` / `free_cash_flow`；默认 `revenue,net_income` |

## 对比图（`generate_comparison_chart`）

- `metric="price"`：多标的价格对比。`normalization="base_100"` 时按 `P_t / P_0 × 100` 归一化（起点 = 100）。
- 币种不同的标的**必须**使用 `base_100`，否则返回 `unavailable`（不会把不同币种放在同一纵轴）；归一化只消除价格水平差异，**不代表汇率调整后的投资回报**。
- `metric` 为财务指标（如 `gross_margin`、`roe`、`revenue`）：比例指标可以横向对比；金额指标跨币种会被拒绝。

## 输出

```
outputs/charts/stock/        # line / area / candlestick / volume / 价格对比
outputs/charts/technical/    # technical 面板
outputs/charts/fundamental/  # fundamental 趋势 / 财务对比
```

- 文件名：`{SYMBOL}_{chart_type}_{YYYYmmdd_HHMMSS}.html`，同名自动加序号，**不会覆盖已有文件**。
- HTML 内联 Plotly，单个文件即可交互（缩放、悬停、图例开关），不依赖 CDN。
- PNG 由 kaleido 导出（需要本机 Chromium/Chrome）；`FMI_ENABLE_PNG_EXPORT=0` 可关闭。PNG 失败不会影响 HTML，只在 `notes` 里说明。
- 同时输出 Plotly JSON 便于程序处理。
- 输出目录可用 `FMI_OUTPUT_DIR` 覆盖。

## 元数据

每个 `ChartResult` 带 `metadata`：`symbol`、`asset_name`、`chart_type`、`period`、`interval`、`currency`、`source`、`retrieved_at`、`data_timestamp`、`data_range`、`generated_at`、`points`。财务图另加 `fiscal_period`、`reporting_currency`、`reporting_unit`。

**生成时间不是行情时间**：`generated_at` 记录出图时刻，`data_timestamp` 记录最后一根 K 线（或最新报告期）的时间，报告与图表都分别展示。

## 数据不足时

- 数据点不足（如 K 线少于 2 根、成交量全为空）→ 返回 `status="unavailable"` 与中文原因，**不生成任何文件**。
- 某项指标在所有期间都缺失 → 该序列不画，并在 `notes` 里列出缺失项；其余序列照常出图（`status="partial"`）。

## 配色约定

- 全项目统一：涨/正 = `#c0392b`（红），跌/负 = `#1e8449`（绿），与 A股习惯一致。
- MACD 柱体定义：`Histogram = DIF − DEA`（不是部分软件的 2×(DIF−DEA)）。
- 布林带：中轨 = SMA20，上下轨 = 中轨 ± 2 倍**总体标准差**（`ddof=0`），图中以阴影带标出。
- RSI 图固定 30/50/70 三条参考线，并注明"仅作参考区间"。
