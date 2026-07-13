import threading

from . import config
from . import state
from . import status


class LoginMonitor:
    """Background thread that periodically re-checks TradingView login state
    so status.json stays current even while the app just sits idle at the
    '>' prompt (e.g. the user logs in manually mid-session, with no command
    to trigger a check).

    Only checks TradingView, via a cookie read that doesn't require
    switching tabs (see status.check_tradingview_logged_in) — there's no
    equivalent non-disruptive check for TradingGenerator, since that one can
    only be determined by reading its tab's rendered DOM, which would mean
    visibly switching the browser to that tab every poll cycle. Its status
    is instead only updated on-demand, whenever the 'web' command actually
    uses it.

    Runs only while state.session.driver_lock is free, so it never interleaves
    with a command in progress.
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
            tv_logged_in = status.check_tradingview_logged_in(self.driver)
            if tv_logged_in is not None:
                status.update(tradingview_logged_in=tv_logged_in)
