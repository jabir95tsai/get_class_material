from __future__ import annotations

import html
import json
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from .storage import course_directory_name


class HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"p", "div", "li", "tr", "h1", "h2", "h3", "h4"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if data:
            self.parts.append(data)

    def text(self) -> str:
        lines = []
        for line in "".join(self.parts).splitlines():
            collapsed = " ".join(line.split())
            if collapsed:
                lines.append(collapsed)
        return "\n".join(lines).strip()


def html_to_text(value: str | None) -> str:
    if not value:
        return ""

    parser = HTMLTextExtractor()
    parser.feed(value)
    parser.close()
    return html.unescape(parser.text())


def announcement_markdown(announcement: dict[str, Any]) -> str:
    title = announcement.get("title") or "(untitled)"
    posted_at = announcement.get("posted_at") or announcement.get("created_at") or ""
    author = _author_name(announcement)
    message = html_to_text(announcement.get("message"))

    parts = [f"## {title}"]
    if posted_at:
        parts.append(f"- Posted at: {posted_at}")
    if author:
        parts.append(f"- Author: {author}")
    if message:
        parts.append("")
        parts.append(message)

    return "\n".join(parts).strip()


def write_announcements(
    output_dir: Path,
    course: dict[str, Any],
    announcements: list[dict[str, Any]],
) -> tuple[Path, Path]:
    target_dir = output_dir / course_directory_name(course) / "announcements"
    target_dir.mkdir(parents=True, exist_ok=True)

    json_path = target_dir / "announcements.json"
    markdown_path = target_dir / "announcements.md"
    json_path.write_text(json.dumps(announcements, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(
        "\n\n".join(announcement_markdown(item) for item in announcements),
        encoding="utf-8",
    )
    return json_path, markdown_path


def _author_name(announcement: dict[str, Any]) -> str:
    author = announcement.get("author")
    if isinstance(author, dict):
        return str(author.get("display_name") or author.get("name") or "")
    return ""
