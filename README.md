# TradingView Auto-Trader

Selenium-based bot that reads a trade signal from [TradingGenerator](https://tradinggenerator-english.tiiny.co/) (asset, direction, contract count, stop-loss/take-profit in ticks, and which prop firm/portfolio it's for), switches the TradingView chart to the matching futures symbol, connects the matching Tradovate account, and places the trade (Market order + Take Profit + Stop Loss) through TradingView's order ticket. Runs unattended across multiple companies/portfolios, a bounded daily trading session, and account balance limits.

## What the code currently does

The implementation lives in the [tv_signal_trader/](tv_signal_trader/) package, entered via [main.py](main.py):

| File | Responsibility |
|------|----------------|
| [main.py](main.py) | Entry point — `python main.py` |
| [tv_signal_trader/config.py](tv_signal_trader/config.py) | Paths, URLs, `.env`-backed settings, and other constants |
| [tv_signal_trader/setup_wizard.py](tv_signal_trader/setup_wizard.py) | First-run/`setup` command: prompts for and persists TradingGenerator credentials and (multiple, one per prop firm) Tradovate accounts |
| [tv_signal_trader/browser.py](tv_signal_trader/browser.py) | Chrome setup, stealth tweaks, and download preferences |
| [tv_signal_trader/humanize.py](tv_signal_trader/humanize.py) | Randomized pauses and human-like typing |
| [tv_signal_trader/panel.py](tv_signal_trader/panel.py) | Low-level DOM helpers for reading/filling the order-ticket panel |
| [tv_signal_trader/trading.py](tv_signal_trader/trading.py) | All TradingView-side actions: connecting/switching Tradovate accounts and sub-accounts, `place_order()`, chart switching, waiting for a trade to close, reading account balance |
| [tv_signal_trader/tradinggenerator.py](tv_signal_trader/tradinggenerator.py) | All TradingGenerator-side actions: login, company/portfolio tab switching, generating a trade, reading its parameters, reporting the result, removing a portfolio, saving a backup |
| [tv_signal_trader/signal_source.py](tv_signal_trader/signal_source.py) | `run_web_loop()` — orchestrates the two above into the automatic trading loop (`web`) |
| [tv_signal_trader/multi_signal_source.py](tv_signal_trader/multi_signal_source.py) | `run_web_loop_multi()` — the concurrent, multi-position trading loop (`web_multi`), see [docs/WEB_MULTI.md](docs/WEB_MULTI.md) |
| [tv_signal_trader/status.py](tv_signal_trader/status.py) / [state.py](tv_signal_trader/state.py) / [monitor.py](tv_signal_trader/monitor.py) | `status.json` tracking (app/loop state, per-portfolio balances and trade history) and the background login/browser-alive poller |
| [tv_signal_trader/cli.py](tv_signal_trader/cli.py) | Interactive command loop |

### 1. Browser setup

On startup ([tv_signal_trader/browser.py](tv_signal_trader/browser.py)):

- Launches Chrome via `chromedriver` with a persistent profile folder (`tv_profile` in the user's home directory, resolved with `os.path.expanduser("~")`), so you only need to log into TradingView once. No explicit driver executable is configured — [Selenium Manager](https://www.selenium.dev/documentation/selenium_manager/) (built into Selenium 4.6+) detects the installed Chrome version and downloads a matching `chromedriver` automatically on first run, caching it for later ones.
- Applies a few anti-bot-detection tweaks (custom user-agent, hides `navigator.webdriver`, fakes `navigator.plugins`/`navigator.languages`) so TradingView is less likely to flag the session as automated.
- Sets Chrome to always download automatically without prompting, into `config.APP_DIR` (next to `.env`/`status.json`) — needed for TradingGenerator's "Save Backup" download (see the trading loop below) to work headlessly instead of stalling behind a native "Save As" dialog.
- Opens the chart at `config.CHART_URL` (the default startup symbol — the `web` flow below switches it to whatever symbol the signal actually calls for).

TradingGenerator credentials are read from a local `.env` file ([tv_signal_trader/config.py](tv_signal_trader/config.py)) — see "First-run setup" below for how that gets populated.

### 2. First-run setup — [tv_signal_trader/setup_wizard.py](tv_signal_trader/setup_wizard.py)

Before opening the browser, `ensure_configured()` checks `.env` for a TradingGenerator username/password, and for at least one Tradovate account. Anything missing is prompted for right there in the terminal and written back to `.env` — no manual file editing needed. Tradovate accounts are entered per prop firm: pick one from a fixed list (`config.PROP_FIRMS`) and enter its username/password; each firm gets at most one account, and `setup` lets you add/update as many as you need. The password prompts are plain, visible `input()` rather than masked ones: `getpass` reads via the Windows console API directly rather than stdin, which mishandled pasted text and hid what was typed with no way to catch the corruption. Already-configured values are left untouched and skipped silently. Type `setup` at the `>` prompt anytime to change any of them.

A handful of settings live in `.env` but aren't part of this wizard — edit `.env` directly, or start from [docs/.env](docs/.env), a committed template listing every field with credentials blanked out:

- **Account balance limits** (`ACCOUNT_25K_MIN_BALANCE`/`ACCOUNT_50K_MIN_BALANCE`, `ACCOUNT_25K_MAX_BALANCE_EVAL`/`_LIVE`/`ACCOUNT_50K_MAX_BALANCE_EVAL`/`_LIVE`) — see step 5 below.
- **Trading session window** (`SESSION_START_TIME`/`SESSION_END_TIME`, `HH:MM` 24-hour, Israel time) — see step 6 below. Leave unset to allow trading at any time.
- **No-trade window** (`NO_TRADE_START_TIME`/`NO_TRADE_END_TIME`) and **take-profit cap buffer** (`TP_CAP_BUFFER_MIN`/`TP_CAP_BUFFER_MAX`) — used by `web_multi`, see [docs/WEB_MULTI.md](docs/WEB_MULTI.md).

### 3. Order placement — `place_order(driver, tp_ticks, sl_ticks, side, units)`

This is the core trading action. It drives TradingView's order panel (the right-hand sidebar), preferring stable selectors (`id`/`data-qa-id` attributes) where TradingView's markup actually provides them, and falling back to coordinate/DOM-text matching where it doesn't:

1. Clicks the page body and sends TradingView's built-in keyboard shortcut (`Shift+B` for buy, `Shift+S` for sell) to open the order ticket.
2. Clicks the **Market** order-type button.
3. Makes sure the "Quantity type" dropdown (`#quantity-dropdown-types`) is set to **Units** (not Contracts/Lots/etc.) before typing `units` into `#quantity-field`.
4. Expands the **Exits** section (if collapsed) to reveal the Take Profit/Stop Loss controls.
5. Turns on the **Take Profit** and **Stop Loss** toggles.
6. Makes sure each of the TP/SL bracket dropdowns (`order-ticket-take-profit-dropdown-button` / `order-ticket-stop-loss-dropdown-button`) is set to **Ticks** — since individual menu options aren't uniquely identifiable, "Ticks" is selected positionally (it's always the 2nd item in the menu) — then types `tp_ticks` / `sl_ticks` directly into the `order-ticket-take-profit-input` / `order-ticket-stop-loss-input` fields.
7. Clicks the final **Buy**/**Sell** confirm button.

All typing is done character-by-character with randomized delays (`type_humanlike`), and most steps have randomized pauses between them, to look more like a human user than a script.

### 4. The automatic trading loop — `run_web_loop(driver)`

[tv_signal_trader/signal_source.py](tv_signal_trader/signal_source.py) orchestrates [tv_signal_trader/tradinggenerator.py](tv_signal_trader/tradinggenerator.py) and [tv_signal_trader/trading.py](tv_signal_trader/trading.py) into a loop, stoppable with Ctrl+C. Most conditions that used to stop the loop no longer do — it keeps going through a locked portfolio, a cooldown, a temporarily-unusable account, or an outside-session-hours pause. It only stops for real if TradingGenerator login fails, the generate button disappears entirely, a trade's outcome genuinely can't be determined, or too many attempts in a row fail for reasons unrelated to any one portfolio:

1. Finds the already-open TradingGenerator tab, or opens one (`tradinggenerator.open_tab`), logging in automatically if the session's expired.
2. Checks the trading session window (see below) — pauses here if outside it, resuming on its own once back inside.
3. Clicks **GENERATE NEW TRADE** (`tradinggenerator.generate_trade`) for the "next trade to trade" hint from the previous iteration (company + portfolio), or whatever's currently selected if there's no hint yet:
   - If TradingGenerator's own **cooldown** (~30s "Waiting for next trade") has the button disabled, this reads the countdown and waits it out rather than giving up.
   - If it pops up its "wrong account" warning, this reads the correct company/portfolio straight off the warning, cancels, switches, and retries (up to twice).
   - If the portfolio is **locked** (daily limit reached) or still mismatched after retrying, `signal_source._generate_next_trade` falls back to trying every other configured company/portfolio in turn (`tradinggenerator.list_all_candidates`), confirming "Proceed Anyway" instead of cancelling since that fallback choice is deliberate. If every one is currently unavailable, it doesn't give up — it clears what it ruled out and polls again after a randomized delay (`config.PORTFOLIO_RETRY_RANGE`, see "Randomized timing" below), since a daily limit resetting or a portfolio being re-added can make one tradeable again.
4. Reads the TRADE PARAMETERS box — asset (e.g. `NQ`), direction (`LONG`/`SHORT`), contracts + size (e.g. `1 MINI`), stop-loss/take-profit in ticks — plus the active company/portfolio and the "Next Portfolio to Trade" hint for the following iteration (`tradinggenerator.read_trade_parameters`), retrying the read a couple of times if anything required comes back missing. If it's still missing after that, or if there's no Tradovate account configured for the resolved company, this reports **Trade Not Taken** and moves on to the next signal instead of stopping.
5. Switches to TradingView and resolves the ticker for that asset — `MINI` maps to the Micro contract (`NQ` → `MNQ`), with TradingView's `1!` continuous-contract suffix appended (e.g. `MNQ1!`) — then navigates the chart there (`trading.load_chart_for_signal`).
6. Looks up the Tradovate account configured for that company (`config.TRADOVATE_ACCOUNTS`), connecting or switching (disconnect then reconnect) if a different company's login is currently active (`trading.connect_tradovate`/`disconnect_tradovate`), then selects the matching sub-account in the broker panel's account-selector dropdown (`trading.select_tradovate_account`) — a single Tradovate login can have several sub-accounts (e.g. multiple eval accounts), and this is what actually targets the right one. A connect or sub-account-selection failure reports Trade Not Taken and moves on to another portfolio rather than stopping.
7. Checks the account balance against its limits (see below) before risking a trade on it — if it's already outside range, removes the portfolio instead of trading.
8. Calls `place_order(...)` with the parsed direction/ticks/contracts. A failure here reports **Trade Not Taken** and moves on to the next signal (capped by a consecutive-failure circuit breaker, `MAX_CONSECUTIVE_FAILURES`, since this one isn't tied to any particular portfolio).
9. Waits for the position to close and determines which side it hit (`trading.wait_for_close`) by polling the broker's Orders table on a randomized cadence (`config.POSITION_POLL_RANGE`, see "Randomized timing" below): a filled TP/SL order auto-cancels its linked sibling, so once one bracket leg shows `Status: Filled` and the other `Status: Cancelled`, that's a direct, reliable signal — no need to infer the outcome from price or P&L sign. Stops the loop without reporting a result (rather than guessing) if it times out or the bracket resolves without either leg actually filling, e.g. the position was closed manually.
10. Checks the account balance again (see below), reports the result back to TradingGenerator (`tradinggenerator.report_trade_result`), and loops back to step 2.

### 5. Account balance limits — `trading.account_needs_removal(driver)`

Checked both right before opening a trade (step 7 above) and right after one closes (step 10) — the latter is the trustworthy read, since that's the one moment the account is certain to be active with no position open. There's no way to read which size (25K/50K/etc.) an account actually is from the page, so this guesses by reading the current balance (`trading.read_account_balance`) and picking whichever nominal size in `config.ACCOUNT_BALANCE_TIERS` it's numerically closest to — safe since the real ranges are far apart (a 50K account is never anywhere near a 25K account's ~$27K ceiling). If the balance is at/beyond that tier's min or max, the account's blown past its loss limit or hit its profit target, and `tradinggenerator.remove_portfolio` clicks the portfolio tab's delete "X" and confirms the follow-up modal, pulling it out of rotation.

The tiers' min/max are placeholder defaults, overridable per-tier via `.env` (`ACCOUNT_25K_MIN_BALANCE`/`ACCOUNT_50K_MIN_BALANCE`, plus `ACCOUNT_25K_MAX_BALANCE_EVAL`/`_LIVE`/`ACCOUNT_50K_MAX_BALANCE_EVAL`/`_LIVE` — the max side is split by account type since eval/live profit targets genuinely differ; `trading.account_needs_removal` reads which type via `tradinggenerator.read_active_account_type`) — not part of the setup wizard, so edit `.env` directly.

### 6. Trading session window — `config.session_window_status()`

`SESSION_START_TIME`/`SESSION_END_TIME` in `.env` (`HH:MM`, 24-hour) bound when the loop will generate new trades, always interpreted in Israel time (`zoneinfo`, DST-aware — needs the `tzdata` package on Windows, which has no system IANA timezone database). Unset by default, so trading is allowed at any time unless both are configured.

Checked once per loop iteration, before touching TradingGenerator at all: if outside the window, the loop sleeps until it opens (today's start if it hasn't started yet, tomorrow's if today's has already closed) and resumes automatically — it never stops for the day on its own. A trade already open when the window closes still runs through `wait_for_close` normally rather than being cut off mid-position. Once per day, right as the session transitions to "ended" (not "hasn't started yet"), it also saves a TradingGenerator backup (`tradinggenerator.save_backup`, clicking "Save Backup" — see the download-preferences note under "Browser setup" above).

### 7. Status tracking — [tv_signal_trader/status.py](tv_signal_trader/status.py), [state.py](tv_signal_trader/state.py), [monitor.py](tv_signal_trader/monitor.py)

A `status.json` file (next to `.env`) works as a monitoring/CRM-style record of what the bot is doing, without needing to watch the console:

- **Run-level**: `app_running`, `tradingview_logged_in`, `tradinggenerator_logged_in`; `loop_state` (`idle`/`trading`/`waiting_for_session`/`waiting_for_portfolio`/`stopped`) with a `stop_reason` once stopped; `session_window` (today's configured start/end and current status); `last_backup_at`; `loop_started_at`; `last_error`/`last_error_at` from the last exception the loop caught.
- **Per-portfolio**, under a `"portfolios"` dict keyed `"Company / Portfolio"` (`status.update_portfolio`/`record_trade_result`/`mark_portfolio_removed`/`mark_portfolio_unavailable`/`mark_portfolio_available`): latest `balance`/`tier_size`/`tier_range`; `last_trade` plus lifetime `trades_total`/`wins_total`/`losses_total`/`not_taken_total` counters (not reset daily — TradingGenerator's own on-page counters are the source of truth for "today"); `active: false` with `removed_at`/`removed_reason` once pulled from TradingGenerator; `unavailable_reason`/`unavailable_since` while sitting in the loop's skip-set (locked, no account configured, connect failed, etc.), cleared automatically once it trades successfully again.

Refreshed on startup/shutdown, whenever `web` touches something worth recording, and by a background `LoginMonitor` thread that runs on a randomized cadence (`config.HEARTBEAT_POLL_RANGE`, see "Randomized timing" below) and does double duty:

- Re-checks TradingView's login cookie (via the `Network.getAllCookies` CDP command, so it doesn't need to switch tabs and visibly hijack the browser). TradingGenerator's login status can only be determined by reading its tab's DOM, which *would* require disruptively switching to it, so that one's only updated on-demand whenever `web` actually uses it.
- Checks whether the browser is still alive (`browser.is_alive`, a `driver.window_handles` call wrapped in try/except). If every Chrome window has been closed, chromedriver's session is gone too, so this marks `status.json` stopped and hard-exits the whole process (`os._exit(0)` — a plain `sys.exit()` from this background thread wouldn't reach past the main thread's blocked `input()` call at the `>` prompt) instead of leaving the CLI sitting uselessly forever.

### 8. Randomized timing

Every repeated/idle wait in the codebase — the background `LoginMonitor` heartbeat, waiting for an open position to close (`wait_for_close` in `web`, the equivalent poll in `web_multi`), and waiting for a locked/unavailable rotation to free up — draws a fresh random duration from a range each time, rather than sleeping a fixed interval. This is deliberate: ticking at a perfectly regular cadence for hours or days is exactly the kind of timing signature that makes automated usage easier to spot, so these are jittered instead. The ranges are hardcoded in `config.py` (`HEARTBEAT_POLL_RANGE`, `POSITION_POLL_RANGE`, `PORTFOLIO_RETRY_RANGE`) rather than `.env`-configurable, since they exist purely for this reason rather than being something to tune per deployment. Short, bounded UI-confirmation loops (waiting a few seconds for a tab or connection to confirm) are left alone — they're brief, active-action ticks, not long-running idle polling.

### 9. Interactive command loop

Running the script drops you into a `>` prompt that accepts:

| Command      | Effect                                                              |
|--------------|----------------------------------------------------------------------|
| `web`        | Runs `run_web_loop()` — the automatic trading loop (Ctrl+C to stop) |
| `web_multi`  | Runs the multi-position trading loop — see below                     |
| `buy`        | Places a manual buy with 150-tick TP/SL                              |
| `sell`       | Places a manual sell with 150-tick TP/SL                             |
| `setup`      | Re-run setup to change your TradingGenerator credentials or Tradovate accounts |
| `quit`       | Closes the browser and exits                                         |

### 10. Multi-position trading — `web_multi`

A second automatic trading command, in [tv_signal_trader/multi_signal_source.py](tv_signal_trader/multi_signal_source.py), that can hold several positions open at once (one per portfolio, up to `MAX_POSITIONS_PER_COMPANY` per company, default 3, only one company "engaged" at a time) instead of `web`'s one-trade-at-a-time flow. `web` itself is untouched by this — the two are independent, side by side. Full details, plus how to test each piece, are in **[docs/WEB_MULTI.md](docs/WEB_MULTI.md)**.

## How to run it

1. Install Python 3.9+ and Chrome.
2. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

3. Run the script:

   ```bash
   python main.py
   ```

4. On first run you'll be walked through setup in the terminal for anything missing: your TradingGenerator username/password, then at least one Tradovate account (pick a prop firm from a fixed list, enter its username/password — one account per firm). This gets saved to a local `.env` file so you're only asked once — type `setup` at the `>` prompt anytime to add more accounts or change existing values. (No chromedriver download needed — Selenium Manager fetches it automatically the first time Chrome launches.)
5. Chrome then opens to the chart — log into TradingView (and make sure the intended Tradovate/broker connection is active) in that window. The session persists in the `tv_profile` folder for future runs; TradingView itself isn't part of the `.env`/setup flow since it relies on that persistent cookie-based session, not password auto-fill.
6. Type a command at the `>` prompt (see table above).

## Building a standalone .exe

For sharing this with a few trusted people without handing them the source, [Nuitka](https://nuitka.net/) compiles the whole app (Python → C → machine code) into a single `tv-signal-trader.exe`. This is obfuscation, not real security — treat it as raising the bar for casual inspection, not as a place to store secrets. No credentials are ever compiled in: `.env` and `status.json` are read from/written next to wherever the `.exe` itself lives at runtime ([tv_signal_trader/config.py](tv_signal_trader/config.py) resolves this via `sys.argv[0]`, not `__file__` — `--onefile` self-extracts to a new temp directory on every launch, so anything anchored to `__file__` would silently reset each run).

1. `pip install -r requirements-build.txt`
2. Run [build.ps1](build.ps1) (or the `nuitka` command inside it directly). Output is `dist/tv-signal-trader.exe`. Includes `--include-package-data=tzdata`: the trading-session check needs Israel's IANA timezone data bundled in, since Windows has no system tz database and Nuitka doesn't pick up a pure-data package's files automatically.
3. Hand the recipient just that one `.exe` — they'll get the same first-run setup wizard prompting for their own TradingGenerator credentials, and Selenium Manager still fetches chromedriver on their machine automatically.
4. If Nuitka builds with MSVC (`cl.exe`) rather than MinGW64, it can't statically link the Windows C runtime, so recipients without it already installed will need the [Visual C++ Redistributable (x64)](https://aka.ms/vs/17/release/vc_redist.x64.exe) — a small, extremely common one-time install.
5. Don't commit `dist/` or the `.exe` into git — publish built binaries as [GitHub Releases](https://docs.github.com/en/repositories/releasing-projects-on-github) assets instead, so the repo itself doesn't accumulate large binary blobs. Attach a copy of [docs/.env](docs/.env) alongside it (already a committed template with credentials blanked out, listing every configurable field) so recipients who skip the setup wizard's prompts still see what keys exist — see [v0.2.0](https://github.com/YoniP31/tv-signal-trader/releases/tag/v0.2.0) for an earlier example.

## Known limitations / things to watch out for

- **UI-automation is brittle**: element lookup relies on a mix of stable IDs/`data-qa-id` attributes (where TradingView provides them) and screen coordinates/label text matching (where it doesn't). Any TradingView layout change, browser zoom level, or window-size change can break the latter.
- **The "Ticks" bracket-mode selection is positional**: since the TP/SL dropdown's menu items don't expose a stable per-option identifier, "Ticks" is selected by assuming it's always the 2nd item in the menu — if TradingView ever reorders that menu, this breaks silently.
- **Two failure modes still stop `web` outright rather than retrying**: TradingGenerator login failure, and a trade closing without either its TP or SL leg actually filling (e.g. the position was closed manually) — both are cases where guessing wrong (misreporting a result, or continuing on an unauthenticated session) is worse than stopping and needing a human to look. `web_multi` handles the manual-close case by quarantining that one portfolio instead of stopping everything (see [docs/WEB_MULTI.md](docs/WEB_MULTI.md)); TradingGenerator login failure still stops it too. Everything else — locked/cooldown/misconfigured portfolios, missing trade parameters, a failed order attempt, being outside the trading session — is retried, rotated past, or waited out instead of raised. Check `status.json`'s `stop_reason`/`last_error` after an unexpected stop.
- **Places real orders**: `place_order()` clicks the live Buy/Sell confirm button. Test against a paper/demo Tradovate connection before pointing this at a live account.

## Repository docs

- [docs/GIT_SETUP.md](docs/GIT_SETUP.md) — how to get set up with Git and the day-to-day commands (status/add/commit/push/pull) for contributing to this repo.
- [docs/WEB_MULTI.md](docs/WEB_MULTI.md) — what `web_multi` does and how to test each of its behaviors.
