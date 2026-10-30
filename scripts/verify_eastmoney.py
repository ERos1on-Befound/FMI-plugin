"""One-shot, strictly limited live check of the Eastmoney provider.

Budget: at most THREE requests to Eastmoney (one quote, one index quote, one
kline series). The moment a request is refused the script stops instead of
retrying — retrying a throttled Eastmoney edge only extends the ban.

Run:  .venv\\Scripts\\python.exe scripts\\verify_eastmoney.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from financial_intelligence.config import load_settings, configure_logging  # noqa: E402
from financial_intelligence.providers.base import ProviderError  # noqa: E402
from financial_intelligence.providers.eastmoney import (  # noqa: E402
    BREAKER,
    EastmoneyProvider,
)
from financial_intelligence.utils.symbol_mapper import map_symbol  # noqa: E402

ARROW = "->"


def show_quote(label: str, quote) -> None:
    print(f"[{label}] source={quote.source} status={quote.data_status.value}")
    print(f"    symbol={quote.symbol} name={quote.name} market={quote.market.value} currency={quote.currency}")
    print(f"    price={quote.price} previous_close={quote.previous_close}")
    print(f"    change={quote.change} change_percent={quote.change_percent}")
    print(f"    timestamp={quote.timestamp} retrieved_at={quote.retrieved_at}")
    if quote.status_note:
        print(f"    note={quote.status_note}")
    if quote.error:
        print(f"    error={quote.error}")


def main() -> int:
    settings = load_settings()
    configure_logging(settings)
    print(f"eastmoney min_interval={settings.eastmoney_min_interval}s cooldown={settings.eastmoney_cooldown}s")
    print(f"request_timeout={settings.request_timeout}s")
    print()

    provider = EastmoneyProvider(settings)
    requests_made = 0
    failures: list[str] = []

    # ---- request 1: a single A-share quote --------------------------------
    quote_600519 = None
    try:
        requests_made += 1
        quote_600519 = provider.get_quote(map_symbol("600519.SS"))
        show_quote("1/3 A-share quote", quote_600519)
    except ProviderError as exc:
        failures.append(f"quote 600519.SS {ARROW} {exc}")
        print(f"[1/3 A-share quote] FAILED kind={exc.kind}")
        print(f"    {exc}")
    print()

    stalled = bool(failures) and "rate_limit" in failures[0]

    # ---- request 2: index quote (skipped once throttled) -----------------
    if not stalled:
        try:
            requests_made += 1
            quote_index = provider.get_quote(map_symbol("000001.SS"))
            show_quote("2/3 index quote", quote_index)
        except ProviderError as exc:
            failures.append(f"quote 000001.SS {ARROW} {exc}")
            print(f"[2/3 index quote] FAILED kind={exc.kind}")
            print(f"    {exc}")
        print()

    # ---- request 3: daily klines -----------------------------------------
    history = None
    if not failures:
        try:
            requests_made += 1
            history = provider.get_history(map_symbol("600519.SS"), "1mo", "1d")
            print(f"[3/3 klines] source={history.source} count={history.count} status={history.data_status.value}")
            for bar in history.bars[-3:]:
                print(f"    {bar.date} open={bar.open} high={bar.high} low={bar.low} close={bar.close} volume={bar.volume}")
            if history.status_note:
                print(f"    note={history.status_note}")
        except ProviderError as exc:
            failures.append(f"history 600519.SS {ARROW} {exc}")
            print(f"[3/3 klines] FAILED kind={exc.kind}")
            print(f"    {exc}")
        print()

    # ---- sanity check with an independent source (not Eastmoney) ---------
    if quote_600519 is not None and quote_600519.price is not None:
        try:
            from financial_intelligence.providers.sina import SinaProvider

            sina = SinaProvider(settings).get_quote(map_symbol("600519.SS"))
            print(f"[cross-check sina] price={sina.price} prev={sina.previous_close} ts={sina.timestamp}")
            if sina.price:
                delta = abs(quote_600519.price - sina.price) / sina.price * 100
                print(f"    divergent={delta:.3f}% (<=1% means the Eastmoney snapshot is consistent)")
        except Exception as exc:  # noqa: BLE001 - diagnostic only
            print(f"[cross-check sina] unavailable: {exc}")
        print()

    print(f"requests_sent_to_eastmoney={requests_made}")
    print(f"breaker_open={BREAKER.is_open} last_attempt={BREAKER.last_attempt} reason={BREAKER.reason}")
    payload = {
        "requests_made": requests_made,
        "failures": failures,
        "breaker_open": BREAKER.is_open,
        "breaker_reason": BREAKER.reason,
        "quote": json.loads(quote_600519.model_dump_json()) if quote_600519 else None,
        "history": {"count": history.count, "source": history.source} if history else None,
    }
    out = ROOT / "artifacts" / "eastmoney_verify.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nwrote {out}")

    if not failures:
        print("\nRESULT: fixed")
        return 0
    if quote_600519 is not None and quote_600519.price is not None:
        print("\nRESULT: partially_fixed")
        return 0
    print("\nRESULT: unavailable")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
