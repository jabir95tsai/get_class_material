"""Regression for the picker keeping an active Playwright sync runtime."""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from playwright.sync_api import sync_playwright
from ntu_cool_materials.notebooklm_browser import NotebookLMBrowser, NotebookChoice, startup_error
from ntu_cool_materials.notebooklm_flow import guided_import


class RuntimeTests(unittest.TestCase):
    def test_reuses_active_real_runtime_without_starting_nested_driver(self):
        with tempfile.TemporaryDirectory() as tmp, sync_playwright() as pw:
            context = MagicMock()
            context.pages = [MagicMock()]
            with patch.object(pw.chromium, "launch_persistent_context", return_value=context) as launch, \
                 patch("playwright.sync_api.sync_playwright", side_effect=AssertionError("Nested driver")):
                with NotebookLMBrowser(Path(tmp), playwright=pw) as browser:
                    self.assertIs(browser._pw, pw)
                    self.assertIs(browser.context, context)
                context.close.assert_called_once()
                launch.assert_called_once()
                # Outer runtime still operates after the NotebookLM context closes.
                self.assertEqual(pw.chromium.name, "chromium")
                request = pw.request.new_context()
                request.dispose()

    def test_scan_deduplicates_visible_links_and_rejects_other_hosts(self):
        browser = NotebookLMBrowser(Path("unused"))
        browser._logged_in = True
        browser.page = MagicMock()
        browser.page.url = "https://notebooklm.google.com/"
        links = []
        for href, visible in [("/notebook/one", True), ("/notebook/one", True),
                              ("https://example.com/notebook/two", True), ("/notebook/hidden", False)]:
            link = MagicMock()
            link.is_visible.return_value = visible
            link.get_attribute.return_value = href
            link.inner_text.return_value = "Course notebook"
            links.append(link)
        collection = browser.page.locator.return_value
        collection.count.return_value = len(links)
        collection.nth.side_effect = links.__getitem__
        choices = browser.list_notebooks()
        self.assertEqual([(c.title, c.url) for c in choices], [("Course notebook", "https://notebooklm.google.com/notebook/one")])
        browser.page.goto.assert_not_called()

    def test_failed_launch_does_not_stop_borrowed_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            pw = MagicMock()
            pw.chromium.launch_persistent_context.side_effect = RuntimeError("ProcessSingleton private details")
            with self.assertRaisesRegex(RuntimeError, "設定檔正被"):
                with NotebookLMBrowser(Path(tmp), playwright=pw):
                    pass
            pw.stop.assert_not_called()

    def test_errors_are_classified_without_echoing_private_details(self):
        cases = [("Executable doesn't exist: private-details", "尚未安裝"),
                 ("Playwright Sync API inside the asyncio loop private-details", "執行環境衝突"),
                 ("ProcessSingleton private-details", "設定檔正被"),
                 ("Other private-details", "原因未能分類")]
        for message, expected in cases:
            result = startup_error(RuntimeError(message))
            self.assertIn(expected, result)
            self.assertNotIn("private-details", result)


class GuidedSelectionTests(unittest.TestCase):
    def test_login_scan_select_confirm_import_in_order(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            root = Path(tmp)
            (root / "notes.md").write_text("Notes", encoding="utf-8")
            events = []
            url = "https://notebooklm.google.com/notebook/existing"
            with patch("ntu_cool_materials.notebooklm_flow.NotebookLMBrowser") as factory, \
                 patch("ntu_cool_materials.notebooklm_flow.import_plan") as importer, \
                 patch("sys.stdin.isatty", return_value=True):
                browser = factory.return_value.__enter__.return_value
                browser.login.side_effect = lambda: events.append("login")
                browser.list_notebooks.side_effect = lambda: (events.append("scan") or [NotebookChoice("My course", url)])
                browser.select_notebook.side_effect = lambda choice: (events.append("select") or choice.url)
                importer.side_effect = lambda *a, **k: (events.append("import") or MagicMock())
                answers = iter(["2", "1", "y"])
                def answer(prompt):
                    if "編號" in prompt:
                        self.assertEqual(events, ["login", "scan"])
                    if "開始上傳" in prompt:
                        events.append("confirm")
                    return next(answers)
                with patch("builtins.input", side_effect=answer):
                    self.assertEqual(guided_import(root, profile_dir=root / ".secrets"), 0)
                self.assertEqual(events, ["login", "scan", "select", "confirm", "import"])
                self.assertEqual(importer.call_args.kwargs["notebook_url"], url)
                self.assertFalse(importer.call_args.kwargs["force_new"])

    def test_cancel_after_login_never_creates_notebook_or_uploads(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            root = Path(tmp)
            (root / "notes.md").write_text("Notes", encoding="utf-8")
            with patch("ntu_cool_materials.notebooklm_flow.NotebookLMBrowser") as factory, \
                 patch("ntu_cool_materials.notebooklm_flow.import_plan") as importer, \
                 patch("sys.stdin.isatty", return_value=True), patch("builtins.input", side_effect=["2", "q"]):
                factory.return_value.__enter__.return_value.list_notebooks.return_value = []
                self.assertEqual(guided_import(root, profile_dir=root / ".secrets"), 0)
                importer.assert_not_called()
                factory.return_value.__exit__.assert_called_once()


if __name__ == "__main__":
    unittest.main()
