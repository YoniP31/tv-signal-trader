"""Covers tradinggenerator.py's Flip Mode / Second Withdrawal button
automation: is_flip_mode_active, enable_flip_mode/disable_flip_mode,
is_second_withdrawal_marked, mark_second_withdrawal, and the shared native
window.prompt() helper _send_admin_code_to_prompt they're all built on.

Both buttons are confirmed live to be genuine single toggles, and both
functions' wording is confirmed both ways: #flipModeBtn reads "Enable Flip
Mode" off / "FLIP MODE ON — click to turn off" on; #secondWithdrawalBtn
reads "Mark as Second Withdrawal" unmarked / "Second Withdrawal — click to
cancel" marked (an earlier version wrongly assumed the second one was a
one-way flag safe to click repeatedly -- confirmed live that re-clicking
it while already marked actually cancels it back off). Both
_toggle_flip_mode and mark_second_withdrawal now refuse to click at all
when the current state can't be read, rather than guess -- guessing wrong
on a toggle risks flipping it the *wrong* way. Run with:

    python -m unittest tests.test_flip_mode_buttons -v
"""

import unittest
from unittest.mock import MagicMock, patch

from tv_signal_trader import tradinggenerator as tg


class FakeButton:
    def __init__(self, text, disabled=False):
        self.text = text
        self._disabled = disabled
        self.click_count = 0

    def click(self):
        self.click_count += 1

    def get_attribute(self, name):
        if name == "disabled":
            return "true" if self._disabled else None
        return None


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


def _driver_with_button(element_id, button, switch_to=None):
    driver = MagicMock()

    def fake_find_element(by, selector):
        if selector == element_id:
            return button
        raise Exception("not found")

    driver.find_element.side_effect = fake_find_element
    if switch_to is not None:
        driver.switch_to = switch_to
    return driver


class SendAdminCodeToPromptTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(tg, "humanize")
        self.addCleanup(patcher.stop)
        patcher.start()

    def test_sends_the_code_and_accepts_when_an_alert_is_present(self):
        alert = FakeAlert()
        driver = MagicMock()
        driver.switch_to = AlertSwitchTo(alert)
        result = tg._send_admin_code_to_prompt(driver, "1234")
        self.assertTrue(result)
        self.assertEqual(alert.sent_keys, ["1234"])
        self.assertTrue(alert.accepted)

    def test_returns_false_if_no_alert_ever_appears(self):
        driver = MagicMock()
        driver.switch_to = RaisingSwitchTo()
        result = tg._send_admin_code_to_prompt(driver, "1234", attempts=1)
        self.assertFalse(result)


class IsFlipModeActiveTests(unittest.TestCase):
    def test_reads_the_confirmed_on_label_as_active(self):
        driver = _driver_with_button("flipModeBtn", FakeButton("🔁 FLIP MODE ON — click to turn off"))
        self.assertTrue(tg.is_flip_mode_active(driver))

    def test_reads_the_confirmed_off_label_as_inactive(self):
        driver = _driver_with_button("flipModeBtn", FakeButton("🔄 Enable Flip Mode"))
        self.assertFalse(tg.is_flip_mode_active(driver))

    def test_an_unrecognized_label_returns_none(self):
        driver = _driver_with_button("flipModeBtn", FakeButton("Flip Mode"))
        self.assertIsNone(tg.is_flip_mode_active(driver))

    def test_the_old_wrong_guess_no_longer_matches_as_active(self):
        # "Disable Flip Mode" was the original (wrong) guess for the "on"
        # wording -- confirm it's *not* mistaken for a match now that the
        # real wording is known, in case it ever coincidentally appears.
        driver = _driver_with_button("flipModeBtn", FakeButton("Disable Flip Mode"))
        self.assertIsNone(tg.is_flip_mode_active(driver))

    def test_a_missing_button_returns_none(self):
        driver = MagicMock()
        driver.find_element.side_effect = Exception("not found")
        self.assertIsNone(tg.is_flip_mode_active(driver))


class ToggleFlipModeTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(tg, "humanize")
        self.addCleanup(patcher.stop)
        patcher.start()

    def test_enable_clicks_and_sends_the_code_when_currently_disabled(self):
        button = FakeButton("Enable Flip Mode")
        alert = FakeAlert()
        driver = _driver_with_button("flipModeBtn", button, switch_to=AlertSwitchTo(alert))

        # After the click, is_flip_mode_active must read the button as
        # active for enable_flip_mode to consider it successful.
        def fake_find_element(by, selector):
            if selector == "flipModeBtn":
                button.text = "FLIP MODE ON — click to turn off" if button.click_count else "Enable Flip Mode"
                return button
            raise Exception("not found")
        driver.find_element.side_effect = fake_find_element

        result = tg.enable_flip_mode(driver, "1234")
        self.assertTrue(result)
        self.assertEqual(button.click_count, 1)
        self.assertEqual(alert.sent_keys, ["1234"])

    def test_enable_no_ops_when_already_enabled(self):
        button = FakeButton("FLIP MODE ON — click to turn off")
        driver = _driver_with_button("flipModeBtn", button)
        result = tg.enable_flip_mode(driver, "1234")
        self.assertTrue(result)
        self.assertEqual(button.click_count, 0)

    def test_disable_no_ops_when_already_disabled(self):
        button = FakeButton("Enable Flip Mode")
        driver = _driver_with_button("flipModeBtn", button)
        result = tg.disable_flip_mode(driver, "1234")
        self.assertTrue(result)
        self.assertEqual(button.click_count, 0)

    def test_returns_false_and_never_clicks_when_the_label_is_unrecognized(self):
        # The dangerous case this guards against: if is_flip_mode_active
        # can't tell the current state, clicking anyway risks toggling
        # Flip Mode the *wrong* way -- refusing beats guessing.
        button = FakeButton("Flip Mode")
        driver = _driver_with_button("flipModeBtn", button)
        result = tg.enable_flip_mode(driver, "1234")
        self.assertFalse(result)
        self.assertEqual(button.click_count, 0)

    def test_returns_false_if_no_prompt_appears_after_clicking(self):
        button = FakeButton("Enable Flip Mode")
        driver = _driver_with_button("flipModeBtn", button, switch_to=RaisingSwitchTo())
        result = tg.enable_flip_mode(driver, "1234")
        self.assertFalse(result)

    def test_returns_false_if_the_label_never_flips_after_the_prompt(self):
        # The click and prompt both "succeed", but the button's label is
        # unchanged afterward -- something didn't take.
        button = FakeButton("Enable Flip Mode")
        alert = FakeAlert()
        driver = _driver_with_button("flipModeBtn", button, switch_to=AlertSwitchTo(alert))
        result = tg.enable_flip_mode(driver, "1234")
        self.assertFalse(result)


class IsSecondWithdrawalMarkedTests(unittest.TestCase):
    def test_reads_the_confirmed_marked_label_as_marked(self):
        driver = _driver_with_button(
            "secondWithdrawalBtn", FakeButton("✓ Second Withdrawal — click to cancel")
        )
        self.assertTrue(tg.is_second_withdrawal_marked(driver))

    def test_reads_the_confirmed_unmarked_label_as_not_marked(self):
        driver = _driver_with_button("secondWithdrawalBtn", FakeButton("🔄 Mark as Second Withdrawal"))
        self.assertFalse(tg.is_second_withdrawal_marked(driver))

    def test_an_unrecognized_label_returns_none(self):
        driver = _driver_with_button("secondWithdrawalBtn", FakeButton("Second Withdrawal"))
        self.assertIsNone(tg.is_second_withdrawal_marked(driver))

    def test_a_missing_button_returns_none(self):
        driver = MagicMock()
        driver.find_element.side_effect = Exception("not found")
        self.assertIsNone(tg.is_second_withdrawal_marked(driver))


class MarkSecondWithdrawalTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(tg, "humanize")
        self.addCleanup(patcher.stop)
        patcher.start()

    def test_clicks_and_sends_the_code_when_unmarked(self):
        button = FakeButton("Mark as Second Withdrawal")
        alert = FakeAlert()

        # After the click, is_second_withdrawal_marked must read the
        # button as marked for mark_second_withdrawal to succeed.
        def fake_find_element(by, selector):
            if selector == "secondWithdrawalBtn":
                button.text = (
                    "Second Withdrawal — click to cancel" if button.click_count
                    else "Mark as Second Withdrawal"
                )
                return button
            raise Exception("not found")

        driver = MagicMock()
        driver.find_element.side_effect = fake_find_element
        driver.switch_to = AlertSwitchTo(alert)

        result = tg.mark_second_withdrawal(driver, "1234")
        self.assertTrue(result)
        self.assertEqual(button.click_count, 1)
        self.assertEqual(alert.sent_keys, ["1234"])

    def test_no_ops_when_already_marked(self):
        button = FakeButton("Second Withdrawal — click to cancel")
        driver = _driver_with_button("secondWithdrawalBtn", button)
        result = tg.mark_second_withdrawal(driver, "1234")
        self.assertTrue(result)
        self.assertEqual(button.click_count, 0)

    def test_returns_false_and_never_clicks_when_the_label_is_unrecognized(self):
        # The exact bug seen live: clicking #secondWithdrawalBtn while
        # it's already marked cancels it right back off. Refusing to
        # click when the state can't be confirmed guards against that.
        button = FakeButton("Second Withdrawal")
        driver = _driver_with_button("secondWithdrawalBtn", button)
        result = tg.mark_second_withdrawal(driver, "1234")
        self.assertFalse(result)
        self.assertEqual(button.click_count, 0)

    def test_returns_false_if_no_prompt_appears_after_clicking(self):
        button = FakeButton("Mark as Second Withdrawal")
        driver = _driver_with_button("secondWithdrawalBtn", button, switch_to=RaisingSwitchTo())
        result = tg.mark_second_withdrawal(driver, "1234")
        self.assertFalse(result)

    def test_returns_false_if_the_label_never_flips_after_the_prompt(self):
        button = FakeButton("Mark as Second Withdrawal")
        alert = FakeAlert()
        driver = _driver_with_button("secondWithdrawalBtn", button, switch_to=AlertSwitchTo(alert))
        result = tg.mark_second_withdrawal(driver, "1234")
        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
