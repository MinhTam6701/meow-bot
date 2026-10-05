"""The persona as an AI character: a separate message that reacts to what was logged."""
from datetime import timedelta

from tests.test_bot_flow import ME, buttons, env  # noqa: F401  (fixture)


def facts(env) -> str:
    return env.llm.chats[-1]["messages"][-1]["content"]


def test_reaction_is_its_own_message_with_the_facts(env):
    env.say("/start")
    env.say("/budget everyday 600")
    card = env.say("lunch 7.32")
    assert "<i>" not in card.text and "Logged" in card.text
    assert env.tg.reactions == ["😼 [persona] Nice one."]
    f = facts(env)
    assert '"lunch", S$7.32, category Food & Drinks, wallet DBS' in f
    assert "First time they've logged this exact item" in f
    assert "Mochi's daily bowl" in f and "left today" in f
    assert env.llm.chats[-1]["model"] == "claude-haiku-4-5"
    assert "tools" not in env.llm.chats[-1]
    assert env.conn.execute("select count(*) n from llm_calls where purpose = 'persona'").fetchone()["n"] == 1


def test_unusual_price_is_compared_with_history(env):
    env.say("/start")
    for _ in range(3):
        env.say("lunch 6 yesterday")
    env.say("lunch 45")
    f = facts(env)
    assert "Their usual price for this: S$6.00 (logged 3 times" in f
    assert "Earlier today" not in f
    env.say("kopi 2")
    assert "Earlier today: lunch S$45.00" in facts(env)
    assert "ask why" in env.llm.chats[-1]["system"]


def test_vnd_entry_shows_both_currencies(env):
    env.say("/start")
    env.say("pho 65k")
    assert "65,000₫ (about S$3.25)" in facts(env)


def test_budget_alert_stays_on_card_and_reaches_the_persona(env):
    env.say("/start")
    env.say("/budget Food 20")
    over = env.say("dinner 25")
    assert "🚨 <b>Food &amp; Drinks</b> budget" in over.text and "<i>" not in over.text
    assert "🚨 Food & Drinks budget: S$25.00 / S$20.00 (125%)" in facts(env)


def test_claude_down_falls_back_to_a_written_line(env):
    env.say("/start")
    env.llm.fail_chat = True
    line = env.say("dinner 120")
    card = env.tg.sent[-2]
    assert "Logged" in card.text  # the entry is saved and the card goes first
    assert line.id == card.id + 1 and line.text.startswith("😼") and "S$120.00" in line.text  # pre-written line
    assert env.conn.execute("select count(*) n from transactions").fetchone()["n"] == 1
    assert env.conn.execute("select ok from llm_calls where purpose = 'persona'").fetchone()["ok"] is False


def test_answering_the_persona_continues_the_chat(env):
    env.say("/start")
    env.llm.replies.append("S$45 for lunch?! What was it, gold-plated chicken rice?")
    env.say("lunch 45")
    env.llm.replies.append("A birthday treat, fine. Happy birthday to them!")
    before = len(env.tg.sent)
    env.say("it was my friend's birthday")
    assert len(env.tg.sent) == before  # no "didn't see an amount"
    assert env.tg.reactions[-1] == "😼 [persona] A birthday treat, fine. Happy birthday to them!"
    f = facts(env)
    assert "gold-plated chicken rice" in f and "my friend's birthday" in f
    assert env.conn.execute("select count(*) n from transactions").fetchone()["n"] == 1

    env.clock.now += timedelta(minutes=31)  # too late: back to normal
    assert "didn't see an amount" in env.say("thanks").text


def test_plain_persona_never_chats(env):
    env.say("/start")
    env.press("pers:plain", env.say("/persona").id)
    env.say("lunch 45")
    assert "didn't see an amount" in env.say("it was a birthday").text
    assert env.llm.chats == []
