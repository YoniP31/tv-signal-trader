# `web` / `web_multi` — the trading loop

`web` and `web_multi` are the same engine, [tv_signal_trader/multi_signal_source.py](../tv_signal_trader/multi_signal_source.py) — not two separate implementations. The only difference is `MPPC` (max positions per company): `web` forces it to 1, so combined with rule 3 below ("only one company engaged at a time") it trades one signal at a time, same as it always has. `web_multi` uses whatever's configured in `.env` (default 3), allowing several concurrent positions per company. Everything else on this page — quarantining, daily limits, crash recovery, the sweep, all of it — applies identically to both commands.

Type `web` or `web_multi` at the `>` prompt to run one. Either command first asks whether to show TradingGenerator's window just for this run (Enter keeps `config.HIDE_TRADINGGENERATOR_WINDOW`'s current default) — see the main [README](../README.md#4-the-automatic-trading-loop--run_web_loop_multidriver-).

## The concurrency rules

1. **One open position per portfolio.** A portfolio that already has a trade open won't get a second one until the first closes.
2. **At most `MPPC` open positions per company at once** (default 3, set in `.env`; forced to 1 for the `web` command regardless of this setting).
3. **Only one company may have open positions at a time.** If a signal comes in for a different company while the current one still has open positions, it waits for all of them to close first, then switches over.
4. **A Tradovate sub-account with an open position but no matching TradingGenerator portfolio at all also counts as "open" for its company**, blocking new trades there the same as any of the above until it closes — see "Daily account sweep" below.

The bot decides whether it's allowed to open a signal *before* pressing "Generate New Trade" — it always knows the next portfolio TradingGenerator wants to trade next (from the "Next Portfolio to Trade" box), and checks it against the rules above first.

**How to test:** open a trade on Portfolio A, let TradingGenerator's next signal land on Portfolio B of the *same* company, and confirm the bot opens B alongside A (2 open at once) rather than waiting. Then let a signal come in for a *different* company and confirm the bot waits (you'll see `wait_different_company` printed, checking again shortly) until A and B both close before opening there.

## No hedging

The bot won't open a trade at a company that already has an open position in the *opposite* direction — e.g. if Portfolio A is currently long, a new short signal at the same company gets reported Not Taken and the bot waits for A (and any other open position at that company) to close before trying again. Same-direction signals at the same company are unaffected (that's just the normal multi-position case above).

**How to test:** with a long position open via `web_multi`, arrange for the next signal at the same company to be a short (or vice versa) — confirm the console prints `wait_hedge_conflict`, Not Taken is reported, and the bot doesn't open the opposite-direction trade until the existing position closes.

## Multiple portfolios per signal

Sometimes TradingGenerator says one signal should be opened on *several* portfolios of the same company at once (the "Taken in the following portfolios" box on its page). The bot opens the trade on every one of those portfolios independently — each has its own balance, its own risk of hitting a limit, and its own "Trade Result" buttons on TradingGenerator, so each is opened and later reported separately, not as one bundle.

**How to test:** wait for (or trigger) a signal that lists more than one portfolio under "Taken in the following portfolios", and confirm the bot opens a trade on each one listed, then reports each one's result independently once they close.

## Account balance limits

Every account is assumed to be a $25K or $50K account (whichever is numerically closer to its current balance), each with a minimum balance (max loss) and a maximum balance (profit target). If a trade closing would leave — or a balance check finds — an account at or beyond its limit, that portfolio is removed from TradingGenerator automatically.

Since $25K/$50K "eval" and "live" accounts usually have different profit targets, the maximum is configured separately per type; the minimum is shared. See `.env`:

```
ACCOUNT_25K_MIN_BALANCE=23000
ACCOUNT_50K_MIN_BALANCE=47500
ACCOUNT_25K_MAX_BALANCE_EVAL=27000
ACCOUNT_25K_MAX_BALANCE_LIVE=27000
ACCOUNT_50K_MAX_BALANCE_EVAL=53000
ACCOUNT_50K_MAX_BALANCE_LIVE=53500
```

**How to test:** temporarily set a MAX (or MIN) very close to an account's real current balance, run `web_multi`, and confirm it removes that portfolio from TradingGenerator instead of trading it. Remember to set the values back afterward.

