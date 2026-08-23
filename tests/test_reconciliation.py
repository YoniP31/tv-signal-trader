"""Mocked simulation of the crash-recovery/reporting decision logic in
multi_signal_source.py and tradinggenerator.report_trade_result -- no
browser needed. Run with:

    python -m unittest tests.test_reconciliation -v

This doesn't touch the real TradingView/TradingGenerator DOM (see the
'test' command in the running app for that, against a live page). It exists
to catch regressions in the *branching logic* -- which of several DOM
observations (working bracket? pending Trade Result prompt? stale Open
Trades card? which bracket status?) leads to which action (recover into the
ledger, report tp/sl/not_taken, quarantine, close a stale card) -- quickly
and repeatably.
"""

import unittest
from selenium.webdriver.common.by import By
from unittest.mock import MagicMock, patch

from tv_signal_trader import multi_signal_source as ms
from tv_signal_trader import tradinggenerator as tg
from tv_signal_trader import config


class FakeElement:
    def __init__(self, text, on_click=None):
        self.text = text
        self.clicked = False
        self._on_click = on_click

    def click(self):
        self.clicked = True
        if self._on_click:
            self._on_click()


class FakeOpenTradeCard:
    """Stands in for a TradingGenerator .open-trade-card element.

    `cards_list`, if given, is the same mutable list handed to
    _driver_with_cards -- clicking this card's Close button removes it from
    that list, simulating TradingGenerator actually removing the card from
    the DOM once closed. close_open_trade_cards relies on exactly this
    (re-querying fresh before every click rather than acting off one
    upfront snapshot, precisely because closing a card invalidates
    (stale-element) any already-held reference to the others), so tests
    exercising more than one close need this to behave realistically."""

    def __init__(self, company, portfolio, cards_list=None):
        self.company = company
        self.portfolio = portfolio
        self._cards_list = cards_list
        self.close_btn = FakeElement("✕ Close Trade", on_click=self._on_close)

    def _on_close(self):
        if self._cards_list is not None and self in self._cards_list:
            self._cards_list.remove(self)

    def find_element(self, by, selector):
        if selector == ".meta-company":
            return FakeElement(self.company)
        if selector == ".meta-portfolio":
            return FakeElement(self.portfolio)
        if selector == ".otl-close":
            return self.close_btn
        raise Exception(f"no such element: {selector}")


def _driver_with_cards(cards, buttons=()):
    """A MagicMock driver whose find_elements answers .open-trade-card
    lookups with the current contents of `cards` (a list -- pass the same
    list object to FakeOpenTradeCard(cards_list=...) if a test needs
    closing to actually shrink what's found on the next query) and
    TAG_NAME 'button' lookups with `buttons`."""
    driver = MagicMock()

    def fake_find_elements(by, selector):
        if by == By.CSS_SELECTOR and selector == ".open-trade-card":
            return list(cards)
        if by == By.TAG_NAME and selector == "button":
            return list(buttons)
        return []

    driver.find_elements.side_effect = fake_find_elements
    return driver


class ReportTradeResultFallbackTests(unittest.TestCase):
    """tg.report_trade_result: button-click path, and its fallback to the
    Open Trades grid's Close Trade button when the labeled button can't be
    found -- including picking the *right* card when several portfolios
    are open at once (the scenario from the original bug report)."""

    def test_clicks_the_labeled_button_when_present(self):
        btn = FakeElement("Take Profit")
        driver = _driver_with_cards(cards=[], buttons=[btn])
        self.assertTrue(tg.report_trade_result(driver, 'tp', company="C", portfolio="P"))
        self.assertTrue(btn.clicked)

    def test_falls_back_to_close_trade_card_when_button_missing(self):
        card = FakeOpenTradeCard("Apex Trader Funding", "APEX0007")
        driver = _driver_with_cards(cards=[card], buttons=[])
        self.assertTrue(
            tg.report_trade_result(driver, 'not_taken', company="Apex Trader Funding", portfolio="APEX0007")
        )
        self.assertTrue(card.close_btn.clicked)

    def test_selects_the_correct_card_among_several_open_portfolios(self):
        # Mirrors the real bug report: two portfolios of the same company
        # both showing under OPEN TRADES at once.
        card_a = FakeOpenTradeCard("Apex Trader Funding", "APEX0006")
        card_b = FakeOpenTradeCard("Apex Trader Funding", "APEX0007")
        driver = _driver_with_cards(cards=[card_a, card_b], buttons=[])

        tg.report_trade_result(driver, 'not_taken', company="Apex Trader Funding", portfolio="APEX0007")

        self.assertFalse(card_a.close_btn.clicked, "wrong card must not be touched")
        self.assertTrue(card_b.close_btn.clicked, "the matching company+portfolio card must be closed")

    def test_returns_false_when_neither_button_nor_card_found(self):
        driver = _driver_with_cards(cards=[], buttons=[])
        self.assertFalse(tg.report_trade_result(driver, 'tp', company="C", portfolio="P"))

    def test_no_fallback_attempted_without_company_or_portfolio(self):
        card = FakeOpenTradeCard("C", "P")
        driver = _driver_with_cards(cards=[card], buttons=[])
        self.assertFalse(tg.report_trade_result(driver, 'tp'))
        self.assertFalse(card.close_btn.clicked)


