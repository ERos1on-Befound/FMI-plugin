"""A small, safe and extensible financial formula engine.

Design goals (see the Phase-2 task brief):

* formulas live in an explicit **registry** – adding one means decorating a
  function, never editing a dispatcher;
* every formula documents its inputs, its expression, its unit, the conditions
  under which it applies and how missing data is handled;
* user supplied expressions are evaluated by a whitelisted AST walker – never by
  ``eval``/``exec`` – and cannot reach Python objects, imports or the OS;
* missing data is reported as ``insufficient_data`` and a formula that does not
  apply is reported as ``not_applicable``; a missing value is never turned into
  ``0``.
"""

from __future__ import annotations

import ast
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from ..models.analysis_result import CalculationStatus, FormulaResult

FORMULA_VERSION = "fmi-formulas/1.0"


class FormulaError(ValueError):
    """Raised for invalid formula usage (unknown name, unsafe expression, ...)."""


@dataclass(frozen=True)
class FormulaInput:
    """One documented input of a formula."""

    name: str
    label: str
    unit: str = ""
    required: bool = True
    description: str = ""


@dataclass(frozen=True)
class FormulaOutcome:
    """Result of a single formula evaluation."""

    value: float | None = None
    status: CalculationStatus = "ok"
    explanation: str = ""
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class FormulaSpec:
    """Everything the server needs to advertise and run one formula."""

    name: str
    label: str
    category: str
    description: str
    expression: str
    inputs: tuple[FormulaInput, ...]
    output_unit: str
    func: Callable[[Mapping[str, float | None]], FormulaOutcome]
    notes: tuple[str, ...] = ()
    applicability: str = ""

    def input_names(self) -> tuple[str, ...]:
        return tuple(item.name for item in self.inputs)

    def required_names(self) -> tuple[str, ...]:
        return tuple(item.name for item in self.inputs if item.required)


FORMULAS: dict[str, FormulaSpec] = {}

#: Widely used short spellings accepted next to the canonical registry names.
FORMULA_ALIASES: dict[str, str] = {
    "pe": "pe_ratio",
    "pb": "pb_ratio",
    "ps": "ps_ratio",
    "roe": "return_on_equity",
    "roa": "return_on_assets",
    "fcf": "free_cash_flow",
    "cfo_to_ni": "cfo_to_net_income",
    "free_cash_flow_ratio": "cfo_to_net_income",
}

#: Names that route to the safe expression engine instead of a registered formula.
_CUSTOM_NAMES = frozenset({"custom_expression", "custom", "expression", "custom_formula"})


def resolve_name(name: str) -> str:
    """Map user input (with aliases) to a canonical registry name."""
    normalized = normalize_name(name)
    return FORMULA_ALIASES.get(normalized, normalized)


def normalize_name(name: str) -> str:
    """Normalise a user supplied formula/metric name (``Gross Margin`` → ``gross_margin``)."""
    cleaned = (name or "").strip().lower()
    for char in (" ", "-", ".", "/"):
        cleaned = cleaned.replace(char, "_")
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    return cleaned.strip("_")


def register(spec: FormulaSpec) -> FormulaSpec:
    if spec.name in FORMULAS:
        raise FormulaError(f"公式 {spec.name} 已经注册过")
    FORMULAS[spec.name] = spec
    return spec


def _decorator(**kwargs: Any) -> Callable[[Callable[..., FormulaOutcome]], Callable[..., FormulaOutcome]]:
    """Register a function as a formula using keyword metadata."""

    inputs = tuple(kwargs.pop("inputs"))

    def wrapper(func: Callable[[Mapping[str, float | None]], FormulaOutcome]) -> Callable[[Mapping[str, float | None]], FormulaOutcome]:
        spec = FormulaSpec(func=func, inputs=inputs, **kwargs)
        register(spec)
        return func

    return wrapper


# ---------------------------------------------------------------------------
# formatting helpers
# ---------------------------------------------------------------------------
def format_value(value: float | None, unit: str) -> str:
    """Human readable rendering used in the ``display`` field of every result."""
    if value is None:
        return "不可计算"
    if unit == "%":
        return f"{value:,.2f}%"
    if unit == "x":
        return f"{value:,.2f}x"
    if unit in {"USD/share", "CNY/share", "HKD/share"}:
        return f"{value:,.2f} {unit}"
    if unit in {"USD", "CNY", "HKD"}:
        return f"{value:,.0f} {unit}"
    if unit == "shares":
        return f"{value:,.0f} 股"
    if unit == "years":
        return f"{value:,.2f} 年"
    if unit == "ratio":
        return f"{value:,.4f}"
    return f"{value:,.4f}".rstrip("0").rstrip(".")


def _round(value: float | None, digits: int = 6) -> float | None:
    if value is None:
        return None
    if not math.isfinite(value):
        return None
    return round(value, digits)


def _missing(names: list[str], inputs: Mapping[str, float | None]) -> list[str]:
    return [name for name in names if inputs.get(name) is None]


def _insufficient(missing: list[str], what: str = "") -> FormulaOutcome:
    detail = "、".join(missing)
    prefix = f"缺少 {detail}，无法计算{what}。" if what else f"缺少 {detail}，无法计算。"
    return FormulaOutcome(status="insufficient_data", explanation=prefix)


def _ratio_outcome(
    numerator: float,
    denominator: float,
    *,
    scale: float = 1.0,
    zero_reason: str,
    note: str = "",
) -> FormulaOutcome:
    if denominator == 0:
        return FormulaOutcome(status="not_applicable", explanation=zero_reason)
    return FormulaOutcome(value=_round(numerator / denominator * scale), explanation=note)


def _simple_ratio(
    num_key: str,
    den_key: str,
    *,
    scale: float = 1.0,
    zero_reason: str,
    note: str = "",
    missing_what: str = "",
) -> Callable[[Mapping[str, float | None]], FormulaOutcome]:
    def _func(inputs: Mapping[str, float | None]) -> FormulaOutcome:
        gaps = _missing([num_key, den_key], inputs)
        if gaps:
            return _insufficient(gaps, missing_what)
        numerator = inputs[num_key]
        denominator = inputs[den_key]
        assert numerator is not None and denominator is not None
        return _ratio_outcome(numerator, denominator, scale=scale, zero_reason=zero_reason, note=note)

    return _func


