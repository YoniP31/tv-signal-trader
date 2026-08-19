"""Covers humanize.fast_mode() -- used by cli._run_add_accounts to speed up
pause()/long_pause()/type_humanlike() since TradingGenerator, unlike
TradingView/Tradovate, has no anti-bot reason to be paced at human speed.
Run with:

    python -m unittest tests.test_humanize_fast_mode -v
"""

import unittest
from unittest.mock import MagicMock, patch

from tv_signal_trader import humanize


class FastModeScalingTests(unittest.TestCase):
    def tearDown(self):
        # Guard against a failing assertion inside a `with fast_mode():`
        # block leaving the module-level flag stuck on for later tests.
        humanize._fast = False

    def test_pause_sleeps_the_full_duration_outside_fast_mode(self):
        with patch.object(humanize.time, "sleep") as sleep_mock, \
             patch.object(humanize.random, "uniform", return_value=0.5) as uniform_mock:
            humanize.pause(0.4, 0.9)
        uniform_mock.assert_called_once_with(0.4, 0.9)
        sleep_mock.assert_called_once_with(0.5)

    def test_pause_is_scaled_down_inside_fast_mode(self):
        with humanize.fast_mode():
            with patch.object(humanize.time, "sleep") as sleep_mock, \
                 patch.object(humanize.random, "uniform", return_value=0.05) as uniform_mock:
                humanize.pause(0.4, 0.9)
        uniform_mock.assert_called_once_with(0.4 * humanize._FAST_SCALE, 0.9 * humanize._FAST_SCALE)
        sleep_mock.assert_called_once_with(0.05)

    def test_long_pause_is_scaled_down_inside_fast_mode(self):
        with humanize.fast_mode():
            with patch.object(humanize.time, "sleep") as sleep_mock, \
                 patch.object(humanize.random, "uniform", return_value=0.1) as uniform_mock:
                humanize.long_pause(1.0, 2.5)
        uniform_mock.assert_called_once_with(1.0 * humanize._FAST_SCALE, 2.5 * humanize._FAST_SCALE)
        sleep_mock.assert_called_once_with(0.1)

    def test_fast_mode_flag_is_restored_after_the_block(self):
        self.assertFalse(humanize._fast)
        with humanize.fast_mode():
            self.assertTrue(humanize._fast)
        self.assertFalse(humanize._fast)

    def test_fast_mode_flag_is_restored_even_if_the_block_raises(self):
        with self.assertRaises(ValueError):
            with humanize.fast_mode():
                raise ValueError("boom")
        self.assertFalse(humanize._fast)

    def test_random_wait_is_unaffected_by_fast_mode(self):
        # A deliberately different purpose (polling/retry cadences) from
        # pause()/long_pause()'s UI-action pacing -- fast_mode() must not
        # touch it.
        with humanize.fast_mode():
            with patch.object(humanize.time, "sleep") as sleep_mock, \
                 patch.object(humanize.random, "uniform", return_value=42) as uniform_mock:
                humanize.random_wait(20, 300)
        uniform_mock.assert_called_once_with(20, 300)
        sleep_mock.assert_called_once_with(42)


class TypeHumanlikeFastModeTests(unittest.TestCase):
    def tearDown(self):
        humanize._fast = False

    def test_uses_the_slow_delay_range_by_default(self):
        element = MagicMock()
        with patch.object(humanize.time, "sleep") as sleep_mock, \
             patch.object(humanize.random, "uniform", return_value=0.1) as uniform_mock:
            humanize.type_humanlike(element, "ab")
        self.assertEqual(uniform_mock.call_args_list, [((0.08, 0.22),), ((0.08, 0.22),)])
        self.assertEqual(sleep_mock.call_count, 2)

    def test_uses_a_much_shorter_delay_range_in_fast_mode(self):
        element = MagicMock()
        with humanize.fast_mode():
            with patch.object(humanize.time, "sleep"), \
                 patch.object(humanize.random, "uniform", return_value=0.01) as uniform_mock:
                humanize.type_humanlike(element, "ab")
        self.assertEqual(uniform_mock.call_args_list, [((0.008, 0.022),), ((0.008, 0.022),)])

    def test_still_sends_every_character(self):
        element = MagicMock()
        with humanize.fast_mode():
            with patch.object(humanize.time, "sleep"):
                humanize.type_humanlike(element, "abc")
        self.assertEqual(
            [call.args[0] for call in element.send_keys.call_args_list], ["a", "b", "c"]
        )


if __name__ == "__main__":
    unittest.main()
