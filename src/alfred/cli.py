"""Interactive CLI — drive the whole flow yourself, no chat tokens needed.

    PYTHONPATH=src .venv/bin/python -m alfred.cli data/tesco_eg1.eml

Parse a real receipt -> claim each item (you / flatmate / split) -> see who owes
whom -> save the shop as JSON. This is the exact flow the chat will run; the chat
just swaps "type a number" for "tap a button".
"""

from __future__ import annotations

import sys

from .assemble import receipt_from_parsed
from .memory import LocalMemory
from .models import Member
from .money import format_money as fm
from .parsers import tesco
from .settlement import compute_shares, settle_receipt
from .storage import save_receipt


def ask(prompt: str, default: str = "") -> str:
    try:
        s = input(prompt).strip()
    except EOFError:
        s = ""
    return s or default


def main(argv: list[str] | None = None) -> None:
    argv = argv if argv is not None else sys.argv[1:]
    eml_path = argv[0] if argv else "data/tesco_eg1.eml"

    parsed = tesco.parse(open(eml_path, "rb").read())
    print(f"\nParsed {len(parsed.items)} items from {parsed.retailer} "
          f"order {parsed.order_ref} — total {fm(parsed.total_paid_pence)}")
    if not parsed.ok:
        print("  ⚠️  did not reconcile:", parsed.notes)

    names = [n.strip() for n in ask("\nHousehold members (comma-separated) [Rob,Sam]: ",
                                    "Rob,Sam").split(",") if n.strip()]
    members = [Member(n.lower(), n) for n in names]
    for i, m in enumerate(members, 1):
        print(f"   {i}. {m.name}")
    raw = ask("Who placed the order? [1]: ", "1")
    payer = members[int(raw) - 1] if raw.isdigit() and 1 <= int(raw) <= len(members) else members[0]
    print(f"   -> {payer.name} paid.\n")

    receipt = receipt_from_parsed(parsed, members=members, payer_id=payer.id,
                                  household_id="demo")

    mem = LocalMemory()
    name_of = {m.id: m.name for m in members}
    legend = "  ".join(f"[{i}]{m.name}" for i, m in enumerate(members, 1))
    print(f"Claim each item:  {legend}  [s]plit-all  [-]skip  [Enter]=accept 🔮 suggestion\n")
    for item in receipt.items:
        if item.is_overhead:
            continue
        suggestion = mem.suggest(receipt.household_id, item.name)
        hint = ""
        if suggestion:
            who = "+".join(name_of[x] for x in sorted(suggestion) if x in name_of)
            hint = f"  🔮 {who} ordered before"
        ans = ask(f"  {fm(item.total_pence):>7}  {item.name[:40]:40}{hint} -> ")
        if not ans:
            if suggestion:
                item.assign(suggestion)          # accept the remembered split
            continue
        if ans == "-":
            continue                             # explicit skip
        if ans.lower() == "s":
            item.assign({m.id for m in members})
        else:
            chosen = {members[int(t) - 1].id
                      for t in ans.replace(" ", ",").split(",")
                      if t.isdigit() and 1 <= int(t) <= len(members)}
            if chosen:
                item.assign(chosen)

    shares = compute_shares(receipt)
    print("\n" + "=" * 46)
    if not shares.fully_assigned:
        print(f"!! {len(shares.unassigned_items)} unclaimed "
              f"({fm(shares.unassigned_pence)}): {', '.join(shares.unassigned_items)}")
    print("Each person's share:")
    for m in members:
        print(f"   {m.name:10} {fm(shares.per_member[m.id])}")

    print("\nWho owes whom:")
    txns = settle_receipt(shares, payer.id)
    if not txns:
        print("   nothing to settle")
    for t in txns:
        frm = next(m.name for m in members if m.id == t.from_id)
        to = next(m.name for m in members if m.id == t.to_id)
        print(f"   {frm} -> {to}: {fm(t.pence)}")

    # Teach the memory what was claimed, so next week auto-drafts from it.
    for item in receipt.items:
        if not item.is_overhead and item.assigned_to:
            mem.remember(receipt.household_id, item.name, set(item.assigned_to))

    path = save_receipt(receipt, txns)
    print(f"\nSaved this shop -> {path}")


if __name__ == "__main__":
    main()
