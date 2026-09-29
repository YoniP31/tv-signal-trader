#!/usr/bin/env python
"""activate_remote.py -- enters the one-time activation password on a fleet
of VPS over SSH, and optionally starts the bot afterward. No RDP needed.

    python deploy/activate_remote.py                 # activate every machine in the roster
    python deploy/activate_remote.py --start          # then start the bot's task on each one too
    python deploy/activate_remote.py --host 1.2.3.4 --start
    python deploy/activate_remote.py --yes            # skip the confirmation prompt

The password is asked for once, here, then sent to each machine over SSH's
own stdin forwarding -- never as a command-line argument, never written to a
file (there or here), and never printed or logged anywhere, including in the
JSON report this writes. This is exactly the same thing as typing the
password at that machine's own console, done from here instead; it does not
change what the activation password protects, or who needs to know it (see
the main README's "The one-time activation password").

What happens per machine:
  1. reads which exe its Scheduled Task runs (same as deploy.py)
  2. runs that exe with no arguments, piping the password to it. On a
     correct password (or one already entered before) it proceeds to try
     opening Chrome -- which fails in this plain SSH session (no real
     desktop attached, unlike the Scheduled Task's own interactive-session
     launch) and the exe exits on its own after its own retry/cleanup logic
     gives up, a matter of some seconds. A wrong password fails fast
     instead (nothing further to read from stdin).
  3. refreshes and runs stop-bot.ps1, so that stray attempt (and anything
     it half-started) never lingers -- the same cleanup deploy.py always
     does before a real restart.
  4. with --start: starts the bot's Scheduled Task for real (in its own
     interactive session, where Chrome works normally) and confirms it
     came up.

Reuses deploy.py's config/roster (deploy_config.json, vps_list.txt) and its
Remote/reporting machinery -- this is a sibling tool, not a copy of it.
"""

import argparse
import concurrent.futures
import datetime
import getpass
import importlib.util
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

DEPLOY_DIR = Path(__file__).resolve().parent
REPO_ROOT = DEPLOY_DIR.parent

_SPEC = importlib.util.spec_from_file_location("deploy_tool", DEPLOY_DIR / "deploy.py")
deploy = importlib.util.module_from_spec(_SPEC)
sys.modules.setdefault("deploy_tool", deploy)
_SPEC.loader.exec_module(deploy)

# Generous: a correct/already-used password leads the exe on to try (and
# fail) opening Chrome in this non-interactive session before it gives up
# on its own -- see browser.create_driver's own retry loop -- which can
# take upwards of 15-20s. A wrong password returns almost immediately
# instead (nothing further to read from stdin), so this only matters for
# the success path.
ACTIVATE_WAIT_SECONDS = 60

STATUSES_OK = ("activated", "started")


