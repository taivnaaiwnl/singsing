"""Deterministic arithmetic over CSV documents.

Gemini reads CSVs as text and returns monthly figures. Python independently
re-computes monthly column sums from the same CSV so the VERIFY step can
check (and, if needed, correct) the model's arithmetic.
"""

from __future__ import annotations

import csv
import io
import re
from collections import defaultdict

# Bank exports can carry very long description cells or an unclosed quote; the
# default 128 KB field limit would otherwise raise and fail the whole document.
csv.field_size_limit(10_000_000)

_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_NUM_RE = re.compile(r"^[-+]?\d[\d,]*(\.\d+)?$")
_CURRENCY_RE = re.compile(r"(?i)(MNT|₮|тг\.?|\s)")

# Column-name keywords (English + Mongolian bank/payment exports).
_INFLOW_KEYS = ["credit", "кредит", "орлого", "inflow", "income", "deposit", "net", "sales", "amount", "gross"]
_OUTFLOW_KEYS = ["debit", "дебит", "зарлага", "withdrawal", "outflow", "expense"]
_NOT_A_FLOW = ["balance", "үлдэгдэл", "fee", "шимтгэл"]


def _to_number(cell: str) -> float | None:
    s = _CURRENCY_RE.sub("", (cell or "").strip())
    if not s or not _NUM_RE.match(s):
        return None
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


def _valid_month(month: str) -> bool:
    try:
        return 1 <= int(month[5:7]) <= 12
    except ValueError:
        return False


def monthly_totals_from_csv(text: str) -> dict:
    """Find the first table with a YYYY-MM-DD date column and sum numeric columns per month.

    Returns {"columns": [...], "months": {"YYYY-MM": {column: sum}}, "rows": {"YYYY-MM": "rows a-b"}}
    or an empty dict if no such table exists. A column that carries both signs (a single
    signed "Amount" column) additionally yields "<col> (credits)" (sum of positives) and
    "<col> (debits)" (absolute sum of negatives) so inflows are not netted against outflows.
    """
    try:
        rows = list(csv.reader(io.StringIO(text)))
    except csv.Error:
        return {}
    header: list[str] | None = None
    header_idx = -1
    for i, row in enumerate(rows):
        cells = [c.strip() for c in row]
        if len([c for c in cells if c]) >= 3 and i + 1 < len(rows):
            nxt = [c.strip() for c in rows[i + 1]]
            has_date = any(_DATE_RE.match(c) for c in nxt)
            has_number = any(_to_number(c) is not None for c in nxt if not _DATE_RE.match(c))
            if has_date and has_number and not any(_DATE_RE.match(c) for c in cells):
                header = cells
                header_idx = i
                break
    if header is None:
        return {}
    date_col = None
    totals: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    positives: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    negatives: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    row_ranges: dict[str, list[int]] = defaultdict(list)
    for line_no, row in enumerate(rows[header_idx + 1:], start=header_idx + 2):
        if not row or not any(c.strip() for c in row):
            break
        if date_col is None:
            for j, c in enumerate(row):
                if _DATE_RE.match(c.strip()):
                    date_col = j
                    break
            if date_col is None:
                break
        if date_col >= len(row) or not _DATE_RE.match(row[date_col].strip()):
            break
        month = row[date_col].strip()[:7]
        if not _valid_month(month):
            continue  # malformed date (month 00 or > 12): skip the row rather than poison the series
        row_ranges[month].append(line_no)
        for j, c in enumerate(row):
            if j == date_col or j >= len(header):
                continue
            v = _to_number(c)
            if v is not None:
                totals[month][header[j]] += v
                if v > 0:
                    positives[month][header[j]] += v
                elif v < 0:
                    negatives[month][header[j]] += -v
    if not totals:
        return {}
    # A column with both signs is a single signed amount column: split it into credits/debits.
    has_pos = {c for m in positives.values() for c, v in m.items() if v > 0}
    has_neg = {c for m in negatives.values() for c, v in m.items() if v > 0}
    for col in has_pos & has_neg:
        for month in totals:
            totals[month][f"{col} (credits)"] = positives[month].get(col, 0.0)
            totals[month][f"{col} (debits)"] = negatives[month].get(col, 0.0)
    numeric_cols = sorted({c for m in totals.values() for c in m})
    return {
        "columns": numeric_cols,
        "months": {m: dict(v) for m, v in sorted(totals.items())},
        "rows": {m: f"rows {min(r)}-{max(r)}" for m, r in row_ranges.items()},
    }


def _is_flow(low: str) -> bool:
    return not any(k in low for k in _NOT_A_FLOW)


def pick_inflow_column(columns: list[str]) -> str | None:
    """Return the column holding credits/inflows, or None when no header keyword matches.

    There is deliberately no "first numeric column" fallback: guessing picked the debit
    column for Mongolian headers, and a wrong inflow column silently replaces the model's
    correct figures downstream.
    """
    lowered = [(c, c.lower()) for c in columns]
    for p in _INFLOW_KEYS:
        for c, low in lowered:
            if p in low and _is_flow(low) and not any(k in low for k in _OUTFLOW_KEYS):
                return c
    return None


def pick_outflow_column(columns: list[str]) -> str | None:
    for c in columns:
        low = c.lower()
        if any(k in low for k in _OUTFLOW_KEYS) and _is_flow(low):
            return c
    return None
