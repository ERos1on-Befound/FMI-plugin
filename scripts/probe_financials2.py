"""Second structural probe: exact layouts needed to write the mapping layer."""

from __future__ import annotations

import json
import traceback

import httpx
import pandas as pd

UA = "FMI Research dev-contact fmi@example.local"
OUT: dict[str, object] = {}

SEC_TAGS = [
    "Revenues",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "GrossProfit",
    "OperatingIncomeLoss",
    "NetIncomeLoss",
    "EarningsPerShareBasic",
    "EarningsPerShareDiluted",
    "Assets",
    "Liabilities",
    "StockholdersEquity",
    "AssetsCurrent",
    "LiabilitiesCurrent",
    "InventoryNet",
    "CashAndCashEquivalentsAtCarryingValue",
    "AccountsReceivableNetCurrent",
    "NetCashProvidedByUsedInOperatingActivities",
    "PaymentsToAcquirePropertyPlantAndEquipment",
    "NetCashProvidedByUsedInInvestingActivities",
    "NetCashProvidedByUsedInFinancingActivities",
    "IncomeTaxExpenseBenefit",
    "InterestExpense",
    "CostOfRevenue",
    "LongTermDebtNoncurrent",
    "CommonStockSharesOutstanding",
]


def probe_sec_tags() -> None:
    url = "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json"
    with httpx.Client(timeout=60, headers={"User-Agent": UA}) as client:
        facts = client.get(url).json()["facts"]["us-gaap"]
    rows = {}
    for tag in SEC_TAGS:
        entry = facts.get(tag)
        if not entry:
            rows[tag] = None
            continue
        units = entry["units"]
        unit = next(iter(units))
        items = units[unit]
        annual = [
            u for u in items if u.get("form") == "10-K" and u.get("fp") == "FY" and "start" in u
        ]
        instant = [u for u in items if u.get("form") == "10-K" and "start" not in u]
        pick = (annual or instant)[-1] if (annual or instant) else items[-1]
        rows[tag] = {
            "unit": unit,
            "count": len(items),
            "duration_kind": "duration" if "start" in pick else "instant",
            "latest": {k: pick.get(k) for k in ("start", "end", "val", "fy", "fp", "form", "filed")},
        }
    OUT["sec_aapl_tags"] = rows
    OUT["sec_aapl_has_dei_shares"] = "dei" in facts


def probe_akshare_frames() -> None:
    import akshare as ak

    for symbol in ("利润表", "资产负债表", "现金流量表"):
        frame = ak.stock_financial_report_sina(stock="sh600519", symbol=symbol)
        dates = [c for c in frame.columns if c != "报告日"]
        OUT[f"ak_{symbol}"] = {
            "shape": list(frame.shape),
            "index_name": frame.index.name,
            "report_dates": [str(d) for d in dates[:6]],
            "items": [str(v) for v in frame["报告日"].tolist()[:40]],
            "sample": {
                str(frame.iloc[i]["报告日"]): frame.iloc[i][dates[0]] if dates else None
                for i in range(min(3, len(frame)))
            },
        }


def probe_hk_items() -> None:
    import akshare as ak

    frame = ak.stock_financial_hk_report_em(stock="00700", symbol="利润表", indicator="年度")
    OUT["ak_hk_income"] = {
        "shape": list(frame.shape),
        "columns": list(frame.columns),
        "dates": sorted({str(v) for v in frame["REPORT_DATE"].tolist()})[:6],
        "items": [str(v) for v in frame["STD_ITEM_NAME"].unique().tolist()[:40]],
        "sample_dtypes": {str(k): str(v) for k, v in frame.dtypes.items()},
    }


def main() -> None:
    for name, fn in (
        ("sec_aapl_tags", probe_sec_tags),
        ("ak_frames", probe_akshare_frames),
        ("hk", probe_hk_items),
    ):
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            OUT[name] = f"{type(exc).__name__}: {exc}"
            OUT[name + "_trace"] = traceback.format_exc(limit=3)[-500:]
    print(json.dumps(OUT, ensure_ascii=True, indent=2, default=str)[:12000])


if __name__ == "__main__":
    main()
