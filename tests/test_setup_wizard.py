"""Covers setup_wizard.py: the "skip" option on the Tradovate
company-selection prompt, and check_env_validity()'s interactive fix flow
for malformed .env values. Run with:

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


class ChoosePropFirmSkipTests(_TempEnvFileTestCase):
    def test_typing_skip_returns_none(self):
        with patch("builtins.input", return_value="skip"):
            result = setup_wizard._choose_prop_firm()
        self.assertIsNone(result)

    def test_skip_is_case_insensitive(self):
        with patch("builtins.input", return_value="SKIP"):
            result = setup_wizard._choose_prop_firm()
        self.assertIsNone(result)

    def test_a_normal_number_still_selects_a_company(self):
        with patch("builtins.input", return_value="2"):
            result = setup_wizard._choose_prop_firm()
        self.assertEqual(result, "TopStep")

    def test_an_invalid_entry_is_rejected_before_reaching_skip_or_a_number(self):
        with patch("builtins.input", side_effect=["bogus", "skip"]):
            result = setup_wizard._choose_prop_firm()
        self.assertIsNone(result)


class AddTradovateAccountSkipTests(_TempEnvFileTestCase):
    def test_skipping_company_selection_never_prompts_for_credentials(self):
        # If a username/password prompt happened too, this single-item
        # side_effect would be exhausted and input() would raise
        # StopIteration.
        with patch("builtins.input", side_effect=["skip"]):
            result = setup_wizard._add_tradovate_account()
        self.assertFalse(result)
        self.assertEqual(config.TRADOVATE_ACCOUNTS, {})

    def test_providing_both_configures_the_account(self):
        with patch("builtins.input", side_effect=["2", "trader1", "hunter2"]):
            result = setup_wizard._add_tradovate_account()
        self.assertTrue(result)
        self.assertIn("TopStep", config.TRADOVATE_ACCOUNTS)
        self.assertEqual(config.TRADOVATE_ACCOUNTS["TopStep"]["username"], "trader1")


class ManageTradovateAccountsSkipTests(_TempEnvFileTestCase):
    def test_skipping_the_first_selection_exits_without_asking_to_add_another(self):
        # If "Add/update another?" were asked, this single-item side_effect
        # would be exhausted and input() would raise StopIteration.
        with patch("builtins.input", side_effect=["skip"]):
            setup_wizard._manage_tradovate_accounts()
        self.assertEqual(config.TRADOVATE_ACCOUNTS, {})


class SetPasswordFieldLabelTests(_TempEnvFileTestCase):
    def test_uses_the_custom_field_label_in_the_prompt(self):
        with patch("builtins.input", return_value="abc123") as mock_input:
            setup_wizard._set_password(
                "TradingGenerator", "TRADINGGENERATOR_ADMIN_CODE", field_label="admin code"
            )
        mock_input.assert_called_once_with("TradingGenerator admin code: ")
        self.assertEqual(config.get_env_value("TRADINGGENERATOR_ADMIN_CODE"), "abc123")

    def test_rejects_an_empty_value_before_accepting_one(self):
        with patch("builtins.input", side_effect=["", "abc123"]):
            setup_wizard._set_password(
                "TradingGenerator", "TRADINGGENERATOR_ADMIN_CODE", field_label="admin code"
            )
        self.assertEqual(config.get_env_value("TRADINGGENERATOR_ADMIN_CODE"), "abc123")

    def test_default_field_label_is_still_plain_password(self):
        with patch("builtins.input", return_value="hunter2") as mock_input:
            setup_wizard._set_password("TradingGenerator", "TRADINGGENERATOR_PASSWORD")
        mock_input.assert_called_once_with("TradingGenerator password: ")


class EnsureConfiguredAdminCodeTests(_TempEnvFileTestCase):
    def test_prompts_for_the_admin_code_when_missing(self):
        config.set_env_values({
            "TRADINGGENERATOR_USERNAME": "user1",
            "TRADINGGENERATOR_PASSWORD": "pass1",
        })
        # First answer is the admin code; second is "skip" for the
        # Tradovate-account prompt ensure_configured() falls into next
        # since none are configured yet in this temp .env.
        with patch("builtins.input", side_effect=["secret123", "skip"]):
            setup_wizard.ensure_configured()
        self.assertEqual(config.TRADINGGENERATOR_ADMIN_CODE, "secret123")

    def test_does_not_prompt_again_once_already_set(self):
        config.set_env_values({
            "TRADINGGENERATOR_USERNAME": "user1",
            "TRADINGGENERATOR_PASSWORD": "pass1",
            "TRADINGGENERATOR_ADMIN_CODE": "secret123",
        })
        with patch("builtins.input", side_effect=["skip"]) as mock_input:
            setup_wizard.ensure_configured()
        # Only the Tradovate-account "skip" prompt -- nothing for the
        # admin code, since it was already set.
        mock_input.assert_called_once()


class CheckEnvValidityTests(_TempEnvFileTestCase):
    def setUp(self):
        super().setUp()
        # The two daily limits are required -- a temp .env without them is
        # never "valid" now, so every test here starts from one that has
        # them (the required-setting behavior itself is covered below).
        config.set_env_values({"DAILY_PROFIT_LIMIT": "500", "DAILY_LOSS_LIMIT": "300"})

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


class RequiredEnvSettingsTests(_TempEnvFileTestCase):
    """The daily profit/loss limits (config.REQUIRED_ENV_KEYS) can't be
    left blank, and -- unlike every other problem -- can never be skipped
    past with "continue anyway": that escape hatch means "use the built-in
    default", and for these there deliberately isn't one."""

    def test_missing_limits_are_prompted_for_and_saved(self):
        # Order follows config._ENV_FIELD_SPECS: profit limit, then loss limit.
        with patch("builtins.input", side_effect=["500", "300"]):
            result = setup_wizard.check_env_validity()
        self.assertTrue(result)
        self.assertEqual(config.get_env_value("DAILY_PROFIT_LIMIT"), "500")
        self.assertEqual(config.get_env_value("DAILY_LOSS_LIMIT"), "300")
        self.assertEqual(config.validate_env(), [])

    def test_a_blank_limit_the_user_skips_aborts_the_command(self):
        # Skipping the profit limit, filling in the loss limit. No third
        # prompt: the "continue anyway?" question must never be asked.
        with patch("builtins.input", side_effect=["", "300"]) as mock_input:
            result = setup_wizard.check_env_validity()
        self.assertFalse(result)
        self.assertEqual(mock_input.call_count, 2)
        self.assertEqual(config.get_env_value("DAILY_LOSS_LIMIT"), "300")
        self.assertEqual(config.get_env_value("DAILY_PROFIT_LIMIT"), "")

    def test_skipping_both_limits_never_offers_to_continue_anyway(self):
        # If "continue anyway?" were asked there'd be a third input() call,
        # and this side_effect list would run out and raise.
        with patch("builtins.input", side_effect=["", ""]) as mock_input:
            result = setup_wizard.check_env_validity()
        self.assertFalse(result)
        self.assertEqual(mock_input.call_count, 2)

    def test_a_malformed_required_limit_that_stays_unfixed_also_aborts(self):
        config.set_env_values({"DAILY_PROFIT_LIMIT": "lots", "DAILY_LOSS_LIMIT": "300"})
        with patch("builtins.input", side_effect=[""]) as mock_input:
            result = setup_wizard.check_env_validity()
        self.assertFalse(result)
        self.assertEqual(mock_input.call_count, 1)
        self.assertEqual(config.get_env_value("DAILY_PROFIT_LIMIT"), "lots")

    def test_re_entering_an_invalid_required_value_is_rejected_before_moving_on(self):
        config.set_env_values({"DAILY_LOSS_LIMIT": "300"})
        with patch("builtins.input", side_effect=["five hundred", "500"]):
            result = setup_wizard.check_env_validity()
        self.assertTrue(result)
        self.assertEqual(config.get_env_value("DAILY_PROFIT_LIMIT"), "500")

    def test_the_prompt_says_the_setting_is_required_not_skippable(self):
        with patch("builtins.input", side_effect=["500", "300"]) as mock_input:
            setup_wizard.check_env_validity()
        prompts = [call.args[0] for call in mock_input.call_args_list]
        self.assertTrue(all("required" in p for p in prompts), prompts)
        self.assertFalse(any("Enter to skip" in p for p in prompts), prompts)

    def test_an_optional_setting_still_gets_the_continue_anyway_option(self):
        # Required-ness must not have removed the escape hatch for
        # everything else.
        config.set_env_values({
            "DAILY_PROFIT_LIMIT": "500", "DAILY_LOSS_LIMIT": "300", "MPPC": "three",
        })
        with patch("builtins.input", side_effect=["", "y"]):
            self.assertTrue(setup_wizard.check_env_validity())


if __name__ == "__main__":
    unittest.main()
