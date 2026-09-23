# Deploying updates to VPS machines

`deploy.py` pushes new bot files to many Windows VPS over SSH from your own
machine, stopping and restarting the bot on each one.

## One-time, per VPS

Run `setup_vps.ps1` on the VPS (over RDP, from an elevated PowerShell):

    powershell -ExecutionPolicy Bypass -File .\setup_vps.ps1

It enables OpenSSH, installs the deploy public key, writes `stop-bot.ps1`, and
registers the `TVSignalTrader` Scheduled Task. The bot's folder can be named
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

## Each update

    cp deploy/deploy_config.example.json deploy/deploy_config.json   # once
    cp deploy/vps_list.example.txt       deploy/vps_list.txt         # once, then list your fleet

    python deploy/deploy.py --dry-run          # checks every machine, changes nothing
    python deploy/deploy.py                    # the real run (asks you to type "yes")
    python deploy/deploy.py --host 1.2.3.4     # just one machine, ignoring the roster
    python deploy/deploy.py --push admin_exe,user_exe   # override which files this run pushes
    python deploy/deploy.py -v                 # print every step as it happens

Each file in `deploy_config.json` has a `push` flag (admin_exe, user_exe, env,
accounts) and a source: `"from": "local"` with a `path` (relative paths are from
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
`verify_failed`. A JSON report of every run is written to `deploy/reports/`.
