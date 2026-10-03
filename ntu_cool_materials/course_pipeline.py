"""Bulk per-course download pipeline.

Public API used by `cli.download-course`:
    plan_course(client, course_id, output_dir) -> CoursePlan
    download_files(plan, client) -> StageStats
    save_pages(plan, client, course_id) -> StageStats
    download_youtube(plan, *, cookies_path, yt_dlp) -> StageStats
    capture_and_download_cool_videos(plan, *, course_id, profile_dir, headless, headers_path) -> StageStats

Completed artifacts are identified by source ID and verified against a persistent manifest.

Why a single `capture_and_download_cool_videos`: NTU SAML session cookies on
cool.ntu.edu.tw are session-only (die when the Chromium process exits), so the
LTI launches must happen inside a Playwright context that carries that login.
The function opens one invisible persistent context, re-injects the cookies
saved at login (`ntu_cool_storage_state.json` next to the headers file), and
only falls back to a visible SSO window if that saved login has expired. It
then walks every cool-video module item and downloads the transcoded.mp4 from
the captured /api/.../view JSON's altSourceUri.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .announcements import (
    ANNOUNCEMENTS_JSON, canvas_file_links, html_to_markdown, migrate_legacy_announcements_json, write_announcements,
)
from .canvas_client import CanvasAPIError, SessionExpiredError
from .i18n import t
from .media_naming import build_video_title_map, extract_youtube_ids, sanitize_teacher_title
from .session_client import DROP_REQUEST_HEADER_NAMES, CanvasSessionClient
from .spreadsheet_convert import convert_spreadsheets
from .storage import ManifestStore, atomic_write_text, course_directory_name
from .http_io import DownloadError, download as download_http, origin
from . import console


CANVAS_NETLOC = "cool.ntu.edu.tw"
COOL_VIDEO_VIEW_RE = re.compile(r"/api/courses/(\d+)/videos/(\d+)/view$")
LOGIN_RE = re.compile(r"/login|oauth2|saml", re.IGNORECASE)
TITLE_PREFIX_RE = re.compile(r"^\s*([\w\-]+)")

YT_DLP_BASE_ARGS = [
    "--js-runtimes", "node",
    "--extractor-args", "youtube:player_client=default,web,web_safari",
    "--no-playlist",
    "--ignore-errors",
    "--restrict-filenames",
    "--retries", "10",
    "--fragment-retries", "10",
    "--merge-output-format", "mp4",
    "-f", "bv*[ext=mp4]+ba[ext=m4a]/bv*+ba/b",
]


@dataclass
class WeekPlan:
    label: str               # e.g. "week3"
    module: dict[str, Any]   # raw module from list_modules
    week_dir: Path           # <course_dir>/<label>

    @property
    def items(self) -> list[dict[str, Any]]:
        return self.module.get("items") or []


@dataclass
class CoursePlan:
    course: dict[str, Any]
    course_id: str
    course_dir: Path
    weeks: list[WeekPlan] = field(default_factory=list)
    stats: CourseStats | None = None
    verify_files: bool = False
    workers: int = 3
    file_metadata: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class StageStats:
    done: int = 0
    skipped: int = 0
    failed: list[str] = field(default_factory=list)  # human labels of failed items
    disabled: bool = False


@dataclass
class CourseStats:
    announcements: StageStats = field(default_factory=StageStats)
    pdfs: StageStats = field(default_factory=StageStats)
    pages: StageStats = field(default_factory=StageStats)
    youtube: StageStats = field(default_factory=StageStats)
    cool_videos: StageStats = field(default_factory=StageStats)

    @property
    def successful(self) -> bool:
        return not any(stage.failed for stage in (self.announcements, self.pdfs, self.pages, self.youtube, self.cool_videos))


def save_announcements(plan: CoursePlan, client: CanvasSessionClient) -> StageStats:
    """Refresh all visible announcements, including edits to previously saved posts."""
    stats = StageStats()
    try:
        announcements = client.list_course_announcements(plan.course_id)
        target_dir = plan.course_dir / "announcements"
        migrate_legacy_announcements_json(target_dir)
        json_path = target_dir / ANNOUNCEMENTS_JSON
        markdown_path = target_dir / "announcements.md"

        existing_posts = {}
        has_existing_files = json_path.is_file() and markdown_path.is_file()
        if has_existing_files:
            try:
                data = json.loads(json_path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    for idx, item in enumerate(data):
                        if isinstance(item, dict):
                            key = str(item.get("id")) if item.get("id") is not None else f"idx_{idx}"
                            existing_posts[key] = item
            except Exception:
                existing_posts = {}
                has_existing_files = False

        if not has_existing_files:
            write_announcements(plan.course_dir.parent, plan.course, announcements)
            stats.done = len(announcements)
            stats.skipped = 0
        else:
            new_or_updated = 0
            unchanged = 0
            for idx, post in enumerate(announcements):
                key = str(post.get("id")) if post.get("id") is not None else f"idx_{idx}"
                if key not in existing_posts:
                    new_or_updated += 1
                else:
                    prev = existing_posts[key]
                    prev_up = prev.get("updated_at")
                    curr_up = post.get("updated_at")
                    if prev_up and curr_up and prev_up != curr_up:
                        new_or_updated += 1
                    elif (prev.get("title") != post.get("title") or
                          prev.get("message") != post.get("message")):
                        new_or_updated += 1
                    else:
                        unchanged += 1

            if new_or_updated > 0 or len(existing_posts) != len(announcements):
                write_announcements(plan.course_dir.parent, plan.course, announcements)
                stats.done = new_or_updated
                stats.skipped = unchanged
            else:
                stats.done = 0
                stats.skipped = unchanged
        for post in announcements:
            links = canvas_file_links(post.get("message"), getattr(client, "base_url", f"https://{CANVAS_NETLOC}"))
            for attachment in post.get("attachments") or []:
                if isinstance(attachment, dict) and attachment.get("id"):
                    links[str(attachment["id"])] = attachment.get("display_name") or attachment.get("filename") or "attachment"
            linked = _download_linked_files(plan, client, plan.course_dir / "announcements" / "attachments", links)
            stats.failed.extend(linked.failed)
    except SessionExpiredError:
        raise
    except (CanvasAPIError, DownloadError, OSError, ValueError) as exc:
        stats.failed.append(f"announcements: {exc}")
    return stats


# ---- planning ----

def plan_course(client: CanvasSessionClient, course_id: str, output_dir: Path) -> CoursePlan:
    """Fetch complete module items and reserve stable per-module directories."""
    course = client.get_course(course_id)
    modules = list(client.list_paginated(
        f"/api/v1/courses/{urllib.parse.quote(str(course_id), safe='')}/modules",
        params=[("per_page", "100"), ("include[]", "items"), ("include[]", "content_details")],
    ))

    course_dir = (output_dir / course_directory_name(course)).resolve()
    course_dir.mkdir(parents=True, exist_ok=True)
    plan = CoursePlan(course=course, course_id=str(course_id), course_dir=course_dir)
    relevant_types = {"File", "Page", "ExternalUrl", "ExternalTool"}
    for index, module in enumerate(modules, start=1):
        items = module.get("items")
        if items is None or len(items) < int(module.get("items_count") or 0):
            mid = urllib.parse.quote(str(module["id"]), safe="")
            cid = urllib.parse.quote(str(course_id), safe="")
            items = list(client.list_paginated(
                f"/api/v1/courses/{cid}/modules/{mid}/items",
                params=[("per_page", "100"), ("include[]", "content_details")],
            ))
            module["items"] = items
        if not any(i.get("type") in relevant_types for i in items):
            continue
        label = _module_label(module, index)
        with _manifest(plan) as store:
            week_dir = store.artifact_path(f"module:{module.get('id', index)}", course_dir, label)
        label = week_dir.name
        week_dir.mkdir(parents=True, exist_ok=True)
        # Migrate any leftover legacy subfolders (files/ pages/ videos/ metadata/).
        _migrate_legacy_subfolders(week_dir)
        plan.weeks.append(WeekPlan(label=label, module=module, week_dir=week_dir))
    return plan


def _migrate_legacy_subfolders(week_dir: Path) -> None:
    """One-shot: pull any files out of legacy files/ pages/ videos/ subfolders into week_dir
    and remove the legacy metadata/ subfolder (no longer used).

    Conservative on collisions: won't overwrite existing destinations.
    """
    import shutil
    for subname in ("files", "pages", "videos"):
        sub = week_dir / subname
        if not sub.is_dir():
            continue
        for src in list(sub.iterdir()):
            if not src.is_file():
                continue
            dst = week_dir / src.name
            if dst.exists():
                # Don't clobber. Leave the source where it is so user can resolve manually.
                print(f"    [migrate] skipping {src.relative_to(week_dir.parent.parent)}: target {dst.name} already exists")
                continue
            shutil.move(str(src), str(dst))
        try:
            sub.rmdir()
        except OSError:
            pass  # not empty (some files left due to collisions)
    # Keep legacy metadata: it may be the only surviving source-to-file mapping.


def _module_label(module: dict[str, Any], index: int) -> str:
    """Pick a directory label like 'week3' from the module name."""
    name = str(module.get("name") or "")
    m = re.search(r"week\s*(\d+)", name, re.IGNORECASE)
    if m:
        return f"week{int(m.group(1))}"
    return f"module{index}"


# ---- HTTP helpers (session-cookie auth, cross-origin safe) ----

def _session_headers(client: CanvasSessionClient) -> dict[str, str]:
    h = {n: v for n, v in client.headers.items() if n.lower() not in DROP_REQUEST_HEADER_NAMES}
    h["Accept"] = "application/json, text/plain, */*"
    h.setdefault("User-Agent", "ntu-cool-materials/0.1")
    return h


def _progress(label: str):
    last = [0.0]
    def update(received, total):
        now = time.monotonic()
        if sys.stdout.isatty() and (now - last[0] >= 0.5 or received == total):
            last[0] = now
            size = f" / {total / 1048576:.1f} MB" if total is not None else " MB"
            print(f"\r  {label[:60]}: {received / 1048576:.1f}{size}    ", end="", flush=True)
    return update


def _download_canvas_file(file_id: str, target: Path, headers: dict[str, str], *,
                          base_url=f"https://{CANVAS_NETLOC}", expected_size=None) -> None:
    print(f"  [download] {target.name}")
    url = f"{base_url.rstrip('/')}/files/{urllib.parse.quote(file_id, safe='')}/download?download_frd=1"
    def scoped(current):
        return headers if origin(current) == origin(base_url) else {"User-Agent": "ntu-cool-materials/0.1"}
    download_http(url, target, scoped, identity=f"{base_url}/file/{file_id}", expected_size=expected_size)


def _download_signed_url(url: str, target: Path) -> None:
    try:
        download_http(url, target, lambda _: {"User-Agent": "ntu-cool-materials/0.1"},
                      validate=_valid_video, progress=_progress(target.name))
    finally:
        if sys.stdout.isatty():
            print()


def _manifest(plan: CoursePlan) -> ManifestStore:
    return ManifestStore(plan.course_dir / ".ntu_cool_materials.sqlite3")


def _artifact_key(week: WeekPlan, kind: str, source_id) -> str:
    return f"{week.module.get('id', week.label)}:{kind}:{source_id}"


def _version(info: dict[str, Any]) -> str:
    return json.dumps([info.get("updated_at"), info.get("modified_at"), info.get("size")])


def _adopt_legacy(store: ManifestStore, key: str, target: Path, version, valid) -> bool:
    """Record a file left by a pre-manifest release instead of downloading it again."""
    row = store.artifact(key)
    if row is None or row["size"] is not None or not target.is_file() or not valid(target):
        return False
    store.record_artifact(key, target, version)
    return True


def _download_linked_files(plan, client, directory, links):
    if not links:
        return StageStats()
    items = [{"id": fid, "content_id": fid, "type": "File", "title": title, "_attachment": True}
             for fid, title in links.items()]
    week = WeekPlan(str(directory.resolve().relative_to(plan.course_dir.resolve())), {"items": items}, directory)
    linked_plan = CoursePlan(plan.course, plan.course_id, plan.course_dir, [week],
                             verify_files=plan.verify_files, workers=plan.workers, file_metadata=plan.file_metadata)
    return download_files(linked_plan, client, all_file_types=True)


# ---- per-stage workers ----

_KNOWN_FILE_EXTS = {
    ".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx", ".csv",
    ".zip", ".rar", ".7z", ".tar", ".gz",
    ".txt", ".md", ".rtf",
    ".jpg", ".jpeg", ".png", ".gif", ".bmp",
    ".mp3", ".wav", ".m4a",
    ".mp4", ".mov", ".avi", ".mkv",
}

def _file_item_real_ext(item: dict[str, Any]) -> str:
    """Best-effort original extension for a Canvas File module item."""
    title = str(item.get("title") or "").strip()
    content_details = item.get("content_details") or {}
    display_name = str(content_details.get("display_name") or content_details.get("filename") or "")
    real_ext = Path(display_name).suffix.lower() if display_name else ""
    if not real_ext:
        real_ext = Path(title).suffix.lower()
    return real_ext


def _file_item_target_name(item: dict[str, Any], *, all_file_types: bool) -> str:
    """Preserve real file extensions. all_file_types remains a compatible CLI flag."""
    title = str(item.get("title") or "").strip() or f"item-{item.get('id')}"
    real_ext = _file_item_real_ext(item)
    use_ext = real_ext or ".pdf"
    title_ext = Path(title).suffix.lower()
    stem = title[:-len(title_ext)] if title_ext and (title_ext == real_ext or title_ext in _KNOWN_FILE_EXTS) else title
    safe_title = sanitize_teacher_title(stem)
    return f"{safe_title}{use_ext}"


def download_files(
    plan: CoursePlan, client: CanvasSessionClient, *, all_file_types: bool = False,
) -> StageStats:
    """Refresh metadata, reserve unique paths, and transfer missing/changed files."""
    headers = _session_headers(client)
    stats = StageStats()
    jobs = []
    base_url = getattr(client, "base_url", f"https://{CANVAS_NETLOC}")
    if not isinstance(base_url, str):
        base_url = f"https://{CANVAS_NETLOC}"
    with _manifest(plan) as store:
        for week in plan.weeks:
            for item in week.items:
                if item.get("type") != "File":
                    continue
                fid = str(item.get("content_id") or "")
                try:
                    if not fid:
                        raise ValueError("File item has no content_id")
                    info = plan.file_metadata.get(fid)
                    if info is None:
                        getter = getattr(client, "get_json", None)
                        info = getter(f"/api/v1/files/{urllib.parse.quote(fid, safe='')}") if getter else (item.get("content_details") or {})
                        if not isinstance(info, dict):
                            raise ValueError("Invalid file metadata")
                        plan.file_metadata[fid] = info
                    item["content_details"] = {**(item.get("content_details") or {}), **info}
                    if item.get("_attachment"):
                        item["title"] = info.get("display_name") or info.get("filename") or item["title"]
                    key = _artifact_key(week, "file", fid)
                    target = store.artifact_path(key, week.week_dir, _file_item_target_name(item, all_file_types=all_file_types))
                    item["_local_path"] = str(target)
                    version = _version(info)
                    size = int(info["size"]) if info.get("size") is not None else None
                    if (store.artifact_current(key, target, version, verify_hash=plan.verify_files)
                            or _adopt_legacy(store, key, target, version,
                                             lambda p: p.stat().st_size == size if size is not None else p.stat().st_size > 0)):
                        stats.skipped += 1
                        continue
                    jobs.append((key, fid, target, version, size))
                except SessionExpiredError:
                    raise
                except (CanvasAPIError, DownloadError, OSError, ValueError) as exc:
                    stats.failed.append(f"{week.label}/{item.get('title')}: {exc}")
        # Reserve names and access SQLite only on the calling thread.
        jobs = list({job[0]: job for job in jobs}.values())
        with ThreadPoolExecutor(max_workers=max(1, min(plan.workers, 4))) as pool:
            pending = {pool.submit(_download_canvas_file, fid, target, headers,
                                   base_url=base_url, expected_size=size): (key, target, version)
                       for key, fid, target, version, size in jobs}
            expired = None
            for future in as_completed(pending):
                key, target, version = pending[future]
                try:
                    future.result()
                    store.record_artifact(key, target, version)
                    stats.done += 1
                    print(f"  [file] {target.name}")
                except SessionExpiredError as exc:
                    expired = exc
                except urllib.error.HTTPError as exc:
                    if exc.code == 401:
                        expired = SessionExpiredError("Canvas file session expired")
                    else:
                        stats.failed.append(f"{target.name}: HTTP {exc.code}")
                except Exception as exc:
                    stats.failed.append(f"{target.name}: {type(exc).__name__}: {exc}")
            if expired:
                expired.stage_stats = stats
                raise expired
    return stats


def save_pages(plan: CoursePlan, client: CanvasSessionClient, course_id: str) -> StageStats:
    """Save every Page-type module item as <title>.md directly under the week dir."""
    stats = StageStats()
    from .pages import fetch_page
    for week in plan.weeks:
        for item in week.items:
            if item.get("type") != "Page":
                continue
            try:
                slug = item.get("page_url")
                if not slug:
                    raise ValueError("Page item has no page_url")
                page = fetch_page(client, course_id, str(slug))
                title = str(item.get("title") or page.get("title") or slug)
                base_url = getattr(client, "base_url", f"https://{CANVAS_NETLOC}")
                page_url = f"{base_url}/courses/{course_id}/pages/{urllib.parse.quote(str(slug), safe='')}"
                body = html_to_markdown(page.get("body"), base_url=page_url)
                text = f"# {page.get('title') or title}\n\n{body}\n"
                version = hashlib.sha256(text.encode()).hexdigest()
                with _manifest(plan) as store:
                    key = _artifact_key(week, "page", slug)
                    target = store.artifact_path(key, week.week_dir, f"{sanitize_teacher_title(title)}.md")
                    item["_local_path"] = str(target)
                    if store.artifact_current(key, target, version, verify_hash=True):
                        stats.skipped += 1
                    else:
                        atomic_write_text(target, text)
                        store.record_artifact(key, target, version)
                        stats.done += 1
                linked = _download_linked_files(plan, client, week.week_dir / "attachments",
                                                canvas_file_links(page.get("body"), base_url))
                stats.failed.extend(linked.failed)
            except SessionExpiredError:
                raise
            except (CanvasAPIError, DownloadError, OSError, ValueError) as exc:
                stats.failed.append(f"{week.label}/{item.get('title')}: {exc}")
    return stats


def _count_youtube_urls_in_plan(plan: CoursePlan) -> int:
    """Distinct YouTube video IDs across the whole plan.

    Used to size the "found N YouTube videos, want to log in?" prompt.
    Distinct (not raw item count) because the same video may appear in
    multiple modules and we don't want to overstate the work."""
    seen: set[str] = set()
    for week in plan.weeks:
        for item in week.items:
            if item.get("type") not in {"ExternalUrl", "ExternalTool"}:
                continue
            raw = str(item.get("external_url") or item.get("url") or "")
            for vid in extract_youtube_ids(raw):
                seen.add(vid)
    return len(seen)


