"""The charts Mini App: Telegram's signature check, the data it asks for, and the page itself.

Telegram signs who opened the page (`initData`) with the bot token. Nothing private is ever in a URL:
the page sends that signature with each request, and gets back only the signer's own numbers.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qsl, urlencode

from . import db, report_data

PAGE = Path(__file__).resolve().parent / "webapp" / "report.html"
MAX_AGE = 24 * 3600  # a page left open longer than a day has to be reopened from the chat


def verify_init_data(init_data: str, bot_token: str, now: Optional[float] = None,
                     max_age: int = MAX_AGE) -> Optional[dict]:
    """The Telegram user who opened the page, or None if the signature is missing, wrong or stale.
    https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app"""
    if not init_data or not bot_token:
        return None
    try:
        fields = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=True))
    except ValueError:
        return None
    # Every field except `hash` is checked, including `signature` (Telegram's newer third-party
    # signature): leaving it out makes every real request fail.
    received = fields.pop("hash", "")
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not received or not hmac.compare_digest(expected, received):
        return None
    try:
        signed_at = int(fields.get("auth_date", "0"))
        user = json.loads(fields.get("user", "{}"))
    except ValueError:
        return None
    if (now if now is not None else time.time()) - signed_at > max_age or not isinstance(user.get("id"), int):
        return None
    return user


def report_json(conn, bot, init_data: str, kind: str, start: Optional[str], token: Optional[str] = None) -> dict:
    """{status, body} for the page's data request."""
    who = verify_init_data(init_data, token if token is not None else bot.s.telegram_bot_token)
    if not who:
        return {"status": 401, "body": {"error": "Please open this page from the bot again."}}
    user = db.get_user(conn, who["id"]) if bot.allowed(who["id"]) else None
    if not user:
        return {"status": 403, "body": {"error": "This bot is private."}}
    if kind not in report_data.KINDS:
        return {"status": 400, "body": {"error": "Unknown view."}}
    today = bot.today_for(user)
    try:
        day = date.fromisoformat(start) if start else today
    except ValueError:
        return {"status": 400, "body": {"error": "Bad date."}}
    return {"status": 200, "body": report_data.build(conn, user, kind, day, today)}


@lru_cache(maxsize=1)
def page_html() -> str:
    return PAGE.read_text(encoding="utf-8")


def chart_url(base: str, kind: str, start: date) -> str:
    # The view is chosen by the query string, not the path: Vercel may rewrite the path.
    return f"{base.rstrip('/')}/charts?" + urlencode({"view": "charts", "kind": kind, "start": start.isoformat()})


def chart_button(base: str, kind: str, start: date) -> dict:
    """An inline keyboard with one button that opens the charts inside Telegram."""
    return {"inline_keyboard": [[{"text": "📊 See charts", "web_app": {"url": chart_url(base, kind, start)}}]]}
