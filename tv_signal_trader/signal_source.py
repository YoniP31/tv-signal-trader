import random

from . import config
from . import flip_mode
from . import history
from . import status
from . import trading
from . import tradinggenerator as tg
from .logging_utils import timestamped_print as print

# Circuit breaker for failures that aren't tied to a specific company/
# portfolio (missing trade parameters, order execution) -- those retry by
# just moving on to the next signal, so this bounds how many times in a row
# that can happen before something is clearly systemically wrong.
MAX_CONSECUTIVE_FAILURES = 5


def report_not_taken(driver, params):
    """Reports Trade Not Taken and, if we know which portfolio this was
    for, records it in status.json too."""
    tg.report_trade_result(driver, 'not_taken', company=params['company'], portfolio=params['portfolio'])
    if params['company'] and params['portfolio']:
        status.record_trade_result(
            params['company'], params['portfolio'],
            asset=params['asset'], direction=params['direction'],
            contracts=params['contracts'], sl_ticks=params['sl_ticks'], tp_ticks=params['tp_ticks'],
            result='not_taken',
        )


def sweep_liquidated_accounts(driver, web_tab, tv_tab, connected_company, external_open_accounts):
    """Once a day: for each company with a Tradovate account configured,
    compares TradingGenerator's portfolios against that company's actual
    Tradovate sub-accounts, and removes any TradingGenerator portfolio
    whose account is no longer there -- it's been liquidated on the broker
    side.

    Also discovers Tradovate sub-accounts that exist under a configured
    company's login but have no corresponding TradingGenerator portfolio at
    all, and checks each one for an open position. Any found are added to
    `external_open_accounts` (a set of (company, account) pairs, mutated in
    place) so the main loop treats that company as engaged and refuses to
    generate new trades there until it closes (see
    multi_signal_source.check_eligibility) -- purely a safety measure
    against accidentally opening a second, possibly hedging position on an
    account we didn't know already had one open. There's nothing to report
    for these: no matching TradingGenerator portfolio means no Trade Result
    button to click. Cleared here once closed (and again every loop
    iteration via multi_signal_source._refresh_external_open_accounts, so
    it doesn't sit blocked for a full day waiting for the next sweep).

    Returns whichever company's Tradovate login is connected once the sweep
    finishes (not necessarily the one passed in), so the caller's own
    connected_company tracking stays accurate.
    """
    print("\n[SWEEP] Checking for liquidated accounts...")
    driver.switch_to.window(web_tab)
    companies = tg.list_companies(driver)

    for company in companies:
        account = config.TRADOVATE_ACCOUNTS.get(company)
        if account is None:
            continue  # nothing to reconcile this company's portfolios against

        tg.select_company(driver, company)
        tg_portfolios = tg.list_portfolios(driver)

        driver.switch_to.window(tv_tab)
        if company != connected_company or not trading.is_tradovate_connected(driver):
            if trading.is_tradovate_connected(driver):
                trading.disconnect_tradovate(driver, company=connected_company)
            if not trading.connect_tradovate(driver, account['username'], account['password'], company=company):
                print(f"  [WARN] Could not connect to Tradovate for '{company}' - skipping sweep for it.")
                connected_company = None
                driver.switch_to.window(web_tab)
                continue
            connected_company = company
        tradovate_accounts = trading.list_tradovate_accounts(driver)

        if tradovate_accounts is None:
            # Couldn't actually read the account list (transient DOM/timing
            # failure) -- must not be treated as "confirmed zero accounts",
            # since that would remove every portfolio for this company as
            # if all of them had been liquidated. Skip this company's sweep
            # entirely rather than guess; it'll just be tried again on the
            # next sweep (tomorrow, or next startup).
            print(f"  [WARN] Could not read '{company}' Tradovate account list - "
                  "skipping its sweep rather than risk removing portfolios that are still fine.")
            driver.switch_to.window(web_tab)
            continue

        driver.switch_to.window(web_tab)
        for portfolio in tg_portfolios:
            if portfolio not in tradovate_accounts:
                print(f"  [WARN] '{company} / {portfolio}' not found in its Tradovate account "
                      "list - removing (liquidated).")
                tg.select_company(driver, company)
                tg.remove_portfolio(driver, portfolio)
                status.mark_portfolio_removed(
                    company, portfolio, 'liquidated (missing from Tradovate account list)'
                )

        for extra_account in set(tradovate_accounts) - set(tg_portfolios):
            driver.switch_to.window(tv_tab)
            if not trading.select_tradovate_account_with_reconnect(driver, company, extra_account):
                print(f"  [WARN] Could not select '{company}' Tradovate account '{extra_account}' "
                      "to check it for an open position - skipping.")
                continue
            if not trading.click_orders_tab(driver):
                print(f"  [WARN] Could not open the Orders tab for '{company} / {extra_account}' "
                      "to check it for an open position - skipping.")
                continue
            bracket = trading.find_working_bracket(driver)
            if bracket.get('tp') and bracket.get('sl'):
                if (company, extra_account) not in external_open_accounts:
                    print(f"  [WARN] '{company} / {extra_account}' has an open position but isn't a "
                          f"TradingGenerator portfolio - holding off on new trades for '{company}' "
                          "until it closes.")
                external_open_accounts.add((company, extra_account))
            else:
                print(f"  '{company} / {extra_account}' has no open position - nothing to track.")
                external_open_accounts.discard((company, extra_account))
        driver.switch_to.window(web_tab)

    # Selecting each company in turn above (to read its portfolio tabs)
    # leaves TradingGenerator parked on whichever one happened to be swept
    # last -- deterministic, not random. generate_trade() with no hint
    # (the very first trade of a session, or right after this sweep runs,
    # before a "next portfolio" hint exists yet) just clicks Generate on
    # whatever's currently selected, so without this every such trade
    # would silently land on the same account every time instead of a
    # random one.
    driver.switch_to.window(web_tab)
    candidates = tg.list_all_candidates(driver)
    if candidates:
        company, portfolio = random.choice(candidates)
        tg.select_company(driver, company)
        tg.select_portfolio(driver, portfolio)
        print(f"  Randomly selected '{company} / {portfolio}' so the next hint-less trade "
              "doesn't always land on whichever account the sweep finished on.")

    print("[SWEEP] Done.")
    return connected_company


