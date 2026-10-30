"""东财（Eastmoney）数据源专项诊断 —— 低频、有限次探测。

设计原则：
* 每个端点只发一次请求，不做重试、不并发、不翻页；
* 记录 DNS、TLS、HTTP 状态、响应头与响应体片段，区分「服务端明确拒绝」与
  「连接被服务端断开」两种情况；
* 不做任何指纹伪装、不使用代理、不绕过访问限制。

运行：.venv\\Scripts\\python.exe scripts\\diagnose_eastmoney.py
"""

from __future__ import annotations

import json
import os
import socket
import ssl
import sys
import time
from typing import Any
from urllib.parse import urlsplit

import httpx

TIMEOUT = 12.0
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 FMI/0.1.0"
)

# 单次请求，非翻页。secid=1.600519 是「沪市 + 600519」的东财内部代码。
PROBES: list[tuple[str, str, dict[str, str]]] = [
    (
        "push2 单只报价 (stock/get)",
        "https://push2.eastmoney.com/api/qt/stock/get",
        {"secid": "1.600519", "fields": "f43,f57,f58,f60,f169,f170,f86", "invt": "2", "fltt": "1"},
    ),
    (
        "push2delay 单只报价 (stock/get)",
        "https://push2delay.eastmoney.com/api/qt/stock/get",
        {"secid": "1.600519", "fields": "f43,f57,f58,f60,f169,f170,f86", "invt": "2", "fltt": "1"},
    ),
    (
        "push2his 日K (kline/get)",
        "https://push2his.eastmoney.com/api/qt/stock/kline/get",
        {
            "secid": "1.600519",
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57",
            "klt": "101",
            "fqt": "0",
            "beg": "20251001",
            "end": "20251030",
            "lmt": "10",
        },
    ),
    (
        "82.push2 列表首页 (clist/get, 仅 1 页 20 条)",
        "https://82.push2.eastmoney.com/api/qt/clist/get",
        {
            "pn": "1",
            "pz": "20",
            "po": "1",
            "np": "1",
            "fltt": "1",
            "invt": "2",
            "fid": "f3",
            "fs": "m:0+t:6",
            "fields": "f12,f14,f2,f3",
        },
    ),
    (
        "datacenter-web 数据中心",
        "https://datacenter-web.eastmoney.com/api/data/v1/get",
        {"reportName": "RPT_VALUEANALYSIS_DET", "columns": "ALL", "pageNumber": "1", "pageSize": "2"},
    ),
]

HOSTS = [
    "push2.eastmoney.com",
    "push2delay.eastmoney.com",
    "push2his.eastmoney.com",
    "82.push2.eastmoney.com",
    "datacenter-web.eastmoney.com",
]


def dns_block() -> dict[str, Any]:
    out: dict[str, Any] = {}
    for host in HOSTS:
        t0 = time.perf_counter()
        try:
            infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
            addrs = sorted({info[4][0] for info in infos})
            out[host] = {"ok": True, "addrs": addrs, "ms": round((time.perf_counter() - t0) * 1000, 1)}
        except OSError as exc:
            out[host] = {"ok": False, "error": f"{exc.__class__.__name__}: {exc}"}
    return out


def tls_block(host: str = "push2his.eastmoney.com") -> dict[str, Any]:
    ctx = ssl.create_default_context()
    t0 = time.perf_counter()
    try:
        with socket.create_connection((host, 443), timeout=TIMEOUT) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as tls:
                return {
                    "ok": True,
                    "host": host,
                    "protocol": tls.version(),
                    "cipher": tls.cipher()[0] if tls.cipher() else None,
                    "cert_subject": dict(x[0] for x in tls.getpeercert().get("subject", ())),
                    "ms": round((time.perf_counter() - t0) * 1000, 1),
                }
    except Exception as exc:  # noqa: BLE001 - diagnostic only
        return {"ok": False, "host": host, "error": f"{exc.__class__.__name__}: {exc}"}


def probe(label: str, url: str, params: dict[str, str]) -> dict[str, Any]:
    headers = {"User-Agent": UA, "Referer": "https://quote.eastmoney.com/", "Accept": "*/*"}
    started = time.perf_counter()
    # trust_env=False：不读取任何代理环境变量，确保测的是本机直连出口
    with httpx.Client(timeout=TIMEOUT, headers=headers, trust_env=False, follow_redirects=False) as client:
        try:
            response = client.get(url, params=params)
        except Exception as exc:  # noqa: BLE001 - the interesting case
            chain = []
            cur: BaseException | None = exc
            while cur is not None and len(chain) < 6:
                chain.append(f"{cur.__class__.__name__}: {cur}")
                cur = cur.__cause__ or cur.__context__
            return {
                "label": label,
                "url": url,
                "outcome": "connection-aborted",
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
                "exception_chain": chain,
            }

    interesting = {
        key: response.headers.get(key)
        for key in ("content-type", "retry-after", "x-ratelimit-limit", "x-ratelimit-remaining", "server", "date")
        if response.headers.get(key) is not None
    }
    body = response.text
    return {
        "label": label,
        "url": url,
        "outcome": "http-response",
        "status_code": response.status_code,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        "headers": interesting,
        "body_len": len(body),
        "body_head": body[:400],
        "json_ok": False if body and not body.lstrip().startswith(("{", "[")) else bool(body),
    }


def main() -> int:
    report: dict[str, Any] = {"started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}

    report["proxy_env"] = {
        key: os.environ.get(key)
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "NO_PROXY")
        if os.environ.get(key)
    } or "none"
    try:
        import akshare.utils.context.config as ak_config

        report["akshare_config"] = {
            "timeout": getattr(ak_config, "timeout", None),
            "proxies": getattr(ak_config, "proxies", None),
        }
    except Exception as exc:  # noqa: BLE001
        report["akshare_config"] = f"{exc.__class__.__name__}: {exc}"

    report["dns"] = dns_block()
    report["tls"] = tls_block()

    results = []
    for label, url, params in PROBES:
        results.append(probe(label, url, params))
        time.sleep(1.0)  # 主动放慢，避免连续请求被判定为高频
    report["probes"] = results
    report["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
