from __future__ import annotations

import unittest

from ntu_cool_materials.youtube_cookies import (
    _has_login_cookie,
    _is_relevant_cookie_domain,
    _netscape_cookie_text,
)


class YouTubeCookieTests(unittest.TestCase):
    def test_relevant_cookie_domains(self) -> None:
        self.assertTrue(_is_relevant_cookie_domain(".youtube.com"))
        self.assertTrue(_is_relevant_cookie_domain("accounts.google.com"))
        self.assertFalse(_is_relevant_cookie_domain("cool.ntu.edu.tw"))

    def test_netscape_cookie_text(self) -> None:
        text = _netscape_cookie_text(
            [
                {
                    "domain": ".youtube.com",
                    "path": "/",
                    "secure": True,
                    "expires": 1893456000,
                    "name": "SID",
                    "value": "abc",
                }
            ]
        )

        self.assertIn(".youtube.com\tTRUE\t/\tTRUE\t1893456000\tSID\tabc", text)

    def test_has_login_cookie(self) -> None:
        self.assertFalse(_has_login_cookie([{"name": "PREF"}]))
        self.assertTrue(_has_login_cookie([{"name": "__Secure-1PSID"}]))


if __name__ == "__main__":
    unittest.main()
