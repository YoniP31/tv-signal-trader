"""Covers browser.create_driver()'s retry against SessionNotCreatedException
("session not created: Chrome instance exited") -- seen live across a fleet
deploy: chromedriver gives up its handshake with a freshly-launched Chrome
under momentary load, leaving that Chrome orphaned and holding the profile's
lock file, so a blind retry (or the Scheduled Task's own restart) just fails
the same way again indefinitely. Doesn't launch a real browser --
webdriver.Chrome itself is mocked out. Run with:

    python -m unittest tests.test_create_driver_retry -v
"""

import unittest
from unittest.mock import MagicMock, call, patch

from selenium.common.exceptions import SessionNotCreatedException, WebDriverException

from tv_signal_trader import browser


def _crash(message="session not created: Chrome instance exited"):
    return SessionNotCreatedException(message)


class CreateDriverRetryTests(unittest.TestCase):
    def setUp(self):
        for target in (patch.object(browser, "force_kill"), patch.object(browser.time, "sleep")):
            target.start()
            self.addCleanup(target.stop)

    def test_a_clean_first_launch_never_retries_or_force_kills(self):
        fake_driver = MagicMock()
        with patch.object(browser.webdriver, "Chrome", return_value=fake_driver):
            result = browser.create_driver()
        self.assertIs(result, fake_driver)
        browser.force_kill.assert_not_called()
        browser.time.sleep.assert_not_called()

    def test_a_crash_then_success_is_recovered_transparently(self):
        fake_driver = MagicMock()
        with patch.object(browser.webdriver, "Chrome", side_effect=[_crash(), fake_driver]) as chrome:
            result = browser.create_driver()
        self.assertIs(result, fake_driver)
        self.assertEqual(chrome.call_count, 2)
        browser.force_kill.assert_called_once_with()
        browser.time.sleep.assert_called_once_with(browser._CREATE_DRIVER_RETRY_PAUSE_SECONDS)

    def test_the_orphan_is_cleared_before_the_pause_and_before_retrying(self):
        events = []
        browser.force_kill.side_effect = lambda: events.append("force_kill")
        browser.time.sleep.side_effect = lambda s: events.append("sleep")
        fake_driver = MagicMock()

        def launch(*a, **kw):
            events.append("launch")
            if len(events) == 1:
                raise _crash()
            return fake_driver

        with patch.object(browser.webdriver, "Chrome", side_effect=launch):
            browser.create_driver()
        self.assertEqual(events, ["launch", "force_kill", "sleep", "launch"])

    def test_it_gives_up_after_the_configured_number_of_attempts(self):
        crashes = [_crash(f"attempt {i}") for i in range(browser._CREATE_DRIVER_MAX_ATTEMPTS)]
        with patch.object(browser.webdriver, "Chrome", side_effect=crashes) as chrome:
            with self.assertRaises(SessionNotCreatedException):
                browser.create_driver()
        self.assertEqual(chrome.call_count, browser._CREATE_DRIVER_MAX_ATTEMPTS)

    def test_every_failed_attempt_force_kills_including_the_last(self):
        crashes = [_crash()] * browser._CREATE_DRIVER_MAX_ATTEMPTS
        with patch.object(browser.webdriver, "Chrome", side_effect=crashes):
            with self.assertRaises(SessionNotCreatedException):
                browser.create_driver()
        self.assertEqual(browser.force_kill.call_count, browser._CREATE_DRIVER_MAX_ATTEMPTS)

    def test_there_is_no_pause_after_the_final_failed_attempt(self):
        # Nothing left to wait for once it's about to give up.
        crashes = [_crash()] * browser._CREATE_DRIVER_MAX_ATTEMPTS
        with patch.object(browser.webdriver, "Chrome", side_effect=crashes):
            with self.assertRaises(SessionNotCreatedException):
                browser.create_driver()
        self.assertEqual(browser.time.sleep.call_count, browser._CREATE_DRIVER_MAX_ATTEMPTS - 1)

    def test_the_exception_raised_after_giving_up_is_the_last_one_seen(self):
        crashes = [_crash("first"), _crash("second"), _crash("third and final")]
        with patch.object(browser.webdriver, "Chrome", side_effect=crashes):
            with self.assertRaises(SessionNotCreatedException) as ctx:
                browser.create_driver()
        self.assertIn("third and final", str(ctx.exception))

    def test_a_different_exception_is_not_retried_or_force_killed(self):
        # This retry is deliberately narrow -- a real config/options problem
        # must surface immediately, not be masked by 3 identical retries.
        with patch.object(browser.webdriver, "Chrome", side_effect=WebDriverException("boom")) as chrome:
            with self.assertRaises(WebDriverException):
                browser.create_driver()
        self.assertEqual(chrome.call_count, 1)
        browser.force_kill.assert_not_called()

    def test_the_driver_returned_after_a_retry_still_gets_the_ad_watchdog_injected(self):
        fake_driver = MagicMock()
        with patch.object(browser.webdriver, "Chrome", side_effect=[_crash(), fake_driver]):
            browser.create_driver()
        injected_sources = [
            c.args[1]["source"] for c in fake_driver.execute_cdp_cmd.call_args_list
            if c.args[0] == "Page.addScriptToEvaluateOnNewDocument"
        ]
        from tv_signal_trader import ads
        self.assertIn(ads.WATCHDOG_SCRIPT, injected_sources)


if __name__ == "__main__":
    unittest.main()
