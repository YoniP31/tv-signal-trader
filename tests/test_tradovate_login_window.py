"""Covers the Tradovate login-window handling in trading.connect_tradovate:
finding the window Connect opens (_find_tradovate_login_window), waiting for
its login form to actually render and actively re-selecting the window while
doing so (_wait_for_login_fields), and the longer timeouts wired through the
whole flow.

Real failures this guards against: the form is a JS-rendered page that can
take many seconds to appear on a slow VPS, but the old code looked for the
username/password fields exactly once, right after a fixed 1-2 second pause,
and reported "login fields not found". FakeBrowser reproduces that -- the
fields only exist while the driver is on the login window, and only after a
number of polls -- so a single-shot lookup fails against it. Run with:

    python -m unittest tests.test_tradovate_login_window -v
"""

import inspect
import unittest
from types import SimpleNamespace
from unittest.mock import ANY, MagicMock, patch

from tv_signal_trader import trading


class NoSuchWindow(Exception):
    pass


class FakeElement:
    def __init__(self, displayed=True):
        self._displayed = displayed

    def is_displayed(self):
        return self._displayed


class FakeBrowser:
    """A driver with several windows. The login fields are only findable
    while the driver is switched onto `login_handle`, and only once
    `form_after_polls` earlier attempts (made while on it) have gone by --
    i.e. once the page has 'rendered'."""

    def __init__(self, handles=("tv",), urls=None, login_handle="login", current=None,
                 form_after_polls=0, fields_displayed=True, close_after_attempts=None,
                 appear_after_reads=None):
        self.handles = list(handles)
        self.urls = dict(urls or {})
        self.login_handle = login_handle
        self.current = current or self.handles[0]
        self.form_after_polls = form_after_polls
        self.fields_displayed = fields_displayed
        self.close_after_attempts = close_after_attempts
        self.appear_after_reads = appear_after_reads
        self.find_attempts = 0
        self.reads = 0
        self.switched_to = []
        self.focus_calls = 0
        self.clicked = []
        self.buttons = []
        self.title = "Tradovate"
        self.switch_to = SimpleNamespace(window=self._switch)

    @property
    def window_handles(self):
        self.reads += 1
        if self.appear_after_reads is not None and self.reads > self.appear_after_reads \
                and self.login_handle not in self.handles:
            self.handles.append(self.login_handle)
            self.urls[self.login_handle] = "https://trader.tradovate.com/"
        return list(self.handles)

    @property
    def current_window_handle(self):
        return self.current

    @property
    def current_url(self):
        return self.urls.get(self.current, "about:blank")

    def _switch(self, handle):
        if handle not in self.handles:
            raise NoSuchWindow(handle)
        self.current = handle
        self.switched_to.append(handle)

    def execute_script(self, script, *args):
        if script == "window.focus();":
            self.focus_calls += 1
        elif script == "arguments[0].click();":
            self.clicked.append(args[0])

    def find_element(self, by, value):
        if self.current != self.login_handle:
            raise Exception("no such element (wrong window)")
        self.find_attempts += 1
        if self.close_after_attempts is not None and self.find_attempts >= self.close_after_attempts:
            self.handles.remove(self.login_handle)
            raise NoSuchWindow(self.login_handle)
        if self.find_attempts <= self.form_after_polls:
            raise Exception("not rendered yet")
        return FakeElement(displayed=self.fields_displayed)

    def find_elements(self, by, value):
        return self.buttons


class _NoWaiting(unittest.TestCase):
    """Nothing here should ever really sleep."""

    def setUp(self):
        for target in (patch.object(trading.time, "sleep"), patch.object(trading, "humanize")):
            target.start()
            self.addCleanup(target.stop)


