# 🐱 M.E.O.W. — My Expense & Outflow Watcher

A private Telegram bot that tracks money from **chat messages, voice notes and payment screenshots**,
in SGD and VND. Every entry gets a confirm card (OK / Undo / Category / Wallet) and a short reply from
your persona. Mochi the cat lives on your daily allowance, a streak rewards logging every day, and on
the 1st you get a monthly report and a wallet-by-wallet balance check.

**Status:** M1–M4 done: chat, voice and photo logging; personas; budgets; Mochi; streak; monthly report and
balance check; subscription detector; weekly recap, patterns and questions about your spending.

```
Telegram ──webhook──▶ Vercel (FastAPI, api/index.py) ──▶ meow/bot.py
                              │
     text ───────────────────▶│ spoken amounts → rules ─▶ (unsure?) ─▶ Claude Haiku (tool call) ─▶ Pydantic
     voice ─▶ Groq Whisper ──▶│   (same path as text)
     photo ─▶ Claude vision ─▶│ log clear payments · ask about duplicates, people, big amounts
                              │
                              ├─ ledger: Supabase Postgres (append-only, integer minor units, SGD amount per entry)
                              ├─ confirm card ─▶ Telegram
                              └─ persona reply (Claude, facts computed by code) ─▶ Telegram

Supabase pg_cron (every 15 min) ─▶ Vercel: exchange rates, recurring bills, reminders, Mochi, monthly report
```

**Principles**
- **The AI proposes, code decides.** Claude only turns text or pictures into structured data and writes the
  persona's words. Amounts, totals, budgets and exchange rates are plain, tested code.
- **Nothing is deleted.** Undo appends a reversal entry; corrections are recorded, not overwritten.
- **Safe to repeat.** Every Telegram update and every scheduled job runs at most once.

---

## Using the bot

