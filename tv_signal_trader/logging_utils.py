"""Prefixes every print() call across the app with a wall-clock timestamp,
so a long console/log capture stays readable without cross-referencing
external timing. Each module imports this as `print` (`from . import
logging_utils` then `print = logging_utils.timestamped_print`, or the
equivalent `from .logging_utils import timestamped_print as print`),
shadowing the built-in locally rather than monkey-patching it globally.
"""

import builtins
import contextlib
import datetime

_suppressed = False


@contextlib.contextmanager
def suppressed():
    """Silences every timestamped_print() call (across every module that
    imports it as `print`) for the duration of the `with` block -- used by
    cli.py to hide the regular-user build's web/web_multi console output
    (see config.IS_ADMIN_BUILD) without threading a flag through every
    print call site. Not reentrant-safe across threads, but this app only
    ever has one thread driving the trading loop at a time."""
    global _suppressed
    previous = _suppressed
    _suppressed = True
    try:
        yield
    finally:
        _suppressed = previous


def timestamped_print(*args, **kwargs):
    if _suppressed:
        return
    # A leading "\n" in the first arg is a deliberate blank-line separator
    # used throughout the codebase for visual spacing -- print it on its
    # own so the timestamp still prefixes the actual message, not the
    # blank line itself.
    if args and isinstance(args[0], str) and args[0].startswith('\n'):
        builtins.print()
        args = (args[0].lstrip('\n'),) + args[1:]
    timestamp = datetime.datetime.now().strftime('%H:%M:%S')
    builtins.print(f'[{timestamp}]', *args, **kwargs)
