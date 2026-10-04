"""Rule-based parser for the common, simple messages.

It handles "<words> <amount> [currency] [wallet] [yesterday]" segments, several per
message ("coffee 6, lunch 14"). If any part is unclear it returns None and the
whole message goes to the LLM instead, so rules never guess.

Category comes from, in order:
  1. phrases this user has taught the bot (history import + corrections), most specific first
  2. the keyword table below, longest match first ("tiền điện thoại" beats "tiền điện")
"""
from __future__ import annotations

import re
from datetime import timedelta
from typing import Optional

from .models import Entry, ParseContext
from .money import CURRENCY_ALIASES, parse_amount_token
from .text import DATE_LIKE, merchant_key, normalize, phrase_keys  # noqa: F401  (re-exported)

# Keyword -> category, on accent-free lower-case words. Two- and three-word entries are
# written joined ("antrua" = "ăn trưa") and beat single words.
INCOME_CATEGORIES = {"Salary", "Family gift", "Refund", "Investment", "Other income"}

KEYWORDS: dict[str, set[str]] = {
    "Food & Drinks": {
        "pho", "bun", "com", "banh", "banhmi", "cafe", "caphe", "coffee", "kopi", "teh", "tea",
        "milktea", "boba", "bubble", "bubbletea", "lunch", "dinner", "breakfast", "brunch", "supper",
        "snack", "snacks", "food", "meal", "eat", "hawker", "mcd", "mcdonalds", "kfc", "restaurant",
        "sushi", "ramen", "pizza", "bbq", "hotpot", "chicken", "rice", "noodles", "grabfood",
        "foodpanda", "deliveroo", "starbucks", "ansang", "antrua", "antoi", "anvat", "anbanh",
        "trasua", "highlands", "phuclong", "dessert", "juice", "cake", "milk", "sua", "soya", "soy",
        "suadaunanh", "yogurt", "sandwich", "bakery", "nuoc", "nuocuong", "nuoccam", "nuocep",
        "nuoctao", "nuocloc", "drink", "drinks", "kem", "che", "uongnuoc",
    },
    "Groceries": {
        "grocery", "groceries", "supermarket", "ntuc", "fairprice", "giant", "shengsiong",
        "coldstorage", "donki", "donauan", "nauan", "dicho", "sieuthi", "eggs", "trung", "rau",
        "thit", "gao", "muasua", "muatrung", "muarau", "muathit", "muagao", "muadoan", "muadonau",
    },
    "Transport": {
        "grab", "taxi", "gojek", "tada", "cdg", "comfortdelgro", "uber", "bus", "mrt", "train",
        "ezlink", "simplygo", "petrol", "fuel", "parking", "erp", "xang", "xanhsm", "toll",
        "guixe", "car", "ride", "xe",
    },
    "Housing": {
        "rent", "tiennha", "electricity", "tiendien", "tiennuoc", "utilities", "furniture",
        "giadung", "dogiadung", "maygiat", "hutbui", "mayhutbui", "nhamoi", "banghe",
    },
    "Phone": {
        "phone", "mobile", "singtel", "starhub", "circles", "simba", "giga", "dienthoai",
        "tiendienthoai", "napdienthoai", "internet", "wifi", "data",
    },
    "Shopping": {
        "shopee", "lazada", "amazon", "taobao", "shein", "uniqlo", "zara", "clothes", "shoes",
        "shirt", "shopping", "mall", "ikea", "daiso", "muji", "tiki", "decathlon", "quanao",
        "toiletries", "nuocgiat", "detergent", "tuixach",
    },
    "Beauty": {
        "haircut", "salon", "barber", "cattoc", "goidau", "lamdep", "nails", "spa", "suaruamat",
        "goidaumassage", "lammong",
    },
    "Health": {
        "pharmacy", "guardian", "watsons", "doctor", "clinic", "medicine", "thuoc", "muathuoc",
        "hospital", "vitamins", "physio", "dental", "dentist", "nhakhoa", "khambenh", "gym",
    },
    "Entertainment": {
        "movie", "movies", "cinema", "phim", "xemphim", "game", "games", "napgame", "steam",
        "concert", "bar", "beer", "bia", "uongbia", "karaoke", "club", "nhau", "billiard", "bida",
    },
    "Subscriptions & Fees": {
        "netflix", "spotify", "youtube", "iqiyi", "vieon", "disney", "icloud", "subscription",
        "giahan", "fee", "fees", "phi", "phiquanly", "baohiem", "baohiemtindung", "insurance",
        "thuongnien", "annualfee",
    },
    "Travel": {
        "travel", "trip", "hotel", "khachsan", "flight", "vemaybay", "dulich", "airbnb", "agoda",
    },
    "Education": {
        "tuition", "hocphi", "course", "udemy", "coursera", "claude", "chatgpt", "openai",
    },
    "Gifts & Family": {"gift", "gifts", "muaqua", "quatang", "lixi", "present"},
    "Salary": {"salary", "luong", "payroll", "stipend", "allowance", "wage", "wages", "bonus", "thuong"},
    "Family gift": {"chotien"},
    "Refund": {"refund", "hoantien", "hoangtien", "cashback", "reimbursement", "reimburse"},
    "Investment": {"dividend", "interest", "laisuat", "dautu"},
    "Other income": {"freelance", "income", "received"},
}

