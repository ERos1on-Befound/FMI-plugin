# 技术指标指南（technical-guide）

代码：`src/financial_intelligence/analysis/technical.py`；服务：`services/technical_service.py`。
输入统一为**真实 OHLCV 日线**（`get_historical_data` 的 `bars`），所有数值由 Pandas/NumPy 计算，
模型不得心算、不得估算、不得补充未返回的数值。

## 1. 口径声明（本项目的定义，与其他软件可能有差异）

| 指标 | 参数 | 本项目的定义 | 与其他软件的差异 |
| --- | --- | --- | --- |
| SMA | 5 / 10 / 20 / 60 | 收盘价简单移动平均，需要至少 n 根 | 无 |
| EMA | 12 / 26 | `alpha = 2/(n+1)`，**以第一个收盘价为种子**递推（等价 pandas `ewm(alpha, adjust=False, min_periods=1)`） | 有的软件用 SMA 作种子，早期值会不同 |
| RSI | 14 | Wilder 平滑：首个平均涨/跌幅取前 14 个变化的简单平均，之后 `(prev*(n-1)+x)/n` | 有的软件用 Cutler 的简单平均 |
| MACD | 12 / 26 / 9 | `DIF = EMA12 − EMA26`；`DEA = EMA9(DIF)`；**柱体 = DIF − DEA** | 国内软件常画 `2 × (DIF − DEA)` |
| BOLL | 20 / 2 | 中轨 = SMA20；上下轨 = 中轨 ± 2σ，**σ 为总体标准差（ddof=0）** | 有的软件用样本标准差（ddof=1） |
| ATR | 14 | `TR = max(H−L, |H−C_prev|, |L−C_prev|)`（从第 2 根起算），Wilder 平滑 | 部分软件用 SMA 代替 Wilder |

边界处理：

- RSI：平均跌幅为 0（连续上涨）→ 100；涨跌都为 0（完全横盘）→ 50。
- 数据不足：家族级最小 K 线数为 SMA 5 / EMA 12 / RSI 15 / MACD 35 / BOLL 20 / ATR 15；
  不足时该族整体输出 `status="insufficient_data"`（**不给出半成品的伪值**）。
- 重复日期保留最后一根；`close` 缺失的行丢弃；`high`/`low`/`open` 缺失用 `close` 回填。

## 2. 返回结构

`get_technical_indicators` / `analyze_technicals` 返回 `TechnicalResult`：

| 字段 | 含义 |
| --- | --- |
| `symbol` / `company_name` / `market` / `currency` | 标的与币种 |
| `period` / `interval` | 请求的区间与间隔（`1wk`/`1mo` 由日线聚合） |
| `window_start` / `window_end` / `bars` | 实际使用的起止日期与 K 线根数 |
| `indicators[]` | 每个指标一条 `IndicatorSeries` |
| `summary[]` | 只描述历史状态的若干句 |
| `provenance` | 来源、获取时间、数据状态、口径说明 |
| `data_status` / `error` / `notes` | 状态与限制 |

`IndicatorSeries` 关键字段：`name`、`category`、`params`、`value`（最新值）、`previous_value`、
`change`、`change_pct`、`date`（最新一根日期）、`history[]`（最近 N 根）、`status`、`explanation`。

### series 名称

`sma_5` / `sma_10` / `sma_20` / `sma_60`、`ema_12` / `ema_26`、`rsi_14`、
`macd` / `macd_signal` / `macd_histogram`、`bollinger_middle` / `bollinger_upper` / `bollinger_lower` /
`bollinger_bandwidth` / `bollinger_percent_b`、`atr_14`。

`indicators` 参数既可用族名（`sma`、`macd`、`bollinger`…），也可用具体名（`sma_20`、`macd_histogram`）；
未知名称会抛错并列出可用族。

## 3. 公式

```
SMA_n(t)   = (C_t + C_{t-1} + ... + C_{t-n+1}) / n
alpha      = 2 / (n + 1)
EMA_n(t)   = alpha * C_t + (1 - alpha) * EMA_n(t-1),   EMA_n(0) = C_0
TR(t)      = max(H_t - L_t, |H_t - C_{t-1}|, |L_t - C_{t-1}|)
ATR_14(t)  = (ATR_14(t-1) * 13 + TR(t)) / 14,          首值 = 前 14 个 TR 的均值
RSI_14(t)  = 100 - 100 / (1 + RS),  RS = 平均涨幅 / 平均跌幅（Wilder）
DIF(t)     = EMA_12(t) - EMA_26(t)
DEA(t)     = EMA_9(DIF)(t)
HIST(t)    = DIF(t) - DEA(t)
MID(t)     = SMA_20(t)
UPPER(t)   = MID(t) + 2 * STD_pop(C[t-19..t])
LOWER(t)   = MID(t) - 2 * STD_pop(C[t-19..t])
BANDWIDTH  = (UPPER - LOWER) / MID * 100
%BB(t)     = (C_t - LOWER) / (UPPER - LOWER) * 100
```

## 4. 解读规则（只能描述历史状态）

允许的表述（有数据支持的事实描述）：

- 「价格位于 MA20 上方（收盘 X vs MA20 Y）」
- 「SMA5 高于/低于 SMA20」「EMA12 高于/低于 EMA26」
- 「RSI14 为 N，处于相对高位（>70）/ 中性（30–70）/ 相对低位（<30）区间」——**仅供参考，不是信号**
- 「MACD 柱体为正/为负，DIF 在 DEA 上方/下方」
- 「布林带带宽为 N%，价格位于上轨附近/中轨附近/下轨附近」
- 「ATR14 为 N，描述该区间平均波动幅度」

禁止的表述：

- 「必然上涨 / 将要下跌 / 底部已现 / 突破在即」等任何预测
- 「买入 / 卖出 / 加仓 / 止损 / 目标价」等交易指令
- 「确定性信号 / 稳赚 / 保证收益」
- 把 `insufficient_data` 的指标当成 0 或忽略

用户直接问「能不能买」时：只陈述历史指标状态、波动与风险因素，明确说明不提供买卖建议。

## 5. 常见坑

- 少于所需 K 线时不要解释为"指标为 0"；应说明需要多少根，并建议扩大 `period`。
- `1wk`/`1mo` 是最初的日线聚合而来（开=首、高=最高、低=最低、收=末、量=合计），指标在聚合线上算，与日线结论可能不同。
- 不同市场的交易日历不同，`window_end` 才是真正最后一个交易日。
- 复权口径：本项目使用数据源返回的原始收盘价（`auto_adjust=False`），除权除息日附近指标会出现跳空。
