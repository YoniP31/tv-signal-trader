"""Covers tradinggenerator.py's add_company/add_portfolio (used by the
'add_accounts' command in cli.py to bulk-create TradingGenerator
portfolios for one company from a .txt file) and the
_click_add_portfolio_button helper, built against the real HTML for
#portfolioBar (which has *two* buttons sharing the same
"add-portfolio-btn" class -- '+ Portfolio' and the bulk '+ N Portfolios'
-- so picking the right one by text, not DOM position, is the point of
this module). Run with:

    python -m unittest tests.test_add_company_portfolio -v
"""

import unittest
from unittest.mock import MagicMock, patch

from tv_signal_trader import tradinggenerator as tg


class FakeButton:
    def __init__(self, text, displayed=True):
        self.text = text
        self._displayed = displayed
        self.click_count = 0

    def is_displayed(self):
        return self._displayed

    def click(self):
        self.click_count += 1


def _driver_with_portfolio_bar_buttons(buttons):
    driver = MagicMock()

    def fake_find_elements(by, selector):
        if selector == "#portfolioBar .add-portfolio-btn":
            return buttons
        return []

    driver.find_elements.side_effect = fake_find_elements
    return driver


class ClickAddPortfolioButtonTests(unittest.TestCase):
    def test_clicks_the_single_portfolio_button_by_its_text(self):
        # Matches the real HTML: '+ Portfolio' listed before the bulk
        # '+ 5 Portfolios' button, both sharing the same class.
        single = FakeButton("+ Portfolio")
        bulk = FakeButton("+ 5 Portfolios")
        driver = _driver_with_portfolio_bar_buttons([single, bulk])
        with patch.object(tg.humanize, "long_pause"):
            result = tg._click_add_portfolio_button(driver)
        self.assertTrue(result)
        self.assertEqual(single.click_count, 1)
        self.assertEqual(bulk.click_count, 0)

    def test_still_picks_the_right_button_when_the_bulk_one_comes_first(self):
        # Proves this matches by text, not "whichever comes first in the
        # DOM" -- a coincidence in the real markup that a correct
        # implementation shouldn't quietly depend on.
        bulk = FakeButton("+ 5 Portfolios")
        single = FakeButton("+ Portfolio")
        driver = _driver_with_portfolio_bar_buttons([bulk, single])
        with patch.object(tg.humanize, "long_pause"):
            result = tg._click_add_portfolio_button(driver)
        self.assertTrue(result)
        self.assertEqual(single.click_count, 1)
        self.assertEqual(bulk.click_count, 0)

    def test_returns_false_if_only_the_bulk_button_is_present(self):
        bulk = FakeButton("+ 5 Portfolios")
        driver = _driver_with_portfolio_bar_buttons([bulk])
        with patch.object(tg.humanize, "pause"):
            result = tg._click_add_portfolio_button(driver, attempts=1)
        self.assertFalse(result)
        self.assertEqual(bulk.click_count, 0)

    def test_ignores_a_hidden_single_portfolio_button(self):
        hidden = FakeButton("+ Portfolio", displayed=False)
        driver = _driver_with_portfolio_bar_buttons([hidden])
        with patch.object(tg.humanize, "pause"):
            result = tg._click_add_portfolio_button(driver, attempts=1)
        self.assertFalse(result)


