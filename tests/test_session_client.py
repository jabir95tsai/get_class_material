from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ntu_cool_materials.session_client import read_headers_file, redact_headers


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