def _print_withdrawal_check(company, account, is_withdrawal, details):
    """Narrates a real history.detect_withdrawal() call: the last recorded
    day it compared against, today's real balance and Total P/L, and the
    unexplained amount that decision actually turned on -- printed for
    *every* account checked, not just ones flagged, so a console watching
    this pass shows the full arithmetic behind a "no withdrawal" verdict
    too, not just a silent skip. Same narrate-the-pure-decision split as
    multi_signal_source._print_flip_mode_decision, and for the same
    reason: history.detect_withdrawal itself stays silent and
    hand-derived-testable.
    """
    date = details['last_recorded_date']
    last_equity = details['last_recorded_equity']
    balance = details['current_balance']
    pl = details['today_total_pl']
    pl_text = f"{pl:+.2f}" if pl is not None else "unreadable"
    print(f"  '{company} / {account}': last recorded {date} = {last_equity:.2f}, "
          f"current balance = {balance:.2f}, today's Total P/L = {pl_text}")
    if details['unexplained'] is None:
        print("    Total P/L couldn't be read - refusing to guess, treating as no withdrawal.")
        return
    print(f"    actual change = {details['actual_change']:+.2f}, "
          f"unexplained (actual change - Total P/L) = {details['unexplained']:+.2f} "
          f"(tolerance {details['tolerance']:.2f}) "
          f"-> {'WITHDRAWAL DETECTED' if is_withdrawal else 'no withdrawal'}")


