"""Covers cli._run_add_accounts (the 'add_accounts' command): choosing a
company, reusing vs. creating it, reading account names from
ACCOUNTS_TO_ADD_FILE, and skipping names that already exist as portfolios.
Run with:

    python -m unittest tests.test_add_accounts_command -v
"""

import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from tv_signal_trader import cli
from tv_signal_trader import tradinggenerator as tg


class _AddAccountsTestCase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.accounts_path = os.path.join(self._tmpdir.name, "accounts_to_add.txt")
        self._patcher = patch.object(cli, "ACCOUNTS_TO_ADD_FILE", self.accounts_path)
        self._patcher.start()
        self.addCleanup(self._patcher.stop)

        self.driver = MagicMock()
        self.web_tab = MagicMock()
        self.tv_tab = MagicMock()
        self._open_tab_patcher = patch.object(tg, "open_tab", return_value=self.web_tab)
        self._open_tab_patcher.start()
        self.addCleanup(self._open_tab_patcher.stop)

    def _write_accounts(self, *names):
        with open(self.accounts_path, "w", encoding="utf-8") as f:
            f.write("\n".join(names) + "\n")


class NotLoggedInTests(_AddAccountsTestCase):
    def test_aborts_before_asking_for_a_company(self):
        with patch.object(tg, "ensure_logged_in", return_value=False), \
             patch("builtins.input") as mock_input:
            cli._run_add_accounts(self.driver, self.tv_tab)
        mock_input.assert_not_called()


class CompanySelectionTests(_AddAccountsTestCase):
    def test_reuses_an_existing_company_without_creating_one(self):
        self._write_accounts("APEX001")
        with patch.object(tg, "ensure_logged_in", return_value=True), \
             patch.object(tg, "list_companies", return_value=["TopStep"]), \
             patch.object(tg, "select_company") as select_mock, \
             patch.object(tg, "add_company") as add_company_mock, \
             patch.object(tg, "list_portfolios", return_value=[]), \
             patch.object(tg, "add_portfolio", return_value=True), \
             patch("builtins.input", side_effect=["2", "live"]):
            cli._run_add_accounts(self.driver, self.tv_tab)
        select_mock.assert_called_once_with(self.driver, "TopStep")
        add_company_mock.assert_not_called()

    def test_creates_a_new_company_when_it_does_not_exist_yet(self):
        self._write_accounts("APEX001")
        with patch.object(tg, "ensure_logged_in", return_value=True), \
             patch.object(tg, "list_companies", return_value=[]), \
             patch.object(tg, "add_company", return_value=True) as add_company_mock, \
             patch.object(tg, "list_portfolios", return_value=[]), \
             patch.object(tg, "add_portfolio", return_value=True), \
             patch("builtins.input", side_effect=["2", "live"]):
            cli._run_add_accounts(self.driver, self.tv_tab)
        add_company_mock.assert_called_once_with(self.driver, "TopStep")

    def test_aborts_if_creating_the_company_fails(self):
        with patch.object(tg, "ensure_logged_in", return_value=True), \
             patch.object(tg, "list_companies", return_value=[]), \
             patch.object(tg, "add_company", return_value=False), \
             patch.object(tg, "list_portfolios") as list_portfolios_mock, \
             patch("builtins.input", side_effect=["2"]):
            cli._run_add_accounts(self.driver, self.tv_tab)
        # Never got as far as reading the accounts file / checking
        # existing portfolios.
        list_portfolios_mock.assert_not_called()


