from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path


DEFAULT_PROFILE_DIR = ".secrets/ntu_cool_browser_profile"
DEFAULT_HEADERS_FILE = ".secrets/ntu_cool_headers.txt"
DEFAULT_BASE_URL = "https://cool.ntu.edu.tw"


class BrowserSessionError(RuntimeError):
    pass


@dataclass(frozen=True)
class BrowserSessionResult:
    headers_path: Path
    logged_in: bool
    current_url: str


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        result = refresh_headers_file(
            course_id=args.course_id,
            base_url=args.base_url,
            profile_dir=Path(args.profile_dir),
            headers_path=Path(args.headers_file),
            headless=args.headless,
            timeout_ms=args.timeout_ms,
        )
    except BrowserSessionError as exc:
        print(f"error: {exc}")
        return 1

    status = "logged in" if result.logged_in else "login required"
    print(f"status: {status}")
    print(f"url: {result.current_url}")
    print(f"headers: {result.headers_path}")
    return 0 if result.logged_in else 2


def refresh_headers_file(
    *,
    course_id: str | None,
    base_url: str = DEFAULT_BASE_URL,
    profile_dir: Path = Path(DEFAULT_PROFILE_DIR),
    headers_path: Path = Path(DEFAULT_HEADERS_FILE),
    headless: bool = False,
    timeout_ms: int = 120_000,
) -> BrowserSessionResult:
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise BrowserSessionError(
            "Playwright is not installed. Run: pip install playwright && python -m playwright install chromium"
        ) from exc

    base_url = base_url.rstrip("/")
    if course_id:
        target_url = f"{base_url}/courses/{course_id}/announcements"
        api_url = (
            f"{base_url}/api/v1/courses/{course_id}/discussion_topics"
            "?per_page=100&only_announcements=true&order_by=recent_activity"
        )
    else:
        target_url = f"{base_url}/courses"
        api_url = f"{base_url}/api/v1/courses?per_page=100&include[]=term&enrollment_state=active"

    profile_dir.parent.mkdir(parents=True, exist_ok=True)
    headers_path.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=headless,
            accept_downloads=False,
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(target_url, wait_until="domcontentloaded", timeout=timeout_ms)

            if _looks_like_login(page.url):
                if headless:
                    return BrowserSessionResult(headers_path=headers_path, logged_in=False, current_url=page.url)

                print("Please finish NTU COOL login in the opened browser window.")
                try:
                    page.wait_for_url(lambda url: not _looks_like_login(url), timeout=timeout_ms)
                except PlaywrightTimeoutError as exc:
                    raise BrowserSessionError("Timed out waiting for login to finish.") from exc

            response = page.request.get(api_url, headers={"Accept": "application/json, text/plain, */*"})
            if response.status in {401, 403}:
                return BrowserSessionResult(headers_path=headers_path, logged_in=False, current_url=page.url)
            if response.status >= 400:
                raise BrowserSessionError(f"Announcement API returned HTTP {response.status}.")

            cookie_header = _cookie_header(context.cookies(base_url))
            if not cookie_header:
                raise BrowserSessionError("No NTU COOL cookies found in browser context.")

            headers_path.write_text(
                "\n".join(
                    [
                        "accept: application/json, text/plain, */*",
                        f"cookie: {cookie_header}",
                        "referer: https://cool.ntu.edu.tw/",
                        "user-agent: ntu-cool-materials/0.1",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            return BrowserSessionResult(headers_path=headers_path, logged_in=True, current_url=page.url)
        finally:
            context.close()


def _cookie_header(cookies: list[dict[str, object]]) -> str:
    pairs = []
    for cookie in cookies:
        name = str(cookie.get("name") or "")
        value = str(cookie.get("value") or "")
        if name and value:
            pairs.append(f"{name}={value}")
    return "; ".join(pairs)


def _looks_like_login(url: str) -> bool:
    lowered = url.lower()
    return "/login" in lowered or "oauth2" in lowered or "saml" in lowered


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ntu-cool-session",
        description="Refresh a local NTU COOL browser session and export headers for API calls.",
    )
    parser.add_argument("--course-id", default=None, help="Canvas course id to open while refreshing session.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="NTU COOL base URL.")
    parser.add_argument("--profile-dir", default=DEFAULT_PROFILE_DIR, help="Persistent browser profile directory.")
    parser.add_argument("--headers-file", default=DEFAULT_HEADERS_FILE, help="Headers output path.")
    parser.add_argument("--headless", action="store_true", help="Do not show the browser window.")
    parser.add_argument("--timeout-ms", type=int, default=120_000, help="Login wait timeout.")
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
