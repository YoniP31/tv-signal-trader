"""Covers the Live/Demo toggle handling in trading.connect_tradovate --
_find_demo_option/_ensure_demo_selected, and that connect_tradovate always
makes sure 'Demo' is selected *before* clicking Connect (and never clicks
Connect at all if it can't). The toggle is a role="radiogroup" of
role="radio" buttons whose aria-checked says which is selected, inside a
dialog labelled "Broker login dialog". Run with:

    python -m unittest tests.test_connect_tradovate_demo -v
"""

import unittest
from unittest.mock import MagicMock, patch

from tv_signal_trader import trading


class FakeRadio:
    def __init__(self, text, checked=False, click_takes=True, on_click=None):
        self.text = text
        self.checked = checked
        self._click_takes = click_takes
        self._on_click = on_click

    def get_attribute(self, name):
        if name == "aria-checked":
            return "true" if self.checked else "false"
        return None

    def click(self):
        if self._on_click:
            self._on_click()
        elif self._click_takes:
            self.checked = True


class FakeDialog:
    def __init__(self, options):
        self.options = options

    def find_elements(self, by, selector):
        return list(self.options)


def _driver_with_dialog(dialog):
    driver = MagicMock()

    def fake_find_element(by, selector):
        if selector == '[aria-label="Broker login dialog"]' and dialog is not None:
            return dialog
        raise Exception("not found")

    driver.find_element.side_effect = fake_find_element
    driver.execute_script.side_effect = lambda script, el: el.click()
    return driver


class _NoWaiting(unittest.TestCase):
    """Nothing here should ever really sleep."""

    def setUp(self):
        for target in (patch.object(trading.time, "sleep"), patch.object(trading, "humanize")):
            target.start()
            self.addCleanup(target.stop)


class EnsureDemoSelectedTests(_NoWaiting):
    def test_leaves_demo_alone_when_it_is_already_selected(self):
        live, demo = FakeRadio("Live"), FakeRadio("Demo", checked=True)
        driver = _driver_with_dialog(FakeDialog([live, demo]))
        self.assertTrue(trading._ensure_demo_selected(driver))
        driver.execute_script.assert_not_called()

    def test_switches_from_live_to_demo(self):
        live, demo = FakeRadio("Live", checked=True), FakeRadio("Demo")
        driver = _driver_with_dialog(FakeDialog([live, demo]))
        self.assertTrue(trading._ensure_demo_selected(driver))
        self.assertTrue(demo.checked)
        driver.execute_script.assert_called_once()

    def test_the_option_text_is_matched_ignoring_case_and_whitespace(self):
        demo = FakeRadio("  DEMO ")
        driver = _driver_with_dialog(FakeDialog([FakeRadio("Live", checked=True), demo]))
        self.assertTrue(trading._ensure_demo_selected(driver))
        self.assertTrue(demo.checked)

    def test_fails_if_the_click_does_not_take(self):
        # Never trust the click -- read the state back. A Demo that still
        # isn't selected afterward means Connect would log into Live.
        demo = FakeRadio("Demo", click_takes=False)
        driver = _driver_with_dialog(FakeDialog([FakeRadio("Live", checked=True), demo]))
        self.assertFalse(trading._ensure_demo_selected(driver))

    def test_copes_with_the_control_re_rendering_after_the_click(self):
        # The selection re-renders the segmented control, so the element
        # clicked may no longer be the one in the DOM -- the check must
        # re-find it, not reuse the stale reference.
        dialog = FakeDialog([])
        old_demo = FakeRadio("Demo", on_click=lambda: setattr(
            dialog, "options", [FakeRadio("Live"), FakeRadio("Demo", checked=True)]))
        dialog.options = [FakeRadio("Live", checked=True), old_demo]
        driver = _driver_with_dialog(dialog)
        self.assertTrue(trading._ensure_demo_selected(driver))
        self.assertFalse(old_demo.checked)  # the stale one never flipped; the fresh one did

    def test_fails_if_there_is_no_demo_option(self):
        driver = _driver_with_dialog(FakeDialog([FakeRadio("Live", checked=True)]))
        self.assertFalse(trading._ensure_demo_selected(driver))

    def test_fails_if_the_dialog_never_appears(self):
        driver = _driver_with_dialog(None)
        self.assertFalse(trading._ensure_demo_selected(driver))


class ConnectTradovateDemoOrderingTests(_NoWaiting):
    """connect_tradovate must ensure Demo *before* Connect, and must never
    click Connect if Demo can't be confirmed."""

    def _run(self, demo_ok):
        events = []
        connect_button = MagicMock()
        connect_button.is_displayed.return_value = True
        connect_button.text = "Connect"

        driver = MagicMock()
        driver.find_element.return_value = "tradovate-tile"
        driver.find_elements.return_value = [connect_button]
        driver.execute_script.side_effect = lambda script, el: events.append(
            "click Connect" if el is connect_button else f"click {el}")

        def fake_ensure(d):
            events.append("ensure Demo")
            return demo_ok

        with patch.object(trading, "is_tradovate_connected", return_value=False), \
             patch.object(trading, "_find_trade_button", return_value="trade-button"), \
             patch.object(trading, "_ensure_demo_selected", side_effect=fake_ensure), \
             patch.object(trading, "_find_tradovate_login_window", return_value=None):
            result = trading.connect_tradovate(driver, "user", "pass")
        return result, events

    def test_demo_is_ensured_after_the_tile_and_before_connect(self):
        _result, events = self._run(demo_ok=True)
        self.assertLess(events.index("click tradovate-tile"), events.index("ensure Demo"))
        self.assertLess(events.index("ensure Demo"), events.index("click Connect"))

    def test_connect_is_never_clicked_when_demo_cannot_be_confirmed(self):
        result, events = self._run(demo_ok=False)
        self.assertFalse(result)
        self.assertIn("ensure Demo", events)
        self.assertNotIn("click Connect", events)


if __name__ == "__main__":
    unittest.main()
