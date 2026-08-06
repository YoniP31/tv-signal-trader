from . import config
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


def sweep_liquidated_accounts(driver, web_tab, tv_tab, connected_company):
    """Once a day: for each company with a Tradovate account configured,
    compares TradingGenerator's portfolios against that company's actual
    Tradovate sub-accounts, and removes any TradingGenerator portfolio
    whose account is no longer there -- it's been liquidated on the broker
    side.

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
                trading.disconnect_tradovate(driver)
            if not trading.connect_tradovate(driver, account['username'], account['password']):
                print(f"  [WARN] Could not connect to Tradovate for '{company}' - skipping sweep for it.")
                connected_company = None
                driver.switch_to.window(web_tab)
                continue
            connected_company = company
        tradovate_accounts = trading.list_tradovate_accounts(driver)
        driver.switch_to.window(web_tab)

        if tradovate_accounts is None:
            # Couldn't actually read the account list (transient DOM/timing
            # failure) -- must not be treated as "confirmed zero accounts",
            # since that would remove every portfolio for this company as
            # if all of them had been liquidated. Skip this company's sweep
            # entirely rather than guess; it'll just be tried again on the
            # next sweep (tomorrow, or next startup).
            print(f"  [WARN] Could not read '{company}' Tradovate account list - "
                  "skipping its sweep rather than risk removing portfolios that are still fine.")
            continue

        for portfolio in tg_portfolios:
            if portfolio not in tradovate_accounts:
                print(f"  [WARN] '{company} / {portfolio}' not found in its Tradovate account "
                      "list - removing (liquidated).")
                tg.select_company(driver, company)
                tg.remove_portfolio(driver, portfolio)
                status.mark_portfolio_removed(
                    company, portfolio, 'liquidated (missing from Tradovate account list)'
                )

    print("[SWEEP] Done.")
    return connected_company


def generate_next_trade(driver, next_company, next_portfolio, unavailable):
    """Tries to generate a trade for the hinted next_company/next_portfolio
    (or, if there's no hint yet, whatever's currently selected). If that
    portfolio is locked, mismatched, or already known bad, falls back to
    trying every other configured company/portfolio pair in turn.

    `unavailable` is a set of (company, portfolio) pairs already found to be
    unusable this session (locked, persistent mismatch, etc.) -- mutated in
    place so the caller keeps skipping them on future calls too.

    Returns 'generated', 'not_found' (the generate button itself is missing
    -- a structural problem, not specific to any one portfolio), or
    'exhausted' (every known candidate is locked/unavailable).
    """
    hint = (next_company, next_portfolio) if next_company and next_portfolio else None
    if hint is None or hint not in unavailable:
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
        if (company, portfolio) in unavailable:
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
