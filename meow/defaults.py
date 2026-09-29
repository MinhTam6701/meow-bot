"""Starter data created by /start."""

# (name, emoji, type)
DEFAULT_CATEGORIES = [
    ("Food", "🍜", "expense"),
    ("Transport", "🚕", "expense"),
    ("Shopping", "🛍", "expense"),
    ("Bills", "🧾", "expense"),
    ("Fun", "🎉", "expense"),
    ("Health", "💊", "expense"),
    ("Other", "📦", "expense"),
    ("Salary", "💼", "income"),
    ("Other income", "💰", "income"),
]

# (name, type, currency, is_default for that currency)
DEFAULT_WALLETS = [
    ("DBS", "bank", "SGD", True),
    ("VP", "bank", "VND", True),
]
