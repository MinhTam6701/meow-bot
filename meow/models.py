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

    def wallet_by_name(self, name: str) -> Optional[WalletInfo]:
        n = name.strip().lower()
        return next((w for w in self.wallets if w.name.lower() == n), None)

    def category_names(self, type_: str) -> list[str]:
        return [c.name for c in self.categories if c.type == type_]
