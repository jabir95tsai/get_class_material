from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ntu_cool_materials import cli
from ntu_cool_materials.notebooklm import (
    STATE_NAME, NotebookLMError, build_import_plan, import_plan,
    validate_notebook_url,
)

URL = "https://notebooklm.google.com/notebook/test-notebook"
OTHER_URL = "https://notebooklm.google.com/notebook/other-notebook"


class FakeAdapter:
    def __init__(self):
        self.ready = set()
        self.calls = []
        self.open_calls = []
        self.fail = False
        self.extra_sources = 0

    def open_notebook(self, url, title):
        self.open_calls.append((url, title))
        return url or URL

    def ready_titles(self):
        return self.ready.copy()

    def source_count(self):
        return len(self.ready) + self.extra_sources

    def upload(self, path, title):
        self.calls.append((title, path.read_bytes()))
        if self.fail:
            raise RuntimeError("private-login-url-must-not-be-shown")
        self.ready.add(title)


class NotebookLMTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.output = contextlib.redirect_stdout(io.StringIO())
        self.output.__enter__()
        self.addCleanup(self.output.__exit__, None, None, None)

    def file(self, name="week1/lecture.md", content=b"Lecture content"):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def test_planner_filters_metadata_secrets_partial_files_and_media(self):
        self.file()
        for name in (".secrets/headers.txt", "metadata/private.md", "week1/raw.json",
                     "week1/file.pdf.part", "course_overview.md", "week1/video.mp4"):
            self.file(name)
        self.assertEqual([s.relative_path for s in build_import_plan(self.root).sources], ["week1/lecture.md"])

    def test_duplicate_content_skipped_but_same_name_different_content_kept(self):
        self.file("week1/lecture.md", b"First")
        self.file("week2/lecture.md", b"Second")
        self.file("week3/copy.md", b"First")
        plan = build_import_plan(self.root)
        self.assertEqual(len(plan.sources), 2)
        self.assertEqual({s.title for s in plan.sources}, {"week1 - lecture.md", "week2 - lecture.md"})
        self.assertIn(("week3/copy.md", "內容相同"), plan.skipped)

    def test_sources_do_not_contain_digest_hash_in_titles(self):
        self.file("announcements/announcements.md", b"Announce")
        self.file("module1/2026_1 上課前.pdf", b"Pre")
        plan = build_import_plan(self.root)
        titles = [s.title for s in plan.sources]
        self.assertIn("announcements", titles)
        self.assertIn("module1 - 2026_1 上課前.pdf", titles)
        for title in titles:
            self.assertNotRegex(title, r"\[[0-9a-fA-F]{8,64}\]")

    def test_media_requires_opt_in_and_empty_files_are_skipped(self):
        self.file("week1/video.mp4")
        self.file("week1/empty.pdf", b"")
        self.assertEqual(len(build_import_plan(self.root).sources), 0)
        self.assertEqual(len(build_import_plan(self.root, include_media=True).sources), 1)

    def test_large_files_are_not_hashed_or_uploaded(self):
        self.file(content=b"12345")
        with patch("ntu_cool_materials.notebooklm.MAX_FILE_BYTES", 4):
            self.assertFalse(build_import_plan(self.root).sources)

    def test_bad_urls_rejected_before_browser_use(self):
        for url in ("https://example.com/notebook/a", "http://notebooklm.google.com/notebook/a",
                    "https://notebooklm.google.com@evil.test/notebook/a",
                    "https://notebooklm.google.com:abc/notebook/a", "https://notebooklm.google.com/"):
            with self.subTest(url=url), self.assertRaises(NotebookLMError):
                validate_notebook_url(url)
        self.assertEqual(validate_notebook_url(URL + "?authuser=0#foo"), URL)

    def test_dry_run_does_not_create_state_or_connect(self):
        self.file()
        with patch("ntu_cool_materials.notebooklm_api.api_client_context") as connect:
            self.assertEqual(cli.main(["notebooklm", "--course-dir", str(self.root), "--dry-run"]), 0)
        connect.assert_not_called()
        self.assertFalse((self.root / STATE_NAME).exists())

    def test_success_and_rerun_do_not_duplicate_uploads(self):
        self.file()
        browser = FakeAdapter()
        plan = build_import_plan(self.root)
        self.assertEqual(import_plan(plan, browser).uploaded, 1)
        second = import_plan(plan, browser)
        self.assertEqual((second.uploaded, second.unchanged), (0, 1))
        self.assertEqual(len(browser.calls), 1)
        self.assertEqual(browser.open_calls[-1][0], URL)
        self.assertFalse(list(self.root.glob(".notebooklm-upload-*")))

    def test_timeout_is_pending_and_does_not_automatically_retry(self):
        self.file()
        browser = FakeAdapter()
        browser.fail = True
        plan = build_import_plan(self.root)
        with self.assertRaises(NotebookLMError) as caught:
            import_plan(plan, browser)
        self.assertNotIn("private-login", str(caught.exception))
        state = json.loads((self.root / STATE_NAME).read_text(encoding="utf-8"))
        self.assertEqual(state["notebooks"][URL]["sources"][plan.sources[0].digest]["status"], "pending")
        browser.fail = False
        with self.assertRaises(NotebookLMError):
            import_plan(plan, browser)
        self.assertEqual(len(browser.calls), 1)
        # The server eventually finished; reconcile without reuploading.
        browser.ready.add(plan.sources[0].title)
        self.assertEqual(import_plan(plan, browser).unchanged, 1)
        self.assertEqual(len(browser.calls), 1)

    def test_remote_deletion_and_local_edit_import_again(self):
        source = self.file()
        browser = FakeAdapter()
        import_plan(build_import_plan(self.root), browser)
        browser.ready.clear()
        self.assertEqual(import_plan(build_import_plan(self.root), browser).uploaded, 1)
        source.write_bytes(b"Revised lecture")
        self.assertEqual(import_plan(build_import_plan(self.root), browser).uploaded, 1)
        self.assertEqual(len(browser.ready), 2)  # Old remote version is preserved.

    def test_target_notebook_has_independent_journal(self):
        self.file()
        plan = build_import_plan(self.root)
        import_plan(plan, FakeAdapter())
        other = FakeAdapter()
        self.assertEqual(import_plan(plan, other, notebook_url=OTHER_URL).uploaded, 1)
        state = json.loads((self.root / STATE_NAME).read_text(encoding="utf-8"))
        self.assertEqual(set(state["notebooks"]), {URL, OTHER_URL})

    def test_capacity_includes_existing_remote_sources(self):
        self.file()
        browser = FakeAdapter()
        browser.extra_sources = 50
        with self.assertRaises(NotebookLMError):
            import_plan(build_import_plan(self.root), browser)
        self.assertEqual(browser.calls, [])

    def test_corrupt_journal_and_concurrent_import_fail_closed(self):
        self.file()
        state = self.root / STATE_NAME
        state.write_text("not json", encoding="utf-8")
        browser = FakeAdapter()
        with self.assertRaises(NotebookLMError):
            import_plan(build_import_plan(self.root), browser)
        browser.open_calls.clear()
        state.unlink()
        (self.root / ".notebooklm-import.lock").touch()
        with self.assertRaises(NotebookLMError):
            import_plan(build_import_plan(self.root), browser)
        self.assertEqual(browser.open_calls, [])

    def test_changed_file_since_planning_is_not_uploaded(self):
        source = self.file()
        plan = build_import_plan(self.root)
        source.write_bytes(b"Changed after planning")
        browser = FakeAdapter()
        with self.assertRaises(NotebookLMError):
            import_plan(plan, browser)
        self.assertEqual(browser.calls, [])

    def test_standalone_cli_dry_run_needs_no_canvas_auth(self):
        self.file()
        self.assertEqual(cli.main(["notebooklm", "--course-dir", str(self.root), "--dry-run"]), 0)
        self.assertEqual(cli.main(["notebooklm", "--course-dir", str(self.root / "missing"), "--dry-run"]), 1)

    def test_download_cli_imports_returned_course_and_propagates_error(self):
        headers = self.file("headers.stub")
        args = cli._build_parser().parse_args(["download-course", "--course-id", "123",
                                               "--headers-file", str(headers), "--notebooklm"])
        with patch.object(cli, "download_course", return_value=SimpleNamespace(course_dir=self.root)), \
             patch.object(cli, "_cmd_notebooklm", return_value=1) as importer:
            self.assertEqual(cli._cmd_download_course("https://cool.ntu.edu.tw", args), 1)
            importer.assert_called_once_with(self.root, args)

    def test_download_without_flag_keeps_import_disabled(self):
        headers = self.file("headers.stub")
        args = cli._build_parser().parse_args(["download-course", "--course-id", "123", "--headers-file", str(headers)])
        with patch.object(cli, "download_course"), patch.object(cli, "_cmd_notebooklm") as importer:
            self.assertEqual(cli._cmd_download_course("https://cool.ntu.edu.tw", args), 0)
            importer.assert_not_called()


if __name__ == "__main__":
    unittest.main()
