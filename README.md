# Financial Market Intelligence（FMI）

**A Lightweight Financial Market Data Retrieval & Research Skill** — 一个以 MCP Server 形式交付的
轻量金融行情检索技能：把免费公开行情源封装成 6 个类型化工具，覆盖 **A股 / 港股 / 美股 / 国际大宗商品**，
并且每次都如实交代**数据来源、行情时间、获取时间、币种与数据状态**。

---

## 1. 简介

FMI 的目标很窄：让模型在需要「查一个价格、拉一段 K 线、扫一眼大盘」时，有一个**可信、可追溯、不编造**的
数据入口。它不做数据库、不做前端、不上云，也不做投资建议。

- 交付形态：本地 **MCP Server**（stdio 传输），6 个工具
- 数据类型：延迟报价快照 + 历史 OHLCV（日/周/月）
- 覆盖市场：A股、港股、美股、国际大宗商品（期货参考价）
- 依赖成本：**0 元**，无需任何 API Key
- 代码规模：`src/financial_intelligence` 12 个模块，`tests` 4 个测试文件（109 个用例）

## 2. 定位

**是**：行情检索与研究辅助的「数据底座」——把免费源的延迟数据整理成结构化的、可引用的记录。

**不是**：

| 不是 | 原因 |
| --- | --- |
| 实时行情终端 | 免费公开接口无法证明实时性，本服务从不输出 `realtime` |
| 交易/下单系统 | 只读数据检索，不接券商接口 |
| 投资建议/预测工具 | 不给买卖点、目标价、仓位建议 |
| 财务数据库/新闻聚合 | 只做价格与 K 线 |
| 高可用生产服务 | 上游是免费接口，会限流、会改字段、会失效 |

## 3. 功能

| 能力 | 说明 |
| --- | --- |
| 单只资产报价 | 股票 / ETF / 指数，统一 `Quote` 结构，含涨跌与涨跌幅 |
| 通用资产报价 | 指数、商品期货参考合约、中文别名（「黄金」「上证指数」「纳指」…） |
| 历史 K 线 | `period` 1mo/3mo/6mo/1y/2y/5y × `interval` 1d/1wk/1mo，周/月由日线聚合 |
| 市场概览 | 固定 11 个参考资产，A股 / 港股 / 美股 / 大宗商品四组，逐项独立容错 |
| 代码解析 | 大小写不敏感、港股自动补零、`sh600519` 前缀、中文别名、市场/资产类型提示 |
| 缓存 | 报价 60s / 历史 300s 内存 TTL，命中时 `from_cache=true` 且**不刷新行情时间** |
| 硬超时 | 每次上游调用都有线程级 wall-clock 超时，避免挂死 |
| 失败可解释 | 所有失败都以 `error` 字段给出真实原因，多个数据源的失败原因会被合并 |

## 4. 数据源

四个免费公开源，互补使用（本机实测于 2026-10-30）：

| 数据源 | 角色 | 用途 | 实测状态 |
| --- | --- | --- | --- |
| **新浪财经** `hq.sinajs.cn` | 报价主源 | A股/港股/美股/指数/外盘期货快照 | ✅ 可用，0.07–0.42s |
| **东方财富** `push2*.eastmoney.com` | A股报价/历史（单标的） | A股与 A股指数快照 + 日线 | ⚠️ 本机 IP 被限流，代码已按「限速 + 熔断」实现，见 [docs/eastmoney-diagnosis.md](docs/eastmoney-diagnosis.md) |
| **AKShare**（新浪/腾讯/东财后端） | 历史主源 | 各市场历史日线（周/月本地聚合） | ✅ 可用，历史接口 0.3–1.0s |
| **yfinance**（Yahoo Finance） | 可移植回退 | 报价 + 历史 | ❌ 本机 IP 被限流（`YFRateLimitError`），代码保留 |

已知限制（本机实测，非设计缺陷）：

- **Yahoo Finance**：直连 chart API 返回 `403`，yfinance 抛 `Too Many Requests`，等待后仍限流。
  因此 yfinance 在本环境中不可用，仅作为换网络环境后的回退。
- **东方财富 push2 接口**：请求过多时**不返回 429，而是直接断开连接**（无状态码、无 `Retry-After`）。
  2026-10-30 专项排查结论：DNS/TLS/代理/请求头均正常，5 个端点直连单次请求当时全部 200，
  失败呈时间聚集且随请求次数收紧 → 属 IP 级节流。因此本项目对该源只做
  「单标的端点 + 最小间隔 1s + 熔断冷却」，**不做任何盲目重试**，也不使用翻页端点
  （`clist/get`，`stock_zh_a_spot_em` 的底层，是触发封禁的主因）。
