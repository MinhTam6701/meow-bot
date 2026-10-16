"""One wallet's balance: /balance DBS, or just asking "what is my DBS balance?" (16 Oct)."""
import pytest

from meow import ask
from tests.parser_cases import make_context

pytestmark = pytest.mark.skipif(__import__("os").getenv("TEST_DATABASE_URL") is None, reason="TEST_DATABASE_URL not set")


def test_balance_questions():
    for q in ("what is y current DBS balance?", "DBS balance", "số dư VP", "so du vp", "how much is in VP?",
              "how much money do I have in DBS", "balances"):
        assert ask.is_balance_question(q), q
    for e in ("balance transfer fee 5", "how much on grab in august?", "lunch 12", "pho 65k"):
        assert not ask.is_balance_question(e), e


def test_wallets_named():
    ctx = make_context()
    assert [w.name for w in ask.wallets_named("what is my dbs balance?", ctx)] == ["DBS"]
    assert [w.name for w in ask.wallets_named("vp and DBS", ctx)] == ["VP", "DBS"]
    assert ask.wallets_named("my balance", ctx) == []


def setup(env):
    env.say("/start")
    env.say("/setbalance DBS 1000")
    env.say("lunch 12")
    env.say("pho 65k")


def test_one_wallet_by_command(env):
    setup(env)
    msg = env.say("/balance DBS").text
    assert msg.startswith("👛 <b>DBS</b>: <b>S$988.00</b>")
    assert "VP" not in msg and "Total" not in msg
    assert "lunch −S$12.00" in msg and "Balance set to" in msg
    assert "/setbalance DBS amount" in msg


def test_one_wallet_in_another_currency_shows_home_value(env):
    setup(env)
    msg = env.say("/balance vp").text
    assert "<b>VP</b>: <b>-65,000₫</b>" in msg and "≈ -S$3.25" in msg


def test_asking_in_words_gives_the_balance_not_spending(env):
    setup(env)
    msg = env.say("what is y current DBS balance?").text
    assert msg.startswith("👛 <b>DBS</b>: <b>S$988.00</b>") and "Spent" not in msg
    assert env.llm.calls == []                                 # no AI needed
    both = env.say("số dư DBS và VP").text
    assert "DBS: <b>S$988.00</b>" in both and "VP:" in both and "Total" in both
    assert "Balances" in env.say("what's my balance?").text    # no wallet named: all of them


def test_unknown_wallet_and_plain_balance(env):
    setup(env)
    assert "don't have a wallet called \"Revolut\"" in env.say("/balance Revolut").text
    full = env.say("/balance").text
    assert "DBS: <b>S$988.00</b>" in full and "VP: <b>-65,000₫</b>" in full and "Total" in full
