"""Covers flip_mode.evaluate() -- the pure Flip Mode 3-condition state
machine (equity/consistency/day-count, plus the Flip Mode escalate/exit
cycle), entirely over hand-derived fixture data, no driver/DOM involved.
Every branch of the flow described in the Flip Mode plan gets its own
test case with numbers worked out by hand, since this is exactly the kind
of condition logic this project has already been burned by a subtly
wrong branch in once this session. All examples use a LIVE 50K tier
(starting_balance=50000, tier_final=53500) unless noted. Run with:

    python -m unittest tests.test_flip_mode -v
"""

import unittest

from tv_signal_trader import flip_mode


class EvaluateTests(unittest.TestCase):
    def _evaluate(self, **overrides):
        kwargs = dict(
            current_balance=50000,
            starting_balance=50000,
            tier_final=53500,
            running_equity_target=53000,
            in_flip_mode=False,
            days=[],
            min_profitable_days=2,
            min_daily_profit=200,
            consistency_divisor=0.5,
        )
        kwargs.update(overrides)
        # Most of these tests only care about (decision, target) -- the
        # details breakdown (see DetailsTests below) is exercised
        # separately so the branch-coverage tests here don't need to
        # unpack (and ignore) a third value every time.
        decision, target, _details = flip_mode.evaluate(**kwargs)
        return decision, target

    # --- NORMAL trading ---

    def test_below_the_running_target_keeps_normal_with_the_target_unchanged(self):
        decision, target = self._evaluate(current_balance=52000, running_equity_target=53000)
        self.assertEqual(decision, 'keep_normal')
        self.assertEqual(target, 53000)

    def test_reaches_target_consistency_holds_clears_final_day_count_met_qualifies(self):
        # 2 days: +3000 (best), +600 -- total 3600, best 3000.
        # Consistency: 3000 >= 0.5*3600 (1800) -- holds.
        # Recomputed target -> tier_final (53500). Balance 53600 clears it.
        # 2 profitable days, min 2 -- day-count met.
        days = [
            {"date": "2026-08-01", "equity": 53000},
            {"date": "2026-08-02", "equity": 53600},
        ]
        decision, target = self._evaluate(
            current_balance=53600, running_equity_target=53000, days=days
        )
        self.assertEqual(decision, 'qualifies_for_removal')
        self.assertEqual(target, 53500)

    def test_reaches_target_consistency_holds_clears_final_day_count_not_met_enters_flip_mode(self):
        # Same days as above, but min_profitable_days raised to 3 -- not met.
        days = [
            {"date": "2026-08-01", "equity": 53000},
            {"date": "2026-08-02", "equity": 53600},
        ]
        decision, target = self._evaluate(
            current_balance=53600, running_equity_target=53000, days=days, min_profitable_days=3
        )
        self.assertEqual(decision, 'enter_flip_mode')
        self.assertEqual(target, 53500)

    def test_reaches_target_consistency_holds_but_balance_still_under_final_keeps_normal(self):
        # 1 day: +3100 (equity 53100). total=best=3100 -- consistency
        # trivially holds for a single day. Recomputed target -> tier_final
        # (53500), but balance (53100) doesn't clear it yet.
        days = [{"date": "2026-08-01", "equity": 53100}]
        decision, target = self._evaluate(
            current_balance=53100, running_equity_target=53000, days=days
        )
        self.assertEqual(decision, 'keep_normal')
        self.assertEqual(target, 53500)

    def test_reaches_target_consistency_fails_escalated_target_exceeds_final_qualifies(self):
        # 3 days: +2000 (best), +1000, +1300 -- total 4300, best 2000.
        # Consistency: 2000 >= 0.5*4300 (2150)? No -- fails.
        # Escalated target: 50000 + 2000/0.5 + 1 = 54001, above tier_final
        # (53500) -- wins. Balance (54300) clears it. 3 profitable days,
        # min 2 -- day-count met.
        days = [
            {"date": "2026-08-01", "equity": 52000},
            {"date": "2026-08-02", "equity": 53000},
            {"date": "2026-08-03", "equity": 54300},
        ]
        decision, target = self._evaluate(
            current_balance=54300, running_equity_target=53000, days=days
        )
        self.assertEqual(decision, 'qualifies_for_removal')
        self.assertEqual(target, 54001)

    def test_reaches_target_consistency_fails_escalated_target_exceeds_final_day_count_not_met(self):
        days = [
            {"date": "2026-08-01", "equity": 52000},
            {"date": "2026-08-02", "equity": 53000},
            {"date": "2026-08-03", "equity": 54300},
        ]
        decision, target = self._evaluate(
            current_balance=54300, running_equity_target=53000, days=days, min_profitable_days=4
        )
        self.assertEqual(decision, 'enter_flip_mode')
        self.assertEqual(target, 54001)

    def test_reaches_target_consistency_fails_but_escalated_target_below_final_keeps_normal(self):
        # 3 equal days of +1000 -- total 3000, best 1000.
        # Consistency: 1000 >= 0.5*3000 (1500)? No -- fails.
        # Escalated target: 50000 + 1000/0.5 + 1 = 52001, below tier_final
        # (53500) -- tier_final wins instead. Balance (53000) doesn't
        # clear 53500.
        days = [
            {"date": "2026-08-01", "equity": 51000},
            {"date": "2026-08-02", "equity": 52000},
            {"date": "2026-08-03", "equity": 53000},
        ]
        decision, target = self._evaluate(
            current_balance=53000, running_equity_target=53000, days=days
        )
        self.assertEqual(decision, 'keep_normal')
        self.assertEqual(target, 53500)

    # --- FLIP MODE ---

    def test_flip_mode_day_count_not_met_keeps_flip_mode_with_target_unchanged(self):
        days = [{"date": "2026-08-01", "equity": 51000}]  # 1 profitable day, min 2
        decision, target = self._evaluate(
            current_balance=51000, running_equity_target=53500, in_flip_mode=True, days=days,
        )
        self.assertEqual(decision, 'keep_flip_mode')
        self.assertEqual(target, 53500)

    def test_flip_mode_day_count_met_consistency_holds_balance_clears_target_qualifies(self):
        # Reuse the "qualifies via consistency holding" days from NORMAL.
        days = [
            {"date": "2026-08-01", "equity": 53000},
            {"date": "2026-08-02", "equity": 53600},
        ]
        decision, target = self._evaluate(
            current_balance=53600, running_equity_target=53500, in_flip_mode=True, days=days,
        )
        self.assertEqual(decision, 'qualifies_for_removal')
        # Target is returned unchanged here -- not re-derived, since both
        # conditions already held against it as-is.
        self.assertEqual(target, 53500)

    def test_flip_mode_day_count_met_consistency_fails_exits_with_a_recomputed_target(self):
        # Reuse the "consistency fails, escalated below final" days.
        days = [
            {"date": "2026-08-01", "equity": 51000},
            {"date": "2026-08-02", "equity": 52000},
            {"date": "2026-08-03", "equity": 53000},
        ]
        decision, target = self._evaluate(
            current_balance=53000, running_equity_target=53000, in_flip_mode=True, days=days,
        )
        self.assertEqual(decision, 'exit_flip_mode')
        self.assertEqual(target, 53500)

    def test_flip_mode_day_count_met_consistency_holds_but_balance_under_target_exits(self):
        # Single day, consistency trivially holds, but the *running*
        # target (53500, e.g. left over from before Flip Mode) is higher
        # than the current balance (53100) -- fails that half of the
        # re-check even though consistency itself is fine.
        days = [{"date": "2026-08-01", "equity": 53100}]
        decision, target = self._evaluate(
            current_balance=53100, running_equity_target=53500, in_flip_mode=True, days=days,
            min_profitable_days=1,
        )
        self.assertEqual(decision, 'exit_flip_mode')
        # Recomputed: consistency holds -> tier_final.
        self.assertEqual(target, 53500)

    # --- since_date scoping (Phase 3's per-cycle tracking) ---

    def test_since_date_excludes_an_earlier_days_contribution_to_day_count(self):
        days = [
            {"date": "2026-08-01", "equity": 50700},  # profitable, before since_date
            {"date": "2026-08-02", "equity": 51400},  # profitable, on since_date
        ]
        kwargs = dict(
            current_balance=51400, running_equity_target=53000, in_flip_mode=True, days=days,
        )

        # Without a cutoff, both days count -- 2 profitable days meets the
        # min-2 requirement, so this proceeds past the immediate
        # keep_flip_mode return (whatever it resolves to next).
        decision_without, _ = self._evaluate(**kwargs)
        self.assertNotEqual(decision_without, 'keep_flip_mode')

        # With since_date excluding the first day, only 1 profitable day
        # remains -- day-count isn't met, so this returns immediately.
        decision_with, target_with = self._evaluate(**kwargs, since_date="2026-08-02")
        self.assertEqual(decision_with, 'keep_flip_mode')
        self.assertEqual(target_with, 53000)


