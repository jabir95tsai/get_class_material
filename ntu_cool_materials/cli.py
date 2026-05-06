from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from .canvas_client import CanvasAPIError, CanvasClient
from .announcements import announcement_markdown, write_announcements
from .browser_session import refresh_headers_file
from .course_pipeline import (
    BrowserSession,
    _dump_cookies_to_headers_file,
    download_course,
    open_browser_session,
)
from .doctor import ensure_ready, run_doctor
from .session_client import CanvasSessionClient, read_headers_file
from .storage import ManifestStore
from .sync import SyncStats, sync_course_materials


DEFAULT_BASE_URL = "https://cool.ntu.edu.tw"


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if not getattr(args, "command", None):
        parser.print_help()
        return 2

    base_url = args.base_url or os.environ.get("NTU_COOL_BASE_URL") or DEFAULT_BASE_URL

    try:
        if args.command == "courses":
            client = _courses_client(base_url, args)
            if client is None:
                return 2
            return _cmd_courses(client, args)
        if args.command == "announcements":
            if args.refresh_session:
                try:
                    refresh_headers_file(
                        course_id=args.course_id,
                        base_url=base_url,
                        profile_dir=Path(args.profile_dir),
                        headers_path=Path(args.headers_file),
                        headless=args.headless_refresh,
                    )
                except Exception as exc:
                    print(f"Could not refresh browser session: {exc}")
                    return 1
            client = _announcements_client(base_url, args)
            if client is None:
                return 2
            return _cmd_announcements(client, args)
        if args.command == "sync":
            token = _require_token(args)
            if not token:
                return 2
            client = CanvasClient(base_url=base_url, token=token)
            return _cmd_sync(client, args)
        if args.command == "download-course":
            return _cmd_download_course(base_url, args)
        if args.command == "pick":
            return _cmd_pick(base_url, args)
        if args.command == "doctor":
            return run_doctor(
                headers_path=Path(args.headers_file),
                youtube_cookies_path=Path(args.youtube_cookies),
                fix=args.fix,
            )
    except CanvasAPIError as exc:
        print(f"Canvas API error: {exc}")
        return 1

    parser.print_help()
    return 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ntu-cool-materials",
        description="Sync NTU COOL / Canvas course materials.",
    )
    parser.add_argument("--base-url", default=os.environ.get("NTU_COOL_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--token", default=None, help="Canvas access token. Prefer env var instead.")
    parser.add_argument("--token-env", default="NTU_COOL_TOKEN", help="Environment variable for token.")

    subparsers = parser.add_subparsers(dest="command")

    courses = subparsers.add_parser("courses", help="List visible courses.")
    courses.add_argument("--state", default="active", help="Canvas enrollment_state filter.")
    courses.add_argument(
        "--headers-file",
        default=None,
        help="Use logged-in browser request headers copied from DevTools.",
    )
    courses.add_argument(
        "--refresh-session",
        action="store_true",
        help="Open/reuse a Playwright browser profile and update --headers-file before listing courses.",
    )
    courses.add_argument(
        "--profile-dir",
        default=".secrets/ntu_cool_browser_profile",
        help="Persistent browser profile for --refresh-session.",
    )
    courses.add_argument(
        "--headless-refresh",
        action="store_true",
        help="Refresh session without showing a browser window.",
    )

    announcements = subparsers.add_parser("announcements", help="Fetch course announcements.")
    announcements.add_argument("--course-id", required=True, help="Canvas course id.")
    announcements.add_argument("--out", default="materials", help="Output directory.")
    announcements.add_argument(
        "--api",
        choices=["topics", "announcements"],
        default="topics",
        help="Use discussion_topics or announcements endpoint.",
    )
    announcements.add_argument("--start-date", default=None, help="For --api announcements.")
    announcements.add_argument("--end-date", default=None, help="For --api announcements.")
    announcements.add_argument("--include-inactive", action="store_true", help="For --api announcements.")
    announcements.add_argument("--print", action="store_true", help="Print markdown to stdout.")
    announcements.add_argument(
        "--headers-file",
        default=None,
        help="Use logged-in browser request headers copied from DevTools.",
    )
    announcements.add_argument(
        "--refresh-session",
        action="store_true",
        help="Open/reuse a Playwright browser profile and update --headers-file before fetching.",
    )
    announcements.add_argument(
        "--profile-dir",
        default=".secrets/ntu_cool_browser_profile",
        help="Persistent browser profile for --refresh-session.",
    )
    announcements.add_argument(
        "--headless-refresh",
        action="store_true",
        help="Refresh session without showing a browser window.",
    )

    doctor = subparsers.add_parser(
        "doctor",
        help="Check that everything (Python, yt-dlp, node, ffmpeg, Chromium, cookies) is ready to go.",
    )
    doctor.add_argument("--headers-file", default=".secrets/ntu_cool_headers.txt")
    doctor.add_argument("--youtube-cookies", default=".secrets/youtube_cookies.txt")
    doctor.add_argument("--fix", action="store_true",
                        help="Try to auto-install missing pieces (pip, Chromium, winget/brew).")

    pick = subparsers.add_parser(
        "pick",
        help="Interactive: list your active courses, you pick one, it downloads everything.",
    )
    pick.add_argument("--out", default="ntu-cool-gcm_material",
                      help="Output directory (default: ntu-cool-gcm_material/, relative to current directory).")
    pick.add_argument("--headers-file", default=".secrets/ntu_cool_headers.txt")
    pick.add_argument("--refresh-session", action="store_true",
                      help="Open the browser to refresh login before listing.")
    pick.add_argument("--profile-dir", default=".secrets/ntu_cool_browser_profile")
    pick.add_argument("--youtube-cookies", default=".secrets/youtube_cookies.txt")
    pick.add_argument("--yt-dlp", default="yt-dlp")
    pick.add_argument("--state", default="active",
                      help="enrollment_state filter (default: active).")
    pick.add_argument("--skip-pdfs", action="store_true")
    pick.add_argument("--skip-pages", action="store_true")
    pick.add_argument("--skip-youtube", action="store_true")
    pick.add_argument("--skip-cool-videos", action="store_true")
    pick.add_argument(
        "--keep-terminal", action="store_true",
        help="Don't close the PowerShell window on quit (Windows only).",
    )

    course = subparsers.add_parser(
        "download-course",
        help="Download every PDF, Page, YouTube link and NTU CDN video for a course.",
    )
    course.add_argument("--course-id", required=True, help="Canvas course id (e.g. 60804).")
    course.add_argument("--out", default="ntu-cool-gcm_material",
                        help="Output directory (default: ntu-cool-gcm_material/, relative to current directory).")
    course.add_argument(
        "--headers-file",
        default=".secrets/ntu_cool_headers.txt",
        help="Logged-in browser request headers (Canvas session cookie).",
    )
    course.add_argument(
        "--refresh-session",
        action="store_true",
        help="Open the Playwright browser and refresh --headers-file before starting.",
    )
    course.add_argument(
        "--profile-dir",
        default=".secrets/ntu_cool_browser_profile",
        help="Persistent Playwright Chromium profile directory.",
    )
    course.add_argument(
        "--youtube-cookies",
        default=".secrets/youtube_cookies.txt",
        help="Netscape cookies.txt for yt-dlp.",
    )
    course.add_argument("--yt-dlp", default="yt-dlp", help="yt-dlp executable.")
    course.add_argument("--skip-pdfs", action="store_true")
    course.add_argument("--skip-pages", action="store_true")
    course.add_argument("--skip-youtube", action="store_true")
    course.add_argument("--skip-cool-videos", action="store_true")

    sync = subparsers.add_parser("sync", help="Download course files and module metadata.")
    sync.add_argument("--state", default="active", help="Canvas enrollment_state filter.")
    sync.add_argument("--all", action="store_true", help="Sync all courses returned by Canvas.")
    sync.add_argument(
        "--course-id",
        action="append",
        default=[],
        help="Canvas course id. Repeat or pass comma-separated ids.",
    )
    sync.add_argument("--name-contains", default=None, help="Sync courses whose names contain this text.")
    sync.add_argument("--out", default="materials", help="Output directory.")
    sync.add_argument("--dry-run", action="store_true", help="Show what would be downloaded.")
    sync.add_argument("--no-modules", action="store_true", help="Skip modules.json snapshots.")

    return parser


def _cmd_courses(client: CanvasClient, args: argparse.Namespace) -> int:
    courses = client.list_courses(enrollment_state=args.state)
    if not courses:
        print("No courses found.")
        return 0

    rows = [
        (
            str(course.get("id", "")),
            str(course.get("course_code") or ""),
            str(course.get("name") or ""),
        )
        for course in courses
    ]
    _print_table(("id", "code", "name"), rows)
    return 0


def _cmd_sync(client: CanvasClient, args: argparse.Namespace) -> int:
    course_ids = _split_course_ids(args.course_id)
    if not args.all and not args.name_contains and not course_ids:
        print("Choose what to sync: --all, --course-id, or --name-contains.")
        return 2

    available_courses = client.list_courses(enrollment_state=args.state)
    selected_courses = _select_courses(available_courses, all_courses=args.all, course_ids=course_ids, name_contains=args.name_contains)
    if not selected_courses:
        print("No matching courses found.")
        return 0

    output_dir = Path(args.out)
    store = ManifestStore(output_dir / ".ntu_cool_materials.sqlite3")
    try:
        stats = [
            sync_course_materials(
                client=client,
                course=course,
                output_dir=output_dir,
                store=store,
                include_modules=not args.no_modules,
                dry_run=args.dry_run,
            )
            for course in selected_courses
        ]
    finally:
        store.close()

    _print_sync_summary(stats, dry_run=args.dry_run)
    return 1 if any(item.failed for item in stats) else 0


def _windows_parent_pid_of(pid: int) -> int | None:
    """Walk Windows' process snapshot to find the parent PID of `pid`."""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    class PROCESSENTRY32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_void_p),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_char * 260),
        ]

    TH32CS_SNAPPROCESS = 0x00000002
    INVALID = ctypes.c_void_p(-1).value
    h = ctypes.windll.kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if h == INVALID or h == 0:
        return None
    try:
        pe = PROCESSENTRY32()
        pe.dwSize = ctypes.sizeof(PROCESSENTRY32)
        if ctypes.windll.kernel32.Process32First(h, ctypes.byref(pe)):
            while True:
                if pe.th32ProcessID == pid:
                    return int(pe.th32ParentProcessID)
                if not ctypes.windll.kernel32.Process32Next(h, ctypes.byref(pe)):
                    break
    finally:
        ctypes.windll.kernel32.CloseHandle(h)
    return None


