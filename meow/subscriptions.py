"""Spotting subscriptions: the same thing, a similar amount (±5%), at a regular interval, at least twice.

Pure functions; the bot stores what you confirm in the subscriptions table.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

from .text import normalize

# interval -> (shortest gap, longest gap in days, how long after the last charge it still counts as active)
INTERVALS = {
    "weekly": (6, 8, 10),
    "monthly": (26, 35, 45),
    "quarterly": (84, 98, 110),
    "yearly": (350, 380, 400),
}
PER_MONTH = {"weekly": 52 / 12, "monthly": 1, "quarterly": 1 / 3, "yearly": 1 / 12}
LABEL = {"weekly": "week", "monthly": "month", "quarterly": "quarter", "yearly": "year"}
AMOUNT_TOLERANCE = 0.05
# Everyday spending repeats by habit, not by contract.
NOT_SUBSCRIPTIONS = {"Food & Drinks", "Groceries", "Transport"}
_MONTH_WORDS = {"thang", "month", "monthly", "jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "sept",
                "oct", "nov", "dec", "january", "february", "march", "april", "june", "july", "august",
                "september", "october", "november", "december", "t"}


def key_of(description: str) -> str:
    """'Tiền điện thoại tháng 9' and 'tiền điện thoại tháng 10' -> 'tien dien thoai'."""
    words = [normalize(w) for w in re.findall(r"[^\W\d_]+", description or "")]
    return " ".join(w for w in words if w and w not in _MONTH_WORDS)


def add_interval(day: date, interval: str, times: int = 1) -> date:
    if interval == "weekly":
        return day + timedelta(days=7 * times)
    months = {"monthly": 1, "quarterly": 3, "yearly": 12}[interval] * times
    m = day.month - 1 + months
    year, month = day.year + m // 12, m % 12 + 1
    for d in (day.day, 30, 29, 28):  # 31 Jan + 1 month = 28/29 Feb
        try:
            return date(year, month, d)
        except ValueError:
            continue
    raise ValueError(day)


def next_on_or_after(last: date, interval: str, today: date) -> date:
    """The first renewal after `last` that isn't before today. Counted from `last`, so 31 Jan stays 31st."""
    n = 1
    due = add_interval(last, interval)
    while due < today:
        n += 1
        due = add_interval(last, interval, n)
    return due


def similar(a: int, b: int) -> bool:
    return abs(a - b) <= AMOUNT_TOLERANCE * max(abs(a), abs(b))


@dataclass
class Charge:
    day: date
    amount: int          # positive, minor units of `currency`
    currency: str
    description: str
    category: Optional[str] = None
    wallet_id: Optional[int] = None
    category_id: Optional[int] = None


@dataclass
class Candidate:
    key: str
    name: str             # the latest description, as the user wrote it
    currency: str
    amount: int           # the latest amount
    interval: str
    charges: list[Charge]  # oldest first
    wallet_id: Optional[int] = None
    category_id: Optional[int] = None

    @property
    def last(self) -> date:
        return self.charges[-1].day

    def next_due_from(self, today: date) -> date:
        """The next renewal on or after today (a charge may be a little late this cycle)."""
        return next_on_or_after(self.last, self.interval, today)


def classify(gaps: list[int]) -> Optional[str]:
    for name, (lo, hi, _) in INTERVALS.items():
        if gaps and all(lo <= g <= hi for g in gaps):
            return name
    return None


def detect(charges: list[Charge], today: date) -> list[Candidate]:
    """Group charges by key and currency; keep groups that repeat regularly and are still active."""
    groups: dict[tuple[str, str], list[Charge]] = {}
    for c in charges:
        if c.category in NOT_SUBSCRIPTIONS:
            continue
        k = key_of(c.description)
        if k:
            groups.setdefault((k, c.currency), []).append(c)

    found = []
    for (k, cur), items in groups.items():
        items.sort(key=lambda c: c.day)
        latest = items[-1]
        # Walk back from the latest charge while the amount stays within ±5% and the gaps look regular.
        chain = [latest]
        for c in reversed(items[:-1]):
            if c.day == chain[-1].day or not similar(c.amount, latest.amount):
                continue
            gaps = [(chain[i].day - chain[i + 1].day).days for i in range(len(chain) - 1)]
            gap = (chain[-1].day - c.day).days
            if classify(gaps + [gap]) is None:
                continue  # an extra charge in between (a one-off at the same price): skip it, keep looking
            chain.append(c)
        if len(chain) < 2:
            continue
        chain.reverse()
        gaps = [(chain[i + 1].day - chain[i].day).days for i in range(len(chain) - 1)]
        interval = classify(gaps)
        if interval is None or (interval == "weekly" and len(chain) < 3):
            continue
        if (today - latest.day).days > INTERVALS[interval][2]:
            continue  # stopped: probably cancelled already
        found.append(Candidate(key=k, name=latest.description, currency=cur, amount=latest.amount,
                               interval=interval, charges=chain, wallet_id=latest.wallet_id,
                               category_id=latest.category_id))
    found.sort(key=lambda c: (-len(c.charges), c.key))
    return found


def per_month(amount: int, interval: str) -> int:
    return round(amount * PER_MONTH[interval])
