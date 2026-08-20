"""Covers signal_source.generate_next_trade's open_positions handling --
the fix for a real bug seen in production: TradingGenerator has no idea a
portfolio already has a tracked open position in our own ledger, so it
happily "generates" a signal for one anyway. Without skipping it here,
nothing stopped the very next retry from picking that exact same
already-open portfolio again and again -- burning through generate
attempts on a signal that check_eligibility's 'wait_this_portfolio' was
always going to decline, and contributing to hitting the loop's
consecutive-failures kill switch for no real reason. Run with:

    python -m unittest tests.test_generate_next_trade_open_positions -v
"""

import unittest
from unittest.mock import MagicMock, patch

from tv_signal_trader import signal_source


class GenerateNextTradeOpenPositionsTests(unittest.TestCase):
    def setUp(self):
        self.driver = MagicMock()

    def test_hint_matching_an_open_position_is_skipped_in_favor_of_fallback(self):
        open_positions = {("Apex Trader Funding", "PA010"): {}}
        with patch.object(signal_source.tg, "generate_trade", return_value='generated') as gen_mock, \
             patch.object(signal_source.tg, "list_all_candidates",
                           return_value=[("Apex Trader Funding", "PA010"), ("Apex Trader Funding", "PA020")]):
            result = signal_source.generate_next_trade(
                self.driver, "Apex Trader Funding", "PA010", set(), open_positions=open_positions
            )
        self.assertEqual(result, 'generated')
        # Never attempted the hint itself (PA010) -- went straight to the
        # fallback loop and generated for the first non-open candidate.
        gen_mock.assert_called_once_with(
            self.driver, expected_company="Apex Trader Funding", expected_portfolio="PA020", force=True
        )

    def test_fallback_loop_skips_a_candidate_that_is_already_open(self):
        open_positions = {("Apex Trader Funding", "PA010"): {}}
        # No hint -- the initial "whatever's currently selected" attempt
        # always fires first regardless of open_positions; make it fail
        # so control reaches the fallback loop, which must then skip the
        # open PA010 and land on PA020.
        with patch.object(signal_source.tg, "generate_trade",
                           side_effect=['wrong_account', 'generated']) as gen_mock, \
             patch.object(signal_source.tg, "read_active_company_portfolio", return_value=(None, None)), \
             patch.object(signal_source.tg, "list_all_candidates",
                           return_value=[("Apex Trader Funding", "PA010"), ("Apex Trader Funding", "PA020")]):
            result = signal_source.generate_next_trade(
                self.driver, None, None, set(), open_positions=open_positions
            )
        self.assertEqual(result, 'generated')
        self.assertEqual(gen_mock.call_count, 2)
        gen_mock.assert_called_with(
            self.driver, expected_company="Apex Trader Funding", expected_portfolio="PA020", force=True
        )

    def test_repeated_calls_never_retarget_the_same_open_portfolio(self):
        # Simulates the exact production scenario: PA010 is open, and it's
        # the only candidate -- generate_next_trade must not re-target it
        # in the fallback loop; it should report 'exhausted' instead of
        # wasting a generate click on a portfolio that can never succeed
        # while it's open.
        open_positions = {("Apex Trader Funding", "PA010"): {}}
        with patch.object(signal_source.tg, "generate_trade", return_value='wrong_account') as gen_mock, \
             patch.object(signal_source.tg, "read_active_company_portfolio", return_value=(None, None)), \
             patch.object(signal_source.tg, "list_all_candidates",
                           return_value=[("Apex Trader Funding", "PA010")]):
            result = signal_source.generate_next_trade(
                self.driver, None, None, set(), open_positions=open_positions
            )
        self.assertEqual(result, 'exhausted')
        # Only the initial no-hint attempt happened -- the fallback loop's
        # sole candidate (PA010) was skipped for being open, never retried.
        self.assertEqual(gen_mock.call_count, 1)

    def test_a_set_of_keys_works_the_same_as_a_dict(self):
        # A plain set of (company, portfolio) keys (no .keys() method)
        # must work identically to the real open_positions dict.
        open_positions = {("Apex Trader Funding", "PA010")}
        with patch.object(signal_source.tg, "generate_trade",
                           side_effect=['wrong_account', 'generated']) as gen_mock, \
             patch.object(signal_source.tg, "read_active_company_portfolio", return_value=(None, None)), \
             patch.object(signal_source.tg, "list_all_candidates",
                           return_value=[("Apex Trader Funding", "PA010"), ("Apex Trader Funding", "PA020")]):
            result = signal_source.generate_next_trade(
                self.driver, None, None, set(), open_positions=open_positions
            )
        self.assertEqual(result, 'generated')
        self.assertEqual(gen_mock.call_count, 2)
        gen_mock.assert_called_with(
            self.driver, expected_company="Apex Trader Funding", expected_portfolio="PA020", force=True
        )

    def test_defaults_to_no_open_positions_when_omitted(self):
        # web's own single-position engine (if it ever called this
        # directly without a ledger) must still work unchanged.
        with patch.object(signal_source.tg, "generate_trade", return_value='generated') as gen_mock:
            result = signal_source.generate_next_trade(self.driver, "Apex Trader Funding", "PA010", set())
        self.assertEqual(result, 'generated')
        gen_mock.assert_called_once_with(
            self.driver, expected_company="Apex Trader Funding", expected_portfolio="PA010"
        )

    def test_unavailable_and_open_positions_are_both_respected_in_the_fallback(self):
        unavailable = {("Apex Trader Funding", "PA020")}
        open_positions = {("Apex Trader Funding", "PA010"): {}}
        with patch.object(signal_source.tg, "generate_trade",
                           side_effect=['wrong_account', 'generated']) as gen_mock, \
             patch.object(signal_source.tg, "read_active_company_portfolio", return_value=(None, None)), \
             patch.object(signal_source.tg, "list_all_candidates", return_value=[
                 ("Apex Trader Funding", "PA010"),
                 ("Apex Trader Funding", "PA020"),
                 ("Apex Trader Funding", "PA030"),
             ]):
            result = signal_source.generate_next_trade(
                self.driver, None, None, unavailable, open_positions=open_positions
            )
        self.assertEqual(result, 'generated')
        self.assertEqual(gen_mock.call_count, 2)
        gen_mock.assert_called_with(
            self.driver, expected_company="Apex Trader Funding", expected_portfolio="PA030", force=True
        )


if __name__ == "__main__":
    unittest.main()
