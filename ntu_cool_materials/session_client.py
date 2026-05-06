from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .canvas_client import CanvasAPIError, parse_link_header


SENSITIVE_HEADER_NAMES = {"cookie", "authorization", "x-csrf-token"}
DROP_REQUEST_HEADER_NAMES = {
    "accept-encoding",
    "connection",
    "content-length",
    "host",
}


def read_headers_file(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"Headers file is empty: {path}")

    if text.startswith("{"):
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("JSON headers file must contain an object.")
        return {str(key): str(value) for key, value in data.items()}

    headers: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(":"):
            continue
        name, separator, value = line.partition(":")
        if not separator:
            continue
        headers[name.strip()] = value.strip()

    if not headers:
        raise ValueError(f"No headers found in {path}")
    return headers


def redact_headers(headers: dict[str, str]) -> dict[str, str]:
    redacted: dict[str, str] = {}
    for name, value in headers.items():
        if name.lower() in SENSITIVE_HEADER_NAMES:
            redacted[name] = "<redacted>"
        else:
            redacted[name] = value
    return redacted


@dataclass
class CanvasSessionClient:
    base_url: str
    headers: dict[str, str]
    timeout: float = 30.0

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/")
        parsed = urllib.parse.urlparse(self.base_url)
        if parsed.scheme != "https":
            raise ValueError("Canvas base URL must use HTTPS.")
        self._base_netloc = parsed.netloc.lower()

    def list_courses(self, enrollment_state: str = "active") -> list[dict[str, Any]]:
        params: list[tuple[str, str]] = [
            ("per_page", "100"),
            ("include[]", "term"),
        ]
        if enrollment_state:
            params.append(("enrollment_state", enrollment_state))

        return list(self.list_paginated("/api/v1/courses", params=params))

    def get_course(self, course_id: str) -> dict[str, Any]:
        quoted_course_id = urllib.parse.quote(str(course_id), safe="")
        data, _headers = self._request_json(
            self._build_url(f"/api/v1/courses/{quoted_course_id}", params=[("include[]", "term")])
        )
        if not isinstance(data, dict):
            raise CanvasAPIError(f"Expected an object response for course {course_id}.")
        return data

    def list_course_announcements(self, course_id: str) -> list[dict[str, Any]]:
        quoted_course_id = urllib.parse.quote(str(course_id), safe="")
        return list(
            self.list_paginated(
                f"/api/v1/courses/{quoted_course_id}/discussion_topics",
                params=[
                    ("per_page", "100"),
                    ("only_announcements", "true"),
                    ("order_by", "recent_activity"),
                ],
            )
        )

    def list_announcements(
        self,
        course_id: str,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
        active_only: bool = True,
    ) -> list[dict[str, Any]]:
        params: list[tuple[str, str]] = [
            ("per_page", "100"),
            ("context_codes[]", f"course_{course_id}"),
            ("active_only", "true" if active_only else "false"),
        ]
        if start_date:
            params.append(("start_date", start_date))
        if end_date:
            params.append(("end_date", end_date))

        return list(self.list_paginated("/api/v1/announcements", params=params))

    def get_json(
        self,
        path_or_url: str,
        params: Sequence[tuple[str, str]] | None = None,
    ) -> list[Any] | dict[str, Any]:
        data, _headers = self._request_json(self._build_url(path_or_url, params=params))
        return data

    def list_paginated(
        self,
        path_or_url: str,
        params: Sequence[tuple[str, str]] | None = None,
    ) -> Iterator[dict[str, Any]]:
        next_url: str | None = self._build_url(path_or_url, params=params)

        while next_url:
            data, headers = self._request_json(next_url)
            if not isinstance(data, list):
                raise CanvasAPIError(f"Expected a list response from {next_url!r}.")

            for item in data:
                if isinstance(item, dict):
                    yield item

            next_url = parse_link_header(headers.get("Link")).get("next")
            if next_url:
                time.sleep(0.05)

    def _request_json(self, url: str) -> tuple[list[Any] | dict[str, Any], urllib.request._headers]:  # type: ignore[name-defined]
        request = urllib.request.Request(url, headers=self._request_headers(url))
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                body = response.read().decode(charset)
                return json.loads(body), response.headers
        except urllib.error.HTTPError as exc:
            raise self._api_error(exc, url) from exc
        except json.JSONDecodeError as exc:
            raise CanvasAPIError(f"Canvas returned invalid JSON from {url}") from exc

    def _request_headers(self, url: str) -> dict[str, str]:
        if not self._is_canvas_origin(url):
            return {}

        headers = {
            name: value
            for name, value in self.headers.items()
            if name.lower() not in DROP_REQUEST_HEADER_NAMES
        }
        headers["Accept"] = "application/json, text/plain, */*"
        headers.setdefault("User-Agent", "ntu-cool-materials/0.1")
        return headers

    def _build_url(
        self,
        path_or_url: str,
        params: Sequence[tuple[str, str]] | None = None,
    ) -> str:
        parsed = urllib.parse.urlparse(path_or_url)
        if parsed.scheme:
            url = path_or_url
        else:
            url = f"{self.base_url}/{path_or_url.lstrip('/')}"

        if params:
            separator = "&" if urllib.parse.urlparse(url).query else "?"
            url = f"{url}{separator}{urllib.parse.urlencode(params)}"
        return url

    def _is_canvas_origin(self, url: str) -> bool:
        parsed = urllib.parse.urlparse(url)
        return parsed.scheme == "https" and parsed.netloc.lower() == self._base_netloc

    @staticmethod
    def _api_error(exc: urllib.error.HTTPError, url: str) -> CanvasAPIError:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace").strip()
        except Exception:
            detail = ""

        message = f"Canvas session request failed with HTTP {exc.code} for {url}"
        if detail:
            message = f"{message}: {detail[:500]}"
        return CanvasAPIError(message, status_code=exc.code)
