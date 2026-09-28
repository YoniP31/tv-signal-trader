# Deploying updates to VPS machines

`deploy.py` pushes new bot files to many Windows VPS over SSH from your own
machine, stopping and restarting the bot on each one.

## One-time, per VPS

Run `setup_vps.ps1` on the VPS (over RDP, from an elevated PowerShell):

    powershell -ExecutionPolicy Bypass -File .\setup_vps.ps1

It enables OpenSSH, installs the deploy public key, writes `stop-bot.ps1`,
registers the `TVSignalTrader` Scheduled Task, and registers the
`TVSignalTraderWatchdog` Scheduled Task (see "The watchdog" below) -- the
latter needs `watchdog.ps1` already extracted into the bot's folder (it ships
in the release zip, next to the exe(s)); if it's missing, that one step is
skipped with a warning rather than failing the whole script, and re-running
setup after extracting it registers it. The bot's folder can be named
anything that starts with `tv-signal-trader` (e.g. `tv-signal-trader-v1.1.0`):
the script looks for it on the Desktop (`$InstallRoot` / `$InstallPrefix` at the
top of the script) and picks the one folder holding the exe. If there is more
than one such folder it stops and asks you to set `$InstallDir` explicitly.
After moving the bot to a new folder, run the script again -- it repoints the
task. Afterwards start the bot with
`Start-ScheduledTask -TaskName TVSignalTrader`, not by hand, and close RDP with
the window's X rather than "Sign out" (the task needs a logged-in session).

The task deliberately runs **non-elevated**: Chrome exits immediately
("session not created: Chrome instance exited") when the bot is launched
elevated.

**Activation, once per machine.** A built `.exe` asks for the one-time
activation password the first time it runs on a computer (see "The one-time
activation password" in the main README). The Scheduled Task starts it with
nobody there to type, so on a machine that hasn't been activated yet it just
prints why and exits -- a deploy would report `verify_failed`. So before
relying on a new machine's task, double-click the `.exe` once over RDP (not
from an elevated PowerShell, for the same Chrome reason as above), enter the
password, then close it. That is the only time it is asked: the marker lives in
the user's home folder, so later updates -- including into a new versioned
folder -- never ask again.

### Adding the watchdog to machines set up before it existed

A machine set up before `watchdog.ps1` existed has everything else, just not
that file or its task. Two ways to fix that:

- **By hand, once per machine:** copy `watchdog.ps1` into the bot's folder
  (e.g. `scp` with your deploy key), then re-run `setup_vps.ps1` there. Safe
  to re-run -- it reuses whatever exe/command that machine's task already
  runs rather than a fixed default, so it can't silently repoint a working
  machine at the wrong build.
- **Entirely from your own machine, across the whole fleet:**

      python deploy/deploy.py --push watchdog --run-setup

  This pushes `watchdog.ps1` (a normal file, like any other -- see `files.watchdog`
  in `deploy_config.json`) and, once that succeeds, uploads and runs
  `setup_vps.ps1` there itself over the same SSH connection (hash-verified,
  same care as any other file). It never restarts the bot by itself --
  `watchdog.ps1` isn't the exe or `.env` -- and `--run-setup` composes with
  any other `--push` selection, so a normal exe update can register the
  watchdog in the same run: `--push admin_exe,watchdog --run-setup`.

  This needs no RDP: SSH sessions here connect as the literal built-in
  `Administrator` account, which Windows exempts from UAC's admin-approval
  filtering, so a command run over that connection already carries full
  admin rights -- the same as running `setup_vps.ps1` from an elevated
  PowerShell by hand. Confirm this once on a real VPS before trusting it
  across the fleet.

  Result per machine gets a new value for this step: `setup_failed` (the
  files were fine; `setup_vps.ps1` itself failed there -- check that
  machine's output in `-v`/the JSON report).

### The watchdog

`watchdog.ps1` lives next to the exe(s) (it ships in the release zip, like
`.env`/`accounts_to_add.txt`) and runs every 4 hours on its own Scheduled Task,
`TVSignalTraderWatchdog` (registered by `setup_vps.ps1`, as SYSTEM -- it only
observes/kills processes and triggers the bot's own task, never launches
Chrome itself, so it isn't subject to that task's elevation problem, and
doesn't need anyone logged in just to fire).

Each run: reads which exe the `TVSignalTrader` task actually runs, checks
whether that process is up. If it is, it logs `OK` and does nothing else. If
it isn't, it runs `stop-bot.ps1` first (in case the crash left Chrome/
chromedriver orphaned -- starting the exe again against that would just fail
the same way), then starts the task again and confirms it actually came back.
Every run appends to `watchdog.log`, next to the exe(s) -- a dedicated log,
separate from the bot's own `app.log`.

It can only bring the bot back within a session that's already open (the one
its own task runs in). It does not solve "nobody is logged into this VPS at
all" -- there's no auto-logon configured, so a fully rebooted machine still
needs someone to log back in before either task can do anything.

