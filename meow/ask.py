"""Questions about your own spending: "how much on Grab in August?", "tháng này tiêu bao nhiêu cho ăn uống?".

Claude only turns the question into a filter (words, categories, wallets, dates, what to show).
Code fetches the entries and does every sum, so the numbers come from your ledger, not the model.
"""
from __future__ import annotations

import re
import time
from collections import defaultdict
from datetime import date
from html import escape
from typing import Any, Literal, Optional

from pydantic import BaseModel, ValidationError

from .models import ParseContext
from .money import fmt
from .parser_llm import LLMCallLog
from .text import normalize

TOOL_NAME = "query_ledger"

# Unmistakable questions, even with a number in them ("how much did I spend in 2025?").
_STRONG = re.compile(r"^\s*(how much|how many|how often|what|what's|whats|when|where|which|did i|do i|have i|am i)\b"
                     r"|\bbao (nhiêu|nhieu)\b", re.IGNORECASE)
# Questions only when there's no amount: "total 45", "xem phim 240k" and "mình đã mua sách 200k" are entries.
_WEAK = re.compile(r"^\s*(show me|tell me|list|total|average|compare|tổng|tong|mình đã|minh da|tôi đã|toi da|"
                   r"em đã|em da|có tiêu|co tieu|đã tiêu|da tieu|tiêu|tieu|chi tiêu|chi tieu|xem|liệt kê|liet ke)\b",
                   re.IGNORECASE)
# Right after the persona spoke, a question goes to the ledger only if it's about your data
# ("show me august by category"); "why are you so mean?" or "what do you mean?" is chat.
_ABOUT_DATA = re.compile(
    r"how much|how many|bao (nhiêu|nhieu)|\b(spen[dt]|spending|cost|paid|pay|total|tổng|tong|tiêu|tieu|chi|"
    r"category|categories|budget|today|yesterday|week|month|year|tuần|tuan|tháng|thang|năm|nam|hôm|hom|"
    r"jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|january|february|march|april|june|july|august|"
    r"september|october|november|december)\b", re.IGNORECASE)


_SPENDING = re.compile(r"\b(spen[dt]|spending|cost|costs|paid|tiêu|tieu|chi tiêu|chi tieu|hết bao|het bao)\b", re.IGNORECASE)


def is_question(text: str, chatting: bool = False) -> bool:
    """A question about past spending, not an entry. "lunch 12?" is still an entry, and right after the
    persona spoke, "what do you mean?" is chat. Statements count too: "my housing spending last month"."""
    t = text.strip()
    about_data = bool(_ABOUT_DATA.search(t))
    if _STRONG.search(t):
        return about_data or not chatting
    if re.search(r"\d", t):
        return False
    asked = bool(_WEAK.match(t)) or t.endswith("?") or bool(_SPENDING.search(t))
    if chatting:
        return about_data and asked
    # No amount, so it can't be an entry anyway: anything about your data is worth looking up.
    return asked or about_data


class Query(BaseModel):
    show: Literal["total", "list", "by_category", "by_month", "by_wallet"] = "total"
    type: Literal["expense", "income"] = "expense"
    words: list[str] = []                 # matched against descriptions, accents ignored
    categories: list[str] = []
    wallets: list[str] = []
    date_from: date
    date_to: date
    title: str = ""


def build_tool(ctx: ParseContext) -> dict:
    cats = sorted({c.name for c in ctx.categories})
    return {
        "name": TOOL_NAME,
        "description": "Describe which ledger entries answer the user's question.",
        "input_schema": {
            "type": "object",
            "properties": {
                "show": {"type": "string", "enum": ["total", "list", "by_category", "by_month", "by_wallet"],
                         "description": "total: one sum. list: the entries. by_*: a breakdown."},
                "type": {"type": "string", "enum": ["expense", "income"]},
                "words": {"type": "array", "items": {"type": "string"},
                          "description": "Shop or item words to look for in descriptions, e.g. ['grab'] or ['phở', 'pho']. "
                                         "Empty if the question is about a category or everything."},
                "categories": {"type": "array", "items": {"type": "string", "enum": cats}},
                "wallets": {"type": "array", "items": {"type": "string", "enum": [w.name for w in ctx.wallets]}},
                "date_from": {"type": "string", "description": "YYYY-MM-DD, inclusive"},
                "date_to": {"type": "string", "description": "YYYY-MM-DD, inclusive"},
                "title": {"type": "string", "description": "A short heading, e.g. 'Grab · August 2026'."},
            },
            "required": ["show", "type", "date_from", "date_to", "title"],
        },
    }


def build_system(ctx: ParseContext) -> str:
    return f"""You turn a question about the user's own spending into a filter over their ledger.
Today is {ctx.today.isoformat()} ({ctx.today.strftime('%A')}). Weeks start on Monday.
The user writes English, Vietnamese or a mix. "this month" = from the 1st of this month to today;
"last month" = the whole previous month; a month name without a year = the most recent one that has started;
"this year" = 1 Jan to today. With no time given, use the last 30 days.
Use 'words' for shops or items (Grab, Shopee, phở, kopi); use 'categories' for kinds of spending (food, transport).
For "how much did I earn/receive" use type income. Always answer by calling the {TOOL_NAME} tool."""


