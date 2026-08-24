"""Covers signal_source.detect_second_withdrawals -- the read-only pass
that checks every LIVE-typed account ever tracked (status.
list_tracked_accounts, present or absent from TradingGenerator) for a
balance drop today's own trading P/L can't explain (history.
detect_withdrawal), per the Flip Mode plan's Phase 3 (Second Withdrawal).
Run with:

    python -m unittest tests.test_detect_second_withdrawals -v
"""

import unittest
from unittest.mock import MagicMock, patch

from tv_signal_trader import config
from tv_signal_trader import signal_source as ss


class DetectSecondWithdrawalsTests(unittest.TestCase):
    def setUp(self):
        self._original_accounts = config.TRADOVATE_ACCOUNTS
        config.TRADOVATE_ACCOUNTS = {
            "Apex Trader Funding": {"username": "u1", "password": "p1"},
            "TopStep": {"username": "u2", "password": "p2"},
        }
        self.addCleanup(setattr, config, "TRADOVATE_ACCOUNTS", self._original_accounts)
        self.driver = MagicMock()
        self.tv_tab = "tv_tab"

    def _patches(self, **overrides):
        defaults = dict(
            list_tracked_accounts=[("Apex Trader Funding", "PAAPEX0001")],
            is_tradovate_connected=False,
            connect_tradovate=True,
            select_tradovate_account_with_reconnect=True,
            read_account_balance=52500.0,
            click_account_summary_tab=True,
            read_total_pl=0.0,
            equity_history={"Apex Trader Funding / PAAPEX0001": [{"date": "2026-08-01", "equity": 53000.0}]},
        )
        defaults.update(overrides)

        def fake_get_equity_history(company, account):
            return defaults["equity_history"].get(f"{company} / {account}", [])

        return [
            patch.object(ss.status, "list_tracked_accounts", return_value=defaults["list_tracked_accounts"]),
            patch.object(ss.status, "get_equity_history", side_effect=fake_get_equity_history),
            patch.object(ss.trading, "is_tradovate_connected", return_value=defaults["is_tradovate_connected"]),
            patch.object(ss.trading, "disconnect_tradovate"),
            patch.object(ss.trading, "connect_tradovate", return_value=defaults["connect_tradovate"]),
            patch.object(ss.trading, "select_tradovate_account_with_reconnect",
                          return_value=defaults["select_tradovate_account_with_reconnect"]),
            patch.object(ss.trading, "read_account_balance", return_value=defaults["read_account_balance"]),
            patch.object(ss.trading, "click_account_summary_tab", return_value=defaults["click_account_summary_tab"]),
            patch.object(ss.trading, "read_total_pl", return_value=defaults["read_total_pl"]),
        ]

    def _run(self, initial_connected_company=None, **overrides):
        patches = self._patches(**overrides)
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])
        return ss.detect_second_withdrawals(self.driver, self.tv_tab, initial_connected_company)

    def test_flags_a_clean_withdrawal(self):
        detected, _connected = self._run(read_account_balance=52500.0, read_total_pl=0.0)
        self.assertEqual(len(detected), 1)
        self.assertEqual(detected[0]['company'], "Apex Trader Funding")
        self.assertEqual(detected[0]['account'], "PAAPEX0001")
        self.assertEqual(detected[0]['current_balance'], 52500.0)
        self.assertEqual(detected[0]['last_recorded_equity'], 53000.0)
        self.assertEqual(detected[0]['today_total_pl'], 0.0)

    def test_does_not_flag_a_drop_fully_explained_by_trading(self):
        detected, _connected = self._run(read_account_balance=52500.0, read_total_pl=-500.0)
        self.assertEqual(detected, [])

    def test_no_tracked_live_accounts_does_nothing(self):
        detected, _connected = self._run(list_tracked_accounts=[])
        self.assertEqual(detected, [])

    def test_skips_a_company_with_no_tradovate_account_configured(self):
        detected, _connected = self._run(
            list_tracked_accounts=[("Unconfigured Company", "ACC0001")],
        )
        self.assertEqual(detected, [])

    def test_skips_an_account_with_no_recorded_history_yet(self):
        detected, _connected = self._run(equity_history={})
        self.assertEqual(detected, [])

    def test_skips_the_whole_company_if_it_cannot_connect(self):
        patches = self._patches(connect_tradovate=False)
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])
        detected, connected = ss.detect_second_withdrawals(self.driver, self.tv_tab, None)
        self.assertEqual(detected, [])
        self.assertIsNone(connected)
        ss.trading.select_tradovate_account_with_reconnect.assert_not_called()

    def test_reuses_the_connection_when_already_connected_to_the_right_company(self):
        detected, connected = self._run(
            initial_connected_company="Apex Trader Funding", is_tradovate_connected=True,
        )
        self.assertEqual(connected, "Apex Trader Funding")
        # Already connected to the right company -- connect_tradovate must
        # not be called again.
        ss.trading.connect_tradovate.assert_not_called()

    def test_skips_an_account_that_cannot_be_selected(self):
        detected, _connected = self._run(select_tradovate_account_with_reconnect=False)
        self.assertEqual(detected, [])
        ss.trading.read_account_balance.assert_not_called()

    def test_skips_an_account_whose_balance_cannot_be_read(self):
        detected, _connected = self._run(read_account_balance=None)
        self.assertEqual(detected, [])

    def test_treats_an_unreadable_total_pl_as_unknown_not_zero(self):
        # click_account_summary_tab failing means today_total_pl stays
        # None -- history.detect_withdrawal must never guess a withdrawal
        # from that alone.
        detected, _connected = self._run(
            read_account_balance=40000.0, click_account_summary_tab=False,
        )
        self.assertEqual(detected, [])

    def test_checks_every_account_across_every_configured_company(self):
        detected, _connected = self._run(
            list_tracked_accounts=[
                ("Apex Trader Funding", "PAAPEX0001"),
                ("TopStep", "TS0001"),
            ],
            equity_history={
                "Apex Trader Funding / PAAPEX0001": [{"date": "2026-08-01", "equity": 53000.0}],
                "TopStep / TS0001": [{"date": "2026-08-01", "equity": 27000.0}],
            },
            # A single mocked balance can't vary per account (read_account_
            # balance takes no account argument -- it reads whatever's
            # currently selected), so use one far below *both* accounts'
            # recorded equity to guarantee each is flagged regardless of
            # its own baseline.
            read_account_balance=100.0, read_total_pl=0.0,
        )
        checked = {(c[1], c[2]) for c, _kw in ss.trading.select_tradovate_account_with_reconnect.call_args_list}
        self.assertEqual(checked, {("Apex Trader Funding", "PAAPEX0001"), ("TopStep", "TS0001")})
        # A different company each time -- connect_tradovate must run for
        # both rather than just the first.
        self.assertEqual(ss.trading.connect_tradovate.call_count, 2)
        self.assertEqual(
            {(d['company'], d['account']) for d in detected},
            {("Apex Trader Funding", "PAAPEX0001"), ("TopStep", "TS0001")},
        )


if __name__ == "__main__":
    unittest.main()