class DetailsTests(unittest.TestCase):
    """Covers the details dict evaluate() returns alongside the decision
    -- meant for narrating *why* a decision was reached (see
    multi_signal_source._print_flip_mode_decision), so this checks that
    only the conditions actually evaluated end up populated, with the
    right numbers, rather than branching on the decision string itself."""

    def _evaluate(self, **overrides):
        kwargs = dict(
            current_balance=50000, starting_balance=50000, tier_final=53500,
            running_equity_target=53000, in_flip_mode=False, days=[],
            min_profitable_days=2, min_daily_profit=200, consistency_divisor=0.5,
        )
        kwargs.update(overrides)
        return flip_mode.evaluate(**kwargs)

    def test_below_target_leaves_consistency_and_day_count_unchecked(self):
        _decision, _target, details = self._evaluate(current_balance=52000, running_equity_target=53000)
        self.assertEqual(details['current_balance'], 52000)
        self.assertEqual(details['running_equity_target_before'], 53000)
        self.assertFalse(details['in_flip_mode'])
        self.assertIsNone(details['total_profit'])
        self.assertIsNone(details['best_day_profit'])
        self.assertIsNone(details['consistency_holds'])
        self.assertIsNone(details['profitable_days'])
        self.assertIsNone(details['day_count_met'])

    def test_target_cleared_populates_consistency_but_not_day_count_if_target_not_yet_met(self):
        # Single day, consistency trivially holds; recomputed target
        # (tier_final, 53500) still isn't cleared by balance 53100 --
        # day-count is never reached.
        days = [{"date": "2026-08-01", "equity": 53100}]
        _decision, _target, details = self._evaluate(
            current_balance=53100, running_equity_target=53000, days=days
        )
        self.assertEqual(details['total_profit'], 3100)
        self.assertEqual(details['best_day_profit'], 3100)
        self.assertTrue(details['consistency_holds'])
        self.assertIsNone(details['profitable_days'])
        self.assertIsNone(details['day_count_met'])

    def test_qualifying_pass_populates_every_field(self):
        days = [
            {"date": "2026-08-01", "equity": 53000},
            {"date": "2026-08-02", "equity": 53600},
        ]
        _decision, _target, details = self._evaluate(
            current_balance=53600, running_equity_target=53000, days=days
        )
        self.assertEqual(details['total_profit'], 3600)
        self.assertEqual(details['best_day_profit'], 3000)
        self.assertTrue(details['consistency_holds'])
        self.assertEqual(details['profitable_days'], 2)
        self.assertTrue(details['day_count_met'])

    def test_keep_flip_mode_only_populates_day_count_fields(self):
        days = [{"date": "2026-08-01", "equity": 51000}]  # 1 profitable day, min 2
        _decision, _target, details = self._evaluate(
            current_balance=51000, running_equity_target=53500, in_flip_mode=True, days=days,
        )
        self.assertTrue(details['in_flip_mode'])
        self.assertEqual(details['profitable_days'], 1)
        self.assertFalse(details['day_count_met'])
        self.assertIsNone(details['total_profit'])
        self.assertIsNone(details['consistency_holds'])