def _average(
    inputs: Mapping[str, float | None],
    end_key: str,
    begin_key: str,
    single_key: str,
) -> tuple[float | None, bool, list[str]]:
    """Resolve an average balance from (begin, end) or a directly supplied average.

    Returns ``(value, simplified, used_keys)`` where ``simplified`` is True when
    the caller supplied only one of the two period-end values.
    """
    single = inputs.get(single_key)
    if single is not None:
        return single, False, [single_key]
    end = inputs.get(end_key)
    begin = inputs.get(begin_key)
    if end is not None and begin is not None:
        return (begin + end) / 2.0, False, [begin_key, end_key]
    if end is not None:
        return end, True, [end_key]
    if begin is not None:
        return begin, True, [begin_key]
    return None, False, []


# ---------------------------------------------------------------------------
# basic mathematics
# ---------------------------------------------------------------------------
@_decorator(
    name="percentage",
    label="占比 / 百分比",
    category="基础数学",
    description="计算一个数值占另一个数值的百分比。",
    expression="value = part / whole × 100",
    inputs=(
        FormulaInput("part", "部分值"),
        FormulaInput("whole", "整体值", description="作为分母，不能为 0"),
    ),
    output_unit="%",
    applicability="整体值为 0 时百分比没有意义，返回 not_applicable。",
    notes=("分母为负时会得到方向相反的百分比，解读时需要结合原始数据。",),
)
def _percentage(inputs: Mapping[str, float | None]) -> FormulaOutcome:
    gaps = _missing(["part", "whole"], inputs)
    if gaps:
        return _insufficient(gaps, "占比")
    part = inputs["part"]
    whole = inputs["whole"]
    assert part is not None and whole is not None
    return _ratio_outcome(part, whole, scale=100.0, zero_reason="整体值为 0，百分比没有意义。")


@_decorator(
    name="percentage_change",
    label="变化百分比",
    category="基础数学",
    description="任意两期数值之间的相对变化，分母取上期绝对值。",
    expression="value = (current - previous) / |previous| × 100",
    inputs=(
        FormulaInput("current", "本期值"),
        FormulaInput("previous", "上期值"),
    ),
    output_unit="%",
    applicability="上期值为 0 时变化率无定义。",
    notes=("上期为负时用绝对值做分母，得到的是相对变化方向，解读时必须说明基数符号。",),
)
def _percentage_change(inputs: Mapping[str, float | None]) -> FormulaOutcome:
    gaps = _missing(["current", "previous"], inputs)
    if gaps:
        return _insufficient(gaps, "变化百分比")
    current = inputs["current"]
    previous = inputs["previous"]
    assert current is not None and previous is not None
    if previous == 0:
        return FormulaOutcome(status="not_applicable", explanation="上期值为 0，变化百分比无定义。")
    note = ""
    if previous < 0:
        note = "上期值为负数，变化百分比以绝对值作分母，方向解读需结合原始金额。"
    return FormulaOutcome(value=_round((current - previous) / abs(previous) * 100.0), explanation=note)


@_decorator(
    name="yoy_growth",
    label="同比增长率",
    category="成长",
    description="同一报告期（如去年同期）之间的增长率。",
    expression="value = (current - previous) / |previous| × 100",
    inputs=(
        FormulaInput("current", "本期值"),
        FormulaInput("previous", "上年同期值"),
    ),
    output_unit="%",
    applicability="比较的两个期间必须是同一季节性报告期（如均为 FY 或均为 Q1）。",
    notes=(
        "上期值为 0 时不可计算；上期为负时只反映相对变化，不能据此判断“转好”或“恶化”。",
        "不得用单季数据直接与全年数据比较。",
    ),
)
def _yoy_growth(inputs: Mapping[str, float | None]) -> FormulaOutcome:
    return _percentage_change(inputs)


@_decorator(
    name="qoq_growth",
    label="环比增长率",
    category="成长",
    description="相邻两个报告期之间的增长率。",
    expression="value = (current - previous) / |previous| × 100",
    inputs=(
        FormulaInput("current", "本期值"),
        FormulaInput("previous", "上一期值"),
    ),
    output_unit="%",
    applicability="相邻报告期长度必须一致；累计口径的季度数据需先差分化。",
    notes=("季节性行业环比波动大，应同时参考同比。",),
)
def _qoq_growth(inputs: Mapping[str, float | None]) -> FormulaOutcome:
    return _percentage_change(inputs)


@_decorator(
    name="cagr",
    label="复合年均增长率 (CAGR)",
    category="成长",
    description="按复利口径计算的年均增长率。",
    expression="CAGR = (ending / beginning) ** (1 / years) - 1",
    inputs=(
        FormulaInput("beginning", "起始值", description="必须为正数"),
        FormulaInput("ending", "结束值", description="必须为正数"),
        FormulaInput("years", "间隔年数"),
    ),
    output_unit="%",
    applicability="起止值必须为正；间隔年数必须大于 0。负值或跨零序列不能计算 CAGR。",
    notes=("years 应等于起止年份之差，而不是报告期个数；多期内插值不适用本公式。",),
)
def _cagr(inputs: Mapping[str, float | None]) -> FormulaOutcome:
    gaps = _missing(["beginning", "ending", "years"], inputs)
    if gaps:
        return _insufficient(gaps, "CAGR")
    beginning = inputs["beginning"]
    ending = inputs["ending"]
    years = inputs["years"]
    assert beginning is not None and ending is not None and years is not None
    if years <= 0:
        return FormulaOutcome(status="not_applicable", explanation="间隔年数必须大于 0。")
    if beginning <= 0 or ending <= 0:
        return FormulaOutcome(
            status="not_applicable",
            explanation="起止值存在 0 或负数，复利年均增长率不适用（负基数开方没有实际意义）。",
        )
    value = (ending / beginning) ** (1.0 / years) - 1.0
    return FormulaOutcome(value=_round(value * 100.0), explanation=f"以 {years:g} 年复利口径计算。")


