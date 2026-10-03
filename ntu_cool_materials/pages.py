from __future__ import annotations

import urllib.parse
from typing import Any, Protocol


class _PageClient(Protocol):
    def get_json(self, path_or_url: str, params: list[tuple[str, str]] | None = ...) -> Any: ...


def fetch_page(client: _PageClient, course_id: str, slug: str) -> dict[str, Any]:
    quoted_course = urllib.parse.quote(str(course_id), safe="")
    quoted_slug = urllib.parse.quote(str(slug), safe="")
    data = client.get_json(f"/api/v1/courses/{quoted_course}/pages/{quoted_slug}")
    if not isinstance(data, dict):
        raise ValueError(f"Unexpected page response for {course_id}/{slug}: {type(data).__name__}")
    return data
