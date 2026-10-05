"""The monthly report: plain facts from SQL. The persona only adds one line of tone."""
from __future__ import annotations

from datetime import date, timedelta
from html import escape
from typing import Optional

from . import budgets as bud
from . import db, insights
from .money import fmt


def month_bounds(first: date) -> tuple[date, date]:
    nxt = (first + timedelta(days=32)).replace(day=1)
    return first, nxt


def previous_month(day: date) -> date:
    return (day.replace(day=1) - timedelta(days=1)).replace(day=1)


def _totals(conn, uid: int, first: date, nxt: date) -> dict:
    row = conn.execute(
        """select coalesce(-sum(amount_home) filter (where type = 'expense'), 0)::bigint as spent,
                  coalesce(sum(amount_home) filter (where type = 'income'), 0)::bigint as income,
                  count(*) filter (where amount_home is null and type in ('expense', 'income')) as unconverted
           from transactions where user_id = %s and occurred_on >= %s and occurred_on < %s""",
        (uid, first, nxt)).fetchone()
    return dict(row)


def _top_expenses(conn, uid: int, first: date, nxt: date, n: int = 3) -> list[dict]:
    return conn.execute(
        """select t.description, -t.amount_home as amount, t.occurred_on, c.emoji
           from live_transactions t left join categories c on c.id = t.category_id
           where t.user_id = %s and t.occurred_on >= %s and t.occurred_on < %s and t.type = 'expense'
             and t.amount_home is not null
           order by t.amount_home asc limit %s""",
        (uid, first, nxt, n)).fetchall()


def _habit(conn, uid: int, first: date, nxt: date) -> Optional[dict]:
    """The thing you paid for most often."""
    return conn.execute(
        """select lower(t.description) as what, count(*) as n, -sum(t.amount_home)::bigint as total
           from live_transactions t
           where t.user_id = %s and t.occurred_on >= %s and t.occurred_on < %s and t.type = 'expense'
             and t.source <> 'recurring' and t.amount_home is not null
           group by 1 having count(*) >= 3 order by 2 desc, 3 desc limit 1""",
        (uid, first, nxt)).fetchone()


def _priciest_day(conn, uid: int, first: date, nxt: date) -> Optional[dict]:
    return conn.execute(
        """select occurred_on as day, -sum(amount_home)::bigint as total from transactions
           where user_id = %s and occurred_on >= %s and occurred_on < %s and type = 'expense'
             and source <> 'recurring'
           group by 1 order by 2 desc limit 1""",
        (uid, first, nxt)).fetchone()


