import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ntu_cool_materials.notebooklm import build_import_plan
from ntu_cool_materials.spreadsheet_convert import convert_spreadsheets, markdown_path_for

try:
    import markitdown  # noqa: F401
    import openpyxl
except ImportError:
    openpyxl = None


class FakeConverter:
    def __init__(self, text="## Sheet1\n| a | b |\n| --- | --- |\n| 1 | 2 |"):
        self.text = text
        self.calls = 0

    def convert(self, path):
        self.calls += 1
        return type("Result", (), {"text_content": self.text})()


def _quiet(fn, *args, **kwargs):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*args, **kwargs)


class SpreadsheetConvertTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.week = self.root / "week1"
        self.week.mkdir()
        self.workbook = self.week / "grades.xlsx"
        self.workbook.write_bytes(b"workbook bytes")

    def tearDown(self):
        self.tmp.cleanup()

    def test_markdown_name_keeps_workbook_extension(self):
        self.assertEqual(markdown_path_for(self.workbook).name, "grades.xlsx.md")

    def test_converts_once_then_skips_until_workbook_changes(self):
        fake = FakeConverter()
        with patch("ntu_cool_materials.spreadsheet_convert._load_converter", return_value=fake):
            first = _quiet(convert_spreadsheets, self.root)
            second = _quiet(convert_spreadsheets, self.root)
            later = markdown_path_for(self.workbook).stat().st_mtime + 10
            os.utime(self.workbook, (later, later))
            third = _quiet(convert_spreadsheets, self.root)
        self.assertEqual((first.done, second.skipped, third.done), (1, 1, 1))
        self.assertEqual(fake.calls, 2)
        text = markdown_path_for(self.workbook).read_text(encoding="utf-8")
        self.assertTrue(text.startswith("# grades.xlsx\n"))
        self.assertIn("| 1 | 2 |", text)
        self.assertEqual(self.workbook.read_bytes(), b"workbook bytes")

    def test_missing_markitdown_warns_without_writing(self):
        with patch("ntu_cool_materials.spreadsheet_convert._load_converter", return_value=None):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                stats = convert_spreadsheets(self.root)
        self.assertTrue(stats.missing_dependency)
        self.assertIn("get-class-material[excel]", out.getvalue())
        self.assertFalse(markdown_path_for(self.workbook).exists())

    def test_bad_or_empty_workbook_is_reported_not_raised(self):
        class Broken:
            def convert(self, path):
                raise ValueError("corrupt")
        for converter in (Broken(), FakeConverter("   ")):
            with self.subTest(converter=type(converter).__name__), \
                 patch("ntu_cool_materials.spreadsheet_convert._load_converter", return_value=converter):
                stats = _quiet(convert_spreadsheets, self.root)
            self.assertEqual(len(stats.failed), 1)
            self.assertFalse(markdown_path_for(self.workbook).exists())

    def test_hidden_and_metadata_dirs_are_ignored(self):
        for name in (".cache", "metadata"):
            (self.root / name).mkdir()
            (self.root / name / "x.xlsx").write_bytes(b"x")
        with patch("ntu_cool_materials.spreadsheet_convert._load_converter", return_value=FakeConverter()):
            stats = _quiet(convert_spreadsheets, self.root)
        self.assertEqual(stats.done, 1)

    def test_converted_markdown_is_imported_instead_of_workbook(self):
        with patch("ntu_cool_materials.spreadsheet_convert._load_converter", return_value=FakeConverter()):
            _quiet(convert_spreadsheets, self.root)
        plan = build_import_plan(self.root)
        self.assertEqual([s.relative_path for s in plan.sources], ["week1/grades.xlsx.md"])
        self.assertTrue(any(path == "week1/grades.xlsx" for path, _ in plan.skipped))

    @unittest.skipIf(openpyxl is None, "markitdown[xlsx] not installed")
    def test_real_workbook_renders_every_sheet(self):
        wb = openpyxl.Workbook()
        wb.active.title = "成績"
        wb.active.append(["姓名", "分數"])
        wb.active.append(["王小明", 90])
        wb.create_sheet("出席").append(["週次", "出席"])
        wb.save(self.workbook)
        stats = _quiet(convert_spreadsheets, self.root)
        self.assertEqual(stats.done, 1, stats.failed)
        text = markdown_path_for(self.workbook).read_text(encoding="utf-8")
        for expected in ("## 成績", "王小明", "90", "## 出席"):
            self.assertIn(expected, text)


if __name__ == "__main__":
    unittest.main()
