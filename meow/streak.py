"""Logging streak.

A day counts if you logged any expense or income, sent a day total, or tapped "No spend today".
One missed day can be bridged by a freeze: at most one per week (Mon-Sun), and only if you logged
on at least 5 of the 7 days before it. Today never breaks the streak until it's over.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

MILESTONES = (3, 7, 14, 30, 100)
FREEZE_NEEDS = 5  # logged days out of the previous 7


@dataclass
class Streak:
    current: int
    logged_today: bool
    freezes_used: list[date] = field(default_factory=list)
    best: int = 0

    @property
    def next_milestone(self) -> int | None:
        return next((m for m in MILESTONES if m > self.current), None)


def compute(logged: set[date], today: date) -> Streak:
    logged_today = today in logged
    day = today if logged_today else today - timedelta(days=1)
    count, used, used_weeks = 0, [], set()
    while True:
        if day in logged:
            count += 1
        else:
            week = day.isocalendar()[:2]
            recent = sum(1 for i in range(1, 8) if day - timedelta(days=i) in logged)
            # bridge a single missed day if the run continues before it
            if (count > 0 and week not in used_weeks and recent >= FREEZE_NEEDS
                    and (day - timedelta(days=1)) in logged):
                used.append(day)
                used_weeks.add(week)
            else:
                break
        day -= timedelta(days=1)
    return Streak(current=count, logged_today=logged_today, freezes_used=used, best=max(count, best_run(logged)))


def best_run(logged: set[date]) -> int:
    best = run = 0
    prev = None
    for d in sorted(logged):
        run = run + 1 if prev and d - prev == timedelta(days=1) else 1
        best, prev = max(best, run), d
    return best


def milestone_reached(before: int, after: int) -> int | None:
    return next((m for m in MILESTONES if before < m <= after), None)


def accessory(streak: int) -> str:
    if streak >= 100:
        return "👑"
    if streak >= 30:
        return "🧣"
    if streak >= 7:
        return "🔔"
    return ""