class OpenTradeCardsTests(unittest.TestCase):
    """tg.list_open_trade_cards/close_open_trade_card(s) -- real
    implementation (not mocked), covering the case that motivated this:
    TradingGenerator ending up with more than one card for the *same*
    company/portfolio (stale duplicates), even though Tradovate only ever
    allows one real open position per sub-account."""

    def test_list_returns_only_matching_cards_in_dom_order(self):
        newest = FakeOpenTradeCard("Apex Trader Funding", "APEX0007")
        older = FakeOpenTradeCard("Apex Trader Funding", "APEX0007")
        other_portfolio = FakeOpenTradeCard("Apex Trader Funding", "APEX0006")
        driver = _driver_with_cards(cards=[newest, older, other_portfolio])

        cards = tg.list_open_trade_cards(driver, "Apex Trader Funding", "APEX0007")

        self.assertEqual(cards, [newest, older])

    def test_close_open_trade_card_closes_only_the_newest(self):
        newest = FakeOpenTradeCard("Apex Trader Funding", "APEX0007")
        older = FakeOpenTradeCard("Apex Trader Funding", "APEX0007")
        driver = _driver_with_cards(cards=[newest, older])

        self.assertTrue(tg.close_open_trade_card(driver, "Apex Trader Funding", "APEX0007"))

        self.assertTrue(newest.close_btn.clicked)
        self.assertFalse(older.close_btn.clicked)

    def test_close_open_trade_cards_closes_all_by_default(self):
        # A shared, mutable cards_list: each card removes itself on close,
        # simulating TradingGenerator actually removing it from the DOM --
        # exactly the scenario close_open_trade_cards re-queries between
        # every click to handle correctly (see its docstring). A driver
        # that returned a fixed snapshot regardless of prior clicks would
        # incorrectly report only 1 closed here instead of 3 (this test
        # catches exactly the bug reported live: the second/third card
        # never actually got clicked).
        cards_list = []
        cards = [FakeOpenTradeCard("Apex Trader Funding", "APEX0007", cards_list=cards_list) for _ in range(3)]
        cards_list.extend(cards)
        driver = _driver_with_cards(cards=cards_list)

        with patch.object(tg.humanize, "pause"):
            closed = tg.close_open_trade_cards(driver, "Apex Trader Funding", "APEX0007")

        self.assertEqual(closed, 3)
        self.assertTrue(all(c.close_btn.clicked for c in cards))

    def test_close_open_trade_cards_keeps_newest_when_asked(self):
        cards_list = []
        newest = FakeOpenTradeCard("Apex Trader Funding", "APEX0007", cards_list=cards_list)
        older1 = FakeOpenTradeCard("Apex Trader Funding", "APEX0007", cards_list=cards_list)
        older2 = FakeOpenTradeCard("Apex Trader Funding", "APEX0007", cards_list=cards_list)
        cards_list.extend([newest, older1, older2])
        driver = _driver_with_cards(cards=cards_list)

        with patch.object(tg.humanize, "pause"):
            closed = tg.close_open_trade_cards(driver, "Apex Trader Funding", "APEX0007", keep_newest=True)

        self.assertEqual(closed, 2)
        self.assertFalse(newest.close_btn.clicked, "the newest (leftmost) card must be kept")
        self.assertTrue(older1.close_btn.clicked)
        self.assertTrue(older2.close_btn.clicked)

    def test_close_open_trade_cards_is_a_no_op_when_none_match(self):
        driver = _driver_with_cards(cards=[])
        self.assertEqual(tg.close_open_trade_cards(driver, "C", "P"), 0)
        self.assertEqual(tg.close_open_trade_cards(driver, "C", "P", keep_newest=True), 0)


