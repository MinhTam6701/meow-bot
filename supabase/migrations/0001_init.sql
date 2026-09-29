-- M.E.O.W. v1 schema
-- Money is stored as signed integers in minor units (cents for SGD, dong for VND).
-- Expenses are negative, income is positive. The ledger is append-only:
-- amounts are never edited; undo inserts a reversal row (reverses_id).

create table users (
    telegram_id    bigint primary key,
    first_name     text,
    home_currency  text not null default 'SGD',
    timezone       text not null default 'Asia/Singapore',
    language       text not null default 'en',
    pending_input  text,            -- original message while the bot waits for a clarification answer
    created_at     timestamptz not null default now()
);

create table wallets (
    id          bigserial primary key,
    user_id     bigint not null references users(telegram_id) on delete cascade,
    name        text not null,
    type        text not null check (type in ('cash', 'bank', 'ewallet', 'credit')),
    currency    text not null,
    is_default  boolean not null default false,   -- default wallet for its currency
    archived    boolean not null default false,
    created_at  timestamptz not null default now(),
    unique (user_id, name)
);
create unique index wallets_one_default_per_currency
    on wallets (user_id, currency) where is_default;

create table categories (
    id         bigserial primary key,
    user_id    bigint not null references users(telegram_id) on delete cascade,
    name       text not null,
    emoji      text not null default '',
    type       text not null check (type in ('expense', 'income')),
    parent_id  bigint references categories(id),
    sort_order int not null default 0,
    unique (user_id, name)
);

create table transactions (
    id            bigserial primary key,
    user_id       bigint not null references users(telegram_id) on delete cascade,
    batch_id      uuid not null,               -- all entries from one message
    wallet_id     bigint not null references wallets(id),
    category_id   bigint references categories(id),
    type          text not null check (type in ('expense', 'income', 'adjustment')),
    amount_minor  bigint not null,             -- signed
    currency      text not null,
    description   text,
    occurred_on   date not null,               -- the user's local date
    source        text not null default 'text',
    parser        text,                        -- 'rule' | 'llm' | 'command'
    raw_message   text,
    reverses_id   bigint references transactions(id),
    card_chat_id  bigint,
    card_message_id bigint,
    created_at    timestamptz not null default now()
);
create index transactions_user_date on transactions (user_id, occurred_on);
create index transactions_batch on transactions (batch_id);
create unique index transactions_one_reversal on transactions (reverses_id) where reverses_id is not null;

-- Live entries: not a reversal and not reversed.
create view live_transactions as
select t.*
from transactions t
where t.reverses_id is null
  and not exists (select 1 from transactions r where r.reverses_id = t.id);

create view wallet_balances as
select w.id as wallet_id, w.user_id, w.name, w.currency, w.type,
       coalesce(sum(t.amount_minor), 0)::bigint as balance_minor
from wallets w
left join transactions t on t.wallet_id = w.id
where not w.archived
group by w.id;

-- Telegram retries webhooks; each update_id is handled once.
create table processed_updates (
    update_id     bigint primary key,
    processed_at  timestamptz not null default now()
);

create table llm_calls (
    id             bigserial primary key,
    user_id        bigint,
    purpose        text not null,
    model          text not null,
    input_tokens   int,
    output_tokens  int,
    cost_usd       numeric(10, 6),
    latency_ms     int,
    ok             boolean not null,
    input_text     text,
    output_json    jsonb,
    error          text,
    created_at     timestamptz not null default now()
);
create index llm_calls_user_day on llm_calls (user_id, created_at);

-- The bot connects as the database owner through the pooler, which bypasses RLS.
-- RLS with no policies blocks the public REST API (anon / authenticated keys).
alter table users              enable row level security;
alter table wallets            enable row level security;
alter table categories         enable row level security;
alter table transactions       enable row level security;
alter table processed_updates  enable row level security;
alter table llm_calls          enable row level security;

-- Views run with the caller's rights so they respect RLS too.
alter view live_transactions set (security_invoker = true);
alter view wallet_balances   set (security_invoker = true);
