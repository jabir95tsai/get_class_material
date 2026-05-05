"""Diagnostic command: check the user's environment and report what's missing.

Usage: `ntu-cool-materials doctor` (no args). Returns 0 iff every required item is present;
returns 1 if any required item is missing. Optional items only warn.
"""
from __future__ import annotations

import platform
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path


GREEN_CHECK = "✓"   # ✓
RED_CROSS = "✗"     # ✗
YELLOW_WARN = "⚠"   # ⚠ (single-cell on most terminals)


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str = ""
    fix: str = ""
    optional: bool = False


def _run(cmd: list[str], timeout: int = 5) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        out = (r.stdout or r.stderr or "").strip().splitlines()
        return r.returncode, out[0] if out else ""
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return -1, str(exc)


def _platform_install_hint(*, mac: str, linux: str, windows: str) -> str:
    sys_ = platform.system()
    if sys_ == "Darwin":
        return mac
    if sys_ == "Windows":
        return windows
    return linux


def check_python() -> CheckResult:
    v = sys.version_info
    ok = (v.major, v.minor) >= (3, 11)
    return CheckResult(
        name="Python 3.11+",
        ok=ok,
        detail=f"Python {v.major}.{v.minor}.{v.micro}",
        fix="Upgrade Python from https://www.python.org/downloads/",
    )


def check_yt_dlp() -> CheckResult:
    if shutil.which("yt-dlp") is None:
        return CheckResult(
            name="yt-dlp",
            ok=False,
            detail="not on PATH",
            fix="pip install --upgrade yt-dlp",
        )
    code, line = _run(["yt-dlp", "--version"])
    return CheckResult(
        name="yt-dlp",
        ok=code == 0,
        detail=line if code == 0 else "failed to run --version",
        fix="pip install --upgrade yt-dlp",
    )


def check_node() -> CheckResult:
    if shutil.which("node") is None:
        return CheckResult(
            name="Node.js",
            ok=False,
            detail="not on PATH (yt-dlp needs it to solve YouTube's JS challenge — without it, "
                   "videos are stuck at 360p or fail outright)",
            fix=_platform_install_hint(
                mac="brew install node",
                linux="see https://nodejs.org/ for your distro's package",
                windows="winget install OpenJS.NodeJS  (or download from https://nodejs.org/)",
            ),
        )
    code, line = _run(["node", "--version"])
    return CheckResult(name="Node.js", ok=code == 0, detail=line if code == 0 else "")


def check_ffmpeg() -> CheckResult:
    if shutil.which("ffmpeg") is None:
        return CheckResult(
            name="ffmpeg",
            ok=False,
            detail="not on PATH (used to merge YouTube video+audio streams)",
            fix=_platform_install_hint(
                mac="brew install ffmpeg",
                linux="sudo apt install ffmpeg  (Debian/Ubuntu) or your distro's equivalent",
                windows="winget install Gyan.FFmpeg  (or scoop install ffmpeg)",
            ),
        )
    code, line = _run(["ffmpeg", "-version"])
    return CheckResult(
        name="ffmpeg", ok=code == 0,
        detail=line.split(" Copyright")[0] if line else "",
    )


def check_playwright_chromium() -> CheckResult:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return CheckResult(
            name="Playwright (Python pkg)",
            ok=False,
            detail="not installed",
            fix='pip install --upgrade playwright',
        )
    # Probe the chromium binary by trying to launch it briefly.
    try:
        with sync_playwright() as p:
            browser_type = p.chromium
            executable = browser_type.executable_path
        ok = bool(executable) and Path(executable).exists()
        if ok:
            return CheckResult(
                name="Playwright Chromium",
                ok=True,
                detail=f"installed at {Path(executable).name}",
            )
        return CheckResult(
            name="Playwright Chromium",
            ok=False,
            detail="binary not found",
            fix="python -m playwright install chromium",
        )
    except Exception as exc:
        return CheckResult(
            name="Playwright Chromium",
            ok=False,
            detail=f"probe failed: {exc}",
            fix="python -m playwright install chromium",
        )


def check_ntu_session(headers_path: Path) -> CheckResult:
    if not headers_path.exists():
        return CheckResult(
            name="NTU COOL session",
            ok=False,
            optional=True,
            detail=f"no headers file at {headers_path}",
            fix="ntu-cool-gcm --refresh-session  (will open a browser to log in)",
        )
    age_sec = time.time() - headers_path.stat().st_mtime
    age_hr = age_sec / 3600
    age_label = (
        f"{int(age_hr/24)} day(s) old" if age_hr >= 24 else
        f"{int(age_hr)} hour(s) old" if age_hr >= 1 else
        "fresh"
    )
    return CheckResult(
        name="NTU COOL session",
        ok=True,
        optional=True,
        detail=f"{headers_path} ({age_label})",
    )


def check_youtube_cookies(cookies_path: Path) -> CheckResult:
    if not cookies_path.exists():
        return CheckResult(
            name="YouTube cookies (for unlisted videos)",
            ok=False,
            optional=True,
            detail=f"no cookies file at {cookies_path}",
            fix="youtube-cookies  (will open a browser to log in to your Google account)",
        )
    age_sec = time.time() - cookies_path.stat().st_mtime
    age_hr = age_sec / 3600
    age_label = (
        f"{int(age_hr/24)} day(s) old" if age_hr >= 24 else
        f"{int(age_hr)} hour(s) old" if age_hr >= 1 else
        "fresh"
    )
    return CheckResult(
        name="YouTube cookies (for unlisted videos)",
        ok=True,
        optional=True,
        detail=f"{cookies_path} ({age_label})",
    )


def run_doctor(
    *,
    headers_path: Path = Path(".secrets/ntu_cool_headers.txt"),
    youtube_cookies_path: Path = Path(".secrets/youtube_cookies.txt"),
) -> int:
    print("ntu-cool-materials doctor")
    print(f"Platform: {platform.system()} {platform.release()}")
    print()

    required = [
        check_python(),
        check_yt_dlp(),
        check_node(),
        check_ffmpeg(),
        check_playwright_chromium(),
    ]
    optional = [
        check_ntu_session(headers_path),
        check_youtube_cookies(youtube_cookies_path),
    ]

    def _emit(check: CheckResult) -> None:
        if check.ok:
            mark = GREEN_CHECK
            label = check.detail
        elif check.optional:
            mark = YELLOW_WARN
            label = check.detail
        else:
            mark = RED_CROSS
            label = check.detail
        print(f"  {mark} {check.name}  {label}")
        if not check.ok and check.fix:
            print(f"      → fix: {check.fix}")

    print("Required:")
    for c in required:
        _emit(c)
    print()
    print("Optional (only needed for first-time setup):")
    for c in optional:
        _emit(c)
    print()

    failed_required = [c for c in required if not c.ok]
    failed_optional = [c for c in optional if not c.ok]

    if not failed_required and not failed_optional:
        print(f"{GREEN_CHECK} All set. You can run `ntu-cool-gcm` now.")
        return 0
    if not failed_required:
        print(f"{YELLOW_WARN} Required tools OK. {len(failed_optional)} optional setup step(s) "
              "still needed for the first run — see hints above.")
        return 0
    print(f"{RED_CROSS} {len(failed_required)} required tool(s) missing. Install them, then re-run "
          "`ntu-cool-materials doctor` to verify.")
    return 1
