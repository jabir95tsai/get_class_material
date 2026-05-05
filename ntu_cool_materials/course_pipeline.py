"""Bulk per-course download pipeline.

Public API used by `cli.download-course`:
    plan_course(client, course_id, output_dir) -> CoursePlan
    download_files(plan, client) -> None
    save_pages(plan, client, course_id) -> None
    download_youtube(plan, *, cookies_path, yt_dlp) -> None
    capture_and_download_cool_videos(plan, *, course_id, profile_dir, headless) -> None

Each step is idempotent: it skips items already present on disk.

Why a single `capture_and_download_cool_videos`: NTU SAML session cookies on
cool.ntu.edu.tw are session-only (die when the Chromium process exits), so the
LTI launches must happen inside the same Playwright context that established
login. The function opens one persistent context, waits for SSO if the course
root bounces to /login, then walks every cool-video module item and downloads
the transcoded.mp4 from the captured /api/.../view JSON's altSourceUri.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .announcements import html_to_text
from .canvas_client import NoRedirectHandler
from .media_naming import build_video_title_map, extract_youtube_ids, rename_downloaded_videos, sanitize_teacher_title
from .session_client import DROP_REQUEST_HEADER_NAMES, CanvasSessionClient
from .storage import course_directory_name


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


@dataclass
class StageStats:
    done: int = 0
    skipped: int = 0
    failed: list[str] = field(default_factory=list)  # human labels of failed items


@dataclass
class CourseStats:
    pdfs: StageStats = field(default_factory=StageStats)
    pages: StageStats = field(default_factory=StageStats)
    youtube: StageStats = field(default_factory=StageStats)
    cool_videos: StageStats = field(default_factory=StageStats)


class SessionExpiredError(RuntimeError):
    """Raised when a Canvas request comes back 401/403 mid-pipeline."""


# ---- planning ----

def plan_course(client: CanvasSessionClient, course_id: str, output_dir: Path) -> CoursePlan:
    """Fetch course + modules and lay out per-week directory skeletons.

    Writes <course_dir>/modules_raw.json and <course_dir>/<weekLabel>/metadata/<weekLabel>_items.json.
    Includes only weeks that have at least one downloadable item type (File/Page/ExternalUrl/ExternalTool).
    """
    course = client.get_course(course_id)
    modules = list(client.list_paginated(
        f"/api/v1/courses/{urllib.parse.quote(str(course_id), safe='')}/modules",
        params=[("per_page", "100"), ("include[]", "items"), ("include[]", "content_details")],
    ))

    course_dir = output_dir / course_directory_name(course)
    course_dir.mkdir(parents=True, exist_ok=True)
    # Migration: drop the legacy modules_raw.json — pipeline now keeps modules in memory.
    legacy_modules_raw = course_dir / "modules_raw.json"
    if legacy_modules_raw.exists():
        legacy_modules_raw.unlink()

    plan = CoursePlan(course=course, course_id=str(course_id), course_dir=course_dir)
    relevant_types = {"File", "Page", "ExternalUrl", "ExternalTool"}
    for index, module in enumerate(modules, start=1):
        items = module.get("items") or []
        if not any(i.get("type") in relevant_types for i in items):
            continue
        label = _module_label(module, index)
        week_dir = course_dir / label
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
    # Remove legacy metadata/ entirely (no longer written).
    legacy_metadata = week_dir / "metadata"
    if legacy_metadata.is_dir():
        shutil.rmtree(legacy_metadata, ignore_errors=True)


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


def _stream_with_progress(resp, target: Path, label: str) -> None:
    """Read `resp` into `target` while printing an inline progress bar."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".part")
    total = 0
    try:
        total = int(resp.headers.get("Content-Length") or 0)
    except (TypeError, ValueError):
        total = 0
    downloaded = 0
    last_print = 0.0
    bar_len = 28
    with tmp.open("wb") as f:
        while True:
            chunk = resp.read(1024 * 1024)
            if not chunk:
                break
            f.write(chunk)
            downloaded += len(chunk)
            now = time.monotonic()
            if now - last_print > 0.15 or (total and downloaded >= total):
                if total:
                    pct = downloaded * 100 // total
                    filled = pct * bar_len // 100
                    bar = "#" * filled + "-" * (bar_len - filled)
                    sys.stdout.write(
                        f"\r      [{bar}] {pct:3d}%  "
                        f"{downloaded/1024/1024:6.1f} / {total/1024/1024:6.1f} MB  {label}   "
                    )
                else:
                    sys.stdout.write(
                        f"\r      {downloaded/1024/1024:6.1f} MB  {label}   "
                    )
                sys.stdout.flush()
                last_print = now
    sys.stdout.write("\n")
    sys.stdout.flush()
    tmp.replace(target)


