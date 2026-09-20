"""Covers trading.raise_bottom_panel -- dragging the bottom broker panel's
resize handle up toward the middle of the screen -- and the hook that runs it
whenever the panel is confirmed open. Run with:

    python -m unittest tests.test_bottom_panel_raise -v

Numbers below are hand-derived: viewport 800px -> target top edge 400px,
tolerance 25px (so a top edge at or above 425px counts as high enough).
"""

import unittest
from unittest.mock import MagicMock, patch

from tv_signal_trader import trading


class FakeDriver:
    """A page whose bottom panel has a top edge at `top` px. `queue` holds
    measurements to hand out first (an animation still settling) before
    falling back to the real `top`. A drag moves `top` up by the summed
    offsets, never past `min_top` (TradingView's clamp on the panel's
    height) -- and not at all if `drag_works` is False."""

    def __init__(self, top, viewport=800, min_top=0, drag_works=True, queue=None, measurable=True):
        self.top = top
        self.viewport = viewport
        self.min_top = min_top
        self.drag_works = drag_works
        self.queue = list(queue or [])
        self.measurable = measurable
        self.find_calls = []
        self.drags = []  # each drag's list of dy offsets

    def execute_script(self, script):
        if not self.measurable:
            return None
        top = self.queue.pop(0) if self.queue else self.top
        return {"viewport": self.viewport, "top": top, "height": self.viewport - top}

    def find_element(self, by, selector):
        self.find_calls.append(selector)
        return "handle"


class FakeActionChains:
    """Records the offsets of move_by_offset calls; on perform() applies the
    drag to the driver the way the real page would."""

    def __init__(self, driver):
        self.driver = driver
        self.offsets = []
        self.pressed = False
        self.released = False

    def move_to_element(self, el):
        return self

    def pause(self, seconds):
        return self

    def click_and_hold(self):
        self.pressed = True
        return self

    def move_by_offset(self, dx, dy):
        assert dx == 0
        self.offsets.append(dy)
        return self

    def release(self):
        self.released = True
        return self

    def perform(self):
        assert self.pressed and self.released
        self.driver.drags.append(list(self.offsets))
        if self.driver.drag_works:
            self.driver.top = max(self.driver.min_top, self.driver.top + sum(self.offsets))


class _NoWaiting(unittest.TestCase):
    def setUp(self):
        for target in (patch.object(trading, "humanize"),
                       patch.object(trading, "ActionChains", FakeActionChains)):
            target.start()
            self.addCleanup(target.stop)
        trading._panel_raise_attempts.clear()


