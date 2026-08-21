"""Pure functions deriving the day-consistency/day-count math Flip Mode
needs (see trading.py's profit-target evaluation) from an account's
persisted equity history (status.get_equity_history) -- a list of
{date, equity} dicts, oldest first.

A day's profit isn't stored directly -- it's always the delta from the
previous day's equity (or from `starting_balance` for the very first
recorded day), computed here rather than at write time, so there's a
single place responsible for what "a day's profit" means. Deliberately
takes plain lists/numbers rather than reading status.json itself, so this
stays trivial to unit test with fixture data and has no live-DOM
dependency at all.
"""


def daily_profits(days, starting_balance, since_date=None):
    """[(date, profit), ...] for every recorded day, oldest first, where
    profit is the equity delta from the previous day (or from
    `starting_balance` for the first recorded day).

    `since_date` (inclusive), if given, restricts the *returned* days to
    that date onward -- for Flip Mode's per-withdrawal-cycle tracking
    (see the Flip Mode plan), where consistency/day-count should only
    look at days since the account's current cycle started. The excluded
    earlier days are still used to compute the first included day's delta
    correctly (against its own actual previous day, not `starting_balance`
    again) -- only the output is filtered, not the running equity used to
    compute it.
    """
    profits = []
    previous_equity = starting_balance
    for day in days:
        profits.append((day["date"], day["equity"] - previous_equity))
        previous_equity = day["equity"]
    if since_date is not None:
        profits = [(date, profit) for date, profit in profits if date >= since_date]
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
