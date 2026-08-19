"""Covers setup_wizard.check_env_validity()'s interactive fix flow for
malformed .env values. Run with:

    python -m unittest tests.test_setup_wizard -v
"""

import os
import tempfile
import unittest
from unittest.mock import patch

from tv_signal_trader import config
from tv_signal_trader import setup_wizard


class _TempEnvFileTestCase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.env_path = os.path.join(self._tmpdir.name, ".env")
        self.addCleanup(config._reload_env)
        self._env_file_patcher = patch.object(config, "ENV_FILE", self.env_path)
        self._env_file_patcher.start()
        self.addCleanup(self._env_file_patcher.stop)
        config._reload_env()


class CheckEnvValidityTests(_TempEnvFileTestCase):
    def test_returns_true_immediately_when_nothing_is_wrong(self):
        with patch("builtins.input") as mock_input:
            result = setup_wizard.check_env_validity()
        self.assertTrue(result)
        mock_input.assert_not_called()

    def test_fixing_the_bad_value_returns_true(self):
        config.set_env_values({"MPPC": "three"})
        with patch("builtins.input", return_value="5"):
            result = setup_wizard.check_env_validity()
        self.assertTrue(result)
        self.assertEqual(config.get_env_value("MPPC"), "5")
        self.assertEqual(config.validate_env(), [])

    def test_skipping_then_declining_to_continue_returns_false(self):
        config.set_env_values({"MPPC": "three"})
        # Enter (skip the fix) -> "n" (don't continue anyway).
        with patch("builtins.input", side_effect=["", "n"]):
            result = setup_wizard.check_env_validity()
        self.assertFalse(result)
        # Left untouched -- still the original bad value on disk.
        self.assertEqual(config.get_env_value("MPPC"), "three")

    def test_skipping_then_continuing_anyway_returns_true(self):
        config.set_env_values({"MPPC": "three"})
        with patch("builtins.input", side_effect=["", "y"]):
            result = setup_wizard.check_env_validity()
        self.assertTrue(result)
        self.assertEqual(config.get_env_value("MPPC"), "three")

    def test_re_entering_an_invalid_value_is_rejected_before_moving_on(self):
        config.set_env_values({"MPPC": "three"})
        # First attempt "also bad" is still invalid -> re-prompted; second
        # attempt "4" is valid.
        with patch("builtins.input", side_effect=["also bad", "4"]):
            result = setup_wizard.check_env_validity()
        self.assertTrue(result)
        self.assertEqual(config.get_env_value("MPPC"), "4")


if __name__ == "__main__":
    unittest.main()
