"""Starter data created by /start."""

# (name, emoji, type) — keep in sync with supabase/migrations/0004
DEFAULT_CATEGORIES = [
    ("Food & Drinks", "🍜", "expense"),
    ("Groceries", "🛒", "expense"),
    ("Transport", "🚕", "expense"),
    ("Housing", "🏠", "expense"),
    ("Phone", "📱", "expense"),
    ("Shopping", "🛍", "expense"),
    ("Beauty", "💅", "expense"),
    ("Health", "💊", "expense"),
    ("Entertainment", "🎮", "expense"),
    ("Subscriptions & Fees", "🔁", "expense"),
    ("Travel", "✈️", "expense"),
    ("Education", "📚", "expense"),
    ("Gifts & Family", "🎁", "expense"),
    ("Other", "📦", "expense"),
    ("Salary", "💼", "income"),
    ("Family gift", "🧧", "income"),
    ("Refund", "💸", "income"),
    ("Investment", "📈", "income"),
    ("Other income", "💰", "income"),
]

# One line each, given to Claude so it knows what goes where.
CATEGORY_HINTS = {
    "Food & Drinks": "eating out, meals, coffee, bubble tea, drinks, snacks",
    "Groceries": "food bought to cook at home, supermarket, milk, eggs",
    "Transport": "taxi, Grab, bus, MRT, EZ-Link top-up, parking, flights within a trip go to Travel",
    "Housing": "rent, electricity, water, household items, furniture, moving",
    "Phone": "phone bill, mobile top-up, data plan",
    "Shopping": "clothes, toiletries, personal items, Shopee/Lazada orders, gadgets",
    "Beauty": "haircut, salon, skincare, cosmetics",
    "Health": "medicine, pharmacy, doctor, clinic",
    "Entertainment": "movies, games, karaoke, bars, outings",
    "Subscriptions & Fees": "streaming and app subscriptions, bank fees, card insurance, annual fees",
    "Travel": "trips, hotels, flights, travel SIMs",
    "Education": "tuition, courses, study tools, AI tools for study, printing documents",
    "Gifts & Family": "gifts, red packets, money given to family",
    "Other": "anything that fits nowhere else",
    "Salary": "pay from work",
    "Family gift": "money received from family",
    "Refund": "refunds, cashback, money paid back",
    "Investment": "interest, dividends, investment returns",
    "Other income": "any other money received",
}

# (name, type, currency, is_default for that currency)
DEFAULT_WALLETS = [
    ("DBS", "bank", "SGD", True),
    ("VP", "bank", "VND", True),
]
