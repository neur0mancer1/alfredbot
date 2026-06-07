"""Settlement + allocation maths — the money has to be exact, so we prove it."""

import unittest

from alfred.money import allocate_pence
from alfred.models import LineItem, Member, Receipt
from alfred.settlement import compute_shares, settle_receipt


def make_receipt(members, items, payer):
    """items: list of (name, pence, assigned_set_or_None, is_overhead?)."""
    line_items = []
    for name, pence, assigned, *rest in items:
        line_items.append(
            LineItem(
                name=name,
                total_pence=pence,
                assigned_to=(frozenset(assigned) if assigned is not None else None),
                is_overhead=bool(rest and rest[0]),
            )
        )
    return Receipt(
        id="t", household_id="h", retailer="Tesco", payer_id=payer,
        members=[Member(m, m.title()) for m in members],
        items=line_items,
        stated_total_pence=sum(p for _, p, *_ in items),
    )


class TestAllocate(unittest.TestCase):
    def test_thirds_sum_exactly(self):
        self.assertEqual(allocate_pence(100, [1, 1, 1]), [34, 33, 33])
        self.assertEqual(sum(allocate_pence(100, [1, 1, 1])), 100)

    def test_clean_division(self):
        self.assertEqual(allocate_pence(90, [1, 1, 1]), [30, 30, 30])


class TestShares(unittest.TestCase):
    def test_personal_split_and_overhead(self):
        r = make_receipt(
            ["rob", "sam"],
            [
                ("mince", 519, {"rob"}),
                ("avocado", 76, {"sam"}),
                ("milk", 175, {"rob", "sam"}),       # odd pence -> 88/87
                ("delivery", 300, None, True),        # overhead -> auto-split 150/150
            ],
            payer="rob",
        )
        s = compute_shares(r)
        self.assertTrue(s.fully_assigned)
        self.assertEqual(s.per_member["rob"], 519 + 88 + 150)
        self.assertEqual(s.per_member["sam"], 76 + 87 + 150)
        self.assertEqual(sum(s.per_member.values()), 519 + 76 + 175 + 300)

    def test_unassigned_is_tracked_not_split(self):
        r = make_receipt(["rob", "sam"], [("mystery box", 999, None)], payer="rob")
        s = compute_shares(r)
        self.assertFalse(s.fully_assigned)
        self.assertEqual(s.unassigned_pence, 999)
        self.assertEqual(s.unassigned_items, ["mystery box"])

    def test_settle_payer_is_owed(self):
        r = make_receipt(["rob", "sam"], [("mince", 519, {"sam"})], payer="rob")
        txns = settle_receipt(compute_shares(r), "rob")
        self.assertEqual(len(txns), 1)
        self.assertEqual((txns[0].from_id, txns[0].to_id, txns[0].pence), ("sam", "rob", 519))


if __name__ == "__main__":
    unittest.main()
