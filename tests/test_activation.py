"""Covers the one-time activation password: passhash (salted slow hashing),
activation.ensure_activated (the gate, the per-computer marker, its
generation number), the cli.main() gate, and tools/make_password_hash.py.
Run with:

    python -m unittest tests.test_activation -v
"""

import importlib.util
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

from tv_signal_trader import activation, cli, config, passhash

ROOT = Path(__file__).resolve().parent.parent
PASSWORD = "correct horse battery staple"
FAST = 1000  # PBKDF2 iterations -- the real 600k would make each test crawl


class PasshashTests(unittest.TestCase):
    def test_the_right_password_verifies(self):
        stored = passhash.hash_password(PASSWORD, iterations=FAST)
        self.assertTrue(passhash.verify_password(PASSWORD, stored))

    def test_a_wrong_password_does_not(self):
        stored = passhash.hash_password(PASSWORD, iterations=FAST)
        for wrong in ("", "wrong", PASSWORD + " ", PASSWORD.upper()):
            self.assertFalse(passhash.verify_password(wrong, stored), repr(wrong))

    def test_each_hash_gets_its_own_random_salt(self):
        self.assertNotEqual(passhash.hash_password(PASSWORD, iterations=FAST),
                            passhash.hash_password(PASSWORD, iterations=FAST))

    def test_the_stored_string_does_not_contain_the_password(self):
        self.assertNotIn(PASSWORD, passhash.hash_password(PASSWORD, iterations=FAST))

    def test_the_stored_string_is_self_describing(self):
        scheme, iterations, salt, digest = passhash.hash_password(PASSWORD, iterations=FAST).split("$")
        self.assertEqual((scheme, iterations), ("pbkdf2_sha256", str(FAST)))
        self.assertEqual(len(bytes.fromhex(salt)), 16)
        self.assertEqual(len(bytes.fromhex(digest)), 32)

    def test_the_default_cost_is_high(self):
        self.assertGreaterEqual(passhash.ITERATIONS, 600_000)

    def test_a_malformed_stored_value_never_verifies_and_never_raises(self):
        for bad in ("", "nonsense", "a$b$c$d", "pbkdf2_sha256$x$00$00", "pbkdf2_sha256$0$00$00",
                    "pbkdf2_sha256$1000$zz$00", None, 123):
            self.assertFalse(passhash.verify_password(PASSWORD, bad), repr(bad))
            self.assertIsNone(passhash.parse(bad), repr(bad))

    def test_digest_bytes_is_the_hash_part(self):
        stored = passhash.hash_password(PASSWORD, iterations=FAST)
        self.assertEqual(passhash.digest_bytes(stored).hex(), stored.split("$")[3])
        self.assertIsNone(passhash.digest_bytes("nonsense"))