### Building the release zip

`build_release_zip.py` is the single source of truth for what a release's zip
contains (both exes, `.env`, `accounts_to_add.txt`, `watchdog.ps1`), so a new
shipped file can't be forgotten by hand-listing the zip's contents from memory
on some future release:

    python deploy/build_release_zip.py --version v1.4.0

## Each update

    cp deploy/deploy_config.example.json deploy/deploy_config.json   # once
    cp deploy/vps_list.example.txt       deploy/vps_list.txt         # once, then list your fleet

    python deploy/deploy.py --dry-run          # checks every machine, changes nothing
    python deploy/deploy.py                    # the real run (asks you to type "yes")
    python deploy/deploy.py --host 1.2.3.4     # just one machine, ignoring the roster
    python deploy/deploy.py --push admin_exe,user_exe   # override which files this run pushes
    python deploy/deploy.py --push watchdog --run-setup # add the watchdog to an existing fleet
    python deploy/deploy.py -v                 # print every step as it happens

Each file in `deploy_config.json` has a `push` flag (admin_exe, user_exe, env,
accounts, watchdog) and a source: `"from": "local"` with a `path` (relative paths are from
the repo root, e.g. a fresh `dist/` build), or `"from": "release"` with an
`asset` name, taken from the GitHub release named by `release.tag` (`"latest"`
includes pre-releases). A release asset can be a loose file or a file inside the
release's zip.

### What it does to each machine

`deploy.py` finds each machine's bot folder from that machine's own Scheduled
Task, so folder names can differ per VPS. `remote.install_dir` in the config is
only a fallback for a task whose command has no folder in it.

The bot is restarted only when it has to be: when the exe **that machine's task
runs** is being replaced, or `.env` is (config is read once at startup). Pushing
only the other variant's exe, or `accounts_to_add.txt`, never interrupts trading.

Every file is uploaded to `<name>.new`, its SHA-256 is verified on the VPS, and
only then moved into place. Before every stop, the current `deploy/stop-bot.ps1`
is put on the machine the same way, so a fix to it reaches every VPS without
re-running `setup_vps.ps1` (setup embeds a copy; a test keeps the two identical).
`stop-bot.ps1` only reports success once the bot, its chromedriver and every
Chrome on the bot's profile are really gone -- a Chrome left holding that
profile is what makes the next launch die with "Chrome instance exited".

Once a stop has been attempted the bot may be down, so **whatever goes wrong
after that** -- a copy failure, a stop that times out or fails, a machine too
slow to answer, an unexpected error -- the tool starts the bot again on its
previous files before reporting (it checks first, and starting a task that is
already running is harmless). If even that fails the result says
`COULD NOT RESTART THE BOT` -- check that machine. An existing `.env`
is copied to `.env.bak` before being replaced, and replacing `.env` needs an
extra warning and a typed "yes" -- it holds each machine's real credentials, so
`push` stays `false` for it in normal use.

These VPS can be slowed badly by the bot's own Chrome, so waits are generous and
configurable: `timeouts.stop` (default 300 s, for `stop-bot.ps1`) and
`timeouts.command` (default 90 s, for every other command) in `deploy_config.json`.
If machines still time out, run the update outside the trading session.

Result per machine: `ok`, `ok_no_restart`, `unreachable`, `no_task` (run
`setup_vps.ps1` there), `stop_failed`, `copy_failed`, `restart_failed`,
`verify_failed`, and (only with `--run-setup`) `setup_failed`. A JSON report
of every run is written to `deploy/reports/`.