def _download_canvas_file(file_id: str, target: Path, headers: dict[str, str]) -> None:
    opener = urllib.request.build_opener(NoRedirectHandler)
    current = f"https://{CANVAS_NETLOC}/files/{file_id}/download?download_frd=1"

    for _ in range(10):
        parsed = urllib.parse.urlparse(current)
        h = dict(headers) if parsed.netloc.lower() == CANVAS_NETLOC else {"User-Agent": "ntu-cool-materials/0.1"}
        req = urllib.request.Request(current, headers=h)
        try:
            resp = opener.open(req, timeout=120)
            break
        except urllib.error.HTTPError as exc:
            if exc.code in {301, 302, 303, 307, 308}:
                current = urllib.parse.urljoin(current, exc.headers["Location"])
                continue
            raise
    else:
        raise RuntimeError(f"too many redirects for file {file_id}")

    with resp:
        _stream_with_progress(resp, target, target.name)


def _download_signed_url(url: str, target: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "ntu-cool-materials/0.1"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        _stream_with_progress(resp, target, target.name)


# ---- per-stage workers ----

def download_files(plan: CoursePlan, client: CanvasSessionClient) -> StageStats:
    """Download every File-type module item directly into the week directory."""
    headers = _session_headers(client)
    stats = StageStats()
    for week in plan.weeks:
        for item in week.items:
            if item.get("type") != "File":
                continue
            file_id = str(item.get("content_id") or "")
            title = str(item.get("title") or "").strip() or f"item-{item.get('id')}"
            stem = title[:-4] if title.lower().endswith(".pdf") else title
            safe_title = sanitize_teacher_title(stem)
            target = week.week_dir / f"{safe_title}.pdf"
            if target.exists():
                stats.skipped += 1
                continue
            print(f"  [{week.label}/file] {target.name}")
            try:
                _download_canvas_file(file_id, target, headers)
                stats.done += 1
            except urllib.error.HTTPError as exc:
                if exc.code in {401, 403}:
                    raise SessionExpiredError(f"got HTTP {exc.code} downloading {target.name}") from exc
                print(f"      ✗ failed: {exc}")
                stats.failed.append(f"{week.label}/{target.name}: HTTP {exc.code}")
            except Exception as exc:
                print(f"      ✗ failed: {exc}")
                stats.failed.append(f"{week.label}/{target.name}: {type(exc).__name__}: {exc}")
    return stats


def save_pages(plan: CoursePlan, client: CanvasSessionClient, course_id: str) -> StageStats:
    """Save every Page-type module item as <title>.md directly under the week dir."""
    headers = _session_headers(client)
    stats = StageStats()
    for week in plan.weeks:
        for item in week.items:
            if item.get("type") != "Page":
                continue
            page_url = item.get("page_url")
            if not page_url:
                continue
            title = str(item.get("title") or "").strip() or f"item-{item.get('id')}"
            safe_title = sanitize_teacher_title(title)
            target_md = week.week_dir / f"{safe_title}.md"
            if target_md.exists():
                stats.skipped += 1
                continue
            url = (
                f"https://{CANVAS_NETLOC}/api/v1/courses/{urllib.parse.quote(str(course_id), safe='')}"
                f"/pages/{urllib.parse.quote(str(page_url), safe='')}"
            )
            req = urllib.request.Request(url, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    page = json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                if exc.code in {401, 403}:
                    raise SessionExpiredError(f"got HTTP {exc.code} fetching page {target_md.name}") from exc
                print(f"      ✗ failed: {exc}")
                stats.failed.append(f"{week.label}/{target_md.name}: HTTP {exc.code}")
                continue
            except Exception as exc:
                print(f"      ✗ failed: {exc}")
                stats.failed.append(f"{week.label}/{target_md.name}: {type(exc).__name__}: {exc}")
                continue
            body = html_to_text(page.get("body"))
            md_text = f"# {page.get('title') or '(untitled)'}\n\n{body}\n" if body else f"# {page.get('title') or '(untitled)'}\n"
            target_md.write_text(md_text, encoding="utf-8")
            print(f"  [{week.label}/page] {target_md.name}")
            stats.done += 1
    return stats


def download_youtube(plan: CoursePlan, *, cookies_path: Path, yt_dlp: str = "yt-dlp") -> StageStats:
    """For each week with YouTube items, run yt-dlp into the week dir then rename."""
    import tempfile
    stats = StageStats()
    for week in plan.weeks:
        urls: list[str] = []
        seen: set[str] = set()
        for item in week.items:
            if item.get("type") not in {"ExternalUrl", "ExternalTool"}:
                continue
            raw = str(item.get("external_url") or item.get("url") or "")
            for vid in extract_youtube_ids(raw):
                u = f"https://youtu.be/{vid}"
                if u not in seen:
                    seen.add(u); urls.append(u)
        if not urls:
            continue

        week_items = {"module": week.module}
        videos_dir = week.week_dir
        title_map = build_video_title_map(week_items)
        expected_titles = {sanitize_teacher_title(t) for t in title_map.values() if t}
        existing = {p.stem for p in videos_dir.glob("*.mp4")} if videos_dir.exists() else set()
        missing = expected_titles - existing
        if expected_titles and not missing:
            print(f"  [{week.label}/youtube] {len(expected_titles)} video(s) already present, skipping yt-dlp")
            stats.skipped += len(expected_titles)
            continue

        videos_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", prefix="ntu-cool-yturls-", delete=False, encoding="utf-8"
        ) as tf:
            tf.write("\n".join(urls) + "\n")
            urls_file = Path(tf.name)
        print(f"  [{week.label}/youtube] running yt-dlp for {len(urls)} URL(s) ({len(missing)} missing)")
        cmd = [
            yt_dlp, *YT_DLP_BASE_ARGS,
            "--cookies", str(cookies_path),
            "--newline",
            "-P", str(videos_dir),
            "-o", "%(id)s_%(title).120s.%(ext)s",
            "-a", str(urls_file),
        ]
        try:
            result = subprocess.run(cmd)
        finally:
            urls_file.unlink(missing_ok=True)
        if result.returncode != 0:
            print(f"    yt-dlp exit {result.returncode}")
        rename_downloaded_videos(week_items=week_items, videos_dir=videos_dir)
        # Count how many of the expected titles are now on disk to derive done/failed.
        existing_after = {p.stem for p in videos_dir.glob("*.mp4")}
        for t in expected_titles:
            if t in existing_after and t not in existing:
                stats.done += 1
            elif t not in existing_after:
                stats.failed.append(f"{week.label}/{t}.mp4 (yt-dlp could not download)")
    return stats


def _ensure_logged_in(page, course_id: str | None, sso_timeout_sec: int) -> bool:
    """Navigate to course root (or /courses if no id); if bounced to login, wait for SSO."""
    from playwright.sync_api import TimeoutError as PWTimeout

    target = f"https://{CANVAS_NETLOC}/courses/{course_id}" if course_id else f"https://{CANVAS_NETLOC}/courses"
    print(f"    verifying login at {target}")
    page.goto(target, wait_until="domcontentloaded", timeout=120000)
    if LOGIN_RE.search(page.url):
        print(f"    login required — complete SSO in the browser window (waiting up to {sso_timeout_sec}s)")
        try:
            page.wait_for_url(lambda u: not LOGIN_RE.search(u), timeout=sso_timeout_sec * 1000)
        except PWTimeout:
            print("    SSO timeout")
            return False
    print(f"    logged in: {page.url}")
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
    return True


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
            print(f"      attempt {attempt+1} nav err: {exc}")
            continue
        if LOGIN_RE.search(page.url):
            print(f"      attempt {attempt+1} bounced to login; waiting for SSO")
            try:
                page.wait_for_url(lambda u: not LOGIN_RE.search(u), timeout=sso_timeout_sec * 1000)
            except PWTimeout:
                return None
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            if video_id in captured:
                return captured[video_id]
            page.wait_for_timeout(1500)
        print(f"      attempt {attempt+1}: no view JSON yet")
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


def capture_and_download_cool_videos_in_page(plan: CoursePlan, page, captured: dict[int, dict[str, Any]],
                                              *, course_id: str, sso_timeout_sec: int = 600) -> StageStats:
    """Walk every cool-video target in plan, using the supplied (already-logged-in) page."""
    stats = StageStats()
    targets = _cool_video_targets(plan)
    if not targets:
        return stats
    print(f"  cool-videos: {len(targets)} target(s)")

    for i, (week, item, video_id) in enumerate(targets, 1):
        title = str(item.get("title") or "").strip() or f"item-{item.get('id')}"
        mp4_target = week.week_dir / f"{sanitize_teacher_title(title)}.mp4"
        if mp4_target.exists() and mp4_target.stat().st_size > 0:
            print(f"  [{week.label}/cool-video] [{i}/{len(targets)}] skip (mp4 exists): {mp4_target.name}")
            stats.skipped += 1
            continue
        module_item_url = f"https://{CANVAS_NETLOC}/courses/{course_id}/modules/items/{item['id']}"
        print(f"  [{week.label}/cool-video] [{i}/{len(targets)}] {title}")
        view = _capture_cool_video_in_page(page, captured, module_item_url, video_id, sso_timeout_sec)
        if view is None:
            print(f"      ✗ FAILED to capture view JSON for video {video_id}")
            stats.failed.append(f"{week.label}/{mp4_target.name} (LTI capture failed)")
            continue
        url = view.get("altSourceUri") or view.get("sourceUri")
        if not url:
            print(f"      ✗ view JSON has no source URL")
            stats.failed.append(f"{week.label}/{mp4_target.name} (no source URL)")
            continue
        print(f"      downloading mp4 ({view.get('length')}s) -> {mp4_target.name}")
        try:
            _download_signed_url(url, mp4_target)
            stats.done += 1
        except Exception as exc:
            print(f"        ✗ FAILED: {exc}")
            stats.failed.append(f"{week.label}/{mp4_target.name}: {type(exc).__name__}: {exc}")
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
            captured[v["videoId"]] = v
        except Exception:
            pass
    return _on_response


def capture_and_download_cool_videos(
    plan: CoursePlan, *,
    course_id: str,
    profile_dir: Path = Path(".secrets/ntu_cool_browser_profile"),
    headless: bool = False,
    sso_timeout_sec: int = 600,
) -> StageStats:
    """Standalone entry point: open a Playwright context, log in, capture+download every cool-video."""
    stats = StageStats()
    if not _cool_video_targets(plan):
        return stats
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("    Playwright not installed. Install with: pip install -e \".[browser]\"  &&  python -m playwright install chromium")
        return stats
    profile_dir.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir), headless=headless, accept_downloads=False)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        captured: dict[int, dict[str, Any]] = {}
        page.on("response", make_cool_video_response_handler(captured))
        if not _ensure_logged_in(page, course_id, sso_timeout_sec):
            ctx.close()
            return stats
        stats = capture_and_download_cool_videos_in_page(
            plan, page, captured, course_id=course_id, sso_timeout_sec=sso_timeout_sec)
        ctx.close()
    return stats


