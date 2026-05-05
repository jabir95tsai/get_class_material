from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

YOUTUBE_ID_RE = re.compile(
    r"(?:youtu\.be/|youtube\.com/(?:watch\?[^ \n\r\t]+?v=|embed/|shorts/))([A-Za-z0-9_-]{11})"
)
YOUTUBE_V_PARAM_RE = re.compile(r"[?&]v=([A-Za-z0-9_-]{11})")
WINDOWS_UNSAFE_CHARS = '<>:"/\\|?*'
@dataclass(frozen=True)
class RenameResult:
    source: Path
    target: Path
    title: str
    changed: bool


def extract_youtube_ids(value: str | None) -> list[str]:
    if not value:
        return []

    positioned_ids: list[tuple[int, str]] = []
    for pattern in (YOUTUBE_ID_RE, YOUTUBE_V_PARAM_RE):
        positioned_ids.extend((match.start(1), match.group(1)) for match in pattern.finditer(value))

    ids: list[str] = []
    for _position, video_id in sorted(positioned_ids, key=lambda item: item[0]):
        if video_id not in ids:
            ids.append(video_id)
    return ids


def build_video_title_map(week_items: dict[str, Any]) -> dict[str, str]:
    title_by_video_id: dict[str, str] = {}
    duplicate_ids: set[str] = set()

    module = week_items.get("module")
    items = module.get("items", []) if isinstance(module, dict) else []
    for item in items:
        if item.get("type") not in {"ExternalUrl", "ExternalTool"}:
            continue
        title = str(item.get("title") or "").strip()
        raw_url = str(item.get("external_url") or item.get("url") or "")
        for video_id in extract_youtube_ids(raw_url):
            if video_id in title_by_video_id and title_by_video_id[video_id] != title:
                duplicate_ids.add(video_id)
                continue
            title_by_video_id[video_id] = title

    # When Canvas duplicates a YouTube id across multiple item titles, keep the first
    # title because it matches the downloaded YouTube metadata more often.
    for video_id in duplicate_ids:
        title_by_video_id[video_id] = title_by_video_id[video_id]

    return title_by_video_id


def rename_downloaded_videos(
    *,
    week_items_path: Path | None = None,
    week_items: dict[str, Any] | None = None,
    videos_dir: Path,
    dry_run: bool = False,
) -> list[RenameResult]:
    """Rename mp4s in `videos_dir` using titles from a Canvas week_items dump.

    Pass either `week_items` (already-parsed dict like {"module": {...}}) or
    `week_items_path` (file containing the same JSON). Exactly one is required.
    """
    if week_items is None:
        if week_items_path is None:
            raise ValueError("rename_downloaded_videos: pass week_items or week_items_path")
        week_items = json.loads(week_items_path.read_text(encoding="utf-8"))
    title_by_video_id = build_video_title_map(week_items)

    results: list[RenameResult] = []
    for source in sorted(videos_dir.glob("*.mp4")):
        video_id = _youtube_id_from_filename(source.name, title_by_video_id)
        if not video_id or video_id not in title_by_video_id:
            continue

        title = title_by_video_id[video_id]
        target = source.with_name(f"{sanitize_teacher_title(title, max_length=160)}.mp4")
        target = _avoid_collision(source, target)
        changed = source.resolve() != target.resolve()
        if changed and not dry_run:
            source.rename(target)
        results.append(RenameResult(source=source, target=target, title=title, changed=changed))

    return results


def _youtube_id_from_filename(filename: str, title_by_video_id: dict[str, str]) -> str | None:
    for video_id in title_by_video_id:
        if video_id in filename:
            return video_id
    return None


def sanitize_teacher_title(value: str, *, max_length: int = 160) -> str:
    normalized = unicodedata.normalize("NFC", value).strip()
    cleaned = "".join("_" if ord(char) < 32 or char in WINDOWS_UNSAFE_CHARS else char for char in normalized)
    cleaned = " ".join(cleaned.split()).strip(" .")
    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length].rstrip(" .")
    return cleaned or "untitled"


def _avoid_collision(source: Path, target: Path) -> Path:
    if target.resolve() == source.resolve() or not target.exists():
        return target

    stem = target.stem
    suffix = target.suffix
    for index in range(2, 1000):
        candidate = target.with_name(f"{stem} ({index}){suffix}")
        if not candidate.exists() or candidate.resolve() == source.resolve():
            return candidate

    raise FileExistsError(f"Could not find available filename for {target}")
