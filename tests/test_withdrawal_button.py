"""Covers tradinggenerator.py's #withdrawalBtn automation:
is_withdrawal_submitted/submit_withdrawal (see the Flip Mode plan --
this version submits an account for withdrawal instead of removing its
TradingGenerator portfolio once it qualifies for removal).

Both wordings confirmed live: "💰 Submit Withdrawal" not submitted, "✓
Withdrawal Submitted — click to release" once submitted -- a genuine
single toggle button, same mechanics as #flipModeBtn/#secondWithdrawalBtn
(admin-code prompt included), and the same class of bug already caught
live for #secondWithdrawalBtn applies here too: re-clicking while already
submitted would release it right back, which is exactly why
submit_withdrawal still refuses to click again once already submitted,
even though the *intended* usage is that only a human ever releases it.
Run with:

    python -m unittest tests.test_withdrawal_button -v
"""

import unittest
from unittest.mock import MagicMock, patch

from tv_signal_trader import tradinggenerator as tg


class FakeButton:
    def __init__(self, text):
        self.text = text
        self.click_count = 0

    def click(self):
        self.click_count += 1


class FakeAlert:
    def __init__(self):
        self.sent_keys = []
        self.accepted = False

    def send_keys(self, text):
        self.sent_keys.append(text)

    def accept(self):
        self.accepted = True


class RaisingSwitchTo:
    """driver.switch_to.alert raises, as real Selenium does when no native
    dialog is currently open."""

    @property
    def alert(self):
        raise Exception("no alert present")


class AlertSwitchTo:
    def __init__(self, alert):
        self._alert = alert

    @property
    def alert(self):
        return self._alert


def _driver_with_button(button, switch_to=None):
    driver = MagicMock()

    def fake_find_element(by, selector):
        if selector == "withdrawalBtn":
            return button
        raise Exception("not found")

    driver.find_element.side_effect = fake_find_element
    if switch_to is not None:
        driver.switch_to = switch_to
    return driver


class IsWithdrawalSubmittedTests(unittest.TestCase):
    def test_reads_the_confirmed_unsubmitted_label_as_not_submitted(self):
        driver = _driver_with_button(FakeButton("💰 Submit Withdrawal"))
        self.assertFalse(tg.is_withdrawal_submitted(driver))

    def test_reads_the_confirmed_submitted_label_as_submitted(self):
        driver = _driver_with_button(FakeButton("✓ Withdrawal Submitted — click to release"))
        self.assertTrue(tg.is_withdrawal_submitted(driver))

    def test_an_unrecognized_label_returns_none(self):
        driver = _driver_with_button(FakeButton("Withdrawal"))
        self.assertIsNone(tg.is_withdrawal_submitted(driver))

    def test_a_missing_button_returns_none(self):
        driver = MagicMock()
        driver.find_element.side_effect = Exception("not found")
        self.assertIsNone(tg.is_withdrawal_submitted(driver))


class SubmitWithdrawalTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(tg, "humanize")
        self.addCleanup(patcher.stop)
        patcher.start()

    def test_clicks_and_sends_the_code_when_not_yet_submitted(self):
        button = FakeButton("💰 Submit Withdrawal")
        alert = FakeAlert()

        # After the click, is_withdrawal_submitted must read the button
        # as submitted for submit_withdrawal to consider it successful.
        def fake_find_element(by, selector):
            if selector == "withdrawalBtn":
                button.text = (
                    "✓ Withdrawal Submitted — click to release" if button.click_count
                    else "💰 Submit Withdrawal"
                )
                return button
            raise Exception("not found")

        driver = MagicMock()
        driver.find_element.side_effect = fake_find_element
        driver.switch_to = AlertSwitchTo(alert)

        result = tg.submit_withdrawal(driver, "1234")
        self.assertTrue(result)
        self.assertEqual(button.click_count, 1)
        self.assertEqual(alert.sent_keys, ["1234"])

    def test_no_ops_when_already_submitted(self):
        button = FakeButton("✓ Withdrawal Submitted — click to release")
        driver = _driver_with_button(button)
        result = tg.submit_withdrawal(driver, "1234")
        self.assertTrue(result)
        self.assertEqual(button.click_count, 0)

    def test_returns_false_and_never_clicks_when_the_label_is_unrecognized(self):
        # The exact bug seen live for #secondWithdrawalBtn: clicking a
        # single-toggle button while it's already in the target state
        # cancels/releases it right back. Refusing to click when the
        # state can't be confirmed guards against that here too.
        button = FakeButton("Withdrawal")
        driver = _driver_with_button(button)
        result = tg.submit_withdrawal(driver, "1234")
        self.assertFalse(result)
        self.assertEqual(button.click_count, 0)

    def test_returns_false_if_no_prompt_appears_after_clicking(self):
        button = FakeButton("💰 Submit Withdrawal")
        driver = _driver_with_button(button, switch_to=RaisingSwitchTo())
        result = tg.submit_withdrawal(driver, "1234")
        self.assertFalse(result)

    def test_returns_false_if_the_label_never_flips_after_the_prompt(self):
        button = FakeButton("💰 Submit Withdrawal")
        alert = FakeAlert()
        driver = _driver_with_button(button, switch_to=AlertSwitchTo(alert))
        result = tg.submit_withdrawal(driver, "1234")
        self.assertFalse(result)

    def test_never_clicks_twice_across_repeated_calls_once_submitted(self):
        # There is no release_withdrawal -- once submitted, every further
        # call must keep no-op'ing, never clicking (and so never
        # releasing it) again.
        button = FakeButton("💰 Submit Withdrawal")
        alert = FakeAlert()

        def fake_find_element(by, selector):
            if selector == "withdrawalBtn":
                button.text = (
                    "✓ Withdrawal Submitted — click to release" if button.click_count
                    else "💰 Submit Withdrawal"
                )
                return button
            raise Exception("not found")

        driver = MagicMock()
        driver.find_element.side_effect = fake_find_element
        driver.switch_to = AlertSwitchTo(alert)

        self.assertTrue(tg.submit_withdrawal(driver, "1234"))
        self.assertTrue(tg.submit_withdrawal(driver, "1234"))
        self.assertEqual(button.click_count, 1)


if __name__ == "__main__":
    unittest.main()
