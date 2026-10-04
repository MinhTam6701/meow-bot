"""End-to-end tests of the bot against a real Postgres (not Supabase).

Set TEST_DATABASE_URL to a throwaway database; the tests wipe its public schema.
Skipped when it isn't set.
"""
import os
import random
from decimal import Decimal
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import psycopg
import pytest
from psycopg.rows import dict_row

from meow.bot import Bot
from meow.config import Settings

DB_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DB_URL, reason="TEST_DATABASE_URL not set")

ME = 1001
STRANGER = 2002
SCHEMA = "\n".join(p.read_text() for p in sorted((Path(__file__).parent.parent / "supabase/migrations").glob("*.sql")))
# 22:30 in Singapore on 29 Sep 2026
NOW = datetime(2026, 9, 29, 14, 30, tzinfo=timezone.utc)


class FakeTelegram:
    def __init__(self):
        self.sent, self.edits, self.answers, self.next_id = [], [], [], 500

    def send_message(self, chat_id, text, reply_markup=None):
        self.next_id += 1
        self.sent.append(SimpleNamespace(chat_id=chat_id, text=text, markup=reply_markup, id=self.next_id))
        return {"message_id": self.next_id}

    def edit_message_text(self, chat_id, message_id, text, reply_markup=None):
        self.edits.append(SimpleNamespace(kind="text", message_id=message_id, text=text, markup=reply_markup))

    def edit_message_reply_markup(self, chat_id, message_id, reply_markup=None):
        self.edits.append(SimpleNamespace(kind="markup", message_id=message_id, text=None, markup=reply_markup))

    def answer_callback_query(self, cq_id, text=None):
        self.answers.append(text)

    def send_chat_action(self, *a, **k):
        pass


class FakeLLM:
    """Returns a canned tool call, or a question if nothing is queued."""

    def __init__(self):
        self.queue, self.calls = [], []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        data = self.queue.pop(0) if self.queue else {"entries": [], "question": "How much was it?"}
        block = SimpleNamespace(type="tool_use", input=data)
        return SimpleNamespace(content=[block], usage=SimpleNamespace(input_tokens=1000, output_tokens=100))


@pytest.fixture
def env():
    conn = psycopg.connect(DB_URL, row_factory=dict_row, autocommit=True)
    conn.execute("drop schema public cascade; create schema public;")
    conn.execute(SCHEMA)
    conn.autocommit = False
    tg, llm = FakeTelegram(), FakeLLM()
    settings = Settings(allowed_user_ids={ME}, anthropic_model="claude-haiku-4-5")
    clock = SimpleNamespace(now=NOW)
    fx_calls = []

    def fake_rates(base):
        fx_calls.append(base)
        return {"SGD": Decimal(1), "VND": Decimal(20000), "USD": Decimal("0.8")}

    bot = Bot(settings, tg, llm, clock=lambda: clock.now, fx_fetch=fake_rates, rng=random.Random(1))
    counter = iter(range(1, 10_000))

    def say(text, user=ME):
        bot.process_update(conn, {"update_id": next(counter), "message": {
            "message_id": 1, "chat": {"id": user, "type": "private"},
            "from": {"id": user, "first_name": "Tam"}, "text": text}})
        return tg.sent[-1] if tg.sent else None

    def press(data, message_id, user=ME):
        bot.process_update(conn, {"update_id": next(counter), "callback_query": {
            "id": "cb", "from": {"id": user}, "data": data,
            "message": {"message_id": message_id, "chat": {"id": user}}}})
        return tg.edits[-1] if tg.edits else None

    yield SimpleNamespace(conn=conn, tg=tg, llm=llm, bot=bot, say=say, press=press, clock=clock, fx_calls=fx_calls)
    conn.close()


def buttons(markup):
    return [b["callback_data"] for row in markup["inline_keyboard"] for b in row]


