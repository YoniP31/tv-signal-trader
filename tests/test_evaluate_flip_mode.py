"""Covers multi_signal_source._gather_flip_mode_inputs/evaluate_flip_mode --
the Step 3 "dry run" wiring that reads a real account's live balance/type/
Flip-Mode-state plus its persisted history, then hands it to
flip_mode.evaluate() for a decision. Read-only: no button clicks, no
remove_portfolio, no status.json writes -- only reached today via the
'flip_mode_dry_run' test command. Run with:

    python -m unittest tests.test_evaluate_flip_mode -v
"""

import unittest
from unittest.mock import MagicMock, patch

from tv_signal_trader import config
from tv_signal_trader import multi_signal_source as ms


class GatherFlipModeInputsTests(unittest.TestCase):
    def setUp(self):
        self.driver = MagicMock()
        self.web_tab = "web_tab"
        self.tv_tab = "tv_tab"

    def _patches(self, **overrides):
        defaults = dict(
            _ensure_tradovate_connection=MagicMock(return_value="Apex Trader Funding"),
            select_tradovate_account_with_reconnect=MagicMock(return_value=True),
            read_account_balance=MagicMock(return_value=53200.0),
            _select_and_verify=MagicMock(return_value=True),
            read_active_account_type=MagicMock(return_value="LIVE"),
            set_account_type=MagicMock(),
            is_flip_mode_active=MagicMock(return_value=False),
            get_running_equity_target=MagicMock(return_value=None),
            get_equity_history=MagicMock(return_value=[]),
            get_cycle_start_date=MagicMock(return_value=None),
            get_cycle_starting_balance=MagicMock(return_value=None),
        )
        defaults.update(overrides)
        return defaults

    def _run(self, **overrides):
        p = self._patches(**overrides)
        with patch.object(ms, "_ensure_tradovate_connection", p["_ensure_tradovate_connection"]), \
             patch.object(ms.trading, "select_tradovate_account_with_reconnect",
                           p["select_tradovate_account_with_reconnect"]), \
             patch.object(ms.trading, "read_account_balance", p["read_account_balance"]), \
             patch.object(ms, "_select_and_verify", p["_select_and_verify"]), \
             patch.object(ms.tg, "read_active_account_type", p["read_active_account_type"]), \
             patch.object(ms.status, "set_account_type", p["set_account_type"]), \
             patch.object(ms.tg, "is_flip_mode_active", p["is_flip_mode_active"]), \
             patch.object(ms.status, "get_running_equity_target", p["get_running_equity_target"]), \
             patch.object(ms.status, "get_equity_history", p["get_equity_history"]), \
             patch.object(ms.status, "get_cycle_start_date", p["get_cycle_start_date"]), \
             patch.object(ms.status, "get_cycle_starting_balance", p["get_cycle_starting_balance"]):
            return ms._gather_flip_mode_inputs(
                self.driver, self.web_tab, self.tv_tab, "Apex Trader Funding", "PAAPEX0001", None
            )

    def test_gathers_every_field_on_the_happy_path(self):
        inputs, connected_company = self._run()
        self.assertEqual(connected_company, "Apex Trader Funding")
        self.assertEqual(inputs['current_balance'], 53200.0)
        self.assertEqual(inputs['account_type'], "LIVE")
        self.assertEqual(inputs['tier_size'], 50000)
        self.assertEqual(inputs['in_flip_mode'], False)
        self.assertEqual(inputs['days'], [])
        self.assertIsNone(inputs['since_date'])

    def test_tier_initial_final_come_from_the_confirmed_live_50k_table(self):
        inputs, _ = self._run()
        self.assertEqual(inputs['tier_initial'], config.ACCOUNT_BALANCE_TIERS[50000]['max_initial']['LIVE'])
        self.assertEqual(inputs['tier_final'], config.ACCOUNT_BALANCE_TIERS[50000]['max_final']['LIVE'])

    def test_persists_the_account_type_on_every_read_not_just_at_removal(self):
        # So Second Withdrawal tracking (LIVE only) still knows an
        # account's type after it's gone, even if a human removed it
        # directly and the bot's own removal logic never ran.
        p = self._patches(read_active_account_type=MagicMock(return_value="LIVE"))
        with patch.object(ms, "_ensure_tradovate_connection", p["_ensure_tradovate_connection"]), \
             patch.object(ms.trading, "select_tradovate_account_with_reconnect",
                           p["select_tradovate_account_with_reconnect"]), \
             patch.object(ms.trading, "read_account_balance", p["read_account_balance"]), \
             patch.object(ms, "_select_and_verify", p["_select_and_verify"]), \
             patch.object(ms.tg, "read_active_account_type", p["read_active_account_type"]), \
             patch.object(ms.status, "set_account_type", p["set_account_type"]) as set_type_mock, \
             patch.object(ms.tg, "is_flip_mode_active", p["is_flip_mode_active"]), \
             patch.object(ms.status, "get_running_equity_target", p["get_running_equity_target"]), \
             patch.object(ms.status, "get_equity_history", p["get_equity_history"]), \
             patch.object(ms.status, "get_cycle_start_date", p["get_cycle_start_date"]), \
             patch.object(ms.status, "get_cycle_starting_balance", p["get_cycle_starting_balance"]):
            ms._gather_flip_mode_inputs(
                self.driver, self.web_tab, self.tv_tab, "Apex Trader Funding", "PAAPEX0001", None
            )
        set_type_mock.assert_called_once_with("Apex Trader Funding", "PAAPEX0001", "LIVE")

    def test_tier_min_is_the_tiers_own_min(self):
        # The blown-account check is unaffected by Flip Mode.
        inputs, _ = self._run()
        self.assertEqual(inputs['tier_min'], config.ACCOUNT_BALANCE_TIERS[50000]['min'])

    def test_tp_cap_threshold_is_the_tier_final_while_still_at_the_initial_target(self):
        # A trade should be capped against the account's real profit
        # goal, not the not-yet-proven initial staging checkpoint --
        # confirmed with the user. No running_equity_target persisted yet
        # falls back to tier_initial (53000), but capping must still use
        # tier_final (53500), never that lower initial value.
        inputs, _ = self._run(get_running_equity_target=MagicMock(return_value=None))
        self.assertEqual(inputs['running_equity_target'], 53000.0)
        self.assertEqual(inputs['tp_cap_threshold'], 53500.0)

    def test_tp_cap_threshold_is_the_final_once_the_target_has_advanced_there(self):
        inputs, _ = self._run(get_running_equity_target=MagicMock(return_value=53500.0))
        self.assertEqual(inputs['tp_cap_threshold'], 53500.0)

    def test_tp_cap_threshold_follows_an_escalated_running_target_above_final(self):
        # Consistency failed on an earlier pass and escalated the target
        # past tier_final (53500) -- capping must track that escalated
        # value, not silently cap back down at the tier final.
        inputs, _ = self._run(get_running_equity_target=MagicMock(return_value=54001.0))
        self.assertEqual(inputs['tp_cap_threshold'], 54001.0)

    def test_falls_back_to_the_tier_initial_target_when_none_is_persisted_yet(self):
        inputs, _ = self._run(get_running_equity_target=MagicMock(return_value=None))
        self.assertEqual(inputs['running_equity_target'], inputs['tier_initial'])

    def test_uses_the_persisted_running_target_when_one_exists(self):
        inputs, _ = self._run(get_running_equity_target=MagicMock(return_value=53600.0))
        self.assertEqual(inputs['running_equity_target'], 53600.0)

    def test_falls_back_to_the_tier_size_as_starting_balance_on_a_first_cycle(self):
        inputs, _ = self._run(get_cycle_starting_balance=MagicMock(return_value=None))
        self.assertEqual(inputs['starting_balance'], inputs['tier_size'])
        self.assertEqual(inputs['starting_balance'], 50000)

    def test_uses_the_persisted_cycle_starting_balance_after_a_withdrawal_reset(self):
        inputs, _ = self._run(get_cycle_starting_balance=MagicMock(return_value=50700.0))
        self.assertEqual(inputs['starting_balance'], 50700.0)

    def test_returns_none_if_tradovate_connection_fails(self):
        inputs, connected_company = self._run(_ensure_tradovate_connection=MagicMock(return_value=None))
        self.assertIsNone(inputs)
        self.assertIsNone(connected_company)

    def test_returns_none_if_the_tradovate_account_cannot_be_selected(self):
        inputs, connected_company = self._run(
            select_tradovate_account_with_reconnect=MagicMock(return_value=False)
        )
        self.assertIsNone(inputs)
        self.assertEqual(connected_company, "Apex Trader Funding")

    def test_returns_none_if_the_balance_cannot_be_read(self):
        inputs, connected_company = self._run(read_account_balance=MagicMock(return_value=None))
        self.assertIsNone(inputs)
        self.assertEqual(connected_company, "Apex Trader Funding")

    def test_returns_none_if_the_portfolio_selection_cannot_be_confirmed(self):
        inputs, connected_company = self._run(_select_and_verify=MagicMock(return_value=False))
        self.assertIsNone(inputs)
        self.assertEqual(connected_company, "Apex Trader Funding")

    def test_returns_none_if_the_account_type_cannot_be_read(self):
        inputs, connected_company = self._run(read_active_account_type=MagicMock(return_value=None))
        self.assertIsNone(inputs)
        self.assertEqual(connected_company, "Apex Trader Funding")

    def test_returns_none_if_flip_mode_state_cannot_be_read(self):
        inputs, connected_company = self._run(is_flip_mode_active=MagicMock(return_value=None))
        self.assertIsNone(inputs)
        self.assertEqual(connected_company, "Apex Trader Funding")

    def test_reuses_a_provided_balance_info_instead_of_reading_tradovate_again(self):
        balance_info = {'current_balance': 53200.0, 'tier_size': 50000, 'tier': config.ACCOUNT_BALANCE_TIERS[50000]}
        with patch.object(ms, "_read_balance_and_tier") as read_balance_mock, \
             patch.object(ms, "_ensure_tradovate_connection", return_value="Apex Trader Funding"), \
             patch.object(ms.trading, "select_tradovate_account_with_reconnect", return_value=True), \
             patch.object(ms, "_select_and_verify", return_value=True), \
             patch.object(ms.tg, "read_active_account_type", return_value="LIVE"), \
             patch.object(ms.tg, "is_flip_mode_active", return_value=False), \
             patch.object(ms.status, "get_running_equity_target", return_value=None), \
             patch.object(ms.status, "get_equity_history", return_value=[]), \
             patch.object(ms.status, "get_cycle_start_date", return_value=None), \
             patch.object(ms.status, "get_cycle_starting_balance", return_value=None):
            inputs, connected_company = ms._gather_flip_mode_inputs(
                self.driver, self.web_tab, self.tv_tab, "Apex Trader Funding", "PAAPEX0001",
                "Apex Trader Funding", balance_info=balance_info,
            )
        read_balance_mock.assert_not_called()
        self.assertEqual(inputs['current_balance'], 53200.0)
        self.assertEqual(connected_company, "Apex Trader Funding")


