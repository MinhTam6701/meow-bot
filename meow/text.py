"""Text helpers shared by the parsers."""
from __future__ import annotations

import re
import unicodedata
from typing import Optional


def normalize(word: str) -> str:
    """Lower-case, accent-free: 'Tiền' -> 'tien', 'đồ' -> 'do'."""
    word = word.lower().replace("đ", "d")
    word = unicodedata.normalize("NFKD", word)
    return "".join(ch for ch in word if not unicodedata.combining(ch)).strip(".!?:()\"'[]")


def squash(text: str) -> str:
    """'Tiền mặt' -> 'tienmat': for matching names typed with or without spaces/accents."""
    return "".join(normalize(w) for w in text.split())


# Words skipped when building a phrase key ("mua đồ nấu ăn" -> "do nau an").
PHRASE_STOP = {
    "the", "a", "at", "for", "my", "to", "from", "on", "in", "with", "and", "of", "some",
    "mua", "tien", "di", "cho", "va", "vs", "voi", "thang", "den", "cua", "o", "may", "cai", "nhung",
}
# "an" is deliberately not skipped: Vietnamese "ăn" (eat) normalizes to it.

DATE_LIKE = re.compile(r"^\(?\d{1,2}([./-]\d{1,4})+\)?$")


def phrase_words(description: str) -> list[str]:
    words = []
    for raw in re.sub(r"[+(),]", " ", description).split():
        n = normalize(raw)
        if not n or n in PHRASE_STOP or any(ch.isdigit() for ch in n):
            continue
        words.append(n)
    return words


def phrase_keys(description: str) -> list[str]:
    """Most specific first: ['do nau an', 'do nau', 'do']."""
    w = phrase_words(description)
    keys = []
    for n in (3, 2, 1):
        if len(w) >= n:
            k = " ".join(w[:n])
            if k not in keys:
                keys.append(k)
    return keys


def merchant_key(description: str) -> Optional[str]:
    """The first meaningful word ("Grab to work" -> "grab")."""
    keys = phrase_keys(description)
    return keys[-1] if keys else None
