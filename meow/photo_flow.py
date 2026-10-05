"""Photos and screenshots: read them, log what's clear, ask about the rest.

Asked first (buttons), never logged straight away:
- dup:    the same amount and currency is already logged within a day (a week if the photo has no date)
- own:    money sent to or from the user's own name (probably moving money between their accounts)
- person: money sent to a person (spending? own account? don't log?)
- big:    a large amount (PHOTO_CONFIRM_ABOVE in home currency)
"""
from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from html import escape
from typing import Optional

from . import db, vision
from .cards import btn
from .models import Entry, ParseContext
from .money import fmt, to_minor
from .parser_llm import cost_usd
from .parser_rules import learned_category, normalize
from .transfers import TransferRequest

log = logging.getLogger(__name__)

FLAG_ORDER = ("dup", "own", "person", "big")
IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}


def same_person(a: str, b: str) -> bool:
    """'TRINH MINH TAM' == 'Trịnh Minh Tâm' == 'Tam Trinh Minh' (word order and accents ignored)."""
    wa = {normalize(w) for w in a.split() if w.strip()}
    wb = {normalize(w) for w in b.split() if w.strip()}
    return len(wa) >= 2 and wa == wb


def image_of(msg: dict) -> Optional[tuple[str, str, int]]:
    """(file_id, media type, size) for a photo, or an image sent as a file (uncompressed screenshot)."""
    if msg.get("photo"):
        p = msg["photo"][-1]  # the largest size
        return p["file_id"], "image/jpeg", p.get("file_size") or 0
    doc = msg.get("document") or {}
    if doc.get("mime_type") in IMAGE_TYPES:
        return doc["file_id"], doc["mime_type"], doc.get("file_size") or 0
    return None