class EvaluateFlipModeTests(unittest.TestCase):
    def setUp(self):
        self.driver = MagicMock()
        self.web_tab = "web_tab"
        self.tv_tab = "tv_tab"

    def test_returns_all_nones_if_inputs_could_not_be_gathered(self):
        with patch.object(ms, "_gather_flip_mode_inputs", return_value=(None, None)):
            decision, target, inputs, connected_company = ms.evaluate_flip_mode(
                self.driver, self.web_tab, self.tv_tab, "Apex Trader Funding", "PAAPEX0001", None
            )
        self.assertIsNone(decision)
        self.assertIsNone(target)
        self.assertIsNone(inputs)
        self.assertIsNone(connected_company)

    def test_threads_gathered_inputs_through_to_a_real_decision(self):
        # A single day of +3100 clears an initial target of 53000 but not
        # the tier's final (53500) -- matches
        # tests.test_flip_mode's "consistency holds, still under final"
        # case, confirming the real config knobs/tier values get used.
        gathered = {
            'current_balance': 53100.0,
            'account_type': 'LIVE',
            'tier_size': 50000,
            'tier_initial': 53000.0,
            'tier_final': 53500.0,
            'starting_balance': 50000,
            'running_equity_target': 53000.0,
            'in_flip_mode': False,
            'days': [{"date": "2026-08-01", "equity": 53100.0}],
            'since_date': None,
        }
        with patch.object(ms, "_gather_flip_mode_inputs", return_value=(gathered, "Apex Trader Funding")):
            decision, target, inputs, connected_company = ms.evaluate_flip_mode(
                self.driver, self.web_tab, self.tv_tab, "Apex Trader Funding", "PAAPEX0001", None
            )
        self.assertEqual(decision, 'keep_normal')
        self.assertEqual(target, 53500.0)
        self.assertIs(inputs, gathered)
        self.assertEqual(connected_company, "Apex Trader Funding")

    def test_uses_the_gathered_starting_balance_not_the_tier_size(self):
        # Deliberately chosen so the one recorded day's profit against the
        # real cycle_starting_balance (150 -- below the 200 minimum)
        # doesn't count, but WOULD wrongly count as profitable if this
        # fell back to the tier's nominal size (50000) instead --
        # confirming evaluate_flip_mode threads the gathered
        # starting_balance through, not tier_size.
        gathered = {
            'current_balance': 53250.0,
            'account_type': 'LIVE',
            'tier_size': 50000,
            'tier_initial': 53000.0,
            'tier_final': 53500.0,
            'starting_balance': 53100.0,
            'running_equity_target': 53000.0,
            'in_flip_mode': True,
            'days': [{"date": "2026-08-02", "equity": 53250.0}],
            'since_date': "2026-08-02",
        }
        with patch.object(ms, "_gather_flip_mode_inputs", return_value=(gathered, "Apex Trader Funding")), \
             patch.object(ms.config, "FLIP_MODE_MIN_PROFITABLE_DAYS", 1), \
             patch.object(ms.config, "FLIP_MODE_MIN_DAILY_PROFIT", 200):
            decision, _target, _inputs, _connected_company = ms.evaluate_flip_mode(
                self.driver, self.web_tab, self.tv_tab, "Apex Trader Funding", "PAAPEX0001", None
            )
        self.assertEqual(decision, 'keep_flip_mode')


