"""Charts: the report as numbers, the signed Mini App data route, the pie picture and the buttons (16 Oct)."""
import hashlib
import hmac
import json
import time
from datetime import date
from urllib.parse import urlencode

import pytest

from meow import report, report_data, webapp
from tests.support import ME, STRANGER

TOKEN = "123:test-token"


def sign(user_id, token=TOKEN, auth_date=None, **extra):
    """initData exactly as Telegram builds it for a Mini App."""
    fields = {"auth_date": str(auth_date or int(time.time())), "query_id": "AAH",
              "user": json.dumps({"id": user_id, "first_name": "Tam"}, separators=(",", ":")), **extra}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


# --- Pure pieces -------------------------------------------------------------------------------

def test_periods_and_labels():
    tue = date(2026, 9, 29)
    assert report_data.bounds("week", tue) == (date(2026, 9, 28), date(2026, 10, 5))
    assert report_data.bounds("month", tue) == (date(2026, 9, 1), date(2026, 10, 1))
    assert report_data.label("week", date(2026, 9, 28), date(2026, 10, 5)) == "28 Sep – 4 Oct 2026"
    assert report_data.label("week", date(2026, 9, 7), date(2026, 9, 14)) == "7 – 13 Sep 2026"
    assert report_data.label("month", date(2026, 9, 1), date(2026, 10, 1)) == "September 2026"
    assert report_data.previous_start("month", date(2026, 1, 1)) == date(2025, 12, 1)


def test_pie_keeps_five_and_groups_the_rest():
    cats = [{"name": n, "emoji": "", "amount": a, "bill": n == "Housing"}
            for n, a in [("Food", 500), ("Housing", 80000), ("Transport", 300), ("Groceries", 200),
                         ("Shopping", 100), ("Health", 50), ("Gifts", 30), ("Books", 20)]]
    slices = report_data.pie(cats)
    assert [s["name"] for s in slices] == ["Food", "Transport", "Groceries", "Shopping", "Health", "Other"]
    assert slices[-1]["amount"] == 50 and sum(s["amount"] for s in slices) == 1200
    with_bills = report_data.pie(cats, include_bills=True)
    assert with_bills[0]["name"] == "Housing" and with_bills[0]["share"] == 99
    # A single leftover isn't worth an "Other" slice.
    assert [s["name"] for s in report_data.pie(cats[:7])][-1] == "Gifts"


def test_signature_is_checked():
    now = int(time.time())
    assert webapp.verify_init_data(sign(ME, auth_date=now), TOKEN, now=now)["id"] == ME
    assert webapp.verify_init_data(sign(ME), "999:other-token") is None              # signed for another bot
    tampered = sign(ME).replace("Tam", "Eve")
    assert webapp.verify_init_data(tampered, TOKEN) is None                           # changed after signing
    old = now - 2 * 86400
    assert webapp.verify_init_data(sign(ME, auth_date=old), TOKEN, now=now) is None   # older than a day
    assert webapp.verify_init_data("", TOKEN) is None
    assert webapp.verify_init_data("hash=abc&user=%7B%7D", TOKEN) is None
    assert webapp.verify_init_data(sign(ME), "") is None                               # no token configured
    # Newer Telegram apps add a `signature` field; it is part of what the hash covers.
    assert webapp.verify_init_data(sign(ME, signature="ed25519sig"), TOKEN)["id"] == ME


def test_picture_is_a_png():
    from meow import report_picture
    data = {"kind": "month", "label": "September 2026", "current": False, "spent": 58000, "income": 420000,
            "spent_text": "S$580.00", "income_text": "S$4,200.00", "net_text": "+S$3,620.00", "savings_rate": 86,
            "change_pct": -12, "compared_with": "last month", "bills": 85000, "bills_text": "S$850.00",
            "home": "SGD", "pie": [{"name": "Food & Drinks", "emoji": "🍜", "amount": 24800, "share": 43},
                                   {"name": "Groceries", "emoji": "", "amount": 11200, "share": 19},
                                   {"name": "Ăn vặt (snacks)", "emoji": "", "amount": 22000, "share": 38}]}
    png = report_picture.render(data)
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 10_000
    empty = dict(data, spent=0, pie=[], spent_text="S$0.00")
    assert report_picture.render(empty)[:4] == b"\x89PNG"


# --- With the database --------------------------------------------------------------------------

