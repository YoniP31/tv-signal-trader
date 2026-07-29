import os
import random
import threading

from . import browser
from . import config
from . import state
from . import status
from .logging_utils import timestamped_print as print


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

    Also doubles as the app's "did the user close the browser?" check: if
    every Chrome window is gone, chromedriver's session is gone with it, so
    this hard-exits the whole process rather than leaving the CLI sitting
    uselessly at the '>' prompt forever. A plain sys.exit() wouldn't reach
    past that blocked input() call on the main thread -- it'd just end this
    background thread -- so os._exit() is used deliberately here.
    """

    def __init__(self, driver, interval_range=config.HEARTBEAT_POLL_RANGE):
        self.driver = driver
        self.interval_range = interval_range
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=max(self.interval_range) + 5)

    def _run(self):
        # A fresh random duration each cycle (rather than a fixed interval)
        # so this background heartbeat doesn't tick at a perfectly regular
        # rate for the entire life of the process.
        while not self._stop.wait(random.uniform(*self.interval_range)):
            self._poll_once()

    def _poll_once(self):
        with state.session.driver_lock:
            if not browser.is_alive(self.driver):
                print("\n[FAIL] Browser window closed - exiting.")
                status.mark_app_stopped()
                os._exit(0)

            tv_logged_in = status.check_tradingview_logged_in(self.driver)
            if tv_logged_in is not None:
                status.update(tradingview_logged_in=tv_logged_in)
