"""Covers _open_position's 'blown'/'qualifies_for_removal' outcomes
(multi_signal_source.py) -- it reports these back to the caller rather
than removing the portfolio itself. Real bug this guards against: a
signal is already generated for this portfolio (see run_web_loop_multi)
before _open_position ever runs, and if it's a company's only portfolio,
TradingGenerator can silently refuse to delete it -- removing it here,
before the caller gets a chance to report Not Taken on that signal, left
it stuck with no Trade Result ever clicked. Run with:

    python -m unittest tests.test_open_position_removal_outcome -v
"""

import unittest
from unittest.mock import MagicMock, patch

from tv_signal_trader import multi_signal_source as ms


PARAMS = {
    'company': "Apex Trader Funding", 'portfolio': "APEX0007",
    'asset': "ES", 'contract_size': "MINI", 'account_type': "LIVE",
    'direction': "LONG", 'contracts': 4, 'sl_ticks': 20, 'tp_ticks': 17,
    'tp_dollars': 850.0, 'sl_dollars': 1000.0,
}


class OpenPositionRemovalOutcomeTests(unittest.TestCase):
    def setUp(self):
        self.driver = MagicMock()
        self.web_tab, self.tv_tab = "web_tab", "tv_tab"

    def _run(self, decision):
        with patch.object(ms.trading, "load_chart_for_signal"), \
             patch.object(ms, "_ensure_tradovate_connection", return_value=PARAMS['company']), \
             patch.object(ms.trading, "select_tradovate_account_with_reconnect", return_value=True), \
             patch.object(ms, "evaluate_account_for_removal",
                           return_value=(decision, 46000.0, None, PARAMS['company'])), \
             patch.object(ms, "_act_on_flip_mode_decision") as act_mock, \
             patch.object(ms.status, "update_portfolio") as update_mock, \
             patch.object(ms.status, "mark_portfolio_available") as available_mock, \
             patch.object(ms.trading, "place_order") as place_order_mock:
            outcome, connected_company, entry = ms._open_position(
                self.driver, self.web_tab, self.tv_tab, PARAMS, PARAMS['company']
            )
        return outcome, connected_company, entry, act_mock, update_mock, available_mock, place_order_mock

    def test_blown_is_reported_back_rather_than_acted_on_here(self):
        outcome, connected_company, entry, act_mock, update_mock, available_mock, place_order_mock = self._run('blown')
        self.assertEqual(outcome, 'blown')
        self.assertEqual(connected_company, PARAMS['company'])
        self.assertIsNone(entry)
        # The whole point: _open_position must not remove the portfolio
        # itself, or try to open a trade on it either.
        act_mock.assert_not_called()
        available_mock.assert_not_called()
        place_order_mock.assert_not_called()

    def test_qualifies_for_removal_is_reported_back_rather_than_acted_on_here(self):
        outcome, connected_company, entry, act_mock, update_mock, available_mock, place_order_mock = self._run(
            'qualifies_for_removal'
        )
        self.assertEqual(outcome, 'qualifies_for_removal')
        self.assertEqual(connected_company, PARAMS['company'])
        self.assertIsNone(entry)
        act_mock.assert_not_called()
        available_mock.assert_not_called()
        place_order_mock.assert_not_called()

    def test_balance_is_still_recorded_for_a_blown_account(self):
        _outcome, _cc, _entry, _act_mock, update_mock, _avail, _place = self._run('blown')
        update_mock.assert_called_once_with(PARAMS['company'], PARAMS['portfolio'], balance=46000.0)


if __name__ == "__main__":
    unittest.main()
