"""Tests for cli.py helpers — primarily the UTF-8 stream reconfigure.

`main()` and the cli script entry points call `_force_utf8_streams()` to
prevent Windows cp1252/cp950 consoles from crashing on ✓/⚠/✗ and CJK
output. The test simulates a cp1252 stdout, calls the reconfigure helper,
and verifies the stream now accepts the previously-failing glyphs.
"""
from __future__ import annotations

import io
import unittest
from unittest import mock

from ntu_cool_materials import cli


class ForceUtf8StreamsTests(unittest.TestCase):
    def test_reconfigures_stdout_and_stderr_to_utf8(self) -> None:
        # TextIOWrapper exposes reconfigure() the same way real sys.stdout does.
        # Starting from cp1252 mirrors what GitHub's Windows runner gives us.
        fake_stdout = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
        fake_stderr = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
        with mock.patch.object(cli.sys, "stdout", fake_stdout), mock.patch.object(cli.sys, "stderr", fake_stderr):
            cli._force_utf8_streams()
            self.assertEqual(fake_stdout.encoding.lower().replace("-", ""), "utf8")
            self.assertEqual(fake_stderr.encoding.lower().replace("-", ""), "utf8")

    def test_can_write_check_glyph_after_reconfigure(self) -> None:
        # The exact regression we're guarding: 'charmap' codec can't encode '✓'.
        buf = io.BytesIO()
        fake_stdout = io.TextIOWrapper(buf, encoding="cp1252")
        with mock.patch.object(cli.sys, "stdout", fake_stdout):
            cli._force_utf8_streams()
            cli.sys.stdout.write("✓ ready\n")
            cli.sys.stdout.flush()
        self.assertIn("✓ ready", buf.getvalue().decode("utf-8"))

    def test_handles_streams_without_reconfigure(self) -> None:
        """Some launch contexts (Windows services, captured pipes in unusual
        test rigs) hand back stream objects that lack `reconfigure`. Helper
        must no-op silently rather than AttributeError."""
        class NoReconfigure:
            encoding = "ascii"
        with (
            mock.patch.object(cli.sys, "stdout", NoReconfigure()),
            mock.patch.object(cli.sys, "stderr", NoReconfigure()),
        ):
            cli._force_utf8_streams()  # should not raise

    def test_swallows_reconfigure_errors(self) -> None:
        """If reconfigure raises (detached stream, closed pipe), we eat the
        error rather than crash the CLI before it can print its own message."""
        class RaisingStream:
            encoding = "ascii"
            def reconfigure(self, **kwargs):
                raise OSError("pipe closed")
        with (
            mock.patch.object(cli.sys, "stdout", RaisingStream()),
            mock.patch.object(cli.sys, "stderr", RaisingStream()),
        ):
            cli._force_utf8_streams()  # should not raise


if __name__ == "__main__":
    unittest.main()