class _ActivationCase(unittest.TestCase):
    """A compiled build on computer 'machine-A' with a fast test hash and a
    throwaway marker location. Nothing sleeps or touches the real home dir."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.marker = os.path.join(self._tmp.name, "marker.json")
        self.hash = passhash.hash_password(PASSWORD, iterations=FAST)
        self.machine = "machine-A"
        patches = [
            patch.object(config, "_RUNNING_COMPILED", True),
            patch.object(activation, "PASSWORD_HASH", self.hash),
            patch.object(activation, "ACTIVATION_GENERATION", 1),
            patch.object(activation, "MARKER_PATH", self.marker),
            patch.object(activation, "machine_id", lambda: self.machine),
            patch.object(activation.time, "sleep"),
            patch.object(activation, "print"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def _printed(self):
        return " ".join(str(a) for c in activation.print.call_args_list for a in c.args)

    def _activate(self, *typed, interactive=True):
        with patch("builtins.input", side_effect=list(typed)) as ask:
            result = activation.ensure_activated(interactive=interactive)
        return result, ask


class FirstRunTests(_ActivationCase):
    def test_the_first_run_asks_and_the_right_password_activates(self):
        result, ask = self._activate(PASSWORD)
        self.assertTrue(result)
        self.assertEqual(ask.call_count, 1)
        self.assertTrue(os.path.exists(self.marker))

    def test_it_never_asks_again_once_activated(self):
        self._activate(PASSWORD)
        result, ask = self._activate()
        self.assertTrue(result)
        ask.assert_not_called()

    def test_the_prompt_is_plain_input_not_hidden(self):
        # Visible typing, like every other prompt in the app.
        with patch("builtins.input", return_value=PASSWORD) as ask, \
             patch("getpass.getpass") as hidden:
            activation.ensure_activated()
        ask.assert_called_once()
        hidden.assert_not_called()

    def test_stray_whitespace_around_a_pasted_password_is_ignored(self):
        self.assertTrue(self._activate(f"  {PASSWORD}  ")[0])

    def test_a_wrong_password_then_the_right_one_works(self):
        result, ask = self._activate("nope", PASSWORD)
        self.assertTrue(result)
        self.assertEqual(ask.call_count, 2)

    def test_an_empty_entry_counts_as_a_wrong_attempt(self):
        result, ask = self._activate("", PASSWORD)
        self.assertTrue(result)
        self.assertEqual(ask.call_count, 2)

    def test_it_gives_up_after_the_maximum_number_of_wrong_attempts(self):
        result, ask = self._activate(*["wrong"] * activation.MAX_ATTEMPTS)
        self.assertFalse(result)
        self.assertEqual(ask.call_count, activation.MAX_ATTEMPTS)
        self.assertFalse(os.path.exists(self.marker))

    def test_a_correct_password_after_the_limit_is_not_even_asked_for(self):
        result, ask = self._activate(*["wrong"] * activation.MAX_ATTEMPTS, PASSWORD)
        self.assertFalse(result)
        self.assertEqual(ask.call_count, activation.MAX_ATTEMPTS)

    def test_it_pauses_between_wrong_attempts_but_not_after_the_last(self):
        self._activate(*["wrong"] * activation.MAX_ATTEMPTS)
        self.assertEqual(activation.time.sleep.call_count, activation.MAX_ATTEMPTS - 1)

    def test_no_input_available_means_not_activated(self):
        with patch("builtins.input", side_effect=EOFError):
            self.assertFalse(activation.ensure_activated())

    def test_a_marker_that_cannot_be_written_still_lets_this_run_go_ahead(self):
        with patch.object(activation, "MARKER_PATH", os.path.join(self._tmp.name, "no", "such", "dir", "m")):
            result, _ = self._activate(PASSWORD)
        self.assertTrue(result)
        self.assertIn("ask", self._printed().lower())


class SecrecyTests(_ActivationCase):
    def test_nothing_typed_is_ever_printed_or_logged(self):
        self._activate("a-wrong-guess-123", PASSWORD)
        printed = self._printed()
        for secret in ("a-wrong-guess-123", PASSWORD):
            self.assertNotIn(secret, printed)

    def test_nothing_typed_reaches_the_marker_file(self):
        self._activate(PASSWORD)
        with open(self.marker, encoding="utf-8") as f:
            content = f.read()
        self.assertNotIn(PASSWORD, content)
        self.assertNotIn(self.hash, content)

    def test_no_password_hash_is_written_into_the_source(self):
        # The hash comes from a gitignored module written at build time --
        # this repo is public, so it must never be committed.
        source = (ROOT / "tv_signal_trader" / "activation.py").read_text(encoding="utf-8")
        self.assertNotIn("pbkdf2_sha256$", source)

    def test_the_generated_secret_module_is_gitignored(self):
        ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn("/tv_signal_trader/_activation_secret.py", [line.strip() for line in ignored])

    def test_a_missing_secret_module_means_an_empty_hash_not_a_crash(self):
        # A source checkout (or a build made without running the tool) has no
        # _activation_secret.py: importing activation must still work.
        self.assertIsInstance(activation.PASSWORD_HASH, str)


class MarkerBindingTests(_ActivationCase):
    def test_a_marker_copied_to_another_computer_does_not_activate_it(self):
        self._activate(PASSWORD)
        self.machine = "machine-B"
        result, ask = self._activate(PASSWORD)
        self.assertTrue(result)
        self.assertEqual(ask.call_count, 1)  # had to ask again

    def test_bumping_the_generation_asks_everyone_again(self):
        self._activate(PASSWORD)
        with patch.object(activation, "ACTIVATION_GENERATION", 2):
            result, ask = self._activate(PASSWORD)
        self.assertTrue(result)
        self.assertEqual(ask.call_count, 1)

    def test_the_new_generation_then_sticks(self):
        with patch.object(activation, "ACTIVATION_GENERATION", 2):
            self._activate(PASSWORD)
            result, ask = self._activate()
        self.assertTrue(result)
        ask.assert_not_called()

    def test_changing_the_password_invalidates_existing_markers(self):
        self._activate(PASSWORD)
        with patch.object(activation, "PASSWORD_HASH", passhash.hash_password("a new password!", iterations=FAST)):
            result, ask = self._activate("a new password!")
        self.assertTrue(result)
        self.assertEqual(ask.call_count, 1)

    def test_a_corrupt_marker_just_asks_again(self):
        for junk in ("", "not json", "[]", '{"generation": 1}', '{"generation": 1, "proof": 5}',
                     '{"generation": "1", "proof": "abc"}', '{"generation": 1, "proof": "\\u00e9"}'):
            with open(self.marker, "w", encoding="utf-8") as f:
                f.write(junk)
            result, ask = self._activate(PASSWORD)
            self.assertTrue(result, junk)
            self.assertEqual(ask.call_count, 1, junk)

    def test_a_hand_made_marker_with_a_wrong_proof_is_rejected(self):
        with open(self.marker, "w", encoding="utf-8") as f:
            json.dump({"generation": 1, "proof": "0" * 64}, f)
        self.assertFalse(activation.is_activated())

    def test_the_marker_is_bound_to_the_machine_id_and_generation(self):
        self._activate(PASSWORD)
        with open(self.marker, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["generation"], 1)
        self.assertEqual(data["proof"], activation._proof(1, "machine-A"))
        self.assertNotEqual(data["proof"], activation._proof(1, "machine-B"))
        self.assertNotEqual(data["proof"], activation._proof(2, "machine-A"))


class GateRulesTests(_ActivationCase):
    def test_running_from_source_is_never_gated(self):
        with patch.object(config, "_RUNNING_COMPILED", False), \
             patch("builtins.input") as ask:
            self.assertTrue(activation.ensure_activated())
        ask.assert_not_called()

    def test_a_build_with_no_password_hash_refuses_to_run(self):
        with patch.object(activation, "PASSWORD_HASH", ""), \
             patch("builtins.input") as ask:
            self.assertFalse(activation.ensure_activated())
        ask.assert_not_called()
        self.assertIn("no activation password", self._printed())

    def test_an_unattended_start_on_an_unactivated_computer_exits_without_asking(self):
        result, ask = self._activate(interactive=False)
        self.assertFalse(result)
        ask.assert_not_called()
        self.assertIn("run the .exe", self._printed().lower())

    def test_an_unattended_start_on_an_activated_computer_just_runs(self):
        self._activate(PASSWORD)
        result, ask = self._activate(interactive=False)
        self.assertTrue(result)
        ask.assert_not_called()


class MachineIdTests(unittest.TestCase):
    def test_it_is_stable_and_non_empty(self):
        self.assertTrue(activation.machine_id())
        self.assertEqual(activation.machine_id(), activation.machine_id())

    def test_it_falls_back_when_the_registry_cannot_be_read(self):
        with patch.object(activation, "winreg", None):
            fallback = activation.machine_id()
        self.assertIn("-", fallback)
        broken = MagicMock()
        broken.OpenKey.side_effect = OSError("no such key")
        with patch.object(activation, "winreg", broken):
            self.assertEqual(activation.machine_id(), fallback)


class MainGateTests(unittest.TestCase):
    """cli.main() checks activation first, before configuring anything or
    opening the browser."""

    def _main(self, argv, activated):
        with patch.object(cli.sys, "argv", argv), \
             patch.object(cli.activation, "ensure_activated", return_value=activated) as gate, \
             patch.object(cli.setup_wizard, "ensure_configured") as configure, \
             patch.object(cli.browser, "create_driver") as create_driver, \
             patch.object(cli, "_pause_before_exit") as pause:
            with self.assertRaises(SystemExit) as ctx:
                cli.main()
        return gate, configure, create_driver, pause, ctx.exception.code

    def test_a_refused_run_exits_before_anything_else_starts(self):
        gate, configure, create_driver, pause, code = self._main(["tv-signal-trader.exe"], activated=False)
        self.assertEqual(code, 1)
        configure.assert_not_called()
        create_driver.assert_not_called()

    def test_an_interactive_refusal_keeps_the_window_open_to_read_why(self):
        gate, _c, _d, pause, _code = self._main(["tv-signal-trader.exe"], activated=False)
        gate.assert_called_once_with(interactive=True)
        pause.assert_called_once()

    def test_an_unattended_refusal_does_not_wait_for_a_keypress(self):
        gate, _c, _d, pause, _code = self._main(["tv-signal-trader.exe", "web_multi"], activated=False)
        gate.assert_called_once_with(interactive=False)
        pause.assert_not_called()

    def test_an_unrecognized_argument_is_still_treated_as_interactive(self):
        gate, _c, _d, _p, _code = self._main(["tv-signal-trader.exe", "whatever"], activated=False)
        gate.assert_called_once_with(interactive=True)


class MakePasswordHashToolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = ROOT / "tools" / "make_password_hash.py"
        spec = importlib.util.spec_from_file_location("make_password_hash", path)
        cls.tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.tool)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.output = Path(self._tmp.name) / "_activation_secret.py"

    def _run(self, *typed):
        out = io.StringIO()
        with patch("builtins.input", side_effect=list(typed)), redirect_stdout(out):
            code = self.tool.main(["--output", str(self.output)])
        return code, out.getvalue()

    def _written_hash(self):
        # Executed from its text, not imported: two same-sized files written
        # within one second would otherwise hit Python's bytecode cache and
        # return the first one's value again.
        namespace = {}
        exec(compile(self.output.read_text(encoding="utf-8"), str(self.output), "exec"), namespace)
        return namespace["PASSWORD_HASH"]

    def test_it_writes_a_module_whose_hash_the_app_accepts(self):
        code, _output = self._run(PASSWORD, PASSWORD)
        self.assertEqual(code, 0)
        stored = self._written_hash()
        self.assertTrue(passhash.verify_password(PASSWORD, stored))
        self.assertFalse(passhash.verify_password("something else", stored))

    def test_it_never_prints_or_writes_the_password_or_the_hash_to_the_console(self):
        _code, output = self._run(PASSWORD, PASSWORD)
        self.assertNotIn(PASSWORD, output)
        self.assertNotIn("pbkdf2_sha256$", output)
        self.assertNotIn(PASSWORD, self.output.read_text(encoding="utf-8"))

    def test_it_writes_to_the_gitignored_module_by_default(self):
        self.assertEqual(self.tool.DEFAULT_OUTPUT, ROOT / "tv_signal_trader" / "_activation_secret.py")

    def test_running_it_again_replaces_the_previous_hash(self):
        self._run(PASSWORD, PASSWORD)
        first = self._written_hash()
        _code, output = self._run("a different password 1", "a different password 1")
        self.assertIn("Replaced", output)
        self.assertNotEqual(self._written_hash(), first)
        self.assertTrue(passhash.verify_password("a different password 1", self._written_hash()))

    def test_mismatched_confirmation_writes_nothing(self):
        code, _output = self._run(PASSWORD, "typo")
        self.assertEqual(code, 1)
        self.assertFalse(self.output.exists())

    def test_an_empty_password_writes_nothing(self):
        code, _output = self._run("   ")
        self.assertEqual(code, 1)
        self.assertFalse(self.output.exists())

    def test_a_short_password_is_warned_about_but_allowed(self):
        code, output = self._run("short", "short")
        self.assertEqual(code, 0)
        self.assertIn("Warning", output)

    def test_it_uses_the_same_hashing_as_the_app(self):
        self.assertEqual(self.tool._load_passhash().ITERATIONS, passhash.ITERATIONS)


if __name__ == "__main__":
    unittest.main()
