"""M2.1: categories, phrase memory, wallet nicknames, Claude examples, recurring, totals."""
from datetime import date, datetime, timedelta, timezone

import pytest

from meow.bot import next_monthly
from meow.text import phrase_keys
from tests.support import ME, buttons

pytestmark = pytest.mark.skipif(__import__("os").getenv("TEST_DATABASE_URL") is None, reason="TEST_DATABASE_URL not set")
SGT = timezone(timedelta(hours=8))


def at(env, y, mo, d, hh=9, mm=0):
    env.clock.now = datetime(y, mo, d, hh, mm, tzinfo=SGT).astimezone(timezone.utc)


def one(env, sql, *args):
    return env.conn.execute(sql, args).fetchone()


def test_phrase_keys():
    assert phrase_keys("Mua đồ nấu ăn") == ["do nau an", "do nau", "do"]
    assert phrase_keys("Tiền nhà 298 Punggol Central") == ["nha punggol central", "nha punggol", "nha"]
    assert phrase_keys("grab to work") == ["grab work", "grab"]


def test_next_monthly():
    assert next_monthly(date(2026, 10, 4), 1) == date(2026, 11, 1)
    assert next_monthly(date(2026, 10, 4), 15) == date(2026, 10, 15)
    assert next_monthly(date(2026, 1, 31), 31) == date(2026, 2, 28)
    assert next_monthly(date(2026, 12, 20), 5) == date(2027, 1, 5)


def test_learned_phrase_beats_keywords(env):
    env.say("/start")
    groceries = one(env, "select id from categories where name = 'Groceries'")["id"]
    env.conn.execute("insert into merchant_rules (user_id, keyword, category_id, corrections) values (%s, 'banh mi', %s, 5)",
                     (ME, groceries))
    assert "Groceries" in env.say("bánh mì 3").text         # learned phrase
    assert "Food &amp; Drinks" in env.say("bánh flan 3").text  # keyword table


def test_wallet_aliases(env):
    env.say("/start")
    env.say("/wallet add CashVND VND cash")
    env.conn.execute("update wallets set aliases = '{\"tiền mặt\"}' where name = 'CashVND'")
    card = env.say("phở 65k tiền mặt")
    assert "CashVND" in card.text
    card = env.say("cafe 30k bằng tiền mặt")
    assert "CashVND" in card.text


def test_claude_gets_similar_past_entries(env):
    env.say("/start")
    env.say("ăn trưa 6.5")
    env.say("taxi đi làm 18")
    env.llm.queue.append({"entries": [{"amount": "8", "currency": "SGD", "type": "expense",
                                       "category": "Food & Drinks", "description": "ăn trưa với team", "date": "2026-09-29"}]})
    env.say("ăn trưa với team xyz 8 2")  # two numbers -> rules give up -> Claude
    system = env.llm.calls[-1]["system"]
    assert '"ăn trưa" -> Food & Drinks' in system
    assert "taxi" not in system.split("How this user categorised")[1]  # only similar ones
    assert "Groceries: food bought to cook at home" in system


def test_balance_total_excludes_credit(env):
    env.say("/start")
    env.say("/setbalance DBS 1000")
    env.say("/setbalance VP 2tr")  # 2,000,000 VND = S$100 at the fake rate
    env.say("/wallet add VPCredit VND credit")
    env.conn.execute("update wallets set in_total = false where name = 'VPCredit'")
    env.say("shopee 400k vpcredit")
    bal = env.say("/balance").text
    assert "VPCredit: <b>-400,000₫</b> · not in total" in bal
    assert "Total ≈ <b>S$1,100.00</b>" in bal


def test_recurring_rent(env):
    env.say("/start")
    at(env, 2026, 10, 4)
    msg = env.say("/recurring add rent 800 on 1")
    assert "First one: Sun 01 Nov" in msg.text
    assert "rent" in env.say("/recurring").text
    at(env, 2026, 10, 31, 21, 0)
    assert env.bot.run_tick(env.conn)["recurring"] == 0
    at(env, 2026, 11, 1, 0, 15)
    assert env.bot.run_tick(env.conn)["recurring"] == 1
    card = env.tg.sent[-1]
    assert "S$800.00" in card.text and "Housing" in card.text and "recurring" in card.text
    assert env.bot.run_tick(env.conn)["recurring"] == 0  # not twice
    assert one(env, "select next_run from recurring_rules")["next_run"] == date(2026, 12, 1)
    # a recurring entry doesn't count as "logged today" for the check-in
    at(env, 2026, 11, 1, 21, 31)
    assert env.bot.run_tick(env.conn)["reminders"] == 1
    assert "Stopped" in env.say("/recurring stop 1").text


def test_budget_accepts_short_category_names(env):
    env.say("/start")
    assert "Food &amp; Drinks budget" in env.say("/budget food 300").text
    assert "Subscriptions &amp; Fees" in env.say("/budget subs 30").text
    assert "Be more specific" not in env.say("/budget groc 200").text


def test_rules_listing_and_forget_phrase(env):
    env.say("/start")
    cat = one(env, "select id from categories where name = 'Groceries'")["id"]
    env.conn.execute("insert into merchant_rules (user_id, keyword, category_id, corrections) values (%s, 'do nau an', %s, 9)",
                     (ME, cat))
    assert "do nau an → Groceries" in env.say("/rules nau").text
    assert "Forgot" in env.say("/rules forget đồ nấu ăn").text


def test_your_corrections_beat_imported_history(env):
    env.say("/start")
    transport = one(env, "select id from categories where name = 'Transport'")["id"]
    env.conn.execute("insert into merchant_rules (user_id, keyword, category_id, corrections) values (%s, 'chuyen nha', %s, 14)",
                     (ME, transport))
    assert "Transport" in env.say("chuyển nhà 20").text
    housing = one(env, "select id from categories where name = 'Housing'")["id"]
    for i in range(2):
        card = env.say(f"chuyển nhà {21 + i}")
        tx = int(next(b for b in buttons(card.markup) if b.startswith("cat:")).split(":")[1])
        env.press(f"setcat:{tx}:{housing}", card.id)
    assert "Housing" in env.say("chuyển nhà 30").text
