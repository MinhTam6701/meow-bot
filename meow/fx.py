"""Exchange rates: one fetch a day, stored in fx_rates, used to give every entry a home-currency amount."""
from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from typing import Callable, Optional

import httpx

from .money import from_minor, to_minor

log = logging.getLogger(__name__)

RATES_URL = "https://open.er-api.com/v6/latest/{base}"

# fetch(base) -> {currency: units of currency per 1 base}
Fetcher = Callable[[str], dict[str, Decimal]]


def fetch_open_er_api(base: str) -> dict[str, Decimal]:
    """Free, keyless, daily rates that include VND (exchangerate-api.com open access)."""
    resp = httpx.get(RATES_URL.format(base=base), timeout=5.0)
    data = resp.json()
    if data.get("result") != "success":
        raise RuntimeError(f"rates API: {data.get('error-type', resp.status_code)}")
    return {k: Decimal(str(v)) for k, v in data["rates"].items() if v}


def has_rates(conn, base: str, day: date) -> bool:
    return conn.execute(
        "select 1 from fx_rates where base = %s and day = %s limit 1", (base, day)
    ).fetchone() is not None


def store_rates(conn, base: str, day: date, rates: dict[str, Decimal]) -> int:
    with conn.cursor() as cur:
        cur.executemany(
            """insert into fx_rates (day, base, currency, units_per_base) values (%s, %s, %s, %s)
               on conflict (day, base, currency) do update set units_per_base = excluded.units_per_base,
                                                               fetched_at = now()""",
            [(day, base, c, r) for c, r in rates.items() if r > 0],
        )
    return len(rates)


def ensure_rates(conn, base: str, day: date, fetch: Fetcher) -> bool:
    """Fetch today's rates once. Returns False if they couldn't be fetched (it's retried later)."""
    if has_rates(conn, base, day):
        return True
    try:
        rates = fetch(base)
    except Exception as exc:  # network, API down: entries keep amount_home = null until later
        log.warning("could not fetch FX rates for %s: %s", base, exc)
        return False
    store_rates(conn, base, day, rates)
    return True


def units_per_home(conn, home: str, currency: str, day: date) -> Optional[Decimal]:
    """Latest known rate on or before `day` (or the earliest after, for back-dated entries)."""
    if currency == home:
        return Decimal(1)
    row = conn.execute(
        """select units_per_base from fx_rates where base = %s and currency = %s
           order by (day <= %s) desc, abs(day - %s::date) limit 1""",
        (home, currency, day, day),
    ).fetchone()
    return Decimal(row["units_per_base"]) if row else None


def to_home(amount_minor: int, currency: str, home: str, rate: Decimal) -> int:
    """Convert signed minor units of `currency` to signed minor units of `home`."""
    return to_minor(from_minor(amount_minor, currency) / rate, home)


def convert(amount_minor: int, from_cur: str, to_cur: str, home: str, rate_from: Decimal, rate_to: Decimal) -> int:
    """Between any two currencies through the home currency."""
    value = from_minor(amount_minor, from_cur) / rate_from * rate_to
    return to_minor(value, to_cur)


def backfill(conn, fetch: Optional[Fetcher] = None) -> int:
    """Give entries that were saved without a rate (API was down) their home amount."""
    rows = conn.execute(
        """select t.id, t.amount_minor, t.currency, t.occurred_on, u.home_currency
           from transactions t join users u on u.telegram_id = t.user_id
           where t.amount_home is null
           order by t.id limit 500"""
    ).fetchall()
    done = 0
    for r in rows:
        rate = units_per_home(conn, r["home_currency"], r["currency"], r["occurred_on"])
        if rate is None:
            continue
        conn.execute(
            "update transactions set fx_rate = %s, amount_home = %s where id = %s",
            (rate, to_home(r["amount_minor"], r["currency"], r["home_currency"], rate), r["id"]),
        )
        done += 1
    return done
