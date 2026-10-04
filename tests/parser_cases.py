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
    ("pho 65k", [("65000", "VND", "expense", "Food & Drinks", 0, None)], "rule"),
    ("grab 12.5 yesterday", [("12.5", "SGD", "expense", "Transport", 1, None)], "rule"),
    ("salary 4200 to DBS", [("4200", "SGD", "income", "Salary", 0, "DBS")], "rule"),
    ("coffee 6, lunch 14, taxi 9", [
        ("6", "SGD", "expense", "Food & Drinks", 0, None),
        ("14", "SGD", "expense", "Food & Drinks", 0, None),
        ("9", "SGD", "expense", "Transport", 0, None),
    ], "rule"),
    ("soya milk 2", [("2", "SGD", "expense", "Food & Drinks", 0, None)], "rule"),
    ("kopi 1.8", [("1.8", "SGD", "expense", "Food & Drinks", 0, None)], "rule"),
    ("bun bo 55.000", [("55000", "VND", "expense", "Food & Drinks", 0, None)], "rule"),
    ("grab 45k vp", [("45000", "VND", "expense", "Transport", 0, "VP")], "rule"),
    ("netflix 19.98", [("19.98", "SGD", "expense", "Subscriptions & Fees", 0, None)], "rule"),
    ("shopee 32.40", [("32.40", "SGD", "expense", "Shopping", 0, None)], "rule"),
    ("rent 1,200", [("1200", "SGD", "expense", "Housing", 0, None)], "rule"),
    ("tiền nhà 5tr", [("5000000", "VND", "expense", "Housing", 0, None)], "rule"),
    ("xăng 80k hôm qua", [("80000", "VND", "expense", "Transport", 1, None)], "rule"),
    ("trà sữa 45k", [("45000", "VND", "expense", "Food & Drinks", 0, None)], "rule"),
    ("mrt 2.10", [("2.10", "SGD", "expense", "Transport", 0, None)], "rule"),
    ("dinner $38", [("38", "SGD", "expense", "Food & Drinks", 0, None)], "rule"),
    ("guardian 15.90", [("15.90", "SGD", "expense", "Health", 0, None)], "rule"),
    ("+50 refund", [("50", "SGD", "income", "Refund", 0, None)], "rule"),
    ("lương 25tr vp", [("25000000", "VND", "income", "Salary", 0, "VP")], "rule"),
    ("breakfast 5 and coffee 3", [
        ("5", "SGD", "expense", "Food & Drinks", 0, None),
        ("3", "SGD", "expense", "Food & Drinks", 0, None),
    ], "rule"),
    ("grab food 18", [("18", "SGD", "expense", "Food & Drinks", 0, None)], "rule"),
    ("gym 120000 vnd", [("120000", "VND", "expense", "Health", 0, None)], "rule"),
    ("phone bill 1tr2", [("1200000", "VND", "expense", "Phone", 0, None)], "rule"),
    ("coffee 5 usd", [("5", "USD", "expense", "Food & Drinks", 0, None)], "rule"),
    ("ăn trưa với team 150k", [("150000", "VND", "expense", "Food & Drinks", 0, None)], "rule"),
    ("dentist visit 180 on DBS", [("180", "SGD", "expense", "Health", 0, "DBS")], "rule"),
    # --- Vietnamese everyday phrases ---------------------------------------------------
    ("ăn tối 6.7", [("6.7", "SGD", "expense", "Food & Drinks", 0, None)], "rule"),
    ("ăn sáng 4", [("4", "SGD", "expense", "Food & Drinks", 0, None)], "rule"),
    ("mua đồ nấu ăn 32.91", [("32.91", "SGD", "expense", "Groceries", 0, None)], "rule"),
    ("taxi đi làm 18", [("18", "SGD", "expense", "Transport", 0, None)], "rule"),
    ("nạp tiền thẻ ez link 20", [("20", "SGD", "expense", "Transport", 0, None)], "rule"),
    ("nước cam 3.5", [("3.5", "SGD", "expense", "Food & Drinks", 0, None)], "rule"),
    ("nước uống 1.2", [("1.2", "SGD", "expense", "Food & Drinks", 0, None)], "rule"),
    ("nước giặt 8.9", [("8.9", "SGD", "expense", "Shopping", 0, None)], "rule"),
    ("tiền điện thoại tháng 9.2026 7.9", [("7.9", "SGD", "expense", "Phone", 0, None)], "rule"),
    ("tiền điện tháng 8 45", [("45", "SGD", "expense", "Housing", 0, None)], "rule"),
    ("gia hạn iqiyi 2", [("2", "SGD", "expense", "Subscriptions & Fees", 0, None)], "rule"),
    ("phí quản lý tài khoản 22k", [("22000", "VND", "expense", "Subscriptions & Fees", 0, None)], "rule"),
    ("sữa rửa mặt 15", [("15", "SGD", "expense", "Beauty", 0, None)], "rule"),
    ("cắt tóc 12", [("12", "SGD", "expense", "Beauty", 0, None)], "rule"),
    ("đi xem phim 15", [("15", "SGD", "expense", "Entertainment", 0, None)], "rule"),
    ("mua thuốc 9.5", [("9.5", "SGD", "expense", "Health", 0, None)], "rule"),
    ("học phí 1500", [("1500", "SGD", "expense", "Education", 0, None)], "rule"),
    ("lì xì 500k", [("500000", "VND", "expense", "Gifts & Family", 0, None)], "rule"),
    ("tiền gửi xe 5k", [("5000", "VND", "expense", "Transport", 0, None)], "rule"),
    ("+6.58 hoàn tiền thẻ visa", [("6.58", "SGD", "income", "Refund", 0, None)], "rule"),
    ("lương 1350 dbs", [("1350", "SGD", "income", "Salary", 0, "DBS")], "rule"),
    # --- harder, expected to go to the LLM ------------------------------------------
    ("paid 23 for the haircut", [("23", "SGD", "expense", "Beauty", 0, None)], "rule"),
    ("bought 2 books for 30 total", [("30", "SGD", "expense", "Shopping", 0, None)], "llm"),
    ("mom sent me 2 triệu", [("2000000", "VND", "income", "Family gift", 0, None)], "llm"),
    ("uniqlo jacket 59.90 and socks 9.90", [
        ("59.90", "SGD", "expense", "Shopping", 0, None),
        ("9.90", "SGD", "expense", "Shopping", 0, None),
    ], "llm"),
    ("birthday gift for Linh 40", [("40", "SGD", "expense", "Gifts & Family", 0, None)], "rule"),
]
