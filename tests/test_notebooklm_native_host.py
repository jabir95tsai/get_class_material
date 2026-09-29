from __future__ import annotations

import io
import json
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ntu_cool_materials import notebooklm_native_host as host
from ntu_cool_materials.notebooklm_bridge import BridgeSession, _publish_native_session, _remove_native_session
from ntu_cool_materials.notebooklm import build_import_plan


class _Response:
    def __init__(self, value):
        self.payload = json.dumps(value).encode()
    def __enter__(self): return self
    def __exit__(self, *args): return None
    def read(self, limit): return self.payload[:limit]


class NativeHostTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "notes.md").write_text("notes", encoding="utf-8")
        self.discovery = self.root / "native-session.json"
        plan = build_import_plan(self.root)
        self.session = BridgeSession(plan, token="a" * 43)
        _publish_native_session(self.session, 43821, self.discovery)

    def test_native_frame_round_trip_supports_unicode(self):
        stream = io.BytesIO()
        host.write_message(stream, {"course": "半導體"})
        stream.seek(0)
        self.assertEqual(host.read_message(stream), {"course": "半導體"})

    def test_rejects_unlisted_paths_without_opening_network(self):
        with mock.patch.object(host.urllib.request, "urlopen") as opened:
            result = host.proxy({"id": "1", "path": "/files/C:/secret"}, session_path=self.discovery)
        self.assertEqual(result["error"], "path_not_allowed")
        opened.assert_not_called()

    def test_proxy_adds_private_token_and_returns_bridge_json(self):
        with mock.patch.object(host.urllib.request, "urlopen", return_value=_Response({"status": "waiting"})) as opened:
            result = host.proxy({"id": "2", "path": "/session"}, session_path=self.discovery)
        self.assertTrue(result["ok"])
        request = opened.call_args.args[0]
        self.assertEqual(request.full_url, "http://127.0.0.1:43821/session")
        self.assertEqual(request.headers["Authorization"], "Bearer " + "a" * 43)

    def test_missing_session_fails_closed(self):
        result = host.proxy({"id": "3", "path": "/session"}, session_path=self.root / "missing.json")
        self.assertEqual(result["error"], "no_active_import")

    def test_discovery_cleanup_only_removes_matching_session(self):
        _remove_native_session("other", self.discovery)
        self.assertTrue(self.discovery.exists())
        _remove_native_session(self.session.session_id, self.discovery)
        self.assertFalse(self.discovery.exists())

    def test_rejects_oversized_native_frame(self):
        stream = io.BytesIO(struct.pack("=I", host.MAX_NATIVE_MESSAGE + 1))
        with self.assertRaisesRegex(ValueError, "size"):
            host.read_message(stream)

    @unittest.skipUnless(host.os.name == "nt", "Windows registry test")
    def test_install_manifest_allows_only_requested_extension(self):
        executable = self.root / "native-host.exe"
        executable.touch()
        import winreg
        key = mock.MagicMock()
        key.__enter__.return_value = key
        with mock.patch.object(host.Path, "home", return_value=self.root), \
             mock.patch.object(winreg, "CreateKey", return_value=key) as create, \
             mock.patch.object(winreg, "SetValueEx") as set_value:
            manifest_path = host.install_host("a" * 32, executable=executable)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["allowed_origins"], ["chrome-extension://" + "a" * 32 + "/"])
        self.assertEqual(manifest["path"], str(executable.resolve()))
        self.assertEqual(create.call_count, 2)  # Chrome and Edge, current user only.
        self.assertEqual(set_value.call_count, 2)


if __name__ == "__main__":
    unittest.main()
