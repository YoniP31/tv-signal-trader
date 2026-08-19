"""Covers the "external open account" safety measure: a Tradovate
sub-account with an open position but no matching TradingGenerator
portfolio at all blocks new trades for that whole company, without ever
being reported (there's nothing in TradingGenerator to report against).
Run with:

    python -m unittest tests.test_external_open_accounts -v

Guards against accidentally opening a second, possibly hedging position on
an account the bot didn't know already had one open -- e.g. a Tradovate
sub-account that exists under a company's login but was never added (or
was removed) as a TradingGenerator portfolio.
"""

import contextlib
import io
import unittest
from unittest.mock import MagicMock, patch

from tv_signal_trader import multi_signal_source as ms
from tv_signal_trader import signal_source
from tv_signal_trader import config


class CheckEligibilityExternalAccountTests(unittest.TestCase):
    """The full interaction matrix between engaged_company (tracked) and
    external_open_accounts (untracked) -- see check_eligibility's
    other_company_engaged computation."""

    def test_no_external_accounts_is_unaffected(self):
        self.assertEqual(
            ms.check_eligibility('A', 'P', None, 'A', {}, external_open_accounts=set()),
            'eligible',
        )

    def test_own_company_external_account_blocks_outright(self):
        result = ms.check_eligibility(
            'A', 'P', None, None, {}, external_open_accounts={('A', 'X')}
        )
        self.assertEqual(result, 'wait_external_open_position')

    def test_own_company_external_account_blocks_even_if_already_engaged_there(self):
        result = ms.check_eligibility(
            'A', 'P', None, 'A', {}, external_open_accounts={('A', 'X')}
        )
        self.assertEqual(result, 'wait_external_open_position')

    def test_other_companys_external_account_blocks_as_different_company(self):
        result = ms.check_eligibility(
            'B', 'P', None, None, {}, external_open_accounts={('A', 'X')}
        )
        self.assertEqual(result, 'wait_different_company')

    def test_already_engaged_elsewhere_wins_over_a_third_companys_external_account(self):
        # engaged_company=B (a company with real tracked positions), while
        # A separately has an external open account, and we're asking
        # about A -- B's engagement is the reason to wait, not A's own
        # external account, even though A also has one.
        result = ms.check_eligibility(
            'A', 'P', None, 'B', {}, external_open_accounts={('A', 'X')}
        )
        self.assertEqual(result, 'wait_different_company')

    def test_external_account_check_happens_before_other_checks(self):
        # Even with no tracked open_positions at all (so this_portfolio/
        # hedge/cap checks would all pass), an external open account for
        # the same company still blocks.
        result = ms.check_eligibility(
            'A', 'P', 'LONG', None, {}, max_positions_per_company=5,
            external_open_accounts={('A', 'X')},
        )
        self.assertEqual(result, 'wait_external_open_position')


class RefreshExternalOpenAccountsTests(unittest.TestCase):
    def setUp(self):
        config.TRADOVATE_ACCOUNTS = {"Apex Trader Funding": {"username": "u", "password": "p"}}
        self.driver = MagicMock()
        self.tv_tab = "tv_tab"

    def test_still_open_account_stays_tracked(self):
        external = {("Apex Trader Funding", "PAAPEX0099")}
        with patch.object(ms, "_ensure_tradovate_connection", return_value="Apex Trader Funding"), \
             patch.object(ms.trading, "select_tradovate_account_with_reconnect", return_value=True), \
             patch.object(ms.trading, "click_orders_tab", return_value=True), \
             patch.object(ms.trading, "find_working_bracket", return_value={"tp": "tp1", "sl": "sl1"}):
            ms._refresh_external_open_accounts(self.driver, self.tv_tab, external, "Apex Trader Funding")
        self.assertIn(("Apex Trader Funding", "PAAPEX0099"), external)

    def test_closed_account_is_dropped(self):
        external = {("Apex Trader Funding", "PAAPEX0099")}
        with patch.object(ms, "_ensure_tradovate_connection", return_value="Apex Trader Funding"), \
             patch.object(ms.trading, "select_tradovate_account_with_reconnect", return_value=True), \
             patch.object(ms.trading, "click_orders_tab", return_value=True), \
             patch.object(ms.trading, "find_working_bracket", return_value={}):
            ms._refresh_external_open_accounts(self.driver, self.tv_tab, external, "Apex Trader Funding")
        self.assertEqual(external, set())

    def test_connection_failure_leaves_it_tracked_for_next_cycle(self):
        external = {("Apex Trader Funding", "PAAPEX0099")}
        with patch.object(ms, "_ensure_tradovate_connection", return_value=None), \
             patch.object(ms.trading, "find_working_bracket") as bracket_mock:
            ms._refresh_external_open_accounts(self.driver, self.tv_tab, external, None)
        self.assertIn(("Apex Trader Funding", "PAAPEX0099"), external)
        bracket_mock.assert_not_called()

    def test_multiple_accounts_are_each_checked_independently(self):
        external = {
            ("Apex Trader Funding", "PAAPEX0099"),
            ("Apex Trader Funding", "PAAPEX0100"),
        }
        # PAAPEX0099 still open, PAAPEX0100 now flat.
        def fake_bracket(driver):
            # select_tradovate_account_with_reconnect is mocked below to
            # record which account was last selected, so this reads that
            # back.
            return {"tp": "t", "sl": "s"} if select_calls[-1] == "PAAPEX0099" else {}

        select_calls = []

        def fake_select(driver, company, account):
            select_calls.append(account)
            return True

        with patch.object(ms, "_ensure_tradovate_connection", return_value="Apex Trader Funding"), \
             patch.object(ms.trading, "select_tradovate_account_with_reconnect", side_effect=fake_select), \
             patch.object(ms.trading, "click_orders_tab", return_value=True), \
             patch.object(ms.trading, "find_working_bracket", side_effect=fake_bracket):
            ms._refresh_external_open_accounts(self.driver, self.tv_tab, external, "Apex Trader Funding")

        self.assertEqual(external, {("Apex Trader Funding", "PAAPEX0099")})