class PhotoFlow:
    """Mixed into Bot."""

    # ------------------------------------------------------------------ reading
    def on_photo(self, conn, user: dict, chat_id: int, msg: dict, image: tuple[str, str, int]) -> None:
        file_id, media_type, size = image
        uid = user["telegram_id"]
        if size > vision.MAX_IMAGE_BYTES:
            self.tg.send_message(chat_id, "That picture is too large (over 5 MB). A normal screenshot works best.")
            return
        if not self.llm_ok(conn, uid):
            self.tg.send_message(chat_id, "I can't read photos right now 😿 Type it instead, e.g. <code>lunch 12</code>.")
            return
        self.tg.send_chat_action(chat_id, "typing")
        data = self.tg.download_file(file_id)
        if len(data) > vision.MAX_IMAGE_BYTES:
            self.tg.send_message(chat_id, "That picture is too large (over 5 MB). A normal screenshot works best.")
            return

        today = self.today_for(user)
        ctx = self.context(conn, user, today)
        caption = (msg.get("caption") or "").strip() or None
        seen, call = vision.read_image(self.llm, self.s.vision_model, data, media_type, caption, ctx,
                                       user.get("full_name"))
        db.log_llm_call(conn, uid, "vision", call, cost_usd(call, self.s.llm_input_price, self.s.llm_output_price))
        if seen is None:
            self.tg.send_message(chat_id, "😿 I couldn't read that picture. Try again, or type it like <code>lunch 12</code>.")
            return
        if not seen.payments:
            self.tg.send_message(chat_id, "🔍 " + escape(seen.question or "I couldn't find a finished payment in that picture."))
            return

        raw = "[photo]" + (f" {caption}" if caption else "")
        clear: list[Entry] = []
        guessed_any = False
        for p in seen.payments:
            entry, flags, dup = self.check_seen(conn, user, ctx, p)
            if entry is None:
                continue
            if flags:
                pid = db.add_pending(conn, uid, "photo", entry.model_dump(mode="json"), p.counterparty, p.note,
                                     flags, dup, p.date is None)
                self.ask_pending(conn, user, chat_id, pid)
            else:
                clear.append(entry)
                guessed_any = guessed_any or p.date is None
        if clear:
            note = "📸 Read from your photo" + (" · no date on it, so I used today" if guessed_any else "")
            self.save_entries(conn, user, chat_id, ctx, clear, raw, parser="vision", source="photo", extra_note=note)

    def check_seen(self, conn, user: dict, ctx: ParseContext, p: vision.SeenPayment
                   ) -> tuple[Optional[Entry], list[str], Optional[str]]:
        """Turn what the model saw into an entry, and list what to ask about first."""
        uid, home = user["telegram_id"], user["home_currency"]
        wallet = ctx.wallet_by_name(p.wallet) if p.wallet else None
        if wallet and wallet.currency != p.currency:
            wallet = None
        entry = Entry(amount=p.amount, currency=p.currency, type=p.type, category=p.category,
                      description=p.description, date=p.date or ctx.today, wallet=wallet.name if wallet else None)
        learned = learned_category(entry.description, ctx)  # the user's own rules beat the model
        cat = ctx.category(learned) if learned else None
        if cat and cat.type == entry.type:
            entry.category = cat.name

        rows, question = self.resolve(conn, user, ctx, [entry])
        if question or not rows:
            log.info("photo entry skipped: %s", question)
            return None, [], None
        row = rows[0]
        entry.wallet = next(w.name for w in ctx.wallets if w.id == row["wallet_id"])

        flags: list[str] = []
        dup = db.find_duplicate(conn, uid, entry.currency, row["amount_minor"], entry.type, entry.date,
                                window_days=7 if p.date is None else 1)
        dup_text = None
        if dup:
            flags.append("dup")
            dup_text = (f"{dup['description'] or '(no note)'} · {fmt(abs(dup['amount_minor']), dup['currency'])} · "
                        f"{dup['wallet_name']} · {dup['occurred_on'].strftime('%a %d %b')}")
        owner = user.get("full_name")
        if p.counterparty and owner and same_person(p.counterparty, owner):
            flags.append("own")
        elif p.counterparty_kind == "person" and entry.type == "expense":
            flags.append("person")
        big = to_minor(Decimal(str(self.s.photo_confirm_above)), home)
        if not {"own", "person"} & set(flags) and row["amount_home"] is not None and abs(row["amount_home"]) >= big:
            flags.append("big")
        return entry, flags, dup_text

    # ------------------------------------------------------------------ asking
    def pending_text(self, item: dict) -> str:
        e = Entry.model_validate(item["entry"])
        amount = fmt(to_minor(e.amount, e.currency), e.currency)
        when = e.date.strftime("%a %d %b") + (" (no date on the photo, so I used today)" if item["date_guessed"] else "")
        who = escape(item["counterparty"] or e.description)
        note = f"\nNote: “{escape(item['note'])}”" if item.get("note") else ""
        flag = item["flags"][0]
        if flag == "dup":
            return (f"📸 <b>{escape(e.description)}</b> {amount} · {escape(e.wallet or '')} · {when}\n\n"
                    f"This looks already logged:\n• {escape(item['dup_of'] or '')}\n\nLog it again?")
        if flag == "own":
            arrow = "from" if e.type == "income" else "to"
            return (f"📸 {amount} {arrow} <b>{who}</b> · {escape(e.wallet or '')} · {when}{note}\n\n"
                    "That's your own name. Moving money between your accounts?")
        if flag == "person":
            return (f"📸 {amount} sent to <b>{who}</b> · {escape(e.wallet or '')} · {when}{note}\n\n"
                    "What was it?")
        return (f"📸 <b>{escape(e.description)}</b> {amount} · {escape(e.category)} · {escape(e.wallet or '')} · {when}\n\n"
                "That's a big one, so I'm checking first. Log it?")

    def pending_keyboard(self, item: dict) -> dict:
        i, flag = item["id"], item["flags"][0]
        skip = btn("🚫 Don't log", f"pi:{i}:skip")
        if flag == "dup":
            rows = [[btn("➕ Log anyway", f"pi:{i}:keep"), btn("🚫 Skip", f"pi:{i}:skip")]]
        elif flag == "own":
            rows = [[btn("🔁 Between my accounts", f"pi:{i}:own")], [btn("💸 Spending", f"pi:{i}:spend"), skip]]
        elif flag == "person":
            rows = [[btn("💸 Spending", f"pi:{i}:spend"), btn("🔁 My own account", f"pi:{i}:own")], [skip]]
        else:
            rows = [[btn("✅ Log it", f"pi:{i}:log"), btn("✏️ Category", f"pi:{i}:spend")], [skip]]
        return {"inline_keyboard": rows}

    def ask_pending(self, conn, user: dict, chat_id: int, item_id: int) -> None:
        item = db.get_pending(conn, item_id)
        sent = self.tg.send_message(chat_id, self.pending_text(item), reply_markup=self.pending_keyboard(item))
        if sent:
            db.update_pending(conn, item_id, message_id=sent["message_id"])

    # ------------------------------------------------------------------ buttons
    def on_pending_button(self, conn, uid: int, rest: str, chat_id: int, message_id: int) -> Optional[str]:
        sid, _, action = rest.partition(":")
        item = db.get_pending(conn, int(sid))
        if not item or item["user_id"] != uid:
            return "That isn't available."
        if item["status"] != "open":
            self.tg.edit_message_reply_markup(chat_id, message_id, None)
            return "Already done."
        user = db.get_user(conn, uid)
        entry = Entry.model_validate(item["entry"])
        act, _, arg = action.partition(":")

        if act == "skip":
            db.update_pending(conn, item["id"], status="skipped")
            self.tg.edit_message_text(chat_id, message_id, self.pending_text(item) + "\n\n🚫 <i>Not logged.</i>", None)
            return "Not logged"
        if act == "back":
            self.tg.edit_message_reply_markup(chat_id, message_id, self.pending_keyboard(item))
            return None
        if act == "keep":  # not a duplicate after all: move on to the next check, or log
            flags = [f for f in item["flags"] if f != "dup"]
            db.update_pending(conn, item["id"], flags=flags)
            if flags:
                item["flags"] = flags
                self.tg.edit_message_text(chat_id, message_id, self.pending_text(item), self.pending_keyboard(item))
                return None
            return self.log_pending(conn, user, item, entry, chat_id, message_id)
        if act == "log":
            return self.log_pending(conn, user, item, entry, chat_id, message_id)
        if act == "spend":
            type_ = "expense" if entry.type == "expense" else "income"
            cats = [c for c in db.categories(conn, uid) if c.type == type_]
            buttons = [btn(f"{c.emoji} {c.name}", f"pi:{item['id']}:c:{c.id}") for c in cats]
            rows = [buttons[k:k + 3] for k in range(0, len(buttons), 3)] + [[btn("« Back", f"pi:{item['id']}:back")]]
            self.tg.edit_message_reply_markup(chat_id, message_id, {"inline_keyboard": rows})
            return None
        if act == "c":
            cat = next((c for c in db.categories(conn, uid) if c.id == int(arg)), None)
            if not cat:
                return "Unknown category."
            entry.type = cat.type
            entry.category = cat.name
            return self.log_pending(conn, user, item, entry, chat_id, message_id)
        if act == "own":
            others = [w for w in db.wallets(conn, uid) if w.name != entry.wallet]
            buttons = [btn(f"{w.name} ({w.currency})", f"pi:{item['id']}:w:{w.id}") for w in others]
            rows = [buttons[k:k + 2] for k in range(0, len(buttons), 2)] + [[btn("« Back", f"pi:{item['id']}:back")]]
            self.tg.edit_message_text(chat_id, message_id, self.pending_text(item) + "\n\n"
                                      + ("Which account did it come from?" if entry.type == "income"
                                         else "Which account did it go to?"), {"inline_keyboard": rows})
            return None
        if act == "w":
            return self.move_pending(conn, user, item, entry, int(arg), chat_id, message_id)
        return "Unknown button."

    def log_pending(self, conn, user: dict, item: dict, entry: Entry, chat_id: int, message_id: int) -> str:
        db.update_pending(conn, item["id"], status="logged", entry=entry.model_dump(mode="json"))
        self.tg.edit_message_text(chat_id, message_id, self.pending_text(item) + "\n\n✅ <i>Logged below.</i>", None)
        ctx = self.context(conn, user, self.today_for(user))
        note = "📸 Read from your photo" + (" · no date on it, so I used today" if item["date_guessed"] else "")
        self.save_entries(conn, user, chat_id, ctx, [entry], "[photo]", parser="vision", source="photo",
                          extra_note=note)
        return "Logged"

    def move_pending(self, conn, user: dict, item: dict, entry: Entry, wallet_id: int,
                     chat_id: int, message_id: int) -> str:
        ctx = self.context(conn, user, self.today_for(user))
        here = ctx.wallet_by_name(entry.wallet or "")
        other = next((w for w in ctx.wallets if w.id == wallet_id), None)
        if not here or not other:
            return "Unknown account."
        src, dst = (other, here) if entry.type == "income" else (here, other)
        db.update_pending(conn, item["id"], status="moved")
        self.tg.edit_message_text(chat_id, message_id, self.pending_text(item)
                                  + f"\n\n🔁 <i>Recorded as a move {escape(src.name)} → {escape(dst.name)}.</i>", None)
        t = TransferRequest(amount=entry.amount, currency=entry.currency, vnd_hint=False,
                            from_wallet=src, to_wallet=dst, to_wallet_name=None)
        self.do_transfer(conn, user, chat_id, ctx, t, "[photo]", on=entry.date)
        return "Recorded as a move"
