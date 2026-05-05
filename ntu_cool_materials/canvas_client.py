from __future__ import annotations

import json
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any


JSON = dict[str, Any] | list[Any]


class CanvasAPIError(RuntimeError):
    """Raised when Canvas returns an error response."""


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def parse_link_header(value: str | None) -> dict[str, str]:
    if not value:
        return {}

    links: dict[str, str] = {}
    for part in value.split(","):
        sections = [section.strip() for section in part.split(";")]
        if not sections or not sections[0].startswith("<") or not sections[0].endswith(">"):
            continue

        url = sections[0][1:-1]
        rel = None
        for section in sections[1:]:
            key, _, raw_value = section.partition("=")
            if key.strip().lower() == "rel":
                rel = raw_value.strip().strip('"')
                break

        if rel:
            links[rel] = url

    return links


@dataclass
class CanvasClient:
    base_url: str
    token: str
    timeout: float = 30.0
    user_agent: str = "ntu-cool-materials/0.1"

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/")
        parsed = urllib.parse.urlparse(self.base_url)
        if parsed.scheme != "https":
            raise ValueError("Canvas base URL must use HTTPS.")
        if not self.token:
            raise ValueError("Canvas access token is required.")

        self._base_netloc = parsed.netloc.lower()
        self._download_opener = urllib.request.build_opener(NoRedirectHandler)

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
        data = self.get_json(f"/api/v1/courses/{quoted_course_id}", params=[("include[]", "term")])
        if not isinstance(data, dict):
            raise CanvasAPIError(f"Expected an object response for course {course_id}.")
        return data

    def list_course_files(self, course_id: str) -> list[dict[str, Any]]:
        quoted_course_id = urllib.parse.quote(str(course_id), safe="")
        return list(
            self.list_paginated(
                f"/api/v1/courses/{quoted_course_id}/files",
                params=[("per_page", "100")],
            )
        )

    def list_modules(self, course_id: str) -> list[dict[str, Any]]:
        quoted_course_id = urllib.parse.quote(str(course_id), safe="")
        return list(
            self.list_paginated(
                f"/api/v1/courses/{quoted_course_id}/modules",
                params=[
                    ("per_page", "100"),
                    ("include[]", "items"),
                    ("include[]", "content_details"),
                ],
            )
        )

    def list_module_items(self, course_id: str, module_id: str) -> list[dict[str, Any]]:
        quoted_course_id = urllib.parse.quote(str(course_id), safe="")
        quoted_module_id = urllib.parse.quote(str(module_id), safe="")
        return list(
            self.list_paginated(
                f"/api/v1/courses/{quoted_course_id}/modules/{quoted_module_id}/items",
                params=[("per_page", "100")],
            )
        )

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

    def get_json(
        self,
        path_or_url: str,
        params: Sequence[tuple[str, str]] | None = None,
    ) -> JSON:
        data, _headers = self._request_json(self._build_url(path_or_url, params=params))
        return data

    def download_file_url(self, file_url: str, target_path: Path) -> Path:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = target_path.with_name(f"{target_path.name}.part")
        current_url = self._build_url(file_url)

        response = None
        for _redirect_count in range(10):
            request = urllib.request.Request(current_url, headers=self._download_headers(current_url))
            try:
                response = self._download_opener.open(request, timeout=self.timeout)
                break
            except urllib.error.HTTPError as exc:
                if exc.code not in {301, 302, 303, 307, 308}:
                    raise self._api_error(exc, current_url) from exc

                location = exc.headers.get("Location")
                if not location:
                    raise CanvasAPIError(f"Canvas redirected without a Location header: {current_url}")
                current_url = urllib.parse.urljoin(current_url, location)
        else:
            raise CanvasAPIError(f"Too many redirects while downloading {file_url}")

        assert response is not None
        with response, temp_path.open("wb") as output:
            shutil.copyfileobj(response, output)

        temp_path.replace(target_path)
        return target_path

    def _request_json(self, url: str) -> tuple[JSON, urllib.request._headers]:  # type: ignore[name-defined]
        request = urllib.request.Request(url, headers=self._json_headers(url))
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                body = response.read().decode(charset)
                return json.loads(body), response.headers
        except urllib.error.HTTPError as exc:
            raise self._api_error(exc, url) from exc
        except json.JSONDecodeError as exc:
            raise CanvasAPIError(f"Canvas returned invalid JSON from {url}") from exc

    def _json_headers(self, url: str) -> dict[str, str]:
        headers = {
            "Accept": "application/json+canvas-string-ids",
            "User-Agent": self.user_agent,
        }
        if self._is_canvas_origin(url):
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _download_headers(self, url: str) -> dict[str, str]:
        headers = {"User-Agent": self.user_agent}
        if self._is_canvas_origin(url):
            headers["Authorization"] = f"Bearer {self.token}"
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

        message = f"Canvas request failed with HTTP {exc.code} for {url}"
        if detail:
            message = f"{message}: {detail[:500]}"
        return CanvasAPIError(message)
