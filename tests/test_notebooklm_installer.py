from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tools import install_notebooklm as installer


class InstallerTests(unittest.TestCase):
    def test_missing_source_does_not_modify_installation(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "target"
            with patch.object(installer.subprocess, "run") as run:
                with self.assertRaises(FileNotFoundError):
                    installer.install(Path(temp) / "missing", target)
            run.assert_not_called()
            self.assertFalse(target.exists())

    def test_pip_failure_propagates(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(installer.subprocess, "run", side_effect=[None, subprocess.CalledProcessError(1, "pip")]) as run:
                with self.assertRaises(subprocess.CalledProcessError):
                    installer.install(installer.ROOT, Path(temp))
            self.assertEqual(run.call_count, 2)

    def test_repeat_install_reuses_runtime_and_installs_api_extra(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "space 中文"
            scripts = target / "venv/Scripts"
            scripts.mkdir(parents=True)
            (scripts / "python.exe").touch()
            with patch.object(installer.subprocess, "run") as run:
                for _ in range(2):
                    python = installer.install(installer.ROOT, target)
            self.assertEqual(run.call_count, 2)  # pip only; no venv recreation.
            command = run.call_args.args[0]
            self.assertEqual(command[0], str(python))
            self.assertEqual(command[-1], f"{installer.ROOT}[notebooklm]")
