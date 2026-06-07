"""Core domain models — plain dataclasses, stdlib only (testable with no installs).

An assignment is just a set of member ids:
  * ``None``            -> not yet assigned (the bot nags about these)
  * ``{"alice"}``       -> personal (Mine / Theirs)
  * ``{"alice","bob"}`` -> split equally among that subset
Overhead lines (delivery, bag charge, service) default to splitting across the
whole household when left unassigned.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Member:
    id: str
    name: str


@dataclass
class LineItem:
    name: str
    total_pence: int          # exact, already qty-inclusive (what was charged)
    quantity: int = 1
    assigned_to: frozenset[str] | None = None
    is_overhead: bool = False  # delivery/service/bag -> default-split across all

    def assign(self, member_ids: set[str] | frozenset[str] | None) -> None:
        self.assigned_to = None if member_ids is None else frozenset(member_ids)


@dataclass
class Receipt:
    id: str
    household_id: str
    retailer: str
    payer_id: str                       # who fronted the money
    members: list[Member]
    items: list[LineItem]
    stated_total_pence: int             # the total the retailer says was charged
    created_at: str = ""                # ISO timestamp (dated artifact)
    order_ref: str = ""                 # retailer order number (for duplicate detection)

    @property
    def member_ids(self) -> list[str]:
        return [m.id for m in self.members]

    @property
    def items_total_pence(self) -> int:
        return sum(i.total_pence for i in self.items)

    def total_matches(self) -> bool:
        """Deterministic integrity check: parsed lines == retailer's stated total."""
        return self.items_total_pence == self.stated_total_pence
