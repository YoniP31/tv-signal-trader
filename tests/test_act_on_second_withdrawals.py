"""Covers signal_source.act_on_second_withdrawals -- the real Second
Withdrawal re-entry action (see the Flip Mode plan's Phase 3): re-adds a
flagged LIVE account to TradingGenerator, marks #secondWithdrawalBtn, and
seeds a fresh running_equity_target/cycle_start_date/cycle_starting_balance.
Run with:

    python -m unittest tests.test_act_on_second_withdrawals -v
"""

import unittest
from unittest.mock import MagicMock, patch

from tv_signal_trader import config
from tv_signal_trader import signal_source as ss


class ActOnSecondWithdrawalsTests(unittest.TestCase):
    def setUp(self):
        self._original_tiers = config.ACCOUNT_BALANCE_TIERS
        config.ACCOUNT_BALANCE_TIERS = {
            50000: {
                'min': 47500, 'max': {'EVAL': 53000, 'LIVE': 53500},
                'max_initial': {'EVAL': 52500, 'LIVE': 53000},
                'max_final': {'EVAL': 53000, 'LIVE': 53500},
            },
            25000: {
                'min': 23000, 'max': {'EVAL': 27000, 'LIVE': 27000},
                'max_initial': {'EVAL': 26000, 'LIVE': 26500},
                'max_final': {'EVAL': 26500, 'LIVE': 27000},
            },
        }
        self.addCleanup(setattr, config, "ACCOUNT_BALANCE_TIERS", self._original_tiers)
        self._original_buffer = config.FLIP_MODE_REENTRY_BUFFER
        config.FLIP_MODE_REENTRY_BUFFER = 1500
        self.addCleanup(setattr, config, "FLIP_MODE_REENTRY_BUFFER", self._original_buffer)
        self.driver = MagicMock()
        self.web_tab = "web_tab"
        self.tv_tab = "tv_tab"

    def _one_detected(self, **overrides):
        entry = {
            'company': "Apex Trader Funding", 'account': "PAAPEX0001",
            'current_balance': 51000.0, 'last_recorded_equity': 53000.0, 'today_total_pl': 0.0,
        }
        entry.update(overrides)
        return [entry]

    def _patches(self, detected=(), **overrides):
        defaults = dict(
            select_company=True,
            list_portfolios=[],
            add_portfolio=True,
            select_portfolio=True,
            mark_second_withdrawal=True,
            read_admin_code="secret",
        )
        defaults.update(overrides)
        return [
            patch.object(ss, "detect_second_withdrawals", return_value=(list(detected), "Apex Trader Funding")),
            patch.object(ss.tg, "select_company", return_value=defaults["select_company"]),
            patch.object(ss.tg, "list_portfolios", return_value=defaults["list_portfolios"]),
            patch.object(ss.tg, "add_portfolio", return_value=defaults["add_portfolio"]),
            patch.object(ss.tg, "select_portfolio", return_value=defaults["select_portfolio"]),
            patch.object(ss.tg, "mark_second_withdrawal", return_value=defaults["mark_second_withdrawal"]),
            patch.object(ss.config, "read_admin_code", return_value=defaults["read_admin_code"]),
            patch.object(ss.status, "set_running_equity_target"),
            patch.object(ss.status, "set_cycle_start_date"),
            patch.object(ss.status, "set_cycle_starting_balance"),
        ]

    def _run(self, detected=(), **overrides):
        patches = self._patches(detected=detected, **overrides)
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])
        return ss.act_on_second_withdrawals(self.driver, self.web_tab, self.tv_tab, None)

    def test_nothing_detected_does_nothing(self):
        acted, connected = self._run(detected=[])
        self.assertEqual(acted, [])
        ss.tg.select_company.assert_not_called()

    def test_completes_the_full_cycle_for_a_flagged_account(self):
        acted, connected = self._run(detected=self._one_detected())
        self.assertEqual(acted, [("Apex Trader Funding", "PAAPEX0001")])
        self.assertEqual(connected, "Apex Trader Funding")
        ss.tg.add_portfolio.assert_called_once_with(self.driver, "PAAPEX0001", account_type='live')
        ss.tg.select_portfolio.assert_called_once_with(self.driver, "PAAPEX0001")
        ss.tg.mark_second_withdrawal.assert_called_once_with(self.driver, "secret")

    def test_seeds_the_running_equity_target_via_seed_reentry_target(self):
        # Balance 51000 is below tier_final (53500 LIVE 50K) -- seeds at
        # tier_final directly, not the buffer formula.
        self._run(detected=self._one_detected(current_balance=51000.0))
        ss.status.set_running_equity_target.assert_called_once_with(
            "Apex Trader Funding", "PAAPEX0001", 53500.0
        )

    def test_seeds_with_the_buffer_when_balance_already_cleared_tier_final(self):
        self._run(detected=self._one_detected(current_balance=54000.0))
        # 54000 >= 53500 (tier final) -> seed = 54000 + 1500 (the buffer)
        ss.status.set_running_equity_target.assert_called_once_with(
            "Apex Trader Funding", "PAAPEX0001", 55500.0
        )

    def test_resets_the_cycle_start_date_and_starting_balance(self):
        self._run(detected=self._one_detected(current_balance=51000.0))
        ss.status.set_cycle_starting_balance.assert_called_once_with(
            "Apex Trader Funding", "PAAPEX0001", 51000.0
        )
        date_call = ss.status.set_cycle_start_date.call_args
        self.assertEqual(date_call.args[0], "Apex Trader Funding")
        self.assertEqual(date_call.args[1], "PAAPEX0001")
        self.assertTrue(date_call.args[2])  # some non-empty date string

    def test_uses_the_closest_tier_by_balance_not_always_50k(self):
        # 26800 is far closer to the 25K tier than 50K -- must use its
        # own tier_final (27000 LIVE), not the 50K one.
        self._run(detected=self._one_detected(current_balance=26800.0))
        ss.status.set_running_equity_target.assert_called_once_with(
            "Apex Trader Funding", "PAAPEX0001", 27000.0
        )

    def test_skips_re_adding_if_a_portfolio_already_exists(self):
        acted, _connected = self._run(
            detected=self._one_detected(), list_portfolios=["PAAPEX0001"],
        )
        self.assertEqual(acted, [("Apex Trader Funding", "PAAPEX0001")])
        ss.tg.add_portfolio.assert_not_called()
        # The rest of the cycle still runs even without re-adding.
        ss.tg.mark_second_withdrawal.assert_called_once()

    def test_skips_an_account_it_cannot_select_the_company_for(self):
        acted, _connected = self._run(detected=self._one_detected(), select_company=False)
        self.assertEqual(acted, [])
        ss.tg.add_portfolio.assert_not_called()

    def test_skips_an_account_it_cannot_re_add(self):
        acted, _connected = self._run(detected=self._one_detected(), add_portfolio=False)
        self.assertEqual(acted, [])
        ss.tg.select_portfolio.assert_not_called()
        ss.status.set_running_equity_target.assert_not_called()

    def test_skips_an_account_it_cannot_select_after_re_adding(self):
        acted, _connected = self._run(detected=self._one_detected(), select_portfolio=False)
        self.assertEqual(acted, [])
        ss.tg.mark_second_withdrawal.assert_not_called()
        ss.status.set_running_equity_target.assert_not_called()

    def test_skips_an_account_it_cannot_mark(self):
        acted, _connected = self._run(detected=self._one_detected(), mark_second_withdrawal=False)
        self.assertEqual(acted, [])
        ss.status.set_running_equity_target.assert_not_called()

    def test_one_failure_does_not_block_another_accounts_cycle(self):
        detected = [
            {'company': "Apex Trader Funding", 'account': "PAAPEX0001",
             'current_balance': 51000.0, 'last_recorded_equity': 53000.0, 'today_total_pl': 0.0},
            {'company': "Apex Trader Funding", 'account': "PAAPEX0002",
             'current_balance': 52000.0, 'last_recorded_equity': 53000.0, 'today_total_pl': 0.0},
        ]
        # add_portfolio fails for the first call, succeeds for the second.
        with patch.object(ss, "detect_second_withdrawals", return_value=(detected, None)), \
             patch.object(ss.tg, "select_company", return_value=True), \
             patch.object(ss.tg, "list_portfolios", return_value=[]), \
             patch.object(ss.tg, "add_portfolio", side_effect=[False, True]), \
             patch.object(ss.tg, "select_portfolio", return_value=True), \
             patch.object(ss.tg, "mark_second_withdrawal", return_value=True), \
             patch.object(ss.config, "read_admin_code", return_value="secret"), \
             patch.object(ss.status, "set_running_equity_target"), \
             patch.object(ss.status, "set_cycle_start_date"), \
             patch.object(ss.status, "set_cycle_starting_balance"):
            acted, _connected = ss.act_on_second_withdrawals(self.driver, self.web_tab, self.tv_tab, None)
        self.assertEqual(acted, [("Apex Trader Funding", "PAAPEX0002")])


if __name__ == "__main__":
    unittest.main()
