"""The `env` fixture: a Bot wired to a real Postgres (not Supabase), fake Telegram and fake Claude.

Set TEST_DATABASE_URL to a throwaway database; the tests wipe its public schema.
Tests that use `env` are skipped when it isn't set.
"""
import random
from decimal import Decimal
from types import SimpleNamespace

import psycopg
import pytest
from psycopg.rows import dict_row

from meow.bot import Bot
from meow.config import Settings
from tests.support import DB_URL, ME, NOW, SCHEMA, FakeLLM, FakeTelegram


@pytest.fixture
def env():
    if not DB_URL:
        pytest.skip("TEST_DATABASE_URL not set")
    conn = psycopg.connect(DB_URL, row_factory=dict_row, autocommit=True)
    conn.execute("drop schema public cascade; create schema public;")
    conn.execute(SCHEMA)
    conn.autocommit = False
    tg, llm = FakeTelegram(), FakeLLM()
    settings = Settings(allowed_user_ids={ME}, anthropic_model="claude-haiku-4-5")
    clock = SimpleNamespace(now=NOW)
    fx_calls = []

    def fake_rates(base):
        fx_calls.append(base)
        return {"SGD": Decimal(1), "VND": Decimal(20000), "USD": Decimal("0.8")}

    bot = Bot(settings, tg, llm, clock=lambda: clock.now, fx_fetch=fake_rates, rng=random.Random(1))
    counter = iter(range(1, 10_000))

    def say(text, user=ME):
        bot.process_update(conn, {"update_id": next(counter), "message": {
            "message_id": 1, "chat": {"id": user, "type": "private"},
            "from": {"id": user, "first_name": "Tam"}, "text": text}})
        return tg.sent[-1] if tg.sent else None

    def press(data, message_id, user=ME):
        bot.process_update(conn, {"update_id": next(counter), "callback_query": {
            "id": "cb", "from": {"id": user}, "data": data,
            "message": {"message_id": message_id, "chat": {"id": user}}}})
        return tg.edits[-1] if tg.edits else None

    yield SimpleNamespace(conn=conn, tg=tg, llm=llm, bot=bot, say=say, press=press, clock=clock, fx_calls=fx_calls)
    conn.close()
