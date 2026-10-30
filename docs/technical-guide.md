# 技术指标口径

完整版（给 Agent 使用）见 `skills/financial-market-intelligence/references/technical-guide.md`。实现位于 `analysis/technical.py`，图表位于 `visualization/figures.py`。

## 五个必须固定的口径

| 指标 | 口径 | 说明 |
| --- | --- | --- |
| EMA(12/26) | `α = 2/(n+1)`，`adjust=False`，**以首个收盘价为种子** | 与 `pandas.ewm(alpha=α, adjust=False)` 一致；第一根就给出值 |
| RSI(14) | Wilder 平滑：首个均值 = 前 14 个涨跌幅的简单平均，之后 `(prev*(n-1)+x)/n` | 平均跌幅为 0 → RSI = 100；连续横盘 → 50 |
| MACD(12,26,9) | `DIF = EMA12 − EMA26`，`DEA = EMA9(DIF)`，**`Histogram = DIF − DEA`** | 部分软件用 2×(DIF−DEA)，本项目不是 |
| Bollinger(20,2) | 中轨 = SMA20；上下轨 = 中轨 ± 2σ；**σ 用总体标准差 `ddof=0`** | 另给带宽 `(upper−lower)/middle` 与 %B |
| ATR(14) | `TR = max(H−L, |H−Cprev|, |L−Cprev|)`，Wilder 平滑，TR 从第 2 根起算 | 描述历史波动幅度，**不预测波动** |

## 最少数据量

`MIN_BARS = {"sma": 5, "ema": 12, "rsi": 15, "macd": 35, "bollinger": 20, "atr": 15}`。不足时该族整体输出 `status="insufficient_data"`，不会用不足的数据算出"名不副实"的值。

## 输出

- Series 名：`sma_5`、`sma_10`、`sma_20`、`sma_60`、`ema_12`、`ema_26`、`rsi_14`、`macd`、`macd_signal`、`macd_histogram`、`bollinger_middle`、`bollinger_upper`、`bollinger_lower`、`bollinger_bandwidth`、`bollinger_percent_b`、`atr_14`。
- 每根序列含 `params`、`value`、`previous_value`、`change`、`change_pct`、`date`、`history`、`status`、`explanation`。

## 措辞边界

允许：描述当前与历史状态（"价格高于 MA20"、"RSI14 为 61.4"、"MACD 柱体为负"、"带宽扩大"）。
禁止：必然上涨/下跌、确定性买入卖出信号、目标价、保证收益。`summarize()` 的输出会带免责声明。
