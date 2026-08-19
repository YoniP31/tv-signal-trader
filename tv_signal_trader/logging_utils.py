"""Prefixes every print() call across the app with a wall-clock timestamp,
so a long console/log capture stays readable without cross-referencing
external timing. Each module imports this as `print` (`from . import
logging_utils` then `print = logging_utils.timestamped_print`, or the
equivalent `from .logging_utils import timestamped_print as print`),
shadowing the built-in locally rather than monkey-patching it globally.

Also mirrors every message into a rotating file log (app.log, next to
.env/status.json) at an appropriate severity level, independent of
console output -- unaffected by suppressed() below (the regular-user
.exe build's silent web/web_multi), and identical in both .exe variants.
This is what "help me debug in case of an error" actually needs: a
persistent, leveled record that survives after the console scrolls away
or, for the user build, was never shown at all.
"""

import builtins
import contextlib
import datetime
import logging
import logging.handlers
import os

from . import config

_suppressed = False

logging.addLevelName(logging.WARNING, "WARN")


def _israel_time_converter(timestamp):
    """Used as _FORMATTER.converter below so app.log's timestamps are
    always Israel time, regardless of the host machine's own system
    timezone -- otherwise a VPS running in a different timezone would
    silently produce misleading log times. `timestamp` is a UTC epoch
    float (logging.Formatter calls this the same way as time.localtime),
    and this must return a plain time.struct_time, same as that."""
    return datetime.datetime.fromtimestamp(timestamp, tz=config.SESSION_TIMEZONE).timetuple()


_FORMATTER = logging.Formatter(
    fmt="%(asctime)s %(levelname)-5s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
)
_FORMATTER.converter = _israel_time_converter

_logger = logging.getLogger("tv_signal_trader")
_logger.setLevel(logging.INFO)
_logger.propagate = False


def _configure_file_handler(log_path):
    """(Re)points the file logger at `log_path` -- called once below with
    the real app.log location, and by the test suite (tests/__init__.py)
    to redirect it to a throwaway temp file instead, since running the
    tests exercises plenty of real (non-mocked) print() calls that would
    otherwise clutter the real app.log next to the repo's .env/status.json.
    """
    for handler in list(_logger.handlers):
        _logger.removeHandler(handler)
        handler.close()
    handler = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(_FORMATTER)
    _logger.addHandler(handler)
    return handler


_LOG_PATH = os.path.join(config.APP_DIR, "app.log")
if os.path.exists(_LOG_PATH) and os.path.getsize(_LOG_PATH) > 0:
    # A blank line ahead of each session's own first line, so scrolling
    # through app.log across multiple runs is easier to visually pick
    # apart -- skipped for a brand new/empty file, nothing to separate
    # from yet. Written directly (bypassing the formatter) since this is
    # a plain visual spacer, not a log record of its own.
    with open(_LOG_PATH, "a", encoding="utf-8") as _f:
        _f.write("\n")
_configure_file_handler(_LOG_PATH)
_logger.info(
    "=== Session started (PID %d, build=%s) ===",
    os.getpid(), "admin" if config.IS_ADMIN_BUILD else "user",
)


@contextlib.contextmanager
def suppressed():
    """Silences every timestamped_print() call's *console* output (across
    every module that imports it as `print`) for the duration of the
    `with` block -- used by cli.py to hide the regular-user build's
    web/web_multi console output (see config.IS_ADMIN_BUILD) without
    threading a flag through every print call site. The file log (above)
    keeps recording regardless -- see timestamped_print. Not reentrant-
    safe across threads, but this app only ever has one thread driving the
    trading loop at a time."""
    global _suppressed
    previous = _suppressed
    _suppressed = True
    try:
        yield
    finally:
        _suppressed = previous


# Recognized severity markers already used throughout the codebase's print
# calls (e.g. print(f"  [WARN] ...")) -- stripped from the logged message
# since the log line's own level field already says the same thing, rather
# than repeating it. Checked in order since '[FAIL]' also happens to start
# with the same bracket style as '[WARN]'.
_LEVEL_MARKERS = (
    ("[FAIL] ", logging.ERROR),
    ("[WARN] ", logging.WARNING),
    ("WARNING: ", logging.WARNING),
)


def _log_to_file(args):
    if not args:
        return
    # Mirrors builtins.print's default sep=' ' joining -- no call site in
    # this codebase passes a custom sep/end, so that's the only case worth
    # handling.
    text = " ".join(str(a) for a in args).lstrip("\n").strip()
    if not text:
        return
    for marker, level in _LEVEL_MARKERS:
        if text.startswith(marker):
            _logger.log(level, text[len(marker):])
            return
    _logger.info(text)


def log_exception(message):
    """Logs `message` at ERROR level with the currently-handled exception's
    full traceback attached -- call this from inside an `except` block.
    Console output (traceback.print_exc(), a short [FAIL] summary line) is
    separate and unaffected; this is what actually lands in app.log for
    later debugging, since traceback.print_exc() alone writes straight to
    stderr and never reaches the log file."""
    _logger.error(message, exc_info=True)


def timestamped_print(*args, **kwargs):
    # Always logs to app.log, regardless of suppressed() below -- unlike
    # console output, the file log must stay complete in both the admin
    # and regular-user .exe builds.
    _log_to_file(args)

    if _suppressed:
        return
    # A leading "\n" in the first arg is a deliberate blank-line separator
    # used throughout the codebase for visual spacing -- print it on its
    # own so the timestamp still prefixes the actual message, not the
    # blank line itself.
    if args and isinstance(args[0], str) and args[0].startswith('\n'):
        builtins.print()
        args = (args[0].lstrip('\n'),) + args[1:]
    # Israel time, not the host machine's local time -- otherwise a VPS
    # running in a different timezone would show console timestamps out of
    # step with app.log's (see _israel_time_converter above), and with the
    # trading-session window, which is always Israel time regardless of
    # where the bot happens to be running.
    timestamp = config.now_in_israel().strftime('%H:%M:%S')
    builtins.print(f'[{timestamp}]', *args, **kwargs)
