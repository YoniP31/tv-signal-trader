"""Pure functions deriving the day-consistency/day-count math Flip Mode
needs (see trading.py's profit-target evaluation) from an account's
persisted equity history (status.get_equity_history) -- a list of
{date, equity} dicts, oldest first.

A day's profit isn't stored directly -- it's always the delta from the
previous *included* day's equity (or from `starting_balance` for the
first included day -- see daily_profits' own docstring for what that
means once a `since_date` cycle cutoff is involved), computed here
rather than at write time, so there's a single place responsible for
what "a day's profit" means. Deliberately takes plain lists/numbers
rather than reading status.json itself, so this stays trivial to unit
test with fixture data and has no live-DOM dependency at all.
"""


def daily_profits(days, starting_balance, since_date=None):
    """[(date, profit), ...] for every day on/after `since_date` (all of
    them, oldest first, if since_date is None), where profit is the
    equity delta from the previous *included* day -- or from
    `starting_balance` for the first included day.

    `since_date` (inclusive) is Flip Mode's per-withdrawal-cycle tracking
    (see the Flip Mode plan): once an account is re-added to
    TradingGenerator after a detected withdrawal, consistency/day-count
    must measure profit from the equity right after that withdrawal, not
    from the account's original onboarding balance -- a withdrawal isn't a
    trading loss and must never be counted as one. So `starting_balance`
    here needs to be *that* cycle's starting equity (the caller's
    responsibility to pass the right one for since_date), and the day
    right at/after since_date resets against it directly rather than
    continuing the chain from whatever the last pre-cutoff day happened to
    record -- days before since_date are skipped entirely, not used to
    compute anything.
    """
    profits = []
    previous_equity = starting_balance
    baseline_pending = since_date is not None
    for day in days:
        if since_date is not None and day["date"] < since_date:
            continue
        if baseline_pending:
            previous_equity = starting_balance
            baseline_pending = False
        profits.append((day["date"], day["equity"] - previous_equity))
        previous_equity = day["equity"]
    return profits


def best_day_profit(days, starting_balance, since_date=None):
    """The single largest day's profit, or 0.0 if there's no history yet."""
    profits = daily_profits(days, starting_balance, since_date=since_date)
    if not profits:
        return 0.0
    return max(profit for _date, profit in profits)


def total_profit(days, starting_balance, since_date=None):
    """Profit-to-date -- with no since_date, this telescopes to the latest
    recorded equity minus `starting_balance`; with one, it's profit since
    that date's cutoff instead (the sum of daily_profits' filtered
    deltas). Returns 0.0 if there's no history yet."""
    if not days:
        return 0.0
    profits = daily_profits(days, starting_balance, since_date=since_date)
    return sum(profit for _date, profit in profits)


def profitable_day_count(days, starting_balance, min_profit, since_date=None):
    """How many recorded days had a profit >= min_profit."""
    profits = daily_profits(days, starting_balance, since_date=since_date)
    return sum(1 for _date, profit in profits if profit >= min_profit)
