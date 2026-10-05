"""Insights: pattern rules, the Sunday recap, patterns in the monthly report, and questions."""
from datetime import date, datetime, timedelta, timezone

from meow import ask, insights

# --- pure rules ------------------------------------------------------------------


def test_weekend_vs_weekday():
    start = date(2026, 8, 31)  # a Monday; 28 days
    daily = {start + timedelta(days=i): (6000 if (start + timedelta(days=i)).weekday() >= 5 else 2000) for i in range(28)}
    i = insights.weekend_vs_weekday(daily, start, start + timedelta(days=28), "SGD")
    assert "Weekends cost you <b>200% more</b> a day (S$60.00 vs S$20.00" in i.text
    flat = {d: 2000 for d in daily}
    assert insights.weekend_vs_weekday(flat, start, start + timedelta(days=28), "SGD") is None


def test_rising_three_weeks_in_a_row():
    weeks = [{"Shopping": 1000, "Food": 5000}, {"Shopping": 2500, "Food": 4000}, {"Shopping": 4000, "Food": 6000}]
    (i,) = insights.rising_categories(weeks, "SGD")
    assert "<b>Shopping</b> went up 3 weeks in a row: S$10.00 → S$25.00 → S$40.00" in i.text


def test_budget_pace():
    i = insights.budget_pace(20000, 40000, date(2026, 9, 10), "Shopping", "SGD")  # S$20/day -> hits on day 20
    assert "hit your <b>Shopping</b> budget on the <b>20th</b>" in i.text
    assert insights.budget_pace(5000, 40000, date(2026, 9, 10), "Shopping", "SGD") is None   # on track
    assert "already over budget" in insights.budget_pace(45000, 40000, date(2026, 9, 10), "Shopping", "SGD").text


def test_question_or_entry():
    for q in ("how much on grab in august?", "How much did I spend in 2025?", "tháng này tiêu bao nhiêu?",
              "bao nhiêu tiền phở tháng 9", "what did I buy on shopee", "show me transport this month"):
        assert ask.is_question(q), q
    for e in ("lunch 12?", "pho 65k", "grab 12.5 yesterday", "salary 4200 to DBS", "kopi",
              # found in review: these are entries, not questions
              "mình đã mua sách 200k", "toi da an pho 65k va tra da 5k", "co tieu 50k", "xem phim 2 vé 240k",
              "total 45", "Total: 45", "tổng 300k", "list 5", "average 12", "tôi đã rút 200 từ DBS"):
        assert not ask.is_question(e), e
    # Right after the persona spoke: chat stays chat, money questions still go to the ledger.
    assert not ask.is_question("why are you so mean?", chatting=True)
    assert not ask.is_question("haha really?", chatting=True)
    assert ask.is_question("how much did I spend this week?", chatting=True)
    assert ask.is_question("show me august by category", chatting=True)
    assert not ask.is_question("what do you mean?", chatting=True)


# --- end to end --------------------------------------------------------------------

def query(**kw):
    q = {"show": "total", "type": "expense", "words": [], "categories": [], "wallets": [],
         "date_from": "2026-08-01", "date_to": "2026-08-31", "title": "Grab · August 2026"}
    q.update(kw)
    return q


def seed_august(env):
    env.say("/start")
    for d, text in (("2026-08-03", "grab 12"), ("2026-08-10", "grab 18.50"), ("2026-08-21", "Grab airport 32"),
                    ("2026-08-15", "phở 65k"), ("2026-09-02", "grab 10")):
        env.say(text)
        env.conn.execute("update transactions set occurred_on = %s where id = (select max(id) from transactions)", (d,))


def test_how_much_on_grab_in_august(env):
    seed_august(env)
    env.llm.queue.append(query(words=["grab"]))
    msg = env.say("how much on grab in august?")
    assert "🔎 <b>Grab · August 2026</b>" in msg.text and "“grab” · 01 Aug – 31 Aug 2026" in msg.text
    assert "Spent <b>S$62.50</b> across 3 entries (avg S$20.83)" in msg.text  # 12 + 18.50 + 32, not September's 10
    assert "21 Aug Grab airport S$32.00" in msg.text
    assert env.llm.calls[-1]["tool_choice"]["name"] == "query_ledger"
    assert env.conn.execute("select count(*) n from transactions").fetchone()["n"] == 5  # nothing was logged


