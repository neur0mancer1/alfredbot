"""Marry the two halves: a parsed email + a household -> a settle-able Receipt.

The email gave us items + fees + total (``ParsedReceipt``). The chat/config gives
us the members and who paid. Here they meet. Recognised fees are collapsed into a
single overhead line (split equally per our policy); any unreconciled leftover is
kept as a visible overhead line so the Receipt total still ties to what was paid.
"""

from __future__ import annotations

import uuid

from .models import LineItem, Member, Receipt
from .parsers.base import ParsedReceipt


def receipt_from_parsed(
    parsed: ParsedReceipt,
    *,
    members: list[Member],
    payer_id: str,
    household_id: str,
    receipt_id: str | None = None,
) -> Receipt:
    items = [
        LineItem(name=i.name, total_pence=i.charged_pence, quantity=i.quantity)
        for i in parsed.items
    ]

    if parsed.fees_total_pence:
        items.append(
            LineItem(name="Delivery & fees", total_pence=parsed.fees_total_pence,
                     quantity=1, is_overhead=True)
        )
    if parsed.other_adjustments_pence:
        items.append(
            LineItem(name="Other adjustments", total_pence=parsed.other_adjustments_pence,
                     quantity=1, is_overhead=True)
        )

    return Receipt(
        id=receipt_id or uuid.uuid4().hex[:8],
        household_id=household_id,
        retailer=parsed.retailer,
        payer_id=payer_id,
        members=members,
        items=items,
        stated_total_pence=parsed.total_paid_pence,
        created_at=parsed.ordered_at,
        order_ref=parsed.order_ref,
    )
