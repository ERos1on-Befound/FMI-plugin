"""End-to-end Phase 3 check: drive the MCP server over stdio like a real host.

For every Phase 3 tool this script prints the *real* structured response — in
particular the chart/report file paths, which are then verified to exist on
disk.  Nothing here is mocked: it starts the same server the Harness would.

    .venv\\Scripts\\python.exe scripts\\check_phase3_mcp.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import Client, StdioServerParameters

ROOT = Path(__file__).resolve().parents[1]


def _exists(path: str | None) -> bool:
    return bool(path) and Path(path).is_file()


async def main() -> int:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "financial_intelligence.server"],
        cwd=str(ROOT),
    )
    problems: list[str] = []
    async with Client(params, raise_exceptions=True) as client:
        tools = await client.list_tools()
        names = [tool.name for tool in tools.tools]
        print(f"tools: {len(names)}")
        print(json.dumps(names, ensure_ascii=False))

        available = await client.call_tool("get_available_chart_types", {})
        payload = available.structured_content or {}
        types = [item["name"] for item in payload.get("chart_types", [])]
        print(f"chart types: {types}")
        print(f"png export: {payload.get('png_export_enabled')}  outputs: {payload.get('outputs_dir')}")

        templates = await client.call_tool("get_report_templates", {})
        tpl = [item["name"] for item in (templates.structured_content or {}).get("templates", [])]
        print(f"report templates: {tpl}")

        chart = await client.call_tool(
            "generate_financial_chart",
            {"symbol": "AAPL", "chart_type": "candlestick", "period": "1mo"},
        )
        c = chart.structured_content or {}
        print(f"chart AAPL candlestick: status={c.get('status')} html={c.get('html_path')} png={c.get('png_path')}")
        if not _exists(c.get("html_path")):
            problems.append("candlestick html path missing")
        if c.get("png_path") and not _exists(c.get("png_path")):
            problems.append("candlestick png path missing")

        tech = await client.call_tool(
            "generate_financial_chart",
            {"symbol": "NVDA", "chart_type": "technical", "period": "3mo", "indicators": "ma,rsi"},
        )
        t = tech.structured_content or {}
        print(f"chart NVDA technical: status={t.get('status')} html={t.get('html_path')}")
        if not _exists(t.get("html_path")):
            problems.append("technical html path missing")

        cmp_chart = await client.call_tool(
            "generate_comparison_chart",
            {"symbols": "AAPL,MSFT", "metric": "price", "period": "3mo", "normalization": "base_100"},
        )
        cc = cmp_chart.structured_content or {}
        print(f"comparison chart price base_100: status={cc.get('status')} html={cc.get('html_path')}")
        if not _exists(cc.get("html_path")):
            problems.append("comparison html path missing")

        report = await client.call_tool(
            "generate_analysis_report",
            {"symbol": "AAPL", "report_type": "fundamental", "output_format": "html"},
        )
        r = report.structured_content or {}
        print(
            f"report AAPL fundamental: status={r.get('status')} md={r.get('markdown_path')} "
            f"html={r.get('html_path')} charts={len(r.get('charts') or [])} "
            f"missing={r.get('missing_data')}"
        )
        if not _exists(r.get("markdown_path")):
            problems.append("report markdown path missing")
        if not _exists(r.get("html_path")):
            problems.append("report html path missing")

        tech_report = await client.call_tool(
            "generate_analysis_report",
            {"symbol": "NVDA", "report_type": "technical", "period": "3mo", "output_format": "markdown"},
        )
        tr = tech_report.structured_content or {}
        print(
            f"report NVDA technical: status={tr.get('status')} md={tr.get('markdown_path')} "
            f"period={tr.get('data_period')}"
        )
        if not _exists(tr.get("markdown_path")):
            problems.append("technical report path missing")

    if problems:
        print("RESULT: problems -> " + "; ".join(problems))
        return 1
    print("RESULT: ok")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
