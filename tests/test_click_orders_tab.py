"""Covers trading.click_orders_tab's retry behavior. Run with:

    python -m unittest tests.test_click_orders_tab -v

Real bug this guards against: right after an order is placed, Tradovate's
broker panel briefly re-renders (the new position/working orders
appearing), during which the '#orders' tab element can transiently not be
found even though the panel is genuinely open. The original implementation
looked it up exactly once with no retry, which lost that race consistently
on a slower/higher-latency machine (a VPS) while never failing on a faster
one -- confirmed live via the same code, same page, different hardware.
"""

import unittest
from unittest.mock import MagicMock, patch

from tv_signal_trader import trading


def _driver_with_orders_tab(fail_times, panel_already_open=True):
    """A MagicMock driver whose '#orders' lookup raises `fail_times` times
    before succeeding. The broker-panel toggle is always found and already
    reports open/closed per `panel_already_open`."""
    driver = MagicMock()
    toggle = MagicMock()
    toggle.get_attribute.return_value = "Collapse panel" if panel_already_open else "Open panel"
    state = {"orders_calls": 0}

    def fake_find_element(by, value):
        if value == '[data-name="toggle-visibility-button"]':
            return toggle
        if value == "orders":
            state["orders_calls"] += 1
            if state["orders_calls"] <= fail_times:
                raise Exception("not found yet")
            return MagicMock()
        raise Exception(f"unexpected selector: {value}")

    driver.find_element.side_effect = fake_find_element
    return driver, state


class ClickOrdersTabTests(unittest.TestCase):
    def test_succeeds_immediately_when_orders_tab_is_already_there(self):
        driver, state = _driver_with_orders_tab(fail_times=0)
        with patch.object(trading.humanize, "pause"):
            result = trading.click_orders_tab(driver)
        self.assertTrue(result)
        self.assertEqual(state["orders_calls"], 1)

    def test_retries_past_a_transient_miss_and_succeeds(self):
        driver, state = _driver_with_orders_tab(fail_times=2)
        with patch.object(trading.humanize, "pause"):
            result = trading.click_orders_tab(driver, attempts=5)
        self.assertTrue(result)
        self.assertEqual(state["orders_calls"], 3)

    def test_gives_up_after_exhausting_attempts(self):
        driver, state = _driver_with_orders_tab(fail_times=999)
        with patch.object(trading.humanize, "pause"):
            result = trading.click_orders_tab(driver, attempts=3)
        self.assertFalse(result)
        self.assertEqual(state["orders_calls"], 3)

    def test_returns_false_immediately_if_the_panel_toggle_itself_is_missing(self):
        driver = MagicMock()
        driver.find_element.side_effect = Exception("no toggle button")
        with patch.object(trading.humanize, "pause"):
            result = trading.click_orders_tab(driver, attempts=5)
        self.assertFalse(result)
        # Never even got to looking for '#orders'.
        driver.find_element.assert_called_once()


if __name__ == "__main__":
    unittest.main()
