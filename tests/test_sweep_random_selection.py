"""Covers signal_source.sweep_liquidated_accounts' final step: randomly
selecting a company/portfolio from the current candidate list rather than
leaving TradingGenerator parked on whichever one the sweep's own iteration
happened to touch last. Run with:

    python -m unittest tests.test_sweep_random_selection -v

Real bug this guards against: generate_trade() with no "next portfolio"
hint (the first trade of a session, or right after this sweep runs) just
clicks Generate on whatever's currently selected in TradingGenerator.
Since selecting each company in turn (to read its portfolio tabs) is a
deterministic scan in list order, the sweep always finished on the same
last company/portfolio -- silently biasing every hint-less trade toward
that one account instead of picking a random one.
"""

import unittest
from unittest.mock import MagicMock, patch

from tv_signal_trader import signal_source
from tv_signal_trader import config


class SweepEndsOnRandomSelectionTests(unittest.TestCase):
    def setUp(self):
        config.TRADOVATE_ACCOUNTS = {"Apex Trader Funding": {"username": "u", "password": "p"}}
        self.driver = MagicMock()
        self.web_tab, self.tv_tab = "web_tab", "tv_tab"

    def _run_sweep(self, candidates):
        with patch.object(signal_source.tg, "list_companies", return_value=["Apex Trader Funding"]), \
             patch.object(signal_source.tg, "select_company") as select_company_mock, \
             patch.object(signal_source.tg, "select_portfolio") as select_portfolio_mock, \
             patch.object(signal_source.tg, "list_portfolios", return_value=["PAAPEX0001"]), \
             patch.object(signal_source.trading, "is_tradovate_connected", return_value=True), \
             patch.object(signal_source.trading, "list_tradovate_accounts", return_value=["PAAPEX0001"]), \
             patch.object(signal_source.tg, "list_all_candidates", return_value=candidates):
            signal_source.sweep_liquidated_accounts(
                self.driver, self.web_tab, self.tv_tab, "Apex Trader Funding", set()
            )
        return select_company_mock, select_portfolio_mock

    def test_ends_by_selecting_one_of_the_current_candidates(self):
        candidates = [
            ("Apex Trader Funding", "PAAPEX0001"),
            ("Apex Trader Funding", "PAAPEX0002"),
            ("TopStep", "TS0001"),
        ]

        # The last calls made to select_company/select_portfolio (the ones
        # that matter, since anything earlier in the sweep gets overridden)
        # must be some entry from the current candidate list, not a fixed
        # one -- run several times to make sure it isn't just coincidentally
        # always picking the first/last candidate.
        seen_selections = set()
        for _ in range(30):
            select_company_mock, select_portfolio_mock = self._run_sweep(candidates)
            company = select_company_mock.call_args_list[-1].args[1]
            portfolio = select_portfolio_mock.call_args_list[-1].args[1]
            self.assertIn((company, portfolio), candidates)
            seen_selections.add((company, portfolio))

        self.assertGreater(
            len(seen_selections), 1,
            "expected more than one distinct candidate to get picked across 30 runs",
        )

    def test_no_candidates_is_a_no_op(self):
        select_company_mock, select_portfolio_mock = self._run_sweep(candidates=[])
        # select_company/select_portfolio may still have been called earlier
        # in the sweep (per-company bookkeeping) -- what matters is the
        # final random-selection step didn't blow up with an empty list.
        # No assertion needed beyond "sweep_liquidated_accounts didn't raise".


if __name__ == "__main__":
    unittest.main()
