"""M2 features end to end: FX, reminders, merchant rules, personas, budgets, transfers.

Uses the same fixtures as test_bot_flow (real Postgres, fake Telegram/Claude/rates).
"""
from datetime import date, datetime, timedelta, timezone

import pytest

from meow.models import WalletInfo
from meow.transfers import TransferRequest, parse_transfer
from tests.parser_cases import make_context
from tests.test_bot_flow import ME, buttons, env  # noqa: F401  (fixture)

pytestmark = pytest.mark.skipif(__import__("os").getenv("TEST_DATABASE_URL") is None, reason="TEST_DATABASE_URL not set")

SGT = timezone(timedelta(hours=8))


def at(env, hh, mm, day=29):
    env.clock.now = datetime(2026, 9, day, hh, mm, tzinfo=SGT).astimezone(timezone.utc)


def one(env, sql, *args):
    return env.conn.execute(sql, args).fetchone()


# --- FX ------------------------------------------------------------------------------

def test_every_entry_gets_a_home_amount(env):
    env.say("/start")
    env.say("pho 65k, coffee 5 usd, kopi 1.8")
    rows = env.conn.execute("select currency, amount_home from transactions order by id").fetchall()
    assert [(r["currency"], r["amount_home"]) for r in rows] == [("VND", -325), ("USD", -625), ("SGD", -180)]
    assert env.fx_calls == ["SGD"]  # fetched once, then reused


def test_rates_down_then_backfilled_by_tick(env):
    env.say("/start")
    env.bot.fx_fetch = lambda base: (_ for _ in ()).throw(RuntimeError("API down"))
    card = env.say("pho 65k")
    assert "65,000₫" in card.text and "≈" not in card.text
    assert one(env, "select amount_home from transactions")["amount_home"] is None
    env.bot.fx_fetch = lambda base: {"VND": __import__("decimal").Decimal(26000)}
    stats = env.bot.run_tick(env.conn)
    assert stats["backfilled"] == 1
    assert one(env, "select amount_home from transactions")["amount_home"] == -250


# --- reminders ---------------------------------------------------------------------

def test_reminder_sent_once_when_nothing_logged(env):
    env.say("/start")
    at(env, 21, 0)
    assert env.bot.run_tick(env.conn)["reminders"] == 0  # too early
    at(env, 21, 32)
    sent_before = len(env.tg.sent)
    assert env.bot.run_tick(env.conn)["reminders"] == 1
    msg = env.tg.sent[-1]
    assert len(env.tg.sent) == sent_before + 1 and msg.chat_id == ME
    assert "eod:nospend" in buttons(msg.markup)
    at(env, 21, 47)
    assert env.bot.run_tick(env.conn)["reminders"] == 0  # not twice


def test_no_reminder_if_logged_paused_or_off(env):
    env.say("/start")
    at(env, 12, 0)
    env.say("lunch 12")
    at(env, 21, 31)
    assert env.bot.run_tick(env.conn)["reminders"] == 0
    # next day: paused
    at(env, 9, 0, day=30)
    assert "paused" in env.say("/remind pause 3").text
    at(env, 21, 31, day=30)
    assert env.bot.run_tick(env.conn)["reminders"] == 0


def test_reminder_window_and_custom_time(env):
    env.say("/start")
    assert "22:15" in env.say("/remind 22:15").text
    at(env, 21, 45)
    assert env.bot.run_tick(env.conn)["reminders"] == 0
    at(env, 22, 16)
    assert env.bot.run_tick(env.conn)["reminders"] == 1


def test_missed_window_sends_nothing(env):
    env.say("/start")
    at(env, 1, 0, day=30)  # well after the 3-hour window of the previous evening
    assert env.bot.run_tick(env.conn)["reminders"] == 0


def test_no_spend_button(env):
    env.say("/start")
    at(env, 21, 31)
    env.bot.run_tick(env.conn)
    msg = env.tg.sent[-1]
    edit = env.press("eod:nospend", msg.id)
    assert "No-spend day saved" in edit.text
    assert one(env, "select kind from day_marks")["kind"] == "no_spend"
    assert "No-spend day" in env.say("/today").text


