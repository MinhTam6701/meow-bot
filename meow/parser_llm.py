"""LLM fallback parser: Claude with forced tool calling, validated by Pydantic.

The model only proposes structured entries. Code validates them and does all
money maths afterwards.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Callable, Optional

from pydantic import ValidationError

from .defaults import CATEGORY_HINTS
from .models import Entry, ParseContext, ParseResult

TOOL_NAME = "record_transactions"


@dataclass
class LLMCallLog:
    model: str
    input_tokens: Optional[int]
    output_tokens: Optional[int]
    latency_ms: int
    ok: bool
    input_text: str
    output_json: Optional[dict]
    error: Optional[str] = None


def build_tool(ctx: ParseContext) -> dict:
    currencies = sorted({w.currency for w in ctx.wallets} | {ctx.home_currency, "VND", "SGD", "USD"})
    return {
        "name": TOOL_NAME,
        "description": "Record the money movements found in the user's message, or ask one short question.",
        "input_schema": {
            "type": "object",
            "properties": {
                "entries": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "amount": {"type": "string", "description": "Positive number as a plain decimal string, e.g. '65000' or '12.50'. No separators or symbols."},
                            "currency": {"type": "string", "description": f"ISO 4217 code. Usually one of {currencies}."},
                            "type": {"type": "string", "enum": ["expense", "income"]},
                            "category": {"type": "string", "enum": ctx.category_names("expense") + ctx.category_names("income")},
                            "description": {"type": "string", "description": "Short label in the user's words, e.g. 'pho' or 'grab to work'."},
                            "date": {"type": "string", "description": "YYYY-MM-DD"},
                            "wallet": {"type": "string", "description": f"Only if the user named a wallet: one of {[w.name for w in ctx.wallets]}. Omit otherwise."},
                        },
                        "required": ["amount", "currency", "type", "category", "description", "date"],
                    },
                },
                "question": {"type": "string", "description": "One short question, only when a required detail truly cannot be inferred. Leave entries empty when asking."},
            },
            "required": ["entries"],
        },
    }


def build_system(ctx: ParseContext, examples: Optional[list[tuple[str, str]]] = None) -> str:
    wallets = ", ".join(f"{w.name} ({w.currency})" for w in ctx.wallets) or "none"
    cats = "\n".join(f"- {c.name}: {CATEGORY_HINTS.get(c.name, '')}".rstrip(": ") for c in ctx.categories)
    past = ""
    if examples:
        past = ("\n\nHow this user categorised similar entries before (follow this, it is their own system):\n"
                + "\n".join(f'- "{d}" -> {c}' for d, c in examples))
    return f"""You turn short personal-finance chat messages into structured entries.
The user lives in Singapore and also spends in Vietnam. They write in English, Vietnamese or a mix.

Today is {ctx.today.isoformat()} ({ctx.today.strftime('%A')}). Yesterday was {(ctx.today - timedelta(days=1)).isoformat()}.
Home currency: {ctx.home_currency}. Wallets: {wallets}.

Currency rules:
- A 'k' suffix means thousands of VND (65k = 65000 VND). 'tr', 'triệu' or 'm' means millions of VND (1tr2 = 1200000 VND).
- Dot-grouped thousands like 65.000 are VND.
- A bare number under 10000 with no currency is {ctx.home_currency}; 10000 or more is VND.
- An explicit currency or symbol always wins ($ = SGD, US$ = USD).
- If the user names a wallet, the entry uses that wallet's currency unless another currency is explicit.

Other rules:
- One message can hold several entries; return all of them.
- Salary, bonus, refunds, money received from family are income. Everything else is an expense.
- Pick the closest category; use 'Other' or 'Other income' only if nothing fits.
- Keep descriptions short and in the user's own words.
- Only ask a question if the amount itself is missing or unreadable, or you truly cannot tell whether money came in or went out.
- Never ask about the currency: the currency rules above always decide it. Never ask about category, wallet, or timing.
- A message that mentions a purchase and an amount is something already paid, unless it clearly says it is planned.
- Gifts, treats or payments for other people are expenses.
- If the message has no money movement at all, return no entries and a short friendly question.
Categories:
{cats}{past}

Always answer by calling the {TOOL_NAME} tool."""


def cost_usd(log: LLMCallLog, input_price: float, output_price: float) -> Optional[float]:
    if log.input_tokens is None or log.output_tokens is None:
        return None
    return (log.input_tokens * input_price + log.output_tokens * output_price) / 1_000_000


def parse_with_llm(
    text: str,
    ctx: ParseContext,
    client: Any,
    model: str,
    on_call: Optional[Callable[[LLMCallLog], None]] = None,
    examples: Optional[list[tuple[str, str]]] = None,
) -> ParseResult:
    """`client` is an anthropic.Anthropic instance (or a test double with .messages.create)."""
    started = time.monotonic()
    log = LLMCallLog(model=model, input_tokens=None, output_tokens=None, latency_ms=0,
                     ok=False, input_text=text, output_json=None)
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=1024,
            system=build_system(ctx, examples),
            tools=[build_tool(ctx)],
            tool_choice={"type": "tool", "name": TOOL_NAME},
            messages=[{"role": "user", "content": text}],
        )
        log.latency_ms = int((time.monotonic() - started) * 1000)
        usage = getattr(resp, "usage", None)
        log.input_tokens = getattr(usage, "input_tokens", None)
        log.output_tokens = getattr(usage, "output_tokens", None)

        block = next((b for b in resp.content if getattr(b, "type", None) == "tool_use"), None)
        if block is None:
            raise ValueError("model did not call the tool")
        data = block.input if isinstance(block.input, dict) else json.loads(block.input)
        log.output_json = data

        entries = [Entry.model_validate(e) for e in data.get("entries") or []]
        valid_categories = {c.name for c in ctx.categories}
        for e in entries:
            if e.category not in valid_categories:
                e.category = "Other income" if e.type == "income" else "Other"
            if e.wallet and not ctx.wallet_by_name(e.wallet):
                e.wallet = None
        question = (data.get("question") or "").strip() or None
        log.ok = True
        if entries:
            return ParseResult(entries=entries, parser="llm")
        return ParseResult(question=question or "I couldn't find an amount there. What did you spend or receive?", parser="llm")
    except Exception as exc:  # API errors, bad output: log it and ask the user instead of crashing
        log.error = f"{type(exc).__name__}: {exc}"[:500]
        log.latency_ms = log.latency_ms or int((time.monotonic() - started) * 1000)
        return ParseResult(question="Sorry, I couldn't read that one. Could you write it like \"pho 65k\" or \"grab 12.5\"?", parser="llm")
    finally:
        if on_call:
            on_call(log)
