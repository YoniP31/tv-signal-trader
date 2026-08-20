"""Covers cli._next_restart_streak -- the rapid-restart safety net behind
'web'/'web_multi' full-relaunch auto-restart (see
cli._run_trading_loop_with_auto_restart). The browser-lifecycle glue itself
(_relaunch_browser, _run_trading_loop_with_auto_restart) isn't unit tested
here since it's a real Selenium/thread integration, same as the rest of
main() -- this covers the one piece of actual decision logic in isolation.
Run with:

    python -m unittest tests.test_auto_restart -v
"""

import unittest

from tv_signal_trader import cli


class NextRestartStreakTests(unittest.TestCase):
    def test_a_quick_stop_increments_the_streak(self):
        self.assertEqual(cli._next_restart_streak(1.0, 0), 1)
        self.assertEqual(cli._next_restart_streak(1.0, 1), 2)

    def test_a_stop_right_at_the_window_boundary_still_increments(self):
        self.assertEqual(
            cli._next_restart_streak(cli._RAPID_RESTART_WINDOW_SECONDS - 0.001, 2), 3
        )

    def test_a_long_run_resets_the_streak(self):
        self.assertEqual(cli._next_restart_streak(cli._RAPID_RESTART_WINDOW_SECONDS, 5), 0)
        self.assertEqual(cli._next_restart_streak(cli._RAPID_RESTART_WINDOW_SECONDS + 1000, 5), 0)

    def test_streak_reaches_the_give_up_threshold_after_enough_rapid_stops(self):
        streak = 0
        for _ in range(cli._MAX_RAPID_RESTARTS):
            streak = cli._next_restart_streak(0.5, streak)
        self.assertGreaterEqual(streak, cli._MAX_RAPID_RESTARTS)

    def test_a_single_long_run_between_rapid_stops_keeps_it_below_threshold(self):
        streak = cli._next_restart_streak(0.5, 0)
        streak = cli._next_restart_streak(0.5, streak)
        # A long, healthy run resets it back down before any further rapid
        # stops can accumulate toward the threshold.
        streak = cli._next_restart_streak(cli._RAPID_RESTART_WINDOW_SECONDS + 1, streak)
        self.assertEqual(streak, 0)


if __name__ == "__main__":
    unittest.main()
