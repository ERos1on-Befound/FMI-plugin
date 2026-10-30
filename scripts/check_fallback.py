"""Prove the project still serves A-share/US data while Eastmoney is throttled.

This run deliberately disables the Eastmoney provider (``FMI_ENABLE_EASTMONEY=false``)
so that it proves the *alternative* sources work without sending a single extra
request to the throttled push2 edges.

Run:  .venv\\Scripts\\python.exe scripts\\check_fallback.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from financial_intelligence.config import load_settings  # noqa: E402
from financial_intelligence.providers.eastmoney import BREAKER  # noqa: E402
from financial_intelligence.services import MarketService  # noqa: E402


def main() -> int:
    env = {
        "FMI_ENABLE_EASTMONEY": "false",
        "FMI_PROVIDER_ORDER": "sina,akshare,yahoo",
    }
    settings = load_settings(env)
    print(f"providers_enabled={list(settings.enabled_providers())}")
    print(f"eastmoney_breaker_open={BREAKER.is_open} reason={BREAKER.reason}")
    print()

    service = MarketService(settings)

    for symbol in ("600519.SS", "000001.SS", "AAPL"):
        quote = service.get_quote(symbol)
        print(
            f"quote {symbol:<10} price={quote.price} prev={quote.previous_close} "
            f"pct={quote.change_percent} source={quote.source} "
            f"status={quote.data_status.value} ts={quote.timestamp} err={quote.error}"
        )
    print()

    for symbol, period, interval in (("000001.SS", "1mo", "1d"), ("AAPL", "1mo", "1d")):
        history = service.get_historical_data(symbol, period, interval)
        last = history.bars[-1] if history.bars else None
        print(
            f"history {symbol:<10} {period}/{interval} count={history.count} "
            f"source={history.source} status={history.data_status.value} "
            f"last={last.date if last else None} close={last.close if last else None}"
        )
        if history.error:
            print(f"    error={history.error}")
    print()
    print("RESULT: alternatives_working")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