def _close_parent_terminal_on_quit() -> None:
    """On Windows, close the surrounding terminal window when the user quits.

    Skipped when stdin isn't a tty (piped input / tests) so we never kill the
    caller's shell unintentionally.

    Implementation note: previous attempts spawned `cmd /c timeout && taskkill`
    detached, which briefly flashed two console windows. This version calls
    `TerminateProcess` directly via Win32 — no subprocess, no flashes, no cmd.
    The shell dies immediately; ConPTY / Windows Terminal notice and close
    the tab. Our own Python process dies along with the shell (we're its
    grandchild), but by this point we've already printed 'Bye.' and have
    nothing left to do.

    We target the GRANDPARENT (parent of os.getppid()), because pip's
    console-script wrapper on Windows is a `ntu-cool-gcm.exe` shim sitting
    between Python and the shell:
      powershell.exe  ←  what we kill
        └─ ntu-cool-gcm.exe   (= os.getppid())
            └─ python.exe     (= os.getpid())
    """
    if os.name != "nt":
        return
    if not sys.stdin.isatty():
        return
    try:
        wrapper_pid = os.getppid()
        shell_pid = _windows_parent_pid_of(wrapper_pid)
        target_pid = shell_pid or wrapper_pid
        if not target_pid:
            return
        import ctypes
        kernel32 = ctypes.windll.kernel32
        PROCESS_TERMINATE = 0x0001
        handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, int(target_pid))
        if handle:
            try:
                kernel32.TerminateProcess(handle, 0)
            finally:
                kernel32.CloseHandle(handle)
    except Exception:
        pass


