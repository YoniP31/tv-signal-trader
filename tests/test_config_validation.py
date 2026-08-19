"""Covers config.validate_env() (flags malformed .env values with a clear
message + example) and the safe-parsing fallbacks in config._reload_env()
that keep a malformed value from crashing the app outright. Run with:

    python -m unittest tests.test_config_validation -v
"""

import os
import tempfile
import unittest
from unittest.mock import patch

from tv_signal_trader import config


class _TempEnvFileTestCase(unittest.TestCase):
    """Base class for tests that need config._reload_env() to load from a
    throwaway .env rather than the real project one. Restores config to
    the real .env afterward (in the right order: the ENV_FILE patch must
    be undone *before* reloading, otherwise the reload would read from the
    temp file right before it's deleted)."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.env_path = os.path.join(self._tmpdir.name, ".env")
        self.addCleanup(config._reload_env)
        self._env_file_patcher = patch.object(config, "ENV_FILE", self.env_path)
        self._env_file_patcher.start()
        self.addCleanup(self._env_file_patcher.stop)

    def _write_env(self, content):
        with open(self.env_path, "w", encoding="utf-8") as f:
            f.write(content)
        config._reload_env()


class ValidateEnvTests(_TempEnvFileTestCase):
    def test_blank_env_has_no_problems(self):
        self._write_env("")
        self.assertEqual(config.validate_env(), [])

    def test_well_formed_values_have_no_problems(self):
        self._write_env(
            "SESSION_START_TIME=09:00\n"
            "SESSION_END_TIME=17:00\n"
            "NO_TRADE_START_TIME=12:00\n"
            "NO_TRADE_END_TIME=13:00\n"
            "MPPC=5\n"
            "DAILY_PROFIT_LIMIT=500\n"
            "DAILY_LOSS_LIMIT=300\n"
            "TP_CAP_BUFFER_MIN=50\n"
            "TP_CAP_BUFFER_MAX=200\n"
            "ACCOUNT_25K_MIN_BALANCE=23000\n"
        )
        self.assertEqual(config.validate_env(), [])

    def test_commented_out_lines_are_ignored(self):
        self._write_env("#SESSION_START_TIME=bogus\n")
        self.assertEqual(config.validate_env(), [])

    def test_malformed_time_is_flagged_with_an_hh_mm_example(self):
        self._write_env("SESSION_START_TIME=9:30am\nSESSION_END_TIME=17:00\n")
        problems = config.validate_env()
        keys = [p[0] for p in problems]
        self.assertIn("SESSION_START_TIME", keys)
        key, raw, message, example = next(p for p in problems if p[0] == "SESSION_START_TIME")
        self.assertEqual(raw, "9:30am")
        self.assertIn("HH:MM", message)
        self.assertIn("HH:MM", example)

    def test_malformed_number_is_flagged(self):
        self._write_env("DAILY_PROFIT_LIMIT=abc\n")
        problems = config.validate_env()
        self.assertEqual([p[0] for p in problems], ["DAILY_PROFIT_LIMIT"])

    def test_non_numeric_mppc_is_flagged(self):
        self._write_env("MPPC=three\n")
        problems = config.validate_env()
        self.assertEqual([p[0] for p in problems], ["MPPC"])

    def test_zero_mppc_is_flagged_as_not_positive(self):
        self._write_env("MPPC=0\n")
        problems = config.validate_env()
        self.assertEqual([p[0] for p in problems], ["MPPC"])
        self.assertIn("greater than 0", problems[0][2])

    def test_one_sided_session_window_is_flagged(self):
        self._write_env("SESSION_START_TIME=09:00\n")
        problems = config.validate_env()
        keys = [p[0] for p in problems]
        self.assertIn("SESSION_END_TIME", keys)
        self.assertNotIn("SESSION_START_TIME", keys)

    def test_one_sided_no_trade_window_is_flagged(self):
        self._write_env("NO_TRADE_END_TIME=13:00\n")
        problems = config.validate_env()
        keys = [p[0] for p in problems]
        self.assertIn("NO_TRADE_START_TIME", keys)

    def test_both_sides_of_a_window_set_is_not_flagged(self):
        self._write_env("SESSION_START_TIME=09:00\nSESSION_END_TIME=17:00\n")
        self.assertEqual(config.validate_env(), [])

    def test_malformed_balance_tier_is_flagged(self):
        self._write_env("ACCOUNT_25K_MAX_BALANCE_EVAL=lots\n")
        problems = config.validate_env()
        self.assertEqual([p[0] for p in problems], ["ACCOUNT_25K_MAX_BALANCE_EVAL"])


class SafeParsingFallbackTests(_TempEnvFileTestCase):
    """_reload_env() must never crash on a malformed value -- it falls back
    to the built-in default instead, so the app stays usable (with just
    that one setting reverted) even before validate_env() gets a chance to
    flag it to the user."""

    def test_malformed_mppc_falls_back_to_the_default(self):
        self._write_env("MPPC=three\n")
        self.assertEqual(config.MAX_POSITIONS_PER_COMPANY, config._DEFAULT_MAX_POSITIONS_PER_COMPANY)

    def test_zero_mppc_falls_back_to_the_default(self):
        self._write_env("MPPC=0\n")
        self.assertEqual(config.MAX_POSITIONS_PER_COMPANY, config._DEFAULT_MAX_POSITIONS_PER_COMPANY)

    def test_malformed_daily_profit_limit_falls_back_to_unset(self):
        self._write_env("DAILY_PROFIT_LIMIT=abc\n")
        self.assertIsNone(config.DAILY_PROFIT_LIMIT)

    def test_malformed_balance_tier_falls_back_to_the_default(self):
        self._write_env("ACCOUNT_25K_MIN_BALANCE=nope\n")
        self.assertEqual(config.ACCOUNT_BALANCE_TIERS[25000]['min'], config._DEFAULT_ACCOUNT_TIER_MIN[25000])

    def test_malformed_time_leaves_the_window_unset_rather_than_crashing(self):
        self._write_env("SESSION_START_TIME=bogus\nSESSION_END_TIME=17:00\n")
        self.assertIsNone(config.SESSION_START_TIME)


if __name__ == "__main__":
    unittest.main()
