"""The pie picture sent in the chat with /report, /recap and /month. Drawn from report_data, nothing else."""
from __future__ import annotations

import io
import math
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .money import fmt

FONTS = Path(__file__).resolve().parent.parent / "assets" / "fonts"
# Same order as the chart page (webapp/report.html): slice 1 is the biggest category; "Other" is grey.
PALETTE = ["#3B6FD8", "#E07B4C", "#2FA57D", "#8C5FC7", "#D9A21F", "#D05B85"]
OTHER = "#9AA0A8"
INK, QUIET, LINE, BG = "#16181D", "#6B7280", "#E6E8EC", "#FFFFFF"
GOOD, BAD = "#2FA57D", "#D0453A"
WIDTH, S = 1080, 2  # drawn at twice the size, then scaled down for smooth edges


@lru_cache(maxsize=None)
def _font(size: int, bold: bool = False):
    try:
        return ImageFont.truetype(str(FONTS / ("Inter-SemiBold.otf" if bold else "Inter-Regular.otf")), size * S)
    except OSError:
        return ImageFont.load_default(size * S)


def colour(i: int, name: str) -> str:
    return OTHER if name == "Other" else PALETTE[i % len(PALETTE)]


def render(data: dict) -> bytes:
    slices, home = data["pie"], data["home"]
    rows = len(slices)
    height = 1010 + max(rows, 1) * 62 + (70 if data.get("bills") else 30)
    img = Image.new("RGB", (WIDTH * S, height * S), BG)
    d = ImageDraw.Draw(img)

    def text(x, y, s, size, fill=INK, bold=False, anchor="la"):
        d.text((x * S, y * S), s, font=_font(size, bold), fill=fill, anchor=anchor)

    left, right = 72, WIDTH - 72
    kind = "This week" if data["kind"] == "week" else "This month"
    if not data.get("current"):
        kind = "Week" if data["kind"] == "week" else "Month"
    text(left, 64, f"{kind}{' so far' if data.get('current') else ''}", 30, QUIET)
    text(left, 104, data["label"], 52, bold=True)

    # Spent, with the change; income and savings rate on the right.
    text(left, 200, "Spent", 28, QUIET)
    text(left, 236, data["spent_text"], 60, bold=True)
    if data.get("change_pct") is not None:
        pct = data["change_pct"]
        text(left, 318, f"{'+' if pct >= 0 else '−'}{abs(pct)}% vs {data['compared_with']}", 28,
             BAD if pct > 0 else GOOD)
    if data.get("income"):
        text(right, 200, "Income", 28, QUIET, anchor="ra")
        text(right, 236, data["income_text"], 44, bold=True, anchor="ra")
        if data.get("savings_rate") is not None:
            rate = data["savings_rate"]
            text(right, 300, f"Savings rate {rate}%", 30, GOOD if rate > 0 else BAD, bold=True, anchor="ra")

    # The donut.
    cx, cy, outer, inner = WIDTH // 2, 640, 250, 158
    box = [(cx - outer) * S, (cy - outer) * S, (cx + outer) * S, (cy + outer) * S]
    total = sum(s["amount"] for s in slices)
    if total:
        angle = -90.0
        for i, s in enumerate(slices):
            sweep = 360.0 * s["amount"] / total
            d.pieslice(box, angle, angle + sweep, fill=colour(i, s["name"]))
            if len(slices) > 1:  # a thin white gap between slices
                a = math.radians(angle)
                d.line([(cx * S, cy * S), ((cx + outer * math.cos(a)) * S, (cy + outer * math.sin(a)) * S)],
                       fill=BG, width=4 * S)
            angle += sweep
    else:
        d.ellipse(box, fill=LINE)
    d.ellipse([(cx - inner) * S, (cy - inner) * S, (cx + inner) * S, (cy + inner) * S], fill=BG)
    if total:
        text(cx, cy - 6, fmt(total, home), 44, bold=True, anchor="ms")
        text(cx, cy + 34, "everyday spending" if data.get("bills") else "spent", 26, QUIET, anchor="ms")
    else:
        text(cx, cy + 12, "Nothing spent", 32, QUIET, anchor="ms")

    # The legend: one row per slice.
    y = 950
    for i, s in enumerate(slices):
        d.rounded_rectangle([left * S, (y - 22) * S, (left + 28) * S, (y + 6) * S], radius=6 * S,
                            fill=colour(i, s["name"]))
        name = s["name"] if len(s["name"]) <= 26 else s["name"][:25] + "…"
        text(left + 48, y, name, 32, bold=(i == 0), anchor="ls")
        text(right - 110, y, fmt(s["amount"], home), 32, anchor="rs")
        text(right, y, f"{s['share']}%", 32, QUIET, anchor="rs")
        y += 62

    if data.get("bills"):
        d.line([(left * S, (y - 14) * S), (right * S, (y - 14) * S)], fill=LINE, width=2 * S)
        text(left, y + 26, f"Plus bills {data['bills_text']} ({data.get('bills_note', 'rent, phone, study, fees')}), not in the pie", 26, QUIET,
             anchor="ls")

    out = io.BytesIO()
    img.resize((WIDTH, height), Image.LANCZOS).save(out, "PNG", optimize=True)
    return out.getvalue()
