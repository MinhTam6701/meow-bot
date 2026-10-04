-- M3: monthly report + balance check, streak, Mochi. Additive only.

-- Mochi's food bowl comes from the everyday budget (fixed bills excluded).
alter table users
    add column everyday_budget_minor bigint check (everyday_budget_minor > 0),
    add column awaiting text;  -- e.g. 'recon:<wallet_id>:<YYYY-MM-01>' while waiting for a typed balance

-- Which wallets the monthly balance check asks about.
alter table wallets add column check_monthly boolean not null default true;

create table mochi_state (
    user_id            bigint primary key references users(telegram_id) on delete cascade,
    weight             smallint not null default 50 check (weight between 0 and 100),
    away               boolean not null default false,  -- at grandma's house
    started_on         date not null,
    last_scored_on     date,
    pinned_message_id  bigint,
    updated_at         timestamptz not null default now()
);

-- One row per scored day: makes scoring idempotent and gives a weight history.
create table mochi_log (
    user_id       bigint not null references users(telegram_id) on delete cascade,
    day           date not null,
    spent_minor   bigint not null,
    bowl_minor    bigint not null,
    result        text not null,      -- no_spend | half | within | over | splurge | silent
    delta         smallint not null,
    weight_after  smallint not null,
    away_after    boolean not null,
    created_at    timestamptz not null default now(),
    primary key (user_id, day)
);

create table monthly_reports (
    user_id     bigint not null references users(telegram_id) on delete cascade,
    month       date not null,        -- first day of the reported month
    sent_at     timestamptz not null default now(),
    message_id  bigint,
    primary key (user_id, month)
);

create table reconciliations (
    user_id     bigint not null references users(telegram_id) on delete cascade,
    wallet_id   bigint not null references wallets(id) on delete cascade,
    month       date not null,
    expected    bigint,
    actual      bigint,
    difference  bigint,
    status      text not null default 'pending' check (status in ('pending', 'matched', 'adjusted', 'skipped')),
    answered_at timestamptz,
    primary key (user_id, wallet_id, month)
);

alter table mochi_state     enable row level security;
alter table mochi_log       enable row level security;
alter table monthly_reports enable row level security;
alter table reconciliations enable row level security;
