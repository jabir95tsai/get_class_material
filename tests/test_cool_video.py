from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ntu_cool_materials.cool_video import (
    CoolVideoError,
    merge_segments,
    normalize_base_url,
    probe_top_level_mp4_boxes,
    segment_url,
)


class CoolVideoTests(unittest.TestCase):
    def test_normalize_base_url_requires_absolute_url(self) -> None:
        self.assertEqual(normalize_base_url("https://example.test/path"), "https://example.test/path/")
        self.assertEqual(normalize_base_url("https://example.test/path/"), "https://example.test/path/")
        with self.assertRaises(CoolVideoError):
            normalize_base_url("files-1.dlc.ntu.edu.tw/cool-video/id")

    def test_normalize_base_url_accepts_segment_url(self) -> None:
        self.assertEqual(
            normalize_base_url(
                "https://files-1.dlc.ntu.edu.tw/cool-video/202201/uuid/audio-480-10.m4s"
            ),
            "https://files-1.dlc.ntu.edu.tw/cool-video/202201/uuid/",
        )

    def test_segment_url_uses_expected_filename(self) -> None:
        self.assertEqual(
            segment_url("https://files-1.dlc.ntu.edu.tw/cool-video/202201/uuid/", "video-480-", 12),
            "https://files-1.dlc.ntu.edu.tw/cool-video/202201/uuid/video-480-12.m4s",
        )

    def test_merge_segments_concatenates_in_order(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = root / "1.m4s"
            second = root / "2.m4s"
            merged = root / "merged.m4s"
            first.write_bytes(b"abc")
            second.write_bytes(b"def")

            merge_segments([first, second], merged)

            self.assertEqual(merged.read_bytes(), b"abcdef")

    def test_probe_top_level_mp4_boxes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "init.m4s"
            path.write_bytes(
                b"\x00\x00\x00\x18ftyp" + b"\x00" * 16 + b"\x00\x00\x00\x08moov"
            )

            self.assertEqual(probe_top_level_mp4_boxes(path), {"ftyp", "moov"})


if __name__ == "__main__":
    unittest.main()
