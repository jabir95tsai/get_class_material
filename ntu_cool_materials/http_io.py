"""Bounded GET retries, origin-safe redirects and validated atomic downloads."""
from __future__ import annotations

import hashlib
import http.client
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from email.utils import parsedate_to_datetime
from pathlib import Path

from .storage import atomic_write_text


class DownloadError(RuntimeError):
    pass


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


RETRY_CODES = {429, 500, 502, 503, 504}
NETWORK_ERRORS = (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException)
LOGIN_PATH = re.compile(r"/(?:login|saml|oauth2?)(?:/|$|\?)", re.I)


def origin(url: str) -> tuple[str, str]:
    p = urllib.parse.urlsplit(url)
    return p.scheme.lower(), p.netloc.lower()


def safe_url(url: str) -> str:
    """Never put signed query parameters or userinfo in diagnostics."""
    p = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((p.scheme, p.hostname or "", p.path, "", ""))


def retry_delay(attempt: int, headers=None) -> float:
    value = headers.get("Retry-After") if headers else None
    if value:
        try:
            return min(60.0, max(0.0, float(value)))
        except ValueError:
            try:
                return min(60.0, max(0.0, parsedate_to_datetime(value).timestamp() - time.time()))
            except (ValueError, TypeError, OverflowError):
                pass
    return min(8.0, 0.5 * 2 ** attempt)


def open_url(url: str, headers_for_url, *, timeout=60, api_origin=None, extra_headers=None):
    """One GET attempt; each redirect receives freshly scoped credentials."""
    from .canvas_client import SessionExpiredError

    opener = urllib.request.build_opener(NoRedirectHandler)
    current = url
    for _ in range(10):
        parsed = urllib.parse.urlsplit(current)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise DownloadError("Refusing a non-HTTPS or credential-bearing URL")
        if api_origin is not None and origin(current) != origin(api_origin):
            raise SessionExpiredError("Canvas API redirected outside its origin; refresh login")
        if LOGIN_PATH.search(parsed.path):
            raise SessionExpiredError("Canvas redirected to a login page")
        headers = dict(headers_for_url(current))
        headers.update(extra_headers or {})
        try:
            return opener.open(urllib.request.Request(current, headers=headers), timeout=timeout)
        except urllib.error.HTTPError as exc:
            if exc.code not in {301, 302, 303, 307, 308}:
                raise
            location = exc.headers.get("Location")
            exc.close()
            if not location:
                raise DownloadError("Redirect did not include Location") from None
            current = urllib.parse.urljoin(current, location)
    raise DownloadError("Too many download redirects")


def request_json(url: str, headers_for_url, *, timeout=30, api_origin=None, attempts=3):
    from .canvas_client import SessionExpiredError

    for attempt in range(attempts):
        try:
            with open_url(url, headers_for_url, timeout=timeout, api_origin=api_origin) as response:
                raw = response.read()
                if raw.lstrip().lower().startswith((b"<!doctype html", b"<html")):
                    raise SessionExpiredError("Canvas returned an HTML login page instead of JSON")
                charset = getattr(response.headers, "get_content_charset", lambda: None)() or "utf-8"
                return json.loads(raw.decode(charset)), response.headers
        except urllib.error.HTTPError as exc:
            if exc.code not in RETRY_CODES or attempt + 1 == attempts:
                raise
            delay = retry_delay(attempt, exc.headers)
            exc.close()
            time.sleep(delay)
        except NETWORK_ERRORS:
            if attempt + 1 == attempts:
                raise DownloadError(f"Network request failed: {safe_url(url)}") from None
            time.sleep(retry_delay(attempt))


def _integer(headers, name):
    value = headers.get(name)
    if value is None:
        return None
    try:
        value = int(value)
        if value < 0:
            raise ValueError
        return value
    except (TypeError, ValueError):
        raise DownloadError(f"Invalid {name} response header") from None


