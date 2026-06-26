from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ntu_cool_materials import update_check
from ntu_cool_materials.update_check import check_for_update, is_newer


class IsNewerTests(unittest.TestCase):
    def test_basic_ordering(self) -> None:
        self.assertTrue(is_newer("0.2.18", "0.2.17"))
        self.assertTrue(is_newer("0.3.0", "0.2.17"))
        self.assertTrue(is_newer("1.0.0", "0.9.9"))
        self.assertFalse(is_newer("0.2.17", "0.2.17"))
        self.assertFalse(is_newer("0.2.16", "0.2.17"))

    def test_tolerates_nonnumeric_suffix(self) -> None:
        # Must not raise on rc / local-version tags.
        self.assertFalse(is_newer("0.2.17", "0.2.17rc1"))
        self.assertTrue(is_newer("0.2.18", "0.2.17+local"))


class CheckForUpdateTests(unittest.TestCase):
    def _cache(self, temp: str) -> Path:
        return Path(temp) / "update_check.json"

    def test_newer_available_returns_latest(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            result = check_for_update(
                "0.2.17", cache_path=self._cache(temp), now=1000.0,
                fetch=lambda: "0.2.18",
            )
        self.assertEqual(result, "0.2.18")

    def test_up_to_date_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            result = check_for_update(
                "0.2.18", cache_path=self._cache(temp), now=1000.0,
                fetch=lambda: "0.2.18",
            )
        self.assertIsNone(result)

    def test_unknown_version_never_nags(self) -> None:
        calls = []
        with tempfile.TemporaryDirectory() as temp:
            result = check_for_update(
                "0.0.0+unknown", cache_path=self._cache(temp),
                fetch=lambda: calls.append(1) or "9.9.9",
            )
        self.assertIsNone(result)
        self.assertEqual(calls, [], "must not even hit the network for an unknown version")

    def test_network_failure_is_silent(self) -> None:
        def boom():
            raise OSError("offline")
        with tempfile.TemporaryDirectory() as temp:
            result = check_for_update(
                "0.2.17", cache_path=self._cache(temp), now=1000.0, fetch=boom,
            )
        self.assertIsNone(result)

    def test_throttle_uses_cache_without_network(self) -> None:
        calls = []

        def fetch():
            calls.append(1)
            return "0.2.18"

        with tempfile.TemporaryDirectory() as temp:
            cache = self._cache(temp)
            # First call at t=1000 hits the network and writes the cache.
            first = check_for_update("0.2.17", cache_path=cache, now=1000.0, fetch=fetch)
            # Second call 1 hour later: within the 24h window → no network.
            second = check_for_update("0.2.17", cache_path=cache, now=1000.0 + 3600, fetch=fetch)
        self.assertEqual(first, "0.2.18")
        self.assertEqual(second, "0.2.18")
        self.assertEqual(len(calls), 1, "second call within interval must not hit the network")

    def test_refreshes_after_interval(self) -> None:
        calls = []

        def fetch():
            calls.append(1)
            return "0.2.18"

        with tempfile.TemporaryDirectory() as temp:
            cache = self._cache(temp)
            check_for_update("0.2.17", cache_path=cache, now=1000.0, fetch=fetch)
            # 25 hours later → past the 24h window → network again.
            check_for_update("0.2.17", cache_path=cache, now=1000.0 + 25 * 3600, fetch=fetch)
        self.assertEqual(len(calls), 2)

    def test_falls_back_to_cache_when_network_later_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            cache = self._cache(temp)
            check_for_update("0.2.17", cache_path=cache, now=1000.0, fetch=lambda: "0.2.18")

            def boom():
                raise OSError("offline")

            # Past interval so it tries the network (fails) → should reuse cache.
            result = check_for_update(
                "0.2.17", cache_path=cache, now=1000.0 + 25 * 3600, fetch=boom,
            )
        self.assertEqual(result, "0.2.18")

    def test_corrupt_cache_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            cache = self._cache(temp)
            cache.write_text("{not json", encoding="utf-8")
            result = check_for_update(
                "0.2.17", cache_path=cache, now=1000.0, fetch=lambda: "0.2.18",
            )
        self.assertEqual(result, "0.2.18")

    def test_writes_cache_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            cache = self._cache(temp)
            check_for_update("0.2.17", cache_path=cache, now=1234.0, fetch=lambda: "0.2.18")
            data = json.loads(cache.read_text(encoding="utf-8"))
        self.assertEqual(data["latest"], "0.2.18")
        self.assertEqual(data["last_check"], 1234.0)


if __name__ == "__main__":
    unittest.main()