def _youtube_cookie_args(cookies_path: Path, cookies_from_browser: str | None) -> list[str]:
    """yt-dlp auth args. A real cookies.txt wins; otherwise read the login
    live from the user's normal browser via --cookies-from-browser, which
    sidesteps Google's 'this browser may not be secure' block on automated
    sign-ins. Empty list = try as a public/unlisted download."""
    if cookies_path.exists():
        return ["--cookies", str(cookies_path)]
    if cookies_from_browser:
        return ["--cookies-from-browser", cookies_from_browser]
    return []


# yt-dlp --cookies-from-browser names mapped to the profile dir that proves
# the browser is installed. Detecting by profile-dir existence keeps us from
# invoking yt-dlp against browsers that aren't there (which only prints scary
# errors). Order = the sequence we try them in on a retry.
def _cookie_browser_candidates() -> list[tuple[str, Path]]:
    import platform

    home = Path.home()
    sys_ = platform.system()
    if sys_ == "Windows":
        local = Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local")
        roaming = Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming")
        return [
            ("chrome", local / "Google" / "Chrome" / "User Data"),
            ("edge", local / "Microsoft" / "Edge" / "User Data"),
            ("brave", local / "BraveSoftware" / "Brave-Browser" / "User Data"),
            ("vivaldi", local / "Vivaldi" / "User Data"),
            ("opera", roaming / "Opera Software" / "Opera Stable"),
            ("firefox", roaming / "Mozilla" / "Firefox" / "Profiles"),
        ]
    if sys_ == "Darwin":
        appsup = home / "Library" / "Application Support"
        return [
            ("chrome", appsup / "Google" / "Chrome"),
            ("edge", appsup / "Microsoft Edge"),
            ("brave", appsup / "BraveSoftware" / "Brave-Browser"),
            ("firefox", appsup / "Firefox" / "Profiles"),
        ]
    cfg = home / ".config"
    return [
        ("chrome", cfg / "google-chrome"),
        ("chromium", cfg / "chromium"),
        ("brave", cfg / "BraveSoftware" / "Brave-Browser"),
        ("firefox", home / ".mozilla" / "firefox"),
    ]


