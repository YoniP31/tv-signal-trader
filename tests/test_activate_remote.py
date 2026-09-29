"""Covers deploy/activate_remote.py -- entering the one-time activation
password on a fleet of VPS over SSH (Option A from the activation-password
discussion: send the real password once per machine, over SSH's own stdin
forwarding, never as a command-line argument or into any file/log), and
optionally starting the bot afterward. The whole per-machine flow runs
against a fake SSH backend that reproduces exactly what the real exe prints
for each case (see tv_signal_trader/activation.py), so this is checked
without a network or a real build. Run with:

    python -m unittest tests.test_activate_remote -v
"""

import importlib.util
import io
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

_SPEC = importlib.util.spec_from_file_location(
    "activate_remote_tool", Path(__file__).resolve().parent.parent / "deploy" / "activate_remote.py"
)
activate_remote = importlib.util.module_from_spec(_SPEC)
sys.modules["activate_remote_tool"] = activate_remote
_SPEC.loader.exec_module(activate_remote)

deploy = activate_remote.deploy  # the same deploy.py module it loaded, for its HostResult/etc.

PASSWORD = "the-real-activation-password"


def _completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr=stderr)


class FakeActivationVps:
    """Stands in for subprocess.run against one VPS, reproducing exactly
    what activation.ensure_activated() prints for each real outcome."""

    DEFAULT_TASK_DIR = "C:\\Users\\Administrator\\Desktop\\tv-signal-trader"

    def __init__(self, task_exe="tv-signal-trader.exe", correct_password=PASSWORD, reachable=True,
                 has_task=True, already_activated=False, no_password_configured=False,
                 stop_works=True, start_works=True, stays_up=True, task_dir=DEFAULT_TASK_DIR,
                 activation_hangs=False):
        self.task_exe, self.correct_password = task_exe, correct_password
        self.reachable, self.has_task = reachable, has_task
        self.already_activated, self.no_password_configured = already_activated, no_password_configured
        self.stop_works, self.start_works, self.stays_up = stop_works, start_works, stays_up
        self.task_dir, self.activation_hangs = task_dir, activation_hangs
        self.running = False  # the fake bot's own process -- starts down
        self.calls = []  # ("ssh", command, kwargs) or ("scp", local, remote)

    def ssh_commands(self):
        return [c[1] for c in self.calls if c[0] == "ssh"]

    def _activation_output(self, entered):
        banner = ("\nThis computer has not been activated yet. Enter the activation "
                  "password (asked only this one time).\nActivation password: ")
        if self.no_password_configured:
            return 1, "[FAIL] This build has no activation password configured -- run tools/make_password_hash.py, then rebuild."
        if self.already_activated:
            return 1, ""  # no prompt at all -- straight to (failing to) open Chrome
        if entered == self.correct_password:
            return 0, banner + "[ACTIVATION] This computer is now activated."
        return 1, banner + "[FAIL] Wrong password (1/5).\n[FAIL] Too many wrong attempts -- exiting."

    def __call__(self, args, **kwargs):
        if args[0] == "scp":
            self.calls.append(("scp", args[-2], args[-1]))
            return _completed(0)

        command = args[-1]
        self.calls.append(("ssh", command, kwargs))
        if not self.reachable:
            return _completed(255, stderr="Connection timed out")
        if command == "echo ok":
            return _completed(0, "ok\r\n")
        if command.startswith("schtasks /query"):
            if not self.has_task:
                return _completed(1, stderr="ERROR: The system cannot find the file specified.")
            cmd = f"{self.task_dir}\\{self.task_exe}" if self.task_dir else self.task_exe
            return _completed(0, f"<Task><Actions><Exec><Command>{cmd}</Command></Exec></Actions></Task>")
        if command.startswith("cd /d") and self.task_exe in command:
            entered = (kwargs.get("input") or "").rstrip("\n")
            code, output = self._activation_output(entered)
            if self.activation_hangs:
                raise subprocess.TimeoutExpired(args, kwargs.get("timeout"), output=output, stderr="")
            return _completed(code, output)
        if command.startswith("powershell") and "stop-bot.ps1" in command:
            self.running = False
            return _completed(0 if self.stop_works else 1)
        if command.startswith("tasklist"):
            return _completed(0, f'"{self.task_exe}","1234","Console","1","10,000 K"\r\n' if self.running
                              else "INFO: No tasks are running which match the specified criteria.\r\n")
        if command.startswith("schtasks /run"):
            if not self.start_works:
                return _completed(1, stderr="ERROR: cannot start")
            self.running = self.stays_up
            return _completed(0)
        return _completed(0)  # move / del / certutil (stop-bot.ps1 refresh)


