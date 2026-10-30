"""Probe script: verify yfinance behaviour for the MVP asset universe.

Run with:  uv run python scripts/probe_yahoo.py
It performs real network calls and prints one JSON object per ticker so the
developer can see exactly which fields Yahoo returns today.
"""

from __future__ import annotations

import json
import sys

import yfinance as yf

TICKERS = ["AAPL", "NVDA", "MSFT", "0700.HK", "600519.SS", "^GSPC", "GC=F", "CL=F"]

META_KEYS = [
    "currency",
    "symbol",
    "exchangeName",
    "fullExchangeName",
    "instrumentType",
    "regularMarketPrice",
    "regularMarketTime",
    "previousClose",
    "chartPreviousClose",
    "shortName",
    "longName",
    "timezone",
    "exchangeTimezoneName",
    "dataGranularity",
    "range",
]


def probe(ticker: str) -> dict:
    out: dict = {"ticker": ticker}
    try:
        tk = yf.Ticker(ticker)
        hist = tk.history(period="5d", interval="1d", auto_adjust=False)
        out["history_rows"] = int(len(hist))
        out["history_columns"] = [str(c) for c in hist.columns]
        if len(hist):
            out["history_last_index"] = str(hist.index[-1])
            out["history_last_close"] = float(hist["Close"].iloc[-1])
        meta = getattr(tk, "history_metadata", None) or {}
        out["meta"] = {k: meta.get(k) for k in META_KEYS if k in meta}
        out["meta_keys"] = sorted(str(k) for k in meta.keys())
        try:
            fi = tk.fast_info
            out["fast_info"] = {
                "keys": sorted(k for k in dir(fi) if not k.startswith("_"))[:40],
                "last_price": getattr(fi, "last_price", None),
                "previous_close": getattr(fi, "previous_close", None),
                "currency": getattr(fi, "currency", None),
            }
        except Exception as exc:  # pragma: no cover - diagnostic path
            out["fast_info_error"] = f"{type(exc).__name__}: {exc}"
    except Exception as exc:  # pragma: no cover - diagnostic path
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


def main() -> int:
    print(f"yfinance {yf.__version__} / python {sys.version.split()[0]}")
    for ticker in TICKERS:
        print(json.dumps(probe(ticker), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
