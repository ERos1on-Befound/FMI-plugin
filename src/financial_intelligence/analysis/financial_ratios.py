"""Turn canonical financial statements into financial metrics.

Every metric here is a thin, documented mapping between canonical statement
fields and one formula from :mod:`financial_intelligence.analysis.financial_formulas`.
That keeps a single implementation of each ratio and guarantees that the
``calculate_financial_metrics`` tool and the fundamental analysis report always
agree with the formula engine.

Rules that the code enforces (mirroring the task brief):

* a missing input is ``insufficient_data`` – never ``0``;
* a formula that does not apply is ``not_applicable`` and says why;
* valuation metrics require a price in the **same currency** as the statements,
  because no free FX conversion is available;
* average balances use the previous period when present and are labelled
  "简化口径" when only one period-end value exists.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

from ..models.analysis_result import MetricSeries, MetricValue, TrendLabel
from ..models.financial_statements import FinancialStatement
from .financial_formulas import FORMULAS, compute_formula, format_value

#: Relative change below which a series is considered ``stable``.
TREND_TOLERANCE = 0.05

DIMENSIONS = {
    "profitability": "盈利能力",
    "solvency": "偿债能力",
    "efficiency": "运营效率",
    "growth": "成长",
    "cash_flow": "现金流",
    "valuation": "估值",
}


@dataclass(frozen=True)
class QuoteContext:
    """A price snapshot used by valuation metrics."""

    price: float | None = None
    currency: str | None = None
    timestamp: str | None = None
    source: str = "unknown"
    symbol: str | None = None


@dataclass(frozen=True)
class MetricSpec:
    """Documented mapping from statements to one formula."""

    name: str
    formula_name: str
    label: str
    dimension: str
    input_fields: tuple[str, ...]
    build: Callable[
        [FinancialStatement, FinancialStatement | None, QuoteContext | None],
        dict[str, float | None],
    ]
    requires_quote: bool = False
    limitations: tuple[str, ...] = ()

    @property
    def category(self) -> str:
        return DIMENSIONS.get(self.dimension, self.dimension)

    @property
    def formula(self) -> str:
        spec = FORMULAS.get(self.formula_name)
        return spec.expression if spec else ""

    @property
    def unit(self) -> str:
        spec = FORMULAS.get(self.formula_name)
        return spec.output_unit if spec else "ratio"


# ---------------------------------------------------------------------------
# field helpers
# ---------------------------------------------------------------------------
def _income(statement: FinancialStatement, field: str) -> float | None:
    return statement.income_statement.get(field)


def _balance(statement: FinancialStatement, field: str) -> float | None:
    return statement.balance_sheet.get(field)


def _cash(statement: FinancialStatement, field: str) -> float | None:
    return statement.cash_flow_statement.get(field)


def _eps(statement: FinancialStatement) -> float | None:
    """Diluted EPS when disclosed, otherwise basic EPS (documented preference)."""
    diluted = _income(statement, "eps_diluted")
    if diluted is not None:
        return diluted
    return _income(statement, "eps_basic")


def _book_value_per_share(statement: FinancialStatement) -> float | None:
    shares = _balance(statement, "shares_outstanding")
    if shares is None:
        shares = _income(statement, "shares_diluted")
    equity = _balance(statement, "equity_to_parent")
    if equity is None:
        equity = _balance(statement, "total_equity")
    if not shares or equity is None or shares <= 0:
        return None
    return equity / shares


def _dividend_per_share(statement: FinancialStatement) -> float | None:
    shares = _balance(statement, "shares_outstanding")
    if shares is None:
        shares = _income(statement, "shares_diluted")
    paid = _cash(statement, "dividends_paid")
    if not shares or paid is None or shares <= 0:
        return None
    return abs(paid) / shares


def _market_cap(quote: QuoteContext | None, statement: FinancialStatement) -> float | None:
    shares = _balance(statement, "shares_outstanding")
    if quote is None or quote.price is None or not shares:
        return None
    return quote.price * shares


def _same_currency(quote: QuoteContext | None, statement: FinancialStatement) -> bool:
    if quote is None or not quote.currency:
        return False
    return quote.currency.upper() == statement.currency.upper()


# ---------------------------------------------------------------------------
# metric definitions
# ---------------------------------------------------------------------------
def _build_gross_margin(cur: FinancialStatement, prev: FinancialStatement | None, quote: QuoteContext | None) -> dict[str, float | None]:
    return {"revenue": _income(cur, "revenue"), "cost_of_revenue": _income(cur, "cost_of_revenue")}


def _build_operating_margin(cur: FinancialStatement, prev: FinancialStatement | None, quote: QuoteContext | None) -> dict[str, float | None]:
    return {"operating_income": _income(cur, "operating_income"), "revenue": _income(cur, "revenue")}


def _build_net_margin(cur: FinancialStatement, prev: FinancialStatement | None, quote: QuoteContext | None) -> dict[str, float | None]:
    return {"net_income": _income(cur, "net_income"), "revenue": _income(cur, "revenue")}


def _build_roe(cur: FinancialStatement, prev: FinancialStatement | None, quote: QuoteContext | None) -> dict[str, float | None]:
    return {
        "net_income": _income(cur, "net_income"),
        "equity_begin": _balance(prev, "total_equity") if prev else None,
        "equity_end": _balance(cur, "total_equity"),
    }


def _build_roa(cur: FinancialStatement, prev: FinancialStatement | None, quote: QuoteContext | None) -> dict[str, float | None]:
    return {
        "net_income": _income(cur, "net_income"),
        "assets_begin": _balance(prev, "total_assets") if prev else None,
        "assets_end": _balance(cur, "total_assets"),
    }


def _build_debt_ratio(cur: FinancialStatement, prev: FinancialStatement | None, quote: QuoteContext | None) -> dict[str, float | None]:
    return {"total_liabilities": _balance(cur, "total_liabilities"), "total_assets": _balance(cur, "total_assets")}


def _build_current_ratio(cur: FinancialStatement, prev: FinancialStatement | None, quote: QuoteContext | None) -> dict[str, float | None]:
    return {"current_assets": _balance(cur, "current_assets"), "current_liabilities": _balance(cur, "current_liabilities")}


def _build_quick_ratio(cur: FinancialStatement, prev: FinancialStatement | None, quote: QuoteContext | None) -> dict[str, float | None]:
    return {
        "current_assets": _balance(cur, "current_assets"),
        "inventory": _balance(cur, "inventory"),
        "current_liabilities": _balance(cur, "current_liabilities"),
        "prepayments": _balance(cur, "prepayments"),
    }


def _build_interest_coverage(cur: FinancialStatement, prev: FinancialStatement | None, quote: QuoteContext | None) -> dict[str, float | None]:
    # EBIT is approximated by operating income; a real EBIT requires interest and
    # tax add-backs that are only meaningful when both are disclosed.
    return {"ebit": _income(cur, "operating_income"), "interest_expense": _income(cur, "interest_expense")}


def _build_asset_turnover(cur: FinancialStatement, prev: FinancialStatement | None, quote: QuoteContext | None) -> dict[str, float | None]:
    return {
        "revenue": _income(cur, "revenue"),
        "assets_begin": _balance(prev, "total_assets") if prev else None,
        "assets_end": _balance(cur, "total_assets"),
    }


def _build_receivables_turnover(cur: FinancialStatement, prev: FinancialStatement | None, quote: QuoteContext | None) -> dict[str, float | None]:
    return {
        "revenue": _income(cur, "revenue"),
        "receivables_begin": _balance(prev, "accounts_receivable") if prev else None,
        "receivables_end": _balance(cur, "accounts_receivable"),
    }


def _build_inventory_turnover(cur: FinancialStatement, prev: FinancialStatement | None, quote: QuoteContext | None) -> dict[str, float | None]:
    return {
        "cost_of_revenue": _income(cur, "cost_of_revenue"),
        "inventory_begin": _balance(prev, "inventory") if prev else None,
        "inventory_end": _balance(cur, "inventory"),
    }


def _build_revenue_growth(cur: FinancialStatement, prev: FinancialStatement | None, quote: QuoteContext | None) -> dict[str, float | None]:
    return {
        "current": _income(cur, "revenue"),
        "previous": _income(prev, "revenue") if prev else None,
    }


def _build_net_income_growth(cur: FinancialStatement, prev: FinancialStatement | None, quote: QuoteContext | None) -> dict[str, float | None]:
    return {
        "current": _income(cur, "net_income"),
        "previous": _income(prev, "net_income") if prev else None,
    }


def _build_eps_growth(cur: FinancialStatement, prev: FinancialStatement | None, quote: QuoteContext | None) -> dict[str, float | None]:
    return {
        "eps_current": _eps(cur),
        "eps_previous": _eps(prev) if prev else None,
    }


def _build_free_cash_flow(cur: FinancialStatement, prev: FinancialStatement | None, quote: QuoteContext | None) -> dict[str, float | None]:
    return {
        "operating_cash_flow": _cash(cur, "operating_cash_flow"),
        "capital_expenditure": _cash(cur, "capital_expenditure"),
    }


def _build_cfo_to_net_income(cur: FinancialStatement, prev: FinancialStatement | None, quote: QuoteContext | None) -> dict[str, float | None]:
    return {
        "operating_cash_flow": _cash(cur, "operating_cash_flow"),
        "net_income": _income(cur, "net_income"),
    }


def _build_pe(cur: FinancialStatement, prev: FinancialStatement | None, quote: QuoteContext | None) -> dict[str, float | None]:
    return {"price": quote.price if quote else None, "eps": _eps(cur)}


def _build_pb(cur: FinancialStatement, prev: FinancialStatement | None, quote: QuoteContext | None) -> dict[str, float | None]:
    return {"price": quote.price if quote else None, "book_value_per_share": _book_value_per_share(cur)}


def _build_ps(cur: FinancialStatement, prev: FinancialStatement | None, quote: QuoteContext | None) -> dict[str, float | None]:
    return {"market_cap": _market_cap(quote, cur), "revenue": _income(cur, "revenue")}


def _build_dividend_yield(cur: FinancialStatement, prev: FinancialStatement | None, quote: QuoteContext | None) -> dict[str, float | None]:
    return {
        "dividend_per_share": _dividend_per_share(cur),
        "price": quote.price if quote else None,
    }


METRIC_SPECS: dict[str, MetricSpec] = {
    spec.name: spec
    for spec in (
        MetricSpec("gross_margin", "gross_margin", "毛利率", "profitability", ("revenue", "cost_of_revenue"), _build_gross_margin,
                   limitations=("营业成本口径因准则而异。",)),
        MetricSpec("operating_margin", "operating_margin", "营业利润率", "profitability", ("revenue", "operating_income"), _build_operating_margin),
        MetricSpec("net_margin", "net_margin", "净利率", "profitability", ("revenue", "net_income"), _build_net_margin,
                   limitations=("净利润可能含非经常性损益。",)),
        MetricSpec("roe", "return_on_equity", "净资产收益率 (ROE)", "profitability", ("net_income", "total_equity"), _build_roe),
        MetricSpec("roa", "return_on_assets", "总资产收益率 (ROA)", "profitability", ("net_income", "total_assets"), _build_roa),
        MetricSpec("debt_ratio", "debt_ratio", "资产负债率", "solvency", ("total_liabilities", "total_assets"), _build_debt_ratio,
                   limitations=("金融机构与制造业的负债率不可直接比较。",)),
        MetricSpec("current_ratio", "current_ratio", "流动比率", "solvency", ("current_assets", "current_liabilities"), _build_current_ratio),
        MetricSpec("quick_ratio", "quick_ratio", "速动比率", "solvency", ("current_assets", "inventory", "current_liabilities"), _build_quick_ratio),
        MetricSpec("interest_coverage", "interest_coverage", "利息保障倍数", "solvency", ("operating_income", "interest_expense"), _build_interest_coverage,
                   limitations=("EBIT 以营业利润近似；利息费用缺失时不可计算。",)),
        MetricSpec("asset_turnover", "asset_turnover", "总资产周转率", "efficiency", ("revenue", "total_assets"), _build_asset_turnover),
        MetricSpec("receivables_turnover", "receivables_turnover", "应收账款周转率", "efficiency", ("revenue", "accounts_receivable"), _build_receivables_turnover),
        MetricSpec("inventory_turnover", "inventory_turnover", "存货周转率", "efficiency", ("cost_of_revenue", "inventory"), _build_inventory_turnover),
        MetricSpec("revenue_growth", "yoy_growth", "营业收入同比增长率", "growth", ("revenue",), _build_revenue_growth),
        MetricSpec("net_income_growth", "yoy_growth", "净利润同比增长率", "growth", ("net_income",), _build_net_income_growth),
        MetricSpec("eps_growth", "eps_growth", "每股收益同比增长率", "growth", ("eps_basic", "eps_diluted"), _build_eps_growth),
        MetricSpec("free_cash_flow", "free_cash_flow", "自由现金流 (FCF)", "cash_flow", ("operating_cash_flow", "capital_expenditure"), _build_free_cash_flow),
        MetricSpec("cfo_to_net_income", "cfo_to_net_income", "经营现金流 / 净利润", "cash_flow", ("operating_cash_flow", "net_income"), _build_cfo_to_net_income),
        MetricSpec("pe", "pe_ratio", "市盈率 (PE)", "valuation", ("eps_basic", "eps_diluted"), _build_pe, requires_quote=True,
                   limitations=("EPS 口径需与股价时点匹配。",)),
        MetricSpec("pb", "pb_ratio", "市净率 (PB)", "valuation", ("total_equity", "shares_outstanding"), _build_pb, requires_quote=True),
        MetricSpec("ps", "ps_ratio", "市销率 (PS)", "valuation", ("revenue", "shares_outstanding"), _build_ps, requires_quote=True),
        MetricSpec("dividend_yield", "dividend_yield", "股息率", "valuation", ("dividends_paid", "shares_outstanding"), _build_dividend_yield, requires_quote=True,
                   limitations=("使用已支付股息，属于历史股息率。",)),
    )
}

METRIC_NAMES: tuple[str, ...] = tuple(METRIC_SPECS)
DIMENSION_ORDER: tuple[str, ...] = tuple(DIMENSIONS)
DIMENSION_METRICS: dict[str, tuple[str, ...]] = {
    dimension: tuple(name for name, spec in METRIC_SPECS.items() if spec.dimension == dimension)
    for dimension in DIMENSIONS
}


def resolve_metric_name(name: str) -> str | None:
    from .financial_formulas import normalize_name

    normalized = normalize_name(name)
    if normalized in METRIC_SPECS:
        return normalized
    aliases = {
        "return_on_equity": "roe",
        "return_on_assets": "roa",
        "revenue_yoy": "revenue_growth",
        "net_income_yoy": "net_income_growth",
        "pe_ratio": "pe",
        "pb_ratio": "pb",
        "ps_ratio": "ps",
        "fcf": "free_cash_flow",
        "cfo_ni": "cfo_to_net_income",
        "gross_profit_margin": "gross_margin",
        "operating_profit_margin": "operating_margin",
        "profit_margin": "net_margin",
        "asset_turnover_ratio": "asset_turnover",
        "receivable_turnover": "receivables_turnover",
        "debt_to_assets": "debt_ratio",
        "current_ratio_ratio": "current_ratio",
        "quick_ratio_ratio": "quick_ratio",
        "interest_cover": "interest_coverage",
    }
    return aliases.get(normalized)


# ---------------------------------------------------------------------------
# computation
# ---------------------------------------------------------------------------
def _valuation_blocked(spec: MetricSpec, statement: FinancialStatement, quote: QuoteContext | None) -> str | None:
    """Return a reason when a valuation metric cannot be computed honestly."""
    if not spec.requires_quote:
        return None
    if quote is None or quote.price is None:
        return "缺少可用的最新股价，无法计算估值指标。"
    if not _same_currency(quote, statement):
        return (
            f"行情币种 {quote.currency or '未知'} 与财报币种 {statement.currency} 不一致，"
            "项目没有免费的汇率换算来源，因此不生成该估值指标（避免口径混用）。"
        )
    return None


def _iso(value: object) -> str | None:
    """Render a date/datetime/str as an ISO string (or None)."""
    if value is None:
        return None
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return isoformat()
    return str(value)


def compute_metric(
    name: str,
    statements: Sequence[FinancialStatement],
    index: int = 0,
    *,
    quote: QuoteContext | None = None,
) -> MetricValue:
    """Compute one metric for ``statements[index]`` (statements are newest first)."""
    spec = METRIC_SPECS[name]
    current = statements[index]
    previous = statements[index + 1] if index + 1 < len(statements) else None

    blocked = _valuation_blocked(spec, current, quote)
    if blocked is not None:
        return MetricValue(
            name=spec.name,
            label=spec.label,
            category=spec.category,
            period=current.fiscal_period,
            period_type=current.period_type,
            value=None,
            unit=spec.unit,
            display="不可计算",
            formula=spec.formula,
            input_fields=list(spec.input_fields),
            calculation_status="not_applicable",
            explanation=blocked,
            source=current.source,
            filing_date=_iso(current.filing_date),
            currency=current.currency,
            notes=list(spec.limitations),
        )

    inputs = spec.build(current, previous, quote)
    result = compute_formula(spec.formula_name, inputs)
    derived = bool(set(spec.input_fields) & set(current.derived_fields))
    notes = list(spec.limitations)
    notes.extend(result.notes)
    unit = result.unit
    display = result.display or format_value(result.value, result.unit)
    if spec.name == "free_cash_flow":
        # A cash amount is expressed in the reporting currency, not as a ratio.
        unit = current.currency
        display = format_value(result.value, unit)
    return MetricValue(
        name=spec.name,
        label=spec.label,
        category=spec.category,
        period=current.fiscal_period,
        period_type=current.period_type,
        value=result.value,
        unit=unit,
        display=display,
        formula=result.expression,
        input_fields=list(spec.input_fields),
        inputs={key: value for key, value in inputs.items()},
        calculation_status=result.calculation_status,
        explanation=result.explanation,
        source=current.source,
        filing_date=_iso(current.filing_date),
        currency=current.currency,
        derived=derived,
        notes=notes,
    )


def _trend(points: list[MetricValue]) -> tuple[TrendLabel, str]:
    """Trend over chronologically ordered points (oldest first)."""
    values = [point.value for point in points if point.value is not None]
    if len(values) < 2:
        return "insufficient_data", "可用期间少于 2 个，无法判断趋势。"
    first, last = values[0], values[-1]
    base = max(abs(first), abs(last), 1e-9)
    change = (last - first) / base
    if abs(change) <= TREND_TOLERANCE:
        return "stable", f"首末期间变化 {change * 100:+.1f}%，在 ±{TREND_TOLERANCE:.0%} 容差内视为基本稳定。"
    direction = "上升" if change > 0 else "下降"
    crossover = ""
    if first * last < 0:
        crossover = "区间内符号发生变化，趋势解读需结合具体数值。"
    return (
        "rising" if change > 0 else "falling",
        f"从 {first:,.4f} 到 {last:,.4f}（{change * 100:+.1f}%），整体{direction}。{crossover}",
    )


def compute_series(
    name: str,
    statements: Sequence[FinancialStatement],
    *,
    periods: int | None = None,
    quote: QuoteContext | None = None,
) -> MetricSeries:
    """Compute a metric across periods (newest period first in ``points``)."""
    spec = METRIC_SPECS[name]
    count = len(statements)
    if periods is not None:
        count = max(1, min(count, int(periods)))
    points = [compute_metric(name, statements, index, quote=quote) for index in range(count)]
    trend, note = _trend(list(reversed(points)))
    unit = spec.unit
    if spec.name == "free_cash_flow" and statements:
        unit = statements[0].currency
    return MetricSeries(
        name=spec.name,
        label=spec.label,
        category=spec.category,
        unit=unit,
        formula=spec.formula,
        points=points,
        trend=trend,
        trend_note=note,
    )


def compute_metrics(
    statements: Sequence[FinancialStatement],
    names: Iterable[str],
    *,
    periods: int | None = None,
    quote: QuoteContext | None = None,
) -> list[MetricSeries]:
    """Compute several metric series, skipping unknown names."""
    series: list[MetricSeries] = []
    for name in names:
        resolved = resolve_metric_name(name) or name
        if resolved not in METRIC_SPECS:
            continue
        series.append(compute_series(resolved, statements, periods=periods, quote=quote))
    return series


def metric_catalog() -> list[dict[str, object]]:
    """Advertise every metric for ``get_supported_indicators``."""
    catalog: list[dict[str, object]] = []
    for name, spec in METRIC_SPECS.items():
        formula_spec = FORMULAS.get(spec.formula_name)
        catalog.append(
            {
                "name": name,
                "label": spec.label,
                "category": spec.category,
                "dimension": spec.dimension,
                "unit": spec.unit,
                "formula": spec.formula,
                "inputs": list(formula_spec.input_names()) if formula_spec else list(spec.input_fields),
                "requires_quote": spec.requires_quote,
                "limitations": list(spec.limitations),
            }
        )
    return catalog


def table_for_series(series: Sequence[MetricSeries]) -> list[list[str]]:
    """Render ``指标 | 最新期 | 上期 …`` rows for report tables."""
    periods: list[str] = []
    for item in series:
        for point in item.points:
            if point.period not in periods:
                periods.append(point.period)
    header = ["指标"] + periods
    rows: list[list[str]] = [header]
    for item in series:
        by_period = {point.period: point for point in item.points}
        row = [f"{item.label}"]
        for period in periods:
            point = by_period.get(period)
            row.append(point.display if point is not None and point.value is not None else "不可计算")
        rows.append(row)
    return rows
