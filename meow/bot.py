"""Update handling: messages, commands, button presses and scheduled ticks.

The same Bot is used by the Vercel webhook, the scheduler endpoint and the local polling script.
"""
from __future__ import annotations

import logging
import random
import re
import uuid
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from html import escape
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo

from . import budgets as bud
from . import db, fx
from .cards import (category_picker, language_keyboard, pause_keyboard, persona_keyboard, reminder_keyboard,
                    render_card, roast_keyboard, totals_line_home, wallet_picker)
from .config import Settings
from .models import Entry, ParseContext, WalletInfo
from .money import CURRENCY_ALIASES, fmt, parse_amount_token, to_minor
from .parser import has_amount, parse_message
from .parser_llm import cost_usd
from .parser_rules import merchant_key, parse_with_rules
from .personas import LANGUAGES, PERSONAS, ROAST_LABELS, line as persona_line
from .telegram import TelegramAPI
from .transfers import TransferRequest, parse_transfer

log = logging.getLogger(__name__)

HELP = """<b>How to log</b>
Just type what you spent or received:
• <code>pho 65k</code>
• <code>grab 12.5 yesterday</code>
• <code>coffee 6, lunch 14, taxi 9</code>
• <code>salary 4200 to DBS</code>
• <code>+50 refund</code>
• <code>move 200 from DBS to Cash</code>

Plain numbers are SGD. <code>65k</code>, <code>65.000</code> or <code>1tr2</code> are VND.
Name a wallet to use it; otherwise SGD goes to DBS and VND to VP.

<b>Commands</b>
/today – today's entries
/month – this month by category, in SGD
/budget – monthly budgets
/balance – wallet balances
/wallet – add or list wallets
/remind – daily reminder time
/persona – who talks to you
/settings – your settings
/undo – undo the last entry
/help – this message"""

COMMANDS = [
    ("today", "Today's entries"),
    ("month", "This month by category"),
    ("budget", "Monthly budgets, e.g. /budget Food 400"),
    ("balance", "Wallet balances"),
    ("wallet", "Add or list wallets"),
    ("setbalance", "Set a wallet balance, e.g. /setbalance DBS 2340.50"),
    ("remind", "Daily reminder, e.g. /remind 21:30"),
    ("persona", "Pick a persona and roast level"),
    ("language", "Persona language: EN, VI or mix"),
    ("rules", "Categories I've learned"),
    ("settings", "Your settings"),
    ("undo", "Undo the last entry"),
    ("help", "How to log"),
]

TOTAL_WORDS = {"total", "all", "month", "overall", "tong"}
WALLET_TYPES = {"cash", "bank", "ewallet", "credit"}


