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
    the TradingView window/tab is gone, this closes the browser (any other
    window, e.g. TradingGenerator's hidden one -- see tradinggenerator.
    open_tab -- would otherwise be orphaned, since the user can't close a
    hidden window themselves) and hard-exits the whole process rather than
    leaving the CLI sitting uselessly at the '>' prompt forever. A plain
    sys.exit() wouldn't reach past that blocked input() call on the main
    thread -- it'd just end this background thread -- so os._exit() is used
    deliberately here; that's also *why* browser.force_kill() (not
    driver.quit()) is called explicitly right before it, rather than
    relying on cli.py's own try/finally -- os._exit() skips all normal
    Python cleanup, finally blocks included, and driver.quit() itself can
    hang on an already-half-closed session for long enough that an
    impatient second Ctrl+C aborts it before the browser's actually told
    to close.
    """

    def __init__(self, driver, tv_tab, interval_range=config.HEARTBEAT_POLL_RANGE):
        self.driver = driver
        self.tv_tab = tv_tab
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
            if not browser.is_alive(self.driver, self.tv_tab):
                print("\n[FAIL] TradingView window closed - closing the browser and exiting.")
                # force_kill first, before anything that could possibly
                # raise (status write racing this thread's own next write)
                # -- see cli.py's _handle_sigint for the same reasoning.
                browser.force_kill(self.driver)
                try:
                    status.mark_app_stopped()
                except Exception:
                    pass
                os._exit(0)

            tv_logged_in = status.check_tradingview_logged_in(self.driver)
            if tv_logged_in is not None:
                status.update(tradingview_logged_in=tv_logged_in)