class SweepDiscoversExternalOpenAccountsTests(unittest.TestCase):
    """signal_source.sweep_liquidated_accounts' new pass over Tradovate
    accounts with no matching TradingGenerator portfolio."""

    def setUp(self):
        config.TRADOVATE_ACCOUNTS = {"Apex Trader Funding": {"username": "u", "password": "p"}}
        self.driver = MagicMock()
        self.web_tab, self.tv_tab = "web_tab", "tv_tab"

    def _run_sweep(self, tg_portfolios, tradovate_accounts, bracket_by_account, external_open_accounts):
        def fake_bracket(driver):
            return bracket_by_account.get(select_calls[-1], {})

        select_calls = []

        def fake_select(driver, company, account):
            select_calls.append(account)
            return True

        with patch.object(signal_source.tg, "list_companies", return_value=["Apex Trader Funding"]), \
             patch.object(signal_source.tg, "select_company"), \
             patch.object(signal_source.tg, "list_portfolios", return_value=tg_portfolios), \
             patch.object(signal_source.trading, "is_tradovate_connected", return_value=True), \
             patch.object(signal_source.trading, "list_tradovate_accounts", return_value=tradovate_accounts), \
             patch.object(signal_source.trading, "select_tradovate_account_with_reconnect", side_effect=fake_select), \
             patch.object(signal_source.trading, "click_orders_tab", return_value=True), \
             patch.object(signal_source.trading, "find_working_bracket", side_effect=fake_bracket), \
             patch.object(signal_source.tg, "remove_portfolio"), \
             patch.object(signal_source.status, "mark_portfolio_removed"):
            return signal_source.sweep_liquidated_accounts(
                self.driver, self.web_tab, self.tv_tab, "Apex Trader Funding", external_open_accounts
            )

    def test_extra_account_with_open_position_is_tracked(self):
        external = set()
        self._run_sweep(
            tg_portfolios=["PAAPEX0001"],
            tradovate_accounts=["PAAPEX0001", "PAAPEX0099"],
            bracket_by_account={"PAAPEX0099": {"tp": "t", "sl": "s"}},
            external_open_accounts=external,
        )
        self.assertEqual(external, {("Apex Trader Funding", "PAAPEX0099")})

    def test_extra_account_with_no_open_position_is_not_tracked(self):
        external = set()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self._run_sweep(
                tg_portfolios=["PAAPEX0001"],
                tradovate_accounts=["PAAPEX0001", "PAAPEX0099"],
                bracket_by_account={},
                external_open_accounts=external,
            )
        self.assertEqual(external, set())
        # Checking an extra account and finding nothing must still be
        # visible in the log -- not silently skipped over.
        self.assertIn("PAAPEX0099", buf.getvalue())
        self.assertIn("no open position", buf.getvalue())

    def test_known_portfolio_accounts_are_never_checked_as_external(self):
        # PAAPEX0001 has a matching TG portfolio -- even if it has an open
        # position, it's handled by the normal open_positions ledger, not
        # this mechanism.
        external = set()
        self._run_sweep(
            tg_portfolios=["PAAPEX0001"],
            tradovate_accounts=["PAAPEX0001"],
            bracket_by_account={"PAAPEX0001": {"tp": "t", "sl": "s"}},
            external_open_accounts=external,
        )
        self.assertEqual(external, set())

    def test_previously_tracked_account_that_is_now_flat_is_untracked(self):
        external = {("Apex Trader Funding", "PAAPEX0099")}
        self._run_sweep(
            tg_portfolios=["PAAPEX0001"],
            tradovate_accounts=["PAAPEX0001", "PAAPEX0099"],
            bracket_by_account={},
            external_open_accounts=external,
        )
        self.assertEqual(external, set())

    def test_no_report_to_tradinggenerator_for_external_accounts(self):
        external = set()
        with patch.object(signal_source.tg, "report_trade_result") as report_mock, \
             patch.object(signal_source.status, "record_trade_result") as record_mock:
            self._run_sweep(
                tg_portfolios=["PAAPEX0001"],
                tradovate_accounts=["PAAPEX0001", "PAAPEX0099"],
                bracket_by_account={"PAAPEX0099": {"tp": "t", "sl": "s"}},
                external_open_accounts=external,
            )
        report_mock.assert_not_called()
        record_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
