"""Voice notes: transcribe with Groq (faked here), then the same path as typed messages."""
from decimal import Decimal

import pytest

from meow.money import normalize_spoken
from meow.parser_llm import LLMCallLog
from tests.parser_cases import make_context
from meow.parser_rules import parse_with_rules
from tests.support import ME


class FakeSTT:
    def __init__(self):
        self.heard, self.calls, self.fail = [], [], False

    def __call__(self, audio: bytes, filename: str, prompt: str = ""):
        self.calls.append(filename)
        self.prompts = getattr(self, "prompts", []) + [prompt]
        log = LLMCallLog(model="whisper-large-v3", input_tokens=None, output_tokens=None, latency_ms=300,
                         ok=not self.fail, input_text="[voice]", output_json=None)
        if self.fail:
            log.error = "HTTP 500"
            return None, log
        return self.heard.pop(0), log


def voice(env, text=None, duration=4, mime="audio/ogg", kind="voice"):
    stt = env.bot.stt
    if text is not None:
        stt.heard.append(text)
    env.bot.process_update(env.conn, {"update_id": 800_000 + len(env.tg.sent), "message": {
        "message_id": 1, "chat": {"id": ME, "type": "private"}, "from": {"id": ME, "first_name": "Tam"},
        kind: {"file_id": "v1", "duration": duration, "mime_type": mime, "file_size": 9000}}})
    return env.tg.sent[-1]


@pytest.fixture
def venv(env):
    env.bot.stt = FakeSTT()
    env.say("/start")
    return env


def test_vietnamese_voice_note_is_logged_with_the_transcript(venv):
    card = voice(venv, "Ăn trưa 50 nghìn.")
    assert "Logged" in card.text and "50,000₫" in card.text and "Food &amp; Drinks" in card.text
    assert "🎙 “Ăn trưa 50 nghìn.”" in card.text
    row = venv.conn.execute("select amount_minor, currency, source, description from transactions").fetchone()
    assert row == {"amount_minor": -50000, "currency": "VND", "source": "voice", "description": "Ăn trưa"}
    assert venv.bot.stt.calls == ["voice.ogg"]
    call = venv.conn.execute("select purpose, ok, cost_usd from llm_calls where purpose = 'voice'").fetchone()
    assert call["ok"] and float(call["cost_usd"]) == pytest.approx(10 / 3600 * 0.111, abs=1e-6)  # 10-second minimum
    assert venv.tg.reactions  # the persona replies as usual


def test_english_voice_with_two_items(venv):
    card = voice(venv, "Grab 12 dollars, coffee 5 dollars 50 cents.")
    assert "Logged 2 entries" in card.text and "S$12.00" in card.text and "S$5.50" in card.text


def test_a_question_shows_what_was_heard(venv):
    msg = voice(venv, "I bought something nice")          # no amount
    assert msg.text.startswith("🎙 <i>“I bought something nice”</i>") and "didn't see an amount" in msg.text


def test_voice_transfer(venv):
    voice(venv, "chuyển 2 triệu từ VP sang cash")
    rows = venv.conn.execute("select amount_minor, currency from transactions order by id").fetchall()
    assert [r["amount_minor"] for r in rows] == [-2000000, 2000000]


def test_bank_name_after_a_comma_picks_the_wallet(venv):
    """The real case: Whisper writes "Game 33 nghìn, Vietcombank." with a comma before the bank."""
    venv.conn.execute("insert into wallets (user_id, name, type, currency, aliases) "
                      "values (%s, 'VCB', 'bank', 'VND', '{vietcombank}')", (ME,))
    card = voice(venv, "Game 33 nghìn, Vietcombank.")
    assert "33,000₫" in card.text and "VCB" in card.text and venv.llm.calls == []
    assert "VCB" in venv.bot.stt.prompts[-1] and "vietcombank" in venv.bot.stt.prompts[-1]


def test_too_long(venv):
    msg = voice(venv, duration=300)
    assert "under 120 seconds" in msg.text and venv.bot.stt.calls == []


def test_silence(venv):
    msg = voice(venv, "   ")
    assert "couldn't hear anything" in msg.text
    assert venv.conn.execute("select count(*) n from transactions").fetchone()["n"] == 0


def test_groq_down(venv):
    venv.bot.stt.fail = True
    msg = voice(venv)
    assert "couldn't process that voice note" in msg.text
    assert venv.conn.execute("select ok from llm_calls where purpose = 'voice'").fetchone()["ok"] is False


def test_audio_file_and_no_key(env):
    env.say("/start")
    msg = voice(env, kind="audio", mime="audio/mpeg")
    assert "aren't set up yet" in msg.text
    env.bot.stt = FakeSTT()
    voice(env, "kopi 1.8", kind="audio", mime="audio/mpeg")
    assert env.bot.stt.calls == ["voice.mp3"]


def test_reply_to_the_persona_by_voice(venv):
    venv.llm.replies.append("S$45 for lunch?!")
    venv.say("lunch 45")
    venv.llm.replies.append("A birthday, fine.")
    voice(venv, "It was my friend's birthday.")
    assert venv.tg.reactions[-1].endswith("A birthday, fine.")
    assert venv.conn.execute("select count(*) n from transactions").fetchone()["n"] == 1


# --- spoken amounts (also used for typed messages) -----------------------------------------

@pytest.mark.parametrize("said,want", [
    ("ăn trưa 50 nghìn", "ăn trưa 50k"),
    ("Ăn trưa 50 nghìn.", "Ăn trưa 50k"),
    ("phở 65 ngàn", "phở 65k"),
    ("cà phê 30 nghìn đồng", "cà phê 30k"),
    ("taxi 1 triệu 2", "taxi 1200k"),
    ("taxi 1 triệu 200 nghìn", "taxi 1200k"),
    ("2 triệu rưỡi tiền nhà", "2500k tiền nhà"),
    ("1,2 triệu", "1200k"),
    ("2 triệu 25", "2250k"),
    ("grab 12 dollars", "grab 12 sgd"),
    ("grab 12 dollars 50 cents", "grab 12.50 sgd"),
    ("coffee 5 US dollars", "coffee 5 usd"),
    ("đi grab 12 đô", "đi grab 12 sgd"),
    ("kopi 80 cents", "kopi 0.80 sgd"),
    ("pho 65k", "pho 65k"),                 # already compact: unchanged
    ("salary 4200 to DBS", "salary 4200 to DBS"),
    ("grab 12.5", "grab 12.5"),
])
def test_normalize_spoken(said, want):
    assert normalize_spoken(said) == want


@pytest.mark.parametrize("said,amount,currency,desc", [
    ("ăn trưa 50 nghìn", "50000", "VND", "ăn trưa"),
    ("phở 65 ngàn", "65000", "VND", "phở"),
    ("cà phê 30 nghìn đồng", "30000", "VND", "cà phê"),
    ("taxi 1 triệu 2", "1200000", "VND", "taxi"),
    ("grab 12 dollars", "12", "SGD", "grab"),
    ("kopi 80 cents", "0.80", "SGD", "kopi"),
])
def test_spoken_amounts_parse_right(said, amount, currency, desc):
    (e,) = parse_with_rules(normalize_spoken(said), make_context())
    assert (e.amount, e.currency, e.description) == (Decimal(amount), currency, desc)


def test_typed_nghin_is_vnd_too(env):
    env.say("/start")
    card = env.say("phở 65 nghìn")
    assert "65,000₫" in card.text and "S$65" not in card.text.split("Today")[0]