class WaitForLoginFieldsTests(_NoWaiting):
    def test_returns_the_fields_as_soon_as_the_form_is_there(self):
        browser = FakeBrowser(handles=["tv", "login"], current="login")
        state, fields = trading._wait_for_login_fields(browser, "login", timeout=5)
        self.assertEqual(state, "ready")
        self.assertEqual(len(fields), 2)

    def test_keeps_waiting_while_the_form_renders(self):
        # The exact failure: the old single-shot lookup gave up here.
        browser = FakeBrowser(handles=["tv", "login"], current="login", form_after_polls=6)
        state, _fields = trading._wait_for_login_fields(browser, "login", timeout=45)
        self.assertEqual(state, "ready")
        self.assertGreater(browser.find_attempts, 6)

    def test_times_out_when_the_form_never_appears(self):
        browser = FakeBrowser(handles=["tv", "login"], current="login", form_after_polls=10 ** 6)
        state, fields = trading._wait_for_login_fields(browser, "login", timeout=3)
        self.assertEqual(state, "timeout")
        self.assertIsNone(fields)

    def test_fields_that_exist_but_are_not_displayed_do_not_count(self):
        browser = FakeBrowser(handles=["tv", "login"], current="login", fields_displayed=False)
        state, _fields = trading._wait_for_login_fields(browser, "login", timeout=2)
        self.assertEqual(state, "timeout")

    def test_actively_reselects_the_login_window_instead_of_trusting_the_driver(self):
        # The driver is still on the TradingView window -- as if the browser
        # never switched -- and the fields are only findable on the login
        # window. It must switch there itself.
        browser = FakeBrowser(handles=["tv", "login"], current="tv")
        state, _fields = trading._wait_for_login_fields(browser, "login", timeout=5)
        self.assertEqual(state, "ready")
        self.assertIn("login", browser.switched_to)
        self.assertGreaterEqual(browser.focus_calls, 1)

    def test_reports_closed_if_the_window_is_already_gone(self):
        browser = FakeBrowser(handles=["tv"], current="tv")
        state, fields = trading._wait_for_login_fields(browser, "login", timeout=5)
        self.assertEqual(state, "closed")
        self.assertIsNone(fields)

    def test_reports_closed_if_the_window_goes_away_mid_wait(self):
        browser = FakeBrowser(handles=["tv", "login"], current="login",
                              form_after_polls=10 ** 6, close_after_attempts=3)
        state, _fields = trading._wait_for_login_fields(browser, "login", timeout=45)
        self.assertEqual(state, "closed")


class FindLoginWindowTests(_NoWaiting):
    def test_finds_a_new_window_that_is_on_tradovate(self):
        browser = FakeBrowser(handles=["tv", "login"], urls={"login": "https://trader.tradovate.com/x"})
        self.assertEqual(trading._find_tradovate_login_window(browser, {"tv"}, timeout=5), "login")

    def test_prefers_the_tradovate_window_over_an_unrelated_new_one(self):
        # An unrelated popup listed first must not be mistaken for the login
        # window -- typing credentials into it is a failed login.
        browser = FakeBrowser(
            handles=["tv", "ad", "login"],
            urls={"ad": "https://ads.example.com/x", "login": "https://trader.tradovate.com/"},
        )
        self.assertEqual(trading._find_tradovate_login_window(browser, {"tv"}, timeout=5), "login")

    def test_falls_back_to_the_first_new_window_if_none_reads_as_tradovate(self):
        # A fresh popup sits on about:blank until it navigates; don't wait
        # out the whole timeout for a URL that may never match.
        browser = FakeBrowser(handles=["tv", "weird"], urls={"weird": "about:blank"})
        self.assertEqual(trading._find_tradovate_login_window(browser, {"tv"}, timeout=30), "weird")

    def test_returns_none_when_no_new_window_ever_opens(self):
        browser = FakeBrowser(handles=["tv"])
        self.assertIsNone(trading._find_tradovate_login_window(browser, {"tv"}, timeout=3))

    def test_waits_for_a_window_that_opens_late(self):
        browser = FakeBrowser(handles=["tv"], appear_after_reads=8)
        self.assertEqual(trading._find_tradovate_login_window(browser, {"tv"}, timeout=30), "login")

    def test_ignores_windows_that_already_existed_before_connect_was_clicked(self):
        browser = FakeBrowser(handles=["tv", "tg"], urls={"tg": "https://tradovate.example/"})
        self.assertIsNone(trading._find_tradovate_login_window(browser, {"tv", "tg"}, timeout=2))


