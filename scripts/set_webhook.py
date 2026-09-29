"""Point Telegram at your Vercel deployment and register the command menu.

    python scripts/set_webhook.py https://meow-bot.vercel.app
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from meow.bot import COMMANDS  # noqa: E402
from meow.config import get_settings  # noqa: E402
from meow.telegram import TelegramAPI  # noqa: E402


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("Usage: python scripts/set_webhook.py https://<your-app>.vercel.app")
    s = get_settings()
    if not s.telegram_webhook_secret:
        sys.exit("Set TELEGRAM_WEBHOOK_SECRET in .env first (the same value as on Vercel).")
    tg = TelegramAPI(s.telegram_bot_token)
    url = sys.argv[1].rstrip("/") + "/api/telegram"
    tg.call("setWebhook", url=url, secret_token=s.telegram_webhook_secret,
            allowed_updates=["message", "callback_query"], drop_pending_updates=True, max_connections=10)
    tg.call("setMyCommands", commands=[{"command": c, "description": d} for c, d in COMMANDS])
    info = tg.call("getWebhookInfo")
    print(f"Webhook set to {info['url']}")
    if info.get("last_error_message"):
        print(f"Last error reported by Telegram: {info['last_error_message']}")


if __name__ == "__main__":
    main()