class RaiseBottomPanelTests(_NoWaiting):
    def test_a_panel_already_above_the_middle_is_left_alone(self):
        driver = FakeDriver(top=300)
        self.assertEqual(trading.raise_bottom_panel(driver), "already_high")
        self.assertEqual(driver.drags, [])
        self.assertEqual(driver.find_calls, [])

    def test_a_panel_within_the_tolerance_of_the_middle_is_left_alone(self):
        driver = FakeDriver(top=425)  # 25px under the 400 target -- exactly the tolerance
        self.assertEqual(trading.raise_bottom_panel(driver), "already_high")
        self.assertEqual(driver.drags, [])

    def test_a_panel_just_past_the_tolerance_is_dragged(self):
        driver = FakeDriver(top=426)
        self.assertEqual(trading.raise_bottom_panel(driver), "raised")
        self.assertEqual(driver.top, 400)

    def test_a_low_panel_is_dragged_up_to_the_middle(self):
        driver = FakeDriver(top=600)  # 200px too low
        self.assertEqual(trading.raise_bottom_panel(driver), "raised")
        self.assertEqual(driver.top, 400)
        self.assertEqual(driver.find_calls, [trading._PANEL_HANDLE_SELECTOR])

    def test_the_drag_is_many_small_upward_steps_summing_to_the_distance(self):
        driver = FakeDriver(top=600)
        trading.raise_bottom_panel(driver)
        (offsets,) = driver.drags
        self.assertEqual(len(offsets), trading._PANEL_DRAG_STEPS)
        self.assertTrue(all(dy <= 0 for dy in offsets))  # upward only
        self.assertEqual(sum(offsets), -200)
        # 200 = 12*16 + 8: eleven steps of 16, the last carrying the remainder
        self.assertEqual(offsets[:-1], [-16] * 11)
        self.assertEqual(offsets[-1], -24)

    def test_a_distance_smaller_than_the_step_count_still_sums_exactly(self):
        driver = FakeDriver(top=466, viewport=880)  # target 440 -> 26px: 12*2 + a remainder of 2
        trading.raise_bottom_panel(driver)
        (offsets,) = driver.drags
        self.assertEqual(sum(offsets), -26)
        self.assertEqual(len(offsets), trading._PANEL_DRAG_STEPS)

    def test_it_reports_partial_when_the_page_limits_how_tall_the_panel_may_get(self):
        driver = FakeDriver(top=600, min_top=500)  # clamped at 500, target was 400
        self.assertEqual(trading.raise_bottom_panel(driver), "partial")
        self.assertEqual(driver.top, 500)

    def test_a_clamp_just_inside_the_tolerance_counts_as_raised(self):
        driver = FakeDriver(top=600, min_top=420)  # 420 <= 400 + 25
        self.assertEqual(trading.raise_bottom_panel(driver), "raised")

    def test_it_reports_failed_when_the_panel_does_not_move(self):
        driver = FakeDriver(top=600, drag_works=False)
        self.assertEqual(trading.raise_bottom_panel(driver), "failed")

    def test_a_move_smaller_than_the_minimum_counts_as_no_effect(self):
        driver = FakeDriver(top=600, min_top=597)  # moved 3px < 5
        self.assertEqual(trading.raise_bottom_panel(driver), "failed")

    def test_a_drag_that_raises_is_reported_as_failed_not_propagated(self):
        driver = FakeDriver(top=600)
        with patch.object(trading, "ActionChains", side_effect=RuntimeError("boom")), \
             patch.object(trading, "print") as printed:
            self.assertEqual(trading.raise_bottom_panel(driver), "failed")
        self.assertTrue(any("Could not drag" in str(c) for c in printed.call_args_list))

    def test_a_missing_handle_is_reported_as_failed(self):
        driver = FakeDriver(top=600)
        driver.find_element = MagicMock(side_effect=Exception("no such element"))
        self.assertEqual(trading.raise_bottom_panel(driver), "failed")

    def test_an_unmeasurable_panel_is_unavailable_and_nothing_is_attempted(self):
        driver = FakeDriver(top=600, measurable=False)
        self.assertEqual(trading.raise_bottom_panel(driver), "unavailable")
        self.assertEqual(driver.drags, [])
        self.assertEqual(driver.find_calls, [])

    def test_a_script_error_while_measuring_is_unavailable(self):
        driver = FakeDriver(top=600)
        driver.execute_script = MagicMock(side_effect=Exception("javascript error"))
        self.assertEqual(trading.raise_bottom_panel(driver), "unavailable")

    def test_a_non_dict_measurement_is_unavailable(self):
        # Test doubles (a bare MagicMock driver) return a MagicMock here.
        driver = MagicMock()
        self.assertEqual(trading.raise_bottom_panel(driver), "unavailable")
        driver.find_element.assert_not_called()

    def test_a_measurement_missing_keys_is_unavailable(self):
        driver = MagicMock()
        driver.execute_script.return_value = {"viewport": 800, "top": 600}
        self.assertEqual(trading.raise_bottom_panel(driver), "unavailable")


class SettlingTests(_NoWaiting):
    def test_the_distance_is_worked_out_after_an_opening_animation_has_settled(self):
        # Still sliding up when first measured: 700 -> 650 -> 600 -> 600.
        driver = FakeDriver(top=600, queue=[700, 650, 600, 600])
        self.assertEqual(trading.raise_bottom_panel(driver), "raised")
        (offsets,) = driver.drags
        self.assertEqual(sum(offsets), -200)  # from the settled 600, not the first 700

    def test_it_stops_waiting_after_a_bounded_number_of_polls(self):
        # Never settles (drifts 10px every poll): must still act, not hang.
        driver = FakeDriver(top=500, queue=[900 - 10 * i for i in range(20)])
        trading.raise_bottom_panel(driver)  # returns rather than looping forever
        # 6 settle polls + 1 wait for the layout after the drag
        self.assertEqual(trading.humanize.pause.call_count, 7)

    def test_a_panel_that_settles_already_high_is_left_alone(self):
        driver = FakeDriver(top=380, queue=[700, 500, 380, 380])
        self.assertEqual(trading.raise_bottom_panel(driver), "already_high")
        self.assertEqual(driver.drags, [])

    def test_a_panel_that_disappears_while_settling_is_unavailable(self):
        driver = FakeDriver(top=600)
        measurements = iter([
            {"viewport": 800, "top": 700, "height": 100},  # first measurement: low
            None,                                          # gone while settling
        ])
        driver.execute_script = lambda script: next(measurements)
        self.assertEqual(trading.raise_bottom_panel(driver), "unavailable")
        self.assertEqual(driver.drags, [])

    def test_an_already_high_panel_never_waits(self):
        driver = FakeDriver(top=300)
        trading.raise_bottom_panel(driver)
        trading.humanize.pause.assert_not_called()


