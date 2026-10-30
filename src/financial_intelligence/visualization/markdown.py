"""Markdown rendering helpers for the report module.

Reports are produced as Markdown first (the primary, diff-friendly format) and
then wrapped into a self-contained HTML document with a small, readable style
sheet.  No external CSS/JS is loaded, so a generated report opens offline.
"""

from __future__ import annotations

from markdown import markdown as _markdown

_EXTENSIONS = ["tables", "fenced_code", "sane_lists", "toc"]

_STYLE = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body {
  margin: 0; padding: 48px 24px 72px; background: #f7f8fa; color: #1f2937;
  font-family: "Segoe UI", "Microsoft YaHei", -apple-system, BlinkMacSystemFont, sans-serif;
  line-height: 1.65; font-size: 15px;
}
main { max-width: 980px; margin: 0 auto; background: #ffffff; padding: 44px 52px 56px;
  border-radius: 12px; box-shadow: 0 1px 3px rgba(15, 23, 42, 0.08); }
h1 { font-size: 26px; margin: 0 0 6px; color: #1f3a5f; letter-spacing: 0.01em; }
h2 { font-size: 20px; margin: 34px 0 12px; padding-bottom: 6px; color: #1f3a5f;
  border-bottom: 1px solid #e5e7eb; }
h3 { font-size: 16px; margin: 22px 0 8px; color: #334155; }
p, li { color: #334155; }
table { border-collapse: collapse; width: 100%; margin: 14px 0 18px; font-size: 14px; }
th, td { border: 1px solid #e5e7eb; padding: 7px 10px; text-align: right; }
th:first-child, td:first-child { text-align: left; }
thead th { background: #f1f5f9; color: #1f3a5f; font-weight: 600; }
tbody tr:nth-child(even) { background: #fafbfc; }
code { background: #f1f5f9; padding: 1px 5px; border-radius: 4px; font-size: 13px; }
pre { background: #0f172a; color: #e2e8f0; padding: 14px 16px; border-radius: 8px;
  overflow-x: auto; font-size: 13px; }
blockquote { margin: 16px 0; padding: 10px 16px; border-left: 3px solid #1f3a5f;
  background: #f8fafc; color: #475569; }
img { max-width: 100%; border: 1px solid #e5e7eb; border-radius: 8px; margin: 12px 0; }
hr { border: none; border-top: 1px solid #e5e7eb; margin: 28px 0; }
.meta { color: #64748b; font-size: 13px; margin: 0 0 24px; }
.footer { margin-top: 36px; padding-top: 14px; border-top: 1px solid #e5e7eb;
  color: #64748b; font-size: 12px; }
"""


def to_html_fragment(markdown_text: str) -> str:
    """Convert our Markdown subset into an HTML fragment."""
    return _markdown(markdown_text, extensions=_EXTENSIONS, output_format="html")


def render_document(markdown_text: str, *, title: str, subtitle: str | None = None) -> str:
    """Render a complete standalone HTML document."""
    body = to_html_fragment(markdown_text)
    subtitle_html = f'<p class="meta">{subtitle}</p>' if subtitle else ""
    footer = (
        '<p class="footer">本报告由 Financial Market Intelligence 自动生成 · '
        "数据来源与限制见报告正文 · 仅供研究与学习，不构成投资建议。</p>"
    )
    return (
        "<!DOCTYPE html>\n"
        '<html lang="zh-CN">\n<head>\n<meta charset="utf-8" />\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1" />\n'
        f"<title>{title}</title>\n<style>{_STYLE}</style>\n</head>\n"
        f'<body><main><h1>{title}</h1>{subtitle_html}{body}{footer}</main></body>\n</html>\n'
    )