## Daily profit/loss limits

An optional per-account cap on today's profit and/or loss, read from Tradovate's broker-panel Account Summary tab ("Total P/L", which resets daily on Tradovate's side). Once an account's Total P/L reaches either limit, that account stops opening new trades for the rest of the day — quarantined the same way as a manual close or a rejected order — but keeps reporting/monitoring any position already open normally. Set in `.env`:

```
DAILY_PROFIT_LIMIT=500
DAILY_LOSS_LIMIT=500
```

`DAILY_LOSS_LIMIT` is a positive number (the max acceptable loss) — the account is quarantined once Total P/L drops to/below its negative. Leave either (or both) commented out (the default) to disable that side entirely.

**How to test:** set a limit just inside an account's current Total P/L (e.g. `DAILY_PROFIT_LIMIT` just below it if currently profitable, or `DAILY_LOSS_LIMIT` just below the current loss if currently negative), run `web_multi`, and confirm the console prints that the account hit its daily profit/loss limit and is quarantined, rather than opening the next signal for it.

As a bulletproofing measure on top of correctly reporting results (which is what stops TradingGenerator itself from offering a new trade there), the bot also refuses to open a *new* trade once Total P/L is already within `DAILY_PNL_CAP_BUFFER_MAX` dollars of either limit — rather than letting the take-profit/stop-loss capping below try to size an ever-thinner trade into whatever room is left, it just quarantines outright once that margin is too tight.

**How to test:** get Total P/L within `DAILY_PNL_CAP_BUFFER_MAX` dollars of a configured limit, run `web_multi`, and confirm it refuses to open the next signal there ("too little room to safely size a trade") rather than opening a heavily-capped trade.

If Total P/L can't be read at all right before opening a trade (a transient DOM/timing glitch, or the broker panel wouldn't open) while a daily limit is configured, the trade is refused outright rather than opened uncapped and unchecked — an unreadable Total P/L is not the same as "no limit configured," and treating it that way would silently trade past a limit that's supposed to be protecting the account. It's simply retried the next time that signal/portfolio comes up.

**How to test:** hard to trigger deliberately (needs a real read failure). If you suspect it happened, check the console for `[FAIL] '<company> / <portfolio>' - could not read Total P/L to check the daily profit/loss limit before trading` right before that portfolio's turn — the trade should not have been placed that cycle.

## Take-profit capping

If a trade's take-profit would push an account's balance *past* its maximum, the bot shrinks the take-profit so the account instead lands just above the maximum — by a random amount between `TP_CAP_BUFFER_MIN` and `TP_CAP_BUFFER_MAX` dollars (default $50–$200) — rather than blowing far past it. This keeps profit-target accounts from wildly overshooting once they're close to passing.

**How to test:** get an account's balance close to its maximum, then let it take a trade whose full take-profit would cross that maximum — check the console output for a line saying the TP was capped, and confirm the resulting order's TP ticks are smaller than TradingGenerator's original number.

## Daily P&L capping

Same idea as take-profit capping above, but based on today's P&L (`DAILY_PROFIT_LIMIT`/`DAILY_LOSS_LIMIT`) instead of account balance, and it can shrink *either* side of the trade: a take-profit that would push today's P&L past `DAILY_PROFIT_LIMIT` gets shrunk, and a stop-loss that would push today's P&L past (i.e. below) `-DAILY_LOSS_LIMIT` gets shrunk too — each independently, by a random amount between `DAILY_PNL_CAP_BUFFER_MIN` and `DAILY_PNL_CAP_BUFFER_MAX` dollars (default $50–$200) short of the relevant limit. Only applies to whichever side has a limit configured.

**How to test:** with `DAILY_PROFIT_LIMIT`/`DAILY_LOSS_LIMIT` set, get today's P&L close to one of them, then let a trade generate whose full TP (or SL) would cross it — check the console for a line saying the TP or SL was capped, and confirm the resulting order's ticks are smaller than TradingGenerator's original number.

## No-trade window