def test_accents_dont_matter_and_breakdowns(env):
    seed_august(env)
    env.llm.queue.append(query(words=["pho"], title="Phở"))
    assert "65,000₫" in env.say("bao nhiêu tiền phở tháng 8").text
    env.llm.queue.append(query(show="by_category", title="August"))
    msg = env.say("show me august by category").text
    assert "• Transport S$62.50" in msg and "• Food &amp; Drinks S$3.25" in msg


def test_nothing_matched_and_bad_answers(env):
    seed_august(env)
    env.llm.queue.append(query(words=["netflix"]))
    assert "Nothing matched" in env.say("how much on netflix in august?").text
    env.llm.queue.append({"show": "total", "type": "expense"})  # no dates
    assert "couldn't work out what to look up" in env.say("how much?").text


def test_reply_to_the_persona_is_chat_not_a_lookup(env):
    env.say("/start")
    env.llm.replies.append("S$45 for lunch?!")
    env.say("lunch 45")
    env.llm.replies.append("Fine, fine.")
    env.say("haha really?")
    assert env.tg.reactions[-1].endswith("Fine, fine.")
    assert not [c for c in env.llm.calls if c.get("tool_choice", {}).get("name") == "query_ledger"]


def test_future_dates_are_clamped(env):
    seed_august(env)
    env.llm.queue.append(query(date_from="2026-12-01", date_to="2026-12-31", title="December " * 20))
    msg = env.say("how much in december?").text
    assert "Nothing matched" in msg and "29 Sep 2026" in msg   # today, not a reversed range


def test_lunch_with_a_question_mark_is_still_an_entry(env):
    env.say("/start")
    assert "Logged" in env.say("lunch 12?").text


def sunday_8pm(env):
    env.clock.now = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)  # Sun 27 Sep, 20:00 in Singapore


def test_sunday_recap(env):
    env.say("/start")
    env.say("/budget Shopping 100")
    env.say("lunch 14")
    env.conn.execute("update transactions set occurred_on = '2026-09-25'")
    env.say("shopee 95")   # S$95 of a S$100 budget by the 27th: on pace to pass it on the 29th
    env.conn.execute("update transactions set occurred_on = '2026-09-02' where description = 'shopee'")
    sunday_8pm(env)
    env.llm.replies.append("Solid week.")
    before = len(env.tg.sent)
    env.bot.run_tick(env.conn)
    recap = env.tg.reactions[-1]  # the recap carries the persona line, so the fake routes it here
    assert "🗓 <b>Your week</b> · 21 Sep – 27 Sep" in recap and "Spent <b>S$14.00</b>" in recap
    assert "Logged on 1 of 7 days" in recap and "Solid week." in recap
    assert "hit your <b>Shopping</b> budget on the <b>29th</b>" in recap
    assert "This is the weekly recap" in env.llm.chats[-1]["messages"][-1]["content"]
    env.bot.run_tick(env.conn)
    assert len([m for m in env.tg.reactions if "Your week" in m]) == 1  # once per Sunday
    assert len(env.tg.sent) == before


def test_recap_on_demand_and_plain_persona(env):
    env.say("/start")
    env.press("pers:plain", env.say("/persona").id)
    msg = env.say("/recap")
    assert "Your week" in msg.text and env.llm.chats == []


def test_monthly_report_shows_patterns(env):
    env.say("/start")
    start = date(2026, 8, 31)
    for i in range(30):
        d = start + timedelta(days=i)
        env.say(f"lunch {'60' if d.weekday() >= 5 else '20'}")
        env.conn.execute("update transactions set occurred_on = %s where id = (select max(id) from transactions)", (d,))
    msg = env.say("/report 2026-09")
    assert "<b>Patterns</b>" in msg.text and "Weekends cost you" in msg.text


def test_upcoming_renewals_in_the_recap(env):
    env.say("/start")
    env.say("/subscriptions add Netflix 17.98 monthly")
    env.conn.execute("update subscriptions set next_due = '2026-10-01'")
    sunday_8pm(env)
    env.press("pers:plain", env.say("/persona").id)
    msg = env.say("/recap")
    assert "Subscriptions renewing this week" in msg.text and "Netflix S$17.98 on Thu 01 Oct" in msg.text
