import tempfile
import unittest

from alfred.access import AccessStore


class TestAccessStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = AccessStore(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_code_activates_exactly_one_chat(self):
        code = self.store.issue(kind="paid")
        ok, _ = self.store.activate(code, -100)
        self.assertTrue(ok)
        self.assertTrue(self.store.is_activated(-100))

        ok, message = self.store.activate(code, -200)
        self.assertFalse(ok)
        self.assertIn("already been used", message)

    def test_invalid_code_fails(self):
        ok, message = self.store.activate("NOPE", -100)
        self.assertFalse(ok)
        self.assertIn("invalid", message)
