"""Covers logging_utils.suppressed() (which cli.py uses to silence the
regular-user .exe build's web/web_multi console output, see
config.IS_ADMIN_BUILD) and the app.log file-logging mirror (which stays
complete regardless of that suppression -- see FileLoggingTests). Run
with:

    python -m unittest tests.test_logging_utils -v
"""

import contextlib
import datetime
import io
import os
import tempfile
import unittest

from tv_signal_trader import logging_utils as lu


class SuppressedTests(unittest.TestCase):
    def test_suppresses_output_inside_the_block(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            with lu.suppressed():
                lu.timestamped_print("should not appear")
        self.assertNotIn("should not appear", buf.getvalue())

    def test_output_resumes_after_the_block(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            with lu.suppressed():
                lu.timestamped_print("suppressed")
            lu.timestamped_print("should appear")
        self.assertNotIn("suppressed", buf.getvalue())
        self.assertIn("should appear", buf.getvalue())

    def test_nested_blocks_restore_correctly(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            with lu.suppressed():
                with lu.suppressed():
                    lu.timestamped_print("inner - suppressed")
                lu.timestamped_print("outer - still suppressed")
            lu.timestamped_print("outside both - should appear")
        output = buf.getvalue()
        self.assertNotIn("inner - suppressed", output)
        self.assertNotIn("outer - still suppressed", output)
        self.assertIn("outside both - should appear", output)

    def test_restores_state_even_if_the_block_raises(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            with self.assertRaises(ValueError):
                with lu.suppressed():
                    raise ValueError("boom")
            lu.timestamped_print("should appear after the exception")
        self.assertIn("should appear after the exception", buf.getvalue())

    def test_unsuppressed_output_is_unaffected(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            lu.timestamped_print("normal output")
        self.assertIn("normal output", buf.getvalue())


class FileLoggingTests(unittest.TestCase):
    """logging_utils mirrors every timestamped_print() call into app.log,
    leveled and cleaned up (no terminal indentation/blank-line spacers) --
    and, critically, does this regardless of suppressed(), since the
    regular-user .exe build needs a complete debug trail on disk even with
    a silent console (see config.IS_ADMIN_BUILD)."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.log_path = os.path.join(self._tmpdir.name, "app.log")
        lu._configure_file_handler(self.log_path)

    def tearDown(self):
        # Point the logger at some other writable temp file rather than
        # the (about to be deleted) per-test one above, so whatever runs
        # next doesn't hit a closed/missing file.
        fallback_dir = tempfile.mkdtemp(prefix="tv_signal_trader_tests_")
        lu._configure_file_handler(os.path.join(fallback_dir, "app.log"))

    def _read_log(self):
        for handler in lu._logger.handlers:
            handler.flush()
        with open(self.log_path, encoding="utf-8") as f:
            return f.read()

    def test_plain_message_logged_at_info_level(self):
        lu.timestamped_print("Chart loaded")
        log = self._read_log()
        self.assertIn(" INFO  Chart loaded", log)

    def test_multiple_args_are_joined_like_print(self):
        lu.timestamped_print("Chart loaded:", "some-title")
        self.assertIn("Chart loaded: some-title", self._read_log())

    def test_warn_prefix_is_logged_as_warning_and_stripped(self):
        lu.timestamped_print("  [WARN] something bad happened")
        log = self._read_log()
        self.assertIn(" WARN  something bad happened", log)
        self.assertNotIn("[WARN] something bad happened", log)

    def test_fail_prefix_is_logged_as_error_and_stripped(self):
        lu.timestamped_print("  [FAIL] could not connect")
        log = self._read_log()
        self.assertIn(" ERROR could not connect", log)
        self.assertNotIn("[FAIL] could not connect", log)

    def test_warning_colon_prefix_is_logged_as_warning_and_stripped(self):
        lu.timestamped_print("  WARNING: could not find a button")
        log = self._read_log()
        self.assertIn(" WARN  could not find a button", log)
        self.assertNotIn("WARNING: could not find a button", log)

    def test_leading_blank_line_spacer_does_not_leave_a_stray_entry(self):
        lu.timestamped_print("\n[RECOVER] resolved")
        log = self._read_log()
        self.assertIn("[RECOVER] resolved", log)
        # Exactly one log record for this call, not a blank second one.
        self.assertEqual(log.count(" INFO "), 1)

    def test_file_logging_happens_even_when_console_is_suppressed(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            with lu.suppressed():
                lu.timestamped_print("hidden from console but logged")
        self.assertNotIn("hidden from console but logged", buf.getvalue())
        self.assertIn("hidden from console but logged", self._read_log())

    def test_log_exception_records_the_traceback(self):
        try:
            raise ValueError("boom")
        except ValueError:
            lu.log_exception("something went wrong")
        log = self._read_log()
        self.assertIn(" ERROR something went wrong", log)
        self.assertIn("Traceback (most recent call last):", log)
        self.assertIn("ValueError: boom", log)


class IsraelTimeConverterTests(unittest.TestCase):
    """_israel_time_converter is what makes app.log's timestamps Israel
    time regardless of the host machine's own system timezone -- e.g. a
    VPS that isn't itself configured for Israel time. Computed purely from
    the UTC epoch + zoneinfo, never touching time.localtime, so this is
    correct no matter what timezone the test runner's own machine is in."""

    def test_converts_a_known_utc_instant_to_israel_wall_clock_time(self):
        import time as time_module
        from zoneinfo import ZoneInfo

        # 2026-08-17 10:00:00 UTC -- August, so Israel is in DST (UTC+3).
        utc_dt = datetime.datetime(2026, 8, 17, 10, 0, 0, tzinfo=datetime.timezone.utc)
        expected = utc_dt.astimezone(ZoneInfo("Asia/Jerusalem")).strftime("%Y-%m-%d %H:%M:%S")

        result = time_module.strftime("%Y-%m-%d %H:%M:%S", lu._israel_time_converter(utc_dt.timestamp()))

        self.assertEqual(result, expected)
        self.assertEqual(result, "2026-08-17 13:00:00")


class SessionMarkerBlankLineTests(unittest.TestCase):
    """A blank line is written directly ahead of each session's own
    "=== Session started ===" line once app.log already has content from a
    previous session -- purely a visual spacer when scrolling through
    multiple runs, not a log record of its own."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.log_path = os.path.join(self._tmpdir.name, "app.log")

    def tearDown(self):
        fallback_dir = tempfile.mkdtemp(prefix="tv_signal_trader_tests_")
        lu._configure_file_handler(os.path.join(fallback_dir, "app.log"))

    def _start_session(self, pid):
        if os.path.exists(self.log_path) and os.path.getsize(self.log_path) > 0:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write("\n")
        lu._configure_file_handler(self.log_path)
        lu._logger.info("=== Session started (PID %d, build=admin) ===", pid)
        for handler in lu._logger.handlers:
            handler.flush()

    def test_first_session_on_an_empty_file_has_no_leading_blank_line(self):
        self._start_session(111)
        with open(self.log_path, encoding="utf-8") as f:
            content = f.read()
        self.assertFalse(content.startswith("\n"))

    def test_second_session_gets_a_blank_line_before_its_marker(self):
        self._start_session(111)
        self._start_session(222)
        with open(self.log_path, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("PID 111", content.split("\n\n")[0])
        self.assertIn("PID 222", content.split("\n\n")[1])


if __name__ == "__main__":
    unittest.main()