A second, optional time window inside the main trading session where the bot simply won't start any *new* trades — useful for a lunch break or a period you don't want to trade through. Positions already open keep running and get reported normally; only new trades are paused. Set in `.env`:

```
NO_TRADE_START_TIME=12:00
NO_TRADE_END_TIME=13:00
```

Leave both blank to disable it.

**How to test:** set the window to bracket the next few minutes, run `web_multi`, and confirm it prints that it's pausing and stops generating trades, then resumes on its own once the window passes.

## Manual close / liquidation

If a position's Take Profit *and* Stop Loss both end up cancelled and *neither* filled, something closed it outside of the bot's own control — you closed it by hand, or the account was liquidated. There's no win/loss to score for that, so the bot reports it as **Trade Not Taken** (clearing TradingGenerator's own bookkeeping rather than leaving it stuck) and quarantines that portfolio (won't trade it again) until the next trading session starts.

**How to test:** with a position open via `web_multi`, manually close it from the Tradovate panel in TradingView. Confirm the bot detects this, logs a warning, reports Trade Not Taken, and doesn't try opening a new trade on that same portfolio again until you restart the session (or the next day's session begins).

## Rejected orders

If the broker rejects an order outright (e.g. the position size requested is too large for the account), no Take Profit/Stop Loss brackets ever get created. The bot recognizes this specifically (checking the Orders table for a "Rejected" entry order) and quarantines that portfolio the same way as a manual close — until the next session.

**How to test:** hard to trigger deliberately (it depends on the broker actually rejecting something, e.g. requesting far more contracts than the account allows). If you do hit one, confirm the console shows it was recognized as a rejection (not just a generic failure) and that the portfolio gets quarantined.

## Daily account sweep

Once per calendar day (at startup if already inside the trading session, or right when a new day's session opens), the bot checks each configured company: it connects to that company's Tradovate login, lists every actual sub-account there, and compares that against the portfolios shown in TradingGenerator. Any TradingGenerator portfolio whose account isn't in the Tradovate list anymore has been liquidated, so it gets removed from TradingGenerator automatically.

If the Tradovate account list itself can't be read (a transient DOM/timing glitch — e.g. the account-selector dropdown didn't open in time), that company's sweep is skipped entirely rather than treated as "zero accounts found": an unreadable list is not the same as a confirmed-empty one, and treating it that way would remove every portfolio for that company as if all of them had been liquidated. It's simply retried on the next sweep instead.

**How to test:** hard to fully test without an actually-liquidated account. At minimum, confirm the sweep runs cleanly on startup (look for `[SWEEP] Checking for liquidated accounts...` and `[SWEEP] Done.` in the console) without errors, for every company you have a Tradovate account configured for.

