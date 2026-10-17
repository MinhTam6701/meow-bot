-- M5: Mochi's bowl can follow your pace for the rest of a month ("Set Mochi's bowl to S$14.10/day").
-- From `bowl_override_from` to the end of that month the daily bowl is `bowl_override_minor`
-- instead of everyday budget ÷ days in the month. A new everyday budget clears it. Additive only.
alter table mochi_state
    add column bowl_override_minor bigint check (bowl_override_minor > 0),
    add column bowl_override_from  date;
