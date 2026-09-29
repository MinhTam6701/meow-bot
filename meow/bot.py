"""Update handling: messages, commands and button presses.

The same Bot is used by the Vercel webhook and by the local polling script.
"""
from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from html import escape
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo

from . import db
from .cards import category_picker, render_card, totals_line, wallet_picker
from .config import Settings
from .models import Entry, ParseContext
from .money import fmt, parse_amount_token, to_minor
from .parser import has_amount, parse_message
from .parser_llm import cost_usd
from .parser_rules import parse_with_rules
from .telegram import TelegramAPI

log = logging.getLogger(__name__)

HELP = """<b>How to log</b>
Just type what you spent or received:
• <code>pho 65k</code>
• <code>grab 12.5 yesterday</code>
• <code>coffee 6, lunch 14, taxi 9</code>
• <code>salary 4200 to DBS</code>
• <code>+50 refund</code>

Plain numbers are SGD. <code>65k</code>, <code>65.000</code> or <code>1tr2</code> are VND.
Name a wallet (DBS, VP) to use it; otherwise SGD goes to DBS and VND to VP.

<b>Commands</b>
/today – today's entries
/month – this month by category
/balance – wallet balances
/setbalance DBS 2340.50 – set a wallet's current balance
/undo – undo the last entry
/help – this message"""

COMMANDS = [
    ("today", "Today's entries"),
    ("month", "This month by category"),
    ("balance", "Wallet balances"),
    ("setbalance", "Set a wallet balance, e.g. /setbalance DBS 2340.50"),
    ("undo", "Undo the last entry"),
    ("help", "How to log"),
]


