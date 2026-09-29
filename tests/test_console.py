import io
import os
import unittest
from unittest.mock import patch

from ntu_cool_materials import console


class StdinInteractiveTests(unittest.TestCase):
    def test_null_device_is_not_interactive(self):
        # Windows reports isatty() for NUL; `command <nul` must never prompt.
        with open(os.devnull, encoding="utf-8") as null, patch.object(console.sys, "stdin", null):
            self.assertFalse(console.stdin_is_interactive())

    def test_pipe_and_missing_stdin_are_not_interactive(self):
        for stream in (io.StringIO("y\n"), None):
            with self.subTest(stream=stream), patch.object(console.sys, "stdin", stream):
                self.assertFalse(console.stdin_is_interactive())


if __name__ == "__main__":
    unittest.main()
