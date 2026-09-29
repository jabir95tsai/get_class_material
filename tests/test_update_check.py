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


class YtDlpUpdateTests(unittest.TestCase):
    def test_version_age_calculation(self) -> None:
        import datetime
        now = datetime.date(2026, 9, 29)
        self.assertEqual(update_check.yt_dlp_version_age_days("2026.08.19", now=now), 41)
        self.assertEqual(update_check.yt_dlp_version_age_days("2026.04.10.235301", now=now), 172)
        self.assertEqual(update_check.yt_dlp_version_age_days("2026.4.10.235301.dev0", now=now), 172)
        self.assertIsNone(update_check.yt_dlp_version_age_days("invalid"))
        self.assertIsNone(update_check.yt_dlp_version_age_days(""))

    def test_ensure_yt_dlp_updated_skips_when_fresh(self) -> None:
        import datetime
        from unittest import mock
        now = datetime.date(2026, 9, 29)
        with mock.patch("ntu_cool_materials.update_check.get_yt_dlp_version", return_value="2026.08.19"):
            with mock.patch("ntu_cool_materials.update_check.update_yt_dlp") as mock_update:
                updated = update_check.ensure_yt_dlp_updated("yt-dlp", max_age_days=60, now=now)
                self.assertIsNone(updated)
                mock_update.assert_not_called()

    def test_ensure_yt_dlp_updated_triggers_when_old(self) -> None:
        import datetime
        from unittest import mock
        now = datetime.date(2026, 9, 29)
        with mock.patch("ntu_cool_materials.update_check.get_yt_dlp_version", return_value="2026.04.10"):
            with mock.patch("ntu_cool_materials.update_check.update_yt_dlp", return_value=(True, "ok")) as mock_update:
                updated = update_check.ensure_yt_dlp_updated("yt-dlp", max_age_days=60, now=now,
                                                             confirm=lambda _: True)
                self.assertTrue(updated)
                mock_update.assert_called_once_with("yt-dlp")

    def test_ensure_yt_dlp_updated_needs_consent(self) -> None:
        import datetime
        from unittest import mock
        now = datetime.date(2026, 9, 29)
        with mock.patch("ntu_cool_materials.update_check.get_yt_dlp_version", return_value="2026.04.10"), \
             mock.patch("ntu_cool_materials.update_check.update_yt_dlp") as mock_update, \
             mock.patch("ntu_cool_materials.console.stdin_is_interactive", return_value=False):
            declined = update_check.ensure_yt_dlp_updated("yt-dlp", now=now, confirm=lambda _: False)
            non_interactive = update_check.ensure_yt_dlp_updated("yt-dlp", now=now)
        self.assertIs(declined, False)
        self.assertIs(non_interactive, False)
        mock_update.assert_not_called()

    def test_confirm_defaults_to_yes_but_accepts_no(self) -> None:
        self.assertTrue(update_check.confirm_yt_dlp_update("?", input_fn=lambda _: ""))
        self.assertFalse(update_check.confirm_yt_dlp_update("?", input_fn=lambda _: "n"))

    def test_update_yt_dlp_pip_success(self) -> None:
        from unittest import mock
        mock_proc = mock.Mock(returncode=0)
        with mock.patch("subprocess.run", return_value=mock_proc):
            with mock.patch("ntu_cool_materials.update_check.get_yt_dlp_version", return_value="2026.8.19"):
                ok, msg = update_check.update_yt_dlp("yt-dlp")
                self.assertTrue(ok)
                self.assertIn("pip", msg)

    def test_update_yt_dlp_pip_fails_fallback_succeeds(self) -> None:
        from unittest import mock
        def fake_run(cmd, **kwargs):
            if "-m" in cmd and "pip" in cmd:
                return mock.Mock(returncode=1)
            if "-U" in cmd:
                return mock.Mock(returncode=0)
            return mock.Mock(returncode=1)

        with mock.patch("subprocess.run", side_effect=fake_run):
            with mock.patch("shutil.which", return_value="yt-dlp"):
                with mock.patch("ntu_cool_materials.update_check.get_yt_dlp_version", return_value="2026.8.19"):
                    ok, msg = update_check.update_yt_dlp("yt-dlp")
                    self.assertTrue(ok)
                    self.assertIn("yt-dlp -U", msg)


if __name__ == "__main__":
    unittest.main()