class MaybeRaiseBottomPanelTests(_NoWaiting):
    def test_it_stops_dragging_after_the_attempt_cap_for_a_panel_that_will_not_move(self):
        driver = FakeDriver(top=600, drag_works=False)
        for _ in range(trading._PANEL_MAX_RAISE_ATTEMPTS + 3):
            trading._maybe_raise_bottom_panel(driver)
        self.assertEqual(len(driver.drags), trading._PANEL_MAX_RAISE_ATTEMPTS)

    def test_an_already_high_panel_never_uses_up_attempts(self):
        driver = FakeDriver(top=300)
        for _ in range(10):
            trading._maybe_raise_bottom_panel(driver)
        # Now it slips low (e.g. the layout was reset): still gets fixed.
        driver.top = 600
        trading._maybe_raise_bottom_panel(driver)
        self.assertEqual(driver.top, 400)

    def test_an_unavailable_panel_never_uses_up_attempts(self):
        driver = FakeDriver(top=600, measurable=False)
        for _ in range(10):
            trading._maybe_raise_bottom_panel(driver)
        driver.measurable = True
        trading._maybe_raise_bottom_panel(driver)
        self.assertEqual(driver.top, 400)

    def test_a_successful_raise_counts_but_a_later_slip_is_still_fixed_within_the_cap(self):
        driver = FakeDriver(top=600)
        trading._maybe_raise_bottom_panel(driver)
        self.assertEqual(driver.top, 400)
        driver.top = 650
        trading._maybe_raise_bottom_panel(driver)
        self.assertEqual(driver.top, 400)
        self.assertEqual(len(driver.drags), 2)

    def test_the_cap_is_per_browser_session(self):
        first, second = FakeDriver(top=600, drag_works=False), FakeDriver(top=600, drag_works=False)
        for _ in range(trading._PANEL_MAX_RAISE_ATTEMPTS + 2):
            trading._maybe_raise_bottom_panel(first)
        trading._maybe_raise_bottom_panel(second)
        self.assertEqual(len(second.drags), 1)  # a new session starts fresh

    def test_it_never_raises(self):
        driver = FakeDriver(top=600)
        with patch.object(trading, "raise_bottom_panel", side_effect=RuntimeError("boom")), \
             patch.object(trading, "print") as printed:
            trading._maybe_raise_bottom_panel(driver)  # must not propagate
        self.assertTrue(any("Could not adjust" in str(c) for c in printed.call_args_list))


class EnsureBrokerPanelOpenHookTests(_NoWaiting):
    def _driver(self, labels):
        """A driver whose toggle button reports each label in turn."""
        driver = MagicMock()
        toggles = []
        for label in labels:
            toggle = MagicMock()
            toggle.get_attribute.return_value = label
            toggles.append(toggle)
        driver.find_element.side_effect = toggles
        return driver

    def test_an_open_panel_is_raised(self):
        with patch.object(trading, "_maybe_raise_bottom_panel") as raise_it:
            driver = self._driver(["Collapse panel", "Collapse panel"])
            self.assertTrue(trading._ensure_broker_panel_open(driver))
        raise_it.assert_called_once_with(driver)

    def test_a_panel_that_had_to_be_opened_is_raised_after_opening(self):
        events = []
        with patch.object(trading, "_maybe_raise_bottom_panel",
                          side_effect=lambda d: events.append("raise")):
            driver = self._driver(["Open panel", "Collapse panel"])
            driver.execute_script.side_effect = lambda *a: events.append("click toggle")
            self.assertTrue(trading._ensure_broker_panel_open(driver))
        self.assertEqual(events, ["click toggle", "raise"])

    def test_a_panel_that_would_not_open_is_not_raised(self):
        with patch.object(trading, "_maybe_raise_bottom_panel") as raise_it:
            driver = self._driver(["Open panel", "Open panel"])
            self.assertFalse(trading._ensure_broker_panel_open(driver))
        raise_it.assert_not_called()

    def test_a_missing_toggle_is_not_raised(self):
        with patch.object(trading, "_maybe_raise_bottom_panel") as raise_it:
            driver = MagicMock()
            driver.find_element.side_effect = Exception("not found")
            self.assertFalse(trading._ensure_broker_panel_open(driver))
        raise_it.assert_not_called()

    def test_a_raise_that_blows_up_never_stops_the_panel_from_counting_as_open(self):
        with patch.object(trading, "raise_bottom_panel", side_effect=RuntimeError("boom")), \
             patch.object(trading, "print"):
            driver = self._driver(["Collapse panel", "Collapse panel"])
            self.assertTrue(trading._ensure_broker_panel_open(driver))


if __name__ == "__main__":
    unittest.main()
