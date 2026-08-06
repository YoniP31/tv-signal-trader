"""Covers logging_utils.suppressed(), which cli.py uses to silence the
regular-user .exe build's web/web_multi console output (see
config.IS_ADMIN_BUILD) without threading a flag through every print call
site. Run with:

    python -m unittest tests.test_logging_utils -v
"""

import contextlib
import io
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


if __name__ == "__main__":
    unittest.main()
