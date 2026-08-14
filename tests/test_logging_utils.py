"""Covers logging_utils.suppressed() (which cli.py uses to silence the
regular-user .exe build's web/web_multi console output, see
config.IS_ADMIN_BUILD) and the app.log file-logging mirror (which stays
complete regardless of that suppression -- see FileLoggingTests). Run
with:

    python -m unittest tests.test_logging_utils -v
"""

import contextlib
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


if __name__ == "__main__":
    unittest.main()
