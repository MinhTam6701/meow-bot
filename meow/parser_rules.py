"""Rule-based parser for the common, simple messages.

It handles "<words> <amount> [currency] [wallet] [yesterday]" segments, several per
message ("coffee 6, lunch 14"). If any part is unclear it returns None and the
whole message goes to the LLM instead, so rules never guess.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import timedelta
from typing import Optional

from .models import Entry, ParseContext
from .money import CURRENCY_ALIASES, parse_amount_token

# Keyword -> category. Matched on accent-free lower-case words.
KEYWORDS: dict[str, set[str]] = {
    "Food": {
        "pho", "bun", "com", "banh", "banhmi", "cafe", "coffee", "kopi", "teh", "tea",
        "milktea", "boba", "bubble", "lunch", "dinner", "breakfast", "brunch", "supper",
        "snack", "snacks", "food", "meal", "eat", "grocery", "groceries", "supermarket",
        "ntuc", "fairprice", "giant", "sheng", "siong", "bread", "hawker", "mcd",
        "mcdonalds", "kfc", "restaurant", "sushi", "ramen", "pizza", "bbq", "hotpot",
        "chicken", "rice", "noodles", "grabfood", "foodpanda", "deliveroo", "starbucks",
        "ansang", "antrua", "antoi", "trasua", "highlands", "phuclong", "dessert",
        "fruit", "juice", "cake",
    },
    "Transport": {
        "grab", "taxi", "gojek", "tada", "cdg", "comfortdelgro", "uber", "bus", "mrt",
        "train", "ezlink", "simplygo", "petrol", "fuel", "parking", "erp", "xang", "xe",
        "xanhsm", "car", "ride", "toll",
    },
    "Shopping": {
        "shopee", "lazada", "amazon", "taobao", "uniqlo", "zara", "clothes", "shoes",
        "shirt", "shopping", "mall", "ikea", "daiso", "muji", "tiki", "decathlon",
    },
    "Bills": {
        "rent", "phone", "mobile", "internet", "wifi", "electricity", "utilities", "bill",
        "bills", "insurance", "singtel", "starhub", "circles", "dien", "nuoc",
        "tiennha", "tiendien", "tiennuoc", "fee", "fees", "tuition",
    },
    "Fun": {
        "movie", "movies", "cinema", "netflix", "spotify", "youtube", "game", "games",
        "steam", "concert", "bar", "beer", "karaoke", "club", "nhau", "trip", "travel",
    },
    "Health": {
        "pharmacy", "guardian", "watsons", "doctor", "clinic", "medicine", "gym",
        "dental", "dentist", "thuoc", "hospital", "vitamins", "physio",
    },
    "Salary": {"salary", "luong", "payroll", "stipend", "allowance", "wage", "wages"},
    "Other income": {
        "bonus", "thuong", "refund", "cashback", "dividend", "interest", "freelance",
        "income", "received", "reimbursement", "reimburse",
    },
}
INCOME_CATEGORIES = {"Salary", "Other income"}

# Words that override a match: "grab food" is food, not transport.
FOOD_OVERRIDES = {"food", "grabfood", "foodpanda", "deliveroo"}

YESTERDAY = {"yesterday", "yday", "ytd", "hqua", "homqua"}
TODAY = {"today", "hnay", "homnay"}
CONNECTORS = {"to", "from", "via", "by", "on", "with", "using", "into", "vao", "bang", "tu", "qua"}

SPLIT_RE = re.compile(r",(?!\d{3}\b)|;|\n|\s+and\s+|\s+&\s+|\s+va\s+|\s+và\s+", re.IGNORECASE)


def normalize(word: str) -> str:
    word = word.lower().replace("đ", "d")
    word = unicodedata.normalize("NFKD", word)
    return "".join(ch for ch in word if not unicodedata.combining(ch)).strip(".!?:()\"'")


def _category_for(words: list[str]) -> Optional[str]:
    norm = [normalize(w) for w in words]
    # Join two-word names such as "xanh sm", "hom qua", "tra sua".
    joined = norm + [a + b for a, b in zip(norm, norm[1:])]
    if any(w in FOOD_OVERRIDES for w in joined):
        return "Food"
    hits = {cat for cat, keys in KEYWORDS.items() for w in joined if w in keys}
    if len(hits) == 1:
        return hits.pop()
    if hits == {"Salary", "Other income"}:
        return "Salary"
    return None  # none or conflicting -> let the LLM decide


def _parse_segment(segment: str, ctx: ParseContext) -> Optional[Entry]:
    tokens = segment.split()
    if not tokens:
        return None

    income_sign = False
    if tokens[0].startswith("+"):
        income_sign = True
        tokens[0] = tokens[0][1:]
        if not tokens[0]:
            tokens = tokens[1:]

    amount_idx = [i for i, t in enumerate(tokens) if parse_amount_token(t)]
    if len(amount_idx) != 1:
        return None
    idx = amount_idx[0]
    amount = parse_amount_token(tokens[idx])
    assert amount is not None

    rest = tokens[:idx] + tokens[idx + 1:]
    explicit_currency: Optional[str] = None
    wallet: Optional[str] = None
    day = ctx.today
    words: list[str] = []

    norm_rest = [normalize(t) for t in rest]
    i = 0
    while i < len(rest):
        raw, n = rest[i], norm_rest[i]
        pair = n + (norm_rest[i + 1] if i + 1 < len(rest) else "")
        if raw.lower() in CURRENCY_ALIASES and explicit_currency is None and abs(i - idx) <= 1:
            explicit_currency = CURRENCY_ALIASES[raw.lower()]
        elif n in YESTERDAY:
            day = ctx.today - timedelta(days=1)
        elif pair in YESTERDAY:
            day = ctx.today - timedelta(days=1)
            i += 1
        elif n in TODAY:
            pass
        elif pair in TODAY:
            i += 1
        elif ctx.wallet_by_name(n):
            wallet = ctx.wallet_by_name(n).name
        elif n in CONNECTORS and i + 1 < len(rest) and ctx.wallet_by_name(norm_rest[i + 1]):
            pass  # "to DBS": the wallet is picked up on the next word
        else:
            words.append(raw)
        i += 1

    if not words:
        return None
    if any(re.search(r"\d", w) for w in words):
        return None  # other numbers (dates, quantities) -> LLM

    category = _category_for(words)
    if category is None:
        if not income_sign:
            return None
        category = "Other income"
    type_ = "income" if (income_sign or category in INCOME_CATEGORIES) else "expense"
    if income_sign and category not in INCOME_CATEGORIES:
        category = "Other income"

    value, currency = amount.resolve(ctx.home_currency, explicit_currency)
    return Entry(
        amount=value,
        currency=currency,
        type=type_,
        category=category,
        description=" ".join(words)[:200],
        date=day,
        wallet=wallet,
    )


def parse_with_rules(text: str, ctx: ParseContext) -> Optional[list[Entry]]:
    segments = [s.strip() for s in SPLIT_RE.split(text) if s and s.strip()]
    if not segments or len(segments) > 20:
        return None
    entries = []
    for seg in segments:
        entry = _parse_segment(seg, ctx)
        if entry is None:
            return None
        entries.append(entry)
    return entries