class ReadBalanceAndTierTests(unittest.TestCase):
    """Covers _read_balance_and_tier -- the tv_tab-only balance/tier read
    evaluate_account_for_removal uses for its blown-account pre-check,
    before ever switching to TradingGenerator's tab."""

    def setUp(self):
        self.driver = MagicMock()
        self.tv_tab = "tv_tab"

    def test_reads_balance_and_picks_the_nearest_tier(self):
        with patch.object(ms, "_ensure_tradovate_connection", return_value="Apex Trader Funding"), \
             patch.object(ms.trading, "select_tradovate_account_with_reconnect", return_value=True), \
             patch.object(ms.trading, "read_account_balance", return_value=53200.0):
            info, connected_company = ms._read_balance_and_tier(
                self.driver, self.tv_tab, "Apex Trader Funding", "PAAPEX0001", None
            )
        self.assertEqual(connected_company, "Apex Trader Funding")
        self.assertEqual(info['current_balance'], 53200.0)
        self.assertEqual(info['tier_size'], 50000)
        self.assertIs(info['tier'], config.ACCOUNT_BALANCE_TIERS[50000])

    def test_returns_none_if_tradovate_connection_fails(self):
        with patch.object(ms, "_ensure_tradovate_connection", return_value=None):
            info, connected_company = ms._read_balance_and_tier(
                self.driver, self.tv_tab, "Apex Trader Funding", "PAAPEX0001", None
            )
        self.assertIsNone(info)
        self.assertIsNone(connected_company)

    def test_returns_none_if_the_balance_cannot_be_read(self):
        with patch.object(ms, "_ensure_tradovate_connection", return_value="Apex Trader Funding"), \
             patch.object(ms.trading, "select_tradovate_account_with_reconnect", return_value=True), \
             patch.object(ms.trading, "read_account_balance", return_value=None):
            info, connected_company = ms._read_balance_and_tier(
                self.driver, self.tv_tab, "Apex Trader Funding", "PAAPEX0001", None
            )
        self.assertIsNone(info)
        self.assertEqual(connected_company, "Apex Trader Funding")


