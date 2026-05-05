from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ntu_cool_materials.canvas_client import CanvasAPIError
from ntu_cool_materials.pages import fetch_page, write_page
from ntu_cool_materials.session_client import CanvasSessionClient, read_headers_file


WEEK_PREFIX_RE = re.compile(r"^Week\s*(\d+)\b", re.IGNORECASE)


def _parse_weeks(spec: str) -> set[int]:
    weeks: set[int] = set()
    for piece in spec.split(","):
        piece = piece.strip()
        if not piece:
            continue
        if "-" in piece:
            lo, hi = piece.split("-", 1)
            weeks.update(range(int(lo), int(hi) + 1))
        else:
            weeks.add(int(piece))
    return weeks


def _module_week(name: str) -> int | None:
    match = WEEK_PREFIX_RE.match(name or "")
    return int(match.group(1)) if match else None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Download Canvas Page items for a range of weeks based on modules_raw.json."
    )
    parser.add_argument("--course-id", required=True)
    parser.add_argument("--modules", required=True, help="Path to modules_raw.json.")
    parser.add_argument("--course-dir", required=True, help="Course root, e.g. materials/course_57544.")
    parser.add_argument("--weeks", required=True, help="Week numbers, e.g. '3-11' or '4,6,8'.")
    parser.add_argument(
        "--headers-file",
        default=".secrets/ntu_cool_headers.txt",
        help="DevTools-style request headers file.",
    )
    parser.add_argument("--base-url", default="https://cool.ntu.edu.tw")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    weeks = _parse_weeks(args.weeks)
    modules = json.loads(Path(args.modules).read_text(encoding="utf-8"))
    targets: list[tuple[int, str, str]] = []
    for module in modules:
        week = _module_week(str(module.get("name") or ""))
        if week is None or week not in weeks:
            continue
        for item in module.get("items") or []:
            if item.get("type") != "Page":
                continue
            slug = item.get("page_url") or item.get("url")
            title = item.get("title") or slug
            if slug:
                targets.append((week, slug, title))

    if not targets:
        print(f"No Page items found in weeks {sorted(weeks)}.")
        return 0

    print(f"Found {len(targets)} page(s) across weeks {sorted({w for w, *_ in targets})}.")
    for week, slug, title in targets:
        print(f"  week{week}: {slug} ({title})")

    if args.dry_run:
        return 0

    headers = read_headers_file(Path(args.headers_file))
    client = CanvasSessionClient(base_url=args.base_url, headers=headers)
    course_dir = Path(args.course_dir)

    failures = 0
    for week, slug, title in targets:
        out_dir = course_dir / f"week{week}" / "pages"
        try:
            page = fetch_page(client, args.course_id, slug)
            json_path, md_path = write_page(out_dir, page)
            print(f"week{week}: saved {md_path.name}")
        except (CanvasAPIError, OSError, ValueError) as exc:
            failures += 1
            print(f"week{week}: failed {slug}: {exc}")
        time.sleep(0.1)

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
