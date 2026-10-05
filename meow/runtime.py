"""Wires settings, Telegram, Claude and the database together."""
from __future__ import annotations

import logging
from functools import lru_cache

from . import db
from .bot import Bot
from .config import get_settings
from .telegram import TelegramAPI

log = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_bot() -> Bot:
    s = get_settings()
    llm = None
    if s.anthropic_api_key:
        import anthropic

        llm = anthropic.Anthropic(api_key=s.anthropic_api_key, timeout=20.0, max_retries=1)
    stt = None
    if s.groq_api_key:
        from .speech import groq_transcriber

        stt = groq_transcriber(s.groq_api_key, s.stt_model)
    return Bot(s, TelegramAPI(s.telegram_bot_token), llm, stt=stt)


def handle_update(update: dict) -> None:
    """Process one Telegram update. Errors are logged and the user gets a short note."""
    bot = get_bot()
    try:
        with db.connect(bot.s.database_url) as conn:
            bot.process_update(conn, update)
    except Exception:
        log.exception("failed to handle update %s", update.get("update_id"))
        chat_id = (update.get("message") or {}).get("chat", {}).get("id")
        if chat_id:
            try:
                bot.tg.send_message(chat_id, "😿 Something went wrong on my side. Nothing was saved, please try again.")
            except Exception:
                log.exception("could not send the error note")


def run_tick() -> dict:
    """Scheduled work: exchange rates and end-of-day reminders."""
    bot = get_bot()
    with db.connect(bot.s.database_url) as conn:
        return bot.run_tick(conn)
