"""Generate ``examples/sample_market_data.md`` from live (delayed) data.

Run it from the project root with the project venv::

    .venv\\Scripts\\python.exe scripts\\make_example.py

The output is a real transcript of the MCP service on the day it was generated:
it is NOT hand-written sample data.  Everything is marked ``delayed`` because
free public endpoints cannot prove realtime delivery.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from financial_intelligence.config import load_settings
from financial_intelligence.services import MarketService

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "examples" / "sample_market_data.md"
CN_TZ = ZoneInfo("Asia/Shanghai")

QUOTE_SYMBOLS = [
    ("AAPL", "美股"),
    ("NVDA", "美股"),
    ("MSFT", "美股"),
    ("0700.HK", "港股"),
    ("600519.SS", "A股"),
    ("^GSPC", "美股指数"),
    ("GC=F", "期货参考"),
    ("CL=F", "期货参考"),
]


def _num(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "—"
    return f"{value:,.{digits}f}"


def _pct(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:+.2f}%"


def _local(stamp: str | None) -> str:
    if not stamp:
        return "—"
    try:
        return datetime.fromisoformat(stamp).astimezone(CN_TZ).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return stamp


def render_quotes(service: MarketService) -> list[str]:
    lines = [
        "| 代码 | 名称 | 市场 | 币种 | 最新价 | 涨跌幅 | 行情时间(北京) | 获取时间(北京) | 数据状态 | 来源 | 备注 |",
        "| --- | --- | --- | --- | ---: | ---: | --- | --- | --- | --- | --- |",
    ]
    for symbol, hint in QUOTE_SYMBOLS:
        quote = service.get_quote(symbol)
        if quote.price is None:
            lines.append(
                f"| `{quote.symbol}` | {quote.name or '—'} | {hint} | {quote.currency or '—'} | — | — | — | "
                f"{_local(quote.retrieved_at)} | `{quote.data_status.value}` | {quote.error or '无数据'} | |"
            )
            continue
        lines.append(
            "| `{}` | {} | {} | {} | {} | {} | {} | {} | `{}` | {} | {} |".format(
                quote.symbol,
                quote.name or hint,
                quote.market.value,
                quote.currency,
                _num(quote.price),
                _pct(quote.change_percent),
                _local(quote.timestamp),
                _local(quote.retrieved_at),
                quote.data_status.value,
                quote.source,
                (quote.status_note or "").replace("|", "/"),
            )
        )
    return lines


def render_history(service: MarketService, symbol: str, period: str, interval: str, rows: int = 5) -> list[str]:
    history = service.get_historical_data(symbol, period, interval)
    head = [
        f"**`{symbol}`** · period=`{period}` · interval=`{interval}` · "
        f"共 {history.count} 根（展示最近 {min(rows, history.count)} 根）· 来源 `{history.source}` · "
        f"状态 `{history.data_status.value}`",
        "",
    ]
    if history.status_note:
        head.insert(1, f"> {history.status_note}")
        head.insert(1, "")
    if not history.bars:
        head.append(f"> 取不到数据：{history.error}")
        return head
    table = [
        "| 日期 | 开盘 | 最高 | 最低 | 收盘 | 成交量 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for bar in history.bars[-rows:]:
        table.append(
            f"| {bar.date} | {_num(bar.open)} | {_num(bar.high)} | {_num(bar.low)} | "
            f"{_num(bar.close)} | {_num(bar.volume, 0)} |"
        )
    return head + table


def render_overview(service: MarketService) -> list[str]:
    overview = service.get_market_overview()
    lines = [
        f"覆盖 {len(overview.groups)} 个分组，成功 {overview.ok_count} 项，缺失 {overview.unavailable_count} 项。",
        "",
        "| 分组 | 项目 | 最新价 | 涨跌幅 | 行情时间(北京) | 数据状态 | 备注 |",
        "| --- | --- | ---: | ---: | --- | --- | --- |",
    ]
    for group in overview.groups:
        for item in group.items:
            lines.append(
                "| {} | `{}` {} | {} | {} | {} | `{}` | {} |".format(
                    group.label,
                    item.symbol,
                    item.name or "",
                    _num(item.price),
                    _pct(item.change_percent),
                    _local(item.timestamp),
                    item.data_status.value,
                    item.error or ("期货参考价" if item.asset_type.value == "commodity" else ""),
                )
            )
    return lines


def main() -> int:
    service = MarketService(load_settings())
    now = datetime.now(CN_TZ).strftime("%Y-%m-%d %H:%M")

    parts: list[str] = [
        "# 示例输出：真实行情数据（延迟数据）",
        "",
        f"> 本文件由 `scripts/make_example.py` 于 **{now}（北京时间）** 自动生成，"
        "内容来自免费公开接口的真实返回值，不是手工编写的样例。",
        "> 所有报价均为**延迟行情**（免费接口无法证明实时性），不构成任何投资建议。",
        "> 行情时间为市场行情对应的时刻，获取时间为本次调用的时刻，两者必须分开看。",
        "",
        "## 1. 单只资产报价（get_stock_quote / get_asset_quote）",
        "",
    ]
    parts += render_quotes(service)
    parts += ["", "## 2. 历史K线（get_historical_data）", ""]
    parts += render_history(service, "AAPL", "1mo", "1d")
    parts += [""]
    parts += render_history(service, "600519.SS", "3mo", "1wk")
    parts += ["", "## 3. 市场概览（get_market_overview）", ""]
    parts += render_overview(service)
    parts += [
        "",
        "## 4. 读这份示例时要注意",
        "",
        "- `data_status` 只会是 `delayed` / `stale` / `unknown` / `unavailable`，不会出现 `realtime`。",
        "- 期货代码（`GC=F` / `CL=F` / `BZ=F`）返回的是**期货合约参考价**，不是现货价格。",
        "- 价格为 `—` 表示该数据源本次没有取到值，原因写在「备注 / 来源」列，不会用 0 填充。",
        "- 服务器在 60 秒（报价）/ 300 秒（历史）内会命中缓存，`from_cache=true`，但行情时间不变。",
        "",
    ]

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(parts), encoding="utf-8")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