@_decorator(
    name="compound_interest",
    label="复利终值",
    category="基础数学",
    description="按年复利计算终值。",
    expression="FV = PV × (1 + r) ** n",
    inputs=(
        FormulaInput("present_value", "现值 PV"),
        FormulaInput("annual_rate", "年利率 r", description="小数形式，例如 0.05 表示 5%"),
        FormulaInput("years", "期数 n（年）"),
    ),
    output_unit="ratio",
    applicability="利率可按任何正负小数输入；期数可为小数。",
    notes=("输出与 present_value 同单位（未做币种换算）。",),
)
def _compound_interest(inputs: Mapping[str, float | None]) -> FormulaOutcome:
    gaps = _missing(["present_value", "annual_rate", "years"], inputs)
    if gaps:
        return _insufficient(gaps, "复利终值")
    pv = inputs["present_value"]
    rate = inputs["annual_rate"]
    years = inputs["years"]
    assert pv is not None and rate is not None and years is not None
    if 1.0 + rate <= 0:
        return FormulaOutcome(status="not_applicable", explanation="(1 + r) 不为正，复利公式不适用。")
    return FormulaOutcome(value=_round(pv * (1.0 + rate) ** years))


@_decorator(
    name="annualized_return",
    label="年化收益率",
    category="基础数学",
    description="把区间收益率折算成年化收益率。",
    expression="annualized = (1 + total_return) ** (1 / years) - 1",
    inputs=(
        FormulaInput("total_return", "区间收益率", description="小数形式，例如 0.21 表示 21%"),
        FormulaInput("years", "区间年数"),
    ),
    output_unit="%",
    applicability="区间年数必须大于 0；区间收益率必须大于 -100%。",
    notes=("年化只是把区间收益按复利摊平，不代表未来收益。",),
)
def _annualized_return(inputs: Mapping[str, float | None]) -> FormulaOutcome:
    gaps = _missing(["total_return", "years"], inputs)
    if gaps:
        return _insufficient(gaps, "年化收益率")
    total = inputs["total_return"]
    years = inputs["years"]
    assert total is not None and years is not None
    if years <= 0:
        return FormulaOutcome(status="not_applicable", explanation="区间年数必须大于 0。")
    if total <= -1.0:
        return FormulaOutcome(status="not_applicable", explanation="区间收益率小于等于 -100%，无法开方年化。")
    value = (1.0 + total) ** (1.0 / years) - 1.0
    return FormulaOutcome(value=_round(value * 100.0))


# ---------------------------------------------------------------------------
# profitability
# ---------------------------------------------------------------------------
@_decorator(
    name="gross_margin",
    label="毛利率",
    category="盈利能力",
    description="毛利润占营业收入的比重。",
    expression="gross_margin = (revenue - cost_of_revenue) / revenue",
    inputs=(
        FormulaInput("revenue", "营业收入"),
        FormulaInput("cost_of_revenue", "营业成本"),
    ),
    output_unit="%",
    applicability="营业收入必须为正；金融类公司通常没有可直接比较的营业成本。",
    notes=("不同会计准则下营业成本口径不同（是否含折旧、运费），跨公司比较前需核对口径。",),
)
def _gross_margin(inputs: Mapping[str, float | None]) -> FormulaOutcome:
    gaps = _missing(["revenue", "cost_of_revenue"], inputs)
    if gaps:
        return _insufficient(gaps, "毛利率")
    revenue = inputs["revenue"]
    cost = inputs["cost_of_revenue"]
    assert revenue is not None and cost is not None
    return _ratio_outcome(revenue - cost, revenue, scale=100.0, zero_reason="营业收入为 0，毛利率无定义。")


@_decorator(
    name="operating_margin",
    label="营业利润率",
    category="盈利能力",
    description="营业利润占营业收入的比重。",
    expression="operating_margin = operating_income / revenue",
    inputs=(
        FormulaInput("operating_income", "营业利润"),
        FormulaInput("revenue", "营业收入"),
    ),
    output_unit="%",
    applicability="营业收入必须为正。",
    notes=("营业利润是否包含投资收益、汇兑损益等取决于报表口径，需注明来源。",),
)
def _operating_margin(inputs: Mapping[str, float | None]) -> FormulaOutcome:
    gaps = _missing(["operating_income", "revenue"], inputs)
    if gaps:
        return _insufficient(gaps, "营业利润率")
    operating = inputs["operating_income"]
    revenue = inputs["revenue"]
    assert operating is not None and revenue is not None
    return _ratio_outcome(operating, revenue, scale=100.0, zero_reason="营业收入为 0，营业利润率无定义。")


@_decorator(
    name="net_margin",
    label="净利率",
    category="盈利能力",
    description="净利润占营业收入的比重。",
    expression="net_margin = net_income / revenue",
    inputs=(
        FormulaInput("net_income", "净利润"),
        FormulaInput("revenue", "营业收入"),
    ),
    output_unit="%",
    applicability="营业收入必须为正；净利润可为负（此时为负净利率）。",
    notes=("净利润含非经常性损益时，净利率会受一次性项目影响。",),
)
def _net_margin(inputs: Mapping[str, float | None]) -> FormulaOutcome:
    gaps = _missing(["net_income", "revenue"], inputs)
    if gaps:
        return _insufficient(gaps, "净利率")
    net = inputs["net_income"]
    revenue = inputs["revenue"]
    assert net is not None and revenue is not None
    return _ratio_outcome(net, revenue, scale=100.0, zero_reason="营业收入为 0，净利率无定义。")


