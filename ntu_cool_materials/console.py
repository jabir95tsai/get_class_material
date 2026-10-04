"""Whether a person can answer prompts on stdin."""
from __future__ import annotations

import os
import sys


def stdin_is_interactive() -> bool:
    """True only for a real terminal.

    On Windows `isatty()` is also true for the NUL device, so `command <nul`
    (used by non-interactive commands to mean "never prompt") would still open login
    windows and wait on questions. A console input handle is the reliable test.
    """
    stream = sys.stdin
    try:
        if stream is None or not stream.isatty():
            return False
        if os.name != "nt":
            return True
        import ctypes
        import msvcrt
        handle = msvcrt.get_osfhandle(stream.fileno())
        mode = ctypes.c_uint32()
        return bool(ctypes.windll.kernel32.GetConsoleMode(handle, ctypes.byref(mode)))
    except (AttributeError, OSError, TypeError, ValueError):
        return False
