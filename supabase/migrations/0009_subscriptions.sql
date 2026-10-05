-- M4: subscriptions (suggested by the detector, confirmed by you) and the weekly recap.
create table if not exists subscriptions (
    id              bigserial primary key,
    user_id         bigint not null references users (telegram_id) on delete cascade,
    key             text not null,                 -- normalised description, e.g. 'netflix'
    currency        text not null,
    name            text not null,                 -- as you wrote it, e.g. 'Netflix'
    amount_minor    bigint not null check (amount_minor > 0),
    interval        text not null check (interval in ('weekly', 'monthly', 'quarterly', 'yearly')),
    status          text not null default 'suggested'
                    check (status in ('suggested', 'active', 'dismissed', 'cancelled')),
    wallet_id       bigint references wallets (id) on delete set null,
    category_id     bigint references categories (id) on delete set null,
    last_charge_on  date not null,
    next_due        date not null,
    reminded_for    date,                          -- the renewal date we already reminded about
    checked_on      date,                          -- last "still using it?" check
    created_at      timestamptz not null default now(),
    unique (user_id, key, currency)
);
alter table subscriptions enable row level security;
