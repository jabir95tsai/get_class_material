# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Python CLI that syncs NTU COOL (Canvas) course materials to a local `materials/` tree for downstream AI study workflows. Targets Python 3.11+. Standard library `urllib` is used for Canvas API calls; `requests` is only used by the CDN video downloader; `playwright` is an optional `[browser]` extra for cookie/session refresh.

## Commands

```powershell
# Install editable + tests
pip install -e .
python -m unittest discover -s tests        # all tests
python -m unittest tests.test_storage       # single module
python -m unittest tests.test_storage.StorageTests.test_sanitize_component_replaces_unsafe_chars

# Optional browser extra (Playwright + Chromium)
pip install -e ".[browser]"
python -m playwright install chromium
```

There is no linter, formatter, or type-checker configured in `pyproject.toml`. Tests are plain `unittest`, not pytest.

Console entry points (declared in `pyproject.toml`):
- `ntu-cool-materials` → `ntu_cool_materials.cli:main` (subcommands: `courses`, `sync`, `announcements`, `download-course`, `pick`)
- `ntu-cool-gcm`       → `ntu_cool_materials.cli:pick_main` (shortcut alias for `ntu-cool-materials pick`)
- `ntu-cool-video`     → `cool_video:main` (DASH `.m4s` segment downloader + ffmpeg mux)
- `ntu-cool-session`   → `browser_session:main` (Playwright session refresh)
- `youtube-cookies`    → `youtube_cookies:main` (export YouTube cookies for yt-dlp)

`download-course` is the bulk per-course pipeline (one command does the whole course):
```powershell
ntu-cool-materials download-course --course-id 60804 --refresh-session
# subsequent runs are idempotent; --refresh-session only needed if Canvas session expired
ntu-cool-materials download-course --course-id 60804
# stages can be skipped:
ntu-cool-materials download-course --course-id 60804 --skip-cool-videos --skip-youtube
```

`pick` (or shortcut `ntu-cool-gcm`) is the interactive entry point — lists active courses, prompts the user, then runs `download-course` on the choice. Critically, **the picker passes its open `BrowserSession` through to `download_course` via the `browser=` kwarg**, so a single SSO covers the whole pipeline (refresh + cool-videos in one Chromium context).

On Windows, run with `python -X utf8 -m ntu_cool_materials download-course ...` if the console mojibakes CJK course/file names.

Auth — set one of:
- `NTU_COOL_TOKEN` env var (Canvas access token), or
- `--headers-file .secrets/ntu_cool_headers.txt` (raw DevTools headers, used when token issuance is disabled).

## Architecture

The package is built around **two parallel Canvas API clients** that the CLI picks between based on which auth method the user provides. Most other modules consume either client via duck-typed methods (`list_courses`, `list_course_announcements`, `list_paginated`):

- `canvas_client.CanvasClient` — Bearer-token auth. Sends `Authorization: Bearer <token>` only to the configured Canvas origin (see `_is_canvas_origin`); strips it on cross-origin file-download redirects (uses `NoRedirectHandler` + manual redirect loop) so signed CDN URLs don't leak the token.
- `session_client.CanvasSessionClient` — cookie/header auth. Reads a DevTools "Copy request headers" blob via `read_headers_file` (also accepts JSON), drops hop-by-hop headers, and reuses them. The token-equivalent secret is the `cookie` header in `.secrets/ntu_cool_headers.txt`; `redact_headers` exists specifically to keep it out of logs.

Both implement `list_paginated` by walking RFC 5988 `Link: rel="next"` headers (`parse_link_header`), with a 50ms sleep between pages.

`cli.py` is the orchestrator. `_courses_client` / `_announcements_client` resolve auth: if `--refresh-session` is set, they invoke `browser_session.refresh_headers_file` first to write a fresh `.secrets/ntu_cool_headers.txt`, then load it. The `sync` subcommand currently only supports token auth.

