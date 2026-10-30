# 东方财富（Eastmoney）数据源专项诊断报告

- 项目：financial-market-intelligence（第一阶段 MVP）
- 环境：Windows 11 家庭中文版，Python 3.12.16（项目 venv），AKShare 1.20.8，httpx 0.28.1
- 诊断日期：2026-10-30（北京时间 19:33–19:56）
- 结论状态：**unavailable**（本机 IP 仍被东财 push2 边缘节点限流；已按「限速 + 熔断 + 不重试」实现并把该源降级为可选项）

---

## 1. 结论摘要

| 问题 | 结论 |
| --- | --- |
| 是否为 IP 限流？ | **是**（IP 级节流，时间聚集、随请求次数收紧） |
| 有 HTTP 状态码吗？ | **没有**。服务端直接断开 TCP（`RemoteDisconnected` / `RemoteProtocolError`），无 429、无 `Retry-After` |
| DNS / TLS / 代理 / 请求头 是变量吗？ | 都不是。DNS 全部解析、TLS 1.3 握手正常、无代理环境变量、换请求头与客户端均不改变结果 |
| AKShare 版本兼容问题？ | 不是。1.20.8 的接口仍可用，函数本身正确 |
| 项目自身有重复请求吗？ | 有（历史回退链、批量翻页端点），已修复：单标的端点 + 1s 最小间隔 + 熔断冷却 + 跨进程冷却 |
| 能否修复？ | 不能用合规手段修复（限流来自服务端对出口 IP 的判定），因此如实降级为 `unavailable`，不伪造、不绕过 |
| 影响项目其他功能吗？ | 不影响。报价走新浪文本接口，历史走 AKShare 的新浪/腾讯后端 |

---

## 2. 项目里东财代码的实际调用路径

AKShare 1.20.8 中，本项目会用到的函数与它们真实请求的端点（读源码确认）：

| AKShare 函数 | 实际端点 | 本项目用途 |
| --- | --- | --- |
| `stock_bid_ask_em` | `https://push2.eastmoney.com/api/qt/stock/get` | 原计划 A股单只报价 |
| `stock_zh_a_spot_em` | `https://82.push2.eastmoney.com/api/qt/clist/get`（翻页数千行） | 原计划全市场快照 |
| `stock_zh_index_spot_em` | `https://48.push2.eastmoney.com/api/qt/clist/get` | 原计划指数快照 |
| `stock_zh_a_hist` | `https://push2his.eastmoney.com/api/qt/stock/kline/get` | A股日线 |
| `index_zh_a_hist` | `https://push2his.eastmoney.com/api/qt/stock/kline/get` | A股指数日线 |

`akshare/utils/http.py` 的关键实现（决定了错误形态）：

- `Push2RateLimitError(RateLimitError, requests.ConnectionError)`：只有当 host 匹配
  `^(?:\d+\.)?push2(?:his|ex|delay)?\.eastmoney\.com$` **且**异常链里出现
  `RemoteDisconnected` / `ConnectionResetError` 时才抛；
- 抛错文案：「东方财富 push2 行情服务器断开了连接，通常是短时间内请求过多，当前 IP 被临时限流。
  首次限流一般约 5 分钟后恢复，反复触发后可能需要 40 分钟以上；等待期间不要重复调用东方财富的行情接口，
  否则会延长限流。」
- `request_with_retry(max_retries=3, base_delay=1.0, ...)`：对 push2 限流**立即抛出、不重试**；
  仅对 `{429,500,502,503,504}` 重试。

---

## 3. 诊断证据（按时间顺序）

### 3.1 网络层：DNS / TLS / 代理

`scripts/diagnose_eastmoney.py`（httpx，每端点仅 1 次请求，`trust_env=False`）：

```text
push2.eastmoney.com        A 43.144.251.121   TLSv1.3 TLS_AES_256_GCM_SHA384 CN=*.eastmoney.com  92ms
push2delay.eastmoney.com   A 61.152.229.217
push2his.eastmoney.com     A 120.79.123.125 / 120.79.243.12
82.push2.eastmoney.com     A 61.129.129.196
datacenter-web.eastmoney.com  多 A
代理环境变量：无（HTTP_PROXY / HTTPS_PROXY / ALL_PROXY 均为空）
```

### 3.2 HTTP 层：5 个端点当时全部 200

| 端点 | 耗时 | 结果 |
| --- | --- | --- |
| `push2/api/qt/stock/get?secid=1.600519` | 282ms | 200，`{"rc":0,...,"data":{"f43":139205,"f57":"600519","f58":"贵州茅台","f60":135400,"f86":1793347921,"f169":3805,"f170":281}}` |
| `push2delay/api/qt/stock/get` | 230ms | 200，同结构（`dlmkts:"8,10,128"`） |
| `push2his/api/qt/stock/kline/get` | 130ms | 200，1218 B，`preKPrice=1443.99`，`klines[0]="2025-10-09,1436.00,1436.78,1439.38,1420.00,54918,7845448947.00"` |
| `82.push2/api/qt/clist/get` | 158ms | 200，`total=1642`（翻页端点） |
| `datacenter-web/api/data/v1/get` | 186ms | 200，但业务层 `{"success":false,"message":"服务器繁忙","code":9701}`（真实服务端拒绝，不是限流） |