- 所有快照一律标记为 `delayed`：免费源无法证明实时性。
- 详细证据、修复与验证结果见 [docs/eastmoney-diagnosis.md](docs/eastmoney-diagnosis.md)。

## 5. 目录结构

```text
financial-market-intelligence/
├── README.md
├── LICENSE                     # MIT
├── pyproject.toml              # 依赖、entry point、pytest 配置
├── uv.lock
├── .python-version             # 3.12
├── .env.example                # 可选运行参数（项目不自动加载 .env）
├── .gitignore
├── skills/
│   └── financial-market-intelligence/
│       └── SKILL.md            # 技能说明：需求→工具对照、输出规则、风险提示
├── docs/
│   └── eastmoney-diagnosis.md  # 东财限流专项：证据、修复、验证结果
├── examples/
│   └── sample_market_data.md   # 真实数据示例（脚本生成，非手写）
├── scripts/
│   ├── check_real_data.py      # 真实数据联调检查
│   ├── check_fallback.py       # 东财不可用时的替代源验证
│   ├── check_default_fallback.py # 默认顺序下东财冷却时的回退验证（零东财请求）
│   ├── verify_eastmoney.py     # 东财限定次数（≤3 次请求）真实验证
│   ├── diagnose_eastmoney.py   # 东财端点 DNS/TLS/HTTP 诊断
│   ├── make_example.py         # 生成 examples/sample_market_data.md
│   ├── probe_sina.py           # 数据源探测（新浪）
│   ├── probe_akshare.py        # 数据源探测（AKShare）
│   └── probe_yahoo.py          # 数据源探测（yfinance）
├── src/financial_intelligence/
│   ├── __init__.py
│   ├── server.py               # MCP 服务与 6 个工具
│   ├── config.py               # 设置、日志（只写 stderr）
│   ├── schemas.py              # Pydantic 数据契约
│   ├── providers/
│   │   ├── base.py             # Provider 抽象、硬超时、历史后处理
│   │   ├── sina.py             # 报价主源
│   │   ├── eastmoney.py        # A股单标的报价/历史（限速 + 熔断）
│   │   ├── akshare.py          # 历史主源 + 期货报价格式回退
│   │   └── yahoo.py            # yfinance 回退
│   ├── services/
│   │   └── market_service.py   # 编排：回退、缓存、概览、失败聚合
│   └── utils/
│       ├── symbol_mapper.py    # 代码解析与别名
│       ├── validators.py       # 参数校验
│       └── bars.py             # K 线规整、重采样、窗口裁剪
└── tests/
    ├── conftest.py
    ├── test_schemas.py         # 数据契约（不编造、不填 0、NaN→None）
    ├── test_providers.py       # 代码映射、字段解析、K 线聚合
    ├── test_eastmoney.py       # 东财 secid、字段解码、限速、熔断、跨进程冷却
    ├── test_services.py        # 回退顺序、缓存、失败聚合、概览计数
    └── test_tools.py           # MCP 工具（内存客户端 + stdio 子进程握手）
```

## 6. 环境要求