def _installed_cookie_browsers() -> list[str]:
    """yt-dlp browser names whose profile dir exists, in try-order."""
    out: list[str] = []
    for name, path in _cookie_browser_candidates():
        try:
            if path.exists():
                out.append(name)
        except OSError:
            pass
    return out


def maybe_retry_youtube_with_login(
    cookies_path: Path, failed_count: int,
    *, interactive: bool = True, input_fn=None,
) -> bool:
    """Post-failure prompt: yt-dlp couldn't grab `failed_count` videos and we
    have no cookies. Ask once whether to retry using the YouTube login from
    the user's normal browser. Returns True iff the user consents — the caller
    then loops `_installed_cookie_browsers()` passing --cookies-from-browser.

    Why browser cookies instead of opening a login window: Google blocks
    sign-in on automation-controlled browsers ('this browser may not be
    secure'), so a Playwright login almost always fails. Reading cookies from
    the browser the user already uses sidesteps that entirely.

    No-op (returns False, no prompt) when:
      - cookies.txt already exists (failures aren't auth-related)
      - failed_count == 0 (nothing to retry)
      - interactive=False (batch / CI callers)
      - stdin isn't a TTY (piped scripts)

    The upfront "want to log in just in case?" prompt was removed entirely:
    most courses are public, so the common path is now zero prompts.
    """
    if cookies_path.exists():
        return False
    if failed_count <= 0:
        return False
    if not interactive:
        return False
    if input_fn is None:
        if not console.stdin_is_interactive():
            return False
        input_fn = input

    prompt = t(
        f"\n  ❓ 有 {failed_count} 個 YouTube 影片下載失敗。\n"
        f"     如果是私人 / 限齡 / 會員影片,需要你的 YouTube 登入狀態才能抓。\n"
        f"     要用你瀏覽器裡已登入的 YouTube 帳號重試嗎? [y/N]: ",
        f"\n  ❓ {failed_count} YouTube download(s) failed.\n"
        f"     Private / age-restricted / member videos need your YouTube login.\n"
        f"     Retry using the YouTube login from your browser? [y/N]: ",
    )
    try:
        answer = input_fn(prompt).strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return answer in {"y", "yes"}