def _user(env):
    from meow import db
    return db.get_user(env.conn, ME)


def test_numbers_match_the_text_reports(env):
    env.say("/start")
    env.say("salary 4200 to DBS")
    env.say("pho 65k")          # VND, converted at 20,000 per SGD
    env.say("grab 12.5")
    env.say("lunch 14")
    env.say("rent 800")
    user, today = _user(env), date(2026, 9, 29)

    month = report_data.build(env.conn, user, "month", today, today)
    text = report.build(env.conn, user, date(2026, 9, 1))
    assert month["label"] == "September 2026" and month["current"]
    assert month["spent"] == 82975 and month["spent_text"] in text      # 3.25 + 12.50 + 14 + 800
    assert month["income"] == 420000 and month["income_text"] in text
    assert month["savings_rate"] == (420000 - 82975) * 100 // 420000
    assert month["bills"] == 80000                                        # rent is a bill
    assert [s["name"] for s in month["pie"]] and all(s["name"] != "Housing" for s in month["pie"])
    assert month["pie_with_bills"][0]["name"] == "Housing"
    assert sum(s["amount"] for s in month["pie"]) == month["everyday"]
    assert len(month["entries"]) == 5 and month["entries"][0]["amount_text"].startswith("S$")
    pho = next(e for e in month["entries"] if "pho" in e["description"])
    assert pho["original_text"] == "65,000₫"

    week = report_data.build(env.conn, user, "week", today, today)
    assert week["label"] == "28 Sep – 4 Oct 2026" and week["savings_rate"] is None
    assert week["income"] == 420000 and week["days"] == 2                 # Mon and Tue so far
    assert week["next_start"] is None                                     # can't look into the future


def test_older_periods_and_navigation(env):
    env.say("/start")
    env.say("coffee 6")
    env.conn.execute("update transactions set occurred_on = '2026-07-15' where description ilike 'coffee%'")
    env.say("grab 10")
    user, today = _user(env), date(2026, 9, 29)
    sep = report_data.build(env.conn, user, "month", today, today)
    assert sep["previous_start"] == "2026-08-01"
    aug = report_data.build(env.conn, user, "month", date(2026, 8, 1), today)
    assert not aug["current"] and aug["spent"] == 0 and aug["next_start"] == "2026-09-01"
    assert aug["compared_with"] == "last month"
    jul = report_data.build(env.conn, user, "month", date(2026, 7, 1), today)
    assert jul["spent"] == 600 and jul["previous_start"] is None          # nothing before July
    future = report_data.build(env.conn, user, "month", date(2027, 1, 1), today)
    assert future["label"] == "September 2026"                            # clamped to today
    with pytest.raises(ValueError):
        report_data.build(env.conn, user, "year", today, today)


def test_data_route_only_answers_the_signer(env):
    env.say("/start")
    env.say("grab 12.5")
    ok = webapp.report_json(env.conn, env.bot, sign(ME), "month", "2026-09-01", token=TOKEN)
    assert ok["status"] == 200 and ok["body"]["spent"] == 1250
    assert webapp.report_json(env.conn, env.bot, sign(ME), "week", None, token=TOKEN)["body"]["kind"] == "week"
    assert webapp.report_json(env.conn, env.bot, sign(STRANGER), "month", None, token=TOKEN)["status"] == 403
    assert webapp.report_json(env.conn, env.bot, "nonsense", "month", None, token=TOKEN)["status"] == 401
    assert webapp.report_json(env.conn, env.bot, sign(ME), "decade", None, token=TOKEN)["status"] == 400
    assert webapp.report_json(env.conn, env.bot, sign(ME), "month", "not-a-date", token=TOKEN)["status"] == 400
    json.dumps(ok["body"])  # everything in it is plain JSON


def _chart_button(markup):
    return [b for row in (markup or {}).get("inline_keyboard", []) for b in row if "web_app" in b]


def test_report_recap_and_month_come_with_picture_and_button(env):
    env.say("/start")
    env.conn.execute("update users set persona = 'plain'")  # the persona's line is a separate message
    env.say("grab 12.5")
    env.say("lunch 14")
    for cmd, kind in (("/month", "month"), ("/recap", "week"), ("/report this", "month")):
        n = len(env.tg.sent)
        env.say(cmd)
        new = env.tg.sent[n:]
        # One message: the picture, with the button under it (no separate text).
        assert len(new) == 1 and new[0].photo == "report.png", cmd
        url = _chart_button(new[0].markup)[0]["web_app"]["url"]
        assert url.startswith("https://") and f"kind={kind}" in url and "view=charts" in url, cmd


