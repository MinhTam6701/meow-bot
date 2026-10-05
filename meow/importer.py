"""Import a Money Manager export (xlsx) and backup (.mmbackup) into M.E.O.W.

- Entries (expenses, income, transfers) come from the xlsx export.
- Current wallet balances come from the backup; an "opening balance" adjustment per wallet
  makes the ledger end exactly at those balances.
- Historical SGD/VND rates come from the export itself (it stores every SGD entry in VND too).
- Phrases you used consistently become learned rules ("mua đồ nấu ăn" -> Groceries).

The plan is applied with plain SQL so it can run through psycopg (local) or be split into
chunks for the Supabase SQL editor / MCP.
"""
from __future__ import annotations

import collections
import hashlib
import json
import sqlite3
import statistics
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from .text import normalize, phrase_keys

# Money Manager category -> M.E.O.W. category
EXPENSE_MAP = {
    "Ăn uống": "Food & Drinks", "Transportation": "Transport", "Mua sắm": "Shopping", "Nhà cửa": "Housing",
    "Leisure": "Subscriptions & Fees", "Điện thoại": "Phone", "Gifts": "Gifts & Family", "Education": "Education",
    "Giải trí": "Entertainment", "Health": "Health", "Du lịch": "Travel", "Làm đẹp": "Beauty", "Other": "Other",
}
INCOME_MAP = {"Lương": "Salary", "Gift": "Family gift", "Hoàn tiền": "Refund", "Đầu tư": "Investment",
              "Other": "Other income"}
# Food entries that are really groceries (accent-free substrings of the description)
GROCERY_HINTS = ("do nau an", "nau an", "di cho", "sieu thi", "ntuc", "fairprice", "sheng siong", "giant",
                 "mua do an", "mua sua", "mua trung", "mua rau", "mua thit", "mua gao", "cold storage")

# Money Manager account -> wallet (name, type, aliases, in_total)
WALLET_MAP = {
    "DBS": ("DBS", "bank", ["dbs"], True),
    "VPBank": ("VPBank", "bank", ["vp", "vpbank"], True),
    "VCB": ("VCB", "bank", ["vietcombank"], True),
    "BIDV": ("BIDV", "bank", [], True),
    "MBBank": ("MBBank", "bank", ["mb"], True),
    "Techcombank": ("Techcombank", "bank", ["tcb"], True),
    "Tiền mặt": ("CashVND", "cash", ["tiền mặt", "cash vnd"], True),
    "Tiền mặt SGD": ("Cash", "cash", ["tiền mặt sgd", "cash sgd"], True),
    "Tiết kiệm": ("Savings", "bank", ["tiết kiệm"], True),
    "Tiết kiệm mua nhà": ("HouseFund", "bank", ["tiết kiệm mua nhà", "house fund"], True),
    "VPBank (credit)": ("VPCredit", "credit", ["vp credit", "thẻ tín dụng"], False),
}
DEFAULT_WALLETS = {"SGD": "DBS", "VND": "VCB"}
MINOR = {"SGD": 2, "VND": 0}


@dataclass
class Plan:
    wallets: list[dict] = field(default_factory=list)
    rows: list[dict] = field(default_factory=list)
    fx: list[dict] = field(default_factory=list)
    balances: dict[str, int] = field(default_factory=dict)
    rules: list[dict] = field(default_factory=list)
    first_day: Optional[date] = None
    last_day: Optional[date] = None

    def summary(self) -> dict:
        kinds = collections.Counter(r["type"] for r in self.rows)
        return {"wallets": len(self.wallets), "rows": len(self.rows), **kinds, "fx_days": len(self.fx),
                "rules": len(self.rules), "from": str(self.first_day), "to": str(self.last_day)}


def to_minor(value, currency: str) -> int:
    d = MINOR[currency]
    return int((Decimal(str(value)) * (10 ** d)).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _key(*parts) -> str:
    return "mm:" + hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:20]


def _category(old: str, kind: str, description: str) -> str:
    if kind == "income":
        return INCOME_MAP.get(old, "Other income")
    new = EXPENSE_MAP.get(old, "Other")
    if new == "Food & Drinks":
        flat = " ".join(normalize(w) for w in description.split())
        if any(h in flat for h in GROCERY_HINTS):
            return "Groceries"
    return new


