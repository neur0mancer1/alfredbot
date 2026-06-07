"""Deterministic Tesco receipt parser (BeautifulSoup) — no LLM, no floats.

Reads a Tesco "Receipt for your tesco.com order" .eml and extracts items + fees +
the grand total, then reconciles them. The one rule that matters for money: the
**charged** price is the price cell *without* the ``hidden-on-mobile`` class —
Tesco hides the guide (pre-offer) price column on mobile, so the visible cell is
what was actually billed. We never read prices by column position.
"""

from __future__ import annotations

import email
import re
from email import policy

from bs4 import BeautifulSoup

from .base import ParsedFee, ParsedItem, ParsedReceipt, parse_money

_ORDER_RE = re.compile(r"(\d{4}-\d{4}-\d{3,})")
_FEE_KEYWORDS = ("pick", "pack", "deliver", "basket charge", "bag charge", "service charge")
_SUBST = "†"  # † marks a substituted item


def parse(eml_bytes: bytes) -> ParsedReceipt:
    msg = email.message_from_bytes(eml_bytes, policy=policy.default)
    subject = msg["subject"] or ""
    m = _ORDER_RE.search(subject)
    order_ref = m.group(1) if m else ""
    ordered_at = msg["date"] or ""

    html = msg.get_body(preferencelist=("html",)).get_content()
    soup = BeautifulSoup(html, "lxml")

    r = ParsedReceipt(retailer="Tesco", order_ref=order_ref, ordered_at=ordered_at)

    for tr in soup.find_all("tr"):
        cells = tr.find_all("td", recursive=False)
        if len(cells) < 2:
            continue
        first = cells[0].get_text(" ", strip=True)

        if first.isdigit():
            # Item row: first cell is the quantity.
            item = _parse_item_row(cells, int(first))
            if item:
                r.items.append(item)
        elif "£" in tr.get_text() and any(ch.isalpha() for ch in first):
            # Summary row: a text label + an amount somewhere in the row.
            amount = parse_money(tr.get_text(" ", strip=True))
            if amount is None:
                continue
            low = first.lower()
            if low == "total paid":
                r.total_paid_pence = amount
            elif any(k in low for k in _FEE_KEYWORDS):
                r.fees.append(ParsedFee(label=first, pence=amount))

    if r.total_paid_pence == 0:
        r.notes.append("grand total 'Total paid' not found")

    # Hybrid reconciliation: whatever items + recognised fees don't explain is
    # surfaced as a *visible* leftover, never silently split across the household.
    r.other_adjustments_pence = (
        r.total_paid_pence - r.items_subtotal_pence - r.fees_total_pence
    )
    if r.other_adjustments_pence != 0:
        r.notes.append(
            f"unreconciled leftover {r.other_adjustments_pence}p — manual review"
        )

    return r


def _parse_item_row(cells, qty: int) -> ParsedItem | None:
    name = cells[1].get_text(" ", strip=True)
    substituted = name.startswith(_SUBST)
    name = name.lstrip(_SUBST).strip()
    name = re.split(r"\s*Substitutions?\s*:", name)[0].strip()  # drop "Substitutions: On" tail

    # Tesco lists the price columns as [guide(was), charged, (saving)]. So the charged
    # line total is the SECOND positive cell when a guide is present, else the only one.
    # Savings render +ve (Whoosh) or -ve (delivery), so ignore negatives. This is more
    # layout-stable than the hidden-on-mobile class, which differs by service.
    positives = [
        v for c in cells[2:]
        if (v := parse_money(c.get_text(" ", strip=True))) is not None and v >= 0
    ]
    if not positives:
        return None                    # not a real priced item row
    charged = positives[1] if len(positives) >= 2 else positives[0]
    guide = positives[0] if len(positives) >= 2 else None
    return ParsedItem(
        name=name,
        quantity=qty,
        charged_pence=charged,
        guide_pence=guide,
        on_offer=(guide is not None and guide > charged),
        substituted=substituted,
    )