def test_start_seeds_wallets_and_categories(env):
    msg = env.say("/start")
    assert "DBS" in msg.text and "VP" in msg.text
    wallets = env.conn.execute("select name, currency, is_default from wallets order by id").fetchall()
    assert [(w["name"], w["currency"], w["is_default"]) for w in wallets] == [("DBS", "SGD", True), ("VP", "VND", True)]
    assert env.conn.execute("select count(*) n from categories").fetchone()["n"] == 19
    env.say("/start")  # idempotent
    assert env.conn.execute("select count(*) n from wallets").fetchone()["n"] == 2


def test_stranger_is_turned_away(env):
    msg = env.say("pho 65k", user=STRANGER)
    assert "private" in msg.text
    assert env.conn.execute("select count(*) n from users").fetchone()["n"] == 0


def test_log_multi_entry_message(env):
    env.say("/start")
    card = env.say("coffee 6, lunch 14, pho 65k")
    assert "Logged 3 entries" in card.text
    assert "S$6.00" in card.text and "65,000₫" in card.text
    assert "65,000₫ (≈ S$3.25)" in card.text  # 20,000 VND per SGD in the fake rates
    assert "Today: S$23.25 spent" in card.text
    rows = env.conn.execute("select t.amount_minor, t.currency, w.name wallet from transactions t join wallets w on w.id = t.wallet_id order by t.id").fetchall()
    assert [(r["amount_minor"], r["currency"], r["wallet"]) for r in rows] == [
        (-600, "SGD", "DBS"), (-1400, "SGD", "DBS"), (-65000, "VND", "VP")]
    assert env.llm.calls == []  # all handled by rules
    assert any(b.startswith("undo:") for b in buttons(card.markup))


def test_undo_button_appends_reversal(env):
    env.say("/start")
    card = env.say("grab 12.5")
    undo = next(b for b in buttons(card.markup) if b.startswith("undo:"))
    edit = env.press(undo, card.id)
    assert "Undone" in edit.text and edit.markup == {"inline_keyboard": []}
    rows = env.conn.execute("select amount_minor, reverses_id from transactions order by id").fetchall()
    assert [r["amount_minor"] for r in rows] == [-1250, 1250] and rows[1]["reverses_id"] is not None
    # a second press does nothing more
    env.press(undo, card.id)
    assert env.conn.execute("select count(*) n from transactions").fetchone()["n"] == 2
    assert "Nothing" in env.say("/today").text


def test_change_category_and_wallet(env):
    env.say("/start")
    env.conn.execute("insert into wallets (user_id, name, type, currency) values (%s, 'Cash', 'cash', 'SGD')", (ME,))
    card = env.say("netflix 19.98")
    tx_id = int(next(b for b in buttons(card.markup) if b.startswith("cat:")).split(":")[1])

    picker = env.press(f"cat:{tx_id}", card.id)
    bills = next(b for b in buttons(picker.markup) if b.startswith(f"setcat:{tx_id}:") and "Housing" in str(picker.markup))
    bills_id = env.conn.execute("select id from categories where name = 'Housing'").fetchone()["id"]
    edit = env.press(f"setcat:{tx_id}:{bills_id}", card.id)
    assert "Housing" in edit.text

    wal_picker = env.press(f"wal:{tx_id}", card.id)
    assert len([b for b in buttons(wal_picker.markup) if b.startswith("setwal:")]) == 2  # DBS + Cash, not VP
    cash_id = env.conn.execute("select id from wallets where name = 'Cash'").fetchone()["id"]
    edit = env.press(f"setwal:{tx_id}:{cash_id}", card.id)
    assert "Cash" in edit.text
    vp_id = env.conn.execute("select id from wallets where name = 'VP'").fetchone()["id"]
    env.press(f"setwal:{tx_id}:{vp_id}", card.id)  # VND wallet for an SGD entry: refused
    assert env.tg.answers[-1] == "Couldn't change that."
    assert bills


def test_other_users_cannot_touch_my_entries(env):
    env.say("/start")
    card = env.say("grab 12.5")
    undo = next(b for b in buttons(card.markup) if b.startswith("undo:"))
    env.bot.s.allowed_user_ids.add(STRANGER)
    env.press(undo, card.id, user=STRANGER)
    assert env.conn.execute("select count(*) n from transactions").fetchone()["n"] == 1


