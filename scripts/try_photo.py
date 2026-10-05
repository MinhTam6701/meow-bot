"""Show what the bot reads from your screenshots, with the real Claude. Nothing is saved.

    python scripts/try_photo.py private/shopee.jpg private/dbs_paynow.jpg ...

Keep your screenshots in the private/ folder: git ignores it, so they're never committed.
"""
import glob
import mimetypes
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import anthropic  # noqa: E402

from meow import db, vision  # noqa: E402
from meow.config import get_settings  # noqa: E402
from meow.models import ParseContext  # noqa: E402
from meow.money import to_minor  # noqa: E402


def main(paths: list[str]) -> None:
    s = get_settings()
    client = anthropic.Anthropic(api_key=s.anthropic_api_key)
    uid = min(s.allowed_user_ids)
    with db.connect(s.database_url) as conn:
        user = db.get_user(conn, uid)
        ctx = ParseContext(today=date.today(), home_currency=user["home_currency"], wallets=db.wallets(conn, uid),
                           categories=db.categories(conn, uid))
        for path in paths:
            data = Path(path).read_bytes()
            media = mimetypes.guess_type(path)[0] or "image/jpeg"
            seen, log = vision.read_image(client, s.vision_model, data, media, None, ctx, user.get("full_name"))
            print(f"\n=== {path}  ({log.latency_ms} ms, {log.input_tokens} in / {log.output_tokens} out)")
            if seen is None:
                print("  could not read:", log.error)
                continue
            if not seen.payments:
                print("  no payment:", seen.question)
            for p in seen.payments:
                print(f"  {p.type} {p.amount} {p.currency} | {p.description} | {p.category} | "
                      f"date {p.date or '(none, today would be used)'} | wallet {p.wallet or '(default)'} | "
                      f"to {p.counterparty or '-'} ({p.counterparty_kind}) | note {p.note or '-'}")
                day = p.date or ctx.today
                window = 7 if day == ctx.today else 1  # same rule as the bot
                sign = -1 if p.type == "expense" else 1
                dup = db.find_duplicate(conn, uid, p.currency, sign * to_minor(p.amount, p.currency), p.type, day,
                                        window_days=window)
                if dup:
                        print(f"  ↳ looks already logged: {dup['description']} on {dup['occurred_on']}")


def expand(args: list[str]) -> list[str]:
    """Windows PowerShell doesn't expand private/*.jpg, so do it here."""
    out = []
    for a in args:
        matches = sorted(glob.glob(a)) if any(ch in a for ch in "*?[") else [a]
        out += matches
    return out


if __name__ == "__main__":
    files = expand(sys.argv[1:])
    if not files:
        sys.exit(__doc__ + "\nNo files found. Put screenshots in the private folder first.")
    main(files)