def download_youtube(
    plan: CoursePlan, *, cookies_path: Path, yt_dlp: str = "yt-dlp",
    cookies_from_browser: str | None = None,
) -> StageStats:
    """For each week with YouTube items, run yt-dlp into the week dir then rename.

    Auth: a cookies.txt at `cookies_path` wins; else, if `cookies_from_browser`
    is set (a yt-dlp browser name), yt-dlp reads the YouTube login live from
    that browser; else the download is attempted as public/unlisted.
    """
    import tempfile
    stats = StageStats()
    cache = plan.course_dir / ".media-cache" / "youtube"
    jobs = {}
    with _manifest(plan) as store:
        for week in plan.weeks:
            title_map = build_video_title_map({"module": week.module})
            for vid, title in title_map.items():
                key = _artifact_key(week, "youtube", vid)
                target = store.artifact_path(key, week.week_dir, f"{sanitize_teacher_title(title)}.mp4")
                for item in week.items:
                    if vid in extract_youtube_ids(str(item.get("external_url") or item.get("url") or "")):
                        item["_local_path"] = str(target)
                if (store.artifact_current(key, target, vid, verify_hash=plan.verify_files)
                        or _adopt_legacy(store, key, target, vid, _valid_video)):
                    stats.skipped += 1
                    continue
                jobs.setdefault(vid, []).append((key, target))
        if not jobs:
            return stats
        if shutil.which("ffmpeg") is None:
            # Without ffmpeg yt-dlp leaves separate video/audio files it can't merge.
            import platform
            hint = {
                "Windows": "winget install Gyan.FFmpeg",
                "Darwin": "brew install ffmpeg",
            }.get(platform.system(), "apt install ffmpeg  (or your distro's equivalent)")
            print(t(
                f"  ⚠ 找不到 ffmpeg,無法把 YouTube 的影片與聲音合併成可播放的 mp4。\n"
                f"     請先安裝: {hint}\n"
                f"     或執行 `ntu-cool-materials doctor --fix` 嘗試自動安裝。YouTube 階段先跳過。",
                f"  ⚠ ffmpeg not found; YouTube video and audio can't be merged into a playable mp4.\n"
                f"     Install it first: {hint}\n"
                f"     Or run `ntu-cool-materials doctor --fix` to try auto-install. Skipping YouTube.",
            ))
            stats.failed = [f"YouTube {vid}: ffmpeg is required" for vid in jobs]
            return stats
        if shutil.which("node") is None:
            # yt-dlp needs a JS runtime for YouTube's challenge; without it some
            # videos fail or cap at 360p.
            print(t(
                "  ⚠ 找不到 Node.js。yt-dlp 在解 YouTube JS 挑戰時可能會失敗或畫質卡在 360p。\n"
                "     建議裝 Node.js: winget install OpenJS.NodeJS / brew install node",
                "  ⚠ Node.js not found. yt-dlp may fail YouTube's JS challenge or cap quality\n"
                "     at 360p. Install: winget install OpenJS.NodeJS / brew install node",
            ))
        cache.mkdir(parents=True, exist_ok=True)
        cache_paths = {vid: store.artifact_path(f"youtube-cache:{vid}", cache, f"{vid}.mp4") for vid in jobs}
        missing = [vid for vid in jobs if not store.artifact_current(
            f"youtube-cache:{vid}", cache_paths[vid], vid, verify_hash=plan.verify_files)]
        if missing:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as tf:
                tf.write("\n".join(f"https://youtu.be/{vid}" for vid in missing))
                urls_file = Path(tf.name)
            cmd = [yt_dlp, *YT_DLP_BASE_ARGS, "--socket-timeout", "30", "--remux-video", "mp4",
                   "-P", str(cache), "-o", "%(id)s.%(ext)s", "-a", str(urls_file)]
            cmd[1:1] = _youtube_cookie_args(cookies_path, cookies_from_browser)
            try:
                # A prior invalid final file must not make yt-dlp claim "already downloaded".
                for vid in missing:
                    cache_paths[vid].unlink(missing_ok=True)
                subprocess.run(cmd, timeout=7200)
            except (OSError, subprocess.TimeoutExpired) as exc:
                print(f"  YouTube downloader: {type(exc).__name__}")
            finally:
                urls_file.unlink(missing_ok=True)
        for vid, destinations in jobs.items():
            source = cache_paths[vid]
            if not _valid_video(source):
                stats.failed.append(f"YouTube {vid}: no complete playable MP4")
                continue
            store.record_artifact(f"youtube-cache:{vid}", source, vid)
            copied, placed_all = False, True
            for key, target in destinations:
                try:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    part = target.with_name(target.name + ".part")
                    part.unlink(missing_ok=True)
                    try:
                        # The cache shares the course volume: a hard link avoids a second copy.
                        os.link(source, part)
                    except OSError:
                        shutil.copyfile(source, part)
                        copied = True
                    part.replace(target)
                    store.record_artifact(key, target, vid)
                    stats.done += 1
                except OSError as exc:
                    placed_all = False
                    stats.failed.append(f"{target.name}: {exc}")
            if copied and placed_all:
                # No hard links here (e.g. exFAT): don't keep a duplicate in the cache.
                source.unlink(missing_ok=True)
    return stats


