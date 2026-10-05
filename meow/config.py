"""Settings, read from environment variables (and a local .env file when present)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

try:  # python-dotenv is only needed locally; Vercel injects env vars directly.
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass


def _ids(raw: str) -> set[int]:
    return {int(x) for x in raw.replace(" ", "").split(",") if x}


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str = field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", ""))
    telegram_webhook_secret: str = field(default_factory=lambda: os.getenv("TELEGRAM_WEBHOOK_SECRET", ""))
    database_url: str = field(default_factory=lambda: os.getenv("DATABASE_URL", ""))
    anthropic_api_key: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""))
    anthropic_model: str = field(default_factory=lambda: os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5"))
    # Empty = nobody. The bot is private in v1.
    allowed_user_ids: set[int] = field(default_factory=lambda: _ids(os.getenv("ALLOWED_USER_IDS", "")))
    home_currency: str = field(default_factory=lambda: os.getenv("HOME_CURRENCY", "SGD"))
    default_timezone: str = field(default_factory=lambda: os.getenv("DEFAULT_TIMEZONE", "Asia/Singapore"))
    # Sent by the Supabase scheduler to /api/... as X-Cron-Secret.
    cron_secret: str = field(default_factory=lambda: os.getenv("CRON_SECRET", ""))
    # A single expense at or above this (home currency) gets the "big spend" comment.
    big_expense: float = field(default_factory=lambda: float(os.getenv("BIG_EXPENSE", "50")))
    # The persona's reactions. Haiku is quick and cheap; set PERSONA_MODEL to try a bigger model.
    persona_model: str = field(default_factory=lambda: os.getenv("PERSONA_MODEL", "claude-haiku-4-5"))
    # Reading photos and screenshots.
    vision_model: str = field(default_factory=lambda: os.getenv("VISION_MODEL", "claude-haiku-4-5"))
    # A photo entry at or above this (home currency) is checked with you before logging.
    photo_confirm_above: float = field(default_factory=lambda: float(os.getenv("PHOTO_CONFIRM_ABOVE", "500")))
    llm_daily_call_cap: int = field(default_factory=lambda: int(os.getenv("LLM_DAILY_CALL_CAP", "300")))
    # US$ per million tokens, used to estimate the cost of each call.
    llm_input_price: float = field(default_factory=lambda: float(os.getenv("LLM_INPUT_PRICE_PER_MTOK", "1.0")))
    llm_output_price: float = field(default_factory=lambda: float(os.getenv("LLM_OUTPUT_PRICE_PER_MTOK", "5.0")))


def get_settings() -> Settings:
    return Settings()
