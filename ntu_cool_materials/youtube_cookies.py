from __future__ import annotations

import argparse
import time
from dataclasses import dataclass
from pathlib import Path


DEFAULT_PROFILE_DIR = ".secrets/youtube_browser_profile"
DEFAULT_COOKIES_FILE = ".secrets/youtube_cookies.txt"
YOUTUBE_URL = "https://www.youtube.com/"


def _secrets_base() -> Path:
    """Writable base for the cookies/profile defaults — mirrors
    cli._secrets_dir so the standalone `youtube-cookies` command also works
    from a non-writable CWD (e.g. C:\\WINDOWS\\system32). Uses ./.secrets when
    it already exists, else ~/.ntu-cool-gcm/.secrets."""
    legacy = Path(".secrets")
    try:
        if legacy.is_dir():
            return legacy
    except OSError:
        pass
    return Path.home() / ".ntu-cool-gcm" / ".secrets"


class YouTubeCookieError(RuntimeError):
    pass


@dataclass(frozen=True)
class YouTubeCookieResult:
    cookies_path: Path
    cookie_count: int
    current_url: str


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        result = export_youtube_cookies(
            profile_dir=Path(args.profile_dir),
            cookies_path=Path(args.cookies_file),
            headless=args.headless,
            browser_channel=args.browser_channel,
            timeout_ms=args.timeout_ms,
            wait_for_login=not args.no_wait_for_login,
        )
    except YouTubeCookieError as exc:
        print(f"error: {exc}")
        return 1

    print(f"cookies: {result.cookies_path}")
    print(f"count: {result.cookie_count}")
    print(f"url: {result.current_url}")
    return 0


def export_youtube_cookies(
    *,
    profile_dir: Path = Path(DEFAULT_PROFILE_DIR),
    cookies_path: Path = Path(DEFAULT_COOKIES_FILE),
    headless: bool = False,
    browser_channel: str | None = None,
    timeout_ms: int = 180_000,
    wait_for_login: bool = True,
) -> YouTubeCookieResult:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise YouTubeCookieError(
            "Playwright is not installed. Run: pip install playwright && python -m playwright install chromium"
        ) from exc

    profile_dir.parent.mkdir(parents=True, exist_ok=True)
    cookies_path.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        launch_kwargs: dict[str, object] = {
            "user_data_dir": str(profile_dir),
            "headless": headless,
            "accept_downloads": False,
        }
        if browser_channel and browser_channel != "chromium":
            launch_kwargs["channel"] = browser_channel

        context = playwright.chromium.launch_persistent_context(**launch_kwargs)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(YOUTUBE_URL, wait_until="domcontentloaded", timeout=timeout_ms)
            if not headless and wait_for_login:
                print("請在開啟的瀏覽器登入 YouTube / Google。")
                print("偵測到登入 cookie 後會自動匯出。")
                _wait_for_login_cookies(context, timeout_ms=timeout_ms)
            elif not headless:
                print("請在開啟的瀏覽器登入 YouTube / Google。")
                print("稍待片刻就會自動匯出 cookies。")
                page.wait_for_timeout(20_000)

            cookies = context.cookies(
                [
                    "https://www.youtube.com/",
                    "https://youtube.com/",
                    "https://accounts.google.com/",
                    "https://google.com/",
                ]
            )
            relevant = [cookie for cookie in cookies if _is_relevant_cookie_domain(str(cookie.get("domain") or ""))]
            if not relevant:
                raise YouTubeCookieError("No YouTube/Google cookies found in the dedicated browser profile.")

            cookies_path.write_text(_netscape_cookie_text(relevant), encoding="utf-8")
            return YouTubeCookieResult(
                cookies_path=cookies_path,
                cookie_count=len(relevant),
                current_url=page.url,
            )
        finally:
            context.close()


def _is_relevant_cookie_domain(domain: str) -> bool:
    normalized = domain.lstrip(".").lower()
    return normalized in {
        "youtube.com",
        "www.youtube.com",
        "google.com",
        "accounts.google.com",
    } or normalized.endswith(".youtube.com") or normalized.endswith(".google.com")


def _has_login_cookie(cookies: list[dict[str, object]]) -> bool:
    names = {str(cookie.get("name") or "") for cookie in cookies}
    return bool(
        names
        & {
            "SID",
            "HSID",
            "SSID",
            "APISID",
            "SAPISID",
            "LOGIN_INFO",
            "__Secure-1PSID",
            "__Secure-3PSID",
            "__Secure-1PSIDTS",
            "__Secure-3PSIDTS",
        }
    )


def _wait_for_login_cookies(context, *, timeout_ms: int) -> None:  # type: ignore[no-untyped-def]
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        cookies = context.cookies(
            [
                "https://www.youtube.com/",
                "https://youtube.com/",
                "https://accounts.google.com/",
                "https://google.com/",
            ]
        )
        if _has_login_cookie(cookies):
            return
        time.sleep(2)
    raise YouTubeCookieError("Timed out waiting for YouTube/Google login cookies.")


def _netscape_cookie_text(cookies: list[dict[str, object]]) -> str:
    lines = [
        "# Netscape HTTP Cookie File",
        "# This file is generated by ntu-cool-materials for local yt-dlp use.",
        "# Keep it private. It may grant access to your YouTube account session.",
    ]
    for cookie in sorted(cookies, key=lambda item: (str(item.get("domain")), str(item.get("name")))):
        domain = str(cookie.get("domain") or "")
        include_subdomains = "TRUE" if domain.startswith(".") else "FALSE"
        path = str(cookie.get("path") or "/")
        secure = "TRUE" if bool(cookie.get("secure")) else "FALSE"
        expires_raw = cookie.get("expires")
        try:
            expires = str(max(0, int(float(expires_raw)))) if expires_raw is not None else "0"
        except (TypeError, ValueError):
            expires = "0"
        name = str(cookie.get("name") or "")
        value = str(cookie.get("value") or "")
        if name:
            lines.append("\t".join([domain, include_subdomains, path, secure, expires, name, value]))
    return "\n".join(lines) + "\n"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="youtube-cookies",
        description="Open a dedicated Playwright browser profile and export YouTube cookies for yt-dlp.",
    )
    base = _secrets_base()
    parser.add_argument("--profile-dir", default=str(base / "youtube_browser_profile"),
                        help="Dedicated browser profile directory.")
    parser.add_argument("--cookies-file", default=str(base / "youtube_cookies.txt"),
                        help="Netscape cookies.txt output path.")
    parser.add_argument("--headless", action="store_true", help="Export without showing the browser window.")
    parser.add_argument(
        "--browser-channel",
        choices=["chromium", "chrome", "msedge"],
        default="chromium",
        help="Browser channel to launch. Use chrome/msedge if Google blocks Playwright Chromium login.",
    )
    parser.add_argument(
        "--no-wait-for-login",
        action="store_true",
        help="Export after a short wait even if login cookies are not detected.",
    )
    parser.add_argument("--timeout-ms", type=int, default=180_000, help="Browser navigation timeout.")
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
