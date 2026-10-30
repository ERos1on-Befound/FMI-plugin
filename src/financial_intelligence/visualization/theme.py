"""Shared visual styling for FMI charts.

Everything that decides *how* a chart looks lives here: colours, fonts, the base
layout, axis rules and the provenance footer.  Figure builders
(:mod:`financial_intelligence.visualization.figures`) only add traces.

Design goals (Phase 3 spec):

* a restrained, professional research look — no decorative elements;
* consistent up/down colours across every chart type;
* the provenance footer is always present: source, data timestamp, generation
  time and data status appear on every figure;
* numbers use one formatting rule everywhere, in both Chinese and English.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, Sequence

FONT_FAMILY = '"Segoe UI", "Microsoft YaHei", "Helvetica Neue", Arial, sans-serif'

#: Chinese market convention: red = up, green = down.  Used everywhere so that
#: every chart in the project agrees.
UP_COLOR = "#c0392b"
DOWN_COLOR = "#1e8449"
UP_FILL = "rgba(192, 57, 43, 0.16)"
DOWN_FILL = "rgba(30, 132, 73, 0.16)"

PRIMARY = "#1f3a5f"
SECONDARY = "#b8860b"
MUTED = "#6b7280"
GRID_COLOR = "rgba(15, 23, 42, 0.08)"
AXIS_LINE = "rgba(15, 23, 42, 0.25)"
REFERENCE_COLOR = "rgba(107, 114, 128, 0.55)"

PALETTE: tuple[str, ...] = (
    PRIMARY,
    "#e07a5f",
    "#3d9970",
    "#7b6cb0",
    "#b8860b",
    "#4a9bd1",
    "#b56576",
    "#2a9d8f",
)

_STATUS_TEXT = {
    "realtime": "实时",
    "delayed": "延迟",
    "historical": "历史",
    "stale": "过期",
    "unavailable": "不可用",
    "unknown": "未知",
}


def status_text(status: str | None) -> str:
    """Return the Chinese label for a ``data_status`` value."""
    return _STATUS_TEXT.get((status or "unknown").lower(), status or "未知")


def now_iso() -> str:
    """Current time as an ISO-8601 string in UTC (seconds precision)."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def format_number(value: float | None, *, digits: int = 2) -> str:
    """Format a plain number with thousands separators."""
    if value is None:
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    if number != number:  # NaN
        return "—"
    return f"{number:,.{digits}f}"


def format_amount(value: float | None, unit: str | None = None) -> str:
    """Format a monetary amount compactly (B/M/K) for axis and hover labels."""
    if value is None:
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    if number != number:
        return "—"
    magnitude = abs(number)
    suffix = ""
    scaled = number
    if magnitude >= 1_000_000_000_000:
        scaled, suffix = number / 1_000_000_000_000, "T"
    elif magnitude >= 1_000_000_000:
        scaled, suffix = number / 1_000_000_000, "B"
    elif magnitude >= 1_000_000:
        scaled, suffix = number / 1_000_000, "M"
    elif magnitude >= 1_000:
        scaled, suffix = number / 1_000, "K"
    text = f"{scaled:,.2f}".rstrip("0").rstrip(".")
    return f"{text}{suffix}{(' ' + unit) if unit else ''}"


def percent(value: float | None, *, digits: int = 2) -> str:
    """Format a ratio-as-percent value (``12.5`` means ``12.5%``)."""
    if value is None:
        return "—"
    return f"{float(value):,.{digits}f}%"


def hex_to_rgba(color: str, alpha: float) -> str:
    """Convert ``#rrggbb`` to ``rgba(...)`` (used for soft fills)."""
    color = color.lstrip("#")
    if len(color) != 6:
        return f"rgba(31, 58, 95, {alpha})"
    r, g, b = (int(color[i : i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r}, {g}, {b}, {alpha})"


def source_note(
    *,
    source: str | None,
    data_timestamp: str | None,
    generated_at: str | None,
    data_status: str | None,
    extra: str | None = None,
) -> str:
    """One-line provenance footer used on every figure."""
    parts = [f"来源：{source or '未知'}"]
    parts.append(f"行情时间：{data_timestamp or '未提供'}")
    parts.append(f"获取时间：{generated_at or '未知'}")
    parts.append(f"数据状态：{status_text(data_status)}")
    if extra:
        parts.append(extra)
    return " · ".join(parts)


def base_layout(
    *,
    title: str,
    subtitle: str | None = None,
    height: int = 640,
    y_title: str = "",
    x_title: str = "",
    showlegend: bool = True,
    margin: dict[str, int] | None = None,
) -> dict:
    """Return the shared layout dictionary for a figure."""
    annotations: list[dict] = []
    if subtitle:
        annotations.append(
            dict(
                text=subtitle,
                xref="paper",
                yref="paper",
                x=0,
                y=1.04,
                xanchor="left",
                yanchor="bottom",
                showarrow=False,
                font=dict(size=12, color=MUTED),
            )
        )
    return dict(
        title=dict(text=title, x=0.01, xanchor="left", font=dict(size=18, color=PRIMARY)),
        font=dict(family=FONT_FAMILY, size=12, color="#1f2937"),
        height=height,
        margin=margin or dict(l=60, r=30, t=86, b=96),
        paper_bgcolor="#ffffff",
        plot_bgcolor="#ffffff",
        hovermode="x unified",
        showlegend=showlegend,
        legend=dict(orientation="h", yanchor="bottom", y=1.01, xanchor="right", x=1.0),
        xaxis=dict(
            title=dict(text=x_title, font=dict(size=12, color=MUTED)),
            showgrid=False,
            showline=True,
            linecolor=AXIS_LINE,
            ticks="outside",
            tickfont=dict(size=11, color=MUTED),
            rangeslider=dict(visible=False),
        ),
        yaxis=dict(
            title=dict(text=y_title, font=dict(size=12, color=MUTED)),
            gridcolor=GRID_COLOR,
            zeroline=False,
            showline=True,
            linecolor=AXIS_LINE,
            tickfont=dict(size=11, color=MUTED),
        ),
        annotations=annotations,
    )


def add_source_note(
    fig,
    *,
    source: str | None,
    data_timestamp: str | None,
    generated_at: str | None,
    data_status: str | None,
    extra: str | None = None,
) -> None:
    """Attach the provenance footer to the bottom-left of a figure."""
    fig.add_annotation(
        text=source_note(
            source=source,
            data_timestamp=data_timestamp,
            generated_at=generated_at,
            data_status=data_status,
            extra=extra,
        ),
        xref="paper",
        yref="paper",
        x=0,
        y=-0.16,
        xanchor="left",
        yanchor="top",
        showarrow=False,
        font=dict(size=10, color=MUTED),
    )


def add_reference_line(fig, *, value: float, label: str, row=None, col=None) -> None:
    """Add a dashed horizontal reference line (used by RSI/MACD panels)."""
    fig.add_hline(
        y=value,
        line=dict(color=REFERENCE_COLOR, width=1, dash="dot"),
        annotation_text=label,
        annotation_position="right",
        annotation_font=dict(size=10, color=MUTED),
        row=row,
        col=col,
    )


def color_for(index: int) -> str:
    """Stable colour for the n-th series in a comparison chart."""
    return PALETTE[index % len(PALETTE)]


def split_note_lines(notes: Iterable[str] | None, *, limit: int = 3) -> Sequence[str]:
    """Return the first ``limit`` notes, dropping empties."""
    if not notes:
        return []
    cleaned = [str(note).strip() for note in notes if str(note).strip()]
    return cleaned[:limit]
