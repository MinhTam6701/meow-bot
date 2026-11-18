"""/setup: home currency, the currencies you use, your wallets in each, the default wallet for each
currency, and the day your month starts. New users get it from /start; anyone can run it again.

Also the tap-to-pick default wallet (/wallet default) and /monthstart.

Each step edits one message. The state lives in users.setup (jsonb):
    {"step": "home" | "cur" | "wal" | "def" | "day", "cur": ["SGD", "VND"], "i": 0,
     "pick": ["DBS", "OCBC"], "new": true, "msg": 123}
`i` is the currency whose wallets are being picked, `pick` the wallets ticked for it.
"""
from __future__ import annotations

import re
from html import escape
from typing import Optional

from . import db, periods
from .cards import btn
from .money import CURRENCY_ALIASES
from .parser import has_amount

# Currencies the parser and formatter know, in the order they're offered.
CURRENCIES = ["SGD", "VND", "USD", "MYR", "EUR", "GBP", "AUD", "THB", "JPY", "KRW", "IDR"]
PRESETS = {
    "SGD": ["DBS", "POSB", "OCBC", "UOB", "Trust", "GXS", "YouTrip", "GrabPay", "Cash"],
    "VND": ["VCB", "VPBank", "Techcombank", "MB", "BIDV", "ACB", "TPBank", "MoMo", "Cash"],
    "MYR": ["Maybank", "CIMB", "PublicBank", "TnG", "Cash"],
}
OTHER_PRESETS = ["Wise", "Revolut", "Cash"]
EWALLETS = {"grabpay", "momo", "tng", "youtrip", "revolut", "wise", "zalopay", "shopeepay", "paylah", "gopay"}
MONTH_DAYS = [1, 25, 26, 27, 28]
WALLET_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,19}")
assert set(CURRENCIES) <= set(CURRENCY_ALIASES.values())


def presets(cur: str, home: str = "") -> list[str]:
    """Wallets offered for a currency. Cash outside the home currency is "Cash VND", like the cash
    wallet the bot makes by itself when an entry arrives in a new currency."""
    return [f"Cash {cur}" if p == "Cash" and cur != home else p for p in PRESETS.get(cur, OTHER_PRESETS)]


def wallet_type(name: str) -> str:
    n = name.lower()
    return "cash" if n.startswith("cash") else "ewallet" if n in EWALLETS else "bank"


def day_keyboard(prefix: str, current: int) -> dict:
    """Month start buttons: `prefix` is "su:day" in /setup and "ms" for /monthstart."""
    def label(d: int) -> str:
        text = "1st (calendar month)" if d == 1 else periods.ordinal(d)
        return ("● " if d == current else "") + text
    rows = [[btn(label(1), f"{prefix}:1")], [btn(label(d), f"{prefix}:{d}") for d in MONTH_DAYS[1:]]]
    rows.append([btn("✏️ Another day", f"{prefix}:type")])
    return {"inline_keyboard": rows}


def default_keyboard(prefix: str, wallets: list) -> dict:
    buttons = [btn(("● " if w.is_default else "") + w.name, f"{prefix}:{w.id}") for w in wallets]
    return {"inline_keyboard": [buttons[i:i + 3] for i in range(0, len(buttons), 3)]}


