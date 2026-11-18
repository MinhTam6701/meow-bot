"""M6: guided /setup, the default wallet per currency, your own month start, and the balance on the card."""
import dataclasses
from datetime import date, datetime, timedelta, timezone

from meow import mochi, periods
from tests.support import buttons

SGT = timezone(timedelta(hours=8))


def at(env, y, mo, d, hh=12, mm=0):
    env.clock.now = datetime(y, mo, d, hh, mm, tzinfo=SGT).astimezone(timezone.utc)


def fresh(env):
    """A new user with no wallets at all, as in production (no STARTER_WALLETS)."""
    env.bot.s = dataclasses.replace(env.bot.s, starter_wallets=[])


def wallets(env):
    return [(r["name"], r["currency"], r["is_default"]) for r in env.conn.execute(
        "select name, currency, is_default from wallets where not archived order by id").fetchall()]


def tap(env, msg, label):
    """Press the button whose text contains `label` on the setup message (edited in place)."""
    markup = env.tg.edits[-1].markup if env.tg.edits and env.tg.edits[-1].message_id == msg.id else msg.markup
    data = next(b["callback_data"] for row in markup["inline_keyboard"] for b in row if label in b["text"])
    return env.press(data, msg.id)


# ---------------------------------------------------------------- periods
def test_periods():
    assert periods.bounds(date(2026, 11, 18), 26) == (date(2026, 10, 26), date(2026, 11, 26))
    assert periods.bounds(date(2026, 11, 26), 26) == (date(2026, 11, 26), date(2026, 12, 26))
    assert periods.bounds(date(2026, 1, 5), 26) == (date(2025, 12, 26), date(2026, 1, 26))
    assert periods.bounds(date(2026, 2, 14)) == (date(2026, 2, 1), date(2026, 3, 1))
    assert periods.previous(date(2026, 11, 18), 26) == date(2026, 9, 26)
    assert periods.previous(date(2026, 1, 1)) == date(2025, 12, 1)
    assert periods.label(date(2026, 11, 1), date(2026, 12, 1)) == "November 2026"
    assert periods.label(date(2026, 10, 26), date(2026, 11, 26)) == "26 Oct – 25 Nov 2026"
    assert periods.label(date(2025, 12, 26), date(2026, 1, 26)) == "26 Dec 2025 – 25 Jan 2026"
    assert periods.length(date(2026, 3, 10), 26) == 28  # 26 Feb – 25 Mar
    # Mochi's bowl spreads the everyday budget over the user's month, not March's 31 days
    assert mochi.bowl(31000, date(2026, 3, 10), 26) == 31000 // 28
    assert mochi.bowl(31000, date(2026, 3, 10)) == 31000 // 31


# ---------------------------------------------------------------- setup
def test_new_user_setup_currencies_wallets_default_and_month(env):
    fresh(env)
    msg = env.say("/start")
    assert "Hi Tam" in env.tg.sent[-2].text and "Your home currency" in msg.text
    assert wallets(env) == []

    tap(env, msg, "SGD")
    assert "Which currencies do you use?" in env.tg.edits[-1].text
    tap(env, msg, "VND")                                  # add VND
    assert "✅ VND" in str(env.tg.edits[-1].markup)
    assert env.press("su:cur:SGD", msg.id) is not None and "home currency" in env.tg.answers[-1]
    tap(env, msg, "Next")
    assert "Your SGD wallets</b> (1/2)" in env.tg.edits[-1].text
    tap(env, msg, "DBS")
    tap(env, msg, "OCBC")
    tap(env, msg, "Next")
    assert "Your default SGD wallet" in env.tg.edits[-1].text
    tap(env, msg, "OCBC")
    assert "Your VND wallets</b> (2/2)" in env.tg.edits[-1].text
    tap(env, msg, "Next")                                 # nothing picked: asked again
    assert "Pick at least one VND wallet" in env.tg.answers[-1]
    tap(env, msg, "VCB")
    tap(env, msg, "Cash")
    tap(env, msg, "Next")
    tap(env, msg, "VCB")                                  # default VND
    assert "When does your month start?" in env.tg.edits[-1].text
    tap(env, msg, "26th")
    done = env.tg.edits[-1].text
    assert "All set!" in done and "SGD: DBS, OCBC ⭐" in done and "VND: VCB ⭐, Cash VND" in done
    assert "Month starts on the 26th: now 26 Sep – 25 Oct 2026" in done
    assert "How to log" in env.tg.sent[-1].text           # new users get the help at the end

    assert wallets(env) == [("DBS", "SGD", False), ("OCBC", "SGD", True), ("VCB", "VND", True),
                            ("Cash VND", "VND", False)]
    assert env.conn.execute("select month_start_day, setup from users").fetchone() == {
        "month_start_day": 26, "setup": None}
    card = env.say("lunch 12").text
    assert "OCBC" in card and "OCBC balance: <b>-S$12.00</b>" in card
    assert "VCB" in env.say("pho 65k").text


