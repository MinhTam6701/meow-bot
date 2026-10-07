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
_MULT = r"(?P<mult>k|m|tr|triệu|trieu)?(?P<frac>\d{1,3})?(?P<k2>k)?"  # 30tr160k = 30,160,000
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
    if m["k2"] and (mult == "k" or not frac):
        return None  # "5k3k" is not an amount
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
        # 1tr2 = 1.2 million; 30tr160k = 30 million + 160 thousand
        if frac and m["k2"]:
            value = value * 1_000_000 + Decimal(frac) * 1000
        else:
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


# --- spoken and written-out amounts ----------------------------------------------------
# Voice notes (and some typing) say "50 nghìn", "1 triệu 2", "12 dollars". Rewrite them into the
# compact forms the parser already knows ("50k", "1200k", "12 sgd") before parsing.

_THOUSAND = r"(?:nghìn|nghin|ngàn|ngan)"
_MILLION_RE = re.compile(
    r"(\d+)(?:[.,](\d{1,3}))?\s*(?:triệu|trieu|củ)(?!\w)"
    r"(?:\s*(\d{1,3})\s*(?:" + _THOUSAND + r"|k)(?!\w)"         # 1 triệu 200 nghìn
    r"|\s+(rưỡi|ruoi)(?!\w)"                                     # 1 triệu rưỡi
    r"|\s+(\d{1,3})(?![\d.,]|\s*(?:" + _THOUSAND + r"|k|triệu|trieu)(?!\w)))?",  # 1 triệu 2
    re.IGNORECASE)


def _million(m: re.Match) -> str:
    thousands = int(m[1]) * 1000
    if m[2]:                      # 1,2 triệu / 1.25 triệu
        thousands += int(m[2].ljust(3, "0"))
    elif m[3]:                    # 1 triệu 200 nghìn
        thousands += int(m[3])
    elif m[4]:                    # rưỡi = half
        thousands += 500
    elif m[5]:                    # 1 triệu 2 = 1.2m, 2 triệu 25 = 2.25m, 2 triệu 250 = 2.25m
        thousands += int(m[5]) * (100, 10, 1)[len(m[5]) - 1]
    return f"{thousands}k"


_SPOKEN = [
    (re.compile(r"(\d+(?:[.,]\d+)?)\s*" + _THOUSAND + r"(?!\w)", re.I), r"\1k"),
    # "50k đồng": the k already says VND; a trailing currency word would only confuse the parser
    (re.compile(r"(\d+(?:[.,]\d+)?k)\s*(?:đồng|dong|vnđ|vnd|đ)(?!\w)", re.I), r"\1"),
    (re.compile(r"(\d+)\s*dollars?\s*(?:and\s*)?(\d{1,2})\s*cents?(?!\w)", re.I), r"\1.\2 sgd"),
    (re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:us|u\.s\.|american)\s*dollars?(?!\w)", re.I), r"\1 usd"),
    (re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:đô mỹ|do my)(?!\w)", re.I), r"\1 usd"),
    (re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:singapore dollars?|dollars?|bucks|đô la|đô|do la)(?!\w)", re.I), r"\1 sgd"),
    (re.compile(r"(\d+)\s*cents?(?!\w)", re.I), lambda m: f"{int(m[1]) / 100:.2f} sgd"),
    (re.compile(r"(?<!\w)(?:trả|tra)\s+(bằng|bang|qua)(?!\w)", re.I), r"\1"),  # "trả bằng VCB" = "bằng VCB"
]


def normalize_spoken(text: str) -> str:
    out = _MILLION_RE.sub(_million, text)
    for pattern, repl in _SPOKEN:
        out = pattern.sub(repl, out)
    return re.sub(r"[.!?…]+\s*$", "", out.strip())  # dictation adds a full stop at the end
