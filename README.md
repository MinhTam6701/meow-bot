# 🐱 M.E.O.W. — My Expense & Outflow Watcher

A Telegram bot that logs money from plain chat messages. **v1 (M1 proof of concept):**
chat logging (rules first, Claude Haiku when unsure), confirm card with OK / Undo /
Category / Wallet buttons, `/today`, `/month`, `/balance`, `/setbalance`, `/undo`.

```
Telegram ──webhook──▶ Vercel (FastAPI, api/index.py)
                         │
                         ├─ parser: rules ─▶ (unclear?) ─▶ Claude Haiku tool call ─▶ Pydantic
                         ├─ ledger: Supabase Postgres (append-only, integer minor units)
                         └─ confirm card back to Telegram
```

**Principle:** the LLM proposes, code decides. Claude only turns text into JSON;
all money maths is plain, tested code. Undo never deletes: it appends a reversal row.

## Project layout

| Path | What it is |
|---|---|
| `api/index.py` | Vercel entry point (`POST /api/telegram`, `GET /api/health`) |
| `meow/bot.py` | Messages, commands and button handling |
| `meow/parser_rules.py` | Rule parser for simple messages (`pho 65k`, `grab 12.5 yesterday`) |
| `meow/parser_llm.py` | Claude fallback with forced tool calling |
| `meow/money.py` | Amount reading (`65k`, `1tr2`, `65.000`), minor units, formatting |
| `meow/db.py` | All SQL |
| `meow/cards.py` | Confirm card text and buttons |
| `supabase/migrations/0001_init.sql` | Database schema (already applied to your project) |
| `scripts/poll.py` | Run the bot on your laptop, no public URL needed |
| `scripts/set_webhook.py` | Point Telegram at your Vercel URL |
| `scripts/eval_parser.py` | Measure parser accuracy and cost on the sample messages |
| `tests/` | Unit tests + end-to-end tests against a real Postgres |

## Currency rules

- Plain numbers under 10,000 are **SGD** (`grab 12.5`).
- `65k`, `65.000`, `1tr2`, `2m`, `đ`, or any plain number ≥ 10,000 are **VND**.
- An explicit currency wins: `$5`, `5 usd`, `120000 vnd`.
- SGD goes to **DBS**, VND to **VP**, unless you name a wallet. A new currency gets a `Cash XXX` wallet.

## Setup

### 1. Get your database URL
Supabase → your `meow-bot` project → **Connect** → **Transaction pooler** (port **6543**).
Copy the URI and put your database password in it. (Forgot it? Settings → Database → Reset password.)

### 2. Run it locally first
```bash
python -m venv .venv
.venv\Scripts\activate            # Windows  (macOS/Linux: source .venv/bin/activate)
pip install -r requirements-dev.txt
copy .env.example .env            # then fill in the values
python -m pytest                  # 58 unit tests; DB tests skip without TEST_DATABASE_URL
python scripts/eval_parser.py     # parser accuracy + cost with the real Claude
python scripts/poll.py            # now message your bot on Telegram
```
Send `/start`, then try `pho 65k`, `coffee 6, lunch 14`, `/setbalance DBS 2340.50`.

### 3. Push to GitHub
```bash
git remote add origin https://github.com/MinhTam6701/meow-bot.git
git push -u origin main
```

### 4. Deploy on Vercel
1. Vercel → **Add New → Project** → import `meow-bot`. Framework preset: **Other**. No build command.
2. **Environment Variables**: add every key from `.env.example` with your real values.
3. Deploy, then open `https://<your-app>.vercel.app/api/health` — it should say `{"ok":true}`.
4. From your laptop (stop `poll.py` first):
   ```bash
   python scripts/set_webhook.py https://<your-app>.vercel.app
   ```
5. Message the bot. If nothing happens: Vercel → Logs, and re-run `set_webhook.py` to see Telegram's last error.

To go back to local testing, run `python scripts/poll.py --force` (it removes the webhook), and
`set_webhook.py` again when done.

## Useful SQL (Supabase → SQL editor)

```sql
-- How cheap is parsing? (the M1 question)
select count(*) calls, round(avg(latency_ms)) avg_ms, sum(cost_usd) usd,
       sum(case when ok then 0 else 1 end) failures
from llm_calls;

-- Share of messages handled by rules vs the LLM
select parser, count(distinct batch_id) from transactions where reverses_id is null group by 1;
```

## Not in v1 (next milestones)
Transfers, budgets, FX conversion to SGD, personas, end-of-day reminder (via Supabase
pg_cron → a Vercel endpoint, since Vercel Hobby cron runs only once a day), merchant-rule
learning, receipts and voice, Mochi.
