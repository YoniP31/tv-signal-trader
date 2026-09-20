"""Covers deploy/deploy.py -- the tool that pushes new bot files to a fleet of
VPS over SSH. The whole per-machine flow runs against a fake VPS (a stand-in
for subprocess.run that tracks whether the bot is running), so every ordering
guarantee and failure path is checked without a network. Run with:

    python -m unittest tests.test_deploy -v
"""

import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "deploy_tool", Path(__file__).resolve().parent.parent / "deploy" / "deploy.py"
)
deploy = importlib.util.module_from_spec(_SPEC)
sys.modules["deploy_tool"] = deploy
_SPEC.loader.exec_module(deploy)


def _completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr=stderr)


class FakeVps:
    """Stands in for subprocess.run for ssh/scp against one VPS."""

    DEFAULT_TASK_DIR = "C:\\Users\\Administrator\\Desktop\\tv-signal-trader"

    def __init__(self, task_exe="tv-signal-trader.exe", reachable=True, has_task=True, stop_works=True,
                 scp_ok=True, hash_ok=True, start_works=True, stays_up=True, scp_times_out=False,
                 task_dir=DEFAULT_TASK_DIR):
        self.task_exe, self.reachable, self.has_task = task_exe, reachable, has_task
        self.task_dir = task_dir  # None -> the task's command is a bare file name, no folder
        self.stop_works, self.scp_ok, self.hash_ok = stop_works, scp_ok, hash_ok
        self.start_works, self.stays_up, self.scp_times_out = start_works, stays_up, scp_times_out
        self.running = True
        self.calls = []  # ("ssh", command) or ("scp", local, remote)
        self._last_upload_hash = None

    def ssh_commands(self):
        return [c[1] for c in self.calls if c[0] == "ssh"]

    def __call__(self, args, **kwargs):
        if args[0] == "scp":
            self.calls.append(("scp", args[-2], args[-1]))
            if self.scp_times_out:
                raise subprocess.TimeoutExpired(args, 1)
            self._last_upload_hash = deploy.sha256_of(args[-2])
            return _completed(0 if self.scp_ok else 1, stderr="" if self.scp_ok else "connection lost")

        command = args[-1]
        self.calls.append(("ssh", command))
        if not self.reachable:
            return _completed(255, stderr="Connection timed out")
        if command == "echo ok":
            return _completed(0, "ok\r\n")
        if command.startswith("schtasks /query"):
            if not self.has_task:
                return _completed(1, stderr="ERROR: The system cannot find the file specified.")
            command = f"{self.task_dir}\\{self.task_exe}" if self.task_dir else self.task_exe
            return _completed(0, f"<Task><Actions><Exec><Command>{command}</Command></Exec></Actions></Task>")
        if "stop-bot.ps1" in command:
            if not self.stop_works:
                return _completed(1, stderr="stop-bot.ps1 not found")
            self.running = False
            return _completed(0)
        if command.startswith("tasklist"):
            return _completed(0, f'"{self.task_exe}","1234","Console","1","10,000 K"\r\n' if self.running
                              else "INFO: No tasks are running which match the specified criteria.\r\n")
        if command.startswith("certutil"):
            digest = self._last_upload_hash if self.hash_ok else "0" * 64
            return _completed(0, f"SHA256 hash of file x:\r\n{digest}\r\nCertUtil: -hashfile command completed successfully.\r\n")
        if command.startswith("schtasks /run"):
            if not self.start_works:
                return _completed(1, stderr="ERROR: cannot start")
            self.running = self.stays_up
            return _completed(0)
        return _completed(0)  # move / del / copy


class UpdateHostTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.cfg = deploy._merge(deploy.DEFAULT_CONFIG, {"verify_seconds": 0})
        self.paths = {}
        for key, content in {"admin_exe": b"admin-bytes", "user_exe": b"user-bytes",
                             "env": b"KEY=value\n", "accounts": b"ACC1\n"}.items():
            p = self.dir / deploy.REMOTE_FILENAMES[key]
            p.write_bytes(content)
            self.paths[key] = p
        self.hashes = {k: deploy.sha256_of(p) for k, p in self.paths.items()}

    def _run(self, vps, keys, dry_run=False):
        return deploy.update_host(
            ("Administrator", "1.2.3.4"), self.cfg, {k: self.paths[k] for k in keys},
            {k: self.hashes[k] for k in keys}, dry_run=dry_run, runner=vps, sleep=lambda s: None,
        )

    def _order(self, vps, *needles):
        """Index of the first call containing each needle -- to assert ordering."""
        texts = [c[1] if c[0] == "ssh" else c[2] for c in vps.calls]
        return [next(i for i, t in enumerate(texts) if n in t) for n in needles]

    # --- the happy path ---

    def test_replacing_the_running_exe_stops_copies_verifies_and_restarts(self):
        vps = FakeVps()
        result = self._run(vps, ["admin_exe"])
        self.assertEqual(result.status, "ok", result.detail)
        stop, upload, hashcheck, move, start = self._order(
            vps, "stop-bot.ps1", "tv-signal-trader.exe.new", "certutil", "move /Y", "schtasks /run")
        self.assertLess(stop, upload)
        self.assertLess(upload, hashcheck)
        self.assertLess(hashcheck, move)
        self.assertLess(move, start)

    def test_upload_goes_to_a_staging_name_in_the_install_dir(self):
        vps = FakeVps()
        self._run(vps, ["admin_exe"])
        scp_call = next(c for c in vps.calls if c[0] == "scp")
        self.assertEqual(scp_call[2], "Administrator@1.2.3.4:C:/Users/Administrator/Desktop/tv-signal-trader/tv-signal-trader.exe.new")

    # --- the install folder comes from the machine's own task ---

    def test_a_versioned_install_folder_is_used_for_stop_upload_and_move(self):
        folder = "C:\\Users\\Administrator\\Desktop\\tv-signal-trader-v1.1.0"
        vps = FakeVps(task_dir=folder)
        result = self._run(vps, ["admin_exe"])
        self.assertEqual(result.status, "ok", result.detail)
        scp_call = next(c for c in vps.calls if c[0] == "scp")
        self.assertEqual(scp_call[2], "Administrator@1.2.3.4:" + folder.replace("\\", "/") + "/tv-signal-trader.exe.new")
        stop = next(c for c in vps.ssh_commands() if "stop-bot.ps1" in c)
        self.assertIn(f'"{folder}\\stop-bot.ps1"', stop)
        move = next(c for c in vps.ssh_commands() if c.startswith("move /Y"))
        self.assertIn(f'"{folder}\\tv-signal-trader.exe"', move)
        self.assertNotIn("Desktop\\tv-signal-trader\\", " ".join(vps.ssh_commands()))

    def test_machines_with_different_folder_names_each_use_their_own(self):
        for folder in ("C:\\bots\\tv-signal-trader-v1.0.0", "D:\\tv-signal-trader-v1.1.0"):
            vps = FakeVps(task_dir=folder)
            self.assertEqual(self._run(vps, ["admin_exe"]).status, "ok")
            scp_call = next(c for c in vps.calls if c[0] == "scp")
            self.assertIn(folder.replace("\\", "/"), scp_call[2])

    def test_the_configured_install_dir_is_the_fallback_when_the_task_has_no_folder(self):
        vps = FakeVps(task_dir=None)  # <Command>tv-signal-trader.exe</Command>
        self.assertEqual(self._run(vps, ["admin_exe"]).status, "ok")
        scp_call = next(c for c in vps.calls if c[0] == "scp")
        self.assertEqual(scp_call[2], "Administrator@1.2.3.4:C:/Users/Administrator/Desktop/tv-signal-trader/tv-signal-trader.exe.new")

    # --- deciding whether to interrupt trading at all ---

    def test_pushing_only_the_other_variants_exe_never_stops_the_bot(self):
        vps = FakeVps(task_exe="tv-signal-trader.exe")  # this VPS runs the admin build
        result = self._run(vps, ["user_exe"])
        self.assertEqual(result.status, "ok_no_restart")
        self.assertFalse(any("stop-bot" in c or "schtasks /run" in c for c in vps.ssh_commands()))
        self.assertTrue(vps.running)

    def test_pushing_only_accounts_never_stops_the_bot(self):
        vps = FakeVps()
        self.assertEqual(self._run(vps, ["accounts"]).status, "ok_no_restart")
        self.assertFalse(any("stop-bot" in c for c in vps.ssh_commands()))

    def test_a_user_variant_vps_is_restarted_when_user_exe_is_pushed(self):
        vps = FakeVps(task_exe="tv-signal-trader-user.exe")
        self.assertEqual(self._run(vps, ["user_exe"]).status, "ok")
        self.assertTrue(any("stop-bot" in c for c in vps.ssh_commands()))

    def test_pushing_env_restarts_and_backs_up_the_existing_one_first(self):
        vps = FakeVps()
        result = self._run(vps, ["env"])
        self.assertEqual(result.status, "ok", result.detail)
        backup, move = self._order(vps, ".env.bak", "move /Y")
        self.assertLess(backup, move)
        self.assertTrue(any("stop-bot" in c for c in vps.ssh_commands()))

    # --- failures before anything changes ---

    def test_an_unreachable_vps_is_reported_and_left_alone(self):
        vps = FakeVps(reachable=False)
        self.assertEqual(self._run(vps, ["admin_exe"]).status, "unreachable")
        self.assertEqual(len(vps.calls), 1)

    def test_a_vps_without_the_scheduled_task_is_refused_before_stopping_anything(self):
        vps = FakeVps(has_task=False)
        result = self._run(vps, ["admin_exe"])
        self.assertEqual(result.status, "no_task")
        self.assertIn("setup_vps.ps1", result.detail)
        self.assertFalse(any("stop-bot" in c for c in vps.ssh_commands()))

    def test_a_failed_stop_aborts_without_copying_or_starting(self):
        vps = FakeVps(stop_works=False)
        self.assertEqual(self._run(vps, ["admin_exe"]).status, "stop_failed")
        self.assertFalse(any(c[0] == "scp" for c in vps.calls))
        self.assertFalse(any("schtasks /run" in c for c in vps.ssh_commands()))

    def test_dry_run_changes_nothing(self):
        vps = FakeVps()
        result = self._run(vps, ["admin_exe", "env"], dry_run=True)
        self.assertEqual(result.status, "dry_run")
        self.assertFalse(any(c[0] == "scp" for c in vps.calls))
        for mutating in ("stop-bot", "schtasks /run", "move", "certutil"):
            self.assertFalse(any(mutating in c for c in vps.ssh_commands()), mutating)
        self.assertTrue(vps.running)

    # --- failures after the bot is stopped: never leave it down ---

    def test_a_hash_mismatch_never_installs_the_file_but_still_restarts_the_bot(self):
        vps = FakeVps(hash_ok=False)
        result = self._run(vps, ["admin_exe"])
        self.assertEqual(result.status, "copy_failed")
        self.assertIn("SHA-256", result.detail)
        self.assertFalse(any("move /Y" in c for c in vps.ssh_commands()))
        self.assertTrue(any("del /Q" in c for c in vps.ssh_commands()))
        self.assertTrue(vps.running, "the bot must be restarted on its old files")

    def test_a_failed_upload_still_restarts_the_bot(self):
        vps = FakeVps(scp_ok=False)
        self.assertEqual(self._run(vps, ["admin_exe"]).status, "copy_failed")
        self.assertTrue(vps.running)

    def test_an_upload_timeout_still_restarts_the_bot(self):
        vps = FakeVps(scp_times_out=True)
        result = self._run(vps, ["admin_exe"])
        self.assertEqual(result.status, "copy_failed")
        self.assertIn("timed out", result.detail)
        self.assertTrue(vps.running)

    def test_a_failed_start_is_reported(self):
        vps = FakeVps(start_works=False)
        self.assertEqual(self._run(vps, ["admin_exe"]).status, "restart_failed")

    def test_a_bot_that_dies_right_after_starting_is_caught(self):
        vps = FakeVps(stays_up=False)
        result = self._run(vps, ["admin_exe"])
        self.assertEqual(result.status, "verify_failed")
        self.assertIn("not running", result.detail)

    def test_an_unexpected_exception_on_one_machine_is_contained(self):
        def boom(args, **kwargs):
            raise OSError("ssh not found")
        result = deploy.update_host(("Administrator", "1.2.3.4"), self.cfg, {}, {}, runner=boom, sleep=lambda s: None)
        self.assertEqual(result.status, "error")


