from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ntu_cool_materials.media_naming import extract_youtube_ids, rename_downloaded_videos


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


def extract_youtube_urls(week_items_path: Path) -> list[str]:
    data = json.loads(week_items_path.read_text(encoding="utf-8"))
    module = data.get("module") or {}
    urls: list[str] = []
    seen: set[str] = set()
    for item in module.get("items", []):
        if item.get("type") not in {"ExternalUrl", "ExternalTool"}:
            continue
        raw = str(item.get("external_url") or item.get("url") or "")
        for video_id in extract_youtube_ids(raw):
            url = f"https://youtu.be/{video_id}"
            if url not in seen:
                seen.add(url)
                urls.append(url)
    return urls


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Download all YouTube videos referenced in a week_items.json at max source quality, then rename to titles."
    )
    parser.add_argument("--week-items", required=True, help="Path to weekN_items.json (Canvas module dump).")
    parser.add_argument("--videos-dir", required=True, help="Output directory for the .mp4 files.")
    parser.add_argument("--cookies", default=".secrets/youtube_cookies.txt", help="Netscape cookies.txt for yt-dlp.")
    parser.add_argument("--yt-dlp", default="yt-dlp", help="yt-dlp executable.")
    parser.add_argument("--no-rename", action="store_true", help="Skip renaming pass after download.")
    parser.add_argument("--dry-run", action="store_true", help="List URLs and skip download.")
    args = parser.parse_args()

    week_items_path = Path(args.week_items)
    videos_dir = Path(args.videos_dir)
    videos_dir.mkdir(parents=True, exist_ok=True)

    urls = extract_youtube_urls(week_items_path)
    if not urls:
        print("No YouTube URLs found in", week_items_path)
        return 0

    urls_file = videos_dir.parent / "youtube_urls.txt"
    urls_file.write_text("\n".join(urls) + "\n", encoding="utf-8")
    print(f"{len(urls)} URL(s) -> {urls_file}")
    for url in urls:
        print(" ", url)

    if args.dry_run:
        return 0

    cmd = [
        args.yt_dlp,
        *YT_DLP_BASE_ARGS,
        "--cookies", args.cookies,
        "-P", str(videos_dir),
        "-o", "%(id)s_%(title).120s.%(ext)s",
        "-a", str(urls_file),
    ]
    print("running:", " ".join(cmd))
    log_path = videos_dir.parent / "yt_dlp_attempts.log"
    with log_path.open("wb") as log:
        completed = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT)
    print(f"yt-dlp exit {completed.returncode}; log -> {log_path}")

    if not args.no_rename:
        results = rename_downloaded_videos(week_items_path=week_items_path, videos_dir=videos_dir)
        for r in results:
            tag = "renamed" if r.changed else "already named"
            print(f"{tag}: {r.source.name} -> {r.target.name}")

    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
