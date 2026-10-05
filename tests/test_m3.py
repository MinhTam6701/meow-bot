"""M3 end to end: Mochi, streak, monthly report, balance check."""
from datetime import date, datetime, timedelta, timezone

import pytest

from tests.support import ME, buttons

pytestmark = pytest.mark.skipif(__import__("os").getenv("TEST_DATABASE_URL") is None, reason="TEST_DATABASE_URL not set")
SGT = timezone(timedelta(hours=8))


def at(env, y, mo, d, hh=12, mm=0):
    env.clock.now = datetime(y, mo, d, hh, mm, tzinfo=SGT).astimezone(timezone.utc)


def one(env, sql, *args):
    return env.conn.execute(sql, args).fetchone()


def setup_mochi(env, day=(2026, 10, 5)):
    at(env, *day)
    env.say("/start")
    env.conn.execute("update users set reminders_on = false")
    msg = env.say("/budget everyday 900")
    assert "S$29.03" in msg.text  # 900 / 31 days
    return msg


def test_everyday_budget_starts_mochi_and_pins_status(env):
    setup_mochi(env)
    assert one(env, "select weight, started_on from mochi_state") == {"weight": 50, "started_on": date(2026, 10, 5)}
    pinned = env.tg.sent[-1]
    assert pinned.silent and "Mochi 50/100" in pinned.text and env.tg.pinned == pinned.id
    card = env.say("lunch 12")
    assert "😸 Mochi 50/100 ▓▓▓▓▓░░░░░ Healthy · bowl S$17.03 left" in card.text


def test_nightly_scoring_and_verdict(env):
    setup_mochi(env)
    env.say("lunch 12")                     # 41% of the bowl -> +2
    at(env, 2026, 10, 6, 0, 15)
    stats = env.bot.run_tick(env.conn)
    assert stats["mochi"] == 1
    assert one(env, "select result, delta, weight_after from mochi_log") == {"result": "half", "delta": 2, "weight_after": 52}
    verdict = next(m for m in env.tg.sent if "verdict" in m.text)
    assert verdict.silent and "+2" in verdict.text
    assert env.bot.run_tick(env.conn).get("mochi", 0) == 0   # not twice
    at(env, 2026, 10, 7, 0, 15)                               # 6 Oct: nothing logged
    env.bot.run_tick(env.conn)
    assert one(env, "select result, weight_after from mochi_log where day = '2026-10-06'") == {"result": "silent", "weight_after": 50}


def test_bills_and_planned_dont_feed_mochi(env):
    setup_mochi(env)
    env.say("/recurring add rent 800 on 6")
    at(env, 2026, 10, 6, 9)
    env.bot.run_tick(env.conn)              # rent logs itself on the 6th
    env.say("shopee 1200 #planned")
    env.say("kopi 2")
    assert one(env, "select count(*) n from transactions where occurred_on = '2026-10-06'")["n"] == 3
    at(env, 2026, 10, 7, 0, 15)
    env.bot.run_tick(env.conn)
    row = one(env, "select spent_minor, result from mochi_log where day = '2026-10-06'")
    assert row == {"spent_minor": 200, "result": "half"}


def test_streak_milestone_on_the_card(env):
    at(env, 2026, 10, 3)
    env.say("/start")
    env.say("kopi 2")
    at(env, 2026, 10, 4)
    env.say("kopi 2")
    at(env, 2026, 10, 5)
    card = env.say("kopi 2")
    assert "3-day streak!" in card.text
    assert "3-day streak!" not in env.say("teh 1.5").text  # only once
    assert "<b>3-day streak</b>" in env.say("/streak").text


