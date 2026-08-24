"""Covers tv_signal_trader/history.py -- the pure day-consistency/day-count
math Flip Mode's evaluation will be built on, derived entirely from plain
{date, equity} fixture data (no status.json/DOM involved). Run with:

    python -m unittest tests.test_history -v
"""

import unittest

from tv_signal_trader import history


class DailyProfitsTests(unittest.TestCase):
    def test_empty_history_gives_no_profits(self):
        self.assertEqual(history.daily_profits([], 50000), [])

    def test_first_day_profit_is_relative_to_starting_balance(self):
        days = [{"date": "2026-08-01", "equity": 51000}]
        self.assertEqual(history.daily_profits(days, 50000), [("2026-08-01", 1000)])

    def test_subsequent_days_are_relative_to_the_previous_day(self):
        days = [
            {"date": "2026-08-01", "equity": 51000},
            {"date": "2026-08-02", "equity": 50500},
            {"date": "2026-08-03", "equity": 53000},
        ]
        self.assertEqual(
            history.daily_profits(days, 50000),
            [("2026-08-01", 1000), ("2026-08-02", -500), ("2026-08-03", 2500)],
        )

    def test_since_date_resets_the_baseline_at_the_first_included_day(self):
        days = [
            {"date": "2026-08-01", "equity": 51000},
            {"date": "2026-08-02", "equity": 50500},
            {"date": "2026-08-03", "equity": 53000},
        ]
        # Days 1-2 are filtered out, making day 3 the first included day --
        # its delta resets against starting_balance (50000) directly, not
        # against day 2's actual equity (50500). A withdrawal between
        # cycles isn't a trading loss and must never leak into this delta,
        # so continuing the old chain here would be wrong (it would give
        # 2500 instead of the correct 3000).
        self.assertEqual(
            history.daily_profits(days, 50000, since_date="2026-08-03"),
            [("2026-08-03", 3000)],
        )

    def test_since_date_before_every_day_changes_nothing(self):
        days = [{"date": "2026-08-01", "equity": 51000}]
        self.assertEqual(
            history.daily_profits(days, 50000, since_date="2026-07-01"),
            [("2026-08-01", 1000)],
        )


class BestDayProfitTests(unittest.TestCase):
    def test_no_history_is_zero(self):
        self.assertEqual(history.best_day_profit([], 50000), 0.0)

    def test_picks_the_largest_single_day(self):
        days = [
            {"date": "2026-08-01", "equity": 50500},
            {"date": "2026-08-02", "equity": 53000},
            {"date": "2026-08-03", "equity": 52500},
        ]
        # Day 1: +500, Day 2: +2500, Day 3: -500.
        self.assertEqual(history.best_day_profit(days, 50000), 2500)

    def test_all_losing_days_still_returns_the_least_bad_one(self):
        days = [
            {"date": "2026-08-01", "equity": 49000},
            {"date": "2026-08-02", "equity": 48500},
        ]
        # Day 1: -1000, Day 2: -500.
        self.assertEqual(history.best_day_profit(days, 50000), -500)

    def test_since_date_excludes_an_earlier_days_contribution_entirely(self):
        days = [
            {"date": "2026-08-01", "equity": 100000},  # a prior cycle's own huge day -- excluded
            {"date": "2026-08-02", "equity": 50500},   # new cycle's first day: +500
            {"date": "2026-08-03", "equity": 51000},   # +500
        ]
        # Without since_date, day 1's implicit +50000 (from starting_balance)
        # would dominate. With it, only the two post-cutoff days -- each
        # +500 against the reset baseline -- are ever considered.
        self.assertEqual(history.best_day_profit(days, 50000, since_date="2026-08-02"), 500)


