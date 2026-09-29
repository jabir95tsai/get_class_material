from __future__ import annotations

import html
import json
import re
import urllib.parse
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from .storage import atomic_write_text, course_directory_name


class MarkdownExtractor(HTMLParser):
    def __init__(self, base_url=""):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.parts = []
        self.links = []
        self.hidden = 0
        self.file_links = {}

    def _url(self, value):
        url = urllib.parse.urljoin(self.base_url, value or "")
        return url if urllib.parse.urlsplit(url).scheme in {"https", "http"} else ""

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in {"script", "style"}:
            self.hidden += 1
        if self.hidden:
            return
        url = self._url(attrs.get("href") if tag == "a" else attrs.get("src")) if tag in {"a", "img"} else ""
        if url and urllib.parse.urlsplit(url).netloc == urllib.parse.urlsplit(self.base_url).netloc:
            match = re.search(r"/files/(\d+)(?:/|$)", urllib.parse.urlsplit(url).path)
            if match:
                self.file_links[match.group(1)] = attrs.get("title") or attrs.get("alt") or f"file-{match.group(1)}"
        if tag == "a":
            self.links.append(url)
            if url:
                self.parts.append("[")
        elif tag == "img" and url:
            self.parts.append(f"![{attrs.get('alt', 'image')}](<{url}>)")
        elif tag in {"br", "p", "div", "tr", "h1", "h2", "h3", "h4"}:
            self.parts.append("\n")
        elif tag == "li":
            self.parts.append("\n- ")
        elif tag in {"td", "th"}:
            self.parts.append(" | ")

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)
            return
        if self.hidden:
            return
        if tag == "a" and self.links:
            url = self.links.pop()
            if url:
                self.parts.append(f"](<{url}>)")
        elif tag in {"p", "div", "li", "tr", "h1", "h2", "h3", "h4"}:
            self.parts.append("\n")

    def handle_data(self, value):
        if not self.hidden:
            self.parts.append(value)


def html_to_markdown(value: str | None, *, base_url="") -> str:
    parser = MarkdownExtractor(base_url)
    parser.feed(value or "")
    parser.close()
    return "\n".join(line.strip() for line in "".join(parser.parts).splitlines() if line.strip())


def canvas_file_links(value: str | None, base_url: str) -> dict[str, str]:
    parser = MarkdownExtractor(base_url)
    parser.feed(value or "")
    parser.close()
    return parser.file_links


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
    message = html_to_markdown(announcement.get("message"), base_url=announcement.get("html_url") or "https://cool.ntu.edu.tw/")

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
    atomic_write_text(json_path, json.dumps(announcements, ensure_ascii=False, indent=2))
    atomic_write_text(markdown_path,
        "\n\n".join(announcement_markdown(item) for item in announcements),
    )
    return json_path, markdown_path


def _author_name(announcement: dict[str, Any]) -> str:
    author = announcement.get("author")
    if isinstance(author, dict):
        return str(author.get("display_name") or author.get("name") or "")
    return ""
