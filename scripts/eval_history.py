"""Measure the parser on your own history (data stays on your machine, never committed).

Learns phrases from everything before the last N days, then parses the last N days the way
you would type them ("<description> <amount>") and compares with the category you chose.

    python scripts/eval_history.py EXPORT.xlsx              # rules only, free
    python scripts/eval_history.py EXPORT.xlsx --claude     # also send the rest to Claude
"""
import argparse
import collections
import os
import sys
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from meow.config import get_settings  # noqa: E402
from meow.defaults import DEFAULT_CATEGORIES  # noqa: E402
from meow.importer import build_plan  # noqa: E402
from meow.models import CategoryInfo, ParseContext, WalletInfo  # noqa: E402
from meow.parser import parse_message  # noqa: E402
from meow.parser_llm import cost_usd  # noqa: E402
from meow.text import phrase_keys, phrase_words  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("xlsx")
    ap.add_argument("--days", type=int, default=92)
    ap.add_argument("--claude", action="store_true")
    ap.add_argument("--show", type=int, default=25, help="how many misses to print")
    a = ap.parse_args()

    plan = build_plan(a.xlsx, None)
    rows = [r for r in plan.rows if r["type"] in ("expense", "income")]
    cut = plan.last_day - timedelta(days=a.days)
    train = [r for r in rows if r["day"] <= cut.isoformat()]
    test = [r for r in rows if r["day"] > cut.isoformat()]

    counts = collections.defaultdict(collections.Counter)
    for r in train:
        for k in phrase_keys(r["description"]):
            counts[k][r["category"]] += 1
    rules = {k: c.most_common(1)[0][0] for k, c in counts.items()
             if c.most_common(1)[0][1] >= 2 and c.most_common(1)[0][1] / sum(c.values()) >= 0.8}

    wallets = [WalletInfo(1, "DBS", "SGD", True), WalletInfo(2, "VCB", "VND", True)]
    cats = [CategoryInfo(i, n, e, t) for i, (n, e, t) in enumerate(DEFAULT_CATEGORIES)]
    past = collections.Counter((r["description"], r["category"]) for r in train)

    def examples(text):
        want = set(phrase_words(text))
        scored = sorted(((len(want & set(phrase_words(d))) / max(1, len(want | set(phrase_words(d)))), n, d, c)
                         for (d, c), n in past.items() if want & set(phrase_words(d))), reverse=True)
        return [(d, c) for _, _, d, c in scored[:8]]

    client = None
    if a.claude:
        import anthropic
        client = anthropic.Anthropic(api_key=get_settings().anthropic_api_key)

    tally, misses, cost, calls = collections.Counter(), [], 0.0, 0
    for r in test:
        amount = abs(r["amount_minor"])
        typed = f"{amount / 100:g}" if r["currency"] == "SGD" else (f"{amount // 1000}k" if amount % 1000 == 0 else f"{amount} vnd")
        text = ("+" if r["type"] == "income" else "") + f"{r['description']} {typed}"
        from datetime import date
        ctx = ParseContext(date.fromisoformat(r["day"]), "SGD", wallets, cats, rules)
        logs = []
        res = parse_message(text, ctx, llm_client=client, model=get_settings().anthropic_model,
                            on_llm_call=logs.append, examples=examples)
        for log in logs:
            calls += 1
            cost += cost_usd(log, 1.0, 5.0) or 0
        got = res.entries[0].category if len(res.entries) == 1 else None
        via = res.parser if res.entries else ("asked" if res.question else "none")
        ok = got == r["category"]
        tally[(via, ok)] += 1
        if not ok:
            misses.append((text, r["category"], got, via))

    n = len(test)
    rule_n = tally[("rule", True)] + tally[("rule", False)]
    print(f"Learned {len(rules)} phrases from {len(train)} entries; testing {n} entries from the last {a.days} days\n")
    print(f"Handled by rules: {rule_n} ({rule_n * 100 // n}%), correct {tally[('rule', True)]}"
          f" ({tally[('rule', True)] * 100 // max(1, rule_n)}% of those)")
    if a.claude:
        llm_n = tally[("llm", True)] + tally[("llm", False)]
        print(f"Sent to Claude:   {llm_n} ({llm_n * 100 // n}%), correct {tally[('llm', True)]}, "
              f"cost ≈ US${cost:.4f} for {calls} calls")
    else:
        left = n - rule_n
        print(f"Would go to Claude: {left} ({left * 100 // n}%)  (run with --claude to test those)")
    total_ok = sum(v for (via, ok), v in tally.items() if ok)
    print(f"Overall correct: {total_ok}/{n} ({total_ok * 100 // n}%)\n")
    print("Misses (typed → expected / got):")
    for text, want, got, via in misses[: a.show]:
        print(f"  [{via:5}] {text!r:45} → {want} / {got}")


if __name__ == "__main__":
    main()
