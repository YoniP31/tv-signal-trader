"""Covers status.record_daily_equity/get_equity_history -- the persisted
per-account equity record Flip Mode's day-count condition and Second
Withdrawal detection are both built on. Run with:

    python -m unittest tests.test_daily_equity_history -v
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


class RecordDailyEquityTests(_TempStatusFileTestCase):
    def test_first_record_appears_in_history(self):
        status.record_daily_equity("Apex Trader Funding", "PAAPEX0001", date="2026-08-01", equity=50500)
        self.assertEqual(
            status.get_equity_history("Apex Trader Funding", "PAAPEX0001"),
            [{"date": "2026-08-01", "equity": 50500}],
        )

    def test_multiple_days_accumulate_in_order(self):
        status.record_daily_equity("Apex Trader Funding", "PAAPEX0001", date="2026-08-01", equity=50500)
        status.record_daily_equity("Apex Trader Funding", "PAAPEX0001", date="2026-08-02", equity=51200)
        self.assertEqual(
            status.get_equity_history("Apex Trader Funding", "PAAPEX0001"),
            [{"date": "2026-08-01", "equity": 50500}, {"date": "2026-08-02", "equity": 51200}],
        )

    def test_calling_again_for_the_same_date_does_not_duplicate(self):
        status.record_daily_equity("Apex Trader Funding", "PAAPEX0001", date="2026-08-01", equity=50500)
        status.record_daily_equity("Apex Trader Funding", "PAAPEX0001", date="2026-08-01", equity=99999)
        history = status.get_equity_history("Apex Trader Funding", "PAAPEX0001")
        self.assertEqual(len(history), 1)
        # The original reading is kept -- a same-day re-call doesn't overwrite it.
        self.assertEqual(history[0]["equity"], 50500)

    def test_different_accounts_are_tracked_separately(self):
        status.record_daily_equity("Apex Trader Funding", "PAAPEX0001", date="2026-08-01", equity=50500)
        status.record_daily_equity("TopStep", "TS0001", date="2026-08-01", equity=25100)
        self.assertEqual(
            status.get_equity_history("Apex Trader Funding", "PAAPEX0001"),
            [{"date": "2026-08-01", "equity": 50500}],
        )
        self.assertEqual(
            status.get_equity_history("TopStep", "TS0001"),
            [{"date": "2026-08-01", "equity": 25100}],
        )

    def test_history_survives_independently_of_the_portfolios_dict(self):
        # Deliberately not stored under the "portfolios" dict (which
        # mirrors TradingGenerator's own current portfolio list and
        # disappears on removal) -- this needs to keep tracking an
        # account after it's been pulled from TG.
        status.mark_portfolio_removed("Apex Trader Funding", "PAAPEX0001", "reached target")
        status.record_daily_equity("Apex Trader Funding", "PAAPEX0001", date="2026-08-01", equity=53600)
        self.assertEqual(
            status.get_equity_history("Apex Trader Funding", "PAAPEX0001"),
            [{"date": "2026-08-01", "equity": 53600}],
        )


class GetEquityHistoryTests(_TempStatusFileTestCase):
    def test_returns_empty_list_for_an_unknown_account(self):
        self.assertEqual(status.get_equity_history("Apex Trader Funding", "PAAPEX0001"), [])

    def test_returns_empty_list_before_status_json_exists_at_all(self):
        self.assertFalse(os.path.exists(self.status_path))
        self.assertEqual(status.get_equity_history("Apex Trader Funding", "PAAPEX0001"), [])


if __name__ == "__main__":
    unittest.main()