**同一时刻**（19:35）`ak.stock_bid_ask_em(symbol="600519")` 在 136.6ms 后抛 `Push2RateLimitError`。

### 3.3 变量隔离实验

| 实验 | 脚本 | 结果 |
| --- | --- | --- |
| 只换客户端/请求头 | `scripts/probe_eastmoney_headers.py` | requests 默认头 200/138ms、requests 仅 UA 200/125ms、requests UA+Referer 200/121ms、**httpx UA+Referer 被断开** 181.9ms → 请求头不是变量，客户端库有影响 |
| 只换地址族（IPv4/IPv6） | `scripts/probe_eastmoney_family.py` | requests+IPv4 **200**（1701ms）、requests+IPv6 断开 131.5ms、httpx+IPv4 断开 417.8ms、httpx+IPv6 断开 114.5ms → IPv6 路径先死 |
| 命中率 | `scripts/probe_eastmoney_success_rate.py`（requests/IPv4，6 次，间隔 1.2s） | **0/6 全部 `RemoteDisconnected`**（210–817ms） |

### 3.4 时间聚集特征

```text
19:34  5 个端点（含翻页端点）全部 200
19:35  ak.stock_bid_ask_em 抛 Push2RateLimitError
19:36–19:39  所有探测全部 RemoteDisconnected
19:44  冷却 5 分钟后，本项目新 provider 第 1 次请求仍被断开
```

→ 失败不是「端点失效」也不是「代码写错」，而是**出口 IP 在时间窗内被节流，且节流强度随请求次数上升**。

---

## 4. 根因

1. 东财 `push2*` 边缘节点对同一出口 IP 的请求频率做软限制；
2. 超限时不返回 `429`，而是**直接关闭连接**——所以日志里只有 `RemoteDisconnected`，
   没有状态码可记录，也没有 `Retry-After` 可遵守；
3. 触发点主要是**翻页批量端点**（`clist/get`，`stock_zh_a_spot_em` 的底层，一次拉 1600+ 行）；
4. 已经被拒后继续请求会**延长**封禁窗口（AKShare 源码注释与官方提示一致）。

---

## 5. 已实施的修复（合规范围内）

### 5.1 新增 `src/financial_intelligence/providers/eastmoney.py`

| 措施 | 实现 |
| --- | --- |
| 只打单标的端点 | 报价 `push2/api/qt/stock/get`（`secid=1.600519`），历史 `push2his/api/qt/stock/kline/get`；**永不调用** `clist/get` |
| 限速 | 模块级 `PACER`，请求串行化并保证 ≥ `FMI_EASTMONEY_MIN_INTERVAL`（默认 1.0s）间隔 |
| 不重试 | 每次调用**只发一次** HTTP 请求；失败立即判定，绝不进入重试循环 |
| 熔断 | 连接被断开 → `BREAKER.trip()`，进入 `FMI_EASTMONEY_COOLDOWN`（默认 300s）冷却；冷却期内直接返回 `unavailable`，不产生网络请求 |
| 跨进程冷却 | 冷却状态写入 `~/.fmi/eastmoney_state.json`（`FMI_STATE_DIR` 可改），新建的 CLI/MCP 进程不会重新撞墙 |
| 强制 IPv4 | `httpx.HTTPTransport(local_address="0.0.0.0")`（实测 IPv6 路径被断开） |
| 硬超时 | 复用 `call_with_timeout`（默认 10s） |
| 诚实状态 | 快照一律 `delayed`（`_SNAPSHOT_NOTE`），缺价格 `unavailable` + 原因，绝不填 0 |
| 失败可解释 | `error` 里记录：URL、异常类型、无 HTTP 状态码、最后尝试时间（UTC）、疑似 IP 限流、冷却剩余时间 |

### 5.2 配置与编排

- `config.py`：新增 `enable_eastmoney`、`eastmoney_min_interval`、`eastmoney_cooldown`；
  默认顺序改为 `sina,eastmoney,akshare,yahoo`（A股优先用东财的单标的端点，失败即回退）。
- `providers/akshare.py`：当东财熔断打开时，A股个股历史改为 **新浪后端优先**
  （`stock_zh_a_daily` 先于 `stock_zh_a_hist`），避免浪费一次已知会失败的请求。
- `.env.example`：补充 `FMI_ENABLE_EASTMONEY`、`FMI_PROVIDER_ORDER`、
  `FMI_EASTMONEY_MIN_INTERVAL`、`FMI_EASTMONEY_COOLDOWN`。
- `tests/test_eastmoney.py`：35 个离线用例（secid 映射、字段解码、K 线列序、限速、熔断、跨进程冷却）。

