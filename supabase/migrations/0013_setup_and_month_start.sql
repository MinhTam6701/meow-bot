-- M6: your own month and a guided setup. Additive only.

-- The day your month starts (the day after payday, say): budgets, Mochi's bowl, /month, /report and
-- the charts follow it. 1 = calendar months. Days 29-31 aren't allowed so every month has the day.
-- The wallet balance check stays on the 1st, to match bank statements.
alter table users
    add column month_start_day smallint not null default 1 check (month_start_day between 1 and 28),
    -- /setup in progress: which step, the currencies picked, wallets picked per currency.
    add column setup jsonb;
