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

    def test_posix_on_path(self) -> None:
        """On POSIX, when the scripts dir is already on PATH the check passes
        without trying to modify any rc file.

        Test uses platform-appropriate path forms so it works whether the
        runner is actually POSIX or Windows-with-mocked-platform."""
        fake_scripts = _fake_scripts_path()
        env = _join_path("/usr/bin", str(fake_scripts), "/usr/local/bin") if os.name != "nt" \
            else _join_path(r"C:\Windows", str(fake_scripts), r"C:\Other")
        with (
            mock.patch.object(doctor.platform, "system", return_value="Darwin"),
            mock.patch.object(doctor, "_scripts_dir", return_value=fake_scripts),
            mock.patch.dict(os.environ, {"PATH": env, "SHELL": "/bin/zsh"}),
        ):
            result = doctor.check_scripts_on_path()
        self.assertTrue(result.ok)

    def test_posix_missing_offers_auto_install(self) -> None:
        """The python.org-installer-on-Mac case Benson would have hit if
        he'd been on a Mac: scripts dir exists, isn't on PATH, fix is wired."""
        fake_scripts = _fake_scripts_path()
        env = _join_path("/usr/bin", "/usr/local/bin") if os.name != "nt" \
            else _join_path(r"C:\Windows", r"C:\Other")
        with (
            mock.patch.object(doctor.platform, "system", return_value="Darwin"),
            mock.patch.object(doctor, "_scripts_dir", return_value=fake_scripts),
            mock.patch.dict(os.environ, {"PATH": env, "SHELL": "/bin/zsh"}),
        ):
            result = doctor.check_scripts_on_path()
        self.assertFalse(result.ok)
        self.assertTrue(result.optional)
        self.assertIsNotNone(result.auto_install)
        self.assertIn(str(fake_scripts), result.fix_command)
        self.assertIn(".zprofile", result.fix_command)

    def test_name_is_stable_for_set_membership(self) -> None:
        """`ensure_ready` matches checks against `recommended_names` by exact
        string. If we ever localize this check's name via t(), the match breaks
        silently. Lock the name in place via a test."""
        with mock.patch.object(doctor.platform, "system", return_value="Linux"):
            result = doctor.check_scripts_on_path()
        self.assertEqual(result.name, "Python Scripts 在 PATH 上")


class PosixShellRcPathTests(unittest.TestCase):
    """The shell-rc resolver decides which file `_add_scripts_to_user_path_posix`
    writes to. Wrong choice = the export ends up in a file the user's shell
    never reads = `ntu-cool-gcm` still not found after the 'fix'. Critical."""

    def test_zsh_returns_zprofile(self) -> None:
        """zsh users get .zprofile (login shell hook, what macOS Terminal.app
        runs). Not .zshrc — that's interactive-only and skipped by some
        non-interactive launch contexts."""
        with (
            mock.patch.dict(os.environ, {"SHELL": "/bin/zsh"}),
            mock.patch.object(doctor.platform, "system", return_value="Darwin"),
        ):
            result = doctor._posix_shell_rc_path()
        self.assertEqual(result.name, ".zprofile")

    def test_bash_on_mac_returns_bash_profile(self) -> None:
        """macOS bash users follow the .bash_profile-for-login convention."""
        with (
            mock.patch.dict(os.environ, {"SHELL": "/bin/bash"}),
            mock.patch.object(doctor.platform, "system", return_value="Darwin"),
        ):
            result = doctor._posix_shell_rc_path()
        self.assertEqual(result.name, ".bash_profile")

    def test_bash_on_linux_returns_bashrc(self) -> None:
        """Linux bash users follow the .bashrc-sourced-by-everything convention."""
        with (
            mock.patch.dict(os.environ, {"SHELL": "/bin/bash"}),
            mock.patch.object(doctor.platform, "system", return_value="Linux"),
        ):
            result = doctor._posix_shell_rc_path()
        self.assertEqual(result.name, ".bashrc")

    def test_unknown_shell_on_mac_defaults_to_zprofile(self) -> None:
        """macOS default shell since Catalina is zsh; if $SHELL is empty
        or unrecognized we still target the zsh rc on Mac because that's
        what >95% of Mac users have today."""
        with (
            mock.patch.dict(os.environ, {"SHELL": ""}),
            mock.patch.object(doctor.platform, "system", return_value="Darwin"),
        ):
            result = doctor._posix_shell_rc_path()
        self.assertEqual(result.name, ".zprofile")

    def test_unknown_shell_on_linux_returns_none(self) -> None:
        """No good guess for Linux + unknown shell — return None so the
        caller falls back to printing manual instructions."""
        with (
            mock.patch.dict(os.environ, {"SHELL": "/bin/fish"}),
            mock.patch.object(doctor.platform, "system", return_value="Linux"),
        ):
            result = doctor._posix_shell_rc_path()
        self.assertIsNone(result)


