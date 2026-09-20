"""Covers the 'test' submenu's withdrawal_status/submit_withdrawal
commands (cli._run_test_menu) -- confirms they're actually wired up to
tg.is_withdrawal_submitted/tg.submit_withdrawal (see
tests/test_withdrawal_button.py for those functions' own behavior),
the same way test_flip_mode_buttons.py's functions are covered directly
but nothing yet covered the menu wiring itself for any command. Drives
_run_test_menu the same way a person would at the "test>" prompt: the
command name, then Enter to confirm, then "back" to exit the loop
afterward. Run with:

    python -m unittest tests.test_withdrawal_test_commands -v
"""

import unittest
from unittest.mock import MagicMock, patch

from tv_signal_trader import cli


class WithdrawalTestCommandsTests(unittest.TestCase):
    def setUp(self):
        self.driver = MagicMock()
        self.tv_tab = "tv_tab"
        self._patches = [
            patch.object(cli.tg, "open_tab", return_value="web_tab"),
            patch.object(cli.tg, "ensure_logged_in", return_value=True),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)

    def _run_command(self, command_name):
        with patch("builtins.input", side_effect=[command_name, "", "back"]):
            cli._run_test_menu(self.driver, self.tv_tab)

    def test_withdrawal_status_command_is_listed(self):
        # cli.py binds its own module-level `print` to logging_utils.
        # timestamped_print (see its import), so builtins.print is never
        # actually called here -- patch cli's own name instead.
        with patch("builtins.input", side_effect=["back"]), \
             patch.object(cli, "print") as print_mock:
            cli._run_test_menu(self.driver, self.tv_tab)
        listed = " ".join(str(call.args[0]) for call in print_mock.call_args_list if call.args)
        self.assertIn("withdrawal_status", listed)
        self.assertIn("submit_withdrawal", listed)

    def test_withdrawal_status_reads_and_prints_the_live_state(self):
        with patch.object(cli.tg, "is_withdrawal_submitted", return_value=True) as is_submitted_mock:
            self._run_command("withdrawal_status")
        is_submitted_mock.assert_called_once_with(self.driver)

    def test_submit_withdrawal_clicks_with_the_live_admin_code(self):
        with patch.object(cli.tg, "submit_withdrawal", return_value=True) as submit_mock, \
             patch.object(cli.config, "read_admin_code", return_value="the-code"):
            self._run_command("submit_withdrawal")
        submit_mock.assert_called_once_with(self.driver, "the-code")

    def test_a_test_command_raising_does_not_crash_the_menu(self):
        # Same safety net every command in this menu already gets (see
        # _run_test_menu's own try/except around handler()) -- worth
        # confirming it also covers these two new ones.
        with patch.object(cli.tg, "is_withdrawal_submitted", side_effect=Exception("boom")), \
             patch.object(cli.logging_utils, "log_exception") as log_mock:
            self._run_command("withdrawal_status")
        log_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