def test_setup_typed_wallet_name_and_day(env):
    fresh(env)
    msg = env.say("/start")
    tap(env, msg, "SGD")
    tap(env, msg, "Next")
    tap(env, msg, "Type a name")
    assert "Type the SGD wallet name" in env.tg.sent[-1].text
    shown = env.say("Citibank, Amex")
    assert "✅ Citibank" in str(shown.markup) and "✅ Amex" in str(shown.markup)
    tap(env, shown, "Next")
    tap(env, shown, "Amex")                               # default
    tap(env, shown, "Another day")
    assert "Pick a day from 1 to 28" in env.say("31").text
    env.say("25")
    assert "All set!" in env.tg.sent[-2].text and "Month starts on the 25th" in env.tg.sent[-2].text
    assert wallets(env) == [("Citibank", "SGD", False), ("Amex", "SGD", True)]


def test_typing_an_entry_during_setup_still_logs_it(env):
    fresh(env)
    msg = env.say("/start")
    tap(env, msg, "SGD")
    tap(env, msg, "Next")
    tap(env, msg, "Type a name")
    card = env.say("lunch 12")                            # not a wallet name: logged as usual
    assert "Logged" in card.text and "Cash" in card.text


def test_existing_user_reruns_setup_and_keeps_home_currency(env):
    env.say("/start")                                     # starter wallets DBS (SGD) and VP (VND)
    env.say("lunch 12")
    msg = env.say("/setup")
    assert "Which currencies do you use?" in msg.text   # the home step is skipped once there are entries
    env.press("su:cur:VND", msg.id)
    assert "you have wallets in it" in env.tg.answers[-1]
    tap(env, msg, "Next")
    assert "Already set up: DBS" in env.tg.edits[-1].text
    tap(env, msg, "OCBC")
    tap(env, msg, "Next")
    tap(env, msg, "OCBC")                                 # the friend's fix: OCBC becomes the default
    tap(env, msg, "Next")                                 # VND: keep VP
    tap(env, msg, "1st")
    assert "All set!" in env.tg.edits[-1].text and "How to log" not in env.tg.sent[-1].text
    assert "OCBC" in env.say("coffee 6").text


def test_old_setup_buttons_are_ignored(env):
    fresh(env)
    old = env.say("/start")
    new = env.say("/setup")
    env.press("su:home:VND", old.id)
    assert "latest setup message" in env.tg.answers[-1]
    env.press("su:home:VND", new.id)
    assert env.conn.execute("select home_currency from users").fetchone()["home_currency"] == "VND"


# ---------------------------------------------------------------- default wallet
def test_added_wallet_offers_to_become_default(env):
    env.say("/start")
    added = env.say("/wallet add OCBC SGD")
    assert "default SGD wallet is still DBS" in added.text
    env.press(buttons(added.markup)[0], added.id)
    assert "OCBC</b> is now your default SGD wallet" in env.tg.edits[-1].text
    assert "OCBC" in env.say("lunch 12").text


def test_wallet_list_has_default_picker(env):
    env.say("/start")
    env.say("/wallet add OCBC SGD")
    listing = env.say("/wallet")
    assert buttons(listing.markup) == ["wd:SGD"]           # VND has only VP
    env.press("wd:SGD", listing.id)
    picker = env.tg.edits[-1]
    assert "● DBS" in str(picker.markup)
    ocbc = env.conn.execute("select id from wallets where name = 'OCBC'").fetchone()["id"]
    assert f"wd:s:{ocbc}" in buttons(picker.markup)
    env.press(f"wd:s:{ocbc}", listing.id)
    assert ("OCBC", "SGD", True) in wallets(env)
    assert "tap a currency" in env.say("/wallet default").text.lower()


