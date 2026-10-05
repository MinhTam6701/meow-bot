-- M4 photos: the owner's name (to spot transfers to yourself) and entries waiting for a tap.
alter table users add column if not exists full_name text;

create table if not exists pending_items (
    id          bigserial primary key,
    user_id     bigint not null references users (telegram_id) on delete cascade,
    source      text not null,                       -- 'photo' or 'voice'
    entry       jsonb not null,                      -- the parsed entry (amount, currency, type, ...)
    counterparty text,                               -- who the money went to, if a person
    note        text,                                -- the transfer note, e.g. "wifi"
    flags       text[] not null default '{}',        -- checks still to ask about: dup, own, person, big
    dup_of      text,                                -- the entry it may duplicate, as shown to the user
    date_guessed boolean not null default false,     -- no date on the picture, today was used
    status      text not null default 'open',        -- open, logged, skipped, moved
    message_id  bigint,
    created_at  timestamptz not null default now()
);
create index if not exists pending_items_user on pending_items (user_id, status);
alter table pending_items enable row level security;
