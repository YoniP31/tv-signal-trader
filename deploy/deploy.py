#!/usr/bin/env python
"""deploy.py -- push tv-signal-trader updates to a fleet of Windows VPS over SSH.

Run from your own machine (needs the OpenSSH client: `ssh`/`scp`). Each target
VPS must already have been prepared once with deploy/setup_vps.ps1 (OpenSSH
server, the deploy key, the TVSignalTrader Scheduled Task, stop-bot.ps1).

    python deploy/deploy.py --dry-run                       # plan only, touches nothing
    python deploy/deploy.py                                 # per deploy_config.json
    python deploy/deploy.py --host 169.58.224.170           # one machine, ignoring the roster
    python deploy/deploy.py --push admin_exe,user_exe       # override which files this run pushes

Per machine, in order:
  1. reachability check                          -> unreachable
  2. read the exe its Scheduled Task runs        -> no_task
  3. decide whether the bot must restart at all: only when the exe *it runs* is
     being replaced, or .env is (config is read once at startup). Pushing only
     the other variant's exe, or accounts_to_add.txt, never interrupts trading.
  4. stop the bot (stop-bot.ps1) and confirm it's gone   -> stop_failed
  5. per file: upload to "<name>.new", verify its SHA-256 on the VPS, and only
     then move it into place (a half-written or corrupt file never replaces a
     working one)                                        -> copy_failed
     An existing .env is copied to .env.bak first.
  6. start the task (even after a copy failure: the old files are still intact,
     so the machine is never left down)                  -> restart_failed
  7. wait, then confirm the process is actually running  -> verify_failed

Files come from wherever deploy_config.json says, per file: a local path
(e.g. a fresh dist/ build) or a GitHub release (a loose asset, or unpacked
from the release zip).
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

DEPLOY_DIR = Path(__file__).resolve().parent
REPO_ROOT = DEPLOY_DIR.parent

# file key -> the name it always has on the VPS, next to the exe.
REMOTE_FILENAMES = {
    "admin_exe": "tv-signal-trader.exe",
    "user_exe": "tv-signal-trader-user.exe",
    "env": ".env",
    "accounts": "accounts_to_add.txt",
}
EXE_KEYS = ("admin_exe", "user_exe")

DEFAULT_CONFIG = {
    "targets_file": "deploy/vps_list.txt",
    "ssh": {"user": "Administrator", "key": "~/.ssh/tv_deploy_key", "connect_timeout": 15},
    "remote": {
        "install_dir": "C:/Users/Administrator/Desktop/tv-signal-trader",
        "task_name": "TVSignalTrader",
    },
    "release": {"repo": "YoniP31/tv-signal-trader", "tag": "latest"},
    "max_parallel": 5,
    "verify_seconds": 25,
}

OK_STATUSES = ("ok", "ok_no_restart", "dry_run")


class ConfigError(Exception):
    pass


class SourceError(Exception):
    pass


# --------------------------------------------------------------------------
# Config, roster, sources
# --------------------------------------------------------------------------

def _merge(defaults, overrides):
    merged = dict(defaults)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(path):
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(f"Config file not found: {path} (copy deploy/deploy_config.example.json)")
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path} isn't valid JSON: {exc}")
    cfg = _merge(DEFAULT_CONFIG, raw)

    files = cfg.get("files")
    if not isinstance(files, dict) or not files:
        raise ConfigError("Config needs a non-empty \"files\" section.")
    for key, spec in files.items():
        if key not in REMOTE_FILENAMES:
            raise ConfigError(f"Unknown file key '{key}' - expected one of {', '.join(REMOTE_FILENAMES)}.")
        origin = spec.get("from")
        if origin not in ("local", "release"):
            raise ConfigError(f"files.{key}.from must be 'local' or 'release', got {origin!r}.")
        if origin == "local" and not spec.get("path"):
            raise ConfigError(f"files.{key} is 'local' but has no \"path\".")
    return cfg


def selected_files(cfg, override=None):
    """The file keys this run pushes: the config's push flags, or an explicit
    override (comma-separated string or list) that replaces them entirely."""
    if override is not None:
        keys = [k.strip() for k in (override.split(",") if isinstance(override, str) else override) if k.strip()]
        for key in keys:
            if key not in cfg["files"]:
                raise ConfigError(f"--push names '{key}', which has no entry under \"files\" in the config.")
    else:
        keys = [key for key, spec in cfg["files"].items() if spec.get("push")]
    return [k for k in REMOTE_FILENAMES if k in keys]  # stable order: exes first, .env last


def parse_targets(text, default_user):
    """One target per line: `host` or `user@host`; `#` starts a comment."""
    targets, seen = [], set()
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        user, _, host = line.rpartition("@")
        target = (user or default_user, host.strip())
        if target not in seen:
            seen.add(target)
            targets.append(target)
    return targets


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _github_json(url, opener):
    req = urllib.request.Request(url, headers={
        "User-Agent": "tv-signal-trader-deploy", "Accept": "application/vnd.github+json",
    })
    with opener(req, timeout=30) as resp:
        return json.load(resp)


def get_release(release_cfg, opener=urllib.request.urlopen):
    repo, tag = release_cfg["repo"], release_cfg["tag"]
    if tag == "latest":
        # Not /releases/latest: that endpoint skips pre-releases and 404s when
        # every release is one -- which is exactly this project's situation.
        releases = [r for r in _github_json(
            f"https://api.github.com/repos/{repo}/releases?per_page=5", opener) if not r.get("draft")]
        if not releases:
            raise SourceError(f"{repo} has no published releases.")
        return releases[0]
    return _github_json(f"https://api.github.com/repos/{repo}/releases/tags/{tag}", opener)


def _download(url, dest, expected_size, opener):
    if dest.is_file() and expected_size and dest.stat().st_size == expected_size:
        return dest
    req = urllib.request.Request(url, headers={"User-Agent": "tv-signal-trader-deploy"})
    with opener(req, timeout=120) as resp, open(dest, "wb") as out:
        shutil.copyfileobj(resp, out)
    return dest


def fetch_release_file(release_cfg, asset_name, cache_dir, opener=urllib.request.urlopen, _release=None):
    """A release file by name: a loose asset if the release has one, otherwise
    pulled out of the release's zip (how releases up to v1.0.0 were bundled)."""
    release = _release or get_release(release_cfg, opener)
    dest_dir = Path(cache_dir) / release["tag_name"]
    dest_dir.mkdir(parents=True, exist_ok=True)
    assets = release.get("assets", [])

    for asset in assets:
        if asset["name"] == asset_name:
            return _download(asset["browser_download_url"], dest_dir / asset_name, asset.get("size"), opener)

    for asset in (a for a in assets if a["name"].lower().endswith(".zip")):
        zip_path = _download(asset["browser_download_url"], dest_dir / asset["name"], asset.get("size"), opener)
        with zipfile.ZipFile(zip_path) as zf:
            for member in zf.namelist():
                if member.rsplit("/", 1)[-1].lower() == asset_name.lower():
                    out_path = dest_dir / asset_name
                    out_path.write_bytes(zf.read(member))
                    return out_path
    raise SourceError(f"Release {release['tag_name']} has no asset or zip entry named '{asset_name}'.")


