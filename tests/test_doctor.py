"""Tests for the PATH-detection bits of doctor.py.

The path-string tests use platform-neutral fixtures (built from `os.pathsep`
and `Path("/fake/scripts")` style paths) so they pass on Windows, macOS, and
Linux. Tests that assert Windows-specific behavior (case-insensitivity,
backslash trailing separators) are guarded by `os.name == "nt"`.

None of these tests invoke subprocesses or modify real PATH.
"""
from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest import mock

from ntu_cool_materials import doctor


def _join_path(*entries: str) -> str:
    """Build a PATH-style string with the platform's separator."""
    return os.pathsep.join(entries)


def _fake_scripts_path() -> Path:
    """A platform-appropriate fake Scripts directory."""
    return Path(r"C:\Python\Scripts") if os.name == "nt" else Path("/fake/python/scripts")


def _other_path() -> str:
    return r"C:\Other" if os.name == "nt" else "/usr/local/bin"


def _windows_path() -> str:
    return r"C:\Windows" if os.name == "nt" else "/usr/bin"


class ScriptsOnPathTests(unittest.TestCase):
    def test_exact_match(self) -> None:
        scripts = _fake_scripts_path()
        env = _join_path(_windows_path(), str(scripts), _other_path())
        with mock.patch.dict(os.environ, {"PATH": env}):
            self.assertTrue(doctor._scripts_on_path(scripts))

    def test_missing(self) -> None:
        scripts = _fake_scripts_path()
        env = _join_path(_windows_path(), _other_path())
        with mock.patch.dict(os.environ, {"PATH": env}):
            self.assertFalse(doctor._scripts_on_path(scripts))

    @unittest.skipUnless(os.name == "nt", "case-insensitive PATH lookup is Windows-only")
    def test_case_insensitive_on_windows(self) -> None:
        # normcase lowercases on Windows; PATH entries from the registry can
        # come back upper-cased, so we need to match regardless of case.
        scripts = Path(r"C:\Python\Scripts")
        env = _join_path(r"C:\WINDOWS", r"c:\python\scripts", r"C:\Other")
        with mock.patch.dict(os.environ, {"PATH": env}):
            self.assertTrue(doctor._scripts_on_path(scripts))

    @unittest.skipUnless(os.name == "nt", "trailing-backslash normalization is Windows-only")
    def test_trailing_separator_tolerated(self) -> None:
        # Trailing backslash should normpath away on Windows.
        scripts = Path(r"C:\Python\Scripts")
        env = _join_path(r"C:\Python\Scripts\\", r"C:\Other")
        with mock.patch.dict(os.environ, {"PATH": env}):
            self.assertTrue(doctor._scripts_on_path(scripts))

    def test_empty_path_entries_ignored(self) -> None:
        scripts = _fake_scripts_path()
        # Empty entries from leading/trailing/double separators should not crash
        # or false-match.
        env = _join_path("", "", str(scripts), "", "")
        with mock.patch.dict(os.environ, {"PATH": env}):
            self.assertTrue(doctor._scripts_on_path(scripts))


class CheckScriptsOnPathTests(unittest.TestCase):
    def test_non_windows_returns_ok(self) -> None:
        with mock.patch.object(doctor.platform, "system", return_value="Darwin"):
            result = doctor.check_scripts_on_path()
        self.assertTrue(result.ok)
        self.assertTrue(result.optional)

    @unittest.skipUnless(os.name == "nt", "Windows-only end-to-end check")
    def test_windows_on_path(self) -> None:
        fake_scripts = _fake_scripts_path()
        env = _join_path(str(fake_scripts), _other_path())
        with (
            mock.patch.object(doctor.platform, "system", return_value="Windows"),
            mock.patch.object(doctor, "_scripts_dir", return_value=fake_scripts),
            mock.patch.dict(os.environ, {"PATH": env}),
        ):
            result = doctor.check_scripts_on_path()
        self.assertTrue(result.ok)
        self.assertEqual(result.detail, str(fake_scripts))

    @unittest.skipUnless(os.name == "nt", "Windows-only end-to-end check")
    def test_windows_missing_offers_auto_install(self) -> None:
        fake_scripts = _fake_scripts_path()
        env = _join_path(_windows_path(), _other_path())
        with (
            mock.patch.object(doctor.platform, "system", return_value="Windows"),
            mock.patch.object(doctor, "_scripts_dir", return_value=fake_scripts),
            mock.patch.dict(os.environ, {"PATH": env}),
        ):
            result = doctor.check_scripts_on_path()
        self.assertFalse(result.ok)
        self.assertTrue(result.optional)  # not blocking
        self.assertIsNotNone(result.auto_install)
        self.assertIn(str(fake_scripts), result.fix_command)

    def test_name_is_stable_for_set_membership(self) -> None:
        """`ensure_ready` matches checks against `recommended_names` by exact
        string. If we ever localize this check's name via t(), the match breaks
        silently. Lock the name in place via a test."""
        with mock.patch.object(doctor.platform, "system", return_value="Linux"):
            result = doctor.check_scripts_on_path()
        self.assertEqual(result.name, "Python Scripts 在 PATH 上")


if __name__ == "__main__":
    unittest.main()
