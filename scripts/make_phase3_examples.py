"""Generate the phase-3 example documents from real data.

Every number in examples/ comes from an actual call: market overview and quotes
via ``MarketService``, indicator panels via ``ChartService`` (which reuses
``analysis.technical``), and the report excerpt via ``ReportService``.

Run from the project root with the project interpreter:

    .venv/Scripts/python scripts/make_phase3_examples.py
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from financial_intelligence.config import load_settings  # noqa: E402
from financial_intelligence.services import ChartService, MarketService, ReportService  # noqa: E402

LOCAL = ZoneInfo("Asia/Shanghai")
EXAMPLES = ROOT / "examples"
GENERATED = datetime.now(tz=LOCAL)
STAMP = GENERATED.strftime("%Y-%m-%d %H:%M 北京时间")


def local(iso_text: str | None) -> str:
    """Render an ISO-8601 UTC timestamp in Beijing time for readability."""
    if not iso_text:
        return "—"
    try:
        moment = datetime.fromisoformat(iso_text.replace("Z", "+00:00"))
    except ValueError:
        return iso_text
    return moment.astimezone(LOCAL).strftime("%Y-%m-%d %H:%M")


def value(number: float | None, digits: int = 2) -> str:
    return "—" if number is None else f"{number:,.{digits}f}"


def percent(number: float | None) -> str:
    return "—" if number is None else f"{number:+.2f}%"


def write(name: str, lines: list[str]) -> Path:
    path = EXAMPLES / name
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    print(f"wrote {path} ({len('\n'.join(lines))} chars)")
    return path


def build_market_overview() -> list[str]:
    service = MarketService(load_settings())
    overview = service.get_market_overview()
    lines = [
        "# 市场概览示例（真实数据）",
        "",
        f"生成时间：{STAMP}",
        "",
        "> 数据来自公开免费接口，属于**延迟行情**；行情时间为数据源快照时间，获取时间见每节末尾。"
        "本文件由 `scripts/make_phase3_examples.py` 生成，不含手工填写的数字。",
        "",
        f"成功 {overview.ok_count} 项，不可用 {overview.unavailable_count} 项。",
        "",
    ]
    for group in overview.groups:
        lines += [f"## {group.label}", "", "| 代码 | 名称 | 最新价 | 涨跌幅 | 行情时间 | 数据状态 |", "| --- | --- | --- | --- | --- | --- |"]
        for item in group.items:
            lines.append(
                f"| `{item.symbol}` | {item.name or '—'} | {value(item.price)} | {percent(item.change_percent)} "
                f"| {local(item.timestamp)} | {item.data_status.value} |"
            )
        lines.append("")
        if group.label.startswith("大宗"):
            lines.append("> 大宗商品为**期货合约参考价**，不是现货价格。")
            lines.append("")
    lines.append(f"来源：{', '.join(sorted({item.source for group in overview.groups for item in group.items}))}")
    return lines


def build_quote_example() -> list[str]:
    service = MarketService(load_settings())
    targets = ["AAPL", "0700.HK", "600519.SS", "^GSPC", "GC=F"]
    lines = [
        "# 单只标的行情示例（真实数据）",
        "",
        f"生成时间：{STAMP}",
        "",
        "| 代码 | 名称 | 市场 | 币种 | 最新价 | 昨收 | 涨跌幅 | 行情时间 | 获取时间 | 状态 | 来源 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    quotes = []
    for symbol in targets:
        try:
            quote = service.get_quote(symbol)
        except Exception as exc:  # noqa: BLE001 - example script keeps going
            lines.append(f"| `{symbol}` | 获取失败 | — | — | — | — | — | — | — | unavailable | — |")
            print(f"quote {symbol}: {exc}")
            continue
        quotes.append(quote)
        lines.append(
            f"| `{quote.symbol}` | {quote.name or '—'} | {quote.market.value} | {quote.currency} "
            f"| {value(quote.price)} | {value(quote.previous_close)} | {percent(quote.change_percent)} "
            f"| {local(quote.timestamp)} | {local(quote.retrieved_at)} | {quote.data_status.value} | {quote.source} |"
        )
    lines += ["", "说明：", "", "- 行情时间为数据源快照时间，获取时间是本次请求时间，两者必须区分。"]
    notes = [q.status_note for q in quotes if q.status_note]
    if notes:
        lines += ["- " + note for note in dict.fromkeys(notes)]
    lines.append("- 免费源无法证明实时性，因此状态是 `delayed` 而不是 `realtime`。")
    return lines


def build_chart_gallery() -> list[str]:
    charts = ChartService(load_settings())
    jobs = [
        ("AAPL", dict(chart_type="line", period="6mo", interval="1d")),
        ("NVDA", dict(chart_type="candlestick", period="3mo", interval="1d")),
        ("NVDA", dict(chart_type="technical", period="6mo", interval="1d", indicators="ma,rsi,macd")),
        ("AAPL", dict(chart_type="fundamental", metrics="revenue,gross_margin")),
    ]
    lines = [
        "# 图表示例（真实数据）",
        "",
        f"生成时间：{STAMP}",
        "",
        "以下图表全部由 `ChartService` 用真实行情/财报生成，指标来自 `analysis.technical` 与 `analysis.financial_ratios`，"
        "绘图代码不另算公式。HTML 内联 plotly.js，可离线打开。",
        "",
        "| 图表 | 标的 | 类型 | 数据区间 | 数据点 | 来源 | 状态 | HTML | PNG |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    produced: list[tuple[str, str, str]] = []
    for symbol, kwargs in jobs:
        try:
            result = charts.generate(symbol, **kwargs)
        except Exception as exc:  # noqa: BLE001
            print(f"chart {symbol} {kwargs}: {exc}")
            lines.append(f"| — | `{symbol}` | {kwargs['chart_type']} | 生成失败 | — | — | unavailable | — | — |")
            continue
        html = str(Path(result.html_path).relative_to(ROOT)) if result.html_path else "—"
        png = str(Path(result.png_path).relative_to(ROOT)) if result.png_path else "—"
        data_range = result.data_range or "—"
        lines.append(
            f"| {result.chart_name} | `{result.symbol}` | {result.chart_type} | {data_range} "
            f"| {result.metadata.points} | {result.source} | {result.status} | {html} | {png} |"
        )
        if result.html_path:
            produced.append((result.chart_name, html, result.chart_type))

    try:
        comparison = charts.comparison("AAPL,MSFT,NVDA", metric="price", period="6mo", normalization="base_100")
        html = str(Path(comparison.html_path).relative_to(ROOT)) if comparison.html_path else "—"
        lines.append(
            f"| {comparison.chart_name} | 多标的 | {comparison.chart_type} | {comparison.data_range or '—'} "
            f"| {comparison.metadata.points} | {comparison.source} | {comparison.status} | {html} | — |"
        )
    except Exception as exc:  # noqa: BLE001
        print(f"comparison: {exc}")

    lines += [
        "",
        "说明：",
        "",
        "- 归一化对比（`base_100`）把各标的起点都设为 100，只反映相对涨跌，**不代表汇率调整后的投资回报**。",
        "- 不同币种的金额不能放在同一纵轴；比率指标与归一化走势可以。",
        "- 文件路径是本机路径；其他机器不保证可访问。",
    ]
    return lines


def build_report_example() -> list[str]:
    reports = ReportService(load_settings())
    result = reports.generate(
        "AAPL",
        report_type="fundamental",
        output_format="html",
        include_charts=True,
        periods=3,
    )
    markdown_path = Path(result.markdown_path) if result.markdown_path else None
    body = markdown_path.read_text(encoding="utf-8") if markdown_path and markdown_path.exists() else ""
    excerpt = body[:1200].rstrip() + "\n\n…（以下略，完整文件见路径）" if body else "报告生成失败。"
    lines = [
        "# 报告生成示例（真实数据）",
        "",
        f"生成时间：{STAMP}",
        "",
        "调用：",
        "",
        "```python",
        "from financial_intelligence.services import ReportService",
        "",
        "reports = ReportService()",
        "result = reports.generate(",
        '    "AAPL", report_type="fundamental", output_format="html", include_charts=True, periods=3',
        ")",
        "```",
        "",
        f"- 报告类型：{result.report_type}",
        f"- 生成状态：{result.status}",
        f"- 数据期间：{result.data_period or '—'}",
        f"- 数据来源：{', '.join(result.data_sources) or '—'}",
        f"- Markdown：`{result.markdown_path or '—'}`",
        f"- HTML：`{result.html_path or '—'}`",
        f"- 图表数量：{len(result.charts)}",
        f"- 缺失数据：{', '.join(result.missing_data) or '无'}",
        "",
        "## 报告开头（节选）",
        "",
        "```markdown",
        excerpt,
        "```",
        "",
        "说明：报告里的每个数字都来自真实财报与真实计算；缺失科目用「—」标注并在「数据限制」里说明，"
        "报告不包含投资建议或评级。",
    ]
    return lines


def main() -> int:
    EXAMPLES.mkdir(parents=True, exist_ok=True)
    write("market_overview.md", build_market_overview())
    write("stock_quote.md", build_quote_example())
    write("chart_gallery.md", build_chart_gallery())
    write("report_generation.md", build_report_example())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
