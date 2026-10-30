"""Probe script: verify AKShare endpoints for the MVP asset universe.

Run with:  uv run python scripts/probe_akshare.py
Each test prints timing, shape and a truncated preview so the developer can see
exactly what Eastmoney/Sina return today.
"""

from __future__ import annotations

import inspect
import json
import time
import traceback

import akshare as ak
import pandas as pd

pd.set_option("display.width", 200)
pd.set_option("display.max_columns", 40)


def preview(df, rows: int = 3) -> str:
    if df is None:
        return "None"
    if not isinstance(df, pd.DataFrame):
        return repr(df)[:600]
    return json.dumps(
        {
            "shape": list(df.shape),
            "columns": [str(c) for c in df.columns],
            "head": json.loads(df.head(rows).to_json(orient="records", force_ascii=False)),
        },
        ensure_ascii=False,
    )[:1600]


def run(name: str, func, **kwargs) -> None:
    started = time.time()
    try:
        result = func(**kwargs)
        elapsed = time.time() - started
        print(f"\n=== {name} OK {elapsed:.1f}s ===")
        print(preview(result))
    except Exception as exc:  # noqa: BLE001 - diagnostic script
        elapsed = time.time() - started
        print(f"\n=== {name} FAIL {elapsed:.1f}s === {type(exc).__name__}: {exc}")
        traceback.print_exc(limit=1)


def main() -> int:
    print(f"akshare {ak.__version__}")
    for fn in (
        "stock_bid_ask_em",
        "stock_zh_a_hist",
        "stock_zh_index_spot_em",
        "index_zh_a_hist",
        "stock_hk_hist",
        "stock_us_hist",
        "futures_global_spot_em",
        "futures_foreign_commodity_realtime",
    ):
        try:
            print(f"{fn}{inspect.signature(getattr(ak, fn))}")
        except Exception as exc:  # noqa: BLE001
            print(f"{fn} signature error: {exc}")

    run("stock_bid_ask_em(600519)", ak.stock_bid_ask_em, symbol="600519")
    run(
        "stock_zh_a_hist(600519)",
        ak.stock_zh_a_hist,
        symbol="600519",
        period="daily",
        start_date="20251001",
        end_date="20251030",
        adjust="",
    )
    run("stock_zh_index_spot_em(沪深重要指数)", ak.stock_zh_index_spot_em, symbol="沪深重要指数")
    run(
        "index_zh_a_hist(000001)",
        ak.index_zh_a_hist,
        symbol="000001",
        period="daily",
        start_date="20251001",
        end_date="20251030",
    )
    run(
        "stock_hk_hist(00700)",
        ak.stock_hk_hist,
        symbol="00700",
        period="daily",
        start_date="20251001",
        end_date="20251030",
        adjust="",
    )
    run(
        "stock_us_hist(AAPL)",
        ak.stock_us_hist,
        symbol="105.AAPL",
        period="daily",
        start_date="20251001",
        end_date="20251030",
        adjust="",
    )
    run("futures_global_spot_em()", ak.futures_global_spot_em)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
