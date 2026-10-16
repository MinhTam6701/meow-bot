"""A week or a month as numbers: what the chart page, the picture in the chat and the tests read.

Same queries as the text reports (report.py, insights.py), so the pie and the text can't disagree.
Amounts are integer minor units of the home currency; `*_text` fields are already formatted.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from . import db, insights, report
from .money import fmt, minor_digits

KINDS = ("week", "month")
PIE_SLICES = 5  # biggest categories; the rest is "Other"


def bounds(kind: str, day: date) -> tuple[date, date]:
    """The period containing `day`: (first day, first day of the next period)."""
    if kind == "week":
        start = insights.week_start(day)
        return start, start + timedelta(days=7)
    return report.month_bounds(day.replace(day=1))


def previous_start(kind: str, start: date) -> date:
    return start - timedelta(days=7) if kind == "week" else report.previous_month(start)


def label(kind: str, start: date, end: date) -> str:
    if kind == "month":
        return start.strftime("%B %Y")
    last = end - timedelta(days=1)
    if start.month == last.month:
        return f"{start.day} – {last.day} {last.strftime('%b %Y')}"
    return f"{start.day} {start.strftime('%b')} – {last.day} {last.strftime('%b %Y')}"


def first_entry_day(conn, uid: int) -> Optional[date]:
    row = conn.execute("select min(occurred_on) as d from transactions where user_id = %s", (uid,)).fetchone()
    return row["d"]


def _entries(conn, uid: int, start: date, end: date) -> list[dict]:
    return conn.execute(
        """select t.occurred_on, t.type, coalesce(t.description, '') as description,
                  coalesce(c.name, 'Uncategorised') as category, w.name as wallet,
                  t.amount_minor, t.currency, t.amount_home
           from live_transactions t
           left join categories c on c.id = t.category_id
           left join wallets w on w.id = t.wallet_id
           where t.user_id = %s and t.occurred_on >= %s and t.occurred_on < %s
             and t.type in ('expense', 'income') and t.amount_home is not null
           order by t.occurred_on desc, t.id desc limit 600""",
        (uid, start, end)).fetchall()


def pie(categories: list[dict], include_bills: bool = False, home: str = "SGD") -> list[dict]:
    """Slices for the chart: the biggest categories, then everything else as "Other"."""
    rows = [c for c in categories if c["amount"] > 0 and (include_bills or not c["bill"])]
    rows.sort(key=lambda c: -c["amount"])
    total = sum(c["amount"] for c in rows)
    head, tail = rows[:PIE_SLICES], rows[PIE_SLICES:]
    if len(tail) == 1:  # "Other" with one thing in it is just that thing
        head, tail = rows, []
    slices = [{"name": c["name"], "emoji": c["emoji"], "amount": c["amount"]} for c in head]
    if tail:
        slices.append({"name": "Other", "emoji": "", "amount": sum(c["amount"] for c in tail)})
    for s in slices:
        s["share"] = round(s["amount"] * 100 / total) if total else 0
        s["amount_text"] = fmt(s["amount"], home)
        s["members"] = [c["name"] for c in (tail if s["name"] == "Other" and tail else [s])]
    return slices


def build(conn, user: dict, kind: str, day: date, today: date) -> dict:
    """Everything the page and the picture show for the week or month containing `day`."""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    uid, home = user["telegram_id"], user["home_currency"]
    start, end = bounds(kind, min(day, today))
    current = start <= today < end
    # The current period is compared with the same number of days of the one before.
    days = ((today + timedelta(days=1)) if current else end) - start
    prev_start = previous_start(kind, start)
    _, prev_full_end = bounds(kind, prev_start)
    prev_end = min(prev_start + days, prev_full_end)

    totals = report._totals(conn, uid, start, end)
    prev = report._totals(conn, uid, prev_start, prev_end)
    spent, income = totals["spent"], totals["income"]
    rows = db.month_by_category_home(conn, uid, start, end)
    prev_by_cat = {r["category"]: -(r["total"] or 0)
                   for r in db.month_by_category_home(conn, uid, prev_start, prev_end) if r["type"] == "expense"}

    categories = [{"name": r["category"], "emoji": r["emoji"], "amount": -(r["total"] or 0),
                   "amount_text": fmt(-(r["total"] or 0), home),
                   "previous": prev_by_cat.get(r["category"], 0), "bill": r["category"] in db.FIXED_CATEGORIES}
                  for r in rows if r["type"] == "expense" and r["total"]]
    income_rows = [{"name": r["category"], "emoji": r["emoji"], "amount": r["total"],
                    "amount_text": fmt(r["total"], home)}
                   for r in rows if r["type"] == "income" and r["total"]]
    bills = sum(c["amount"] for c in categories if c["bill"])
    net = income - spent

    budgets = []
    if kind == "month":
        everyday = user.get("everyday_budget_minor")
        if everyday:
            budgets.append({"name": "Everyday", "spent": db.everyday_spent(conn, uid, start, end), "limit": everyday})
        spent_by_cat = db.month_spent_home(conn, uid, start, end)
        for b in db.budgets(conn, uid):
            budgets.append({"name": b["name"] or "Total", "spent": spent_by_cat.get(b["category_id"], 0),
                            "limit": b["limit_minor"]})

    if current:
        patterns = [insights._plain(i.text) for i in insights.find(conn, user, today)[:3]]
    elif kind == "month":
        patterns = [insights._plain(t) for t in insights.best_for_report(conn, user, end - timedelta(days=1))]
    else:
        patterns = []

    first = first_entry_day(conn, uid)
    has_prev = first is not None and first < start
    next_start = end if end <= today else None
    logged = len([d for d in db.logged_days(conn, uid, start) if d < end])
    compared_with = ("the same days last " + kind) if current else ("last " + kind)

    # Fun facts and one tip: the same rules as the monthly text report, for any week or month.
    facts = [f"📅 Logged on {logged} of {days.days} day{'s' if days.days != 1 else ''}"]
    habit = report._habit(conn, uid, start, end)
    if habit:
        facts.append(f"🔁 Most frequent: “{habit['what']}” × {habit['n']} = {fmt(habit['total'], home)}")
    priciest = report._priciest_day(conn, uid, start, end)
    if priciest and priciest["total"]:
        facts.append(f"🔥 Priciest day: {priciest['day'].strftime('%a %d %b')} ({fmt(priciest['total'], home)})")
    tip = report._tip([{"category": c["name"], "total": -c["amount"]} for c in categories],
                      {c["name"]: c["previous"] for c in categories}, spent, user.get("everyday_budget_minor"),
                      home, compared_with=compared_with, min_growth=2000 if kind == "month" else 1000)

    entries = [{"day": e["occurred_on"].isoformat(), "type": e["type"], "description": e["description"],
                "category": e["category"], "wallet": e["wallet"] or "",
                "amount": abs(e["amount_home"]), "amount_text": fmt(abs(e["amount_home"]), home),
                "original_text": fmt(abs(e["amount_minor"]), e["currency"]) if e["currency"] != home else ""}
               for e in _entries(conn, uid, start, end)]
    top = [{"description": t["description"] or "", "day": t["occurred_on"].isoformat(),
            "amount": t["amount"], "amount_text": fmt(t["amount"], home)}
           for t in report._top_expenses(conn, uid, start, end)]

    change = round((spent - prev["spent"]) * 100 / prev["spent"]) if prev["spent"] else None
    return {
        "kind": kind, "label": label(kind, start, end), "start": start.isoformat(),
        "end": (end - timedelta(days=1)).isoformat(), "current": current,
        "days": days.days, "days_total": (end - start).days, "days_logged": logged,
        "previous_start": prev_start.isoformat() if has_prev else None,
        "next_start": next_start.isoformat() if next_start else None,
        "home": home, "decimals": minor_digits(home),
        "spent": spent, "income": income, "net": net, "bills": bills, "everyday": spent - bills,
        "spent_text": fmt(spent, home), "income_text": fmt(income, home),
        "net_text": ("+" if net >= 0 else "") + fmt(net, home), "bills_text": fmt(bills, home),
        "previous_spent": prev["spent"], "change_pct": change,
        "compared_with": compared_with,
        # Salary arrives once a month, so a weekly savings rate swings wildly: months only.
        "savings_rate": (net * 100 // income) if kind == "month" and income > 0 else None,
        "categories": categories, "income_categories": income_rows,
        "pie": pie(categories, home=home), "pie_with_bills": pie(categories, include_bills=True, home=home),
        "everyday_text": fmt(spent - bills, home),
        "budgets": [dict(b, spent_text=fmt(b["spent"], home), limit_text=fmt(b["limit"], home),
                         pct=b["spent"] * 100 // b["limit"]) for b in budgets],
        "top": top, "patterns": patterns, "entries": entries,
        "facts": facts, "tip": insights._plain(f"💡 {tip}") if tip else None,
        "unconverted": totals["unconverted"],
    }