def build(conn, user: dict, first: date, persona_line: Optional[str] = None) -> str:
    uid, home = user["telegram_id"], user["home_currency"]
    first, nxt = month_bounds(first)
    prev_first, _ = month_bounds(previous_month(first))
    cur, prev = _totals(conn, uid, first, nxt), _totals(conn, uid, prev_first, first)
    cats = [r for r in db.month_by_category_home(conn, uid, first, nxt) if r["type"] == "expense"]
    prev_cats = {r["category"]: -(r["total"] or 0)
                 for r in db.month_by_category_home(conn, uid, prev_first, first) if r["type"] == "expense"}
    days_in_month = (nxt - first).days

    lines = [f"📊 <b>{first.strftime('%B %Y')}</b> · monthly report"]
    if persona_line:
        lines.append(f"<i>{escape(persona_line, quote=False)}</i>")

    spent, income = cur["spent"], cur["income"]
    change = ""
    if prev["spent"]:
        pct = (spent - prev["spent"]) * 100 // prev["spent"]
        change = f" ({'+' if pct >= 0 else ''}{pct}% vs {prev_first.strftime('%B')})"
    net = income - spent
    lines += ["", f"💸 Spent <b>{fmt(spent, home)}</b>{change}",
              f"💰 Income <b>{fmt(income, home)}</b>",
              f"{'📈' if net >= 0 else '📉'} Net <b>{'+' if net >= 0 else ''}{fmt(net, home)}</b>"
              + (f" · savings rate {net * 100 // income}%" if income > 0 and net > 0 else "")]

    if cats:
        lines += ["", "<b>Where it went</b>"]
        for r in cats:
            amt = -(r["total"] or 0)
            share = amt * 100 // spent if spent else 0
            before = prev_cats.get(r["category"])
            trend = ""
            if before:
                d = (amt - before) * 100 // before
                trend = f" {'▲' if d > 0 else '▼'}{abs(d)}%" if abs(d) >= 10 else ""
            lines.append(f"{r['emoji']} {escape(r['category'])} {fmt(amt, home)} · {share}%{trend}")

    top = _top_expenses(conn, uid, first, nxt)
    if top:
        lines += ["", "<b>Top 3</b>"]
        for i, t in enumerate(top, 1):
            lines.append(f"{i}. {t['emoji'] or '•'} {escape(t['description'] or '')} — {fmt(t['amount'], home)} "
                         f"({t['occurred_on'].strftime('%d %b')})")

    budgets = db.budgets(conn, uid)
    everyday = user.get("everyday_budget_minor")
    if budgets or everyday:
        spent_by_cat = db.month_spent_home(conn, uid, first, nxt)
        lines += ["", "<b>Budget scorecard</b>"]
        if everyday:
            everyday_spent = db.everyday_spent(conn, uid, first, nxt)
            ok = everyday_spent <= everyday
            lines.append(f"{'✅' if ok else '❌'} Everyday (no rent, bills, study) {fmt(everyday_spent, home)} / {fmt(everyday, home)} "
                         f"({bud.pct(everyday_spent, everyday)}%)")
        for b in budgets:
            s = spent_by_cat.get(b["category_id"], 0)
            ok = s <= b["limit_minor"]
            label = escape(b["name"]) if b["name"] else "Total"
            lines.append(f"{'✅' if ok else '❌'} {label} {fmt(s, home)} / {fmt(b['limit_minor'], home)} "
                         f"({bud.pct(s, b['limit_minor'])}%)")

    logged = len([d for d in db.logged_days(conn, uid, first) if d < nxt])
    facts = [f"📅 You logged on {logged} of {days_in_month} days."]
    habit = _habit(conn, uid, first, nxt)
    if habit:
        facts.append(f"🔁 Most frequent: “{escape(habit['what'])}” × {habit['n']} = {fmt(habit['total'], home)}.")
    day = _priciest_day(conn, uid, first, nxt)
    if day and day["total"]:
        facts.append(f"🔥 Priciest day: {day['day'].strftime('%a %d %b')} ({fmt(day['total'], home)}).")
    lines += ["", "<b>Fun facts</b>"] + facts

    patterns = insights.best_for_report(conn, user, nxt - timedelta(days=1))
    if patterns:
        lines += ["", "<b>Patterns</b>"] + patterns

    tip = _tip(cats, prev_cats, spent, everyday, home)
    if tip:
        lines += ["", f"💡 {tip}"]
    if cur["unconverted"]:
        lines.append(f"⏳ {cur['unconverted']} entries had no exchange rate and aren't in the totals.")
    return "\n".join(lines)


def _tip(cats: list[dict], prev_cats: dict, spent: int, everyday: Optional[int], home: str) -> Optional[str]:
    """One concrete suggestion: the everyday category that grew the most."""
    skip = {"Housing", "Phone", "Subscriptions & Fees", "Education"}
    best = None
    for r in cats:
        before = prev_cats.get(r["category"], 0)
        amt = -(r["total"] or 0)
        if r["category"] in skip or before <= 0:
            continue
        growth = amt - before
        if growth > 0 and (best is None or growth > best[1]):
            best = (r["category"], growth, amt)
    if best and best[1] >= 2000:  # at least S$20 more
        return (f"{escape(best[0])} grew by {fmt(best[1], home)} vs last month. "
                f"Cutting it back to last month's level is the quickest saving.")
    return None
