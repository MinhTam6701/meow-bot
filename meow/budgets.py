"""Small pure helpers for budgets."""
from __future__ import annotations

from typing import Optional

WARN_AT = 80  # percent


def pct(spent: int, limit: int) -> int:
    return spent * 100 // limit if limit else 0


def crossing(before: int, after: int, limit: int) -> Optional[str]:
    """'over' or 'warn' if this entry pushed spending past 100% or 80% of the limit."""
    if before < limit <= after:
        return "over"
    warn = limit * WARN_AT / 100
    if before < warn <= after:
        return "warn"
    return None


def bar(spent: int, limit: int, width: int = 10) -> str:
    filled = min(width, max(0, round(spent * width / limit))) if limit else 0
    return "▓" * filled + "░" * (width - filled)