# ---------------------------------------------------------------- balance on the card
def test_card_shows_wallet_balance(env):
    env.say("/start")
    env.say("/setbalance DBS 100")
    card = env.say("lunch 12")
    assert "👛 DBS balance: <b>S$88.00</b>" in card.text
    env.press(f"undo:{buttons(card.markup)[1].split(':')[1]}", card.id)
    assert "DBS balance: <b>S$100.00</b>" in env.tg.edits[-1].text
    both = env.say("coffee 6, pho 65k").text
    assert "👛 Balances: DBS <b>S$94.00</b> · VP <b>-65,000₫</b>" in both
    moved = env.say("move 50 from DBS to Cash").text
    assert "Balances: DBS <b>S$44.00</b> · Cash <b>S$50.00</b>" in moved


# ---------------------------------------------------------------- month start
def test_month_start_moves_month_budgets_and_bowl(env):
    env.tg.photo_fails = True
    at(env, 2026, 9, 24)
    env.say("/start")
    env.conn.execute("update users set reminders_on = false")
    assert "Logged" in env.say("lunch 50").text           # before the 26th: last month
    at(env, 2026, 9, 29, 22, 30)
    assert "the 26th" in env.say("/monthstart 26").text
    assert "Month starts on: the 26th" in env.say("/settings").text
    env.say("/budget Food 60")
    card = env.say("dinner 20").text
    assert "Logged" in card and "budget" not in card      # S$20 of 60 this month: no alert
    month = env.say("/month").text
    assert "26 Sep – 25 Oct 2026" in month and "Spent <b>S$20.00</b>" in month
    assert "S$20.00 / S$60.00" in env.say("/budget").text
    env.say("/budget everyday 600")
    assert "Daily bowl: S$20.00" in env.say("/mochi").text  # 600 over 30 days (26 Sep – 25 Oct)
    assert "Pick a day from 1 to 28" in env.say("/monthstart 30").text


def test_report_on_month_start_and_check_on_the_first(env):
    env.tg.photo_fails = True
    at(env, 2026, 9, 27)
    env.say("/start")
    env.conn.execute("update users set reminders_on = false")
    env.say("/monthstart 26")
    env.say("lunch 12")
    at(env, 2026, 10, 1, 9, 0)                            # the 1st: balance check only
    stats = env.bot.run_tick(env.conn)
    assert stats.get("reports", 0) == 0
    assert any("Balance check" in m.text for m in env.tg.sent)
    assert not any("monthly report" in m.text for m in env.tg.sent)
    at(env, 2026, 10, 26, 9, 0)                           # the 26th: last month's report
    assert env.bot.run_tick(env.conn)["reports"] == 1
    rep = next(m for m in env.tg.sent if "monthly report" in m.text)
    assert "26 Sep – 25 Oct 2026" in rep.text and "Spent <b>S$12.00</b>" in rep.text
    assert env.bot.run_tick(env.conn).get("reports", 0) == 0
    assert "26 Sep – 25 Oct 2026" in env.say("/report").text


def test_month_start_buttons(env):
    env.say("/start")
    shown = env.say("/monthstart")
    assert "ms:26" in buttons(shown.markup)
    env.press("ms:26", shown.id)
    assert "now starts on <b>the 26th</b>" in env.tg.edits[-1].text


def test_same_preset_in_two_currencies(env):
    fresh(env)
    msg = env.say("/start")
    tap(env, msg, "SGD")
    tap(env, msg, "USD")
    tap(env, msg, "EUR")
    tap(env, msg, "Next")
    tap(env, msg, "DBS")
    tap(env, msg, "Next")
    for _ in ("USD", "EUR"):
        tap(env, msg, "Wise")
        tap(env, msg, "Next")
    assert [w[:2] for w in wallets(env)] == [("DBS", "SGD"), ("Wise", "USD"), ("Wise EUR", "EUR")]
    assert all(w[2] for w in wallets(env))  # one wallet each: it's the default
