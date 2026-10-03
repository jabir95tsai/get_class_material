from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path

from ntu_cool_materials.storage import (
    ManifestStore, atomic_write_text, hide_dot_entries, sanitize_component, sha256_file,
)


class StorageTests(unittest.TestCase):
    def test_sanitize_component_replaces_unsafe_chars(self) -> None:
        self.assertEqual(sanitize_component('week:1/intro?.pdf'), "week_1_intro_.pdf")
        self.assertEqual(sanitize_component("CON"), "_CON")
        self.assertEqual(sanitize_component("   "), "untitled")

    def test_manifest_detects_changed_file_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "file.pdf"
            target.write_bytes(b"hello")

            store = ManifestStore(root / "manifest.sqlite3")
            try:
                file_info = {
                    "id": "10",
                    "display_name": "file.pdf",
                    "size": 5,
                    "updated_at": "2026-01-01T00:00:00Z",
                    "url": "https://cool.ntu.edu.tw/files/10/download",
                }

                self.assertTrue(store.needs_download(file_info, target))
                store.upsert_file(
                    file_info=file_info,
                    course_id="1",
                    course_name="Course",
                    local_path=target,
                    sha256=sha256_file(target),
                )
                self.assertFalse(store.needs_download(file_info, target))

                changed = dict(file_info, updated_at="2026-01-02T00:00:00Z")
                self.assertTrue(store.needs_download(changed, target))
            finally:
                store.close()


@unittest.skipUnless(os.name == "nt", "hidden attribute is Windows-only")
class HiddenFileTests(unittest.TestCase):
    def assert_hidden(self, path: Path, hidden: bool = True) -> None:
        self.assertEqual(bool(path.stat().st_file_attributes & stat.FILE_ATTRIBUTE_HIDDEN), hidden)

    def test_dot_files_stay_hidden_across_rewrites(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / ".state.json"
            atomic_write_text(state, "1")
            self.assert_hidden(state)
            atomic_write_text(state, "2")  # replacing must not leave it visible
            self.assertEqual(state.read_text(encoding="utf-8"), "2")
            self.assert_hidden(state)

            visible = Path(tmp) / "notes.md"
            atomic_write_text(visible, "x")
            self.assert_hidden(visible, hidden=False)

    def test_manifest_database_is_hidden_and_reopenable(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / ".manifest.sqlite3"
            ManifestStore(db).close()
            self.assert_hidden(db)
            ManifestStore(db).close()

    def test_hide_dot_entries_covers_folders_and_old_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / ".media-cache"
            (cache / "youtube").mkdir(parents=True)
            old_state = root / ".notebooklm-import.json"
            old_state.write_text("{}", encoding="utf-8")
            week = root / "week1"
            week.mkdir()

            hide_dot_entries(root)

            self.assert_hidden(cache)
            self.assertTrue((cache / "youtube").is_dir())
            self.assert_hidden(old_state)
            self.assert_hidden(week, hidden=False)


if __name__ == "__main__":
    unittest.main()