def stream_response(response, target: Path, *, offset=0, expected_size=None, progress=None, validate=None):
    """Validate the body before promoting .part; preserve the previous target."""
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    length = _integer(response.headers, "Content-Length")
    status = response.status
    total = None if length is None else offset + length
    if status == 206:
        match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", response.headers.get("Content-Range", ""))
        if not match:
            raise DownloadError("Missing or invalid Content-Range")
        start, end, full = map(int, match.groups())
        if start != offset or end < start or end + 1 != full:
            raise DownloadError("Unexpected resume range; refusing to concatenate different bytes")
        if length is not None and length != end - start + 1:
            raise DownloadError("Content-Length disagrees with Content-Range")
        length, total = end - start + 1, full
    elif status != 200 or offset:
        raise DownloadError(f"Unexpected download status {status}")
    if expected_size is not None and total is not None and total != expected_size:
        raise DownloadError("Remote size differs from file metadata")
    if offset and (not part.is_file() or part.stat().st_size != offset):
        raise DownloadError("Partial file changed during download")
    content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
    if content_type in {"text/html", "application/xhtml+xml"} and target.suffix.lower() not in {".html", ".htm"}:
        from .canvas_client import SessionExpiredError
        raise SessionExpiredError("Download returned HTML; refresh login before retrying")
    written = 0
    with part.open("ab" if offset else "wb") as output:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            if written == 0 and not offset:
                prefix = chunk[:512].lstrip().lower()
                if target.suffix.lower() in {".pdf", ".mp4"} and prefix.startswith((b"<!doctype html", b"<html", b"#extm3u", b"<?xml", b"<mpd")):
                    raise DownloadError("Received a webpage or stream manifest instead of the requested file")
            output.write(chunk)
            written += len(chunk)
            if progress:
                progress(offset + written, total)
    if length is not None and written != length:
        raise DownloadError(f"Incomplete response: received {written} of {length} bytes")
    if expected_size is not None and offset + written != expected_size:
        raise DownloadError("Downloaded size differs from file metadata")
    if offset + written == 0 and expected_size != 0:
        raise DownloadError("Empty response")
    if validate is not None and not validate(part):
        raise DownloadError("Downloaded media failed validation")
    part.replace(target)


def download(url: str, target: Path, headers_for_url, *, identity=None, expected_size=None,
             timeout=120, attempts=3, progress=None, validate=None):
    """Resume only with a persisted validator belonging to the same resource."""
    key = hashlib.sha256((identity or safe_url(url)).encode()).hexdigest()
    part = target.with_name(target.name + ".part")
    metadata = part.with_name(part.name + ".json")
    for attempt in range(attempts):
        offset, saved = 0, {}
        try:
            saved = json.loads(metadata.read_text(encoding="utf-8"))
            if saved.get("identity") == key and saved.get("validator") and part.is_file():
                offset = part.stat().st_size
        except (OSError, ValueError, AttributeError):
            saved = {}
        extra = {"Accept-Encoding": "identity"}
        if offset:
            extra.update({"Range": f"bytes={offset}-", "If-Range": saved["validator"]})
        try:
            with open_url(url, headers_for_url, timeout=timeout, extra_headers=extra) as response:
                etag = response.headers.get("ETag", "")
                validator = etag if etag and not etag.startswith("W/") else response.headers.get("Last-Modified")
                if response.status == 206 and offset and validator and validator != saved.get("validator"):
                    metadata.unlink(missing_ok=True)
                    raise DownloadError("Resource changed during resume")
                if response.status == 200:
                    offset = 0
                    # A new validator must never be paired with old partial bytes,
                    # even if validation rejects the new response before opening it.
                    part.unlink(missing_ok=True)
                elif offset and not validator:
                    validator = saved.get("validator")
                atomic_write_text(metadata, json.dumps({"identity": key, "validator": validator}))
                stream_response(response, target, offset=offset, expected_size=expected_size, progress=progress, validate=validate)
            metadata.unlink(missing_ok=True)
            return target
        except urllib.error.HTTPError as exc:
            if exc.code == 416 and offset:
                metadata.unlink(missing_ok=True)
            elif exc.code not in RETRY_CODES:
                raise
            if attempt + 1 == attempts:
                raise DownloadError(f"Download failed (HTTP {exc.code}): {safe_url(url)}") from None
            delay = retry_delay(attempt, exc.headers)
            exc.close()
            time.sleep(delay)
        except (*NETWORK_ERRORS, DownloadError) as exc:
            # Invalid ranges must restart; a short body can resume with its validator.
            if isinstance(exc, DownloadError) and "Incomplete response" not in str(exc):
                metadata.unlink(missing_ok=True)
            if attempt + 1 == attempts:
                raise DownloadError(f"Download failed: {safe_url(url)} ({type(exc).__name__})") from None
            time.sleep(retry_delay(attempt))
