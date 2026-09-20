"""Covers tv_signal_trader/ads.py -- detecting/dismissing TradingView's
ad and upsell popups (see the module's own docstring for the two variants
caught in production and why both the persistent watchdog script and the
on-demand dismiss_ads() share one selector list). There's no JS engine
here to actually execute WATCHDOG_SCRIPT/_DISMISS_ONCE_JS against, so this
covers what's verifiable from Python: the selector list itself, that both
scripts are built from it (so they can't silently drift apart), and
dismiss_ads()'s own return-value/never-raises contract. Run with:

    python -m unittest tests.test_ads -v
"""

import json
import unittest
from unittest.mock import MagicMock

from tv_signal_trader import ads


class AdCloseSelectorsTests(unittest.TestCase):
    def test_covers_the_ad_toast_groups_close_all_button(self):
        self.assertIn('[data-name^="toast-group-close-button-"]', ads.AD_CLOSE_SELECTORS)

    def test_covers_the_ad_blocker_promo_dialogs_close_button(self):
        self.assertIn('[data-qa-id="promo-dialog-close-button"]', ads.AD_CLOSE_SELECTORS)


class ScriptsBuiltFromTheSameSelectorListTests(unittest.TestCase):
    """Both scripts are generated from AD_CLOSE_SELECTORS via the same
    _SELECTORS_JSON -- this just confirms every selector actually made it
    into both, so a future addition to the list can't silently end up in
    only one of the two mechanisms."""

    def test_watchdog_script_embeds_every_selector(self):
        # The selectors are JSON-encoded into the script (so a selector
        # containing a literal '"' round-trips correctly as a JS string),
        # which escapes those quotes -- decode the embedded array back
        # out and compare it directly rather than substring-matching
        # against the raw (unescaped) selector text.
        self.assertIn(ads._SELECTORS_JSON, ads.WATCHDOG_SCRIPT)
        self.assertEqual(json.loads(ads._SELECTORS_JSON), ads.AD_CLOSE_SELECTORS)

    def test_dismiss_once_script_embeds_every_selector(self):
        self.assertIn(ads._SELECTORS_JSON, ads._DISMISS_ONCE_JS)

    def test_watchdog_script_sets_up_a_mutation_observer(self):
        self.assertIn("MutationObserver", ads.WATCHDOG_SCRIPT)

    def test_watchdog_script_defers_until_body_exists(self):
        # addScriptToEvaluateOnNewDocument runs before <body> exists --
        # this guards the real bug that would cause (observing a null
        # node throws), not just a stylistic preference.
        self.assertIn("DOMContentLoaded", ads.WATCHDOG_SCRIPT)
        self.assertIn("document.body", ads.WATCHDOG_SCRIPT)

    def test_dismiss_once_script_returns_a_value(self):
        # Selenium's execute_script only returns a value with an explicit
        # top-level `return` -- easy to lose track of when the script body
        # is itself an IIFE.
        self.assertIn("return (function()", ads._DISMISS_ONCE_JS.strip())


class DismissAdsTests(unittest.TestCase):
    def test_returns_the_count_execute_script_reports(self):
        driver = MagicMock()
        driver.execute_script.return_value = 2
        self.assertEqual(ads.dismiss_ads(driver), 2)

    def test_returns_zero_when_nothing_was_closed(self):
        driver = MagicMock()
        driver.execute_script.return_value = 0
        self.assertEqual(ads.dismiss_ads(driver), 0)

    def test_treats_a_none_result_as_zero(self):
        driver = MagicMock()
        driver.execute_script.return_value = None
        self.assertEqual(ads.dismiss_ads(driver), 0)

    def test_never_raises_if_execute_script_fails(self):
        driver = MagicMock()
        driver.execute_script.side_effect = Exception("boom")
        self.assertEqual(ads.dismiss_ads(driver), 0)

    def test_runs_the_dismiss_once_script_against_the_given_driver(self):
        driver = MagicMock()
        driver.execute_script.return_value = 0
        ads.dismiss_ads(driver)
        driver.execute_script.assert_called_once_with(ads._DISMISS_ONCE_JS)


if __name__ == "__main__":
    unittest.main()
