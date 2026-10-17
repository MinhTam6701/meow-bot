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
    """The biggest everyday entries (db.EVERYDAY: no bills, auto-logged bills or #planned buys)."""
    return conn.execute(
        f"""select t.description, -t.amount_home as amount, t.occurred_on, c.emoji
            from live_transactions t left join categories c on c.id = t.category_id
            where t.user_id = %s and t.occurred_on >= %s and t.occurred_on < %s and t.type = 'expense'
              and t.amount_home is not null and {db.EVERYDAY}
            order by t.amount_home asc limit %s""",
        [uid, first, nxt, *db.everyday_args(), n]).fetchall()


def build(conn, user: dict, first: date, persona_line: Optional[str] = None,
          today: Optional[date] = None) -> str:
    uid, home = user["telegram_id"], user["home_currency"]
    first, nxt = month_bounds(first)
    prev_first, _ = month_bounds(previous_month(first))
    cur, prev = _totals(conn, uid, first, nxt), _totals(conn, uid, prev_first, first)
    cats = [r for r in db.month_by_category_home(conn, uid, first, nxt) if r["type"] == "expense"]
    prev_cats = {r["category"]: -(r["total"] or 0)
                 for r in db.month_by_category_home(conn, uid, prev_first, first) if r["type"] == "expense"}

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

    from . import report_data  # it builds on this module

    data = report_data.build(conn, user, "month", first, today or date.today())
    lines += ["", "<b>Fun facts</b>"] + [escape(f, quote=False) for f in data["facts"]]

    patterns = insights.best_for_report(conn, user, nxt - timedelta(days=1))
    if patterns:
        lines += ["", "<b>Patterns</b>"] + patterns

    if data["tip"]:
        lines += ["", escape(data["tip"], quote=False)]
    if cur["unconverted"]:
        lines.append(f"⏳ {cur['unconverted']} entries had no exchange rate and aren't in the totals.")
    return "\n".join(lines)
