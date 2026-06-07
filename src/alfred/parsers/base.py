"""Parser output contract — the email half only.

A parser turns a retailer's receipt .eml into a ``ParsedReceipt``: the items, the
recognised non-item fees, and the grand total — everything the *email* knows. It
deliberately knows nothing about households, members, or who paid; that lives in
the chat/config and is married to this later. (Email knows items + total; chat
knows people + payer.)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..money import to_pence

_MONEY = re.compile(r"(-?)\s*£\s*(\d+(?:\.\d{2})?)")


def parse_money(text: str) -> int | None:
    """First money value in ``text`` as signed pence. '£1.75' -> 175, '-£0.45' -> -45."""
    m = _MONEY.search(text)
    if not m:
        return None
    pence = to_pence(m.group(2))
    return -pence if m.group(1) == "-" else pence


@dataclass
class ParsedItem:
    name: str
    quantity: int
    charged_pence: int            # the line total actually billed (col2)
    guide_pence: int | None = None  # pre-offer guide price (metadata, not billed)
    on_offer: bool = False
    substituted: bool = False


@dataclass
class ParsedFee:
    label: str
    pence: int                    # signed: charges +, removals/refunds -


@dataclass
class ParsedReceipt:
    retailer: str
    order_ref: str
    ordered_at: str               # the email's Date header
    items: list[ParsedItem] = field(default_factory=list)
    fees: list[ParsedFee] = field(default_factory=list)
    total_paid_pence: int = 0     # SSOT grand total from the email
    other_adjustments_pence: int = 0  # visible leftover; 0 when everything reconciles
    currency: str = "GBP"
    notes: list[str] = field(default_factory=list)

    @property
    def items_subtotal_pence(self) -> int:
        return sum(i.charged_pence for i in self.items)

    @property
    def fees_total_pence(self) -> int:
        return sum(f.pence for f in self.fees)

    @property
    def ok(self) -> bool:
        """Anti-tamper invariant: items + fees + leftover == grand total."""
        return (
            self.items_subtotal_pence
            + self.fees_total_pence
            + self.other_adjustments_pence
        ) == self.total_paid_pence
