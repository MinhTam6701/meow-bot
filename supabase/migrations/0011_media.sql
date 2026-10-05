-- Pictures uploaded to Telegram once (e.g. Mochi's art), reused by file_id afterwards.
create table if not exists media (
    key        text primary key,          -- e.g. 'mochi/fat.jpg@3f2a9c1d' (changes when the file changes)
    file_id    text not null,
    created_at timestamptz not null default now()
);
alter table media enable row level security;
