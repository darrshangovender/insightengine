"""Heuristic chart-shape picker.

Looks at the result columns + first row and picks the best chart shape:

    - Single row, single numeric column   -> KPI
    - First column looks like a date/time -> LINE
    - First column is categorical + one numeric column -> BAR
    - Two numeric columns                 -> SCATTER
    - Otherwise                            -> TABLE

This is intentionally deterministic — no second LLM call. If the heuristic
guesses wrong, the UI lets the user override.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

ChartType = Literal["kpi", "line", "bar", "scatter", "table"]


@dataclass
class ChartSpec:
    """Frontend-ready chart specification."""

    type: ChartType
    x: str | None = None
    y: str | list[str] | None = None
    label: str | None = None
    value: Any = None
    meta: dict = field(default_factory=dict)


# Recognised date/timestamp column-name patterns.
_DATE_NAME_RE = re.compile(
    r"(date|time|day|week|month|year|created|updated|_at$)",
    re.IGNORECASE,
)
# Loose ISO-8601 / SQLite date-string detection.
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}(-\d{2})?")


def pick_chart(columns: list[str], rows: list[tuple]) -> ChartSpec:
    """Pick a chart spec for the given result."""
    if not columns:
        return ChartSpec(type="table")
    if not rows:
        return ChartSpec(type="table", meta={"empty": True})

    n_cols = len(columns)
    n_rows = len(rows)
    first_row = rows[0]

    # KPI: single scalar.
    if n_rows == 1 and n_cols == 1:
        return ChartSpec(type="kpi", label=columns[0], value=first_row[0])

    # 2+ columns with one numeric series.
    if n_cols >= 2:
        x_col = columns[0]
        x_val = first_row[0]
        numeric_cols = [
            c for c, v in zip(columns[1:], first_row[1:]) if _is_number(v)
        ]
        if _looks_like_date(x_col, x_val) and numeric_cols:
            return ChartSpec(
                type="line",
                x=x_col,
                y=numeric_cols[0] if len(numeric_cols) == 1 else numeric_cols,
            )
        if numeric_cols and not _is_number(x_val):
            return ChartSpec(type="bar", x=x_col, y=numeric_cols[0])
        if n_cols == 2 and _is_number(x_val) and _is_number(first_row[1]):
            return ChartSpec(type="scatter", x=columns[0], y=columns[1])

    return ChartSpec(type="table")


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _looks_like_date(col_name: str, value: Any) -> bool:
    if _DATE_NAME_RE.search(col_name):
        return True
    if isinstance(value, str) and _ISO_DATE_RE.match(value):
        return True
    return False
