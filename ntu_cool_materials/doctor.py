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

import os
import platform
import shutil
import subprocess
import sys
import sysconfig
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


def _scripts_dir() -> Path | None:
    """Where pip drops console-script .exe shims for this Python install."""
    try:
        path = sysconfig.get_path("scripts")
    except Exception:
        return None
    return Path(path) if path else None


def _normalized_path_entries() -> list[str]:
    raw = os.environ.get("PATH", "")
    out: list[str] = []
    for entry in raw.split(os.pathsep):
        if not entry:
            continue
        out.append(os.path.normcase(os.path.normpath(entry)))
    return out


def _scripts_on_path(scripts_dir: Path) -> bool:
    target = os.path.normcase(os.path.normpath(str(scripts_dir)))
    return target in _normalized_path_entries()


def _add_scripts_to_user_path_windows(scripts_dir: Path) -> bool:
    """Append scripts_dir to the current user's PATH (Windows only).

    We bypass `setx` for two reasons:
      1. setx truncates PATH at 1024 chars (legacy registry limit).
      2. setx reads from the process's PATH (Machine + User merged), so its
         "write" actually corrupts the User scope with Machine entries.
    `[Environment]::SetEnvironmentVariable(..., 'User')` reads + writes the
    HKCU\\Environment hive cleanly, no length limit, no admin required.
    The change only affects newly-launched processes — the current PowerShell
    won't see it until reopened, which is fine for our messaging.
    """
    if platform.system() != "Windows":
        return False

    scripts = str(scripts_dir)
    # Single-quote the path inside the PowerShell literal; PowerShell single
    # quotes don't expand $vars and treat backslashes literally, so Windows
    # paths drop in as-is. Embedded apostrophes (rare in usernames) get
    # doubled-up to escape them.
    ps_scripts = scripts.replace("'", "''")
    ps_script = (
        f"$scripts = '{ps_scripts}'; "
        "$user = [Environment]::GetEnvironmentVariable('PATH', 'User'); "
        "if (-not $user) { $user = '' }; "
        "$entries = @($user -split ';' | Where-Object { $_ -ne '' }); "
        "if ($entries -notcontains $scripts) { "
        "  if ($user) { [Environment]::SetEnvironmentVariable('PATH', $user.TrimEnd(';') + ';' + $scripts, 'User') } "
        "  else { [Environment]::SetEnvironmentVariable('PATH', $scripts, 'User') }; "
        "  Write-Output 'added' "
        "} else { Write-Output 'already-present' }"
    )

    ok = _stream_subprocess(
        ["powershell", "-NoProfile", "-Command", ps_script],
        f"add {scripts_dir} to user PATH",
    )
    if ok:
        print(t(
            "    ⚠ 請關掉這個視窗、重新打開,之後就能直接打 `ntu-cool-gcm` 啟動。",
            "    ⚠ Close this window, reopen it, then `ntu-cool-gcm` will work directly.",
        ))
    return ok


# Marker block we wrap our PATH export with on POSIX rc files. Lets re-runs
# detect "already added, don't duplicate" by string match, and gives the
# user something obvious to grep for / hand-remove if they ever want to
# undo it. Don't change these strings without a migration path — they're
# the idempotency key.
_POSIX_RC_MARKER_BEGIN = "# >>> get-class-material PATH fix >>>"
_POSIX_RC_MARKER_END = "# <<< get-class-material PATH fix <<<"


def _posix_shell_rc_path() -> Path | None:
    """Best-guess rc file to append PATH exports to.

    macOS: $SHELL is typically /bin/zsh; Terminal.app launches login shells
    so .zprofile is the canonical "ran-on-login" hook. We prefer .zprofile
    over .zshrc because .zshrc is interactive-only and skipped for some
    non-interactive contexts (cron, GUI launchers).

    Linux: zsh users uncommon; default to ~/.bashrc since most distros
    source it from both login and interactive shells.

    Fish + nushell + other exotic shells: not supported — too many
    syntactic variations. Returns None and the caller prints manual
    instructions.
    """
    shell = os.environ.get("SHELL", "")
    home = Path.home()
    name = os.path.basename(shell).lower()

    if name == "zsh":
        return home / ".zprofile"
    if name == "bash":
        # macOS bash users: .bash_profile (login shell convention).
        # Linux bash users: .bashrc (interactive shell convention).
        if platform.system() == "Darwin":
            return home / ".bash_profile"
        return home / ".bashrc"
    # Default for unknown shells on Mac (covers users who never changed
    # the default zsh but somehow have an empty / unusual $SHELL):
    if platform.system() == "Darwin":
        return home / ".zprofile"
    return None


