"""Covers cli._startup_command -- the command-line argument (e.g.
`tv-signal-trader.exe web_multi`) that lets an unattended launcher (a
Scheduled Task, see the remote-deployment tooling) bring the bot fully
back up after an update or reboot, instead of leaving it idle at the '>'
prompt waiting for a human to type the command. Run with:

    python -m unittest tests.test_startup_command -v
"""

import unittest
from unittest.mock import patch

from tv_signal_trader import cli


class StartupCommandTests(unittest.TestCase):
    def test_no_argument_means_wait_at_the_prompt_as_usual(self):
        self.assertIsNone(cli._startup_command(["tv-signal-trader.exe"]))

    def test_web_multi_is_accepted(self):
        self.assertEqual(cli._startup_command(["tv-signal-trader.exe", "web_multi"]), "web_multi")

    def test_web_is_accepted(self):
        self.assertEqual(cli._startup_command(["tv-signal-trader.exe", "web"]), "web")

    def test_case_and_surrounding_whitespace_are_ignored(self):
        self.assertEqual(cli._startup_command(["tv-signal-trader.exe", "  WEB_MULTI "]), "web_multi")

    def test_interactive_commands_are_never_auto_run(self):
        # 'test'/'add_accounts'/'setup' prompt for input by nature -- an
        # unattended launcher must never end up running one.
        for interactive in ("test", "add_accounts", "setup", "quit"):
            with patch.object(cli, "print"):
                self.assertIsNone(cli._startup_command(["tv-signal-trader.exe", interactive]))

    def test_an_unknown_argument_is_warned_about_then_ignored(self):
        with patch.object(cli, "print") as print_mock:
            result = cli._startup_command(["tv-signal-trader.exe", "web_mutli"])
        self.assertIsNone(result)
        print_mock.assert_called_once()
        self.assertIn("web_mutli", print_mock.call_args.args[0])

    def test_extra_arguments_beyond_the_first_are_ignored(self):
        self.assertEqual(
            cli._startup_command(["tv-signal-trader.exe", "web_multi", "--whatever"]), "web_multi"
        )


class ResolveHideTgWindowTests(unittest.TestCase):
    """Whether TradingGenerator's window is hidden (True), a visible tab
    (False), or left to config.HIDE_TRADINGGENERATOR_WINDOW (None) -- and in
    particular that an auto-started run, where nobody can be asked, comes up
    visible by default."""

    def test_the_shipped_default_for_an_auto_started_run_is_visible(self):
        self.assertIs(cli.config.HIDE_TRADINGGENERATOR_WINDOW_WHEN_AUTO_STARTED, False)

    def test_an_auto_started_admin_run_is_visible_and_never_asks(self):
        with patch.object(cli.config, "IS_ADMIN_BUILD", True), \
             patch("builtins.input") as ask:
            self.assertIs(cli._resolve_hide_tg_window(ask=False), False)
        ask.assert_not_called()

    def test_the_auto_started_default_can_be_flipped_back_to_hidden_in_code(self):
        with patch.object(cli.config, "IS_ADMIN_BUILD", True), \
             patch.object(cli.config, "HIDE_TRADINGGENERATOR_WINDOW_WHEN_AUTO_STARTED", True):
            self.assertIs(cli._resolve_hide_tg_window(ask=False), True)

    def test_the_auto_started_choice_does_not_depend_on_the_interactive_default(self):
        with patch.object(cli.config, "IS_ADMIN_BUILD", True), \
             patch.object(cli.config, "HIDE_TRADINGGENERATOR_WINDOW", True):
            self.assertIs(cli._resolve_hide_tg_window(ask=False), False)

    def test_a_typed_command_still_asks_and_enter_keeps_the_config_default(self):
        with patch.object(cli.config, "IS_ADMIN_BUILD", True), \
             patch("builtins.input", return_value="") as ask:
            self.assertIsNone(cli._resolve_hide_tg_window(ask=True))
        ask.assert_called_once()

    def test_a_typed_command_honors_yes_and_no(self):
        with patch.object(cli.config, "IS_ADMIN_BUILD", True):
            with patch("builtins.input", return_value="y"):
                self.assertIs(cli._resolve_hide_tg_window(ask=True), False)
            with patch("builtins.input", return_value="n"):
                self.assertIs(cli._resolve_hide_tg_window(ask=True), True)

    def test_the_user_build_is_always_hidden_however_it_was_started(self):
        with patch.object(cli.config, "IS_ADMIN_BUILD", False), \
             patch.object(cli.config, "HIDE_TRADINGGENERATOR_WINDOW_WHEN_AUTO_STARTED", False), \
             patch("builtins.input") as ask:
            self.assertIs(cli._resolve_hide_tg_window(ask=False), True)
            self.assertIs(cli._resolve_hide_tg_window(ask=True), True)
        ask.assert_not_called()


if __name__ == "__main__":
    unittest.main()