class TotalProfitTests(unittest.TestCase):
    def test_no_history_is_zero(self):
        self.assertEqual(history.total_profit([], 50000), 0.0)

    def test_is_the_latest_equity_minus_starting_balance(self):
        days = [
            {"date": "2026-08-01", "equity": 51000},
            {"date": "2026-08-02", "equity": 54000},
        ]
        self.assertEqual(history.total_profit(days, 50000), 4000)

    def test_since_date_measures_from_the_cycles_own_starting_balance(self):
        days = [
            {"date": "2026-08-01", "equity": 60000},   # a prior cycle's own day -- irrelevant once excluded
            {"date": "2026-08-02", "equity": 51000},   # new cycle's first day (e.g. right after a withdrawal)
            {"date": "2026-08-03", "equity": 51700},
        ]
        # starting_balance here (50500) is the new cycle's own starting
        # equity -- neither day 1's value nor the account's original
        # onboarding balance. Total profit since the cutoff telescopes to
        # the latest equity minus *that*: 51700 - 50500 = 1200.
        self.assertEqual(history.total_profit(days, 50500, since_date="2026-08-02"), 1200)


class ProfitableDayCountTests(unittest.TestCase):
    def test_no_history_is_zero(self):
        self.assertEqual(history.profitable_day_count([], 50000, 250), 0)

    def test_counts_only_days_meeting_the_minimum(self):
        days = [
            {"date": "2026-08-01", "equity": 50100},   # +100, below min
            {"date": "2026-08-02", "equity": 50600},   # +500, meets min
            {"date": "2026-08-03", "equity": 50400},   # -200, a loss
            {"date": "2026-08-04", "equity": 51000},   # +600, meets min
        ]
        self.assertEqual(history.profitable_day_count(days, 50000, 250), 2)

    def test_a_day_exactly_at_the_minimum_counts(self):
        days = [{"date": "2026-08-01", "equity": 50250}]  # +250, exactly min
        self.assertEqual(history.profitable_day_count(days, 50000, 250), 1)

    def test_since_date_excludes_an_earlier_qualifying_day(self):
        days = [
            {"date": "2026-08-01", "equity": 49000},   # excluded -- a lower prior-cycle day
            {"date": "2026-08-02", "equity": 50200},   # new cycle's first day
        ]
        # If this wrongly chained from day 1's actual equity (49000), day
        # 2's delta would be 1200 -- comfortably over the 250 minimum.
        # Reset against the new cycle's own starting_balance (50000)
        # instead, it's only 200 -- correctly not profitable enough to
        # count. This is exactly the kind of mistake that would let a
        # withdrawal between cycles get misread as a huge trading loss on
        # the day right after it.
        self.assertEqual(history.profitable_day_count(days, 50000, 250, since_date="2026-08-02"), 0)


class DetectWithdrawalTests(unittest.TestCase):
    def _is_withdrawal(self, *args, **kwargs):
        is_withdrawal, _details = history.detect_withdrawal(*args, **kwargs)
        return is_withdrawal

    def test_no_history_yet_is_never_a_withdrawal(self):
        self.assertFalse(self._is_withdrawal([], 50000, today_total_pl=0))

    def test_unreadable_total_pl_is_never_a_withdrawal(self):
        # None means "couldn't be checked", not "zero" -- never guess.
        days = [{"date": "2026-08-01", "equity": 53000}]
        self.assertFalse(self._is_withdrawal(days, 40000, today_total_pl=None))

    def test_unchanged_balance_with_no_trading_is_not_a_withdrawal(self):
        days = [{"date": "2026-08-01", "equity": 53000}]
        self.assertFalse(self._is_withdrawal(days, 53000, today_total_pl=0))

    def test_a_drop_fully_explained_by_todays_trading_loss_is_not_a_withdrawal(self):
        # Balance dropped by exactly what trading lost today -- an
        # ordinary losing day, not a withdrawal.
        days = [{"date": "2026-08-01", "equity": 53000}]
        self.assertFalse(self._is_withdrawal(days, 52500, today_total_pl=-500))

    def test_a_drop_with_no_trading_today_is_a_withdrawal(self):
        days = [{"date": "2026-08-01", "equity": 53000}]
        self.assertTrue(self._is_withdrawal(days, 52500, today_total_pl=0))

    def test_a_drop_bigger_than_todays_trading_loss_is_a_withdrawal(self):
        # $200 trading loss, but the balance actually dropped $700 --
        # $500 of that is unexplained by trading (a same-day withdrawal
        # alongside an ordinary losing day).
        days = [{"date": "2026-08-01", "equity": 53000}]
        self.assertTrue(self._is_withdrawal(days, 52300, today_total_pl=-200))

    def test_a_profitable_day_that_still_ends_with_a_lower_balance_is_a_withdrawal(self):
        # +$300 today, but the balance is still $200 *lower* overall --
        # $500 must have been pulled out, exceeding the day's profit.
        days = [{"date": "2026-08-01", "equity": 53000}]
        self.assertTrue(self._is_withdrawal(days, 52800, today_total_pl=300))

    def test_a_drop_smaller_than_todays_trading_loss_is_not_a_withdrawal(self):
        # Trading lost $500, but the balance only dropped $200 -- nothing
        # unexplained (if anything, this looks like money came *in*,
        # which isn't what this detects).
        days = [{"date": "2026-08-01", "equity": 53000}]
        self.assertFalse(self._is_withdrawal(days, 52800, today_total_pl=-500))

    def test_a_tiny_mismatch_within_tolerance_is_not_a_withdrawal(self):
        days = [{"date": "2026-08-01", "equity": 53000}]
        self.assertFalse(self._is_withdrawal(days, 52999.5, today_total_pl=0, tolerance=1.0))

    def test_exactly_at_the_tolerance_boundary_is_not_a_withdrawal(self):
        days = [{"date": "2026-08-01", "equity": 53000}]
        self.assertFalse(self._is_withdrawal(days, 52999.0, today_total_pl=0, tolerance=1.0))

    def test_just_past_the_tolerance_boundary_is_a_withdrawal(self):
        days = [{"date": "2026-08-01", "equity": 53000}]
        self.assertTrue(self._is_withdrawal(days, 52998.9, today_total_pl=0, tolerance=1.0))

    def test_compares_against_the_last_recorded_day_not_an_earlier_one(self):
        days = [
            {"date": "2026-08-01", "equity": 60000},
            {"date": "2026-08-02", "equity": 53000},
        ]
        # Dropping to 52500 from the *last* recorded day (53000) is a
        # $500 unexplained drop -- not compared against the much higher
        # first day (60000), which would swamp everything else.
        self.assertTrue(self._is_withdrawal(days, 52500, today_total_pl=0))


