from __future__ import annotations

import unittest

from ntu_cool_materials.announcements import announcement_markdown, html_to_text


class AnnouncementTests(unittest.TestCase):
    def test_html_to_text_keeps_readable_lines(self) -> None:
        value = "<p>Hello <strong>class</strong></p><ul><li>Read chapter 1</li></ul>"

        self.assertEqual(html_to_text(value), "Hello class\nRead chapter 1")

    def test_announcement_markdown_includes_metadata(self) -> None:
        markdown = announcement_markdown(
            {
                "title": "Week 1",
                "posted_at": "2026-05-04T00:00:00Z",
                "author": {"display_name": "Teacher"},
                "message": "<p>Welcome</p>",
            }
        )

        self.assertIn("## Week 1", markdown)
        self.assertIn("Posted at: 2026-05-04T00:00:00Z", markdown)
        self.assertIn("Author: Teacher", markdown)
        self.assertIn("Welcome", markdown)


if __name__ == "__main__":
    unittest.main()
