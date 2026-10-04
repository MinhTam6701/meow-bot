-- M2.1: categories that match how Tam actually spends, wallet nicknames, recurring entries.

-- Categories ----------------------------------------------------------------------
-- Rename the v1 starter categories in place (keeps ids, so entries and budgets follow),
-- then add the new ones for every user.
update categories set name = 'Food & Drinks'  where name = 'Food'  and type = 'expense';
update categories set name = 'Entertainment', emoji = '🎮' where name = 'Fun' and type = 'expense';
update categories set name = 'Housing', emoji = '🏠' where name = 'Bills' and type = 'expense';

insert into categories (user_id, name, emoji, type, sort_order)
select u.telegram_id, c.name, c.emoji, c.type, c.sort_order
from users u
cross join (values
    ('Food & Drinks', '🍜', 'expense', 0),
    ('Groceries', '🛒', 'expense', 1),
    ('Transport', '🚕', 'expense', 2),
    ('Housing', '🏠', 'expense', 3),
    ('Phone', '📱', 'expense', 4),
    ('Shopping', '🛍', 'expense', 5),
    ('Beauty', '💅', 'expense', 6),
    ('Health', '💊', 'expense', 7),
    ('Entertainment', '🎮', 'expense', 8),
    ('Subscriptions & Fees', '🔁', 'expense', 9),
    ('Travel', '✈️', 'expense', 10),
    ('Education', '📚', 'expense', 11),
    ('Gifts & Family', '🎁', 'expense', 12),
    ('Other', '📦', 'expense', 13),
    ('Salary', '💼', 'income', 20),
    ('Family gift', '🧧', 'income', 21),
    ('Refund', '💸', 'income', 22),
    ('Investment', '📈', 'income', 23),
    ('Other income', '💰', 'income', 24)
) as c(name, emoji, type, sort_order)
on conflict (user_id, name) do update set sort_order = excluded.sort_order, emoji = excluded.emoji;

-- Wallets -------------------------------------------------------------------------
alter table wallets
    add column aliases   text[] not null default '{}',   -- other names: 'tiền mặt', 'vp'
    add column in_total  boolean not null default true;  -- false for credit cards: not part of net worth

drop view if exists wallet_balances;
create view wallet_balances with (security_invoker = true) as
select w.id as wallet_id, w.user_id, w.name, w.currency, w.type, w.in_total,
       coalesce(sum(t.amount_minor), 0)::bigint as balance_minor
from wallets w
left join transactions t on t.wallet_id = w.id
where not w.archived
group by w.id;

-- Recurring entries (rent, phone bill...) ------------------------------------------
create table recurring_rules (
    id            bigserial primary key,
    user_id       bigint not null references users(telegram_id) on delete cascade,
    description   text not null,
    type          text not null check (type in ('expense', 'income')),
    amount_minor  bigint not null check (amount_minor > 0),
    currency      text not null,
    wallet_id     bigint not null references wallets(id) on delete cascade,
    category_id   bigint references categories(id) on delete set null,
    day_of_month  smallint not null check (day_of_month between 1 and 31),
    next_run      date not null,
    active        boolean not null default true,
    created_at    timestamptz not null default now()
);
alter table recurring_rules enable row level security;

alter table transactions add column recurring_id bigint references recurring_rules(id) on delete set null;
create unique index transactions_one_per_recurring_day
    on transactions (recurring_id, occurred_on) where recurring_id is not null and reverses_id is null;

-- Imported entries keep their origin so a re-import can find them.
alter table transactions add column import_key text;
create unique index transactions_import_key on transactions (user_id, import_key) where import_key is not null;
