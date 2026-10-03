import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ntu_cool_materials import cli
from ntu_cool_materials.notebooklm import NotebookLMError
from ntu_cool_materials.notebooklm_api import api_client_context
from ntu_cool_materials.notebooklm_flow import guided_import, select_course_folder


class PublicFlowTests(unittest.TestCase):
    def test_guide_routes_directly_to_api_without_mode_prompt(self):
        with patch("ntu_cool_materials.console.stdin_is_interactive", return_value=True), \
             patch("builtins.input") as ask, \
             patch("ntu_cool_materials.notebooklm_api.run_api_import", return_value=0) as run:
            self.assertEqual(guided_import(self.root), 0)
        ask.assert_not_called()
        self.assertEqual(run.call_args.args[0], self.root)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        for i in range(3):
            (self.root / f"lecture{i}.md").write_text(f"Lecture {i}", encoding="utf-8")
        self.output = io.StringIO()
        self.enterContext(contextlib.redirect_stdout(self.output))

    def test_removed_modes_are_rejected(self):
        for flag in ("--manual", "--extension", "--browser"):
            with self.subTest(flag=flag), contextlib.redirect_stderr(io.StringIO()), \
                 self.assertRaises(SystemExit):
                cli.main(["notebooklm", "--course-dir", str(self.root), flag])

    def test_failed_import_keeps_materials_and_prints_retry_command(self):
        with patch("ntu_cool_materials.console.stdin_is_interactive", return_value=False), \
             patch("ntu_cool_materials.notebooklm_api.run_api_import", side_effect=NotebookLMError("API failed")):
            self.assertEqual(cli.main(["notebooklm", "--course-dir", str(self.root)]), 1)
        self.assertIn("API failed", self.output.getvalue())
        self.assertIn(f'notebooklm --course-dir "{self.root}"', self.output.getvalue())

    def test_missing_package_explains_how_to_install(self):
        with patch.dict(sys.modules, {"notebooklm": None}):
            with self.assertRaises(NotebookLMError) as caught:
                api_client_context()
        self.assertIn("get-class-material[notebooklm]", str(caught.exception))
        self.assertIn(sys.executable, str(caught.exception))

    def test_guide_passes_source_options_to_api(self):
        with patch("ntu_cool_materials.console.stdin_is_interactive", return_value=True), \
             patch("ntu_cool_materials.notebooklm_api.run_api_import", return_value=0) as run:
            guided_import(self.root, include_media=True, max_sources=17)
        self.assertTrue(run.call_args.kwargs["include_media"])
        self.assertEqual(run.call_args.kwargs["max_sources"], 17)

    def test_guide_hashes_each_file_only_once(self):
        from ntu_cool_materials import notebooklm, notebooklm_api
        with patch("ntu_cool_materials.console.stdin_is_interactive", return_value=True), \
             patch.object(notebooklm, "sha256_file", wraps=notebooklm.sha256_file) as hashed, \
             patch.object(notebooklm_api, "NotebookLMAPIAdapter") as adapter, \
             patch.object(notebooklm_api, "import_plan",
                          return_value=notebooklm.ImportResult(notebook_url="https://notebook.google.com/notebook/x")):
            guided_import(self.root)
        adapter.assert_called_once()
        self.assertEqual(hashed.call_count, 3)

    def test_english_ui_covers_notebooklm_messages(self):
        from ntu_cool_materials.i18n import set_lang
        self.addCleanup(set_lang, "zh")
        with patch("ntu_cool_materials.console.stdin_is_interactive", return_value=False), \
             patch("ntu_cool_materials.notebooklm_api.run_api_import", side_effect=NotebookLMError("API failed")):
            self.assertEqual(cli.main(["notebooklm", "--course-dir", str(self.root), "--lang", "en"]), 1)
        self.assertIn("NotebookLM import incomplete: API failed", self.output.getvalue())
        (self.root / "slides.xyz").write_text("x", encoding="utf-8")
        from ntu_cool_materials.notebooklm import build_import_plan
        self.assertIn(("slides.xyz", "format NotebookLM doesn't support"), build_import_plan(self.root).skipped)

    def test_existing_notebook_url_passed_to_api(self):
        url = "https://notebooklm.google.com/notebook/example"
        with patch("ntu_cool_materials.console.stdin_is_interactive", return_value=True), \
             patch("ntu_cool_materials.notebooklm_api.run_api_import", return_value=0) as automatic:
            self.assertEqual(guided_import(self.root, notebook_url=url), 0)
        self.assertEqual(automatic.call_args.kwargs["notebook_url"], url)

    def test_api_failure_propagates_without_extra_prompt(self):
        with patch("ntu_cool_materials.console.stdin_is_interactive", return_value=True), patch("builtins.input") as ask, \
             patch("ntu_cool_materials.notebooklm_api.run_api_import", side_effect=NotebookLMError("API failed")):
            with self.assertRaises(NotebookLMError):
                guided_import(self.root)
        ask.assert_not_called()

    def test_noninteractive_no_folder_fails_without_prompt(self):
        with patch("ntu_cool_materials.console.stdin_is_interactive", return_value=False), patch("builtins.input") as ask:
            self.assertEqual(cli.main(["notebooklm"]), 1)
        ask.assert_not_called()

    def test_picker_ignores_hidden_folders(self):
        (self.root / ".secrets").mkdir()
        course = self.root / "My course"
        course.mkdir()
        with patch("ntu_cool_materials.console.stdin_is_interactive", return_value=True), patch("builtins.input", return_value="1"):
            self.assertEqual(select_course_folder(self.root), course)


if __name__ == "__main__":
    unittest.main()
