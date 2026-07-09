import threading

from . import config
from . import login
from . import state
from . import status


class LoginMonitor:
    """Background thread that periodically re-checks login state so status.json
    stays current even while the app just sits idle at the '>' prompt (e.g. the
    user logs in manually mid-session, with no command to trigger a check).

    Runs only while state.session.driver_lock is free, so it never interleaves
    with a command in progress (tab-switching mid-trade would be dangerous).
    """

    def __init__(self, driver, interval=config.LOGIN_POLL_INTERVAL_SECONDS):
        self.driver = driver
        self.interval = interval
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=self.interval + 5)

    def _run(self):
        while not self._stop.wait(self.interval):
            self._poll_once()

    def _poll_once(self):
        with state.session.driver_lock:
            try:
                original_tab = self.driver.current_window_handle
                open_tabs = self.driver.window_handles
            except Exception:
                return

            fields = {}

            if state.session.tv_tab in open_tabs:
                try:
                    self.driver.switch_to.window(state.session.tv_tab)
                    tv_logged_in = status.check_tradingview_logged_in(self.driver)
                    if tv_logged_in is not None:
                        fields["tradingview_logged_in"] = tv_logged_in
                except Exception:
                    pass

            if state.session.web_tab and state.session.web_tab in open_tabs:
                try:
                    self.driver.switch_to.window(state.session.web_tab)
                    fields["tradinggenerator_logged_in"] = not login.is_login_form_present(self.driver)
                except Exception:
                    pass

            try:
                if original_tab in self.driver.window_handles:
                    self.driver.switch_to.window(original_tab)
            except Exception:
                pass

            if fields:
                status.update(**fields)