class DetectWithdrawalDetailsTests(unittest.TestCase):
    """Covers the details dict detect_withdrawal() returns alongside the
    bool -- meant for narrating *why* a verdict was reached (see
    signal_source._print_withdrawal_check), mirroring flip_mode.evaluate()'s
    own (decision, ..., details) shape."""

    def test_no_history_populates_only_the_current_readings(self):
        _is_withdrawal, details = history.detect_withdrawal([], 50000, today_total_pl=0)
        self.assertIsNone(details['last_recorded_date'])
        self.assertIsNone(details['last_recorded_equity'])
        self.assertEqual(details['current_balance'], 50000)
        self.assertEqual(details['today_total_pl'], 0)
        self.assertIsNone(details['actual_change'])
        self.assertIsNone(details['unexplained'])

    def test_unreadable_total_pl_leaves_actual_change_and_unexplained_unset(self):
        days = [{"date": "2026-08-01", "equity": 53000}]
        _is_withdrawal, details = history.detect_withdrawal(days, 52500, today_total_pl=None)
        self.assertEqual(details['last_recorded_date'], "2026-08-01")
        self.assertEqual(details['last_recorded_equity'], 53000)
        self.assertIsNone(details['today_total_pl'])
        # Actual change *could* have been computed (both balances are
        # known), but is deliberately left None -- there's no unexplained
        # figure to report without a real Total P/L to subtract, so
        # nothing here should look more complete than it is.
        self.assertIsNone(details['actual_change'])
        self.assertIsNone(details['unexplained'])

    def test_a_normal_check_populates_every_field(self):
        days = [{"date": "2026-08-01", "equity": 53000}]
        is_withdrawal, details = history.detect_withdrawal(days, 52300, today_total_pl=-200, tolerance=1.0)
        self.assertEqual(details['last_recorded_date'], "2026-08-01")
        self.assertEqual(details['last_recorded_equity'], 53000)
        self.assertEqual(details['current_balance'], 52300)
        self.assertEqual(details['today_total_pl'], -200)
        self.assertEqual(details['tolerance'], 1.0)
        self.assertEqual(details['actual_change'], -700)
        self.assertEqual(details['unexplained'], -500)
        self.assertTrue(is_withdrawal)


if __name__ == "__main__":
    unittest.main()