class ReconciliationTests(unittest.TestCase):
    """_reconcile_open_positions_at_startup's per-portfolio branching."""

    def setUp(self):
        config.TRADOVATE_ACCOUNTS = {"Apex Trader Funding": {"username": "u", "password": "p"}}
        self.driver = MagicMock()
        self.web_tab, self.tv_tab = "web_tab", "tv_tab"
        self.company, self.portfolio = "Apex Trader Funding", "APEX1871970000007"
        # The reconciliation loop pauses briefly after re-selecting a
        # portfolio, to let TradingGenerator's UI settle -- not relevant to
        # any of this class's branching-logic assertions, so skip the real
        # sleep.
        self._humanize_pause_patcher = patch.object(ms.humanize, "pause")
        self._humanize_pause_patcher.start()
        self.addCleanup(self._humanize_pause_patcher.stop)

    def test_still_open_bracket_is_recovered_into_ledger(self):
        with patch.object(ms.tg, "list_all_candidates", return_value=[(self.company, self.portfolio)]), \
             patch.object(ms, "_ensure_tradovate_connection", return_value=self.company), \
             patch.object(ms.trading, "select_tradovate_account", return_value=True), \
             patch.object(ms.trading, "click_orders_tab", return_value=True), \
             patch.object(ms.trading, "find_working_bracket", return_value={"tp": "tp1", "sl": "sl1"}), \
             patch.object(ms, "_select_and_verify", return_value=True), \
             patch.object(ms.tg, "read_trade_parameters", return_value={
                 "asset": "ES", "direction": "LONG", "contracts": 4, "sl_ticks": 20,
                 "tp_ticks": 17, "account_type": "eval",
             }), \
             patch.object(ms.tg, "close_open_trade_cards", return_value=0) as close_cards_mock:
            open_positions, _ = ms._reconcile_open_positions_at_startup(self.driver, self.web_tab, self.tv_tab)
        self.assertIn((self.company, self.portfolio), open_positions)
        # Still checked even when nothing needed closing -- Tradovate only
        # allows one real open position per sub-account, so any other card
        # for this company/portfolio would be a stale duplicate.
        close_cards_mock.assert_called_once_with(
            self.driver, self.company, self.portfolio, keep_newest=True
        )

    def test_still_open_with_duplicate_cards_keeps_newest_closes_rest(self):
        # The scenario from the bug report: TradingGenerator's Open Trades
        # grid ends up with more than one card for the same account, even
        # though Tradovate confirms only one real position is open.
        with patch.object(ms.tg, "list_all_candidates", return_value=[(self.company, self.portfolio)]), \
             patch.object(ms, "_ensure_tradovate_connection", return_value=self.company), \
             patch.object(ms.trading, "select_tradovate_account", return_value=True), \
             patch.object(ms.trading, "click_orders_tab", return_value=True), \
             patch.object(ms.trading, "find_working_bracket", return_value={"tp": "tp1", "sl": "sl1"}), \
             patch.object(ms, "_select_and_verify", return_value=True), \
             patch.object(ms.tg, "read_trade_parameters", return_value={
                 "asset": "ES", "direction": "LONG", "contracts": 4, "sl_ticks": 20,
                 "tp_ticks": 17, "account_type": "eval",
             }), \
             patch.object(ms.tg, "close_open_trade_cards", return_value=2) as close_cards_mock:
            open_positions, _ = ms._reconcile_open_positions_at_startup(self.driver, self.web_tab, self.tv_tab)

        self.assertIn((self.company, self.portfolio), open_positions)
        close_cards_mock.assert_called_once_with(
            self.driver, self.company, self.portfolio, keep_newest=True
        )

    def test_pending_result_tp_is_reported_normally(self):
        with patch.object(ms.tg, "list_all_candidates", return_value=[(self.company, self.portfolio)]), \
             patch.object(ms, "_ensure_tradovate_connection", return_value=self.company), \
             patch.object(ms.trading, "select_tradovate_account", return_value=True), \
             patch.object(ms.trading, "click_orders_tab", return_value=True), \
             patch.object(ms.trading, "find_working_bracket", return_value={}), \
             patch.object(ms, "_select_and_verify", return_value=True), \
             patch.object(ms.tg, "has_pending_trade_result", return_value=True), \
             patch.object(ms.tg, "has_open_trade_card", return_value=False) as has_card_mock, \
             patch.object(ms.trading, "find_last_bracket", return_value={"tp": "tp1", "sl": "sl1"}), \
             patch.object(ms.trading, "check_bracket_status", return_value="tp"), \
             patch.object(ms.tg, "read_trade_parameters", return_value={
                 "asset": "ES", "direction": "LONG", "contracts": 4, "sl_ticks": 20, "tp_ticks": 17,
             }), \
             patch.object(ms.tg, "report_trade_result") as report_mock, \
             patch.object(ms.status, "record_trade_result") as record_mock, \
             patch.object(ms.tg, "close_open_trade_cards", return_value=0) as close_cards_mock:
            ms._reconcile_open_positions_at_startup(self.driver, self.web_tab, self.tv_tab)

        # The Open Trades grid is checked regardless of the pending Trade
        # Result prompt -- there could be extra stale duplicate cards on
        # top of an otherwise perfectly normal reportable trade.
        has_card_mock.assert_called_once_with(self.driver, self.company, self.portfolio)
        report_mock.assert_called_once_with(self.driver, "tp", company=self.company, portfolio=self.portfolio)
        self.assertEqual(record_mock.call_args.kwargs["result"], "tp")
        close_cards_mock.assert_called_once_with(
            self.driver, self.company, self.portfolio, keep_newest=False
        )

    def test_pending_result_with_extra_duplicate_cards_still_cleans_them_up(self):
        # Same as above, but this time there ARE extra stale duplicate
        # cards alongside the normal pending Trade Result prompt -- both
        # the normal report AND the duplicate cleanup must happen.
        with patch.object(ms.tg, "list_all_candidates", return_value=[(self.company, self.portfolio)]), \
             patch.object(ms, "_ensure_tradovate_connection", return_value=self.company), \
             patch.object(ms.trading, "select_tradovate_account", return_value=True), \
             patch.object(ms.trading, "click_orders_tab", return_value=True), \
             patch.object(ms.trading, "find_working_bracket", return_value={}), \
             patch.object(ms, "_select_and_verify", return_value=True), \
             patch.object(ms.tg, "has_pending_trade_result", return_value=True), \
             patch.object(ms.tg, "has_open_trade_card", return_value=True), \
             patch.object(ms.trading, "find_last_bracket", return_value={"tp": "tp1", "sl": "sl1"}), \
             patch.object(ms.trading, "check_bracket_status", return_value="sl"), \
             patch.object(ms.tg, "read_trade_parameters", return_value={
                 "asset": "ES", "direction": "LONG", "contracts": 4, "sl_ticks": 20, "tp_ticks": 17,
             }), \
             patch.object(ms.tg, "report_trade_result") as report_mock, \
             patch.object(ms.status, "record_trade_result") as record_mock, \
             patch.object(ms.tg, "close_open_trade_cards", return_value=2) as close_cards_mock:
            ms._reconcile_open_positions_at_startup(self.driver, self.web_tab, self.tv_tab)

        report_mock.assert_called_once_with(self.driver, "sl", company=self.company, portfolio=self.portfolio)
        self.assertEqual(record_mock.call_args.kwargs["result"], "sl")
        close_cards_mock.assert_called_once_with(
            self.driver, self.company, self.portfolio, keep_newest=False
        )

    def test_no_bracket_history_reports_not_taken_without_quarantine(self):
        with patch.object(ms.tg, "list_all_candidates", return_value=[(self.company, self.portfolio)]), \
             patch.object(ms, "_ensure_tradovate_connection", return_value=self.company), \
             patch.object(ms.trading, "select_tradovate_account", return_value=True), \
             patch.object(ms.trading, "click_orders_tab", return_value=True), \
             patch.object(ms.trading, "find_working_bracket", return_value={}), \
             patch.object(ms, "_select_and_verify", return_value=True), \
             patch.object(ms.tg, "has_pending_trade_result", return_value=True), \
             patch.object(ms.tg, "has_open_trade_card", return_value=False), \
             patch.object(ms.trading, "find_last_bracket", return_value={}), \
             patch.object(ms.tg, "read_trade_parameters", return_value={
                 "asset": "ES", "direction": "LONG", "contracts": 4, "sl_ticks": 20, "tp_ticks": 17,
             }), \
             patch.object(ms.tg, "report_trade_result") as report_mock, \
             patch.object(ms.status, "record_trade_result") as record_mock, \
             patch.object(ms.status, "mark_portfolio_unavailable") as mark_unavail_mock, \
             patch.object(ms.tg, "close_open_trade_cards", return_value=0):
            ms._reconcile_open_positions_at_startup(self.driver, self.web_tab, self.tv_tab)

        report_mock.assert_called_once_with(
            self.driver, "not_taken", company=self.company, portfolio=self.portfolio
        )
        self.assertEqual(record_mock.call_args.kwargs["result"], "not_taken")
        mark_unavail_mock.assert_not_called()

    def test_manual_close_reports_not_taken_and_quarantines(self):
        with patch.object(ms.tg, "list_all_candidates", return_value=[(self.company, self.portfolio)]), \
             patch.object(ms, "_ensure_tradovate_connection", return_value=self.company), \
             patch.object(ms.trading, "select_tradovate_account", return_value=True), \
             patch.object(ms.trading, "click_orders_tab", return_value=True), \
             patch.object(ms.trading, "find_working_bracket", return_value={}), \
             patch.object(ms, "_select_and_verify", return_value=True), \
             patch.object(ms.tg, "has_pending_trade_result", return_value=True), \
             patch.object(ms.tg, "has_open_trade_card", return_value=False), \
             patch.object(ms.trading, "find_last_bracket", return_value={"tp": "tp1", "sl": "sl1"}), \
             patch.object(ms.trading, "check_bracket_status", return_value="manual_close"), \
             patch.object(ms.tg, "read_trade_parameters", return_value={
                 "asset": "ES", "direction": "LONG", "contracts": 4, "sl_ticks": 20, "tp_ticks": 17,
             }), \
             patch.object(ms.tg, "report_trade_result") as report_mock, \
             patch.object(ms.status, "record_trade_result") as record_mock, \
             patch.object(ms.status, "mark_portfolio_unavailable") as mark_unavail_mock, \
             patch.object(ms.tg, "close_open_trade_cards", return_value=0) as close_cards_mock:
            ms._reconcile_open_positions_at_startup(self.driver, self.web_tab, self.tv_tab)

        report_mock.assert_called_once_with(
            self.driver, "not_taken", company=self.company, portfolio=self.portfolio
        )
        self.assertEqual(record_mock.call_args.kwargs["result"], "not_taken")
        mark_unavail_mock.assert_called_once_with(self.company, self.portfolio, "manual_close_or_liquidation")
        close_cards_mock.assert_called_once_with(
            self.driver, self.company, self.portfolio, keep_newest=False
        )

    def test_stale_open_card_without_pending_result_still_gets_processed(self):
        with patch.object(ms.tg, "list_all_candidates", return_value=[(self.company, self.portfolio)]), \
             patch.object(ms, "_ensure_tradovate_connection", return_value=self.company), \
             patch.object(ms.trading, "select_tradovate_account", return_value=True), \
             patch.object(ms.trading, "click_orders_tab", return_value=True), \
             patch.object(ms.trading, "find_working_bracket", return_value={}), \
             patch.object(ms, "_select_and_verify", return_value=True), \
             patch.object(ms.tg, "has_pending_trade_result", return_value=False), \
             patch.object(ms.tg, "has_open_trade_card", return_value=True), \
             patch.object(ms.trading, "find_last_bracket", return_value={"tp": "tp1", "sl": "sl1"}), \
             patch.object(ms.trading, "check_bracket_status", return_value="sl"), \
             patch.object(ms.tg, "read_trade_parameters", return_value={
                 "asset": "ES", "direction": "LONG", "contracts": 4, "sl_ticks": 20, "tp_ticks": 17,
             }), \
             patch.object(ms.tg, "report_trade_result") as report_mock, \
             patch.object(ms.status, "record_trade_result") as record_mock, \
             patch.object(ms.tg, "close_open_trade_cards", return_value=1) as close_cards_mock:
            ms._reconcile_open_positions_at_startup(self.driver, self.web_tab, self.tv_tab)

        # report_trade_result itself is responsible for falling back to the
        # Close Trade card when there's no button -- the reconciliation loop
        # just calls it the same way regardless of pending_result.
        report_mock.assert_called_once_with(self.driver, "sl", company=self.company, portfolio=self.portfolio)
        self.assertEqual(record_mock.call_args.kwargs["result"], "sl")
        close_cards_mock.assert_called_once_with(
            self.driver, self.company, self.portfolio, keep_newest=False
        )

    def test_nothing_pending_and_no_open_card_is_a_no_op(self):
        with patch.object(ms.tg, "list_all_candidates", return_value=[(self.company, self.portfolio)]), \
             patch.object(ms, "_ensure_tradovate_connection", return_value=self.company), \
             patch.object(ms.trading, "select_tradovate_account", return_value=True), \
             patch.object(ms.trading, "click_orders_tab", return_value=True), \
             patch.object(ms.trading, "find_working_bracket", return_value={}), \
             patch.object(ms, "_select_and_verify", return_value=True), \
             patch.object(ms.tg, "has_pending_trade_result", return_value=False), \
             patch.object(ms.tg, "has_open_trade_card", return_value=False), \
             patch.object(ms.trading, "find_last_bracket") as find_last_mock, \
             patch.object(ms.tg, "report_trade_result") as report_mock:
            open_positions, _ = ms._reconcile_open_positions_at_startup(self.driver, self.web_tab, self.tv_tab)

        find_last_mock.assert_not_called()
        report_mock.assert_not_called()
        self.assertEqual(open_positions, {})


