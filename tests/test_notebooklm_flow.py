import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from ntu_cool_materials import cli
from ntu_cool_materials.notebooklm import NotebookLMError, STATE_NAME, build_import_plan
from ntu_cool_materials.notebooklm_flow import guided_import, prepare_manual_upload, select_course_folder
from ntu_cool_materials.notebooklm_browser import GoogleLoginRejected


class PublicFlowTests(unittest.TestCase):
    def test_rejected_login_closes_context_then_offers_normal_browser(self):
        self.browser.login.side_effect = GoogleLoginRejected("Google 已拒絕登入")
        with patch("sys.stdin.isatty", return_value=True), \
             patch("builtins.input", side_effect=["2", "y", "y"]), \
             patch("ntu_cool_materials.notebooklm_flow.import_plan") as importer, \
             patch("ntu_cool_materials.notebooklm_flow.webbrowser.open") as open_browser:
            def opened(url):
                self.browser_factory.return_value.__exit__.assert_called_once()
                return True
            open_browser.side_effect = opened
            self.assertEqual(guided_import(self.root, profile_dir=self.profile), 1)
            importer.assert_not_called()
            self.browser.list_notebooks.assert_not_called()
            open_browser.assert_called_once_with("https://notebooklm.google.com/")
        self.assertFalse((self.root / STATE_NAME).exists())
        self.assertTrue((self.root / ".notebooklm-manual").exists())

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for i in range(3):
            (self.root / f"lecture{i}.md").write_text(f"Lecture {i}", encoding="utf-8")
        self.profile = self.root / ".secrets/profile"
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        self.browser_factory = self.enterContext(patch("ntu_cool_materials.notebooklm_flow.NotebookLMBrowser"))
        self.browser = self.browser_factory.return_value.__enter__.return_value
        self.browser.list_notebooks.return_value = []

    def test_manual_batches_preserve_bytes_and_do_not_claim_uploaded(self):
        bundle = prepare_manual_upload(self.root, max_sources=2)
        self.assertEqual(len(list((bundle / "batch-001").iterdir())), 2)
        self.assertEqual(len(list((bundle / "batch-002").iterdir())), 1)
        self.assertFalse((self.root / STATE_NAME).exists())
        self.assertFalse(self.profile.exists())
        self.assertIn("尚未上傳", (bundle / "上傳說明.txt").read_text(encoding="utf-8"))
        self.assertEqual(len(build_import_plan(self.root).sources), 3)
        again = prepare_manual_upload(self.root)
        self.assertNotEqual(bundle, again)

    def test_manual_cli_needs_no_google_or_canvas_auth(self):
        with patch("ntu_cool_materials.notebooklm_flow.import_plan") as automatic:
            self.assertEqual(cli.main(["notebooklm", "--course-dir", str(self.root), "--manual"]), 0)
        automatic.assert_not_called()

    def test_cancel_does_not_open_browser_or_create_files(self):
        with patch("sys.stdin.isatty", return_value=True), patch("builtins.input", return_value="q"), \
             patch("ntu_cool_materials.notebooklm_flow.import_plan") as automatic:
            self.assertEqual(guided_import(self.root, profile_dir=self.profile), 0)
        automatic.assert_not_called()
        self.assertFalse((self.root / ".notebooklm-manual").exists())

    def test_manual_flow_does_not_start_automatic_login(self):
        with patch("sys.stdin.isatty", return_value=True), patch("builtins.input", side_effect=["1", "n"]), \
             patch("ntu_cool_materials.notebooklm_flow.import_plan") as automatic:
            self.assertEqual(guided_import(self.root, profile_dir=self.profile), 0)
        automatic.assert_not_called()

    def test_upload_requires_confirmation_in_guide(self):
        with patch("sys.stdin.isatty", return_value=True), patch("builtins.input", side_effect=["2", "n", "n"]), \
             patch("ntu_cool_materials.notebooklm_flow.import_plan") as automatic:
            self.assertEqual(guided_import(self.root, profile_dir=self.profile), 0)
        automatic.assert_not_called()

    def test_existing_notebook_url_passed_to_automatic_import(self):
        url = "https://notebooklm.google.com/notebook/example"
        with patch("sys.stdin.isatty", return_value=True), \
             patch("builtins.input", side_effect=["2", "p", url, "y"]), \
             patch("ntu_cool_materials.notebooklm_flow.import_plan") as automatic:
            self.assertEqual(guided_import(self.root, profile_dir=self.profile), 0)
        self.assertEqual(automatic.call_args.kwargs["notebook_url"], url)

    def test_failed_upload_with_fallback_still_returns_failure(self):
        with patch("sys.stdin.isatty", return_value=True), patch("builtins.input", side_effect=["2", "n", "y", "y", "n"]), \
             patch("ntu_cool_materials.notebooklm_flow.import_plan", side_effect=NotebookLMError("登入未完成")):
            self.assertEqual(guided_import(self.root, profile_dir=self.profile), 1)
        self.assertTrue((self.root / ".notebooklm-manual").exists())
        self.assertFalse((self.root / STATE_NAME).exists())

    def test_noninteractive_no_folder_fails_without_prompt(self):
        with patch("sys.stdin.isatty", return_value=False), patch("builtins.input") as ask:
            self.assertEqual(cli.main(["notebooklm"]), 1)
        ask.assert_not_called()

    def test_picker_ignores_hidden_folders(self):
        (self.root / ".secrets").mkdir()
        course = self.root / "My course"
        course.mkdir()
        with patch("sys.stdin.isatty", return_value=True), patch("builtins.input", return_value="1"):
            self.assertEqual(select_course_folder(self.root), course)


if __name__ == "__main__":
    unittest.main()