**Idempotent file sync** lives in `sync.py` + `storage.py`:
- `sync.sync_course_materials` writes `materials/<course-name> (<id>)/modules.json` (modules + items, calling `list_module_items` per module if items aren't inlined), then iterates `list_course_files` and downloads each through `client.download_file_url`.
- `storage.ManifestStore` is a SQLite (`materials/.ntu_cool_materials.sqlite3`) keyed by Canvas `file_id`. `needs_download` re-fetches when `updated_at` or `size` differs from the manifest, OR the local file is missing. After a successful download, `upsert_file` records `sha256` of the bytes on disk.
- `storage.sanitize_component` is the single source of truth for filename safety: NFKC-normalize, replace `<>:"/\|?*` and control chars with `_`, prefix Windows reserved names (`CON`, `AUX`, …) with `_`, and clip to a max length. Used for both course directory names and per-file names. Don't reinvent this — call it.
- `storage.course_directory_name(course)` is the single source of truth for the per-course directory name: `<course-name> (<id>)` after sanitization, max 160 chars. Used by `sync.sync_course_materials` and `announcements.write_announcements`. If you add a new subcommand that writes per-course, use this — never hardcode `course_<id>`.

**Browser-session bridge** (`browser_session.py`) launches a Playwright persistent context at `.secrets/ntu_cool_browser_profile/`, navigates to a course page (or `/courses` if no course id), waits out an SSO/SAML login if needed (detected via `_looks_like_login`), probes the API once to confirm cookies work, then writes `cool.ntu.edu.tw` cookies into the headers file. With `--headless-refresh` it returns exit code 2 instead of opening a window when the session is gone.

**Announcements** (`announcements.py`) — `html_to_text` is a tiny `HTMLParser` subclass that inserts newlines around block tags; `announcement_markdown` produces a per-item section, and `write_announcements(out, course, announcements)` saves both `announcements.json` and `announcements.md` under `materials/<course_directory_name>/announcements/`.

**Video pipeline** is independent from the Canvas sync:
- `cool_video.py` (`ntu-cool-video`) downloads NTU COOL CDN MPEG-DASH segments. Walks `video-{quality}-{n}.m4s` and `audio-{quality}-{n}.m4s` from `--start` until a 404, merges them in order, then `ffmpeg -c copy` mux to MP4. `normalize_base_url` accepts either a directory URL or any numbered segment URL (it strips back to the parent). If the first numbered segment lacks an `ftyp/moov` box (`probe_top_level_mp4_boxes`), the user must supply an explicit init segment via `--video-init-url` / `--audio-init-url` (or `*-init-file`); the ffmpeg error path explicitly hints at this.
- `media_naming.py` (`scripts/rename_week_videos.py`) post-processes a directory of downloaded videos by matching the YouTube id in each filename against a `week_items.json` Canvas modules export and renaming to the human-readable item title (`sanitize_teacher_title` preserves CJK + fullwidth punctuation, unlike `storage.sanitize_component` which is more aggressive about whitespace collapsing). `extract_youtube_ids` is intentionally robust to malformed concatenated Canvas URLs.
- `scripts/download_week_youtube.py` reads a `weekN_items.json`, pulls every `ExternalUrl`/`ExternalTool` YouTube id out, runs yt-dlp at max source quality, then invokes the rename pass. **Critical yt-dlp flags** (without these, yt-dlp picks legacy format 18 = 360p combined or fails with "video unavailable"/"DRM protected"): `--js-runtimes node --extractor-args "youtube:player_client=default,web,web_safari" -f "bv*[ext=mp4]+ba[ext=m4a]/bv*+ba/b" --cookies .secrets/youtube_cookies.txt`. The `web` client is what unlocks DASH formats above 360p; the JS runtime (node, since deno isn't installed here) is needed to solve YouTube's n-challenge. Source uploads in this course cap at 480p — that's a YouTube-side limit, not ours. **The same flags are baked into `course_pipeline.YT_DLP_BASE_ARGS`** for the bulk command.

**Bulk per-course pipeline** (`course_pipeline.py`, exposed as `download-course` subcommand) consolidates the four standalone scripts. The orchestration is ordered so each stage is idempotent (skips items already present on disk):
1. `plan_course` fetches modules, writes `<course-dir>/modules_raw.json` + per-week `metadata/<weekLabel>_items.json`. Week label comes from regex on module name (`Week\s*(\d+)`), so this works for any course that uses "Week 1 - 2/27" / "Week3 - 3/9" / etc. style names. Falls back to `module<index>` if no number found. Modules with no downloadable items are skipped.
2. `download_files` — Files-type items via session-cookie download with cross-origin redirect handling (mirror of `CanvasClient.download_file_url`).
3. `save_pages` — Pages via `/api/v1/courses/:id/pages/:url`, saved as `<safe_title>.json` + `<safe_title>.md` (HTML → markdown via `html_to_text`).
4. `download_youtube` — yt-dlp subprocess per week with rename pass. **Cookie strategy** (`_youtube_cookie_args`): try public/unlisted first with no auth (the common case — zero prompts). If videos fail AND no `youtube_cookies.txt` exists, `maybe_retry_youtube_with_login` asks once for consent, then the stage loops `_installed_cookie_browsers()` re-running yt-dlp with `--cookies-from-browser <name>` until nothing's failing. We read cookies from the user's real browser (not a Playwright login) because Google blocks sign-in on automation-controlled browsers. There is intentionally **no upfront cookie prompt** in `cli._cmd_pick`. A pre-existing `youtube_cookies.txt` (e.g. from the standalone `youtube-cookies` Playwright exporter) still wins via `--cookies`. Caveat: Chrome/Edge lock their cookie DB while running, so the retry warns the user to close the browser.
5. `capture_and_download_cool_videos` — **single Playwright context** for the whole course. NTU SAML cookies on `cool.ntu.edu.tw` are session-only (die when Chromium exits), so refresh-then-capture across separate processes loses login. The function visits the course root first, waits up to 10 min for SSO if needed, then walks every cool-video LTI module item and downloads `altSourceUri` (`transcoded.mp4`, single signed S3 URL — way simpler than the DASH segment walker in `cool_video.py`).

The four standalone scripts under `scripts/` (`prepare_week_assets.py`, `capture_cool_video_views.py`, `download_cool_videos_from_views.py`, `download_week_youtube.py`) are now superseded by `download-course` but kept for finer-grained / debugging use.
- `youtube_cookies.py` (`youtube-cookies`) is unrelated to NTU COOL — it's a Playwright helper that exports a Netscape `cookies.txt` from a dedicated browser profile so yt-dlp can hit member-only / age-gated YouTube videos.

## Conventions worth preserving

- **Secrets stay in a `.secrets/` dir** (git-ignored). `materials/`, `ntu-cool-gcm_material/`, `*.sqlite3`, `*.m4s`, `*.part`, `.env` are also ignored. Never write cookies, tokens, or downloaded course files outside these paths. **Location resolution** lives in `cli._secrets_dir()`: it uses `./.secrets` when that dir already exists (repo devs, existing users — the default PowerShell start dir is `~`, so most users already have `~/.secrets`), otherwise `~/.ntu-cool-gcm/.secrets`. Anchoring to home prevents the `WinError 5` crash when launched from a non-writable CWD like `C:\WINDOWS\system32` (elevated-PowerShell default). All subcommand `--headers-file` / `--profile-dir` / `--youtube-cookies` defaults are built from this in `_build_parser`; the YouTube browser profile is derived from the cookies file's parent. When adding new secret paths, route them through `_secrets_dir()` — never hardcode `.secrets/...`.
- **Default output dir is `ntu-cool-gcm_material/`** (relative to CWD) for the user-facing `pick` and `download-course` subcommands. Older subcommands (`announcements`, `sync`) still default to `materials/` for backwards compat. Both are gitignored.
- **Cross-origin token hygiene**: when adding new download paths, follow the `CanvasClient.download_file_url` pattern — only attach `Authorization` when `_is_canvas_origin(url)` is true, since Canvas redirects to signed S3/CDN URLs that already carry their own auth.
- **All Canvas list endpoints paginate**; route new ones through `list_paginated`, not bare `urlopen`.
- Use `urllib.parse.quote(..., safe="")` for path components built from Canvas ids (see `canvas_client.list_course_files`).
- The README is bilingual (Traditional Chinese + English code blocks). Keep that style if extending it.
