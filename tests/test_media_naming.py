from __future__ import annotations

import unittest

from ntu_cool_materials.media_naming import build_video_title_map, extract_youtube_ids, sanitize_teacher_title


class MediaNamingTests(unittest.TestCase):
    def test_extract_youtube_ids_from_malformed_canvas_url(self) -> None:
        value = (
            "https://www.youtube.comhttps://www.youtube.com/watch?v=q_o3vYUhDlQ"
            "&index=4/watch?v=6vbqf9ozYT8"
        )

        self.assertEqual(extract_youtube_ids(value), ["q_o3vYUhDlQ", "6vbqf9ozYT8"])

    def test_build_video_title_map_keeps_first_duplicate_title(self) -> None:
        title_map = build_video_title_map(
            {
                "module": {
                    "items": [
                        {
                            "type": "ExternalUrl",
                            "title": "1-1 生物音樂學簡介",
                            "external_url": "https://www.youtube.com/watch?v=FXx6tn10wnQ",
                        },
                        {
                            "type": "ExternalUrl",
                            "title": "1-2 如何定義音樂？",
                            "external_url": "https://www.youtube.com/watch?v=FXx6tn10wnQ",
                        },
                    ]
                }
            }
        )

        self.assertEqual(title_map["FXx6tn10wnQ"], "1-1 生物音樂學簡介")

    def test_sanitize_teacher_title_preserves_fullwidth_punctuation(self) -> None:
        self.assertEqual(
            sanitize_teacher_title("1-6 音樂的口傳（續篇：腦科學的補充）"),
            "1-6 音樂的口傳（續篇：腦科學的補充）",
        )
        self.assertEqual(sanitize_teacher_title("bad:name?.mp4"), "bad_name_.mp4")


if __name__ == "__main__":
    unittest.main()
