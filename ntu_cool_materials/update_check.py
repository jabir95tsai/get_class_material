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

import json
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
