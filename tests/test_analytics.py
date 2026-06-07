import tempfile
import unittest

from alfred.analytics import Analytics
from scripts.usage_report import build_report


class TestAnalytics(unittest.TestCase):
    def test_tracks_anonymised_events_and_repeat_households(self):
        with tempfile.TemporaryDirectory() as root:
            analytics = Analytics(root)
            analytics.track("household_activated", chat_id=-100)
            analytics.track("member_joined", chat_id=-100, user_id=123)
            analytics.track("settlement_completed", chat_id=-100)
            analytics.track("settlement_completed", chat_id=-100)
            analytics.track("payment_confirmed", chat_id=-100, user_id=123)

            events = analytics.events()
            self.assertNotIn("-100", str(events))
            self.assertNotIn("123", str(events))
            report = build_report(root)["headline"]
            self.assertEqual(report["activated_households"], 1)
            self.assertEqual(report["joined_users"], 1)
            self.assertEqual(report["completed_settlements"], 2)
            self.assertEqual(report["repeat_households"], 1)


if __name__ == "__main__":
    unittest.main()
