-- Bills you need (rent, phone, bank fees) vs subscriptions you could live without (iQIYI, Claude).
-- Both kinds of recurring rule log themselves; subscriptions also get renewal reminders and the
-- quarterly "still using it?" check, and are listed under /subscriptions instead of /recurring.
alter table recurring_rules add column if not exists kind text not null default 'bill'
    check (kind in ('bill', 'subscription'));
alter table recurring_rules add column if not exists reminded_for date;   -- renewal already reminded about
alter table recurring_rules add column if not exists checked_on date;     -- last "still using it?"