class AddScriptsToUserPathPosixTests(unittest.TestCase):
    """End-to-end-ish: temp HOME dir, simulated $SHELL, real file writes
    against the temp dir. Verifies the marker block lands in the right file
    and that re-runs are idempotent."""

    def setUp(self) -> None:
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        # Silence the print() output so test runs stay readable.
        import contextlib
        import io
        self._stdout_ctx = contextlib.redirect_stdout(io.StringIO())
        self._stdout_ctx.__enter__()
        self.addCleanup(self._stdout_ctx.__exit__, None, None, None)

    def _run_in_fake_env(self, scripts_dir: Path, shell: str = "/bin/zsh",
                         system: str = "Darwin") -> bool:
        with (
            mock.patch.object(Path, "home", return_value=self.home),
            mock.patch.dict(os.environ, {"SHELL": shell}),
            mock.patch.object(doctor.platform, "system", return_value=system),
        ):
            return doctor._add_scripts_to_user_path_posix(scripts_dir)

    def test_writes_marker_block_to_zprofile(self) -> None:
        scripts = Path("/Library/Frameworks/Python.framework/Versions/3.13/bin")
        ok = self._run_in_fake_env(scripts)
        self.assertTrue(ok)
        zprofile = self.home / ".zprofile"
        content = zprofile.read_text(encoding="utf-8")
        self.assertIn(doctor._POSIX_RC_MARKER_BEGIN, content)
        self.assertIn(doctor._POSIX_RC_MARKER_END, content)
        self.assertIn(f'export PATH="$PATH:{scripts}"', content)

    def test_idempotent_does_not_duplicate(self) -> None:
        scripts = Path("/opt/python/3.13/bin")
        self.assertTrue(self._run_in_fake_env(scripts))
        first_pass = (self.home / ".zprofile").read_text(encoding="utf-8")
        self.assertTrue(self._run_in_fake_env(scripts))
        second_pass = (self.home / ".zprofile").read_text(encoding="utf-8")
        self.assertEqual(first_pass, second_pass,
                         "second run must not append a duplicate marker block")
        # And no double-export anywhere.
        self.assertEqual(second_pass.count(doctor._POSIX_RC_MARKER_BEGIN), 1)

    def test_skips_if_user_already_added_path_manually(self) -> None:
        """User edited .zprofile by hand with the same export. Don't add
        a marker block on top of their manual line."""
        scripts = Path("/opt/python/3.13/bin")
        zprofile = self.home / ".zprofile"
        zprofile.write_text(f'export PATH="$PATH:{scripts}"\n', encoding="utf-8")
        self.assertTrue(self._run_in_fake_env(scripts))
        content = zprofile.read_text(encoding="utf-8")
        self.assertNotIn(doctor._POSIX_RC_MARKER_BEGIN, content,
                         "marker block must not be added when path is already exported")

    def test_bash_on_linux_writes_to_bashrc(self) -> None:
        scripts = Path("/usr/local/python/bin")
        ok = self._run_in_fake_env(scripts, shell="/bin/bash", system="Linux")
        self.assertTrue(ok)
        self.assertTrue((self.home / ".bashrc").exists())
        self.assertFalse((self.home / ".zprofile").exists())


if __name__ == "__main__":
    unittest.main()
