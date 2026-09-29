"""M1 question: how cheap and accurate is parsing?

Runs every sample in tests/parser_cases.py through the full parser (rules, then
Claude) and prints accuracy, how many needed the LLM, and the estimated cost.
Needs ANTHROPIC_API_KEY in .env. Doesn't touch the database or Telegram.

    python scripts/eval_parser.py
"""
import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import anthropic  # noqa: E402

from meow.config import get_settings  # noqa: E402
from meow.parser import parse_message  # noqa: E402
from meow.parser_llm import cost_usd  # noqa: E402
from tests.parser_cases import CASES, TODAY, make_context  # noqa: E402


def main() -> None:
    s = get_settings()
    client = anthropic.Anthropic(api_key=s.anthropic_api_key)
    ctx = make_context()
    passed, llm_used, total_cost, latencies = 0, 0, 0.0, []
    for text, expected, _via in CASES:
        logs = []
        result = parse_message(text, ctx, llm_client=client, model=s.anthropic_model, on_llm_call=logs.append)
        got = [(e.amount, e.currency, e.type, e.category, (TODAY - e.date).days, e.wallet) for e in result.entries]
        want = [(Decimal(a), c, t, cat, d, w) for a, c, t, cat, d, w in expected]
        ok = got == want
        passed += ok
        for call in logs:
            llm_used += 1
            total_cost += cost_usd(call, s.llm_input_price, s.llm_output_price) or 0
            latencies.append(call.latency_ms)
        mark = "✅" if ok else "❌"
        print(f"{mark} [{result.parser:4}] {text}")
        if not ok:
            print(f"      want {want}\n      got  {got} {result.question or ''}")
    n = len(CASES)
    print(f"\nAccuracy: {passed}/{n} ({passed * 100 // n}%)")
    print(f"LLM calls: {llm_used}/{n}  ·  cost ≈ US${total_cost:.4f}"
          + (f"  ·  ≈ US${total_cost / llm_used:.5f} per call  ·  median latency {sorted(latencies)[len(latencies) // 2]} ms" if llm_used else ""))


if __name__ == "__main__":
    main()
