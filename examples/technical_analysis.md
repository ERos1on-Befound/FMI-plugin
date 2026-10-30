# 示例：真实标的技术面分析（NVDA）

> 由 `scripts/make_phase2_examples.py` 调用真实行情生成；生成方式：`analyze_technicals(symbol="NVDA", period="6mo", interval="1d")`。

## 1. 标的与数据范围

- 代码 / 名称：`NVDA` / 英伟达
- 区间 / 间隔：`6mo` / `1d`
- 实际区间：2026-04-30 → 2026-10-29（127 根）
- 来源：akshare；获取时间：2026-10-30T12:54:51+00:00
- 数据状态：`historical`

## 2. 指标数值

| 指标 | 类别 | 参数 | 最新值 | 前值 | 变化 | 日期 | 状态 |
| --- | --- | --- | ---: | ---: | ---: | --- | --- |
| sma_5 | trend | window=5 | 214.788 | 214.498 | 0.29 | 2026-10-29 | ok |
| sma_10 | trend | window=10 | 215.581 | 215.938 | -0.357 | 2026-10-29 | ok |
| sma_20 | trend | window=20 | 222.925 | 223.785 | -0.86 | 2026-10-29 | ok |
| sma_60 | trend | window=60 | 221.9083 | 222.001 | -0.0927 | 2026-10-29 | ok |
| ema_12 | trend | window=12, alpha=0.153846 | 217.5219 | 218.224 | -0.7022 | 2026-10-29 | ok |
| ema_26 | trend | window=26, alpha=0.074074 | 220.3625 | 220.8987 | -0.5362 | 2026-10-29 | ok |
| rsi_14 | momentum | period=14 | 42.2716 | 43.8699 | -1.5983 | 2026-10-29 | ok |
| macd | momentum | fast=12, slow=26, signal=9 | -2.8407 | -2.6747 | -0.166 | 2026-10-29 | ok |
| macd_signal | momentum | fast=12, slow=26, signal=9 | -1.8118 | -1.5546 | -0.2572 | 2026-10-29 | ok |
| macd_histogram | momentum | fast=12, slow=26, signal=9 | -1.0288 | -1.1201 | 0.0913 | 2026-10-29 | ok |
| bollinger_middle | volatility | period=20, std=2 | 222.925 | 223.785 | -0.86 | 2026-10-29 | ok |
| bollinger_upper | volatility | period=20, std=2 | 241.1315 | 241.7834 | -0.6519 | 2026-10-29 | ok |
| bollinger_lower | volatility | period=20, std=2 | 204.7185 | 205.7866 | -1.0681 | 2026-10-29 | ok |
| bollinger_bandwidth | volatility | period=20, std=2 | 16.33% | 16.09% | 0.25% | 2026-10-29 | ok |
| bollinger_percent_b | volatility | period=20, std=2 | 24.56% | 26.65% | -2.09% | 2026-10-29 | ok |
| atr_14 | volatility | period=14 | 5.7224 | 5.9087 | -0.1863 | 2026-10-29 | ok |

## 3. 历史状态解读（只描述，不预测）

- 短期均线 SMA5 低于中期均线 SMA20（214.7880 vs 222.9250）。
- RSI14 为 42.27，仅反映近期涨跌力量对比。
- MACD：DIF -2.8407，DEA -1.8118，柱体为负。
- 布林带带宽 16.33%，最新收盘价位于通道 25% 位置。
- ATR14 为 5.7224，描述历史波动幅度。
- 以上指标基于 2026-04-30 至 2026-10-29 内的 127 个交易日数据，只描述历史状态，不构成买卖建议。

## 4. 口径与限制

- 所有指标只描述历史价格状态，不构成买卖建议，也不预测未来价格。

> 技术指标只是对历史价格的统计描述，不构成买卖建议。