class AddCompanyTests(unittest.TestCase):
    def setUp(self):
        self.driver = MagicMock()
        self.name_input = MagicMock()
        self.driver.find_element.return_value = self.name_input
        patcher = patch.object(tg, "humanize")
        self.addCleanup(patcher.stop)
        patcher.start()

    def test_creates_and_selects_the_new_company(self):
        with patch.object(tg, "_click_visible", return_value=True) as click_mock, \
             patch.object(tg.panel, "set_field") as set_field_mock, \
             patch.object(tg, "list_companies", return_value=["TopStep"]), \
             patch.object(tg, "select_company") as select_mock:
            result = tg.add_company(self.driver, "TopStep")
        self.assertTrue(result)
        set_field_mock.assert_called_once_with(self.driver, self.name_input, "TopStep")
        select_mock.assert_called_once_with(self.driver, "TopStep")
        click_mock.assert_any_call(self.driver, "#companiesBar .add-btn-company")
        click_mock.assert_any_call(self.driver, ".btn-confirm-purple")

    def test_returns_false_if_the_add_button_is_never_found(self):
        with patch.object(tg, "_click_visible", return_value=False), \
             patch.object(tg.panel, "set_field") as set_field_mock:
            result = tg.add_company(self.driver, "TopStep")
        self.assertFalse(result)
        set_field_mock.assert_not_called()

    def test_returns_false_if_the_name_field_is_missing(self):
        self.driver.find_element.side_effect = Exception("not found")
        with patch.object(tg, "_click_visible", return_value=True):
            result = tg.add_company(self.driver, "TopStep")
        self.assertFalse(result)

    def test_returns_false_if_confirming_fails(self):
        def fake_click_visible(driver, selector, attempts=6):
            return selector != ".btn-confirm-purple"
        with patch.object(tg, "_click_visible", side_effect=fake_click_visible), \
             patch.object(tg.panel, "set_field"):
            result = tg.add_company(self.driver, "TopStep")
        self.assertFalse(result)

    def test_returns_false_if_the_company_never_shows_up_afterward(self):
        # Confirm button was clicked, but the new tab still doesn't appear
        # -- something went wrong TradingGenerator-side that isn't
        # reducible to any single failed click above.
        with patch.object(tg, "_click_visible", return_value=True), \
             patch.object(tg.panel, "set_field"), \
             patch.object(tg, "list_companies", return_value=[]), \
             patch.object(tg, "select_company") as select_mock:
            result = tg.add_company(self.driver, "TopStep")
        self.assertFalse(result)
        select_mock.assert_not_called()


class AddPortfolioTests(unittest.TestCase):
    def setUp(self):
        self.driver = MagicMock()
        self.name_input = MagicMock()
        self.driver.find_element.return_value = self.name_input
        patcher = patch.object(tg, "humanize")
        self.addCleanup(patcher.stop)
        patcher.start()

    def test_creates_a_live_portfolio_without_touching_the_type_selector(self):
        with patch.object(tg, "_click_add_portfolio_button", return_value=True), \
             patch.object(tg.panel, "set_field") as set_field_mock, \
             patch.object(tg, "_click_visible", return_value=True) as click_mock, \
             patch.object(tg, "list_portfolios", return_value=["APEX007"]):
            result = tg.add_portfolio(self.driver, "APEX007")
        self.assertTrue(result)
        set_field_mock.assert_called_once_with(self.driver, self.name_input, "APEX007")
        # Only the confirm button, never #optPaper -- 'live' matches the
        # modal's own default, so nothing else should be clicked.
        click_mock.assert_called_once_with(self.driver, ".btn-confirm-green")

    def test_creates_an_eval_portfolio_by_clicking_the_paper_option_first(self):
        with patch.object(tg, "_click_add_portfolio_button", return_value=True), \
             patch.object(tg.panel, "set_field"), \
             patch.object(tg, "_click_visible", return_value=True) as click_mock, \
             patch.object(tg, "list_portfolios", return_value=["APEX007"]):
            result = tg.add_portfolio(self.driver, "APEX007", account_type='eval')
        self.assertTrue(result)
        click_mock.assert_any_call(self.driver, "#optPaper")
        click_mock.assert_any_call(self.driver, ".btn-confirm-green")

    def test_returns_false_if_the_add_portfolio_button_is_never_found(self):
        with patch.object(tg, "_click_add_portfolio_button", return_value=False), \
             patch.object(tg.panel, "set_field") as set_field_mock:
            result = tg.add_portfolio(self.driver, "APEX007")
        self.assertFalse(result)
        set_field_mock.assert_not_called()

    def test_returns_false_if_selecting_eval_fails(self):
        def fake_click_visible(driver, selector, attempts=6):
            return selector != "#optPaper"
        with patch.object(tg, "_click_add_portfolio_button", return_value=True), \
             patch.object(tg.panel, "set_field"), \
             patch.object(tg, "_click_visible", side_effect=fake_click_visible):
            result = tg.add_portfolio(self.driver, "APEX007", account_type='eval')
        self.assertFalse(result)

    def test_returns_false_if_the_portfolio_never_shows_up_afterward(self):
        with patch.object(tg, "_click_add_portfolio_button", return_value=True), \
             patch.object(tg.panel, "set_field"), \
             patch.object(tg, "_click_visible", return_value=True), \
             patch.object(tg, "list_portfolios", return_value=[]):
            result = tg.add_portfolio(self.driver, "APEX007")
        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
