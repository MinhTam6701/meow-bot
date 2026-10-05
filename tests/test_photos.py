"""Photos and screenshots, modelled on real samples: a DBS card notification, PayNow and FAST
transfers to people, a VPBank bill payment, a Shopee order without a date, and a huge transfer."""
from datetime import date

import pytest

from meow import vision
from meow.models import CategoryInfo, ParseContext, WalletInfo
from meow.photo_flow import same_person
from tests.test_bot_flow import ME, buttons, env  # noqa: F401  (fixture)

# NOW in the fixture is 29 Sep 2026, 22:30 in Singapore.


def pay(amount, currency="SGD", desc="Scarlett Supermarket", cat="Groceries", day="2026-09-20", wallet="DBS",
        who=None, kind="business", type_="expense", note=None):
    p = {"amount": amount, "currency": currency, "type": type_, "category": cat, "description": desc,
         "counterparty_kind": kind}
    for k, v in (("date", day), ("wallet", wallet), ("counterparty", who), ("note", note)):
        if v is not None:
            p[k] = v
    return p


def photo(env, *payments, caption=None, question=None, document=None, file_size=200_000):
    env.llm.queue.append({"payments": list(payments), **({"question": question} if question else {})})
    msg = {"message_id": 1, "chat": {"id": ME, "type": "private"}, "from": {"id": ME, "first_name": "Tam"}}
    if document:
        msg["document"] = {"file_id": "doc1", "mime_type": document, "file_size": file_size}
    else:
        msg["photo"] = [{"file_id": "small", "file_size": 1000}, {"file_id": "big", "file_size": file_size}]
    if caption:
        msg["caption"] = caption
    env.bot.process_update(env.conn, {"update_id": 900_000 + len(env.tg.sent) + len(env.llm.calls), "message": msg})
    return env.tg.sent[-1]


def count(env, sql="select count(*) n from live_transactions"):
    return env.conn.execute(sql).fetchone()["n"]


def test_card_notification_logs_straight_away(env):
    env.say("/start")
    card = photo(env, pay("15.80"))
    assert "Logged" in card.text and "Scarlett Supermarket" in card.text and "S$15.80" in card.text
    assert "📸 Read from your photo" in card.text and "Groceries" in card.text and "Sun 20 Sep" in card.text
    row = env.conn.execute("select source, parser, raw_message, occurred_on from transactions").fetchone()
    assert row == {"source": "photo", "parser": "vision", "raw_message": "[photo]", "occurred_on": date(2026, 9, 20)}
    call = env.llm.calls[-1]
    assert env.tg.downloads == ["big"]  # the largest size
    image = call["messages"][0]["content"][0]
    assert image["type"] == "image" and image["source"]["media_type"] == "image/jpeg"
    assert call["tool_choice"] == {"type": "tool", "name": "record_payments"}
    assert env.conn.execute("select purpose from llm_calls where purpose = 'vision'").fetchone()
    assert env.tg.reactions  # the persona still reacts


def test_no_date_uses_today_and_says_so(env):
    env.say("/start")
    card = photo(env, pay("71.99", desc="Shopee vacuum", cat="Shopping", day=None, wallet=None))
    assert "no date on it, so I used today" in card.text
    assert env.conn.execute("select occurred_on from transactions").fetchone()["occurred_on"] == date(2026, 9, 29)


def test_several_payments_in_one_picture(env):
    env.say("/start")
    card = photo(env, pay("4.50", desc="Kopi", cat="Food & Drinks"), pay("12.00", desc="Grab", cat="Transport"))
    assert "Logged 2 entries" in card.text and count(env) == 2


def test_duplicate_asks_and_log_anyway(env):
    env.say("/start")
    env.say("toiletries 15.80 yesterday")              # 28 Sep, typed earlier
    msg = photo(env, pay("15.80", day="2026-09-27"))
    assert "looks already logged" in msg.text and "toiletries · S$15.80 · DBS · Mon 28 Sep" in msg.text
    assert count(env) == 1
    edit = env.press(next(b for b in buttons(msg.markup) if b.endswith(":keep")), msg.id)
    assert "Logged below" in edit.text
    assert count(env) == 2


def test_duplicate_skip(env):
    env.say("/start")
    env.say("toiletries 15.80 yesterday")
    msg = photo(env, pay("15.80", day="2026-09-28"))
    edit = env.press(next(b for b in buttons(msg.markup) if b.endswith(":skip")), msg.id)
    assert "Not logged" in edit.text and not (edit.markup or {}).get("inline_keyboard")
    assert count(env) == 1
    env.press(next(b for b in buttons(msg.markup) if b.endswith(":skip")), msg.id)
    assert env.tg.answers[-1] == "Already done."


