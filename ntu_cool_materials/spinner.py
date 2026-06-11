"""A tiny rotating-bar spinner for the silent network waits.

Used to reassure the user during the gap between launching `ntu-cool-gcm`
and the course list appearing (the `check_auth` + `list_courses` round-trips),
so a slow network doesn't look like a hang.

Design notes:
  - TTY-only animation. When stdout isn't a terminal (CI, pipes, redirected
    output), it prints the message once and skips the animation so logs stay
    clean and don't fill with carriage-return spam.
  - The animation runs on a daemon thread; the wrapped block runs on the
    caller's thread, unchanged. Only wrap *silent* work — anything that prints
    to the same stream inside the block would interleave with the spinner.
  - ASCII frames (hyphen, backslash, pipe, slash) on purpose: they render on
    every Windows console (legacy conhost included), unlike Unicode braille.
"""
from __future__ import annotations

import contextlib
import itertools
import sys
import threading
from collections.abc import Iterator


_FRAMES = ["-", "\\", "|", "/"]


def _display_width(text: str) -> int:
    """Rough terminal cell width — count CJK/fullwidth chars as 2 so the
    line-clear below fully erases CJK messages instead of leaving tails."""
    width = 0
    for ch in text:
        width += 2 if ord(ch) >= 0x1100 and _is_wide(ch) else 1
    return width


def _is_wide(ch: str) -> bool:
    import unicodedata

    return unicodedata.east_asian_width(ch) in ("W", "F")


@contextlib.contextmanager
def spinner(message: str, *, stream=None, interval: float = 0.1) -> Iterator[None]:
    """Show `message` with a rotating bar while the wrapped block runs."""
    out = stream if stream is not None else sys.stdout

    try:
        isatty = bool(out.isatty())
    except Exception:
        isatty = False

    if not isatty:
        # Non-interactive: emit the message once, no animation.
        with contextlib.suppress(Exception):
            out.write(message + "\n")
            out.flush()
        yield
        return

    stop = threading.Event()

    def _animate() -> None:
        for frame in itertools.cycle(_FRAMES):
            if stop.is_set():
                break
            try:
                out.write(f"\r{frame} {message}")
                out.flush()
            except Exception:
                return
            stop.wait(interval)
        # Clear the line so the next output starts clean.
        with contextlib.suppress(Exception):
            out.write("\r" + " " * (_display_width(message) + 2) + "\r")
            out.flush()

    thread = threading.Thread(target=_animate, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=1.0)
