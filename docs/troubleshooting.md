# 故障排查

## MCP 起不来 / Harness 连不上

1. 确认解释器是项目虚拟环境里的那个：
   ```bash
   uv run python -c "import financial_intelligence, sys; print(sys.executable)"
   ```
2. 直接试跑 stdio：
   ```bash
   .venv/Scripts/python -m financial_intelligence.server
   ```
   正常时会在 stderr 打出 `Financial Market Intelligence v0.3.0 starting (transport=stdio, ...)`，然后等待 JSON-RPC；不会退出。
3. 日志只写 stderr（stdout 被 MCP 协议占用）。看不到日志时检查是否被重定向。
4. Harness 配置里 `command` 必须是**真实的 venv 解释器路径**，`args` 用 `["-m", "financial_intelligence.server"]`。不要用 shell 别名或 `uv run`（会多一层进程，某些环境收不到 stdio）。

## 行情取不到

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `data_status="unavailable"` 且 error 含 `yfinance` / `rate_limit` | Yahoo 对本机 IP 限流 | 这不是 bug；`sina` 与 `akshare` 仍在链路上，通常不影响 |
| error 含 `push2` / `断开` | 东方财富限流 | 默认已停用（`FMI_ENABLE_EASTMONEY=0`）；如需开启，冷却期内不要再请求 |
| `error` 里多条原因用 ` | ` 连接 | 所有 provider 都失败了，逐条看原因（非法代码、网络、限流） |
| 港股报价为空 | 代码位数 | 用 `0700.HK` 或 `700.HK`，不要用 `HK700` |

## 财报相关

| 现象 | 说明 |
| --- | --- |
| A股/港股 `filing_date` 为空 | 镜像接口的"公告日期"按报告类型重复，不可靠；已在 notes 说明，`更新日期` 记录在 notes |
| `cumulative=true` | A股/港股季报是年初至今累计口径，单季需与上一期相减 |
| `missing_fields` 里出现 `interest_expense` 等 | 该市场/该期确实没披露，或本项目的映射保守（不做猜测性映射） |
| `period_type="ttm"` 报错 | 免费源不保证能拼出可靠的 TTM；本项目不支持 |
| 市值/PE 为 `not_applicable` | 行情币种 ≠ 财报币种（如腾讯），或没有可用股价 |

## 图表

| 现象 | 说明 |
| --- | --- |
| `status="unavailable"` 且没有 html | 数据不足（K 线 < 2 根、成交量全空、指标全缺失）。不会生成空图 |
| 没有 PNG | `FMI_ENABLE_PNG_EXPORT=0`，或本机没有 Chromium/Chrome。HTML 不受影响，`notes` 里会写原因 |
| 对比图被拒绝 | 不同币种不能放同一纵轴，改用 `normalization="base_100"`（或换比例指标） |
| 技术图少了某些指标 | `indicators` 只传了部分族；默认是 `ma,macd,rsi` |
| 文件太大 | Plotly 3D/散点无关；这里约 4.7 MB/HTML（内联 plotly.js）。可用 `include_plotlyjs="cdn"` 自行改造 |

## 报告

| 现象 | 说明 |
| --- | --- |
| `status="partial"` | 有真实缺失（如 `interest_expense`），报告里用"—"标注并在"数据限制"中说明 |
| 对比报告"不可比" | 报告期或币种不一致；报告会明确写出，不做强行排名 |
| 只有 markdown 没有 html | `output_format` 指定了 `markdown`；需要网页版就再生成一次 `html` |

## 测试

- 全量：`.venv/Scripts/python -m pytest -q`（本机 pytest 9 在 `-q` 下不打印 "N passed"，看退出码即可）。
- 计数：`.venv/Scripts/python -m pytest --co -q`。
- 真实数据检查：`scripts/check_real_data.py`、`scripts/check_financials.py`、`scripts/check_phase3_mcp.py`（这些会真的联网，不要放进 CI）。
