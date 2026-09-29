import base64
import contextlib
import http.client
import io
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from ntu_cool_materials import cli
from ntu_cool_materials.notebooklm import STATE_NAME, build_import_plan
from ntu_cool_materials.notebooklm_bridge import BridgeSession, make_server, pairing_key

TOKEN = "x" * 43
URL = "https://notebooklm.google.com/notebook/test-notebook"
ORIGIN = "chrome-extension://" + "a" * 32


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "notes.md").write_bytes(b"# Synthetic lecture\nExample data")
        self.session = BridgeSession(build_import_plan(self.root), token=TOKEN, timeout=1)
        self.server = make_server(self.session, port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))

    def tearDown(self):
        self.session.stop()
        if self.session.worker:
            self.session.worker.join(timeout=3)
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.temp.cleanup()

    def request(self, method, path, data=None, *, auth=True, origin=ORIGIN, extra=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        headers = {"Origin": origin}
        if auth:
            headers["Authorization"] = "Bearer " + TOKEN
        if extra:
            headers.update(extra)
        body = json.dumps(data).encode() if data is not None else None
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        content = response.read()
        result = (response.status, json.loads(content) if content else None, dict(response.getheaders()))
        connection.close()
        return result

    def post(self, path, **values):
        return self.request("POST", path, {"session_id": self.session.session_id, **values})

    def test_requires_auth_and_rejects_web_origin_and_wrong_host(self):
        self.assertEqual(self.request("GET", "/session", auth=False)[0], 401)
        self.assertEqual(self.request("GET", "/session", origin="https://notebooklm.google.com")[0], 403)
        self.assertEqual(self.request("GET", "/session", extra={"Host": "example.com"})[0], 403)
        code, data, headers = self.request("GET", "/session")
        self.assertEqual(code, 200)
        self.assertNotIn(TOKEN, json.dumps(data))
        self.assertEqual(headers["Access-Control-Allow-Origin"], ORIGIN)
        self.assertEqual(headers["Cache-Control"], "no-store")

    def test_preflight_does_not_expose_session_and_disallows_websites(self):
        self.assertEqual(self.request("OPTIONS", "/session", auth=False)[0], 204)
        self.assertEqual(self.request("OPTIONS", "/session", auth=False, origin="https://example.com")[0], 403)

    def test_no_arbitrary_file_access_or_unstaged_chunks(self):
        for path in ("/file/notes.md", "/../../.secrets", "/session?token=" + TOKEN):
            self.assertEqual(self.request("GET", path)[0], 404)
        self.assertEqual(self.request("GET", "/chunk/" + "a" * 32 + "/0")[0], 409)

    def test_stale_session_and_reply_cannot_mutate_state(self):
        self.assertEqual(self.request("POST", "/start", {"session_id":"old", "force_new":True})[0], 400)
        self.assertEqual(self.post("/reply", id="missing", ok=True, result=URL)[0], 400)
        self.assertEqual(self.session.status, "waiting")

    def test_complete_transport_roundtrip_uses_existing_journal(self):
        self.assertEqual(self.post("/start", notebook_url=URL, force_new=False)[0], 200)
        ready = []
        seen = set()
        until = time.monotonic() + 5
        while time.monotonic() < until and self.session.status == "running":
            command = self.request("GET", "/command")[1]["command"]
            if not command or command["id"] in seen:
                time.sleep(.01)
                continue
            seen.add(command["id"])
            operation = command["operation"]
            if operation == "open": result = URL
            elif operation == "ready": result = list(ready)
            elif operation == "count": result = len(ready)
            elif operation == "upload":
                response = self.request("GET", f"/chunk/{command['id']}/0")
                self.assertEqual(response[0], 200)
                self.assertEqual(base64.b64decode(response[1]["data"]), (self.root / "notes.md").read_bytes())
                self.assertEqual(self.request("GET", f"/chunk/{command['id']}/999")[0], 416)
                ready.append(command["payload"]["title"])
                result = True
            self.assertEqual(self.post("/reply", id=command["id"], ok=True, result=result)[0], 200)
        self.session.worker.join(timeout=2)
        self.assertEqual(self.session.status, "complete")
        state = json.loads((self.root / STATE_NAME).read_text(encoding="utf-8"))
        entries = list(state["notebooks"][URL]["sources"].values())
        self.assertEqual(entries[0]["status"], "complete")
        self.assertFalse(list(self.root.glob(".notebooklm-upload-*")))

    def test_upload_disappearance_keeps_pending_and_reports_verification_stage(self):
        source = self.session.plan.sources[0]
        def rpc(operation, payload):
            return {"open": URL, "ready": [], "count": 0, "upload": True}[operation]
        with patch.object(self.session, "rpc", side_effect=rpc), contextlib.redirect_stdout(io.StringIO()) as output:
            self.session._run(URL, False)
        self.assertEqual(self.session.status, "failed")
        self.assertIn("來源已不在可用清單", output.getvalue())
        state = json.loads((self.root / STATE_NAME).read_text(encoding="utf-8"))
        self.assertEqual(state["notebooks"][URL]["sources"][source.digest]["status"], "pending")
        self.assertEqual(self.session.uploaded, 0)

    def test_timeout_ends_worker_instead_of_replaying_command(self):
        self.session.timeout = .03
        self.post("/start", notebook_url=URL, force_new=False)
        self.session.worker.join(timeout=2)
        self.assertEqual(self.session.status, "failed")
        self.assertIsNone(self.session.command)
        self.assertEqual(self.post("/start", notebook_url=URL, force_new=False)[0], 400)

    def test_pairing_key_persists_and_invalid_file_is_rejected(self):
        target = self.root / ".secrets/pairing.txt"
        first = pairing_key(target)
        self.assertEqual(first, pairing_key(target))
        self.assertEqual(len(first), 43)
        target.write_text("broken", encoding="ascii")
        with self.assertRaises(RuntimeError): pairing_key(target)

    def test_cli_extension_routes_to_bridge_without_playwright(self):
        with patch("ntu_cool_materials.notebooklm_bridge.run_extension_import", return_value=0) as bridge:
            self.assertEqual(cli.main(["notebooklm", "--course-dir", str(self.root), "--extension"]), 0)
            self.assertEqual(bridge.call_args.args[0], self.root)


if __name__ == "__main__":
    unittest.main()
