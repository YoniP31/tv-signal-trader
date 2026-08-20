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

from tv_signal_trader import browser


class BuildOptionsBackgroundThrottlingTests(unittest.TestCase):
    def test_disables_background_timer_throttling(self):
        self.assertIn("--disable-background-timer-throttling", browser.build_options().arguments)

    def test_disables_backgrounding_occluded_windows(self):
        self.assertIn("--disable-backgrounding-occluded-windows", browser.build_options().arguments)

    def test_disables_renderer_backgrounding(self):
        self.assertIn("--disable-renderer-backgrounding", browser.build_options().arguments)


if __name__ == "__main__":
    unittest.main()
