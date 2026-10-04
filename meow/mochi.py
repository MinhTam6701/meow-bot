"""Mochi the cat: she eats what you don't spend.

Pure functions only; the bot stores state in mochi_state / mochi_log.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date
from typing import Optional

from .money import fmt

START_WEIGHT = 50
RETURN_WEIGHT = 20     # weight when she comes back from grandma's
RETURN_AFTER = 3       # on-budget days in a row while away
MAX_DELTA = 3

STAGES = [  # (min weight, name)
    (90, "Chonky King"),
    (70, "Chubby"),
    (40, "Healthy"),
    (20, "Thin"),
    (0, "Skinny"),
]

RESULTS = {  # result -> weight change
    "no_spend": 3,
    "half": 2,      # spent <= 50% of the bowl
    "within": 1,    # spent <= 100%
    "over": -1,     # 100-150%
    "splurge": -3,  # > 150%
    "silent": -2,   # nothing logged, reminder ignored
}


def bowl(everyday_budget_minor: int, day: date) -> int:
    """Today's food bowl: the everyday budget spread over the month's days."""
    return everyday_budget_minor // calendar.monthrange(day.year, day.month)[1]


def result_for(spent: int, bowl_minor: int, logged: bool) -> str:
    if not logged:
        return "silent"
    if spent <= 0:
        return "no_spend"
    if spent <= bowl_minor // 2:
        return "half"
    if spent <= bowl_minor:
        return "within"
    if spent <= bowl_minor * 3 // 2:
        return "over"
    return "splurge"


@dataclass
class DayScore:
    result: str
    delta: int
    weight: int
    away: bool
    came_back: bool = False
    left: bool = False


def score(weight: int, away: bool, result: str, recent_results: list[str]) -> DayScore:
    """Apply one day. `recent_results` are the previous days' results, newest last."""
    delta = max(-MAX_DELTA, min(MAX_DELTA, RESULTS[result]))
    if away:
        on_budget = [r for r in recent_results[-(RETURN_AFTER - 1):] + [result]]
        if len(on_budget) == RETURN_AFTER and all(RESULTS[r] > 0 for r in on_budget):
            return DayScore(result, RETURN_WEIGHT, RETURN_WEIGHT, False, came_back=True)
        return DayScore(result, 0, 0, True)
    new = max(0, min(100, weight + delta))
    if new == 0:
        return DayScore(result, new - weight, 0, True, left=True)
    return DayScore(result, new - weight, new, False)


def stage(weight: int) -> str:
    return next(name for floor, name in STAGES if weight >= floor)


def mood(spent_today: int, bowl_minor: int, no_spend_marked: bool) -> str:
    if no_spend_marked:
        return "😻"
    if spent_today == 0:
        return "😺"
    if spent_today <= bowl_minor:
        return "😸"
    if spent_today <= bowl_minor * 3 // 2:
        return "😿"
    return "🙀"


def bar(weight: int, width: int = 10) -> str:
    filled = round(weight * width / 100)
    return "▓" * filled + "░" * (width - filled)


def status_line(weight: int, away: bool, spent_today: int, bowl_minor: int, home: str,
                no_spend_marked: bool = False, accessory: str = "", streak: int = 0) -> str:
    """🐱 Mochi 62/100 ▓▓▓▓▓▓░░░░ Healthy · bowl S$12.30 left · 🔥 29"""
    fire = f" · 🔥 {streak}" if streak else ""
    if away:
        return f"🏡 Mochi is at grandma's. {RETURN_AFTER} on-budget days in a row bring her back.{fire}"
    left = bowl_minor - spent_today
    food = f"bowl {fmt(left, home)} left" if left >= 0 else f"bowl over by {fmt(-left, home)}"
    crown = "👑" if weight >= 90 else accessory
    return (f"{mood(spent_today, bowl_minor, no_spend_marked)}{crown} Mochi {weight}/100 {bar(weight)} "
            f"{stage(weight)} · {food}{fire}")


VERDICTS = {
    "no_spend": "😻 No-spend day! Mochi had a feast. +3",
    "half": "😸 Under half the bowl. Mochi is happy. +2",
    "within": "😺 Within the bowl. Mochi is fed. +1",
    "over": "😿 A bit over the bowl. Mochi is peckish. −1",
    "splurge": "🙀 Big splurge. Mochi went hungry. −3",
    "silent": "😾 Nothing logged yesterday. Mochi waited by her bowl. −2",
}


def verdict(s: DayScore) -> str:
    if s.came_back:
        return "🎉 Mochi is back from grandma's house!"
    if s.left:
        return "🏡 Mochi got too hungry and moved to grandma's house. 3 on-budget days in a row bring her back."
    if s.away:
        return "🏡 Mochi is still at grandma's. Keep going!"
    return VERDICTS[s.result]
