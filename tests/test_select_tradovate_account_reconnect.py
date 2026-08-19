"""Covers trading.select_tradovate_account_with_reconnect. Run with:

    python -m unittest tests.test_select_tradovate_account_reconnect -v

Real bug this guards against: switching Tradovate accounts can take a
while to load on a slow/high-latency machine, and occasionally drops the
broker panel's connection entirely partway through -- from the caller's
side that looked identical to the account genuinely being unavailable, so
every caller just gave up on that account and moved on to the next one,
even though it was never actually confirmed bad.
"""

import unittest
from unittest.mock import MagicMock, patch

from tv_signal_trader import trading
from tv_signal_trader import config


class SelectTradovateAccountWithReconnectTests(unittest.TestCase):
    def setUp(self):
        config.TRADOVATE_ACCOUNTS = {"Apex Trader Funding": {"username": "u", "password": "p"}}
        self.driver = MagicMock()

    def test_succeeds_on_the_first_try_without_reconnecting(self):
        with patch.object(trading, "select_tradovate_account", return_value=True) as select_mock, \
             patch.object(trading, "is_tradovate_connected") as connected_mock, \
             patch.object(trading, "connect_tradovate") as connect_mock:
            result = trading.select_tradovate_account_with_reconnect(
                self.driver, "Apex Trader Funding", "PAAPEX0001"
            )
        self.assertTrue(result)
        select_mock.assert_called_once()
        connected_mock.assert_not_called()
        connect_mock.assert_not_called()

    def test_failure_while_still_connected_is_not_retried(self):
        # A real failure -- e.g. the account genuinely isn't in the
        # dropdown -- not a dropped connection, so retrying won't help.
        with patch.object(trading, "select_tradovate_account", return_value=False) as select_mock, \
             patch.object(trading, "is_tradovate_connected", return_value=True), \
             patch.object(trading, "connect_tradovate") as connect_mock:
            result = trading.select_tradovate_account_with_reconnect(
                self.driver, "Apex Trader Funding", "PAAPEX0001"
            )
        self.assertFalse(result)
        self.assertEqual(select_mock.call_count, 1)
        connect_mock.assert_not_called()

    def test_dropped_connection_reconnects_and_retries_the_same_account(self):
        select_calls = []

        def fake_select(driver, account_name, timeout=45):
            select_calls.append(account_name)
            # Fails the first time (connection dropped mid-switch),
            # succeeds once retried after reconnecting.
            return len(select_calls) > 1

        with patch.object(trading, "select_tradovate_account", side_effect=fake_select), \
             patch.object(trading, "is_tradovate_connected", return_value=False), \
             patch.object(trading, "connect_tradovate", return_value=True) as connect_mock:
            result = trading.select_tradovate_account_with_reconnect(
                self.driver, "Apex Trader Funding", "PAAPEX0001"
            )
        self.assertTrue(result)
        self.assertEqual(select_calls, ["PAAPEX0001", "PAAPEX0001"])
        connect_mock.assert_called_once_with(self.driver, "u", "p")

    def test_reconnect_failure_gives_up(self):
        with patch.object(trading, "select_tradovate_account", return_value=False), \
             patch.object(trading, "is_tradovate_connected", return_value=False), \
             patch.object(trading, "connect_tradovate", return_value=False):
            result = trading.select_tradovate_account_with_reconnect(
                self.driver, "Apex Trader Funding", "PAAPEX0001"
            )
        self.assertFalse(result)

    def test_unknown_company_fails_without_attempting_reconnect(self):
        with patch.object(trading, "select_tradovate_account", return_value=False), \
             patch.object(trading, "is_tradovate_connected", return_value=False), \
             patch.object(trading, "connect_tradovate") as connect_mock:
            result = trading.select_tradovate_account_with_reconnect(
                self.driver, "Some Unconfigured Company", "PAAPEX0001"
            )
        self.assertFalse(result)
        connect_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
