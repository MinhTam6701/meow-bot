"""Run the bot on your laptop with long polling (no public URL needed).

    python scripts/poll.py            # refuses if a webhook is set
    python scripts/poll.py --force    # removes the webhook first (re-run set_webhook.py after)
"""
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from meow.runtime import get_bot, handle_update  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def main() -> None:
    tg = get_bot().tg
    info = tg.call("getWebhookInfo")
    if info.get("url"):
        if "--force" not in sys.argv:
            sys.exit(f"A webhook is set ({info['url']}). Polling would conflict with it.\n"
                     "Run with --force to remove it, then run scripts/set_webhook.py when you're done.")
        tg.call("deleteWebhook")
        print("Webhook removed.")
    print("Polling… press Ctrl+C to stop.")
    offset = None
    while True:
        updates = tg.http.post(f"{tg.base}/getUpdates", json={
            "offset": offset, "timeout": 30, "allowed_updates": ["message", "callback_query"],
        }, timeout=40).json().get("result", [])
        for update in updates:
            offset = update["update_id"] + 1
            handle_update(update)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