class Bot:
    def __init__(self, settings: Settings, tg: TelegramAPI, llm_client: Any = None,
                 clock: Optional[Callable[[], datetime]] = None):
        self.s = settings
        self.tg = tg
        self.llm = llm_client
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    # ------------------------------------------------------------------ entry point
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

    def log_money(self, conn, user: dict, chat_id: int, text: str) -> None:
        uid = user["telegram_id"]
        today = self.today_for(user)
        ctx = self.context(conn, user, today)

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
        if self.llm is not None:
            self.tg.send_chat_action(chat_id)
        result = parse_message(to_parse, ctx, llm_client=self.llm, model=self.s.anthropic_model,
                               on_llm_call=llm_logs.append, llm_allowed=llm_allowed)
        for call in llm_logs:
            db.log_llm_call(conn, uid, "parse", call,
                            cost_usd(call, self.s.llm_input_price, self.s.llm_output_price))

        if not result.entries:
            db.set_pending(conn, uid, to_parse if result.parser == "llm" else None)
            self.tg.send_message(chat_id, escape(result.question or "I couldn't read that. Try /help."))
            return

        rows, question = self.resolve(conn, uid, ctx, result.entries)
        if question:
            db.set_pending(conn, uid, to_parse)
            self.tg.send_message(chat_id, escape(question))
            return

        db.set_pending(conn, uid, None)
        batch_id = db.insert_transactions(conn, uid, rows, raw_message=to_parse, parser=result.parser)
        text_out, keyboard = render_card(db.get_batch(conn, batch_id), today, db.spent_on(conn, uid, today))
        sent = self.tg.send_message(chat_id, text_out, reply_markup=keyboard)
        if sent:
            db.set_card(conn, batch_id, chat_id, sent["message_id"])

    def context(self, conn, user: dict, today: date) -> ParseContext:
        uid = user["telegram_id"]
        return ParseContext(today=today, home_currency=user["home_currency"],
                            wallets=db.wallets(conn, uid), categories=db.categories(conn, uid))

    def resolve(self, conn, uid: int, ctx: ParseContext, entries: list[Entry]) -> tuple[list[dict], Optional[str]]:
        """Turn parsed entries into ledger rows. Returns (rows, question)."""
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
                    wallet = db.create_cash_wallet(conn, uid, e.currency)
                    ctx.wallets.append(wallet)

            cat = next((c for c in ctx.categories if c.name == e.category and c.type == e.type), None)
            if cat is None:
                fallback = "Other income" if e.type == "income" else "Other"
                cat = next((c for c in ctx.categories if c.name == fallback), None)

            minor = to_minor(e.amount, e.currency)
            if minor == 0:
                return [], f"\"{e.description}\" came out as zero. What was the amount?"
            rows.append({
                "wallet_id": wallet.id,
                "category_id": cat.id if cat else None,
                "type": e.type,
                "amount_minor": minor if e.type == "income" else -minor,
                "currency": e.currency,
                "description": e.description,
                "occurred_on": e.date,
            })
        return rows, None

    # ------------------------------------------------------------------ commands
    def cmd_start(self, conn, user, chat_id, args, created=False):
        name = escape(user.get("first_name") or "there")
        intro = (f"🐱 Hi {name}! I'm <b>M.E.O.W.</b>, your Expense &amp; Outflow Watcher.\n\n"
                 "I've set up two wallets: <b>DBS</b> (SGD) and <b>VP</b> (VND), plus some starter categories.\n"
                 "Tip: set your real balances first, e.g. <code>/setbalance DBS 2340.50</code>\n\n")
        if not created:
            intro = f"🐱 Welcome back, {name}!\n\n"
        self.tg.send_message(chat_id, intro + HELP)

    def cmd_help(self, conn, user, chat_id, args, **_):
        self.tg.send_message(chat_id, HELP)

    def cmd_today(self, conn, user, chat_id, args, **_):
        uid, today = user["telegram_id"], self.today_for(user)
        entries = db.entries_on(conn, uid, today)
        if not entries:
            self.tg.send_message(chat_id, "📅 <b>Today</b>\n\nNothing logged yet today.")
            return
        lines = [f"📅 <b>Today</b> · {today.strftime('%a %d %b')}", ""]
        for e in entries:
            sign = "+" if e["type"] == "income" else ""
            lines.append(f"{e['category_emoji'] or '•'} {escape(e['description'] or '')} — "
                         f"{sign}{fmt(abs(e['amount_minor']), e['currency'])} · {escape(e['wallet_name'])}")
        lines += ["", totals_line(db.spent_on(conn, uid, today))]
        self.tg.send_message(chat_id, "\n".join(lines))

    def cmd_month(self, conn, user, chat_id, args, **_):
        uid, today = user["telegram_id"], self.today_for(user)
        first = today.replace(day=1)
        nxt = (first + timedelta(days=32)).replace(day=1)
        rows = db.month_summary(conn, uid, first, nxt)
        title = f"📊 <b>{first.strftime('%B %Y')}</b>"
        if not rows:
            self.tg.send_message(chat_id, f"{title}\n\nNothing logged this month yet.")
            return
        lines = [title]
        for cur in sorted({r["currency"] for r in rows}):
            exp = [r for r in rows if r["currency"] == cur and r["type"] == "expense"]
            inc = [r for r in rows if r["currency"] == cur and r["type"] == "income"]
            spent = -sum(r["total"] for r in exp)
            earned = sum(r["total"] for r in inc)
            lines += ["", f"<b>{cur}</b>", f"Spent {fmt(spent, cur)}"]
            for r in exp:
                share = f" ({-r['total'] * 100 // spent}%)" if spent > 0 else ""
                lines.append(f"  {r['emoji']} {escape(r['category'])} {fmt(-r['total'], cur)}{share}")
            if earned:
                lines.append(f"Income +{fmt(earned, cur)}")
                net = earned - spent
                lines.append(f"Net {'+' if net >= 0 else ''}{fmt(net, cur)}")
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
        if len(parts) != 2:
            self.tg.send_message(chat_id, usage)
            return
        wallet = next((w for w in db.wallets(conn, uid) if w.name.lower() == parts[0].lower()), None)
        token = parse_amount_token(parts[1])
        if wallet is None or token is None:
            names = ", ".join(w.name for w in db.wallets(conn, uid))
            self.tg.send_message(chat_id, f"{usage}\nYour wallets: {escape(names)}")
            return
        target = to_minor(token.value, wallet.currency)
        diff = target - db.wallet_balance(conn, wallet.id)
        if diff == 0:
            self.tg.send_message(chat_id, f"{escape(wallet.name)} is already {fmt(target, wallet.currency)} ✅")
            return
        db.insert_transactions(conn, uid, [{
            "wallet_id": wallet.id, "category_id": None, "type": "adjustment", "amount_minor": diff,
            "currency": wallet.currency, "description": f"Balance set to {fmt(target, wallet.currency)}",
            "occurred_on": self.today_for(user),
        }], raw_message=f"/setbalance {args}", parser="command", source="command")
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
            else:
                note = "Unknown button."
        finally:
            self.tg.answer_callback_query(cq["id"], note)

    def refresh_card(self, conn, uid: int, batch_id, chat_id: int, message_id: int) -> None:
        user = db.get_user(conn, uid)
        today = self.today_for(user)
        text, kb = render_card(db.get_batch(conn, batch_id), today, db.spent_on(conn, uid, today))
        self.tg.edit_message_text(chat_id, message_id, text, kb)
