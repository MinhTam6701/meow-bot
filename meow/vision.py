"""Read a receipt, bank-app screenshot or card notification with Claude vision.

The model only reports what it sees. Code decides what to log, what to ask about,
and checks for duplicates. Account, card and phone numbers are never kept.
"""
from __future__ import annotations

import base64
import re
import time
from datetime import date, timedelta
from datetime import date as Day
from decimal import Decimal
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, ValidationError, field_validator

from .defaults import CATEGORY_HINTS
from .models import ParseContext
from .parser_llm import LLMCallLog

TOOL_NAME = "record_payments"
MAX_IMAGE_BYTES = 5 * 1024 * 1024  # Claude's limit per image


class SeenPayment(BaseModel):
    amount: Decimal = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)
    type: Literal["expense", "income"]
    category: str
    description: str = Field(min_length=1, max_length=80)
    date: Optional[Day] = None
    wallet: Optional[str] = None
    counterparty: Optional[str] = Field(default=None, max_length=80)
    counterparty_kind: Literal["business", "person", "unknown"] = "unknown"
    note: Optional[str] = Field(default=None, max_length=80)

    @field_validator("currency")
    @classmethod
    def upper(cls, v: str) -> str:
        return v.upper()

    @field_validator("description", "counterparty", "note")
    @classmethod
    def no_numbers(cls, v: Optional[str]) -> Optional[str]:
        return scrub(v) if v else v


class SeenImage(BaseModel):
    payments: list[SeenPayment] = []
    question: Optional[str] = None


def scrub(text: str) -> str:
    """Drop anything that looks like an account, card, phone or reference number."""
    text = re.sub(r"\+?\d[\d\s\-•.*]{5,}\d", "", text)   # long digit runs, with spaces/dashes
    text = re.sub(r"[•*]{2,}\s*\d+", "", text)           # masked card endings like ••5949
    text = re.sub(r"\b(?=[A-Za-z0-9]*\d{4})[A-Za-z0-9]{6,}\b", "", text)  # codes: NAP245729, 6234BFTVGLYXN6X1
    return re.sub(r"\s{2,}", " ", text).strip(" ,:-")


def build_tool(ctx: ParseContext) -> dict:
    expense = ctx.category_names("expense")
    income = ctx.category_names("income")
    return {
        "name": TOOL_NAME,
        "description": "Report the completed payments visible in the image.",
        "input_schema": {
            "type": "object",
            "properties": {
                "payments": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "amount": {"type": "string", "description": "The amount actually paid or received, digits only, '.' as decimal point. E.g. '71.99', '3468000'."},
                            "currency": {"type": "string", "description": "ISO code: SGD, VND, USD..."},
                            "type": {"type": "string", "enum": ["expense", "income"]},
                            "category": {"type": "string", "enum": sorted(set(expense + income))},
                            "description": {"type": "string", "description": "2-5 words: the shop or what it was, e.g. 'Scarlett Supermarket', 'Shopee vacuum', 'Viettel wifi', 'PayNow to Six Guo'."},
                            "date": {"type": "string", "description": "YYYY-MM-DD of the payment, if the image shows or implies it. Omit if unknown."},
                            "wallet": {"type": "string", "description": f"Only if the bank or card is identifiable: one of {[w.name for w in ctx.wallets]}."},
                            "counterparty": {"type": "string", "description": "Who received (or sent) the money: a shop, company or person's name. Never a number."},
                            "counterparty_kind": {"type": "string", "enum": ["business", "person", "unknown"]},
                            "note": {"type": "string", "description": "The transfer message / nội dung, if any, without codes or numbers."},
                        },
                        "required": ["amount", "currency", "type", "category", "description", "counterparty_kind"],
                    },
                },
                "question": {"type": "string", "description": "Only if there is no completed payment in the image: one short sentence saying what you see."},
            },
            "required": ["payments"],
        },
    }


