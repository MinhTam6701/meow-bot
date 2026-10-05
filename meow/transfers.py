"""Transfers between wallets: "move 200 from DBS to Cash", "chuyển 2tr từ VP sang cash",
"withdraw 100 from DBS", "top up grabpay 50 from dbs", "move 500 from DBS to VP as 10tr".
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, Union

from .models import ParseContext, WalletInfo
from .money import parse_amount_token
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

    amount = received = None
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
            if expect == "as":
                received = tok
            elif amount is None:
                amount = tok
            else:
                return "I saw two amounts there. Try: <code>move 500 from DBS to VP as 10tr</code>"
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
        return "How much? Try: <code>move 200 from DBS to Cash</code>"
    if from_w is None:
        return "From which wallet? Try: <code>move 200 from DBS to Cash</code>"
    if to_w is None and cash_name is None:
        return "To which wallet? Try: <code>move 200 from DBS to Cash</code>"
    if to_w is not None and to_w.id == from_w.id:
        return "That's the same wallet twice."
    rec = (received.value, received.currency, received.vnd_hint) if received else None
    return TransferRequest(amount.value, amount.currency, amount.vnd_hint, from_w, to_w,
                           None if to_w else cash_name, rec)
