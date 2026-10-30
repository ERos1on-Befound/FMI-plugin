"""Smoke test for the service layer: real network calls, printed as JSON."""

from __future__ import annotations

import json
import sys

from financial_intelligence.services import MarketService


def show(title: str, payload) -> None:
    print(f"\n=== {title} ===")
    print(json.dumps(payload, ensure_ascii=True, indent=2, default=str)[:2600])


def main() -> int:
    service = MarketService()

    status = service.get_server_status()
    show("server status", status.model_dump(mode="json"))

    for symbol in ["AAPL", "600519.SS", "0700.HK", "^GSPC", "GC=F", "上证指数"]:
        try:
            quote = service.get_quote(symbol)
        except Exception as exc:  # noqa: BLE001
            print(f"\n!! {symbol} raised {exc.__class__.__name__}: {exc}")
            continue
        show(f"quote {symbol}", quote.model_dump(mode="json"))

    history = service.get_historical_data("AAPL", period="1mo", interval="1d")
    payload = history.model_dump(mode="json")
    payload["bars"] = payload["bars"][-2:]
    show("history AAPL 1mo/1d (last 2 bars)", payload)

    weekly = service.get_historical_data("600519.SS", period="3mo", interval="1wk")
    weekly_payload = weekly.model_dump(mode="json")
    weekly_payload["bars"] = weekly_payload["bars"][-2:]
    show("history 600519.SS 3mo/1wk (last 2 bars)", weekly_payload)

    overview = service.get_market_overview()
    slim = overview.model_dump(mode="json")
    for group in slim["groups"]:
        for item in group["items"]:
            item.pop("status_note", None)
    show("market overview", slim)
    return 0


if __name__ == "__main__":
    sys.exit(main())
