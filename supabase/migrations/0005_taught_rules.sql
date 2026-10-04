-- Rules you taught by correcting a category beat the keyword table, even for single words.
-- Rules learned automatically from imported history only do so for multi-word phrases.
alter table merchant_rules add column taught boolean not null default false;