def activate_host(target, cfg, password, start, runner=subprocess.run, sleep=time.sleep,
                   log=None, stop_script=deploy.STOP_SCRIPT):
    """Runs the whole activation (+ optional start) for one VPS. Never
    raises for an ordinary failure -- returns a deploy.HostResult (the same
    shape deploy.py's own report uses) whose status says what happened.
    Never includes the password in anything it returns, logs, or notes."""
    user, host = target
    started_at = time.monotonic()
    result = deploy.HostResult(host)

    def note(msg):
        result.steps.append(msg)
        if log:
            log(host, msg)

    def finish(status, detail=""):
        result.status, result.detail = status, detail
        result.seconds = time.monotonic() - started_at
        return result

    remote = deploy.Remote(user, host, cfg, runner=runner)
    task = cfg["remote"]["task_name"]
    quick = cfg["timeouts"]["command"]

    try:
        note("checking connection")
        r = remote.run("echo ok", timeout=remote.connect_timeout + 20)
        if r.returncode != 0 or "ok" not in r.stdout:
            return finish("unreachable", deploy._tail(r.stderr))

        note("reading the scheduled task")
        r = remote.run(f'schtasks /query /tn "{task}" /xml', timeout=quick)
        vps_exe = deploy.parse_task_exe(r.stdout) if r.returncode == 0 else None
        if not vps_exe:
            return finish("no_task", f"no usable '{task}' task - run deploy/setup_vps.ps1 on this VPS first")
        task_dir = deploy.parse_task_dir(r.stdout)
        install_win = deploy.win_path(task_dir or cfg["remote"]["install_dir"])
        note(f"runs {vps_exe}")

        note("entering the activation password")
        command = f'cd /d "{install_win}" && {vps_exe}'
        try:
            r = remote.run(command, timeout=ACTIVATE_WAIT_SECONDS, input_data=password + "\n")
            output = (r.stdout or "") + (r.stderr or "")
        except subprocess.TimeoutExpired as exc:
            # Expected on a fresh activation that went on to (fail to) open
            # Chrome -- what was printed before the timeout already says
            # whether activation itself succeeded, which happens early.
            output = (exc.stdout or "") + (exc.stderr or "")
            note("still running past the wait (expected if it went on to try opening Chrome)")

        if "no activation password configured" in output:
            return finish("no_password_configured", "this build has no activation password baked in")
        if "Wrong password" in output:
            return finish("wrong_password", "rejected - check the password and try again")
        if "This computer is now activated" in output:
            note("activated")
        elif "has not been activated yet" in output:
            return finish("error", "did not seem to complete - " + deploy._tail(output))
        else:
            note("was already activated (no prompt shown)")

        note("cleaning up this attempt's own run")
        install_posix = deploy.posix_path(install_win)
        deploy.refresh_stop_script(remote, stop_script, install_win, install_posix, quick, note)
        stop_problem = deploy.stop_bot(remote, install_win, vps_exe, cfg, sleep)
        if stop_problem:
            note(f"cleanup warning: {stop_problem}")

        if not start:
            return finish("activated")

        note("starting the bot")
        r = remote.run(f'schtasks /run /tn "{task}"', timeout=quick)
        if r.returncode != 0:
            return finish("start_failed", deploy._tail(r.stderr or r.stdout))
        sleep(cfg["verify_seconds"])
        if deploy.check_running(remote, vps_exe, quick):
            return finish("started")
        return finish("start_failed", f"{vps_exe} is not running {cfg['verify_seconds']}s after starting")
    except subprocess.TimeoutExpired as exc:
        return finish("unreachable", f"timed out: {deploy._tail(str(exc.cmd) if exc.cmd else '')}")
    except Exception as exc:  # one machine's surprise must never abort the whole batch
        return finish("error", f"{type(exc).__name__}: {exc}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Enter the activation password on a fleet of VPS over SSH.")
    ap.add_argument("--config", default=str(DEPLOY_DIR / "deploy_config.json"))
    ap.add_argument("--targets", help="roster file, one host or user@host per line (default: config's targets_file)")
    ap.add_argument("--host", action="append", help="just this host (repeatable); overrides the roster")
    ap.add_argument("--start", action="store_true", help="also start the bot's Scheduled Task afterward")
    ap.add_argument("--hidden", action="store_true", help="hide the password while typing it here "
                                                           "(mishandles pasted text on Windows)")
    ap.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    ap.add_argument("-v", "--verbose", action="store_true", help="print every step as it happens")
    args = ap.parse_args(argv)

    try:
        cfg = deploy.load_config(args.config)
        if args.host:
            targets = deploy.parse_targets("\n".join(args.host), cfg["ssh"]["user"])
        else:
            roster = Path(args.targets or cfg["targets_file"])
            roster = roster if roster.is_absolute() else REPO_ROOT / roster
            if not roster.is_file():
                raise deploy.ConfigError(f"Roster file not found: {roster} (or pass --host)")
            targets = deploy.parse_targets(roster.read_text(encoding="utf-8"), cfg["ssh"]["user"])
        if not targets:
            raise deploy.ConfigError("No target machines.")
    except deploy.ConfigError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print(f"\nWill enter the activation password on {len(targets)} machine(s)"
          f"{' and start the bot afterward' if args.start else ''}:")
    for _user, host in targets[:10]:
        print(f"    -> {_user}@{host}")
    if len(targets) > 10:
        print(f"    ... and {len(targets) - 10} more")

    ask = getpass.getpass if args.hidden else input
    password = ask("\nActivation password (sent over SSH to each machine above, never stored): ").strip()
    if not password:
        print("The password can't be empty.", file=sys.stderr)
        return 2

    if not args.yes and input("\nType 'yes' to continue: ").strip().lower() != "yes":
        print("Aborted.")
        return 1

    print_lock = threading.Lock()

    def log(host, msg):
        if args.verbose:
            with print_lock:
                print(f"  [{host}] {msg}")

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=cfg["max_parallel"]) as pool:
        futures = {pool.submit(activate_host, t, cfg, password, args.start, log=log): t for t in targets}
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            results.append(res)
            with print_lock:
                print(f"  {res.host:<20} {res.status:<22} {res.detail}")

    ok = sum(1 for r in results if r.status in STATUSES_OK)
    print(f"\n{ok}/{len(results)} machine(s) OK.")
    failed = [r for r in results if r.status not in STATUSES_OK]
    for r in failed:
        print(f"  FAILED {r.host}: {r.status} - {r.detail}")

    report_dir = DEPLOY_DIR / "reports"
    report_dir.mkdir(exist_ok=True)
    report_path = report_dir / f"activate_{datetime.datetime.now():%Y%m%d_%H%M%S}.json"
    report_path.write_text(json.dumps({
        "start": args.start,
        "results": [r.as_dict() for r in sorted(results, key=lambda r: r.host)],
    }, indent=2))
    print(f"Report: {report_path}")

    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