def _equity_based(
    inputs: Mapping[str, float | None],
    *,
    numerator_key: str,
    balance_name: str,
    label: str,
) -> FormulaOutcome:
    gaps = _missing([numerator_key], inputs)
    if gaps:
        return _insufficient(gaps, label)
    numerator = inputs[numerator_key]
    assert numerator is not None
    average, simplified, used = _average(inputs, f"{balance_name}_end", f"{balance_name}_begin", f"{balance_name}_average")
    if average is None:
        return _insufficient([f"{balance_name}_average 或 {balance_name}_begin/{balance_name}_end"], label)
    if average == 0:
        return FormulaOutcome(status="not_applicable", explanation=f"{balance_name} 平均余额为 0，{label} 无定义。")
    note = ""
    if simplified:
        note = f"仅提供期末{balance_name}（{used[0]}），按“简化口径”以期末值替代平均余额。"
    if average < 0:
        note = (note + " " if note else "") + f"{balance_name} 为负（资不抵债），比值的解读需格外谨慎。"
    return FormulaOutcome(value=_round(numerator / average * 100.0), explanation=note)


@_decorator(
    name="return_on_equity",
    label="净资产收益率 (ROE)",
    category="盈利能力",
    description="净利润相对平均股东权益的回报率。",
    expression="ROE = net_income / average(equity_begin, equity_end)",
    inputs=(
        FormulaInput("net_income", "净利润"),
        FormulaInput("equity_average", "平均股东权益", required=False, description="直接给平均值时优先使用"),
        FormulaInput("equity_begin", "期初股东权益", required=False),
        FormulaInput("equity_end", "期末股东权益", required=False),
    ),
    output_unit="%",
    applicability="股东权益平均余额应为正；缺少期初值时可用期末值替代但需标注简化口径。",
    notes=("股东权益为负（资不抵债）时 ROE 失去可比性。",),
)
def _roe(inputs: Mapping[str, float | None]) -> FormulaOutcome:
    return _equity_based(inputs, numerator_key="net_income", balance_name="equity", label="ROE")


@_decorator(
    name="return_on_assets",
    label="总资产收益率 (ROA)",
    category="盈利能力",
    description="净利润相对平均总资产的回报率。",
    expression="ROA = net_income / average(assets_begin, assets_end)",
    inputs=(
        FormulaInput("net_income", "净利润"),
        FormulaInput("assets_average", "平均总资产", required=False),
        FormulaInput("assets_begin", "期初总资产", required=False),
        FormulaInput("assets_end", "期末总资产", required=False),
    ),
    output_unit="%",
    applicability="总资产平均余额应为正。",
    notes=("总资产含负债融资形成的资产，重资产行业 ROA 天然偏低。",),
)
def _roa(inputs: Mapping[str, float | None]) -> FormulaOutcome:
    return _equity_based(inputs, numerator_key="net_income", balance_name="assets", label="ROA")


@_decorator(
    name="eps_growth",
    label="每股收益增长率",
    category="成长",
    description="每股收益的同比增长率。",
    expression="eps_growth = (eps_current - eps_previous) / |eps_previous| × 100",
    inputs=(
        FormulaInput("eps_current", "本期每股收益"),
        FormulaInput("eps_previous", "上期每股收益"),
    ),
    output_unit="%",
    applicability="上期每股收益为 0 时不可计算；由负转正的增长百分比没有实际含义。",
    notes=("EPS 口径需一致（基本/稀释、是否扣非）。",),
)
def _eps_growth(inputs: Mapping[str, float | None]) -> FormulaOutcome:
    gaps = _missing(["eps_current", "eps_previous"], inputs)
    if gaps:
        return _insufficient(gaps, "每股收益增长率")
    current = inputs["eps_current"]
    previous = inputs["eps_previous"]
    assert current is not None and previous is not None
    if previous == 0:
        return FormulaOutcome(status="not_applicable", explanation="上期每股收益为 0，增长率无定义。")
    note = ""
    if previous < 0 < current:
        note = "由亏损转为盈利，百分比增长不具可比性，应直接比较绝对金额。"
    return FormulaOutcome(value=_round((current - previous) / abs(previous) * 100.0), explanation=note)


# ---------------------------------------------------------------------------
# solvency
# ---------------------------------------------------------------------------
@_decorator(
    name="debt_ratio",
    label="资产负债率",
    category="偿债能力",
    description="总负债占总资产的比重。",
    expression="debt_ratio = total_liabilities / total_assets × 100",
    inputs=(
        FormulaInput("total_liabilities", "总负债"),
        FormulaInput("total_assets", "总资产"),
    ),
    output_unit="%",
    applicability="总资产必须为正；金融机构的负债率天然很高，不能直接与制造业比较。",
    notes=("负债率高低需结合行业属性与负债结构判断，不能单凭数值下结论。",),
)
def _debt_ratio(inputs: Mapping[str, float | None]) -> FormulaOutcome:
    gaps = _missing(["total_liabilities", "total_assets"], inputs)
    if gaps:
        return _insufficient(gaps, "资产负债率")
    liabilities = inputs["total_liabilities"]
    assets = inputs["total_assets"]
    assert liabilities is not None and assets is not None
    return _ratio_outcome(liabilities, assets, scale=100.0, zero_reason="总资产为 0，资产负债率无定义。")


@_decorator(
    name="current_ratio",
    label="流动比率",
    category="偿债能力",
    description="流动资产覆盖流动负债的倍数。",
    expression="current_ratio = current_assets / current_liabilities",
    inputs=(
        FormulaInput("current_assets", "流动资产"),
        FormulaInput("current_liabilities", "流动负债"),
    ),
    output_unit="x",
    applicability="流动负债必须大于 0；银行等金融机构通常不适用流动比率。",
    notes=("比率低于 1 表示流动资产不足以覆盖短期负债，但不必然意味着违约风险。",),
)
def _current_ratio(inputs: Mapping[str, float | None]) -> FormulaOutcome:
    gaps = _missing(["current_assets", "current_liabilities"], inputs)
    if gaps:
        return _insufficient(gaps, "流动比率")
    assets = inputs["current_assets"]
    liabilities = inputs["current_liabilities"]
    assert assets is not None and liabilities is not None
    return _ratio_outcome(assets, liabilities, zero_reason="流动负债为 0，流动比率无定义。")


