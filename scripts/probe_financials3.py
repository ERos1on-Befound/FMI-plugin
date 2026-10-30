"""Third probe: dump exact item names so the mapping layer is grounded in real data."""

from __future__ import annotations

import json
from pathlib import Path

import httpx

UA = "FMI Research dev-contact fmi@example.local"
OUT_DIR = Path(__file__).resolve().parent.parent / "artifacts" / "probe"
OUT_DIR.mkdir(parents=True, exist_ok=True)

SEC_CANDIDATES = {
    "AAPL": 320193,
    "MSFT": 789019,
    "NVDA": 1045810,
}

WANTED = [
    "Revenues",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "SalesRevenueNet",
    "GrossProfit",
    "CostOfRevenue",
    "CostOfGoodsAndServicesSold",
    "CostOfGoodsSold",
    "OperatingIncomeLoss",
    "NetIncomeLoss",
    "ProfitLoss",
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
    "LongTermDebtNoncurrent",
    "CommonStockSharesOutstanding",
    "WeightedAverageNumberOfSharesOutstandingBasic",
]


def sec_presence() -> dict:
    out: dict[str, object] = {}
    with httpx.Client(timeout=60, headers={"User-Agent": UA}) as client:
        for ticker, cik in SEC_CANDIDATES.items():
            url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
            facts = client.get(url).json()["facts"].get("us-gaap", {})
            out[ticker] = {
                tag: ("duration" if "start" in facts[tag]["units"][next(iter(facts[tag]["units"]))][-1] else "instant")
                for tag in WANTED
                if tag in facts
            }
            out[ticker]["_entity"] = None
    return out


def a_share_items() -> dict:
    import akshare as ak

    out: dict[str, object] = {}
    for symbol in ("利润表", "资产负债表", "现金流量表"):
        frame = ak.stock_financial_report_sina(stock="sh600519", symbol=symbol)
        item_columns = [c for c in frame.columns if c != "报告日"]
        first = frame.iloc[0]
        out[symbol] = {
            "periods": len(frame),
            "latest_period": str(first["报告日"]),
            "items": item_columns,
            "sample": {
                name: first[name]
                for name in item_columns[:200]
                if first[name] is not None and first[name] == first[name]
            },
        }
    return out


def hk_items() -> dict:
    import akshare as ak

    out: dict[str, object] = {}
    for symbol in ("利润表", "资产负债表", "现金流量表"):
        frame = ak.stock_financial_hk_report_em(stock="00700", symbol=symbol, indicator="年度")
        out[symbol] = {
            "rows": len(frame),
            "items": [
                {"name": str(name), "code": str(code)}
                for name, code in frame[["STD_ITEM_NAME", "STD_ITEM_CODE"]]
                .drop_duplicates()
                .itertuples(index=False, name=None)
            ],
        }
    return out


def main() -> None:
    result = {"sec": sec_presence(), "a_share": a_share_items(), "hk": hk_items()}
    (OUT_DIR / "financial_item_catalog.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
    )
    summary = {
        "sec_tags_present": {k: len(v) for k, v in result["sec"].items()},
        "a_share_items": {k: len(v["items"]) for k, v in result["a_share"].items()},
        "a_share_latest": {k: v["latest_period"] for k, v in result["a_share"].items()},
        "hk_items": {k: len(v["items"]) for k, v in result["hk"].items()},
        "catalog": str(OUT_DIR / "financial_item_catalog.json"),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
