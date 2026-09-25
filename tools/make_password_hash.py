#!/usr/bin/env python
"""Sets the activation password for the next build: hashes the password you
type and writes the hash to tv_signal_trader/_activation_secret.py, which is
gitignored (this repo is public) and gets bundled into the .exe when you
build.

    python tools/make_password_hash.py

You type the password here, on your own machine -- it is never sent, saved
or printed back; only its salted hash is written to that file. Nobody else
needs to see the password to build the app with it.

The password is typed as plain visible input (like the app's own prompts;
the Windows hidden-input prompt mishandles pasted text). Pass --hidden to
use it anyway. Use a long, random password: the hash ends up inside the .exe,
where it can be attacked offline.
"""

import argparse
import getpass
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = ROOT / "tv_signal_trader" / "_activation_secret.py"
MIN_RECOMMENDED_LENGTH = 12


def _load_passhash():
    # Loaded straight from its file so importing the tv_signal_trader package
    # (and its logging/config side effects) is never needed here.
    spec = importlib.util.spec_from_file_location("passhash", ROOT / "tv_signal_trader" / "passhash.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main(argv=None):
    ap = argparse.ArgumentParser(description="Set the activation password for the next build (writes its hash to a gitignored module).")
    ap.add_argument("--hidden", action="store_true", help="hide what you type (mishandles pasted text on Windows)")
    ap.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="where to write the hash (default: the gitignored module the app imports)")
    args = ap.parse_args(argv)

    ask = getpass.getpass if args.hidden else input
    password = ask("New activation password: ").strip()
    if not password:
        print("The password can't be empty.")
        return 1
    if ask("Type it again to confirm: ").strip() != password:
        print("The two entries don't match - nothing was made.")
        return 1
    if len(password) < MIN_RECOMMENDED_LENGTH:
        print(f"Warning: shorter than {MIN_RECOMMENDED_LENGTH} characters. The hash goes inside the .exe and "
              "can be attacked offline -- a long random password is much safer.")

    passhash = _load_passhash()
    stored = passhash.hash_password(password)
    assert passhash.verify_password(password, stored)

    replaced = args.output.exists()
    args.output.write_text(
        "# Written by tools/make_password_hash.py -- gitignored, never commit this file.\n"
        f'PASSWORD_HASH = "{stored}"\n',
        encoding="utf-8",
    )
    print(f"\n{'Replaced' if replaced else 'Wrote'} {args.output}")
    print("Rebuild the .exe(s) to use it. It is gitignored: never commit it.")
    print("Changing the password invalidates every computer's existing activation on its own. To make")
    print("everyone re-enter the SAME password, bump ACTIVATION_GENERATION in tv_signal_trader/activation.py.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
