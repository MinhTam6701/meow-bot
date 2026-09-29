"""Sample messages with the expected result.

Used by the unit tests (the `rule` cases) and by scripts/eval_parser.py, which
runs every case through the full parser, including Claude, to measure accuracy
and cost.

Each expected entry is (amount, currency, type, category, days_ago, wallet).
`via` says which parser should handle it: 'rule' or 'llm'.
"""
from datetime import date

from meow.defaults import DEFAULT_CATEGORIES, DEFAULT_WALLETS
from meow.models import CategoryInfo, ParseContext, WalletInfo

TODAY = date(2026, 9, 29)


def make_context(today: date = TODAY) -> ParseContext:
    return ParseContext(
        today=today,
        home_currency="SGD",
        wallets=[WalletInfo(i + 1, n, c, d) for i, (n, _t, c, d) in enumerate(DEFAULT_WALLETS)],
        categories=[CategoryInfo(i + 1, n, e, t) for i, (n, e, t) in enumerate(DEFAULT_CATEGORIES)],
    )


CASES = [
    # --- simple, handled by rules -------------------------------------------------
    ("pho 65k", [("65000", "VND", "expense", "Food", 0, None)], "rule"),
    ("grab 12.5 yesterday", [("12.5", "SGD", "expense", "Transport", 1, None)], "rule"),
    ("salary 4200 to DBS", [("4200", "SGD", "income", "Salary", 0, "DBS")], "rule"),
    ("coffee 6, lunch 14, taxi 9", [
        ("6", "SGD", "expense", "Food", 0, None),
        ("14", "SGD", "expense", "Food", 0, None),
        ("9", "SGD", "expense", "Transport", 0, None),
    ], "rule"),
    ("kopi 1.8", [("1.8", "SGD", "expense", "Food", 0, None)], "rule"),
    ("bun bo 55.000", [("55000", "VND", "expense", "Food", 0, None)], "rule"),
    ("grab 45k vp", [("45000", "VND", "expense", "Transport", 0, "VP")], "rule"),
    ("netflix 19.98", [("19.98", "SGD", "expense", "Fun", 0, None)], "rule"),
    ("shopee 32.40", [("32.40", "SGD", "expense", "Shopping", 0, None)], "rule"),
    ("rent 1,200", [("1200", "SGD", "expense", "Bills", 0, None)], "rule"),
    ("tiền nhà 5tr", [("5000000", "VND", "expense", "Bills", 0, None)], "rule"),
    ("xăng 80k hôm qua", [("80000", "VND", "expense", "Transport", 1, None)], "rule"),
    ("trà sữa 45k", [("45000", "VND", "expense", "Food", 0, None)], "rule"),
    ("mrt 2.10", [("2.10", "SGD", "expense", "Transport", 0, None)], "rule"),
    ("dinner $38", [("38", "SGD", "expense", "Food", 0, None)], "rule"),
    ("guardian 15.90", [("15.90", "SGD", "expense", "Health", 0, None)], "rule"),
    ("+50 refund", [("50", "SGD", "income", "Other income", 0, None)], "rule"),
    ("lương 25tr vp", [("25000000", "VND", "income", "Salary", 0, "VP")], "rule"),
    ("breakfast 5 and coffee 3", [
        ("5", "SGD", "expense", "Food", 0, None),
        ("3", "SGD", "expense", "Food", 0, None),
    ], "rule"),
    ("grab food 18", [("18", "SGD", "expense", "Food", 0, None)], "rule"),
    ("gym 120000 vnd", [("120000", "VND", "expense", "Health", 0, None)], "rule"),
    ("phone bill 1tr2", [("1200000", "VND", "expense", "Bills", 0, None)], "rule"),
    ("coffee 5 usd", [("5", "USD", "expense", "Food", 0, None)], "rule"),
    ("ăn trưa với team 150k", [("150000", "VND", "expense", "Food", 0, None)], "rule"),
    ("dentist visit 180 on DBS", [("180", "SGD", "expense", "Health", 0, "DBS")], "rule"),
    # --- harder, expected to go to the LLM ------------------------------------------
    ("paid 23 for the haircut", [("23", "SGD", "expense", "Other", 0, None)], "llm"),
    ("bought 2 books for 30 total", [("30", "SGD", "expense", "Shopping", 0, None)], "llm"),
    ("mom sent me 2 triệu", [("2000000", "VND", "income", "Other income", 0, None)], "llm"),
    ("uniqlo jacket 59.90 and socks 9.90", [
        ("59.90", "SGD", "expense", "Shopping", 0, None),
        ("9.90", "SGD", "expense", "Shopping", 0, None),
    ], "llm"),
    ("birthday gift for Linh 40", [("40", "SGD", "expense", "Other", 0, None)], "llm"),
]
