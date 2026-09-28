"""Covers deploy/build_release_zip.py -- the single source of truth for what
goes into the release zip attached to each GitHub release, so a new shipped
file (like watchdog.ps1) can't be left out of some future release by
hand-writing the file list from memory each time. Run with:

    python -m unittest tests.test_build_release_zip -v
"""

import importlib.util
import io
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "build_release_zip", Path(__file__).resolve().parent.parent / "deploy" / "build_release_zip.py"
)
build_release_zip = importlib.util.module_from_spec(_SPEC)
sys.modules["build_release_zip"] = build_release_zip
_SPEC.loader.exec_module(build_release_zip)

REPO_ROOT = Path(__file__).resolve().parent.parent


class _FixtureCase(unittest.TestCase):
    """A throwaway dist/docs/deploy tree with exactly the 5 files a release
    ships, each with known, distinct byte content."""

    CONTENTS = {
        "tv-signal-trader.exe": b"admin-exe-bytes",
        "tv-signal-trader-user.exe": b"user-exe-bytes",
        ".env": b"DAILY_PROFIT_LIMIT=\n",
        "accounts_to_add.txt": b"ACC1\n",
        "watchdog.ps1": b"# a watchdog script\n",
    }

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.exe_dir, self.docs_dir, self.deploy_dir = root / "dist", root / "docs", root / "deploy"
        for d in (self.exe_dir, self.docs_dir, self.deploy_dir):
            d.mkdir()
        for member, (dir_attr, filename) in build_release_zip.MEMBERS.items():
            (getattr(self, dir_attr) / filename).write_bytes(self.CONTENTS[filename])
        self.output = root / "out" / "tv-signal-trader-vTEST.zip"

    def _sources(self):
        return build_release_zip.resolve_sources(self.exe_dir, self.docs_dir, self.deploy_dir)


class ResolveSourcesTests(_FixtureCase):
    def test_it_finds_every_shipped_file(self):
        sources = self._sources()
        self.assertEqual(set(sources), set(build_release_zip.MEMBERS))

    def test_a_single_missing_file_is_reported_by_its_full_path(self):
        (self.docs_dir / ".env").unlink()
        with self.assertRaises(build_release_zip.BuildError) as ctx:
            self._sources()
        self.assertIn(str(self.docs_dir / ".env"), str(ctx.exception))

    def test_every_missing_file_is_reported_at_once_not_just_the_first(self):
        (self.docs_dir / ".env").unlink()
        (self.deploy_dir / "watchdog.ps1").unlink()
        with self.assertRaises(build_release_zip.BuildError) as ctx:
            self._sources()
        message = str(ctx.exception)
        self.assertIn(str(self.docs_dir / ".env"), message)
        self.assertIn(str(self.deploy_dir / "watchdog.ps1"), message)

    def test_an_empty_directory_is_still_a_real_missing_file_not_a_crash(self):
        for f in self.exe_dir.iterdir():
            f.unlink()
        with self.assertRaises(build_release_zip.BuildError):
            self._sources()


class BuildZipTests(_FixtureCase):
    def test_the_zip_contains_exactly_the_expected_members(self):
        build_release_zip.build_zip(self._sources(), self.output)
        with zipfile.ZipFile(self.output) as zf:
            names = set(zf.namelist())
        expected = {"tv-signal-trader/"} | {f"tv-signal-trader/{m}" for m in build_release_zip.MEMBERS}
        self.assertEqual(names, expected)

    def test_every_member_is_byte_identical_to_its_source(self):
        build_release_zip.build_zip(self._sources(), self.output)
        with zipfile.ZipFile(self.output) as zf:
            for member, (dir_attr, filename) in build_release_zip.MEMBERS.items():
                source_bytes = (getattr(self, dir_attr) / filename).read_bytes()
                self.assertEqual(zf.read(f"tv-signal-trader/{member}"), source_bytes, member)

    def test_watchdog_ps1_lives_directly_alongside_the_exes_not_in_a_subfolder(self):
        build_release_zip.build_zip(self._sources(), self.output)
        with zipfile.ZipFile(self.output) as zf:
            names = zf.namelist()
        self.assertIn("tv-signal-trader/watchdog.ps1", names)
        self.assertIn("tv-signal-trader/tv-signal-trader.exe", names)

    def test_the_zip_is_well_formed(self):
        build_release_zip.build_zip(self._sources(), self.output)
        with zipfile.ZipFile(self.output) as zf:
            self.assertIsNone(zf.testzip())

    def test_a_parent_directory_that_does_not_exist_yet_is_created(self):
        nested = Path(self._tmp.name) / "brand" / "new" / "path.zip"
        build_release_zip.build_zip(self._sources(), nested)
        self.assertTrue(nested.exists())

    def test_rebuilding_overwrites_the_previous_zip_cleanly(self):
        build_release_zip.build_zip(self._sources(), self.output)
        (self.docs_dir / ".env").write_bytes(b"CHANGED=1\n")
        build_release_zip.build_zip(self._sources(), self.output)
        with zipfile.ZipFile(self.output) as zf:
            self.assertEqual(zf.read("tv-signal-trader/.env"), b"CHANGED=1\n")


class MainCliTests(_FixtureCase):
    def _run(self, *args):
        out = io.StringIO()
        with redirect_stdout(out):
            code = build_release_zip.main([
                "--exe-dir", str(self.exe_dir), "--docs-dir", str(self.docs_dir),
                "--deploy-dir", str(self.deploy_dir), *args,
            ])
        return code, out.getvalue()

    def test_an_explicit_output_path_is_used_as_is(self):
        code, output = self._run("--output", str(self.output))
        self.assertEqual(code, 0)
        self.assertTrue(self.output.exists())
        self.assertIn(str(self.output), output)

    def test_version_names_the_zip_under_deploy_cache(self):
        code, output = self._run("--version", "v9.9.9")
        self.assertEqual(code, 0)
        expected = REPO_ROOT / "deploy" / "_cache" / "tv-signal-trader-v9.9.9.zip"
        try:
            self.assertTrue(expected.exists())
            self.assertIn(str(expected), output)
        finally:
            expected.unlink(missing_ok=True)

    def test_neither_version_nor_output_is_a_usage_error(self):
        with self.assertRaises(SystemExit):
            self._run()

    def test_a_missing_source_file_exits_nonzero_and_writes_nothing(self):
        (self.docs_dir / ".env").unlink()
        code, output = self._run("--output", str(self.output))
        self.assertEqual(code, 1)
        self.assertIn("[FAIL]", output)
        self.assertFalse(self.output.exists())

    def test_success_lists_every_member_and_its_source(self):
        _code, output = self._run("--output", str(self.output))
        for member in build_release_zip.MEMBERS:
            self.assertIn(member, output)


class RealRepoTests(unittest.TestCase):
    """The parts of the real repo that are always present at test time --
    the exes themselves are a separate Nuitka build step, not committed, so
    only they are faked here."""

    def test_it_resolves_against_the_real_docs_and_deploy_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe_dir = Path(tmp)
            (exe_dir / "tv-signal-trader.exe").write_bytes(b"fake")
            (exe_dir / "tv-signal-trader-user.exe").write_bytes(b"fake")
            sources = build_release_zip.resolve_sources(exe_dir, REPO_ROOT / "docs", REPO_ROOT / "deploy")
        self.assertEqual(sources["watchdog.ps1"], REPO_ROOT / "deploy" / "watchdog.ps1")
        self.assertEqual(sources[".env"], REPO_ROOT / "docs" / ".env")


if __name__ == "__main__":
    unittest.main()
