# TradingView Auto-Trader

Selenium-based bot that reads a trade signal from [TradingGenerator](https://tradinggenerator-english.tiiny.co/) (asset, direction, contract count, stop-loss/take-profit in ticks), switches the TradingView chart to the matching futures symbol, and places the trade (Market order + Take Profit + Stop Loss) through TradingView's order ticket.

## What the code currently does

The implementation lives in the [tv_signal_trader/](tv_signal_trader/) package, entered via [main.py](main.py):

| File | Responsibility |
|------|----------------|
| [main.py](main.py) | Entry point — `python main.py` |
| [tv_signal_trader/config.py](tv_signal_trader/config.py) | Paths, URLs, `.env`-backed settings, and other constants |
| [tv_signal_trader/setup_wizard.py](tv_signal_trader/setup_wizard.py) | First-run/`setup` command: prompts for and persists chromedriver path + TradingGenerator credentials |
| [tv_signal_trader/browser.py](tv_signal_trader/browser.py) | Chrome/chromedriver setup and stealth tweaks |
| [tv_signal_trader/humanize.py](tv_signal_trader/humanize.py) | Randomized pauses and human-like typing |
| [tv_signal_trader/panel.py](tv_signal_trader/panel.py) | Low-level DOM helpers for reading/filling the order-ticket panel |
| [tv_signal_trader/trading.py](tv_signal_trader/trading.py) | `place_order()` — the core trading action |
| [tv_signal_trader/signal_source.py](tv_signal_trader/signal_source.py) | `trade_from_website()` — reads a TradingGenerator signal and triggers a trade |
| [tv_signal_trader/login.py](tv_signal_trader/login.py) | TradingGenerator auto-login fallback |
| [tv_signal_trader/status.py](tv_signal_trader/status.py) / [state.py](tv_signal_trader/state.py) / [monitor.py](tv_signal_trader/monitor.py) | `status.json` tracking (app running, login state) and the background login-poller |
| [tv_signal_trader/cli.py](tv_signal_trader/cli.py) | Interactive command loop |

### 1. Browser setup

On startup ([tv_signal_trader/browser.py](tv_signal_trader/browser.py)):

- Launches Chrome via `chromedriver` with a persistent profile folder (`tv_profile` in the user's home directory, resolved with `os.path.expanduser("~")`), so you only need to log into TradingView once.
- Applies a few anti-bot-detection tweaks (custom user-agent, hides `navigator.webdriver`, fakes `navigator.plugins`/`navigator.languages`) so TradingView is less likely to flag the session as automated.
- Opens the chart at `config.CHART_URL` (the default startup symbol — the `web` flow below switches it to whatever symbol the signal actually calls for).

The `chromedriver` path, and TradingGenerator credentials, are read from a local `.env` file ([tv_signal_trader/config.py](tv_signal_trader/config.py)) — see "First-run setup" below for how that gets populated.

### 2. First-run setup — [tv_signal_trader/setup_wizard.py](tv_signal_trader/setup_wizard.py)

Before opening the browser, `ensure_configured()` checks `.env` for a valid chromedriver path and TradingGenerator username/password. Anything missing is prompted for right there in the terminal (chromedriver path is validated as a real file; the password prompt uses `getpass` so it isn't echoed) and written back to `.env` — no manual file editing needed. Already-configured values are left untouched and skipped silently. Type `setup` at the `>` prompt anytime to change any of them.

### 3. Order placement — `place_order(driver, tp_ticks, sl_ticks, side, units)`

This is the core trading action. It drives TradingView's order panel (the right-hand sidebar), preferring stable selectors (`id`/`data-qa-id` attributes) where TradingView's markup actually provides them, and falling back to coordinate/DOM-text matching where it doesn't:

1. Clicks the page body and sends TradingView's built-in keyboard shortcut (`Shift+B` for buy, `Shift+S` for sell) to open the order ticket.
2. Clicks the **Market** order-type button.
3. Makes sure the "Quantity type" dropdown (`#quantity-dropdown-types`) is set to **Units** (not Contracts/Lots/etc.) before typing `units` into `#quantity-field`.
4. Expands the **Exits** section (if collapsed) to reveal the Take Profit/Stop Loss controls.
5. Turns on the **Take Profit** and **Stop Loss** toggles.
6. Makes sure each of the TP/SL bracket dropdowns (`order-ticket-take-profit-dropdown-button` / `order-ticket-stop-loss-dropdown-button`) is set to **Ticks** — since individual menu options aren't uniquely identifiable, "Ticks" is selected positionally (it's always the 2nd item in the menu) — then types `tp_ticks` / `sl_ticks` directly into the `order-ticket-take-profit-input` / `order-ticket-stop-loss-input` fields.
7. Clicks the final **Buy**/**Sell** confirm button and saves a screenshot (`after_buy.png` / `after_sell.png`).

All typing is done character-by-character with randomized delays (`type_humanlike`), and most steps have randomized pauses between them, to look more like a human user than a script.

### 4. Reading and executing a signal — `trade_from_website(driver)`

1. Opens TradingGenerator in a new tab (logging in automatically via [login.py](tv_signal_trader/login.py) if the session's expired) and clicks **GENERATE NEW TRADE**.
2. Reads the TRADE PARAMETERS box off the resulting page: asset (e.g. `NQ`), direction (`LONG`/`SHORT`), contracts + size (e.g. `1 MINI`), and stop-loss/take-profit in ticks.
3. Switches back to TradingView and resolves the TradingView ticker for that asset — `MINI` maps to the Micro contract (`NQ` → `MNQ`), with TradingView's `1!` continuous-contract suffix appended (e.g. `MNQ1!`) — then navigates the chart there.
4. Calls `place_order(...)` with the parsed direction/ticks/contracts. If any required field couldn't be parsed, it aborts instead of guessing/falling back to a default.

### 5. Status tracking — [tv_signal_trader/status.py](tv_signal_trader/status.py), [state.py](tv_signal_trader/state.py), [monitor.py](tv_signal_trader/monitor.py)

A `status.json` file (next to `.env`) tracks `app_running`, `tradingview_logged_in`, and `tradinggenerator_logged_in`, refreshed on startup/shutdown and by a background `LoginMonitor` thread that re-checks TradingView's login cookie every 15s (via the `Network.getAllCookies` CDP command, so it doesn't need to switch tabs and visibly hijack the browser). TradingGenerator's login status can only be determined by reading its tab's DOM, which *would* require disruptively switching to it, so that one's only updated on-demand whenever `web` actually uses it.

### 6. Interactive command loop

Running the script drops you into a `>` prompt that accepts:

| Command      | Effect                                                              |
|--------------|----------------------------------------------------------------------|
| `web`        | Runs `trade_from_website()` — pulls a signal and executes the trade |
| `buy`        | Places a manual buy with 150-tick TP/SL                              |
| `sell`       | Places a manual sell with 150-tick TP/SL                             |
| `scan`       | Debug: prints every input field detected in the right-hand panel     |
| `screenshot` | Saves a screenshot to `current.png`                                  |
| `setup`      | Re-run setup to change the chromedriver path or TradingGenerator credentials |
| `quit`       | Closes the browser and exits                                         |

## How to run it

1. Install Python 3.9+ and Chrome.
2. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

3. Download the `chromedriver` build matching your installed Chrome version from [Chrome for Testing](https://googlechromelabs.github.io/chrome-for-testing/) — you'll be prompted for its path on first run, so it can live anywhere.
4. Run the script:

   ```bash
   python main.py
   ```

5. On first run you'll be walked through setup in the terminal for anything missing: the chromedriver path, and your TradingGenerator username/password (input hidden). These get saved to a local `.env` file so you're only asked once — type `setup` at the `>` prompt anytime to change them.
6. Chrome then opens to the chart — log into TradingView (and make sure the intended Tradovate/broker connection is active) in that window. The session persists in the `tv_profile` folder for future runs; TradingView itself isn't part of the `.env`/setup flow since it relies on that persistent cookie-based session, not password auto-fill.
7. Type a command at the `>` prompt (see table above).

## Building a standalone .exe

For sharing this with a few trusted people without handing them the source, [Nuitka](https://nuitka.net/) compiles the whole app (Python → C → machine code) into a single `tv-signal-trader.exe`. This is obfuscation, not real security — treat it as raising the bar for casual inspection, not as a place to store secrets. No credentials are ever compiled in: `.env`, `status.json`, and the chromedriver path are all read from/written next to wherever the `.exe` itself lives at runtime ([tv_signal_trader/config.py](tv_signal_trader/config.py) resolves this via `sys.argv[0]`, not `__file__` — `--onefile` self-extracts to a new temp directory on every launch, so anything anchored to `__file__` would silently reset each run).

1. `pip install -r requirements-build.txt`
2. Run [build.ps1](build.ps1) (or the `nuitka` command inside it directly). Output is `dist/tv-signal-trader.exe`.
3. Hand the recipient just that one `.exe` — they'll get the same first-run setup wizard prompting for their own chromedriver path and TradingGenerator credentials.
4. If Nuitka builds with MSVC (`cl.exe`) rather than MinGW64, it can't statically link the Windows C runtime, so recipients without it already installed will need the [Visual C++ Redistributable (x64)](https://aka.ms/vs/17/release/vc_redist.x64.exe) — a small, extremely common one-time install.
5. Don't commit `dist/` or the `.exe` into git — publish built binaries as [GitHub Releases](https://docs.github.com/en/repositories/releasing-projects-on-github) assets instead, so the repo itself doesn't accumulate large binary blobs.

## Known limitations / things to watch out for

- **UI-automation is brittle**: element lookup relies on a mix of stable IDs/`data-qa-id` attributes (where TradingView provides them) and screen coordinates/label text matching (where it doesn't). Any TradingView layout change, browser zoom level, or window-size change can break the latter.
- **The "Ticks" bracket-mode selection is positional**: since the TP/SL dropdown's menu items don't expose a stable per-option identifier, "Ticks" is selected by assuming it's always the 2nd item in the menu — if TradingView ever reorders that menu, this breaks silently.
- **Minimal error handling**: most failures just print a warning and move on rather than retrying or raising — check the console output after each command.
- **Places real orders**: `place_order()` clicks the live Buy/Sell confirm button. Test against a paper/demo Tradovate connection before pointing this at a live account.

## Repository docs

- [GIT_SETUP.md](GIT_SETUP.md) — how to get set up with Git and the day-to-day commands (status/add/commit/push/pull) for contributing to this repo.