def to_query(client: Any, model: str, question: str, ctx: ParseContext) -> tuple[Optional[Query], LLMCallLog]:
    log = LLMCallLog(model=model, input_tokens=None, output_tokens=None, latency_ms=0, ok=False,
                     input_text=question, output_json=None)
    start = time.monotonic()
    try:
        resp = client.messages.create(model=model, max_tokens=400, system=build_system(ctx), tools=[build_tool(ctx)],
                                      tool_choice={"type": "tool", "name": TOOL_NAME},
                                      messages=[{"role": "user", "content": question}])
        usage = getattr(resp, "usage", None)
        log.input_tokens = getattr(usage, "input_tokens", None)
        log.output_tokens = getattr(usage, "output_tokens", None)
        block = next((b for b in resp.content if getattr(b, "type", "") == "tool_use"), None)
        if block is None:
            log.error = "no tool call"
            return None, log
        log.output_json = dict(block.input)
        q = Query.model_validate(block.input)
        if q.date_from > q.date_to:
            q.date_from, q.date_to = q.date_to, q.date_from
        q.date_to = min(q.date_to, ctx.today)
        q.date_from = min(q.date_from, q.date_to)
        q.title = q.title[:80]
        log.ok = True
        return q, log
    except ValidationError as exc:
        log.error = f"invalid: {exc.errors()[:3]}"[:500]
        return None, log
    except Exception as exc:
        log.error = f"{type(exc).__name__}: {exc}"[:500]
        return None, log
    finally:
        log.latency_ms = int((time.monotonic() - start) * 1000)


def _words(text: str) -> str:
    return " " + " ".join(normalize(w) for w in re.findall(r"[^\W_]+", text or "")) + " "


def matches(row: dict, q: Query) -> bool:
    if q.categories and row["category"] not in q.categories:
        return False
    if q.wallets and row["wallet"] not in q.wallets:
        return False
    if q.words:
        desc = _words(row["description"])
        return any(_words(w).strip() and _words(w) in desc for w in q.words)
    return True


def answer(rows: list[dict], q: Query, home: str) -> str:
    """Everything here is plain arithmetic over the matching entries."""
    hit = [r for r in rows if matches(r, q)]
    span = (f"{q.date_from.strftime('%d %b %Y')}" if q.date_from == q.date_to
            else f"{q.date_from.strftime('%d %b')} – {q.date_to.strftime('%d %b %Y')}")
    looked = []
    if q.words:
        looked.append("“" + "”, “".join(escape(w) for w in q.words) + "”")
    if q.categories:
        looked.append(", ".join(escape(c) for c in q.categories))
    if q.wallets:
        looked.append(", ".join(escape(w) for w in q.wallets))
    filt = (" · ".join(looked) + " · " if looked else "") + span
    head = f"🔎 <b>{escape(q.title or 'Your question')}</b>\n<i>{filt}</i>\n"
    if not hit:
        return head + "\nNothing matched. Try other words, or a wider date range."

    sign = -1 if q.type == "expense" else 1
    amounts = [sign * r["amount_home"] for r in hit if r["amount_home"] is not None]
    total, n = sum(amounts), len(hit)
    verb = "Spent" if q.type == "expense" else "Received"
    lines = [head, f"{verb} <b>{fmt(total, home)}</b> across {n} {'entry' if n == 1 else 'entries'}"
             + (f" (avg {fmt(round(total / len(amounts)), home)})" if len(amounts) > 1 else "")]
    missing = n - len(amounts)
    if missing:
        lines.append(f"<i>{missing} without an exchange rate yet, not in the total.</i>")

    def group(key) -> list[str]:
        sums: dict[str, int] = defaultdict(int)
        for r in hit:
            if r["amount_home"] is not None:
                sums[key(r)] += sign * r["amount_home"]
        top = sorted(sums.items(), key=lambda kv: -kv[1])[:8]
        return [f"• {escape(k)} {fmt(v, home)}" + (f" · {v * 100 // total}%" if total else "") for k, v in top]

    if q.show == "by_category":
        lines += [""] + group(lambda r: r["category"] or "Uncategorised")
    elif q.show == "by_month":
        sums: dict[date, int] = defaultdict(int)
        for r in hit:
            if r["amount_home"] is not None:
                sums[r["occurred_on"].replace(day=1)] += sign * r["amount_home"]
        lines += [""] + [f"• {m.strftime('%b %Y')} {fmt(v, home)}" for m, v in sorted(sums.items())]
    elif q.show == "by_wallet":
        lines += [""] + group(lambda r: r["wallet"])
    if q.show == "list" or (q.show == "total" and n <= 5):
        shown = hit[-10:]
        lines += [""] + [f"• {r['occurred_on'].strftime('%d %b')} {escape(r['description'] or '')} "
                         f"{fmt(abs(r['amount_minor']), r['currency'])}" for r in shown]
        if n > len(shown):
            lines.append(f"… and {n - len(shown)} earlier")
    elif n > 1:
        biggest = max((r for r in hit if r["amount_home"] is not None), key=lambda r: sign * r["amount_home"], default=None)
        if biggest:
            lines.append(f"Biggest: {escape(biggest['description'] or '')} {fmt(abs(biggest['amount_minor']), biggest['currency'])}"
                         f" on {biggest['occurred_on'].strftime('%d %b')}")
    return "\n".join(lines)