class SetupFlow:
    """Mixed into Bot."""

    # ------------------------------------------------------------------ start
    def cmd_setup(self, conn, user, chat_id, args, created=False, **_):
        self.start_setup(conn, user, chat_id, new=created)

    def start_setup(self, conn, user: dict, chat_id: int, new: bool = False) -> None:
        uid = user["telegram_id"]
        currencies = [user["home_currency"]] + sorted({w.currency for w in db.wallets(conn, uid)}
                                                      - {user["home_currency"]})
        # The home currency can only change before anything is logged: every entry is stored in it too.
        step = "cur" if db.has_entries(conn, uid) else "home"
        state = {"step": step, "cur": currencies, "i": 0, "pick": [], "new": new}
        text, kb = self.setup_view(conn, user, state)
        sent = self.tg.send_message(chat_id, text, reply_markup=kb)
        state["msg"] = sent["message_id"] if sent else None
        db.set_setup(conn, uid, state)

    # ------------------------------------------------------------------ what each step shows
    def setup_view(self, conn, user: dict, st: dict) -> tuple[str, dict]:
        home = user["home_currency"]
        step = st["step"]
        if step == "home":
            buttons = [btn(("● " if c == home else "") + c, f"su:home:{c}") for c in CURRENCIES]
            return ("💱 <b>Your home currency</b>\n"
                    "Which currency do you mostly spend in? Plain amounts like <code>lunch 12</code> are read in it, "
                    "and reports add everything up in it.",
                    {"inline_keyboard": [buttons[i:i + 4] for i in range(0, len(buttons), 4)]})
        if step == "cur":
            locked = self.locked_currencies(conn, user)
            buttons = [btn(("✅ " if c in st["cur"] else "") + c, f"su:cur:{c}") for c in CURRENCIES]
            rows = [buttons[i:i + 4] for i in range(0, len(buttons), 4)] + [[btn("Next »", "su:next")]]
            return (f"💱 <b>Which currencies do you use?</b>\nTap to add or remove. {home} is your home currency"
                    + (f"; {', '.join(sorted(locked - {home}))} already have wallets" if locked - {home} else "")
                    + ".", {"inline_keyboard": rows})
        if step == "wal":
            cur = st["cur"][st["i"]]
            have = [w.name for w in db.wallets(conn, user["telegram_id"]) if w.currency == cur]
            options = self.wallet_options(cur, home, have, st["pick"])
            have_l = {h.lower() for h in have}
            buttons = [btn(("✅ " if o in st["pick"] or o.lower() in have_l else "") + o, f"su:wal:{n}")
                       for n, o in enumerate(options)]
            rows = [buttons[i:i + 3] for i in range(0, len(buttons), 3)]
            rows.append([btn("✏️ Type a name", "su:type"), btn("Next »", "su:next")])
            count = f" ({st['i'] + 1}/{len(st['cur'])})" if len(st["cur"]) > 1 else ""
            text = (f"👛 <b>Your {cur} wallets</b>{count}\n"
                    f"Tap the banks, cards, e-wallets and cash you pay with in {cur}. Not listed? Type a name.")
            if have:
                text += f"\nAlready set up: {escape(', '.join(have))}"
            return text, {"inline_keyboard": rows}
        if step == "def":
            cur = st["cur"][st["i"]]
            ws = [w for w in db.wallets(conn, user["telegram_id"]) if w.currency == cur]
            return (f"⭐ <b>Your default {cur} wallet</b>\n"
                    f"When you don't name a wallet, {cur} entries go here. You can always name one: "
                    f"<code>lunch 12 {escape(ws[-1].name if ws else 'DBS')}</code>.",
                    default_keyboard("su:def", ws))
        sd = periods.start_day(user)
        return ("📅 <b>When does your month start?</b>\n"
                "Budgets, Mochi's bowl, /month and the monthly report follow your month. "
                "Pick the day after payday: paid on the 25th → start on the 26th.\n"
                "The bank balance check stays on the 1st.",
                day_keyboard("su:day", sd))

    def locked_currencies(self, conn, user: dict) -> set[str]:
        """The home currency and any currency that already has a wallet can't be untapped."""
        return {user["home_currency"]} | {w.currency for w in db.wallets(conn, user["telegram_id"])}

    @staticmethod
    def wallet_options(cur: str, home: str, have: list[str], pick: list[str]) -> list[str]:
        out = presets(cur, home)
        lower = {o.lower() for o in out}
        for name in have + pick:
            if name.lower() not in lower:
                out.append(name)
                lower.add(name.lower())
        return out

    def show_setup(self, conn, user: dict, chat_id: int, st: dict, message_id: Optional[int], new_message: bool = False):
        text, kb = self.setup_view(conn, user, st)
        if new_message or not message_id:
            sent = self.tg.send_message(chat_id, text, reply_markup=kb)
            st["msg"] = sent["message_id"] if sent else None
        else:
            self.tg.edit_message_text(chat_id, message_id, text, kb)
        db.set_setup(conn, user["telegram_id"], st)

    # ------------------------------------------------------------------ buttons
    def on_setup_button(self, conn, uid: int, rest: str, chat_id: int, message_id: int) -> Optional[str]:
        user = db.get_user(conn, uid)
        st = user.get("setup")
        if not st:
            return "Setup is finished. Run /setup to start again."
        if st.get("msg") and message_id != st["msg"]:
            return "Use the latest setup message (or /setup to start again)."
        what, _, value = rest.partition(":")

        if what == "home" and st["step"] == "home" and value in CURRENCIES:
            if db.has_entries(conn, uid):
                return "You have entries already, so the home currency stays."
            db.update_user(conn, uid, home_currency=value)
            user = db.get_user(conn, uid)
            st["cur"] = [value] + [c for c in st["cur"] if c != value and c in self.locked_currencies(conn, user)]
            st["step"] = "cur"
            self.show_setup(conn, user, chat_id, st, message_id)
            return f"Home currency: {value}"

        if what == "cur" and st["step"] == "cur" and value in CURRENCIES:
            if value in st["cur"]:
                if value in self.locked_currencies(conn, user):
                    return f"{value} stays: " + ("it's your home currency." if value == user["home_currency"]
                                                 else "you have wallets in it.")
                st["cur"].remove(value)
            else:
                st["cur"].append(value)
            self.show_setup(conn, user, chat_id, st, message_id)
            return None

        if what == "next" and st["step"] == "cur":
            st["i"] = 0
            return self.enter_wallets(conn, user, chat_id, st, message_id)

        if what == "wal" and st["step"] == "wal" and value.isdigit():
            cur = st["cur"][st["i"]]
            have = [w.name for w in db.wallets(conn, uid) if w.currency == cur]
            options = self.wallet_options(cur, user["home_currency"], have, st["pick"])
            if int(value) >= len(options):
                return None
            name = options[int(value)]
            if name.lower() in {h.lower() for h in have}:
                return f"{name} is already set up."
            if name in st["pick"]:
                st["pick"].remove(name)
            else:
                st["pick"].append(name)
            self.show_setup(conn, user, chat_id, st, message_id)
            return None

        if what == "type" and st["step"] == "wal":
            cur = st["cur"][st["i"]]
            db.set_awaiting(conn, uid, f"setup:name:{cur}")
            self.tg.send_message(chat_id, f"✏️ Type the {cur} wallet name, one word each, e.g. "
                                          "<code>Citibank</code> or <code>Citibank, Amex</code>")
            return None

        if what == "next" and st["step"] == "wal":
            return self.save_wallets(conn, user, chat_id, st, message_id)

        if what == "def" and st["step"] == "def" and value.isdigit():
            cur = st["cur"][st["i"]]
            w = next((w for w in db.wallets(conn, uid) if w.id == int(value) and w.currency == cur), None)
            if w is None:
                return "That wallet isn't available."
            db.set_default_wallet(conn, uid, w.id)
            self.next_currency(conn, user, chat_id, st, message_id)
            return f"{w.name} is your default {cur} wallet"

        if what == "day" and st["step"] == "day":
            if value == "type":
                db.set_awaiting(conn, uid, "setup:day")
                self.tg.send_message(chat_id, "✏️ Type the day your month starts, from 1 to 28 (e.g. <code>26</code>).")
                return None
            if value.isdigit() and periods.MIN_DAY <= int(value) <= periods.MAX_DAY:
                db.set_month_start(conn, uid, int(value))
                self.finish_setup(conn, db.get_user(conn, uid), chat_id, st, message_id)
                return "All set ✅"
        return "That step is done. Use the latest setup message."

    # ------------------------------------------------------------------ steps
    def enter_wallets(self, conn, user: dict, chat_id: int, st: dict, message_id: Optional[int],
                      new_message: bool = False) -> None:
        st["step"], st["pick"] = "wal", []
        self.show_setup(conn, user, chat_id, st, message_id, new_message)

    def save_wallets(self, conn, user: dict, chat_id: int, st: dict, message_id: Optional[int]) -> Optional[str]:
        """Create the wallets ticked for this currency, then ask for its default if there's a choice."""
        uid = user["telegram_id"]
        cur = st["cur"][st["i"]]
        if not st["pick"] and not any(w.currency == cur for w in db.wallets(conn, uid)):
            return f"Pick at least one {cur} wallet (Cash is fine)."
        taken = db.all_wallet_names(conn, uid)
        same = {w.name.lower() for w in db.wallets(conn, uid) if w.currency == cur}
        for name in st["pick"]:
            if name.lower() in taken and taken[name.lower()][1] != cur:
                name = f"{name} {cur}"  # "Wise" in USD and EUR
            if name.lower() in same:
                continue  # already set up: leave it (and its default) alone
            if name.lower() in taken:
                name = taken[name.lower()][0]  # an archived wallet comes back: keep its spelling
            if db.add_setup_wallet(conn, uid, name, wallet_type(name), cur):
                taken[name.lower()] = (name, cur)
        ws = [w for w in db.wallets(conn, uid) if w.currency == cur]
        if len(ws) >= 2:
            st["step"] = "def"
            self.show_setup(conn, user, chat_id, st, message_id)
            return None
        if ws and not ws[0].is_default:
            db.set_default_wallet(conn, uid, ws[0].id)
        self.next_currency(conn, user, chat_id, st, message_id)
        return None

    def next_currency(self, conn, user: dict, chat_id: int, st: dict, message_id: Optional[int],
                      new_message: bool = False) -> None:
        st["i"] += 1
        if st["i"] < len(st["cur"]):
            self.enter_wallets(conn, user, chat_id, st, message_id, new_message)
            return
        st["step"] = "day"
        self.show_setup(conn, user, chat_id, st, message_id, new_message)

    def finish_setup(self, conn, user: dict, chat_id: int, st: dict, message_id: Optional[int],
                     new_message: bool = False) -> None:
        uid, home = user["telegram_id"], user["home_currency"]
        db.set_setup(conn, uid, None)
        ws = db.wallets(conn, uid)
        lines = ["✅ <b>All set!</b>", "", f"Home currency: <b>{home}</b>"]
        for cur in [home] + sorted({w.currency for w in ws} - {home}):
            names = [escape(w.name) + (" ⭐" if w.is_default else "") for w in ws if w.currency == cur]
            if names:
                lines.append(f"{cur}: " + ", ".join(names))
        sd = periods.start_day(user)
        lines.append(f"Month starts on {periods.describe(sd)}: now "
                     f"{periods.label(*periods.bounds(self.today_for(user), sd))}")
        first = next((w for w in ws if w.is_default and w.currency == home), ws[0] if ws else None)
        lines += ["", "⭐ = default wallet for that currency.",
                  f"Next: match your balances, e.g. <code>/setbalance {escape(first.name) if first else 'DBS'} 2340.50</code>",
                  "Change any of this later with /setup, /wallet or /monthstart."]
        text = "\n".join(lines)
        if new_message or not message_id:
            self.tg.send_message(chat_id, text)
        else:
            self.tg.edit_message_text(chat_id, message_id, text, None)
        if st.get("new"):
            from .bot import HELP
            self.tg.send_message(chat_id, HELP)

    # ------------------------------------------------------------------ typed answers
    def on_setup_text(self, conn, user: dict, chat_id: int, text: str) -> bool:
        """A typed wallet name or month start day. False = not an answer: handle it as a normal message."""
        uid, kind = user["telegram_id"], user["awaiting"]
        db.set_awaiting(conn, uid, None)
        st = user.get("setup")
        if not st:
            return False
        if kind == "setup:day" and st["step"] == "day":
            m = re.fullmatch(r"(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)?", text.strip().lower())
            if not m:
                return False
            day = int(m[1])
            if not periods.MIN_DAY <= day <= periods.MAX_DAY:
                db.set_awaiting(conn, uid, "setup:day")
                self.tg.send_message(chat_id, "Pick a day from 1 to 28, so every month has it. "
                                              "Paid on the 28th–31st? Start your month on the 1st.")
                return True
            db.set_month_start(conn, uid, day)
            self.finish_setup(conn, db.get_user(conn, uid), chat_id, st, None, new_message=True)
            return True
        if kind.startswith("setup:name:") and st["step"] == "wal":
            cur = kind.split(":")[2]
            if has_amount(text) or cur != st["cur"][st["i"]]:
                return False
            names = [n for n in re.split(r"[,\s]+", text.strip()) if n]
            bad = [n for n in names if not WALLET_NAME.fullmatch(n)]
            if not names or bad:
                db.set_awaiting(conn, uid, kind)
                self.tg.send_message(chat_id, "Wallet names are one word (letters and numbers), e.g. "
                                              "<code>Citibank</code>. Try again?")
                return True
            taken = db.all_wallet_names(conn, uid)
            clash = [n for n in names if n.lower() in taken and taken[n.lower()][1] != cur]
            if clash:
                db.set_awaiting(conn, uid, kind)
                self.tg.send_message(chat_id, f"You already have a {taken[clash[0].lower()][1]} wallet called "
                                              f"{escape(clash[0])}. Pick another name?")
                return True
            for n in names:
                if n.lower() not in {p.lower() for p in st["pick"]}:
                    st["pick"].append(n)
            self.show_setup(conn, user, chat_id, st, None, new_message=True)
            return True
        return False

    # ------------------------------------------------------------------ default wallet anywhere
    def on_wallet_default_button(self, conn, uid: int, rest: str, chat_id: int, message_id: int) -> Optional[str]:
        """wd:<currency> shows the picker; wd:s:<wallet id> sets it."""
        what, _, value = rest.partition(":")
        ws = db.wallets(conn, uid)
        if what == "s" and value.isdigit():
            w = next((w for w in ws if w.id == int(value)), None)
            if w is None:
                return "That wallet isn't available."
            db.set_default_wallet(conn, uid, w.id)
            self.tg.edit_message_text(chat_id, message_id,
                                      f"⭐ <b>{escape(w.name)}</b> is now your default {w.currency} wallet.\n"
                                      f"{w.currency} entries without a wallet name go there.", None)
            return "Saved"
        cur = what
        choices = [w for w in ws if w.currency == cur]
        if len(choices) < 2:
            return f"You only have one {cur} wallet."
        self.tg.edit_message_text(chat_id, message_id, f"⭐ Which wallet should be your default for <b>{cur}</b>?",
                                  default_keyboard("wd:s", choices))
        return None

    def default_buttons(self, conn, uid: int) -> Optional[dict]:
        """One "⭐ Default SGD: DBS" button per currency that has a choice."""
        ws = db.wallets(conn, uid)
        rows = []
        for cur in dict.fromkeys(w.currency for w in ws):
            mine = [w for w in ws if w.currency == cur]
            if len(mine) >= 2:
                d = next((w.name for w in mine if w.is_default), "none")
                rows.append([btn(f"⭐ Default {cur}: {d} · change", f"wd:{cur}")])
        return {"inline_keyboard": rows} if rows else None

    # ------------------------------------------------------------------ /monthstart
    def cmd_monthstart(self, conn, user, chat_id, args, **_):
        sd = periods.start_day(user)
        arg = args.strip().lower()
        if not arg:
            now = periods.label(*periods.bounds(self.today_for(user), sd))
            self.tg.send_message(chat_id, f"📅 Your month starts on <b>{periods.describe(sd)}</b> (now: {now}).\n"
                                          "Budgets, Mochi's bowl, /month, /report and the charts follow it; the bank "
                                          "balance check stays on the 1st.\n\n"
                                          "Pick the day after payday, or type it: <code>/monthstart 26</code>",
                                  reply_markup=day_keyboard("ms", sd))
            return
        m = re.fullmatch(r"(\d{1,2})(?:st|nd|rd|th)?", arg)
        if not m or not periods.MIN_DAY <= int(m[1]) <= periods.MAX_DAY:
            self.tg.send_message(chat_id, "Pick a day from 1 to 28, e.g. <code>/monthstart 26</code>. "
                                          "Paid on the 28th–31st? Start your month on the 1st.")
            return
        self.tg.send_message(chat_id, self.set_month_start(conn, user, int(m[1])))

    cmd_payday = cmd_monthstart

    def set_month_start(self, conn, user: dict, day: int) -> str:
        uid = user["telegram_id"]
        db.set_month_start(conn, uid, day)
        user = db.get_user(conn, uid)
        today = self.today_for(user)
        now = periods.label(*periods.bounds(today, day))
        self.update_pinned_quietly(conn, user)
        return (f"📅 Your month now starts on <b>{periods.describe(day)}</b>. This month: <b>{now}</b>.\n"
                "Budgets, Mochi's bowl, /month, /report and the charts follow it. "
                "The bank balance check stays on the 1st.")

    def on_month_start_button(self, conn, uid: int, value: str, chat_id: int, message_id: int) -> Optional[str]:
        user = db.get_user(conn, uid)
        if value == "type":
            self.tg.edit_message_text(chat_id, message_id, "✏️ Send it like <code>/monthstart 26</code> (1 to 28).", None)
            return None
        if not value.isdigit() or not periods.MIN_DAY <= int(value) <= periods.MAX_DAY:
            return "Unknown option."
        self.tg.edit_message_text(chat_id, message_id, self.set_month_start(conn, user, int(value)), None)
        return "Saved"
