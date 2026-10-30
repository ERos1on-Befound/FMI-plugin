"""Manual check of the financial-statement providers against live sources.

Usage (from the project root)::

    .venv\\Scripts\\python.exe scripts\\check_financials.py AAPL
    .venv\\Scripts\\python.exe scripts\\check_financials.py 600519.SS
    .venv\\Scripts\\python.exe scripts\\check_financials.py 00700.HK

This script talks to real data sources; it is not part of the test suite.
"""

from __future__ import annotations

import json
import sys

from financial_intelligence.config import load_settings
from financial_intelligence.providers import build_financial_providers
from financial_intelligence.providers.base import ProviderError
from financial_intelligence.utils.symbol_mapper import map_symbol


def show(symbol: str, period_type: str = "annual", limit: int = 4) -> None:
    settings = load_settings()
    spec = map_symbol(symbol)
    print(json.dumps({"symbol": symbol, "market": spec.market.value, "asset_type": spec.asset_type.value}))

    providers = build_financial_providers(settings)
    if not providers:
        print("no financial provider enabled")
        return

    errors = []
    statements = None
    source = None
    for provider in providers:
        if not provider.supports_financials(spec):
            errors.append(f"{provider.name}: not supported")
            continue
        try:
            statements = provider.get_financials(spec, period_type=period_type, limit=limit)
            source = provider.name
            break
        except ProviderError as exc:
            errors.append(f"{exc.provider} [{exc.kind}]: {exc.message}")

    if not statements:
        print(json.dumps({"ok": False, "errors": errors}, ensure_ascii=False, indent=2))
        return

    print(json.dumps({"ok": True, "source": source, "count": len(statements)}, ensure_ascii=False))
    for statement in statements:
        print("-" * 78)
        print(
            json.dumps(
                {
                    "fiscal_period": statement.fiscal_period,
                    "period_type": statement.period_type,
                    "cumulative": statement.cumulative,
                    "period_start": str(statement.period_start),
                    "period_end": str(statement.period_end),
                    "filing_date": str(statement.filing_date),
                    "currency": statement.currency,
                    "unit_scale": statement.unit_scale,
                    "company_name": statement.company_name,
                },
                ensure_ascii=False,
            )
        )
        print("income :", json.dumps(_round(statement.income_statement), ensure_ascii=False))
        print("balance:", json.dumps(_round(statement.balance_sheet), ensure_ascii=False))
        print("cash   :", json.dumps(_round(statement.cash_flow_statement), ensure_ascii=False))
        if statement.missing_fields:
            print("missing:", ", ".join(statement.missing_fields))


def _round(mapping: dict[str, float | None]) -> dict[str, float | None]:
    out: dict[str, float | None] = {}
    for key, value in mapping.items():
        if value is None:
            out[key] = None
        elif abs(value) >= 1000:
            out[key] = round(value, 0)
        else:
            out[key] = round(value, 4)
    return out


if __name__ == "__main__":
    args = sys.argv[1:]
    symbol = args[0] if args else "AAPL"
    period_type = args[1] if len(args) > 1 else "annual"
    limit = int(args[2]) if len(args) > 2 else 4
    show(symbol, period_type, limit)