@_decorator(
    name="quick_ratio",
    label="速动比率",
    category="偿债能力",
    description="剔除存货（与预付款）后，流动资产覆盖流动负债的倍数。",
    expression="quick_ratio = (current_assets - inventory - prepayments) / current_liabilities",
    inputs=(
        FormulaInput("current_assets", "流动资产"),
        FormulaInput("inventory", "存货"),
        FormulaInput("current_liabilities", "流动负债"),
        FormulaInput("prepayments", "预付款项", required=False),
    ),
    output_unit="x",
    applicability="缺预付款数据时按 (流动资产-存货)/流动负债 计算并标注简化口径。",
    notes=("存货变现能力差的行业，速动比率更能反映短期偿付能力。",),
)
def _quick_ratio(inputs: Mapping[str, float | None]) -> FormulaOutcome:
    gaps = _missing(["current_assets", "inventory", "current_liabilities"], inputs)
    if gaps:
        return _insufficient(gaps, "速动比率")
    assets = inputs["current_assets"]
    inventory = inputs["inventory"]
    liabilities = inputs["current_liabilities"]
    prepayments = inputs.get("prepayments")
    assert assets is not None and inventory is not None and liabilities is not None
    note = ""
    if prepayments is None:
        note = "缺少预付款项，按 (流动资产 - 存货) / 流动负债 的简化口径计算。"
        quick_assets = assets - inventory
    else:
        quick_assets = assets - inventory - prepayments
    return _ratio_outcome(quick_assets, liabilities, zero_reason="流动负债为 0，速动比率无定义。", note=note)


@_decorator(
    name="interest_coverage",
    label="利息保障倍数",
    category="偿债能力",
    description="息税前利润覆盖利息费用的倍数。",
    expression="interest_coverage = EBIT / interest_expense",
    inputs=(
        FormulaInput("ebit", "息税前利润 (EBIT)"),
        FormulaInput("interest_expense", "利息费用"),
    ),
    output_unit="x",
    applicability="利息费用必须为正；利息资本化或计入其他科目时口径不一致，应返回不可计算。",
    notes=("利息费用为 0 或缺失时不能把比率当作“无穷大/极安全”。",),
)
def _interest_coverage(inputs: Mapping[str, float | None]) -> FormulaOutcome:
    gaps = _missing(["ebit", "interest_expense"], inputs)
    if gaps:
        return _insufficient(gaps, "利息保障倍数")
    ebit = inputs["ebit"]
    interest = inputs["interest_expense"]
    assert ebit is not None and interest is not None
    if interest <= 0:
        return FormulaOutcome(
            status="not_applicable",
            explanation="利息费用为 0 或缺失（或为负的利息收入净额），利息保障倍数不适用。",
        )
    return FormulaOutcome(value=_round(ebit / interest))


# ---------------------------------------------------------------------------
# operating efficiency
# ---------------------------------------------------------------------------
@_decorator(
    name="asset_turnover",
    label="总资产周转率",
    category="运营效率",
    description="营业收入相对平均总资产的周转次数。",
    expression="asset_turnover = revenue / average(total_assets)",
    inputs=(
        FormulaInput("revenue", "营业收入"),
        FormulaInput("assets_average", "平均总资产", required=False),
        FormulaInput("assets_begin", "期初总资产", required=False),
        FormulaInput("assets_end", "期末总资产", required=False),
    ),
    output_unit="x",
    applicability="总资产平均余额必须为正；需注明对应的财务期间（年度或季度）。",
    notes=("季度周转率与年度周转率不可直接比较。",),
)
def _asset_turnover(inputs: Mapping[str, float | None]) -> FormulaOutcome:
    gaps = _missing(["revenue"], inputs)
    if gaps:
        return _insufficient(gaps, "总资产周转率")
    revenue = inputs["revenue"]
    assert revenue is not None
    average, simplified, used = _average(inputs, "assets_end", "assets_begin", "assets_average")
    if average is None:
        return _insufficient(["assets_average 或 assets_begin/assets_end"], "总资产周转率")
    note = "仅提供期末总资产，按简化口径计算。" if simplified else ""
    return _ratio_outcome(revenue, average, zero_reason="平均总资产为 0，周转率无定义。", note=note)


@_decorator(
    name="receivables_turnover",
    label="应收账款周转率",
    category="运营效率",
    description="营业收入相对平均应收账款的周转次数。",
    expression="receivables_turnover = revenue / average(accounts_receivable)",
    inputs=(
        FormulaInput("revenue", "营业收入"),
        FormulaInput("receivables_average", "平均应收账款", required=False),
        FormulaInput("receivables_begin", "期初应收账款", required=False),
        FormulaInput("receivables_end", "期末应收账款", required=False),
    ),
    output_unit="x",
    applicability="平均应收账款必须为正；收入含大量现金销售时该指标意义有限。",
    notes=("应收账款应包含应收票据，口径需与来源报表一致。",),
)
def _receivables_turnover(inputs: Mapping[str, float | None]) -> FormulaOutcome:
    gaps = _missing(["revenue"], inputs)
    if gaps:
        return _insufficient(gaps, "应收账款周转率")
    revenue = inputs["revenue"]
    assert revenue is not None
    average, simplified, _ = _average(inputs, "receivables_end", "receivables_begin", "receivables_average")
    if average is None:
        return _insufficient(["receivables_average 或 receivables_begin/receivables_end"], "应收账款周转率")
    note = "仅提供期末应收账款，按简化口径计算。" if simplified else ""
    return _ratio_outcome(revenue, average, zero_reason="平均应收账款为 0，周转率无定义。", note=note)


@_decorator(
    name="inventory_turnover",
    label="存货周转率",
    category="运营效率",
    description="营业成本相对平均存货的周转次数。",
    expression="inventory_turnover = cost_of_revenue / average(inventory)",
    inputs=(
        FormulaInput("cost_of_revenue", "营业成本"),
        FormulaInput("inventory_average", "平均存货", required=False),
        FormulaInput("inventory_begin", "期初存货", required=False),
        FormulaInput("inventory_end", "期末存货", required=False),
    ),
    output_unit="x",
    applicability="平均存货必须为正；服务业与金融企业通常没有存货。",
    notes=("用营业成本而非收入做分子，避免利润被存货计价方式放大。",),
)
def _inventory_turnover(inputs: Mapping[str, float | None]) -> FormulaOutcome:
    gaps = _missing(["cost_of_revenue"], inputs)
    if gaps:
        return _insufficient(gaps, "存货周转率")
    cost = inputs["cost_of_revenue"]
    assert cost is not None
    average, simplified, _ = _average(inputs, "inventory_end", "inventory_begin", "inventory_average")
    if average is None:
        return _insufficient(["inventory_average 或 inventory_begin/inventory_end"], "存货周转率")
    note = "仅提供期末存货，按简化口径计算。" if simplified else ""
    return _ratio_outcome(cost, average, zero_reason="平均存货为 0，周转率无定义。", note=note)


