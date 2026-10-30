"""MCP-level tests: the six tools, their schemas and a real stdio handshake.

The in-memory client exercises the exact code path an MCP host uses, while the
stdio test proves the server can actually be launched as a subprocess.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from mcp import Client, StdioServerParameters

from financial_intelligence import server
from financial_intelligence.services import MarketService
from test_services import FakeProvider

pytestmark = pytest.mark.anyio

EXPECTED_TOOLS = {
    "get_server_status",
    "get_supported_markets",
    "get_stock_quote",
    "get_historical_data",
    "get_market_overview",
    "get_asset_quote",
}


@pytest.fixture()
def wired_server(monkeypatch: pytest.MonkeyPatch):
    """A server whose service is backed by an offline fake provider."""
    service = MarketService(providers=[FakeProvider("fake", price=123.456)])
    monkeypatch.setattr(server, "_service", service)
    return server.build_server()


async def test_all_six_tools_are_registered_with_descriptions(wired_server) -> None:
    async with Client(wired_server, raise_exceptions=True) as client:
        tools = (await client.list_tools()).tools

    names = {tool.name for tool in tools}
    assert names == EXPECTED_TOOLS
    for tool in tools:
        assert tool.description and len(tool.description) > 20
        assert tool.input_schema is not None


async def test_tool_schemas_expose_the_documented_parameters(wired_server) -> None:
    async with Client(wired_server, raise_exceptions=True) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}

    history_props = tools["get_historical_data"].input_schema["properties"]
    assert set(history_props) == {"symbol", "period", "interval"}
    assert history_props["period"]["default"] == "1mo"
    assert history_props["interval"]["default"] == "1d"

    stock_props = tools["get_stock_quote"].input_schema["properties"]
    assert "symbol" in stock_props and "market" in stock_props
    assert tools["get_market_overview"].input_schema.get("properties", {}) == {}


async def test_get_server_status_returns_a_typed_payload(wired_server) -> None:
    async with Client(wired_server, raise_exceptions=True) as client:
        result = await client.call_tool("get_server_status", {})

    payload = result.structured_content
    assert payload["status"] == "ok"
    assert payload["providers"] == ["fake"]
    assert result.content[0].text  # models also get a text rendering


async def test_get_supported_markets_payload(wired_server) -> None:
    async with Client(wired_server, raise_exceptions=True) as client:
        result = await client.call_tool("get_supported_markets", {})

    payload = result.structured_content
    assert set(payload) >= {"markets", "asset_types", "providers", "periods", "intervals", "notes"}
    assert payload["intervals"] == ["1d", "1wk", "1mo"]


async def test_get_stock_quote_returns_price_and_times(wired_server) -> None:
    async with Client(wired_server, raise_exceptions=True) as client:
        result = await client.call_tool("get_stock_quote", {"symbol": "AAPL"})

    payload = result.structured_content
    assert payload["price"] == pytest.approx(123.456)
    assert payload["previous_close"] == pytest.approx(100.0)
    assert payload["change_percent"] == pytest.approx(23.456)
    assert payload["data_status"] != "realtime"
    assert payload["timestamp"] and payload["retrieved_at"]
    assert payload["currency"] == "USD"


async def test_get_stock_quote_with_bad_symbol_returns_unavailable_not_an_exception(wired_server) -> None:
    async with Client(wired_server, raise_exceptions=True) as client:
        result = await client.call_tool("get_stock_quote", {"symbol": "!!!bad!!!"})

    payload = result.structured_content
    assert payload["price"] is None
    assert payload["data_status"] == "unavailable"
    assert payload["error"]
    assert result.is_error in (None, False)


async def test_get_historical_data_returns_bars(wired_server) -> None:
    async with Client(wired_server, raise_exceptions=True) as client:
        result = await client.call_tool(
            "get_historical_data", {"symbol": "600519.SS", "period": "3mo", "interval": "1d"}
        )

    payload = result.structured_content
    assert payload["period"] == "3mo"
    assert payload["count"] == len(payload["bars"]) == 1
    bar = payload["bars"][0]
    assert set(bar) >= {"date", "open", "high", "low", "close", "volume", "symbol", "currency", "source"}


async def test_get_historical_data_rejects_impossible_combination_gracefully(wired_server) -> None:
    async with Client(wired_server, raise_exceptions=True) as client:
        result = await client.call_tool(
            "get_historical_data", {"symbol": "AAPL", "period": "1mo", "interval": "1mo"}
        )

    payload = result.structured_content
    assert payload["bars"] == []
    assert payload["data_status"] == "unavailable"
    assert "1mo" in payload["error"]


async def test_get_market_overview_covers_all_four_groups(wired_server) -> None:
    async with Client(wired_server, raise_exceptions=True) as client:
        result = await client.call_tool("get_market_overview", {})

    payload = result.structured_content
    assert len(payload["groups"]) == 4
    assert payload["ok_count"] == 11
    assert payload["unavailable_count"] == 0
    assert any("参考价" in note for note in payload["notes"])


async def test_get_asset_quote_accepts_aliases_and_asset_type(wired_server) -> None:
    async with Client(wired_server, raise_exceptions=True) as client:
        gold = await client.call_tool("get_asset_quote", {"symbol": "黄金"})
        index = await client.call_tool("get_asset_quote", {"symbol": "000001.SS", "asset_type": "index"})

    assert gold.structured_content["market"] == "COMMODITY"
    assert index.structured_content["asset_type"] == "index"


async def test_missing_required_argument_is_reported_as_a_tool_error(wired_server) -> None:
    """The SDK validates the input schema before our code runs: no symbol -> tool error."""
    async with Client(wired_server, raise_exceptions=True) as client:
        result = await client.call_tool("get_stock_quote", {})

    assert result.is_error is True
    assert "symbol" in (result.content[0].text or "")


async def test_unknown_extra_argument_is_ignored_by_the_sdk(wired_server) -> None:
    async with Client(wired_server, raise_exceptions=True) as client:
        result = await client.call_tool("get_stock_quote", {"symbol": "AAPL", "nonsense": 1})

    assert result.is_error in (None, False)
    assert result.structured_content["price"] == pytest.approx(123.456)


@pytest.mark.slow
async def test_stdio_transport_starts_and_lists_tools() -> None:
    """Launch the real entry point as a subprocess and do a JSON-RPC handshake."""
    project_root = Path(__file__).resolve().parents[1]
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "financial_intelligence.server"],
        cwd=str(project_root),
    )
    async with Client(params, raise_exceptions=True) as client:
        tools = (await client.list_tools()).tools
        status = await client.call_tool("get_server_status", {})

    assert {tool.name for tool in tools} == EXPECTED_TOOLS
    assert status.structured_content["status"] == "ok"
