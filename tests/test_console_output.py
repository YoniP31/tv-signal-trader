"""Covers cli._console_suppressed_for_this_build -- whether the regular-user
build's web/web_multi runs silently. It now prints everything the admin
build does by default (config.SUPPRESS_CONSOLE_IN_USER_BUILD = False); the
old silent behavior remains one hard toggle away. Run with:

    python -m unittest tests.test_console_output -v
"""

import unittest
from unittest.mock import patch

from tv_signal_trader import cli


class ConsoleSuppressionTests(unittest.TestCase):
    def test_the_shipped_default_is_full_console_output(self):
        self.assertIs(cli.config.SUPPRESS_CONSOLE_IN_USER_BUILD, False)

    def test_the_user_build_prints_everything_by_default(self):
        with patch.object(cli.config, "IS_ADMIN_BUILD", False):
            self.assertFalse(cli._console_suppressed_for_this_build())

    def test_the_user_build_is_silent_again_when_the_toggle_is_switched_on(self):
        with patch.object(cli.config, "IS_ADMIN_BUILD", False), \
             patch.object(cli.config, "SUPPRESS_CONSOLE_IN_USER_BUILD", True):
            self.assertTrue(cli._console_suppressed_for_this_build())

    def test_the_admin_build_is_never_suppressed_whatever_the_toggle(self):
        with patch.object(cli.config, "IS_ADMIN_BUILD", True):
            for value in (False, True):
                with patch.object(cli.config, "SUPPRESS_CONSOLE_IN_USER_BUILD", value):
                    self.assertFalse(cli._console_suppressed_for_this_build())


if __name__ == "__main__":
    unittest.main()
