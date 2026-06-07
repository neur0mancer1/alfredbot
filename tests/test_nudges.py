import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from alfred.bot.telegram_bot import automatic_nudge_text, due_automatic_nudges
from alfred.storage import mark_nudge_sent


class TestAutomaticNudges(unittest.TestCase):
    def make_receipt(self, root, *, elapsed_hours, stage=0):
        now = datetime(2026, 6, 6, 20, 0, tzinfo=timezone.utc)
        rec = {
            "id": "receipt1",
            "household_id": "-100",
            "retailer": "Tesco",
            "total_paid_pence": 1879,
            "status": "settled",
            "settled_at": (now - timedelta(hours=elapsed_hours)).isoformat(),
            "nudge_stage_hours": stage,
            "members": [
                {"id": "alex", "name": "Alex"},
                {"id": "sam", "name": "Sam"},
            ],
            "settlement": [{"from": "alex", "to": "sam", "pence": 580}],
        }
        folder = Path(root) / "-100"
        folder.mkdir()
        (folder / "receipt1.json").write_text(json.dumps(rec))
        return now

    def test_sends_highest_due_stage_only(self):
        with tempfile.TemporaryDirectory() as root:
            now = self.make_receipt(root, elapsed_hours=2.5)
            due = due_automatic_nudges(root, now)
            self.assertEqual([(chat, stage) for chat, _rec, stage in due], [(-100, 2)])

    def test_persisted_stage_prevents_resend_until_next_stage(self):
        with tempfile.TemporaryDirectory() as root:
            now = self.make_receipt(root, elapsed_hours=2.5)
            mark_nudge_sent("-100", "receipt1", 2, root)
            self.assertEqual(due_automatic_nudges(root, now), [])
            later = now + timedelta(hours=2)
            self.assertEqual(due_automatic_nudges(root, later)[0][2], 4)

    def test_non_chat_folders_are_ignored(self):
        with tempfile.TemporaryDirectory() as root:
            now = self.make_receipt(root, elapsed_hours=1.5)
            demo = Path(root) / "demo"
            demo.mkdir()
            (demo / "receipt.json").write_text("{}")
            self.assertEqual(len(due_automatic_nudges(root, now)), 1)

    def test_messages_become_more_direct(self):
        rec = {
            "members": [{"id": "a", "name": "Alex"}, {"id": "s", "name": "Sam"}],
            "settlement": [{"from": "s", "to": "a", "pence": 123}],
        }
        self.assertIn("gentle reminder", automatic_nudge_text(rec, 1))
        self.assertIn("firmer reminder", automatic_nudge_text(rec, 2))
        self.assertIn("Please settle", automatic_nudge_text(rec, 4))


if __name__ == "__main__":
    unittest.main()
