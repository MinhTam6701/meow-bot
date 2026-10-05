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



def totals_line_home(home: str, spent_home: int, unconverted: dict[str, int]) -> str:
    parts = [fmt(spent_home, home)] if spent_home or not unconverted else []
    parts += [fmt(v, c) for c, v in unconverted.items()]
    if not spent_home and not unconverted:
        return "Nothing spent today."
    return "Today: " + " + ".join(parts) + " spent"


def _amount(r: dict, home: str) -> str:
    sign = "+" if r["type"] == "income" else ""
    shown = sign + fmt(abs(r["amount_minor"]), r["currency"])
    if r["currency"] != home and r.get("amount_home") is not None:
        shown += f" (≈ {fmt(abs(r['amount_home']), home)})"
    return shown


def render_card(rows: list[dict], today: date, footer: str, home: str = "SGD") -> tuple[str, dict]:
    """rows come from db.get_batch; footer is the totals line."""
    if rows and all(r["type"] == "transfer" for r in rows):
        return render_transfer_card(rows, today)
    live = [r for r in rows if not r["reversed"]]
    if not live:
        header = "↩️ <b>Undone</b>"
    elif len(rows) == 1:
        header = "✅ <b>Logged</b>"
    else:
        header = f"✅ <b>Logged {len(live)} entries</b>" + (" (some undone)" if len(live) < len(rows) else "")

    lines = [header, ""]
    for i, r in enumerate(rows, 1):
        prefix = f"{i}. " if len(rows) > 1 else ""
        main = f"{prefix}{r['category_emoji'] or '•'} {escape(r['description'] or '')} — <b>{_amount(r, home)}</b>"
        detail = f"    {escape(r['category_name'] or 'Uncategorised')} · {escape(r['wallet_name'])} · {day_label(r['occurred_on'], today)}"
        if r["reversed"]:
            main, detail = f"<s>{main}</s>", f"<s>{detail}</s>"
        lines += [main, detail]
    lines += ["", footer]
    note = rows[0].get("card_note") if rows else None
    if note and live:
        lines += ["", note]
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


def render_transfer_card(rows: list[dict], today: date) -> tuple[str, dict]:
    out = next(r for r in rows if r["amount_minor"] < 0)
    inc = next(r for r in rows if r["amount_minor"] > 0)
    undone = all(r["reversed"] for r in rows)
    line = (f"{fmt(-out['amount_minor'], out['currency'])} {escape(out['wallet_name'])} → "
            f"{escape(inc['wallet_name'])}" + ("" if inc["currency"] == out["currency"]
                                               else f" ({fmt(inc['amount_minor'], inc['currency'])})"))
    lines = ["↩️ <b>Transfer undone</b>" if undone else "🔁 <b>Transfer</b>", "", f"<s>{line}</s>" if undone else line,
             f"    {day_label(out['occurred_on'], today)}"]
    note = rows[0].get("card_note")
    if note and not undone:
        lines += ["", note]
    if undone:
        return "\n".join(lines), {"inline_keyboard": []}
    batch = rows[0]["batch_id"].hex
    return "\n".join(lines), {"inline_keyboard": [[btn("✅ OK", f"ok:{batch}"), btn("↩️ Undo", f"undo:{batch}")]]}


def reminder_keyboard() -> dict:
    return {"inline_keyboard": [
        [btn("😻 No spend today", "eod:nospend")],
        [btn("⏰ In 1 hour", "eod:snooze"), btn("🙈 Skip today", "eod:skip")],
        [btn("✈️ Pause reminders", "eod:pausemenu")],
    ]}


def pause_keyboard() -> dict:
    return {"inline_keyboard": [
        [btn("3 days", "eod:pause:3"), btn("1 week", "eod:pause:7"), btn("2 weeks", "eod:pause:14")],
        [btn("« Back", "eod:back")],
    ]}


def persona_keyboard(current: str, personas: dict[str, str]) -> dict:
    rows = [[btn(("● " if k == current else "") + label, f"pers:{k}")] for k, label in personas.items()]
    return {"inline_keyboard": rows}


def roast_keyboard(current: int, labels: dict[int, str]) -> dict:
    return {"inline_keyboard": [[btn(("● " if k == current else "") + v, f"roast:{k}") for k, v in list(labels.items())[:2]],
                                [btn(("● " if k == current else "") + v, f"roast:{k}") for k, v in list(labels.items())[2:]]]}


def language_keyboard(current: str, langs: dict[str, str]) -> dict:
    return {"inline_keyboard": [[btn(("● " if k == current else "") + v, f"lang:{k}") for k, v in langs.items()]]}


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