YESTERDAY = {"yesterday", "yday", "ytd", "hqua", "homqua"}
TODAY = {"today", "hnay", "homnay"}
CONNECTORS = {"to", "from", "via", "by", "on", "with", "using", "into", "vao", "bang", "tu", "qua"}
MONTH_WORDS = {"thang", "month", "t"}

SPLIT_RE = re.compile(r",(?!\d{3}\b)|;|\n|\s+and\s+|\s+&\s+|\s+va\s+|\s+và\s+", re.IGNORECASE)


def _category_for(words: list[str], income_only: bool = False) -> Optional[str]:
    norm = [normalize(w) for w in words]
    table = {c: k for c, k in KEYWORDS.items() if c in INCOME_CATEGORIES} if income_only else KEYWORDS
    for n in (3, 2, 1):  # longest phrase wins
        grams = {"".join(norm[i:i + n]) for i in range(len(norm) - n + 1)}
        hits = {cat for cat, keys in table.items() if grams & keys}
        if len(hits) == 1:
            return hits.pop()
        if len(hits) > 1:
            return None  # conflicting -> let the LLM decide
    return None


def learned_category(description: str, ctx: ParseContext) -> Optional[str]:
    for key in phrase_keys(description):
        if key in ctx.merchant_rules:
            return ctx.merchant_rules[key]
    return None


def _is_amount(tokens: list[str], i: int) -> bool:
    if parse_amount_token(tokens[i]) is None:
        return False
    # "tháng 9" / "month 10": a month, not an amount
    if i > 0 and normalize(tokens[i - 1]) in MONTH_WORDS and re.fullmatch(r"\d{1,2}([./]\d{2,4})?", tokens[i]):
        return False
    return True


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

    amount_idx = [i for i in range(len(tokens)) if _is_amount(tokens, i)]
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
        pair_wallet = ctx.wallet_by_name(pair) if i + 1 < len(rest) else None
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
        elif pair_wallet:  # two-word wallet name or alias: "tiền mặt"
            wallet = pair_wallet.name
            i += 1
        elif ctx.wallet_by_name(n):
            wallet = ctx.wallet_by_name(n).name
        elif n in CONNECTORS and i + 1 < len(rest) and (
                ctx.wallet_by_name(norm_rest[i + 1])
                or (i + 2 < len(rest) and ctx.wallet_by_name(norm_rest[i + 1] + norm_rest[i + 2]))):
            pass  # "to DBS": the wallet is picked up on the next word
        else:
            words.append(raw)
        i += 1

    if not words:
        return None
    if any(re.search(r"\d", w) and not DATE_LIKE.match(w) and not _after_month(words, j)
           for j, w in enumerate(words)):
        return None  # other numbers (quantities, addresses) -> LLM

    description = " ".join(words)[:200]
    value, currency = amount.resolve(ctx.home_currency, explicit_currency)
    learned = learned_category(description, ctx)
    learned_cat = ctx.category(learned) if learned else None
    if learned_cat and (not income_sign or learned_cat.type == "income"):
        return Entry(amount=value, currency=currency, type=learned_cat.type, category=learned,
                     description=description, date=day, wallet=wallet)

    category = _category_for(words, income_only=income_sign)
    if category is None:
        if not income_sign:
            return None
        category = "Other income"
    if income_sign and category not in INCOME_CATEGORIES:
        category = "Other income"
    type_ = "income" if category in INCOME_CATEGORIES else "expense"
    return Entry(amount=value, currency=currency, type=type_, category=category,
                 description=description, date=day, wallet=wallet)


def _after_month(words: list[str], j: int) -> bool:
    return j > 0 and normalize(words[j - 1]) in MONTH_WORDS and bool(re.fullmatch(r"\d{1,2}([./]\d{2,4})?", words[j]))


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
