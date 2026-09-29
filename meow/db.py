"""Database access. Plain SQL through psycopg 3.

Use the Supabase *transaction pooler* URL (port 6543) in DATABASE_URL: each Vercel
invocation opens a short connection, and the pooler keeps that cheap. Prepared
statements are turned off because the transaction pooler doesn't support them.
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import date
from typing import Iterator, Optional

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .defaults import DEFAULT_CATEGORIES, DEFAULT_WALLETS
from .models import CategoryInfo, WalletInfo


@contextmanager
def connect(database_url: str) -> Iterator[psycopg.Connection]:
    conn = psycopg.connect(database_url, prepare_threshold=None, row_factory=dict_row, connect_timeout=10)
    try:
        yield conn
    finally:
        conn.close()


# --- updates ----------------------------------------------------------------------

def claim_update(conn, update_id: int) -> bool:
    """True the first time an update_id is seen (inside the current transaction)."""
    row = conn.execute(
        "insert into processed_updates (update_id) values (%s) on conflict do nothing returning update_id",
        (update_id,),
    ).fetchone()
    return row is not None


# --- users ------------------------------------------------------------------------

def get_user(conn, telegram_id: int) -> Optional[dict]:
    return conn.execute("select * from users where telegram_id = %s", (telegram_id,)).fetchone()


def ensure_user(conn, telegram_id: int, first_name: str, home_currency: str, timezone: str) -> tuple[dict, bool]:
    """Returns (user, created). New users get the default wallets and categories."""
    user = get_user(conn, telegram_id)
    if user:
        return user, False
    user = conn.execute(
        """insert into users (telegram_id, first_name, home_currency, timezone)
           values (%s, %s, %s, %s) returning *""",
        (telegram_id, first_name, home_currency, timezone),
    ).fetchone()
    for i, (name, emoji, type_) in enumerate(DEFAULT_CATEGORIES):
        conn.execute(
            "insert into categories (user_id, name, emoji, type, sort_order) values (%s, %s, %s, %s, %s)",
            (telegram_id, name, emoji, type_, i),
        )
    for name, type_, currency, is_default in DEFAULT_WALLETS:
        conn.execute(
            "insert into wallets (user_id, name, type, currency, is_default) values (%s, %s, %s, %s, %s)",
            (telegram_id, name, type_, currency, is_default),
        )
    return user, True


def set_pending(conn, telegram_id: int, text: Optional[str]) -> None:
    conn.execute("update users set pending_input = %s where telegram_id = %s", (text, telegram_id))


# --- wallets & categories -------------------------------------------------------

def wallets(conn, user_id: int) -> list[WalletInfo]:
    rows = conn.execute(
        "select id, name, currency, is_default from wallets where user_id = %s and not archived order by id",
        (user_id,),
    ).fetchall()
    return [WalletInfo(r["id"], r["name"], r["currency"], r["is_default"]) for r in rows]


def categories(conn, user_id: int) -> list[CategoryInfo]:
    rows = conn.execute(
        "select id, name, emoji, type from categories where user_id = %s order by sort_order, id",
        (user_id,),
    ).fetchall()
    return [CategoryInfo(r["id"], r["name"], r["emoji"], r["type"]) for r in rows]


def create_cash_wallet(conn, user_id: int, currency: str) -> WalletInfo:
    """Used when an entry arrives in a currency the user has no wallet for."""
    has_default = conn.execute(
        "select 1 from wallets where user_id = %s and currency = %s and is_default", (user_id, currency)
    ).fetchone()
    r = conn.execute(
        """insert into wallets (user_id, name, type, currency, is_default)
           values (%s, %s, 'cash', %s, %s)
           on conflict (user_id, name) do update set archived = false
           returning id, name, currency, is_default""",
        (user_id, f"Cash {currency}", currency, not has_default),
    ).fetchone()
    return WalletInfo(r["id"], r["name"], r["currency"], r["is_default"])


# --- transactions -----------------------------------------------------------------

def insert_transactions(conn, user_id: int, rows: list[dict], raw_message: str, parser: str,
                        source: str = "text") -> uuid.UUID:
    """rows: wallet_id, category_id, type, amount_minor (signed), currency, description, occurred_on."""
    batch_id = uuid.uuid4()
    for r in rows:
        conn.execute(
            """insert into transactions
               (user_id, batch_id, wallet_id, category_id, type, amount_minor, currency,
                description, occurred_on, source, parser, raw_message)
               values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (user_id, batch_id, r["wallet_id"], r["category_id"], r["type"], r["amount_minor"],
             r["currency"], r["description"], r["occurred_on"], source, parser, raw_message),
        )
    return batch_id


def set_card(conn, batch_id: uuid.UUID, chat_id: int, message_id: int) -> None:
    conn.execute(
        "update transactions set card_chat_id = %s, card_message_id = %s where batch_id = %s",
        (chat_id, message_id, batch_id),
    )


_BATCH_SQL = """
select t.id, t.user_id, t.batch_id, t.type, t.amount_minor, t.currency, t.description,
       t.occurred_on, t.wallet_id, t.category_id, t.card_chat_id, t.card_message_id,
       w.name as wallet_name, c.name as category_name, c.emoji as category_emoji,
       exists (select 1 from transactions r where r.reverses_id = t.id) as reversed
from transactions t
join wallets w on w.id = t.wallet_id
left join categories c on c.id = t.category_id
where t.batch_id = %s and t.reverses_id is null
order by t.id
"""