def test_no_picture_when_nothing_was_spent(env):
    env.say("/start")
    env.say("/month")
    assert not [m for m in env.tg.sent if getattr(m, "photo", None)]


def test_recap_persona_line_rides_on_the_picture(env):
    env.say("/start")
    env.say("grab 12.5")
    env.llm.replies.append("Nice and calm week.")
    n = len(env.tg.sent)
    env.say("/recap")
    new = env.tg.sent[n:]
    assert len(new) == 1 and new[0].photo == "report.png" and "Nice and calm week." in new[0].text


def test_a_failed_picture_never_blocks_the_report(env):
    env.say("/start")
    env.say("grab 12.5")
    env.tg.photo_fails = True
    env.say("/month")
    assert "Spent" in env.tg.sent[-1].text and _chart_button(env.tg.sent[-1].markup)


def test_scheduled_recap_and_monthly_report_have_charts(env):
    env.say("/start")
    env.conn.execute("update users set persona = 'plain'")
    env.say("grab 12.5")
    user = _user(env)
    n = len(env.tg.sent)
    env.bot.send_recap(env.conn, user, ME, date(2026, 9, 29), scheduled=True)
    assert any(getattr(m, "photo", None) for m in env.tg.sent[n:])
    assert _chart_button(env.tg.sent[-1].markup)

    from datetime import datetime, timezone
    n = len(env.tg.sent)
    env.bot.maybe_monthly_report(env.conn, user, datetime(2026, 10, 1, 2, 0, tzinfo=timezone.utc))
    new = env.tg.sent[n:]
    report_msg = next(m for m in new if "monthly report" in m.text)
    assert "start=2026-09-01" in _chart_button(report_msg.markup)[0]["web_app"]["url"]
    assert any(getattr(m, "photo", None) for m in new)


def test_page_route_and_menu_button():
    page = webapp.page_html()
    assert "telegram-web-app.js" in page and "X-Telegram-Init-Data" in page
    assert webapp.chart_url("https://meow-finance.vercel.app/", "week", date(2026, 9, 28)) == \
        "https://meow-finance.vercel.app/charts?view=charts&kind=week&start=2026-09-28"


def test_api_serves_the_page_and_guards_the_data(monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "hook")
    from meow import runtime
    runtime.get_bot.cache_clear()
    import api.index as api
    client = TestClient(api.app)
    page = client.get("/charts?view=charts&kind=week&start=2026-09-28")
    assert page.status_code == 200 and "text/html" in page.headers["content-type"]
    assert client.get("/api/index?view=charts").status_code == 200        # whatever path Vercel hands us
    assert client.get("/anything").json() == {"ok": True}
    bad = client.post("/charts", json={"kind": "month"}, headers={"X-Telegram-Init-Data": "hash=x&user=%7B%7D"})
    assert bad.status_code == 401 and "open this page" in bad.json()["error"]
    # Without the signature header a POST is still the webhook, which needs its own secret.
    assert client.post("/charts", json={"kind": "month"}).status_code == 401
    runtime.get_bot.cache_clear()


def _week_and_month(env):
    """Tue 29 Sep 2026. Everyday budget S$600 (S$20 a day in September, S$140 a week)."""
    env.say("/start")
    env.say("/budget everyday 600")
    for _ in range(3):
        env.say("coffee 6")
    env.say("grab 25")
    env.say("rent 800")                      # a bill: never the biggest spend, never a "change"
    env.say("grab 5")
    env.say("shopee 40")
    env.conn.execute("update transactions set occurred_on = '2026-09-21' where description = 'grab' "
                     "and amount_minor = -500")
    env.conn.execute("update transactions set occurred_on = '2026-09-22' where description = 'shopee'")
    return _user(env), date(2026, 9, 29)


def test_fun_facts_are_logged_days_biggest_spend_and_biggest_changes(env):
    user, today = _week_and_month(env)
    week = report_data.build(env.conn, user, "week", today, today)
    assert week["facts"] == ["📅 Logged on 1 of 2 days",
                             "💸 Biggest spend: grab S$25.00 (Tue 29 Sep)",
                             "📈 Transport +S$20.00 · 📉 Shopping −S$40.00 vs the same days last week"]
    assert all(t["description"] != "rent" for t in week["top"])   # the Biggest list leaves bills out too
    # Small moves (under S$10) aren't worth a fact; bills never count.
    assert report_data.biggest_changes({"Food": 900, "Housing": 80000}, {"Food": 100}) == []


