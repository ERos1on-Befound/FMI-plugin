"""Probe script #3: nail down symbol codes for indices/futures + history endpoints."""

from __future__ import annotations

import json
import time
import traceback

import akshare as ak
import httpx

HEADERS = {
    "Referer": "https://finance.sina.com.cn/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
}


def raw(url: str, label: str) -> None:
    try:
        started = time.time()
        with httpx.Client(timeout=10.0, headers=HEADERS, follow_redirects=True) as client:
            resp = client.get(url)
        text = resp.content.decode("gbk", errors="replace")
        print(f"\n--- {label} {resp.status_code} {time.time()-started:.2f}s ---")
        print(json.dumps(text[:3000], ensure_ascii=True))
    except Exception as exc:  # noqa: BLE001
        print(f"\n--- {label} ERROR {type(exc).__name__}: {exc}")


def call(label: str, func, **kwargs) -> None:
    started = time.time()
    try:
        df = func(**kwargs)
        payload = {
            "elapsed": round(time.time() - started, 2),
            "shape": list(df.shape),
            "columns": [str(c) for c in df.columns],
            "tail": json.loads(df.tail(2).to_json(orient="records", date_format="iso", force_ascii=False)),
        }
        print(f"\n--- {label} ---")
        print(json.dumps(payload, ensure_ascii=True))
    except Exception as exc:  # noqa: BLE001
        print(f"\n--- {label} ERROR ---")
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}", "tb": traceback.format_exc(limit=1)}, ensure_ascii=True))


INDEX_NAMES = [n for n in dir(ak) if "index_" in n and not n.startswith("_")]
FUT_NAMES = [n for n in dir(ak) if n.startswith("futures_") and not n.startswith("_")]

if __name__ == "__main__":
    print("index_*:", json.dumps([n for n in INDEX_NAMES if "us" in n or "global" in n or "spot" in n]))
    print("futures_*:", json.dumps([n for n in FUT_NAMES if "foreign" in n or "global" in n]))

    raw("https://hq.sinajs.cn/list=hkHSI,hkHSTECH,hkHSTI,gb_$dji,gb_$ixic,gb_$inx,gb_$spx,int_dji,int_nasdaq,int_sp500,int_hangseng,hf_OIL,hf_SI,hf_HG,sh000300,sh000905,sz399006", "sina symbol probe")
    raw("https://qt.gtimg.cn/q=usDJI,usIXIC,usSPX,usINX,hkHSI,hkHSTECH", "tencent symbol probe")

    call("index_us_stock_sina(.INX)", ak.index_us_stock_sina, symbol=".INX")
    call("futures_foreign_hist(GC)", ak.futures_foreign_hist, symbol="GC")
    call("futures_global_hist_em(GC00Y)", ak.futures_global_hist_em, symbol="GC00Y")