def get_batch(conn, batch_id) -> list[dict]:
    return conn.execute(_BATCH_SQL, (batch_id,)).fetchall()


def get_transaction(conn, tx_id: int) -> Optional[dict]:
    return conn.execute(
        """select t.*, exists (select 1 from transactions r where r.reverses_id = t.id) as reversed
           from transactions t where t.id = %s and t.reverses_id is null""",
        (tx_id,),
    ).fetchone()


def undo_batch(conn, user_id: int, batch_id) -> int:
    """Append a reversal for every live entry in the batch. Returns how many."""
    rows = conn.execute(
        """insert into transactions
           (user_id, batch_id, wallet_id, category_id, type, amount_minor, currency,
            description, occurred_on, source, parser, reverses_id)
           select user_id, batch_id, wallet_id, category_id, type, -amount_minor, currency,
                  description, occurred_on, 'undo', 'command', id
           from live_transactions
           where batch_id = %s and user_id = %s
           returning id""",
        (batch_id, user_id),
    ).fetchall()
    return len(rows)


def last_live_batch(conn, user_id: int):
    row = conn.execute(
        "select batch_id from live_transactions where user_id = %s order by id desc limit 1", (user_id,)
    ).fetchone()
    return row["batch_id"] if row else None


def set_category(conn, user_id: int, tx_id: int, category_id: int) -> bool:
    """Category and wallet are labels, so they can be corrected in place. Amounts never are."""
    row = conn.execute(
        """update transactions t set category_id = c.id
           from categories c
           where t.id = %s and t.user_id = %s and c.id = %s and c.user_id = %s
             and c.type = t.type and t.reverses_id is null
             and not exists (select 1 from transactions r where r.reverses_id = t.id)
           returning t.id""",
        (tx_id, user_id, category_id, user_id),
    ).fetchone()
    return row is not None


def set_wallet(conn, user_id: int, tx_id: int, wallet_id: int) -> bool:
    row = conn.execute(
        """update transactions t set wallet_id = w.id
           from wallets w
           where t.id = %s and t.user_id = %s and w.id = %s and w.user_id = %s
             and w.currency = t.currency and t.reverses_id is null
             and not exists (select 1 from transactions r where r.reverses_id = t.id)
           returning t.id""",
        (tx_id, user_id, wallet_id, user_id),
    ).fetchone()
    return row is not None


# --- reports ----------------------------------------------------------------------

def entries_on(conn, user_id: int, day: date) -> list[dict]:
    return conn.execute(
        """select t.id, t.type, t.amount_minor, t.currency, t.description,
                  w.name as wallet_name, c.name as category_name, c.emoji as category_emoji
           from live_transactions t
           join wallets w on w.id = t.wallet_id
           left join categories c on c.id = t.category_id
           where t.user_id = %s and t.occurred_on = %s and t.type <> 'adjustment'
           order by t.id""",
        (user_id, day),
    ).fetchall()


def spent_on(conn, user_id: int, day: date) -> dict[str, int]:
    """Money spent on a day, per currency, as positive minor units."""
    rows = conn.execute(
        """select currency, -sum(amount_minor)::bigint as spent
           from transactions
           where user_id = %s and occurred_on = %s and type = 'expense'
           group by currency having sum(amount_minor) <> 0 order by currency""",
        (user_id, day),
    ).fetchall()
    return {r["currency"]: r["spent"] for r in rows}


def month_summary(conn, user_id: int, first_day: date, next_first_day: date) -> list[dict]:
    """Totals per currency, type and category. Reversals cancel out in the sums."""
    return conn.execute(
        """select t.currency, t.type, coalesce(c.name, 'Uncategorised') as category,
                  coalesce(c.emoji, '') as emoji, sum(t.amount_minor)::bigint as total
           from transactions t
           left join categories c on c.id = t.category_id
           where t.user_id = %s and t.occurred_on >= %s and t.occurred_on < %s
             and t.type in ('expense', 'income')
           group by 1, 2, 3, 4
           having sum(t.amount_minor) <> 0
           order by t.currency, t.type, abs(sum(t.amount_minor)) desc""",
        (user_id, first_day, next_first_day),
    ).fetchall()


def balances(conn, user_id: int) -> list[dict]:
    return conn.execute(
        "select wallet_id, name, currency, type, balance_minor from wallet_balances where user_id = %s order by wallet_id",
        (user_id,),
    ).fetchall()


def wallet_balance(conn, wallet_id: int) -> int:
    row = conn.execute(
        "select coalesce(sum(amount_minor), 0)::bigint as b from transactions where wallet_id = %s", (wallet_id,)
    ).fetchone()
    return row["b"]


# --- LLM usage --------------------------------------------------------------------

def log_llm_call(conn, user_id: Optional[int], purpose: str, log, cost: Optional[float]) -> None:
    conn.execute(
        """insert into llm_calls (user_id, purpose, model, input_tokens, output_tokens, cost_usd,
                                  latency_ms, ok, input_text, output_json, error)
           values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (user_id, purpose, log.model, log.input_tokens, log.output_tokens, cost, log.latency_ms,
         log.ok, log.input_text, Jsonb(log.output_json) if log.output_json is not None else None, log.error),
    )


def llm_calls_since(conn, user_id: int, since) -> int:
    row = conn.execute(
        "select count(*) as n from llm_calls where user_id = %s and created_at >= %s", (user_id, since)
    ).fetchone()
    return row["n"]