class ActivateHostTests(unittest.TestCase):
    def setUp(self):
        self.cfg = deploy._merge(deploy.DEFAULT_CONFIG, {"verify_seconds": 0})

    def _run(self, vps, start=False, password=PASSWORD):
        return activate_remote.activate_host(
            ("Administrator", "1.2.3.4"), self.cfg, password, start, runner=vps, sleep=lambda s: None,
        )

    # --- the happy paths ---

    def test_a_correct_password_activates_the_machine(self):
        vps = FakeActivationVps()
        result = self._run(vps)
        self.assertEqual(result.status, "activated", result.detail)

    def test_the_password_is_sent_as_stdin_not_as_part_of_any_command(self):
        vps = FakeActivationVps()
        self._run(vps)
        activation_call = next(c for c in vps.calls if c[0] == "ssh" and vps.task_exe in c[1] and "cd /d" in c[1])
        self.assertEqual(activation_call[2].get("input"), PASSWORD + "\n")
        for _kind, command, *_ in vps.calls:
            self.assertNotIn(PASSWORD, command)

    def test_it_cleans_up_the_activation_attempt_before_finishing(self):
        vps = FakeActivationVps()
        self._run(vps)
        self.assertTrue(any("stop-bot.ps1" in c for c in vps.ssh_commands()))

    def test_a_machine_already_activated_before_is_still_a_success(self):
        vps = FakeActivationVps(already_activated=True)
        result = self._run(vps)
        self.assertEqual(result.status, "activated", result.detail)

    def test_a_slow_response_that_times_out_is_still_read_as_activated(self):
        # Expected on a fresh activation: it goes on to (fail to) open
        # Chrome, which can take a while -- the ssh call itself times out,
        # but what was printed before that already proves activation worked.
        vps = FakeActivationVps(activation_hangs=True)
        result = self._run(vps)
        self.assertEqual(result.status, "activated", result.detail)

    def test_the_correct_exe_variant_is_used_for_a_user_build_machine(self):
        vps = FakeActivationVps(task_exe="tv-signal-trader-user.exe")
        result = self._run(vps)
        self.assertEqual(result.status, "activated", result.detail)
        self.assertTrue(any("tv-signal-trader-user.exe" in c[1] for c in vps.calls if c[0] == "ssh"))

    # --- --start ---

    def test_without_start_the_bot_task_is_never_triggered(self):
        vps = FakeActivationVps()
        result = self._run(vps, start=False)
        self.assertEqual(result.status, "activated")
        self.assertFalse(any("schtasks /run" in c for c in vps.ssh_commands()))

    def test_with_start_the_bot_is_started_and_verified(self):
        vps = FakeActivationVps()
        result = self._run(vps, start=True)
        self.assertEqual(result.status, "started", result.detail)
        self.assertTrue(vps.running)

    def test_the_cleanup_happens_before_starting_for_real(self):
        vps = FakeActivationVps()
        self._run(vps, start=True)
        texts = vps.ssh_commands()
        stop_i = next(i for i, c in enumerate(texts) if "stop-bot.ps1" in c)
        start_i = next(i for i, c in enumerate(texts) if c.startswith("schtasks /run"))
        self.assertLess(stop_i, start_i)

    def test_a_failed_start_is_reported_distinctly_from_activation_itself(self):
        vps = FakeActivationVps(start_works=False)
        result = self._run(vps, start=True)
        self.assertEqual(result.status, "start_failed")

    def test_a_start_that_does_not_stay_up_is_reported(self):
        vps = FakeActivationVps(stays_up=False)
        result = self._run(vps, start=True)
        self.assertEqual(result.status, "start_failed")
        self.assertIn("not running", result.detail)

    # --- failure paths ---

    def test_a_wrong_password_is_reported_and_nothing_else_is_attempted(self):
        vps = FakeActivationVps()
        result = self._run(vps, password="totally wrong")
        self.assertEqual(result.status, "wrong_password")
        self.assertFalse(any("stop-bot.ps1" in c for c in vps.ssh_commands()))
        self.assertFalse(any("schtasks /run" in c for c in vps.ssh_commands()))

    def test_a_build_with_no_password_configured_is_reported(self):
        vps = FakeActivationVps(no_password_configured=True)
        result = self._run(vps)
        self.assertEqual(result.status, "no_password_configured")

    def test_an_unreachable_machine_is_reported_and_left_alone(self):
        vps = FakeActivationVps(reachable=False)
        result = self._run(vps)
        self.assertEqual(result.status, "unreachable")
        self.assertEqual(len(vps.calls), 1)

    def test_a_machine_without_the_scheduled_task_is_reported(self):
        vps = FakeActivationVps(has_task=False)
        result = self._run(vps)
        self.assertEqual(result.status, "no_task")

    def test_an_unexpected_exception_is_contained(self):
        def boom(args, **kwargs):
            raise OSError("ssh not found")
        result = activate_remote.activate_host(("Administrator", "1.2.3.4"), self.cfg, PASSWORD, False, runner=boom)
        self.assertEqual(result.status, "error")

    # --- the password must never leak into anything reported ---

    def test_the_password_never_appears_in_the_result_for_any_outcome(self):
        for vps in (FakeActivationVps(), FakeActivationVps(already_activated=True),
                    FakeActivationVps(no_password_configured=True), FakeActivationVps(reachable=False),
                    FakeActivationVps(has_task=False), FakeActivationVps(start_works=False)):
            result = self._run(vps, start=True)
            self.assertNotIn(PASSWORD, result.detail, result.status)
            self.assertNotIn(PASSWORD, " ".join(result.steps), result.status)

    def test_a_wrong_password_never_appears_in_the_result_either(self):
        vps = FakeActivationVps()
        result = self._run(vps, password="a guessed password")
        self.assertNotIn("a guessed password", result.detail)
        self.assertNotIn("a guessed password", " ".join(result.steps))


class MainCliTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.config_path = Path(self._tmp.name) / "deploy_config.json"
        self.config_path.write_text('{"files": {"admin_exe": {"push": true, "from": "local", "path": "x"}}}')

    def test_an_empty_password_is_refused(self):
        with patch("builtins.input", side_effect=["", ""]), redirect_stdout(io.StringIO()):
            code = activate_remote.main(["--config", str(self.config_path), "--host", "1.2.3.4"])
        self.assertEqual(code, 2)

    def test_declining_the_confirmation_aborts(self):
        out = io.StringIO()
        with patch("builtins.input", side_effect=[PASSWORD, "no"]), redirect_stdout(out):
            code = activate_remote.main(["--config", str(self.config_path), "--host", "1.2.3.4"])
        self.assertEqual(code, 1)
        self.assertIn("Aborted", out.getvalue())

    def test_a_missing_config_is_a_clean_error_not_a_crash(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = activate_remote.main(["--config", "does-not-exist.json", "--host", "1.2.3.4"])
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
