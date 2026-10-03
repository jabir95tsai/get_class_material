from __future__ import annotations

import re
import unicodedata
from typing import Any

from .storage import WINDOWS_RESERVED_NAMES


YOUTUBE_ID_RE = re.compile(
    r"(?:youtu\.be/|youtube\.com/(?:watch\?[^ \n\r\t]+?v=|embed/|shorts/))([A-Za-z0-9_-]{11})"
)
YOUTUBE_V_PARAM_RE = re.compile(r"[?&]v=([A-Za-z0-9_-]{11})")
WINDOWS_UNSAFE_CHARS = '<>:"/\\|?*'


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
    # When Canvas reuses a YouTube id under several item titles, keep the first
    # title because it matches the downloaded YouTube metadata more often.
    title_by_video_id: dict[str, str] = {}

    module = week_items.get("module")
    items = module.get("items", []) if isinstance(module, dict) else []
    for item in items:
        if item.get("type") not in {"ExternalUrl", "ExternalTool"}:
            continue
        title = str(item.get("title") or "").strip()
        raw_url = str(item.get("external_url") or item.get("url") or "")
        for video_id in extract_youtube_ids(raw_url):
            title_by_video_id.setdefault(video_id, title)
    return title_by_video_id


def sanitize_teacher_title(value: str, *, max_length: int = 160) -> str:
    normalized = unicodedata.normalize("NFC", value).strip()
    cleaned = "".join("_" if ord(char) < 32 or char in WINDOWS_UNSAFE_CHARS else char for char in normalized)
    cleaned = " ".join(cleaned.split()).strip(" .")
    if cleaned.split(".", 1)[0].upper() in WINDOWS_RESERVED_NAMES:
        cleaned = "_" + cleaned
    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length].rstrip(" .")
    return cleaned or "untitled"
