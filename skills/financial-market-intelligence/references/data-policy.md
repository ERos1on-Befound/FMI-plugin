# 数据策略（data-policy）

本文件说明 FMI 的数据来源、状态语义、时间与币种口径、缓存与失败处理规则。
**任何回答都不得违反本文件**；文档与代码不一致时以代码为准，并应先修文档。

## 1. 数据源一览（2026-10 实测）

| 源 | 用途 | 覆盖市场 | 状态 | 说明 |
| --- | --- | --- | --- | --- |
| 新浪财经 `hq.sinajs.cn` | 报价（主源） | CN / HK / US / COMMODITY | available | 0.07–0.42s；GBK 文本接口；**无法证明实时性 → 一律 `delayed`** |
| AKShare | 历史K线、A股/港股财务报表 | CN / HK / US / COMMODITY | available | 历史走新浪/交易日历后端；财报为交易所与披露易定期报告镜像 |
| SEC EDGAR `data.sec.gov` | 美股财务报表 | US | available | companyfacts XBRL；必须带 User-Agent（`FMI_SEC_USER_AGENT`），遵守 SEC 频率要求 |
| 东方财富 push2 / push2his | 报价与历史（CN） | CN | throttled | 出口 IP 被判定请求过多时**直接断开 TCP**（无 429、无 Retry-After）；熔断后不再重试 |
| yfinance | 报价与历史（可移植回退） | US / HK / CN / COMMODITY | rate_limited | 本机 IP 被 Yahoo 限流（`YFRateLimitError`）；保留代码但不作依赖 |

原则：

- **免费公开接口优先**，不使用付费源、代理 IP 池、IP 轮换或任何规避访问限制的手段。
- 已被明确限流的接口**不反复请求**；失败要如实反馈，不允许为"跑通"而伪造数据。
- 不使用数据库、Redis、前端；缓存是内存 + 简单 JSON 文件。

## 2. 数据状态 `data_status`

| 值 | 含义 | 触发条件 |
| --- | --- | --- |
| `delayed` | 有权重延迟的市场快照 | 有价格且行情时间在 `stale_after`（默认 10 天）内 |
| `stale` | 行情时间过旧 | 有价格但行情时间超过 `stale_after` |
| `historical` | 历史序列 / 已披露财报 | 历史K线与财务报表 |
| `unknown` | 无法判定 | 有价格但拿不到行情时间；或数据源未给出足够信息 |
| `unavailable` | 无可用数据 | 所有源都失败；`price=null` 且 `error` 写明真实原因 |

**不输出的值**：`realtime`。免费源不能证明实时性，本技能永远不会把 `delayed` 标成 `realtime`。

## 3. 三个时间必须区分

| 时间 | 字段 | 含义 |
| --- | --- | --- |
| 行情时间 | `Quote.timestamp` / `HistoricalBar.date` | 市场产生该价格的那一刻（UTC ISO 字符串） |
| 获取时间 | `Quote.retrieved_at` / `retrieved_at` | 本次调用从数据源取到数据的时刻（UTC） |
| 披露日 | `FinancialStatement.filing_date` | 财报实际申报/公告日期；A股/港股镜像可能为空 |

缓存命中时 `retrieved_at` 会刷新，但 `timestamp` **不变**——因此不能因为"刚查过"就说数据是最新的。

## 4. 币种与单位

- `currency` 统一 ISO 三字码：`USD` / `CNY` / `HKD`。
- 财报金额同时记录 `unit` 与 `unit_scale`：`原始金额 = 展示值 × unit_scale`。
- **跨币种不生成估值指标**（PE/PB/PS/股息率）：项目没有免费的汇率来源。
  例：腾讯（`0700.HK`）以人民币报表、以港币交易，因此其估值指标返回 `not_applicable` 并说明原因。
- A股/港股季报常为**年初至今累计口径**（`cumulative=true`）：同比可同期间比较，环比需先差分化。

## 5. 缓存策略

| 数据 | TTL | 位置 |
| --- | --- | --- |
| 报价 | `FMI_CACHE_TTL`（默认 60s） | 内存 |
| 历史K线 | `FMI_HISTORY_CACHE_TTL`（默认 300s） | 内存 |
| 财报（规范化后） | `FMI_FINANCIAL_CACHE_TTL`（默认 12h） | 内存 |
| SEC 公司代码表 | 7 天 | 磁盘 JSON（`FMI_CACHE_DIR`，默认 `~/.fmi/cache`） |
| SEC companyfacts | 24 小时 | 磁盘 JSON |

- `ttl <= 0` 表示关闭该缓存。
- 磁盘缓存写失败不得影响主流程（只记日志）。
- 同一轮对话中不要反复拉同一份财报：数据不会变，只会拖慢响应、增加被限流概率。

## 6. 失败处理

| 场景 | 行为 |
| --- | --- |
| 某个源超时 | `ProviderError(kind="timeout")`，换下一个源；最终 `error` 里保留每个源的原因 |
| 源返回限流 | 记为 `rate_limit`；东方财富另加熔断（`FMI_EASTMONEY_COOLDOWN`，默认 300s，跨进程持久化） |
| 字段缺失 | 写进 `missing_fields`；**绝对不用 0 填充** |
| 公式不适用 | `calculation_status="not_applicable"` + 原因（零分母、负 EPS、跨币种、利息未披露…） |
| 数据不足 | `calculation_status="insufficient_data"` + 缺哪些输入 |
| 工具内部异常 | 转成 `data_status="unavailable"` 的结构化结果返回，**不向上抛异常**，服务保持可用 |

失败原因的写法要求：说清「哪个源、什么错、有没有重试、建议怎么办」，不要只写"失败"。

## 7. 合规红线

- 不伪造、不模拟、不用旧值顶替、不用 0 占位。
- 不绕过验证码/登录/访问控制，不做浏览器指纹伪装，不用代理池或 VPN 规避限流。
- 不对已被明确拒绝的请求反复重试。
- 不提供投资建议、买卖信号、目标价、收益保证与回测功能。