def read_balances(backup_path: str) -> dict[str, tuple[str, int]]:
    """account title -> (currency, balance in minor units) from a .mmbackup (zip with MyFinance.db)."""
    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(backup_path) as z:
            z.extract("MyFinance.db", tmp)
        con = sqlite3.connect(f"{tmp}/MyFinance.db")
        out = {}
        for title, cur, value in con.execute(
                """select a.title, a.currencyCode, coalesce(b.value, 0) from account a
                   left join account_balance b on b.uid = a.uid where a.isRemoved = 0"""):
            # Money Manager stores amounts x100 for every currency
            out[title] = (cur, to_minor(Decimal(value) / 100, cur))
        con.close()
    return out


def build_plan(xlsx_path: str, backup_path: Optional[str]) -> Plan:
    import pandas as pd

    plan = Plan()
    exp = pd.read_excel(xlsx_path, sheet_name="Expenses", header=1)
    inc = pd.read_excel(xlsx_path, sheet_name="Income", header=1)
    tra = pd.read_excel(xlsx_path, sheet_name="Transfers", header=1)

    accounts = set(exp["Account"]) | set(inc["Account"]) | set(tra["Outgoing"]) | set(tra["Incoming"])
    balances = read_balances(backup_path) if backup_path else {}
    accounts |= set(balances)
    unknown = accounts - set(WALLET_MAP)
    if unknown:
        raise ValueError(f"No wallet mapping for accounts: {sorted(unknown)}")

    currency_of: dict[str, str] = {}
    for df, col_acc, col_cur in ((exp, "Account", "Account currency"), (inc, "Account", "Account currency")):
        for a, c in zip(df[col_acc], df[col_cur]):
            currency_of[a] = c
    for a, (c, _) in balances.items():
        currency_of.setdefault(a, c)
    for a, c in zip(tra["Outgoing"], tra["Outgoing currency"]):
        currency_of.setdefault(a, c)
    for a, c, out_c in zip(tra["Incoming"], tra["Incoming currency"], tra["Outgoing currency"]):
        currency_of.setdefault(a, c if isinstance(c, str) and c else out_c)
    bad = {c for c in currency_of.values() if c not in MINOR}
    if bad:
        raise ValueError(f"Unsupported currencies: {bad}")

    for acc in sorted(accounts):
        name, type_, aliases, in_total = WALLET_MAP[acc]
        cur = currency_of[acc]
        plan.wallets.append({"name": name, "type": type_, "currency": cur, "aliases": aliases,
                             "in_total": in_total, "is_default": DEFAULT_WALLETS.get(cur) == name})

    seen = collections.Counter()

    def add(kind, day, acc, amount_minor, category, description, batch, currency):
        k = (kind, day, acc, amount_minor, description)
        seen[k] += 1
        plan.rows.append({"type": kind, "day": day.isoformat(), "wallet": WALLET_MAP[acc][0], "currency": currency,
                          "amount_minor": amount_minor, "category": category, "description": description,
                          "batch": batch, "import_key": _key(*k, seen[k])})

    for kind, df in (("expense", exp), ("income", inc)):
        for _, r in df.iterrows():
            day = r["Date and time"].date()
            cur = r["Account currency"]
            desc = str(r["Comment"]).strip() if isinstance(r["Comment"], str) and r["Comment"].strip() else r["Category"]
            minor = to_minor(r["Amount in account currency"], cur)
            cat = _category(r["Category"], kind, desc)
            add(kind, day, r["Account"], -minor if kind == "expense" else minor, cat, desc, None, cur)
            plan.rows[-1]["batch"] = plan.rows[-1]["import_key"]

    for _, r in tra.iterrows():
        day = r["Date and time"].date()
        out_cur, in_acc = r["Outgoing currency"], r["Incoming"]
        in_cur = r["Incoming currency"] if isinstance(r["Incoming currency"], str) and r["Incoming currency"] else out_cur
        out_minor = to_minor(r["Amount in outgoing currency"], out_cur)
        in_amount = r["Amount in incoming currency"]
        in_minor = to_minor(in_amount, in_cur) if in_amount not in ("", None) and not pd.isna(in_amount) else out_minor
        batch = _key("transfer", day, r["Outgoing"], in_acc, out_minor, in_minor, len(plan.rows))
        add("transfer", day, r["Outgoing"], -out_minor, None, f"to {WALLET_MAP[in_acc][0]}", batch, out_cur)
        add("transfer", day, in_acc, in_minor, None, f"from {WALLET_MAP[r['Outgoing']][0]}", batch, in_cur)

    days = [date.fromisoformat(r["day"]) for r in plan.rows]
    plan.first_day, plan.last_day = min(days), max(days)

    # Historical VND-per-SGD rate, from SGD entries that the app also stored in VND.
    per_day = collections.defaultdict(list)
    for df in (exp, inc):
        sgd = df[(df["Account currency"] == "SGD") & (df["Amount in account currency"] > 0.5)]
        for d, vnd, s in zip(sgd["Date and time"], sgd["Amount in default currency"], sgd["Amount in account currency"]):
            per_day[d.date()].append(float(vnd) / float(s))
    plan.fx = [{"day": d.isoformat(), "rate": round(statistics.median(v), 4)} for d, v in sorted(per_day.items())]

    for acc, (cur, minor) in balances.items():
        plan.balances[WALLET_MAP[acc][0]] = minor

    # Phrases used consistently (>= 2 times, >= 80% one category) become learned rules.
    counts: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for r in plan.rows:
        if r["type"] in ("expense", "income"):
            for k in phrase_keys(r["description"]):
                counts[k][(r["category"], r["type"])] += 1
    for k, c in counts.items():
        (cat, _), n = c.most_common(1)[0]
        total = sum(c.values())
        min_uses = 2 if " " in k else 5  # single words need more evidence
        if n >= min_uses and n / total >= 0.8:
            plan.rules.append({"keyword": k, "category": cat, "count": n})
    return plan


