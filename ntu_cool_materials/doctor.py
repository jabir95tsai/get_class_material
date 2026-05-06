"""Diagnostic + auto-installer for ntu-cool-material.

Two modes:

- `run_doctor()` — full report. Shows every check (✓/⚠/✗) and any fix hints.
- `ensure_ready()` — quiet "first-run" check used by `pick`. Only prints when
  something is missing, attempts to auto-install where it safely can, prints
  copy-pasteable instructions where it can't.

Auto-install covers: pip-installable Python deps, the Playwright Chromium binary,
and (best-effort, with user confirmation) Node.js / ffmpeg via winget on Windows
or Homebrew on macOS. Linux package managers vary too widely to attempt; we
print the right command for the user to run themselves.
"""
from __future__ import annotations

import platform
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from .i18n import t


GREEN_CHECK = "✓"
RED_CROSS = "✗"
YELLOW_WARN = "⚠"


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str = ""
    fix_command: str = ""              # human-readable command (shown to user)
    auto_install: callable | None = None  # function() -> bool, returns True on success
    optional: bool = False             # only-needed-for-X check; doesn't block running


# ---- low-level helpers ----

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


def _has(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def _stream_subprocess(cmd: list[str], desc: str) -> bool:
    """Run a command, streaming output. Returns True on success."""
    print(f"    → 執行: {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd)
        if result.returncode == 0:
            print(f"    ✓ {desc} 完成")
            return True
        print(f"    ✗ {desc} 失敗 (exit {result.returncode})")
        return False
    except FileNotFoundError as exc:
        print(f"    ✗ {desc}: {exc}")
        return False


# ---- auto-install hooks ----

def _install_chromium() -> bool:
    return _stream_subprocess(
        [sys.executable, "-m", "playwright", "install", "chromium"],
        "install Playwright Chromium",
    )


def _install_winget(package: str) -> bool:
    if not _has("winget"):
        print("    (找不到 winget,請依照上方提示手動安裝)")
        return False
    return _stream_subprocess(
        ["winget", "install", "--accept-source-agreements", "--accept-package-agreements", "-e", "--id", package],
        f"用 winget 安裝 {package}",
    )


def _install_brew(package: str) -> bool:
    if not _has("brew"):
        print("    (找不到 Homebrew,請依照上方提示手動安裝)")
        return False
    return _stream_subprocess(["brew", "install", package], f"用 Homebrew 安裝 {package}")


def _install_node_auto() -> bool:
    sys_ = platform.system()
    if sys_ == "Windows":
        return _install_winget("OpenJS.NodeJS")
    if sys_ == "Darwin":
        return _install_brew("node")
    return False


def _install_ffmpeg_auto() -> bool:
    sys_ = platform.system()
    if sys_ == "Windows":
        return _install_winget("Gyan.FFmpeg")
    if sys_ == "Darwin":
        return _install_brew("ffmpeg")
    return False


# ---- checks ----

def check_python() -> CheckResult:
    v = sys.version_info
    return CheckResult(
        name="Python 3.11 以上",
        ok=(v.major, v.minor) >= (3, 11),
        detail=f"Python {v.major}.{v.minor}.{v.micro}",
        fix_command="到 https://www.python.org/downloads/ 升級 Python",
    )


def check_playwright_chromium() -> CheckResult:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return CheckResult(
            name="Playwright (Python 套件)",
            ok=False,
            detail="未安裝",
            fix_command=f"{sys.executable} -m pip install --upgrade playwright",
            auto_install=lambda: _stream_subprocess(
                [sys.executable, "-m", "pip", "install", "--upgrade", "playwright"],
                "安裝 playwright",
            ),
        )
    try:
        with sync_playwright() as p:
            executable = p.chromium.executable_path
        ok = bool(executable) and Path(executable).exists()
        return CheckResult(
            name="Playwright Chromium",
            ok=ok,
            detail=f"已安裝 ({Path(executable).name})" if ok else "瀏覽器尚未下載",
            fix_command=f"{sys.executable} -m playwright install chromium",
            auto_install=_install_chromium if not ok else None,
        )
    except Exception as exc:
        return CheckResult(
            name="Playwright Chromium",
            ok=False,
            detail=f"偵測失敗: {exc}",
            fix_command=f"{sys.executable} -m playwright install chromium",
            auto_install=_install_chromium,
        )


def check_yt_dlp() -> CheckResult:
    if _has("yt-dlp"):
        code, line = _run(["yt-dlp", "--version"])
        return CheckResult(name="yt-dlp", ok=code == 0, detail=line if code == 0 else "")
    return CheckResult(
        name="yt-dlp",
        ok=False,
        detail="不在 PATH 上",
        fix_command=f"{sys.executable} -m pip install --upgrade yt-dlp",
        auto_install=lambda: _stream_subprocess(
            [sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp"],
            "安裝 yt-dlp",
        ),
    )


def check_node() -> CheckResult:
    if _has("node"):
        code, line = _run(["node", "--version"])
        return CheckResult(name="Node.js (下載 YouTube 影片用)", ok=code == 0, detail=line if code == 0 else "")
    return CheckResult(
        name="Node.js (下載 YouTube 影片用)",
        ok=False,
        optional=True,
        detail="未安裝 (沒有它 YouTube 影片畫質會卡在 360p 或下載失敗)",
        fix_command=_platform_install_hint(
            mac="brew install node",
            linux="到 https://nodejs.org/ 找你的發行版的安裝方式",
            windows="winget install OpenJS.NodeJS",
        ),
        auto_install=_install_node_auto,
    )


def check_ffmpeg() -> CheckResult:
    if _has("ffmpeg"):
        code, line = _run(["ffmpeg", "-version"])
        return CheckResult(
            name="ffmpeg (下載 YouTube 影片用)",
            ok=code == 0,
            detail=line.split(" Copyright")[0] if line else "",
        )
    return CheckResult(
        name="ffmpeg (下載 YouTube 影片用)",
        ok=False,
        optional=True,
        detail="未安裝 (用來合併 YouTube 影音串流)",
        fix_command=_platform_install_hint(
            mac="brew install ffmpeg",
            linux="apt install ffmpeg (或你的發行版的對應指令)",
            windows="winget install Gyan.FFmpeg",
        ),
        auto_install=_install_ffmpeg_auto,
    )


def check_ntu_session(headers_path: Path) -> CheckResult:
    if not headers_path.exists():
        return CheckResult(
            name="NTU COOL 登入憑證",
            ok=False,
            optional=True,
            detail=f"找不到 {headers_path}",
            fix_command="ntu-cool-gcm --refresh-session (會開啟瀏覽器讓你登入)",
        )
    age_hr = (time.time() - headers_path.stat().st_mtime) / 3600
    age_label = (
        f"{int(age_hr/24)} 天前" if age_hr >= 24 else
        f"{int(age_hr)} 小時前" if age_hr >= 1 else "剛刷新"
    )
    return CheckResult(name="NTU COOL 登入憑證", ok=True, optional=True,
                       detail=f"{headers_path} ({age_label})")


def check_youtube_cookies(cookies_path: Path) -> CheckResult:
    if not cookies_path.exists():
        return CheckResult(
            name="YouTube cookies (下載不公開影片用)",
            ok=False,
            optional=True,
            detail=f"找不到 {cookies_path}",
            fix_command="youtube-cookies (會開啟瀏覽器讓你登入 Google)",
        )
    age_hr = (time.time() - cookies_path.stat().st_mtime) / 3600
    age_label = (
        f"{int(age_hr/24)} 天前" if age_hr >= 24 else
        f"{int(age_hr)} 小時前" if age_hr >= 1 else "剛刷新"
    )
    return CheckResult(name="YouTube cookies (下載不公開影片用)", ok=True, optional=True,
                       detail=f"{cookies_path} ({age_label})")


# ---- entry points ----

def _all_checks(headers_path: Path, youtube_cookies_path: Path) -> list[CheckResult]:
    return [
        check_python(),
        check_playwright_chromium(),
        check_yt_dlp(),
        check_node(),
        check_ffmpeg(),
        check_ntu_session(headers_path),
        check_youtube_cookies(youtube_cookies_path),
    ]


def _emit(check: CheckResult) -> None:
    if check.ok:
        mark = GREEN_CHECK
    elif check.optional:
        mark = YELLOW_WARN
    else:
        mark = RED_CROSS
    print(f"  {mark} {check.name}  {check.detail}")
    if not check.ok and check.fix_command:
        print(t(f"      → 修復方式: {check.fix_command}", f"      → fix: {check.fix_command}"))


def run_doctor(
    *,
    headers_path: Path = Path(".secrets/ntu_cool_headers.txt"),
    youtube_cookies_path: Path = Path(".secrets/youtube_cookies.txt"),
    fix: bool = False,
) -> int:
    """Standalone `doctor` subcommand: report (and optionally auto-fix) everything."""
    print(t("ntu-cool-material 系統檢查", "ntu-cool-material doctor"))
    print(t(
        f"作業系統: {platform.system()} {platform.release()}\n",
        f"Platform: {platform.system()} {platform.release()}\n",
    ))
    checks = _all_checks(headers_path, youtube_cookies_path)
    for c in checks:
        _emit(c)

    if fix:
        broken = [c for c in checks if not c.ok and c.auto_install is not None]
        if broken:
            print(t(
                f"\n嘗試自動修復 {len(broken)} 個項目...",
                f"\nAttempting auto-fix for {len(broken)} item(s)...",
            ))
            for c in broken:
                print(t(f"  修復: {c.name}", f"  fixing: {c.name}"))
                c.auto_install()
            print(t("\n重新檢查...\n", "\nRe-checking...\n"))
            checks = _all_checks(headers_path, youtube_cookies_path)
            for c in checks:
                _emit(c)

    failed = [c for c in checks if not c.ok and not c.optional]
    if not failed:
        print(t(f"\n{GREEN_CHECK} 環境就緒。", f"\n{GREEN_CHECK} Ready."))
        return 0
    print(t(
        f"\n{RED_CROSS} 還缺少 {len(failed)} 個必要項目。",
        f"\n{RED_CROSS} {len(failed)} required item(s) still missing.",
    ))
    return 1


def ensure_ready(
    *,
    headers_path: Path = Path(".secrets/ntu_cool_headers.txt"),
    youtube_cookies_path: Path = Path(".secrets/youtube_cookies.txt"),
    interactive: bool = True,
) -> bool:
    """Quiet first-run check used by `pick`. Returns True iff every required item is OK
    after auto-fix attempts. Stays silent when everything is already fine.

    Strategy:
      1. Run all required checks.
      2. If anything fails with an auto_install hook, run it (no prompt — it's a routine
         setup install, not destructive).
      3. Re-check; if still missing required items, print remaining hints and return False.
    """
    required_names = {"Python 3.11 以上", "Playwright Chromium", "yt-dlp"}

    initial = [c for c in _all_checks(headers_path, youtube_cookies_path) if c.name in required_names]
    missing = [c for c in initial if not c.ok]
    if not missing:
        return True

    print(t("第一次設定: 還缺少幾個東西。\n", "First-time setup: a few things still need to be installed.\n"))
    for c in missing:
        print(f"  {RED_CROSS} {c.name} — {c.detail}")

    auto_fixable = [c for c in missing if c.auto_install is not None]
    manual = [c for c in missing if c.auto_install is None]

    if auto_fixable:
        print(t(
            f"\n自動安裝 {len(auto_fixable)} 個項目...\n",
            f"\nInstalling {len(auto_fixable)} item(s) automatically...\n",
        ))
        for c in auto_fixable:
            print(f"  → {c.name}")
            c.auto_install()
            print()

    if manual:
        print(t("請手動安裝以下項目後再重試:\n", "Please install these manually, then re-run:\n"))
        for c in manual:
            print(f"  • {c.name}: {c.fix_command}")
        return False

    after = [c for c in _all_checks(headers_path, youtube_cookies_path) if c.name in required_names]
    still_missing = [c for c in after if not c.ok]
    if still_missing:
        print(t("自動安裝後仍有問題:\n", "Some items still failing after auto-install:\n"))
        for c in still_missing:
            print(f"  • {c.name}: {c.fix_command}")
        return False

    print(t(f"{GREEN_CHECK} 設定完成。\n", f"{GREEN_CHECK} Setup complete.\n"))
    return True
