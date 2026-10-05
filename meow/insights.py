"""Spending patterns, found by plain statistics. Claude only phrases them (in the recap's persona line).

Used by the Sunday recap (/recap) and the monthly report.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date, timedelta
from html import escape
from typing import Optional

from . import db
from .money import fmt

MIN_PATTERN = 2000  # S$20 in minor units: smaller differences aren't worth a comment


@dataclass
class Insight:
    text: str        # HTML, numbers already formatted
    weight: int      # bigger = more worth saying (used to pick the best one)


def week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())  # Monday


def weekend_vs_weekday(daily: dict[date, int], start: date, end: date, home: str) -> Optional[Insight]:
    """Average everyday spending per weekend day vs per weekday, over start..end (excl.)."""
    days = [start + timedelta(days=i) for i in range((end - start).days)]
    we = [daily.get(d, 0) for d in days if d.weekday() >= 5]
    wd = [daily.get(d, 0) for d in days if d.weekday() < 5]
    if len(we) < 4 or len(wd) < 10:
        return None
    a_we, a_wd = sum(we) / len(we), sum(wd) / len(wd)
    if a_wd <= 0 or a_we <= 0:
        return None
    pct = round((a_we - a_wd) * 100 / a_wd)
    if abs(pct) < 25 or abs(a_we - a_wd) < MIN_PATTERN / 4:
        return None
    if pct > 0:
        return Insight(f"📅 Weekends cost you <b>{pct}% more</b> a day ({fmt(round(a_we), home)} vs "
                       f"{fmt(round(a_wd), home)} on weekdays), over the last 4 weeks.", min(pct, 300))
    return Insight(f"📅 You spend <b>{-pct}% less</b> on weekend days ({fmt(round(a_we), home)} vs "
                   f"{fmt(round(a_wd), home)} on weekdays).", min(-pct, 300) // 2)


def rising_categories(weekly: list[dict[str, int]], home: str) -> list[Insight]:
    """Categories that went up three weeks in a row (oldest week first in `weekly`)."""
    out = []
    if len(weekly) < 3:
        return out
    a, b, c = weekly[-3:]
    for cat, now in c.items():
        if a.get(cat, 0) < b.get(cat, 0) < now and now - a.get(cat, 0) >= MIN_PATTERN:
            out.append(Insight(f"📈 <b>{escape(cat)}</b> went up 3 weeks in a row: "
                               f"{fmt(a.get(cat, 0), home)} → {fmt(b.get(cat, 0), home)} → {fmt(now, home)}.",
                               100 + (now - a.get(cat, 0)) // 100))
    return out


def budget_pace(spent: int, limit: int, today: date, name: str, home: str) -> Optional[Insight]:
    """'At this pace you'll hit your Shopping budget on the 22nd.'"""
    first = today.replace(day=1)
    days_in_month = ((first + timedelta(days=32)).replace(day=1) - first).days
    elapsed = today.day
    if spent <= 0 or elapsed < 3:
        return None
    if spent >= limit:
        return Insight(f"🚨 <b>{escape(name)}</b> is already over budget: {fmt(spent, home)} of {fmt(limit, home)}.", 250)
    per_day = spent / elapsed
    hit_day = math.ceil(limit / per_day)
    if hit_day > days_in_month:
        return None
    hit = first + timedelta(days=hit_day - 1)
    return Insight(f"⏳ At this pace you'll hit your <b>{escape(name)}</b> budget on the "
                   f"<b>{hit.day}{_suffix(hit.day)}</b> ({fmt(spent, home)} of {fmt(limit, home)} so far).",
                   200 - (hit_day - elapsed))


def _suffix(n: int) -> str:
    return "th" if 11 <= n % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


def find(conn, user: dict, today: date) -> list[Insight]:
    """Every pattern worth mentioning as of today, best first."""
    uid, home = user["telegram_id"], user["home_currency"]
    found: list[Insight] = []
    four_weeks_ago = today - timedelta(days=28)
    daily = db.daily_spend(conn, uid, four_weeks_ago, today + timedelta(days=1))
    w = weekend_vs_weekday(daily, four_weeks_ago, today + timedelta(days=1), home)
    if w:
        found.append(w)

    start = week_start(today)
    weekly = []
    for k in (2, 1, 0):
        a = start - timedelta(weeks=k)
        rows = db.month_by_category_home(conn, uid, a, a + timedelta(days=7))
        weekly.append({r["category"]: -(r["total"] or 0) for r in rows if r["type"] == "expense"})
    found += rising_categories(weekly, home)

    first = today.replace(day=1)
    nxt = (first + timedelta(days=32)).replace(day=1)
    spent_by_cat = db.month_spent_home(conn, uid, first, nxt)
    for b in db.budgets(conn, uid):
        p = budget_pace(spent_by_cat.get(b["category_id"], 0), b["limit_minor"], today, b["name"] or "Total", home)
        if p:
            found.append(p)
    everyday = user.get("everyday_budget_minor")
    if everyday:
        p = budget_pace(db.everyday_spent(conn, uid, first, nxt), everyday, today, "everyday", home)
        if p:
            found.append(p)
    found.sort(key=lambda i: -i.weight)
    return found


