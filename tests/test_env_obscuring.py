"""Covers config.py's TradingGenerator-credential obscuring in .env
(_obscure/_unobscure, wired into set_env_values/_load_env_file). Run with:

    python -m unittest tests.test_env_obscuring -v

This is casual obscurity against someone glancing at .env, not real
encryption -- base64 is trivially reversible. The point of these tests is
the round-trip (what gets written is what you get back) and backward
compatibility with an existing plaintext .env from before this existed.
"""

import os
import tempfile
import unittest
from unittest.mock import patch

from tv_signal_trader import config


class ObscureHelperTests(unittest.TestCase):
    def test_obscure_then_unobscure_round_trips(self):
        for value in ("plain", "weird$chars!", "unicode-é-ü", ""):
            self.assertEqual(config._unobscure(config._obscure(value)), value)

    def test_unobscure_leaves_unprefixed_values_alone(self):
        # No "b64:" prefix -- e.g. a legacy plaintext value -- must pass
        # through unchanged rather than being guessed at.
        self.assertEqual(config._unobscure("plain_password"), "plain_password")

    def test_unobscure_falls_back_on_corrupted_payload(self):
        self.assertEqual(config._unobscure("b64:not valid base64!!"), "b64:not valid base64!!")


class EnvFileRoundTripTests(unittest.TestCase):
    """set_env_values -> disk -> _load_env_file, against a temp .env (never
    the real project .env, which holds real credentials)."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.env_path = os.path.join(self._tmpdir.name, ".env")
        self._env_file_patcher = patch.object(config, "ENV_FILE", self.env_path)
        self._env_file_patcher.start()
        self.addCleanup(self._env_file_patcher.stop)

    def test_tradinggenerator_credentials_are_obscured_on_disk(self):
        config.set_env_values({
            "TRADINGGENERATOR_USERNAME": "someone@example.com",
            "TRADINGGENERATOR_PASSWORD": "hunter2!weird$chars",
        })
        with open(self.env_path, encoding="utf-8") as f:
            raw = f.read()
        self.assertNotIn("someone@example.com", raw)
        self.assertNotIn("hunter2!weird$chars", raw)
        self.assertIn("TRADINGGENERATOR_USERNAME=b64:", raw)
        self.assertIn("TRADINGGENERATOR_PASSWORD=b64:", raw)

    def test_tradinggenerator_credentials_read_back_as_plaintext(self):
        config.set_env_values({
            "TRADINGGENERATOR_USERNAME": "someone@example.com",
            "TRADINGGENERATOR_PASSWORD": "hunter2!weird$chars",
        })
        loaded = config._load_env_file(self.env_path)
        self.assertEqual(loaded["TRADINGGENERATOR_USERNAME"], "someone@example.com")
        self.assertEqual(loaded["TRADINGGENERATOR_PASSWORD"], "hunter2!weird$chars")

    def test_other_keys_are_left_as_plain_text(self):
        # Only the two TradingGenerator keys are obscured -- Tradovate
        # accounts, MPPC, etc. are untouched.
        config.set_env_values({
            "MPPC": "5",
            "TRADOVATE_APEX_TRADER_FUNDING_USERNAME": "trader1",
        })
        with open(self.env_path, encoding="utf-8") as f:
            raw = f.read()
        self.assertIn("MPPC=5", raw)
        self.assertIn("TRADOVATE_APEX_TRADER_FUNDING_USERNAME=trader1", raw)

    def test_legacy_plaintext_env_still_loads_correctly(self):
        # A .env written before this feature existed -- no "b64:" prefix.
        with open(self.env_path, "w", encoding="utf-8") as f:
            f.write("TRADINGGENERATOR_USERNAME=legacy_plain_user\n")
            f.write("TRADINGGENERATOR_PASSWORD=legacy_plain_pass\n")
        loaded = config._load_env_file(self.env_path)
        self.assertEqual(loaded["TRADINGGENERATOR_USERNAME"], "legacy_plain_user")
        self.assertEqual(loaded["TRADINGGENERATOR_PASSWORD"], "legacy_plain_pass")

    def test_re_saving_a_legacy_value_obscures_it_going_forward(self):
        with open(self.env_path, "w", encoding="utf-8") as f:
            f.write("TRADINGGENERATOR_PASSWORD=legacy_plain_pass\n")
        # Simulates re-running 'setup' and keeping the current value (which
        # setup_wizard resolves to plaintext via get_env_value/_env, then
        # writes back through set_env_values unchanged).
        config.set_env_values({"TRADINGGENERATOR_PASSWORD": "legacy_plain_pass"})
        with open(self.env_path, encoding="utf-8") as f:
            raw = f.read()
        self.assertIn("TRADINGGENERATOR_PASSWORD=b64:", raw)
        self.assertNotIn("legacy_plain_pass", raw)


class ReadEnvValueFromDiskTests(unittest.TestCase):
    """read_env_value_from_disk/read_admin_code -- the one config value
    deliberately *not* cached, so it can be rotated on disk and take
    effect immediately even while web_multi holds the '>' prompt (and so
    'setup') hostage for its entire run."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.env_path = os.path.join(self._tmpdir.name, ".env")
        self._env_file_patcher = patch.object(config, "ENV_FILE", self.env_path)
        self._env_file_patcher.start()
        self.addCleanup(self._env_file_patcher.stop)

    def _write_env(self, content):
        with open(self.env_path, "w", encoding="utf-8") as f:
            f.write(content)

    def test_reads_a_plain_unobscured_value_written_by_hand(self):
        # No "b64:" prefix needed -- a value hand-typed straight into
        # .env (e.g. to deliberately break it for a live test) works
        # exactly like an already-obscured one.
        self._write_env("TRADINGGENERATOR_ADMIN_CODE=wrong-code-123\n")
        self.assertEqual(config.read_admin_code(), "wrong-code-123")

    def test_reads_a_properly_obscured_value(self):
        config.set_env_values({"TRADINGGENERATOR_ADMIN_CODE": "the-real-code"})
        self.assertEqual(config.read_admin_code(), "the-real-code")

    def test_reflects_a_hand_edit_without_reloading_the_cached_env(self):
        config.set_env_values({"TRADINGGENERATOR_ADMIN_CODE": "original-code"})
        # config._env (and config.TRADINGGENERATOR_ADMIN_CODE) now cache
        # "original-code" -- simulate hand-editing .env directly on disk,
        # bypassing set_env_values entirely, the way a user would to test
        # a wrong code live without restarting.
        self._write_env("TRADINGGENERATOR_ADMIN_CODE=edited-on-disk\n")
        # The cached module-level constant is unaffected...
        self.assertEqual(config.TRADINGGENERATOR_ADMIN_CODE, "original-code")
        # ...but a fresh read picks up the on-disk edit immediately.
        self.assertEqual(config.read_admin_code(), "edited-on-disk")

    def test_missing_key_returns_empty_string(self):
        self._write_env("SOME_OTHER_KEY=value\n")
        self.assertEqual(config.read_admin_code(), "")


if __name__ == "__main__":
    unittest.main()