class AccountsFileTests(_AddAccountsTestCase):
    def _run_with_company_selected(self, existing_portfolios=(), extra_inputs=()):
        with patch.object(tg, "ensure_logged_in", return_value=True), \
             patch.object(tg, "list_companies", return_value=["TopStep"]), \
             patch.object(tg, "select_company"), \
             patch.object(tg, "list_portfolios", return_value=list(existing_portfolios)), \
             patch.object(tg, "add_portfolio", return_value=True) as add_portfolio_mock, \
             patch("builtins.input", side_effect=["2", *extra_inputs]):
            cli._run_add_accounts(self.driver, self.tv_tab)
        return add_portfolio_mock

    def test_missing_file_aborts_without_prompting_for_type(self):
        with patch.object(tg, "ensure_logged_in", return_value=True), \
             patch.object(tg, "list_companies", return_value=["TopStep"]), \
             patch.object(tg, "select_company"), \
             patch.object(tg, "add_portfolio") as add_portfolio_mock, \
             patch("builtins.input", side_effect=["2"]):
            cli._run_add_accounts(self.driver, self.tv_tab)
        add_portfolio_mock.assert_not_called()

    def test_empty_file_adds_nothing(self):
        self._write_accounts()
        add_portfolio_mock = self._run_with_company_selected()
        add_portfolio_mock.assert_not_called()

    def test_blank_lines_are_ignored(self):
        with open(self.accounts_path, "w", encoding="utf-8") as f:
            f.write("APEX001\n\n\nAPEX002\n")
        add_portfolio_mock = self._run_with_company_selected(extra_inputs=["live"])
        names = [call.args[1] for call in add_portfolio_mock.call_args_list]
        self.assertEqual(names, ["APEX001", "APEX002"])

    def test_comment_lines_are_ignored(self):
        with open(self.accounts_path, "w", encoding="utf-8") as f:
            f.write("# one account name per line\nAPEX001\n#APEX999 - not a real account\nAPEX002\n")
        add_portfolio_mock = self._run_with_company_selected(extra_inputs=["live"])
        names = [call.args[1] for call in add_portfolio_mock.call_args_list]
        self.assertEqual(names, ["APEX001", "APEX002"])

    def test_a_leading_hash_after_whitespace_is_still_a_comment(self):
        with open(self.accounts_path, "w", encoding="utf-8") as f:
            f.write("  # indented comment\nAPEX001\n")
        add_portfolio_mock = self._run_with_company_selected(extra_inputs=["live"])
        names = [call.args[1] for call in add_portfolio_mock.call_args_list]
        self.assertEqual(names, ["APEX001"])

    def test_a_file_of_only_comments_is_treated_as_empty(self):
        with open(self.accounts_path, "w", encoding="utf-8") as f:
            f.write("# nothing here yet\n")
        add_portfolio_mock = self._run_with_company_selected()
        add_portfolio_mock.assert_not_called()

    def test_already_existing_names_are_skipped_not_recreated(self):
        self._write_accounts("APEX001", "APEX002")
        add_portfolio_mock = self._run_with_company_selected(
            existing_portfolios=["APEX001"], extra_inputs=["live"]
        )
        add_portfolio_mock.assert_called_once_with(self.driver, "APEX002", account_type='live')

    def test_a_name_added_earlier_in_the_batch_is_not_added_again(self):
        # Same name twice in the file -- the in-memory existing set must
        # grow as each one succeeds, not just start from list_portfolios().
        self._write_accounts("APEX001", "APEX001")
        add_portfolio_mock = self._run_with_company_selected(extra_inputs=["live"])
        self.assertEqual(add_portfolio_mock.call_count, 1)


class AccountTypePromptTests(_AddAccountsTestCase):
    def _run_with_type_input(self, type_input):
        self._write_accounts("APEX001")
        with patch.object(tg, "ensure_logged_in", return_value=True), \
             patch.object(tg, "list_companies", return_value=["TopStep"]), \
             patch.object(tg, "select_company"), \
             patch.object(tg, "list_portfolios", return_value=[]), \
             patch.object(tg, "add_portfolio", return_value=True) as add_portfolio_mock, \
             patch("builtins.input", side_effect=["2", type_input]):
            cli._run_add_accounts(self.driver, self.tv_tab)
        return add_portfolio_mock

    def test_blank_defaults_to_live(self):
        mock = self._run_with_type_input("")
        mock.assert_called_once_with(self.driver, "APEX001", account_type='live')

    def test_eval_is_recognized(self):
        mock = self._run_with_type_input("eval")
        mock.assert_called_once_with(self.driver, "APEX001", account_type='eval')

    def test_e_shorthand_is_recognized(self):
        mock = self._run_with_type_input("e")
        mock.assert_called_once_with(self.driver, "APEX001", account_type='eval')

    def test_is_case_insensitive(self):
        mock = self._run_with_type_input("EVAL")
        mock.assert_called_once_with(self.driver, "APEX001", account_type='eval')

    def test_unrecognized_input_falls_back_to_live(self):
        mock = self._run_with_type_input("bogus")
        mock.assert_called_once_with(self.driver, "APEX001", account_type='live')


if __name__ == "__main__":
    unittest.main()
