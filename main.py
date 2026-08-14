import sys

from tv_signal_trader import logging_utils
from tv_signal_trader.cli import main

if __name__ == "__main__":
    # Windows consoles (esp. a plain cmd.exe running a double-clicked .exe)
    # often default to a legacy codepage that can't encode the checkmark/emoji
    # characters used in status messages throughout the app, which would
    # otherwise crash with UnicodeEncodeError on the first such print.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    try:
        main()
    except Exception:
        # Catches anything that crashes before/outside the trading loop's
        # own try/except (e.g. browser.create_driver() failing) -- without
        # this, such a crash would only ever show up as a bare traceback on
        # the console, never recorded in app.log.
        logging_utils.log_exception("Unhandled exception - app is exiting")
        raise
