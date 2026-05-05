from __future__ import annotations

import unittest

from ntu_cool_materials.browser_session import _cookie_header, _looks_like_login
from ntu_cool_materials.session_client import CanvasSessionClient


class BrowserSessionTests(unittest.TestCase):
    def test_cookie_header(self) -> None:
        self.assertEqual(
            _cookie_header(
                [
                    {"name": "a", "value": "1"},
                    {"name": "b", "value": "2"},
                    {"name": "", "value": "ignored"},
                ]
            ),
            "a=1; b=2",
        )

    def test_looks_like_login(self) -> None:
        self.assertTrue(_looks_like_login("https://cool.ntu.edu.tw/login"))
        self.assertTrue(_looks_like_login("https://cool.ntu.edu.tw/oauth2/login"))
        self.assertFalse(_looks_like_login("https://cool.ntu.edu.tw/courses/57544/announcements"))

    def test_session_client_builds_course_url(self) -> None:
        client = CanvasSessionClient(base_url="https://cool.ntu.edu.tw", headers={"cookie": "a=b"})

        url = client._build_url(
            "/api/v1/courses",
            params=[("per_page", "100"), ("include[]", "term"), ("enrollment_state", "active")],
        )

        self.assertEqual(
            url,
            "https://cool.ntu.edu.tw/api/v1/courses?per_page=100&include%5B%5D=term&enrollment_state=active",
        )


if __name__ == "__main__":
    unittest.main()
