"""Shapes shared by the parsers and the bot."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


class Entry(BaseModel):
    """One money movement read from a message."""

    amount: Decimal = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)
    type: Literal["expense", "income"]
    category: str
    description: str = Field(min_length=1, max_length=200)
    date: date
    wallet: Optional[str] = None

    @field_validator("currency")
    @classmethod
    def upper(cls, v: str) -> str:
        return v.upper()


@dataclass
class ParseResult:
    entries: list[Entry] = field(default_factory=list)
    question: Optional[str] = None  # asked instead of guessing
    parser: Literal["rule", "llm", "none"] = "none"


@dataclass
class WalletInfo:
    id: int
    name: str
    currency: str
    is_default: bool = False
    aliases: list[str] = field(default_factory=list)


@dataclass
class CategoryInfo:
    id: int
    name: str
    emoji: str
    type: Literal["expense", "income"]


@dataclass
class ParseContext:
    today: date
    home_currency: str
    wallets: list[WalletInfo]
    categories: list[CategoryInfo]
    # Learned from corrections: merchant keyword -> category name
    merchant_rules: dict[str, str] = field(default_factory=dict)
    # Keywords the user taught by correcting entries (these beat the keyword table)
    taught: set[str] = field(default_factory=set)

    def wallet_by_name(self, name: str) -> Optional[WalletInfo]:
        """Matches the name or an alias, ignoring case, accents and spaces ("tiền mặt" = "tienmat")."""
        from .text import squash

        n = squash(name)
        if not n:
            return None
        return next((w for w in self.wallets if squash(w.name) == n or n in {squash(a) for a in w.aliases}), None)

    def category(self, name: str) -> Optional[CategoryInfo]:
        return next((c for c in self.categories if c.name == name), None)

    def category_names(self, type_: str) -> list[str]:
        return [c.name for c in self.categories if c.type == type_]
