import os
import signal

from . import browser
from . import config
from . import humanize
from . import monitor
from . import multi_signal_source
from . import setup_wizard
from . import state
from . import status
from . import trading
from .logging_utils import timestamped_print as print


def main():
    setup_wizard.ensure_configured()

    driver = browser.create_driver()
    tv_tab = driver.current_window_handle
    status.mark_app_started()
    login_monitor = monitor.LoginMonitor(driver, tv_tab)

    def _handle_sigint(signum, frame):
        # Replaces Python's default Ctrl+C behavior (raising
        # KeyboardInterrupt wherever the program happens to be, then
        # relying on that unwinding cleanly through however many nested
        # try/except/finally blocks sit between there and the cleanup
        # code below) with something that doesn't depend on unwinding at
        # all: force-kill the browser directly, then hard-exit. Works
        # identically whether Ctrl+C lands at the '>' prompt or deep
        # inside a running trading loop's Selenium call, and can't race
        # with (or be aborted by) a second, impatient Ctrl+C the way
        # waiting on driver.quit()/a thread join could.
        #
        # Deliberately doesn't acquire state.session.driver_lock -- the
        # thread being interrupted may already hold it (a plain Lock isn't
        # reentrant), and there's nothing here that needs it anyway.
        #
        # force_kill runs first, before anything that could possibly raise
        # (e.g. status.mark_app_stopped()'s file write racing the
        # background monitor thread's own write) -- otherwise an exception
        # there would skip force_kill entirely, which defeats the whole
        # point of this handler. status write is best-effort only; the
        # browser actually closing is the one thing that must not be
        # skippable.
        print("\n[STOP] Ctrl+C - closing the browser and exiting now.")
        browser.force_kill(driver)
        try:
            status.mark_app_stopped()
        except Exception:
            pass
        os._exit(0)

    signal.signal(signal.SIGINT, _handle_sigint)

    try:
        print("Opening chart...")
        driver.get(config.CHART_URL)
        humanize.long_pause(5, 8)
        print("Chart loaded:", driver.title)

        tv_logged_in = status.check_tradingview_logged_in(driver)
        status.update(tradingview_logged_in=tv_logged_in)
        if tv_logged_in is False:
            print("[WARN] TradingView doesn't look logged in - sign in in the browser window.")

        login_monitor.start()

        print("\nCommands: 'web', 'web_multi', 'buy', 'sell', 'connect_tradovate'," \
        " 'disconnect_tradovate', 'setup', 'quit'")
        while True:
            cmd = input("> ").strip().lower()
            with state.session.driver_lock:
                if cmd == "web":
                    # Same engine as 'web_multi' (see multi_signal_source.py),
                    # just capped at one open position per company -- combined
                    # with the engine's existing "only one company engaged at
                    # a time" rule, that reproduces single-position-at-a-time
                    # behavior without a separate implementation to maintain.
                    multi_signal_source.run_web_loop_multi(driver, max_positions_per_company=1, command_name="web")
                elif cmd == "web_multi":
                    multi_signal_source.run_web_loop_multi(driver)
                elif cmd == "buy":
                    trading.place_order(driver, tp_ticks=150, sl_ticks=150, side="buy")
                elif cmd == "sell":
                    trading.place_order(driver, tp_ticks=150, sl_ticks=150, side="sell")
                elif cmd == "connect_tradovate":
                    # Temporary manual-test command for trading.connect_tradovate().
                    # Uses the sole configured account -- see signal_source._pick_tradovate_account.
                    if len(config.TRADOVATE_ACCOUNTS) == 1:
                        account = next(iter(config.TRADOVATE_ACCOUNTS.values()))
                        trading.connect_tradovate(driver, account['username'], account['password'])
                    else:
                        print(f"[FAIL] {len(config.TRADOVATE_ACCOUNTS)} accounts configured, "
                              "need exactly 1 for this test command.")
                elif cmd == "disconnect_tradovate":
                    # Temporary manual-test command for trading.disconnect_tradovate().
                    trading.disconnect_tradovate(driver)
                elif cmd == "setup":
                    setup_wizard.run_setup()
                elif cmd == "quit":
                    break
    finally:
        # Force-killing the browser (not driver.quit()) first, before
        # anything else in this block, matters: driver.quit() can hang on
        # a session that's already half-broken, and login_monitor.stop()'s
        # join() below is itself interruptible by an impatient second
        # Ctrl+C -- if that happened before the browser was actually told
        # to close, it'd be orphaned holding the profile lock. Doing this
        # first means the browser's already gone by the time anything else
        # here could be interrupted.
        browser.force_kill(driver)
        login_monitor.stop()
        status.mark_app_stopped()


if __name__ == "__main__":
    main()
