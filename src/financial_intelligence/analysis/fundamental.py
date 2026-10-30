"""Rule-based fundamental analysis.

Every conclusion is produced by an explicit rule over computed metrics - never
by free-form judgement.  Rules are intentionally conservative: the project has
no industry benchmark database, so it never calls a single ratio "good" or
"bad"; it only states sign/coverage facts and multi-period direction.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..models.analysis_result import DimensionAnalysis, Finding, MetricSeries
from ..models.financial_statements import FinancialStatement
from .financial_ratios import DIMENSIONS, DIMENSION_METRICS, DIMENSION_ORDER, table_for_series

SCOPE_ALL = "full"

SCOPE_ALIASES: dict[str, str] = {
    "full": SCOPE_ALL,
    "all": SCOPE_ALL,
    "overview": SCOPE_ALL,
    "profitability": "profitability",
    "profit": "profitability",
    "margin": "profitability",
    "盈利": "profitability",
    "盈利能力": "profitability",
    "solvency": "solvency",
    "偿债": "solvency",
    "偿债能力": "solvency",
    "efficiency": "efficiency",
    "operation": "efficiency",
    "运营": "efficiency",
    "运营效率": "efficiency",
    "growth": "growth",
    "成长": "growth",
    "成长性": "growth",
    "cash_flow": "cash_flow",
    "cashflow": "cash_flow",
    "现金流": "cash_flow",
    "valuation": "valuation",
    "估值": "valuation",
}


def resolve_scope(scope: str | None) -> str:
    key = str(scope or SCOPE_ALL).strip().lower().replace(" ", "_")
    if key in SCOPE_ALIASES:
        return SCOPE_ALIASES[key]
    raise ValueError(
        f"不支持的分析范围：{scope}；可用：full、profitability、solvency、efficiency、growth、cash_flow、valuation。"
    )


def _point(series: MetricSeries | None, index: int = 0):
    if series is None or len(series.points) <= index:
        return None
    return series.points[index]


def _finding_from_sign(
    series: MetricSeries,
    *,
    positive_rule: str,
    negative_rule: str,
    positive_text: str,
    negative_text: str,
    positive_when_above: float = 0.0,
    rule_label: str,
) -> Finding:
    point = series.latest
    value = point.value if point else None
    period = point.period if point else None
    if value is None:
        return Finding(
            metric=series.name,
            label=series.label,
            status="insufficient_data",
            rule=rule_label,
            explanation=f"{series.label}无法计算：{point.explanation if point else '缺少所需科目'}",
            period=period,
        )
    if value > positive_when_above:
        status = "positive"
        text = positive_text
        rule = positive_rule
    else:
        status = "negative"
        text = negative_text
        rule = negative_rule
    return Finding(
        metric=series.name,
        label=series.label,
        value=value,
        unit=series.unit,
        display=point.display if point else None,
        status=status,
        rule=rule,
        explanation=text,
        period=period,
    )


def _ratio_component(series: MetricSeries, *, threshold: float, metric_above: str, metric_below: str) -> Finding:
    """A descriptive finding that never claims "good"/"bad" beyond the sign of coverage."""
    point = series.latest
    value = point.value if point else None
    if value is None:
        return Finding(
            metric=series.name,
            label=series.label,
            status="insufficient_data",
            rule="需要该指标可计算",
            explanation=f"{series.label}无法计算：{point.explanation if point else '缺少所需科目'}",
            period=point.period if point else None,
        )
    if value < threshold:
        return Finding(
            metric=series.name,
            label=series.label,
            value=value,
            unit=series.unit,
            display=point.display if point else None,
            status="negative",
            rule=metric_below,
            explanation=f"{series.label}为 {point.display if point else value}，低于 {threshold:g}，这是一个可以通过数据核对的客观事实；是否构成风险需结合行业与债务结构判断。",
            period=point.period if point else None,
        )
    return Finding(
        metric=series.name,
        label=series.label,
        value=value,
        unit=series.unit,
        display=point.display if point else None,
        status="neutral",
        rule=metric_above,
        explanation=f"{series.label}为 {point.display if point else value}，不低于 {threshold:g}；该规则只核对覆盖关系，不代表经营质量评价。",
        period=point.period if point else None,
    )


def _trend_note(series: MetricSeries | None) -> str | None:
    if series is None or len(series.points) < 2:
        return None
    labels = {"rising": "上升", "falling": "下降", "stable": "基本持平"}
    direction = labels.get(series.trend)
    if not direction:
        return None
    newest, oldest = series.points[0], series.points[-1]
    if newest.value is None or oldest.value is None:
        return None
    return f"{series.label}从 {oldest.period} 的 {oldest.display} 变为 {newest.period} 的 {newest.display}（{direction}）。"


def _dimension(dimension: str, findings: list[Finding], notes: list[str]) -> DimensionAnalysis:
    statuses = [finding.status for finding in findings]
    if not findings or all(status == "insufficient_data" for status in statuses):
        status = "insufficient_data"
    elif any(status == "negative" for status in statuses):
        status = "negative"
    elif any(status == "positive" for status in statuses):
        status = "positive"
    else:
        status = "neutral"
    return DimensionAnalysis(
        dimension=dimension,
        label=DIMENSIONS[dimension],
        status=status,
        findings=findings,
        notes=[note for note in notes if note],
    )


def analyze_profitability(series: dict[str, MetricSeries]) -> DimensionAnalysis:
    findings: list[Finding] = []
    if "net_margin" in series:
        findings.append(
            _finding_from_sign(
                series["net_margin"],
                positive_rule="净利率 > 0",
                negative_rule="净利率 <= 0",
                positive_text="净利润为正，因此净利率大于 0；该规则只核对盈利方向，不评价盈利能力强弱（项目没有行业基准）。",
                negative_text="净利润为负，净利率小于等于 0。",
                rule_label="净利率符号",
            )
        )
    if "roe" in series:
        findings.append(
            _finding_from_sign(
                series["roe"],
                positive_rule="ROE > 0",
                negative_rule="ROE <= 0",
                positive_text="股东权益回报率为正（净利润/平均股东权益），未使用行业基准判断高低。",
                negative_text="股东权益回报率小于等于 0，说明该期净利润为负或股东权益为负，需结合净资产符号解读。",
                rule_label="ROE 符号",
            )
        )
    notes = [
        _trend_note(series.get("gross_margin")),
        _trend_note(series.get("net_margin")),
        "毛利率/营业利润率/净利率均为比率指标，可跨币种直接比较，但需注意各市场会计准则差异。",
    ]
    return _dimension("profitability", findings, notes)


def analyze_solvency(series: dict[str, MetricSeries]) -> DimensionAnalysis:
    findings: list[Finding] = []
    if "current_ratio" in series:
        findings.append(
            _ratio_component(
                series["current_ratio"],
                threshold=1.0,
                metric_above="流动比率 >= 1",
                metric_below="流动比率 < 1",
            )
        )
    if "interest_coverage" in series:
        point = series["interest_coverage"].latest
        if point is not None and point.value is None:
            findings.append(
                Finding(
                    metric="interest_coverage",
                    label=series["interest_coverage"].label,
                    status="not_applicable",
                    rule="利息费用 > 0 且 EBIT 可得",
                    explanation=f"利息保障倍数不适用：{point.explanation or '缺少利息费用或口径不一致'}",
                    period=point.period,
                )
            )
        elif point is not None:
            findings.append(
                _ratio_component(
                    series["interest_coverage"],
                    threshold=1.0,
                    metric_above="利息保障倍数 >= 1",
                    metric_below="利息保障倍数 < 1",
                )
            )
    notes = [
        _trend_note(series.get("debt_ratio")),
        "资产负债率在不同行业之间差异很大（银行/保险天然高于制造业），缺少行业基准时不做横向优劣判断。",
    ]
    return _dimension("solvency", findings, notes)


def analyze_efficiency(series: dict[str, MetricSeries]) -> DimensionAnalysis:
    findings: list[Finding] = []
    for name in ("asset_turnover", "receivables_turnover", "inventory_turnover"):
        if name not in series:
            continue
        point = series[name].latest
        if point is None or point.value is None:
            findings.append(
                Finding(
                    metric=name,
                    label=series[name].label,
                    status="insufficient_data",
                    rule="需要对应科目与平均余额",
                    explanation=f"{series[name].label}无法计算：{point.explanation if point else '缺少所需科目'}",
                    period=point.period if point else None,
                )
            )
            continue
        findings.append(
            Finding(
                metric=name,
                label=series[name].label,
                value=point.value,
                unit=series[name].unit,
                display=point.display,
                status="neutral",
                rule="指标可计算",
                explanation=f"{series[name].label}为 {point.display}（口径：{point.formula}），不同行业差异显著，仅作描述。",
                period=point.period,
            )
        )
    notes = [_trend_note(series.get("asset_turnover")), "周转率依赖平均余额口径，跨市场比较前需确认分母是否一致。"]
    return _dimension("efficiency", findings, notes)


def analyze_growth(series: dict[str, MetricSeries]) -> DimensionAnalysis:
    findings: list[Finding] = []
    for name in ("revenue_growth", "net_income_growth"):
        if name not in series:
            continue
        findings.append(
            _finding_from_sign(
                series[name],
                positive_rule=f"{name} > 0",
                negative_rule=f"{name} <= 0",
                positive_text=f"{series[name].label}为正，即本期高于可比上一期；增长幅度未与行业或自身历史基准比较。",
                negative_text=f"{series[name].label}小于等于 0，本期不高于可比上一期。若基期为负值，百分比变化的方向会失真，需同时核对绝对金额。",
                rule_label="同比变化符号",
            )
        )
    if "eps_growth" in series:
        point = series["eps_growth"].latest
        if point is not None and point.value is None:
            findings.append(
                Finding(
                    metric="eps_growth",
                    label=series["eps_growth"].label,
                    status="insufficient_data",
                    rule="需要两期每股收益且基期非 0",
                    explanation=f"每股收益同比无法计算：{point.explanation or '缺少可比基期'}",
                    period=point.period,
                )
            )
    notes = [
        "成长指标基于相邻同类型报告期（年度对年度、季度对季度），季度为年初至今累计口径时需先差分化。",
        "基期为负或接近 0 时，增长率会失真，本模块会同时给出绝对金额以便核对。",
    ]
    return _dimension("growth", findings, notes)


def analyze_cash_flow(series: dict[str, MetricSeries]) -> DimensionAnalysis:
    findings: list[Finding] = []
    if "free_cash_flow" in series:
        point = series["free_cash_flow"].latest
        if point is None or point.value is None:
            findings.append(
                Finding(
                    metric="free_cash_flow",
                    label=series["free_cash_flow"].label,
                    status="insufficient_data",
                    rule="需要经营现金流与资本支出",
                    explanation=f"自由现金流无法计算：{point.explanation if point else '缺少经营现金流或资本支出'}",
                    period=point.period if point else None,
                )
            )
        else:
            status = "positive" if point.value > 0 else "negative"
            findings.append(
                Finding(
                    metric="free_cash_flow",
                    label=series["free_cash_flow"].label,
                    value=point.value,
                    unit=series["free_cash_flow"].unit,
                    display=point.display,
                    status=status,
                    rule="自由现金流 > 0" if status == "positive" else "自由现金流 <= 0",
                    explanation=(
                        f"自由现金流（经营现金流 - 资本支出，CapEx 已统一为正的支出金额）= {point.display}。"
                        + ("为正表示经营现金在覆盖资本支出后仍有剩余。" if status == "positive" else "小于等于 0 表示当期经营现金流未覆盖资本支出。")
                    ),
                    period=point.period,
                )
            )
    if "cfo_to_net_income" in series:
        point = series["cfo_to_net_income"].latest
        if point is not None and point.value is not None:
            status = "positive" if point.value >= 1 else "neutral"
            findings.append(
                Finding(
                    metric="cfo_to_net_income",
                    label=series["cfo_to_net_income"].label,
                    value=point.value,
                    unit=series["cfo_to_net_income"].unit,
                    display=point.display,
                    status=status,
                    rule="经营现金流/净利润 >= 1",
                    explanation=f"经营现金流是净利润的 {point.display}；该比值只描述现金与账面利润的关系，净利润为负或接近 0 时不应据此判断盈利质量。",
                    period=point.period,
                )
            )
        elif point is not None:
            findings.append(
                Finding(
                    metric="cfo_to_net_income",
                    label=series["cfo_to_net_income"].label,
                    status="not_applicable",
                    rule="净利润 > 0",
                    explanation=f"经营现金流/净利润不适用：{point.explanation or '净利润小于等于 0'}",
                    period=point.period,
                )
            )
    notes = ["现金流为期间流量指标，不同报告期长度不可直接比较。"]
    return _dimension("cash_flow", findings, notes)


def analyze_valuation(series: dict[str, MetricSeries]) -> DimensionAnalysis:
    findings: list[Finding] = []
    for name in ("pe", "pb", "ps"):
        if name not in series:
            continue
        point = series[name].latest
        if point is None:
            continue
        if point.value is None:
            findings.append(
                Finding(
                    metric=name,
                    label=series[name].label,
                    status="not_applicable",
                    rule="需要同币种最新股价与对应财务口径",
                    explanation=f"{series[name].label}未生成：{point.explanation or '缺少可用行情或口径不一致'}",
                    period=point.period,
                )
            )
            continue
        findings.append(
            Finding(
                metric=name,
                label=series[name].label,
                value=point.value,
                unit=series[name].unit,
                display=point.display,
                status="neutral",
                rule="指标可计算",
                explanation=f"{series[name].label}为 {point.display}（{point.formula}）。估值倍数只描述价格与财务数据的比值，不构成买卖判断。",
                period=point.period,
            )
        )
    notes = [
        "估值指标需要行情币种与财报币种一致；项目没有免费的汇率来源，因此跨币种一律不生成。",
        "市盈率使用已披露每股收益（优先稀释口径），不生成预测 EPS。",
    ]
    return _dimension("valuation", findings, notes)


_DIMENSION_ANALYZERS = {
    "profitability": analyze_profitability,
    "solvency": analyze_solvency,
    "efficiency": analyze_efficiency,
    "growth": analyze_growth,
    "cash_flow": analyze_cash_flow,
    "valuation": analyze_valuation,
}


def build_dimensions(
    series_by_dimension: dict[str, dict[str, MetricSeries]],
    *,
    scope: str = SCOPE_ALL,
) -> list[DimensionAnalysis]:
    dimensions = DIMENSION_ORDER if scope == SCOPE_ALL else [scope]
    return [_DIMENSION_ANALYZERS[dimension](series_by_dimension.get(dimension, {})) for dimension in dimensions]


def build_observations(series_by_dimension: dict[str, dict[str, MetricSeries]]) -> list[str]:
    """Factual, data-backed sentences (no judgement, no prediction)."""
    observations: list[str] = []
    labels = {"rising": "上升", "falling": "下降", "stable": "基本持平"}
    for dimension in DIMENSION_ORDER:
        for name, series in series_by_dimension.get(dimension, {}).items():
            if len(series.points) < 2:
                continue
            newest, oldest = series.points[0], series.points[-1]
            if newest.value is None or oldest.value is None:
                continue
            if series.unit == "%" and "growth" not in name and dimension != "growth":
                continue
            direction = labels.get(series.trend, "趋势未判定")
            observations.append(
                f"{series.label}：{oldest.period} {oldest.display} → {newest.period} {newest.display}（{direction}）。"
            )
    return observations


def build_tables(series_by_dimension: dict[str, dict[str, MetricSeries]]) -> dict[str, list[list[str]]]:
    tables: dict[str, list[list[str]]] = {}
    trends = {
        "rising": "上升",
        "falling": "下降",
        "stable": "持平",
        "insufficient_data": "数据不足",
        "not_applicable": "不适用",
    }
    for dimension in DIMENSION_ORDER:
        series_map = series_by_dimension.get(dimension, {})
        if not series_map:
            continue
        selected = [
            series_map[name]
            for name in DIMENSION_METRICS[dimension]
            if series_map.get(name) is not None and series_map[name].points
        ]
        if not selected:
            continue
        table = table_for_series(selected)
        header = list(table[0]) + ["趋势"]
        rows = [
            list(row) + [trends.get(item.trend, "")]
            for row, item in zip(table[1:], selected, strict=False)
        ]
        tables[dimension] = [header] + rows
    return tables


def build_limitations(
    statements: Sequence[FinancialStatement],
    *,
    valuation_blocked: bool = False,
    extra: Sequence[str] = (),
) -> list[str]:
    limitations: list[str] = []
    if statements:
        newest = statements[0]
        limitations.append(
            f"财务数据来源：{newest.source}；报告期 {newest.period_start or '未知'} 至 {newest.period_end}，"
            f"披露/申报日期 {newest.filing_date or '数据源未提供'}。"
        )
        limitations.append(f"金额单位为 {newest.currency}，单位倍数 {newest.unit_scale:g}（原始金额 = 展示值 × 单位倍数）。")
        if newest.cumulative:
            limitations.append("所选季度报告为年初至今累计口径，单季数据需与上一期累计值相减。")
        missing = sorted({field for statement in statements for field in statement.missing_fields})
        if missing:
            limitations.append("以下标准科目在数据源中缺失或无法映射（不代表为 0）：" + "、".join(missing) + "。")
        if newest.derived_fields:
            limitations.append("部分科目由其他科目推导得到：" + "、".join(sorted(set(newest.derived_fields))) + "。")
    limitations.append("项目不提供行业分类与行业基准，因此不评价单个指标是“优秀”还是“糟糕”，也不做同业排名。")
    limitations.append("不同市场会计准则（US GAAP / CAS / HKFRS）不同，科目映射可能存在口径差异，横向比较需谨慎。")
    if valuation_blocked:
        limitations.append("估值指标未生成：缺少同币种的最新行情，或行情币种与财报币种不一致（项目不做汇率换算）。")
    limitations.extend(extra)
    return limitations
