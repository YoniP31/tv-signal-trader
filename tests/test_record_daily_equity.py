"""Covers multi_signal_source._record_daily_equity -- the once-per-day pass
that reads and persists every Tradovate sub-account's balance under every
configured company, feeding status.record_daily_equity (see
tests/test_daily_equity_history.py) regardless of whether an account
still has a TradingGenerator portfolio. Run with:

    python -m unittest tests.test_record_daily_equity -v
"""

import unittest
from unittest.mock import MagicMock, patch

from tv_signal_trader import config
from tv_signal_trader import multi_signal_source as ms


class RecordDailyEquityTests(unittest.TestCase):
    def setUp(self):
        self._original_accounts = config.TRADOVATE_ACCOUNTS
        config.TRADOVATE_ACCOUNTS = {
            "Apex Trader Funding": {"username": "u1", "password": "p1"},
            "TopStep": {"username": "u2", "password": "p2"},
        }
        self.addCleanup(setattr, config, "TRADOVATE_ACCOUNTS", self._original_accounts)
        self.driver = MagicMock()
        self.tv_tab = "tv_tab"

    def test_records_every_account_under_every_company(self):
        accounts_by_company = {
            "Apex Trader Funding": ["PAAPEX0001", "PAAPEX0002"],
            "TopStep": ["TS0001"],
        }
        # _ensure_tradovate_connection's return value *is* which company is
        # now connected -- list_tradovate_accounts routes off of that,
        # since the fake driver has no real connection state of its own.
        connected = {"company": None}

        def fake_ensure(d, company, connected_company):
            connected["company"] = company
            return company

        with patch.object(ms, "_ensure_tradovate_connection", side_effect=fake_ensure), \
             patch.object(ms.trading, "list_tradovate_accounts",
                           side_effect=lambda d: accounts_by_company[connected["company"]]), \
             patch.object(ms.trading, "select_tradovate_account_with_reconnect", return_value=True), \
             patch.object(ms.trading, "read_account_balance", return_value=50500), \
             patch.object(ms.status, "record_daily_equity") as record_mock:
            ms._record_daily_equity(self.driver, self.tv_tab, None)

        calls = {(c[0], c[1]) for c, _kw in record_mock.call_args_list}
        self.assertEqual(
            calls,
            {("Apex Trader Funding", "PAAPEX0001"), ("Apex Trader Funding", "PAAPEX0002"),
             ("TopStep", "TS0001")},
        )

    def test_skips_a_company_it_cannot_connect_to(self):
        with patch.object(ms, "_ensure_tradovate_connection", return_value=None), \
             patch.object(ms.trading, "list_tradovate_accounts") as list_accounts_mock, \
             patch.object(ms.status, "record_daily_equity") as record_mock:
            ms._record_daily_equity(self.driver, self.tv_tab, None)
        list_accounts_mock.assert_not_called()
        record_mock.assert_not_called()

    def test_skips_a_company_whose_account_list_cannot_be_read(self):
        with patch.object(ms, "_ensure_tradovate_connection", side_effect=lambda d, c, cc: c), \
             patch.object(ms.trading, "list_tradovate_accounts", return_value=None), \
             patch.object(ms.trading, "select_tradovate_account_with_reconnect") as select_mock, \
             patch.object(ms.status, "record_daily_equity") as record_mock:
            ms._record_daily_equity(self.driver, self.tv_tab, None)
        select_mock.assert_not_called()
        record_mock.assert_not_called()

    def test_skips_an_account_that_cannot_be_selected(self):
        with patch.object(ms, "_ensure_tradovate_connection", side_effect=lambda d, c, cc: c), \
             patch.object(ms.trading, "list_tradovate_accounts", return_value=["PAAPEX0001"]), \
             patch.object(ms.trading, "select_tradovate_account_with_reconnect", return_value=False), \
             patch.object(ms.trading, "read_account_balance") as read_balance_mock, \
             patch.object(ms.status, "record_daily_equity") as record_mock:
            ms._record_daily_equity(self.driver, self.tv_tab, None)
        read_balance_mock.assert_not_called()
        record_mock.assert_not_called()

    def test_skips_an_account_whose_balance_cannot_be_read(self):
        with patch.object(ms, "_ensure_tradovate_connection", side_effect=lambda d, c, cc: c), \
             patch.object(ms.trading, "list_tradovate_accounts", return_value=["PAAPEX0001"]), \
             patch.object(ms.trading, "select_tradovate_account_with_reconnect", return_value=True), \
             patch.object(ms.trading, "read_account_balance", return_value=None), \
             patch.object(ms.status, "record_daily_equity") as record_mock:
            ms._record_daily_equity(self.driver, self.tv_tab, None)
        record_mock.assert_not_called()

    def test_returns_the_final_connected_company(self):
        with patch.object(ms, "_ensure_tradovate_connection", side_effect=lambda d, c, cc: c), \
             patch.object(ms.trading, "list_tradovate_accounts", return_value=[]), \
             patch.object(ms.status, "record_daily_equity"):
            result = ms._record_daily_equity(self.driver, self.tv_tab, None)
        # With two configured companies and each one connected in turn,
        # the last one iterated is whatever connected_company ends up as.
        self.assertIn(result, config.TRADOVATE_ACCOUNTS)


if __name__ == "__main__":
    unittest.main()
