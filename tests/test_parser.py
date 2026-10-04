from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from meow.money import fmt, parse_amount_token, to_minor
from meow.parser import parse_message
from meow.parser_rules import parse_with_rules
from tests.parser_cases import CASES, TODAY, make_context


def as_tuples(entries):
    return [
        (e.amount, e.currency, e.type, e.category, (TODAY - e.date).days, e.wallet)
        for e in entries
    ]


def expected_tuples(expected):
    return [(Decimal(a), c, t, cat, d, w) for a, c, t, cat, d, w in expected]


@pytest.mark.parametrize("text,expected", [(t, e) for t, e, via in CASES if via == "rule"])
def test_rule_cases(text, expected):
    entries = parse_with_rules(text, make_context())
    assert entries is not None, f"rules should handle: {text}"
    assert as_tuples(entries) == expected_tuples(expected)


@pytest.mark.parametrize("text", [t for t, e, via in CASES if via == "llm"])
def test_hard_cases_fall_back_to_llm(text):
    assert parse_with_rules(text, make_context()) is None


@pytest.mark.parametrize("text", ["hello", "65k", "what did I spend?", "grab 12 15", "an umbrella 20"])
def test_rules_do_not_guess(text):
    assert parse_with_rules(text, make_context()) is None


@pytest.mark.parametrize("token,value,cur", [
    ("65k", "65000", "VND"), ("12.5", "12.5", "SGD"), ("65.000", "65000", "VND"),
    ("1,200", "1200", "SGD"), ("1,200,000", "1200000", "VND"), ("1tr2", "1200000", "VND"),
    ("$5", "5", "SGD"), ("50000đ", "50000", "VND"), ("6sgd", "6", "SGD"), ("12,5", "12.5", "SGD"),
    ("2.5m", "2500000", "VND"), ("us$9", "9", "USD"), ("$5k", "5000", "SGD"),
])
def test_amount_tokens(token, value, cur):
    tok = parse_amount_token(token)
    assert tok is not None
    assert tok.resolve("SGD") == (Decimal(value), cur)


@pytest.mark.parametrize("token", ["abc", "12a", "0", "-5", "1.2.3"])
def test_bad_amount_tokens(token):
    assert parse_amount_token(token) is None


def test_minor_units_and_format():
    assert to_minor(Decimal("12.345"), "SGD") == 1235
    assert to_minor(Decimal("65000"), "VND") == 65000
    assert fmt(-1235, "SGD") == "-S$12.35"
    assert fmt(65000, "VND") == "65,000₫"
    assert fmt(1200, "MYR") == "12.00 MYR"


# --- LLM fallback, with a fake Anthropic client ------------------------------------

class FakeClient:
    def __init__(self, tool_input, usage=(900, 80)):
        self.tool_input = tool_input
        self.usage = usage
        self.calls = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        block = SimpleNamespace(type="tool_use", name="record_transactions", input=self.tool_input)
        return SimpleNamespace(content=[block], usage=SimpleNamespace(input_tokens=self.usage[0], output_tokens=self.usage[1]))


def test_llm_used_only_when_rules_fail():
    client = FakeClient({"entries": []})
    result = parse_message("pho 65k", make_context(), llm_client=client)
    assert result.parser == "rule" and client.calls == []


def test_llm_entries_are_validated_and_cleaned():
    logs = []
    client = FakeClient({"entries": [
        {"amount": "23", "currency": "sgd", "type": "expense", "category": "Haircuts",
         "description": "haircut", "date": TODAY.isoformat(), "wallet": "Citibank"},
    ]})
    result = parse_message("paid 23 for the thing at the market", make_context(), llm_client=client, on_llm_call=logs.append)
    assert result.parser == "llm"
    e = result.entries[0]
    assert (e.amount, e.currency, e.category, e.wallet) == (Decimal("23"), "SGD", "Other", None)
    assert logs[0].ok and logs[0].input_tokens == 900
    call = client.calls[0]
    assert call["tool_choice"] == {"type": "tool", "name": "record_transactions"}
    assert "2026-09-29" in call["system"]


def test_llm_question_is_passed_through():
    client = FakeClient({"entries": [], "question": "Was that 20 SGD or 20k VND?"})
    result = parse_message("taxi twenty-ish", make_context(), llm_client=client)
    assert result.entries == [] and result.question == "Was that 20 SGD or 20k VND?"


def test_llm_bad_output_becomes_a_question_not_a_crash():
    logs = []
    client = FakeClient({"entries": [{"amount": "-5", "currency": "SGD"}]})
    result = parse_message("something weird 5 5", make_context(), llm_client=client, on_llm_call=logs.append)
    assert result.entries == [] and result.question
    assert logs[0].ok is False and "ValidationError" in logs[0].error


def test_llm_call_matches_real_sdk_signature():
    """The fake client accepts anything, so check the arguments against the real SDK."""
    import inspect

    from anthropic.resources.messages import Messages

    client = FakeClient({"entries": []})
    parse_message("paid 23 for the thing at the market", make_context(), llm_client=client)
    allowed = inspect.signature(Messages.create).parameters
    unknown = set(client.calls[0]) - set(allowed)
    assert not unknown, f"not accepted by anthropic SDK: {unknown}"


def test_llm_api_error_is_logged_not_raised():
    class Boom:
        messages = None

        def __init__(self):
            self.messages = self

        def create(self, **kw):
            raise RuntimeError("overloaded")

    logs = []
    result = parse_message("paid 23 for the thing at the market", make_context(), llm_client=Boom(), on_llm_call=logs.append)
    assert result.question and not result.entries
    assert logs[0].ok is False and "overloaded" in logs[0].error