class SeedReentryTargetTests(unittest.TestCase):
    """Covers flip_mode.seed_reentry_target() -- the re-entry target seed
    for an account just re-added to TradingGenerator after a detected
    withdrawal (see the Flip Mode plan's Phase 3)."""

    def test_below_tier_final_seeds_at_tier_final(self):
        # The normal case: the withdrawal took the account well under its
        # tier's final threshold -- seed there directly, not the buffer
        # formula (which is only for the account having kept growing past
        # tier_final in the gap before the withdrawal was detected).
        self.assertEqual(flip_mode.seed_reentry_target(51000, tier_final=53500, buffer=1500), 53500)

    def test_at_tier_final_seeds_with_the_buffer(self):
        # "at or above" -- a post-withdrawal equity exactly equal to
        # tier_final would otherwise seed a target already cleared,
        # immediately re-qualifying for removal with no real evaluation
        # in between.
        self.assertEqual(flip_mode.seed_reentry_target(53500, tier_final=53500, buffer=1500), 55000)

    def test_above_tier_final_seeds_post_withdrawal_equity_plus_the_buffer(self):
        # The account kept growing between qualifying for removal and the
        # withdrawal actually being detected -- the seed must still be a
        # real target ahead of where it already is, not the (already
        # cleared) tier final.
        self.assertEqual(flip_mode.seed_reentry_target(54200, tier_final=53500, buffer=1500), 55700)

    def test_uses_whatever_buffer_is_passed_in(self):
        self.assertEqual(flip_mode.seed_reentry_target(53500, tier_final=53500, buffer=1000), 54500)


if __name__ == "__main__":
    unittest.main()