# --- applying ---------------------------------------------------------------------

STAGING = """
create table if not exists import_staging_rows (
    type text, day date, wallet text, currency text, amount_minor bigint, category text,
    description text, batch text, import_key text
);
create table if not exists import_staging_meta (kind text, payload jsonb);
alter table import_staging_rows enable row level security;
alter table import_staging_meta enable row level security;
"""


def _lit(obj) -> str:
    return "'" + json.dumps(obj, ensure_ascii=False).replace("'", "''") + "'::jsonb"


def staging_sql(plan: Plan, chunk: int = 400) -> list[str]:
    """SQL statements to load the plan into staging tables (split into chunks)."""
    stmts = [CLEANUP, STAGING]
    meta = {"wallets": plan.wallets, "fx": plan.fx, "balances": plan.balances, "rules": plan.rules,
            "first_day": str(plan.first_day), "last_day": str(plan.last_day)}
    stmts.append("insert into import_staging_meta values " +
                 ", ".join(f"('{k}', {_lit(v)})" for k, v in meta.items()) + ";")
    for i in range(0, len(plan.rows), chunk):
        part = plan.rows[i:i + chunk]
        stmts.append(
            "insert into import_staging_rows select * from jsonb_to_recordset(" + _lit(part) + ") as r("
            "type text, day date, wallet text, currency text, amount_minor bigint, category text, "
            "description text, batch text, import_key text);")
    return stmts


