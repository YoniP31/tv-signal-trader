"""Prefixes every print() call across the app with a wall-clock timestamp,
so a long console/log capture stays readable without cross-referencing
external timing. Each module imports this as `print` (`from . import
logging_utils` then `print = logging_utils.timestamped_print`, or the
equivalent `from .logging_utils import timestamped_print as print`),
shadowing the built-in locally rather than monkey-patching it globally.
"""

import builtins
import datetime


def timestamped_print(*args, **kwargs):
    # A leading "\n" in the first arg is a deliberate blank-line separator
    # used throughout the codebase for visual spacing -- print it on its
    # own so the timestamp still prefixes the actual message, not the
    # blank line itself.
    if args and isinstance(args[0], str) and args[0].startswith('\n'):
        builtins.print()
        args = (args[0].lstrip('\n'),) + args[1:]
    timestamp = datetime.datetime.now().strftime('%H:%M:%S')
    builtins.print(f'[{timestamp}]', *args, **kwargs)
