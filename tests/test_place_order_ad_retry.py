"""Covers trading.place_order's retry-after-dismissing-an-ad wrapper
around _place_order_once -- see ads.py for why this is only the reactive
fallback (the persistent watchdog script normally beats a click to the
punch). _place_order_once itself is mocked out entirely here: it's the
big multi-step DOM sequence, untouched by this change, and not what this
covers -- only the orchestration around it (catch, dismiss, retry once,
give up cleanly) is new. Run with:

    python -m unittest tests.test_place_order_ad_retry -v
"""

import unittest
from unittest.mock import MagicMock, patch

from selenium.common.exceptions import ElementClickInterceptedException

from tv_signal_trader import trading


class PlaceOrderAdRetryTests(unittest.TestCase):
    def setUp(self):
        self.driver = MagicMock()

    def test_succeeds_on_the_first_try_without_touching_ads_at_all(self):
        with patch.object(trading, "_place_order_once", return_value=True) as once_mock, \
             patch.object(trading.ads, "dismiss_ads") as dismiss_mock:
            result = trading.place_order(self.driver, side="buy")
        self.assertTrue(result)
        once_mock.assert_called_once_with(self.driver, 150, 150, "buy", 1)
        dismiss_mock.assert_not_called()

    def test_a_failure_that_is_not_a_click_interception_is_not_retried(self):
        # place_order's own steps already return False on every other
        # kind of failure (element not found, field verification failed,
        # etc.) -- only an actual intercepted click should trigger the
        # ad-dismiss-and-retry path.
        with patch.object(trading, "_place_order_once", return_value=False) as once_mock, \
             patch.object(trading.ads, "dismiss_ads") as dismiss_mock:
            result = trading.place_order(self.driver, side="sell")
        self.assertFalse(result)
        once_mock.assert_called_once()
        dismiss_mock.assert_not_called()

    def test_dismisses_and_retries_once_after_an_intercepted_click(self):
        with patch.object(
            trading, "_place_order_once", side_effect=[ElementClickInterceptedException(), True]
        ) as once_mock, \
             patch.object(trading.ads, "dismiss_ads", return_value=1) as dismiss_mock:
            result = trading.place_order(self.driver, side="buy")
        self.assertTrue(result)
        self.assertEqual(once_mock.call_count, 2)
        dismiss_mock.assert_called_once_with(self.driver)

    def test_gives_up_after_a_second_intercepted_click(self):
        with patch.object(
            trading, "_place_order_once",
            side_effect=[ElementClickInterceptedException(), ElementClickInterceptedException()],
        ) as once_mock, \
             patch.object(trading.ads, "dismiss_ads", return_value=1) as dismiss_mock:
            result = trading.place_order(self.driver, side="buy")
        self.assertFalse(result)
        self.assertEqual(once_mock.call_count, 2)
        # Only dismissed once, after the first interception -- no point
        # dismissing again right before giving up on the second.
        self.assertEqual(dismiss_mock.call_count, 1)

    def test_still_retries_once_even_if_dismiss_ads_finds_nothing(self):
        # The interception might clear on its own a moment later even if
        # nothing matched AD_CLOSE_SELECTORS -- worth one retry either way
        # rather than giving up immediately.
        with patch.object(
            trading, "_place_order_once", side_effect=[ElementClickInterceptedException(), True]
        ) as once_mock, \
             patch.object(trading.ads, "dismiss_ads", return_value=0):
            result = trading.place_order(self.driver, side="buy")
        self.assertTrue(result)
        self.assertEqual(once_mock.call_count, 2)

    def test_passes_through_all_arguments_to_place_order_once(self):
        with patch.object(trading, "_place_order_once", return_value=True) as once_mock:
            trading.place_order(self.driver, tp_ticks=200, sl_ticks=100, side="sell", units=3)
        once_mock.assert_called_once_with(self.driver, 200, 100, "sell", 3)


if __name__ == "__main__":
    unittest.main()
