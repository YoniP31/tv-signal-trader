import os
import signal
import time

from . import browser
from . import config
from . import humanize
from . import logging_utils
from . import monitor
from . import multi_signal_source
from . import setup_wizard
from . import signal_source
from . import state
from . import status
from . import trading
from . import tradinggenerator as tg
from .logging_utils import timestamped_print as print

# Auto-restart safety net for the trading loop (see _run_trading_loop_with_
# auto_restart below): a run that stops again within this many seconds of
# starting counts as "rapid" -- e.g. bad TradingGenerator credentials or a
# dead site would otherwise make every relaunch fail immediately, retrying
# forever and hammering both sites with fresh Chrome launches. After this
# many rapid stops in a row, auto-restart gives up and falls back to the
# '>' prompt instead of continuing to spin.
_RAPID_RESTART_WINDOW_SECONDS = 120
_MAX_RAPID_RESTARTS = 3


def _next_restart_streak(elapsed_seconds, streak):
    """Given how long the trading loop just ran before stopping, and the
    current streak of consecutive rapid (short-lived) restarts, returns the
    streak's new value -- incremented if this run ended almost immediately
    (suggesting a relaunch won't fix it), reset to 0 if it ran for a while
    first (a stop after a long, otherwise-healthy run isn't evidence of a
    persistent problem, even if it's the same stop_reason as before)."""
    if elapsed_seconds < _RAPID_RESTART_WINDOW_SECONDS:
        return streak + 1
    return 0


