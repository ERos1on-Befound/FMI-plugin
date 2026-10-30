# 数据源现状

> 以下状态是 2026-10-30 在本机实测的结果。数据源随时可能变更、限流或改版，使用前请用 `get_server_status` 或 `scripts/check_real_data.py` 复核。

## 行情与历史

| 数据源 | 适用市场 | 已验证功能 | 状态 | 已知限制 |
| --- | --- | --- | --- | --- |
| 新浪财经（`hq.sinajs.cn`） | A股、港股、美股、美股指数、外盘期货 | 实时快照式报价（名称、现价、昨收、开高低、成交量、行情时间） | ✅ 可用 | 属于**延迟快照**，不能标 realtime；需 `Referer` 头；GBK 编码 |
| AKShare（新浪/交易所/披露源） | A股、港股、美股、美股指数、外盘期货 | 日线历史 OHLCV；A股与港股定期报告 | ✅ 可用 | 部分接口走东方财富 push2（会被限流），本项目已改为优先非 push2 接口；港股财报需 5 位代码 |
| SEC EDGAR companyfacts | 美股 | 10-K/10-Q/20-F/40-F 的 XBRL 原始申报值 | ✅ 可用 | 必须设置合规 `User-Agent`（`FMI_SEC_USER_AGENT`）；标签缺失（如 AAPL 的 `InterestExpense` 只到 FY2023）需如实反映 |
| yfinance（Yahoo） | 全球 | 报价与历史（代码保留为可移植回退） | ⚠️ 本机被限流 | 调用返回 `YFRateLimitError: Too Many Requests`；本项目未做任何绕过 |
| 东方财富 push2 / push2his | A股、港股、美股指数、期货 | 报价、K 线（`providers/eastmoney.py`） | ❌ 停用 / 不可用 | 判定请求过多时**直接断开连接**，不返回 HTTP 状态、不返回 `Retry-After`；触发后持续数分钟到数十分钟。默认 `FMI_ENABLE_EASTMONEY=0`，仅在明确需要时开启，详见 [eastmoney-diagnosis.md](eastmoney-diagnosis.md) |

## 财务数据

| 市场 | 来源 | 覆盖 | 已知缺失 |
| --- | --- | --- | --- |
| 美股（US） | SEC EDGAR `companyfacts`（XBRL） | 利润表、资产负债表、现金流量表、EPS（basic/diluted）、股数 | 利息费用（AAPL 只披露到 FY2023）、预付款项；无 TTM 拼接 |
| A股（CN） | AKShare `stock_financial_report_sina`（定期报告镜像） | 三表、EPS、股数（部分）、分红 | 总股本/加权股数常缺、总债务需推导、折旧摊销缺失、`filing_date` 不可靠（留空） |
| 港股（HK） | AKShare `stock_financial_hk_report_em`（披露易镜像） | 三表、EPS、币种（`CURRENCY` 列） | 研发费用常缺、股数常缺、`filing_date` 留空 |

**为什么没有 `providers/cninfo.py` 与 `providers/hkex.py`**：本项目实际读取的是 AKShare 对交易所/巨潮/披露易定期报告的镜像接口。把 provider 命名成 `cninfo`/`hkex` 会误导来源，因此 A股/港股财报统一由 `providers/akshare.py` 内的 `AkshareFinancialProvider` 提供，`FMI_FINANCIAL_PROVIDER_ORDER` 里的 `cninfo`、`hkex` 只是别名。

## 合规与速率

- 不提供任何代理池、IP 轮换、验证码绕过、指纹伪装或登录态注入。
- 东财 push2 触发限流后**不做重试**（实测重试会延长封禁）；其它源只做有限的失败回退。
- SEC 要求合理的 `User-Agent` 与低频访问；请把 `FMI_SEC_USER_AGENT` 设成含联系方式的值。
- 数据仅用于研究与学习，不构成投资建议；第三方数据源的使用条款与再分发权限由使用者自行确认。

## 快速自查

```bash
# 行情与历史（真实网络，约 10 秒）
uv run python scripts/check_real_data.py

# 三市场财报（真实网络）
uv run python scripts/check_financials.py AAPL annual 2
uv run python scripts/check_financials.py 600519.SS annual 2
uv run python scripts/check_financials.py 00700.HK annual 2

# 19 个 MCP 工具的端到端检查（会真实生成图表与报告）
uv run python scripts/check_phase3_mcp.py
```