def test_guessed_date_widens_the_duplicate_check(env):
    env.say("/start")
    env.say("shopee 71.99 yesterday")
    env.conn.execute("update transactions set occurred_on = '2026-09-26' where description = 'shopee'")
    # No date on the Shopee screenshot: today (29 Sep) is a guess, so 26 Sep still counts as a match.
    msg = photo(env, pay("71.99", desc="Shopee vacuum", cat="Shopping", day=None))
    assert "looks already logged" in msg.text
    # With a printed date (not today) three days away, it's a different purchase.
    card = photo(env, pay("71.99", desc="Shopee vacuum", cat="Shopping", day="2026-09-23"))
    assert "Logged" in card.text


def test_date_filled_in_as_today_still_finds_the_earlier_entry(env):
    """The real case: a Shopee order screenshot with no order date, read as today's date."""
    env.say("/start")
    env.say("shopee 71.99 yesterday")
    env.conn.execute("update transactions set occurred_on = '2026-09-27' where description = 'shopee'")
    msg = photo(env, pay("71.99", desc="Russell Taylors cordless vacuum", cat="Shopping", day="2026-09-29"))
    assert "looks already logged" in msg.text and "shopee · S$71.99 · DBS · Sun 27 Sep" in msg.text


def test_money_to_a_person_asks_what_it_was(env):
    env.say("/start")
    msg = photo(env, pay("92.00", desc="Transfer to Tran Cam Van", cat="Other", day="2026-09-13",
                         who="TRAN CAM VAN", kind="person"))
    assert "S$92.00 sent to <b>TRAN CAM VAN</b>" in msg.text and "What was it?" in msg.text
    assert count(env) == 0
    pid_btns = buttons(msg.markup)
    assert any(b.endswith(":spend") for b in pid_btns) and any(b.endswith(":own") for b in pid_btns)
    picker = env.press(next(b for b in pid_btns if b.endswith(":spend")), msg.id)
    housing = env.conn.execute("select id from categories where name = 'Housing'").fetchone()["id"]
    cat_btn = next(b for b in buttons(picker.markup) if b.endswith(f":c:{housing}"))
    env.press(cat_btn, msg.id)
    row = env.conn.execute("select c.name, t.amount_minor, t.occurred_on from transactions t "
                           "join categories c on c.id = t.category_id").fetchone()
    assert row == {"name": "Housing", "amount_minor": -9200, "occurred_on": date(2026, 9, 13)}
    assert any(e.text and "Logged below" in e.text for e in env.tg.edits)
    env.press(cat_btn, msg.id)                           # a second tap does nothing
    assert count(env) == 1 and env.tg.answers[-1] == "Already done."


def test_money_to_my_own_name_becomes_a_move(env):
    env.say("/start")
    env.say("/myname Trinh Minh Tam")
    msg = photo(env, pay("3468000", currency="VND", desc="Transfer", cat="Other", day="2026-09-08",
                         wallet="VP", who="TRỊNH MINH TÂM", kind="person"))
    assert "That's your own name" in msg.text
    picker = env.press(next(b for b in buttons(msg.markup) if b.endswith(":own")), msg.id)
    dbs = env.conn.execute("select id from wallets where name = 'DBS'").fetchone()["id"]
    env.press(next(b for b in buttons(picker.markup) if b.endswith(f":w:{dbs}")), msg.id)
    rows = env.conn.execute("select type, amount_minor, currency, occurred_on from transactions order by id").fetchall()
    assert [(r["type"], r["amount_minor"], r["currency"]) for r in rows] == [
        ("transfer", -3468000, "VND"), ("transfer", 17340, "SGD")]  # 20,000 VND per SGD in the fake rates
    assert all(r["occurred_on"] == date(2026, 9, 8) for r in rows)
    assert count(env, "select count(*) n from transactions where type = 'expense'") == 0


def test_huge_transfer_to_a_person_is_never_logged_without_asking(env):
    env.say("/start")
    msg = photo(env, pay("240000000", currency="VND", desc="Transfer", cat="Gifts & Family", day="2026-08-19",
                         wallet="VP", who="PHAM THI QUYNH HOA", kind="person"))
    assert "240,000,000₫ sent to" in msg.text and count(env) == 0


def test_big_business_payment_checks_first(env):
    env.say("/start")
    msg = photo(env, pay("899.00", desc="Laptop", cat="Shopping", day="2026-09-25"))
    assert "That's a big one" in msg.text and count(env) == 0
    env.press(next(b for b in buttons(msg.markup) if b.endswith(":log")), msg.id)
    assert count(env) == 1