| 项目 | 要求 | 本机实测 |
| --- | --- | --- |
| 操作系统 | Windows / macOS / Linux | Windows 11 家庭中文版（NT 10.0.26200） |
| Python | **≥ 3.12**（推荐 3.12） | 3.12.16（由 uv 安装） |
| 包管理 | [uv](https://docs.astral.sh/uv/) | 0.13.6 |
| 网络 | 能访问 `hq.sinajs.cn`、`qt.gtimg.cn`、东财/新浪数据接口 | 直连可用 |
| 可选 | Git（版本管理） | 2.56.0 |

Python 3.14 也能装但**未被本项目测试**；`requires-python = ">=3.12"`，推荐 3.12。

## 7. 安装与依赖

```powershell
# 1) 安装 uv（Windows PowerShell）
irm https://astral.sh/uv/install.ps1 | iex

# 2) 准备 Python 3.12（如系统没有）
uv python install 3.12

# 3) 安装项目依赖（创建 .venv 并写入 uv.lock）
cd financial-market-intelligence
uv sync
```

核心依赖（全部免费、无需 Key）：

| 包 | 版本 | 用途 |
| --- | --- | --- |
| `mcp` | ≥ 2.3.0 | 官方 MCP Python SDK（`MCPServer` / `Client`） |
| `pydantic` | ≥ 2.12 | 工具输入/输出 schema 与校验 |
| `httpx` | ≥ 0.28 | 新浪行情文本接口请求 |
| `akshare` | ≥ 1.20 | 历史行情 |
| `pandas` / `numpy` | ≥ 2.2 / ≥ 1.26 | 数据规整与重采样 |
| `yfinance` | ≥ 1.7 | 可选回退数据源 |
| `pytest` | ≥ 8（dev 组） | 测试 |

`uv sync` 成功标志：`Installed 66 packages`。若出现
`Failed to hardlink files; falling back to full copy` 只是跨盘符回退复制，不影响使用
（可设 `$env:UV_LINK_MODE="copy"` 消除警告）。

## 8. 启动

三种方式，任选其一（都在项目根目录执行）：

```powershell
# A. 用 venv 里的解释器直接启动（推荐给 MCP 宿主）
.\.venv\Scripts\python.exe -m financial_intelligence.server

# B. 用安装出来的命令行入口（等价）
.\.venv\Scripts\financial-intelligence-mcp.exe

# C. 用 uv 运行
uv run financial-intelligence-mcp
```

默认传输是 **stdio**（`stdout` 是 MCP 协议线，日志全部走 `stderr`）。
需要调试时也可以跑 HTTP：

```powershell
.\.venv\Scripts\python.exe -m financial_intelligence.server --transport streamable-http --port 8000
```

快速自检（不依赖 MCP 宿主）：

```powershell
.\.venv\Scripts\python.exe scripts\check_real_data.py      # 打印 6 个报价 + 2 段历史 + 概览
.\.venv\Scripts\python.exe scripts\make_example.py         # 重新生成 examples/sample_market_data.md
```

## 9. 工具

| # | 工具 | 参数 | 返回 |
| --- | --- | --- | --- |
| 1 | `get_server_status` | — | `ServerStatus`：版本、启用数据源、市场、模块、说明 |
| 2 | `get_supported_markets` | — | `SupportedMarkets`：市场、资产类型、数据源、periods、intervals、代码写法 |
| 3 | `get_stock_quote` | `symbol`、`market?` | `Quote` |
| 4 | `get_historical_data` | `symbol`、`period=1mo`、`interval=1d` | `HistoricalData`（`bars`、`count`、`truncated`） |
| 5 | `get_market_overview` | — | `MarketOverview`（4 组、`ok_count`、`unavailable_count`） |
| 6 | `get_asset_quote` | `symbol`、`asset_type?` | `Quote`（与 3 共用底层服务） |

### 统一报价 `Quote`

`symbol`、`name`、`market`、`asset_type`、`currency`、`price`、`previous_close`、`change`、
`change_percent`、`timestamp`（行情时间）、`retrieved_at`（获取时间）、`source`、`data_status`、`error`。

- `data_status` ∈ `realtime` / `delayed` / `historical` / `stale` / `unavailable` / `unknown`
  —— 本服务**只会**产出后五个，`realtime` 永不出现。
- 价格缺失时字段为 `null`，**绝不填 0**，原因写进 `error`。
- `timestamp` 与 `retrieved_at` 是两个不同概念，任何输出都必须分开呈现。

### 代码写法

| 市场 | 写法 | 例子 |
| --- | --- | --- |
| A股 | 6 位 + `.SS`（沪）/ `.SZ`（深） | `600519.SS`、`000001.SZ` |
| A股指数 | `.SS` / `.SZ` | `000001.SS`、`399001.SZ`、`000300.SS` |
| 港股 | 4–5 位 + `.HK` | `0700.HK`、`9988.HK` |
| 美股 | 字母 | `AAPL`、`NVDA`、`MSFT` |
| 美股指数 | `^` | `^GSPC`、`^IXIC`、`^DJI` |
| 港股指数 | `^` | `^HSI`、`^HSTECH` |
| 大宗商品 | `=F` 期货代码 | `GC=F`、`CL=F`、`BZ=F` |

中文别名也可直接用：上证指数、深证成指、沪深300、恒生指数、恒生科技、标普500、纳指、道指、
黄金、WTI原油、布伦特原油，以及常见个股名（贵州茅台、腾讯控股、苹果…）。

## 10. 测试

```powershell
.\.venv\Scripts\python.exe -m pytest          # 109 passed in ~3s
.\.venv\Scripts\python.exe -m pytest -m "not slow"   # 跳过 stdio 子进程用例
```

| 文件 | 覆盖内容 | 用例数 |
| --- | --- | --- |
| `tests/test_schemas.py` | 枚举契约、`Quote.unavailable`、涨跌幅计算、NaN/inf→None、`extra=forbid`、时间字段独立 | 12 |
| `tests/test_providers.py` | 代码映射/别名/冲突、Sina 代码映射、四种字段布局解析、K 线规整与重采样、超时与错误分类、环境变量 | 63 |
| `tests/test_eastmoney.py` | 东财 `secid` 映射、`f43/f59/f60/f86` 解码、K 线列序、限速间隔、熔断冷却、跨进程冷却 | 35 |
| `tests/test_services.py` | 数据源回退、无价视为失败、全失败原因合并、缓存 TTL 与 `from_cache`、参数组合校验、概览逐项容错 | 21 |
| `tests/test_tools.py` | 6 工具注册与 schema、结构化返回、非法输入不抛异常、缺参报错、真实 stdio 子进程握手 | 13 |

合计 **144 passed**（`pytest` 约 3.2s）。说明：单元测试全部离线（假 Provider / 固定报文），
**唯一**会联网的用例是 `test_stdio_transport_starts_and_lists_tools`，它只做 `list_tools` 与
`get_server_status`（不查行情），所以测试可以在无网环境跑通。

## 11. Harness 接入

在 MCP 宿主（如 DeepSeek Harness）里注册一个 **stdio** 服务即可。使用项目 venv 的**绝对路径**，
不要依赖激活状态或环境变量插值：

```json
{
  "mcpServers": {
    "financial-market-intelligence": {
      "command": "D:\\DSH Workspace\\FMI工作区\\financial-market-intelligence\\.venv\\Scripts\\python.exe",
      "args": ["-m", "financial_intelligence.server"],
      "cwd": "D:\\DSH Workspace\\FMI工作区\\financial-market-intelligence"
    }
  }
}
```

要点：

- `command` 指向 venv 里的 `python.exe`（或 `financial-intelligence-mcp.exe`），**不要**用系统 `python`。
- `cwd` 设为项目根，保证 `src` 可被导入（editable 安装后其实任意目录都行，写明更稳）。
- **不假设**宿主会做 `${VAR}` 插值：需要覆盖参数时，把 `FMI_*` 直接写进 `env` 字段或系统环境变量。
- 修改宿主配置前**先备份**原文件，并在改动说明里写清加了哪个 server、用了哪个解释器。
- 接入后先调 `get_server_status` 验证；再调 `get_supported_markets` 看代码写法。
- stdio 模式下日志只写 `stderr`，不会污染协议；如果把 `stderr` 重定向到文件，注意会持续增长。

## 12. 限制

1. **不是实时行情**：所有快照都是延迟数据，`data_status` 只会是 `delayed` / `stale` / `unknown` / `unavailable`。
2. **依赖免费接口**：会限流（本机已遇到 Yahoo 与东财 push2 限流）、会改字段、会临时不可用。
   东财专项结论与替代方案见 [docs/eastmoney-diagnosis.md](docs/eastmoney-diagnosis.md)。
3. **东财源默认启用但可能长期处于冷却**：一旦被断开即写入 `~/.fmi/eastmoney_state.json`
   （可用 `FMI_STATE_DIR` 改位置），冷却期内所有进程都直接返回 `unavailable` 而不发请求；
   行情本身仍由新浪（报价）与 AKShare 的新浪/腾讯后端（历史）提供，不影响其余功能。
3. **期货 ≠ 现货**：`GC=F` / `CL=F` / `BZ=F` 是期货合约参考价，带 `status_note` 说明，不能当现货引用。
4. **周/月 K 线是聚合结果**：由日线在服务端聚合（开=首日、高=最大、低=最小、收=末日、量=合计）。
5. **历史深度受上游限制**：日线从上游可取的最大范围开始，`period` 只是窗口；结果还会被
   `FMI_MAX_HISTORY_ROWS`（默认 1500）截断并标 `truncated=true`。
6. **无鉴权、无多租户、无持久化**：单进程、内存缓存，进程退出即清空。
7. **概览是固定清单**：只覆盖 4 组共 11 个参考资产，不是全市场扫描。
8. **字段以工具 schema 为准**：上游字段偶有变动，遇到解析失败会返回 `unavailable` + 原因，而不是猜测。

## 13. 后续计划

| 优先级 | 事项 |
| --- | --- |
| P1 | 报价多源并行（新浪 + 腾讯交叉校验），给 `data_status` 增加"多源一致/不一致"标记 |
| P1 | 合规替代源：上交所/深交所公开行情文件、巨潮资讯网（CNINFO）公开披露数据 |
| P1 | 历史数据落本地 SQLite 归档（仅当用户明确需要离线复算时；当前阶段刻意不做数据库） |
| P2 | 增加 `get_symbol_profile`（基本面摘要）与板块/行业维度概览 |
| P2 | 交易日历与停牌识别，减少 `stale` 误判 |
| P3 | 可选的 streamable-http 部署 + 简单鉴权，供多客户端共享 |
| P3 | 东财恢复后重新评估 `clist/get` 批量端点（当前刻意禁用：它是触发限流的主因） |

## 14. 免责声明

本项目仅用于**数据检索与技术研究**，所有数据来自第三方免费公开接口，可能存在延迟、缺失或错误。
本项目**不构成任何投资建议**，不提供买卖点、目标价或收益承诺；据此进行的任何投资决策及后果，
由使用者自行承担。引用数据时请保留来源、行情时间与数据状态说明，并遵守各数据源的使用条款。

---

许可：MIT（见 [LICENSE](LICENSE)）。
