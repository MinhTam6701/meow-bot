"""Subscription detector: pure rules, then the daily job, buttons, reminders and /subscriptions."""
from datetime import date, datetime, timedelta, timezone

from meow import subscriptions as subs
from meow.subscriptions import Charge
from tests.support import ME, buttons

TODAY = date(2026, 9, 29)  # the fixture's clock: Tue 29 Sep 2026, 22:30 in Singapore


def ch(day, amount, desc="Netflix", cur="SGD", cat="Subscriptions & Fees"):
    return Charge(day=date.fromisoformat(day), amount=amount, currency=cur, description=desc, category=cat)


# --- pure rules ------------------------------------------------------------------

def test_key_ignores_month_words_numbers_and_accents():
    assert subs.key_of("Tiền điện thoại tháng 9") == subs.key_of("tiền điện thoại tháng 10") == "tien dien thoai"
    assert subs.key_of("Netflix Sept") == subs.key_of("netflix") == "netflix"


def test_add_interval_handles_month_ends():
    assert subs.add_interval(date(2026, 1, 31), "monthly") == date(2026, 2, 28)
    assert subs.add_interval(date(2026, 11, 15), "quarterly") == date(2027, 2, 15)
    assert subs.add_interval(date(2026, 9, 1), "weekly") == date(2026, 9, 8)
    assert subs.add_interval(date(2024, 2, 29), "yearly") == date(2025, 2, 28)


def test_monthly_with_small_price_wobble():
    (c,) = subs.detect([ch("2026-07-03", 1798), ch("2026-08-03", 1798), ch("2026-09-03", 1850)], TODAY)
    assert (c.interval, c.amount, len(c.charges), c.next_due_from(TODAY)) == ("monthly", 1850, 3, date(2026, 10, 3))


def test_two_charges_are_enough_but_must_be_recent():
    assert subs.detect([ch("2026-08-10", 999), ch("2026-09-10", 999)], TODAY)
    assert not subs.detect([ch("2026-03-12", 1657, "Loveable"), ch("2026-04-14", 1730, "Loveable")], TODAY)


def test_quarterly_like_vieon():
    (c,) = subs.detect([ch("2026-01-23", 285000, "Gia hạn VieON", "VND"), ch("2026-04-24", 285000, "Gia hạn VieON", "VND"),
                        ch("2026-07-24", 285000, "Gia hạn VieON", "VND")], TODAY)
    assert c.interval == "quarterly" and c.next_due_from(TODAY) == date(2026, 10, 24)


def test_not_subscriptions():
    irregular = [ch("2026-04-29", 18375, "Bảo hiểm", "VND"), ch("2026-08-03", 18375, "Bảo hiểm", "VND"),
                 ch("2026-09-28", 18375, "Bảo hiểm", "VND")]
    assert not subs.detect(irregular, TODAY)
    weekly_twice = [ch("2026-09-15", 2500, "Yoga"), ch("2026-09-22", 2500, "Yoga")]
    assert not subs.detect(weekly_twice, TODAY)  # weekly needs three
    food = [ch(f"2026-0{m}-05", 450, "Kopi", cat="Food & Drinks") for m in (7, 8, 9)]
    assert not subs.detect(food, TODAY)
    price_jump = [ch("2026-07-01", 1098, "Spotify"), ch("2026-08-01", 1098, "Spotify"), ch("2026-09-01", 1198, "Spotify")]
    assert not subs.detect(price_jump, TODAY)  # 9% apart: not "a similar amount"


def test_next_renewal_is_never_in_the_past_and_doesnt_drift():
    (c,) = subs.detect([ch("2026-07-20", 999), ch("2026-08-20", 999)], TODAY)  # 20 Sep has passed already
    assert c.next_due_from(TODAY) == date(2026, 10, 20)
    assert subs.next_on_or_after(date(2026, 1, 31), "monthly", date(2026, 4, 1)) == date(2026, 4, 30)  # not the 28th


def test_an_extra_charge_in_between_doesnt_hide_it():
    (c,) = subs.detect([ch("2026-07-01", 999), ch("2026-08-01", 999), ch("2026-08-15", 999), ch("2026-09-01", 999)], TODAY)
    assert c.interval == "monthly" and len(c.charges) == 3


