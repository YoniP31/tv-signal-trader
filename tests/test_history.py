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


class TotalProfitTests(unittest.TestCase):
    def test_no_history_is_zero(self):
        self.assertEqual(history.total_profit([], 50000), 0.0)

    def test_is_the_latest_equity_minus_starting_balance(self):
        days = [
            {"date": "2026-08-01", "equity": 51000},
            {"date": "2026-08-02", "equity": 54000},
        ]
        self.assertEqual(history.total_profit(days, 50000), 4000)


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


if __name__ == "__main__":
    unittest.main()