def _add_scripts_to_user_path_posix(scripts_dir: Path) -> bool:
    """Append scripts_dir to the user's PATH by writing an export block to
    their shell rc file. Idempotent — re-runs detect the marker and skip."""
    rc_path = _posix_shell_rc_path()
    if rc_path is None:
        print(t(
            "    無法判斷你的 shell。請手動把下面這行加進你的 shell rc 檔案:",
            "    Could not detect your shell. Please add this line to your shell rc file:",
        ))
        print(f'      export PATH="$PATH:{scripts_dir}"')
        return False

    block = (
        f"\n{_POSIX_RC_MARKER_BEGIN}\n"
        f"# Added by ntu-cool-gcm doctor so the `ntu-cool-gcm` shortcut resolves.\n"
        f"# Remove this block to undo.\n"
        f'export PATH="$PATH:{scripts_dir}"\n'
        f"{_POSIX_RC_MARKER_END}\n"
    )

    try:
        existing = rc_path.read_text(encoding="utf-8") if rc_path.exists() else ""
    except OSError as exc:
        print(f"    無法讀取 {rc_path}: {exc}")
        return False

    # Idempotency: if either the marker OR a literal export line for this
    # exact directory is already present, do nothing. The marker covers
    # past runs by us; the literal-export check covers users who set up
    # PATH themselves by hand.
    if _POSIX_RC_MARKER_BEGIN in existing or f":{scripts_dir}" in existing or f"={scripts_dir}" in existing:
        print(f"    ✓ {rc_path} 已包含 PATH 設定,沒動")
        return True

    try:
        with rc_path.open("a", encoding="utf-8") as f:
            f.write(block)
    except OSError as exc:
        print(f"    無法寫入 {rc_path}: {exc}")
        return False

    print(f"    ✓ 已把 {scripts_dir} 加進 {rc_path}")
    print(t(
        f"    ⚠ 請執行 `source {rc_path}` 或開新的終端機,之後就能直接打 `ntu-cool-gcm`。",
        f"    ⚠ Run `source {rc_path}` or open a new terminal, then `ntu-cool-gcm` will work directly.",
    ))
    return True


