from __future__ import annotations

import unittest
import contextlib
import io
import json
import tempfile
from pathlib import Path
from unittest import mock

from ntu_cool_materials import course_pipeline as pipeline
from ntu_cool_materials.canvas_client import CanvasAPIError, SessionExpiredError
from ntu_cool_materials.cli import _build_parser
from ntu_cool_materials.storage import course_directory_name

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


class AnnouncementPipelineTests(unittest.TestCase):
    def test_default_download_refreshes_full_content_and_overview(self):
        with tempfile.TemporaryDirectory() as tmp:
            course = {"id": 123, "name": "Test course"}
            client = mock.Mock()
            client.list_course_announcements.return_value = [
                {"id": 1, "title": "Notice", "message": "<p>完整公告內容</p>"}
            ]
            plan = pipeline.CoursePlan(course, "123", Path(tmp) / course_directory_name(course))
            plan.course_dir.mkdir()
            with mock.patch.object(pipeline, "plan_course", return_value=plan), contextlib.redirect_stdout(io.StringIO()):
                kwargs = dict(course_id="123", output_dir=Path(tmp), client=client,
                              skip_pdfs=True, skip_pages=True, skip_youtube=True, skip_cool_videos=True)
                pipeline.download_course(**kwargs)
                target = plan.course_dir / "announcements"
                self.assertIn("完整公告內容", (target / "announcements.md").read_text(encoding="utf-8"))
                self.assertEqual(json.loads((target / "announcements.json").read_text(encoding="utf-8")), client.list_course_announcements.return_value)
                self.assertIn("announcements/announcements.md", (plan.course_dir / "course_overview.md").read_text(encoding="utf-8"))
                client.list_course_announcements.return_value[0]["message"] = "<p>Updated</p>"
                pipeline.download_course(**kwargs)
                self.assertIn("Updated", (target / "announcements.md").read_text(encoding="utf-8"))
                client.reset_mock()
                pipeline.download_course(**kwargs, skip_announcements=True)
                client.list_course_announcements.assert_not_called()

    def test_failure_preserves_saved_content_and_session_expiry_propagates(self):
        with tempfile.TemporaryDirectory() as tmp:
            course = {"id": 123, "name": "Test"}
            plan = pipeline.CoursePlan(course, "123", Path(tmp) / course_directory_name(course))
            client = mock.Mock()
            client.list_course_announcements.return_value = [{"message": "Existing"}]
            pipeline.save_announcements(plan, client)
            client.list_course_announcements.side_effect = CanvasAPIError("Unavailable")
            self.assertEqual(len(pipeline.save_announcements(plan, client).failed), 1)
            self.assertIn("Existing", (plan.course_dir / "announcements/announcements.md").read_text(encoding="utf-8"))
            client.list_course_announcements.side_effect = SessionExpiredError("Expired")
            with self.assertRaises(SessionExpiredError):
                pipeline.save_announcements(plan, client)

    def test_empty_announcements_saved_and_flags_available(self):
        with tempfile.TemporaryDirectory() as tmp:
            course = {"id": 123}
            plan = pipeline.CoursePlan(course, "123", Path(tmp) / course_directory_name(course))
            client = mock.Mock()
            client.list_course_announcements.return_value = []
            self.assertEqual(pipeline.save_announcements(plan, client).done, 0)
            self.assertEqual(json.loads((plan.course_dir / "announcements/announcements.json").read_text()), [])
        for args in [["pick"], ["download-course", "--course-id", "123"]]:
            self.assertFalse(_build_parser().parse_args(args).skip_announcements)
            self.assertTrue(_build_parser().parse_args(args + ["--skip-announcements"]).skip_announcements)


if __name__ == "__main__":
    unittest.main()
