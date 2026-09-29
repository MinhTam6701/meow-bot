"""Money helpers: minor units, amount tokens and display formatting."""
from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

# Currencies without a minor unit.
ZERO_DECIMAL = {"VND", "JPY", "KRW", "IDR"}

SYMBOLS = {"SGD": "S$", "USD": "US$", "EUR": "€", "GBP": "£", "AUD": "A$"}
SUFFIX_SYMBOLS = {"VND": "₫"}

# Words or symbols that name a currency, lower-case.
CURRENCY_ALIASES = {
    "sgd": "SGD", "s$": "SGD", "$": "SGD",
    "vnd": "VND", "đ": "VND", "₫": "VND", "dong": "VND", "đồng": "VND", "vnđ": "VND",
    "usd": "USD", "us$": "USD",
    "eur": "EUR", "€": "EUR",
    "gbp": "GBP", "£": "GBP",
    "myr": "MYR", "rm": "MYR",
    "thb": "THB", "baht": "THB",
    "jpy": "JPY", "yen": "JPY", "¥": "JPY",
    "krw": "KRW", "won": "KRW",
    "idr": "IDR", "aud": "AUD",
}

# Numbers at or above this, with no currency or suffix, are read as VND.
VND_BARE_THRESHOLD = Decimal(10_000)


def minor_digits(currency: str) -> int:
    return 0 if currency in ZERO_DECIMAL else 2


def to_minor(amount: Decimal, currency: str) -> int:
    digits = minor_digits(currency)
    q = Decimal(1).scaleb(-digits)
    return int((amount.quantize(q, rounding=ROUND_HALF_UP) * (10**digits)).to_integral_value())


def from_minor(minor: int, currency: str) -> Decimal:
    return Decimal(minor).scaleb(-minor_digits(currency))


def fmt(minor: int, currency: str) -> str:
    """S$1,234.50 · 65,000₫ · 12.00 MYR"""
    value = from_minor(abs(minor), currency)
    digits = minor_digits(currency)
    body = f"{value:,.{digits}f}"
    sign = "-" if minor < 0 else ""
    if currency in SYMBOLS:
        return f"{sign}{SYMBOLS[currency]}{body}"
    if currency in SUFFIX_SYMBOLS:
        return f"{sign}{body}{SUFFIX_SYMBOLS[currency]}"
    return f"{sign}{body} {currency}"


# ---------------------------------------------------------------------------
# Amount tokens such as 65k, 12.5, 65.000, 1,200,000, 1tr2, $5, 50000đ, 6sgd
# ---------------------------------------------------------------------------
_PREFIX = r"(?P<pre>us\$|s\$|\$|€|£|¥)?"
_NUM = r"(?P<num>\d{1,3}(?:\.\d{3})+|\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+(?:[.,]\d{1,2})?)"
_MULT = r"(?P<mult>k|m|tr|triệu|trieu)?(?P<frac>\d{1,3})?"
_CUR = r"(?P<cur>sgd|vnd|vnđ|usd|eur|gbp|myr|thb|jpy|krw|idr|aud|đ|₫)?"
AMOUNT_RE = re.compile(rf"^{_PREFIX}{_NUM}{_MULT}{_CUR}$", re.IGNORECASE)


class AmountToken:
    """An amount read from one token, before the currency is settled."""

    def __init__(self, value: Decimal, currency: Optional[str], vnd_hint: bool):
        self.value = value
        self.currency = currency  # explicit currency in the token, if any
        self.vnd_hint = vnd_hint  # k / m / tr suffix, or dot-grouped thousands

    def resolve(self, home: str, explicit: Optional[str] = None) -> tuple[Decimal, str]:
        cur = explicit or self.currency
        if cur:
            return self.value, cur
        if self.vnd_hint or self.value >= VND_BARE_THRESHOLD:
            return self.value, "VND"
        return self.value, home


def parse_amount_token(token: str) -> Optional[AmountToken]:
    m = AMOUNT_RE.match(token.strip())
    if not m:
        return None
    num, mult, frac = m["num"], (m["mult"] or "").lower(), m["frac"]
    if frac and not mult:
        return None  # digits glued after digits, not a number we understand
    vnd_hint = False
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+", num):  # 65.000 -> Vietnamese thousands
        value = Decimal(num.replace(".", ""))
        vnd_hint = True
    elif re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?", num):  # 1,200.50 -> thousands separator
        value = Decimal(num.replace(",", ""))
    else:
        value = Decimal(num.replace(",", "."))

    if mult == "k":
        value *= 1000
        vnd_hint = True
    elif mult in ("m", "tr", "triệu", "trieu"):
        # 1tr2 = 1.2 million
        if frac:
            value = value + Decimal(f"0.{frac}")
        value *= 1_000_000
        vnd_hint = True

    currency = None
    if m["pre"]:
        currency = CURRENCY_ALIASES.get(m["pre"].lower())
    if m["cur"]:
        currency = CURRENCY_ALIASES.get(m["cur"].lower())
    if value <= 0:
        return None
    return AmountToken(value, currency, vnd_hint)