def test_weekly_with_three():
    (c,) = subs.detect([ch("2026-09-08", 2500, "Yoga"), ch("2026-09-15", 2500, "Yoga"), ch("2026-09-22", 2500, "Yoga")], TODAY)
    assert c.interval == "weekly"


def test_per_month():
    assert subs.per_month(1200, "yearly") == 100 and subs.per_month(1000, "weekly") == 4333


# --- end to end --------------------------------------------------------------------

def add(env, desc, minor, day, cur="SGD", cat="Subscriptions & Fees", source="text"):
    w = env.conn.execute("select id from wallets where currency = %s order by id limit 1", (cur,)).fetchone()["id"]
    c = env.conn.execute("select id from categories where name = %s", (cat,)).fetchone()["id"]
    home = minor if cur == "SGD" else round(minor / 20000 * 100)
    env.conn.execute(
        """insert into transactions (user_id, batch_id, wallet_id, category_id, type, amount_minor, currency,
                                     description, occurred_on, source, amount_home)
           values (%s, gen_random_uuid(), %s, %s, 'expense', %s, %s, %s, %s, %s, %s)""",
        (ME, w, c, -minor, cur, desc, day, source, -home))


def tick(env):
    before = len(env.tg.sent)
    env.bot.run_tick(env.conn)
    return env.tg.sent[before:]


def netflix(env):
    for d in ("2026-07-03", "2026-08-03", "2026-09-03"):
        add(env, "Netflix", 1798, d)


def test_daily_job_suggests_and_yes_tracks_it(env):
    env.say("/start")
    netflix(env)
    sent = tick(env)
    ask = next(m for m in sent if "a subscription?" in m.text)
    assert "<b>Netflix</b> S$17.98" in ask.text and "every month: 03 Jul, 03 Aug, 03 Sep" in ask.text
    env.press(next(b for b in buttons(ask.markup) if b.endswith(":yes")), ask.id)
    assert "Tracking <b>Netflix</b>" in env.tg.edits[-1].text
    listing = env.say("/subscriptions").text
    assert "1. <b>Netflix</b> S$17.98 / month · next Sat 03 Oct" in listing and "≈ <b>S$17.98</b> a month" in listing
    assert not [m for m in tick(env) if "a subscription?" in m.text]  # once a day


def test_waits_until_10am_and_no_means_never_again(env):
    env.say("/start")
    netflix(env)
    env.clock.now = datetime(2026, 9, 29, 1, 0, tzinfo=timezone.utc)  # 09:00 in Singapore
    assert not [m for m in tick(env) if "a subscription?" in m.text]
    env.clock.now = datetime(2026, 9, 29, 2, 0, tzinfo=timezone.utc)  # 10:00
    ask = next(m for m in tick(env) if "a subscription?" in m.text)
    env.press(next(b for b in buttons(ask.markup) if b.endswith(":no")), ask.id)
    env.clock.now += timedelta(days=1)
    assert not [m for m in tick(env) if "a subscription?" in m.text]
    assert "None tracked yet" in env.say("/subscriptions").text


def test_bills_that_already_log_themselves_are_not_suggested(env):
    env.say("/start")
    env.say("/recurring add phone 7.90 on 5")
    for d in ("2026-07-05", "2026-08-05", "2026-09-05"):
        add(env, "Tiền điện thoại tháng 9", 790, d, cat="Phone")
    assert not [m for m in tick(env) if "a subscription?" in m.text]
    assert "Bills that log themselves" in env.say("/subscriptions").text


def test_a_recurring_bill_only_hides_the_same_thing(env):
    env.say("/start")
    env.say("/recurring add phone 20 on 5")
    for d in ("2026-08-10", "2026-09-10"):
        add(env, "Spotify", 2000, d)          # same price as the phone bill, but something else
    assert [m for m in tick(env) if "<b>Spotify</b>" in m.text]


def test_re_adding_by_hand_changes_the_interval(env):
    env.say("/start")
    netflix(env)
    ask = next(m for m in tick(env) if "a subscription?" in m.text)
    env.press(next(b for b in buttons(ask.markup) if b.endswith(":no")), ask.id)
    env.say("/subscriptions add Netflix 180 yearly")
    assert env.conn.execute("select interval, status from subscriptions").fetchone() == {"interval": "yearly",
                                                                                         "status": "active"}