def recap(conn, user: dict, today: date) -> tuple[str, list[str]]:
    """The Sunday recap for the week ending today: (HTML body, plain facts for the persona line)."""
    uid, home = user["telegram_id"], user["home_currency"]
    start = week_start(today)
    end = start + timedelta(days=7)
    this_week = db.daily_spend(conn, uid, start, end, everyday=False)
    everyday = db.daily_spend(conn, uid, start, end)
    total, every = sum(this_week.values()), sum(everyday.values())
    prev = sum(db.daily_spend(conn, uid, start - timedelta(days=7), start, everyday=False).values())
    avg4 = sum(db.daily_spend(conn, uid, start - timedelta(days=28), start, everyday=False).values()) // 4

    lines = [f"🗓 <b>Your week</b> · {start.strftime('%d %b')} – {(end - timedelta(days=1)).strftime('%d %b')}", ""]
    change = ""
    if prev:
        pct = round((total - prev) * 100 / prev)
        change = f" ({'+' if pct >= 0 else ''}{pct}% vs last week)"
    lines.append(f"💸 Spent <b>{fmt(total, home)}</b>{change}")
    if every != total:
        lines.append(f"🛒 Everyday spending {fmt(every, home)} (without rent, bills and study)")
    if avg4:
        lines.append(f"📊 Your 4-week average is {fmt(avg4, home)} a week")
    facts = [f"Spent {fmt(total, home)} this week{change}; 4-week average {fmt(avg4, home)}."]

    rows = [r for r in db.month_by_category_home(conn, uid, start, end) if r["type"] == "expense" and r["total"]]
    rows.sort(key=lambda r: r["total"])
    if rows:
        lines += ["", "<b>Where it went</b>"]
        lines += [f"{r['emoji']} {escape(r['category'])} {fmt(-r['total'], home)}" for r in rows[:4]]
        facts.append("Top: " + ", ".join(f"{r['category']} {fmt(-r['total'], home)}" for r in rows[:3]) + ".")

    days_logged = len([d for d in db.logged_days(conn, uid, start) if d < end])
    good = [h for h in db.mochi_history(conn, uid, 7)
            if start <= h["day"] < end and h["result"] in ("no_spend", "half", "within")]
    lines += ["", f"📅 Logged on {days_logged} of 7 days"
              + (f" · Mochi had {len(good)} good days" if user.get("everyday_budget_minor") else "")]

    found = find(conn, user, today)
    if found:
        lines += ["", "<b>Patterns</b>"] + [i.text for i in found[:3]]
        facts += [_plain(i.text) for i in found[:3]]

    soon = [(s["name"], s["amount_minor"], s["currency"], s["next_due"]) for s in db.subscriptions(conn, uid)]
    soon += [(r["description"], r["amount_minor"], r["currency"], r["next_run"]) for r in db.recurring(conn, uid)
             if r["kind"] == "subscription"]
    soon = sorted((x for x in soon if today < x[3] <= today + timedelta(days=7)), key=lambda x: x[3])
    if soon:
        lines += ["", "<b>Subscriptions renewing this week</b>"]
        lines += [f"🔁 {escape(n)} {fmt(a, c)} on {d.strftime('%a %d %b')}" for n, a, c, d in soon]
        facts.append("Renewing soon: " + ", ".join(n for n, *_ in soon) + ".")
    return "\n".join(lines), facts


def best_for_report(conn, user: dict, last_day_of_month: date) -> list[str]:
    """One or two patterns for the monthly report (budget pace is left out: the month is over)."""
    return [i.text for i in find(conn, user, last_day_of_month) if not i.text.startswith(("⏳", "🚨"))][:2]


def _plain(html: str) -> str:
    return re.sub(r"<[^>]+>", "", html).replace("&amp;", "&")

