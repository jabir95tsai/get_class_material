"""Download-to-import boundary, using synthetic HTTP bodies, no live accounts."""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ntu_cool_materials.course_pipeline import CoursePlan, WeekPlan, download_files
from ntu_cool_materials.notebooklm import build_import_plan
from ntu_cool_materials.notebooklm_flow import prepare_manual_upload


class ExcelDownloadImportTests(unittest.TestCase):
    def test_excel_bytes_download_unchanged_then_excluded_from_import(self):
        # These are transport fixtures, not a test of Excel's workbook renderer.
        for extension in (".xlsx", ".xls"):
            for all_types in (False, True):
                with self.subTest(extension=extension, all_types=all_types), tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    week_dir = root / "week1"
                    name = "practice" + extension
                    body = b"synthetic spreadsheet transport payload\x00\xff"
                    response = io.BytesIO(body)
                    response.status = 200
                    response.headers = {"Content-Length": str(len(body))}
                    item = {"id": 1, "content_id": 101, "type": "File", "title": name,
                            "content_details": {"display_name": name}}
                    plan = CoursePlan({"id": 1, "name": "Test"}, "1", root,
                                      [WeekPlan("week1", {"items": [item]}, week_dir)])
                    with contextlib.redirect_stdout(io.StringIO()), \
                         patch("ntu_cool_materials.course_pipeline.urllib.request.build_opener") as opener:
                        opener.return_value.open.return_value = response
                        result = download_files(plan, SimpleNamespace(headers={}), all_file_types=all_types)
                        self.assertEqual(result.done, 1)
                        self.assertEqual(result.failed, [])
                        self.assertEqual((week_dir / name).read_bytes(), body)
                        self.assertFalse(list(week_dir.glob("*.pdf")))
                        self.assertFalse(list(week_dir.glob("*.part")))
                        opener.return_value.open.assert_called_once()
                        # Rerunning must retain the existing download.
                        again = download_files(plan, SimpleNamespace(headers={}), all_file_types=all_types)
                        self.assertEqual(again.skipped, 1)
                        opener.return_value.open.assert_called_once()
                        (week_dir / "notes.md").write_text("Test notes", encoding="utf-8")
                        imports = build_import_plan(root)
                        self.assertEqual([s.relative_path for s in imports.sources], ["week1/notes.md"])
                        self.assertTrue(any(path == "week1/" + name for path, _ in imports.skipped))
                        bundle = prepare_manual_upload(root)
                        uploaded_files = list(bundle.glob("batch-*/*"))
                        self.assertEqual(len(uploaded_files), 1)
                        self.assertEqual(uploaded_files[0].suffix, ".md")
                        self.assertEqual((week_dir / name).read_bytes(), body)


if __name__ == "__main__":
    unittest.main()
