# M.E.O.W. tests

Run everything from the project folder:

```bash
python -m pytest -q                         # unit tests only; database tests are skipped
python -m pytest -q tests/test_photos.py    # one file
python -m pytest -q -k duplicate            # tests whose name contains 'duplicate'
python -m pytest -v                         # list each test as it runs
```

The end-to-end tests need a throwaway Postgres in `TEST_DATABASE_URL` (they wipe it). Shared setup lives in
`conftest.py` (the `env` fixture: bot + fake Telegram + fake Claude + real database) and `support.py`.
A test with several cases (parametrized) runs once per case, which is why there are more cases than test functions.

**118 test functions, 217 cases in total.**

## `test_parser.py` · 12 tests, 82 cases

Text parser on its own: amounts, currencies, dates, several items per message (no database).

- rule cases (49 cases)
- hard cases fall back to llm (3 cases)
- rules do not guess (5 cases)
- amount tokens (13 cases)
- bad amount tokens (5 cases)
- minor units and format
- llm used only when rules fail
- llm entries are validated and cleaned
- llm question is passed through
- llm bad output becomes a question not a crash
- llm call matches real sdk signature
- llm api error is logged not raised

## `test_bot_flow.py` · 14 tests, 14 cases

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

## `test_m2.py` · 22 tests, 25 cases

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
- parse transfer (4 cases)
- parse transfer errors and non transfers
- transfers move money but are not spending
- cross currency transfer estimated and exact
- withdraw creates cash wallet
- wallet default and validation
- settings summary

## `test_m21.py` · 10 tests, 10 cases

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

## `test_m3.py` · 10 tests, 10 cases

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

## `test_streak_mochi.py` · 8 tests, 8 cases

Streak and Mochi rules on their own (pure functions, no database).

- streak counts back from today or yesterday
- freeze bridges one missed day after a good week
- freeze needs five of seven and one per week
- milestones and accessories
- bowl and results
- weight is capped and clamped
- grandmas house and return
- stages moods and status line

## `test_persona.py` · 8 tests, 8 cases

The AI persona reply: separate message, price history facts, chat back, fallback, undo totals.

- reaction is its own message with the facts
- unusual price is compared with history
- vnd entry shows both currencies
- budget alert stays on card and reaches the persona
- claude down falls back to a written line
- answering the persona continues the chat
- plain persona never chats
- undo takes the entry off today and the allowance

## `test_photos.py` · 21 tests, 26 cases

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
- scrub (6 cases)
- tidy reads vietnamese amounts and drops future dates

## `test_voice.py` · 13 tests, 34 cases

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
- normalize spoken (17 cases)
- spoken amounts parse right (6 cases)
- typed nghin is vnd too
