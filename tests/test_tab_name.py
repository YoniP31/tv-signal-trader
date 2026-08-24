"""Covers tradinggenerator._tab_name -- reads a company/portfolio tab's
plain-text name span, used by select_company/select_portfolio/
list_companies/list_portfolios/remove_portfolio. Once a portfolio is
marked for Second Withdrawal, TradingGenerator prepends an extra ordinal
badge span ("2nd") to its tab before the real name -- caught live (see the
Flip Mode plan's Phase 3): the badge carries no class the CSS selector
already excludes (only inline styling), so a naive "first match" read
returned the badge's own text ("2nd") instead of the account name, which
then got treated as a real Tradovate account name downstream and failed
to select. Run with:

    python -m unittest tests.test_tab_name -v
"""

import unittest
from unittest.mock import MagicMock

from tv_signal_trader import tradinggenerator as tg


class FakeSpan:
    def __init__(self, text):
        self.text = text


def _fake_tab(*span_texts):
    """A fake tab element whose find_elements call returns one FakeSpan
    per text given, in order -- standing in for what the real CSS
    selector (span:not(.tab-x):not(.tab-type):not(.tab-delete)) would
    already have filtered down to in the browser, same simplification
    tests/test_add_company_portfolio.py's fake buttons use."""
    tab = MagicMock()
    tab.find_elements.return_value = [FakeSpan(text) for text in span_texts]
    return tab


class TabNameTests(unittest.TestCase):
    def test_a_plain_tab_with_no_badge_returns_its_only_name_span(self):
        tab = _fake_tab("PAAPEX0001")
        self.assertEqual(tg._tab_name(tab), "PAAPEX0001")

    def test_a_second_withdrawal_badge_is_not_mistaken_for_the_name(self):
        # Real DOM order confirmed live: the "2nd" badge span sits before
        # the actual name span -- both survive the .tab-x/.tab-type/
        # .tab-delete exclusion since the badge carries none of those
        # classes (only inline styling).
        tab = _fake_tab("2nd", "PAAPEX1871970000001")
        self.assertEqual(tg._tab_name(tab), "PAAPEX1871970000001")

    def test_strips_whitespace_from_the_name(self):
        tab = _fake_tab("  PAAPEX0001  ")
        self.assertEqual(tg._tab_name(tab), "PAAPEX0001")

    def test_no_matching_spans_returns_none(self):
        tab = _fake_tab()
        self.assertIsNone(tg._tab_name(tab))

    def test_an_exception_reading_the_tab_returns_none(self):
        tab = MagicMock()
        tab.find_elements.side_effect = Exception("stale element")
        self.assertIsNone(tg._tab_name(tab))


if __name__ == "__main__":
    unittest.main()
