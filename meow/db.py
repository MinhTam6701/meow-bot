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


def create_cash_wallet(conn, user_id: int, currency: str, name: Optional[str] = None) -> WalletInfo:
    """Used when an entry arrives in a currency the user has no wallet for."""
    has_default = conn.execute(
        "select 1 from wallets where user_id = %s and currency = %s and is_default", (user_id, currency)
    ).fetchone()
    r = conn.execute(
        """insert into wallets (user_id, name, type, currency, is_default)
           values (%s, %s, 'cash', %s, %s)
           on conflict (user_id, name) do update set archived = false
           returning id, name, currency, is_default""",
        (user_id, name or f"Cash {currency}", currency, not has_default),
    ).fetchone()
    return WalletInfo(r["id"], r["name"], r["currency"], r["is_default"])


# --- transactions -----------------------------------------------------------------

def insert_transactions(conn, user_id: int, rows: list[dict], raw_message: str, parser: str,
                        source: str = "text") -> uuid.UUID:
    """rows: wallet_id, category_id, type, amount_minor (signed), currency, description, occurred_on,
    and optionally fx_rate and amount_home."""
    batch_id = uuid.uuid4()
    for r in rows:
        conn.execute(
            """insert into transactions
               (user_id, batch_id, wallet_id, category_id, type, amount_minor, currency,
                description, occurred_on, source, parser, raw_message, fx_rate, amount_home)
               values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (user_id, batch_id, r["wallet_id"], r["category_id"], r["type"], r["amount_minor"],
             r["currency"], r["description"], r["occurred_on"], source, parser, raw_message,
             r.get("fx_rate"), r.get("amount_home")),
        )
    return batch_id


def set_card(conn, batch_id: uuid.UUID, chat_id: int, message_id: int, note: Optional[str] = None) -> None:
    conn.execute(
        "update transactions set card_chat_id = %s, card_message_id = %s, card_note = %s where batch_id = %s",
        (chat_id, message_id, note, batch_id),
    )


_BATCH_SQL = """
select t.id, t.user_id, t.batch_id, t.type, t.amount_minor, t.currency, t.description,
       t.occurred_on, t.wallet_id, t.category_id, t.card_chat_id, t.card_message_id,
       t.amount_home, t.card_note,
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
           where t.user_id = %s and t.occurred_on = %s and t.type in ('expense', 'income')
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


# --- M2: settings ----------------------------------------------------------------

USER_FIELDS = {"reminder_time", "reminders_on", "reminders_paused_until", "last_reminded_on",
               "snooze_until", "persona", "roast_level", "language"}


def update_user(conn, telegram_id: int, **fields) -> None:
    bad = set(fields) - USER_FIELDS
    if bad:
        raise ValueError(f"unknown user fields: {bad}")
    cols = ", ".join(f"{k} = %s" for k in fields)
    conn.execute(f"update users set {cols} where telegram_id = %s", (*fields.values(), telegram_id))


# --- M2: home-currency totals ----------------------------------------------------

def spent_on_home(conn, user_id: int, day: date) -> tuple[int, dict[str, int]]:
    """(spent in home currency, {currency: spent} for entries that have no rate yet)."""
    row = conn.execute(
        """select coalesce(-sum(amount_home), 0)::bigint as home
           from transactions where user_id = %s and occurred_on = %s and type = 'expense'
             and amount_home is not null""",
        (user_id, day),
    ).fetchone()
    missing = conn.execute(
        """select currency, -sum(amount_minor)::bigint as spent from transactions
           where user_id = %s and occurred_on = %s and type = 'expense' and amount_home is null
           group by currency having sum(amount_minor) <> 0""",
        (user_id, day),
    ).fetchall()
    return row["home"], {r["currency"]: r["spent"] for r in missing}


def month_by_category_home(conn, user_id: int, first_day: date, next_first_day: date) -> list[dict]:
    return conn.execute(
        """select t.type, t.category_id, coalesce(c.name, 'Uncategorised') as category,
                  coalesce(c.emoji, '') as emoji, sum(t.amount_home)::bigint as total,
                  count(*) filter (where t.amount_home is null) as unconverted
           from transactions t left join categories c on c.id = t.category_id
           where t.user_id = %s and t.occurred_on >= %s and t.occurred_on < %s
             and t.type in ('expense', 'income')
           group by 1, 2, 3, 4
           having coalesce(sum(t.amount_home), 0) <> 0 or count(*) filter (where t.amount_home is null) > 0
           order by t.type, abs(coalesce(sum(t.amount_home), 0)) desc""",
        (user_id, first_day, next_first_day),
    ).fetchall()


# --- M2: reminders ---------------------------------------------------------------

def users_for_tick(conn) -> list[dict]:
    return conn.execute(
        "select * from users where reminders_on or snooze_until is not null order by telegram_id"
    ).fetchall()


def claim_reminder(conn, user_id: int, day: date) -> bool:
    row = conn.execute(
        """update users set last_reminded_on = %s
           where telegram_id = %s and last_reminded_on is distinct from %s returning telegram_id""",
        (day, user_id, day),
    ).fetchone()
    return row is not None


def logged_on(conn, user_id: int, day: date) -> bool:
    """Any live expense/income for the day, or a no-spend mark."""
    row = conn.execute(
        """select exists (select 1 from live_transactions where user_id = %s and occurred_on = %s
                          and type in ('expense', 'income'))
               or exists (select 1 from day_marks where user_id = %s and day = %s) as logged""",
        (user_id, day, user_id, day),
    ).fetchone()
    return row["logged"]


def spent_anything_on(conn, user_id: int, day: date) -> bool:
    return conn.execute(
        "select 1 from live_transactions where user_id = %s and occurred_on = %s and type = 'expense' limit 1",
        (user_id, day),
    ).fetchone() is not None


def mark_no_spend(conn, user_id: int, day: date) -> bool:
    row = conn.execute(
        """insert into day_marks (user_id, day, kind) values (%s, %s, 'no_spend')
           on conflict do nothing returning day""",
        (user_id, day),
    ).fetchone()
    return row is not None


def claim_job(conn, key: str) -> bool:
    return conn.execute(
        "insert into job_runs (key) values (%s) on conflict do nothing returning key", (key,)
    ).fetchone() is not None


# --- M2: merchant rules ----------------------------------------------------------

LEARN_AFTER = 2


def record_correction(conn, user_id: int, keyword: str, category_id: int) -> int:
    row = conn.execute(
        """insert into merchant_rules (user_id, keyword, category_id) values (%s, %s, %s)
           on conflict (user_id, keyword, category_id)
           do update set corrections = merchant_rules.corrections + 1, updated_at = now()
           returning corrections""",
        (user_id, keyword, category_id),
    ).fetchone()
    return row["corrections"]


def active_rules(conn, user_id: int) -> dict[str, str]:
    """keyword -> category name, for rules corrected at least LEARN_AFTER times (strongest wins)."""
    rows = conn.execute(
        """select distinct on (r.keyword) r.keyword, c.name
           from merchant_rules r join categories c on c.id = r.category_id
           where r.user_id = %s and r.corrections >= %s
           order by r.keyword, r.corrections desc, r.updated_at desc""",
        (user_id, LEARN_AFTER),
    ).fetchall()
    return {r["keyword"]: r["name"] for r in rows}


def forget_rule(conn, user_id: int, keyword: str) -> int:
    return conn.execute(
        "delete from merchant_rules where user_id = %s and keyword = %s", (user_id, keyword)
    ).rowcount


# --- M2: budgets -----------------------------------------------------------------

def set_budget(conn, user_id: int, category_id: Optional[int], limit_minor: int) -> None:
    conn.execute(
        """insert into budgets (user_id, category_id, limit_minor) values (%s, %s, %s)
           on conflict (user_id, coalesce(category_id, 0)) do update set limit_minor = excluded.limit_minor""",
        (user_id, category_id, limit_minor),
    )


def delete_budget(conn, user_id: int, category_id: Optional[int]) -> int:
    return conn.execute(
        "delete from budgets where user_id = %s and coalesce(category_id, 0) = coalesce(%s, 0)",
        (user_id, category_id),
    ).rowcount


def budgets(conn, user_id: int) -> list[dict]:
    return conn.execute(
        """select b.category_id, b.limit_minor, c.name, c.emoji
           from budgets b left join categories c on c.id = b.category_id
           where b.user_id = %s order by b.category_id nulls first, c.sort_order""",
        (user_id,),
    ).fetchall()


def month_spent_home(conn, user_id: int, first_day: date, next_first_day: date) -> dict[Optional[int], int]:
    """Spent this month in home currency per category, plus None -> total."""
    rows = conn.execute(
        """select category_id, coalesce(-sum(amount_home), 0)::bigint as spent
           from transactions
           where user_id = %s and occurred_on >= %s and occurred_on < %s and type = 'expense'
           group by category_id""",
        (user_id, first_day, next_first_day),
    ).fetchall()
    out: dict[Optional[int], int] = {r["category_id"]: r["spent"] for r in rows}
    out[None] = sum(out.values())
    return out


# --- M2: wallets -----------------------------------------------------------------

def add_wallet(conn, user_id: int, name: str, type_: str, currency: str) -> WalletInfo:
    has_default = conn.execute(
        "select 1 from wallets where user_id = %s and currency = %s and is_default and not archived",
        (user_id, currency),
    ).fetchone()
    r = conn.execute(
        """insert into wallets (user_id, name, type, currency, is_default) values (%s, %s, %s, %s, %s)
           returning id, name, currency, is_default""",
        (user_id, name, type_, currency, not has_default),
    ).fetchone()
    return WalletInfo(r["id"], r["name"], r["currency"], r["is_default"])


def set_default_wallet(conn, user_id: int, wallet_id: int) -> None:
    w = conn.execute("select currency from wallets where id = %s and user_id = %s", (wallet_id, user_id)).fetchone()
    conn.execute("update wallets set is_default = false where user_id = %s and currency = %s",
                 (user_id, w["currency"]))
    conn.execute("update wallets set is_default = true where id = %s", (wallet_id,))
