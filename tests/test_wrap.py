import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from unittest.mock import patch

from alfred.bot.telegram_bot import alltimewrap
from alfred.wrap import _awards, _duration, render


class TestWrap(unittest.TestCase):
    def test_short_payment_times_display_in_minutes(self):
        self.assertEqual(_duration(0), "<1m")
        self.assertEqual(_duration(0.5), "30m")

    def test_awards_show_actual_products(self):
        receipts = [{
            "items": [{
                "name": "Tesco British Chicken Thighs 1Kg",
                "assigned_to": ["rob"],
            }],
        }]
        awards = _awards(receipts, {"rob": "Rob"})
        self.assertEqual(
            awards,
            ["🍗 Rob — Store Manager at Los Pollos Hermanos: British Chicken Thighs 1Kg"],
        )
        self.assertNotIn("(1 item)", awards[0])

    def test_payment_time_uses_settled_timestamp_not_order_timestamp(self):
        receipts = [{
            "id": "r1",
            "retailer": "Tesco",
            "total_paid_pence": 1000,
            "ordered_at": "2026-05-01T10:00:00+00:00",
            "settled_at": "2026-06-06T10:00:00+00:00",
            "paid_at": "2026-06-06T12:00:00+00:00",
            "members": [],
            "items": [],
        }]
        with (
            patch("alfred.wrap._load_receipts", return_value=receipts),
            patch("alfred.wrap._signature_splits", return_value=[]),
        ):
            text = render("household", "all")
        self.assertIn("Avg time to confirm payment: 2h", text)


class TestAllTimeWrapCommand(unittest.IsolatedAsyncioTestCase):
    async def test_alltimewrap_renders_complete_history(self):
        message = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(
            effective_chat=SimpleNamespace(id=-100),
            effective_message=message,
            message=message,
        )
        with (
            patch("alfred.bot.telegram_bot.access.is_activated", return_value=True),
            patch("alfred.bot.telegram_bot.wrap_render", return_value="all-time") as render_wrap,
        ):
            await alltimewrap(update, SimpleNamespace())
        render_wrap.assert_called_once_with("-100", "all")
        message.reply_text.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
