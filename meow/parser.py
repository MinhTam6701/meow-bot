"""Parser entry point: rules first, the LLM only when the rules aren't sure."""
from __future__ import annotations

import re
from typing import Any, Callable, Optional

from .models import ParseContext, ParseResult
from .parser_llm import LLMCallLog, parse_with_llm
from .parser_rules import merchant_key, parse_with_rules


def has_amount(text: str) -> bool:
    return bool(re.search(r"\d", text))


def parse_message(
    text: str,
    ctx: ParseContext,
    llm_client: Any = None,
    model: str = "claude-haiku-4-5",
    on_llm_call: Optional[Callable[[LLMCallLog], None]] = None,
    llm_allowed: bool = True,
) -> ParseResult:
    text = text.strip()
    if not text:
        return ParseResult()

    entries = parse_with_rules(text, ctx)
    if entries:
        return ParseResult(entries=entries, parser="rule")

    if llm_client is None or not llm_allowed:
        return ParseResult(question="I couldn't read that one on my own. Try a format like \"pho 65k\" or \"grab 12.5 yesterday\".")
    result = parse_with_llm(text, ctx, llm_client, model, on_llm_call)
    # Learned merchant rules beat the model's guess.
    for e in result.entries:
        learned = ctx.merchant_rules.get(merchant_key(e.description) or "")
        cat = ctx.category(learned) if learned else None
        if cat and cat.type == e.type:
            e.category = cat.name
    return result
