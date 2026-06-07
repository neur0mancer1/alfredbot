"""Authorization rules for confirming that a settlement was paid."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from alfred.bot.telegram_bot import can_confirm_payment, on_paid


class TestPaymentConfirmationAuthorization(unittest.TestCase):
    def test_only_recorded_payer_can_confirm(self):
        receipt = {"payer_tg_id": 123}
        self.assertTrue(can_confirm_payment(receipt, 123))
        self.assertFalse(can_confirm_payment(receipt, 456))

    def test_missing_payer_identity_fails_closed(self):
        self.assertFalse(can_confirm_payment({"payer_tg_id": None}, 123))
        self.assertFalse(can_confirm_payment({}, 123))


class TestPaymentConfirmationCallback(unittest.IsolatedAsyncioTestCase):
    def make_update(self, user_id):
        query = SimpleNamespace(
            data="paid:receipt1",
            from_user=SimpleNamespace(id=user_id),
            message=SimpleNamespace(
                chat=SimpleNamespace(id=-100),
                message_id=42,
                text="Status: ⏳ awaiting payment",
            ),
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        return SimpleNamespace(
            callback_query=query, effective_chat=query.message.chat
        ), query

    async def test_non_payer_cannot_mark_receipt_paid(self):
        update, query = self.make_update(user_id=456)
        ctx = SimpleNamespace(bot=SimpleNamespace(pin_chat_message=AsyncMock()))
        with (
            patch("alfred.bot.telegram_bot.access.is_activated", return_value=True),
            patch("alfred.bot.telegram_bot.load_receipt", return_value={"payer_tg_id": 123}),
            patch("alfred.bot.telegram_bot.mark_receipt_paid") as mark_paid,
        ):
            await on_paid(update, ctx)

        mark_paid.assert_not_called()
        query.answer.assert_awaited_once_with(
            "Only the payer can confirm they've been paid back.", show_alert=True
        )

    async def test_payer_marks_paid_and_pins_summary(self):
        update, query = self.make_update(user_id=123)
        pin = AsyncMock()
        ctx = SimpleNamespace(bot=SimpleNamespace(pin_chat_message=pin))
        receipt = {
            "payer_tg_id": 123,
            "payer_id": "rob",
            "members": [{"id": "rob", "name": "Rob"}],
            "status": "settled",
        }
        with (
            patch("alfred.bot.telegram_bot.access.is_activated", return_value=True),
            patch("alfred.bot.telegram_bot.load_receipt", return_value=receipt),
            patch("alfred.bot.telegram_bot.mark_receipt_paid", return_value=receipt) as mark_paid,
        ):
            await on_paid(update, ctx)

        mark_paid.assert_called_once_with("-100", "receipt1")
        pin.assert_awaited_once_with(-100, 42, disable_notification=True)
        query.answer.assert_awaited_once_with("Payment confirmed and summary pinned 🎩")


if __name__ == "__main__":
    unittest.main()