class ParsingTests(unittest.TestCase):
    def test_targets_skip_comments_and_blanks_and_default_the_user(self):
        text = "# fleet\n1.1.1.1\n\n  bob@2.2.2.2  # west\n"
        self.assertEqual(deploy.parse_targets(text, "Administrator"),
                         [("Administrator", "1.1.1.1"), ("bob", "2.2.2.2")])

    def test_targets_are_deduplicated_in_order(self):
        self.assertEqual(deploy.parse_targets("1.1.1.1\n2.2.2.2\n1.1.1.1\n", "u"),
                         [("u", "1.1.1.1"), ("u", "2.2.2.2")])

    def test_task_exe_is_read_from_the_task_xml(self):
        xml = "<Exec><Command>C:\\Users\\Administrator\\Desktop\\tv-signal-trader\\tv-signal-trader.exe</Command></Exec>"
        self.assertEqual(deploy.parse_task_exe(xml), "tv-signal-trader.exe")

    def test_task_exe_tolerates_quotes_and_forward_slashes(self):
        self.assertEqual(deploy.parse_task_exe('<Command>"C:/bot/tv-signal-trader-user.exe"</Command>'),
                         "tv-signal-trader-user.exe")

    def test_task_exe_is_none_when_there_is_no_command(self):
        self.assertIsNone(deploy.parse_task_exe("<Task/>"))

    def test_task_dir_is_the_folder_of_the_task_exe_whatever_it_is_called(self):
        xml = "<Command>C:\\Users\\Administrator\\Desktop\\tv-signal-trader-v1.1.0\\tv-signal-trader.exe</Command>"
        self.assertEqual(deploy.parse_task_dir(xml), "C:\\Users\\Administrator\\Desktop\\tv-signal-trader-v1.1.0")

    def test_task_dir_tolerates_quotes_and_forward_slashes(self):
        self.assertEqual(deploy.parse_task_dir('<Command>"C:/bot v2/tv-signal-trader-user.exe"</Command>'), "C:\\bot v2")

    def test_task_dir_is_none_for_a_bare_file_name_or_no_command(self):
        self.assertIsNone(deploy.parse_task_dir("<Command>tv-signal-trader.exe</Command>"))
        self.assertIsNone(deploy.parse_task_dir("<Task/>"))

    def test_certutil_hash_is_found_with_or_without_spaces(self):
        digest = "ab" * 32
        spaced = " ".join(digest[i:i + 2] for i in range(0, 64, 2))
        for body in (digest, spaced):
            self.assertEqual(deploy.parse_certutil_hash(f"SHA256 hash of file x:\r\n{body}\r\nCertUtil: ok\r\n"), digest)

    def test_certutil_hash_is_none_on_garbage(self):
        self.assertIsNone(deploy.parse_certutil_hash("CertUtil: -hashfile command FAILED"))


