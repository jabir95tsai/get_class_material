from __future__ import annotations

import base64
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tools import install_notebooklm as installer


class InstallerTests(unittest.TestCase):
    def test_chromium_id_known_sha256_vector(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp)
            (source / "manifest.json").write_text(json.dumps({"key": base64.b64encode(b"abc").decode()}))
            self.assertEqual(installer.extension_id(source), "lkhibglpipabmpokebebeanofnkocccd")

    def test_checkout_has_complete_fixed_identity(self):
        identity = installer.validate_source(installer.ROOT)
        self.assertRegex(identity, r"^[a-p]{32}$")
        with tempfile.TemporaryDirectory() as temp:
            copied = Path(temp) / "different location 中文"
            copied.mkdir()
            (copied / "manifest.json").write_bytes((installer.ROOT / "extensions/notebooklm/manifest.json").read_bytes())
            self.assertEqual(installer.extension_id(copied), identity)

    def test_missing_source_does_not_modify_installation(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "target"
            with patch.object(installer.subprocess, "run") as run:
                with self.assertRaises(FileNotFoundError):
                    installer.install(Path(temp) / "missing", target)
            run.assert_not_called()
            self.assertFalse(target.exists())

    def test_pip_failure_does_not_copy_extension_or_register_host(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp)
            with patch.object(installer.subprocess, "run", side_effect=[None, subprocess.CalledProcessError(1, "pip")]) as run:
                with self.assertRaises(subprocess.CalledProcessError):
                    installer.install(installer.ROOT, target)
            self.assertEqual(run.call_count, 2)
            self.assertFalse((target / "extension").exists())

    def test_repeat_install_uses_exact_runtime_and_allowlisted_files(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "space 中文"
            scripts = target / "venv/Scripts"
            scripts.mkdir(parents=True)
            (scripts / "python.exe").touch()
            (scripts / "ntu-cool-notebooklm-host.exe").touch()
            with patch.object(installer.subprocess, "run") as run:
                for _ in range(2):
                    extension = installer.install(installer.ROOT, target)
            self.assertEqual(run.call_count, 4)  # pip + registration; no venv recreation.
            command = run.call_args.args[0]
            self.assertEqual(command[0], str(scripts / "python.exe"))
            self.assertEqual(command[-1], str(scripts / "ntu-cool-notebooklm-host.exe"))
            self.assertEqual(command[-2], installer.extension_id(extension))
            files = {p.relative_to(extension).as_posix() for p in extension.rglob("*") if p.is_file()}
            self.assertEqual(files, set(installer.EXTENSION_FILES) | {"LICENSE"})