def _maybe_set_up_youtube_cookies(cookies_path: Path) -> None:
    """If the user has no YouTube cookies, offer to set them up inline.
    Skips silently when stdin isn't a tty (piped input / tests)."""
    if cookies_path.exists():
        return
    if not sys.stdin.isatty():
        return
    print("\n尚未設定 YouTube cookies,下載不公開影片會需要。")
    print("(若你只下載 PDF 或 Page 可以跳過)")
    try:
        ans = input("現在設定嗎? 會跳出瀏覽器讓你登入 Google 帳號 [y/N]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return
    if ans not in {"y", "yes"}:
        return
    try:
        from .youtube_cookies import export_youtube_cookies
        result = export_youtube_cookies(cookies_path=cookies_path)
        print(f"  ✓ 已存入 {result.cookie_count} 個 cookies → {result.cookies_path}")
    except Exception as exc:
        print(f"  設定 YouTube cookies 失敗: {exc}")
        print("  (之後可以隨時執行 `youtube-cookies` 重試)")


def _cmd_pick(base_url: str, args: argparse.Namespace) -> int:
    """Interactive course picker → download_course.

    First-run hook: silently checks Python/Playwright/yt-dlp/Chromium and
    auto-installs whatever is missing so the user can run `ntu-cool-gcm`
    immediately after `pip install ntu-cool-material` without other steps.
    """
    headers_path = Path(args.headers_file)
    youtube_cookies = Path(args.youtube_cookies)
    if not ensure_ready(headers_path=headers_path, youtube_cookies_path=youtube_cookies):
        return 1
    if not args.skip_youtube:
        _maybe_set_up_youtube_cookies(youtube_cookies)
    browser: BrowserSession | None = None
    if args.refresh_session or not headers_path.exists():
        if not headers_path.exists() and not args.refresh_session:
            print(f"找不到登入憑證 ({headers_path}),開啟瀏覽器登入...")
        try:
            browser = open_browser_session(
                profile_dir=Path(args.profile_dir), headless=False, course_id=None,
            )
        except RuntimeError as exc:
            print(f"無法啟動瀏覽器: {exc}")
            return 1
        if not _dump_cookies_to_headers_file(browser.context, headers_path):
            browser.close()
            print("瀏覽器內沒有 NTU COOL 的 cookies。")
            return 1
        print(f"  已寫入登入憑證 → {headers_path}")

    try:
        client = CanvasSessionClient(base_url=base_url, headers=read_headers_file(headers_path))
    except (OSError, ValueError) as exc:
        if browser is not None:
            browser.close()
        print(f"無法讀取登入憑證: {exc}")
        return 1

    try:
        courses = client.list_courses(enrollment_state=args.state)
    except CanvasAPIError as exc:
        if browser is not None:
            browser.close()
        print(f"無法列出課程: {exc}")
        return 1

    if not courses:
        if browser is not None:
            browser.close()
        print("找不到課程。可以試試 --refresh-session 重新登入,或用 --state 改變過濾條件。")
        return 0

    def _run_download(course: dict[str, Any]) -> None:
        course_id = str(course["id"])
        print(f"\n→ {course.get('name')!r} (課程 ID {course_id})\n")
        try:
            download_course(
                course_id=course_id,
                output_dir=Path(args.out),
                base_url=base_url,
                headers_path=headers_path,
                refresh_session=False,
                client=client,
                browser=browser,
                yt_cookies=Path(args.youtube_cookies),
                yt_dlp=args.yt_dlp,
                profile_dir=Path(args.profile_dir),
                skip_pdfs=args.skip_pdfs,
                skip_pages=args.skip_pages,
                skip_youtube=args.skip_youtube,
                skip_cool_videos=args.skip_cool_videos,
            )
        except RuntimeError as exc:
            print(f"下載失敗: {exc}")
            # Don't bail on the loop — let the user try another course.

    n = len(courses)
    downloaded_in_session: set[str] = set()

    def _quit(_message: str = "") -> int:
        if not args.keep_terminal:
            _close_parent_terminal_on_quit()
        return 0

    def _course_label(c: dict[str, Any]) -> str:
        return str(c.get("name") or c.get("course_code") or c.get("id"))

    def _print_course_list() -> None:
        print(f"\n找到 {len(courses)} 門課程:\n")
        for i, c in enumerate(courses, 1):
            name = c.get("name") or c.get("course_code") or str(c.get("id"))
            code = c.get("course_code")
            suffix = f"  [{code}]" if code and code != name else ""
            mark = "  ✓" if str(c.get("id")) in downloaded_in_session else ""
            print(f"  {i}) {name}{suffix}{mark}")

    def _confirm_redownload(course: dict[str, Any]) -> bool:
        try:
            ans = input(
                f"⚠ 剛剛已經下載過「{_course_label(course)}」。\n"
                f"  再跑一次只會檢查是否有新檔案,要繼續嗎? [y/N]: "
            ).strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        return ans in {"y", "yes"}

    def _wrap_download(course: dict[str, Any], *, force: bool = False) -> None:
        if not force and str(course.get("id")) in downloaded_in_session:
            if not _confirm_redownload(course):
                return
        _run_download(course)
        downloaded_in_session.add(str(course.get("id")))

    class _UserQuit(Exception):
        """Raised when the user types 'q' from any prompt — propagates to the
        top-level handler which exits cleanly."""

    def _pick_historical_course() -> dict[str, Any] | None:
        """Two-step picker for past semesters: pick semester, then pick course.

        Returns the chosen course dict, or None when the user backs out at the
        TOP level of this picker (b/empty from the semester prompt). 'b' from
        the course-in-semester prompt goes back to the semester prompt only.
        Raises _UserQuit if 'q' is typed anywhere."""
        try:
            historical = client.list_courses(enrollment_state="completed")
        except CanvasAPIError as exc:
            print(f"無法取得歷史課程: {exc}")
            return None
        historical = [c for c in historical if c.get("name") or c.get("course_code")]
        if not historical:
            print("找不到任何已結束的課程。")
            return None

        groups: dict[str, list[dict[str, Any]]] = {}
        for c in historical:
            term = c.get("term") or {}
            term_name = str(term.get("name") or "(未指定學期)")
            groups.setdefault(term_name, []).append(c)
        # Most recent first; Canvas term names like "114-2 (2026 Spring)" sort
        # correctly under reverse-alpha.
        term_names = sorted(groups.keys(), reverse=True)

        while True:  # semester picker (re-entered if course-level user picks 'b')
            print(f"\n找到 {len(historical)} 門已結束課程,分布在 {len(term_names)} 個學期:\n")
            for i, name in enumerate(term_names, 1):
                print(f"  {i}) {name}  ({len(groups[name])} 門課)")
            try:
                raw = input(f"\n選擇學期 (1-{len(term_names)}, b = 返回, q = 離開)\n> ").strip()
            except (EOFError, KeyboardInterrupt):
                raise _UserQuit()
            cmd = raw.lower()
            if cmd in {"q", "quit", "exit"}:
                raise _UserQuit()
            if cmd in {"b", "back", ""}:
                return None
            try:
                idx = int(raw) - 1
                if idx < 0:
                    raise IndexError
                term_name = term_names[idx]
                term_courses = groups[term_name]
            except (ValueError, IndexError):
                print(f"無效輸入: {raw!r}。請輸入 1-{len(term_names)}、b、或 q。")
                continue

            while True:  # course-in-semester picker
                print(f"\n{term_name} 的課程:\n")
                for i, c in enumerate(term_courses, 1):
                    name = c.get("name") or c.get("course_code") or str(c.get("id"))
                    code = c.get("course_code")
                    suffix = f"  [{code}]" if code and code != name else ""
                    mark = "  ✓" if str(c.get("id")) in downloaded_in_session else ""
                    print(f"  {i}) {name}{suffix}{mark}")
                try:
                    raw = input(
                        f"\n選擇課程 (1-{len(term_courses)}, b = 返回, q = 離開)\n> "
                    ).strip()
                except (EOFError, KeyboardInterrupt):
                    raise _UserQuit()
                cmd = raw.lower()
                if cmd in {"q", "quit", "exit"}:
                    raise _UserQuit()
                if cmd in {"b", "back", ""}:
                    break  # back to semester picker (outer loop)
                try:
                    idx = int(raw) - 1
                    if idx < 0:
                        raise IndexError
                    return term_courses[idx]
                except (ValueError, IndexError):
                    print(f"無效輸入: {raw!r}。請輸入 1-{len(term_courses)}、b、或 q。")
                    continue

    def _ask_pick_number() -> list[dict[str, Any]] | None:
        """Show the course list and ask the user to pick. Returns:
          - [single course]    if user picked a number or chose one from history
          - [all courses]      if user picked 'a'
          - None               (currently unused — 'q' raises _UserQuit instead)
        Raises _UserQuit on 'q'."""
        _print_course_list()
        while True:
            try:
                raw = input(
                    f"\n選擇課程 (1-{n}, h = 歷史課程, a = 下載全部, q = 離開)\n> "
                ).strip()
            except (EOFError, KeyboardInterrupt):
                raise _UserQuit()
            cmd = raw.lower()
            if cmd in {"q", "quit", "exit", ""}:
                raise _UserQuit()
            if cmd in {"a", "all"}:
                return list(courses)
            if cmd in {"h", "history"}:
                chosen = _pick_historical_course()  # may raise _UserQuit
                if chosen is not None:
                    return [chosen]
                _print_course_list()
                continue
            try:
                idx = int(raw) - 1
                if idx < 0:
                    raise IndexError
                return [courses[idx]]
            except (ValueError, IndexError):
                print(f"無效輸入: {raw!r}。請輸入 1-{n}、h、a、或 q。")

    def _download_each(targets: list[dict[str, Any]]) -> None:
        """Download a single course (1-element list) or many in sequence."""
        if len(targets) > 1:
            print(f"\n→ 開始依序下載全部 {len(targets)} 門課程...")
            for i, course in enumerate(targets, 1):
                print(f"\n=========== [{i}/{len(targets)}] ===========")
                _wrap_download(course)
        else:
            _wrap_download(targets[0])

    try:
        try:
            _download_each(_ask_pick_number())
            # Subsequent iterations: small action menu, list shown only after 'c'/'h'.
            while True:
                try:
                    raw = input(
                        "\n下一個動作: c = 繼續下載別的 / a = 下載全部 / h = 歷史課程 / q = 離開\n> "
                    ).strip()
                except (EOFError, KeyboardInterrupt):
                    return _quit()
                cmd = raw.lower()
                if cmd in {"q", "quit", "exit", ""}:
                    return _quit()
                if cmd in {"a", "all"}:
                    _download_each(list(courses))
                    continue
                if cmd in {"c", "continue"}:
                    _download_each(_ask_pick_number())
                    continue
                if cmd in {"h", "history"}:
                    chosen = _pick_historical_course()
                    if chosen is not None:
                        _wrap_download(chosen)
                    continue
                print(f"無效輸入: {raw!r}。請輸入 c、a、h、或 q。")
        except _UserQuit:
            return _quit()
    finally:
        if browser is not None:
            browser.close()


def pick_main(argv: list[str] | None = None) -> int:
    """Console-script alias: behaves like `ntu-cool-materials pick`.

    When called from the `ntu-cool-gcm` entry point, argv is None and we forward
    sys.argv[1:] so flags like `--help` and `--refresh-session` pass through.
    """
    import sys
    forwarded = list(sys.argv[1:]) if argv is None else list(argv)
    return main(["pick", *forwarded])


def _cmd_download_course(base_url: str, args: argparse.Namespace) -> int:
    headers_path = Path(args.headers_file)
    if not args.refresh_session and not headers_path.exists():
        print(f"No headers file at {headers_path}.")
        print("Tip: pass --refresh-session to open the browser and create one.")
        return 2
    try:
        download_course(
            course_id=args.course_id,
            output_dir=Path(args.out),
            base_url=base_url,
            headers_path=headers_path,
            refresh_session=args.refresh_session,
            yt_cookies=Path(args.youtube_cookies),
            yt_dlp=args.yt_dlp,
            profile_dir=Path(args.profile_dir),
            skip_pdfs=args.skip_pdfs,
            skip_pages=args.skip_pages,
            skip_youtube=args.skip_youtube,
            skip_cool_videos=args.skip_cool_videos,
        )
        return 0
    except RuntimeError as exc:
        print(f"download-course aborted: {exc}")
        return 1


def _cmd_announcements(client: CanvasClient, args: argparse.Namespace) -> int:
    course = client.get_course(args.course_id)
    if args.api == "announcements":
        announcements = client.list_announcements(
            args.course_id,
            start_date=args.start_date,
            end_date=args.end_date,
            active_only=not args.include_inactive,
        )
    else:
        announcements = client.list_course_announcements(args.course_id)

    json_path, markdown_path = write_announcements(Path(args.out), course, announcements)
    print(f"Saved {len(announcements)} announcement(s).")
    print(f"JSON: {json_path}")
    print(f"Markdown: {markdown_path}")

    if args.print:
        print()
        print("\n\n".join(announcement_markdown(item) for item in announcements))

    return 0


def _require_token(args: argparse.Namespace) -> str | None:
    token = args.token or os.environ.get(args.token_env)
    if not token:
        print(f"Missing token. Set {args.token_env} or pass --token.")
        return None
    return token


def _announcements_client(base_url: str, args: argparse.Namespace) -> CanvasClient | CanvasSessionClient | None:
    if args.refresh_session and not args.headers_file:
        args.headers_file = ".secrets/ntu_cool_headers.txt"

    if args.headers_file:
        try:
            headers = read_headers_file(Path(args.headers_file))
        except (OSError, ValueError) as exc:
            print(f"Could not read headers file: {exc}")
            return None
        return CanvasSessionClient(base_url=base_url, headers=headers)

    token = _require_token(args)
    if not token:
        print("Or pass --headers-file with logged-in browser request headers.")
        return None
    return CanvasClient(base_url=base_url, token=token)


def _courses_client(base_url: str, args: argparse.Namespace) -> CanvasClient | CanvasSessionClient | None:
    if args.refresh_session and not args.headers_file:
        args.headers_file = ".secrets/ntu_cool_headers.txt"

    if args.refresh_session:
        try:
            refresh_headers_file(
                course_id=None,
                base_url=base_url,
                profile_dir=Path(args.profile_dir),
                headers_path=Path(args.headers_file),
                headless=args.headless_refresh,
            )
        except Exception as exc:
            print(f"Could not refresh browser session: {exc}")
            return None

    if args.headers_file:
        try:
            headers = read_headers_file(Path(args.headers_file))
        except (OSError, ValueError) as exc:
            print(f"Could not read headers file: {exc}")
            return None
        return CanvasSessionClient(base_url=base_url, headers=headers)

    token = _require_token(args)
    if not token:
        print("Or pass --headers-file with logged-in browser request headers.")
        return None
    return CanvasClient(base_url=base_url, token=token)


def _split_course_ids(values: list[str]) -> set[str]:
    course_ids: set[str] = set()
    for value in values:
        for item in value.split(","):
            item = item.strip()
            if item:
                course_ids.add(item)
    return course_ids


def _select_courses(
    courses: list[dict[str, Any]],
    *,
    all_courses: bool,
    course_ids: set[str],
    name_contains: str | None,
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    name_filter = name_contains.casefold() if name_contains else None

    for course in courses:
        course_id = str(course.get("id") or "")
        course_name = str(course.get("name") or course.get("course_code") or "")
        matches_id = course_id in course_ids
        matches_name = bool(name_filter and name_filter in course_name.casefold())
        if all_courses or matches_id or matches_name:
            selected.append(course)
            seen_ids.add(course_id)

    for course_id in sorted(course_ids - seen_ids):
        selected.append({"id": course_id, "name": course_id})

    return selected


def _print_table(headers: tuple[str, ...], rows: list[tuple[str, ...]]) -> None:
    widths = [
        max(len(headers[index]), *(len(row[index]) for row in rows))
        for index in range(len(headers))
    ]
    print("  ".join(header.ljust(widths[index]) for index, header in enumerate(headers)))
    print("  ".join("-" * width for width in widths))
    for row in rows:
        print("  ".join(value.ljust(widths[index]) for index, value in enumerate(row)))


def _print_sync_summary(stats: list[SyncStats], *, dry_run: bool) -> None:
    mode = "would download" if dry_run else "downloaded"
    rows = [
        (
            item.course_id,
            item.course_name,
            str(item.downloaded),
            str(item.unchanged),
            str(item.skipped),
            str(item.failed),
        )
        for item in stats
    ]
    _print_table(("id", "course", mode, "unchanged", "skipped", "failed"), rows)
