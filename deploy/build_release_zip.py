#!/usr/bin/env python
"""build_release_zip.py -- builds the tv-signal-trader-vX.Y.Z.zip attached to
each GitHub release, from one place, so a new shipped file (like
watchdog.ps1) can't be forgotten on some future release the way it would be
by hand-writing the zip's file list from memory each time.

    python deploy/build_release_zip.py --version v1.4.0

Every member is one folder, tv-signal-trader/, holding:
  - both .exe variants, built beforehand (see build.ps1) -- dist/*.exe
  - the .env settings template -- docs/.env
  - the accounts_to_add.txt template -- docs/accounts_to_add.txt
  - watchdog.ps1 -- deploy/watchdog.ps1 (lives next to the exe(s) on a VPS;
    see setup_vps.ps1's watchdog Scheduled Task step)

--exe-dir/--docs-dir/--deploy-dir override where those come from (tests use
this to point at a throwaway fixture instead of the real repo); the normal
build needs none of them.
"""

import argparse
import hashlib
import sys
import time
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FOLDER_NAME = "tv-signal-trader"

# member path (inside FOLDER_NAME/) -> (source directory kwarg, filename in
# that directory). Single source of truth for what a release ships.
MEMBERS = {
    "tv-signal-trader.exe": ("exe_dir", "tv-signal-trader.exe"),
    "tv-signal-trader-user.exe": ("exe_dir", "tv-signal-trader-user.exe"),
    ".env": ("docs_dir", ".env"),
    "accounts_to_add.txt": ("docs_dir", "accounts_to_add.txt"),
    "watchdog.ps1": ("deploy_dir", "watchdog.ps1"),
}


class BuildError(Exception):
    pass


def resolve_sources(exe_dir, docs_dir, deploy_dir):
    """{member path: source Path}, or raises BuildError naming every file
    that's missing (all of them, not just the first) so a build failure
    says everything wrong in one go."""
    dirs = {"exe_dir": Path(exe_dir), "docs_dir": Path(docs_dir), "deploy_dir": Path(deploy_dir)}
    sources, missing = {}, []
    for member, (dir_key, filename) in MEMBERS.items():
        path = dirs[dir_key] / filename
        if path.is_file():
            sources[member] = path
        else:
            missing.append(str(path))
    if missing:
        raise BuildError("Missing file(s):\n  " + "\n  ".join(missing))
    return sources


def build_zip(sources, output_path):
    """Writes the release zip to `output_path` from {member path: source
    Path} (see resolve_sources). Deterministic file order (MEMBERS') and a
    plain 644/755 permission bits, like an ordinary extracted archive."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    now = time.localtime()[:6]

    def info(name, is_dir=False):
        zi = zipfile.ZipInfo(name, date_time=now)
        if is_dir:
            zi.external_attr = (0o40755 << 16) | 0x10
        else:
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = 0o644 << 16
        return zi

    with zipfile.ZipFile(output_path, "w") as zf:
        zf.writestr(info(f"{FOLDER_NAME}/", is_dir=True), b"")
        for member in MEMBERS:  # dict order = the declared, deterministic order
            zf.writestr(info(f"{FOLDER_NAME}/{member}"), sources[member].read_bytes())
    return output_path


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build the release zip attached to a GitHub release.")
    ap.add_argument("--version", help='e.g. "v1.4.0" -- names the zip tv-signal-trader-<version>.zip')
    ap.add_argument("--output", type=Path, help="exact output path (overrides --version's default naming)")
    ap.add_argument("--exe-dir", default=REPO_ROOT / "dist", help="default: dist/")
    ap.add_argument("--docs-dir", default=REPO_ROOT / "docs", help="default: docs/")
    ap.add_argument("--deploy-dir", default=REPO_ROOT / "deploy", help="default: deploy/")
    args = ap.parse_args(argv)

    if args.output:
        output_path = args.output
    elif args.version:
        output_path = REPO_ROOT / "deploy" / "_cache" / f"{FOLDER_NAME}-{args.version}.zip"
    else:
        ap.error("pass --version (e.g. v1.4.0) or an explicit --output path")

    try:
        sources = resolve_sources(args.exe_dir, args.docs_dir, args.deploy_dir)
        build_zip(sources, output_path)
    except BuildError as exc:
        print(f"[FAIL] {exc}")
        return 1

    print(f"Wrote {output_path} ({output_path.stat().st_size:,} bytes)")
    for member, source in sources.items():
        print(f"  {member:<28} <- {source}  (sha256 {sha256_of(source)[:12]}...)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
