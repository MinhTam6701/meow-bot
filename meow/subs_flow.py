"""Subscriptions: suggest, track, remind before renewals, flag price changes, check in quarterly.

Runs once a day from the scheduler (from 10:00 local). Everything you're asked has buttons.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from decimal import Decimal
from html import escape
from typing import Optional
from zoneinfo import ZoneInfo

from . import db, fx
from . import subscriptions as subs
from .cards import btn
from .money import fmt, parse_amount_token, to_minor

log = logging.getLogger(__name__)

DAILY_HOUR = 10          # local time the daily subscription job runs
REMIND_DAYS_BEFORE = 2
CHECK_EVERY_DAYS = 90    # "still using it?"
MAX_SUGGESTIONS_PER_DAY = 2
LOOKBACK_DAYS = 800      # two years, so yearly subscriptions can be seen twice
GRACE_DAYS = {"weekly": 2, "monthly": 7, "quarterly": 14, "yearly": 30}


def charges_from(rows: list[dict]) -> list[subs.Charge]:
    return [subs.Charge(day=r["occurred_on"], amount=r["amount"], currency=r["currency"],
                        description=r["description"] or "", category=r["category"],
                        wallet_id=r["wallet_id"], category_id=r["category_id"]) for r in rows]


class SubscriptionFlow:
    """Mixed into Bot."""

    # ------------------------------------------------------------------ daily job
    def maybe_daily_subscriptions(self, conn, user: dict, now: datetime) -> bool:
        local = now.astimezone(ZoneInfo(user["timezone"]))
        if local.hour < DAILY_HOUR:
            return False
        if not db.claim_job(conn, f"subs:{user['telegram_id']}:{local.date()}"):
            return False
        self.run_subscriptions(conn, user, local.date())
        return True

    def run_subscriptions(self, conn, user: dict, today: date, max_suggestions: int = MAX_SUGGESTIONS_PER_DAY) -> int:
        """Update tracked subscriptions, send reminders, then suggest new ones. Returns suggestions sent."""
        uid = user["telegram_id"]
        charges = charges_from(db.expense_charges(conn, uid, today - timedelta(days=LOOKBACK_DAYS)))
        notes = self.track_subscriptions(conn, uid, charges, today)
        if notes:
            self.send_quietly(uid, "🔁 <b>Subscriptions</b>\n" + "\n".join(notes))
        self.quarterly_check(conn, user, today)
        return self.suggest_subscriptions(conn, user, charges, today, max_suggestions)

    def track_subscriptions(self, conn, uid: int, charges: list[subs.Charge], today: date) -> list[str]:
        notes = []
        for s in db.subscriptions(conn, uid):
            new = [c for c in charges if c.currency == s["currency"] and subs.key_of(c.description) == s["key"]
                   and c.day > s["last_charge_on"]]
            if new:
                latest = max(new, key=lambda c: c.day)
                fields = {"last_charge_on": latest.day, "next_due": subs.add_interval(latest.day, s["interval"])}
                if latest.amount != s["amount_minor"]:
                    word = "went up" if latest.amount > s["amount_minor"] else "went down"
                    notes.append(f"{'⚠️' if latest.amount > s['amount_minor'] else '📉'} <b>{escape(s['name'])}</b> {word} "
                                 f"from {fmt(s['amount_minor'], s['currency'])} to {fmt(latest.amount, s['currency'])}.")
                    fields["amount_minor"] = latest.amount
                db.update_subscription(conn, s["id"], **fields)
                s = {**s, **fields}
            elif today > s["next_due"] + timedelta(days=GRACE_DAYS[s["interval"]]):
                due = subs.next_on_or_after(s["last_charge_on"], s["interval"], today)  # no charge seen: move on
                db.update_subscription(conn, s["id"], next_due=due)
                s = {**s, "next_due": due}
            days = (s["next_due"] - today).days
            if 0 <= days <= REMIND_DAYS_BEFORE and s["reminded_for"] != s["next_due"]:
                when = "today" if days == 0 else ("tomorrow" if days == 1 else f"in {days} days")
                notes.append(f"🔔 <b>{escape(s['name'])}</b> {fmt(s['amount_minor'], s['currency'])} renews {when} "
                             f"({s['next_due'].strftime('%a %d %b')}). Cancel before then if you don't need it.")
                db.update_subscription(conn, s["id"], reminded_for=s["next_due"])
        for r in self.auto_subscriptions(conn, uid):
            days = (r["next_run"] - today).days
            if 0 <= days <= REMIND_DAYS_BEFORE and r["reminded_for"] != r["next_run"]:
                when = "today" if days == 0 else ("tomorrow" if days == 1 else f"in {days} days")
                notes.append(f"🔔 <b>{escape(r['description'])}</b> {fmt(r['amount_minor'], r['currency'])} renews {when} "
                             f"({r['next_run'].strftime('%a %d %b')}) and logs itself. Cancel before then if you "
                             "don't need it, then <code>/subscriptions stop</code> it.")
                db.update_recurring(conn, r["id"], reminded_for=r["next_run"])
        return notes

    def auto_subscriptions(self, conn, uid: int) -> list[dict]:
        """Subscriptions that log themselves (recurring rules marked as subscriptions)."""
        return [r for r in db.recurring(conn, uid) if r["kind"] == "subscription"]

    def quarterly_check(self, conn, user: dict, today: date) -> None:
        uid = user["telegram_id"]
        for s in db.subscriptions(conn, uid):
            since = s["checked_on"] or s["created_at"].date()
            if (today - since).days >= CHECK_EVERY_DAYS:
                db.update_subscription(conn, s["id"], checked_on=today)
                yearly = fmt(round(s["amount_minor"] * subs.PER_MONTH[s["interval"]] * 12), s["currency"])
                self.send_quietly(uid, f"🤔 Still using <b>{escape(s['name'])}</b>? "
                                          f"{fmt(s['amount_minor'], s['currency'])} a {subs.LABEL[s['interval']]} "
                                          f"is about {yearly} a year.",
                                     reply_markup={"inline_keyboard": [[btn("👍 Yes, keep it", f"sub:{s['id']}:using"),
                                                                        btn("✂️ I cancelled it", f"sub:{s['id']}:cancel")]]})
                return  # one at a time
        for r in self.auto_subscriptions(conn, uid):
            since = r["checked_on"] or r["created_at"].date()
            if (today - since).days >= CHECK_EVERY_DAYS:
                db.update_recurring(conn, r["id"], checked_on=today)
                self.send_quietly(uid, f"🤔 Still using <b>{escape(r['description'])}</b>? "
                                       f"{fmt(r['amount_minor'], r['currency'])} a month is about "
                                       f"{fmt(r['amount_minor'] * 12, r['currency'])} a year.",
                                  reply_markup={"inline_keyboard": [[btn("👍 Yes, keep it", f"subr:{r['id']}:using"),
                                                                     btn("✂️ I cancelled it", f"subr:{r['id']}:cancel")]]})
                return

    def suggest_subscriptions(self, conn, user: dict, charges: list[subs.Charge], today: date, limit: int) -> int:
        uid = user["telegram_id"]
        known = db.subscription_keys(conn, uid)
        rules = [r for r in db.recurring(conn, uid) if r["active"]]
        sent = 0
        for cand in subs.detect(charges, today):
            if sent >= limit:
                break
            if (cand.key, cand.currency) in known:
                continue
            if any(r["currency"] == cand.currency and subs.similar(r["amount_minor"], cand.amount)
                   and (r["category_id"] == cand.category_id or set(subs.key_of(r["description"]).split()) & set(cand.key.split()))
                   for r in rules):
                continue  # already logs itself (/recurring)
            sid = db.add_subscription(conn, uid, cand.key, cand.currency, cand.name, cand.amount, cand.interval,
                                      cand.last, cand.next_due_from(today), cand.wallet_id, cand.category_id)
            if sid is None:
                continue
            dates = ", ".join(c.day.strftime("%d %b") for c in cand.charges[-4:])
            self.send_quietly(uid, f"🔁 Is <b>{escape(cand.name)}</b> {fmt(cand.amount, cand.currency)} a subscription?\n"
                                      f"It came every {subs.LABEL[cand.interval]}: {dates}.\n"
                                      f"If so I'll remind you before the next one ({cand.next_due_from(today).strftime('%a %d %b')}).",
                                 reply_markup={"inline_keyboard": [[btn("✅ Yes, track it", f"sub:{sid}:yes"),
                                                                    btn("❌ No", f"sub:{sid}:no")]]})
            sent += 1
        return sent

    def send_quietly(self, chat_id: int, text: str, reply_markup: Optional[dict] = None) -> None:
        """Scheduled messages: a failed send (e.g. rate limit) must not roll back what was already sent,
        or the next tick would send it all again."""
        try:
            self.tg.send_message(chat_id, text, reply_markup=reply_markup)
        except Exception:
            log.exception("scheduled message not sent")

    # ------------------------------------------------------------------ buttons
    def on_subscription_button(self, conn, uid: int, rest: str, chat_id: int, message_id: int) -> Optional[str]:
        sid, _, action = rest.partition(":")
        s = db.get_subscription(conn, int(sid))
        if not s or s["user_id"] != uid:
            return "That isn't available."
        name = escape(s["name"])
        if action == "yes":
            db.update_subscription(conn, s["id"], status="active")
            text = (f"✅ Tracking <b>{name}</b>: {fmt(s['amount_minor'], s['currency'])} a {subs.LABEL[s['interval']]}, "
                    f"next {s['next_due'].strftime('%a %d %b')}. See all: /subscriptions")
        elif action == "no":
            db.update_subscription(conn, s["id"], status="dismissed")
            text = f"👌 Got it, <b>{name}</b> isn't a subscription. I won't ask again."
        elif action == "using":
            text = f"👍 Keeping <b>{name}</b>. I'll check again in 3 months."
        elif action == "cancel":
            db.update_subscription(conn, s["id"], status="cancelled")
            text = (f"✂️ Marked <b>{name}</b> as cancelled: "
                    f"{fmt(s['amount_minor'], s['currency'])} a {subs.LABEL[s['interval']]} saved 🎉")
        else:
            return "Unknown button."
        self.tg.edit_message_text(chat_id, message_id, text, None)
        return None

    def on_auto_subscription_button(self, conn, uid: int, rest: str, chat_id: int, message_id: int) -> Optional[str]:
        rid, _, action = rest.partition(":")
        r = next((x for x in self.auto_subscriptions(conn, uid) if x["id"] == int(rid)), None)
        if r is None:
            return "That isn't available."
        name = escape(r["description"])
        if action == "using":
            text = f"👍 Keeping <b>{name}</b>. I'll check again in 3 months."
        elif action == "cancel":
            db.update_recurring(conn, r["id"], active=False)
            text = (f"✂️ Stopped <b>{name}</b>: it won't log itself any more. "
                    f"{fmt(r['amount_minor'] * 12, r['currency'])} a year saved 🎉")
        else:
            return "Unknown button."
        self.tg.edit_message_text(chat_id, message_id, text, None)
        return None

    # ------------------------------------------------------------------ /subscriptions
    def cmd_subscriptions(self, conn, user, chat_id, args, **_):
        uid, home, today = user["telegram_id"], user["home_currency"], self.today_for(user)
        parts = args.split()
        usage = ("Find them now: <code>/subscriptions scan</code>\n"
                 "Add one: <code>/subscriptions add Netflix 17.98 monthly</code> (weekly, monthly, quarterly, yearly)\n"
                 "Stop tracking: <code>/subscriptions stop 2</code>")
        if parts and parts[0].lower() == "scan":
            found = self.run_subscriptions(conn, user, today, max_suggestions=5)
            if not found:
                self.tg.send_message(chat_id, "🔍 No new subscriptions found. I look for the same thing at a similar "
                                              "price (±5%) every week, month, quarter or year, at least twice.")
            return
        if parts and parts[0].lower() == "add":
            self.tg.send_message(chat_id, self.add_subscription_by_hand(conn, user, parts[1:], today) or usage)
            return
        if parts and parts[0].lower() in ("stop", "remove", "cancel") and len(parts) == 2 and parts[1].isdigit():
            items = self.subscription_items(conn, uid)
            n = int(parts[1])
            if not 1 <= n <= len(items):
                self.tg.send_message(chat_id, "No subscription with that number. See /subscriptions")
                return
            kind, item = items[n - 1]
            if kind == "rule":
                db.update_recurring(conn, item["id"], active=False)
                self.tg.send_message(chat_id, f"✂️ Stopped <b>{escape(item['description'])}</b>: it won't log itself any more.")
            else:
                db.update_subscription(conn, item["id"], status="cancelled")
                self.tg.send_message(chat_id, f"✂️ Stopped tracking <b>{escape(item['name'])}</b>.")
            return
        if parts:
            self.tg.send_message(chat_id, usage)
            return

        items = self.subscription_items(conn, uid)
        lines = ["🔁 <b>Subscriptions</b> (nice to have)", ""]
        monthly_home = 0
        for i, (kind, s) in enumerate(items, 1):
            if kind == "rule":
                lines.append(f"{i}. <b>{escape(s['description'])}</b> {fmt(s['amount_minor'], s['currency'])} / month · "
                             f"next {s['next_run'].strftime('%a %d %b')} · {escape(s['wallet_name'])} · logs itself")
                monthly_home += self.in_home(conn, home, s["amount_minor"], s["currency"], today)
            else:
                lines.append(f"{i}. <b>{escape(s['name'])}</b> {fmt(s['amount_minor'], s['currency'])} / "
                             f"{subs.LABEL[s['interval']]} · next {s['next_due'].strftime('%a %d %b')}"
                             + (f" · {escape(s['wallet_name'])}" if s["wallet_name"] else ""))
                monthly_home += self.in_home(conn, home, subs.per_month(s["amount_minor"], s["interval"]),
                                             s["currency"], today)
        if items:
            lines += ["", f"≈ <b>{fmt(monthly_home, home)}</b> a month · {fmt(monthly_home * 12, home)} a year"]
        else:
            lines.append("None yet. I check every day and ask when something repeats.")
        lines += ["", "Bills you need (rent, phone…): /recurring", "", usage]
        self.tg.send_message(chat_id, "\n".join(lines))

    def subscription_items(self, conn, uid: int) -> list[tuple[str, dict]]:
        """Tracked subscriptions, then the ones that log themselves; one numbering for /subscriptions stop N."""
        return [("tracked", s) for s in db.subscriptions(conn, uid)] + \
               [("rule", r) for r in self.auto_subscriptions(conn, uid)]

    def add_subscription_by_hand(self, conn, user: dict, words: list[str], today: date) -> Optional[str]:
        interval = next((w.lower() for w in words if w.lower() in subs.INTERVALS), "monthly")
        rest = [w for w in words if w.lower() not in subs.INTERVALS]
        amount_at = next((i for i, w in enumerate(rest) if parse_amount_token(w)), None)
        if amount_at is None or amount_at == 0:
            return None
        tok = parse_amount_token(rest[amount_at])
        value, currency = tok.resolve(user["home_currency"], None)
        name = " ".join(rest[:amount_at])
        key = subs.key_of(name)
        if not key:
            return None
        minor = to_minor(Decimal(value), currency)
        sid = db.add_subscription(conn, user["telegram_id"], key, currency, name, minor, interval, today,
                                  subs.add_interval(today, interval), None, None, status="active")
        if sid is None:
            existing = next((s for s in db.subscriptions(conn, user["telegram_id"], ("suggested", "dismissed", "cancelled"))
                             if s["key"] == key and s["currency"] == currency), None)
            if existing is None:
                return f"You already track {escape(name)}. See /subscriptions"
            db.update_subscription(conn, existing["id"], status="active", amount_minor=minor, name=name,
                                   interval=interval, last_charge_on=today, next_due=subs.add_interval(today, interval))
        return (f"✅ Tracking <b>{escape(name)}</b>: {fmt(minor, currency)} a {subs.LABEL[interval]}, "
                f"next {subs.add_interval(today, interval).strftime('%a %d %b')}.")

    def in_home(self, conn, home: str, minor: int, currency: str, day: date) -> int:
        if currency == home:
            return minor
        rate = self.home_rate(conn, home, currency, day)
        return fx.to_home(minor, currency, home, rate) if rate else 0
