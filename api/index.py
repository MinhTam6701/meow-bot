"""Vercel entry point: Telegram posts every update to /api/telegram."""
from __future__ import annotations

import hmac
import logging
import os
import sys

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from meow.config import get_settings  # noqa: E402
from meow import webapp  # noqa: E402
from meow.runtime import charts_data, handle_update, run_tick  # noqa: E402

logging.basicConfig(level=logging.INFO)
# httpx logs full request URLs, which contain the bot token. Keep them out of logs.
logging.getLogger("httpx").setLevel(logging.WARNING)
app = FastAPI(title="M.E.O.W. bot", docs_url=None, redoc_url=None, openapi_url=None)


# Vercel may hand the request to this function under a rewritten path (e.g. /api/index),
# so the routes accept any path and look at the query string and headers instead:
#   GET  ?view=charts               the charts Mini App page (no data in it)
#   GET  anything else              health check
#   POST X-Telegram-Init-Data       the page asking for numbers (Telegram's signature decides whose)
#   POST X-Cron-Secret              the scheduler tick
#   POST anything else              the Telegram webhook (secret token required)
log = logging.getLogger("meow.api")


@app.get("/{path:path}")
def health(request: Request, path: str = ""):
    if request.query_params.get("view") == "charts":
        return HTMLResponse(webapp.page_html(), headers={"Cache-Control": "no-cache"})
    return {"ok": True}


@app.post("/{path:path}")
async def telegram_webhook(request: Request, path: str = "", x_telegram_bot_api_secret_token: str = Header(default=""),
                           x_cron_secret: str = Header(default=""), x_telegram_init_data: str = Header(default="")):
    if x_telegram_init_data:
        try:
            body = await request.json()
        except ValueError:
            body = {}
        result = await run_in_threadpool(charts_data, x_telegram_init_data, str(body.get("kind") or "month"),
                                         body.get("start") or None)
        return JSONResponse(result["body"], status_code=result["status"], headers={"Cache-Control": "no-store"})

    if x_cron_secret:
        # The Supabase scheduler (pg_cron + pg_net) calls this every 15 minutes.
        cron = get_settings().cron_secret
        if not cron or not hmac.compare_digest(x_cron_secret, cron):
            log.warning("tick rejected: bad cron secret")
            raise HTTPException(status_code=401, detail="bad secret")
        return {"ok": True, **(await run_in_threadpool(run_tick))}

    secret = get_settings().telegram_webhook_secret
    if not secret or not hmac.compare_digest(x_telegram_bot_api_secret_token, secret):
        log.warning("webhook rejected: bad or missing secret (path=/%s)", path)
        raise HTTPException(status_code=401, detail="bad secret")
    update = await request.json()
    # Handled before responding: Vercel may freeze the function once the response is sent.
    # Always answer 200 so Telegram doesn't retry; duplicates are dropped by update_id anyway.
    await run_in_threadpool(handle_update, update)
    return {"ok": True}
