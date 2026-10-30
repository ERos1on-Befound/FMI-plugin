"""最小变量探测 2：只改「地址族（IPv4 / IPv6）」，其它全部相同。

背景：同一秒内对 push2 的同一请求，requests 成功而 httpx 被断开（RemoteDisconnected），
说明「请求头」不是变量。DNS 同时返回 A 与 AAAA 记录，本探测验证是否是 IPv6 出口
被中断导致 connect 后被 reset。

每个组合只发 1 次请求，组间间隔 1.5 秒。
"""

from __future__ import annotations

import json
import socket
import time
from typing import Any

import httpx
import requests

URL = "https://push2.eastmoney.com/api/qt/stock/get"
PARAMS = {"secid": "1.600519", "fields": "f43,f57,f58,f60,f86", "invt": "2", "fltt": "1"}
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 FMI/0.1.0"
)
HEADERS = {"User-Agent": UA, "Referer": "https://quote.eastmoney.com/", "Accept": "*/*"}

_real_getaddrinfo = socket.getaddrinfo


class family_guard:
    """临时把 getaddrinfo 限制到指定地址族。"""

    def __init__(self, family: int) -> None:
        self.family = family

    def __enter__(self) -> None:
        real = _real_getaddrinfo

        def limited(host, port, fam=0, type=0, proto=0, flags=0):  # noqa: A002
            return real(host, port, self.family, type, proto, flags)

        socket.getaddrinfo = limited  # type: ignore[assignment]

    def __exit__(self, *exc: object) -> None:
        socket.getaddrinfo = _real_getaddrinfo  # type: ignore[assignment]


def chain(exc: BaseException) -> list[str]:
    out, cur = [], exc
    while cur is not None and len(out) < 4:
        out.append(f"{cur.__class__.__name__}: {str(cur)[:160]}")
        cur = cur.__cause__ or cur.__context__
    return out


def via_requests(family: int) -> dict[str, Any]:
    t0 = time.perf_counter()
    label = "IPv4" if family == socket.AF_INET else "IPv6"
    try:
        with family_guard(family):
            response = requests.get(URL, params=PARAMS, headers=HEADERS, timeout=12)
        peer = response.raw._connection.sock.getpeername()[0] if getattr(response.raw, "_connection", None) else None
        return {
            "client": "requests",
            "family": label,
            "outcome": "http",
            "status": response.status_code,
            "peer": peer,
            "ms": round((time.perf_counter() - t0) * 1000, 1),
        }
    except Exception as exc:  # noqa: BLE001
        return {"client": "requests", "family": label, "outcome": "aborted",
                "ms": round((time.perf_counter() - t0) * 1000, 1), "chain": chain(exc)}


def via_httpx(family: int) -> dict[str, Any]:
    t0 = time.perf_counter()
    label = "IPv4" if family == socket.AF_INET else "IPv6"
    try:
        with family_guard(family):
            with httpx.Client(timeout=12, headers=HEADERS, trust_env=False) as client:
                response = client.get(URL, params=PARAMS)
                stream = response.extensions.get("network_stream")
                peer = stream.get_extra_info("server_addr") if stream else None
        return {
            "client": "httpx",
            "family": label,
            "outcome": "http",
            "status": response.status_code,
            "peer": peer[0] if peer else None,
            "ms": round((time.perf_counter() - t0) * 1000, 1),
        }
    except Exception as exc:  # noqa: BLE001
        return {"client": "httpx", "family": label, "outcome": "aborted",
                "ms": round((time.perf_counter() - t0) * 1000, 1), "chain": chain(exc)}


def main() -> int:
    results = []
    for func in (via_requests, via_httpx):
        for family in (socket.AF_INET, socket.AF_INET6):
            results.append(func(family))
            time.sleep(1.5)
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