def detect_second_withdrawals(driver, tv_tab, connected_company):
    """Read-only pass over every LIVE-typed account ever tracked
    (status.list_tracked_accounts, present or absent from
    TradingGenerator -- see the Flip Mode plan's Phase 3), comparing each
    one's real, current Tradovate balance and today's own Total P/L
    against its last recorded day (history.detect_withdrawal) to flag a
    balance drop trading alone can't explain -- a withdrawal a human made
    outside the bot's control.

    Meant to run once a day, alongside sweep_liquidated_accounts and
    *before* this session's own _record_daily_equity -- both need
    status.get_equity_history to still reflect the *previous* day's
    equity, not today's (which _record_daily_equity only writes at session
    end). Not yet wired into the main trading loop or into any action --
    this only detects and reports (no tg.add_portfolio, no
    #secondWithdrawalBtn click, no status.json writes at all); see
    test_commands' second_withdrawal_dry_run in cli.py for a way to run
    this on demand.

    Returns (detected, connected_company) -- `detected` is a list of dicts
    (company, account, current_balance, last_recorded_equity,
    today_total_pl), one per account flagged this pass; connected_company
    follows the same convention as every other per-company sweep here.
    """
    print("\n[WITHDRAWAL] Checking LIVE accounts for undetected withdrawals...")
    detected = []
    accounts_by_company = {}
    for company, account in status.list_tracked_accounts(account_type='LIVE'):
        accounts_by_company.setdefault(company, []).append(account)

    for company, accounts in accounts_by_company.items():
        tradovate_account = config.TRADOVATE_ACCOUNTS.get(company)
        if tradovate_account is None:
            continue  # nothing configured to connect with for this company

        driver.switch_to.window(tv_tab)
        if company != connected_company or not trading.is_tradovate_connected(driver):
            if trading.is_tradovate_connected(driver):
                trading.disconnect_tradovate(driver, company=connected_company)
            if not trading.connect_tradovate(
                driver, tradovate_account['username'], tradovate_account['password'], company=company
            ):
                print(f"  [WARN] Could not connect to Tradovate for '{company}' - skipping its "
                      "withdrawal check.")
                connected_company = None
                continue
            connected_company = company

        for account in accounts:
            days = status.get_equity_history(company, account)
            if not days:
                print(f"  '{company} / {account}': no recorded equity history yet - "
                      "nothing to compare against, skipping.")
                continue

            if not trading.select_tradovate_account_with_reconnect(driver, company, account):
                print(f"  [WARN] Could not select '{company}' Tradovate account '{account}' to "
                      "check it for a withdrawal - skipping.")
                continue
            balance = trading.read_account_balance(driver)
            if balance is None:
                print(f"  [WARN] Could not read '{company} / {account}' balance to check it for "
                      "a withdrawal - skipping.")
                continue
            today_total_pl = None
            if trading.click_account_summary_tab(driver):
                today_total_pl = trading.read_total_pl(driver)

            is_withdrawal, details = history.detect_withdrawal(days, balance, today_total_pl)
            _print_withdrawal_check(company, account, is_withdrawal, details)
            if is_withdrawal:
                detected.append({
                    'company': company,
                    'account': account,
                    'current_balance': balance,
                    'last_recorded_equity': details['last_recorded_equity'],
                    'today_total_pl': today_total_pl,
                })

    print(f"[WITHDRAWAL] Done ({len(detected)} possible withdrawal(s) detected).")
    return detected, connected_company


def act_on_second_withdrawals(driver, web_tab, tv_tab, connected_company):
    """Once a day, alongside sweep_liquidated_accounts: runs
    detect_second_withdrawals, then completes the real re-entry cycle (see
    the Flip Mode plan's Phase 3) for every account it flags -- re-adds it
    to TradingGenerator (tg.add_portfolio, always 'live' -- Second
    Withdrawal is LIVE-only by construction, since detect_second_withdrawals
    only ever looks at LIVE-typed accounts), marks #secondWithdrawalBtn
    (tg.mark_second_withdrawal -- already idempotent, a no-op if an earlier
    withdrawal on this same account marked it already), and seeds a fresh
    running_equity_target/cycle_start_date/cycle_starting_balance so
    consistency/day-count start measuring from today's post-withdrawal
    equity onward -- never from before it, which would let the withdrawal
    itself get mistaken for a trading loss (see flip_mode.
    seed_reentry_target and history.py's since_date handling).

    If the account already has a current TradingGenerator portfolio (e.g.
    a previous pass got partway through this same cycle before a crash/
    stop), re-adding is skipped and the rest of the cycle still runs --
    completing it rather than getting stuck retrying a re-add that would
    fail anyway.

    Each step that can fail leaves the account to simply be picked up
    again on the next day's sweep (detect_second_withdrawals will flag it
    again -- the underlying balance mismatch is still there until this
    actually finishes) rather than partially resetting its state.

    Returns (acted, connected_company) -- `acted` is the list of
    (company, account) pairs that completed the full cycle this pass.
    """
    detected, connected_company = detect_second_withdrawals(driver, tv_tab, connected_company)
    acted = []
    today = config.now_in_israel().date().isoformat()

    for entry in detected:
        company = entry['company']
        account = entry['account']
        current_balance = entry['current_balance']

        driver.switch_to.window(web_tab)
        if not tg.select_company(driver, company):
            print(f"  [WARN] Could not select '{company}' in TradingGenerator to re-add "
                  f"'{account}' - will retry next sweep.")
            continue

        if account in tg.list_portfolios(driver):
            print(f"  '{company} / {account}' already has a TradingGenerator portfolio - not "
                  "re-adding (picking up where an earlier, interrupted pass may have left off).")
        elif not tg.add_portfolio(driver, account, account_type='live'):
            print(f"  [WARN] Could not re-add '{company} / {account}' to TradingGenerator - "
                  "will retry next sweep.")
            continue

        if not tg.select_portfolio(driver, account):
            print(f"  [WARN] Could not select '{company} / {account}' after re-adding it - "
                  "will retry next sweep.")
            continue

        if not tg.mark_second_withdrawal(driver, config.read_admin_code()):
            print(f"  [WARN] Could not mark '{company} / {account}' for second withdrawal - "
                  "will retry next sweep.")
            continue

        tiers = config.ACCOUNT_BALANCE_TIERS
        tier_size = min(tiers, key=lambda size: abs(current_balance - size))
        tier_final_by_type = tiers[tier_size]['max_final']
        tier_final = tier_final_by_type.get('LIVE', max(tier_final_by_type.values()))
        new_target = flip_mode.seed_reentry_target(current_balance, tier_final, config.FLIP_MODE_REENTRY_BUFFER)

        status.set_running_equity_target(company, account, new_target)
        status.set_cycle_start_date(company, account, today)
        status.set_cycle_starting_balance(company, account, current_balance)

        print(f"  [WITHDRAWAL] '{company} / {account}' re-added and reset - new running equity "
              f"target {new_target:.2f}, cycle starts today ({today}) at {current_balance:.2f}.")
        acted.append((company, account))

    return acted, connected_company


