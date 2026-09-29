"""Text and buttons for the confirm card and the pickers."""
from __future__ import annotations

from datetime import date, timedelta
from html import escape

from .models import CategoryInfo, WalletInfo
from .money import fmt


def day_label(day: date, today: date) -> str:
    if day == today:
        return "today"
    if day == today - timedelta(days=1):
        return "yesterday"
    return day.strftime("%a %d %b")


def totals_line(spent: dict[str, int]) -> str:
    if not spent:
        return "Nothing spent today."
    return "Today: " + " · ".join(fmt(v, c) for c, v in spent.items()) + " spent"


def render_card(rows: list[dict], today: date, spent_today: dict[str, int]) -> tuple[str, dict]:
    """rows come from db.get_batch."""
    live = [r for r in rows if not r["reversed"]]
    if not live:
        header = "↩️ <b>Undone</b>"
    elif len(rows) == 1:
        header = "✅ <b>Logged</b>"
    else:
        header = f"✅ <b>Logged {len(live)} entries</b>" + (" (some undone)" if len(live) < len(rows) else "")

    lines = [header, ""]
    for i, r in enumerate(rows, 1):
        sign = "+" if r["type"] == "income" else ""
        amount = sign + fmt(abs(r["amount_minor"]), r["currency"])
        prefix = f"{i}. " if len(rows) > 1 else ""
        main = f"{prefix}{r['category_emoji'] or '•'} {escape(r['description'] or '')} — <b>{amount}</b>"
        detail = f"    {escape(r['category_name'] or 'Uncategorised')} · {escape(r['wallet_name'])} · {day_label(r['occurred_on'], today)}"
        if r["reversed"]:
            main, detail = f"<s>{main}</s>", f"<s>{detail}</s>"
        lines += [main, detail]
    lines += ["", totals_line(spent_today)]
    text = "\n".join(lines)

    if not live:
        return text, {"inline_keyboard": []}

    batch = rows[0]["batch_id"].hex
    if len(rows) == 1:
        tx = rows[0]["id"]
        keyboard = [
            [btn("✅ OK", f"ok:{batch}"), btn("↩️ Undo", f"undo:{batch}")],
            [btn("🏷 Category", f"cat:{tx}"), btn("👛 Wallet", f"wal:{tx}")],
        ]
    else:
        keyboard = [[btn("✅ OK", f"ok:{batch}"), btn("↩️ Undo all", f"undo:{batch}")]]
        cat_buttons = [btn(f"🏷 {i}", f"cat:{r['id']}") for i, r in enumerate(rows, 1) if not r["reversed"]]
        keyboard += [cat_buttons[i:i + 5] for i in range(0, len(cat_buttons), 5)]
    return text, {"inline_keyboard": keyboard}


def category_picker(tx_id: int, batch_hex: str, cats: list[CategoryInfo], type_: str) -> dict:
    buttons = [btn(f"{c.emoji} {c.name}", f"setcat:{tx_id}:{c.id}") for c in cats if c.type == type_]
    rows = [buttons[i:i + 3] for i in range(0, len(buttons), 3)]
    rows.append([btn("« Back", f"back:{batch_hex}")])
    return {"inline_keyboard": rows}


def wallet_picker(tx_id: int, batch_hex: str, wallets: list[WalletInfo], currency: str) -> dict:
    buttons = [btn(w.name, f"setwal:{tx_id}:{w.id}") for w in wallets if w.currency == currency]
    rows = [buttons[i:i + 3] for i in range(0, len(buttons), 3)]
    rows.append([btn("« Back", f"back:{batch_hex}")])
    return {"inline_keyboard": rows}


def btn(text: str, data: str) -> dict:
    assert len(data.encode()) <= 64, data
    return {"text": text, "callback_data": data}