# ---------------------------------------------------------------------------
# cash flow
# ---------------------------------------------------------------------------
@_decorator(
    name="free_cash_flow",
    label="自由现金流 (FCF)",
    category="现金流",
    description="经营活动现金流减去资本性支出。",
    expression="FCF = operating_cash_flow - |capital_expenditure|",
    inputs=(
        FormulaInput("operating_cash_flow", "经营活动现金流量净额"),
        FormulaInput("capital_expenditure", "资本性支出", description="正负号均可，按支出绝对值处理"),
    ),
    output_unit="",
    applicability="需要同时有经营活动现金流与资本性支出；资本性支出的符号口径需先统一。",
    notes=(
        "不同数据源可能把资本性支出记为正数或负数，本公式统一取绝对值。",
        "FCF 不是会计科目，不同机构的定义（是否含并购、是否含租赁）可能不同。",
    ),
)
def _free_cash_flow(inputs: Mapping[str, float | None]) -> FormulaOutcome:
    gaps = _missing(["operating_cash_flow", "capital_expenditure"], inputs)
    if gaps:
        return _insufficient(gaps, "自由现金流")
    ocf = inputs["operating_cash_flow"]
    capex = inputs["capital_expenditure"]
    assert ocf is not None and capex is not None
    note = "资本性支出已按绝对值处理（视为现金流出）。"
    if capex < 0:
        note += " 原始数据中该项为负数。"
    return FormulaOutcome(value=_round(ocf - abs(capex)), explanation=note)


@_decorator(
    name="cfo_to_net_income",
    label="经营现金流 / 净利润",
    category="现金流",
    description="经营活动现金流与净利润的比值，用于观察利润的现金含量。",
    expression="ratio = operating_cash_flow / net_income",
    inputs=(
        FormulaInput("operating_cash_flow", "经营活动现金流量净额"),
        FormulaInput("net_income", "净利润"),
    ),
    output_unit="x",
    applicability="净利润必须为正；净利润为负或为零时该比值不具盈利质量含义。",
    notes=(
        "净利润为负时比值没有意义，应直接比较经营现金流与净利润的绝对金额。",
        "比值显著高于 1 可能来自折旧摊销等非付现成本，也可能是营运资本变动，需结合明细。",
    ),
)
def _cfo_to_net_income(inputs: Mapping[str, float | None]) -> FormulaOutcome:
    gaps = _missing(["operating_cash_flow", "net_income"], inputs)
    if gaps:
        return _insufficient(gaps, "经营现金流/净利润")
    ocf = inputs["operating_cash_flow"]
    net = inputs["net_income"]
    assert ocf is not None and net is not None
    if net <= 0:
        return FormulaOutcome(
            status="not_applicable",
            explanation=(
                f"净利润为 {net:,.4f}（≤0），经营现金流/净利润 比值不具备盈利质量含义；"
                f"请直接比较 经营现金流 {ocf:,.4f} 与净利润 {net:,.4f}。"
            ),
        )
    return FormulaOutcome(value=_round(ocf / net))


# ---------------------------------------------------------------------------
# valuation
# ---------------------------------------------------------------------------
@_decorator(
    name="pe_ratio",
    label="市盈率 (PE)",
    category="估值",
    description="股价相对每股收益的倍数。",
    expression="PE = price / EPS",
    inputs=(
        FormulaInput("price", "股价"),
        FormulaInput("eps", "每股收益", description="需注明是 TTM、年度还是预测 EPS"),
    ),
    output_unit="x",
    applicability="EPS 必须为正；EPS ≤ 0 时 PE 不适用（负盈利）。",
    notes=("不同口径（TTM / 年度 / 预测）的 PE 不可混用；PE 只描述估值水平，不构成买卖建议。",),
)
def _pe_ratio(inputs: Mapping[str, float | None]) -> FormulaOutcome:
    gaps = _missing(["price", "eps"], inputs)
    if gaps:
        return _insufficient(gaps, "市盈率")
    price = inputs["price"]
    eps = inputs["eps"]
    assert price is not None and eps is not None
    if eps <= 0:
        return FormulaOutcome(
            status="not_applicable",
            explanation=f"每股收益为 {eps:,.4f}（≤0），PE 不适用：亏损公司没有可解释的正市盈率。",
        )
    return FormulaOutcome(value=_round(price / eps), explanation="EPS 口径需与股价时点匹配。")


@_decorator(
    name="pb_ratio",
    label="市净率 (PB)",
    category="估值",
    description="股价相对每股净资产的倍数。",
    expression="PB = price / book_value_per_share",
    inputs=(
        FormulaInput("price", "股价"),
        FormulaInput("book_value_per_share", "每股净资产"),
    ),
    output_unit="x",
    applicability="每股净资产必须为正；净资产为负时 PB 不适用。",
    notes=("轻资产公司的账面净资产与市场价值差异较大，PB 的可比性有限。",),
)
def _pb_ratio(inputs: Mapping[str, float | None]) -> FormulaOutcome:
    gaps = _missing(["price", "book_value_per_share"], inputs)
    if gaps:
        return _insufficient(gaps, "市净率")
    price = inputs["price"]
    bvps = inputs["book_value_per_share"]
    assert price is not None and bvps is not None
    if bvps <= 0:
        return FormulaOutcome(
            status="not_applicable",
            explanation=f"每股净资产为 {bvps:,.4f}（≤0），PB 不适用。",
        )
    return FormulaOutcome(value=_round(price / bvps))


