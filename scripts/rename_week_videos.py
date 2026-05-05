from __future__ import annotations

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ntu_cool_materials.media_naming import rename_downloaded_videos


def main() -> int:
    parser = argparse.ArgumentParser(description="Rename downloaded week videos using Canvas module item titles.")
    parser.add_argument("--week-items", required=True, help="Path to week_items.json.")
    parser.add_argument("--videos-dir", required=True, help="Directory containing downloaded videos.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    results = rename_downloaded_videos(
        week_items_path=Path(args.week_items),
        videos_dir=Path(args.videos_dir),
        dry_run=args.dry_run,
    )
    for result in results:
        action = "would rename" if args.dry_run else "renamed"
        if not result.changed:
            action = "already named"
        print(f"{action}: {result.source.name} -> {result.target.name}")
    print(f"processed {len(results)} video(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