def test_snooze_one_hour(env):
    env.say("/start")
    at(env, 21, 31)
    env.bot.run_tick(env.conn)
    env.press("eod:snooze", env.tg.sent[-1].id)
    at(env, 22, 0)
    assert env.bot.run_tick(env.conn)["reminders"] == 0
    at(env, 22, 33)
    assert env.bot.run_tick(env.conn)["reminders"] == 1
    at(env, 23, 40)
    assert env.bot.run_tick(env.conn)["reminders"] == 0


def test_bare_number_after_reminder_is_day_total(env):
    env.say("/start")
    at(env, 21, 31)
    env.bot.run_tick(env.conn)
    card = env.say("45")
    assert "Day total" in card.text and "S$45.00" in card.text
    assert env.llm.calls == []
    assert one(env, "select source from transactions")["source"] == "eod"


def test_pause_button(env):
    env.say("/start")
    at(env, 21, 31)
    env.bot.run_tick(env.conn)
    msg = env.tg.sent[-1]
    assert "eod:pause:7" in buttons(env.press("eod:pausemenu", msg.id).markup)
    assert "back on Tue 06 Oct" in env.press("eod:pause:7", msg.id).text  # paused 29 Sep - 5 Oct
    assert one(env, "select reminders_paused_until from users")["reminders_paused_until"] == date(2026, 10, 5)


# --- merchant rules ------------------------------------------------------------------

def test_learns_after_two_corrections(env):
    env.say("/start")
    fun = one(env, "select id from categories where name = 'Entertainment'")["id"]
    for i in range(2):
        card = env.say(f"grab {10 + i}")
        tx = int(next(b for b in buttons(card.markup) if b.startswith("cat:")).split(":")[1])
        env.press(f"setcat:{tx}:{fun}", card.id)
    assert env.tg.answers[-1] == '🧠 Learned: "grab" → Entertainment from now on.'
    card = env.say("grab 9")
    assert "Entertainment" in card.text
    assert "grab → Entertainment" in env.say("/rules").text
    env.say("/rules forget grab")
    assert "Transport" in env.say("grab 8").text


# --- personas ------------------------------------------------------------------------

def test_persona_flow_and_plain_mode(env):
    env.say("/start")
    msg = env.say("/persona")
    assert "pers:mom" in buttons(msg.markup)
    edit = env.press("pers:mom", msg.id)
    assert "roast:3" in buttons(edit.markup)
    edit = env.press("roast:3", msg.id)
    assert "Sample:" in edit.text
    assert one(env, "select persona, roast_level from users") == {"persona": "mom", "roast_level": 3}
    card = env.say("bubble tea 7")
    assert "<i>" in card.text
    env.press("pers:plain", env.say("/persona").id)
    assert "<i>" not in env.say("kopi 2").text


def test_big_expense_gets_big_comment(env):
    env.say("/start")
    card = env.say("dinner 120")
    assert "S$120.00" in card.text.split("<i>")[1]  # the persona line names the amount


def test_language_setting(env):
    env.say("/start")
    msg = env.say("/language")
    env.press("lang:vi", msg.id)
    assert one(env, "select language from users")["language"] == "vi"


# --- budgets -------------------------------------------------------------------------

def test_budget_alerts_at_80_and_100(env):
    env.say("/start")
    assert "S$20.00" in env.say("/budget Food 20").text
    assert "budget" not in env.say("lunch 14").text            # 70%
    warn = env.say("coffee 3").text                              # 85%
    assert "⚠️ <b>Food &amp; Drinks</b> budget: S$17.00 / S$20.00 (85%)" in warn
    assert "budget" not in env.say("kopi 1").text               # 90%, no new crossing
    over = env.say("cake 4").text                                # 110%
    assert "🚨 <b>Food &amp; Drinks</b> budget: S$22.00 / S$20.00 (110%)" in over
    listing = env.say("/budget").text
    assert "▓" in listing and "(110%)" in listing
    assert "budget 110%" in env.say("/month").text
    assert "Removed" in env.say("/budget Food off").text