def test_monthly_report_and_balance_check(env):
    at(env, 2026, 9, 10)
    env.say("/start")
    env.conn.execute("update users set reminders_on = false")
    env.say("/setbalance DBS 1000")
    env.say("salary 1350 to DBS")
    env.say("lunch 12, taxi 20, grab 15, dinner 30")
    env.say("taxi 18")
    env.say("taxi 19")
    env.say("/wallet add Savings VND")
    env.conn.execute("update wallets set check_monthly = false where name = 'Savings'")

    at(env, 2026, 10, 1, 8, 45)
    assert env.bot.run_tick(env.conn).get("reports", 0) == 0   # before 09:00
    at(env, 2026, 10, 1, 9, 0)
    assert env.bot.run_tick(env.conn)["reports"] == 1
    rep = next(m for m in env.tg.sent if "monthly report" in m.text)
    assert "September 2026" in rep.text and "Spent <b>S$114.00</b>" in rep.text
    assert "Top 3" in rep.text and "Fun facts" in rep.text and "“taxi” × 3" in rep.text
    assert env.bot.run_tick(env.conn).get("reports", 0) == 0   # once a month

    first = env.tg.sent[-1]
    assert "DBS</b> should be <b>S$2,236.00</b>" in first.text
    env.press(next(b for b in buttons(first.markup) if b.startswith("rc:ok:")), first.id)
    second = env.tg.sent[-1]
    assert "VP</b> should be" in second.text            # Savings is excluded
    env.press(next(b for b in buttons(second.markup) if b.startswith("rc:diff:")), second.id)
    assert "What does your bank app show" in env.tg.edits[-1].text
    fixed = env.say("150000")
    done = env.tg.sent[-1]
    assert "VP set to <b>150,000₫</b>" in env.tg.sent[-2].text
    assert "Balance check done" in done.text and "1/2 matched" in done.text
    assert "VP: <b>150,000₫</b>" in env.say("/balance").text
    recs = env.conn.execute("select status, difference from reconciliations order by wallet_id").fetchall()
    assert [r["status"] for r in recs] == ["matched", "adjusted"] and recs[1]["difference"] == 150000
    assert fixed


def test_report_command_and_check_command(env):
    at(env, 2026, 9, 10)
    env.say("/start")
    env.say("lunch 12")
    at(env, 2026, 10, 3)
    assert "September 2026" in env.say("/report").text
    assert "September 2026" in env.say("/report 2026-09").text
    msg = env.say("/check")
    assert "DBS</b> should be" in msg.text


def test_typing_text_while_check_is_waiting_logs_normally(env):
    at(env, 2026, 10, 3)
    env.say("/start")
    msg = env.say("/check")
    env.press(next(b for b in buttons(msg.markup) if b.startswith("rc:diff:")), msg.id)
    card = env.say("lunch 12")
    assert "Logged" in card.text
    assert one(env, "select awaiting from users")["awaiting"] is None


def test_mochi_command_without_budget_explains(env):
    env.say("/start")
    assert "/budget everyday 900" in env.say("/mochi").text


def test_pin_notices_and_bots_are_ignored(env):
    env.say("/start")
    before = len(env.tg.sent)
    pin_notice = {"message_id": 9, "chat": {"id": ME, "type": "private"},
                  "from": {"id": 777, "is_bot": True, "first_name": "meow_bot"},
                  "pinned_message": {"message_id": 8}}
    env.bot.process_update(env.conn, {"update_id": 99999, "message": pin_notice})
    user_pin = {"message_id": 10, "chat": {"id": ME, "type": "private"},
                "from": {"id": ME, "first_name": "Tam"}, "pinned_message": {"message_id": 8}}
    env.bot.process_update(env.conn, {"update_id": 99998, "message": user_pin})
    assert len(env.tg.sent) == before


def test_choose_wallets_for_the_balance_check(env):
    env.say("/start")
    env.say("/wallet add VCB VND")
    assert "VP, VCB" in env.say("/wallet check").text or "DBS, VP, VCB" in env.say("/wallet check").text
    assert "DBS, VCB" in env.say("/wallet check dbs vcb").text
    assert "🏦 checked monthly" in env.say("/wallet").text
    assert "don't have a wallet called Revolut" in env.say("/wallet check DBS Revolut").text
    at(env, 2026, 10, 3)
    msg = env.say("/check")
    assert "DBS</b> should be" in msg.text
    env.press(next(b for b in buttons(msg.markup) if b.startswith("rc:ok:")), msg.id)
    assert "VCB</b> should be" in env.tg.sent[-1].text  # VP was skipped
