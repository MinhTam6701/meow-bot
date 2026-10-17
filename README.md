# 🐱 M.E.O.W. — My Expense & Outflow Watcher

**A private Telegram money tracker you can text, talk to or send screenshots, in English, Vietnamese or both.**
It keeps an exact ledger across Singapore and Vietnam accounts, and a cat called Mochi gets fatter or thinner
with your everyday spending.

| | |
|---|---|
| **Track** | Projects for Daily Life |
| **Deployed** | [meow-finance.vercel.app](https://meow-finance.vercel.app) (Telegram webhook, scheduler and the charts Mini App; the bot itself answers only approved Telegram accounts) |
| **Stack** | Python · FastAPI on Vercel · Supabase Postgres · Telegram Bot API + Mini App · Claude Haiku 4.5 · Groq Whisper |
| **Tests** | 211 tests, 319 checks (`python -m pytest -q`) |

---

## Problem statement

**Tracking money is a chore, so most people stop.** Finance apps want you to open them, pick a category from a
menu and type an amount into a form, several times a day. The habit dies within weeks, and the record that's
left is too patchy to say where the money went.

It's worse when your life runs in **two currencies**. A Vietnamese professional in Singapore pays in SGD,
sends and spends VND at home, and moves money between banks in both countries. Amounts like `65k`, `1tr2`
or Vietnamese spoken numbers don't fit English-only apps, exchange rates turn every total into guesswork, and bank
screenshots in two languages pile up unlogged.

**Who has it:** young professionals and students living across two countries (here: Singapore and Vietnam)
who want to know where their money goes, without bookkeeping.

**Why it matters:** you can't change a spending habit you can't see. A tracker that takes five seconds per
entry, in the app you already have open all day, is one you'll still use in month three.

## Solution overview

M.E.O.W. lives in **Telegram**, so logging is just texting: `pho 65k`, `grab 12.5 yesterday`, a voice note in English or
Vietnamese ("lunch, 50 thousand dong, Vietcombank") or a screenshot of a bank transfer. Simple messages are read instantly by
rules; only unclear ones go to Claude. Every entry comes back as a card you can undo or correct in one tap,
and corrections teach it your categories.

Behind the chat is a proper ledger: integer minor units, an SGD value for every entry at that day's rate,
append-only (undo adds a reversal, nothing is deleted). On top of it:

- **Mochi the cat** turns your everyday budget into a daily food bowl. Spend less, she gets chonky; overspend
  or forget to log, she gets thin and eventually moves to grandma's.
- **Reports as pictures:** `/month`, `/recap` and `/report` arrive as a pie chart with fun facts, a daily
  pace ("S$212 left: about S$14 a day for 15 days") and one AI suggestion. A **📊 Mini App** inside Telegram
  lets you browse any week or month.
- **A persona** (Sassy Cat, Asian Mom, Zen Monk) reacts to each entry using your own price history, and
  questions odd prices.
- **It does the chores itself:** bills log themselves, subscriptions are spotted from your history, renewals
  are flagged, and on the 1st it walks you through matching each wallet to your bank app.

**How it fits together**

```
Telegram ──webhook──▶ Vercel (FastAPI, api/index.py) ──▶ meow/bot.py
                              │
     text ───────────────────▶│ spoken amounts → rules ─▶ (unsure?) ─▶ Claude Haiku (tool call) ─▶ Pydantic
     voice ─▶ Groq Whisper ──▶│   (same path as text)
     photo ─▶ Claude vision ─▶│ log clear payments · ask about duplicates, people, big amounts
                              │
                              ├─ ledger: Supabase Postgres (append-only, integer minor units, SGD amount per entry)
                              ├─ confirm card ─▶ Telegram
                              ├─ persona reply (Claude, facts computed by code) ─▶ Telegram
                              └─ reports: report_data.py ─▶ pie picture (Pillow) + 📊 button ─▶ Telegram
Mini App (GET ?view=charts) ─▶ page ─▶ POST + Telegram signature ─▶ report_data.py ─▶ JSON (your numbers only)

Supabase pg_cron (every 15 min) ─▶ Vercel: exchange rates, recurring bills, reminders, Mochi, reports, recap
```

## Key features

| Feature | What it does |
|---|---|
| **Chat logging** | `pho 65k`, `coffee 6, lunch 14`, `salary 4200 to DBS`. SGD/VND detection (`65k`, `1tr2`, `30tr160k`), dates, wallets, several items per message |
| **Voice notes** | English, Vietnamese or mixed, up to 2 minutes; spoken amounts ("1.2 million" said in Vietnamese, "12 dollars 50") are normalised before parsing |
| **Photos & screenshots** | Receipts, bank apps, card notifications, Shopee orders. Clear payments are logged; duplicates, money to people, transfers to yourself and amounts ≥ S$500 are asked about first |
| **Transfers** | `move 500 from DBS to VP as 9.8tr`: cross-currency moves with a typo guard against today's rate |
| **Learning** | Correct a shop's category twice and it's remembered |
| **Budgets** | Per category, total, and an *everyday* budget (no rent/bills/#planned) with 80% / 100% alerts |
| **Mochi 🐱 & streak 🔥** | Daily bowl, nightly verdict with her picture, a live pinned status line, streak with freezes and milestones |
| **Reports & charts 📊** | Pie picture + caption (fun facts, pace tip, AI suggestion) and a Telegram Mini App: weeks/months, savings rate, tap a slice to see its entries |
| **Questions** | "how much on Grab in August?" — the AI writes a filter, code finds and adds up the entries |
| **Subscriptions & bills** | Detects repeating charges, renewal reminders 2 days before, price-change alerts, "still using it?" every 3 months |
| **Monthly balance check** | On the 1st, wallet by wallet: ✅ matches / ✏️ different (the gap is recorded as an adjustment) |
| **Personas** | Three characters + Plain, roast level 0–3, English / Vietnamese / mix |

Full usage: [Using the bot](#using-the-bot) below, and the user guide.

## AI integration

**The rule that shapes everything: the AI reads and writes words; code does every number.** Claude never
adds, converts or compares money. It turns messy input into structured data that code validates, and writes
short text from facts code computed.

| Where | Model | What the AI does | What code does around it |
|---|---|---|---|
| **Parsing text** (`parser_llm.py`) | Claude Haiku 4.5, forced tool call | Only for messages the rules can't read (~27% in real use): returns entries (amount, currency, type, category, date) | Rules run first; output is validated by Pydantic against your real categories/wallets; your learned phrases override its category; bad output becomes a question, never a crash |
| **Your history as context** | same | Gets up to a handful of your similar past entries as examples | Picks them from your ledger by phrase match |
| **Voice notes** (`speech.py`) | Groq `whisper-large-v3` | Speech → text, with your wallet names as a spelling hint | Spoken amounts normalised (Vietnamese "50 thousand" → 50k), then the same parser as text |
| **Photos** (`vision.py`, `photo_flow.py`) | Claude Haiku 4.5 vision, forced tool call | Reads amount paid, date, bank, counterparty, category | Scrubs account/card/phone numbers; checks duplicates (±1 day, ±7 if undated), your own name, people vs businesses, big amounts → asks before saving |
| **Transfers** (`transfers.py`) | Claude Haiku 4.5, forced tool call | Reads wording the rules miss | Rejects received amounts far from today's exchange rate |
| **Questions** (`ask.py`) | Claude Haiku 4.5, forced tool call | Turns a question into a filter (words, categories, wallets, dates, grouping) | Runs the query and does the sums; shows the filter so mistakes are visible |
| **Persona replies** (`persona_llm.py`) | Claude Haiku 4.5 | 1–2 in-character sentences after each entry | Supplies the facts: your usual price for the item, today's total, allowance, budget alerts |
| **Report suggestion** | Claude Haiku 4.5 | One practical suggestion under weekly/monthly reports | Supplies the numbers and entries; strict rules (below); "NONE" when nothing stands out |

Real usage so far: 99 AI calls for about **US$0.12** in total, 1 failed parse (handled as a question);
30 of 41 typed or spoken messages were read by rules alone.

## External tools & datasets

| Tool / API | Used for |
|---|---|
| [Telegram Bot API](https://core.telegram.org/bots/api) + [Mini Apps](https://core.telegram.org/bots/webapps) | The chat interface; the 📊 charts page (signed `initData`) |
| [Anthropic Claude](https://docs.claude.com) (`claude-haiku-4-5`) | Parsing fallback, photo reading, transfers, questions, persona, report suggestion |
| [Groq](https://console.groq.com) (`whisper-large-v3`) | Voice-note transcription |
| [open.er-api.com](https://open.er-api.com) | Daily exchange rates (incl. VND), stored per entry |
| [Supabase](https://supabase.com) Postgres + `pg_cron`, `pg_net`, Vault | Database; the 15-minute scheduler and its secrets |
| [Vercel](https://vercel.com) (Python, FastAPI) | Webhook, scheduler endpoint, Mini App page and data |
| Python: `fastapi`, `httpx`, `anthropic`, `psycopg`, `pydantic`, `Pillow` | App, HTTP, AI SDK, database, validation, pie pictures |
| [Inter](https://rsms.me/inter/) font (SIL OFL, in `assets/fonts/`) | Text in the pie pictures |
| Mochi artwork (`assets/mochi/`) | Own art, 7 still pictures |

**Datasets:** no public dataset. The author's own ~1 year of [Money Manager](https://realbyteapps.com) history
(1,162 entries) was imported with `scripts/import_moneymanager.py` to give the parser real examples and the
reports real history. Personal exports are git-ignored and never committed. Test data is synthetic
(`tests/parser_cases.py`, fixtures in `tests/`).

## Installation & setup

You need Python 3.11+, a Telegram account, a free Supabase project, an Anthropic API key, and (optional)
a Groq key for voice. Allow about 30 minutes.

**1. Create the bot.** In Telegram, message [@BotFather](https://t.me/BotFather) → `/newbot` → copy the token.
Get your own Telegram user ID from [@userinfobot](https://t.me/userinfobot).

**2. Create the database.** New Supabase project → **SQL editor** → run every file in
`supabase/migrations/` in order (`0001` … `0012`). Then **Connect → Transaction pooler** (port 6543) and copy
the URI with your password (URL-encode special characters: `@` → `%40`).

**3. Configure.**
```bash
git clone https://github.com/MinhTam6701/meow-bot && cd meow-bot
python -m venv .venv
source .venv/bin/activate              # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env                   # Windows: copy .env.example .env
```
Fill in `.env`: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET` (any long random string), `DATABASE_URL`,
`ANTHROPIC_API_KEY`, `ALLOWED_USER_IDS` (your user ID), `CRON_SECRET` (another random string) and optionally
`GROQ_API_KEY`. All settings are listed under [Settings](#settings-environment-variables).

**4. Run it locally** (no public URL needed):
```bash
python -m pytest -q                    # unit tests; see Testing for the end-to-end ones
python scripts/poll.py --force         # long-polls Telegram (removes any webhook)
```
Message your bot `/start`, then try `pho 65k`, `coffee 6, lunch 14`, a voice note or a screenshot.

**5. Deploy (optional).** Vercel → **Add New → Project** → import the repo, framework **Other**, no build
command → add every `.env` key under **Environment Variables** → deploy. Check `https://<your-app>.vercel.app/api/health`
says `{"ok":true}`, then point Telegram at it (also sets the command menu and the 📊 Charts button):
```bash
python scripts/set_webhook.py https://<your-app>.vercel.app
```

**6. Turn on the scheduler** (reminders, bills, Mochi, reports). In Supabase SQL editor, store the URL and
secret in Vault and schedule the 15-minute tick:
```sql
select vault.create_secret('https://<your-app>.vercel.app/api/tick', 'meow_tick_url');
select vault.create_secret('<your CRON_SECRET>', 'meow_cron_secret');
select cron.schedule('meow-tick', '*/15 * * * *', $$
  select net.http_post(
    url := (select decrypted_secret from vault.decrypted_secrets where name = 'meow_tick_url'),
    headers := jsonb_build_object('Content-Type', 'application/json',
      'X-Cron-Secret', (select decrypted_secret from vault.decrypted_secrets where name = 'meow_cron_secret')),
    body := '{"job": "tick"}'::jsonb, timeout_milliseconds := 25000);
$$);
```
(Enable the `pg_cron` and `pg_net` extensions first under **Database → Extensions**.)

## AI safety & limitations

### Risks and the guardrails against them

| Risk | Guardrail |
|---|---|
| **The AI gets a number wrong** (wrong total, bad conversion, invented amount) | The AI never does arithmetic. Totals, rates, budgets, pace and reports are plain code with tests. AI output is only ever parsed data or words |
| **The AI misreads a message** | Rules handle common phrasing first (no AI). AI output goes through a forced tool schema + Pydantic validation against your real categories and wallets; anything invalid becomes a clarifying question. Every entry is shown on a card with one-tap Undo / Category / Wallet |
| **Typos in transfers** (`9.8tr` vs `98tr`) | Received amounts far from today's exchange rate are refused |
| **A screenshot logs the wrong thing** | Duplicates, money sent to people, transfers to your own name and amounts ≥ S$500 are never logged without asking |
| **Leaking personal data** | Photos are not stored. Account, card and phone numbers and reference codes are scrubbed *before* anything is saved or logged (the AI's raw output included). Personal exports are git-ignored. Only the AI-relevant text is sent to Claude/Groq |
| **Strangers using the bot or seeing data** | Only `ALLOWED_USER_IDS` are served. The webhook and scheduler need secrets. The Mini App checks Telegram's HMAC signature (bot token, 24 h expiry) and returns only the signer's own numbers; nothing private is in its URL |
| **Prompt injection** (text in a screenshot or message that tries to steer the model) | Every reading call is a forced tool call with a fixed schema, so the model can only return entries/filters, which code validates. It can't run actions, see other users' data or change settings |
| **Harmful or useless advice** | The persona is told to use only given numbers, never invent facts, never give investment advice, never lecture. The report suggestion may **never suggest cutting needs** (meals, groceries, rent, bills, phone, health, study, getting to work), must point at something in the data, and answers `NONE` when nothing stands out. It's off for the Plain persona |
| **AI outage or cost runaway** | Pre-written fallback lines when Claude is down; failures are logged, never shown as errors mid-entry. A cap of 300 AI calls per user per 24 h. Every call is logged with tokens, cost, latency and success (`llm_calls`) |
| **Double-counting** (Telegram retries, overlapping scheduler runs) | Every update and every daily/weekly job runs at most once (`processed_updates`, `job_runs`); the ledger is append-only, so history always adds up |

### Testing

- **211 tests, 319 checks** (`tests/`). End-to-end tests run the whole bot against a real Postgres with a
  fake Telegram and a fake Claude that can return bad output or fail on purpose: malformed AI answers,
  Claude down, Groq down, silence, duplicate updates, strangers, tampered or expired Mini App signatures,
  and privacy scrubbing (`test_photos.py`).
- **Regression rule:** every bug found in real use becomes a test with the real input first (e.g. a
  Whisper transcript with a Vietnamese amount and the bank name after a comma, a Shopee screenshot read with today's date, `30tr160k`).
- **Accuracy:** `scripts/eval_parser.py` runs 52 sample messages through rules + Claude; the rules alone get
  all 49 they claim, and 3 are left for Claude. `scripts/eval_history.py` replays your last 3 months.
  `scripts/try_photo.py` shows what Claude reads from real screenshots without saving anything.

### Limitations

- **Single-user by design.** Home currency (SGD) and timezone (Singapore) are fixed; there's no in-app way
  to delete your data. Opening it to the public would need those, plus a privacy notice and per-person cost limits.
- **Text and photos go to Anthropic, voice to Groq** for reading. Nothing is stored there by this app, but
  the data does leave the server.
- **Readings can still be wrong** (unusual receipts, noisy voice notes, Vietnamese bank apps it hasn't
  seen). The card makes every entry visible and one tap to fix, and big or unusual ones are confirmed.
- **Exchange rates are daily** from a free API, so SGD values of VND entries are approximate within a day.
- **The AI suggestion is advice from a small model,** based only on your own entries. It's not financial advice.
- **Charts page needs Telegram**: it can't be opened in a normal browser (by design, for privacy).

---

# Reference

## Using the bot

**One rule for "everyday spending"** (`db.EVERYDAY`): expenses outside the bill categories (Housing,
Phone, Subscriptions & Fees, Education), not logged automatically by a recurring bill, and not tagged
`#planned`. Mochi's bowl, the everyday budget, the pace tip, the pie, the biggest spend and the biggest
changes all use it, so they never disagree. Everything else counts as "bills".

**Principles**
- **The AI proposes, code decides.** Claude only turns text or pictures into structured data and writes the
  persona's words. Amounts, totals, budgets and exchange rates are plain, tested code.
- **Nothing is deleted.** Undo appends a reversal entry; corrections are recorded, not overwritten.
- **Safe to repeat.** Every Telegram update and every scheduled job runs at most once.

### Logging
| You send | What happens |
|---|---|
| `pho 65k`, `grab 12.5 yesterday`, `coffee 6, lunch 14` | Rules read it instantly; Claude only when the rules aren't sure |
| `salary 4200 to DBS`, `+50 refund` | Income |
| `move 200 from DBS to Cash`, `withdraw 100 from DBS`, `move 500 from DBS to VP as 9.8tr` | Transfer between wallets (not spending). Also `transfer vp to dbs 30tr160k to 1471.2`; wording the rules miss goes to Claude, and a received amount far from today's rate is refused as a likely typo. `withdraw` uses the cash wallet you already have in that currency, whatever its name |
| A voice note in English or Vietnamese: “lunch, 50 thousand dong, Vietcombank” | Transcribed (🎙 shown on the card), then read like text |
| A receipt, bank-app screenshot, card notification or Shopee order | Read by Claude (📸 on the card); see [Photos](#photos) |
| `45` right after the evening reminder | That day's total |
| `#planned` in a message or photo caption | Not everyday spending: kept out of Mochi's bowl, the everyday budget, the pace and the pie (shown with bills) |
| “how much on Grab in August?”, “how much on food this month?” (in English or Vietnamese) | A question: answered from your own entries (see [Questions](#questions)) |

### Amounts and currencies
- Plain numbers under 10,000 are **SGD** (`grab 12.5`); 10,000 or more are **VND**.
- `65k`, `65.000`, `1tr2` (Vietnamese shorthand for 1.2 million), `2m` and the dong sign are **VND**. An explicit currency wins: `$5`, `5 usd`, `120000 vnd`.
- Spoken or written-out forms work too, in English or Vietnamese: “50 thousand” = 50k, “one million two” = 1.2 million,
  “two and a half million” = 2.5 million, “12 dollars 50 cents” = S$12.50, “5 US dollars” = US$5.
- Each currency goes to its default wallet (`/wallet default VCB`) unless you name one (`pho 65k VP`, `…, Vietcombank`).
  A new currency gets a `Cash XXX` wallet. Every entry also stores its SGD value at that day's rate.

### Commands
| Command | What it does |
|---|---|
| `/today`, `/month` | Today's entries; this month as a pie picture with fun facts, the pace tip and the 📊 charts button |
| `/balance`, `/balance DBS`, "what is my DBS balance?", `/setbalance DBS 2340.50` | All balances, or one wallet with its latest entries; correct one (the gap is recorded as an adjustment) |
| `/wallet` | List wallets · `add GrabPay SGD ewallet` · `default Cash` · `check DBS VPBank VCB` (which wallets the monthly check covers) |
| `/budget` | `Food 400`, `total 1600`, `everyday 600` (Mochi's allowance), `Food off` |
| `/recurring` | Bills you need, logged automatically: `add rent 800 on 1`, `stop 2`; `sub 3` moves one to subscriptions, `bill 3` back |
| `/rules` | Categories learned from your corrections; `forget grab` |
| `/persona`, `/roast`, `/language` | Sassy Cat, Asian Mom, Zen Monk or Plain; roast 0–3; EN, VI or mix |
| `/remind 21:30` | Evening check-in time (only sent if nothing was logged) · `off` · `on` · `pause 7` |
| `/mochi`, `/streak` | How Mochi is doing; your logging streak |
| `/report`, `/report 2026-09`, `/report this` | Last month's (or that month's) report as a picture |
| `/check` | Balance check now |
| `/subscriptions` | Nice-to-haves (iQIYI, Claude…) and their monthly total, incl. ones that log themselves · `scan` · `add Netflix 17.98 monthly` · `stop 2` |
| `/recap` | This week so far as a picture (also sent Sundays at 20:00) |
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
| M3 | Mochi 🐱 | Daily bowl = everyday budget ÷ days in month (or your pace, if you tap "Set Mochi's bowl" under `/month`). Scored after midnight: no-spend +3, ≤50% +2, ≤100% +1, ≤150% −1, more −3, silent day −2. At 0 she goes to grandma's; 3 good days bring her back. Only everyday spending counts (see the rule above) |
| M4 | Mochi's pictures | Seven still pictures, sent only with `/mochi` and the nightly verdict (never pinned): skinny below 40, neutral 40–69, fat 70–89, king from 90; sleeping after a no-spend day, sad after a bad day, an empty cushion while she's at grandma's. Art in `assets/mochi/`; each picture is uploaded to Telegram once and reused (`media` table). Replace a file to change the art |
| M3 | Streak 🔥 | A day counts if you log or mark no-spend. One freeze a week (after 5 of 7 logged days). Milestones 3, 7, 14, 30, 100 with 🔔🧣👑 |
| M3 | Monthly report | 1st at 09:00, as a pie picture (see [Charts](#charts)); the text version (totals vs last month, categories with trends, top 3, budget scorecard, fun facts, the tip) only if the picture fails |
| M3 | Balance check | After the report, wallet by wallet: ✅ matches / ✏️ different (gap becomes an adjustment) / skip |
| M4 | Photos | See below |
| M4 | Voice | Groq Whisper (`whisper-large-v3`), English, Vietnamese or mixed, up to 2 minutes. Your wallet names are given as a spelling hint |
| M4 | Subscriptions | See below |
| M4 | Weekly recap & patterns | Sundays 20:00, as a pie picture with your persona's line, fun facts, the pace tip and an AI suggestion. Patterns are on the charts page. The 4-week average and "renewing this week" are only in the text version (sent if the picture fails); renewals still get their own reminder 2 days before. `/recap` any time |
| M4 | Questions | See below |
| M5 | Charts 📊 | See [Charts](#charts) |

### Charts
`/month`, `/report` and `/recap` (and the Sunday recap and the report on the 1st) arrive as one **pie picture**
with a **📊 See charts** button under it. The caption has:
- the persona's line (recap) or the report heading;
- **fun facts**: days logged, the biggest spend (bills left out), and the everyday category that rose most
  and the one that fell most, in money, vs the same days of the last period (moves under S$10 left out);
- **💡 the pace** (code): what's left of the everyday budget (a week gets budget × 7 ÷ days in the month)
  and what that is per day from tomorrow; a finished period says how it ended. On the current month a
  **🐱 Set Mochi's bowl to S$x/day** button makes that her bowl until the month ends (a new everyday budget
  resets it; `mochi_state.bowl_override_*`);
- **🤖 one suggestion** (AI, chat only, not with the Plain persona): Claude gets the numbers above and the
  entries behind them, and may not suggest cutting needs (meals, groceries, rent, bills, phone, health,
  study, getting to work). It answers NONE when nothing stands out. The Mini App shows only the code parts. The text version is only sent
when there's nothing to draw or the picture fails. The button opens a page inside Telegram (a Mini App) with:
- **Week / Month** tabs and ‹ › to go back to any week or month since your first entry;
- spent (vs the same days of the last period), income, net, and the **savings rate** (months only: salary
  arrives once a month, so a weekly rate swings wildly);
- a donut of everyday spending by category. Bills (rent, phone, study, fees) are one line under it, with a
  switch to put them in. Tap a category to see its entries;
- budgets, the biggest entries, fun facts, the tip and patterns.

The numbers come from `meow/report_data.py`, which uses the same queries as the text reports, and nothing
is stored for the page. Telegram signs who opened it (`initData`); the app checks that signature with the bot
token and `ALLOWED_USER_IDS`, so the page only ever shows the signer's own entries. Nothing private is in the URL.
The **📊 Charts** button beside the message box is set by `scripts/set_webhook.py`.

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
**Bills vs subscriptions.** Bills are what you need to keep life running (rent, phone, bank fees): `/recurring`.
Subscriptions are nice-to-haves you could live without (iQIYI, Claude): `/subscriptions`. Either kind can log
itself every month; only subscriptions get renewal reminders and the “still using it?” check, and stopping one
there also stops it logging. Move one with `/recurring sub 3` / `/recurring bill 3`.

Every day from 10:00 the bot looks for **the same thing at a similar price (±5%) at a regular interval,
at least twice**: weekly (3 times), monthly, quarterly or yearly, with the latest charge still recent.
Month words and numbers are ignored, so “Phone bill September” and “Phone bill October” match. Food, groceries and
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
Ask in English or Vietnamese: “how much on Grab in August?”, “show me this month by category”,
“how much on pho in September?”. Claude turns the question into a filter (words, categories, wallets, dates,
total / list / by category / by month / by wallet); **the bot's code finds the entries and adds them up**.
The reply shows the filter it used, so a misunderstanding is easy to spot. Entries like “lunch 12?”,
“total 45” or “I bought books 200k” are still logged, and right after the persona speaks, “haha really?”
is chat, not a lookup.

**Privacy:** photos are not stored. Account, card and phone numbers and reference codes are removed
before anything is saved or recorded. Sending a screenshot as a file (📎 → File) gives a sharper read.

---

## Project layout

| Path | What it is |
|---|---|
| `api/index.py` | Vercel entry point: Telegram webhook, scheduler tick, charts page and its data (any path; query and headers decide) |
| `meow/bot.py` | Messages, commands, buttons and the scheduled tick |
| `meow/parser_rules.py`, `meow/parser.py` | Rule parser for simple messages; rules-then-Claude entry point |
| `meow/parser_llm.py` | Claude fallback with forced tool calling |
| `meow/money.py` | Amount reading (`65k`, `1tr2`, `65.000`, spoken amounts), minor units, formatting |
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
| `meow/report_data.py`, `meow/report_picture.py` | A week or month as numbers; the pie picture (Pillow, Inter font in `assets/fonts/`) |
| `meow/webapp.py`, `meow/webapp/report.html` | The charts Mini App: signature check, data, page |
| `supabase/migrations/` | Database schema, applied in order (0001 to 0012 are on your project) |
| `scripts/` | Local tools, see [Scripts](#scripts) |
| `tests/` | See [Testing](#testing) |

## Running it

Setup is in [Installation & setup](#installation--setup) above; details here.

**Database changes go first.** Apply a new migration in Supabase *before* pushing code that needs it.
Pushing to `main` deploys to Vercel automatically.

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
| `PUBLIC_URL` | `https://meow-finance.vercel.app` | Where the charts page is served |
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
| `scripts/set_webhook.py URL` | Point Telegram at Vercel, refresh the command menu and the 📊 Charts button |
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

**211 tests, 319 checks.** Some tests run once per example (e.g. 49 sample messages through the parser),
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
| `test_photos.py` | Both | Card notifications, no date, several payments, duplicates, money to people or yourself, big amounts, files, privacy scrubbing, #planned captions | 22 (27) |
| `test_voice.py` | Both | Voice notes in English/Vietnamese, transfers by voice, bank names after a comma, Groq down, silence, spoken amounts | 13 (34) |
| `test_transfers.py` | Both | `30tr160k`, sent-then-received amounts, typos far from the rate, Claude fallback for transfers | 8 (17) |
| `test_balance.py` | Both | `/balance DBS`, “what is my DBS balance?”, other currencies, unknown wallets | 6 |
| `test_subscriptions.py` | Both | Detection rules (intervals, ±5%, stale, habits, month ends); daily job, buttons, reminders, price changes, quarterly check, `/subscriptions`, failed sends | 28 |
| `test_insights.py` | Both | Weekend/rising/budget-pace rules; entry vs question; questions answered from the ledger; Sunday recap; patterns in the report | 20 |
| `test_charts.py` | Both | Periods and pie slices, Telegram's signature, the data route, the picture, buttons on every report | 23 |
| `test_mochi_art.py` | Both | Which picture for her weight and day, upload once and reuse, grandma's, fallback to text, nightly verdict | 7 |

### Running them
```bash
python -m pytest -q                         # everything that can run here
python -m pytest -q tests/test_photos.py    # one file
python -m pytest -q -k duplicate            # tests with "duplicate" in the name
python -m pytest -v                         # print each test as it runs
```
Without `TEST_DATABASE_URL` you'll see about **148 passed, 171 skipped**: the end-to-end tests skip.
To run all 319, install Postgres 16, create an empty database, and set for example
`TEST_DATABASE_URL=postgresql://postgres@localhost:5432/meow_test` (the tests wipe it; never point it at Supabase).

In VS Code: the **Testing** panel (flask icon) runs single tests with a click. Choose **pytest** and the `tests` folder.

### Adding a test
Each bug found in real use gets a test with the real input, e.g. a Whisper transcript with a Vietnamese amount and the bank name after a comma
or a Shopee screenshot read with today's date. Copy a nearby test, use `env.say("…")` to send text,
`env.press(button, message_id)` to tap a button, and check the reply or the database.
