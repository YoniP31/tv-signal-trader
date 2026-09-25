"""One-time, per-computer activation password, for both .exe variants.

The first time a built .exe runs on a computer, it asks for the activation
password in the terminal -- plain `input()`, so what's typed stays visible,
like every other prompt in the app (see setup_wizard._set_password for why
not getpass). After one correct entry the computer is activated and never
asks again, across restarts, updates and deploys.

What is and isn't stored:
  - The password itself is nowhere: not in .env, the code, status.json or
    app.log. The build contains only a salted, slow hash of it, made by
    tools/make_password_hash.py into tv_signal_trader/_activation_secret.py
    -- a gitignored file that exists only on the machine that builds, so the
    hash is not in the (public) repository either.
  - Activating writes a small marker file in the user's home folder (next to
    tv_profile, deliberately NOT next to the .exe, so a new versioned install
    folder such as tv-signal-trader-v1.2.0 doesn't ask again). The marker is
    an HMAC over this computer's Windows machine ID and ACTIVATION_GENERATION,
    keyed by the password hash -- so copying it to another computer doesn't
    activate that one, and changing the password invalidates every existing
    marker on its own.

This is a deterrent, not real security (same stance as the README's Nuitka
note): whoever holds the .exe can attack the hash offline -- use a long,
random password -- or patch the check out of the binary.

Only built (Nuitka-compiled) .exes are gated; running from source skips it,
so development and the test suite aren't affected.
"""

import hashlib
import hmac
import json
import os
import platform
import time
import uuid

try:
    import winreg
except ImportError:  # not Windows
    winreg = None

from . import config
from . import passhash
from .logging_utils import timestamped_print as print

# Written by tools/make_password_hash.py before building, into a gitignored
# file so it never lands in the repo. Nuitka follows this import and bundles
# the file if it exists at build time. Without it the hash is empty, and a
# built .exe with no hash refuses to run rather than silently shipping ungated.
try:
    from ._activation_secret import PASSWORD_HASH
except ImportError:
    PASSWORD_HASH = ""

# Bump this to make every already-activated computer ask again without
# changing the password (making a new hash does that by itself). It is
# NOT the release number: tying it to the release would re-ask everyone on
# every update, and this is meant to be asked once.
ACTIVATION_GENERATION = 1

MAX_ATTEMPTS = 5
_RETRY_DELAY_SECONDS = 1

MARKER_PATH = os.path.join(os.path.expanduser("~"), ".tv_signal_trader_activation")


def machine_id():
    """This computer's Windows MachineGuid, or a hostname/MAC-based
    fallback if that can't be read."""
    if winreg is not None:
        try:
            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography",
                0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
            )
            try:
                value, _ = winreg.QueryValueEx(key, "MachineGuid")
            finally:
                winreg.CloseKey(key)
            if value:
                return str(value)
        except OSError:
            pass
    return f"{platform.node()}-{uuid.getnode()}"


def _proof(generation, machine):
    key = passhash.digest_bytes(PASSWORD_HASH)
    if key is None:
        return None
    return hmac.new(key, f"{generation}|{machine}".encode("utf-8"), hashlib.sha256).hexdigest()


def is_activated():
    """True if a valid marker for THIS computer, THIS password and THIS
    generation exists. Any problem reading it counts as not activated."""
    try:
        expected = _proof(ACTIVATION_GENERATION, machine_id())
        if expected is None:
            return False
        with open(MARKER_PATH, encoding="utf-8") as f:
            data = json.load(f)
        proof = data.get("proof")
        return (data.get("generation") == ACTIVATION_GENERATION
                and isinstance(proof, str) and hmac.compare_digest(proof, expected))
    except Exception:
        return False


def _write_marker():
    """Records this computer as activated. False if it couldn't be written
    (the run still goes ahead; it will just ask again next time)."""
    try:
        data = {"generation": ACTIVATION_GENERATION,
                "proof": _proof(ACTIVATION_GENERATION, machine_id())}
        tmp = MARKER_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, MARKER_PATH)
        return True
    except OSError:
        return False


def ensure_activated(interactive=True):
    """True if this run may go ahead, False if it must exit.

    `interactive` is False when the run was started with a command-line
    command (the deploy Scheduled Task) and nobody is there to type: an
    unactivated computer then exits with a message instead of hanging --
    it needs one manual run first.

    Nothing typed is ever printed or logged."""
    if not config._RUNNING_COMPILED:
        return True

    if passhash.parse(PASSWORD_HASH) is None:
        print("[FAIL] This build has no activation password configured -- "
              "run tools/make_password_hash.py, then rebuild.")
        return False

    if is_activated():
        return True

    if not interactive:
        print("[FAIL] This computer has not been activated yet. Run the .exe "
              "once by hand and enter the activation password; after that it "
              "starts on its own.")
        return False

    print("\nThis computer has not been activated yet. Enter the activation "
          "password (asked only this one time).")
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            entered = input("Activation password: ").strip()
        except EOFError:
            return False
        if passhash.verify_password(entered, PASSWORD_HASH):
            if _write_marker():
                print("[ACTIVATION] This computer is now activated.")
            else:
                print("[WARN] Activated for this run, but the activation marker "
                      f"couldn't be saved to {MARKER_PATH} -- you'll be asked again next time.")
            return True
        print(f"[FAIL] Wrong password ({attempt}/{MAX_ATTEMPTS}).")
        if attempt < MAX_ATTEMPTS:
            time.sleep(_RETRY_DELAY_SECONDS)
    print("[FAIL] Too many wrong attempts -- exiting.")
    return False