def build_system(ctx: ParseContext, owner_name: Optional[str]) -> str:
    wallets = ", ".join(f"{w.name} ({w.currency}{', also called ' + ', '.join(w.aliases) if w.aliases else ''})"
                        for w in ctx.wallets)
    cats = "\n".join(f"- {c.name}: {CATEGORY_HINTS.get(c.name, '')}".rstrip(": ") for c in ctx.categories)
    return f"""You read pictures of payments for a personal money tracker: receipts, bank-app transfer
confirmations, card payment notifications, shopping-app orders and bill payments.
The user lives in Singapore and also spends in Vietnam.{f' Their name is {owner_name}.' if owner_name else ''}

Today is {ctx.today.isoformat()} ({ctx.today.strftime('%A')}).
Wallets: {wallets}.

Rules:
- Report only completed payments. One image may show several (e.g. a transaction list); report each one.
- Amount: what was actually paid after discounts and vouchers (e.g. "Total 1 item: $71.99", not a crossed-out or list price).
  On a receipt, the final total including tax and service charge, not a subtotal or a line item.
- Vietnamese amounts use spaces, dots or commas as thousand separators: "3 468 000 đ" is 3468000 VND.
  "$" in a Singapore app (DBS, PayLah, Shopee SG, Grab SG) is SGD.
- Date: from the image. Dates like 08/09/2026 are day/month/year. If only "7 minutes ago" is shown,
  use the date in the phone's status bar. If no year is shown, use the most recent past date. Omit if unknown.
- Wallet: DBS for DBS/POSB digibank, PayLah, a DBS Visa card, or a dark "You send SGD" transfer screen with a
  272-xxxxxx-x style account. VPBank for VPBank screens, VCB for Vietcombank / VCB Digibank. Otherwise omit.
- counterparty_kind: 'person' for an individual's name (e.g. "TRAN CAM VAN", a PayNow name),
  'business' for a shop, company or biller (e.g. Viettel, Shopee seller, supermarket).
- Bank transfers that come in are income; money going out is an expense.
- Category: pick the closest. Home bills (rent, wifi/internet, electricity, water) go to Housing; phone bills to Phone.
- Description in the user's language style, short, no codes.
- Never copy account numbers, card numbers, phone numbers or reference codes into any field.
- If the image has no completed payment (a product page, a cart, a failed transfer), return no payments
  and a short question.

Categories:
{cats}

Always answer by calling the {TOOL_NAME} tool."""


def read_image(client: Any, model: str, image: bytes, media_type: str, caption: Optional[str],
               ctx: ParseContext, owner_name: Optional[str] = None) -> tuple[Optional[SeenImage], LLMCallLog]:
    text = "Read the payments in this image."
    if caption:
        text += f"\nThe user added this note: {caption}"
    log = LLMCallLog(model=model, input_tokens=None, output_tokens=None, latency_ms=0, ok=False,
                     input_text=f"[image {len(image) // 1024} KB] {caption or ''}".strip(), output_json=None)
    start = time.monotonic()
    try:
        resp = client.messages.create(
            model=model, max_tokens=1200, system=build_system(ctx, owner_name),
            tools=[build_tool(ctx)], tool_choice={"type": "tool", "name": TOOL_NAME},
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type,
                                             "data": base64.b64encode(image).decode()}},
                {"type": "text", "text": text}]}])
        usage = getattr(resp, "usage", None)
        log.input_tokens = getattr(usage, "input_tokens", None)
        log.output_tokens = getattr(usage, "output_tokens", None)
        block = next((b for b in resp.content if getattr(b, "type", "") == "tool_use"), None)
        if block is None:
            log.error = "no tool call"
            return None, log
        raw = dict(block.input)
        seen = SeenImage.model_validate(_tidy(raw, ctx))
        log.output_json = seen.model_dump(mode="json")  # scrubbed, so no account numbers in the logs
        log.ok = True
        return seen, log
    except ValidationError as exc:
        log.error = f"invalid: {exc.errors()[:3]}"[:500]
        return None, log
    except Exception as exc:
        log.error = f"{type(exc).__name__}: {exc}"[:500]
        return None, log
    finally:
        log.latency_ms = int((time.monotonic() - start) * 1000)


def _tidy(raw: dict, ctx: ParseContext) -> dict:
    """Small repairs before validation: amounts with separators, future dates, unknown categories."""
    out = []
    for p in raw.get("payments") or []:
        p = dict(p)
        amt = str(p.get("amount", "")).replace(" ", "").replace(",", "")
        if p.get("currency", "").upper() == "VND":
            amt = amt.replace(".", "")
        p["amount"] = amt
        d = p.get("date")
        if d:
            try:
                day = date.fromisoformat(d)
                if day > ctx.today + timedelta(days=1):  # a misread year; keep it unknown
                    p["date"] = None
            except ValueError:
                p["date"] = None
        if not ctx.category(p.get("category", "")):
            p["category"] = "Other income" if p.get("type") == "income" else "Other"
        for k in ("wallet", "counterparty", "note"):
            if p.get(k) in ("", None):
                p.pop(k, None)
        out.append(p)
    return {"payments": out, "question": raw.get("question")}
