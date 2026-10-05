"""See how each persona reacts to a few sample entries, with the real Claude.

    python scripts/try_persona.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import anthropic  # noqa: E402

from meow import persona_llm  # noqa: E402
from meow.config import get_settings  # noqa: E402

s = get_settings()
c = anthropic.Anthropic(api_key=s.anthropic_api_key)
cases = [
 ("cat", 2, "en", '''Now: Monday 05 Oct, 14:09 (their local time).

They just logged:
- Expense: "lunch", S$45.00, category Food & Drinks, wallet DBS
  Their usual price for this: S$7.10 (logged 38 times in the past year, range S$3.50 to S$16.00).
  Their typical Food & Drinks entry: S$6.80.
Total spent today: S$46.65.
Mochi's daily bowl (everyday allowance): S$19.35; over by S$27.30 today. Mochi's weight 50/100.
Rent, bills, subscriptions and study don't count against the bowl.'''),
 ("mom", 3, "mix", '''Now: Monday 05 Oct, 01:34 (their local time).

They just logged:
- Expense: "game", 33,500₫ (about S$1.65), category Entertainment, wallet VCB
  First time they've logged this exact item in the past year.
  Their typical Entertainment entry: S$12.00.
Total spent today: S$1.65.
Mochi's daily bowl (everyday allowance): S$19.35; S$17.70 left today. Mochi's weight 50/100.'''),
 ("monk", 1, "en", '''Now: Monday 05 Oct, 08:40 (their local time).

They just logged:
- Expense: "kopi", S$1.80, category Food & Drinks, wallet DBS
  Their usual price for this: S$1.80 (logged 120 times in the past year, range S$1.40 to S$2.20).
Earlier today: kopi S$1.80; kopi S$1.80
Total spent today: S$5.40.
Mochi's daily bowl (everyday allowance): S$19.35; S$13.95 left today. Mochi's weight 62/100.'''),
]
for p, r, l, f in cases:
    t, log = persona_llm.react(c, s.persona_model, persona_llm.build_system(p, r, l, "Tam"), f)
    print(p, r, l, log.latency_ms, "ms", log.input_tokens, log.output_tokens, "\n ", t or log.error, "\n")
