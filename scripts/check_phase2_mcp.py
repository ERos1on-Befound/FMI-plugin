"""End-to-end check of the Phase 2 tools through a real MCP stdio session.

Starts `python -m financial_intelligence.server` as a subprocess, talks MCP over
stdio, and prints a compact summary of what the eight research tools return with
real upstream data. Used to verify the tools are discoverable and callable by an
MCP host (e.g. DeepSeek Harness), not just by the service layer.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from mcp import Client, StdioServerParameters

ROOT = Path(__file__).resolve().parents[1]


def _params() -> StdioServerParameters:
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "financial_intelligence.server"],
        cwd=str(ROOT),
    )


def _structured(result) -> dict:
    data = getattr(result, "structured_content", None)
    return data if isinstance(data, dict) else {}


def _show(label: str, value) -> None:
    print(f"{label}: {value}")


async def main() -> int:
    async with Client(_params(), raise_exceptions=True) as client:
        tools = await client.list_tools()
        names = [tool.name for tool in tools.tools]
        _show("tools", len(names))
        _show("tool_names", ", ".join(names))

        result = await client.call_tool(
            "get_company_financials", {"symbol": "AAPL", "period_type": "annual", "limit": 1}
        )
        data = _structured(result)
        statement = (data.get("statements") or [{}])[0]
        _show("financials.company", data.get("company_name"))
        _show("financials.source", data.get("source"))
        _show("financials.status", data.get("data_status"))
        _show("financials.period", statement.get("fiscal_period"))
        _show("financials.filing_date", statement.get("filing_date"))
        _show("financials.currency", statement.get("currency"))
        income = statement.get("income_statement") or {}
        _show("financials.revenue", income.get("revenue"))
        _show("financials.net_income", income.get("net_income"))
        _show("financials.missing", statement.get("missing_fields"))

        result = await client.call_tool(
            "calculate_financial_metrics",
            {"symbol": "AAPL", "metrics": "gross_margin,net_margin,roe,pe", "periods": 2},
        )
        data = _structured(result)
        for series in data.get("metrics") or []:
            points = series.get("points") or []
            latest = points[0] if points else {}
            _show(
                f"metrics.{series.get('name')}",
                f"{latest.get('display')} ({latest.get('period')}, {latest.get('calculation_status')})",
            )

        result = await client.call_tool(
            "analyze_technicals", {"symbol": "NVDA", "period": "6mo", "interval": "1d"}
        )
        data = _structured(result)
        _show("technicals.window", f"{data.get('window_start')} -> {data.get('window_end')}")
        _show("technicals.bars", data.get("bars"))
        _show("technicals.source", (data.get("provenance") or {}).get("source"))
        wanted = {
            "sma_20",
            "sma_60",
            "ema_12",
            "rsi_14",
            "macd",
            "macd_signal",
            "macd_histogram",
            "bollinger_upper",
            "bollinger_lower",
            "atr_14",
        }
        for series in data.get("indicators") or []:
            if series.get("name") in wanted:
                _show(f"technicals.{series.get('name')}", f"{series.get('value')} [{series.get('status')}]")

        result = await client.call_tool(
            "calculate_financial_formula",
            {"formula_name": "cagr", "inputs": {"beginning": 394328.0, "ending": 416161.0, "years": 3}},
        )
        data = _structured(result)
        _show("formula.cagr", f"{data.get('display')} [{data.get('calculation_status')}]")

        result = await client.call_tool(
            "compare_companies",
            {"symbols": "AAPL,MSFT", "metrics": "gross_margin,net_margin", "period_type": "annual"},
        )
        data = _structured(result)
        _show("compare.comparable", [row.get("comparable") for row in data.get("rows") or []])
        _show("compare.notes", (data.get("comparability_notes") or [])[:1])

        result = await client.call_tool("get_supported_indicators", {})
        data = _structured(result)
        _show(
            "catalog",
            {
                "formulas": len(data.get("formulas") or []),
                "metrics": len(data.get("metrics") or []),
                "technical": len(data.get("technical") or []),
            },
        )
    print("RESULT: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
