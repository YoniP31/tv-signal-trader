"""Covers browser.build_options() -- specifically the background-throttling
flags added to keep TradingGenerator's hidden window (and, on an
unattended VPS, often TradingView's own tab too) fully responsive even
after many idle hours, instead of Chrome suspending its renderer and
causing the next real interaction to hang until it hits chromedriver's own
script-execution timeout (seen as a bare "script timeout" TimeoutException
right after the daily session-start sweep, on more than one machine). Run
with:

    python -m unittest tests.test_browser_options -v
"""

import unittest
from unittest.mock import MagicMock, patch

from tv_signal_trader import ads
from tv_signal_trader import browser


class BuildOptionsBackgroundThrottlingTests(unittest.TestCase):
    def test_disables_background_timer_throttling(self):
        self.assertIn("--disable-background-timer-throttling", browser.build_options().arguments)

    def test_disables_backgrounding_occluded_windows(self):
        self.assertIn("--disable-backgrounding-occluded-windows", browser.build_options().arguments)

    def test_disables_renderer_backgrounding(self):
        self.assertIn("--disable-renderer-backgrounding", browser.build_options().arguments)


class CreateDriverInjectsTheAdWatchdogTests(unittest.TestCase):
    """Covers create_driver() wiring ads.WATCHDOG_SCRIPT in via
    Page.addScriptToEvaluateOnNewDocument -- see ads.py's own docstring
    for why this (not a Selenium-side poll) is the primary defense
    against TradingView's ad/upsell popups. Doesn't launch a real
    browser -- webdriver.Chrome itself is mocked out."""

    def test_injects_the_watchdog_script_alongside_the_existing_spoofing_script(self):
        fake_driver = MagicMock()
        with patch.object(browser.webdriver, "Chrome", return_value=fake_driver):
            result = browser.create_driver()
        self.assertIs(result, fake_driver)
        injected_sources = [
            call.args[1]["source"] for call in fake_driver.execute_cdp_cmd.call_args_list
            if call.args[0] == "Page.addScriptToEvaluateOnNewDocument"
        ]
        self.assertIn(ads.WATCHDOG_SCRIPT, injected_sources)
        # The pre-existing webdriver-detection spoofing must still be
        # injected too -- this is additive, not a replacement.
        self.assertTrue(any("navigator" in src and "webdriver" in src for src in injected_sources))


if __name__ == "__main__":
    unittest.main()
