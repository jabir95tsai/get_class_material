from __future__ import annotations

import unittest

from ntu_cool_materials.canvas_client import parse_link_header


class LinkHeaderTests(unittest.TestCase):
    def test_parse_next_link(self) -> None:
        header = (
            '<https://cool.ntu.edu.tw/api/v1/courses?page=1>; rel="current", '
            '<https://cool.ntu.edu.tw/api/v1/courses?page=2>; rel="next", '
            '<https://cool.ntu.edu.tw/api/v1/courses?page=4>; rel="last"'
        )

        links = parse_link_header(header)

        self.assertEqual(links["next"], "https://cool.ntu.edu.tw/api/v1/courses?page=2")
        self.assertEqual(links["last"], "https://cool.ntu.edu.tw/api/v1/courses?page=4")

    def test_empty_link_header(self) -> None:
        self.assertEqual(parse_link_header(None), {})
        self.assertEqual(parse_link_header(""), {})


if __name__ == "__main__":
    unittest.main()