def _valid_video(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size < 12:
        return False
    with path.open("rb") as stream:
        if b"ftyp" not in stream.read(64):
            return False
    probe = shutil.which("ffprobe")
    if not probe:
        return True
    try:
        result = subprocess.run([probe, "-v", "error", "-show_entries", "stream=codec_type:format=duration",
                                 "-of", "json", str(path)], capture_output=True, text=True, timeout=30)
        data = json.loads(result.stdout)
        return result.returncode == 0 and any(s.get("codec_type") == "video" for s in data.get("streams", []))
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return False


def _ensure_logged_in(page, course_id: str | None, sso_timeout_sec: int) -> bool:
    """Navigate to course root (or /courses if no id); if bounced to login, wait for SSO."""
    from playwright.sync_api import TimeoutError as PWTimeout

    target = f"https://{CANVAS_NETLOC}/courses/{course_id}" if course_id else f"https://{CANVAS_NETLOC}/courses"
    print(f"    確認登入狀態 ({target})")
    # `wait_until="commit"` returns as soon as the browser commits the
    # navigation (i.e. starts loading the response) rather than blocking
    # until the DOM is fully built. NTU's SAML chain is multi-redirect and
    # the final SSO login page is JS-heavy enough that `domcontentloaded`
    # would routinely hit the timeout when the saved cookies were stale —
    # which is exactly the moment we need this code path to work, since
    # the whole reason we're here is to refresh those cookies. The
    # subsequent wait_for_url loop is what actually gates on "user has
    # finished logging in", so we don't need goto itself to wait long.
    try:
        page.goto(target, wait_until="commit", timeout=60000)
    except PWTimeout:
        # The navigation didn't even commit in 60s — either the network
        # is down, or NTU's gateway is completely unreachable. But if
        # we landed somewhere recognizable (a login page mid-redirect),
        # let the wait_for_url loop below take over. Only bail if the
        # page object literally has no URL to work with.
        if not page.url or page.url == "about:blank":
            print("    無法連到 NTU COOL — 請檢查網路或稍後再試")
            return False
        print(f"    導向尚未完成,進入登入等待 ({page.url})")

    if LOGIN_RE.search(page.url) or page.url == "about:blank":
        print(f"    需要登入 — 請在開啟的瀏覽器視窗完成 NTU SSO (最多等 {sso_timeout_sec} 秒)")
        try:
            page.wait_for_url(lambda u: not LOGIN_RE.search(u) and u != "about:blank",
                              timeout=sso_timeout_sec * 1000)
        except PWTimeout:
            print("    SSO 登入逾時")
            return False
    print(f"    已登入: {page.url}")
    return True


@dataclass
class BrowserSession:
    """Holds an open Playwright context for cross-stage reuse (avoids repeat SSO)."""
    manager: Any
    pw: Any
    context: Any
    page: Any
    captured: dict[int, dict[str, Any]]
    owns: bool = True

    def close(self) -> None:
        if not self.owns:
            return
        try:
            self.context.close()
        finally:
            self.manager.__exit__(None, None, None)


def open_browser_session(
    *, profile_dir: Path = Path(".secrets/ntu_cool_browser_profile"),
    headless: bool = False,
    course_id: str | None = None,
    sso_timeout_sec: int = 600,
) -> BrowserSession:
    """Open a persistent Chromium context, ensure SSO, return a reusable BrowserSession.

    Caller is responsible for `.close()` (or use `with closing(...)`)."""
    try:
        from playwright.sync_api import sync_playwright as _sp
    except ImportError as exc:
        raise RuntimeError(
            "Playwright is required. Install with: pip install -e \".[browser]\" && python -m playwright install chromium"
        ) from exc

    profile_dir.parent.mkdir(parents=True, exist_ok=True)
    manager = _sp()
    pw = manager.__enter__()
    try:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir), headless=headless, accept_downloads=False)
        page = context.pages[0] if context.pages else context.new_page()
        captured: dict[int, dict[str, Any]] = {}
        page.on("response", make_cool_video_response_handler(captured))
        if not _ensure_logged_in(page, course_id, sso_timeout_sec):
            context.close()
            raise RuntimeError("SSO failed; aborting")
        return BrowserSession(manager=manager, pw=pw, context=context, page=page, captured=captured, owns=True)
    except Exception:
        manager.__exit__(None, None, None)
        raise


def _dump_cookies_to_headers_file(context, headers_path: Path) -> bool:
    """Write Canvas cookies from the live Playwright context to a DevTools-style headers file."""
    cookies = context.cookies(f"https://{CANVAS_NETLOC}")
    pairs = [f"{c['name']}={c['value']}" for c in cookies if c.get("name") and c.get("value")]
    if not pairs:
        return False
    headers_path.parent.mkdir(parents=True, exist_ok=True)
    headers_path.write_text(
        "\n".join([
            "accept: application/json, text/plain, */*",
            f"cookie: {'; '.join(pairs)}",
            f"referer: https://{CANVAS_NETLOC}/",
            "user-agent: ntu-cool-materials/0.1",
        ]) + "\n",
        encoding="utf-8",
    )
    # The full cookie jar (with domain/path/secure, plus the NTU SSO cookies)
    # lets a later, invisible Chromium resume this login: Canvas session
    # cookies don't survive the browser exiting, even in a persistent profile.
    try:
        atomic_write_text(_storage_state_path(headers_path), json.dumps({"cookies": context.cookies()}))
    except (OSError, TypeError, ValueError):
        pass
    return True


STORAGE_STATE_NAME = "ntu_cool_storage_state.json"
_COOKIE_KEYS = ("name", "value", "domain", "path", "expires", "httpOnly", "secure", "sameSite")


def _storage_state_path(headers_path: Path) -> Path:
    return headers_path.with_name(STORAGE_STATE_NAME)


def _saved_login_cookies(headers_path: Path) -> list[dict[str, Any]]:
    """Cookies from the last login, in Playwright `add_cookies` form.

    Prefers the saved cookie jar; falls back to the headers file's `cookie:`
    line (written by older versions and by `ntu-cool-session`)."""
    try:
        saved = json.loads(_storage_state_path(headers_path).read_text(encoding="utf-8")).get("cookies")
        cookies = [{k: c[k] for k in _COOKIE_KEYS if k in c} for c in saved
                   if isinstance(c, dict) and c.get("name") and c.get("domain")]
        if cookies:
            return cookies
    except (OSError, ValueError, AttributeError, TypeError):
        pass
    from .session_client import read_headers_file
    try:
        headers = read_headers_file(headers_path)
    except (OSError, ValueError):
        return []
    header = next((v for k, v in headers.items() if k.lower() == "cookie"), "")
    cookies = []
    for part in header.split(";"):
        name, sep, value = part.strip().partition("=")
        if sep and name:
            cookies.append({"name": name, "value": value, "domain": CANVAS_NETLOC, "path": "/", "secure": True})
    return cookies


def _inject_saved_login(context, headers_path: Path | None) -> int:
    """Add the saved login cookies to a fresh context. Returns how many stuck."""
    if headers_path is None:
        return 0
    added = 0
    for cookie in _saved_login_cookies(headers_path):
        try:  # one at a time: a single malformed cookie must not drop the rest
            context.add_cookies([cookie])
            added += 1
        except Exception:
            pass
    return added


def _session_alive(page, course_id: str | None, settle_ms: int = 15000) -> bool:
    """Open the course without waiting on the user; True if Canvas let us in.

    A saved NTU SSO cookie can bounce through the SAML pages on its own, so a
    login URL gets `settle_ms` to resolve before we call the session dead."""
    from playwright.sync_api import TimeoutError as PWTimeout

    target = f"https://{CANVAS_NETLOC}/courses/{course_id}" if course_id else f"https://{CANVAS_NETLOC}/courses"
    try:
        page.goto(target, wait_until="domcontentloaded", timeout=60000)
    except PWTimeout:
        pass
    if not LOGIN_RE.search(page.url) and page.url != "about:blank":
        return True
    try:
        page.wait_for_url(lambda u: not LOGIN_RE.search(u) and u != "about:blank", timeout=settle_ms)
        return True
    except PWTimeout:
        return False


def _capture_cool_video_in_page(page, captured: dict[int, dict[str, Any]],
                                module_item_url: str, video_id: int,
                                sso_timeout_sec: int) -> dict[str, Any] | None:
    """Drive one navigation in an existing page; return the captured view JSON or None."""
    from playwright.sync_api import TimeoutError as PWTimeout

    for attempt in range(4):
        captured.clear()
        try:
            page.goto(module_item_url, wait_until="domcontentloaded", timeout=90000)
        except Exception as exc:
            print(f"      嘗試 {attempt+1} 載入失敗: {exc}")
            continue
        if LOGIN_RE.search(page.url):
            print(f"      嘗試 {attempt+1} 被導回登入頁,等待 SSO")
            try:
                page.wait_for_url(lambda u: not LOGIN_RE.search(u), timeout=sso_timeout_sec * 1000)
            except PWTimeout:
                return None
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            if video_id in captured:
                return captured[video_id]
            page.wait_for_timeout(1500)
        print(f"      嘗試 {attempt+1}: 還沒擷取到 view JSON")
    return None