def finalize_sql(telegram_id: int) -> str:
    """Moves staged data into the ledger for one user. Replaces bot entries up to the last imported day."""
    uid = int(telegram_id)
    return f"""
do $$
declare
    meta_w jsonb := (select payload from import_staging_meta where kind = 'wallets');
    first_day date := ((select payload from import_staging_meta where kind = 'first_day') #>> '{{}}')::date;
    last_day date := ((select payload from import_staging_meta where kind = 'last_day') #>> '{{}}')::date;
begin
    if not exists (select 1 from users where telegram_id = {uid}) then
        raise exception 'user {uid} has not started the bot yet (send /start first)';
    end if;

    -- 1. wallets: the v1 "VP" wallet becomes "VPBank"
    if exists (select 1 from wallets where user_id = {uid} and name = 'VP')
       and not exists (select 1 from wallets where user_id = {uid} and name = 'VPBank') then
        update wallets set name = 'VPBank' where user_id = {uid} and name = 'VP';
    end if;
    insert into wallets (user_id, name, type, currency, aliases, in_total, is_default)
    select {uid}, w->>'name', w->>'type', w->>'currency',
           array(select jsonb_array_elements_text(w->'aliases')), (w->>'in_total')::boolean, false
    from jsonb_array_elements(meta_w) w
    on conflict (user_id, name) do update set type = excluded.type, currency = excluded.currency,
        aliases = excluded.aliases, in_total = excluded.in_total, archived = false;
    update wallets set is_default = false where user_id = {uid};
    update wallets set is_default = true where user_id = {uid}
       and name in (select w->>'name' from jsonb_array_elements(meta_w) w where (w->>'is_default')::boolean);

    -- 2. the old app is the truth: drop bot entries up to its last day, and any earlier import
    delete from transactions where user_id = {uid}
       and (import_key is not null or (source not in ('import') and occurred_on <= last_day));

    -- 3. historical rates (live API rates win where both exist)
    insert into fx_rates (day, base, currency, units_per_base)
    select (f->>'day')::date, 'SGD', 'VND', (f->>'rate')::numeric
    from jsonb_array_elements((select payload from import_staging_meta where kind = 'fx')) f
    on conflict (day, base, currency) do nothing;

    -- 4. entries
    insert into transactions (user_id, batch_id, wallet_id, category_id, type, amount_minor, currency,
                              description, occurred_on, source, parser, raw_message, fx_rate, amount_home, import_key)
    select {uid}, md5(s.batch)::uuid, w.id, c.id, s.type, s.amount_minor, s.currency, s.description, s.day,
           'import', 'import', null, rate.r,
           case when s.currency = 'SGD' then s.amount_minor else round(s.amount_minor * 100.0 / rate.r)::bigint end,
           s.import_key
    from import_staging_rows s
    join wallets w on w.user_id = {uid} and w.name = s.wallet
    left join categories c on c.user_id = {uid} and c.name = s.category and c.type = s.type
    cross join lateral (
        select case when s.currency = 'SGD' then 1::numeric else (
            select units_per_base from fx_rates where base = 'SGD' and currency = s.currency
            order by (day <= s.day) desc, abs(day - s.day) limit 1) end as r
    ) rate;

    -- 5. opening balance per wallet so the ledger ends at the old app's balances
    insert into transactions (user_id, batch_id, wallet_id, category_id, type, amount_minor, currency,
                              description, occurred_on, source, parser, fx_rate, amount_home, import_key)
    select {uid}, md5('open:' || w.name)::uuid, w.id, null, 'adjustment', diff, w.currency,
           'Opening balance (Money Manager import)', first_day - 1, 'import', 'import', rate.r,
           case when w.currency = 'SGD' then diff else round(diff * 100.0 / rate.r)::bigint end,
           'mm:open:' || w.name
    from (
        select w.id, w.name, w.currency, (b.value #>> '{{}}')::bigint
               - coalesce((select sum(amount_minor) from transactions t
                           where t.wallet_id = w.id and t.occurred_on <= last_day), 0) as diff
        from jsonb_each((select payload from import_staging_meta where kind = 'balances')) b
        join wallets w on w.user_id = {uid} and w.name = b.key
    ) w
    cross join lateral (
        select case when w.currency = 'SGD' then 1::numeric else (
            select units_per_base from fx_rates where base = 'SGD' and currency = w.currency
            order by abs(day - (first_day - 1)) limit 1) end as r
    ) rate
    where w.diff <> 0;

    -- 6. learned phrases
    insert into merchant_rules (user_id, keyword, category_id, corrections)
    select {uid}, r->>'keyword', c.id, (r->>'count')::int
    from jsonb_array_elements((select payload from import_staging_meta where kind = 'rules')) r
    join categories c on c.user_id = {uid} and c.name = r->>'category'
    on conflict (user_id, keyword, category_id)
    do update set corrections = greatest(merchant_rules.corrections, excluded.corrections);
end $$;
"""

CLEANUP = "drop table if exists import_staging_rows, import_staging_meta;"
