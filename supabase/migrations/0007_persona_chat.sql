-- The persona's last reaction, so a reply like "it was a birthday dinner" continues the chat.
alter table users add column if not exists last_reaction text;
alter table users add column if not exists last_reaction_at timestamptz;
