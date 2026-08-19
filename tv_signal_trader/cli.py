import os
import signal

from . import browser
from . import config
from . import humanize
from . import logging_utils
from . import monitor
from . import multi_signal_source
from . import setup_wizard
from . import state
from . import status
from . import trading
from . import tradinggenerator as tg
from .logging_utils import timestamped_print as print


def _run_test_menu(driver, tv_tab, hide_tg_window=None):
    """The 'test' command's submenu -- ad hoc, one-off manual verification
    of individual pieces (placing an order, reporting a Trade Result,
    closing a stale Open Trades card, ...) against whatever's actually on
    the page right now, without running the full web/web_multi loop.

    Each test command prints setup instructions and waits for Enter before
    running, since the point is to give you time to actually go set up the
    scenario (select the right portfolio, open the right panel, ...) in the
    browser first.

    `hide_tg_window` (None = use config.HIDE_TRADINGGENERATOR_WINDOW) is
    asked once by the caller before entering this menu, same as for
    web/web_multi -- applies to every TG-related test command below that
    opens/reuses TradingGenerator's tab.
    """

    def _tg_tab():
        web_tab = tg.open_tab(driver, tv_tab, hide_window=hide_tg_window)
        driver.switch_to.window(web_tab)
        return web_tab

    def _active_company_portfolio():
        company, portfolio = tg.read_active_company_portfolio(driver)
        if not company or not portfolio:
            print("  [FAIL] Could not read the currently-selected company/portfolio in TradingGenerator.")
        return company, portfolio

    def _test_buy():
        driver.switch_to.window(tv_tab)
        trading.place_order(driver, tp_ticks=150, sl_ticks=150, side="buy")

    def _test_sell():
        driver.switch_to.window(tv_tab)
        trading.place_order(driver, tp_ticks=150, sl_ticks=150, side="sell")

    def _test_connect_tradovate():
        driver.switch_to.window(tv_tab)
        # Uses the sole configured account -- see signal_source._pick_tradovate_account.
        if len(config.TRADOVATE_ACCOUNTS) == 1:
            company, account = next(iter(config.TRADOVATE_ACCOUNTS.items()))
            trading.connect_tradovate(driver, account['username'], account['password'], company=company)
        else:
            print(f"  [FAIL] {len(config.TRADOVATE_ACCOUNTS)} accounts configured, "
                  "need exactly 1 for this test command.")

    def _test_disconnect_tradovate():
        driver.switch_to.window(tv_tab)
        trading.disconnect_tradovate(driver)

    def _test_report(outcome):
        _tg_tab()
        company, portfolio = _active_company_portfolio()
        if company and portfolio:
            tg.report_trade_result(driver, outcome, company=company, portfolio=portfolio)

    def _test_close_open_trade():
        _tg_tab()
        default_company, default_portfolio = tg.read_active_company_portfolio(driver)
        company = input(f"  Company [default: {default_company or '(none selected)'}]: ").strip() or default_company
        portfolio = (
            input(f"  Portfolio [default: {default_portfolio or '(none selected)'}]: ").strip()
            or default_portfolio
        )
        if not company or not portfolio:
            print("  [FAIL] No company/portfolio to close (nothing selected and none entered).")
            return
        if tg.close_open_trade_card(driver, company, portfolio):
            print(f"  [OK] Closed '{company} / {portfolio}''s newest card via the Open Trades grid.")
        else:
            print(f"  [FAIL] '{company} / {portfolio}' isn't listed in the Open Trades grid.")

    def _test_close_all_open_trades():
        _tg_tab()
        default_company, default_portfolio = tg.read_active_company_portfolio(driver)
        company = input(f"  Company [default: {default_company or '(none selected)'}]: ").strip() or default_company
        portfolio = (
            input(f"  Portfolio [default: {default_portfolio or '(none selected)'}]: ").strip()
            or default_portfolio
        )
        if not company or not portfolio:
            print("  [FAIL] No company/portfolio to close (nothing selected and none entered).")
            return
        before = len(tg.list_open_trade_cards(driver, company, portfolio))
        print(f"  '{company} / {portfolio}' currently has {before} Open Trades card(s).")
        keep_newest = input("  Keep the newest one (y/n) [default: n]: ").strip().lower() in ("y", "yes")
        closed = tg.close_open_trade_cards(driver, company, portfolio, keep_newest=keep_newest)
        print(f"  [OK] Closed {closed} card(s) for '{company} / {portfolio}'"
              f"{' (kept the newest)' if keep_newest else ''}.")

    def _test_tg_status():
        _tg_tab()
        company, portfolio = tg.read_active_company_portfolio(driver)
        print(f"  Selected: '{company} / {portfolio}'")
        print(f"  Pending Trade Result prompt: {tg.has_pending_trade_result(driver)}")
        if company and portfolio:
            card_count = len(tg.list_open_trade_cards(driver, company, portfolio))
            print(f"  Open Trades cards for this company/portfolio: {card_count}")

    test_commands = {
        "buy": (
            "Places a manual buy with 150-tick TP/SL on whatever symbol is currently loaded "
            "in the TradingView tab. Connect a Tradovate account first (see connect_tradovate).",
            _test_buy,
        ),
        "sell": (
            "Places a manual sell with 150-tick TP/SL on whatever symbol is currently loaded "
            "in the TradingView tab. Connect a Tradovate account first (see connect_tradovate).",
            _test_sell,
        ),
        "connect_tradovate": (
            "Connects the sole configured Tradovate account. Only works with exactly one "
            "account configured in .env.",
            _test_connect_tradovate,
        ),
        "disconnect_tradovate": (
            "Disconnects whichever Tradovate account is currently connected.",
            _test_disconnect_tradovate,
        ),
        "report_tp": (
            "Clicks the 'Take Profit' Trade Result button. In TradingGenerator, select the "
            "company/portfolio you want to test first, with its Trade Result prompt visible.",
            lambda: _test_report('tp'),
        ),
        "report_sl": (
            "Clicks the 'Stop Loss' Trade Result button. In TradingGenerator, select the "
            "company/portfolio you want to test first, with its Trade Result prompt visible.",
            lambda: _test_report('sl'),
        ),
        "report_not_taken": (
            "Clicks the 'Trade Not Taken' Trade Result button. In TradingGenerator, select the "
            "company/portfolio you want to test first, with its Trade Result prompt visible.",
            lambda: _test_report('not_taken'),
        ),
        "close_open_trade": (
            "Clicks a specific company/portfolio's newest '(X) Close Trade' button in "
            "TradingGenerator's OPEN TRADES grid (just the one, even if several cards exist for "
            "it -- see close_all_open_trades to clear all of them). To test that the right card "
            "gets picked, get more than one portfolio showing there first (e.g. across different "
            "accounts of the same company) -- you'll be asked which company/portfolio to target "
            "(defaults to whatever's currently selected in TradingGenerator).",
            _test_close_open_trade,
        ),
        "close_all_open_trades": (
            "Closes every card for a specific company/portfolio in TradingGenerator's OPEN "
            "TRADES grid -- useful for testing the startup-reconciliation duplicate-card cleanup. "
            "Set up more than one card for the same company/portfolio first (e.g. by not "
            "reporting a couple of trades in a row on the same portfolio), then confirm this "
            "clears them, optionally keeping the newest one.",
            _test_close_all_open_trades,
        ),
        "tg_status": (
            "Read-only -- no setup needed. Prints the currently-selected company/portfolio in "
            "TradingGenerator, whether it has a pending Trade Result prompt, and how many cards "
            "it has in the OPEN TRADES grid.",
            _test_tg_status,
        ),
    }

    while True:
        print("\nTest commands: " + ", ".join(test_commands) + ", back")
        cmd = input("test> ").strip().lower()
        if cmd in ("back", "exit", "quit", ""):
            return
        entry = test_commands.get(cmd)
        if entry is None:
            print(f"  Unknown test command '{cmd}'.")
            continue
        instructions, handler = entry
        print(f"\n  Setup: {instructions}")
        input("  Press Enter when ready to run this test (Ctrl+C to abort)... ")
        try:
            handler()
        except Exception as exc:
            print(f"  [FAIL] Test command raised: {exc!r}")
            logging_utils.log_exception(f"Test command '{cmd}' raised an exception")


