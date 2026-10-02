-- The persona line and budget alerts shown on a confirm card, kept so the card can be redrawn.
alter table transactions add column card_note text;
