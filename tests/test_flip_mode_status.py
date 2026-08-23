"""Covers status.py's Flip Mode persistence: set_running_equity_target/
get_running_equity_target, set_cycle_start_date/get_cycle_start_date, and
set_cycle_starting_balance/get_cycle_starting_balance -- the per-account
state a restart shouldn't lose track of mid-state-machine (see the Flip
Mode plan). Run with:

    python -m unittest tests.test_flip_mode_status -v
"""

import os
import tempfile
import unittest
from unittest.mock import patch

from tv_signal_trader import status


class _TempStatusFileTestCase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.status_path = os.path.join(self._tmpdir.name, "status.json")
        self._patcher = patch.object(status, "STATUS_FILE", self.status_path)
        self._patcher.start()
        self.addCleanup(self._patcher.stop)


class RunningEquityTargetTests(_TempStatusFileTestCase):
    def test_defaults_to_none_for_an_unknown_account(self):
        self.assertIsNone(status.get_running_equity_target("Apex Trader Funding", "PAAPEX0001"))

    def test_set_then_get_round_trips(self):
        status.set_running_equity_target("Apex Trader Funding", "PAAPEX0001", 53000)
        self.assertEqual(status.get_running_equity_target("Apex Trader Funding", "PAAPEX0001"), 53000)

    def test_can_be_updated_to_a_new_value(self):
        status.set_running_equity_target("Apex Trader Funding", "PAAPEX0001", 53000)
        status.set_running_equity_target("Apex Trader Funding", "PAAPEX0001", 53500)
        self.assertEqual(status.get_running_equity_target("Apex Trader Funding", "PAAPEX0001"), 53500)

    def test_can_be_cleared_with_none(self):
        status.set_running_equity_target("Apex Trader Funding", "PAAPEX0001", 53000)
        status.set_running_equity_target("Apex Trader Funding", "PAAPEX0001", None)
        self.assertIsNone(status.get_running_equity_target("Apex Trader Funding", "PAAPEX0001"))

    def test_different_accounts_are_tracked_separately(self):
        status.set_running_equity_target("Apex Trader Funding", "PAAPEX0001", 53000)
        status.set_running_equity_target("TopStep", "TS0001", 27000)
        self.assertEqual(status.get_running_equity_target("Apex Trader Funding", "PAAPEX0001"), 53000)
        self.assertEqual(status.get_running_equity_target("TopStep", "TS0001"), 27000)

    def test_coexists_with_equity_history_for_the_same_account(self):
        status.record_daily_equity("Apex Trader Funding", "PAAPEX0001", date="2026-08-01", equity=50500)
        status.set_running_equity_target("Apex Trader Funding", "PAAPEX0001", 53000)
        self.assertEqual(
            status.get_equity_history("Apex Trader Funding", "PAAPEX0001"),
            [{"date": "2026-08-01", "equity": 50500}],
        )
        self.assertEqual(status.get_running_equity_target("Apex Trader Funding", "PAAPEX0001"), 53000)


class CycleStartDateTests(_TempStatusFileTestCase):
    def test_defaults_to_none_for_an_unknown_account(self):
        self.assertIsNone(status.get_cycle_start_date("Apex Trader Funding", "PAAPEX0001"))

    def test_set_then_get_round_trips(self):
        status.set_cycle_start_date("Apex Trader Funding", "PAAPEX0001", "2026-08-01")
        self.assertEqual(status.get_cycle_start_date("Apex Trader Funding", "PAAPEX0001"), "2026-08-01")

    def test_resetting_after_a_withdrawal_moves_it_forward(self):
        status.set_cycle_start_date("Apex Trader Funding", "PAAPEX0001", "2026-08-01")
        status.set_cycle_start_date("Apex Trader Funding", "PAAPEX0001", "2026-08-15")
        self.assertEqual(status.get_cycle_start_date("Apex Trader Funding", "PAAPEX0001"), "2026-08-15")


class CycleStartingBalanceTests(_TempStatusFileTestCase):
    def test_defaults_to_none_for_an_unknown_account(self):
        self.assertIsNone(status.get_cycle_starting_balance("Apex Trader Funding", "PAAPEX0001"))

    def test_set_then_get_round_trips(self):
        status.set_cycle_starting_balance("Apex Trader Funding", "PAAPEX0001", 50700.0)
        self.assertEqual(status.get_cycle_starting_balance("Apex Trader Funding", "PAAPEX0001"), 50700.0)

    def test_a_later_withdrawal_replaces_the_earlier_starting_balance(self):
        status.set_cycle_starting_balance("Apex Trader Funding", "PAAPEX0001", 50700.0)
        status.set_cycle_starting_balance("Apex Trader Funding", "PAAPEX0001", 54200.0)
        self.assertEqual(status.get_cycle_starting_balance("Apex Trader Funding", "PAAPEX0001"), 54200.0)

    def test_can_be_cleared_with_none(self):
        status.set_cycle_starting_balance("Apex Trader Funding", "PAAPEX0001", 50700.0)
        status.set_cycle_starting_balance("Apex Trader Funding", "PAAPEX0001", None)
        self.assertIsNone(status.get_cycle_starting_balance("Apex Trader Funding", "PAAPEX0001"))


if __name__ == "__main__":
    unittest.main()
