"""Number guardrail for LLM-written text.

The EXPLAIN and WRITE prompts forbid inventing figures. This module enforces
it: every number found in generated text must match a number that exists in
the inputs the model was given (within a small tolerance, allowing for
rounding like "₮6.2M" for 6,200,000 or "18%" for 0.18).
"""

from __future__ import annotations

import re
from typing import Any, Iterable

_NUM_RE = re.compile(
    r"(?<![\w.])(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*(?P<unit>million|mln|mn\b|m\b|bn\b|billion|thousand|k\b|сая|мянга|тэрбум|%)?",
    re.IGNORECASE,
)

_UNIT_MULT = {
    "million": 1e6, "mln": 1e6, "mn": 1e6, "m": 1e6, "сая": 1e6,
    "billion": 1e9, "bn": 1e9, "тэрбум": 1e9,
    "thousand": 1e3, "k": 1e3, "мянга": 1e3,
}


def _walk(obj: Any) -> Iterable[Any]:
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, (list, tuple, set)):
        for v in obj:
            yield from _walk(v)
    else:
        yield obj


def allowed_numbers(inputs: Any) -> set[float]:
    """Collect every number present in the inputs (including inside strings)."""
    allowed: set[float] = set()
    for leaf in _walk(inputs):
        if isinstance(leaf, bool):
            continue
        if isinstance(leaf, (int, float)):
            v = float(leaf)
            allowed.add(v)
            allowed.add(round(v * 100, 6))  # ratio -> percent rendering
            if abs(v) >= 1000:
                allowed.add(round(v / 1e6, 2))  # "6.2M" style
        elif isinstance(leaf, str):
            for m in _NUM_RE.finditer(leaf):
                try:
                    val = float(m.group("num").replace(",", ""))
                except ValueError:
                    continue
                allowed.add(val)
                allowed.add(round(val * 100, 6))
    return allowed


def _matches(value: float, allowed: set[float], tol: float = 0.02) -> bool:
    if value in allowed:
        return True
    for a in allowed:
        if a == 0:
            continue
        if abs(value - a) <= max(0.5, abs(a) * tol):
            return True
    return False


def check_numbers(text: str, allowed: set[float]) -> tuple[int, list[str]]:
    """Return (numbers_checked, violations) for a block of generated text."""
    checked = 0
    violations: list[str] = []
    for m in _NUM_RE.finditer(text or ""):
        raw = m.group("num")
        unit = (m.group("unit") or "").lower()
        try:
            val = float(raw.replace(",", ""))
        except ValueError:
            continue
        checked += 1
        if unit in _UNIT_MULT:
            candidates = [val * _UNIT_MULT[unit], val]
        elif unit == "%":
            candidates = [val, val / 100.0]
        else:
            candidates = [val]
        # Small integers (counts such as "4 months", "2 sources", list numbering) are fine.
        if val < 40 and float(val).is_integer() and unit == "":
            continue
        if not any(_matches(c, allowed) for c in candidates):
            violations.append(m.group(0).strip())
    return checked, violations


def check_payload(payload: Any, allowed: set[float]) -> tuple[int, list[str]]:
    """Check every string inside a dict/list payload."""
    checked = 0
    violations: list[str] = []
    for leaf in _walk(payload):
        if isinstance(leaf, str):
            c, v = check_numbers(leaf, allowed)
            checked += c
            violations.extend(v)
    return checked, sorted(set(violations))