def test_wallet_guess_must_match_the_currency(env):
    env.say("/start")
    photo(env, pay("3468000", currency="VND", desc="Viettel wifi", cat="Housing", day="2026-09-08", wallet="DBS"))
    assert env.conn.execute("select w.name from transactions t join wallets w on w.id = t.wallet_id").fetchone()["name"] == "VP"


def test_nothing_to_log(env):
    env.say("/start")
    msg = photo(env, question="This is a product page, not a payment.")
    assert msg.text == "🔍 This is a product page, not a payment." and count(env) == 0


def test_unreadable_answer_from_the_model(env):
    env.say("/start")
    env.llm.queue.append({"payments": [{"amount": "abc"}]})
    msg = {"message_id": 1, "chat": {"id": ME, "type": "private"}, "from": {"id": ME}, "photo": [{"file_id": "x"}]}
    env.bot.process_update(env.conn, {"update_id": 77, "message": msg})
    assert "couldn't read that picture" in env.tg.sent[-1].text and count(env) == 0


def test_screenshot_sent_as_a_file(env):
    env.say("/start")
    card = photo(env, pay("15.80"), document="image/png")
    assert "Logged" in card.text and env.llm.calls[-1]["messages"][0]["content"][0]["source"]["media_type"] == "image/png"
    too_big = photo(env, pay("1.00"), document="image/png", file_size=9_000_000)
    assert "too large" in too_big.text


def test_other_files_are_not_read(env):
    env.say("/start")
    msg = {"message_id": 1, "chat": {"id": ME, "type": "private"}, "from": {"id": ME},
           "document": {"file_id": "d", "mime_type": "application/pdf"}}
    env.bot.process_update(env.conn, {"update_id": 78, "message": msg})
    assert "photos of receipts" in env.tg.sent[-1].text


def test_caption_reaches_the_model(env):
    env.say("/start")
    photo(env, pay("15.80"), caption="split with Minh")
    assert "split with Minh" in env.llm.calls[-1]["messages"][0]["content"][1]["text"]
    assert env.conn.execute("select raw_message from transactions").fetchone()["raw_message"] == "[photo] split with Minh"


def test_numbers_are_scrubbed_before_saving_or_logging(env):
    env.say("/start")
    photo(env, pay("92.00", desc="PayNow 272-644799-9", who="SIX GUO +65 83832159", kind="person",
                   note="NAP245729 U5371 ref 6234BFTVGLYXN6X1"))
    item = env.conn.execute("select entry, counterparty, note from pending_items").fetchone()
    blob = str(item) + str(env.conn.execute("select output_json from llm_calls where purpose = 'vision'").fetchone())
    for secret in ("644799", "83832159", "6234BFTVGLYXN6X1", "245729"):
        assert secret not in blob


# --- units -------------------------------------------------------------------------

def test_same_person_ignores_accents_case_and_order():
    assert same_person("TRINH MINH TAM", "Trịnh Minh Tâm")
    assert same_person("Tam Trinh Minh", "Trinh Minh Tam")
    assert not same_person("TRAN CAM VAN", "Trinh Minh Tam")
    assert not same_person("Tam", "Tam")  # a first name alone is too weak


@pytest.mark.parametrize("raw,want", [
    ("PayNow 272-644799-9", "PayNow"),
    ("SIX GUO +65 83832159", "SIX GUO"),
    ("NAP245729 U5371", "U5371"),
    ("card ••5949", "card"),
    ("Viettel wifi", "Viettel wifi"),
    ("Shopee 10.10 sale", "Shopee 10.10 sale"),
])
def test_scrub(raw, want):
    assert vision.scrub(raw) == want


def ctx():
    cats = [CategoryInfo(1, "Housing", "🏠", "expense"), CategoryInfo(2, "Other", "📦", "expense"),
            CategoryInfo(3, "Other income", "💰", "income")]
    return ParseContext(today=date(2026, 10, 5), home_currency="SGD", wallets=[WalletInfo(1, "DBS", "SGD", True)],
                        categories=cats)


def test_tidy_reads_vietnamese_amounts_and_drops_future_dates():
    out = vision._tidy({"payments": [
        {"amount": "3 468 000", "currency": "VND", "category": "Wifi bills", "type": "expense", "date": "2026-09-08"},
        {"amount": "3.468.000", "currency": "VND", "category": "Housing", "type": "expense", "date": "2027-01-01"},
        {"amount": "1,234.50", "currency": "SGD", "category": "Housing", "type": "expense", "wallet": ""},
    ]}, ctx())
    assert [p["amount"] for p in out["payments"]] == ["3468000", "3468000", "1234.50"]
    assert out["payments"][0]["category"] == "Other"      # unknown category -> Other
    assert out["payments"][1]["date"] is None             # a future date is a misread
    assert "wallet" not in out["payments"][2]