class EvaluateAccountForRemovalTests(unittest.TestCase):
    """Covers evaluate_account_for_removal -- the Flip Mode replacement
    for trading.account_needs_removal used by both _refresh_open_positions
    and _open_position. The min-side (blown) check is untouched; only the
    max-side is replaced by flip_mode.evaluate()."""

    def setUp(self):
        self.driver = MagicMock()
        self.web_tab = "web_tab"
        self.tv_tab = "tv_tab"

    def test_returns_failed_when_balance_cannot_be_read(self):
        with patch.object(ms, "_read_balance_and_tier", return_value=(None, None)), \
             patch.object(ms, "_gather_flip_mode_inputs") as gather_mock, \
             patch.object(ms.status, "set_running_equity_target") as set_target_mock:
            decision, balance, tp_cap, connected_company = ms.evaluate_account_for_removal(
                self.driver, self.web_tab, self.tv_tab, "Apex Trader Funding", "PAAPEX0001", None
            )
        self.assertEqual(decision, 'failed')
        self.assertIsNone(balance)
        self.assertIsNone(tp_cap)
        self.assertIsNone(connected_company)
        gather_mock.assert_not_called()
        set_target_mock.assert_not_called()

    def test_blown_account_never_reads_flip_mode_state_at_all(self):
        # The exact bug report this guards against: #flipModeBtn's label
        # was being read (and printed) for accounts that were about to be
        # removed for being blown -- completely unrelated to Flip Mode.
        balance_info = {'current_balance': 47000.0, 'tier_size': 50000, 'tier': {'min': 47500.0}}
        with patch.object(ms, "_read_balance_and_tier",
                           return_value=(balance_info, "Apex Trader Funding")), \
             patch.object(ms, "_select_and_verify", return_value=True), \
             patch.object(ms, "_gather_flip_mode_inputs") as gather_mock, \
             patch.object(ms.flip_mode, "evaluate") as evaluate_mock, \
             patch.object(ms.status, "set_running_equity_target") as set_target_mock:
            decision, balance, tp_cap, connected_company = ms.evaluate_account_for_removal(
                self.driver, self.web_tab, self.tv_tab, "Apex Trader Funding", "PAAPEX0001", None
            )
        self.assertEqual(decision, 'blown')
        self.assertEqual(balance, 47000.0)
        self.assertIsNone(tp_cap)
        self.assertEqual(connected_company, "Apex Trader Funding")
        gather_mock.assert_not_called()
        evaluate_mock.assert_not_called()
        set_target_mock.assert_not_called()

    def test_returns_failed_if_the_portfolio_cannot_be_selected_to_remove_a_blown_account(self):
        balance_info = {'current_balance': 47000.0, 'tier_size': 50000, 'tier': {'min': 47500.0}}
        with patch.object(ms, "_read_balance_and_tier",
                           return_value=(balance_info, "Apex Trader Funding")), \
             patch.object(ms, "_select_and_verify", return_value=False):
            decision, balance, tp_cap, connected_company = ms.evaluate_account_for_removal(
                self.driver, self.web_tab, self.tv_tab, "Apex Trader Funding", "PAAPEX0001", None
            )
        self.assertEqual(decision, 'failed')
        self.assertEqual(balance, 47000.0)
        self.assertIsNone(tp_cap)
        self.assertEqual(connected_company, "Apex Trader Funding")

    def test_a_balance_exactly_at_the_min_counts_as_blown(self):
        balance_info = {'current_balance': 47500.0, 'tier_size': 50000, 'tier': {'min': 47500.0}}
        with patch.object(ms, "_read_balance_and_tier",
                           return_value=(balance_info, "Apex Trader Funding")), \
             patch.object(ms, "_select_and_verify", return_value=True):
            decision, _balance, _tp_cap, _connected_company = ms.evaluate_account_for_removal(
                self.driver, self.web_tab, self.tv_tab, "Apex Trader Funding", "PAAPEX0001", None
            )
        self.assertEqual(decision, 'blown')

    def test_not_blown_reuses_the_balance_read_and_persists_the_returned_target(self):
        # Same fixture as test_evaluate_flip_mode's "consistency holds,
        # still under final" case: balance clears the initial target
        # (53000) but not the tier's final (53500).
        balance_info = {'current_balance': 53100.0, 'tier_size': 50000, 'tier': {'min': 47500.0}}
        gathered = {
            'current_balance': 53100.0, 'tier_min': 47500.0, 'tp_cap_threshold': 53500.0,
            'account_type': 'LIVE', 'tier_size': 50000, 'tier_initial': 53000.0, 'tier_final': 53500.0,
            'starting_balance': 50000, 'running_equity_target': 53000.0, 'in_flip_mode': False,
            'days': [{"date": "2026-08-01", "equity": 53100.0}], 'since_date': None,
        }
        with patch.object(ms, "_read_balance_and_tier",
                           return_value=(balance_info, "Apex Trader Funding")), \
             patch.object(ms, "_gather_flip_mode_inputs",
                           return_value=(gathered, "Apex Trader Funding")) as gather_mock, \
             patch.object(ms.status, "set_running_equity_target") as set_target_mock:
            decision, balance, tp_cap, connected_company = ms.evaluate_account_for_removal(
                self.driver, self.web_tab, self.tv_tab, "Apex Trader Funding", "PAAPEX0001", None
            )
        self.assertEqual(decision, 'keep_normal')
        self.assertEqual(balance, 53100.0)
        self.assertEqual(tp_cap, 53500.0)
        self.assertEqual(connected_company, "Apex Trader Funding")
        set_target_mock.assert_called_once_with("Apex Trader Funding", "PAAPEX0001", 53500.0)
        # The already-fetched balance read is passed straight through,
        # never re-fetched from Tradovate a second time.
        gather_mock.assert_called_once_with(
            self.driver, self.web_tab, self.tv_tab, "Apex Trader Funding", "PAAPEX0001",
            "Apex Trader Funding", balance_info=balance_info
        )