def _add_scripts_to_user_path(scripts_dir: Path) -> bool:
    """Dispatch to the platform-appropriate PATH-fix routine."""
    if platform.system() == "Windows":
        return _add_scripts_to_user_path_windows(scripts_dir)
    return _add_scripts_to_user_path_posix(scripts_dir)


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
        # Node is optional in *both* branches: on Windows `shutil.which` can
        # resolve the WindowsApps execution-alias stub (a 0-byte launcher for
        # the Microsoft Store), so `node --version` exits non-zero even though
        # `node` is "on PATH". That must degrade to a ⚠ warning, never a
        # blocking ✗ — Node only gates the YouTube stage.
        return CheckResult(
            name="Node.js (下載 YouTube 影片用)",
            ok=code == 0,
            optional=True,
            detail=line if code == 0 else "偵測到 node 但無法執行 (可能是 Windows 商店捷徑)",
            fix_command=_platform_install_hint(
                mac="brew install node",
                linux="到 https://nodejs.org/ 找你的發行版的安裝方式",
                windows="winget install OpenJS.NodeJS",
            ),
            auto_install=_install_node_auto if code != 0 else None,
        )
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
        # Optional in both branches — same WindowsApps-stub reasoning as Node.
        return CheckResult(
            name="ffmpeg (下載 YouTube 影片用)",
            ok=code == 0,
            optional=True,
            detail=line.split(" Copyright")[0] if (line and code == 0) else "偵測到 ffmpeg 但無法執行",
            fix_command=_platform_install_hint(
                mac="brew install ffmpeg",
                linux="apt install ffmpeg (或你的發行版的對應指令)",
                windows="winget install Gyan.FFmpeg",
            ),
            auto_install=_install_ffmpeg_auto if code != 0 else None,
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


def _scripts_on_path_fix_hint(scripts_dir: Path) -> str:
    """Copy-pasteable manual instructions for the user-facing 'fix' field."""
    if platform.system() == "Windows":
        return (
            f'powershell -NoProfile -Command "[Environment]::SetEnvironmentVariable('
            f"'PATH', [Environment]::GetEnvironmentVariable('PATH', 'User').TrimEnd(';') + "
            f"';{scripts_dir}', 'User')\""
        )
    rc = _posix_shell_rc_path()
    if rc is None:
        return f'echo \'export PATH="$PATH:{scripts_dir}"\' >> ~/.profile'
    return f'echo \'export PATH="$PATH:{scripts_dir}"\' >> {rc}'


def check_scripts_on_path() -> CheckResult:
    """Python's scripts dir must be on PATH or pip's entry-point shims
    (`ntu-cool-gcm` etc.) won't resolve from a fresh shell.

    Cross-platform since 0.2.7: previously Windows-only, but the same
    pitfall hits Mac users who install Python from the python.org installer
    (`/Library/Frameworks/Python.framework/Versions/X.Y/bin` isn't on the
    default PATH) — Homebrew Python users avoid it because brew links
    binaries into /opt/homebrew/bin which is already on PATH.

    NOTE: keep the `name` string fixed (not localized) — it's used as a
    set-membership key by `ensure_ready`'s `recommended_names`. The rest
    of the user-facing strings here can vary by locale.
    """
    name = "Python Scripts 在 PATH 上"
    scripts_dir = _scripts_dir()
    if scripts_dir is None:
        return CheckResult(name=name, ok=True, optional=True, detail="(scripts dir unknown)")

    on_path = _scripts_on_path(scripts_dir)
    if on_path:
        return CheckResult(name=name, ok=True, detail=str(scripts_dir))

    return CheckResult(
        name=name,
        ok=False,
        optional=True,
        detail=f"{scripts_dir} 不在 PATH 上",
        fix_command=_scripts_on_path_fix_hint(scripts_dir),
        auto_install=lambda: _add_scripts_to_user_path(scripts_dir),
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
        check_scripts_on_path(),
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
    from . import __version__
    print(t("ntu-cool-material 系統檢查", "ntu-cool-material doctor"))
    print(f"get-class-material {__version__}")
    try:
        from .cli import _secrets_dir
        from .update_check import check_for_update
        _newer = check_for_update(__version__, cache_path=_secrets_dir() / "update_check.json")
        if _newer:
            print(t(
                f"  💡 有新版本 {_newer},更新: pip install --upgrade get-class-material",
                f"  💡 Update available: {_newer} — pip install --upgrade get-class-material",
            ))
    except Exception:
        pass
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
    # "Blocking" — without these, nothing works at all. Refuse to proceed if
    # we can't get them installed.
    blocking_names = {"Python 3.11 以上", "Playwright Chromium", "yt-dlp"}
    # "Strongly recommended" — needed for the YouTube stage but not the
    # PDF / Page / NTU CDN stages. Auto-install best-effort; if the install
    # fails (no winget, no brew, etc.), warn and let the user proceed —
    # download_youtube has its own pre-check that prints a clear install
    # hint at the moment YouTube downloads would fire.
    recommended_names = {
        "Node.js (下載 YouTube 影片用)",
        "ffmpeg (下載 YouTube 影片用)",
        # Not blocking — if Scripts isn't on PATH, the user invoked us via
        # `python -m ntu_cool_materials pick` and the pick already works.
        # The check exists to upgrade that to the shorter `ntu-cool-gcm`
        # for future runs.
        "Python Scripts 在 PATH 上",
    }
    relevant = blocking_names | recommended_names

    # Gathering the checks is the real wait the user sits through before the
    # course list shows (Playwright launches a context to detect Chromium —
    # multiple seconds). Spin a bar over it so a returning user, for whom this
    # is otherwise dead-silent, can see it's working rather than hung. The
    # spinner stops (and clears its line) before any of the messages below.
    from .spinner import spinner
    with spinner(t("檢查環境中…", "Checking your setup…")):
        initial = [c for c in _all_checks(headers_path, youtube_cookies_path) if c.name in relevant]
    missing = [c for c in initial if not c.ok]
    if not missing:
        return True

    print(t("第一次設定: 還缺少幾個東西。\n", "First-time setup: a few things still need to be installed.\n"))
    for c in missing:
        print(f"  {RED_CROSS} {c.name} — {c.detail}")

    auto_fixable = [c for c in missing if c.auto_install is not None]
    manual_blocking = [c for c in missing if c.auto_install is None and c.name in blocking_names]

    if auto_fixable:
        print(t(
            f"\n自動安裝 {len(auto_fixable)} 個項目...\n",
            f"\nInstalling {len(auto_fixable)} item(s) automatically...\n",
        ))
        for c in auto_fixable:
            print(f"  → {c.name}")
            c.auto_install()
            print()

    if manual_blocking:
        print(t("請手動安裝以下必要項目後再重試:\n", "Please install these required items manually, then re-run:\n"))
        for c in manual_blocking:
            print(f"  • {c.name}: {c.fix_command}")
        return False

    after = [c for c in _all_checks(headers_path, youtube_cookies_path) if c.name in relevant]
    still_missing_blocking = [c for c in after if not c.ok and c.name in blocking_names]
    still_missing_recommended = [c for c in after if not c.ok and c.name in recommended_names]

    if still_missing_blocking:
        print(t("自動安裝後必要項目仍有問題:\n", "Required items still failing after auto-install:\n"))
        for c in still_missing_blocking:
            print(f"  • {c.name}: {c.fix_command}")
        return False

    if still_missing_recommended:
        print(t(
            "\n以下項目沒裝起來(YouTube 下載會跳過):\n",
            "\nThese installs didn't take (YouTube downloads will be skipped):\n",
        ))
        for c in still_missing_recommended:
            print(f"  • {c.name}: {c.fix_command}")
        print(t(
            "  之後可以手動裝完再重新打開,或執行 `ntu-cool-materials doctor --fix` 再試一次。\n",
            "  Install them manually then restart this terminal, or re-run `ntu-cool-materials doctor --fix`.\n",
        ))
        # don't return False — PDFs / Pages / cool-video still work

    print(t(f"{GREEN_CHECK} 設定完成。\n", f"{GREEN_CHECK} Setup complete.\n"))
    return True