# ---- top-level orchestrator ----

def _build_session_client_from_file(headers_path: Path, base_url: str) -> CanvasSessionClient:
    from .session_client import read_headers_file
    return CanvasSessionClient(base_url=base_url, headers=read_headers_file(headers_path))


def _write_course_overview(plan: CoursePlan) -> Path:
    """Write a Markdown index at the course root listing every downloaded artifact per week.
    Designed to be readable by both humans and AI tools."""
    from datetime import datetime, timezone
    course_name = plan.course.get("name") or plan.course.get("course_code") or plan.course_id
    lines: list[str] = [
        f"# {course_name}",
        "",
        f"**Course ID:** {plan.course_id}",
        f"**Generated:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        f"**Path:** `{plan.course_dir}`",
        "",
        "教材總覽 / Materials index. 每週的 PDF / Page / 影片都列在下方並連到本機檔案。",
        "",
    ]
    for week in plan.weeks:
        module_name = week.module.get("name") or week.label
        lines.append(f"## {module_name}")
        lines.append("")
        # Group items by file type for readability
        per_type: dict[str, list[str]] = {"pdf": [], "md": [], "mp4": [], "other": []}
        for item in week.items:
            kind = item.get("type")
            title = str(item.get("title") or "").strip()
            if kind == "File":
                stem = title[:-4] if title.lower().endswith(".pdf") else title
                fname = f"{sanitize_teacher_title(stem)}.pdf"
                if (week.week_dir / fname).exists():
                    per_type["pdf"].append(f"- 📄 [{title}]({week.label}/{urllib.parse.quote(fname)})")
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
            lines.append("- _(no downloadable items)_")
        lines.append("")
    overview = plan.course_dir / "course_overview.md"
    overview.write_text("\n".join(lines), encoding="utf-8")
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
    skip_youtube: bool = False, skip_cool_videos: bool = False,
    sso_timeout_sec: int = 600,
) -> CoursePlan:
    """Top-level orchestrator. Opens at most ONE Playwright context for the entire run.

    Pass `browser` (e.g. from `pick`) to reuse an already-opened logged-in context — no second SSO.
    Otherwise: opens its own context if `refresh_session` or any cool-video items exist.
    """
    owns_browser = False
    if browser is None and refresh_session:
        browser = open_browser_session(
            profile_dir=profile_dir, headless=headless,
            course_id=course_id, sso_timeout_sec=sso_timeout_sec,
        )
        owns_browser = True
        if not _dump_cookies_to_headers_file(browser.context, headers_path):
            browser.close()
            raise RuntimeError("No NTU COOL cookies found in browser context")
        print(f"  refreshed -> {headers_path}")

    if client is None:
        client = _build_session_client_from_file(headers_path, base_url)

    try:
        plan = plan_course(client, course_id, output_dir)
        print(f"Course: {plan.course.get('name')!r}")
        print(f"Output: {plan.course_dir}")
        print(f"Weeks with content: {[w.label for w in plan.weeks]}")

        course_stats = CourseStats()

        def _try_recover_session() -> bool:
            """Refresh SSO + cookies if we have a Playwright session. Returns True on success."""
            nonlocal client
            if browser is None:
                print("  → cannot auto-recover session (no browser available). Re-run with --refresh-session.")
                return False
            print("  → NTU COOL session expired; re-authenticating in the same browser...")
            if not _ensure_logged_in(browser.page, course_id, sso_timeout_sec):
                return False
            if not _dump_cookies_to_headers_file(browser.context, headers_path):
                return False
            client = _build_session_client_from_file(headers_path, base_url)
            print("  ✓ session refreshed, retrying stage")
            return True

        def _run_with_session_retry(stage_fn, label: str) -> StageStats:
            try:
                return stage_fn(client)
            except SessionExpiredError as exc:
                print(f"  ⚠ {label}: {exc}")
                if not _try_recover_session():
                    raise
                return stage_fn(client)

        if not skip_pdfs:
            print("\n[1/4] PDFs / Files")
            course_stats.pdfs = _run_with_session_retry(
                lambda c: download_files(plan, c), "files"
            )
            print(f"  downloaded={course_stats.pdfs.done}, skipped={course_stats.pdfs.skipped}, "
                  f"failed={len(course_stats.pdfs.failed)}")
        if not skip_pages:
            print("\n[2/4] Pages")
            course_stats.pages = _run_with_session_retry(
                lambda c: save_pages(plan, c, course_id), "pages"
            )
            print(f"  saved={course_stats.pages.done}, skipped={course_stats.pages.skipped}, "
                  f"failed={len(course_stats.pages.failed)}")
        if not skip_youtube:
            print("\n[3/4] YouTube videos")
            if yt_cookies is None or not yt_cookies.exists():
                print(f"  WARNING: youtube cookies not found at {yt_cookies} — videos may be unavailable")
            course_stats.youtube = download_youtube(
                plan, cookies_path=yt_cookies or Path(".secrets/youtube_cookies.txt"), yt_dlp=yt_dlp
            )

        if not skip_cool_videos:
            print("\n[4/4] NTU CDN videos (cool-video)")
            if browser is not None:
                course_stats.cool_videos = capture_and_download_cool_videos_in_page(
                    plan, browser.page, browser.captured,
                    course_id=course_id, sso_timeout_sec=sso_timeout_sec,
                )
            else:
                course_stats.cool_videos = capture_and_download_cool_videos(
                    plan, course_id=course_id, profile_dir=profile_dir, headless=headless,
                    sso_timeout_sec=sso_timeout_sec,
                )

        # Per-course overview at the course root.
        try:
            overview_path = _write_course_overview(plan)
            print(f"\n  overview: {overview_path.name}")
        except Exception as exc:
            print(f"\n  (could not write overview: {exc})")

        # Summary
        print("\n" + "=" * 60)
        print("Done.")
        print(f"  PDFs:        {course_stats.pdfs.done} new, "
              f"{course_stats.pdfs.skipped} skipped, "
              f"{len(course_stats.pdfs.failed)} failed")
        print(f"  Pages:       {course_stats.pages.done} new, "
              f"{course_stats.pages.skipped} skipped, "
              f"{len(course_stats.pages.failed)} failed")
        print(f"  YouTube:     {course_stats.youtube.done} new, "
              f"{course_stats.youtube.skipped} skipped, "
              f"{len(course_stats.youtube.failed)} failed")
        print(f"  Cool-video:  {course_stats.cool_videos.done} new, "
              f"{course_stats.cool_videos.skipped} skipped, "
              f"{len(course_stats.cool_videos.failed)} failed")
        all_failures = (course_stats.pdfs.failed + course_stats.pages.failed
                        + course_stats.youtube.failed + course_stats.cool_videos.failed)
        if all_failures:
            print(f"\nFailures ({len(all_failures)}):")
            for f in all_failures:
                print(f"  ✗ {f}")
        print(f"\nFiles saved to:\n  {plan.course_dir.resolve()}")
        return plan
    finally:
        if owns_browser and browser is not None:
            browser.close()