def test_tip_is_the_pace_for_the_rest_of_the_period(env):
    user, today = _week_and_month(env)
    week = report_data.build(env.conn, user, "week", today, today)
    # Today's S$43 isn't taken off first: the per-day figure includes today and holds still all day.
    assert week["tip"] == ("💡 S$140.00 left of this week's S$140.00 share of your everyday budget: "
                           "about S$23.33 a day for the 6 days left, today included.")
    assert week["bowl_offer"] is None                              # the bowl is a monthly thing
    month = report_data.build(env.conn, user, "month", today, today)
    assert month["tip"] == ("💡 S$555.00 left of your S$600.00 everyday budget: "
                            "about S$277.50 a day for the 2 days left, today included.")
    assert month["bowl_offer"] == {"minor": 27750, "from": "2026-09-29",
                                   "text": "🐱 Set Mochi's bowl to S$277.50/day"}
    last = report_data.build(env.conn, user, "month", date(2026, 9, 30), date(2026, 9, 30))
    assert last["tip"] == "💡 S$512.00 left of your S$600.00 everyday budget: about S$512.00 a day for today, the last day."
    aug = report_data.build(env.conn, user, "month", date(2026, 8, 1), today)
    assert aug["tip"] == "💡 Finished S$600.00 under your S$600.00 everyday budget."
    env.conn.execute("update users set everyday_budget_minor = null")
    assert report_data.build(env.conn, _user(env), "month", today, today)["tip"] is None


def test_bowl_button_sets_mochis_bowl_for_the_rest_of_the_month(env):
    from meow import db, mochi
    user, today = _week_and_month(env)
    env.conn.execute("update users set persona = 'plain'")
    msg = env.say("/month")
    rows = msg.markup["inline_keyboard"]
    assert rows[1][0]["callback_data"] == "bowl:27750:2026-09-29" and "S$277.50/day" in rows[1][0]["text"]
    env.press("bowl:27750:2026-09-29", msg.id)
    state = db.mochi_state(env.conn, ME)
    assert mochi.bowl_on(state, 60000, date(2026, 9, 29)) == 27750         # today too
    assert mochi.bowl_on(state, 60000, date(2026, 9, 30)) == 27750
    assert mochi.bowl_on(state, 60000, date(2026, 9, 28)) == 2000
    assert mochi.bowl_on(state, 60000, date(2026, 10, 1)) == 60000 // 31   # next month: back to normal
    assert ("Mochi's bowl is <b>S$277.50</b> a day from today to Wed 30 Sep (S$234.50 left today)"
            in env.tg.sent[-1].text)
    assert env.tg.answers[-1] == "Bowl updated 🐱"
    # /today, Mochi's card and the line under each entry all show the new bowl at once.
    assert "bowl S$234.50 left" in env.say("/today").text
    assert "Daily bowl: S$277.50 (your pace for the rest of the month)" in env.say("/mochi").text
    assert "bowl S$228.50 left" in env.say("coffee 6").text
    # /month no longer offers the same bowl, and the figure doesn't drift as today's spending grows.
    assert len(env.say("/month").markup["inline_keyboard"]) == 1
    env.press("bowl:100:2026-09-28", msg.id)                              # an old button
    assert "out of date" in env.tg.answers[-1]
    env.press("bowl:100:2026-09-30", msg.id)                              # a button from before this fix
    assert db.mochi_state(env.conn, ME)["bowl_override_from"] == date(2026, 9, 29)
    env.say("/budget everyday 700")                                       # a new budget clears it
    assert db.mochi_state(env.conn, ME)["bowl_override_minor"] is None


