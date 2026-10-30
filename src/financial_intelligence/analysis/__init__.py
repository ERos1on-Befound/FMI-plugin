"""Analysis layers: safe formula engine, ratios, fundamentals and technicals.

The package is intentionally split so that each piece has a single job:

- :mod:`financial_formulas` – registered formula engine plus a safe arithmetic
  expression evaluator (no ``eval``, no dynamic code).
- :mod:`financial_ratios` – turns canonical financial statements into metrics.
- :mod:`fundamental` – five-dimension fundamental analysis with explicit rules.
- :mod:`technical` – technical indicators computed with pandas/numpy.
"""

from __future__ import annotations

from .financial_formulas import (
    FORMULA_VERSION,
    FORMULAS,
    FormulaError,
    FormulaInput,
    FormulaOutcome,
    FormulaSpec,
    compute_expression,
    compute_formula,
    evaluate_expression,
    format_value,
    list_formulas,
    normalize_name,
)

__all__ = [
    "FORMULAS",
    "FORMULA_VERSION",
    "FormulaError",
    "FormulaInput",
    "FormulaOutcome",
    "FormulaSpec",
    "compute_formula",
    "compute_expression",
    "evaluate_expression",
    "format_value",
    "list_formulas",
    "normalize_name",
]
