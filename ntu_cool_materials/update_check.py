"""Best-effort 'is there a newer version on PyPI?' check.

Design rules (a startup nag must never get in the way):
  - Never raises. Any network/parse/cache error → returns None (no nag).
  - Hits the network at most once per `interval_sec` (default 24h); between
    network checks it compares against the cached latest version, so the user
    still gets reminded every run while they're behind without re-hitting PyPI.
  - Short timeout so a slow/offline network can't stall startup.

`check_for_update` is the entry point and is pure enough to unit-test by
injecting `fetch` and `now`.
"""
from __future__ import annotations

import datetime
import json
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

PYPI_JSON_URL = "https://pypi.org/pypi/get-class-material/json"
_CHECK_INTERVAL_SEC = 24 * 3600
_UNKNOWN_VERSION = "0.0.0+unknown"


def _parse_version(value: str) -> tuple[int, ...]:
    """Lenient numeric-tuple parse: '0.2.17' -> (0, 2, 17). Non-numeric
    suffixes (e.g. '1rc2', '+unknown') are truncated to their leading digits."""
    parts: list[int] = []
    for chunk in str(value).split("."):
        digits = ""
        for ch in chunk:
            if ch.isdigit():
                digits += ch
            else:
                break
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def is_newer(latest: str, current: str) -> bool:
    return _parse_version(latest) > _parse_version(current)


def _fetch_latest_version(timeout: float) -> str:
    request = urllib.request.Request(PYPI_JSON_URL, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8"))
    return str(data["info"]["version"])


def check_for_update(
    current_version: str,
    *,
    cache_path: Path,
    timeout: float = 3.0,
    now: float | None = None,
    interval_sec: int = _CHECK_INTERVAL_SEC,
    fetch=None,
) -> str | None:
    """Return the latest version string if PyPI has a newer one, else None.

    Network is contacted at most once per `interval_sec`; results are cached in
    `cache_path`. Never raises.
    """
    if not current_version or current_version == _UNKNOWN_VERSION:
        return None  # running from an uninstalled source tree — don't nag

    now = time.time() if now is None else now
    fetch = fetch or (lambda: _fetch_latest_version(timeout))

    try:
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
        if not isinstance(cache, dict):
            cache = {}
    except Exception:
        cache = {}

    last_check = cache.get("last_check", 0)
    cached_latest = cache.get("latest")
    fresh = (
        isinstance(last_check, (int, float))
        and (now - last_check) < interval_sec
        and bool(cached_latest)
    )

    if fresh:
        latest = str(cached_latest)
    else:
        try:
            latest = fetch()
        except Exception:
            latest = None
        if latest:
            try:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_text(
                    json.dumps({"last_check": now, "latest": latest}), encoding="utf-8"
                )
            except OSError:
                pass
        elif cached_latest:
            latest = str(cached_latest)  # network down → fall back to cache

    if latest and is_newer(latest, current_version):
        return latest
    return None


def yt_dlp_version_age_days(version_str: str, now: datetime.date | None = None) -> int | None:
    """Calculate the age in days of a yt-dlp release (version is YYYY.MM.DD[.patch])."""
    if not version_str:
        return None
    m = re.match(r"^(\d{4})\.(\d{1,2})\.(\d{1,2})", version_str.strip())
    if not m:
        return None
    try:
        ver_date = datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        current_date = now or datetime.date.today()
        return (current_date - ver_date).days
    except (ValueError, OverflowError):
        return None


def get_yt_dlp_version(yt_dlp: str = "yt-dlp") -> str | None:
    """Return the installed yt-dlp version string, or None if unavailable."""
    try:
        import importlib.metadata
        return importlib.metadata.version("yt-dlp")
    except Exception:
        pass
    try:
        res = subprocess.run([yt_dlp, "--version"], capture_output=True, text=True, timeout=5)
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    except Exception:
        pass
    return None


def update_yt_dlp(yt_dlp: str = "yt-dlp") -> tuple[bool, str]:
    """Attempt to update yt-dlp using pip, falling back to yt-dlp -U.

    Returns (success, message).
    """
    from .i18n import t
    # 1. First try python -m pip install --upgrade yt-dlp
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if proc.returncode == 0:
            # Invalidate importlib caches to reflect newly installed package version
            import importlib
            importlib.invalidate_caches()
            new_ver = get_yt_dlp_version(yt_dlp) or t("最新版", "the latest version")
            return True, t(f"已透過 pip 升級至 {new_ver}", f"updated to {new_ver} via pip")
    except Exception:
        pass

    # 2. Fallback: try yt-dlp -U if executable is on PATH
    if shutil.which(yt_dlp):
        try:
            proc = subprocess.run([yt_dlp, "-U"], capture_output=True, text=True, timeout=120)
            if proc.returncode == 0:
                new_ver = get_yt_dlp_version(yt_dlp) or t("最新版", "the latest version")
                return True, t(f"已透過 yt-dlp -U 升級至 {new_ver}", f"updated to {new_ver} via yt-dlp -U")
        except Exception:
            pass

    return False, t("更新失敗，請手動執行 pip install --upgrade yt-dlp",
                    "update failed; run pip install --upgrade yt-dlp manually")


def confirm_yt_dlp_update(prompt: str, *, input_fn=None) -> bool:
    """Ask before changing the user's Python environment; never in non-interactive runs."""
    if input_fn is None:
        if not sys.stdin.isatty():
            return False
        input_fn = input
    try:
        answer = input_fn(prompt).strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return answer in {"", "y", "yes"}


def ensure_yt_dlp_updated(
    yt_dlp: str = "yt-dlp",
    max_age_days: int = 60,
    now: datetime.date | None = None,
    *,
    confirm=confirm_yt_dlp_update,
) -> bool | None:
    """Offer to update yt-dlp when it is older than max_age_days.

    Returns None when no update was needed (or the version is unknown), True
    after a successful update, and False when the user declined or it failed.
    """
    version = get_yt_dlp_version(yt_dlp)
    if not version:
        return None
    age = yt_dlp_version_age_days(version, now=now)
    if age is None or age < max_age_days:
        return None

    from .i18n import t
    if not confirm(t(
        f"  [yt-dlp] 目前版本 ({version}) 已發布 {age} 天，YouTube 下載可能失敗。現在更新嗎？[Y/n]: ",
        f"  [yt-dlp] current version ({version}) is {age} days old; YouTube downloads may fail. Update now? [Y/n]: ",
    )):
        print(t("  [yt-dlp] 略過更新；可稍後執行 pip install --upgrade yt-dlp",
                "  [yt-dlp] update skipped; run pip install --upgrade yt-dlp later"))
        return False
    ok, msg = update_yt_dlp(yt_dlp)
    print(f"  [yt-dlp] {msg}")
    return ok
