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


if __name__ == "__main__":
    unittest.main()