### Logging
| You send | What happens |
|---|---|
| `pho 65k`, `grab 12.5 yesterday`, `coffee 6, lunch 14` | Rules read it instantly; Claude only when the rules aren't sure |
| `salary 4200 to DBS`, `+50 refund` | Income |
| `move 200 from DBS to Cash`, `withdraw 100 from DBS`, `move 500 from DBS to VP as 9.8tr` | Transfer between wallets (not spending) |
| A voice note: “ăn trưa 50 nghìn, Vietcombank” | Transcribed (🎙 shown on the card), then read like text |
| A receipt, bank-app screenshot, card notification or Shopee order | Read by Claude (📸 on the card); see [Photos](#photos) |
| `45` right after the evening reminder | That day's total |
| `#planned` in a message | Doesn't count against Mochi's bowl |
| “how much on Grab in August?”, “tháng này tiêu bao nhiêu cho ăn uống?” | A question: answered from your own entries (see [Questions](#questions)) |

### Amounts and currencies
- Plain numbers under 10,000 are **SGD** (`grab 12.5`); 10,000 or more are **VND**.
- `65k`, `65.000`, `1tr2` (1.2 million), `2m`, `đ` are **VND**. An explicit currency wins: `$5`, `5 usd`, `120000 vnd`.
- Spoken or written-out forms work too: “50 nghìn” = 50k, “1 triệu 2” = 1.2 million, “2 triệu rưỡi” = 2.5 million,
  “12 dollars 50 cents” = S$12.50, “5 US dollars” = US$5.
- Each currency goes to its default wallet (`/wallet default VCB`) unless you name one (`pho 65k VP`, `…, Vietcombank`).
  A new currency gets a `Cash XXX` wallet. Every entry also stores its SGD value at that day's rate.

### Commands
| Command | What it does |
|---|---|
| `/today`, `/month` | Today's entries; this month by category (in SGD) |
| `/balance`, `/setbalance DBS 2340.50` | Wallet balances; correct one (the gap is recorded as an adjustment) |
| `/wallet` | List wallets · `add GrabPay SGD ewallet` · `default Cash` · `check DBS VPBank VCB` (which wallets the monthly check covers) |
| `/budget` | `Food 400`, `total 1600`, `everyday 600` (Mochi's allowance), `Food off` |
| `/recurring` | Bills that log themselves: `add rent 800 on 1`, `stop 2` |
| `/rules` | Categories learned from your corrections; `forget grab` |
| `/persona`, `/roast`, `/language` | Sassy Cat, Asian Mom, Zen Monk or Plain; roast 0–3; EN, VI or mix |
| `/remind 21:30` | Evening check-in time (only sent if nothing was logged) |
| `/mochi`, `/streak` | How Mochi is doing; your logging streak |
| `/report`, `/report 2026-09` | Monthly report |
| `/check` | Balance check now |
| `/subscriptions` | Tracked subscriptions and monthly total · `scan` · `add Netflix 17.98 monthly` · `stop 2` |
| `/recap` | This week so far, with patterns (also sent Sundays at 20:00) |
| `/myname Trinh Minh Tam` | Your name as banks print it (spots transfers to yourself in screenshots) |
| `/undo`, `/settings`, `/help` | |

### Features by milestone
| | Feature | How it works |
|---|---|---|
| M1 | Chat logging | Rules first, Claude Haiku fallback with forced tool calling; confirm card; undo |
| M2 | Exchange rates | Daily rates (incl. VND) from open.er-api.com, stored per entry |
| M2 | Evening check-in | At your reminder time, only if nothing was logged. Buttons: no-spend day, in 1h, skip, pause |
| M2 | Learning | Change a shop's category twice and it's remembered; your corrections beat imported history |
| M2 | Budgets | Per category and total; alerts on the card at 80% and 100% |
| M2 | Personas | The persona replies to each entry in its own message (Claude). Code gives it the facts: your usual price for that item, today's spending, your allowance, budget alerts. It questions odd prices, and you can answer it within 30 minutes. If Claude is down, a pre-written line is used. Plain = no replies |
| M2.1 | Your history | Money Manager import (1 year), English categories incl. Groceries, recurring bills |
| M3 | Mochi 🐱 | Daily bowl = everyday budget ÷ days in month. Scored after midnight: no-spend +3, ≤50% +2, ≤100% +1, ≤150% −1, more −3, silent day −2. At 0 she goes to grandma's; 3 good days bring her back. Rent, bills, phone, subscriptions, study, recurring entries and `#planned` don't count |
| M3 | Streak 🔥 | A day counts if you log or mark no-spend. One freeze a week (after 5 of 7 logged days). Milestones 3, 7, 14, 30, 100 with 🔔🧣👑 |
| M3 | Monthly report | 1st at 09:00: totals vs last month, categories with trends, top 3, budget scorecard, fun facts, one tip |
| M3 | Balance check | After the report, wallet by wallet: ✅ matches / ✏️ different (gap becomes an adjustment) / skip |
| M4 | Photos | See below |
| M4 | Voice | Groq Whisper (`whisper-large-v3`), English, Vietnamese or mixed, up to 2 minutes. Your wallet names are given as a spelling hint |
| M4 | Subscriptions | See below |
| M4 | Weekly recap & patterns | Sundays 20:00: the week vs last week and your 4-week average, top categories, days logged, Mochi's good days, patterns, renewals coming up, and one line from your persona. `/recap` any time |
| M4 | Questions | See below |

### Photos
Claude reads the amount actually paid (after vouchers), the date, the bank and who was paid.
Clear payments (receipts, card notifications, bills to businesses) are **logged straight away**.
These **ask first** with buttons instead:

| Check | Why | Buttons |
|---|---|---|
| Already logged | Same amount and currency within a day; within a week if the photo has no date or says today | ➕ Log anyway · 🚫 Skip |
| Sent to a person | Could be spending, a move to your own account, or nothing to log | 💸 Spending (pick category) · 🔁 My own account · 🚫 Don't log |
| Sent to your own name | Probably moving money between your accounts (`/myname`) | 🔁 Between my accounts · 💸 Spending · 🚫 Don't log |
| Big amount | Over `PHOTO_CONFIRM_ABOVE` (S$500) | ✅ Log it · ✏️ Category · 🚫 Don't log |

### Subscriptions
Every day from 10:00 the bot looks for **the same thing at a similar price (±5%) at a regular interval,
at least twice**: weekly (3 times), monthly, quarterly or yearly, with the latest charge still recent.
Month words and numbers are ignored, so “Tiền điện thoại tháng 9” and “… tháng 10” match. Food, groceries and
transport are habits, not subscriptions, and bills that already log themselves (`/recurring`) are skipped.

| When | What you get |
|---|---|
| Something repeats | “Is Claude S$30.84 a subscription?” ✅ Yes, track it · ❌ No (never asked again). At most 2 a day |
| 2 days before a renewal | 🔔 “Netflix S$17.98 renews in 2 days. Cancel before then if you don't need it.” |
| The price changes | ⚠️ “Spotify went up from S$10.98 to S$11.98.” |
| Every 3 months | 🤔 “Still using Gym? S$120 a month is about S$1,440 a year.” 👍 Keep · ✂️ I cancelled it |

### Patterns
Found by plain statistics (Claude only writes the persona line):
- **Weekends vs weekdays**: “Weekends cost you 40% more a day”, over the last 4 weeks.
- **Rising categories**: up three weeks in a row.
- **Budget pace**: “At this pace you'll hit your Shopping budget on the 22nd” (and for Mochi's everyday budget).

The best patterns also appear in the monthly report.

### Questions
Ask in English, Vietnamese or both: “how much on Grab in August?”, “show me this month by category”,
“bao nhiêu tiền phở tháng 9”. Claude turns the question into a filter (words, categories, wallets, dates,
total / list / by category / by month / by wallet); **the bot's code finds the entries and adds them up**.
The reply shows the filter it used, so a misunderstanding is easy to spot. Entries like “lunch 12?”,
“total 45” or “mình đã mua sách 200k” are still logged, and right after the persona speaks, “haha really?”
is chat, not a lookup.

**Privacy:** photos are not stored. Account, card and phone numbers and reference codes are removed
before anything is saved or recorded. Sending a screenshot as a file (📎 → File) gives a sharper read.

---

## Project layout

| Path | What it is |
|---|---|
| `api/index.py` | Vercel entry point: Telegram webhook and the scheduler tick (any path; secrets decide) |
| `meow/bot.py` | Messages, commands, buttons and the scheduled tick |
| `meow/parser_rules.py`, `meow/parser.py` | Rule parser for simple messages; rules-then-Claude entry point |
| `meow/parser_llm.py` | Claude fallback with forced tool calling |
| `meow/money.py` | Amount reading (`65k`, `1tr2`, `65.000`, “50 nghìn”), minor units, formatting |
| `meow/transfers.py` | Wallet transfers |
| `meow/fx.py` | Daily exchange rates and SGD conversion |
| `meow/persona_llm.py` | The persona's reply to each entry (Claude) |
| `meow/personas.py` | Pre-written lines: reminders, verdicts, report, fallback |
| `meow/vision.py`, `meow/photo_flow.py` | Reading screenshots; logging or asking about each payment |
| `meow/speech.py` | Voice notes to text (Groq Whisper) |
| `meow/budgets.py`, `meow/mochi.py`, `meow/streak.py`, `meow/report.py` | Budgets, Mochi, streak, monthly report |
| `meow/importer.py` | Money Manager import |
| `meow/db.py` | All SQL |
| `meow/cards.py` | Confirm card text and buttons |
| `meow/config.py`, `meow/runtime.py` | Settings from environment variables; wiring for Vercel |
| `meow/subscriptions.py`, `meow/subs_flow.py` | Spotting subscriptions; tracking, reminders, buttons, `/subscriptions` |
| `meow/insights.py`, `meow/ask.py` | Patterns and the weekly recap; answering questions |
| `supabase/migrations/` | Database schema, applied in order (0001 to 0009 are on your project) |
| `scripts/` | Local tools, see [Scripts](#scripts) |
| `tests/` | See [Testing](#testing) |

## Setup

### 1. Database URL
Supabase → your `meow-bot` project → **Connect** → **Transaction pooler** (port **6543**).
Copy the URI and put your database password in it. URL-encode special characters (`@` → `%40`).

### 2. Run it locally
```bash
python -m venv .venv
.venv\Scripts\activate            # Windows  (macOS/Linux: source .venv/bin/activate)
pip install -r requirements-dev.txt
copy .env.example .env            # then fill in the values (macOS/Linux: cp)
python -m pytest -q               # see Testing
python scripts/poll.py --force    # run the bot from your laptop (removes the webhook)
```
Send `/start`, then try `pho 65k`, `coffee 6, lunch 14`, a voice note or a screenshot.
When done, point Telegram back at Vercel: `python scripts/set_webhook.py https://meow-finance.vercel.app`.

### 3. Deploy on Vercel
Pushing to `main` on GitHub deploys automatically.
1. First time: Vercel → **Add New → Project** → import `meow-bot`. Framework preset: **Other**. No build command.
2. **Environment Variables**: every key from `.env.example` with your real values. Redeploy after changing them.
3. `https://meow-finance.vercel.app/api/health` should say `{"ok":true}`.
4. `python scripts/set_webhook.py https://meow-finance.vercel.app` sets the webhook and the command menu.
   Run it again whenever commands change. If the bot is silent: Vercel → Logs, and re-run it to see Telegram's last error.

**Database changes go first.** Apply a new migration in Supabase *before* pushing code that needs it.

### The scheduler
Supabase `pg_cron` calls the Vercel app every 15 minutes with an `X-Cron-Secret` header (stored in Supabase
Vault as `meow_cron_secret`; the same value is `CRON_SECRET` in Vercel). Each tick: exchange rates, entries
saved without a rate, due recurring bills, Mochi's score after midnight, the monthly report on the 1st,
the subscription check (daily from 10:00), the weekly recap (Sundays from 20:00) and evening check-ins.
Every step is safe to repeat: daily and weekly jobs are claimed once in `job_runs`.

### Settings (environment variables)
| Variable | Default | What it does |
|---|---|---|
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET` | | From BotFather; any long random string |
| `DATABASE_URL` | | Supabase transaction pooler URI |
| `ANTHROPIC_API_KEY` | | Claude |
| `GROQ_API_KEY` | | Voice notes (no key = voice off) |
| `ALLOWED_USER_IDS` | | Your Telegram ID; comma-separate to add testers |
| `CRON_SECRET` | | Shared with the Supabase scheduler |
| `HOME_CURRENCY`, `DEFAULT_TIMEZONE` | `SGD`, `Asia/Singapore` | |
| `ANTHROPIC_MODEL`, `PERSONA_MODEL`, `VISION_MODEL` | `claude-haiku-4-5` | Models for parsing, persona replies, photos |
| `STT_MODEL` | `whisper-large-v3` | Groq speech model |
| `PHOTO_CONFIRM_ABOVE` | `500` | Photo entries at or above this (SGD) ask first |
| `VOICE_MAX_SECONDS` | `120` | Longest voice note |
| `BIG_EXPENSE` | `50` | “Big spend” line when the persona falls back to written lines |
| `LLM_DAILY_CALL_CAP` | `300` | AI calls per 24 hours (parsing, persona, photos, voice together) |
| `LLM_INPUT_PRICE_PER_MTOK`, `LLM_OUTPUT_PRICE_PER_MTOK`, `STT_PRICE_PER_HOUR` | `1.0`, `5.0`, `0.111` | For the cost log |

### Scripts
| Script | Use |
|---|---|
| `scripts/poll.py [--force]` | Run the bot locally without a public URL |
| `scripts/set_webhook.py URL` | Point Telegram at Vercel and refresh the command menu |
| `scripts/try_persona.py` | Real persona replies to sample entries |
| `scripts/try_photo.py private/*.jpg` | What Claude reads from your screenshots, and whether they're already logged. Saves nothing |
| `scripts/eval_parser.py` | Parser accuracy and cost on sample messages |
| `scripts/eval_history.py EXPORT.xlsx [--claude]` | Parser accuracy on your last 3 months |
| `scripts/import_moneymanager.py EXPORT.xlsx BACKUP.mmbackup --telegram-id ID [--dry-run]` | Import Money Manager history (re-running replaces the previous import) |

Keep personal files in `private/`. Git ignores it, plus `.xlsx`, `.csv`, `.mmbackup` and image files.

### Useful SQL (Supabase → SQL editor)
```sql
-- What the AI costs, by purpose
select purpose, count(*) calls, round(avg(latency_ms)) avg_ms, round(sum(cost_usd)::numeric, 4) usd,
       count(*) filter (where not ok) failures
from llm_calls where created_at > now() - interval '30 days' group by 1 order by 1;

-- Share of entries read by rules vs Claude vs photo/voice
select source, parser, count(distinct batch_id) from transactions where reverses_id is null group by 1, 2;
```

---

## Testing

**155 tests, 254 checks.** Some tests run once per example (e.g. 49 sample messages through the parser),
which is why there are more checks than tests. `tests/README.md` lists every test by name.

### Two kinds of test
| Kind | What it covers | Needs |
|---|---|---|
| **Unit tests** | Pure logic on its own: reading amounts and dates, the rule parser, spoken amounts, Mochi and streak rules, number scrubbing, name matching | Nothing; run anywhere |
| **End-to-end tests** | The whole bot, message in → database → reply out: cards, buttons, undo, budgets, reminders, reports, persona, photos, voice | A throwaway Postgres in `TEST_DATABASE_URL` |

End-to-end tests use **fakes** so they're fast, free and repeatable (`tests/support.py`):
- **Fake Telegram** records every message, edit and button answer instead of sending them.
- **Fake Claude** returns prepared answers (parsed entries, what a photo shows, a persona reply), or fails on purpose.
- **Fake Groq** returns a prepared transcript, or fails.
- **Fixed clock and exchange rates**: always 29 Sep 2026, 22:30 Singapore; 20,000 VND per SGD.

The `env` fixture (`tests/conftest.py`) rebuilds the database from `supabase/migrations/` for every test, so the
schema tested is the schema deployed. The real Claude and Groq are checked separately with the `try_*`
and `eval_*` scripts, and one test checks that every Claude call matches the real SDK's signature.

### What each file covers
| File | Kind | Covers | Tests (checks) |
|---|---|---|---|
| `test_parser.py` | Unit | Amounts (`65k`, `1tr2`, `65.000`), currencies, dates, several items per message, when Claude is used, bad Claude output | 12 (82) |
| `test_streak_mochi.py` | Unit | Streak, freezes, milestones; Mochi's scoring, grandma's, comeback | 8 |
| `test_bot_flow.py` | End-to-end | Core logging: card, undo, category/wallet buttons, strangers turned away, duplicate updates, balances, timezones | 14 |
| `test_m2.py` | Both | Exchange rates, evening check-in, learned categories, personas, budgets, transfers | 22 |
| `test_m21.py` | Both | Imported history, learned phrases, recurring bills, wallet nicknames, income guard | 10 |
| `test_m3.py` | End-to-end | Monthly report, balance check, `/wallet check`, pinned Mochi message, ignoring pin notices | 10 |
| `test_persona.py` | End-to-end | Persona in its own message, price history facts, answering it, fallback when Claude is down, undo totals | 8 |
| `test_photos.py` | Both | Card notifications, no date, several payments, duplicates, money to people or yourself, big amounts, files, privacy scrubbing | 21 (25) |
| `test_voice.py` | Both | Voice notes in English/Vietnamese, transfers by voice, bank names after a comma, Groq down, silence, spoken amounts | 13 (37) |
| `test_subscriptions.py` | Both | Detection rules (intervals, ±5%, stale, habits, month ends); daily job, buttons, reminders, price changes, quarterly check, `/subscriptions`, failed sends | 23 |
| `test_insights.py` | Both | Weekend/rising/budget-pace rules; entry vs question; questions answered from the ledger; Sunday recap; patterns in the report | 14 |

### Running them
```bash
python -m pytest -q                         # everything that can run here
python -m pytest -q tests/test_photos.py    # one file
python -m pytest -q -k duplicate            # tests with "duplicate" in the name
python -m pytest -v                         # print each test as it runs
```
Without `TEST_DATABASE_URL` you'll see about **135 passed, 119 skipped**: the end-to-end tests skip.
To run all 254, install Postgres 16, create an empty database, and set for example
`TEST_DATABASE_URL=postgresql://postgres@localhost:5432/meow_test` (the tests wipe it; never point it at Supabase).

In VS Code: the **Testing** panel (flask icon) runs single tests with a click. Choose **pytest** and the `tests` folder.

### Adding a test
Each bug found in real use gets a test with the real input, e.g. Whisper's “Game 33 nghìn, Vietcombank.”
or a Shopee screenshot read with today's date. Copy a nearby test, use `env.say("…")` to send text,
`env.press(button, message_id)` to tap a button, and check the reply or the database.