def _run_test_menu(driver, tv_tab, hide_tg_window=None, relaunch_browser=None):
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

    `relaunch_browser` (main()'s own _relaunch_browser, passed in since it
    closes over main()'s driver/tv_tab/login_monitor locals that this
    module-level function has no access to) backs the 'restart_browser'
    test command below -- None disables that one command, e.g. if this
    function is ever called from somewhere without a relaunch to offer.
    """

    def _tg_tab():
        web_tab = tg.open_tab(driver, tv_tab, hide_window=hide_tg_window)
        driver.switch_to.window(web_tab)
        if not tg.ensure_logged_in(driver):
            print("  [WARN] Not logged in to TradingGenerator - log in manually before testing.")
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

    def _test_flip_mode_status():
        _tg_tab()
        active = tg.is_flip_mode_active(driver)
        print(f"  is_flip_mode_active() -> {active}")

    def _test_enable_flip_mode():
        _tg_tab()
        tg.enable_flip_mode(driver, config.read_admin_code())

    def _test_disable_flip_mode():
        _tg_tab()
        tg.disable_flip_mode(driver, config.read_admin_code())

    def _test_second_withdrawal_status():
        _tg_tab()
        marked = tg.is_second_withdrawal_marked(driver)
        print(f"  is_second_withdrawal_marked() -> {marked}")

    def _test_mark_second_withdrawal():
        _tg_tab()
        tg.mark_second_withdrawal(driver, config.read_admin_code())

    def _test_flip_mode_dry_run():
        web_tab = _tg_tab()
        default_company, default_portfolio = tg.read_active_company_portfolio(driver)
        company = input(f"  Company [default: {default_company or '(none selected)'}]: ").strip() or default_company
        portfolio = (
            input(f"  Portfolio [default: {default_portfolio or '(none selected)'}]: ").strip()
            or default_portfolio
        )
        if not company or not portfolio:
            print("  [FAIL] No company/portfolio to evaluate (nothing selected and none entered).")
            return
        decision, new_target, inputs, _connected_company = multi_signal_source.evaluate_flip_mode(
            driver, web_tab, tv_tab, company, portfolio, None
        )
        if decision is None:
            print("  [FAIL] Could not gather everything needed to evaluate this account - see warnings above.")
            return
        print(f"\n  --- DRY RUN for '{company} / {portfolio}' -- no action taken ---")
        print(f"  Account type: {inputs['account_type']}, tier size: {inputs['tier_size']}")
        print(f"  Balance: {inputs['current_balance']:.2f}")
        print(f"  Running equity target: {inputs['running_equity_target']:.2f} "
              f"(tier initial: {inputs['tier_initial']:.2f}, tier final: {inputs['tier_final']:.2f})")
        print(f"  Currently in Flip Mode: {inputs['in_flip_mode']}")
        print(f"  Cycle starting balance: {inputs['starting_balance']:.2f} "
              f"{'(this cycle -- a past withdrawal was detected)' if inputs['since_date'] else '(the tier size -- still on its first cycle)'}")
        print(f"  Cycle start date: {inputs['since_date'] or '(none set - using the whole history)'}")
        print(f"  Recorded days: {len(inputs['days'])}")
        print(f"  Decision: {decision}")
        print(f"  New running equity target (not saved): {new_target:.2f}")

    def _test_second_withdrawal_dry_run():
        driver.switch_to.window(tv_tab)
        detected, _connected_company = signal_source.detect_second_withdrawals(driver, tv_tab, None)
        if not detected:
            print("\n  No undetected withdrawals found among tracked LIVE accounts.")

    def _test_second_withdrawal_act():
        web_tab = _tg_tab()
        driver.switch_to.window(tv_tab)
        acted, _connected_company = signal_source.act_on_second_withdrawals(driver, web_tab, tv_tab, None)
        if not acted:
            print("\n  No undetected withdrawals found among tracked LIVE accounts - nothing to act on.")
        else:
            print(f"\n  Completed the re-entry cycle for: {', '.join(f'{c} / {a}' for c, a in acted)}")

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
        "flip_mode_status": (
            "Read-only -- in TradingGenerator, select the company/portfolio you want to check "
            "first. Prints #flipModeBtn's raw label and the on/off reading "
            "tg.is_flip_mode_active() derives from it -- confirmed live both ways ('Enable Flip "
            "Mode' off, 'FLIP MODE ON — click to turn off' on).",
            _test_flip_mode_status,
        ),
        "enable_flip_mode": (
            "In TradingGenerator, select the company/portfolio you want to test first. Requires "
            "TRADINGGENERATOR_ADMIN_CODE set in .env. Clicks 'Enable Flip Mode' and enters the "
            "admin code into the native browser prompt that appears -- no-ops if it already "
            "reads as enabled, refuses to click at all if its state can't be read (a single "
            "toggle button -- guessing wrong risks flipping it the wrong way).",
            _test_enable_flip_mode,
        ),
        "disable_flip_mode": (
            "Same as enable_flip_mode, in reverse -- no-ops if it already reads as disabled.",
            _test_disable_flip_mode,
        ),
        "second_withdrawal_status": (
            "Read-only -- in TradingGenerator, select the company/portfolio you want to check "
            "first. Prints #secondWithdrawalBtn's raw label and the marked/unmarked reading "
            "tg.is_second_withdrawal_marked() derives from it -- confirmed live both ways ('Mark "
            "as Second Withdrawal' unmarked, 'Second Withdrawal — click to cancel' marked).",
            _test_second_withdrawal_status,
        ),
        "mark_second_withdrawal": (
            "In TradingGenerator, select the (LIVE) company/portfolio you want to test first. "
            "Requires TRADINGGENERATOR_ADMIN_CODE set in .env. Clicks 'Mark as Second Withdrawal' "
            "and enters the admin code into the native browser prompt -- no-ops if it already "
            "reads as marked, refuses to click at all if its state can't be read (a single toggle "
            "button, same as Flip Mode -- clicking it while already marked cancels it back off).",
            _test_mark_second_withdrawal,
        ),
        "flip_mode_dry_run": (
            "Read-only -- no button clicks, no remove_portfolio, no status.json writes. Reads a "
            "real account's live balance/type/Flip-Mode-state plus its persisted equity history, "
            "runs it through the Flip Mode decision logic, and prints what it *would* do -- "
            "defaults to whatever's currently selected in TradingGenerator, or asks for a "
            "company/portfolio.",
            _test_flip_mode_dry_run,
        ),
        "second_withdrawal_dry_run": (
            "Read-only -- no button clicks, no add_portfolio, no status.json writes. Connects to "
            "every configured company's Tradovate login in turn and checks every LIVE-typed "
            "account ever tracked (present or absent from TradingGenerator) for a balance drop "
            "today's own trading P/L can't explain. No setup needed beyond having recorded at "
            "least one prior day's equity for a LIVE account (see the daily equity recording at "
            "session end) -- nothing to select first.",
            _test_second_withdrawal_dry_run,
        ),
        "second_withdrawal_act": (
            "MUTATING -- re-adds every LIVE account second_withdrawal_dry_run would flag as a "
            "portfolio (account_type 'live'), clicks 'Mark as Second Withdrawal' (requires "
            "TRADINGGENERATOR_ADMIN_CODE in .env), and resets its running equity target/cycle "
            "start date/cycle starting balance in status.json. Same detection as "
            "second_withdrawal_dry_run -- run that first to see what this would act on before "
            "running this for real.",
            _test_second_withdrawal_act,
        ),
    }
    if relaunch_browser is not None:
        test_commands["restart_browser"] = (
            "No setup needed. Force-kills the current browser and brings up a completely fresh "
            "Chrome + tab + TradingView/TradingGenerator login -- the same full relaunch "
            "'web'/'web_multi' now perform automatically after a crash or stop. Ends this test "
            "session afterward (this menu's driver/tab handles go stale once it runs) -- run "
            "'test' again against the new browser to keep testing.",
            relaunch_browser,
        )

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
        if cmd == "restart_browser":
            # driver/tv_tab above are now stale (main() has already moved on
            # to new ones) -- every other command in this menu closes over
            # them directly, so continuing would silently operate on a dead
            # browser session.
            return


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

    def _relaunch_browser():
        # Full relaunch: force-kill the current browser and bring up a
        # completely fresh Chrome + tab + login, in place, so
        # _run_trading_loop_with_auto_restart can just call the trading
        # loop again afterward as if the app had just started. Reuses the
        # same persistent profile dir as the original launch (see
        # browser.build_options), so TradingView/Tradovate's login cookies
        # survive the kill -- this is what makes an unattended relaunch
        # actually recover on its own instead of just reopening a
        # logged-out browser.
        nonlocal driver, tv_tab, login_monitor
        print("\n[RESTART] Relaunching the browser...")
        # The monitor polls under this same driver_lock (see
        # monitor.LoginMonitor._poll_once) and, for any run longer than one
        # HEARTBEAT_POLL_RANGE cycle, is essentially guaranteed to already
        # be sitting blocked trying to acquire it by the time a trading run
        # stops -- so stop()'s join() would deadlock forever against this
        # thread's own hold on the lock unless it's released first.
        # Reacquired immediately after, before touching the driver at all,
        # so the rest of this function (and the REPL's own 'with' block
        # this was called from) keeps the same locked-for-the-whole-command
        # invariant as everywhere else. Stopping the monitor fully before
        # force_kill also matters on its own: otherwise its poll could
        # notice the TradingView tab gone mid-kill and hard-exit the whole
        # process itself, which would defeat the relaunch entirely.
        state.session.driver_lock.release()
        try:
            login_monitor.stop()
        finally:
            state.session.driver_lock.acquire()

        browser.force_kill(driver)
        driver = browser.create_driver()
        tv_tab = driver.current_window_handle

        print("Opening chart...")
        driver.get(config.CHART_URL)
        humanize.long_pause(5, 8)
        print("Chart loaded:", driver.title)

        tv_logged_in = status.check_tradingview_logged_in(driver)
        status.update(tradingview_logged_in=tv_logged_in)
        if tv_logged_in is False:
            print("[WARN] TradingView doesn't look logged in - sign in in the browser window.")

        login_monitor = monitor.LoginMonitor(driver, tv_tab)
        login_monitor.start()
        print("[RESTART] Browser relaunched [OK]")

    def _run_trading_loop_with_auto_restart(**kwargs):
        # web/web_multi only ever stop on their own for a reason worth
        # recovering from automatically -- signal_source.MAX_CONSECUTIVE_
        # FAILURES, a TradingGenerator login failure, or a genuine
        # unhandled exception (run_web_loop_multi catches those itself and
        # returns rather than propagating; see its own try/except). Ctrl+C
        # is handled entirely separately, by _handle_sigint's hard
        # os._exit -- it never reaches here at all. So any return from
        # _run_trading_loop below really does mean "stopped and needs a
        # fresh browser", unattended, for as long as it keeps happening --
        # bounded only by the rapid-restart safety net above.
        restart_streak = 0
        while True:
            started_at = time.monotonic()
            _run_trading_loop(**kwargs)
            restart_streak = _next_restart_streak(time.monotonic() - started_at, restart_streak)
            if restart_streak >= _MAX_RAPID_RESTARTS:
                print(
                    f"\n[FAIL] Trading loop stopped {restart_streak} times in a row, each within "
                    f"{_RAPID_RESTART_WINDOW_SECONDS}s of starting - giving up on auto-restart. "
                    "Check status.json / the logs, fix whatever's wrong, then restart manually."
                )
                return
            print("\n[RESTART] Trading loop stopped - relaunching and resuming automatically...")
            _relaunch_browser()

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
                    _run_trading_loop_with_auto_restart(
                        max_positions_per_company=1, command_name="web", hide_tg_window=hide_tg_window
                    )
                elif cmd == "web_multi":
                    if not setup_wizard.check_env_validity():
                        continue
                    hide_tg_window = _resolve_hide_tg_window()
                    _run_trading_loop_with_auto_restart(hide_tg_window=hide_tg_window)
                elif cmd == "test" and config.IS_ADMIN_BUILD:
                    if not setup_wizard.check_env_validity():
                        continue
                    hide_tg_window = _ask_hide_tg_window()
                    _run_test_menu(driver, tv_tab, hide_tg_window=hide_tg_window, relaunch_browser=_relaunch_browser)
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