class SubmitLoginTests(_NoWaiting):
    def setUp(self):
        super().setUp()
        set_field = patch.object(trading.panel, "set_field")
        self.set_field = set_field.start()
        self.addCleanup(set_field.stop)

    def _browser(self, **kwargs):
        browser = FakeBrowser(handles=["tv", "login"], current="login", **kwargs)
        login_button = MagicMock()
        login_button.is_displayed.return_value = True
        login_button.text = "Login"
        browser.buttons = [login_button]
        browser.login_button = login_button
        return browser

    def test_fills_and_submits_once_a_slow_form_has_rendered(self):
        browser = self._browser(form_after_polls=5)
        self.assertTrue(trading._submit_tradovate_login(browser, "the-user", "the-pass", timeout=45))
        self.assertEqual([c.args[2] for c in self.set_field.call_args_list], ["the-user", "the-pass"])
        self.assertEqual(browser.clicked, [browser.login_button])

    def test_defaults_to_whichever_window_the_driver_is_on(self):
        browser = self._browser()
        self.assertTrue(trading._submit_tradovate_login(browser, "u", "p", timeout=5))

    def test_a_window_that_closed_by_itself_is_not_a_failure(self):
        # e.g. a remembered session signed straight in -- nothing to submit;
        # the caller's connection check decides whether it really worked.
        browser = FakeBrowser(handles=["tv"], current="tv")
        self.assertTrue(trading._submit_tradovate_login(browser, "u", "p", login_tab="login", timeout=5))
        self.set_field.assert_not_called()

    def test_fails_without_typing_anything_if_the_form_never_appears(self):
        browser = self._browser(form_after_polls=10 ** 6)
        self.assertFalse(trading._submit_tradovate_login(browser, "u", "p", timeout=2))
        self.set_field.assert_not_called()
        self.assertEqual(browser.clicked, [])


class ConnectTradovateTimeoutTests(_NoWaiting):
    def test_the_timeouts_were_actually_extended(self):
        self.assertGreater(trading._LOGIN_WINDOW_TIMEOUT, 10)
        self.assertGreater(trading._LOGIN_CLOSE_TIMEOUT, 15)
        self.assertGreater(trading._CONNECTED_TIMEOUT, 15)
        self.assertGreater(trading._LOGIN_FIELDS_TIMEOUT, 0)
        default = inspect.signature(trading.connect_tradovate).parameters["timeout"].default
        self.assertEqual(default, trading._CONNECTED_TIMEOUT)

    def _connect(self, login_window):
        connect_button = MagicMock()
        connect_button.is_displayed.return_value = True
        connect_button.text = "Connect"
        driver = MagicMock()
        driver.current_window_handle = "tv"
        driver.window_handles = ["tv"]
        driver.find_element.return_value = "tradovate-tile"
        driver.find_elements.return_value = [connect_button]

        mocks = SimpleNamespace(
            find_window=MagicMock(return_value=login_window),
            submit=MagicMock(return_value=True),
            wait_close=MagicMock(return_value=True),
        )
        with patch.object(trading, "is_tradovate_connected", side_effect=[False, True]), \
             patch.object(trading, "_find_trade_button", return_value="trade-button"), \
             patch.object(trading, "_ensure_demo_selected", return_value=True), \
             patch.object(trading, "_find_tradovate_login_window", mocks.find_window), \
             patch.object(trading, "_submit_tradovate_login", mocks.submit), \
             patch.object(trading, "_wait_for_tab_to_close", mocks.wait_close):
            result = trading.connect_tradovate(driver, "user", "pass")
        return result, driver, mocks

    def test_every_stage_uses_its_extended_timeout(self):
        result, _driver, mocks = self._connect("login")
        self.assertTrue(result)
        self.assertEqual(mocks.find_window.call_args.kwargs["timeout"], trading._LOGIN_WINDOW_TIMEOUT)
        mocks.wait_close.assert_called_once_with(ANY, "login", timeout=trading._LOGIN_CLOSE_TIMEOUT)

    def test_the_login_window_is_passed_explicitly_to_the_submit_step(self):
        _result, _driver, mocks = self._connect("login")
        self.assertEqual(mocks.submit.call_args.kwargs["login_tab"], "login")

    def test_the_login_window_is_selected_before_typing(self):
        _result, driver, _mocks = self._connect("login")
        driver.switch_to.window.assert_any_call("login")

    def test_a_missing_login_window_fails_and_returns_to_tradingview(self):
        result, driver, mocks = self._connect(None)
        self.assertFalse(result)
        mocks.submit.assert_not_called()
        # Probing for the window may have moved the driver -- it must end
        # back on the TradingView window.
        self.assertEqual(driver.switch_to.window.call_args.args[0], "tv")


if __name__ == "__main__":
    unittest.main()
