"""Vercel entry point: Telegram posts every update to /api/telegram."""
from __future__ import annotations

import hmac
import logging
import os
import sys

from fastapi import FastAPI, Header, HTTPException, Request
from starlette.concurrency import run_in_threadpool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from meow.config import get_settings  # noqa: E402
from meow.runtime import handle_update  # noqa: E402

logging.basicConfig(level=logging.INFO)
# httpx logs full request URLs, which contain the bot token. Keep them out of logs.
logging.getLogger("httpx").setLevel(logging.WARNING)
app = FastAPI(title="M.E.O.W. bot", docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/api/health")
def health():
    return {"ok": True}


@app.post("/api/telegram")
async def telegram_webhook(request: Request, x_telegram_bot_api_secret_token: str = Header(default="")):
    secret = get_settings().telegram_webhook_secret
    if not secret or not hmac.compare_digest(x_telegram_bot_api_secret_token, secret):
        raise HTTPException(status_code=401, detail="bad secret")
    update = await request.json()
    # Handled before responding: Vercel may freeze the function once the response is sent.
    # Always answer 200 so Telegram doesn't retry; duplicates are dropped by update_id anyway.
    await run_in_threadpool(handle_update, update)
    return {"ok": True}