def test_llm_fallback_logs_cost_and_saves(env):
    env.say("/start")
    env.llm.queue.append({"entries": [{"amount": "23", "currency": "SGD", "type": "expense", "category": "Other",
                                       "description": "haircut", "date": "2026-09-29"}]})
    card = env.say("paid 23 for the thing at the market")
    assert "haircut" in card.text and "S$23.00" in card.text
    call = env.conn.execute("select * from llm_calls").fetchone()
    assert call["ok"] and call["input_tokens"] == 1000 and float(call["cost_usd"]) == pytest.approx(0.0015)
    assert env.conn.execute("select parser from transactions").fetchone()["parser"] == "llm"


def test_clarifying_question_then_answer(env):
    env.say("/start")
    q = env.say("taxi twenty 5 5")  # rules can't read it; the fake LLM asks a question
    assert q.text == "How much was it?"
    assert env.conn.execute("select pending_input from users").fetchone()["pending_input"] == "taxi twenty 5 5"
    env.llm.queue.append({"entries": [{"amount": "20", "currency": "SGD", "type": "expense", "category": "Transport",
                                       "description": "taxi", "date": "2026-09-29"}]})
    card = env.say("20 sgd")
    assert "S$20.00" in card.text
    assert "Answer to your question: 20 sgd" in env.llm.calls[-1]["messages"][0]["content"]
    assert env.conn.execute("select pending_input from users").fetchone()["pending_input"] is None


def test_wallet_currency_mismatch_asks(env):
    env.say("/start")
    msg = env.say("pho 65 vp")
    assert "VP holds VND" in msg.text
    assert env.conn.execute("select count(*) n from transactions").fetchone()["n"] == 0


def test_unknown_currency_gets_a_cash_wallet(env):
    env.say("/start")
    env.say("coffee 5 usd")
    assert env.conn.execute("select name, currency from wallets where currency = 'USD'").fetchone() == {"name": "Cash USD", "currency": "USD"}


def test_setbalance_balance_month_undo(env):
    env.say("/start")
    assert "now <b>S$2,340.50</b>" in env.say("/setbalance DBS 2,340.50").text
    env.say("/setbalance vp 12m")
    env.say("salary 4200 to DBS")
    env.say("lunch 14")
    env.say("grab 45k")
    bal = env.say("/balance").text
    assert "DBS: <b>S$6,526.50</b>" in bal and "VP: <b>11,955,000₫</b>" in bal

    month = env.say("/month").text
    # Everything in SGD: lunch 14 + grab 45k VND (= S$2.25 at 20,000/SGD)
    assert "September 2026" in month and "Spent <b>S$16.25</b>" in month and "Income <b>+S$4,200.00</b>" in month
    assert "Net +S$4,183.75" in month and "Paid in other currencies: 45,000₫" in month

    assert "grab" in env.say("/undo").text
    assert "VP: <b>12,000,000₫</b>" in env.say("/balance").text


def test_duplicate_update_is_ignored(env):
    env.say("/start")
    update = {"update_id": 777, "message": {"message_id": 1, "chat": {"id": ME, "type": "private"},
                                            "from": {"id": ME, "first_name": "Tam"}, "text": "kopi 1.8"}}
    env.bot.process_update(env.conn, update)
    env.bot.process_update(env.conn, update)
    assert env.conn.execute("select count(*) n from transactions").fetchone()["n"] == 1


def test_yesterday_is_in_user_timezone(env):
    env.say("/start")
    env.say("grab 12.5 yesterday")
    day = env.conn.execute("select occurred_on from transactions").fetchone()["occurred_on"]
    assert day.isoformat() == "2026-09-28"


def test_non_money_message_skips_llm(env):
    env.say("/start")
    msg = env.say("hello there")
    assert "didn't see an amount" in msg.text and env.llm.calls == []
