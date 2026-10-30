"""Probe free financial-statement sources for Phase 2 (limited, single request each).

Order: SEC EDGAR (US) -> AKShare Sina/A-share + HK interfaces.
Prints a compact JSON summary so results are auditable.
"""

from __future__ import annotations

import json
import time
import traceback

import httpx

UA = "FMI Research dev-contact fmi@example.local"
REPORT: dict[str, object] = {}


def timed(label: str, fn):
    started = time.perf_counter()
    try:
        value = fn()
        REPORT[label] = {
            "ok": True,
            "seconds": round(time.perf_counter() - started, 2),
            **(value if isinstance(value, dict) else {"result": str(value)[:400]}),
        }
    except Exception as exc:  # noqa: BLE001 - probe
        REPORT[label] = {
            "ok": False,
            "seconds": round(time.perf_counter() - started, 2),
            "error": f"{type(exc).__name__}: {str(exc)[:300]}",
            "trace": traceback.format_exc(limit=2)[-300:],
        }


def probe_sec_tickers():
    with httpx.Client(timeout=20, headers={"User-Agent": UA}) as client:
        response = client.get("https://www.sec.gov/files/company_tickers.json")
        response.raise_for_status()
        data = response.json()
        apple = [row for row in data.values() if row.get("ticker") == "AAPL"]
        return {"status": response.status_code, "count": len(data), "apple": apple[:1]}


def probe_sec_concept_periods():
    url = "https://data.sec.gov/api/xbrl/companyconcept/CIK0000320193/us-gaap/Revenues.json"
    with httpx.Client(timeout=25, headers={"User-Agent": UA}) as client:
        response = client.get(url)
        response.raise_for_status()
        units = response.json()["units"]["USD"]
        fy = [u for u in units if u.get("form") == "10-K" and u.get("fp") == "FY"]
        latest = fy[-1] if fy else None
        return {
            "status": response.status_code,
            "unit_count": len(units),
            "annual_count": len(fy),
            "latest": {
                k: latest[k]
                for k in ("start", "end", "val", "fy", "fp", "form", "filed")
                if k in latest
            },
        }


def probe_sec_companyfacts_size():
    url = "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json"
    with httpx.Client(timeout=60, headers={"User-Agent": UA}) as client:
        response = client.get(url)
        response.raise_for_status()
        body = response.json()
        facts = body["facts"]
        return {
            "status": response.status_code,
            "bytes": len(response.content),
            "taxonomies": sorted(facts),
            "us_gaap_tags": len(facts.get("us-gaap", {})),
            "dei_tags": sorted(facts.get("dei", {}))[:8],
        }


def probe_akshare_sina_report():
    import akshare as ak

    frame = ak.stock_financial_report_sina(stock="sh600519", symbol="资产负债表")
    return {"rows": len(frame), "columns": list(frame.columns)[:12]}


def probe_akshare_abstract():
    import akshare as ak

    frame = ak.stock_financial_abstract(symbol="600519")
    return {"rows": len(frame), "columns": list(frame.columns)[:14]}


def probe_akshare_hk_report():
    import akshare as ak

    frame = ak.stock_financial_hk_report_em(stock="00700", symbol="资产负债表", indicator="年度")
    return {"rows": len(frame), "columns": list(frame.columns)[:10]}


def main() -> None:
    timed("sec_tickers", probe_sec_tickers)
    timed("sec_concept_aapl_revenues", probe_sec_concept_periods)
    timed("sec_companyfacts_aapl", probe_sec_companyfacts_size)
    timed("ak_sina_balance_sheet_600519", probe_akshare_sina_report)
    timed("ak_financial_abstract_600519", probe_akshare_abstract)
    timed("ak_hk_report_00700", probe_akshare_hk_report)
    print(json.dumps(REPORT, ensure_ascii=True, indent=2, default=str))


if __name__ == "__main__":
    main()
