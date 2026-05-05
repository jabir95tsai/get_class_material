"""Walk through every ExternalTool cool-video item across multiple week_items.json,
opens each module item via Playwright (reusing the persistent NTU profile so SAML works),
captures the /api/courses/{course_id}/videos/{view_id}/view JSON, and saves it under
each week's metadata/ directory.

Usage:
  python scripts/capture_cool_video_views.py --course-id 57544 \
      --week materials/course_57544/week4/metadata/week4_items.json \
      --week materials/course_57544/week5/metadata/week5_items.json ...
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


COOL_VIDEO_VIEW_URL_RE = re.compile(r"/api/courses/(\d+)/videos/(\d+)/view$")


def collect_targets(week_items_paths: list[Path]) -> list[dict[str, Any]]:
    targets: list[dict[str, Any]] = []
    for path in week_items_paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        items = (data.get("module") or {}).get("items", [])
        week_dir = path.parent.parent  # week_items.json sits in metadata/
        for item in items:
            if item.get("type") != "ExternalTool":
                continue
            ext = str(item.get("external_url") or "")
            if "cool-video.dlc.ntu.edu.tw" not in ext:
                continue
            targets.append({
                "title": item.get("title"),
                "module_item_id": item.get("id"),
                "external_url": ext,
                "week_dir": week_dir,
            })
    return targets


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--course-id", required=True)
    parser.add_argument("--week", action="append", required=True, dest="weeks")
    parser.add_argument("--profile-dir", default=".secrets/ntu_cool_browser_profile")
    parser.add_argument("--headless", action="store_true")
    args = parser.parse_args()

    targets = collect_targets([Path(w) for w in args.weeks])
    if not targets:
        print("no cool-video items found")
        return 0
    print(f"capturing {len(targets)} cool-video items...")

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=args.profile_dir,
            headless=args.headless,
            accept_downloads=False,
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        captured_view_per_target: dict[int, dict[str, Any]] = {}

        def on_response(resp):
            url = resp.url
            m = COOL_VIDEO_VIEW_URL_RE.search(url.split("?", 1)[0])
            if not m:
                return
            try:
                ct = resp.headers.get("content-type", "")
                if "json" not in ct:
                    return
                body = resp.text()
                view_json = json.loads(body)
            except Exception as exc:
                print(f"  failed to read view body: {exc}")
                return
            video_id = view_json.get("videoId")
            captured_view_per_target[video_id] = view_json

        page.on("response", on_response)

        for i, t in enumerate(targets, 1):
            module_item_url = f"https://cool.ntu.edu.tw/courses/{args.course_id}/modules/items/{t['module_item_id']}"
            print(f"[{i}/{len(targets)}] {t['title']!r} -> {module_item_url}")

            # Find which captured view's videoId matches the LTI URL videoId from external_url
            m = re.search(r"/videos/(\d+)", t["external_url"])
            launch_video_id = int(m.group(1)) if m else None

            view = None
            for attempt in range(3):
                captured_view_per_target.clear()
                try:
                    page.goto(module_item_url, wait_until="domcontentloaded", timeout=90000)
                    page.wait_for_timeout(4500 if attempt == 0 else 7000)
                except Exception as exc:
                    print(f"  attempt {attempt+1} navigation error: {exc}")
                    continue
                if launch_video_id and launch_video_id in captured_view_per_target:
                    view = captured_view_per_target[launch_video_id]
                    break
                if captured_view_per_target:
                    view = next(iter(captured_view_per_target.values()))
                    break
                print(f"  attempt {attempt+1}: no view JSON captured")

            if view is None:
                print(f"  GAVE UP for video {launch_video_id}")
                continue

            # Title prefix: leading run of word-chars/dashes (e.g. "4-1-1", "0424_part1")
            title = str(t["title"] or "")
            prefix_m = re.match(r"^\s*([\w\-]+)", title)
            prefix = prefix_m.group(1).rstrip("-") if prefix_m else f"item-{t['module_item_id']}"
            metadata_dir = t["week_dir"] / "metadata"
            metadata_dir.mkdir(parents=True, exist_ok=True)
            target_path = metadata_dir / f"cool_video_{prefix}_view.json"
            target_path.write_text(json.dumps(view, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"  saved {target_path}  ({view.get('length')}s, videoId={view.get('videoId')})")

        ctx.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
