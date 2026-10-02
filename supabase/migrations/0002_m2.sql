-- M.E.O.W. M2: reminders, personas, FX, budgets, merchant rules, transfers.
-- Additive only, so the running v1 code keeps working while this is applied.

-- Reminder and persona settings ------------------------------------------------
alter table users
    add column reminder_time        time not null default '21:30',
    add column reminders_on         boolean not null default true,
    add column reminders_paused_until date,
    add column last_reminded_on     date,
    add column snooze_until         timestamptz,
    add column persona              text not null default 'cat',
    add column roast_level          smallint not null default 1 check (roast_level between 0 and 3);
-- users.language already exists: 'en' | 'vi' | 'mix'

-- A day with no spending still counts as a logged day.
create table day_marks (
    user_id     bigint not null references users(telegram_id) on delete cascade,
    day         date not null,
    kind        text not null check (kind in ('no_spend')),
    created_at  timestamptz not null default now(),
    primary key (user_id, day)
);

-- Exchange rates ------------------------------------------------------------------
-- units_per_base: how many units of `currency` one unit of `base` buys (1 SGD = 20292 VND).
create table fx_rates (
    day            date not null,
    base           text not null,
    currency       text not null,
    units_per_base numeric(24, 10) not null check (units_per_base > 0),
    fetched_at     timestamptz not null default now(),
    primary key (day, base, currency)
);

alter table transactions
    add column fx_rate      numeric(24, 10),   -- units of `currency` per 1 home-currency unit
    add column amount_home  bigint;            -- signed, minor units of the user's home currency

-- Transfers between wallets: two rows (out and in) in one batch.
alter table transactions drop constraint transactions_type_check;
alter table transactions add constraint transactions_type_check
    check (type in ('expense', 'income', 'adjustment', 'transfer'));

-- Budgets (monthly, in the home currency). category_id null = whole month.
create table budgets (
    id           bigserial primary key,
    user_id      bigint not null references users(telegram_id) on delete cascade,
    category_id  bigint references categories(id) on delete cascade,
    limit_minor  bigint not null check (limit_minor > 0),
    created_at   timestamptz not null default now()
);
create unique index budgets_one_per_category on budgets (user_id, coalesce(category_id, 0));

-- Merchant rules learned from category corrections -----------------------------
create table merchant_rules (
    user_id      bigint not null references users(telegram_id) on delete cascade,
    keyword      text not null,
    category_id  bigint not null references categories(id) on delete cascade,
    corrections  int not null default 1,
    updated_at   timestamptz not null default now(),
    primary key (user_id, keyword, category_id)
);

-- Scheduled jobs run at most once per key (e.g. 'fx:2026-10-02').
create table job_runs (
    key         text primary key,
    ran_at      timestamptz not null default now()
);

alter table day_marks      enable row level security;
alter table fx_rates       enable row level security;
alter table budgets        enable row level security;
alter table merchant_rules enable row level security;
alter table job_runs       enable row level security;

-- Views are recreated so they pick up the new transaction columns.
create or replace view live_transactions with (security_invoker = true) as
select t.*
from transactions t
where t.reverses_id is null
  and not exists (select 1 from transactions r where r.reverses_id = t.id);
