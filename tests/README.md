# M.E.O.W. tests

Every test, by file. How the tests work and how to run them: see **Testing** in the main README.

```bash
python -m pytest -q                         # everything that can run here
python -m pytest -q tests/test_photos.py    # one file
python -m pytest -q -k duplicate            # by name
```

**155 tests, 254 checks** (a test with several examples runs once per example).

## `test_parser.py` · 12 tests, 82 checks

Text parser on its own: amounts, currencies, dates, several items per message (no database).

- rule cases (49 examples)
- hard cases fall back to llm (3 examples)
- rules do not guess (5 examples)
- amount tokens (13 examples)
- bad amount tokens (5 examples)
- minor units and format
- llm used only when rules fail
- llm entries are validated and cleaned
- llm question is passed through
- llm bad output becomes a question not a crash
- llm call matches real sdk signature
- llm api error is logged not raised

## `test_streak_mochi.py` · 8 tests, 8 checks

Streak and Mochi rules on their own (no database).

- streak counts back from today or yesterday
- freeze bridges one missed day after a good week
- freeze needs five of seven and one per week
- milestones and accessories
- bowl and results
- weight is capped and clamped
- grandmas house and return
- stages moods and status line

## `test_bot_flow.py` · 14 tests, 14 checks

Core logging end to end: card, undo, category/wallet buttons, Claude fallback, balances.

- start seeds wallets and categories
- stranger is turned away
- log multi entry message
- undo button appends reversal
- change category and wallet
- other users cannot touch my entries
- llm fallback logs cost and saves
- clarifying question then answer
- wallet currency mismatch asks
- unknown currency gets a cash wallet
- setbalance balance month undo
- duplicate update is ignored
- yesterday is in user timezone
- non money message skips llm

## `test_m2.py` · 22 tests, 25 checks

M2: exchange rates, end-of-day reminder, learned categories, personas, budgets, transfers.

- every entry gets a home amount
- rates down then backfilled by tick
- reminder sent once when nothing logged
- no reminder if logged paused or off
- reminder window and custom time
- missed window sends nothing
- no spend button
- snooze one hour
- bare number after reminder is day total
- pause button
- learns after two corrections
- persona flow and plain mode
- language setting
- budget alerts at 80 and 100
- total budget and bad category
- parse transfer (4 examples)
- parse transfer errors and non transfers
- transfers move money but are not spending
- cross currency transfer estimated and exact
- withdraw creates cash wallet
- wallet default and validation
- settings summary

## `test_m21.py` · 10 tests, 10 checks

M2.1: your Money Manager history, learned phrases, recurring bills, wallet nicknames.

- phrase keys
- next monthly
- learned phrase beats keywords
- wallet aliases
- claude gets similar past entries
- balance total excludes credit
- recurring rent
- budget accepts short category names
- rules listing and forget phrase
- your corrections beat imported history

## `test_m3.py` · 10 tests, 10 checks

M3: monthly report, balance check, /wallet check, pinned Mochi message, ignoring pin notices.

- everyday budget starts mochi and pins status
- nightly scoring and verdict
- bills and planned dont feed mochi
- streak milestone on the card
- monthly report and balance check
- report command and check command
- typing text while check is waiting logs normally
- mochi command without budget explains
- pin notices and bots are ignored
- choose wallets for the balance check

## `test_persona.py` · 8 tests, 8 checks

The AI persona reply: separate message, price history facts, chat back, fallback, undo totals.

- reaction is its own message with the facts
- unusual price is compared with history
- vnd entry shows both currencies
- budget alert stays on card and reaches the persona
- claude down falls back to a written line
- answering the persona continues the chat
- plain persona never chats
- undo takes the entry off today and the allowance

## `test_photos.py` · 21 tests, 26 checks

Photo logging: card notifications, duplicates, money to people or yourself, privacy scrubbing.

- card notification logs straight away
- no date uses today and says so
- several payments in one picture
- duplicate asks and log anyway
- duplicate skip
- guessed date widens the duplicate check
- date filled in as today still finds the earlier entry
- money to a person asks what it was
- money to my own name becomes a move
- huge transfer to a person is never logged without asking
- big business payment checks first
- wallet guess must match the currency
- nothing to log
- unreadable answer from the model
- screenshot sent as a file
- other files are not read
- caption reaches the model
- numbers are scrubbed before saving or logging
- same person ignores accents case and order
- scrub (6 examples)
- tidy reads vietnamese amounts and drops future dates

## `test_voice.py` · 13 tests, 34 checks

Voice notes and spoken amounts (“50 nghìn”, “1 triệu 2”, “12 dollars”), bank names.

- vietnamese voice note is logged with the transcript
- english voice with two items
- a question shows what was heard
- voice transfer
- bank name after a comma picks the wallet
- too long
- silence
- groq down
- audio file and no key
- reply to the persona by voice
- normalize spoken (17 examples)
- spoken amounts parse right (6 examples)
- typed nghin is vnd too

## `test_subscriptions.py` · 23 tests, 23 checks

Subscriptions: detection rules, daily job, buttons, reminders, price changes, quarterly check.

- key ignores month words numbers and accents
- add interval handles month ends
- monthly with small price wobble
- two charges are enough but must be recent
- quarterly like vieon
- not subscriptions
- next renewal is never in the past and doesnt drift
- an extra charge in between doesnt hide it
- weekly with three
- per month
- daily job suggests and yes tracks it
- waits until 10am and no means never again
- bills that already log themselves are not suggested
- a recurring bill only hides the same thing
- re adding by hand changes the interval
- a failed send does not resend everything next tick
- at most two suggestions a day
- reminder two days before renewal once
- price change is flagged and dates move on
- missed renewal rolls forward quietly
- still using it every three months
- add and stop by hand
- buttons belong to their owner

## `test_insights.py` · 14 tests, 14 checks

Insights: pattern rules, entry vs question, questions answered from the ledger, Sunday recap.

- weekend vs weekday
- rising three weeks in a row
- budget pace
- question or entry
- how much on grab in august
- accents dont matter and breakdowns
- nothing matched and bad answers
- reply to the persona is chat not a lookup
- future dates are clamped
- lunch with a question mark is still an entry
- sunday recap
- recap on demand and plain persona
- monthly report shows patterns
- upcoming renewals in the recap
