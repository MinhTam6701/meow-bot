"""Transfers between wallets: "move 200 from DBS to Cash", "chuyển 2tr từ VP sang cash",
"withdraw 100 from DBS", "top up grabpay 50 from dbs", "move 500 from DBS to VP as 10tr",
"transfer vp to dbs 30tr160k to 1471.2" (sent, then received).

Rules first. When they can't read a transfer (odd wording, an amount they don't know), Claude reads it
with a forced tool call; code still checks the wallets and the exchange rate before anything is saved.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Optional, Union

from .models import ParseContext, WalletInfo
from .money import VND_BARE_THRESHOLD, AmountToken, parse_amount_token
from .parser_llm import LLMCallLog
from .parser_rules import normalize

VERBS = {"move", "transfer", "chuyen", "topup", "top", "withdraw", "rut", "nap", "send"}
FROM_WORDS = {"from", "tu", "out"}
TO_WORDS = {"to", "into", "sang", "vao", "qua", "toi"}
AS_WORDS = {"as", "=", "thanh", "nhan", "received", "got"}
WITHDRAW_VERBS = {"withdraw", "rut"}


@dataclass
class TransferRequest:
    amount: Decimal
    currency: Optional[str]      # explicit or VND hint; None = use the source wallet's currency
    vnd_hint: bool
    from_wallet: Optional[WalletInfo]
    to_wallet: Optional[WalletInfo]
    to_wallet_name: Optional[str]   # for "withdraw": a cash wallet to create if missing
    received: Optional[tuple[Decimal, Optional[str], bool]] = None  # "as 10tr"


class Unclear(str):
    """An error from the rules that Claude may be able to sort out ("how much?", an unreadable amount)."""


EXAMPLE = "move 500 from DBS to VP as 10tr"


def _likely_currency(tok: AmountToken) -> Optional[str]:
    if tok.currency:
        return tok.currency
    if tok.vnd_hint or tok.value >= VND_BARE_THRESHOLD:
        return "VND"
    return None


def looks_like_transfer(text: str, ctx: Optional[ParseContext] = None) -> bool:
    """A transfer verb plus a from/to word or a wallet name ("chuyển nhà" = moving house, not a transfer)."""
    words = [normalize(w) for w in text.split()]
    if not words or not (words[0] in VERBS or (words[0] == "top" and len(words) > 1 and words[1] == "up")):
        return False
    if words[0] in WITHDRAW_VERBS:
        return True
    rest = words[1:]
    names_wallet = ctx is not None and any(ctx.wallet_by_name(w) for w in rest)
    return names_wallet or any(w in FROM_WORDS | TO_WORDS for w in rest) or "cash" in rest


def parse_transfer(text: str, ctx: ParseContext) -> Union[TransferRequest, str, None]:
    """TransferRequest, an error message for the user, or None if it isn't a transfer."""
    if not looks_like_transfer(text, ctx):
        return None
    raw = text.replace("=", " = ").replace("→", " to ").replace("->", " to ").split()
    words = [normalize(w) for w in raw]
    verb = words[0]
    i = 2 if (verb == "top" and len(words) > 1 and words[1] == "up") else 1

    amount: Optional[AmountToken] = None
    received: Optional[AmountToken] = None
    said_received = False          # "as 10tr" / "to 1471.2": the user said which amount arrived
    from_w = to_w = None
    loose: list[WalletInfo] = []
    unknown: list[str] = []
    expect = None  # 'from' | 'to' | 'as'
    cash_name = None
    while i < len(raw):
        w, tok = words[i], parse_amount_token(raw[i])
        if w in FROM_WORDS:
            expect = "from"
        elif w in TO_WORDS:
            expect = "to"
        elif w in AS_WORDS:
            expect = "as"
        elif tok is not None:
            if amount is None and expect != "as":
                amount = tok
            elif received is None:
                received = tok
                said_received = expect in ("as", "to")
            else:
                return Unclear(f"I saw more than two amounts there. Try: <code>{EXAMPLE}</code>")
            expect = None
        elif ctx.wallet_by_name(w):
            wal = ctx.wallet_by_name(w)
            if expect == "from":
                from_w = wal
            elif expect == "to":
                to_w = wal
            else:
                loose.append(wal)
            expect = None
        elif w in ("cash", "tienmat"):
            if expect == "from":
                return "You don't have a Cash wallet yet. Add it with <code>/wallet add Cash SGD cash</code>."
            cash_name = "cash"  # created on first use
            expect = None
        elif w in {"sgd", "vnd", "usd", "d", "dong"} and amount is not None:
            pass  # currency word after the amount: handled by the amount itself
        elif w in {"money", "tien", "the", "my"} and expect is None:
            pass
        elif re.search(r"\d", raw[i]):
            return Unclear(f"I couldn't read the amount “{raw[i]}”. Try: <code>{EXAMPLE}</code>")
        else:
            if expect in ("from", "to"):
                unknown.append(raw[i])
            expect = None
        i += 1

    if to_w is None and verb in WITHDRAW_VERBS:
        cash_name = "cash"
    for wal in loose:
        if to_w is None and verb in ("top", "topup", "nap") and cash_name is None:
            to_w = wal
        elif from_w is None:
            from_w = wal
        elif to_w is None:
            to_w = wal

    if unknown:
        return (f"I don't have a wallet called \"{unknown[0]}\". "
                f"Add it with <code>/wallet add {unknown[0]} SGD</code>.")
    if amount is None:
        return Unclear("How much? Try: <code>move 200 from DBS to Cash</code>")
    if from_w is None:
        return Unclear("From which wallet? Try: <code>move 200 from DBS to Cash</code>")
    if to_w is None and cash_name is None:
        return Unclear("To which wallet? Try: <code>move 200 from DBS to Cash</code>")
    if to_w is not None and to_w.id == from_w.id:
        return "That's the same wallet twice."
    if received is not None:
        if to_w is None or to_w.currency == from_w.currency:
            return Unclear(f"I saw two amounts there. Try: <code>{EXAMPLE}</code>")
        # Two amounts across currencies: each goes with the wallet whose currency it looks like.
        a, r = _likely_currency(amount), _likely_currency(received)
        if not said_received and ((a == to_w.currency and r in (from_w.currency, None))
                                  or (r == from_w.currency and a in (to_w.currency, None))):
            amount, received = received, amount
        elif said_received and a == to_w.currency and r == from_w.currency:
            amount, received = received, amount
    rec = (received.value, received.currency, received.vnd_hint) if received else None
    return TransferRequest(amount.value, amount.currency, amount.vnd_hint, from_w, to_w,
                           None if to_w else cash_name, rec)