The same sweep also goes the other way: any Tradovate sub-account under a company's login that *isn't* a TradingGenerator portfolio at all gets checked for an open position. If it has one, that whole company is treated as engaged — no new trades get generated there, tracked or not — until it closes, the same as a normal tracked open position (see "The concurrency rules" above). Nothing is ever reported to TradingGenerator for these (there's no matching portfolio to report against); it's purely a safety measure against accidentally opening a second, possibly hedging position on an account the bot didn't know already had one open. Checked again every loop iteration (not just once a day) so it releases the block as soon as the position actually closes, rather than sitting blocked until the next day's sweep. Since the sweep always runs on the very first loop iteration too, this covers program start as well as the once-a-day cadence.

**How to test:** manually open a position (e.g. via the `test` command's `buy`/`sell`) on a Tradovate sub-account that has no matching TradingGenerator portfolio, then run `web_multi` and confirm the console shows it being tracked ("has an open position but isn't a TradingGenerator portfolio") and that company doesn't trade until you close that position by hand, at which point the next cycle should log it as no longer holding that company back.

Selecting each company in turn during the sweep (to read its portfolio tabs) would otherwise leave TradingGenerator parked on whichever one happened to be swept last — a deterministic, not random, choice. Since generating a trade with no "next portfolio" hint yet (the very first trade of a session, or right after this sweep runs) just clicks Generate on whatever's currently selected, the sweep finishes by explicitly selecting a random company/portfolio from the current candidate list instead, so that first hint-less trade doesn't silently land on the same account every time.

## Crash recovery (startup reconciliation)

Every time `web_multi` starts, before it generates any new trade, it checks every configured company/portfolio for a position a *previous* run left behind (e.g. the bot crashed or was closed unexpectedly):

- **Still open** (a working Take Profit/Stop Loss bracket): recovered straight into the ledger, same as if the bot had just opened it itself. Its trade parameters (asset/direction/contracts/SL/TP) are read back from TradingGenerator, which keeps showing them until a result is reported.
- **Already closed, but TradingGenerator's Trade Result prompt is still waiting**: the bot works out whether it hit TP or SL from the Orders table and reports it now, exactly as if it had just happened.
- **No bracket order history at all** for that portfolio (nothing to determine a TP/SL outcome from): reported as **Trade Not Taken** rather than left stuck.
- **Closed manually/liquidated while the bot was down** (neither TP nor SL filled): reported as **Trade Not Taken** (there's no win/loss to score) and quarantined until the next session, same as a live manual close.
- **No Trade Result prompt to report through at all** — seen after a new trading day resets the prompt, or a TradingGenerator-side bug — **but the portfolio still shows in TradingGenerator's "OPEN TRADES" grid**: the outcome above is still worked out the same way, but since there's no result button to click, `report_trade_result` automatically falls back to that portfolio's own "✕ Close Trade" button in the Open Trades grid instead — this same fallback also kicks in any time the normal result button just can't be found for any reason, not only during startup recovery.
- **More than one card for the same company/portfolio in the Open Trades grid**: Tradovate only ever allows one real open position per sub-account, but TradingGenerator's own bookkeeping can end up with several stale duplicate cards for the same account (left behind by an earlier, never-cleared attempt) — the grid is always checked, even when a perfectly normal Trade Result prompt is also present. If Tradovate confirms a position really is still open, the newest card (leftmost) is the one recovered into the ledger and every older duplicate gets closed alongside it; if nothing is genuinely open, every remaining card for that account gets closed once whatever outcome there is has been reported.

While any recovered position remains open, the bot won't generate any new trade — it just keeps monitoring/reporting (the same as any other open position) until the ledger is fully drained, then resumes normally.

**How to test:** open a position via `web_multi`, then stop the bot (Ctrl+C or close the browser) while it's still open. Restart `web_multi` and confirm the console reports finding it ("Recovered N position(s) from a previous run"), keeps monitoring it until it closes, and only then starts generating new trades. To test the "closed but unreported" path, stop the bot, manually resolve the position from the Tradovate panel (let TP or SL fill), then restart and confirm it detects and reports the result on its own. The "no Trade Result prompt, but still in Open Trades" path is hardest to trigger deliberately (it needs TradingGenerator itself in that state) — if you do hit it, confirm the console shows it working out the outcome from the Orders table and clicking Close Trade rather than leaving it stuck. The duplicate-cards path can be set up directly via the `test` command's `close_all_open_trades`/`tg_status` (to inspect the card count) — see "Manual testing" in the main [README](../README.md).

## Randomized polling delays

`web_multi` (like `web`) checks on a randomized cadence rather than a fixed one, so it doesn't tick at a perfectly regular interval over a long unattended run. See the main [README](../README.md) — this behavior is shared with `web`, not specific to `web_multi`.

**How to test:** run `web_multi` with an open position and watch the console's "checking again" messages — they should land after varying delays, not a constant one.

## Where things live, if you need to dig deeper

| What | Where |
|------|-------|
| The main loop, eligibility rules, opening/refreshing positions | [tv_signal_trader/multi_signal_source.py](../tv_signal_trader/multi_signal_source.py) |
| Reading balances, placing orders, checking bracket status, Tradovate accounts | [tv_signal_trader/trading.py](../tv_signal_trader/trading.py) |
| Reading TradingGenerator's page, switching company/portfolio, reporting results | [tv_signal_trader/tradinggenerator.py](../tv_signal_trader/tradinggenerator.py) |
| All the `.env` settings mentioned above | [tv_signal_trader/config.py](../tv_signal_trader/config.py) |
| What each portfolio is doing right now | `status.json`, next to `.env` |
