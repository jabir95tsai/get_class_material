"""For every cool_video_*_view.json under <week>/metadata/, download the transcoded.mp4
(altSourceUri) into <week>/videos/<title-prefix> <CDN-title>.mp4. Skips if file exists."""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ntu_cool_materials.media_naming import sanitize_teacher_title


def title_for_video_id(week_items_path: Path, video_id: int) -> str | None:
    data = json.loads(week_items_path.read_text(encoding="utf-8"))
    for item in (data.get("module") or {}).get("items", []):
        if item.get("type") != "ExternalTool":
            continue
        m = re.search(r"/videos/(\d+)", str(item.get("external_url") or ""))
        if m and int(m.group(1)) == video_id:
            return str(item.get("title") or "").strip()
    return None


def download(url: str, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "ntu-cool-materials/0.1"})
    with urllib.request.urlopen(req, timeout=120) as resp, tmp.open("wb") as f:
        while True:
            chunk = resp.read(1024 * 1024)
            if not chunk:
                break
            f.write(chunk)
    tmp.replace(target)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--week-dir", action="append", required=True)
    args = parser.parse_args()

    total_done = 0
    total_skipped = 0
    total_failed = 0
    for wd in args.week_dir:
        week_dir = Path(wd)
        items_files = list((week_dir / "metadata").glob("*_items.json"))
        if not items_files:
            print(f"  [{week_dir.name}] no _items.json")
            continue
        items_path = items_files[0]
        for view_path in sorted((week_dir / "metadata").glob("cool_video_*_view.json")):
            view = json.loads(view_path.read_text(encoding="utf-8"))
            video_id = int(view["videoId"])
            url = view.get("altSourceUri") or view.get("sourceUri")
            if not url:
                print(f"  [{week_dir.name}] {view_path.name}: no source URL")
                total_failed += 1
                continue
            title = title_for_video_id(items_path, video_id) or f"video-{video_id}"
            target = week_dir / "videos" / f"{sanitize_teacher_title(title)}.mp4"
            if target.exists() and target.stat().st_size > 0:
                print(f"  [{week_dir.name}] skip (exists): {target.name}")
                total_skipped += 1
                continue
            print(f"  [{week_dir.name}] downloading {target.name} ({view.get('length')}s)")
            try:
                download(url, target)
                size_mb = target.stat().st_size / 1024 / 1024
                print(f"      done: {size_mb:.1f} MB")
                total_done += 1
            except Exception as exc:
                print(f"      FAILED: {exc}")
                total_failed += 1

    print(f"\nsummary: downloaded={total_done}, skipped={total_skipped}, failed={total_failed}")
    return 1 if total_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
