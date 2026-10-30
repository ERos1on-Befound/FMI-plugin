"""验收演示：通过真实 MCP stdio 调用检索「今日上午 A 股市场」。

用法（项目根目录）：

    .venv\\Scripts\\python.exe scripts\\demo_a_share_morning.py

它完整走一遍技能的标准工作流：
get_server_status → get_supported_markets → get_market_overview
→ get_stock_quote（指数篮子 + 个股篮子）→ get_asset_quote（中文别名校验）
→ get_historical_data（上证指数 / 茅台近月日线，用于给上午的涨跌找位置）

原始返回同时落盘到 artifacts/，便于逐条核对。
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from mcp import Client, StdioServerParameters

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "artifacts"
CN_TZ = ZoneInfo("Asia/Shanghai")

INDEX_POOL = [
    ("000001.SS", "上证指数"),
    ("399001.SZ", "深证成指"),
    ("000300.SS", "沪深300"),
    ("399006.SZ", "创业板指"),
    ("000688.SS", "科创50"),
]

STOCK_POOL = [
    ("600519.SS", "贵州茅台"),
    ("601318.SS", "中国平安"),
    ("600036.SS", "招商银行"),
    ("601899.SS", "紫金矿业"),
    ("600030.SS", "中信证券"),
    ("000858.SZ", "五粮液"),
    ("300750.SZ", "宁德时代"),
    ("002594.SZ", "比亚迪"),
]


def _params() -> StdioServerParameters:
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "financial_intelligence.server"],
        cwd=str(ROOT),
    )


async def call(client: Client, tool: str, args: dict | None = None) -> dict:
    result = await client.call_tool(tool, args or {})
    return result.structured_content or {}


def _quote_row(payload: dict) -> dict:
    return {
        "symbol": payload.get("symbol"),
        "name": payload.get("name"),
        "price": payload.get("price"),
        "previous_close": payload.get("previous_close"),
        "change": payload.get("change"),
        "change_percent": payload.get("change_percent"),
        "currency": payload.get("currency"),
        "timestamp": payload.get("timestamp"),
        "retrieved_at": payload.get("retrieved_at"),
        "data_status": payload.get("data_status"),
        "source": payload.get("source"),
        "from_cache": payload.get("from_cache"),
        "error": payload.get("error"),
        "status_note": payload.get("status_note"),
    }


def _fmt(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "—"
    return f"{value:,.{digits}f}"


def _pct(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:+.2f}%"


def _bj(stamp: str | None) -> str:
    if not stamp:
        return "—"
    try:
        return datetime.fromisoformat(stamp).astimezone(CN_TZ).strftime("%m-%d %H:%M")
    except ValueError:
        return stamp


async def main() -> int:
    bundle: dict = {"generated_at": datetime.now(CN_TZ).isoformat(), "calls": []}
    lines: list[str] = []

    async with Client(_params(), raise_exceptions=True) as client:
        # 1) 健康检查
        status = await call(client, "get_server_status")
        bundle["calls"].append({"tool": "get_server_status", "args": {}, "result": status})
        lines.append("## 1. get_server_status")
        lines.append(f"- status={status.get('status')} version={status.get('version')}")
        lines.append(f"- providers={status.get('providers')}")
        lines.append(f"- current_time={status.get('current_time')}")

        # 2) 能力 & 代码写法
        markets = await call(client, "get_supported_markets")
        bundle["calls"].append({"tool": "get_supported_markets", "args": {}, "result": markets})
        lines.append("\n## 2. get_supported_markets")
        lines.append(f"- markets={markets.get('markets')}")
        lines.append(f"- periods={markets.get('periods')} intervals={markets.get('intervals')}")

        # 3) 市场概览
        overview = await call(client, "get_market_overview")
        bundle["calls"].append({"tool": "get_market_overview", "args": {}, "result": overview})
        lines.append("\n## 3. get_market_overview")
        lines.append(f"- ok={overview.get('ok_count')} unavailable={overview.get('unavailable_count')}")
        lines.append("| 分组 | 代码 | 名称 | 最新价 | 涨跌幅 | 行情时间(北京) | 状态 | 备注 |")
        lines.append("| --- | --- | --- | ---: | ---: | --- | --- | --- |")
        for group in overview.get("groups", []):
            for item in group.get("items", []):
                lines.append(
                    "| {} | `{}` | {} | {} | {} | {} | `{}` | {} |".format(
                        group.get("label"),
                        item.get("symbol"),
                        item.get("name") or "",
                        _fmt(item.get("price")),
                        _pct(item.get("change_percent")),
                        _bj(item.get("timestamp")),
                        item.get("data_status"),
                        item.get("error") or "",
                    )
                )

        # 4) A股指数篮子
        index_quotes = []
        for symbol, _ in INDEX_POOL:
            payload = await call(client, "get_stock_quote", {"symbol": symbol})
            index_quotes.append(payload)
            bundle["calls"].append(
                {"tool": "get_stock_quote", "args": {"symbol": symbol}, "result": payload}
            )
        lines.append("\n## 4. get_stock_quote — A股指数篮子")
        lines.append("| 代码 | 名称 | 最新价 | 昨收 | 涨跌 | 涨跌幅 | 行情时间(北京) | 获取时间(北京) | 币种 | 状态 | 来源 |")
        lines.append("| --- | --- | ---: | ---: | ---: | ---: | --- | --- | --- | --- | --- |")
        for q in index_quotes:
            lines.append(
                "| `{}` | {} | {} | {} | {} | {} | {} | {} | {} | `{}` | {} |".format(
                    q.get("symbol"),
                    q.get("name") or "",
                    _fmt(q.get("price")),
                    _fmt(q.get("previous_close")),
                    _fmt(q.get("change")),
                    _pct(q.get("change_percent")),
                    _bj(q.get("timestamp")),
                    _bj(q.get("retrieved_at")),
                    q.get("currency"),
                    q.get("data_status"),
                    q.get("source"),
                )
            )

        # 5) 个股篮子
        stock_quotes = []
        for symbol, _ in STOCK_POOL:
            payload = await call(client, "get_stock_quote", {"symbol": symbol})
            stock_quotes.append(payload)
            bundle["calls"].append(
                {"tool": "get_stock_quote", "args": {"symbol": symbol}, "result": payload}
            )
        lines.append("\n## 5. get_stock_quote — A股个股篮子")
        lines.append("| 代码 | 名称 | 最新价 | 昨收 | 涨跌 | 涨跌幅 | 行情时间(北京) | 状态 | 来源 |")
        lines.append("| --- | --- | ---: | ---: | ---: | ---: | --- | --- | --- |")
        for q in stock_quotes:
            lines.append(
                "| `{}` | {} | {} | {} | {} | {} | {} | `{}` | {} |".format(
                    q.get("symbol"),
                    q.get("name") or "",
                    _fmt(q.get("price")),
                    _fmt(q.get("previous_close")),
                    _fmt(q.get("change")),
                    _pct(q.get("change_percent")),
                    _bj(q.get("timestamp")),
                    q.get("data_status"),
                    q.get("source"),
                )
            )

        # 6) 中文别名（get_asset_quote）
        alias_quote = await call(client, "get_asset_quote", {"symbol": "上证指数"})
        bundle["calls"].append(
            {"tool": "get_asset_quote", "args": {"symbol": "上证指数"}, "result": alias_quote}
        )
        lines.append("\n## 6. get_asset_quote — 中文别名")
        lines.append(
            "- 「上证指数」→ `{}` {} 最新 {} 涨跌 {} 状态 `{}`".format(
                alias_quote.get("symbol"),
                alias_quote.get("name") or "",
                _fmt(alias_quote.get("price")),
                _pct(alias_quote.get("change_percent")),
                alias_quote.get("data_status"),
            )
        )

        # 7) 历史数据：给上午的涨跌找位置
        for symbol, period, interval in (("000001.SS", "1mo", "1d"), ("600519.SS", "1mo", "1d")):
            history = await call(
                client, "get_historical_data", {"symbol": symbol, "period": period, "interval": interval}
            )
            bundle["calls"].append(
                {
                    "tool": "get_historical_data",
                    "args": {"symbol": symbol, "period": period, "interval": interval},
                    "result": history,
                }
            )
            bars = history.get("bars") or []
            closes = [b.get("close") for b in bars if b.get("close") is not None]
            lines.append(f"\n## 7. get_historical_data — `{symbol}` period={period} interval={interval}")
            lines.append(
                "- count={} source={} status={} truncated={}".format(
                    history.get("count"),
                    history.get("source"),
                    history.get("data_status"),
                    history.get("truncated"),
                )
            )
            if history.get("status_note"):
                lines.append(f"- status_note: {history.get('status_note')}")
            if closes:
                lines.append(
                    "- 区间收盘：最低 {} 最高 {} 最近 5 日 {}".format(
                        _fmt(min(closes)),
                        _fmt(max(closes)),
                        ", ".join(_fmt(c) for c in closes[-5:]),
                    )
                )
            lines.append("| 日期 | 开盘 | 最高 | 最低 | 收盘 | 成交量 |")
            lines.append("| --- | ---: | ---: | ---: | ---: | ---: |")
            for bar in bars[-5:]:
                lines.append(
                    "| {} | {} | {} | {} | {} | {} |".format(
                        bar.get("date"),
                        _fmt(bar.get("open")),
                        _fmt(bar.get("high")),
                        _fmt(bar.get("low")),
                        _fmt(bar.get("close")),
                        _fmt(bar.get("volume"), 0),
                    )
                )

    # 汇总：篮子涨跌排序
    ranked = sorted(
        [q for q in stock_quotes if q.get("change_percent") is not None],
        key=lambda q: q["change_percent"],
        reverse=True,
    )
    lines.append("\n## 8. 篮子涨跌排序（个股）")
    for q in ranked:
        lines.append(
            "- {} `{}` {} {}（行情时间 {}）".format(
                q.get("name"),
                q.get("symbol"),
                _pct(q.get("change_percent")),
                _fmt(q.get("price")),
                _bj(q.get("timestamp")),
            )
        )

    OUT_DIR.mkdir(exist_ok=True)
    out_path = OUT_DIR / f"a_share_morning_{datetime.now(CN_TZ):%Y%m%d_%H%M}.json"
    out_path.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")

    report = "\n".join(lines)
    (OUT_DIR / "a_share_morning_report.md").write_text(report, encoding="utf-8")
    print(report)
    print(f"\nRAW -> {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
