from __future__ import annotations

import argparse
import os
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
    browser: BrowserSession | None = None
    if args.refresh_session or not headers_path.exists():
        if not headers_path.exists() and not args.refresh_session:
            print(f"No headers file at {headers_path}. Opening browser to log in...")
        try:
            browser = open_browser_session(
                profile_dir=Path(args.profile_dir), headless=False, course_id=None,
            )
        except RuntimeError as exc:
            print(f"Could not start browser session: {exc}")
            return 1
        if not _dump_cookies_to_headers_file(browser.context, headers_path):
            browser.close()
            print("No NTU COOL cookies found in browser context.")
            return 1
        print(f"  refreshed -> {headers_path}")

    try:
        client = CanvasSessionClient(base_url=base_url, headers=read_headers_file(headers_path))
    except (OSError, ValueError) as exc:
        if browser is not None:
            browser.close()
        print(f"Could not read headers file: {exc}")
        return 1

    try:
        courses = client.list_courses(enrollment_state=args.state)
    except CanvasAPIError as exc:
        if browser is not None:
            browser.close()
        print(f"Could not list courses: {exc}")
        return 1

    if not courses:
        if browser is not None:
            browser.close()
        print("No courses returned. Try --refresh-session, or --state to widen the filter.")
        return 0

    def _print_course_list() -> None:
        print(f"\nFound {len(courses)} course(s):\n")
        for i, c in enumerate(courses, 1):
            name = c.get("name") or c.get("course_code") or str(c.get("id"))
            code = c.get("course_code")
            suffix = f"  [{code}]" if code and code != name else ""
            print(f"  {i}) {name}{suffix}")

    def _run_download(course: dict[str, Any]) -> None:
        course_id = str(course["id"])
        print(f"\n→ {course.get('name')!r} (id {course_id})\n")
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
            print(f"download-course failed: {exc}")
            # Don't bail on the loop — let the user try another course.

    _print_course_list()

    n = len(courses)
    options_for_first = (
        f"輸入課程編號 1-{n} 開始下載 / a = 下載全部 / q = 離開"
    )
    options_for_next = (
        f"下一個動作: 1-{n} 下載另一門 / a = 下載全部 / l = 重新列出 / q = 離開"
    )

    try:
        first_iteration = True
        while True:
            prompt = f"\n{options_for_first if first_iteration else options_for_next}\n> "
            try:
                raw = input(prompt).strip()
            except (EOFError, KeyboardInterrupt):
                print("\naborted.")
                return 0
            if raw.lower() in {"q", "quit", "exit", ""}:
                print("Bye.")
                return 0
            if raw.lower() in {"l", "list", "ls"}:
                _print_course_list()
                continue
            if raw.lower() in {"a", "all"}:
                print(f"\n→ Downloading all {n} courses sequentially...")
                for i, course in enumerate(courses, 1):
                    print(f"\n=========== [{i}/{n}] ===========")
                    _run_download(course)
                first_iteration = False
                continue
            try:
                idx = int(raw) - 1
                if idx < 0:
                    raise IndexError
                chosen = courses[idx]
            except (ValueError, IndexError):
                print(f"Invalid choice: {raw!r}. Enter 1-{n}, 'a', 'l', or 'q'.")
                continue
            _run_download(chosen)
            first_iteration = False
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