@_decorator(
    name="ps_ratio",
    label="市销率 (PS)",
    category="估值",
    description="市值相对营业收入的倍数。",
    expression="PS = market_cap / revenue",
    inputs=(
        FormulaInput("market_cap", "总市值"),
        FormulaInput("revenue", "营业收入"),
    ),
    output_unit="x",
    applicability="营业收入必须为正，且与市值对应同一报告期与币种。",
    notes=("跨币种或跨报告期比较前需要换算；亏损公司也可计算 PS。",),
)
def _ps_ratio(inputs: Mapping[str, float | None]) -> FormulaOutcome:
    gaps = _missing(["market_cap", "revenue"], inputs)
    if gaps:
        return _insufficient(gaps, "市销率")
    cap = inputs["market_cap"]
    revenue = inputs["revenue"]
    assert cap is not None and revenue is not None
    if revenue <= 0:
        return FormulaOutcome(status="not_applicable", explanation="营业收入为 0 或为负，市销率不适用。")
    return FormulaOutcome(value=_round(cap / revenue))


@_decorator(
    name="dividend_yield",
    label="股息率",
    category="估值",
    description="每股年股息相对股价的百分比。",
    expression="dividend_yield = dividend_per_share / price × 100",
    inputs=(
        FormulaInput("dividend_per_share", "每股年股息"),
        FormulaInput("price", "股价"),
    ),
    output_unit="%",
    applicability="股价必须为正；已支付股息与预期股息需分别注明。",
    notes=("使用已支付股息时是历史股息率，使用预期股息时是预测股息率，两者不可混淆。",),
)
def _dividend_yield(inputs: Mapping[str, float | None]) -> FormulaOutcome:
    gaps = _missing(["dividend_per_share", "price"], inputs)
    if gaps:
        return _insufficient(gaps, "股息率")
    dps = inputs["dividend_per_share"]
    price = inputs["price"]
    assert dps is not None and price is not None
    if price <= 0:
        return FormulaOutcome(status="not_applicable", explanation="股价为 0 或为负，股息率无定义。")
    return FormulaOutcome(value=_round(dps / price * 100.0))


@_decorator(
    name="market_cap",
    label="总市值",
    category="估值",
    description="股价乘以总股本。",
    expression="market_cap = price × shares_outstanding",
    inputs=(
        FormulaInput("price", "股价"),
        FormulaInput("shares_outstanding", "总股本"),
    ),
    output_unit="",
    applicability="需要股价与股本，且两者时点应尽量接近。",
    notes=("不同类别股份（A/H/B）应分别计算或按总股本合并，需注明口径。",),
)
def _market_cap(inputs: Mapping[str, float | None]) -> FormulaOutcome:
    gaps = _missing(["price", "shares_outstanding"], inputs)
    if gaps:
        return _insufficient(gaps, "总市值")
    price = inputs["price"]
    shares = inputs["shares_outstanding"]
    assert price is not None and shares is not None
    if shares <= 0:
        return FormulaOutcome(status="not_applicable", explanation="总股本为 0 或为负，市值无定义。")
    return FormulaOutcome(value=_round(price * shares))


# ---------------------------------------------------------------------------
# safe expression evaluation
# ---------------------------------------------------------------------------
_ALLOWED_NODES: tuple[type[ast.AST], ...] = (
    ast.Expression,
    ast.BinOp,
    ast.UnaryOp,
    ast.Constant,
    ast.Name,
    ast.Load,
    ast.Call,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.Pow,
    ast.Mod,
    ast.USub,
    ast.UAdd,
)
_SAFE_FUNCTIONS: dict[str, Callable[..., float]] = {
    "sqrt": math.sqrt,
    "abs": abs,
    "min": min,
    "max": max,
    "round": round,
    "log": math.log,
    "ln": math.log,
    "log10": math.log10,
    "exp": math.exp,
}


