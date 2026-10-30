"""最小变量探测 3：成功率统计（requests / IPv4 / 每次新连接 / 间隔 1.2s，共 6 次）。

目的：区分「确定性拒绝」与「概率性断连」。
* 若 6 次全成功 → 之前是偶发；
* 若全失败 → 确定性拒绝（限流或封锁）；
* 若部分成功 → 概率性断连，正确对策是「有限重试 + 退避 + 熔断」，而不是换请求头。

只发 6 次请求，不做并发、不翻页、不重试。
"""

from __future__ import annotations

import json
import socket
import time
from typing import Any

import requests

URL = "https://push2.eastmoney.com/api/qt/stock/get"
PARAMS = {"secid": "1.600519", "fields": "f43,f57,f58,f60,f86", "invt": "2", "fltt": "1"}
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 FMI/0.1.0"
)
HEADERS = {"User-Agent": UA, "Referer": "https://quote.eastmoney.com/", "Accept": "*/*"}
REAL = socket.getaddrinfo


def ipv4_only():
    def limited(host, port, fam=0, type=0, proto=0, flags=0):  # noqa: A002
        return REAL(host, port, socket.AF_INET, type, proto, flags)

    socket.getaddrinfo = limited  # type: ignore[assignment]


def main() -> int:
    ipv4_only()
    rows: list[dict[str, Any]] = []
    for i in range(6):
        t0 = time.perf_counter()
        try:
            r = requests.get(URL, params=PARAMS, headers=HEADERS, timeout=12)
            rows.append({
                "attempt": i + 1,
                "ok": r.status_code == 200 and '"data"' in r.text,
                "status": r.status_code,
                "ms": round((time.perf_counter() - t0) * 1000, 1),
            })
        except Exception as exc:  # noqa: BLE001
            rows.append({
                "attempt": i + 1,
                "ok": False,
                "error": f"{exc.__class__.__name__}: {str(exc)[:120]}",
                "ms": round((time.perf_counter() - t0) * 1000, 1),
            })
        time.sleep(1.2)
    ok = sum(1 for r in rows if r["ok"])
    print(json.dumps({"url": URL, "attempts": rows, "success": ok, "total": len(rows)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