def _cool_video_targets(plan: CoursePlan) -> list[tuple[WeekPlan, dict[str, Any], int]]:
    targets: list[tuple[WeekPlan, dict[str, Any], int]] = []
    for week in plan.weeks:
        for item in week.items:
            if item.get("type") != "ExternalTool":
                continue
            ext = str(item.get("external_url") or "")
            m = re.search(r"cool-video\.dlc\.ntu\.edu\.tw/.*?/videos/(\d+)", ext)
            if not m:
                continue
            targets.append((week, item, int(m.group(1))))
    return targets


def _pending_cool_videos(plan):
    pending, skipped = [], 0
    with _manifest(plan) as store:
        for week, item, vid in _cool_video_targets(plan):
            key = _artifact_key(week, "cool-video", vid)
            title = str(item.get("title") or f"video-{vid}")
            target = store.artifact_path(key, week.week_dir, f"{sanitize_teacher_title(title)}.mp4")
            item["_local_path"] = str(target)
            version = json.dumps([vid, item.get("updated_at")])
            if (store.artifact_current(key, target, version, verify_hash=plan.verify_files)
                    or _adopt_legacy(store, key, target, version, _valid_video)):
                skipped += 1
            else:
                pending.append((week, item, vid, key, target, version))
    return pending, skipped


def capture_and_download_cool_videos_in_page(plan: CoursePlan, page, captured: dict[int, dict[str, Any]],
                                              *, course_id: str, sso_timeout_sec: int = 600) -> StageStats:
    pending, skipped = _pending_cool_videos(plan)
    stats = StageStats(skipped=skipped)
    with _manifest(plan) as store:
        for week, item, vid, key, target, version in pending:
            module_url = f"https://{CANVAS_NETLOC}/courses/{course_id}/modules/items/{item['id']}"
            for attempt in range(2):
                try:
                    view = _capture_cool_video_in_page(page, captured, module_url, vid, sso_timeout_sec)
                    if not view:
                        raise DownloadError("Could not capture video source")
                    url = view.get("altSourceUri") or view.get("sourceUri")
                    if not url:
                        raise DownloadError("Video has no download source")
                    if urllib.parse.urlsplit(url).path.lower().endswith((".mpd", ".m3u8")):
                        raise DownloadError("Only a streaming manifest is available; no downloadable MP4")
                    _download_signed_url(url, target)
                    store.record_artifact(key, target, version)
                    stats.done += 1
                    break
                except urllib.error.HTTPError as exc:
                    if exc.code in {401, 403} and attempt == 0:
                        continue  # refresh the signed URL, not the expired URL itself
                    stats.failed.append(f"{target.name}: HTTP {exc.code}")
                    break
                except Exception as exc:
                    stats.failed.append(f"{target.name}: {type(exc).__name__}: {exc}")
                    break
    return stats


def make_cool_video_response_handler(captured: dict[int, dict[str, Any]]):
    """Return a Playwright response handler that captures /api/.../videos/:id/view JSON bodies."""
    def _on_response(resp):
        if not COOL_VIDEO_VIEW_RE.search(resp.url.split("?", 1)[0]):
            return
        try:
            ct = resp.headers.get("content-type", "")
            if "json" not in ct:
                return
            v = json.loads(resp.text())
            captured[int(v["videoId"])] = v
        except Exception:
            pass
    return _on_response


def capture_and_download_cool_videos(
    plan: CoursePlan, *,
    course_id: str,
    profile_dir: Path = Path(".secrets/ntu_cool_browser_profile"),
    headless: bool = False,
    sso_timeout_sec: int = 600,
    headers_path: Path | None = None,
) -> StageStats:
    """Standalone entry point: open a Playwright context, log in, capture+download every cool-video.

    Runs invisibly on the saved login from `headers_path` when it is still
    valid; a visible SSO window opens only if it has expired (never with
    `headless`). The refreshed cookies are saved back for the next course."""
    stats = StageStats()
    pending, stats.skipped = _pending_cool_videos(plan)
    if not pending:
        return stats
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        stats.failed.extend(f"{job[4].name}: Playwright is required" for job in pending)
        print("    沒有安裝 Playwright。請執行: pip install -e \".[browser]\" 之後 python -m playwright install chromium")
        return stats
    profile_dir.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        def launch(visible: bool):
            ctx = p.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir), headless=not visible, accept_downloads=False)
            _inject_saved_login(ctx, headers_path)
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            captured: dict[int, dict[str, Any]] = {}
            page.on("response", make_cool_video_response_handler(captured))
            return ctx, page, captured

        ctx, page, captured = launch(visible=False)
        if not _session_alive(page, course_id):
            ctx.close()
            if headless:
                print(t("    NTU COOL 登入已過期,--headless 模式無法開視窗重新登入",
                        "    NTU COOL login expired; --headless can't open a window to log in again"))
                stats.failed.extend(f"{job[4].name}: SSO login required" for job in pending)
                return stats
            print(t("    NTU COOL 登入已過期,開啟瀏覽器重新登入",
                    "    NTU COOL login expired; opening a browser to log in again"))
            ctx, page, captured = launch(visible=True)
            if not _ensure_logged_in(page, course_id, sso_timeout_sec):
                ctx.close()
                stats.failed.extend(f"{job[4].name}: SSO login failed" for job in pending)
                return stats
        try:
            stats = capture_and_download_cool_videos_in_page(
                plan, page, captured, course_id=course_id, sso_timeout_sec=sso_timeout_sec)
        finally:
            if headers_path is not None:
                _dump_cookies_to_headers_file(ctx, headers_path)
            ctx.close()
    return stats


# ---- top-level orchestrator ----

def _build_session_client_from_file(headers_path: Path, base_url: str) -> CanvasSessionClient:
    from .session_client import read_headers_file
    return CanvasSessionClient(base_url=base_url, headers=read_headers_file(headers_path))


def _write_course_overview(plan: CoursePlan, *, all_file_types: bool = False) -> Path:
    """Write a Markdown index at the course root listing every downloaded artifact per week.
    Designed to be readable by both humans and AI tools."""
    from datetime import datetime, timezone
    course_name = plan.course.get("name") or plan.course.get("course_code") or plan.course_id
    lines: list[str] = [
        f"# {course_name}",
        "",
        f"**課程 ID:** {plan.course_id}",
        f"**產生時間:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        f"**路徑:** `{plan.course_dir}`",
        "",
        "課程教材總覽。每週的 PDF / Page / 影片都列在下方並連到本機檔案。",
        "",
    ]
    if (plan.course_dir / "announcements" / "announcements.md").exists():
        lines.extend(["## 公告", "", "- [公告全文](announcements/announcements.md)", ""])
    for week in plan.weeks:
        module_name = week.module.get("name") or week.label
        lines.append(f"## {module_name}")
        lines.append("")
        # Group items by file type for readability
        per_type: dict[str, list[str]] = {"pdf": [], "md": [], "mp4": [], "other": []}
        for item in week.items:
            kind = item.get("type")
            title = str(item.get("title") or "").strip()
            local = item.get("_local_path")
            if local and Path(local).is_file():
                path = Path(local)
                rel = path.relative_to(plan.course_dir.resolve()).as_posix()
                bucket = "md" if path.suffix == ".md" else "mp4" if path.suffix == ".mp4" else "other"
                per_type[bucket].append(f"- [{title}](<{urllib.parse.quote(rel)}>)")
                continue
            if kind == "File":
                fname = _file_item_target_name(item, all_file_types=all_file_types)
                if (week.week_dir / fname).exists():
                    # In default mode every file is .pdf on disk, so the dual
                    # icon only matters under --all-file-types where extensions
                    # are preserved. Either way we route by what's actually
                    # on disk, not by what the source extension said.
                    icon = "📄" if Path(fname).suffix.lower() == ".pdf" else "📎"
                    bucket = "pdf" if Path(fname).suffix.lower() == ".pdf" else "other"
                    per_type[bucket].append(f"- {icon} [{title}]({week.label}/{urllib.parse.quote(fname)})")
            elif kind == "Page":
                fname = f"{sanitize_teacher_title(title)}.md"
                if (week.week_dir / fname).exists():
                    per_type["md"].append(f"- 📝 [{title}]({week.label}/{urllib.parse.quote(fname)})")
            elif kind in {"ExternalUrl", "ExternalTool"}:
                fname = f"{sanitize_teacher_title(title)}.mp4"
                if (week.week_dir / fname).exists():
                    per_type["mp4"].append(f"- 🎬 [{title}]({week.label}/{urllib.parse.quote(fname)})")
                else:
                    raw = str(item.get("external_url") or item.get("url") or "")
                    per_type["other"].append(f"- 🔗 [{title}]({raw})")
        for key in ("pdf", "md", "mp4", "other"):
            for line in per_type[key]:
                lines.append(line)
        if not any(per_type.values()):
            lines.append("- _(沒有可下載的內容)_")
        lines.append("")
    overview = plan.course_dir / "course_overview.md"
    atomic_write_text(overview, "\n".join(lines))
    return overview




