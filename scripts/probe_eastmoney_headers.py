"""最小变量探测：同一 URL、同一参数，只改「HTTP 客户端 + 请求头」两处变量。

目的：判断 akshare 调用 push2 被断开（RemoteDisconnected）的真正触发条件，
是「请求头缺失」「客户端指纹」还是「请求频率」。每个组合只发 1 次请求。
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx
import requests

URL = "https://push2.eastmoney.com/api/qt/stock/get"
PARAMS = {"secid": "1.600519", "fields": "f43,f57,f58,f60,f169,f170,f86", "invt": "2", "fltt": "1"}
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 FMI/0.1.0"
)
FULL_HEADERS = {"User-Agent": UA, "Referer": "https://quote.eastmoney.com/", "Accept": "*/*"}
UA_ONLY = {"User-Agent": UA}


def chain(exc: BaseException) -> list[str]:
    out, cur = [], exc
    while cur is not None and len(out) < 5:
        out.append(f"{cur.__class__.__name__}: {cur}")
        cur = cur.__cause__ or cur.__context__
    return out


def via_requests(label: str, headers: dict[str, str]) -> dict[str, Any]:
    t0 = time.perf_counter()
    try:
        response = requests.get(URL, params=PARAMS, headers=headers, timeout=12)
        return {
            "case": label,
            "client": "requests",
            "headers": sorted(headers) or "lib-default",
            "outcome": "http",
            "status": response.status_code,
            "ms": round((time.perf_counter() - t0) * 1000, 1),
            "body_head": response.text[:120],
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "case": label,
            "client": "requests",
            "headers": sorted(headers) or "lib-default",
            "outcome": "aborted",
            "ms": round((time.perf_counter() - t0) * 1000, 1),
            "chain": chain(exc),
        }


def via_httpx(label: str, headers: dict[str, str], http2: bool = False) -> dict[str, Any]:
    t0 = time.perf_counter()
    try:
        with httpx.Client(timeout=12, headers=headers, trust_env=False, http2=http2) as client:
            response = client.get(URL, params=PARAMS)
        return {
            "case": label,
            "client": f"httpx(h2={http2})",
            "headers": sorted(headers) or "lib-default",
            "outcome": "http",
            "status": response.status_code,
            "ms": round((time.perf_counter() - t0) * 1000, 1),
            "body_head": response.text[:120],
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "case": label,
            "client": f"httpx(h2={http2})",
            "headers": sorted(headers) or "lib-default",
            "outcome": "aborted",
            "ms": round((time.perf_counter() - t0) * 1000, 1),
            "chain": chain(exc),
        }


def main() -> int:
    results = []
    # 1) akshare/requests 的默认行为：无自定义请求头
    results.append(via_requests("requests，库默认头（=akshare 的行为）", {}))
    time.sleep(1.5)
    # 2) 只补 UA
    results.append(via_requests("requests，仅补 User-Agent", UA_ONLY))
    time.sleep(1.5)
    # 3) 补 UA + Referer
    results.append(via_requests("requests，UA + Referer", FULL_HEADERS))
    time.sleep(1.5)
    # 4) httpx 对照组（与诊断脚本同一配置）
    results.append(via_httpx("httpx，UA + Referer", FULL_HEADERS))
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
