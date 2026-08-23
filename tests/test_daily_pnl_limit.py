"""Covers _open_position's pre-trade daily profit/loss limit check
(multi_signal_source.py) -- specifically that an unreadable Total P/L is
treated as a reason *not* to trade, rather than silently falling through
as if no daily limit were configured at all. Run with:

    python -m unittest tests.test_daily_pnl_limit -v

Real bug this guards against: a transient DOM/timing glitch reading
Tradovate's Total P/L field left `total_pl` as None, which every daily-
limit check downstream (both the outright-refusal check and
adjust_ticks_for_daily_pnl's capping) silently no-ops for -- so a trade
opened at its full, uncapped size with no daily-limit protection at all,
even though a limit was configured and the account was already close to it.
"""

import unittest
from unittest.mock import MagicMock, patch

from tv_signal_trader import multi_signal_source as ms
from tv_signal_trader import config


PARAMS = {
    'company': "Apex Trader Funding", 'portfolio': "APEX0007",
    'asset': "ES", 'contract_size': "MINI", 'account_type': "eval",
    'direction': "LONG", 'contracts': 4, 'sl_ticks': 20, 'tp_ticks': 17,
    'tp_dollars': 850.0, 'sl_dollars': 1000.0,
}


class OpenPositionDailyPnlLimitTests(unittest.TestCase):
    def setUp(self):
        self.driver = MagicMock()
        self.web_tab, self.tv_tab = "web_tab", "tv_tab"
        self._orig_profit_limit = config.DAILY_PROFIT_LIMIT
        self._orig_loss_limit = config.DAILY_LOSS_LIMIT
        self._orig_buffer_range = config.DAILY_PNL_CAP_BUFFER_RANGE
        config.DAILY_PROFIT_LIMIT = 1500
        config.DAILY_LOSS_LIMIT = 1500
        config.DAILY_PNL_CAP_BUFFER_RANGE = (50, 200)

    def tearDown(self):
        config.DAILY_PROFIT_LIMIT = self._orig_profit_limit
        config.DAILY_LOSS_LIMIT = self._orig_loss_limit
        config.DAILY_PNL_CAP_BUFFER_RANGE = self._orig_buffer_range

    def test_refuses_to_trade_when_total_pl_cannot_be_read(self):
        with patch.object(ms, "_ensure_tradovate_connection", return_value=PARAMS['company']), \
             patch.object(ms.trading, "select_tradovate_account", return_value=True), \
             patch.object(ms, "evaluate_account_for_removal",
                           return_value=("keep_normal", 25000, 27000, PARAMS['company'])), \
             patch.object(ms.status, "update_portfolio"), \
             patch.object(ms.status, "mark_portfolio_available"), \
             patch.object(ms.trading, "load_chart_for_signal"), \
             patch.object(ms.trading, "click_account_summary_tab", return_value=True), \
             patch.object(ms.trading, "read_total_pl", return_value=None), \
             patch.object(ms.trading, "place_order") as place_order_mock, \
             patch.object(ms.trading, "adjust_tp_for_max_balance") as adjust_balance_mock, \
             patch.object(ms.trading, "adjust_ticks_for_daily_pnl") as adjust_pnl_mock:
            outcome, _, entry = ms._open_position(
                self.driver, self.web_tab, self.tv_tab, PARAMS, PARAMS['company']
            )

        self.assertEqual(outcome, 'failed')
        self.assertIsNone(entry)
        place_order_mock.assert_not_called()
        adjust_balance_mock.assert_not_called()
        adjust_pnl_mock.assert_not_called()

    def test_refuses_when_broker_panel_cannot_be_opened(self):
        # click_account_summary_tab itself failing (broker panel wouldn't
        # open) must be treated the same as a failed read -- total_pl never
        # even gets a chance to be read.
        with patch.object(ms, "_ensure_tradovate_connection", return_value=PARAMS['company']), \
             patch.object(ms.trading, "select_tradovate_account", return_value=True), \
             patch.object(ms, "evaluate_account_for_removal",
                           return_value=("keep_normal", 25000, 27000, PARAMS['company'])), \
             patch.object(ms.status, "update_portfolio"), \
             patch.object(ms.status, "mark_portfolio_available"), \
             patch.object(ms.trading, "load_chart_for_signal"), \
             patch.object(ms.trading, "click_account_summary_tab", return_value=False), \
             patch.object(ms.trading, "read_total_pl") as read_pl_mock, \
             patch.object(ms.trading, "place_order") as place_order_mock:
            outcome, _, entry = ms._open_position(
                self.driver, self.web_tab, self.tv_tab, PARAMS, PARAMS['company']
            )

        self.assertEqual(outcome, 'failed')
        self.assertIsNone(entry)
        read_pl_mock.assert_not_called()
        place_order_mock.assert_not_called()

    def test_trades_normally_when_total_pl_reads_successfully(self):
        with patch.object(ms, "_ensure_tradovate_connection", return_value=PARAMS['company']), \
             patch.object(ms.trading, "select_tradovate_account", return_value=True), \
             patch.object(ms, "evaluate_account_for_removal",
                           return_value=("keep_normal", 25000, 27000, PARAMS['company'])), \
             patch.object(ms.status, "update_portfolio"), \
             patch.object(ms.status, "mark_portfolio_available"), \
             patch.object(ms.trading, "load_chart_for_signal"), \
             patch.object(ms.trading, "click_account_summary_tab", return_value=True), \
             patch.object(ms.trading, "read_total_pl", return_value=200.0), \
             patch.object(ms.trading, "adjust_tp_for_max_balance", return_value=17), \
             patch.object(ms.trading, "adjust_ticks_for_daily_pnl", return_value=(17, 20)), \
             patch.object(ms.trading, "place_order", return_value=True), \
             patch.object(ms.trading, "find_working_bracket", return_value={"tp": "tp1", "sl": "sl1"}):
            outcome, _, entry = ms._open_position(
                self.driver, self.web_tab, self.tv_tab, PARAMS, PARAMS['company']
            )

        self.assertEqual(outcome, 'opened')
        self.assertIsNotNone(entry)

    def test_refuses_outright_within_the_buffer_zone(self):
        # 1380 is within DAILY_PNL_CAP_BUFFER_MAX (200) of the 1500 limit --
        # must refuse outright rather than reaching the capping logic.
        with patch.object(ms, "_ensure_tradovate_connection", return_value=PARAMS['company']), \
             patch.object(ms.trading, "select_tradovate_account", return_value=True), \
             patch.object(ms, "evaluate_account_for_removal",
                           return_value=("keep_normal", 25000, 27000, PARAMS['company'])), \
             patch.object(ms.status, "update_portfolio"), \
             patch.object(ms.status, "mark_portfolio_available"), \
             patch.object(ms.trading, "load_chart_for_signal"), \
             patch.object(ms.trading, "click_account_summary_tab", return_value=True), \
             patch.object(ms.trading, "read_total_pl", return_value=1380.0), \
             patch.object(ms.trading, "place_order") as place_order_mock, \
             patch.object(ms.trading, "adjust_ticks_for_daily_pnl") as adjust_pnl_mock:
            outcome, _, entry = ms._open_position(
                self.driver, self.web_tab, self.tv_tab, PARAMS, PARAMS['company']
            )

        self.assertEqual(outcome, 'daily_profit_limit_reached')
        self.assertIsNone(entry)
        place_order_mock.assert_not_called()
        adjust_pnl_mock.assert_not_called()

    def test_no_daily_limit_configured_skips_the_check_entirely(self):
        config.DAILY_PROFIT_LIMIT = None
        config.DAILY_LOSS_LIMIT = None
        with patch.object(ms, "_ensure_tradovate_connection", return_value=PARAMS['company']), \
             patch.object(ms.trading, "select_tradovate_account", return_value=True), \
             patch.object(ms, "evaluate_account_for_removal",
                           return_value=("keep_normal", 25000, 27000, PARAMS['company'])), \
             patch.object(ms.status, "update_portfolio"), \
             patch.object(ms.status, "mark_portfolio_available"), \
             patch.object(ms.trading, "load_chart_for_signal"), \
             patch.object(ms.trading, "click_account_summary_tab") as click_summary_mock, \
             patch.object(ms.trading, "adjust_tp_for_max_balance", return_value=17), \
             patch.object(ms.trading, "adjust_ticks_for_daily_pnl", return_value=(17, 20)), \
             patch.object(ms.trading, "place_order", return_value=True), \
             patch.object(ms.trading, "find_working_bracket", return_value={"tp": "tp1", "sl": "sl1"}):
            outcome, _, entry = ms._open_position(
                self.driver, self.web_tab, self.tv_tab, PARAMS, PARAMS['company']
            )

        self.assertEqual(outcome, 'opened')
        click_summary_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
