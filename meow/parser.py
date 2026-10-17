"""Parser entry point: rules first, the LLM only when the rules aren't sure."""
from __future__ import annotations

import re
from typing import Any, Callable, Optional

from .models import ParseContext, ParseResult
from .parser_llm import LLMCallLog, parse_with_llm
from .parser_rules import learned_category, parse_with_rules


def has_amount(text: str) -> bool:
    return bool(re.search(r"\d", text))


PLANNED = "#planned"


def parse_message(
    text: str,
    ctx: ParseContext,
    llm_client: Any = None,
    model: str = "claude-haiku-4-5",
    on_llm_call: Optional[Callable[[LLMCallLog], None]] = None,
    llm_allowed: bool = True,
    examples: Optional[Callable[[str], list[tuple[str, str]]]] = None,
) -> ParseResult:
    text = text.strip()
    if not text:
        return ParseResult()

    entries = parse_with_rules(text, ctx)
    if entries:
        return ParseResult(entries=entries, parser="rule")

    if llm_client is None or not llm_allowed:
        return ParseResult(question="I couldn't read that one on my own. Try a format like \"pho 65k\" or \"grab 12.5 yesterday\".")
    past = examples(text) if examples else []
    result = parse_with_llm(text, ctx, llm_client, model, on_llm_call, examples=past)
    planned = PLANNED in text.lower()
    for e in result.entries:
        # Learned phrases beat the model's guess.
        learned = learned_category(e.description, ctx)
        cat = ctx.category(learned) if learned else None
        if cat and cat.type == e.type:
            e.category = cat.name
        # "#planned" keeps a buy out of everyday spending (db.EVERYDAY reads it from the description).
        # The rules keep the tag; Claude may rewrite the description, so it's put back here.
        if planned and PLANNED not in (e.description or "").lower():
            e.description = f"{e.description or ''} {PLANNED}".strip()
    return result
