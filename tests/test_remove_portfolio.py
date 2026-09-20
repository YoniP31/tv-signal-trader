"""Covers tradinggenerator.list_portfolios/remove_portfolio end to end at
the DOM level -- the exact pair signal_source.sweep_liquidated_accounts
uses to find and remove a portfolio whose account no longer appears in
Tradovate's account list (a liquidated account). Written to directly
verify these weren't harmed by the _tab_name fix (see
tests/test_tab_name.py and the Flip Mode plan's Phase 3): a portfolio
marked for Second Withdrawal gets an extra "2nd" badge span prepended to
its tab, and before that fix, list_portfolios/remove_portfolio would have
silently misread or failed to find such a tab by its real account name.
Run with:

    python -m unittest tests.test_remove_portfolio -v
"""

import unittest
from unittest.mock import MagicMock, patch

from tv_signal_trader import tradinggenerator as tg


class FakeSpan:
    def __init__(self, text):
        self.text = text


class FakeDeleteButton:
    def __init__(self):
        self.click_count = 0

    def click(self):
        self.click_count += 1


class FakeTab:
    """A fake #portfolioBar .portfolio-tab element: find_elements returns
    its name span(s) (standing in for what the real CSS selector already
    filtered down to -- see test_tab_name.py's FakeSpan for the same
    simplification), find_element returns its delete button."""

    def __init__(self, *span_texts, has_delete_button=True):
        self._spans = [FakeSpan(text) for text in span_texts]
        self.delete_button = FakeDeleteButton() if has_delete_button else None

    def find_elements(self, by, selector):
        return self._spans

    def find_element(self, by, selector):
        if self.delete_button is None:
            raise Exception("no delete button")
        return self.delete_button


def _driver_with_portfolio_tabs(tabs):
    driver = MagicMock()

    def fake_find_elements(by, selector):
        if selector == "#portfolioBar .portfolio-tab":
            return tabs
        return []

    driver.find_elements.side_effect = fake_find_elements
    return driver


class ListPortfoliosWithBadgeTests(unittest.TestCase):
    def test_a_plain_portfolio_is_read_correctly(self):
        driver = _driver_with_portfolio_tabs([FakeTab("PAAPEX0001")])
        self.assertEqual(tg.list_portfolios(driver), ["PAAPEX0001"])

    def test_a_second_withdrawal_marked_portfolio_still_reads_its_real_name(self):
        # Real DOM order confirmed live: the "2nd" badge span sits before
        # the account name span. Before the _tab_name fix, this would
        # have returned "2nd" instead of "PAAPEX1871970000001".
        driver = _driver_with_portfolio_tabs([FakeTab("2nd", "PAAPEX1871970000001")])
        self.assertEqual(tg.list_portfolios(driver), ["PAAPEX1871970000001"])

    def test_a_mix_of_plain_and_badged_portfolios_all_read_correctly(self):
        driver = _driver_with_portfolio_tabs([
            FakeTab("PAAPEX0001"),
            FakeTab("2nd", "PAAPEX0002"),
            FakeTab("PAAPEX0003"),
        ])
        self.assertEqual(tg.list_portfolios(driver), ["PAAPEX0001", "PAAPEX0002", "PAAPEX0003"])


class RemovePortfolioWithBadgeTests(unittest.TestCase):
    def test_removes_a_plain_portfolio_by_name(self):
        tab = FakeTab("PAAPEX0001")
        driver = _driver_with_portfolio_tabs([tab])
        with patch.object(tg, "_click_visible", return_value=True), \
             patch.object(tg.humanize, "long_pause"):
            result = tg.remove_portfolio(driver, "PAAPEX0001")
        self.assertTrue(result)
        self.assertEqual(tab.delete_button.click_count, 1)

    def test_removes_a_second_withdrawal_marked_portfolio_by_its_real_name(self):
        # The exact liquidation-removal scenario: sweep_liquidated_accounts
        # calls remove_portfolio with the real account name it read from
        # Tradovate's account list -- it must still match this tab even
        # though the tab itself carries the extra "2nd" badge span.
        tab = FakeTab("2nd", "PAAPEX1871970000001")
        driver = _driver_with_portfolio_tabs([tab])
        with patch.object(tg, "_click_visible", return_value=True), \
             patch.object(tg.humanize, "long_pause"):
            result = tg.remove_portfolio(driver, "PAAPEX1871970000001")
        self.assertTrue(result)
        self.assertEqual(tab.delete_button.click_count, 1)

    def test_does_not_confuse_a_badged_tab_with_a_different_plain_one(self):
        # Two tabs present -- only the one whose real name matches should
        # ever get clicked, regardless of which one carries the badge.
        plain_tab = FakeTab("PAAPEX0001")
        badged_tab = FakeTab("2nd", "PAAPEX0002")
        driver = _driver_with_portfolio_tabs([plain_tab, badged_tab])
        with patch.object(tg, "_click_visible", return_value=True), \
             patch.object(tg.humanize, "long_pause"):
            result = tg.remove_portfolio(driver, "PAAPEX0002")
        self.assertTrue(result)
        self.assertEqual(badged_tab.delete_button.click_count, 1)
        self.assertEqual(plain_tab.delete_button.click_count, 0)

    def test_a_portfolio_not_present_is_not_found(self):
        driver = _driver_with_portfolio_tabs([FakeTab("2nd", "PAAPEX0002")])
        with patch.object(tg, "_click_visible") as click_visible_mock, \
             patch.object(tg.humanize, "long_pause"):
            result = tg.remove_portfolio(driver, "PAAPEX9999")
        self.assertFalse(result)
        click_visible_mock.assert_not_called()


class LiquidationSweepScenarioTests(unittest.TestCase):
    """Mirrors signal_source.sweep_liquidated_accounts' own removal
    condition (`if portfolio not in tradovate_accounts`) directly against
    list_portfolios' real output, to confirm the badge doesn't cause a
    still-live account to look liquidated (or vice versa)."""

    def test_a_badged_account_still_present_in_tradovate_is_not_flagged_liquidated(self):
        driver = _driver_with_portfolio_tabs([FakeTab("2nd", "PAAPEX1871970000001")])
        tg_portfolios = tg.list_portfolios(driver)
        tradovate_accounts = ["PAAPEX1871970000001"]
        to_remove = [p for p in tg_portfolios if p not in tradovate_accounts]
        self.assertEqual(to_remove, [])

    def test_a_badged_account_missing_from_tradovate_is_correctly_flagged_liquidated(self):
        driver = _driver_with_portfolio_tabs([FakeTab("2nd", "PAAPEX1871970000001")])
        tg_portfolios = tg.list_portfolios(driver)
        tradovate_accounts = []  # liquidated -- no longer in Tradovate's account list
        to_remove = [p for p in tg_portfolios if p not in tradovate_accounts]
        self.assertEqual(to_remove, ["PAAPEX1871970000001"])


if __name__ == "__main__":
    unittest.main()