# --- Claude, for transfers the rules can't read ------------------------------------------

TOOL_NAME = "record_transfer"


def build_tool(ctx: ParseContext) -> dict:
    names = [w.name for w in ctx.wallets]
    return {
        "name": TOOL_NAME,
        "description": "Record one transfer between the user's own wallets, or ask one short question.",
        "input_schema": {
            "type": "object",
            "properties": {
                "from_wallet": {"type": "string", "enum": names},
                "to_wallet": {"type": "string", "enum": names},
                "amount_sent": {"type": "string", "description": "What left from_wallet, in from_wallet's currency, "
                                                                 "as a plain decimal string, e.g. '30160000' or '1471.20'."},
                "amount_received": {"type": "string", "description": "What arrived in to_wallet, in to_wallet's "
                                                                     "currency. Only if the user gave it; never convert."},
                "question": {"type": "string", "description": "One short question, only if the wallets or the amount "
                                                              "truly can't be worked out. Leave the other fields out then."},
            },
        },
    }


def build_system(ctx: ParseContext) -> str:
    wallets = "; ".join(f"{w.name} ({w.currency})" + (f", also called {', '.join(w.aliases)}" if w.aliases else "")
                        for w in ctx.wallets)
    return f"""You read a message about moving money between the user's own wallets.
Wallets: {wallets}.
Vietnamese shorthand: k = thousand, tr / triệu / củ / m = million. "30tr160k" = 30,160,000; "1tr2" = 1,200,000;
"2tr5" = 2,500,000; "500k" = 500,000. A dot groups thousands in VND ("30.160.000").
When there are two amounts, one is what was sent and one is what arrived: give each to the wallet whose currency
it fits (a VND amount is in the millions; an SGD amount is small). Never convert currencies yourself.
Copy digits exactly as the user wrote them. If a number has a typo you can't be sure about, ask.
Always answer by calling the {TOOL_NAME} tool."""


def _decimal(value) -> Optional[Decimal]:
    try:
        d = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None
    return d if d > 0 else None


def parse_transfer_llm(client: Any, model: str, text: str, ctx: ParseContext
                       ) -> tuple[Union[TransferRequest, str, None], LLMCallLog]:
    """A TransferRequest, a question for the user, or None if Claude couldn't help."""
    log = LLMCallLog(model=model, input_tokens=None, output_tokens=None, latency_ms=0, ok=False,
                     input_text=text, output_json=None)
    start = time.monotonic()
    try:
        resp = client.messages.create(model=model, max_tokens=300, system=build_system(ctx), tools=[build_tool(ctx)],
                                      tool_choice={"type": "tool", "name": TOOL_NAME},
                                      messages=[{"role": "user", "content": text}])
        usage = getattr(resp, "usage", None)
        log.input_tokens = getattr(usage, "input_tokens", None)
        log.output_tokens = getattr(usage, "output_tokens", None)
        block = next((b for b in resp.content if getattr(b, "type", "") == "tool_use"), None)
        if block is None:
            log.error = "no tool call"
            return None, log
        data = dict(block.input)
        log.output_json = data
        log.ok = True
        src, dst = ctx.wallet_by_name(data.get("from_wallet") or ""), ctx.wallet_by_name(data.get("to_wallet") or "")
        sent = _decimal(data.get("amount_sent"))
        got = _decimal(data.get("amount_received")) if data.get("amount_received") else None
        if not (src and dst and sent) or src.id == dst.id:
            q = (data.get("question") or "").strip()
            return (q or None), log
        if dst.currency == src.currency:
            got = None
        return TransferRequest(sent, src.currency, False, src, dst, None,
                               (got, dst.currency, False) if got else None), log
    except Exception as exc:
        log.error = f"{type(exc).__name__}: {exc}"[:500]
        return None, log
    finally:
        log.latency_ms = int((time.monotonic() - start) * 1000)