def resolve_sources(cfg, keys, cache_dir, opener=urllib.request.urlopen):
    """file key -> local Path, downloading release files as needed."""
    paths, release = {}, None
    for key in keys:
        spec = cfg["files"][key]
        if spec["from"] == "local":
            p = Path(spec["path"]).expanduser()
            p = p if p.is_absolute() else REPO_ROOT / p
            if not p.is_file():
                raise SourceError(f"files.{key}: local file not found: {p}")
            paths[key] = p
        else:
            release = release or get_release(cfg["release"], opener)
            paths[key] = fetch_release_file(
                cfg["release"], spec.get("asset", REMOTE_FILENAMES[key]), cache_dir, opener, _release=release)
    return paths


# --------------------------------------------------------------------------
# Talking to a VPS
# --------------------------------------------------------------------------

def win_path(p):
    return p.replace("/", "\\")


def posix_path(p):
    return p.replace("\\", "/")


class Remote:
    """ssh/scp to one VPS via the system OpenSSH client. `runner` is injectable
    (defaults to subprocess.run) so the whole flow is testable without a network."""

    def __init__(self, user, host, cfg, runner=subprocess.run):
        self.user, self.host, self.runner = user, host, runner
        ssh = cfg["ssh"]
        self.connect_timeout = ssh["connect_timeout"]
        self._opts = [
            "-i", os.path.expanduser(ssh["key"]),
            "-o", "BatchMode=yes",  # never hang on a password prompt
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", f"ConnectTimeout={self.connect_timeout}",
            "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=4",
        ]

    def run(self, command, timeout=60):
        """`command` runs under cmd.exe on the VPS (Windows OpenSSH's default shell)."""
        return self.runner(
            ["ssh", *self._opts, f"{self.user}@{self.host}", command],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
        )

    def copy(self, local_path, remote_posix_path, timeout=900):
        return self.runner(
            ["scp", *self._opts, str(local_path), f"{self.user}@{self.host}:{remote_posix_path}"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
        )


def parse_task_exe(task_xml):
    """The exe file name a Scheduled Task runs, from `schtasks /query /xml`."""
    m = re.search(r"<Command>\s*(.*?)\s*</Command>", task_xml, re.S | re.I)
    if not m:
        return None
    command = m.group(1).strip().strip('"').replace("/", "\\")
    return command.rsplit("\\", 1)[-1] or None


def parse_certutil_hash(text):
    for line in text.splitlines():
        candidate = line.replace(" ", "").strip().lower()
        if re.fullmatch(r"[0-9a-f]{64}", candidate):
            return candidate
    return None


def is_running(remote, exe_name):
    r = remote.run(f'tasklist /fi "imagename eq {exe_name}" /fo csv /nh', timeout=30)
    return exe_name.lower() in r.stdout.lower()


class HostResult:
    def __init__(self, host):
        self.host = host
        self.status = "unknown"
        self.detail = ""
        self.steps = []
        self.seconds = 0.0

    def as_dict(self):
        return {"host": self.host, "status": self.status, "detail": self.detail,
                "seconds": round(self.seconds, 1), "steps": self.steps}


def _tail(text, n=200):
    return (text or "").strip().replace("\r", " ").replace("\n", " ")[-n:]


def update_host(target, cfg, files, hashes, dry_run=False,
                runner=subprocess.run, sleep=time.sleep, log=None):
    """Runs the whole update for one VPS. `files` is {file key: local Path};
    `hashes` is {file key: sha256}. Never raises for an ordinary failure --
    returns a HostResult whose status says what happened."""
    user, host = target
    started = time.monotonic()
    result = HostResult(host)

    def note(msg):
        result.steps.append(msg)
        if log:
            log(host, msg)

    def finish(status, detail=""):
        result.status, result.detail = status, detail
        result.seconds = time.monotonic() - started
        return result

    remote = Remote(user, host, cfg, runner=runner)
    install_win = win_path(cfg["remote"]["install_dir"])
    install_posix = posix_path(cfg["remote"]["install_dir"])
    task = cfg["remote"]["task_name"]

    try:
        note("checking connection")
        r = remote.run("echo ok", timeout=remote.connect_timeout + 20)
        if r.returncode != 0 or "ok" not in r.stdout:
            return finish("unreachable", _tail(r.stderr))

        note("reading the scheduled task")
        r = remote.run(f'schtasks /query /tn "{task}" /xml', timeout=30)
        vps_exe = parse_task_exe(r.stdout) if r.returncode == 0 else None
        if not vps_exe:
            return finish("no_task", f"no usable '{task}' task - run deploy/setup_vps.ps1 on this VPS first")

        pushed_names = {REMOTE_FILENAMES[k] for k in files}
        needs_restart = vps_exe.lower() in {n.lower() for n in pushed_names} or "env" in files
        note(f"runs {vps_exe}; pushing {sorted(pushed_names)}; "
             f"{'restart needed' if needs_restart else 'no restart needed'}")

        if dry_run:
            return finish("dry_run", f"would push {sorted(pushed_names)}"
                                     f"{' and restart' if needs_restart else ' (no restart)'}")

        if needs_restart:
            note("stopping the bot")
            try:
                r = remote.run(f'powershell -NoProfile -ExecutionPolicy Bypass -File "{install_win}\\stop-bot.ps1"',
                               timeout=120)
            except subprocess.TimeoutExpired:
                return finish("stop_failed", "stop-bot.ps1 timed out - the bot may be stopped; check this machine")
            stopped = False
            for _ in range(5):
                if not is_running(remote, vps_exe):
                    stopped = True
                    break
                sleep(1)
            if r.returncode != 0 or not stopped:
                return finish("stop_failed", _tail(r.stderr) or f"{vps_exe} still running after stop-bot.ps1")

        # From here on the bot may be stopped, so nothing below may raise past
        # the restart: whatever goes wrong copying, the start still happens.
        copy_error = None
        for key in files:
            name = REMOTE_FILENAMES[key]
            final, staged = f"{install_win}\\{name}", f"{install_win}\\{name}.new"
            try:
                note(f"uploading {name}")
                r = remote.copy(files[key], f"{install_posix}/{name}.new")
                if r.returncode != 0:
                    copy_error = f"upload of {name} failed: {_tail(r.stderr)}"
                    break
                r = remote.run(f'certutil -hashfile "{staged}" SHA256', timeout=120)
                if parse_certutil_hash(r.stdout) != hashes[key]:
                    remote.run(f'del /Q "{staged}"', timeout=30)
                    copy_error = f"{name} failed its SHA-256 check after upload (not installed)"
                    break
                if key == "env":
                    remote.run(f'if exist "{final}" copy /Y "{final}" "{final}.bak"', timeout=30)
                r = remote.run(f'move /Y "{staged}" "{final}"', timeout=30)
                if r.returncode != 0:
                    copy_error = f"could not move {name} into place: {_tail(r.stderr or r.stdout)}"
                    break
                note(f"installed {name}")
            except subprocess.TimeoutExpired:
                copy_error = f"timed out while copying {name} (the installed file is untouched)"
                break
            except Exception as exc:
                copy_error = f"{type(exc).__name__} while copying {name}: {exc}"
                break

        if not needs_restart:
            return finish("copy_failed", copy_error) if copy_error else finish("ok_no_restart")

        note("starting the bot")
        try:
            r = remote.run(f'schtasks /run /tn "{task}"', timeout=30)
            start_error = None if r.returncode == 0 else _tail(r.stderr or r.stdout)
        except subprocess.TimeoutExpired:
            start_error = "timed out starting the task"
        if start_error:
            return finish("restart_failed", start_error + (f" (also: {copy_error})" if copy_error else ""))

        note(f"waiting {cfg['verify_seconds']}s to confirm it stays up")
        sleep(cfg["verify_seconds"])
        try:
            running = is_running(remote, vps_exe)
        except subprocess.TimeoutExpired:
            running = False
        if not running:
            return finish("verify_failed", f"{vps_exe} is not running {cfg['verify_seconds']}s after starting"
                          + (f" (also: {copy_error})" if copy_error else ""))
        if copy_error:
            return finish("copy_failed", copy_error + " - the bot was restarted on its previous files")
        return finish("ok")
    except subprocess.TimeoutExpired as exc:
        return finish("unreachable", f"timed out: {_tail(str(exc.cmd) if exc.cmd else '')}")
    except Exception as exc:  # one machine's surprise must never abort the whole batch
        return finish("error", f"{type(exc).__name__}: {exc}")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _print_plan(cfg, keys, paths, hashes, targets, dry_run):
    print(f"\n{'DRY RUN - ' if dry_run else ''}Deploying to {len(targets)} machine(s):")
    for key in keys:
        spec = cfg["files"][key]
        origin = f"local {paths[key]}" if spec["from"] == "local" else f"release {cfg['release']['tag']} ({paths[key].name})"
        print(f"  {REMOTE_FILENAMES[key]:<28} {hashes[key][:12]}...  <- {origin}")
    for _user, host in targets[:10]:
        print(f"    -> {_user}@{host}")
    if len(targets) > 10:
        print(f"    ... and {len(targets) - 10} more")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Push tv-signal-trader updates to VPS machines over SSH.")
    ap.add_argument("--config", default=str(DEPLOY_DIR / "deploy_config.json"))
    ap.add_argument("--targets", help="roster file, one host or user@host per line (default: config's targets_file)")
    ap.add_argument("--host", action="append", help="deploy to this host only (repeatable); overrides the roster")
    ap.add_argument("--push", help="comma-separated file keys (admin_exe,user_exe,env,accounts), "
                                   "overriding the config's push flags for this run")
    ap.add_argument("--dry-run", action="store_true", help="check every machine and print the plan; change nothing")
    ap.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    ap.add_argument("-v", "--verbose", action="store_true", help="print every step as it happens")
    args = ap.parse_args(argv)

    try:
        cfg = load_config(args.config)
        keys = selected_files(cfg, args.push)
        if not keys:
            raise ConfigError("No files selected - set \"push\": true on some file in the config, or use --push.")

        if args.host:
            targets = parse_targets("\n".join(args.host), cfg["ssh"]["user"])
        else:
            roster = Path(args.targets or cfg["targets_file"])
            roster = roster if roster.is_absolute() else REPO_ROOT / roster
            if not roster.is_file():
                raise ConfigError(f"Roster file not found: {roster} (or pass --host)")
            targets = parse_targets(roster.read_text(encoding="utf-8"), cfg["ssh"]["user"])
        if not targets:
            raise ConfigError("No target machines.")

        cache_dir = DEPLOY_DIR / "_cache"
        paths = resolve_sources(cfg, keys, cache_dir)
    except (ConfigError, SourceError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    hashes = {key: sha256_of(paths[key]) for key in keys}
    _print_plan(cfg, keys, paths, hashes, targets, args.dry_run)

    if not args.dry_run and not args.yes:
        if "env" in keys:
            print("\n!! .env holds each machine's real credentials. This OVERWRITES it on every machine above\n"
                  "   (the existing one is first copied to .env.bak next to it).")
        if input("\nType 'yes' to continue: ").strip().lower() != "yes":
            print("Aborted.")
            return 1

    print_lock = threading.Lock()

    def log(host, msg):
        if args.verbose:
            with print_lock:
                print(f"  [{host}] {msg}")

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=cfg["max_parallel"]) as pool:
        futures = {pool.submit(update_host, t, cfg, {k: paths[k] for k in keys}, hashes,
                               args.dry_run, log=log): t for t in targets}
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            results.append(res)
            with print_lock:
                print(f"  {res.host:<20} {res.status:<15} {res.detail}")

    ok = sum(1 for r in results if r.status in OK_STATUSES)
    print(f"\n{ok}/{len(results)} machine(s) OK.")
    failed = [r for r in results if r.status not in OK_STATUSES]
    for r in failed:
        print(f"  FAILED {r.host}: {r.status} - {r.detail}")

    report_dir = DEPLOY_DIR / "reports"
    report_dir.mkdir(exist_ok=True)
    report_path = report_dir / f"deploy_{datetime.datetime.now():%Y%m%d_%H%M%S}.json"
    report_path.write_text(json.dumps({
        "dry_run": args.dry_run,
        "files": {k: {"remote_name": REMOTE_FILENAMES[k], "sha256": hashes[k], "source": str(paths[k])} for k in keys},
        "results": [r.as_dict() for r in sorted(results, key=lambda r: r.host)],
    }, indent=2), encoding="utf-8")
    print(f"Report: {report_path}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
