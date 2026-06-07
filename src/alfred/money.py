"""Money handling — always exact, never floats.

We keep money as integer **pence** internally and only convert to Decimal
pounds at the display boundary. Splitting is done with the largest-remainder
(Hamilton) method so allocated parts *always* sum back to the original total —
no lost or phantom pennies. This is the one part of the product that must be
deterministic and provably correct, so it has no LLM and no floats anywhere.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP


def to_pence(value: str | int | Decimal) -> int:
    """Convert pounds (e.g. '12.34', Decimal('12.34')) to integer pence.

    Rounds half-up to the nearest penny. Raises on garbage rather than guessing.
    """
    d = Decimal(str(value))
    pennies = (d * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(pennies)


def from_pence(pence: int) -> Decimal:
    """Integer pence -> Decimal pounds (e.g. 1234 -> Decimal('12.34'))."""
    return (Decimal(pence) / 100).quantize(Decimal("0.01"))


def format_money(pence: int) -> str:
    """Human-readable, e.g. 1234 -> '£12.34', -500 -> '-£5.00'."""
    sign = "-" if pence < 0 else ""
    return f"{sign}£{abs(pence) / 100:.2f}"


def allocate_pence(total_pence: int, weights: list[int]) -> list[int]:
    """Split ``total_pence`` across ``weights`` so the parts sum to exactly the total.

    Uses the largest-remainder method: everyone gets their floored share, then
    the leftover pennies go to the largest fractional remainders (ties broken by
    lowest index, so the result is fully deterministic). For an equal split pass
    ``[1, 1, ...]``.

    >>> allocate_pence(100, [1, 1, 1])
    [34, 33, 33]
    """
    if not weights:
        raise ValueError("cannot allocate across zero recipients")
    if any(w < 0 for w in weights):
        raise ValueError("weights must be non-negative")
    total_weight = sum(weights)
    if total_weight == 0:
        raise ValueError("weights sum to zero")

    floors = [(total_pence * w) // total_weight for w in weights]
    remainder = total_pence - sum(floors)
    # Numerator of each fractional part (denominator is total_weight, common).
    fracs = [(total_pence * w) % total_weight for w in weights]

    # Distribute the leftover pennies to the largest remainders first.
    order = sorted(range(len(weights)), key=lambda i: (-fracs[i], i))
    for i in order[:remainder]:
        floors[i] += 1
    return floors
