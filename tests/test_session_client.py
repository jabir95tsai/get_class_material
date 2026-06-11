from __future__ import annotations

import email.message
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from ntu_cool_materials import session_client
from ntu_cool_materials.session_client import (
    AUTH_EXPIRED,
    AUTH_OK,
    AUTH_UNKNOWN,
    CanvasSessionClient,
    read_headers_file,
    redact_headers,
)


def _client() -> CanvasSessionClient:
    return CanvasSessionClient(
        base_url="https://cool.ntu.edu.tw",
        headers={"cookie": "canvas_session=secret"},
    )


def _http_error(code: int, location: str | None = None) -> urllib.error.HTTPError:
    hdrs = email.message.Message()
    if location is not None:
        hdrs["Location"] = location
    return urllib.error.HTTPError("https://cool.ntu.edu.tw", code, "err", hdrs, None)


class _FakeResp:
    def __init__(self, status: int) -> None:
        self.status = status

    def __enter__(self) -> "_FakeResp":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


class _FakeOpener:
    """Stands in for build_opener(NoRedirectHandler); .open() returns or raises
    whatever the test wires up."""

    def __init__(self, result: object) -> None:
        self._result = result

    def open(self, request: object, timeout: float | None = None) -> object:  # noqa: A003
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class CheckAuthTests(unittest.TestCase):
    """The pre-flight probe must only report AUTH_EXPIRED on definitive
    signals (401 / redirect-to-SSO). Network/timeout/ambiguous → AUTH_UNKNOWN,
    so a slow connection never triggers a spurious browser re-login."""

    def _run_with(self, result: object) -> str:
        with mock.patch.object(
            session_client.urllib.request, "build_opener", return_value=_FakeOpener(result)
        ):
            return _client().check_auth()

    def test_200_is_ok(self) -> None:
        self.assertEqual(self._run_with(_FakeResp(200)), AUTH_OK)

    def test_401_is_expired(self) -> None:
        self.assertEqual(self._run_with(_http_error(401)), AUTH_EXPIRED)

    def test_redirect_off_origin_to_sso_is_expired(self) -> None:
        self.assertEqual(
            self._run_with(_http_error(302, "https://sso.ntu.edu.tw/login")),
            AUTH_EXPIRED,
        )

    def test_redirect_on_origin_is_unknown(self) -> None:
        # An on-origin 3xx isn't a definitive logout signal — don't guess.
        self.assertEqual(
            self._run_with(_http_error(302, "https://cool.ntu.edu.tw/api/v1/users/self/")),
            AUTH_UNKNOWN,
        )

    def test_relative_redirect_is_unknown(self) -> None:
        self.assertEqual(self._run_with(_http_error(302, "/login/canvas")), AUTH_UNKNOWN)

    def test_server_error_is_unknown(self) -> None:
        self.assertEqual(self._run_with(_http_error(500)), AUTH_UNKNOWN)

    def test_timeout_is_unknown_not_expired(self) -> None:
        # The critical one: a slow network must NOT look like an expired login.
        self.assertEqual(self._run_with(TimeoutError("timed out")), AUTH_UNKNOWN)

    def test_url_error_is_unknown(self) -> None:
        self.assertEqual(
            self._run_with(urllib.error.URLError("connection refused")), AUTH_UNKNOWN
        )


class SessionClientTests(unittest.TestCase):
    def test_read_headers_file_from_raw_devtools_headers(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "headers.txt"
            path.write_text(
                "accept: application/json\n"
                "cookie: canvas_session=secret\n"
                ":authority: cool.ntu.edu.tw\n",
                encoding="utf-8",
            )

            headers = read_headers_file(path)

            self.assertEqual(headers["accept"], "application/json")
            self.assertEqual(headers["cookie"], "canvas_session=secret")
            self.assertNotIn(":authority", headers)

    def test_redact_headers_hides_cookie(self) -> None:
        self.assertEqual(
            redact_headers({"cookie": "secret", "user-agent": "test"}),
            {"cookie": "<redacted>", "user-agent": "test"},
        )


if __name__ == "__main__":
    unittest.main()
