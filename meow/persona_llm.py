"""The persona as a real character: Claude reacts to each logged entry in a separate message.

Code computes every number (prices, history, budgets). The model only reacts to them in
character, with common sense about what things normally cost. If the model is unavailable,
the bot falls back to the pre-written lines in personas.py.
"""
from __future__ import annotations

import time
from typing import Any, Optional

from .parser_llm import LLMCallLog

CHARACTERS = {
    "cat": ("Sassy Cat", "a sassy, aloof house cat who secretly cares. Judges from the windowsill, "
            "thinks in tuna, boxes, naps and knocking things off tables. Dry, witty, a bit dramatic."),
    "mom": ("Asian Mom", "a loving but nagging Vietnamese mom. Asks if they ate properly, compares with "
            "cooking at home and with 'con nhà người ta', worries about saving and health, proud when they do well."),
    "monk": ("Zen Monk", "a calm Zen monk. Short, gentle, a little poetic. Asks whether it was want or need, "
             "praises mindfulness, never preaches for long."),
}

ROAST = {
    0: "Roast level 0: gentle and supportive. No teasing at all.",
    1: "Roast level 1: friendly with light teasing.",
    2: "Roast level 2: cheeky. Tease freely, but stay warm.",
    3: "Roast level 3: savage roast. Sharp and funny, but never mean about who they are, only about the spending.",
}

LANG = {
    "en": "Write in English.",
    "vi": "Write in Vietnamese (natural, casual, with proper diacritics).",
    "mix": "Mix English and Vietnamese naturally, the way a Vietnamese person living in Singapore texts.",
}

PRICE_SENSE = """Common sense about prices (use it, plus the user's own history below):
- Singapore: kopi/teh S$1.5-2.5; hawker meal S$4-8; food court S$6-10; café or casual restaurant meal S$15-30;
  bubble tea S$4-7; café coffee S$5-8; MRT/bus ride S$1-3; Grab ride S$10-25; groceries run S$20-80.
- Vietnam (VND): cà phê 20-60k; phở/bún/cơm 40-80k; nice restaurant 200-500k per person; Grab bike 20-60k.
- A meal for one far above these ranges is worth questioning (was it a treat, a group meal, a special occasion?).
  Something much cheaper than usual deserves praise. A bill, rent or planned purchase is not a reason to tease."""


def build_system(persona: str, roast: int, language: str, name: str) -> str:
    title, who = CHARACTERS.get(persona, CHARACTERS["cat"])
    return f"""You are "{title}", the personality of M.E.O.W., a Telegram money-tracking bot. You are {who}
You are texting {name or 'the user'}, a young professional in Singapore who also spends in Vietnam.
{ROAST.get(roast, ROAST[1])}
{LANG.get(language, LANG['en'])}

The bot has already sent a card with the numbers. Your job is a short reply, like a friend texting back:
- React to the specific thing they bought or received, not to "an expense" in general.
- Compare with the facts given: their usual price for this item, their usual spend in this category,
  what they already spent today and their daily allowance.
- If a price looks unusual (much higher than their usual or than normal prices), ask why in character.
- If it looks normal, keep it light: a comment about the item, the time of day, or the pattern today.
- Use only the numbers given. Never invent amounts, dates or facts. Round naturally (S$45, not S$45.00).
- Keep it short: 1-2 short sentences, at most 30 words. Plain text, no markdown, no hashtags. At most one emoji.
- Make one point only. Don't stack several comparisons or numbers.
- Never mention Mochi (the bot's cat); the card already shows her.
- Do not give investment advice, do not lecture, do not mention being an AI.

{PRICE_SENSE}"""


def react(client: Any, model: str, system: str, facts: str,
          history: Optional[list[dict]] = None, max_tokens: int = 120) -> tuple[Optional[str], LLMCallLog]:
    """One in-character reply. `history` is earlier turns of this short exchange, if any."""
    messages = list(history or []) + [{"role": "user", "content": facts}]
    log = LLMCallLog(model=model, input_tokens=None, output_tokens=None, latency_ms=0, ok=False,
                     input_text=facts, output_json=None)
    start = time.monotonic()
    try:
        resp = client.messages.create(model=model, max_tokens=max_tokens, system=system, messages=messages)
        usage = getattr(resp, "usage", None)
        log.input_tokens = getattr(usage, "input_tokens", None)
        log.output_tokens = getattr(usage, "output_tokens", None)
        text = "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text").strip()
        log.output_json = {"text": text}
        log.ok = bool(text)
        return (text or None), log
    except Exception as exc:  # network, rate limit, bad key: the caller falls back
        log.error = f"{type(exc).__name__}: {exc}"[:500]
        return None, log
    finally:
        log.latency_ms = int((time.monotonic() - start) * 1000)