# The 'add_accounts' command reads account names to bulk-create from this
# file, next to .env -- one name per line, blank lines ignored. Not
# .env-configurable itself (there's nothing to validate a plain filename
# against), just a fixed, documented location.
ACCOUNTS_TO_ADD_FILE = os.path.join(config.APP_DIR, "accounts_to_add.txt")


def _choose_company(prompt):
    print("\nCompanies:")
    for i, name in enumerate(config.PROP_FIRMS, 1):
        print(f"  {i}. {name}")
    while True:
        raw = input(f"{prompt} (1-{len(config.PROP_FIRMS)}): ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(config.PROP_FIRMS):
            return config.PROP_FIRMS[int(raw) - 1]
        print(f"  [FAIL] Enter a number from 1 to {len(config.PROP_FIRMS)}.")


def _run_add_accounts(driver, tv_tab, hide_tg_window=None):
    """The 'add_accounts' command: bulk-creates TradingGenerator portfolios
    (accounts) for one company, reading their names from
    ACCOUNTS_TO_ADD_FILE -- for setting up a batch of newly funded
    prop-firm accounts without clicking through TradingGenerator's
    '+ Portfolio' modal one at a time.

    Never creates a duplicate company or portfolio: an existing company
    tab is reused rather than re-created, and any account name already
    present as a portfolio for that company is skipped.

    Runs under humanize.fast_mode() -- this only touches TradingGenerator,
    never TradingView/Tradovate, so there's no anti-bot reason to pace
    every click/keystroke at human speed the way the actual trading paths
    deliberately do.
    """
    with humanize.fast_mode():
        web_tab = tg.open_tab(driver, tv_tab, hide_window=hide_tg_window)
        driver.switch_to.window(web_tab)
        if not tg.ensure_logged_in(driver):
            print("  [FAIL] Not logged in to TradingGenerator - log in manually and try again.")
            return

        company = _choose_company("Select a company to add accounts to")

        if company in tg.list_companies(driver):
            print(f"  '{company}' already exists in TradingGenerator - using it.")
            tg.select_company(driver, company)
        else:
            print(f"  '{company}' doesn't exist yet - creating it...")
            if not tg.add_company(driver, company):
                print(f"  [FAIL] Could not create company '{company}' - aborting.")
                return

        print(f"\nReading account names from '{ACCOUNTS_TO_ADD_FILE}'")
        print("  One account name per line; blank lines and lines starting with # are ignored.")
        if not os.path.exists(ACCOUNTS_TO_ADD_FILE):
            print(f"  [FAIL] File not found. Create '{ACCOUNTS_TO_ADD_FILE}' with the account "
                  "names (one per line) and run 'add_accounts' again. See docs/accounts_to_add.txt "
                  "for an example.")
            return
        with open(ACCOUNTS_TO_ADD_FILE, encoding="utf-8") as f:
            names = [
                line.strip() for line in f
                if line.strip() and not line.strip().startswith("#")
            ]
        if not names:
            print(f"  [FAIL] '{ACCOUNTS_TO_ADD_FILE}' is empty - nothing to add.")
            return
        print(f"  Found {len(names)} account name(s).")

        type_raw = input("  Account type for this batch - live or eval [live]: ").strip().lower()
        account_type = 'eval' if type_raw in ('e', 'eval') else 'live'

        existing_portfolios = set(tg.list_portfolios(driver))
        added = skipped = failed = 0
        for name in names:
            if name in existing_portfolios:
                print(f"  '{name}' already exists for '{company}' - skipping.")
                skipped += 1
                continue
            if tg.add_portfolio(driver, name, account_type=account_type):
                existing_portfolios.add(name)
                added += 1
            else:
                print(f"  [FAIL] Could not add '{name}'.")
                failed += 1

        print(f"\n[ADD ACCOUNTS] '{company}': added {added}, skipped {skipped} (already existed), "
              f"failed {failed}, out of {len(names)} total.")


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

    def _ask_hide_tg_window():
        # Lets the user override config.HIDE_TRADINGGENERATOR_WINDOW for
        # just this run, without editing config.py. Only takes effect if
        # TradingGenerator's window/tab isn't already open from earlier in
        # this same browser session (see tg.open_tab) -- asking again in
        # that case is harmless, just a no-op.
        default_hidden = config.HIDE_TRADINGGENERATOR_WINDOW
        choice = input(
            f"  Show TradingGenerator window this run? (y/n) [default: "
            f"{'n (hidden)' if default_hidden else 'y (visible)'}]: "
        ).strip().lower()
        if choice == "":
            return None
        return choice not in ("y", "yes")

    def _resolve_hide_tg_window():
        # The regular-user build never asks and is never shown the window,
        # full stop -- not just defaulted to hidden, so it can't be flipped
        # visible by a stray config.py edit either. Only the admin build
        # gets a say (see _ask_hide_tg_window above).
        if not config.IS_ADMIN_BUILD:
            return True
        return _ask_hide_tg_window()

    def _run_trading_loop(**kwargs):
        # The regular-user build's web/web_multi runs silently -- see
        # logging_utils.suppressed -- until there's something more
        # deliberately built to show instead of raw console output.
        if config.IS_ADMIN_BUILD:
            multi_signal_source.run_web_loop_multi(driver, **kwargs)
        else:
            with logging_utils.suppressed():
                multi_signal_source.run_web_loop_multi(driver, **kwargs)

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

        if config.IS_ADMIN_BUILD:
            print("\nCommands: 'web', 'web_multi', 'test', 'add_accounts', 'setup', 'quit'")
        else:
            print("\nCommands: 'web', 'web_multi', 'setup', 'quit'")
        while True:
            cmd = input("> ").strip().lower()
            with state.session.driver_lock:
                if cmd == "web":
                    if not setup_wizard.check_env_validity():
                        continue
                    # Same engine as 'web_multi' (see multi_signal_source.py),
                    # just capped at one open position per company -- combined
                    # with the engine's existing "only one company engaged at
                    # a time" rule, that reproduces single-position-at-a-time
                    # behavior without a separate implementation to maintain.
                    hide_tg_window = _resolve_hide_tg_window()
                    _run_trading_loop(
                        max_positions_per_company=1, command_name="web", hide_tg_window=hide_tg_window
                    )
                elif cmd == "web_multi":
                    if not setup_wizard.check_env_validity():
                        continue
                    hide_tg_window = _resolve_hide_tg_window()
                    _run_trading_loop(hide_tg_window=hide_tg_window)
                elif cmd == "test" and config.IS_ADMIN_BUILD:
                    if not setup_wizard.check_env_validity():
                        continue
                    hide_tg_window = _ask_hide_tg_window()
                    _run_test_menu(driver, tv_tab, hide_tg_window=hide_tg_window)
                elif cmd == "add_accounts" and config.IS_ADMIN_BUILD:
                    hide_tg_window = _ask_hide_tg_window()
                    _run_add_accounts(driver, tv_tab, hide_tg_window=hide_tg_window)
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
