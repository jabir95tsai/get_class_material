from __future__ import annotations

import threading
import unittest

from ntu_cool_materials.spinner import spinner


class _FakeStream:
    """Records writes; configurable isatty. Thread-safe because the spinner
    writes from a background thread while the test reads from the main one."""

    def __init__(self, *, isatty: bool) -> None:
        self._isatty = isatty
        self._lock = threading.Lock()
        self.chunks: list[str] = []

    def isatty(self) -> bool:
        return self._isatty

    def write(self, text: str) -> int:
        with self._lock:
            self.chunks.append(text)
        return len(text)

    def flush(self) -> None:
        pass

    @property
    def text(self) -> str:
        with self._lock:
            return "".join(self.chunks)


class SpinnerTests(unittest.TestCase):
    def test_non_tty_prints_message_once_no_animation(self) -> None:
        stream = _FakeStream(isatty=False)
        with spinner("Loading…", stream=stream):
            pass
        # One plain line, no carriage-return animation frames.
        self.assertEqual(stream.text, "Loading…\n")
        self.assertNotIn("\r", stream.text)

    def test_tty_animates_and_clears_line(self) -> None:
        stream = _FakeStream(isatty=True)
        with spinner("Loading…", stream=stream, interval=0.01):
            # Give the animation thread a beat to draw at least one frame.
            threading.Event().wait(0.05)
        text = stream.text
        # Animation uses carriage returns; the final write clears the line.
        self.assertIn("\r", text)
        self.assertTrue(text.rstrip().endswith("") or text.endswith("\r"))
        # The trailing clear leaves nothing but spaces after the last \r.
        tail = text.rsplit("\r", 1)[-1]
        self.assertEqual(tail.strip(), "")

    def test_exception_propagates_and_thread_stops(self) -> None:
        stream = _FakeStream(isatty=True)
        before = threading.active_count()
        with self.assertRaises(ValueError):
            with spinner("Working…", stream=stream, interval=0.01):
                raise ValueError("boom")
        # The daemon thread is joined on exit — no leak.
        threading.Event().wait(0.05)
        self.assertLessEqual(threading.active_count(), before)

    def test_returns_value_is_not_swallowed(self) -> None:
        # The context manager yields None but must not interfere with the
        # block's own return path (caller returns from inside the with).
        stream = _FakeStream(isatty=True)

        def work() -> int:
            with spinner("…", stream=stream, interval=0.01):
                return 42

        self.assertEqual(work(), 42)


if __name__ == "__main__":
    unittest.main()