def download_course(
    *, course_id: str, output_dir: Path,
    base_url: str = f"https://{CANVAS_NETLOC}",
    headers_path: Path = Path(".secrets/ntu_cool_headers.txt"),
    refresh_session: bool = False,
    client: CanvasSessionClient | None = None,
    browser: BrowserSession | None = None,
    yt_cookies: Path | None = None, yt_dlp: str = "yt-dlp",
    profile_dir: Path = Path(".secrets/ntu_cool_browser_profile"),
    headless: bool = False,
    skip_pdfs: bool = False, skip_pages: bool = False,
    skip_announcements: bool = False,
    skip_youtube: bool = False, skip_cool_videos: bool = False,
    all_file_types: bool = False,
    sso_timeout_sec: int = 600,
    verify_files: bool = False, workers: int = 3,
) -> CoursePlan:
    """Top-level orchestrator. Opens at most ONE Playwright context for the entire run.

    Pass `browser` (e.g. from `pick`) to reuse an already-opened logged-in context — no second SSO.
    Otherwise: opens its own context if `refresh_session` or any cool-video items exist.
    """
    owns_browser = False
    try:
        if browser is None and refresh_session:
            browser = open_browser_session(
                profile_dir=profile_dir, headless=headless,
                course_id=course_id, sso_timeout_sec=sso_timeout_sec,
            )
            owns_browser = True
            if not _dump_cookies_to_headers_file(browser.context, headers_path):
                raise RuntimeError("No NTU COOL cookies found in browser context")
            print(f"  已寫入登入憑證 → {headers_path}")

        if client is None:
            client = _build_session_client_from_file(headers_path, base_url)

        course_stats = CourseStats()

        def _try_recover_session() -> bool:
            """Refresh SSO + cookies if we have a Playwright session. Returns True on success."""
            nonlocal client, browser, owns_browser
            if browser is None:
                if not console.stdin_is_interactive():
                    return False
                browser = open_browser_session(profile_dir=profile_dir, headless=headless,
                                               course_id=course_id, sso_timeout_sec=sso_timeout_sec)
                owns_browser = True
            print(t(
                "  → NTU COOL 登入已過期,在同一個瀏覽器重新登入...",
                "  → NTU COOL session expired, re-authenticating in the open browser...",
            ))
            if not _ensure_logged_in(browser.page, course_id, sso_timeout_sec):
                return False
            if not _dump_cookies_to_headers_file(browser.context, headers_path):
                return False
            client = _build_session_client_from_file(headers_path, base_url)
            print(t("  ✓ 重新登入完成,重試此階段", "  ✓ session refreshed, retrying"))
            return True

        def _api_call_with_session_retry(fn, label: str):
            """Run fn(client). If it raises SessionExpiredError, try to refresh
            the login once and retry. Used for any API call that touches
            cool.ntu.edu.tw — list_modules, get_course, files, pages, etc."""
            try:
                return fn(client)
            except SessionExpiredError as exc:
                print(t(f"  ⚠ {label}: {exc}", f"  ⚠ {label}: {exc}"))
                if not _try_recover_session():
                    raise
                result = fn(client)
                previous = getattr(exc, "stage_stats", None)
                if previous is not None and isinstance(result, StageStats):
                    result.done += previous.done
                    result.skipped = max(0, result.skipped - previous.done)
                return result

        # plan_course hits the Canvas API (get_course + list_modules); if the
        # session expired between list_courses (in cli.py) and now, this is
        # where we'd see the 401. Wrap it in the same retry helper as the
        # download stages.
        plan = _api_call_with_session_retry(
            lambda c: plan_course(c, course_id, output_dir),
            t("plan", "plan"),
        )
        plan.verify_files = verify_files
        plan.workers = max(1, min(workers, 4))
        plan.stats = course_stats
        print(t(f"課程: {plan.course.get('name')!r}", f"Course: {plan.course.get('name')!r}"))
        print(t(f"存放位置: {plan.course_dir}", f"Output: {plan.course_dir}"))
        print(t(
            f"有教材的週次: {[w.label for w in plan.weeks]}",
            f"Weeks with content: {[w.label for w in plan.weeks]}",
        ))

        def _run_with_session_retry(stage_fn, label: str) -> StageStats:
            return _api_call_with_session_retry(stage_fn, label)

        for stage, disabled in ((course_stats.announcements, skip_announcements), (course_stats.pdfs, skip_pdfs),
                                (course_stats.pages, skip_pages), (course_stats.youtube, skip_youtube),
                                (course_stats.cool_videos, skip_cool_videos)):
            stage.disabled = disabled

        if not skip_announcements:
            print(t("\n[1/5] 公告內容", "\n[1/5] Announcements"))
            course_stats.announcements = _run_with_session_retry(
                lambda c: save_announcements(plan, c), t("公告", "announcements")
            )
            print(t(
                f"  儲存 {course_stats.announcements.done}、跳過 {course_stats.announcements.skipped}、失敗 {len(course_stats.announcements.failed)}",
                f"  saved {course_stats.announcements.done}, skipped {course_stats.announcements.skipped}, failed {len(course_stats.announcements.failed)}",
            ))

        if not skip_pdfs:
            print(t("\n[2/5] 教材檔案", "\n[2/5] Files"))
            course_stats.pdfs = _run_with_session_retry(
                lambda c: download_files(plan, c, all_file_types=all_file_types), t("PDF", "files")
            )
            print(t(
                f"  下載 {course_stats.pdfs.done}、跳過 {course_stats.pdfs.skipped}、失敗 {len(course_stats.pdfs.failed)}",
                f"  downloaded {course_stats.pdfs.done}, skipped {course_stats.pdfs.skipped}, failed {len(course_stats.pdfs.failed)}",
            ))
        if not skip_pages:
            print(t("\n[3/5] Page 內容", "\n[3/5] Pages"))
            course_stats.pages = _run_with_session_retry(
                lambda c: save_pages(plan, c, course_id), t("Page", "pages")
            )
            print(t(
                f"  儲存 {course_stats.pages.done}、跳過 {course_stats.pages.skipped}、失敗 {len(course_stats.pages.failed)}",
                f"  saved {course_stats.pages.done}, skipped {course_stats.pages.skipped}, failed {len(course_stats.pages.failed)}",
            ))
        if not (skip_pdfs and skip_pages):
            # Files and Page attachments can both bring in Excel workbooks.
            excel = convert_spreadsheets(plan.course_dir)
            if excel.done or excel.failed:
                print(t(f"  Excel → Markdown: 轉換 {excel.done}、失敗 {len(excel.failed)}",
                        f"  Excel → Markdown: {excel.done} converted, {len(excel.failed)} failed"))
        if not skip_youtube:
            print(t("\n[4/5] YouTube 影片", "\n[4/5] YouTube videos"))
            if yt_cookies is None:
                from .cli import _secrets_dir
                yt_cookies = _secrets_dir() / "youtube_cookies.txt"
            yt_cookies_path = yt_cookies
            has_youtube_jobs = any(
                extract_youtube_ids(str(item.get("external_url") or item.get("url") or ""))
                for week in plan.weeks
                for item in week.items
            )
            # None = no update offered yet; True/False = already updated or declined.
            yt_dlp_update = None
            if has_youtube_jobs:
                from .update_check import ensure_yt_dlp_updated
                try:
                    yt_dlp_update = ensure_yt_dlp_updated(yt_dlp=yt_dlp, max_age_days=60)
                except Exception:
                    pass

            # Strategy: just try to download. Most class YouTube content is
            # public, so most users never need cookies — asking up front
            # would burn an interaction on every run for no benefit. After
            # the run, IF anything failed AND we don't have cookies, then
            # we ask. That way zero-interaction is the common path and the
            # post-failure prompt can quote a concrete number of failures.
            course_stats.youtube = download_youtube(
                plan, cookies_path=yt_cookies_path, yt_dlp=yt_dlp
            )
            failed_count = len(course_stats.youtube.failed)
            if failed_count > 0 and has_youtube_jobs and yt_dlp_update is None:
                from .update_check import confirm_yt_dlp_update, update_yt_dlp
                ok = False
                if confirm_yt_dlp_update(t(
                    f"\n  有 {failed_count} 個 YouTube 影片下載失敗；更新 yt-dlp 後重試嗎？[Y/n]: ",
                    f"\n  {failed_count} YouTube download(s) failed; update yt-dlp and retry? [Y/n]: ",
                )):
                    ok, msg = update_yt_dlp(yt_dlp=yt_dlp)
                    print(f"  [yt-dlp] {msg}")
                if ok:
                    retry_stats = download_youtube(
                        plan, cookies_path=yt_cookies_path, yt_dlp=yt_dlp
                    )
                    course_stats.youtube.done += retry_stats.done
                    course_stats.youtube.failed = retry_stats.failed
                    failed_count = len(course_stats.youtube.failed)
            if maybe_retry_youtube_with_login(yt_cookies_path, failed_count):
                # Retry the still-missing videos using the YouTube login from
                # the user's normal browser(s). We try each installed browser
                # in turn and stop as soon as nothing's left failing — the
                # right browser is whichever one the user is signed into.
                # Already-downloaded videos are skipped on each pass, so the
                # retries only re-attempt what's still missing (no double work,
                # and `done` can't double-count: each pass derives `done` from
                # files that weren't on disk when that pass started).
                browsers = _installed_cookie_browsers()
                if not browsers:
                    print(t(
                        "  ⚠ 找不到可讀取的瀏覽器(Chrome / Edge / Firefox …),無法用瀏覽器登入重試。",
                        "  ⚠ No readable browser (Chrome / Edge / Firefox …) found; can't retry with browser cookies.",
                    ))
                for br in browsers:
                    print(t(
                        f"\n  → 用 {br} 裡已登入的 YouTube 帳號重試失敗的下載...",
                        f"\n  → retrying failed downloads using your {br} YouTube login...",
                    ))
                    retry_stats = download_youtube(
                        plan, cookies_path=yt_cookies_path, yt_dlp=yt_dlp,
                        cookies_from_browser=br,
                    )
                    course_stats.youtube.done += retry_stats.done
                    course_stats.youtube.failed = retry_stats.failed
                    if not retry_stats.failed:
                        break
                recovered = failed_count - len(course_stats.youtube.failed)
                if recovered > 0:
                    print(t(
                        f"  ✓ 重試救回 {recovered} 個影片",
                        f"  ✓ retry recovered {recovered} video(s)",
                    ))
                if browsers and course_stats.youtube.failed:
                    print(t(
                        "  ⚠ 還是有影片抓不到。若你用 Chrome / Edge,請先完全關閉瀏覽器再重跑一次"
                        "(瀏覽器執行中時 cookie 檔會被鎖住)。",
                        "  ⚠ Some videos still failed. If you use Chrome / Edge, fully close it and re-run "
                        "— its cookie database is locked while the browser is open.",
                    ))

        if not skip_cool_videos:
            print(t("\n[5/5] NTU 上課影片 (cool-video)", "\n[5/5] NTU CDN videos (cool-video)"))
            if browser is not None:
                course_stats.cool_videos = capture_and_download_cool_videos_in_page(
                    plan, browser.page, browser.captured,
                    course_id=course_id, sso_timeout_sec=sso_timeout_sec,
                )
            else:
                course_stats.cool_videos = capture_and_download_cool_videos(
                    plan, course_id=course_id, profile_dir=profile_dir, headless=headless,
                    sso_timeout_sec=sso_timeout_sec, headers_path=headers_path,
                )

        # Per-course overview at the course root.
        try:
            overview_path = _write_course_overview(plan, all_file_types=all_file_types)
            print(t(f"\n  目錄: {overview_path.name}", f"\n  overview: {overview_path.name}"))
        except Exception as exc:
            print(t(f"\n  (無法產生目錄: {exc})", f"\n  (could not write overview: {exc})"))

        # Summary
        print("\n" + "=" * 60)
        print(t("完成。", "Done.") if course_stats.successful else t("部分下載失敗。", "Completed with failures."))
        def _row(label_zh: str, label_en: str, s: StageStats) -> None:
            if s.disabled:
                print(t(f"  {label_zh} 使用者略過", f"  {label_en} disabled by user"))
                return
            line_zh = f"  {label_zh}  新增 {s.done}、跳過 {s.skipped}、失敗 {len(s.failed)}"
            line_en = f"  {label_en}  {s.done} new, {s.skipped} skipped, {len(s.failed)} failed"
            print(t(line_zh, line_en))
        _row("公告:      ", "Announcements:", course_stats.announcements)
        _row("檔案:      ", "Files:       ", course_stats.pdfs)
        _row("Page:      ", "Pages:       ", course_stats.pages)
        _row("YouTube:   ", "YouTube:     ", course_stats.youtube)
        _row("上課影片:  ", "Cool-video:  ", course_stats.cool_videos)
        all_failures = (course_stats.announcements.failed + course_stats.pdfs.failed + course_stats.pages.failed
                        + course_stats.youtube.failed + course_stats.cool_videos.failed)
        if all_failures:
            print(t(f"\n失敗清單 ({len(all_failures)} 筆):", f"\nFailures ({len(all_failures)}):"))
            for f in all_failures:
                print(f"  ✗ {f}")
        print(t(
            f"\n檔案存放位置:\n  {plan.course_dir.resolve()}",
            f"\nFiles saved to:\n  {plan.course_dir.resolve()}",
        ))
        atomic_write_text(plan.course_dir / ".download_report.json", json.dumps(
            {"successful": course_stats.successful, "stages": {
                name: {"done": stage.done, "skipped": stage.skipped, "failed": stage.failed, "disabled": stage.disabled}
                for name, stage in vars(course_stats).items()}}, ensure_ascii=False, indent=2))
        # Older versions wrote a visible report; the hidden one above replaces it.
        (plan.course_dir / "download_report.json").unlink(missing_ok=True)
        return plan
    finally:
        if owns_browser and browser is not None:
            browser.close()
