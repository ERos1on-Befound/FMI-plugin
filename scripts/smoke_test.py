"""Offline smoke test for the MCP server.

Starts the server in memory (no subprocess, no network), lists the tools, and
calls the tools that only read local capability metadata. Safe for CI.

    uv run python scripts/smoke_test.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from mcp import Client  # noqa: E402

from financial_intelligence.server import build_server  # noqa: E402

OFFLINE_TOOLS = (
    ("get_server_status", {}),
    ("get_supported_markets", {}),
    ("get_supported_indicators", {}),
    ("get_available_chart_types", {}),
    ("get_report_templates", {}),
)

EXPECTED_TOOLS = 19


def _dump(result) -> dict:
    return dict(result.structured_content or {})


async def main() -> int:
    server = build_server()
    async with Client(server, raise_exceptions=True) as client:
        listing = await client.list_tools()
        names = [tool.name for tool in listing.tools]
        print(f"[1] tools: {len(names)}")
        for name in names:
            print(f"    - {name}")
        if len(names) != EXPECTED_TOOLS:
            print(f"FAIL expected {EXPECTED_TOOLS} tools, got {len(names)}")
            return 1

        for name, arguments in OFFLINE_TOOLS:
            result = await client.call_tool(name, arguments)
            payload = _dump(result)
            if result.is_error:
                print(f"FAIL {name}: {result.content}")
                return 1
            keys = ", ".join(sorted(payload)) or "text-only"
            print(f"[ok] {name} -> {keys}")

        indicators = _dump(await client.call_tool("get_supported_indicators", {}))
        print(
            "[2] catalog: formulas=%s metrics=%s technical=%s"
            % (
                len(indicators.get("formulas") or []),
                len(indicators.get("metrics") or []),
                len(indicators.get("technical") or []),
            )
        )

        charts = _dump(await client.call_tool("get_available_chart_types", {}))
        chart_types = [item.get("name") for item in charts.get("chart_types") or []]
        print(f"[3] chart types: {chart_types}")
        if len(chart_types) != 6:
            print("FAIL expected 6 chart types")
            return 1

        templates = _dump(await client.call_tool("get_report_templates", {}))
        report_types = [item.get("report_type") for item in templates.get("templates") or []]
        print(f"[4] report templates: {sorted(set(report_types))}")
        if len(templates.get("templates") or []) != 4:
            print("FAIL expected 4 report templates")
            return 1

        # A deliberately invalid symbol must come back as a structured
        # unavailable result rather than raising through the transport.
        bad = await client.call_tool("get_company_financials", {"symbol": "!!!bad!!!"})
        payload = _dump(bad)
        if bad.is_error or payload.get("data_status") != "unavailable":
            print(f"FAIL invalid-symbol handling: {payload}")
            return 1
        print("[5] invalid symbol -> structured unavailable")

    print("RESULT: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