class ActOnFlipModeDecisionTests(unittest.TestCase):
    def setUp(self):
        self.driver = MagicMock()
        self.company, self.portfolio = "Apex Trader Funding", "PAAPEX0001"

    def test_blown_removes_the_portfolio_and_marks_it_removed(self):
        with patch.object(ms.tg, "remove_portfolio") as remove_mock, \
             patch.object(ms.status, "mark_portfolio_removed") as mark_mock:
            result = ms._act_on_flip_mode_decision(self.driver, self.company, self.portfolio, 'blown')
        self.assertTrue(result)
        remove_mock.assert_called_once_with(self.driver, self.portfolio)
        mark_mock.assert_called_once()
        self.assertIn("blown", mark_mock.call_args.args[2])

    def test_qualifies_for_removal_removes_the_portfolio_and_marks_it_removed(self):
        with patch.object(ms.tg, "remove_portfolio") as remove_mock, \
             patch.object(ms.status, "mark_portfolio_removed") as mark_mock:
            result = ms._act_on_flip_mode_decision(
                self.driver, self.company, self.portfolio, 'qualifies_for_removal'
            )
        self.assertTrue(result)
        remove_mock.assert_called_once_with(self.driver, self.portfolio)
        mark_mock.assert_called_once()
        self.assertIn("Flip Mode", mark_mock.call_args.args[2])

    def test_enter_flip_mode_calls_enable_and_returns_its_result(self):
        with patch.object(ms.tg, "enable_flip_mode", return_value=True) as enable_mock, \
             patch.object(ms.config, "read_admin_code", return_value="the-code"):
            result = ms._act_on_flip_mode_decision(self.driver, self.company, self.portfolio, 'enter_flip_mode')
        self.assertTrue(result)
        # Read fresh from disk every time (see config.read_admin_code),
        # not the cached module-level constant -- so it can be rotated
        # without restarting the loop.
        enable_mock.assert_called_once_with(self.driver, "the-code")

    def test_enter_flip_mode_propagates_a_click_failure(self):
        with patch.object(ms.tg, "enable_flip_mode", return_value=False):
            result = ms._act_on_flip_mode_decision(self.driver, self.company, self.portfolio, 'enter_flip_mode')
        self.assertFalse(result)

    def test_exit_flip_mode_calls_disable_and_returns_its_result(self):
        with patch.object(ms.tg, "disable_flip_mode", return_value=True) as disable_mock, \
             patch.object(ms.config, "read_admin_code", return_value="the-code"):
            result = ms._act_on_flip_mode_decision(self.driver, self.company, self.portfolio, 'exit_flip_mode')
        self.assertTrue(result)
        disable_mock.assert_called_once_with(self.driver, "the-code")

    def test_exit_flip_mode_propagates_a_click_failure(self):
        with patch.object(ms.tg, "disable_flip_mode", return_value=False):
            result = ms._act_on_flip_mode_decision(self.driver, self.company, self.portfolio, 'exit_flip_mode')
        self.assertFalse(result)

    def test_keep_flip_mode_and_keep_normal_are_no_ops(self):
        with patch.object(ms.tg, "remove_portfolio") as remove_mock, \
             patch.object(ms.tg, "enable_flip_mode") as enable_mock, \
             patch.object(ms.tg, "disable_flip_mode") as disable_mock:
            for decision in ('keep_flip_mode', 'keep_normal'):
                result = ms._act_on_flip_mode_decision(self.driver, self.company, self.portfolio, decision)
                self.assertTrue(result)
        remove_mock.assert_not_called()
        enable_mock.assert_not_called()
        disable_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
