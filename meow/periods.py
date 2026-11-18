"""Budget months that start on any day from 1 to 28: the day after payday, if the user wants.

With start day 26, "the month" runs 26 Oct – 25 Nov. Day 1 is the calendar month. A period is
named by its first day everywhere (reports, the charts page, Mochi's bowl override).
Days 29–31 aren't offered so that every month has the start day.
"""
from __future__ import annotations

from datetime import date, timedelta

MIN_DAY, MAX_DAY = 1, 28


def start_day(user: dict) -> int:
    return user.get("month_start_day") or 1


def _shift(start: date, months: int) -> date:
    y, m = divmod(start.year * 12 + start.month - 1 + months, 12)
    return date(y, m + 1, start.day)


def bounds(day: date, sd: int = 1) -> tuple[date, date]:
    """The period containing `day`: (first day, first day of the next period)."""
    start = date(day.year, day.month, sd)
    if day.day < sd:
        start = _shift(start, -1)
    return start, _shift(start, 1)


def previous(start: date, sd: int = 1) -> date:
    """First day of the period before the one containing `start`."""
    return bounds(bounds(start, sd)[0] - timedelta(days=1), sd)[0]


def starting_in(year: int, month: int, sd: int = 1) -> date:
    """The period that starts in that calendar month (/report 2026-10 with day 26 → 26 Oct – 25 Nov)."""
    return date(year, month, sd)


def length(day: date, sd: int = 1) -> int:
    start, nxt = bounds(day, sd)
    return (nxt - start).days


def label(start: date, nxt: date) -> str:
    """"November 2026" for a calendar month, "26 Oct – 25 Nov 2026" otherwise."""
    last = nxt - timedelta(days=1)
    if start.day == 1 and last.month == start.month:
        return start.strftime("%B %Y")
    if start.year == last.year:
        return f"{start.day} {start:%b} – {last.day} {last:%b %Y}"
    return f"{start.day} {start:%b %Y} – {last.day} {last:%b %Y}"


def ordinal(n: int) -> str:
    return f"{n}{'th' if 11 <= n % 100 <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def describe(sd: int) -> str:
    return "the 1st (calendar months)" if sd == 1 else f"the {ordinal(sd)}"