class ConfigTests(unittest.TestCase):
    def _write(self, data):
        tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        self.addCleanup(lambda: Path(tmp.name).unlink())
        json.dump(data, tmp)
        tmp.close()
        return tmp.name

    def test_defaults_are_merged_under_the_users_settings(self):
        cfg = deploy.load_config(self._write({
            "ssh": {"user": "Bob"},
            "files": {"admin_exe": {"push": True, "from": "local", "path": "x.exe"}},
        }))
        self.assertEqual(cfg["ssh"]["user"], "Bob")
        self.assertEqual(cfg["ssh"]["connect_timeout"], 15)  # untouched default survives
        self.assertEqual(cfg["remote"]["task_name"], "TVSignalTrader")

    def test_an_unknown_file_key_is_rejected(self):
        with self.assertRaises(deploy.ConfigError):
            deploy.load_config(self._write({"files": {"mystery": {"from": "local", "path": "x"}}}))

    def test_a_local_file_needs_a_path(self):
        with self.assertRaises(deploy.ConfigError):
            deploy.load_config(self._write({"files": {"admin_exe": {"from": "local"}}}))

    def test_a_bad_source_type_is_rejected(self):
        with self.assertRaises(deploy.ConfigError):
            deploy.load_config(self._write({"files": {"admin_exe": {"from": "ftp"}}}))

    def test_a_missing_config_says_where_to_copy_the_example_from(self):
        with self.assertRaises(deploy.ConfigError) as ctx:
            deploy.load_config("does-not-exist.json")
        self.assertIn("example", str(ctx.exception))

    def test_selected_files_follow_the_push_flags(self):
        cfg = {"files": {
            "admin_exe": {"push": True}, "user_exe": {"push": False},
            "env": {"push": False}, "accounts": {"push": True},
        }}
        self.assertEqual(deploy.selected_files(cfg), ["admin_exe", "accounts"])

    def test_an_override_replaces_the_push_flags_entirely(self):
        cfg = {"files": {"admin_exe": {"push": True}, "user_exe": {"push": False}, "env": {"push": False}}}
        self.assertEqual(deploy.selected_files(cfg, "user_exe, env"), ["user_exe", "env"])

    def test_an_override_naming_an_undefined_file_is_rejected(self):
        with self.assertRaises(deploy.ConfigError):
            deploy.selected_files({"files": {"admin_exe": {"push": True}}}, "env")


class SourceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def test_a_local_file_resolves_to_its_path(self):
        f = self.dir / "a.exe"
        f.write_bytes(b"x")
        cfg = {"files": {"admin_exe": {"from": "local", "path": str(f)}}}
        self.assertEqual(deploy.resolve_sources(cfg, ["admin_exe"], self.dir)["admin_exe"], f)

    def test_a_missing_local_file_is_a_clear_error(self):
        cfg = {"files": {"admin_exe": {"from": "local", "path": str(self.dir / "nope.exe")}}}
        with self.assertRaises(deploy.SourceError):
            deploy.resolve_sources(cfg, ["admin_exe"], self.dir)

    # --- releases ---

    def _opener(self, payloads):
        """A urlopen stand-in: maps a URL substring to the bytes it returns."""
        def opener(req, timeout=None):
            url = req.full_url if hasattr(req, "full_url") else req
            for needle, body in payloads.items():
                if needle in url:
                    return io.BytesIO(body if isinstance(body, bytes) else json.dumps(body).encode())
            raise AssertionError(f"unexpected URL {url}")
        return opener

    def _zip(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("tv-signal-trader/.env", "FROM_ZIP=1\n")
            zf.writestr("tv-signal-trader/tv-signal-trader.exe", "zip-exe")
        return buf.getvalue()

    def test_latest_includes_prereleases_by_using_the_release_list(self):
        # /releases/latest would 404 here -- every release this project has
        # ever published is a pre-release.
        listing = [{"tag_name": "v9", "draft": True, "assets": []},
                   {"tag_name": "v1.0.0", "draft": False, "prerelease": True, "assets": []}]
        opener = self._opener({"/releases?per_page": listing})
        self.assertEqual(deploy.get_release({"repo": "o/r", "tag": "latest"}, opener)["tag_name"], "v1.0.0")

    def test_a_loose_release_asset_is_downloaded_by_name(self):
        release = {"tag_name": "v2", "assets": [
            {"name": "tv-signal-trader.exe", "browser_download_url": "https://dl/loose.exe", "size": 5}]}
        opener = self._opener({"https://dl/loose.exe": b"12345"})
        path = deploy.fetch_release_file({}, "tv-signal-trader.exe", self.dir, opener, _release=release)
        self.assertEqual(path.read_bytes(), b"12345")

    def test_a_file_is_extracted_from_the_release_zip_when_there_is_no_loose_asset(self):
        # How every release up to v1.0.0 is packaged.
        data = self._zip()
        release = {"tag_name": "v1.0.0", "assets": [
            {"name": "tv-signal-trader-v1.0.0.zip", "browser_download_url": "https://dl/bundle.zip", "size": len(data)}]}
        opener = self._opener({"https://dl/bundle.zip": data})
        path = deploy.fetch_release_file({}, ".env", self.dir, opener, _release=release)
        self.assertEqual(path.read_text(), "FROM_ZIP=1\n")

    def test_a_file_the_release_does_not_contain_is_a_clear_error(self):
        data = self._zip()
        release = {"tag_name": "v1.0.0", "assets": [
            {"name": "b.zip", "browser_download_url": "https://dl/b.zip", "size": len(data)}]}
        opener = self._opener({"https://dl/b.zip": data})
        with self.assertRaises(deploy.SourceError):
            deploy.fetch_release_file({}, "missing.txt", self.dir, opener, _release=release)


if __name__ == "__main__":
    unittest.main()
