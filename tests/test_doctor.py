"""Tests for the PATH-detection bits of doctor.py.

These avoid actually invoking subprocesses or modifying real PATH — we just
patch `os.environ["PATH"]` and call the pure helpers.
"""
from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest import mock

from ntu_cool_materials import doctor


class ScriptsOnPathTests(unittest.TestCase):
    def test_exact_match(self) -> None:
        scripts = Path(r"C:\Python\Scripts")
        with mock.patch.dict(os.environ, {"PATH": r"C:\Windows;C:\Python\Scripts;C:\Other"}):
            self.assertTrue(doctor._scripts_on_path(scripts))

    def test_missing(self) -> None:
        scripts = Path(r"C:\Python\Scripts")
        with mock.patch.dict(os.environ, {"PATH": r"C:\Windows;C:\Other"}):
            self.assertFalse(doctor._scripts_on_path(scripts))

    def test_case_insensitive_on_windows(self) -> None:
        # normcase lowercases on Windows; os.pathsep + paths come back upper-cased
        # from the registry sometimes, so we need to match regardless of case.
        scripts = Path(r"C:\Python\Scripts")
        with mock.patch.dict(os.environ, {"PATH": r"C:\WINDOWS;c:\python\scripts;C:\Other"}):
            if os.name == "nt":
                self.assertTrue(doctor._scripts_on_path(scripts))
            else:
                # POSIX is case-sensitive — the test is only meaningful on Windows.
                self.assertFalse(doctor._scripts_on_path(scripts))

    def test_trailing_separator_tolerated(self) -> None:
        scripts = Path(r"C:\Python\Scripts")
        # Trailing backslash should normpath away.
        with mock.patch.dict(os.environ, {"PATH": r"C:\Python\Scripts\;C:\Other"}):
            self.assertTrue(doctor._scripts_on_path(scripts))

    def test_empty_path_entries_ignored(self) -> None:
        scripts = Path(r"C:\Python\Scripts")
        # Empty entries from leading/trailing/double separators should not crash
        # or false-match.
        with mock.patch.dict(os.environ, {"PATH": f";;{scripts};;"}):
            self.assertTrue(doctor._scripts_on_path(scripts))


class CheckScriptsOnPathTests(unittest.TestCase):
    def test_non_windows_returns_ok(self) -> None:
        with mock.patch.object(doctor.platform, "system", return_value="Darwin"):
            result = doctor.check_scripts_on_path()
        self.assertTrue(result.ok)
        self.assertTrue(result.optional)

    def test_windows_on_path(self) -> None:
        fake_scripts = Path(r"C:\Python\Scripts")
        with (
            mock.patch.object(doctor.platform, "system", return_value="Windows"),
            mock.patch.object(doctor, "_scripts_dir", return_value=fake_scripts),
            mock.patch.dict(os.environ, {"PATH": str(fake_scripts) + ";C:\\Other"}),
        ):
            result = doctor.check_scripts_on_path()
        self.assertTrue(result.ok)
        self.assertEqual(result.detail, str(fake_scripts))

    def test_windows_missing_offers_auto_install(self) -> None:
        fake_scripts = Path(r"C:\Python\Scripts")
        with (
            mock.patch.object(doctor.platform, "system", return_value="Windows"),
            mock.patch.object(doctor, "_scripts_dir", return_value=fake_scripts),
            mock.patch.dict(os.environ, {"PATH": r"C:\Windows;C:\Other"}),
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