def evaluate_expression(expression: str, variables: Mapping[str, Any]) -> float:
    """Evaluate a small arithmetic expression against named variables.

    Only ``+ - * / ** %``, parentheses, numeric literals, the variables passed
    in ``variables`` and a whitelist of maths functions are accepted. Anything
    else (attribute access, indexing, comprehensions, lambdas, imports, strings)
    raises :class:`FormulaError`. This is deliberately *not* ``eval``.
    """
    text = (expression or "").strip()
    if not text:
        raise FormulaError("表达式为空。")
    if len(text) > 500:
        raise FormulaError("表达式过长（超过 500 字符）。")
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as exc:  # pragma: no cover - message passthrough
        raise FormulaError(f"表达式语法错误：{exc.msg}") from exc

    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            raise FormulaError(f"表达式包含不允许的语法：{type(node).__name__}")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in _SAFE_FUNCTIONS:
                allowed = "、".join(sorted(_SAFE_FUNCTIONS))
                raise FormulaError(
                    f"表达式包含不允许的函数调用（{getattr(node.func, 'id', type(node.func).__name__)}）；"
                    f"只允许使用这些函数：{allowed}。"
                )
            if node.keywords:
                raise FormulaError("函数调用不支持关键字参数。")

    def _eval(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                raise FormulaError("表达式只能包含数字常量。")
            return float(node.value)
        if isinstance(node, ast.Name):
            if node.id not in variables:
                raise FormulaError(f"变量 {node.id} 没有提供数值。")
            value = variables[node.id]
            if value is None:
                raise FormulaError(f"变量 {node.id} 的值为空。")
            try:
                return float(value)
            except (TypeError, ValueError) as exc:
                raise FormulaError(f"变量 {node.id} 不是数值。") from exc
        if isinstance(node, ast.UnaryOp):
            operand = _eval(node.operand)
            if isinstance(node.op, ast.USub):
                return -operand
            if isinstance(node.op, ast.UAdd):
                return operand
            raise FormulaError("不支持的一元运算。")
        if isinstance(node, ast.BinOp):
            left = _eval(node.left)
            right = _eval(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                if right == 0:
                    raise FormulaError("表达式出现除以零。")
                return left / right
            if isinstance(node.op, ast.Pow):
                return float(left**right)
            if isinstance(node.op, ast.Mod):
                if right == 0:
                    raise FormulaError("表达式出现对零取模。")
                return left % right
            raise FormulaError("不支持的二元运算。")
        if isinstance(node, ast.Call):
            func_name = node.func.id  # type: ignore[attr-defined]
            args = [_eval(argument) for argument in node.args]
            try:
                return float(_SAFE_FUNCTIONS[func_name](*args))
            except (TypeError, ValueError) as exc:
                raise FormulaError(f"函数 {func_name} 的参数不合法：{exc}") from exc
        raise FormulaError(f"表达式包含不允许的语法：{type(node).__name__}")

    try:
        value = _eval(tree)
    except ZeroDivisionError as exc:  # pragma: no cover - defensive
        raise FormulaError("表达式出现除以零。") from exc
    if not math.isfinite(value):
        raise FormulaError("表达式计算结果不是有限数值。")
    return value


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------
def list_formulas() -> list[FormulaSpec]:
    return sorted(FORMULAS.values(), key=lambda spec: (spec.category, spec.name))


def _coerce_inputs(
    spec: FormulaSpec,
    inputs: Mapping[str, Any] | None,
) -> tuple[dict[str, float | None], list[str]]:
    """Coerce raw inputs to floats, collecting human readable problems."""
    problems: list[str] = []
    coerced: dict[str, float | None] = {}
    raw = dict(inputs or {})
    for item in spec.inputs:
        value = raw.get(item.name)
        if value is None or (isinstance(value, str) and not value.strip()):
            coerced[item.name] = None
            continue
        if isinstance(value, bool):
            problems.append(f"{item.name} 不是数值")
            coerced[item.name] = None
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            problems.append(f"{item.name}={value!r} 不是数值")
            coerced[item.name] = None
            continue
        if not math.isfinite(number):
            problems.append(f"{item.name}={value!r} 不是有限数值")
            coerced[item.name] = None
            continue
        coerced[item.name] = number
    unknown = [name for name in raw if name not in coerced]
    if unknown:
        problems.append("未识别的输入字段：" + "、".join(sorted(unknown)))
    return coerced, problems


def compute_formula(formula_name: str, inputs: Mapping[str, Any] | None = None) -> FormulaResult:
    """Run one registered formula and wrap the outcome in a :class:`FormulaResult`."""
    payload = dict(inputs or {})
    if normalize_name(formula_name) in _CUSTOM_NAMES:
        expression = str(payload.get("expression") or "").strip()
        variables: dict[str, Any] = {}
        raw_variables = payload.get("variables")
        if isinstance(raw_variables, Mapping):
            variables.update(raw_variables)
        for key, value in payload.items():
            if key not in {"expression", "variables"}:
                variables[key] = value
        return compute_expression(expression, variables)

    name = resolve_name(formula_name)
    spec = FORMULAS.get(name)
    if spec is None:
        available = "、".join(sorted(FORMULAS))
        return FormulaResult(
            formula_name=formula_name,
            category="未知",
            description="未注册的公式。",
            expression="",
            inputs={},
            value=None,
            unit="",
            display="不可计算",
            calculation_status="unavailable",
            explanation=(
                f"公式 {formula_name!r} 未注册，无法计算。可用公式：{available}。"
                "如需自定义计算，请把结果拆解为明确的输入变量，并使用 custom_expression 的安全表达式（例如 (revenue - cost) / revenue）。"
            ),
            formula_version=FORMULA_VERSION,
        )

    coerced, problems = _coerce_inputs(spec, inputs)
    if problems:
        return FormulaResult(
            formula_name=spec.name,
            category=spec.category,
            description=spec.description,
            expression=spec.expression,
            inputs=coerced,
            value=None,
            unit=spec.output_unit,
            display="不可计算",
            calculation_status="insufficient_data",
            explanation="输入有问题：" + "；".join(problems),
            formula_version=FORMULA_VERSION,
        )

    outcome = spec.func(coerced)
    notes = list(spec.notes)
    notes.extend(outcome.notes)
    if outcome.status == "ok" and outcome.value is None:
        outcome = FormulaOutcome(status="insufficient_data", explanation=outcome.explanation or "缺少必要输入。")
    explanation = outcome.explanation or (spec.applicability if outcome.status != "ok" else "")
    return FormulaResult(
        formula_name=spec.name,
        category=spec.category,
        description=spec.description,
        expression=spec.expression,
        inputs=coerced,
        value=outcome.value,
        unit=spec.output_unit,
        display=format_value(outcome.value, spec.output_unit),
        calculation_status=outcome.status,
        explanation=explanation,
        formula_version=FORMULA_VERSION,
        notes=notes,
    )


def compute_expression(expression: str, variables: Mapping[str, Any] | None = None) -> FormulaResult:
    """Compute a user supplied arithmetic expression safely."""
    variables = dict(variables or {})
    try:
        value = evaluate_expression(expression, variables)
    except FormulaError as exc:
        return FormulaResult(
            formula_name="custom_expression",
            category="自定义表达式",
            description="在安全数学表达式引擎中计算用户拆解的公式。",
            expression=expression,
            inputs={key: (float(val) if isinstance(val, (int, float)) else None) for key, val in variables.items()},
            value=None,
            unit="",
            display="不可计算",
            calculation_status="not_applicable",
            explanation=str(exc),
            formula_version=FORMULA_VERSION,
            notes=("只支持 + - * / ** % 与括号、数字变量以及 sqrt/abs/min/max/round/log/exp 等函数。",),
        )
    return FormulaResult(
        formula_name="custom_expression",
        category="自定义表达式",
        description="在安全数学表达式引擎中计算用户拆解的公式。",
        expression=expression,
        inputs={key: (float(val) if isinstance(val, (int, float)) else None) for key, val in variables.items()},
        value=_round(value),
        unit="",
        display=format_value(value, "ratio"),
        calculation_status="ok",
        explanation="按安全表达式引擎计算，仅做算术运算，未引入外部数据。",
        formula_version=FORMULA_VERSION,
        notes=("结果单位由用户自行确定；表达式不访问任何外部数据。",),
    )