def test_total_budget_and_bad_category(env):
    env.say("/start")
    env.say("/budget total 100")
    assert "Total" in env.say("dinner 85").text
    assert "don't have a category" in env.say("/budget Snacks 50").text


# --- transfers & wallets ------------------------------------------------------------

def ctx_with_cash():
    ctx = make_context()
    ctx.wallets.append(WalletInfo(9, "Cash", "SGD"))
    return ctx


@pytest.mark.parametrize("text,src,dst,amount", [
    ("move 200 from DBS to Cash", "DBS", "Cash", "200"),
    ("transfer 50 dbs to cash", "DBS", "Cash", "50"),
    ("chuyển 2tr từ VP sang DBS", "VP", "DBS", "2000000"),
    ("top up 30 cash from dbs", "DBS", "Cash", "30"),
])
def test_parse_transfer(text, src, dst, amount):
    t = parse_transfer(text, ctx_with_cash())
    assert isinstance(t, TransferRequest)
    assert (t.from_wallet.name, t.to_wallet.name, str(t.amount)) == (src, dst, amount)


def test_parse_transfer_errors_and_non_transfers():
    ctx = make_context()
    assert "Revolut" in parse_transfer("move 50 from DBS to Revolut", ctx)
    assert parse_transfer("movie 12", ctx) is None
    assert parse_transfer("chuyển nhà 20", ctx) is None            # moving house
    assert parse_transfer("chuyển tiền cho Vũ 500k", ctx) is None  # money given to someone
    assert parse_transfer("grab 12", ctx) is None
    w = parse_transfer("withdraw 100 from dbs", ctx)
    assert isinstance(w, TransferRequest) and w.to_wallet is None and w.to_wallet_name == "cash"


def test_transfers_move_money_but_are_not_spending(env):
    env.say("/start")
    env.say("/setbalance DBS 1000")
    assert "Added" in env.say("/wallet add Cash SGD cash").text
    card = env.say("move 200 from DBS to Cash")
    assert "🔁 <b>Transfer</b>" in card.text and "S$200.00 DBS → Cash" in card.text
    bal = env.say("/balance").text
    assert "DBS: <b>S$800.00</b>" in bal and "Cash: <b>S$200.00</b>" in bal
    assert "Nothing logged this month" in env.say("/month").text
    env.press(next(b for b in buttons(card.markup) if b.startswith("undo:")), card.id)
    assert "DBS: <b>S$1,000.00</b>" in env.say("/balance").text


def test_cross_currency_transfer_estimated_and_exact(env):
    env.say("/start")
    est = env.say("move 100 from DBS to VP")
    assert "S$100.00 DBS → VP (2,000,000₫)" in est.text and "today's rate" in est.text
    exact = env.say("move 500 from DBS to VP as 9.8tr")
    assert "(9,800,000₫)" in exact.text and "today's rate" not in exact.text
    assert "VP: <b>11,800,000₫</b>" in env.say("/balance").text


def test_withdraw_creates_cash_wallet(env):
    env.say("/start")
    card = env.say("withdraw 100 from DBS")
    assert "DBS → Cash" in card.text
    assert one(env, "select name, currency, type from wallets where name = 'Cash'") == {
        "name": "Cash", "currency": "SGD", "type": "cash"}


def test_wallet_default_and_validation(env):
    env.say("/start")
    env.say("/wallet add GrabPay SGD ewallet")
    assert "default" in env.say("/wallet default GrabPay").text
    assert "GrabPay" in env.say("lunch 12").text
    assert "one word" in env.say("/wallet add Grab-Pay! SGD").text
    assert "already have" in env.say("/wallet add grabpay SGD").text


def test_settings_summary(env):
    env.say("/start")
    s = env.say("/settings").text
    assert "Sassy Cat" in s and "21:30" in s and "SGD" in s