### 5.3 明确**没有**做的事

未使用代理 IP 池、未轮换 IP、未启用 VPN、未绕过验证码或登录、未伪造 UA 指纹以规避限制、
未高频并发压测阈值、未对被拒请求反复重试、未为了「测试通过」而伪造行情。

---

## 6. 真实验证结果

### 6.1 东财：`scripts/verify_eastmoney.py`（限定 ≤3 次请求）

实际只发出 **1 次** HTTP 请求（第 2 次被本地熔断拦截，未触网）：

```text
[1/3 A-share quote] FAILED kind=rate_limit
    [eastmoney] 600519.SS: 请求 https://push2.eastmoney.com/api/qt/stock/get 时连接被服务端断开
    （RemoteProtocolError），没有 HTTP 状态码可记录；判定为疑似 IP 限流，已进入 300 秒冷却。
[2/3 index quote] FAILED kind=rate_limit
    [eastmoney] 冷却中（剩余约 300 秒），不发起请求。
    最后一次尝试时间：2026-10-30T11:44:03Z。
breaker_open=True  last_attempt=2026-10-30T11:44:03Z
RESULT: unavailable
```

→ 东财在本机仍不可用；熔断与「不重试」行为符合预期。

### 6.2 替代数据源：`scripts/check_fallback.py`（`FMI_ENABLE_EASTMONEY=false`，对东财零请求）

```text
quote 600519.SS  price=1392.05 prev=1354.0 pct=2.810192 source=sina status=delayed ts=2026-10-30T07:34:59Z
quote 000001.SS  price=3599.0166 prev=3651.4337 pct=-1.435521 source=sina status=delayed ts=2026-10-30T07:36:00Z
quote AAPL       price=348.91 prev=343.83 pct=1.477474 source=sina status=delayed ts=2026-10-30T11:47:03Z
history 000001.SS 1mo/1d count=19 source=akshare last=2026-10-30 close=3599.017
history AAPL      1mo/1d count=23 source=akshare last=2026-10-29 close=348.91
RESULT: alternatives_working
```

### 6.3 关键交叉验证：东财快照的数值是对的

东财在 19:34 返回的贵州茅台快照（`f43=139205`、`f60=135400`、`f170=281`，即 1392.05 / 1354.00 / +2.81%）
与 19:47 新浪独立返回的收盘值（`price=1392.05`、`prev=1354.0`、`pct=2.810192`）**完全一致**。

→ 说明：① 东财字段解码实现（`f43`/`f59`/`f60`/`f86`）是正确的；
② 限流只影响「能否拿到数据」，不影响「拿到的数据是否真实」；
③ 该 provider 一旦网络恢复即可直接用真实数据工作。

---

## 7. 当前状态

| 项 | 状态 |
| --- | --- |
| 东财 provider 代码 | ✅ 已实现并通过 35 个离线用例 |
| 东财真实取数（本机） | ❌ `unavailable`：IP 被 push2 边缘节点节流 |
| 项目整体 | ✅ 正常：报价（新浪）+ 历史（AKShare 新浪/腾讯后端）全部可用，144 tests passed |
| 是否标记为正常源 | 否。默认顺序里仍会先试东财（单标的、1s 间隔、失败即熔断），失败原因如实写入 `error` |

---

## 8. 后续建议（合规替代源）

| 优先级 | 方案 | 说明 |
| --- | --- | --- |
| P1 | 上交所 / 深交所官方公开数据 | 交易所网站提供公开行情文件与统计月报，需遵守其使用条款与频率要求 |
| P1 | 巨潮资讯网（CNINFO） | 官方指定披露平台，适合公告与定期报告类数据，不用于高频行情 |
| P1 | 腾讯行情 `qt.gtimg.cn` | 已验证可用，字段比新浪更丰富（含币种、52 周高低），可作为报价第二源做交叉校验 |
| P2 | 发行人官方公告 / IR 页面 | 用于财报、分红等低频权威数据 |
| P2 | 东财恢复后 | 仅重新启用自己的单标的端点；`clist/get` 批量端点继续禁用 |

不建议的技术路线（已排除）：代理 IP 池、IP 轮换、VPN 规避、指纹伪装、验证码绕过。
这些既违反用户给定的约束，也会让数据来源与合规性变得不可解释。

---

## 9. 复现方式

```powershell
cd "D:\DSH Workspace\FMI工作区\financial-market-intelligence"
$env:PYTHONIOENCODING="utf-8"

# 1) 网络层诊断（每端点 1 次请求）
.\.venv\Scripts\python.exe scripts\diagnose_eastmoney.py

# 2) 限定次数真实验证（≤3 次请求，失败即停）
.\.venv\Scripts\python.exe scripts\verify_eastmoney.py

# 3) 替代源验证（对东财零请求）
.\.venv\Scripts\python.exe scripts\check_fallback.py

# 4) 离线测试（含东财 35 例）
.\.venv\Scripts\python.exe -m pytest -q
```
