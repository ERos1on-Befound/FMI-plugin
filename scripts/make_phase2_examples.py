"""Generate the three phase-2 example documents from real tool output.

Usage:
    .venv\\Scripts\\python.exe scripts/make_phase2_examples.py

Everything written here comes from live provider calls (no hard-coded numbers),
so the examples cannot drift away from actual behaviour.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from financial_intelligence.analysis.financial_formulas import compute_formula
from financial_intelligence.services import CalculationService, FundamentalService, TechnicalService

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"


def _value(value: object) -> str:
    """Render enums (e.g. DataStatus.HISTORICAL) as their plain value."""
    return str(getattr(value, "value", value))


def _compact(params: dict[str, object]) -> str:
    parts: list[str] = []
    for key, raw in params.items():
        if isinstance(raw, float) and raw.is_integer():
            parts.append(f"{key}={int(raw)}")
        else:
            parts.append(f"{key}={raw}")
    return ", ".join(parts)


def _fmt(value: float | None, unit: str = "") -> str:
    if value is None:
        return "—"
    if unit == "%":
        return f"{value:,.2f}%"
    if unit == "x":
        return f"{value:,.2f}x"
    if abs(value) >= 1000:
        return f"{value:,.0f}"
    return f"{value:,.4f}".rstrip("0").rstrip(".")


def fundamental_doc() -> str:
    service = FundamentalService()
    result = service.analyze("AAPL", scope="full", period_type="annual", periods=3)
    lines = [
        "# 示例：真实公司基本面分析（AAPL）",
        "",
        "> 本文件由 `scripts/make_phase2_examples.py` 调用真实数据源生成，数字不是手写的。",
        "> 生成方式：`analyze_fundamentals(symbol=\"AAPL\", analysis_scope=\"full\", period_type=\"annual\", periods=3)`。",
        "",
        "## 1. 公司与数据来源",
        "",
        f"- 代码 / 名称：`{result.symbol}` / {result.company_name or '—'}",
        f"- 市场 / 币种 / 单位：{_value(result.market)} / {result.currency} / {result.unit}",
        f"- 报告期类型：{result.period_type}；最新报告期：{result.fiscal_period or '—'}",
        f"- 披露日 `filing_date`：{result.filing_date or '—（该源未提供）'}",
        f"- 数据来源：{result.provenance.source}；获取时间：{result.provenance.retrieved_at}",
        f"- 数据状态：`{_value(result.data_status)}`",
        "",
        "## 2. 多期指标表",
        "",
    ]
    for dimension, table in result.metric_tables.items():
        lines.append(f"### {dimension}")
        lines.append("")
        for row_index, row in enumerate(table):
            lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
            if row_index == 0:
                lines.append("|" + " --- |" * len(row))
        lines.append("")

    lines.extend(["## 3. 维度结论（规则驱动，可核对）", ""])
    for dimension in result.dimensions:
        lines.append(f"### {dimension.label}（`{dimension.status}`）")
        lines.append("")
        for finding in dimension.findings:
            value = _fmt(finding.value, finding.unit)
            lines.append(f"- **{finding.label}**：{value} — `{finding.status}`；规则：{finding.rule}")
            if finding.explanation:
                lines.append(f"  - {finding.explanation}")
        for note in dimension.notes:
            lines.append(f"- 备注：{note}")
        lines.append("")

    lines.extend(["## 4. 主要观察点", ""])
    lines.extend(f"- {item}" for item in result.observations)
    lines.extend(["", "## 5. 数据限制", ""])
    lines.extend(f"- {item}" for item in result.limitations)
    lines.append("")
    lines.append("> 本分析只做数据整理与规则计算，不提供行业基准与投资建议。")
    lines.append("")
    return "\n".join(lines)


def technical_doc() -> str:
    service = TechnicalService()
    result = service.get_indicators("NVDA", period="6mo", interval="1d")
    lines = [
        "# 示例：真实标的技术面分析（NVDA）",
        "",
        "> 由 `scripts/make_phase2_examples.py` 调用真实行情生成；"
        "生成方式：`analyze_technicals(symbol=\"NVDA\", period=\"6mo\", interval=\"1d\")`。",
        "",
        "## 1. 标的与数据范围",
        "",
        f"- 代码 / 名称：`{result.symbol}` / {result.company_name or '—'}",
        f"- 区间 / 间隔：`{result.period}` / `{result.interval}`",
        f"- 实际区间：{result.window_start} → {result.window_end}（{result.bars} 根）",
        f"- 来源：{result.provenance.source}；获取时间：{result.provenance.retrieved_at}",
        f"- 数据状态：`{_value(result.data_status)}`",
        "",
        "## 2. 指标数值",
        "",
        "| 指标 | 类别 | 参数 | 最新值 | 前值 | 变化 | 日期 | 状态 |",
        "| --- | --- | --- | ---: | ---: | ---: | --- | --- |",
    ]
    for indicator in result.indicators:
        params = _compact(indicator.params)
        lines.append(
            "| "
            + " | ".join(
                [
                    indicator.name,
                    indicator.category,
                    params,
                    _fmt(indicator.value, indicator.unit),
                    _fmt(indicator.previous_value, indicator.unit),
                    _fmt(indicator.change, indicator.unit),
                    indicator.date or "—",
                    indicator.status,
                ]
            )
            + " |"
        )

    lines.extend(["", "## 3. 历史状态解读（只描述，不预测）", ""])
    lines.extend(f"- {item}" for item in result.summary)
    lines.extend(["", "## 4. 口径与限制", ""])
    lines.extend(f"- {item}" for item in result.notes)
    lines.append("")
    lines.append("> 技术指标只是对历史价格的统计描述，不构成买卖建议。")
    lines.append("")
    return "\n".join(lines)


def calculation_doc() -> str:
    fundamental = FundamentalService()
    statements = fundamental.get_financials("AAPL", period_type="annual", limit=4).statements
    calc = CalculationService()

    lines = [
        "# 示例：金融公式计算（真实营收 CAGR 与现金流质量）",
        "",
        "> 数字来自 `get_company_financials(\"AAPL\", annual, 4)` 的真实返回值，"
        "再交给 `calculate_financial_formula` 计算。",
        "",
        "## 1. 取数",
        "",
        "| 报告期 | 报告期区间 | 营业收入 | 净利润 | 经营现金流 | 资本支出 |",
        "| --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for statement in statements:
        lines.append(
            "| "
            + " | ".join(
                [
                    statement.fiscal_period,
                    f"{statement.period_start} → {statement.period_end}",
                    _fmt(statement.income_statement.get("revenue")),
                    _fmt(statement.income_statement.get("net_income")),
                    _fmt(statement.cash_flow_statement.get("operating_cash_flow")),
                    _fmt(statement.cash_flow_statement.get("capital_expenditure")),
                ]
            )
            + " |"
        )

    newest, oldest = statements[0], statements[-1]
    years = len(statements) - 1
    beginning = oldest.income_statement.get("revenue")
    ending = newest.income_statement.get("revenue")
    cagr = calc.calculate(
        "cagr",
        {"beginning": beginning, "ending": ending, "years": years},
    )

    lines.extend(
        [
            "",
            "## 2. 营收 CAGR",
            "",
            f"- 起止：{oldest.fiscal_period}（{_fmt(beginning)}）→ {newest.fiscal_period}（{_fmt(ending)}）",
            f"- 间隔年数 n = {years}（首尾报告期之间）",
            f"- 公式：{cagr.expression}",
            f"- 结果：**{cagr.display}**（value={cagr.value}，status=`{cagr.calculation_status}`）",
            f"- 说明：{cagr.explanation or '—'}",
            "",
            "## 3. 经营现金流 / 净利润（逐年）",
            "",
            "| 报告期 | OCF | 净利润 | OCF/净利 | 状态 | 说明 |",
            "| --- | ---: | ---: | ---: | --- | --- |",
        ]
    )
    for statement in statements:
        ocf = statement.cash_flow_statement.get("operating_cash_flow")
        net = statement.income_statement.get("net_income")
        ratio = compute_formula(
            "cfo_to_net_income",
            {"operating_cash_flow": ocf, "net_income": net},
        )
        lines.append(
            "| "
            + " | ".join(
                [
                    statement.fiscal_period,
                    _fmt(ocf),
                    _fmt(net),
                    ratio.display if ratio.display else "—",
                    ratio.calculation_status,
                    (ratio.explanation or "—").replace("|", "/"),
                ]
            )
            + " |"
        )

    lines.extend(
        [
            "",
            "## 4. 自定义表达式（安全沙箱）",
            "",
            "```json",
            '{"formula_name": "custom_expression",',
            ' "inputs": {"expression": "(revenue - cost) / revenue * 100",',
            '            "variables": {"revenue": 1000, "cost": 600}}}',
            "```",
            "",
        ]
    )
    custom = calc.calculate(
        "custom_expression",
        {"expression": "(revenue - cost) / revenue * 100", "variables": {"revenue": 1000, "cost": 600}},
    )
    lines.append(f"结果：{custom.display}（status=`{custom.calculation_status}`）；禁止 `eval`，只允许白名单数学运算。")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    EXAMPLES.mkdir(parents=True, exist_ok=True)
    outputs = {
        "fundamental_analysis.md": fundamental_doc(),
        "technical_analysis.md": technical_doc(),
        "financial_calculation.md": calculation_doc(),
    }
    for name, text in outputs.items():
        path = EXAMPLES / name
        path.write_text(text, encoding="utf-8")
        print(f"wrote {path} ({len(text)} chars)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
