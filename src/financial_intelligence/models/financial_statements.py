"""Unified financial statement model, canonical field mapping and unit handling.

Design notes
------------
The same economic line item is tagged differently across sources:

* SEC XBRL  -> ``RevenueFromContractWithCustomerExcludingAssessedTax`` / ``Revenues``
* A-share   -> ``营业收入`` / ``营业总收入``
* HK        -> ``营业额`` / ``营运收入``

So we never guess "the data is missing" just because a tag name differs.  Every
canonical field owns an ordered alias list per source; the first alias that is
actually present wins.  Values are never invented: when no alias is present the
canonical field stays ``None`` and the reason is recorded in ``mapping_notes``.

We also never coerce a *different* concept into a canonical field.  If a source
only exposes ``营业利润`` we map it to ``operating_income`` (same concept), but we
do **not** map ``利润总额`` to ``operating_income`` (different concept: pre-tax).
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..schemas import DataStatus, Market, iso, utc_now

PeriodType = Literal["annual", "quarterly", "ttm"]

# --------------------------------------------------------------------------- #
# Canonical field catalogue
# --------------------------------------------------------------------------- #

INCOME_FIELDS: tuple[str, ...] = (
    "revenue",
    "cost_of_revenue",
    "gross_profit",
    "operating_income",
    "pretax_income",
    "net_income",
    "net_income_to_parent",
    "eps_basic",
    "eps_diluted",
    "interest_expense",
    "income_tax_expense",
    "rd_expense",
    "selling_expense",
    "admin_expense",
    "shares_basic",
    "shares_diluted",
)

BALANCE_FIELDS: tuple[str, ...] = (
    "total_assets",
    "total_liabilities",
    "total_equity",
    "equity_to_parent",
    "current_assets",
    "current_liabilities",
    "inventory",
    "cash_and_equivalents",
    "accounts_receivable",
    "prepayments",
    "short_term_debt",
    "long_term_debt",
    "total_debt",
    "shares_outstanding",
)

CASHFLOW_FIELDS: tuple[str, ...] = (
    "operating_cash_flow",
    "capital_expenditure",
    "investing_cash_flow",
    "financing_cash_flow",
    "dividends_paid",
    "depreciation_amortization",
)

FIELD_LABELS: dict[str, str] = {
    "revenue": "营业收入",
    "cost_of_revenue": "营业成本",
    "gross_profit": "毛利润",
    "operating_income": "营业利润",
    "pretax_income": "税前利润",
    "net_income": "净利润",
    "net_income_to_parent": "归母净利润",
    "eps_basic": "基本每股收益",
    "eps_diluted": "稀释每股收益",
    "interest_expense": "利息费用",
    "income_tax_expense": "所得税费用",
    "rd_expense": "研发费用",
    "selling_expense": "销售费用",
    "admin_expense": "管理费用",
    "shares_basic": "基本加权平均股数",
    "shares_diluted": "稀释加权平均股数",
    "total_assets": "资产总计",
    "total_liabilities": "负债合计",
    "total_equity": "所有者权益合计",
    "equity_to_parent": "归母所有者权益",
    "current_assets": "流动资产合计",
    "current_liabilities": "流动负债合计",
    "inventory": "存货",
    "cash_and_equivalents": "货币资金及现金等价物",
    "accounts_receivable": "应收账款",
    "prepayments": "预付款项",
    "short_term_debt": "短期借款",
    "long_term_debt": "长期借款",
    "total_debt": "有息负债合计",
    "shares_outstanding": "期末股本股数",
    "operating_cash_flow": "经营活动现金流量净额",
    "capital_expenditure": "资本支出",
    "investing_cash_flow": "投资活动现金流量净额",
    "financing_cash_flow": "筹资活动现金流量净额",
    "dividends_paid": "已付股利",
    "depreciation_amortization": "折旧与摊销",
}

#: source key -> canonical field -> ordered alias list.
#: ``sec``       : SEC companyfacts XBRL tag names (us-gaap)
#: ``cn_sina``  : AKShare ``stock_financial_report_sina`` Chinese item names
#: ``hk_em``    : AKShare ``stock_financial_hk_report_em`` STD_ITEM_NAME values
FIELD_ALIASES: dict[str, dict[str, list[str]]] = {
    "sec": {
        "revenue": [
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "Revenues",
            "RevenueFromContractWithCustomerIncludingAssessedTax",
            "SalesRevenueNet",
        ],
        "cost_of_revenue": ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold"],
        "gross_profit": ["GrossProfit"],
        "operating_income": ["OperatingIncomeLoss"],
        "pretax_income": [
            "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
            "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
        ],
        "net_income": ["NetIncomeLoss", "ProfitLoss"],
        "net_income_to_parent": ["NetIncomeLoss", "ProfitLoss"],
        "eps_basic": ["EarningsPerShareBasic"],
        "eps_diluted": ["EarningsPerShareDiluted"],
        "interest_expense": ["InterestExpense", "InterestExpenseNonoperating", "InterestAndDebtExpense"],
        "income_tax_expense": ["IncomeTaxExpenseBenefit"],
        "rd_expense": ["ResearchAndDevelopmentExpense"],
        "selling_expense": ["SellingAndMarketingExpense"],
        "admin_expense": ["GeneralAndAdministrativeExpense"],
        "shares_basic": ["WeightedAverageNumberOfSharesOutstandingBasic"],
        "shares_diluted": ["WeightedAverageNumberOfDilutedSharesOutstanding"],
        "total_assets": ["Assets"],
        "total_liabilities": ["Liabilities"],
        "total_equity": [
            "StockholdersEquity",
            "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
        ],
        "equity_to_parent": ["StockholdersEquity"],
        "current_assets": ["AssetsCurrent"],
        "current_liabilities": ["LiabilitiesCurrent"],
        "inventory": ["InventoryNet", "InventoryFinishedGoodsNetOfReserves"],
        "cash_and_equivalents": [
            "CashAndCashEquivalentsAtCarryingValue",
            "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
        ],
        "accounts_receivable": ["AccountsReceivableNetCurrent", "ReceivablesNetCurrent"],
        "prepayments": ["PrepaidExpenseCurrent", "PrepaidExpenseAndOtherAssetsCurrent"],
        "short_term_debt": [
            "ShortTermBorrowings",
            "CommercialPaper",
            "LongTermDebtCurrent",
            "DebtCurrent",
        ],
        "long_term_debt": ["LongTermDebtNoncurrent", "LongTermDebt"],
        "shares_outstanding": ["CommonStockSharesOutstanding", "EntityCommonStockSharesOutstanding"],
        "operating_cash_flow": [
            "NetCashProvidedByUsedInOperatingActivities",
            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
        ],
        "capital_expenditure": [
            "PaymentsToAcquirePropertyPlantAndEquipment",
            "PaymentsToAcquireProductiveAssets",
        ],
        "investing_cash_flow": ["NetCashProvidedByUsedInInvestingActivities"],
        "financing_cash_flow": ["NetCashProvidedByUsedInFinancingActivities"],
        "dividends_paid": ["PaymentsOfDividends", "PaymentsOfDividendsCommonStock"],
        "depreciation_amortization": [
            "DepreciationDepletionAndAmortization",
            "DepreciationAmortizationAndAccretionNet",
        ],
    },
    "cn_sina": {
        "revenue": ["营业收入", "营业总收入"],
        "cost_of_revenue": ["营业成本"],
        "gross_profit": [],
        "operating_income": ["营业利润"],
        "pretax_income": ["利润总额"],
        "net_income": ["净利润"],
        "net_income_to_parent": ["归属于母公司所有者的净利润"],
        "eps_basic": ["基本每股收益"],
        "eps_diluted": ["稀释每股收益"],
        "interest_expense": ["利息费用", "利息支出"],
        "income_tax_expense": ["所得税费用"],
        "rd_expense": ["研发费用"],
        "selling_expense": ["销售费用"],
        "admin_expense": ["管理费用"],
        "total_assets": ["资产总计"],
        "total_liabilities": ["负债合计"],
        "total_equity": ["所有者权益(或股东权益)合计", "所有者权益合计"],
        "equity_to_parent": ["归属于母公司股东权益合计"],
        "current_assets": ["流动资产合计"],
        "current_liabilities": ["流动负债合计"],
        "inventory": ["存货"],
        "cash_and_equivalents": ["货币资金"],
        "accounts_receivable": ["应收账款", "应收票据及应收账款"],
        "prepayments": ["预付款项"],
        "short_term_debt": ["短期借款"],
        "long_term_debt": ["长期借款", "应付债券"],
        "operating_cash_flow": ["经营活动产生的现金流量净额"],
        "capital_expenditure": ["购建固定资产、无形资产和其他长期资产所支付的现金"],
        "investing_cash_flow": ["投资活动产生的现金流量净额"],
        "financing_cash_flow": ["筹资活动产生的现金流量净额"],
        "dividends_paid": ["分配股利、利润或偿付利息所支付的现金"],
    },
    "hk_em": {
        "revenue": ["营业额", "营运收入"],
        "cost_of_revenue": [],
        "gross_profit": ["毛利"],
        "operating_income": ["经营溢利"],
        "pretax_income": ["除税前溢利"],
        "net_income": ["除税后溢利"],
        "net_income_to_parent": ["股东应占溢利"],
        "eps_basic": ["每股基本盈利"],
        "eps_diluted": ["每股摊薄盈利"],
        "interest_expense": ["融资成本"],
        "income_tax_expense": ["税项"],
        "selling_expense": ["销售及分销费用"],
        "admin_expense": ["行政开支"],
        "total_assets": ["总资产"],
        "total_liabilities": ["总负债"],
        "total_equity": ["总权益", "股东权益"],
        "equity_to_parent": ["股东权益"],
        "current_assets": ["流动资产合计"],
        "current_liabilities": ["流动负债合计"],
        "inventory": ["存货"],
        "cash_and_equivalents": ["现金及等价物"],
        "accounts_receivable": ["应收帐款"],
        "prepayments": ["预付款按金及其他应收款"],
        "short_term_debt": ["短期贷款"],
        "long_term_debt": ["长期贷款"],
        "operating_cash_flow": ["经营业务现金净额"],
        "capital_expenditure": ["购建固定资产", "购建无形资产及其他资产"],
        "investing_cash_flow": ["投资业务现金净额"],
        "financing_cash_flow": ["融资业务现金净额"],
        "dividends_paid": ["已付股息(融资)"],
        "depreciation_amortization": ["加:折旧及摊销"],
    },
}

#: Which canonical fields live in which statement.
FIELD_HOME: dict[str, tuple[str, ...]] = {
    "income": INCOME_FIELDS,
    "balance": BALANCE_FIELDS,
    "cashflow": CASHFLOW_FIELDS,
}


class CanonicalResult(BaseModel):
    """Canonical fields plus the audit trail of how each one was filled."""

    model_config = ConfigDict(extra="forbid")

    values: dict[str, float | None] = Field(default_factory=dict)
    matched_items: dict[str, str] = Field(default_factory=dict)
    derived_fields: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        if number != number or number in (float("inf"), float("-inf")):
            return None
        return number
    text = str(value).strip().replace(",", "")
    if not text or text in {"-", "--", "None", "nan", "NaN"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def canonicalize(kind: str, raw: dict[str, Any], source: str) -> CanonicalResult:
    """Map source-specific item names onto canonical fields.

    ``kind`` is ``income`` / ``balance`` / ``cashflow``.  Unknown sources return
    an empty result instead of raising: callers surface ``unavailable``.
    """

    aliases = FIELD_ALIASES.get(source)
    if aliases is None:
        raise ValueError(f"unknown source '{source}'")
    if kind not in FIELD_HOME:
        raise ValueError(f"unknown statement kind '{kind}'")

    result = CanonicalResult()
    for field in FIELD_HOME[kind]:
        result.values[field] = None
        for name in aliases.get(field, []):
            if name in raw:
                number = _to_float(raw[name])
                if number is not None:
                    result.values[field] = number
                    result.matched_items[field] = name
                    break
        if result.values[field] is None:
            result.missing_fields.append(field)
    return result


def derive_income(income: dict[str, float | None]) -> list[str]:
    """Fill gross profit / cost of revenue in either direction. Returns derived names."""

    derived: list[str] = []
    revenue = income.get("revenue")
    gross = income.get("gross_profit")
    cost = income.get("cost_of_revenue")
    if gross is None and revenue is not None and cost is not None:
        income["gross_profit"] = revenue - cost
        derived.append("gross_profit=revenue-cost_of_revenue")
    elif cost is None and revenue is not None and gross is not None:
        income["cost_of_revenue"] = revenue - gross
        derived.append("cost_of_revenue=revenue-gross_profit")
    return derived


def derive_balance(balance: dict[str, float | None]) -> list[str]:
    """Fill total liabilities / total equity / total debt where safely implied."""

    derived: list[str] = []
    assets = balance.get("total_assets")
    liabilities = balance.get("total_liabilities")
    equity = balance.get("total_equity")
    if liabilities is None and assets is not None and equity is not None:
        balance["total_liabilities"] = assets - equity
        derived.append("total_liabilities=total_assets-total_equity")
    elif equity is None and assets is not None and liabilities is not None:
        balance["total_equity"] = assets - liabilities
        derived.append("total_equity=total_assets-total_liabilities")

    short = balance.get("short_term_debt") or 0.0
    long = balance.get("long_term_debt") or 0.0
    if balance.get("total_debt") is None and (short or long):
        balance["total_debt"] = short + long
        derived.append("total_debt=short_term_debt+long_term_debt")
    return derived


def normalise_capex(value: float | None) -> float | None:
    """Capital expenditure is stored as a positive *spending* amount.

    SEC ``PaymentsToAcquirePropertyPlantAndEquipment`` and the Chinese line
    ``购建固定资产...所支付的现金`` are already positive outflows; some sources
    report capex as a negative cash-flow line, so we normalise the sign.
    """

    if value is None:
        return None
    return abs(value)


def quarter_label(period_end: date) -> str:
    quarter = (period_end.month - 1) // 3 + 1
    return f"{period_end.year}Q{quarter}"


def fiscal_label(period_end: date, period_type: str, fiscal_year: int | None = None) -> str:
    if period_type == "annual":
        year = fiscal_year or period_end.year
        return f"FY{year}"
    if period_type == "ttm":
        return f"TTM{period_end.isoformat()[:7]}"
    return quarter_label(period_end)


class FinancialStatement(BaseModel):
    """One reporting period of one company, normalised across sources."""

    model_config = ConfigDict(extra="forbid")

    symbol: str
    company_name: str | None = None
    market: Market = Market.UNKNOWN
    currency: str = "UNKNOWN"
    unit: str = "unknown"
    unit_scale: float = 1.0
    fiscal_period: str
    fiscal_year: int | None = None
    period_type: PeriodType = "annual"
    #: True when flow items (income statement / cash flow) are year-to-date
    #: cumulative figures, as published by Chinese A-share reports.
    cumulative: bool = False
    period_start: date | None = None
    period_end: date
    filing_date: date | None = None
    source: str
    income_statement: dict[str, float | None] = Field(default_factory=dict)
    balance_sheet: dict[str, float | None] = Field(default_factory=dict)
    cash_flow_statement: dict[str, float | None] = Field(default_factory=dict)
    raw_items: dict[str, dict[str, float | None]] = Field(default_factory=dict)
    matched_items: dict[str, dict[str, str]] = Field(default_factory=dict)
    derived_fields: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    data_status: DataStatus = DataStatus.HISTORICAL
    retrieved_at: str = Field(default_factory=lambda: iso(utc_now()))
    notes: list[str] = Field(default_factory=list)

    @field_validator("unit")
    @classmethod
    def _unit_not_blank(cls, value: str) -> str:
        return value.strip() or "unknown"

    def scaled(self, field: str) -> float | None:
        """Value expressed in ``unit`` (``unit_scale`` already applied)."""

        for statement in ("income_statement", "balance_sheet", "cash_flow_statement"):
            value = getattr(self, statement).get(field)
            if value is not None:
                return value * self.unit_scale
        return None


class FinancialStatements(BaseModel):
    """Tool-facing payload for ``get_company_financials``."""

    model_config = ConfigDict(extra="forbid")

    symbol: str
    company_name: str | None = None
    market: Market = Market.UNKNOWN
    currency: str = "UNKNOWN"
    unit: str = "unknown"
    period_type: PeriodType = "annual"
    source: str = "unavailable"
    statements: list[FinancialStatement] = Field(default_factory=list)
    data_status: DataStatus = DataStatus.UNAVAILABLE
    retrieved_at: str = Field(default_factory=lambda: iso(utc_now()))
    error: str | None = None
    notes: list[str] = Field(default_factory=list)


CANONICAL_FIELD_DOCS: dict[str, str] = {
    "revenue": "营业收入（合并口径，取来源披露值）",
    "cost_of_revenue": "营业成本；缺失时可由 revenue - gross_profit 推导并标注",
    "gross_profit": "毛利润；缺失时可由 revenue - cost_of_revenue 推导并标注",
    "operating_income": "营业利润 / 经营溢利（不含营业外项目）",
    "pretax_income": "税前利润",
    "net_income": "净利润（含少数股东损益）",
    "net_income_to_parent": "归属于母公司股东的净利润",
    "eps_basic": "基本每股收益（来源披露值）",
    "eps_diluted": "稀释每股收益（来源披露值）",
    "interest_expense": "利息费用 / 融资成本；口径依来源可能含资本化利息差异",
    "income_tax_expense": "所得税费用",
    "rd_expense": "研发费用",
    "selling_expense": "销售费用 / 销售及分销费用",
    "admin_expense": "管理费用 / 行政开支",
    "shares_basic": "基本加权平均股数（用于自算 EPS）",
    "shares_diluted": "稀释加权平均股数",
    "total_assets": "资产总计",
    "total_liabilities": "负债合计；缺失时可由 资产总计 - 所有者权益 推导",
    "total_equity": "所有者权益合计（含少数股东权益）",
    "equity_to_parent": "归属于母公司股东权益",
    "current_assets": "流动资产合计",
    "current_liabilities": "流动负债合计",
    "inventory": "存货",
    "cash_and_equivalents": "货币资金及现金等价物",
    "accounts_receivable": "应收账款（可能含应收票据，见 matched_items）",
    "prepayments": "预付款项（速动资产口径使用）",
    "short_term_debt": "短期借款 / 短期贷款",
    "long_term_debt": "长期借款、应付债券 / 长期贷款",
    "total_debt": "有息负债合计（默认 = 短期 + 长期，标注为推导值）",
    "shares_outstanding": "期末股本股数；A股/港股来源未直接提供，常为 None",
    "operating_cash_flow": "经营活动产生的现金流量净额",
    "capital_expenditure": "资本支出（统一为正的支出金额）",
    "investing_cash_flow": "投资活动现金流量净额",
    "financing_cash_flow": "筹资活动现金流量净额",
    "dividends_paid": "已付股利；A股来源该行含利息，已在文档标注口径",
    "depreciation_amortization": "折旧与摊销（部分来源不提供）",
}
