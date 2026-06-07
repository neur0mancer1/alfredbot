"""Settlement — who owes whom, penny-perfect and deterministic.

Given a Receipt whose items each carry an ``assigned_to`` (set by people claiming
in the chat), work out each member's share, then who repays the person who paid.
No floats: every split goes through ``allocate_pence`` so the parts tie back to the
item total exactly.

(The cross-shop running ledger + minimal-transfer simplification comes later, when
we track balances across weeks. A single shop with one payer is just "everyone
else repays the payer their share".)
"""

from __future__ import annotations

from dataclasses import dataclass

from .money import allocate_pence
from .models import Receipt


@dataclass
class Shares:
    per_member: dict[str, int]       # member_id -> pence they consumed
    unassigned_pence: int            # value of non-overhead items nobody claimed yet
    unassigned_items: list[str]      # their names, for the chat nudge

    @property
    def fully_assigned(self) -> bool:
        return self.unassigned_pence == 0 and not self.unassigned_items


@dataclass
class Transaction:
    from_id: str
    to_id: str
    pence: int


def compute_shares(receipt: Receipt) -> Shares:
    """Allocate each item to whoever claimed it; track anything still unclaimed."""
    per_member: dict[str, int] = {mid: 0 for mid in receipt.member_ids}
    unassigned_pence = 0
    unassigned_items: list[str] = []

    for item in receipt.items:
        targets = item.assigned_to
        if targets is None:
            if item.is_overhead:
                targets = frozenset(receipt.member_ids)   # fees -> split across all
            else:
                unassigned_pence += item.total_pence       # real item, not yet claimed
                unassigned_items.append(item.name)
                continue

        claimers = [mid for mid in receipt.member_ids if mid in targets]
        if not claimers:                                   # claimed by nobody real
            unassigned_pence += item.total_pence
            unassigned_items.append(item.name)
            continue

        for mid, part in zip(claimers, allocate_pence(item.total_pence, [1] * len(claimers))):
            per_member[mid] += part

    return Shares(per_member, unassigned_pence, unassigned_items)


def settle_receipt(shares: Shares, payer_id: str) -> list[Transaction]:
    """One payer fronted the bill -> everyone else repays their share to them."""
    return [
        Transaction(from_id=mid, to_id=payer_id, pence=owed)
        for mid, owed in shares.per_member.items()
        if mid != payer_id and owed > 0
    ]