class Bot:
    def __init__(self, settings: Settings, tg: TelegramAPI, llm_client: Any = None,
                 clock: Optional[Callable[[], datetime]] = None,
                 fx_fetch: Optional[fx.Fetcher] = None, rng: Optional[random.Random] = None):
        self.s = settings
        self.tg = tg
        self.llm = llm_client
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.fx_fetch = fx_fetch or fx.fetch_open_er_api
        self.rng = rng or random.Random()

    # ------------------------------------------------------------------ entry points
    def process_update(self, conn, update: dict) -> None:
        """Handle one update inside one DB transaction. Duplicates are ignored."""
        with conn.transaction():
            if not db.claim_update(conn, update["update_id"]):
                log.info("duplicate update %s ignored", update["update_id"])
                return
            if "message" in update:
                self.on_message(conn, update["message"])
            elif "callback_query" in update:
                self.on_callback(conn, update["callback_query"])

    def today_for(self, user: dict) -> date:
        return self.clock().astimezone(ZoneInfo(user["timezone"])).date()

    def allowed(self, user_id: int) -> bool:
        return user_id in self.s.allowed_user_ids

    def say(self, user: dict, situation: str, **facts) -> Optional[str]:
        return persona_line(user["persona"], user["roast_level"], user["language"], situation, self.rng, **facts)

    # ------------------------------------------------------------------ messages
    def on_message(self, conn, msg: dict) -> None:
        chat = msg["chat"]
        sender = msg.get("from") or {}
        if chat.get("type") != "private":
            return
        if not self.allowed(sender.get("id", 0)):
            self.tg.send_message(chat["id"], "🐱 Sorry, M.E.O.W. is a private bot for now.")
            return

        user, created = db.ensure_user(conn, sender["id"], sender.get("first_name", ""),
                                       self.s.home_currency, self.s.default_timezone)
        if created:
            user = db.get_user(conn, sender["id"])  # pick up column defaults
        text = (msg.get("text") or "").strip()
        if not text:
            self.tg.send_message(chat["id"], "I only read text messages for now. Photos and voice notes are coming later 🐾")
            return

        if text.startswith("/"):
            cmd, _, args = text.partition(" ")
            cmd = cmd[1:].split("@")[0].lower()
            handler = getattr(self, f"cmd_{cmd}", None)
            if handler is None:
                self.tg.send_message(chat["id"], "I don't know that command. Try /help.")
                return
            handler(conn, user, chat["id"], args.strip(), created=created)
            return

        self.log_money(conn, user, chat["id"], text)

    def context(self, conn, user: dict, today: date) -> ParseContext:
        uid = user["telegram_id"]
        return ParseContext(today=today, home_currency=user["home_currency"],
                            wallets=db.wallets(conn, uid), categories=db.categories(conn, uid),
                            merchant_rules=db.active_rules(conn, uid))

    def log_money(self, conn, user: dict, chat_id: int, text: str) -> None:
        uid = user["telegram_id"]
        today = self.today_for(user)
        ctx = self.context(conn, user, today)

        transfer = parse_transfer(text, ctx)
        if isinstance(transfer, str):
            self.tg.send_message(chat_id, transfer)
            return
        if isinstance(transfer, TransferRequest):
            db.set_pending(conn, uid, None)
            self.do_transfer(conn, user, chat_id, ctx, transfer, text)
            return

        day_total = self.day_total_entry(user, text, today)
        if day_total is not None:
            db.set_pending(conn, uid, None)
            self.save_entries(conn, user, chat_id, ctx, [day_total], text, parser="eod", source="eod")
            return

        pending = user.get("pending_input")
        to_parse = text
        if pending and not parse_with_rules(text, ctx):
            # The user is answering the bot's question: give the model both parts.
            to_parse = f"{pending}\n\nAnswer to your question: {text}"

        if not has_amount(to_parse):
            db.set_pending(conn, uid, None)
            self.tg.send_message(chat_id, "I didn't see an amount there 🐾 Try something like <code>pho 65k</code>, or /help.")
            return

        llm_logs = []
        since = self.clock() - timedelta(days=1)
        llm_allowed = db.llm_calls_since(conn, uid, since) < self.s.llm_daily_call_cap
        result = parse_message(to_parse, ctx, llm_client=self.llm, model=self.s.anthropic_model,
                               on_llm_call=llm_logs.append, llm_allowed=llm_allowed)
        for call in llm_logs:
            db.log_llm_call(conn, uid, "parse", call,
                            cost_usd(call, self.s.llm_input_price, self.s.llm_output_price))

        if not result.entries:
            db.set_pending(conn, uid, to_parse if result.parser == "llm" else None)
            self.tg.send_message(chat_id, escape(result.question or "I couldn't read that. Try /help."))
            return
        db.set_pending(conn, uid, None)
        self.save_entries(conn, user, chat_id, ctx, result.entries, to_parse, parser=result.parser)

    def day_total_entry(self, user: dict, text: str, today: date) -> Optional[Entry]:
        """After a reminder, a bare number ("45") is the day's total."""
        if user.get("last_reminded_on") != today:
            return None
        tokens = text.split()
        if not 1 <= len(tokens) <= 2:
            return None
        tok = parse_amount_token(tokens[0])
        explicit = CURRENCY_ALIASES.get(tokens[1].lower()) if len(tokens) == 2 else None
        if tok is None or (len(tokens) == 2 and explicit is None):
            return None
        value, currency = tok.resolve(user["home_currency"], explicit)
        return Entry(amount=value, currency=currency, type="expense", category="Other",
                     description="Day total", date=today)

    def save_entries(self, conn, user: dict, chat_id: int, ctx: ParseContext, entries: list[Entry],
                     raw: str, parser: str, source: str = "text") -> None:
        uid, today = user["telegram_id"], ctx.today
        rows, question = self.resolve(conn, user, ctx, entries)
        if question:
            db.set_pending(conn, uid, raw)
            self.tg.send_message(chat_id, escape(question))
            return
        batch_id = db.insert_transactions(conn, uid, rows, raw_message=raw, parser=parser, source=source)
        note = self.compose_note(conn, user, rows, today)
        batch = db.get_batch(conn, batch_id)
        for r in batch:
            r["card_note"] = note
        text_out, keyboard = render_card(batch, today, self.footer(conn, user, today), user["home_currency"])
        sent = self.tg.send_message(chat_id, text_out, reply_markup=keyboard)
        if sent:
            db.set_card(conn, batch_id, chat_id, sent["message_id"], note)

    def footer(self, conn, user: dict, today: date) -> str:
        spent, missing = db.spent_on_home(conn, user["telegram_id"], today)
        return totals_line_home(user["home_currency"], spent, missing)

    # ------------------------------------------------------------------ money helpers
    def home_rate(self, conn, home: str, currency: str, day: date) -> Optional[Decimal]:
        rate = fx.units_per_home(conn, home, currency, day)
        if rate is None and currency != home:
            if fx.ensure_rates(conn, home, self.clock().date(), self.fx_fetch):
                rate = fx.units_per_home(conn, home, currency, day)
        return rate

    def with_home(self, conn, home: str, row: dict) -> dict:
        rate = self.home_rate(conn, home, row["currency"], row["occurred_on"])
        row["fx_rate"] = rate
        row["amount_home"] = fx.to_home(row["amount_minor"], row["currency"], home, rate) if rate else None
        return row

    def resolve(self, conn, user: dict, ctx: ParseContext, entries: list[Entry]) -> tuple[list[dict], Optional[str]]:
        """Turn parsed entries into ledger rows. Returns (rows, question)."""
        uid, home = user["telegram_id"], user["home_currency"]
        rows = []
        for e in entries:
            if e.wallet:
                wallet = ctx.wallet_by_name(e.wallet)
                if wallet and wallet.currency != e.currency:
                    shown = fmt(to_minor(e.amount, e.currency), e.currency)
                    return [], (f"{wallet.name} holds {wallet.currency}, but I read {shown} for "
                                f"\"{e.description}\". Which currency did you mean?")
            else:
                wallet = (next((w for w in ctx.wallets if w.currency == e.currency and w.is_default), None)
                          or next((w for w in ctx.wallets if w.currency == e.currency), None))
                if wallet is None:
                    wallet = self.cash_wallet(conn, uid, ctx, e.currency, home)

            cat = next((c for c in ctx.categories if c.name == e.category and c.type == e.type), None)
            if cat is None:
                fallback = "Other income" if e.type == "income" else "Other"
                cat = next((c for c in ctx.categories if c.name == fallback), None)

            minor = to_minor(e.amount, e.currency)
            if minor == 0:
                return [], f"\"{e.description}\" came out as zero. What was the amount?"
            rows.append(self.with_home(conn, home, {
                "wallet_id": wallet.id,
                "category_id": cat.id if cat else None,
                "type": e.type,
                "amount_minor": minor if e.type == "income" else -minor,
                "currency": e.currency,
                "description": e.description,
                "occurred_on": e.date,
            }))
        return rows, None

    def cash_wallet(self, conn, uid: int, ctx: ParseContext, currency: str, home: str) -> WalletInfo:
        """Find or create a cash wallet: "Cash" for the home currency, "Cash VND" etc. otherwise."""
        name = "Cash" if currency == home else f"Cash {currency}"
        existing = ctx.wallet_by_name(name)
        if existing and existing.currency == currency:
            return existing
        if existing:  # a "Cash" wallet in another currency already exists
            name = f"Cash {currency}"
        wallet = db.create_cash_wallet(conn, uid, currency, name)
        ctx.wallets.append(wallet)
        return wallet

    def compose_note(self, conn, user: dict, rows: list[dict], today: date) -> Optional[str]:
        """Persona line plus any budget alerts this entry triggered."""
        home = user["home_currency"]
        lines: list[str] = []
        expenses = [r for r in rows if r["type"] == "expense"]
        if any(r["type"] == "income" for r in rows):
            situation, facts = "income", {}
        else:
            big = max(expenses, key=lambda r: -(r["amount_home"] or 0), default=None)
            if big and big["amount_home"] is not None and -big["amount_home"] >= to_minor(Decimal(str(self.s.big_expense)), home):
                situation, facts = "big", {"amount": fmt(-big["amount_home"], home)}
            else:
                situation, facts = "expense", {}
        alerts = self.budget_alerts(conn, user, rows, today)
        if not alerts:
            said = self.say(user, situation, **facts)
            if said:
                lines.append(f"<i>{escape(said, quote=False)}</i>")
        lines += alerts
        return "\n".join(lines) or None

    def budget_alerts(self, conn, user: dict, rows: list[dict], today: date) -> list[str]:
        uid, home = user["telegram_id"], user["home_currency"]
        first = today.replace(day=1)
        nxt = (first + timedelta(days=32)).replace(day=1)
        this_month = [r for r in rows if r["type"] == "expense" and first <= r["occurred_on"] < nxt
                      and r["amount_home"] is not None]
        if not this_month:
            return []
        spent = db.month_spent_home(conn, uid, first, nxt)
        added: dict[Optional[int], int] = {None: 0}
        for r in this_month:
            added[r["category_id"]] = added.get(r["category_id"], 0) - r["amount_home"]
            added[None] -= r["amount_home"]
        out = []
        for b in db.budgets(conn, uid):
            cid = b["category_id"]
            if cid not in added:
                continue
            after = spent.get(cid, 0)
            kind = bud.crossing(after - added[cid], after, b["limit_minor"])
            if not kind:
                continue
            name = b["name"] or "Total"
            p = bud.pct(after, b["limit_minor"])
            said = self.say(user, f"budget_{kind}", category=name, pct=p)
            icon = "🚨" if kind == "over" else "⚠️"
            out.append(f"{icon} <b>{escape(name)}</b> budget: {fmt(after, home)} / {fmt(b['limit_minor'], home)} ({p}%)")
            if said:
                out.append(f"<i>{escape(said, quote=False)}</i>")
        return out

    # ------------------------------------------------------------------ transfers
    def do_transfer(self, conn, user: dict, chat_id: int, ctx: ParseContext, t: TransferRequest, raw: str) -> None:
        uid, home, today = user["telegram_id"], user["home_currency"], ctx.today
        src = t.from_wallet
        dst = t.to_wallet or self.cash_wallet(conn, uid, ctx, src.currency, home)
        cur = t.currency or ("VND" if t.vnd_hint else src.currency)
        estimated = False

        if cur == src.currency:
            out_minor = to_minor(t.amount, cur)
            if dst.currency == src.currency:
                in_minor = out_minor
            elif t.received:
                in_minor = to_minor(t.received[0], dst.currency)
            else:
                in_minor, estimated = self.fx_convert(conn, home, out_minor, src.currency, dst.currency, today), True
        elif cur == dst.currency:
            in_minor = to_minor(t.amount, cur)
            out_minor, estimated = self.fx_convert(conn, home, in_minor, dst.currency, src.currency, today), True
        else:
            self.tg.send_message(chat_id, f"{escape(src.name)} holds {src.currency} and {escape(dst.name)} holds "
                                          f"{dst.currency}, but the amount looks like {cur}.")
            return
        if out_minor is None or in_minor is None:
            self.tg.send_message(chat_id, "I don't have an exchange rate right now. Add the amount received, e.g. "
                                          "<code>move 500 from DBS to VP as 9.8tr</code>")
            return
        if out_minor <= 0 or in_minor <= 0:
            self.tg.send_message(chat_id, "The amount must be more than zero.")
            return

        rows = [
            self.with_home(conn, home, {"wallet_id": src.id, "category_id": None, "type": "transfer",
                                        "amount_minor": -out_minor, "currency": src.currency,
                                        "description": f"to {dst.name}", "occurred_on": today}),
            self.with_home(conn, home, {"wallet_id": dst.id, "category_id": None, "type": "transfer",
                                        "amount_minor": in_minor, "currency": dst.currency,
                                        "description": f"from {src.name}", "occurred_on": today}),
        ]
        batch_id = db.insert_transactions(conn, uid, rows, raw_message=raw, parser="rule", source="transfer")
        note = ("<i>Converted at today's rate. If your bank used a different rate, "
                "fix it with /setbalance.</i>") if estimated else None
        batch = db.get_batch(conn, batch_id)
        for r in batch:
            r["card_note"] = note
        text_out, keyboard = render_card(batch, today, "", home)
        sent = self.tg.send_message(chat_id, text_out, reply_markup=keyboard)
        if sent:
            db.set_card(conn, batch_id, chat_id, sent["message_id"], note)

    def fx_convert(self, conn, home: str, minor: int, src: str, dst: str, day: date) -> Optional[int]:
        r_src = self.home_rate(conn, home, src, day)
        r_dst = self.home_rate(conn, home, dst, day)
        if not r_src or not r_dst:
            return None
        return fx.convert(minor, src, dst, home, r_src, r_dst)

    # ------------------------------------------------------------------ commands
    def cmd_start(self, conn, user, chat_id, args, created=False):
        name = escape(user.get("first_name") or "there")
        intro = (f"🐱 Hi {name}! I'm <b>M.E.O.W.</b>, your Expense &amp; Outflow Watcher.\n\n"
                 "I've set up two wallets: <b>DBS</b> (SGD) and <b>VP</b> (VND), plus some starter categories.\n"
                 "Tip: set your real balances first, e.g. <code>/setbalance DBS 2340.50</code>\n"
                 "I'll check in at 21:30 if you haven't logged anything. Change it with /remind.\n\n")
        if not created:
            intro = f"🐱 Welcome back, {name}!\n\n"
        self.tg.send_message(chat_id, intro + HELP)

    def cmd_help(self, conn, user, chat_id, args, **_):
        self.tg.send_message(chat_id, HELP)

    def cmd_today(self, conn, user, chat_id, args, **_):
        uid, today, home = user["telegram_id"], self.today_for(user), user["home_currency"]
        entries = db.entries_on(conn, uid, today)
        marked = db.logged_on(conn, uid, today) and not entries
        if not entries:
            body = "😻 No-spend day." if marked else "Nothing logged yet today."
            self.tg.send_message(chat_id, f"📅 <b>Today</b>\n\n{body}")
            return
        lines = [f"📅 <b>Today</b> · {today.strftime('%a %d %b')}", ""]
        for e in entries:
            sign = "+" if e["type"] == "income" else ""
            lines.append(f"{e['category_emoji'] or '•'} {escape(e['description'] or '')} — "
                         f"{sign}{fmt(abs(e['amount_minor']), e['currency'])} · {escape(e['wallet_name'])}")
        lines += ["", self.footer(conn, user, today)]
        self.tg.send_message(chat_id, "\n".join(lines))

    def cmd_month(self, conn, user, chat_id, args, **_):
        uid, today, home = user["telegram_id"], self.today_for(user), user["home_currency"]
        first = today.replace(day=1)
        nxt = (first + timedelta(days=32)).replace(day=1)
        rows = db.month_by_category_home(conn, uid, first, nxt)
        title = f"📊 <b>{first.strftime('%B %Y')}</b> · in {home}"
        if not rows:
            self.tg.send_message(chat_id, f"{title}\n\nNothing logged this month yet.")
            return
        limits = {b["category_id"]: b["limit_minor"] for b in db.budgets(conn, uid)}
        exp = [r for r in rows if r["type"] == "expense"]
        inc = [r for r in rows if r["type"] == "income"]
        spent = -sum(r["total"] or 0 for r in exp)
        earned = sum(r["total"] or 0 for r in inc)
        lines = [title, "", f"Spent <b>{fmt(spent, home)}</b>" + (
            f" of {fmt(limits[None], home)} ({bud.pct(spent, limits[None])}%)" if None in limits else "")]
        for r in exp:
            amt = -(r["total"] or 0)
            share = f" ({amt * 100 // spent}%)" if spent > 0 else ""
            budget = f" · budget {bud.pct(amt, limits[r['category_id']])}%" if r["category_id"] in limits else ""
            lines.append(f"  {r['emoji']} {escape(r['category'])} {fmt(amt, home)}{share}{budget}")
        if earned:
            lines += [f"Income <b>+{fmt(earned, home)}</b>"]
            net = earned - spent
            lines.append(f"Net {'+' if net >= 0 else ''}{fmt(net, home)}")
            if earned > 0:
                lines.append(f"Savings rate {max(net, 0) * 100 // earned}%")
        # What was paid in other currencies, before conversion.
        foreign = [r for r in db.month_summary(conn, uid, first, nxt)
                   if r["currency"] != home and r["type"] == "expense"]
        by_cur: dict[str, int] = {}
        for r in foreign:
            by_cur[r["currency"]] = by_cur.get(r["currency"], 0) - r["total"]
        if by_cur:
            lines += ["", "Paid in other currencies: " + ", ".join(fmt(v, c) for c, v in by_cur.items())]
        waiting = sum(r["unconverted"] for r in rows)
        if waiting:
            lines.append(f"⏳ {waiting} entr{'y' if waiting == 1 else 'ies'} waiting for an exchange rate (not in totals).")
        self.tg.send_message(chat_id, "\n".join(lines))

    def cmd_balance(self, conn, user, chat_id, args, **_):
        rows = db.balances(conn, user["telegram_id"])
        lines = ["👛 <b>Balances</b>", ""]
        lines += [f"{escape(r['name'])}: <b>{fmt(r['balance_minor'], r['currency'])}</b>" for r in rows]
        lines += ["", "Balances start from zero until you set them with /setbalance."]
        self.tg.send_message(chat_id, "\n".join(lines))

    def cmd_setbalance(self, conn, user, chat_id, args, **_):
        uid = user["telegram_id"]
        parts = args.split()
        usage = "Usage: <code>/setbalance DBS 2340.50</code>"
        if len(parts) < 2:
            self.tg.send_message(chat_id, usage)
            return
        name, amount = " ".join(parts[:-1]), parts[-1]
        wallet = next((w for w in db.wallets(conn, uid) if w.name.lower() == name.lower()), None)
        token = parse_amount_token(amount)
        if wallet is None or token is None:
            names = ", ".join(w.name for w in db.wallets(conn, uid))
            self.tg.send_message(chat_id, f"{usage}\nYour wallets: {escape(names)}")
            return
        target = to_minor(token.value, wallet.currency)
        diff = target - db.wallet_balance(conn, wallet.id)
        if diff == 0:
            self.tg.send_message(chat_id, f"{escape(wallet.name)} is already {fmt(target, wallet.currency)} ✅")
            return
        row = self.with_home(conn, user["home_currency"], {
            "wallet_id": wallet.id, "category_id": None, "type": "adjustment", "amount_minor": diff,
            "currency": wallet.currency, "description": f"Balance set to {fmt(target, wallet.currency)}",
            "occurred_on": self.today_for(user)})
        db.insert_transactions(conn, uid, [row], raw_message=f"/setbalance {args}", parser="command", source="command")
        sign = "+" if diff > 0 else ""
        self.tg.send_message(chat_id, f"👛 {escape(wallet.name)} is now <b>{fmt(target, wallet.currency)}</b> "
                                      f"(adjustment {sign}{fmt(diff, wallet.currency)}).")

    def cmd_undo(self, conn, user, chat_id, args, **_):
        uid = user["telegram_id"]
        batch_id = db.last_live_batch(conn, uid)
        if batch_id is None:
            self.tg.send_message(chat_id, "Nothing to undo.")
            return
        rows = db.get_batch(conn, batch_id)
        db.undo_batch(conn, uid, batch_id)
        summary = ", ".join(f"{r['description']} {fmt(abs(r['amount_minor']), r['currency'])}"
                            for r in rows if not r["reversed"])
        self.tg.send_message(chat_id, f"↩️ Undone: {escape(summary)}")
        card = rows[0]
        if card["card_message_id"]:
            self.refresh_card(conn, uid, batch_id, card["card_chat_id"], card["card_message_id"])

    def cmd_budget(self, conn, user, chat_id, args, **_):
        uid, home = user["telegram_id"], user["home_currency"]
        usage = ("Set one with <code>/budget Food 400</code> or <code>/budget total 2000</code>.\n"
                 "Remove one with <code>/budget Food off</code>.")
        if not args:
            today = self.today_for(user)
            first = today.replace(day=1)
            spent = db.month_spent_home(conn, uid, first, (first + timedelta(days=32)).replace(day=1))
            items = db.budgets(conn, uid)
            if not items:
                self.tg.send_message(chat_id, f"💰 <b>Budgets</b>\n\nNo budgets yet. {usage}")
                return
            lines = [f"💰 <b>Budgets</b> · {first.strftime('%B')}", ""]
            for b in items:
                s = spent.get(b["category_id"], 0)
                label = f"{b['emoji']} {escape(b['name'])}" if b["name"] else "🧮 Total"
                lines.append(f"{label}\n{bud.bar(s, b['limit_minor'])} {fmt(s, home)} / "
                             f"{fmt(b['limit_minor'], home)} ({bud.pct(s, b['limit_minor'])}%)")
            lines += ["", usage]
            self.tg.send_message(chat_id, "\n".join(lines))
            return

        parts = args.split()
        if len(parts) < 2:
            self.tg.send_message(chat_id, usage)
            return
        name, value = " ".join(parts[:-1]), parts[-1]
        if name.lower() in TOTAL_WORDS:
            cat_id, label = None, "Total"
        else:
            cat = next((c for c in db.categories(conn, uid) if c.name.lower() == name.lower() and c.type == "expense"), None)
            if cat is None:
                names = ", ".join(c.name for c in db.categories(conn, uid) if c.type == "expense")
                self.tg.send_message(chat_id, f"I don't have a category called \"{escape(name)}\".\nCategories: {escape(names)}")
                return
            cat_id, label = cat.id, cat.name
        if value.lower() in ("off", "remove", "delete", "0"):
            n = db.delete_budget(conn, uid, cat_id)
            self.tg.send_message(chat_id, f"Removed the {escape(label)} budget." if n else f"There was no {escape(label)} budget.")
            return
        tok = parse_amount_token(value)
        if tok is None:
            self.tg.send_message(chat_id, usage)
            return
        limit = to_minor(tok.value, home)
        db.set_budget(conn, uid, cat_id, limit)
        self.tg.send_message(chat_id, f"💰 {escape(label)} budget set to <b>{fmt(limit, home)}</b> a month. "
                                      f"I'll warn you at 80% and 100%.")

    def cmd_remind(self, conn, user, chat_id, args, **_):
        uid = user["telegram_id"]
        a = args.lower().split()
        today = self.today_for(user)
        if not a:
            t = user["reminder_time"].strftime("%H:%M")
            state = "on" if user["reminders_on"] else "off"
            paused = user["reminders_paused_until"]
            extra = f"\nPaused until {paused.strftime('%a %d %b')}." if paused and paused >= today else ""
            self.tg.send_message(chat_id, f"⏰ Daily check-in at <b>{t}</b> ({state}).{extra}\n\n"
                                          "Change it: <code>/remind 22:00</code> · <code>/remind off</code> · "
                                          "<code>/remind on</code> · <code>/remind pause 7</code>\n"
                                          "I only message you if nothing was logged that day.")
            return
        if a[0] in ("off", "stop"):
            db.update_user(conn, uid, reminders_on=False)
            self.tg.send_message(chat_id, "⏰ Daily reminder is off. Turn it back on with <code>/remind on</code>.")
            return
        if a[0] in ("on", "start", "resume"):
            db.update_user(conn, uid, reminders_on=True, reminders_paused_until=None)
            self.tg.send_message(chat_id, f"⏰ Daily reminder is on at {user['reminder_time'].strftime('%H:%M')}.")
            return
        if a[0] == "pause" and len(a) == 2 and a[1].isdigit() and 1 <= int(a[1]) <= 60:
            until = today + timedelta(days=int(a[1]) - 1)
            db.update_user(conn, uid, reminders_paused_until=until)
            self.tg.send_message(chat_id, f"✈️ Reminders paused. Back on {(until + timedelta(days=1)).strftime('%a %d %b')}.")
            return
        m = re.fullmatch(r"(\d{1,2})[:.h]?(\d{2})?", a[0])
        if m and int(m[1]) < 24 and int(m[2] or 0) < 60:
            t = time(int(m[1]), int(m[2] or 0))
            db.update_user(conn, uid, reminder_time=t, reminders_on=True)
            self.tg.send_message(chat_id, f"⏰ Got it. I'll check in at <b>{t.strftime('%H:%M')}</b> if nothing's logged.")
            return
        self.tg.send_message(chat_id, "Try <code>/remind 21:30</code>, <code>/remind off</code> or <code>/remind pause 7</code>.")

    def cmd_persona(self, conn, user, chat_id, args, **_):
        self.tg.send_message(chat_id, "🎭 <b>Who should talk to you?</b>\nThe persona changes the tone only, never the numbers.",
                             reply_markup=persona_keyboard(user["persona"], PERSONAS))

    def cmd_roast(self, conn, user, chat_id, args, **_):
        self.tg.send_message(chat_id, "🌶 <b>Roast level</b>", reply_markup=roast_keyboard(user["roast_level"], ROAST_LABELS))

    def cmd_language(self, conn, user, chat_id, args, **_):
        self.tg.send_message(chat_id, "🗣 <b>Persona language</b>\nMenus and numbers stay in English.",
                             reply_markup=language_keyboard(user["language"], LANGUAGES))

    def cmd_settings(self, conn, user, chat_id, args, **_):
        rem = (f"{user['reminder_time'].strftime('%H:%M')}" if user["reminders_on"] else "off")
        self.tg.send_message(chat_id, "⚙️ <b>Settings</b>\n\n"
                                      f"Persona: {PERSONAS.get(user['persona'], user['persona'])} (/persona)\n"
                                      f"Roast level: {ROAST_LABELS[user['roast_level']]} (/roast)\n"
                                      f"Language: {LANGUAGES.get(user['language'], user['language'])} (/language)\n"
                                      f"Daily check-in: {rem} (/remind)\n"
                                      f"Home currency: {user['home_currency']}\n"
                                      f"Timezone: {user['timezone']}")

    def cmd_wallet(self, conn, user, chat_id, args, **_):
        uid = user["telegram_id"]
        parts = args.split()
        usage = ("Add one: <code>/wallet add Cash SGD cash</code> (types: cash, bank, ewallet, credit)\n"
                 "Make one the default for its currency: <code>/wallet default Cash</code>")
        if not parts:
            ws = db.wallets(conn, uid)
            lines = ["👛 <b>Wallets</b>", ""] + [
                f"• {escape(w.name)} ({w.currency})" + (" · default" if w.is_default else "") for w in ws]
            self.tg.send_message(chat_id, "\n".join(lines + ["", usage]))
            return
        if parts[0].lower() == "add" and len(parts) in (3, 4):
            name, cur = parts[1], parts[2].upper()
            type_ = parts[3].lower() if len(parts) == 4 else ("cash" if name.lower() == "cash" else "bank")
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,19}", name):
                self.tg.send_message(chat_id, "Wallet names are one word (letters and numbers), e.g. <code>GrabPay</code>.")
                return
            if not re.fullmatch(r"[A-Z]{3}", cur) or type_ not in WALLET_TYPES:
                self.tg.send_message(chat_id, usage)
                return
            if any(w.name.lower() == name.lower() for w in db.wallets(conn, uid)):
                self.tg.send_message(chat_id, f"You already have a wallet called {escape(name)}.")
                return
            w = db.add_wallet(conn, uid, name, type_, cur)
            extra = " It's now the default for " + cur + "." if w.is_default else ""
            self.tg.send_message(chat_id, f"👛 Added <b>{escape(w.name)}</b> ({cur}, {type_}).{extra}\n"
                                          f"Set its balance: <code>/setbalance {escape(w.name)} 100</code>")
            return
        if parts[0].lower() == "default" and len(parts) == 2:
            w = next((w for w in db.wallets(conn, uid) if w.name.lower() == parts[1].lower()), None)
            if w is None:
                self.tg.send_message(chat_id, f"I don't have a wallet called {escape(parts[1])}.")
                return
            db.set_default_wallet(conn, uid, w.id)
            self.tg.send_message(chat_id, f"👛 {escape(w.name)} is now the default {w.currency} wallet.")
            return
        self.tg.send_message(chat_id, usage)

    cmd_wallets = cmd_wallet

    def cmd_rules(self, conn, user, chat_id, args, **_):
        uid = user["telegram_id"]
        parts = args.split()
        if len(parts) == 2 and parts[0].lower() == "forget":
            n = db.forget_rule(conn, uid, parts[1].lower())
            self.tg.send_message(chat_id, f"Forgot \"{escape(parts[1])}\"." if n else "I didn't have a rule for that.")
            return
        rules = db.active_rules(conn, uid)
        if not rules:
            self.tg.send_message(chat_id, "🧠 No learned rules yet. When you change an entry's category twice for "
                                          "the same shop (e.g. Grab → Transport), I'll remember it.")
            return
        lines = ["🧠 <b>What I've learned</b>", ""] + [f"• {escape(k)} → {escape(v)}" for k, v in sorted(rules.items())]
        lines += ["", "Forget one: <code>/rules forget grab</code>"]
        self.tg.send_message(chat_id, "\n".join(lines))

    # ------------------------------------------------------------------ buttons
    def on_callback(self, conn, cq: dict) -> None:
        uid = cq["from"]["id"]
        data = cq.get("data") or ""
        msg = cq.get("message") or {}
        chat_id, message_id = msg.get("chat", {}).get("id"), msg.get("message_id")
        if not self.allowed(uid) or not chat_id:
            self.tg.answer_callback_query(cq["id"])
            return

        action, _, rest = data.partition(":")
        note = None
        try:
            if action in ("ok", "undo", "back"):
                batch_id = uuid.UUID(hex=rest)
                rows = db.get_batch(conn, batch_id)
                if not rows or rows[0]["user_id"] != uid:
                    note = "That entry isn't available."
                elif action == "ok":
                    self.tg.edit_message_reply_markup(chat_id, message_id, None)
                    note = "Saved ✅"
                elif action == "undo":
                    db.undo_batch(conn, uid, batch_id)
                    self.refresh_card(conn, uid, batch_id, chat_id, message_id)
                    note = "Undone"
                else:
                    self.refresh_card(conn, uid, batch_id, chat_id, message_id)

            elif action in ("cat", "wal"):
                tx = db.get_transaction(conn, int(rest))
                if not tx or tx["user_id"] != uid or tx["reversed"]:
                    note = "That entry isn't available."
                elif action == "cat":
                    kb = category_picker(tx["id"], tx["batch_id"].hex, db.categories(conn, uid), tx["type"])
                    self.tg.edit_message_reply_markup(chat_id, message_id, kb)
                else:
                    ws = [w for w in db.wallets(conn, uid) if w.currency == tx["currency"]]
                    if len(ws) < 2:
                        note = f"You only have one {tx['currency']} wallet."
                    else:
                        self.tg.edit_message_reply_markup(chat_id, message_id,
                                                          wallet_picker(tx["id"], tx["batch_id"].hex, ws, tx["currency"]))

            elif action in ("setcat", "setwal"):
                tx_id, _, target = rest.partition(":")
                tx = db.get_transaction(conn, int(tx_id))
                setter = db.set_category if action == "setcat" else db.set_wallet
                if not tx or not setter(conn, uid, int(tx_id), int(target)):
                    note = "Couldn't change that."
                else:
                    self.refresh_card(conn, uid, tx["batch_id"], chat_id, message_id)
                    note = "Updated"
                    if action == "setcat":
                        note = self.learn(conn, uid, tx, int(target)) or note

            elif action == "eod":
                note = self.on_reminder_button(conn, uid, rest, chat_id, message_id)

            elif action in ("pers", "roast", "lang"):
                note = self.on_setting_button(conn, uid, action, rest, chat_id, message_id)
            else:
                note = "Unknown button."
        finally:
            self.tg.answer_callback_query(cq["id"], note)

    def learn(self, conn, uid: int, tx: dict, category_id: int) -> Optional[str]:
        key = merchant_key(tx["description"] or "")
        if not key:
            return None
        if db.record_correction(conn, uid, key, category_id) == db.LEARN_AFTER:
            cat = next((c for c in db.categories(conn, uid) if c.id == category_id), None)
            if cat:
                return f"🧠 Learned: \"{key}\" → {cat.name} from now on."
        return None

    def on_reminder_button(self, conn, uid: int, rest: str, chat_id: int, message_id: int) -> Optional[str]:
        user = db.get_user(conn, uid)
        today = self.today_for(user)
        if rest == "nospend":
            if db.spent_anything_on(conn, uid, today):
                return "You already logged spending today 🙂"
            db.mark_no_spend(conn, uid, today)
            said = self.say(user, "no_spend")
            self.tg.edit_message_text(chat_id, message_id,
                                      "😻 <b>No-spend day saved.</b>" + (f"\n<i>{escape(said, quote=False)}</i>" if said else ""))
            return "Saved"
        if rest == "snooze":
            at = self.clock() + timedelta(hours=1)
            db.update_user(conn, uid, snooze_until=at)
            local = at.astimezone(ZoneInfo(user["timezone"])).strftime("%H:%M")
            self.tg.edit_message_text(chat_id, message_id, f"⏰ OK, I'll ask again at {local}.")
            return None
        if rest == "skip":
            self.tg.edit_message_text(chat_id, message_id, "🙈 Skipped for today. See you tomorrow.")
            return None
        if rest == "pausemenu":
            self.tg.edit_message_reply_markup(chat_id, message_id, pause_keyboard())
            return None
        if rest == "back":
            self.tg.edit_message_reply_markup(chat_id, message_id, reminder_keyboard())
            return None
        if rest.startswith("pause:"):
            days = int(rest.split(":")[1])
            until = today + timedelta(days=days - 1)
            db.update_user(conn, uid, reminders_paused_until=until)
            back = (until + timedelta(days=1)).strftime("%a %d %b")
            self.tg.edit_message_text(chat_id, message_id, f"✈️ Reminders paused. I'll be back on {back}. "
                                                           "You can still log any time.")
            return None
        return "Unknown button."

    def on_setting_button(self, conn, uid: int, action: str, value: str, chat_id: int, message_id: int) -> Optional[str]:
        if action == "pers" and value in PERSONAS:
            db.update_user(conn, uid, persona=value)
            if value == "plain":
                self.tg.edit_message_text(chat_id, message_id, "📋 Plain mode: no comments, just numbers.")
                return "Saved"
            user = db.get_user(conn, uid)
            self.tg.edit_message_text(chat_id, message_id, f"{PERSONAS[value]} it is. Now pick a <b>roast level</b>:",
                                      roast_keyboard(user["roast_level"], ROAST_LABELS))
            return "Saved"
        if action == "roast" and value.isdigit() and int(value) in ROAST_LABELS:
            db.update_user(conn, uid, roast_level=int(value))
        elif action == "lang" and value in LANGUAGES:
            db.update_user(conn, uid, language=value)
        else:
            return "Unknown option."
        user = db.get_user(conn, uid)
        sample = self.say(user, "big", amount=fmt(12000, user["home_currency"]))
        label = ROAST_LABELS[user["roast_level"]] if action == "roast" else LANGUAGES[user["language"]]
        text = f"✅ {'Roast level' if action == 'roast' else 'Language'}: <b>{label}</b>"
        if sample:
            text += f"\n\nSample: <i>{escape(sample, quote=False)}</i>"
        self.tg.edit_message_text(chat_id, message_id, text)
        return "Saved"

    def refresh_card(self, conn, uid: int, batch_id, chat_id: int, message_id: int) -> None:
        user = db.get_user(conn, uid)
        today = self.today_for(user)
        text, kb = render_card(db.get_batch(conn, batch_id), today, self.footer(conn, user, today),
                               user["home_currency"])
        self.tg.edit_message_text(chat_id, message_id, text, kb)

    # ------------------------------------------------------------------ scheduler
    def run_tick(self, conn) -> dict:
        """Called every 15 minutes by the scheduler. Safe to call more often: every step is idempotent."""
        now = self.clock()
        stats = {"reminders": 0, "fx": False, "backfilled": 0}
        with conn.transaction():
            homes = {r["home_currency"] for r in conn.execute("select distinct home_currency from users").fetchall()}
            for home in homes or {self.s.home_currency}:
                stats["fx"] = fx.ensure_rates(conn, home, now.date(), self.fx_fetch) or stats["fx"]
            stats["backfilled"] = fx.backfill(conn)
        for user in db.users_for_tick(conn):
            try:
                with conn.transaction():
                    if self.maybe_remind(conn, user, now):
                        stats["reminders"] += 1
            except Exception:
                log.exception("reminder failed for user %s", user["telegram_id"])
        return stats

    def maybe_remind(self, conn, user: dict, now: datetime) -> bool:
        uid = user["telegram_id"]
        tz = ZoneInfo(user["timezone"])
        local = now.astimezone(tz)
        today = local.date()
        paused = user["reminders_paused_until"] is not None and user["reminders_paused_until"] >= today
        due_at = datetime.combine(today, user["reminder_time"], tz)
        regular = (user["reminders_on"] and not paused and due_at <= local < due_at + timedelta(hours=3)
                   and user["last_reminded_on"] != today)
        snoozed = user["snooze_until"] is not None and now >= user["snooze_until"]
        if not (regular or snoozed):
            return False
        if snoozed:
            db.update_user(conn, uid, snooze_until=None)
        if regular and not db.claim_reminder(conn, uid, today):
            return False
        if paused or db.logged_on(conn, uid, today):
            return False
        if snoozed and not regular:
            db.update_user(conn, uid, last_reminded_on=today)  # so a bare number still counts as the day total
        said = self.say(user, "reminder") or "🐱 Nothing logged today. What did you spend?"
        self.tg.send_message(uid, f"{escape(said, quote=False)}\n\nReply with entries (<code>lunch 12, grab 9</code>) "
                                  "or just today's total (<code>45</code>).", reply_markup=reminder_keyboard())
        return True
