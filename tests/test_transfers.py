"""Transfers found in real use (7 Oct): "30tr160k", sent-then-received amounts, typos, and Claude as a fallback."""
from decimal import Decimal

import pytest

from meow.money import parse_amount_token
from meow.transfers import TransferRequest, Unclear, parse_transfer
from tests.parser_cases import make_context

def one(env, sql, *args):
    return env.conn.execute(sql, args).fetchone()


pytestmark = pytest.mark.skipif(__import__("os").getenv("TEST_DATABASE_URL") is None, reason="TEST_DATABASE_URL not set")


@pytest.mark.parametrize("token,value", [
    ("30tr160k", 30_160_000), ("2m500k", 2_500_000), ("1tr2k", 1_002_000),
    ("1tr2", 1_200_000), ("30tr160", 30_160_000), ("65k", 65_000),
])
def test_million_plus_thousands(token, value):
    assert parse_amount_token(token).value == value
    assert parse_amount_token("5k3k") is None


@pytest.mark.parametrize("text", [
    "Transfer vp to dbs 30tr160k to 1471.2",      # the exact message that went wrong
    "Move 30tr160k from vp to dbs as 1471.2",
    "transfer vp 30160000 to dbs 1471.2",         # two amounts, no "as"
    "move 1471.2 to dbs from vp 30tr160k",        # amounts the other way round: matched by currency
    "chuyển 30.160.000 từ VP sang DBS nhận 1471.2",
])
def test_sent_and_received(text):
    t = parse_transfer(text, make_context())
    assert isinstance(t, TransferRequest), t
    assert (t.from_wallet.name, t.to_wallet.name) == ("VP", "DBS")
    assert t.amount == 30_160_000 and t.received[0] == Decimal("1471.2")


def test_what_the_rules_cant_read_goes_to_claude():
    ctx = make_context()
    assert isinstance(parse_transfer("transfer vp to dbs 30tr1x6", ctx), Unclear)       # unreadable amount
    assert isinstance(parse_transfer("chuyển ba mươi triệu từ VP qua DBS", ctx), Unclear)  # no digits
    assert not isinstance(parse_transfer("move 50 from DBS to Revolut", ctx), Unclear)  # unknown wallet: say so
    # Two amounts between wallets of the same currency can't be sent-and-received:
    ctx.wallets[1].currency = "SGD"
    assert "two amounts" in parse_transfer("move 50 from DBS to VP 20", ctx)


def test_real_message_end_to_end(env):
    env.say("/start")
    card = env.say("Transfer vp to dbs 30tr160k to 1471.2")
    assert "30,160,000₫ VP → DBS (S$1,471.20)" in card.text and "today's rate" not in card.text
    assert env.llm.calls == []                     # the rules read it, no AI call
    assert "DBS: <b>S$1,471.20</b>" in env.say("/balance").text


def test_typo_far_from_the_rate_is_not_saved(env):
    env.say("/start")
    msg = env.say("Transfer vp 30160000000 to dbs 1471.2")   # an extra 000
    assert "about <b>S$1,508,000.00</b> at today's rate" in msg.text and "Nothing saved" in msg.text
    assert one(env, "select count(*) n from transactions")["n"] == 0


def test_claude_reads_what_the_rules_cant(env):
    env.say("/start")
    env.llm.queue.append({"from_wallet": "VP", "to_wallet": "DBS", "amount_sent": "30000000"})
    card = env.say("chuyển ba mươi triệu từ VP qua DBS")
    assert "30,000,000₫ VP → DBS (S$1,500.00)" in card.text and "today's rate" in card.text
    assert env.llm.calls[-1]["tool_choice"]["name"] == "record_transfer"
    assert "30tr160k" in env.llm.calls[-1]["system"]
    assert one(env, "select purpose from llm_calls order by id desc limit 1")["purpose"] == "transfer"


def test_claude_can_ask_and_still_gets_checked(env):
    env.say("/start")
    env.llm.queue.append({"question": "How much did you move?"})
    msg = env.say("chuyển tiền từ VP qua DBS")
    assert "How much did you move?" in msg.text and "move 500 from DBS to VP" in msg.text
    env.llm.queue.append({"from_wallet": "VP", "to_wallet": "DBS", "amount_sent": "30160000000", "amount_received": "1471.2"})
    assert "Nothing saved" in env.say("chuyển ba mươi tỷ từ VP qua DBS nhận 1471.2").text


def test_without_ai_the_hint_stays(env):
    env.say("/start")
    env.bot.llm = None
    assert "How much?" in env.say("chuyển ba mươi triệu từ VP qua DBS").text