def test_a_failed_send_does_not_resend_everything_next_tick(env):
    env.say("/start")
    netflix(env)
    real = env.tg.send_message

    def flaky(chat_id, text, reply_markup=None, silent=False):
        if "a subscription?" in text:
            raise RuntimeError("429 Too Many Requests")
        return real(chat_id, text, reply_markup, silent)

    env.tg.send_message = flaky
    env.bot.run_tick(env.conn)
    env.tg.send_message = real
    assert env.conn.execute("select count(*) n from job_runs where key like 'subs:%'").fetchone()["n"] == 1
    assert not [m for m in tick(env) if "a subscription?" in m.text]  # not retried every 15 minutes


def test_at_most_two_suggestions_a_day(env):
    env.say("/start")
    for name in ("Netflix", "Spotify", "Disney", "iCloud"):
        for d in ("2026-08-10", "2026-09-10"):
            add(env, name, 999, d)
    assert len([m for m in tick(env) if "a subscription?" in m.text]) == 2
    env.say("/subscriptions scan")
    assert env.conn.execute("select count(*) n from subscriptions").fetchone()["n"] == 4


def track(env, name="Netflix", amount="17.98", interval="monthly"):
    return env.say(f"/subscriptions add {name} {amount} {interval}").text


def test_reminder_two_days_before_renewal_once(env):
    env.say("/start")
    assert "Tracking <b>Netflix</b>: S$17.98 a month, next Thu 29 Oct" in track(env)
    env.clock.now = datetime(2026, 10, 27, 3, 0, tzinfo=timezone.utc)  # Tue 27 Oct, 11:00
    note = next(m for m in tick(env) if "renews" in m.text)
    assert "<b>Netflix</b> S$17.98 renews in 2 days (Thu 29 Oct)" in note.text
    env.clock.now += timedelta(days=1)
    assert not [m for m in tick(env) if "renews" in m.text]


def test_price_change_is_flagged_and_dates_move_on(env):
    env.say("/start")
    track(env, "Spotify", "10.98")
    add(env, "Spotify", 1198, "2026-10-29")
    env.clock.now = datetime(2026, 10, 30, 3, 0, tzinfo=timezone.utc)
    note = next(m for m in tick(env) if "Spotify" in m.text)
    assert "⚠️ <b>Spotify</b> went up from S$10.98 to S$11.98." in note.text
    s = env.conn.execute("select amount_minor, last_charge_on, next_due from subscriptions").fetchone()
    assert s == {"amount_minor": 1198, "last_charge_on": date(2026, 10, 29), "next_due": date(2026, 11, 29)}


def test_missed_renewal_rolls_forward_quietly(env):
    env.say("/start")
    track(env)  # next 29 Oct
    env.clock.now = datetime(2026, 11, 10, 3, 0, tzinfo=timezone.utc)
    tick(env)
    assert env.conn.execute("select next_due from subscriptions").fetchone()["next_due"] == date(2026, 11, 29)


def test_still_using_it_every_three_months(env):
    env.say("/start")
    track(env, "Gym", "120")
    env.conn.execute("update subscriptions set created_at = now() - interval '100 days'")
    env.clock.now = datetime(2026, 12, 30, 3, 0, tzinfo=timezone.utc)
    check = next(m for m in tick(env) if "Still using" in m.text)
    assert "S$120.00 a month is about S$1,440.00 a year" in check.text
    env.press(next(b for b in buttons(check.markup) if b.endswith(":cancel")), check.id)
    assert "Marked <b>Gym</b> as cancelled" in env.tg.edits[-1].text
    assert "None tracked yet" in env.say("/subscriptions").text


def test_add_and_stop_by_hand(env):
    env.say("/start")
    assert "a year" in track(env, "Domain", "15", "yearly")
    assert "Domain" in env.say("/subscriptions").text
    assert "Stopped tracking <b>Domain</b>" in env.say("/subscriptions stop 1").text
    assert "Tracking" in track(env, "Domain", "15", "yearly")  # can come back


def test_buttons_belong_to_their_owner(env):
    env.say("/start")
    netflix(env)
    ask = next(m for m in tick(env) if "a subscription?" in m.text)
    env.bot.s.allowed_user_ids.add(2002)
    env.press(next(b for b in buttons(ask.markup) if b.endswith(":yes")), ask.id, user=2002)
    assert env.conn.execute("select status from subscriptions").fetchone()["status"] == "suggested"