class RefreshOpenPositionsTests(unittest.TestCase):
    """_refresh_open_positions' live-loop manual-close handling."""

    def setUp(self):
        config.TRADOVATE_ACCOUNTS = {"Apex Trader Funding": {"username": "u", "password": "p"}}
        self.driver = MagicMock()
        self.web_tab, self.tv_tab = "web_tab", "tv_tab"
        self.company, self.portfolio = "Apex Trader Funding", "APEX1871970000007"

    def test_manual_close_reports_not_taken_and_quarantines(self):
        open_positions = {
            (self.company, self.portfolio): {
                "tp_id": "tp1", "sl_id": "sl1", "account_type": "eval",
                "asset": "ES", "direction": "LONG", "contracts": 4, "sl_ticks": 20, "tp_ticks": 17,
            }
        }
        quarantined = set()

        with patch.object(ms, "_ensure_tradovate_connection", return_value=self.company), \
             patch.object(ms.trading, "select_tradovate_account", return_value=True), \
             patch.object(ms.trading, "click_orders_tab", return_value=True), \
             patch.object(ms.trading, "check_bracket_status", return_value="manual_close"), \
             patch.object(ms, "evaluate_account_for_removal",
                           return_value=("keep_normal", 25000, 27000, self.company)), \
             patch.object(ms, "_select_and_verify", return_value=True), \
             patch.object(ms.status, "update_portfolio"), \
             patch.object(ms.tg, "report_trade_result") as report_mock, \
             patch.object(ms.status, "record_trade_result") as record_mock, \
             patch.object(ms.status, "mark_portfolio_unavailable") as mark_unavail_mock:
            ms._refresh_open_positions(
                self.driver, self.web_tab, self.tv_tab, open_positions, self.company, quarantined
            )

        report_mock.assert_called_once_with(
            self.driver, "not_taken", company=self.company, portfolio=self.portfolio
        )
        self.assertEqual(record_mock.call_args.kwargs["result"], "not_taken")
        mark_unavail_mock.assert_called_once_with(self.company, self.portfolio, "manual_close_or_liquidation")
        self.assertIn((self.company, self.portfolio), quarantined)
        self.assertEqual(open_positions, {})


if __name__ == "__main__":
    unittest.main()