def generate_next_trade(driver, next_company, next_portfolio, unavailable, open_positions=()):
    """Tries to generate a trade for the hinted next_company/next_portfolio
    (or, if there's no hint yet, whatever's currently selected). If that
    portfolio is locked, mismatched, or already known bad, falls back to
    trying every other configured company/portfolio pair in turn.

    `unavailable` is a set of (company, portfolio) pairs already found to be
    unusable this session (locked, persistent mismatch, etc.) -- mutated in
    place so the caller keeps skipping them on future calls too.

    `open_positions` (web_multi only; a dict or set keyed by (company,
    portfolio), same as multi_signal_source's ledger -- defaults to empty
    for callers that don't track concurrent positions) is checked
    alongside `unavailable` so a portfolio that already has a tracked open
    position is never (re-)targeted here. TradingGenerator itself doesn't
    know about our own ledger and will happily "generate" a signal for one
    anyway -- the caller's own eligibility check correctly declines to
    open it (reporting Not Taken), but without this, nothing stops the
    very next retry from picking that exact same already-open portfolio
    again, burning through candidates and generate attempts for a signal
    that was always going to be declined. Deliberately not folded into
    `unavailable` itself: unlike a genuinely broken/locked portfolio, this
    is a purely transient status that clears the moment the position
    closes, with no cleanup needed here since open_positions already
    reflects that live.
    """
    open_keys = open_positions.keys() if hasattr(open_positions, 'keys') else open_positions
    hint = (next_company, next_portfolio) if next_company and next_portfolio else None
    if hint is None or (hint not in unavailable and hint not in open_keys):
        outcome = tg.generate_trade(
            driver, expected_company=next_company, expected_portfolio=next_portfolio
        )
        if outcome == 'generated':
            return 'generated'
        if outcome == 'not_found':
            return 'not_found'
        bad = hint or tg.read_active_company_portfolio(driver)
        if bad[0] and bad[1]:
            print(f"  '{bad[0]} / {bad[1]}' isn't available ({outcome}) - trying other portfolios...")
            unavailable.add(bad)
            status.mark_portfolio_unavailable(bad[0], bad[1], outcome)
        else:
            print(f"  Current portfolio isn't available ({outcome}) - trying other portfolios...")

    for company, portfolio in tg.list_all_candidates(driver):
        if (company, portfolio) in unavailable or (company, portfolio) in open_keys:
            continue
        print(f"  Trying '{company} / {portfolio}'...")
        outcome = tg.generate_trade(
            driver, expected_company=company, expected_portfolio=portfolio, force=True
        )
        if outcome == 'generated':
            return 'generated'
        if outcome == 'not_found':
            return 'not_found'
        unavailable.add((company, portfolio))
        status.mark_portfolio_unavailable(company, portfolio, outcome)

    return 'exhausted'
