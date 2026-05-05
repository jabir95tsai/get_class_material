from __future__ import annotations

import json
import urllib.parse
from pathlib import Path
from typing import Any, Protocol

from .announcements import html_to_text
from .media_naming import sanitize_teacher_title


class _PageClient(Protocol):
    def get_json(self, path_or_url: str, params: list[tuple[str, str]] | None = ...) -> Any: ...


def fetch_page(client: _PageClient, course_id: str, slug: str) -> dict[str, Any]:
    quoted_course = urllib.parse.quote(str(course_id), safe="")
    quoted_slug = urllib.parse.quote(str(slug), safe="")
    data = client.get_json(f"/api/v1/courses/{quoted_course}/pages/{quoted_slug}")
    if not isinstance(data, dict):
        raise ValueError(f"Unexpected page response for {course_id}/{slug}: {type(data).__name__}")
    return data


def page_markdown(page: dict[str, Any]) -> str:
    title = page.get("title") or page.get("url") or "(untitled)"
    body = html_to_text(page.get("body"))
    parts = [f"# {title}"]
    if body:
        parts.append("")
        parts.append(body)
    return "\n".join(parts).strip() + "\n"


def write_page(out_dir: Path, page: dict[str, Any]) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = sanitize_teacher_title(
        (page.get("title") or page.get("url") or "page").replace(":", ""),
        max_length=160,
    )
    json_path = out_dir / f"{stem}.json"
    md_path = out_dir / f"{stem}.md"
    json_path.write_text(json.dumps(page, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(page_markdown(page), encoding="utf-8")
    return json_path, md_path
