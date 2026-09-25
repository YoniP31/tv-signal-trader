"""Salted, deliberately slow password hashing for the one-time activation
password (see activation.py). Standard library only, and importing this
module touches nothing else in the app -- so tools/make_password_hash.py can
load it straight from its file, without the app's logging/config side
effects, and get exactly the same hashing the app verifies with.

A stored hash is one self-describing string:

    pbkdf2_sha256$<iterations>$<salt hex>$<hash hex>

The password itself is never stored, logged, or recoverable from it -- but a
hash can still be attacked offline by anyone holding the .exe, so the
password should be long and random.
"""

import hashlib
import hmac
import os

ITERATIONS = 600_000
_SCHEME = "pbkdf2_sha256"


def hash_password(password, salt=None, iterations=ITERATIONS):
    """The storable hash string for `password`. A fresh random salt is used
    unless one is given (tests only)."""
    salt = os.urandom(16) if salt is None else salt
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"{_SCHEME}${iterations}${salt.hex()}${digest.hex()}"


def parse(stored):
    """(iterations, salt bytes, digest bytes) from a stored hash string, or
    None if it isn't one -- never raises."""
    try:
        scheme, iterations, salt_hex, digest_hex = stored.split("$")
        iterations = int(iterations)
        if scheme != _SCHEME or iterations < 1:
            return None
        return iterations, bytes.fromhex(salt_hex), bytes.fromhex(digest_hex)
    except (AttributeError, ValueError):
        return None


def verify_password(password, stored):
    """True only if `password` matches the stored hash. False for anything
    else, including a malformed or empty stored value. Constant-time
    comparison."""
    parsed = parse(stored)
    if parsed is None:
        return False
    iterations, salt, expected = parsed
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)


def digest_bytes(stored):
    """The raw hash bytes inside a stored string (used as a key for the
    per-computer activation marker), or None if it isn't valid."""
    parsed = parse(stored)
    return parsed[2] if parsed else None
