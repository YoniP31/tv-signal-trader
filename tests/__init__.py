import atexit
import os
import shutil
import tempfile

from tv_signal_trader import logging_utils

# Redirect the app-wide file logger to a throwaway temp location for the
# whole test run, instead of the real app.log next to the repo's
# .env/status.json -- the tests exercise plenty of real (non-mocked)
# print()/timestamped_print() calls, which would otherwise mix test noise
# into real usage history every time the suite runs.
_test_log_dir = tempfile.mkdtemp(prefix="tv_signal_trader_tests_")
logging_utils._configure_file_handler(os.path.join(_test_log_dir, "app.log"))
atexit.register(shutil.rmtree, _test_log_dir, ignore_errors=True)
