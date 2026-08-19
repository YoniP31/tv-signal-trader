"""Covers the "single Tradovate account" edge case in trading.py: when a
company's Tradovate login has only one sub-account, TradingView renders
the account-selector as a plain read-only label (an extra
"singleAccountButton-<hash>" class, no real dropdown behind it) instead of
the usual dropdown trigger. Run with:

    python -m unittest tests.test_single_account_mode -v

Real bug this guards against: clicking the selector in this state never
opens '[data-qa-id="account-dropdown"]', which list_tradovate_accounts and
select_tradovate_account both used to treat identically to "the dropdown
failed to open" -- the exact same "confirmed-empty vs failed-read"
ambiguity this codebase already got burned by once (the daily sweep
mistakenly deleting every portfolio when the account list read failed).
"""

import unittest
from unittest.mock import MagicMock, patch

from selenium.webdriver.common.by import By

from tv_signal_trader import trading


SINGLE_ACCOUNT_CLASSES = (
    "dropdownButton-_BZVWXcP singleAccountButton-_BZVWXcP lightButton-kjRfTlfx "
    "quiet-primary-IeQwPjcZ gray-IeQwPjcZ medium-kjRfTlfx typography-regular16px-kjRfTlfx"
)
MULTI_ACCOUNT_CLASSES = (
    "dropdownButton-_BZVWXcP lightButton-kjRfTlfx quiet-primary-IeQwPjcZ "
    "gray-IeQwPjcZ medium-kjRfTlfx typography-regular16px-kjRfTlfx"
)


class FakeTextElement:
    def __init__(self, text):
        self.text = text


class FakeSelectorButton:
    """Mirrors the real <button data-qa-id="account-selector"> structure
    from the reported HTML -- both the single- and multi-account variants
    share the same outer button, differing only in the extra class and
    (for single-account) having the account name directly findable inside
    it via the "accountName-<hash>" class."""

    def __init__(self, classes, account_name=None, text=None):
        self._classes = classes
        self._account_name = account_name
        self.text = text if text is not None else (account_name or "")

    def get_attribute(self, name):
        if name == "class":
            return self._classes
        return None

    def find_element(self, by, selector):
        if selector == '[class*="accountName-"]' and self._account_name is not None:
            return FakeTextElement(self._account_name)
        raise Exception(f"no such element: {selector}")

    def click(self):
        pass


class IsSingleAccountModeTests(unittest.TestCase):
    def test_true_when_the_single_account_class_is_present(self):
        btn = FakeSelectorButton(SINGLE_ACCOUNT_CLASSES, account_name="APEX1871970000007")
        self.assertTrue(trading._is_single_account_mode(btn))

    def test_false_for_the_normal_multi_account_button(self):
        btn = FakeSelectorButton(MULTI_ACCOUNT_CLASSES, account_name="APEX1871970000007")
        self.assertFalse(trading._is_single_account_mode(btn))

    def test_false_when_class_attribute_is_missing_entirely(self):
        btn = MagicMock()
        btn.get_attribute.return_value = None
        self.assertFalse(trading._is_single_account_mode(btn))


class ReadSingleAccountNameTests(unittest.TestCase):
    def test_reads_the_account_name_from_the_button(self):
        btn = FakeSelectorButton(SINGLE_ACCOUNT_CLASSES, account_name="APEX1871970000007")
        self.assertEqual(trading._read_single_account_name(btn), "APEX1871970000007")

    def test_returns_none_if_the_name_span_is_missing(self):
        btn = FakeSelectorButton(SINGLE_ACCOUNT_CLASSES, account_name=None)
        self.assertIsNone(trading._read_single_account_name(btn))


def _driver_with_selector(selector_btn):
    driver = MagicMock()

    def fake_find_element(by, selector):
        if selector == '[data-qa-id="account-selector"]':
            return selector_btn
        raise Exception(f"unexpected selector: {selector}")

    driver.find_element.side_effect = fake_find_element
    return driver


class ListTradovateAccountsSingleModeTests(unittest.TestCase):
    def test_returns_the_one_account_without_opening_a_dropdown(self):
        btn = FakeSelectorButton(SINGLE_ACCOUNT_CLASSES, account_name="APEX1871970000007")
        driver = _driver_with_selector(btn)
        with patch.object(trading, "_ensure_broker_panel_open", return_value=True), \
             patch.object(trading, "humanize"):
            result = trading.list_tradovate_accounts(driver)
        self.assertEqual(result, ["APEX1871970000007"])
        # Never attempted to click the selector to open a dropdown.
        driver.execute_script.assert_not_called()

    def test_returns_none_if_the_single_account_name_cannot_be_read(self):
        btn = FakeSelectorButton(SINGLE_ACCOUNT_CLASSES, account_name=None)
        driver = _driver_with_selector(btn)
        with patch.object(trading, "_ensure_broker_panel_open", return_value=True):
            result = trading.list_tradovate_accounts(driver)
        self.assertIsNone(result)

    def test_multi_account_mode_is_unaffected(self):
        # Regression: the normal dropdown-based path must still work
        # exactly as before when the single-account class isn't present.
        btn = FakeSelectorButton(MULTI_ACCOUNT_CLASSES, text="")
        dropdown = MagicMock()
        dropdown.find_elements.return_value = [FakeTextElement("ACC1"), FakeTextElement("ACC2")]
        driver = MagicMock()

        def fake_find_element(by, selector):
            if selector == '[data-qa-id="account-selector"]':
                return btn
            if selector == '[data-qa-id="account-dropdown"]':
                return dropdown
            raise Exception(f"unexpected selector: {selector}")

        driver.find_element.side_effect = fake_find_element
        with patch.object(trading, "_ensure_broker_panel_open", return_value=True), \
             patch.object(trading, "humanize"):
            result = trading.list_tradovate_accounts(driver)
        self.assertEqual(result, ["ACC1", "ACC2"])
        self.assertTrue(driver.execute_script.called)


class SelectTradovateAccountSingleModeTests(unittest.TestCase):
    def test_wanted_account_matches_the_single_account_already_shown(self):
        btn = FakeSelectorButton(
            SINGLE_ACCOUNT_CLASSES, account_name="APEX1871970000007", text="APEX1871970000007 USD"
        )
        driver = _driver_with_selector(btn)
        with patch.object(trading, "_ensure_broker_panel_open", return_value=True):
            result = trading.select_tradovate_account(driver, "APEX1871970000007")
        self.assertTrue(result)
        # No dropdown click attempted -- the "already selected" shortcut
        # handles this without needing single-account detection at all.
        driver.execute_script.assert_not_called()

    def test_wanted_account_does_not_match_the_single_account(self):
        btn = FakeSelectorButton(
            SINGLE_ACCOUNT_CLASSES, account_name="APEX1871970000007", text="APEX1871970000007 USD"
        )
        driver = _driver_with_selector(btn)
        with patch.object(trading, "_ensure_broker_panel_open", return_value=True):
            result = trading.select_tradovate_account(driver, "SOME_OTHER_ACCOUNT")
        self.assertFalse(result)
        # Must not try to click and wait for a dropdown that will never
        # appear.
        driver.execute_script.assert_not_called()


if __name__ == "__main__":
    unittest.main()