def test_ai_suggestion_goes_under_the_tip_in_the_chat(env):
    _week_and_month(env)
    env.llm.replies += ["Good week.", "Take the MRT instead of Grab on short trips."]
    n = len(env.tg.sent)
    env.say("/recap")
    caption = env.tg.sent[n].text
    assert "💡 S$140.00 left" in caption and "🤖 <i>" in caption and "Take the MRT" in caption
    prompt = env.llm.chats[-1]
    assert "Needs are not for cutting" in prompt["system"]
    assert "All Transport entries this period" in prompt["messages"][-1]["content"]
    assert "rent" not in prompt["messages"][-1]["content"].split("Biggest everyday entries:")[1]
    # Plain persona: no AI at all.
    env.conn.execute("update users set persona = 'plain'")
    calls = len(env.llm.chats)
    env.say("/month")
    assert len(env.llm.chats) == calls and "🤖" not in env.tg.sent[-1].text


def test_caption_is_short_enough_for_telegram():
    from meow.bot import Bot
    data = {"facts": ["💸 Biggest spend: " + "x" * 800], "tip": "💡 " + "y" * 100}
    cap = Bot.chart_caption(data, "head", "z" * 300)
    assert len(cap) <= 1024 and cap.startswith("head") and "zzz" not in cap   # the AI line goes first
    assert "🤖" in Bot.chart_caption({"facts": ["a"], "tip": "b"}, None, "short")
    assert Bot.chart_caption({"facts": [], "tip": None}) is None


def test_suggestion_none_and_errors_mean_no_line():
    from types import SimpleNamespace
    from meow import persona_llm

    class Client:
        def __init__(self, reply=None, fail=False):
            self.reply, self.fail, self.messages = reply, fail, self

        def create(self, **kwargs):
            if self.fail:
                raise RuntimeError("overloaded")
            return SimpleNamespace(content=[SimpleNamespace(type="text", text=self.reply)], usage=None)

    for reply in ("NONE", "none.", " NONE "):
        assert persona_llm.suggest(Client(reply), "m", "en", "Tam", "facts")[0] is None
    assert persona_llm.suggest(Client(fail=True), "m", "en", "Tam", "facts")[0] is None
    assert persona_llm.suggest(Client("Book Grab less on Fridays."), "m", "vi", "Tam", "f")[0] == \
        "Book Grab less on Fridays."


def test_planned_buys_are_out_of_everyday_everywhere(env):
    """One rule for "everyday" (db.EVERYDAY): the pie, the budget bar and the pace never disagree."""
    user, today = _week_and_month(env)
    # Read by Claude (an unknown item); its description drops the tag, the bot puts it back.
    env.llm.queue.append({"entries": [{"amount": "900", "currency": "SGD", "type": "expense",
                                       "category": "Shopping", "description": "laptop", "date": "2026-09-29"}]})
    env.say("new laptop 900 #planned")
    assert env.conn.execute("select description from transactions order by id desc limit 1").fetchone() == \
        {"description": "laptop #planned"}
    month = report_data.build(env.conn, _user(env), "month", today, today)
    everyday_bar = next(b for b in month["budgets"] if b["name"] == "Everyday")["spent"]
    assert sum(s["amount"] for s in month["pie"]) == month["everyday"] == everyday_bar == 8800
    assert month["bills"] == 80000 + 90000 and month["bills_note"] == "rent, phone, study, fees and #planned buys"
    assert "laptop" not in month["facts"][1]                       # not the biggest everyday spend
    later = report_data.build(env.conn, _user(env), "month", date(2026, 9, 30), date(2026, 9, 30))
    assert later["tip"].startswith("💡 S$512.00 left")              # the pace ignores it too


def test_withdraw_uses_the_cash_wallet_you_already_have(env):
    from meow import db
    env.say("/start")
    env.say("/wallet add Cash SGD cash")
    env.conn.execute("update wallets set name = 'Cash SGD' where name = 'Cash'")   # as in your imported wallets
    n = len(db.wallets(env.conn, ME))
    env.say("withdraw 100 from DBS")
    names = [w.name for w in db.wallets(env.conn, ME)]
    assert len(names) == n and "Cash" not in names                 # no duplicate "Cash" wallet
    bal = env.conn.execute("select balance_minor from wallet_balances where name = 'Cash SGD'").fetchone()
    assert bal["balance_minor"] == 10000


def test_old_update_ids_are_pruned(env):
    env.say("/start")
    env.conn.execute("insert into processed_updates (update_id, processed_at) values (999999, now() - interval '9 days')")
    env.bot.run_tick(env.conn)
    assert not env.conn.execute("select 1 from processed_updates where update_id = 999999").fetchone()
    assert env.conn.execute("select count(*) as n from processed_updates").fetchone()["n"] >= 1
