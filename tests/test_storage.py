from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ntu_cool_materials.storage import ManifestStore, sanitize_component, sha256_file


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


if __name__ == "__main__":
    unittest.main()
