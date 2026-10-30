"""Probe script: check which free real-time-ish quote endpoints answer from this host.

Covers Sina (hq.sinajs.cn), Tencent (qt.gtimg.cn) and the AKShare wrappers that
sit on top of them. Output is ASCII-escaped JSON so console encoding cannot
garble the result.
"""

from __future__ import annotations

import json
import time
import traceback

import httpx

SINA_URL = "https://hq.sinajs.cn/list={codes}"
TENCENT_URL = "https://qt.gtimg.cn/q={codes}"
HEADERS_SINA = {
    "Referer": "https://finance.sina.com.cn/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
}


def dump(label: str, payload) -> None:
    print(f"\n--- {label} ---")
    print(json.dumps(payload, ensure_ascii=True, default=str)[:2500])


def probe_sina(codes: str) -> None:
    started = time.time()
    try:
        with httpx.Client(timeout=10.0, headers=HEADERS_SINA, follow_redirects=True) as client:
            resp = client.get(SINA_URL.format(codes=codes))
        body = resp.content.decode("gbk", errors="replace")
        dump(f"sina {codes} status={resp.status_code} {time.time()-started:.2f}s", {"body": body})
    except Exception as exc:  # noqa: BLE001
        dump(f"sina {codes} ERROR", {"error": f"{type(exc).__name__}: {exc}"})


def probe_tencent(codes: str) -> None:
    started = time.time()
    try:
        with httpx.Client(
            timeout=10.0,
            headers={"User-Agent": HEADERS_SINA["User-Agent"], "Referer": "https://gu.qq.com/"},
            follow_redirects=True,
        ) as client:
            resp = client.get(TENCENT_URL.format(codes=codes))
        body = resp.content.decode("gbk", errors="replace")
        dump(f"tencent {codes} status={resp.status_code} {time.time()-started:.2f}s", {"body": body})
    except Exception as exc:  # noqa: BLE001
        dump(f"tencent {codes} ERROR", {"error": f"{type(exc).__name__}: {exc}"})


def probe_akshare() -> None:
    import akshare as ak

    tests = {
        "futures_foreign_commodity_realtime": lambda: ak.futures_foreign_commodity_realtime(
            symbol=["GC", "CL", "OIL"]
        ),
        "stock_zh_index_daily_tx(sh000001)": lambda: ak.stock_zh_index_daily_tx(symbol="sh000001"),
        "stock_hk_daily(00700)": lambda: ak.stock_hk_daily(symbol="00700", adjust=""),
        "stock_us_daily(AAPL)": lambda: ak.stock_us_daily(symbol="AAPL", adjust=""),
    }
    for name, func in tests.items():
        started = time.time()
        try:
            df = func()
            payload = {
                "elapsed": round(time.time() - started, 2),
                "shape": list(df.shape),
                "columns": [str(c) for c in df.columns],
                "tail": json.loads(df.tail(2).to_json(orient="records", force_ascii=False)),
            }
            dump(name, payload)
        except Exception as exc:  # noqa: BLE001
            dump(name + " ERROR", {"error": f"{type(exc).__name__}: {exc}", "tb": traceback.format_exc(limit=1)})


if __name__ == "__main__":
    probe_sina("sh600519,sh000001,sz399001,hk00700,gb_aapl,hf_GC,hf_CL,int_dji")
    probe_tencent("sh600519,sh000001,hk00700,usAAPL")
    probe_akshare()
